"""AIDE2 C43: opt-in `perception.gpu_prepare` moves the per-point numpy preparation of prepare_frame (camera projection,
voxel keys, unique rows, voxel sums, point features) onto the CUDA device. Off by default; off must stay bit-identical to
the code before C43. On is APPROXIMATE (floating-point summation order of GPU reductions and float64 matmuls), checked
against explicit tolerances here. Without CUDA it falls back to the CPU path with one warning."""
from dataclasses import replace
from pathlib import Path
import itertools,os,warnings
import numpy as np
import pytest
import torch
from torch.nn import functional as F
from core.configuration import load_config
from core.contracts import CameraCalibration,VoxelGridState
from core.se3_math import inverse,transform_points
import perception_ml.inference_engine as engine_module
from perception_ml.inference_engine import InferenceEngine,PreparedFrame,fused_observations,prepare_frame
from perception_ml.eigen_descriptors import eigen_descriptors
from perception_ml.spatial_indices import build_spatial_index_pack

ROOT=Path(__file__).resolve().parents[1]
RECORDING=Path(os.environ.get('USERPROFILE',str(Path.home())))/'aethertwin_rec/wifi_stationary_60s'
CHECKPOINT=ROOT/'assets/checkpoints/synthetic.pt'
RTOL,ATOL=1e-5,1e-6;ARGMAX_AGREEMENT=.999
CUDA=torch.cuda.is_available()


# ---- verbatim copies of the preparation code before C43: the oracles ----
def old_project_camera(frame,world_points=None):
    n=len(frame.lidar_points) if world_points is None else len(world_points);rgb=np.full((n,3),.5,np.float32)
    uv=np.zeros((n,2),np.float32); valid=np.zeros(n,bool)
    if n==0 or frame.calibration is None or frame.rgb is None or frame.depth is None: return rgb,uv,valid
    lidar=frame.lidar_points[:,:3] if world_points is None else transform_points(inverse(frame.sensor_pose_world),world_points[:,:3])
    camera_pose=frame.calibration.T_lidar_camera if frame.camera_pose_in_lidar is None else frame.camera_pose_in_lidar
    from ingestion.sensor_synchronizer import project_points,_bilinear
    projected,z=project_points(lidar,frame.calibration,camera_pose);h,w=frame.depth.shape
    valid=(z>=.2)&(projected[:,0]>=0)&(projected[:,0]<=w-1)&(projected[:,1]>=0)&(projected[:,1]<=h-1)
    safe=np.nan_to_num(projected,nan=-1,posinf=-1,neginf=-1)
    measured=_bilinear(frame.depth,safe)[:,0]
    valid&=(measured>0)&(np.abs(measured-z)<.15)
    painted=_bilinear(frame.rgb/255.,safe).astype(np.float32)
    rgb[valid]=painted[valid];uv=np.where(valid[:,None],safe,0.).astype(np.float32)
    return rgb,uv,valid


def old_unique_rows(coords):
    if len(coords)==0:return np.unique(coords,axis=0,return_inverse=True)
    lo=coords.min(0);span=[int(s)+1 for s in coords.max(0)-lo]
    if span[0]*span[1]*span[2]>=(1<<62):return np.unique(coords,axis=0,return_inverse=True)
    shifted=coords-lo;key=(shifted[:,0]*span[1]+shifted[:,1])*span[2]+shifted[:,2]
    keys,first,inv=np.unique(key,return_index=True,return_inverse=True)
    return coords[first],inv.reshape(-1)


def old_voxel_features(points,rgb,voxel_size=.18,max_voxels=1400):
    n=len(points)
    coords=np.floor(points[:,:3]/voxel_size).astype(np.int64)
    unique,inv=old_unique_rows(coords)
    keep=np.linspace(0,len(unique)-1,min(len(unique),max_voxels)).astype(int) if len(unique) else np.empty(0,int)
    chosen=np.zeros(len(unique),bool);chosen[keep]=True
    point_ids=np.flatnonzero(chosen[inv]);p=points[point_ids];rgb=rgb[point_ids]
    unique,inv=old_unique_rows(coords[point_ids]);v=len(unique)
    count=np.bincount(inv,minlength=v).astype(np.float32)
    def mean(x):
        x=np.asarray(x,np.float64)
        if x.ndim==1:out=np.bincount(inv,weights=x,minlength=v)
        else:out=np.column_stack([np.bincount(inv,weights=x[:,j],minlength=v) for j in range(x.shape[1])]) if v else np.zeros((0,)+x.shape[1:])
        return out/count.reshape((-1,)+(1,)*(x.ndim-1)) if v else out
    centers=mean(p[:,:3]).astype(np.float32)
    lo=np.full(v,.85,np.float32)
    perm=mean((p[:,5]-p[:,4])/np.maximum(p[:,5],1)).astype(np.float32)
    zmin=np.full(v,np.inf);zmax=np.full(v,-np.inf)
    if v:
        order=np.argsort(inv,kind='stable');starts=np.r_[0,np.flatnonzero(np.diff(inv[order]))+1];z=p[order,2]
        zmin=np.minimum.reduceat(z,starts).astype(np.float64);zmax=np.maximum.reduceat(z,starts).astype(np.float64)
    feats=np.column_stack([centers,mean(p[:,3]),zmax-zmin,np.log1p(count),perm,lo,mean(rgb),zmax]).astype(np.float32)
    return VoxelGridState(voxel_size,unique,centers,feats,lo,perm,inv),point_ids


def old_prepare_frame(model,frame,world_points,background_distance=None,persistence=None,
                  voxel_size=.18,max_voxels=1400,device='cpu',occupancy=None,
                  previous_coords=None,camera_feature_height=None,camera_feature_width=None):
    rgb,uv,paint_valid=old_project_camera(frame,world_points)
    vox,ids=old_voxel_features(world_points,rgb,voxel_size,max_voxels)
    if occupancy is not None:
        lo=occupancy(vox.voxel_centers);features=vox.voxel_features.copy();features[:,7]=lo
        vox=replace(vox,voxel_features=features,log_odds=lo)
    v=len(vox.voxel_centers);mapping=vox.point_to_voxel_idx
    def tensor(a,dtype=torch.float32):return torch.as_tensor(np.array(a,copy=True),dtype=dtype,device=device)
    spatial=build_spatial_index_pack(vox.voxel_centers,model.neighbors,model.temporal_neighbors,previous_coords)
    eigen=eigen_descriptors(vox.voxel_centers,indices=spatial.eigen.numpy())
    context=np.zeros((v,2),np.float32)
    if background_distance is not None:
        d=background_distance(vox.voxel_centers) if callable(background_distance) else background_distance
        context[:,0]=np.clip(d,0,2)/2
    if persistence is not None:
        age=persistence(vox.voxel_centers) if callable(persistence) else persistence
        context[:,1]=np.clip(age,0,10)/10
    points=world_points[ids]
    offset=points[:,:3]-vox.voxel_centers[mapping] if len(points) else np.empty((0,3))
    pf=np.column_stack([points[:,:3]/6,points[:,3],points[:,4]/np.maximum(points[:,5],1),rgb[ids],offset, np.linalg.norm(offset,axis=1)]).astype(np.float32)
    cf=tensor(np.zeros((v,16),np.float32));cam_logits=None
    if frame.rgb is not None and frame.depth is not None and v:
        image=np.concatenate([frame.rgb.astype(np.float32)/255,np.clip(frame.depth[:,:,None]/6,0,1)],-1)
        camera_input=tensor(image).permute(2,0,1)[None]
        if (camera_feature_height is None)!=(camera_feature_width is None):raise ValueError('Camera feature height/width must be supplied together')
        if camera_feature_height is not None:
            if any(isinstance(a,bool) or not isinstance(a,(int,np.integer)) or a<2 for a in (camera_feature_height,camera_feature_width)):
                raise ValueError('Camera feature dimensions must be integers at least two')
            camera_input=F.interpolate(camera_input,size=(camera_feature_height,camera_feature_width),mode='bilinear',align_corners=True)
        maps,cam_logits=model.camera(camera_input)
        h,w=frame.depth.shape
        grid=tensor(uv[ids]);grid=2*grid/tensor([max(w-1,1),max(h-1,1)])-1
        sampled=F.grid_sample(maps,grid[None,None],mode='bilinear',align_corners=True)[0,:,0].T
        sampled=sampled*tensor(paint_valid[ids,None])
        mi=tensor(mapping,torch.long);cf=cf.index_add(0,mi,sampled)
        counts=tensor(np.bincount(mapping,minlength=v)).clamp_min(1)
        cf=cf/counts[:,None]
    inputs=dict(coords_t=tensor(vox.voxel_centers),voxel_feats_t=tensor(vox.voxel_features),
                eigen_feats_t=tensor(eigen),context=tensor(context),point_features=tensor(pf),
                point_to_voxel=tensor(mapping,torch.long),camera_features=cf,
                sensor_coords=tensor(transform_points(inverse(frame.sensor_pose_world),vox.voxel_centers)),spatial_indices=spatial.to(device))
    return PreparedFrame(inputs,vox,ids,cam_logits,rgb)
# ---- end of oracles ----


VOXEL_FIELDS=('voxel_coords','voxel_centers','voxel_features','log_odds','permeability','point_to_voxel_idx')
FLOAT_INPUTS=('coords_t','voxel_feats_t','eigen_feats_t','context','point_features','camera_features','sensor_coords')


def synthetic_frames(count):
    import yaml
    from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'))
    source=SyntheticLabSimulator(config['seed'],config,mobile=True)
    try:return [source.next_frame() for _ in range(count)]
    finally:source.close()


# Placeholder camera model for the real frames (the recording carries none): factory colour intrinsics of the bring-up
# recording and the simulation-fixture extrinsics, as in the rehearsal. Not a calibration; it only gives the projection
# real image content and real distortion terms to work on.
REAL_CALIBRATION=CameraCalibration(np.array([[305.055908203125,0,322.01495361328125],[0,305.04791259765625,198.9817657470703],[0,0,1]]),
    np.array([[0,0,1,0],[-1,0,0,0],[0,-1,0,0],[0,0,0,1.]]),
    np.array([-0.03058234602212906,0.03583690524101257,-6.669134018011391e-05,-0.00018124846974387765,-0.012553744949400425]),'c43-test-placeholder',False)


def real_frames(count):
    if not RECORDING.exists():return []
    from ingestion.recorder_replayer import ReplaySource
    from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
    source=ReplaySource(str(RECORDING));deskewer=None;out=[]
    try:
        for raw in itertools.islice(iter(source),count):
            if deskewer is None:deskewer=ContinuousTimeIMUDeskewer(initial_pose=raw.sensor_pose_world,imu_to_lidar=raw.imu_to_lidar)
            out.append(replace(deskewer.deskew_frame(raw),calibration=REAL_CALIBRATION))
    finally:source.close()
    return out


def world_rows(frame,stride=3):
    """LiDAR rows in the world frame plus the fused RGB-D rows, as InferenceEngine.infer builds them."""
    world=frame.lidar_points.copy();world[:,:3]=transform_points(frame.sensor_pose_world,world[:,:3])
    return fused_observations(frame,world,stride=stride)[0]


def callables(seed):
    rng=np.random.default_rng(seed);a=rng.normal(size=3)
    return (lambda c:np.abs(c@a)),(lambda c:np.abs(c[:,0])*3),(lambda c:np.tanh(c[:,2]).astype(np.float32))


@pytest.fixture(scope='module')
def frames():
    return synthetic_frames(4)+real_frames(4)


@pytest.fixture
def one_thread():
    threads=torch.get_num_threads();torch.set_num_threads(1)
    try:yield
    finally:torch.set_num_threads(threads)


def assert_identical(a,b):
    assert a.inputs.keys()==b.inputs.keys()
    for key,x in a.inputs.items():
        y=b.inputs[key]
        if key=='spatial_indices':assert all(p.dtype==q.dtype and torch.equal(p,q) for p,q in zip(x,y)),key
        else:assert x.dtype==y.dtype and x.device==y.device and torch.equal(x,y),key
    for name in VOXEL_FIELDS:
        x,y=getattr(a.voxels,name),getattr(b.voxels,name);assert x.dtype==y.dtype and np.array_equal(x,y),name
    assert a.voxels.voxel_size==b.voxels.voxel_size
    assert a.point_indices.dtype==b.point_indices.dtype and np.array_equal(a.point_indices,b.point_indices)
    assert a.rgb.dtype==b.rgb.dtype and np.array_equal(a.rgb,b.rgb)
    assert (a.camera_logits is None)==(b.camera_logits is None) and (a.camera_logits is None or torch.equal(a.camera_logits,b.camera_logits))


def test_config_default_is_false_everywhere():
    assert load_config(ROOT/'config/system_config.yaml')['perception']['gpu_prepare'] is False
    for profile in sorted((ROOT/'config/profiles').glob('*.yaml')):
        assert load_config(profile)['perception'].get('gpu_prepare') is False,profile.name


def test_flag_off_is_bit_identical_to_the_code_before_c43(frames,one_thread):
    model=InferenceEngine(CHECKPOINT).model;real=0
    for index,frame in enumerate(frames):
        world=world_rows(frame);real+=frame.calibration is REAL_CALIBRATION
        background,persistence,occupancy=callables(index)
        for size in ((None,None),(40,64)):
            kwargs=dict(background_distance=background,persistence=persistence,occupancy=occupancy,max_voxels=600 if index%2 else 1400,
                        camera_feature_height=size[0],camera_feature_width=size[1])
            want=old_prepare_frame(model,frame,world,**kwargs)
            assert_identical(prepare_frame(model,frame,world,**kwargs),want)
            assert_identical(prepare_frame(model,frame,world,gpu_prepare=False,**kwargs),want)
    if RECORDING.exists():assert real==4
    # An engine with the flag off is the default engine.
    engine=InferenceEngine(CHECKPOINT,gpu_prepare=False);assert engine.gpu_prepare_status=='disabled'


def test_flag_on_without_cuda_falls_back_to_the_cpu_path_with_one_warning(frames,one_thread,monkeypatch):
    monkeypatch.setattr(torch.cuda,'is_available',lambda:False)
    with pytest.warns(RuntimeWarning,match='gpu_prepare') as caught:
        engine=InferenceEngine(CHECKPOINT,gpu_prepare=True)
    assert len([w for w in caught if 'gpu_prepare' in str(w.message)])==1 and engine.gpu_prepare_status.startswith('fallback')
    reference=InferenceEngine(CHECKPOINT)
    with warnings.catch_warnings():
        warnings.simplefilter('error')  # no further warning per frame
        for frame in frames[:3]:
            world=world_rows(frame)
            a=engine.infer(frame,world,already_fused=True);b=reference.infer(frame,world,already_fused=True)
            assert_identical(a[0],b[0])
            for x,y in zip(a[1:],b[1:]):assert np.array_equal(x,y)
    # prepare_frame itself never crashes on a CPU device with the flag on: it runs the CPU path.
    frame=frames[0];world=world_rows(frame)
    assert_identical(prepare_frame(engine.model,frame,world,gpu_prepare=True),old_prepare_frame(engine.model,frame,world))


def assert_close(name,x,y,report):
    x=x.detach().cpu().numpy() if torch.is_tensor(x) else np.asarray(x);y=y.detach().cpu().numpy() if torch.is_tensor(y) else np.asarray(y)
    assert x.shape==y.shape and x.dtype==y.dtype,name
    if x.size:report[name]=max(report.get(name,0.),float(np.abs(x.astype(np.float64)-y.astype(np.float64)).max()))
    np.testing.assert_allclose(x,y,rtol=RTOL,atol=ATOL,err_msg=name)


@pytest.mark.skipif(not CUDA,reason='perception.gpu_prepare needs torch.cuda; this venv has no CUDA device (run with .venv-gpu)')
def test_flag_on_matches_the_cpu_path_within_tolerance(frames):
    report={};agree=total=0
    on=InferenceEngine(CHECKPOINT,device='cuda',gpu_prepare=True);off=InferenceEngine(CHECKPOINT,device='cuda')
    assert on.gpu_prepare_status=='active'
    for index,frame in enumerate(frames):
        world=world_rows(frame);background,persistence,occupancy=callables(index)
        for size in ((None,None),(40,64)):
            kwargs=dict(background_distance=background,persistence=persistence,occupancy=occupancy,device='cuda',camera_feature_height=size[0],camera_feature_width=size[1])
            a=prepare_frame(on.model,frame,world,gpu_prepare=True,**kwargs);b=prepare_frame(off.model,frame,world,**kwargs)
            # The voxel set, its order, the kept points and the point-to-voxel map are exact (integer keys, sorted unique).
            for name in ('voxel_coords','point_to_voxel_idx'):
                x,y=getattr(a.voxels,name),getattr(b.voxels,name);assert x.dtype==y.dtype and np.array_equal(x,y),name
            assert np.array_equal(a.point_indices,b.point_indices) and torch.equal(a.inputs['point_to_voxel'],b.inputs['point_to_voxel'])
            assert all(p.dtype==q.dtype and torch.equal(p,q) for p,q in zip(a.inputs['spatial_indices'],b.inputs['spatial_indices']))
            for name in ('voxel_centers','voxel_features','log_odds','permeability'):assert_close('voxels.'+name,getattr(a.voxels,name),getattr(b.voxels,name),report)
            assert_close('rgb',a.rgb,b.rgb,report)
            for name in FLOAT_INPUTS:
                assert a.inputs[name].device==b.inputs[name].device;assert_close(name,a.inputs[name],b.inputs[name],report)
            if b.camera_logits is not None:assert_close('camera_logits',a.camera_logits,b.camera_logits,report)
        # Whole engine, frame after frame (temporal state included): semantic argmax per voxel.
        pa=on.infer(frame,world,occupancy=occupancy,already_fused=True);pb=off.infer(frame,world,occupancy=occupancy,already_fused=True)
        assert np.array_equal(pa[0].voxels.voxel_coords,pb[0].voxels.voxel_coords)
        agree+=int((pa[1].argmax(1)==pb[1].argmax(1)).sum());total+=len(pa[1])
        report['probabilities']=max(report.get('probabilities',0.),float(np.abs(pa[1]-pb[1]).max()))
    print(f'\nC43 gpu_prepare tolerance report over {len(frames)} frames: max |on-off| {report}; argmax agreement {agree}/{total}={agree/total:.6f}')
    assert total and agree/total>=ARGMAX_AGREEMENT

"""Targetless camera-LiDAR extrinsics on ray-cast synthetic scenes with a known transform (no hardware)."""
import json
import numpy as np
import pytest
pytest.importorskip('scipy')
from core.contracts import MultiModalSensorFrame,IMUSample
from ingestion.recorder_replayer import FrameRecorder
from tools import targetless_extrinsics as tx

K=np.array([[76.,0,79.5],[0,76.,49.5],[0,0,1]]);W,H=160,100
# Room: half-spaces n.x <= o around the LiDAR (floor, ceiling, front/back/left walls, a slanted right wall, a cut corner).
ROOM=[((0,0,-1),.8),((0,0,1),2.),((1,0,0),4.5),((-1,0,0),3.),((0,1,0),3.),((.3,-1,.15),3.2),((1,1,0),5.)]
FLOOR_AND_WALL=[((0,0,-1),.8),((1,0,0),4.)]
BOXES=[((2.6,.9,-.4),(.35,.3,.4),30.),((2.2,-1.3,-.5),(.3,.45,.3),-20.),((3.4,-.2,.3),(.15,.15,1.1),10.),((1.8,.1,-.65),(.2,.2,.15),45.)]
TRUE=tx.rigid(tx.rpy_matrix(1.,3.,-2.)@tx.OPTICAL_TO_LIDAR,[.12,-.05,-.08])


def raycast(origin,dirs,planes,boxes):
    """Distance along each (unnormalised) ray to the first surface, inf where nothing is hit."""
    t=np.full(len(dirs),np.inf)
    for n,o in planes:
        n=np.asarray(n,float);o=o/np.linalg.norm(n);n=n/np.linalg.norm(n);den=dirs@n
        with np.errstate(divide='ignore',invalid='ignore'):hit=np.where(den>1e-9,(o-origin@n)/den,np.inf)
        t=np.minimum(t,np.where(hit>0,hit,np.inf))
    for c,h,yaw in boxes:
        R=tx.rpy_matrix(0,0,yaw);ob=(origin-np.asarray(c))@R;db=dirs@R;h=np.asarray(h)
        with np.errstate(divide='ignore',invalid='ignore'):
            db=np.where(np.abs(db)<1e-12,1e-12,db);t1=(-h-ob)/db;t2=(h-ob)/db
        near=np.minimum(t1,t2).max(1);far=np.maximum(t1,t2).min(1);t=np.where((far>=near)&(near>0)&(near<t),near,t)
    return t


def scene(planes,boxes,rng,sweeps=12,rays=7000,depth_frames=6):
    lidar=[]
    for _ in range(sweeps):
        d=rng.normal(size=(rays,3));d/=np.linalg.norm(d,axis=1,keepdims=True);d=d[d[:,2]>-.85]
        r=raycast(np.zeros(3),d,planes,boxes);ok=np.isfinite(r);lidar.append((d[ok]*(r[ok]+rng.normal(0,.008,ok.sum()))[:,None]).astype(np.float32))
    v,u=np.mgrid[0:H,0:W];rays_cam=np.c_[(u.ravel()-K[0,2])/K[0,0],(v.ravel()-K[1,2])/K[1,1],np.ones(W*H)]
    z=raycast(TRUE[:3,3],rays_cam@TRUE[:3,:3].T,planes,boxes).reshape(H,W)  # camera z = 1 per ray, so t is depth
    depth=[np.where(np.isfinite(z),z*(1+rng.normal(0,.004,z.shape)),0).astype(np.float32) for _ in range(depth_frames)]
    return lidar,depth


def perturbed_guess(rng):
    axis=rng.normal(size=3);axis/=np.linalg.norm(axis);shift=rng.normal(size=3);shift*=.10/np.linalg.norm(shift)
    return tx.rigid(tx.exp_so3(np.radians(5.)*axis)@TRUE[:3,:3],TRUE[:3,3]+shift)


FAST=dict(subsets=2,bootstrap=2,grid_yaw_deg=6.,grid_pitch_deg=6.,grid_step=6.,coarse_points=1000,basins=2,depth_stride=2,windows=1)


def test_recovers_known_extrinsic_from_a_perturbed_guess():
    rng=np.random.default_rng(3);lidar,depth=scene(ROOM,BOXES,rng);guess=perturbed_guess(rng)
    assert tx.difference(guess,TRUE)['translation_norm_m']>.09 and tx.difference(guess,TRUE)['rotation_deg']>4.9
    report=tx.estimate(lidar,depth,K,None,guess,compare=TRUE,**FAST);error=tx.difference(report['T_lidar_camera'],TRUE)
    assert error['translation_norm_m']<.01 and error['rotation_deg']<.5,error
    assert report['verdict']['status']=='PLAUSIBLE',report['verdict'];assert not report['observability']['degenerate']
    assert report['fit']['fitness']>.8 and report['fit']['inlier_rmse_m']<.02
    assert report['spread']['temporal_subsets']['runs']==2 and report['spread']['temporal_subsets']['translation_max_mm']<15
    assert abs(report['depth_scale_ratio_pose_held']-1)<.01
    assert report['comparison']['translation_norm_m']<.01 and report['comparison']['compared_fit_on_this_data']['fitness']>.8


def test_floor_and_single_wall_is_flagged_degenerate():
    rng=np.random.default_rng(5);lidar,depth=scene(FLOOR_AND_WALL,[],rng);guess=perturbed_guess(rng)
    report=tx.estimate(lidar,depth,K,None,guess,**FAST);obs=report['observability']
    assert obs['degenerate'] and report['verdict']['status']=='DEGENERATE',obs
    assert obs['weakest_direction']=='translation along LiDAR y (left)',obs['weakest_direction']
    assert min(obs['translation_block_eigenvalues'])<.01


def test_output_document_is_never_a_measured_calibration(tmp_path):
    from ingestion.live_source import load_measured_calibration
    report={'T_lidar_camera':TRUE.tolist()};doc=tx.document(report,K,None,[W,H])
    assert doc['measured'] is False and doc['method']=='targetless_depth_icp' and 'NOT-MEASURED' in doc['calibration_id']
    path=tmp_path/'t.json';path.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='measured calibration'):load_measured_calibration({'calibration':str(path)})


def test_refuses_config_paths_and_existing_files(tmp_path,capsys):
    config=tx.ROOT/'config'/'calibration_extrinsics_640x400.json'
    assert tx.main([str(tmp_path/'missing'),'--initial-guess','0.1,0,0','--output',str(config),'--force'])==2
    assert 'inside config/' in capsys.readouterr().err
    existing=tmp_path/'x.json';existing.write_text('{}')
    assert tx.main([str(tmp_path/'missing'),'--initial-guess','0.1,0,0','--output',str(existing)])==2 and existing.read_text()=='{}'


def test_cli_on_a_recording_with_millimetre_depth(tmp_path):
    rng=np.random.default_rng(7);lidar,depth=scene(ROOM,BOXES,rng,sweeps=8,depth_frames=8)
    diagnostics={'camera_calibration_validation':{'color':{'K':K.tolist(),'distortion':[0]*5},'depth':{'K':K.tolist(),'distortion':[0]*5}},
                 'camera_profiles':{'alignment':'hardware'}}
    with FrameRecorder(tmp_path/'rec',chunk_size=4,compression_level=1) as recorder:
        for i,(xyz,d) in enumerate(zip(lidar,depth)):
            cloud=np.zeros((len(xyz),7),np.float32);cloud[:,:3]=xyz
            recorder.write(MultiModalSensorFrame(i,i*.1,cloud,(IMUSample(i*.1,np.zeros(3),np.zeros(3)),),depth=d*1000,rgb=np.zeros((H,W,3),np.uint8),
                                                 diagnostics=diagnostics,camera_timestamp=i*.1))
    out=tmp_path/'out.json';guess=TRUE[:3,3]+[.04,-.03,.03]
    code=tx.main([str(tmp_path/'rec'),'--initial-guess',','.join(map(str,guess)),'--initial-rpy','1,3,-2','--output',str(out),'--subsets','2',
                  '--bootstrap','2','--grid-yaw-deg','0','--grid-pitch-deg','0','--windows','1','--depth-stride','1'])
    doc=json.loads(out.read_text());info=doc['targetless']['recording_info']
    assert 'millimetres' in info['depth_units'] and info['camera_frame'].startswith('colour optical frame')
    assert doc['measured'] is False and tx.difference(doc['T_lidar_camera'],TRUE)['translation_norm_m']<.015 and code==0
    assert tx.main([str(tmp_path/'rec'),'--initial-guess','0.1,0,0','--output',str(out)])==2  # exists, no --force

"""One feature preparation path shared by training and deployment."""
from dataclasses import dataclass,replace
import warnings
import numpy as np
import torch
from torch.nn import functional as F
from core.contracts import VoxelGridState,MaterialCorrespondences
from core.se3_math import inverse,transform_points
from .eigen_descriptors import eigen_descriptors
from .hybrid_3d_network import MultiTaskVoxelSceneFlowNet
from .spatial_indices import build_spatial_index_pack
from .depth_cache import DEPTH_RAY_CACHE


def fused_observations(frame,world_points,truth=None,stride=3,dense_rgbd=None):
    """Densify with actual calibrated depth, and optionally aligned training targets.

    No truth is required or read for runtime densification. Pixel addresses are
    observation provenance; semantic/instance IDs exist only in the side channel.
    """
    if isinstance(stride,bool) or not isinstance(stride,(int,np.integer)) or stride<1:
        raise ValueError('RGB-D fusion stride must be a positive integer')
    if frame.depth is None or frame.calibration is None:return world_points,truth
    if dense_rgbd is None:
        cloud=DEPTH_RAY_CACHE.backproject(frame.depth,frame.calibration,stride,frame.rgb,camera_pose_in_lidar=frame.camera_pose_in_lidar)
    else:
        cloud=np.asarray(dense_rgbd)
        sampled=frame.depth[::stride,::stride]
        count=int((np.isfinite(sampled)&(sampled>=.2)&(sampled<=6)).sum())
        if cloud.ndim!=2 or cloud.shape[1] not in (3,6) or len(cloud)!=count or not np.isfinite(cloud).all():
            raise ValueError('Reused RGB-D must be calibrated LiDAR-frame rows at the requested stride')
    dense=np.zeros((len(cloud),7),np.float32)
    dense[:,:3]=transform_points(frame.sensor_pose_world,cloud[:,:3]);dense[:,3]=.5;dense[:,4:6]=1;dense[:,6]=frame.sweep_duration
    combined=np.concatenate([world_points,dense])
    if truth is not None:
        yy,xx=np.indices(frame.depth.shape)
        mask=(frame.depth>=.2)&(frame.depth<=6)&np.isfinite(frame.depth)&(yy%stride==0)&(xx%stride==0)
        if truth.camera_semantic is None or truth.camera_flow is None:raise ValueError('Dense training requires camera ground truth side-channel')
        correspondence=None
        if truth.correspondences is not None:
            if truth.camera_correspondences is None:raise ValueError('Dense correspondence targets require camera material pairs')
            lidar_pairs=truth.correspondences;camera_pairs=truth.camera_correspondences
            fields=('current_world','previous_world','timestamps','previous_timestamps','valid')
            correspondence=MaterialCorrespondences(**{name:np.concatenate([
                getattr(lidar_pairs,name),getattr(camera_pairs,name)[mask.ravel()]]) for name in fields})
        truth=replace(truth,semantic=np.r_[truth.semantic,truth.camera_semantic[mask]],
            flow=np.r_[truth.flow,truth.camera_flow[mask]],instance_ids=np.r_[truth.instance_ids,truth.camera_instance_ids[mask]],
            flow_valid=np.r_[truth.flow_valid,truth.camera_flow_valid[mask]],correspondences=correspondence)
    return combined,truth


def project_camera(frame,world_points=None):
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
    # Returns behind or beside the camera project to inf/NaN (and overflow float32); grid_sample would turn them
    # into NaN camera features even though paint_valid zeroes them, so invalid returns sample a fixed in-image pixel.
    rgb[valid]=painted[valid];uv=np.where(valid[:,None],safe,0.).astype(np.float32)
    return rgb,uv,valid


def _unique_rows(coords):
    """np.unique(coords,axis=0,return_inverse=True) via one packed int64 key: mixed radix over the shifted columns keeps
    the lexicographic row order, so rows and inverse are identical; the 1-D sort is several times faster."""
    if len(coords)==0:return np.unique(coords,axis=0,return_inverse=True)
    lo=coords.min(0);span=[int(s)+1 for s in coords.max(0)-lo]
    if span[0]*span[1]*span[2]>=(1<<62):return np.unique(coords,axis=0,return_inverse=True)
    shifted=coords-lo;key=(shifted[:,0]*span[1]+shifted[:,1])*span[2]+shifted[:,2]
    keys,first,inv=np.unique(key,return_index=True,return_inverse=True)
    return coords[first],inv.reshape(-1)


def voxel_features(points,rgb,voxel_size=.18,max_voxels=1400):
    n=len(points)
    coords=np.floor(points[:,:3]/voxel_size).astype(np.int64)
    unique,inv=_unique_rows(coords)
    # Spatially uniform, label-independent cap, deterministic across execution.
    keep=np.linspace(0,len(unique)-1,min(len(unique),max_voxels)).astype(int) if len(unique) else np.empty(0,int)
    chosen=np.zeros(len(unique),bool);chosen[keep]=True
    point_ids=np.flatnonzero(chosen[inv]);p=points[point_ids];rgb=rgb[point_ids]
    unique,inv=_unique_rows(coords[point_ids]);v=len(unique)
    count=np.bincount(inv,minlength=v).astype(np.float32)
    def mean(x):
        # bincount adds in input order in float64, exactly like np.add.at, without its per-element overhead.
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


@dataclass
class PreparedFrame:
    inputs: dict
    voxels: VoxelGridState
    point_indices: np.ndarray
    camera_logits: object
    rgb: np.ndarray


def gpu_prepare_device(device):
    """The CUDA device perception.gpu_prepare runs on, or None when the CPU preparation path must be used."""
    device=torch.device(device);return device if device.type=='cuda' and torch.cuda.is_available() else None


def _div(a,b):
    """a/b with numpy's correctly rounded division: CUDA divides by a Python scalar as a multiply by its reciprocal
    (off by one ulp on ~half of the values, enough to move floor() voxel keys), by a 0-dim device tensor it does not."""
    return a/torch.tensor(b,dtype=a.dtype,device=a.device)


def _transform_torch(T,points):
    T=torch.as_tensor(T,dtype=torch.float64,device=points.device);return points@T[:3,:3].T+T[:3,3]


def _bilinear_torch(image,uv,scale=None):
    """ingestion.sensor_synchronizer._bilinear on a device tensor: the same float64 element operations in the same order.
    scale divides the gathered pixels as numpy divides the whole image first (uint8/255. is float64, float32/255. float32)."""
    h,w=image.shape[:2];u=uv[:,0].clamp(0,w-1);v=uv[:,1].clamp(0,h-1)
    u0=torch.floor(u).long();v0=torch.floor(v).long();u1=(u0+1).clamp_max(w-1);v1=(v0+1).clamp_max(h-1)
    a=(u-u0)[:,None];b=(v-v0)[:,None];data=image if image.ndim==3 else image[...,None]
    def px(y,x):
        out=data[y,x]
        return out if scale is None else _div(out if out.is_floating_point() else out.double(),scale)
    return (1-a)*(1-b)*px(v0,u0)+a*(1-b)*px(v0,u1)+(1-a)*b*px(v1,u0)+a*b*px(v1,u1)


def _project_torch(xyz,calibration):
    """cv2.projectPoints (zero rvec/tvec, the five-term distortion model of CameraCalibration) in float64 on the device,
    in OpenCV's operation order; its zero terms (rational and thin-prism coefficients, identity tilt) are dropped."""
    k1,k2,p1,p2,k3=(float(c) for c in calibration.distortion);K=calibration.K
    z=xyz[:,2];inv=torch.where(z!=0,1./z,torch.ones_like(z));x=xyz[:,0]*inv;y=xyz[:,1]*inv
    r2=x*x+y*y;r4=r2*r2;r6=r4*r2;a1=2*x*y;a2=r2+2*x*x;a3=r2+2*y*y
    cdist=1+k1*r2+k2*r4+k3*r6
    xd=x*cdist+p1*a1+p2*a2;yd=y*cdist+p1*a3+p2*a1
    return torch.stack([xd*float(K[0,0])+float(K[0,2]),yd*float(K[1,1])+float(K[1,2])],1),z


def _prepare_frame_gpu(model,frame,world_points,background_distance,persistence,voxel_size,max_voxels,device,occupancy,
                       previous_coords,camera_feature_height,camera_feature_width):
    """prepare_frame with the per-point work on a CUDA device (perception.gpu_prepare, C43). APPROXIMATE: voxel keys,
    their sorted order, the kept points and the point-to-voxel map are exact (integer keys, sorted unique), but the voxel
    sums use float64 index_add_ (atomic: another summation order than bincount's input order) and the two pose transforms
    use float64 cuBLAS matmuls, so float features can differ in the last bits (tests/test_aide2_c43_gpu_prepare.py checks
    rtol 1e-5, atol 1e-6). Per-voxel work (occupancy, KD-tree indices, eigen descriptors, context) stays on the CPU.
    Returns None, before any side effect, when the packed voxel key would overflow; the caller then runs the CPU path."""
    wp=torch.as_tensor(np.ascontiguousarray(world_points),device=device);n=len(wp)
    coords=torch.floor(_div(wp[:,:3],voxel_size)).long()
    lo=coords.min(0).values;span=[int(s)+1 for s in (coords.max(0).values-lo).tolist()]
    if span[0]*span[1]*span[2]>=(1<<62):return None
    shifted=coords-lo;keys,inv=torch.unique((shifted[:,0]*span[1]+shifted[:,1])*span[2]+shifted[:,2],sorted=True,return_inverse=True)
    u=len(keys);keep=np.linspace(0,u-1,min(u,max_voxels)).astype(int);v=len(keep)
    # keep is strictly increasing, so the second unique of voxel_features is keep's rank: same rows, same order.
    keep_t=torch.as_tensor(keep,dtype=torch.long,device=device)
    remap=torch.full((u,),-1,dtype=torch.long,device=device);remap[keep_t]=torch.arange(v,device=device)
    full=remap[inv];ids_t=torch.nonzero(full>=0).squeeze(1);mi=full[ids_t];p=wp[ids_t]
    kept=keys[keep_t];unique_t=torch.stack([kept//(span[1]*span[2]),(kept//span[2])%span[1],kept%span[2]],1)+lo
    rgb=torch.full((n,3),.5,dtype=torch.float32,device=device);uv=torch.zeros((n,2),dtype=torch.float32,device=device)
    paint_valid=torch.zeros(n,dtype=torch.bool,device=device);image=depth=None
    if frame.rgb is not None and frame.depth is not None:
        def upload(x):x=np.ascontiguousarray(x);return torch.as_tensor(x if x.flags.writeable else x.copy(),device=device)  # frames are read-only
        image=upload(frame.rgb);depth=upload(frame.depth)
    if frame.calibration is not None and image is not None:
        camera_pose=frame.calibration.T_lidar_camera if frame.camera_pose_in_lidar is None else frame.camera_pose_in_lidar
        lidar=_transform_torch(inverse(frame.sensor_pose_world),wp[:,:3].double())
        projected,z=_project_torch(_transform_torch(inverse(camera_pose),lidar),frame.calibration);h,w=frame.depth.shape
        valid=(z>=.2)&(projected[:,0]>=0)&(projected[:,0]<=w-1)&(projected[:,1]>=0)&(projected[:,1]<=h-1)
        safe=torch.nan_to_num(projected,nan=-1,posinf=-1,neginf=-1)
        measured=_bilinear_torch(depth,safe)[:,0]
        paint_valid=valid&(measured>0)&((measured-z).abs()<.15)
        rgb=torch.where(paint_valid[:,None],_bilinear_torch(image,safe,255.).float(),rgb);uv=torch.where(paint_valid[:,None],safe,0.).float()
    prgb=rgb[ids_t];count_t=torch.bincount(mi,minlength=v)
    columns=torch.cat([p[:,:4].double(),((p[:,5]-p[:,4])/p[:,5].clamp_min(1)).double()[:,None],prgb.double()],1)
    sums=torch.zeros((v,8),dtype=torch.float64,device=device).index_add_(0,mi,columns)
    zmin=torch.full((v,),np.inf,dtype=p.dtype,device=device).scatter_reduce(0,mi,p[:,2],'amin')
    zmax=torch.full((v,),-np.inf,dtype=p.dtype,device=device).scatter_reduce(0,mi,p[:,2],'amax')
    S=torch.cat([sums,zmin.double()[:,None],zmax.double()[:,None]],1).cpu().numpy()
    unique=unique_t.cpu().numpy();count=count_t.cpu().numpy().astype(np.float32);ids=ids_t.cpu().numpy();mapping=mi.cpu().numpy()
    # The per-voxel finishing of voxel_features, on the device sums.
    centers=(S[:,:3]/count[:,None]).astype(np.float32);lo_=np.full(v,.85,np.float32);perm=(S[:,4]/count).astype(np.float32)
    feats=np.column_stack([centers,S[:,3]/count,S[:,9]-S[:,8],np.log1p(count),perm,lo_,S[:,5:8]/count[:,None],S[:,9]]).astype(np.float32)
    vox=VoxelGridState(voxel_size,unique,centers,feats,lo_,perm,mapping)
    if occupancy is not None:
        lo_=occupancy(vox.voxel_centers);features=vox.voxel_features.copy();features[:,7]=lo_
        vox=replace(vox,voxel_features=features,log_odds=lo_)
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
    # (a*a+b*b)+c*c is the row sum np.linalg.norm(axis=1) takes, in the same order and dtype.
    centers_t=tensor(vox.voxel_centers);offset=p[:,:3]-centers_t[mi];square=offset*offset
    pf=torch.cat([_div(p[:,:3],6.),p[:,3:4],(p[:,4]/p[:,5].clamp_min(1))[:,None],prgb,offset,torch.sqrt(square[:,0]+square[:,1]+square[:,2])[:,None]],1).float()
    cf=torch.zeros((v,16),dtype=torch.float32,device=device);cam_logits=None
    if image is not None:
        color=_div(image.float(),255.);scaled=_div(depth[:,:,None],6.).clamp(0,1);dtype=torch.promote_types(color.dtype,scaled.dtype)
        camera_input=torch.cat([color.to(dtype),scaled.to(dtype)],-1).float().permute(2,0,1)[None]
        if (camera_feature_height is None)!=(camera_feature_width is None):raise ValueError('Camera feature height/width must be supplied together')
        if camera_feature_height is not None:
            if any(isinstance(a,bool) or not isinstance(a,(int,np.integer)) or a<2 for a in (camera_feature_height,camera_feature_width)):
                raise ValueError('Camera feature dimensions must be integers at least two')
            camera_input=F.interpolate(camera_input,size=(camera_feature_height,camera_feature_width),mode='bilinear',align_corners=True)
        maps,cam_logits=model.camera(camera_input)
        h,w=frame.depth.shape
        grid=uv[ids_t];grid=2*grid/tensor([max(w-1,1),max(h-1,1)])-1
        sampled=F.grid_sample(maps,grid[None,None],mode='bilinear',align_corners=True)[0,:,0].T
        sampled=sampled*paint_valid[ids_t,None].float()
        cf=cf.index_add(0,mi,sampled)/count_t.float().clamp_min(1)[:,None]
    inputs=dict(coords_t=centers_t,voxel_feats_t=tensor(vox.voxel_features),
                eigen_feats_t=tensor(eigen),context=tensor(context),point_features=pf,
                point_to_voxel=mi,camera_features=cf,
                sensor_coords=tensor(transform_points(inverse(frame.sensor_pose_world),vox.voxel_centers)),spatial_indices=spatial.to(device))
    return PreparedFrame(inputs,vox,ids,cam_logits,rgb.cpu().numpy())


def prepare_frame(model,frame,world_points,background_distance=None,persistence=None,
                  voxel_size=.18,max_voxels=1400,device='cpu',occupancy=None,
                  previous_coords=None,camera_feature_height=None,camera_feature_width=None,gpu_prepare=False):
    if gpu_prepare and len(world_points) and gpu_prepare_device(device) is not None:
        prepared=_prepare_frame_gpu(model,frame,world_points,background_distance,persistence,voxel_size,max_voxels,device,occupancy,
                                    previous_coords,camera_feature_height,camera_feature_width)
        if prepared is not None:return prepared
    rgb,uv,paint_valid=project_camera(frame,world_points)
    vox,ids=voxel_features(world_points,rgb,voxel_size,max_voxels)
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


def aggregate_targets(prepared,truth):
    ids=prepared.point_indices;inv=prepared.voxels.point_to_voxel_idx;v=len(prepared.voxels.voxel_centers)
    votes=np.zeros((v,7),int);np.add.at(votes,(inv,truth.semantic[ids]),1)
    labels=votes.argmax(1)
    valid_points=truth.flow_valid[ids].astype(bool)
    count=np.bincount(inv[valid_points],minlength=v)
    flow=np.zeros((v,3),np.float32)
    np.add.at(flow,inv[valid_points],truth.flow[ids][valid_points])
    flow/=np.maximum(count[:,None],1)
    instances=np.full(v,-1,np.int64)
    for j in range(v):
        vals,counts=np.unique(truth.instance_ids[ids][inv==j],return_counts=True)
        if len(vals):instances[j]=vals[counts.argmax()]
    return labels,flow,count>0,instances


COMPILE_TOLERANCE=1e-4  # max |compiled-eager| over logits, flow and embedding


class InferenceEngine:
    def __init__(self,checkpoint,device='cpu',voxel_size=.18,max_voxels=1400,neighbors=16,temporal_neighbors=12,rgbd_stride=3,range_image_height=24,range_image_width=96,
                 camera_feature_height=None,camera_feature_width=None,compile=False,gpu_prepare=False):
        if not isinstance(compile,bool):raise ValueError('perception.compile must be boolean')
        if not isinstance(gpu_prepare,bool):raise ValueError('perception.gpu_prepare must be boolean')
        # Opt-in, APPROXIMATE device preparation (C43); without a CUDA device it warns once here and keeps the CPU path.
        self.gpu_prepare=gpu_prepare and gpu_prepare_device(device) is not None
        self.gpu_prepare_status='active' if self.gpu_prepare else 'disabled' if not gpu_prepare else f'fallback: CPU preparation (device {device}, CUDA available {torch.cuda.is_available()})'
        if gpu_prepare and not self.gpu_prepare:
            warnings.warn(f'perception.gpu_prepare needs a CUDA perception device; {self.gpu_prepare_status}',RuntimeWarning,stacklevel=2)
        if isinstance(rgbd_stride,bool) or not isinstance(rgbd_stride,(int,np.integer)) or rgbd_stride<1:
            raise ValueError('RGB-D fusion stride must be a positive integer')
        self.rgbd_stride=int(rgbd_stride)
        self.camera_feature_height=camera_feature_height;self.camera_feature_width=camera_feature_width
        if (camera_feature_height is None)!=(camera_feature_width is None) or any(isinstance(x,bool) or not isinstance(x,(int,np.integer)) or x<2 for x in (camera_feature_height,camera_feature_width) if x is not None):
            raise ValueError('Camera feature dimensions must be null together or integers at least two')
        self.device=device;self.model=MultiTaskVoxelSceneFlowNet(neighbors=neighbors,temporal_neighbors=temporal_neighbors,
            range_image_height=range_image_height,range_image_width=range_image_width).to(device)
        data=torch.load(checkpoint,map_location=device,weights_only=True)
        self.model.load_state_dict(data['model']);self.model.eval()
        self.metadata=data.get('metadata',{});self.voxel_size=voxel_size;self.max_voxels=max_voxels
        self.previous=None;self.last_timestamp=None;self.persistence={};self.temporal_valid=False
        # Opt-in torch.compile (dynamic shapes: voxel counts change per frame). Each input signature is
        # first run eager and compiled side by side; any mismatch or compiler error falls back to eager for good.
        self._compiled=torch.compile(self.model,dynamic=True) if compile else None
        self._verified=set();self.compile_status='pending' if compile else 'disabled'
    def _forward(self,kwargs):
        if self._compiled is None:return self.model(**kwargs)
        signature=tuple(sorted(kwargs))
        if signature in self._verified:return self._compiled(**kwargs)
        eager=self.model(**kwargs)
        try:
            compiled=self._compiled(**kwargs)
            error=max(float((a-b).abs().max()) if a.numel() else 0. for a,b in zip(compiled,eager))
        except Exception as exc:
            self._compiled=None;self.compile_status=f'disabled: {type(exc).__name__}: {exc}'[:300];return eager
        if not error<=COMPILE_TOLERANCE:
            self._compiled=None;self.compile_status=f'disabled: parity error {error:.3g}';return eager
        self._verified.add(signature);self.compile_status='active';return compiled
    def reset(self):self.previous=None;self.last_timestamp=None;self.persistence.clear();self.temporal_valid=False
    @torch.inference_mode()
    def infer(self,frame,world_points,background_distance=None,occupancy=None,dense_rgbd=None,already_fused=False):
        if not already_fused:world_points,_=fused_observations(frame,world_points,stride=self.rgbd_stride,dense_rgbd=dense_rgbd)
        elif dense_rgbd is not None:raise ValueError('Choose reused RGB-D rows or already-fused world observations')
        aligned_time=frame.timestamp+frame.sweep_duration
        self.temporal_valid=bool(self.previous is not None and len(self.previous[0]) and self.last_timestamp is not None and 0<aligned_time-self.last_timestamp<.5)
        previous_coords=self.previous[0] if self.temporal_valid else None
        def age(xyz):
            # Python-int key tuples hash and compare equal to the np.int64 tuples they replace, at a third of the cost (C35).
            result=[];persistence=self.persistence;now=frame.timestamp
            for key in map(tuple,np.floor(xyz/self.voxel_size).astype(np.int64).tolist()):
                entry=persistence.get(key);first=now if entry is None else entry[0];persistence[key]=(first,now)
                result.append(now-first)
            return np.array(result)
        prepared=prepare_frame(self.model,frame,world_points,background_distance,persistence=age,
            voxel_size=self.voxel_size,max_voxels=self.max_voxels,device=self.device,occupancy=occupancy,previous_coords=previous_coords,
            camera_feature_height=self.camera_feature_height,camera_feature_width=self.camera_feature_width,gpu_prepare=self.gpu_prepare)
        if frame.frame_id%50==0:self.persistence={k:v for k,v in self.persistence.items() if frame.timestamp-v[1]<30}
        kwargs=dict(prepared.inputs)
        if self.temporal_valid:
            kwargs['coords_prev'],kwargs['cached_stage2_prev']=self.previous
        logits,flow,embedding=self._forward(kwargs)
        self.previous=(kwargs['coords_t'].detach(),embedding.detach());self.last_timestamp=aligned_time
        return prepared,logits.softmax(-1).cpu().numpy(),flow.cpu().numpy(),embedding.cpu().numpy()

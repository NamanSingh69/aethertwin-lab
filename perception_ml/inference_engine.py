"""One feature preparation path shared by training and deployment."""
from dataclasses import dataclass,replace
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
    rgb[valid]=painted[valid];uv=projected.astype(np.float32)
    return rgb,uv,valid


def voxel_features(points,rgb,voxel_size=.18,max_voxels=1400):
    n=len(points)
    coords=np.floor(points[:,:3]/voxel_size).astype(np.int64)
    unique,inv=np.unique(coords,axis=0,return_inverse=True)
    # Spatially uniform, label-independent cap, deterministic across execution.
    keep=np.linspace(0,len(unique)-1,min(len(unique),max_voxels)).astype(int) if len(unique) else np.empty(0,int)
    chosen=np.zeros(len(unique),bool);chosen[keep]=True
    point_ids=np.flatnonzero(chosen[inv]);p=points[point_ids];rgb=rgb[point_ids]
    unique,inv=np.unique(coords[point_ids],axis=0,return_inverse=True);v=len(unique)
    count=np.bincount(inv,minlength=v).astype(np.float32)
    def mean(x):
        x=np.asarray(x);out=np.zeros((v,)+x.shape[1:],np.float64);np.add.at(out,inv,x)
        return out/count.reshape((-1,)+(1,)*(x.ndim-1)) if v else out
    centers=mean(p[:,:3]).astype(np.float32)
    lo=np.full(v,.85,np.float32)
    perm=mean((p[:,5]-p[:,4])/np.maximum(p[:,5],1)).astype(np.float32)
    zmin=np.full(v,np.inf);zmax=np.full(v,-np.inf)
    np.minimum.at(zmin,inv,p[:,2]);np.maximum.at(zmax,inv,p[:,2])
    feats=np.column_stack([centers,mean(p[:,3]),zmax-zmin,np.log1p(count),perm,lo,mean(rgb),zmax]).astype(np.float32)
    return VoxelGridState(voxel_size,unique,centers,feats,lo,perm,inv),point_ids


@dataclass
class PreparedFrame:
    inputs: dict
    voxels: VoxelGridState
    point_indices: np.ndarray
    camera_logits: object
    rgb: np.ndarray


def prepare_frame(model,frame,world_points,background_distance=None,persistence=None,
                  voxel_size=.18,max_voxels=1400,device='cpu',occupancy=None,
                  previous_coords=None,camera_feature_height=None,camera_feature_width=None):
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
                 camera_feature_height=None,camera_feature_width=None,compile=False):
        if not isinstance(compile,bool):raise ValueError('perception.compile must be boolean')
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
            result=[]
            for key in map(tuple,np.floor(xyz/self.voxel_size).astype(np.int64)):
                first,last=self.persistence.get(key,(frame.timestamp,frame.timestamp));self.persistence[key]=(first,frame.timestamp)
                result.append(frame.timestamp-first)
            return np.array(result)
        prepared=prepare_frame(self.model,frame,world_points,background_distance,persistence=age,
            voxel_size=self.voxel_size,max_voxels=self.max_voxels,device=self.device,occupancy=occupancy,previous_coords=previous_coords,
            camera_feature_height=self.camera_feature_height,camera_feature_width=self.camera_feature_width)
        if frame.frame_id%50==0:self.persistence={k:v for k,v in self.persistence.items() if frame.timestamp-v[1]<30}
        kwargs=dict(prepared.inputs)
        if self.temporal_valid:
            kwargs['coords_prev'],kwargs['cached_stage2_prev']=self.previous
        logits,flow,embedding=self._forward(kwargs)
        self.previous=(kwargs['coords_t'].detach(),embedding.detach());self.last_timestamp=aligned_time
        return prepared,logits.softmax(-1).cpu().numpy(),flow.cpu().numpy(),embedding.cpu().numpy()

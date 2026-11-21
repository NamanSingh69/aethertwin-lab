"""AIDE2 C35: two exact perception wins from the read-only review (2026-11-05): the voxel-age key loop built
np.int64 tuples (Python-int keys hash and compare equal), and farthest-point sampling allocated two (N,3) arrays per
pick. Both must give bit-identical index packs, network inputs, outputs and persistence."""
from dataclasses import replace
from pathlib import Path
import numpy as np
import pytest
import torch
from scipy.spatial import cKDTree
import perception_ml.inference_engine as engine_module
from perception_ml.inference_engine import InferenceEngine,fused_observations,prepare_frame
from perception_ml.spatial_indices import SpatialIndexPack,_xyz,build_spatial_index_pack

ROOT=Path(__file__).resolve().parents[1]


# ---- verbatim copies of the code before C35: the oracles ----
def old_build_spatial_index_pack(coords,neighbors=16,temporal_neighbors=12,previous_coords=None,device=None,fps_cap=256):
    xyz=_xyz(coords);n=len(xyz)
    if any(isinstance(k,bool) or not isinstance(k,(int,np.integer)) or k<1 for k in (neighbors,temporal_neighbors,fps_cap)):
        raise ValueError('Spatial neighbor counts must be positive integers')
    device=coords.device if device is None and torch.is_tensor(coords) else (device or 'cpu')
    def tensor(value):return torch.as_tensor(np.asarray(value,dtype=np.int64),device=device)
    if not n:
        empty=tensor(np.empty((0,0),np.int64))
        return SpatialIndexPack(empty,tensor([]),empty,empty,empty,empty,empty)
    tree=cKDTree(xyz)
    def query(support,points,k):
        _,idx=support.query(points,k=min(k,support.n),workers=1)
        return np.asarray(idx,dtype=np.int64).reshape(len(points),-1)
    local=query(tree,xyz,neighbors)
    count=min(fps_cap,max(1,n//4));distance=np.full(n,np.inf);ids=[];current=0
    for _ in range(count):
        ids.append(current);distance=np.minimum(distance,np.sum((xyz-xyz[current])**2,axis=1));current=int(distance.argmax())
    ids=np.asarray(ids,np.int64);centers=xyz[ids]
    large=query(tree,centers,2*neighbors)
    propagation=query(cKDTree(centers),xyz,3)
    eigen=local if neighbors==16 else query(tree,xyz,16)
    temporal=np.empty((n,0),np.int64)
    if previous_coords is not None:
        previous=_xyz(previous_coords)
        if len(previous):temporal=query(cKDTree(previous),xyz,temporal_neighbors)
    return SpatialIndexPack(tensor(local),tensor(ids),tensor(local[ids]),tensor(large),tensor(propagation),tensor(temporal),tensor(eigen))


@torch.inference_mode()
def old_infer(self,frame,world_points,background_distance=None,occupancy=None,dense_rgbd=None,already_fused=False):
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
# ---- end of oracles ----


def same_pack(a,b):
    for x,y in zip(a,b):assert x.dtype==y.dtype and x.shape==y.shape and torch.equal(x,y)


def clouds():
    rng=np.random.default_rng(35)
    for n in (1,2,3,4,7,9,64,1400,5000):
        for dtype in (np.float32,np.float64):
            yield (rng.normal(size=(n,3))*rng.uniform(.01,30)).astype(dtype)
    grid=np.stack(np.meshgrid(*[np.arange(6.)]*3),-1).reshape(-1,3);yield grid;yield grid.astype(np.float32)  # many ties
    yield np.repeat(rng.normal(size=(5,3)),40,axis=0).astype(np.float32)  # duplicate points, zero distances
    yield (rng.normal(size=(800,3))*1e4).astype(np.float32);yield (rng.normal(size=(800,3))*1e-4).astype(np.float32)


def test_farthest_point_sampling_gives_bit_identical_index_packs():
    for xyz in clouds():
        for kwargs in ({},{'neighbors':5,'fps_cap':17},{'previous_coords':xyz[::-1].copy()},{'previous_coords':torch.as_tensor(xyz[:3].copy())}):
            same_pack(build_spatial_index_pack(xyz,**kwargs),old_build_spatial_index_pack(xyz,**kwargs))
        same_pack(build_spatial_index_pack(torch.as_tensor(xyz)),old_build_spatial_index_pack(torch.as_tensor(xyz)))


def synthetic_frames(count):
    import yaml
    from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'))
    source=SyntheticLabSimulator(config['seed'],config,mobile=True)
    try:return [source.next_frame() for _ in range(count)]
    finally:source.close()


def test_inference_inputs_outputs_and_persistence_are_bit_identical_to_the_old_code(monkeypatch):
    import yaml
    from core.se3_math import transform_points
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'))
    checkpoint=ROOT/config['perception']['checkpoint']
    threads=torch.get_num_threads();deterministic=torch.are_deterministic_algorithms_enabled()
    torch.set_num_threads(1);torch.use_deterministic_algorithms(True)
    try:
        torch.manual_seed(0);new=InferenceEngine(checkpoint);torch.manual_seed(0);old=InferenceEngine(checkpoint)
        frames=synthetic_frames(6)
        frames=frames+[replace(frames[-1],frame_id=50,timestamp=frames[-1].timestamp+.1)]  # a pruning frame
        for frame in frames:
            world=frame.lidar_points.copy();world[:,:3]=transform_points(frame.sensor_pose_world,world[:,:3])
            a=new.infer(frame,world)
            with monkeypatch.context() as patch:
                patch.setattr(engine_module,'build_spatial_index_pack',old_build_spatial_index_pack);b=old_infer(old,frame,world)
            assert new.temporal_valid==old.temporal_valid
            for key,x in a[0].inputs.items():
                y=b[0].inputs[key]
                if key=='spatial_indices':same_pack(x,y)
                else:assert x.dtype==y.dtype and torch.equal(x,y),key
            for x,y in zip(a[1:],b[1:]):assert x.dtype==y.dtype and np.array_equal(x,y)
            assert np.array_equal(a[0].point_indices,b[0].point_indices) and new.persistence==old.persistence and len(new.persistence)
            assert all(type(v[0]) is type(w[0]) and type(v[1]) is type(w[1]) for v,w in zip(new.persistence.values(),old.persistence.values()))
            assert new.last_timestamp==old.last_timestamp and all(torch.equal(x,y) for x,y in zip(new.previous,old.previous))
        assert old.temporal_valid
    finally:torch.set_num_threads(threads);torch.use_deterministic_algorithms(deterministic)

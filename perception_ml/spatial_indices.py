"""Frame-local CPU spatial preparation; the network consumes only index tensors.

No pairwise cloud matrix is allocated. One current KD-tree serves local and
multiscale queries, one sampled-center tree serves propagation, and an optional
previous-frame tree serves temporal correlation in the common world frame.
"""
from typing import NamedTuple
import numpy as np
import torch
from scipy.spatial import cKDTree


class SpatialIndexPack(NamedTuple):
    stage1: torch.Tensor
    fps: torch.Tensor
    scale_small: torch.Tensor
    scale_large: torch.Tensor
    propagation: torch.Tensor
    temporal: torch.Tensor
    eigen: torch.Tensor
    def to(self,device):return SpatialIndexPack(*(value.to(device) for value in self))


def _xyz(value):
    out=value.detach().cpu().numpy() if torch.is_tensor(value) else np.asarray(value)
    if out.ndim!=2 or out.shape[1]!=3 or not np.isfinite(out).all():raise ValueError('Spatial coordinates must be finite (N,3)')
    return out


def build_spatial_index_pack(coords,neighbors=16,temporal_neighbors=12,previous_coords=None,device=None,fps_cap=256):
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

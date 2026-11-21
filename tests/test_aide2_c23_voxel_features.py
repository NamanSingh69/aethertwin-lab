"""AIDE2 C23: voxel_features (~34 ms a frame on the parked GPU profile) used row-wise np.unique twice and ufunc.at."""
import time
import numpy as np
from perception_ml import inference_engine
from perception_ml.inference_engine import voxel_features


def reference(points,rgb,voxel_size=.18,max_voxels=1400):
    """The previous implementation, verbatim, as the oracle."""
    coords=np.floor(points[:,:3]/voxel_size).astype(np.int64)
    unique,inv=np.unique(coords,axis=0,return_inverse=True)
    keep=np.linspace(0,len(unique)-1,min(len(unique),max_voxels)).astype(int) if len(unique) else np.empty(0,int)
    chosen=np.zeros(len(unique),bool);chosen[keep]=True
    point_ids=np.flatnonzero(chosen[inv]);p=points[point_ids];rgb=rgb[point_ids]
    unique,inv=np.unique(coords[point_ids],axis=0,return_inverse=True);v=len(unique)
    count=np.bincount(inv,minlength=v).astype(np.float32)
    def mean(x):
        x=np.asarray(x);out=np.zeros((v,)+x.shape[1:],np.float64);np.add.at(out,inv,x)
        return out/count.reshape((-1,)+(1,)*(x.ndim-1)) if v else out
    centers=mean(p[:,:3]).astype(np.float32);lo=np.full(v,.85,np.float32)
    perm=mean((p[:,5]-p[:,4])/np.maximum(p[:,5],1)).astype(np.float32)
    zmin=np.full(v,np.inf);zmax=np.full(v,-np.inf);np.minimum.at(zmin,inv,p[:,2]);np.maximum.at(zmax,inv,p[:,2])
    feats=np.column_stack([centers,mean(p[:,3]),zmax-zmin,np.log1p(count),perm,lo,mean(rgb),zmax]).astype(np.float32)
    return unique,centers,feats,lo,perm,inv,point_ids


def cloud(seed,n,spread=20.):
    rng=np.random.default_rng(seed);xyz=rng.normal(0,spread,(n,3));m=len(xyz[1::5]);xyz[:5*m:5]=xyz[1::5]  # repeats
    pts=np.c_[xyz,rng.random(n),rng.integers(1,3,n),rng.integers(1,3,n),rng.random(n)*.1]
    return pts,rng.random((n,3)).astype(np.float32)


def test_voxel_features_are_bit_identical_to_the_previous_implementation():
    for seed,n,cap in ((0,30000,1400),(1,500,1400),(2,5000,50),(3,1,1400)):
        pts,rgb=cloud(seed,n)
        if seed==0:pts=pts.astype(np.float32)  # live LiDAR points are float32
        state,ids=voxel_features(pts,rgb,.18,cap);ref=reference(pts,rgb,.18,cap)
        for got,want in zip((state.voxel_coords,state.voxel_centers,state.voxel_features,state.log_odds,state.permeability,state.point_to_voxel_idx,ids),ref):
            np.testing.assert_array_equal(got,want)
    empty,ids=voxel_features(np.zeros((0,7)),np.zeros((0,3),np.float32));assert len(empty.voxel_centers)==0 and len(ids)==0


def test_voxel_features_is_at_least_twice_as_fast():
    pts,rgb=cloud(4,40000,spread=6.)
    def best(f):
        times=[]
        for _ in range(5):t=time.perf_counter();f(pts,rgb,.18,1400);times.append(time.perf_counter()-t)
        return min(times)
    assert best(voxel_features)<.5*best(reference)

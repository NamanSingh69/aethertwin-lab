"""AIDE2: voxelize_and_update used a row-wise np.unique(axis=0) (1.9 ms at 5k returns, 13 ms at 28k) and ufunc.at
feature sums, and built a VoxelGridState the live pipeline never reads. Oracle: verbatim copies of the previous
voxelize_and_update and traverse_beams. Every output, the cell dict (order, keys, value bits) and the returned state
must be bit-identical; return_state=False must leave the map exactly as return_state=True does."""
from pathlib import Path
import numpy as np
import pytest
from core.contracts import VoxelGridState
from slam_and_twin import log_odds_octomap as octomap
from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
from slam_and_twin.tiled_map import DiskBackedVoxelMap

RECORDING=Path.home()/'aethertwin_rec'/'wifi_stationary_60s'


def traverse_beams(origin,targets,size,max_cells=None):
    """Batched exact DDA, identical tie/boundary rules to traverse_voxels.

    Iteration is over traversal depth, not over individual rays. Temporary
    arrays are bounded by beam_count * maximum traversal depth, then uniqued.
    """
    targets=np.asarray(targets,dtype=np.float64).reshape(-1,3)
    if max_cells is not None and (isinstance(max_cells,bool) or not isinstance(max_cells,(int,np.integer)) or max_cells<1):raise ValueError('Reference ray capacity must be a positive integer')
    if not len(targets):return np.empty((0,3),np.int64)
    if max_cells is not None and len(targets)>max_cells:raise ValueError(f'Reference ray capacity {max_cells} exceeded before allocation')
    origins=np.asarray(origin,dtype=np.float64)
    if origins.shape==(3,):origins=np.broadcast_to(origins,targets.shape)
    if origins.shape!=targets.shape or not np.isfinite(origins).all() or not np.isfinite(targets).all():
        raise ValueError('DDA requires one finite common origin or an aligned origin for every ray')
    o=origins/size;t=targets/size
    cell=np.floor(o).astype(np.int64);end=np.floor(t).astype(np.int64)
    direction=t-o;step=np.sign(direction).astype(np.int64);moving=direction!=0
    delta=np.full(direction.shape,np.inf);np.divide(1.,np.abs(direction),out=delta,where=moving)
    maximum=np.full(direction.shape,np.inf)
    np.divide(cell+(step>0)-o,direction,out=maximum,where=moving)
    maximum[cell==end]=np.inf
    chunks=[cell.copy()];active=np.any(cell!=end,axis=1);total=len(cell)
    limit=int(np.max(np.abs(end-cell).sum(1)))+3
    for _ in range(limit):
        if not active.any():break
        count=int(np.count_nonzero(active))
        if max_cells is not None and total+count>max_cells:raise ValueError(f'Reference ray capacity {max_cells} exceeded before traversal allocation')
        minimum=maximum.min(1)
        axes=np.isclose(maximum,minimum[:,None],atol=1e-12,rtol=0)&active[:,None]
        cell[axes]+=step[axes];maximum[axes]+=delta[axes]
        maximum[cell==end]=np.inf
        chunks.append(cell[active].copy());total+=count;active=np.any(cell!=end,axis=1)
    if active.any():raise RuntimeError('DDA traversal failed to reach measured endpoints')
    return np.unique(np.concatenate(chunks),axis=0)


def oracle_voxelize(self,points,rgb=None,timestamp=None,origin=None,beam_ids=None,return_metadata_valid=False,integrate_rays=True):
    """SparseMultiReturnVoxelMap.voxelize_and_update before this change, verbatim."""
    points=np.asarray(points)
    n=len(points);xyz=points[:,:3];origin=np.zeros(3) if origin is None else np.asarray(origin,float)
    if origin.shape==(3,):origins=np.broadcast_to(origin,(n,3))
    elif origin.shape==(n,3):origins=origin
    else:raise ValueError('Map origin must have shape (3,) or (number_of_returns,3)')
    if not np.isfinite(origins).all():raise ValueError('Map ray origins must be finite')
    if beam_ids is not None:
        beam_ids=np.asarray(beam_ids)
        if beam_ids.shape!=(n,):raise ValueError('Beam identities must align with returns')
        if n:
            order=np.argsort(beam_ids,kind='stable')
            same=np.diff(beam_ids[order])==0
            if np.any(np.linalg.norm(np.diff(origins[order],axis=0)[same],axis=1)>1e-6):
                raise ValueError('Returns of one beam must share the same acquisition origin')
    self.timestamp=float(self.timestamp+.1 if timestamp is None else timestamp)
    coords=np.floor(xyz/self.voxel_size).astype(np.int64)
    unique,inv,counts=np.unique(coords,axis=0,return_inverse=True,return_counts=True)
    free=set();hits={};penetrations={}
    if return_metadata_valid and beam_ids is None:raise ValueError("Multi-return integration requires beam identity")
    if integrate_rays and n:
        if beam_ids is None:finals=np.arange(n)
        else:
            distance=np.linalg.norm(xyz-origins,axis=1)
            order=np.lexsort((distance,np.asarray(beam_ids)))
            boundaries=np.r_[0,np.flatnonzero(np.diff(np.asarray(beam_ids)[order]))+1,n]
            finals=order[boundaries[1:]-1] if return_metadata_valid else order[boundaries[:-1]]
        # Endpoint/intermediate-hit evidence is excluded globally below.
        from native.voxel_rays import backend_status,traverse_beams as native_beams
        use_native=self.ray_backend=='native' or (self.ray_backend=='auto' and backend_status()['available'])
        if use_native:
            traversed=native_beams(origins[finals],xyz[finals],self.voxel_size,backend='native',max_cells=self.max_traversal_cells)
            self.last_ray_backend='native'
        else:
            traversed=traverse_beams(origins[finals],xyz[finals],self.voxel_size,max_cells=self.max_traversal_cells)
            self.last_ray_backend='python_batched_reference'
        free=set(map(tuple,np.asarray(traversed).tolist()))
    if n:
        # Aggregate each return once. Repeatedly scanning inv for every
        # voxel was O(points * voxels), particularly costly at camera scale.
        if return_metadata_valid:
            score=(points[:,5]-points[:,4])/points[:,5]
            solid=np.bincount(inv,weights=(score<=0),minlength=len(unique))>0
            permeability=np.bincount(inv,weights=score,minlength=len(unique))/counts
        else:
            solid=np.ones(len(unique),bool);permeability=np.zeros(len(unique))
        unique_keys=list(map(tuple,unique.tolist()))
        hits={key:(.85 if is_solid else .25) for key,is_solid in zip(unique_keys,solid)}
        penetrations={key:float(value) for key,value in zip(unique_keys,permeability)}
    self._apply_evidence(free.difference(hits),hits,penetrations)
    self._updates+=1
    if self._updates%50==0 and np.isfinite(self.max_age) and getattr(self,'eager_pruning',True):
        stale=[k for k,v in self.cells.items() if self.timestamp-v[2]>self.max_age]
        for key in stale:del self.cells[key]
    V=len(unique);features=np.zeros((V,12),np.float32)
    lo,perm=self._observed_values(unique)
    if n:
        mean=np.zeros((V,3));np.add.at(mean,inv,xyz);mean/=counts[:,None]
        intensity=np.bincount(inv,weights=points[:,3],minlength=V)/counts
        minz=np.full(V,np.inf);maxz=np.full(V,-np.inf);np.minimum.at(minz,inv,xyz[:,2]);np.maximum.at(maxz,inv,xyz[:,2])
        colors=np.full((V,3),.5)
        if rgb is not None:
            colors=np.zeros((V,3));np.add.at(colors,inv,np.asarray(rgb)[:,:3]);colors/=counts[:,None]
        features[:]=np.c_[mean,intensity,maxz-minz,np.log1p(counts),perm,lo,colors,maxz]
    else:mean=np.empty((0,3))
    return VoxelGridState(self.voxel_size,unique.astype(np.int32),mean.astype(np.float32),features,lo,perm,inv)


class OracleMap(SparseMultiReturnVoxelMap):
    voxelize_and_update=oracle_voxelize


class OracleDiskMap(DiskBackedVoxelMap):
    def voxelize_and_update(self,*args,**kwargs):
        if self.readonly:raise TypeError('Loaded tile authority is read-only; open explicitly writable to integrate observations')
        result=oracle_voxelize(self,*args,**kwargs)
        if np.isfinite(self.max_age) and self._updates%self.expiry_interval_updates==0:self.prune_expired(self.expiry_tiles_per_update)
        return result


FIELDS=('voxel_size','voxel_coords','voxel_centers','voxel_features','log_odds','permeability','point_to_voxel_idx')


def bits(a,b):
    a=np.asarray(a);b=np.asarray(b)
    return a.dtype==b.dtype and a.shape==b.shape and a.flags['C_CONTIGUOUS']==b.flags['C_CONTIGUOUS'] and a.tobytes()==b.tobytes()


def same_state(new,old):
    for field in FIELDS:
        a,b=getattr(new,field),getattr(old,field)
        assert bits(a,b),field
        if np.isfinite(np.asarray(b,float)).all():assert np.array_equal(a,b),field


def same_map(new,old,full=False):
    # repr keeps key order, value types and the sign of zeros; float repr round-trips every bit.
    if full:assert repr(list(new.cells.items()))==repr(list(old.cells.items()))
    else:
        assert list(new.cells)==list(old.cells)
        assert np.array(list(new.cells.values()),float).tobytes()==np.array(list(old.cells.values()),float).tobytes()
    assert (new.timestamp,new._updates,new.last_ray_backend)==(old.timestamp,old._updates,old.last_ray_backend)


def cloud(rng,n,*,spread=3.,signed_zero=True):
    """Returns with repeated voxels, exact duplicates, negative coordinates and +-0 heights in shared voxels."""
    xyz=rng.normal(0,spread,(n,3))
    if n>4:m=len(xyz[1::4]);xyz[:4*m:4]=xyz[1::4]  # exact duplicates
    if n>6:xyz[2::7]=np.round(xyz[2::7]/.25)*.25  # many returns per voxel
    if signed_zero and n>3:xyz[::3,2]=np.where(rng.random(len(xyz[::3]))<.5,0.,-0.)
    counts=rng.integers(1,4,n);index=np.minimum(rng.integers(1,4,n),counts)
    return np.c_[xyz,rng.random(n),index,counts,rng.random(n)*.1]


def colors_for(rng,n,kind):
    if kind=='none':return None
    if kind=='u8':return rng.integers(0,256,(n,3)).astype(np.uint8)
    if kind=='wide':return rng.random((n,4))
    return rng.random((n,3)).astype(np.float32)


def run_both(new,old,points,**kwargs):
    a=new.voxelize_and_update(points,**kwargs);b=old.voxelize_and_update(points,**kwargs)
    same_state(a,b);same_map(new,old);return a


@pytest.mark.parametrize('backend',['auto','reference'])
@pytest.mark.parametrize('dtype',[np.float32,np.float64])
def test_randomized_sequences_are_bit_identical_to_the_oracle(backend,dtype):
    rng=np.random.default_rng(23 if backend=='auto' else 29)
    new=SparseMultiReturnVoxelMap(.18,max_age=.6,ray_backend=backend);old=OracleMap(.18,max_age=.6,ray_backend=backend)
    sizes=[0,1,2,7,40,300,1200]+list(rng.integers(0,900,48))
    for step,n in enumerate(sizes):
        n=int(n);points=cloud(rng,n).astype(dtype);mode=step%4;stamp=None if step%5==3 else .05*step
        rgb=colors_for(rng,n,['none','f32','u8','wide'][step%4])
        if mode==0:run_both(new,old,points,rgb=rgb,timestamp=stamp)
        elif mode==1:run_both(new,old,points,rgb=rgb,timestamp=stamp,origin=rng.normal(0,.3,3))
        elif mode==2:run_both(new,old,points,rgb=rgb,timestamp=stamp,integrate_rays=False)
        else:
            beams=rng.integers(0,max(1,n//2),n);beam_origin=rng.normal(0,.3,(max(1,n//2),3))
            run_both(new,old,points,rgb=rgb,timestamp=stamp,origin=beam_origin[beams],beam_ids=beams,return_metadata_valid=step%8==3)
    same_map(new,old,True);assert new._updates==len(sizes)>50 and len(new.cells)


def test_wide_box_fallback_and_non_finite_heights_match_the_oracle():
    rng=np.random.default_rng(5)
    new=SparseMultiReturnVoxelMap(.18);old=OracleMap(.18)
    wide=cloud(rng,200);wide[:100,0]=rng.uniform(-1e17,1e17,100)  # key box beyond 62 bits: row-wise fallback
    run_both(new,old,wide,rgb=colors_for(rng,200,'f32'),integrate_rays=False,timestamp=1.)
    holes=cloud(rng,200);holes[5,2]=np.nan;holes[9,2]=np.inf  # min/max keep the ufunc.at path for non-finite heights
    for m in (new,old):  # the evidence is integrated, then the state contract rejects the non-finite features
        with np.errstate(invalid='ignore'),pytest.raises(ValueError,match='Non-finite'):m.voxelize_and_update(holes,integrate_rays=False,timestamp=1.1)
    same_map(new,old)
    single=cloud(rng,1,signed_zero=False);run_both(new,old,single,timestamp=1.2,origin=[0.,0.,0.])
    run_both(new,old,np.empty((0,7)),timestamp=1.3)


def test_signed_zero_heights_keep_the_oracle_bits_in_long_runs():
    rng=np.random.default_rng(8)
    for trial in range(40):
        n=int(rng.integers(1,3000));points=np.zeros((n,7));points[:,0]=rng.integers(0,3,n)*.2;points[:,4:6]=1
        points[:,2]=rng.choice(np.array([0.,-0.,.05,-.05,.1]) if trial%2 else np.array([0.,-0.]),n)
        new=SparseMultiReturnVoxelMap(.18);old=OracleMap(.18)
        run_both(new,old,points,integrate_rays=False,timestamp=0.)


def test_unique_helpers_equal_row_unique():
    rng=np.random.default_rng(3)
    for n in [1,2,5,64,4000]:
        coords=rng.integers(-40,40,(n,3)).astype(np.int64);coords[n//2:]=coords[:n-n//2]
        unique,inv,counts=np.unique(coords,axis=0,return_inverse=True,return_counts=True)
        got=octomap._unique_voxels(coords)
        for a,b in zip(got[:3],(unique,inv,counts)):assert bits(a,b)
        assert bits(octomap._unique_cells(coords),unique)
        order,starts=got[3],got[4];assert np.array_equal(inv[order],np.repeat(np.arange(len(unique)),counts))
        assert np.all(np.diff(order)[np.diff(inv[order])==0]>0) and np.array_equal(starts,np.r_[0,np.cumsum(counts)[:-1]])


def test_return_state_false_leaves_the_identical_map_and_returns_none():
    rng=np.random.default_rng(13)
    lean=SparseMultiReturnVoxelMap(.18,max_age=.5);full=SparseMultiReturnVoxelMap(.18,max_age=.5)
    for step in range(60):
        points=cloud(rng,int(rng.integers(0,500)));rgb=colors_for(rng,len(points),'f32')
        assert lean.voxelize_and_update(points,rgb,timestamp=.05*step,origin=[0.,0.,.2],return_state=False) is None
        full.voxelize_and_update(points,rgb,timestamp=.05*step,origin=[0.,0.,.2])
    same_map(lean,full,True)


def test_tiled_map_sequence_is_bit_identical_including_store_accounting(tmp_path):
    rng=np.random.default_rng(41)
    budgets=dict(tile_size=4,max_resident_tiles=6,max_resident_bytes=200000)  # small enough to evict every frame
    new=DiskBackedVoxelMap(tmp_path/'new',.25,decay_rate=.96,max_age=1.,expiry_interval_updates=3,**budgets)
    old=OracleDiskMap(tmp_path/'old',.25,decay_rate=.96,max_age=1.,expiry_interval_updates=3,**budgets)
    lean=DiskBackedVoxelMap(tmp_path/'lean',.25,decay_rate=.96,max_age=1.,expiry_interval_updates=3,**budgets)
    try:
        for step in range(24):
            n=int(rng.integers(0,400));points=cloud(rng,n,spread=1.5);beams=np.arange(n);origins=rng.normal(0,.2,(n,3))
            kwargs=dict(timestamp=.1*step,origin=origins,beam_ids=beams,return_metadata_valid=bool(step%2),rgb=colors_for(rng,n,'f32'))
            same_state(new.voxelize_and_update(points,**kwargs),old.voxelize_and_update(points,**kwargs))
            assert lean.voxelize_and_update(points,return_state=False,**kwargs) is None
            # Residency drives the tiled round-robin expiry, so the lean call must leave identical store accounting too.
            assert new.cells.statistics()==old.cells.statistics()==lean.cells.statistics() and new.cells.evictions>0
            assert (new.timestamp,new._updates,new.last_ray_backend)==(old.timestamp,old._updates,old.last_ray_backend)
        assert repr(list(new.cells.items()))==repr(list(old.cells.items()))==repr(list(lean.cells.items()))
    finally:
        for m in (new,old,lean):m.cells.close()


@pytest.mark.skipif(not RECORDING.exists(),reason='real bench recording not on this machine')
def test_thirty_real_l2_sweeps_are_bit_identical():
    from ingestion.recorder_replayer import ReplaySource
    source=ReplaySource(RECORDING);rng=np.random.default_rng(1)
    new=SparseMultiReturnVoxelMap(.18);old=OracleMap(.18);lean=SparseMultiReturnVoxelMap(.18)
    try:
        for k,frame in zip(range(30),iter(source)):
            points=frame.lidar_points;rgb=None if k%2 else rng.random((len(points),3)).astype(np.float32)
            origin=np.zeros(3) if frame.lidar_origins is None else frame.lidar_origins
            kwargs=dict(timestamp=frame.timestamp,origin=origin,beam_ids=frame.beam_ids,return_metadata_valid=frame.return_metadata_valid)
            run_both(new,old,points,rgb=rgb,**kwargs);lean.voxelize_and_update(points,rgb,return_state=False,**kwargs)
        same_map(lean,old,True);same_map(new,old,True);assert len(new.cells)>1000
    finally:source.close()

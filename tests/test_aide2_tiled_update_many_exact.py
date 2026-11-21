"""AIDE2: per-key overhead in the tiled dynamic map (update_many, get_many on resident tiles, query, _observed_values,
page loads). Oracle: verbatim copies of the pre-change code; every stored value, byte count, counter, query result and
written tile payload must be identical."""
import math,sqlite3,sys
from pathlib import Path
import numpy as np
import pytest
from slam_and_twin.tiled_map import DiskBackedVoxelMap,DiskTileStore
from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap

RECORDING=Path.home()/'aethertwin_rec'/'wifi_stationary_60s'


class OracleStore(DiskTileStore):
    """_page, get_many and update_many exactly as before (copied verbatim)."""
    def _page(self,tile):
        if self.closed:raise RuntimeError('Tile store is closed')
        if tile in self.cache:self.cache.move_to_end(tile);return self.cache[tile]
        coords,values=self._read_arrays(tile)
        data={tuple(map(int,key)):value.tolist() for key,value in zip(coords,values)}
        size=sys.getsizeof(data)+sum(self._item_bytes(k,v) for k,v in data.items())
        page={'data':data,'dirty':False,'bytes':size};self.cache[tile]=page;self.resident_bytes+=size
        self._trim(exclude=tile);return page

    def get_many(self,keys):
        """One bounded page visit per tile, restoring caller row order."""
        coords,groups=self._groups(keys);values=np.zeros((len(coords),self.value_width),float);known=np.zeros(len(coords),bool)
        with self.lock:
            for tile,selected in groups:
                if tile in self.cache:
                    self.cache.move_to_end(tile);data=self.cache[tile]['data']
                    for index in selected:
                        value=data.get(tuple(map(int,coords[index])))
                        if value is not None:values[index]=value;known[index]=True
                else:
                    # Read-only misses must not evict/write back a dirty page.
                    # Local integer IDs index ONE tile, never a global grid or
                    # a pairwise distance matrix. The scratch table is bounded.
                    stored,rows=self._read_arrays(tile);self.streamed_reads+=1
                    if not len(stored):continue
                    local=stored%self.tile_size;flat=(local[:,0]*self.tile_size+local[:,1])*self.tile_size+local[:,2]
                    lookup=np.full(self.tile_size**3,-1,np.int32);lookup[flat]=np.arange(len(stored),dtype=np.int32)
                    requested=coords[selected]%self.tile_size;ids=(requested[:,0]*self.tile_size+requested[:,1])*self.tile_size+requested[:,2]
                    locations=lookup[ids];valid=locations>=0
                    values[selected[valid]]=rows[locations[valid]];known[selected[valid]]=True
        return values,known

    def update_many(self,keys,update):
        """Apply per-cell callbacks in original within-key order, grouped by tile.

        Callbacks see value copies. Cache trimming still occurs after EACH
        assignment, so neither entry nor byte budgets are increased temporarily
        across operation boundaries. No transaction-sized tile residency exists.
        """
        if self.readonly:raise TypeError('Tile store is read-only')
        coords,groups=self._groups(keys)
        with self.lock:
            for tile,selected in groups:
                page=self._page(tile);data=page['data']
                for index in selected:
                    key=tuple(map(int,coords[index]));old=data.get(key)
                    value=update(int(index),key,None if old is None else old.copy())
                    try:new=list(map(float,value))
                    except (TypeError,ValueError) as error:raise ValueError('Invalid tile callback value') from error
                    if len(new)!=self.value_width or not all(math.isfinite(v) for v in new):raise ValueError('Invalid tile callback value width/finiteness')
                    before=sys.getsizeof(data);data[key]=new
                    delta=sys.getsizeof(data)-before+self._replaced_bytes(key,new,old)
                    page['bytes']+=delta;self.resident_bytes+=delta;page['dirty']=True
                    if old is None:self.count+=1
                    self._trim(exclude=tile)


class OracleMap(DiskBackedVoxelMap):
    """__init__ (with OracleStore), query, _apply_evidence and _observed_values exactly as before (copied verbatim)."""
    def __init__(self,path,voxel_size=.25,decay_rate=1.,min_log_odds=-4,max_log_odds=4,max_age=float('inf'),*,readonly=False,
                 ray_backend='auto',max_traversal_cells=5_000_000,expiry_tiles_per_update=1,expiry_interval_updates=20,**budgets):
        SparseMultiReturnVoxelMap.__init__(self,voxel_size,decay_rate,min_log_odds,max_log_odds,max_age,ray_backend,max_traversal_cells)
        self.path=Path(path).resolve();self.cells=OracleStore(self.path,readonly=readonly,**budgets);self.readonly=readonly
        self.eager_pruning=False;self.expiry_tiles_per_update=max(0,int(expiry_tiles_per_update));self._expiry_after=None
        self.expiry_interval_updates=max(1,int(expiry_interval_updates));self._expiry_round=0

    def query(self,points,return_known=False):
        coordinates=np.floor(np.asarray(points)[:,:3]/self.voxel_size).astype(np.int64);values=np.zeros(len(coordinates),np.float32);known=np.zeros(len(coordinates),bool)
        stored,present=self.cells.get_many(coordinates)
        for i in np.flatnonzero(present):
            value=stored[i]
            if self.timestamp-float(value[2])<=self.max_age:
                known[i]=True;values[i]=float(value[0])*self.decay_rate**(max(0.,self.timestamp-float(value[2]))/.1)
        return (values,known) if return_known else values

    def _apply_evidence(self,free,hits,penetrations):
        free=list(free);keys=free+list(hits);boundary=len(free)
        def apply(index,key,old):
            previous=0. if old is None else old[0]*self.decay_rate**(max(0.,self.timestamp-old[2])/.1)
            if index<boundary:return [max(self.minimum,previous-.4),0.,self.timestamp]
            perm=penetrations[key] if old is None else .7*old[1]+.3*penetrations[key]
            return [min(self.maximum,previous+hits[key]),perm,self.timestamp]
        self.cells.update_many(keys,apply)

    def _observed_values(self,unique):
        stored,known=self.cells.get_many(unique)
        if not known.all():raise KeyError('Fresh observed voxel missing from tile authority')
        lo=np.array([float(v[0])*self.decay_rate**(max(0.,self.timestamp-float(v[2]))/.1) for v in stored],np.float32)
        return lo,stored[:,1].astype(np.float32)


COUNTERS=('count','resident_bytes','peak_resident_bytes','evictions','page_reads','page_writes','streamed_reads')


def pages(store):
    return [(tile,page['dirty'],page['bytes'],list(page['data'].items())) for tile,page in store.cache.items()]


def stored_rows(store):
    with sqlite3.connect(store.path/'tiles.sqlite') as conn:return conn.execute('SELECT * FROM tiles ORDER BY x,y,z').fetchall()


def same_store(a,b):
    assert [getattr(a,k) for k in COUNTERS]==[getattr(b,k) for k in COUNTERS]
    assert pages(a)==pages(b)  # tile LRU order, dirty flags, byte counts, cell order, keys and float values
    assert a.resident_bytes==sum(page['bytes'] for page in a.cache.values())
    for page in a.cache.values():assert page['bytes']==sys.getsizeof(page['data'])+sum(a._item_bytes(k,v) for k,v in page['data'].items())
    assert stored_rows(a)==stored_rows(b)  # every written tile payload, byte for byte


def same_map(a,b,points):
    same_store(a.cells,b.cells);assert a.timestamp==b.timestamp and a._updates==b._updates
    va,ka=a.query(points,return_known=True);vb,kb=b.query(points,return_known=True)
    assert va.dtype==vb.dtype and np.array_equal(va,vb) and np.array_equal(ka,kb)
    ga,gka=a.cells.get_many(np.floor(points/a.voxel_size).astype(np.int64));gb,gkb=b.cells.get_many(np.floor(points/b.voxel_size).astype(np.int64))
    assert np.array_equal(ga,gb) and np.array_equal(gka,gkb)
    same_store(a.cells,b.cells)  # read paths changed the LRU order and stream counters identically


def same_grid(a,b):
    for name in ('voxel_coords','voxel_centers','voxel_features','log_odds','permeability','point_to_voxel_idx'):
        x,y=getattr(a,name),getattr(b,name);assert x.dtype==y.dtype and np.array_equal(x,y),name


def pair(tmp_path,**kwargs):
    return DiskBackedVoxelMap(tmp_path/'new',**kwargs),OracleMap(tmp_path/'old',**kwargs)


@pytest.mark.parametrize('budget',[dict(tile_size=4,max_resident_tiles=12),dict(tile_size=8,max_resident_tiles=64,max_resident_bytes=400_000),
                                   dict(tile_size=16,max_resident_tiles=128)])
def test_random_evidence_with_repeats_tiles_evictions_reloads_and_clamping_is_identical(tmp_path,budget):
    rng=np.random.default_rng(len(str(budget)))
    new,old=pair(tmp_path,voxel_size=.1,decay_rate=.97,min_log_odds=-1.,max_log_odds=1.2,max_age=1.5,**budget)
    try:
        t=0.
        for step in range(18):
            t+=float(rng.choice([.1,.05,.3,0.]));center=rng.normal(0,.6,3)
            n=int(rng.integers(0,400));free=set(map(tuple,rng.integers(-25,25,(n,3)).tolist()))
            hit_keys=list(map(tuple,(rng.integers(-3,3,(int(rng.integers(0,200)),3))+np.round(center*10).astype(int)).tolist()))
            hits={k:float(rng.choice([.85,.25])) for k in hit_keys};penetrations={k:float(rng.random()) for k in hits}
            free=free.difference(hits)
            for m in (new,old):m.timestamp=t;m._apply_evidence(free,hits,penetrations)
            # repeated keys inside one call, in caller order, through the generic callback path
            keys=rng.integers(-12,12,(int(rng.integers(1,300)),3));keys=np.r_[keys,keys[::-1],keys[:5]]
            for m in (new,old):m.cells.update_many(keys,lambda i,k,v:[(0. if v is None else v[0])+i*1e-3,float(k[0]),t])
            points=rng.uniform(-3,3,(500,3));same_map(new,old,points)
            if step%7==3:
                for m in (new,old):m.prune_expired(2)
            if step%11==5:
                for m in (new,old):m.flush()
            same_store(new.cells,old.cells)
        assert old.cells.evictions>0 or budget['max_resident_tiles']==128
    finally:new.close();old.close()
    assert stored_rows(new.cells)==stored_rows(old.cells)


def test_voxelize_and_update_with_rays_echoes_and_expiry_is_identical(tmp_path):
    rng=np.random.default_rng(7);new,old=pair(tmp_path,voxel_size=.1,decay_rate=.98,max_age=.6,tile_size=8,max_resident_tiles=16)
    try:
        for step in range(15):
            n=400;pts=np.zeros((n,7),np.float32);pts[:,:3]=rng.normal(0,1.5,(n,3));pts[:,3]=rng.random(n)
            pts[:,5]=rng.integers(1,3,n);pts[:,4]=np.minimum(pts[:,5],rng.integers(0,3,n));beam=np.arange(n)//2
            origin=rng.normal(0,.2,3);ra=new.voxelize_and_update(pts,timestamp=.1*step,origin=origin,beam_ids=beam,return_metadata_valid=True)
            rb=old.voxelize_and_update(pts,timestamp=.1*step,origin=origin,beam_ids=beam,return_metadata_valid=True)
            same_grid(ra,rb)
            same_map(new,old,rng.uniform(-4,4,(800,3)))
        assert old.cells.evictions>0
    finally:new.close();old.close()


def test_callback_errors_leave_identical_partial_state(tmp_path):
    a=DiskTileStore(tmp_path/'a',tile_size=4,max_resident_tiles=2);b=OracleStore(tmp_path/'b',tile_size=4,max_resident_tiles=2)
    keys=np.random.default_rng(3).integers(-9,9,(200,3))
    for bad in ([1.,2.],[1.,float('inf'),0.],None,['x',0,0]):
        for s in (a,b):
            with pytest.raises(ValueError):s.update_many(keys,lambda i,k,v,bad=bad:bad if i==150 else [float(i),0.,1.])
        same_store(a,b)
    a.close();b.close()


def test_in_budget_updates_do_not_call_trim_per_key(tmp_path,monkeypatch):
    store=DiskTileStore(tmp_path/'t',tile_size=8,max_resident_tiles=1000,max_resident_bytes=1<<30)
    keys=np.random.default_rng(1).integers(-20,20,(3000,3));calls={'n':0};original=DiskTileStore._trim
    monkeypatch.setattr(DiskTileStore,'_trim',lambda self,exclude=None:(calls.__setitem__('n',calls['n']+1),original(self,exclude))[1])
    store.update_many(keys,lambda i,k,v:[1.,2.,3.])
    assert calls['n']==len(store.cache)  # one per page load, none per key
    store.close()


@pytest.mark.skipif(not RECORDING.exists(),reason='real bench recording not on this machine')
def test_first_40_real_l2_sweeps_through_the_dynamic_map_are_identical(tmp_path):
    from ingestion.recorder_replayer import ReplaySource
    from core.se3_math import transform_points
    budgets=dict(max_resident_tiles=128,max_resident_bytes=67108864,max_disk_bytes=4294967296)  # laptop.yaml tile_budgets
    maps=[pair(tmp_path/'live',voxel_size=.18,decay_rate=.98,max_age=30.,**budgets),
          pair(tmp_path/'tight',voxel_size=.1,decay_rate=.98,max_age=30.,tile_size=8,max_resident_tiles=12,max_disk_bytes=4294967296)]
    source=ReplaySource(RECORDING)
    try:
        for k,frame in zip(range(40),iter(source)):
            pose=frame.sensor_pose_world;pts=frame.lidar_points.copy();pts[:,:3]=transform_points(pose,pts[:,:3])
            origins=pose[:3,3] if frame.lidar_origins is None else transform_points(pose,frame.lidar_origins)
            for new,old in (maps if k<12 else maps[:1]):  # the tight pair evicts (fsync) on every frame: 12 frames
                ra,rb=[m.voxelize_and_update(pts,None,timestamp=frame.timestamp,origin=origins,beam_ids=frame.beam_ids,
                       return_metadata_valid=frame.return_metadata_valid) for m in (new,old)]
                same_grid(ra,rb)
                if k%10==9:same_map(new,old,pts[:,:3].astype(float))
        assert maps[1][1].cells.evictions>0
    finally:
        source.close()
        for new,old in maps:new.close();old.close()
    for new,old in maps:assert stored_rows(new.cells)==stored_rows(old.cells)

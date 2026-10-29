import numpy as np
import pytest
from slam_and_twin.tiled_map import DiskTileStore,DiskBackedVoxelMap,MapBudgetExceeded
from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap


def test_batch_grouping_restores_query_order_and_duplicate_update_order_with_one_tile_cache(tmp_path):
    store=DiskTileStore(tmp_path/'store',tile_size=2,max_resident_tiles=1,max_resident_bytes=12000)
    keys=np.array([[0,0,0],[20,0,0],[0,0,0],[-20,0,0],[20,0,0],[0,0,1]])
    additions=np.array([1.,2.,3.,4.,5.,6.]);expected={}
    for key,increment in zip(keys,additions):
        k=tuple(key);previous=expected.get(k,[0.,0.,0.]);expected[k]=[previous[0]+increment,.2,1.]
    store.update_many(keys,lambda i,key,old:[(old or [0.])[0]+additions[i],.2,1.])
    values,known=store.get_many(keys[[4,0,3,5,2]])
    assert known.all()
    np.testing.assert_array_equal(values,[expected[tuple(key)] for key in keys[[4,0,3,5,2]]])
    assert len(store)==len(expected) and len(store.cache)<=1 and store.resident_bytes<=12000
    assert store.evictions<=7
    store.close()
    loaded=DiskTileStore(tmp_path/'store',tile_size=2,max_resident_tiles=1,max_resident_bytes=12000,readonly=True)
    np.testing.assert_array_equal(loaded.get_many(keys)[0],[expected[tuple(key)] for key in keys])
    with pytest.raises(TypeError):loaded.update_many(keys,lambda *args:[0,0,0])
    loaded.close()


def test_grouped_disk_evidence_is_bit_exact_against_reference_with_echoes_clamping_decay_and_collisions(tmp_path):
    memory=SparseMultiReturnVoxelMap(.25,decay_rate=.96,ray_backend='reference')
    disk=DiskBackedVoxelMap(tmp_path/'map',.25,decay_rate=.96,tile_size=2,max_resident_tiles=2,max_resident_bytes=20000,ray_backend='reference')
    rng=np.random.default_rng(71)
    targets=rng.uniform([-4,-4,.1],[4,4,2],(80,3));origins=rng.uniform(-.2,.2,(80,3))
    targets=np.r_[targets,targets[:10]*.5];origins=np.r_[origins,origins[:10]]
    beam_ids=np.r_[np.arange(80),np.arange(10)];counts=np.r_[np.r_[np.full(10,2),np.ones(70)],np.full(10,2)]
    indices=np.r_[counts[:80],np.ones(10)]
    points=np.c_[targets,np.full(len(targets),.7),indices,counts,np.zeros(len(targets))]
    for stamp in [0,.1,.4,.6,.9,1.,1.3,1.7,2.,2.2,2.5,3.]:
        reference=memory.voxelize_and_update(points,origin=origins,beam_ids=beam_ids,return_metadata_valid=True,timestamp=stamp)
        actual=disk.voxelize_and_update(points,origin=origins,beam_ids=beam_ids,return_metadata_valid=True,timestamp=stamp)
        for field in ('voxel_coords','voxel_features','log_odds','permeability','point_to_voxel_idx'):
            np.testing.assert_array_equal(getattr(actual,field),getattr(reference,field))
    assert dict(disk.cells.items())==memory.cells
    probes=rng.uniform(-5,5,(200,3))
    np.testing.assert_array_equal(disk.query(probes,True)[0],memory.query(probes,True)[0])
    np.testing.assert_array_equal(disk.is_known(probes),memory.is_known(probes))
    assert len(disk.cells.cache)<=2 and disk.cells.peak_resident_bytes<=20000
    disk.close()


def test_grouped_queries_preserve_expiry_unknown_and_do_not_force_dirty_writeback(tmp_path):
    disk=DiskBackedVoxelMap(tmp_path/'expiry',1.,max_age=.5,tile_size=2,max_resident_tiles=2,expiry_tiles_per_update=0)
    disk.cells.update_many([(0,0,0),(1,0,0)],lambda i,key,old:[.85,.2,float(i)])
    disk.timestamp=1.
    values,known=disk.query([[1.5,.5,.5],[0.5,.5,.5],[9.5,.5,.5]],True)
    np.testing.assert_array_equal(known,[True,False,False]);np.testing.assert_array_equal(values,np.array([.85,0,0],np.float32))
    assert disk.cells.conn.execute('SELECT COUNT(*) FROM tiles').fetchone()[0]==0
    assert not disk.cells.get_many(np.empty((0,3),np.int64))[1].any()
    with pytest.raises(ValueError):disk.cells.get_many([[.2,0,0]])
    disk.close()


def test_streaming_cold_reads_never_evict_or_flush_dirty_hot_page(tmp_path):
    store=DiskTileStore(tmp_path/'stream',tile_size=2,max_resident_tiles=1,max_resident_bytes=12000)
    keys=np.array([[i*2,0,0] for i in range(20)])
    for key in keys:store[tuple(key)]=[.85,0.,0.]
    store.flush();store[(0,0,0)]=[1.2,.3,1.]
    before=store.statistics();resident=list(store.cache)
    values,known=store.get_many(keys[::-1]);assert known.all()
    assert values[-1,0]==1.2
    rows={tuple(key):value for _,coords,data in store.iter_tiles() for key,value in zip(coords,data)}
    assert rows[(0,0,0)][0]==1.2 and len(rows)==20
    after=store.statistics()
    assert after['evictions']==before['evictions'] and after['page_writes']==before['page_writes']
    assert list(store.cache)==resident and after['resident_bytes']==before['resident_bytes']
    assert after['tile_query_lookup_max_bytes']==32 and after['streamed_reads']>before['streamed_reads']
    store.close()


def test_reference_ray_budget_fails_without_partial_evidence_or_diagonal_overrejection():
    from slam_and_twin.log_odds_octomap import traverse_beams
    assert len(traverse_beams([0,0,0],[[10,10,10]],1.,max_cells=11))==11
    with pytest.raises(ValueError,match='capacity'):traverse_beams([0,0,0],[[10,0,0]],1.,max_cells=10)
    mapping=SparseMultiReturnVoxelMap(1.,ray_backend='reference',max_traversal_cells=10)
    with pytest.raises(ValueError,match='capacity'):mapping.voxelize_and_update([[10,0,0,.5,1,1,0]],origin=[0,0,0])
    assert not mapping.cells

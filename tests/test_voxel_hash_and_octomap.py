import numpy as np
import pytest
from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap

def test_dynamic_obstacle_clears_without_clearing_unseen_space(tmp_path):
    m=SparseMultiReturnVoxelMap(1.,decay_rate=1.)
    hit=np.array([[2.5,.5,.5,1,1,1,0]],np.float32)
    m.voxelize_and_update(hit,timestamp=0,origin=[.5,.5,.5])
    assert m.query([[2.5,.5,.5]])[0]>.8
    farther=hit.copy();farther[0,0]=4.5
    for k in range(4):m.voxelize_and_update(farther,timestamp=(k+1)*.1,origin=[.5,.5,.5])
    values,known=m.query([[2.5,.5,.5],[5.5,.5,.5]],True)
    assert values[0]<0 and known[0] and not known[1]
    m.save(tmp_path/'map.npz');loaded=SparseMultiReturnVoxelMap.load(tmp_path/'map.npz')
    assert np.allclose(loaded.query([[2.5,.5,.5],[4.5,.5,.5]]),m.query([[2.5,.5,.5],[4.5,.5,.5]]))

def test_temporal_decay_uses_elapsed_time_not_update_count():
    a=SparseMultiReturnVoxelMap(1.,decay_rate=.9);b=SparseMultiReturnVoxelMap(1.,decay_rate=.9)
    p=np.array([[2.5,.5,.5,1,1,1,0]],np.float32)
    for m in (a,b):m.voxelize_and_update(p,timestamp=0,integrate_rays=False)
    for t in np.linspace(.1,1,10):a.voxelize_and_update(np.empty((0,7)),timestamp=t)
    b.voxelize_and_update(np.empty((0,7)),timestamp=1)
    assert np.allclose(a.query(p),b.query(p))


def test_reloaded_map_keys_from_numpy_answer_python_int_lookups_identically(tmp_path):
    from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
    rng=np.random.default_rng(4);points=np.c_[rng.uniform(-2,2,(400,3)),np.ones(400)]
    live=SparseMultiReturnVoxelMap(voxel_size=.2)
    live.voxelize_and_update(points,timestamp=1.,origin=np.zeros(3));live.voxelize_and_update(points[::2],timestamp=1.3,origin=np.zeros(3))
    live.save(tmp_path/'map.npz');loaded=SparseMultiReturnVoxelMap.load(tmp_path/'map.npz')
    assert any(isinstance(k[0],np.integer) for k in loaded.cells)  # load keeps numpy-int tuples
    for a,b in zip(live.query(points,True),loaded.query(points,True)):np.testing.assert_array_equal(a,b)
    np.testing.assert_array_equal(live.occupied_points(),loaded.occupied_points())
    live.voxelize_and_update(points[1::2],timestamp=1.6,origin=np.zeros(3));loaded.voxelize_and_update(points[1::2],timestamp=1.6,origin=np.zeros(3))
    assert len(live.cells)==len(loaded.cells) and all(live.cells[k]==v for k,v in loaded.cells.items())


@pytest.mark.parametrize('rate',[.96,1.])
def test_thresholded_exports_match_a_brute_force_decay_scan_at_boundaries(rate):
    from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
    rng=np.random.default_rng(11);m=SparseMultiReturnVoxelMap(decay_rate=rate)
    values=np.r_[rng.uniform(-4,4,4000),[.6,.4,0.,-.2,-1.,.6000000000000001,.5999999999999999]]
    m.cells={(i,0,0):(float(v),0.,float(rng.uniform(0,2))) for i,v in enumerate(values)}
    for t in (0.,1.,2.5,40.):
        m.timestamp=t
        decayed={k:v[0]*rate**(max(0.,t-v[2])/.1) for k,v in m.cells.items()}
        for threshold in (.6,.4,0.,-.2,-1.,2.):
            above=[k for k,d in decayed.items() if d>threshold];below=[k for k,d in decayed.items() if d<threshold]
            np.testing.assert_array_equal(m.occupied_points(threshold),(np.array(above,float).reshape(-1,3)+.5)*m.voxel_size)
            np.testing.assert_array_equal(m.free_points(threshold),(np.array(below,float).reshape(-1,3)+.5)*m.voxel_size)

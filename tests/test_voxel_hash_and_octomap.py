import numpy as np
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

import numpy as np
from slam_and_twin.log_odds_octomap import traverse_voxels,traverse_beams

def test_batched_traversal_matches_scalar_analytic_and_random_rays():
    rng=np.random.default_rng(13)
    for origin in [np.zeros(3),np.array([-.4,.4,.2]),np.array([.123,-.34,.10])]:
        targets=np.r_[rng.uniform(-3,3,(100,3)),[[0,0,0],[1,1,1],[-1,0,0],[0,2,0],[0,0,-2]]]
        expected=set()
        for target in targets:expected.update(traverse_voxels(origin,target,.2))
        actual=set(map(tuple,traverse_beams(origin,targets,.2)))
        assert actual==expected

def test_mixed_direction_ray_stops_at_boundary_hit():
    cells=list(traverse_voxels([-.4,.4,.2],[0,0,0],.2))
    assert cells[-1]==(0,0,0)
    assert all(-2<=x<=0 and 0<=y<=2 and 0<=z<=1 for x,y,z in cells)

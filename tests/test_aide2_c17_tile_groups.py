"""AIDE2 C17: DiskTileStore._groups (4-key lexsort) was the top self-time entry of the parked GPU live profile."""
import time
import numpy as np
from slam_and_twin.tiled_map import DiskTileStore


def reference_order(coords,tile_size):
    tiles=coords//tile_size;return np.lexsort((np.arange(len(coords)),tiles[:,2],tiles[:,1],tiles[:,0]))


def test_grouping_order_matches_the_lexsort_including_negative_and_duplicate_coordinates(tmp_path):
    store=DiskTileStore(tmp_path/'t',tile_size=16)
    rng=np.random.default_rng(0);coords=rng.integers(-3000,3000,(20000,3));coords[::7]=coords[3]  # duplicates
    out,groups=store._groups(coords);order=np.concatenate([selected for _,selected in groups])
    np.testing.assert_array_equal(order,reference_order(coords,16))
    huge=np.array([[-(1<<62),0,0],[(1<<62),5,5],[0,0,0]]);_,g=store._groups(huge)
    np.testing.assert_array_equal(np.concatenate([s for _,s in g]),reference_order(huge,16))


def old_setup(coords,tile_size):
    """The previous _groups setup: 4-key lexsort plus boundary detection."""
    tiles=coords//tile_size;order=np.lexsort((np.arange(len(coords)),tiles[:,2],tiles[:,1],tiles[:,0]))
    return np.r_[0,np.flatnonzero(np.any(np.diff(tiles[order],axis=0),axis=1))+1,len(order)]


def test_grouping_setup_is_cheaper_than_the_lexsort_it_replaces(tmp_path):
    store=DiskTileStore(tmp_path/'t',tile_size=16);coords=np.random.default_rng(1).integers(-60,60,(150000,3))  # a room: few tiles
    def best(f):
        times=[]
        for _ in range(7):t=time.perf_counter();f();times.append(time.perf_counter()-t)
        return min(times)
    new=best(lambda:store._groups(coords));old=best(lambda:old_setup(coords,16))
    assert new<.7*old,(new,old)

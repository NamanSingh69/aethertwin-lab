import numpy as np
import pytest
from scipy.spatial import cKDTree
from slam_and_twin.tiled_reconstruction import DiskPointCloud


@pytest.mark.parametrize('budget',[2_000_000,500],ids=['one-global-tree','per-tile-trees'])
def test_cached_trees_match_a_global_search_and_avoid_rereading_tiles(tmp_path,budget):
    rng=np.random.default_rng(3)
    store=DiskPointCloud(tmp_path/'surface',voxel_size=.08,max_resident_tiles=4);store.TREE_CACHE_POINTS=budget
    store.add(rng.uniform([-6,-5,0],[6,5,3],(40000,3)));store.flush()
    centroids=np.concatenate(list(store.iter_points()))
    queries=rng.uniform([-7,-6,-.5],[7,6,3.5],(3000,3))
    for radius in (0,.15,2.,None):
        expected=cKDTree(centroids).query(queries,k=1,distance_upper_bound=np.inf if radius is None else np.nextafter(radius,np.inf))[0]
        if radius==0:expected[expected!=0]=np.inf
        np.testing.assert_allclose(store.distances(queries,radius),expected)
    reads=store.cells.page_reads
    store.distances(queries,2.)
    if budget>len(centroids):assert store.cells.page_reads==reads
    store.add(np.array([[0.,0.,1.]]))
    assert store.distances(np.array([[0.,0.,1.]]),.5)[0]<1e-9

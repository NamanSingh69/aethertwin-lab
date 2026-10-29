"""Moving the world origin cannot change range-adaptive novelty decisions."""
from types import SimpleNamespace
import numpy as np
from core.pipeline import detections_from_prediction
from slam_and_twin.spatial_diff_engine import SpatialDiffEngine


def test_detection_range_is_relative_to_sensor_not_world_origin():
    xyz=np.array([[1+x,y,1+z] for x in (0.,.05) for y in (0.,.05) for z in (0.,.05)])
    probabilities=np.zeros((len(xyz),7));probabilities[:,4]=1
    config={'perception':{'confidence_threshold':.3},'tracking':{'cluster_eps':.4,'min_cluster_points':4}}
    results=[]
    for offset in (np.zeros(3),np.array([50.,0.,0.])):
        points=xyz+offset
        diff=SpatialDiffEngine(points+np.array([0.,.3,0.]))
        prepared=SimpleNamespace(voxels=SimpleNamespace(voxel_centers=points))
        results.append(detections_from_prediction(prepared,probabilities,np.zeros((len(xyz),3)),
            np.ones((len(xyz),8)),diff,config,False,offset))
    assert len(results[0])==len(results[1])==1
    np.testing.assert_allclose(results[1][0].position-results[0][0].position,[50,0,0])

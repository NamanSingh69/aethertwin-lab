"""Reject malformed boundary data before it reaches geometry or supervision."""
from dataclasses import replace
import numpy as np
import pytest
from core.contracts import DiffedLabFrame,GroundTruthFrame,MultiModalSensorFrame,PlannedTrajectory,VoxelGridState
from ingestion.calibration import calibrate_camera_lidar


def truth():
    return GroundTruthFrame([0,2],np.zeros((2,3)),[0,1],[False,True],np.eye(4))


@pytest.mark.parametrize('updates',[{'flow':np.zeros((1,3))},{'semantic':[0,7]},
    {'semantic':[0,1.5]},{'flow_valid':[0,2]},{'instance_ids':[0,-2]},
    {'camera_semantic':np.zeros((2,2))}])
def test_supervised_contract_rejects_misaligned_or_invalid_targets(updates):
    with pytest.raises(ValueError):replace(truth(),**updates)


@pytest.mark.parametrize('updates',[{'sweep_duration':float('nan')},
    {'rgb':np.zeros((2,3))},{'depth':-np.ones((2,3))},
    {'gemini_rgbd_cloud':np.zeros((3,5))},{'rplidar_s2_points':np.zeros((3,4))},
    {'painted_features':np.zeros((1,3))},{'sensor_pose_world':np.diag([1.1,1,1,1])},
    {'imu_to_lidar':np.diag([-1,1,1,1])}])
def test_sensor_contract_rejects_unusable_geometry_and_images(updates):
    with pytest.raises(ValueError):replace(MultiModalSensorFrame(0,0,np.empty((0,7))),**updates)


def test_diff_snapshot_owns_readonly_foreground_and_rejects_misaligned_color():
    voxels=VoxelGridState(.2,np.empty((0,3)),np.empty((0,3)),np.empty((0,12)),np.empty(0),np.empty(0),np.empty(0))
    raw=np.zeros((2,7));rgb=np.ones((1,3))
    frame=DiffedLabFrame(0,0,.1,raw,raw[:1],rgb,voxels,np.zeros((3,3)))
    raw[:]=10;rgb[:]=0
    assert not frame.deskewed_full_points.any() and frame.foreground_rgb.sum()==3
    with pytest.raises(ValueError):frame.foreground_points[0,0]=1
    with pytest.raises(ValueError):replace(frame,foreground_rgb=np.zeros((2,3)))


@pytest.mark.parametrize('times',[[0,0],[1,0],[-.1,.2]])
def test_trajectory_rejects_noncausal_time_parameters(times):
    with pytest.raises(ValueError):PlannedTrajectory('ok',np.zeros((2,3)),times)


def test_synthetic_calibration_provenance_and_image_profile_are_preserved(tmp_path):
    from pathlib import Path
    source=Path(__file__).resolve().parents[1]/'assets/calibration/synthetic_pairs.npz'
    with np.load(source,allow_pickle=False) as data:
        result,report=calibrate_camera_lidar(data['lidar_points'],data['image_points'],data['K'],data['distortion'],
            tmp_path/'synthetic.json',image_size=data['image_size'],measured=data['measured'].item())
    assert result.measured is False and report['measured'] is False
    assert report['image_size']==[640,480] and report['calibration_id'].startswith('synthetic-board-v1-')
    assert report['reprojection_rmse_pixels']<1e-4

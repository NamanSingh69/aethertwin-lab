"""AIDE2 C31: fuse_frame back-projected the depth image with uncached undistortion every frame (5.6 ms on a real
640x400 frame; the shared DEPTH_RAY_CACHE gives the same rows in 1.5 ms, read-only review 2026-11-05)."""
from dataclasses import replace
import numpy as np
from core.contracts import CameraCalibration
from ingestion.sensor_synchronizer import backproject_depth,fuse_frame
from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
from perception_ml.depth_cache import DEPTH_RAY_CACHE
import yaml
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def test_fused_depth_cloud_is_identical_and_comes_from_the_ray_cache():
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'))
    source=SyntheticLabSimulator(config['seed'],config,mobile=False)
    try:frame=source.next_frame()
    finally:source.close()
    calibration=CameraCalibration(frame.calibration.K,frame.calibration.T_lidar_camera,np.array([.04,-.01,.001,.002,0.]),'c31-distorted')
    frame=replace(frame,calibration=calibration)
    expected=backproject_depth(frame.depth,calibration,3,frame.rgb,camera_pose_in_lidar=frame.camera_pose_in_lidar)
    before=DEPTH_RAY_CACHE.hits+DEPTH_RAY_CACHE.misses
    first=fuse_frame(frame);second=fuse_frame(frame)
    np.testing.assert_array_equal(first.gemini_rgbd_cloud,expected)
    np.testing.assert_array_equal(second.gemini_rgbd_cloud,expected)
    assert DEPTH_RAY_CACHE.hits+DEPTH_RAY_CACHE.misses-before==2

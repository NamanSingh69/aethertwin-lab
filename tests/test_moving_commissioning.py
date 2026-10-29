"""Moving commissioning and calibrated TSDF regressions, without runtime labels."""
from dataclasses import replace
import numpy as np
import pytest
import cv2
from scipy.spatial import cKDTree
from core.contracts import IMUSample,MultiModalSensorFrame,CameraCalibration
from core.se3_math import so3_exp,transform_points
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
from slam_and_twin.static_twin_builder import StaticTwinBuilder,rectify_rgbd_for_tsdf
from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
from tracking_and_navigation.observed_navigation import integrate_commissioning_frame


def static_surfaces(seed=9):
    rng=np.random.default_rng(seed)
    floor=rng.uniform([-2,-2,0],[2,2,0],(500,3))
    left=rng.uniform([-2,-2,0],[-2,2,2.5],(300,3))
    rear=rng.uniform([-2,2,0],[2,2,2.5],(300,3))
    return np.r_[floor,left,rear]


def moving_scans(count=3,velocity=1.,yaw_rate=.6):
    world=static_surfaces();offsets=np.linspace(0,.1,len(world))
    frames=[]
    for index in range(count):
        start=index*.1;times=start+offsets
        xyz=np.array([(point-np.array([velocity*t,0,1.4]))@so3_exp([0,0,yaw_rate*t]) for point,t in zip(world,times)])
        points=np.c_[xyz,np.ones((len(world),3)),offsets]
        imu=tuple(IMUSample(t,[0,0,yaw_rate],[0,0,9.80665]) for t in np.linspace(start,start+.1,21))
        initial=np.eye(4);initial[2,3]=1.4
        frames.append(MultiModalSensorFrame(index,start,points,imu,sensor_pose_world=initial,
            beam_ids=np.arange(len(world)),diagnostics={'pose_prior_valid':False}))
    return frames,world


def test_builder_prepares_causal_translation_rotation_and_reuses_immutable_frames():
    frames,world=moving_scans()
    before=frames[0].lidar_points.copy()
    builder=StaticTwinBuilder(.10,poisson_depth=5,initial_velocity=[1,0,0]).build(frames)
    assert isinstance(builder.prepared_frames,tuple) and len(builder.prepared_frames)==3
    for index,frame in enumerate(builder.prepared_frames):
        assert frame.diagnostics['coordinate_reference']=='lidar_sweep_end'
        assert frame.diagnostics['commissioning_preparation_mode']=='causal_mobile'
        assert frame.lidar_origins.shape==(len(frame.lidar_points),3)
        observed=transform_points(builder.poses[index],frame.lidar_points[:,:3])
        assert np.quantile(cKDTree(world).query(observed)[0],.95)<.018
        expected=np.eye(4);expected[:3,:3]=so3_exp([0,0,.6*(index+1)*.1]);expected[:3,3]=[(index+1)*.1,0,1.4]
        assert np.linalg.norm(builder.poses[index][:3,3]-expected[:3,3])<.015
        assert not frame.lidar_origins.flags.writeable
    np.testing.assert_array_equal(frames[0].lidar_points,before)
    assert frames[0].lidar_origins is None
    assert builder.report['commissioning']['initial_velocity_source']=='provided_world_prior'
    assert builder.report['commissioning']['constant_velocity_observable_from_imu'] is False
    np.testing.assert_allclose(builder.prepared_frames[0].lidar_origins[0],[-.1*np.cos(.06),.1*np.sin(.06),0],atol=1e-8)


def test_builder_reports_unobservable_zero_velocity_bootstrap_and_uses_icp_cadence():
    frames,_=moving_scans(count=3,velocity=.5,yaw_rate=0.)
    builder=StaticTwinBuilder(.12,poisson_depth=5).build(frames)
    assert 'unobservable' in builder.report['commissioning']['initial_velocity_source']
    displacement=builder.poses[2][0,3]-builder.poses[1][0,3]
    assert .025<displacement<.075
    assert np.linalg.norm(builder.prepared_frames[2].lidar_origins[0])>.02


def test_prepared_rotating_input_is_not_deskewed_twice():
    frames,_=moving_scans(count=1)
    prepared=ContinuousTimeIMUDeskewer(frames[0].sensor_pose_world,initial_velocity=[1,0,0]).deskew_frame(frames[0])
    builder=StaticTwinBuilder(.12,poisson_depth=5).build([prepared])
    np.testing.assert_array_equal(builder.prepared_frames[0].lidar_points,prepared.lidar_points)
    np.testing.assert_array_equal(builder.prepared_frames[0].lidar_origins,prepared.lidar_origins)
    assert builder.report['commissioning']['frames'][0]['mode']=='prepared'
    missing=replace(prepared,lidar_origins=None)
    with pytest.raises(ValueError,match='acquisition origins'):StaticTwinBuilder(.12,5).build([missing])


def test_nonchronological_raw_mobile_input_requires_explicit_station_mode():
    frames,_=moving_scans(count=2)
    with pytest.raises(ValueError,match='Overlapping'):
        StaticTwinBuilder(.12,5).build([frames[0],replace(frames[1],timestamp=0,imu_samples=frames[0].imu_samples)])


def test_explicit_independent_station_resets_at_rest_and_accepts_known_no_imu_capture():
    world=static_surfaces();points=np.c_[world-[0,0,1.4],np.ones((len(world),3)),np.zeros(len(world))]
    pose=np.eye(4);pose[2,3]=1.4
    raw=MultiModalSensorFrame(0,0,points,sensor_pose_world=pose,diagnostics={'commissioning_stationary':True})
    twin=StaticTwinBuilder(.12,5).build([raw])
    assert twin.report['commissioning']['frames'][0]['mode']=='independent_station'
    assert twin.prepared_frames[0].diagnostics['commissioning_static_without_imu'] is True
    with pytest.raises(ValueError,match='IMU coverage'):
        StaticTwinBuilder(.12,5,commissioning_mode='mobile').build([raw])


def test_motion_aware_commissioning_carves_actual_origins_and_rejects_raw_unknown_motion():
    velocity=10.;yaw=.8;pose=np.eye(4);pose[:3,3]=[-1,0,.625]
    targets=np.array([[1.125,1.125,.625],[1.125,-1.125,.625]])
    offsets=np.array([0.,.1]);raw=[]
    for point,t in zip(targets,offsets):raw.append((point-(pose[:3,3]+[velocity*t,0,0]))@so3_exp([0,0,yaw*t]))
    imu=tuple(IMUSample(t,[0,0,yaw],[0,0,9.80665]) for t in np.linspace(0,.1,21))
    frame=MultiModalSensorFrame(0,0,np.c_[raw,np.ones((2,3)),offsets],imu,sensor_pose_world=pose)
    mapping=SparseMultiReturnVoxelMap(.25)
    with pytest.raises(ValueError,match='stationary capture'):integrate_commissioning_frame(mapping,frame)
    assert not mapping.cells
    prepared=ContinuousTimeIMUDeskewer(pose,initial_velocity=[velocity,0,0]).deskew_frame(frame)
    counts=integrate_commissioning_frame(mapping,prepared)
    assert counts['lidar_rays']==2
    assert not mapping.is_known([[.625,.625,.625]])[0]
    assert np.all(mapping.query(targets)>0)
    np.testing.assert_allclose(transform_points(prepared.sensor_pose_world,prepared.lidar_origins),[[-1,0,.625],[0,0,.625]],atol=1e-8)
    # Zero gyro does not establish stationarity: constant translation is not
    # observable from a gravity-only IMU and still needs preparation/declaration.
    translation=replace(frame,imu_samples=tuple(IMUSample(t,[0,0,0],[0,0,9.80665]) for t in [0,.1]))
    with pytest.raises(ValueError,match='stationary capture'):integrate_commissioning_frame(SparseMultiReturnVoxelMap(.25),translation)


def test_prepared_depth_uses_full_capture_pose_and_original_distorted_pixels():
    K=np.array([[1.,0,1],[0,1.,1],[0,0,1]])
    calibration=CameraCalibration(K,np.eye(4),distortion=np.array([.5,0,0,0,0]))
    depth=np.zeros((3,3),np.float32);depth[1,2]=2.
    effective=np.eye(4);effective[:3,:3]=so3_exp([0,.2,0]);effective[:3,3]=[-.875,.125,.625]
    frame=MultiModalSensorFrame(0,0,np.empty((0,7)),rgb=np.zeros((3,3,3),np.uint8),depth=depth,
        calibration=calibration,camera_timestamp=.05,camera_pose_in_lidar=effective,lidar_origins=np.empty((0,3)),
        diagnostics={'coordinate_reference':'lidar_sweep_end'})
    mapping=SparseMultiReturnVoxelMap(.25);integrate_commissioning_frame(mapping,frame)
    # Off-axis Brown observation: x_distorted=x+.5*x^3=1. Solve
    # independently to check the original measured ray, not a rectified pixel.
    x=1.
    for _ in range(12):x-=(x+.5*x**3-1)/(1+1.5*x*x)
    endpoint=transform_points(effective,[[2*x,0,2.]])
    assert mapping.query(endpoint)[0]>0
    assert not mapping.is_known(transform_points(effective,[[2,0,2.]]))[0]
    assert not mapping.is_known([[.125,.125,2.125]])[0]
    missing=replace(frame,camera_pose_in_lidar=None)
    with pytest.raises(ValueError,match='capture-time'):integrate_commissioning_frame(SparseMultiReturnVoxelMap(.25),missing)


def distorted_plane(width=80,height=64):
    K=np.array([[70.,0,(width-1)/2],[0,71.,(height-1)/2],[0,0,1.]])
    distortion=np.array([.18,-.04,.008,-.004,.01]);calibration=CameraCalibration(K,np.eye(4),distortion)
    yy,xx=np.indices((height,width));xy=cv2.undistortPoints(np.c_[xx.ravel(),yy.ravel()].astype(float)[:,None],K,distortion)[:,0]
    normal=np.array([.3,-.15,1.]);depth=(1.8/(np.c_[xy,np.ones(len(xy))]@normal)).reshape(height,width).astype(np.float32)
    depth[29:35,37:43]=0
    rgb=np.zeros((height,width,3),np.uint8);rgb[...,0]=xx;rgb[...,1]=yy
    return calibration,rgb,depth,normal


def test_tsdf_rectification_retains_measured_K_invalid_holes_and_tilted_plane():
    calibration,rgb,depth,normal=distorted_plane();before=calibration.K.copy()
    rectified_rgb,rectified_depth,report=rectify_rgbd_for_tsdf(rgb,depth,calibration)
    yy,xx=np.indices(depth.shape);rays=np.stack([(xx-calibration.K[0,2])/calibration.K[0,0],(yy-calibration.K[1,2])/calibration.K[1,1],np.ones(depth.shape)],axis=-1)
    expected=1.8/(rays@normal);valid=rectified_depth>0
    assert np.quantile(np.abs(rectified_depth[valid]-expected[valid]),.99)<.007
    assert rectified_depth[31,39]==0 and report['K_rescaled'] is False
    assert report['rectified'] is True and rectified_rgb.shape==rgb.shape
    np.testing.assert_array_equal(calibration.K,before)


def test_actual_tsdf_uses_timed_camera_pose_and_rectifies_nonzero_Brown_images():
    calibration,rgb,depth,normal=distorted_plane()
    world=static_surfaces();pose=np.eye(4);pose[2,3]=1.4
    effective=np.eye(4);effective[:3,:3]=so3_exp([0,0,.25]);effective[:3,3]=[.2,0,.4]
    packed=np.c_[world-[0,0,1.4],np.ones((len(world),3)),np.zeros(len(world))]
    frame=MultiModalSensorFrame(0,0,packed,sensor_pose_world=pose,lidar_origins=np.zeros((len(world),3)),
        rgb=rgb,depth=depth,calibration=calibration,camera_timestamp=.05,camera_pose_in_lidar=effective,
        diagnostics={'coordinate_reference':'lidar_sweep_end'})
    twin=StaticTwinBuilder(.06,5).build([frame])
    assert twin.report['tsdf_rectification'][0]['rectified'] is True
    vertices=np.asarray(twin.tsdf_mesh.vertices);assert len(vertices)>100
    camera=twin.floor_transform@pose@effective;normal_world=camera[:3,:3]@normal
    offset=1.8+normal_world@camera[:3,3]
    distances=np.abs(vertices@normal_world-offset)/np.linalg.norm(normal_world)
    assert np.quantile(distances,.95)<.025


def test_calibrated_independent_stations_can_share_capture_timestamps():
    world=static_surfaces();frames=[]
    for index,yaw in enumerate([0.,.25]):
        pose=np.eye(4);pose[:3,:3]=so3_exp([0,0,yaw]);pose[:3,3]=[index*.7,0,1.4]
        xyz=(world-pose[:3,3])@pose[:3,:3]
        imu=(IMUSample(0,[0,0,0],[0,0,9.80665]),IMUSample(.1,[0,0,0],[0,0,9.80665]))
        frames.append(MultiModalSensorFrame(index,0,np.c_[xyz,np.ones((len(world),3)),np.linspace(0,.1,len(world))],imu,
            sensor_pose_world=pose,diagnostics={'pose_prior_valid':True,'pose_prior_source':'commissioning station calibration'}))
    twin=StaticTwinBuilder(.12,5).build(frames)
    assert twin.report['commissioning']['mode_counts']=={'independent_station':2}
    assert twin.report['commissioning']['initial_velocity_source']=='known_zero_at_independent_station_starts'
    for frame in twin.prepared_frames:np.testing.assert_allclose(frame.lidar_origins,0,atol=1e-9)
    np.testing.assert_allclose(twin.poses[1][:3,3],[.7,0,1.4],atol=.015)

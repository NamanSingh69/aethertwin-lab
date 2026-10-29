import numpy as np
import pytest
from core.contracts import CameraCalibration,IMUSample,MultiModalSensorFrame
from core.se3_math import so3_exp,transform_points
from ingestion.sensor_synchronizer import paint_points,backproject_depth,SensorSynchronizer
from ingestion.calibration import calibrate_camera_lidar
from ingestion.recorder_replayer import FrameRecorder,ReplaySource
from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap,traverse_voxels
from slam_and_twin.icp_pose_graph import point_to_plane_icp,ICPPoseGraph
from slam_and_twin.spatial_diff_engine import SpatialDiffEngine
from slam_and_twin.static_twin_builder import StaticTwinBuilder,fit_floor_transform
from ingestion.gemini_333l_driver import decode_color


def test_pointwise_deskew_known_constant_motion_and_carried_velocity():
    w=.8;duration=.1;offsets=np.linspace(0,duration,101)
    world=np.c_[np.full(101,4.),np.linspace(-1,1,101),np.full(101,1.)]
    raw=[]
    for p,t in zip(world,offsets):raw.append((p-np.array([t,0,0]))@so3_exp(np.array([0,0,w*t])))
    points=np.c_[raw,np.ones((101,3)),offsets].astype(np.float64)
    samples=[IMUSample(t,[0,0,w],[0,0,9.80665]) for t in np.linspace(0,duration,21)]
    d=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0]);result=d.deskew_sweep(points,samples,0,duration)
    expected=(world-[duration,0,0])@so3_exp(np.array([0,0,w*duration]))
    np.testing.assert_allclose(result[:,:3],expected,atol=1e-8)
    samples2=[IMUSample(t,[0,0,w],[0,0,9.80665]) for t in np.linspace(.1,.2,21)]
    d.deskew_sweep(points,samples2,.1,.1)
    np.testing.assert_allclose(d.pose_world[:3,3],[.2,0,0],atol=1e-8)
    np.testing.assert_allclose(d.velocity,[1,0,0],atol=1e-8)
    with pytest.raises(ValueError):d.deskew_sweep(points,samples[:2],0,.1)


def test_imu_integrates_observed_gap_between_nonoverlapping_sweeps():
    points=np.array([[2.,0,0,1,1,1,.04]])
    deskewer=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0])
    first=[IMUSample(t,[0,0,0],[0,0,9.80665]) for t in np.linspace(0,.08,9)]
    deskewer.deskew_sweep(points,first,0,.08)
    second=[IMUSample(t,[0,0,0],[0,0,9.80665]) for t in np.linspace(.08,.18,11)]
    deskewer.deskew_sweep(points,second,.1,.08)
    np.testing.assert_allclose(deskewer.pose_world[:3,3],[.18,0,0],atol=1e-8)
    assert deskewer.last_diagnostics['inter_sweep_integration_seconds']==pytest.approx(.02)


def test_dda_multi_return_evidence_and_unobserved_space(tmp_path):
    m=SparseMultiReturnVoxelMap(1.)
    p=np.array([[2.5,.5,.5,.4,1,2,0],[4.5,.5,.5,.7,2,2,0]])
    state=m.voxelize_and_update(p,timestamp=0,origin=[.5,.5,.5],beam_ids=[0,0],return_metadata_valid=True)
    q=np.c_[np.arange(6)+.5,np.full(6,.5),np.full(6,.5)]
    lo,known=m.query(q,True)
    np.testing.assert_allclose(lo,[-.4,-.4,.25,-.4,.85,0],atol=1e-6)
    assert not known[-1] and state.permeability[0]>.4
    m.save(tmp_path/'map.npz');loaded=SparseMultiReturnVoxelMap.load(tmp_path/'map.npz')
    np.testing.assert_array_equal(loaded.query(q),lo)
    assert list(traverse_voxels([.5,.5,.5],[2.5,2.5,.5],1.))==[(0,0,0),(1,1,0),(2,2,0)]


def test_hash_collision_does_not_merge_voxels():
    coords=np.array([[0,0,0],[19349663,73856093,0]],np.int64)
    assert len(np.unique(SparseMultiReturnVoxelMap.compute_spatial_hash(coords)))==1
    points=np.zeros((2,7),np.float64);points[:,:3]=coords+.25;points[:,4:6]=1
    m=SparseMultiReturnVoxelMap(1.);out=m.voxelize_and_update(points,integrate_rays=False)
    assert len(out.voxel_coords)==2 and len(m.cells)==2


def test_painting_projection_occlusion_and_backprojection():
    K=np.array([[2,0,1],[0,2,1],[0,0,1.]])
    c=CameraCalibration(K,np.eye(4));rgb=np.full((3,3,3),[64,128,192],np.uint8);depth=np.full((3,3),2,np.float32)
    p=np.array([[0,0,2],[0,0,4],[1,0,-2]],float)
    painted,valid=paint_points(p,rgb,depth,c)
    np.testing.assert_array_equal(valid,[True,False,False]);np.testing.assert_allclose(painted[0],[64,128,192]/np.array(255.),atol=1e-6)
    xyz=backproject_depth(depth,c);assert np.any(np.all(np.isclose(xyz,[0,0,2]),axis=1))


def test_replay_sensor_fidelity_and_seeded_scene_variation(tmp_path):
    s=SyntheticLabSimulator(seed=1,config={'lidar_points':500,'width':40,'height':25})
    f=s.next_frame();g=s.next_frame()
    with FrameRecorder(tmp_path/'record',chunk_size=1) as writer:writer.write(f);writer.write(g)
    result=list(ReplaySource(tmp_path/'record'))
    for before,after in zip([f,g],result):
        np.testing.assert_array_equal(before.lidar_points,after.lidar_points)
        np.testing.assert_array_equal(before.rgb,after.rgb)
        assert before.timestamp==after.timestamp and before.imu_samples[-1].timestamp==after.imu_samples[-1].timestamp
        assert before.capabilities==after.capabilities
    s2=SyntheticLabSimulator(seed=1,config={'lidar_points':500,'width':40,'height':25});h=s2.next_frame()
    np.testing.assert_array_equal(f.lidar_points,h.lidar_points)
    s3=SyntheticLabSimulator(seed=2,config={'lidar_points':500,'width':40,'height':25});other=s3.next_frame()
    assert not np.array_equal(f.lidar_points,other.lidar_points)
    assert not hasattr(f,'semantic')
    s.close();s2.close();s3.close()


def test_synchronizer_requires_bracketing_imu_and_camera():
    sync=SensorSynchronizer();f=MultiModalSensorFrame(0,0,np.empty((0,7)))
    c=CameraCalibration(np.eye(3),np.eye(4));sync.push_lidar(f)
    sync.push_camera(.1,np.zeros((2,2,3),np.uint8),np.ones((2,2),np.float32),c)
    sync.push_imu(IMUSample(.01,[0,0,0],[0,0,9.80665]));assert sync.synchronize() is None
    sync.push_imu(IMUSample(0,[0,0,0],[0,0,9.80665]));sync.push_imu(IMUSample(.1,[0,0,0],[0,0,9.80665]))
    # Real streams are chronological; deliberately reset the out-of-order input.
    sync.imu=type(sync.imu)(sorted(sync.imu,key=lambda s:s.timestamp),maxlen=sync.imu.maxlen)
    assert sync.synchronize() is not None


def test_calibration_recovers_known_transform():
    import cv2
    rng=np.random.default_rng(2);p=rng.uniform([-1,-1,3],[1,1,6],(40,3));K=np.array([[500,0,320],[0,500,240],[0,0,1.]])
    r=np.array([.04,-.08,.02]);t=np.array([.1,-.03,.02]);uv,_=cv2.projectPoints(p,r,t,K,np.zeros(5))
    c,report=calibrate_camera_lidar(p,uv.reshape(-1,2),K)
    T=np.eye(4);T[:3,:3]=cv2.Rodrigues(r)[0];T[:3,3]=t
    np.testing.assert_allclose(c.T_lidar_camera@T,np.eye(4),atol=1e-5);assert report['reprojection_rmse_pixels']<1e-4


def test_spatial_diff_preserves_new_obstacle():
    map_points=np.array([[0,0,0],[1,0,0],[1,1,1]],float);engine=SpatialDiffEngine(map_points)
    assert engine.foreground_mask([[1,1,1],[0,2,1],[0,2,0]]).tolist()==[False,True,False]


def test_icp_recovers_rigid_offset_on_three_planes():
    rng=np.random.default_rng(0);a=rng.uniform(-1,1,(1000,3));a[:333,0]=0;a[333:666,1]=0;a[666:,2]=0
    T=np.eye(4);T[:3,:3]=so3_exp(np.array([.02,-.01,.015]));T[:3,3]=[.04,-.03,.02]
    b=transform_points(T,a);estimate,diag=point_to_plane_icp(a,b,voxel=.08)
    np.testing.assert_allclose(estimate,T,atol=.015);assert diag['fitness']>.95


def test_mobile_pose_graph_window_retains_world_anchor():
    rng=np.random.default_rng(5);cloud=rng.uniform(-1,1,(400,3));cloud[:130,0]=0;cloud[130:260,1]=0;cloud[260:,2]=0
    graph=ICPPoseGraph(voxel_size=.1,max_keyframes=4)
    for i in range(7):
        pose=np.eye(4);pose[:3,3]=[3+.02*i,0,0]
        graph.add_scan(cloud-[.02*i,0,0],pose)
    assert len(graph.clouds)==4 and graph.total_scans==7 and graph.dropped_keyframes==3
    assert all(0<=a<b<4 for a,b,_,_ in graph.edges)
    assert graph.poses[0][0,3]>3
    graph.optimize();assert graph.poses[0][0,3]>3


def test_rgbd_ground_truth_is_separate_material_flow_side_channel():
    s=SyntheticLabSimulator(seed=0,config={'lidar_points':500,'width':80,'height':50})
    s.next_frame();frame=s.next_frame();gt=s.last_ground_truth
    assert gt.camera_flow.shape==(*frame.depth.shape,3)
    assert gt.camera_instance_ids.shape==frame.depth.shape
    assert gt.camera_flow_valid.shape==frame.depth.shape
    assert not hasattr(frame,'camera_flow') and not hasattr(frame,'camera_semantic')
    valid=frame.depth>0;dynamic=np.isin(gt.camera_semantic,[2,3])&gt.camera_flow_valid
    static=valid&~np.isin(gt.camera_semantic,[2,3])
    assert dynamic.any() and np.linalg.norm(gt.camera_flow[dynamic],axis=1).max()>.001
    np.testing.assert_array_equal(gt.camera_flow[static],0)
    assert np.all(gt.camera_instance_ids[valid]>=0)
    assert not gt.camera_flow.flags.writeable
    s.close()


def test_floor_reconstruction_accepts_elevated_scanner_origin():
    rng=np.random.default_rng(4)
    floor=rng.uniform([-3,-3,-1.6],[3,3,-1.6],(800,3));floor[:,2]+=rng.normal(0,.002,len(floor))
    wall=rng.uniform([-3,-3,-1.6],[-3,3,1.4],(400,3))
    p=np.r_[floor,wall];packed=np.c_[p,np.ones((len(p),3)),np.zeros(len(p))]
    frame=MultiModalSensorFrame(0,0,packed,diagnostics={'commissioning_stationary':True})
    twin=StaticTwinBuilder(voxel_size=.15,poisson_depth=5).build([frame])
    assert abs(twin.floor_transform[2,3]-1.6)<.02
    assert twin.report['floor_fit']['support_points']>100
    assert abs(twin.report['floor_fit']['height_before_alignment']+1.6)<.02
    with pytest.raises(ValueError):fit_floor_transform(wall,normals=np.tile([1,0,0],(len(wall),1)))


def test_camera_decodes_enumerated_compressed_and_packed_formats():
    import cv2
    rgb=np.full((8,10,3),[220,40,15],np.uint8)
    np.testing.assert_array_equal(decode_color(rgb,10,8,'RGB'),rgb)
    np.testing.assert_array_equal(decode_color(rgb[...,::-1],10,8,'BGR'),rgb)
    ok,jpeg=cv2.imencode('.jpg',rgb[...,::-1]);assert ok
    np.testing.assert_allclose(decode_color(jpeg,10,8,'MJPG').astype(float),rgb,atol=4)
    packed=np.tile(np.array([82,90,82,240],np.uint8),(2,1)).reshape(2,2,2)
    red=decode_color(packed,2,2,'YUYV')
    assert red[...,0].min()>250 and red[...,1:].max()<4
    with pytest.raises(ValueError):decode_color(np.zeros(10,np.uint8),10,8,'MJPG')


def test_camera_raw_timestamps_and_clock_fit_survive_synchronization():
    sync=SensorSynchronizer();imu=(IMUSample(0,[0,0,0],[0,0,9.80665]),IMUSample(.1,[0,0,0],[0,0,9.80665]))
    frame=MultiModalSensorFrame(0,0,np.empty((0,7)),imu)
    sync.push_lidar(frame);sync.push_camera(.1,np.zeros((2,2,3),np.uint8),np.ones((2,2),np.float32),CameraCalibration(np.eye(3),np.eye(4)),
        metadata={'raw_timestamp':123.4,'camera_device_us':123400000,'camera_depth_device_us':123399000,'clock_fit_residual_seconds':.0002,'color_depth_delta_seconds':.001,'color_format':'MJPG'})
    out=sync.synchronize();assert out.raw_timestamps['camera']==123.4 and out.raw_timestamps['camera_device_us']==123400000
    assert out.diagnostics['camera_clock_fit_residual_seconds']==.0002


def test_gemini_negotiates_mjpg_when_rgb_profile_is_unavailable(monkeypatch):
    import sys,types,cv2
    from ingestion.gemini_333l_driver import Gemini335LDriver
    class Profile:
        def __init__(self,fmt):self.fmt=fmt
        def as_video_stream_profile(self):return self
        def get_width(self):return 4
        def get_height(self):return 2
        def get_fps(self):return 30
        def get_format(self):return self.fmt
        def get_intrinsic(self):return types.SimpleNamespace(width=4,height=2,fx=1.,fy=1.,cx=0.,cy=0.)
        def get_distortion(self):return types.SimpleNamespace(model='NONE',k1=0.,k2=0.,p1=0.,p2=0.,k3=0.,k4=0.,k5=0.,k6=0.)
    class Options:
        def __init__(self,fmt):self.profile=Profile(fmt)
        def get_count(self):return 1
        def get_stream_profile_by_index(self,index):return self.profile
    class DeviceList:
        def get_count(self):return 1
        def get_device_by_index(self,index):return 'gemini-test-device'
    class Context:
        def query_devices(self):return DeviceList()
    class Config:
        def __init__(self):self.streams=[];self.align=None
        def enable_stream(self,profile):self.streams.append(profile)
        def set_align_mode(self,mode):self.align=mode
        def set_depth_scale_require(self,enabled):self.scale=enabled
    class Color:
        def get_width(self):return 4
        def get_height(self):return 2
        def get_format(self):return 'MJPG'
        def get_timestamp_us(self):return 123456000
        def get_stream_profile(self):return Profile('MJPG')
        def get_data(self):return cv2.imencode('.jpg',np.full((2,4,3),[10,30,200],np.uint8))[1]
    class Depth:
        def get_width(self):return 4
        def get_height(self):return 2
        def get_timestamp_us(self):return 123455500
        def get_format(self):return 'Y16'
        def get_stream_profile(self):return Profile('Y16')
        def get_data(self):return np.full((2,4),1500,np.uint16)
        def get_depth_scale(self):return 1.
    class Frames:
        def get_color_frame(self):return Color()
        def get_depth_frame(self):return Depth()
    class Pipeline:
        def __init__(self,device):self.device=device;self.started=False;self.synced=False
        def get_stream_profile_list(self,sensor):return Options('MJPG' if sensor=='color' else 'Y16')
        def get_d2c_depth_profile_list(self,color,mode):return Options('Y16')
        def enable_frame_sync(self):self.synced=True
        def start(self,config):self.config=config;self.started=True
        def wait_for_frames(self,timeout):return Frames()
        def stop(self):self.started=False
    sdk=types.SimpleNamespace(Context=Context,Pipeline=Pipeline,Config=Config,
        OBFormat=types.SimpleNamespace(RGB='RGB',BGR='BGR',MJPG='MJPG',YUYV='YUYV',Y16='Y16'),
        OBSensorType=types.SimpleNamespace(COLOR_SENSOR='color',DEPTH_SENSOR='depth'),OBAlignMode=types.SimpleNamespace(HW_MODE='hw'))
    monkeypatch.setitem(sys.modules,'pyorbbecsdk',sdk)
    camera=Gemini335LDriver(CameraCalibration(np.eye(3),np.eye(4),measured=True),width=4,height=2,fps=30,calibration_image_size=(4,2))
    assert camera.pipeline.config.streams[0].fmt=='MJPG' and camera.pipeline.synced
    timestamp,rgb,depth,_=camera.read();assert rgb.shape==(2,4,3)
    np.testing.assert_allclose(depth,1.5)
    assert camera.last_clock_metadata['metadata']['camera_device_us']==123456000
    assert camera.last_clock_metadata['metadata']['color_depth_delta_seconds']==.0005
    camera.close();assert camera.pipeline is None


def test_moving_lidar_retains_each_ray_origin_and_does_not_clear_unseen_cells(tmp_path):
    from dataclasses import replace
    points=np.array([[2.,0,.5,1,1,1,0],[1.9,0,.5,1,1,1,.1]])
    imu=tuple(IMUSample(t,[0,0,0],[0,0,9.80665]) for t in np.linspace(0,.1,11))
    raw=MultiModalSensorFrame(0,0,points,imu)
    deskewer=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0]);frame=deskewer.deskew_frame(raw)
    np.testing.assert_allclose(frame.lidar_origins,[[-.1,0,0],[0,0,0]],atol=1e-9)
    np.testing.assert_allclose(transform_points(frame.sensor_pose_world,frame.lidar_origins),[[0,0,0],[.1,0,0]],atol=1e-9)
    assert not frame.lidar_origins.flags.writeable
    with FrameRecorder(tmp_path/'origins') as writer:writer.write(frame)
    replay=next(iter(ReplaySource(tmp_path/'origins')))
    np.testing.assert_array_equal(replay.lidar_origins,frame.lidar_origins)
    # The end-pose ray would cross this cell, but the actual acquisition ray
    # passes above it. Unknown space must not become free under ego motion.
    hit=np.array([[1.,1.,.5,1,1,1,0.]])
    correct=SparseMultiReturnVoxelMap(.25);correct.voxelize_and_update(hit,origin=[[-1,0,.5]])
    incorrect=SparseMultiReturnVoxelMap(.25);incorrect.voxelize_and_update(hit,origin=[0,0,.5])
    probe=np.array([[.625,.625,.625]])
    assert not correct.is_known(probe)[0] and incorrect.query(probe)[0]<0
    with pytest.raises(ValueError):replace(raw,lidar_origins=np.zeros((1,3)))


def test_per_beam_origins_preserve_multi_return_bounds_and_reject_inconsistent_echoes():
    from slam_and_twin.log_odds_octomap import traverse_beams
    starts=np.array([[-.1,.1,.2],[1.2,-.4,.7],[0.,0.,0.]])
    ends=np.array([[1.,2.,.5],[-.5,.9,1.3],[.7,-.4,1.]])
    expected=set().union(*(set(traverse_voxels(o,t,.25)) for o,t in zip(starts,ends)))
    assert set(map(tuple,traverse_beams(starts,ends,.25)))==expected
    p=np.array([[1.5,.5,.5,.4,1,2,0],[2.5,.5,.5,.7,2,2,0],[1.5,2.5,.5,.7,1,1,.05]])
    origins=np.array([[.5,.5,.5],[.5,.5,.5],[1.5,1.5,.5]])
    m=SparseMultiReturnVoxelMap(1.);m.voxelize_and_update(p,origin=origins,beam_ids=[0,0,1],return_metadata_valid=True)
    assert not m.is_known([[3.5,.5,.5]])[0]
    assert m.query([[1.5,.5,.5]])[0]>0 and m.query([[2.5,.5,.5]])[0]>0
    origins[1,0]+=.01
    with pytest.raises(ValueError,match='one beam'):
        m.voxelize_and_update(p,origin=origins,beam_ids=[0,0,1],return_metadata_valid=True)


def test_camera_and_s2_captures_use_imu_pose_without_replacing_calibration(tmp_path):
    from dataclasses import replace
    from ingestion.sensor_synchronizer import project_points,fuse_frame
    T=np.eye(4);T[0,3]=.3;c=CameraCalibration(np.array([[2,0,1],[0,2,1],[0,0,1.]]),T)
    imu=tuple(IMUSample(t,[0,0,0],[0,0,9.80665]) for t in np.linspace(0,.1,11))
    raw=MultiModalSensorFrame(0,0,np.array([[.4,0,2,1,1,1,.1]]),imu,
        rgb=np.full((3,3,3),128,np.uint8),depth=np.full((3,3),2,np.float32),calibration=c,camera_timestamp=.08,
        gemini_rgbd_cloud=np.array([[.3,0,2,.5,.5,.5]]),painted_features=np.ones((1,3)),
        rplidar_s2_points=np.array([[1.2,0,.5]]),rplidar_origin_lidar=[.2,0,0],rplidar_timestamp=.05)
    d=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0]);frame=d.deskew_frame(raw)
    assert frame.calibration.calibration_id==c.calibration_id
    np.testing.assert_array_equal(frame.calibration.T_lidar_camera,T)
    np.testing.assert_allclose(frame.camera_pose_in_lidar[:3,3],[.28,0,0],atol=1e-9)
    np.testing.assert_allclose(frame.gemini_rgbd_cloud[0,:3],[.28,0,2],atol=2e-8)
    np.testing.assert_allclose(frame.rplidar_origin_lidar,[.15,0,0],atol=1e-9)
    np.testing.assert_allclose(frame.rplidar_s2_points[0],[1.15,0,.5],atol=1e-7)
    assert frame.painted_features is None
    uv,_=project_points([[.28,0,2]],c,frame.camera_pose_in_lidar)
    np.testing.assert_allclose(uv,[[1,1]],atol=1e-9)
    dense=fuse_frame(frame,stride=1).gemini_rgbd_cloud
    assert np.any(np.all(np.isclose(dense[:,:3],[.28,0,2],atol=1e-7),axis=1))
    with FrameRecorder(tmp_path/'camera') as writer:writer.write(frame)
    replay=next(iter(ReplaySource(tmp_path/'camera')))
    np.testing.assert_array_equal(replay.camera_pose_in_lidar,frame.camera_pose_in_lidar)
    assert not replay.camera_pose_in_lidar.flags.writeable
    with pytest.raises(ValueError,match='outside'):
        ContinuousTimeIMUDeskewer().deskew_frame(replace(raw,camera_timestamp=.12))


def test_future_camera_pose_requires_observed_imu_and_keeps_end_state_at_lidar_time():
    c=CameraCalibration(np.eye(3),np.eye(4))
    imu=tuple(IMUSample(t,[0,0,0],[0,0,9.80665]) for t in np.linspace(0,.12,13))
    raw=MultiModalSensorFrame(0,0,np.empty((0,7)),imu,calibration=c,camera_timestamp=.12)
    d=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0]);out=d.deskew_frame(raw)
    np.testing.assert_allclose(out.camera_pose_in_lidar[:3,3],[.02,0,0],atol=1e-9)
    np.testing.assert_allclose(d.pose_world[:3,3],[.1,0,0],atol=1e-9)
    assert d.last_timestamp==pytest.approx(.1)
    imu2=tuple(IMUSample(t,[0,0,0],[0,0,9.80665]) for t in np.linspace(.1,.2,11))
    d.deskew_frame(MultiModalSensorFrame(1,.1,np.empty((0,7)),imu2))
    np.testing.assert_allclose(d.pose_world[:3,3],[.2,0,0],atol=1e-9)
    with pytest.raises(ValueError,match='outside'):d.pose_at(.3)


def test_synchronizer_holds_future_camera_until_real_imu_arrives():
    imu=(IMUSample(0,[0,0,0],[0,0,9.80665]),IMUSample(.1,[0,0,0],[0,0,9.80665]))
    sync=SensorSynchronizer();sync.push_lidar(MultiModalSensorFrame(0,0,np.empty((0,7)),imu))
    sync.push_camera(.115,np.zeros((2,2,3),np.uint8),np.ones((2,2)),CameraCalibration(np.eye(3),np.eye(4)))
    assert sync.synchronize() is None
    sync.push_imu(IMUSample(.12,[0,0,0],[0,0,9.80665]))
    result=sync.synchronize();assert result is not None and result.camera_timestamp==.115
    assert result.imu_samples[-1].timestamp==.12


def test_empty_lidar_camera_fusion_and_all_invalid_depth_are_valid():
    from ingestion.sensor_synchronizer import fuse_frame,project_points
    c=CameraCalibration(np.eye(3),np.eye(4))
    frame=MultiModalSensorFrame(0,0,np.empty((0,7)),rgb=np.zeros((3,4,3),np.uint8),depth=np.zeros((3,4)),calibration=c)
    uv,z=project_points(frame.lidar_points,c)
    assert uv.shape==(0,2) and z.shape==(0,)
    fused=fuse_frame(frame)
    assert fused.painted_features.shape==(0,3) and fused.gemini_rgbd_cloud.shape==(0,6)
    assert fused.diagnostics['painted_fraction']==0


def test_auxiliary_capture_uses_bounded_reanchored_trajectory_history():
    from dataclasses import replace
    imu=lambda a,b:tuple(IMUSample(t,[0,0,0],[0,0,9.80665]) for t in np.linspace(a,b,11))
    d=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0])
    d.deskew_frame(MultiModalSensorFrame(0,0,np.empty((0,7)),imu(0,.1)))
    corrected=np.eye(4);corrected[0,3]=5.1;d.correct_state(corrected)
    frame=MultiModalSensorFrame(1,.1,np.empty((0,7)),imu(.1,.2),rplidar_s2_points=np.array([[1,0,.5]]),rplidar_timestamp=.05)
    out=d.deskew_frame(frame)
    np.testing.assert_allclose(out.rplidar_s2_points,[[.85,0,.5]],atol=1e-7)
    np.testing.assert_allclose(d.pose_world[:3,3],[5.2,0,0],atol=1e-9)
    for i in range(2,8):d.deskew_frame(MultiModalSensorFrame(i,.1*i,np.empty((0,7)),imu(.1*i,.1*(i+1))))
    assert len(d._trajectories)==4
    with pytest.raises(ValueError,match='outside'):d.pose_at(.05)


def test_live_source_buffers_measured_next_sweep_for_future_camera():
    import threading,types
    from ingestion.live_source import LiveSensorSource
    def frame(i):
        imu=tuple(IMUSample(t,[0,0,0],[0,0,9.80665]) for t in np.linspace(i*.1,(i+1)*.1,11))
        return MultiModalSensorFrame(i,i*.1,np.empty((0,7)),imu)
    source=LiveSensorSource.__new__(LiveSensorSource)
    frames=iter([frame(0),frame(1)]);source.lidar=types.SimpleNamespace(next_frame=lambda:next(frames))
    source.sync=SensorSynchronizer();source.lock=threading.Lock();source.errors=[];source.initial_pose=np.eye(4)
    source.imu_transform=None;source.s2=None
    source.sync.push_camera(.115,np.zeros((2,2,3),np.uint8),np.ones((2,2)),CameraCalibration(np.eye(3),np.eye(4)))
    result=next(source)
    assert result.frame_id==0 and result.camera_timestamp==.115
    assert result.imu_samples[-1].timestamp>=.115 and source.sync.lidar[0].frame_id==1


def test_rotating_camera_capture_pose_uses_full_se3_interpolation():
    from core.se3_math import inverse
    w=.8;capture=.08;end=.1
    c=CameraCalibration(np.eye(3),np.array([[1.,0,0,.2],[0,1,0,.03],[0,0,1,0],[0,0,0,1]]))
    imu=tuple(IMUSample(t,[0,0,w],[0,0,9.80665]) for t in np.linspace(0,end,21))
    frame=MultiModalSensorFrame(0,0,np.empty((0,7)),imu,calibration=c,camera_timestamp=capture)
    d=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0]);out=d.deskew_frame(frame)
    T_end=np.eye(4);T_end[:3,:3]=so3_exp([0,0,w*end]);T_end[:3,3]=[end,0,0]
    T_capture=np.eye(4);T_capture[:3,:3]=so3_exp([0,0,w*capture]);T_capture[:3,3]=[capture,0,0]
    np.testing.assert_allclose(out.camera_pose_in_lidar,inverse(T_end)@T_capture@c.T_lidar_camera,atol=1e-9)


def test_deskewed_recording_is_explicitly_marked_and_cannot_be_deskewed_twice(tmp_path):
    imu=tuple(IMUSample(t,[0,0,0],[0,0,9.80665]) for t in [0,.1])
    raw=MultiModalSensorFrame(0,0,np.zeros((1,7)),imu,lidar_origins=np.array([[.2,0,0]]))
    out=ContinuousTimeIMUDeskewer().deskew_frame(raw)
    assert out.diagnostics['coordinate_reference']=='lidar_sweep_end'
    with FrameRecorder(tmp_path/'deskewed') as writer:writer.write(out)
    replay=next(iter(ReplaySource(tmp_path/'deskewed')))
    with pytest.raises(ValueError,match='already been deskewed'):ContinuousTimeIMUDeskewer().deskew_frame(replay)


def test_localization_rotation_roundoff_is_projected_and_reflections_rejected():
    pose=np.eye(4);pose[0,0]+=1e-8
    d=ContinuousTimeIMUDeskewer(initial_pose=pose)
    np.testing.assert_allclose(d.pose_world[:3,:3].T@d.pose_world[:3,:3],np.eye(3),atol=1e-14)
    d.correct_state(pose);np.testing.assert_allclose(d.pose_world[:3,:3].T@d.pose_world[:3,:3],np.eye(3),atol=1e-14)
    pose[0,0]=-1
    with pytest.raises(ValueError,match='SO'):d.correct_state(pose)


def test_dropped_sweep_with_long_covered_imu_gap_preserves_constant_motion():
    points=np.array([[2,0,.5,1,1,1,0],[1.9,0,.5,1,1,1,.1]],float)
    imu=lambda a,b:tuple(IMUSample(t,[0,0,0],[0,0,9.80665]) for t in np.linspace(a,b,31))
    d=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0])
    d.deskew_sweep(points,imu(0,.1),0,.1)
    d.deskew_sweep(points,imu(.1,.4),.3,.1)
    np.testing.assert_allclose(d.pose_world[:3,3],[.4,0,0],atol=1e-9)
    np.testing.assert_allclose(d.last_point_origins_world,[[.3,0,0],[.4,0,0]],atol=1e-9)
    assert d.last_diagnostics['inter_sweep_integration_seconds']==pytest.approx(.2)
    bad=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0]);bad.deskew_sweep(points,imu(0,.1),0,.1)
    with pytest.raises(ValueError,match='between LiDAR sweeps'):bad.deskew_sweep(points,imu(.3,.4),.3,.1)


def test_synchronizer_retains_continuous_imu_across_dropped_lidar_sweeps():
    sync=SensorSynchronizer();calib=CameraCalibration(np.eye(3),np.eye(4))
    def camera(t):sync.push_camera(t,np.zeros((2,2,3),np.uint8),np.ones((2,2)),calib)
    for t in np.linspace(0,.1,11):sync.push_imu(IMUSample(t,[0,0,0],[0,0,9.80665]))
    sync.push_lidar(MultiModalSensorFrame(0,0,np.empty((0,7))));camera(.1)
    first=sync.synchronize();assert first is not None
    for t in np.linspace(.11,.4,30):sync.push_imu(IMUSample(t,[0,0,0],[0,0,9.80665]))
    sync.push_lidar(MultiModalSensorFrame(3,.3,np.empty((0,7))));camera(.4)
    second=sync.synchronize();assert second is not None and second.imu_samples[0].timestamp<=.1
    d=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0]);d.deskew_frame(first);d.deskew_frame(second)
    np.testing.assert_allclose(d.pose_world[:3,3],[.4,0,0],atol=1e-9)

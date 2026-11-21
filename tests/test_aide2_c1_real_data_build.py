"""AIDE2 C1-C6: what running the real L2 bench recordings through build-twin exposed (2026-11-05).

Each test pins a behaviour that the previous code lacked; they count work instead of timing it.
"""
from pathlib import Path
import numpy as np
import pytest
import yaml
from scipy.optimize import least_squares
from core.contracts import CameraCalibration,IMUSample,MultiModalSensorFrame,SensorCapabilities
from core.se3_math import se3_exp,se3_log,inverse,so3_exp

ROOT=Path(__file__).resolve().parents[1]


def test_c2_batched_se3_matches_the_scalar_functions_including_near_zero_and_near_pi():
    from core.se3_math import se3_exp_batch,se3_log_batch,inverse_batch
    rng=np.random.default_rng(3);xi=rng.normal(size=(200,6));xi[:4,:3]*=1e-7
    xi[4:8,:3]=xi[4:8,:3]/np.linalg.norm(xi[4:8,:3],axis=1)[:,None]*3.1
    T=se3_exp_batch(xi)
    np.testing.assert_allclose(T,[se3_exp(x) for x in xi],atol=1e-12)
    np.testing.assert_allclose(se3_log_batch(T),[se3_log(t) for t in T],atol=1e-10)
    np.testing.assert_allclose(inverse_batch(T),[inverse(t) for t in T],atol=1e-12)
    assert se3_log_batch(np.zeros((0,4,4))).shape==(0,6)


def room(seed=5):
    rng=np.random.default_rng(seed)
    planes=[rng.uniform([-3,-3,0],[3,3,0],(500,3)),rng.uniform([-3,-3,0],[-3,3,2.5],(400,3)),rng.uniform([-3,3,0],[3,3,2.5],(400,3)),
            rng.uniform([3,-3,0],[3,3,2.5],(400,3)),rng.uniform([.5,-.5,0],[.5,.5,1],(150,3)),rng.uniform([.5,-.5,1],[1.5,.5,1],(150,3))]
    return np.concatenate(planes)


def test_c3_keyframe_geometry_is_prepared_once_and_loop_closure_is_capped(monkeypatch):
    import slam_and_twin.icp_pose_graph as graph_module
    calls={'prepare':0,'icp':0};prepare=graph_module.prepare_reference;icp=graph_module.point_to_plane_icp
    def counted_prepare(*a,**k):calls['prepare']+=1;return prepare(*a,**k)
    def counted_icp(*a,**k):calls['icp']+=1;return icp(*a,**k)
    monkeypatch.setattr(graph_module,'prepare_reference',counted_prepare);monkeypatch.setattr(graph_module,'point_to_plane_icp',counted_icp)
    world=room();graph=graph_module.ICPPoseGraph(voxel_size=.15,max_keyframes=32);rng=np.random.default_rng(0);per_scan=[]
    for i in range(30):
        before=calls['icp'];graph.add_scan(world+rng.normal(0,.003,world.shape));per_scan.append(calls['icp']-before)
    # Parked: every older keyframe is a loop candidate. One odometry ICP plus at most three loop ICPs per scan.
    assert max(per_scan)<=4 and per_scan[-1]==4
    # One preparation per scan: each cloud is prepared as a source once and then reused as a target.
    assert calls['prepare']==30
    assert np.linalg.norm(graph.poses[-1][:3,3])<.02


def test_c2_batched_pose_graph_residual_reaches_the_same_optimum_as_the_scalar_one():
    from slam_and_twin.icp_pose_graph import ICPPoseGraph
    rng=np.random.default_rng(1);graph=ICPPoseGraph(max_keyframes=None)
    truth=[np.eye(4)]
    for _ in range(7):truth.append(truth[-1]@se3_exp(np.r_[rng.normal(0,.05,3),rng.normal(0,.3,3)]))
    graph.poses=[t@se3_exp(rng.normal(0,.02,6)) if i else t.copy() for i,t in enumerate(truth)]
    info=np.diag([400,400,400,100,100,100.])
    graph.edges=[(a,b,inverse(truth[a])@truth[b]@se3_exp(rng.normal(0,.002,6)),info) for a,b in [(i,i+1) for i in range(7)]+[(0,4),(2,7),(1,6)]]
    initial=[p.copy() for p in graph.poses];optimized=graph.optimize()
    # The pre-batching residual, one scalar log per edge, solved the same way.
    from core.se3_math import adjoint
    def root(info,T):
        A=adjoint(T);v,V=np.linalg.eigh(A.T@info@A);return (V*np.sqrt(np.maximum(v,1e-6)))@V.T
    roots=[root(i,T) for _,_,T,i in graph.edges]
    def unpack(x):return [initial[0]]+[initial[i]@se3_exp(x[6*(i-1):6*i]) for i in range(1,len(initial))]
    def residual(x):
        P=unpack(x);return np.concatenate([r@se3_log(inverse(T)@inverse(P[a])@P[b]) for (a,b,T,_),r in zip(graph.edges,roots)])
    reference=unpack(least_squares(residual,np.zeros(6*(len(initial)-1)),loss='huber',f_scale=.1,max_nfev=100).x)
    for got,want in zip(optimized,reference):np.testing.assert_allclose(got,want,atol=1e-6)


def test_c4_point_cloud_tiles_are_visited_once_per_tile_per_add(tmp_path,monkeypatch):
    from slam_and_twin.tiled_reconstruction import DiskPointCloud
    from slam_and_twin.tiled_map import DiskTileStore
    visits={'n':0};page=DiskTileStore._page
    def counted(self,tile):visits['n']+=1;return page(self,tile)
    monkeypatch.setattr(DiskTileStore,'_page',counted)
    cloud=DiskPointCloud(tmp_path/'pts',.1,tile_size=4,max_resident_tiles=2)
    points=np.random.default_rng(2).uniform(0,.8,(3000,3))  # 8x8x8 voxels = 2x2x2 tiles
    cloud.add(points);cloud.add(points)
    assert visits['n']<=16
    values=np.concatenate([v for _,_,v in cloud.cells.iter_tiles()])
    assert values[:,3].sum()==6000 and np.isclose(values[:,:3].sum(),2*points.sum())


def moving_frames(count=6,depth=True):
    rng=np.random.default_rng(27)
    floor=rng.uniform([-2,-2,0],[2,2,0],(220,3));wall=rng.uniform([-2,-2,0],[-2,2,2.5],(180,3));rear=rng.uniform([-2,2,0],[2,2,2.5],(180,3))
    world=np.r_[floor,wall,rear];offsets=np.linspace(0,.1,len(world));frames=[]
    for index in range(count):
        start=index*.1;xyz=np.array([(p-[0,0,1.4])@so3_exp([0,0,.6*t]) for p,t in zip(world,start+offsets)])
        pose=np.eye(4);pose[2,3]=1.4
        imu=tuple(IMUSample(t,[0,0,.6],[0,0,9.80665]) for t in np.linspace(start-.002,start+.1,22))
        frames.append(MultiModalSensorFrame(index,start,np.c_[xyz,np.ones((len(world),3)),offsets],imu,sensor_pose_world=pose,
            rgb=np.zeros((12,16,3),np.uint8) if depth else None,depth=np.full((12,16),2.,np.float32) if depth else None,
            camera_timestamp=start+.1 if depth else None,capabilities=SensorCapabilities('unitree-l2'),diagnostics={'synthetic':True,'pose_prior_valid':False}))
    return frames


def build_config():
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text())
    config['mapping'].update(commissioning_mode='mobile',twin_voxel_size=.14,
        commissioning_budgets={'frame_buffer_bytes':128000,'max_resident_tiles':8,'max_resident_bytes':1000000,'tile_size':4,'tsdf_tile_resolution':16})
    config['planning']['max_local_grid_cells']=10000;return config


def test_c5_an_uncalibrated_bring_up_recording_builds_a_lidar_only_twin_and_reports_progress(tmp_path):
    from core.pipeline import build_commissioned_assets
    messages=[];twin,mapping,grid=build_commissioned_assets(moving_frames(),tmp_path/'twin',build_config(),progress=messages.append)
    try:
        storage=twin.report['navigation_storage']
        assert storage['camera_depth_used'] is False and storage['depth_frames_skipped_without_calibration']==6
        assert any(m.startswith('Prepared ') and 'sweeps/s' in m for m in messages)
    finally:twin.close();mapping.close()


def test_c6_merged_sweeps_and_auto_bias_are_recorded_in_the_build_report(tmp_path):
    from core.pipeline import build_commissioned_assets
    config=build_config();config['mapping']['commissioning_sweeps_per_scan']=2;config['live']['imu_accel_bias']='auto'
    frames=moving_frames(8,depth=False);messages=[]
    twin,mapping,grid=build_commissioned_assets(frames,tmp_path/'twin',config,progress=messages.append)
    try:
        report=twin.report['commissioning']
        assert report['sweeps_per_scan']==2 and len(twin.poses)==4
        # The synthetic cart turns from the first sample, so auto refuses to estimate rather than guess.
        assert report['imu_static_init']['applied'] is False and report['imu_accel_bias'] is None
        assert any('IMU bias (auto): not applied' in m for m in messages)
    finally:twin.close();mapping.close()


def test_c8_the_build_reports_its_trajectory_and_flags_jumps_no_pushed_cart_makes():
    from core.pipeline import trajectory_summary
    still=[np.eye(4) for _ in range(5)];summary=trajectory_summary(still)
    assert summary['scans']==5 and summary['path_m']==0 and summary['suspect_jumps']==0
    poses=[];jump=np.eye(4)
    for i in range(6):
        pose=np.eye(4);pose[0,3]=.2*i+(3. if i>=4 else 0.);poses.append(pose)
    summary=trajectory_summary(poses)
    assert summary['max_step_m']==pytest.approx(3.2) and summary['suspect_jumps']==1 and summary['start_to_end_m']==pytest.approx(4.)
    assert 'WARNING' in summary['message'] and 'registration' in summary['message']


def test_c8_build_progress_includes_the_trajectory_line(tmp_path):
    from core.pipeline import build_commissioned_assets
    messages=[];twin,mapping,grid=build_commissioned_assets(moving_frames(4,depth=False),tmp_path/'twin',build_config(),progress=messages.append)
    try:
        assert any(m.startswith('Trajectory: 4 scans') for m in messages)
        assert twin.report['commissioning']['trajectory']['scans']==4
    finally:twin.close();mapping.close()

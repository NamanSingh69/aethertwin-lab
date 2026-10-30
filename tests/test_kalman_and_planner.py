"""Complete observation -> neural model -> tracking/navigation regressions.

These tests use the packaged learned checkpoint, commissioned observations, and
the real CPU-ray/physics simulator. They do not replace inference, localization,
mapping, tracking, or planning with mocks. Ground truth is used only to score the
mobile pose estimate; it is never passed into LabPipeline.
"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import numpy as np
import pytest
import torch
import yaml
from core.pipeline import LabPipeline,load_commissioned_assets
from core.se3_math import so3_log,inverse,transform_points
from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
from ingestion.recorder_replayer import FrameRecorder,ReplaySource
from tracking_and_navigation.geometry import obb_from_corners


ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def runtime_assets():
    config=yaml.safe_load((ROOT/"config/system_config.yaml").read_text(encoding="utf-8"))
    config["device"]="cpu"
    checkpoint=ROOT/config["perception"]["checkpoint"]
    twin_path=ROOT/f"assets/twin_seed{config['seed']}"
    assert checkpoint.is_file(),"The complete delivery must include its learned synthetic checkpoint"
    assert (twin_path/"planning_grid.npz").is_file(),"The default measured twin must be packaged"
    assets=load_commissioned_assets(twin_path)
    previous_threads=torch.get_num_threads()
    torch.set_num_threads(min(2,previous_threads))
    yield config,checkpoint,assets
    torch.set_num_threads(previous_threads)


def assert_state_equivalent(left,right):
    assert left.frame_id==right.frame_id and left.timestamp==right.timestamp
    assert left.trajectory_status==right.trajectory_status
    for field in ("sensor_pose_world","voxel_centers","voxel_colors","voxel_flow_vectors",
                  "floor_risk_values","traffic_heatmap_2d"):
        np.testing.assert_allclose(getattr(left,field),getattr(right,field),rtol=1e-5,atol=2e-5)
    assert (left.planned_safe_path is None)==(right.planned_safe_path is None)
    if left.planned_safe_path is not None:
        np.testing.assert_allclose(left.planned_safe_path,right.planned_safe_path,rtol=1e-5,atol=2e-5)
    assert len(left.entities)==len(right.entities)
    for a,b in zip(left.entities,right.entities):
        assert a.track_id==b.track_id and a.semantic_class==b.semantic_class
        assert a.is_breaching_geofence==b.is_breaching_geofence
        for field in ("position","velocity","acceleration","class_probs","covariance","obb_corners"):
            np.testing.assert_allclose(getattr(a,field),getattr(b,field),rtol=1e-5,atol=2e-5)
        if np.isfinite(a.ttc_seconds) or np.isfinite(b.ttc_seconds):
            assert a.ttc_seconds==pytest.approx(b.ttc_seconds,rel=1e-5,abs=2e-5)
    assert [(g.name,g.breaching_track_ids) for g in left.geofences]==[(g.name,g.breaching_track_ids) for g in right.geofences]
    assert left.active_alerts==right.active_alerts
    for field in ("robot_position","robot_velocity"):
        np.testing.assert_allclose(left.diagnostics[field],right.diagnostics[field],rtol=1e-5,atol=2e-5)


def assert_navigation_snapshot_safe(pipeline,state):
    robot_position=np.asarray(state.diagnostics["robot_position"])
    robot_velocity=np.asarray(state.diagnostics["robot_velocity"])
    for entity in state.entities:
        radius=float(np.linalg.norm(obb_from_corners(entity.obb_corners).half_extents))
        expected=pipeline.risk.ttc(entity.position,entity.velocity,robot_position,robot_velocity,
                                   pipeline.risk.safety_radius+radius)
        if np.isfinite(expected): assert entity.ttc_seconds==pytest.approx(expected,rel=1e-6,abs=1e-6)
        else: assert np.isinf(entity.ttc_seconds)
    if state.trajectory_status=="ok":
        points,times=pipeline._remaining_trajectory()
        assert state.planned_safe_path is not None
        np.testing.assert_allclose(points,state.planned_safe_path)
        assert pipeline.planner.path_clear(points,times,state.entities),"Published remaining route must clear the current snapshot"
    else:
        assert state.planned_safe_path is None
        np.testing.assert_allclose(robot_velocity,0.)


def test_complete_record_replay_is_deterministic(runtime_assets,tmp_path):
    config,checkpoint,(twin,static,grid)=runtime_assets
    source=SyntheticLabSimulator(config["seed"],config,mobile=False)
    try:
        frames=[source.next_frame() for _ in range(3)]
    finally:
        source.close()
    recording=tmp_path/"raw_observations"
    with FrameRecorder(recording,chunk_size=2) as recorder:
        for frame in frames: recorder.write(frame)
    manifest=json.loads((recording/"manifest.json").read_text())
    assert manifest["frame_count"]==3 and len(manifest["chunks"])==2
    replay=ReplaySource(recording)
    restored=list(replay); replay.close()
    for original,reconstructed in zip(frames,restored):
        for field in ("lidar_points","rgb","depth","beam_ids","rplidar_s2_points"):
            np.testing.assert_array_equal(getattr(original,field),getattr(reconstructed,field))
        assert not hasattr(reconstructed,"semantic") and not hasattr(reconstructed,"instance_ids")
    original_pipeline=LabPipeline(copy.deepcopy(config),checkpoint,twin,static,grid)
    replay_pipeline=LabPipeline(copy.deepcopy(config),checkpoint,twin,static,grid)
    original_states=[]; replay_states=[]
    for frame in frames:
        state=original_pipeline.process(frame); assert_navigation_snapshot_safe(original_pipeline,state)
        original_states.append(state)
    for frame in restored:
        state=replay_pipeline.process(frame); assert_navigation_snapshot_safe(replay_pipeline,state)
        replay_states.append(state)
    for original,reconstructed in zip(original_states,replay_states):
        assert_state_equivalent(original,reconstructed)
        assert original.timestamp==pytest.approx(frames[original.frame_id].timestamp+frames[original.frame_id].sweep_duration)
    assert len(original_states[-1].voxel_centers)>0
    assert len(original_pipeline.occupancy.cells)>0
    assert original_pipeline.occupancy.cells.keys()==replay_pipeline.occupancy.cells.keys()
    for key in original_pipeline.occupancy.cells:
        np.testing.assert_allclose(original_pipeline.occupancy.cells[key],replay_pipeline.occupancy.cells[key],atol=1e-7,rtol=1e-7)
    # Immutable recorded snapshots cannot corrupt subsequent processing or export.
    with pytest.raises(TypeError): original_states[0].diagnostics["robot_position"]=(999,999,999)
    assert not original_states[0].voxel_centers.flags.writeable
    if original_states[0].trajectory_status!="ok":
        assert original_states[0].planned_safe_path is None
    report={"checkpoint_sha256":hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "frames":len(frames),"npz_chunks":len(manifest["chunks"]),
        "voxel_counts":[len(s.voxel_centers) for s in original_states],
        "track_counts":[len(s.entities) for s in original_states],
        "route_statuses":[s.trajectory_status for s in original_states],
        "occupancy_cells":len(original_pipeline.occupancy.cells),
        "max_flow_replay_error":float(max(np.max(np.abs(a.voxel_flow_vectors-b.voxel_flow_vectors)) for a,b in zip(original_states,replay_states))),
        "aligned_timestamps":[s.timestamp for s in original_states]}
    (tmp_path/"record_replay_regression.json").write_text(json.dumps(report,indent=2),encoding="utf-8")


def test_mobile_pipeline_processes_real_moving_rig_observations(runtime_assets,tmp_path):
    config,checkpoint,(twin,static,grid)=runtime_assets
    pipeline=LabPipeline(copy.deepcopy(config),checkpoint,twin,static,grid,mobile=True)
    source=SyntheticLabSimulator(config["seed"],config,mobile=True)
    estimates=[];truths=[];frames=[]
    try:
        for _ in range(12):
            frame=source.next_frame(); frames.append(frame)
            # Kept only in this test; runtime process takes the sensor frame alone.
            expected=twin.floor_transform@source.last_ground_truth.pose_world
            state=pipeline.process(frame)
            assert_navigation_snapshot_safe(pipeline,state)
            estimates.append(state.sensor_pose_world.copy()); truths.append(expected)
            assert np.isfinite(state.sensor_pose_world).all()
            assert state.diagnostics["pointwise"] is True
            assert state.timestamp==pytest.approx(frame.timestamp+frame.sweep_duration)
            if state.trajectory_status!="ok":
                assert state.planned_safe_path is None
                np.testing.assert_allclose(state.diagnostics["robot_velocity"],0.)
    finally:
        source.close()
    assert pipeline.odometry.total_scans==12 and len(pipeline.odometry.clouds)==12
    stages=pipeline.report()["module_latency_ms"]
    assert {"localization","occupancy","perception_tracking","planning","risk_fields"}<=set(stages)
    assert all(0<=v["p50"]<=v["p95"] for v in stages.values())
    assert pipeline.odometry.last_diagnostics["fitness"]>.15
    assert pipeline.odometry.last_diagnostics["map_anchored"] is True
    assert pipeline.odometry.last_diagnostics["map_information_rank"]==6
    assert np.linalg.norm(estimates[-1][:3,3]-estimates[0][:3,3])>.001
    translation_errors=[np.linalg.norm(a[:3,3]-b[:3,3]) for a,b in zip(estimates,truths)]
    rotation_errors=[np.linalg.norm(so3_log(a[:3,:3].T@b[:3,:3])) for a,b in zip(estimates,truths)]
    assert max(translation_errors)<.03,translation_errors
    assert max(rotation_errors)<.01,rotation_errors
    assert pipeline.frames==12 and len(pipeline.occupancy.cells)>0
    report={"frames":pipeline.frames,"translation_errors_m":translation_errors,
        "rotation_errors_rad":rotation_errors,"last_icp_fitness":float(pipeline.odometry.last_diagnostics["fitness"]),
        "estimated_displacement_m":float(np.linalg.norm(estimates[-1][:3,3]-estimates[0][:3,3])),
        "retained_keyframes":len(pipeline.odometry.clouds),"occupancy_cells":len(pipeline.occupancy.cells),
        "checkpoint_sha256":hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
    (tmp_path/"mobile_pipeline_smoke.json").write_text(json.dumps(report,indent=2),encoding="utf-8")


def test_sparse_widely_spaced_plane_has_supported_normals():
    from slam_and_twin.icp_pose_graph import covariance_normals,prepare_reference
    a,b=np.meshgrid(np.arange(-5.,6.),np.arange(-5.,6.))
    points=np.column_stack([np.zeros(a.size),a.ravel(),b.ravel()])
    # Every neighbor is at least one metre away, so a fixed .36m search would
    # leave each point without a plane. The correct normal is the X axis.
    normals,covariance=covariance_normals(points)
    np.testing.assert_allclose(np.abs(normals[:,0]),1.,atol=1e-10)
    assert np.all(np.linalg.eigvalsh(covariance)[:,1]>0)
    reference=prepare_reference(points,voxel=.12)
    assert len(reference.points)==len(points)


def test_volumetric_patch_does_not_supply_arbitrary_plane_constraints():
    from itertools import product
    from slam_and_twin.icp_pose_graph import prepare_reference
    points=np.array(list(product((-1.,1.),repeat=3)))
    with pytest.raises(ValueError,match="supported planar"):
        prepare_reference(points,voxel=0)


def test_real_mobile_map_anchors_survive_graph_trimming_and_optimization(runtime_assets):
    from slam_and_twin.icp_pose_graph import ICPPoseGraph
    from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
    config,_,(twin,_,_)=runtime_assets
    reference=transform_points(inverse(twin.floor_transform),twin.points)
    graph=ICPPoseGraph(max_keyframes=4,reference_points=reference)
    source=SyntheticLabSimulator(config["seed"],config,mobile=True)
    deskew=None;previous=None;truth=None
    try:
        for _ in range(8):
            raw=source.next_frame()
            if deskew is None:deskew=ContinuousTimeIMUDeskewer(initial_pose=raw.sensor_pose_world)
            aligned=deskew.deskew_frame(raw)
            estimated=graph.add_scan(aligned.lidar_points[:,:3],aligned.sensor_pose_world)
            velocity=(estimated[:3,3]-previous[:3,3])/raw.sweep_duration if previous is not None else deskew.velocity
            deskew.correct_state(estimated,velocity);previous=estimated.copy()
            truth=source.last_ground_truth.pose_world
    finally:source.close()
    assert graph.total_scans==8 and graph.dropped_keyframes==4
    assert len(graph.clouds)==len(graph.poses)==len(graph.anchors)==4
    assert all(0<=index<4 for index,_,_ in graph.anchors)
    anchor_before=graph.poses[0].copy()
    optimized=graph.optimize()
    np.testing.assert_allclose(optimized[0],anchor_before,atol=1e-12)
    assert np.linalg.norm(optimized[-1][:3,3]-truth[:3,3])<.03
    assert np.linalg.norm(so3_log(optimized[-1][:3,:3].T@truth[:3,:3]))<.01


def test_supported_normals_bound_longer_unanchored_mobile_drift(runtime_assets,tmp_path):
    """The sparse-normal correction must work even without a commissioned map."""
    from slam_and_twin.icp_pose_graph import ICPPoseGraph
    from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
    config,_,_=runtime_assets
    graph=ICPPoseGraph(max_keyframes=8)
    source=SyntheticLabSimulator(config["seed"],config,mobile=True)
    deskew=None;previous=None;errors=[]
    try:
        for _ in range(24):
            raw=source.next_frame()
            if deskew is None:deskew=ContinuousTimeIMUDeskewer(initial_pose=raw.sensor_pose_world)
            aligned=deskew.deskew_frame(raw)
            estimated=graph.add_scan(aligned.lidar_points[:,:3],aligned.sensor_pose_world)
            velocity=(estimated[:3,3]-previous[:3,3])/raw.sweep_duration if previous is not None else deskew.velocity
            deskew.correct_state(estimated,velocity);previous=estimated.copy()
            # Independent scoring only: this pose is never given to the graph.
            expected=source.last_ground_truth.pose_world
            errors.append([float(np.linalg.norm(estimated[:3,3]-expected[:3,3])),
                           float(np.linalg.norm(so3_log(estimated[:3,:3].T@expected[:3,:3])))])
    finally:source.close()
    assert max(error[0] for error in errors)<.06,errors
    assert max(error[1] for error in errors)<.05,errors
    assert len(graph.clouds)==8 and graph.dropped_keyframes==16
    report={"frames":24,"map_anchoring":False,"pose_errors_m_rad":errors,
            "max_translation_m":max(error[0] for error in errors),
            "max_rotation_rad":max(error[1] for error in errors)}
    (tmp_path/"unanchored_mobile_drift.json").write_text(json.dumps(report,indent=2),encoding="utf-8")


def test_repeated_icp_pose_feedback_preserves_so3_under_roundoff():
    from slam_and_twin.icp_pose_graph import ICPPoseGraph
    random=np.random.default_rng(117)
    cloud=random.uniform(-2,2,(420,3))
    cloud[:140,0]=0;cloud[140:280,1]=0;cloud[280:,2]=0
    graph=ICPPoseGraph(max_keyframes=8)
    pose=np.eye(4);pose[:3,3]=[2.,-3.,.5]
    # A realistic tiny metric error in the original seed previously magnified
    # under repeated transpose inversion/composition until camera contracts broke.
    pose[:3,:3]*=1+1e-11
    for _ in range(40):
        pose=graph.add_scan(cloud,pose)
        np.testing.assert_allclose(pose[:3,:3].T@pose[:3,:3],np.eye(3),atol=2e-12,rtol=0)
        assert np.linalg.det(pose[:3,:3])==pytest.approx(1.,abs=2e-12)
    assert graph.total_scans==40 and len(graph.clouds)==8
    np.testing.assert_allclose(pose[:3,3],[2.,-3.,.5],atol=1e-7)


@pytest.mark.parametrize('rotation',[np.diag([-1.,1.,1.]),np.eye(3)*1.1])
def test_icp_does_not_repair_reflections_or_true_affine_inputs(rotation):
    from slam_and_twin.icp_pose_graph import ICPPoseGraph
    graph=ICPPoseGraph();pose=np.eye(4);pose[:3,:3]=rotation
    with pytest.raises(ValueError,match='proper|scale/shear'):
        graph.add_scan(np.random.default_rng(1).normal(size=(30,3)),pose)

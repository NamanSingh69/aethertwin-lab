import json
import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from core.contracts import Detection3D,OrientedGeofence3D,SemanticClass
from tracking_and_navigation.geometry import OBB,boxes_intersect,intersection_volume,generalized_iou_3d
from tracking_and_navigation.kalman_9dof_tracker import KalmanMultiObjectTracker,transition_and_noise
from tracking_and_navigation.risk_and_geofence import RiskGeofenceEngine
from tracking_and_navigation.traffic_heatmap import TrafficHeatmap
from tracking_and_navigation.safe_corridor_planner import PlannerGrid,SafeCorridorPlanner


def detection(position=(0,0,1),velocity=(1,0,0),dt=.1,label=2,embedding=None,size=(.4,.4,1.5)):
    probabilities=np.eye(7)[label]*.94+.01
    return Detection3D(np.asarray(position,dtype=float),np.asarray(size),np.eye(3),
        probabilities,np.asarray(velocity)*dt,np.eye(7)[label] if embedding is None else np.asarray(embedding),
        np.asarray(position)[None],np.ones(10),True,1e-5)


def entity(position=(0,0,1),velocity=(1,0,0),size=(.4,.4,1.5)):
    return KalmanMultiObjectTracker().update([detection(position,velocity,size=size)],0)[0]


def planner(grid,radius=.04):
    return SafeCorridorPlanner(grid,radius=radius,mppi_samples=12,mppi_iterations=1,seed=10)


@pytest.mark.parametrize('model',['ca','cv'])
def test_occluded_track_is_explicit_prediction_until_measurement_returns(model):
    tracker=KalmanMultiObjectTracker(model=model)
    observed=tracker.update([detection()],0.,.1)[0]
    predicted=tracker.update([],.2,.2)[0]
    assert observed.state_kind=='measurement_updated' and observed.last_observed_timestamp==0.
    assert predicted.state_kind=='predicted' and predicted.last_observed_timestamp==0.
    returned=tracker.update([detection((.4,0,1))],.4,.2)[0]
    assert returned.track_id==observed.track_id
    assert returned.state_kind=='measurement_updated' and returned.last_observed_timestamp==.4


def test_exact_obb_intersection_volume_and_rotation_invariance():
    a=OBB(np.zeros(3),np.ones(3)*.5,np.eye(3))
    b=OBB(np.array([.5,0,0]),np.ones(3)*.5,np.eye(3))
    assert intersection_volume(a,b)==pytest.approx(.5,abs=1e-10)
    assert generalized_iou_3d(a,b)==pytest.approx(1/3,abs=1e-10)
    R=Rotation.from_euler("xyz",[.3,-.4,.7]).as_matrix(); translation=np.array([2,-4,1])
    ar=OBB(R@a.center+translation,a.half_extents,R@a.rotation)
    br=OBB(R@b.center+translation,b.half_extents,R@b.rotation)
    assert generalized_iou_3d(ar,br)==pytest.approx(1/3,abs=1e-9)
    assert generalized_iou_3d(a,a)==pytest.approx(1)


def test_sat_detects_crossing_boxes_with_no_included_corners():
    a=OBB(np.zeros(3),np.array([2,.1,.1]),np.eye(3))
    b=OBB(np.zeros(3),np.array([2,.1,.1]),Rotation.from_euler("z",np.pi/2).as_matrix())
    assert not np.any(a.contains(b.corners))
    assert not np.any(b.contains(a.corners))
    assert boxes_intersect(a,b)
    assert intersection_volume(a,b)==pytest.approx(.008,abs=1e-8)
    assert not boxes_intersect(a,OBB(np.array([0,0,.21]),b.half_extents,b.rotation))


def test_tracker_irregular_time_motion_and_covariance():
    tracker=KalmanMultiObjectTracker(model="ca",max_age=2)
    previous=0.
    for timestamp in [0.,.1,.27,.5,.85,1.2]:
        dt=.1 if timestamp==0 else timestamp-previous
        result=tracker.update([detection((timestamp,0,1),dt=dt)],timestamp,dt)
        assert len(result)==1 and result[0].track_id==1
        assert result[0].position[0]==pytest.approx(timestamp,abs=.025)
        assert result[0].velocity[0]==pytest.approx(1,abs=.05)
        assert np.linalg.eigvalsh(result[0].covariance).min()>-1e-10
        previous=timestamp
    assert len(tracker.update([],2.0,.8))==1
    assert tracker.update([],3.3,1.3)==[]


def test_cv_and_ca_process_covariance_are_psd():
    for model,dimension in (("ca",9),("cv",6)):
        for dt in (.001,.1,2.):
            F,Q=transition_and_noise(dt,model)
            assert F.shape==(dimension,dimension)
            assert np.linalg.eigvalsh(Q).min()>-1e-10
        tracker=KalmanMultiObjectTracker(model=model)
        assert len(tracker.update([detection()],0))==1


def test_crossing_tracks_retain_ids_with_appearance_cues():
    tracker=KalmanMultiObjectTracker(process_noise=.1)
    ids={}
    for k in range(9):
        t=k*.1
        detections=[detection((-.4+t,0,1),(1,0,0),label=2),
                    detection((.4-t,0,1),(-1,0,0),label=3)]
        if k%2: detections.reverse()
        entities=tracker.update(detections,t,.1)
        current={int(e.semantic_class):e.track_id for e in entities}
        if k==0: ids=current
        assert current==ids


def test_ttc_relative_velocity_tangent_and_overlap():
    engine=RiskGeofenceEngine()
    assert engine.ttc([5,0,0],[-1,0,0],radii=1)==pytest.approx(4)
    assert engine.ttc([5,0,0],[1,0,0],robot_vel=[2,0,0],radii=1)==pytest.approx(4)
    assert np.isinf(engine.ttc([5,0,0],[1,0,0],robot_vel=[1,0,0],radii=1))
    assert engine.ttc([0,0,0],[1,0,0],radii=1)==0
    assert engine.ttc([5,1,0],[-1,0,0],radii=1)==pytest.approx(5)
    assert np.isinf(engine.ttc([5,1.01,0],[-1,0,0],radii=1))


def test_directional_risk_and_geofence_entry_exit_events():
    ent=entity((0,0,1),(1,0,0),size=(4,.2,.2))
    engine=RiskGeofenceEngine()
    risk,_=engine.compute_risk(np.array([[2,0,1],[-2,0,1],[0,5,1]]),[ent])
    assert risk[0]>risk[1]>risk[2]
    zone=OrientedGeofence3D("test",np.array([0,0,1]),Rotation.from_euler("z",np.pi/2).as_matrix(),np.array([2,.1,.1]))
    updated,zones,alerts=engine.evaluate([ent],[zone])
    assert updated[0].is_breaching_geofence and zones[0].is_breached and len(alerts)==1
    assert engine.evaluate(updated,zones)[2]==[]
    assert "exited" in engine.evaluate([],zones)[2][0]


def test_heatmap_sampling_invariance_decay_and_persistence(tmp_path):
    e=entity((0,0,1),(1,0,0))
    fine=TrafficHeatmap(); coarse=TrafficHeatmap()
    fine.update([],0); coarse.update([],0)
    for t in np.arange(.1,1.01,.1): fine.update([e],float(t))
    coarse.update([e],1.)
    assert np.allclose(fine.values,coarse.values,atol=1e-12)
    initial=fine.values.sum(); fine.update([],31.)
    assert fine.values.sum()==pytest.approx(initial/np.e)
    file=tmp_path/"heat.npz"; fine.save(file); restored=TrafficHeatmap.load(file)
    assert np.array_equal(restored.values,fine.values) and restored.timestamp==31.


def test_segment_collision_checks_intervening_space_and_unknown():
    occupied=np.zeros((7,7,7),bool); occupied[3,3,3]=True
    known=np.ones_like(occupied)
    grid=PlannerGrid(np.zeros(3),1.,occupied,known); p=planner(grid,0.)
    assert p.segment_clear([1.5,3.5,3.5],[2.5,3.5,3.5])
    assert not p.segment_clear([1.5,3.5,3.5],[5.5,3.5,3.5])
    known[3,:,:]=False
    grid=PlannerGrid(np.zeros(3),1.,np.zeros_like(occupied),known)
    result=planner(grid,0.).plan([1.5,3.5,3.5],[5.5,3.5,3.5])
    assert result.status=="no_path"


def test_ground_planner_routes_through_gap_and_exports(tmp_path):
    occupied=np.zeros((24,24,8),bool); occupied[12,:,:]=True; occupied[12,16:22,:]=False
    grid=PlannerGrid(np.zeros(3),.25,occupied,np.ones_like(occupied))
    p=planner(grid)
    start=np.array([.625,.625,.625]); goal=np.array([5.375,.625,.625])
    result=p.plan(start,goal)
    assert result.status=="ok",result.reason
    assert np.allclose(result.points[0],start) and np.allclose(result.points[-1],goal)
    assert p.path_clear(result.points,result.times)
    assert np.max(result.points[:,1])>3.8
    assert p.last_diagnostics["mppi_samples"]==12
    destination=tmp_path/"route.json"; p.export(result,destination)
    assert json.loads(destination.read_text())["schema_version"]==1
    file=tmp_path/"grid.npz"; grid.save(file)
    assert np.array_equal(PlannerGrid.load(file).occupied,occupied)


def test_volumetric_planning_can_go_over_wall_ground_cannot():
    occupied=np.zeros((12,12,12),bool); occupied[6,:,0:5]=True
    grid=PlannerGrid(np.zeros(3),.25,occupied,np.ones_like(occupied))
    p=planner(grid)
    start=np.array([.625,1.375,.625]); goal=np.array([2.375,1.375,.625])
    assert p.plan(start,goal,ground=True).status=="no_path"
    result=p.plan(start,goal,ground=False)
    assert result.status=="ok",result.reason
    assert np.max(result.points[:,2])>1.25
    assert p.path_clear(result.points,result.times)


def test_unknown_start_and_footprint_narrow_gap_are_rejected():
    occupied=np.zeros((12,12,6),bool); occupied[6,:,:]=True; occupied[6,5,:]=False
    known=np.ones_like(occupied)
    p=planner(PlannerGrid(np.zeros(3),.2,occupied,known),radius=.15)
    result=p.plan([.5,1.1,.5],[1.9,1.1,.5])
    assert result.status=="no_path"
    known[2,5,2]=False
    p=planner(PlannerGrid(np.zeros(3),.2,occupied,known),radius=0)
    assert p.plan([.5,1.1,.5],[1.9,1.1,.5]).status=="invalid_start"


def test_planner_does_not_shortcut_across_risk_cost():
    occupied=np.zeros((20,20,5),bool)
    grid=PlannerGrid(np.zeros(3),.25,occupied,np.ones_like(occupied))
    p=planner(grid)
    xx,yy=np.meshgrid(np.arange(20),np.arange(20),indexing="ij")
    risk=np.exp(-((xx-10)**2+(yy-10)**2)/7)
    start=np.array([.625,2.625,.625]); goal=np.array([4.375,2.625,.625])
    route=p.plan(start,goal,risk=risk)
    assert route.status=="ok",route.reason
    direct=p._route_cost(np.array([start,goal]),risk)
    assert p._route_cost(route.points,risk)<direct*.8


def test_voxel_map_resampling_preserves_unseen_subcells_and_ambiguity():
    from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
    source=SparseMultiReturnVoxelMap(voxel_size=.1,decay_rate=1.)
    for x in range(2):
        for y in range(2):
            for z in range(2): source.cells[(x,y,z)]=[-.4,0.,0.]
    bounds=(0,.2,0,.2,0,.2)
    grid=PlannerGrid.from_voxel_map(source,bounds,resolution=.2)
    assert grid.known[0,0,0] and not grid.occupied[0,0,0]
    del source.cells[(1,1,1)]
    grid=PlannerGrid.from_voxel_map(source,bounds,resolution=.2)
    assert not grid.known[0,0,0] and not grid.occupied[0,0,0]
    source.cells[(1,1,1)]=[0.,0.,0.]
    assert not PlannerGrid.from_voxel_map(source,bounds,resolution=.2).known[0,0,0]
    source.cells[(1,1,1)]=[.85,0.,0.]
    grid=PlannerGrid.from_voxel_map(source,bounds,resolution=.2)
    assert grid.known[0,0,0] and grid.occupied[0,0,0]


def test_depth_commissioning_uses_only_calibrated_valid_sensor_pixels():
    from core.contracts import MultiModalSensorFrame,CameraCalibration
    from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
    from tracking_and_navigation.observed_navigation import integrate_commissioning_frame
    K=np.array([[3.,0,1],[0,3.,1],[0,0,1]])
    pose=np.eye(4); pose[:3,3]=[1,1,1]
    depth=np.ones((3,3),np.float32); depth[0,0]=0
    f=MultiModalSensorFrame(0,0.,np.empty((0,7)),sensor_pose_world=pose,
        rgb=np.zeros((3,3,3),np.uint8),depth=depth,
        calibration=CameraCalibration(K,np.eye(4)),camera_timestamp=.1)
    source=SparseMultiReturnVoxelMap(voxel_size=.1,decay_rate=1.)
    counts=integrate_commissioning_frame(source,f,stationary=True)
    assert counts=={"lidar_rays":0,"depth_rays":8}
    endpoint=source.query(np.array([[1,1,2.]]),return_known=True)
    assert endpoint[1][0] and endpoint[0][0]>.4


def test_exact_cached_edge_masks_match_continuous_slab_reference():
    from itertools import product
    random=np.random.default_rng(42)
    occupied=random.random((7,6,5))<.08
    known=random.random(occupied.shape)>.06
    grid=PlannerGrid(np.array([-.75,-1.,-.5]),.25,occupied,known)
    offsets=[np.asarray(d) for d in product((-1,0,1),repeat=3) if d!=(0,0,0)]
    for radius in (0.,.04,.125,.2,.4,.6):
        p=planner(grid,radius)
        for offset in offsets:
            cached=p._static_edge_mask(offset)
            for _ in range(12):
                cell=np.array([random.integers(n) for n in grid.shape]); destination=cell+offset
                actual=bool(cached[tuple(cell)])
                expected=p._static_segment_clear(grid.center(cell),grid.center(destination))
                assert actual==expected,(radius,cell,offset)
        # Cached point-footprint masks have the same boundary/contact rules.
        for cell in np.argwhere(np.ones(grid.shape,bool)):
            center=grid.center(cell)
            assert bool(p._center_clear[tuple(cell)])==p._static_segment_clear(center,center)


def test_dynamic_slab_batch_matches_single_entity_reference():
    from tracking_and_navigation.safe_corridor_planner import _segment_intersects_boxes
    from tracking_and_navigation.geometry import obb_from_corners
    grid=PlannerGrid(np.zeros(3),.25,np.zeros((24,24,12),bool),np.ones((24,24,12),bool))
    p=planner(grid,radius=.15); random=np.random.default_rng(3)
    entities=[entity(random.uniform([.5,.5,.5],[5.5,5.5,2.5]),random.uniform(-1,1,3)) for _ in range(8)]
    context=p._dynamic_geometry(entities)
    for _ in range(80):
        start=random.uniform([.5,.5,.5],[5.5,5.5,2.5]); end=random.uniform([.5,.5,.5],[5.5,5.5,2.5])
        t0=float(random.uniform(0,1)); t1=t0+float(random.uniform(.01,2))
        expected=True
        for e in entities:
            box=obb_from_corners(e.obb_corners)
            uncertainty=2*np.sqrt(np.linalg.eigvalsh(e.covariance[:3,:3]).max())
            half=box.half_extents+p.radius+uncertainty+np.linalg.norm(e.acceleration)*(t1-t0)**2/8
            c0=e.position+e.velocity*t0+.5*e.acceleration*t0*t0
            c1=e.position+e.velocity*t1+.5*e.acceleration*t1*t1
            if _segment_intersects_boxes((start-c0)@box.rotation,(end-c1)@box.rotation,(-half)[None],half[None])[0]:
                expected=False; break
        assert p._dynamic_segment_clear(start,end,t0,t1,context)==expected


def test_subcell_start_and_goal_are_both_retained():
    occupied=np.zeros((8,8,8),bool)
    p=planner(PlannerGrid(np.zeros(3),.5,occupied,np.ones_like(occupied)),radius=.04)
    a=np.array([1.1,1.1,1.1]); b=np.array([1.4,1.4,1.1])
    result=p.plan(a,b)
    assert result.status=="ok"
    assert np.allclose(result.points[0],a)
    assert np.allclose(result.points[-1],b)


def test_validated_route_is_not_rounded_into_nearby_voxel_wall():
    from tracking_and_navigation.safe_corridor_planner import remaining_trajectory
    occupied=np.zeros((12,8,8),bool);occupied[6,2,2]=True
    grid=PlannerGrid(np.zeros(3),.2,occupied,np.ones_like(occupied))
    p=planner(grid,radius=0.)
    # Float32 rounds this X coordinate from just outside the wall to INSIDE it.
    x=1.2-1e-8
    start=np.array([x,.45,.5]);goal=np.array([x,.55,.5])
    assert not p.segment_clear(start.astype(np.float32),goal.astype(np.float32))
    result=p.plan(start,goal)
    assert result.status=="ok",result.reason
    assert result.points.dtype==np.float64 and p.path_clear(result.points,result.times)
    remainder=remaining_trajectory(result,result.times[-1]*.3)
    assert p.path_clear(remainder.points,remainder.times)


def test_dynamic_occupancy_merge_covers_entire_source_voxel_volume():
    from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
    from tracking_and_navigation.observed_navigation import merge_observed_occupancy
    occupied=np.zeros((4,4,4),bool); known=np.zeros_like(occupied)
    grid=PlannerGrid(np.zeros(3),.25,occupied,known)
    mapping=SparseMultiReturnVoxelMap(voxel_size=.18,decay_rate=1.)
    mapping.cells[(1,1,1)]=[.85,0.,0.]
    merged=merge_observed_occupancy(grid,mapping)
    assert merged.occupied[:2,:2,:2].all()
    assert int(merged.occupied.sum())==8 and int(merged.known.sum())==8
    assert not np.any(grid.occupied) and not np.any(grid.known)


def test_rebased_remaining_path_uses_current_entity_reference_time():
    from core.contracts import PlannedTrajectory
    from tracking_and_navigation.safe_corridor_planner import remaining_trajectory
    occupied=np.zeros((24,20,12),bool)
    p=planner(PlannerGrid(np.zeros(3),.25,occupied,np.ones_like(occupied)),radius=.04)
    points=np.array([[.5,1.,1.],[1.5,1.,1.],[2.5,1.,1.],[3.5,1.,1.],[4.5,1.,1.]])
    trajectory=PlannedTrajectory("ok",points,np.arange(5.))
    remaining=remaining_trajectory(trajectory,3.,position=points[3])
    assert np.allclose(remaining.points,points[3:]) and np.allclose(remaining.times,[0,1])
    # Obstacle currently crosses the remaining endpoint in one second. Checking
    # the original path with old times would predict it three seconds too late.
    crossing=entity((4.5,2.,1.),(0,-1,0),size=(.2,.2,.2))
    assert p.path_clear(trajectory.points,trajectory.times,[crossing])
    assert not p.path_clear(remaining.points,remaining.times,[crossing])
    # Conversely a present obstruction on an already traversed segment is not
    # a reason to discard a clear remaining path.
    behind=entity((1.5,1.,1.),(0,0,0),size=(.2,.2,.2))
    assert not p.path_clear(trajectory.points,trajectory.times,[behind])
    assert p.path_clear(remaining.points,remaining.times,[behind])


def _minimal_pipeline_for_navigation_test(monkeypatch,grid=None,source_size=.18,motion_model="ca"):
    """Real navigation/occupancy with an empty-perception test double."""
    from pathlib import Path
    from types import SimpleNamespace
    import yaml
    from core.contracts import PlannedTrajectory,VoxelGridState
    import core.pipeline as pipeline_module
    from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
    voxels=VoxelGridState(.18,np.empty((0,3),int),np.empty((0,3)),np.empty((0,12)),np.empty(0),np.empty(0),np.empty(0,int))
    fake_inference=SimpleNamespace(metadata={'synthetic_only':True},temporal_valid=False,max_voxels=1400,
        model=SimpleNamespace(neighbors=16,temporal_neighbors=12),
        infer=lambda *args,**kwargs:(SimpleNamespace(voxels=voxels),np.empty((0,7)),np.empty((0,3)),np.empty((0,256))))
    monkeypatch.setattr(pipeline_module,"InferenceEngine",lambda *args,**kwargs:fake_inference)
    config=yaml.safe_load((Path(__file__).resolve().parents[1]/"config/system_config.yaml").read_text())
    config["mapping"]["voxel_size"]=source_size; config["mapping"]["decay_rate"]=1.
    config["tracking"]["model"]=motion_model
    config["planning"].update(start=[1.125,1.125,.625],goal=[3.125,1.125,.625],replan_interval=5,radius=.2)
    if grid is None:
        occupied=np.zeros((20,16,8),bool)
        grid=PlannerGrid(np.zeros(3),.25,occupied,np.ones_like(occupied))
    twin=SimpleNamespace(floor_transform=np.eye(4),points=np.array([[0.,0.,0.]]))
    pipeline=pipeline_module.LabPipeline(config,None,twin,SparseMultiReturnVoxelMap(.25),grid)
    pipeline.deskewer=SimpleNamespace(deskew_frame=lambda frame:frame,last_diagnostics={})
    pipeline.planner=planner(grid,radius=.2)
    pipeline.trajectory=PlannedTrajectory("ok",np.array([pipeline.start,pipeline.goal]),np.array([0.,2.]))
    pipeline.frames=1; pipeline.last_time=0.; pipeline.geofences=[]
    return pipeline


def test_root_pipeline_merges_source_voxel_volume_not_just_its_center(monkeypatch):
    pipeline=_minimal_pipeline_for_navigation_test(monkeypatch)
    pipeline.occupancy.cells[(1,1,1)]=[.85,0.,0.]
    pipeline._update_planning_grid()
    assert pipeline.planner.grid.occupied[:2,:2,:2].all()


def test_root_pipeline_new_obstacle_invalidates_path_before_robot_moves(monkeypatch):
    from core.contracts import MultiModalSensorFrame
    from tracking_and_navigation.observed_navigation import merge_observed_occupancy
    pipeline=_minimal_pipeline_for_navigation_test(monkeypatch)
    pipeline.occupancy.cells[(10,6,3)]=[.85,0.,0.]
    expected_grid=merge_observed_occupancy(pipeline.base_grid,pipeline.occupancy)
    verifier=planner(expected_grid,radius=.2)
    before=pipeline.robot_position.copy()
    # Frame 1 is deliberately between scheduled replans. The new evidence must
    # still invalidate unsafe motion immediately, even if A* is rate limited.
    frame=MultiModalSensorFrame(1,.1,np.empty((0,7)))
    state=pipeline.process(frame)
    assert verifier.segment_clear(before,pipeline.robot_position)
    if state.trajectory_status!="ok":
        assert state.planned_safe_path is None
        assert np.allclose(pipeline.robot_position,before)
        assert np.allclose(pipeline.robot_velocity,0.)
    else:
        assert verifier.path_clear(state.planned_safe_path)


def test_root_pipeline_no_path_waits_without_moving_or_publishing(monkeypatch):
    from core.contracts import MultiModalSensorFrame
    pipeline=_minimal_pipeline_for_navigation_test(monkeypatch)
    for y in range(23):
        for z in range(12): pipeline.occupancy.cells[(10,y,z)]=[.85,0.,0.]
    calls=[]; original_plan=pipeline.planner.plan
    def counted_plan(*args,**kwargs):
        calls.append(1)
        return original_plan(*args,**kwargs)
    monkeypatch.setattr(pipeline.planner,"plan",counted_plan)
    before=pipeline.robot_position.copy()
    for frame_id,timestamp in ((1,.1),(2,.2)):
        state=pipeline.process(MultiModalSensorFrame(frame_id,timestamp,np.array([[4.9,3.9,1.9,.5,1,1,0.]])))
        assert state.trajectory_status=="no_path"
        assert state.planned_safe_path is None
        assert np.allclose(pipeline.robot_position,before)
        assert np.allclose(pipeline.robot_velocity,0.)
    assert len(calls)==1,"No-path waiting should respect the configured replanning interval"


def test_root_empty_observations_hold_an_existing_clear_route(monkeypatch):
    from core.contracts import MultiModalSensorFrame
    pipeline=_minimal_pipeline_for_navigation_test(monkeypatch)
    before=pipeline.robot_position.copy()
    state=pipeline.process(MultiModalSensorFrame(1,.1,np.empty((0,7))))
    assert state.validity['observations'] is False
    assert state.validity['flow'] is False
    assert state.trajectory_status=='stale_observations'
    assert state.planned_safe_path is None
    np.testing.assert_array_equal(pipeline.robot_position,before)
    np.testing.assert_array_equal(pipeline.robot_velocity,np.zeros(3))
    # A returning observation causes a fresh plan even between regular replans.
    state=pipeline.process(MultiModalSensorFrame(2,.2,np.array([[4.9,3.9,1.9,.5,1,1,0.]])))
    assert state.validity['observations'] is True
    assert state.trajectory_status=='ok'


def test_root_remaining_trajectory_rebases_current_time(monkeypatch):
    from core.contracts import PlannedTrajectory
    pipeline=_minimal_pipeline_for_navigation_test(monkeypatch)
    pipeline.trajectory=PlannedTrajectory("ok",np.array([[.5,1,1],[1.5,1,1],[2.5,1,1],[3.5,1,1],[4.5,1,1]]),np.arange(5.))
    pipeline.trajectory_elapsed=3.; pipeline.robot_position=np.array([3.5,1.,1.])
    points,times=pipeline._remaining_trajectory()
    assert np.allclose(points,[[3.5,1,1],[4.5,1,1]])
    assert np.allclose(times,[0,1])


def test_root_unknown_space_requires_all_fine_subcells_observed(monkeypatch):
    grid=PlannerGrid(np.zeros(3),.25,np.zeros((1,1,1),bool),np.zeros((1,1,1),bool))
    pipeline=_minimal_pipeline_for_navigation_test(monkeypatch,grid=grid,source_size=.05)
    # A 3^3 sample subset does not cover the required 5^3 source cells.
    for x in range(3):
        for y in range(3):
            for z in range(3): pipeline.occupancy.cells[(x,y,z)]=[-.4,0.,0.]
    pipeline._update_planning_grid()
    assert not pipeline.planner.grid.known[0,0,0]
    for x in range(5):
        for y in range(5):
            for z in range(5): pipeline.occupancy.cells[(x,y,z)]=[-.4,0.,0.]
    pipeline._update_planning_grid()
    assert pipeline.planner.grid.known[0,0,0] and not pipeline.planner.grid.occupied[0,0,0]


def test_root_large_source_voxel_blocks_all_overlapping_planner_cells(monkeypatch):
    grid=PlannerGrid(np.zeros(3),.25,np.zeros((4,4,4),bool),np.ones((4,4,4),bool))
    pipeline=_minimal_pipeline_for_navigation_test(monkeypatch,grid=grid,source_size=.6)
    pipeline.occupancy.cells[(0,0,0)]=[.85,0.,0.]
    pipeline._update_planning_grid()
    assert pipeline.planner.grid.occupied[:3,:3,:3].all()
    assert int(pipeline.planner.grid.occupied.sum())==27


def test_root_cv_model_snapshot_geometry_is_read_only(monkeypatch):
    from core.contracts import MultiModalSensorFrame
    pipeline=_minimal_pipeline_for_navigation_test(monkeypatch,motion_model="cv")
    pipeline.tracker.update([detection((3.,3.,.8),(0.,0.,0.))],0.,.1)
    state=pipeline.process(MultiModalSensorFrame(1,.1,np.empty((0,7))))
    assert len(state.entities)==1 and state.entities[0].covariance.shape==(6,6)
    assert np.allclose(state.entities[0].acceleration,0.)
    for value in (state.sensor_pose_world,state.voxel_centers,state.traffic_heatmap_2d,
                  state.entities[0].position,state.entities[0].obb_corners,state.geofences):
        if isinstance(value,np.ndarray): assert not value.flags.writeable

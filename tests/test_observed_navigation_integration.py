"""Optional ~70s real sensor-simulator -> DDA -> planning integration regression.

Run with RUN_OBSERVED_INTEGRATION=1. This test deliberately has no access to
last_ground_truth, simulator surfaces, labels, or architectural scene meshes.
"""
import json
import os
import time
import numpy as np
import pytest
from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
from tracking_and_navigation.safe_corridor_planner import PlannerGrid,SafeCorridorPlanner
from tracking_and_navigation.observed_navigation import integrate_commissioning_frame,select_observed_ground_endpoints


@pytest.mark.skipif(os.getenv("RUN_OBSERVED_INTEGRATION")!="1",reason="Enable RUN_OBSERVED_INTEGRATION=1 for dense commissioning ray integration")
def test_observed_commissioning_produces_supported_collision_checked_route(tmp_path):
    started=time.perf_counter()
    simulator=SyntheticLabSimulator(seed=0,config={"lidar_points":2500,"camera_width":160,"camera_height":100})
    try:
        observations=simulator.commissioning_frames()
        mapping=SparseMultiReturnVoxelMap(voxel_size=.25,decay_rate=1.,max_age=3600.)
        counts=[integrate_commissioning_frame(mapping,frame) for frame in observations]
        grid=PlannerGrid.from_voxel_map(mapping)
        start,goal,selection=select_observed_ground_endpoints(grid,height=.625,radius=.2)
        assert start is not None and goal is not None
        planner=SafeCorridorPlanner(grid,radius=.2,mppi_samples=12,mppi_iterations=1,seed=0)
        trajectory=planner.plan(start,goal)
        assert trajectory.status=="ok",trajectory.reason
        assert np.linalg.norm(goal-start)>5.
        assert planner.path_clear(trajectory.points,trajectory.times)
        # Every selected center remains known free under the actual measured map.
        centers=grid.index(trajectory.points)
        assert np.all(grid.known[tuple(centers.T)])
        assert not np.any(grid.occupied[tuple(centers.T)])
        report={"synthetic_observations":True,"source_voxel_m":.25,"planning_resolution_m":.25,
            "radius_m":.2,"height_m":.625,"lidar_returns":sum(c["lidar_rays"] for c in counts),
            "depth_rays":sum(c["depth_rays"] for c in counts),"known_cells":int(grid.known.sum()),
            "occupied_cells":int(grid.occupied.sum()),"free_cells":int((grid.known&~grid.occupied).sum()),
            "selection":selection,"start":start.tolist(),"goal":goal.tolist(),
            "planner":planner.last_diagnostics,"elapsed_seconds":time.perf_counter()-started}
        (tmp_path/"observed_navigation_report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    finally:
        simulator.close()

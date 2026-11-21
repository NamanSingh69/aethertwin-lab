"""AIDE2 C30: with no goal set, nothing reads the planning grid, yet the tiled hotspot profile rebuilt it every 10th frame
(353 ms each on the real bench data, ~30 ms a frame on average plus a p95 spike; read-only review 2026-11-05). By
default an idle run now skips the rebuild; the first frame with a goal rebuilds it from the current map before planning
(the planner's set_grid is stateless), and refresh_planning_grid() gives readers such as evidence tools the same grid as
a run that rebuilt it every frame."""
import copy
from pathlib import Path
import numpy as np
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]


def pipeline_and_frames(frames_count,interval=None):
    torch=pytest.importorskip('torch')
    from core.pipeline import LabPipeline,load_commissioned_assets
    from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'));config['device']='cpu'
    config['planning'].pop('idle_grid_interval',None)
    if interval is not None:config['planning']['idle_grid_interval']=interval
    twin,static,grid=load_commissioned_assets(ROOT/f"assets/twin_seed{config['seed']}")
    source=SyntheticLabSimulator(config['seed'],config,mobile=False)
    try:frames=[source.next_frame() for _ in range(frames_count)]
    finally:source.close()
    torch.set_num_threads(min(2,torch.get_num_threads()))
    pipeline=LabPipeline(copy.deepcopy(config),ROOT/config['perception']['checkpoint'],twin,static,grid)
    pipeline.start=pipeline.goal=None;pipeline.planning_requested=False
    calls=[];original=pipeline._update_planning_grid
    pipeline._update_planning_grid=lambda:(calls.append(pipeline.frames),original())[1]
    return pipeline,frames,calls


def test_by_default_an_idle_run_never_rebuilds_the_planning_grid():
    pipeline,frames,calls=pipeline_and_frames(6)
    try:
        for f in frames:pipeline.process(f)
        assert calls==[] and pipeline.planning_requested is False
    finally:pipeline.close()


def test_refresh_gives_the_grid_of_a_run_that_rebuilt_it_every_frame():
    lazy,frames,_=pipeline_and_frames(5)
    eager,_,eager_calls=pipeline_and_frames(5,interval=1)
    try:
        for f in frames:lazy.process(f);eager.process(f)
        assert len(eager_calls)==5
        lazy.refresh_planning_grid()
        a,b=lazy.planner.grid,eager.planner.grid
        assert np.array_equal(a.occupied,b.occupied) and np.array_equal(a.known,b.known)
        assert np.array_equal(a.origin,b.origin) and a.resolution==b.resolution
        assert np.array_equal(lazy.planner.clearance,eager.planner.clearance)
    finally:lazy.close();eager.close()


def test_the_first_frame_with_a_goal_rebuilds_the_grid_before_planning():
    pipeline,frames,calls=pipeline_and_frames(5)
    try:
        for f in frames[:3]:pipeline.process(f)
        assert calls==[]
        start=pipeline.robot_position.copy();pipeline.set_start_goal(start,start+np.array([.5,0,0]))
        pipeline.process(frames[3])
        assert calls==[3] and pipeline.planning_requested is True
    finally:pipeline.close()

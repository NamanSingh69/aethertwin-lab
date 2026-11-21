"""AIDE2 C16: on the parked GPU rehearsal (2026-11-05) the planning stage cost ~209 ms of ~650 ms per frame rebuilding a
planning grid that nothing reads until a goal is set."""
import copy
from pathlib import Path
import numpy as np
import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]


def run(frames_count,goal):
    torch=pytest.importorskip('torch')
    from core.pipeline import LabPipeline,load_commissioned_assets
    from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'));config['device']='cpu'
    config['planning']['idle_grid_interval']=5
    twin,static,grid=load_commissioned_assets(ROOT/f"assets/twin_seed{config['seed']}")
    source=SyntheticLabSimulator(config['seed'],config,mobile=False)
    try:frames=[source.next_frame() for _ in range(frames_count)]
    finally:source.close()
    threads=torch.get_num_threads();torch.set_num_threads(min(2,threads))
    pipeline=LabPipeline(copy.deepcopy(config),ROOT/config['perception']['checkpoint'],twin,static,grid)
    calls=[];original=pipeline._update_planning_grid
    pipeline._update_planning_grid=lambda:(calls.append(pipeline.frames),original())[1]
    try:
        if not goal:
            if hasattr(pipeline,'start'):pipeline.start=pipeline.goal=None
            pipeline.planning_requested=False
        for f in frames:pipeline.process(f)
        return calls,pipeline.planning_requested
    finally:pipeline.close();torch.set_num_threads(threads)


def test_without_a_goal_the_grid_is_refreshed_every_idle_interval_frames():
    calls,requested=run(7,goal=False)
    assert requested is False and len(calls)==2  # frames 0 and 5 of 0..6


def test_with_a_goal_the_grid_is_refreshed_every_frame():
    calls,requested=run(4,goal=True)
    assert requested is True and len(calls)==4

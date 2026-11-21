"""AIDE2 C12: tomorrow's 30-minute run is parked. Live mode used to force mobile odometry (drift, pose-graph
stalls, localization loss within ~40 real frames, rehearsal 2026-11-05); a parked run localizes once, then holds."""
import copy
from dataclasses import replace
from pathlib import Path
import numpy as np
import pytest
import yaml
from core.se3_math import se3_exp,se3_log,inverse

ROOT=Path(__file__).resolve().parents[1]


def parked_run(offset,frames_count=6):
    torch=pytest.importorskip('torch')
    from core.pipeline import LabPipeline,load_commissioned_assets
    from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'));config['device']='cpu'
    config['live']['parked_relocalize']=True
    twin,static,grid=load_commissioned_assets(ROOT/f"assets/twin_seed{config['seed']}")
    source=SyntheticLabSimulator(config['seed'],config,mobile=False)
    try:frames=[source.next_frame() for _ in range(frames_count)]
    finally:source.close()
    truth=frames[0].sensor_pose_world.copy();prior=truth@offset
    shifted=[replace(f,sensor_pose_world=prior) for f in frames]
    threads=torch.get_num_threads();torch.set_num_threads(min(2,threads))
    pipeline=LabPipeline(copy.deepcopy(config),ROOT/config['perception']['checkpoint'],twin,static,grid)
    try:states=[pipeline.process(f) for f in shifted]
    finally:pipeline.close();torch.set_num_threads(threads)
    return truth,prior,states


def test_a_cart_parked_off_the_commissioning_pose_is_localized_once_and_then_held():
    truth,prior,states=parked_run(se3_exp([0,0,np.radians(4),.2,-.15,0]))
    final=states[-1];error=se3_log(inverse(truth)@final.sensor_pose_world)
    assert np.linalg.norm(error[3:])<.05 and np.degrees(np.linalg.norm(error[:3]))<1.
    report=final.diagnostics['parked_localization']
    assert report['accepted'] is True and report['sweeps']==4 and final.validity['localization'] is True
    assert states[0].validity['localization'] is False  # not yet localized: honest until the ICP ran
    for state in states[4:]:np.testing.assert_allclose(state.sensor_pose_world,final.sensor_pose_world,atol=1e-9)


def test_a_relocalization_outside_the_trust_region_is_refused_and_reported():
    truth,prior,states=parked_run(se3_exp([0,0,0,3.,0,0]))
    report=states[-1].diagnostics['parked_localization']
    assert report['accepted'] is False and 'reason' in report and states[-1].validity['localization'] is False
    for state in states:np.testing.assert_allclose(state.sensor_pose_world,states[0].sensor_pose_world,atol=1e-9)  # the prior is kept


def test_live_parked_flag_selects_the_parked_pipeline():
    from command_center.runtime import live_motion
    cfg={'live':{'parked':True}};assert live_motion(cfg) is False and cfg['live']['parked_relocalize'] is True
    cfg={'live':{}};assert live_motion(cfg) is True and not cfg['live'].get('parked_relocalize')

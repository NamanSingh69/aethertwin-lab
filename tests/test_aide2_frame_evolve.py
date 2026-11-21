"""AIDE2 C34: each live frame went through several dataclasses.replace() calls, and every one re-ran the whole
MultiModalSensorFrame validation (copy + isfinite of the LiDAR points, RGB and depth) and re-froze the diagnostics
(read-only review 2026-11-05). freeze() now returns its own FrozenDicts as they are, and the pipeline's pose and
diagnostics updates validate only the changed field. New external inputs stay exactly as strict."""
import copy,sys
from dataclasses import fields,replace
from pathlib import Path
import numpy as np
import pytest
import yaml
from core import contracts
from core.contracts import FrozenDict,MultiModalSensorFrame,TwinMasterFrameState,evolve_frame,freeze

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(Path(__file__).resolve().parent))
RECORDING=Path.home()/'aethertwin_rec'/'wifi_stationary_60s'


def old_freeze(value):  # verbatim copy of core.contracts.freeze before C34: the oracle
    if isinstance(value,dict):return FrozenDict({key:old_freeze(item) for key,item in value.items()})
    if isinstance(value,(list,tuple)):return tuple(old_freeze(item) for item in value)
    if isinstance(value,np.ndarray):return contracts.array(value,dtype=value.dtype)
    return value


def same(a,b,path='',skip=()):
    """Exact structural equality: types, key order, array dtype and bits (NaN equal to NaN)."""
    assert type(a) is type(b) or (isinstance(a,dict) and isinstance(b,dict)),f'{path}: {type(a)} vs {type(b)}'
    if isinstance(a,dict):
        assert type(a) is type(b) and list(a)==list(b),f'{path}: keys {list(a)} vs {list(b)}'
        for key in a:
            if key not in skip:same(a[key],b[key],f'{path}.{key}',skip)
    elif isinstance(a,(list,tuple)):
        assert len(a)==len(b),path
        for i,(x,y) in enumerate(zip(a,b)):same(x,y,f'{path}[{i}]',skip)
    elif isinstance(a,np.ndarray):
        assert a.dtype==b.dtype and a.shape==b.shape and np.array_equal(a,b,equal_nan=a.dtype.kind in 'fc'),path
        assert a.flags.writeable==b.flags.writeable,path
    elif hasattr(a,'__dataclass_fields__'):
        for f in fields(a):
            if f.name not in skip:same(getattr(a,f.name),getattr(b,f.name),f'{path}.{f.name}',skip)
    elif isinstance(a,float) and np.isnan(a):assert np.isnan(b),path
    else:assert a==b,f'{path}: {a!r} vs {b!r}'


def sample_diagnostics():
    return {'a':1,'nested':{'list':[1,2,{'x':np.arange(3.)}],'arr':np.eye(2,dtype=np.float32)},'t':(1,'s'),'none':None}


def test_freeze_returns_its_own_frozen_dicts_unchanged_and_still_freezes_everything_else():
    raw=sample_diagnostics();frozen=freeze(raw)
    same(frozen,old_freeze(raw))
    assert freeze(frozen) is frozen and freeze({'k':frozen})['k'] is frozen
    same(freeze(frozen),old_freeze(frozen))
    raw['nested']['arr'][0,0]=7.;assert frozen['nested']['arr'][0,0]==1.  # input mutation never leaks in
    with pytest.raises(TypeError):frozen['b']=2
    with pytest.raises(ValueError):frozen['nested']['arr'][0,0]=3.


def test_a_frozen_dict_not_built_by_freeze_is_still_validated_and_copied():
    bad=FrozenDict({'x':np.array([np.nan])})
    with pytest.raises(ValueError):freeze(bad)
    with pytest.raises(ValueError):MultiModalSensorFrame(0,0.,np.empty((0,7)),diagnostics=bad)
    good=FrozenDict({'x':np.array([1.])});out=freeze(good)
    assert out is not good and out['x'] is not good['x'] and not out['x'].flags.writeable


@pytest.mark.parametrize('updates',[{'sensor_pose_world':np.diag([1.1,1,1,1])},{'sensor_pose_world':np.diag([-1.,1,1,1])},
    {'sensor_pose_world':np.full((4,4),np.nan)},{'sensor_pose_world':np.eye(3)},{'imu_to_lidar':np.diag([-1,1,1,1])},
    {'diagnostics':{'x':np.array([np.inf])}},{'diagnostics':{'deep':[{'x':np.array([np.nan])}]}},{'raw_timestamps':{'x':np.array([np.nan])}},
    {'lidar_points':np.full((2,7),np.nan)},{'beam_ids':np.zeros(3)},{'sweep_duration':0.},{'rgb':np.zeros((2,3))}])
def test_invalid_values_raise_through_constructor_replace_and_evolve_frame(updates):
    base=MultiModalSensorFrame(0,0.,np.zeros((2,7)),diagnostics=sample_diagnostics())
    kwargs={f.name:getattr(base,f.name) for f in fields(base)};kwargs.update(updates)
    with pytest.raises(ValueError):MultiModalSensorFrame(**kwargs)
    with pytest.raises(ValueError):replace(base,**updates)
    with pytest.raises(ValueError):evolve_frame(base,**updates)


def synthetic_frames(count,mobile=False):
    from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'))
    source=SyntheticLabSimulator(config['seed'],config,mobile=mobile)
    try:return [source.next_frame() for _ in range(count)]
    finally:source.close()


def real_frames(count,start=10):
    import itertools
    from ingestion.recorder_replayer import ReplaySource
    source=ReplaySource(str(RECORDING))
    try:return list(itertools.islice(iter(source),start,start+count))
    finally:source.close()


def test_evolve_frame_gives_the_frame_replace_gives_field_by_field():
    frames=synthetic_frames(2)+(real_frames(2) if RECORDING.exists() else [])
    for frame in frames:
        pose=frame.sensor_pose_world.copy();pose[:3,3]+=[.5,-.25,.125]
        for changes in ({'sensor_pose_world':pose},{'diagnostics':{**frame.diagnostics,'deskew_reseeded':'imu_gap'}},
                        {'sensor_pose_world':pose,'diagnostics':{**frame.diagnostics,'x':np.ones(2)}},{'imu_to_lidar':pose},
                        {'raw_timestamps':{'lidar':1.5}},{'lidar_points':frame.lidar_points[::-1].copy()}):
            new=evolve_frame(frame,**changes)
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(contracts,'freeze',old_freeze);old=replace(frame,**changes)
            same(new,old)
            for name in changes:assert getattr(new,name) is not changes[name]  # inputs are copied, never aliased


def state_record(state):
    """Everything a snapshot carries except wall-clock timing."""
    timing=('module_ms','processing_ms','memory_mb','end_to_end_fps')
    return state,{'skip':('fps','processed_at_unix','diagnostics')},{k:v for k,v in state.diagnostics.items() if k not in timing}


def run_pipeline(frames,old):
    torch=pytest.importorskip('torch')
    from core.pipeline import LabPipeline,load_commissioned_assets
    import core.pipeline as pipeline_module
    import perception_ml.inference_engine as engine_module
    import perception_ml.spatial_indices as spatial_module
    from test_aide2_perception_exact import old_infer,old_build_spatial_index_pack
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'));config['device']='cpu'
    twin,static,grid=load_commissioned_assets(ROOT/f"assets/twin_seed{config['seed']}")
    threads=torch.get_num_threads();deterministic=torch.are_deterministic_algorithms_enabled()
    torch.set_num_threads(1);torch.use_deterministic_algorithms(True);torch.manual_seed(0);np.random.seed(0)
    with pytest.MonkeyPatch.context() as patch:
        if old:
            patch.setattr(contracts,'freeze',old_freeze);patch.setattr(pipeline_module,'evolve_frame',replace)
            patch.setattr(engine_module.InferenceEngine,'infer',old_infer)
            patch.setattr(engine_module,'build_spatial_index_pack',old_build_spatial_index_pack)
            patch.setattr(spatial_module,'build_spatial_index_pack',old_build_spatial_index_pack)
        pipeline=LabPipeline(copy.deepcopy(config),ROOT/config['perception']['checkpoint'],twin,static,grid)
        try:
            states=[pipeline.process(f) for f in frames]
            persistence=dict(pipeline.inference.persistence)
        finally:pipeline.close();torch.set_num_threads(threads);torch.use_deterministic_algorithms(deterministic)
    return states,persistence


def assert_identical_runs(frames):
    old,old_persistence=run_pipeline(frames,old=True);new,new_persistence=run_pipeline(frames,old=False)
    assert len(old)==len(new)==len(frames) and old_persistence==new_persistence and len(new_persistence)
    for a,b in zip(old,new):
        A,skip,da=state_record(a);B,_,db=state_record(b)
        same(A,B,skip=skip['skip']);same(da,db)
        assert type(b.diagnostics) is FrozenDict and len(b.entities)==len(a.entities)
    return new


def test_lab_pipeline_outputs_are_bit_identical_to_the_old_code_on_synthetic_frames():
    states=assert_identical_runs(synthetic_frames(8))
    assert any(s.validity['flow'] for s in states) and sum(len(s.voxel_centers) for s in states)


@pytest.mark.skipif(not RECORDING.exists(),reason='real recording not on this machine')
def test_lab_pipeline_outputs_are_bit_identical_to_the_old_code_on_real_frames():
    states=assert_identical_runs(real_frames(20))
    assert sum(len(s.voxel_centers) for s in states)

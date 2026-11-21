"""AIDE2 C46: opt-in `perception.gpu_keepalive_ms` keeps the laptop GPU out of its idle power state between live frames
(the camera CNN took ~58 ms at a 0.2 s cadence in P8 against ~3.2 ms back to back). Off (0) by default everywhere; off
starts no thread and leaves the pipeline unchanged; without CUDA it warns once and does nothing; on, a daemon thread
launches a matmul on its own CUDA stream and stops cleanly on LabPipeline.close()."""
import copy,threading,time
from pathlib import Path
import pytest
import yaml
from core.configuration import load_config
import perception_ml.gpu_keepalive as keepalive_module
from perception_ml.gpu_keepalive import GpuKeepAlive,THREAD_NAME

ROOT=Path(__file__).resolve().parents[1]


def keepalive_threads():return [t for t in threading.enumerate() if t.name==THREAD_NAME]


def wait_until(predicate,timeout=2.):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return True
        time.sleep(.005)
    return predicate()


def test_config_default_is_off_everywhere():
    assert load_config(ROOT/'config/system_config.yaml')['perception']['gpu_keepalive_ms']==0
    for profile in sorted((ROOT/'config/profiles').glob('*.yaml')):
        assert load_config(profile)['perception'].get('gpu_keepalive_ms')==0,profile.name


@pytest.mark.parametrize('bad',[-1,True,'5',float('nan'),float('inf')])
def test_invalid_periods_are_rejected(bad):
    with pytest.raises(ValueError,match='gpu_keepalive_ms'):GpuKeepAlive.from_config({'device':'cuda','perception':{'gpu_keepalive_ms':bad}})


def test_off_returns_none_without_a_warning_or_thread(recwarn):
    before=threading.active_count()
    for perception in ({},{'gpu_keepalive_ms':0},{'gpu_keepalive_ms':0.}):
        assert GpuKeepAlive.from_config({'device':'cuda','perception':perception}) is None
    assert not [w for w in recwarn if 'gpu_keepalive' in str(w.message)]
    assert threading.active_count()==before and not keepalive_threads()


def test_on_without_cuda_is_a_noop_with_one_warning(monkeypatch):
    monkeypatch.setattr(keepalive_module.torch.cuda,'is_available',lambda:False)
    for device in ('cuda','cpu'):
        with pytest.warns(RuntimeWarning,match='gpu_keepalive') as caught:
            assert GpuKeepAlive.from_config({'device':device,'perception':{'gpu_keepalive_ms':20}}) is None
        assert len([w for w in caught if 'gpu_keepalive' in str(w.message)])==1
    assert not keepalive_threads()


def test_start_stop_lifecycle_runs_the_op_off_the_caller_and_leaks_no_thread():
    before=threading.active_count();calls=[]
    keepalive=GpuKeepAlive(2,op=lambda:calls.append(threading.current_thread().name))
    assert not keepalive.running and not keepalive_threads()
    started=time.perf_counter();keepalive.start();assert time.perf_counter()-started<.5
    keepalive.start()  # idempotent: still one thread
    assert keepalive.running and len(keepalive_threads())==1
    assert wait_until(lambda:len(calls)>=3) and set(calls)=={THREAD_NAME}
    assert keepalive_threads()[0].daemon
    keepalive.stop();keepalive.stop()
    assert not keepalive.running and not keepalive_threads() and threading.active_count()==before
    count=len(calls);time.sleep(.02);assert len(calls)==count
    for _ in range(20):keepalive.start();keepalive.stop()
    assert not keepalive_threads() and threading.active_count()==before


def test_a_failing_op_stops_the_thread_and_records_why():
    def broken():raise RuntimeError('device lost')
    keepalive=GpuKeepAlive(1,op=broken);keepalive.start()
    assert wait_until(lambda:not keepalive.running) and not keepalive_threads()
    assert 'device lost' in keepalive.status;keepalive.stop()


def pipeline_parts():
    pytest.importorskip('torch')
    from core.pipeline import LabPipeline,load_commissioned_assets
    from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'));config['device']='cpu'
    twin,static,grid=load_commissioned_assets(ROOT/f"assets/twin_seed{config['seed']}")
    source=SyntheticLabSimulator(config['seed'],config,mobile=False)
    try:frames=[source.next_frame() for _ in range(2)]
    finally:source.close()
    return LabPipeline,config,twin,static,grid,frames


def test_pipeline_with_keepalive_off_starts_no_thread():
    LabPipeline,config,twin,static,grid,frames=pipeline_parts()
    before={t.ident for t in threading.enumerate()}
    pipeline=LabPipeline(copy.deepcopy(config),ROOT/config['perception']['checkpoint'],twin,static,grid)
    try:
        assert pipeline.gpu_keepalive is None
        for frame in frames:pipeline.process(frame)
        assert {t.ident for t in threading.enumerate()}<=before and not keepalive_threads()
    finally:pipeline.close()
    assert {t.ident for t in threading.enumerate()}<=before


def test_pipeline_with_keepalive_on_but_cpu_device_warns_and_starts_nothing():
    LabPipeline,config,twin,static,grid,frames=pipeline_parts()
    config=copy.deepcopy(config);config['perception']['gpu_keepalive_ms']=20
    with pytest.warns(RuntimeWarning,match='gpu_keepalive'):
        pipeline=LabPipeline(config,ROOT/config['perception']['checkpoint'],twin,static,grid)
    try:assert pipeline.gpu_keepalive is None and not keepalive_threads()
    finally:pipeline.close()


def test_pipeline_close_stops_a_running_keepalive(monkeypatch):
    LabPipeline,config,twin,static,grid,frames=pipeline_parts()
    import core.pipeline as pipeline_module
    calls=[];made=[]
    def fake(cfg):
        k=GpuKeepAlive(cfg['perception']['gpu_keepalive_ms'],op=lambda:calls.append(1));made.append(k);return k
    monkeypatch.setattr(pipeline_module.GpuKeepAlive,'from_config',staticmethod(fake))
    config=copy.deepcopy(config);config['perception']['gpu_keepalive_ms']=2
    pipeline=LabPipeline(config,ROOT/config['perception']['checkpoint'],twin,static,grid)
    try:
        assert pipeline.gpu_keepalive is made[0] and pipeline.gpu_keepalive.running and len(keepalive_threads())==1
        for frame in frames:pipeline.process(frame)
        assert wait_until(lambda:len(calls)>=2)
    finally:pipeline.close()
    assert not made[0].running and not keepalive_threads()
    pipeline.close()  # idempotent


@pytest.mark.skipif(not keepalive_module.torch.cuda.is_available(),reason='needs a CUDA device (run with .venv-gpu)')
def test_real_cuda_keepalive_runs_on_its_own_stream_and_stops():
    torch=keepalive_module.torch
    keepalive=GpuKeepAlive.from_config({'device':'cuda','perception':{'gpu_keepalive_ms':5,'gpu_keepalive_matmul':256}})
    assert keepalive is not None and keepalive.status=='configured'
    keepalive.start()
    try:
        assert wait_until(lambda:keepalive.launches>=3)
        assert keepalive.stream is not None and keepalive.stream!=torch.cuda.current_stream()
    finally:keepalive.stop()
    assert not keepalive.running and not keepalive_threads()

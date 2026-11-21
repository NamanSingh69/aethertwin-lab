"""AIDE2 C13: the replay rehearsal (2026-11-05) stopped for good after one IMU gap or one bad frame."""
import copy
from dataclasses import replace
from pathlib import Path
import numpy as np
import pytest
import yaml
from core.contracts import IMUSample

ROOT=Path(__file__).resolve().parents[1]


def shifted(frame,dt):
    return replace(frame,timestamp=frame.timestamp+dt,camera_timestamp=None if frame.camera_timestamp is None else frame.camera_timestamp+dt,
                   imu_samples=tuple(IMUSample(s.timestamp+dt,s.gyro,s.accel) for s in frame.imu_samples),
                   rplidar_timestamp=None if frame.rplidar_timestamp is None else frame.rplidar_timestamp+dt)


def test_an_imu_gap_reseeds_deskew_from_the_last_pose_instead_of_failing_every_later_frame():
    torch=pytest.importorskip('torch')
    from core.pipeline import LabPipeline,load_commissioned_assets
    from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'));config['device']='cpu'
    twin,static,grid=load_commissioned_assets(ROOT/f"assets/twin_seed{config['seed']}")
    source=SyntheticLabSimulator(config['seed'],config,mobile=False)
    try:frames=[source.next_frame() for _ in range(5)]
    finally:source.close()
    # A 6 s stall: the frames after it carry no IMU for the interval since the last processed sweep.
    frames=frames[:2]+[shifted(f,6.) for f in frames[2:]]
    threads=torch.get_num_threads();torch.set_num_threads(min(2,threads))
    pipeline=LabPipeline(copy.deepcopy(config),ROOT/config['perception']['checkpoint'],twin,static,grid)
    try:states=[pipeline.process(f) for f in frames]
    finally:pipeline.close();torch.set_num_threads(threads)
    assert states[2].diagnostics['deskew_reseeded']=='imu_gap' and pipeline.deskew_reseeds==1
    assert 'deskew_reseeded' not in states[3].diagnostics
    for state in states[1:]:np.testing.assert_allclose(state.sensor_pose_world,states[0].sensor_pose_world,atol=1e-9)


def test_the_live_loop_skips_failing_frames_and_stops_only_after_many_in_a_row():
    from command_center.runtime import FrameErrorPolicy
    policy=FrameErrorPolicy(max_consecutive=3)
    assert policy.failed(ValueError('Static-map localization lost')) is False
    assert policy.failed(ValueError('again')) is False
    policy.succeeded();assert policy.consecutive==0 and policy.total==2
    for _ in range(2):assert policy.failed(RuntimeError('x')) is False
    assert policy.failed(RuntimeError('third in a row')) is True
    status=policy.status();assert status['total_frame_errors']==5 and status['last_frame_error']=='RuntimeError: third in a row'


def test_a_starved_synchronizer_retries_a_few_windows_before_giving_up():
    import threading,time
    from ingestion.remote_source import RemoteSensorSource
    class Client:
        fatal=None;faults={};state='streaming'
        def status(self):return {'state':'streaming','segment':1,'last_error':None,'outage_seconds':0}
    class Sync:
        def __init__(self,ready_after):self.calls=0;self.ready_after=ready_after
        def synchronize(self):
            self.calls+=1;return 'frame' if time.monotonic()>=self.ready_after else None
    class Lidar:
        def next_frame(self):time.sleep(.02);raise TimeoutError('no sweep yet')
    def source(ready_in,retries):
        s=object.__new__(RemoteSensorSource);s.client=Client();s.lock=threading.Lock();s.sync_segment=s.consumer_segment=0
        s.sync=Sync(time.monotonic()+ready_in);s.lidar=Lidar();s.sync_timeout=.1;s.max_outage=10.;s.sync_retries=retries;s.sync_timeouts=0
        s._finish=lambda result:result;return s
    # The old code raised after the first window; the exact count of retried windows depends on scheduling.
    starved=source(.25,3);assert next(starved)=='frame' and 1<=starved.sync_timeouts<=3
    with pytest.raises(TimeoutError,match='Cannot synchronize'):next(source(5.,1))


def test_a_dead_live_acquisition_is_reopened_with_backoff_and_a_cap():
    from command_center.runtime import LiveSourceReopener
    class Source:
        def __init__(self,terminal=False):self.terminal=terminal;self.closed=False
        def close(self):self.closed=True
    attempts=[];fail={'n':1}
    def open_source():
        attempts.append(1)
        if fail['n']:fail['n']-=1;raise RuntimeError('Raw-sensor transport unavailable')
        return Source()
    reopener=LiveSourceReopener(open_source,max_reopens=2,backoff_seconds=5.)
    alive=Source();assert reopener.maybe_reopen(alive,now=0.) is None and not attempts  # a live source is left alone
    dead=Source(terminal=True)
    assert reopener.maybe_reopen(dead,now=0.) is None and dead.closed and len(attempts)==1  # open failed: back off
    assert reopener.maybe_reopen(dead,now=3.) is None and len(attempts)==1                   # still backing off
    fresh=reopener.maybe_reopen(dead,now=5.5);assert isinstance(fresh,Source) and reopener.reopens==1
    assert reopener.maybe_reopen(Source(terminal=True),now=20.) is not None and reopener.reopens==2
    assert reopener.maybe_reopen(Source(terminal=True),now=40.) is None and reopener.exhausted    # cap reached
    assert reopener.status()['live_source_reopens']==2 and 'unavailable' in reopener.status()['last_reopen_error']

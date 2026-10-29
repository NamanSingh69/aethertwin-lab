import threading
import time
import numpy as np
import pytest
from core.async_recording import AsyncRecorder
from core.bounded_acquisition import frame_bytes
from core.contracts import MultiModalSensorFrame
from ingestion.recorder_replayer import FrameRecorder,ReplaySource


def test_async_recording_preserves_raw_observations_and_flushes_on_close(tmp_path):
    writer=AsyncRecorder(FrameRecorder(tmp_path/'recorded',chunk_size=2),max_frames=8)
    frames=[MultiModalSensorFrame(i,i*.1,np.array([[1+i,0,.5,1,1,1,0]],float)) for i in range(5)]
    for frame in frames:assert writer.write(frame)
    writer.close();replayed=list(ReplaySource(tmp_path/'recorded'))
    assert len(replayed)==5 and writer.status()['complete']
    for before,after in zip(frames,replayed):np.testing.assert_array_equal(before.lidar_points,after.lidar_points)


def test_slow_disk_does_not_block_capture_or_hide_an_incomplete_recording():
    entered=threading.Event();release=threading.Event()
    class SlowSink:
        def __init__(self):self.ids=[]
        def write(self,frame):entered.set();release.wait(2);self.ids.append(frame.frame_id)
        def close(self):pass
    sink=SlowSink();frame=MultiModalSensorFrame(0,0,np.empty((0,7)))
    writer=AsyncRecorder(sink,max_frames=2,max_bytes=2*frame_bytes(frame))
    assert writer.write(frame) and entered.wait(2)
    assert writer.write(frame)
    started=time.monotonic();assert writer.write(frame) is False
    assert time.monotonic()-started<.1
    report=writer.status();assert not report['complete'] and report['dropped_frames']==1
    assert report['queued_bytes']+report['inflight_bytes']<=report['capacity_bytes']
    release.set()
    with pytest.raises(RuntimeError,match='incomplete'):writer.close()
    assert sink.ids==[0,0]


def test_incomplete_recording_evidence_persists_and_requires_explicit_recovery(tmp_path):
    import json
    sink=FrameRecorder(tmp_path/'partial',chunk_size=1)
    entered=threading.Event();release=threading.Event();original=sink.write
    def slow(frame):entered.set();release.wait(2);original(frame)
    sink.write=slow
    writer=AsyncRecorder(sink,max_frames=1)
    frame=MultiModalSensorFrame(0,0,np.array([[1,0,.5,1,1,1,0.]]))
    assert writer.write(frame) and entered.wait(2)
    assert not writer.write(frame)
    release.set()
    with pytest.raises(RuntimeError,match='incomplete'):writer.close()
    status=json.loads((sink.path/'recording_status.json').read_text())
    assert not status['complete'] and status['dropped_frames']==1
    with pytest.raises(ValueError,match='incomplete'):ReplaySource(sink.path)
    assert len(list(ReplaySource(sink.path,allow_incomplete=True)))==1


def test_replay_verifies_a_chunk_digest_before_decoding(tmp_path):
    import json
    with FrameRecorder(tmp_path/'recorded',chunk_size=1) as sink:
        sink.write(MultiModalSensorFrame(0,0,np.empty((0,7))))
    manifest=tmp_path/'recorded/manifest.json'
    data=json.loads(manifest.read_text());data['chunks'][0]['sha256']='0'*64
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='checksum'):list(ReplaySource(manifest.parent))

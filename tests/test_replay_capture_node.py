"""tools/replay_capture_node.py over loopback: a tiny recording made through the real transport is served back through it."""
import json,time
import numpy as np
import pytest

from ingestion.capture_node import CaptureServer,SyntheticDevices
from ingestion.recorder_replayer import FrameRecorder
from ingestion.remote_source import RemoteSensorSource
from tools.replay_capture_node import ReplayDevices,wire_points,depth_raw

KEY=bytes(range(32));W,H=64,40


@pytest.fixture
def calibration(tmp_path):
    path=tmp_path/'calibration.json'
    path.write_text(json.dumps({'calibration_id':'replay-test','measured':True,'image_size':[W,H],
        'K':[[50.,0,W/2],[0,50.,H/2],[0,0,1]],'distortion':[0]*5,'T_lidar_camera':np.eye(4).tolist()}))
    return path


def source(port,calibration,**extra):
    config={'capture_host':'127.0.0.1','capture_port':port,'calibration':str(calibration),'allow_synthetic_capture':True,
            'capture_ping_interval_seconds':.1,'capture_initial_wait_seconds':5.,'capture_stall_seconds':2.,**extra}
    return RemoteSensorSource(config,key=KEY)


@pytest.fixture(scope='module')
def recording(tmp_path_factory):
    """Eight synchronised frames recorded from a synthetic node through the transport (stands in for a rig recording)."""
    tmp=tmp_path_factory.mktemp('replay');cal=tmp/'cal.json'
    cal.write_text(json.dumps({'calibration_id':'replay-test','measured':True,'image_size':[W,H],'K':[[50.,0,W/2],[0,50.,H/2],[0,0,1]],
                               'distortion':[0]*5,'T_lidar_camera':np.eye(4).tolist()}))
    server=CaptureServer(SyntheticDevices(seed=3,width=W,height=H,points=200),KEY,bind='127.0.0.1',port=0,allowed_peers=['127.0.0.1'],codecs={'depth':'zlib'}).start()
    src=source(server.address[1],cal)
    try:frames=[next(src) for _ in range(8)]
    finally:src.close();server.close()
    with FrameRecorder(tmp/'rec',chunk_size=3) as recorder:
        for frame in frames:recorder.write(frame)
    return tmp/'rec',frames


def serve(path,calibration,**kwargs):
    data=None if calibration is None else json.loads(calibration.read_text())
    devices=ReplayDevices(path,calibration=data,lead_seconds=.3,**kwargs)
    server=CaptureServer(devices,KEY,bind='127.0.0.1',port=0,allowed_peers=['127.0.0.1'],calibration_path=calibration,
                         codecs={'depth':'zlib','color':'raw'}).start()
    return server,devices


def test_wire_conversions_invert_the_recorded_columns():
    points=np.array([[1,2,3,.5,1,1,.04]],np.float32);assert np.allclose(wire_points(points),[[1,2,3,127.5,.04,0]])
    assert depth_raw(np.array([[0.,1.234,np.nan,70.]])).tolist()==[[0,1234,0,65535]]


def test_replay_serves_the_recording_through_the_transport_and_loops_monotonically(recording,calibration):
    path,recorded=recording;server,devices=serve(path,calibration,max_frames=5);src=source(server.address[1],calibration)
    try:
        identity=src.client.identity['devices']
        assert identity['synthetic'] is True and identity['bringup'] is False and 'not a lab result' in identity['replay']['label']
        got=[next(src) for _ in range(7)]
    finally:src.close();server.close()
    stamps=[f.timestamp for f in got];assert all(b>a for a,b in zip(stamps,stamps[1:]))
    assert devices.counts['passes']>=1 and devices.error is None
    by_points={f.lidar_points[:,:3].tobytes():f for f in recorded}
    matched=[by_points.get(f.lidar_points[:,:3].tobytes()) for f in got]
    assert sum(m is not None for m in matched)>=5
    for frame,original in zip(got,matched):
        assert frame.calibration.calibration_id=='replay-test' and len(frame.imu_samples)>=2
        if original is None:continue
        assert np.allclose(frame.lidar_points[:,6],original.lidar_points[:,6]) and np.allclose(frame.lidar_points[:,3],original.lidar_points[:,3],atol=1e-6)
        assert abs(frame.sweep_duration-original.sweep_duration)<1e-6
        # Lossless codecs: the replayed camera frame is the recorded one (depth at 1 mm).
        if abs((frame.camera_timestamp-frame.timestamp)-(original.camera_timestamp-original.timestamp))<1e-3:
            assert np.array_equal(frame.rgb,original.rgb) and np.allclose(frame.depth,original.depth,atol=6e-4)
    status=src.status();assert status['imu_sequence_breaks']==0 and status['clock_jumps']==0 and status['segments']==1


def test_end_of_a_single_pass_is_an_explicit_fault_and_modes_must_match(recording,calibration):
    path,_=recording;server,devices=serve(path,calibration,loop=False,max_frames=3);src=source(server.address[1],calibration)
    try:
        with pytest.raises(RuntimeError,match='ended after 1 pass'):
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:next(src)
    finally:src.close();server.close()
    # Without a calibration the replay node announces bring-up, which a calibrated host refuses.
    server,_=serve(path,None,max_frames=3)
    try:
        with pytest.raises(RuntimeError,match='bring-up'):source(server.address[1],calibration,capture_reconnect=False)
    finally:server.close()

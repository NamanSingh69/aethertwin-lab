"""Raw-sensor transport over loopback with labelled synthetic devices. No Pi, L2 or Gemini is involved."""
import json,os,socket,threading,time
from dataclasses import replace
import numpy as np
import pytest

from ingestion import raw_transport as rt
from ingestion.capture_node import CaptureServer,Outbox,SyntheticDevices,HardwareDevices,DeviceSink
from ingestion.remote_source import RemoteSensorSource,IdentityMismatch
from ingestion.gemini_333l_driver import depth_meters
from ingestion.recorder_replayer import FrameRecorder,ReplaySource
from core.bounded_acquisition import BufferedAcquisition

KEY=bytes(range(32));W,H=160,100


def wait_for(condition,seconds=8.):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if condition():return True
        time.sleep(.01)
    return False


@pytest.fixture
def calibration(tmp_path):
    path=tmp_path/'calibration.json'
    path.write_text(json.dumps({'calibration_id':'loopback-test','measured':True,'image_size':[W,H],
        'K':[[120.,0,W/2],[0,120.,H/2],[0,0,1]],'distortion':[0]*5,'T_lidar_camera':np.eye(4).tolist(),'T_lidar_imu':np.eye(4).tolist()}))
    return path


class Recorder:
    """Wraps the node sink to remember what was sent, keyed by camera device time."""
    def __init__(self,sink):
        self.sink=sink;self.sent={};original=sink.camera
        def camera(capture_mono,depth_raw,depth_scale,rgb,metadata,device_color=None):
            # Device colour (synthetic MJPG) is compared with a decode of the very bytes that were sent.
            expected=(rt.decode_color(bytes(device_color['data']),'device',np.shape(depth_raw),device_color['format'])
                      if sink.color_codec=='device' else np.array(rgb))
            self.sent[metadata['camera_device_us']]=(np.array(depth_raw),float(depth_scale),expected)
            return original(capture_mono,depth_raw,depth_scale,rgb,metadata,device_color)
        sink.camera=camera


def node(devices=None,codecs=None,allowed=('127.0.0.1',),**kwargs):
    devices=devices or SyntheticDevices(seed=1,width=W,height=H,points=500)
    server=CaptureServer(devices,KEY,bind='127.0.0.1',port=0,allowed_peers=allowed,codecs=codecs,**kwargs)
    recorder=Recorder(server.sink);server.start();return server,recorder


def source(server,calibration,**extra):
    config={'capture_host':'127.0.0.1','capture_port':server.address[1],'calibration':str(calibration),'allow_synthetic_capture':True,
            'capture_ping_interval_seconds':.1,'capture_initial_wait_seconds':5.,'capture_stall_seconds':1.5,**extra}
    return RemoteSensorSource(config,key=extra.pop('key',KEY))


def frames(src,count):return [next(src) for _ in range(count)]


# Framing, versioning and authentication
def pair():
    a,b=socket.socketpair();return rt.Channel(a),rt.Channel(b)


def test_authenticated_messages_reject_tampering_replay_and_unauthenticated_data():
    a,b=pair();keys=rt.derive_keys(KEY,b'n'*32,b'm'*32);a.authenticate(*keys);b.authenticate(keys[1],keys[0])
    a.send(rt.IMU,{'first_seq':1,'count':0},b'abc');kind,meta,payload=b.receive()
    assert (kind,meta['first_seq'],bytes(payload))==(rt.IMU,1,b'abc')
    parts=a.encode(rt.PING,{'t1':1.});raw=b''.join(bytes(p) for p in parts)
    tampered=bytearray(raw);tampered[-40]^=1;a.sock.sendall(tampered)
    with pytest.raises(rt.AuthenticationError):b.receive()
    a2,b2=pair();a2.authenticate(*keys);b2.authenticate(keys[1],keys[0])
    parts=a2.encode(rt.PING,{'t1':1.});raw=b''.join(bytes(p) for p in parts);a2.sock.sendall(raw);b2.receive();a2.sock.sendall(raw)
    with pytest.raises(rt.TransportError,match='Sequence'):b2.receive()
    c,d=pair()
    with pytest.raises(rt.TransportError,match='authenticated'):c.send(rt.IMU,{})
    c.sock.sendall(rt.LENGTH.pack(rt.HEADER.size)+rt.HEADER.pack(rt.MAGIC,rt.VERSION,rt.IMU,0,1,0,0))
    with pytest.raises(rt.TransportError,match='before authentication'):d.receive()


def test_version_magic_and_length_limits_are_enforced():
    a,b=pair();a.sock.sendall(rt.LENGTH.pack(rt.HEADER.size)+rt.HEADER.pack(rt.MAGIC,rt.VERSION+1,rt.HELLO,0,1,0,0))
    with pytest.raises(rt.TransportError,match='version'):b.receive()
    a,b=pair();a.sock.sendall(rt.LENGTH.pack(rt.HEADER.size)+rt.HEADER.pack(b'XXXX',rt.VERSION,rt.HELLO,0,1,0,0))
    with pytest.raises(rt.TransportError,match='Not an AetherTwin'):b.receive()
    a,b=pair();a.sock.sendall(rt.LENGTH.pack(rt.PREAUTH_MAX+1))
    with pytest.raises(rt.TransportError,match='length'):b.receive()


def test_key_files_are_generated_private_and_validated(tmp_path):
    path=rt.generate_key(tmp_path/'node.key');assert len(rt.load_key(path))==32
    with pytest.raises(FileExistsError):rt.generate_key(path)
    short=tmp_path/'short.key';short.write_text('abcd');short.chmod(0o600)
    with pytest.raises(ValueError,match='at least'):rt.load_key(short)
    if os.name=='posix':
        path.chmod(0o640)
        with pytest.raises(PermissionError,match='chmod 600'):rt.load_key(path)


def test_wrong_key_is_rejected_on_both_sides_and_not_retried(calibration):
    server,_=node()
    try:
        with pytest.raises(RuntimeError,match='authentication'):source(server,calibration,key=bytes(32))
        assert wait_for(lambda:server.counts['auth_failures']==1)
    finally:server.close()


def test_unlisted_peer_and_wildcard_bind_are_refused(calibration):
    server,_=node(allowed=('192.0.2.7',))
    try:
        with pytest.raises(RuntimeError,match='unavailable'):source(server,calibration,capture_reconnect=False)
        assert server.counts['rejected_peers']>=1 and server.session is None
    finally:server.close()
    with pytest.raises(ValueError,match='never to all interfaces'):CaptureServer(SyntheticDevices(),KEY,bind='0.0.0.0',port=0)


def test_synthetic_node_is_refused_unless_allowed_and_calibration_must_match(calibration,tmp_path):
    server,_=node()
    try:
        with pytest.raises(RuntimeError,match='synthetic'):source(server,calibration,allow_synthetic_capture=False)
    finally:server.close()
    src=object.__new__(RemoteSensorSource);src.allow_synthetic=False;src.bringup=False;src.calibration_identity=rt.file_identity(calibration)
    identity={'protocol':rt.PROTOCOL,'protocol_version':rt.VERSION,'devices':{'synthetic':False},'calibration':{'sha256':'0'*64}}
    with pytest.raises(IdentityMismatch,match='Calibration differs'):src._verify(identity)
    identity['calibration']=rt.file_identity(calibration);src._verify(identity)


# Clock mapping
def test_peer_clock_uses_the_lowest_delay_exchange_and_slews_without_steps():
    clock=rt.PeerClock(window=4,slew=1e-3,jump=.005)
    clock.add(0.,10.0005,10.0006,.002);clock.add(1.,11.003,11.0031,1.008)  # second exchange is slow and asymmetric
    assert clock.target()==pytest.approx(10.,abs=1e-3);clock.anchor(0.)
    assert clock.to_host(15.)==pytest.approx(5.,abs=1e-3)
    anchored=clock.applied
    clock.add(2.,12.002,12.0021,2.0002)  # lowest delay now says 10.0019; the applied offset may move only 1 ms per second
    assert clock.update(1.)==pytest.approx(anchored+1e-3) and clock.update(1.5)==pytest.approx(anchored+1.5e-3)
    assert clock.update(10.)==pytest.approx(clock.target())
    with pytest.raises(rt.TransportError):clock.add(2.,1.,0.,3.)


# Outbox policy (node side, no sockets)
def test_outbox_priorities_fragment_interleaving_and_counted_drops():
    box=Outbox(imu_capacity=3,cloud_capacity=1,camera_capacity=1,fragment_bytes=4);owner=object();box.start(owner)
    box.put_camera({'capture_mono':1.},b'0123456789');box.put_camera({'capture_mono':2.},b'abcdefghij')  # first dropped
    box.put_cloud({'frame_id':1},np.zeros((2,6)));box.put_cloud({'frame_id':2},np.zeros((2,6)))  # first dropped
    for i in range(5):box.put_imu(np.full(8,i))  # two oldest dropped -> one coalesced GAP
    kinds=[];message=box.next_message(owner)
    assert message[0]==rt.GAP and message[1]['count']==2 and message[1]['first_missing_seq']==1
    message=box.next_message(owner);assert message[0]==rt.IMU and message[1]=={'first_seq':3,'count':3}
    message=box.next_message(owner);assert message[0]==rt.CLOUD and message[1]['frame_id']==2
    message=box.next_message(owner);assert message[0]==rt.CAMERA and message[1]['index']==0 and message[1]['count']==3 and bytes(message[2])==b'abcd'
    box.put_imu(np.zeros(8))  # arrives mid-frame: sent before the next fragment
    assert box.next_message(owner)[0]==rt.IMU
    rest=[box.next_message(owner) for _ in range(2)];assert b''.join(bytes(m[2]) for m in rest)==b'efghij'
    assert box.next_message(owner,timeout=.01) is None and box.next_message(object(),timeout=.01) is None
    s=box.status();assert (s['camera_dropped'],s['clouds_dropped'],s['imu_dropped'],s['imu_gaps'])==(1,1,2,1)


def test_codecs_are_lossless_except_jpeg():
    rng=np.random.default_rng(3);raw=rng.integers(0,65535,(H,W),dtype=np.uint16);rgb=rng.integers(0,255,(H,W,3),dtype=np.uint8)
    for codec in rt.DEPTH_CODECS:assert np.array_equal(rt.decode_depth(rt.encode_depth(raw,codec),codec,raw.shape),raw)
    for codec in ('raw','png'):assert np.array_equal(rt.decode_color(rt.encode_color(rgb,codec),codec,rgb.shape[:2]),rgb)
    assert np.array_equal(rt.decode_color(rgb.tobytes(),'device',rgb.shape[:2],'RGB'),rgb)
    smooth=np.repeat(np.linspace(0,255,W,dtype=np.uint8)[None,:,None],H,0).repeat(3,2)
    assert np.abs(rt.decode_color(rt.encode_color(smooth,'jpeg',95),'jpeg',smooth.shape[:2]).astype(int)-smooth).max()<12
    with pytest.raises(rt.TransportError):rt.decode_depth(rt.encode_depth(raw,'zlib')[:-3],'zlib',raw.shape)


# End to end over loopback
@pytest.mark.parametrize('codecs',[{'depth':'png','color':'device'},{'depth':'zlib','color':'png'},{'depth':'raw','color':'raw'}])
def test_loopback_frames_are_complete_synchronised_and_bit_exact(calibration,codecs):
    server,sent=node(codecs=codecs);src=source(server,calibration)
    try:
        output=frames(src,6)
        for frame in output:
            raw,scale,rgb=sent.sent[frame.raw_timestamps['camera_device_us']]
            assert np.array_equal(frame.depth,depth_meters(raw,scale)) and frame.depth.dtype==np.float32
            assert np.array_equal(frame.rgb,rgb)
            times=[s.timestamp for s in frame.imu_samples];end=frame.timestamp+frame.sweep_duration
            assert times==sorted(times) and times[0]<=frame.timestamp+1e-6 and times[-1]>=end-1e-6
            assert abs(frame.camera_timestamp-end)<=.02 and frame.calibration.calibration_id=='loopback-test'
            assert frame.diagnostics['acquisition_segment']==1 and frame.diagnostics['raw_transport']['state']=='streaming'
            assert 'Pi capture node' in frame.diagnostics['transport']
        ids=[f.frame_id for f in output];assert ids==sorted(ids)
        status=src.status();assert status['clock']['error_bound_seconds']<.01 and status['imu_sequence_breaks']==0
        assert status['camera_incomplete']==0 and status['decode_errors']==0
    finally:src.close();server.close()


def test_sweep_forwarding_over_loopback_keeps_every_sweep_fused(calibration):
    """camera_forward: sweep (hotspot profile) sends about one camera frame per L2 sweep and loses no fused frame."""
    server,_=node(codecs={'depth':'zlib','color':'jpeg','camera_forward':'sweep'});src=source(server,calibration)
    try:
        output=frames(src,15);ids=[f.frame_id for f in output[3:]]
        assert ids==list(range(ids[0],ids[0]+len(ids)))  # after the first sweeps, no sweep goes unfused
        assert all(abs(f.camera_timestamp-(f.timestamp+f.sweep_duration))<=.02 for f in output)
        status=src.status();sent=server.outbox.counts['camera_frames'];skipped=server.sink.camera_skipped
        assert skipped>0 and sent/(sent+skipped)<.6 and status['camera_missing']==0 and status['decode_errors']==0
        assert server.identity()['codecs']['camera_forward']=='sweep'
    finally:src.close();server.close()


def test_reconnect_starts_a_new_segment_without_splicing(calibration):
    server,_=node();src=source(server,calibration)
    try:
        before=frames(src,3);last_end=before[-1].timestamp+before[-1].sweep_duration
        server.session.close('test drops the link');outage=time.monotonic()
        after=next(src)
        while after.diagnostics['acquisition_segment']==1:after=next(src)
        assert after.diagnostics['acquisition_segment']==2 and after.diagnostics['segment_start']
        assert all(s.timestamp>last_end for s in after.imu_samples) and after.timestamp>last_end
        assert src.status()['connections']==2 and 'reconnected' in src.status()['segment_reasons'][-1]['reason']
        assert not next(src).diagnostics['segment_start']
    finally:src.close();server.close()


def test_imu_sequence_break_and_clock_jumps_start_new_segments(calibration):
    shift=[0.];devices=SyntheticDevices(seed=2,width=W,height=H,points=300)
    server,_=node(devices=devices,clock=lambda:time.monotonic()+shift[0]);src=source(server,calibration)
    try:
        frames(src,2)
        with server.outbox.cv:server.outbox.imu_seq+=7  # seven IMU samples vanish without a GAP
        assert wait_for(lambda:src.status()['imu_sequence_breaks']==1)
        assert src.status()['imu_missing_samples']==7 and src.status()['segment']==2
        segment=src.status()['segment']
        shift[0]=devices.clock_shift=.5  # the node clock steps forward by 0.5 s
        assert wait_for(lambda:src.status()['clock_jumps']>=1 and src.status()['segment']>segment)
        assert wait_for(lambda:src.status()['state']=='streaming')
        frame=next(src)
        while frame.diagnostics['acquisition_segment']<=segment:frame=next(src)
        assert abs(frame.timestamp+frame.sweep_duration-time.monotonic())<1.  # re-anchored, not 0.5 s in the future
    finally:src.close();server.close()


def test_missing_camera_is_an_explicit_fault(calibration):
    devices=SyntheticDevices(seed=4,width=W,height=H,points=300);devices.fail_camera_after=8
    server,_=node(devices=devices);src=source(server,calibration)
    try:
        with pytest.raises(RuntimeError,match='camera acquisition failed'):
            for _ in range(100):next(src)
    finally:src.close();server.close()


def test_slow_consumer_memory_is_bounded_and_overflow_is_a_new_segment(calibration):
    devices=SyntheticDevices(seed=5,imu_hz=400,width=W,height=H,points=300)
    server,_=node(devices=devices);src=source(server,calibration,capture_receiver_packets=300)
    try:
        frames(src,1);time.sleep(2.)  # consumer stalls
        status=src.status();assert status['receiver_overflows']>=1 and src.reader.depth()<=300
        assert len(src.sync.camera)<=src.sync.camera.maxlen
        frame=next(src)
        while not frame.diagnostics['segment_start']:frame=next(src)
        assert frame.diagnostics['acquisition_segment']>1
    finally:src.close();server.close()


def test_stalled_link_is_detected_and_recovered(calibration):
    server,_=node();src=source(server,calibration,capture_stall_seconds=.6)
    try:
        frames(src,1)
        with server.outbox.cv:server.outbox.active=False  # node stops sending anything, including PONGs
        assert wait_for(lambda:src.status()['stalls']>=1)
        assert wait_for(lambda:src.status()['state']=='streaming' and src.status()['connections']>=2)
        frames(src,1)
    finally:src.close();server.close()


def test_record_then_replay_is_identical(calibration,tmp_path):
    server,_=node();src=source(server,calibration)
    try:recorded=frames(src,5)
    finally:src.close();server.close()
    with FrameRecorder(tmp_path/'rec',chunk_size=2) as recorder:
        for frame in recorded:recorder.write(frame)
    replay=ReplaySource(tmp_path/'rec')
    try:replayed=list(replay)
    finally:replay.close()
    assert len(replayed)==len(recorded)
    for a,b in zip(recorded,replayed):
        for name in ('lidar_points','rgb','depth','imu_to_lidar','sensor_pose_world'):assert np.array_equal(getattr(a,name),getattr(b,name)),name
        assert [(s.timestamp,*s.gyro,*s.accel) for s in a.imu_samples]==[(s.timestamp,*s.gyro,*s.accel) for s in b.imu_samples]
        assert (a.timestamp,a.camera_timestamp,a.frame_id)==(b.timestamp,b.camera_timestamp,b.frame_id)
        assert b.diagnostics['acquisition_segment']==1


def test_buffered_acquisition_never_carries_imu_or_frames_across_segments():
    from core.contracts import MultiModalSensorFrame,IMUSample
    def frame(i,segment):
        start=i*.1;imu=tuple(IMUSample(start+k*.025,np.zeros(3),np.zeros(3)) for k in range(5))
        return MultiModalSensorFrame(i,start,np.zeros((4,7),np.float32),imu,diagnostics={'acquisition_segment':segment})
    class Slow:
        def __init__(self):self.items=[frame(0,1),frame(1,1),frame(2,1),frame(3,2)]
        def __iter__(self):return iter(self.items)
        def close(self):pass
    acquisition=BufferedAcquisition(Slow(),max_frames=8).start()
    try:
        assert wait_for(lambda:acquisition.status()['acquired']==4)
        out=next(acquisition);assert out.frame_id==3 and min(s.timestamp for s in out.imu_samples)>=.3-1e-9
        status=acquisition.status();assert status['segment_changes']==1 and status['dropped_at_segment_change']==3
    finally:acquisition.close()


# Hardware adapter with fake SDK objects (no devices)
class FakeReader:
    def __init__(self,packets):self.packets=list(packets);self.closed=False
    def poll(self):return self.packets.pop(0) if self.packets else None
    def close(self):self.closed=True


class FakeCamera:
    profile_metadata={'device':{'serial':'CP0000TEST','firmware':'fake'},'alignment':'hardware'}
    def __init__(self):self.calls=0;self.closed=False
    def read(self):
        self.calls+=1
        if self.calls>2:time.sleep(.05);raise RuntimeError('unplugged')
        raw=np.full((H,W),1000+self.calls,np.uint16);rgb=np.zeros((H,W,3),np.uint8)
        self.last_raw={'depth':raw,'depth_scale':1.,'color':rgb.reshape(-1),'color_format':'RGB'}
        self.last_clock_metadata={'metadata':{'camera_device_us':self.calls}}
        return 100.+self.calls,rgb,depth_meters(raw,1.),None
    def close(self):self.closed=True


def test_hardware_adapter_maps_sdk_packets_and_reports_camera_faults(calibration):
    from ingestion.live_source import load_measured_calibration
    packets=[{'type':'imu','timestamp':1000.,'gyro':np.zeros(3),'accel':np.ones(3)},
             {'type':'cloud','timestamp':1000.05,'frame_id':7,'duration':.1,'points':np.zeros((3,6),np.float32)}]
    reader=FakeReader(packets);camera=FakeCamera()
    # lidar_clock_scale 1.0 keeps the fixed SDK-epoch mapping this test checks (the default fits the device clock).
    devices=HardwareDevices({'calibration':str(calibration),'lidar_clock_scale':1.},reader=reader,camera=camera,calibration=load_measured_calibration({'calibration':str(calibration)}))
    box=Outbox();owner=object();box.start(owner);sink=DeviceSink(box,{'depth':'png','color':'device'})
    devices.start(sink)
    try:
        assert wait_for(lambda:'camera' in sink.faults)
        assert 'unplugged' in sink.faults['camera']['error'] and box.counts['camera_frames']==2
        messages=[];message=box.next_message(owner,timeout=.1)
        while message is not None:messages.append(message);message=box.next_message(owner,timeout=.1)
        imu=next(m for m in messages if m[0]==rt.IMU);row=np.frombuffer(imu[2],'<f8')
        assert row[0]==1000. and row[1]==pytest.approx(1000.+devices.clock_offset)
        cloud=next(m for m in messages if m[0]==rt.CLOUD);assert cloud[1]['frame_id']==7 and cloud[1]['device_timestamp']==1000.05
        assert devices.identity()['camera']['device']['serial']=='CP0000TEST'
    finally:devices.close()
    assert reader.closed and camera.closed


# Review follow-ups: hostile or broken peers, binds, accept errors, silent devices, reorders
def test_impostor_server_and_reflected_messages_are_rejected(calibration):
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
    def impostor():
        sock,_=listener.accept();channel=rt.Channel(sock)
        channel.send(rt.HELLO,{'protocol':rt.PROTOCOL,'version':rt.VERSION,'nonce':'11'*32});channel.receive()
        channel.send(rt.AUTH_OK,{'proof':'22'*32});time.sleep(.5);sock.close()
    threading.Thread(target=impostor,daemon=True).start()
    config={'capture_host':'127.0.0.1','capture_port':listener.getsockname()[1],'calibration':str(calibration),'allow_synthetic_capture':True}
    with pytest.raises(RuntimeError,match='impostor'):RemoteSensorSource(config,key=KEY)
    listener.close()
    a,b=pair();keys=rt.derive_keys(KEY,b'n'*32,b'm'*32);a.authenticate(*keys);b.authenticate(keys[1],keys[0])
    parts=a.encode(rt.PING,{'t1':1.});a.sock.sendall(b''.join(bytes(p) for p in parts))
    raw=b.sock.recv(65536);b.sock.sendall(raw)  # bounce a's own message back to it
    with pytest.raises(rt.AuthenticationError):a.receive()


def test_malformed_metadata_before_authentication_is_a_protocol_error():
    for meta,match in ((b'['*2000+b']'*2000,'object'),(b'{"nonce":',"Malformed"),(bytes([255,254]),"Malformed")):
        a,b=pair()
        a.sock.sendall(rt.LENGTH.pack(rt.HEADER.size+len(meta))+rt.HEADER.pack(rt.MAGIC,rt.VERSION,rt.HELLO,0,1,len(meta),0)+meta)
        with pytest.raises(rt.TransportError,match=match):b.receive()


@pytest.mark.parametrize('bind',['::','::0','0:0:0:0:0:0:0:0',0,'0.0.0.0','8.8.8.8','::ffff:0.0.0.0'])
def test_only_one_private_or_loopback_address_can_be_bound(bind):
    with pytest.raises(ValueError):CaptureServer(SyntheticDevices(),KEY,bind=bind,port=0)


def test_transient_accept_errors_do_not_stop_the_node(calibration):
    server=CaptureServer(SyntheticDevices(seed=6,width=W,height=H,points=300),KEY,bind='127.0.0.1',port=0,allowed_peers=['127.0.0.1'])
    class Flaky:
        def __init__(self,sock):self.sock=sock;self.failures=2
        def accept(self):
            if self.failures:self.failures-=1;raise ConnectionAbortedError('ECONNABORTED injected by a test')
            return self.sock.accept()
        def close(self):self.sock.close()
    server.listener=Flaky(server.listener);server.start();src=source(server,calibration)
    try:
        frames(src,1);assert server.counts['accept_errors']==2 and server.accept_thread.is_alive()
    finally:src.close();server.close()


def test_pong_offset_step_below_the_future_tolerance_is_a_clock_jump(calibration):
    shift=[0.];devices=SyntheticDevices(seed=8,width=W,height=H,points=300)
    server,_=node(devices=devices,clock=lambda:time.monotonic()+shift[0]);src=source(server,calibration)
    try:
        frames(src,2);shift[0]=devices.clock_shift=.025  # 25 ms: inside the 50 ms future tolerance, above the 20 ms jump threshold
        assert wait_for(lambda:src.status()['clock_jumps']>=1 and src.status()['connections']>=2)
        assert 'node clock offset moved' in src.status()['segment_reasons'][-1]['reason']
        frames(src,2)
    finally:src.close();server.close()


def test_small_reorders_are_dropped_and_counted_large_ones_are_clock_jumps():
    from ingestion.remote_source import RawSensorClient,ClockJump
    client=RawSensorClient('127.0.0.1',1,KEY,on_packet=None,on_camera=None,on_segment=lambda *a:None,verify_identity=None)
    client.clock=rt.PeerClock();client.clock.add(0.,0.,0.,0.);client.clock.anchor(0.)
    assert client._check_time('imu',10.,10.01)==10. and client._check_time('imu',9.9995,10.01) is None
    assert client.counts['imu_out_of_order_dropped']==1
    with pytest.raises(ClockJump):client._check_time('imu',9.9,10.01)


def test_silent_lidar_and_unopenable_devices_are_explicit_faults(calibration):
    from ingestion.live_source import load_measured_calibration
    from ingestion.capture_node import FailedDevices
    calibration_record=load_measured_calibration({'calibration':str(calibration)})
    class IdleCamera(FakeCamera):
        def read(self):time.sleep(.05);return super().read() if self.calls<1 else (_ for _ in ()).throw(RuntimeError('stop'))
    devices=HardwareDevices({'calibration':str(calibration)},reader=FakeReader([]),camera=FakeCamera(),calibration=calibration_record,
                            lidar_startup_seconds=.2)
    box=Outbox();box.start(object());sink=DeviceSink(box,{'depth':'raw','color':'device'});devices.start(sink)
    try:assert wait_for(lambda:'lidar' in sink.faults) and 'no L2 packets' in sink.faults['lidar']['error']
    finally:devices.close()
    sink=DeviceSink(Outbox(),{});FailedDevices(RuntimeError('No Orbbec camera is connected')).start(sink)
    assert 'No Orbbec camera' in sink.faults['devices']['error']


def test_a_new_session_counts_what_it_discards():
    box=Outbox();first=object();box.start(first)
    for i in range(3):box.put_imu(np.zeros(8))
    box.put_camera({'capture_mono':1.},b'x');box.start(object())
    assert box.status()['discarded_at_session_start']==4


def test_bringup_capture_records_uncalibrated_frames_that_replay(tmp_path):
    """Calibration-target capture before a calibration exists: record_raw --bringup over loopback."""
    import yaml
    from core.configuration import load_config
    from tools.record_raw import main as record_raw
    key_path=tmp_path/'node.key';key_path.write_text(KEY.hex());key_path.chmod(0o600);server,_=node()
    try:
        config=load_config('config/system_config.yaml')
        config['live'].update(capture_host='127.0.0.1',capture_port=server.address[1],capture_key_file=str(key_path),
                              capture_ping_interval_seconds=.1,camera_width=W,camera_height=H)
        config_path=tmp_path/'lab.yaml';config_path.write_text(yaml.safe_dump(config))
        out=tmp_path/'calib_targets'
        assert record_raw(['--config',str(config_path),'--output',str(out),'--duration','2','--bringup','--allow-synthetic',
                           '--status-seconds','1'])==0
    finally:server.close()
    report=json.loads((out/'capture_report.json').read_text())
    assert report['bringup_uncalibrated'] and report['frames']>=8 and report['recorder']['complete'] and 'SYNTHETIC' in report['label']
    assert report['compression_level']==1
    replay=ReplaySource(out)
    try:frames=list(replay)
    finally:replay.close()
    assert len(frames)==report['frames']
    for frame in frames:
        assert frame.calibration is None and frame.rgb.shape==(H,W,3) and frame.depth.shape==(H,W)
        assert frame.diagnostics['capture_bringup_uncalibrated'] is True and len(frame.imu_samples)>=2


def test_bringup_and_calibrated_modes_must_match(calibration):
    src=object.__new__(RemoteSensorSource);src.allow_synthetic=False;src.bringup=True;src.calibration_identity=None
    with pytest.raises(IdentityMismatch,match='start it with --bringup'):
        src._verify({'protocol':rt.PROTOCOL,'protocol_version':rt.VERSION,'devices':{'synthetic':False,'bringup':False},'calibration':None})
    src._verify({'protocol':rt.PROTOCOL,'protocol_version':rt.VERSION,'devices':{'synthetic':False,'bringup':True},'calibration':None})
    src.bringup=False;src.calibration_identity=rt.file_identity(calibration)
    with pytest.raises(IdentityMismatch,match='bring-up mode'):
        src._verify({'protocol':rt.PROTOCOL,'protocol_version':rt.VERSION,'devices':{'synthetic':False,'bringup':True},'calibration':None})

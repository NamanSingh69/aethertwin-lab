from types import SimpleNamespace
import math,threading,time
import json,shutil,subprocess,sys
from pathlib import Path
import numpy as np
import pytest
from hardware_halo import HaloController,halo_state,encode_packet,decode_packet,crc16,ReceiverState
from hardware_halo.protocol import HELLO,IDENTITY,sequence_newer


def snapshot(now=1000.,latency=100.,people=None,pose=None):
    return SimpleNamespace(frame_id=3,timestamp=-12345.,processed_at_unix=now,source_mode='sim',
        diagnostics={'processing_ms':latency},sensor_pose_world=np.eye(4) if pose is None else pose,
        entities=people or [],geofences=[])


def person(position,track_id=7):
    return SimpleNamespace(semantic_class=2,confidence=.8,position=np.asarray(position),track_id=track_id)


def test_packet_crc_reference_length_fields_corruption_and_sequence_wrap():
    assert crc16(b'123456789')==0x29B1
    state=halo_state(snapshot(people=[person([.5,0,0])]),now=1000.1)
    packet=encode_packet(state,65535);decoded=decode_packet(packet)
    assert len(packet)==16 and decoded['sequence']==65535 and decoded['source']=='sim'
    assert decoded['severity']=='critical' and decoded['source_age_ms']==200
    assert decoded['azimuth_degrees']==0. and decoded['pixels']==24
    for index in range(16):
        wrong=bytearray(packet);wrong[index]^=1
        with pytest.raises(ValueError):decode_packet(wrong)
    assert sequence_newer(0,65535) and not sequence_newer(65535,0)
    assert not sequence_newer(2,2) and not sequence_newer(32768,0)
    assert decode_packet(encode_packet(state,65536))['sequence']==0


def test_rig_orientation_selects_nearest_person_and_front_left_pixel():
    pose=np.eye(4);pose[:3,3]=[4.,2.,1.]
    pose[:3,:3]=np.array([[0,-1,0],[1,0,0],[0,0,1.]])
    # Sensor +X points world +Y; sensor +Y points world -X.
    state=halo_state(snapshot(people=[person([4,4,1],10),person([2.5,2,1],11)],pose=pose),now=1000.)
    assert state['nearest_track_id']==11 and state['distance_m']==pytest.approx(1.5)
    assert state['azimuth_degrees']==pytest.approx(90.) and state['severity']=='warning'
    strength=np.array(state['pixels']).sum(1)
    assert strength.argmax()==6 and state['pixel_count']==24
    front=halo_state(snapshot(people=[person([4,3.5,1])],pose=pose),now=1000.)
    assert front['azimuth_degrees']==pytest.approx(0.) and np.array(front['pixels']).sum(1).argmax()==0


def test_source_age_uses_wall_completion_plus_latency_and_distinguishes_watchdog():
    state=halo_state(snapshot(now=1000.,latency=900.),now=1000.2)
    assert state['source_age_ms']==pytest.approx(1100.) and state['stale'] and not state['watchdog']
    assert state['source']=='sim' and state['state_name']=='stale'
    queued=snapshot(now=1000.,latency=100.);queued.diagnostics['acquisition_age_ms']=900.
    assert halo_state(queued,now=1000.2)['source_age_ms']==pytest.approx(1200.)
    assert halo_state(queued,now=1000.2)['stale'] and not halo_state(queued,now=1000.2)['watchdog']
    expired=halo_state(snapshot(now=1000.,latency=900.),now=1002.1)
    assert expired['watchdog'] and expired['state_name']=='watchdog'
    disconnected=halo_state(None,now=1000.)
    assert disconnected['disconnected'] and not disconnected['watchdog']
    assert disconnected['pixels']!=expired['pixels']!=state['pixels']
    assert not halo_state(snapshot(now=1000.,latency=20.),now=1000.1)['stale']
    override=halo_state(snapshot(),now=1000.,age=2.5)
    assert override['source_age_ms']==2500. and override['watchdog']
    missing=snapshot();missing.processed_at_unix=None
    assert halo_state(missing,now=1000.)['reason']=='missing_wall_clock_freshness'
    invalid=snapshot();invalid.validity={'observations':False}
    assert halo_state(invalid,now=1000.)['stale']
    empty=snapshot();empty.voxel_centers=np.empty((0,3))
    assert halo_state(empty,now=1000.)['reason']=='empty_or_invalid_observations'
    unlocalized=snapshot(people=[person([.5,0,0])]);unlocalized.validity={'observations':True,'localization':False}
    uncertain=halo_state(unlocalized,now=1000.)
    assert uncertain['stale'] and uncertain['reason']=='invalid_localization' and uncertain['azimuth_degrees'] is None


def test_receiver_rejects_duplicate_and_crc_without_refreshing_link_watchdog():
    state=halo_state(snapshot(),now=1000.)
    receiver=ReceiverState(watchdog_ms=250)
    assert receiver.status(0)['watchdog']
    assert receiver.accept(encode_packet(state,65535),0)
    assert receiver.accept(encode_packet(state,0),30)
    assert not receiver.accept(encode_packet(state,65535),100)
    assert not receiver.accept(encode_packet(state,0),150)
    wrong=bytearray(encode_packet(state,1));wrong[8]^=4
    with pytest.raises(ValueError):receiver.accept(wrong,200)
    assert receiver.status(279)['connected'] and receiver.status(280)['watchdog']
    assert receiver.status(30+2**32)['watchdog']  # Expiry cannot clear on clock wrap.
    assert receiver.accept(encode_packet(state,1),2**32-100)
    assert receiver.status(20)['connected'] and receiver.status(150)['watchdog']


class MockSerial:
    def __init__(self,response=IDENTITY+b'\r\n',delay=0.):
        self.response=response;self.writes=[];self.closed=False;self.received=threading.Event();self.delay=delay
    def write(self,data):
        if data!=HELLO:
            self.received.set()
            if self.delay:threading.Event().wait(self.delay)
        self.writes.append(bytes(data));return len(data)
    def read(self,size):
        data=self.response[:size];self.response=self.response[size:];return data
    def close(self):self.closed=True


def test_default_controller_is_virtual_only_and_publish_does_not_block():
    called=[]
    def forbidden(**kwargs):called.append(kwargs);raise AssertionError('Serial must not be probed')
    controller=HaloController(serial_factory=forbidden)
    try:
        start=time.perf_counter();controller.publish(snapshot(now=time.time()))
        assert time.perf_counter()-start<.02
        threading.Event().wait(.12)
        status=controller.status()
        assert not called and not status['enabled'] and not status['connected'] and status['status_hz']==30
        assert status['status_ticks']>=3 and status['virtual']['source']=='sim'
    finally:controller.close()


def test_configured_compatible_receiver_handshake_precedes_all_binary_packets():
    serial=MockSerial(delay=.08);opened=[]
    def factory(**kwargs):opened.append(kwargs);return serial
    controller=HaloController({'enabled':True,'port':'EXPLICIT_TEST_PORT'},serial_factory=factory)
    try:
        controller.publish(snapshot(now=time.time()))
        assert serial.received.wait(.7)
        started=time.perf_counter();controller.publish(snapshot(now=time.time()))
        assert time.perf_counter()-started<.02
        deadline=time.monotonic()+.5
        while time.monotonic()<deadline:
            if any(decode_packet(item)['source']=='sim' for item in serial.writes if item!=HELLO):break
            threading.Event().wait(.01)
        status=controller.status()
        assert len(opened)==1 and opened[0]['port']=='EXPLICIT_TEST_PORT'
        assert serial.writes[0]==HELLO and status['compatible'] and status['connected']
        packets=[item for item in serial.writes if item!=HELLO]
        assert packets and all(len(item)==16 for item in packets)
        assert decode_packet(packets[-1])['source']=='sim' and status['status_ticks']>=3
    finally:controller.close()
    assert serial.closed


def test_incompatible_serial_device_receives_no_telemetry_and_virtual_status_continues():
    serial=MockSerial(b'UNRELATED_DEVICE 7\n')
    controller=HaloController({'enabled':True,'port':'ONLY_THIS_PORT','handshake_timeout_seconds':.1},serial_factory=lambda **kwargs:serial)
    try:
        controller.publish(snapshot(now=time.time()));threading.Event().wait(.2)
        status=controller.status()
        assert serial.closed and not status['connected'] and not status['compatible']
        assert status['last_error'] and all(item==HELLO for item in serial.writes)
        assert status['status_ticks']>=4 and status['virtual']['source']=='sim'
    finally:controller.close()


def test_malformed_snapshot_shows_disconnected_and_status_worker_survives():
    controller=HaloController()
    try:
        controller.publish(snapshot(now=time.time()));threading.Event().wait(.1)
        assert controller.status()['virtual']['source']=='sim'
        bad=snapshot(now=time.time());bad.diagnostics['processing_ms']=None
        controller.publish(bad);threading.Event().wait(.1)
        first=controller.status();threading.Event().wait(.1);second=controller.status()
        assert first['virtual']['state_name']=='disconnected' and first['virtual']['reason']=='invalid_snapshot'
        assert 'TypeError' in first['last_error'] and second['status_ticks']>first['status_ticks']
        controller.publish(snapshot(now=time.time()));threading.Event().wait(.1)
        assert controller.status()['virtual']['source']=='sim'
    finally:controller.close()


def test_stalled_status_worker_stops_serial_packets_so_the_device_watchdog_expires(monkeypatch):
    import hardware_halo.controller as module
    serial=MockSerial();release=threading.Event();real=module.halo_state;calls=[0]
    def stalling(snapshot,**options):
        calls[0]+=1
        if snapshot is not None and calls[0]>5:release.wait(5)
        return real(snapshot,**options)
    monkeypatch.setattr(module,'halo_state',stalling)
    controller=HaloController({'enabled':True,'port':'EXPLICIT_TEST_PORT'},serial_factory=lambda **kwargs:serial)
    try:
        controller.publish(snapshot(now=time.time()));assert serial.received.wait(1)
        threading.Event().wait(.3);sent=controller.status()['packets_sent'];threading.Event().wait(.3)
        assert controller.status()['packets_sent']==sent
    finally:
        release.set();controller.close()


def test_enabled_halo_requires_explicit_port_before_starting_any_worker():
    with pytest.raises(ValueError,match='explicitly'):HaloController({'enabled':True})
    for config in ({'enabled':'false'},{'handshake_timeout_seconds':float('inf')},{'stale_after_ms':float('nan')}):
        with pytest.raises(ValueError):HaloController(config)


def test_actual_firmware_header_matches_host_crc_sequence_and_all_pixel_patterns(tmp_path):
    compiler=shutil.which('g++')
    if compiler is None:pytest.skip('Optional desktop C++ compiler unavailable; firmware compilation has separate evidence')
    root=Path(__file__).resolve().parents[1];source=root/'hardware_halo/firmware/host_protocol_check.cpp'
    executable=tmp_path/('halo_protocol_check.exe' if sys.platform=='win32' else 'halo_protocol_check')
    subprocess.run([compiler,'-O2',str(source),'-o',str(executable)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    states=[halo_state(None,now=1000.),halo_state(snapshot(),now=1000.),halo_state(snapshot(),now=1001.2),halo_state(snapshot(),now=1003.)]
    for degrees in (-180.,-90.,-7.4999,0.,7.4999,90.,179.9999):
        angle=math.radians(degrees);states.append(halo_state(snapshot(people=[person([.5*math.cos(angle),.5*math.sin(angle),0.])]),now=1000.))
    for state in states:
        packet=encode_packet(state,65535)
        result=json.loads(subprocess.check_output([str(executable),packet.hex()],text=True))
        assert result['pixels']==state['pixels'] and result['sequence']==65535
        assert result['crc_reference']==0x29B1 and result['wrap'] and not result['reverse']
    lost_link=json.loads(subprocess.check_output([str(executable),encode_packet(states[0],0).hex(),'link-watchdog'],text=True))
    assert lost_link['pixels'][0]==[64,0,47] and lost_link['pixels']!=states[0]['pixels']
    broken=bytearray(encode_packet(states[-1],1));broken[14]^=1
    assert subprocess.run([str(executable),broken.hex()],stdout=subprocess.PIPE).returncode==2

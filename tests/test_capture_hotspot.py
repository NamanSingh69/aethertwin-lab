"""Wi-Fi hotspot mode: the profile, bind/allow addresses, sweep-aligned camera forwarding and the rendered
systemd unit. Loopback and synthetic only: no Pi, Wi-Fi link, L2 or Gemini is involved."""
import json,os,shutil,socket,subprocess,sys,time
from pathlib import Path
import numpy as np
import pytest

from core.configuration import load_config
from ingestion import capture_node as cn
from ingestion import raw_transport as rt
from ingestion.capture_node import CaptureServer,DeviceSink,Outbox,bind_address,check_bind,source_root
from ingestion.remote_source import RemoteSensorSource

ROOT=Path(__file__).resolve().parents[1];PROFILE=ROOT/'config/profiles/hotspot.yaml'
UNIT=ROOT/'deployment/capture_node/aethertwin-capture.service';BOOTSTRAP=ROOT/'deployment/lab/pi_bootstrap.sh'
KEY=bytes(range(32))
HARDENING=('NoNewPrivileges=yes','ProtectSystem=strict','ProtectHome=read-only','PrivateTmp=yes','ProtectKernelTunables=yes',
           'ProtectKernelModules=yes','ProtectControlGroups=yes','RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK')


def wait_for(condition,seconds=8.):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if condition():return True
        time.sleep(.01)
    return False


def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


# Profile
def test_hotspot_profile_inherits_the_base_and_validates():
    cfg=load_config(PROFILE);node=cfg['capture_node'];live=cfg['live']
    assert node['allowed_peers']==['192.168.137.1'] and node['bind_interface']=='wlan0' and node['bind'] is None
    assert node['key_file']=='config/capture_node.key' and node['imu_queue_samples']==20000  # inherited from system_config
    assert cfg['mapping']['runtime_tiled'] is True  # inherited from laptop.yaml (the command center default)
    assert live['source']=='remote' and live['capture_host'] is None
    # The L2 stays on the Pi's wired port; only the processing link moves to Wi-Fi.
    assert (live['lidar_ip'],live['host_ip'])==('192.168.1.62','192.168.1.2')
    assert (live['camera_width'],live['camera_height'],live['camera_fps'])==(640,400,30)
    assert (live['camera_color_width'],live['camera_color_height'],live['camera_color_fps'])==(640,400,30)
    assert live['calibration']!=load_config(ROOT/'config/system_config.yaml')['live']['calibration']
    sink=DeviceSink(Outbox(),{'depth':node['depth_codec'],'color':node['color_codec'],'color_quality':node['color_quality'],
                              'depth_level':node['depth_level'],'camera_forward':node['camera_forward'],
                              'camera_sweep_window_seconds':node['camera_sweep_window_seconds']})
    assert (sink.depth_codec,sink.color_codec,sink.camera_forward)==('zlib','jpeg','sweep')
    Outbox(node['imu_queue_samples'],node['cloud_queue_sweeps'],node['camera_queue_frames'],node['fragment_bytes'])
    assert bind_address(node,resolve=lambda name:{'wlan0':'192.168.137.37'}[name])=='192.168.137.37'


def test_the_capture_node_follows_extends_and_resolves_paths_from_the_source_tree():
    cfg=cn.load_config(PROFILE)
    assert cfg['capture_node']['allowed_peers']==['192.168.137.1'] and cfg['live']['lidar_port']==6101
    assert source_root(PROFILE)==ROOT and source_root(ROOT/'config/system_config.yaml')==ROOT


# Bind and allow
@pytest.mark.parametrize('address',['192.168.137.37','192.168.137.1','192.168.1.2','127.0.0.1'])
def test_hotspot_and_wired_addresses_are_accepted_for_bind(address):
    assert check_bind(address)==address


@pytest.mark.parametrize('address',[None,'','0.0.0.0','::','8.8.8.8','100.64.0.1'])
def test_wildcard_public_and_missing_bind_addresses_are_refused(address):
    with pytest.raises(ValueError):check_bind(address)


def test_bind_precedence_and_an_interface_without_a_lease():
    node={'bind':'192.168.1.2','bind_interface':'wlan0'};lease=lambda name:'192.168.137.37'
    assert bind_address(node,resolve=lease)=='192.168.137.37'
    assert bind_address(node,bind='192.168.137.50',resolve=lease)=='192.168.137.50'
    assert bind_address({'bind':'192.168.1.2'},resolve=lease)=='192.168.1.2'
    assert bind_address({'bind':'192.168.1.2'},interface='wlan1',resolve=lambda name:'192.168.137.9')=='192.168.137.9'
    assert bind_address({},resolve=lease)=='192.168.1.2'
    with pytest.raises(RuntimeError,match='no IPv4 address'):bind_address(node,resolve=lambda name:None)
    with pytest.raises(ValueError):bind_address(node,resolve=lambda name:'8.8.8.8')


@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='SIOCGIFADDR is Linux-only (the Pi)')
def test_interface_address_lookup_on_linux():
    assert cn.interface_ipv4('lo')=='127.0.0.1' and cn.interface_ipv4('nosuchif0') is None


class IdleDevices:
    def identity(self):return {'synthetic':True}
    def start(self,sink):pass
    def close(self):pass


def test_only_the_hotspot_laptop_is_allowed():
    """With the hotspot peer list a connection from anywhere else (here loopback) is closed before any handshake."""
    peers=load_config(PROFILE)['capture_node']['allowed_peers']
    server=CaptureServer(IdleDevices(),KEY,bind='127.0.0.1',port=0,allowed_peers=peers).start()
    try:
        with socket.create_connection(server.address[:2],timeout=2):
            assert wait_for(lambda:server.counts['rejected_peers']==1)
        assert server.session is None and '127.0.0.1' in server.last_error
    finally:server.close()


def test_receiver_without_a_capture_host_says_how_to_pass_one(tmp_path):
    calibration=tmp_path/'calibration.json'
    calibration.write_text(json.dumps({'calibration_id':'hotspot-test','measured':True,'image_size':[160,100],'K':[[120.,0,80],[0,120.,50],[0,0,1]],
        'distortion':[0]*5,'T_lidar_camera':np.eye(4).tolist(),'T_lidar_imu':np.eye(4).tolist()}))
    with pytest.raises(ValueError,match='--capture-host'):RemoteSensorSource({'capture_host':None,'calibration':str(calibration)},key=KEY)


def run_node(monkeypatch,tmp_path,capsys,leases,args=()):
    key=tmp_path/'node.key';key.write_text(KEY.hex());key.chmod(0o600);answers=iter(leases)
    monkeypatch.setattr(cn,'interface_ipv4',lambda name:next(answers,leases[-1]));monkeypatch.setattr(cn,'LEASE_CHECK_SECONDS',.5)
    code=cn.main(['--config',str(PROFILE),'--synthetic','--key-file',str(key),'--port',str(free_port()),'--status-interval','60',*args])
    return code,[json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{')]


def test_node_binds_the_interface_lease_and_exits_when_it_changes(monkeypatch,tmp_path,capsys):
    code,events=run_node(monkeypatch,tmp_path,capsys,['127.0.0.1','127.0.0.1','127.0.0.2'],['--duration','10'])
    assert code==5 and events[0]['event']=='listening' and events[0]['address'][0]=='127.0.0.1' and events[0]['interface']=='wlan0'
    assert events[-1]=={'event':'address_changed','interface':'wlan0','bound':'127.0.0.1','now':'127.0.0.2'}


def test_node_without_a_lease_exits_for_a_systemd_retry(monkeypatch,tmp_path,capsys):
    code,events=run_node(monkeypatch,tmp_path,capsys,[None])
    assert code==6 and events==[{'event':'bind_failed','interface':'wlan0','error':events[0]['error']}] and 'no IPv4' in events[0]['error']


def test_an_explicit_bind_skips_the_interface(monkeypatch,tmp_path,capsys):
    code,events=run_node(monkeypatch,tmp_path,capsys,[None],['--bind','127.0.0.1','--duration','1'])
    assert code==0 and events[0]['address'][0]=='127.0.0.1' and events[0]['interface'] is None


# Sweep-aligned camera forwarding
def test_sweep_forwarding_sends_only_frames_near_predicted_sweep_ends():
    box=Outbox();box.start(object());sink=DeviceSink(box,{'depth':'raw','color':'raw','camera_forward':'sweep'})
    depth=np.zeros((4,6),np.uint16);rgb=np.zeros((4,6,3),np.uint8);swept=[0]
    def frame(t):
        # Sweeps of 10 Hz (ends n*0.1 + 0.0999) reach the sink once they have ended, as from the L2.
        while swept[0]*.1+.0999<=t:sink.cloud(swept[0],swept[0]*.1,swept[0]*.1,.0999,np.zeros((1,6),np.float32));swept[0]+=1
        before=box.counts['camera_frames'];sink.camera(t,depth,1.,rgb,{});return box.counts['camera_frames']>before
    times=[round(k/30+.004,4) for k in range(30)];sent=[frame(t) for t in times]
    assert all(sent[:9])  # fewer than three sweeps seen: no prediction yet, everything goes
    # From then on only the frame 4.5 ms after each predicted end (0.2999, 0.3999, ...) is within 20 ms.
    assert [t for t,s in zip(times[9:],sent[9:]) if s]==[.304,.404,.504,.604,.704,.804,.904] and sink.camera_skipped==21-7
    sink.camera(1.5,depth,1.,rgb,{});assert box.counts['camera_frames']==9+7+1  # the L2 went quiet: stop predicting, send all
    assert DeviceSink(Outbox(),{}).fusable(123.456) and sink.status()['camera_skipped']==14
    with pytest.raises(ValueError):DeviceSink(Outbox(),{'camera_forward':'some'})
    with pytest.raises(ValueError):DeviceSink(Outbox(),{'camera_forward':'sweep','camera_sweep_window_seconds':0})


# systemd unit
def test_unit_template_has_placeholders_and_keeps_its_hardening():
    text=UNIT.read_text(encoding='utf-8')
    assert 'User=@USER@' in text and 'WorkingDirectory=@HOME@/aethertwin/src' in text
    assert 'User=aether' not in text and '/home/aether' not in text and 'labpi' not in text
    for line in HARDENING:assert line in text.splitlines()
    assert 'EnvironmentFile=-/etc/default/aethertwin-capture' in text and '--config ${AETHERTWIN_CAPTURE_CONFIG}' in text


def git_bash():
    candidates=[shutil.which('bash')]
    if os.name=='nt':candidates=[c for c in candidates if c and 'system32' not in c.lower()]+[r'C:\Program Files\Git\bin\bash.exe']
    return next((c for c in candidates if c and Path(c).exists()),None)


def bash_path(path):
    path=Path(path)
    return '/'+path.drive[0].lower()+path.as_posix()[2:] if os.name=='nt' and path.drive else path.as_posix()


@pytest.mark.skipif(git_bash() is None,reason='bash is not available')
@pytest.mark.skipif(getattr(os,'geteuid',lambda:1)()==0,reason='pi_bootstrap.sh refuses to run as root')
def test_bootstrap_is_bash_clean_and_renders_the_unit_for_the_login_user(tmp_path):
    bash=git_bash();assert subprocess.run([bash,'-n',bash_path(BOOTSTRAP)]).returncode==0
    home=tmp_path/'home'/'labpi';home.mkdir(parents=True);env={**os.environ,'HOME':bash_path(home)}
    user=subprocess.run([bash,'-c','id -un'],capture_output=True,text=True,env=env).stdout.strip()
    result=subprocess.run([bash,bash_path(BOOTSTRAP),'unit'],capture_output=True,text=True,env=env)
    assert result.returncode==0,result.stdout+result.stderr
    lines=result.stdout.splitlines();h=bash_path(home)
    assert f'User={user}' in lines and f'WorkingDirectory={h}/aethertwin/src' in lines and '@' not in result.stdout
    assert any(line.startswith(f'ExecStart={h}/aethertwin/capture-env/bin/python -m ingestion.capture_node') for line in lines)
    for line in HARDENING:assert line in lines
    bad=tmp_path/'bad home';bad.mkdir()
    refused=subprocess.run([bash,bash_path(BOOTSTRAP),'unit'],capture_output=True,text=True,env={**os.environ,'HOME':bash_path(bad)})
    assert refused.returncode==2 and 'Unsupported characters' in refused.stdout


def test_a_failed_device_open_exits_for_a_systemd_retry(monkeypatch,tmp_path,capsys):
    # On the rig the Gemini can still be resetting when the service restarts; the node must not idle forever.
    key=tmp_path/'node.key';key.write_text(KEY.hex());key.chmod(0o600)
    def broken(*args,**kwargs):raise TimeoutError('Gemini RGB-D acquisition timed out')
    monkeypatch.setattr(cn,'HardwareDevices',broken)
    code=cn.main(['--config',str(PROFILE),'--bringup','--bind','127.0.0.1','--key-file',str(key),'--port',str(free_port()),
                  '--status-interval','60','--duration','20'])
    events=[json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith('{')]
    # Exit code 3 (device fault) makes systemd restart the node (Restart=on-failure); seen working on the rig.
    assert code==3 and [e['event'] for e in events]==['device_open_failed','listening','device_fault'] and 'timed out' in events[0]['error']

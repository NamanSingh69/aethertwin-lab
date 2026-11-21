"""tools/rig.py with a fake subprocess runner: ARP parsing, command construction in service and fallback mode, the --live gate,
no-overwrite evidence (no hardware)."""
import json
from pathlib import Path
from tools import rig
from tools.rig import Result,Rig,parse_arp,decode_throttled,service_defaults,summarize_log

PI='labpi@192.168.137.37'
WINDOWS_ARP='''
Interface: 192.168.1.10 --- 0x5
  Internet Address      Physical Address      Type
  192.168.1.1           02-00-00-00-00-02     dynamic
  224.0.0.22            02-00-00-00-00-03     static

Interface: 192.168.137.1 --- 0x8
  Internet Address      Physical Address      Type
  192.168.137.37        02-00-00-00-00-04     static
  192.168.137.52        aa-bb-cc-dd-ee-ff     dynamic
  192.168.137.255       ff-ff-ff-ff-ff-ff     static
  239.255.255.250       02-00-00-00-00-05     static
'''
BRINGUP='4989 /home/x/aethertwin/capture-env/bin/python -m ingestion.capture_node --config config/profiles/hotspot.yaml --status-interval 30 --bringup'
LIVE='4989 /home/x/aethertwin/capture-env/bin/python -m ingestion.capture_node --config config/profiles/hotspot.yaml --status-interval 30'
STATE='echo enabled='  # the node_state script
DEFAULTS='AETHERTWIN_CAPTURE_CONFIG=config/profiles/hotspot.yaml\nAETHERTWIN_CAPTURE_ARGS=--bringup\n'
LISTENING='active/\n{"event": "listening", "address": ["192.168.137.37", 7600], "interface": "wlan0"}\n'


class FakeRunner:
    """Records every command; answers with the first response whose key is a substring of the joined command."""
    def __init__(self,*responses):self.calls=[];self.responses=list(responses)
    def __call__(self,cmd,*,input=None,timeout=None,stream=False,pipe=None):
        cmd=[str(c) for c in cmd];self.calls.append({'cmd':cmd,'pipe':pipe,'stream':stream});joined=' '.join(cmd)
        for key,response in self.responses:
            if key in joined:return response(cmd) if callable(response) else response
        return Result(0,'','')
    def remotes(self):return [c['cmd'][-1] for c in self.calls if Path(c['cmd'][0]).stem=='ssh']
    def scps(self):return [c['cmd'] for c in self.calls if Path(c['cmd'][0]).stem=='scp']


def node(*procs,service=False,active='inactive',pid='0'):
    """What the node_state script prints."""
    return Result(0,f"enabled={'enabled' if service else ''}\nactive={active}\npid={pid}\n"+''.join(f'proc={p}\n' for p in procs))


def seq(*results):
    """Answers in order, then repeats the last one."""
    left=list(results);return lambda cmd:left.pop(0) if len(left)>1 else left[0]


def make(tmp_path,runner,**kw):
    out=[];return Rig(kw.pop('pi',PI),runner=runner,out=out.append,sleep=lambda s:None,root=kw.pop('root',tmp_path),state=tmp_path/'.local'/'rig.json',**kw),out


def measured(tmp_path,value=True):
    path=tmp_path/rig.DEFAULT_CALIBRATION;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps({'measured':value}))


def test_parse_arp_windows_table_keeps_only_hotspot_clients():
    assert parse_arp(WINDOWS_ARP)==[('192.168.137.37','02:00:00:00:00:04'),('192.168.137.52','aa:bb:cc:dd:ee:ff')]
    assert parse_arp('  192.168.137.37        02-00-00-00-00-04     static')==[('192.168.137.37','02:00:00:00:00:04')]
    assert parse_arp('  192.168.137.1         02-00-00-00-00-06     dynamic\n  192.168.137.0  02-00-00-00-00-07  static')==[]


def test_parse_arp_unix_format():
    text='? (192.168.137.37) at 02:00:00:00:00:04 [ether] on wlan0\n? (192.168.137.9) at <incomplete> on wlan0\n? (10.0.0.2) at 0:1:2:3:4:5 on en0'
    assert parse_arp(text)==[('192.168.137.37','02:00:00:00:00:04')]


def test_find_probes_key_only_and_saves_state(tmp_path):
    runner=FakeRunner(('arp -a',Result(0,WINDOWS_ARP)),('@192.168.137.37',Result(0,'capture-pi\n1671b5a41a7b\n')),
                      ('@192.168.137.52',Result(255,'','Permission denied (publickey).')))
    r,out=make(tmp_path,runner,pi=None)
    assert r.find()==0
    state=json.loads((tmp_path/'.local'/'rig.json').read_text())
    assert state['pi']==PI and state['hostname']=='capture-pi' and state['mac']=='02:00:00:00:00:04'
    probes=[c['cmd'] for c in runner.calls if Path(c['cmd'][0]).stem=='ssh']
    assert len(probes)==2 and all('BatchMode=yes' in c and 'IdentitiesOnly=yes' in c for c in probes)
    assert Rig(None,runner=runner,state=tmp_path/'.local'/'rig.json').pi==PI  # later commands reuse it


def test_start_command_and_bracketed_pattern(tmp_path):
    r,_=make(tmp_path,FakeRunner())
    assert r.start_command()=='setsid ~/aethertwin/start_node.sh --config config/profiles/hotspot.yaml --bringup --status-interval 5 >/dev/null 2>&1 < /dev/null &'
    assert '--bringup' not in r.start_command(live=True)
    assert rig.STOP_COMMAND=='pkill -f "[c]apture-env/bin/python -m ingestion"'
    assert 'timeout 5 systemctl is-enabled aethertwin-capture' in r.state_script()


def test_node_state_parses_service_and_processes(tmp_path):
    r,_=make(tmp_path,FakeRunner((STATE,node(BRINGUP,service=True,active='active',pid='4989'))))
    s=r.node_state();assert s.service and s.running and s.pid=='4989' and s.bringup is True and s.procs[0][0]=='4989'
    r,_=make(tmp_path,FakeRunner((STATE,node(service=True,active='activating'))))
    s=r.node_state();assert s.running and s.bringup is None
    r,_=make(tmp_path,FakeRunner((STATE,node(LIVE))));s=r.node_state();assert not s.service and s.running and s.bringup is False


def test_service_defaults_toggle_bringup_and_keep_other_flags():
    assert service_defaults(DEFAULTS,live=True)=='AETHERTWIN_CAPTURE_CONFIG=config/profiles/hotspot.yaml\nAETHERTWIN_CAPTURE_ARGS=\n'
    assert service_defaults(DEFAULTS,live=False)==DEFAULTS
    assert service_defaults('AETHERTWIN_CAPTURE_ARGS=--bind 192.168.137.37\n',live=False)=='AETHERTWIN_CAPTURE_ARGS=--bind 192.168.137.37 --bringup\n'
    assert service_defaults('AETHERTWIN_CAPTURE_CONFIG=x.yaml\n',live=False)=='AETHERTWIN_CAPTURE_CONFIG=x.yaml\nAETHERTWIN_CAPTURE_ARGS=--bringup\n'


# ---- fallback mode (no enabled unit): start_node.sh and pkill

def test_fallback_stop_runs_pkill_in_its_own_ssh_call(tmp_path):
    runner=FakeRunner((STATE,seq(node(BRINGUP),node())));r,out=make(tmp_path,runner)
    assert r.node_stop()==0 and out[-1]=='Node stopped'
    assert [c for c in runner.remotes() if 'pkill' in c]==[rig.STOP_COMMAND]
    assert not any('capture-env/bin/python -m' in c for c in runner.remotes())  # the pattern only ever appears bracketed
    assert not any('systemctl stop' in c for c in runner.remotes())


def test_fallback_start_bringup_waits_for_listening(tmp_path):
    runner=FakeRunner((STATE,node()),('echo running',Result(0,'running\n{"event": "listening", "address": ["192.168.137.37", 7600]}\n')))
    r,out=make(tmp_path,runner)
    assert r.node_start()==0 and 'bring-up mode' in out[-1]
    assert r.start_command() in runner.remotes() and not any('pkill' in c or 'systemctl' in c.replace('systemctl is-','') for c in runner.remotes()[1:])


def test_fallback_restart_stops_then_starts(tmp_path):
    runner=FakeRunner((STATE,seq(node(BRINGUP),node(BRINGUP),node())),('echo running',Result(0,'running\n{"event": "listening"}\n')))
    r,out=make(tmp_path,runner)
    assert r.node_start(restart=True)==0
    remotes=runner.remotes();assert remotes.index(rig.STOP_COMMAND)<remotes.index(r.start_command())


def test_live_refused_without_measured_calibration(tmp_path):
    for service in (False,True):
        runner=FakeRunner((STATE,node(service=service)));r,out=make(tmp_path,runner)
        assert r.node_start(live=True)==2 and 'Refusing --live' in out[-1]
        assert not any('start_node.sh' in c or 'tee' in c or 'systemctl start' in c for c in runner.remotes())
    runner=FakeRunner((STATE,node()));r,out=make(tmp_path,runner)
    assert r.node_start(live=True)==2 and 'no calibration' in out[-1]
    measured(tmp_path,False)
    assert r.node_start(live=True)==2 and 'not a measured calibration' in out[-1]
    assert r.record('live_run',10,live=True)==2 and 'Refusing --live' in out[-1]


def test_live_allowed_with_measured_calibration_on_both_ends(tmp_path):
    measured(tmp_path)
    runner=FakeRunner((STATE,node()),('test -f',Result(1)),('echo running',Result(0,'running\n{"event": "listening"}\n')))
    r,out=make(tmp_path,runner)
    assert r.node_start(live=True)==2 and 'missing on the Pi' in out[-1]
    runner.responses[1]=('test -f',Result(0));assert r.node_start(live=True)==0
    assert r.start_command(live=True) in runner.remotes()


# ---- service mode (aethertwin-capture enabled): systemctl, the defaults file, the journal

def test_service_restart_live_rewrites_defaults_and_does_not_block(tmp_path):
    measured(tmp_path)
    runner=FakeRunner((STATE,node(BRINGUP,service=True,active='active',pid='4989')),('test -f',Result(0)),('cat /etc/default',Result(0,DEFAULTS)),
                      ('date +%s',Result(0,'1762280000\n')),('show -p Job',seq(Result(0,'inactive/812\n'),Result(0,'activating/812\n'),Result(0,LISTENING))))
    r,out=make(tmp_path,runner)
    assert r.node_start(live=True,restart=True)==0 and 'Service active in live mode' in out[-1]
    remotes=runner.remotes()
    tee="printf '%s\\n' 'AETHERTWIN_CAPTURE_CONFIG=config/profiles/hotspot.yaml' 'AETHERTWIN_CAPTURE_ARGS=' | sudo -n tee /etc/default/aethertwin-capture >/dev/null"
    assert tee in remotes and 'date +%s; sudo -n systemctl restart --no-block aethertwin-capture' in remotes
    assert remotes.index(tee)<remotes.index('date +%s; sudo -n systemctl restart --no-block aethertwin-capture')
    assert any('--since @1762280000' in c and "grep -v '\\[UDPHandler\\]'" in c for c in remotes)
    assert any('before:' in line for line in out) and any('after:' in line for line in out)
    assert not any('pkill' in c or 'start_node.sh' in c for c in remotes)


def test_service_plain_start_puts_bringup_back(tmp_path):
    runner=FakeRunner((STATE,node(service=True)),('cat /etc/default',Result(0,'AETHERTWIN_CAPTURE_ARGS=--bind 192.168.137.37\n')),
                      ('date +%s',Result(0,'1762280000\n')),('show -p Job',Result(0,LISTENING)))
    r,out=make(tmp_path,runner)
    assert r.node_start()==0 and 'bring-up mode' in out[-1]
    remotes=runner.remotes()
    assert "printf '%s\\n' 'AETHERTWIN_CAPTURE_ARGS=--bind 192.168.137.37 --bringup' | sudo -n tee /etc/default/aethertwin-capture >/dev/null" in remotes
    assert 'date +%s; sudo -n systemctl start --no-block aethertwin-capture' in remotes


def test_service_start_when_already_running_in_that_mode_does_nothing(tmp_path):
    runner=FakeRunner((STATE,node(BRINGUP,service=True,active='active',pid='4989')),('cat /etc/default',Result(0,DEFAULTS)))
    r,out=make(tmp_path,runner)
    assert r.node_start()==0 and 'already active' in out[-1]
    assert not any('tee' in c or 'systemctl start' in c or 'systemctl restart' in c for c in runner.remotes())


def test_service_start_gives_up_after_the_wait_instead_of_hanging(tmp_path):
    runner=FakeRunner((STATE,node(service=True)),('cat /etc/default',Result(0,DEFAULTS)),('date +%s',Result(0,'1\n')),('show -p Job',Result(0,'inactive/812\n')))
    r,out=make(tmp_path,runner)
    assert r.node_start()==1 and 'no listening event after 60 s' in out[-1] and 'time-sync' in out[-1]
    runner.responses[-1]=('show -p Job',Result(0,'failed/\n{"event": "bind_failed", "error": "wlan0 has no address"}\n'))
    assert r.node_start()==1 and 'Service failed' in out[-1] and 'bind_failed' in out[-1]


def test_service_stop_uses_systemctl_never_pkill(tmp_path):
    runner=FakeRunner((STATE,seq(node(BRINGUP,service=True,active='active',pid='4989'),node(service=True,active='inactive'))))
    r,out=make(tmp_path,runner)
    assert r.node_stop()==0 and out[-1]=='Service stopped'
    assert 'sudo -n systemctl stop aethertwin-capture' in runner.remotes() and not any('pkill' in c for c in runner.remotes())


def test_service_log_reads_the_journal(tmp_path):
    status='{"event": "status", "uptime_seconds": 90, "session": null, "outbox": {"imu_samples": 5}}'
    runner=FakeRunner((STATE,node(BRINGUP,service=True,active='active')),('journalctl',Result(0,status+'\n')));r,out=make(tmp_path,runner)
    assert r.node_log(6)==0 and out[-1].startswith('up 1.5 min; no client connected')
    assert "journalctl -u aethertwin-capture -o cat --no-pager -n 6 | grep -v '\\[UDPHandler\\]'; true" in runner.remotes()
    runner=FakeRunner((STATE,node()));r,out=make(tmp_path,runner);r.node_log(6)
    assert 'tail -n 6 ~/aethertwin/evidence/capture_node_bringup.log' in runner.remotes()


def test_record_reuses_the_bringup_service_and_never_starts_a_second_node(tmp_path):
    (tmp_path/'rec'/'taken').mkdir(parents=True);(tmp_path/'rec'/'taken'/'x').write_text('')
    runner=FakeRunner((STATE,node(BRINGUP,service=True,active='active',pid='4989')))
    r,out=make(tmp_path,runner,rec_root=tmp_path/'rec')
    assert r.record('taken',10)==2 and 'not empty' in out[-1]
    assert r.record('fresh',30,'mobile')==0
    (local,)=[c for c in runner.calls if c['stream']]
    assert local['cmd'][1].endswith('record_raw.py') and '--bringup' in local['cmd']
    assert local['cmd'][local['cmd'].index('--capture-host')+1]=='192.168.137.37' and local['cmd'][-1]==str(tmp_path/'rec'/'fresh')
    assert not any('start_node.sh' in c or 'systemctl start' in c or 'systemctl restart' in c for c in runner.remotes())
    runner=FakeRunner((STATE,node(service=True,active='activating')));r,out=make(tmp_path,runner,rec_root=tmp_path/'rec')
    assert r.record('fresh2',30)==2 and 'activating' in out[-1]


def device_report(cmd):
    report={'system_before':{'throttled':'throttled=0x0','temp':"temp=46.6'C"},'system_after':{'throttled':'throttled=0x50000'},
            'l2':{'sweeps':60,'sweep_hz':11.95,'imu_hz':251.4,'points_per_sweep':{'median':4900,'p5':4800,'p95':5000},'dropped_clouds':0}}
    Path(cmd[-1]).write_text(json.dumps(report));return Result(0)


def test_check_refuses_while_node_runs_and_builds_remote_command(tmp_path):
    runner=FakeRunner((STATE,node(BRINGUP)));r,out=make(tmp_path,runner)
    assert r.check(5,'l2')==2 and '--stop-node' in out[-1]
    runner=FakeRunner((STATE,node()),('scp',device_report));r,out=make(tmp_path,runner,evidence=tmp_path/'ev')
    assert r.check(5,'l2')==0
    remote=[c for c in runner.remotes() if 'pi_device_check' in c][0]
    assert remote.startswith('cd ~/aethertwin/src && ~/aethertwin/capture-env/bin/python tools/pi_device_check.py --config config/profiles/hotspot.yaml --seconds 5 --skip gemini --output ~/aethertwin/evidence/device_check_l2_')
    assert any('L2: 60 sweeps at 11.95 Hz' in line for line in out) and any('throttling has occurred' in line for line in out)


def test_check_stop_node_stops_the_service_and_starts_it_again_in_the_same_mode(tmp_path):
    running=node(BRINGUP,service=True,active='active',pid='4989');stopped=node(service=True,active='inactive')
    runner=FakeRunner((STATE,seq(running,running,stopped,stopped)),('scp',device_report),('cat /etc/default',Result(0,DEFAULTS)),
                      ('date +%s',Result(0,'1762280000\n')),('show -p Job',Result(0,LISTENING)))
    r,out=make(tmp_path,runner,evidence=tmp_path/'ev')
    assert r.check(5,'gemini',stop_node=True)==0
    remotes=runner.remotes()
    stop=remotes.index('sudo -n systemctl stop aethertwin-capture');check=next(i for i,c in enumerate(remotes) if 'pi_device_check' in c)
    start=remotes.index('date +%s; sudo -n systemctl start --no-block aethertwin-capture')
    assert stop<check<start and not any('tee' in c for c in remotes)  # same (bring-up) mode: defaults untouched
    assert 'Service active in bring-up mode' in out[-1]


def test_evidence_never_overwrites_without_force(tmp_path):
    dest=tmp_path/'evidence';dest.mkdir();(dest/'a.json').write_text('local copy')
    runner=FakeRunner(('ls -1p',Result(0,'a.json\nb.json\nsub/\nbad name.txt\n')));r,out=make(tmp_path,runner,evidence=dest)
    assert r.evidence()==0
    (scp,)=runner.scps();assert scp[-2:]==[f'{PI}:aethertwin/evidence/b.json',str(dest)] and not any('a.json' in a for a in scp)
    assert (dest/'a.json').read_text()=='local copy' and any('Kept 1' in line for line in out)
    assert r.evidence(force=True)==0 and any('a.json' in a for a in runner.scps()[-1])


def test_push_pipes_git_archive_and_reports_native_changes(tmp_path):
    runner=FakeRunner(('rev-parse HEAD',Result(0,'b'*40+'\n')),('status --porcelain',Result(0,'')),('SOURCE_COMMIT 2>',Result(0,'a'*40+'\n')),
                      (STATE,node()),('diff --name-only',Result(0,'native/src/lab_sensors.cpp\n')))
    r,out=make(tmp_path,runner)
    assert r.push()==0
    (call,)=[c for c in runner.calls if c['pipe']]
    assert call['pipe'][-3:]==['archive','--format=tar','HEAD'] and call['cmd'][-1].endswith(f"echo {'b'*40} > ~/aethertwin/src/SOURCE_COMMIT")
    assert any('native/ changed' in line for line in out)


def test_shutdown_needs_confirmation(tmp_path):
    runner=FakeRunner((STATE,node()))
    r,out=make(tmp_path,runner,ask=lambda prompt:'n')
    assert r.shutdown()==1 and not any('shutdown' in c for c in runner.remotes())
    assert r.shutdown(yes=True)==0 and runner.remotes()[-1]=='sudo -n shutdown -h now'


def test_decoders():
    assert decode_throttled('throttled=0x0')=='ok (0x0)'
    assert decode_throttled('throttled=0x50000')=='0x50000: under-voltage has occurred, throttling has occurred'
    a={'event':'status','uptime_seconds':60,'session':{'peer':'192.168.137.1','streaming':True,'bytes_sent':0},'outbox':{'imu_samples':1}}
    b=a|{'uptime_seconds':65,'session':{'peer':'192.168.137.1','streaming':True,'bytes_sent':25_000_000}}
    assert '5.00 MB/s' in summarize_log([json.dumps(a),json.dumps(b)])
    assert summarize_log(['{"event": "bind_failed", "error": "no wlan0"}'])=='last event: bind_failed no wlan0'


def test_a_start_counts_only_once_its_devices_are_open():
    from tools.rig import start_healthy
    L='{"event": "listening", "address": ["192.168.137.37", 7600]}';F='{"event": "device_open_failed", "error": "Gemini timed out"}';D='{"event": "device_fault"}'
    assert start_healthy(L) and not start_healthy('') and not start_healthy(F+'\n'+L)
    assert start_healthy('\n'.join([F,L,D,'Started aethertwin-capture.service',L]))  # systemd retried and the second open worked
    assert not start_healthy('\n'.join([F,L,D,'Started',F,L]))

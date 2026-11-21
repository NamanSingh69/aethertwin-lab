"""Lab rig helper: drive the Raspberry Pi capture node from the Windows laptop (processing host) over SSH.

One tool for the bench steps that otherwise need long, error-prone command lines. It runs on the laptop
(Git Bash, PowerShell or cmd), prints every command it runs and a short result, and never prints secrets.
Local commands are argument lists (no shell); each remote command is one string for `ssh`.

Usage (from the repository root; `python` is the repo venv, `.venv\\Scripts\\python.exe` on Windows):
    python tools/rig.py find                         # locate the Pi on the hotspot, remember it in .local/rig.json
    python tools/rig.py status                       # one screen of health: node, power, LiDAR link, camera, disks, commit
    python tools/rig.py push [--rebuild]             # committed HEAD -> Pi:~/aethertwin/src; says if native/ changed
    python tools/rig.py node start|stop|restart [--live]   # bring-up mode by default; --live needs a measured calibration
    python tools/rig.py node log [-n 20] [--raw] [--follow]
    python tools/rig.py check [--seconds 20] [--only l2|gemini] [--stop-node]   # pi_device_check, JSON -> reports/hardware/pi/
    python tools/rig.py record NAME --duration 60 [--motion stationary|mobile] [--live]   # record_raw on the laptop
    python tools/rig.py evidence [--force]           # Pi:~/aethertwin/evidence/* -> reports/hardware/pi/ (no overwrite)
    python tools/rig.py shutdown [--yes]             # clean `sudo -n shutdown -h now` after a prompt
Global options: --pi user@host (overrides .local/rig.json), --config (default config/profiles/hotspot.yaml, the
same relative path on both ends), --key (default ~/.ssh/aethertwin_ed25519, used with IdentitiesOnly and BatchMode).

Two ways the node runs on the Pi, detected on every node command (`systemctl is-enabled aethertwin-capture`):
  * service mode (unit enabled, rendered by `pi_bootstrap.sh service`): start/stop/restart go through
    `sudo -n systemctl ... aethertwin-capture` (start and restart with --no-block, then up to ~60 s of polling
    `systemctl is-active` and the journal for the "listening" event: the unit waits for time-sync.target).
    The mode lives in AETHERTWIN_CAPTURE_ARGS in /etc/default/aethertwin-capture: `--live` removes `--bringup`,
    a plain start/restart puts it back (written with `sudo -n tee`, printed before and after). Logs come from
    `journalctl -u aethertwin-capture -o cat` ([UDPHandler] lines filtered). Never pkill the service's python:
    a clean SIGTERM exit is not a failure, so systemd would not restart it.
  * fallback (no enabled unit): ~/aethertwin/start_node.sh under setsid, its log in evidence/capture_node_bringup.log.

Conventions it enforces:
  * fallback stop is `pkill -f "[c]apture-env/bin/python -m ingestion"` in an SSH call of its own (a pkill whose
    own command line contains its pattern kills its shell; the brackets keep pgrep/pkill off it);
  * `--live` (no `--bringup`) is refused until the profile's calibration file exists here with "measured": true
    and also exists on the Pi; bring-up remains the mode for calibration-target captures;
  * `check` refuses while the node holds the devices unless --stop-node (then the node is started again afterwards
    in the same mode); `push --rebuild` refuses while the node runs the module; `record` reuses a running node
    in the right mode and never starts a second one;
  * `evidence` never overwrites a local file unless --force; `record` never writes into a non-empty directory.
Recordings go to ~/aethertwin_rec/NAME unless --rec-root (or "rec_root" in .local/rig.json) says otherwise.
"""
from __future__ import annotations
import argparse,ipaddress,json,os,re,shutil,subprocess,sys,time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
STATE=ROOT/'.local'/'rig.json';EVIDENCE=ROOT/'reports'/'hardware'/'pi';REC_ROOT=Path.home()/'aethertwin_rec'
KEY=Path.home()/'.ssh'/'aethertwin_ed25519';SUBNET='192.168.137.0/24';USER='labpi';CONFIG='config/profiles/hotspot.yaml'
DEFAULT_CALIBRATION='config/calibration_extrinsics_640x400.json';DEFAULT_LIDAR_IP='192.168.1.62'
SRC='~/aethertwin/src';PY='~/aethertwin/capture-env/bin/python';EVIDENCE_REMOTE='aethertwin/evidence'
LOG='~/aethertwin/evidence/capture_node_bringup.log'
# Brackets: the regex matches the node's command line but never the shell carrying the pattern.
NODE_PATTERN='[c]apture-env/bin/python -m ingestion'
STOP_COMMAND=f'pkill -f "{NODE_PATTERN}"'
SERVICE='aethertwin-capture';SERVICE_DEFAULTS='/etc/default/aethertwin-capture'
JOURNAL=f"journalctl -u {SERVICE} -o cat --no-pager";NOT_UDP="grep -v '\\[UDPHandler\\]'"
SAFE_DEFAULT_LINE=re.compile(r'^[A-Za-z0-9_=./:#, +@-]*$')  # what may go into the defaults file through printf '...'
THROTTLE_BITS={0:'under-voltage NOW',1:'frequency capped NOW',2:'throttled NOW',3:'soft temperature limit NOW',
               16:'under-voltage has occurred',17:'frequency capping has occurred',18:'throttling has occurred',19:'soft temperature limit has occurred'}
SAFE_NAME=re.compile(r'^[A-Za-z0-9._@+-]+$')


@dataclass
class Result:
    code:int;out:str='';err:str=''


@dataclass
class NodeState:
    """service: the unit is enabled (systemd drives the node); procs: [(pid, command line)] of running nodes."""
    service:bool=False;active:str='';pid:str='';procs:tuple=()
    @property
    def running(self):return bool(self.procs) or (self.service and self.active in ('active','activating','reloading'))
    @property
    def bringup(self):return None if not self.procs else '--bringup' in self.procs[0][1]
    @property
    def mode(self):return {True:'bring-up',False:'LIVE',None:'mode not known yet'}[self.bringup]


def service_defaults(text,live):
    """/etc/default/aethertwin-capture with --bringup added to (bring-up) or removed from (live) AETHERTWIN_CAPTURE_ARGS; other lines and flags kept."""
    out=[];seen=False
    for line in text.splitlines():
        if line.startswith('AETHERTWIN_CAPTURE_ARGS='):
            seen=True;flags=[f for f in line.split('=',1)[1].strip().strip('"\'').split() if f!='--bringup']
            line='AETHERTWIN_CAPTURE_ARGS='+' '.join(flags+([] if live else ['--bringup']))
        out.append(line)
    if not seen:out.append('AETHERTWIN_CAPTURE_ARGS='+('' if live else '--bringup'))
    return '\n'.join(out)+'\n'


def repr_single(text):
    """Single-quoted for the remote shell; callers allow only SAFE_DEFAULT_LINE characters (no quotes)."""
    return "'"+text+"'"


def run_process(cmd,*,input=None,timeout=None,stream=False,pipe=None):
    """Run an argument list (never a shell string). stream: share this console. pipe: a command whose stdout feeds cmd."""
    try:
        if pipe is not None:
            source=subprocess.Popen(pipe,stdout=subprocess.PIPE)
            try:done=subprocess.run(cmd,stdin=source.stdout,capture_output=True,timeout=timeout)
            finally:source.stdout.close();source.wait()
            return Result(done.returncode or source.returncode,done.stdout.decode('utf-8','replace'),done.stderr.decode('utf-8','replace'))
        if stream:return Result(subprocess.run(cmd,input=input,text=True,timeout=timeout).returncode)
        done=subprocess.run(cmd,input=input,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
        return Result(done.returncode,done.stdout,done.stderr)
    except FileNotFoundError as exc:return Result(127,'',f'not found: {exc.filename}')
    except subprocess.TimeoutExpired:return Result(124,'',f'timed out after {timeout} s')
    except KeyboardInterrupt:return Result(130,'','interrupted')


def ssh_binary(name):
    """Windows' own OpenSSH first: Git Bash's msys ssh/scp would read C:/... as a host name."""
    if os.name=='nt':
        native=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32'/'OpenSSH'/f'{name}.exe'
        if native.exists():return str(native)
    return shutil.which(name) or name


def parse_arp(text,subnet=SUBNET):
    """(ip, mac) hotspot clients from `arp -a` (Windows or Unix format), without the network, gateway (.1) and broadcast."""
    net=ipaddress.ip_network(subnet);skip={net.network_address,net.network_address+1,net.broadcast_address};found=[]
    for line in text.splitlines():
        m=re.match(r'\s*(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9A-Fa-f]{2}(?:[-:][0-9A-Fa-f]{2}){5})\b',line) \
            or re.search(r'\((\d{1,3}(?:\.\d{1,3}){3})\) at ([0-9A-Fa-f]{1,2}(?::[0-9A-Fa-f]{1,2}){5})\b',line)
        if not m:continue
        try:ip=ipaddress.ip_address(m[1])
        except ValueError:continue
        mac=':'.join(p.zfill(2) for p in re.split('[-:]',m[2].lower()))
        if ip in net and ip not in skip and mac!='ff:ff:ff:ff:ff:ff' and str(ip) not in (f[0] for f in found):found.append((str(ip),mac))
    return found


def decode_throttled(text):
    m=re.search(r'0x([0-9A-Fa-f]+)',text or '')
    if not m:return f'unavailable ({(text or "").strip() or "no output"})'
    value=int(m[1],16)
    if not value:return 'ok (0x0)'
    return f'0x{value:x}: '+', '.join(v for b,v in THROTTLE_BITS.items() if value>>b&1)


def usb_speed(mbps):
    return {'480':'USB 2 (480 Mb/s)','5000':'USB 3 (5 Gb/s)','10000':'USB 3.1 (10 Gb/s)','12':'USB 1 (12 Mb/s)'}.get(str(mbps).strip(),f'{mbps} Mb/s')


def start_healthy(out):
    """True once a start is listening with its devices open: a failed open (device_open_failed) is followed by a
    device_fault exit and a systemd restart, so only a listening event after that fault counts."""
    ok=out.rfind('"event": "listening"');bad=out.rfind('"event": "device_open_failed"');fault=out.rfind('"event": "device_fault"')
    return ok>=0 and ok>fault and (bad<0 or fault>bad)


def summarize_log(lines):
    """One line for the node's last event; status lines become counts, a client summary and the send rate."""
    events=[]
    for line in lines:
        try:e=json.loads(line)
        except ValueError:continue
        if isinstance(e,dict):events.append(e)
    if not events:return 'no JSON events in the log tail'
    last=events[-1];statuses=[e for e in events if e.get('event')=='status']
    if last.get('event')!='status':
        detail=last.get('error') or last.get('faults') or last.get('address') or ''
        return f"last event: {last.get('event')} {detail}".strip()
    s=last;o=s.get('outbox') or {};d=s.get('devices') or {};session=s.get('session')
    text=f"up {s.get('uptime_seconds',0)/60:.1f} min; "
    text+=f"client {session.get('peer')} {'streaming' if session.get('streaming') else 'idle'}" if session else 'no client connected'
    if len(statuses)>=2 and session and (statuses[-2].get('session') or {}).get('peer')==session.get('peer'):
        a,b=statuses[-2],statuses[-1];dt=b.get('uptime_seconds',0)-a.get('uptime_seconds',0)
        sent=(b['session'].get('bytes_sent') or 0)-(a['session'].get('bytes_sent') or 0)
        if dt>0 and sent>=0:text+=f' at {sent/dt/1e6:.2f} MB/s'
    text+=(f"; imu {o.get('imu_samples')} (dropped {o.get('imu_dropped')}), clouds {o.get('clouds')} (dropped {o.get('clouds_dropped')}), "
           f"camera {o.get('camera_frames')} (dropped {o.get('camera_dropped')})")
    if d.get('faults'):text+=f"; FAULTS {d['faults']}"
    if s.get('last_error'):text+=f"; last error: {s['last_error']}"
    return text


def spread_text(value,unit='',digits=1):
    if not isinstance(value,dict):return 'n/a'
    return f"median {value.get('median',float('nan')):.{digits}f}{unit} (p5 {value.get('p5',float('nan')):.{digits}f}, p95 {value.get('p95',float('nan')):.{digits}f})"


def summarize_device_check(report):
    lines=[]
    for phase in ('system_before','system_after'):
        s=report.get(phase) or {};lines.append(f"{phase.replace('_',' ')}: {decode_throttled(s.get('throttled'))}, {s.get('temp','?')}")
    l2=report.get('l2')
    if l2 is not None:
        lines.append(f"L2: ERROR {l2['error']}" if 'error' in l2 else
            f"L2: {l2.get('sweeps')} sweeps at {l2.get('sweep_hz') or 0:.2f} Hz, points/sweep {spread_text(l2.get('points_per_sweep'),'',0)}, "
            f"IMU {l2.get('imu_hz') or 0:.1f} Hz, |accel| {spread_text(l2.get('imu_accel_norm'),'',3)}, dropped {l2.get('dropped_clouds')}, "
            f"monotonic {l2.get('monotonic_sweeps')}")
    gemini=report.get('gemini')
    if gemini is not None:
        lines.append(f"Gemini: ERROR {gemini['error']}" if 'error' in gemini else
            f"Gemini: {gemini.get('frames')} frames at {gemini.get('fps') or 0:.2f} fps, shapes {gemini.get('shapes')}, "
            f"depth valid {spread_text(gemini.get('depth_valid_fraction'),'',3)}, depth {spread_text(gemini.get('depth_m'),' m',2)}")
    return lines


def summarize_capture(report):
    rates=[r.get('bandwidth_mb_s') for r in report.get('timeline',[]) if r.get('bandwidth_mb_s') is not None]
    t=report.get('transport') or {};rec=report.get('recorder') or {}
    return [f"{report.get('label')}; {'bring-up (uncalibrated)' if report.get('bringup_uncalibrated') else 'calibrated'}, motion {report.get('motion')}",
            f"frames {report.get('frames')} in {report.get('elapsed_s') or 0:.1f} s ({report.get('frames_hz') or 0:.2f} Hz), sensor span {report.get('sensor_time_span_s')}",
            f"segments {t.get('segment')}, camera missing {t.get('camera_missing')}, cloud missing {t.get('cloud_missing')}, IMU breaks {t.get('imu_sequence_breaks')}",
            f"link {sum(rates)/len(rates):.2f} MB/s mean" if rates else 'link: no bandwidth samples',
            f"recorder complete: {rec.get('complete')}; error: {report.get('error')}"]


def calibration_path(root,config):
    """The profile's calibration (relative to the tree holding config/), or the hotspot default if the config cannot be read."""
    try:
        from core.configuration import load_config
        return str(load_config(Path(root)/config)['live']['calibration'])
    except Exception:return DEFAULT_CALIBRATION


def lidar_ip(root,config):
    try:
        from core.configuration import load_config
        return str(load_config(Path(root)/config)['live']['lidar_ip'])
    except Exception:return DEFAULT_LIDAR_IP


class Rig:
    def __init__(self,pi=None,*,runner=run_process,out=print,ask=input,sleep=time.sleep,root=ROOT,state=STATE,key=KEY,config=CONFIG,
                 evidence=None,rec_root=None):
        self.run=runner;self.out=out;self.ask=ask;self.sleep=sleep;self.root=Path(root);self.state_path=Path(state);self.key=Path(key)
        self.config=config;self.evidence_dir=Path(evidence) if evidence else self.root/'reports'/'hardware'/'pi';self._pi=pi;self._rec_root=rec_root

    # ---- plumbing
    def load_state(self):
        try:return json.loads(self.state_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}
    def save_state(self,**values):
        state=self.load_state()|values;self.state_path.parent.mkdir(parents=True,exist_ok=True)
        self.state_path.write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8');return state
    @property
    def pi(self):
        pi=self._pi or self.load_state().get('pi')
        if not pi:raise SystemExit('No Pi known yet: run `python tools/rig.py find` (or pass --pi user@host)')
        return pi
    @property
    def host(self):return self.pi.rsplit('@',1)[-1]
    @property
    def rec_root(self):return Path(self._rec_root or self.load_state().get('rec_root') or REC_ROOT)
    def ssh_options(self,timeout=6):
        return ['-i',str(self.key),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new','-o',f'ConnectTimeout={timeout}']
    def ssh_command(self,remote,pi=None,connect=6):return [ssh_binary('ssh'),*self.ssh_options(connect),pi or self.pi,remote]
    def ssh(self,remote,*,timeout=60,label=None,stream=False,pi=None,connect=6,quiet=False):
        if not quiet:self.out(f"$ ssh {pi or self.pi} {label or repr(remote)}")
        return self.run(self.ssh_command(remote,pi,connect),timeout=timeout,stream=stream)
    def scp(self,sources,dest,timeout=600):
        cmd=[ssh_binary('scp'),*self.ssh_options(),*[f'{self.pi}:{EVIDENCE_REMOTE}/{s}' for s in sources],str(dest)]
        shown=sources[0] if len(sources)==1 else f"{{{','.join(sources)}}}"
        self.out(f'$ scp {self.pi}:{EVIDENCE_REMOTE}/{shown} {dest}');return self.run(cmd,timeout=timeout)
    def local(self,cmd,**kw):
        self.out('$ '+subprocess.list2cmdline([str(c) for c in cmd]));return self.run([str(c) for c in cmd],**kw)
    def git(self,*args):return self.run(['git','-C',str(self.root),*args],timeout=60)
    def fail(self,message,code=1):self.out(message);return code

    # ---- find
    def find(self,user=USER,subnet=SUBNET):
        self.out('$ arp -a');table=self.run(['arp','-a'],timeout=20);candidates=parse_arp(table.out,subnet)
        if not candidates:
            net=ipaddress.ip_network(subnet);hosts=[str(h) for h in list(net.hosts())[1:]]
            self.out(f'No clients of {subnet} in the ARP table; pinging {hosts[0]}..{hosts[-1]} once each to fill it')
            ping=(lambda ip:['ping','-n','1','-w','400',ip]) if os.name=='nt' else (lambda ip:['ping','-c','1','-W','1',ip])
            with ThreadPoolExecutor(64) as pool:list(pool.map(lambda ip:self.run(ping(ip),timeout=5),hosts))
            candidates=parse_arp(self.run(['arp','-a'],timeout=20).out,subnet)
        if not candidates:return self.fail(f'No hotspot client found on {subnet}: is the Pi powered and joined to the hotspot?')
        self.out('Candidates: '+', '.join(f'{ip} ({mac})' for ip,mac in candidates))
        probe=lambda c:(c,self.ssh('hostname; cat ~/aethertwin/src/SOURCE_COMMIT 2>/dev/null || echo no-source',pi=f'{user}@{c[0]}',timeout=15,connect=4))
        with ThreadPoolExecutor(8) as pool:probes=list(pool.map(probe,candidates))
        good=[(c,r.out.split()) for c,r in probes if r.code==0 and r.out.strip()]
        for c,r in probes:
            if r.code!=0:self.out(f'  {c[0]}: no key-only SSH as {user} ({(r.err.strip().splitlines() or ["exit "+str(r.code)])[-1]})')
        if not good:return self.fail('No candidate accepted the key: check the user name (--user) and that the key is installed on the Pi')
        (ip,mac),words=good[0];pi=f'{user}@{ip}'
        if len(good)>1:self.out(f'Several hosts accept the key: using {pi}; pass --pi to choose another')
        self.save_state(pi=pi,hostname=words[0],mac=mac,found_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        self.out(f"Pi: {pi} (hostname {words[0]}, source {words[1][:12] if len(words)>1 else '?'}); saved to {self.state_path}");return 0

    # ---- node
    def state_script(self):
        return (f'echo enabled=$(timeout 5 systemctl is-enabled {SERVICE} 2>/dev/null); echo active=$(timeout 5 systemctl is-active {SERVICE} 2>/dev/null); '
                f"echo pid=$(timeout 5 systemctl show -p MainPID --value {SERVICE} 2>/dev/null); pgrep -af '{NODE_PATTERN}' | sed 's/^/proc=/'; true")
    def node_state(self):
        """Service mode (unit enabled) or fallback, and the running node processes, in one SSH call."""
        r=self.ssh(self.state_script(),timeout=30,quiet=True)
        if r.code!=0:raise SystemExit(f'Cannot reach {self.pi}: {r.err.strip() or "exit "+str(r.code)}')
        values={};procs=[]
        for line in r.out.splitlines():
            k,_,v=line.partition('=')
            if k=='proc' and v.strip():procs.append(tuple(v.strip().split(' ',1)) if ' ' in v.strip() else (v.strip(),''))
            elif k:values[k]=v.strip()
        return NodeState(values.get('enabled')=='enabled',values.get('active',''),values.get('pid',''),tuple(procs))
    def start_command(self,live=False):
        return f"setsid ~/aethertwin/start_node.sh --config {self.config}{'' if live else ' --bringup'} --status-interval 5 >/dev/null 2>&1 < /dev/null &"
    def calibration_problem(self):
        rel=calibration_path(self.root,self.config);path=self.root/rel
        advice='Stay in bring-up mode (drop --live) until a calibration is measured and copied to both ends'
        if not path.exists():return f'Refusing --live: no calibration at {rel} on this laptop. {advice} (deployment/lab/lab_sync.sh push-secret {rel}).'
        try:measured=json.loads(path.read_text(encoding='utf-8')).get('measured') is True
        except (OSError,ValueError) as exc:return f'Refusing --live: {rel} is unreadable ({exc}). {advice}.'
        if not measured:return f'Refusing --live: {rel} is not a measured calibration ("measured" is not true). {advice}.'
        if self.ssh(f'test -f {SRC}/{rel}',timeout=20).code!=0:return f'Refusing --live: {rel} is missing on the Pi; copy it with deployment/lab/lab_sync.sh push-secret {rel}.'
        return None
    def node_start(self,live=False,wait=30.,restart=False):
        state=self.node_state()
        if live and (problem:=self.calibration_problem()):return self.fail(problem,2)
        if state.service:return self.service_start(state,live,restart)
        if state.running:
            if not restart:self.out(f'Node already running (pid {state.procs[0][0]}, {state.mode} mode); use `node restart` to change it');return 0
            if code:=self.node_stop():return code
        self.ssh(f'[ -s {LOG} ] && cp {LOG} ~/aethertwin/evidence/capture_node_$(date -u +%Y%m%dT%H%M%SZ).log; true',timeout=20,
                 label='<keep the previous node log as evidence/capture_node_UTC.log>')
        r=self.ssh(self.start_command(live),timeout=30)
        if r.code!=0:return self.fail(f'Start failed: {r.err.strip() or "exit "+str(r.code)}')
        probe=f"pgrep -f '{NODE_PATTERN}' >/dev/null && echo running || echo stopped; tail -n 5 {LOG} 2>/dev/null"
        for _ in range(max(1,int(wait/2))):
            self.sleep(2.);r=self.ssh(probe,timeout=20,quiet=True);lines=r.out.strip().splitlines()
            alive=bool(lines) and lines[0]=='running';text=summarize_log(lines[1:])
            if not alive:return self.fail(f'Node exited: {text} (python tools/rig.py node log)')
            if '"event": "listening"' in r.out or '"event": "status"' in r.out:
                self.out(f"Node running in {'live' if live else 'bring-up'} mode: {text}");return 0
        self.out(f'Node running but not listening yet after {wait:.0f} s: {text}');return 0
    def service_start(self,state,live,restart=False,wait=60.):
        """Set the mode in the defaults file, then systemctl start/restart --no-block and poll for the listening event."""
        before=self.ssh(f'cat {SERVICE_DEFAULTS} 2>/dev/null',timeout=20).out
        after=service_defaults(before,live);changed=after.strip()!=before.strip()
        self.out(f'{SERVICE_DEFAULTS} before:');[self.out('  '+line) for line in before.splitlines() or ['(missing)']]
        if changed:
            lines=after.splitlines()
            if not all(SAFE_DEFAULT_LINE.match(line) for line in lines):return self.fail(f'Not rewriting {SERVICE_DEFAULTS}: unexpected characters in it; edit it by hand',2)
            r=self.ssh(f"printf '%s\\n' {' '.join(repr_single(line) for line in lines)} | sudo -n tee {SERVICE_DEFAULTS} >/dev/null",timeout=20)
            if r.code!=0:return self.fail(f'Could not write {SERVICE_DEFAULTS}: {r.err.strip() or "exit "+str(r.code)}')
            self.out(f'{SERVICE_DEFAULTS} after:');[self.out('  '+line) for line in self.ssh(f'cat {SERVICE_DEFAULTS}',timeout=20,quiet=True).out.splitlines()]
        else:self.out(f'{SERVICE_DEFAULTS} unchanged ({"live" if live else "bring-up"} mode)')
        if state.running and not restart and not changed:
            self.out(f"Service already {state.active} (pid {state.pid or '?'}, {state.mode}); use `node restart` to restart it");return 0
        verb='restart' if state.running else 'start'
        r=self.ssh(f'date +%s; sudo -n systemctl {verb} --no-block {SERVICE}',timeout=30)
        if r.code!=0:return self.fail(f'systemctl {verb} failed: {r.err.strip() or "exit "+str(r.code)}')
        since=(r.out.split() or ['0'])[0]
        # A start job queued behind time-sync.target leaves the unit inactive with a Job id: that is waiting, not failure.
        probe=(f"echo $(timeout 5 systemctl is-active {SERVICE})/$(timeout 5 systemctl show -p Job --value {SERVICE}); "
               f"{JOURNAL} --since @{since} | {NOT_UDP} | tail -n 20")
        polls=max(1,int(wait/2));text='no output yet';active='?'
        for i in range(polls):
            self.sleep(2.);r=self.ssh(probe,timeout=20,quiet=True);lines=r.out.strip().splitlines()
            active,_,job=(lines[0].strip() if lines else '?/').partition('/');text=summarize_log(lines[1:])
            if active=='failed' or (active=='inactive' and not job and i>=2):return self.fail(f'Service {active}: {text} (python tools/rig.py node log)')
            if active=='inactive' and job:text=f'start job {job} queued (waiting for time-sync.target?)'
            if active=='active' and start_healthy(r.out):
                self.out(f"Service active in {'live' if live else 'bring-up'} mode: {text}");return 0
            if '"event": "device_open_failed"' in r.out:text='devices failed to open; the node exits and systemd retries in ~3 s (seen on the rig after a quick restart)'
        return self.fail(f"Service {active} but no listening event after {wait:.0f} s (time-sync.target can hold the start): {text}")
    def node_stop(self,wait=15.):
        state=self.node_state()
        if state.service:
            if not state.running:self.out(f'Service {state.active or "inactive"}: nothing to stop');return 0
            r=self.ssh(f'sudo -n systemctl stop {SERVICE}',timeout=120)
            if r.code!=0:return self.fail(f'systemctl stop failed: {r.err.strip() or "exit "+str(r.code)}')
            after=self.node_state()
            if after.running:return self.fail(f'Service still {after.active} after stop (python tools/rig.py node log)')
            self.out('Service stopped');return 0
        if not state.running:self.out('Node not running');return 0
        self.ssh(STOP_COMMAND,timeout=20)  # its own SSH call, nothing else on that command line
        for _ in range(max(1,int(wait))):
            self.sleep(1.)
            if not self.node_state().running:self.out('Node stopped');return 0
        return self.fail(f'Node still running after {wait:.0f} s (python tools/rig.py node log)')
    def node_log(self,lines=20,raw=False,follow=False):
        service=self.node_state().service;n=int(lines)
        if follow:
            remote=f'{JOURNAL} -n {n} -f | grep --line-buffered -v \'\\[UDPHandler\\]\'' if service else f'tail -n {n} -F {LOG}'
            return self.ssh(remote,timeout=None,stream=True).code
        r=self.ssh(f'{JOURNAL} -n {n} | {NOT_UDP}; true' if service else f'tail -n {n} {LOG}',timeout=30)
        if r.code!=0:return self.fail(f'No log: {r.err.strip()}')
        for line in r.out.splitlines():
            if raw:self.out(line);continue
            try:e=json.loads(line)
            except ValueError:self.out(line);continue
            self.out(summarize_log([line]) if isinstance(e,dict) and e.get('event')=='status' else line[:300])
        return 0

    # ---- status
    def status_script(self):
        return ('echo host=$(hostname); echo uptime=$(uptime -p); echo commit=$(cat ~/aethertwin/src/SOURCE_COMMIT 2>/dev/null); '
                f"echo node=$(pgrep -af '{NODE_PATTERN}' | head -1); "
                'echo throttled=$(vcgencmd get_throttled 2>&1); echo temp=$(vcgencmd measure_temp 2>&1); echo ext5v=$(vcgencmd pmic_read_adc EXT5V_V 2>&1); '
                'echo carrier=$(cat /sys/class/net/eth0/carrier 2>/dev/null); echo eth_speed=$(cat /sys/class/net/eth0/speed 2>/dev/null); '
                f'echo lidar_ping=$(ping -c 1 -W 1 {lidar_ip(self.root,self.config)} >/dev/null 2>&1 && echo ok || echo no); '
                'for d in /sys/bus/usb/devices/*; do [ "$(cat $d/idVendor 2>/dev/null)" = 2bc5 ] && echo "camera=$(cat $d/product 2>/dev/null)|$(cat $d/speed 2>/dev/null)"; done; '
                f'echo svc_enabled=$(timeout 5 systemctl is-enabled {SERVICE} 2>/dev/null); echo svc_active=$(timeout 5 systemctl is-active {SERVICE} 2>/dev/null); '
                f'echo svc_pid=$(timeout 5 systemctl show -p MainPID --value {SERVICE} 2>/dev/null); '
                f'echo svc_since_us=$(timeout 5 systemctl show -p ActiveEnterTimestampMonotonic --value {SERVICE} 2>/dev/null); '
                f"echo boot_s=$(cut -d' ' -f1 /proc/uptime); echo svc_args=$(grep '^AETHERTWIN_CAPTURE_ARGS=' {SERVICE_DEFAULTS} 2>/dev/null | cut -d= -f2-); "
                f'echo disk=$(df -Pk ~ | tail -1); '
                f"if [ \"$(timeout 5 systemctl is-enabled {SERVICE} 2>/dev/null)\" = enabled ]; then {JOURNAL} -n 200 | grep '^{{' | tail -n 3; "
                f"else tail -n 3 {LOG} 2>/dev/null; fi | sed 's/^/log=/'")
    def status(self):
        r=self.ssh(self.status_script(),timeout=30,label='<health script: node, vcgencmd, eth0, USB 2bc5, df, log tail>')
        rows=[('Pi',self.pi)]
        if r.code!=0:
            rows.append(('reachable',f"NO ({(r.err.strip().splitlines() or ['exit '+str(r.code)])[-1]}); try `python tools/rig.py find`"));self.table(rows);return 1
        values={};log=[];cameras=[]
        for line in r.out.splitlines():
            k,_,v=line.partition('=')
            if k=='log':log.append(v)
            elif k=='camera':cameras.append(v)
            elif k:values[k]=v.strip()
        rows.append(('reachable',f"yes ({values.get('host')}, {values.get('uptime')})"))
        node=values.get('node','')
        if values.get('svc_enabled')=='enabled':
            active=values.get('svc_active') or '?';pid=values.get('svc_pid','')
            try:up=f", up {(float(values['boot_s'])-int(values['svc_since_us'])/1e6)/60:.1f} min" if active=='active' and int(values['svc_since_us'])>0 else ''
            except (KeyError,ValueError):up=''
            rows.append(('service',f"{SERVICE} enabled, {active}{', pid '+pid if pid not in ('','0') else ''}{up}; ARGS={values.get('svc_args','')!r}"))
        else:rows.append(('service',f"{SERVICE} not enabled ({values.get('svc_enabled') or 'not installed'}): node commands use start_node.sh"))
        rows.append(('node',f"running pid {node.split()[0]}, {'bring-up' if '--bringup' in node else 'LIVE'} mode" if node else 'not running'))
        if log:rows.append(('node log',summarize_log(log)))
        volts=re.search(r'=\s*([\d.]+)V',values.get('ext5v',''))
        rows+=[('power',f"{decode_throttled(values.get('throttled'))}; EXT5V {volts[1][:5]+' V' if volts else 'n/a'}"),
               ('temperature',values.get('temp','').replace('temp=','') or 'n/a'),
               ('LiDAR link',f"eth0 carrier {'up' if values.get('carrier')=='1' else 'DOWN'}{', '+values['eth_speed']+' Mb/s' if values.get('eth_speed','').isdigit() else ''}"
                             f", ping {lidar_ip(self.root,self.config)} {values.get('lidar_ping','?')}")]
        rows.append(('camera','; '.join(f"{c.split('|')[0] or 'Orbbec'} on {usb_speed(c.split('|')[-1])}" for c in cameras) or 'NO Orbbec (2bc5) device on USB'))
        disk=values.get('disk','').split()
        rows.append(('Pi disk',f"{int(disk[3])/1024**2:.1f} GB free of {int(disk[1])/1024**2:.1f} GB" if len(disk)>=4 and disk[3].isdigit() else 'n/a'))
        target=self.rec_root
        probe=next((p for p in (target,*target.parents) if p.exists()),None)
        if probe:rows.append(('laptop disk',f'{shutil.disk_usage(probe).free/1024**3:.1f} GB free for {target}'))
        rows.append(('source',self.commit_text(values.get('commit',''))));self.table(rows);return 0
    def commit_text(self,remote):
        head=self.git('rev-parse','HEAD').out.strip()
        if not remote:return f'no SOURCE_COMMIT on the Pi (local HEAD {head[:12]}): python tools/rig.py push'
        if remote==head:return f'Pi at local HEAD {head[:12]}'
        behind=self.git('rev-list','--count',f'{remote}..HEAD')
        return (f'Pi at {remote[:12]}, {behind.out.strip()} commit(s) behind local HEAD {head[:12]}' if behind.code==0 else
                f'Pi at {remote[:12]}, local HEAD {head[:12]} (not related here)')+'; python tools/rig.py push'
    def table(self,rows):
        width=max(len(k) for k,_ in rows)
        for k,v in rows:self.out(f'  {k.ljust(width)} : {v}')

    # ---- push
    def native_changed(self,old,head):
        """True/False if native/ changed between the Pi's commit and HEAD, None when that cannot be told."""
        if not old:return None
        if old==head:return False
        r=self.git('diff','--name-only',old,head,'--','native/')
        return None if r.code!=0 else bool(r.out.strip())
    def push(self,rebuild=False):
        head=self.git('rev-parse','HEAD').out.strip()
        if not head:return self.fail('Not a git checkout here')
        if self.git('status','--porcelain','--untracked-files=no').out.strip():self.out(f'Note: uncommitted changes are NOT sent; shipping {head[:12]}')
        old=self.ssh('cat ~/aethertwin/src/SOURCE_COMMIT 2>/dev/null',timeout=20).out.strip()
        running=self.node_state().running;remote=f'mkdir -p {SRC} && tar -x -C {SRC} && echo {head} > {SRC}/SOURCE_COMMIT'
        archive=['git','-C',str(self.root),'archive','--format=tar','HEAD']
        self.out(f"$ git archive --format=tar HEAD | ssh {self.pi} {remote!r}")
        r=self.run(self.ssh_command(remote),pipe=archive,timeout=1800)
        if r.code!=0:return self.fail(f'Push failed: {r.err.strip() or "exit "+str(r.code)}')
        changed=self.native_changed(old,head)
        self.out(f"Pi source at {head[:12]} (was {old[:12] or 'none'})")
        self.out({True:'native/ changed: rebuild the module (push --rebuild)',False:'native/ unchanged: no rebuild needed',
                  None:'native/ change unknown (previous commit not known here): rebuild to be safe (push --rebuild)'}[changed])
        if running:self.out('The node still runs the previous code: python tools/rig.py node restart')
        if rebuild:
            if running:return self.fail('Not rebuilding while the node runs the module: node stop, push --rebuild, node start',2)
            r=self.ssh(f'bash {SRC}/deployment/lab/pi_bootstrap.sh native',timeout=1800,stream=True)
            self.out('Native module rebuilt and imported' if r.code==0 else f'Native rebuild failed (exit {r.code})');return r.code
        return 0

    # ---- check
    def check(self,seconds=20.,only=None,stop_node=False):
        """The devices are exclusive: with --stop-node a running node is stopped first and started again afterwards in the same mode."""
        state=self.node_state()
        if not state.running:return self.device_check(seconds,only)
        if not stop_node:return self.fail('The node holds the L2 and the camera: pass --stop-node (it is started again afterwards in the same mode)',2)
        if state.bringup is None:return self.fail(f'The node is {state.active or "starting"} and its mode is not known yet; try again in a few seconds',2)
        live=not state.bringup;self.out(f"Stopping the node ({state.mode} mode) for the device check")
        if code:=self.node_stop():return code
        try:code=self.device_check(seconds,only)
        finally:
            self.out(f"Starting the node again in {state.mode} mode")
            again=self.node_start(live)
        return code or again
    def device_check(self,seconds,only):
        name=f"device_check_{only or 'all'}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        skip={'l2':' --skip gemini','gemini':' --skip l2'}.get(only,'')
        remote=f'cd {SRC} && {PY} tools/pi_device_check.py --config {self.config} --seconds {seconds:g}{skip} --output ~/{EVIDENCE_REMOTE}/{name}'
        r=self.ssh(remote,timeout=2*seconds+240)
        if r.code not in (0,1):return self.fail(f"Device check did not finish (exit {r.code}): {(r.err.strip().splitlines() or [''])[-1]}")
        self.evidence_dir.mkdir(parents=True,exist_ok=True);local=self.evidence_dir/name
        if self.scp([name],local).code!=0:return self.fail(f'Could not copy {name} back')
        try:report=json.loads(local.read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:return self.fail(f'Unreadable report {local}: {exc}')
        for line in summarize_device_check(report):self.out('  '+line)
        self.out(f'Report: {local}');return r.code

    # ---- record
    def record(self,name,duration,motion='stationary',live=False):
        if not SAFE_NAME.match(name):return self.fail(f'Recording name {name!r}: use letters, digits, . _ - only',2)
        out=self.rec_root/name
        if out.exists() and any(out.iterdir()):return self.fail(f'{out} exists and is not empty; choose a new name',2)
        if live and (problem:=self.calibration_problem()):return self.fail(problem,2)
        state=self.node_state()  # a running node (service or not) is reused, never doubled
        if state.running and state.bringup is None:return self.fail(f'The node is {state.active or "starting"}; wait for it (python tools/rig.py status)',2)
        if state.running and state.bringup==live:
            return self.fail(f"The node runs in {state.mode} mode; restart it: python tools/rig.py node restart{' --live' if live else ''}",2)
        if state.running:self.out(f"Using the running node (pid {state.procs[0][0]}, {state.mode} mode{', systemd service' if state.service else ''})")
        elif self.node_start(live)!=0:return 1
        cmd=[sys.executable,self.root/'tools'/'record_raw.py','--config',self.root/self.config,*([] if live else ['--bringup']),
             '--capture-host',self.host,'--duration',f'{duration:g}','--motion',motion,'--output',out]
        r=self.local(cmd,stream=True,timeout=duration+600)
        report=out/'capture_report.json'
        if report.exists():
            for line in summarize_capture(json.loads(report.read_text(encoding='utf-8'))):self.out('  '+line)
            self.out(f'Report: {report}')
        else:self.out(f'No capture_report.json in {out}')
        return r.code

    # ---- evidence
    def evidence(self,force=False):
        r=self.ssh(f'ls -1p ~/{EVIDENCE_REMOTE}',timeout=30)
        if r.code!=0:return self.fail(f'Cannot list the Pi evidence: {r.err.strip()}')
        names=[n.strip() for n in r.out.splitlines() if n.strip() and not n.strip().endswith('/')]
        odd=[n for n in names if not SAFE_NAME.match(n)];names=[n for n in names if SAFE_NAME.match(n)]
        self.evidence_dir.mkdir(parents=True,exist_ok=True)
        kept=[n for n in names if (self.evidence_dir/n).exists() and not force];new=[n for n in names if n not in kept]
        if new and self.scp(new,self.evidence_dir).code!=0:return self.fail('Copy failed')
        self.out(f"Pulled {len(new)} file(s) into {self.evidence_dir}"+(': '+', '.join(new) if new else ''))
        if kept:self.out(f"Kept {len(kept)} existing local file(s) (--force overwrites): {', '.join(kept)}")
        if odd:self.out(f"Skipped names with unusual characters: {', '.join(odd)}")
        return 0

    # ---- shutdown
    def shutdown(self,yes=False):
        if not yes:
            running=self.node_state().running
            prompt=f"Shut down {self.pi} now?{' The capture node is running and any recording from it stops.' if running else ''} [y/N] "
            if self.ask(prompt).strip().lower() not in ('y','yes'):self.out('Not shut down');return 1
        r=self.ssh('sudo -n shutdown -h now',timeout=30)
        if 'password' in r.err.lower():return self.fail('sudo wants a password on the Pi; shut it down from its own console')
        if r.code not in (0,255):return self.fail(f'Shutdown failed: {r.err.strip() or "exit "+str(r.code)}')
        self.out('Shutdown sent. Wait for the green activity LED to stop (about 10 s) before removing power.');return 0


def build_parser():
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0],formatter_class=argparse.RawDescriptionHelpFormatter,
                              epilog='\n'.join(__doc__.split('\n')[2:]))
    p.add_argument('--pi',help='user@host (default: the one `find` saved in .local/rig.json)')
    p.add_argument('--config',default=CONFIG,help='Profile, relative to the repository (same path on the Pi)')
    p.add_argument('--key',type=Path,default=KEY,help='SSH private key (used key-only, never with a password)')
    sub=p.add_subparsers(dest='command',required=True)
    f=sub.add_parser('find',help='Locate the Pi on the hotspot and remember it');f.add_argument('--user',default=USER);f.add_argument('--subnet',default=SUBNET)
    s=sub.add_parser('status',help='One screen of rig health');s.add_argument('--rec-root',type=Path)
    pu=sub.add_parser('push',help='Copy the committed HEAD to the Pi');pu.add_argument('--rebuild',action='store_true',help='Then rebuild the native module on the Pi')
    n=sub.add_parser('node',help='Start, stop, restart the capture node or show its log');n.add_argument('action',choices=['start','stop','restart','log'])
    n.add_argument('--live',action='store_true',help='Drop --bringup (refused without a measured calibration)')
    n.add_argument('-n','--lines',type=int,default=20);n.add_argument('--raw',action='store_true');n.add_argument('--follow',action='store_true')
    c=sub.add_parser('check',help='pi_device_check on the Pi, report pulled into reports/hardware/pi/');c.add_argument('--seconds',type=float,default=20.)
    c.add_argument('--only',choices=['l2','gemini']);c.add_argument('--stop-node',action='store_true',help='Stop a running node first, start it again afterwards (same mode)')
    r=sub.add_parser('record',help='Raw recording from the node (record_raw on this laptop)');r.add_argument('name');r.add_argument('--duration',type=float,required=True)
    r.add_argument('--motion',choices=['stationary','mobile'],default='stationary');r.add_argument('--live',action='store_true');r.add_argument('--rec-root',type=Path)
    e=sub.add_parser('evidence',help='Pull the Pi evidence folder without overwriting');e.add_argument('--force',action='store_true')
    e.add_argument('--dest',type=Path,help='Local folder (default reports/hardware/pi)')
    d=sub.add_parser('shutdown',help='Clean Pi shutdown');d.add_argument('--yes',action='store_true')
    return p


def main(argv=None,**rig_options):
    args=build_parser().parse_args(argv)
    rig=Rig(args.pi,key=args.key,config=args.config,evidence=getattr(args,'dest',None),rec_root=getattr(args,'rec_root',None),**rig_options)
    if args.command=='find':return rig.find(args.user,args.subnet)
    if args.command=='status':return rig.status()
    if args.command=='push':return rig.push(args.rebuild)
    if args.command=='node':
        if args.action=='start':return rig.node_start(args.live)
        if args.action=='stop':return rig.node_stop()
        if args.action=='restart':return rig.node_start(args.live,restart=True)
        return rig.node_log(args.lines,args.raw,args.follow)
    if args.command=='check':return rig.check(args.seconds,args.only,args.stop_node)
    if args.command=='record':return rig.record(args.name,args.duration,args.motion,args.live)
    if args.command=='evidence':return rig.evidence(args.force)
    return rig.shutdown(args.yes)


if __name__=='__main__':sys.exit(main())

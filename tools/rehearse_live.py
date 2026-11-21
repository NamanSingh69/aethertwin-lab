"""Rehearse the live run on this laptop: a recorded session served by tools/replay_capture_node.py on loopback, the
real command_center live service against it, sampled until it ends. A REHEARSAL, never a lab or hardware result.

    python tools/rehearse_live.py --recording %USERPROFILE%/aethertwin_rec/wifi_stationary_60s --twin <twin dir> \
        --calibration <calibration json> --duration 1800 [--device cuda] [--parked]

Safe when several agents share the machine (2026-11-05: two concurrent ad-hoc rehearsals on fixed ports and one key
let one host take over the other's replay node, and a pattern-based kill ended the other session's run):
- one rehearsal per machine at a time (a lock in the temp folder; a stale lock of a dead process is taken over);
- a fresh random key per run, so no other service can authenticate to this run's node;
- free ephemeral ports and a new run folder every time (never reused or overwritten);
- it stops only the process trees it started, by PID, never by command-line pattern;
- it writes monitor.jsonl as it goes and summary.json at the end itself, so the result survives the caller.
"""
from __future__ import annotations
import argparse,webbrowser,json,os,secrets,socket,subprocess,sys,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import psutil

ROOT=Path(__file__).resolve().parents[1]
LOCK=Path(os.environ.get('TEMP',os.environ.get('TMPDIR','/tmp')))/'aethertwin_rehearsal.lock'


def acquire_lock(run_dir,lock=LOCK):
    """Take the machine-wide rehearsal lock, or raise naming the holder. A lock whose process is gone is stale."""
    lock.parent.mkdir(parents=True,exist_ok=True)
    for _ in range(2):
        try:
            fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
            with os.fdopen(fd,'w') as f:json.dump({'pid':os.getpid(),'run_dir':str(run_dir),'started':time.time()},f)
            return lock
        except FileExistsError:
            try:holder=json.loads(lock.read_text())
            except (OSError,ValueError):holder={}
            if holder.get('pid') and psutil.pid_exists(int(holder['pid'])):
                raise RuntimeError(f"Another rehearsal is running (pid {holder['pid']}, {holder.get('run_dir')}); wait for it to finish")
            lock.unlink(missing_ok=True)  # stale: its process is gone
    raise RuntimeError('Could not take the rehearsal lock')


def release_lock(lock=LOCK):
    try:
        if json.loads(lock.read_text()).get('pid')==os.getpid():lock.unlink()
    except (OSError,ValueError):pass


def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def stop_tree(pid):
    """Stop one process and its descendants (the venv launcher stub and its interpreter), by PID only."""
    try:parent=psutil.Process(pid)
    except psutil.NoSuchProcess:return []
    procs=parent.children(recursive=True)+[parent]
    for p in procs:
        try:p.kill()
        except psutil.NoSuchProcess:pass
    psutil.wait_procs(procs,timeout=10);return [p.pid for p in procs]


def tree_rss_mb(pid):
    """RSS of the largest process in the tree: the real interpreter, not the ~4 MB venv launcher stub."""
    try:procs=[psutil.Process(pid)]+psutil.Process(pid).children(recursive=True)
    except psutil.NoSuchProcess:return None
    sizes=[]
    for p in procs:
        try:sizes.append(p.memory_info().rss)
        except psutil.NoSuchProcess:pass
    return max(sizes)/2**20 if sizes else None


def owns_port(pid,port):
    """True when the listener on port belongs to pid's process tree (not an orphan from an earlier run)."""
    try:tree={pid}|{p.pid for p in psutil.Process(pid).children(recursive=True)}
    except psutil.NoSuchProcess:return False
    # A listener has no remote address; psutil on Windows reports some listening sockets with status NONE, not LISTEN.
    return any(c.laddr and c.laddr.port==port and not c.raddr and c.pid in tree for c in psutil.net_connections('tcp'))


def get(port,path):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}',timeout=5) as r:return json.loads(r.read())
    except Exception as error:return {'error':f'{type(error).__name__}: {error}'}


def summarize(rows,duration):
    good=[r for r in rows if isinstance(r.get('state'),dict) and 'frame_id' in r['state']]
    if not good:return {'samples':0,'verdict':'no frames processed'}
    d=lambda r:r['state'].get('diagnostics',{})
    proc=np.array([d(r)['processing_ms'] for r in good if d(r).get('processing_ms') is not None],float)
    t=np.array([r['t'] for r in good]);rss=np.array([r.get('rss_mb') or np.nan for r in good],float);half=t>t[-1]/2
    last=good[-1];health=last.get('health') if isinstance(last.get('health'),dict) else {};runtime=health.get('runtime',{}) or {}
    raw=d(last).get('raw_transport',{});queue=d(last).get('acquisition_queue',{})
    modules={k:float(np.median([d(r)['module_ms'][k] for r in good if k in d(r).get('module_ms',{})])) for k in d(last).get('module_ms',{})}
    ok=bool(runtime.get('completed')) and not runtime.get('error') and t[-1]>=.95*duration
    return {'verdict':'ran the full duration without a runtime error' if ok else 'did NOT run the full duration cleanly',
        'monitored_span_s':float(t[-1]),'samples':len(good),'completed':runtime.get('completed'),'runtime_error':runtime.get('error'),
        'frame_errors':runtime.get('frame_errors'),'live_source':runtime.get('live_source'),
        'localization_valid_at_end':last['state'].get('validity',{}).get('localization'),
        'parked_localization':{k:(d(last).get('parked_localization') or {}).get(k) for k in ('accepted','fitness','rmse_m','correction_m','correction_deg')},
        'processing_ms':{'p50':float(np.percentile(proc,50)),'p95':float(np.percentile(proc,95)),'target_ms':35} if len(proc) else None,
        'module_ms_median':modules,'acquired_frames':queue.get('acquired'),'dropped_by_latest_frame_coalescing':queue.get('dropped_frames'),
        'transport':{k:raw.get(k) for k in ('state','segment','segments','bytes','clock_jumps','imu_sequence_breaks','cloud_missing','camera_missing')},
        'rss_mb':{'first':float(rss[0]),'last':float(rss[-1]),'max':float(np.nanmax(rss)),
                  'slope_second_half_mb_per_min':float(np.polyfit(t[half],rss[half],1)[0]*60) if half.sum()>2 and np.isfinite(rss[half]).all() else None}}


def write_config(run_dir,base,calibration,key_file,node_port,parked):
    """calibration None: an uncalibrated (bring-up, LiDAR-only) run on both ends."""
    lines=[f'extends: {Path(base).resolve().as_posix()}','live:',(f'  calibration: {Path(calibration).resolve().as_posix()}' if calibration else '  capture_bringup: true'),
           f'  capture_key_file: {Path(key_file).resolve().as_posix()}','  allow_synthetic_capture: true',f'  capture_port: {node_port}']
    if parked:lines.append('  parked: true')
    path=run_dir/'run.yaml';path.write_text('\n'.join(lines)+'\n',encoding='utf-8');return path


def studio_url(port):
    return f'http://127.0.0.1:{port}/'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--recording',type=Path,required=True);p.add_argument('--twin',type=Path,required=True)
    p.add_argument('--calibration',type=Path,help='calibration JSON announced by the replay node and loaded by the host; omit for a LiDAR-only (bring-up) rehearsal')
    p.add_argument('--duration',type=float,default=600);p.add_argument('--device',default='cuda');p.add_argument('--parked',action='store_true',default=True)
    p.add_argument('--mobile',dest='parked',action='store_false');p.add_argument('--base-config',type=Path,default=ROOT/'config/profiles/hotspot.yaml')
    p.add_argument('--service-python',default=str(ROOT/('.venv-gpu' if (ROOT/'.venv-gpu').exists() else '.venv')/('Scripts/python.exe' if os.name=='nt' else 'bin/python')))
    p.add_argument('--node-python',default=sys.executable);p.add_argument('--sample-seconds',type=float,default=10)
    p.add_argument('--open-browser',action='store_true',help='open the studio in the default browser once the service is up (demo fallback)')
    p.add_argument('--out-root',type=Path,default=Path(os.environ.get('USERPROFILE',str(Path.home())))/'aethertwin_rehearsals')
    a=p.parse_args(argv)
    run_dir=a.out_root/f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}";run_dir.mkdir(parents=True,exist_ok=False)
    acquire_lock(run_dir);started=[]
    try:
        key=run_dir/'loopback.key';key.write_text(secrets.token_hex(32)+'\n',encoding='ascii')
        node_port,service_port=free_port(),free_port();config=write_config(run_dir,a.base_config,a.calibration,key,node_port,a.parked)
        meta={'kind':'live_replay_rehearsal','label':'REHEARSAL: recorded data replayed over the real transport on loopback; NOT live hardware, NOT a lab result',
              'commit':subprocess.run(['git','rev-parse','--short','HEAD'],capture_output=True,text=True,cwd=ROOT).stdout.strip(),
              'recording':str(a.recording),'twin':str(a.twin),'calibration':str(a.calibration) if a.calibration else 'none (LiDAR-only, bring-up)','duration_s':a.duration,'device':a.device,'parked':a.parked,
              'node_port':node_port,'service_port':service_port,'started_utc':datetime.now(timezone.utc).isoformat()}
        (run_dir/'run.json').write_text(json.dumps(meta,indent=2),encoding='utf-8');print(json.dumps(meta),flush=True)
        node=subprocess.Popen([a.node_python,str(ROOT/'tools/replay_capture_node.py'),'--recording',str(a.recording),'--key-file',str(key),
            *(['--calibration',str(a.calibration)] if a.calibration else []),'--port',str(node_port)],cwd=ROOT,stdout=open(run_dir/'node.log','w'),stderr=subprocess.STDOUT)
        started.append(node.pid);time.sleep(8)
        service=subprocess.Popen([a.service_python,'-m','command_center','--mode','live','--config',str(config),'--twin',str(a.twin),
            '--capture-host','127.0.0.1','--device',a.device,'--port',str(service_port),'--duration',str(a.duration),'--output',str(run_dir/'service')]
            +(['--parked'] if a.parked else [])+([] if a.calibration else ['--lidar-only']),cwd=ROOT,stdout=open(run_dir/'service.log','w'),stderr=subprocess.STDOUT,env={**os.environ,'PYTHONUNBUFFERED':'1'})
        started.append(service.pid);deadline=time.monotonic()+300
        while not owns_port(service.pid,service_port):
            if service.poll() is not None or time.monotonic()>deadline:raise RuntimeError(f'Service did not start; see {run_dir/"service.log"}')
            time.sleep(2)
        print(f'Studio: {studio_url(service_port)}',flush=True)
        if a.open_browser:webbrowser.open(studio_url(service_port))
        t0=time.monotonic();rows=[]
        with open(run_dir/'monitor.jsonl','a') as log:
            while time.monotonic()-t0<a.duration+60:
                state=get(service_port,'/api/v2/state')
                # Keep what the summary needs; a full state carries voxel arrays (~0.6 MB a sample).
                state={k:state[k] for k in ('frame_id','timestamp','fps','validity','diagnostics','error') if k in state} if isinstance(state,dict) else state
                row={'t':round(time.monotonic()-t0,1),'rss_mb':tree_rss_mb(service.pid),'state':state,'health':get(service_port,'/api/v2/health')}
                rows.append(row);log.write(json.dumps(row,default=str)+'\n');log.flush()
                runtime=row['health'].get('runtime',{}) if isinstance(row['health'],dict) else {}
                if runtime.get('completed') or service.poll() is not None:break
                time.sleep(a.sample_seconds)
        summary={**meta,**summarize(rows,a.duration),'not_shown':['Wi-Fi delay or loss (loopback)','a measured calibration unless one was given',
            'Pi temperature or power','moving people unless the recording has them']}
        (run_dir/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8');print(json.dumps(summary,indent=1),flush=True)
        return 0 if summary.get('verdict','').startswith('ran the full') else 1
    finally:
        for pid in reversed(started):stop_tree(pid)
        release_lock()


if __name__=='__main__':raise SystemExit(main())

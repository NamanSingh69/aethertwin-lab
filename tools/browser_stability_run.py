"""Run the real browser service, record bounded evidence, then shut down cleanly."""
from __future__ import annotations
import argparse,csv,ctypes,hashlib,json,os,sys,threading,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def source_hashes():
    paths=[ROOT/'main.py',ROOT/'pyproject.toml',ROOT/'tools/browser_stability_run.py']
    for name in ('core','ingestion','slam_and_twin','perception_ml','tracking_and_navigation',
                 'visualization','command_center','hardware_halo','diagnostics','native','config','web/dist'):
        paths.extend(p for p in (ROOT/name).rglob('*') if p.is_file()
            and '__pycache__' not in p.parts and p.suffix in ('.py','.yaml','.json','.dll','.so','.c','.js','.css','.html'))
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}


def active_seconds():
    if sys.platform!='win32':return None
    value=ctypes.c_ulonglong()
    if not ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(value)):raise ctypes.WinError()
    return value.value/10_000_000


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--duration',type=float,default=1800)
    p.add_argument('--config',type=Path,default=ROOT/'config/profiles/laptop.yaml')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--record',type=Path)
    p.add_argument('--port',type=int,default=8080);p.add_argument('--threads',type=int,default=4)
    args=p.parse_args(argv)
    if not 1<=args.duration<=7200 or args.threads<1:p.error('Use duration1..7200 and positive threads')
    os.environ.setdefault('OMP_NUM_THREADS',str(args.threads));os.environ.setdefault('MKL_NUM_THREADS',str(args.threads))
    import torch,psutil,uvicorn
    torch.set_num_threads(args.threads)
    from core.configuration import load_config
    from command_center.runtime import BrowserRuntime
    from command_center.service import create_app,SceneHub
    from command_center.commissioning import CommissioningManager
    cfg=load_config(args.config);args.output.mkdir(parents=True,exist_ok=True)
    before=source_hashes();checkpoint=ROOT/cfg['perception']['checkpoint']
    (args.output/'run_provenance.json').write_text(json.dumps({'started_utc':datetime.now(timezone.utc).isoformat(),
        'source_sha256':before,'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        'configuration':cfg,'mode':'sim','hardware_verified':False},indent=2),encoding='utf-8')
    runtime=BrowserRuntime(cfg,root=ROOT,mode='sim',output=args.output,record=args.record,duration=args.duration)
    manager=CommissioningManager(cfg,root=ROOT,output_root=args.output/'commissioning',
        sources={'simulation':{'mode':'sim','label':'Synthetic commissioning / fixed rig','static_scene':True,'fixed_position':True}},
        twins={'active':runtime.twin_path},correspondence_files={'synthetic-example':{
            'path':ROOT/'assets/calibration/synthetic_pairs.npz','label':'Synthetic calibration fixture','measured':False,'independent_validation':False}})
    hub=SceneHub(cfg['mapping']['voxel_size']);app=create_app(runtime,hub=hub,asset_root=runtime.asset_root,
        frontend_root=ROOT/'web/dist',commissioning=manager)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=args.port,access_log=False,proxy_headers=False))
    process=psutil.Process();stop=threading.Event();samples=[];clock=time.monotonic();active=active_seconds()
    def observe():
        with (args.output/'process_samples.csv').open('w',newline='',encoding='utf-8') as stream:
            writer=csv.writer(stream);writer.writerow(['elapsed_s','rss_mb','cpu_percent','threads','request_ms','frames','clients','network_queue','record_drops','error'])
            while not stop.is_set():
                started=time.monotonic();health={};error=None
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/api/v2/health',timeout=3) as response:health=json.load(response)
                except Exception as exc:error=str(exc)
                elapsed=time.monotonic()-clock;req_ms=1000*(time.monotonic()-started)
                rt=health.get('runtime',{});telemetry=health.get('telemetry',{})
                row=[elapsed,process.memory_info().rss/1024**2,process.cpu_percent(),process.num_threads(),req_ms,
                    rt.get('replay',{}).get('current_frame',-1)+1,telemetry.get('clients'),telemetry.get('queue_depth'),
                    (rt.get('recording') or {}).get('dropped_frames'),error or rt.get('error') or telemetry.get('error')]
                writer.writerow(row);stream.flush();samples.append(row)
                if rt.get('completed') or runtime.error or elapsed>args.duration+60:server.should_exit=True
                stop.wait(max(0,1-(time.monotonic()-started)))
    watcher=threading.Thread(target=observe,name='bounded-browser-observer',daemon=False)
    if sys.platform=='win32' and not ctypes.windll.kernel32.SetThreadExecutionState(0x80000003):raise ctypes.WinError()
    watcher.start()
    try:server.run()
    finally:
        stop.set();watcher.join(timeout=5);runtime.close()
        wall=time.monotonic()-clock;active_end=active_seconds()
        if sys.platform=='win32':ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        after=source_hashes();changes=[key for key in before.keys()|after.keys() if before.get(key)!=after.get(key)]
        sleep=None if active is None else max(0,wall-(active_end-active))
        summary={'schema_version':1,'wall_seconds':wall,'active_seconds':None if active is None else active_end-active,
            'suspended_seconds':sleep,'interrupted_by_suspend':None if sleep is None else sleep>5,'power_settings_changed':False,
            'source_changes_during_run':changes,'samples':len(samples),'runtime_error':runtime.error,
            'completed':runtime.completed,'final_telemetry':hub.status(),'recording':None if runtime.recorder is None else runtime.recorder.status(),
            'hardware_verified':False}
        (args.output/'run_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return 0 if runtime.completed and not runtime.error else 2


if __name__=='__main__':raise SystemExit(main())

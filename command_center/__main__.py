"""Run the AetherTwin local/private browser command center."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import yaml


def main(argv=None):
    from core.resources import resource_root
    root=resource_root()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['sim','replay','live'],default='sim')
    parser.add_argument('--config',type=Path,default=root/'config/profiles/laptop.yaml')
    parser.add_argument('--input',type=Path);parser.add_argument('--twin',type=Path);parser.add_argument('--checkpoint',type=Path)
    parser.add_argument('--record',type=Path,help='Record raw source segments under a new session directory')
    parser.add_argument('--commissioning-static-recording',type=Path,help='Declare an explicitly static recording for commissioning jobs')
    parser.add_argument('--calibration-correspondences',type=Path,help='Register measured correspondence NPZ for candidate export')
    parser.add_argument('--output',type=Path,default=Path('artifacts/browser'))
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8080)
    parser.add_argument('--device',choices=['cpu','cuda','auto']);parser.add_argument('--threads',type=int,default=4)
    parser.add_argument('--max-frames',type=int);parser.add_argument('--seed',type=int)
    parser.add_argument('--duration',type=float,help='Bound processing by wall-clock seconds; keep the viewer available')
    parser.add_argument('--ssl-certfile',type=Path);parser.add_argument('--ssl-keyfile',type=Path)
    parser.add_argument('--allowed-origin',action='append',default=[],help='Explicit trusted HTTPS gateway origin; may repeat')
    parser.add_argument('--access-log',action='store_true',help='Log each HTTP request (off by default)')
    parser.add_argument('--open-browser',action='store_true',help='Open the local command center after the server is ready')
    args=parser.parse_args(argv)
    from .auth import is_loopback
    if bool(args.ssl_certfile)!=bool(args.ssl_keyfile):parser.error('Both TLS certificate and key are required')
    if not is_loopback(args.host) and not args.ssl_certfile:parser.error('Private network listeners require TLS certificate and key')
    if args.threads<1:parser.error('--threads must be positive')
    os.environ.setdefault('OMP_NUM_THREADS',str(args.threads));os.environ.setdefault('MKL_NUM_THREADS',str(args.threads))
    import torch
    torch.set_num_threads(args.threads)
    from core.configuration import load_config
    cfg=load_config(args.config)
    if args.seed is not None:cfg['seed']=args.seed
    device=args.device or cfg['device']
    cfg['device']='cuda' if device=='auto' and torch.cuda.is_available() else ('cpu' if device=='auto' else device)
    if cfg['device']=='cuda' and not torch.cuda.is_available():raise RuntimeError('CUDA requested but unavailable')
    from .auth import SessionAuth
    # Reject unsafe exposure before opening sensors or allocating the model.
    SessionAuth(args.host)
    from .runtime import BrowserRuntime
    from .service import create_app,SceneHub
    runtime=BrowserRuntime(cfg,root=root,mode=args.mode,recording=args.input,twin=args.twin,output=args.output,
                           checkpoint=args.checkpoint,max_frames=args.max_frames,record=args.record,duration=args.duration)
    from .commissioning import CommissioningManager
    sources={'simulation':{'mode':'sim','label':'Dedicated simulation commissioning scans','static_scene':True,
        'fixed_position':not cfg['simulation'].get('mobile',False)}}
    sources.update({key:{'mode':'replay','label':key,'input_path':path,'static_scene':False} for key,path in runtime.recordings.items()})
    if args.commissioning_static_recording is not None:
        sources['static-input']={'mode':'replay','label':'Declared static recording','input_path':args.commissioning_static_recording,'static_scene':True}
    correspondence_files={} if args.calibration_correspondences is None else {
        'measured-input':{'path':args.calibration_correspondences,'measured':True,'independent_validation':False}}
    try:
        commissioning=CommissioningManager(cfg,root=root,output_root=args.output/'commissioning',sources=sources,
            twins={'active':runtime.twin_path},correspondence_files=correspondence_files)
        app=create_app(runtime,host=args.host,asset_root=runtime.asset_root,frontend_root=root/'web/dist',
                       hub=SceneHub(cfg['mapping']['voxel_size']),
                       allowed_origins=tuple(args.allowed_origin)+('http://127.0.0.1:5173','http://localhost:5173'),commissioning=commissioning)
    except BaseException:runtime.close();raise
    import uvicorn
    from .auth import configure_audit_log
    configure_audit_log()
    if args.open_browser:
        import threading,time,urllib.request,webbrowser
        browser_host='127.0.0.1' if args.host=='0.0.0.0' else args.host
        if ':' in browser_host:browser_host='['+browser_host+']'
        url=f'{"https" if args.ssl_certfile else "http"}://{browser_host}:{args.port}/'
        def open_when_ready():
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                try:
                    with urllib.request.urlopen(url,timeout=1) as response:
                        if response.status==200:webbrowser.open(url);return
                except OSError:time.sleep(.2)
        threading.Thread(target=open_when_ready,name='open-local-studio',daemon=True).start()
    try:uvicorn.run(app,host=args.host,port=args.port,proxy_headers=False,ws_max_size=4096,access_log=args.access_log,
                    ssl_certfile=None if args.ssl_certfile is None else str(args.ssl_certfile),
                    ssl_keyfile=None if args.ssl_keyfile is None else str(args.ssl_keyfile))
    finally:runtime.close()
    if runtime.error:raise RuntimeError(runtime.error)


if __name__=='__main__':main()

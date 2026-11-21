"""Record raw synchronised frames from the Pi capture node, without the processing pipeline.

Use it for the transport evidence recordings (1- and 10-minute, stationary and moving) and, with
--bringup, for calibration-target captures before any calibration exists. It writes a normal
recording (replayable with ReplaySource) plus capture_report.json with the transport counters,
the node identity and this host's environment. A recording that could not keep up is marked
incomplete (recording_status.json) and the tool exits non-zero; frames are never invented.

    python tools/record_raw.py --output D:/aethertwin/rec/stationary_60s --duration 60 --motion stationary
    python tools/record_raw.py --output D:/aethertwin/rec/calib_targets --duration 300 --bringup
"""
from __future__ import annotations
import argparse,json,os,platform,sys,time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--config',type=Path,default=ROOT/'config/system_config.yaml')
    p.add_argument('--output',type=Path,required=True,help='New recording directory (on the desktop SSD)')
    p.add_argument('--duration',type=float,required=True,help='Seconds to record')
    p.add_argument('--motion',choices=['stationary','mobile'],default='stationary')
    p.add_argument('--bringup',action='store_true',help='No calibration yet (calibration-target captures); the node must run --bringup')
    p.add_argument('--capture-host');p.add_argument('--allow-synthetic',action='store_true',help='Accept a --synthetic node (rehearsals only)')
    p.add_argument('--compression-level',type=int,help='Deflate level 0-9 (default: recording.compression_level, else 1)')
    p.add_argument('--status-seconds',type=float,default=5.)
    args=p.parse_args(argv)
    from core.configuration import load_config
    from core.async_recording import AsyncRecorder
    from ingestion.live_source import open_live_source
    from ingestion.recorder_replayer import FrameRecorder
    import numpy as np
    config=load_config(args.config);live=dict(config['live']);live['source']='remote'
    if args.capture_host:live['capture_host']=args.capture_host
    if args.bringup:live['capture_bringup']=True
    if args.allow_synthetic:live['allow_synthetic_capture']=True
    options=config.get('recording',{})
    level=args.compression_level if args.compression_level is not None else options.get('compression_level',1)
    if args.output.exists() and any(args.output.iterdir()):raise SystemExit(f'{args.output} exists and is not empty; choose a new directory')
    source=open_live_source(live,ROOT)
    identity=source.client.identity or {}
    metadata={'motion_mode':args.motion,'recording_policy':'synchronized_raw_frames','source':'pi_capture_node',
              'calibration':'none (bring-up: factory intrinsics in frame diagnostics only)' if args.bringup else live.get('calibration'),
              'node_identity':{k:identity.get(k) for k in ('host','node_session','node_version','boot_id','platform','devices','codecs','calibration')}}
    recorder=AsyncRecorder(FrameRecorder(args.output,chunk_size=options.get('chunk_frames',4),max_buffer_bytes=options.get('chunk_bytes',64*1024**2),
        max_disk_bytes=options.get('max_disk_bytes',16*1024**3),metadata=metadata,compression_level=level),
        max_frames=max(16,int(options.get('queued_frames',8))),max_bytes=max(256*1024**2,int(options.get('queued_bytes',64*1024**2))),required=True)
    started=time.monotonic();last=started;frames=0;timeline=[];error=None;first_ts=last_ts=None
    try:
        for frame in source:
            if not recorder.write(frame):error='recording could not keep up (see recording_status.json)';break
            frames+=1;first_ts=frame.timestamp if first_ts is None else first_ts;last_ts=frame.timestamp
            now=time.monotonic()
            if now-last>=args.status_seconds:
                last=now;s=source.status()
                row={'t':round(now-started,1),'frames':frames,'segment':s['segment'],'rates_hz':s['rates_hz'],
                     'bandwidth_mb_s':None if s['bandwidth_bytes_per_second'] is None else s['bandwidth_bytes_per_second']/1e6,
                     'camera_missing':s['camera_missing'],'cloud_missing':s['cloud_missing'],'imu_breaks':s['imu_sequence_breaks'],
                     'recorder':recorder.status()}
                timeline.append(row);print(json.dumps({k:row[k] for k in ('t','frames','segment','bandwidth_mb_s','camera_missing','imu_breaks')}),flush=True)
            if now-started>=args.duration:break
    except BaseException as exc:error=f'{type(exc).__name__}: {exc}'
    finally:
        status=source.status()
        try:source.close()
        except BaseException as exc:error=error or f'close: {exc}'
        try:recorder.close()
        except BaseException as exc:error=error or f'recorder: {exc}'
    elapsed=time.monotonic()-started
    import cv2
    report={'schema_version':1,'kind':'raw_capture','date_utc':datetime.now(timezone.utc).isoformat(),
        'label':'real devices via the Pi capture node' if not (identity.get('devices') or {}).get('synthetic') else 'SYNTHETIC node (rehearsal), not a hardware result',
        'bringup_uncalibrated':args.bringup,'motion':args.motion,'duration_requested_s':args.duration,'elapsed_s':elapsed,
        'frames':frames,'frames_hz':frames/elapsed if elapsed else None,'sensor_time_span_s':None if first_ts is None else last_ts-first_ts,
        'error':error,'recorder':recorder.status(),'transport':status,'timeline':timeline,
        'host':{'name':platform.node(),'platform':platform.platform(),'processor':platform.processor(),'cpu_count':os.cpu_count(),
                'python':platform.python_version(),'numpy':np.__version__,'opencv':cv2.__version__},
        'compression_level':level}
    (args.output/'capture_report.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('label','frames','frames_hz','error')}|{'segments':status['segment'],'camera_missing':status['camera_missing'],
        'cloud_missing':status['cloud_missing'],'imu_breaks':status['imu_sequence_breaks']},default=str))
    return 0 if error is None and report['recorder']['complete'] else 1


if __name__=='__main__':sys.exit(main())

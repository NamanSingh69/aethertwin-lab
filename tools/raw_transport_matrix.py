"""Offered load and CPU of the raw-sensor transport per camera profile and codec, one loopback soak each.

Synthetic devices over loopback on this host (tools/raw_transport_soak.py per row): it measures
what the capture node would offer a link, and the CPU both ends spend, not what a Wi-Fi link or
a Raspberry Pi achieves. Synthetic depth is a smooth surface with 3 mm noise and synthetic colour
a gradient, so real compression ratios (holes, texture, MJPG from the camera) will differ.

    python tools/raw_transport_matrix.py --duration 60 --out reports/raw_transport/hotspot_codec_matrix_windows_laptop.json
"""
from __future__ import annotations
import argparse,json,subprocess,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
# name, width, height, depth codec, colour codec, colour quality, camera forward
ROWS=(('wired_default_1280x800_raw_device',1280,800,'raw','device',90,'all'),
      ('1280x800_zlib_device',1280,800,'zlib','device',90,'all'),
      ('1280x800_png_device',1280,800,'png','device',90,'all'),
      ('1280x800_zlib_jpeg80_sweep',1280,800,'zlib','jpeg',80,'sweep'),
      ('848x480_zlib_jpeg80_sweep',848,480,'zlib','jpeg',80,'sweep'),
      ('640x400_raw_jpeg80_all',640,400,'raw','jpeg',80,'all'),
      ('640x400_zlib_jpeg80_all',640,400,'zlib','jpeg',80,'all'),
      ('640x400_png_jpeg80_sweep',640,400,'png','jpeg',80,'sweep'),
      ('hotspot_profile_640x400_zlib_jpeg80_sweep',640,400,'zlib','jpeg',80,'sweep'),
      ('fallback_424x240_zlib_jpeg80_sweep',424,240,'zlib','jpeg',80,'sweep'))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--duration',type=float,default=60.);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--only',action='append',help='Run only rows whose name contains this (repeatable)')
    p.add_argument('--fragment-bytes',type=int,default=65536);p.add_argument('--port',type=int,default=7621)
    p.add_argument('--reuse',type=Path,help='Directory of per-row soak reports: keep the rows already there, run the missing ones')
    args=p.parse_args(argv)
    work=args.reuse or Path(tempfile.mkdtemp(prefix='aethertwin-matrix-'));work.mkdir(parents=True,exist_ok=True);rows=[];environment=None
    print(f'per-row soak reports in {work}',flush=True)
    for name,width,height,depth,color,quality,forward in ROWS:
        if args.only and not any(o in name for o in args.only):continue
        out=work/f'{name}.json'
        cmd=[sys.executable,str(ROOT/'tools/raw_transport_soak.py'),'--duration',str(args.duration),'--out',str(out),'--width',str(width),'--height',str(height),
             '--fps','30','--depth-codec',depth,'--color-codec',color,'--color-quality',str(quality),'--camera-forward',forward,
             '--fragment-bytes',str(args.fragment_bytes),'--port',str(args.port),'--sample-seconds','5']
        if not out.exists():print(f'== {name}',flush=True);subprocess.run(cmd,cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT)
        report=json.loads(out.read_text(encoding='utf-8'));s=report['summary'];environment=report['environment'];devices=s.get('node_devices') or {}
        bench=report['codec_bench'];elapsed=s['elapsed_seconds']
        passed=not s['consumer_errors'] and s['imu_sequence_breaks']==0 and s['segments']==1
        rows.append({'name':name,'soak_passed':passed,'elapsed_seconds':elapsed,'consumer_errors':s['consumer_errors'],'camera':{'width':width,'height':height,'fps':30},'depth_codec':depth,'color_codec':color,
            'color_quality':quality if color=='jpeg' else None,'camera_forward':forward,
            'offered_megabytes_per_second':s['bandwidth_megabytes_per_second'],
            'offered_megabits_per_second_mean':8*s['bandwidth_megabytes_per_second']['mean'] if s['bandwidth_megabytes_per_second']['mean'] else None,
            'camera_messages_hz':s['camera_frames']/elapsed if elapsed else None,'camera_skipped_at_node':devices.get('camera_skipped'),
            'camera_message_bytes_p50':devices.get('camera_message_bytes_p50'),
            'node_camera_encode_ms':{'p50':devices.get('camera_encode_ms_p50'),'p95':devices.get('camera_encode_ms_p95')},
            'cpu_percent_of_one_core':s['cpu_percent_of_one_core'],'fused_frames_hz':s['delivered_hz'],
            'segments':s['segments'],'imu_sequence_breaks':s['imu_sequence_breaks'],'camera_missing':s['camera_missing'],
            'clouds':s['clouds'],'cloud_missing':s['cloud_missing'],'receiver_overflows':s['receiver_overflows'],
            'codec_bench_single_frame':{'depth':bench.get(f'depth_{depth}'),'color':bench.get(f'color_{color}')}})
        print(json.dumps({k:rows[-1][k] for k in ('offered_megabits_per_second_mean','camera_messages_hz','cpu_percent_of_one_core','fused_frames_hz')}),flush=True)
    report={'schema_version':1,'kind':'raw_transport_codec_matrix',
        'label':'SYNTHETIC devices over LOOPBACK on this host: offered load and CPU only; not a Wi-Fi, Raspberry Pi or sensor result',
        'date_utc':datetime.now(timezone.utc).isoformat(),'environment':environment,
        'settings':{'duration_seconds_per_row':args.duration,'imu_hz':250,'lidar_hz':10,'points_per_sweep':6400,'camera_fps':30,'fragment_bytes':args.fragment_bytes,
                    'sync_tolerance_seconds':.02,'cpu_note':'percent of one logical core, psutil, mean of 5 s windows after the first'},
        'rows':rows}
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8',newline='\n')
    return 0 if all(r['soak_passed'] for r in rows) else 1


if __name__=='__main__':sys.exit(main())

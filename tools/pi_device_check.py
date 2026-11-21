"""First device checks on the Pi capture node: the L2 and the Gemini through the same drivers the node uses.

Runs on the Pi (capture venv), one device at a time, and writes one JSON report:
  * system: model, kernel, throttling and temperature before and after, the USB tree (camera speed);
  * L2: sweeps, points per sweep, sweep period and duration, IMU rate and gravity magnitude, range spread;
  * Gemini (bring-up: no calibration, factory intrinsics as metadata only): device identity, frame rate,
    image sizes, depth validity and spread.
Nothing is configured on either device and nothing is written outside --output.

    ~/aethertwin/capture-env/bin/python tools/pi_device_check.py --seconds 20 --output ~/aethertwin/evidence/device_check.json
"""
from __future__ import annotations
import argparse,json,platform,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def run(*command):
    try:return subprocess.run(command,capture_output=True,text=True,timeout=10).stdout.strip()
    except Exception as exc:return f'unavailable: {exc}'


def system_state():
    return {'throttled':run('vcgencmd','get_throttled'),'temp':run('vcgencmd','measure_temp'),'uptime':run('uptime')}


def spread(values):
    import numpy as np
    values=np.asarray(values,float)
    if not len(values):return None
    return {k:float(v) for k,v in zip(('min','p5','median','p95','max'),np.percentile(values,[0,5,50,95,100]))}


def check_l2(live,seconds):
    import numpy as np
    from ingestion.lidar_l2_driver import LidarL2Driver
    driver=LidarL2Driver(live['lidar_ip'],live['host_ip'],live['lidar_port'],live['host_port'],timeout=30.)
    starts=[];points=[];durations=[];ranges=[];imu_times=set();gravity=[]
    try:
        opened=time.monotonic();first=driver.next_frame();first_sweep_s=time.monotonic()-opened;driver.timeout=5.  # spin-up can take ~20 s
        starts.append(first.timestamp);points.append(len(first.lidar_points));durations.append(first.sweep_duration)
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            frame=driver.next_frame();starts.append(frame.timestamp);points.append(len(frame.lidar_points));durations.append(frame.sweep_duration)
            if len(frame.lidar_points):ranges.extend(np.linalg.norm(frame.lidar_points[::50,:3],axis=1).tolist())
            for s in frame.imu_samples:
                if s.timestamp not in imu_times:imu_times.add(s.timestamp);gravity.append(float(np.linalg.norm(s.accel)))
        dropped,rejected=driver.dropped,driver.rejected
    finally:driver.close()
    imu=sorted(imu_times);periods=np.diff(starts) if len(starts)>1 else []
    return {'first_sweep_after_open_s':first_sweep_s,'sweeps':len(starts),'sweep_hz':(len(starts)-1)/(starts[-1]-starts[0]) if len(starts)>1 and starts[-1]>starts[0] else None,
            'sweep_period_s':spread(periods),'sweep_duration_s':spread(durations),'points_per_sweep':spread(points),
            'imu_samples':len(imu),'imu_hz':(len(imu)-1)/(imu[-1]-imu[0]) if len(imu)>1 and imu[-1]>imu[0] else None,
            'imu_accel_norm':spread(gravity),'range_m':spread(ranges),'dropped_clouds':dropped,'rejected_long_clouds':rejected,
            'monotonic_sweeps':bool(np.all(np.diff(starts)>0)) if len(starts)>1 else None}


def check_gemini(live,seconds):
    import numpy as np
    from ingestion.gemini_333l_driver import Gemini335LDriver
    size=(live.get('camera_color_width') or live['camera_width'],live.get('camera_color_height') or live['camera_height'])
    camera=Gemini335LDriver(None,live['camera_width'],live['camera_height'],live['camera_fps'],color_width=live.get('camera_color_width'),
        color_height=live.get('camera_color_height'),color_fps=live.get('camera_color_fps'),calibration_image_size=size,
        align_mode=live.get('camera_align_mode','auto'),bringup=True)
    stamps=[];valid=[];depth=[];shapes=set()
    try:
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            timestamp,rgb,depth_m,_=camera.read();stamps.append(timestamp);shapes.add((tuple(rgb.shape),tuple(depth_m.shape)))
            good=depth_m>0;valid.append(float(good.mean()))
            if good.any():depth.extend(depth_m[good][::997].tolist())
        profile=json.loads(json.dumps(getattr(camera,'profile_metadata',{}),default=str));metadata=json.loads(json.dumps(camera.last_metadata,default=str))
    finally:camera.close()
    return {'frames':len(stamps),'fps':(len(stamps)-1)/(stamps[-1]-stamps[0]) if len(stamps)>1 and stamps[-1]>stamps[0] else None,
            'shapes':[list(map(list,s)) for s in shapes],'depth_valid_fraction':spread(valid),'depth_m':spread(depth),
            'profile':profile,'last_metadata':metadata}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--config',type=Path,default=ROOT/'config/system_config.yaml');p.add_argument('--seconds',type=float,default=20.)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--skip',choices=['l2','gemini'],action='append',default=[])
    args=p.parse_args(argv)
    from core.configuration import load_config
    live=load_config(args.config)['live']
    report={'schema_version':1,'kind':'pi_device_check','date_utc':datetime.now(timezone.utc).isoformat(),'label':'physical devices on the Pi capture node',
            'hardware_verified':False,'host':{'name':platform.node(),'platform':platform.platform(),'python':platform.python_version(),
            'model':Path('/proc/device-tree/model').read_text(errors='replace').strip('\x00') if Path('/proc/device-tree/model').exists() else None},
            'system_before':system_state(),'usb_tree':run('lsusb','-t'),'usb':run('lsusb')}
    for name,check in (('l2',check_l2),('gemini',check_gemini)):
        if name in args.skip:continue
        try:report[name]=check(live,args.seconds)
        except Exception as exc:report[name]={'error':f'{type(exc).__name__}: {exc}'}
        print(json.dumps({name:{k:v for k,v in report[name].items() if k not in ('profile','last_metadata')}},default=str),flush=True)
    report['system_after']=system_state()
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'system_before':report['system_before'],'system_after':report['system_after']}))
    return 0 if all('error' not in report.get(n,{}) for n in ('l2','gemini') if n not in args.skip) else 1


if __name__=='__main__':sys.exit(main())

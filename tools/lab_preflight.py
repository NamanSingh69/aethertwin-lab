"""Laptop-side lab-day preflight: one command on arrival, PASS / WARN / FAIL with a fix for each check.

    python tools/lab_preflight.py            # laptop only (the Pi side is `python tools/rig.py status`)
    python tools/lab_preflight.py --json reports/lab_preflight.json

Checks: CPU and GPU environments (CUDA really computes), branch clean and pushed, free disk for the day's recordings,
profiles load, capture key and calibration files present (existence only; the key is never read), mains power,
ESP32 serial port, no rehearsal lock or stray live/replay processes. Exit 0 all pass, 1 warnings, 2 a failure.
"""
from __future__ import annotations
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
WIN=os.name=='nt'
VENV=ROOT/'.venv'/('Scripts/python.exe' if WIN else 'bin/python');GPU_VENV=ROOT/'.venv-gpu'/('Scripts/python.exe' if WIN else 'bin/python')
RECORDINGS=Path(os.environ.get('USERPROFILE',str(Path.home())))/'aethertwin_rec'
# A 600 s recording is ~5.6 GB (reports/hardware/pi); the day needs self-mask, calibration, two moving and one
# commissioning recording plus a twin build.
NEED_GB=25.


def result(name,status,detail,fix=''):return {'check':name,'status':status,'detail':detail,'fix':fix}


def run(cmd,timeout=120):
    try:done=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=timeout);return done.returncode,(done.stdout+done.stderr).strip()
    except (OSError,subprocess.TimeoutExpired) as error:return 1,f'{type(error).__name__}: {error}'


def check_envs():
    out=[]
    code,text=run([str(VENV),'-c','import numpy,open3d,torch,cv2;print(torch.__version__)']) if VENV.exists() else (1,'missing')
    out.append(result('CPU environment (.venv)','PASS' if code==0 else 'FAIL',text.splitlines()[-1] if text else '',
        'pip install --require-hashes -r requirements-windows.lock into .venv' if code else ''))
    if not GPU_VENV.exists():
        out.append(result('GPU environment (.venv-gpu)','FAIL','missing','docs/RASPBERRY_PI_WINDOWS.md "Windows GPU environment" (requirements-windows-cuda.lock)'));return out
    code,text=run([str(GPU_VENV),'-c',"import torch;assert torch.cuda.is_available();x=torch.randn(256,256,device='cuda');(x@x).sum().item();print(torch.__version__,torch.cuda.get_device_name(0))"])
    out.append(result('GPU environment (.venv-gpu)','PASS' if code==0 else 'FAIL',text.splitlines()[-1] if text else '',
        'check nvidia-smi and the laptop GPU mode; the live run can fall back to .venv with --device cpu' if code else ''))
    return out


def check_git():
    code,branch=run(['git','rev-parse','--abbrev-ref','HEAD']);_,dirty=run(['git','status','--porcelain','--untracked-files=no'])
    code2,ahead=run(['git','rev-list','--count','@{u}..HEAD'])
    status='PASS' if not dirty and code2==0 and ahead.strip()=='0' else 'WARN'
    detail=f"branch {branch}; {'clean' if not dirty else 'uncommitted changes'}; "+(f'{ahead.strip()} commit(s) not pushed' if code2==0 else 'no upstream')
    return [result('Repository',status,detail,'commit and push before pulling on another machine' if status!='PASS' else '')]


def check_disk(path=RECORDINGS,need_gb=NEED_GB):
    target=path if path.exists() else path.parent;free=shutil.disk_usage(target).free/2**30
    return [result('Free disk for recordings','PASS' if free>=need_gb else ('WARN' if free>=need_gb/2 else 'FAIL'),
        f'{free:.0f} GB free at {target} (need ~{need_gb:.0f} GB)','delete old recordings or record to a bigger drive' if free<need_gb else '')]


def check_config():
    out=[]
    for profile in ('config/profiles/hotspot.yaml','config/profiles/hotspot_build.yaml'):
        code,text=run([str(VENV),'-c',f"from core.configuration import load_config;c=load_config('{profile}');print(c['live']['camera_width'],c['mapping'].get('commissioning_sweeps_per_scan'))"])
        out.append(result(f'Profile {Path(profile).name}','PASS' if code==0 else 'FAIL',text.splitlines()[-1] if text else '','fix the YAML before the lab' if code else ''))
    key=ROOT/'config/capture_node.key'
    out.append(result('Capture stream key (presence only)','PASS' if key.exists() else 'FAIL','present' if key.exists() else 'missing',
        '' if key.exists() else 'create it once and push it to the Pi with deployment/lab/lab_sync.sh push-secret (never commit it)'))
    calibration=ROOT/'config/calibration_extrinsics_640x400.json'
    out.append(result('Calibration 640x400','PASS' if calibration.exists() else 'WARN','present' if calibration.exists() else 'not yet measured',
        '' if calibration.exists() else 'expected until LAB_DAY step 5; the node runs in bring-up mode until then'))
    return out


def check_power():
    try:import psutil;battery=psutil.sensors_battery()
    except Exception:battery=None
    if battery is None:return [result('Mains power','PASS','no battery reported')]
    return [result('Mains power','PASS' if battery.power_plugged else 'WARN',f'{battery.percent:.0f} % battery, plugged in: {battery.power_plugged}',
        '' if battery.power_plugged else 'plug the laptop in; the 30-minute run and builds need full GPU power')]


def check_serial(port='COM8'):
    try:import serial.tools.list_ports as list_ports;ports=[p.device for p in list_ports.comports()]
    except Exception as error:return [result('ESP32 status board','WARN',f'cannot list ports ({type(error).__name__})')]
    return [result('ESP32 status board','PASS' if port in ports else 'WARN',f'ports: {", ".join(ports) or "none"}',
        '' if port in ports else f'plug the ESP32-S3 into native USB ({port}); only needed for step 7 (LEDs)')]


def check_rehearsals():
    sys.path.insert(0,str(ROOT/'tools'))
    try:import psutil,rehearse_live
    except Exception as error:return [result('Rehearsal and live processes','WARN',f'cannot inspect ({type(error).__name__})')]
    lock=rehearse_live.LOCK;holder=None
    if lock.exists():
        try:holder=json.loads(lock.read_text())
        except (OSError,ValueError):holder={}
    live=[]
    for p in psutil.process_iter(['pid','cmdline']):
        line=' '.join(p.info['cmdline'] or [])
        if ('command_center' in line or 'replay_capture_node' in line or 'rehearse_live' in line) and p.info['pid']!=os.getpid():live.append(p.info['pid'])
    if holder and holder.get('pid') and psutil.pid_exists(int(holder['pid'])):
        return [result('Rehearsal and live processes','WARN',f"rehearsal running (pid {holder['pid']})",'wait for it to finish; do not kill other sessions\' processes')]
    return [result('Rehearsal and live processes','PASS' if not live else 'WARN',f'{len(live)} live/replay process(es) running' if live else 'none running',
        'stop only processes you started, by PID (taskkill /T /PID <pid>)' if live else '')]


CHECKS=(check_envs,check_git,check_disk,check_config,check_power,check_serial,check_rehearsals)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter);p.add_argument('--json',type=Path)
    a=p.parse_args(argv);results=[]
    for check in CHECKS:
        try:results.extend(check())
        except Exception as error:results.append(result(check.__name__,'FAIL',f'{type(error).__name__}: {error}'))
    for r in results:print(f"{r['status']:4}  {r['check']}: {r['detail']}"+(f"\n      fix: {r['fix']}" if r['fix'] else ''))
    worst=2 if any(r['status']=='FAIL' for r in results) else (1 if any(r['status']=='WARN' for r in results) else 0)
    print({0:'All laptop checks passed.',1:'Warnings: read the fixes; nothing blocks the first steps.',2:'FAIL: fix before starting.'}[worst])
    if a.json:a.json.parent.mkdir(parents=True,exist_ok=True);a.json.write_text(json.dumps({'results':results,'exit':worst},indent=2)+'\n',encoding='utf-8')
    return worst


if __name__=='__main__':raise SystemExit(main())

"""Bounded external memory/CPU sampling for a running verification process."""
import argparse
import ctypes
import csv
import json
from pathlib import Path
import time
import sys
import psutil


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid',type=int,required=True)
    parser.add_argument('--duration',type=float,default=1800.)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--keep-awake',action='store_true',help='Request Windows stay awake only while this observer is active')
    args=parser.parse_args()
    if not 0<args.duration<=7200:parser.error('duration must be between0 and7200seconds')
    process=psutil.Process(args.pid);started=time.monotonic();samples=0;peak=0
    if args.keep_awake and sys.platform=='win32':
        if not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001):raise ctypes.WinError()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('w',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream);writer.writerow(['elapsed_s','rss_mb','cpu_percent','threads'])
        while time.monotonic()-started<args.duration:
            try:
                rss=process.memory_info().rss/1024**2
                writer.writerow([time.monotonic()-started,rss,process.cpu_percent(),process.num_threads()])
                stream.flush();samples+=1;peak=max(peak,rss)
                if not process.is_running():break
            except psutil.NoSuchProcess:break
            time.sleep(1)
    args.output.with_suffix('.json').write_text(json.dumps({'pid':args.pid,'samples':samples,
        'peak_rss_mb':peak,'wall_seconds':time.monotonic()-started,'source':'external_process_observer'},indent=2),encoding='utf-8')

"""Fast pre-flight and parallel full suite for AIDE2 cycles, without new dependencies (no pytest-xdist in the lock).

    python tools/fast_check.py            # lab-critical tests (~1 min): sensing, commissioning build, QA, calibration tools
    python tools/fast_check.py --full     # every tests/test_*.py, split over parallel pytest processes
    python tools/fast_check.py --full --workers 6 tests/test_a.py tests/test_b.py

Prints one summary line "passed=P failed=F skipped=S errors=E total=T" (T counts executed tests, as the AIDE2
journal does) and the failing test ids; exit code 0 only when nothing failed or errored.
"""
import argparse,os,re,subprocess,sys,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
# What tomorrow's lab steps run through: deskew/registration, the commissioning build, recording QA, calibration
# extraction, the capture node and the rig wrapper.
LAB_CRITICAL=['tests/test_aide2_c1_real_data_build.py','tests/test_sensing_mapping.py','tests/test_sweep_merge.py','tests/test_imu_bias.py',
    'tests/test_moving_commissioning.py','tests/test_recording_qa.py','tests/test_targetless_extrinsics.py','tests/test_calibrate_rig.py',
    'tests/test_capture_hotspot.py','tests/test_rig.py','tests/test_lidar_l2_driver.py','tests/test_clock_aligner.py','tests/test_lidar_self_mask.py',
    'tests/test_score_landmarks.py']
# Files that assert on elapsed time: run alone after the parallel groups, so machine load cannot fail them (C45; the
# c22 ratio test failed twice under 8 workers on 2026-11-05 and passed alone). Their assertions stay unchanged.
SERIAL=['tests/test_aide2_c17_tile_groups.py','tests/test_aide2_c22_tracker_geometry.py','tests/test_aide2_c23_voxel_features.py',
    'tests/test_hardware_halo.py']
COUNT=re.compile(r'(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)')


def python():
    for candidate in (ROOT/'.venv/Scripts/python.exe',ROOT/'.venv/bin/python'):
        if candidate.exists():return str(candidate)
    return sys.executable


def run_group(files,timeout):
    started=time.perf_counter()
    try:
        done=subprocess.run([python(),'-m','pytest','-q','-p','no:cacheprovider','-rfE',*files],cwd=ROOT,capture_output=True,text=True,timeout=timeout,
                            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        output=done.stdout+done.stderr
    except subprocess.TimeoutExpired as error:
        output=f'{error.stdout or ""}\nERROR timeout after {timeout} s: {" ".join(files)}\n1 error'
    counts={'passed':0,'failed':0,'skipped':0,'errors':0}
    tail=[line for line in output.splitlines() if re.search(r'\d+ (passed|failed|error)',line) and ' in ' in line]
    for number,kind in COUNT.findall(tail[-1] if tail else '1 error'):
        if kind.startswith('error'):counts['errors']+=int(number)
        elif kind in counts:counts[kind]+=int(number)
    failures=[line for line in output.splitlines() if line.startswith(('FAILED ','ERROR '))]
    return counts,failures,time.perf_counter()-started


def split_serial(files):
    """(parallel, serial): the timing-sensitive files in SERIAL run alone after the parallel pool, order kept."""
    return [f for f in files if f not in SERIAL],[f for f in files if f in SERIAL]


def groups(files,workers):
    # Largest files first, each to the currently lightest group (file size as a cost proxy).
    sized=sorted(files,key=lambda f:-(ROOT/f).stat().st_size);bins=[[0,[]] for _ in range(max(1,min(workers,len(sized))))]
    for f in sized:
        light=min(bins,key=lambda b:b[0]);light[0]+=(ROOT/f).stat().st_size;light[1].append(f)
    return [b[1] for b in bins if b[1]]


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('files',nargs='*');p.add_argument('--full',action='store_true',help='every tests/test_*.py')
    p.add_argument('--workers',type=int,default=min(8,os.cpu_count() or 2));p.add_argument('--timeout',type=float,default=1200)
    args=p.parse_args(argv)
    files=args.files or ([str(f.relative_to(ROOT)).replace(os.sep,'/') for f in sorted((ROOT/'tests').glob('test_*.py'))] if args.full else [f for f in LAB_CRITICAL if (ROOT/f).exists()])
    started=time.perf_counter();total={'passed':0,'failed':0,'skipped':0,'errors':0};failures=[]
    parallel,serial=split_serial(files)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for counts,failed,_ in pool.map(lambda g:run_group(g,args.timeout),groups(parallel,args.workers)):
            for key in total:total[key]+=counts[key]
            failures+=failed
    if serial:
        counts,failed,_=run_group(serial,args.timeout)
        for key in total:total[key]+=counts[key]
        failures+=failed
    for line in failures:print(line)
    executed=total['passed']+total['failed']+total['errors']
    print(f"passed={total['passed']} failed={total['failed']} skipped={total['skipped']} errors={total['errors']} total={executed} "
          f"files={len(files)} workers={args.workers} seconds={time.perf_counter()-started:.0f}")
    return 0 if not total['failed'] and not total['errors'] else 1


if __name__=='__main__':raise SystemExit(main())

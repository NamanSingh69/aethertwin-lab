"""Cloud GPU benchmarks on Modal for work a laptop browser cannot cover.

Suites (one GPU class per invocation, results saved under reports/modal/):
  smoke     environment + short model-variant timing + 20-frame CUDA pipeline run
  latency   model variants (fp32, cudnn.benchmark, TF32, AMP fp16/bf16, torch.compile)
            and full simulator pipeline runs on CUDA and on the container CPU
  pytest    regression suite installed from requirements-ubuntu-cuda.lock, plus a CUDA smoke
  sweep     CUDA pipeline over several simulator seeds (one container per seed)
  stability one long CUDA pipeline run (wall-clock --duration seconds)

Usage (Modal client on Python 3.12, credentials from the active Modal profile):
  modal run tools/modal_bench.py --suite smoke --gpu T4
  modal run tools/modal_bench.py --suite sweep --gpu L4 --seeds 7,101,102,201,202 --max-usd 2

The image is built from the hash-locked CUDA requirements; the repository is added
at container start and copied to /tmp before running, so the source is never changed.

Evidence limits: these are Modal cloud GPUs running the repository's synthetic
simulator (virtual sensors). They are not RTX laptop, A2000 workstation, Raspberry Pi
or lab results, and simulated sensors say nothing about physical accuracy.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import modal

ROOT=Path(__file__).resolve().parents[1]
REMOTE_SOURCE='/src';WORK=Path('/tmp/aethertwin')
GPUS=('T4','L4','A10')
# Modal list prices in USD per second (modal.com/pricing, read 2026-10-30).
GPU_USD_PER_S={'T4':0.000164,'L4':0.000222,'A10':0.000306}
CPU_CORES=4;MEMORY_GIB=16;CPU_USD_PER_CORE_S=0.0000131;MEMORY_USD_PER_GIB_S=0.00000222
TIMEOUT_S=20*60
LIMITS=('Modal cloud GPU running the synthetic simulator (virtual sensors). Not an RTX laptop, A2000, '
    'Raspberry Pi or lab result; simulated sensors are not physical accuracy. Any A2000 scaling is an unverified estimate.')
IGNORE=['.git','.venv','.venv-*','.local','.claude','web/node_modules','web/test-results','web/playwright-report',
    '**/__pycache__','**/.pytest_cache','reports/modal',
    # Written locally while a run uploads; Modal refuses files that change mid-build.
    'PROGRESS.md','artifacts/renderer_benchmarks','web/reports']

image=(modal.Image.debian_slim(python_version='3.12')
    .apt_install('libegl1','libgl1','libglib2.0-0','gcc','g++')  # Triton/Inductor need a C compiler.
    .pip_install_from_requirements(str(ROOT/'requirements-ubuntu-cuda.lock'),extra_options='--require-hashes')
    .env({'OMP_NUM_THREADS':str(CPU_CORES),'MKL_NUM_THREADS':str(CPU_CORES),'PYTHONDONTWRITEBYTECODE':'1'})
    .add_local_dir(ROOT,REMOTE_SOURCE,ignore=IGNORE))
app=modal.App('aethertwin-bench',image=image)


def estimate_usd(gpu,seconds,containers=1):
    rate=GPU_USD_PER_S[gpu]+CPU_CORES*CPU_USD_PER_CORE_S+MEMORY_GIB*MEMORY_USD_PER_GIB_S
    return round(rate*seconds*containers,4)


def _now():return datetime.now(timezone.utc).isoformat(timespec='seconds')


@app.cls(gpu='T4',cpu=CPU_CORES,memory=MEMORY_GIB*1024,timeout=TIMEOUT_S,max_containers=8,scaledown_window=60)
class Bench:
    @modal.enter()
    def prepare(self):
        import shutil
        if not WORK.exists():shutil.copytree(REMOTE_SOURCE,WORK)
        os.chdir(WORK);sys.path.insert(0,str(WORK))

    @modal.method()
    def environment(self):
        import platform,torch
        smi=subprocess.run(['nvidia-smi','--query-gpu=name,driver_version,memory.total,power.limit,clocks.max.sm','--format=csv,noheader'],
            capture_output=True,text=True).stdout.strip()
        cc=list(torch.cuda.get_device_capability(0)) if torch.cuda.is_available() else None
        # Plain built-ins only: results are unpickled by a client without torch (TorchVersion is a str subclass).
        return {'recorded_at':_now(),'gpu':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,'nvidia_smi':smi,
            'compute_capability':cc,'torch':str(torch.__version__),'cuda_runtime':str(torch.version.cuda),'cudnn':int(torch.backends.cudnn.version()),
            'python':platform.python_version(),'cpu_count':os.cpu_count(),'cpu_model':platform.processor() or _cpu_model(),
            'requirements':'requirements-ubuntu-cuda.lock (--require-hashes)'}

    @modal.method()
    def pipeline(self,seed=7,frames=60,device='cuda',duration=None,threads=CPU_CORES):
        output=Path(f'/tmp/runs/{device}-{seed}-{int(time.time())}')
        command=[sys.executable,'main.py','--mode','sim','--device',device,'--headless','--seed',str(seed),'--threads',str(threads),'--output',str(output)]
        command+=['--duration',str(duration)] if duration else ['--max-frames',str(frames)]
        began=time.monotonic();result=subprocess.run(command,cwd=WORK,capture_output=True,text=True,timeout=TIMEOUT_S-90)
        report=json.loads((output/'benchmark.json').read_text()) if (output/'benchmark.json').exists() else {}
        keep=('frames','wall_seconds','end_to_end_fps','processing_latency_ms','module_latency_ms','mean_processing_fps','rss_mb','dropped_snapshots',
            'occupancy_cells','presentation_queue','input_mode','hardware_validation')
        return {'suite':'pipeline','seed':seed,'device':device,'threads':threads,'requested_frames':None if duration else frames,
            'requested_duration_s':duration,'returncode':result.returncode,'container_seconds':round(time.monotonic()-began,1),
            'benchmark':{key:report.get(key) for key in keep},'stderr_tail':result.stderr[-1500:] if result.returncode else ''}

    @modal.method()
    def model_variants(self,seed=7,repeats=200,warmup_frames=3):
        import numpy as np,torch
        from core.configuration import load_config
        from core.se3_math import transform_points
        from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
        from perception_ml.inference_engine import InferenceEngine
        from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
        config=load_config(WORK/'config/system_config.yaml');p=config['perception']
        engine=InferenceEngine(WORK/p['checkpoint'],'cuda',config['mapping']['voxel_size'],p['max_voxels'],p.get('neighbors',16),
            p.get('temporal_neighbors',12),p.get('rgbd_stride',3),p.get('range_image_height',24),p.get('range_image_width',96))
        simulator=SyntheticLabSimulator(seed,config);deskewer=None;previous=None
        try:
            for _ in range(warmup_frames):  # several frames so the temporal branch is active, as in the live pipeline
                raw=simulator.next_frame()
                if deskewer is None:deskewer=ContinuousTimeIMUDeskewer(initial_pose=raw.sensor_pose_world,imu_to_lidar=raw.imu_to_lidar)
                frame=deskewer.deskew_frame(raw);points=frame.lidar_points.copy();points[:,:3]=transform_points(frame.sensor_pose_world,points[:,:3])
                previous=engine.previous;prepared,*_=engine.infer(frame,points)
        finally:simulator.close()
        kwargs=dict(prepared.inputs)
        if engine.temporal_valid:kwargs['coords_prev'],kwargs['cached_stage2_prev']=previous
        model=engine.model

        def timed(function):
            for _ in range(20):function()
            torch.cuda.synchronize();samples=[]
            for _ in range(repeats):
                start=time.perf_counter();function();torch.cuda.synchronize();samples.append(1000*(time.perf_counter()-start))
            return {'p50_ms':float(np.median(samples)),'p95_ms':float(np.percentile(samples,95)),'mean_ms':float(np.mean(samples))}

        def flags(cudnn_benchmark=False,tf32=False):
            torch.backends.cudnn.benchmark=cudnn_benchmark;torch.backends.cuda.matmul.allow_tf32=tf32;torch.backends.cudnn.allow_tf32=tf32

        report={'suite':'model_variants','seed':seed,'repeats':repeats,'voxels':len(kwargs['coords_t']),'points':len(kwargs['point_features']),
            'temporal':engine.temporal_valid,'scope':'Model forward only (precomputed spatial indices); not preprocessing or whole-pipeline latency.',
            'variants':{}}
        with torch.inference_mode():
            flags();reference=[t.float() for t in model(**kwargs)]
            def error(outputs):return max(float((a.float()-b).abs().max()) for a,b in zip(outputs,reference))
            def run(name,function,**settings):
                flags(**settings)
                try:report['variants'][name]={**timed(function),'max_abs_error_vs_fp32':error(function()),**settings}
                except Exception as exc:report['variants'][name]={'error':f'{type(exc).__name__}: {exc}'[:500]}
                finally:flags()
            run('fp32',lambda:model(**kwargs))
            run('fp32_cudnn_benchmark',lambda:model(**kwargs),cudnn_benchmark=True)
            run('tf32',lambda:model(**kwargs),tf32=True)
            def autocast(dtype):
                def function():
                    with torch.autocast('cuda',dtype=dtype):return model(**kwargs)
                return function
            run('amp_fp16',autocast(torch.float16))
            if torch.cuda.is_bf16_supported():run('amp_bf16',autocast(torch.bfloat16))
            try:
                compiled=torch.compile(model);start=time.perf_counter();compiled(**kwargs);torch.cuda.synchronize()
                compile_seconds=time.perf_counter()-start
                run('torch_compile_default',lambda:compiled(**kwargs))
                report['variants']['torch_compile_default']['compile_seconds']=compile_seconds
            except Exception as exc:report['variants']['torch_compile_default']={'error':f'{type(exc).__name__}: {exc}'[:500]}
        report['not_measured']=['ONNX Runtime and TensorRT export: not part of the locked environment; see docs/REVIEW_AND_ROADMAP.md']
        return report

    @modal.method()
    def pytest_cuda(self):
        import torch
        x=torch.randn(512,512,device='cuda');y=(x@x).sum().item();torch.cuda.synchronize()
        result=subprocess.run([sys.executable,'-m','pytest','-q','-p','no:cacheprovider'],cwd=WORK,capture_output=True,text=True,
            timeout=TIMEOUT_S-120,env={**os.environ,'RUN_OBSERVED_INTEGRATION':'1'})
        lines=result.stdout.strip().splitlines()
        return {'suite':'pytest','cuda_matmul_smoke':bool(y==y),'returncode':result.returncode,'summary':lines[-1] if lines else '',
            'failures':[line for line in lines if line.startswith(('FAILED','ERROR'))][:40],
            'scope':'Validates the Linux CUDA lock install; most tests exercise CPU code paths.'}


def _cpu_model():
    try:return next(line.split(':',1)[1].strip() for line in open('/proc/cpuinfo') if line.startswith('model name'))
    except Exception:return None


@app.local_entrypoint()
def main(suite:str='smoke',gpu:str='T4',seeds:str='7',frames:int=60,repeats:int=200,duration:float=900.,max_usd:float=1.,
         out:str='reports/modal'):
    if gpu not in GPUS:raise SystemExit(f'--gpu must be one of {GPUS}')
    seed_list=[int(s) for s in seeds.split(',') if s.strip()]
    containers=len(seed_list) if suite=='sweep' else 1
    expected={'smoke':240,'latency':900,'pytest':600,'sweep':600,'stability':duration+240}[suite]
    upper=estimate_usd(gpu,TIMEOUT_S,containers)
    print(f'{suite} on {gpu}: expected about ${estimate_usd(gpu,expected,containers)}, upper bound ${upper} '
        f'({containers} container(s) x {TIMEOUT_S//60} min timeout)')
    if upper>max_usd:raise SystemExit(f'Upper-bound cost ${upper} exceeds --max-usd {max_usd}; nothing launched.')
    bench=Bench.with_options(gpu=gpu)();started=time.monotonic()
    results={'environment':bench.environment.remote()}
    if suite=='smoke':
        results['model_variants']=bench.model_variants.remote(seed_list[0],50)
        results['pipeline_cuda']=bench.pipeline.remote(seed_list[0],20,'cuda')
    elif suite=='latency':
        results['model_variants']=bench.model_variants.remote(seed_list[0],repeats)
        results['pipeline_cuda']=bench.pipeline.remote(seed_list[0],frames,'cuda')
        results['pipeline_cpu']=bench.pipeline.remote(seed_list[0],frames,'cpu')
    elif suite=='pytest':results['pytest']=bench.pytest_cuda.remote()
    elif suite=='sweep':results['pipeline_cuda']=list(bench.pipeline.starmap([(s,frames,'cuda') for s in seed_list]))
    elif suite=='stability':results['pipeline_cuda']=bench.pipeline.remote(seed_list[0],0,'cuda',duration)
    else:raise SystemExit(f'Unknown suite {suite}')
    wall=time.monotonic()-started
    record={'schema_version':1,'provider':'Modal','gpu_class':gpu,'suite':suite,'recorded_at':_now(),'seeds':seed_list,
        'client_wall_seconds':round(wall,1),'estimated_cost_usd':estimate_usd(gpu,wall,containers),
        'cost_note':'Estimate from list prices and client wall time; the Modal billing report is authoritative.',
        'limits':LIMITS,'results':results}
    target=ROOT/out;target.mkdir(parents=True,exist_ok=True)
    path=target/f"{suite}_{gpu.lower()}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(f'Wrote {path.relative_to(ROOT)} (about ${record["estimated_cost_usd"]})')

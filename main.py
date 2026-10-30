"""Unified, explicit CLI for the lab digital twin."""
from __future__ import annotations
import os
os.environ.setdefault('OMP_NUM_THREADS','4')
os.environ.setdefault('MKL_NUM_THREADS','4')
import argparse,json,queue,threading,time,traceback,shutil
import sys
from pathlib import Path
from dataclasses import replace
import numpy as np
import yaml
import torch
from core.resources import resource_root

ROOT=resource_root()
RUN_ROOT=Path.cwd()


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['sim','replay','live','build-twin','pretrain','train','evaluate','calibrate','hil-test'],default='sim')
    p.add_argument('--config',type=Path,default=ROOT/'config/system_config.yaml')
    p.add_argument('--seed',type=int);p.add_argument('--input',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--checkpoint',type=Path);p.add_argument('--mae-checkpoint',type=Path)
    p.add_argument('--device',choices=['cpu','cuda','auto'],default=None)
    p.add_argument('--headless',action='store_true');p.add_argument('--max-frames',type=int)
    p.add_argument('--duration',type=float,help='Wall-clock run duration, seconds')
    p.add_argument('--epochs',type=int);p.add_argument('--record',type=Path);p.add_argument('--twin',type=Path)
    p.add_argument('--mobile',action='store_true',default=None);p.add_argument('--rebuild-twin',action='store_true')
    p.add_argument('--commissioning-mode',choices=['auto','mobile','stations'],help='Explicitly declare independent at-rest station captures when timestamps reset')
    p.add_argument('--websocket',action='store_true');p.add_argument('--threads',type=int,default=4)
    p.add_argument('--render-backend',choices=['auto','native','mesa'])
    p.add_argument('--mesa-dir',type=Path)
    p.add_argument('--real-time',action='store_true',help='Pace replay/simulation at acquisition time when processing is faster')
    return p


def _asset(path):return path if path.is_absolute() else ROOT/path


def _raw_recorder(path,twin_path,mobile,options=None):
    from core.recording import start_raw_recording
    return start_raw_recording(path,twin_path,mobile,options=options)


def main(argv=None):
    args=parser().parse_args(argv)
    if args.max_frames is not None and args.max_frames<1:raise ValueError('--max-frames must be positive')
    if args.duration is not None and args.duration<=0:raise ValueError('--duration must be positive')
    torch.set_num_threads(max(1,args.threads))
    from core.configuration import load_config
    config=load_config(args.config)
    config['input_mode']=args.mode
    if args.commissioning_mode is not None:config['mapping']['commissioning_mode']=args.commissioning_mode
    if args.seed is not None:config['seed']=args.seed
    if args.mobile is not None:config['simulation']['mobile']=args.mobile
    device=args.device or config['device'];config['device']='cuda' if device=='auto' and torch.cuda.is_available() else ('cpu' if device=='auto' else device)
    if config['device']=='cuda' and not torch.cuda.is_available():raise RuntimeError('CUDA requested but unavailable; choose --device cpu')
    checkpoint=args.checkpoint or _asset(Path(config['perception']['checkpoint']))
    output=args.output or RUN_ROOT/'artifacts'/args.mode
    if args.mode=='hil-test':
        from diagnostics import run_diagnostics
        report=run_diagnostics(config,source='replay' if args.input else 'sim',input_path=args.input,
            twin_path=args.twin,output=output,max_frames=args.max_frames or 8,root=ROOT)
        print(json.dumps({'software_checks_passed':report['software_checks_passed'],
                         'hardware_checks_passed':report['hardware_checks_passed'],'output':str(output)},indent=2))
        return 0 if report['software_checks_passed'] else 2
    if args.mode in ('train','evaluate','pretrain','calibrate'):
        output.parent.mkdir(parents=True,exist_ok=True)
        if args.mode=='train':
            from perception_ml.training import train
            target=args.output or RUN_ROOT/'artifacts'/('fine_tuned.pt' if args.checkpoint else 'trained.pt')
            train(config,target,args.epochs,config['device'],mae_checkpoint=args.mae_checkpoint,
                  initial_checkpoint=args.checkpoint,data_manifest=args.input)
        elif args.mode=='evaluate':
            from perception_ml.training import evaluate_checkpoint
            report=evaluate_checkpoint(checkpoint,config,args.output or RUN_ROOT/'artifacts/evaluation.json',config['device'],data_manifest=args.input)
            print(json.dumps(report,indent=2))
            if not report['semantic_beats_baseline'] or not report['dynamic_flow_beats_baseline']:return 2
        elif args.mode=='pretrain':
            from perception_ml.point_mae_pretrain import pretrain
            from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
            from ingestion.recorder_replayer import ReplaySource
            if args.input:
                source=ReplaySource(args.input);frames=[f.lidar_points for _,f in zip(range(args.max_frames or 36),source)]
            else:
                source=SyntheticLabSimulator(config['seed'],config);frames=[source.next_frame().lidar_points for _ in range(args.max_frames or 12)]
            source.close();history=pretrain(frames,args.output or RUN_ROOT/'artifacts/point_mae.pt',args.epochs or 2,config['device'],config['seed'])
            print(json.dumps({'chamfer_loss':history}))
        else:
            if not args.input:raise ValueError('--mode calibrate requires paired observations NPZ via --input')
            from ingestion.calibration import calibrate_camera_lidar
            with np.load(args.input,allow_pickle=False) as data:
                measured=np.asarray(data['measured']).item() if 'measured' in data else True
                _,report=calibrate_camera_lidar(data['lidar_points'],data['image_points'],data['K'],data.get('distortion'),args.output or RUN_ROOT/'artifacts/measured_calibration.json',
                    image_size=data.get('image_size'),measured=measured)
            print(json.dumps(report,indent=2))
        return 0
    rendering_info={'name':'Headless processing','requested':'none'}
    if not args.headless and args.mode in ('sim','replay','live'):
        from visualization.render_backend import configure_rendering
        render_config=config.get('rendering',{})
        mesa_dir=args.mesa_dir or _asset(Path(render_config.get('mesa_dir','assets/rendering/mesa')))
        backend=args.render_backend or render_config.get('backend','auto')
        if backend=='auto':backend='mesa' if sys.platform=='win32' and mesa_dir.is_dir() else 'native'
        rendering_info=configure_rendering(backend,mesa_dir=mesa_dir)
        print(f"Graphics: {rendering_info['name']}",flush=True)
    config['active_rendering']=rendering_info
    from core.pipeline import LabPipeline,build_commissioned_assets,load_commissioned_assets
    from ingestion.recorder_replayer import ReplaySource,FrameRecorder
    source=None
    if args.mode=='build-twin':
        if not args.input:raise ValueError('build-twin requires an explicitly empty-room recording via --input')
        source=ReplaySource(args.input)
        try:build_commissioned_assets(source,output,config)
        finally:source.close()
        return 0
    if args.mode=='sim':
        from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
        source=SyntheticLabSimulator(config['seed'],config,config['simulation'].get('mobile',False))
        cached=ROOT/f'assets/twin_seed{config["seed"]}'
        twin_path=args.twin or (cached if (cached/'planning_grid.npz').exists() and not args.rebuild_twin
                              else RUN_ROOT/f'artifacts/twin_seed{config["seed"]}')
        if args.rebuild_twin or not (twin_path/'planning_grid.npz').exists():
            twin,static,grid=build_commissioned_assets(source.commissioning_frames(),twin_path,config)
        else:twin,static,grid=load_commissioned_assets(twin_path)
    elif args.mode=='replay':
        if not args.input:raise ValueError('replay requires --input recording directory')
        source=ReplaySource(args.input);twin_path=args.twin or args.input/'twin'
        if not (twin_path/'planning_grid.npz').exists():raise ValueError('Replay requires its recorded twin or an explicit matching --twin')
        twin,static,grid=load_commissioned_assets(twin_path)
    else:
        from ingestion.live_source import LiveSensorSource
        from core.pipeline import read_commissioning_provenance
        twin_path=args.twin
        if twin_path is None:raise ValueError('Live mode requires a measured commissioned map via --twin')
        if not read_commissioning_provenance(twin_path).get('live_reference_eligible',False):
            raise ValueError('Live mode rejects synthetic/unknown commissioning provenance; build a measured physical reference')
        twin,static,grid=load_commissioned_assets(twin_path)
        config['live'].setdefault('initial_pose_world',np.asarray(twin.poses[-1]).tolist())
        source=LiveSensorSource(config['live'],ROOT)
    if not checkpoint.exists():
        source.close();raise FileNotFoundError(f'No trained checkpoint at {checkpoint}. Run --mode train first.')
    output.mkdir(parents=True,exist_ok=True)
    mobile=bool(config['simulation'].get('mobile',False))
    if args.mode=='live':mobile=True
    if args.mode=='live' or config['planning'].get('bounds') is None:
        config['planning']['bounds']=[grid.origin[0],grid.upper[0],grid.origin[1],grid.upper[1],grid.origin[2],grid.upper[2]]
    from command_center.assets import file_revision
    config.setdefault('identity',{})['map_revision']=file_revision(twin_path/('bundle_manifest.json' if (twin_path/'bundle_manifest.json').is_file() else 'measured_map.npz'))
    if args.mode=='replay' and args.mobile is None:
        recorded_mode=source.manifest.get('metadata',{}).get('motion_mode')
        if recorded_mode is not None:mobile=recorded_mode=='mobile'
    config['simulation']['mobile']=mobile
    try:pipeline=LabPipeline(config,checkpoint,twin,static,grid,mobile)
    except BaseException:
        source.close();raise
    from core.resources import freeze_startup_heap
    freeze_startup_heap()
    recorder=None
    if args.record:
        try:recorder=_raw_recorder(args.record,twin_path,mobile,config.get('recording',{}))
        except BaseException:source.close();pipeline.close();raise
    if args.mode=='live':
        from core.bounded_acquisition import BufferedAcquisition
        budgets=config.get('acquisition',{})
        source=BufferedAcquisition(source,max_frames=budgets.get('max_frames',2),max_bytes=budgets.get('max_bytes',64*1024*1024),
            max_age_seconds=budgets.get('max_age_seconds',.5),imu_history_seconds=budgets.get('imu_history_seconds',5.),
            max_imu_samples=budgets.get('max_imu_samples',8192),raw_sink=recorder)
    stop=threading.Event();paused=threading.Event();snapshots=queue.Queue(maxsize=2);failures=[]
    broadcaster=None
    if args.websocket or config['telemetry']['enabled']:
        from visualization.websocket_broadcaster import WebSocketBroadcaster
        broadcaster=WebSocketBroadcaster(config['telemetry']['host'],config['telemetry']['port'])
        try:broadcaster.start()
        except BaseException:
            source.close();pipeline.close()
            if recorder:recorder.close()
            raise
    from hardware_halo import HaloController
    try:halo=HaloController(config.get('hardware_halo',{}))
    except BaseException:
        source.close();pipeline.close()
        if recorder:recorder.close()
        if broadcaster:broadcaster.close()
        raise
    def finalize(action):
        try:action()
        except BaseException as exc:
            failures.append(exc);traceback.print_exc()
    def produce():
        started=time.monotonic();iterator=iter(source);last_print=started
        try:
            while not stop.is_set():
                if args.duration and time.monotonic()-started>=args.duration:break
                if args.max_frames and pipeline.frames>=args.max_frames:break
                if paused.wait(.01):
                    time.sleep(.03);continue
                try:frame=next(iterator)
                except StopIteration:break
                tick=time.monotonic()
                if recorder and args.mode!='live':recorder.write(frame)
                frame=replace(frame,diagnostics={**frame.diagnostics,'source_mode':args.mode,
                    'recording':None if recorder is None else recorder.status()})
                state=pipeline.process(frame)
                halo.publish(state)
                if broadcaster:broadcaster.publish(state)
                if not args.headless:
                    if snapshots.full():
                        try:snapshots.get_nowait();pipeline.dropped_snapshots+=1
                        except queue.Empty:None
                    snapshots.put_nowait(state)
                    pipeline.presentation_queue_depth=snapshots.qsize()
                    pipeline.presentation_queue_max_depth=max(getattr(pipeline,'presentation_queue_max_depth',0),pipeline.presentation_queue_depth)
                now=time.monotonic()
                if now-last_print>=10:
                    print(json.dumps({'frames':pipeline.frames,'processing_fps':round(state.fps,2),'tracks':len(state.entities),
                        'route':state.trajectory_status,'rss_mb':round(state.diagnostics['memory_mb'],1)}),flush=True);last_print=now
                    (output/'progress.json').write_text(json.dumps(pipeline.report(),indent=2),encoding='utf-8')
                if args.real_time:stop.wait(max(0,frame.sweep_duration-(time.monotonic()-tick)))
        except BaseException as exc:
            failures.append(exc);traceback.print_exc()
        finally:
            stop.set();finalize(source.close)
            finalize(halo.close)
            if recorder:finalize(recorder.close)
            if broadcaster:finalize(broadcaster.close)
            finalize(lambda:pipeline.heatmap.save(output/'traffic_heatmap.npz'))
            finalize(lambda:pipeline.occupancy.save(output/'occupancy.npz'))
            if pipeline.trajectory.status=='ok':finalize(lambda:pipeline.export_trajectory(output/'trajectory.csv'))
            finalize(lambda:(output/'benchmark.json').write_text(json.dumps(pipeline.report(),indent=2),encoding='utf-8'))
            if pipeline.last_state:
                from visualization.websocket_broadcaster import frame_message
                finalize(lambda:(output/'last_state.json').write_text(json.dumps(frame_message(pipeline.last_state),allow_nan=False,indent=2),encoding='utf-8'))
    if args.headless:
        if args.max_frames is None and args.duration is None:args.max_frames=100
        produce()
    else:
        from visualization.open3d_command_center import CommandCenter
        label='Software graphics' if rendering_info.get('requested')=='mesa' else 'Native graphics'
        center=CommandCenter(on_start_goal=pipeline.set_start_goal,on_export=lambda:pipeline.export_trajectory(output/'trajectory.csv'),title=f'Lab Digital Twin · {label}')
        center.initial_start=pipeline.start;center.initial_goal=pipeline.goal
        center.set_static_mesh(twin.mesh,inferred_mesh=getattr(twin,'watertight_mesh',None))
        worker=threading.Thread(target=produce,name='lab-perception',daemon=False);worker.start()
        try:center.run(snapshots,stop,paused)
        finally:stop.set();worker.join(timeout=120)
        if worker.is_alive():raise RuntimeError('Processing worker failed to stop')
        pipeline.presentation_queue_depth=snapshots.qsize()
        report=pipeline.report()
        report['viewer']=getattr(center,'metrics',{})
        (output/'benchmark.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    np.savez_compressed(output/'performance_samples.npz',
        frame_ids=np.arange(pipeline.frames-len(pipeline.processing_ms),pipeline.frames),
        processing_ms=np.asarray(pipeline.processing_ms),rss_mb=np.asarray(pipeline.memory_mb))
    report=pipeline.report();pipeline.close()
    if failures:raise failures[0]
    print(json.dumps(report,indent=2));return 0


if __name__=='__main__':
    raise SystemExit(main())

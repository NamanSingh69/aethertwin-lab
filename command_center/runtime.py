"""Browser controls connected to the ordinary observation-only lab pipeline."""
from __future__ import annotations
import copy
from dataclasses import replace
import io
import json
from pathlib import Path
import threading
import time
import uuid
import numpy as np

from core.resources import resource_root
from .assets import export_twin_assets,file_revision


class BrowserRuntime:
    def __init__(self,config,*,root=None,mode='sim',recording=None,twin=None,output='artifacts/browser',
                 checkpoint=None,max_frames=None,record=None,duration=None):
        if mode not in ('sim','replay','live'):raise ValueError('Unknown browser source')
        if max_frames is not None and max_frames<1:raise ValueError('max_frames must be positive')
        if duration is not None and duration<=0:raise ValueError('duration must be positive')
        self.root=Path(root or resource_root()).resolve();self.output=Path(output).resolve();self.output.mkdir(parents=True,exist_ok=True)
        self.asset_root=self.output/'assets';self.asset_root.mkdir(exist_ok=True)
        self.original_config=copy.deepcopy(config);self.checkpoint=Path(checkpoint).resolve() if checkpoint else self.root/config['perception']['checkpoint']
        self.requested_twin=None if twin is None else Path(twin).resolve();self.max_frames=max_frames
        self.duration=duration;self._started=None
        self.recordings={}
        sample=self.root/'assets/sample_recording'
        if (sample/'manifest.json').exists():self.recordings['sample']=sample
        if recording is not None:self.recordings['input']=Path(recording).resolve()
        self.recording_id='input' if recording is not None else 'sample'
        self.mode=mode;self.source=None;self.pipeline=None;self.iterator=None;self.hub=None;self.controls=None
        self.paused=False;self.completed=False;self.error=None;self.current_frame=-1;self.frame_count=None
        self.seek_target=None;self.steps=0;self.session_id=uuid.uuid4().hex;self.source_active=False
        self._stop=threading.Event();self._thread=None;self._status_lock=threading.Lock();self._asset_map=None
        self.halo=None
        self.recorder=None;self._record_segment=0
        self.record_root=None if record is None else Path(record).resolve()/self.session_id
        self._open(mode,self.recording_id)
        from hardware_halo import HaloController
        try:self.halo=HaloController(config.get('hardware_halo',{}))
        except BaseException:self.close();raise

    def _open(self,mode,recording_id=None):
        from core.pipeline import LabPipeline,load_commissioned_assets,build_commissioned_assets,read_commissioning_provenance
        from ingestion.recorder_replayer import ReplaySource
        cfg=copy.deepcopy(self.original_config);cfg['input_mode']=mode
        if not self.checkpoint.is_file():raise FileNotFoundError(f'Trained checkpoint missing: {self.checkpoint}')
        if mode=='sim':
            from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
            candidate=SyntheticLabSimulator(cfg['seed'],cfg,cfg['simulation'].get('mobile',False))
            twin_path=self.requested_twin or self.root/f'assets/twin_seed{cfg["seed"]}'
            if not (twin_path/'planning_grid.npz').is_file():
                twin_path=self.output/f'twin_seed{cfg["seed"]}'
                if not (twin_path/'planning_grid.npz').is_file():build_commissioned_assets(candidate.commissioning_frames(),twin_path,cfg)
            count=None;mobile=bool(cfg['simulation'].get('mobile',False))
        elif mode=='replay':
            if recording_id not in self.recordings:raise ValueError('Recording is not in the configured source registry')
            candidate=ReplaySource(self.recordings[recording_id]);twin_path=self.requested_twin or candidate.path/'twin'
            count=len(candidate);mobile=candidate.manifest.get('metadata',{}).get('motion_mode')=='mobile'
        else:
            if self.requested_twin is None:raise ValueError('Live mode requires an explicit measured commissioned twin')
            twin_path=self.requested_twin;candidate=None;count=None;mobile=True
            if not read_commissioning_provenance(twin_path).get('live_reference_eligible',False):
                raise ValueError('Live mode rejects synthetic/unknown commissioning provenance')
        twin=static=pipeline=recorder=live=None
        try:
            twin,static,grid=load_commissioned_assets(twin_path)
            revision=file_revision(twin_path/('bundle_manifest.json' if (twin_path/'bundle_manifest.json').is_file() else 'measured_map.npz'))
            cfg.setdefault('identity',{}).update(map_revision=revision)
            cfg['planning']['bounds']=[grid.origin[0],grid.upper[0],grid.origin[1],grid.upper[1],grid.origin[2],grid.upper[2]]
            cfg['simulation']['mobile']=mobile
            if mode=='live':
                cfg['planning']['start']=None;cfg['planning']['goal']=None;cfg['geofences']=cfg.get('geofences',[])
                # A last-station pose is a visible initialization prior only;
                # constrained measured-map ICP must still validate its overlap.
                cfg['live'].setdefault('initial_pose_world',np.asarray(twin.poses[-1]).tolist())
            cfg['mapping']['runtime_cache_root']=str(self.output/'.runtime_cache')
            pipeline=LabPipeline(cfg,self.checkpoint,twin,static,grid,mobile)
            from core.resources import freeze_startup_heap
            freeze_startup_heap()
            folder=self.asset_root/revision
            manifest=export_twin_assets(twin,folder,revision,url_prefix=f'/api/v2/assets/{revision}/')
            if self.record_root is not None:
                from core.recording import start_raw_recording,tree_bytes
                if self.recorder is not None:
                    if self.source is not None:self.source.close();self.source=None;self.source_active=False
                    self.recorder.close();self.recorder=None
                options=cfg.get('recording',{})
                remaining=int(options.get('max_disk_bytes',16*1024**3))-tree_bytes(self.record_root)
                recorder=start_raw_recording(self.record_root/f'{self._record_segment:04d}-{mode}',twin_path,mobile,
                    {'source_mode':mode,'map_revision':revision},options=options,remaining_bytes=remaining)
            if mode=='live':
                from ingestion.live_source import LiveSensorSource
                from core.bounded_acquisition import BufferedAcquisition
                if self.source is not None:self.source.close();self.source=None;self.source_active=False
                live=LiveSensorSource(cfg['live'],self.root)
                budgets=cfg.get('acquisition',{})
                candidate=BufferedAcquisition(live,max_frames=budgets.get('max_frames',2),max_bytes=budgets.get('max_bytes',64*1024*1024),
                    max_age_seconds=budgets.get('max_age_seconds',.5),imu_history_seconds=budgets.get('imu_history_seconds',5),
                    max_imu_samples=budgets.get('max_imu_samples',8192),raw_sink=recorder).start()
        except BaseException:
            extra_live=live if candidate is None else None
            resources=(candidate,extra_live,recorder,pipeline) if pipeline is not None else (candidate,extra_live,recorder,static,twin)
            for resource in resources:
                try:
                    if hasattr(resource,'close'):resource.close()
                except Exception:pass
            raise
        previous=self.source;previous_pipeline=self.pipeline;previous_recorder=self.recorder
        self.source=candidate;self.pipeline=pipeline;self.iterator=iter(candidate);self.mode=mode
        self.twin_path=twin_path
        self.recorder=recorder;self._record_segment+=1
        if previous is not None:previous.close()
        if previous_recorder is not None:previous_recorder.close()
        if previous_pipeline is not None:previous_pipeline.close()
        temporary=self.asset_root/'manifest.tmp';temporary.write_text(json.dumps(manifest,indent=2),encoding='utf-8');temporary.replace(self.asset_root/'manifest.json')
        self._asset_map=revision;self.recording_id=recording_id;self.frame_count=count;self.current_frame=-1
        self.paused=False;self.completed=False;self.error=None;self.source_active=True;self.steps=0
        self.session_id=uuid.uuid4().hex;self.seek_target=None
        if self.halo is not None:self.halo.publish(None)
        if self.controls is not None:self.controls.map_revision=revision

    def start(self,hub,controls):
        if self._thread is not None:raise RuntimeError('Browser runtime already started')
        self.hub=hub;self.controls=controls;controls.map_revision=self._asset_map
        self._started=time.monotonic()
        self._thread=threading.Thread(target=self._run,name='lab-browser-pipeline',daemon=False);self._thread.start()

    def _apply(self,command):
        action=command.action;p=command.payload
        if action=='pause':self.paused=True
        elif action=='resume':
            if self.completed:raise ValueError('Source ended; seek or select a source before resuming')
            self.paused=False
        elif action=='step':
            if self.mode=='live':raise ValueError('Live acquisition cannot be frame stepped')
            if self.completed:raise ValueError('Source ended; seek before stepping')
            self.paused=True;self.steps+=1
        elif action=='set_start_goal':self.pipeline.set_start_goal(p['start'],p['goal'])
        elif action=='set_geofences':self.pipeline.set_geofences(p['geofences'])
        elif action=='seek':
            if self.mode!='replay':raise ValueError('Seek is available only for recorded replay')
            target=p['frame_index']
            if target>=self.frame_count:raise ValueError('Requested frame lies outside the recording')
            self._open('replay',self.recording_id);self.seek_target=target
        elif action=='select_source':self._open(p['source'],p.get('recording_id',self.recording_id))
        else:raise ValueError('Unsupported runtime action')

    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    self.controls.drain(self._apply)
                    if self.duration is not None and time.monotonic()-self._started>=self.duration:
                        self.completed=True;self.paused=True
                    if self.error or self.completed or (self.paused and not self.steps and self.mode!='live'):
                        self._stop.wait(.03);continue
                    if self.max_frames is not None and self.pipeline.frames>=self.max_frames:
                        self.completed=True;self.paused=True;continue
                    started=time.monotonic()
                    try:frame=next(self.iterator)
                    except StopIteration:
                        self.completed=True;self.paused=True;self.source_active=False;continue
                    if self.recorder is not None and self.mode!='live':self.recorder.write(frame)
                    frame=replace(frame,diagnostics={**frame.diagnostics,'source_mode':self.mode,'runtime_session_id':self.session_id,
                        'recording':None if self.recorder is None else self.recorder.status()})
                    state=self.pipeline.process(frame);self.current_frame+=1
                    state=replace(state,diagnostics={**state.diagnostics,'controls':self.controls.status(),
                        'playback':{'paused':self.paused,'frame_index':self.current_frame,'frame_count':self.frame_count,
                                    'seeking':self.seek_target is not None,'target_frame':self.seek_target},
                        'presentation_prediction_horizon_seconds':.25})
                    self.pipeline.last_state=state
                    if self.pipeline.frames%10==0:
                        progress=self.pipeline.report();progress['telemetry']=self.hub.status()
                        (self.output/'progress.json').write_text(json.dumps(progress,indent=2),encoding='utf-8')
                    if self.halo is not None:self.halo.publish(state)
                    if self.seek_target is not None:
                        if self.current_frame>=self.seek_target:self.seek_target=None;self.paused=True;self.hub.publish(state)
                    elif not self.paused or self.steps:self.hub.publish(state)
                    if self.steps:self.steps-=1
                    if self.mode=='sim':self._stop.wait(max(0,frame.sweep_duration-(time.monotonic()-started)))
                except Exception as exc:
                    self.error=f'{type(exc).__name__}: {exc}';self.paused=True;self.source_active=False
        finally:
            try:
                if self.source is not None:self.source.close()
                if self.recorder is not None:self.recorder.close()
                if self.pipeline is not None:
                    report=self.pipeline.report();report['browser_runtime']=self.bootstrap()
                    (self.output/'benchmark.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                    self.pipeline.occupancy.save(self.output/'occupancy_latest.npz')
                    self.pipeline.heatmap.save(self.output/'traffic_latest.npz')
                    np.savez_compressed(self.output/'performance_samples.npz',
                        frame_ids=np.arange(self.pipeline.frames-len(self.pipeline.processing_ms),self.pipeline.frames),
                        processing_ms=np.asarray(self.pipeline.processing_ms),rss_mb=np.asarray(self.pipeline.memory_mb))
            except Exception as exc:self.error=f'Finalization failed: {exc}'
            finally:
                if self.pipeline is not None:self.pipeline.close()

    def bootstrap(self):
        pipeline=self.pipeline;latest=None if pipeline is None else pipeline.last_state
        halo_status=None if self.halo is None else self.halo.status()
        available=[{'id':'simulation','source':'sim','label':'Simulation'}]
        available.extend({'id':key,'source':'replay','recording_id':key,'label':'Sample recording' if key=='sample' else 'Input recording'} for key in self.recordings)
        if self.requested_twin is not None:available.append({'id':'live','source':'live','label':'Live rig (requires connected devices)'})
        return {'identity':{} if latest is None else {'lab_id':latest.lab_id,'rig_id':latest.rig_id,
                    'map_revision':latest.map_revision,'calibration_id':latest.calibration_id},
                'source_mode':self.mode,'paused':self.paused,'completed':self.completed,'error':self.error,
                'source_active':self.source_active,'available_sources':available,
                'capabilities':{'pause':True,'resume':True,'step':self.mode!='live','seek':self.mode=='replay',
                                'set_start_goal':True,'set_geofences':True,'select_source':True},
                'replay':{'frame_count':self.frame_count,'current_frame':self.current_frame,
                          'current_time':None if latest is None else latest.timestamp,'seeking':self.seek_target is not None},
                'session_id':self.session_id,'hardware_verified':False,
                'halo':None if halo_status is None else halo_status['virtual'],'halo_transport':halo_status,
                'recording':None if self.recorder is None else self.recorder.status()}

    def trajectory_csv(self):
        if self.pipeline is None:raise ValueError('Pipeline is not ready')
        with self.pipeline.lock:
            if self.pipeline.trajectory.status!='ok':raise ValueError('No validated trajectory is available')
            points,times=self.pipeline._remaining_trajectory()
            stream=io.StringIO();np.savetxt(stream,np.c_[times,points],delimiter=',',header='time_s,x_m,y_m,z_m',comments='')
            return stream.getvalue()

    def close(self):
        self._stop.set()
        # Only live blocking acquisition needs an early close to unblock next().
        # A simulation step must finish before its MuJoCo data is released.
        try:
            if self.source is not None and self.mode=='live':self.source.close();self.source=None
            if self._thread:
                self._thread.join(timeout=30)
                if self._thread.is_alive():raise RuntimeError('Browser perception pipeline failed to stop')
            else:
                try:
                    if self.source is not None:self.source.close();self.source=None
                    if self.recorder is not None:self.recorder.close()
                finally:
                    if self.pipeline is not None:self.pipeline.close()
        finally:
            if self.halo is not None:self.halo.close()

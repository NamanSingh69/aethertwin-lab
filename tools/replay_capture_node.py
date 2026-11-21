"""Serve a recorded session through the capture node's server over the real raw-sensor transport (loopback rehearsals).

A dress rehearsal of the live stack without the rig: a recording made through the capture node
(tools/record_raw.py, e.g. %USERPROFILE%/aethertwin_rec/wifi_stationary_60s) is turned back into the
node's messages and served by ``CaptureServer``, ``Outbox`` and ``DeviceSink`` unchanged, so the host
(``python -m command_center --mode live`` with ``live.source: remote``) runs exactly as against the Pi.
Every event leaves at its recorded time (IMU samples, each sweep at its end, each camera frame), the
session loops with continuous clocks, and colour and depth are re-encoded with the profile's codecs.

What a replay cannot reproduce: only the camera frames the recording fused with a sweep exist (one
per sweep, ~12 Hz; the node with ``camera_forward: sweep`` sends ~15 Hz); Wi-Fi delay and the Pi's
encode time are absent (loopback); the L2 ring index is not recorded (sent as 0) and IMU device
timestamps keep the recorded offset, not the L2 device clock. The identity says ``synthetic`` with a
``replay`` block, so the host refuses it unless ``live.allow_synthetic_capture`` is set and
record_raw labels it a rehearsal: a replay is never a hardware, network or lab result.

    python tools/replay_capture_node.py --recording %USERPROFILE%/aethertwin_rec/wifi_stationary_60s \
        --key-file SCRATCH/loopback.key [--calibration SCRATCH/rehearsal_calibration.json] [--port 7600]

Without ``--calibration`` the node announces bring-up (no calibration), like ``capture_node --bringup``.
Bind is loopback by default; use a throwaway key (``--generate-key``), never the rig's key on a shared link.
"""
from __future__ import annotations
from collections import deque
from pathlib import Path
import argparse,heapq,itertools,json,queue,sys,threading,time
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ingestion import raw_transport as rt
from ingestion.capture_node import CaptureServer,Outbox,load_config,percentile

LABEL='recorded real-device data replayed by tools/replay_capture_node.py; not live hardware and not a lab result'
CAMERA_META=('camera_profiles','camera_timestamps','camera_available','camera_calibration_validation')


def wire_points(points):
    """Recorded LidarL2Driver points (x,y,z,intensity/255,1,1,time) back to the node's L2 columns (x,y,z,intensity,time,ring=0)."""
    points=np.asarray(points,np.float32);out=np.zeros((len(points),6),np.float32)
    out[:,:3]=points[:,:3];out[:,3]=points[:,3]*255.;out[:,4]=points[:,6];return out


def depth_raw(depth):
    """Recorded depth in metres back to Gemini Y16 at scale 1 (1 mm units, as recorded by depth_meters)."""
    depth=np.nan_to_num(np.asarray(depth,np.float64),nan=0.,posinf=0.,neginf=0.)
    return np.clip(np.rint(depth*1000.),0,65535).astype(np.uint16)


class ReplayDevices:
    """A recording's L2 sweeps, IMU and RGB-D frames re-emitted through ``DeviceSink`` at their recorded timing, like HardwareDevices.

    One loader thread decodes chunks ahead into two bounded queues (L2+IMU, camera); two pacing threads emit
    each event when the node clock reaches it. Node time = recorded time + a fixed epoch + one shift per loop,
    so every stream stays monotonic across loops. The end of a non-looping pass, or a read error, is a FAULT.
    """
    def __init__(self,recording,*,loop=True,max_frames=None,calibration=None,lead_seconds=1.,clock=rt.host_monotonic,
                 lidar_queue_events=4000,camera_queue_frames=48):
        from ingestion.recorder_replayer import ReplaySource
        self.path=Path(recording);self.source=ReplaySource(self.path);self.loop=bool(loop);self.clock=clock
        self.max_frames=None if max_frames is None else int(max_frames);self.lead=float(lead_seconds)
        if self.max_frames is not None and self.max_frames<2:raise ValueError('Replay needs at least two frames')
        self.calibration=calibration;self.bringup=calibration is None
        self.stop=threading.Event();self.threads=[];self.epoch=None
        self.lidar_queue=queue.Queue(int(lidar_queue_events));self.camera_queue=queue.Queue(int(camera_queue_frames))
        self.counts={'passes':0,'frames':0,'imu':0,'clouds':0,'camera':0,'imu_duplicates':0,'camera_duplicates':0,'frames_without_camera':0}
        self.late_ms=deque(maxlen=2048);self.error=None
        first=next(iter(ReplaySource(self.path)))
        self.first_time=min([first.timestamp]+[s.timestamp for s in first.imu_samples]+([first.camera_timestamp] if first.camera_timestamp else []))
        self.device_offset=float((first.raw_timestamps or {}).get('lidar',first.timestamp))-first.timestamp
        profiles=first.diagnostics.get('camera_profiles') or {}
        self.camera_identity={'kind':'replay-of-orbbec-gemini','device':profiles.get('device'),'profile':(profiles.get('aligned_output') or {}).get('depth'),
                              'alignment':profiles.get('alignment')}
        self.metadata={k:self.source.manifest.get('metadata',{}).get(k) for k in ('motion_mode','source','recording_policy','calibration')}
    def identity(self):
        return {'synthetic':True,'bringup':self.bringup,
                'replay':{'label':LABEL,'recording':self.path.name,'frames':len(self.source),'loop':self.loop,'max_frames':self.max_frames,
                          'recording_metadata':self.metadata},
                'lidar':{'kind':'replay-of-unitree-l2','serial':'replay'},'camera':self.camera_identity}
    def start(self,sink):
        self.sink=sink;self.epoch=self.clock()+self.lead-self.first_time
        for target,args,name in ((self._load,(),'replay-loader'),(self._emit,(self.lidar_queue,),'replay-l2'),(self._emit,(self.camera_queue,),'replay-camera')):
            thread=threading.Thread(target=target,args=args,name=name,daemon=True);thread.start();self.threads.append(thread)
    def _put(self,target,item):
        while not self.stop.is_set():
            try:target.put(item,timeout=.25);return True
            except queue.Full:continue
        return False
    def _events(self,frame,shift,state):
        """(send time, kind, args) on the node clock for one recorded frame; IMU and camera already sent are skipped."""
        base=self.epoch+shift;events=[]
        for s in frame.imu_samples:
            t=float(s.timestamp)
            if t<=state['imu']:self.counts['imu_duplicates']+=1;continue
            state['imu']=t;state['imu_first']=t if state['imu_first'] is None else state['imu_first'];state['imu_count']+=1;node=t+base
            events.append((node,'imu',(t+self.device_offset+shift,node,np.asarray(s.gyro,float),np.asarray(s.accel,float))))
        if frame.lidar_points is not None and len(frame.lidar_points):
            start=float(frame.timestamp)+base;duration=float(frame.sweep_duration or 0.)
            device=float((frame.raw_timestamps or {}).get('lidar',frame.timestamp+self.device_offset))+shift
            events.append((start+max(duration,1e-4),'cloud',(int(frame.frame_id)+state['frame_shift'],device,start,duration,wire_points(frame.lidar_points))))
        if frame.rgb is None or frame.depth is None or frame.camera_timestamp is None:self.counts['frames_without_camera']+=1
        elif float(frame.camera_timestamp)<=state['camera']:self.counts['camera_duplicates']+=1
        else:
            t=float(frame.camera_timestamp);state['camera']=t;d=frame.diagnostics or {};raw=frame.raw_timestamps or {}
            meta={k:d[k] for k in CAMERA_META if k in d}
            meta.update({'raw_timestamp':float(raw.get('camera',t))+shift,'clock_fit_residual_seconds':d.get('camera_clock_fit_residual_seconds'),
                         'color_depth_delta_seconds':d.get('camera_color_depth_delta_seconds'),'color_format':d.get('camera_color_format'),
                         'hardware_trigger_claimed':False,'camera_available':True,'replay':True})
            for key in ('camera_device_us','camera_depth_device_us'):
                if key in raw:meta[key]=int(raw[key])+int(round(shift*1e6))
            if self.calibration is not None and isinstance(meta.get('camera_calibration_validation'),dict):
                meta['camera_calibration_validation']={**meta['camera_calibration_validation'],'calibration_id':self.calibration.get('calibration_id'),
                    'measured':bool(self.calibration.get('measured')),'bringup_factory_intrinsics_only':False,'replay_calibration':True}
            rgb=np.ascontiguousarray(frame.rgb,np.uint8)
            events.append((t+base,'camera',(t+base,depth_raw(frame.depth),1.,rgb,meta,{'data':rgb,'format':'RGB'})))
        return events
    def _load(self):
        from ingestion.recorder_replayer import ReplaySource
        shift=0.;order=itertools.count()
        try:
            while not self.stop.is_set():
                state={'imu':-np.inf,'imu_first':None,'imu_count':0,'camera':-np.inf,'frame_shift':self.counts['passes']*1000000};heap=[];last=self.first_time
                for n,frame in enumerate(ReplaySource(self.path)):
                    if self.stop.is_set():return
                    if self.max_frames is not None and n>=self.max_frames:break
                    for event in self._events(frame,shift,state):
                        heapq.heappush(heap,(event[0],next(order),event[1],event[2]));last=max(last,event[0]-self.epoch-shift)
                    self.counts['frames']+=1
                    # Events of later frames are never earlier than this frame's start minus the IMU look-back.
                    horizon=float(frame.timestamp)+self.epoch+shift-.5
                    while heap and heap[0][0]<horizon:
                        due,_,kind,args=heapq.heappop(heap)
                        if not self._put(self.camera_queue if kind=='camera' else self.lidar_queue,(due,kind,args)):return
                while heap:
                    due,_,kind,args=heapq.heappop(heap)
                    if not self._put(self.camera_queue if kind=='camera' else self.lidar_queue,(due,kind,args)):return
                self.counts['passes']+=1
                if not self.loop:break
                # The next pass's first IMU sample follows this pass's last one at the IMU period, so the deskewer sees an
                # unbroken IMU stream across the seam (a gap would be an IMU outage, which the real link never has silently).
                first,end=state['imu_first'],state['imu'];dt=(end-first)/max(state['imu_count']-1,1) if first is not None else .004
                first=self.first_time if first is None else first;shift+=max(end-first+dt,last-first+1e-3)
            for target in (self.lidar_queue,self.camera_queue):self._put(target,None)
        except BaseException as exc:
            self.error=f'{type(exc).__name__}: {exc}';self.sink.fault('lidar',f'replay of {self.path.name} failed: {self.error}')
    def _emit(self,source):
        sink=self.sink
        try:
            while not self.stop.is_set():
                try:item=source.get(timeout=.25)
                except queue.Empty:continue
                if item is None:
                    if source is self.lidar_queue:sink.fault('lidar',f'replay of {self.path.name} ended after {self.counts["passes"]} pass(es) (not looping)')
                    return
                due,kind,args=item
                while not self.stop.is_set():
                    wait=due-self.clock()
                    if wait<=0:break
                    time.sleep(min(wait,.05))  # time.sleep is high resolution on Windows (3.11+); Event.wait is not
                if self.stop.is_set():return
                self.late_ms.append(1000*(self.clock()-due))
                if kind=='imu':sink.imu(*args);self.counts['imu']+=1
                elif kind=='cloud':sink.cloud(*args);self.counts['clouds']+=1
                else:
                    t,depth,scale,rgb,meta,color=args;sink.camera(t,depth,scale,rgb,meta,device_color=color);self.counts['camera']+=1
        except BaseException as exc:
            self.error=f'{type(exc).__name__}: {exc}';sink.fault('camera' if source is self.camera_queue else 'lidar',f'replay emit failed: {self.error}')
    def status(self):
        late=list(self.late_ms)
        return {**self.counts,'queued_lidar_events':self.lidar_queue.qsize(),'queued_camera_frames':self.camera_queue.qsize(),
                'late_ms_p50':percentile(late,50),'late_ms_p95':percentile(late,95),'late_ms_max':None if not late else float(max(late)),'error':self.error}
    def close(self):
        self.stop.set()
        for thread in self.threads:thread.join(timeout=3)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--recording',type=Path,required=True,help='Recording directory (manifest.json + chunk_*.npz)')
    p.add_argument('--config',type=Path,default=ROOT/'config/profiles/hotspot.yaml',help='Profile whose capture_node codecs and queues are used')
    p.add_argument('--key-file',type=Path,required=True,help='Shared key for this loopback link (use a throwaway key, see --generate-key)')
    p.add_argument('--generate-key',action='store_true',help='Create --key-file first if it does not exist')
    p.add_argument('--calibration',type=Path,help='Calibration JSON announced to the host (omit for bring-up: no calibration)')
    p.add_argument('--bind',default='127.0.0.1');p.add_argument('--port',type=int,default=7600)
    p.add_argument('--allow',action='append',help='Allowed processing host (repeatable; default 127.0.0.1)')
    p.add_argument('--no-loop',action='store_true',help='One pass, then a FAULT (the host stops) instead of looping')
    p.add_argument('--max-frames',type=int,help='Replay only the first N frames per pass');p.add_argument('--duration',type=float)
    p.add_argument('--status-interval',type=float,default=10.)
    args=p.parse_args(argv)
    if args.generate_key and not args.key_file.exists():rt.generate_key(args.key_file)
    key=rt.load_key(args.key_file);config=load_config(args.config);node=dict(config.get('capture_node') or {})
    calibration=None if args.calibration is None else json.loads(args.calibration.read_text(encoding='utf-8'))
    devices=ReplayDevices(args.recording,loop=not args.no_loop,max_frames=args.max_frames,calibration=calibration)
    server=CaptureServer(devices,key,bind=args.bind,port=args.port,allowed_peers=args.allow or ['127.0.0.1'],calibration_path=args.calibration,
        codecs={'depth':node.get('depth_codec','raw'),'color':node.get('color_codec','device'),'color_quality':node.get('color_quality',90),
                'depth_level':node.get('depth_level',1),'camera_forward':node.get('camera_forward','all'),
                'camera_sweep_window_seconds':node.get('camera_sweep_window_seconds',.02)},
        outbox=Outbox(node.get('imu_queue_samples',20000),node.get('cloud_queue_sweeps',4),node.get('camera_queue_frames',2),node.get('fragment_bytes',1024*1024)))
    server.start();print(json.dumps({'event':'listening','address':list(server.address[:2]),'replay':devices.identity()['replay']},default=str),flush=True)
    started=time.monotonic();last_status=started;code=0
    try:
        while not args.duration or time.monotonic()-started<args.duration:
            time.sleep(.5)
            if server.sink.faults:
                time.sleep(2.);print(json.dumps({'event':'device_fault','faults':server.sink.faults,'replay':devices.status()},default=str),flush=True);code=3;break
            if time.monotonic()-last_status>=args.status_interval:
                last_status=time.monotonic();print(json.dumps({'event':'status',**server.status(),'replay':devices.status()},default=str),flush=True)
    except KeyboardInterrupt:pass
    finally:server.close()
    return code


if __name__=='__main__':sys.exit(main())

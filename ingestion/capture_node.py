"""Raspberry Pi capture node: forwards raw L2 packets, IMU and RGB-D over the raw-sensor transport.

The node does acquisition, timestamps and bounded buffering only. It binds to
one private address, accepts only listed peers that prove the shared key, and
streams to one authenticated processing host at a time. Queue policy: IMU is
never dropped silently (an overflow is counted, announced with a GAP message and
breaks the sample sequence, so the host starts a new segment); LiDAR sweeps and
camera frames drop oldest-first and are counted. Camera frames are sent in
fragments with lower priority than IMU and LiDAR, so a slow camera cannot starve
them. Nothing is simulated in hardware mode; ``--synthetic`` is labelled as such
in the identity and the processing host refuses it unless explicitly allowed.

Run on the Pi: ``python -m ingestion.capture_node --config config/system_config.yaml``
(see docs/RASPBERRY_PI_WINDOWS.md for the key, the systemd unit and addresses). Over the
laptop's Wi-Fi hotspot use ``--config config/profiles/hotspot.yaml``: it binds whatever DHCP
address ``wlan0`` holds (``--bind-interface``, or pin one with ``--bind``) and sends only the
camera frames that can be fused with a LiDAR sweep (``camera_forward: sweep``).
"""
from __future__ import annotations
from collections import deque
from pathlib import Path
import argparse,ipaddress,json,os,platform,socket,struct,sys,threading,time,uuid
import numpy as np
from . import raw_transport as rt

NODE_VERSION='1'
WILDCARDS={'','0.0.0.0','::','*'}
LEASE_CHECK_SECONDS=5.


def percentile(values,q):return None if not values else float(np.percentile(np.asarray(values),q))


def check_bind(bind):
    """One private or loopback address (192.168.1.2 wired, 192.168.137.x on the Windows hotspot); never a wildcard."""
    if bind is None or str(bind).strip() in WILDCARDS:raise ValueError('The capture node binds to one private address, never to all interfaces')
    address=ipaddress.ip_address(str(bind).strip());mapped=getattr(address,'ipv4_mapped',None)
    if address.is_unspecified or (mapped is not None and mapped.is_unspecified):
        raise ValueError('The capture node binds to one private address, never to all interfaces')
    if not (address.is_private or address.is_loopback):raise ValueError(f'The capture node binds to a private address only, not {address}')
    return str(address)


def interface_ipv4(name):
    """The IPv4 address an interface holds now (Linux), e.g. the DHCP lease on ``wlan0``; None if it has none yet."""
    try:import fcntl
    except ImportError as exc:raise RuntimeError('--bind-interface needs Linux (the Pi); pass --bind ADDRESS elsewhere') from exc
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
        try:data=fcntl.ioctl(probe.fileno(),0x8915,struct.pack('256s',str(name).encode()[:15]))  # SIOCGIFADDR
        except OSError:return None
    return socket.inet_ntoa(data[20:24])


def bind_address(node,bind=None,interface=None,resolve=None):
    """``--bind``, else ``--bind-interface``, else ``capture_node.bind_interface``, else ``capture_node.bind``."""
    if bind:return check_bind(bind)
    interface=interface or node.get('bind_interface')
    if not interface:return check_bind(node.get('bind') or '192.168.1.2')
    address=(resolve or interface_ipv4)(interface)
    if address is None:raise RuntimeError(f'{interface} has no IPv4 address yet (not joined to the hotspot?); systemd retries')
    return check_bind(address)


class Outbox:
    """Priority scheduler between device threads and the one sender thread."""
    def __init__(self,imu_capacity=20000,cloud_capacity=4,camera_capacity=2,fragment_bytes=1024*1024,imu_batch=256):
        if min(imu_capacity,cloud_capacity,camera_capacity,fragment_bytes,imu_batch)<1:raise ValueError('Outbox capacities must be positive')
        self.imu_capacity=int(imu_capacity);self.fragment_bytes=int(fragment_bytes);self.imu_batch=int(imu_batch)
        self.cv=threading.Condition();self.active=False
        self.control=deque();self.imu=deque();self.clouds=deque(maxlen=int(cloud_capacity));self.cameras=deque(maxlen=int(camera_capacity))
        self.fragment=None;self.imu_seq=0;self.cloud_seq=0;self.camera_seq=0;self.owner=None;self.gap=None;self.clock=rt.host_monotonic
        self.counts={'imu_samples':0,'imu_dropped':0,'imu_gaps':0,'clouds':0,'clouds_dropped':0,'camera_frames':0,'camera_dropped':0,
                     'discarded_without_client':0,'discarded_at_session_start':0}
    def start(self,owner):
        """A new authenticated session: forget anything queued for a previous one (it is a new segment)."""
        with self.cv:
            self.counts['discarded_at_session_start']+=len(self.imu)+len(self.clouds)+len(self.cameras)+(self.fragment is not None)
            self.control.clear();self.imu.clear();self.clouds.clear();self.cameras.clear();self.fragment=None;self.gap=None
            self.active=True;self.owner=owner;self.cv.notify_all()
    def stop(self,owner=None):
        with self.cv:
            if owner is None or owner is self.owner:self.active=False;self.owner=None
            self.cv.notify_all()
    def put_control(self,kind,meta):
        with self.cv:
            if self.active:self.control.append((kind,meta));self.cv.notify()
    def put_imu(self,row):
        with self.cv:
            self.imu_seq+=1;self.counts['imu_samples']+=1
            if not self.active:self.counts['discarded_without_client']+=1;return
            if len(self.imu)>=self.imu_capacity:
                seq,_=self.imu.popleft();self.counts['imu_dropped']+=1
                if self.gap is not None and self.gap['first_missing_seq']+self.gap['count']==seq:self.gap['count']+=1
                else:
                    self.gap={'stream':'imu','first_missing_seq':seq,'count':1,'reason':'capture node IMU queue overflow'}
                    self.counts['imu_gaps']+=1;self.control.append((rt.GAP,self.gap))
            self.imu.append((self.imu_seq,np.asarray(row,np.float64)));self.cv.notify()
    def put_cloud(self,meta,points):
        with self.cv:
            self.cloud_seq+=1;self.counts['clouds']+=1
            if not self.active:self.counts['discarded_without_client']+=1;return
            if len(self.clouds)==self.clouds.maxlen:self.counts['clouds_dropped']+=1
            self.clouds.append(({**meta,'seq':self.cloud_seq},np.ascontiguousarray(points,np.float32)));self.cv.notify()
    def put_camera(self,meta,body):
        with self.cv:
            self.camera_seq+=1;self.counts['camera_frames']+=1
            if not self.active:self.counts['discarded_without_client']+=1;return
            if len(self.cameras)==self.cameras.maxlen:self.counts['camera_dropped']+=1
            self.cameras.append(({**meta,'seq':self.camera_seq},body));self.cv.notify()
    def _ready(self):return bool(self.control or self.imu or self.clouds or self.cameras or self.fragment)
    def next_message(self,owner,timeout=.25):
        """Control, then all pending IMU, then one sweep, then one camera fragment (only for the current session)."""
        with self.cv:
            if not self.cv.wait_for(lambda:not self.active or self.owner is not owner or self._ready(),timeout):return None
            if not self.active or self.owner is not owner:return None
            if self.control:
                kind,meta=self.control.popleft()
                if kind==rt.PONG:meta={**meta,'t3':self.clock()}
                elif kind==rt.GAP:
                    # Once sent, a gap is final; later overflows open a new one.
                    if meta is self.gap:self.gap=None
                    meta=dict(meta)
                return kind,meta,b''
            if self.imu:
                count=min(len(self.imu),self.imu_batch);first=self.imu[0][0]
                # Sequence numbers are contiguous inside a batch: stop at a gap.
                rows=[];expected=first
                while self.imu and len(rows)<count and self.imu[0][0]==expected:rows.append(self.imu.popleft()[1]);expected+=1
                return rt.IMU,{'first_seq':first,'count':len(rows)},np.stack(rows).tobytes()
            if self.clouds:
                meta,points=self.clouds.popleft()
                return rt.CLOUD,{**meta,'shape':list(points.shape),'dtype':'<f4'},points.tobytes()
            if self.fragment is None and self.cameras:
                meta,body=self.cameras.popleft();count=max(1,-(-len(body)//self.fragment_bytes))
                self.fragment=[meta,memoryview(body),0,count]
            meta,body,index,count=self.fragment
            chunk=body[index*self.fragment_bytes:(index+1)*self.fragment_bytes]
            header={'seq':meta['seq'],'index':index,'count':count}
            if index==0:header.update(meta,bytes=len(body))
            self.fragment[2]+=1
            if self.fragment[2]>=count:self.fragment=None
            return rt.CAMERA,header,chunk
    def status(self):
        with self.cv:return {**self.counts,'queued_imu':len(self.imu),'queued_clouds':len(self.clouds),'queued_camera':len(self.cameras),
                             'imu_capacity':self.imu_capacity,'cloud_capacity':self.clouds.maxlen,'camera_capacity':self.cameras.maxlen,'streaming':self.active}


CAMERA_FORWARD=('all','sweep')


class DeviceSink:
    """What device adapters call; converts to wire rows and records faults per modality.

    ``camera_forward: sweep`` (bandwidth-limited links) sends only camera frames within
    ``camera_sweep_window_seconds`` of a predicted L2 sweep end, the only frames the host's
    synchroniser can fuse (it pairs each sweep end with a camera frame within 20 ms). The
    prediction is the last sweep end plus the median sweep period; until three sweeps have been
    seen, or if the L2 goes quiet, every frame is sent. Skipped frames are counted, never encoded.
    """
    def __init__(self,outbox,codecs):
        self.outbox=outbox;self.depth_codec=codecs.get('depth','raw');self.color_codec=codecs.get('color','device')
        self.color_quality=int(codecs.get('color_quality',90));self.depth_level=int(codecs.get('depth_level',1))
        self.camera_forward=codecs.get('camera_forward','all');self.sweep_window=float(codecs.get('camera_sweep_window_seconds',.02))
        if self.depth_codec not in rt.DEPTH_CODECS:raise ValueError(f'depth codec must be one of {rt.DEPTH_CODECS}')
        if self.color_codec not in rt.COLOR_CODECS:raise ValueError(f'colour codec must be one of {rt.COLOR_CODECS}')
        if self.camera_forward not in CAMERA_FORWARD:raise ValueError(f'camera_forward must be one of {CAMERA_FORWARD}')
        if not np.isfinite(self.sweep_window) or self.sweep_window<=0:raise ValueError('camera_sweep_window_seconds must be positive')
        self.faults={};self.lock=threading.Lock();self.encode_ms=deque(maxlen=512);self.camera_bytes=deque(maxlen=512)
        self.sweep_end=None;self.sweep_starts=deque(maxlen=9);self.camera_skipped=0
    def imu(self,device_timestamp,capture_mono,gyro,accel):
        self.outbox.put_imu([device_timestamp,capture_mono,*gyro,*accel])
    def cloud(self,frame_id,device_timestamp,capture_mono,duration,points):
        # The host ends a sweep at start + max(duration, 0.1 ms) (LidarL2Driver); predict the same ends.
        with self.lock:self.sweep_starts.append(float(capture_mono));self.sweep_end=float(capture_mono)+max(float(duration),1e-4)
        self.outbox.put_cloud({'frame_id':int(frame_id),'device_timestamp':float(device_timestamp),'capture_mono':float(capture_mono),
                               'duration':float(duration)},points)
    def fusable(self,capture_mono):
        """Whether a camera frame captured at ``capture_mono`` (node monotonic) is worth sending."""
        if self.camera_forward=='all':return True
        with self.lock:end=self.sweep_end;starts=np.asarray(self.sweep_starts)
        periods=np.diff(starts);periods=periods[(periods>0)&(periods<1)]
        if end is None or len(periods)<2:return True
        period=float(np.median(periods))
        if capture_mono-end>3*period:return True
        phase=(capture_mono-end)%period
        return min(phase,period-phase)<=self.sweep_window
    def camera(self,capture_mono,depth_raw,depth_scale,rgb,metadata,device_color=None):
        """``rgb`` may be None when colour is forwarded as device bytes; colour is aligned to depth, so shapes match."""
        if not self.fusable(capture_mono):self.camera_skipped+=1;return
        started=time.perf_counter();depth=rt.encode_depth(depth_raw,self.depth_codec,self.depth_level)
        codec=self.color_codec
        if codec=='device':
            if device_color is None:raise ValueError('device colour passthrough needs the SDK colour buffer')
            color=bytes(np.asarray(device_color['data'],np.uint8).reshape(-1));color_format=str(device_color['format'])
        else:
            if rgb is None:raise ValueError(f'colour codec {codec} needs decoded RGB on the capture node')
            color=rt.encode_color(rgb,codec,self.color_quality);color_format=None
        self.encode_ms.append(1000*(time.perf_counter()-started));self.camera_bytes.append(len(depth)+len(color))
        meta={'capture_mono':float(capture_mono),'depth':{'codec':self.depth_codec,'shape':list(np.shape(depth_raw)),'scale':float(depth_scale),'bytes':len(depth)},
              'color':{'codec':codec,'shape':list(np.shape(depth_raw)[:2]),'format':color_format,'bytes':len(color),'lossy':codec=='jpeg'},'metadata':metadata}
        self.outbox.put_camera(meta,depth+color)
    def fault(self,modality,error):
        with self.lock:self.faults[modality]={'modality':modality,'error':str(error),'at_mono':time.monotonic()}
        self.outbox.put_control(rt.FAULT,self.faults[modality])
    def replay_faults(self):
        with self.lock:faults=list(self.faults.values())
        for fault in faults:self.outbox.put_control(rt.FAULT,fault)
    def status(self):
        return {'depth_codec':self.depth_codec,'color_codec':self.color_codec,'camera_forward':self.camera_forward,
                'camera_sweep_window_seconds':self.sweep_window,'camera_skipped':self.camera_skipped,'faults':dict(self.faults),
                'camera_encode_ms_p50':percentile(list(self.encode_ms),50),'camera_encode_ms_p95':percentile(list(self.encode_ms),95),
                'camera_message_bytes_p50':percentile(list(self.camera_bytes),50)}


class SyntheticDevices:
    """Labelled synthetic L2, IMU and RGB-D for loopback tests and soaks. Never a hardware substitute."""
    def __init__(self,seed=0,imu_hz=200.,lidar_hz=10.,points=2000,camera_fps=30.,width=160,height=100,camera=True):
        self.rng=np.random.default_rng(seed);self.imu_hz=float(imu_hz);self.lidar_hz=float(lidar_hz);self.points=int(points)
        self.camera_fps=float(camera_fps);self.width=int(width);self.height=int(height);self.camera_enabled=bool(camera)
        self.stop=threading.Event();self.thread=None;self.fail_camera_after=None;self.pause=threading.Event()
        # Tests step the node clock (with CaptureServer(clock=...)) to exercise clock-jump handling.
        self.clock_shift=0.
        v,u=np.indices((self.height,self.width));self.base_depth=(1500+400*np.sin(u/37.)+300*np.cos(v/23.)).astype(np.float64)
        self.base_rgb=np.stack([(u*255//max(self.width-1,1)),(v*255//max(self.height-1,1)),np.full_like(u,128)],-1).astype(np.uint8)
        # A few noisy depth images (3 mm noise) cycled, so generation cost does not dominate soaks.
        self.depths=[np.clip(self.base_depth+self.rng.normal(0,3,self.base_depth.shape),0,65535).astype(np.uint16) for _ in range(4)]
        import cv2
        self.colors=[np.roll(self.base_rgb,8*i,axis=1) for i in range(4)]
        self.mjpg=[cv2.imencode('.jpg',cv2.cvtColor(c,cv2.COLOR_RGB2BGR),[cv2.IMWRITE_JPEG_QUALITY,90])[1].reshape(-1) for c in self.colors]
        self.threads=[]
    def identity(self):
        return {'synthetic':True,'lidar':{'kind':'synthetic-l2','serial':'synthetic'},'camera':{'kind':'synthetic-rgbd','serial':'synthetic',
                'profile':{'width':self.width,'height':self.height,'fps':self.camera_fps}} if self.camera_enabled else None,
                'rates_hz':{'imu':self.imu_hz,'lidar':self.lidar_hz,'camera':self.camera_fps}}
    def start(self,sink):
        start=rt.host_monotonic()
        for target,name in ((self._run_lidar,'synthetic-l2'),(self._run_camera,'synthetic-camera')):
            thread=threading.Thread(target=target,args=(sink,start),name=name,daemon=True);thread.start();self.threads.append(thread)
    def _wait(self,due):
        wait=due-rt.host_monotonic()
        if wait>0:self.stop.wait(min(wait,.05))
        return wait<=0
    def _run_lidar(self,sink,start):
        epoch=time.time()-start;imu_n=sweep_n=0;sweep=1/self.lidar_hz
        while not self.stop.is_set():
            if self.pause.is_set():time.sleep(.005);continue
            due,kind=min((start+imu_n/self.imu_hz,'imu'),(start+(sweep_n+1)*sweep,'cloud'))
            if not self._wait(due):continue
            shift=self.clock_shift
            if kind=='imu':
                phase=imu_n/self.imu_hz
                sink.imu(due+epoch,due+shift,[.01*np.sin(phase),.02,0.],[0.,0.,9.81+.01*np.cos(phase)]);imu_n+=1
            elif kind=='cloud':
                begin=start+sweep_n*sweep;angles=self.rng.uniform(0,2*np.pi,self.points);r=self.rng.uniform(1,6,self.points)
                points=np.c_[r*np.cos(angles),r*np.sin(angles),self.rng.uniform(-.5,1.5,self.points),self.rng.uniform(0,255,self.points),
                             np.sort(self.rng.uniform(0,sweep*.999,self.points)),self.rng.integers(0,18,self.points)].astype(np.float32)
                sink.cloud(sweep_n,begin+epoch,begin+shift,float(points[:,4].max()),points);sweep_n+=1
    def _run_camera(self,sink,start):
        epoch=time.time()-start;cam_n=0
        while not self.stop.is_set() and self.camera_enabled:
            if self.pause.is_set():time.sleep(.005);continue
            due=start+cam_n/self.camera_fps
            if not self._wait(due):continue
            shift=self.clock_shift
            if self.fail_camera_after is not None and cam_n>=self.fail_camera_after:
                sink.fault('camera','synthetic camera failure injected by a test');self.camera_enabled=False;continue
            depth=self.depths[cam_n%len(self.depths)];index=cam_n%len(self.colors)
            metadata={'raw_timestamp':due+epoch,'camera_device_us':int((due+epoch)*1e6),'camera_depth_device_us':int((due+epoch)*1e6),
                      'clock_fit_residual_seconds':0.,'color_depth_delta_seconds':0.,'color_format':'MJPG','hardware_trigger_claimed':False,
                      'camera_available':True,'synthetic':True}
            sink.camera(due+shift,depth,1.,self.colors[index],metadata,device_color={'data':self.mjpg[index],'format':'MJPG'});cam_n+=1
    def close(self):
        self.stop.set()
        for thread in self.threads:thread.join(timeout=3)


class HardwareDevices:
    """Unitree L2 (native SDK binding) and Gemini 335L (pyorbbecsdk2) on the Pi.

    L2 timestamps are mapped onto the Pi's monotonic clock exactly as
    ``LidarL2Driver`` maps them (SDK-synchronised epoch plus a fixed offset
    taken at start), and the raw SDK timestamp is sent alongside.
    """
    def __init__(self,live,root=Path('.'),*,reader=None,camera=None,calibration=None,decode_color=False,lidar_silence_seconds=3.,lidar_startup_seconds=15.,
                 bringup=False):
        from .live_source import load_measured_calibration
        self.live=live;self.stop=threading.Event();self.threads=[];self.bringup=bool(bringup)
        self.lidar_silence=float(lidar_silence_seconds);self.lidar_startup=float(lidar_startup_seconds)
        if self.bringup:
            # Bring-up records calibration targets before any calibration exists: no calibration object at all.
            size=[live.get('camera_color_width') or live['camera_width'],live.get('camera_color_height') or live['camera_height']]
            self.calibration,self.calibration_data,self.calibration_path=None,{'image_size':size},None
        else:self.calibration,self.calibration_data,self.calibration_path=calibration or load_measured_calibration(live,root)
        if reader is None:
            try:from lab_sensors_native import L2Reader
            except ImportError as exc:raise RuntimeError('The capture node needs the native Unitree SDK binding built on the Pi (native/README.md)') from exc
            reader=L2Reader(live['lidar_ip'],live['host_ip'],live['lidar_port'],live['host_port'],18)
        self.reader=reader;self.clock_offset=time.monotonic()-time.time()
        # L2 device time runs at ~0.503x the Pi clock (see LidarL2Driver.L2DeviceClock): fit it onto the Pi's
        # monotonic clock from IMU arrivals. live.lidar_clock_scale fixes the slope instead (1.0 = the old
        # SDK-epoch-plus-fixed-offset mapping).
        from .lidar_l2_driver import L2DeviceClock
        scale=live.get('lidar_clock_scale')
        self.device_clock=L2DeviceClock() if scale is None else L2DeviceClock(scale=float(scale),offset=self.clock_offset if float(scale)==1. else None)
        if camera is None:
            from .gemini_333l_driver import Gemini335LDriver
            try:camera=Gemini335LDriver(self.calibration,live['camera_width'],live['camera_height'],live['camera_fps'],
                color_width=live.get('camera_color_width'),color_height=live.get('camera_color_height'),color_fps=live.get('camera_color_fps'),
                calibration_image_size=self.calibration_data.get('image_size'),align_mode=live.get('camera_align_mode','auto'),
                intrinsics_tolerance_px=live.get('camera_intrinsics_tolerance_px',1.0),distortion_tolerance=live.get('camera_distortion_tolerance',1e-4),
                retain_raw=True,decode=decode_color,bringup=self.bringup)
            except BaseException:self.reader.close();raise
        self.camera=camera
    def identity(self):
        meta=getattr(self.camera,'profile_metadata',{})
        return {'synthetic':False,'bringup':self.bringup,'lidar':{'kind':'unitree-l2','lidar_ip':self.live.get('lidar_ip'),'serial':'not exposed by the SDK binding',
                'clock_domain':'SDK synchronised epoch mapped to the node monotonic clock'},
                'camera':{'kind':'orbbec-gemini-335l','device':meta.get('device'),'profile':meta.get('aligned_output') or meta.get('selected_source_depth'),
                          'alignment':meta.get('alignment')}}
    def start(self,sink):
        def lidar():
            started=last=time.monotonic();seen=False
            try:
                while not self.stop.is_set():
                    packet=self.reader.poll()
                    if packet is None:
                        # A silent L2 (unplugged, wrong address) is an explicit fault, not an idle stream.
                        quiet=time.monotonic()-last
                        if quiet>(self.lidar_silence if seen else self.lidar_startup):
                            raise TimeoutError(f'no L2 packets for {quiet:.1f} s'+('' if seen else ' since start'))
                        time.sleep(.0005);continue
                    last=time.monotonic();seen=True
                    stamp=float(packet['timestamp']);clock=self.device_clock
                    if packet['type']=='imu':clock.add(stamp,last)
                    if not clock.ready:continue  # the first ~1 s of packets fit the clock
                    if packet['type']=='imu':sink.imu(stamp,clock.map(stamp),np.asarray(packet['gyro']),np.asarray(packet['accel']))
                    elif packet['type']=='cloud':
                        points=np.array(packet['points'],np.float32);duration=float(packet.get('duration',points[:,4].max() if len(points) else .1))
                        if len(points):points[:,4]*=clock.scale  # per-point times onto the host time scale
                        sink.cloud(packet['frame_id'],stamp,clock.map(stamp),duration*clock.scale,points)
            except BaseException as exc:sink.fault('lidar',f'L2 acquisition failed: {exc}')
        def camera():
            try:
                while not self.stop.is_set():
                    timestamp,rgb,_,_=self.camera.read();raw=self.camera.last_raw
                    sink.camera(timestamp,raw['depth'],raw['depth_scale'],rgb,self.camera.last_clock_metadata.get('metadata',{}),
                                device_color={'data':raw['color'],'format':raw['color_format']})
            except BaseException as exc:sink.fault('camera',f'Gemini acquisition failed: {exc}')
        for target,name in ((lidar,'l2-capture'),(camera,'gemini-capture')):
            thread=threading.Thread(target=target,name=name,daemon=True);thread.start();self.threads.append(thread)
    def close(self):
        self.stop.set()
        for thread in self.threads:thread.join(timeout=3)
        try:self.camera.close()
        finally:self.reader.close()


class FailedDevices:
    """Devices that could not be opened: the node still serves, so the host sees an explicit FAULT, not silence."""
    def __init__(self,error):self.error=error
    def identity(self):return {'synthetic':False,'failed':True,'error':str(self.error)}
    def start(self,sink):sink.fault('devices',f'capture devices failed to open: {self.error}')
    def close(self):pass


class CaptureServer:
    def __init__(self,devices,key,*,bind='192.168.1.2',port=7600,allowed_peers=('192.168.1.10',),codecs=None,calibration_path=None,
                 outbox=None,send_timeout=5.,handshake_timeout=5.,ping_timeout=5.,clock=rt.host_monotonic,max_handshakes=4):
        bind=check_bind(bind)
        self.peers={ipaddress.ip_address(p) for p in allowed_peers}
        if not self.peers:raise ValueError('List at least one allowed processing host')
        self.devices=devices;self.key=key;self.outbox=outbox or Outbox();self.sink=DeviceSink(self.outbox,codecs or {})
        self.clock=clock;self.outbox.clock=clock
        self.send_timeout=float(send_timeout);self.handshake_timeout=float(handshake_timeout);self.ping_timeout=float(ping_timeout)
        self.calibration=None if calibration_path is None else rt.file_identity(calibration_path)
        self.node_session=uuid.uuid4().hex;self.stop=threading.Event();self.lock=threading.Lock();self.session=None;self.sessions=0
        self.counts={'rejected_peers':0,'auth_failures':0,'protocol_errors':0,'sessions':0,'replaced_sessions':0,'accept_errors':0,'handshakes_refused':0}
        self.handshakes=threading.BoundedSemaphore(int(max_handshakes));self.session_threads=[]
        self.last_error=None;self.started_mono=time.monotonic()
        family=socket.AF_INET6 if ':' in str(bind) else socket.AF_INET
        self.listener=socket.socket(family,socket.SOCK_STREAM)
        if os.name!='nt':self.listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        self.listener.bind((bind,int(port)));self.listener.listen(int(max_handshakes));self.listener.settimeout(.25)
        self.address=self.listener.getsockname()
        self.accept_thread=threading.Thread(target=self._accept,name='capture-accept',daemon=True)
    def start(self):
        self.devices.start(self.sink);self.accept_thread.start();return self
    def identity(self):
        boot=None
        try:boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        except OSError:pass
        try:import cv2;cv2_version=cv2.__version__
        except ImportError:cv2_version=None
        return {'protocol':rt.PROTOCOL,'protocol_version':rt.VERSION,'node_version':NODE_VERSION,'node_session':self.node_session,
                'host':socket.gethostname(),'platform':platform.platform(),'machine':platform.machine(),'python':platform.python_version(),
                'numpy':np.__version__,'opencv':cv2_version,'boot_id':boot,'calibration':self.calibration,
                'codecs':{'depth':self.sink.depth_codec,'color':self.sink.color_codec,'camera_forward':self.sink.camera_forward},'node_utc_minus_mono':rt.utc_minus_mono(),
                'devices':self.devices.identity()}
    def _accept(self):
        while not self.stop.is_set():
            try:sock,peer=self.listener.accept()
            except (TimeoutError,socket.timeout):continue
            except OSError as exc:
                if self.stop.is_set():return
                # ECONNABORTED, EPROTO, ENETDOWN or EMFILE are transient: never let them end the accept loop.
                self.counts['accept_errors']+=1;self.last_error=f'accept failed: {exc}';self.stop.wait(.1);continue
            try:
                if ipaddress.ip_address(peer[0].split('%')[0]) not in self.peers:
                    self.counts['rejected_peers']+=1;self.last_error=f'rejected connection from unlisted peer {peer[0]}'
                    sock.close();continue
                if not self.handshakes.acquire(blocking=False):
                    self.counts['handshakes_refused']+=1;self.last_error=f'refused {peer[0]}: too many handshakes in progress'
                    sock.close();continue
                threading.Thread(target=self._handshake,args=(sock,peer),name='capture-handshake',daemon=True).start()
            except Exception as exc:
                self.counts['accept_errors']+=1;self.last_error=f'accept failed: {exc}';sock.close()
    def _handshake(self,sock,peer):
        try:
            sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1);channel=rt.Channel(sock)
            try:rt.server_handshake(channel,self.key,self.identity,self.handshake_timeout)
            except rt.AuthenticationError as exc:
                self.counts['auth_failures']+=1;self.last_error=f'{peer[0]}: {exc}';sock.close();return
            except Exception as exc:
                self.counts['protocol_errors']+=1;self.last_error=f'{peer[0]}: {exc}';sock.close();return
            session=Session(self,channel,peer)
            with self.lock:
                if self.stop.is_set():sock.close();return
                previous=self.session;self.session=session;self.counts['sessions']+=1
                self.session_threads=[s for s in self.session_threads if not s.closed.is_set()]+[session]
                if previous is not None:self.counts['replaced_sessions']+=1
            # An authenticated host replaces a stale one (for example after a host restart).
            if previous is not None:previous.close('replaced by a newly authenticated host')
            session.start()
        finally:self.handshakes.release()
    def status(self):
        with self.lock:session=self.session
        return {'node_session':self.node_session,'uptime_seconds':time.monotonic()-self.started_mono,'address':list(self.address[:2]),**self.counts,
                'last_error':self.last_error,'outbox':self.outbox.status(),'devices':self.sink.status(),
                'session':None if session is None else session.status()}
    def close(self):
        self.stop.set()
        try:self.listener.close()
        except OSError:pass
        with self.lock:session=self.session;self.session=None;sessions=list(self.session_threads)
        if session is not None:session.close('capture node stopping')
        if self.accept_thread.is_alive():self.accept_thread.join(timeout=3)
        for item in sessions:item.join(timeout=3)
        self.devices.close()


class Session:
    def __init__(self,server,channel,peer):
        self.server=server;self.channel=channel;self.peer=peer;self.closed=threading.Event();self.streaming=False
        self.started_mono=time.monotonic();self.reason=None;self.last_ping=time.monotonic()
        self.sender=threading.Thread(target=self._send,name='capture-send',daemon=True)
        self.receiver=threading.Thread(target=self._receive,name='capture-receive',daemon=True)
    def start(self):
        self.channel.sock.settimeout(self.server.send_timeout);self.receiver.start()
    def _receive(self):
        try:
            while not self.closed.is_set():
                try:kind,meta,_=self.channel.receive()
                except (TimeoutError,socket.timeout):
                    if time.monotonic()-self.last_ping>self.server.ping_timeout:raise rt.TransportError('processing host stopped pinging')
                    continue
                now=self.server.clock()
                if kind==rt.PING:
                    self.last_ping=time.monotonic();meta={'t1':meta.get('t1'),'t2':now,'node_utc_minus_mono':rt.utc_minus_mono()}
                    if self.streaming:self.server.outbox.put_control(rt.PONG,{**meta,'node':self.server.outbox.status()})
                    else:self.channel.send(rt.PONG,{**meta,'t3':self.server.clock()})
                elif kind==rt.START and not self.streaming:
                    self.server.outbox.start(self);self.streaming=True;self.server.sink.replay_faults();self.sender.start()
                elif kind==rt.BYE:raise ConnectionError('processing host said goodbye')
                else:raise rt.TransportError(f'unexpected {rt.NAMES.get(kind,kind)} from the processing host')
        except BaseException as exc:self.close(str(exc))
    def _send(self):
        try:
            while not self.closed.is_set():
                message=self.server.outbox.next_message(self)
                if message is not None:self.channel.send(*message)
        except BaseException as exc:self.close(f'send failed: {exc}')
    def close(self,reason):
        if self.closed.is_set():return
        self.closed.set();self.reason=reason
        with self.server.lock:
            if self.server.session is self:self.server.session=None
        self.server.outbox.stop(self)
        try:self.channel.sock.shutdown(socket.SHUT_RDWR)
        except OSError:pass
        self.channel.sock.close()
        if reason and 'goodbye' not in reason:self.server.last_error=f'session {self.peer[0]} closed: {reason}'
    def join(self,timeout):
        for thread in (self.sender,self.receiver):
            if thread.is_alive() and thread is not threading.current_thread():thread.join(timeout)
    def status(self):
        c=self.channel
        return {'peer':self.peer[0],'streaming':self.streaming,'age_seconds':time.monotonic()-self.started_mono,'bytes_sent':c.bytes_sent,
                'messages_sent':c.messages_sent,'bytes_received':c.bytes_received}


def load_config(path):
    """The node's YAML, following ``extends:`` like the host (config/profiles/hotspot.yaml extends the base)."""
    from core.configuration import load_config as load
    return load(path)


def source_root(config_path):
    """The tree holding ``config/``: relative calibration paths resolve from it for base and profile files alike."""
    path=Path(config_path).resolve()
    return next((p for p in path.parents if (p/'config').is_dir()),path.parent.parent)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--config',type=Path,default=Path('config/system_config.yaml'))
    p.add_argument('--generate-key',type=Path,metavar='PATH',help='Write a new shared key (mode 600) and exit')
    p.add_argument('--synthetic',action='store_true',help='Labelled synthetic devices (loopback tests and soaks only)')
    p.add_argument('--bringup',action='store_true',help='Real devices before a calibration exists (record calibration targets); '
                   'frames carry no calibration and the host must also run in bring-up mode')
    p.add_argument('--bind',help='Private address to listen on (overrides the config; e.g. the Pi\'s hotspot lease 192.168.137.37)')
    p.add_argument('--bind-interface',metavar='IFACE',help='Listen on the IPv4 address IFACE holds at start (Linux, e.g. wlan0 on the hotspot); '
                   'the node exits for a systemd restart if that address changes')
    p.add_argument('--port',type=int);p.add_argument('--allow',action='append',help='Allowed processing host (repeatable; replaces the config list)')
    p.add_argument('--key-file',type=Path);p.add_argument('--status-interval',type=float,default=10.)
    p.add_argument('--duration',type=float,help='Stop after this many seconds (soaks)')
    args=p.parse_args(argv)
    if args.generate_key:print(f'Wrote {rt.generate_key(args.generate_key)}; copy it to the processing host over SSH, never over the sensor link');return 0
    config=load_config(args.config) if args.config.exists() else {}
    node=dict(config.get('capture_node') or {});live=dict(config.get('live') or {})
    key_file=args.key_file or node.get('key_file')
    if not key_file:raise SystemExit('Set capture_node.key_file or pass --key-file')
    key=rt.load_key(key_file);root=source_root(args.config)
    interface=None if args.bind else args.bind_interface or node.get('bind_interface')
    try:bind=bind_address(node,args.bind,interface)
    except (RuntimeError,ValueError) as exc:print(json.dumps({'event':'bind_failed','interface':interface,'error':str(exc)}),flush=True);return 6
    if args.synthetic:devices=SyntheticDevices(**(node.get('synthetic') or {}));calibration_path=None
    else:
        calibration_path=None
        try:devices=HardwareDevices(live,root,decode_color=node.get('color_codec','device')!='device',bringup=args.bringup);calibration_path=devices.calibration_path
        except Exception as exc:
            print(json.dumps({'event':'device_open_failed','error':str(exc)}),flush=True);devices=FailedDevices(exc)
    server=CaptureServer(devices,key,bind=bind,port=args.port or node.get('port',7600),
        allowed_peers=args.allow or node.get('allowed_peers',['192.168.1.10']),calibration_path=calibration_path,
        codecs={'depth':node.get('depth_codec','raw'),'color':node.get('color_codec','device'),'color_quality':node.get('color_quality',90),
                'depth_level':node.get('depth_level',1),'camera_forward':node.get('camera_forward','all'),
                'camera_sweep_window_seconds':node.get('camera_sweep_window_seconds',.02)},
        outbox=Outbox(node.get('imu_queue_samples',20000),node.get('cloud_queue_sweeps',4),node.get('camera_queue_frames',2),node.get('fragment_bytes',1024*1024)))
    server.start();print(json.dumps({'event':'listening','address':list(server.address[:2]),'interface':interface,'synthetic':bool(args.synthetic)}),flush=True)
    started=time.monotonic();last_status=started;last_lease=started;code=0
    try:
        while not args.duration or time.monotonic()-started<args.duration:
            time.sleep(.5)
            if server.sink.faults:
                # Give the host time to receive the FAULT, then exit so systemd restarts the devices cleanly.
                time.sleep(2.);print(json.dumps({'event':'device_fault','faults':server.sink.faults,'status':server.status()},default=str),flush=True)
                code=3;break
            if not server.accept_thread.is_alive():
                print(json.dumps({'event':'accept_stopped','status':server.status()},default=str),flush=True);code=4;break
            if interface and time.monotonic()-last_lease>=LEASE_CHECK_SECONDS:
                # A new DHCP lease leaves this listener on a dead address: exit so systemd rebinds. No address (Wi-Fi down) is waited out.
                last_lease=time.monotonic();current=interface_ipv4(interface)
                if current is not None and current!=bind:
                    print(json.dumps({'event':'address_changed','interface':interface,'bound':bind,'now':current}),flush=True);code=5;break
            if time.monotonic()-last_status>=args.status_interval:
                last_status=time.monotonic();print(json.dumps({'event':'status',**server.status()},default=str),flush=True)
    except KeyboardInterrupt:pass
    finally:server.close()
    return code


if __name__=='__main__':sys.exit(main())

"""Processing-host receiver for a Pi capture node (``live.source: remote``).

``RemoteSensorSource`` has the iterator contract of ``LiveSensorSource``: it
reuses ``LidarL2Driver``'s sweep assembly (fed by transported SDK packets) and
``SensorSynchronizer`` unchanged, so local and remote acquisition synchronise
identically. Node timestamps are mapped onto this host's monotonic clock by the
ping exchange in ``raw_transport.PeerClock``.

Segments: a reconnect, a clock jump, a broken IMU sequence or a receiver
overflow starts a new segment. Buffered packets are discarded, no sweep or
synchronised frame spans a boundary, and the first frame of a segment carries
``segment_start``. A missing modality is an explicit fault; nothing is simulated.
"""
from __future__ import annotations
from collections import deque
from dataclasses import replace
from pathlib import Path
import socket,threading,time
import numpy as np
from . import raw_transport as rt
from .gemini_333l_driver import depth_meters
from .sensor_synchronizer import SensorSynchronizer


class ClockJump(rt.TransportError):
    """The node clock mapping broke; the session is dropped and re-established with a fresh clock exchange."""


class SegmentChanged(Exception):
    def __init__(self,segment):super().__init__(f'raw-sensor segment {segment} started');self.segment=segment


class RemoteL2Reader:
    """``L2Reader.poll()`` replacement over the transport; raises SegmentChanged at a boundary."""
    def __init__(self,capacity=50000):
        self.queue=deque();self.capacity=int(capacity);self.lock=threading.Lock();self.segment=0;self.closed=False
    def push(self,segment,packet):
        with self.lock:
            if len(self.queue)>=self.capacity:return False
            self.queue.append((segment,packet));return True
    def clear(self):
        with self.lock:count=len(self.queue);self.queue.clear()
        return count
    def poll(self):
        with self.lock:
            if not self.queue:return None
            segment,packet=self.queue[0]
            if segment!=self.segment:
                self.segment=segment;raise SegmentChanged(segment)
            self.queue.popleft();return packet
    def depth(self):
        with self.lock:return len(self.queue)
    def close(self):self.closed=True


class RawSensorClient:
    """Connects, authenticates, maps clocks, reconnects with backoff; one receiver and one ping thread."""
    def __init__(self,host,port,key,*,on_packet,on_camera,on_segment,verify_identity,ping_interval=.5,clock_pings=5,
                 stall_seconds=3.,connect_timeout=5.,reconnect=True,backoff=(.25,5.),clock_jump_seconds=.02,clock_slew=1e-4,
                 future_tolerance_seconds=.05,reorder_tolerance_seconds=.001,max_short_sessions=5,short_session_seconds=5.):
        self.host=host;self.port=int(port);self.key=key;self.on_packet=on_packet;self.on_camera=on_camera;self.on_segment=on_segment
        self.verify_identity=verify_identity;self.ping_interval=float(ping_interval);self.clock_pings=int(clock_pings)
        self.stall_seconds=float(stall_seconds);self.connect_timeout=float(connect_timeout);self.reconnect=bool(reconnect);self.backoff=backoff
        self.clock_jump=float(clock_jump_seconds);self.clock_slew=float(clock_slew);self.future_tolerance=float(future_tolerance_seconds)
        self.reorder_tolerance=float(reorder_tolerance_seconds);self.max_short_sessions=int(max_short_sessions);self.short_session=float(short_session_seconds)
        # Repeated short sessions (a clock that keeps jumping, a node that keeps failing) or auth failures end in an explicit fatal error.
        self.short_sessions=0;self.auth_streak=0;self.connecting=None;self.dropped_camera_seq=None;self.node_utc_first=None
        self.stop=threading.Event();self.lock=threading.Lock();self.thread=None;self.channel=None;self.send_lock=threading.Lock()
        self.state='connecting';self.segment=0;self.identity=None;self.fatal=None;self.last_error=None;self.faults={}
        self.connected_event=threading.Event();self.clock=None;self.disconnected_at=None;self.fragment=None
        self.expected={'imu':None,'cloud':None,'camera':None};self.last_mono={'imu':None,'cloud':None,'camera':None}
        self.counts={'connections':0,'connect_failures':0,'auth_failures':0,'protocol_errors':0,'stalls':0,'segments':0,'clock_jumps':0,
                     'imu_samples':0,'imu_sequence_breaks':0,'imu_missing_samples':0,'node_gaps':0,'clouds':0,'cloud_missing':0,
                     'camera_frames':0,'camera_missing':0,'camera_incomplete':0,'receiver_overflows':0,'decode_errors':0,'bytes':0,
                     'imu_out_of_order_dropped':0,'clouds_out_of_order_dropped':0,'node_utc_steps':0}
        self.rates={key:deque(maxlen=256) for key in ('imu','cloud','camera')};self.segment_reasons=deque(maxlen=16);self.node=None
        self.camera_decode_ms=deque(maxlen=256);self.bytes_window=deque(maxlen=512)
        self.latency={key:deque(maxlen=512) for key in ('imu','cloud','camera')}

    def start(self):
        self.thread=threading.Thread(target=self._run,name='raw-sensor-client',daemon=True);self.thread.start();return self

    def new_segment(self,reason):
        """Receiver thread only. Every later packet carries the new number."""
        if self.stop.is_set():return
        with self.lock:
            self.segment+=1;self.counts['segments']+=1;self.segment_reasons.append({'segment':self.segment,'reason':reason,'at_mono':time.monotonic()})
            self.expected={'imu':None,'cloud':None,'camera':None};self.last_mono={'imu':None,'cloud':None,'camera':None};self.fragment=None
        self.on_segment(self.segment,reason)

    def _send(self,kind,meta=None):
        with self.send_lock:self.channel.send(kind,meta)

    def _connect_once(self):
        sock=rt.connect(self.host,self.port,self.connect_timeout);channel=rt.Channel(sock)
        with self.lock:self.connecting=sock
        if self.stop.is_set():sock.close();raise ConnectionError('client closing')
        try:
            identity=rt.client_handshake(channel,self.key,self.connect_timeout)
            self.verify_identity(identity)
            clock=rt.PeerClock(slew=self.clock_slew,jump=self.clock_jump)
            for _ in range(self.clock_pings):
                t1=rt.host_monotonic();channel.send(rt.PING,{'t1':t1})
                kind,meta,_=channel.receive();t4=rt.host_monotonic()
                if kind!=rt.PONG or meta.get('t1')!=t1:raise rt.TransportError('Expected PONG during clock exchange')
                clock.add(t1,float(meta['t2']),float(meta['t3']),t4,meta.get('node_utc_minus_mono'),rt.utc_minus_mono())
            clock.anchor()
        except BaseException:
            sock.close();raise
        finally:
            with self.lock:self.connecting=None
        return channel,identity,clock

    def _run(self):
        delay=self.backoff[0]
        while not self.stop.is_set():
            try:channel,identity,clock=self._connect_once()
            except rt.AuthenticationError as exc:
                # Fatal before the first session (wrong key); later, something answering at the node address
                # (for example while the Pi reboots) is retried a few times before it is fatal.
                self.counts['auth_failures']+=1;self.auth_streak+=1;self.last_error=f'connect: {exc}'
                if self.counts['connections']==0 or self.auth_streak>=self.max_short_sessions or not self.reconnect:self._fail(exc,fatal=True);return
                self.stop.wait(delay);delay=min(delay*2,self.backoff[1]);continue
            except IdentityMismatch as exc:self._fail(exc,fatal=True);return
            except Exception as exc:
                self.counts['connect_failures']+=1;self.last_error=f'connect: {type(exc).__name__}: {exc}'
                if not self.reconnect and self.counts['connections']==0:self._fail(exc,fatal=True);return
                self.stop.wait(delay);delay=min(delay*2,self.backoff[1]);continue
            if self.stop.is_set():channel.sock.close();return
            delay=self.backoff[0];self.auth_streak=0;session_started=time.monotonic();self.node_utc_first=None
            with self.lock:self.channel=channel;self.identity=identity;self.clock=clock;self.counts['connections']+=1
            self.new_segment('connected' if self.counts['connections']==1 else f'reconnected after: {self.last_error}')
            channel.sock.settimeout(self.stall_seconds)
            try:
                self._send(rt.START,{'segment':self.segment})
                with self.lock:self.state='streaming';self.disconnected_at=None
                self.connected_event.set()
                pinger=threading.Thread(target=self._ping,args=(channel,),name='raw-sensor-ping',daemon=True);pinger.start()
                self._receive(channel)
            except rt.AuthenticationError as exc:
                self.counts['auth_failures']+=1;self.last_error=str(exc)
            except (TimeoutError,socket.timeout) as exc:
                self.counts['stalls']+=1;self.last_error=f'stall: no message for {self.stall_seconds:g} s'
            except OSError as exc:self.last_error=str(exc)
            except Exception as exc:
                # Includes malformed (authenticated) values: a protocol error, retried, escalated below if it repeats.
                self.counts['protocol_errors']+=1;self.last_error=f'{type(exc).__name__}: {exc}'
            except BaseException as exc:
                self._fail(exc,fatal=True)
            finally:
                with self.lock:
                    self.state='reconnecting' if self.reconnect and not self.stop.is_set() and self.fatal is None else 'disconnected'
                    self.disconnected_at=time.monotonic()
                try:channel.sock.close()
                except OSError:pass
            if self.stop.is_set():return
            self.short_sessions=self.short_sessions+1 if time.monotonic()-session_started<self.short_session else 0
            if self.short_sessions>=self.max_short_sessions and self.fatal is None:
                self._fail(ConnectionError(f'{self.short_sessions} consecutive raw-sensor sessions ended within {self.short_session:g} s; '
                                           f'last error: {self.last_error}'),fatal=True)
            if self.fatal is not None or not self.reconnect:
                if self.fatal is None:self._fail(ConnectionError(self.last_error or 'raw-sensor link closed'),fatal=True)
                return
            self.stop.wait(delay)

    def _fail(self,exc,fatal):
        with self.lock:
            self.last_error=str(exc)
            if fatal:self.fatal=exc;self.state='failed'
        self.connected_event.set()

    def _ping(self,channel):
        while not self.stop.is_set() and self.state=='streaming' and channel is self.channel:
            try:
                with self.send_lock:channel.send(rt.PING,{'t1':rt.host_monotonic()})
            except OSError:return
            self.stop.wait(self.ping_interval)

    def _receive(self,channel):
        while not self.stop.is_set():
            kind,meta,payload=channel.receive();now=rt.host_monotonic()
            self.counts['bytes']=channel.bytes_received;self.bytes_window.append((now,channel.bytes_received))
            clock=self.clock;clock.update(now)
            if kind==rt.IMU:self._imu(meta,payload,now)
            elif kind==rt.CLOUD:self._cloud(meta,payload,now)
            elif kind==rt.CAMERA:self._camera(meta,payload,now)
            elif kind==rt.PONG:
                clock.add(float(meta['t1']),float(meta['t2']),float(meta['t3']),now,meta.get('node_utc_minus_mono'),rt.utc_minus_mono())
                self.node=meta.get('node');utc=meta.get('node_utc_minus_mono')
                if isinstance(utc,(int,float)):
                    # A UTC step on the node does not move its monotonic clock, but L2 epochs taken before it may disagree.
                    if self.node_utc_first is None:self.node_utc_first=float(utc)
                    elif abs(float(utc)-self.node_utc_first)>.01:
                        self.counts['node_utc_steps']+=1;self.last_error=f'capture node UTC stepped by {float(utc)-self.node_utc_first:+.3f} s';self.node_utc_first=float(utc)
                if abs(clock.error())>self.clock_jump:
                    # Never slew a step: reconnect for a fresh exchange, so no time is spliced across the jump.
                    self.counts['clock_jumps']+=1;raise ClockJump(f'node clock offset moved {clock.error():+.6f} s')
            elif kind==rt.FAULT:
                modality=str(meta.get('modality','unknown'));self.faults[modality]=meta
                self.last_error=f'capture node {modality} fault: {meta.get("error")}'
            elif kind==rt.GAP:
                self.counts['node_gaps']+=1;self.last_error=f'capture node gap: {meta}'
            elif kind==rt.BYE:raise ConnectionError('capture node closed the session')
            else:raise rt.TransportError(f'unexpected {rt.NAMES.get(kind,kind)} from the capture node')

    def _check_time(self,stream,capture_mono,now):
        """Host time for a node capture time; a backwards or future time is a clock jump."""
        last=self.last_mono[stream];mapped=self.clock.to_host(capture_mono)
        if last is not None and capture_mono<last:
            if last-capture_mono<=self.reorder_tolerance and stream in ('imu','cloud'):
                # A slightly reordered SDK packet is dropped and counted, not treated as a clock jump.
                self.counts[f'{stream}_out_of_order_dropped' if stream=='imu' else 'clouds_out_of_order_dropped']+=1;return None
            self.counts['clock_jumps']+=1;raise ClockJump(f'{stream} capture time went backwards by {last-capture_mono:.6f} s')
        if mapped-now>self.future_tolerance:
            self.counts['clock_jumps']+=1;raise ClockJump(f'{stream} capture time is {mapped-now:.6f} s in the future')
        self.last_mono[stream]=capture_mono;self.latency[stream].append(now-mapped);return mapped

    def _deliver(self,packet):
        if not self.on_packet(self.segment,packet):
            self.counts['receiver_overflows']+=1;self.new_segment('receiver queue overflow (consumer too slow)')
            self.on_packet(self.segment,packet)

    def _imu(self,meta,payload,now):
        first=int(meta['first_seq']);count=int(meta['count'])
        if count<1 or len(payload)!=count*rt.IMU_COLUMNS*8:raise rt.TransportError('IMU payload size mismatch')
        rows=np.frombuffer(payload,'<f8').reshape(count,rt.IMU_COLUMNS)
        if not np.isfinite(rows).all():raise rt.TransportError('Non-finite IMU values')
        expected=self.expected['imu']
        if expected is not None and first!=expected:
            # IMU is never silently lost: a broken sequence ends the segment.
            self.counts['imu_sequence_breaks']+=1;self.counts['imu_missing_samples']+=max(first-expected,0)
            self.new_segment(f'IMU sequence break: expected {expected}, got {first}')
        self.expected['imu']=first+count;self.counts['imu_samples']+=count;self.rates['imu'].append((now,count))
        for row in rows:
            mapped=self._check_time('imu',row[1],now)
            if mapped is None:continue
            self._deliver({'type':'imu','timestamp':mapped,'device_timestamp':float(row[0]),'capture_mono':float(row[1]),
                           'gyro':row[2:5].copy(),'accel':row[5:8].copy()})

    def _cloud(self,meta,payload,now):
        shape=[int(v) for v in meta['shape']]
        if len(shape)!=2 or shape[1]<5 or meta.get('dtype')!='<f4' or len(payload)!=shape[0]*shape[1]*4:raise rt.TransportError('Cloud payload size mismatch')
        points=np.frombuffer(payload,'<f4').reshape(shape).copy()
        if not np.isfinite(points[:,:5]).all():raise rt.TransportError('Non-finite cloud values')
        seq=int(meta['seq']);expected=self.expected['cloud']
        if expected is not None and seq>expected:self.counts['cloud_missing']+=seq-expected
        self.expected['cloud']=seq+1;self.counts['clouds']+=1;self.rates['cloud'].append((now,1))
        mono=float(meta['capture_mono']);mapped=self._check_time('cloud',mono,now)
        if mapped is None:return
        self._deliver({'type':'cloud','timestamp':mapped,'device_timestamp':float(meta['device_timestamp']),'capture_mono':mono,
                       'frame_id':int(meta['frame_id']),'duration':float(meta['duration']),'points':points})

    def _camera(self,meta,payload,now):
        index=int(meta['index']);count=int(meta['count']);seq=int(meta['seq'])
        if index==0:
            if self.fragment is not None:self.counts['camera_incomplete']+=1
            size=int(meta['bytes'])
            if size<1 or size>rt.MAX_MESSAGE or count<1:raise rt.TransportError('Invalid camera frame size')
            self.fragment={'meta':meta,'body':bytearray(),'next':0,'count':count,'size':size,'segment':self.segment}
        fragment=self.fragment
        if fragment is None or fragment['meta']['seq']!=seq or fragment['next']!=index or fragment['count']!=count:
            # Count each lost frame once, however many of its fragments still arrive.
            if seq!=self.dropped_camera_seq:self.counts['camera_incomplete']+=1;self.dropped_camera_seq=seq
            self.fragment=None;return
        fragment['body']+=payload;fragment['next']+=1
        if len(fragment['body'])>fragment['size']:raise rt.TransportError('Camera fragments exceed the declared size')
        if fragment['next']<count:return
        self.fragment=None
        if len(fragment['body'])!=fragment['size'] or fragment['segment']!=self.segment:
            self.counts['camera_incomplete']+=1;return
        meta=fragment['meta'];body=memoryview(fragment['body']);started=time.perf_counter()
        expected=self.expected['camera']
        if expected is not None and seq>expected:self.counts['camera_missing']+=seq-expected
        self.expected['camera']=seq+1
        depth_meta=meta['depth'];color_meta=meta['color'];split=int(depth_meta['bytes'])
        if split+int(color_meta['bytes'])!=len(body):raise rt.TransportError('Camera body size mismatch')
        try:
            raw=rt.decode_depth(body[:split],depth_meta['codec'],depth_meta['shape'])
            rgb=rt.decode_color(body[split:],color_meta['codec'],color_meta['shape'],color_meta.get('format'))
        except (rt.TransportError,ValueError) as exc:
            self.counts['decode_errors']+=1;self.last_error=f'camera decode: {exc}';return
        if rgb.shape[:2]!=raw.shape:raise rt.TransportError('Colour and depth shapes differ after alignment')
        self.camera_decode_ms.append(1000*(time.perf_counter()-started))
        mono=float(meta['capture_mono']);mapped=self._check_time('camera',mono,now)
        metadata=dict(meta.get('metadata') or {})
        metadata['transport_codecs']={'depth':depth_meta['codec'],'color':color_meta['codec'],'color_lossy':bool(color_meta.get('lossy'))}
        self.counts['camera_frames']+=1;self.rates['camera'].append((now,1))
        self.on_camera(self.segment,(mapped,rgb,depth_meters(raw,depth_meta['scale'])),metadata)

    def _rate(self,stream,now,window=5.):
        items=[(t,n) for t,n in list(self.rates[stream]) if now-t<=window]
        if len(items)<2:return None
        span=items[-1][0]-items[0][0]
        return None if span<=0 else sum(n for _,n in items[1:])/span

    def status(self):
        now=time.monotonic()
        with self.lock:
            window=[(t,b) for t,b in list(self.bytes_window) if now-t<=5.]
            bandwidth=None if len(window)<2 or window[-1][0]<=window[0][0] else (window[-1][1]-window[0][1])/(window[-1][0]-window[0][0])
            decode=list(self.camera_decode_ms)
            return {'state':self.state,'segment':self.segment,'capture_host':self.host,'capture_port':self.port,**self.counts,
                    'rates_hz':{key:self._rate(key,now) for key in self.rates},'bandwidth_bytes_per_second':bandwidth,
                    'clock':None if self.clock is None else {**self.clock.status(),'host_clock_alignment_seconds':rt.host_clock_alignment()},
                    'last_error':self.last_error,'faults':dict(self.faults),
                    'outage_seconds':None if self.disconnected_at is None else now-self.disconnected_at,
                    'camera_decode_ms_p95':None if not decode else float(np.percentile(decode,95)),
                    'arrival_latency_ms':{key:None if not values else {'p50':1000*float(np.percentile(values,50)),'p95':1000*float(np.percentile(values,95)),
                        'max':1000*float(np.max(values))} for key,values in ((k,list(v)) for k,v in self.latency.items())},
                    'segment_reasons':list(self.segment_reasons)[-4:],'node':self.node,
                    'node_identity':None if self.identity is None else {k:self.identity.get(k) for k in ('host','node_session','node_version','boot_id','codecs')}}

    def close(self):
        self.stop.set()
        with self.lock:connecting=self.connecting
        if connecting is not None:
            try:connecting.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        channel=self.channel
        if channel is not None:
            try:
                with self.send_lock:channel.send(rt.BYE)
            except OSError:pass
            try:channel.sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            channel.sock.close()
        if self.thread is not None:self.thread.join(timeout=5)


class IdentityMismatch(ValueError):
    """The node's calibration, protocol or device kind differs from this host's configuration (not retried)."""


class RemoteSensorSource:
    """``LiveSensorSource`` contract over the raw-sensor transport."""
    def __init__(self,config,root=Path('.'),*,key=None):
        from .live_source import load_measured_calibration
        from .lidar_l2_driver import LidarL2Driver
        if config.get('s2_port'):raise ValueError('RPLIDAR S2 is not carried by the raw-sensor transport; connect it to this host or leave s2_port empty')
        # Bring-up records calibration targets before a calibration exists: frames carry calibration=None
        # (never fused) and the node must be in bring-up mode too. Normal live mode needs the measured file.
        self.bringup=bool(config.get('capture_bringup',False))
        if self.bringup:self.calibration,data,self.calibration_identity=None,{},None
        else:
            self.calibration,data,path=load_measured_calibration(config,root);self.calibration_identity=rt.file_identity(path)
        self.allow_synthetic=bool(config.get('allow_synthetic_capture',False))
        self.imu_transform=np.asarray(data['T_lidar_imu']) if 'T_lidar_imu' in data else None
        self.initial_pose=np.asarray(config.get('initial_pose_world',np.eye(4)),dtype=float)
        self.sync=SensorSynchronizer(capacity=config.get('sync_capacity',8),camera_max_bytes=config.get('camera_buffer_bytes',64*1024*1024),
            camera_history_seconds=config.get('camera_history_seconds',.75),imu_capacity=config.get('imu_capacity',8192))
        self.lock=threading.Lock();self.sync_segment=0;self.consumer_segment=0;self.segment_started=False
        self.frames=0;self.dropped_at_boundary=0;self.camera_at_boundary=0;self.packets_cleared=0
        self.max_outage=float(config.get('capture_max_outage_seconds',10.));self.sync_timeout=float(config.get('capture_sync_timeout_seconds',1.))
        # A CPU-starved host can miss one window while the node keeps streaming; retry a few before giving up.
        self.sync_retries=int(config.get('capture_sync_retries',4));self.sync_timeouts=0
        self.reader=RemoteL2Reader(config.get('capture_receiver_packets',50000))
        self.lidar=LidarL2Driver(reader=self.reader,clock_offset=0.,timeout=float(config.get('capture_lidar_timeout_seconds',1.)),
            transport='Pi capture node: authenticated TCP raw-sensor transport (Unitree SDK UDP on the node)',
            clock_domain='node monotonic clock mapped to host monotonic by ping exchange')
        if key is None:
            key_path=Path(config.get('capture_key_file','config/capture_node.key'));key_path=key_path if key_path.is_absolute() else Path(root)/key_path
            key=rt.load_key(key_path)
        host=config.get('capture_host','192.168.1.2')
        # The hotspot profile leaves it empty: the Pi's DHCP lease is only known once it has joined.
        if not host:raise ValueError("live.capture_host is empty: pass the Pi's address with --capture-host (hotspot: the 192.168.137.x lease shown under the hotspot's connected devices)")
        self.client=RawSensorClient(host,config.get('capture_port',7600),key,
            on_packet=self.reader.push,on_camera=self._on_camera,on_segment=self._on_segment,verify_identity=self._verify,
            ping_interval=config.get('capture_ping_interval_seconds',.5),stall_seconds=config.get('capture_stall_seconds',3.),
            connect_timeout=config.get('capture_connect_timeout_seconds',5.),reconnect=config.get('capture_reconnect',True),
            clock_jump_seconds=config.get('capture_clock_jump_seconds',.02),
            future_tolerance_seconds=config.get('capture_future_tolerance_seconds',.05)).start()
        if not self.client.connected_event.wait(float(config.get('capture_initial_wait_seconds',15.))) or self.client.fatal is not None:
            error=self.client.fatal or TimeoutError(f'No capture node at {self.client.host}:{self.client.port} ({self.client.last_error})')
            self.client.close();raise RuntimeError(f'Raw-sensor transport unavailable: {error}') from error

    def _verify(self,identity):
        if identity.get('protocol')!=rt.PROTOCOL or identity.get('protocol_version')!=rt.VERSION:raise IdentityMismatch('Capture node protocol differs')
        devices=identity.get('devices') or {}
        if devices.get('synthetic') and not self.allow_synthetic:
            raise IdentityMismatch('Capture node runs synthetic devices; live mode refuses them (set allow_synthetic_capture only for tests)')
        node=(identity.get('calibration') or {}).get('sha256')
        if self.bringup:
            if node is not None or (not devices.get('synthetic') and not devices.get('bringup')):
                raise IdentityMismatch('This host is in bring-up mode but the capture node is not (start it with --bringup)')
            return
        if devices.get('bringup'):raise IdentityMismatch('Capture node runs in bring-up mode (no calibration); live processing needs the measured calibration')
        if not devices.get('synthetic') and node!=self.calibration_identity['sha256']:
            raise IdentityMismatch(f'Calibration differs between capture node ({node}) and this host ({self.calibration_identity["sha256"]}); copy the same measured file to both')

    def _on_segment(self,segment,reason):
        self.packets_cleared+=self.reader.clear()
        with self.lock:self._reset_sync_locked(segment)

    def _reset_sync_locked(self,segment):
        if segment<=self.sync_segment:return
        sync=self.sync;self.camera_at_boundary+=len(sync.camera);self.dropped_at_boundary+=len(sync.lidar)
        sync.lidar.clear();sync.camera.clear();sync.imu.clear();sync.last_output_end=None;self.sync_segment=segment

    def _on_camera(self,segment,item,metadata):
        with self.lock:
            self._reset_sync_locked(segment)
            if segment==self.sync_segment:self.sync.push_camera(*item,self.calibration,metadata)

    def __iter__(self):return self

    def __next__(self):
        deadline=time.monotonic()+self.sync_timeout;retries=0
        while True:
            if self.client.fatal is not None:raise RuntimeError(f'Raw-sensor transport failed: {self.client.fatal}') from self.client.fatal
            faults=dict(self.client.faults)
            if faults:
                modality,fault=next(iter(faults.items()))
                raise RuntimeError(f'Required {modality} acquisition failed on the capture node: {fault.get("error")}')
            with self.lock:result=self.sync.synchronize() if self.sync_segment==self.consumer_segment else None
            if result is not None:return self._finish(result)
            if time.monotonic()>deadline and self.client.state=='streaming':
                if retries<self.sync_retries:
                    retries+=1;self.sync_timeouts+=1;deadline=time.monotonic()+self.sync_timeout;continue
                raise TimeoutError(f'Cannot synchronize required LiDAR, IMU and RGB-D streams within tolerance (transport: {self._brief()})')
            try:frame=self.lidar.next_frame()
            except SegmentChanged as change:
                self.lidar.reset();self.consumer_segment=change.segment;self.segment_started=True
                with self.lock:self._reset_sync_locked(change.segment)
                deadline=time.monotonic()+self.sync_timeout;continue
            except TimeoutError:
                status=self.client.status()
                if status['state']=='reconnecting' and (status['outage_seconds'] or 0)<self.max_outage:
                    deadline=time.monotonic()+self.sync_timeout;continue
                if status['state']=='streaming':continue
                raise TimeoutError(f'Raw-sensor transport has no data (transport: {self._brief()})')
            with self.lock:
                if self.sync_segment!=self.consumer_segment:self.dropped_at_boundary+=1;continue
                self.sync.push_lidar(frame)
                for sample in frame.imu_samples:self.sync.push_imu(sample)

    def _brief(self):
        s=self.client.status();return f"state {s['state']}, segment {s['segment']}, last error {s['last_error']}"

    def _finish(self,result):
        self.frames+=1;start=self.segment_started;self.segment_started=False
        status=self.client.status();status['receiver_queue_packets']=self.reader.depth()
        status['dropped_at_segment_boundary']={'sweeps':self.dropped_at_boundary,'camera_frames':self.camera_at_boundary,
                                               'queued_packets':self.packets_cleared}
        fields={'sensor_pose_world':self.initial_pose}
        if self.imu_transform is not None:fields['imu_to_lidar']=self.imu_transform
        fields['diagnostics']={**result.diagnostics,'raw_transport':status,'acquisition_segment':self.consumer_segment,'segment_start':start,
                               'capture_bringup_uncalibrated':self.bringup}
        return replace(result,**fields)

    def status(self):return self.client.status()

    def close(self):
        try:self.client.close()
        finally:self.lidar.close()

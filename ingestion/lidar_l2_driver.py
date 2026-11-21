"""Unitree SDK adapter. Echo index/count are unavailable, never fabricated."""
from collections import deque
import time
import numpy as np
from core.contracts import MultiModalSensorFrame,IMUSample,SensorCapabilities


class L2DeviceClock:
    """Maps L2 device timestamps onto a host clock with a fitted scale and offset.

    Measured on the rig (unilidar_sdk2 2.0.9, firmware 2.8.11.1): device stamps advance at 0.503 x the Pi's
    NTP-locked clock, with or without syncLidarTimeStamp(); IMU arrives at 251.5/s and clouds at 12.0/s by
    the host clock (about 500/s and 24/s by device time). A fixed offset would stretch every interval about
    2x. The slope is a least-squares fit of (device stamp, host arrival) over a sliding window; the offset is
    the lower envelope of host - slope*device (arrival = true time + a non-negative delay). A pair whose
    device step disagrees with its host step by more than ``jump_seconds`` (a stale first packet, an L2
    reset) restarts the fit. ``scale`` is host seconds per device second (about 1.99 on the rig); passing it
    fixes the slope (1.0 with ``offset`` gives the old fixed mapping)."""
    def __init__(self,window_seconds=10.,min_span=1.,jump_seconds=.5,scale=None,offset=None):
        self.window=float(window_seconds);self.min_span=float(min_span);self.jump=float(jump_seconds)
        self.fixed=None if scale is None else float(scale);self.scale=1. if scale is None else float(scale)
        self.offset=None if offset is None else float(offset);self.pairs=deque();self.restarts=0;self._since=0
    @property
    def ready(self):
        if self.fixed is not None and self.offset is not None:return True
        return len(self.pairs)>=8 and self.pairs[-1][1]-self.pairs[0][1]>=self.min_span
    def add(self,device,host):
        if self.fixed is not None and self.offset is not None:return
        if self.pairs:
            d0,h0=self.pairs[-1]
            if abs((device-d0)-self.scale*(host-h0))>self.jump or device<d0:self.pairs.clear();self.restarts+=1
        self.pairs.append((float(device),float(host)))
        while self.pairs and self.pairs[-1][1]-self.pairs[0][1]>self.window:self.pairs.popleft()
        self._since+=1
        if self.ready and (self.offset is None or self._since>=25):self._fit()
    def _fit(self):
        data=np.asarray(self.pairs);x=data[:,0];y=data[:,1];self._since=0
        if self.fixed is None:
            x0=x.mean();slope=np.dot(x-x0,y-y.mean())/max(np.dot(x-x0,x-x0),1e-12)
            if not .1<slope<10:raise ValueError(f'Implausible L2 device clock rate {slope:.4f}x host')
            self.scale=float(slope)
        self.offset=float(np.min(y-self.scale*x))
    def map(self,device):
        if not self.ready:raise RuntimeError('L2 device clock is not fitted yet')
        return self.offset+self.scale*float(device)
    def status(self):return {'scale':self.scale,'offset':self.offset,'fitted':self.fixed is None,'pairs':len(self.pairs),'restarts':self.restarts}


class LidarL2Driver:
    def __init__(self,lidar_ip='192.168.1.62',local_ip='192.168.1.2',lidar_port=6101,local_port=6201,scan_lines=18,timeout=3.,imu_to_lidar=None,*,
                 reader=None,clock_offset=None,transport='Unitree SDK UDP',clock_domain='SDK synchronized device epoch mapped to monotonic',
                 max_sweep_seconds=.25,clock_scale=None,host_clock=time.monotonic):
        """``reader``/``clock_offset`` let the raw-sensor receiver feed packets already on this host's monotonic clock.

        Clouds whose point times span more than ``max_sweep_seconds`` are rejected and counted: the first cloud
        a fresh SDK reader returns is a stale leftover spanning the time since the previous reader stopped
        (16 s measured on the rig, against ~0.042 s per real sweep)."""
        self.max_sweep_seconds=float(max_sweep_seconds)
        if not self.max_sweep_seconds>0:raise ValueError('max_sweep_seconds must be positive')
        self.imu_to_lidar=np.eye(4) if imu_to_lidar is None else np.array(imu_to_lidar,dtype=float)
        if imu_to_lidar is None:self.imu_to_lidar[:3,3]=[-.007698,-.014655,.00667]
        if self.imu_to_lidar.shape!=(4,4):raise ValueError('IMU-to-LiDAR extrinsics must be 4x4')
        R=self.imu_to_lidar[:3,:3]
        if not np.allclose(R.T@R,np.eye(3),atol=1e-5) or np.linalg.det(R)<.999 or not np.allclose(self.imu_to_lidar[3],[0,0,0,1]):
            raise ValueError('IMU-to-LiDAR extrinsics must be SE(3)')
        self.imu_extrinsics_source='Unitree SDK2 factory specification' if imu_to_lidar is None else 'declared override'
        if reader is None:
            try:from lab_sensors_native import L2Reader
            except ImportError as exc:raise RuntimeError('Unitree live mode requires native SDK binding; build native/ on Ubuntu. No simulator fallback.') from exc
            reader=L2Reader(lidar_ip,local_ip,lidar_port,local_port,scan_lines)
        self.reader=reader;self.timeout=float(timeout);self.transport=transport;self.clock_domain=clock_domain
        self.imu=deque(maxlen=4096);self.pending=deque(maxlen=8);self.previous_imu=None;self.closed=False;self.dropped=0;self.rejected=0;self.last_frame_end=None
        # Local SDK packets carry device time (fitted onto the host clock from IMU arrivals); remote packets were
        # already mapped onto this host's clock by the receiver, so they keep an exact fixed offset.
        self.clock_offset=None if clock_offset is None else float(clock_offset);self.host_clock=host_clock;self.warmup_dropped=0
        self.device_clock=L2DeviceClock(scale=clock_scale) if clock_offset is None else L2DeviceClock(scale=1.,offset=clock_offset)
    def reset(self):
        """Forget buffered packets so no sweep is assembled across a transport segment boundary."""
        self.imu.clear();self.pending.clear();self.previous_imu=None;self.last_frame_end=None
    def next_frame(self):
        deadline=time.monotonic()+self.timeout
        while time.monotonic()<deadline:
            packet=self.reader.poll()
            if packet is None:
                time.sleep(.001);continue
            kind=packet['type'];stamp=float(packet['timestamp'])
            if kind=='imu':self.device_clock.add(stamp,self.host_clock())  # IMU arrivals have the smallest delay
            if not self.device_clock.ready:
                self.warmup_dropped+=1;continue
            scale=self.device_clock.scale
            if kind=='imu':
                self.imu.append(IMUSample(self.device_clock.map(stamp),np.asarray(packet['gyro']),np.asarray(packet['accel'])))
            elif kind=='cloud':
                native=np.asarray(packet['points'],np.float32)
                span=float(packet.get('duration',native[:,4].max() if len(native) else 0.))*scale
                if span>self.max_sweep_seconds:self.rejected+=1
                else:
                    if len(self.pending)==self.pending.maxlen:self.dropped+=1
                    self.pending.append({**packet,'start':self.device_clock.map(stamp),'span':max(span,1e-4),'scale':scale})
            if not self.pending:continue
            cloud=self.pending[0];start=cloud['start']
            native=np.asarray(cloud['points'],np.float32);duration=cloud['span'];end=start+duration
            if not self.imu or self.imu[-1].timestamp<end:continue
            coverage_start=start if self.last_frame_end is None else min(start,self.last_frame_end)
            past=[s for s in self.imu if s.timestamp<=coverage_start]
            if not past:
                self.pending.popleft();continue
            samples=[past[-1]]+[s for s in self.imu if coverage_start<s.timestamp<=end]
            future=next((s for s in self.imu if s.timestamp>=end),None)
            if future is not None and (not samples or future.timestamp>samples[-1].timestamp):samples.append(future)
            self.pending.popleft()
            self.last_frame_end=end
            points=np.zeros((len(native),7),np.float32);points[:,:3]=native[:,:3];points[:,3]=np.clip(native[:,3]/255.,0,1)
            points[:,4:6]=1;points[:,6]=native[:,4]*cloud['scale']
            return MultiModalSensorFrame(int(cloud['frame_id']),start,points,tuple(samples),sweep_duration=duration,
                imu_to_lidar=self.imu_to_lidar,
                return_metadata_valid=False,capabilities=SensorCapabilities('unitree-l2',False,True,True,False),
                raw_timestamps={'lidar':float(cloud.get('device_timestamp',cloud['timestamp']))},diagnostics={'echo_metadata':'unavailable','transport':self.transport,'clock_domain':self.clock_domain,'dropped_clouds':self.dropped,'rejected_long_clouds':self.rejected,'device_clock':self.device_clock.status(),'imu_extrinsics_source':self.imu_extrinsics_source})
        raise TimeoutError('Unitree L2 timed out waiting for a complete cloud and bracketing IMU samples')
    def __iter__(self):return self
    def __next__(self):return self.next_frame()
    def close(self):
        if not self.closed:self.reader.close();self.closed=True

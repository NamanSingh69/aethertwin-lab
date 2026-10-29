"""Unitree SDK adapter. Echo index/count are unavailable, never fabricated."""
from collections import deque
import time
import numpy as np
from core.contracts import MultiModalSensorFrame,IMUSample,SensorCapabilities


class LidarL2Driver:
    def __init__(self,lidar_ip='192.168.1.62',local_ip='192.168.1.2',lidar_port=6101,local_port=6201,scan_lines=18,timeout=3.,imu_to_lidar=None):
        self.imu_to_lidar=np.eye(4) if imu_to_lidar is None else np.array(imu_to_lidar,dtype=float)
        if imu_to_lidar is None:self.imu_to_lidar[:3,3]=[-.007698,-.014655,.00667]
        if self.imu_to_lidar.shape!=(4,4):raise ValueError('IMU-to-LiDAR extrinsics must be 4x4')
        R=self.imu_to_lidar[:3,:3]
        if not np.allclose(R.T@R,np.eye(3),atol=1e-5) or np.linalg.det(R)<.999 or not np.allclose(self.imu_to_lidar[3],[0,0,0,1]):
            raise ValueError('IMU-to-LiDAR extrinsics must be SE(3)')
        self.imu_extrinsics_source='Unitree SDK2 factory specification' if imu_to_lidar is None else 'declared override'
        try:from lab_sensors_native import L2Reader
        except ImportError as exc:raise RuntimeError('Unitree live mode requires native SDK binding; build native/ on Ubuntu. No simulator fallback.') from exc
        self.reader=L2Reader(lidar_ip,local_ip,lidar_port,local_port,scan_lines);self.timeout=float(timeout)
        self.imu=deque(maxlen=4096);self.pending=deque(maxlen=8);self.previous_imu=None;self.closed=False;self.dropped=0;self.last_frame_end=None
        self.clock_offset=time.monotonic()-time.time()
    def next_frame(self):
        deadline=time.monotonic()+self.timeout
        while time.monotonic()<deadline:
            packet=self.reader.poll()
            if packet is None:
                time.sleep(.001);continue
            kind=packet['type']
            if kind=='imu':
                sample=IMUSample(float(packet['timestamp'])+self.clock_offset,np.asarray(packet['gyro']),np.asarray(packet['accel']))
                self.imu.append(sample)
            elif kind=='cloud':
                if len(self.pending)==self.pending.maxlen:self.dropped+=1
                self.pending.append(packet)
            if not self.pending:continue
            cloud=self.pending[0];start=float(cloud['timestamp'])+self.clock_offset
            native=np.asarray(cloud['points'],np.float32);duration=float(cloud.get('duration',native[:,4].max() if len(native) else .1))
            duration=max(duration,1e-4);end=start+duration
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
            points[:,4:6]=1;points[:,6]=native[:,4]
            return MultiModalSensorFrame(int(cloud['frame_id']),start,points,tuple(samples),sweep_duration=duration,
                imu_to_lidar=self.imu_to_lidar,
                return_metadata_valid=False,capabilities=SensorCapabilities('unitree-l2',False,True,True,False),
                raw_timestamps={'lidar':float(cloud['timestamp'])},diagnostics={'echo_metadata':'unavailable','transport':'Unitree SDK UDP','clock_domain':'SDK synchronized device epoch mapped to monotonic','dropped_clouds':self.dropped,'imu_extrinsics_source':self.imu_extrinsics_source})
        raise TimeoutError('Unitree L2 timed out waiting for a complete cloud and bracketing IMU samples')
    def __iter__(self):return self
    def __next__(self):return self.next_frame()
    def close(self):
        if not self.closed:self.reader.close();self.closed=True

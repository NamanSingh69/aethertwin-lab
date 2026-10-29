"""Slamtec SDK planar acquisition and encoder-timestamped nodding transforms."""
import time
import numpy as np
from core.se3_math import so3_exp,transform_points


class RPLidarS2Driver:
    def __init__(self,port,baudrate=1000000,extrinsics=None,pitch_source=None,max_pitch_age=.02,transport='serial',udp_port=8089):
        if transport not in ('serial','udp'):raise ValueError('S2 transport must be serial or udp')
        if not 0<int(udp_port)<65536:raise ValueError('Invalid S2 UDP port')
        try:from lab_sensors_native import S2Reader
        except ImportError as exc:raise RuntimeError('S2 live capture requires the optional official Slamtec native binding') from exc
        self.reader=S2Reader(port,int(baudrate),transport,int(udp_port));self.extrinsics=np.eye(4) if extrinsics is None else np.asarray(extrinsics,float)
        self.pitch_source=pitch_source;self.max_pitch_age=float(max_pitch_age);self.closed=False
    def read(self):
        polar=np.asarray(self.reader.read(),float);timestamp=time.monotonic();pitch=0.
        if self.pitch_source is not None:
            encoder_time,pitch=self.pitch_source()
            if abs(timestamp-encoder_time)>self.max_pitch_age:raise ValueError('Nodding encoder timestamp is stale')
        ranges,angles=polar[:,0],polar[:,1];valid=np.isfinite(ranges)&(ranges>.05)
        points=np.c_[ranges[valid]*np.cos(angles[valid]),ranges[valid]*np.sin(angles[valid]),np.zeros(valid.sum())]
        T=self.extrinsics.copy();T[:3,:3]=T[:3,:3]@so3_exp(np.array([0,pitch,0]))
        return timestamp,transform_points(T,points).astype(np.float32)
    def close(self):
        if not self.closed:self.reader.close();self.closed=True

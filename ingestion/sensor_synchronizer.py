"""Clock-aware buffered synchronization and distortion-aware PointPainting."""
from collections import deque
from dataclasses import replace
import numpy as np
import cv2
from core.contracts import IMUSample
from core.se3_math import inverse,transform_points


class ClockAligner:
    """Affine device->host time fit; exposes residuals rather than claiming HW sync."""
    def __init__(self,window=200):self.samples=deque(maxlen=window);self.scale=1.;self.offset=0.;self.residual=0.
    def update(self,device_time,host_time):
        self.samples.append((device_time,host_time))
        if len(self.samples)==1:self.offset=host_time-device_time
        elif len(self.samples)>3:
            data=np.asarray(self.samples);x=data[:,0];y=data[:,1];x0=x.mean();y0=y.mean()
            slope=np.dot(x-x0,y-y0)/max(np.dot(x-x0,x-x0),1e-12)
            if not .99<slope<1.01:raise ValueError("Device clock reset or implausible drift")
            self.scale=slope;self.offset=y0-slope*x0;self.residual=float(np.std(y-(slope*x+self.offset)))
        return self.scale*device_time+self.offset


def project_points(points,calibration,camera_pose_in_lidar=None):
    if not len(points):return np.empty((0,2)),np.empty(0)
    pose=calibration.T_lidar_camera if camera_pose_in_lidar is None else camera_pose_in_lidar
    xyz=transform_points(inverse(pose),np.asarray(points)[:,:3])
    uv,_=cv2.projectPoints(xyz,np.zeros(3),np.zeros(3),calibration.K,calibration.distortion)
    return uv.reshape(-1,2),xyz[:,2]


def backproject_depth(depth,calibration,stride=1,rgb=None,min_depth=.2,max_depth=6.,camera_pose_in_lidar=None):
    depth=np.asarray(depth);v,u=np.indices(depth.shape);mask=(depth>=min_depth)&(depth<=max_depth)&np.isfinite(depth)
    mask[~((v%stride==0)&(u%stride==0))]=False
    pix=np.c_[u[mask],v[mask]].astype(np.float64).reshape(-1,1,2)
    xy=cv2.undistortPoints(pix,calibration.K,calibration.distortion).reshape(-1,2) if len(pix) else np.empty((0,2))
    pose=calibration.T_lidar_camera if camera_pose_in_lidar is None else camera_pose_in_lidar
    z=depth[mask];camera=np.c_[xy*z[:,None],z];lidar=transform_points(pose,camera)
    if rgb is not None:return np.c_[lidar,np.asarray(rgb)[mask]/255.].astype(np.float32)
    return lidar.astype(np.float32)


def _bilinear(image,uv):
    h,w=image.shape[:2];u=np.clip(uv[:,0],0,w-1);v=np.clip(uv[:,1],0,h-1)
    u0=np.floor(u).astype(int);v0=np.floor(v).astype(int);u1=np.minimum(u0+1,w-1);v1=np.minimum(v0+1,h-1)
    a=(u-u0)[:,None];b=(v-v0)[:,None];data=np.asarray(image)
    if data.ndim==2:data=data[...,None]
    return (1-a)*(1-b)*data[v0,u0]+a*(1-b)*data[v0,u1]+(1-a)*b*data[v1,u0]+a*b*data[v1,u1]


def paint_points(points,rgb,depth,calibration,semantic_features=None,occlusion_tolerance=.08,camera_pose_in_lidar=None):
    uv,z=project_points(points,calibration,camera_pose_in_lidar);h,w=depth.shape
    valid=(z>=.2)&(uv[:,0]>=0)&(uv[:,0]<=w-1)&(uv[:,1]>=0)&(uv[:,1]<=h-1)
    safe_uv=np.nan_to_num(uv,nan=-1,posinf=-1,neginf=-1)
    measured=_bilinear(depth,safe_uv)[:,0]
    valid&=(measured>0)&(np.abs(measured-z)<occlusion_tolerance)
    data=_bilinear(np.asarray(rgb)/255.,safe_uv)
    if semantic_features is not None:
        features=np.asarray(semantic_features)
        if features.shape[:2]!=(h,w):features=cv2.resize(features,(w,h),interpolation=cv2.INTER_LINEAR)
        if features.ndim==2:features=features[...,None]
        data=np.c_[data,_bilinear(features,safe_uv)]
    data[~valid]=0
    return data.astype(np.float32),valid


def fuse_frame(frame,semantic_features=None,stride=3,occlusion_tolerance=.08):
    if frame.rgb is None or frame.depth is None or frame.calibration is None:
        return replace(frame,diagnostics={**frame.diagnostics,"painting":"camera_unavailable"})
    painted,valid=paint_points(frame.lidar_points,frame.rgb,frame.depth,frame.calibration,semantic_features,occlusion_tolerance,frame.camera_pose_in_lidar)
    dense=backproject_depth(frame.depth,frame.calibration,stride,frame.rgb,camera_pose_in_lidar=frame.camera_pose_in_lidar)
    return replace(frame,painted_features=painted,gemini_rgbd_cloud=dense,
                   diagnostics={**frame.diagnostics,"painted_fraction":float(valid.mean()) if len(valid) else 0.})


class SensorSynchronizer:
    def __init__(self,tolerance=.02,capacity=256,camera_max_bytes=64*1024*1024,camera_history_seconds=.75,imu_capacity=None):
        if capacity<1 or camera_max_bytes<1 or camera_history_seconds<=0 or (imu_capacity is not None and imu_capacity<2):
            raise ValueError('Invalid synchronization buffer budget')
        self.tolerance=float(tolerance);self.lidar=deque(maxlen=capacity);self.camera=deque(maxlen=capacity);self.imu=deque(maxlen=imu_capacity or capacity*4)
        self.camera_max_bytes=int(camera_max_bytes);self.camera_history_seconds=float(camera_history_seconds);self.imu_dropped=0
        self.dropped=0;self.missing=0
        self.last_output_end=None
    def push_lidar(self,frame):
        if len(self.lidar)==self.lidar.maxlen:self.dropped+=1
        self.lidar.append(frame)
    def push_camera(self,timestamp,rgb,depth,calibration,metadata=None):
        size=np.asarray(rgb).nbytes+np.asarray(depth).nbytes
        if size>self.camera_max_bytes:raise MemoryError('Camera frame exceeds synchronization byte budget')
        def used():return sum(np.asarray(c[1]).nbytes+np.asarray(c[2]).nbytes for c in self.camera)
        while self.camera and (len(self.camera)>=self.camera.maxlen or used()+size>self.camera_max_bytes or timestamp-self.camera[0][0]>self.camera_history_seconds):
            self.camera.popleft();self.dropped+=1
        self.camera.append((float(timestamp),rgb,depth,calibration,dict(metadata or {})))
    def push_imu(self,sample):
        if len(self.imu)==self.imu.maxlen:self.imu_dropped+=1
        self.imu.append(sample)
    def synchronize(self):
        if not self.lidar or not self.camera:return None
        frame=self.lidar[0];end=frame.timestamp+frame.sweep_duration
        # Merge native frame IMUs with subsequent buffered packets so a camera
        # captured just after sweep-end can wait for measured IMU coverage.
        coverage_start=frame.timestamp if self.last_output_end is None else min(frame.timestamp,self.last_output_end)
        samples={s.timestamp:s for s in self.imu if coverage_start<=s.timestamp<=end+.2}
        past=[s for s in self.imu if s.timestamp<coverage_start]
        if past:samples[past[-1].timestamp]=past[-1]
        samples.update({s.timestamp:s for s in frame.imu_samples})
        imu=tuple(samples[t] for t in sorted(samples))
        if len(imu)<2 or imu[0].timestamp>coverage_start+1e-6 or imu[-1].timestamp<end-1e-6:
            self.missing+=1;return None
        covered=[c for c in self.camera if frame.timestamp-1e-6<=c[0]<=imu[-1].timestamp+1e-6]
        if not covered:self.missing+=1;return None
        candidate=min(covered,key=lambda c:abs(c[0]-end));error=abs(candidate[0]-end)
        if error>self.tolerance:
            if self.camera[-1][0]>end+self.tolerance:self.lidar.popleft();self.dropped+=1
            self.missing+=1;return None
        self.lidar.popleft()
        self.last_output_end=end
        while len(self.camera)>1 and self.camera[1][0]<=candidate[0]:self.camera.popleft()
        while len(self.imu)>1 and self.imu[1].timestamp<end:self.imu.popleft()
        meta=candidate[4]
        camera_diagnostics={key:meta[key] for key in ('camera_profiles','camera_timestamps','camera_available','camera_calibration_validation') if key in meta}
        raw={**frame.raw_timestamps,'camera':meta.get('raw_timestamp',candidate[0])}
        for key in ('camera_device_us','camera_depth_device_us'):
            if key in meta:raw[key]=meta[key]
        return replace(frame,imu_samples=imu,rgb=candidate[1],depth=candidate[2],calibration=candidate[3],camera_timestamp=candidate[0],raw_timestamps=raw,
                       diagnostics={**frame.diagnostics,**camera_diagnostics,"sync_error_seconds":error,"dropped_frames":self.dropped,"missing_coverage":self.missing,
                                    'camera_clock_fit_residual_seconds':meta.get('clock_fit_residual_seconds'),
                                    'synchronization_buffers':{'camera_frames':len(self.camera),'camera_bytes':sum(np.asarray(c[1]).nbytes+np.asarray(c[2]).nbytes for c in self.camera),
                                        'camera_capacity_bytes':self.camera_max_bytes,'camera_capacity_frames':self.camera.maxlen,
                                        'lidar_frames':len(self.lidar),'imu_samples':len(self.imu),'imu_capacity':self.imu.maxlen,'imu_dropped':self.imu_dropped},
                                    'camera_color_depth_delta_seconds':meta.get('color_depth_delta_seconds'),'camera_color_format':meta.get('color_format')})

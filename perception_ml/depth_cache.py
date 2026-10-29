"""Bounded calibrated pixel-ray cache; captures/extrinsics are never cached."""
from collections import OrderedDict
import threading
import cv2
import numpy as np
from core.se3_math import transform_points


class DepthRayCache:
    def __init__(self,capacity=8):
        if capacity<1:raise ValueError('Depth cache capacity must be positive')
        self.capacity=int(capacity);self._entries=OrderedDict();self._lock=threading.Lock();self.hits=0;self.misses=0
    def rays(self,shape,calibration,stride):
        if isinstance(stride,bool) or not isinstance(stride,(int,np.integer)) or stride<1:raise ValueError('Depth stride must be a positive integer')
        key=(tuple(shape),int(stride),calibration.calibration_id,calibration.K.tobytes(),calibration.distortion.tobytes())
        with self._lock:
            if key in self._entries:
                self.hits+=1;self._entries.move_to_end(key);return self._entries[key]
            self.misses+=1
            v,u=np.meshgrid(np.arange(0,shape[0],stride),np.arange(0,shape[1],stride),indexing='ij')
            pixels=np.column_stack([u.ravel(),v.ravel()]).astype(np.float64)
            xy=cv2.undistortPoints(pixels.reshape(-1,1,2),calibration.K,calibration.distortion).reshape(-1,2)
            result=(v.ravel(),u.ravel(),xy)
            for value in result:value.setflags(write=False)
            self._entries[key]=result
            while len(self._entries)>self.capacity:self._entries.popitem(last=False)
            return result
    def backproject(self,depth,calibration,stride=3,rgb=None,camera_pose_in_lidar=None,min_depth=.2,max_depth=6.):
        depth=np.asarray(depth);v,u,xy=self.rays(depth.shape,calibration,stride);z=depth[v,u]
        valid=np.isfinite(z)&(z>=min_depth)&(z<=max_depth)
        z=z[valid];camera=np.column_stack([xy[valid]*z[:,None],z])
        pose=calibration.T_lidar_camera if camera_pose_in_lidar is None else camera_pose_in_lidar
        lidar=transform_points(pose,camera)
        if rgb is not None:lidar=np.column_stack([lidar,np.asarray(rgb)[v[valid],u[valid]]/255.])
        return lidar.astype(np.float32)


DEPTH_RAY_CACHE=DepthRayCache()

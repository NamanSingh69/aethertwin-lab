"""Carried-state IMU integration and per-acquisition motion compensation."""
from collections import deque
from dataclasses import replace
import numpy as np
from core.se3_math import so3_exp,so3_exp_batch,so3_log,inverse,transform_points


def _rigid_pose(value):
    pose=np.array(value,dtype=np.float64,copy=True)
    if pose.shape!=(4,4) or not np.isfinite(pose).all() or not np.allclose(pose[3],[0,0,0,1]):
        raise ValueError('IMU localization state must be a finite rigid transform')
    rotation=pose[:3,:3]
    if np.linalg.det(rotation)<=0 or np.linalg.norm(rotation.T@rotation-np.eye(3))>.01:
        raise ValueError('IMU localization rotation is not close to SO(3)')
    u,_,vt=np.linalg.svd(rotation);pose[:3,:3]=u@vt
    return pose


class ContinuousTimeIMUDeskewer:
    def __init__(self,initial_pose=None,initial_velocity=None,gyro_bias=None,accel_bias=None,imu_to_lidar=None):
        self.pose_world=np.eye(4) if initial_pose is None else _rigid_pose(initial_pose)
        self.velocity=np.zeros(3) if initial_velocity is None else np.array(initial_velocity,dtype=float)
        self.gyro_bias=np.zeros(3) if gyro_bias is None else np.asarray(gyro_bias,float)
        self.accel_bias=np.zeros(3) if accel_bias is None else np.asarray(accel_bias,float)
        self.imu_to_lidar=np.eye(4) if imu_to_lidar is None else np.asarray(imu_to_lidar,float)
        self.last_timestamp=None;self.last_diagnostics={};self._trajectories=deque(maxlen=4)
        self.last_point_origins_world=np.empty((0,3));self.last_point_origins_lidar=np.empty((0,3))

    def correct_state(self,pose_world,velocity=None):
        pose_world=_rigid_pose(pose_world)
        # Localization re-anchors bounded recent trajectories as well as the end
        # pose, so auxiliary captures can use preceding observed IMU intervals.
        correction=pose_world@inverse(self.pose_world)
        for trajectory in self._trajectories:
            trajectory['R']=np.einsum('ij,njk->nik',correction[:3,:3],trajectory['R'])
            trajectory['p']=transform_points(correction,trajectory['p'])
            for key in ('v','a'):trajectory[key]=trajectory[key]@correction[:3,:3].T
        self.pose_world=pose_world
        self.velocity=self.velocity@correction[:3,:3].T if velocity is None else np.asarray(velocity,float).copy()
        self.last_point_origins_world=transform_points(correction,self.last_point_origins_world)

    @staticmethod
    def _sample(trajectory,query_times):
        times=trajectory['times'];qt=np.asarray(query_times,float).reshape(-1)
        if np.any(qt<times[0]-1e-6) or np.any(qt>times[-1]+1e-6):
            raise ValueError('Sensor capture timestamp lies outside observed IMU coverage')
        qt=np.clip(qt,times[0],times[-1]);idx=np.clip(np.searchsorted(times,qt,side='right')-1,0,len(times)-2)
        delta=qt-times[idx];fraction=delta/(times[idx+1]-times[idx])
        rotvec=trajectory['rotvec'];R=trajectory['R'];p=trajectory['p'];v=trajectory['v'];a=trajectory['a']
        rotations=R[idx]@so3_exp_batch(fraction[:,None]*rotvec[idx])
        positions=p[idx]+v[idx]*delta[:,None]+.5*a[idx]*delta[:,None]**2
        velocities=v[idx]+a[idx]*delta[:,None]
        return rotations,positions,velocities

    def pose_at(self,timestamp):
        """IMU-interpolated world pose; never extrapolates beyond covered data."""
        for trajectory in reversed(self._trajectories):
            if trajectory['times'][0]-1e-6<=timestamp<=trajectory['times'][-1]+1e-6:
                R,p,_=self._sample(trajectory,[timestamp]);pose=np.eye(4);pose[:3,:3]=R[0];pose[:3,3]=p[0];return pose
        raise ValueError('Sensor capture timestamp lies outside retained IMU trajectory coverage')

    def deskew_frame(self,frame):
        if frame.diagnostics.get('coordinate_reference')=='lidar_sweep_end':
            raise ValueError('Frame has already been deskewed; runtime replay requires raw acquisition frames')
        end=frame.timestamp+frame.sweep_duration
        camera_time=end if frame.camera_timestamp is None else frame.camera_timestamp
        auxiliary_times=[]
        if frame.calibration is not None:auxiliary_times.append(camera_time)
        if frame.rplidar_s2_points is not None and frame.rplidar_timestamp is not None:auxiliary_times.append(frame.rplidar_timestamp)
        points=self.deskew_sweep(frame.lidar_points,frame.imu_samples,frame.timestamp,frame.sweep_duration,
                                 origins=frame.lidar_origins,query_timestamps=auxiliary_times)
        fields={'lidar_points':points,'lidar_origins':self.last_point_origins_lidar,'sensor_pose_world':self.pose_world,
                'diagnostics':{**frame.diagnostics,**self.last_diagnostics}}
        reference_inverse=inverse(self.pose_world)
        if frame.calibration is not None:
            effective=reference_inverse@self.pose_at(camera_time)@frame.calibration.T_lidar_camera
            fields['camera_pose_in_lidar']=effective
            fields['diagnostics']['camera_pose_time_offset_seconds']=float(camera_time-end)
            if frame.camera_timestamp is None:fields['diagnostics']['camera_time_assumption']='missing_timestamp_assumed_sweep_end'
            if frame.gemini_rgbd_cloud is not None:
                old_pose=frame.camera_pose_in_lidar if frame.camera_pose_in_lidar is not None else frame.calibration.T_lidar_camera
                cloud=frame.gemini_rgbd_cloud.copy();cloud[:,:3]=transform_points(effective@inverse(old_pose),cloud[:,:3])
                fields['gemini_rgbd_cloud']=cloud
            # Raw painting belongs to a different reference frame. Repaint with
            # capture-time projection rather than retaining stale correspondences.
            fields['painted_features']=None
        if frame.rplidar_s2_points is not None:
            capture=end if frame.rplidar_timestamp is None else frame.rplidar_timestamp
            motion=reference_inverse@self.pose_at(capture)
            cloud=frame.rplidar_s2_points.copy();cloud[:,:3]=transform_points(motion,cloud[:,:3])
            fields['rplidar_s2_points']=cloud
            fields['rplidar_origin_lidar']=transform_points(motion,frame.rplidar_origin_lidar[None])[0]
            fields['diagnostics']['rplidar_pose_time_offset_seconds']=float(capture-end)
            if frame.rplidar_timestamp is None:fields['diagnostics']['rplidar_time_assumption']='missing_timestamp_assumed_sweep_end'
        return replace(frame,**fields)

    def deskew_sweep(self,points,imu_samples,sweep_start=None,sweep_duration=None,origins=None,query_timestamps=()):
        points=np.asarray(points);samples=sorted(imu_samples,key=lambda s:s.timestamp)
        if len(samples)<2:raise ValueError('Deskew requires IMU coverage, not a silent identity fallback')
        raw_t=np.array([s.timestamp for s in samples],float)
        if np.any(np.diff(raw_t)<=0):raise ValueError('IMU timestamps must be strictly increasing')
        t0=float(raw_t[0] if sweep_start is None else sweep_start)
        duration=float((raw_t[-1]-t0) if sweep_duration is None else sweep_duration);t1=t0+duration
        if duration<=0 or not np.isfinite([t0,duration]).all():raise ValueError('Invalid LiDAR sweep interval')
        if raw_t[0]>t0+1e-6 or raw_t[-1]<t1-1e-6:raise ValueError('IMU does not cover the complete LiDAR sweep')
        if len(points) and (points[:,6].min()<-1e-6 or points[:,6].max()>duration+1e-6):raise ValueError('Point timestamp lies outside sweep')
        origins=np.zeros((len(points),3)) if origins is None else np.asarray(origins,float)
        if origins.shape!=(len(points),3) or not np.isfinite(origins).all():raise ValueError('Point origins must be finite and align with LiDAR returns')
        integration_start=t0
        if self.last_timestamp is not None:
            gap=t0-self.last_timestamp
            if gap< -1e-6:raise ValueError('Overlapping sweeps require trajectory history; cannot integrate a future state twice')
            if gap>1e-6:
                if raw_t[0]>self.last_timestamp+1e-6:raise ValueError('IMU does not cover the interval between LiDAR sweeps')
                integration_start=self.last_timestamp
        if not np.isfinite(query_timestamps).all():raise ValueError('Auxiliary capture timestamps must be finite')
        for capture in query_timestamps:
            if capture<integration_start-1e-6:self.pose_at(capture)
        integration_end=max([t1,*query_timestamps])
        if not np.isfinite(integration_end) or integration_end>raw_t[-1]+1e-6:
            raise ValueError('Auxiliary sensor timestamp lies outside observed IMU coverage; cannot extrapolate')
        times=np.unique(np.r_[integration_start,raw_t[(raw_t>integration_start+1e-9)&(raw_t<integration_end-1e-9)],t1,integration_end])
        gyro=np.stack([s.gyro for s in samples]);acc=np.stack([s.accel for s in samples])
        gyro=np.column_stack([np.interp(times,raw_t,gyro[:,i]) for i in range(3)])-self.gyro_bias
        acc=np.column_stack([np.interp(times,raw_t,acc[:,i]) for i in range(3)])-self.accel_bias
        Ril=self.imu_to_lidar[:3,:3];lever=self.imu_to_lidar[:3,3]
        gyro=gyro@Ril.T;acc=acc@Ril.T
        alpha=np.gradient(gyro,times,axis=0)
        acc-=np.cross(alpha,lever)+np.cross(gyro,np.cross(gyro,lever))
        rotations=[self.pose_world[:3,:3].copy()];positions=[self.pose_world[:3,3].copy()];velocities=[self.velocity.copy()];world_acc=[]
        for k,dt in enumerate(np.diff(times)):
            w=.5*(gyro[k]+gyro[k+1]);specific=.5*(acc[k]+acc[k+1])
            Rmid=rotations[-1]@so3_exp(w*dt/2)
            a=Rmid@specific+np.array([0,0,-9.80665]);world_acc.append(a)
            positions.append(positions[-1]+velocities[-1]*dt+.5*a*dt*dt)
            velocities.append(velocities[-1]+a*dt)
            rotations.append(rotations[-1]@so3_exp(w*dt))
        R=np.array(rotations);p=np.array(positions);v=np.array(velocities);a=np.array(world_acc)
        trajectory={'times':times,'R':R,'p':p,'v':v,'a':a,'rotvec':np.array([so3_log(R[j].T@R[j+1]) for j in range(len(R)-1)])}
        qt=np.clip(t0+points[:,6].astype(float),t0,t1)
        interp_R,interp_p,_=self._sample(trajectory,qt)
        end_R,end_p,end_v=self._sample(trajectory,[t1]);end_R=end_R[0];end_p=end_p[0]
        world=np.einsum('nij,nj->ni',interp_R,points[:,:3])+interp_p
        world_origins=np.einsum('nij,nj->ni',interp_R,origins)+interp_p
        result=points.copy();result[:,:3]=(world-end_p)@end_R
        self.last_point_origins_world=world_origins
        self.last_point_origins_lidar=(world_origins-end_p)@end_R
        self.pose_world=np.eye(4);self.pose_world[:3,:3]=end_R;self.pose_world[:3,3]=end_p
        self.velocity=end_v[0];self.last_timestamp=t1;self._trajectories.append(trajectory)
        self.last_diagnostics={'imu_samples':len(samples),'coverage_seconds':float(raw_t[-1]-raw_t[0]),'pointwise':True,
                               'inter_sweep_integration_seconds':t0-integration_start,'per_return_origins':True,
                               'coordinate_reference':'lidar_sweep_end'}
        return result

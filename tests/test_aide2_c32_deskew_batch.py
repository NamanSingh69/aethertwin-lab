"""AIDE2 C32: batch the per-step SO(3) exp/log of the deskew integration (13-15 ms of a parked frame in process; the
read-only review measured the batched forms bit-identical). Oracle: a frozen copy of the pre-C32 deskew_sweep."""
from pathlib import Path
import numpy as np
import pytest
from core.contracts import IMUSample
from core.se3_math import so3_exp,so3_exp_batch,so3_log,inverse,transform_points
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer

ROOT=Path(__file__).resolve().parents[1]
RECORDING=Path.home()/'aethertwin_rec'/'wifi_stationary_60s'


class OracleDeskewer(ContinuousTimeIMUDeskewer):
    """deskew_sweep exactly as before C32 (copied verbatim)."""
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
        integration_start=t0;overlap=0.
        if self.last_timestamp is not None:
            gap=t0-self.last_timestamp
            if gap< -1e-6-self.overlap_tolerance:raise ValueError('Overlapping sweeps require trajectory history; cannot integrate a future state twice')
            if gap< -1e-6:
                # Jitter-sized overlap: integrate from the previous end; returns stamped inside the overlap take
                # the pose at that instant (at most overlap_tolerance of motion, recorded in the diagnostics).
                overlap=-gap;integration_start=self.last_timestamp
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
        qt=np.clip(t0+points[:,6].astype(float),integration_start,t1)
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
                               'inter_sweep_integration_seconds':max(0.,t0-integration_start),'sweep_overlap_seconds':overlap,'per_return_origins':True,
                               'coordinate_reference':'lidar_sweep_end'}
        return result


def same_state(a,b):
    assert np.array_equal(a.pose_world,b.pose_world) and np.array_equal(a.velocity,b.velocity) and a.last_timestamp==b.last_timestamp
    assert np.array_equal(a.last_point_origins_world,b.last_point_origins_world)
    assert np.array_equal(a.last_point_origins_lidar,b.last_point_origins_lidar)
    assert len(a._trajectories)==len(b._trajectories)
    for ta,tb in zip(a._trajectories,b._trajectories):
        assert ta.keys()==tb.keys()
        for key in ta:assert np.array_equal(ta[key],tb[key]),key


def synthetic_sweep(rng,t0,duration,rate=250.,points=400):
    times=np.arange(t0-.7,t0+duration+.05,1/rate)
    samples=[IMUSample(float(t),rng.normal(0,.6,3),np.array([0,0,9.80665])+rng.normal(0,.4,3)) for t in times]
    cloud=np.zeros((points,7));cloud[:,:3]=rng.uniform(-5,5,(points,3));cloud[:,6]=np.sort(rng.uniform(0,duration,points))
    return cloud,samples


def test_batched_deskew_is_bit_identical_over_gaps_overlaps_and_abutting_sweeps():
    rng=np.random.default_rng(32);new=ContinuousTimeIMUDeskewer(initial_velocity=[.3,0,0],accel_bias=[0,0,.7]);old=OracleDeskewer(initial_velocity=[.3,0,0],accel_bias=[0,0,.7])
    t=0.
    for gap in (0.,.25,-.0005,0.,.0021,-.0019,.6):
        t+=gap;cloud,samples=synthetic_sweep(rng,t,.084)
        a=new.deskew_sweep(cloud,samples,t,.084,query_timestamps=(t+.06,));b=old.deskew_sweep(cloud,samples,t,.084,query_timestamps=(t+.06,))
        assert np.array_equal(a,b);same_state(new,old);t+=.084


@pytest.mark.skipif(not RECORDING.exists(),reason='real bench recording not on this machine')
def test_batched_deskew_is_bit_identical_on_real_l2_frames():
    from ingestion.recorder_replayer import ReplaySource
    source=ReplaySource(RECORDING);new=ContinuousTimeIMUDeskewer();old=OracleDeskewer()
    try:
        for k,frame in zip(range(120),iter(source)):
            a=new.deskew_frame(frame);b=old.deskew_frame(frame)
            assert np.array_equal(a.lidar_points,b.lidar_points),k;same_state(new,old)
    finally:source.close()

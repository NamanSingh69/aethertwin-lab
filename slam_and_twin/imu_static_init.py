"""Accelerometer bias along gravity from the rig's first still seconds (live.imu_accel_bias: auto).

The L2's IMU read |a| = 10.52 m/s^2 at rest against g = 9.807 (reports/hardware/pi/imu_bias_still_60s.json). From
one orientation only the component along gravity is observable (tilt absorbs the rest), and that is the component
that matters for deskewing: subtracting it cut the end-of-scan IMU drift on the real 600 s recording from 2.9 to
0.4 mm per sweep and from 41.8 to 2.0 mm per four merged sweeps, the same as the bench-measured vector.
"""
import itertools
import numpy as np

GRAVITY=9.80665


def gravity_bias_from_still(samples,min_seconds=1.,gyro_max=.03,accel_std_max=.2):
    """(bias, report): bias is None unless the samples cover min_seconds of the rig at rest."""
    samples=sorted(samples,key=lambda s:s.timestamp)
    if len(samples)<10:return None,{'applied':False,'reason':'fewer than 10 IMU samples'}
    t=np.array([s.timestamp for s in samples]);acc=np.array([s.accel for s in samples],float);gyro=np.array([s.gyro for s in samples],float)
    span=float(t[-1]-t[0]);gyro_peak=float(np.linalg.norm(gyro,axis=1).max());accel_std=float(np.linalg.norm(acc.std(0)))
    mean=acc.mean(0);norm=float(np.linalg.norm(mean))
    report={'seconds':span,'samples':len(samples),'gyro_peak_rad_s':gyro_peak,'accel_std_m_s2':accel_std,'accel_norm_m_s2':norm,'gravity_m_s2':GRAVITY}
    if span<min_seconds:return None,{**report,'applied':False,'reason':f'only {span:.2f} s of IMU before motion (need {min_seconds} s still)'}
    if gyro_peak>gyro_max or accel_std>accel_std_max:return None,{**report,'applied':False,'reason':'rig was not still at the start'}
    if not 5.<norm<15.:return None,{**report,'applied':False,'reason':'accelerometer magnitude is not near gravity'}
    bias=mean/norm*(norm-GRAVITY)
    if np.linalg.norm(bias)>3.:return None,{**report,'applied':False,'reason':'estimated bias exceeds 3 m/s^2'}
    return bias,{**report,'applied':True,'accel_bias_m_s2':bias.tolist(),'component':'along gravity only (one orientation)','gravity_up_imu':(mean/norm).tolist()}


def resolve_auto_bias(frames,seconds=2.,min_seconds=1.,gyro_max=.03):
    """Peek the leading still frames of a stream. Returns (bias or None, report, the same frames in order).

    Frames are read until `seconds` of IMU are seen or the rig starts to move; the still prefix gives the estimate.
    """
    iterator=iter(frames);peeked=[];still=[];first=None;moving=False
    for frame in iterator:
        peeked.append(frame)
        for s in frame.imu_samples:
            if first is None:first=s.timestamp
            if np.linalg.norm(s.gyro)>gyro_max:moving=True;break
            still.append(s)
        if moving or (first is not None and still and still[-1].timestamp-first>=seconds):break
    unique={float(s.timestamp):s for s in still}
    bias,report=gravity_bias_from_still(list(unique.values()),min_seconds=min_seconds,gyro_max=gyro_max)
    if 'gravity_up_imu' in report and peeked:
        # Up in the build's world frame (the first sweep's LiDAR frame at its prior pose): floor fit fallback normal.
        first=peeked[0];up=first.sensor_pose_world[:3,:3]@first.imu_to_lidar[:3,:3]@np.asarray(report['gravity_up_imu'])
        report['gravity_up_world']=(up/np.linalg.norm(up)).tolist();report['tilt_from_world_z_deg']=float(np.degrees(np.arccos(np.clip(up[2]/np.linalg.norm(up),-1,1))))
    return bias,{**report,'source':'live.imu_accel_bias: auto','frames_inspected':len(peeked)},itertools.chain(peeked,iterator)

import numpy as np
import pytest
from core.contracts import IMUSample,MultiModalSensorFrame
from ingestion.sweep_merge import merge_sweeps,merge_frames
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer


def sweeps(count,start=0.,duration=.08,gaps=None,velocity=1.):
    """Raw sweeps of a wall at x=3 seen from a rig moving along +x; each frame carries IMU to its end."""
    frames=[];t=start;gaps=[0.]*count if gaps is None else gaps
    for i in range(count):
        offsets=np.linspace(0,duration,40);y=np.linspace(-1,1,40)
        world=np.c_[np.full(40,3.),y,np.full(40,.5)];position=velocity*(t+offsets)[:,None]*[1,0,0]
        points=np.c_[world-position,np.ones((40,3)),offsets]
        imu=[IMUSample(s,[0,0,0],[0,0,9.80665]) for s in np.linspace(t-.004,t+duration+.004,12)]
        frames.append(MultiModalSensorFrame(i,t,points,imu_samples=imu,sweep_duration=duration,diagnostics={'sweep':i}))
        t+=duration+gaps[i]
    return frames


def test_merging_keeps_every_return_with_its_acquisition_time_and_deskews_identically():
    raw=sweeps(4,gaps=[-.0007,.0003,.0002,0])
    merged=merge_frames(raw)
    assert len(merged.lidar_points)==160 and merged.timestamp==0 and merged.frame_id==0
    assert merged.sweep_duration==pytest.approx(raw[-1].timestamp+.08)
    np.testing.assert_allclose(merged.lidar_points[40:80,6],raw[1].lidar_points[:,6]+raw[1].timestamp,atol=1e-6)
    times=[s.timestamp for s in merged.imu_samples];assert np.all(np.diff(times)>0)
    assert merged.diagnostics['merged_sweeps']==4 and list(merged.diagnostics['merged_frame_ids'])==[0,1,2,3]
    one=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0]);[one.deskew_frame(f) for f in raw]
    four=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0]);out=four.deskew_frame(merged)
    np.testing.assert_allclose(four.pose_world,one.pose_world,atol=1e-9)
    # Every return lands on the wall in the merged scan's end frame: x = 3 - end position.
    np.testing.assert_allclose(out.lidar_points[:,0],3-four.pose_world[0,3],atol=1e-6)


def test_groups_split_at_holes_and_station_changes_and_k1_is_identity():
    raw=sweeps(7,gaps=[0,0,.2,0,0,0,0])
    assert list(merge_sweeps(iter(raw),1))==raw
    sizes=[f.diagnostics.get('merged_sweeps',1) for f in merge_sweeps(iter(raw),4)]
    assert sizes==[3,4]
    station=sweeps(4);station[2]=MultiModalSensorFrame(2,station[2].timestamp,station[2].lidar_points,imu_samples=station[2].imu_samples,
        sweep_duration=.08,diagnostics={'commissioning_stationary':True})
    assert [f.diagnostics.get('merged_sweeps',1) for f in merge_sweeps(iter(station),4)]==[2,1,1]
    with pytest.raises(ValueError):list(merge_sweeps(iter(raw),0))

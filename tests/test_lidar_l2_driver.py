"""LidarL2Driver against packets shaped like the native binding's, including the stale first cloud seen on the rig."""
import numpy as np
import pytest
from ingestion.lidar_l2_driver import LidarL2Driver


class Reader:
    def __init__(self,packets):self.packets=list(packets);self.closed=False
    def poll(self):return self.packets.pop(0) if self.packets else None
    def close(self):self.closed=True


def cloud(frame_id,start,duration,n=50):
    points=np.zeros((n,6),np.float32);points[:,0]=np.linspace(1,5,n);points[:,3]=100;points[:,4]=np.linspace(0,duration,n)
    return {'type':'cloud','frame_id':frame_id,'timestamp':start,'duration':duration,'points':points}


def imu(t):return {'type':'imu','timestamp':t,'gyro':np.zeros(3,np.float32),'accel':np.array([0,0,9.8],np.float32)}


def test_stale_first_cloud_is_rejected_and_counted():
    # A fresh SDK reader first returns a leftover cloud spanning the 16 s since the previous reader stopped.
    packets=[imu(84.),cloud(1,84.,16.)]+[imu(100.+k*.002) for k in range(60)]+[cloud(2,100.01,.042),cloud(3,100.052,.042)]+[imu(100.12+k*.002) for k in range(10)]
    driver=LidarL2Driver(reader=Reader(packets),clock_offset=0.,timeout=.2)
    first,second=driver.next_frame(),driver.next_frame()
    assert (first.frame_id,second.frame_id)==(2,3) and first.sweep_duration==pytest.approx(.042)
    assert driver.rejected==1 and second.diagnostics['rejected_long_clouds']==1 and second.diagnostics['dropped_clouds']==0
    assert first.imu_samples[0].timestamp<=100.01 and first.imu_samples[-1].timestamp>=100.052


def test_sweep_limit_is_configurable_and_must_be_positive():
    driver=LidarL2Driver(reader=Reader([imu(0.),cloud(1,0.,.3)]+[imu(k*.01) for k in range(1,40)]),clock_offset=0.,timeout=.2,max_sweep_seconds=.5)
    assert driver.next_frame().frame_id==1 and driver.rejected==0
    with pytest.raises(ValueError):LidarL2Driver(reader=Reader([]),clock_offset=0.,max_sweep_seconds=0)


def test_device_clock_fit_recovers_the_half_rate_l2_clock():
    # Rig measurement: device stamps advance at 0.503x the Pi clock. Arrivals carry a small non-negative delay.
    from ingestion.lidar_l2_driver import L2DeviceClock
    rng=np.random.default_rng(1);clock=L2DeviceClock()
    clock.add(-16.,0.)  # a stale first packet from the previous session
    for k in range(1,2500):
        host=k*.004;clock.add(1000.+.503*host,host+abs(rng.exponential(.0005)))
    assert clock.restarts==1 and clock.scale==pytest.approx(1/.503,abs=1e-3)  # host seconds per device second
    assert abs(clock.map(1000.+.503*9.)-9.)<.001


def test_local_driver_puts_sweeps_and_points_on_the_host_time_scale():
    # Device time at half rate: sweeps 83 ms apart on the host clock claim 42 ms each in device time.
    now=[0.]
    class HalfRateReader(Reader):
        def poll(self):
            if not self.packets:return None
            packet=self.packets.pop(0);now[0]=packet.pop('host');return packet
    packets=[]
    for k in range(600):  # 2.4 s of IMU at 250 Hz host time
        h=k*.004;packets.append({**imu(500.+.5*h),'host':h})
    for k,h in enumerate((1.4,1.483,1.566)):
        packets.insert(int(h/.004)+1,{**cloud(k+1,500.+.5*(h-.083),.0415),'host':h})
    driver=LidarL2Driver(reader=HalfRateReader(packets),timeout=.5,host_clock=lambda:now[0])
    frame=driver.next_frame()
    assert driver.device_clock.scale==pytest.approx(2.,abs=2e-3) and driver.warmup_dropped>0
    assert frame.sweep_duration==pytest.approx(.083,abs=.002) and frame.lidar_points[:,6].max()==pytest.approx(.083,abs=.002)
    assert frame.diagnostics['device_clock']['fitted'] and np.all(np.diff([s.timestamp for s in frame.imu_samples])>.003)

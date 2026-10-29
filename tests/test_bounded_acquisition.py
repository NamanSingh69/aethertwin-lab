import queue
import time
import numpy as np
import pytest
from core.contracts import MultiModalSensorFrame,IMUSample,CameraCalibration
from core.bounded_acquisition import BufferedAcquisition,frame_bytes
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
from ingestion.sensor_synchronizer import SensorSynchronizer


class ControlledSource:
    def __init__(self):self.items=queue.Queue();self.closed=False
    def __iter__(self):return self
    def __next__(self):
        item=self.items.get(timeout=3)
        if item is None:raise StopIteration
        return item
    def close(self):
        if not self.closed:self.items.put(None);self.closed=True


def frame(i):
    start=i*.1
    imu=tuple(IMUSample(t,[0,0,0],[0,0,9.80665]) for t in np.linspace(start,start+.1,5))
    return MultiModalSensorFrame(i,start,[[2,0,.5,1,1,1,0],[1.9,0,.5,1,1,1,.1]],imu)


def wait_count(reader,count):
    end=time.monotonic()+3
    while reader.acquired<count and time.monotonic()<end:time.sleep(.005)
    assert reader.acquired>=count


def test_slow_consumer_keeps_byte_budgets_and_actual_imu_between_dropped_sweeps():
    source=ControlledSource();reader=BufferedAcquisition(source,max_frames=2,max_bytes=frame_bytes(frame(0))*2+10).start()
    try:
        source.items.put(frame(0));wait_count(reader,1);first=next(reader)
        deskewer=ContinuousTimeIMUDeskewer(initial_velocity=[1,0,0]);deskewer.deskew_frame(first)
        for i in range(1,9):source.items.put(frame(i))
        wait_count(reader,9);latest=next(reader)
        assert latest.frame_id==7 and reader.dropped==6
        assert latest.imu_samples[0].timestamp<=.1
        deskewer.deskew_frame(latest)
        np.testing.assert_allclose(deskewer.pose_world[:3,3],[.8,0,0],atol=1e-8)
        status=reader.status();assert status['peak_bytes']<=status['capacity_bytes'] and status['peak_frames']<=2
        assert latest.diagnostics['acquisition_queue']['dropped_frames']==6
    finally:reader.close()


def test_oversized_frames_fail_visibly_instead_of_silently_truncating_observations():
    source=ControlledSource();source.items.put(frame(0));reader=BufferedAcquisition(source,max_bytes=10)
    try:
        with pytest.raises(RuntimeError,match='acquisition failed'):next(reader)
        assert 'byte budget' in reader.status()['error']
    finally:reader.close()


def test_synchronizer_camera_history_is_byte_and_time_bounded_without_clearing_imu():
    sync=SensorSynchronizer(capacity=8,camera_max_bytes=100,camera_history_seconds=.15,imu_capacity=100)
    calib=CameraCalibration(np.eye(3),np.eye(4));rgb=np.zeros((2,2,3),np.uint8);depth=np.ones((2,2),np.float32)
    for i in range(20):
        sync.push_camera(i*.1,rgb,depth,calib);sync.push_imu(IMUSample(i*.1,[0,0,0],[0,0,9.80665]))
    assert len(sync.camera)==2 and sum(c[1].nbytes+c[2].nbytes for c in sync.camera)<=100
    assert len(sync.imu)==20 and sync.dropped==18
    with pytest.raises(MemoryError):sync.push_camera(2,np.zeros((20,20,3)),np.ones((20,20)),calib)

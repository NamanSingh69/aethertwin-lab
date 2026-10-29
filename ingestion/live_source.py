"""Concurrent SDK acquisition with explicit synchronization failures."""
from pathlib import Path
from dataclasses import replace
import json,threading,time
import numpy as np
from core.contracts import CameraCalibration
from .sensor_synchronizer import SensorSynchronizer


class LiveSensorSource:
    def __init__(self,config,root=Path('.')):
        from .lidar_l2_driver import LidarL2Driver
        from .gemini_333l_driver import Gemini335LDriver
        path=Path(config['calibration']);path=path if path.is_absolute() else Path(root)/path
        data=json.loads(path.read_text(encoding='utf-8'))
        calibration=CameraCalibration(np.array(data['K']),np.array(data['T_lidar_camera']),np.array(data.get('distortion',[0]*5)),data['calibration_id'],data.get('measured',False))
        if not calibration.measured:raise ValueError('Live fusion requires a measured calibration; simulation calibration is rejected')
        self.lidar=LidarL2Driver(config['lidar_ip'],config['host_ip'],config['lidar_port'],config['host_port'])
        try:self.camera=Gemini335LDriver(calibration,config['camera_width'],config['camera_height'],config['camera_fps'],
            color_width=config.get('camera_color_width'),color_height=config.get('camera_color_height'),color_fps=config.get('camera_color_fps'),
            calibration_image_size=data.get('image_size'),align_mode=config.get('camera_align_mode','auto'),
            intrinsics_tolerance_px=config.get('camera_intrinsics_tolerance_px',1.0),distortion_tolerance=config.get('camera_distortion_tolerance',1e-4))
        except BaseException:self.lidar.close();raise
        self.sync=SensorSynchronizer(capacity=config.get('sync_capacity',8),camera_max_bytes=config.get('camera_buffer_bytes',64*1024*1024),
            camera_history_seconds=config.get('camera_history_seconds',.75),imu_capacity=config.get('imu_capacity',8192))
        self.lock=threading.Lock();self.stop=threading.Event();self.errors=[]
        self.allow_degraded=bool(config.get('allow_degraded',False))
        self.s2=None;self.s2_latest=None;self.s2_error=None;self.s2_thread=None
        self.imu_transform=np.asarray(data['T_lidar_imu']) if 'T_lidar_imu' in data else None
        self.initial_pose=np.asarray(config.get('initial_pose_world',np.eye(4)),dtype=float)
        self.s2_origin=np.zeros(3);self.s2_max_age=float(config.get('s2_max_age',.15))
        if config.get('s2_port'):
            from .rplidar_s2_driver import RPLidarS2Driver
            if 'T_lidar_s2' not in data:
                self.camera.close();self.lidar.close();raise ValueError('S2 requires measured T_lidar_s2 calibration')
            pitch_file=config.get('s2_pitch_file')
            if pitch_file and not Path(pitch_file).is_absolute():pitch_file=Path(root)/pitch_file
            def pitch():
                info=json.loads(Path(pitch_file).read_text(encoding='utf-8'))
                return float(info['timestamp']),float(info['pitch_rad'])
            try:self.s2=RPLidarS2Driver(config['s2_port'],config.get('s2_baudrate',1000000),np.asarray(data['T_lidar_s2']),pitch if pitch_file else None,
                transport=config.get('s2_transport','serial'),udp_port=config.get('s2_udp_port',8089))
            except BaseException:self.camera.close();self.lidar.close();raise
            self.s2_origin=np.asarray(data['T_lidar_s2'],dtype=float)[:3,3]
            def capture_s2():
                try:
                    while not self.stop.is_set():
                        sample=self.s2.read()
                        with self.lock:self.s2_latest=sample
                except BaseException as exc:
                    self.s2_error=exc
                    if not config.get('allow_degraded',False):self.errors.append(exc);self.stop.set()
            self.s2_thread=threading.Thread(target=capture_s2,name='s2-capture',daemon=True);self.s2_thread.start()
        def capture():
            try:
                while not self.stop.is_set():
                    item=self.camera.read()
                    with self.lock:self.sync.push_camera(*item,**getattr(self.camera,'last_clock_metadata',{}))
            except BaseException as exc:self.errors.append(exc);self.stop.set()
        self.thread=threading.Thread(target=capture,name='gemini-capture',daemon=True);self.thread.start()
    def __iter__(self):return self
    def __next__(self):
        if self.errors:raise RuntimeError('A required sensor acquisition failed') from self.errors[0]
        deadline=time.monotonic()+1
        while time.monotonic()<deadline:
            if self.errors:raise RuntimeError('A required sensor acquisition failed') from self.errors[0]
            with self.lock:result=self.sync.synchronize()
            if result is not None:
                fields={'sensor_pose_world':self.initial_pose}
                if self.imu_transform is not None:fields['imu_to_lidar']=self.imu_transform
                if self.s2 is not None:
                    with self.lock:sample=self.s2_latest
                    if (sample is not None and abs(sample[0]-(result.timestamp+result.sweep_duration))<=self.s2_max_age
                        and result.timestamp-1e-6<=sample[0]<=result.imu_samples[-1].timestamp+1e-6):
                        fields.update(rplidar_s2_points=sample[1],rplidar_timestamp=sample[0],rplidar_origin_lidar=self.s2_origin)
                        fields['diagnostics']={**result.diagnostics,'s2_scan_timing':'single_capture_timestamp_no_per_beam_times'}
                    else:
                        if self.allow_degraded:
                            fields['diagnostics']={**result.diagnostics,'s2_degraded':True,'s2_error':str(self.s2_error or 'no synchronized fresh scan')}
                        else:raise TimeoutError('Configured S2 has no synchronized fresh scan')
                return replace(result,**fields)
            # Acquisition continues while waiting for camera/IMU coverage. This
            # bounds synchronization latency without inventing post-sweep motion.
            frame=self.lidar.next_frame()
            with self.lock:
                self.sync.push_lidar(frame)
                for sample in frame.imu_samples:self.sync.push_imu(sample)
        raise TimeoutError('Cannot synchronize required LiDAR, IMU and RGB-D streams within tolerance')
    def close(self):
        self.stop.set();self.thread.join(timeout=3)
        if self.s2_thread:self.s2_thread.join(timeout=3)
        try:self.camera.close()
        finally:
            try:self.lidar.close()
            finally:
                if self.s2:self.s2.close()

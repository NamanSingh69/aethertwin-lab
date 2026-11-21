"""Merge consecutive raw LiDAR sweeps into one denser scan before deskew and registration.

A Unitree L2 sweep is ~5200 returns in 83.6 ms (reports/hardware/pi); its non-repetitive pattern needs several
sweeps to cover a surface densely. Merging k consecutive raw sweeps keeps every return and its acquisition time
(the deskewer still moves each return to its own IMU pose), but registers k times fewer, k times denser scans:
fewer ICP calls per second of data and better-conditioned point-to-plane constraints. Opt-in; k=1 is identity.
"""
from dataclasses import replace
import numpy as np

# Larger holes or overlaps than these between consecutive sweeps start a new scan instead of being merged.
MAX_HOLE_SECONDS=.05;MAX_OVERLAP_SECONDS=.002


def _mergeable(previous,frame):
    end=previous.timestamp+previous.sweep_duration;gap=frame.timestamp-end
    if gap>MAX_HOLE_SECONDS or gap< -MAX_OVERLAP_SECONDS:return False
    if not np.array_equal(previous.imu_to_lidar,frame.imu_to_lidar):return False
    for key in ('commissioning_stationary','coordinate_reference','station_id'):
        if previous.diagnostics.get(key)!=frame.diagnostics.get(key):return False
    return True


def merge_frames(frames):
    """One raw frame covering consecutive raw sweeps; camera data comes from the newest frame that has it."""
    frames=list(frames)
    if not frames:raise ValueError('Cannot merge an empty sweep group')
    if len(frames)==1:return frames[0]
    first=frames[0];last=frames[-1];start=first.timestamp
    duration=float(max(f.timestamp+f.sweep_duration for f in frames)-start)
    points=[];origins=[];beams=[];any_origins=any(f.lidar_origins is not None for f in frames)
    for f in frames:
        p=np.array(f.lidar_points,np.float64,copy=True);p[:,6]=np.clip(p[:,6]+(f.timestamp-start),0.,duration);points.append(p)
        if any_origins:origins.append(np.zeros((len(p),3)) if f.lidar_origins is None else f.lidar_origins)
        beams.append(f.beam_ids)
    imu={}
    for f in frames:
        for s in f.imu_samples:imu.setdefault(float(s.timestamp),s)
    camera=next((f for f in reversed(frames) if f.rgb is not None or f.depth is not None),last)
    rplidar=next((f for f in reversed(frames) if f.rplidar_s2_points is not None),last)
    diagnostics={**camera.diagnostics,'merged_sweeps':len(frames),'merged_frame_ids':[int(f.frame_id) for f in frames],
                 'merged_sweep_start_gaps_s':[float(b.timestamp-(a.timestamp+a.sweep_duration)) for a,b in zip(frames,frames[1:])]}
    if any(f.diagnostics.get('synthetic') for f in frames):diagnostics['synthetic']=True
    return replace(last,frame_id=first.frame_id,timestamp=start,sweep_duration=duration,lidar_points=np.concatenate(points).astype(first.lidar_points.dtype),
        imu_samples=tuple(imu[t] for t in sorted(imu)),lidar_origins=np.concatenate(origins) if any_origins else None,
        beam_ids=None if any(b is None for b in beams) else np.concatenate(beams),
        return_metadata_valid=all(f.return_metadata_valid for f in frames),painted_features=None,
        rgb=camera.rgb,depth=camera.depth,calibration=camera.calibration,camera_timestamp=camera.camera_timestamp,
        camera_pose_in_lidar=camera.camera_pose_in_lidar,gemini_rgbd_cloud=camera.gemini_rgbd_cloud,
        rplidar_s2_points=rplidar.rplidar_s2_points,rplidar_timestamp=rplidar.rplidar_timestamp,rplidar_origin_lidar=rplidar.rplidar_origin_lidar,
        raw_timestamps=dict(camera.raw_timestamps),diagnostics=diagnostics)


def merge_sweeps(frames,per_scan):
    """Yield scans of up to per_scan consecutive raw sweeps; holes, overlaps and station changes start a new scan."""
    per_scan=int(per_scan)
    if per_scan<1 or per_scan>16:raise ValueError('sweeps per scan must lie in [1, 16]')
    if per_scan==1:yield from frames;return
    group=[]
    for frame in frames:
        if frame.diagnostics.get('coordinate_reference')=='lidar_sweep_end':
            # Already deskewed (prepared) frames are never re-timed.
            if group:yield merge_frames(group);group=[]
            yield frame;continue
        if group and (len(group)>=per_scan or not _mergeable(group[-1],frame)):yield merge_frames(group);group=[]
        group.append(frame)
    if group:yield merge_frames(group)

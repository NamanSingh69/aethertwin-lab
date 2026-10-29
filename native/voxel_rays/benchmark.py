"""Exact native/reference ray and full occupancy timing on recorded sensors."""
from pathlib import Path
import argparse,json,time
import numpy as np
from core.se3_math import transform_points
from ingestion.recorder_replayer import ReplaySource
from ingestion.sensor_synchronizer import backproject_depth
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
import slam_and_twin.log_odds_octomap as occupancy
from . import backend_status,traverse_beams,traverse_rays


def measure(function,repeats):
    samples=[];last=None
    for _ in range(repeats):
        start=time.perf_counter();last=function();samples.append(1000*(time.perf_counter()-start))
    return {'p50_ms':float(np.median(samples)),'p95_ms':float(np.percentile(samples,95)),'samples_ms':samples},last


def recorded_observations(recording,frames):
    source=ReplaySource(recording);deskewer=None;result=[]
    try:
        for index,raw in enumerate(source):
            if deskewer is None:deskewer=ContinuousTimeIMUDeskewer(initial_pose=raw.sensor_pose_world,imu_to_lidar=raw.imu_to_lidar)
            frame=deskewer.deskew_frame(raw);pose=frame.sensor_pose_world
            points=frame.lidar_points.copy();points[:,:3]=transform_points(pose,points[:,:3])
            origins=pose[:3,3] if frame.lidar_origins is None else transform_points(pose,frame.lidar_origins)
            modalities=[(points,origins,frame.beam_ids,frame.return_metadata_valid)]
            if frame.depth is not None:
                dense=backproject_depth(frame.depth,frame.calibration,3,frame.rgb,camera_pose_in_lidar=frame.camera_pose_in_lidar)
                dense_points=np.zeros((len(dense),7),np.float32);dense_points[:,:3]=transform_points(pose,dense[:,:3]);dense_points[:,3]=.5;dense_points[:,4:6]=1
                camera_pose=frame.calibration.T_lidar_camera if frame.camera_pose_in_lidar is None else frame.camera_pose_in_lidar
                modalities.append((dense_points,(pose@camera_pose)[:3,3],None,False))
            if frame.rplidar_s2_points is not None:
                ring=np.zeros((len(frame.rplidar_s2_points),7),np.float32);ring[:,:3]=transform_points(pose,frame.rplidar_s2_points);ring[:,3]=.5;ring[:,4:6]=1
                origin=transform_points(pose,frame.rplidar_origin_lidar[None])[0];modalities.append((ring,origin,None,False))
            result.append((frame.timestamp+frame.sweep_duration,modalities))
            if index+1>=frames:break
    finally:source.close()
    return result


def benchmark(recording,output,repeats=5,frames=3):
    status=backend_status()
    if not status['available']:raise RuntimeError(status['reason'])
    observations=recorded_observations(recording,frames)
    if not observations:raise ValueError('Recording contains no observations')
    reference=occupancy.traverse_beams
    points,origins,beam_ids,metadata=observations[0][1][0]
    origins=np.broadcast_to(origins,(len(points),3))
    distance=np.linalg.norm(points[:,:3]-origins,axis=1)
    if beam_ids is None:finals=np.arange(len(points))
    else:
        order=np.lexsort((distance,beam_ids));boundaries=np.r_[0,np.flatnonzero(np.diff(beam_ids[order]))+1,len(points)]
        finals=order[boundaries[1:]-1] if metadata else order[boundaries[:-1]]
    args=(origins[finals],points[finals,:3],.18)
    report={'schema_version':1,'source':'actual_recorded_multimodal_sensor_frames','recording':str(recording),'frames':len(observations),
        'native_status':status,'selected_lidar_rays':len(finals),'voxel_size':.18,
        'limits':'Full occupancy measurements include LiDAR, calibrated depth and S2; no perception/planning/render time is included.'}
    report['reference_batch_rays'],expected=measure(lambda:reference(*args),repeats)
    report['native_batch_rays'],actual=measure(lambda:traverse_beams(*args,backend='native'),repeats)
    np.testing.assert_array_equal(expected,actual)
    report['unique_ray_cells']=len(actual);report['native_sequence_cells']=len(traverse_rays(*args,backend='native').keys)
    def integrate(function):
        old=occupancy.traverse_beams;occupancy.traverse_beams=function
        try:
            grid=occupancy.SparseMultiReturnVoxelMap(.18,ray_backend='reference')
            for timestamp,modalities in observations:
                for points,origin,beam_ids,metadata in modalities:
                    grid.voxelize_and_update(points,timestamp=timestamp,origin=origin,beam_ids=beam_ids,return_metadata_valid=metadata)
            return grid
        finally:occupancy.traverse_beams=old
    report['reference_full_occupancy'],expected_map=measure(lambda:integrate(reference),repeats)
    report['native_full_occupancy'],actual_map=measure(lambda:integrate(lambda *a,**kw:traverse_beams(*a,backend='native',**kw)),repeats)
    assert expected_map.cells.keys()==actual_map.cells.keys()
    maximum=max((float(np.max(np.abs(np.array(value)-actual_map.cells[key]))) for key,value in expected_map.cells.items()),default=0.)
    if maximum:raise RuntimeError(f'Native occupancy state differs: {maximum}')
    report['occupancy_cells']=len(actual_map.cells);report['maximum_state_error']=maximum;report['parity_passed']=True
    Path(output).parent.mkdir(parents=True,exist_ok=True);Path(output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--recording',type=Path,default=Path('assets/sample_recording'))
    parser.add_argument('--output',type=Path,default=Path('artifacts/native_voxel_rays.json'))
    parser.add_argument('--repeats',type=int,default=5);parser.add_argument('--frames',type=int,default=3)
    args=parser.parse_args()
    if args.repeats<2 or args.frames<1:parser.error('Use at least two measurements and positive frame count')
    result=benchmark(args.recording,args.output,args.repeats,args.frames)
    print(json.dumps({key:value for key,value in result.items() if not isinstance(value,dict)},indent=2))

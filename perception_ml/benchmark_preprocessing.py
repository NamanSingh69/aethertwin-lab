"""Measure preprocessing and tensor-boundary parity on actual recorded sensors."""
from pathlib import Path
import argparse,json,platform,time
import numpy as np
import torch
from core.se3_math import transform_points
from ingestion.recorder_replayer import ReplaySource
from ingestion.sensor_synchronizer import backproject_depth
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
from .depth_cache import DepthRayCache
from .inference_engine import InferenceEngine
from .spatial_indices import build_spatial_index_pack


def measure(function,repeats):
    samples=[]
    for _ in range(repeats):
        start=time.perf_counter();function();samples.append(1000*(time.perf_counter()-start))
    return {'p50_ms':float(np.median(samples)),'p95_ms':float(np.percentile(samples,95)),'samples_ms':samples}


def benchmark(checkpoint,recording,output,repeats=12):
    engine=InferenceEngine(checkpoint);source=ReplaySource(recording);deskewer=None;previous=None;prepared=None
    try:
        for index,raw in enumerate(source):
            if deskewer is None:deskewer=ContinuousTimeIMUDeskewer(initial_pose=raw.sensor_pose_world,imu_to_lidar=raw.imu_to_lidar)
            frame=deskewer.deskew_frame(raw);points=frame.lidar_points.copy();points[:,:3]=transform_points(frame.sensor_pose_world,points[:,:3])
            previous=engine.previous;prepared,*_=engine.infer(frame,points)
            if index>=1:break
    finally:source.close()
    if prepared is None:raise ValueError('Benchmark recording is empty')
    kwargs=dict(prepared.inputs)
    if engine.temporal_valid:kwargs['coords_prev'],kwargs['cached_stage2_prev']=previous
    old={key:value for key,value in kwargs.items() if key!='spatial_indices'}
    report={'schema_version':1,'source':'actual_recorded_sensor_frames','checkpoint':str(checkpoint),'recording':str(recording),
        'platform':platform.platform(),'torch_threads':torch.get_num_threads(),'voxels':len(kwargs['coords_t']),
        'points':len(kwargs['point_features']),'temporal':engine.temporal_valid,
        'limits':'Component CPU timings; not whole-system FPS. Native camera processing resolution retained.'}
    with torch.inference_mode():
        expected=engine.model(**old);actual=engine.model(**kwargs)
        error=max(float((a-b).abs().max()) for a,b in zip(expected,actual))
        if error>2e-5:raise RuntimeError(f'Canonical model parity failed: {error}')
        report['maximum_output_error']=error
        report['legacy_neural_with_repeated_queries']=measure(lambda:engine.model(**old),repeats)
        report['tensor_neural_precomputed_indices']=measure(lambda:engine.model(**kwargs),repeats)
        def packed_total():
            pack=build_spatial_index_pack(kwargs['coords_t'],engine.model.neighbors,engine.model.temporal_neighbors,
                kwargs.get('coords_prev'))
            engine.model(**{**kwargs,'spatial_indices':pack})
        report['index_preparation_and_tensor_neural']=measure(packed_total,repeats)
    if frame.depth is not None:
        cache=DepthRayCache();cache.backproject(frame.depth,frame.calibration,3,frame.rgb,frame.camera_pose_in_lidar)
        legacy=lambda:backproject_depth(frame.depth,frame.calibration,3,frame.rgb,camera_pose_in_lidar=frame.camera_pose_in_lidar)
        candidate=lambda:cache.backproject(frame.depth,frame.calibration,3,frame.rgb,frame.camera_pose_in_lidar)
        np.testing.assert_array_equal(legacy(),candidate())
        report['depth_uncached']=measure(legacy,repeats);report['depth_cached']=measure(candidate,repeats)
        report['depth_cache_hits']=cache.hits
    Path(output).parent.mkdir(parents=True,exist_ok=True);Path(output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint',type=Path,default=Path('assets/checkpoints/synthetic.pt'))
    parser.add_argument('--recording',type=Path,default=Path('assets/sample_recording'))
    parser.add_argument('--output',type=Path,default=Path('artifacts/perception_preprocessing.json'))
    parser.add_argument('--repeats',type=int,default=12);parser.add_argument('--threads',type=int,default=2)
    args=parser.parse_args()
    if args.repeats<2 or args.threads<1:parser.error('Use at least two measurements and positive threads')
    torch.set_num_threads(args.threads)
    result=benchmark(args.checkpoint,args.recording,args.output,args.repeats)
    print(json.dumps({key:value for key,value in result.items() if not isinstance(value,dict)},indent=2))

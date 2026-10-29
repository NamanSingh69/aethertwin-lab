"""Profiled tensor-only torch.export artifacts with mandatory numerical gates.

CPU spatial preparation and calibrated image geometry stay outside the graph.
Profiles explicitly record current/previous voxel and point counts. Empty and
expired-history frames remain handled by the Python runtime, not a traced
nonempty profile. TensorRT and FP16 availability are reported independently.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,time
import numpy as np
import torch
from torch import nn
from .spatial_indices import SpatialIndexPack


class TensorInferenceModule(nn.Module):
    """Only tensors cross this boundary; indices refer to common-world points."""
    def __init__(self,model,temporal=True):super().__init__();self.model=model;self.temporal=bool(temporal)
    def forward(self,coords,voxel,eigen,context,points,mapping,camera,sensor,
                previous_coords,previous_features,stage1,fps,small,large,propagation,temporal,eigen_indices):
        spatial=SpatialIndexPack(stage1,fps,small,large,propagation,temporal,eigen_indices)
        return self.model(coords,voxel,eigen,previous_coords if self.temporal else None,
            previous_features if self.temporal else None,context,points,mapping,camera,sensor,spatial)


def tensor_arguments(prepared,previous_coords=None,previous_features=None):
    values=prepared.inputs;coords=values['coords_t']
    if previous_coords is None:previous_coords=coords.new_empty((0,3))
    if previous_features is None:previous_features=coords.new_empty((0,256))
    names=('coords_t','voxel_feats_t','eigen_feats_t','context','point_features','point_to_voxel','camera_features','sensor_coords')
    return tuple(values[name] for name in names)+(previous_coords,previous_features)+tuple(values['spatial_indices'])


def _max_error(expected,actual):
    return max((float((a-b).abs().max()) if a.numel() else 0.) for a,b in zip(expected,actual))


def _latency(module,args,repeats=5):
    samples=[]
    for _ in range(repeats):
        start=time.perf_counter();module(*args)
        if args[0].is_cuda:torch.cuda.synchronize()
        samples.append(1000*(time.perf_counter()-start))
    return {'median_ms':float(np.median(samples)),'p95_ms':float(np.percentile(samples,95)),'repeats':repeats}


def export_tensor_profile(model,prepared,output,previous_coords=None,previous_features=None,
                          camera_input=None,precision='fp32',checkpoint=None):
    """Write gated modules or an explicit unsupported report; never promote weights."""
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if precision not in ('fp32','fp16'):raise ValueError('Export precision must be fp32 or fp16')
    args=tensor_arguments(prepared,previous_coords,previous_features)
    device=args[0].device
    report={'schema_version':1,'backend':'torch.export','precision':precision,'device':str(device),
        'fp16':{'status':'unsupported' if device.type!='cuda' else 'unverified',
            'reason':'CPU remains FP32; CUDA FP16 requires its own numerical and latency gates'},
        'tensorrt':{'status':'unverified' if importlib.util.find_spec('tensorrt') else 'unsupported',
            'reason':'No TensorRT/Jetson deployment or numerical/latency measurement is available'},
        'profile':{'current_voxels':len(args[0]),'previous_voxels':len(args[8]),'points':len(args[4]),
            'temporal':bool(len(args[8])),'camera_input_shape':list(camera_input.shape) if camera_input is not None else None},
        'checkpoint_sha256':hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest() if checkpoint else None,
        'limits':'Profiled nonempty counts; raw RGB-D, spatial indices and empty/history gating remain outside the graph'}
    if not len(args[0]):raise ValueError('Export needs a nonempty representative profile; empty frames use the runtime gate')
    if precision=='fp16' and device.type!='cuda':
        report.update(status='unsupported',reason='FP16 is enabled only after measured CUDA numerical and latency gates; CPU remains FP32')
        (output/'export.json').write_text(json.dumps(report,indent=2),encoding='utf-8');return report
    import copy
    export_model=copy.deepcopy(model).eval().requires_grad_(False)
    fp32_model=None;fp32_args=None
    if precision=='fp16':
        fp32_model=TensorInferenceModule(copy.deepcopy(model).float().eval().requires_grad_(False),temporal=bool(len(args[8])))
        fp32_args=tuple(x.float() if x.is_floating_point() else x for x in args)
        export_model.half();args=tuple(x.half() if x.is_floating_point() else x for x in args)
        if camera_input is not None:camera_input=camera_input.half()
    wrapper=TensorInferenceModule(export_model,temporal=bool(len(args[8]))).eval()
    tolerance=3e-3 if precision=='fp16' else 2e-5
    fastpath=torch.backends.mha.get_fastpath_enabled()
    try:
        torch.backends.mha.set_fastpath_enabled(False)
        with torch.no_grad():
            expected=wrapper(*args)
            if fp32_model is not None:
                fp32_result=fp32_model(*fp32_args)
                fp16_error=_max_error(fp32_result,tuple(x.float() for x in expected))
                if not np.isfinite(fp16_error) or fp16_error>tolerance:
                    raise RuntimeError(f'FP16 is unsupported for this measured profile: FP32 difference {fp16_error} > {tolerance}')
                report['fp16_vs_fp32_max_error']=fp16_error
                report['fp32_reference_latency']=_latency(fp32_model,fp32_args)
                report['fp16']={'status':'measured_passed','reason':'Profile passed the measured FP32 comparison; see separate component latency'}
            program=torch.export.export(wrapper,args,strict=True)
            traced=program.module()
            actual=traced(*args);error=_max_error(expected,actual)
            if not np.isfinite(error) or error>tolerance:raise RuntimeError(f'Tensor export parity failed: {error} > {tolerance}')
            # A second tensor set checks that coordinates/features were not captured
            # as constants. Spatial support is rebuilt outside the traced graph.
            shifted=list(args);shifted[1]=args[1]+.001;shifted[2]=args[2]*.99
            second=_max_error(wrapper(*shifted),traced(*shifted))
            if not np.isfinite(second) or second>tolerance:raise RuntimeError('Tensor export second-input parity failed')
            reference_time=_latency(wrapper,args);export_time=_latency(traced,args)
            report.update(status='passed',max_absolute_error=error,second_input_error=second,tolerance=tolerance,
                eager_latency=reference_time,export_latency=export_time)
            camera_program=None
            if camera_input is not None:
                camera_program=torch.export.export(export_model.camera,(camera_input,),strict=True)
                camera=camera_program.module()
                camera_eager=export_model.camera(camera_input)
                camera_error=_max_error(camera_eager,camera(camera_input))
                if not np.isfinite(camera_error) or camera_error>tolerance:raise RuntimeError('Camera export parity failed')
                if fp32_model is not None:
                    camera_fp32=fp32_model.model.camera(camera_input.float())
                    camera_half_error=_max_error(camera_fp32,tuple(x.float() for x in camera_eager))
                    if not np.isfinite(camera_half_error) or camera_half_error>tolerance:raise RuntimeError('FP16 camera comparison against FP32 failed')
                    report['fp16_camera_vs_fp32_max_error']=camera_half_error
                report['camera_max_absolute_error']=camera_error
            # Publish only after every requested graph passes its gates.
            temporary=output/'.perception_tensor.tmp.pt2';torch.export.save(program,str(temporary))
            if camera_program is not None:
                temporary_camera=output/'.camera_tensor.tmp.pt2';torch.export.save(camera_program,str(temporary_camera))
                temporary_camera.replace(output/'camera_tensor.pt2')
            temporary.replace(output/'perception_tensor.pt2')
    finally:torch.backends.mha.set_fastpath_enabled(fastpath)
    (output/'export.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint',type=Path,required=True);parser.add_argument('--recording',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--precision',choices=('fp32','fp16'),default='fp32')
    parser.add_argument('--device',default='cpu');parser.add_argument('--threads',type=int,default=2);args=parser.parse_args()
    if args.threads<1:parser.error('Use a positive CPU thread count')
    torch.set_num_threads(args.threads)
    from ingestion.recorder_replayer import ReplaySource
    from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
    from core.se3_math import transform_points
    from .inference_engine import InferenceEngine
    source=ReplaySource(args.recording);engine=InferenceEngine(args.checkpoint,device=args.device);deskewer=None;prepared=None;previous=None;camera_input=None
    try:
        for index,raw in enumerate(source):
            if deskewer is None:deskewer=ContinuousTimeIMUDeskewer(initial_pose=raw.sensor_pose_world,imu_to_lidar=raw.imu_to_lidar)
            frame=deskewer.deskew_frame(raw);points=frame.lidar_points.copy();points[:,:3]=transform_points(frame.sensor_pose_world,points[:,:3])
            previous=engine.previous;prepared,*_=engine.infer(frame,points)
            if frame.rgb is not None and frame.depth is not None:
                image=np.concatenate([frame.rgb.astype(np.float32)/255,np.clip(frame.depth[:,:,None]/6,0,1)],axis=-1)
                camera_input=torch.tensor(image,device=args.device).permute(2,0,1)[None]
            if index>=1 and len(prepared.inputs['coords_t']):break
    finally:source.close()
    if prepared is None:raise ValueError('Export recording contains no frames')
    previous=(None,None) if not engine.temporal_valid else previous
    print(json.dumps(export_tensor_profile(engine.model,prepared,args.output,*previous,camera_input,precision=args.precision,checkpoint=args.checkpoint),indent=2))


if __name__=='__main__':main()

import copy
from pathlib import Path
import numpy as np
import pytest
import torch
from torch import nn
from core.contracts import CameraCalibration
from core.se3_math import transform_points
from ingestion.sensor_synchronizer import backproject_depth
from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
from perception_ml.depth_cache import DepthRayCache
from perception_ml.hybrid_3d_network import MultiTaskVoxelSceneFlowNet
from perception_ml.inference_engine import prepare_frame,fused_observations,InferenceEngine
from perception_ml.spatial_indices import build_spatial_index_pack
from perception_ml.tensor_export import export_tensor_profile,tensor_arguments

torch.set_num_threads(2)
ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('temporal',[False,True])
def test_precomputed_indices_preserve_values_and_gradients_without_neural_cpu_queries(monkeypatch,temporal):
    import perception_ml.hybrid_3d_network as network
    torch.manual_seed(52);legacy=MultiTaskVoxelSceneFlowNet();packed=copy.deepcopy(legacy)
    coords=torch.randn(91,3);features=torch.randn(91,12,requires_grad=True);eigen=torch.rand(91,10)
    previous=torch.randn(77,3) if temporal else None
    previous_features=torch.randn(77,256) if temporal else None
    expected=legacy(coords,features,eigen,previous,previous_features)
    (expected[0].square().mean()+expected[1].square().mean()).backward()
    pack=build_spatial_index_pack(coords,previous_coords=previous)
    monkeypatch.setattr(network,'neighbor_indices',lambda *a,**k:(_ for _ in ()).throw(AssertionError('CPU query inside tensor network')))
    monkeypatch.setattr(torch,'cdist',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Full pairwise allocation')))
    inputs=features.detach().clone().requires_grad_(True)
    actual=packed(coords,inputs,eigen,previous,previous_features,spatial_indices=pack)
    for a,b in zip(actual,expected):torch.testing.assert_close(a,b,rtol=0,atol=1e-6)
    (actual[0].square().mean()+actual[1].square().mean()).backward()
    torch.testing.assert_close(inputs.grad,features.grad,rtol=1e-5,atol=1e-6)
    for (name,a),(_,b) in zip(legacy.named_parameters(),packed.named_parameters()):
        if a.grad is not None:torch.testing.assert_close(a.grad,b.grad,rtol=1e-5,atol=1e-6,msg=name)
    packed.load_state_dict(legacy.state_dict())


def test_spatial_pack_uses_only_local_storage_and_common_world_temporal_coordinates():
    xyz=np.random.default_rng(4).normal(size=(1400,3)).astype(np.float32)
    pack=build_spatial_index_pack(xyz,temporal_neighbors=1,previous_coords=xyz.copy())
    np.testing.assert_array_equal(pack.temporal[:,0],np.arange(len(xyz)))
    assert pack.stage1.shape==(1400,16) and pack.scale_large.shape==(256,32)
    assert sum(x.numel() for x in pack)<1400*100
    empty=build_spatial_index_pack(np.empty((0,3)))
    assert empty.stage1.shape==(0,0) and empty.fps.shape==(0,)
    with pytest.raises(ValueError):build_spatial_index_pack(np.full((2,3),np.nan))


def test_depth_ray_cache_matches_calibrated_distorted_projection_and_never_caches_capture_pose():
    cache=DepthRayCache(capacity=2);rng=np.random.default_rng(13)
    depth=rng.uniform(.1,6.2,(61,87)).astype(np.float32);depth[0,0]=0
    rgb=rng.integers(0,255,(61,87,3),dtype=np.uint8)
    calibration=CameraCalibration(np.array([[71.,0,41.],[0,70.,30.],[0,0,1.]]),np.eye(4),np.array([.05,-.02,.001,.002,0.]))
    for translation in (0.,.7):
        pose=np.eye(4);pose[0,3]=translation
        actual=cache.backproject(depth,calibration,3,rgb,camera_pose_in_lidar=pose)
        expected=backproject_depth(depth,calibration,3,rgb,camera_pose_in_lidar=pose)
        np.testing.assert_array_equal(actual,expected)
    assert cache.hits==1 and cache.misses==1
    changed=CameraCalibration(calibration.K+np.diag([1.,0,0]),np.eye(4),calibration.distortion,calibration.calibration_id)
    cache.backproject(depth,changed,3);cache.backproject(depth[:20],changed,2)
    assert cache.misses==3 and len(cache._entries)==2


def test_reused_depth_cloud_matches_fusion_and_camera_feature_resize_preserves_pixel_coordinates():
    class CoordinateCamera(nn.Module):
        def forward(self,image):
            h,w=image.shape[-2:]
            y,x=torch.meshgrid(torch.linspace(-1,1,h),torch.linspace(-1,1,w),indexing='ij')
            maps=torch.stack([x,y]*8)[None].to(image)
            return maps,image.new_zeros((1,7,h,w))
    source=SyntheticLabSimulator(seed=19,config={'lidar_points':500,'width':64,'height':40})
    try:
        frame=source.next_frame();points=frame.lidar_points.copy();points[:,:3]=transform_points(frame.sensor_pose_world,points[:,:3])
        dense=backproject_depth(frame.depth,frame.calibration,3,frame.rgb)
        expected,_=fused_observations(frame,points);actual,_=fused_observations(frame,points,dense_rgbd=dense)
        np.testing.assert_array_equal(actual,expected)
        with pytest.raises(ValueError):fused_observations(frame,points,dense_rgbd=dense[:-1])
        model=MultiTaskVoxelSceneFlowNet();model.camera=CoordinateCamera()
        native=prepare_frame(model,frame,actual,max_voxels=200)
        resized=prepare_frame(model,frame,actual,max_voxels=200,camera_feature_height=16,camera_feature_width=24)
        torch.testing.assert_close(native.inputs['camera_features'],resized.inputs['camera_features'],atol=2e-6,rtol=0)
        assert resized.camera_logits.shape[-2:]==(16,24)
    finally:source.close()


@pytest.mark.parametrize('has_history',[False,True])
def test_export_real_checkpoint_profile_is_numerically_gated_and_fp16_cpu_is_explicit(tmp_path,has_history):
    source=SyntheticLabSimulator(seed=19,config={'lidar_points':400,'width':40,'height':24})
    engine=InferenceEngine(ROOT/'assets/checkpoints/synthetic.pt',max_voxels=80)
    try:
        previous=None
        for _ in range(2 if has_history else 1):
            frame=source.next_frame();points=frame.lidar_points.copy();points[:,:3]=transform_points(frame.sensor_pose_world,points[:,:3])
            previous=engine.previous;prepared,*_=engine.infer(frame,points)
        previous=(None,None) if previous is None else previous
        image=np.concatenate([frame.rgb.astype(np.float32)/255,np.clip(frame.depth[:,:,None]/6,0,1)],-1)
        camera=torch.tensor(image).permute(2,0,1)[None]
        report=export_tensor_profile(engine.model,prepared,tmp_path/'fp32',*previous,camera,checkpoint=ROOT/'assets/checkpoints/synthetic.pt')
        assert report['status']=='passed' and report['max_absolute_error']<=2e-5
        assert report['second_input_error']<=2e-5 and report['camera_max_absolute_error']<=2e-5
        loaded=torch.export.load(str(tmp_path/'fp32/perception_tensor.pt2')).module()
        result=loaded(*tensor_arguments(prepared,*previous))
        assert result[0].shape==(80,7) and result[1].shape==(80,3)
        wrong=list(tensor_arguments(prepared,*previous));wrong[0]=wrong[0][:-1]
        with pytest.raises((AssertionError,RuntimeError),match='Guard|shape|size'):loaded(*wrong)
        unsupported=export_tensor_profile(engine.model,prepared,tmp_path/'fp16',*previous,precision='fp16')
        assert unsupported['status']=='unsupported' and not (tmp_path/'fp16/perception_tensor.pt2').exists()
        assert 'status' in unsupported['tensorrt']
    finally:source.close()

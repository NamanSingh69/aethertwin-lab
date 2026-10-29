import numpy as np
import torch
import pytest
from perception_ml.hybrid_3d_network import MultiTaskVoxelSceneFlowNet,LearnedVoxelEncoder
from perception_ml.point_mae_pretrain import PointMaskedAutoencoder
from perception_ml.eigen_descriptors import eigen_descriptors
from perception_ml.inference_engine import prepare_frame,fused_observations,aggregate_targets
from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
from core.se3_math import transform_points

torch.set_num_threads(2)

def test_learned_vfe_is_invariant_to_point_order():
    torch.manual_seed(1);encoder=LearnedVoxelEncoder()
    points=torch.randn(20,12);mapping=torch.arange(20)%4
    permutation=torch.randperm(20)
    assert torch.allclose(encoder(points,mapping,4),encoder(points[permutation],mapping[permutation],4))

def test_sparse_network_uses_local_search_and_backpropagates(monkeypatch):
    torch.manual_seed(2);model=MultiTaskVoxelSceneFlowNet()
    coords=torch.randn(80,3);features=torch.randn(80,12);eigen=torch.tensor(eigen_descriptors(coords.numpy()))
    monkeypatch.setattr(torch,'cdist',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('Dense distance matrix forbidden')))
    logits,flow,cache=model(coords,features,eigen)
    logits2,flow2,_=model(coords+.01,features,eigen,coords,cache.detach())
    loss=logits2.square().mean()+flow2.square().mean();loss.backward()
    assert logits.shape==(80,7) and flow.shape==(80,3) and torch.isfinite(loss)
    for name in ['stage1','stage2','flow_corr','semantic_head','scene_flow_head','range','cross_attention']:
        assert any(p.grad is not None and torch.isfinite(p.grad).all() for p in getattr(model,name).parameters())
    empty=model(torch.empty(0,3),torch.empty(0,12),torch.empty(0,10))
    assert empty[0].shape==(0,7)

def test_mae_updates_shared_backbone_without_labels():
    torch.manual_seed(4);model=PointMaskedAutoencoder()
    points=torch.randn(96,3);loss,_,_=model(points);loss.backward()
    assert torch.isfinite(loss)
    assert model.backbone.vfe.point[0].weight.grad is not None
    assert any(p.grad is not None for p in model.backbone.stage2.parameters())

def test_fused_targets_are_separate_from_runtime_observations():
    sim=SyntheticLabSimulator(seed=19,config={'lidar_points':500,'width':64,'height':40})
    sim.commissioning_frames();frame=sim.next_frame();truth=sim.last_ground_truth
    points=frame.lidar_points.copy();points[:,:3]=transform_points(frame.sensor_pose_world,points[:,:3])
    runtime,_=fused_observations(frame,points)
    training,targets=fused_observations(frame,points,truth)
    assert np.array_equal(runtime,training) and len(runtime)>len(points)
    assert len(targets.semantic)==len(runtime) and targets.flow.shape==(len(runtime),3)
    assert not hasattr(frame,'semantic') and not hasattr(frame,'instance_ids')
    model=MultiTaskVoxelSceneFlowNet();prepared=prepare_frame(model,frame,runtime,max_voxels=200)
    labels,flow,valid,ids=aggregate_targets(prepared,targets)
    assert labels.min()>=0 and labels.max()<7 and flow.shape==(len(labels),3)
    assert np.isfinite(prepared.voxels.voxel_features).all()
    sim.close()


def test_configured_neighbor_counts_reach_spatial_and_temporal_search(monkeypatch):
    import perception_ml.hybrid_3d_network as network
    model=network.MultiTaskVoxelSceneFlowNet(neighbors=5,temporal_neighbors=4,range_image_height=8,range_image_width=32)
    assert model.stage1.k==model.stage2.k==5 and model.flow_corr.k==4
    assert model.range.height==8 and model.range.width==32
    range_shapes=[]
    model.range.encoder.register_forward_pre_hook(lambda module,args:range_shapes.append(tuple(args[0].shape)))
    observed=[];original=network.neighbor_indices
    def capture(query,support,k):
        observed.append(k)
        return original(query,support,k)
    monkeypatch.setattr(network,'neighbor_indices',capture)
    coords=torch.randn(40,3);features=torch.randn(40,12);eigen=torch.zeros(40,10)
    logits,flow,_=model(coords,features,eigen,torch.randn(37,3),torch.randn(37,256))
    assert logits.shape==(40,7) and flow.shape==(40,3)
    assert 5 in observed and 10 in observed and 4 in observed
    assert range_shapes==[(1,4,8,32)]
    assert torch.isfinite(logits).all() and torch.isfinite(flow).all()
    # Neighbor count changes do not invalidate saved model tensor shapes.
    other=network.MultiTaskVoxelSceneFlowNet()
    other.load_state_dict(model.state_dict())


@pytest.mark.parametrize('values',[{'neighbors':0},{'neighbors':-1},{'neighbors':True},
                                  {'temporal_neighbors':0},{'temporal_neighbors':1.5},
                                  {'range_image_height':0},{'range_image_width':False},{'range_image_width':3.2}])
def test_invalid_neighborhood_configuration_is_rejected(values):
    with pytest.raises(ValueError,match='positive integer'):
        MultiTaskVoxelSceneFlowNet(**values)


def test_camera_present_empty_lidar_and_invalid_depth_inference_is_safe():
    from dataclasses import replace
    from pathlib import Path
    from core.contracts import MultiModalSensorFrame,CameraCalibration
    from perception_ml.inference_engine import InferenceEngine
    checkpoint=Path(__file__).resolve().parents[1]/'assets/checkpoints/synthetic.pt'
    engine=InferenceEngine(checkpoint,neighbors=5,temporal_neighbors=4)
    assert engine.model.stage1.k==engine.model.stage2.k==5
    assert engine.model.flow_corr.k==4
    h,w=24,32
    calibration=CameraCalibration(np.array([[24.,0,15.5],[0,24.,11.5],[0,0,1.]]),np.eye(4))
    empty=MultiModalSensorFrame(0,0.,np.empty((0,7),np.float32),
        rgb=np.zeros((h,w,3),np.uint8),depth=np.zeros((h,w),np.float32),
        calibration=calibration,camera_timestamp=.1)
    for frame in (empty,replace(empty,frame_id=1,timestamp=.1,camera_timestamp=.2)):
        prepared,probabilities,flow,embedding=engine.infer(frame,frame.lidar_points)
        assert prepared.voxels.voxel_centers.shape==(0,3)
        assert probabilities.shape==(0,7) and flow.shape==(0,3) and embedding.shape==(0,256)
        assert engine.temporal_valid is False
    # The first real return after empty frames must not reuse an empty temporal
    # feature cache as if it supplied a valid scene-flow velocity observation.
    points=np.array([[0.,0.,1.,.4,1.,1.,.05]],np.float32)
    frame=replace(empty,frame_id=2,timestamp=.2,camera_timestamp=.3,lidar_points=points)
    _,probabilities,flow,_=engine.infer(frame,points)
    assert probabilities.shape==(1,7) and flow.shape==(1,3)
    assert np.isfinite(probabilities).all() and np.isfinite(flow).all()
    assert engine.temporal_valid is False


def test_shared_rgbd_fusion_rejects_invalid_stride():
    from core.contracts import MultiModalSensorFrame
    frame=MultiModalSensorFrame(0,0.,np.empty((0,7),np.float32))
    for stride in (0,-1,False,1.5):
        with pytest.raises(ValueError,match='positive integer'):
            fused_observations(frame,frame.lidar_points,stride=stride)

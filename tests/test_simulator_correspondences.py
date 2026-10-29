"""Independent continuous material pairs, separate from noisy sensor inputs."""
import numpy as np
import pytest
from core.se3_math import transform_points
from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
from perception_ml.inference_engine import fused_observations


@pytest.mark.parametrize('mobile',[False,True])
def test_material_pairs_are_clean_noise_independent_and_not_runtime_inputs(mobile):
    observations=[];truths=[]
    for noise in (0.,.035):
        source=SyntheticLabSimulator(seed=17,config={'lidar_points':1800,'width':80,'height':50,'range_noise':noise},mobile=mobile)
        try:
            first=source.next_frame();initial=source.last_ground_truth
            assert not initial.correspondences.valid.any()
            assert not initial.camera_correspondences.valid.any()
            previous_history=source._previous_history
            frame=source.next_frame();truth=source.last_ground_truth
            current_history=source._previous_history
            assert not hasattr(frame,'correspondences') and not hasattr(frame,'semantic')
            pairs=truth.correspondences
            assert pairs.valid.all() and not pairs.current_world.flags.writeable
            np.testing.assert_allclose(pairs.current_world-pairs.previous_world,truth.flow,atol=1e-8)
            np.testing.assert_allclose(pairs.timestamps,frame.timestamp+frame.lidar_points[:,6].astype(np.float64),atol=4e-9,rtol=0)
            np.testing.assert_allclose(pairs.timestamps-pairs.previous_timestamps,frame.sweep_duration,atol=1e-14)
            count=0
            for name,identity in [('human',10),('human2',11),('cart',12)]:
                mask=truth.instance_ids==identity
                if not mask.any():continue
                count+=int(mask.sum())
                rotation,position=source._body_transforms(name,*current_history,pairs.timestamps[mask])
                previous_rotation,previous_position=source._body_transforms(name,*previous_history,pairs.previous_timestamps[mask])
                material=np.einsum('nji,nj->ni',rotation,pairs.current_world[mask]-position)
                old_material=np.einsum('nji,nj->ni',previous_rotation,pairs.previous_world[mask]-previous_position)
                np.testing.assert_allclose(material,old_material,atol=1e-12)
            assert count>10
            camera=truth.camera_correspondences
            assert len(camera.valid)==frame.depth.size
            np.testing.assert_array_equal(camera.valid,(frame.depth>0).ravel())
            np.testing.assert_allclose((camera.current_world-camera.previous_world)[camera.valid],truth.camera_flow.reshape(-1,3)[camera.valid],atol=1e-8)
            assert np.isfinite(camera.current_world).all() and np.isfinite(camera.previous_world).all()
            observations.append(frame);truths.append(truth)
        finally:source.close()
    assert np.max(np.abs(observations[0].lidar_points[:,:3]-observations[1].lidar_points[:,:3]))>.02
    for name in ('current_world','previous_world','timestamps','previous_timestamps','valid'):
        np.testing.assert_array_equal(getattr(truths[0].correspondences,name),getattr(truths[1].correspondences,name))
    np.testing.assert_array_equal(truths[0].flow,truths[1].flow)


def test_densified_material_pairs_follow_exact_camera_sample_rows():
    source=SyntheticLabSimulator(seed=17,config={'lidar_points':900,'width':80,'height':50})
    try:
        source.next_frame();frame=source.next_frame();truth=source.last_ground_truth
        points=frame.lidar_points.copy();points[:,:3]=transform_points(truth.pose_world,points[:,:3])
        runtime,_=fused_observations(frame,points,stride=5)
        training,fused=fused_observations(frame,points,truth,stride=5)
        np.testing.assert_array_equal(runtime,training)
        yy,xx=np.indices(frame.depth.shape)
        mask=(frame.depth>=.2)&(frame.depth<=6)&(yy%5==0)&(xx%5==0)
        assert len(fused.correspondences.valid)==len(training)
        count=len(points)
        for name in ('current_world','previous_world','timestamps','previous_timestamps','valid'):
            np.testing.assert_array_equal(getattr(fused.correspondences,name)[:count],getattr(truth.correspondences,name))
            np.testing.assert_array_equal(getattr(fused.correspondences,name)[count:],getattr(truth.camera_correspondences,name)[mask.ravel()])
    finally:source.close()

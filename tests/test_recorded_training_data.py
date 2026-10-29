"""Real simulated sensor recordings exercise the lab-annotation I/O contract.

Simulator truth is written as annotation sidecars ONLY for these tests. The
dataset loader sees exactly the same recording/annotation interface as a lab
dataset and never invokes a simulator or manufactures target labels.
"""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json
import shutil
import numpy as np
import pytest
import yaml
from core.se3_math import transform_points
from ingestion.recorder_replayer import FrameRecorder
from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
from perception_ml.recorded_dataset import load_recorded_dataset,correspondence_sidecar_arrays


ROOT=Path(__file__).resolve().parents[1]


def write_fixture(directory,mobile=False,missing_imu=False,partial_camera=False):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    config=yaml.safe_load((ROOT/"config/system_config.yaml").read_text())
    config["simulation"].update(lidar_points=800,width=80,height=50)
    source=SyntheticLabSimulator(config["seed"],config,mobile=mobile)
    recording=directory/"recording";labels=directory/"labels";labels.mkdir()
    frames=[];truths=[]
    try:
        with FrameRecorder(recording,chunk_size=1) as recorder:
            for _ in range(3):
                frame=source.next_frame();truth=source.last_ground_truth
                if missing_imu:frame=replace(frame,imu_samples=())
                if partial_camera:frame=replace(frame,calibration=None)
                recorder.write(frame);frames.append(frame);truths.append(truth)
                payload={key:getattr(truth,key) for key in ("semantic","flow","instance_ids","flow_valid",
                    "camera_semantic","camera_flow","camera_instance_ids","camera_flow_valid","pose_world")}
                payload.update(frame_id=np.array(frame.frame_id),timestamp=np.array(frame.timestamp),
                    camera_timestamp=np.array(frame.camera_timestamp),flow_frame=np.array("world"),
                    flow_direction=np.array("past_to_current"))
                np.savez_compressed(labels/f"{frame.frame_id:06d}.npz",**payload)
    finally:source.close()
    twin=directory/"twin";twin.mkdir()
    shutil.copy2(ROOT/f"assets/twin_seed{config['seed']}/measured_map.npz",twin/"measured_map.npz")
    manifest={"schema_version":1,"splits":{"train":[{"scene_id":"lab-a","recording":"recording",
        "labels":"labels","twin":"twin","mobile":mobile}],"validation":[],"test":[]}}
    path=directory/"dataset.json";path.write_text(json.dumps(manifest),encoding="utf-8")
    return path,config,frames,truths


def rewrite_labels(path,change):
    with np.load(path,allow_pickle=False) as source: contents={key:source[key].copy() for key in source.files}
    change(contents)
    np.savez_compressed(path,**contents)


def test_recorded_dataset_streams_and_restarts_with_aligned_camera_targets(tmp_path):
    path,config,frames,truths=write_fixture(tmp_path)
    splits=load_recorded_dataset(path,config)
    assert set(splits)=={"train","validation","test"} and not splits["validation"] and not splits["test"]
    scene_id,tree,sequence=splits["train"][0]
    assert scene_id=="lab-a" and len(sequence)==3
    assert not hasattr(sequence,"frames") and not hasattr(sequence,"buffer")
    first=list(sequence);second=list(sequence)
    for raw,raw_truth,(frame,truth,points),(_,again,points_again) in zip(frames,truths,first,second):
        np.testing.assert_allclose(points,points_again,atol=1e-7)
        np.testing.assert_array_equal(truth.semantic,again.semantic)
        assert len(points)==len(truth.semantic)==len(truth.flow)==len(truth.instance_ids)==len(truth.flow_valid)
        assert len(points)>len(raw.lidar_points)
        assert truth.camera_semantic.shape==raw.depth.shape
        np.testing.assert_array_equal(truth.semantic[:len(raw.lidar_points)],raw_truth.semantic)
        np.testing.assert_allclose(truth.flow[:len(raw.lidar_points)],raw_truth.flow@sequence.floor_transform[:3,:3].T,atol=1e-7)
        expected=transform_points(sequence.floor_transform@raw.sensor_pose_world,raw.lidar_points[:,:3])
        np.testing.assert_allclose(points[:len(raw.lidar_points),:3],expected,atol=2e-6)
        assert np.isfinite(tree.query(points[:,:3])[0]).all()
        assert not hasattr(frame,"semantic") and not hasattr(frame,"instance_ids")


def test_mobile_recorded_localization_ignores_label_debug_pose(tmp_path):
    path,config,_,_=write_fixture(tmp_path,mobile=True)
    sequence=load_recorded_dataset(path,config)["train"][0][2]
    baseline=list(sequence)
    for sidecar in (tmp_path/"labels").glob("*.npz"):
        def poison(values):
            values["pose_world"][:3,3]=[1000.,2000.,3000.]
        rewrite_labels(sidecar,poison)
    changed=list(sequence)
    for (frame,_,points),(other,truth,other_points) in zip(baseline,changed):
        np.testing.assert_allclose(frame.sensor_pose_world,other.sensor_pose_world,atol=1e-10)
        np.testing.assert_allclose(points,other_points,atol=1e-7)
        assert np.linalg.norm(other.sensor_pose_world[:3,3])<20
        assert np.linalg.norm(truth.pose_world[:3,3])>1000


@pytest.mark.parametrize("leakage",["scene_id","recording"])
def test_split_leakage_rejected_for_ids_and_canonical_recordings(tmp_path,leakage):
    path,config,_,_=write_fixture(tmp_path)
    manifest=json.loads(path.read_text());entry=dict(manifest["splits"]["train"][0])
    if leakage=="scene_id":
        shutil.copytree(tmp_path/"recording",tmp_path/"recording_b")
        entry["recording"]="recording_b"
    else:
        entry["scene_id"]="different-id";entry["recording"]="recording/../recording"
    manifest["splits"]["validation"]=[entry];path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match="leakage"):load_recorded_dataset(path,config)


@pytest.mark.parametrize("malformation",["shape","class","timestamp","frame_id","camera_field","camera_semantic","flow_direction","nonfinite"])
def test_malformed_annotation_sidecars_are_rejected(tmp_path,malformation):
    path,config,_,_=write_fixture(tmp_path)
    def change(values):
        if malformation=="shape":values["flow"]=values["flow"][:-1]
        elif malformation=="class":values["semantic"][0]=7
        elif malformation=="timestamp":values["timestamp"]=np.array(123.)
        elif malformation=="frame_id":values["frame_id"]=np.array(999)
        elif malformation=="camera_field":del values["camera_flow_valid"]
        elif malformation=="camera_semantic":values["camera_semantic"][:]=-1
        elif malformation=="flow_direction":values["flow_direction"]=np.array("current_to_past")
        elif malformation=="nonfinite":values["flow"][0,0]=np.nan
    rewrite_labels(tmp_path/"labels/000000.npz",change)
    sequence=load_recorded_dataset(path,config)["train"][0][2]
    with pytest.raises(ValueError):next(iter(sequence))


def test_missing_labels_are_reported_lazily_without_reading_whole_recording(tmp_path):
    path,config,_,_=write_fixture(tmp_path)
    (tmp_path/"labels/000001.npz").unlink()
    sequence=load_recorded_dataset(path,config)["train"][0][2]
    iterator=iter(sequence)
    frame,_,_=next(iterator)
    assert frame.frame_id==0
    with pytest.raises(FileNotFoundError,match="000001"):next(iterator)


@pytest.mark.parametrize("missing",["imu","camera"])
def test_recordings_missing_required_sensor_data_fail(tmp_path,missing):
    path,config,_,_=write_fixture(tmp_path,missing_imu=missing=="imu",partial_camera=missing=="camera")
    sequence=load_recorded_dataset(path,config)["train"][0][2]
    with pytest.raises(ValueError):next(iter(sequence))


def test_dataset_rejects_missing_measured_map_and_unsupported_version(tmp_path):
    path,config,_,_=write_fixture(tmp_path)
    (tmp_path/"twin/measured_map.npz").unlink()
    with pytest.raises(FileNotFoundError,match="measured map"):load_recorded_dataset(path,config)
    manifest=json.loads(path.read_text());manifest["schema_version"]=2;path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match="schema_version"):load_recorded_dataset(path,config)


def test_truncated_recording_manifest_count_is_not_silently_accepted(tmp_path):
    path,config,_,_=write_fixture(tmp_path)
    recording_manifest=tmp_path/"recording/manifest.json"
    content=json.loads(recording_manifest.read_text());content["frame_count"]=4
    recording_manifest.write_text(json.dumps(content))
    # Manifest validation rejects the truncated bundle before exposing a dataset.
    with pytest.raises(ValueError,match="frame count mismatch"):
        load_recorded_dataset(path,config)


def test_configured_dense_stride_preserves_annotation_correspondence(tmp_path):
    path,config,frames,truths=write_fixture(tmp_path)
    config['perception']['rgbd_stride']=5
    sequence=load_recorded_dataset(path,config)['train'][0][2]
    frame,truth,points=next(iter(sequence))
    yy,xx=np.indices(frames[0].depth.shape)
    sampled=(yy%5==0)&(xx%5==0)&(frames[0].depth>=.2)&(frames[0].depth<=6)
    assert sequence.rgbd_stride==5
    assert len(points)==len(frames[0].lidar_points)+int(sampled.sum())
    np.testing.assert_array_equal(truth.semantic[len(frames[0].lidar_points):],truths[0].camera_semantic[sampled])
    config['perception']['rgbd_stride']=0
    with pytest.raises(ValueError,match='positive integer'):load_recorded_dataset(path,config)


def test_mobile_velocity_uses_capture_cadence_not_shortened_sweep_width(tmp_path,monkeypatch):
    """A genuine partial scan retains all IMU coverage across a capture gap."""
    import perception_ml.recorded_dataset as datasets
    path,config,frames,_=write_fixture(tmp_path,mobile=True)
    second=frames[1];trim=.02
    keep=second.lidar_points[:,6]>=trim
    points=second.lidar_points[keep].copy();points[:,6]-=trim
    cropped=replace(second,timestamp=second.timestamp+trim,sweep_duration=second.sweep_duration-trim,
        lidar_points=points,beam_ids=second.beam_ids[keep],
        lidar_origins=None if second.lidar_origins is None else second.lidar_origins[keep],
        raw_timestamps={**second.raw_timestamps,'lidar':second.timestamp+trim})
    # End-time RGB-D observations and physical returns stay unchanged. Only
    # early LiDAR returns were unavailable; labels follow those same rows.
    recording=tmp_path/'partial_recording'
    with FrameRecorder(recording,chunk_size=1) as recorder:
        recorder.write(frames[0]);recorder.write(cropped)
    def trim_labels(values):
        for name in ('semantic','flow','instance_ids','flow_valid'):values[name]=values[name][keep]
        values['timestamp']=np.array(cropped.timestamp)
    rewrite_labels(tmp_path/'labels/000001.npz',trim_labels)
    manifest=json.loads(path.read_text());manifest['splits']['train'][0]['recording']='partial_recording'
    path.write_text(json.dumps(manifest))
    observed=[];original=datasets.ContinuousTimeIMUDeskewer
    class ObservedDeskewer(original):
        def correct_state(self,pose_world,velocity=None):
            observed.append((pose_world.copy(),np.array(velocity),self.last_timestamp))
            return super().correct_state(pose_world,velocity)
    monkeypatch.setattr(datasets,'ContinuousTimeIMUDeskewer',ObservedDeskewer)
    sequence=load_recorded_dataset(path,config)['train'][0][2]
    assert len(list(sequence))==2 and len(observed)==2
    first,last=observed
    elapsed=last[2]-first[2]
    assert elapsed==pytest.approx(.1) and cropped.sweep_duration==pytest.approx(.08)
    expected=(last[0][:3,3]-first[0][:3,3])/elapsed
    np.testing.assert_allclose(last[1],expected,atol=1e-12)
    assert np.linalg.norm(last[1]-(last[0][:3,3]-first[0][:3,3])/cropped.sweep_duration)>1e-5


def test_optional_material_pair_sidecars_preserve_floor_frame_and_pixel_alignment(tmp_path):
    path,config,_,truths=write_fixture(tmp_path)
    for index,truth in enumerate(truths):
        rewrite_labels(tmp_path/f'labels/{index:06d}.npz',lambda fields:fields.update(correspondence_sidecar_arrays(truth)))
    sequence=load_recorded_dataset(path,config)['train'][0][2]
    for raw,(_,truth,points) in zip(truths,sequence):
        assert len(truth.correspondences.valid)==len(points)
        count=len(raw.semantic)
        np.testing.assert_allclose(truth.correspondences.current_world[:count],
            transform_points(sequence.floor_transform,raw.correspondences.current_world),atol=1e-12)
        np.testing.assert_allclose(truth.correspondences.previous_world[:count],
            transform_points(sequence.floor_transform,raw.correspondences.previous_world),atol=1e-12)
        np.testing.assert_array_equal(truth.correspondences.valid[:count],raw.correspondences.valid)
    rewrite_labels(tmp_path/'labels/000000.npz',lambda fields:fields.pop('correspondence_valid'))
    with pytest.raises(ValueError,match='five material-pair arrays'):next(iter(sequence))

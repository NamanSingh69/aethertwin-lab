"""Restartable supervised datasets from recorded sensors and human label sidecars.

Version-1 manifest (paths are relative to the manifest file)::

    {"schema_version": 1, "splits": {
      "train": [{"scene_id": "lab-session-a", "recording": "recordings/a",
                 "labels": "labels/a", "twin": "measured-twin", "mobile": false}],
      "validation": [], "test": []}}

Each ``labels/<frame_id:06d>.npz`` contains semantic(N,), flow(N,3),
instance_ids(N,), flow_valid(N,). RGB-D recordings additionally require
camera_semantic(H,W), camera_flow(H,W,3), camera_instance_ids(H,W), and
camera_flow_valid(H,W). Flow is displacement in metres from the previous to the
current material-point position, expressed in the recording's WORLD frame
(the same world as sensor_pose_world), before the twin's floor alignment.

Optional scalar frame_id, timestamp (sweep START), camera_timestamp, flow_frame
('world'), and flow_direction ('past_to_current') are checked when supplied.
Optional pose_world is debugging ground truth only: it never initializes or
corrects deskew/localization. There are no pseudo-labels or class fallbacks.
Optional correspondence_* and camera_correspondence_* arrays preserve exact
material-point pairs for analysis; they are never inputs to learned inference.

The loader retains a measured point map/KD-tree and streams a single replay
chunk/frame and its labels. Reiterating a scene resets all causal pose state.
"""
from __future__ import annotations
from dataclasses import dataclass,replace
from pathlib import Path
import json
import os
import numpy as np
from scipy.spatial import cKDTree
from core.contracts import GroundTruthFrame,MaterialCorrespondences
from core.se3_math import inverse,transform_points
from ingestion.recorder_replayer import ReplaySource
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
from slam_and_twin.icp_pose_graph import ICPPoseGraph
from .inference_engine import fused_observations


def _integer_array(value,name,shape,minimum,maximum=None):
    result=np.asarray(value)
    if result.shape!=shape or not np.issubdtype(result.dtype,np.number) or not np.isfinite(result).all():
        raise ValueError(f"{name} must be finite with shape {shape}")
    upper=np.iinfo(np.int64).max if maximum is None else maximum
    if np.any(result!=np.round(result)) or np.any(result<minimum) or np.any(result>upper):
        raise ValueError(f"{name} contains invalid integer labels")
    return result.astype(np.int64,copy=True)


def _flow_array(value,name,shape):
    result=np.asarray(value)
    if result.shape!=shape or not np.issubdtype(result.dtype,np.number) or not np.isfinite(result).all():
        raise ValueError(f"{name} must be finite with shape {shape}")
    return result.astype(np.float32,copy=True)


def _valid_array(value,name,shape):
    result=np.asarray(value)
    if result.shape!=shape or not np.all(np.isin(result,(0,1))):
        raise ValueError(f"{name} must be a boolean/0-1 mask with shape {shape}")
    return result.astype(bool,copy=True)


def _scalar(data,key):
    result=np.asarray(data[key])
    if result.shape!=(): raise ValueError(f"Label metadata {key} must be scalar")
    return result.item()


_PAIR_FIELDS=('current_world','previous_world','timestamps','previous_timestamps','valid')


def correspondence_sidecar_arrays(truth):
    """Flatten optional GT-only material pairs into safe numeric NPZ fields."""
    output={}
    for prefix,pairs in (('correspondence',truth.correspondences),('camera_correspondence',truth.camera_correspondences)):
        if pairs is not None:
            output.update({f'{prefix}_{name}':getattr(pairs,name) for name in _PAIR_FIELDS})
    return output


def _read_correspondences(data,prefix,count,floor_transform):
    present=[f'{prefix}_{name}' in data for name in _PAIR_FIELDS]
    if not any(present):return None
    if not all(present):raise ValueError(f'{prefix} requires all five material-pair arrays')
    pairs=MaterialCorrespondences(**{name:data[f'{prefix}_{name}'] for name in _PAIR_FIELDS})
    if len(pairs.valid)!=count:raise ValueError(f'{prefix} material pairs must align with observations')
    return replace(pairs,current_world=transform_points(floor_transform,pairs.current_world),
                   previous_world=transform_points(floor_transform,pairs.previous_world))


def _read_labels(path,frame,floor_transform,estimated_pose):
    if not path.is_file(): raise FileNotFoundError(f"Missing supervised label sidecar: {path}")
    required={"semantic","flow","instance_ids","flow_valid"}
    has_camera=any(value is not None for value in (frame.rgb,frame.depth,frame.calibration))
    if has_camera:
        if frame.rgb is None or frame.depth is None or frame.calibration is None or frame.camera_timestamp is None:
            raise ValueError("Recorded RGB-D supervision requires aligned RGB, depth, calibration and camera timestamp")
        required.update(("camera_semantic","camera_flow","camera_instance_ids","camera_flow_valid"))
    with np.load(path,allow_pickle=False) as data:
        missing=required-set(data.files)
        if missing: raise ValueError(f"Missing label arrays in {path.name}: {', '.join(sorted(missing))}")
        if "frame_id" in data and _scalar(data,"frame_id")!=frame.frame_id:
            raise ValueError(f"Sidecar frame_id does not match recorded frame {frame.frame_id}")
        if "timestamp" in data and not np.isclose(float(_scalar(data,"timestamp")),frame.timestamp,atol=1e-6,rtol=0):
            raise ValueError(f"Sidecar timestamp does not match recorded frame {frame.frame_id}")
        if "camera_timestamp" in data:
            if frame.camera_timestamp is None or not np.isclose(float(_scalar(data,"camera_timestamp")),frame.camera_timestamp,atol=1e-6,rtol=0):
                raise ValueError("Sidecar camera timestamp does not match recorded image")
        for name,expected in (("flow_frame","world"),("flow_direction","past_to_current")):
            if name in data and _scalar(data,name)!=expected: raise ValueError(f"{name} must be {expected!r}")
        count=len(frame.lidar_points)
        semantic=_integer_array(data["semantic"],"semantic",(count,),0,6)
        flow=_flow_array(data["flow"],"flow",(count,3))
        instances=_integer_array(data["instance_ids"],"instance_ids",(count,),-1)
        valid=_valid_array(data["flow_valid"],"flow_valid",(count,))
        R=floor_transform[:3,:3]
        kwargs={'correspondences':_read_correspondences(data,'correspondence',count,floor_transform)}
        if has_camera:
            shape=frame.depth.shape
            camera_semantic=_integer_array(data["camera_semantic"],"camera_semantic",shape,-1,6)
            image_valid=np.isfinite(frame.depth)&(frame.depth>=.2)&(frame.depth<=6.)
            if np.any(camera_semantic[image_valid]<0):
                raise ValueError("Every valid RGB-D sample needs a semantic annotation; no pseudo-label fallback")
            camera_flow=_flow_array(data["camera_flow"],"camera_flow",(*shape,3))
            camera_instances=_integer_array(data["camera_instance_ids"],"camera_instance_ids",shape,-1)
            camera_valid=_valid_array(data["camera_flow_valid"],"camera_flow_valid",shape)
            kwargs.update(camera_semantic=camera_semantic,camera_flow=camera_flow@R.T,
                        camera_instance_ids=camera_instances,camera_flow_valid=camera_valid,
                        camera_correspondences=_read_correspondences(data,'camera_correspondence',frame.depth.size,floor_transform))
        debug_pose=estimated_pose
        if "pose_world" in data:
            pose=np.asarray(data["pose_world"],dtype=float)
            if pose.shape!=(4,4) or not np.isfinite(pose).all(): raise ValueError("Debug pose_world requires a finite 4x4 matrix")
            debug_pose=floor_transform@pose
        return GroundTruthFrame(semantic,flow@R.T,instances,valid,debug_pose,**kwargs)


@dataclass(frozen=True)
class RecordedSequence:
    """A fresh bounded-memory replay/localization iterator for each epoch."""
    recording: Path
    labels: Path
    floor_transform: np.ndarray
    measured_points: np.ndarray
    mobile: bool=False
    frame_count: int=0
    rgbd_stride: int=3

    def __len__(self): return self.frame_count

    def __iter__(self):
        replay=ReplaySource(self.recording);deskewer=None;previous_pose=None;previous_pose_time=None;previous_id=None;previous_time=None
        consumed=0
        reference=transform_points(inverse(self.floor_transform),self.measured_points) if self.mobile else None
        odometry=ICPPoseGraph(reference_points=reference) if self.mobile else None
        try:
            for index,raw in enumerate(replay):
                consumed+=1
                if previous_id is not None and (raw.frame_id<=previous_id or raw.timestamp<=previous_time):
                    raise ValueError("Training recordings require increasing frame IDs and capture times")
                previous_id=raw.frame_id;previous_time=raw.timestamp
                if deskewer is None:
                    deskewer=ContinuousTimeIMUDeskewer(initial_pose=raw.sensor_pose_world,imu_to_lidar=raw.imu_to_lidar)
                frame=deskewer.deskew_frame(raw);pose=frame.sensor_pose_world
                if self.mobile:
                    pose=odometry.add_scan(frame.lidar_points[:,:3],pose)
                    if index and index%20==0:pose=odometry.optimize()[-1]
                    pose_time=raw.timestamp+raw.sweep_duration
                    velocity=(pose[:3,3]-previous_pose[:3,3])/(pose_time-previous_pose_time) if previous_pose is not None else deskewer.velocity
                    deskewer.correct_state(pose,velocity);previous_pose=pose.copy();previous_pose_time=pose_time
                localized_pose=self.floor_transform@pose
                frame=replace(frame,sensor_pose_world=localized_pose)
                # Reading sidecars happens AFTER localization: debug target poses
                # cannot affect the input geometry, even accidentally.
                truth=_read_labels(self.labels/f"{raw.frame_id:06d}.npz",raw,self.floor_transform,localized_pose)
                points=frame.lidar_points.copy();points[:,:3]=transform_points(localized_pose,points[:,:3])
                points,truth=fused_observations(frame,points,truth,stride=self.rgbd_stride)
                yield frame,truth,points
            if consumed!=self.frame_count:
                raise ValueError(f"Recording frame count mismatch: expected {self.frame_count}, read {consumed}")
        finally:
            replay.close()


def _relative_directory(base,value,name):
    if not isinstance(value,str) or not value: raise ValueError(f"Dataset entry needs a nonempty relative {name} path")
    path=Path(value)
    if path.is_absolute(): raise ValueError(f"Dataset {name} must be relative to its manifest")
    resolved=(base/path).resolve()
    if not resolved.is_dir(): raise FileNotFoundError(f"Dataset {name} directory does not exist: {resolved}")
    return resolved


def load_recorded_dataset(manifest_path,config):
    """Load scene metadata/KD-trees, returning train/validation/test sequences.

    Each result entry is ``(scene_id: str, measured_tree, RecordedSequence)``.
    Frame/label contents are validated lazily while streaming. Scene IDs and
    canonical recording directories are unique across the entire manifest.
    ``config`` shares the training entry-point interface; sensor calibration and
    localization always come from observations and their measured twin bundle.
    """
    rgbd_stride=config.get('perception',{}).get('rgbd_stride',3)
    if isinstance(rgbd_stride,bool) or not isinstance(rgbd_stride,(int,np.integer)) or rgbd_stride<1:
        raise ValueError('RGB-D fusion stride must be a positive integer')
    manifest_path=Path(manifest_path).resolve()
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version")!=1: raise ValueError("Unsupported supervised dataset schema_version")
    splits=manifest.get("splits")
    if not isinstance(splits,dict) or any(name not in ("train","validation","test") for name in splits):
        raise ValueError("Dataset splits must contain train, validation and/or test lists")
    result={name:[] for name in ("train","validation","test")}
    scene_ids=set();recording_paths=set();maps={};entries=[]
    for split in result:
        scenes=splits.get(split,[])
        if not isinstance(scenes,list): raise ValueError(f"{split} split must be a list")
        for entry in scenes:
            if not isinstance(entry,dict): raise ValueError("Dataset scene entries must be objects")
            identifier=entry.get("scene_id")
            if isinstance(identifier,bool) or not isinstance(identifier,(str,int)) or str(identifier).strip()=="":
                raise ValueError("Dataset scene_id must be a nonempty string or integer")
            scene_id=str(identifier)
            recording=_relative_directory(manifest_path.parent,entry.get("recording"),"recording")
            canonical=os.path.normcase(str(recording))
            if scene_id in scene_ids or canonical in recording_paths:
                raise ValueError("Dataset split leakage: duplicate scene ID or resolved recording path")
            scene_ids.add(scene_id);recording_paths.add(canonical)
            labels=_relative_directory(manifest_path.parent,entry.get("labels"),"labels")
            twin=_relative_directory(manifest_path.parent,entry.get("twin"),"twin")
            mobile=entry.get("mobile",False)
            if not isinstance(mobile,bool): raise ValueError("Dataset mobile must be boolean")
            entries.append((split,scene_id,recording,labels,twin,mobile))
    for split,scene_id,recording,labels,twin,mobile in entries:
        replay=ReplaySource(recording)
        count=len(replay);replay.close()
        if count<1: raise ValueError(f"Recorded scene {scene_id} is empty")
        if twin not in maps:
            source=twin/"measured_map.npz"
            if not source.is_file(): raise FileNotFoundError(f"Missing commissioned measured map: {source}")
            with np.load(source,allow_pickle=False) as data:
                points=np.array(data["points"],dtype=float,copy=True)
                floor=np.array(data["floor_transform"],dtype=float,copy=True)
            if points.ndim!=2 or points.shape[1]!=3 or len(points)<6 or not np.isfinite(points).all():
                raise ValueError("Dataset twin requires at least six finite measured XYZ points")
            if floor.shape!=(4,4) or not np.isfinite(floor).all() or not np.allclose(floor[3],[0,0,0,1]):
                raise ValueError("Invalid measured floor transform")
            R=floor[:3,:3]
            if not np.allclose(R.T@R,np.eye(3),atol=1e-5) or np.linalg.det(R)<.999:
                raise ValueError("Measured floor transform rotation is not SO(3)")
            points.setflags(write=False);floor.setflags(write=False)
            maps[twin]=(points,floor,cKDTree(points))
        points,floor,tree=maps[twin]
        result[split].append((scene_id,tree,RecordedSequence(recording,labels,floor,points,mobile,count,int(rgbd_stride))))
    return result

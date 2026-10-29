"""Boundary objects. World: right handed, z up; camera: x right/y down/z forward.

Times are float64 seconds on a common monotonic epoch; raw device timestamps are
retained separately. LiDAR columns: xyz[m], intensity[0,1], return index/count,
and acquisition offset[s] from sweep start. Flow is past-to-current displacement
in WORLD coordinates, attached to current observations, not velocity.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional
import numpy as np


class SemanticClass(IntEnum):
    TRAVERSABLE_GROUND = 0
    STATIC_STRUCTURE = 1
    HUMAN_PEDESTRIAN = 2
    DYNAMIC_ROBOT_CART = 3
    NEW_STATIC_OBSTACLE = 4
    THIN_WIRE_CABLE = 5
    PERMEABLE_FOLIAGE = 6


PALETTE = np.array([[.18,.30,.36],[0,.71,.85],[0,.96,.83],
                    [.61,.36,.90],[1,.89,.25],[.80,1,.20],[.95,.36,.71]], np.float32)


class FrozenDict(dict):
    """JSON-compatible immutable mapping for cross-thread diagnostic snapshots."""
    def _immutable(self,*args,**kwargs):raise TypeError('Snapshot mappings are immutable')
    __setitem__=__delitem__=clear=pop=popitem=setdefault=update=__ior__=_immutable


def freeze(value):
    if isinstance(value,dict):return FrozenDict({key:freeze(item) for key,item in value.items()})
    if isinstance(value,(list,tuple)):return tuple(freeze(item) for item in value)
    if isinstance(value,np.ndarray):return array(value,dtype=value.dtype)
    return value


def array(value, shape=None, dtype=np.float32):
    out = np.array(value, dtype=dtype, copy=True)
    if shape is not None and (out.ndim != len(shape) or any(s is not None and s != n for s,n in zip(shape,out.shape))):
        raise ValueError(f"Expected shape {shape}, got {out.shape}")
    if not np.isfinite(out).all():
        raise ValueError("Non-finite array at pipeline boundary")
    out.setflags(write=False)
    return out


def rigid_array(value):
    out=array(value,(4,4),np.float64)
    R=out[:3,:3]
    if not np.allclose(out[3],[0,0,0,1],atol=1e-8) or not np.allclose(R.T@R,np.eye(3),atol=1e-5) or np.linalg.det(R)<.999:
        raise ValueError('Boundary transform must be SE(3), without scale, shear or reflection')
    return out


def label_array(value,shape,minimum=0,maximum=6):
    raw=np.asarray(value)
    if not np.issubdtype(raw.dtype,np.number) or not np.isfinite(raw).all() or np.any(raw!=np.round(raw)) or np.any(raw<minimum) or (maximum is not None and np.any(raw>maximum)):
        raise ValueError('Labels must contain valid integer identities/classes')
    return array(raw,shape,np.int64)


def mask_array(value,shape):
    if not np.all(np.isin(value,[False,True])):raise ValueError('Validity masks must contain boolean or 0/1 values')
    return array(value,shape,bool)


@dataclass(frozen=True)
class SensorCapabilities:
    sensor_id: str = "synthetic"
    multi_return: bool = False
    per_point_time: bool = True
    imu: bool = True
    hardware_sync: bool = False


@dataclass(frozen=True)
class IMUSample:
    timestamp: float
    gyro: np.ndarray
    accel: np.ndarray
    def __post_init__(self):
        if not np.isfinite(self.timestamp):raise ValueError('IMU timestamp must be finite')
        object.__setattr__(self, "gyro", array(self.gyro,(3,),np.float64))
        object.__setattr__(self, "accel", array(self.accel,(3,),np.float64))


@dataclass(frozen=True)
class CameraCalibration:
    K: np.ndarray
    T_lidar_camera: np.ndarray
    distortion: np.ndarray = field(default_factory=lambda: np.zeros(5))
    calibration_id: str = "simulation-v1"
    measured: bool = False
    def __post_init__(self):
        for name,shape in (("K",(3,3)),("T_lidar_camera",(4,4)),("distortion",(5,))):
            object.__setattr__(self,name,array(getattr(self,name),shape,np.float64))
        R = self.T_lidar_camera[:3,:3]
        if not np.allclose(R.T@R,np.eye(3),atol=1e-5) or np.linalg.det(R)<.999:
            raise ValueError("Calibration rotation is not SO(3)")
        if not np.allclose(self.T_lidar_camera[3],[0,0,0,1]) or min(self.K[0,0],self.K[1,1])<=0:
            raise ValueError("Invalid calibration")


@dataclass(frozen=True)
class MultiModalSensorFrame:
    frame_id: int
    timestamp: float  # sweep start; endpoint = timestamp + sweep_duration
    lidar_points: np.ndarray
    imu_samples: tuple[IMUSample,...] = ()
    painted_features: Optional[np.ndarray] = None
    gemini_rgbd_cloud: Optional[np.ndarray] = None
    rplidar_s2_points: Optional[np.ndarray] = None
    sensor_pose_world: np.ndarray = field(default_factory=lambda: np.eye(4))
    rgb: Optional[np.ndarray] = None
    depth: Optional[np.ndarray] = None
    calibration: Optional[CameraCalibration] = None
    camera_timestamp: Optional[float] = None
    beam_ids: Optional[np.ndarray] = None
    return_metadata_valid: bool = False
    capabilities: SensorCapabilities = field(default_factory=SensorCapabilities)
    raw_timestamps: dict = field(default_factory=dict)
    sweep_duration: float = .1
    diagnostics: dict = field(default_factory=dict)
    imu_to_lidar: np.ndarray = field(default_factory=lambda: np.eye(4))
    rplidar_origin_lidar: np.ndarray = field(default_factory=lambda: np.zeros(3))
    rplidar_timestamp: Optional[float] = None
    # Acquisition origins in the same reference coordinates as lidar_points.
    # None means the raw LiDAR origin is [0,0,0] at each point's timestamp.
    # Deskewed snapshots declare diagnostics.coordinate_reference=lidar_sweep_end;
    # they are recordable for analysis, but must not be deskewed a second time.
    lidar_origins: Optional[np.ndarray] = None
    # Effective T_L(reference)_C(capture); measured rigid calibration is retained.
    camera_pose_in_lidar: Optional[np.ndarray] = None
    def __post_init__(self):
        object.__setattr__(self,"lidar_points",array(self.lidar_points,(None,7)))
        object.__setattr__(self,"sensor_pose_world",rigid_array(self.sensor_pose_world))
        object.__setattr__(self,"imu_to_lidar",rigid_array(self.imu_to_lidar))
        object.__setattr__(self,"rplidar_origin_lidar",array(self.rplidar_origin_lidar,(3,),np.float64))
        if self.lidar_origins is not None:
            object.__setattr__(self,"lidar_origins",array(self.lidar_origins,(len(self.lidar_points),3),np.float64))
        if self.camera_pose_in_lidar is not None:
            pose=array(self.camera_pose_in_lidar,(4,4),np.float64)
            if not np.allclose(pose[3],[0,0,0,1]) or not np.allclose(pose[:3,:3].T@pose[:3,:3],np.eye(3),atol=1e-5) or np.linalg.det(pose[:3,:3])<.999:
                raise ValueError('Effective camera pose must be SE(3)')
            object.__setattr__(self,'camera_pose_in_lidar',pose)
        if any(value is not None and not np.isfinite(value) for value in (self.camera_timestamp,self.rplidar_timestamp)):
            raise ValueError('Auxiliary sensor timestamps must be finite')
        object.__setattr__(self,"imu_samples",tuple(self.imu_samples))
        if self.sweep_duration<=0 or not np.isfinite([self.timestamp,self.sweep_duration]).all():
            raise ValueError("Invalid sensor time")
        for name,dtype in (("rgb",np.uint8),("depth",np.float32),("beam_ids",np.int64),
                           ("painted_features",np.float32),("gemini_rgbd_cloud",np.float32),("rplidar_s2_points",np.float32)):
            value=getattr(self,name)
            if value is not None: object.__setattr__(self,name,array(value,dtype=dtype))
        if self.beam_ids is not None and self.beam_ids.shape!=(len(self.lidar_points),):
            raise ValueError("beam_ids must align with LiDAR points")
        if self.return_metadata_valid and len(self.lidar_points):
            r=self.lidar_points[:,4:6]
            if np.any(r<1) or np.any(r[:,0]>r[:,1]) or np.any(r!=np.round(r)):
                raise ValueError("Invalid echo index/count")
        if self.depth is not None and self.rgb is not None and self.depth.shape!=self.rgb.shape[:2]:
            raise ValueError("RGB and depth must be aligned")
        if self.rgb is not None and (self.rgb.ndim!=3 or self.rgb.shape[2]!=3):raise ValueError('RGB requires shape (H,W,3)')
        if self.depth is not None and (self.depth.ndim!=2 or np.any(self.depth<0)):raise ValueError('Depth requires a nonnegative (H,W) image in metres')
        if self.painted_features is not None and (self.painted_features.ndim!=2 or self.painted_features.shape[0]!=len(self.lidar_points) or self.painted_features.shape[1]<3):
            raise ValueError('Painted features must have shape (N,3+C) aligned with LiDAR returns')
        for name,columns in (('gemini_rgbd_cloud',6),('rplidar_s2_points',3)):
            value=getattr(self,name)
            if value is not None and (value.ndim!=2 or value.shape[1]!=columns):raise ValueError(f'{name} requires shape (N,{columns})')
        object.__setattr__(self,'raw_timestamps',freeze(self.raw_timestamps))
        object.__setattr__(self,'diagnostics',freeze(self.diagnostics))


@dataclass(frozen=True)
class MaterialCorrespondences:
    """Independent clean world-space material pairs, in current observation order.

    Camera pairs are flattened in row-major image order. A valid pair describes
    the same material location at its two capture times; it need not be sampled
    by a ray in the preceding frame. Noisy runtime returns remain separate.
    """
    current_world: np.ndarray
    previous_world: np.ndarray
    timestamps: np.ndarray
    previous_timestamps: np.ndarray
    valid: np.ndarray
    def __post_init__(self):
        n=len(self.current_world)
        for name,shape in (('current_world',(n,3)),('previous_world',(n,3)),('timestamps',(n,)),('previous_timestamps',(n,))):
            object.__setattr__(self,name,array(getattr(self,name),shape,np.float64))
        object.__setattr__(self,'valid',mask_array(self.valid,(n,)))
        if np.any(self.previous_timestamps[self.valid]>=self.timestamps[self.valid]):
            raise ValueError('Valid correspondences must refer to an earlier material position')


@dataclass(frozen=True)
class GroundTruthFrame:
    """Training/evaluation side channel; never accepted by runtime inference."""
    semantic: np.ndarray
    flow: np.ndarray
    instance_ids: np.ndarray
    flow_valid: np.ndarray
    pose_world: np.ndarray
    camera_semantic: Optional[np.ndarray] = None
    camera_flow: Optional[np.ndarray] = None
    camera_instance_ids: Optional[np.ndarray] = None
    camera_flow_valid: Optional[np.ndarray] = None
    correspondences: Optional[MaterialCorrespondences] = None
    camera_correspondences: Optional[MaterialCorrespondences] = None
    def __post_init__(self):
        n=len(self.semantic)
        object.__setattr__(self,'semantic',label_array(self.semantic,(n,)))
        object.__setattr__(self,'flow',array(self.flow,(n,3)))
        object.__setattr__(self,'instance_ids',label_array(self.instance_ids,(n,),-1,None))
        object.__setattr__(self,'flow_valid',mask_array(self.flow_valid,(n,)))
        object.__setattr__(self,'pose_world',rigid_array(self.pose_world))
        camera=(self.camera_semantic,self.camera_flow,self.camera_instance_ids,self.camera_flow_valid)
        if any(value is not None for value in camera):
            if any(value is None for value in camera):raise ValueError('Camera supervision requires labels, flow, instances and validity together')
            shape=np.asarray(self.camera_semantic).shape
            if len(shape)!=2:raise ValueError('Camera semantic labels require shape (H,W)')
            object.__setattr__(self,'camera_semantic',label_array(self.camera_semantic,shape,-1))
            object.__setattr__(self,'camera_flow',array(self.camera_flow,(*shape,3)))
            object.__setattr__(self,'camera_instance_ids',label_array(self.camera_instance_ids,shape,-1,None))
            object.__setattr__(self,'camera_flow_valid',mask_array(self.camera_flow_valid,shape))
        if self.correspondences is not None and len(self.correspondences.current_world)!=n:
            raise ValueError('LiDAR/material correspondences must align with supervised points')
        if self.camera_correspondences is not None and (self.camera_semantic is None or len(self.camera_correspondences.current_world)!=self.camera_semantic.size):
            raise ValueError('Camera correspondences must align with the flattened semantic image')


@dataclass(frozen=True)
class VoxelGridState:
    voxel_size: float
    voxel_coords: np.ndarray
    voxel_centers: np.ndarray
    voxel_features: np.ndarray  # cx cy cz intensity height_span log_density permeability log_odds RGB zmax
    log_odds: np.ndarray
    permeability: np.ndarray
    point_to_voxel_idx: np.ndarray
    def __post_init__(self):
        v=len(self.voxel_centers)
        for name,shape,dtype in (("voxel_coords",(v,3),np.int64),("voxel_centers",(v,3),np.float32),
            ("voxel_features",(v,12),np.float32),("log_odds",(v,),np.float32),("permeability",(v,),np.float32),
            ("point_to_voxel_idx",(None,),np.int64)):
            object.__setattr__(self,name,array(getattr(self,name),shape,dtype))


@dataclass(frozen=True)
class DiffedLabFrame:
    frame_id: int
    timestamp: float
    dt: float
    deskewed_full_points: np.ndarray
    foreground_points: np.ndarray
    foreground_rgb: Optional[np.ndarray]
    voxel_state: VoxelGridState
    floor_grid_points: np.ndarray
    gemini_inlay_points: Optional[np.ndarray] = None
    def __post_init__(self):
        if not np.isfinite([self.timestamp,self.dt]).all() or self.dt<=0:raise ValueError('Diffed frame requires finite time and positive dt')
        for name,columns in (('deskewed_full_points',7),('foreground_points',7),('floor_grid_points',3),('gemini_inlay_points',6)):
            value=getattr(self,name)
            if value is not None:object.__setattr__(self,name,array(value,(None,columns)))
        if self.foreground_rgb is not None:object.__setattr__(self,'foreground_rgb',array(self.foreground_rgb,(len(self.foreground_points),3)))


@dataclass(frozen=True)
class Detection3D:
    position: np.ndarray
    size: np.ndarray
    rotation: np.ndarray
    class_probs: np.ndarray
    flow: np.ndarray
    embedding: np.ndarray
    points: np.ndarray
    eigen: np.ndarray
    flow_valid: bool = True
    flow_variance: float = .04
    def __post_init__(self):
        for name,shape in (("position",(3,)),("size",(3,)),("rotation",(3,3)),("class_probs",(7,)),
            ("flow",(3,)),("embedding",(None,)),("points",(None,3)),("eigen",(10,))):
            object.__setattr__(self,name,array(getattr(self,name),shape,np.float64))


@dataclass(frozen=True)
class TrackedEntity3D:
    track_id: int
    semantic_class: SemanticClass
    class_probs: np.ndarray
    confidence: float
    position: np.ndarray
    velocity: np.ndarray
    acceleration: np.ndarray
    scene_flow_vector: np.ndarray
    speed: float
    heading_rad: float
    bbox_min: np.ndarray
    bbox_max: np.ndarray
    obb_corners: np.ndarray
    member_points: np.ndarray
    eigen_invariants: np.ndarray
    ttc_seconds: float
    predicted_trajectory: np.ndarray
    is_breaching_geofence: bool = False
    breached_geofence_names: tuple[str,...] = ()
    covariance: Optional[np.ndarray] = None
    last_observed_timestamp: Optional[float] = None
    state_kind: str = 'unspecified'
    def __post_init__(self):
        for name,shape in (("class_probs",(7,)),("position",(3,)),("velocity",(3,)),("acceleration",(3,)),
            ("scene_flow_vector",(3,)),("bbox_min",(3,)),("bbox_max",(3,)),("obb_corners",(8,3)),
            ("member_points",(None,3)),("eigen_invariants",(10,)),("predicted_trajectory",(None,3))):
            object.__setattr__(self,name,array(getattr(self,name),shape,np.float64))
        if self.covariance is not None:object.__setattr__(self,'covariance',array(self.covariance,dtype=np.float64))
        if self.last_observed_timestamp is not None and not np.isfinite(self.last_observed_timestamp):raise ValueError('Invalid track observation timestamp')
        if self.state_kind not in ('unspecified','measurement_updated','predicted'):raise ValueError('Invalid track state kind')
        object.__setattr__(self,'breached_geofence_names',tuple(self.breached_geofence_names))


@dataclass(frozen=True)
class OrientedGeofence3D:
    name: str
    center: np.ndarray
    rotation_matrix: np.ndarray
    half_extents: np.ndarray
    severity_level: int = 2
    is_breached: bool = False
    breaching_track_ids: tuple[int,...] = ()
    def __post_init__(self):
        for name,shape in (("center",(3,)),("rotation_matrix",(3,3)),("half_extents",(3,))):
            object.__setattr__(self,name,array(getattr(self,name),shape,np.float64))
        object.__setattr__(self,'breaching_track_ids',tuple(self.breaching_track_ids))


@dataclass(frozen=True)
class PlannedTrajectory:
    status: str
    points: np.ndarray
    times: np.ndarray
    reason: str = ""
    cost: float = 0.0
    def __post_init__(self):
        object.__setattr__(self,'points',array(self.points,(None,3),np.float64))
        object.__setattr__(self,'times',array(self.times,(len(self.points),),np.float64))
        if np.any(self.times<0) or np.any(np.diff(self.times)<=0):raise ValueError('Trajectory times must be nonnegative and strictly increasing')
        if np.isnan(self.cost):raise ValueError('Trajectory cost must not be NaN')


@dataclass(frozen=True)
class TwinMasterFrameState:
    frame_id: int
    timestamp: float
    fps: float
    sensor_pose_world: np.ndarray
    entities: tuple[TrackedEntity3D,...]
    voxel_centers: np.ndarray
    voxel_colors: np.ndarray
    voxel_flow_vectors: np.ndarray
    floor_grid_points: np.ndarray
    floor_risk_values: np.ndarray
    floor_risk_colors: np.ndarray
    traffic_heatmap_2d: np.ndarray
    geofences: tuple[OrientedGeofence3D,...]
    planned_safe_path: Optional[np.ndarray]
    gemini_inlay_points: Optional[np.ndarray] = None
    active_alerts: tuple[str,...] = ()
    diagnostics: dict = field(default_factory=dict)
    trajectory_status: str = "not_requested"
    lab_id: str = "lab-default"
    rig_id: str = "rig-01"
    map_revision: str = "unversioned"
    calibration_id: str = "unavailable"
    source_mode: str = "unspecified"
    processed_at_unix: Optional[float] = None
    validity: dict = field(default_factory=dict)
    def __post_init__(self):
        if not np.isfinite([self.timestamp,self.fps]).all() or self.fps<0:raise ValueError('Snapshot time/rate must be finite and rate nonnegative')
        object.__setattr__(self,'sensor_pose_world',rigid_array(self.sensor_pose_world))
        v=len(self.voxel_centers);g=len(self.floor_grid_points)
        shapes={'voxel_centers':(v,3),'voxel_colors':(v,3),'voxel_flow_vectors':(v,3),
            'floor_grid_points':(g,3),'floor_risk_values':(g,),'floor_risk_colors':(g,3),
            'traffic_heatmap_2d':(None,None),'planned_safe_path':(None,3),'gemini_inlay_points':(None,6)}
        for name,shape in shapes.items():
            value=getattr(self,name)
            if value is not None:object.__setattr__(self,name,array(value,shape,dtype=np.float64 if name=='planned_safe_path' else np.float32))
        object.__setattr__(self,"entities",tuple(self.entities))
        object.__setattr__(self,"geofences",tuple(self.geofences))
        object.__setattr__(self,"active_alerts",tuple(self.active_alerts))
        object.__setattr__(self,"diagnostics",freeze(self.diagnostics))
        for name in ('lab_id','rig_id','map_revision','calibration_id'):
            value=getattr(self,name)
            if not isinstance(value,str) or not value or len(value)>256:raise ValueError(f'Invalid snapshot {name}')
        if self.source_mode not in ('unspecified','sim','replay','live','test'):
            raise ValueError('Invalid snapshot source mode')
        if self.processed_at_unix is not None and not np.isfinite(self.processed_at_unix):
            raise ValueError('Invalid snapshot processing wall clock')
        object.__setattr__(self,'validity',freeze(self.validity))

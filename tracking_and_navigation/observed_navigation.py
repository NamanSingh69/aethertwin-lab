"""Observation-only commissioning integration and reachable ground selections.

This module receives sensor frames, never simulator scene objects or labels.
Raw input requires an explicitly declared fixed station. Moving input must have
pointwise-deskewed endpoints/origins and a capture-time camera pose.
"""
from __future__ import annotations
import cv2
import numpy as np
from scipy.ndimage import label
from core.se3_math import transform_points
from .safe_corridor_planner import PlannerGrid,SafeCorridorPlanner


def merge_observed_occupancy(base_grid:PlannerGrid,voxel_map,threshold=.6):
    """OR occupied source voxel VOLUMES into a differently resolved grid.

    Source voxels are cubes, not points at their centers. Every positive-volume
    overlap is blocked; unknown cells elsewhere retain their original state.
    No negative evidence is inferred from an absence of dynamic returns.
    """
    occupied=base_grid.occupied.copy(); known=base_grid.known.copy()
    centers=voxel_map.occupied_points(threshold)
    if len(centers):
        half=float(voxel_map.voxel_size)/2
        epsilon=1e-10*min(float(voxel_map.voxel_size),base_grid.resolution)
        first=np.floor((centers-half-base_grid.origin+epsilon)/base_grid.resolution).astype(int)
        last=np.floor((centers+half-base_grid.origin-epsilon)/base_grid.resolution).astype(int)
        shape=np.asarray(base_grid.shape)
        for lower,upper in zip(first,last):
            if np.any(upper<0) or np.any(lower>=shape): continue
            lower=np.maximum(lower,0); upper=np.minimum(upper,shape-1)
            slices=tuple(slice(int(lo),int(hi)+1) for lo,hi in zip(lower,upper))
            occupied[slices]=True; known[slices]=True
    return PlannerGrid(base_grid.origin,base_grid.resolution,occupied,known)


def integrate_commissioning_frame(voxel_map,frame,camera_stride=1,timestamp=None,stationary=None):
    """Fuse prepared moving observations or explicitly fixed station captures.

    Depth values are optical-axis depth in metres; invalid pixels do not clear
    any space. Intermediate pixels are not filled or interpolated. Return a
    diagnostic count of actual rays, without looking at ground-truth channels.
    """
    if camera_stride<1: raise ValueError("camera_stride must be positive")
    prepared=frame.diagnostics.get('coordinate_reference')=='lidar_sweep_end'
    declared=bool(frame.diagnostics.get('commissioning_stationary',False)) or (
        bool(frame.diagnostics.get('pose_prior_valid',False)) and
        frame.diagnostics.get('pose_prior_source')=='commissioning station calibration')
    if not prepared:
        if not (stationary is True or declared):
            raise ValueError('Raw commissioning requires an explicit stationary capture declaration or pointwise deskew/localization')
        if any(np.linalg.norm(sample.gyro)>.02 for sample in frame.imu_samples):
            raise ValueError('Raw station moved during commissioning; deskew/localize before carving rays')
    elif len(frame.lidar_points) and frame.lidar_origins is None:
        raise ValueError('Deskewed commissioning requires per-return acquisition origins')
    capture=None
    if frame.depth is not None:
        if frame.calibration is None: raise ValueError('Depth integration requires camera calibration')
        if frame.camera_timestamp is None: raise ValueError('Depth integration requires a capture timestamp')
        end=frame.timestamp+frame.sweep_duration
        if not prepared and abs(frame.camera_timestamp-end)>frame.sweep_duration:
            raise ValueError('Depth image lies outside the stationary scan synchronization window')
        if prepared and frame.camera_pose_in_lidar is None and abs(frame.camera_timestamp-end)>1e-6:
            raise ValueError('Moving depth integration requires its effective capture-time camera pose')
        capture=frame.calibration.T_lidar_camera if frame.camera_pose_in_lidar is None else frame.camera_pose_in_lidar
    time=float(frame.timestamp+frame.sweep_duration if timestamp is None else timestamp)
    points=frame.lidar_points.copy()
    points[:,:3]=transform_points(frame.sensor_pose_world,points[:,:3])
    lidar_origin=frame.sensor_pose_world[:3,3] if frame.lidar_origins is None else transform_points(frame.sensor_pose_world,frame.lidar_origins)
    voxel_map.voxelize_and_update(points,timestamp=time,origin=lidar_origin,
        beam_ids=frame.beam_ids,return_metadata_valid=frame.return_metadata_valid)
    count=0
    if frame.depth is not None:
        yy,xx=np.indices(frame.depth.shape)
        selection=(yy%camera_stride==0)&(xx%camera_stride==0)&np.isfinite(frame.depth)&(frame.depth>0)
        pixels=np.column_stack([xx[selection],yy[selection]]).astype(np.float64)
        if len(pixels):
            calib=frame.calibration
            normalized=cv2.undistortPoints(pixels[:,None,:],calib.K,calib.distortion)[:,0,:]
            camera_points=np.c_[normalized,np.ones(len(normalized))]*frame.depth[selection,None]
            transform=frame.sensor_pose_world@capture
            depth_points=np.zeros((len(camera_points),7),np.float32)
            depth_points[:,:3]=transform_points(transform,camera_points)
            depth_points[:,3]=.5; depth_points[:,4:6]=1.
            colors=frame.rgb[selection]/255. if frame.rgb is not None else None
            voxel_map.voxelize_and_update(depth_points,rgb=colors,timestamp=time,origin=transform[:3,3],
                                         return_metadata_valid=False)
            count=len(depth_points)
    return {"lidar_rays":int(len(frame.lidar_points)),"depth_rays":int(count)}


def select_observed_ground_endpoints(grid:PlannerGrid,height=.375,radius=.2):
    """Choose a useful connected pair ONLY from footprint-clear observed cells.

    A traversable ground cell must have observed occupied support below it.
    No semantic labels or assumptions about room boundaries are used. The
    support search extends from the planning layer down to the map's lower Z
    boundary; a cell with no measured support is not called ground.
    """
    layer=int(np.floor((height-grid.origin[2])/grid.resolution))
    if not 0<=layer<grid.shape[2]: raise ValueError("Requested ground height is outside map")
    p=SafeCorridorPlanner(grid,radius=radius,mppi_samples=4,mppi_iterations=1)
    free=np.zeros(grid.shape[:2],bool)
    for x,y in np.argwhere(grid.known[:,:,layer]&~grid.occupied[:,:,layer]):
        center=grid.center([x,y,layer]); center[2]=height
        support=np.any(grid.occupied[x,y,:layer+1])
        if support and p.segment_clear(center,center): free[x,y]=True
    components,count=label(free,structure=np.array([[0,1,0],[1,1,1],[0,1,0]]))
    if count==0: return None,None,{"supported_clear_cells":0,"components":0}
    sizes=np.bincount(components.ravel()); sizes[0]=0
    chosen=int(sizes.argmax()); cells=np.argwhere(components==chosen)
    if len(cells)<2: return None,None,{"supported_clear_cells":int(free.sum()),"components":int(count)}
    # Two deterministic farthest-point sweeps find a long pair in this component.
    first=cells[0]; second=cells[np.argmax(np.sum((cells-first)**2,axis=1))]
    first=cells[np.argmax(np.sum((cells-second)**2,axis=1))]
    a=grid.center(np.r_[first,layer]); b=grid.center(np.r_[second,layer])
    a[2]=b[2]=height
    return a,b,{"supported_clear_cells":int(free.sum()),"components":int(count),
                "largest_component":int(len(cells)),"endpoint_distance":float(np.linalg.norm(b-a))}

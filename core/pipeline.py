"""Production dataflow: observations -> localization -> map -> ML -> tracks -> routes.

This module never imports or accepts simulator ground truth. Commissioning is a
separate, explicitly empty-room sensor acquisition, not access to a scene mesh.
"""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json,time,threading,tempfile,uuid,shutil,hashlib
from collections import deque
import numpy as np
import psutil
from scipy.spatial.transform import Rotation
from core.contracts import Detection3D,OrientedGeofence3D,PlannedTrajectory,TwinMasterFrameState,PALETTE
from core.se3_math import transform_points,inverse
from ingestion.sensor_synchronizer import fuse_frame
from ingestion.recorder_replayer import FrameRecorder
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
from slam_and_twin.icp_pose_graph import ICPPoseGraph
from slam_and_twin.static_twin_builder import StaticTwinBuilder
from slam_and_twin.spatial_diff_engine import SpatialDiffEngine
from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
from slam_and_twin.tiled_map import DiskBackedVoxelMap
from perception_ml.inference_engine import InferenceEngine
from perception_ml.eigen_descriptors import cluster_points,eigen_descriptors
from tracking_and_navigation.kalman_9dof_tracker import KalmanMultiObjectTracker
from tracking_and_navigation.risk_and_geofence import RiskGeofenceEngine
from tracking_and_navigation.traffic_heatmap import TrafficHeatmap
from tracking_and_navigation.safe_corridor_planner import PlannerGrid,SafeCorridorPlanner
from tracking_and_navigation.observed_navigation import integrate_commissioning_frame


def build_commissioned_assets(frames,path,config,progress=print):
    """Publish a complete bundle atomically; preserve the preceding bundle."""
    path=Path(path).resolve();path.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix=f'.{path.name}.building-',dir=path.parent)).resolve()
    backup=None
    try:
        result=_build_assets_into(frames,stage,config,progress)
        result[0].close();result[1].close()
        if path.exists():
            backup=path.with_name(f'{path.name}.previous-{uuid.uuid4().hex[:8]}')
            path.rename(backup)
        try:stage.rename(path)
        except BaseException:
            if backup is not None and not path.exists():backup.rename(path)
            raise
        if backup is not None:progress(f'Previous bundle preserved at {backup}')
        return load_commissioned_assets(path)
    finally:
        if stage.exists():
            if stage.parent!=path.parent or not stage.name.startswith(f'.{path.name}.building-') or stage.is_symlink():
                raise RuntimeError('Refusing unexpected reconstruction staging cleanup')
            shutil.rmtree(stage)


def observed_planning_window(voxel_map,planning):
    """Build a budgeted dense window; the disk authority keeps every observed tile."""
    resolution=float(planning['resolution']);observed=voxel_map.observed_bounds
    if observed is None:raise ValueError('No measured occupancy from which to select planning extents')
    bounds=np.asarray(planning['bounds'] if planning.get('bounds_mode','observed')=='configured' else observed,float)
    lower=np.floor(bounds[[0,2,4]]/resolution)*resolution;upper=np.ceil(bounds[[1,3,5]]/resolution)*resolution
    shape=np.maximum(1,np.rint((upper-lower)/resolution).astype(np.int64));maximum=int(planning.get('max_local_grid_cells',131072))
    if maximum<1:raise ValueError('Local planning-grid cell budget must be positive')
    full=bool(np.prod(shape,dtype=object)<=maximum)
    if not full:
        factor=(maximum/float(np.prod(shape,dtype=object)))**(1/3);limited=np.maximum(1,np.floor(shape*factor).astype(int))
        while int(np.prod(limited,dtype=object))>maximum:limited[np.argmax(limited)]-=1
        center=np.asarray(planning.get('window_center',(lower+upper)/2),float)
        if center.shape!=(3,) or not np.isfinite(center).all():raise ValueError('Planning window center must be a finite world 3-vector')
        first=np.floor((center-lower)/resolution-limited/2).astype(int);first=np.maximum(0,np.minimum(first,shape-limited))
        lower=lower+first*resolution;upper=lower+limited*resolution
    selected=np.array([lower[0],upper[0],lower[1],upper[1],lower[2],upper[2]])
    return PlannerGrid.from_voxel_map(voxel_map,bounds=selected,resolution=resolution),{
        'observed_bounds':observed.tolist(),'local_bounds':selected.tolist(),'full_extent_in_local_grid':full,
        'max_local_grid_cells':maximum,'unknown_space_is_free':False}


def _build_assets_into(frames,path,config,progress=print):
    path=Path(path);path.mkdir(parents=True,exist_ok=True);mapping=config['mapping'];limits=mapping.get('commissioning_budgets',{})
    twin=StaticTwinBuilder(mapping['twin_voxel_size'],commissioning_mode=mapping.get('commissioning_mode','auto'),
        initial_velocity=mapping.get('commissioning_initial_velocity'),storage_path=path,budgets=limits)
    static=None
    recorder=FrameRecorder(path/'commissioning',chunk_size=4,max_buffer_bytes=twin.budgets['frame_buffer_bytes'],
        max_disk_bytes=twin.budgets['frame_disk_bytes'],metadata={'commissioning_raw_acquisitions':True})
    provenance={'frame_count':0,'any_synthetic':False,'source_modes':set(),'sensor_ids':set(),'unknown_source_frames':0,'calibrations':{}}
    def raw_stream():
        for raw in frames:
            sensor=str(raw.capabilities.sensor_id)
            if len(sensor)>512:raise ValueError('Sensor identity exceeds provenance metadata budget')
            synthetic=bool(raw.diagnostics.get('synthetic',False)) or sensor.lower().startswith('synthetic')
            if raw.calibration is not None:
                calibration=raw.calibration;identity=str(calibration.calibration_id)
                if len(identity)>512:raise ValueError('Calibration identity exceeds provenance metadata budget')
                synthetic=synthetic or identity.lower().startswith('simulation')
                digest=hashlib.sha256(calibration.K.tobytes()+calibration.T_lidar_camera.tobytes()+calibration.distortion.tobytes()).hexdigest()
                entry={'calibration_id':identity,'measured':bool(calibration.measured),'matrix_sha256':digest}
                old=provenance['calibrations'].get(identity)
                if old is not None and old!=entry:raise ValueError('Calibration identity reused for different parameters/provenance')
                provenance['calibrations'][identity]=entry
            origin='sim' if synthetic else str(raw.diagnostics.get('source_mode',raw.diagnostics.get('source','unknown')))
            if origin=='unknown' and sensor.lower().startswith(('unitree','orbbec','gemini')):origin='physical_sensor_recording'
            provenance['frame_count']+=1;provenance['any_synthetic']|=synthetic;provenance['source_modes'].add(origin);provenance['sensor_ids'].add(sensor)
            if origin not in ('sim','live','physical_sensor_recording'):provenance['unknown_source_frames']+=1
            recorder.write(raw);yield raw
    try:
        progress('Streaming raw commissioning scans through pointwise preparation and bounded reconstruction...')
        try:twin.build(raw_stream())
        finally:recorder.close()
        tile_limits={key:twin.budgets[key] for key in ('tile_size','max_resident_tiles','max_resident_bytes','max_disk_bytes','sqlite_cache_bytes')}
        tile_limits.update(mapping.get('tile_budgets',{}))
        static=DiskBackedVoxelMap(path/'occupancy_tiles',config['planning']['resolution'],decay_rate=1.,max_age=float('inf'),**tile_limits)
        for j,(frame,pose) in enumerate(zip(twin.prepared_frames,twin.poses)):
            measured=replace(frame,sensor_pose_world=twin.floor_transform@pose)
            progress(f'Carving prepared navigation observations: {j+1}/{len(twin.poses)}')
            integrate_commissioning_frame(static,measured,camera_stride=1)
        static.flush();grid,window=observed_planning_window(static,config['planning']);grid.save(path/'planning_grid.npz')
        calibrations=list(provenance.pop('calibrations').values());provenance['source_modes']=sorted(provenance['source_modes']);provenance['sensor_ids']=sorted(provenance['sensor_ids'])
        all_measured=all(item['measured'] for item in calibrations) if calibrations else None
        provenance.update(calibrations=calibrations,all_camera_calibrations_measured=all_measured,
            live_reference_eligible=bool(not provenance['any_synthetic'] and not provenance['unknown_source_frames'] and all_measured is True),
            declaration_only=True,hardware_validation='not_tested')
        twin.report['navigation_storage']={**window,'authority':'occupancy_tiles','tile_cache':static.cells.statistics()}
        twin.report['source_provenance']=provenance
        twin.save(path)
        manifest={'schema_version':2,'type':'commissioned-lab-twin','raw_recording':'commissioning','prepared_recording':'prepared_frames',
            'surface_tiles':'surface_tiles','occupancy_tiles':'occupancy_tiles','tsdf_tiles':'tsdf_tiles','preview':'measured_map.npz',
            'planning_grid':'planning_grid.npz','planning_window':window,'hardware_validation':'not_tested','source_provenance':provenance}
        # Finalize metadata and SQLite handles before integrity hashes and the
        # enclosing atomic rename (including Windows file sharing semantics).
        static.close();twin.close()
        def digest_file(target):
            digest=hashlib.sha256()
            with target.open('rb') as stream:
                for block in iter(lambda:stream.read(1024**2),b''):digest.update(block)
            return digest.hexdigest()
        files=['occupancy_tiles/tiles.sqlite','occupancy_tiles/manifest.json','surface_tiles/tiles.sqlite','surface_tiles/points.json','measured_map.npz','planning_grid.npz',
            'commissioning/manifest.json','prepared_frames/manifest.json','tsdf_tiles/index.sqlite','tsdf_tiles/manifest.json','reconstruction.json']
        files.extend(name for name in ('mesh.ply','tsdf_mesh.ply','poisson_mesh.ply','watertight_mesh.ply') if (path/name).exists())
        manifest['authority_sha256']={name:digest_file(path/name) for name in files}
        manifest['map_revision']=hashlib.sha256(json.dumps({'authorities':manifest['authority_sha256'],'source':provenance},sort_keys=True,separators=(',',':')).encode()).hexdigest()
        (path/'bundle_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        progress(f'Commissioning complete: {len(static.cells)} measured evidence cells; bounded preview {len(twin.points)} surface points')
        return twin,static,grid
    except BaseException:
        try:twin.close()
        finally:
            if static is not None:static.close()
        raise


def load_commissioned_assets(path):
    path=Path(path)
    static=DiskBackedVoxelMap.load(path/'occupancy_tiles') if (path/'occupancy_tiles/manifest.json').exists() else SparseMultiReturnVoxelMap.load(path/'static_occupancy.npz')
    return StaticTwinBuilder.load(path),static,PlannerGrid.load(path/'planning_grid.npz')


def read_commissioning_provenance(path,verify_integrity=True):
    path=Path(path).resolve();manifest=path/'bundle_manifest.json'
    if not manifest.exists():return {'live_reference_eligible':False,'any_synthetic':None,'source_modes':['unknown_legacy'],
        'hardware_validation':'not_tested','reason':'Legacy bundle has no commissioning provenance; source cannot be established'}
    content=json.loads(manifest.read_text(encoding='utf-8'))
    provenance=dict(content.get('source_provenance',{'live_reference_eligible':False,'source_modes':['unknown']}))
    provenance['map_revision']=content.get('map_revision')
    if verify_integrity:
        def matches(relative,expected):
            target=(path/relative).resolve()
            if not target.is_relative_to(path) or not target.is_file():return False
            with target.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()==expected
        hashes=content.get('authority_sha256',{})
        revision=hashlib.sha256(json.dumps({'authorities':hashes,'source':content.get('source_provenance')},sort_keys=True,separators=(',',':')).encode()).hexdigest()
        valid=bool(hashes) and content.get('map_revision')==revision and all(matches(name,digest) for name,digest in hashes.items())
        if valid:
            for directory in ('commissioning','prepared_frames'):
                recording=json.loads((path/directory/'manifest.json').read_text())
                if not all('sha256' in chunk and matches(str(Path(directory)/chunk['file']),chunk['sha256']) for chunk in recording['chunks']):valid=False;break
        if valid:
            import sqlite3
            connection=sqlite3.connect((path/'tsdf_tiles/index.sqlite').as_uri()+'?mode=ro',uri=True)
            try:valid=all(matches(str(Path('tsdf_tiles')/name),digest) for name,digest in connection.execute('SELECT file,sha256 FROM meshes'))
            finally:connection.close()
        provenance['integrity_verified']=valid
        if not valid:provenance.update(live_reference_eligible=False,integrity_failure='Authority, mesh tile or recording checksum differs from publication manifest')
    return provenance


def detections_from_prediction(prepared,probabilities,flow,embeddings,diff,config,flow_valid,sensor_origin):
    xyz=prepared.voxels.voxel_centers
    labels=probabilities.argmax(1);confidence=probabilities.max(1)
    novel=diff.foreground_mask(xyz,origin=sensor_origin)
    detections=[]
    for cls in range(2,7):
        selected=np.flatnonzero((labels==cls)&novel&(confidence>=config['perception']['confidence_threshold']))
        if not len(selected):continue
        clusters=cluster_points(xyz[selected],config['tracking']['cluster_eps'],config['tracking']['min_cluster_points'])
        for cluster in np.unique(clusters):
            if cluster<0:continue
            ids=selected[clusters==cluster];p=xyz[ids].astype(float)
            centered=p-p.mean(0)
            _,basis=np.linalg.eigh(centered.T@centered/max(len(p)-1,1));R=basis[:,::-1].copy()
            if np.linalg.det(R)<0:R[:,-1]*=-1
            local=centered@R;lo=local.min(0);hi=local.max(0)
            position=p.mean(0)+(lo+hi)/2@R.T;size=np.maximum(hi-lo,.10)
            prob=probabilities[ids].mean(0);mean_flow=flow[ids].mean(0)
            variance=float(np.var(flow[ids],axis=0).mean()+.0025)
            eigen=eigen_descriptors(p).mean(0)
            detections.append(Detection3D(position,size,R,prob,mean_flow,embeddings[ids].mean(0),p,eigen,flow_valid,variance))
    return detections


class LabPipeline:
    def __init__(self,config,checkpoint,twin,static_map,planning_grid,mobile=False):
        self._closed=False;self._owned_map_cache=None
        try:self._initialize(config,checkpoint,twin,static_map,planning_grid,mobile)
        except BaseException:
            try:self.close()
            except Exception:pass
            raise

    def _initialize(self,config,checkpoint,twin,static_map,planning_grid,mobile):
        self.config=config;self.twin=twin;self.static_map=static_map;self.base_grid=planning_grid;self.mobile=mobile
        self.inference=InferenceEngine(checkpoint,config['device'],config['mapping']['voxel_size'],config['perception']['max_voxels'],
            config['perception'].get('neighbors',16),config['perception'].get('temporal_neighbors',12),config['perception'].get('rgbd_stride',3),
            config['perception'].get('range_image_height',24),config['perception'].get('range_image_width',96),
            config['perception'].get('camera_feature_height'),config['perception'].get('camera_feature_width'))
        tiled_reference=getattr(twin,'point_store',None) is not None
        self.diff=SpatialDiffEngine(twin.points,config['mapping']['tau0'],config['mapping']['beta'],config['mapping']['floor_epsilon'],
            distance_provider=twin.distances if tiled_reference else None)
        map_args={'max_age':30.,'ray_backend':config['mapping'].get('ray_backend','auto'),
                  'max_traversal_cells':config['mapping'].get('max_traversal_cells',5_000_000)}
        self._owned_map_cache=None;self._closed=False
        if config['mapping'].get('runtime_tiled',False):
            cache_root=config['mapping'].get('runtime_cache_root')
            if cache_root is not None:Path(cache_root).mkdir(parents=True,exist_ok=True)
            self._owned_map_cache=tempfile.TemporaryDirectory(prefix='aethertwin-dynamic-',dir=cache_root)
            self.occupancy=DiskBackedVoxelMap(self._owned_map_cache.name,config['mapping']['voxel_size'],config['mapping']['decay_rate'],
                **map_args,**config['mapping'].get('tile_budgets',{}))
        else:self.occupancy=SparseMultiReturnVoxelMap(config['mapping']['voxel_size'],config['mapping']['decay_rate'],**map_args)
        self.deskewer=None
        reference=transform_points(inverse(twin.floor_transform),twin.points) if mobile and not tiled_reference else None
        self.odometry=ICPPoseGraph(reference_points=reference)
        self._tiled_reference=tiled_reference;self._reference_center=None
        self.tracker=KalmanMultiObjectTracker(model=config['tracking']['model'])
        self.risk=RiskGeofenceEngine()
        observed=getattr(static_map,'observed_bounds',None)
        heat_bounds=config['planning']['bounds'][:4] if observed is None else np.asarray(observed)[:4]
        heat_resolution=float(config['planning']['resolution']);heat_limit=int(config['planning'].get('max_heatmap_cells',1048576))
        if heat_limit<1:raise ValueError('Heatmap cell budget must be positive')
        area=max(0.,(heat_bounds[1]-heat_bounds[0])*(heat_bounds[3]-heat_bounds[2]))
        heat_resolution=max(heat_resolution,np.sqrt(area/heat_limit)*1.001)
        widths=np.asarray([heat_bounds[1]-heat_bounds[0],heat_bounds[3]-heat_bounds[2]])
        while int(np.prod(np.maximum(1,np.ceil(widths/heat_resolution)).astype(object)))>heat_limit:heat_resolution*=1.1
        self.heatmap=TrafficHeatmap(bounds=heat_bounds,resolution=heat_resolution)
        self.planner=SafeCorridorPlanner(planning_grid,radius=config['planning']['radius'],seed=config['seed'],
            mppi_samples=config['planning'].get('mppi_samples',96),mppi_iterations=config['planning'].get('mppi_iterations',2))
        self._prepare_planning_queries();self.planning_region_error=None
        live=config.get('input_mode')=='live'
        self.planning_requested=bool(not live or config['planning'].get('use_configured_endpoints',False))
        if config['planning'].get('start') is None or config['planning'].get('goal') is None:self.planning_requested=False
        self.start=np.array(config['planning']['start'],float) if self.planning_requested else planning_grid.center(np.asarray(planning_grid.shape)//2)
        self.goal=np.array(config['planning']['goal'],float) if self.planning_requested else self.start.copy()
        self.robot_position=self.start.copy();self.robot_velocity=np.zeros(3)
        self.trajectory=PlannedTrajectory('not_requested',np.empty((0,3)),np.empty(0));self.trajectory_elapsed=0.
        self.geofences=[OrientedGeofence3D('Robotics cell',np.array([-.7,-.2,.9]),Rotation.from_euler('z',.2).as_matrix(),np.array([.7,.8,.9])),
            OrientedGeofence3D('Server rack',np.array([-4.8,3,1.]),np.eye(3),np.array([.6,.8,1.]))]
        if live or 'geofences' in config:
            self.geofences=[OrientedGeofence3D(z['name'],z['center'],Rotation.from_euler('z',z.get('yaw',0)).as_matrix(),z['half_extents'],z.get('severity_level',2))
                            for z in config.get('geofences',[])]
        xmin,xmax,ymin,ymax=config['planning']['bounds'][:4]
        resolution=config['planning']['resolution']
        x,y=np.meshgrid(np.arange(xmin,xmax,resolution),np.arange(ymin,ymax,resolution))
        self.floor_points=np.c_[x.ravel(),y.ravel(),np.full(x.size,.08)]
        self.frames=0;self.last_time=None;self.lock=threading.Lock();self.pending_goal=None
        self.alert_log=deque(maxlen=12);self.processing_ms=deque(maxlen=20000);self.memory_mb=deque(maxlen=20000);self.module_ms={}
        self.last_state=None;self.started=time.monotonic();self.dropped_snapshots=0

    def set_start_goal(self,start,goal):
        start=np.asarray(start,float);goal=np.asarray(goal,float)
        if start.shape!=(3,) or goal.shape!=(3,) or not np.isfinite(np.r_[start,goal]).all():raise ValueError('Start/goal require finite XYZ')
        with self.lock:self.pending_goal=(start.copy(),goal.copy())

    def set_geofences(self,zones):
        from command_center.controls import validate_payload
        data=validate_payload('set_geofences',{'geofences':zones})
        fences=[OrientedGeofence3D(z['name'],z['center'],Rotation.from_euler('z',z['yaw']).as_matrix(),z['half_extents'],z['severity_level'])
                for z in data['geofences']]
        with self.lock:self.geofences=fences

    def _prepare_planning_queries(self):
        grid=self.base_grid
        self.planning_points=grid.center(np.indices(grid.shape).reshape(3,-1).T)
        unknown_indices=np.argwhere(~grid.known)
        lower=grid.origin+unknown_indices*grid.resolution
        first=np.floor((lower+1e-10)/self.occupancy.voxel_size).astype(np.int64)
        last=np.floor((lower+grid.resolution-1e-10)/self.occupancy.voxel_size).astype(np.int64)
        count=int(np.ceil(grid.resolution/self.occupancy.voxel_size))+1
        offsets=np.indices((count,count,count)).reshape(3,-1).T
        subcells=first[:,None]+offsets;valid=np.all(subcells<=last[:,None],axis=-1)
        self.unknown_indices=unknown_indices;self.unknown_queries=(subcells[valid]+.5)*self.occupancy.voxel_size
        self.unknown_owners=np.broadcast_to(np.arange(len(first))[:,None],valid.shape)[valid]

    def _page_planning_region(self,start,goal):
        if not hasattr(self.static_map,'iter_observed'):return
        endpoints=np.asarray([start,goal]);radius=self.planner.radius
        if np.all(endpoints-radius>=self.base_grid.origin) and np.all(endpoints+radius<self.base_grid.upper):return
        from slam_and_twin.planning_region import load_observed_planning_region
        grid=load_observed_planning_region(self.static_map,start,goal,self.config['planning'])
        self.base_grid=grid;self._prepare_planning_queries();self.planner.set_grid(grid)
        self.config['planning']['bounds']=[grid.origin[0],grid.upper[0],grid.origin[1],grid.upper[1],grid.origin[2],grid.upper[2]]
        x,y=np.meshgrid(np.arange(grid.origin[0],grid.upper[0],grid.resolution),np.arange(grid.origin[1],grid.upper[1],grid.resolution))
        self.floor_points=np.c_[x.ravel(),y.ravel(),np.full(x.size,.08)]

    def export_trajectory(self,path=None):
        path=Path(path or 'artifacts/trajectory.csv');path.parent.mkdir(parents=True,exist_ok=True)
        with self.lock:
            points,times=self._remaining_trajectory()
            export=PlannedTrajectory(self.trajectory.status,points,times,self.trajectory.reason,self.trajectory.cost)
            self.planner.export(export,path)
        return str(path)

    def _remaining_trajectory(self):
        if self.trajectory.status!='ok':return np.empty((0,3)),np.empty(0)
        keep=self.trajectory.times>self.trajectory_elapsed+1e-9
        return np.vstack([self.robot_position,self.trajectory.points[keep]]),np.r_[0,self.trajectory.times[keep]-self.trajectory_elapsed]

    def _update_planning_grid(self):
        occupied=self.base_grid.occupied.copy();known=self.base_grid.known.copy()
        if hasattr(self.occupancy,'iter_occupied_points'):
            margin=self.occupancy.voxel_size/2
            lower=self.base_grid.origin-margin;upper=self.base_grid.upper+margin
            bounds=[lower[0],upper[0],lower[1],upper[1],lower[2],upper[2]]
            points=self.occupancy.occupied_points(.6,bounds=bounds,max_points=self.config['mapping'].get('max_local_occupied_points',1048576))
        else:points=self.occupancy.occupied_points(.6)
        if len(points):
            size=self.occupancy.voxel_size
            first=np.floor((points-size/2-self.base_grid.origin+1e-10)/self.base_grid.resolution).astype(int)
            last=np.floor((points+size/2-self.base_grid.origin-1e-10)/self.base_grid.resolution).astype(int)
            count=int(np.ceil(size/self.base_grid.resolution))+1
            offsets=np.indices((count,count,count)).reshape(3,-1).T
            idx=first[:,None]+offsets
            valid=np.all((idx<=last[:,None])&(idx>=0)&(idx<self.base_grid.shape),axis=-1);idx=idx[valid]
            occupied[tuple(idx.T)]=True;known[tuple(idx.T)]=True
        if len(self.unknown_queries):
            values,observed=self.occupancy.query(self.unknown_queries,return_known=True)
            all_free=np.ones(len(self.unknown_indices),bool)
            np.logical_and.at(all_free,self.unknown_owners,observed&(values<-.2))
            ids=self.unknown_indices[all_free];known[tuple(ids.T)]=True
        grid=PlannerGrid(self.base_grid.origin,self.base_grid.resolution,occupied,known)
        current=self.planner.grid
        if (current.resolution==grid.resolution and np.array_equal(current.origin,grid.origin)
            and np.array_equal(current.occupied,grid.occupied) and np.array_equal(current.known,grid.known)):
            return
        self.planner.set_grid(grid)

    def process(self,raw_frame):
        start=time.perf_counter();timings={}
        if self.deskewer is None:self.deskewer=ContinuousTimeIMUDeskewer(initial_pose=raw_frame.sensor_pose_world,imu_to_lidar=raw_frame.imu_to_lidar)
        frame=self.deskewer.deskew_frame(raw_frame)
        pose=frame.sensor_pose_world
        aligned_time=frame.timestamp+frame.sweep_duration
        localization_valid=not self.mobile
        if self.mobile and len(frame.lidar_points)>=30:
            if self._tiled_reference:
                center=transform_points(self.twin.floor_transform,pose[:3,3][None])[0]
                radius=float(self.config['mapping'].get('localization_radius_m',8.))
                if self._reference_center is None or np.linalg.norm(center-self._reference_center)>radius/4:
                    measured=self.twin.load_local_points(center,radius,self.config['mapping'].get('localization_max_points',100000))
                    if len(measured)<30:raise ValueError('No sufficiently observed local reference for map localization')
                    self.odometry.set_reference(transform_points(inverse(self.twin.floor_transform),measured))
                    self._reference_center=center.copy()
            corrected=self.odometry.add_scan(frame.lidar_points[:,:3],pose)
            if self.frames and self.frames%20==0:
                corrected=self.odometry.optimize()[-1]
            pose_dt=aligned_time-self.last_time if self.last_time is not None else raw_frame.sweep_duration
            if pose_dt<=0:raise ValueError('Localized sweep-end times must increase')
            velocity=(corrected[:3,3]-self.previous_pose[:3,3])/pose_dt if hasattr(self,'previous_pose') else self.deskewer.velocity
            self.deskewer.correct_state(corrected,velocity)
            pose=corrected
            self.previous_pose=corrected.copy()
            localization_valid=True
        pose=self.twin.floor_transform@pose
        frame=replace(frame,sensor_pose_world=pose)
        fusion_stride=self.config['perception'].get('rgbd_stride',3)
        frame=fuse_frame(frame,stride=fusion_stride)
        observation_counts={'lidar':len(frame.lidar_points),
            'depth':0 if frame.gemini_rgbd_cloud is None else len(frame.gemini_rgbd_cloud),
            'legacy_s2':0 if frame.rplidar_s2_points is None else len(frame.rplidar_s2_points)}
        observations_valid=bool(sum(observation_counts.values()))
        navigation_valid=observations_valid and localization_valid
        world_points=frame.lidar_points.copy();world_points[:,:3]=transform_points(pose,world_points[:,:3])
        timings['localization']=1000*(time.perf_counter()-start);stage=time.perf_counter()
        rgb=frame.painted_features[:,:3] if frame.painted_features is not None else None
        origins=pose[:3,3] if frame.lidar_origins is None else transform_points(pose,frame.lidar_origins)
        if localization_valid:
            self.occupancy.voxelize_and_update(world_points,rgb,timestamp=aligned_time,origin=origins,
                beam_ids=frame.beam_ids,return_metadata_valid=frame.return_metadata_valid)
        else:self.occupancy.timestamp=aligned_time
        if localization_valid and frame.gemini_rgbd_cloud is not None:
            dense=np.zeros((len(frame.gemini_rgbd_cloud),7),np.float32)
            dense[:,:3]=transform_points(pose,frame.gemini_rgbd_cloud[:,:3]);dense[:,3]=.5;dense[:,4:6]=1
            camera_pose=frame.calibration.T_lidar_camera if frame.camera_pose_in_lidar is None else frame.camera_pose_in_lidar
            camera_origin=(pose@camera_pose)[:3,3]
            self.occupancy.voxelize_and_update(dense,frame.gemini_rgbd_cloud[:,3:],timestamp=aligned_time,origin=camera_origin)
        if localization_valid and frame.rplidar_s2_points is not None:
            ring=np.zeros((len(frame.rplidar_s2_points),7),np.float32)
            ring[:,:3]=transform_points(pose,frame.rplidar_s2_points[:,:3]);ring[:,3]=.5;ring[:,4:6]=1
            ring_origin=transform_points(pose,frame.rplidar_origin_lidar[None])[0]
            self.occupancy.voxelize_and_update(ring,timestamp=aligned_time,origin=ring_origin)
        timings['occupancy']=1000*(time.perf_counter()-stage);stage=time.perf_counter()
        prepared,probs,flow,embedding=self.inference.infer(frame,world_points,self.diff.distances,self.occupancy.query,
            dense_rgbd=frame.gemini_rgbd_cloud)
        dt=frame.sweep_duration if self.last_time is None else max(1e-6,aligned_time-self.last_time)
        detections=detections_from_prediction(prepared,probs,flow,embedding,self.diff,self.config,self.inference.temporal_valid,pose[:3,3])
        entities=self.tracker.update(detections,aligned_time,dt)
        if not self.planning_requested:self.robot_position=pose[:3,3].copy()
        entities,self.geofences,alerts=self.risk.evaluate(entities,self.geofences,self.robot_position,self.robot_velocity)
        self.alert_log.extend(alerts);heat=self.heatmap.update(entities,aligned_time)
        timings['perception_tracking']=1000*(time.perf_counter()-stage);stage=time.perf_counter()
        with self.lock:
            if self.pending_goal is not None:
                self.start,self.goal=self.pending_goal;self.robot_position=self.start.copy();self.pending_goal=None
                self.planning_requested=True
                self.trajectory=PlannedTrajectory('not_requested',np.empty((0,3)),np.empty(0))
                self.planning_region_error=None
                try:self._page_planning_region(self.start,self.goal)
                except RuntimeError as exc:
                    from slam_and_twin.tiled_map import MapBudgetExceeded
                    if not isinstance(exc,MapBudgetExceeded):raise
                    self.planning_region_error=str(exc)
                    self.trajectory=PlannedTrajectory('resource_limit',np.empty((0,3)),np.empty(0),str(exc))
            if observations_valid:self._update_planning_grid()
            if self.planning_requested and not navigation_valid:
                self.trajectory=PlannedTrajectory('stale_observations',np.empty((0,3)),np.empty(0),
                    'Motion held: no current observations or insufficient localization coverage')
                self.robot_velocity=np.zeros(3)
            need_replan=(navigation_valid and self.planning_requested and self.planning_region_error is None
                and (self.frames%self.config['planning']['replan_interval']==0
                     or self.trajectory.status in ('not_requested','stale_observations')))
            if self.trajectory.status=='ok':
                remaining,remaining_times=self._remaining_trajectory()
                if not self.planner.path_clear(remaining,remaining_times,entities):
                    self.trajectory=PlannedTrajectory('invalidated',np.empty((0,3)),np.empty(0),'New observation invalidated remaining trajectory')
                    need_replan=True
            if need_replan:
                planning_risk=self.risk.compute_risk(self.planning_points,entities)[0].reshape(self.base_grid.shape)
                self.trajectory=self.planner.plan(self.robot_position,self.goal,
                    risk=planning_risk,ground=self.config['planning']['ground'],entities=entities)
                self.trajectory_elapsed=0.
            if self.trajectory.status=='ok' and len(self.trajectory.times)>1:
                previous=self.robot_position.copy();next_time=min(self.trajectory_elapsed+dt,self.trajectory.times[-1])
                proposed=np.array([np.interp(next_time,self.trajectory.times,self.trajectory.points[:,j]) for j in range(3)])
                if self.planner.segment_clear(previous,proposed,0.,dt,entities):
                    self.trajectory_elapsed=next_time;self.robot_position=proposed;self.robot_velocity=(proposed-previous)/dt
                else:
                    self.trajectory=PlannedTrajectory('invalidated',np.empty((0,3)),np.empty(0),'Proposed motion failed swept collision check')
                    self.robot_velocity=np.zeros(3)
            else:self.robot_velocity=np.zeros(3)
            path=self._remaining_trajectory()[0] if self.trajectory.status=='ok' else None
        timings['planning']=1000*(time.perf_counter()-stage)
        # The displayed TTC and simulated robot diagnostics share one snapshot.
        entities,self.geofences,_=self.risk.evaluate(entities,self.geofences,self.robot_position,self.robot_velocity)
        risk_start=time.perf_counter();risk,risk_colors=self.risk.compute_risk(self.floor_points,entities)
        timings['risk_fields']=1000*(time.perf_counter()-risk_start)
        latency=1000*(time.perf_counter()-start);self.processing_ms.append(latency)
        for name,value in timings.items():self.module_ms.setdefault(name,deque(maxlen=20000)).append(value)
        rss=psutil.Process().memory_info().rss/1024**2;self.memory_mb.append(rss)
        colors=PALETTE[probs.argmax(1)] if len(probs) else np.empty((0,3))
        inlay=frame.gemini_rgbd_cloud
        if inlay is not None:inlay=np.c_[transform_points(pose,inlay[:,:3]),inlay[:,3:]]
        diagnostics={**frame.diagnostics,**self.deskewer.last_diagnostics,'module_ms':timings,'processing_ms':latency,
            'module_shapes':{'localization':{'raw_lidar':raw_frame.lidar_points.shape,'deskewed_lidar':frame.lidar_points.shape,'pose':pose.shape},
                'occupancy':{'lidar_rays':world_points.shape,'depth_points':None if frame.gemini_rgbd_cloud is None else frame.gemini_rgbd_cloud.shape},
                'perception_tracking':{'voxel_centers':prepared.voxels.voxel_centers.shape,'semantic_probabilities':probs.shape,
                    'scene_flow':flow.shape,'embeddings':embedding.shape,'track_count':len(entities)},
                'planning':{'grid':self.base_grid.shape,'path':None if path is None else path.shape},
                'risk_fields':{'points':self.floor_points.shape,'risk':risk.shape}},
            'memory_mb':rss,'queue_depth':getattr(self,'presentation_queue_depth',0),'dropped_snapshots':self.dropped_snapshots,
            'end_to_end_fps':(self.frames+1)/max(time.monotonic()-self.started,1e-6),
            'trajectory_status':self.trajectory.status,'trajectory_reason':self.trajectory.reason,
            'observation_counts':observation_counts,'navigation_observations_valid':navigation_valid,
            'planning_window':{'origin':self.base_grid.origin.tolist(),'upper':self.base_grid.upper.tolist(),
                'cells':int(np.prod(self.base_grid.shape)),'resource_error':self.planning_region_error},
            'planner':self.planner.last_diagnostics,'robot_position':self.robot_position.tolist(),
            'robot_velocity':self.robot_velocity.tolist(),'checkpoint_synthetic_only':self.inference.metadata.get('synthetic_only'),'hardware_validated':False,
            'protected_reference':'simulated_agent' if self.planning_requested else 'sensor_rig',
            'planning_requested':self.planning_requested,'processing_profile':{'rgbd_stride':fusion_stride,
                'camera_feature_height':self.config['perception'].get('camera_feature_height'),
                'camera_feature_width':self.config['perception'].get('camera_feature_width'),'neighbors':self.inference.model.neighbors,
                'temporal_neighbors':self.inference.model.temporal_neighbors,'max_voxels':self.inference.max_voxels},
            'halo_options':{key:value for key,value in self.config.get('hardware_halo',{}).items() if key in ('brightness','stale_after_ms','watchdog_ms')},
            'occupancy_cells':len(self.occupancy.cells),'active_voxels':len(colors),
            'ray_traversal_backend':self.occupancy.last_ray_backend,
            'map_storage':self.occupancy.cells.statistics() if hasattr(self.occupancy.cells,'statistics') else {'type':'in_memory_reference'},
            'traffic_grid':{'bounds_xy':self.heatmap.bounds,'resolution_m':self.heatmap.resolution,'decay_seconds':self.heatmap.decay_seconds}}
        if self.mobile:
            diagnostics['localization']={key:value for key,value in self.odometry.last_diagnostics.items() if np.isscalar(value)}
        state=TwinMasterFrameState(raw_frame.frame_id,aligned_time,1000/max(latency,.001),pose,tuple(entities),
            prepared.voxels.voxel_centers,colors,flow,self.floor_points,risk,risk_colors,heat,tuple(self.geofences),path,
            inlay,tuple(self.alert_log),diagnostics,self.trajectory.status,
            lab_id=self.config.get('identity',{}).get('lab_id','lab-default'),rig_id=self.config.get('identity',{}).get('rig_id','rig-01'),
            map_revision=self.config.get('identity',{}).get('map_revision','unversioned'),
            calibration_id=frame.calibration.calibration_id if frame.calibration is not None else 'unavailable',
            source_mode=self.config.get('input_mode','unspecified'),processed_at_unix=time.time(),
            validity={'observations':observations_valid,'localization':localization_valid,
                      'flow':bool(observations_valid and self.inference.temporal_valid),'hardware_verified':False,
                      'calibration_measured':bool(frame.calibration and frame.calibration.measured)})
        self.last_state=state;self.frames+=1;self.last_time=aligned_time
        return state

    def report(self):
        p=np.array(self.processing_ms);m=np.array(self.memory_mb)
        elapsed=time.monotonic()-self.started
        return {'schema_version':1,'frames':self.frames,'wall_seconds':elapsed,'end_to_end_fps':self.frames/max(elapsed,1e-6),
            'processing_latency_ms':{'p50':float(np.percentile(p,50)),'p95':float(np.percentile(p,95)),'max':float(p.max())} if len(p) else {},
            'mean_processing_fps':float(1000/p.mean()) if len(p) else 0.,
            'module_latency_ms':{name:{'p50':float(np.percentile(v,50)),'p95':float(np.percentile(v,95))} for name,v in self.module_ms.items() if len(v)},
            'rss_mb':{'first':float(m[0]),'last':float(m[-1]),'max':float(m.max()),'last_half_slope_mb_per_frame':float(np.polyfit(np.arange(len(m)//2,len(m)),m[len(m)//2:],1)[0]) if len(m)>4 else 0.} if len(m) else {},
            'dropped_snapshots':self.dropped_snapshots,'occupancy_cells':len(self.occupancy.cells),
            'presentation_queue':{'capacity':2,'current_depth':getattr(self,'presentation_queue_depth',0),
                'max_depth':getattr(self,'presentation_queue_max_depth',0)},
            'configuration':self.config,'input_mode':self.config.get('input_mode','unspecified'),
            'hardware_validation':'physical devices and real-lab accuracy have not been validated by this delivery'}

    def close(self):
        if getattr(self,'_closed',False):return
        self._closed=True
        failures=[]
        for resource in (getattr(self,'occupancy',None),getattr(self,'static_map',None),getattr(self,'twin',None),self._owned_map_cache):
            try:
                if hasattr(resource,'close'):resource.close()
                elif hasattr(resource,'cleanup'):resource.cleanup()
            except Exception as exc:failures.append(exc)
        if failures:raise failures[0]

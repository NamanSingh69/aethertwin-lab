"""Measured multi-station registration, RGB-D TSDF and oriented-cloud Poisson."""
from pathlib import Path
from dataclasses import replace
import copy
import json
import numpy as np
import open3d as o3d
import cv2
from core.se3_math import transform_points,inverse,so3_exp
from .icp_pose_graph import as_cloud,ICPPoseGraph,point_to_plane_icp,global_registration
from .imu_deskew import ContinuousTimeIMUDeskewer
from ingestion.recorder_replayer import FrameRecorder,FrameReplaySequence,frame_nbytes
from .tiled_reconstruction import DiskPointCloud,TSDFTileArchive,reconstruct_tsdf_tiles


def stationary_capture_declared(frame):
    """Operator/station calibration declaration, never inferred from zero gyro."""
    return bool(frame.diagnostics.get('commissioning_stationary',False)) or (
        bool(frame.diagnostics.get('pose_prior_valid',False)) and
        frame.diagnostics.get('pose_prior_source')=='commissioning station calibration')


def rectify_rgbd_for_tsdf(rgb,depth,calibration):
    """Rectify Brown images for pinhole TSDF; navigation retains original rays.

    Keep the measured K and optical frame. Nearest-neighbor depth resampling
    preserves invalid pixels and does not interpolate holes into measurements.
    RGB is bilinear only for visual surface color, never occupancy evidence.
    """
    rgb=np.asarray(rgb);depth=np.asarray(depth,dtype=np.float32)
    if rgb.shape[:2]!=depth.shape:raise ValueError('TSDF RGB/depth dimensions differ')
    if not np.any(calibration.distortion):
        return rgb.copy(),depth.copy(),{'rectified':False,'valid_input_pixels':int(np.count_nonzero(depth>0)),'valid_output_pixels':int(np.count_nonzero(depth>0))}
    h,w=depth.shape
    map_x,map_y=cv2.initUndistortRectifyMap(calibration.K,calibration.distortion,np.eye(3),calibration.K,(w,h),cv2.CV_32FC1)
    rectified_rgb=cv2.remap(rgb,map_x,map_y,cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT,borderValue=0)
    rectified_depth=cv2.remap(depth,map_x,map_y,cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT,borderValue=0)
    return rectified_rgb,rectified_depth,{'rectified':True,'method':'Brown_to_same_K_pinhole',
        'depth_interpolation':'nearest_preserving_invalid','K_rescaled':False,
        'valid_input_pixels':int(np.count_nonzero(depth>0)),'valid_output_pixels':int(np.count_nonzero(rectified_depth>0))}


def fit_floor_transform(points,normals=None,voxel_size=.08):
    """Find a low gravity-horizontal support plane, independent of scanner height.

    This assumes a gravity-aligned initial world z direction. A missing/weak
    measured support plane is an error; it cannot justify treating z=0 as floor.
    """
    p=np.asarray(points,dtype=float)[:,:3]
    if len(p)<30:raise ValueError("Floor calibration requires at least 30 measured points")
    if normals is None:
        cloud=as_cloud(p,0);cloud.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=max(.35,4*voxel_size),max_nn=40));normals=np.asarray(cloud.normals)
    horizontal=p[np.abs(np.asarray(normals)[:,2])>.85]
    if len(horizontal)<20:raise ValueError("No measured gravity-horizontal floor support")
    # The low quantile avoids below-floor outliers while rejecting upper bench
    # surfaces. Heights are relative, so scanner-origin recordings at -1.6m work.
    cutoff=np.quantile(horizontal[:,2],.35)+max(.05,voxel_size)
    candidate=horizontal[horizontal[:,2]<=cutoff]
    plane,inliers=as_cloud(candidate,0).segment_plane(max(.025,voxel_size*.2),3,500)
    n=np.asarray(plane[:3]);d=float(plane[3])
    if n[2]<0:n=-n;d=-d
    support=candidate[inliers]
    if n[2]<.9 or len(support)<20:raise ValueError("Floor fit failed gravity/support checks")
    support_span=np.ptp(support[:,:2],axis=0);scene_span=np.maximum(np.ptp(p[:,:2],axis=0),1e-6)
    if min(support_span)<.5 or np.prod(support_span)<.15*np.prod(scene_span):
        raise ValueError("Floor fit has insufficient measured spatial coverage")
    axis=np.cross(n,[0,0,1]);theta=np.arccos(np.clip(n[2],-1,1))
    R=np.eye(3) if np.linalg.norm(axis)<1e-10 else so3_exp(axis/np.linalg.norm(axis)*theta)
    T=np.eye(4);T[:3,:3]=R;T[2,3]=d
    return T,{'normal':n.tolist(),'offset':d,'support_points':len(support),'height_before_alignment':float(-d/n[2])}


class StaticTwinBuilder:
    def __init__(self,voxel_size=.08,poisson_depth=7,seed=0,initial_velocity=None,commissioning_mode='auto',*,storage_path=None,budgets=None):
        if commissioning_mode not in ('auto','mobile','stations'):raise ValueError('commissioning_mode must be auto, mobile or stations')
        velocity=None if initial_velocity is None else np.asarray(initial_velocity,dtype=float)
        if velocity is not None and (velocity.shape!=(3,) or not np.isfinite(velocity).all()):raise ValueError('Initial velocity prior must be a finite world-frame 3-vector')
        self.initial_velocity=None if velocity is None else velocity.copy();self.commissioning_mode=commissioning_mode;self.prepared_frames=()
        self.storage_path=None if storage_path is None else Path(storage_path).resolve();self.point_store=None;self.tsdf_archive=None
        self.budgets={'max_frames':10000,'max_in_memory_frames':32,'max_in_memory_bytes':128*1024**2,
            'frame_buffer_bytes':32*1024**2,'frame_disk_bytes':64*1024**3,'max_keyframes':32,
            'registration_points':5000,'reconstruction_points':120000,'tile_size':16,
            'max_resident_tiles':32,'max_resident_bytes':64*1024**2,'max_disk_bytes':4*1024**3,
            'sqlite_cache_bytes':2*1024**2,'tsdf_tile_resolution':48,'mesh_preview_vertices':120000,
            'mesh_preview_triangles':240000,'tsdf_disk_bytes':2*1024**3}
        if set(budgets or {})-self.budgets.keys():raise ValueError('Unknown commissioning budget key')
        self.budgets.update(budgets or {})
        for key,value in self.budgets.items():
            if not np.isfinite(value) or int(value)!=value or int(value)<1:raise ValueError('Commissioning resource budgets must be positive integers')
            self.budgets[key]=int(value)
        self.voxel_size=float(voxel_size);self.poisson_depth=int(poisson_depth);self.points=np.empty((0,3));self.mesh=o3d.geometry.TriangleMesh()
        self.poisson_mesh=o3d.geometry.TriangleMesh();self.tsdf_mesh=o3d.geometry.TriangleMesh();self.watertight_mesh=o3d.geometry.TriangleMesh();self.floor_transform=np.eye(4);self.poses=[];self.report={};self.seed=int(seed)
    def build(self,frames):
        if self.storage_path is None:
            retained=[];size=0
            for frame in frames:
                size+=frame_nbytes(frame)
                if len(retained)>=self.budgets['max_in_memory_frames'] or size>self.budgets['max_in_memory_bytes']:
                    raise ValueError('Direct builder observation budget exceeded; provide storage_path for streaming commissioning')
                retained.append(frame)
            frames=retained
        if self.storage_path is not None:self.storage_path.mkdir(parents=True,exist_ok=True)
        writer=None if self.storage_path is None else FrameRecorder(self.storage_path/'prepared_frames',chunk_size=4,
            max_buffer_bytes=self.budgets['frame_buffer_bytes'],max_disk_bytes=self.budgets['frame_disk_bytes'],metadata={'coordinate_reference':'lidar_sweep_end'})
        self.watertight_mesh=o3d.geometry.TriangleMesh()
        o3d.utility.random.seed(self.seed)
        graph=ICPPoseGraph(voxel_size=max(.1,self.voxel_size),max_keyframes=self.budgets['max_keyframes']);clouds=[];coarse_reports=[];prepared=[];preparation=[];self.poses=[]
        carried=None;previous_end=None;previous_pose=None
        for raw in frames:
            if len(self.poses)>=self.budgets['max_frames']:raise ValueError('Commissioning frame/pose metadata budget exceeded')
            already=raw.diagnostics.get('coordinate_reference')=='lidar_sweep_end'
            station=self.commissioning_mode=='stations' or (self.commissioning_mode=='auto' and stationary_capture_declared(raw))
            mode='prepared' if already else ('independent_station' if station else 'causal_mobile')
            if already:
                if raw.lidar_origins is None:raise ValueError('Already deskewed commissioning requires its per-return acquisition origins')
                if raw.calibration is not None and raw.camera_pose_in_lidar is None:
                    if raw.camera_timestamp is None or abs(raw.camera_timestamp-(raw.timestamp+raw.sweep_duration))>1e-6:
                        raise ValueError('Deskewed commissioning requires the effective capture-time camera pose')
                f=raw;carried=None
            elif station:
                if len(raw.imu_samples)>=2:
                    independent=ContinuousTimeIMUDeskewer(initial_pose=raw.sensor_pose_world,initial_velocity=np.zeros(3),imu_to_lidar=raw.imu_to_lidar)
                    f=independent.deskew_frame(raw)
                else:
                    # Explicit fixed station observations can have no IMU. This
                    # path is a declared at-rest capture, not motion detection.
                    if raw.imu_samples:raise ValueError('Station IMU coverage is incomplete')
                    origins=np.zeros((len(raw.lidar_points),3)) if raw.lidar_origins is None else raw.lidar_origins
                    f=replace(raw,lidar_origins=origins,camera_pose_in_lidar=raw.calibration.T_lidar_camera if raw.calibration is not None else None,
                              diagnostics={**raw.diagnostics,'coordinate_reference':'lidar_sweep_end','commissioning_static_without_imu':True})
                carried=None
            else:
                if carried is None:
                    velocity=self.initial_velocity if previous_pose is None else np.zeros(3)
                    carried=ContinuousTimeIMUDeskewer(initial_pose=raw.sensor_pose_world if previous_pose is None else previous_pose,
                        initial_velocity=velocity,imu_to_lidar=raw.imu_to_lidar)
                    if previous_end is not None:carried.last_timestamp=previous_end
                elif not np.allclose(carried.imu_to_lidar,raw.imu_to_lidar,atol=1e-10):
                    raise ValueError('IMU extrinsics changed inside causal commissioning sequence')
                f=carried.deskew_frame(raw)
            f=replace(f,diagnostics={**f.diagnostics,'commissioning_preparation_mode':mode})
            cloud=np.asarray(f.lidar_points[:,:3]);full_cloud=cloud
            if len(cloud)>self.budgets['registration_points']:cloud=cloud[np.linspace(0,len(cloud)-1,self.budgets['registration_points']).astype(int)]
            if writer is None:clouds.append(full_cloud)
            if len(cloud)<10:raise ValueError("Insufficient commissioning returns")
            if graph.clouds:
                # Supplied station pose is an initialization, refined against observations.
                guess=f.sensor_pose_world;prior_relative=inverse(graph.poses[-1])@guess
                known_prior=bool(f.diagnostics.get('pose_prior_valid',not np.allclose(guess,np.eye(4))))
                coarse_report={'station':len(self.poses),'prior_declared':known_prior,'method':'causal_imu_prediction' if mode=='causal_mobile' else 'pose_prior'}
                try:
                    coarse,fitness=global_registration(cloud,graph.clouds[-1]);coarse_report['fpfh_ransac_fitness']=fitness
                    if not known_prior and mode!='causal_mobile':
                        coarse_refined,coarse_diag=point_to_plane_icp(cloud,graph.clouds[-1],coarse,voxel=max(.1,self.voxel_size))
                        prior_refined,prior_diag=point_to_plane_icp(cloud,graph.clouds[-1],prior_relative,voxel=max(.1,self.voxel_size))
                        def score(diag):return diag['rmse']/max(diag['fitness'],1e-6)+.1*(1-diag['fitness'])
                        if score(coarse_diag)<score(prior_diag):guess=graph.poses[-1]@coarse_refined;coarse_report['method']='fpfh_ransac'
                except ValueError as error:coarse_report['fpfh_ransac_error']=str(error)
                coarse_reports.append(coarse_report)
                try:graph.add_scan(cloud,guess)
                except ValueError:
                    relative,fitness=global_registration(cloud,graph.clouds[-1])
                    graph.add_scan(cloud,graph.poses[-1]@relative)
            else:graph.add_scan(cloud,f.sensor_pose_world)
            localized=graph.poses[-1].copy();end=raw.timestamp+raw.sweep_duration
            if mode=='causal_mobile':
                if previous_end is not None:
                    cadence=end-previous_end
                    if cadence<=0:raise ValueError('Causal mobile commissioning must have increasing sweep-end timestamps; use declared independent stations for reset captures')
                    velocity=(localized[:3,3]-previous_pose[:3,3])/cadence
                else:velocity=carried.velocity
                carried.correct_state(localized,velocity)
            localized_frame=replace(f,sensor_pose_world=localized)
            if writer is None:prepared.append(localized_frame)
            else:writer.write(localized_frame)
            self.poses.append(localized)
            if len(self.poses)%self.budgets['max_keyframes']==0:
                optimized=graph.optimize();self.poses[len(self.poses)-len(optimized):]=optimized
            preparation.append({'frame_id':raw.frame_id,'mode':mode,'per_return_origins':f.lidar_origins is not None,
                'camera_pose_timed':f.camera_pose_in_lidar is not None,'pointwise_deskew_applied':not already and len(raw.imu_samples)>=2})
            previous_end=end;previous_pose=localized
        if not self.poses:raise ValueError('No commissioning scans supplied')
        if writer is not None:writer.close()
        # Multi-station loops need not wait for a long mobile odometry history.
        for a in range(len(graph.clouds)):
            for b in range(a+2,len(graph.clouds)):
                initial=inverse(graph.poses[a])@graph.poses[b]
                T,diag=point_to_plane_icp(graph.clouds[b],graph.clouds[a],initial,voxel=max(.1,self.voxel_size))
                if diag['fitness']>.4 and diag['rmse']<.15:graph.edges.append((a,b,T,diag['information']))
        optimized=graph.optimize();self.poses[len(self.poses)-len(optimized):]=optimized
        if writer is None:
            self.prepared_frames=tuple(replace(frame,sensor_pose_world=pose) for frame,pose in zip(prepared,self.poses))
            combined=np.concatenate([transform_points(T,p) for T,p in zip(self.poses,clouds)])
            cloud=as_cloud(combined,self.voxel_size)
            if len(cloud.points)>self.budgets['reconstruction_points']:
                raise ValueError('Direct reconstruction point budget exceeded; provide storage_path for complete tiled observations')
        else:
            self.prepared_frames=FrameReplaySequence(self.storage_path/'prepared_frames',self.poses)
            options={key:self.budgets[key] for key in ('tile_size','max_resident_tiles','max_resident_bytes','max_disk_bytes','sqlite_cache_bytes')}
            self.point_store=DiskPointCloud(self.storage_path/'surface_tiles',self.voxel_size,**options)
            for frame,pose in zip(self.prepared_frames,self.poses):self.point_store.add(transform_points(pose,frame.lidar_points[:,:3]))
            self.point_store.flush();cloud=as_cloud(self.point_store.sample(self.budgets['reconstruction_points'],self.seed),0)
        self.floor_transform,floor_report=fit_floor_transform(np.asarray(cloud.points),voxel_size=self.voxel_size)
        cloud.transform(self.floor_transform)
        if self.point_store is not None:self.point_store.transform=self.floor_transform;self.point_store.flush()
        self.points=np.asarray(cloud.points).copy();cloud.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=self.voxel_size*4,max_nn=30))
        cloud.orient_normals_consistent_tangent_plane(20)
        # Poisson's native worker state can race after other OpenMP consumers
        # have run. Offline single-thread reconstruction is deterministic and
        # avoids the repeated-call crash without changing the reconstruction.
        self.poisson_mesh,density=o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(cloud,depth=self.poisson_depth,n_threads=1)
        # Screened Poisson closes unobserved regions. Preserve this inferred
        # surface separately; never use its imputed closure for collision/free-space.
        closure_scale=1.1;raw_closed=self.poisson_mesh.is_watertight()
        if raw_closed:self.watertight_mesh=copy.deepcopy(self.poisson_mesh)
        else:
            expanded,_=o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(cloud,depth=self.poisson_depth,scale=1.5,n_threads=1)
            if expanded.is_watertight():self.watertight_mesh=expanded;closure_scale=1.5
        density=np.asarray(density)
        if len(density):self.poisson_mesh.remove_vertices_by_mask(density<np.quantile(density,.02))
        self.poisson_mesh=self.poisson_mesh.crop(cloud.get_axis_aligned_bounding_box())
        self.poisson_mesh.compute_vertex_normals()
        volume=None if writer is not None else o3d.pipelines.integration.ScalableTSDFVolume(voxel_length=self.voxel_size,sdf_trunc=4*self.voxel_size,color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8)
        integrated=0;rectification=[]
        for frame,pose in (() if writer is not None else zip(self.prepared_frames,self.poses)):
            if frame.depth is None or frame.rgb is None or frame.calibration is None:continue
            rgb,depth,rectified=rectify_rgbd_for_tsdf(frame.rgb,frame.depth,frame.calibration)
            rectification.append({'frame_id':frame.frame_id,**rectified})
            rgbd=o3d.geometry.RGBDImage.create_from_color_and_depth(o3d.geometry.Image(rgb),o3d.geometry.Image(depth),depth_scale=1.,depth_trunc=6.,convert_rgb_to_intensity=False)
            K=frame.calibration.K;h,w=frame.depth.shape
            intrinsic=o3d.camera.PinholeCameraIntrinsic(w,h,K[0,0],K[1,1],K[0,2],K[1,2])
            capture=frame.calibration.T_lidar_camera if frame.camera_pose_in_lidar is None else frame.camera_pose_in_lidar
            Twc=self.floor_transform@pose@capture
            volume.integrate(rgbd,intrinsic,inverse(Twc));integrated+=1
        if writer is None:tsdf=volume.extract_triangle_mesh();tiled_report=None
        else:
            tsdf,tiled_report=reconstruct_tsdf_tiles(self.prepared_frames,self.poses,self.floor_transform,self.storage_path/'tsdf_tiles',self.voxel_size,
                rectify_rgbd_for_tsdf,tile_resolution=self.budgets['tsdf_tile_resolution'],max_preview_vertices=self.budgets['mesh_preview_vertices'],
                max_preview_triangles=self.budgets['mesh_preview_triangles'],max_disk_bytes=self.budgets['tsdf_disk_bytes'])
            self.tsdf_archive=TSDFTileArchive(self.storage_path/'tsdf_tiles');integrated=tiled_report['frames']
        tsdf.compute_vertex_normals();self.tsdf_mesh=tsdf
        for name in ('poisson_mesh','watertight_mesh'):
            mesh=getattr(self,name)
            target=self.budgets['mesh_preview_triangles']
            if len(mesh.vertices)>self.budgets['mesh_preview_vertices']:
                target=min(target,max(1,int(len(mesh.triangles)*self.budgets['mesh_preview_vertices']/len(mesh.vertices)*.8)))
            if len(mesh.triangles)>target:setattr(self,name,mesh.simplify_quadric_decimation(target))
        # Four sparse viewpoints can yield only small disconnected TSDF fragments.
        # Retain both reconstructions; present the better covered Poisson surface.
        self.mesh=tsdf if len(tsdf.triangles)>=.25*len(self.poisson_mesh.triangles) else self.poisson_mesh
        modes=[step['mode'] for step in preparation]
        velocity_source=('provided_world_prior' if self.initial_velocity is not None else 'assumed_zero_unobservable_mobile_bootstrap') if 'causal_mobile' in modes else (
            'known_zero_at_independent_station_starts' if 'independent_station' in modes else 'externally_prepared_state')
        self.report={'stations':len(self.poses),'measured_points':len(self.points),'display_mesh_source':'tsdf_preview' if self.mesh is self.tsdf_mesh else 'poisson_inferred_preview',
                     'total_measured_surface_voxels':len(self.point_store) if self.point_store is not None else len(self.points),
                     'preview_complete':self.point_store is None or len(self.point_store)<=len(self.points),'tsdf_frames':integrated,'tsdf_triangles':len(tsdf.triangles),'mesh_triangles':len(self.mesh.triangles),'pose_graph_edges':len(graph.edges),
                     'poisson_triangles':len(self.poisson_mesh.triangles),'watertight':self.mesh.is_watertight(),'unknown_space_inferred_free':False,'floor_fit':floor_report,
                     'watertight_visual_mesh':bool(len(self.watertight_mesh.triangles) and self.watertight_mesh.is_watertight()),
                     'watertight_visual_method':'uncropped_screened_poisson','watertight_visual_scale':closure_scale,
                     'watertight_visual_closure_inferred':True,'watertight_visual_is_collision_authority':False,
                     'coarse_registration':coarse_reports,
                     'commissioning':{'requested_mode':self.commissioning_mode,'frames':preparation,
                         'mode_counts':{mode:modes.count(mode) for mode in sorted(set(modes))},
                         'initial_velocity_world_mps':(np.zeros(3).tolist() if self.initial_velocity is None else self.initial_velocity.tolist()) if 'causal_mobile' in modes else None,
                         'initial_velocity_source':velocity_source,
                         'constant_velocity_observable_from_imu':False,'raw_observations_preserved':True},
                     'tsdf_rectification':rectification,
                     'storage':{'mode':'disk_backed' if writer is not None else 'bounded_direct','budgets':self.budgets,
                         'pose_graph':'sliding_window_globally_anchored','retired_keyframes':graph.dropped_keyframes,
                         'prepared_frame_storage':'prepared_frames' if writer is not None else None,
                         'frame_buffer_peak_bytes':writer.peak_buffer_bytes if writer is not None else size,
                         'surface_tiles':self.point_store.cells.statistics() if self.point_store is not None else None,
                         'tsdf_tiles':tiled_report},
                     'graph':{k:v for k,v in graph.last_diagnostics.items() if np.isscalar(v)}}
        return self
    def iter_measured_points(self,bounds=None):
        if self.point_store is not None:yield from self.point_store.iter_points(bounds)
        else:
            points=self.points
            if bounds is not None:
                b=np.asarray(bounds,float);points=points[np.all((points>=b[[0,2,4]])&(points<b[[1,3,5]]),axis=1)]
            for start in range(0,len(points),65536):yield points[start:start+65536]
    def iter_tsdf_meshes(self,bounds=None):
        if self.tsdf_archive is not None:yield from self.tsdf_archive.iter_meshes(bounds)
        elif len(self.tsdf_mesh.vertices):yield self.tsdf_mesh
    def distances(self,points,radius=2.):
        """Exact measured-map residuals within radius; +inf beyond it.

        Default 2 m matches the neural context's existing distance clipping.
        Use radius=None for a slower globally exact streamed search.
        """
        if self.point_store is not None:return self.point_store.distances(points,radius)
        from scipy.spatial import cKDTree
        points=np.asarray(points,float)[:,:3]
        if not len(self.points):return np.full(len(points),np.inf)
        tree=getattr(self,'_distance_tree',None)
        if tree is None:self._distance_tree=cKDTree(self.points);tree=self._distance_tree
        return tree.query(points,k=1,workers=1,distance_upper_bound=np.inf if radius is None else np.nextafter(float(radius),np.inf))[0]
    def load_local_points(self,pose_or_center,radius=8.,max_points=100000):
        """Exact local measured centroids in floor-aligned WORLD coordinates.

        Exceeding the point allowance is a resource error, never a hidden
        downsample or an assertion that this measured region is empty.
        """
        center=np.asarray(pose_or_center,float)
        if center.shape==(4,4):center=center[:3,3]
        if center.shape!=(3,) or not np.isfinite(center).all() or not np.isfinite(radius) or radius<=0 or max_points<1:
            raise ValueError('Invalid local measured-point request')
        lower=center-radius;upper=center+radius;bounds=np.array([lower[0],upper[0],lower[1],upper[1],lower[2],upper[2]])
        chunks=[];count=0
        from .tiled_map import MapBudgetExceeded
        for points in self.iter_measured_points(bounds):
            points=points[np.linalg.norm(points-center,axis=1)<=radius];count+=len(points)
            if count>max_points:raise MapBudgetExceeded('Local ICP measured-point budget exceeded; adjust radius/budget explicitly')
            if len(points):chunks.append(points)
        return np.concatenate(chunks) if chunks else np.empty((0,3))
    def close(self):
        if self.point_store is not None:self.point_store.close()
    def save(self,path):
        path=Path(path);path.mkdir(parents=True,exist_ok=True)
        np.savez_compressed(path/'measured_map.npz',points=self.points,floor_transform=self.floor_transform,poses=np.array(self.poses),voxel_size=self.voxel_size)
        if len(self.mesh.vertices):o3d.io.write_triangle_mesh(str(path/'mesh.ply'),self.mesh)
        if len(self.tsdf_mesh.vertices):o3d.io.write_triangle_mesh(str(path/'tsdf_mesh.ply'),self.tsdf_mesh)
        if len(self.poisson_mesh.vertices):o3d.io.write_triangle_mesh(str(path/'poisson_mesh.ply'),self.poisson_mesh)
        if len(self.watertight_mesh.vertices):o3d.io.write_triangle_mesh(str(path/'watertight_mesh.ply'),self.watertight_mesh)
        elif (path/'watertight_mesh.ply').exists():(path/'watertight_mesh.ply').unlink()
        (path/'reconstruction.json').write_text(json.dumps(self.report,indent=2),encoding='utf-8')
    @classmethod
    def load(cls,path):
        path=Path(path)
        with np.load(path/'measured_map.npz',allow_pickle=False) as z:
            out=cls(float(z['voxel_size']));out.points=z['points'];out.floor_transform=z['floor_transform'];out.poses=list(z['poses'])
        if (path/'mesh.ply').exists():out.mesh=o3d.io.read_triangle_mesh(str(path/'mesh.ply'))
        if (path/'tsdf_mesh.ply').exists():out.tsdf_mesh=o3d.io.read_triangle_mesh(str(path/'tsdf_mesh.ply'))
        if not len(out.mesh.vertices):out.mesh=out.tsdf_mesh
        if (path/'poisson_mesh.ply').exists():out.poisson_mesh=o3d.io.read_triangle_mesh(str(path/'poisson_mesh.ply'))
        if (path/'watertight_mesh.ply').exists():out.watertight_mesh=o3d.io.read_triangle_mesh(str(path/'watertight_mesh.ply'))
        if (path/'surface_tiles/points.json').exists():out.point_store=DiskPointCloud.load(path/'surface_tiles')
        if (path/'tsdf_tiles/manifest.json').exists():out.tsdf_archive=TSDFTileArchive(path/'tsdf_tiles')
        if (path/'prepared_frames/manifest.json').exists():out.prepared_frames=FrameReplaySequence(path/'prepared_frames',out.poses)
        out.report=json.loads((path/'reconstruction.json').read_text(encoding='utf-8'));return out

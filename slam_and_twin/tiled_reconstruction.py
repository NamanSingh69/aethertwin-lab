"""Disk-backed measured surface points and bounded tile-wise TSDF reconstruction."""
from pathlib import Path
import json,sqlite3,hashlib
import numpy as np
import open3d as o3d
import cv2
from scipy.spatial import cKDTree
from core.se3_math import transform_points,inverse
from .tiled_map import DiskTileStore,MapBudgetExceeded


class DiskPointCloud:
    def __init__(self,path,voxel_size=.08,*,readonly=False,**budgets):
        self.path=Path(path).resolve();self.voxel_size=float(voxel_size);self.transform=np.eye(4)
        self.cells=DiskTileStore(path,value_width=4,readonly=readonly,**budgets);self.readonly=readonly
    def add(self,points):
        points=np.asarray(points,float);coords=np.floor(points/self.voxel_size).astype(np.int64)
        unique,indices,counts=np.unique(coords,axis=0,return_inverse=True,return_counts=True)
        sums=np.zeros((len(unique),3));np.add.at(sums,indices,points)
        for key,total,n in zip(unique,sums,counts):
            previous=np.asarray(self.cells.get(tuple(key),[0.,0.,0.,0.]));self.cells[tuple(key)]=previous+np.r_[total,n]
    def __len__(self):return len(self.cells)
    def iter_points(self,bounds=None):
        source_bounds=None
        if bounds is not None:
            b=np.asarray(bounds,float).reshape(6);corners=np.array([[b[i],b[2+j],b[4+k]] for i,j,k in np.ndindex(2,2,2)])
            source=transform_points(inverse(self.transform),corners)
            source_bounds=np.array([np.floor(source.min(0)/self.voxel_size),np.floor(source.max(0)/self.voxel_size)],np.int64)
        for _,_,values in self.cells.iter_tiles(source_bounds):
            points=transform_points(self.transform,values[:,:3]/values[:,3,None])
            if bounds is not None:
                b=np.asarray(bounds,float);points=points[np.all((points>=b[[0,2,4]])&(points<b[[1,3,5]]),axis=1)]
            if len(points):yield points
    def sample(self,max_points,seed=0):
        """Deterministic uniform reservoir; full measured data remains on disk."""
        limit=int(max_points);sample=np.empty((limit,3));seen=0;rng=np.random.default_rng(seed)
        for points in self.iter_points():
            for point in points:
                if seen<limit:sample[seen]=point
                else:
                    index=int(rng.integers(seen+1))
                    if index<limit:sample[index]=point
                seen+=1
        return sample[:min(seen,limit)].copy()
    def distances(self,points,radius=2.,batch_size=256):
        """Exact nearest measured centroid within radius, otherwise +inf.

        None requests a globally exact streamed search. Each KD-tree contains
        one bounded tile; no additional point downsampling is performed.
        """
        points=np.asarray(points,float)[:,:3]
        if not np.isfinite(points).all() or batch_size<1 or int(batch_size)!=batch_size:raise ValueError('Invalid measured-distance query')
        batch_size=int(batch_size)
        if radius is not None and (not np.isfinite(radius) or radius<0):raise ValueError('Distance radius must be finite and nonnegative')
        query=transform_points(inverse(self.transform),points);result=np.full(len(query),np.inf)
        tile_length=self.voxel_size*self.cells.tile_size
        groups=np.zeros(len(query),np.int64) if radius is None else np.unique(np.floor(query/tile_length).astype(np.int64),axis=0,return_inverse=True)[1]
        upper=np.inf if radius is None or radius==0 else np.nextafter(float(radius),np.inf)
        for group in np.unique(groups):
            members=np.flatnonzero(groups==group)
            for start in range(0,len(members),int(batch_size)):
                ids=members[start:start+batch_size];selected=query[ids]
                bounds=None if radius is None else np.array([np.floor((selected.min(0)-radius)/self.voxel_size),np.floor((selected.max(0)+radius)/self.voxel_size)],np.int64)
                for _,_,values in self.cells.iter_tiles(bounds):
                    candidates=values[:,:3]/values[:,3,None]
                    distances=cKDTree(candidates).query(selected,k=1,distance_upper_bound=upper,workers=1)[0]
                    result[ids]=np.minimum(result[ids],distances)
        if radius==0:result[result!=0]=np.inf
        return result
    def flush(self):
        self.cells.flush()
        if self.readonly:return
        metadata={'schema_version':1,'voxel_size':self.voxel_size,'transform':self.transform.tolist(),'tile_size':self.cells.tile_size,'budgets':self.cells.statistics()}
        (self.path/'points.json').write_text(json.dumps(metadata,indent=2))
    def close(self):
        if self.cells.closed:return
        try:self.flush()
        finally:self.cells.close()
    @classmethod
    def load(cls,path,readonly=True):
        metadata=json.loads((Path(path)/'points.json').read_text())
        if metadata['schema_version']!=1:raise ValueError('Unsupported measured surface store')
        options={key:metadata['budgets'][key] for key in ('max_resident_tiles','max_resident_bytes','max_disk_bytes','sqlite_cache_bytes')}
        obj=cls(path,metadata['voxel_size'],readonly=readonly,tile_size=metadata['tile_size'],**options);obj.transform=np.array(metadata['transform']);return obj


class TSDFTileArchive:
    def __init__(self,path):self.path=Path(path).resolve()
    def iter_meshes(self,bounds=None):
        connection=sqlite3.connect((self.path/'index.sqlite').as_uri()+'?mode=ro',uri=True)
        try:
            query='SELECT file FROM meshes';parameters=()
            if bounds is not None:
                b=np.asarray(bounds,float).reshape(6)
                query+=' WHERE ux>=? AND uy>=? AND uz>=? AND lx<=? AND ly<=? AND lz<=?';parameters=(*b[[0,2,4]],*b[[1,3,5]])
            for (name,) in connection.execute(query+' ORDER BY x,y,z',parameters):
                target=self.path/name
                if target.parent!=self.path or '..' in Path(name).parts:raise ValueError('Invalid mesh tile path')
                yield o3d.io.read_triangle_mesh(str(target))
        finally:connection.close()


def reconstruct_tsdf_tiles(frames,poses,floor_transform,path,voxel_size,rectify,*,tile_resolution=48,
                           max_preview_vertices=120000,max_preview_triangles=240000,max_disk_bytes=2*1024**3,depth_batch_rows=32):
    """Fuse every calibrated observation, one bounded UniformTSDF tile at a time.

    A disk index associates surface-neighbor tiles with actual observing frames.
    No recording-wide RGB-D cache or world-sized TSDF volume is allocated.
    """
    path=Path(path);path.mkdir(parents=True,exist_ok=True);resolution=int(tile_resolution)
    if not 8<=resolution<=128 or depth_batch_rows<1:raise ValueError('Invalid TSDF tile/batch resolution')
    length=resolution*voxel_size;trunc=4*voxel_size;connection=sqlite3.connect(path/'index.sqlite')
    connection.execute('PRAGMA cache_size=-2048')
    page_size=int(connection.execute('PRAGMA page_size').fetchone()[0]);connection.execute(f'PRAGMA max_page_count={max(32,max_disk_bytes//(4*page_size))}')
    connection.execute('CREATE TABLE IF NOT EXISTS observations (x INTEGER,y INTEGER,z INTEGER,frame INTEGER,PRIMARY KEY(x,y,z,frame)) WITHOUT ROWID')
    connection.execute('CREATE TABLE IF NOT EXISTS meshes (x INTEGER,y INTEGER,z INTEGER,file TEXT,vertices INTEGER,triangles INTEGER,lx REAL,ly REAL,lz REAL,ux REAL,uy REAL,uz REAL,sha256 TEXT,PRIMARY KEY(x,y,z)) WITHOUT ROWID')
    rgbd_frames=0
    try:
        # Index actual depth samples. Neighbor tiles cover the truncation band.
        for index,frame in enumerate(frames):
            if frame.depth is None or frame.rgb is None or frame.calibration is None:continue
            rgbd_frames+=1;calibration=frame.calibration
            capture=calibration.T_lidar_camera if frame.camera_pose_in_lidar is None else frame.camera_pose_in_lidar
            transform=floor_transform@poses[index]@capture;h,w=frame.depth.shape;tiles=set()
            for start in range(0,h,depth_batch_rows):
                depth=frame.depth[start:start+depth_batch_rows];yy,xx=np.indices(depth.shape);valid=(depth>0)&(depth<=6.)
                if not valid.any():continue
                pixels=np.c_[xx[valid],yy[valid]+start].astype(float)
                xy=cv2.undistortPoints(pixels[:,None],calibration.K,calibration.distortion)[:,0]
                points=transform_points(transform,np.c_[xy,np.ones(len(xy))]*depth[valid,None])
                lower=np.floor((points-trunc)/length).astype(np.int64);upper=np.floor((points+trunc)/length).astype(np.int64)
                for offsets in np.ndindex(2,2,2):
                    ids=lower+np.array(offsets);ids=ids[np.all(ids<=upper,axis=1)]
                    tiles.update(map(tuple,np.unique(ids,axis=0)))
            with connection:connection.executemany('INSERT OR IGNORE INTO observations VALUES (?,?,?,?)',((*map(int,tile),index) for tile in tiles))
        preview=o3d.geometry.TriangleMesh();mesh_bytes=0;tile_count=0;total_vertices=0;total_triangles=0;rectified_frames=0
        # Keep only a bounded preview. Every nonempty tile is also saved intact.
        for (x,y,z) in connection.execute('SELECT DISTINCT x,y,z FROM observations ORDER BY x,y,z'):
            tile=np.array([x,y,z]);origin=tile*length
            volume=o3d.pipelines.integration.UniformTSDFVolume(length=length,resolution=resolution,sdf_trunc=trunc,
                color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,origin=origin.astype(float))
            for (index,) in connection.execute('SELECT frame FROM observations WHERE x=? AND y=? AND z=? ORDER BY frame',(x,y,z)):
                frame=frames[index];rgb,depth,information=rectify(frame.rgb,frame.depth,frame.calibration)
                if information['rectified']:rectified_frames+=1
                K=frame.calibration.K;h,w=depth.shape
                intrinsic=o3d.camera.PinholeCameraIntrinsic(w,h,K[0,0],K[1,1],K[0,2],K[1,2])
                capture=frame.calibration.T_lidar_camera if frame.camera_pose_in_lidar is None else frame.camera_pose_in_lidar
                camera=floor_transform@poses[index]@capture
                rgbd=o3d.geometry.RGBDImage.create_from_color_and_depth(o3d.geometry.Image(rgb),o3d.geometry.Image(depth),depth_scale=1.,depth_trunc=6.,convert_rgb_to_intensity=False)
                volume.integrate(rgbd,intrinsic,inverse(camera))
            mesh=volume.extract_triangle_mesh();del volume
            if not len(mesh.triangles):continue
            mesh.compute_vertex_normals();name=f'tile_{x}_{y}_{z}.ply';target=path/name
            o3d.io.write_triangle_mesh(str(target),mesh);size=target.stat().st_size
            if mesh_bytes+size>max_disk_bytes//2:
                target.unlink();raise MapBudgetExceeded('TSDF mesh archive disk budget exhausted')
            mesh_bytes+=size;tile_count+=1;total_vertices+=len(mesh.vertices);total_triangles+=len(mesh.triangles)
            if len(preview.vertices)+len(mesh.vertices)<=max_preview_vertices and len(preview.triangles)+len(mesh.triangles)<=max_preview_triangles:preview+=mesh
            digest=hashlib.sha256()
            with target.open('rb') as stream:
                for block in iter(lambda:stream.read(1024**2),b''):digest.update(block)
            with connection:connection.execute('INSERT OR REPLACE INTO meshes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (x,y,z,name,len(mesh.vertices),len(mesh.triangles),*origin.tolist(),*(origin+length).tolist(),digest.hexdigest()))
        geometry_digest=hashlib.sha256()
        for row in connection.execute('SELECT x,y,z,sha256 FROM meshes ORDER BY x,y,z'):geometry_digest.update(json.dumps(row,separators=(',',':')).encode())
        report={'mode':'disk_indexed_uniform_tiles','frames':rgbd_frames,'nonempty_tiles':tile_count,'total_vertices':total_vertices,
            'total_triangles':total_triangles,'preview_vertices':len(preview.vertices),'preview_triangles':len(preview.triangles),
            'preview_complete':len(preview.triangles)==total_triangles,'tile_resolution':resolution,'resident_tile_voxels':resolution**3,
            'tile_length_m':length,'mesh_bytes':mesh_bytes,'max_disk_bytes':max_disk_bytes,'depth_batch_rows':depth_batch_rows,
            'rectified_tile_observations':rectified_frames,'all_observations_processed':True,'max_depth_m':6.,'geometry_sha256':geometry_digest.hexdigest()}
        (path/'manifest.json').write_text(json.dumps(report,indent=2));return preview,report
    finally:connection.close()

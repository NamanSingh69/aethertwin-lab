"""Sparse-scan covariance normals, robust ICP, and map-anchored pose graphs.

Local normals need actual geometric support. A fixed small metric radius can
contain only one or two points in a wide-angle sparse LiDAR scan, leading to
arbitrary default normals and unobservable motion despite high nearest-neighbor
fitness. Covariance neighborhoods here guarantee up to twelve observations and
adapt their support when that neighborhood is degenerate or strongly curved.
"""
from dataclasses import dataclass
import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree
from scipy.optimize import least_squares
from core.se3_math import se3_exp,se3_log,inverse,transform_points,adjoint


def _rigid_transform(value):
    """Remove floating-point metric drift without accepting an affine pose.

    Transpose inversion is valid only on SO(3). If a tiny metric error is kept
    in successive registration seeds, R_prev.T @ R_guess and pose composition
    can amplify it on every frame. Polar projection closes this numerical loop.
    Reflections and materially scaled inputs remain errors, not repaired poses.
    """
    pose=np.array(value,dtype=np.float64,copy=True)
    if pose.shape!=(4,4) or not np.isfinite(pose).all() or not np.allclose(pose[3],[0,0,0,1],atol=1e-10,rtol=0):
        raise ValueError('ICP pose must be a finite homogeneous transform')
    rotation=pose[:3,:3]
    if np.linalg.det(rotation)<=0:raise ValueError('ICP pose rotation must be proper; reflections are invalid')
    u,singular,vt=np.linalg.svd(rotation)
    if np.max(np.abs(singular-1))>1e-3:raise ValueError('ICP pose rotation has a non-rigid scale/shear')
    pose[:3,:3]=u@vt;pose[3]=[0,0,0,1]
    return pose


def covariance_normals(points,minimum_support=12,expanded_support=20):
    """Estimate normals and covariance using supported, adaptive KNN patches.

    The smallest eigenvector is the plane normal. Support expands only when a
    twelve-point patch is rank deficient or curved, and only if the expanded
    patch improves its surface score. Invalid surfaces are retained for display
    but excluded from point-to-plane constraints by prepare_reference().
    """
    points=np.asarray(points,dtype=float).reshape(-1,3); count=len(points)
    if count<3: return np.zeros((count,3)),np.zeros((count,3,3))
    maximum=min(expanded_support,count)
    _,indices=cKDTree(points).query(points,k=maximum,workers=1)
    if indices.ndim==1: indices=indices[:,None]
    def estimate(support):
        neighbors=points[indices[:,:support]]
        centered=neighbors-neighbors.mean(axis=1,keepdims=True)
        cov=np.einsum('nki,nkj->nij',centered,centered)/max(support-1,1)
        values,vectors=np.linalg.eigh(cov)
        curvature=values[:,0]/np.maximum(values.sum(1),1e-15)
        planarity=(values[:,1]-values[:,0])/np.maximum(values[:,2],1e-15)
        score=planarity*np.maximum(1-curvature/.12,0)
        return cov,values,vectors[:,:,0],score,curvature
    base=min(minimum_support,count)
    covariance,values,normals,score,curvature=estimate(base)
    if maximum>base:
        larger,larger_values,larger_normals,larger_score,_=estimate(maximum)
        expand=((values[:,1]<1e-8)|(curvature>.08)|(score<.10))&(larger_score>score)
        covariance[expand]=larger[expand]; normals[expand]=larger_normals[expand]
    return normals,covariance


def as_cloud(points,voxel=.12):
    cloud=o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.asarray(points)[:,:3]))
    if voxel:cloud=cloud.voxel_down_sample(voxel)
    if len(cloud.points)>=3:
        normals,covariance=covariance_normals(np.asarray(cloud.points))
        cloud.normals=o3d.utility.Vector3dVector(normals)
        cloud.covariances=o3d.utility.Matrix3dVector(covariance)
    return cloud


@dataclass(frozen=True)
class ICPReference:
    points: np.ndarray
    normals: np.ndarray
    quality: np.ndarray
    tree: object
    input_count: int


def prepare_reference(points,voxel=.12):
    """Cache reliable target geometry and a KD-tree, without semantic labels."""
    cloud=as_cloud(points,voxel); xyz=np.asarray(cloud.points); normals=np.asarray(cloud.normals)
    if len(xyz)<6: raise ValueError("ICP requires at least six target points")
    values=np.linalg.eigvalsh(np.asarray(cloud.covariances))
    curvature=values[:,0]/np.maximum(values.sum(1),1e-15)
    planarity=(values[:,1]-values[:,0])/np.maximum(values[:,2],1e-15)
    valid=(values[:,1]>1e-8)&(curvature<.12)&(planarity>.08)
    quality=planarity*np.maximum(1-curvature/.12,0)
    if valid.sum()<6: raise ValueError("ICP target lacks supported planar neighborhoods")
    xyz=xyz[valid].copy(); normals=normals[valid].copy(); quality=quality[valid].copy()
    return ICPReference(xyz,normals,quality,cKDTree(xyz),len(cloud.points))


def global_registration(source,target,voxel=.2):
    a=as_cloud(source,voxel);b=as_cloud(target,voxel)
    if min(len(a.points),len(b.points))<10:raise ValueError("Insufficient points for global registration")
    fa=o3d.pipelines.registration.compute_fpfh_feature(a,o3d.geometry.KDTreeSearchParamHybrid(radius=5*voxel,max_nn=100))
    fb=o3d.pipelines.registration.compute_fpfh_feature(b,o3d.geometry.KDTreeSearchParamHybrid(radius=5*voxel,max_nn=100))
    result=o3d.pipelines.registration.registration_ransac_based_on_feature_matching(a,b,fa,fb,True,voxel*1.5,
        o3d.pipelines.registration.TransformationEstimationPointToPoint(False),3,
        [o3d.pipelines.registration.CorrespondenceCheckerBasedOnEdgeLength(.9),o3d.pipelines.registration.CorrespondenceCheckerBasedOnDistance(voxel*1.5)],
        o3d.pipelines.registration.RANSACConvergenceCriteria(10000,.999))
    if result.fitness<.1:raise ValueError("Global registration has insufficient overlap")
    return _rigid_transform(result.transformation),float(result.fitness)


def point_to_plane_icp(source,target,initial=None,max_distance=.45,iterations=30,voxel=.12,reference=None):
    # Use the same geometric support criterion on both sides. Retaining a
    # corner on the source while excluding it on the target makes even an
    # identical cloud register to an adjacent plane, causing stationary drift.
    source_geometry=prepare_reference(source,voxel)
    src=source_geometry.points
    ref=prepare_reference(target,voxel) if reference is None else reference
    dst=ref.points;normal=ref.normals
    T=np.eye(4) if initial is None else _rigid_transform(initial)
    if min(len(src),len(dst))<6:raise ValueError("ICP requires at least six points")
    tree=ref.tree;information=np.eye(6)*1e-6;rmse=float('inf');fitness=0.;rank=0
    for _ in range(iterations):
        p=transform_points(T,src);distance,index=tree.query(p,workers=1);mask=distance<max_distance
        fitness=float(mask.mean())
        if mask.sum()<6:break
        p=p[mask];q=dst[index[mask]];n=normal[index[mask]];r=np.einsum('ij,ij->i',p-q,n)
        weights=np.minimum(1.,.08/np.maximum(np.abs(r),1e-12))*np.maximum(ref.quality[index[mask]],.02)*np.maximum(source_geometry.quality[mask],.02)
        J=np.c_[np.cross(p,n),n]
        information=J.T@(weights[:,None]*J);rhs=J.T@(weights*r)
        rank=int(np.linalg.matrix_rank(information,tol=max(np.linalg.norm(information,ord=2)*1e-8,1e-10)))
        delta=-np.linalg.lstsq(information+1e-8*np.eye(6),rhs,rcond=1e-10)[0]
        T=se3_exp(delta)@T;rmse=float(np.sqrt(np.mean(r*r)))
        if np.linalg.norm(delta)<1e-6:break
    return _rigid_transform(T),{'fitness':fitness,'rmse':rmse,'information':information,
        'normal_support':min(12,ref.input_count),'reliable_target_points':len(dst),'information_rank':rank}


class ICPPoseGraph:
    def __init__(self,voxel_size=.12,loop_distance=2.0,max_keyframes=100,reference_points=None):
        if max_keyframes is not None and max_keyframes<2:raise ValueError("A pose graph window needs at least two keyframes")
        self.voxel_size=voxel_size;self.loop_distance=loop_distance;self.clouds=[];self.poses=[];self.edges=[];self.last_diagnostics={}
        self.max_keyframes=max_keyframes;self.total_scans=0;self.dropped_keyframes=0
        self.reference=None;self.anchors=[]
        if reference_points is not None:self.set_reference(reference_points)
    def set_reference(self,points):
        """Use a commissioned static point map in the SAME world as initial_pose."""
        self.reference=prepare_reference(points,self.voxel_size)
    def add_scan(self,points,initial_pose=None):
        cloud=np.asarray(as_cloud(points,self.voxel_size).points)
        if not self.clouds:
            pose=np.eye(4) if initial_pose is None else _rigid_transform(initial_pose)
        else:
            guess=self.poses[-1] if initial_pose is None else _rigid_transform(initial_pose)
            relative=inverse(self.poses[-1])@guess
            T,diag=point_to_plane_icp(cloud,self.clouds[-1],relative,voxel=self.voxel_size)
            if diag['fitness']<.15:raise ValueError("Odometry lost: insufficient reliable overlap")
            pose=self.poses[-1]@T;self.last_diagnostics=diag
            self.edges.append((len(self.clouds)-1,len(self.clouds),T,diag['information']))
            for j in range(max(0,len(self.clouds)-20)):
                if np.linalg.norm(pose[:3,3]-self.poses[j][:3,3])<self.loop_distance:
                    loop,ld=point_to_plane_icp(cloud,self.clouds[j],inverse(self.poses[j])@pose,voxel=self.voxel_size)
                    if ld['fitness']>.55 and ld['rmse']<.12:self.edges.append((j,len(self.clouds),loop,ld['information']))
        if self.reference is not None:
            # The global map is measured commissioning data, never a simulator
            # scene mesh. It removes scan-pattern-dependent incremental drift.
            anchored,anchor_diag=point_to_plane_icp(cloud,None,pose,max_distance=.45,
                voxel=self.voxel_size,reference=self.reference)
            if anchor_diag['fitness']<.2 or anchor_diag['rmse']>.15 or anchor_diag['information_rank']<6:
                raise ValueError("Static-map localization lost: insufficient constrained overlap")
            correction=se3_log(inverse(pose)@anchored)
            if np.linalg.norm(correction[3:])>1. or np.linalg.norm(correction[:3])>.4:
                raise ValueError("Static-map correction exceeds localization trust region")
            pose=anchored
            self.anchors.append((len(self.clouds),pose.copy(),anchor_diag['information'].copy()))
            self.last_diagnostics={**self.last_diagnostics,'map_fitness':anchor_diag['fitness'],
                'map_rmse':anchor_diag['rmse'],'map_information_rank':anchor_diag['information_rank'],
                'map_anchored':True,'map_correction_m':float(np.linalg.norm(correction[3:]))}
        pose=_rigid_transform(pose)
        self.clouds.append(cloud);self.poses.append(pose);self.total_scans+=1
        if self.max_keyframes is not None and len(self.clouds)>self.max_keyframes:
            drop=len(self.clouds)-self.max_keyframes
            self.clouds=self.clouds[drop:];self.poses=self.poses[drop:]
            self.edges=[(a-drop,b-drop,T,info) for a,b,T,info in self.edges if a>=drop and b>=drop]
            self.anchors=[(i-drop,T,info) for i,T,info in self.anchors if i>=drop]
            self.dropped_keyframes+=drop
        # Surviving poses remain globally anchored; optimize() fixes the first of
        # this window, rather than silently resetting the map origin after trim.
        self.last_diagnostics={**self.last_diagnostics,'retained_keyframes':len(self.clouds),'dropped_keyframes':self.dropped_keyframes}
        return pose.copy()
    def optimize(self):
        if len(self.poses)<2:return self.poses
        initial=[p.copy() for p in self.poses]
        roots=[]
        def root_information(info,T):
            # Right-invariant graph residuals use the source/body tangent frame;
            # point-to-plane Jacobians use left increments in the target frame.
            A=adjoint(T); transformed=A.T@info@A
            val,vec=np.linalg.eigh(transformed)
            return (vec*np.sqrt(np.maximum(val,1e-6)))@vec.T
        for _,_,T,info in self.edges:roots.append(root_information(info,T))
        anchor_roots=[root_information(info,T) for _,T,info in self.anchors]
        def unpack(x):return [initial[0]]+[initial[i]@se3_exp(x[6*(i-1):6*i]) for i in range(1,len(initial))]
        def residual(x):
            poses=unpack(x)
            pieces=[root@se3_log(inverse(T)@inverse(poses[a])@poses[b]) for (a,b,T,_),root in zip(self.edges,roots)]
            pieces.extend(root@se3_log(inverse(T)@poses[i]) for (i,T,_),root in zip(self.anchors,anchor_roots))
            return np.concatenate(pieces)
        result=least_squares(residual,np.zeros(6*(len(initial)-1)),loss='huber',f_scale=.1,max_nfev=100)
        self.poses=[_rigid_transform(pose) for pose in unpack(result.x)];self.last_diagnostics={**self.last_diagnostics,'graph_cost':float(result.cost),'graph_success':bool(result.success)}
        return [p.copy() for p in self.poses]

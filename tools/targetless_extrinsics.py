"""Targetless camera-to-LiDAR extrinsics (fallback and cross-check): Gemini depth registered to the accumulated L2 cloud.

NOT a board calibration. The output is always `measured: false` with method `targetless_depth_icp`, so
`ingestion.live_source.load_measured_calibration` rejects it for live fusion; use it to sanity-check the
screen-checkerboard result (tools/calibrate_rig.py) or, if the board fails, as a clearly labelled estimate for
offline work. It never writes into config/ (config/calibration_extrinsics_640x400.json belongs to calibrate_rig.py).

Record the rig PARKED (nothing moving, people out of view) for 30-60 s facing a structured scene (walls at two or more
angles, boxes or furniture 1-3 m away ABOVE the LiDAR height, not one flat wall), then:

    .venv\\Scripts\\python.exe tools/targetless_extrinsics.py %USERPROFILE%/aethertwin_rec/self_mask_parked --initial-guess 0.15,0.0,-0.05
        [--initial-rpy 0,0,0] [--compare config/calibration_extrinsics_640x400.json] [--output reports/targetless_calibration/x.json]

--initial-guess is the camera's optical centre in the LiDAR frame (x forward, y left, z up, metres) from a tape
measure, or a JSON file with T_lidar_camera; the camera is assumed to look along LiDAR +x (image right = -y) and
--initial-rpy (degrees, about the LiDAR axes at the camera centre; roll x, pitch y: positive turns the optical
axis down, yaw z: positive turns it left) corrects that nominal orientation.

Method (LiDAR frame, metres):
 1. LiDAR: every sweep's returns with range in --lidar-range are pooled (the L2's non-repetitive pattern densifies
    a static scene over seconds), binned into --lidar-voxel cubes; a voxel must be hit in >= --min-windows of
    --windows equal time windows (drops people walking through); voxel means get PCA normals from --normal-k
    neighbours; noisy or point-like neighbourhoods (curvature > --max-curvature) are dropped. An upright L2 sees only
    above its horizontal plane, so only the camera's view above the LiDAR height can match (fitness counts those).
 2. Camera: up to --depth-frames depth images (hardware aligned to colour, metres; millimetres are detected and
    converted) are reduced to a per-pixel median; unstable pixels and depth edges (flying pixels) are dropped;
    pixels are undistorted with the recorded colour intrinsics, back-projected and voxel-downsampled.
 3. Coarse: from the tape guess, a yaw/pitch grid (+-(--grid-yaw-deg, --grid-pitch-deg), step --grid-step) of
    rotation-only ICPs on a subsample (translation held at the tape guess); the best distinct basins are refined fully
    and the basin explaining the most camera points wins (rival basins are reported; near-equal ones: AMBIGUOUS).
 4. Fine: point-to-plane Gauss-Newton ICP (rotation about the camera centre, correspondence distance shrinking to
    --final-distance, Huber loss --huber, worst (1 - --trim) residuals dropped).
 5. Uncertainty: the whole pipeline again on --subsets disjoint time blocks, and --bootstrap resamples of the
    camera points from the final estimate; the spread is reported (the time blocks are the honest number).
 6. Degeneracy: eigenvalues of the normalised point-to-plane Hessian (rotation scaled by the RMS range, so both
    are in metres); a scene of only a floor and one wall leaves translation along the wall unobservable and the
    verdict says DEGENERATE. A depth-scale check (pose held, diagnostic only) is printed too.
 7. Projection check (independent of the ICP residual): persistent LiDAR returns projected into the camera, z-buffered,
    compared with the median camera depth; <output>.estimate.png colours them green (agree), red (LiDAR nearer),
    blue (LiDAR farther) over the RGB frame for a human look.
--compare FILE reports the rotation/translation difference to another calibration file (e.g. the board result) and
that file's ICP fit and projection check on the same data (and <output>.compared.png). The JSON report goes to --output (default reports/targetless_calibration/<recording>.json); an
existing file is refused without --force; any path inside config/ is always refused.
"""
from __future__ import annotations
import argparse,hashlib,json,math,os,subprocess,sys,time,warnings
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.extract_calibration_targets import OPTICAL_TO_LIDAR,initial_guess  # noqa: E402

METHOD='targetless_depth_icp'
AXES=('x (forward)','y (left)','z (up)')
DEFAULTS=dict(lidar_range=(.3,8.),depth_range=(.3,5.),lidar_voxel=.03,depth_voxel=.04,depth_stride=2,normal_k=20,normal_radius=.2,
              max_curvature=.2,windows=6,min_windows=3,depth_min_share=.5,depth_edge=.04,grid_yaw_deg=10.,grid_pitch_deg=10.,grid_step=5.,coarse_points=2500,
              basins=3,final_distance=.05,huber=.02,trim=.9,subsets=4,bootstrap=8,seed=0)
LIMITS=dict(min_fitness=.5,max_rmse=.03,max_spread_mm=20.,max_spread_deg=1.,min_eigen_ratio=.01,max_offset=.5,max_guess_m=.15,max_guess_deg=15.)


# ---------------------------------------------------------------- geometry
def hat(w):return np.array([[0,-w[2],w[1]],[w[2],0,-w[0]],[-w[1],w[0],0]],float)


def exp_so3(w):
    w=np.asarray(w,float);th=float(np.linalg.norm(w))
    if th<1e-12:return np.eye(3)+hat(w)
    K=hat(w/th);return np.eye(3)+math.sin(th)*K+(1-math.cos(th))*K@K


def rpy_matrix(roll,pitch,yaw):
    """Rz(yaw) Ry(pitch) Rx(roll), degrees, about the LiDAR axes."""
    r,p,y=np.radians([roll,pitch,yaw]);cx,sx,cy,sy,cz,sz=math.cos(r),math.sin(r),math.cos(p),math.sin(p),math.cos(y),math.sin(y)
    return np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]])@np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]])@np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]])


def matrix_rpy(R):
    """(roll, pitch, yaw) degrees with R = Rz Ry Rx."""
    return [float(math.degrees(math.atan2(R[2,1],R[2,2]))),float(math.degrees(math.asin(-np.clip(R[2,0],-1,1)))),float(math.degrees(math.atan2(R[1,0],R[0,0])))]


def rotation_deg(R):return float(np.degrees(np.arccos(np.clip((np.trace(R)-1)/2,-1,1))))


def rigid(R,t):T=np.eye(4);T[:3,:3]=R;T[:3,3]=t;return T


def difference(T,reference):
    """Translation (m, LiDAR frame) and rotation (deg) of T relative to reference."""
    T,reference=np.asarray(T,float),np.asarray(reference,float);d=T[:3,3]-reference[:3,3]
    return {'translation_m':d.tolist(),'translation_norm_m':float(np.linalg.norm(d)),'rotation_deg':rotation_deg(T[:3,:3]@reference[:3,:3].T)}


def guess_transform(spec,rpy=None):
    """4x4 T_lidar_camera from the tape guess ('forward,left,up' or a JSON file) and optional roll,pitch,yaw degrees."""
    T=initial_guess(spec)
    if rpy:T[:3,:3]=rpy_matrix(*rpy)@T[:3,:3]
    return T


# ---------------------------------------------------------------- clouds
def voxel_keys(points,voxel):
    k=np.floor(np.asarray(points,float)/voxel).astype(np.int64)+(1<<20)
    return (k[:,0]<<42)|(k[:,1]<<21)|k[:,2]


def voxel_mean(points,voxel):
    if not len(points):return np.empty((0,3))
    keys=voxel_keys(points,voxel);_,inverse=np.unique(keys,return_inverse=True);count=np.bincount(inverse).astype(float)
    return np.stack([np.bincount(inverse,points[:,i])/count for i in range(3)],axis=1)


def persistent_keys(frames,voxel,windows,min_windows):
    """Voxel keys hit in >= min_windows of `windows` equal blocks of consecutive sweeps (None when off)."""
    if windows<=1 or min_windows<=1 or len(frames)<windows:return None
    seen=[np.unique(voxel_keys(np.concatenate([frames[i] for i in block]),voxel)) for block in np.array_split(np.arange(len(frames)),windows)]
    keys,count=np.unique(np.concatenate(seen),return_counts=True);return keys[count>=min_windows]


def lidar_cloud(frames,opts,persistent=None):
    """Pooled LiDAR returns (list of (N,3) sweeps) cropped to range and to persistent voxels."""
    points=np.concatenate([np.asarray(f,float)[:,:3] for f in frames]) if frames else np.empty((0,3))
    r=np.linalg.norm(points,axis=1);points=points[np.isfinite(r)&(r>=opts['lidar_range'][0])&(r<=opts['lidar_range'][1])]
    if persistent is not None:points=points[np.isin(voxel_keys(points,opts['lidar_voxel']),persistent)]
    return points


def build_target(points,opts):
    """Voxel means with PCA normals; non-planar or sparse neighbourhoods are dropped."""
    from scipy.spatial import cKDTree
    pts=voxel_mean(points,opts['lidar_voxel']);k=min(int(opts['normal_k']),len(pts))
    if k<5:raise ValueError(f'LiDAR cloud too small for normals ({len(pts)} voxels)')
    tree=cKDTree(pts);normals=np.zeros_like(pts);curvature=np.ones(len(pts));ok=np.zeros(len(pts),bool)
    for s in range(0,len(pts),50000):
        dist,idx=tree.query(pts[s:s+50000],k,workers=-1);nb=pts[idx];nb=nb-nb.mean(axis=1,keepdims=True)
        vals,vecs=np.linalg.eigh(np.einsum('nki,nkj->nij',nb,nb)/k)
        normals[s:s+50000]=vecs[:,:,0];curvature[s:s+50000]=vals[:,0]/np.maximum(vals.sum(1),1e-12);ok[s:s+50000]=dist[:,-1]<=opts['normal_radius']
    keep=ok&(curvature<=opts['max_curvature'])
    if keep.sum()<50:raise ValueError(f'Only {int(keep.sum())} planar LiDAR voxels: the scene has too little planar structure')
    return {'points':pts[keep],'normals':normals[keep],'tree':cKDTree(pts[keep]),'voxels':int(len(pts)),'planar_voxels':int(keep.sum()),
            'directions':np.unique(direction_keys(pts)),'max_range':float(np.linalg.norm(pts,axis=1).max())}


def direction_keys(points,bin_deg=2.):
    """Azimuth/elevation bins (LiDAR frame) of points: where the LiDAR has returns at all."""
    az=np.degrees(np.arctan2(points[:,1],points[:,0]))+180;el=np.degrees(np.arctan2(points[:,2],np.hypot(points[:,0],points[:,1])))+90
    return np.floor(az/bin_deg).astype(np.int64)*1000+np.floor(el/bin_deg).astype(np.int64)


def covisible(p,target):
    """Camera points (LiDAR frame) in a direction where the LiDAR returned something, within its range: the overlap."""
    return np.isin(direction_keys(p),target['directions'])&(np.linalg.norm(p,axis=1)<=target['max_range'])


def depth_units(depth):
    """(scale to metres, label): Gemini depth decoded by the driver is metres; raw Y16 would be millimetres."""
    valid=depth[np.isfinite(depth)&(depth>0)]
    if not len(valid):return 1.,'no valid depth'
    median=float(np.median(valid))
    return (1e-3,f'millimetres (median {median:.0f}); converted to metres') if median>50 else (1.,f'metres (median {median:.3f} m)')


def depth_median(frames,opts):
    """Per-pixel median depth (metres) of the frames, NaN where unstable, too rarely valid, out of range or on an edge."""
    stack=np.stack([np.asarray(f,np.float32) for f in frames]);stack=np.where(np.isfinite(stack)&(stack>0),stack,np.nan)
    n=len(stack);count=np.sum(np.isfinite(stack),axis=0)
    with warnings.catch_warnings():  # all-NaN pixels are expected (no return)
        warnings.simplefilter('ignore',RuntimeWarning);med=np.nanmedian(stack,axis=0);mad=np.nanmedian(np.abs(stack-med),axis=0) if n>2 else np.zeros_like(med)
    lo,hi=opts['depth_range'];valid=(count>=max(1,math.ceil(opts['depth_min_share']*n)))&(med>=lo)&(med<=hi)&(np.nan_to_num(mad,nan=1)<=.01+.01*med)
    m=np.where(valid,med,np.nan);edge=np.zeros_like(valid)
    for axis in (0,1):
        for shift in (1,-1):
            other=np.roll(m,shift,axis=axis);jump=np.abs(other-m)>opts['depth_edge']*m;edge|=jump&np.isfinite(other)
    valid&=~edge;valid[[0,-1],:]=False;valid[:,[0,-1]]=False
    return np.where(valid,med,np.nan)


def back_project(depth,K,distortion,stride=1):
    """(N,3) camera-optical points (x right, y down, z forward) from a metric depth image aligned to K."""
    v,u=np.mgrid[0:depth.shape[0]:stride,0:depth.shape[1]:stride];z=depth[v,u];ok=np.isfinite(z)&(z>0);u,v,z=u[ok].astype(float),v[ok].astype(float),z[ok]
    K=np.asarray(K,float);distortion=np.zeros(5) if distortion is None else np.asarray(distortion,float)
    if np.any(distortion!=0) and len(u):
        import cv2
        xy=cv2.undistortPoints(np.c_[u,v][:,None].astype(np.float64),K,distortion).reshape(-1,2)
    else:xy=np.c_[(u-K[0,2])/K[0,0],(v-K[1,2])/K[1,1]]
    return np.c_[xy*z[:,None],z]


def build_source(depth_frames,K,distortion,opts):
    med=depth_median(depth_frames,opts);points=back_project(med,K,distortion,opts['depth_stride'])
    pts=voxel_mean(points,opts['depth_voxel'])
    if len(pts)<200:raise ValueError(f'Only {len(pts)} camera voxels with stable depth in range {opts["depth_range"]}')
    return pts,{'frames':len(depth_frames),'valid_pixels':int(np.isfinite(med).sum()),'points':int(len(pts)),'median_depth_m':float(np.nanmedian(med))}


# ---------------------------------------------------------------- ICP
def correspond(source,target,R,t,distance):
    p=source@R.T+t;dist,idx=target['tree'].query(p,distance_upper_bound=distance,workers=-1 if len(p)>50000 else 1);ok=np.isfinite(dist)  # threads only pay off on big queries
    n=target['normals'][idx[ok]];r=np.einsum('ij,ij->i',p[ok]-target['points'][idx[ok]],n)
    return p[ok],n,r,ok


def robust_weights(r,huber,trim):
    a=np.abs(r);keep=a<=np.quantile(a,trim) if 0<trim<1 and len(a)>20 else np.ones(len(a),bool)
    return np.where(keep,np.minimum(1.,huber/np.maximum(a,1e-12)),0.)


def icp(source,target,T,schedule,*,iterations=20,huber=.02,trim=.9,rotation_only=False):
    """Point-to-plane Gauss-Newton; the update is a rotation about the current camera centre plus a translation (held when rotation_only)."""
    R=np.asarray(T,float)[:3,:3].copy();t=np.asarray(T,float)[:3,3].copy()
    for distance in schedule:
        for _ in range(iterations):
            p,n,r,ok=correspond(source,target,R,t,distance)
            if len(r)<12:break
            w=robust_weights(r,huber,trim);J=np.cross(p-t,n) if rotation_only else np.c_[np.cross(p-t,n),n];H=(J*w[:,None]).T@J;g=J.T@(w*r)
            delta=-np.linalg.solve(H+1e-9*max(np.trace(H),1e-12)*np.eye(len(H)),g);delta=np.r_[delta,np.zeros(3)] if rotation_only else delta
            step=float(np.linalg.norm(delta[:3]))
            if step>.1 or np.linalg.norm(delta[3:])>.2:delta*=min(.1/max(step,1e-12),.2/max(float(np.linalg.norm(delta[3:])),1e-12),1.)
            R=exp_so3(delta[:3])@R;t=t+delta[3:]
            if np.linalg.norm(delta[:3])<1e-6 and np.linalg.norm(delta[3:])<1e-6:break
    return rigid(R,t)


def evaluate(source,target,T,distance,*,huber=.02,trim=.9):
    """Fit of T: share of the co-visible camera points (fitness) and of all of them (fitness_all) with a LiDAR plane
    within `distance`, point-to-plane RMSE, and the Hessian."""
    R,t=np.asarray(T,float)[:3,:3],np.asarray(T,float)[:3,3];p,n,r,ok=correspond(source,target,R,t,distance)
    seen=covisible(source@R.T+t,target);share={'fitness':float(ok.sum()/max(seen.sum(),1)),'fitness_all':float(ok.mean()) if len(ok) else 0.,
                                               'covisible_share':float(seen.mean()) if len(seen) else 0.}
    if len(r)<12:return {**share,'inlier_rmse_m':None,'correspondences':int(len(r)),'H':None}
    w=robust_weights(r,huber,trim);J=np.c_[np.cross(p-t,n),n]
    return {**share,'inlier_rmse_m':float(np.sqrt(np.mean(r**2))),'trimmed_rmse_m':float(np.sqrt(np.mean(r[w>0]**2))) if np.any(w>0) else None,
            'median_abs_residual_m':float(np.median(np.abs(r))),'correspondences':int(len(r)),'H':(J*w[:,None]).T@J,'weight':float(w.sum()),
            'length_m':float(np.sqrt(np.mean(np.sum((p-t)**2,axis=1)))),'p':p,'n':n,'r':r,'w':w}


def observability(fit,min_ratio):
    """Normalised point-to-plane Hessian (rotation scaled by the RMS range so both blocks are in metres)."""
    if fit.get('H') is None:return {'degenerate':True,'why':'too few correspondences'}
    L=fit['length_m'];D=np.diag([1/L]*3+[1]*3);H=D@fit['H']@D/max(fit['weight'],1e-12);vals,vecs=np.linalg.eigh(H)
    ratio=float(vals[0]/max(vals[-1],1e-18));weak=vecs[:,0];rot=float(np.sum(weak[:3]**2))
    part=weak[:3] if rot>.5 else weak[3:];axis=int(np.argmax(np.abs(part)))
    words=('rotation about' if rot>.5 else 'translation along')+f' LiDAR {AXES[axis]}'
    tvals=np.linalg.eigvalsh(H[3:,3:]);rvals=np.linalg.eigvalsh(H[:3,:3])
    return {'degenerate':ratio<min_ratio,'eigen_ratio_min_over_max':ratio,'limit':min_ratio,'eigenvalues':vals.tolist(),'rms_range_m':L,
            'weakest_direction':words,'weakest_eigenvector_[rot_x,rot_y,rot_z,t_x,t_y,t_z]':weak.tolist(),
            'translation_block_eigenvalues':tvals.tolist(),'rotation_block_eigenvalues':rvals.tolist(),
            'note':'translation block = share of plane-normal support per direction (sums to 1); a floor plus one wall leaves one translation near 0'}


def scale_check(fit,centre):
    """Depth/LiDAR scale ratio with the pose held (diagnostic): the s minimising sum w (n.(R s p + t - q))^2."""
    if fit.get('H') is None:return None
    a=np.einsum('ij,ij->i',fit['p']-centre,fit['n']);return float(1-np.sum(fit['w']*a*fit['r'])/max(np.sum(fit['w']*a*a),1e-12))


def register(source,target,guess,opts,rng):
    """Rotation-only ICP from a yaw/pitch grid around the guess (translation held at the tape guess), then full 6-DoF
    refinement of the best distinct basins; the basin explaining the most camera points wins."""
    sub=source[rng.choice(len(source),min(int(opts['coarse_points']),len(source)),replace=False)];s=float(opts['grid_step'])
    grid=lambda g:np.arange(-g,g+1e-9,s) if s>0 and g>0 else np.zeros(1)
    coarse=[]
    for yaw in grid(float(opts['grid_yaw_deg'])):
        for pitch in grid(float(opts['grid_pitch_deg'])):
            T0=guess.copy();T0[:3,:3]=rpy_matrix(0,pitch,yaw)@guess[:3,:3]
            T=icp(sub,target,T0,[.5,.25,.12],iterations=8,huber=opts['huber'],trim=opts['trim'],rotation_only=True);e=evaluate(sub,target,T,opts['final_distance'])
            coarse.append({'T':T,'fitness':e['fitness_all'],'rmse':e['inlier_rmse_m'] or 1.,'offset_deg':[float(yaw),float(pitch)]})
    coarse.sort(key=lambda c:(-round(c['fitness'],3),c['rmse']));seeds=[]
    for c in coarse:
        if all(difference(c['T'],d['T'])['translation_norm_m']>.03 or difference(c['T'],d['T'])['rotation_deg']>1. for d in seeds):seeds.append(c)
        if len(seeds)>=int(opts['basins']):break
    finals=[]
    for c in seeds:
        T=icp(source,target,c['T'],[.2,.1,opts['final_distance']],iterations=30,huber=opts['huber'],trim=opts['trim'])
        e=evaluate(source,target,T,opts['final_distance'],huber=opts['huber'],trim=opts['trim']);finals.append({'T':T,'fit':e,'offset_deg':c['offset_deg']})
    finals.sort(key=lambda f:(-round(f['fit']['fitness_all'],3),f['fit']['inlier_rmse_m'] or 1.));best=finals[0]
    rivals=[{'offset_deg':f['offset_deg'],'fitness':f['fit']['fitness_all'],'inlier_rmse_m':f['fit']['inlier_rmse_m'],**difference(f['T'],best['T'])}
            for f in finals[1:] if difference(f['T'],best['T'])['translation_norm_m']>.02 or difference(f['T'],best['T'])['rotation_deg']>.5]
    same=sum(difference(c['T'],best['T'])['rotation_deg']<=1.5 for c in coarse)  # coarse starts are rotation-only
    ambiguous=[r for r in rivals if r['fitness']>=.95*best['fit']['fitness_all']]
    _,pitch,yaw=matrix_rpy(best['T'][:3,:3]@guess[:3,:3].T)  # result relative to the guess, about the LiDAR axes
    outside=[abs(a)>float(opts[k])+float(opts['grid_step']) and float(opts[k])<180 for a,k in ((yaw,'grid_yaw_deg'),(pitch,'grid_pitch_deg'))]
    return best['T'],best['fit'],{'grid_candidates':len(coarse),'coarse_starts_reaching_best_rotation':int(same),'best_offset_deg':best['offset_deg'],
                                  'result_yaw_pitch_from_guess_deg':[yaw,pitch],'result_outside_grid':bool(any(outside)),'rival_basins':rivals,'ambiguous':bool(ambiguous)}


def spread(results,reference):
    if not results:return None
    d=[difference(T,reference) for T in results];t=np.array([x['translation_m'] for x in d])
    return {'runs':len(d),'translation_max_mm':1000*max(x['translation_norm_m'] for x in d),'rotation_max_deg':max(x['rotation_deg'] for x in d),
            'translation_std_mm':(1000*t.std(axis=0)).tolist(),'per_run':d}


def summarise_fit(e):return {k:e.get(k) for k in ('fitness','fitness_all','covisible_share','inlier_rmse_m','trimmed_rmse_m','median_abs_residual_m','correspondences')}


def estimate(lidar_frames,depth_frames,K,distortion,guess,*,compare=None,**overrides):
    """Main entry (arrays only): lidar_frames = list of (N,>=3) sweeps in time order, depth_frames = list of metric depth images."""
    opts={**DEFAULTS,**{k:v for k,v in overrides.items() if k in DEFAULTS}};limits={**LIMITS,**{k:v for k,v in overrides.items() if k in LIMITS}}
    rng=np.random.default_rng(opts['seed']);started=time.perf_counter();guess=np.asarray(guess,float)
    persistent=persistent_keys([np.asarray(f,float)[:,:3] for f in lidar_frames],opts['lidar_voxel'],opts['windows'],opts['min_windows'])
    target=build_target(lidar_cloud(lidar_frames,opts,persistent),opts);source,source_info=build_source(depth_frames,K,distortion,opts)
    T,fit,basins=register(source,target,guess,opts,rng);obs=observability(fit,limits['min_eigen_ratio'])
    scale=scale_check(fit,T[:3,3])
    temporal=[];temporal_errors=[]
    if opts['subsets']>1:
        blocks_l=np.array_split(np.arange(len(lidar_frames)),opts['subsets']);blocks_d=np.array_split(np.arange(len(depth_frames)),opts['subsets'])
        for bl,bd in zip(blocks_l,blocks_d):
            try:
                if not len(bl) or not len(bd):raise ValueError('empty block')
                tg=build_target(lidar_cloud([lidar_frames[i] for i in bl],opts,persistent),opts);src,_=build_source([depth_frames[i] for i in bd],K,distortion,opts)
                temporal.append(register(src,tg,guess,{**opts,'basins':1},rng)[0])
            except ValueError as exc:temporal_errors.append(str(exc))
    boot=[]
    for _ in range(int(opts['bootstrap'])):
        src=source[rng.integers(0,len(source),len(source))]
        boot.append(icp(src,target,T,[2*opts['final_distance'],opts['final_distance']],iterations=20,huber=opts['huber'],trim=opts['trim']))
    report={'T_lidar_camera':T.tolist(),'camera_origin_in_lidar_m':T[:3,3].tolist(),
            'rpy_deg_from_nominal_optical_axes':matrix_rpy(T[:3,:3]@OPTICAL_TO_LIDAR.T),'fit':summarise_fit(fit),'observability':obs,
            'depth_scale_ratio_pose_held':scale,'basins':basins,'from_guess':difference(T,guess),
            'spread':{'temporal_subsets':spread(temporal,T),'temporal_subset_errors':temporal_errors,'bootstrap':spread(boot,T)},
            'lidar':{'sweeps':len(lidar_frames),'voxels':target['voxels'],'planar_voxels':target['planar_voxels'],
                     'persistence':None if persistent is None else {'windows':opts['windows'],'min_windows':opts['min_windows']}},
            'camera':source_info,'parameters':{k:(list(v) if isinstance(v,tuple) else v) for k,v in opts.items()},'limits':limits}
    if compare is not None:
        C=np.asarray(compare,float);e=evaluate(source,target,C,opts['final_distance'],huber=opts['huber'],trim=opts['trim'])
        report['comparison']={**difference(T,C),'note':'estimate minus compared file (LiDAR frame)','compared_fit_on_this_data':summarise_fit(e)}
    report['verdict']=verdict(report,limits);report['runtime_s']=float(time.perf_counter()-started);return report


def verdict(report,limits):
    reasons=[];fit=report['fit'];obs=report['observability'];status='PLAUSIBLE'
    if obs.get('degenerate'):
        status='DEGENERATE';reasons.append(f"scene does not constrain {obs.get('weakest_direction','all six degrees of freedom')} "
            f"(eigen ratio {obs.get('eigen_ratio_min_over_max',0):.4f} < {limits['min_eigen_ratio']}): add structure (boxes, a corner, objects at several depths)")
    if fit['fitness']<limits['min_fitness'] or fit['inlier_rmse_m'] is None or fit['inlier_rmse_m']>limits['max_rmse']:
        status='POOR_FIT' if status=='PLAUSIBLE' else status
        reasons.append(f"fitness {fit['fitness']:.2f} (>= {limits['min_fitness']}) / inlier RMSE {fit['inlier_rmse_m'] if fit['inlier_rmse_m'] is None else round(fit['inlier_rmse_m'],4)} m (<= {limits['max_rmse']})")
    sp=report['spread']['temporal_subsets'];parts={'rotation':None,'translation':None}
    if sp is None:reasons.append('no time-block subsets: spread unknown')
    else:
        parts={'rotation':sp['rotation_max_deg']<=limits['max_spread_deg'],'translation':sp['translation_max_mm']<=limits['max_spread_mm']}
        if not all(parts.values()):
            status='INCONSISTENT' if status=='PLAUSIBLE' else status
            reasons.append(f"time blocks disagree by up to {sp['translation_max_mm']:.1f} mm / {sp['rotation_max_deg']:.2f} deg (<= {limits['max_spread_mm']} mm / {limits['max_spread_deg']} deg): "
                           f"rotation {'consistent' if parts['rotation'] else 'NOT consistent'}, translation {'consistent' if parts['translation'] else 'NOT consistent'}")
    if report['basins']['ambiguous']:
        status='AMBIGUOUS' if status=='PLAUSIBLE' else status;reasons.append('another grid basin fits almost as well: re-measure the tape guess')
    if report['basins'].get('result_outside_grid'):
        reasons.append('warning: the result turned beyond the searched yaw/pitch grid; rerun with a wider --grid-yaw-deg/--grid-pitch-deg or a corrected --initial-rpy to confirm it')
    offset=float(np.linalg.norm(report['camera_origin_in_lidar_m']))
    if offset>limits['max_offset']:
        status='IMPLAUSIBLE' if status=='PLAUSIBLE' else status;reasons.append(f'camera {offset:.2f} m from the LiDAR origin (> {limits["max_offset"]} m)')
    g=report['from_guess']
    if g['translation_norm_m']>limits['max_guess_m'] or g['rotation_deg']>limits['max_guess_deg']:
        reasons.append(f"warning: {g['translation_norm_m']*100:.1f} cm / {g['rotation_deg']:.1f} deg from the tape guess (check the guess or the result)")
    return {'status':status,'reasons':reasons,'time_block_consistent':parts,'evidence':'TARGETLESS ESTIMATE (measured: false); not a board calibration, never used by live fusion'}


# ---------------------------------------------------------------- recording and output
def load_recording(path,*,start_s=0.,duration_s=None,depth_frames=30,intrinsics=None):
    """LiDAR sweeps, up to `depth_frames` evenly spaced depth images (metres) and the camera intrinsics of a raw recording."""
    from ingestion.recorder_replayer import ReplaySource
    source=ReplaySource(path);total=len(source);stride=max(1,total//max(1,depth_frames));lidar=[];depth=[];info={'frames_in_recording':total}
    K=dist=None;image_size=None;t0=None;poses=[];units=None;alignment=None;camera_frame=None;intrinsics_source=None;rgb=None
    if intrinsics:
        data=json.loads(Path(intrinsics).read_text(encoding='utf-8'));K=np.asarray(data['K'],float);dist=np.asarray(data.get('distortion',[0]*5),float)
        intrinsics_source=str(intrinsics);camera_frame='as given by --intrinsics'
    try:
        for index,frame in enumerate(source):
            t0=frame.timestamp if t0 is None else t0;rel=frame.timestamp-t0
            if rel<start_s or (duration_s is not None and rel>start_s+duration_s):continue
            lidar.append(np.asarray(frame.lidar_points[:,:3],np.float32));poses.append(np.asarray(frame.sensor_pose_world,float))
            if K is None:
                validation=(frame.diagnostics.get('camera_calibration_validation') or {});color=validation.get('color') or {};dep=validation.get('depth') or {}
                profiles=frame.diagnostics.get('camera_profiles') or {};alignment=profiles.get('alignment')
                if 'K' in color:
                    K=np.asarray(color['K'],float);dist=np.asarray(color.get('distortion',[0]*5),float)
                    intrinsics_source='SDK factory colour intrinsics recorded in the frame diagnostics'
                    aligned=alignment in ('hardware','software') or ('K' in dep and np.allclose(dep['K'],color['K']))
                    camera_frame='colour optical frame (depth aligned to colour: '+str(alignment)+')' if aligned else 'UNKNOWN: depth may not be aligned to colour'
                elif frame.calibration is not None:
                    K=np.asarray(frame.calibration.K,float);dist=np.asarray(frame.calibration.distortion,float)
                    intrinsics_source=f'recorded calibration {frame.calibration.calibration_id}';camera_frame='colour optical frame (recorded calibration)'
            if frame.depth is not None and index%stride==0 and len(depth)<depth_frames:
                d=np.asarray(frame.depth,np.float32)
                if units is None:units=depth_units(d)
                depth.append(d*np.float32(units[0]));image_size=[int(d.shape[1]),int(d.shape[0])]
                if frame.rgb is not None and len(depth)<=depth_frames//2+1:rgb=np.asarray(frame.rgb)  # middle sampled frame, for the overlay
    finally:source.close()
    if K is None:raise ValueError('No camera intrinsics in the recording; pass --intrinsics FILE (JSON with K and distortion)')
    if not depth:raise ValueError('No depth frames in the selected part of the recording')
    moved=max((float(np.linalg.norm(p[:3,3]-poses[0][:3,3])) for p in poses),default=0.)
    half=max(1,len(depth)//3);third=lambda frames:np.nanmedian(np.where(np.stack(frames)>0,np.stack(frames),np.nan),axis=0)
    with warnings.catch_warnings():warnings.simplefilter('ignore',RuntimeWarning);early,late=third(depth[:half]),third(depth[-half:])
    info.update({'lidar_sweeps':len(lidar),'depth_frames':len(depth),'depth_units':units[1] if units else None,'image_size':image_size,
                 'intrinsics_source':intrinsics_source,'camera_frame':camera_frame,'alignment':alignment,
                 'stationarity':{'sensor_pose_world_max_move_m':moved,'depth_first_vs_last_third_median_abs_change_m':float(np.nanmedian(np.abs(early-late)))}})
    return lidar,depth,K,dist,image_size,info,rgb


def projection_check(lidar_frames,depth,T,K,distortion,*,max_range=8.,max_points=2000000,agree=.05,seed=0):
    """LiDAR returns projected into the camera with T (4x4 cell z-buffer), compared with the median camera depth there.

    Independent of the ICP residual (LiDAR to pixel instead of camera point to LiDAR plane). Returns (summary, pixels, z, delta)."""
    import cv2
    points=np.concatenate([np.asarray(f,float)[:,:3] for f in lidar_frames]);r=np.linalg.norm(points,axis=1);points=points[(r>.3)&(r<max_range)]
    if len(points)>max_points:points=points[np.random.default_rng(seed).choice(len(points),max_points,replace=False)]
    Ti=np.linalg.inv(np.asarray(T,float));cam=points@Ti[:3,:3].T+Ti[:3,3];cam=cam[cam[:,2]>.2];h,w=depth.shape
    uv=cv2.projectPoints(cam,np.zeros(3),np.zeros(3),np.asarray(K,float),np.zeros(5) if distortion is None else np.asarray(distortion,float))[0].reshape(-1,2)
    ok=(uv[:,0]>=0)&(uv[:,0]<w)&(uv[:,1]>=0)&(uv[:,1]<h);uv=uv[ok].astype(int);z=cam[ok,2]
    # robust z-buffer on 4x4-pixel cells: the front surface is the 20th-percentile range of the cell's returns (isolated near noise does not hide it)
    cell=(uv[:,1]//4)*(w//4+1)+uv[:,0]//4;order=np.lexsort((z,cell));counts=np.bincount(cell,minlength=(h//4+1)*(w//4+1));starts=np.cumsum(counts)-counts
    front_z=np.full(len(counts),np.inf);used=counts>0;front_z[used]=z[order][starts[used]+(.2*(counts[used]-1)).astype(int)]
    front=z<=front_z[cell]*1.05+.05;uv,z=uv[front],z[front];delta=z-depth[uv[:,1],uv[:,0]];valid=np.isfinite(delta)
    d=delta[valid];summary={'projected_returns':int(len(z)),'with_camera_depth':int(valid.sum()),'agree_within_m':agree,
        'agree_share':float(np.mean(np.abs(d)<=agree)) if len(d) else None,'lidar_nearer_share':float(np.mean(d<-agree)) if len(d) else None,
        'lidar_farther_share':float(np.mean(d>agree)) if len(d) else None,'median_lidar_minus_camera_m':float(np.median(d)) if len(d) else None,
        'note':'LiDAR range minus median camera depth at the projected pixel; nearer/farther returns are parallax, transients or misalignment'}
    return summary,uv,z,delta


def overlay(rgb,check,path,title):
    """PNG: green = LiDAR agrees with the camera depth, red = LiDAR nearer, blue = LiDAR farther, grey = no camera depth."""
    import cv2
    summary,uv,z,delta=check;image=(cv2.cvtColor(np.asarray(rgb,np.uint8),cv2.COLOR_RGB2BGR)*.55).astype(np.uint8);a=summary['agree_within_m']
    colour=np.where(~np.isfinite(delta)[:,None],[160,160,160],np.where((np.abs(delta)<=a)[:,None],[0,230,0],np.where((delta<-a)[:,None],[0,0,255],[255,128,0])))
    image[uv[:,1],uv[:,0]]=colour.astype(np.uint8)
    for i,text in enumerate((title,'green: LiDAR = camera depth (+-%.0f cm), red: LiDAR nearer, blue: farther'%(100*a))):
        cv2.putText(image,text,(8,16+16*i),cv2.FONT_HERSHEY_SIMPLEX,.42,(255,255,255),1,cv2.LINE_AA)
    Path(path).parent.mkdir(parents=True,exist_ok=True);cv2.imwrite(str(path),image)


def git_commit():
    try:return subprocess.run(['git','-C',str(ROOT),'rev-parse','HEAD'],capture_output=True,text=True,timeout=10).stdout.strip() or None
    except (OSError,subprocess.SubprocessError):return None


def capture_summary(recording):
    path=Path(recording)/'capture_report.json'
    if not path.is_file():return None
    r=json.loads(path.read_text(encoding='utf-8'));return {k:r.get(k) for k in ('label','date_utc','motion','frames','bringup_uncalibrated','error')}


def document(report,K,distortion,image_size,*,recording=None,info=None,initial=None):
    """Calibration-file schema (K, distortion, T_lidar_camera, image_size, calibration_id, measured) with honesty flags."""
    identity=json.dumps({'K':np.asarray(K).tolist(),'T_lidar_camera':report['T_lidar_camera']},sort_keys=True).encode('utf-8')
    manifest=Path(recording)/'manifest.json' if recording else None
    return {'schema_version':1,'calibration_id':'targetless-depth-icp-NOT-MEASURED-'+hashlib.sha256(identity).hexdigest()[:16],'measured':False,
            'method':METHOD,'board_calibration':False,'image_size':image_size,'K':np.asarray(K,float).tolist(),
            'distortion':np.zeros(5).tolist() if distortion is None else np.asarray(distortion,float).tolist(),'T_lidar_camera':report['T_lidar_camera'],
            'notes':'Targetless estimate (Gemini depth registered to the accumulated L2 cloud by point-to-plane ICP). measured: false, so live fusion '
                    'rejects it. A cross-check or labelled fallback only; never a substitute for config/calibration_extrinsics_640x400.json.',
            'targetless':{'tool':'tools/targetless_extrinsics.py','created_utc':datetime.now(timezone.utc).isoformat(),'git_commit':git_commit(),
                          'recording':None if recording is None else str(recording),
                          'recording_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest and manifest.is_file() else None,
                          'capture_report':capture_summary(recording) if recording else None,'recording_info':info,'initial_guess':initial,**report}}


def refuse_output(output,force):
    """Reason to refuse writing `output`, or None. Anything inside config/ is refused even with --force."""
    resolved=Path(output).resolve()
    if resolved.is_relative_to((ROOT/'config').resolve()):return f'{output} is inside config/: targetless estimates never go there (use reports/ or another --output)'
    if resolved.exists() and not force:return f'{output} exists; pass --force to replace it'
    return None


def write_json(document_,output):
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);temporary=output.with_name(output.name+'.tmp')
    temporary.write_text(json.dumps(document_,indent=2,default=float),encoding='utf-8');os.replace(temporary,output)


def compare_transform(spec):
    data=json.loads(Path(spec).read_text(encoding='utf-8'));T=np.asarray(data['T_lidar_camera'],float)
    if T.shape!=(4,4):raise ValueError(f'{spec}: T_lidar_camera must be 4x4')
    return T,{'file':str(spec),'calibration_id':data.get('calibration_id'),'measured':data.get('measured'),'method':data.get('method')}


def summary_text(report,output=None):
    T=np.asarray(report['T_lidar_camera']);fit=report['fit'];obs=report['observability'];v=report['verdict'];sp=report['spread']
    lines=[f"Targetless camera-LiDAR estimate: {v['status']}  ({v['evidence']})",
           f"  camera origin in LiDAR frame [fwd, left, up]: {np.round(T[:3,3],4).tolist()} m; rpy from nominal axes {np.round(report['rpy_deg_from_nominal_optical_axes'],2).tolist()} deg",
           f"  fit: {100*fit['fitness']:.1f}% of the co-visible camera points ({100*fit['covisible_share']:.0f}% of all) within {report['parameters']['final_distance']} m of a LiDAR plane, "
           f"point-to-plane RMSE {fit['inlier_rmse_m']*1000 if fit['inlier_rmse_m'] is not None else float('nan'):.1f} mm",
           f"  observability: min/max eigen ratio {obs.get('eigen_ratio_min_over_max',0):.4f} (limit {obs.get('limit')}), weakest: {obs.get('weakest_direction')}"]
    if sp['temporal_subsets']:lines.append(f"  time-block spread ({sp['temporal_subsets']['runs']} blocks): {sp['temporal_subsets']['translation_max_mm']:.1f} mm / {sp['temporal_subsets']['rotation_max_deg']:.2f} deg max")
    if sp['bootstrap']:lines.append(f"  bootstrap spread ({sp['bootstrap']['runs']}): {sp['bootstrap']['translation_max_mm']:.1f} mm / {sp['bootstrap']['rotation_max_deg']:.3f} deg max (optimistic)")
    if report.get('depth_scale_ratio_pose_held') is not None:lines.append(f"  depth/LiDAR scale ratio (pose held, diagnostic): {report['depth_scale_ratio_pose_held']:.4f}")
    if 'comparison' in report:
        c=report['comparison'];cf=c['compared_fit_on_this_data']
        lines.append(f"  vs {c.get('file','compared file')}: {1000*c['translation_norm_m']:.1f} mm {np.round(np.multiply(c['translation_m'],1000),1).tolist()} mm, {c['rotation_deg']:.2f} deg; "
                     f"its fit here {100*cf['fitness']:.1f}% / {cf['inlier_rmse_m']*1000 if cf['inlier_rmse_m'] is not None else float('nan'):.1f} mm")
    for name,c in (report.get('projection_check') or {}).items():
        if c['agree_share'] is not None:lines.append(f"  projection check ({name}): {100*c['agree_share']:.1f}% of {c['with_camera_depth']} projected LiDAR returns within {100*c['agree_within_m']:.0f} cm of the camera depth "
                                                    f"({100*c['lidar_nearer_share']:.1f}% nearer, {100*c['lidar_farther_share']:.1f}% farther)")
    for name,path in (report.get('overlay_png') or {}).items():lines.append(f'  overlay ({name}): {path}')
    lines+=[f'  - {r}' for r in v['reasons']]
    if output:lines.append(f'  wrote {output} (measured: false, method {METHOD})')
    return '\n'.join(lines)


def parser():
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0],formatter_class=argparse.RawDescriptionHelpFormatter,epilog=__doc__.split('\n',1)[1])
    p.add_argument('recording',type=Path);p.add_argument('--initial-guess',required=True,help="camera optical centre 'forward,left,up' metres in the LiDAR frame, or a JSON file with T_lidar_camera")
    p.add_argument('--initial-rpy',help="'roll,pitch,yaw' degrees about the LiDAR axes applied to the nominal camera orientation")
    p.add_argument('--output',type=Path,help='Report JSON (default reports/targetless_calibration/<recording>.json); never inside config/')
    p.add_argument('--force',action='store_true',help='Replace an existing --output');p.add_argument('--compare',type=Path,help='Calibration JSON to cross-check (e.g. the board result)')
    p.add_argument('--no-overlay',action='store_true',help='Skip the <output>.estimate.png (and .compared.png) LiDAR-on-RGB pictures')
    p.add_argument('--intrinsics',type=Path,help='JSON with K and distortion when the recording has none')
    p.add_argument('--start-s',type=float,default=0.);p.add_argument('--duration-s',type=float);p.add_argument('--depth-frames',type=int,default=30)
    p.add_argument('--lidar-range',type=float,nargs=2,default=DEFAULTS['lidar_range']);p.add_argument('--depth-range',type=float,nargs=2,default=DEFAULTS['depth_range'])
    for name in ('lidar_voxel','depth_voxel','normal_radius','max_curvature','depth_edge','grid_yaw_deg','grid_pitch_deg','grid_step','final_distance','huber','trim'):
        p.add_argument('--'+name.replace('_','-'),type=float,default=DEFAULTS[name])
    for name in ('depth_stride','normal_k','windows','min_windows','coarse_points','basins','subsets','bootstrap','seed'):p.add_argument('--'+name.replace('_','-'),type=int,default=DEFAULTS[name])
    for name,value in LIMITS.items():p.add_argument('--'+name.replace('_','-'),type=float,default=value)
    return p


def main(argv=None):
    args=parser().parse_args(argv);output=args.output or ROOT/'reports'/'targetless_calibration'/f'{Path(args.recording).name}.json'
    why=refuse_output(output,args.force)
    if why:print(f'Refusing: {why}.',file=sys.stderr);return 2
    rpy=[float(v) for v in args.initial_rpy.split(',')] if args.initial_rpy else None
    if rpy is not None and len(rpy)!=3:print('Refusing: --initial-rpy needs roll,pitch,yaw',file=sys.stderr);return 2
    guess=guess_transform(args.initial_guess,rpy);compare=compare_info=None
    if args.compare:compare,compare_info=compare_transform(args.compare)
    started=time.perf_counter()
    lidar,depth,K,dist,image_size,info,rgb=load_recording(args.recording,start_s=args.start_s,duration_s=args.duration_s,depth_frames=args.depth_frames,intrinsics=args.intrinsics)
    print(f"loaded {info['lidar_sweeps']} sweeps and {info['depth_frames']} depth frames ({info['depth_units']}; {info['camera_frame']}) in {time.perf_counter()-started:.1f} s",flush=True)
    options={k:getattr(args,k) for k in list(DEFAULTS)+list(LIMITS) if hasattr(args,k)};options['lidar_range']=tuple(args.lidar_range);options['depth_range']=tuple(args.depth_range)
    report=estimate(lidar,depth,K,dist,guess,compare=compare,**options)
    if compare_info:report['comparison'].update(compare_info)
    opts={**DEFAULTS,**options};median=depth_median(depth,opts);checks={'estimate':report['T_lidar_camera']}
    kept=[lidar_cloud(lidar,opts,persistent_keys(lidar,opts['lidar_voxel'],opts['windows'],opts['min_windows']))]  # same persistence filter as the fit
    if compare is not None:checks['compared']=compare
    for name,T in checks.items():
        check=projection_check(kept,median,T,K,dist);report.setdefault('projection_check',{})[name]=check[0]
        if rgb is not None and not args.no_overlay:
            picture=Path(output).with_suffix(f'.{name}.png');report.setdefault('overlay_png',{})[name]=str(picture)
            overlay(rgb,check,picture,'targetless estimate, measured: false' if name=='estimate' else f'compared: {args.compare}')
    report['total_runtime_s']=float(time.perf_counter()-started)
    doc=document(report,K,dist,image_size,recording=args.recording,info=info,initial={'spec':args.initial_guess,'rpy_deg':rpy,'T_lidar_camera':guess.tolist()})
    write_json(doc,output);print(summary_text(report,output))
    print(f"  runtime {report['total_runtime_s']:.1f} s")
    return 0 if report['verdict']['status']=='PLAUSIBLE' else 1


if __name__=='__main__':sys.exit(main())

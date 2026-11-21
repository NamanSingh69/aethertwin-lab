"""SYNTHETIC mobile recording from a real STATIONARY one, with ground truth (never a hardware result).

The real parked L2 cloud (dense after ~60 s of the non-repetitive pattern) is the world. Every output
frame reuses one real frame's timing: sweep start/duration (incl. the real start jitter and sub-ms
overlaps), the real per-return time column, the real ray directions (the L2 pattern), the frame's real
IMU timestamps and camera timestamp. Only the content changes: each ray is cast from the ground-truth
pose AT ITS OWN return time (rolling shutter, no deskew) against the world cloud (sphere tracing on a
KD-tree), and the IMU is the trajectory's exact gyro/specific force (gravity along the measured still
direction, lever arm from imu_to_lidar) plus the real still IMU's residual at the same timestamp, so the
real bias (~0.71 m/s^2 on z) and real noise ride along. Returns inside --self-radius of the parked
sensor stay rigid with the rig (the body/mount is in view; self-mask is off until measured). Camera
frames (optional) are a z-buffer of the world cloud at the camera timestamp with a SYNTHETIC pinhole
calibration. The ground-truth trajectory is written beside the recording (ground_truth.npz); frames
never carry it (sensor_pose_world stays identity as on the rig).

Limits: the world contains only what the parked sensor saw (upper hemisphere; nothing below the L2's
horizon, occluded regions are see-through), so this tests the mobile build path, timing and IMU
handling on real-shaped data, not the map quality of a real moving scan.

    python tools/synthesize_mobile_recording.py --template C:/rec/wifi_stationary_60s --output C:/rec/synthetic_mobile_60s
    python tools/synthesize_mobile_recording.py evaluate --recording C:/rec/synthetic_mobile_60s --twin C:/twin --json out.json
"""
from __future__ import annotations
import argparse,json,sys,time
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
from scipy.interpolate import CubicSpline
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import cKDTree
from scipy.spatial.transform import Rotation

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
G=9.80665;LABEL='synthetic mobile from real stationary'
# Default loop in the room frame of wifi_stationary_60s (wall principal axes, signed so the bulk of the room is
# negative), through the free floor at sensor height (the desk clutter around the parked spot is avoided): a turn
# in the open end, along the room, a loop at the far end and back to the start. ~25.7 m, ~54 s at <=0.55 m/s;
# clearance (>=0.25 m from returns within -0.05..0.6 m of sensor height) is checked at run time.
ROOM_WAYPOINTS=((-4.75,-.25),(-4.,.25),(-3.7,-.75),(-4.2,-1.55),(-3.,-1.75),(-2.,-1.8),(-1.25,-2.),(-.5,-2.05),(.25,-1.9),(.9,-1.5),(1.4,-1.05),
    (2.5,-1.),(3.25,-1.25),(4.25,-1.75),(4.75,-1.),(4.6,0.),(3.5,.25),(3.,-.5),(2.6,-1.05),(1.5,-1.15),(1.,-1.65),(.25,-2.),(-.5,-2.),(-1.25,-1.95),
    (-2.,-1.7),(-3.,-1.6),(-4.25,-1.25),(-5.,-.6),(-4.75,-.25))
VIBRATION=dict(roll=((.25,2.3),(.10,7.1)),pitch=((.30,1.9),(.10,6.3)),yaw=((.30,.9),),z=((.002,3.7),))  # (deg or m, Hz)


def load_template(path,max_frames=None,require_stationary=True):
    """Real frames without RGB-D (kept: timing, points, IMU, camera time); plus the manifest metadata."""
    from ingestion.recorder_replayer import ReplaySource
    source=ReplaySource(path);meta=source.manifest.get('metadata',{})
    if require_stationary and meta.get('motion_mode')!='stationary':raise ValueError('Template must be a stationary recording (manifest metadata.motion_mode)')
    frames=[]
    for frame in source:
        frames.append(replace(frame,rgb=None,depth=None,painted_features=None,gemini_rgbd_cloud=None))
        if max_frames and len(frames)>=max_frames:break
    source.close();return frames,meta


def imu_table(frames):
    """Unique real IMU samples (timestamp, gyro, accel) over the template; duplicates across frames merged."""
    seen={}
    for f in frames:
        for s in f.imu_samples:seen.setdefault(float(s.timestamp),(np.asarray(s.gyro,float),np.asarray(s.accel,float)))
    t=np.array(sorted(seen));return t,np.array([seen[k][0] for k in t]),np.array([seen[k][1] for k in t])


def world_from_frames(frames,voxel=.015,self_radius=.6):
    """Parked real returns (sensor frame = world) beyond self_radius, voxel-averaged: (xyz, intensity)."""
    p=np.concatenate([f.lidar_points[:,:4] for f in frames]).astype(np.float64)
    p=p[np.linalg.norm(p[:,:3],axis=1)>=self_radius]
    key=np.floor(p[:,:3]/voxel).astype(np.int64);_,inv,count=np.unique(key,axis=0,return_inverse=True,return_counts=True);inv=inv.reshape(-1)
    out=np.zeros((len(count),4));np.add.at(out,inv,p);out/=count[:,None];return out[:,:3],out[:,3].astype(np.float32)


def room_axes(xyz):
    """2x2 rows: wall principal axes, signed so the bulk of the walls lies on the negative side."""
    w=xyz[(xyz[:,2]>1.)&(xyz[:,2]<2.2),:2]
    if len(w)<100:w=xyz[:,:2]
    _,_,vt=np.linalg.svd(w-w.mean(0),full_matrices=False)
    for k in range(2):
        if np.median(w@vt[k])>0:vt[k]=-vt[k]
    return vt


def rotation_to_up(gdir):
    """R with R @ gdir = +z (lidar vectors into a level cart frame)."""
    g=np.asarray(gdir,float)/np.linalg.norm(gdir);axis=np.cross(g,[0,0,1.]);s=np.linalg.norm(axis)
    return np.eye(3) if s<1e-12 else Rotation.from_rotvec(axis/s*np.arctan2(s,g[2])).as_matrix()


class Trajectory:
    """Ground-truth lidar pose in the template's parked sensor frame (world): level-plane path, heading
    along the tangent, holds at both ends, small speed-scaled pitch/roll/yaw/bounce vibration."""
    def __init__(self,waypoints,t_begin,t_end,gdir=(0,0,1),hold_start=3.,hold_end=2.,vmax=.7,cruise=.55,a_lat=.25,a_long=.3,smooth=.2,vibration=True,seed=0,rate=1000.):
        W=np.asarray(waypoints,float);self.gdir=np.asarray(gdir,float)/np.linalg.norm(gdir);self.R_cl=rotation_to_up(self.gdir)
        if W.ndim!=2 or W.shape[1]!=2 or len(W)<2:raise ValueError('Waypoints must be an (n,2) level-plane polyline')
        if cruise>vmax or vmax>.7+1e-9:raise ValueError('Walking pace: cruise <= vmax <= 0.7 m/s')
        u=np.r_[0,np.cumsum(np.linalg.norm(np.diff(W,axis=0),axis=1))];cs=CubicSpline(u,W,bc_type='natural')
        uu=np.linspace(0,u[-1],max(2000,int(u[-1]*400)));xy=cs(uu);d1=cs(uu,1);d2=cs(uu,2)
        ds=np.linalg.norm(np.diff(xy,axis=0),axis=1);s=np.r_[0,np.cumsum(ds)]
        kappa=np.abs(d1[:,0]*d2[:,1]-d1[:,1]*d2[:,0])/np.maximum(np.linalg.norm(d1,axis=1)**3,1e-12)
        v=np.minimum(cruise,np.sqrt(a_lat/np.maximum(kappa,1e-9)));v[0]=v[-1]=0
        for i in range(1,len(v)):v[i]=min(v[i],np.sqrt(v[i-1]**2+2*a_long*ds[i-1]))
        for i in range(len(v)-2,-1,-1):v[i]=min(v[i],np.sqrt(v[i+1]**2+2*a_long*ds[i]))
        tm=np.r_[0,np.cumsum(2*ds/np.maximum(v[1:]+v[:-1],1e-9))]
        self.length=float(s[-1]);self.moving_seconds=float(tm[-1]);self.t_move=t_begin+hold_start
        if self.t_move+self.moving_seconds+hold_end>t_end:
            raise ValueError(f'Path needs {hold_start+self.moving_seconds+hold_end:.1f} s but the template spans {t_end-t_begin:.1f} s; shorten the path or raise --cruise')
        t=np.arange(t_begin-1.,t_end+1.,1/rate);self.t=t
        sd=gaussian_filter1d(np.interp(t-self.t_move,tm,s),smooth*rate,mode='nearest')
        x=np.interp(sd,s,xy[:,0]);y=np.interp(sd,s,xy[:,1]);tangent=np.stack([np.interp(sd,s,d1[:,0]),np.interp(sd,s,d1[:,1])],1)
        heading=np.unwrap(np.arctan2(tangent[:,1],tangent[:,0]));yaw=heading-heading[0]
        speed=np.r_[0,np.abs(np.diff(sd))*rate];self.max_speed=float(speed.max());m=np.clip(gaussian_filter1d(speed,.1*rate)/.3,0,1)
        rng=np.random.default_rng(seed);vib={k:np.zeros_like(t) for k in VIBRATION}
        if vibration:
            for k,terms in VIBRATION.items():
                for amp,hz in terms:vib[k]+=m*(amp if k=='z' else np.radians(amp))*np.sin(2*np.pi*hz*t+rng.uniform(0,2*np.pi))
        # Heading is relative to the parked yaw: the rig starts with the template's orientation at the first waypoint.
        self.spline=CubicSpline(t,np.stack([x,y,vib['z'],yaw+vib['yaw'],vib['pitch'],vib['roll']],1))
    def pose(self,times):
        """(R (n,3,3), p (n,3)) of the lidar in world at the given times."""
        c=self.spline(np.asarray(times,float).reshape(-1))
        Rc=Rotation.from_euler('ZYX',c[:,3:6]).as_matrix();pc=c[:,:3]
        R=np.einsum('ji,njk,kl->nil',self.R_cl,Rc,self.R_cl);return R,pc@self.R_cl
    def matrices(self,times):
        R,p=self.pose(times);T=np.tile(np.eye(4),(len(R),1,1));T[:,:3,:3]=R;T[:,:3,3]=p;return T
    def imu(self,times,imu_to_lidar=np.eye(4),h=1e-3):
        """Exact IMU-frame gyro (rad/s) and specific force (m/s^2) at the IMU point: no bias, no noise."""
        times=np.asarray(times,float).reshape(-1);Ril=np.asarray(imu_to_lidar)[:3,:3];lever=np.asarray(imu_to_lidar)[:3,3]
        (Rm,pm),(R0,p0),(Rp,pp)=self.pose(times-h),self.pose(times),self.pose(times+h)
        omega=Rotation.from_matrix(np.einsum('nji,njk->nik',Rm,Rp)).as_rotvec()/(2*h)
        point=lambda R,p:p+R@lever
        acc=(point(Rp,pp)-2*point(R0,p0)+point(Rm,pm))/h**2
        specific=np.einsum('nji,nj->ni',R0,acc+G*self.gdir)
        return omega@Ril,specific@Ril


def cast_rays(tree,world,origins,dirs,hit_radius=.03,min_range=.1,max_range=25.,max_iter=160):
    """Sphere tracing on the point cloud's distance field: (range or nan, nearest world index or -1)."""
    n=len(origins);t=np.full(n,min_range);hit=np.full(n,-1,np.int64);active=np.arange(n)
    lo=world.min(0)-.5;hi=world.max(0)+.5
    for _ in range(max_iter):
        if not len(active):break
        x=origins[active]+dirs[active]*t[active,None];d,j=tree.query(x,workers=-1)
        done=d<hit_radius;hit[active[done]]=j[done]
        t[active]+=np.maximum(d-.8*hit_radius,.5*hit_radius)
        keep=~done&(t[active]<max_range)&np.all((x>=lo)&(x<=hi),axis=1);active=active[keep]
    rng=np.full(n,np.nan);ok=hit>=0
    rng[ok]=np.einsum('ij,ij->i',world[hit[ok]]-origins[ok],dirs[ok]);rng[ok&(rng<min_range)]=np.nan
    return rng,hit


def render_camera(world,intensity,T_wc,K,size):
    """Z-buffered (nearest return per pixel, 3x3 hole fill) depth in metres and grey RGB from intensity."""
    import cv2
    w,h=size;R=T_wc[:3,:3];pc=(world-T_wc[:3,3])@R;z=pc[:,2];m=(z>.1)&(z<8.)
    pc=pc[m];z=z[m];value=intensity[m];u=np.floor(K[0,0]*pc[:,0]/z+K[0,2]).astype(np.int64);v=np.floor(K[1,1]*pc[:,1]/z+K[1,2]).astype(np.int64)
    m=(u>=0)&(u<w)&(v>=0)&(v<h);flat=v[m]*w+u[m];z=z[m];value=value[m]
    order=np.lexsort((z,flat));flat=flat[order];first=np.r_[True,flat[1:]!=flat[:-1]] if len(flat) else np.zeros(0,bool)
    depth=np.full(h*w,np.inf,np.float32);grey=np.zeros(h*w,np.float32);depth[flat[first]]=z[order][first];grey[flat[first]]=value[order][first]
    # cv2.erode pads with FLT_MAX (finite): accept only in-range fills, or border holes get depth 3.4e38.
    depth=depth.reshape(h,w);grey=grey.reshape(h,w);fill=cv2.erode(depth,np.ones((3,3),np.uint8));holes=~np.isfinite(depth)&(fill<8.)
    depth[holes]=fill[holes];grey[holes]=cv2.dilate(grey,np.ones((3,3),np.uint8))[holes];depth[~np.isfinite(depth)]=0
    rgb=np.repeat(np.clip(grey*255,0,255).astype(np.uint8)[...,None],3,axis=2);return rgb,depth.astype(np.float32)


def synthetic_calibration(K=None,size=(640,400)):
    """A SYNTHETIC pinhole camera 5 cm ahead of and 10 cm below the L2, looking along +x (not measured)."""
    from core.contracts import CameraCalibration
    K=np.array([[305.055908203125,0,322.01495361328125],[0,305.04791259765625,198.9817657470703],[0,0,1]]) if K is None else np.asarray(K,float)
    T=np.array([[0,0,1,.05],[-1,0,0,0],[0,-1,0,-.10],[0,0,0,1.]])
    return CameraCalibration(K,T,np.zeros(5),'synthetic-mobile-from-real-stationary-NOT-MEASURED',False)


def synthesize_frames(templates,world,intensity,trajectory,*,imu_error='real',self_radius=.6,drop_self_returns=False,hit_radius=.03,
                      camera=True,camera_world=None,calibration=None,image_size=(640,400),progress=None):
    """Yield synthetic MultiModalSensorFrames, one per template frame, plus per-frame statistics via progress."""
    from core.contracts import MultiModalSensorFrame,IMUSample
    tree=cKDTree(world);ts,gyro_real,acc_real=imu_table(templates)
    gdir_imu=acc_real.mean(0)/np.linalg.norm(acc_real.mean(0))
    imu_to_lidar=templates[0].imu_to_lidar
    g_true,a_true=trajectory.imu(ts,imu_to_lidar)
    if imu_error=='real':g_err=gyro_real;a_err=acc_real-G*gdir_imu
    elif imu_error=='bias-free':g_err=gyro_real-gyro_real.mean(0);a_err=acc_real-acc_real.mean(0)
    elif imu_error=='none':g_err=a_err=np.zeros_like(gyro_real)
    else:raise ValueError('imu_error must be real, bias-free or none')
    gyro=g_true+g_err;accel=a_true+a_err
    if camera and calibration is None:calibration=synthetic_calibration()
    cam_world=(world,intensity) if camera_world is None else camera_world
    for k,f in enumerate(templates):
        if not np.allclose(f.imu_to_lidar,imu_to_lidar):raise ValueError('Template IMU extrinsics change between frames')
        p=f.lidar_points;r=np.linalg.norm(p[:,:3],axis=1);own=r<self_radius;dirs=p[:,:3]/np.maximum(r,1e-9)[:,None]
        cast=~own;R,o=trajectory.pose(f.timestamp+p[cast,6].astype(float))
        rng,idx=cast_rays(tree,world,o,np.einsum('nij,nj->ni',R,dirs[cast]),hit_radius)
        good=np.isfinite(rng);out=p.copy();rows=np.flatnonzero(cast)
        out[rows[good],:3]=dirs[rows[good]]*rng[good,None];out[rows[good],3]=intensity[idx[good]]
        keep=own.copy() if not drop_self_returns else np.zeros(len(p),bool);keep[rows[good]]=True;out=out[keep]
        frame_t=np.array([s.timestamp for s in f.imu_samples]);j=np.searchsorted(ts,frame_t)
        if not np.allclose(ts[j],frame_t,atol=0,rtol=0):raise ValueError('IMU timestamp lookup failed')
        samples=tuple(IMUSample(float(frame_t[i]),gyro[j[i]],accel[j[i]]) for i in range(len(j)))
        fields=dict(frame_id=f.frame_id,timestamp=f.timestamp,lidar_points=out.astype(np.float32),imu_samples=samples,sweep_duration=f.sweep_duration,
            camera_timestamp=f.camera_timestamp,return_metadata_valid=f.return_metadata_valid,capabilities=f.capabilities,raw_timestamps=dict(f.raw_timestamps),
            imu_to_lidar=f.imu_to_lidar,rplidar_origin_lidar=np.zeros(3),
            diagnostics={'synthetic':True,'synthetic_label':LABEL,'hardware_validation':'not_tested','template_frame_id':int(f.frame_id),
                'synthetic_returns':{'cast':int(cast.sum()),'missed':int((~good).sum()),'rig_returns':int(own.sum()) if not drop_self_returns else 0},
                'imu_extrinsics_source':f.diagnostics.get('imu_extrinsics_source'),'echo_metadata':f.diagnostics.get('echo_metadata')})
        if camera and f.camera_timestamp is not None:
            Tw=trajectory.matrices([f.camera_timestamp])[0]@calibration.T_lidar_camera
            fields['rgb'],fields['depth']=render_camera(cam_world[0],cam_world[1],Tw,calibration.K,image_size);fields['calibration']=calibration
        frame=MultiModalSensorFrame(**fields)
        if progress:progress(k,frame)
        yield frame


def clearance(trajectory,world,times,band=(-.05,.6)):
    """Minimum horizontal distance from the path to world returns within the sensor-height band (m)."""
    _,p=trajectory.pose(times);up=trajectory.gdir;h=world@up;obstacles=world[(h>band[0])&(h<band[1])]
    if not len(obstacles):return float('inf')
    flat=lambda q:q@trajectory.R_cl.T[:,:2]
    d,_=cKDTree(flat(obstacles)).query(flat(p));return float(d.min())


def write_recording(output,frames,metadata,report,ground_truth,chunk_size=4,compression_level=1):
    from ingestion.recorder_replayer import FrameRecorder
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise SystemExit(f'{output} exists and is not empty; choose a new directory')
    recorder=FrameRecorder(output,chunk_size=chunk_size,max_buffer_bytes=64*1024**2,max_disk_bytes=16*1024**3,metadata=metadata,compression_level=compression_level)
    count=0
    try:
        for frame in frames:recorder.write(frame);count+=1
    finally:recorder.close()
    np.savez_compressed(output/'ground_truth.npz',**ground_truth)
    (output/'recording_status.json').write_text(json.dumps({'written_frames':count,'dropped_frames':0,'complete':True,'error':None,'synthetic':True,'schema_version':1},indent=2),encoding='utf-8')
    (output/'capture_report.json').write_text(json.dumps({**report,'frames':count},indent=2,default=str),encoding='utf-8')
    return count


def synthesize(args):
    started=time.time();templates,template_meta=load_template(args.template,args.max_frames)
    if args.duration:templates=[f for f in templates if f.timestamp+f.sweep_duration-templates[0].timestamp<=args.duration]
    world,intensity=world_from_frames(templates,args.world_voxel,args.self_radius);print(f'world: {len(world)} voxels from {len(templates)} parked sweeps',flush=True)
    ts,_,acc=imu_table(templates);gdir_imu=acc.mean(0)/np.linalg.norm(acc.mean(0));gdir=templates[0].imu_to_lidar[:3,:3]@gdir_imu
    axes=room_axes(world)
    if args.waypoints:waypoints=np.array([[float(v) for v in item.split(',')] for item in args.waypoints.split(';')])
    else:waypoints=np.asarray(ROOM_WAYPOINTS,float)@axes
    t_begin=templates[0].timestamp;t_end=min(templates[-1].timestamp+templates[-1].sweep_duration,ts[-1])
    trajectory=Trajectory(waypoints,t_begin,t_end,gdir,args.hold_start,args.hold_end,args.vmax,args.cruise,seed=args.seed,vibration=not args.no_vibration)
    ends=np.array([f.timestamp+f.sweep_duration for f in templates]);gt=trajectory.matrices(ends)
    gap=clearance(trajectory,world,np.linspace(t_begin,t_end,2000))
    print(f'path {trajectory.length:.1f} m, moving {trajectory.moving_seconds:.1f} s, peak {trajectory.max_speed:.2f} m/s, clearance {gap:.2f} m',flush=True)
    if gap<.25 and not args.force:raise SystemExit('Path passes within 0.25 m of an obstacle at sensor height; adjust --waypoints or pass --force')
    cam_world=world_from_frames(templates,.03,args.self_radius) if args.camera=='depth' else None
    stats={'cast':0,'missed':0,'points':0}
    def progress(k,frame):
        s=frame.diagnostics['synthetic_returns'];stats['cast']+=s['cast'];stats['missed']+=s['missed'];stats['points']+=len(frame.lidar_points)
        if k%50==0:print(f'frame {k+1}/{len(templates)} points {len(frame.lidar_points)} missed {s["missed"]} ({time.time()-started:.0f} s)',flush=True)
    frames=synthesize_frames(templates,world,intensity,trajectory,imu_error=args.imu_error,self_radius=args.self_radius,drop_self_returns=args.drop_self_returns,
        hit_radius=args.hit_radius,camera=args.camera=='depth',camera_world=cam_world,progress=progress)
    note=('SYNTHETIC: rays and IMU generated from a ground-truth trajectory in the real parked cloud of the template; '
          'real timing, ray pattern and IMU noise/bias only. Not a hardware measurement.')
    metadata={'motion_mode':'mobile','synthetic':True,'label':LABEL,'note':note,'recording_policy':'synchronized_raw_frames',
        'source':'tools/synthesize_mobile_recording.py','template_recording':str(Path(args.template).resolve()),'template_motion_mode':template_meta.get('motion_mode'),
        'calibration':'synthetic pinhole (NOT MEASURED)' if args.camera=='depth' else 'none','ground_truth':'ground_truth.npz','imu_error':args.imu_error}
    params={k:(str(v) if isinstance(v,Path) else v) for k,v in vars(args).items() if k!='func'}
    report={'schema_version':1,'kind':'raw_capture','synthetic':True,'label':f'SYNTHETIC ({LABEL}), not a hardware result','note':note,
        'date_utc':datetime.now(timezone.utc).isoformat(),'motion':'mobile','bringup_uncalibrated':args.camera!='depth','parameters':params,
        'trajectory':{'length_m':trajectory.length,'moving_seconds':trajectory.moving_seconds,'peak_speed_m_s':trajectory.max_speed,'clearance_m':gap,
            'waypoints_world_xy':waypoints.tolist(),'gravity_direction_lidar':gdir.tolist(),'vibration':VIBRATION if not args.no_vibration else None},
        'imu':{'error_model':args.imu_error,'real_accel_mean_imu':acc.mean(0).tolist(),'implied_accel_bias_imu':(acc.mean(0)-G*gdir_imu).tolist(),'samples':len(ts)},
        'world':{'voxels':len(world),'voxel_m':args.world_voxel,'self_radius_m':args.self_radius,'rig_returns':'dropped' if args.drop_self_returns else 'kept rigid with the rig'},
        'recorder':{'complete':True,'dropped_frames':0}}
    truth={'sweep_end_times':ends,'frame_ids':np.array([f.frame_id for f in templates],np.int64),'poses_sweep_end':gt,'trajectory_t':trajectory.t[::10],'trajectory_poses':trajectory.matrices(trajectory.t[::10]),
           'world_points':world.astype(np.float32),'gravity_direction_lidar':gdir,'label':np.array(LABEL)}
    count=write_recording(args.output,frames,metadata,report,truth,compression_level=args.compression_level)
    summary={'frames':count,'points':stats['points'],'miss_fraction':stats['missed']/max(stats['cast'],1),'seconds':time.time()-started,'output':str(args.output)}
    report=json.loads((Path(args.output)/'capture_report.json').read_text(encoding='utf-8'));report['synthesis']=summary
    (Path(args.output)/'capture_report.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    print(json.dumps(summary));return 0


def umeyama(a,b):
    """Rigid (R,t) minimising |R a + t - b|."""
    ma,mb=a.mean(0),b.mean(0);u,_,vt=np.linalg.svd((b-mb).T@(a-ma));D=np.eye(3);D[2,2]=np.sign(np.linalg.det(u@vt));R=u@D@vt;return R,mb-R@ma


def rot_deg(R):return float(np.degrees(np.linalg.norm(Rotation.from_matrix(R).as_rotvec())))


def trajectory_errors(est,gt,rpe_step=12):
    est=np.asarray(est);gt=np.asarray(gt);inv=np.linalg.inv
    raw=np.linalg.norm(est[:,:3,3]-gt[:,:3,3],axis=1);R,t=umeyama(est[:,:3,3],gt[:,:3,3]);aligned=np.linalg.norm(est[:,:3,3]@R.T+t-gt[:,:3,3],axis=1)
    rot=np.array([rot_deg(e[:3,:3].T@g[:3,:3]) for e,g in zip(est,gt)])
    rel=[inv(gt[i])@gt[i+rpe_step] for i in range(len(gt)-rpe_step)];rel_e=[inv(est[i])@est[i+rpe_step] for i in range(len(est)-rpe_step)]
    rpe_t=np.array([np.linalg.norm((inv(g)@e)[:3,3]) for g,e in zip(rel,rel_e)]);rpe_r=np.array([rot_deg((inv(g)@e)[:3,:3]) for g,e in zip(rel,rel_e)])
    loop_gt=inv(gt[0])@gt[-1];loop_est=inv(est[0])@est[-1];loop=inv(loop_gt)@loop_est
    length=float(np.linalg.norm(np.diff(gt[:,:3,3],axis=0),axis=1).sum())
    rms=lambda x:float(np.sqrt(np.mean(np.square(x))))
    return {'frames':len(est),'path_length_m':length,'ate_rmse_m':rms(raw),'ate_max_m':float(raw.max()),'ate_aligned_rmse_m':rms(aligned),
        'rotation_rmse_deg':rms(rot),'rotation_max_deg':float(rot.max()),f'rpe_{rpe_step}_frames_translation_rmse_m':rms(rpe_t),f'rpe_{rpe_step}_frames_rotation_rmse_deg':rms(rpe_r),
        'loop_closure_translation_m':float(np.linalg.norm(loop[:3,3])),'loop_closure_rotation_deg':rot_deg(loop[:3,:3]),
        'loop_closure_drift_percent':100*float(np.linalg.norm(loop[:3,3]))/max(length,1e-9),'final_position_error_m':float(raw[-1])}


def recording_frame_ids(recording):
    ids=[];manifest=json.loads((Path(recording)/'manifest.json').read_text(encoding='utf-8'))
    for chunk in manifest['chunks']:
        with np.load(Path(recording)/chunk['file'],allow_pickle=False) as z:ids+=[int(m['frame_id']) for m in json.loads(str(z['metadata_json']))]
    return np.array(ids,np.int64)


def evaluate(args):
    truth=np.load(Path(args.recording)/'ground_truth.npz');twin=Path(args.twin)
    with np.load(twin/'measured_map.npz') as z:poses=z['poses'];floor=z['floor_transform']
    report=json.loads((twin/'reconstruction.json').read_text(encoding='utf-8'))
    ids=truth['frame_ids'] if 'frame_ids' in truth else recording_frame_ids(args.recording)
    # One pose per registered scan (commissioning_sweeps_per_scan may merge sweeps): its reference is the end of
    # the scan's last sweep, i.e. the sweep before the next scan's first sweep.
    first=np.searchsorted(ids,[int(x['frame_id']) for x in report['commissioning']['frames']]);last=np.r_[first[1:]-1,len(ids)-1]
    gt0=truth['poses_sweep_end'][last[0]];gt=np.linalg.inv(gt0)@truth['poses_sweep_end'][last]  # the build's world is the first scan-end frame
    if len(poses)!=len(gt):raise ValueError(f'Twin has {len(poses)} poses, ground truth {len(gt)}')
    step=max(1,int(round(len(ids)/len(poses))));result={'label':f'SYNTHETIC ({LABEL}) evaluation, not a hardware result','sweeps_per_scan':step,
        'trajectory':trajectory_errors(poses,gt,rpe_step=max(1,12//step))}
    from slam_and_twin.static_twin_builder import StaticTwinBuilder
    builder=StaticTwinBuilder.load(twin);world=truth['world_points'].astype(float);tree=cKDTree(world)
    pts=np.concatenate(list(builder.iter_measured_points()));builder.close()
    pts=(pts-floor[:3,3])@floor[:3,:3]@gt0[:3,:3].T+gt0[:3,3]  # floor-aligned twin -> build world -> template world
    d,_=tree.query(pts,workers=-1);g=world[::max(1,len(world)//200000)];dg,_=cKDTree(pts).query(g,workers=-1)
    result['map']={'surface_points':len(pts),'to_truth_median_m':float(np.median(d)),'to_truth_p90_m':float(np.percentile(d,90)),'to_truth_p95_m':float(np.percentile(d,95)),
        'within_5cm':float(np.mean(d<.05)),'within_10cm':float(np.mean(d<.10)),'truth_covered_within_10cm':float(np.mean(dg<.10)),
        'note':'distances from twin surface points to the parked world cloud; rig returns that move with the cart count as errors'}
    c=report.get('coarse_registration',[])
    result['build']={'stations':report.get('stations'),'pose_graph_edges':report.get('pose_graph_edges'),'mode_counts':report.get('commissioning',{}).get('mode_counts'),
        'coarse_methods':{m:sum(1 for x in c if x.get('method')==m) for m in sorted({x.get('method') for x in c})},'fpfh_errors':sum(1 for x in c if 'fpfh_ransac_error' in x),
        'floor_fit':report.get('floor_fit'),'trajectory_summary':report.get('commissioning',{}).get('trajectory'),'imu_static_init':report.get('commissioning',{}).get('imu_static_init'),'tsdf_frames':report.get('tsdf_frames'),'mesh_triangles':report.get('mesh_triangles'),'graph':report.get('graph')}
    text=json.dumps(result,indent=2,default=float);print(text)
    if args.json:Path(args.json).write_text(text,encoding='utf-8')
    return 0


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0]=='evaluate':
        p=argparse.ArgumentParser(description='Compare a build-twin output with the synthetic ground truth')
        p.add_argument('--recording',type=Path,required=True);p.add_argument('--twin',type=Path,required=True);p.add_argument('--json',type=Path)
        return evaluate(p.parse_args(argv[1:]))
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--template',type=Path,required=True,help='Real STATIONARY recording (the world and the timing)')
    p.add_argument('--output',type=Path,required=True,help='New directory for the synthetic recording')
    p.add_argument('--duration',type=float,help='Use only the first N seconds of the template');p.add_argument('--max-frames',type=int)
    p.add_argument('--waypoints',help='World-frame level-plane loop "x,y;x,y;..." (default: a loop in the room frame)')
    p.add_argument('--hold-start',type=float,default=3.);p.add_argument('--hold-end',type=float,default=2.)
    p.add_argument('--vmax',type=float,default=.7);p.add_argument('--cruise',type=float,default=.55)
    p.add_argument('--imu-error',choices=['real','bias-free','none'],default='real',help='real: add the still IMU residual incl. its bias')
    p.add_argument('--world-voxel',type=float,default=.015);p.add_argument('--hit-radius',type=float,default=.03)
    p.add_argument('--self-radius',type=float,default=.6,help='Parked returns nearer than this are rig-attached, not world')
    p.add_argument('--drop-self-returns',action='store_true');p.add_argument('--no-vibration',action='store_true')
    p.add_argument('--camera',choices=['depth','none'],default='depth');p.add_argument('--compression-level',type=int,default=1)
    p.add_argument('--seed',type=int,default=0);p.add_argument('--force',action='store_true')
    return synthesize(p.parse_args(argv))


if __name__=='__main__':sys.exit(main())

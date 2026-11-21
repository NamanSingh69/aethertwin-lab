"""Fast quality gate for a raw recording, run right after `tools/rig.py record`, before any long processing step.

    .venv\\Scripts\\python.exe tools/recording_qa.py %USERPROFILE%/aethertwin_rec/self_mask_parked --purpose self_mask
    .venv\\Scripts\\python.exe tools/recording_qa.py <dir> --purpose calibration --cols 7 --rows 5 --square 0.0406 --json <dir>_qa.json

One streaming pass over the chunks (ingestion.recorder_replayer.ReplaySource, which also verifies every chunk's
sha256); only small per-frame numbers, the IMU samples, one decimated 6-sweep cloud per second and the first and
last 3 s of decimated clouds are kept, so memory stays small for a 600 s recording. capture_report.json
(tools/record_raw.py) and recording_status.json supply the transport and recorder counters.

Every check prints PASS, WARN or FAIL with the measured value and a one-line fix hint. Exit code: 0 all pass,
1 at least one warning, 2 at least one failure. Purposes: stationary and self_mask (the cart must not move),
calibration (the cart stands; counts still checkerboard holds), mobile and commissioning (the cart is pushed).

What the numbers are: a screening aid, not a result. The motion classes come from 1 s IMU windows (plus ICP links
faster than 0.1 m/s); the distance is a proxy from chaining a coarse gyro-initialised point-to-plane ICP between the
per-second keyframes, not odometry; the start/end score is the share of end-cloud voxels found next to start-cloud
voxels at the best yaw, with a direct ICP of the last keyframe onto the first for the offset: a loop-closure hint,
not a loop closure. Points nearer than 1.5 m (cart, pusher) are left out of both. For still purposes a start/end
offset over 0.1 m or 2 deg, or a gyro turn, fails; accelerometer-only windows (bumps, vibration) only warn.
The calibration hold count is a fast estimate (the SB corner detector without its slow flags, sampled at ~3 Hz,
looser stillness than the extractor); `--exact-holds` also runs the detector tools/calibrate_rig.py uses (slow),
and calibrate_rig.py's own acceptance checks remain the authority.
"""
from __future__ import annotations
import argparse,json,math,sys,time
from collections import deque
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
PURPOSES=('stationary','self_mask','calibration','mobile','commissioning');STILL_PURPOSES=('stationary','self_mask','calibration')
LEVEL={'PASS':0,'WARN':1,'FAIL':2}
MIN_DURATION_S={'self_mask':50.,'calibration':240.,'mobile':50.,'commissioning':300.}
# IMU window classification (1 s windows): mean gyro norm and the std of the accelerometer norm.
# Measured at rest on the bench (wifi_stationary_60s, L2 motor running): gyro ~0.006 rad/s, accel-norm std ~0.07 m/s^2.
GYRO_MOVING_RAD_S=.03;ACCEL_STD_MOVING=.2
NEAR_M=1.5  # points closer than this (cart body, the person pushing) are left out of ICP and start/end scoring
OVERLAP_VOXEL=.2;ICP_VOXEL=.15;KEY_SWEEPS=6;EDGE_S=3.;LINK_MOVING_M_S=.1


def voxel_keys(points,size):
    k=np.floor(points/size).astype(np.int64)+(1<<20);return (k[:,0]<<42)|(k[:,1]<<21)|k[:,2]


def decimate(points,size):
    if not len(points):return points
    _,first=np.unique(voxel_keys(points,size),return_index=True);return points[first]


def far(points,near=NEAR_M):
    xyz=np.asarray(points[:,:3],np.float64);r=np.linalg.norm(xyz,axis=1);return xyz[np.isfinite(r)&(r>=near)]


def dilated(keys):
    offsets=[(dx<<42)+(dy<<21)+dz for dx in (-1,0,1) for dy in (-1,0,1) for dz in (-1,0,1)]
    return np.unique((keys[:,None]+np.array(offsets,np.int64)[None,:]).ravel())


def overlap_score(start,end,size=OVERLAP_VOXEL,yaws=np.arange(0,360,5)):
    """(best share of end voxels that land next to a start voxel, yaw deg at the best, share at yaw 0)."""
    if len(start)<50 or len(end)<50:return None,None,None
    A=dilated(np.unique(voxel_keys(start,size)));E=decimate(end,size/2);best=(-1.,0.);zero=None
    for yaw in yaws:
        c,s=math.cos(math.radians(yaw)),math.sin(math.radians(yaw));R=np.array([[c,-s,0],[s,c,0],[0,0,1]])
        keys=np.unique(voxel_keys(E@R.T,size));share=float(np.isin(keys,A,assume_unique=True).mean())
        if yaw==0:zero=share
        if share>best[0]:best=(share,float(yaw))
    return best[0],best[1],zero


def icp(src,dst,R,t,iterations=15):
    """Point-to-plane ICP, p_dst = R p_src + t, correspondence radius shrinking 0.6 -> 0.15 m; (R, t, inlier share).

    On the parked bench recording (6 sweeps per keyframe, 0.15 m voxels) a link errs by ~7 mm (max 15 mm)."""
    from scipy.spatial import cKDTree
    from scipy.spatial.transform import Rotation
    tree=cKDTree(dst);_,idx=tree.query(dst,k=min(10,len(dst)));N=dst[idx]-dst[idx].mean(1,keepdims=True)
    normal=np.linalg.eigh(np.einsum('nki,nkj->nij',N,N))[1][:,:,0];share=0.
    for radius in np.linspace(.6,.15,iterations):
        p=src@R.T+t;d,i=tree.query(p,distance_upper_bound=radius);m=np.isfinite(d);share=float(m.mean())
        if m.sum()<30:return R,t,0.
        a=p[m];b=dst[i[m]];n=normal[i[m]];r=np.einsum('ij,ij->i',a-b,n);J=np.hstack([np.cross(a,n),n])
        x=np.linalg.solve(J.T@J+1e-6*np.eye(6),-J.T@r);dR=Rotation.from_rotvec(x[:3]).as_matrix();R=dR@R;t=dR@t+x[3:]
    return R,t,share


def gyro_rotation(t,gyro,t0,t1):
    """Orientation of the IMU at t1 in its frame at t0, integrating the gyro."""
    from scipy.spatial.transform import Rotation
    m=(t>t0)&(t<=t1);ts=np.concatenate([[t0],t[m]]);w=gyro[m]
    if not len(w):return np.eye(3)
    R=np.eye(3)
    for step in Rotation.from_rotvec(w*np.diff(ts)[:,None]).as_matrix():R=R@step
    return R


def board_pose_fast(rgb,K,dist,pattern,square):
    """(rvec, tvec, centre_u_fraction) of a checkerboard: the extractor's SB detector without its EXHAUSTIVE/ACCURACY
    flags (~35 ms instead of ~120 ms a 640x400 frame; the classic detector misses the borderless screen board), IPPE."""
    import cv2
    gray=cv2.cvtColor(np.asarray(rgb,np.uint8),cv2.COLOR_RGB2GRAY);found,c=cv2.findChessboardCornersSB(gray,pattern,flags=0)
    if not found:return None
    cols,rows=pattern;grid=np.zeros((cols*rows,3));grid[:,:2]=np.mgrid[0:cols,0:rows].T.reshape(-1,2)*square
    grid[:,0]-=(cols-1)*square/2;grid[:,1]-=(rows-1)*square/2
    ok,rvec,tvec=cv2.solvePnP(grid,c.reshape(-1,2).astype(np.float64),K,dist,flags=cv2.SOLVEPNP_IPPE)
    return (rvec.reshape(3),tvec.reshape(3),float(c.reshape(-1,2)[:,0].mean()/gray.shape[1])) if ok else None


def pct(values,q):return None if not len(values) else float(np.percentile(values,q))


def rnd(value,digits=3):
    if isinstance(value,dict):return {k:rnd(v,digits) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [rnd(v,digits) for v in value]
    if isinstance(value,(float,np.floating)):return None if not math.isfinite(value) else round(float(value),digits)
    if isinstance(value,np.integer):return int(value)
    return value


class Scan:
    """Everything the checks need, gathered in one pass over the frames."""
    def __init__(self,args):
        self.args=args;self.rows=[];self.imu={};self.images=[];self.board=[];self.keyframes=[];self.imu_to_lidar=np.eye(4)
        self.start=[];self.end=deque();self.pending=None;self.t0=None;self.K=None;self.dist=None;self.last_key=-1e9;self.range_bins=np.array([0,.3,.5,.8,1.5,3,6,12,25,1e9])
        self.range_hist=np.zeros(len(self.range_bins)-1,np.int64);self.ranges=[];self.image_stride=max(1,int(args.image_stride));self.board_stride=1
    def add(self,i,f):
        end=f.timestamp+f.sweep_duration;xyz=np.asarray(f.lidar_points[:,:3],np.float64);r=np.linalg.norm(xyz,axis=1);r=r[np.isfinite(r)]
        if self.t0 is None:self.t0=f.timestamp;self.imu_to_lidar=np.asarray(f.imu_to_lidar,float)
        imu_t=np.array([s.timestamp for s in f.imu_samples],float)
        for s in f.imu_samples:self.imu.setdefault(round(float(s.timestamp),6),(*np.asarray(s.gyro,float),*np.asarray(s.accel,float)))
        inside=np.sort(imu_t[(imu_t>=f.timestamp-.02)&(imu_t<=end+.02)]);edges=np.concatenate([[f.timestamp],inside[(inside>f.timestamp)&(inside<end)],[end]])
        covered=bool(len(imu_t)>=2 and imu_t.min()<=f.timestamp+.005 and imu_t.max()>=end-.005)
        imu_gap=float(np.max(np.diff(edges))) if covered else float('nan')
        has_cam=f.rgb is not None and f.depth is not None;age=None if f.camera_timestamp is None else float(f.camera_timestamp-end)
        self.rows.append((int(f.frame_id),float(f.timestamp),float(f.sweep_duration),len(xyz),float(np.median(r)) if len(r) else np.nan,covered,imu_gap,
                          has_cam,np.nan if age is None else age,np.nan if f.camera_timestamp is None else float(f.camera_timestamp)))
        self.range_hist+=np.histogram(r,self.range_bins)[0]
        if len(r):self.ranges.append(np.percentile(r,[5,50,95]))
        if has_cam and i%self.image_stride==0:self.images.append(self.image_stats(f.rgb,f.depth))
        if self.args.purpose=='calibration' and f.rgb is not None and i%self.board_stride==0:self.calibration_frame(f)
        pts=decimate(far(f.lidar_points),OVERLAP_VOXEL/2)
        if f.timestamp-self.t0<=EDGE_S:self.start.append(pts)
        self.end.append((f.timestamp,pts))
        while self.end and self.end[0][0]<f.timestamp-EDGE_S:self.end.popleft()
        if self.pending is None and f.timestamp-self.last_key>=self.args.keyframe_s:self.last_key=f.timestamp;self.pending=(f.timestamp,[])
        if self.pending is not None:
            self.pending[1].append(far(f.lidar_points))
            if len(self.pending[1])>=KEY_SWEEPS:self.keyframes.append((self.pending[0],decimate(np.vstack(self.pending[1]),ICP_VOXEL).astype(np.float32)));self.pending=None
    def image_stats(self,rgb,depth):
        import cv2
        gray=cv2.cvtColor(np.asarray(rgb,np.uint8),cv2.COLOR_RGB2GRAY);d=np.asarray(depth,np.float32);valid=np.isfinite(d)&(d>0)
        return (float(valid.mean()),float(np.median(d[valid])) if valid.any() else np.nan,float((gray>=250).mean()),float((gray<=5).mean()),
                float(cv2.Laplacian(gray,cv2.CV_64F).var()),float(gray.mean()))
    def calibration_frame(self,f):
        if self.K is None:
            v=(f.diagnostics.get('camera_calibration_validation') or {}).get('color') or {}
            if 'K' not in v and f.calibration is None:return
            self.K=np.asarray(v['K'] if 'K' in v else f.calibration.K,float);self.dist=np.asarray(v.get('distortion',[0]*5) if 'K' in v else f.calibration.distortion,float)
        pose=board_pose_fast(f.rgb,self.K,self.dist,(self.args.cols,self.args.rows),self.args.square)
        self.board.append((float(f.timestamp),pose))
    def imu_arrays(self):
        t=np.array(sorted(self.imu));v=np.array([self.imu[k] for k in t]).reshape(-1,6);return t,v[:,:3],v[:,3:]


def detect_holds(board,args):
    """Runs of sampled board detections that stay within --hold-mm/--hold-deg of their first pose for >= --min-hold-s."""
    from tools.extract_calibration_targets import same_hold
    holds=[];current=None
    for t,pose in board:
        if pose is None:current=None;continue
        if current is not None and same_hold(current['poses'][0],pose,args.hold_mm,args.hold_deg):current['poses'].append(pose);current['t1']=t
        else:current={'poses':[pose],'t0':t,'t1':t};holds.append(current)
    return [h for h in holds if h['t1']-h['t0']>=args.min_hold_s]


def exact_holds(recording,args):
    from tools import extract_calibration_targets as targets
    a=targets.add_target_arguments(argparse.ArgumentParser()).parse_args(['--cols',str(args.cols),'--rows',str(args.rows),'--square',str(args.square),'--board-size','1','1'])
    return len(targets.detect_holds(recording,a)[0])


def load_json(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).is_file() else None
    except (OSError,ValueError):return None


def analyse(recording,args):
    from ingestion.recorder_replayer import ReplaySource
    recording=Path(recording);checks=[];started=time.perf_counter()
    def add(name,status,value,hint=''):checks.append({'check':name,'status':status,'value':rnd(value),'hint':hint if status!='PASS' else ''})
    report=load_json(recording/'capture_report.json');status=load_json(recording/'recording_status.json')
    # ---- recorder and transport counters
    if (recording/'.recording.lock').exists():add('recorder finished','FAIL','.recording.lock present','the recorder is still running or crashed; wait for it, else re-record')
    if status is not None and not status.get('complete',False):add('recorder complete','FAIL',{'complete':status.get('complete'),'error':status.get('error')},'the laptop could not keep up or the run aborted: re-record (close other programs, check disk space)')
    scan=Scan(args)
    if args.purpose=='calibration':scan.board_stride=max(1,round(12./args.board_hz))
    source=ReplaySource(recording,allow_incomplete=True)
    try:
        for i,frame in enumerate(source):scan.add(i,frame)
    except Exception as exc:add('recording readable','FAIL',f'{type(exc).__name__}: {exc} (after {len(scan.rows)} frames)','chunk or manifest damaged: re-record; keep the damaged one for inspection')
    finally:source.close()
    read_s=time.perf_counter()-started
    if len(scan.rows)<10:
        add('frames','FAIL',len(scan.rows),'almost nothing recorded: check rig.py status and the node log, then re-record');return finish(recording,args,checks,{},started)
    fid,ts,dur,npts,medr,covered,imugap,hascam,age,camts=(np.array(c) for c in zip(*scan.rows));n=len(fid);ends=ts+dur
    period=float(np.median(dur));span=float(ends[-1]-ts[0])
    requested=float((report or {}).get('duration_requested_s') or args.duration or span)
    summary={'frames':n,'sensor_span_s':span,'requested_s':requested,'median_sweep_s':period}
    if report is None:add('capture report','WARN','missing','not recorded with tools/rig.py record / record_raw.py: transport counters unknown')
    else:
        if report.get('error'):add('capture error','FAIL',report.get('error'),'the capture ended with an error: read the node log (rig.py node log -n 50) and re-record')
        rec=report.get('recorder') or {};tr=report.get('transport') or {};node=tr.get('node') or {}
        dropped=int(rec.get('dropped_frames') or 0)
        if dropped>.01*n:add('recorder dropped frames','FAIL',dropped,'the laptop disk/CPU fell behind: close other programs, record to the SSD, re-record')
        counters={k:tr.get(k) for k in ('cloud_missing','camera_missing','camera_incomplete','imu_missing_samples','imu_sequence_breaks','clock_jumps','stalls','decode_errors',
                                         'receiver_overflows','protocol_errors','clouds_out_of_order_dropped','imu_out_of_order_dropped','node_utc_steps')}
        counters.update({'node_'+k:node.get(k) for k in ('clouds_dropped','camera_dropped','imu_dropped','imu_gaps')});counters['recorder_dropped_frames']=dropped
        counters['segments']=tr.get('segments');bad={k:v for k,v in counters.items() if v and (k!='segments' or v>1)}
        clouds=max(1,int(tr.get('clouds') or n))
        if not tr:add('transport losses','WARN','no transport counters in capture_report.json','not a Pi capture-node recording: losses unknown; record with tools/rig.py record')
        elif int(tr.get('cloud_missing') or 0)>.05*clouds:add('transport losses','FAIL',bad,'more than 5 % of clouds lost: weak Wi-Fi; move the laptop closer to the cart or use the wired link, re-record')
        elif bad:add('transport losses','WARN',bad,'losses or reconnects on the link: keep the laptop near the cart; re-record if a later step needs every sweep')
        else:add('transport losses','PASS',{'segments':counters['segments'],'clouds':tr.get('clouds'),'camera_frames':tr.get('camera_frames'),'imu_samples':tr.get('imu_samples')})
        if args.purpose=='commissioning' and report.get('bringup_uncalibrated'):
            add('calibrated node','FAIL','bring-up (uncalibrated)','commissioning needs the calibrated node: rig.py node restart --live, then record with --live')
        summary['transport_label']=report.get('label')
    # ---- frame count, rate, ids and sweep timing
    coverage=n*period/requested if requested>0 else 1.;rate=(n-1)/max(ts[-1]-ts[0],1e-9)
    value={'frames':n,'rate_hz':rate,'expected_hz':1/period,'coverage_of_requested':coverage,'requested_s':requested}
    add('frame count and rate','FAIL' if coverage<.8 else 'WARN' if coverage<.95 or rate<.95/period else 'PASS',value,'recording shorter than asked or sweeps missing: see transport losses; re-record if the step needs the full duration')
    if args.purpose in MIN_DURATION_S and span<MIN_DURATION_S[args.purpose]:
        add('duration for purpose','WARN',{'span_s':span,'minimum_s':MIN_DURATION_S[args.purpose]},f'too short for {args.purpose}: record the duration LAB_DAY.md gives')
    steps=np.diff(fid);missing=int(np.sum(np.clip(steps-1,0,None)));backwards=int(np.sum(steps<=0))
    add('sweep ids','FAIL' if backwards or missing>.05*n else 'WARN' if missing else 'PASS',{'missing_ids':missing,'non_increasing':backwards},
        'sweeps lost (id gaps) or out of order: weak link or node restart; check rig.py node log')
    gaps=(ts[1:]-ends[:-1])*1000;overlaps=int(np.sum(gaps<-2));holes=int(np.sum(gaps>50));hole_s=float(np.sum(gaps[gaps>50])/1000)
    add('sweep start gaps','FAIL' if hole_s>.02*span else 'WARN' if overlaps or holes else 'PASS',
        {'min_ms':gaps.min(),'median_ms':np.median(gaps),'p99_ms':pct(gaps,99),'max_ms':gaps.max(),'overlaps_gt_2ms':overlaps,'holes_gt_50ms':holes,'hole_s':hole_s},
        'sweep timing broken (overlaps mean a clock jump, holes mean lost sweeps): check transport clock_jumps and segments; re-record')
    # ---- LiDAR points and ranges
    small=int(np.sum(npts<.5*np.median(npts)));R=np.array(scan.ranges) if scan.ranges else np.zeros((0,3))
    hist=scan.range_hist/max(1,scan.range_hist.sum());near=float(hist[:2].sum())
    value={'median_points':np.median(npts),'min_points':npts.min(),'sweeps_below_half_median':small,'range_p5_m':pct(R[:,0],50) if len(R) else None,
           'range_median_m':pct(R[:,1],50) if len(R) else None,'range_p95_m':pct(R[:,2],50) if len(R) else None,'share_below_0.5m':near,
           'range_histogram':{f'{a:g}-{b:g}':float(h) for a,b,h in zip(scan.range_bins[:-1],scan.range_bins[1:],hist)}}
    st='FAIL' if np.median(npts)<1000 else 'WARN' if np.median(npts)<3000 or small>.02*n or near>.3 or (len(R) and np.median(R[:,2])<2) else 'PASS'
    add('points per sweep and ranges',st,value,'few or near points: something blocks the LiDAR (cable, hand, plate edge) or it faces a wall; clear the view and re-record')
    # ---- IMU
    t,gyro,accel=scan.imu_arrays()
    if len(t)<10:add('IMU','FAIL',len(t),'no IMU samples: check the L2 IMU stream (rig.py status) and re-record');return finish(recording,args,checks,summary,started)
    imu_rate=(len(t)-1)/max(t[-1]-t[0],1e-9);imu_dt=np.diff(t);max_gap=float(imu_dt.max())
    add('IMU rate','FAIL' if imu_rate<100 else 'WARN' if imu_rate<200 or max_gap>.05 else 'PASS',{'rate_hz':imu_rate,'samples':len(t),'max_gap_ms':1000*max_gap},
        'IMU stream thin or gappy (L2 ~250 Hz expected): check imu_sequence_breaks; re-record if deskew matters')
    bad=int(np.sum(~covered.astype(bool)|(np.nan_to_num(imugap,nan=1.)>.02)))
    add('IMU coverage per sweep','FAIL' if bad>.05*n else 'WARN' if bad else 'PASS',{'sweeps_not_covered':bad,'max_gap_in_sweep_ms':1000*float(np.nanmax(imugap)) if np.isfinite(imugap).any() else None},
        'sweeps without IMU across their whole span cannot be deskewed: check the IMU stream and the link')
    # ---- camera
    cam=float(hascam.mean());need_cam=args.purpose in ('calibration','commissioning')
    add('camera frames present','FAIL' if need_cam and cam<.9 else 'WARN' if cam<.95 else 'PASS',{'share':cam,'frames_with_camera':int(hascam.sum())},
        'camera frames missing: check the USB cable and rig.py status (Gemini); the step needs RGB-D' if need_cam else 'camera frames missing: check the Gemini USB cable')
    ages=age[np.isfinite(age)]*1000;repeats=int(np.sum(np.diff(camts[np.isfinite(camts)])==0)) if np.isfinite(camts).sum()>1 else 0
    if len(ages):add('camera age vs sweep end','WARN' if pct(np.abs(ages),95)>50 or repeats>.05*n else 'PASS',
                     {'median_ms':np.median(ages),'p95_abs_ms':pct(np.abs(ages),95),'max_abs_ms':float(np.abs(ages).max()),'reused_camera_frames':repeats},
                     'camera frames far from their sweep or reused: camera USB bandwidth or clock fit; check camera_fps in rig.py status')
    if scan.images:
        I=np.array(scan.images);valid,depth_m,over,under,lap,mean=(I[:,k] for k in range(6))
        add('depth','FAIL' if np.median(valid)<.1 else 'WARN' if np.median(valid)<.5 else 'PASS',{'valid_fraction_median':np.median(valid),'valid_fraction_p10':pct(valid,10),'median_depth_m':float(np.nanmedian(depth_m))},
            'little valid depth: lens covered, IR emitter off, or the view is too near/far; clean the lens and check the Gemini')
        exposure='WARN' if np.median(over)>.2 or np.median(under)>.3 or np.median(mean)<25 else 'PASS'
        add('colour exposure',exposure,{'overexposed_median':np.median(over),'underexposed_median':np.median(under),'mean_gray_median':np.median(mean)},
            'image too bright or too dark: turn the room lights on, avoid a window or lamp in view')
        blurred=float(np.mean(lap<args.min_sharpness))
        add('colour sharpness','WARN' if np.median(lap)<args.min_sharpness or (args.purpose in ('mobile','commissioning') and blurred>.25) else 'PASS',
            {'laplacian_var_median':np.median(lap),'laplacian_var_p10':pct(lap,10),'share_below_threshold':blurred,'threshold':args.min_sharpness},
            'blurred colour: clean the lens, check focus/lighting; push the cart more slowly')
    # ---- motion from the IMU
    edges=np.arange(t[0],t[-1],1.);gn=np.linalg.norm(gyro,axis=1);an=np.linalg.norm(accel,axis=1);win=[]
    for a in edges:
        m=(t>=a)&(t<a+1)
        if m.sum()>=20:win.append((float(gn[m].mean()),float(an[m].std())))
    W=np.array(win) if win else np.zeros((0,2));turning=W[:,0]>GYRO_MOVING_RAD_S;shaking=W[:,1]>ACCEL_STD_MOVING;imu_moving=turning|shaking
    k=max(1,int(round(.05*imu_rate)));smooth=np.convolve(gn,np.ones(k)/k,mode='valid') if len(gn)>=k else gn
    max_rate=float(np.degrees(smooth.max()))
    # ---- LiDAR: distance proxy (also marks windows where the scans moved) and start/end comparison
    path,net,weak,links=lidar_path(scan,t,gyro);lidar_moving=np.zeros(len(W),bool);starts=edges[:len(W)]
    for a,b,speed in links:
        if speed>LINK_MOVING_M_S:lidar_moving|=(starts<b)&(starts+1>a)
    moving=imu_moving|lidar_moving;still=float(1-moving.mean()) if len(moving) else 1.
    motion={'windows':len(W),'imu_moving_windows':int(imu_moving.sum()),'turning_windows':int(turning.sum()),'shaking_only_windows':int((shaking&~turning).sum()),
            'lidar_moving_windows':int(lidar_moving.sum()),'still_fraction':still,'max_angular_rate_deg_s':max_rate,'gyro_window_mean_rad_s_p95':pct(W[:,0],95),
            'accel_norm_std_p95':pct(W[:,1],95),'moving_window_starts_s':[float(v) for v in (starts[moving]-t[0])[:12]]}
    start=np.vstack(scan.start) if scan.start else np.zeros((0,3));end=np.vstack([p for _,p in scan.end]) if scan.end else np.zeros((0,3))
    score,yaw,zero=overlap_score(start,end);still_purpose=args.purpose in STILL_PURPOSES
    offset,turn,_=start_end_offset(scan,0. if still_purpose else yaw or 0.) if score is not None and (still_purpose or score>=.5) else (None,None,None)
    motion.update(path_proxy_m=path,chained_net_displacement_proxy_m=net,icp_weak_links=weak,start_end_overlap=score,start_end_best_yaw_deg=yaw,
                  start_end_overlap_yaw0=zero,start_end_icp_offset_m=offset,start_end_icp_rotation_deg=turn)
    summary['motion']=rnd(motion)
    if still_purpose:
        # Displacement decides (direct start/end ICP; the voxel overlap only if ICP found no fit), and so does a turn on
        # the gyro. Accelerometer-only windows are bumps or vibration (a hand on the cart or table): a warning.
        turns=int(turning.sum());shakes=int((shaking&~turning).sum())
        displaced=(offset>args.max_still_offset or turn>2.) if offset is not None else zero is not None and zero<args.min_still_overlap
        st='FAIL' if (displaced or turns>2) and args.purpose!='calibration' else 'WARN' if displaced or turns or shakes or lidar_moving.any() else 'PASS'
        add('cart stayed still',st,{'turning_windows':turns,'shaking_only_windows':shakes,'lidar_moving_windows':int(lidar_moving.sum()),'max_angular_rate_deg_s':max_rate,
                                    'start_end_icp_offset_m':offset,'start_end_icp_rotation_deg':turn,'start_end_overlap_yaw0':zero,'moving_window_starts_s':motion['moving_window_starts_s']},
            'the cart moved: park it, nobody touches it or the cords, re-record' if st=='FAIL' else
            'bumps or vibration at the listed seconds (a hand on the cart or table, a cord pulled); fine unless they repeat, else keep hands off and re-record')
    else:
        add('cart moved','FAIL' if still>.95 else 'WARN' if still>.8 else 'PASS',{'still_fraction':still,'moving_windows':int(moving.sum()),'path_proxy_m':path,'icp_weak_links':weak},
            'the cart barely moved: push it (walking pace or less) for the whole recording')
        add('max angular rate','WARN' if max_rate>args.max_rate_deg_s else 'PASS',{'max_deg_s':max_rate,'limit_deg_s':args.max_rate_deg_s,'p99_deg_s':float(np.degrees(np.percentile(smooth,99)))},
            'turned too fast for deskew: turn the cart slowly (under ~60 deg/s, a quarter turn in 2 s or more)')
        close=score is not None and score>=args.loop_overlap and (offset is None or offset<=args.loop_offset_m)
        add('start and end look alike (loop hint)','PASS' if close or args.purpose=='mobile' else 'WARN',
            {'overlap':score,'best_yaw_deg':yaw,'threshold':args.loop_overlap,'max_offset_m':args.loop_offset_m,'same_place_likely':close,'icp_offset_m':offset,'icp_rotation_deg':turn},
            'end does not look like the start: finish the loop where you started (same spot, any heading) and hold still 3 s at both ends')
    # ---- calibration holds
    if args.purpose=='calibration':
        holds=detect_holds(scan.board,args);sampled=sum(1 for _,p in scan.board if p is not None)
        dist=[float(np.linalg.norm(np.median([p[1] for p in h['poses']],axis=0))) for h in holds];us=[float(np.median([p[2] for p in h['poses']])) for h in holds]
        value={'holds_estimated':len(holds),'board_detections':sampled,'frames_sampled':len(scan.board),'distance_m_min':min(dist) if dist else None,
               'distance_m_max':max(dist) if dist else None,'image_u_min':min(us) if us else None,'image_u_max':max(us) if us else None,'hold_durations_s':[h['t1']-h['t0'] for h in holds]}
        if args.exact_holds:value['holds_exact_detector']=exact_holds(recording,args)
        count=value.get('holds_exact_detector',len(holds))
        add('checkerboard holds','FAIL' if count<10 else 'WARN' if count<12 else 'PASS',value,
            'too few still holds (need 12-20 of ~3 s): hold the monitor still longer, keep the whole board in the image, record again')
        if len(dist)>=2 and (max(dist)-min(dist)<.8 or max(us)-min(us)<.3):
            add('hold variety','WARN',{'distance_spread_m':max(dist)-min(dist),'image_u_spread':max(us)-min(us)},'holds too similar: place the board near (~1 m) and far (~2.5 m), left and right')
    summary['read_s']=read_s;return finish(recording,args,checks,summary,started)


def lidar_path(scan,t,gyro):
    """Chained ICP between keyframes (6 sweeps each, one per --keyframe-s): (path length, net displacement,
    weak links, [(t0, t1, speed m/s)] per good link); a proxy only."""
    keys=[(ts,p.astype(np.float64)) for ts,p in scan.keyframes if len(p)>=100]
    if len(keys)<2:return None,None,None,[]
    A=scan.imu_to_lidar[:3,:3];path=0.;T=np.eye(4);weak=0;v=np.zeros(3);links=[]
    for (t0,p0),(t1,p1) in zip(keys[:-1],keys[1:]):
        Ri=gyro_rotation(t,gyro,t0,t1);R,tr,share=icp(p1,p0,A@Ri@A.T,v.copy())
        if share<.3:weak+=1;v=np.zeros(3);continue
        v=tr;step_m=float(np.linalg.norm(tr));path+=step_m;links.append((t0,t1,step_m/max(t1-t0,1e-6)))
        step=np.eye(4);step[:3,:3]=R;step[:3,3]=tr;T=T@step
    return path,float(np.linalg.norm(T[:3,3])),weak,links


def start_end_offset(scan,yaw_deg=0.):
    """Direct ICP of the last keyframe onto the first (yaw-initialised): (offset m, rotation deg, inlier share) or Nones."""
    keys=[p.astype(np.float64) for _,p in scan.keyframes if len(p)>=100]
    if len(keys)<2:return None,None,None
    c,s=math.cos(math.radians(yaw_deg)),math.sin(math.radians(yaw_deg));R,t,share=icp(keys[-1],keys[0],np.array([[c,-s,0],[s,c,0],[0,0,1.]]),np.zeros(3))
    if share<.3:return None,None,share
    return float(np.linalg.norm(t)),float(np.degrees(np.arccos(np.clip((np.trace(R)-1)/2,-1,1)))),share


def finish(recording,args,checks,summary,started):
    worst=max((LEVEL[c['status']] for c in checks),default=0);result={'schema_version':1,'kind':'recording_qa','recording':str(recording),'purpose':args.purpose,
        'verdict':['PASS','WARN','FAIL'][worst],'exit_code':worst,'elapsed_s':round(time.perf_counter()-started,2),'summary':rnd(summary),'checks':checks,
        'note':'screening aid: IMU motion windows, ICP distance proxy and start/end voxel overlap are heuristics, not measured odometry or loop closure'}
    return result


def print_result(result):
    print(f"Recording QA ({result['purpose']}): {result['recording']}")
    for c in result['checks']:
        value=c['value'];text=json.dumps(value,separators=(',',':')) if isinstance(value,(dict,list)) else str(value)
        print(f"  {c['status']:<4}  {c['check']}: {text}"+(f"\n        fix: {c['hint']}" if c['hint'] else ''))
    print(f"Verdict: {result['verdict']} (exit {result['exit_code']}) in {result['elapsed_s']} s")


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('recording',type=Path);p.add_argument('--purpose',choices=PURPOSES,required=True);p.add_argument('--json',type=Path,help='Write the full result here')
    p.add_argument('--duration',type=float,help='Requested seconds when there is no capture_report.json')
    p.add_argument('--image-stride',type=int,default=2,help='Image checks on every Nth frame');p.add_argument('--keyframe-s',type=float,default=1.,help='ICP keyframe spacing, s')
    p.add_argument('--min-sharpness',type=float,default=100.,help='Variance of the Laplacian below which a colour frame counts as blurred')
    p.add_argument('--max-rate-deg-s',type=float,default=60.);p.add_argument('--loop-overlap',type=float,default=.7,help='Start/end overlap that counts as the same place (~0.8 m on the bench room)')
    p.add_argument('--min-still-overlap',type=float,default=.7,help='Start/end overlap (yaw 0) below which a still purpose counts as moved')
    p.add_argument('--loop-offset-m',type=float,default=.75,help='Start-to-end ICP offset, m, up to which the end counts as the start')
    p.add_argument('--max-still-offset',type=float,default=.1,help='Start-to-end ICP offset, m, above which a still purpose counts as moved')
    p.add_argument('--cols',type=int,default=7);p.add_argument('--rows',type=int,default=5);p.add_argument('--square',type=float,default=.04,help='Square, metres (only scales hold stillness)')
    p.add_argument('--board-hz',type=float,default=3.,help='Checkerboard search rate');p.add_argument('--hold-mm',type=float,default=25.);p.add_argument('--hold-deg',type=float,default=2.)
    p.add_argument('--min-hold-s',type=float,default=1.);p.add_argument('--exact-holds',action='store_true',help="Also count holds with calibrate_rig's detector (slow)")
    args=p.parse_args(argv)
    if not (args.recording/'manifest.json').is_file():print(f'{args.recording}: no manifest.json (not a recording)',file=sys.stderr);return 2
    result=analyse(args.recording,args);print_result(result)
    if args.json:args.json.parent.mkdir(parents=True,exist_ok=True);args.json.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result['exit_code']


if __name__=='__main__':sys.exit(main())

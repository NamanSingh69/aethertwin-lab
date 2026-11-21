"""Turn a bring-up recording of checkerboard holds into camera-LiDAR correspondences for --mode calibrate.

Procedure the human follows: hold a flat checkerboard board still for ~3 s at 12-20 positions spread
through the useful volume (near/far, left/right, high/low, tilted up to ~45 degrees), moving between
holds. Record with `tools/record_raw.py --bringup`.

Per frame the checkerboard is found in RGB and its pose solved with the camera's factory intrinsics
(from the frame diagnostics, or --intrinsics). Consecutive frames whose board pose stays within
--hold-mm / --hold-deg form a hold. For each hold:
  * image: the board's four physical corners, projected from the averaged board pose;
  * LiDAR: points from every sweep of the hold near the board, a board-like RANSAC plane cleared of
    the support, stand and clutter around it, and a rectangle of the known board size placed on its
    edges (see lidar_corners); its corners are the 3D landmarks.
tools/calibrate_rig.py runs this twice (the second time around the first solution) and installs the result.
A rough camera-to-LiDAR guess (--initial-guess, from tape measurements of the mount) is used ONLY to
find the board in the cloud and to label corners consistently; it never enters the output values.
Every --holdout-every'th hold goes to validation_* arrays for held-out scoring. The output NPZ has
lidar_points, image_points, K, distortion, image_size (+ validation_*) for `main.py --mode calibrate`
and the commissioning panel; the JSON report lists each hold's quality numbers.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

# Camera optical frame (x right, y down, z forward) into a LiDAR frame (x forward, y left, z up).
OPTICAL_TO_LIDAR=np.array([[0,0,1],[-1,0,0],[0,-1,0]],float)


def initial_guess(spec):
    """'tx,ty,tz' metres (camera origin in the LiDAR frame) with optical axes facing LiDAR +X, or a 4x4 JSON file."""
    T=np.eye(4)
    if spec and Path(spec).is_file():T=np.asarray(json.loads(Path(spec).read_text())['T_lidar_camera'],float)
    else:
        T[:3,:3]=OPTICAL_TO_LIDAR
        if spec:T[:3,3]=[float(v) for v in spec.split(',')]
    if T.shape!=(4,4):raise ValueError('initial guess must be 4x4')
    return T


def board_pose(image,K,dist,pattern,square):
    """(rvec,tvec,corners) of the checkerboard in the camera frame, or None. Board frame: grid centre, z out of the board."""
    import cv2
    gray=cv2.cvtColor(np.asarray(image,np.uint8),cv2.COLOR_RGB2GRAY)
    found,corners=cv2.findChessboardCornersSB(gray,pattern,flags=cv2.CALIB_CB_EXHAUSTIVE|cv2.CALIB_CB_ACCURACY)
    if not found:return None
    cols,rows=pattern;grid=np.zeros((cols*rows,3),np.float64)
    grid[:,:2]=np.mgrid[0:cols,0:rows].T.reshape(-1,2)*square
    grid[:,0]-=(cols-1)*square/2;grid[:,1]-=(rows-1)*square/2
    ok,rvec,tvec=cv2.solvePnP(grid,corners.reshape(-1,2).astype(np.float64),K,dist,flags=cv2.SOLVEPNP_IPPE)
    if not ok:return None
    projected,_=cv2.projectPoints(grid,rvec,tvec,K,dist)
    error=float(np.sqrt(np.mean(np.sum((projected.reshape(-1,2)-corners.reshape(-1,2))**2,axis=1))))
    return rvec.reshape(3),tvec.reshape(3),error


def rotation(rvec):
    import cv2
    return cv2.Rodrigues(np.asarray(rvec,float))[0]


def same_hold(a,b,mm,deg):
    angle=np.degrees(np.arccos(np.clip((np.trace(rotation(a[0]).T@rotation(b[0]))-1)/2,-1,1)))
    return np.linalg.norm(a[1]-b[1])*1000<=mm and angle<=deg


def fit_plane(points,threshold,iterations,rng,accept=None):
    """RANSAC plane (centre, unit normal, inlier mask) or None; `accept(normal)` filters hypotheses and the refit."""
    best=None
    for _ in range(iterations):
        sample=points[rng.choice(len(points),3,replace=False)];normal=np.cross(sample[1]-sample[0],sample[2]-sample[0])
        norm=np.linalg.norm(normal)
        if norm<1e-9:continue
        normal/=norm
        if accept is not None and not accept(normal):continue
        inliers=np.abs((points-sample[0])@normal)<=threshold
        if best is None or inliers.sum()>best.sum():best=inliers
    if best is None or best.sum()<3:return None
    inlier=points[best];centre=inlier.mean(0);_,_,vt=np.linalg.svd(inlier-centre)
    normal=vt[2];inliers=np.abs((points-centre)@normal)<=threshold
    if accept is not None and not accept(normal):return None
    return centre,normal,inliers


def largest_component(flat,cell):
    """Mask of the in-plane points in the largest 8-connected cluster on a `cell`-metre grid."""
    import cv2
    keys=np.floor(flat/cell).astype(np.int64);keys-=keys.min(0)
    grid=np.zeros(tuple(keys.max(0)+1),np.uint8);grid[keys[:,0],keys[:,1]]=1
    _,labels=cv2.connectedComponents(grid,connectivity=8);label=labels[keys[:,0],keys[:,1]]
    return label==np.argmax(np.bincount(label))


def lidar_corners(points,predicted,board_size,args,rng):
    """Board rectangle corners in the LiDAR frame, ordered like `predicted` (4x3, board-frame corner order).

    A monitor on its stand shares the search region with its support: the floor or seat meets the board plane
    in a line under the bottom edge, a stand neck can sit just behind it, and parallel clutter behind it (a
    desk panel, a wall) can hold more points than the board. So only planes within --max-normal-deg of the
    predicted normal are fitted, each is reduced to its largest in-plane cluster (gaps split off the floor
    line), its edges come from the points inside the best board-sized window (+ --edge-margin, so a narrow
    neck or a stray cluster cannot stretch them), and up to --max-planes planes are tried until one matches
    the declared board size. The guess only selects and labels points; the corners come from the data.
    """
    centre_guess=predicted.mean(0);u=predicted[1]-predicted[0];v=predicted[3]-predicted[0]
    normal_guess=np.cross(u,v);normal_guess/=np.linalg.norm(normal_guess)
    local=points-centre_guess
    near=(np.abs(local@normal_guess)<=args.plane_margin)&(np.linalg.norm(local,axis=1)<=np.linalg.norm(u+v)/2+args.region_margin)
    candidates=points[near]
    if len(candidates)<args.min_points:return None,{'reason':f'only {len(candidates)} LiDAR points near the predicted board'}
    gate=np.cos(np.radians(args.max_normal_deg));board_like=lambda n:abs(float(n@normal_guess))>=gate
    tried=[];best=None
    for attempt in range(args.max_planes):
        if len(candidates)<args.min_points:break
        fitted=fit_plane(candidates,args.plane_threshold,args.ransac_iterations,rng,accept=board_like)
        if fitted is None:break
        centre,normal,inliers=fitted;board=candidates[inliers];candidates=candidates[~inliers]
        if len(board)<args.min_points:tried.append(f'plane {attempt}: only {len(board)} inliers');break
        x0=u-(u@normal)*normal;x0/=np.linalg.norm(x0);y0=np.cross(normal,x0)
        cell=float(np.clip(2*np.sqrt(np.prod(board_size)/len(board)),args.min_cell,args.max_cell))
        keep=largest_component(np.c_[(board-centre)@x0,(board-centre)@y0],cell);dropped=int((~keep).sum());board=board[keep]
        if len(board)<args.min_points:tried.append(f'plane {attempt}: largest cluster has only {len(board)} points');continue
        corners,quality=board_rectangle(board,u,v,board_size,args)
        quality.update(plane_attempt=attempt,cluster_cell_m=cell,cluster_points_dropped=dropped,
                       normal_angle_to_guess_deg=float(np.degrees(np.arccos(min(1.,abs(float(np.asarray(quality['normal'])@normal_guess)))))),
                       max_corner_shift_from_guess_m=float(np.max(np.linalg.norm(corners-predicted,axis=1))))
        problem=extent_problem(quality,args)
        if problem is None:
            if tried:quality['rejected_planes']=tried
            return corners,quality
        tried.append(f'plane {attempt}: {problem}')
        if best is None or np.max(np.abs(np.log(quality['extent_ratio'])))<np.max(np.abs(np.log(best[1]['extent_ratio']))):best=(corners,quality)
    if best is not None:best[1]['rejected_planes']=tried;return best
    return None,{'reason':'no board-like plane'+(f" ({'; '.join(tried)})" if tried else '')}


def extent_problem(quality,args):
    """Why a fitted rectangle is not the declared board, or None."""
    ratios=np.asarray(quality['extent_ratio'],float)
    if not np.all((ratios>=1/args.max_extent_ratio)&(ratios<=args.max_extent_ratio)):return f'extent ratio {np.round(ratios,3).tolist()}'
    if quality['window_fraction']<args.min_window_fraction:return f"only {quality['window_fraction']:.0%} of the plane's points fit a board-sized window"
    return None


def window_centre(values,size):
    """Centre of a `size`-long window placed to hold the most values (median over the best placements)."""
    s=np.sort(values);i=np.arange(len(s));j=np.searchsorted(s,s+size,side='right')-1;count=j-i+1;best=np.flatnonzero(count==count.max())
    return float(np.median((s[j[best]]-size+s[best])/2+size/2))  # left edge L holds s[i..j] for L in [s[j]-size, s[i]]


def board_rectangle(board,u,v,board_size,args):
    # Start from the predicted edge directions on the fitted plane, then take the in-plane rotation from the
    # data: the bounding box of a uniformly sampled rectangle has its smallest area when aligned with its edges.
    # (The guess only resolves which edge is which; its own rotation error must not reach the corners.)
    # The edges then come from the points inside the best board-sized window (+ --edge-margin), refitted once.
    # Range noise moves a point along its ray (the sensor is at the origin), so each point is re-placed where its
    # ray meets the fitted plane: that removes the noise from the in-plane coordinates and keeps the edges sharp.
    # A turned or raised monitor also shows the sensor one side face (and the stand neck under it), which looks
    # like board just beyond the nearer edge; on such an axis the centre comes from the far edge and the known size.
    size=np.asarray(board_size,float);half=size/2;keep=np.ones(len(board),bool);cluster=len(board);search=args.max_inplane_deg;hint=u
    for _ in range(2):
        points=board[keep];centre=points.mean(0);normal=np.linalg.svd(points-centre)[2][2]
        along=board*((normal@centre)/(board@normal))[:,None]
        x0=hint-(hint@normal)*normal;x0/=np.linalg.norm(x0);y0=np.cross(normal,x0)
        if y0@v<0:y0=-y0
        flat=np.c_[(along[keep]-centre)@x0,(along[keep]-centre)@y0]
        def area(theta):
            c,s=np.cos(theta),np.sin(theta);rotated=flat@np.array([[c,-s],[s,c]])
            span=np.percentile(rotated,100-args.extent_percentile,axis=0)-np.percentile(rotated,args.extent_percentile,axis=0)
            return span[0]*span[1]
        angles=np.radians(np.arange(-search,search+1e-9,.5));theta=angles[np.argmin([area(a) for a in angles])]
        fine=theta+np.radians(np.arange(-.5,.5001,.02));theta=fine[np.argmin([area(a) for a in fine])]
        x=np.cos(theta)*x0+np.sin(theta)*y0;y=-np.sin(theta)*x0+np.cos(theta)*y0
        coords=np.c_[(along-centre)@x,(along-centre)@y]
        window=np.array([window_centre(coords[:,k],size[k]) for k in (0,1)])
        keep=np.all(np.abs(coords-window)<=half+args.edge_margin,axis=1);search=1.;hint=x
    kept=coords[keep];guess_x=u-(u@normal)*normal;guess_x/=np.linalg.norm(guess_x);p=args.extent_percentile/100
    # The p-th percentile lies p*N points inside the edge: p*N/density, with the point density along the axis taken
    # just inside that edge (the far part of a turned board is sparser than the near part).
    low=np.percentile(kept,100*p,axis=0);high=np.percentile(kept,100*(1-p),axis=0);strip=np.maximum(.03,.15*size)
    for k in (0,1):
        values=kept[:,k];n=p*len(values)
        low[k]-=n*strip[k]/max(1,np.count_nonzero((values>=low[k])&(values<low[k]+strip[k])))
        high[k]+=n*strip[k]/max(1,np.count_nonzero((values<=high[k])&(values>high[k]-strip[k])))
    extent=high-low
    sensor=np.array([-centre@x,-centre@y])-(low+high)/2;mid=(low+high)/2;edges=[]
    for k in (0,1):
        if sensor[k]>half[k]:mid[k]=low[k]+half[k];edges.append('low')  # the +edge's side face is in view
        elif sensor[k]<-half[k]:mid[k]=high[k]-half[k];edges.append('high')
        else:edges.append('both')
    origin=centre+mid[0]*x+mid[1]*y
    corners=np.array([origin+sx*half[0]*x+sy*half[1]*y for sx,sy in ((-1,-1),(1,-1),(1,1),(-1,1))])
    points=board[keep];rms=float(np.sqrt(np.mean(((points-centre)@normal)**2)))
    quality={'plane_inliers':int(keep.sum()),'cluster_points':int(cluster),'window_fraction':float(keep.mean()),'plane_rms_m':rms,
             'normal':normal.tolist(),'observed_extent_m':extent.tolist(),'board_size_m':list(board_size),'extent_ratio':(extent/size).tolist(),
             'edges_used':edges,'inplane_correction_deg':float(np.degrees(np.arctan2(x@np.cross(normal,guess_x),x@guess_x)))}
    return corners,quality


def detect_holds(recording,args):
    """Still board poses in the recording: (holds, K, distortion, image_size, intrinsics_source)."""
    from ingestion.recorder_replayer import ReplaySource
    pattern=(args.cols,args.rows);holds=[];current=None;K=dist=None;image_size=None;intrinsics_source=None
    if args.intrinsics:
        data=json.loads(Path(args.intrinsics).read_text());K=np.asarray(data['K'],float);dist=np.asarray(data.get('distortion',[0]*5),float)
        intrinsics_source=str(args.intrinsics)
    source=ReplaySource(recording)
    try:
        for frame in source:
            if frame.rgb is None:continue
            if K is None:
                validation=(frame.diagnostics.get('camera_calibration_validation') or {}).get('color') or {}
                if 'K' not in validation:raise ValueError('No camera intrinsics in the recording; pass --intrinsics')
                K=np.asarray(validation['K'],float);dist=np.asarray(validation.get('distortion',[0]*5),float)
                intrinsics_source='SDK factory intrinsics recorded in the frame diagnostics'
            image_size=[int(frame.rgb.shape[1]),int(frame.rgb.shape[0])]
            pose=board_pose(frame.rgb,K,dist,pattern,args.square)
            if pose is None or pose[2]>args.max_reprojection_px:
                current=None;continue
            if current is not None and same_hold(current['poses'][0],pose,args.hold_mm,args.hold_deg):
                current['poses'].append(pose);current['clouds'].append(np.asarray(frame.lidar_points[:,:3],np.float32))
            else:
                current={'poses':[pose],'clouds':[np.asarray(frame.lidar_points[:,:3],np.float32)],'first_frame':int(frame.frame_id)};holds.append(current)
    finally:source.close()
    return [h for h in holds if len(h['poses'])>=args.min_hold_frames],K,dist,image_size,intrinsics_source


def build_targets(holds,K,dist,image_size,args,guess,*,validation_holds=None,intrinsics_source=None,recording=None):
    """(arrays or None, summary): image and LiDAR board corners per hold, located with the 4x4 `guess`.

    `validation_holds` (hold indexes) fixes the held-out set, so a second pass scores the same holds;
    by default every --holdout-every'th accepted hold is held out.
    """
    import cv2
    rng=np.random.default_rng(args.seed);pattern=(args.cols,args.rows)
    W,H=args.board_size;outer=np.array([[-W/2,-H/2,0],[W/2,-H/2,0],[W/2,H/2,0],[-W/2,H/2,0]],float)
    accepted=[];report=[]
    for index,hold in enumerate(holds):
        rvecs=np.array([p[0] for p in hold['poses']]);tvecs=np.array([p[1] for p in hold['poses']])
        rvec=np.median(rvecs,axis=0);tvec=np.median(tvecs,axis=0)
        image,_=cv2.projectPoints(outer,rvec,tvec,K,dist);image=image.reshape(4,2)
        camera_corners=(rotation(rvec)@outer.T).T+tvec
        predicted=(guess[:3,:3]@camera_corners.T).T+guess[:3,3]
        corners,quality=lidar_corners(np.vstack(hold['clouds']).astype(float),predicted,args.board_size,args,rng)
        entry={'hold':index,'first_frame':hold['first_frame'],'frames':len(hold['poses']),'camera_distance_m':float(np.linalg.norm(tvec)),
               'pnp_rms_px':float(np.median([p[2] for p in hold['poses']])),**quality}
        inside=bool(np.all((image>=0)&(image<=np.asarray(image_size)-1)))
        ok=corners is not None and inside and extent_problem(quality,args) is None
        if corners is not None and not inside:entry['reason']='a board corner is outside the image'
        elif corners is not None and not ok:entry['reason']='LiDAR board extent disagrees with the declared board size'
        entry['accepted']=bool(ok);report.append(entry)
        if ok:accepted.append((index,corners,image))
    if validation_holds is None:
        validation_holds={a[0] for i,a in enumerate(accepted) if args.holdout_every and (i+1)%args.holdout_every==0}
    validation=[a for a in accepted if a[0] in validation_holds];training=[a for a in accepted if a[0] not in validation_holds]
    summary={'schema_version':1,'recording':None if recording is None else str(recording),'intrinsics_source':intrinsics_source,
             'pattern_inner_corners':list(pattern),'square_m':args.square,'board_size_m':list(args.board_size),'holds_found':len(holds),
             'holds_accepted':len(accepted),'training_holds':[a[0] for a in training],'validation_holds':[a[0] for a in validation],
             'initial_guess_used_for':'board search region and corner labelling only','holds':report}
    if len(accepted)<2 or not training:
        summary['error']=f'Only {len(accepted)} usable holds (need >=2, ideally 12+)';return None,summary
    arrays={'lidar_points':np.vstack([c for _,c,_ in training]),'image_points':np.vstack([p for _,_,p in training]),
            'K':K,'distortion':dist,'image_size':np.asarray(image_size)}
    if validation:
        arrays.update(validation_lidar_points=np.vstack([c for _,c,_ in validation]),validation_image_points=np.vstack([p for _,_,p in validation]))
    summary.update(training_landmarks=int(len(arrays['lidar_points'])),validation_landmarks=int(4*len(validation)))
    return arrays,summary


def extract(recording,args):
    """(arrays, summary) with the --initial-guess; SystemExit when fewer than two holds are usable."""
    holds,K,dist,image_size,source=detect_holds(recording,args)
    arrays,summary=build_targets(holds,K,dist,image_size,args,initial_guess(args.initial_guess),intrinsics_source=source,recording=recording)
    if arrays is None:raise SystemExit(summary['error']+'; see the report for reasons')
    return arrays,summary


def parser():
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('recording',type=Path);p.add_argument('--output',type=Path,required=True,help='Output .npz (a .json report is written next to it)')
    return add_target_arguments(p)


def add_target_arguments(p):
    """Board, hold and LiDAR-fit options (shared with tools/calibrate_rig.py)."""
    p.add_argument('--cols',type=int,required=True,help='Inner corners per row');p.add_argument('--rows',type=int,required=True,help='Inner corners per column')
    p.add_argument('--square',type=float,required=True,help='Square size, metres')
    p.add_argument('--board-size',type=float,nargs=2,required=True,metavar=('WIDTH','HEIGHT'),help='Physical board, metres (pattern centred on it)')
    p.add_argument('--initial-guess',help="Camera origin in the LiDAR frame 'tx,ty,tz' (metres, from the mount) or a JSON with T_lidar_camera")
    p.add_argument('--intrinsics',type=Path,help='JSON with K/distortion instead of the recorded factory intrinsics')
    p.add_argument('--hold-mm',type=float,default=8.);p.add_argument('--hold-deg',type=float,default=.8);p.add_argument('--min-hold-frames',type=int,default=8)
    p.add_argument('--holdout-every',type=int,default=4);p.add_argument('--max-reprojection-px',type=float,default=1.5)
    p.add_argument('--plane-margin',type=float,default=.25);p.add_argument('--region-margin',type=float,default=.3)
    p.add_argument('--plane-threshold',type=float,default=.02);p.add_argument('--ransac-iterations',type=int,default=300)
    p.add_argument('--min-points',type=int,default=150);p.add_argument('--extent-percentile',type=float,default=.5)
    p.add_argument('--max-extent-ratio',type=float,default=1.25);p.add_argument('--seed',type=int,default=0)
    p.add_argument('--max-inplane-deg',type=float,default=20.,help='Search range for the board rotation in its plane around the guess')
    p.add_argument('--max-normal-deg',type=float,default=30.,help='Board planes lie within this of the predicted normal; planes further off are supports')
    p.add_argument('--max-planes',type=int,default=3,help='Board-like planes tried in turn (clutter behind the board can hold more points)')
    p.add_argument('--edge-margin',type=float,default=.02,help='Points this far outside the best board-sized window still count for the edges, metres')
    p.add_argument('--min-window-fraction',type=float,default=.8,help="Share of a plane cluster's points that must fit the board-sized window")
    p.add_argument('--min-cell',type=float,default=.02);p.add_argument('--max-cell',type=float,default=.06,help='In-plane cluster grid limits, metres')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    holds,K,dist,image_size,source=detect_holds(args.recording,args)
    arrays,summary=build_targets(holds,K,dist,image_size,args,initial_guess(args.initial_guess),intrinsics_source=source,recording=args.recording)
    args.output.parent.mkdir(parents=True,exist_ok=True);report=args.output.with_suffix('.json')
    report.write_text(json.dumps(summary,indent=2),encoding='utf-8')
    if arrays is None:raise SystemExit(f"{summary['error']}; see {report} for each hold's reason")
    np.savez(args.output,**arrays)
    print(json.dumps({k:summary[k] for k in ('holds_found','holds_accepted','training_landmarks','validation_landmarks','intrinsics_source')}))
    return 0


if __name__=='__main__':sys.exit(main())

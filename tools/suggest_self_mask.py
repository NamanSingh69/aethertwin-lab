"""Suggest lidar_self_mask boxes from a raw recording made while the robot is parked.

This tool only SUGGESTS. It prints a `lidar_self_mask` section (also written as YAML next to the JSON report)
and never edits config/system_config.yaml or any other config: review the boxes and the report, then paste the
section in by hand. The method is validated only on synthetic recordings (tests/test_suggest_self_mask.py)
until it has been run on the real rig; its numbers describe the recording it was given, nothing more.

Procedure: park the robot on a flat floor with nothing else static within --radius of the LiDAR, step out of
that radius (people who keep moving fail the persistence test, people who stand still do not), and record
about 30 s raw:
    python tools/record_raw.py --output D:/aethertwin/rec/self_mask_parked --duration 30 --motion stationary
    python tools/suggest_self_mask.py D:/aethertwin/rec/self_mask_parked --lidar-height 0.40 \
        --robot-size 0.50 0.40 0.48 --lidar-offset 0.05 0 --output reports/self_mask/suggestion.json

Method (LiDAR frame, metres):
  * returns within --radius are binned into --voxel cubes; a voxel is persistent when it is hit in at least
    --persistence of the persistence windows (--window consecutive frames form one window: use > 1 for
    non-repetitive scan patterns that cover a surface only over several frames);
  * the floor is excluded: with --lidar-height it is the plane z = -height (LiDAR assumed level), otherwise a
    RANSAC plane through the persistent voxels (normal within --max-floor-tilt of +z; of the planes with at
    least half the best support, the one with the fewest persistent voxels below it); voxels and returns less than --floor-clearance above it count as floor;
  * the remaining persistent voxels are clustered (voxels whose centres are within --link, per axis, join),
    clusters under --min-voxels are dropped, and each cluster becomes the bounding box of its returns plus
    --margin, never reaching down into the floor clearance; beyond MAX_BOXES clusters, the pair whose merged
    box adds the least volume is merged until the cap holds;
  * a last pass applies the boxes with LidarSelfMask and counts what they remove: returns per box per frame,
    the share of near returns, near floor and other near returns, and the far scene (beyond --radius, ~0).
With --robot-size (outer length x, width y, height z from the floor; --lidar-offset is the LiDAR's x y from
the footprint centre, LiDAR axes assumed parallel to the robot's) each box, less its margin, must lie inside
the robot's outer box plus 2 cm (range noise), or a warning names it.

Limitations: body parts within --floor-clearance of the floor (tyre contact patches) stay as floor; anything
else static inside --radius (a wall, a table leg, a person standing still) is persistent and becomes a box
(the footprint check and the "reaches the radius" warning flag such boxes); boxes are axis-aligned, so a
diagonal part gets a loose box; the robot must stay still for the whole recording.
"""
from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ingestion.self_mask import LidarSelfMask,MAX_BOXES

EVIDENCE=('Suggestion only, from one parked recording; the method is validated on synthetic recordings only until it '
          'has been run on the real rig. Review the boxes and the far-scene count before enabling the mask.')
FOOTPRINT_SLACK=.02  # range noise allowance (m) on top of the margin when checking boxes against the robot's outer size


def valid_xyz(frame):
    xyz=np.asarray(frame.lidar_points[:,:3],float);ok=np.isfinite(xyz).all(axis=1)&(np.einsum('ij,ij->i',xyz,xyz)>1e-12)
    return xyz[ok],int((~ok).sum())


def frames_of(open_frames,skip=0,limit=None):
    """Valid xyz of each used frame (re-opens the source, so each pass streams the recording again)."""
    source=open_frames()
    try:
        for index,frame in enumerate(source):
            if index<skip:continue
            if limit is not None and index>=skip+limit:break
            yield valid_xyz(frame)
    finally:
        close=getattr(source,'close',None)
        if close:close()


class Grid:
    """Sparse voxel keys for points within `radius` of the LiDAR."""
    def __init__(self,radius,voxel):self.radius=radius;self.voxel=voxel;self.n=int(math.ceil(2*radius/voxel))+1
    def keys(self,xyz):
        index=np.clip(np.floor((xyz+self.radius)/self.voxel).astype(np.int64),0,self.n-1)
        return (index[:,0]*self.n+index[:,1])*self.n+index[:,2]
    def centres(self,keys):
        keys=np.asarray(keys,np.int64);index=np.c_[keys//(self.n*self.n),keys//self.n%self.n,keys%self.n]
        return (index+.5)*self.voxel-self.radius


def lookup(sorted_keys,keys):
    """Position of each key in sorted_keys, -1 where absent."""
    if not len(sorted_keys):return np.full(len(keys),-1)
    at=np.clip(np.searchsorted(sorted_keys,keys),0,len(sorted_keys)-1);return np.where(sorted_keys[at]==keys,at,-1)


def fit_floor(centres,voxel,max_tilt,seed=0,iterations=500,min_support=30):
    """(normal, offset, support) with height = xyz @ normal + offset, or None. Hypotheses are refined by least
    squares on their inliers; planes with persistent voxels below them are rejected (the floor has none; a plane
    through the chassis bottom or tilted across floor and body does); of the near-horizontal planes below the
    LiDAR with at least half the best support, the one with the fewest voxels below it, then the most support."""
    if len(centres)<min_support:return None
    rng=np.random.default_rng(seed);cos=math.cos(math.radians(max_tilt));tol=voxel;hypotheses=[]
    def refine(normal,offset):  # least squares on the inliers (voxel centres quantise the floor into one or two layers)
        inliers=centres[np.abs(centres@normal+offset)<tol]
        if len(inliers)<3:return normal,offset
        mean=inliers.mean(axis=0);centred=inliers-mean;normal=np.linalg.eigh(centred.T@centred)[1][:,0];normal=-normal if normal[2]<0 else normal
        return normal,float(-normal@mean)
    for _ in range(iterations):
        a,b,c=centres[rng.choice(len(centres),3,replace=False)];normal=np.cross(b-a,c-a);norm=np.linalg.norm(normal)
        if norm<1e-12:continue
        normal/=norm;normal=-normal if normal[2]<0 else normal;offset=-normal@a
        if normal[2]<cos or offset<=0:continue  # near-horizontal and below the LiDAR
        normal,offset=refine(*refine(normal,offset))
        if normal[2]<cos or offset<=0:continue
        height=centres@normal+offset;support=int((np.abs(height)<tol).sum());below=int((height<-2*tol).sum())
        if below<=max(3,.02*support):hypotheses.append((support,below,offset,normal))  # a body surface has the wheels below it
    if not hypotheses:return None
    top=max(h[0] for h in hypotheses)
    if top<min_support:return None
    support,_,offset,normal=min((h for h in hypotheses if h[0]>=top/2),key=lambda h:(h[1],-h[0]))
    return normal,float(offset),support


def cluster(centres,link):
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    if not len(centres):return np.zeros(0,int)
    pairs=cKDTree(centres).query_pairs(link+1e-9,p=np.inf,output_type='ndarray')
    graph=coo_matrix((np.ones(len(pairs)),(pairs[:,0],pairs[:,1])),shape=(len(centres),)*2)
    return connected_components(graph,directed=False)[1]


def merge_to_cap(extents,cap):
    """Greedily merge (low, high, voxels, reach) extents with the least added volume until at most `cap` remain."""
    extents=[list(e) for e in extents];merged=0
    while len(extents)>cap:
        low=np.array([e[0] for e in extents]);high=np.array([e[1] for e in extents]);own=np.prod(high-low,axis=1)
        grow=np.prod(np.maximum(high[:,None],high[None])-np.minimum(low[:,None],low[None]),axis=2)-own[:,None]-own[None]
        grow[np.tril_indices(len(extents))]=np.inf;i,j=np.unravel_index(np.argmin(grow),grow.shape)
        extents[i]=[np.minimum(low[i],low[j]),np.maximum(high[i],high[j]),extents[i][2]+extents[j][2],max(extents[i][3],extents[j][3])];del extents[j];merged+=1
    return extents,merged


def floor_z(floor,x,y):
    normal,offset,_=floor;return -(normal[0]*x+normal[1]*y+offset)/normal[2]


def snippet(boxes,source,frames):
    lines=[f'# Suggested by tools/suggest_self_mask.py from {source} ({frames} frames). A suggestion, not a measurement:',
           '# validated on synthetic recordings only; review the boxes and the JSON report before enabling.',
           'lidar_self_mask:','  enabled: true','  min_range: 0.0','  boxes:']
    vec=lambda v:'['+', '.join(f'{float(x):.3f}' for x in v)+']'
    lines+=[f"    - {{name: {b['name']}, min: {vec(b['min'])}, max: {vec(b['max'])}}}" for b in boxes]
    return '\n'.join(lines)+'\n'


def suggest(open_frames,*,radius=.8,voxel=.02,persistence=.5,window=1,margin=.02,lidar_height=None,floor_clearance=.03,
            max_floor_tilt=15.,link=.04,min_voxels=4,robot_size=None,lidar_offset=(0.,0.),skip=0,max_frames=None,source=''):
    """(report, yaml_text) for frames from `open_frames()` (a re-openable source, e.g. lambda: ReplaySource(path))."""
    if not (radius>0 and voxel>0 and 0<persistence<=1 and window>=1 and margin>=0 and floor_clearance>=0 and link>=0 and min_voxels>=1):
        raise ValueError('need radius, voxel > 0, 0 < persistence <= 1, window >= 1, margin, floor clearance, link >= 0, min voxels >= 1')
    grid=Grid(radius,voxel);r2=radius*radius;warnings=[]
    # Pass 1: in how many persistence windows each near voxel is hit.
    hits=[];pending=[];frames=invalid=0
    for xyz,bad in frames_of(open_frames,skip,max_frames):
        frames+=1;invalid+=bad;pending.append(grid.keys(xyz[np.einsum('ij,ij->i',xyz,xyz)<r2]))
        if len(pending)==window:hits.append(np.unique(np.concatenate(pending)));pending=[]
    windows=len(hits)
    if windows==0:raise ValueError(f'recording has {frames} usable frames, fewer than one persistence window of {window}')
    if windows<10:warnings.append(f'only {windows} persistence windows: persistence is coarse; record longer')
    keys,counts=np.unique(np.concatenate(hits),return_counts=True);persistent=keys[counts>=persistence*windows]
    centres=grid.centres(persistent)
    # Floor.
    if lidar_height is not None:floor=(np.array([0.,0.,1.]),float(lidar_height),None);floor_source='lidar_height'
    else:
        floor=fit_floor(centres,voxel,max_floor_tilt);floor_source='ransac' if floor else None
        if floor is None:warnings.append('no floor plane found among the persistent returns; nothing is excluded as floor')
    above=centres@floor[0]+floor[1]>=floor_clearance if floor else np.ones(len(centres),bool)
    body_keys=persistent[above];labels=cluster(centres[above],link)
    sizes=np.bincount(labels) if len(labels) else np.zeros(0,int);kept=np.flatnonzero(sizes>=min_voxels)
    remap=np.full(len(sizes),-1);remap[kept]=np.arange(len(kept));voxel_cluster=remap[labels] if len(labels) else labels
    body_keys,voxel_cluster=body_keys[voxel_cluster>=0],voxel_cluster[voxel_cluster>=0]
    small=np.flatnonzero(sizes<min_voxels);above_centres=centres[above]
    dropped=[{'voxels':int(sizes[k]),'centre':[round(float(v),3) for v in above_centres[labels==k].mean(axis=0)]} for k in small]
    def classify(xyz):
        near=np.einsum('ij,ij->i',xyz,xyz)<r2;height=xyz@floor[0]+floor[1] if floor else np.full(len(xyz),np.inf)
        member=np.full(len(xyz),-1);at=lookup(body_keys,grid.keys(xyz));inside=near&(at>=0)&(height>=floor_clearance)
        member[inside]=voxel_cluster[at[inside]];return near,height,member
    # Pass 2: extents of each cluster's returns.
    count=len(kept);low=np.full((count,3),np.inf);high=np.full((count,3),-np.inf);reach=np.zeros(count)
    if count:
        for xyz,_ in frames_of(open_frames,skip,max_frames):
            _,_,member=classify(xyz);sel=member>=0
            np.minimum.at(low,member[sel],xyz[sel]);np.maximum.at(high,member[sel],xyz[sel])
            np.maximum.at(reach,member[sel],np.linalg.norm(xyz[sel],axis=1))
    extents=[(low[k],high[k],int((voxel_cluster==k).sum()),float(reach[k])) for k in range(count) if np.isfinite(low[k]).all()]
    extents.sort(key=lambda e:-e[2]);extents,merged=merge_to_cap(extents,MAX_BOXES)
    if merged:warnings.append(f'{len(extents)+merged} clusters exceed MAX_BOXES={MAX_BOXES}: {merged} merges (least added volume) made larger boxes')
    boxes=[]
    for k,(lo,hi,voxels,reach_k) in enumerate(extents):
        lo=np.floor((lo-margin)*1000)/1000;hi=np.ceil((hi+margin)*1000)/1000;clamped=False
        if floor:
            limit=max(floor_z(floor,x,y) for x in (lo[0],hi[0]) for y in (lo[1],hi[1]))+floor_clearance  # floor clearance under every corner
            if lo[2]<limit:lo[2]=math.ceil(limit*1000)/1000;clamped=True
        if lo[2]>=hi[2]:continue
        box={'name':f'body_{k:02d}','min':[round(float(v),3) for v in lo],'max':[round(float(v),3) for v in hi],'voxels':voxels,
             'clamped_to_floor_clearance':clamped,'reaches_radius':reach_k>radius-2*voxel}
        if box['reaches_radius']:warnings.append(f"{box['name']} reaches the {radius} m analysis radius: the body is larger than --radius or a static object is nearby")
        boxes.append(box)
    for item in dropped:
        item['inside_a_box']=any(all(b['min'][i]<=item['centre'][i]<=b['max'][i] for i in range(3)) for b in boxes)
    outside=[item for item in dropped if not item['inside_a_box']]
    if outside:warnings.append(f'{len(outside)} persistent clusters under {min_voxels} voxels outside every box were dropped (voxels.dropped_clusters): thin body parts? lower --min-voxels')
    footprint=None
    if robot_size is not None:
        length,width,height=map(float,robot_size);ox,oy=map(float,lidar_offset);h=lidar_height if lidar_height is not None else (floor[1] if floor else None)
        fp_low=np.array([-length/2-ox,-width/2-oy,-h if h is not None else -np.inf]);fp_high=np.array([length/2-ox,width/2-oy,height-h if h is not None else np.inf])
        tolerance=margin+FOOTPRINT_SLACK;footprint={'min':[float(v) if np.isfinite(v) else None for v in fp_low],'max':[float(v) if np.isfinite(v) else None for v in fp_high],
                                        'tolerance_m':tolerance,'z_checked':h is not None}
        if h is None:warnings.append('footprint z not checked: give --lidar-height (no floor plane was found)')
        for box in boxes:
            excess=float(max(0.,np.max(np.maximum(fp_low-tolerance-np.array(box['min']),np.array(box['max'])-fp_high-tolerance))))
            box['outside_footprint_m']=round(excess,3)
            if excess>0:warnings.append(f"{box['name']} sticks {excess:.3f} m outside the robot's outer size: not the body, or the size/offset is wrong")
    config={'enabled':True,'min_range':0.,'boxes':[{k:b[k] for k in ('name','min','max')} for b in boxes]}
    report={'tool':'tools/suggest_self_mask.py','suggestion_only':True,'evidence':EVIDENCE,'recording':source,'frame':'lidar',
            'frames_used':frames,'invalid_returns_ignored':invalid,'persistence_windows':windows,
            'parameters':{'radius_m':radius,'voxel_m':voxel,'persistence':persistence,'window_frames':window,'margin_m':margin,
                          'lidar_height_m':lidar_height,'floor_clearance_m':floor_clearance,'max_floor_tilt_deg':max_floor_tilt,'link_m':link,
                          'min_voxels':min_voxels,'robot_size_m':list(robot_size) if robot_size is not None else None,'lidar_offset_m':list(lidar_offset),
                          'skip_frames':skip,'max_frames':max_frames},
            'floor':{'source':floor_source,'normal':[float(v) for v in floor[0]],'offset_m':floor[1],'support_voxels':floor[2]} if floor else {'source':None},
            'voxels':{'near_hit':int(len(keys)),'persistent':int(len(persistent)),'floor':int((~above).sum()),'body':int(len(body_keys)),
                      'clusters':int(count),'merges':merged,'dropped_clusters':dropped},
            'boxes':boxes,'footprint':footprint,'lidar_self_mask':config,'warnings':warnings}
    if not boxes:
        warnings.append('no persistent body returns above the floor: nothing to suggest');report['removal']=None;return report,None
    # Pass 3: what the suggested boxes remove.
    mask=LidarSelfMask(config['boxes']);per_box=np.zeros(len(boxes));per_box_max=np.zeros(len(boxes),int);n=dict.fromkeys(
        ('near','near_removed','body','body_removed','floor','floor_removed','other','other_removed','far','far_removed'),0)
    for xyz,_ in frames_of(open_frames,skip,max_frames):
        near,height,member=classify(xyz);removed=mask.body(xyz);floor_hit=near&(height<floor_clearance);body=member>=0;other=near&~body&~floor_hit
        for k,(_,lo,hi) in enumerate(mask.boxes):
            inside=int(np.all((xyz>=lo)&(xyz<=hi),axis=1).sum());per_box[k]+=inside;per_box_max[k]=max(per_box_max[k],inside)
        for name,sel in (('near',near),('body',body),('floor',floor_hit),('other',other),('far',~near)):
            n[name]+=int(sel.sum());n[name+'_removed']+=int((sel&removed).sum())
    for k,box in enumerate(boxes):box['returns_per_frame_mean']=round(float(per_box[k]/frames),2);box['returns_per_frame_max']=int(per_box_max[k])
    share=lambda a,b:round(n[a]/n[b],6) if n[b] else None
    report['removal']={**n,'near_removed_fraction':share('near_removed','near'),'body_removed_fraction':share('body_removed','body'),
                       'floor_removed_fraction':share('floor_removed','floor'),'other_near_removed_fraction':share('other_removed','other'),
                       'far_removed_fraction':share('far_removed','far'),'removed_per_frame_mean':round((n['near_removed']+n['far_removed'])/frames,2),
                       'note':'body = returns in the persistent clustered voxels above the floor; other = transient near returns (people, noise) and dropped clusters'}
    if n['far'] and n['far_removed']/n['far']>1e-3:warnings.append(f"boxes remove {n['far_removed']/n['far']:.2%} of the far scene (beyond {radius} m)")
    if n['body'] and n['body_removed']/n['body']<.99:warnings.append(f"boxes remove only {n['body_removed']/n['body']:.2%} of the persistent body returns")
    if n['floor'] and n['floor_removed']/n['floor']>1e-3:warnings.append(f"boxes remove {n['floor_removed']/n['floor']:.2%} of the near floor returns")
    return report,snippet(boxes,source,frames)


def parser():
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('recording',type=Path,help='Raw recording (ReplaySource directory) made while the robot is parked')
    p.add_argument('--output',type=Path,required=True,help='JSON report path; the YAML snippet is written next to it (.yaml)')
    p.add_argument('--radius',type=float,default=.8,help='Body search radius around the LiDAR, m (default 0.8)')
    p.add_argument('--voxel',type=float,default=.02,help='Persistence voxel edge, m (default 0.02)')
    p.add_argument('--persistence',type=float,default=.5,help='Fraction of windows a voxel must be hit in (default 0.5)')
    p.add_argument('--window',type=int,default=1,help='Consecutive frames per persistence window (default 1; raise for non-repetitive scans)')
    p.add_argument('--margin',type=float,default=.02,help='Safety margin added to each box, m (default 0.02)')
    p.add_argument('--lidar-height',type=float,help='LiDAR origin height above the floor, m (LiDAR level); otherwise the floor is fitted (RANSAC)')
    p.add_argument('--floor-clearance',type=float,default=.03,help='Returns less than this above the floor are floor, m (default 0.03)')
    p.add_argument('--max-floor-tilt',type=float,default=15.,help='Max floor-normal tilt from LiDAR +z for the fitted floor, degrees')
    p.add_argument('--link',type=float,default=.04,help='Voxels whose centres are within this (per axis) join one cluster, m (default 0.04)')
    p.add_argument('--min-voxels',type=int,default=4,help='Drop clusters with fewer persistent voxels (default 4)')
    p.add_argument('--robot-size',type=float,nargs=3,metavar=('LENGTH','WIDTH','HEIGHT'),help='Robot outer size, m: x, y, and z from the floor')
    p.add_argument('--lidar-offset',type=float,nargs=2,default=(0.,0.),metavar=('X','Y'),help='LiDAR x y relative to the footprint centre, m')
    p.add_argument('--skip',type=int,default=0,help='Frames to skip at the start (e.g. while walking away)')
    p.add_argument('--max-frames',type=int,help='Use at most this many frames after --skip')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    from ingestion.recorder_replayer import ReplaySource
    report,text=suggest(lambda:ReplaySource(args.recording),radius=args.radius,voxel=args.voxel,persistence=args.persistence,window=args.window,
                        margin=args.margin,lidar_height=args.lidar_height,floor_clearance=args.floor_clearance,max_floor_tilt=args.max_floor_tilt,
                        link=args.link,min_voxels=args.min_voxels,robot_size=args.robot_size,lidar_offset=args.lidar_offset,skip=args.skip,
                        max_frames=args.max_frames,source=str(args.recording))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8',newline='\n')
    if text:args.output.with_suffix('.yaml').write_text(text,encoding='utf-8',newline='\n');print(text,end='')
    removal=report['removal'] or {}
    print(json.dumps({'boxes':len(report['boxes']),'frames_used':report['frames_used'],'near_removed_fraction':removal.get('near_removed_fraction'),
                      'far_removed_fraction':removal.get('far_removed_fraction'),'warnings':report['warnings']},indent=1),file=sys.stderr)
    return 0 if text else 1


if __name__=='__main__':sys.exit(main())

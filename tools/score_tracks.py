"""Score the person tracks of a parked recording replayed offline through the real pipeline (no per-frame labels).

    python tools/score_tracks.py --recording %USERPROFILE%/aethertwin_rec/walk_line --twin %USERPROFILE%/aethertwin_twin \
        --mode walk --calibration config/calibration_extrinsics_640x400.json --line 0.5,2.0,7.5,2.0 \
        --output walk_report.json --tracks-out walk_tracks.jsonl

The recording runs through the same parked LabPipeline a parked live run uses (tools/export_showcase.py
offline_states: parked relocalization against the twin, station prior = the twin's last commissioning pose). Offline
replay processes EVERY sweep in the window; the live loop processes the latest sweep and drops older ones, so a live
run sees fewer frames when the pipeline is slower than the sensor.

Modes (the lab script says what is in the room; nothing else is ground truth):
  empty     nobody in the room: every published person or moving-cart track is a false positive (FP).
  walk      one person on the taped walk line: fraction of frames with exactly one person track, ID switches
            (distinct person ids - 1), fragments (gaps with no person track longer than --gap-s), and with --line the
            median / 95th-percentile distance of the person tracks from the taped segment (twin world metres).
  crossing  two people crossing: fraction of frames with exactly two person tracks, ID switches (distinct ids - 2),
            fragments, the maximum simultaneous person tracks.
  radial    one person on the radial line: the farthest horizontal distance from the LiDAR at which a person track
            persisted --min-frames consecutive frames, and the distance at the end of the longest continuous detection.

"Person" is SemanticClass.HUMAN_PEDESTRIAN; "dynamic" adds DYNAMIC_ROBOT_CART (core/contracts.py; the classes the
traffic heatmap counts as moving). A track's class is the per-frame argmax of its smoothed class probabilities.
--tracks-out writes one JSON line per processed frame: {"t_s", "tracks": [{id, class, confidence, x, y, z, speed}]}.
Track-level metrics without per-frame ground truth; not MOTA/HOTA.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from core.contracts import SemanticClass  # noqa: E402

PERSON_CLASSES=(SemanticClass.HUMAN_PEDESTRIAN.name,)
DYNAMIC_CLASSES=(SemanticClass.HUMAN_PEDESTRIAN.name,SemanticClass.DYNAMIC_ROBOT_CART.name)
MODES=('empty','walk','crossing','radial')
HONESTY='Track-level metrics without per-frame ground truth; not MOTA/HOTA.'
POLICY=('Offline replay processes every sweep in the window; the live loop processes only the latest sweep and drops older '
        'ones when the pipeline is slower than the sensor, so a live run can see fewer frames and longer gaps than this replay.')
COMMON_LIMITS=['No per-frame ground truth: the room content comes from the lab script and the taped geometry only.',
    'The tracker publishes a track from its first matched detection (min_hits=1) and keeps it, predicted, up to 1 s '
    'without a detection (tracking_and_navigation/kalman_9dof_tracker.py); "confirmed" means published by the pipeline.',
    'A class can change during a track: the counts use the class of each frame.']
LIMITS={'empty':['Shows phantom person/cart tracks in a room the script says is empty; it does not show missed detections.'],
    'walk':['Shows identity stability and coverage of one walker; a phantom person track elsewhere lowers the one-person fraction '
            'and adds an ID, a wrong track on the walker cannot be told apart from the walker.',
            'A gap followed by a new id counts once as a fragment and once in the distinct ids (both are reported separately).',
            'The lateral distance includes every person track sample, phantoms too; it is a distance from the tape, not a position error.'],
    'crossing':['Shows whether two simultaneous tracks exist and how many ids appeared; two tracks that swap identities while '
                'keeping both ids are invisible without ground truth.'],
    'radial':['The range is the distance of the track from the LiDAR, not a verified detection range: any person track counts, '
              'including a phantom; walk outward from the rig so the end of the longest continuous detection is where it was lost.']}


def frame_durations(times):
    """Each frame's share of the timeline: the interval to the next frame; the last frame gets the median interval."""
    t=np.asarray(times,float)
    if len(t)<2:return np.zeros(len(t))
    d=np.diff(t);return np.r_[d,np.median(d)]


def _person(track):return track['class'] in PERSON_CLASSES


def _gaps(frames,predicate=_person):
    """Gaps between consecutive frames that hold a matching track: (t_previous, t_next) pairs, t_next - t_previous the gap."""
    present=[t for t,tracks in frames if any(predicate(k) for k in tracks)]
    return list(zip(present[:-1],present[1:]))


def _identity(frames,gap_s,expected):
    ids={k['id'] for _,tracks in frames for k in tracks if _person(k)};gaps=[b-a for a,b in _gaps(frames)]
    fragments=sum(g>gap_s+1e-9 for g in gaps);switches=max(0,len(ids)-expected)
    return {'distinct_person_ids':len(ids),'id_switches':switches,'fragments':fragments,'id_switches_plus_fragments':switches+fragments,
            'longest_gap_s':max(gaps) if gaps else 0.}


def _base(frames):
    durations=frame_durations([t for t,_ in frames])
    return durations,{'frames':len(frames),'duration_s':float(durations.sum()),
        'confirmed_tracks':len({k['id'] for _,tracks in frames for k in tracks})}


def _per_min(count,duration_s):return float(count)/(duration_s/60.) if duration_s>0 else None


def score_empty(frames,origin=None):
    durations,out=_base(frames);duration=out['duration_s'];time_by_id={};frames_by_id={};xy={};frames_with_fp=0;person_ids=set()
    for (_,tracks),d in zip(frames,durations):
        dynamic=[k for k in tracks if k['class'] in DYNAMIC_CLASSES];frames_with_fp+=bool(dynamic)
        for k in dynamic:
            time_by_id[k['id']]=time_by_id.get(k['id'],0.)+float(d);frames_by_id[k['id']]=frames_by_id.get(k['id'],0)+1
            xy.setdefault(k['id'],[]).append((k['x'],k['y']))
            if _person(k):person_ids.add(k['id'])
    longest=max(time_by_id,key=lambda i:(time_by_id[i],-i)) if time_by_id else None
    listing=[]
    for i in sorted(time_by_id,key=lambda i:-time_by_id[i])[:20]:
        mean=np.mean(xy[i],axis=0);row={'id':i,'time_s':time_by_id[i],'frames':frames_by_id[i],'mean_x':float(mean[0]),'mean_y':float(mean[1])}
        if origin is not None:row['range_m']=float(np.hypot(*(mean-np.asarray(origin,float)[:2])))
        listing.append(row)
    out.update(confirmed_tracks_per_min=_per_min(out['confirmed_tracks'],duration),fp_tracks=len(time_by_id),
        fp_tracks_per_min=_per_min(len(time_by_id),duration),person_fp_tracks=len(person_ids),person_fp_tracks_per_min=_per_min(len(person_ids),duration),
        longest_fp_track_s=time_by_id[longest] if longest is not None else 0.,longest_fp_track_id=longest,
        fp_time_s_per_min=_per_min(sum(time_by_id.values()),duration),frames_with_fp=frames_with_fp,fp_tracks_longest_first=listing)
    return out


def segment_distances(points,line):
    """Distance of each (x, y) point from the segment (x1, y1)-(x2, y2)."""
    a=np.asarray(line[:2],float);b=np.asarray(line[2:4],float);v=b-a;length=float(v@v)
    if not length>1e-12:raise ValueError('--line needs two distinct end points')
    p=np.asarray(points,float).reshape(-1,2);s=np.clip((p-a)@v/length,0.,1.)
    return np.linalg.norm(p-(a+s[:,None]*v),axis=1)


def _lateral(frames,line):
    samples=[(k['x'],k['y']) for _,tracks in frames for k in tracks if _person(k)]
    if not samples:return {'lateral_samples':0,'lateral_median_m':None,'lateral_p95_m':None,'lateral_max_m':None}
    d=segment_distances(samples,line)
    return {'lateral_samples':len(d),'lateral_median_m':float(np.median(d)),'lateral_p95_m':float(np.percentile(d,95)),'lateral_max_m':float(d.max())}


def _counts(frames):return np.array([sum(_person(k) for k in tracks) for _,tracks in frames],int)


def score_walk(frames,gap_s=1.,line=None):
    _,out=_base(frames);counts=_counts(frames);n=max(len(frames),1)
    out.update(fraction_one_person=float((counts==1).sum()/n),fraction_no_person=float((counts==0).sum()/n),
        fraction_several_people=float((counts>1).sum()/n),**_identity(frames,gap_s,1))
    if line is not None:out.update(_lateral(frames,line))
    return out


def score_crossing(frames,gap_s=1.,line=None):
    _,out=_base(frames);counts=_counts(frames);n=max(len(frames),1)
    out.update(fraction_two_people=float((counts==2).sum()/n),fraction_at_least_two=float((counts>=2).sum()/n),
        max_simultaneous=int(counts.max()) if len(counts) else 0,**_identity(frames,gap_s,2))
    if line is not None:out.update(_lateral(frames,line))
    return out


def score_radial(frames,origin,min_frames=5,gap_s=1.):
    """Farthest sustained range: over every run of min_frames consecutive frames of one person track, the window's
    nearest distance; the maximum over windows. Last continuous detection: the farthest person track at the last frame
    of the longest run of person presence (any id) whose gaps do not exceed gap_s."""
    if min_frames<1:raise ValueError('--min-frames must be at least 1')
    _,out=_base(frames);origins=np.asarray(origin,float)
    origins=np.broadcast_to(origins[...,:2] if origins.ndim>1 else origins[:2],(len(frames),2))
    per_track={};presence=[]
    for i,((t,tracks),o) in enumerate(zip(frames,origins)):
        people=[(k['id'],float(math.hypot(k['x']-o[0],k['y']-o[1]))) for k in tracks if _person(k)]
        for track_id,r in people:per_track.setdefault(track_id,[]).append((i,r))
        if people:presence.append((t,max(r for _,r in people)))
    best=None
    for track_id,rows in per_track.items():
        index=np.array([i for i,_ in rows]);r=np.array([r for _,r in rows]);breaks=np.flatnonzero(np.diff(index)!=1)+1
        for run in np.split(np.arange(len(rows)),breaks):
            for start in range(len(run)-min_frames+1):
                value=float(r[run[start:start+min_frames]].min())
                if best is None or value>best[0]:best=(value,track_id,float(frames[index[run[start]]][0]))
    out.update(max_range_m=None if best is None else best[0],max_range_track_id=None if best is None else best[1],
        max_range_t_s=None if best is None else best[2],min_frames=int(min_frames))
    runs=[];current=[]
    for row in presence:
        if current and row[0]-current[-1][0]>gap_s+1e-9:runs.append(current);current=[]
        current.append(row)
    if current:runs.append(current)
    if runs:
        longest=max(runs,key=lambda run:run[-1][0]-run[0][0])  # max keeps the first of equal runs
        out.update(last_continuous_range_m=longest[-1][1],continuous_start_range_m=longest[0][1],continuous_start_t_s=longest[0][0],
                   continuous_end_t_s=longest[-1][0])
    else:out.update(last_continuous_range_m=None,continuous_start_range_m=None,continuous_start_t_s=None,continuous_end_t_s=None)
    return out


def track_rows(state):
    """The tracks.jsonl view of a snapshot's tracks (also what the scores read, so the file reproduces the report)."""
    return [{'id':int(e.track_id),'class':SemanticClass(int(e.semantic_class)).name,'confidence':round(float(e.confidence),4),
             'x':round(float(e.position[0]),4),'y':round(float(e.position[1]),4),'z':round(float(e.position[2]),4),'speed':round(float(e.speed),4)}
            for e in state.entities]


def score(frames,mode,*,origin=None,gap_s=1.,min_frames=5,line=None):
    if mode=='empty':return score_empty(frames,origin)
    if mode=='walk':return score_walk(frames,gap_s,line)
    if mode=='crossing':return score_crossing(frames,gap_s,line)
    if mode=='radial':return score_radial(frames,origin if origin is not None else (0.,0.),min_frames,gap_s)
    raise ValueError(f'Unknown mode {mode}')


def _line(text):
    try:values=[float(v) for v in text.split(',')]
    except ValueError:values=[]
    if len(values)!=4 or not all(math.isfinite(v) for v in values):raise argparse.ArgumentTypeError('--line takes x1,y1,x2,y2 in twin world metres')
    return tuple(values)


def _plain(value):
    """JSON-ready copy (frozen snapshot mappings, tuples, numpy values); non-finite floats become None."""
    if isinstance(value,dict):return {str(k):_plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,np.ndarray)):return [_plain(v) for v in (value.tolist() if isinstance(value,np.ndarray) else value)]
    if isinstance(value,np.generic):value=value.item()
    if isinstance(value,float) and not math.isfinite(value):return None
    return value if value is None or isinstance(value,(bool,int,float,str)) else str(value)


def run(a,log=print):
    from tools.export_showcase import _weakest,offline_states,short_commit
    info={};frames=[];origins=[];started=time.monotonic();sink=None
    if a.tracks_out:Path(a.tracks_out).parent.mkdir(parents=True,exist_ok=True);sink=open(a.tracks_out,'w',encoding='utf-8',newline='\n')
    try:
        for t,state in offline_states(a.recording,a.twin,a.config,seconds=a.seconds,device=a.device,calibration=a.calibration,lidar_only=a.lidar_only,
                                      lidar_height_assumed=a.lidar_height_assumed,start_s=a.start_s,checkpoint=a.checkpoint,info=info):
            rows=track_rows(state);frames.append((round(t,6),rows));origins.append(np.asarray(state.sensor_pose_world)[:2,3].copy())
            if sink is not None:sink.write(json.dumps({'t_s':round(t,6),'tracks':rows},allow_nan=False)+'\n')
            if len(frames)%50==0:log(f'  {len(frames)} frames, t={t:.1f} s, {time.monotonic()-started:.0f} s wall')
    finally:
        if sink is not None:sink.close()
    if not frames:raise ValueError('No frames inside the requested --start-s/--seconds window')
    origin=np.array(origins) if a.mode=='radial' else origins[-1]
    metrics=score(frames,a.mode,origin=origin,gap_s=a.gap_s,min_frames=a.min_frames,line=a.line)
    provenance={'recording':info['recording'],'commit':short_commit(),'calibration':_weakest(info['calibration_classes']),
        'calibration_file':'measured' if a.calibration else 'none','lidar_only':bool(a.lidar_only),'lidar_height':info['lidar_height'],
        'frames_processed':info['frames_processed'],'frames_acquired':info['frames_acquired'],'recording_frames':info['recording_frames'],
        'window':{'start_s':a.start_s,'seconds':a.seconds,'first_t_s':frames[0][0],'last_t_s':frames[-1][0]},'device':a.device,
        'config':Path(a.config).name,'synthetic':bool(info['twin_synthetic'] or info['synthetic_frames']),'twin_live_eligible':info['twin_live_eligible'],
        'lidar_xy_world':[float(v) for v in origins[-1]],'parked_localization':info['parked_localization'],'hardware_verified':False,
        'wall_s':round(time.monotonic()-started,1)}
    report={'tool':'score_tracks','version':1,'mode':a.mode,'scored':True,'honesty':HONESTY,'latest_frame_policy':POLICY,'provenance':provenance,
        'parameters':{'gap_s':a.gap_s,'min_frames':a.min_frames,'line':None if a.line is None else list(a.line)},
        'classes':{'person':list(PERSON_CLASSES),'dynamic':list(DYNAMIC_CLASSES)},'does_not_show':COMMON_LIMITS+LIMITS[a.mode],'metrics':metrics}
    report=_plain(report)
    if a.output:
        Path(a.output).parent.mkdir(parents=True,exist_ok=True)
        Path(a.output).write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8',newline='\n')
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--recording',type=Path,required=True,help='raw parked recording (tools/rig.py record)')
    p.add_argument('--twin',type=Path,required=True,help='commissioned twin directory')
    p.add_argument('--mode',choices=MODES,required=True)
    p.add_argument('--calibration',type=Path,help='measured camera calibration JSON (replaces the recorded one)')
    p.add_argument('--lidar-only',action='store_true',help='bring-up: camera not fused; accepts a LiDAR-only twin')
    p.add_argument('--lidar-height-assumed',action='store_true',help='the twin was built with an ASSUMED LiDAR height (provenance only)')
    p.add_argument('--config',type=Path,default=ROOT/'config/profiles/hotspot.yaml')
    p.add_argument('--seconds',type=float,default=60.);p.add_argument('--start-s',type=float,default=0.,help='skip (do not process) earlier sweeps')
    p.add_argument('--line',type=_line,help='taped line x1,y1,x2,y2 in twin world metres (walk, crossing)')
    p.add_argument('--gap-s',type=float,default=1.,help='a gap with no person track longer than this is a fragment')
    p.add_argument('--min-frames',type=int,default=5,help='radial: consecutive frames a track must persist')
    p.add_argument('--device',choices=['cpu','cuda'],default='cpu');p.add_argument('--checkpoint',type=Path)
    p.add_argument('--output',type=Path,help='report JSON');p.add_argument('--tracks-out',type=Path,help='per-frame tracks JSONL')
    a=p.parse_args(argv)
    if a.calibration and a.lidar_only:p.error('--calibration and --lidar-only are mutually exclusive')
    if a.line is not None and a.mode not in ('walk','crossing'):p.error('--line applies to walk and crossing')
    if not a.gap_s>0 or a.min_frames<1:p.error('--gap-s must be positive and --min-frames at least 1')
    if a.device=='cuda':
        import torch
        if not torch.cuda.is_available():p.error('CUDA requested but unavailable in this Python environment')
    report=run(a)
    m=report['metrics'];shown={k:v for k,v in m.items() if not isinstance(v,(list,dict))}
    print(f"score_tracks {a.mode} ({HONESTY}) {report['provenance']['frames_processed']} frames, calibration {report['provenance']['calibration']}"
          f"{', SYNTHETIC' if report['provenance']['synthetic'] else ''}")
    for key,value in shown.items():print(f'  {key}: {round(value,4) if isinstance(value,float) else value}')
    return report


if __name__=='__main__':main()

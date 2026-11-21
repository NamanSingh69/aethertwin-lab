"""Export a recorded session as STATIC files that the browser studio replays on a public showcase site (format v1).

There is no backend on the public site, so everything the studio would fetch from the service is precomputed here:
the real pipeline (core/pipeline.py LabPipeline) runs offline in parked mode over the recording exactly as a parked
live run does (live.parked -> parked relocalization against the twin, station prior = the twin's last commissioning
pose), every snapshot goes through the service's own SceneEncoder (command_center/protocol.py), and the packets that
/ws/v2/scene would carry are stored in gzip chunks that each start with a keyframe, so a reader can seek to any chunk.

    python tools/export_showcase.py --recording %USERPROFILE%/aethertwin_rec/wifi_stationary_60s \
        --twin %USERPROFILE%/aethertwin_demo/bench_twin_lidar_only --lidar-only --lidar-height-assumed \
        --config config/profiles/hotspot.yaml --seconds 60 --device cuda --output %USERPROFILE%/aethertwin_demo/showcase_bench

Output (<out>/): index.json (format, provenance, chunk list), bootstrap.json (static stand-in for GET /api/v2/bootstrap,
read-only, no auth), chunks/NNNN-<10 hex of sha256>.bin.gz (content-hashed for immutable caching; records: uint32 LE length N, float64 LE t_s since the replay start, N bytes of
one scene protocol v2 packet), assets/<revision>/ (export_twin_assets display tiles; their URLs are relative to manifest.json).

Scrub (security-critical): the recorded diagnostics carry the Pi's Wi-Fi address, hostname, boot_id, node_session,
device serials and local paths. Diagnostics are REPLACED by an explicit allowlist (numeric timing, rates, validity
flags, parked-localization quality) before encoding; identity strings are reduced to safe tokens and the calibration
id to its class (measured/placeholder/none). A final byte scan of every output file (chunks decompressed) fails the
export, and removes the partial output, on any IPv4 address, user-profile or /home/ path, "boot_id", "node_session",
"hostname", "raspberrypi", ws:// or /api/v2, 32+ hex digits, the Pi login (tools/rig.py), or a literal identity of this
machine or the recording's node.

Evidence: the output is labelled a recorded replay, never live; hardware_verified is always false. A recording or twin
with synthetic or unknown provenance is refused unless --allow-synthetic, which marks the export as a test export.
"""
from __future__ import annotations
import argparse
from collections.abc import Mapping
import copy
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import getpass
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

FORMAT='aethertwin-showcase';VERSION=1;LABEL='Recorded replay - not live'
REAL_NOTE='recorded real sensor data processed offline by the AetherTwin pipeline'
TEST_NOTE='TEST EXPORT: synthetic or unverified-provenance data processed offline by the AetherTwin pipeline; not real sensor data'
RECORD=struct.Struct('<Id')  # uint32 packet length, float64 t_s; then the packet


class LeakDetected(ValueError):
    """An output byte matched a network, path or identity pattern; the export is refused."""


_OCTET=rb'(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
LEAK_PATTERNS=(('IPv4 address',re.compile(rb'(?<![0-9.])'+_OCTET+rb'(?:\.'+_OCTET+rb'){3}(?![0-9])')),
    ('user-profile path',re.compile(rb'(?i)[\\/]users[\\/]')),('/home/ path',re.compile(rb'(?i)/home/')),
    ('boot_id',re.compile(rb'(?i)boot_id')),('node_session',re.compile(rb'(?i)node_session')),('hostname',re.compile(rb'(?i)hostname')),
    ('raspberrypi',re.compile(rb'(?i)raspberrypi')),('ws:// URL',re.compile(rb'(?i)wss?://')),('/api/v2 endpoint',re.compile(rb'(?i)/api/v2')),
    ('32+ hex digits',re.compile(rb'[0-9a-fA-F]{32,}')))
# Values under these keys (recording manifest, capture report, first frame) are forbidden literals in the output too.
IDENTITY_KEYS={'host','hostname','node_session','boot_id','serial','lidar_ip','capture_host','capture_ip','device_id','machine_id'}


def find_leaks(data,literals=()):
    """Names of the leak patterns found in bytes (empty when clean)."""
    found=[name for name,pattern in LEAK_PATTERNS if pattern.search(data)]
    found.extend(f'identity literal #{i}' for i,value in enumerate(literals) if re.search(re.escape(value.encode()),data,re.IGNORECASE))
    return found


def machine_literals():
    """This machine's user, profile folder and host names (only distinctive ones, 4+ characters)."""
    values=set()
    for get in (getpass.getuser,socket.gethostname,lambda:Path.home().name,lambda:os.environ.get('COMPUTERNAME',''),lambda:os.environ.get('USERNAME','')):
        try:values.add(str(get()).strip())
        except Exception:pass
    return {value for value in values if len(value)>=4}|rig_literals()


def rig_literals():
    """The Pi login and host the rig tooling knows (tools/rig.py USER, .local/rig.json "pi"), read as text, not imported."""
    values=set()
    try:
        match=re.search(r"(?:^|;)USER='([^']+)'",(ROOT/'tools/rig.py').read_text(encoding='utf-8'),re.MULTILINE)
        if match:values.add(match.group(1))
    except OSError:pass
    try:
        pi=json.loads((ROOT/'.local/rig.json').read_text(encoding='utf-8')).get('pi')
        if isinstance(pi,str):values.update(part for part in pi.split('@') if part)
    except (OSError,ValueError,AttributeError):pass
    return {value for value in values if len(value)>=4}


def identity_literals(value,depth=0):
    """String values under identity keys anywhere in a JSON-like tree (4+ characters)."""
    out=set()
    if depth>12:return out
    if isinstance(value,Mapping):
        for key,item in value.items():
            if str(key).lower() in IDENTITY_KEYS and isinstance(item,str) and len(item.strip())>=4:out.add(item.strip())
            else:out|=identity_literals(item,depth+1)
    elif isinstance(value,(list,tuple)):
        for item in value:out|=identity_literals(item,depth+1)
    return out


def scan_for_leaks(root,extra_literals=()):
    """Byte-scan every file under root (gzip chunks decompressed); raise LeakDetected on any match."""
    root=Path(root);literals=sorted({str(v) for v in extra_literals if v and len(str(v))>=4}|machine_literals());problems=[]
    for path in sorted(p for p in root.rglob('*') if p.is_file()):
        data=path.read_bytes()
        if path.suffix=='.gz':
            try:data=gzip.decompress(data)
            except (OSError,EOFError) as exc:problems.append(f'{path.relative_to(root).as_posix()}: unreadable gzip ({exc})');continue
        for name in find_leaks(data,literals):problems.append(f'{path.relative_to(root).as_posix()}: {name}')
    if problems:raise LeakDetected('Showcase export refused, output would leak: '+'; '.join(problems[:12])+(' ...' if len(problems)>12 else ''))


# ---- diagnostics allowlist -------------------------------------------------------------------------------------------
KEY=re.compile(r'[a-z][a-z0-9_]{0,63}\Z');WORD=re.compile(r'[a-z][a-z0-9_]{0,47}\Z')
PARKED_REASONS={'insufficient overlap with the twin','correction exceeds the parked trust region (1 m, 20 deg)'}


def _num(value):
    if isinstance(value,(bool,np.bool_)) or not isinstance(value,(int,float,np.integer,np.floating)):return None
    value=value.item() if isinstance(value,np.generic) else value
    return value if math.isfinite(value) else None


def _flag(value):return bool(value) if isinstance(value,(bool,np.bool_)) else None


def _word(value):return value if isinstance(value,str) and WORD.match(value) else None


def _nums(value,limit=64):
    if isinstance(value,(str,bytes,Mapping)) or not hasattr(value,'__iter__'):return None
    items=[_num(v) for v in list(value)[:limit+1]]
    return None if len(items)>limit or any(v is None for v in items) else [float(v) for v in items]


def _num_map(value):
    if not isinstance(value,Mapping):return None
    return {k:v for k,v in ((k,_num(v)) for k,v in value.items()) if isinstance(k,str) and KEY.match(k) and v is not None}


def _shape(value):
    if value is None:return None
    if isinstance(value,(str,bytes,Mapping)) or not hasattr(value,'__iter__'):return None
    items=list(value)[:9]
    return [int(v) for v in items] if items and all(isinstance(v,(int,np.integer)) and not isinstance(v,bool) and 0<=v<2**40 for v in items) else None


def _shape_map(value):
    if not isinstance(value,Mapping):return None
    out={}
    for stage,shapes in value.items():
        if isinstance(stage,str) and KEY.match(stage) and isinstance(shapes,Mapping):
            out[stage]={k:_shape(v) for k,v in shapes.items() if isinstance(k,str) and KEY.match(k)}
    return out


def _fields(spec):
    def clean(value):
        if not isinstance(value,Mapping):return None
        return {key:item for key,item in ((key,kind(value[key])) for key,kind in spec.items() if key in value) if item is not None}
    return clean


ALLOWED_DIAGNOSTICS={
    'processing_ms':_num,'module_ms':_num_map,'end_to_end_fps':_num,'memory_mb':_num,'queue_depth':_num,'dropped_snapshots':_num,
    'occupancy_cells':_num,'active_voxels':_num,'painted_fraction':_num,'observation_counts':_num_map,'robot_position':lambda v:_nums(v,3),
    'navigation_observations_valid':_flag,'checkpoint_synthetic_only':_flag,'planning_requested':_flag,
    'hardware_validated':lambda v:False,'trajectory_status':_word,'painting':_word,'deskew_reseeded':_word,'module_shapes':_shape_map,
    'traffic_grid':_fields({'bounds_xy':lambda v:_nums(v,4),'resolution_m':_num,'decay_seconds':_num}),
    'halo_options':_fields({'brightness':_num,'stale_after_ms':_num,'watchdog_ms':_num}),
    'parked_localization':_fields({'accepted':_flag,'attempts':_num,'sweeps':_num,'fitness':_num,'rmse_m':_num,'information_rank':_num,
                                   'correction_m':_num,'correction_deg':_num,'reason':lambda v:v if v in PARKED_REASONS else None}),
}


def sanitize_diagnostics(diagnostics,frame_index=None):
    """A new diagnostics dict holding only allowlisted, type-checked values (everything else is dropped)."""
    out={}
    for key,clean in ALLOWED_DIAGNOSTICS.items():
        if key in diagnostics:
            value=clean(diagnostics[key])
            if value is not None:out[key]=value
    out['source_mode']='replay';out['presentation_prediction_horizon_seconds']=.25
    out['playback']={'paused':False,'frame_index':frame_index,'frame_count':None,'seeking':False,'target_frame':None}
    return out


def _token(value,default):
    ok=isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',value) and not find_leaks(value.encode())
    return value if ok else default


def calibration_class(calibration):
    return 'none' if calibration is None else ('measured' if calibration.measured else 'placeholder')


def scrub_state(state,frame_index,calibration):
    """The snapshot the encoder sees: allowlisted diagnostics, safe identity tokens, the calibration class, replay mode."""
    return replace(state,diagnostics=sanitize_diagnostics(state.diagnostics,frame_index),calibration_id=calibration,
        lab_id=_token(state.lab_id,'lab'),rig_id=_token(state.rig_id,'rig'),map_revision=_token(state.map_revision,'unversioned'),
        source_mode='replay',active_alerts=tuple(a for a in state.active_alerts if isinstance(a,str) and not find_leaks(a.encode())))


# ---- provenance ------------------------------------------------------------------------------------------------------
def recorded_at(recording):
    """Start of the capture (capture_report.json date_utc minus elapsed_s); else the manifest's modification time."""
    try:
        report=json.loads((recording/'capture_report.json').read_text(encoding='utf-8'))
        moment=datetime.fromisoformat(report['date_utc'])-timedelta(seconds=float(report.get('elapsed_s') or 0))
        if moment.tzinfo is None:moment=moment.replace(tzinfo=timezone.utc)
    except (OSError,KeyError,TypeError,ValueError):moment=datetime.fromtimestamp((recording/'manifest.json').stat().st_mtime,timezone.utc)
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def short_commit():
    try:value=subprocess.run(['git','rev-parse','--short','HEAD'],capture_output=True,text=True,cwd=ROOT,timeout=10).stdout.strip()
    except (OSError,subprocess.SubprocessError):value=''
    return value if re.fullmatch(r'[0-9a-f]{4,20}',value) else 'unknown'


def _weakest(classes):
    for name in ('placeholder','none','measured'):
        if name in classes:return name
    return 'none'


def _json_bytes(value):return (json.dumps(value,indent=2,allow_nan=False)+'\n').encode('utf-8')


# ---- parked offline replay (shared with tools/score_tracks.py) --------------------------------------------------------
def replay_config(config,device,lidar_only):
    """The configuration a parked live run uses, for an offline replay: (cfg, mobile)."""
    from core.configuration import load_config
    from command_center.runtime import live_motion
    cfg=copy.deepcopy(load_config(config));cfg['input_mode']='replay';cfg['device']=device
    live=cfg.setdefault('live',{});live['parked']=True
    if lidar_only:live['capture_bringup']=True
    mobile=live_motion(cfg)  # parked: localize once against the twin, then hold the station every sweep
    cfg.setdefault('simulation',{})['mobile']=mobile
    # As a live run: no simulated agent route and only configured geofences (the defaults describe the simulator's lab).
    cfg['planning']['start']=None;cfg['planning']['goal']=None;cfg['geofences']=cfg.get('geofences',[])
    cfg['mapping']['runtime_cache_root']=None
    return cfg,mobile


def parked_pipeline(cfg,twin_path,twin_obj,static,grid,mobile,checkpoint):
    """The LabPipeline of a parked live run over a loaded twin: (pipeline, station prior or None, map revision)."""
    from core.pipeline import LabPipeline
    from command_center.assets import file_revision
    revision=file_revision(twin_path/('bundle_manifest.json' if (twin_path/'bundle_manifest.json').is_file() else 'measured_map.npz'))
    cfg.setdefault('identity',{})['map_revision']=revision
    cfg['planning']['bounds']=[grid.origin[0],grid.upper[0],grid.origin[1],grid.upper[1],grid.origin[2],grid.upper[2]]
    # As the live path: the station prior is live.initial_pose_world, else the twin's last commissioning pose.
    prior=cfg['live'].get('initial_pose_world');poses=getattr(twin_obj,'poses',None)
    if prior is None and poses is not None and len(poses):prior=poses[-1]
    station=None if prior is None else np.asarray(prior,float)
    return LabPipeline(cfg,checkpoint,twin_obj,static,grid,mobile),station,revision


def replay_frame(raw,station,lidar_only,measured):
    """A recorded sweep as the parked live loop hands it to the pipeline (station pose, calibration choice, replay mode)."""
    frame=raw if station is None else replace(raw,sensor_pose_world=station)
    if lidar_only:frame=replace(frame,calibration=None)  # bring-up: camera not fused
    elif measured is not None:frame=replace(frame,calibration=measured)
    return replace(frame,diagnostics={**frame.diagnostics,'source_mode':'replay'})


def _measured_calibration(calibration):
    if calibration is None:return None
    from ingestion.live_source import load_measured_calibration
    return load_measured_calibration({'calibration':str(Path(calibration).resolve())})[0]


def _close_assets(pipeline,static,twin_obj):
    if pipeline is not None:pipeline.close()
    else:
        for resource in (static,twin_obj):
            try:
                if hasattr(resource,'close'):resource.close()
            except Exception:pass


def offline_states(recording,twin,config,*,seconds,device='cpu',calibration=None,lidar_only=False,lidar_height_assumed=False,start_s=0.,
                   checkpoint=None,info=None):
    """Yield (t_s, state) for every sweep of a parked recording in [start_s, start_s+seconds) (t_s since the first sweep),
    processed by the real parked LabPipeline as export_showcase does. Every sweep is processed (the live loop drops older
    ones). Sweeps before start_s are not processed: the pipeline starts there. `info` (a dict) receives provenance."""
    from core.pipeline import load_commissioned_assets,read_commissioning_provenance
    from command_center.runtime import live_twin_acceptable
    from ingestion.recorder_replayer import ReplaySource
    if not seconds>0 or not start_s>=0:raise ValueError('--seconds must be positive and --start-s nonnegative')
    if calibration is not None and lidar_only:raise ValueError('--calibration and --lidar-only are mutually exclusive')
    recording=Path(recording).resolve();twin_path=Path(twin).resolve()
    cfg,mobile=replay_config(config,device,lidar_only)
    provenance=read_commissioning_provenance(twin_path);measured=_measured_calibration(calibration)
    checkpoint=Path(checkpoint).resolve() if checkpoint else ROOT/cfg['perception']['checkpoint']
    if not checkpoint.is_file():raise FileNotFoundError(f'Trained checkpoint missing: {checkpoint}')
    info=info if info is not None else {}
    info.update(recording=re.sub(r'[^A-Za-z0-9_.-]','_',recording.name)[:64] or 'recording',twin_live_eligible=bool(live_twin_acceptable(provenance,cfg)),
        twin_synthetic=provenance.get('any_synthetic') is not False,lidar_height='assumed' if lidar_height_assumed else 'measured',
        calibration_classes=set(),synthetic_frames=0,frames_acquired=0,frames_processed=0,recording_frames=None,station=None,parked_localization=None)
    source=ReplaySource(recording);twin_obj=static=pipeline=None
    try:
        info['recording_frames']=len(source)
        if source.manifest.get('metadata',{}).get('motion_mode')=='mobile':raise ValueError('Offline parked replay needs a parked (stationary) recording')
        twin_obj,static,grid=load_commissioned_assets(twin_path)
        pipeline,station,info['map_revision']=parked_pipeline(cfg,twin_path,twin_obj,static,grid,mobile,checkpoint)
        info['station']=None if station is None else station.tolist();t0=None
        for raw in source:
            aligned=float(raw.timestamp+raw.sweep_duration)
            if t0 is None:t0=aligned
            if aligned-t0<start_s:continue
            if aligned-t0>=start_s+seconds:break
            info['frames_acquired']+=1;info['synthetic_frames']+=bool(raw.diagnostics.get('synthetic'))
            frame=replay_frame(raw,station,lidar_only,measured);info['calibration_classes'].add(calibration_class(frame.calibration))
            state=pipeline.process(frame);info['frames_processed']+=1
            parked=state.diagnostics.get('parked_localization');info['parked_localization']=None if parked is None else dict(parked)
            yield float(state.timestamp-t0),state
    finally:
        try:source.close()
        finally:_close_assets(pipeline,static,twin_obj)


# ---- export ----------------------------------------------------------------------------------------------------------
def export_showcase(recording,twin,output,*,config,seconds=60.,chunk_seconds=4.,device='cpu',calibration=None,lidar_only=False,
                    lidar_height_assumed=False,allow_synthetic=False,checkpoint=None,log=print):
    from core.pipeline import load_commissioned_assets,read_commissioning_provenance
    from command_center.assets import export_twin_assets
    from command_center.controls import ACTIONS
    from command_center.protocol import SceneEncoder
    from command_center.runtime import live_twin_acceptable
    from ingestion.recorder_replayer import ReplaySource
    if not seconds>0 or not chunk_seconds>0:raise ValueError('--seconds and --chunk-seconds must be positive')
    if calibration is not None and lidar_only:raise ValueError('--calibration and --lidar-only are mutually exclusive')
    recording=Path(recording).resolve();twin_path=Path(twin).resolve();output=Path(output).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):raise FileExistsError(f'Output must be a new or empty directory: {output}')
    cfg,mobile=replay_config(config,device,lidar_only)
    source=ReplaySource(recording);manifest=source.manifest
    if manifest.get('metadata',{}).get('motion_mode')=='mobile':raise ValueError('Showcase v1 replays parked (stationary) recordings only')
    provenance=read_commissioning_provenance(twin_path)
    real=bool(live_twin_acceptable(provenance,cfg) and provenance.get('any_synthetic') is False)
    if not real and not allow_synthetic:
        raise ValueError('Twin provenance is synthetic/unknown or not eligible for this mode (a LiDAR-only twin needs --lidar-only); '
                         'use --allow-synthetic only for a labelled test export')
    measured=_measured_calibration(calibration)
    checkpoint=Path(checkpoint).resolve() if checkpoint else ROOT/cfg['perception']['checkpoint']
    if not checkpoint.is_file():raise FileNotFoundError(f'Trained checkpoint missing: {checkpoint}')
    literals=machine_literals()|identity_literals(manifest.get('metadata',{}))
    try:literals|=identity_literals(json.loads((recording/'capture_report.json').read_text(encoding='utf-8')))
    except (OSError,ValueError):pass
    staging=output.parent/f'.{output.name}.partial-{secrets.token_hex(4)}';staging.mkdir(parents=True)
    twin_obj=static=pipeline=None;started=time.monotonic()
    try:
        twin_obj,static,grid=load_commissioned_assets(twin_path)
        pipeline,station,revision=parked_pipeline(cfg,twin_path,twin_obj,static,grid,mobile,checkpoint)
        prefix=f'assets/{revision}/'
        # Tile URLs inside manifest.json are relative to the manifest itself (standard URL resolution); the
        # index's assets_prefix locates the manifest from the showcase root.
        export_twin_assets(twin_obj,staging/'assets'/revision,revision,url_prefix='')
        (staging/'chunks').mkdir()
        chunks=[];pending=[];window=None;encoder=None;t0=None;frames=0;classes=set();state=None;synthetic=False
        def flush():
            if not pending:return
            data=gzip.compress(b''.join(RECORD.pack(len(packet),t)+packet for t,packet in pending),compresslevel=9,mtime=0)
            # Content-hashed name: the site caches /showcase/* as immutable; only index.json (no-cache) names the chunks.
            name=f'chunks/{len(chunks):04d}-{hashlib.sha256(data).hexdigest()[:10]}.bin.gz'
            (staging/name).write_bytes(data)
            chunks.append({'file':name,'first_frame':frames-len(pending),'frames':len(pending),'start_s':pending[0][0],'end_s':pending[-1][0],'bytes':len(data)})
            pending.clear()
        for raw in source:
            aligned=float(raw.timestamp+raw.sweep_duration)
            if t0 is None:t0=aligned;literals|=identity_literals(raw.diagnostics)
            if aligned-t0>=seconds:break
            if raw.diagnostics.get('synthetic'):
                synthetic=True
                if not allow_synthetic:raise ValueError('Recording contains synthetic frames; use --allow-synthetic for a labelled test export')
            frame=replay_frame(raw,station,lidar_only,measured)
            kind=calibration_class(frame.calibration);classes.add(kind)
            state=scrub_state(pipeline.process(frame),frames,kind)
            t=float(state.timestamp-t0);current=int(math.floor(t/chunk_seconds+1e-9))
            if current!=window:
                flush();window=current
                # A fresh encoder per chunk: its first packet is a keyframe; the stream id is a plain counter, not a uuid.
                encoder=SceneEncoder(cfg['mapping']['voxel_size']);encoder.stream_id=f'showcase-{len(chunks):04d}'
            pending.append((t,encoder.encode(state).incremental));frames+=1
            if frames%50==0:log(f'  {frames} frames, t={t:.1f} s, {time.monotonic()-started:.0f} s wall')
        flush()
        if not frames:raise ValueError('No frames inside the requested --seconds window')
        test_export=synthetic or not real
        source_info={'recording':re.sub(r'[^A-Za-z0-9_.-]','_',recording.name)[:64] or 'recording','commit':short_commit(),
            'calibration':_weakest(classes),'lidar_height':'assumed' if lidar_height_assumed else 'measured','hardware_verified':False,
            'note':TEST_NOTE if test_export else REAL_NOTE}
        duration=chunks[-1]['end_s']
        index={'format':FORMAT,'version':VERSION,'label':LABEL,'recorded_at':recorded_at(recording),'duration_s':duration,'frame_count':frames,
               'source':source_info,'bootstrap':'bootstrap.json','assets_prefix':prefix,'chunks':chunks}
        scene_identity={'lab_id':state.lab_id,'rig_id':state.rig_id,'map_revision':state.map_revision,'calibration_id':state.calibration_id}
        bootstrap={'protocol_version':2,'label':LABEL,'identity':scene_identity,'source_mode':'replay','hardware_status':'not_tested',
            'hardware_verified':False,'map_revision':revision,'map_manifest_url':prefix+'manifest.json','appearance_url':None,'stream_url':None,
            'showcase_index':'index.json','role':'viewer','auth':'unavailable','controls':'read_only','control_revision':0,
            'capabilities':{action:False for action in sorted(ACTIONS)},'latest':None,'paused':False,'completed':False,'error':None,
            'source_active':True,'recording_id':'showcase',
            'available_sources':[{'id':'showcase','source':'replay','recording_id':'showcase','label':LABEL}],
            'replay':{'frame_count':frames,'current_frame':0,'duration':duration},'session_id':'showcase','halo':None,'source':source_info}
        (staging/'index.json').write_bytes(_json_bytes(index));(staging/'bootstrap.json').write_bytes(_json_bytes(bootstrap))
        scan_for_leaks(staging,literals)
        if output.exists():output.rmdir()  # verified empty above
        os.replace(staging,output)
    except BaseException:
        shutil.rmtree(staging,ignore_errors=True);raise
    finally:
        try:source.close()
        finally:_close_assets(pipeline,static,twin_obj)
    total=sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
    parked=state.diagnostics.get('parked_localization')
    return {'output':str(output),'frame_count':frames,'duration_s':duration,'chunks':[(c['file'],c['bytes']) for c in chunks],
            'chunk_bytes':sum(c['bytes'] for c in chunks),'asset_bytes':sum(p.stat().st_size for p in (output/'assets').rglob('*') if p.is_file()),
            'total_bytes':total,'wall_s':time.monotonic()-started,'calibration':source_info['calibration'],'test_export':test_export,
            'parked_localization':None if parked is None else dict(parked),'localization_valid_at_end':bool(state.validity.get('localization'))}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--recording',type=Path,required=True,help='raw recording directory (manifest.json + chunk_*.npz)')
    p.add_argument('--twin',type=Path,required=True,help='commissioned twin directory')
    p.add_argument('--calibration',type=Path,help='measured camera calibration JSON (replaces the recorded one)')
    p.add_argument('--lidar-only',action='store_true',help='bring-up: no camera calibration, camera not fused; accepts a LiDAR-only twin')
    p.add_argument('--config',type=Path,default=ROOT/'config/profiles/hotspot.yaml')
    p.add_argument('--seconds',type=float,default=60.);p.add_argument('--chunk-seconds',type=float,default=4.)
    p.add_argument('--output',type=Path,required=True,help='new or empty output directory')
    p.add_argument('--device',choices=['cpu','cuda'],default='cpu');p.add_argument('--checkpoint',type=Path)
    p.add_argument('--lidar-height-assumed',action='store_true',help='the twin was built with an ASSUMED (not tape-measured) LiDAR height')
    p.add_argument('--allow-synthetic',action='store_true',help='accept synthetic/unknown provenance; the export is labelled a test export')
    a=p.parse_args(argv)
    if a.device=='cuda':
        import torch
        if not torch.cuda.is_available():p.error('CUDA requested but unavailable in this Python environment')
    summary=export_showcase(a.recording,a.twin,a.output,config=a.config,seconds=a.seconds,chunk_seconds=a.chunk_seconds,device=a.device,
        calibration=a.calibration,lidar_only=a.lidar_only,lidar_height_assumed=a.lidar_height_assumed,allow_synthetic=a.allow_synthetic,
        checkpoint=a.checkpoint)
    print(f"Showcase export ({LABEL}{'; TEST EXPORT' if summary['test_export'] else ''}): {summary['frame_count']} frames, "
          f"{summary['duration_s']:.2f} s, calibration {summary['calibration']}, {summary['wall_s']:.1f} s wall")
    for name,size in summary['chunks']:print(f'  {name}  {size:>10,d} bytes')
    print(f"  chunks {summary['chunk_bytes']:,d} bytes, assets {summary['asset_bytes']:,d} bytes, total {summary['total_bytes']:,d} bytes")
    print(f"  parked localization: {summary['parked_localization']}; localization valid at end: {summary['localization_valid_at_end']}")
    print(f"  output: {summary['output']} (leak scan passed)")
    return summary


if __name__=='__main__':main()

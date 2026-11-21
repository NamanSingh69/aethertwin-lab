"""One command from a bring-up recording of screen-checkerboard holds to an accepted camera-LiDAR calibration file.

Real session (laptop, after `tools/record_raw.py --config config/profiles/hotspot.yaml --bringup --duration 300
--output D:/aethertwin/rec/calib_targets` while the monitor is moved through 12-20 holds of ~3 s each):

    .venv\\Scripts\\python.exe tools/calibrate_rig.py D:/aethertwin/rec/calib_targets --cols 7 --rows 5 --square 0.04062
        --board-size 0.543 0.324 --initial-guess 0.15,0.0,-0.05 --output config/calibration_extrinsics_640x400.json

--square and --board-size are what tools/screen_checkerboard.html prints (square width, and the
monitor's whole front face, in metres; the pattern is centred on that face). --initial-guess is the camera's
optical centre in the LiDAR frame (x forward, y left, z up, metres) from a tape measure, or a JSON file with
T_lidar_camera; it only finds and labels the board in the cloud and never enters the corner values.

Steps:
 1. find the still holds in the recording once (tools/extract_calibration_targets.py detection);
 2. pass 1: board corners searched around the tape guess with the extractor's wide margins; solve
    (ingestion.calibration.calibrate_camera_lidar: RANSAC PnP, LM refinement, six-DoF observability gate);
 3. pass 2: the same holds searched again around the pass-1 result with tight margins, which keeps clutter next
    to the board (floor, stand, desk, people) out; the held-out holds stay the same; solve again;
 4. acceptance, all of which must pass or nothing is written (exit 2): enough accepted and held-out holds,
    six observed degrees of freedom within the conditioning limit, training RMSE and RANSAC inlier share,
    held-out corner error (median and max, pixels), a result within a gross-error bound of the tape guess,
    and passes 1 and 2 agreeing;
 5. write the solver's calibration JSON (K and distortion = the recorded factory intrinsics, T_lidar_camera,
    image_size, measured, calibration_id, covariance) with a `rig_calibration` provenance block, in the format
    `ingestion.live_source.load_measured_calibration` reads. An existing file is replaced only with --force,
    and its bytes are archived first in <name>.history/<sha256>.json.
`measured: true` needs the recording's capture_report.json to name real devices via the Pi capture node; a
synthetic or unlabelled recording is refused, or with --rehearsal written as `measured: false` (live mode rejects
it). The pass reports, NPZ files and acceptance.json go to --workdir (default <recording>_calibration/), also on
failure. The printed next steps copy the file to the Pi and restart the node without --bringup.
"""
from __future__ import annotations
import argparse,copy,hashlib,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools import extract_calibration_targets as targets  # noqa: E402

REAL_LABEL='real devices via the Pi capture node'


def recording_provenance(recording):
    """(is_real_rig_recording, summary) from the recording's capture_report.json (written by tools/record_raw.py)."""
    path=Path(recording)/'capture_report.json'
    if not path.is_file():return False,{'capture_report':None,'why':'no capture_report.json: cannot show the frames came from the rig'}
    report=json.loads(path.read_text(encoding='utf-8'));devices=((report.get('node_identity') or {}).get('devices') or {})
    real=report.get('label')==REAL_LABEL and not devices.get('synthetic') and not report.get('error')
    return real,{'capture_report':str(path),'label':report.get('label'),'date_utc':report.get('date_utc'),'frames':report.get('frames'),
                 'bringup_uncalibrated':report.get('bringup_uncalibrated'),'error':report.get('error'),
                 'node_devices':{k:devices.get(k) for k in ('synthetic','bringup','lidar','camera') if k in devices},
                 'why':None if real else 'capture report does not name real devices via the Pi capture node'}


def rotation_deg(R):return float(np.degrees(np.arccos(np.clip((np.trace(R)-1)/2,-1,1))))


def holdout(T_lidar_camera,arrays):
    """Held-out corner reprojection with the fitted transform (pixels, and metres at each corner's depth)."""
    import cv2
    if 'validation_lidar_points' not in arrays:return None
    T=np.linalg.inv(T_lidar_camera);camera=arrays['validation_lidar_points']@T[:3,:3].T+T[:3,3]
    projected,_=cv2.projectPoints(camera,np.zeros(3),np.zeros(3),arrays['K'],arrays['distortion'])
    error=np.linalg.norm(projected.reshape(-1,2)-arrays['validation_image_points'],axis=1);metres=error*camera[:,2]/arrays['K'][0,0]
    return {'corners':int(len(error)),'median_px':float(np.median(error)),'rmse_px':float(np.sqrt(np.mean(error**2))),'max_px':float(np.max(error)),
            'median_m_at_depth':float(np.median(metres)),'max_m_at_depth':float(np.max(metres))}


def solve(arrays,measured):
    from ingestion.calibration import calibrate_camera_lidar,CalibrationDegeneracyError
    try:
        calibration,report=calibrate_camera_lidar(arrays['lidar_points'],arrays['image_points'],arrays['K'],arrays['distortion'],None,
                                                  image_size=tuple(int(v) for v in arrays['image_size']),measured=measured)
    except CalibrationDegeneracyError as exc:return None,{'error':str(exc),'diagnostics':exc.diagnostics}
    report['holdout']=holdout(calibration.T_lidar_camera,arrays);return calibration,report


def run_pass(name,holds,detected,args,guess,workdir,measured,validation_holds=None):
    K,dist,image_size,source=detected
    arrays,summary=targets.build_targets(holds,K,dist,image_size,args,guess,validation_holds=validation_holds,intrinsics_source=source,recording=args.recording)
    (workdir/f'{name}_targets.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    result={'name':name,'summary':summary,'arrays':arrays,'calibration':None,'report':None}
    if arrays is None:result['error']=summary['error'];return result
    np.savez(workdir/f'{name}_targets.npz',**arrays)
    result['calibration'],result['report']=solve(arrays,measured)
    if result['calibration'] is None:result['error']=result['report']['error']
    return result


def pass2_args(args):
    tight=copy.copy(args);tight.plane_margin=args.pass2_plane_margin;tight.region_margin=args.pass2_region_margin
    tight.max_inplane_deg=args.pass2_max_inplane_deg;return tight


def evaluate(final,first,guess,args):
    """Acceptance checks for the final pass: a list of {check, value, limit, passed}."""
    checks=[];add=lambda name,value,limit,passed:checks.append({'check':name,'value':value,'limit':limit,'passed':bool(passed)})
    summary=final['summary'];add('accepted holds',summary['holds_accepted'],f">= {args.min_holds}",summary['holds_accepted']>=args.min_holds)
    add('held-out holds',len(summary['validation_holds']),f'>= {args.min_validation_holds}',len(summary['validation_holds'])>=args.min_validation_holds)
    report=final['report']
    if final['calibration'] is None:
        add('solver (six-DoF observability gate)',final.get('error'),'solved and fully observable',False);return checks
    observability=report['observability']
    add('six-DoF observability',{'rank':observability['scaled_jacobian_rank'],'scaled_condition_number':observability['scaled_jacobian_condition_number']},
        f"rank 6, condition <= {observability['max_scaled_condition_number']:g}",True)  # the solver refuses otherwise
    add('training RMSE px',report['reprojection_point_rmse_pixels'],f'<= {args.max_train_rmse_px}',report['reprojection_point_rmse_pixels']<=args.max_train_rmse_px)
    share=report['inliers']/report['unique_observations'];add('RANSAC inlier share',share,f'>= {args.min_inlier_share}',share>=args.min_inlier_share)
    held=report['holdout']
    if held is None:add('held-out corners',None,'present',False)
    else:
        add('held-out median px',held['median_px'],f'<= {args.max_holdout_median_px}',held['median_px']<=args.max_holdout_median_px)
        add('held-out max px',held['max_px'],f'<= {args.max_holdout_px}',held['max_px']<=args.max_holdout_px)
    T=final['calibration'].T_lidar_camera;shift=float(np.linalg.norm(T[:3,3]-guess[:3,3]));turn=rotation_deg(T[:3,:3]@guess[:3,:3].T)
    add('distance from the tape guess m',shift,f'<= {args.max_guess_deviation_m}',shift<=args.max_guess_deviation_m)
    add('rotation from the nominal axes deg',turn,f'<= {args.max_guess_deviation_deg}',turn<=args.max_guess_deviation_deg)
    if first is not final and first['calibration'] is not None:
        P=first['calibration'].T_lidar_camera;move=float(np.linalg.norm(T[:3,3]-P[:3,3]));spin=rotation_deg(T[:3,:3]@P[:3,:3].T)
        add('pass 1 to pass 2 change mm',1000*move,f'<= {args.max_pass_change_mm}',1000*move<=args.max_pass_change_mm)
        add('pass 1 to pass 2 change deg',spin,f'<= {args.max_pass_change_deg}',spin<=args.max_pass_change_deg)
    return checks


def git_commit():
    try:return subprocess.run(['git','-C',str(ROOT),'rev-parse','HEAD'],capture_output=True,text=True,timeout=10).stdout.strip() or None
    except (OSError,subprocess.SubprocessError):return None


def manifest_sha256(recording):
    path=Path(recording)/'manifest.json'
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def calibration_document(final,first,checks,provenance,args,measured):
    report=dict(final['report']);summary=final['summary']
    def brief(result):
        if result['calibration'] is None:return {'pass':result['name'],'error':result.get('error')}
        r=result['report'];return {'pass':result['name'],'holds_accepted':result['summary']['holds_accepted'],'T_lidar_camera':r['T_lidar_camera'],
            'training_rmse_px':r['reprojection_point_rmse_pixels'],'inliers':r['inliers'],'observations':r['observations'],'holdout':r['holdout']}
    report['rig_calibration']={'schema_version':1,'tool':'tools/calibrate_rig.py','created_utc':datetime.now(timezone.utc).isoformat(),
        'git_commit':git_commit(),'evidence':'measured on the rig from the named recording' if measured else 'REHEARSAL: not a rig measurement; live mode rejects it',
        'recording':str(args.recording),'recording_manifest_sha256':manifest_sha256(args.recording),'recording_provenance':provenance,
        'target':{'kind':'screen checkerboard (tools/screen_checkerboard.html), pattern centred on the monitor face',
                  'inner_corners':[args.cols,args.rows],'square_m':args.square,'board_size_m':list(args.board_size)},
        'intrinsics_source':summary['intrinsics_source'],'initial_guess':args.initial_guess,'initial_guess_used_for':summary['initial_guess_used_for'],
        'holds':{'found':summary['holds_found'],'accepted':summary['holds_accepted'],'training':summary['training_holds'],'validation':summary['validation_holds'],
                 'per_hold':[{k:h.get(k) for k in ('hold','first_frame','frames','camera_distance_m','pnp_rms_px','plane_inliers','plane_rms_m','extent_ratio',
                              'edges_used','accepted','reason')} for h in summary['holds']]},
        'holdout':report['holdout'],'passes':[brief(first)]+([brief(final)] if final is not first else []),
        'acceptance':{'passed':all(c['passed'] for c in checks),'checks':checks},
        'covariance_note':'covariance_se3 assumes independent pixel errors; board-edge errors in the LiDAR are correlated, so the held-out error is the better guide'}
    return report


def install(document,output,force):
    """Write `document` at `output`; an existing file needs force and is archived first (as calibrate_camera_lidar does)."""
    from ingestion.calibration import _publish
    output=Path(output)
    if output.exists() and not force:raise FileExistsError(f'{output} exists; pass --force to replace it (the old file is archived in {output.stem}.history/)')
    _publish(document,output);return document['publication']


def parser():
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0],formatter_class=argparse.RawDescriptionHelpFormatter,epilog=__doc__.split('\n',1)[1])
    p.add_argument('recording',type=Path);p.add_argument('--output',type=Path,required=True,help='Calibration JSON to write, e.g. config/calibration_extrinsics_640x400.json')
    targets.add_target_arguments(p)
    p.add_argument('--workdir',type=Path,help='Pass reports and NPZ files (default: <recording>_calibration next to the recording)')
    p.add_argument('--force',action='store_true',help='Replace an existing output (its bytes are archived first)')
    p.add_argument('--rehearsal',action='store_true',help='Accept a synthetic or unlabelled recording and write measured: false')
    p.add_argument('--single-pass',action='store_true',help='Skip pass 2')
    p.add_argument('--pass2-plane-margin',type=float,default=.06);p.add_argument('--pass2-region-margin',type=float,default=.08)
    p.add_argument('--pass2-max-inplane-deg',type=float,default=5.)
    p.add_argument('--min-holds',type=int,default=10);p.add_argument('--min-validation-holds',type=int,default=2)
    p.add_argument('--max-train-rmse-px',type=float,default=2.);p.add_argument('--min-inlier-share',type=float,default=.75)
    p.add_argument('--max-holdout-median-px',type=float,default=2.);p.add_argument('--max-holdout-px',type=float,default=6.)
    p.add_argument('--max-guess-deviation-m',type=float,default=.15);p.add_argument('--max-guess-deviation-deg',type=float,default=15.)
    p.add_argument('--max-pass-change-mm',type=float,default=20.);p.add_argument('--max-pass-change-deg',type=float,default=1.)
    return p


def next_steps(output):
    name=Path(output).name
    return '\n'.join(['Next steps:',
        f'  1. Copy the same bytes to the Pi (node and laptop compare SHA-256), from Git Bash in the repo:',
        f'       PI=labpi@<pi address from tools/rig.py find> deployment/lab/lab_sync.sh push-secret config/{name}',
        '  2. Restart the capture service WITHOUT --bringup (refused unless the file is on both ends):',
        '       python tools/rig.py node restart --live',
        '  3. On the laptop run live mode (no --bringup):',
        '       python -m command_center --mode live --config config/profiles/hotspot.yaml --capture-host 192.168.137.N',
        '  4. Keep the mount rigid: a knock to the camera or LiDAR voids this calibration (re-record and rerun).'])


def main(argv=None):
    args=parser().parse_args(argv)
    real,provenance=recording_provenance(args.recording)
    if not real and not args.rehearsal:
        print(f"Refusing: {provenance['why']}. Record with tools/record_raw.py --bringup, or pass --rehearsal (writes measured: false).",file=sys.stderr);return 2
    measured=bool(real)
    if args.output.exists() and not args.force:print(f'Refusing: {args.output} exists; pass --force (the old file is archived).',file=sys.stderr);return 2
    workdir=args.workdir or args.recording.parent/f'{args.recording.name}_calibration';workdir.mkdir(parents=True,exist_ok=True)
    guess=targets.initial_guess(args.initial_guess)
    holds,K,dist,image_size,source=targets.detect_holds(args.recording,args);detected=(K,dist,image_size,source)
    print(f'{len(holds)} still holds found',flush=True)
    first=run_pass('pass1',holds,detected,args,guess,workdir,measured);final=first
    if first['calibration'] is not None and not args.single_pass:
        final=run_pass('pass2',holds,detected,pass2_args(args),first['calibration'].T_lidar_camera,workdir,measured,
                       validation_holds=set(first['summary']['validation_holds']))
    checks=evaluate(final,first,guess,args);accepted=all(c['passed'] for c in checks)
    summary={'accepted':accepted,'measured':measured,'checks':checks,'passes':[{'pass':r['name'],'error':r.get('error'),
             'holds_accepted':r['summary']['holds_accepted'],'T_lidar_camera':None if r['calibration'] is None else r['calibration'].T_lidar_camera.tolist()}
             for r in ([first,final] if final is not first else [first])]}
    (workdir/'acceptance.json').write_text(json.dumps(summary,indent=2,default=str),encoding='utf-8')
    for c in checks:print(f"  {'ok  ' if c['passed'] else 'FAIL'} {c['check']}: {c['value'] if not isinstance(c['value'],float) else round(c['value'],4)} ({c['limit']})")
    if not accepted:
        print(f"NOT accepted: nothing written. Per-hold reasons: {workdir} (pass*_targets.json). Too few holds: record more, "
              "nearer (1-2 m) and spread over the view; tape-guess checks: re-measure the mount.",file=sys.stderr);return 2
    document=calibration_document(final,first,checks,provenance,args,measured)
    try:install(document,args.output,args.force)
    except FileExistsError as exc:print(f'Refusing: {exc}',file=sys.stderr);return 2
    T=np.asarray(document['T_lidar_camera']);held=document['holdout']
    print(f"Accepted ({'MEASURED' if measured else 'REHEARSAL, measured: false'}): wrote {args.output} ({document['calibration_id']})")
    print(f"  camera in LiDAR frame: {np.round(T[:3,3],4).tolist()} m; held-out median {held['median_px']:.2f} px over {held['corners']} corners")
    if measured:print(next_steps(args.output))
    return 0


if __name__=='__main__':sys.exit(main())

"""Estimate IMU biases from a recording made with the rig standing still (no calibration needed).

Gyro bias is the mean angular rate. Accelerometer bias uses the gravity-magnitude method: at rest the
true specific force is gravity (9.80665 m/s^2) along the measured mean direction, so the bias is the
mean minus that vector. One orientation cannot separate a bias from a scale error on the vertical axis;
recordings in other orientations (pass several) are combined so each axis is estimated from the
orientations that load it. The tool only suggests values (`live.imu_accel_bias`); it never edits config.

    python tools/imu_bias_from_recording.py C:/rec/still_60s --output reports/hardware/pi/imu_bias.json
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
G=9.80665


def samples(recording):
    from ingestion.recorder_replayer import ReplaySource
    seen={};source=ReplaySource(recording)
    try:
        for frame in source:
            for s in frame.imu_samples:seen[round(float(s.timestamp),6)]=(np.asarray(s.gyro,float),np.asarray(s.accel,float))
    finally:source.close()
    t=np.array(sorted(seen));return t,np.array([seen[k][0] for k in t]),np.array([seen[k][1] for k in t])


def analyse(recording,max_gyro_std,max_accel_std):
    t,g,a=samples(recording)
    if len(t)<100:raise SystemExit(f'{recording}: only {len(t)} IMU samples')
    still={'gyro_std_rad_s':g.std(0).tolist(),'accel_std_m_s2':a.std(0).tolist()}
    moving=bool(np.max(g.std(0))>max_gyro_std or np.max(a.std(0))>max_accel_std)
    mean=a.mean(0);direction=mean/np.linalg.norm(mean)
    return {'recording':str(recording),'samples':int(len(t)),'span_s':float(t[-1]-t[0]),'rate_hz':float((len(t)-1)/(t[-1]-t[0])),
            'stationary':not moving,'noise':still,'gyro_mean_rad_s':g.mean(0).tolist(),'accel_mean_m_s2':mean.tolist(),
            'accel_norm_m_s2':float(np.linalg.norm(mean)),'gravity_direction':direction.tolist(),
            'accel_bias_gravity_method':(mean-G*direction).tolist()},mean,direction


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('recordings',type=Path,nargs='+');p.add_argument('--output',type=Path)
    p.add_argument('--max-gyro-std',type=float,default=.02,help='rad/s; above this the rig was not still')
    p.add_argument('--max-accel-std',type=float,default=.3,help='m/s^2')
    args=p.parse_args(argv)
    rows=[];means=[];dirs=[]
    for recording in args.recordings:
        row,mean,direction=analyse(recording,args.max_gyro_std,args.max_accel_std);rows.append(row)
        if row['stationary']:means.append(mean);dirs.append(direction)
    if not means:raise SystemExit('No stationary recording: keep the rig still for the whole recording')
    M=np.array(means);D=np.array(dirs)
    # Per axis, weight each orientation by how much gravity loads that axis; the bias estimate along an axis
    # is only trustworthy where some orientation loads it (one upright orientation: vertical axis only).
    W=np.abs(D);bias=(W*(M-G*D)).sum(0)/np.maximum(W.sum(0),1e-9);coverage=W.max(0)
    gyro=np.mean([r['gyro_mean_rad_s'] for r in rows if r['stationary']],axis=0)
    report={'schema_version':1,'kind':'imu_bias','suggestion_only':True,'gravity_m_s2':G,'recordings':rows,
            'accel_bias_m_s2':bias.tolist(),'axis_gravity_coverage':coverage.tolist(),'gyro_bias_rad_s':gyro.tolist(),
            'limitations':'gravity-magnitude method; one orientation cannot separate bias from scale on the loaded axis; '
                          'unloaded axes keep the gravity-direction estimate only',
            'config_snippet':{'live':{'imu_accel_bias':[round(float(v),4) for v in bias]}}}
    text=json.dumps(report,indent=2)
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text,encoding='utf-8')
    print(json.dumps({'accel_bias_m_s2':report['config_snippet']['live']['imu_accel_bias'],'gyro_bias_rad_s':[round(float(v),6) for v in gyro],
                      'stationary_recordings':len(means),'axis_gravity_coverage':[round(float(v),3) for v in coverage]}))
    return 0


if __name__=='__main__':sys.exit(main())

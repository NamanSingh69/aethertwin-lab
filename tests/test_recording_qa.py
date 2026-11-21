"""tools/recording_qa.py on small synthetic recordings written with the repo's FrameRecorder (synthetic only).

A box room with two pillars is ray-cast from the cart at 12 sweeps/s with a 250 Hz IMU and a small RGB-D image:
a parked recording passes, the same recording with the cart pushed 1 m and turned mid-way fails as `stationary`,
and one with two sweeps lost on the link warns."""
import json
from pathlib import Path
import numpy as np
import pytest

pytest.importorskip('cv2');pytest.importorskip('scipy')
from core.contracts import MultiModalSensorFrame,IMUSample
from ingestion.recorder_replayer import FrameRecorder
from tools import recording_qa

PERIOD=1/12;SECONDS=12;ROOM=(np.array([-3.,-2.5,-.6]),np.array([5.,3.5,2.2]))
PILLARS=[(np.array([1.5,1.,-.6]),np.array([2.,1.5,2.2])),(np.array([-1.5,-1.8,-.6]),np.array([-.9,-1.2,1.]))]


def cast(origin,dirs):
    """Range along each unit direction from an origin inside the room to the nearest wall or pillar."""
    lo,hi=ROOM;inv=1/np.where(np.abs(dirs)<1e-9,1e-9,dirs);t=np.min(np.where(dirs>0,(hi-origin)*inv,(lo-origin)*inv),axis=1)
    for a,b in PILLARS:
        t1=(a-origin)*inv;t2=(b-origin)*inv;near=np.max(np.minimum(t1,t2),axis=1);far=np.min(np.maximum(t1,t2),axis=1)
        t=np.where((near<=far)&(near>0)&(near<t),near,t)
    return t


def texture(rng):
    return np.kron(rng.choice([60,190],(13,20)),np.ones((8,8)))[:100,:160].astype(np.uint8)[:,:,None].repeat(3,axis=2)


def write_recording(path,moving=False,drop=(),seed=0):
    """12 s parked (or pushed 1 m forward and turned 30 deg between 4 and 8 s); frame ids in `drop` are lost."""
    rng=np.random.default_rng(seed);rgb=texture(rng);depth=np.full((100,160),2.,np.float32);depth[:20]=0
    diagnostics={'synthetic':True};n=int(SECONDS/PERIOD)
    with FrameRecorder(path,chunk_size=16,compression_level=1) as recorder:
        for i in range(n):
            ts=i*PERIOD;end=ts+PERIOD;a=np.clip((ts-4)/4,0,1) if moving else 0.;yaw=np.radians(30)*a;c=np.array([a*1.,0,0])
            moving_now=moving and 4<=ts<8
            dirs=rng.normal(size=(3500,3));dirs/=np.linalg.norm(dirs,axis=1,keepdims=True)
            world=c+dirs*cast(c,dirs)[:,None];Rz=np.array([[np.cos(yaw),-np.sin(yaw),0],[np.sin(yaw),np.cos(yaw),0],[0,0,1]])
            local=(world-c)@Rz+rng.normal(0,.01,(3500,3))
            points=np.hstack([local,np.full((3500,1),.5),np.ones((3500,2)),np.linspace(0,PERIOD,3500)[:,None]]).astype(np.float32)
            stamps=np.linspace(ts,end,22);imu=[]
            for s in stamps:
                gyro=rng.normal(0,.003,3)+([0,0,np.radians(30)/4] if moving_now else 0);accel=np.array([0,0,9.81])+rng.normal(0,.5 if moving_now else .05,3)
                imu.append(IMUSample(float(s),gyro,accel))
            if i in drop:continue
            recorder.write(MultiModalSensorFrame(i,ts,points,tuple(imu),rgb=rgb,depth=depth,camera_timestamp=end-.005,sweep_duration=PERIOD,diagnostics=diagnostics))
    transport={k:0 for k in ('cloud_missing','camera_missing','camera_incomplete','imu_missing_samples','imu_sequence_breaks','clock_jumps','stalls','decode_errors','receiver_overflows','protocol_errors')}
    transport.update(segments=1,clouds=n,camera_frames=n,imu_samples=22*n,cloud_missing=len(drop),node={'clouds_dropped':0,'camera_dropped':0,'imu_dropped':0})
    report={'schema_version':1,'kind':'raw_capture','label':'SYNTHETIC (pytest), not a hardware result','bringup_uncalibrated':True,'motion':'mobile' if moving else 'stationary',
            'duration_requested_s':float(SECONDS),'frames':n-len(drop),'error':None,'recorder':{'written_frames':n-len(drop),'dropped_frames':0,'complete':True},'transport':transport}
    (Path(path)/'capture_report.json').write_text(json.dumps(report),encoding='utf-8')
    (Path(path)/'recording_status.json').write_text(json.dumps({'complete':True,'dropped_frames':0,'schema_version':1}),encoding='utf-8')
    return path


def run(recording,purpose,tmp_path):
    out=tmp_path/f'{Path(recording).name}_{purpose}.json';code=recording_qa.main([str(recording),'--purpose',purpose,'--json',str(out)])
    result=json.loads(out.read_text(encoding='utf-8'));assert result['exit_code']==code;return code,{c['check']:c for c in result['checks']},result


def test_parked_recording_passes(tmp_path):
    code,checks,result=run(write_recording(tmp_path/'parked'),'stationary',tmp_path)
    assert code==0,[c for c in checks.values() if c['status']!='PASS']
    assert checks['frame count and rate']['value']['frames']==144 and checks['cart stayed still']['value']['turning_windows']==checks['cart stayed still']['value']['shaking_only_windows']==0
    assert checks['cart stayed still']['value']['start_end_icp_offset_m']<.05 and result['elapsed_s']<15


def test_cart_moved_during_stationary_fails_and_passes_as_mobile(tmp_path):
    recording=write_recording(tmp_path/'pushed',moving=True,seed=1)
    code,checks,_=run(recording,'stationary',tmp_path)
    assert code==2 and checks['cart stayed still']['status']=='FAIL' and checks['cart stayed still']['hint']
    assert checks['cart stayed still']['value']['turning_windows']>=4
    code,checks,result=run(recording,'mobile',tmp_path)
    assert checks['cart moved']['status']=='PASS' and checks['max angular rate']['status']=='PASS'
    assert .6<result['summary']['motion']['path_proxy_m']<1.5  # pushed 1.0 m; a proxy, not odometry
    loop=checks['start and end look alike (loop hint)']['value'];assert not loop['same_place_likely'] and .8<loop['icp_offset_m']<1.2 and 20<loop['icp_rotation_deg']<40


def test_dropped_frames_warn(tmp_path):
    code,checks,_=run(write_recording(tmp_path/'lossy',drop=(70,71),seed=2),'stationary',tmp_path)
    assert code==1 and not [c for c in checks.values() if c['status']=='FAIL']
    assert checks['transport losses']['status']=='WARN' and checks['transport losses']['value']['cloud_missing']==2
    assert checks['sweep ids']['value']['missing_ids']==2 and checks['sweep start gaps']['value']['holes_gt_50ms']==1

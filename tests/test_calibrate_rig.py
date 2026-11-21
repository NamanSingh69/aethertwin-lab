"""SYNTHETIC end-to-end rehearsal of the real camera-LiDAR calibration (no hardware, no real data).

The scene mirrors the planned session: a Gemini 336L at 640x400 with the factory intrinsics recorded on
the Pi (reports/hardware/pi/device_check_gemini_usb2.json), JPEG colour at the hotspot profile's quality,
a Unitree L2 (~5190 points per sweep, ~12 sweeps/s, 1.5 cm range noise, a non-repetitive pattern over the
repo simulator's elevation band), and the target drawn by tools/screen_checkerboard.html on
a 24-inch monitor (default measurements: 543 x 324 mm face, 40.6 mm squares, pattern centred on the face)
standing on its stand on the floor or a chair, with a desk panel behind it in some holds and a person
beside it. Every number it produces is synthetic evidence about the software chain, not about the rig.
The capture_report.json fixture below is shaped like a real bring-up capture only so the measured-file path
(hotspot profile loader, Pi capture node) can be exercised; the files it leads to live in pytest tmp dirs.
"""
import json
from pathlib import Path
import numpy as np
import pytest

cv2=pytest.importorskip('cv2')
from core.contracts import MultiModalSensorFrame,IMUSample
from ingestion.recorder_replayer import FrameRecorder
from tools.extract_calibration_targets import OPTICAL_TO_LIDAR
from tools import calibrate_rig

ROOT=Path(__file__).resolve().parents[1]

W,H=640,400;FPS=12.
K=np.array([[305.055908203125,0,322.01495361328125],[0,305.04791259765625,198.9817657470703],[0,0,1.]])
DIST=np.array([-0.03058234602212906,0.03583690524101257,-6.669134018011391e-05,-0.00018124846974387765,-0.012553744949400425])
LIDAR_HEIGHT=.30;FLOOR=-LIDAR_HEIGHT;POINTS_PER_SWEEP=5190;RANGE_NOISE=.015


def screen_target(picture=(527.,296.),pixels=(1920,1080),frame=(8.,8.,8.,20.)):
    """The layout screen_checkerboard.html draws (same integer-pixel square search), in metres, face-centred."""
    (sw,sh),(pw,ph),(bl,br,bt,bb)=picture,pixels,frame
    px,py=sw/pw,sh/ph;fw,fh=bl+sw+br,bt+sh+bb;cx,cy=(fw/2-bl)/px,(fh/2-bt)/py;s=0
    for t in range(1,min(pw,ph)):
        if cx-4.5*t>=0 and cx+4.5*t<=pw and cy-3.5*t>=0 and cy+3.5*t<=ph:s=t
        elif s:break
    x0,y0=round(cx-4*s),round(cy-3*s)
    return {'face':(fw/1000,fh/1000),'screen':((bl-fw/2)/1000,(bt-fh/2)/1000,sw/1000,sh/1000),
            'pattern':((bl+x0*px-fw/2)/1000,(bt+y0*py-fh/2)/1000,s*px/1000,s*py/1000),'square':s*px/1000}


TARGET=screen_target();BOARD=TARGET['face'];SQUARE=TARGET['square']


def rot(axis,deg):
    v=np.zeros(3);v['xyz'.index(axis)]=np.radians(deg);return cv2.Rodrigues(v)[0]


def true_extrinsics():
    """Camera 18 cm ahead of and below the L2, a few degrees off the nominal optical-to-LiDAR axes."""
    T=np.eye(4);T[:3,:3]=rot('z',1.5)@rot('y',-3.)@rot('x',1.)@OPTICAL_TO_LIDAR;T[:3,3]=[.16,-.03,-.07];return T


TAPE_GUESS='0.13,0.0,-0.04'  # ~5 cm off the truth, nominal axes (~3.5 degrees off)

# Holds: distance from the camera (m), bearing (deg, +left), turn of the monitor about its stand (deg), tilt back (deg),
# what it stands on, and a desk panel parallel to it this far behind its face (m) or None.
HOLDS=[(1.0,0,0,0,'floor',None),(1.1,20,30,5,'floor',None),(1.2,-22,-30,0,'floor',.22),(1.4,10,-35,10,'chair',None),
       (1.5,-12,25,-5,'chair',None),(1.6,0,40,15,'floor',.25),(1.8,24,-20,0,'floor',None),(1.9,-24,35,10,'chair',.3),
       (2.1,8,-30,5,'floor',None),(2.2,-8,20,18,'chair',None),(2.4,14,30,0,'floor',None),(1.3,30,0,18,'floor',None),
       (1.7,-30,-40,0,'chair',None),(2.5,-14,-25,8,'floor',.25)]


def box(centre,axes,half):return (np.asarray(centre,float),np.asarray(axes,float),np.asarray(half,float))


def monitor_scene(distance,bearing,turn,tilt,support,desk,T):
    """(R_lidar_board, centre_lidar, boxes) for a monitor on its stand; board frame x right, y down, z away from the rig."""
    camera=T[:3,3];b=np.radians(bearing);phi=np.radians(bearing+turn)
    z=np.array([np.cos(phi),np.sin(phi),0.]);x=np.array([np.sin(phi),-np.cos(phi),0.]);up=np.array([0,0,1.])
    R=np.c_[x,np.cross(z,x),z]@rot('x',-tilt)  # tilt back: the top edge moves away
    top=FLOOR+(.475 if support=='chair' else 0.)
    centre=np.r_[camera[:2]+distance*np.array([np.cos(b),np.sin(b)]),top+.10+BOARD[1]/2]
    yaw=np.c_[x,z,up];neck=centre+.06*z;boxes=[box(centre+R@[0,0,.025],R,[BOARD[0]/2,BOARD[1]/2,.025]),  # monitor body, face at z=0
        box((neck+np.r_[neck[:2],top])/2,yaw,[.035,.015,(neck[2]-top)/2]),box(np.r_[neck[:2],top+.008],yaw,[.12,.10,.008])]
    if support=='chair':
        seat=np.r_[neck[:2],FLOOR+.45];boxes.append(box(seat,yaw,[.22,.22,.025]))
        for sx in (-.2,.2):
            for sy in (-.2,.2):boxes.append(box(seat+yaw@[sx,sy,0]-[0,0,.225],yaw,[.015,.015,.225]))
    if desk is not None:
        panel=np.r_[centre[:2]+desk*z[:2],FLOOR+.5];boxes+=[box(panel,yaw,[.6,.01,.22]),box(panel+yaw@[0,.3,.24],yaw,[.6,.3,.015])]
    side=np.r_[centre[:2]+.75*np.sign(bearing or 1)*-x[:2]+.3*z[:2],FLOOR+.85];boxes.append(box(side,yaw,[.2,.12,.85]))  # the person
    return R,centre,boxes


def cast(dirs,boxes):
    """Nearest hit along unit rays from the LiDAR origin: scene boxes inside a 7 x 6 x 2.7 m room."""
    room=np.array([[-2.5,4.5],[-3.,3.],[FLOOR,FLOOR+2.7]]);safe=np.where(np.abs(dirs)<1e-12,1e-12,dirs)
    t=np.min(np.where(safe>0,room[:,1]/safe,room[:,0]/safe),axis=1)
    for c,A,h in boxes:
        o=-c@A;d=dirs@A;d=np.where(np.abs(d)<1e-12,1e-12,d);t1=(-h-o)/d;t2=(h-o)/d
        near=np.max(np.minimum(t1,t2),axis=1);far=np.min(np.maximum(t1,t2),axis=1);hit=(far>=near)&(near>.05)
        t=np.where(hit&(near<t),near,t)
    return t


def sweep(boxes,rng):
    az=rng.uniform(0,2*np.pi,POINTS_PER_SWEEP);el=np.arcsin(rng.uniform(-.65,.8,POINTS_PER_SWEEP))
    dirs=np.c_[np.cos(el)*np.cos(az),np.cos(el)*np.sin(az),np.sin(el)];r=cast(dirs,boxes)+rng.normal(0,RANGE_NOISE,len(dirs))
    cloud=np.zeros((len(dirs),7),np.float32);cloud[:,:3]=dirs*r[:,None];cloud[:,3]=.5;cloud[:,4:6]=1;cloud[:,6]=np.linspace(0,1/FPS,len(dirs))
    return cloud


_RAYS={}
def camera_rays(ss=2):
    if ss not in _RAYS:
        u,v=np.meshgrid((np.arange(W*ss)+.5)/ss-.5,(np.arange(H*ss)+.5)/ss-.5)
        n=cv2.undistortPoints(np.c_[u.ravel(),v.ravel()].reshape(-1,1,2).astype(np.float64),K,DIST).reshape(-1,2);_RAYS[ss]=np.c_[n,np.ones(len(n))]
    return _RAYS[ss],ss


def shade(x,y):
    fw,fh=BOARD;sx,sy,sw,sh=TARGET['screen'];px,py,qx,qy=TARGET['pattern']
    out=np.full(x.shape,np.nan);face=(np.abs(x)<=fw/2)&(np.abs(y)<=fh/2);out[face]=28.  # bezel
    lit=face&(x>=sx)&(x<sx+sw)&(y>=sy)&(y<sy+sh);out[lit]=232.
    i=np.floor((x-px)/qx);j=np.floor((y-py)/qy);out[lit&(i>=0)&(i<8)&(j>=0)&(j<6)&((i+j)%2==0)]=18.
    return out


def render(R,t,background):
    """Grey image of the monitor face (board pose R,t in the camera frame), supersampled, with the factory distortion."""
    d,ss=camera_rays();n=R[:,2];s=(n@t)/(d@n);local=(d*s[:,None]-t)@R
    g=shade(local[:,0],local[:,1]);g[s<=0]=np.nan;g=g.reshape(H*ss,W*ss)
    g=np.where(np.isnan(g),cv2.resize(background,(W*ss,H*ss),interpolation=cv2.INTER_LINEAR),g)
    return cv2.GaussianBlur(cv2.resize(g.astype(np.float32),(W,H),interpolation=cv2.INTER_AREA),(0,0),.6)


def photograph(clean,rng,quality=80):
    image=np.clip(clean+rng.normal(0,2.,clean.shape),0,255).astype(np.uint8)
    ok,data=cv2.imencode('.jpg',np.repeat(image[...,None],3,axis=2),[cv2.IMWRITE_JPEG_QUALITY,quality]);assert ok
    return cv2.cvtColor(cv2.imdecode(data,cv2.IMREAD_COLOR),cv2.COLOR_BGR2RGB)


def record_session(path,T,rng,holds=HOLDS,frames_per_hold=24,moving_frames=4):
    """Bring-up-shaped recording: each hold ~2 s still, then the monitor moves to the next pose."""
    background=cv2.GaussianBlur(rng.uniform(60,170,(H//8,W//8)).astype(np.float32),(0,0),1.5)
    R_cl=T[:3,:3].T;scenes=[monitor_scene(*hold,T) for hold in holds];fid=0;truth=[]
    diagnostics={'camera_calibration_validation':{'bringup_factory_intrinsics_only':True,'color':{'K':K.tolist(),'distortion':DIST.tolist()}},'synthetic':True}
    with FrameRecorder(path,chunk_size=16,compression_level=1) as recorder:
        def write(R,centre,boxes,clean):
            nonlocal fid
            imu=(IMUSample(fid/FPS,np.zeros(3),np.zeros(3)),IMUSample((fid+1)/FPS,np.zeros(3),np.zeros(3)))
            recorder.write(MultiModalSensorFrame(fid,fid/FPS,sweep(boxes,rng),imu,rgb=photograph(clean,rng),sweep_duration=1/FPS,diagnostics=diagnostics));fid+=1
        for k,(R,centre,boxes) in enumerate(scenes):
            pose=(R_cl@R,R_cl@(centre-T[:3,3]));truth.append(pose);clean=render(*pose,background)
            for _ in range(frames_per_hold):write(R,centre,boxes,clean)
            if k+1<len(scenes):
                R2,c2,_=scenes[k+1];step=cv2.Rodrigues(R2@R.T)[0].ravel()
                for m in range(1,moving_frames+1):
                    a=m/(moving_frames+1);Rm=cv2.Rodrigues(a*step)[0]@R;cm=(1-a)*centre+a*c2
                    write(Rm,cm,[box(cm+Rm@[0,0,.025],Rm,boxes[0][2])]+boxes[3:],render(R_cl@Rm,R_cl@(cm-T[:3,3]),background))
    return truth


def capture_report(recording,synthetic=False):
    """Shaped like tools/record_raw.py's capture_report.json (see the module docstring)."""
    Path(recording).mkdir(parents=True,exist_ok=True)
    report={'schema_version':1,'kind':'raw_capture','label':'SYNTHETIC node (rehearsal), not a hardware result' if synthetic else calibrate_rig.REAL_LABEL,
            'bringup_uncalibrated':True,'error':None,'node_identity':{'devices':{'synthetic':synthetic,'bringup':True}},'note':'pytest fixture over synthetic frames'}
    (Path(recording)/'capture_report.json').write_text(json.dumps(report),encoding='utf-8')


def rotation_error(A,B):return float(np.degrees(np.arccos(np.clip((np.trace(A[:3,:3]@B[:3,:3].T)-1)/2,-1,1))))


ARGS=['--cols','7','--rows','5','--square',str(SQUARE),'--board-size',*map(str,BOARD),'--initial-guess',TAPE_GUESS]


@pytest.fixture(scope='module')
def session(tmp_path_factory):
    """One synthetic monitor session through the whole tool; the detected holds are kept for the cheaper tests."""
    base=tmp_path_factory.mktemp('rig');T=true_extrinsics();recording=base/'calib_targets'
    record_session(recording,T,np.random.default_rng(3));capture_report(recording)
    output=base/'config'/'calibration_extrinsics_640x400.json';detect=calibrate_rig.targets.detect_holds;cache={}
    def remember(rec,args):cache['holds']=detect(rec,args);return cache['holds']
    calibrate_rig.targets.detect_holds=remember
    try:code=calibrate_rig.main([str(recording),*ARGS,'--output',str(output)])
    finally:calibrate_rig.targets.detect_holds=detect
    return {'base':base,'recording':recording,'output':output,'T':T,'code':code,'holds':cache['holds'],'workdir':base/'calib_targets_calibration'}


def test_synthetic_monitor_session_recovers_the_extrinsics(session):
    assert session['code']==0
    data=json.loads(session['output'].read_text(encoding='utf-8'));truth=session['T'];T=np.array(data['T_lidar_camera'])
    angle=rotation_error(T,truth);shift=float(np.linalg.norm(T[:3,3]-truth[:3,3]))
    assert angle<.5 and shift<.015,(angle,shift)
    guess=calibrate_rig.targets.initial_guess(TAPE_GUESS);assert np.linalg.norm(guess[:3,3]-truth[:3,3])>.04 and rotation_error(guess,truth)>3
    rig=data['rig_calibration'];assert rig['acceptance']['passed'] and rig['holds']['accepted']>=12 and len(rig['holds']['validation'])>=3
    assert rig['holdout']['median_px']<2. and rig['holdout']['corners']>=12
    first=np.array(rig['passes'][0]['T_lidar_camera']);assert [p['pass'] for p in rig['passes']]==['pass1','pass2']
    assert rotation_error(first,truth)<.5 and np.linalg.norm(first[:3,3]-truth[:3,3])<.015
    # The floor line, the stand and the desk panel never become the board: accepted holds match the declared size,
    # and the desk panel behind the monitor was tried as a plane and refused.
    assert all(np.all(np.abs(np.asarray(h['extent_ratio'])-1)<.06) for h in rig['holds']['per_hold'] if h['accepted'])
    assert any(h.get('rejected_planes') for h in json.loads((session['workdir']/'pass1_targets.json').read_text())['holds'])
    assert data['measured'] is True and data['image_size']==[W,H] and data['calibration_id'].startswith('measured-board-v1-')
    np.testing.assert_array_equal(np.array(data['K']),K);np.testing.assert_array_equal(np.array(data['distortion']),DIST)


def test_hotspot_profile_and_pi_capture_node_accept_the_written_file(session,monkeypatch):
    from core.configuration import load_config
    from ingestion.live_source import load_measured_calibration
    from ingestion.capture_node import HardwareDevices
    from ingestion import raw_transport as rt
    from test_gemini_profiles import fake_sdk
    live=dict(load_config(ROOT/'config/profiles/hotspot.yaml')['live']);assert live['calibration']=='config/calibration_extrinsics_640x400.json'
    calibration,data,path=load_measured_calibration(live,session['base'])
    assert path==session['output'] and calibration.measured and data['image_size']==[live['camera_color_width'],live['camera_color_height']]
    # Pi side without --bringup: HardwareDevices loads the same file, and the Gemini driver accepts it against
    # SDK-shaped 640x400 profiles that report the factory intrinsics (a fake SDK, not a device test).
    fake_sdk(monkeypatch,color_size=(W,H),depth_size=(live['camera_width'],live['camera_height']),color_K=K,distortion=DIST)
    class Reader:
        def poll(self):return None
        def close(self):pass
    devices=HardwareDevices(live,session['base'],reader=Reader(),decode_color=False)
    try:
        assert devices.calibration_path==session['output'] and devices.camera.calibration is devices.calibration and devices.calibration.measured
        validation=devices.camera.last_metadata['camera_calibration_validation']
        assert validation['measured'] and validation['image_size']==[W,H]
        for stream in ('color','depth'):
            assert validation[stream]['max_intrinsics_error_px']<=live.get('camera_intrinsics_tolerance_px',1.) and validation[stream]['max_distortion_error']<=1e-4
        assert rt.file_identity(devices.calibration_path)==rt.file_identity(path)  # the laptop compares this SHA-256 with the node's
    finally:devices.close()


def test_refuses_unproven_recordings_and_existing_files(session,tmp_path):
    output=tmp_path/'cal.json'
    assert calibrate_rig.main([str(tmp_path/'none'),*ARGS,'--output',str(output)])==2 and not output.exists()  # no capture report
    capture_report(tmp_path/'synthetic',synthetic=True)
    assert calibrate_rig.main([str(tmp_path/'synthetic'),*ARGS,'--output',str(output)])==2 and not output.exists()
    before=session['output'].read_bytes()
    assert calibrate_rig.main([str(session['recording']),*ARGS,'--output',str(session['output'])])==2 and session['output'].read_bytes()==before


def test_failed_acceptance_writes_nothing_and_force_archives_the_old_file(session,tmp_path,monkeypatch):
    monkeypatch.setattr(calibrate_rig.targets,'detect_holds',lambda recording,args:session['holds'])
    output=tmp_path/'cal.json';run=lambda *extra:calibrate_rig.main([str(session['recording']),*ARGS,'--output',str(output),'--workdir',str(tmp_path/'w'),*extra])
    assert run('--max-holdout-median-px','0.01')==2 and not output.exists()
    checks=json.loads((tmp_path/'w'/'acceptance.json').read_text())['checks'];assert [c['check'] for c in checks if not c['passed']]==['held-out median px']
    assert run('--min-holds','15')==2 and not output.exists()
    output.write_text('{"calibration_id": "old"}',encoding='utf-8');old=output.read_bytes()
    assert run()==2 and output.read_bytes()==old  # exists, no --force
    assert run('--force')==0
    previous=json.loads(output.read_text(encoding='utf-8'))['publication']['previous_output']
    assert previous['calibration_id']=='old' and Path(previous['retained_path']).read_bytes()==old


def test_rehearsal_on_a_synthetic_recording_is_never_loadable_as_measured(session,tmp_path,monkeypatch):
    from ingestion.live_source import load_measured_calibration
    monkeypatch.setattr(calibrate_rig.targets,'detect_holds',lambda recording,args:session['holds'])
    capture_report(tmp_path/'rehearsal',synthetic=True);output=tmp_path/'rehearsal.json'
    assert calibrate_rig.main([str(tmp_path/'rehearsal'),*ARGS,'--output',str(output),'--rehearsal'])==0
    data=json.loads(output.read_text(encoding='utf-8'));assert data['measured'] is False and 'REHEARSAL' in data['rig_calibration']['evidence']
    assert data['calibration_id'].startswith('synthetic-board-v1-')
    with pytest.raises(ValueError,match='measured'):load_measured_calibration({'calibration':str(output)})

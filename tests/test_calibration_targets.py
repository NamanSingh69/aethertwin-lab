"""Calibration-target extraction on a synthetic scene with a known camera-LiDAR transform (no hardware)."""
import json
import numpy as np
import pytest

cv2=pytest.importorskip('cv2')
from core.contracts import MultiModalSensorFrame,IMUSample
from ingestion.recorder_replayer import FrameRecorder
from ingestion.calibration import calibrate_camera_lidar
from tools.extract_calibration_targets import OPTICAL_TO_LIDAR,main as extract

W,H=640,400;K=np.array([[450.,0,320],[0,450.,200],[0,0,1]]);DIST=np.zeros(5)
COLS,ROWS,SQUARE=7,5,.06;BOARD=(.60,.45);PPM=1000


def rot(rx,ry,rz):
    return cv2.Rodrigues(np.radians([rx,ry,rz]))[0]


def texture():
    image=np.full((int(BOARD[1]*PPM),int(BOARD[0]*PPM)),255,np.uint8)
    squares=(COLS+1,ROWS+1);x0=int((BOARD[0]-squares[0]*SQUARE)/2*PPM);y0=int((BOARD[1]-squares[1]*SQUARE)/2*PPM);s=int(SQUARE*PPM)
    for i in range(squares[0]):
        for j in range(squares[1]):
            if (i+j)%2==0:image[y0+j*s:y0+(j+1)*s,x0+i*s:x0+(i+1)*s]=0
    return image


def render(R,t,tex):
    # texture pixel centre u <-> board x = (u+0.5)/PPM - W/2 (board frame centred on the pattern, z out of the board)
    A=np.array([[1/PPM,0,.5/PPM-BOARD[0]/2],[0,1/PPM,.5/PPM-BOARD[1]/2],[0,0,1]])
    Hm=K@np.c_[R[:,:2],t]@A
    gray=cv2.warpPerspective(tex,Hm,(W,H),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT,borderValue=140)
    return np.repeat(gray[...,None],3,axis=2)


def scene(T_true,rng):
    poses=[]
    for k in range(12):
        distance=1.3+.1*k;lateral=(-.45+.08*k)*distance/2;vertical=(.15 if k%2 else -.15)*distance/2
        poses.append((rot(15*np.sin(k),25*np.cos(1.3*k),8*np.sin(.7*k)),np.array([lateral,vertical,distance])))
    tex=texture();frames=[];fid=0
    for R,t in poses:
        image=render(R,t,tex)
        for _ in range(12):
            uv=rng.uniform(-.5,.5,(260,2))*BOARD
            board=np.c_[uv,rng.normal(0,.01,260)]  # 1 cm range noise
            camera=(R@board.T).T+t
            wall=np.c_[rng.uniform(-2,2,500),rng.uniform(-1,1,500),np.full(500,4.)]  # background 4 m away
            points=np.vstack([camera,wall]);lidar=(T_true[:3,:3]@points.T).T+T_true[:3,3]
            cloud=np.zeros((len(lidar),7),np.float32);cloud[:,:3]=lidar;cloud[:,3]=.5;cloud[:,4:6]=1
            imu=(IMUSample(fid*.1,np.zeros(3),np.zeros(3)),IMUSample(fid*.1+.1,np.zeros(3),np.zeros(3)))
            frames.append(MultiModalSensorFrame(fid,fid*.1,cloud,imu,rgb=image,depth=np.zeros((H,W),np.float32),
                diagnostics={'camera_calibration_validation':{'color':{'K':K.tolist(),'distortion':DIST.tolist()}}}))
            fid+=1
        # one moving frame between holds (board elsewhere) so holds separate as they would in a real capture
    return frames


def test_extracted_correspondences_recover_the_true_extrinsics(tmp_path):
    rng=np.random.default_rng(5)
    T_true=np.eye(4);T_true[:3,:3]=rot(2.,-3.,1.5)@OPTICAL_TO_LIDAR;T_true[:3,3]=[.03,-.05,-.07]
    with FrameRecorder(tmp_path/'rec',chunk_size=8,compression_level=1) as recorder:
        for frame in scene(T_true,rng):recorder.write(frame)
    out=tmp_path/'targets.npz'
    assert extract([str(tmp_path/'rec'),'--output',str(out),'--cols',str(COLS),'--rows',str(ROWS),'--square',str(SQUARE),
                    '--board-size',*map(str,BOARD),'--initial-guess','0,0,0'])==0  # guess off by ~9 cm and a few degrees
    data=np.load(out);report=json.loads(out.with_suffix('.json').read_text())
    assert report['holds_found']==12 and report['holds_accepted']==12 and report['validation_landmarks']==12
    assert 'only' in report['initial_guess_used_for']
    calibration,result=calibrate_camera_lidar(data['lidar_points'],data['image_points'],data['K'],data['distortion'],tmp_path/'cal.json',
                                              image_size=tuple(int(v) for v in data['image_size']))
    R_err=calibration.T_lidar_camera[:3,:3]@T_true[:3,:3].T
    angle=np.degrees(np.arccos(np.clip((np.trace(R_err)-1)/2,-1,1)));shift=np.linalg.norm(calibration.T_lidar_camera[:3,3]-T_true[:3,3])
    assert angle<.5 and shift<.015,(angle,shift)
    # Held-out corners reproject close to their detected pixels with the fitted transform.
    T=np.linalg.inv(calibration.T_lidar_camera);camera=(T[:3,:3]@data['validation_lidar_points'].T).T+T[:3,3]
    projected,_=cv2.projectPoints(camera,np.zeros(3),np.zeros(3),K,DIST)
    assert np.median(np.linalg.norm(projected.reshape(-1,2)-data['validation_image_points'],axis=1))<3.


def test_board_not_in_view_is_reported_not_invented(tmp_path):
    rng=np.random.default_rng(1);frames=[]
    for fid in range(10):
        cloud=np.zeros((50,7),np.float32);cloud[:,:3]=rng.normal(0,1,(50,3))
        imu=(IMUSample(fid*.1,np.zeros(3),np.zeros(3)),IMUSample(fid*.1+.1,np.zeros(3),np.zeros(3)))
        frames.append(MultiModalSensorFrame(fid,fid*.1,cloud,imu,rgb=np.full((H,W,3),128,np.uint8),depth=np.zeros((H,W),np.float32),
            diagnostics={'camera_calibration_validation':{'color':{'K':K.tolist(),'distortion':DIST.tolist()}}}))
    with FrameRecorder(tmp_path/'rec',chunk_size=8) as recorder:
        for frame in frames:recorder.write(frame)
    with pytest.raises(SystemExit,match='usable holds'):
        extract([str(tmp_path/'rec'),'--output',str(tmp_path/'t.npz'),'--cols','7','--rows','5','--square','.06','--board-size','.6','.45'])

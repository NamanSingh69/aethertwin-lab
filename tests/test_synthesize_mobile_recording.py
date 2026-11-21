"""tools/synthesize_mobile_recording.py on a tiny synthetic box room: format round trip and IMU consistency."""
import importlib.util,json
from pathlib import Path
import numpy as np
from core.contracts import MultiModalSensorFrame,IMUSample,SensorCapabilities
from ingestion.recorder_replayer import ReplaySource
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('synthesize_mobile_recording',ROOT/'tools/synthesize_mobile_recording.py')
synth=importlib.util.module_from_spec(spec);spec.loader.exec_module(synth)
G=9.80665;LEVER=np.array([-.007698,-.014655,.00667]);BIAS=np.array([.0017,-.001,.7147])


def box_world(step=.03):
    """Walls and ceiling of a 6 x 4 m room, 2.5 m above the sensor plane down to 0.5 m below it."""
    a=np.arange(-3,3+1e-9,step);b=np.arange(-2,2+1e-9,step);z=np.arange(-.5,2.5+1e-9,step);parts=[]
    for x in (-3.,3.):Y,Z=np.meshgrid(b,z);parts.append(np.c_[np.full(Y.size,x),Y.ravel(),Z.ravel()])
    for y in (-2.,2.):X,Z=np.meshgrid(a,z);parts.append(np.c_[X.ravel(),np.full(X.size,y),Z.ravel()])
    X,Y=np.meshgrid(a,b);parts.append(np.c_[X.ravel(),Y.ravel(),np.full(X.size,2.5)]);w=np.concatenate(parts)
    return w,(.3+.4*np.abs(np.sin(w.sum(1)))).astype(np.float32)


def templates(count=30,rays=300,seed=1):
    """Parked frames shaped like the L2 recording: 83.6 ms sweeps with jitter and one sub-ms overlap."""
    rng=np.random.default_rng(seed);frames=[];start=100.;imu_t=np.arange(start-.002,start+count*.0837+.1,.004)
    still=np.array([0,0,G])+BIAS+rng.normal(0,.05,(len(imu_t),3));gyro=rng.normal(0,.004,(len(imu_t),3))
    for k in range(count):
        duration=.0836+rng.uniform(-3e-4,3e-4);el=np.arcsin(rng.uniform(.05,1,rays));az=rng.uniform(-np.pi,np.pi,rays)
        d=np.c_[np.cos(el)*np.cos(az),np.cos(el)*np.sin(az),np.sin(el)];r=np.full(rays,2.);r[:5]=.3  # five rig returns
        pts=np.zeros((rays,7),np.float32);pts[:,:3]=d*r[:,None];pts[:,3]=.5;pts[:,4:6]=1;pts[:,6]=np.sort(rng.uniform(0,duration,rays));pts[-1,6]=duration
        m=(imu_t>=start-.0045)&(imu_t<=start+duration+.004)
        frames.append(MultiModalSensorFrame(frame_id=k,timestamp=start,lidar_points=pts,sweep_duration=duration,camera_timestamp=start+duration-.003,
            imu_samples=tuple(IMUSample(float(t),g,a) for t,g,a in zip(imu_t[m],gyro[m],still[m])),capabilities=SensorCapabilities('unitree-l2',False,True,True,False),
            imu_to_lidar=np.r_[np.c_[np.eye(3),LEVER],[[0,0,0,1]]]))
        start+=duration+(-4e-4 if k==10 else rng.uniform(0,2e-4))
    return frames


def trajectory_for(frames,gdir=(0,0,1)):
    t0=frames[0].timestamp;t1=frames[-1].timestamp+frames[-1].sweep_duration
    return synth.Trajectory([(0,0),(.25,.1),(.45,.35)],t0,t1,gdir,hold_start=.05,hold_end=.05,vmax=.7,cruise=.6,a_long=.6,smooth=.05)


def test_round_trip_through_repo_reader_and_rolling_shutter_geometry(tmp_path):
    world,intensity=box_world();frames=templates();traj=trajectory_for(frames)
    out=list(synth.synthesize_frames(frames,world,intensity,traj,imu_error='real',camera=True,image_size=(64,40),
        calibration=synth.synthetic_calibration(np.array([[30.5,0,32],[0,30.5,20],[0,0,1]]))))
    assert traj.moving_seconds>1 and traj.max_speed<=.7
    gt={'poses_sweep_end':traj.matrices([f.timestamp+f.sweep_duration for f in frames]),'world_points':world.astype(np.float32)}
    synth.write_recording(tmp_path/'rec',out,{'motion_mode':'mobile','synthetic':True,'label':synth.LABEL},{'label':'SYNTHETIC test'},gt)
    source=ReplaySource(tmp_path/'rec');back=list(source)
    assert source.manifest['metadata']['label']=='synthetic mobile from real stationary' and len(back)==len(frames)
    assert json.loads((tmp_path/'rec/capture_report.json').read_text())['label'].startswith('SYNTHETIC')
    assert np.load(tmp_path/'rec/ground_truth.npz')['poses_sweep_end'].shape==(len(frames),4,4)
    for a,b,f in zip(out,back,frames):
        assert np.array_equal(a.lidar_points,b.lidar_points) and a.timestamp==f.timestamp==b.timestamp and b.sweep_duration==f.sweep_duration
        assert [s.timestamp for s in b.imu_samples]==[s.timestamp for s in f.imu_samples] and b.diagnostics['synthetic'] is True
        assert b.depth.shape==(40,64) and b.depth.max()<8 and (b.depth>0).any() and b.calibration.measured is False and np.allclose(b.sensor_pose_world,np.eye(4))
        assert np.array_equal(b.lidar_points[:5],f.lidar_points[:5])  # rig returns stay rigid with the rig
    # Each return, placed with the ground-truth pose at its own timestamp, lies on the box.
    f=back[20];p=f.lidar_points[5:];R,o=traj.pose(f.timestamp+p[:,6].astype(float));w=np.einsum('nij,nj->ni',R,p[:,:3])+o
    from scipy.spatial import cKDTree
    assert len(p)>250 and np.percentile(cKDTree(world).query(w)[0],95)<.03
    # IMU = exact trajectory IMU + the template's real residual about gravity (bias and noise carried over).
    _,gyro,acc=synth.imu_table(frames);up=acc.mean(0)/np.linalg.norm(acc.mean(0))
    for b,f in ((back[0],frames[0]),(back[25],frames[25])):
        s0=b.imu_samples[3];r0=f.imu_samples[3];g,a=traj.imu([r0.timestamp],f.imu_to_lidar)
        assert np.allclose(s0.accel,a[0]+r0.accel-G*up,atol=1e-9) and np.allclose(s0.gyro,g[0]+r0.gyro,atol=1e-9)


def test_noise_free_imu_integrates_to_ground_truth_with_repo_deskewer():
    world,intensity=box_world(.06);frames=templates(rays=40);traj=trajectory_for(frames)
    out=list(synth.synthesize_frames(frames,world,intensity,traj,imu_error='none',camera=False))
    start=traj.matrices([frames[0].timestamp])[0]
    deskewer=ContinuousTimeIMUDeskewer(initial_pose=start,initial_velocity=np.zeros(3),imu_to_lidar=frames[0].imu_to_lidar)
    for f in out:deskewer.deskew_frame(f)
    end=traj.matrices([frames[-1].timestamp+frames[-1].sweep_duration])[0];moved=np.linalg.norm(end[:3,3]-start[:3,3])
    assert moved>.3 and np.linalg.norm(deskewer.pose_world[:3,3]-end[:3,3])<.01
    assert synth.rot_deg(deskewer.pose_world[:3,:3].T@end[:3,:3])<.1
    # The deskewed last sweep, placed at the integrated sweep-end pose, lies on the box.
    from scipy.spatial import cKDTree
    w=out[-1].lidar_points[5:,:3]@deskewer.pose_world[:3,:3].T+deskewer.pose_world[:3,3]
    assert np.percentile(cKDTree(world).query(w)[0],90)<.05


def test_trajectory_errors_and_tilted_gravity():
    frames=templates(count=40,rays=5);t0=frames[0].timestamp;t1=frames[-1].timestamp+frames[-1].sweep_duration
    traj=synth.Trajectory([(0,0),(.25,.1),(.45,.35)],t0,t1,(.0024,-.0014,1),hold_start=1.,hold_end=.05,cruise=.6,a_long=.6,smooth=.05)
    T=traj.matrices(np.linspace(t0,t1,12))
    e=synth.trajectory_errors(T,T,rpe_step=3);assert e['ate_rmse_m']<1e-12 and e['loop_closure_translation_m']<1e-9
    shifted=T.copy();shifted[:,0,3]+=.1;assert abs(synth.trajectory_errors(shifted,T,3)['ate_rmse_m']-.1)<1e-9
    _,a=traj.imu([t0+.2],frames[0].imu_to_lidar);up=np.array([.0024,-.0014,1]);assert np.allclose(a[0],G*up/np.linalg.norm(up),atol=1e-3)

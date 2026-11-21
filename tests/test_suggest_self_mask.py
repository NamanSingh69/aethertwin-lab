"""Self-mask suggestion on a synthetic parked recording: fixed body, floor, a moving person, a far room (no hardware)."""
import json
import numpy as np
import pytest
import yaml
pytest.importorskip('scipy')
from core.contracts import MultiModalSensorFrame,IMUSample
from ingestion.recorder_replayer import FrameRecorder,ReplaySource
from ingestion.self_mask import LidarSelfMask
from tools.suggest_self_mask import main,suggest

H=.40  # LiDAR origin above the floor
BODY={'chassis':([-.30,-.15,-.28],[.10,.15,-.12]),'battery':([-.28,-.10,-.12],[-.12,.10,-.04]),'mast':([-.03,-.03,-.12],[.03,.03,-.05]),
      'bracket':([.16,-.04,-.10],[.20,.04,-.04]),'wheel_left':([-.12,.21,-.355],[0.,.25,-.26]),'wheel_right':([-.12,-.25,-.355],[0.,-.21,-.26])}
ROBOT=dict(robot_size=(.50,.50,.48),lidar_offset=(.05,0.))  # footprint x -0.30..0.20, y -0.25..0.25 in the LiDAR frame
LABELS={'body':0,'floor':1,'person':2,'room':3}


def box_surface(rng,low,high,count):
    low,high=np.asarray(low),np.asarray(high);size=high-low;areas=np.array([size[1]*size[2],size[0]*size[2],size[0]*size[1]])
    axis=rng.choice(3,count,p=areas/areas.sum());points=low+rng.random((count,3))*size
    points[np.arange(count),axis]=np.where(rng.random(count)<.5,low[axis],high[axis]);return points


def body_surface(rng,count):
    areas=np.array([2*sum(a*b for a,b in ((s[0],s[1]),(s[0],s[2]),(s[1],s[2]))) for s in (np.subtract(hi,lo) for lo,hi in BODY.values())])
    return np.vstack([box_surface(rng,lo,hi,int(count*a/areas.sum())) for (lo,hi),a in zip(BODY.values(),areas)])


def floor(rng,near=4000,far=1500):
    """Dense near the robot (where a LiDAR 0.4 m up hits the floor steeply), sparser out to 4 m; none under the robot."""
    radius=np.r_[np.sqrt(rng.uniform(0,1.1**2,near)),rng.uniform(1.1,4.,far)];angle=rng.uniform(0,2*np.pi,len(radius))
    points=np.c_[radius*np.cos(angle),radius*np.sin(angle),np.full(len(radius),-H)]
    under=(points[:,0]>-.31)&(points[:,0]<.21)&(np.abs(points[:,1])<.26)  # occluded by the robot
    return points[~under]


def room(rng,count):
    u=rng.random(count);v=rng.uniform(-1,1,count);w=rng.uniform(-H,2.2,count);side=rng.choice(4,count)
    x=np.select([side==0,side==1],[np.full(count,3.),np.full(count,-2.5)],v*3);y=np.select([side==2,side==3],[np.full(count,2.),np.full(count,-2.)],v*2)
    points=np.c_[x,y,w];ceiling=u<.2;points[ceiling]=np.c_[rng.uniform(-2.5,3,ceiling.sum()),rng.uniform(-2,2,ceiling.sum()),np.full(ceiling.sum(),2.2)]
    return points


def person(rng,t,count=400):
    centre=np.array([-.05,0.])+.65*np.array([np.cos(2*np.pi*t),np.sin(2*np.pi*t)]);angle=rng.uniform(0,2*np.pi,count)
    return np.c_[centre[0]+.2*np.cos(angle),centre[1]+.2*np.sin(angle),rng.uniform(-H,1.3,count)]


def scene(repetitive,frames,seed=3):
    """Per frame (xyz, label). Repetitive: the same beam directions every frame (noise + 10 % dropout);
    otherwise every frame samples new points, as a non-repetitive scan pattern does."""
    rng=np.random.default_rng(seed);static=lambda:(body_surface(rng,3000),floor(rng),room(rng,2000))
    fixed=static();out=[]
    for k in range(frames):
        body,ground,walls=fixed if repetitive else static();parts=[body,ground,person(rng,k/frames),walls]
        xyz=np.vstack(parts);label=np.repeat(np.arange(4),[len(p) for p in parts])
        keep=rng.random(len(xyz))>(.1 if repetitive else 0.);xyz=xyz[keep]+rng.normal(0,.003,(keep.sum(),3));out.append((xyz,label[keep]))
    return out


def record(path,frames):
    with FrameRecorder(path,chunk_size=16,compression_level=1) as recorder:
        for fid,(xyz,_) in enumerate(frames):
            cloud=np.zeros((len(xyz),7),np.float32);cloud[:,:3]=xyz;cloud[:,3]=.5;cloud[:,6]=np.linspace(0,.09,len(xyz))
            imu=(IMUSample(fid*.1,np.zeros(3),np.zeros(3)),IMUSample(fid*.1+.1,np.zeros(3),np.zeros(3)))
            recorder.write(MultiModalSensorFrame(fid,fid*.1,cloud,imu))


@pytest.mark.parametrize('repetitive,window,lidar_height',[(True,1,H),(False,4,None)],ids=['repetitive-known-height','nonrepetitive-ransac'])
def test_suggested_boxes_mask_the_body_and_spare_the_rest(tmp_path,repetitive,window,lidar_height):
    frames=scene(repetitive,40 if repetitive else 64);record(tmp_path/'rec',frames)
    report,text=suggest(lambda:ReplaySource(tmp_path/'rec'),window=window,lidar_height=lidar_height,source='synthetic',**ROBOT)
    assert report['suggestion_only'] and 'synthetic' in report['evidence'] and report['warnings']==[],report['warnings']
    if lidar_height is None:
        assert report['floor']['source']=='ransac' and abs(report['floor']['offset_m']-H)<.01 and report['floor']['normal'][2]>.999
    config=yaml.safe_load(text);mask=LidarSelfMask.from_config(config)  # the snippet pastes straight into the config
    assert mask is not None and 3<=len(mask.boxes)<=6  # chassis+battery+mast (touching), bracket (6 cm gap: may link), two wheels
    assert all(box['outside_footprint_m']==0 for box in report['boxes'])
    xyz=np.vstack([f[0] for f in frames]);label=np.concatenate([f[1] for f in frames]);removed=mask.body(xyz)
    body=label==LABELS['body']
    assert removed[body].mean()>=.99,removed[body].mean()
    assert removed[~body].mean()<.005,removed[~body].mean()
    for name in ('floor','person','room'):assert removed[label==LABELS[name]].mean()<.005,name
    assert removed[label==LABELS['person']].sum()==0 and removed[label==LABELS['room']].sum()==0
    # every part of the body lies inside some box
    for low,high in BODY.values():
        centre=(np.asarray(low)+np.asarray(high))/2;assert mask.body(centre[None])[0]
    removal=report['removal'];assert removal['far_removed']==0 and removal['body_removed_fraction']>=.99 and removal['floor_removed_fraction']<.005
    assert sum(b['returns_per_frame_mean'] for b in report['boxes'])==pytest.approx(removal['removed_per_frame_mean'],rel=.01)


def test_footprint_check_warns_and_cli_writes_report_and_snippet(tmp_path,capsys):
    record(tmp_path/'rec',scene(True,24))
    out=tmp_path/'out/suggestion.json'
    assert main([str(tmp_path/'rec'),'--output',str(out),'--lidar-height',str(H),'--robot-size','.30','.30','.48'])==0
    report=json.loads(out.read_text());text=out.with_suffix('.yaml').read_text()
    assert text==capsys.readouterr().out and LidarSelfMask.from_config(yaml.safe_load(text)) is not None
    assert any('outside the robot' in w for w in report['warnings'])  # wheels and chassis end stick out of a 0.30 m robot
    assert report['lidar_self_mask']==yaml.safe_load(text)['lidar_self_mask'] and len(report['boxes'])<=32


def test_static_object_inside_the_radius_is_flagged(tmp_path):
    frames=[(np.vstack([xyz,box_surface(np.random.default_rng(7),[.6,-.3,-.40],[.7,.3,.3],3000)]),None) for k,(xyz,_) in enumerate(scene(True,20))]
    record(tmp_path/'rec',frames)
    report,_=suggest(lambda:ReplaySource(tmp_path/'rec'),window=1,lidar_height=H,**ROBOT)
    assert any('reaches the' in w for w in report['warnings']) and any('outside the robot' in w for w in report['warnings'])


def test_empty_near_field_suggests_nothing(tmp_path):
    rng=np.random.default_rng(0);record(tmp_path/'rec',[(room(rng,500),None) for _ in range(12)])
    report,text=suggest(lambda:ReplaySource(tmp_path/'rec'))
    assert text is None and report['boxes']==[] and report['removal'] is None and any('nothing to suggest' in w for w in report['warnings'])


def test_clusters_beyond_the_cap_merge_with_the_least_added_volume():
    from tools.suggest_self_mask import merge_to_cap
    cubes=[(np.array([.1*k,0,0]),np.array([.1*k+.02,.02,.02]),1,0.) for k in range(40)]+[(np.array([0,.5,0]),np.array([.02,.52,.02]),1,0.)]
    merged,count=merge_to_cap(cubes,32);assert len(merged)==32 and count==9
    for low,high,_,_ in cubes:assert any(np.all(low>=m[0]) and np.all(high<=m[1]) for m in merged)
    assert any(np.allclose(m[0],[0,.5,0]) for m in merged)  # the far cube stays on its own: merging it would add the most volume

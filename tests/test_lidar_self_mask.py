"""LiDAR self-mask: returns on the rig's own body are dropped before processing, aligned arrays follow."""
import copy
from pathlib import Path
import numpy as np
import pytest
import yaml
from core.contracts import MultiModalSensorFrame,IMUSample
from ingestion.self_mask import LidarSelfMask

ROOT=Path(__file__).resolve().parents[1]
CHASSIS={'name':'chassis','min':[-.3,-.12,-.2],'max':[.12,.12,.02]}


def frame(points):
    cloud=np.zeros((len(points),7),np.float32);cloud[:,:3]=points;cloud[:,3]=.5;cloud[:,6]=np.linspace(0,.09,len(points))
    imu=(IMUSample(0.,np.zeros(3),np.zeros(3)),IMUSample(.1,np.zeros(3),np.zeros(3)))
    return MultiModalSensorFrame(1,0.,cloud,imu,beam_ids=np.arange(len(points)),lidar_origins=np.zeros((len(points),3)),
                                 painted_features=np.c_[points,np.arange(len(points))].astype(np.float32),diagnostics={'source':'test'})


def test_body_returns_are_removed_and_aligned_arrays_follow():
    points=np.array([[0.,0.,0.],[-.2,.1,-.1],[.11,-.11,.01],  # inside the chassis box
                     [.05,.0,.3],                            # within min_range only
                     [1.,0.,0.],[0.,2.,.5],[-.36,0.,0.]],float)  # world returns (the last just outside box and range)
    mask=LidarSelfMask([CHASSIS],min_range=.35);out=mask.apply(frame(points))
    np.testing.assert_allclose(out.lidar_points[:,:3],points[[4,5,6]],atol=1e-6)
    np.testing.assert_array_equal(out.beam_ids,[4,5,6]);assert out.lidar_origins.shape==(3,3)
    np.testing.assert_array_equal(out.painted_features[:,3],[4,5,6])
    np.testing.assert_allclose(out.lidar_points[:,6],frame(points).lidar_points[[4,5,6],6])  # per-return times kept
    report=out.diagnostics['lidar_self_mask']
    assert report['removed']==4 and report['kept']==3 and report['frame']=='lidar' and report['boxes'][0]['name']=='chassis'
    assert out.diagnostics['source']=='test'


def test_mask_is_off_unless_enabled_and_rejects_bad_boxes():
    assert LidarSelfMask.from_config({}) is None
    assert LidarSelfMask.from_config({'lidar_self_mask':{'enabled':False,'boxes':[CHASSIS]}}) is None
    assert LidarSelfMask.from_config({'lidar_self_mask':{'enabled':True,'boxes':[CHASSIS]}}).boxes[0][0]=='chassis'
    for bad in ({'min':[0,0,0],'max':[0,1,1]},{'min':[0,0],'max':[1,1]},{'min':[0,0,float('nan')],'max':[1,1,1]},{'max':[1,1,1]}):
        with pytest.raises(ValueError):LidarSelfMask([bad])
    with pytest.raises(ValueError,match='no boxes'):LidarSelfMask.from_config({'lidar_self_mask':{'enabled':True}})
    with pytest.raises(ValueError):LidarSelfMask([],min_range=-1)


def test_mask_applies_once():
    mask=LidarSelfMask([CHASSIS]);out=mask.apply(frame(np.array([[1.,0,0],[0,0,0]])))
    with pytest.raises(ValueError,match='already applied'):mask.apply(out)


def test_shipped_config_leaves_the_mask_off():
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'))
    assert config['lidar_self_mask']['enabled'] is False and LidarSelfMask.from_config(config) is None


def test_pipeline_drops_body_returns_before_processing():
    torch=pytest.importorskip('torch')
    from core.pipeline import LabPipeline,load_commissioned_assets
    from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'));config['device']='cpu'
    twin,static,grid=load_commissioned_assets(ROOT/f"assets/twin_seed{config['seed']}")
    source=SyntheticLabSimulator(config['seed'],config,mobile=False)
    try:raw=source.next_frame()
    finally:source.close()
    near=np.linalg.norm(raw.lidar_points[:,:3],axis=1)
    radius=float(np.percentile(near,20))  # a body-sized sphere that certainly holds returns
    masked=copy.deepcopy(config);masked['lidar_self_mask']={'enabled':True,'min_range':radius,'boxes':[]}
    threads=torch.get_num_threads();torch.set_num_threads(min(2,threads))
    pipeline=LabPipeline(masked,ROOT/config['perception']['checkpoint'],twin,static,grid)
    try:state=pipeline.process(raw)
    finally:
        pipeline.close();torch.set_num_threads(threads)
    report=state.diagnostics['lidar_self_mask']
    assert report['removed']==int((near<radius).sum())>0 and report['kept']==int((near>=radius).sum())
    assert state.diagnostics['module_shapes']['localization']['raw_lidar'][0]==report['kept']


def test_commissioning_keeps_raw_returns_and_records_the_mask(tmp_path):
    import json
    from core.pipeline import build_commissioned_assets
    from ingestion.recorder_replayer import ReplaySource
    from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'));config['device']='cpu'
    config['lidar_self_mask']={'enabled':True,'min_range':1.5,'boxes':[]}
    source=SyntheticLabSimulator(config['seed'],config,mobile=False)
    try:frames=source.commissioning_frames()
    finally:source.close()
    expected=sum(int((np.linalg.norm(f.lidar_points[:,:3],axis=1)<1.5).sum()) for f in frames)
    twin,static,grid=build_commissioned_assets(iter(frames),tmp_path/'twin',config,progress=lambda *_:None)
    twin.close();static.close()
    manifest=json.loads((tmp_path/'twin/bundle_manifest.json').read_text())
    assert manifest['source_provenance']['lidar_self_mask']['removed']==expected>0
    replay=ReplaySource(tmp_path/'twin/commissioning')
    try:assert [len(f.lidar_points) for f in replay]==[len(f.lidar_points) for f in frames]  # the recording is raw
    finally:replay.close()

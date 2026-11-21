"""IMU bias: estimation from a still recording, config plumbing, and the parked pipeline not drifting."""
import copy
import json
from dataclasses import replace
from pathlib import Path
import numpy as np
import pytest
import yaml
from core.contracts import IMUSample,MultiModalSensorFrame
from core.pipeline import imu_accel_bias

ROOT=Path(__file__).resolve().parents[1]


def still_recording(path,bias,seconds=6.):
    from ingestion.recorder_replayer import FrameRecorder
    rng=np.random.default_rng(3)
    with FrameRecorder(path,chunk_size=8) as recorder:
        for k in range(int(seconds/.1)):
            t0=k*.1;imu=tuple(IMUSample(t0+j*.004,rng.normal(0,.004,3),np.array([0,0,9.80665])+bias+rng.normal(0,.08,3)) for j in range(26))
            cloud=np.zeros((20,7),np.float32);cloud[:,0]=np.linspace(1,3,20)
            recorder.write(MultiModalSensorFrame(k,t0,cloud,imu))


def test_still_recording_gives_the_vertical_accelerometer_bias(tmp_path):
    from tools.imu_bias_from_recording import main
    still_recording(tmp_path/'rec',np.array([0.,0.,.715]))
    assert main([str(tmp_path/'rec'),'--output',str(tmp_path/'bias.json')])==0
    report=json.loads((tmp_path/'bias.json').read_text())
    assert report['accel_bias_m_s2'][2]==pytest.approx(.715,abs=.01) and report['axis_gravity_coverage'][2]>.99
    assert report['suggestion_only'] and report['recordings'][0]['stationary']


def test_accel_bias_config_is_validated():
    assert imu_accel_bias({}) is None and imu_accel_bias({'live':{'imu_accel_bias':None}}) is None
    np.testing.assert_allclose(imu_accel_bias({'live':{'imu_accel_bias':[0,0,.7]}}),[0,0,.7])
    for bad in ([0,0],[0,0,float('nan')],[0,0,9.8]):
        with pytest.raises(ValueError):imu_accel_bias({'live':{'imu_accel_bias':bad}})


def test_parked_pipeline_stays_at_its_station_with_a_biased_imu():
    torch=pytest.importorskip('torch')
    from core.pipeline import LabPipeline,load_commissioned_assets
    from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
    config=yaml.safe_load((ROOT/'config/system_config.yaml').read_text(encoding='utf-8'));config['device']='cpu'
    twin,static,grid=load_commissioned_assets(ROOT/f"assets/twin_seed{config['seed']}")
    source=SyntheticLabSimulator(config['seed'],config,mobile=False)
    try:frames=[source.next_frame() for _ in range(4)]
    finally:source.close()
    biased=[replace(f,imu_samples=tuple(IMUSample(s.timestamp,s.gyro,np.asarray(s.accel)+[0,0,.715]) for s in f.imu_samples)) for f in frames]
    threads=torch.get_num_threads();torch.set_num_threads(min(2,threads))
    pipeline=LabPipeline(copy.deepcopy(config),ROOT/config['perception']['checkpoint'],twin,static,grid)
    try:
        poses=[pipeline.process(f).sensor_pose_world for f in biased]
        assert np.allclose(pipeline.deskewer.velocity,0)
    finally:
        pipeline.close();torch.set_num_threads(threads)
    for pose in poses[1:]:np.testing.assert_allclose(pose,poses[0],atol=1e-9)


def test_auto_bias_is_explicit_and_estimates_the_component_along_gravity():
    from core.contracts import IMUSample,MultiModalSensorFrame
    from slam_and_twin.imu_static_init import gravity_bias_from_still,resolve_auto_bias
    assert imu_accel_bias({'live':{'imu_accel_bias':'auto'}})=='auto'
    with pytest.raises(ValueError):imu_accel_bias({'live':{'imu_accel_bias':'yes'}})
    # Tilted 3 degrees, reading 10.52 m/s^2 at rest like the real L2 (reports/hardware/pi/imu_bias_still_60s.json).
    rng=np.random.default_rng(0);up=np.array([np.sin(np.radians(3)),0,np.cos(np.radians(3))])
    still=lambda t0,n:[IMUSample(t,rng.normal(0,.005,3),up*10.52+rng.normal(0,.05,3)) for t in t0+np.arange(n)/250]
    bias,report=gravity_bias_from_still(still(0,500))
    assert report['applied'];np.testing.assert_allclose(bias,up*(10.52-9.80665),atol=.01)
    moving=[IMUSample(s.timestamp,[0,0,.5],s.accel) for s in still(0,500)]
    assert gravity_bias_from_still(moving)[0] is None and gravity_bias_from_still(still(0,100))[1]['reason'].startswith('only')
    frames=[MultiModalSensorFrame(i,i*.08,np.zeros((1,7)),imu_samples=still(i*.08,21),sweep_duration=.08) for i in range(40)]
    bias,report,stream=resolve_auto_bias(iter(frames))
    assert report['applied'] and report['frames_inspected']<40 and list(stream)==frames

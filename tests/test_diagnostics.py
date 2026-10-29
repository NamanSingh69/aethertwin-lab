from dataclasses import replace
from pathlib import Path
import copy,hashlib,json
import numpy as np
import pytest
import torch,yaml
import diagnostics.runner as runner
from diagnostics.audits import floor_alignment_audit,IMUAuditAccumulator
from diagnostics.virtual_targets import run_virtual_targets
from core.contracts import IMUSample,MultiModalSensorFrame

ROOT=Path(__file__).resolve().parents[1]
torch.set_num_threads(2)


def configuration():return yaml.safe_load((ROOT/'config/system_config.yaml').read_text())


def test_floor_reports_only_three_observable_dofs_and_rejects_degenerate_calibration():
    x,y=np.meshgrid(np.linspace(-2,2,19),np.linspace(-1.5,1.5,17))
    points=np.column_stack([x.ravel(),y.ravel(),(.02*x-.015*y+.12).ravel()])
    report=floor_alignment_audit(points)
    assert report['status']=='passed' and report['information_rank']==3
    assert not report['full_six_dof_calibration'] and not report['calibration_written']
    assert report['unobservable_parameters']==['x_translation','y_translation','yaw']
    assert np.asarray(report['observable_covariance']).shape==(3,3)
    line=np.column_stack([np.linspace(-2,2,60),np.zeros((60,2))])
    rejected=floor_alignment_audit(line)
    assert rejected['status']=='failed' and rejected['information_rank']<3
    assert floor_alignment_audit(np.empty((0,3)))['status']=='failed'


def test_imu_measured_rate_coverage_and_explicit_sample_budget():
    samples=tuple(IMUSample(float(t),np.zeros(3),np.array([0,0,9.80665])) for t in np.linspace(0,.1,21))
    frame=MultiModalSensorFrame(0,0.,np.empty((0,7)),samples,sweep_duration=.1)
    audit=IMUAuditAccumulator(max_samples=30);audit.update(frame);report=audit.report()
    assert report['status']=='passed' and report['measured_rate_hz']==pytest.approx(200.)
    assert report['stationary_candidate'] and 'constant translation' in report['stationarity_limit']
    later=replace(frame,frame_id=1,timestamp=.1,imu_samples=tuple(IMUSample(s.timestamp+.1,s.gyro,s.accel) for s in samples))
    audit.update(later)
    assert audit.report()['status']=='failed' and audit.report()['sample_budget_exhausted']
    assert len(audit.samples)==30


def test_isolated_virtual_closed_loop_is_explicit_and_exercises_both_models():
    report=run_virtual_targets()
    assert report['status']=='passed' and report['injected_virtual'] and not report['active_map_modified']
    assert set(report['models'])=={'ca','cv'} and report['blocked_route_status']=='no_path'
    assert report['relative_motion_ttc_seconds']==pytest.approx(1.5)


def test_real_simulated_diagnostic_pipeline_keeps_hardware_pending_and_integrity_verifiable(tmp_path):
    config=configuration();original=copy.deepcopy(config)
    result=runner.run_diagnostics(config,output=tmp_path/'observed',max_frames=2,root=ROOT)
    assert config==original and result['software_checks_passed'] and not result['hardware_checks_passed']
    assert result['source']['actual']=='sim' and result['hardware_status']=='not_tested'
    assert result['stages']['sdk_device_discovery']['status']=='not_tested'
    assert result['stages']['map_residuals']['status']=='passed'
    assert result['stages']['floor_alignment']['information_rank']==3
    virtual=result['stages']['virtual_closed_loop']
    assert virtual['active_map_digest_before']==virtual['active_map_digest_after'] and not virtual['active_map_modified']
    document=json.loads((tmp_path/'observed/report.json').read_text());integrity=document.pop('integrity')
    canonical=json.dumps(document,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    assert hashlib.sha256(canonical).hexdigest()==integrity['canonical_report_sha256']
    manifest=json.loads((tmp_path/'observed/integrity.json').read_text())
    for name,digest in manifest['files'].items():assert hashlib.sha256((tmp_path/'observed'/name).read_bytes()).hexdigest()==digest
    with pytest.raises(FileExistsError):runner.run_diagnostics(config,output=tmp_path/'observed',max_frames=2,root=ROOT)


def test_live_initialization_failure_is_recorded_without_simulation_fallback(tmp_path,monkeypatch):
    selected=[]
    def fail(config,source,input_path,root):selected.append(source);raise RuntimeError('Injected required SDK initialization failure')
    monkeypatch.setattr(runner,'_make_source',fail)
    result=runner.run_diagnostics(configuration(),'live',twin_path=ROOT/'assets/twin_seed7',output=tmp_path/'failure',max_frames=1,root=ROOT)
    assert selected==['live'] and result['source']['actual'] is None
    assert result['stages']['source_initialization']['status']=='failed'
    assert 'Injected required SDK' in result['stages']['source_initialization']['reason']
    assert not result['software_checks_passed'] and not result['hardware_checks_passed']
    assert result['stages']['virtual_closed_loop']['status']=='passed'


@pytest.mark.parametrize('fault',['imu','camera','empty'])
def test_missing_modalities_and_empty_actual_sensor_frames_are_visible(tmp_path,monkeypatch,fault):
    original=runner._make_source
    class FaultCapture:
        def __init__(self,source):self.source=source
        def __iter__(self):return self
        def __next__(self):
            raw=self.source.next_frame()
            if fault=='imu':return replace(raw,imu_samples=())
            if fault=='camera':return replace(raw,rgb=None,depth=None,calibration=None,camera_timestamp=None,gemini_rgbd_cloud=None)
            return replace(raw,lidar_points=np.empty((0,7),np.float32),beam_ids=np.empty(0,np.int64),lidar_origins=None,
                depth=np.zeros_like(raw.depth),rplidar_s2_points=None,gemini_rgbd_cloud=None)
        def close(self):self.source.close()
    monkeypatch.setattr(runner,'_make_source',lambda config,source,input_path,root:FaultCapture(original(config,source,input_path,root)))
    result=runner.run_diagnostics(configuration(),output=tmp_path/fault,max_frames=1,root=ROOT)
    assert not result['software_checks_passed'] and result['stages']['virtual_closed_loop']['status']=='passed'
    if fault=='imu':assert result['stages']['imu_observations']['status']=='failed' and result['stages']['pipeline_observations']['status']=='failed'
    else:assert result['stages']['modality_coverage']['status']=='failed'


def test_replay_does_not_claim_independent_geometry_validation_without_provenance(tmp_path):
    result=runner.run_diagnostics(configuration(),'replay',input_path=ROOT/'assets/sample_recording',output=tmp_path/'replay',max_frames=2,root=ROOT)
    assert result['source']['actual']=='replay' and result['software_checks_passed']
    assert result['stages']['map_residuals']['status']=='not_tested'
    assert 'nearest_measured_map_distance_m' not in result['stages']['map_residuals']
    assert result['stages']['floor_repeatability']['status']=='not_tested'
    assert not result['hardware_checks_passed']

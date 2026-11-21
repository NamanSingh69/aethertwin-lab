"""Submission figures: the tool runs end to end on a tiny synthetic recording and writes honest captions."""
import json
import numpy as np
from core.contracts import IMUSample,MultiModalSensorFrame


def synthetic_recording(path,frames=36,seed=5):
    """A stationary 'room' (walls, floor, ceiling) at ~12 Hz with jittered sweep starts, RGB-D and transport diagnostics."""
    from ingestion.recorder_replayer import FrameRecorder
    rng=np.random.default_rng(seed);t=100.;dur=.0836
    with FrameRecorder(path,chunk_size=8) as recorder:
        for k in range(frames):
            n=600;side=rng.integers(0,5,n);u=rng.uniform(-2,2,n);h=rng.uniform(-.3,2.,n)
            xyz=np.select([side[:,None]==0,side[:,None]==1,side[:,None]==2,side[:,None]==3],
                          [np.c_[np.full(n,2.),u,h],np.c_[np.full(n,-2.),u,h],np.c_[u,np.full(n,2.),h],np.c_[u,np.full(n,-2.),h]],np.c_[u,rng.uniform(-2,2,n),np.full(n,2.4)])
            cloud=np.zeros((n,7),np.float32);cloud[:,:3]=xyz;cloud[:,3]=rng.uniform(0,1,n);cloud[:,4:6]=1;cloud[:,6]=np.sort(rng.uniform(0,dur,n))
            imu=tuple(IMUSample(t+j*.004,rng.normal(0,.005,3),np.array([0,0,10.5])+rng.normal(0,.09,3)) for j in range(22))
            depth=np.where(rng.uniform(size=(40,64))<.2,0.,rng.uniform(.5,4,(40,64))).astype(np.float32)
            diag={'acquisition_segment':1,'sync_error_seconds':float(rng.uniform(-.015,.015)),'camera_color_format':'RGB',
                  'raw_transport':{'segment':1,'bytes':int(4.5e6*(t-100.)),'clock':{'offset_seconds':-5.+1e-6*k,'best_delay_seconds':.0017,
                                   'error_bound_seconds':.00085,'jitter_seconds':.01,'host_clock_alignment_seconds':.009}}}
            recorder.write(MultiModalSensorFrame(k,t,cloud,imu,rgb=rng.integers(0,255,(40,64,3),dtype=np.uint8),depth=depth,
                                                 camera_timestamp=t+.05,sweep_duration=dur,diagnostics=diag))
            t+=dur+(-.0007 if k%5==0 else rng.uniform(0,.0005))
    (path/'capture_report.json').write_text(json.dumps({'motion':'stationary','label':'synthetic test recording','bringup_uncalibrated':True,'elapsed_s':3.}),encoding='utf-8')


def test_tool_writes_every_figure_and_an_honest_index(tmp_path):
    from tools.submission_figures import main
    rec=tmp_path/'synthetic_still';synthetic_recording(rec)
    reports=tmp_path/'reports';(reports/'pi').mkdir(parents=True)
    (reports/'pi'/'capture_report_synthetic.json').write_text(json.dumps({'motion':'stationary','elapsed_s':3.,'frames':36,'frames_hz':12.,
        'recorder':{'dropped_frames':0},'transport':{'rates_hz':{'imu':250.,'cloud':12.,'camera':12.},'bytes':1.3e7,'bandwidth_bytes_per_second':4.4e6,
        'clock':{'error_bound_seconds':.0009,'jitter_seconds':.011,'exchanges':12}}}),encoding='utf-8')
    (reports/'pi'/'imu_bias_synthetic.json').write_text(json.dumps({'recordings':[{'samples':792,'accel_mean_m_s2':[0,0,10.5],'accel_norm_m_s2':10.5}],
        'gyro_bias_rad_s':[0,0,0],'accel_bias_m_s2':[0,0,.69]}),encoding='utf-8')
    out=tmp_path/'figures'
    assert main(['--recording',str(rec),'--reports',str(reports),'--output',str(out),'--accumulate-seconds','2'])==0
    names={p.name for p in out.glob('*.png')}
    assert names=={'fig1a_l2_accumulation_plan.png','fig1b_l2_perspective_coverage.png','fig2_rgb_depth_lidar_side_by_side.png','fig3a_transport_timeseries.png',
                   'fig3b_transport_summary_table.png','fig4_imu_still_stats.png','fig5_sweep_start_gap.png'}
    for name in names:assert (out/name).read_bytes()[:8]==b'\x89PNG\r\n\x1a\n'
    index=(out/'INDEX.md').read_text(encoding='utf-8')
    for name in names:assert f'## {name}' in index
    assert index.count('**Does NOT show:**')==len(names) and 'synthetic_still' in index and '--accumulate-seconds 2' in index
    assert 'projected into the image' in index and 'has not been measured' in index
    # The jittered starts include overlaps (every 5th gap is -0.7 ms) and the caption counts them.
    assert '0 beyond the -2 ms tolerance' in index and 'negative' in index


def test_ceiling_layer_is_found_only_when_one_exists():
    from tools.submission_figures import ceiling_cut
    rng=np.random.default_rng(0);walls=rng.uniform(0,2,20000)
    cut,share=ceiling_cut(np.r_[walls,np.full(8000,2.4)+rng.normal(0,.01,8000)])
    assert cut is not None and 2.1<cut<2.4 and .2<share<.35
    assert ceiling_cut(walls)[0] is None

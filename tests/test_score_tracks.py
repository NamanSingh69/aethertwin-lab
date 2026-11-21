"""Track-level scoring of an offline parked replay (tools/score_tracks.py): exact metrics on synthetic track sequences,
the report and tracks.jsonl from the real pipeline on the repo sample, and an optional real empty-room baseline."""
import json
from pathlib import Path
import numpy as np
import pytest

from core.contracts import SemanticClass
from tools import score_tracks as st

ROOT=Path(__file__).resolve().parents[1]
SAMPLE=ROOT/'assets/sample_recording'
REAL_REC=Path.home()/'aethertwin_rec/wifi_stationary_60s';REAL_TWIN=Path.home()/'aethertwin_demo/bench_twin_lidar_only'
PERSON=SemanticClass.HUMAN_PEDESTRIAN.name;CART=SemanticClass.DYNAMIC_ROBOT_CART.name;OBSTACLE=SemanticClass.NEW_STATIC_OBSTACLE.name


def trk(track_id,x=0.,y=0.,cls=PERSON):return {'id':track_id,'class':cls,'confidence':.9,'x':float(x),'y':float(y),'z':.9,'speed':.5}


def timeline(n,make,hz=10.):return [(i/hz,make(i)) for i in range(n)]


def test_person_and_dynamic_classes_come_from_the_contract():
    assert st.PERSON_CLASSES==(SemanticClass.HUMAN_PEDESTRIAN.name,)
    assert set(st.DYNAMIC_CLASSES)=={SemanticClass.HUMAN_PEDESTRIAN.name,SemanticClass.DYNAMIC_ROBOT_CART.name}


def test_empty_room_false_positive_rates_are_exact():
    # 60 s at 10 Hz: a 1 s phantom person (id 1), a 3 s phantom cart (id 2), and a 10 s new static obstacle (id 3, not an FP).
    frames=timeline(600,lambda i:([trk(1)] if i<10 else [])+([trk(2,cls=CART)] if 100<=i<130 else [])+([trk(3,cls=OBSTACLE)] if 200<=i<300 else []))
    m=st.score_empty(frames)
    assert m['duration_s']==pytest.approx(60.) and m['frames']==600
    assert m['confirmed_tracks']==3 and m['confirmed_tracks_per_min']==pytest.approx(3.)
    assert m['fp_tracks']==2 and m['fp_tracks_per_min']==pytest.approx(2.)
    assert m['person_fp_tracks']==1 and m['person_fp_tracks_per_min']==pytest.approx(1.)
    assert m['longest_fp_track_s']==pytest.approx(3.) and m['longest_fp_track_id']==2
    assert m['fp_time_s_per_min']==pytest.approx(4.)  # 1 s + 3 s of phantom track time in one minute
    assert m['frames_with_fp']==40


def test_walk_counts_a_reidentification_and_a_fragmenting_gap():
    # Frames 0-39 id 1, 40-59 id 2 (re-identified without a gap), 60-74 nobody (1.6 s gap), 75-99 id 2 again.
    def make(i):
        if i<40:return [trk(1,i*.1,.25)]
        if i<60:return [trk(2,i*.1,.25),trk(9,0,0,OBSTACLE)]  # a non-person track does not count
        return [] if i<75 else [trk(2,i*.1,.25)]
    frames=timeline(100,make)
    m=st.score_walk(frames,gap_s=1.,line=(0.,0.,10.,0.))
    assert m['fraction_one_person']==pytest.approx(.85) and m['distinct_person_ids']==2
    assert m['id_switches']==1 and m['fragments']==1 and m['id_switches_plus_fragments']==2
    assert m['longest_gap_s']==pytest.approx(1.6)
    assert m['lateral_median_m']==pytest.approx(.25) and m['lateral_p95_m']==pytest.approx(.25) and m['lateral_samples']==85
    assert st.score_walk(frames,gap_s=2.)['fragments']==0  # the same gap is not a fragment under a 2 s threshold
    assert 'lateral_median_m' not in st.score_walk(frames,gap_s=1.)


def test_lateral_distance_is_to_the_taped_segment():
    d=st.segment_distances(np.array([[5.,-1.],[12.,0.],[0.,.5],[-3.,4.]]),(0.,0.,10.,0.))
    assert d==pytest.approx([1.,2.,.5,5.])
    with pytest.raises(ValueError):st.segment_distances(np.zeros((1,2)),(1.,1.,1.,1.))


def test_crossing_two_person_fraction_switches_and_maximum():
    # 0-29: ids 1 and 2; 30-39: id 2 re-identified as 3; 40-48: id 1 alone; 49: a third phantom person (id 7).
    def make(i):
        if i<30:return [trk(1,i*.1,1),trk(2,i*.1,-1)]
        if i<40:return [trk(1,i*.1,1),trk(3,i*.1,-1)]
        return [trk(1,i*.1,1)]+([trk(3),trk(7)] if i==49 else [])
    m=st.score_crossing(timeline(50,make),gap_s=1.)
    assert m['fraction_two_people']==pytest.approx(.8) and m['fraction_at_least_two']==pytest.approx(.82)
    assert m['max_simultaneous']==3 and m['distinct_person_ids']==4 and m['id_switches']==2 and m['fragments']==0


def test_radial_range_needs_min_frames_and_reports_the_last_continuous_detection():
    # Track 1 walks out from 1.0 m to 3.9 m (frames 0-29); 0.6 s with nobody; track 2 at 5.0-5.2 m for only 3 frames.
    def make(i):
        if i<30:return [trk(1,1+.1*i,0)]
        return [trk(2,5+.1*(i-35),0)] if 35<=i<38 else []
    frames=timeline(60,make)
    m=st.score_radial(frames,origin=(0.,0.),min_frames=5,gap_s=1.)
    assert m['max_range_m']==pytest.approx(3.5) and m['max_range_track_id']==1  # the farthest 5-frame window: 3.5-3.9 m
    assert m['last_continuous_range_m']==pytest.approx(5.2)  # one run (the 0.6 s gap is under --gap-s) ending at 5.2 m
    assert st.score_radial(frames,origin=(0.,0.),min_frames=5,gap_s=.5)['last_continuous_range_m']==pytest.approx(3.9)
    assert st.score_radial(frames,origin=(0.,0.),min_frames=3,gap_s=1.)['max_range_m']==pytest.approx(5.)
    assert st.score_radial(frames,origin=(1.,0.),min_frames=5,gap_s=1.)['max_range_m']==pytest.approx(2.5)
    assert st.score_radial(timeline(3,lambda i:[trk(1,2,0)]),origin=(0.,0.),min_frames=5,gap_s=1.)['max_range_m'] is None


def read_jsonl(path):return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def check_report(report,tracks_path,mode):
    assert report['mode']==mode and report['scored'] is True
    assert report['honesty']=='Track-level metrics without per-frame ground truth; not MOTA/HOTA.'
    assert 'every sweep' in report['latest_frame_policy'] and 'drops' in report['latest_frame_policy']
    p=report['provenance']
    assert {'recording','commit','calibration','frames_processed','frames_acquired','recording_frames','hardware_verified'}<=set(p)
    assert p['calibration'] in ('measured','placeholder','none') and p['hardware_verified'] is False
    assert p['frames_processed']==p['frames_acquired']>0 and p['recording_frames']>=p['frames_acquired']
    rows=read_jsonl(tracks_path);assert len(rows)==p['frames_processed']
    times=[row['t_s'] for row in rows];assert times==sorted(times) and len(set(times))==len(times)
    for row in rows:
        assert list(row)==['t_s','tracks']
        for track in row['tracks']:
            assert list(track)==['id','class','confidence','x','y','z','speed'] and track['class'] in SemanticClass.__members__
            assert all(np.isfinite(track[k]) for k in ('confidence','x','y','z','speed'))
    m=report['metrics'];ids={t['id'] for row in rows for t in row['tracks']}
    dynamic={t['id'] for row in rows for t in row['tracks'] if t['class'] in st.DYNAMIC_CLASSES}
    assert m['frames']==len(rows) and m['confirmed_tracks']==len(ids) and m['fp_tracks']==len(dynamic)
    return rows


def test_sample_recording_through_the_real_parked_pipeline(tmp_path):
    import torch
    torch.set_num_threads(2)
    out=tmp_path/'report.json';tracks=tmp_path/'tracks.jsonl'
    report=st.main(['--recording',str(SAMPLE),'--twin',str(SAMPLE/'twin'),'--mode','empty','--config',str(ROOT/'config/system_config.yaml'),
        '--seconds','3','--device','cpu','--output',str(out),'--tracks-out',str(tracks)])
    assert json.loads(out.read_text(encoding='utf-8'))==report
    check_report(report,tracks,'empty')
    p=report['provenance'];assert p['recording']=='sample_recording' and p['calibration']=='placeholder'
    assert p['synthetic'] is True  # the sample is simulator data and the report says so


@pytest.mark.skipif(not (REAL_REC/'manifest.json').is_file() or not (REAL_TWIN).is_dir(),reason='real bench recording or LiDAR-only twin absent')
def test_real_empty_bench_room_false_positive_baseline(tmp_path):
    import torch
    device='cuda' if torch.cuda.is_available() else 'cpu'
    out=tmp_path/'report.json';tracks=tmp_path/'tracks.jsonl'
    report=st.main(['--recording',str(REAL_REC),'--twin',str(REAL_TWIN),'--mode','empty','--lidar-only','--lidar-height-assumed',
        '--config',str(ROOT/'config/profiles/hotspot.yaml'),'--seconds','20','--device',device,'--output',str(out),'--tracks-out',str(tracks)])
    check_report(report,tracks,'empty')
    p=report['provenance'];assert p['calibration']=='none' and p['synthetic'] is False and p['frames_processed']>=100
    print(f"\nREAL empty-room baseline ({device}): {json.dumps(report['metrics'])}")

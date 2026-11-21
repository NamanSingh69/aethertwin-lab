"""Landmark span scorer (tools/score_landmarks.py) on synthetic spans: twin points are the truth plus known offsets."""
import json,math
import numpy as np
import pytest
from tools.score_landmarks import HONESTY,main,read_points

# name: (from, to, tape1 mm, tape2 mm); the tape mean is the truth, the twin's 'to' point carries a known error.
SPANS={'door':('A','B',900,900),'wall_x':('C','D',3998,4002),'wall_y':('E','F',2001,1999),'diagonal':('G','H',5000,5000),'window':('I','J',1200,1210)}
POINTS={'A':(0,0,1.5),'B':(.910,0,1.5),'C':(0,0,1.6),'D':(3.995,0,1.6),'E':(0,0,1.8),'F':(0,2.003,1.8),
        'G':(0,0,2.),'H':(3.006,4.008,2.),'I':(0,0,1.5),'J':(1.225,0,1.5)}
ERRORS={'door':10.,'wall_x':-5.,'wall_y':3.,'diagonal':10.,'window':20.}  # twin minus tape mean, mm


def write_spans(path,spans=SPANS,note=True):
    rows=['name,from,to,tape1_mm,tape2_mm'+(',note' if note else '')]
    rows+=[f'{n},{a},{b},{t1},{t2}'+(f',taped at {n}' if note else '') for n,(a,b,t1,t2) in spans.items()]
    path.write_text('\n'.join(rows)+'\n',encoding='utf-8');return path


def write_points_csv(path,points=POINTS):
    path.write_text('name,x,y,z\n'+''.join(f'{n},{x},{y},{z}\n' for n,(x,y,z) in points.items()),encoding='utf-8');return path


def run(tmp_path,*extra,spans=SPANS,points=POINTS):
    out=tmp_path/'report.json';md=tmp_path/'report.md'
    code=main(['--spans',str(write_spans(tmp_path/'spans.csv',spans)),'--points',str(write_points_csv(tmp_path/'points.csv',points)),
               '--output',str(out),'--markdown',str(md),*extra])
    return code,(json.loads(out.read_text(encoding='utf-8')) if out.exists() else None),(md.read_text(encoding='utf-8') if md.exists() else None)


def test_statistics_match_known_offsets(tmp_path):
    code,report,_=run(tmp_path)
    assert code==0 and report['complete'] and report['missing_points']==[]
    spans={s['name']:s for s in report['spans']}
    for name,error in ERRORS.items():
        assert spans[name]['signed_error_mm']==pytest.approx(error,abs=1e-6);assert spans[name]['abs_error_mm']==pytest.approx(abs(error),abs=1e-6)
    assert spans['wall_x']['tape_mean_mm']==4000 and spans['wall_x']['tape_diff_mm']==4 and spans['wall_x']['twin_mm']==pytest.approx(3995)
    assert spans['door']['relative_error_pct']==pytest.approx(10/900*100,abs=1e-6)
    # Hand check, all five spans: e = [10,-5,3,10,20]; |e| sorted [3,5,10,10,20].
    s=report['summary']['all']
    assert s['n']==5 and s['bias_mm']==pytest.approx(7.6) and s['mean_abs_mm']==pytest.approx(9.6) and s['max_abs_mm']==pytest.approx(20)
    assert s['rmse_mm']==pytest.approx(math.sqrt(634/5)) and s['p68_abs_mm']==pytest.approx(10) and s['p95_abs_mm']==pytest.approx(18)
    assert s['p95_abs_mm']==pytest.approx(np.percentile([10,5,3,10,20],95))
    assert s['mean_abs_relative_pct']==pytest.approx(np.mean([10/900,5/4000,3/2000,10/5000,20/1205])*100)


def test_tape_pair_beyond_tolerance_is_flagged_and_dropped_only_from_clean(tmp_path):
    code,report,md=run(tmp_path)
    spans={s['name']:s for s in report['spans']}
    assert code==0 and spans['window']['tape_flagged'] and not any(spans[n]['tape_flagged'] for n in ('door','wall_x','wall_y','diagonal'))
    assert report['flagged_spans']==['window'] and report['summary']['all']['n']==5
    clean=report['summary']['clean'];assert clean['n']==4 and clean['bias_mm']==pytest.approx(4.5) and clean['rmse_mm']==pytest.approx(math.sqrt(58.5))
    assert 'window' in md and 'tape pair' in md.lower()
    _,loose,_=run(tmp_path,'--tape-tolerance-mm','10')  # 10 mm apart is within a 10 mm tolerance
    assert loose['flagged_spans']==[] and loose['summary']['clean']['n']==5


def test_statistics_over_three_metres_only(tmp_path):
    _,report,_=run(tmp_path)
    s=report['summary']['over_3m']  # wall_x 4000 mm (-5) and diagonal 5000 mm (+10)
    assert s['n']==2 and s['bias_mm']==pytest.approx(2.5) and s['mean_abs_mm']==pytest.approx(7.5) and s['rmse_mm']==pytest.approx(math.sqrt(62.5))
    assert s['max_abs_mm']==pytest.approx(10) and s['p68_abs_mm']==pytest.approx(8.4) and s['p95_abs_mm']==pytest.approx(9.75)
    assert report['summary']['over_3m_clean']['n']==2
    assert {x['name'] for x in report['spans'] if x['over_3m']}=={'wall_x','diagonal'}


def test_missing_points_are_reported_with_exit_code_2(tmp_path,capsys):
    points={k:v for k,v in POINTS.items() if k not in ('J','D')}
    code,report,md=run(tmp_path,points=points)
    assert code==2 and not report['complete'] and report['missing_points']==['D','J']
    spans={s['name']:s for s in report['spans']}
    assert spans['window']['status']=='missing_point' and spans['window']['twin_mm'] is None and report['summary']['all']['n']==3
    assert 'D' in capsys.readouterr().err and md.count('| window |')==1
    assert main(['--spans',str(tmp_path/'spans.csv'),'--points',str(tmp_path/'nope.csv')])==2


def test_csv_json_and_cloudcompare_point_files_parse(tmp_path):
    csv=read_points(write_points_csv(tmp_path/'p.csv'))
    (tmp_path/'p.json').write_text(json.dumps({k:list(v) for k,v in POINTS.items()}),encoding='utf-8');js=read_points(tmp_path/'p.json')
    # CloudCompare point-list ASCII exports: "label name,x,y,z" (default labels "Point #N"), "index,x,y,z" and "x,y,z".
    (tmp_path/'cc_label.txt').write_text(''.join(f'{n},{x:.12f},{y:.12f},{z:.12f}\n' for n,(x,y,z) in POINTS.items()),encoding='utf-8')
    (tmp_path/'cc_default.txt').write_text('Point #0,0.5,1.25,1.75\nPoint #1,1.5,1.25,1.75\n',encoding='utf-8')
    (tmp_path/'cc_index.txt').write_text('0,0.5,1.25,1.75\n1,1.5,1.25,1.75\n',encoding='utf-8')
    (tmp_path/'cc_xyz.txt').write_text('0.5,1.25,1.75\n1.5,1.25,1.75\n',encoding='utf-8')
    for parsed in (csv,js,read_points(tmp_path/'cc_label.txt')):
        assert set(parsed)==set(POINTS) and all(np.allclose(parsed[n],POINTS[n]) for n in POINTS)
    assert set(read_points(tmp_path/'cc_default.txt'))=={'Point #0','Point #1'}
    assert set(read_points(tmp_path/'cc_index.txt'))=={'0','1'}=={*read_points(tmp_path/'cc_xyz.txt')}
    assert np.allclose(read_points(tmp_path/'cc_xyz.txt')['1'],[1.5,1.25,1.75])
    for kind in ('p.json','cc_label.txt'):
        out=tmp_path/f'{kind}.report.json'
        assert main(['--spans',str(write_spans(tmp_path/'spans.csv')),'--points',str(tmp_path/kind),'--output',str(out)])==0
        assert json.loads(out.read_text(encoding='utf-8'))['summary']['all']['rmse_mm']==pytest.approx(math.sqrt(634/5))


def test_markdown_has_one_row_per_span_and_the_honesty_block(tmp_path):
    _,report,md=run(tmp_path)
    for name in SPANS:assert sum(line.startswith(f'| {name} |') for line in md.splitlines())==1
    assert HONESTY in md and report['honesty']==HONESTY
    assert 'Span lengths compare the twin' in HONESTY and 'Not a full-map accuracy survey.' in HONESTY


@pytest.mark.parametrize('bad,column',[('9OO','tape1_mm'),('-900','tape1_mm'),('','tape2_mm'),('nan','tape2_mm'),('0','tape1_mm')])
def test_bad_tape_values_are_rejected_with_a_clear_error(tmp_path,capsys,bad,column):
    spans=dict(SPANS);a,b,t1,t2=spans['door'];spans['door']=(a,b,bad,t2) if column=='tape1_mm' else (a,b,t1,bad)
    code,report,_=run(tmp_path,spans=spans)
    err=capsys.readouterr().err
    assert code==2 and report is None and 'door' in err and column in err


def test_twin_provenance_is_read_only(tmp_path):
    twin=tmp_path/'twin';twin.mkdir()
    (twin/'reconstruction.json').write_text(json.dumps({'floor_fit':{'method':'declared_lidar_height','measured':False,'declared_lidar_height_m':.75},
        'display_mesh_source':'tsdf_preview','commissioning':{'trajectory':{'start_to_end_m':.025,'path_m':14.2}}}),encoding='utf-8')
    (twin/'bundle_manifest.json').write_text(json.dumps({'map_revision':'abc123','hardware_validation':'not_tested','authority_sha256':{'mesh.ply':'f00'},
        'source_provenance':{'any_synthetic':False,'calibrations':[],'all_camera_calibrations_measured':None,'lidar_only_reference_eligible':True}}),encoding='utf-8')
    before={p.name:(p.stat().st_mtime_ns,p.read_bytes()) for p in twin.iterdir()}
    _,report,md=run(tmp_path,'--twin',str(twin))
    t=report['twin']
    assert t['map_revision']=='abc123' and t['camera_calibration_measured'] is None and t['lidar_only'] is True
    assert t['declared_lidar_height_m']==.75 and t['floor_fit_method']=='declared_lidar_height' and t['mesh_sha256']=='f00'
    assert 'abc123' in md and {p.name:(p.stat().st_mtime_ns,p.read_bytes()) for p in twin.iterdir()}==before
    assert main(['--spans',str(tmp_path/'spans.csv'),'--points',str(tmp_path/'points.csv'),'--twin',str(tmp_path/'no_twin')])==2

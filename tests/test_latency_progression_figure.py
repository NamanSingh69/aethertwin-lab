import json,shutil,sys
from pathlib import Path
import cv2,pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import latency_progression_figure as lp

REHEARSAL=ROOT/'reports'/'rehearsal'
FILES=['live_parked_replay_30min.json','live_parked_after_c30_c32_10min.json','live_parked_after_c37_c39_10min.json',
       'live_parked_fast_replay_30min.json','live_lidar_only_replay_10min.json']


def _index(out):(out/'INDEX.md').write_text('# Submission figures\n\n## fig1.png\n\nx\n\n## fig6_live_rehearsal.png\n\ny\n',encoding='utf-8')


def test_reads_the_five_summaries_in_order_and_writes_a_png_of_the_expected_size(tmp_path):
    runs=lp.load(REHEARSAL);assert [r['file'] for r in runs]==FILES
    assert [r['kind'] for r in runs]==['plain','plain','plain','fast','lidar_only']
    out=tmp_path/'figures';out.mkdir();_index(out);assert lp.main(['--rehearsal',str(REHEARSAL),'--output',str(out)])==0
    img=cv2.imread(str(out/lp.NAME));assert img is not None and img.shape==(lp.H,lp.W,3) and (out/lp.NAME).stat().st_size>10000


def test_caption_numbers_equal_the_json_values():
    runs=lp.load(REHEARSAL);text=lp.caption(runs)
    for f in FILES:
        s=json.loads((REHEARSAL/f).read_text(encoding='utf-8'));pm=s['processing_ms']
        assert f"p50 {pm['p50']:.0f} ms / p95 {pm['p95']:.0f} ms" in text and s['commit'] in text and f'`{f}`' in text
        if f in FILES[:3]:
            m=s['module_ms_median'];assert f"localization {m['localization']:.0f}, occupancy {m['occupancy']:.0f}, perception and tracking {m['perception_tracking']:.0f} ms" in text
    assert 'not clean: other jobs ran' in text and f"{pm['target_ms']:.0f} ms" in text and '**What:**' in text and '**Does NOT show:**' in text


def test_caption_follows_the_summaries_not_hand_typed_numbers(tmp_path):
    src=tmp_path/'rehearsal';shutil.copytree(REHEARSAL,src)
    p=src/FILES[2];s=json.loads(p.read_text(encoding='utf-8'));s['processing_ms']['p50']=123.4;s['commit']='feed123';p.write_text(json.dumps(s),encoding='utf-8')
    text=lp.caption(lp.load(src));assert 'p50 123 ms' in text and 'feed123' in text


def test_index_entry_is_replaced_not_duplicated_on_a_rerun(tmp_path):
    out=tmp_path/'figures';out.mkdir();_index(out)
    for _ in range(2):assert lp.main(['--rehearsal',str(REHEARSAL),'--output',str(out)])==0
    index=(out/'INDEX.md').read_text(encoding='utf-8')
    assert index.count(f'## {lp.NAME}')==1 and '## fig1.png' in index and '## fig6_live_rehearsal.png' in index and '\ny\n' in index
    # an entry that is not the last one keeps the sections after it
    (out/'INDEX.md').write_text(index+'\n## fig8_later.png\n\nz\n',encoding='utf-8');assert lp.main(['--rehearsal',str(REHEARSAL),'--output',str(out)])==0
    index=(out/'INDEX.md').read_text(encoding='utf-8');assert index.count(f'## {lp.NAME}')==1 and '## fig8_later.png' in index and index.count('**What:**')==1


def test_a_missing_summary_gives_a_clear_error(tmp_path):
    src=tmp_path/'rehearsal';shutil.copytree(REHEARSAL,src);(src/FILES[2]).unlink()
    with pytest.raises(SystemExit,match='missing rehearsal summary.*live_parked_after_c37_c39_10min.json'):lp.load(src)
    out=tmp_path/'figures';out.mkdir()
    with pytest.raises(SystemExit,match='live_parked_after_c37_c39_10min.json'):lp.main(['--rehearsal',str(src),'--output',str(out)])
    assert not (out/lp.NAME).exists()

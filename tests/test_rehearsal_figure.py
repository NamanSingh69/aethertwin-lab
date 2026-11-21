import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import rehearsal_figure as rf


def test_figure_and_caption_from_a_rehearsal_folder_are_written_once(tmp_path):
    run=tmp_path/'run';run.mkdir();out=tmp_path/'figures';out.mkdir();(out/'INDEX.md').write_text('# Submission figures\n\n## fig1.png\n\nx\n')
    rows=[{'t':10.*i,'rss_mb':1500+i,'state':{'frame_id':i,'diagnostics':{'processing_ms':300.+i,'raw_transport':{'bytes':1e8*i}}}} for i in range(20)]
    (run/'monitor.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    (run/'summary.json').write_text(json.dumps({'verdict':'ran the full duration without a runtime error','monitored_span_s':190.,'commit':'abc123',
        'recording':'C:/x/wifi_stationary_60s','processing_ms':{'p50':310.,'p95':318.},'rss_mb':{'first':1500,'last':1519,'slope_second_half_mb_per_min':.1},
        'transport':{'bytes':1.9e9,'segments':1,'clock_jumps':0},'parked_localization':{'fitness':.97,'rmse_m':.026}}))
    for _ in range(2):assert rf.main([str(run),'--output',str(out)])==0
    index=(out/'INDEX.md').read_text()
    assert (out/rf.NAME).stat().st_size>10000 and index.count(f'## {rf.NAME}')==1 and '## fig1.png' in index
    assert 'Does NOT show' in index and 'not met' in index and 'abc123' in index


def test_rerunning_fig6_keeps_the_sections_after_it(tmp_path):
    # C44: fig7's INDEX.md section follows fig6; regenerating fig6 used to truncate everything after its heading.
    run=tmp_path/'run';run.mkdir();out=tmp_path/'figures';out.mkdir()
    rows=[{'t':10.*i,'rss_mb':1500+i,'state':{'frame_id':i,'diagnostics':{'processing_ms':300.+i,'raw_transport':{'bytes':1e8*i}}}} for i in range(20)]
    (run/'monitor.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    (run/'summary.json').write_text(json.dumps({'verdict':'ran the full duration without a runtime error','monitored_span_s':190.,'commit':'abc123',
        'recording':'C:/x/wifi_stationary_60s','processing_ms':{'p50':310.,'p95':318.},'rss_mb':{'first':1500,'last':1519,'slope_second_half_mb_per_min':.1},
        'transport':{'bytes':1.9e9,'segments':1,'clock_jumps':0},'parked_localization':{'fitness':.97,'rmse_m':.026}}))
    (out/'INDEX.md').write_text(f'# Submission figures\n\n## fig1.png\n\nx\n\n## {rf.NAME}\n\nold fig6\n\n## fig7_latency_progression.png\n\nkeep me\n')
    assert rf.main([str(run),'--output',str(out)])==0
    index=(out/'INDEX.md').read_text()
    assert index.count(f'## {rf.NAME}')==1 and 'old fig6' not in index and '## fig7_latency_progression.png' in index and 'keep me' in index

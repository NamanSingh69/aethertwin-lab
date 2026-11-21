"""Submission figure for a live rehearsal run by tools/rehearse_live.py (a REHEARSAL, never a hardware result).

    python tools/rehearsal_figure.py %USERPROFILE%/aethertwin_rehearsals/<run> --output reports/submission/figures

Draws processing time per frame, service memory and transport volume over the run from <run>/monitor.jsonl and
<run>/summary.json, and appends its caption to the folder's INDEX.md (run it after tools/submission_figures.py,
which rewrites INDEX.md).
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from submission_figures import Figure,Axes,SERIES,INK2,MUTED

NAME='fig6_live_rehearsal.png'


def load(run):
    rows=[json.loads(l) for l in (run/'monitor.jsonl').read_text().splitlines() if l.strip()]
    rows=[r for r in rows if isinstance(r.get('state'),dict) and 'frame_id' in r['state']]
    if len(rows)<2:raise SystemExit(f'{run}: fewer than two monitored frames')
    summary=json.loads((run/'summary.json').read_text()) if (run/'summary.json').exists() else {}
    d=lambda r:r['state'].get('diagnostics',{})
    t=np.array([r['t'] for r in rows],float)
    proc=np.array([d(r).get('processing_ms',np.nan) or np.nan for r in rows],float)
    rss=np.array([r.get('rss_mb') or np.nan for r in rows],float)
    gb=np.array([(d(r).get('raw_transport',{}) or {}).get('bytes',np.nan) or np.nan for r in rows],float)/1e9
    return t,proc,rss,gb,summary


def draw(run,out):
    t,proc,rss,gb,s=load(run);W=1680;ph=170;gap=86;fig=Figure(W,130+3*(ph+gap)+50)
    minutes=t[-1]/60;verdict=s.get('verdict','')
    fig.header(f'Live run rehearsal: {minutes:.0f} minutes of the parked live service on recorded real sensor data',
        'tools/rehearse_live.py: a real bench recording served over the real raw-sensor transport on loopback, the real command_center --mode live --parked on the GPU laptop.')
    panels=[('ms','Processing time per frame (newest sweep each time; orange: the 35 ms target)',proc,(0,max(np.nanmax(proc)*1.15,60))),
            ('MB','Service memory (resident set of the live process)',rss,(0,np.nanmax(rss)*1.2)),
            ('GB','Data received over the transport (cumulative)',gb,(0,max(np.nanmax(gb)*1.12,.1)))]
    pm=s.get('processing_ms') or {};mem=s.get('rss_mb') or {};tr=s.get('transport') or {};pl=s.get('parked_localization') or {}
    notes=[[f"p50 {pm.get('p50',np.nan):.0f} ms",f"p95 {pm.get('p95',np.nan):.0f} ms"],
           [f"start {mem.get('first',np.nan):.0f} MB, end {mem.get('last',np.nan):.0f} MB",f"slope {mem.get('slope_second_half_mb_per_min') or 0:+.2f} MB/min (2nd half)"],
           [f"{(tr.get('bytes') or 0)/1e9:.2f} GB in {tr.get('segments')} segment(s)",f"{tr.get('clock_jumps')} clock jumps"]]
    for k,(unit,title,v,ylim) in enumerate(panels):
        Y0=130+k*(ph+gap)+30;ax=Axes(fig,110,Y0,W-110-400,ph,(0,t[-1]*1.02),ylim)
        ax.decorate('time since monitoring started [s]' if k==2 else '',unit,title);ok=np.isfinite(v);ax.plot(t[ok],v[ok],SERIES[0],1.8)
        if k==0:ax.plot(np.array([0,t[-1]*1.02]),np.array([35,35]),SERIES[1],1.2)
        for j,line in enumerate(notes[k]):fig.text(W-370,Y0+20+18*j,line,12,INK2)
    fig.text(W-370,130+30+ph-30,f"relocalized: fitness {pl.get('fitness',np.nan):.3f}, RMSE {100*(pl.get('rmse_m') or np.nan):.1f} cm",11,MUTED)
    fig.footer(f"{verdict[:1].upper()+verdict[1:]}. REHEARSAL on recorded data (loopback, placeholder calibration, assumed LiDAR height): not Wi-Fi, not the Pi, not a hardware result. Commit {s.get('commit','?')}.")
    return fig.save(out/NAME)


def caption(run,s):
    pm=s.get('processing_ms') or {};mem=s.get('rss_mb') or {};tr=s.get('transport') or {}
    return (f"\n## {NAME}\n\n**What:** the parked live service run by `tools/rehearse_live.py` for {s.get('monitored_span_s',0)/60:.0f} minutes "
        f"on the real bench recording `{Path(str(s.get('recording',''))).name}` replayed over the real raw-sensor transport on loopback "
        f"(commit {s.get('commit','?')}): {s.get('verdict','')}; processing p50 {pm.get('p50',float('nan')):.0f} ms / p95 {pm.get('p95',float('nan')):.0f} ms "
        f"per frame against the 35 ms target (not met); memory {mem.get('first',0):.0f} -> {mem.get('last',0):.0f} MB; "
        f"{(tr.get('bytes') or 0)/1e9:.2f} GB over {tr.get('segments')} transport segment(s). Source: `{run.name}/summary.json`.\n\n"
        "**Does NOT show:** Wi-Fi delay or loss, the Pi, a measured camera-LiDAR calibration (a placeholder was used), a "
        "commissioned twin of the lab (built from the same bench recording with an assumed LiDAR height), or moving people: "
        "it shows that the software path runs the full duration.\n")


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('run',type=Path,help='rehearsal folder (monitor.jsonl, summary.json)');p.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'reports/submission/figures')
    a=p.parse_args(argv);a.output.mkdir(parents=True,exist_ok=True);path=draw(a.run,a.output)
    s=json.loads((a.run/'summary.json').read_text()) if (a.run/'summary.json').exists() else {}
    index=a.output/'INDEX.md';text=index.read_text(encoding='utf-8') if index.exists() else '# Submission figures\n'
    # Replace only this figure's own section (up to the next "## " heading); later sections such as fig7 stay.
    head=f'\n## {NAME}';entry=caption(a.run,s).strip('\n')+'\n'
    if head in text:
        i=text.index(head);j=text.find('\n## ',i+1);rest=text[j:] if j!=-1 else ''
        text=text[:i].rstrip('\n')+'\n\n'+entry+rest
    else:text=text.rstrip('\n')+'\n\n'+entry
    index.write_bytes(text.encode('utf-8'));print('wrote',path)
    return 0


if __name__=='__main__':raise SystemExit(main())

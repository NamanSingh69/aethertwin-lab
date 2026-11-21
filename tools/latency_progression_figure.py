"""Submission figure: processing time per frame across the live-run REHEARSALS (recorded data on loopback, never hardware).

    python tools/latency_progression_figure.py --rehearsal reports/rehearsal --output reports/submission/figures

Reads the five rehearsal summaries named in RUNS (tools/rehearse_live.py output, kept in reports/rehearsal/) and draws
(1) p50 / p95 processing time per frame for the three plain-profile runs in order, plus the opt-in fast profile and the
LiDAR-only fallback as separate comparators, against the 35 ms target; (2) the per-stage medians of the plain runs.
Every number in the figure and its caption is read from the summaries. Replaces (or appends) its own INDEX.md entry
and leaves the other entries alone.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from submission_figures import Figure,Axes,SERIES,INK,INK2,MUTED,AXIS,GRID,SURFACE,SS,_bgr,text_width

NAME='fig7_latency_progression.png';W=1680;H=1040
ROOT=Path(__file__).resolve().parents[1]
# (file, short label, kind, note): order is the plot order; numbers never live here, only in the summaries.
RUNS=(('live_parked_replay_30min.json','1. first run','plain',''),
      ('live_parked_after_c30_c32_10min.json','2. after C30-C32','plain',''),
      ('live_parked_after_c37_c39_10min.json','3. after C37-C39','plain',''),
      ('live_parked_fast_replay_30min.json','fast profile (opt-in)','fast','plus the then-uncommitted fast profile'),
      ('live_lidar_only_replay_10min.json','LiDAR only (fallback)','lidar_only','plus the then-uncommitted LiDAR-only fallback'))
UNCLEAN_P95={'lidar_only':'not clean: other jobs ran'}  # reports/rehearsal/README.md: test suite and a browser session ran
STAGES=(('localization','localization'),('occupancy','occupancy'),('perception_tracking','perception and tracking'))
P50=SERIES[0];P95=_bgr('#9ec5f4');TARGET=SERIES[1];RUNC=[_bgr(h) for h in ('#9ec5f4','#3987e5','#104281')];HATCH=_bgr('#b5b3ab')


def load(folder):
    folder=Path(folder);missing=[str(folder/f) for f,*_ in RUNS if not (folder/f).is_file()]
    if missing:raise SystemExit(f"missing rehearsal summary: {', '.join(missing)} (fig7 needs all of: {', '.join(f for f,*_ in RUNS)})")
    runs=[]
    for f,label,kind,note in RUNS:
        s=json.loads((folder/f).read_text(encoding='utf-8'));pm=s.get('processing_ms') or {};m=s.get('module_ms_median') or {}
        need=[k for k in ('p50','p95','target_ms') if not isinstance(pm.get(k),(int,float))]+[k for k in ('commit','duration_s') if not s.get(k)]
        need+=[f'module_ms_median.{k}' for k,_ in STAGES if kind=='plain' and not isinstance(m.get(k),(int,float))]
        if need:raise SystemExit(f'{folder/f}: summary lacks {", ".join(need)}')
        runs.append({'file':f,'label':label,'kind':kind,'note':note,'p50':float(pm['p50']),'p95':float(pm['p95']),'target':float(pm['target_ms']),
                     'commit':str(s['commit']),'minutes':float(s['duration_s'])/60,'stages':{k:float(m[k]) for k,_ in STAGES if k in m},
                     'minor':{k:float(v) for k,v in m.items() if k not in dict(STAGES) and isinstance(v,(int,float))}})
    if len({r['target'] for r in runs})!=1:raise SystemExit('the summaries disagree on processing_ms.target_ms')
    return runs


def _hatched(fig,x,y,w,h,color):
    X,Y,Wp,Hp=int(round(x*SS)),int(round(y*SS)),max(1,int(round(w*SS))),max(1,int(round(h*SS)))
    i,j=np.mgrid[0:Hp,0:Wp];tile=np.empty((Hp,Wp,3),np.uint8);tile[:]=SURFACE;tile[((i+j)%(9*SS))<2*SS]=color
    fig.paste(tile,X,Y);fig.rect(x,y,w,h,color,fill=False,width=1)


def _wrap(s,width,size=11):
    lines=[''];
    for word in s.split(' '):
        t=(lines[-1]+' '+word).strip()
        if text_width(t,size)<=width or not lines[-1]:lines[-1]=t
        else:lines.append(word)
    return lines


def _mins(runs):return ' vs '.join(f'{m:.0f}' for m in sorted({round(r['minutes']) for r in runs},reverse=True))


def draw(runs,out):
    fig=Figure(W,H);plain=[r for r in runs if r['kind']=='plain'];target=runs[0]['target']
    fig.header('Live-run rehearsals: processing time per frame, before and after the exact speed-ups',
        'tools/rehearse_live.py on one recorded real bench session over the raw-sensor transport on loopback; the real command_center --mode live --parked on the GPU laptop.')
    # ---- panel 1: p50 / p95 per run
    slots=[0,1,2,3.55,4.55];Y1=150;ph=300;top=max(r['p95'] for r in runs)*1.32
    ax=Axes(fig,110,Y1,W-110-60,ph,(-.6,5.15),(0,top));ax.decorate('','ms per frame',
        f'Processing time per frame: p50 (dark) and p95 (light); orange line: the {target:.0f} ms target',xticks=slots,xfmt=lambda v:'')
    for x,r in zip(slots,runs):
        for k,(v,c) in enumerate(((r['p50'],P50),(r['p95'],P95))):
            x0=float(ax.px(x-.36+k*.37));x1=float(ax.px(x-.36+k*.37+.33));y0=float(ax.py(v));base=Y1+ph
            if k==1 and r['kind'] in UNCLEAN_P95:
                _hatched(fig,x0,y0,x1-x0,base-y0,HATCH);fig.text((x0+x1)/2,y0-24,UNCLEAN_P95[r['kind']],11,INK2,ha='c',bold=True)
                fig.text((x0+x1)/2,y0-8,f'p95 {v:.0f}',11,INK2,ha='c')
            else:fig.rect(x0,y0,x1-x0,base-y0,c);fig.text((x0+x1)/2,y0-8,f'{"p50" if k==0 else "p95"} {v:.0f}',11,INK2,ha='c')
        xx=float(ax.px(x));fig.text(xx,Y1+ph+22,r['label'],12,INK,ha='c',bold=True)
        fig.text(xx,Y1+ph+40,f"{r['minutes']:.0f} min, commit {r['commit']}"+('*' if r['note'] else ''),11,INK2,ha='c')
    ax.plot(np.array([-.6,5.15]),np.array([target,target]),TARGET,1.4);fig.text(float(ax.px(2.95)),float(ax.py(target))-6,f'{target:.0f} ms target',11,TARGET,ha='r')
    ax.vline(3.05,AXIS);fig.text(float(ax.px(1)),Y1+16,'plain hotspot.yaml profile, in order',12,INK2,ha='c')
    fig.text(float(ax.px(4.05)),Y1+16,'comparators, not steps of the progression',12,INK2,ha='c')
    # ---- panel 2: per-stage medians of the plain runs
    Y2=Y1+ph+130;ph2=280;top2=max(v for r in plain for v in r['stages'].values())*1.22
    ax2=Axes(fig,110,Y2,W-110-420,ph2,(-.6,2.6),(0,top2));ax2.decorate('','ms (median)','Per-stage median time per frame, plain-profile runs 1-3',xticks=[0,1,2],xfmt=lambda v:'')
    for g,(key,name) in enumerate(STAGES):
        for k,(r,c) in enumerate(zip(plain,RUNC)):
            x0=float(ax2.px(g-.39+k*.27));x1=float(ax2.px(g-.39+k*.27+.24));v=r['stages'][key];y0=float(ax2.py(v))
            fig.rect(x0,y0,x1-x0,Y2+ph2-y0,c);fig.text((x0+x1)/2,y0-8,f'{v:.0f}',11,INK2,ha='c')
        fig.text(float(ax2.px(g)),Y2+ph2+22,name,12,INK,ha='c',bold=True)
    fig.legend(W-380,Y2+20,[(f"{r['label']} ({r['minutes']:.0f} min, {r['commit']})",c) for r,c in zip(plain,RUNC)])
    minor=max((v for r in plain for v in r['minor'].values()),default=0.)
    for j,s in enumerate(_wrap(f"Not drawn: {', '.join(sorted({k.replace('_',' ') for r in plain for k in r['minor']}))} (at most {minor:.1f} ms median).",350,11)
                         +_wrap('Stages are medians of their own; they do not add up to the p50 per frame.',350,11)):fig.text(W-380,Y2+100+17*j,s,11,MUTED)
    # ---- footer
    notes=[f"* {r['label'].split(' (')[0]}: {r['commit']} {r['note']}." for r in runs if r['note']]
    foot=(f"REHEARSALS on recorded real data over loopback (placeholder calibration, none in the LiDAR-only run; assumed LiDAR height): not hardware results, not Wi-Fi, not the Pi. "
          f"Durations differ ({_mins(runs)} minutes), so the p95 values cover different spans. Commit per run (summary 'commit' field): "
          +'; '.join(f"{r['label'].split(' (')[0]} {r['commit']}" for r in runs)+'. '+' '.join(notes)+' '
          "Between the plain runs only exact speed-ups (identical outputs) were applied; the fast-profile and LiDAR-only runs use another configuration. "
          "Machine load differed between runs (reports/rehearsal/README.md).")
    lines=_wrap(foot,W-56,11);y=H-14-17*(len(lines)-1);fig.line(28,y-22,W-28,y-22,GRID)
    for k,s in enumerate(lines):fig.text(28,y+17*k,s,11,MUTED)
    return fig.save(out/NAME)


def caption(runs):
    plain=[r for r in runs if r['kind']=='plain'];comp=[r for r in runs if r['kind']!='plain'];target=runs[0]['target']
    run=lambda r:f"`{r['file']}` ({r['minutes']:.0f} min, commit {r['commit']}{' '+r['note'] if r['note'] else ''}) p50 {r['p50']:.0f} ms / p95 {r['p95']:.0f} ms"+(f" (p95 {UNCLEAN_P95[r['kind']]} during part of the run)" if r['kind'] in UNCLEAN_P95 else '')
    stage=lambda r:f"{r['label']}: localization {r['stages']['localization']:.0f}, occupancy {r['stages']['occupancy']:.0f}, perception and tracking {r['stages']['perception_tracking']:.0f} ms"
    met='not met by any run' if all(r['p50']>target for r in runs) else 'met by '+', '.join(r['label'] for r in runs if r['p50']<=target)+' at p50'
    return (f"\n## {NAME}\n\n**What:** processing time per frame (p50 / p95) of the parked live service in five rehearsals by "
        "`tools/rehearse_live.py`, each on the same real bench recording replayed over the real raw-sensor transport on loopback. "
        "Plain `hotspot.yaml` profile, in order: "+'; '.join(run(r) for r in plain)+". Per-stage medians of these three runs: "
        +'; '.join(stage(r) for r in plain)+". Comparators, not steps of the progression: "+'; '.join(run(r) for r in comp)
        +f". The {target:.0f} ms target is {met}. Between the plain runs only exact speed-ups (identical outputs) were applied. "
        "Source: the summaries in `reports/rehearsal/` (context in its README.md).\n\n"
        f"**Does NOT show:** hardware results (loopback, not Wi-Fi or the Pi; a placeholder calibration, none in the LiDAR-only run; a twin built from the same recording "
        f"with an assumed LiDAR height), runs of equal length ({_mins(runs)} minutes, so the p95 values cover different spans), a controlled "
        "machine load (other work ran during some runs, see the README), the processing of every sweep (the live loop takes the newest one), "
        f"or whether the {target:.0f} ms target can be reached.\n")


def write_index(out,text_caption):
    index=out/'INDEX.md';text=index.read_text(encoding='utf-8') if index.exists() else '# Submission figures\n'
    head=f'\n## {NAME}\n'
    if head in text:
        i=text.index(head);j=text.find('\n## ',i+len(head));after='' if j<0 else text[j:]
        text=text[:i].rstrip('\n')+'\n'+text_caption.rstrip('\n')+'\n'+('\n'+after.lstrip('\n') if after else '')
    else:text=text.rstrip('\n')+'\n'+text_caption
    index.write_bytes(text.encode('utf-8'))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--rehearsal',type=Path,default=ROOT/'reports/rehearsal',help='folder with the rehearsal summaries named in RUNS')
    p.add_argument('--output',type=Path,default=ROOT/'reports/submission/figures')
    a=p.parse_args(argv);runs=load(a.rehearsal);a.output.mkdir(parents=True,exist_ok=True);path=draw(runs,a.output)
    write_index(a.output,caption(runs));print('wrote',path)
    return 0


if __name__=='__main__':raise SystemExit(main())

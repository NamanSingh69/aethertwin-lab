"""Compare identical-recording replay outputs with explicit numeric tolerances."""
import argparse
import json
from pathlib import Path
import numpy as np


def compare(left,right,output,rtol=1e-5,atol=2e-5,allow_source_mode_difference=False):
    left,right=Path(left),Path(right);failures=[];maximum={}
    def check(a,b,name):
        if isinstance(a,dict) and isinstance(b,dict):
            if a.keys()!=b.keys():failures.append(name+': keys differ');return
            for key in a:check(a[key],b[key],name+'.'+key)
        elif isinstance(a,(list,tuple)) and isinstance(b,(list,tuple)):
            if len(a)!=len(b):failures.append(name+': lengths differ');return
            try:
                x,y=np.asarray(a,dtype=float),np.asarray(b,dtype=float)
                if x.shape!=y.shape:failures.append(name+': shapes differ');return
                if x.size:maximum[name]=float(np.max(np.abs(x-y)))
                if not np.allclose(x,y,rtol=rtol,atol=atol,equal_nan=False):failures.append(name+': numeric tolerance exceeded')
            except (ValueError,TypeError):
                for index,(x,y) in enumerate(zip(a,b)):check(x,y,f'{name}[{index}]')
        elif isinstance(a,(int,float)) and isinstance(b,(int,float)):
            if not np.isclose(a,b,rtol=rtol,atol=atol):failures.append(name+': numeric tolerance exceeded')
        elif a!=b:failures.append(name+': values differ')
    a=json.loads((left/'last_state.json').read_text(encoding='utf-8'))
    b=json.loads((right/'last_state.json').read_text(encoding='utf-8'))
    for key in a:
        if key=='source_mode' and allow_source_mode_difference:continue
        if key not in ('diagnostics','fps','processed_at_unix','halo'):check(a[key],b.get(key),key)
    for key in ('robot_position','robot_velocity'):check(a['diagnostics'][key],b['diagnostics'][key],'diagnostics.'+key)
    exact_occupancy=True;cells=None
    with np.load(left/'occupancy.npz',allow_pickle=False) as x,np.load(right/'occupancy.npz',allow_pickle=False) as y:
        cells=len(x['coords'])
        ix=np.lexsort(x['coords'].T[::-1]);iy=np.lexsort(y['coords'].T[::-1])
        for key in x.files:
            xx,yy=(x[key][ix],y[key][iy]) if key in ('coords','values') else (x[key],y[key])
            exact_occupancy&=np.array_equal(xx,yy)
            check(xx.tolist(),yy.tolist(),'occupancy.'+key)
    with np.load(left/'traffic_heatmap.npz',allow_pickle=False) as x,np.load(right/'traffic_heatmap.npz',allow_pickle=False) as y:
        for key in x.files:check(x[key].tolist(),y[key].tolist(),'saved_traffic.'+key)
    report={'schema_version':1,'left':str(left),'right':str(right),'frame_id':a['frame_id'],
        'rtol':rtol,'atol':atol,'excluded':['wall timing','memory diagnostics','processing fps','module timing',
            'processed_at_unix (wall clock)','halo (wall-clock age and latency-derived stale display)'],
        'occupancy_cells':cells,'occupancy_bit_exact':bool(exact_occupancy),
        'source_modes':{'left':a.get('source_mode'),'right':b.get('source_mode')},
        'source_mode_difference_explicitly_allowed':bool(allow_source_mode_difference),
        'max_absolute_differences':maximum,'failures':failures,'passed':not failures}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('frame_id','occupancy_cells','occupancy_bit_exact','failures','passed')},indent=2))
    return not failures


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('left',type=Path);parser.add_argument('right',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--allow-source-mode-difference',action='store_true',help='Explicitly compare simulated observations with their recorded replay')
    args=parser.parse_args();raise SystemExit(0 if compare(args.left,args.right,args.output,
        allow_source_mode_difference=args.allow_source_mode_difference) else 1)

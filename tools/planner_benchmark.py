"""Route quality and planning time over random start/goal pairs on a measured planning grid.

Usage: python tools/planner_benchmark.py [--grid assets/twin_seed7/planning_grid.npz] [--pairs 60]
       [--risk] [--out reports/planner_benchmark.json]

Each pair is planned once on a fresh planner with a fixed seed, so two code versions can be
compared pair by pair. Synthetic map: route quality here is not a physical result.
"""
import argparse,json,sys,time
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tracking_and_navigation.safe_corridor_planner import PlannerGrid,SafeCorridorPlanner


def free_points(planner,rng,count):
    free=np.argwhere(planner.clearance>planner.radius+planner.grid.resolution)
    if planner.grid.shape[2]>1:free=free[free[:,2]==np.median(free[:,2]).astype(int)]
    return planner.grid.center(free[rng.choice(len(free),count,replace=len(free)<count)])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--grid',default='assets/twin_seed7/planning_grid.npz')
    parser.add_argument('--pairs',type=int,default=60);parser.add_argument('--seed',type=int,default=5)
    parser.add_argument('--risk',action='store_true');parser.add_argument('--out')
    args=parser.parse_args(argv)
    grid=PlannerGrid.load(ROOT/args.grid);probe=SafeCorridorPlanner(grid);rng=np.random.default_rng(args.seed)
    starts=free_points(probe,rng,args.pairs);goals=free_points(probe,rng,args.pairs)
    risk=rng.random(grid.shape[:2])*.5 if args.risk else None
    rows=[]
    for start,goal in zip(starts,goals):
        planner=SafeCorridorPlanner(grid,seed=0);began=time.perf_counter()
        result=planner.plan(start,goal,risk=risk,ground=True)
        elapsed=1000*(time.perf_counter()-began);row={'ms':elapsed,'status':result.status}
        if result.status=='ok':
            points=result.points;coords=(points-grid.origin)/grid.resolution-.5
            from scipy.ndimage import map_coordinates
            clearance=map_coordinates(planner.clearance,coords.T,order=1,mode='constant',cval=-1.)
            row.update(cost=float(planner._route_cost(points,risk)),length=float(np.linalg.norm(np.diff(points,axis=0),axis=1).sum()),
                min_clearance=float(clearance.min()),spline=bool(planner.last_diagnostics.get('spline_accepted')),
                jerk=float(np.sum(np.diff(points,n=3,axis=0)**2)) if len(points)>3 else 0.)
        rows.append(row)
    ok=[r for r in rows if r['status']=='ok'];ms=np.array([r['ms'] for r in rows])
    summary={'pairs':len(rows),'ok':len(ok),'spline_accepted':sum(r['spline'] for r in ok),
        'ms_p50':float(np.percentile(ms,50)),'ms_p95':float(np.percentile(ms,95)),'ms_total':float(ms.sum()),
        'mean_cost':float(np.mean([r['cost'] for r in ok])) if ok else None,
        'min_clearance':float(min(r['min_clearance'] for r in ok)) if ok else None,
        'grid':args.grid,'risk':args.risk,'seed':args.seed}
    print(json.dumps(summary))
    if args.out:Path(args.out).write_text(json.dumps({'summary':summary,'rows':rows},indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()

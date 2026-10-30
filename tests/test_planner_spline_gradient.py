from pathlib import Path
import numpy as np
import scipy.optimize
import tracking_and_navigation.safe_corridor_planner as module
from tracking_and_navigation.safe_corridor_planner import PlannerGrid,SafeCorridorPlanner

ROOT=Path(__file__).resolve().parents[1]


def test_spline_objective_gradient_matches_finite_differences(monkeypatch):
    grid=PlannerGrid.load(ROOT/'assets/twin_seed7/planning_grid.npz');rng=np.random.default_rng(2)
    risk=rng.random(grid.shape[:2])*.5;checked=[]
    def checking_minimize(fun,x0,jac=None,**kwargs):
        assert jac is True
        for x in (x0,x0+rng.normal(0,grid.resolution*.2,len(x0))):
            _,analytic=fun(x);numeric=scipy.optimize.approx_fprime(x,lambda v:fun(v)[0],1e-6)
            # Piecewise-linear fields: compare where the finite difference is well defined.
            scale=np.maximum(1.,np.abs(numeric));close=np.abs(analytic-numeric)<=2e-2*scale
            assert close.mean()>.95,(analytic,numeric)
        checked.append(len(x0));return scipy.optimize.minimize(fun,x0,jac=jac,**kwargs)
    monkeypatch.setattr(module,'minimize',checking_minimize)
    planner=SafeCorridorPlanner(grid,seed=0)
    free=np.argwhere(planner.clearance>planner.radius+grid.resolution);free=free[free[:,2]==np.median(free[:,2]).astype(int)]
    for _ in range(6):
        start,goal=grid.center(free[rng.choice(len(free),2,replace=False)])
        assert planner.plan(start,goal,risk=risk,ground=True).status in ('ok','failed')
    assert checked

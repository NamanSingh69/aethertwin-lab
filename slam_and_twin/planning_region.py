"""Page a conservative observed planning region without global dense allocation."""
import numpy as np
from .tiled_map import MapBudgetExceeded
from tracking_and_navigation.safe_corridor_planner import PlannerGrid


def load_observed_planning_region(voxel_map,start,goal,planning):
    start=np.asarray(start,dtype=float);goal=np.asarray(goal,dtype=float)
    if start.shape!=(3,) or goal.shape!=(3,) or not np.isfinite(np.r_[start,goal]).all():
        raise ValueError('Planning region endpoints must be finite world-frame 3-vectors')
    resolution=float(planning['resolution']);maximum=int(planning.get('max_local_grid_cells',131072))
    margin=np.asarray(planning.get('region_margin_m',2.),dtype=float)
    if margin.ndim==0:margin=np.full(3,float(margin))
    if resolution<=0 or not np.isfinite(resolution) or maximum<1 or margin.shape!=(3,) or not np.isfinite(margin).all() or np.any(margin<0):
        raise ValueError('Invalid planning region resolution, margin or cell budget')
    # At least one full cell around endpoints also keeps boundary endpoints
    # inside the grid. The planner separately validates robot footprint support.
    padding=np.maximum(margin,resolution)
    lower=np.floor((np.minimum(start,goal)-padding)/resolution)*resolution
    upper=np.ceil((np.maximum(start,goal)+padding)/resolution)*resolution
    shape=np.maximum(1,np.rint((upper-lower)/resolution).astype(np.int64))
    required=int(np.prod(shape,dtype=object))
    if required>maximum:
        raise MapBudgetExceeded(f'Requested planning region requires {required} cells, budget is {maximum}; this is a resource limit, not evidence of a blocked physical route')
    bounds=np.array([lower[0],upper[0],lower[1],upper[1],lower[2],upper[2]])
    observed=voxel_map.observed_bounds
    if observed is None:return PlannerGrid(lower,resolution,np.zeros(tuple(shape),bool),np.zeros(tuple(shape),bool))
    observed=np.asarray(observed,float)
    class ObservedView:
        voxel_size=voxel_map.voxel_size
        def query(self,points,return_known=False):
            points=np.asarray(points);inside=np.all((points[:,:3]>=observed[[0,2,4]])&(points[:,:3]<observed[[1,3,5]]),axis=1)
            values=np.zeros(len(points),np.float32);known=np.zeros(len(points),bool)
            if inside.any():values[inside],known[inside]=voxel_map.query(points[inside],return_known=True)
            return (values,known) if return_known else values
    return PlannerGrid.from_voxel_map(ObservedView(),bounds=bounds,resolution=resolution)

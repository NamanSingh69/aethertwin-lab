"""Risk-aware volumetric A*, constrained cubic splines, and MPPI refinement.

Grid arrays are indexed [x,y,z]. Unknown cells are blocked. The robot's radius
is conservatively represented by an axis-aligned cube for exact swept segment
checks against voxel boxes. Thus a returned path also clears a sphere of that
radius. The simulated vehicle is a bounded-speed holonomic point robot; this
module emits trajectories and does not command hardware.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import product
from pathlib import Path
import heapq
import json
import numpy as np
from scipy.interpolate import BSpline
from scipy.ndimage import distance_transform_edt,map_coordinates,maximum_filter
from scipy.optimize import minimize
from scipy.spatial import cKDTree
from core.contracts import PlannedTrajectory
from .geometry import obb_from_corners


@dataclass(frozen=True)
class PlannerGrid:
    origin: np.ndarray
    resolution: float
    occupied: np.ndarray
    known: np.ndarray

    def __post_init__(self):
        origin=np.array(self.origin,dtype=float,copy=True).reshape(3)
        occupied=np.array(self.occupied,dtype=bool,copy=True)
        known=np.array(self.known,dtype=bool,copy=True)
        if occupied.ndim!=3 or occupied.shape!=known.shape or min(occupied.shape)<1 or self.resolution<=0:
            raise ValueError("Planner requires matching nonempty 3D occupancy/known grids")
        if not np.isfinite(origin).all(): raise ValueError("Non-finite map origin")
        origin.setflags(write=False); occupied.setflags(write=False); known.setflags(write=False)
        object.__setattr__(self,"origin",origin)
        object.__setattr__(self,"occupied",occupied)
        object.__setattr__(self,"known",known)

    @property
    def shape(self): return self.occupied.shape

    @property
    def upper(self): return self.origin+np.asarray(self.shape)*self.resolution

    def index(self,point): return np.floor((np.asarray(point)-self.origin)/self.resolution).astype(int)

    def center(self,index): return self.origin+(np.asarray(index)+.5)*self.resolution

    def contains_index(self,index): return bool(np.all(np.asarray(index)>=0) and np.all(np.asarray(index)<self.shape))

    def save(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        np.savez_compressed(path,version=1,origin=self.origin,resolution=self.resolution,
                            occupied=self.occupied,known=self.known)

    @classmethod
    def load(cls,path):
        with np.load(path,allow_pickle=False) as data:
            if int(data["version"])!=1: raise ValueError("Unsupported planning map version")
            return cls(data["origin"],float(data["resolution"]),data["occupied"],data["known"])

    @classmethod
    def from_voxel_map(cls,voxel_map,bounds=(-6,6,-5,5,0,3.5),resolution=.25,
                       free_threshold=-.2,occupied_threshold=.4):
        """Conservatively aggregate ALL source cells overlapping each map cell.

        A planning cell is known free only when every positive-volume source
        voxel it intersects is observed and below free_threshold. Any observed
        occupied source voxel blocks the planning cell. Mixed/ambiguous evidence
        stays unknown. Sampling only planning-cell centers would incorrectly
        erase unseen subcells when the two grids have different resolutions.
        """
        bounds=np.asarray(bounds,dtype=float).reshape(6)
        origin=bounds[[0,2,4]]; upper=bounds[[1,3,5]]
        if resolution<=0 or np.any(upper<=origin) or not free_threshold<occupied_threshold:
            raise ValueError("Invalid planning bounds or occupancy thresholds")
        # Do not extend beyond requested bounds into unobserved space.
        shape=np.floor((upper-origin)/resolution+1e-9).astype(int)
        if np.any(shape<1): raise ValueError("Planning bounds smaller than a cell")
        source_size=float(voxel_map.voxel_size)
        occupied=np.zeros(tuple(shape),bool); known=np.zeros_like(occupied)
        # Batch one XY column at a time: memory is independent of world volume.
        z=np.arange(shape[2])
        for x in range(shape[0]):
            for y in range(shape[1]):
                indices=np.column_stack([np.full(len(z),x),np.full(len(z),y),z])
                lower=origin+indices*resolution
                upper_cell=lower+resolution
                first=np.floor((lower+1e-10*source_size)/source_size).astype(np.int64)
                last=np.floor((upper_cell-1e-10*source_size)/source_size).astype(np.int64)
                queries=[]; owners=[]
                for local,(a,b) in enumerate(zip(first,last)):
                    for cell in product(*(range(int(lo),int(hi)+1) for lo,hi in zip(a,b))):
                        queries.append((np.asarray(cell,dtype=float)+.5)*source_size)
                        owners.append(local)
                values,observed=voxel_map.query(np.asarray(queries),return_known=True)
                owners=np.asarray(owners)
                all_free=np.ones(len(z),bool); any_occupied=np.zeros(len(z),bool)
                np.logical_and.at(all_free,owners,observed&(values<free_threshold))
                np.logical_or.at(any_occupied,owners,observed&(values>occupied_threshold))
                occupied[x,y,:]=any_occupied
                known[x,y,:]=all_free|any_occupied
        return cls(origin,float(resolution),occupied,known)


def _segment_intersects_boxes(start,end,lower,upper):
    """Vectorized exact slab test, including stationary and boundary segments."""
    if not len(lower): return np.empty(0,dtype=bool)
    direction=np.asarray(end)-start
    t0=np.zeros(len(lower)); t1=np.ones(len(lower))
    possible=np.ones(len(lower),dtype=bool)
    for axis in range(3):
        if abs(direction[axis])<1e-14:
            possible&=(start[axis]>=lower[:,axis]-1e-10)&(start[axis]<=upper[:,axis]+1e-10)
        else:
            a=(lower[:,axis]-start[axis])/direction[axis]
            b=(upper[:,axis]-start[axis])/direction[axis]
            t0=np.maximum(t0,np.minimum(a,b)); t1=np.minimum(t1,np.maximum(a,b))
    return possible&(t0<=t1+1e-10)


def resample_polyline(points,count):
    points=np.asarray(points,dtype=float)
    if len(points)<=1: return np.repeat(points,max(count,1),axis=0)
    lengths=np.r_[0,np.cumsum(np.linalg.norm(np.diff(points,axis=0),axis=1))]
    unique=np.r_[True,np.diff(lengths)>1e-12]
    points=points[unique]; lengths=lengths[unique]
    if len(points)==1: return np.repeat(points,count,axis=0)
    query=np.linspace(0,lengths[-1],count)
    return np.column_stack([np.interp(query,lengths,points[:,axis]) for axis in range(3)])


def remaining_trajectory(trajectory,elapsed,position=None,duration=None):
    """Rebase a trajectory to CURRENT time, optionally clipping to one step.

    Dynamic obstacle predictions are relative to the current observation. Past
    knots must therefore be removed and future knot times shifted by elapsed.
    The current position is included as the first knot; it may be supplied by
    the simulator so roundoff or externally applied updates are swept too.
    """
    if trajectory.status!="ok" or len(trajectory.points)==0:
        return PlannedTrajectory(trajectory.status,np.empty((0,3),np.float32),np.empty(0),trajectory.reason,trajectory.cost)
    times=np.asarray(trajectory.times,dtype=float); points=np.asarray(trajectory.points,dtype=float)
    if len(times)!=len(points) or not np.isfinite(times).all() or np.any(np.diff(times)<0):
        raise ValueError("Trajectory knots require finite nondecreasing times")
    elapsed=float(np.clip(elapsed,times[0],times[-1]))
    finish=times[-1] if duration is None else min(times[-1],elapsed+max(float(duration),0.))
    current=np.array([np.interp(elapsed,times,points[:,j]) for j in range(3)]) if position is None else np.asarray(position,dtype=float)
    future=(times>elapsed+1e-12)&(times<finish-1e-12)
    remaining_points=np.vstack([current,points[future]])
    remaining_times=np.r_[0.,times[future]-elapsed]
    if finish>elapsed+1e-12:
        endpoint=np.array([np.interp(finish,times,points[:,j]) for j in range(3)])
        remaining_points=np.vstack([remaining_points,endpoint])
        remaining_times=np.r_[remaining_times,finish-elapsed]
    return PlannedTrajectory("ok",remaining_points,remaining_times,
                             trajectory.reason,trajectory.cost)


class SafeCorridorPlanner:
    def __init__(self,grid: PlannerGrid,radius=.25,risk_weight=12.,max_speed=1.,
                 max_acceleration=2.,mppi_samples=96,mppi_iterations=2,seed=0,
                 max_expansions=150000):
        if radius<0 or min(max_speed,max_acceleration)<=0 or mppi_samples<2 or mppi_iterations<1:
            raise ValueError("Invalid planner dynamics/sampling parameters")
        self.radius=float(radius); self.risk_weight=float(risk_weight)
        self.max_speed=float(max_speed); self.max_acceleration=float(max_acceleration)
        self.mppi_samples=int(mppi_samples); self.mppi_iterations=int(mppi_iterations)
        self.rng=np.random.default_rng(seed); self.max_expansions=int(max_expansions)
        self.last_diagnostics={}; self.set_grid(grid)

    def set_grid(self,grid):
        self.grid=grid; self.blocked=grid.occupied|~grid.known
        self.blocked_indices=np.argwhere(self.blocked)
        self.blocked_centers=grid.center(self.blocked_indices)
        self.tree=cKDTree(self.blocked_centers) if len(self.blocked_centers) else None
        # A conservative center-distance field used for rollout costs only.
        padded=np.pad(~self.blocked,1,constant_values=False)
        distances=distance_transform_edt(padded)[1:-1,1:-1,1:-1]*grid.resolution
        self.clearance=distances-.5*np.sqrt(3)*grid.resolution

        # For a point at a cell CENTER the exact cube/voxel slab test reduces
        # to an integer box stencil. This is a mathematical specialization,
        # not a sampled clearance approximation. Continuous endpoints continue
        # to use the reference slab implementation below.
        self._expanded_half=self.radius/grid.resolution+.5
        center_reach=int(np.floor(self._expanded_half+1e-10))
        touched=maximum_filter(self.blocked,size=2*center_reach+1,mode="constant",cval=0)
        self._center_clear=~touched
        for axis,size in enumerate(grid.shape):
            centers=grid.origin[axis]+(np.arange(size)+.5)*grid.resolution
            valid=(centers-self.radius>=grid.origin[axis]-1e-10)&(centers+self.radius<grid.upper[axis])
            reshape=[1,1,1]; reshape[axis]=size
            self._center_clear&=valid.reshape(reshape)
        self._edge_masks={}
        self._edge_pad=int(np.ceil(self._expanded_half))+2
        self._padded_blocked=np.pad(self.blocked,self._edge_pad,constant_values=False)
        self._padded_center_clear=np.pad(self._center_clear,self._edge_pad,constant_values=False)

    def _static_edge_mask(self,offset):
        """Exact static clearance for every center->neighbor edge on this grid.

        Intersect the unit edge with every possible relative expanded voxel
        box once, then OR translated occupancy arrays using that exact stencil.
        The final endpoint masks enforce physical map bounds. The result is
        identical to KD-tree candidate collection plus slab tests, including
        diagonal edge/corner contacts and arbitrary footprint radii.
        """
        offset=tuple(map(int,offset))
        if offset in self._edge_masks: return self._edge_masks[offset]
        d=np.asarray(offset,dtype=float); half=self._expanded_half
        first=np.ceil(np.minimum(0,d)-half-1e-10).astype(int)
        last=np.floor(np.maximum(0,d)+half+1e-10).astype(int)
        candidates=np.array(list(product(*(range(a,b+1) for a,b in zip(first,last)))),dtype=int)
        intersects=_segment_intersects_boxes(np.zeros(3),d,candidates-half,candidates+half)
        stencil=candidates[intersects]
        touched=np.zeros(self.grid.shape,dtype=bool); pad=self._edge_pad
        for delta in stencil:
            slices=tuple(slice(pad+int(k),pad+int(k)+n) for k,n in zip(delta,self.grid.shape))
            np.logical_or(touched,self._padded_blocked[slices],out=touched)
        destination=tuple(slice(pad+k,pad+k+n) for k,n in zip(offset,self.grid.shape))
        result=~touched&self._center_clear&self._padded_center_clear[destination]
        self._edge_masks[offset]=result
        return result

    def _dynamic_geometry(self,entities):
        context=[]
        for ent in entities:
            box=obb_from_corners(ent.obb_corners)
            uncertainty=0.
            if ent.covariance is not None:
                uncertainty=2*np.sqrt(max(float(np.linalg.eigvalsh(ent.covariance[:3,:3]).max()),0.))
            context.append((np.asarray(ent.position),np.asarray(ent.velocity),np.asarray(ent.acceleration),
                            box.rotation,box.half_extents+self.radius+uncertainty))
        if not context: return None
        positions,velocities,accelerations,rotations,half_extents=map(np.stack,zip(*context))
        acceleration_norm=np.linalg.norm(accelerations,axis=1)
        return positions,velocities,accelerations,rotations,half_extents,acceleration_norm

    @staticmethod
    def _dynamic_segment_clear(start,end,t0,t1,context):
        if context is None: return True
        duration=max(float(t1-t0),0.)
        position,velocity,acceleration,rotation,half_base,acceleration_norm=context
        c0=position+velocity*t0+.5*acceleration*t0*t0
        c1=position+velocity*t1+.5*acceleration*t1*t1
        local_start=np.einsum("ni,nij->nj",start-c0,rotation)
        local_end=np.einsum("ni,nij->nj",end-c1,rotation)
        curvature_bound=acceleration_norm*duration*duration/8
        half=half_base+curvature_bound[:,None]
        direction=local_end-local_start
        enter=np.zeros(len(position)); leave=np.ones(len(position)); possible=np.ones(len(position),bool)
        for axis in range(3):
            moving=np.abs(direction[:,axis])>=1e-14
            stationary=~moving
            possible[stationary]&=(np.abs(local_start[stationary,axis])<=half[stationary,axis]+1e-10)
            first=np.zeros(len(position)); last=np.ones(len(position))
            first[moving]=(-half[moving,axis]-local_start[moving,axis])/direction[moving,axis]
            last[moving]=(half[moving,axis]-local_start[moving,axis])/direction[moving,axis]
            enter=np.maximum(enter,np.minimum(first,last)); leave=np.minimum(leave,np.maximum(first,last))
        return not np.any(possible&(enter<=leave+1e-10))

    def _static_segment_clear(self,start,end):
        start=np.asarray(start,dtype=float); end=np.asarray(end,dtype=float)
        if not np.isfinite(np.r_[start,end]).all(): return False
        # A full robot footprint must stay within the known map bounds.
        if np.any(np.minimum(start,end)-self.radius<self.grid.origin-1e-10) or np.any(np.maximum(start,end)+self.radius>=self.grid.upper):
            return False
        if self.tree is not None:
            middle=(start+end)/2
            query_radius=np.linalg.norm(end-start)/2+np.sqrt(3)*(self.radius+self.grid.resolution/2)
            indices=self.tree.query_ball_point(middle,query_radius+1e-9)
            centers=self.blocked_centers[indices]
            half=self.grid.resolution/2+self.radius
            if np.any(_segment_intersects_boxes(start,end,centers-half,centers+half)): return False
        return True

    def segment_clear(self,start,end,t0=0.,t1=0.,entities=(),_context=None):
        if not np.isfinite([t0,t1]).all(): return False
        start=np.asarray(start,dtype=float); end=np.asarray(end,dtype=float)
        if not self._static_segment_clear(start,end): return False
        context=self._dynamic_geometry(entities) if _context is None else _context
        return self._dynamic_segment_clear(start,end,t0,t1,context)

    def path_clear(self,points,times=None,entities=()):
        points=np.asarray(points)
        if len(points)==0: return False
        if times is None:
            times=np.r_[0.,np.cumsum(np.linalg.norm(np.diff(points,axis=0),axis=1))/self.max_speed]
        context=self._dynamic_geometry(entities)
        if len(points)==1: return self.segment_clear(points[0],points[0],float(times[0]),float(times[0]),entities,_context=context)
        return all(self.segment_clear(a,b,float(t0),float(t1),entities,_context=context)
                   for a,b,t0,t1 in zip(points[:-1],points[1:],times[:-1],times[1:]))

    def _risk_values(self,points,risk):
        if risk is None: return np.zeros(np.asarray(points).shape[:-1])
        coords=(np.asarray(points)-self.grid.origin)/self.grid.resolution-.5
        dimensions=risk.ndim
        if dimensions not in (2,3): raise ValueError("risk must be an [x,y] or [x,y,z] array")
        if tuple(risk.shape)!=self.grid.shape[:dimensions]: raise ValueError("Risk grid shape differs from planning grid")
        return map_coordinates(risk,np.moveaxis(coords[...,:dimensions],-1,0),order=1,mode="nearest")

    def _astar(self,start,goal,risk,ground,entities):
        s=tuple(self.grid.index(start)); g=tuple(self.grid.index(goal))
        if ground and s[2]!=g[2]: return None,"Ground mode requires start and goal on one planning layer"
        context=self._dynamic_geometry(entities)
        if s==g:
            duration=np.linalg.norm(goal-start)/self.max_speed
            if self.segment_clear(start,goal,0.,duration,entities,_context=context):
                return np.vstack([start,goal]),""
            return None,"No direct subcell corridor between start and goal"
        neighbors=[np.array(v,dtype=int) for v in product((-1,0,1),repeat=3)
                   if v!=(0,0,0) and (not ground or v[2]==0)]
        off_center_ground=ground and abs(start[2]-self.grid.center(s)[2])>1e-10
        edge_masks={} if off_center_ground else {tuple(delta):self._static_edge_mask(delta) for delta in neighbors}
        if risk is not None:
            # Cell centers map exactly to integer interpolation coordinates.
            endpoint_risk=self._risk_values(np.array([start,goal]),risk)
        else: endpoint_risk=np.zeros(2)
        queue=[(float(np.linalg.norm(goal-start)),0.,s)]
        score={s:0.}; distance={s:0.}; parent={}; visited=set(); expansions=0
        while queue:
            _,cost,node=heapq.heappop(queue)
            if node in visited: continue
            visited.add(node); expansions+=1
            if node==g:
                chain=[node]
                while chain[-1] in parent: chain.append(parent[chain[-1]])
                points=self.grid.center(np.array(chain[::-1])); points[0]=start; points[-1]=goal
                if ground:points[:,2]=start[2]
                self.last_diagnostics["astar_expansions"]=expansions
                return points,""
            if expansions>=self.max_expansions:
                return None,f"Search expansion limit ({self.max_expansions}) reached"
            position=self.grid.center(node)
            if ground:position[2]=start[2]
            if node==s: position=np.asarray(start)
            node_risk=float(endpoint_risk[0]) if node==s else (0. if risk is None else float(self._risk_values(position[None],risk)[0]) if off_center_ground else float(risk[node[:risk.ndim]]))
            for offset in neighbors:
                nxt=tuple(np.asarray(node)+offset)
                if nxt in visited or not self.grid.contains_index(nxt) or self.blocked[nxt]: continue
                if nxt!=g and not off_center_ground and not self._center_clear[nxt]: continue
                endpoint=np.asarray(goal) if nxt==g else self.grid.center(nxt)
                if ground:endpoint=endpoint.copy();endpoint[2]=start[2]
                length=float(np.linalg.norm(endpoint-position))
                t0=distance[node]/self.max_speed; t1=t0+length/self.max_speed
                if node==s or nxt==g or off_center_ground:
                    if not self._static_segment_clear(position,endpoint): continue
                elif not edge_masks[tuple(offset)][node]: continue
                if not self._dynamic_segment_clear(position,endpoint,t0,t1,context): continue
                next_risk=float(endpoint_risk[1]) if nxt==g else (0. if risk is None else float(self._risk_values(endpoint[None],risk)[0]) if off_center_ground else float(risk[nxt[:risk.ndim]]))
                mean_risk=.5*(node_risk+next_risk)
                tentative=score[node]+length*(1+self.risk_weight*mean_risk)
                if tentative<score.get(nxt,float("inf")):
                    score[nxt]=tentative; distance[nxt]=distance[node]+length; parent[nxt]=node
                    heuristic=float(np.linalg.norm(endpoint-goal))
                    heapq.heappush(queue,(tentative+heuristic,tentative,nxt))
        self.last_diagnostics["astar_expansions"]=expansions
        return None,"No route through known, footprint-clear space"

    def _route_cost(self,path,risk):
        length=float(np.linalg.norm(np.diff(path,axis=0),axis=1).sum())
        if length<1e-12: return 0.
        query=resample_polyline(path,max(2,int(np.ceil(length/(self.grid.resolution*.4)))+1))
        segments=np.linalg.norm(np.diff(query,axis=0),axis=1)
        values=self._risk_values(query,risk)
        return float(np.sum(segments*(1+self.risk_weight*.5*(values[:-1]+values[1:]))))

    def _shortcut(self,path,entities,risk):
        result=[path[0]]; i=0; elapsed=0.
        context=self._dynamic_geometry(entities)
        while i<len(path)-1:
            for j in range(len(path)-1,i,-1):
                duration=np.linalg.norm(path[j]-path[i])/self.max_speed
                if self.segment_clear(path[i],path[j],elapsed,elapsed+duration,entities,_context=context):
                    if risk is None or self._route_cost(np.array([path[i],path[j]]),risk)<=self._route_cost(path[i:j+1],risk)*1.01: break
            result.append(path[j]); elapsed+=duration; i=j
        return np.array(result)

    def _spline(self,path,risk,entities,ground=False):
        if len(path)<3: return path
        count=max(4,min(14,len(path)+2)); controls=resample_polyline(path,count)
        # Uniform clamped cubic B-spline: endpoint interpolation and C2 interior.
        degree=3
        knots=np.r_[np.zeros(4),np.linspace(0,1,count-degree+1)[1:-1],np.ones(4)]
        sample_count=max(32,int(np.ceil(np.linalg.norm(np.diff(path,axis=0),axis=1).sum()/(self.grid.resolution*.25))))
        parameter=np.linspace(0,1,sample_count)
        reference=resample_polyline(path,sample_count)
        initial=controls[1:-1].ravel()
        lower=np.minimum(controls[1:-1]-.75*self.grid.resolution,controls[1:-1])
        upper=controls[1:-1]+.75*self.grid.resolution
        if ground:lower[:,2]=upper[:,2]=path[0,2]
        def curve(flat):
            cp=np.vstack([path[0],flat.reshape(-1,3),path[-1]])
            points=BSpline(knots,cp,degree)(parameter)
            if ground:points[:,2]=path[0,2]
            return points
        def objective(flat):
            points=curve(flat); acceleration=np.diff(points,n=2,axis=0)
            coords=(points-self.grid.origin)/self.grid.resolution-.5
            clearance=map_coordinates(self.clearance,coords.T,order=1,mode="constant",cval=-1.)
            collision=np.maximum(self.radius+self.grid.resolution*.05-clearance,0)
            return float(30*np.sum(acceleration**2)+.2*np.sum((points-reference)**2)
                +self.risk_weight*self._risk_values(points,risk).sum()+1e4*np.sum(collision**2))
        solution=minimize(objective,initial,method="L-BFGS-B",bounds=list(zip(lower.ravel(),upper.ravel())),
                          options={"maxiter":35,"ftol":1e-7})
        proposed=curve(solution.x)
        if self.path_clear(proposed,entities=entities) and self._route_cost(proposed,risk)<=self._route_cost(path,risk)*1.05:
            self.last_diagnostics["spline_accepted"]=True
            return proposed
        # No unchecked smoothing: retain the verified corridor centerline.
        self.last_diagnostics["spline_accepted"]=False
        return path

    def _mppi(self,path,risk,entities,ground):
        length=float(np.linalg.norm(np.diff(path,axis=0),axis=1).sum())
        horizon=max(16,min(80,int(np.ceil(length/.16))))
        reference=resample_polyline(path,horizon+1)
        dt=max(length/(self.max_speed*.75*horizon),.03)
        nominal=np.diff(reference,axis=0)/dt
        dimensions=2 if ground else 3
        temperature=1.; sigma=.22*self.max_speed
        costs=[]
        for iteration in range(self.mppi_iterations):
            noise=self.rng.normal(0,sigma,(self.mppi_samples,horizon,3))
            noise[0]=0.
            if ground: noise[:,:,2]=0.
            controls=nominal[None]+noise
            # Velocity and acceleration saturation are part of the rollout model.
            for step in range(horizon):
                speed=np.linalg.norm(controls[:,step,:dimensions],axis=1)
                controls[:,step,:dimensions]*=np.minimum(1,self.max_speed/np.maximum(speed,1e-12))[:,None]
                if step:
                    dv=controls[:,step]-controls[:,step-1]
                    magnitude=np.linalg.norm(dv,axis=1)
                    controls[:,step]=controls[:,step-1]+dv*np.minimum(1,self.max_acceleration*dt/np.maximum(magnitude,1e-12))[:,None]
            trajectories=np.concatenate([np.broadcast_to(path[0],(self.mppi_samples,1,3)),
                path[0]+np.cumsum(controls,axis=1)*dt],axis=1)
            if ground: trajectories[:,:,2]=path[0,2]
            flat=trajectories.reshape(-1,3)
            coords=(flat-self.grid.origin)/self.grid.resolution-.5
            clearance=map_coordinates(self.clearance,coords.T,order=1,mode="constant",cval=-1.).reshape(self.mppi_samples,-1)
            collision=np.maximum(self.radius-clearance,0)
            state_cost=3*np.sum((trajectories-reference[None])**2,axis=(1,2))
            state_cost+=100*np.sum((trajectories[:,-1]-path[-1])**2,axis=1)
            state_cost+=self.risk_weight*self._risk_values(flat,risk).reshape(self.mppi_samples,-1).sum(1)
            state_cost+=1e5*np.sum(collision**2,axis=1)
            state_cost+=.05*np.sum(controls**2,axis=(1,2))
            # MPPI importance correction for shifted Gaussian control sampling.
            state_cost+=temperature*np.sum(nominal[None]*noise/(sigma*sigma),axis=(1,2))*dt
            for ent in entities:
                times=np.arange(horizon+1)*dt
                centers=ent.position+times[:,None]*ent.velocity+.5*times[:,None]**2*ent.acceleration
                radius=self.radius+.5*np.linalg.norm(ent.bbox_max-ent.bbox_min)
                distances=np.linalg.norm(trajectories-centers[None],axis=2)
                state_cost+=1e5*np.maximum(radius-distances,0).sum(1)
            weights=np.exp(-np.clip((state_cost-state_cost.min())/temperature,0,700))
            weights/=weights.sum()
            nominal+=np.einsum("n,ntd->td",weights,controls-nominal[None])
            costs.append(float(state_cost.min()))
        candidate=np.vstack([path[0],path[0]+np.cumsum(nominal,axis=0)*dt])
        if ground: candidate[:,2]=path[0,2]
        # Terminal projection is accepted only after the entire swept path passes.
        candidate[-1]=path[-1]
        self.last_diagnostics.update(mppi_samples=self.mppi_samples,mppi_iterations=self.mppi_iterations,mppi_costs=costs)
        if self.path_clear(candidate,entities=entities) and self._route_cost(candidate,risk)<=self._route_cost(path,risk)*1.05:
            self.last_diagnostics["mppi_accepted"]=True
            return candidate
        self.last_diagnostics["mppi_accepted"]=False
        return path

    def _time_parameterize(self,path):
        if len(path)==1: return np.zeros(1)
        segments=np.linalg.norm(np.diff(path,axis=0),axis=1)
        speed=np.full(len(path),self.max_speed)
        speed[0]=speed[-1]=0.
        if len(path)>2:
            tangent=np.diff(path,axis=0)/np.maximum(segments[:,None],1e-12)
            curvature=np.linalg.norm(np.diff(tangent,axis=0),axis=1)/np.maximum(.5*(segments[:-1]+segments[1:]),1e-9)
            speed[1:-1]=np.minimum(speed[1:-1],np.sqrt(self.max_acceleration/np.maximum(curvature,1e-12)))
        for i in range(1,len(path)):
            speed[i]=min(speed[i],np.sqrt(speed[i-1]**2+2*self.max_acceleration*segments[i-1]))
        for i in range(len(path)-2,-1,-1):
            speed[i]=min(speed[i],np.sqrt(speed[i+1]**2+2*self.max_acceleration*segments[i]))
        durations=2*segments/np.maximum(speed[:-1]+speed[1:],1e-6)
        # A two-point path needs a triangular/trapezoidal acceleration profile.
        if len(path)==2:
            length=segments[0]; ramp=self.max_speed**2/self.max_acceleration
            durations[0]=2*np.sqrt(length/self.max_acceleration) if length<=ramp else length/self.max_speed+self.max_speed/self.max_acceleration
        return np.r_[0,np.cumsum(durations)]

    def plan(self,start,goal,risk=None,ground=True,entities=()):
        self.last_diagnostics={"ground":bool(ground),"radius":self.radius}
        start=np.asarray(start,dtype=float).reshape(3); goal=np.asarray(goal,dtype=float).reshape(3)
        entities=tuple(entities)
        def failure(status,reason):
            return PlannedTrajectory(status,np.empty((0,3),np.float32),np.empty(0),reason,float("inf"))
        if risk is not None:
            risk=np.asarray(risk,dtype=float)
            if not np.isfinite(risk).all() or np.any(risk<0): raise ValueError("Risk costs must be finite and nonnegative")
        if not self.segment_clear(start,start,entities=entities): return failure("invalid_start","Start footprint intersects occupied, unknown, or out-of-map space")
        if not self.segment_clear(goal,goal): return failure("invalid_goal","Goal footprint intersects occupied, unknown, or out-of-map space")
        if np.linalg.norm(goal-start)<1e-9:
            return PlannedTrajectory("ok",start[None].copy(),np.zeros(1))
        if ground and abs(goal[2]-start[2])>1e-6: return failure("invalid_goal","Ground trajectories require equal start/goal heights")
        if ground:goal=goal.copy();goal[2]=start[2]
        path,reason=self._astar(start,goal,risk,ground,entities)
        if path is None: return failure("no_path",reason)
        path=self._shortcut(path,entities,risk)
        path=self._spline(path,risk,entities,ground=ground)
        path=self._mppi(path,risk,entities,ground)
        # Remove duplicate points before time parameterization.
        path=path[np.r_[True,np.linalg.norm(np.diff(path,axis=0),axis=1)>1e-8]]
        times=self._time_parameterize(path)
        if not self.path_clear(path,times,entities):
            return failure("no_path","No trajectory clears predicted dynamic obstacles at its retimed arrival times")
        length=np.linalg.norm(np.diff(path,axis=0),axis=1)
        values=self._risk_values(path,risk)
        cost=float(np.sum(length*(1+self.risk_weight*.5*(values[:-1]+values[1:]))))
        self.last_diagnostics["path_length"]=float(length.sum())
        self.last_diagnostics["duration"]=float(times[-1])
        # Preserve the precision used by the swept collision check. Rounding a
        # near-boundary safe path to float32 can move it inside an obstacle.
        return PlannedTrajectory("ok",path,times,cost=cost)

    @staticmethod
    def export(trajectory,path):
        path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
        payload={"schema_version":1,"frame":"world","units":"metres_seconds",
            "status":trajectory.status,"reason":trajectory.reason,
            "points":trajectory.points.tolist(),"times":trajectory.times.tolist(),
            "cost":float(trajectory.cost) if np.isfinite(trajectory.cost) else None}
        if path.suffix.lower()=='.csv':
            data=np.column_stack([trajectory.times,trajectory.points])
            np.savetxt(path,data,delimiter=',',header='time_s,x_m,y_m,z_m',comments='')
            path.with_suffix('.metadata.json').write_text(json.dumps(payload,indent=2,allow_nan=False),encoding='utf-8')
        else:path.write_text(json.dumps(payload,indent=2,allow_nan=False),encoding="utf-8")

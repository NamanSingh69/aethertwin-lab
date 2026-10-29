"""Sampling-rate-independent, exponentially decayed floor traffic density."""
from __future__ import annotations
from pathlib import Path
import numpy as np
from core.contracts import SemanticClass


class TrafficHeatmap:
    def __init__(self,bounds=(-6.,6.,-5.,5.),resolution=.25,decay_seconds=30.):
        self.bounds=tuple(map(float,bounds)); self.resolution=float(resolution)
        self.decay_seconds=float(decay_seconds)
        if resolution<=0 or decay_seconds<=0 or self.bounds[1]<=self.bounds[0] or self.bounds[3]<=self.bounds[2]:
            raise ValueError("Invalid heatmap geometry/decay")
        self.shape=(int(np.ceil((self.bounds[3]-self.bounds[2])/resolution)),
                    int(np.ceil((self.bounds[1]-self.bounds[0])/resolution)))
        self.values=np.zeros(self.shape,dtype=np.float64); self.timestamp=None

    def update(self,entities,timestamp):
        timestamp=float(timestamp)
        dt=.1 if self.timestamp is None else timestamp-self.timestamp
        if not np.isfinite(timestamp) or dt<0: raise ValueError("Heatmap times must be finite and ordered")
        self.values*=np.exp(-dt/self.decay_seconds)
        # Exact integral of a constant occupancy source over the update interval.
        mass=self.decay_seconds*(-np.expm1(-dt/self.decay_seconds))
        for ent in entities:
            if ent.semantic_class not in (SemanticClass.HUMAN_PEDESTRIAN,SemanticClass.DYNAMIC_ROBOT_CART): continue
            x,y=np.asarray(ent.position)[:2]
            if not (self.bounds[0]<=x<self.bounds[1] and self.bounds[2]<=y<self.bounds[3]): continue
            gx=(x-self.bounds[0])/self.resolution-.5
            gy=(y-self.bounds[2])/self.resolution-.5
            ix,iy=int(np.floor(gx)),int(np.floor(gy)); fx,fy=gx-ix,gy-iy
            deposits=[]
            for dx,dy,w in ((0,0,(1-fx)*(1-fy)),(1,0,fx*(1-fy)),(0,1,(1-fx)*fy),(1,1,fx*fy)):
                xx,yy=ix+dx,iy+dy
                if 0<=xx<self.shape[1] and 0<=yy<self.shape[0]: deposits.append((yy,xx,w))
            total=sum(w for _,_,w in deposits)
            for yy,xx,w in deposits: self.values[yy,xx]+=mass*w/max(total,1e-12)
        self.timestamp=timestamp
        return self.values.astype(np.float32,copy=True)

    def save(self,path):
        path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
        np.savez_compressed(path,version=np.array(1),bounds=self.bounds,resolution=self.resolution,
            decay_seconds=self.decay_seconds,values=self.values,
            timestamp=np.array(np.nan if self.timestamp is None else self.timestamp))

    @classmethod
    def load(cls,path):
        with np.load(path,allow_pickle=False) as data:
            if int(data["version"])!=1: raise ValueError("Unsupported heatmap version")
            result=cls(tuple(data["bounds"]),float(data["resolution"]),float(data["decay_seconds"]))
            if data["values"].shape!=result.shape: raise ValueError("Heatmap shape mismatch")
            result.values=data["values"].astype(np.float64,copy=True)
            t=float(data["timestamp"]); result.timestamp=None if np.isnan(t) else t
        return result

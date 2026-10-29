"""Range-dependent measured-background distance with a level floor exclusion."""
import numpy as np
from scipy.spatial import cKDTree


class SpatialDiffEngine:
    def __init__(self,points,tau0=.14,beta=.008,floor_epsilon=.06,distance_provider=None):
        self.points=np.asarray(points)[:,:3];self.tree=cKDTree(self.points) if len(self.points) else None
        self.tau0=tau0;self.beta=beta;self.floor_epsilon=floor_epsilon;self.distance_provider=distance_provider
    def distances(self,points):
        p=np.asarray(points)[:,:3]
        if self.distance_provider is not None:return self.distance_provider(p,radius=2.)
        return np.full(len(p),np.inf) if self.tree is None else self.tree.query(p,workers=1)[0]
    def foreground_mask(self,points,origin=None):
        p=np.asarray(points)[:,:3];o=np.zeros(3) if origin is None else np.asarray(origin)
        threshold=self.tau0+self.beta*np.linalg.norm(p-o,axis=1)
        distance=self.distances(p) if self.distance_provider is None else self.distance_provider(p,radius=max(2.,float(np.max(threshold,initial=0))))
        return (distance>threshold)&(p[:,2]>self.floor_epsilon)

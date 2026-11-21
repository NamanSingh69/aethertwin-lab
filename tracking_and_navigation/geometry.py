"""Oriented boxes, exact convex intersection volumes, and separating axes.

GIoU uses the convex hull of the sixteen box corners as its enclosing convex
body. Unlike an axis-aligned enclosing box this definition is rotation invariant.
All geometry uses world metres and rotation matrices with box axes in columns.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import product
import numpy as np
from scipy.spatial import ConvexHull, QhullError

SIGNS = np.array(list(product((-1., 1.), repeat=3)))
EDGES = [(i,j) for i in range(8) for j in range(i+1,8)
         if np.count_nonzero(SIGNS[i] != SIGNS[j]) == 1]
_EDGE_I=np.array([i for i,_ in EDGES]);_EDGE_J=np.array([j for _,j in EDGES])


@dataclass(frozen=True)
class OBB:
    center: np.ndarray
    half_extents: np.ndarray
    rotation: np.ndarray

    def __post_init__(self):
        c=np.asarray(self.center,dtype=np.float64).reshape(3)
        h=np.asarray(self.half_extents,dtype=np.float64).reshape(3)
        R=np.asarray(self.rotation,dtype=np.float64).reshape(3,3)
        if not (np.isfinite(c).all() and np.isfinite(h).all() and np.isfinite(R).all()):
            raise ValueError("Box geometry must be finite")
        if np.any(h<0) or not np.allclose(R.T@R,np.eye(3),atol=1e-5) or np.linalg.det(R)<.999:
            raise ValueError("OBB requires nonnegative half extents and an SO(3) rotation")
        object.__setattr__(self,"center",c)
        object.__setattr__(self,"half_extents",h)
        object.__setattr__(self,"rotation",R)

    @property
    def corners(self):
        return (SIGNS*self.half_extents)@self.rotation.T+self.center

    @property
    def volume(self):
        return float(8*np.prod(self.half_extents))

    def contains(self, points, tolerance=1e-9):
        return np.all(np.abs((np.asarray(points)-self.center)@self.rotation)
                      <=self.half_extents+tolerance,axis=-1)

    def planes(self):
        # A x <= b, six box half spaces.
        A=np.r_[self.rotation.T,-self.rotation.T]
        return A, A@self.center+np.tile(self.half_extents,2)


def obb_from_corners(corners):
    """Decode the SIGNS/product corner ordering returned by OBB.corners."""
    p=np.asarray(corners,dtype=np.float64).reshape(8,3)
    edges=np.array([p[4]-p[0],p[2]-p[0],p[1]-p[0]])
    sizes=np.linalg.norm(edges,axis=1)
    if np.any(sizes<1e-10):
        # Degenerate boxes get a deterministic orthogonal frame from SVD.
        _,_,vt=np.linalg.svd(p-p.mean(0),full_matrices=False)
        R=vt.T
        if np.linalg.det(R)<0: R[:,-1]*=-1
        local=(p-p.mean(0))@R
        return OBB(p.mean(0),np.max(np.abs(local),axis=0),R)
    R=(edges/sizes[:,None]).T
    return OBB(p.mean(0),sizes/2,R)


def boxes_intersect(a: OBB,b: OBB,tolerance=1e-9):
    """The complete 15-axis separating-axis test, including edge cross axes (all axes evaluated at once)."""
    ua=a.rotation.T;ub=b.rotation.T
    axes=np.concatenate([ua,ub,np.cross(ua[:,None,:],ub[None,:,:]).reshape(9,3)])
    length=np.linalg.norm(axes,axis=1);keep=length>=1e-12
    axes=axes[keep]/length[keep,None]
    radius_a=np.abs(axes@a.rotation)@a.half_extents;radius_b=np.abs(axes@b.rotation)@b.half_extents
    return not bool(np.any(np.abs(axes@(b.center-a.center))>radius_a+radius_b+tolerance))


def convex_volume(points):
    points=np.unique(np.round(np.asarray(points,dtype=np.float64),12),axis=0)
    if len(points)<4 or np.linalg.matrix_rank(points-points[0],tol=1e-10)<3:
        return 0.
    try:
        return float(ConvexHull(points).volume)
    except QhullError:
        return 0.


def intersection_volume(a: OBB,b: OBB):
    """Enumerate all convex intersection vertices: corners and edge/face hits."""
    if not boxes_intersect(a,b): return 0.
    corners_a,corners_b=a.corners,b.corners
    vertices=[*corners_a[b.contains(corners_a)],*corners_b[a.contains(corners_b)]]
    for corners,source,target in ((corners_a,a,b),(corners_b,b,a)):
        # Every (edge, plane) hit at once, in the same edge-major, plane-ascending order the per-edge loop produced.
        A,limits=target.planes()
        P=corners[_EDGE_I];D=corners[_EDGE_J]-P
        den=D@A.T;usable=np.abs(den)>1e-12
        alpha=np.divide(limits[None,:]-P@A.T,den,out=np.zeros_like(den),where=usable)
        hit=usable&(alpha>=-1e-10)&(alpha<=1+1e-10)
        if hit.any():
            edge,plane=np.nonzero(hit)
            v=P[edge]+np.clip(alpha[edge,plane],0,1)[:,None]*D[edge]
            vertices.extend(v[target.contains(v)&source.contains(v)])
    return min(a.volume,b.volume,convex_volume(vertices)) if vertices else 0.



def generalized_iou_3d(a: OBB,b: OBB):
    intersection=intersection_volume(a,b)
    union=a.volume+b.volume-intersection
    if union<=1e-15:
        return 1. if np.allclose(a.corners,b.corners) else 0.
    enclosing=convex_volume(np.r_[a.corners,b.corners])
    return float(np.clip(intersection/union-(enclosing-union)/max(enclosing,union),-1,1))

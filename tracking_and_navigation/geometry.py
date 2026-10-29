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
    """The complete 15-axis separating-axis test, including edge cross axes."""
    axes=[*a.rotation.T,*b.rotation.T]
    axes.extend(np.cross(u,v) for u in a.rotation.T for v in b.rotation.T)
    displacement=b.center-a.center
    for axis in axes:
        length=np.linalg.norm(axis)
        if length<1e-12: continue
        axis=axis/length
        radius_a=np.dot(a.half_extents,np.abs(a.rotation.T@axis))
        radius_b=np.dot(b.half_extents,np.abs(b.rotation.T@axis))
        if abs(displacement@axis)>radius_a+radius_b+tolerance:
            return False
    return True


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
    for source,target in ((a,b),(b,a)):
        corners=source.corners
        A,limits=target.planes()
        for i,j in EDGES:
            p,q=corners[i],corners[j]
            d=q-p
            den=A@d
            for k in np.flatnonzero(np.abs(den)>1e-12):
                alpha=(limits[k]-A[k]@p)/den[k]
                if -1e-10<=alpha<=1+1e-10:
                    v=p+np.clip(alpha,0,1)*d
                    if target.contains(v) and source.contains(v): vertices.append(v)
    return min(a.volume,b.volume,convex_volume(vertices)) if vertices else 0.


def generalized_iou_3d(a: OBB,b: OBB):
    intersection=intersection_volume(a,b)
    union=a.volume+b.volume-intersection
    if union<=1e-15:
        return 1. if np.allclose(a.corners,b.corners) else 0.
    enclosing=convex_volume(np.r_[a.corners,b.corners])
    return float(np.clip(intersection/union-(enclosing-union)/max(enclosing,union),-1,1))

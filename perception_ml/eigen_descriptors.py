"""Eight specified covariance descriptors plus eigenvalue sum and height std.

Ordering is checkpointed: linearity, planarity, scattering, omnivariance,
anisotropy, eigenentropy, curvature, verticality, eigenvalue_sum, height_std.
"""
import numpy as np
from scipy.spatial import cKDTree
from sklearn.cluster import DBSCAN

FEATURE_NAMES=("linearity","planarity","scattering","omnivariance","anisotropy",
               "eigenentropy","curvature","verticality","eigenvalue_sum","height_std")

def eigen_descriptors(points, k=16,indices=None):
    points=np.asarray(points,dtype=np.float64)
    n=len(points)
    if n==0: return np.empty((0,10),np.float32)
    if indices is None:_,idx=cKDTree(points).query(points,k=min(k,n))
    else:idx=np.asarray(indices)
    idx=np.asarray(idx).reshape(n,-1)
    local=points[idx]; centered=local-local.mean(1,keepdims=True)
    cov=np.einsum('nki,nkj->nij',centered,centered)/max(1,idx.shape[1]-1)+1e-6*np.eye(3)
    lam,vec=np.linalg.eigh(cov); lam=lam[:,::-1]; total=lam.sum(1)
    l1,l2,l3=lam.T; norm=lam/total[:,None]
    return np.column_stack(((l1-l2)/l1,(l2-l3)/l1,l3/l1,np.cbrt(l1*l2*l3),
        (l1-l3)/l1,-(norm*np.log(norm)).sum(1),l3/total,1-np.abs(vec[:,2,0]),
        total,np.std(local[:,:,2],axis=1))).astype(np.float32)

def cluster_points(points,eps=.4,min_samples=4):
    if len(points)==0: return np.empty(0,np.int32)
    return DBSCAN(eps=eps,min_samples=min_samples,n_jobs=1).fit_predict(np.asarray(points)[:,:3])

"""SO(3)/SE(3), twist convention [rotation, translation], float64."""
import numpy as np
from scipy.spatial.transform import Rotation

def skew_symmetric(v):
    x,y,z=np.asarray(v,dtype=np.float64)
    return np.array([[0,-z,y],[z,0,-x],[-y,x,0]])

hat=skew_symmetric

def vee(M):
    return np.array([M[2,1],M[0,2],M[1,0]])

def so3_exp(w):
    w=np.asarray(w,dtype=np.float64); t=np.linalg.norm(w); W=hat(w)
    a=np.sinc(t/np.pi)
    b=.5*np.sinc(t/(2*np.pi))**2
    return np.eye(3)+a*W+b*(W@W)

def so3_exp_batch(vectors):
    """Exact Rodrigues evaluation for N independent point acquisition times."""
    w=np.asarray(vectors,dtype=np.float64)
    if w.ndim!=2 or w.shape[1]!=3 or not np.isfinite(w).all():raise ValueError('Batch rotations require finite (N,3) vectors')
    theta=np.linalg.norm(w,axis=1);W=np.zeros((len(w),3,3),dtype=np.float64)
    W[:,0,1]=-w[:,2];W[:,0,2]=w[:,1];W[:,1,0]=w[:,2]
    W[:,1,2]=-w[:,0];W[:,2,0]=-w[:,1];W[:,2,1]=w[:,0]
    a=np.sinc(theta/np.pi)[:,None,None];b=(.5*np.sinc(theta/(2*np.pi))**2)[:,None,None]
    return np.eye(3)[None]+a*W+b*(W@W)

def so3_log(R):
    # Quaternion-based implementation avoids division by sin(theta) near pi.
    return Rotation.from_matrix(np.asarray(R,dtype=np.float64)).as_rotvec()

def left_jacobian(w):
    t=np.linalg.norm(w); W=hat(w)
    b=.5*np.sinc(t/(2*np.pi))**2
    c=(1/6-t*t/120+t**4/5040) if t<1e-4 else (t-np.sin(t))/t**3
    return np.eye(3)+b*W+c*W@W

def se3_exp(xi):
    xi=np.asarray(xi,dtype=np.float64); T=np.eye(4)
    T[:3,:3]=so3_exp(xi[:3]); T[:3,3]=left_jacobian(xi[:3])@xi[3:]
    return T

def se3_log(T):
    w=so3_log(T[:3,:3]); return np.r_[w,np.linalg.solve(left_jacobian(w),T[:3,3])]

def inverse(T):
    out=np.eye(4); out[:3,:3]=T[:3,:3].T; out[:3,3]=-out[:3,:3]@T[:3,3]; return out

def transform_points(T,points):
    return np.asarray(points)@T[:3,:3].T+T[:3,3]

def interpolate(T0,T1,alpha):
    return T0@se3_exp(float(alpha)*se3_log(inverse(T0)@T1))

def adjoint(T):
    A=np.zeros((6,6)); R=T[:3,:3]; A[:3,:3]=R; A[3:,3:]=R; A[3:,:3]=hat(T[:3,3])@R; return A

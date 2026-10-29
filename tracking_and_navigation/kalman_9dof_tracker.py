"""CA/CV Kalman tracking with gated, multi-cue Hungarian association.

The CA model has nine states [position, velocity, acceleration]; its covariance
uses integrated continuous white jerk. CV uses six states and white acceleration.
Scene flow is a displacement in metres, divided by its measured dt exactly once.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from scipy.linalg import block_diag
from scipy.optimize import linear_sum_assignment
from scipy.stats import chi2
from core.contracts import Detection3D,TrackedEntity3D,SemanticClass
from .geometry import OBB,generalized_iou_3d


def transition_and_noise(dt,model="ca",spectral_density=.8):
    if not np.isfinite(dt) or dt<=0: raise ValueError("Kalman dt must be positive")
    I=np.eye(3)
    if model=="ca":
        F=np.eye(9); F[:3,3:6]=dt*I; F[:3,6:9]=.5*dt**2*I; F[3:6,6:9]=dt*I
        temporal=np.array([[dt**5/20,dt**4/8,dt**3/6],
                           [dt**4/8,dt**3/3,dt**2/2],
                           [dt**3/6,dt**2/2,dt]])
    elif model=="cv":
        F=np.eye(6); F[:3,3:6]=dt*I
        temporal=np.array([[dt**3/3,dt**2/2],[dt**2/2,dt]])
    else: raise ValueError("model must be 'ca' or 'cv'")
    return F,np.kron(temporal,I)*float(spectral_density)


def _measurement(detection,dt,state_dimension,position_noise):
    use_flow=bool(detection.flow_valid) and dt>0
    dimension=6 if use_flow else 3
    H=np.zeros((dimension,state_dimension)); H[:3,:3]=np.eye(3)
    z=np.asarray(detection.position,dtype=float)
    R=np.eye(dimension)*position_noise**2
    if use_flow:
        H[3:6,3:6]=np.eye(3)
        z=np.r_[z,np.asarray(detection.flow,dtype=float)/dt]
        # flow_variance is displacement variance [m^2].
        R[3:,3:]=np.eye(3)*max(float(detection.flow_variance)/dt**2,1e-6)
    return z,H,R


@dataclass
class _Track:
    identifier: int
    x: np.ndarray
    covariance: np.ndarray
    detection: Detection3D
    probabilities: np.ndarray
    embedding: np.ndarray
    last_seen: float
    hits: int=1
    missed: int=0


class KalmanMultiObjectTracker:
    def __init__(self,model="ca",max_age=1.,position_noise=.08,
                 process_noise=.8,gate_probability=.99,min_hits=1,
                 weights=(1.,2.,.5)):
        if model not in ("ca","cv"): raise ValueError("Unknown motion model")
        if max_age<=0 or min_hits<1 or not 0<gate_probability<1:
            raise ValueError("Invalid tracker lifetime/gate")
        self.model=model; self.dimension=9 if model=="ca" else 6
        self.max_age=float(max_age); self.position_noise=float(position_noise)
        self.process_noise=float(process_noise); self.gate_probability=gate_probability
        self.min_hits=int(min_hits); self.weights=np.asarray(weights)
        self.tracks=[]; self.next_id=1; self.timestamp=None

    def reset(self):
        self.tracks=[]; self.next_id=1; self.timestamp=None

    def _create(self,d,timestamp,dt):
        x=np.zeros(self.dimension); x[:3]=d.position
        if d.flow_valid: x[3:6]=np.asarray(d.flow)/dt
        P=np.eye(self.dimension); P[:3,:3]*=self.position_noise**2
        if d.flow_valid: P[3:6,3:6]*=max(d.flow_variance/dt**2,.01)
        probabilities=np.maximum(np.asarray(d.class_probs,dtype=float),1e-8)
        probabilities/=probabilities.sum()
        t=_Track(self.next_id,x,P,d,probabilities,np.array(d.embedding,dtype=float),timestamp)
        self.next_id+=1; return t

    def _validate_detection(self,d):
        if np.asarray(d.position).shape!=(3,) or np.asarray(d.size).shape!=(3,):
            raise ValueError("Detection center and size must have shape (3,)")
        if np.asarray(d.class_probs).shape!=(7,) or np.any(np.asarray(d.class_probs)<0):
            raise ValueError("Detection probabilities must be seven nonnegative values")
        if np.sum(d.class_probs)<=0 or not np.isfinite(np.r_[d.position,d.size,d.class_probs,d.flow]).all():
            raise ValueError("Invalid detection values")
        OBB(d.position,np.asarray(d.size)/2,d.rotation)

    def update(self,detections,timestamp,dt=.1):
        detections=list(detections); timestamp=float(timestamp)
        if not np.isfinite(timestamp): raise ValueError("Invalid timestamp")
        if self.timestamp is not None:
            elapsed=timestamp-self.timestamp
            if elapsed<=0: raise ValueError("Tracker timestamps must strictly increase")
        else: elapsed=float(dt)
        if dt<=0: raise ValueError("Observation dt must be positive")
        for d in detections: self._validate_detection(d)
        self.tracks=[t for t in self.tracks if timestamp-t.last_seen<=self.max_age]
        F,Q=transition_and_noise(elapsed,self.model,self.process_noise)
        for t in self.tracks:
            t.x=F@t.x; t.covariance=F@t.covariance@F.T+Q; t.missed+=1
        cost=np.full((len(self.tracks),len(detections)),1e9)
        for i,t in enumerate(self.tracks):
            a=OBB(t.x[:3],np.maximum(t.detection.size,.01)/2,t.detection.rotation)
            for j,d in enumerate(detections):
                z,H,R=_measurement(d,dt,self.dimension,self.position_noise)
                residual=z-H@t.x; S=H@t.covariance@H.T+R
                mahal=float(residual@np.linalg.solve(S,residual))
                if mahal>chi2.ppf(self.gate_probability,len(z)): continue
                b=OBB(d.position,np.maximum(d.size,.01)/2,d.rotation)
                norm=np.linalg.norm(t.embedding)*np.linalg.norm(d.embedding)
                cosine=(1-float(t.embedding@d.embedding)/norm) if norm>1e-12 and len(t.embedding)==len(d.embedding) else 1.
                cost[i,j]=self.weights@[np.sqrt(max(mahal,0)),1-generalized_iou_3d(a,b),cosine]
        matched_detections=set()
        if cost.size:
            rows,columns=linear_sum_assignment(cost)
            for i,j in zip(rows,columns):
                if cost[i,j]>=1e8: continue
                t=self.tracks[i]; d=detections[j]
                z,H,R=_measurement(d,dt,self.dimension,self.position_noise)
                S=H@t.covariance@H.T+R
                K=np.linalg.solve(S,H@t.covariance).T
                t.x=t.x+K@(z-H@t.x)
                A=np.eye(self.dimension)-K@H
                # Joseph form preserves positive semidefiniteness under roundoff.
                t.covariance=A@t.covariance@A.T+K@R@K.T
                t.covariance=(t.covariance+t.covariance.T)/2
                p=np.asarray(d.class_probs,dtype=float); p=p/p.sum()
                t.probabilities=.75*t.probabilities+.25*p
                t.embedding=.75*t.embedding+.25*d.embedding if len(t.embedding)==len(d.embedding) else np.array(d.embedding)
                t.detection=d; t.last_seen=timestamp; t.hits+=1; t.missed=0
                matched_detections.add(j)
        self.tracks.extend(self._create(d,timestamp,dt) for j,d in enumerate(detections) if j not in matched_detections)
        self.timestamp=timestamp
        return [self._snapshot(t) for t in self.tracks if t.hits>=self.min_hits]

    def _snapshot(self,t):
        d=t.detection; position=t.x[:3].copy(); velocity=t.x[3:6].copy()
        acceleration=t.x[6:9].copy() if self.dimension==9 else np.zeros(3)
        obb=OBB(position,np.maximum(d.size,.01)/2,d.rotation)
        corners=obb.corners
        horizon=np.arange(0.,2.01,.2)
        trajectory=position+horizon[:,None]*velocity+.5*horizon[:,None]**2*acceleration
        shift=position-np.asarray(d.position)
        return TrackedEntity3D(t.identifier,SemanticClass(int(t.probabilities.argmax())),
            t.probabilities.copy(),float(t.probabilities.max()),position,velocity,
            acceleration,np.asarray(d.flow).copy(),float(np.linalg.norm(velocity)),
            float(np.arctan2(velocity[1],velocity[0])),corners.min(0),corners.max(0),
            corners,np.asarray(d.points)+shift,np.asarray(d.eigen).copy(),float("inf"),
            trajectory,covariance=t.covariance.copy(),last_observed_timestamp=t.last_seen,
            state_kind='predicted' if t.missed else 'measurement_updated')


# Name retained for callers familiar with the original specification.
Kalman9DoFTracker=KalmanMultiObjectTracker

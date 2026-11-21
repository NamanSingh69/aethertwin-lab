"""AIDE2 C47: opt-in person class persistence (`tracking.person_confirm_frames` k, `tracking.person_confirm_window` n).

A track is reported as HUMAN_PEDESTRIAN only after the person class won at least k of the track's last n observed frames
(the per-frame winner is the argmax of the smoothed class probabilities, the class the tracker reported before C47).
Before that it keeps its previously reported class (a new track: its most probable non-person class). Post-processing of
the track class only; the detector, the model and the smoothed class_probs are untouched. BEHAVIOUR-CHANGING when on
(it delays real people by up to k-1 observed frames); off by default (k=1), and off must stay bit-identical to the
tracker before C47 (the verbatim oracle below)."""
from dataclasses import dataclass,fields
from pathlib import Path
import os
import numpy as np
import pytest
from scipy.optimize import linear_sum_assignment
from core.configuration import load_config
from core.contracts import Detection3D,TrackedEntity3D,SemanticClass
from hardware_halo.state import halo_state
from tracking_and_navigation.geometry import OBB,generalized_iou_3d
from tracking_and_navigation.kalman_9dof_tracker import KalmanMultiObjectTracker,_gate,_measurement,transition_and_noise

ROOT=Path(__file__).resolve().parents[1]
HOME=Path(os.environ.get('USERPROFILE',str(Path.home())))
# The first seconds of the 600 s bench recording hold the static object (absent from the twin) that flickers into person.
RECORDING=HOME/'aethertwin_rec/wifi_stationary_600s';TWIN=HOME/'aethertwin_demo/bench_twin_lidar_only'
PERSON=int(SemanticClass.HUMAN_PEDESTRIAN);FOLIAGE=int(SemanticClass.PERMEABLE_FOLIAGE)


# ---- verbatim copy of the tracker update and snapshot before C47: the oracle ----
@dataclass
class _OldTrack:
    identifier: int
    x: np.ndarray
    covariance: np.ndarray
    detection: Detection3D
    probabilities: np.ndarray
    embedding: np.ndarray
    last_seen: float
    hits: int=1
    missed: int=0


class OldTracker:
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

    def _create(self,d,timestamp,dt):
        x=np.zeros(self.dimension); x[:3]=d.position
        if d.flow_valid: x[3:6]=np.asarray(d.flow)/dt
        P=np.eye(self.dimension); P[:3,:3]*=self.position_noise**2
        if d.flow_valid: P[3:6,3:6]*=max(d.flow_variance/dt**2,.01)
        probabilities=np.maximum(np.asarray(d.class_probs,dtype=float),1e-8)
        probabilities/=probabilities.sum()
        t=_OldTrack(self.next_id,x,P,d,probabilities,np.array(d.embedding,dtype=float),timestamp)
        self.next_id+=1; return t

    def update(self,detections,timestamp,dt=.1):
        detections=list(detections); timestamp=float(timestamp)
        if not np.isfinite(timestamp): raise ValueError("Invalid timestamp")
        if self.timestamp is not None:
            elapsed=timestamp-self.timestamp
            if elapsed<=0: raise ValueError("Tracker timestamps must strictly increase")
        else: elapsed=float(dt)
        if dt<=0: raise ValueError("Observation dt must be positive")
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
                if mahal>_gate(float(self.gate_probability),len(z)): continue
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


# ---- synthetic scenes ----
def _det(position,probs,size=(.5,.5,1.2),flow=(0.,0.,0.)):
    position=np.asarray(position,float)
    return Detection3D(position,np.asarray(size,float),np.eye(3),np.asarray(probs,float),np.asarray(flow,float),np.r_[1.,0,0,0],
                       position[None],np.ones(10),True,1e-3)


def _probs(person,foliage):
    rest=(1-person-foliage)/5;p=np.full(7,rest);p[PERSON]=person;p[FOLIAGE]=foliage;return p


STEADY=_probs(.40,.45);FLICKER=_probs(.75,.10);RECOVER=_probs(.02,.90);WALKER=_probs(.90,.02)


def flicker_scene(frames=100,period=20):
    """A static foliage-like object: person wins the smoothed argmax in exactly one frame of every `period`."""
    out=[]
    for i in range(frames):
        phase=i%period;probs=FLICKER if phase==period//2 else (RECOVER if phase==period//2+1 else STEADY)
        out.append([_det((.8,0.,.5),probs)])
    return out


def walker_scene(frames=30):
    return [[_det((1.5+.05*i,1.,.9),WALKER,flow=(.05,0.,0.)),_det((-2.,-1.,.6),STEADY)] for i in range(frames)]


def mixed_scene(frames=60,seed=7):
    """Several objects with random class probabilities, appearing and leaving (exercises creation, misses, ageing)."""
    rng=np.random.default_rng(seed);out=[]
    for i in range(frames):
        dets=[]
        for k,(x,y) in enumerate(((1.,1.),(3.,-1.),(-2.,2.),(0.,-3.))):
            if rng.random()<.2 or (k==3 and 20<=i<40):continue
            p=rng.dirichlet(np.ones(7)*.6)+1e-3;dets.append(_det((x+.03*i*(k%2),y,.8),p/p.sum()))
        out.append(dets)
    return out


def run(tracker,scene,dt=.1):return [tracker.update(dets,(i+1)*dt,dt) for i,dets in enumerate(scene)]


def assert_identical(a_frames,b_frames):
    assert len(a_frames)==len(b_frames)
    for a,b in zip(a_frames,b_frames):
        assert len(a)==len(b)
        for x,y in zip(a,b):
            for f in fields(TrackedEntity3D):
                u,v=getattr(x,f.name),getattr(y,f.name)
                if isinstance(u,np.ndarray) or isinstance(v,np.ndarray):assert np.array_equal(u,v),f.name;assert np.asarray(u).dtype==np.asarray(v).dtype
                else:assert type(u) is type(v) and (u==v or (u!=u and v!=v)),f.name


def person_frames(frames,track_id=None):
    return sum(any(int(e.semantic_class)==PERSON and (track_id is None or e.track_id==track_id) for e in ents) for ents in frames)


# ---- acceptance ----
def test_flicker_track_is_never_reported_as_a_person_with_k3_n5():
    scene=flicker_scene();raw=run(OldTracker(),scene)
    assert person_frames(raw)==5 and all(len(f)==1 for f in raw)  # the scene really flickers 1 in 20 frames
    new=run(KalmanMultiObjectTracker(person_confirm_frames=3,person_confirm_window=5),scene)
    assert person_frames(new)==0
    assert {int(e.semantic_class) for f in new for e in f}=={FOLIAGE}
    for a,b in zip(raw,new):  # only the reported class (and its confidence) changes; the smoothed probabilities do not
        assert np.array_equal(a[0].class_probs,b[0].class_probs) and np.array_equal(a[0].position,b[0].position)


def test_consistent_person_is_confirmed_on_its_third_frame():
    frames=run(KalmanMultiObjectTracker(person_confirm_frames=3,person_confirm_window=5),walker_scene())
    walker=[next(e for e in f if e.track_id==1) for f in frames]
    assert [int(e.semantic_class)==PERSON for e in walker[:3]]==[False,False,True]
    assert all(int(e.semantic_class)==PERSON for e in walker[2:])
    assert all(int(e.semantic_class)!=PERSON for f in frames for e in f if e.track_id!=1)
    assert int(walker[0].semantic_class)!=PERSON and walker[0].confidence==pytest.approx(walker[0].class_probs[int(walker[0].semantic_class)])


@pytest.mark.parametrize('k,n,latency',[(1,1,1),(2,3,2),(3,5,3),(5,8,5)])
def test_latency_to_confirm_equals_k_observed_frames(k,n,latency):
    frames=run(KalmanMultiObjectTracker(person_confirm_frames=k,person_confirm_window=n),walker_scene())
    first=next(i for i,f in enumerate(frames) if any(e.track_id==1 and int(e.semantic_class)==PERSON for e in f))
    assert first+1==latency


@pytest.mark.parametrize('scene',[flicker_scene,walker_scene,mixed_scene])
@pytest.mark.parametrize('kwargs',[{},{'person_confirm_frames':1},{'person_confirm_frames':1,'person_confirm_window':9},{'model':'cv'},{'min_hits':2}])
def test_default_is_bit_identical_to_the_tracker_before_c47(scene,kwargs):
    old={k:v for k,v in kwargs.items() if not k.startswith('person_')}
    assert_identical(run(OldTracker(**old),scene()),run(KalmanMultiObjectTracker(**kwargs),scene()))


def test_persisted_class_only_ever_suppresses_person_and_never_invents_it():
    scene=mixed_scene(200,seed=3);raw=run(OldTracker(),scene)
    new=run(KalmanMultiObjectTracker(person_confirm_frames=3,person_confirm_window=5),scene)
    for a,b in zip(raw,new):
        for x,y in zip(a,b):
            assert x.track_id==y.track_id and np.array_equal(x.class_probs,y.class_probs)
            if int(x.semantic_class)!=PERSON:assert y.semantic_class==x.semantic_class and y.confidence==x.confidence
            if int(y.semantic_class)==PERSON:assert int(x.semantic_class)==PERSON
    assert person_frames(new)<person_frames(raw)


def test_halo_sees_the_persisted_class():
    now=1000.;frames=run(KalmanMultiObjectTracker(person_confirm_frames=3,person_confirm_window=5),flicker_scene())
    raw=run(OldTracker(),flicker_scene());flick=next(i for i,f in enumerate(raw) if int(f[0].semantic_class)==PERSON)
    snapshot=lambda ents:{'entities':ents,'sensor_pose_world':np.eye(4),'processed_at_unix':now}
    assert halo_state(snapshot(raw[flick]),now=now)['nearest_track_id']==1  # the phantom warns without C47
    assert halo_state(snapshot(frames[flick]),now=now)['nearest_track_id'] is None


@pytest.mark.parametrize('k,n',[(0,5),(3,2),(-1,1)])
def test_invalid_settings_are_rejected(k,n):
    with pytest.raises(ValueError):KalmanMultiObjectTracker(person_confirm_frames=k,person_confirm_window=n)


def test_config_default_is_off_everywhere():
    for path in [ROOT/'config/system_config.yaml',*sorted((ROOT/'config/profiles').glob('*.yaml'))]:
        tracking=load_config(path)['tracking']
        assert tracking.get('person_confirm_frames')==1 and tracking.get('person_confirm_window')==1,path.name


# ---- real frames: detections captured from the parked pipeline on the bench recording (skipped where it is absent) ----
@pytest.fixture(scope='module')
def real_inputs(tmp_path_factory):
    if not RECORDING.is_dir():pytest.skip('bench recording not on this machine')
    if not TWIN.is_dir():pytest.skip('bench twin not on this machine')
    from tools.export_showcase import offline_states
    config=tmp_path_factory.mktemp('c47')/'k3.yaml'
    config.write_text(f"extends: {(ROOT/'config/profiles/hotspot.yaml').as_posix()}\ntracking:\n  person_confirm_frames: 3\n  person_confirm_window: 5\n",encoding='utf-8')
    calls=[];inits=[];original=KalmanMultiObjectTracker.update;original_init=KalmanMultiObjectTracker.__init__
    def update(self,detections,timestamp,dt=.1):
        out=original(self,detections,timestamp,dt);calls.append((list(detections),float(timestamp),float(dt),out));return out
    def init(self,*a,**k):inits.append(k);original_init(self,*a,**k)
    mp=pytest.MonkeyPatch();mp.setattr(KalmanMultiObjectTracker,'update',update);mp.setattr(KalmanMultiObjectTracker,'__init__',init)
    try:
        for _ in offline_states(RECORDING,TWIN,config,seconds=8.,lidar_only=True):pass
    finally:mp.undo()
    assert calls and sum(len(d) for d,_,_,_ in calls)>0
    return inits,calls


def test_real_frames_default_is_bit_identical_and_the_pipeline_passes_the_setting(real_inputs):
    inits,calls=real_inputs
    assert inits[-1].get('person_confirm_frames')==3 and inits[-1].get('person_confirm_window')==5
    old=OldTracker();new=KalmanMultiObjectTracker();k3=KalmanMultiObjectTracker(person_confirm_frames=3,person_confirm_window=5)
    a=[old.update(d,t,dt) for d,t,dt,_ in calls];b=[new.update(d,t,dt) for d,t,dt,_ in calls]
    assert_identical(a,b);assert_identical([out for *_,out in calls],[k3.update(d,t,dt) for d,t,dt,_ in calls])
    for x,y in zip(a,[out for *_,out in calls]):
        for p,q in zip(x,y):
            if int(q.semantic_class)==PERSON:assert int(p.semantic_class)==PERSON

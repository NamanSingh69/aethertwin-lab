"""Observed-data audits. No calibration writes or unobservable 6-DoF claims."""
import numpy as np


def summary(values):
    values=np.asarray(values,dtype=float).reshape(-1);values=values[np.isfinite(values)]
    if not len(values):return {'count':0,'mean':None,'p50':None,'p95':None,'max':None}
    return {'count':len(values),'mean':float(values.mean()),'p50':float(np.median(values)),
        'p95':float(np.percentile(values,95)),'max':float(values.max())}


class IMUAuditAccumulator:
    """Keep bounded unique IMUs, never RGB-D images or whole capture frames."""
    def __init__(self,max_samples=100000):self.samples={};self.coverage=[];self.max_samples=max_samples;self.exhausted=False
    def update(self,frame):
        stamps=np.array([s.timestamp for s in frame.imu_samples])
        self.coverage.append(bool(len(stamps)>=2 and stamps.min()<=frame.timestamp+1e-6 and stamps.max()>=frame.timestamp+frame.sweep_duration-1e-6))
        for sample in frame.imu_samples:
            if sample.timestamp not in self.samples and len(self.samples)>=self.max_samples:self.exhausted=True;continue
            self.samples[sample.timestamp]=sample
    def report(self):return _imu_summary(self.samples,self.coverage,self.exhausted)


def imu_audit(frames):
    accumulator=IMUAuditAccumulator()
    for frame in frames:accumulator.update(frame)
    return accumulator.report()


def _imu_summary(samples,coverage,exhausted):
    if len(samples)<2:return {'status':'failed','reason':'Insufficient measured IMU samples','sample_count':len(samples),'frame_coverage':coverage}
    ordered=[samples[key] for key in sorted(samples)];times=np.array([s.timestamp for s in ordered]);dt=np.diff(times)
    gyro=np.array([s.gyro for s in ordered]);accel=np.array([s.accel for s in ordered]);norm=np.linalg.norm(accel,axis=1)
    median=float(np.median(dt));rms=float(np.sqrt(np.mean(np.sum(gyro**2,axis=1))))
    return {'status':'passed' if all(coverage) and np.all(dt>0) and not exhausted else 'failed','sample_count':len(samples),'sample_budget_exhausted':exhausted,
        'measured_rate_hz':1/median if median>0 else None,'sample_gap_seconds':summary(dt),'large_gap_count':int(np.count_nonzero(dt>2.5*median)),
        'frame_coverage':coverage,'gyro_rms_rad_s':rms,'specific_force_norm_m_s2':summary(norm),
        'stationary_candidate':bool(rms<.03 and np.std(norm)<.1),
        'stationarity_limit':'IMU alone cannot distinguish rest from constant translation; no stationary prior is inferred.'}


def floor_alignment_audit(points,seed=7):
    points=np.asarray(points,dtype=float)
    base={'status':'failed','calibration_written':False,'observable_parameters':['roll','pitch','height'],
        'unobservable_parameters':['x_translation','y_translation','yaw'],'full_six_dof_calibration':False}
    if points.ndim!=2 or points.shape[1]!=3 or not np.isfinite(points).all():return {**base,'reason':'Floor observations require finite XYZ rows'}
    if len(points)<30:return {**base,'reason':'Fewer than 30 observed points','point_count':len(points),'information_rank':0}
    candidates=points[points[:,2]<=np.quantile(points[:,2],.25)+.12]
    nominal=np.c_[np.cross(candidates,[0,0,1]),np.broadcast_to([0,0,1],candidates.shape)]
    nominal_rank=int(np.linalg.matrix_rank(nominal,tol=1e-8))
    if nominal_rank<3:return {**base,'reason':'Degenerate floor support','information_rank':nominal_rank,'point_count':len(candidates)}
    rng=np.random.default_rng(seed);best=None
    for _ in range(256):
        a,b,c=candidates[rng.choice(len(candidates),3,replace=False)];normal=np.cross(b-a,c-a);length=np.linalg.norm(normal)
        if length<1e-8:continue
        normal/=length
        if normal[2]<0:normal=-normal
        if normal[2]<.9:continue
        offset=-float(normal@a);mask=np.abs(candidates@normal+offset)<.04
        if best is None or mask.sum()>best.sum():best=mask
    if best is None or best.sum()<20:return {**base,'reason':'No supported gravity-horizontal measured plane','information_rank':nominal_rank}
    support=candidates[best];center=support.mean(0);_,_,axes=np.linalg.svd(support-center,full_matrices=False);normal=axes[-1]
    if normal[2]<0:normal=-normal
    offset=-float(normal@center);span=np.ptp(support[:,:2],axis=0)
    J=np.c_[np.cross(support,normal),np.broadcast_to(normal,support.shape)];rank=int(np.linalg.matrix_rank(J,tol=1e-8))
    residual=support@normal+offset
    if rank!=3 or min(span)<.5 or normal[2]<.9:return {**base,'reason':'Insufficient observable floor extent/rank','information_rank':rank,'support_span_xy_m':span.tolist()}
    observable=J[:,[0,1,5]];variance=float(np.sum(residual**2)/max(len(support)-3,1))
    covariance=variance*np.linalg.inv(observable.T@observable)
    return {**base,'status':'passed','point_count':len(points),'support_points':len(support),'normal':normal.tolist(),'offset_m':offset,
        'height_m':float(-offset/normal[2]),'information_rank':rank,'support_span_xy_m':span.tolist(),
        'absolute_plane_residual_m':summary(np.abs(residual)),'observable_covariance':covariance.tolist(),
        'covariance_units':['rad','rad','m'],'limit':'Gravity-aligned world assumed; diagnostic plane only, no calibration/map promotion.'}


def repeatability_audit(reference,later_points):
    if reference.get('status')!='passed' or not later_points:return {'status':'not_tested','reason':'Two independent observed floor captures are required'}
    normal=np.asarray(reference['normal']);offset=reference['offset_m'];values=[]
    for points in later_points:
        residual=np.asarray(points)@normal+offset
        # Explicitly conditional floor-patch statistics; dynamic/upper surfaces
        # are excluded geometrically, without simulator semantics or identities.
        values.extend(residual[np.abs(residual)<.12].tolist())
    if len(values)<20:return {'status':'not_tested','reason':'Insufficient independent floor-patch observations'}
    return {'status':'passed','signed_floor_residual_m':summary(values),'std_m':float(np.std(values)),
        'selection':'Later capture centroids within 0.12m of first-capture plane',
        'limit':'Conditional observed floor repeatability; not whole-lab geometry, neural accuracy or extrinsic calibration accuracy.'}

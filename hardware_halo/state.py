"""Pure advisory display state from immutable snapshots, never simulator time."""
import math,time
from collections.abc import Mapping
import numpy as np
from .protocol import PIXELS


def _get(value,name,default=None):return value.get(name,default) if isinstance(value,Mapping) else getattr(value,name,default)


def render_pixels(severity,stale,watchdog,disconnected,azimuth_degrees,brightness,risk_scale):
    if disconnected:base=[20,80,255]
    elif watchdog:base=[255,0,190]
    elif stale:base=[255,150,0]
    else:base=[[0,180,100],[255,190,0],[255,70,0],[255,0,30]][severity]
    center=None if azimuth_degrees is None else int(math.floor((azimuth_degrees%360)/360*PIXELS+.5))%PIXELS
    result=[]
    for pixel in range(PIXELS):
        if disconnected or watchdog or stale:scale=255 if pixel%2==0 else 38
        elif center is None:scale=64
        else:
            distance=min((pixel-center)%PIXELS,(center-pixel)%PIXELS)
            scale=255 if distance==0 else (128 if distance==1 else 32)
        if not (disconnected or watchdog or stale):scale=scale*(128+risk_scale//2)//255
        result.append([int(channel*scale*brightness//(255*255)) for channel in base])
    return result


def halo_state(snapshot,now=None,age=None,*,stale_after_ms=1000,watchdog_ms=2000,brightness=.25):
    """Return JSON-safe 24-pixel display metadata; age override is source seconds.

    Default source age = wall time since processed_at_unix + processing_ms + acquisition_age_ms.
    A source watchdog measures no new completed snapshots, separately from slow
    processing. Firmware additionally has an independent 250ms link watchdog.
    """
    if not 0<=brightness<=1 or not math.isfinite(stale_after_ms) or not math.isfinite(watchdog_ms) or stale_after_ms<=0 or watchdog_ms<=0:raise ValueError('Invalid Halo display thresholds')
    now=time.time() if now is None else float(now)
    if not math.isfinite(now) or (age is not None and (not math.isfinite(age) or age<0)):raise ValueError('Halo age/clock must be finite and nonnegative')
    diagnostics=_get(snapshot,'diagnostics',{}) or {}
    processed=_get(snapshot,'processed_at_unix')
    processing=float(_get(diagnostics,'processing_ms',0.))
    if not math.isfinite(processing) or processing<0:processing=0.
    acquisition=float(_get(diagnostics,'acquisition_age_ms',0.))
    if not math.isfinite(acquisition) or acquisition<0:acquisition=0.
    completed_age=None if processed is None or not math.isfinite(float(processed)) else max(0.,1000*(now-float(processed)))
    source_age=(None if completed_age is None else completed_age+processing+acquisition) if age is None else float(age)*1000
    if age is not None:completed_age=max(0.,source_age-processing-acquisition)
    disconnected=snapshot is None
    stale=source_age is None or source_age>=stale_after_ms
    watchdog=completed_age is None or completed_age>=watchdog_ms
    if disconnected:watchdog=False
    source=str(_get(snapshot,'source_mode','unspecified'))
    nearest=None;azimuth=None;distance=None;severity=0;reason=None
    pose=np.asarray(_get(snapshot,'sensor_pose_world',np.eye(4)),dtype=float)
    valid_pose=pose.shape==(4,4) and np.isfinite(pose).all() and np.allclose(pose[3],[0,0,0,1]) and np.allclose(pose[:3,:3].T@pose[:3,:3],np.eye(3),atol=1e-5) and np.linalg.det(pose[:3,:3])>.999
    if valid_pose:
        for entity in _get(snapshot,'entities',()) or ():
            semantic=_get(entity,'semantic_class',_get(entity,'class_id',-1))
            person=semantic==2 or str(semantic).lower() in ('human_pedestrian','person')
            if not person or float(_get(entity,'confidence',1.))<.3:continue
            position=np.asarray(_get(entity,'position',[]),dtype=float)
            if position.shape!=(3,) or not np.isfinite(position).all():continue
            delta=pose[:3,:3].T@(position-pose[:3,3]);candidate=float(np.linalg.norm(delta))
            if distance is None or candidate<distance:
                nearest=_get(entity,'track_id');distance=candidate
                azimuth=math.degrees(math.atan2(delta[1],delta[0])) if np.linalg.norm(delta[:2])>1e-9 else None
                if azimuth is not None:azimuth=(round(azimuth*100)/100+180)%360-180
        if distance is not None:severity=3 if distance<1 else (2 if distance<2 else (1 if distance<3 else 0))
        if any(bool(_get(zone,'is_breached',False)) for zone in _get(snapshot,'geofences',()) or ()):severity=max(severity,2)
    else:stale=True;watchdog=True;reason='invalid_rig_pose'
    validity=_get(snapshot,'validity',{}) or {};voxel_centers=_get(snapshot,'voxel_centers')
    if _get(validity,'observations',True) is False or (snapshot is not None and voxel_centers is not None and not len(voxel_centers)):
        stale=True;reason='empty_or_invalid_observations'
    elif _get(validity,'localization',True) is False:
        stale=True;reason='invalid_localization';azimuth=None;nearest=None;distance=None
    if source_age is None:reason='missing_wall_clock_freshness'
    brightness_byte=int(math.floor(brightness*255+.5));risk=int(math.floor(severity/3*255+.5))
    result={'pixel_count':PIXELS,'source':source,'nearest_track_id':None if nearest is None else int(nearest),
        'distance_m':distance,'azimuth_degrees':azimuth,'severity':('clear','caution','warning','critical')[severity],
        'stale':bool(stale),'watchdog':bool(watchdog),'disconnected':disconnected,'source_age_ms':source_age,
        'processing_latency_ms':processing,'acquisition_age_ms':acquisition,'brightness':brightness_byte,'risk_scale':risk,
        'pixels':render_pixels(severity,stale,watchdog,disconnected,azimuth,brightness_byte,risk),
        'virtual':True,'physical_verified':False,'reason':reason}
    result['state_name']='disconnected' if disconnected else ('watchdog' if watchdog else ('stale' if stale else result['severity']))
    return result

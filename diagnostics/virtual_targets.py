"""Explicit offline virtual injections; never update the active twin/pipeline."""
import numpy as np
from core.contracts import Detection3D,OrientedGeofence3D
from tracking_and_navigation.geometry import OBB
from tracking_and_navigation.kalman_9dof_tracker import KalmanMultiObjectTracker
from tracking_and_navigation.risk_and_geofence import RiskGeofenceEngine
from tracking_and_navigation.safe_corridor_planner import PlannerGrid,SafeCorridorPlanner


def run_virtual_targets():
    times=np.array([0,.08,.2,.32,.5,.7,.95,1.2,1.45,1.7,1.9,2.1]);models={}
    for model in ('ca','cv'):
        tracker=KalmanMultiObjectTracker(model=model);engine=RiskGeofenceEngine();zone=OrientedGeofence3D('VIRTUAL ONLY protected zone',np.array([0,0,.8]),np.eye(3),np.array([.5,.5,.8]))
        errors=[];ids=[];alerts=[]
        for index,timestamp in enumerate(times):
            position=np.array([-1.4+1.5*timestamp,0,.8]);dt=.1 if not index else timestamp-times[index-1]
            box=OBB(position,np.array([.2,.2,.6]),np.eye(3));prob=np.eye(7)[2]
            detection=Detection3D(position,2*box.half_extents,np.eye(3),prob,np.array([1.5*dt,0,0]),np.ones(16),box.corners,np.zeros(10),True,1e-6)
            entities=tracker.update([detection],float(timestamp),float(dt));entities,zones,events=engine.evaluate(entities,[zone]);zone=zones[0];alerts.extend(events)
            ids.append(entities[0].track_id);errors.append(float(np.linalg.norm(entities[0].position-position)))
        models[model]={'maximum_position_error_m':max(errors),'stable_identity':len(set(ids))==1,
            'entered':any('entered' in value for value in alerts),'exited':any('exited' in value for value in alerts)}
    ttc=RiskGeofenceEngine.ttc([4,0,0],[-1,0,0],[0,0,0],[1,0,0],1.)
    occupied=np.zeros((12,12,3),bool);occupied[6,:,:]=True
    grid=PlannerGrid(np.zeros(3),.5,occupied,np.ones_like(occupied))
    planner=SafeCorridorPlanner(grid,radius=.1,mppi_samples=8,mppi_iterations=1)
    blocked=planner.plan([1.25,2.25,.75],[4.75,2.25,.75],ground=True)
    passed=all(item['stable_identity'] and item['maximum_position_error_m']<.05 and item['entered'] and item['exited'] for item in models.values()) and abs(ttc-1.5)<1e-10 and blocked.status=='no_path'
    return {'status':'passed' if passed else 'failed','injected_virtual':True,'active_map_modified':False,
        'models':models,'relative_motion_ttc_seconds':ttc,'expected_ttc_seconds':1.5,'blocked_route_status':blocked.status,
        'limit':'Isolated software targets and standalone blocked grid; no physical target, sensor observation or active map is fabricated.'}

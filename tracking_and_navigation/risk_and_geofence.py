"""Directional risk, exact constant-velocity sphere TTC, and OBB geofences."""
from __future__ import annotations
from dataclasses import replace
import numpy as np
from .geometry import OBB,obb_from_corners,boxes_intersect


class RiskGeofenceEngine:
    def __init__(self,sigma_base=.65,alpha_front=.75,alpha_lat=.20,
                 safety_radius=.55,protected_origin=None):
        if min(sigma_base,safety_radius)<=0 or min(alpha_front,alpha_lat)<0:
            raise ValueError("Invalid risk-field dimensions")
        self.sigma_base=float(sigma_base); self.alpha_front=float(alpha_front)
        self.alpha_lat=float(alpha_lat); self.safety_radius=float(safety_radius)
        self.protected_origin=np.zeros(3) if protected_origin is None else np.asarray(protected_origin,dtype=float)

    @staticmethod
    def ttc(pos,vel,robot_pos=None,robot_vel=None,radii=.9):
        """First nonnegative contact time for two constant-velocity spheres.

        radii is the SUM of their radii, in metres. Infinity means no predicted
        contact under this motion model; it is not a general safety guarantee.
        A stable quadratic root avoids cancellation for distant obstacles.
        """
        p0=np.zeros(3) if robot_pos is None else np.asarray(robot_pos,dtype=float)
        v0=np.zeros(3) if robot_vel is None else np.asarray(robot_vel,dtype=float)
        r=np.asarray(pos,dtype=float)-p0; v=np.asarray(vel,dtype=float)-v0
        if radii<0 or not np.isfinite(np.r_[r,v,radii]).all(): raise ValueError("Invalid TTC geometry")
        a=float(v@v); b=float(2*r@v); c=float(r@r-radii*radii)
        if c<=0: return 0.
        if a<=1e-16 or b>=0: return float("inf")
        discriminant=b*b-4*a*c
        scale=max(b*b,abs(4*a*c),1.)
        if discriminant<0 and discriminant>-1e-14*scale: discriminant=0.
        if discriminant<0: return float("inf")
        # For b<0, q is the larger positive numerator, and c/q is the entry root.
        q=-.5*(b-np.sqrt(discriminant))
        result=c/q if q>0 else -b/(2*a)
        return float(result) if result>=0 else float("inf")

    def compute_exact_ttc(self,pos,vel,entity_radius=.35,robot_vel=None):
        return self.ttc(pos,vel,self.protected_origin,robot_vel,self.safety_radius+entity_radius)

    def compute_risk(self,points,entities):
        points=np.asarray(points,dtype=float)
        if points.ndim!=2 or points.shape[1] not in (2,3): raise ValueError("Risk queries require (N,2|3)")
        log_survival=np.zeros(len(points),dtype=float)
        for ent in entities:
            velocity=np.asarray(ent.velocity)[:2]
            speed=np.linalg.norm(velocity)
            heading=np.arctan2(velocity[1],velocity[0]) if speed>1e-8 else ent.heading_rad
            co,si=np.cos(heading),np.sin(heading)
            d=points[:,:2]-np.asarray(ent.position)[:2]
            longitudinal=co*d[:,0]+si*d[:,1]
            lateral=-si*d[:,0]+co*d[:,1]
            sigma_long=np.where(longitudinal>=0,self.sigma_base+self.alpha_front*speed,self.sigma_base)
            sigma_lat=self.sigma_base+self.alpha_lat*speed
            exponent=(longitudinal/sigma_long)**2+(lateral/sigma_lat)**2
            if points.shape[1]==3:
                # Floor queries use horizontal risk; volumetric queries retain
                # vertical distance to the entity's actual bounding interval.
                dz=np.maximum(np.maximum(ent.bbox_min[2]-points[:,2],points[:,2]-ent.bbox_max[2]),0)
                exponent+=(dz/self.sigma_base)**2
            risk=np.exp(-.5*exponent)
            log_survival+=np.log1p(-np.minimum(risk,1-1e-15))
        combined=-np.expm1(log_survival)
        colors=np.column_stack([np.clip(.05+1.4*combined,0,1),
            .75*(1-.8*combined),.25*(1-combined)]).astype(np.float32)
        return combined.astype(np.float32),colors

    compute_anisotropic_floor_risk=compute_risk

    def evaluate(self,entities,geofences,robot_pos=None,robot_vel=None):
        robot_pos=self.protected_origin if robot_pos is None else np.asarray(robot_pos)
        updated=[]; memberships={g.name:[] for g in geofences}; alerts=[]
        for ent in entities:
            box=obb_from_corners(ent.obb_corners)
            breached=[]
            for gf in geofences:
                zone=OBB(gf.center,gf.half_extents,gf.rotation_matrix)
                if boxes_intersect(box,zone):
                    memberships[gf.name].append(ent.track_id); breached.append(gf.name)
            entity_radius=float(np.linalg.norm(box.half_extents))
            ttc=self.ttc(ent.position,ent.velocity,robot_pos,robot_vel,self.safety_radius+entity_radius)
            updated.append(replace(ent,ttc_seconds=ttc,is_breaching_geofence=bool(breached),
                                   breached_geofence_names=tuple(breached)))
        updated_geofences=[]
        for gf in geofences:
            ids=tuple(sorted(memberships[gf.name])); old=set(gf.breaching_track_ids)
            for identifier in set(ids)-old:
                severity="CRITICAL" if gf.severity_level>=2 else "WARNING"
                alerts.append(f"[{severity}] Track #{identifier} entered {gf.name}")
            for identifier in old-set(ids): alerts.append(f"[CLEAR] Track #{identifier} exited {gf.name}")
            updated_geofences.append(replace(gf,is_breached=bool(ids),breaching_track_ids=ids))
        return updated,updated_geofences,alerts

    def evaluate_3d_obb_geofences(self,entities,geofences):
        return self.evaluate(entities,geofences)[2]


RiskGeofenceAndPlannerEngine=RiskGeofenceEngine

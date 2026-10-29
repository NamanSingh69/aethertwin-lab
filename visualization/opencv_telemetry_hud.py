"""High-contrast telemetry HUD. Rendering can be tested without a display."""
from __future__ import annotations
import math
import textwrap
import numpy as np
import cv2
from core.contracts import PALETTE, TwinMasterFrameState
from visualization.sensor_status import sensor_status


class OpenCVTelemetryHUD:
    def __init__(self, width=1120, height=720, bounds=(-6.,6.,-5.,5.)):
        self.width, self.height = int(width), int(height)
        self.bounds = tuple(float(x) for x in bounds)
        self.window_name = "Lab Digital Twin | Telemetry"
        self._visible = False
        self._closed_by_user = False

    def bounds_for_state(self,state):
        bounds=state.diagnostics.get("traffic_grid",{}).get("bounds_xy",self.bounds)
        values=np.asarray(bounds,dtype=float)
        if values.shape!=(4,) or not np.isfinite(values).all() or values[1]<=values[0] or values[3]<=values[2]:
            raise ValueError("Traffic-grid bounds must be [xmin,xmax,ymin,ymax]")
        return tuple(values)

    def _pixel(self, xy, rect, bounds=None):
        x,y,w,h=rect; xmin,xmax,ymin,ymax=self.bounds if bounds is None else bounds
        px=x+(np.asarray(xy)[...,0]-xmin)/(xmax-xmin)*w
        py=y+h-(np.asarray(xy)[...,1]-ymin)/(ymax-ymin)*h
        return np.stack([px,py],axis=-1).astype(np.int32)

    @staticmethod
    def _text(image, text, xy, color=(205,220,232), scale=.5, thickness=1):
        cv2.putText(image,str(text),tuple(map(int,xy)),cv2.FONT_HERSHEY_SIMPLEX,scale,color,thickness,cv2.LINE_AA)

    def render(self, state: TwinMasterFrameState):
        im=np.full((self.height,self.width,3),(21,18,14),dtype=np.uint8)
        W,H=self.width,self.height
        sensing=sensor_status(state.diagnostics)
        detail_lines=textwrap.wrap(" | ".join(sensing.details),width=145)
        if len(detail_lines)>2:
            detail_lines=detail_lines[:2]
            detail_lines[-1]=detail_lines[-1][:112]+"... (full details in 3D)"
        detail_height=15*len(detail_lines)
        cv2.rectangle(im,(0,0),(W,107+detail_height),(37,31,22),-1)
        self._text(im,"LAB / DIGITAL TWIN",(24,35),(235,245,250),.85,2)
        if state.diagnostics.get("preview_source"):
            self._text(im,state.diagnostics["preview_source"],(405,35),(180,205,215),.44)
        self._text(im,f"Frame {state.frame_id:06d}  |  t={state.timestamp:.2f}s  |  {state.fps:.2f} FPS",(24,66),(.0+185,220,220),.54)
        status=state.trajectory_status.replace("_"," ").upper()
        self._text(im,f"ROUTE: {status}",(W-365,33),(110,235,150),.55)
        self._text(im,f"{len(state.entities)} TRACKS  /  {len(state.voxel_centers)} VOXELS",(W-365,64),(210,210,210),.5)
        status_color=(75,185,255) if sensing.degraded else (185,205,215)
        self._text(im,sensing.summary,(24,96),status_color,.44)
        for row,line in enumerate(detail_lines):
            self._text(im,line,(24,112+15*row),status_color,.38)
        rect=(26,126+detail_height,int(W*.63),H-224-detail_height)
        bounds=self.bounds_for_state(state)
        x,y,w,h=rect
        cv2.rectangle(im,(x-1,y-1),(x+w+1,y+h+1),(89,76,58),1)
        heat=np.asarray(state.traffic_heatmap_2d,dtype=float)
        if heat.size:
            scale=max(float(np.max(heat)),1e-8)
            heat8=(255*np.clip(heat/scale,0,1)).astype(np.uint8)
            colored=cv2.applyColorMap(np.flipud(heat8),cv2.COLORMAP_INFERNO)
            colored=cv2.resize(colored,(w,h),interpolation=cv2.INTER_LINEAR)
            im[y:y+h,x:x+w]=cv2.addWeighted(colored,.55,im[y:y+h,x:x+w],.45,0)
        for gx in np.arange(math.ceil(bounds[0]),bounds[1]+.1,max(1,math.ceil((bounds[1]-bounds[0])/24))):
            a=self._pixel([gx,bounds[2]],rect,bounds); b=self._pixel([gx,bounds[3]],rect,bounds)
            cv2.line(im,tuple(a),tuple(b),(45,41,32),1)
        for gy in np.arange(math.ceil(bounds[2]),bounds[3]+.1,max(1,math.ceil((bounds[3]-bounds[2])/24))):
            a=self._pixel([bounds[0],gy],rect,bounds); b=self._pixel([bounds[1],gy],rect,bounds)
            cv2.line(im,tuple(a),tuple(b),(45,41,32),1)
        pts=np.asarray(state.floor_grid_points)
        risk=np.asarray(state.floor_risk_values)
        if len(pts)==len(risk) and len(pts):
            keep=np.flatnonzero(risk>.15)
            if len(keep)>3000: keep=keep[::int(np.ceil(len(keep)/3000))]
            for p,r in zip(self._pixel(pts[keep,:2],rect,bounds),risk[keep]):
                if x<=p[0]<x+w and y<=p[1]<y+h:
                    cv2.circle(im,tuple(p),3,(35,int(145*(1-r)),int(90+165*r)),-1)
        for gf in state.geofences:
            # XY projection of the four z=0 local corners; geofences remain 3D in the scene.
            corners=np.array([[-1,-1,0],[1,-1,0],[1,1,0],[-1,1,0]])*gf.half_extents
            world=corners@gf.rotation_matrix.T+gf.center
            color=(80,70,255) if gf.is_breached else (190,145,50)
            cv2.polylines(im,[self._pixel(world[:,:2],rect,bounds)],True,color,2)
        path=state.planned_safe_path
        if path is not None and len(path)>1:
            cv2.polylines(im,[self._pixel(np.asarray(path)[:,:2],rect,bounds)],False,(100,255,125),3,cv2.LINE_AA)
        origin=self._pixel(state.sensor_pose_world[:2,3],rect,bounds)
        cv2.drawMarker(im,tuple(origin),(255,255,255),cv2.MARKER_TRIANGLE_UP,14,2)
        for ent in state.entities:
            p=self._pixel(ent.position[:2],rect,bounds)
            cls=int(ent.semantic_class)
            color=tuple(int(c*255) for c in PALETTE[cls][::-1])
            if ent.is_breaching_geofence: color=(70,65,255)
            cv2.circle(im,tuple(p),7,color,-1,cv2.LINE_AA)
            self._text(im,f"#{ent.track_id}",p+np.array([10,-8]),color,.44)
            end=self._pixel((ent.position+.8*ent.velocity)[:2],rect,bounds)
            cv2.arrowedLine(im,tuple(p),tuple(end),color,2,tipLength=.25)
        self._text(im,"TOP VIEW / TRAFFIC + RISK HOTSPOTS (RELATIVE RISK >0.15)",(x,y-13),(165,190,202),.44)
        sx=x+w+24
        self._text(im,"TRACK / TIME TO COLLISION",(sx,115+detail_height),(195,216,225),.5)
        for i,ent in enumerate(state.entities[:9]):
            yy=146+i*45+detail_height
            label=ent.semantic_class.name.replace("_"," ")
            self._text(im,f"#{ent.track_id}  {label[:21]}",(sx,yy),scale=.43)
            ttc="clear" if not np.isfinite(ent.ttc_seconds) else f"{ent.ttc_seconds:.2f}s"
            self._text(im,f"{ent.speed:.2f} m/s   TTC {ttc}",(sx,yy+17),(150,180,195),.40)
            danger=0 if not np.isfinite(ent.ttc_seconds) else float(np.clip(1-ent.ttc_seconds/5,0,1))
            cv2.rectangle(im,(sx,yy+24),(W-25,yy+28),(58,50,40),-1)
            if danger>0:
                cv2.rectangle(im,(sx,yy+24),(int(sx+(W-25-sx)*danger),yy+28),(65,80,245),-1)
        bottom=H-75
        diagnostics=state.diagnostics
        timing=diagnostics.get("latency_ms",diagnostics.get("processing_ms",0))
        if isinstance(timing,(float,int,np.number)):
            self._text(im,f"Processing {timing:.1f} ms  |  Queue {diagnostics.get('queue_depth',0)}  |  Dropped {diagnostics.get('dropped_frames',0)}",(24,bottom-8),(145,165,180),.43)
        alerts=list(state.active_alerts)
        if alerts:
            self._text(im,"ALERT  "+str(alerts[-1])[:130],(24,bottom+20),(95,100,255),.44)
        else:
            self._text(im,"No active geofence alerts",(24,bottom+20),(115,205,155),.44)
        self._text(im,"Use the 3D window to pause, inspect tracks, change route goals and export paths.",(24,H-19),(120,140,155),.40)
        return im

    def _check_closed(self):
        if self._visible:
            try:
                if cv2.getWindowProperty(self.window_name,cv2.WND_PROP_VISIBLE)<1:
                    self._visible=False
                    self._closed_by_user=True
            except cv2.error:
                self._visible=False
                self._closed_by_user=True
        return self._closed_by_user

    def present(self,state):
        """Render one new snapshot; event pumping is deliberately independent."""
        if self._check_closed(): return False
        if not self._visible:
            cv2.namedWindow(self.window_name,cv2.WINDOW_NORMAL)
            cv2.resizeWindow(self.window_name,self.width,self.height)
            self._visible=True
        cv2.imshow(self.window_name,self.render(state))
        return True

    def poll_events(self):
        """Service native messages and keys even when paused or awaiting frames."""
        key=cv2.waitKey(1)&0xff
        return ord('q') if self._check_closed() else key

    def show(self,state):
        """Compatibility convenience for callers presenting a fresh snapshot."""
        self.present(state)
        return self.poll_events()

    def close(self):
        if self._visible:
            try: cv2.destroyWindow(self.window_name)
            except cv2.error:
                # The user may have closed the native window since the last tick.
                self._visible=False
            self._visible=False

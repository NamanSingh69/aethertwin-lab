"""Open3D command center. All GUI/OpenCV work stays on the UI thread.

The processing thread supplies immutable snapshots through a bounded queue.
No perception state is edited by visualization; user actions are callbacks.
"""
from __future__ import annotations
from pathlib import Path
import copy
import itertools
import queue
import time
import textwrap
from collections import deque
import numpy as np
from core.contracts import PALETTE
from visualization.opencv_telemetry_hud import OpenCVTelemetryHUD
from visualization.sensor_status import sensor_status

DEFAULT_VIEW_TARGET=np.array([0.,0.,.9])
DEFAULT_VIEW_EYE=np.array([12.,-15.,13.])
LAB_BOUNDS=np.array([[-6.,-5.,0.],[6.,5.,3.5]])


def room_cutaway_mask(vertices,triangles,bounds=LAB_BOUNDS,eye=DEFAULT_VIEW_EYE):
    """Display-only triangle selection: open roof and the two near walls.

    Bounds describe the room envelope, not occupied/free-space evidence. Floor
    triangles remain visible. Neither the source mesh nor occupancy is modified.
    The near sides follow the reset camera; orbiting never changes map evidence.
    """
    vertices=np.asarray(vertices,dtype=float); triangles=np.asarray(triangles,dtype=np.int64)
    if not len(triangles): return np.ones(0,dtype=bool)
    bounds=np.asarray(bounds,dtype=float); size=bounds[1]-bounds[0]
    if bounds.shape!=(2,3) or np.any(size<=0): raise ValueError("Invalid room envelope")
    points=vertices[triangles]
    ceiling=np.all(points[:,:,2]>=bounds[0,2]+.82*size[2],axis=1)
    near_wall=np.zeros(len(triangles),dtype=bool)
    for axis in (0,1):
        margin=.045*size[axis]
        if eye[axis]>=bounds[:,axis].mean():
            near_wall|=np.all(points[:,:,axis]>=bounds[1,axis]-margin,axis=1)
        else:
            near_wall|=np.all(points[:,:,axis]<=bounds[0,axis]+margin,axis=1)
    floor=np.all(points[:,:,2]<=bounds[0,2]+.07*size[2],axis=1)
    return ~(ceiling|(near_wall&~floor))


def display_mesh(mesh,cutaway=True):
    """Return an independent render mesh, retaining source collision geometry."""
    result=copy.deepcopy(mesh)
    if cutaway and len(result.triangles):
        keep=room_cutaway_mask(np.asarray(result.vertices),np.asarray(result.triangles))
        result.remove_triangles_by_mask(~keep)
        result.remove_unreferenced_vertices()
    if len(result.vertices): result.compute_vertex_normals()
    return result


def _box_lines(corners):
    """Extract cuboid edges independently of vertex order using local planes."""
    from scipy.spatial import ConvexHull
    corners=np.asarray(corners,dtype=float)
    unique,first=np.unique(corners,axis=0,return_index=True)
    rank=np.linalg.matrix_rank(unique-unique.mean(axis=0)) if len(unique)>1 else 0
    if rank==0: return np.empty((0,2),dtype=np.int32)
    if rank==1:
        _,_,V=np.linalg.svd(unique-unique.mean(axis=0),full_matrices=False)
        projection=unique@V[0]
        return np.array([[first[np.argmin(projection)],first[np.argmax(projection)]]],np.int32)
    if rank==2:
        _,_,V=np.linalg.svd(unique-unique.mean(axis=0),full_matrices=False)
        boundary=ConvexHull(unique@V[:2].T).vertices
        return np.column_stack([first[boundary],first[np.roll(boundary,-1)]]).astype(np.int32)
    hull=ConvexHull(corners)
    edges=set()
    # Adjacent triangles on the same plane create a diagonal; discard it.
    for a,b in itertools.combinations(range(len(corners)),2):
        planes=[]
        for normal_d in hull.equations:
            if abs(normal_d[:3]@corners[a]+normal_d[3])<1e-6 and abs(normal_d[:3]@corners[b]+normal_d[3])<1e-6:
                n=normal_d[:3]
                if not any(abs(n@m)>.99999 for m in planes): planes.append(n)
        if len(planes)>=2: edges.add((a,b))
    return np.array(sorted(edges),np.int32).reshape(-1,2)


def _geofence_corners(gf):
    return np.array(list(itertools.product([-1,1],repeat=3)))*gf.half_extents@gf.rotation_matrix.T+gf.center


class CommandCenter:
    def __init__(self,on_start_goal=None,on_export=None,title="Lab Digital Twin"):
        self.on_start_goal,self.on_export,self.title=on_start_goal,on_export,str(title)
        self._mesh=None; self._inferred_mesh=None; self._cutaway=True
        self.state=None; self._geometry=set()
        self.selected_track=None; self._track_ids=[]
        self._layers={name:True for name in ("Static twin","Semantic voxels","Scene flow","Risk floor","Planned route","RGB-D inlay","Tracks","Geofences")}
        self._layers["Inferred closed mesh"]=False
        self.hud=OpenCVTelemetryHUD()
        self._closing=False
        self.metrics={};self._tick_intervals=deque(maxlen=200000)

    def set_static_mesh(self,mesh,inferred_mesh=None):
        self._mesh=copy.deepcopy(mesh) if mesh is not None else None
        self._inferred_mesh=copy.deepcopy(inferred_mesh) if inferred_mesh is not None else None

    def _show_static_mesh(self):
        self._remove("Static twin")
        if self._mesh is not None and len(self._mesh.vertices):
            render_mesh=display_mesh(self._mesh,self._cutaway)
            self._add("Static twin",render_mesh,self._material("defaultLitTransparency",(.24,.44,.53,.58)))
            self.scene.scene.show_geometry("Static twin",self._layers["Static twin"])

    def _set_cutaway(self,checked):
        self._cutaway=bool(checked)
        self._show_static_mesh()

    def _show_inferred_mesh(self,visible):
        if not visible:
            if "Inferred closed mesh" in self._geometry: self.scene.scene.show_geometry("Inferred closed mesh",False)
            return
        if "Inferred closed mesh" not in self._geometry and self._inferred_mesh is not None:
            import open3d as o3d
            wire=o3d.geometry.LineSet.create_from_triangle_mesh(self._inferred_mesh)
            wire.paint_uniform_color([.88,.58,.22])
            self._add("Inferred closed mesh",wire,self._material("unlitLine",line_width=1))
        if "Inferred closed mesh" in self._geometry: self.scene.scene.show_geometry("Inferred closed mesh",True)

    @staticmethod
    def _material(shader="defaultUnlit",color=(1,1,1,1),point_size=4,line_width=2):
        from open3d.visualization import rendering
        mat=rendering.MaterialRecord(); mat.shader=shader
        mat.base_color=list(color); mat.point_size=point_size; mat.line_width=line_width
        return mat

    def _remove(self,name):
        if name in self._geometry:
            self.scene.scene.remove_geometry(name); self._geometry.remove(name)

    def _add(self,name,geometry,material):
        self._remove(name)
        self.scene.scene.add_geometry(name,geometry,material)
        self._geometry.add(name)

    def _points(self,name,xyz,colors,point_size=4):
        import open3d as o3d
        xyz=np.asarray(xyz)
        if not len(xyz): self._remove(name); return
        pc=o3d.geometry.PointCloud()
        pc.points=o3d.utility.Vector3dVector(np.array(xyz[:,:3],dtype=float,copy=True))
        col=np.asarray(colors,dtype=float)
        if col.ndim==1: col=np.tile(col,(len(xyz),1))
        pc.colors=o3d.utility.Vector3dVector(np.clip(col,0,1))
        self._add(name,pc,self._material(point_size=point_size))

    def _lines(self,name,points,lines,color,width=2):
        import open3d as o3d
        if not len(lines): self._remove(name); return
        obj=o3d.geometry.LineSet()
        obj.points=o3d.utility.Vector3dVector(np.array(points,dtype=float,copy=True))
        obj.lines=o3d.utility.Vector2iVector(np.array(lines,dtype=np.int32,copy=True))
        col=np.asarray(color,dtype=float)
        if col.ndim==1: col=np.tile(col,(len(lines),1))
        obj.colors=o3d.utility.Vector3dVector(np.array(col,dtype=float,copy=True))
        self._add(name,obj,self._material("unlitLine",line_width=width))

    def _static_scene(self):
        points=[]; lines=[]
        for x in range(-6,7):
            n=len(points); points.extend([[x,-5,.015],[x,5,.015]]); lines.append([n,n+1])
        for y in range(-5,6):
            n=len(points); points.extend([[-6,y,.015],[6,y,.015]]); lines.append([n,n+1])
        self._lines("grid",points,lines,[.13,.23,.28],1)
        self._show_static_mesh()

    def _refresh(self,state):
        self._presented_count=getattr(self,'_presented_count',0)+1
        self.state=state
        self.frame_label.text=f"Frame {state.frame_id:06d}  |  {state.timestamp:.2f}s"
        self.performance_label.text=f"{state.fps:.2f} FPS  |  {len(state.voxel_centers):,} active voxels"
        sensing=sensor_status(state.diagnostics)
        if hasattr(self,"sensor_status_label") and sensing!=getattr(self,"_last_sensor_status",None):
            self.sensor_status_label.text=textwrap.fill(sensing.summary,width=42)
            self.sensor_detail_label.text="\n".join(textwrap.fill(detail,width=44) for detail in sensing.details)
            self.sensor_detail_label.visible=bool(sensing.details)
            self.sensor_status_label.text_color=self._sensor_colors[int(sensing.degraded)]
            self._last_sensor_status=sensing
            self.window.set_needs_layout()
        self.route_label.text=f"Route: {state.trajectory_status.replace('_',' ')}"
        self.alert_label.text="\n".join(str(x)[:64] for x in state.active_alerts[-3:]) or "No active alerts"
        self._points("Semantic voxels",state.voxel_centers,state.voxel_colors)
        # Risk is a relative hazard field, not evidence that unobserved floor
        # is traversable. Match the HUD's hotspot display threshold.
        risk_keep=np.asarray(state.floor_risk_values)>.15
        floor=np.array(state.floor_grid_points[risk_keep],copy=True)
        if len(floor): floor[:,2]=.025
        self._points("Risk floor",floor,state.floor_risk_colors[risk_keep],5)
        inlay=state.gemini_inlay_points
        if inlay is not None and len(inlay):
            rgb=inlay[:,3:6] if inlay.shape[1]>=6 else [.6,.75,.8]
            if np.asarray(rgb).max()>1: rgb=rgb/255
            self._points("RGB-D inlay",inlay[:,:3],rgb,2)
        else: self._remove("RGB-D inlay")
        xyz=np.asarray(state.voxel_centers); flow=np.asarray(state.voxel_flow_vectors)
        if len(xyz)==len(flow) and len(xyz):
            keep=np.flatnonzero(np.linalg.norm(flow,axis=1)>.015)
            if len(keep)>500: keep=keep[::int(np.ceil(len(keep)/500))]
            pts=np.concatenate([xyz[keep],xyz[keep]+flow[keep]],axis=0)
            lines=np.column_stack([np.arange(len(keep)),np.arange(len(keep))+len(keep)])
            self._lines("Scene flow",pts,lines,[.93,.88,.4],2)
        else: self._remove("Scene flow")
        path=state.planned_safe_path
        if path is not None and len(path)>1:
            self._lines("Planned route",path,np.column_stack([np.arange(len(path)-1),np.arange(1,len(path))]),[.22,1,.43],4)
        else: self._remove("Planned route")
        pts=[]; edges=[]; colors=[]
        for ent in state.entities:
            corners=np.asarray(ent.obb_corners)
            if corners.shape!=(8,3): continue
            local_edges=_box_lines(corners)
            color=np.array([1,.15,.28]) if ent.is_breaching_geofence else PALETTE[int(ent.semantic_class)]
            base=len(pts); pts.extend(corners); edges.extend(local_edges+base); colors.extend([color]*len(local_edges))
            traj=np.asarray(ent.predicted_trajectory)
            if len(traj)>1:
                base=len(pts); pts.extend(traj)
                edges.extend(np.column_stack([np.arange(len(traj)-1)+base,np.arange(1,len(traj))+base]))
                colors.extend([color*.65]*max(0,len(traj)-1))
        self._lines("Tracks",pts,edges,colors,3)
        pts=[]; edges=[]; colors=[]
        for gf in state.geofences:
            corners=_geofence_corners(gf); local=_box_lines(corners)
            base=len(pts); pts.extend(corners); edges.extend(local+base)
            color=[1,.18,.25] if gf.is_breached else [.94,.67,.20]
            colors.extend([color]*len(local))
        self._lines("Geofences",pts,edges,colors,3)
        for name,visible in self._layers.items():
            if name in self._geometry: self.scene.scene.show_geometry(name,visible)
        track_ids=[e.track_id for e in state.entities]
        names=[f"#{e.track_id}  {e.semantic_class.name.replace('_',' ')}" for e in state.entities]
        self._track_ids=track_ids
        self.track_list.set_items(names)
        if self.selected_track in track_ids: self.track_list.selected_index=track_ids.index(self.selected_track)
        elif track_ids: self.selected_track=track_ids[0]; self.track_list.selected_index=0
        else: self.selected_track=None
        self._inspect_track()

    def _inspect_track(self):
        if self.state is None: return
        ent=next((e for e in self.state.entities if e.track_id==self.selected_track),None)
        if ent is None: self.details_label.text="Select a track to inspect."; return
        p=", ".join(f"{x:.2f}" for x in ent.position)
        v=", ".join(f"{x:.2f}" for x in ent.velocity)
        ttc=f"{ent.ttc_seconds:.2f} s" if np.isfinite(ent.ttc_seconds) else "No predicted collision"
        self.details_label.text=f"Track #{ent.track_id} / {ent.confidence:.1%}\nPosition: {p} m\nVelocity: {v} m/s\nTTC: {ttc}\nGeofence: {', '.join(ent.breached_geofence_names) or 'clear'}"

    def _select_track(self,text,is_double_click):
        if text.startswith("#"):
            self.selected_track=int(text.split()[0][1:]); self._inspect_track()
            if is_double_click and self.state is not None:
                ent=next((e for e in self.state.entities if e.track_id==self.selected_track),None)
                if ent is not None:
                    self.scene.look_at(ent.position,ent.position+np.array([4,-5,4]),[0,0,1])

    @staticmethod
    def _parse_point(text):
        values=np.array([float(x.strip()) for x in text.split(",")],dtype=float)
        if values.shape!=(3,) or not np.isfinite(values).all(): raise ValueError("Enter x, y, z as three finite numbers")
        return values

    def _apply_route(self):
        try:
            start=self._parse_point(self.start_edit.text_value); goal=self._parse_point(self.goal_edit.text_value)
            if self.on_start_goal is None: raise RuntimeError("No planning callback is connected")
            self.on_start_goal(start,goal)
            self.action_label.text="Route request submitted"
        except (ValueError,RuntimeError) as exc: self.action_label.text=str(exc)

    def _export_route(self):
        try:
            if self.state is None or self.state.planned_safe_path is None or not len(self.state.planned_safe_path):
                raise ValueError("No valid route to export")
            if self.on_export is not None: result=self.on_export()
            else:
                result=Path(__file__).resolve().parents[1]/"artifacts"/"trajectory.csv"
                result.parent.mkdir(parents=True,exist_ok=True)
                np.savetxt(result,self.state.planned_safe_path,delimiter=",",header="x_m,y_m,z_m",comments="")
            self.action_label.text="Exported: "+str(result or "trajectory.csv")
        except (OSError,ValueError,RuntimeError) as exc: self.action_label.text=str(exc)

    @staticmethod
    def _toggle_pause(pause_event,pause_button):
        if pause_event.is_set(): pause_event.clear(); pause_button.text="Pause"
        else: pause_event.set(); pause_button.text="Resume"

    def _pump_presentation(self,snapshot_queue,stop_event,toggle_pause):
        """Always pump input, independently of whether a new frame is available."""
        newest=None
        while True:
            try: newest=snapshot_queue.get_nowait()
            except queue.Empty: break
        changed=newest is not None
        if newest is not None:
            self._refresh(newest)
            self.hud.present(newest)
        key=self.hud.poll_events()
        if key in (27,ord('q')): stop_event.set(); changed=True
        elif key==ord(' '): toggle_pause(); changed=True
        return changed

    def run(self,snapshot_queue,stop_event,pause_event):
        import open3d as o3d
        from open3d.visualization import gui,rendering
        app=gui.Application.instance; app.initialize()
        self.window=app.create_window(self.title,1440,900)
        self.scene=gui.SceneWidget(); self.scene.scene=rendering.Open3DScene(self.window.renderer)
        self.scene.scene.set_background([.025,.045,.065,1])
        self.scene.scene.set_lighting(rendering.Open3DScene.LightingProfile.SOFT_SHADOWS,[.4,-.6,-1])
        self.window.add_child(self.scene)
        em=self.window.theme.font_size
        panel=gui.ScrollableVert(.45*em,gui.Margins(em,em,em,em)); self.panel=panel
        title=gui.Label("LAB DIGITAL TWIN")
        panel.add_child(title)
        panel.add_child(gui.Label("Live scene / trajectory lab"))
        self.frame_label=gui.Label("Waiting for sensor frames..."); panel.add_child(self.frame_label)
        self.performance_label=gui.Label("Initializing pipeline"); panel.add_child(self.performance_label)
        self._sensor_colors=(gui.Color(.72,.81,.86),gui.Color(1.,.70,.28))
        self.sensor_status_label=gui.Label("Source not reported"); panel.add_child(self.sensor_status_label)
        self.sensor_detail_label=gui.Label(""); self.sensor_detail_label.visible=False; panel.add_child(self.sensor_detail_label)
        self.route_label=gui.Label("Route: not requested"); panel.add_child(self.route_label)
        controls=gui.Horiz(.3*em)
        pause_button=gui.Button("Pause")
        def toggle_pause():
            self._toggle_pause(pause_event,pause_button)
        pause_button.set_on_clicked(toggle_pause); controls.add_child(pause_button)
        reset=gui.Button("Reset view")
        reset.set_on_clicked(lambda:self.scene.look_at(DEFAULT_VIEW_TARGET,DEFAULT_VIEW_EYE,[0,0,1]))
        controls.add_child(reset); panel.add_child(controls)
        cutaway=gui.Checkbox("Cutaway room view"); cutaway.checked=self._cutaway
        cutaway.set_on_checked(self._set_cutaway); panel.add_child(cutaway)
        layers=gui.CollapsableVert("Layers",.2*em,gui.Margins(.3*em,0,0,0))
        for name in self._layers:
            check=gui.Checkbox("Risk hotspots" if name=="Risk floor" else name); check.checked=self._layers[name]
            if name=="Inferred closed mesh": check.enabled=self._inferred_mesh is not None
            def on_checked(checked,layer=name):
                self._layers[layer]=checked
                if layer=="Inferred closed mesh": self._show_inferred_mesh(checked)
                if layer in self._geometry: self.scene.scene.show_geometry(layer,checked)
            check.set_on_checked(on_checked); layers.add_child(check)
        layers.add_child(gui.Label("Hotspots: relative risk >0.15"))
        panel.add_child(layers)
        panel.add_child(gui.Label("Start [x, y, z] metres"))
        self.start_edit=gui.TextEdit(); self.start_edit.text_value=", ".join(map(str,getattr(self,'initial_start',[-1.375,-4.375,.625]))); panel.add_child(self.start_edit)
        panel.add_child(gui.Label("Goal [x, y, z] metres"))
        self.goal_edit=gui.TextEdit(); self.goal_edit.text_value=", ".join(map(str,getattr(self,'initial_goal',[2.125,4.125,.625]))); panel.add_child(self.goal_edit)
        routes=gui.Horiz(.3*em)
        apply=gui.Button("Plan route"); apply.set_on_clicked(self._apply_route); routes.add_child(apply)
        export=gui.Button("Export CSV"); export.set_on_clicked(self._export_route); routes.add_child(export)
        panel.add_child(routes)
        self.action_label=gui.Label("Orbit: left drag / Zoom: wheel"); panel.add_child(self.action_label)
        panel.add_child(gui.Label("Tracks (double click to focus)"))
        self.track_list=gui.ListView(); self.track_list.set_max_visible_items(4)
        self.track_list.set_on_selection_changed(self._select_track); panel.add_child(self.track_list)
        self.details_label=gui.Label("Select a track to inspect."); panel.add_child(self.details_label)
        self.alert_label=gui.Label("No active alerts"); panel.add_child(self.alert_label)
        self.window.add_child(panel)
        def layout(context):
            rect=self.window.content_rect; pw=max(330,int(22*em))
            self.scene.frame=gui.Rect(rect.x,rect.y,max(1,rect.width-pw),rect.height)
            panel.frame=gui.Rect(rect.get_right()-pw,rect.y,pw,rect.height)
        self.window.set_on_layout(layout)
        self._static_scene()
        bounds=o3d.geometry.AxisAlignedBoundingBox(np.array([-6,-5,0.]),np.array([6,5,3.5]))
        self.scene.setup_camera(60,bounds,DEFAULT_VIEW_TARGET)
        self.scene.look_at(DEFAULT_VIEW_TARGET,DEFAULT_VIEW_EYE,[0,0,1])
        def close():
            stop_event.set(); self._closing=True; self.hud.close(); return True
        self.window.set_on_close(close)
        previous_tick=time.perf_counter();tick_count=0
        def tick():
            nonlocal previous_tick,tick_count
            now=time.perf_counter();self._tick_intervals.append(now-previous_tick);previous_tick=now;tick_count+=1
            if self._closing: return False
            if stop_event.is_set():
                self._closing=True; self.hud.close(); app.quit(); return False
            return self._pump_presentation(snapshot_queue,stop_event,toggle_pause)
        self.window.set_on_tick_event(tick)
        try: app.run()
        finally:
            stop_event.set();self.hud.close()
            intervals=np.array(self._tick_intervals)*1000
            self.metrics={'ticks':tick_count,'frames_presented':getattr(self,'_presented_count',0),
                'tick_gap_ms_p50':float(np.percentile(intervals,50)) if len(intervals) else None,
                'tick_gap_ms_p95':float(np.percentile(intervals,95)) if len(intervals) else None,
                'tick_gap_ms_max':float(intervals.max()) if len(intervals) else None,
                'input_actions_verified':False,'desktop_permission_pending':True}


Open3DCommandCenter=CommandCenter

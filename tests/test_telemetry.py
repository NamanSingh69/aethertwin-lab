import asyncio
import json
import queue
import time
import threading
from types import SimpleNamespace
from dataclasses import replace
import numpy as np
from core.contracts import TwinMasterFrameState, TrackedEntity3D, SemanticClass
from visualization.opencv_telemetry_hud import OpenCVTelemetryHUD
from visualization.websocket_broadcaster import WebSocketBroadcaster,frame_message
from visualization.open3d_command_center import CommandCenter,_box_lines,room_cutaway_mask,display_mesh
from visualization.sensor_status import sensor_status


def sample_state():
    entity=TrackedEntity3D(1,SemanticClass.HUMAN_PEDESTRIAN,np.eye(7)[2],.95,
        np.array([0.,0.,.8]),np.array([.1,0,0]),np.zeros(3),np.array([.01,0,0]),.1,0.,
        np.array([-.2,-.2,0.]),np.array([.2,.2,1.6]),np.array([[x,y,z] for x in [-.2,.2] for y in [-.2,.2] for z in [0.,1.6]]),
        np.zeros((1,3)),np.zeros(10),float("inf"),np.array([[0,0,.8],[.1,0,.8]]))
    return TwinMasterFrameState(3,.3,10.,np.eye(4),(entity,),np.array([[0.,0.,.8]]),
        np.array([[0.,1.,.5]]),np.zeros((1,3)),np.array([[0.,0.,0.]]),np.array([.4]),
        np.array([[.4,.6,0.]]),np.zeros((20,24)),(),np.array([[-1.,-1.,.3],[1.,1.,.3]]),
        diagnostics={"processing_ms":50.},trajectory_status="success")


def test_json_schema_and_infinite_ttc():
    payload=frame_message(sample_state())
    text=json.dumps(payload,allow_nan=False)
    assert "Infinity" not in text
    assert payload["schema_version"]==1
    assert payload["tracks"][0]["ttc_seconds"] is None
    assert payload["tracks"][0]["ttc_status"]=="no_collision"
    assert payload["floor_risk"]["risk"]==[float(np.float32(.4))]
    assert payload["traffic_heatmap"]["source_shape"]==[20,24]
    assert payload["traffic_heatmap"]["world_registration_available"] is False


def test_bounded_risk_traffic_preserve_peaks_and_registration():
    points=np.column_stack([np.arange(103),np.zeros(103),np.zeros(103)])
    risks=np.linspace(0,1,103); colors=np.column_stack([risks,1-risks,np.zeros(103)])
    heat=np.zeros((101,89)); heat[50,47]=17.; heat[100,88]=31.
    state=replace(sample_state(),floor_grid_points=points,floor_risk_values=risks,
        floor_risk_colors=colors,traffic_heatmap_2d=heat,
        diagnostics={"traffic_grid":{"bounds_xy":[-6,6,-5,5],"resolution_m":.25,"decay_seconds":30.}})
    payload=frame_message(state,max_floor_samples=7,max_traffic_cells=24)
    floor=payload["floor_risk"]; traffic=payload["traffic_heatmap"]
    assert len(floor["centers"])<=7 and max(floor["risk"])==1.
    for idx,p,r,c in zip(floor["source_indices"],floor["centers"],floor["risk"],floor["colors"]):
        assert np.array_equal(p,state.floor_grid_points[idx])
        assert r==state.floor_risk_values[idx] and np.array_equal(c,state.floor_risk_colors[idx])
    pooled=np.array(traffic["values"])
    assert pooled.size<=24 and pooled.max()==31.
    assert traffic["bounds_xy"]==[-6,6,-5,5] and traffic["world_registration_available"]
    for r,(a,b) in enumerate(zip(traffic["source_row_edges"][:-1],traffic["source_row_edges"][1:])):
        for c,(i,j) in enumerate(zip(traffic["source_column_edges"][:-1],traffic["source_column_edges"][1:])):
            assert pooled[r,c]==heat[a:b,i:j].max()
    assert np.array_equal(state.traffic_heatmap_2d,heat)
    json.dumps(payload,allow_nan=False)


def test_empty_telemetry_and_invalid_limits():
    import pytest
    state=replace(sample_state(),floor_grid_points=np.empty((0,3)),floor_risk_values=np.empty(0),
        floor_risk_colors=np.empty((0,3)),traffic_heatmap_2d=np.empty((0,0)))
    payload=frame_message(state)
    assert payload["floor_risk"]["centers"]==[] and payload["traffic_heatmap"]["values"]==[]
    for name in ("max_voxels","max_floor_samples","max_traffic_cells"):
        with pytest.raises(ValueError): frame_message(state,**{name:0})
        with pytest.raises(ValueError): WebSocketBroadcaster(**{name:0})


def test_room_cutaway_opens_roof_near_walls_and_preserves_floor_and_source():
    import open3d as o3d
    mesh=o3d.geometry.TriangleMesh.create_box(12.,10.,3.5).translate([-6.,-5.,0.])
    vertices=np.asarray(mesh.vertices).copy(); triangles=np.asarray(mesh.triangles).copy()
    keep=room_cutaway_mask(vertices,triangles)
    centers=vertices[triangles].mean(axis=1)
    assert np.all(keep[np.isclose(centers[:,2],0)])
    assert not np.any(keep[np.isclose(centers[:,2],3.5)])
    assert not np.any(keep[np.isclose(centers[:,0],6)])
    assert not np.any(keep[np.isclose(centers[:,1],-5)])
    assert np.all(keep[np.isclose(centers[:,0],-6)])
    assert np.all(keep[np.isclose(centers[:,1],5)])
    opened=display_mesh(mesh)
    assert len(opened.triangles)==6 and len(mesh.triangles)==12
    assert np.array_equal(np.asarray(mesh.vertices),vertices)
    assert np.array_equal(np.asarray(mesh.triangles),triangles)
    full=display_mesh(mesh,False)
    assert len(full.triangles)==12 and full is not mesh
    center=CommandCenter(); center.set_static_mesh(mesh,inferred_mesh=full)
    assert center._layers["Inferred closed mesh"] is False and center._cutaway


def test_native_geometry_accepts_immutable_float64_snapshot_buffers():
    center=CommandCenter(); captured={}
    center._add=lambda name,geometry,material:captured.update({name:geometry})
    points=np.array([[0.,0.,0.],[1.,1.,1.]],dtype=np.float64); points.setflags(write=False)
    colors=np.array([[1.,0.,0.],[0.,1.,0.]],dtype=np.float64); colors.setflags(write=False)
    lines=np.array([[0,1]],np.int32); lines.setflags(write=False)
    center._points('points',points,colors)
    center._lines('lines',points,lines,colors[:1])
    assert np.array_equal(np.asarray(captured['points'].points),points)
    assert np.array_equal(np.asarray(captured['lines'].points),points)
    assert not points.flags.writeable and not colors.flags.writeable


def test_headless_hud_is_visible_and_does_not_mutate():
    state=sample_state(); before=state.floor_grid_points.copy()
    image=OpenCVTelemetryHUD().render(state)
    assert image.shape==(720,1120,3)
    assert image.dtype==np.uint8 and image.max()>200
    assert np.array_equal(before,state.floor_grid_points)


def test_sensor_source_mode_is_current_and_never_inferred_from_recorded_transport():
    for diagnostics,mode in (
        ({'source_mode':'sim','synthetic':True},'Sim'),
        ({'source_mode':'replay','synthetic':True},'Replay'),
        ({'source_mode':'live','transport':'Unitree SDK UDP'},'Live'),
        ({'synthetic':True},'Sim'),
        ({'transport':'Unitree SDK UDP'},'Source not reported'),
        ({},'Source not reported')):
        status=sensor_status(diagnostics)
        assert status.mode==mode and not status.degraded and status.summary==mode


def test_sensor_status_only_reports_explicit_degradation_and_preserves_errors():
    diagnostics={'source_mode':'live','s2_degraded':True,'s2_error':'Serial timeout at COM7',
        'painting':'camera_unavailable'}
    before=dict(diagnostics); status=sensor_status(diagnostics)
    assert status.degraded
    assert status.issues==('Optional S2 degraded','RGB-D unavailable')
    assert 'S2: Serial timeout at COM7' in status.details
    assert diagnostics==before
    assert not sensor_status({'camera_available':True,'painted_fraction':0.}).degraded
    assert not sensor_status({'s2_degraded':False}).degraded
    assert sensor_status({'camera_available':False}).issues==('RGB-D unavailable',)
    assert sensor_status({'s2_error':'capture failed'}).details==('S2: capture failed',)
    assert 'Synthetic data reported' in sensor_status({'source_mode':'live','synthetic':True}).issues
    long_error='capture exception: '+('long diagnostic reason '*80)
    assert sensor_status({'s2_degraded':True,'s2_error':long_error}).details==('S2: '+long_error,)


def test_hud_renders_degraded_sensor_badge_and_failure_details(monkeypatch):
    state=replace(sample_state(),diagnostics={'source_mode':'live','s2_degraded':True,
        's2_error':'Serial scan timed out','painting':'camera_unavailable'})
    hud=OpenCVTelemetryHUD(); labels=[]
    original=hud._text
    def capture(image,text,xy,color=(205,220,232),scale=.5,thickness=1):
        labels.append(str(text)); return original(image,text,xy,color,scale,thickness)
    monkeypatch.setattr(hud,'_text',capture)
    image=hud.render(state)
    assert 'Live | Optional S2 degraded | RGB-D unavailable' in labels
    assert any('S2: Serial scan timed out' in text for text in labels)
    band=image[80:140]
    assert np.any(np.all(band==[75,185,255],axis=2))
    assert image.shape==(720,1120,3)
    assert not sample_state().diagnostics.get('s2_degraded')


def test_hud_does_not_label_unreported_absent_inlay_as_sensor_failure(monkeypatch):
    state=replace(sample_state(),diagnostics={'source_mode':'sim','synthetic':True})
    assert state.gemini_inlay_points is None
    hud=OpenCVTelemetryHUD(); labels=[]; original=hud._text
    def capture(image,text,xy,*args,**kwargs):
        labels.append(str(text)); return original(image,text,xy,*args,**kwargs)
    monkeypatch.setattr(hud,'_text',capture)
    hud.render(state)
    assert 'Sim' in labels
    assert not any('unavailable' in text or 'degraded' in text for text in labels)


def test_pause_empty_queue_space_resume_pumps_events_without_redrawing(monkeypatch):
    """Use actual presentation/polling methods with a fake native event source."""
    import cv2
    center=CommandCenter(); presented=[]; refreshed=[]; displayed=[]; polls=[]
    center._refresh=lambda state:refreshed.append(state.frame_id)
    def render(state):
        presented.append(state.frame_id); return np.zeros((2,2,3),np.uint8)
    monkeypatch.setattr(center.hud,'render',render)
    monkeypatch.setattr(cv2,'namedWindow',lambda *args:None)
    monkeypatch.setattr(cv2,'resizeWindow',lambda *args:None)
    monkeypatch.setattr(cv2,'imshow',lambda *args:displayed.append(args[1]))
    monkeypatch.setattr(cv2,'getWindowProperty',lambda *args:1.)
    keys=iter([ord(' '),-1,ord(' '),ord('q')])
    def wait_key(delay):
        polls.append(delay); return next(keys)
    monkeypatch.setattr(cv2,'waitKey',wait_key)
    snapshots=queue.Queue(maxsize=2); snapshots.put(sample_state())
    stopped=threading.Event(); paused=threading.Event(); button=SimpleNamespace(text='Pause')
    toggle=lambda:center._toggle_pause(paused,button)
    assert center._pump_presentation(snapshots,stopped,toggle)
    assert paused.is_set() and button.text=='Resume' and snapshots.empty()
    assert not center._pump_presentation(snapshots,stopped,toggle)
    assert paused.is_set()
    assert center._pump_presentation(snapshots,stopped,toggle)
    assert not paused.is_set() and button.text=='Pause'
    assert center._pump_presentation(snapshots,stopped,toggle) and stopped.is_set()
    assert presented==refreshed==[3] and len(displayed)==1
    assert polls==[1,1,1,1]


def test_paused_hud_close_is_detected_without_another_snapshot(monkeypatch):
    import cv2
    center=CommandCenter(); center.hud._visible=True
    calls=[]
    monkeypatch.setattr(cv2,'waitKey',lambda delay:calls.append(delay) or -1)
    monkeypatch.setattr(cv2,'getWindowProperty',lambda *args:0.)
    stopped=threading.Event()
    assert center._pump_presentation(queue.Queue(),stopped,lambda:None)
    assert stopped.is_set() and center.hud._closed_by_user and not center.hud._visible
    assert calls==[1]
    assert center.hud.present(sample_state()) is False


def test_hud_registers_every_overlay_using_snapshot_bounds():
    original=sample_state(); offset=np.array([120.,200.,0.])
    entity=replace(original.entities[0],position=original.entities[0].position+offset,
        bbox_min=original.entities[0].bbox_min+offset,bbox_max=original.entities[0].bbox_max+offset,
        obb_corners=original.entities[0].obb_corners+offset,
        predicted_trajectory=original.entities[0].predicted_trajectory+offset)
    pose=np.array(original.sensor_pose_world,copy=True); pose[:3,3]+=offset
    translated=replace(original,entities=(entity,),sensor_pose_world=pose,
        floor_grid_points=original.floor_grid_points+offset,planned_safe_path=original.planned_safe_path+offset,
        diagnostics={**original.diagnostics,"traffic_grid":{"bounds_xy":[114,126,195,205]}})
    hud=OpenCVTelemetryHUD(); expected=hud.render(original); actual=hud.render(translated)
    assert np.array_equal(expected,actual)
    assert hud.bounds==(-6.,6.,-5.,5.)
    assert np.array_equal(hud.render(original),expected)


def test_runtime_preview_round_trips_actual_telemetry_without_fixture_layers():
    from tools.render_scene_preview import snapshot_from_message
    original=sample_state(); payload=frame_message(original); restored=snapshot_from_message(payload)
    assert restored.frame_id==original.frame_id
    assert restored.diagnostics['preview_source']=='RUNTIME SNAPSHOT'
    assert not restored.diagnostics['preview_missing_layers']
    for name in ('voxel_centers','voxel_flow_vectors','floor_grid_points','floor_risk_values','traffic_heatmap_2d','planned_safe_path'):
        assert np.array_equal(getattr(original,name),getattr(restored,name))
    assert np.array_equal(original.entities[0].obb_corners,restored.entities[0].obb_corners)
    assert np.isinf(restored.entities[0].ttc_seconds)
    assert len(restored.entities[0].member_points)==0
    old=dict(payload); old.pop('floor_risk'); old.pop('traffic_heatmap')
    assert snapshot_from_message(old).diagnostics['preview_missing_layers']==('floor_risk','traffic_heatmap')


def test_cuboid_edges_independent_of_vertex_order():
    corners=sample_state().entities[0].obb_corners
    assert len(_box_lines(corners))==12
    assert len(_box_lines(corners[[7,2,3,1,4,5,6,0]]))==12


def test_degenerate_box_rendering_does_not_crash():
    corners=sample_state().entities[0].obb_corners.copy()
    corners[:,2]=0
    assert len(_box_lines(corners))==4
    corners[:,1]=0
    assert len(_box_lines(corners))==1
    corners[:,0]=0
    assert len(_box_lines(corners))==0


def test_route_input_validation():
    assert np.array_equal(CommandCenter._parse_point("1, 2, 0.3"),[1,2,.3])
    import pytest
    with pytest.raises(ValueError): CommandCenter._parse_point("nan, 2, 3")
    with pytest.raises(ValueError): CommandCenter._parse_point("1,2")


def test_actual_websocket_delivery_and_shutdown():
    async def client(server):
        from websockets.asyncio.client import connect
        async with connect(f"ws://127.0.0.1:{server.port}") as connection:
            server.publish(sample_state())
            message=json.loads(await asyncio.wait_for(connection.recv(),3))
            assert message["frame_id"]==3
            assert message["tracks"][0]["semantic_class"]==2
            assert message["floor_risk"]["source_count"]==1
            assert message["traffic_heatmap"]["shape"]==[20,24]
    server=WebSocketBroadcaster(port=0).start()
    try:
        for _ in range(20): server.publish(sample_state())
        assert server._incoming.qsize()<=1
        asyncio.run(client(server))
    finally: server.close()
    assert not server._thread.is_alive()


def test_rendering_backend_requires_explicit_supported_selection(monkeypatch):
    import pytest
    from visualization import render_backend
    monkeypatch.setattr(render_backend,"_configured",None)
    with pytest.raises(ValueError): render_backend.configure_rendering("silent_fallback")
    monkeypatch.setattr(render_backend.sys,"platform","linux")
    with pytest.raises(RuntimeError): render_backend.configure_rendering("mesa")
    assert render_backend.configure_rendering("native")["name"]=="Native Open3D rendering"

"""Render the measured room and deterministic display fixtures to a PNG.

This component test creates its own hidden surface; it never reads or controls
desktop apps. Overlay fixtures exercise presentation, not perception accuracy or
route safety. Full interactive acceptance remains a separate computer-use test.
"""
from __future__ import annotations
import argparse
import gc
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def snapshot_from_message(payload):
    """Read our versioned JSON telemetry for display, without inference labels."""
    import numpy as np
    from core.contracts import SemanticClass,TrackedEntity3D,TwinMasterFrameState,OrientedGeofence3D
    if payload.get('schema_version')!=1 or payload.get('message_type')!='twin.frame':
        raise ValueError('Expected version 1 twin.frame telemetry')
    def matrix(value,cols): return np.asarray(value,dtype=float).reshape(-1,cols)
    entities=[]
    for track in payload.get('tracks',[]):
        corners=matrix(track['obb_corners'],3); velocity=np.asarray(track['velocity'],dtype=float)
        if corners.shape!=(8,3): raise ValueError('Track must contain eight OBB corners')
        ttc=track.get('ttc_seconds')
        entities.append(TrackedEntity3D(int(track['track_id']),SemanticClass(track['semantic_class']),
            track['class_probs'],float(track['confidence']),track['position'],velocity,
            track.get('acceleration',[0,0,0]),np.zeros(3),float(np.linalg.norm(velocity)),
            float(np.arctan2(velocity[1],velocity[0])),corners.min(axis=0),corners.max(axis=0),
            corners,np.empty((0,3)),np.zeros(10),float('inf') if ttc is None else float(ttc),
            matrix(track.get('predicted_trajectory',[]),3),bool(track.get('breached_geofences')),
            tuple(track.get('breached_geofences',[]))))
    zones=tuple(OrientedGeofence3D(**value) for value in payload.get('geofences',[]))
    voxels=payload.get('voxels',{}); floor=payload.get('floor_risk',{}); traffic=payload.get('traffic_heatmap',{})
    diagnostics=dict(payload.get('diagnostics',{})); diagnostics['preview_source']='RUNTIME SNAPSHOT'
    if traffic.get('world_registration_available'):
        diagnostics['traffic_grid']={key:traffic.get(key) for key in ('bounds_xy','resolution_m','decay_seconds')}
    diagnostics['preview_missing_layers']=[name for name in ('floor_risk','traffic_heatmap') if name not in payload]
    shape=traffic.get('shape',[0,0]); heat=np.asarray(traffic.get('values',[]),dtype=float).reshape(shape)
    return TwinMasterFrameState(int(payload['frame_id']),float(payload['timestamp']),float(payload['fps']),
        payload['sensor_pose_world'],tuple(entities),matrix(voxels.get('centers',[]),3),
        matrix(voxels.get('colors',[]),3),matrix(voxels.get('flow',[]),3),matrix(floor.get('centers',[]),3),
        np.asarray(floor.get('risk',[])),matrix(floor.get('colors',[]),3),heat,zones,
        None if payload.get('path') is None else matrix(payload['path'],3),
        active_alerts=tuple(payload.get('alerts',[])),diagnostics=diagnostics,
        trajectory_status=str(payload.get('trajectory_status','not_requested')))


def fixture_state():
    import numpy as np
    from core.contracts import PALETTE,SemanticClass,TrackedEntity3D,TwinMasterFrameState,OrientedGeofence3D
    from tracking_and_navigation.risk_and_geofence import RiskGeofenceEngine
    from tracking_and_navigation.traffic_heatmap import TrafficHeatmap
    entities=[]; clouds=[]; colors=[]; flows=[]
    rng=np.random.default_rng(194)
    for identifier,cls,position,half,velocity in (
        (1,SemanticClass.HUMAN_PEDESTRIAN,[-1.4,-1.2,.9],[.26,.26,.9],[.45,.25,0.]),
        (2,SemanticClass.DYNAMIC_ROBOT_CART,[1.8,1.2,.48],[.6,.4,.48],[-.35,-.2,0.]),
        (3,SemanticClass.NEW_STATIC_OBSTACLE,[.7,-2.4,.36],[.35,.35,.36],[0.,0.,0.])):
        p=np.array(position); half=np.array(half); velocity=np.array(velocity)
        samples=rng.uniform(-1,1,(700,3))
        axes=rng.integers(0,3,len(samples)); samples[np.arange(len(samples)),axes]=rng.choice([-1,1],len(samples))
        samples=samples*half+p
        corners=np.array(list(itertools.product([-1,1],repeat=3)))*half+p
        trajectory=p+np.arange(0,3.1,.2)[:,None]*velocity
        ent=TrackedEntity3D(identifier,cls,np.eye(7)[int(cls)],.95,p,velocity,np.zeros(3),
            .1*velocity,float(np.linalg.norm(velocity)),float(np.arctan2(velocity[1],velocity[0])),
            p-half,p+half,corners,samples,np.zeros(10),float('inf'),trajectory)
        entities.append(ent); clouds.append(samples); colors.append(np.tile(PALETTE[int(cls)],(len(samples),1)))
        flows.append(np.tile(velocity*.1,(len(samples),1)))
    geofence=OrientedGeofence3D("Protected workstation",np.array([-1.2,-.5,.9]),np.eye(3),np.array([.8,.9,.9]))
    risk_engine=RiskGeofenceEngine()
    entities,zones,alerts=risk_engine.evaluate(entities,[geofence],np.array([-2.,-3.,.6]),np.zeros(3))
    x,y=np.meshgrid(np.arange(-6,6,.25),np.arange(-5,5,.25))
    floor=np.c_[x.ravel(),y.ravel(),np.full(x.size,.08)]
    risk,risk_colors=risk_engine.compute_risk(floor,entities)
    traffic=TrafficHeatmap()
    for i in range(20): heat=traffic.update(entities,i*.1)
    path=np.array([[-3.,-4.,.35],[-2.8,-2.8,.35],[-2.6,-.8,.35],[-2.,1.5,.35],[-1.,3.8,.35]])
    return TwinMasterFrameState(194,1.9,10.,np.eye(4),tuple(entities),np.concatenate(clouds),
        np.concatenate(colors),np.concatenate(flows),floor,risk,risk_colors,heat,tuple(zones),path,
        active_alerts=tuple(alerts),diagnostics={'display_fixture':True,'preview_source':'DISPLAY FIXTURE'},trajectory_status='display_fixture')


class _TrackList:
    def set_items(self,items): self.items=items
    selected_index=-1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['native','mesa'],default='native')
    parser.add_argument('--mesa-dir',type=Path,default=ROOT/'assets/rendering/mesa')
    parser.add_argument('--twin',type=Path,default=ROOT/'assets/twin_seed7')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/room_cutaway_preview.png')
    parser.add_argument('--full-mesh',action='store_true')
    parser.add_argument('--state',type=Path,help='Versioned last_state.json telemetry from an actual runtime run')
    args=parser.parse_args()
    from visualization.render_backend import configure_rendering
    backend=configure_rendering(args.backend,args.mesa_dir if args.backend=='mesa' else None)
    import numpy as np
    import open3d as o3d
    from open3d.visualization import gui,rendering
    from visualization.open3d_command_center import CommandCenter,room_cutaway_mask,DEFAULT_VIEW_TARGET,DEFAULT_VIEW_EYE
    app=gui.Application.instance; app.initialize()
    width,height=1280,800
    window=app.create_window('Generated room component',width,height,flags=1); window.show(False)
    scene=rendering.Open3DScene(window.renderer)
    center=CommandCenter(); center._cutaway=not args.full_mesh
    mesh=o3d.io.read_triangle_mesh(str(args.twin/'mesh.ply'))
    if not len(mesh.triangles): raise ValueError('Measured mesh has no triangles')
    center.set_static_mesh(mesh)
    center.scene=SimpleNamespace(scene=scene)
    for name in ('frame_label','performance_label','route_label','alert_label','details_label'):
        setattr(center,name,SimpleNamespace(text=''))
    center.track_list=_TrackList()
    try:
        scene.set_background([.025,.045,.065,1])
        scene.set_lighting(rendering.Open3DScene.LightingProfile.SOFT_SHADOWS,[.4,-.6,-1])
        state=snapshot_from_message(json.loads(args.state.read_text(encoding='utf-8'))) if args.state else fixture_state()
        center._static_scene(); center._refresh(state)
        scene.camera.look_at(DEFAULT_VIEW_TARGET,DEFAULT_VIEW_EYE,[0,0,1])
        scene.camera.set_projection(42,width/height,.1,60,rendering.Camera.FovType.Vertical)
        image=app.render_to_image(scene,width,height); pixels=np.asarray(image)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        if not o3d.io.write_image(str(args.output),image): raise OSError(args.output)
        import cv2
        hud_path=args.output.with_name(args.output.stem+'_hud.png')
        if not cv2.imwrite(str(hud_path),center.hud.render(state)): raise OSError(hud_path)
        selected=room_cutaway_mask(np.asarray(mesh.vertices),np.asarray(mesh.triangles))
        report={'test':'generated_room_component_not_desktop_capture','backend':backend['name'],
            'mesh_source':str(args.twin/'mesh.ply'),'measured_triangles':len(mesh.triangles),
            'display_triangles':len(mesh.triangles) if args.full_mesh else int(selected.sum()),
            'cutaway':not args.full_mesh,'fixture_overlays':args.state is None,
            'state_source':str(args.state) if args.state else 'deterministic_display_fixture',
            'runtime_frame_id':state.frame_id if args.state else None,
            'runtime_trajectory_status':state.trajectory_status if args.state else None,
            'missing_layers':state.diagnostics.get('preview_missing_layers',[]),
            'route_collision_validated':False if args.state is None else None,
            'route_note':'Component renderer does not revalidate a runtime route.',
            'perception_accuracy_claimed':False,'shape':list(pixels.shape),'pixel_std':float(pixels.std()),
            'render_geometry':sorted(center._geometry),'normal_shutdown':True,
            'passed':bool(pixels.std()>10)}
    finally:
        center.scene=None; scene=None; gc.collect(); window.close(); window=None
        app.run_one_tick(); app.quit(); app.run_one_tick()
    args.output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report),flush=True)
    print('Normal scene component teardown completed',flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())

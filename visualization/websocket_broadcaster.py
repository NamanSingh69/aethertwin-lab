"""Loopback telemetry with bounded publisher and per-client queues.

Publishing never waits for a network client. Slow clients lose intermediate
snapshots; peers that cannot send a frame within two seconds are disconnected.
"""
from __future__ import annotations
import asyncio
from dataclasses import asdict,is_dataclass
import json
import math
import queue
import threading
import numpy as np


def _json_value(value):
    if isinstance(value,np.ndarray): return _json_value(value.tolist())
    if isinstance(value,np.generic): return _json_value(value.item())
    if isinstance(value,float) and not math.isfinite(value): return None
    if isinstance(value,dict): return {str(k):_json_value(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)): return [_json_value(v) for v in value]
    if is_dataclass(value): return _json_value(asdict(value))
    return value


def _floor_message(state,max_samples):
    """Retain the peak-risk location in each bounded contiguous source block."""
    points=np.asarray(state.floor_grid_points); risk=np.asarray(state.floor_risk_values)
    colors=np.asarray(state.floor_risk_colors); n=len(points)
    if len(risk)!=n or len(colors)!=n: raise ValueError("Floor-risk arrays must be aligned")
    stride=max(1,int(np.ceil(n/max_samples)))
    indices=np.array([start+int(np.argmax(risk[start:start+stride])) for start in range(0,n,stride)],dtype=np.int64)
    return {"centers":points[indices],"risk":risk[indices],"colors":colors[indices],
            "source_indices":indices,"source_count":n,"source_block_size":stride,
            "sampling":"peak_risk_per_source_block","source_peak_risk":float(risk.max()) if n else None}


def _traffic_message(state,max_cells):
    """Max pool without dropping small traffic peaks; publish source bin edges."""
    heat=np.asarray(state.traffic_heatmap_2d)
    if heat.ndim!=2: raise ValueError("Traffic heatmap must be two dimensional")
    rows,cols=heat.shape
    if heat.size<=max_cells:
        row_edges=np.arange(rows+1); col_edges=np.arange(cols+1); pooled=heat
    else:
        out_cols=min(cols,max(1,int(np.sqrt(max_cells*cols/max(rows,1)))))
        out_rows=min(rows,max(1,max_cells//out_cols))
        row_edges=np.linspace(0,rows,out_rows+1,dtype=np.int64)
        col_edges=np.linspace(0,cols,out_cols+1,dtype=np.int64)
        pooled=np.maximum.reduceat(np.maximum.reduceat(heat,row_edges[:-1],axis=0),col_edges[:-1],axis=1)
    metadata=state.diagnostics.get("traffic_grid",{})
    return {"values":pooled,"source_shape":[rows,cols],"shape":list(pooled.shape),
            "source_row_edges":row_edges,"source_column_edges":col_edges,"aggregation":"max_pool",
            "units":"decayed_occupancy_seconds","bounds_xy":metadata.get("bounds_xy"),
            "resolution_m":metadata.get("resolution_m"),"decay_seconds":metadata.get("decay_seconds"),
            "world_registration_available":metadata.get("bounds_xy") is not None,
            "axis_order":["world_y_increasing","world_x_increasing"]}


def frame_message(state,max_voxels=20000,max_floor_samples=4096,max_traffic_cells=4096):
    """Additive schema v1. Nonfinite TTC uses null plus an explicit status.

    Floor samples preserve block peaks; traffic is max pooled with exact source
    index edges. Limits bound payload size without changing runtime snapshots.
    """
    if min(max_voxels,max_floor_samples,max_traffic_cells)<1: raise ValueError("Telemetry limits must be positive")
    n=len(state.voxel_centers); stride=max(1,int(np.ceil(n/max_voxels)))
    tracks=[]
    for ent in state.entities:
        tracks.append({"track_id":ent.track_id,"semantic_class":int(ent.semantic_class),
                       "semantic_name":ent.semantic_class.name,"class_probs":ent.class_probs,
                       "confidence":ent.confidence,"position":ent.position,"velocity":ent.velocity,
                       "acceleration":ent.acceleration,"obb_corners":ent.obb_corners,
                       "eigen_invariants":ent.eigen_invariants,"covariance":ent.covariance,
                       "speed":ent.speed,"heading_rad":ent.heading_rad,"member_count":len(ent.member_points),
                       "state_kind":ent.state_kind,"last_observed_timestamp":ent.last_observed_timestamp,
                       "measurement_age_seconds":None if ent.last_observed_timestamp is None else max(0.,state.timestamp-ent.last_observed_timestamp),
                       "predicted_trajectory":ent.predicted_trajectory,"ttc_seconds":ent.ttc_seconds,
                       "ttc_status":"collision_predicted" if np.isfinite(ent.ttc_seconds) else "no_collision",
                       "breached_geofences":ent.breached_geofence_names})
    from hardware_halo import halo_state
    result={"schema_version":1,"message_type":"twin.frame","frame_id":state.frame_id,
            "timestamp":state.timestamp,"units":{"length":"m","time":"s","velocity":"m/s"},
            "coordinate_frame":"world_z_up","fps":state.fps,"sensor_pose_world":state.sensor_pose_world,
            "identity":{"lab_id":state.lab_id,"rig_id":state.rig_id,"map_revision":state.map_revision,
                        "calibration_id":state.calibration_id},
            "source_mode":state.source_mode if state.source_mode!='unspecified' else state.diagnostics.get('source_mode','unspecified'),
            "processed_at_unix":state.processed_at_unix,"validity":state.validity,
            "halo":halo_state(state,**state.diagnostics.get('halo_options',{})),
            "tracks":tracks,"geofences":state.geofences,"alerts":state.active_alerts,
            "voxels":{"centers":state.voxel_centers[::stride],"colors":state.voxel_colors[::stride],
                      "flow":state.voxel_flow_vectors[::stride],"sampling_stride":stride,"source_count":n},
            "floor_risk":_floor_message(state,max_floor_samples),
            "traffic_heatmap":_traffic_message(state,max_traffic_cells),
            "path":state.planned_safe_path,"trajectory_status":state.trajectory_status,
            "diagnostics":state.diagnostics}
    return _json_value(result)


class WebSocketBroadcaster:
    def __init__(self,host="127.0.0.1",port=8765,max_voxels=20000,max_floor_samples=4096,max_traffic_cells=4096):
        self.host,self.port,self.max_voxels=str(host),int(port),int(max_voxels)
        self.max_floor_samples,self.max_traffic_cells=int(max_floor_samples),int(max_traffic_cells)
        if min(self.max_voxels,self.max_floor_samples,self.max_traffic_cells)<1: raise ValueError("Telemetry limits must be positive")
        self._incoming=queue.Queue(maxsize=1)
        self._stop=threading.Event(); self._ready=threading.Event()
        self._thread=None; self._error=None; self._clients={}; self._latest=None
        self.dropped_frames=0; self.published_frames=0

    def start(self):
        if self._thread is not None: raise RuntimeError("Broadcaster already started")
        self._thread=threading.Thread(target=self._run,name="twin-websocket",daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            self.close(); raise TimeoutError("WebSocket server did not start")
        if self._error is not None: raise RuntimeError("WebSocket server failed") from self._error
        return self

    def publish(self,state):
        if self._thread is None or self._stop.is_set():
            raise RuntimeError("Start the broadcaster before publishing")
        try: self._incoming.put_nowait(state)
        except queue.Full:
            try: self._incoming.get_nowait()
            except queue.Empty: return
            self.dropped_frames+=1
            try: self._incoming.put_nowait(state)
            except queue.Full: self.dropped_frames+=1

    async def _writer(self,websocket,q):
        try:
            while True:
                message=await q.get()
                await asyncio.wait_for(websocket.send(message),timeout=2)
        except Exception:
            await websocket.close(code=1013,reason="Telemetry consumer cannot keep up")

    async def _handler(self,websocket):
        q=asyncio.Queue(maxsize=2); self._clients[websocket]=q
        if self._latest is not None: q.put_nowait(self._latest)
        writer=asyncio.create_task(self._writer(websocket,q))
        try: await websocket.wait_closed()
        finally:
            self._clients.pop(websocket,None); writer.cancel()
            await asyncio.gather(writer,return_exceptions=True)

    async def _serve(self):
        from websockets.asyncio.server import serve
        async with serve(self._handler,self.host,self.port,max_size=1024,ping_interval=20,ping_timeout=20) as server:
            self.port=server.sockets[0].getsockname()[1]
            self._ready.set()
            while not self._stop.is_set():
                try: state=self._incoming.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(.01); continue
                self._latest=json.dumps(frame_message(state,self.max_voxels,self.max_floor_samples,self.max_traffic_cells),allow_nan=False,separators=(",",":"))
                self.published_frames+=1
                for q in tuple(self._clients.values()):
                    if q.full(): q.get_nowait()
                    q.put_nowait(self._latest)
                await asyncio.sleep(0)

    def _run(self):
        try: asyncio.run(self._serve())
        except Exception as exc:
            self._error=exc; self._ready.set()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive(): raise RuntimeError("WebSocket thread did not stop")

    def __enter__(self): return self.start()
    def __exit__(self,*args): self.close()

"""FastAPI browser service. Slow clients cannot block the perception producer."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import queue
import threading
import time

from fastapi import FastAPI,Request,WebSocket,WebSocketDisconnect,HTTPException
from fastapi.responses import FileResponse,JSONResponse,Response

from .auth import SessionAuth,is_loopback
from .controls import ControlPlane
from .protocol import SceneEncoder,EncodedScene


class SceneHub:
    def __init__(self,voxel_size=.18,max_voxels=20000,max_clients=8):
        self.encoder=SceneEncoder(voxel_size,max_voxels);self.max_clients=int(max_clients)
        self.incoming=queue.Queue(maxsize=1);self.stop=threading.Event();self._thread=None
        self._lock=threading.Lock();self._subscribers={};self._latest=None
        self.dropped_snapshots=0;self.encoded_frames=0;self.error=None

    @property
    def latest(self):
        with self._lock:return self._latest

    def start(self):
        if self._thread is not None:return self
        self._thread=threading.Thread(target=self._run,name='scene-encoder',daemon=False);self._thread.start();return self

    def publish(self,state):
        if self.stop.is_set():return
        try:self.incoming.put_nowait(state)
        except queue.Full:
            try:self.incoming.get_nowait()
            except queue.Empty:pass
            self.dropped_snapshots+=1
            try:self.incoming.put_nowait(state)
            except queue.Full:self.dropped_snapshots+=1

    def subscribe(self,loop,callback):
        with self._lock:
            if len(self._subscribers)>=self.max_clients:raise ValueError('Scene client capacity reached')
            token=object();self._subscribers[token]={'loop':loop,'callback':callback,'pending':None,'scheduled':False,'coalesced':False};latest=self._latest
        if latest is not None:callback(latest,True)
        return token

    def unsubscribe(self,token):
        with self._lock:self._subscribers.pop(token,None)

    def _deliver(self,token):
        with self._lock:
            subscriber=self._subscribers.get(token)
            if subscriber is None:return
            encoded=subscriber['pending'];force=subscriber['coalesced'];callback=subscriber['callback']
            subscriber.update(pending=None,scheduled=False,coalesced=False)
        if encoded is not None:callback(encoded,force)

    def _run(self):
        while not self.stop.is_set():
            try:state=self.incoming.get(timeout=.1)
            except queue.Empty:continue
            try:
                encoded=state if isinstance(state,EncodedScene) else self.encoder.encode(state)
                with self._lock:
                    self._latest=encoded;schedules=[]
                    for token,subscriber in self._subscribers.items():
                        if subscriber['pending'] is not None:subscriber['coalesced']=True
                        subscriber['pending']=encoded
                        if not subscriber['scheduled']:
                            subscriber['scheduled']=True;schedules.append((token,subscriber['loop']))
                self.encoded_frames+=1;self.error=None
                for token,loop in schedules:
                    try:loop.call_soon_threadsafe(self._deliver,token)
                    except RuntimeError:self.unsubscribe(token)
            except Exception as exc:self.error=f'{type(exc).__name__}: {exc}'

    def status(self):
        return {'encoded_frames':self.encoded_frames,'coalesced_snapshots':self.dropped_snapshots,
                'queue_capacity':1,'queue_depth':self.incoming.qsize(),'clients':len(self._subscribers),'error':self.error}

    def close(self):
        self.stop.set()
        if self._thread:
            self._thread.join(timeout=10)
            if self._thread.is_alive():raise RuntimeError('Scene encoder failed to stop')


async def bounded_json(request,limit=32768):
    data=bytearray()
    async for chunk in request.stream():
        if len(data)+len(chunk)>limit:raise HTTPException(413,'Request exceeds command limit')
        data.extend(chunk)
    try:
        value=json.loads(data,parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        if not isinstance(value,dict):raise ValueError('Object required')
        return value
    except (ValueError,UnicodeDecodeError,RecursionError) as exc:raise HTTPException(400,'Invalid JSON object') from exc


def create_app(runtime=None,*,hub=None,controls=None,host='127.0.0.1',asset_root=None,frontend_root=None,
               operator_token=None,viewer_token=None,allowed_origins=(),local_development=True,commissioning=None):
    auth=SessionAuth(host,operator_token,viewer_token,local_development)
    hub=hub or SceneHub();controls=controls or ControlPlane()
    asset_root=Path(asset_root).resolve() if asset_root else None
    frontend_root=Path(frontend_root).resolve() if frontend_root else None
    allowed_origins=set(allowed_origins)

    @asynccontextmanager
    async def lifespan(app):
        hub.start()
        try:
            if commissioning is not None:commissioning.start()
            if runtime is not None:runtime.start(hub,controls)
            yield
        finally:
            try:
                if runtime is not None:runtime.close()
            finally:
                try:
                    if commissioning is not None:await asyncio.to_thread(commissioning.close)
                finally:hub.close()

    app=FastAPI(title='AetherTwin',version='2.0.0',lifespan=lifespan,docs_url=None,redoc_url=None)
    app.state.hub=hub;app.state.controls=controls;app.state.auth=auth

    def role_for(connection):
        if auth.local_development and not is_loopback(connection.url.hostname or ''):return None
        peer=connection.client.host if connection.client else None
        return auth.role(connection.cookies.get('aethertwin_session'),peer)

    def require_role(connection,operator=False):
        role=role_for(connection)
        if role is None:raise HTTPException(401,'Sign in to this private command center')
        if operator and role!='operator':raise HTTPException(403,'Operator permission required')
        return role

    def check_origin(connection):
        origin=connection.headers.get('origin')
        own=f"{connection.url.scheme.replace('ws','http')}://{connection.headers.get('host','')}"
        if origin and origin!=own and origin not in allowed_origins:raise HTTPException(403,'Origin is not allowed')

    if commissioning is not None:
        from .commissioning import attach_commissioning_routes
        attach_commissioning_routes(app,commissioning,require_role,check_origin)

    @app.middleware('http')
    async def headers(request,call_next):
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['X-Frame-Options']='DENY'
        if request.url.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
        return response

    @app.post('/api/v2/session')
    async def login(request:Request):
        check_origin(request);data=await bounded_json(request,4096)
        try:cookie,role=auth.create(data.get('token'))
        except ValueError as exc:raise HTTPException(401,'Invalid access credential') from exc
        response=JSONResponse({'role':role,'expires_in':auth.ttl})
        response.set_cookie('aethertwin_session',cookie,httponly=True,samesite='strict',secure=not auth.local_development,max_age=auth.ttl)
        return response

    @app.delete('/api/v2/session')
    async def logout(request:Request):
        check_origin(request);response=JSONResponse({'status':'signed_out'});response.delete_cookie('aethertwin_session');return response

    @app.get('/api/v2/bootstrap')
    async def bootstrap(request:Request,include_latest:bool=True):
        role=require_role(request);latest=hub.latest;scene=None if latest is None else latest.scene
        details={} if runtime is None else runtime.bootstrap()
        capabilities=details.pop('capabilities',{action:True for action in controls.actions})
        return {'protocol_version':2,'identity':(scene or {}).get('identity',details.get('identity',{})),
                'source_mode':(scene or {}).get('source_mode',details.get('source_mode','unspecified')),
                'hardware_status':'not_tested','map_revision':controls.map_revision,
                'map_manifest_url':'/api/v2/assets/manifest.json' if asset_root else None,
                'stream_url':'/ws/v2/scene','role':role,'control_revision':controls.revision,
                'capabilities':{action:role=='operator' and bool(capabilities.get(action,False)) for action in controls.actions},'latest':scene if include_latest else None,
                'telemetry':hub.status(),**details}

    @app.get('/api/v2/state')
    async def state(request:Request):
        require_role(request);latest=hub.latest
        return {'ready':False,'telemetry':hub.status()} if latest is None else latest.scene

    @app.get('/api/v2/health')
    async def health(request:Request):
        require_role(request)
        return {'telemetry':hub.status(),'controls':controls.status(),
                'runtime':None if runtime is None else runtime.bootstrap(),'hardware_verified':False}

    @app.get('/api/v2/commands')
    async def command_history(request:Request):require_role(request);return controls.status()

    @app.post('/api/v2/commands')
    async def command(request:Request):
        require_role(request,True);check_origin(request)
        result=await asyncio.to_thread(controls.submit,await bounded_json(request))
        return JSONResponse(result,status_code=202 if result['status']=='accepted' else 409)

    @app.get('/api/v2/trajectory.csv')
    async def trajectory(request:Request):
        require_role(request)
        if runtime is None:raise HTTPException(409,'No trajectory runtime')
        try:text=await asyncio.to_thread(runtime.trajectory_csv)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        return Response(text,media_type='text/csv',headers={'Content-Disposition':'attachment; filename="trajectory.csv"'})

    @app.get('/api/v2/assets/{filename:path}')
    async def asset(filename:str,request:Request):
        require_role(request)
        if asset_root is None or '..' in Path(filename).parts or not (filename.endswith('manifest.json') or filename.endswith('.glb')):
            raise HTTPException(404,'Unknown scene asset')
        path=(asset_root/filename).resolve()
        if not path.is_relative_to(asset_root) or not path.is_file():raise HTTPException(404,'Unknown scene asset')
        return FileResponse(path,media_type='model/gltf-binary' if path.suffix=='.glb' else 'application/json')

    @app.websocket('/ws/v2/scene')
    async def stream(websocket:WebSocket):
        if role_for(websocket) is None:
            await websocket.close(code=4401,reason='Authentication required');return
        try:check_origin(websocket)
        except HTTPException:
            await websocket.close(code=4403,reason='Origin is not allowed');return
        await websocket.accept();outgoing=asyncio.Queue(maxsize=2);closed=False
        def enqueue(encoded,force_keyframe=False):
            if closed:return
            if outgoing.full():
                while not outgoing.empty():outgoing.get_nowait()
                force_keyframe=True
            outgoing.put_nowait(encoded.keyframe if force_keyframe else encoded.incremental)
        try:token=hub.subscribe(asyncio.get_running_loop(),enqueue)
        except ValueError:
            await websocket.close(code=1013,reason='Client capacity reached');return
        async def writer():
            try:
                while True:
                    packet=await outgoing.get()
                    await asyncio.wait_for(websocket.send_bytes(packet),timeout=2)
            except Exception:
                await websocket.close(code=1013,reason='Scene consumer is too slow')
        task=asyncio.create_task(writer())
        try:
            while True:
                message=await websocket.receive_text()
                if len(message)>1024:await websocket.close(code=1009);break
                try:data=json.loads(message)
                except ValueError:await websocket.close(code=1003);break
                if isinstance(data,dict) and data.get('type')=='resync':
                    latest=hub.latest
                    if latest:
                        while not outgoing.empty():outgoing.get_nowait()
                        enqueue(latest,True)
        except WebSocketDisconnect:pass
        finally:
            closed=True;hub.unsubscribe(token);task.cancel();await asyncio.gather(task,return_exceptions=True)

    @app.get('/{path:path}')
    async def frontend(path:str):
        if path.startswith(('api/','ws/')):raise HTTPException(404,'Unknown API endpoint')
        if frontend_root is None:raise HTTPException(404,'Build the browser frontend first')
        candidate=(frontend_root/path).resolve()
        if not candidate.is_relative_to(frontend_root):raise HTTPException(404)
        if candidate.is_file():return FileResponse(candidate)
        index=frontend_root/'index.html'
        if index.is_file() and not Path(path).suffix:return FileResponse(index)
        raise HTTPException(404)
    return app

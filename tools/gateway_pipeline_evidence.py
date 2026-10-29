"""Actual four-frame replay -> verified TLS edge -> minimal gateway evidence.

This isolated acceptance runner binds loopback only and never accesses port8080.
It changes no production source, device, calibration or existing recording.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, socket, ssl, subprocess, sys, threading, time
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def wait_for(predicate, seconds=45, label='condition'):
    deadline=time.monotonic()+seconds;last=None
    while time.monotonic()<deadline:
        try:
            result=predicate()
            if result:return result
        except (ConnectionError,OSError) as exc:last=exc
        time.sleep(.03)
    raise AssertionError(f'Timed out waiting for {label}: {last}')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(output,gateway_python):
    import httpx, numpy as np, torch, uvicorn
    from websockets.sync.client import connect
    from command_center.runtime import BrowserRuntime
    from command_center.service import SceneHub,create_app
    from command_center.controls import ControlPlane
    from command_center.protocol import SceneAccumulator,decode_packet
    from core.configuration import load_config
    from ingestion.recorder_replayer import ReplaySource
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=False)
    started=time.time();clock=time.monotonic();torch.set_num_threads(2)
    spec=importlib.util.spec_from_file_location('gateway_fixture_helpers',ROOT/'tests/test_gateway.py')
    helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
    tls=output/'tls';tls.mkdir()
    cert,key=helpers.tls_files.__wrapped__(SimpleNamespace(mktemp=lambda _:tls))
    ca=ssl.create_default_context(cafile=str(cert))
    edge_port,gateway_port=free_port(),free_port()
    while gateway_port==edge_port:gateway_port=free_port()
    edge_url=f'https://127.0.0.1:{edge_port}';gateway_url=f'https://127.0.0.1:{gateway_port}'
    cfg=load_config(ROOT/'config/profiles/laptop.yaml')
    runtime=None;edge_server=None;edge_thread=None;gateway=None;gateway_log=None
    client=edge_client=ws=None
    report={'schema_version':1,'source':'actual_sample_recording_LabPipeline','hardware_verified':False,
        'started_at_unix':started,'loopback_only':True,'untouched_endurance_port':8080,
        'configuration':cfg,'torch_threads':2,'gateway_python':str(Path(gateway_python).resolve()),
        'concurrency':'Runs concurrently with the parent-owned port8080 30-minute endurance; timings are not an isolated throughput benchmark.',
        'checks':{},'snapshots':[],'commands':[],'limits':[
            'Replay uses synthetic observations previously recorded by the ordinary sensor simulator.',
            'No physical sensor, robot, external service, firewall or calibration change.',
            'Browser wire behavior is checked with the reference accumulator and a real authenticated WebSocket, not visual browser interaction.',
            'Edge-process crash and hardware reconnection are not exercised; gateway outage/restart is exercised.']}
    try:
        probe="import importlib.util,json; print(json.dumps({k:importlib.util.find_spec(k) is not None for k in ('torch','open3d','mujoco','cv2','numpy','fastapi','httpx','websockets')}))"
        minimal=subprocess.run([str(gateway_python),'-c',probe],cwd=ROOT,text=True,capture_output=True,check=True)
        report['gateway_environment']=json.loads(minimal.stdout)
        assert not any(report['gateway_environment'][name] for name in ('torch','open3d','mujoco','cv2'))
        runtime=BrowserRuntime(cfg,root=ROOT,mode='replay',recording=ROOT/'assets/sample_recording',
            twin=ROOT/'assets/twin_seed7',output=output/'edge',record=output/'local_recordings',max_frames=4)
        runtime.paused=True
        hub=SceneHub(cfg['mapping']['voxel_size']);controls=ControlPlane()
        app=create_app(runtime,hub=hub,controls=controls,asset_root=runtime.asset_root,
            operator_token=helpers.EDGE_OPERATOR,viewer_token=helpers.EDGE_VIEWER,local_development=False)
        edge_server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=edge_port,log_level='error',
            access_log=False,proxy_headers=False,ssl_certfile=str(cert),ssl_keyfile=str(key),
            timeout_graceful_shutdown=2,timeout_keep_alive=2,ws_max_size=4096))
        edge_thread=threading.Thread(target=edge_server.run,name='owned-actual-TLS-edge',daemon=False)
        edge_thread.start();wait_for(lambda:edge_server.started,label='TLS edge start')
        edge_client=httpx.Client(base_url=edge_url,verify=ca,timeout=15,trust_env=False)
        assert edge_client.get('/api/v2/bootstrap').status_code==401
        assert edge_client.post('/api/v2/session',json={'token':helpers.EDGE_OPERATOR}).status_code==200

        def start_gateway():
            nonlocal gateway,gateway_log,client
            env=os.environ.copy();env.update(AETHERTWIN_UPSTREAM_TOKEN=helpers.EDGE_OPERATOR,
                AETHERTWIN_OPERATOR_TOKEN=helpers.LOCAL_OPERATOR,AETHERTWIN_VIEWER_TOKEN=helpers.LOCAL_VIEWER,
                PYTHONUNBUFFERED='1')
            gateway_log=(output/'gateway.log').open('a',encoding='utf-8')
            gateway=subprocess.Popen([str(gateway_python),'-m','command_center.gateway','--upstream',edge_url,
                '--port',str(gateway_port),'--asset-cache',str(output/'gateway_cache'),'--ca-file',str(cert),
                '--ssl-certfile',str(cert),'--ssl-keyfile',str(key)],cwd=ROOT,env=env,
                stdout=gateway_log,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            client=httpx.Client(base_url=gateway_url,verify=ca,timeout=15,trust_env=False)
            def login():
                if gateway.poll() is not None:raise AssertionError('Owned gateway process exited')
                try:return client.post('/api/v2/session',json={'token':helpers.LOCAL_OPERATOR}).status_code==200
                except httpx.TransportError:return False
            wait_for(login,label='gateway TLS login')
            wait_for(lambda:client.get('/api/v2/bootstrap',params={'include_latest':'false'}).json().get('gateway',{}).get('connected'),label='authenticated upstream')

        def stop_gateway():
            nonlocal gateway,gateway_log,client
            if client is not None:client.close();client=None
            if gateway is not None:
                # Only this runner's child process is stopped. The actual edge
                # producer/recorder remains alive and continues independently.
                gateway.terminate();gateway.wait(timeout=15);gateway=None
            if gateway_log is not None:gateway_log.close();gateway_log=None

        def command(action,payload=None,direct=False):
            destination=edge_client if direct else client
            boot=destination.get('/api/v2/bootstrap',params={'include_latest':'false'}).json()
            request={'request_id':f'actual-{len(report["commands"])+1}-{action}',
                'map_revision':boot['map_revision'],'expected_revision':boot['control_revision'],
                'action':action,'payload':payload or {}}
            response=destination.post('/api/v2/commands',json=request);ack=response.json()
            assert response.status_code==202 and ack['status']=='accepted',ack
            wait_for(lambda:controls.applied_revision>=ack['revision'],label=f'{action} edge processing boundary')
            actual=next(row for row in controls.status()['history'] if row['request_id']==request['request_id'])
            assert actual['status']=='applied',actual
            report['commands'].append({'request':request,'ack':ack,'applied':actual,'via':'edge_direct_during_gateway_outage' if direct else 'gateway'})
            return ack

        def frame_done(count):
            wait_for(lambda:runtime.error or (runtime.pipeline.frames>=count and (runtime.paused or runtime.completed)),label=f'paused actual frame {count}')
            assert runtime.error is None,runtime.error
            wait_for(lambda:hub.latest is not None and hub.latest.sequence>=count,label='edge wire encoding')
            wait_for(lambda:runtime.recorder.status()['written_frames']>=count,label='local raw recording')

        def browser_socket():
            cookie='; '.join(f'{k}={v}' for k,v in client.cookies.items())
            return connect(gateway_url.replace('https:','wss:')+'/ws/v2/scene',ssl=ca,
                additional_headers={'Cookie':cookie},origin=gateway_url,open_timeout=15,max_size=16*1024**2)

        def compare_packet(packet,label,accumulator):
            header,arrays=decode_packet(packet);accumulator.apply(packet)
            edge_header,edge_arrays=decode_packet(hub.latest.keyframe)
            assert header['sequence']==edge_header['sequence']
            assert header['stream_id']==edge_header['stream_id']
            assert header['scene']['tracks']==edge_header['scene']['tracks']
            assert header['scene']['identity']==edge_header['scene']['identity']
            assert header['scene']['source_mode']=='replay'
            expected={tuple(k):tuple(edge_arrays['voxels.'+name][i] for name in ('centers','colors','flow'))
                for i,k in enumerate(edge_arrays['voxels.keys'])}
            assert set(accumulator.voxels)==set(expected)
            for key in expected:
                for actual,wanted in zip(accumulator.voxels[key],expected[key]):np.testing.assert_array_equal(actual,wanted)
            for name in ('path','floor_risk.centers','floor_risk.risk','floor_risk.colors','traffic_heatmap.values','gemini_inlay'):
                if name in edge_arrays:np.testing.assert_array_equal(arrays[name],edge_arrays[name])
            snapshot={'label':label,'sequence':header['sequence'],'kind':header['kind'],
                'stream_id':header['stream_id'],'voxels':len(expected),'tracks':len(header['scene']['tracks']),
                'trajectory_status':header['scene']['trajectory_status'],'exact_voxels_tracks_identity_and_auxiliary_arrays':True,
                'packet_bytes':len(packet),'packet_sha256':hashlib.sha256(packet).hexdigest(),
                'processing_ms':runtime.pipeline.last_state.diagnostics.get('processing_ms')}
            report['snapshots'].append(snapshot);(output/f'{label}.atwn').write_bytes(packet)
            return header

        start_gateway();report['checks']['configured_CA_authentication']=True
        command('step');frame_done(1)
        wait_for(lambda:client.get('/api/v2/bootstrap',params={'include_latest':'false'}).json()['gateway']['frames_relayed']>=1,label='first actual relay frame')
        accumulator=SceneAccumulator();ws=browser_socket();compare_packet(ws.recv(timeout=15),'frame1_keyframe',accumulator)
        command('pause');assert runtime.paused

        # Select a short demonstrably clear segment entirely from the observed
        # current planner grid, rather than assuming unobserved cells are free.
        planner=runtime.pipeline.planner;grid=planner.grid;z=grid.index(runtime.pipeline.robot_position)[2]
        candidates=np.argwhere(planner._center_clear[:,:,z]);points=grid.center(np.c_[candidates,np.full(len(candidates),z)])
        reference=runtime.pipeline.robot_position.copy();order=np.argsort(np.linalg.norm(points-reference,axis=1))
        selected=None
        for i in order[:80]:
            start=points[i];distance=np.linalg.norm(points-start,axis=1)
            neighbors=np.flatnonzero((distance>=.5)&(distance<=1.))
            for j in neighbors:
                goal=points[j]
                if planner.segment_clear(start,goal,0.,2.,runtime.pipeline.last_state.entities):selected=(start,goal);break
            if selected is not None:break
        assert selected is not None,'Measured map has no short safe observed segment'
        command('set_start_goal',{'start':selected[0].tolist(),'goal':selected[1].tolist()})
        command('resume');wait_for(lambda:not runtime.paused,label='resume applied')
        command('pause');frame_done(2)
        np.testing.assert_array_equal(runtime.pipeline.goal,selected[1]);assert runtime.pipeline.pending_goal is None
        header=compare_packet(ws.recv(timeout=20),'frame2_delta',accumulator)
        assert header['kind']=='delta'
        report['checks']['revisioned_pause_resume_set_goal_acknowledged_and_applied']=True
        csv=client.get('/api/v2/trajectory.csv');assert csv.status_code==200,csv.text
        assert csv.text==runtime.trajectory_csv();(output/'trajectory.csv').write_text(csv.text,encoding='utf-8')
        exported=np.loadtxt(output/'trajectory.csv',delimiter=',',skiprows=1);assert len(exported)>=2
        report['checks']['trajectory_csv_exact_to_local_validated_path']=True
        report['selected_observed_start_goal']=[v.tolist() for v in selected]

        # Exercise an actual upstream connection failure without shutting down
        # the producer: close its WebSocket protocol, then verify reconnect and
        # a newly connected browser receiving the actual current keyframe.
        before=client.get('/api/v2/bootstrap',params={'include_latest':'false'}).json()['gateway']['reconnects']
        protocols=[p for p in list(edge_server.server_state.connections) if hasattr(p,'websocket') or 'websocket' in type(p).__module__]
        if protocols:
            # uvicorn connections are asyncio-owned; schedule shutdown on that
            # connection's actual loop instead of touching another server.
            for protocol in protocols:protocol.loop.call_soon_threadsafe(protocol.shutdown)
            wait_for(lambda:client.get('/api/v2/bootstrap',params={'include_latest':'false'}).json()['gateway']['reconnects']>before,label='edge WebSocket disconnect observed')
            wait_for(lambda:client.get('/api/v2/bootstrap',params={'include_latest':'false'}).json()['gateway']['connected'],label='real edge WebSocket reconnect')
            report['checks']['edge_websocket_disconnect_and_same_source_reconnect']=True
        else:report['checks']['edge_websocket_disconnect_and_same_source_reconnect']='not_tested_no_owned_protocol_found'
        ws.close();ws=None
        recorded_before=runtime.recorder.status()['written_frames'];edge_sequence=hub.latest.sequence
        stop_gateway();assert runtime._thread.is_alive()
        command('step',direct=True);frame_done(3)
        assert hub.latest.sequence>edge_sequence and runtime.recorder.status()['written_frames']>recorded_before
        report['checks']['local_perception_and_raw_recording_continue_without_gateway']=True
        report['gateway_outage_recording']={'before':recorded_before,'after':runtime.recorder.status()['written_frames']}
        start_gateway()
        wait_for(lambda:client.get('/api/v2/bootstrap',params={'include_latest':'false'}).json()['gateway']['frames_relayed']>=1,label='gateway restart current frame')
        with browser_socket() as recovered:
            recovered_header=compare_packet(recovered.recv(timeout=15),'frame3_recovered_keyframe',SceneAccumulator())
            assert recovered_header['kind']=='keyframe' and recovered_header['sequence']==3
        report['checks']['minimal_gateway_restart_current_keyframe']=True
        command('resume');frame_done(4);wait_for(lambda:runtime.completed,label='bounded replay completion')
        with browser_socket() as final:
            wait_for(lambda:client.get('/api/v2/state').json().get('frame_id')==runtime.pipeline.last_state.frame_id,label='final relay frame')
            compare_packet(final.recv(timeout=15),'frame4_keyframe',SceneAccumulator())
        report['checks']['bounded_four_frame_actual_pipeline']=True
        report['edge_error']=runtime.error;report['edge_hub']=hub.status()
        report['gateway_status']=client.get('/api/v2/bootstrap',params={'include_latest':'false'}).json()['gateway']
        assert hub.status()['clients']==1,'Gateway should use one upstream regardless of browser reconnects'
        report['checks']['one_upstream_stream']=True
        report['passed']=True
    except BaseException as exc:
        report['passed']=False;report['error']=f'{type(exc).__name__}: {exc}';raise
    finally:
        if ws is not None:ws.close()
        if client is not None:client.close()
        if edge_client is not None:edge_client.close()
        if gateway is not None:
            gateway.terminate()
            try:gateway.wait(timeout=15)
            except subprocess.TimeoutExpired:gateway.kill();gateway.wait(timeout=5)
        if gateway_log is not None:gateway_log.close()
        if edge_server is not None:edge_server.should_exit=True
        if edge_thread is not None:
            edge_thread.join(timeout=45);report['edge_thread_stopped']=not edge_thread.is_alive()
        if runtime is not None:
            runtime.close();report['local_recording_status']=runtime.recorder.status()
        # Private TLS fixture keys are ephemeral, never a deployment artifact.
        key.unlink(missing_ok=True)
        report['elapsed_seconds']=time.monotonic()-clock
        if runtime is not None and report.get('passed'):
            manifests=list((output/'local_recordings').glob('*/*/manifest.json'));assert len(manifests)==1
            replay=ReplaySource(manifests[0].parent)
            try:
                assert len(replay)==4;report['re_recorded_frames']=len(replay)
                for original,recorded in zip(ReplaySource(ROOT/'assets/sample_recording'),replay):
                    np.testing.assert_array_equal(recorded.lidar_points,original.lidar_points)
                    np.testing.assert_array_equal(recorded.rgb,original.rgb);np.testing.assert_array_equal(recorded.depth,original.depth)
                report['checks']['raw_recording_reopens_and_matches_original_observations']=True
            finally:replay.close()
        report['evidence_sha256']={p.relative_to(output).as_posix():digest(p) for p in output.glob('*.atwn')}
        checkpoint=ROOT/cfg['perception']['checkpoint'];report['checkpoint_sha256']=digest(checkpoint)
        (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--gateway-python',type=Path,default=ROOT.parent.parent/'work/.gateway-venv/Scripts/python.exe')
    args=parser.parse_args();result=run(args.output,args.gateway_python)
    print(json.dumps({'passed':result['passed'],'checks':result['checks'],'elapsed_seconds':result['elapsed_seconds']}))

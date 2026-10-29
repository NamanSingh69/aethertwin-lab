import json
import struct
import time
from dataclasses import replace
import numpy as np
import pytest

from core.contracts import TwinMasterFrameState
from command_center.protocol import SceneEncoder,SceneAccumulator,decode_packet,encode_packet,PREFIX
from command_center.service import SceneHub,create_app
from command_center.controls import ControlPlane
from command_center.auth import SessionAuth
from command_center.assets import write_glb


def scene(index=0,centers=None):
    points=np.asarray([[.05,.05,.05],[.25,.05,.05]] if centers is None else centers,np.float32).reshape(-1,3)
    return TwinMasterFrameState(index,index*.1,10,np.eye(4),(),points,np.ones_like(points)*.5,np.zeros_like(points),
        np.zeros((0,3)),np.zeros(0),np.zeros((0,3)),np.zeros((2,2)),(),np.array([[0,0,.375],[1,0,.375]],dtype=float),
        diagnostics={'source_mode':'test'},source_mode='test',map_revision='map-1',processed_at_unix=time.time())


def wait_until(condition,seconds=3):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if condition():return
        time.sleep(.005)
    raise AssertionError('Condition did not become true')


def test_delta_reconstructs_add_change_remove_and_missing_base_requires_keyframe():
    encoder=SceneEncoder(.2);client=SceneAccumulator()
    first=encoder.encode(scene());client.apply(first.keyframe)
    second=encoder.encode(scene(1,[[.27,.05,.05],[.65,.05,.05]]));header,arrays=decode_packet(second.incremental)
    assert header['kind']=='delta' and header['base_sequence']==1
    np.testing.assert_array_equal(arrays['voxels.deleted'],[[0,0,0]])
    client.apply(second.incremental)
    assert set(client.voxels)=={(1,0,0),(3,0,0)}
    np.testing.assert_allclose(client.voxels[(1,0,0)][0],[.27,.05,.05])
    third=encoder.encode(scene(2,[[.67,.05,.05]]))
    lost=SceneAccumulator().apply(first.keyframe)
    with pytest.raises(ValueError,match='Resynchronization'):lost.apply(third.incremental)
    lost.apply(third.keyframe)
    assert list(lost.voxels)==[(3,0,0)]
    assert arrays['path'].dtype==np.dtype('<f8')


def test_identity_change_forces_keyframe_and_nonfinite_or_malformed_sections_are_rejected():
    encoder=SceneEncoder();encoder.encode(scene())
    packet=encoder.encode(replace(scene(1),map_revision='map-2')).incremental
    header,arrays=decode_packet(packet)
    assert header['kind']=='keyframe'
    with pytest.raises(ValueError,match='Nonfinite'):encode_packet(header,{**arrays,'path':[[float('nan'),0,0]]})
    # Re-encode a malicious directory without touching its actual payload.
    magic,version,flags,length=PREFIX.unpack_from(packet);base=(PREFIX.size+length+7)//8*8
    def malicious(mutator):
        metadata=json.loads(packet[PREFIX.size:PREFIX.size+length]);mutator(metadata)
        content=json.dumps(metadata,separators=(',',':')).encode();start=PREFIX.size+len(content)
        return PREFIX.pack(magic,version,flags,len(content))+content+bytes((-start)%8)+packet[base:]
    for mutate in (
        lambda h:h['sections'][0].update(offset=10**8),
        lambda h:h['sections'][0].update(shape=[1000001,3]),
        lambda h:h['sections'][1].update(offset=h['sections'][0]['offset']),
        lambda h:h['sections'][0].update(dtype='<f8'),
        lambda h:h['sections'].append(h['sections'][0]),
        lambda h:h.update(stream_id=None),
    ):
        with pytest.raises(ValueError):decode_packet(malicious(mutate))


def test_every_frame_keyframe_profile_and_accumulator_budget_are_enforced_atomically():
    encoder=SceneEncoder(keyframe_interval=1)
    first=encoder.encode(scene());second=encoder.encode(scene(1))
    assert decode_packet(first.incremental)[0]['kind']=='keyframe'
    assert decode_packet(second.incremental)[0]['kind']=='keyframe'
    client=SceneAccumulator(max_voxels=1)
    with pytest.raises(ValueError,match='budget'):client.apply(first.keyframe)
    assert client.sequence==0 and not client.voxels


def test_scene_hub_coalesces_blocked_event_loop_to_one_recoverable_keyframe():
    class Loop:
        def __init__(self):self.pending=[]
        def call_soon_threadsafe(self,callback,*args):self.pending.append((callback,args))
    loop=Loop();delivered=[];hub=SceneHub().start()
    try:
        hub.subscribe(loop,lambda value,force:delivered.append((value,force)))
        for i in range(8):
            count=hub.encoded_frames;hub.publish(scene(i));wait_until(lambda:hub.encoded_frames>count)
        assert len(loop.pending)==1 and len(hub._subscribers)==1
        callback,args=loop.pending.pop();callback(*args)
        assert len(delivered)==1 and delivered[0][1] is True
        client=SceneAccumulator().apply(delivered[0][0].keyframe)
        assert client.sequence==8
    finally:hub.close()


def test_control_transactions_are_bounded_idempotent_and_conflicts_do_not_mutate():
    controls=ControlPlane('map-1',capacity=1)
    command={'request_id':'one','map_revision':'map-1','expected_revision':0,'action':'pause','payload':{}}
    assert controls.submit(command)['status']=='accepted'
    assert controls.submit(command)['revision']==1
    assert controls.submit({**command,'payload':{'x':1}})['status']=='rejected'
    assert controls.submit({**command,'request_id':'two','expected_revision':1})['status']=='rejected'
    applied=[];controls.drain(lambda cmd:applied.append(cmd.action))
    assert applied==['pause'] and controls.status()['applied_revision']==1
    assert controls.submit({**command,'request_id':'three'})['status']=='rejected'
    invalid={**command,'request_id':'bad','expected_revision':1,'action':'set_geofences',
             'payload':{'geofences':[{'name':'a','center':[0,0,0],'half_extents':[-1,1,1]}]}}
    assert controls.submit(invalid)['status']=='rejected' and controls.revision==1


def test_private_sessions_enforce_role_origin_cookie_and_no_auth_in_socket_url():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(ValueError,match='Network exposure'):create_app(host='0.0.0.0')
    operator='o'*40;viewer='v'*40
    app=create_app(host='0.0.0.0',operator_token=operator,viewer_token=viewer)
    with TestClient(app,base_url='https://testserver') as client:
        assert client.get('/api/v2/bootstrap').status_code==401
        assert client.post('/api/v2/session',json={'token':'é'*40}).status_code==401
        response=client.post('/api/v2/session',json={'token':viewer})
        assert response.status_code==200 and 'HttpOnly' in response.headers['set-cookie'] and 'Secure' in response.headers['set-cookie']
        assert client.get('/api/v2/bootstrap').json()['role']=='viewer'
        data={'request_id':'x','map_revision':'unversioned','expected_revision':0,'action':'pause','payload':{}}
        assert client.post('/api/v2/commands',json=data).status_code==403
        assert client.post('/api/v2/session',json={'token':operator}).status_code==200
        assert client.post('/api/v2/commands',json=data,headers={'origin':'https://untrusted.example'}).status_code==403
        assert client.post('/api/v2/commands',json=data).status_code==202
        assert client.post('/api/v2/commands',content=b'x'*40000).status_code==413
        assert client.get('/api/v2/bootstrap').json()['stream_url']=='/ws/v2/scene'


def test_websocket_initial_keyframe_and_explicit_resync():
    from fastapi.testclient import TestClient
    hub=SceneHub();app=create_app(hub=hub)
    with TestClient(app,client=('127.0.0.1',50000),base_url='http://127.0.0.1') as client:
        hub.publish(scene());wait_until(lambda:hub.latest is not None)
        with client.websocket_connect('ws://127.0.0.1/ws/v2/scene') as ws:
            header,_=decode_packet(ws.receive_bytes());assert header['kind']=='keyframe'
            hub.publish(scene(1,[[.4,0,0]]))
            header,_=decode_packet(ws.receive_bytes());assert header['sequence']==2
            ws.send_json({'type':'resync'})
            header,_=decode_packet(ws.receive_bytes());assert header['kind']=='keyframe' and header['sequence']==2


def test_loopback_development_rejects_rebound_host_and_role_credentials_must_differ():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(ValueError,match='different'):
        create_app(operator_token='x'*40,viewer_token='x'*40)
    with TestClient(create_app(),client=('127.0.0.1',50000),base_url='http://attacker.example') as client:
        assert client.get('/api/v2/bootstrap').status_code==401
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect('/ws/v2/scene'):pass
        assert rejected.value.code==4401


def test_glb_contains_actual_mesh_buffers_and_valid_world_bounds(tmp_path):
    path=tmp_path/'mesh.glb';vertices=np.array([[0,0,0],[1,0,0],[0,1,1]],float)
    write_glb(path,vertices,[[0,1,2]],colors=np.ones((3,3)))
    data=path.read_bytes();magic,version,total=struct.unpack_from('<4sII',data)
    assert (magic,version,total)==(b'glTF',2,len(data))
    size,kind=struct.unpack_from('<I4s',data,12);assert kind==b'JSON'
    gltf=json.loads(data[20:20+size]);assert gltf['accessors'][0]['max']==[1.,1.,1.]
    assert gltf['asset']['extras']['navigation_authority'] is False
    offset=20+size+8;actual=np.frombuffer(data,dtype='<f4',count=9,offset=offset).reshape(3,3)
    np.testing.assert_array_equal(actual,vertices)

"""Network-facing hardening shared by the service and the gateway (which builds on create_app)."""
import logging
import time

import pytest

from command_center.auth import LoginLimiter,SessionAuth,configured_token
from command_center.service import SceneHub,create_app
from test_scene_protocol_v2 import scene,wait_until

OPERATOR='o'*40;VIEWER='v'*40


def test_tokens_load_from_files_and_ambiguous_or_oversized_sources_are_rejected(tmp_path,monkeypatch):
    secret=tmp_path/'operator_token';secret.write_text(OPERATOR+'\n',encoding='utf-8')
    monkeypatch.setenv('AETHERTWIN_OPERATOR_TOKEN_FILE',str(secret))
    assert configured_token(None,'AETHERTWIN_OPERATOR_TOKEN')==OPERATOR
    assert SessionAuth('0.0.0.0').operator_token==OPERATOR
    assert configured_token('x'*40,'AETHERTWIN_OPERATOR_TOKEN')=='x'*40
    monkeypatch.setenv('AETHERTWIN_OPERATOR_TOKEN',OPERATOR)
    with pytest.raises(ValueError,match='not both'):configured_token(None,'AETHERTWIN_OPERATOR_TOKEN')
    monkeypatch.delenv('AETHERTWIN_OPERATOR_TOKEN');secret.write_bytes(b'x'*5000)
    with pytest.raises(ValueError,match='4 KiB'):configured_token(None,'AETHERTWIN_OPERATOR_TOKEN')


def test_login_limiter_refills_bounds_memory_and_resets_on_success():
    now=[0.]
    limiter=LoginLimiter(burst=2,rate=.5,max_peers=2,clock=lambda:now[0])
    assert limiter.retry_after('a')==0
    limiter.failure('a');limiter.failure('a')
    assert limiter.retry_after('a')==pytest.approx(2.)
    now[0]=1.;assert limiter.retry_after('a')==pytest.approx(1.)
    now[0]=2.;assert limiter.retry_after('a')==0
    limiter.failure('b');limiter.failure('c')
    assert len(limiter.buckets)==2 and 'a' not in limiter.buckets
    limiter.success('c');assert 'c' not in limiter.buckets


def test_failed_sign_ins_are_rate_limited_and_audited_without_credentials(caplog):
    from fastapi.testclient import TestClient
    app=create_app(host='0.0.0.0',operator_token=OPERATOR,viewer_token=VIEWER)
    caplog.set_level(logging.INFO,logger='aethertwin.audit')
    with TestClient(app,base_url='https://testserver') as client:
        for _ in range(5):assert client.post('/api/v2/session',json={'token':'w'*40}).status_code==401
        limited=client.post('/api/v2/session',json={'token':OPERATOR})
        assert limited.status_code==429 and int(limited.headers['retry-after'])>=1
    events=[record.getMessage() for record in caplog.records if record.name=='aethertwin.audit']
    assert sum('"login_failed"' in event for event in events)==5 and any('login_rate_limited' in event for event in events)
    assert not any('w'*40 in event or OPERATOR in event for event in events)


def test_commands_are_audited_and_https_responses_carry_hsts(caplog):
    from fastapi.testclient import TestClient
    app=create_app(host='0.0.0.0',operator_token=OPERATOR,viewer_token=VIEWER)
    caplog.set_level(logging.INFO,logger='aethertwin.audit')
    with TestClient(app,base_url='https://testserver') as client:
        assert client.post('/api/v2/session',json={'token':OPERATOR}).status_code==200
        data={'request_id':'x','map_revision':'unversioned','expected_revision':0,'action':'pause','payload':{}}
        response=client.post('/api/v2/commands',json=data)
        assert response.status_code==202 and response.headers['strict-transport-security']=='max-age=31536000'
    assert any('"command"' in r.getMessage() and '"pause"' in r.getMessage() for r in caplog.records)
    with TestClient(create_app(),client=('127.0.0.1',50000),base_url='http://127.0.0.1') as client:
        assert 'strict-transport-security' not in client.get('/healthz').headers


def test_health_probes_need_no_credential_and_reveal_only_status():
    from fastapi.testclient import TestClient
    hub=SceneHub();app=create_app(hub=hub,host='0.0.0.0',operator_token=OPERATOR)
    with TestClient(app,base_url='https://testserver') as client:
        assert client.get('/api/v2/health').status_code==401
        assert client.get('/healthz').json()=={'status':'alive'}
        assert client.get('/readyz').status_code==503
        hub.publish(scene());wait_until(lambda:hub.latest is not None)
        ready=client.get('/readyz');assert ready.status_code==200 and ready.json()=={'ready':True}
    flag=[False]
    with TestClient(create_app(ready=lambda:flag[0]),client=('127.0.0.1',50000),base_url='http://127.0.0.1') as client:
        assert client.get('/readyz').status_code==503
        flag[0]=True;assert client.get('/readyz').status_code==200


def test_scene_stream_closes_when_its_session_expires():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    hub=SceneHub();app=create_app(hub=hub,host='0.0.0.0',operator_token=OPERATOR)
    app.state.auth.ttl=1
    with TestClient(app,base_url='https://testserver') as client:
        assert client.post('/api/v2/session',json={'token':OPERATOR}).status_code==200
        started=time.monotonic()
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect('wss://testserver/ws/v2/scene') as ws:
                while True:ws.receive_bytes()
        assert closed.value.code==4401 and time.monotonic()-started<5


def test_gateway_readiness_needs_a_live_recent_upstream_and_failed_asset_syncs_back_off(tmp_path,monkeypatch):
    import asyncio
    import command_center.gateway as gateway
    runtime=gateway.GatewayRuntime('https://edge.local',token='u'*40,asset_root=tmp_path)
    assert not runtime.ready()
    runtime._connected=True;runtime._last_packet=time.time();assert runtime.ready()
    runtime._last_packet=time.time()-10;assert not runtime.ready()
    bootstrap={'protocol_version':2,'capabilities':{},'map_revision':'map-1','control_revision':0,
        'map_manifest_url':'/api/v2/assets/manifest.json'}
    replies={'/api/v2/health':{},'/api/v2/commands':{'revision':0,'applied_revision':0,'pending':0,'history':[]}}
    async def fake_json(method,path,**kwargs):return 200,bootstrap if path.startswith('/api/v2/bootstrap') else replies[path]
    syncs=[]
    async def failing_sync(url,revision):syncs.append(revision);raise RuntimeError('upstream asset unavailable')
    monkeypatch.setattr(runtime,'_json',fake_json);monkeypatch.setattr(runtime,'_sync_assets',failing_sync)
    clock=[100.];monkeypatch.setattr(gateway.time,'monotonic',lambda:clock[0])
    asyncio.run(runtime._refresh());asyncio.run(runtime._refresh())
    assert syncs==['map-1'] and 'unavailable' in runtime._asset_error
    clock[0]+=gateway.ASSET_RETRY_MIN_SECONDS+.1;asyncio.run(runtime._refresh());assert len(syncs)==2
    clock[0]+=gateway.ASSET_RETRY_MIN_SECONDS+.1;asyncio.run(runtime._refresh());assert len(syncs)==2
    bootstrap['map_revision']='map-2';asyncio.run(runtime._refresh());assert syncs[-1]=='map-2'


def test_sign_in_attempts_are_taken_atomically_and_refunded_on_success():
    now=[0.];limiter=LoginLimiter(burst=2,rate=.5,clock=lambda:now[0])
    assert limiter.acquire('p')==0 and limiter.acquire('p')==0
    assert limiter.acquire('p')==pytest.approx(2.)
    limiter.success('p');assert limiter.acquire('p')==0


def test_gateway_reauthentication_is_single_flight(tmp_path):
    import asyncio
    import command_center.gateway as gateway
    runtime=gateway.GatewayRuntime('https://edge.local',token='u'*40,asset_root=tmp_path);logins=[]
    async def login():logins.append(1);await asyncio.sleep(.01)
    runtime._authenticate_once=login
    async def burst():await asyncio.gather(*(runtime._authenticate() for _ in range(5)))
    asyncio.run(burst());assert len(logins)==1

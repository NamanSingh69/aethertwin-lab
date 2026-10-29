"""Gateway tests use an isolated authenticated TLS fixture edge, never port 8080."""

from __future__ import annotations
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
import uvicorn

from command_center.assets import write_glb
from command_center.controls import ControlPlane, ACTIONS
from command_center.gateway import (
    GatewayRuntime,
    RelayAccumulator,
    create_gateway_app,
    upstream_origin,
)
from command_center.protocol import EncodedScene, decode_packet, encode_packet
from command_center.service import SceneHub, create_app

EDGE_OPERATOR = "fixture-edge-operator-credential-0000001"
EDGE_VIEWER = "fixture-edge-viewer-credential-000000001"
LOCAL_OPERATOR = "fixture-gateway-operator-credential-001"
LOCAL_VIEWER = "fixture-gateway-viewer-credential-00001"


def wait_until(predicate, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Gateway fixture condition did not become true")


def encoded(sequence=1, stream="fixture-edge", kind="keyframe", base=None):
    metadata = {
        "schema_version": 1,
        "message_type": "twin.frame",
        "frame_id": sequence,
        "timestamp": sequence * 0.1,
        "fps": 10.0,
        "source_mode": "replay",
        "coordinate_frame": "world_z_up",
        "processed_at_unix": time.time(),
        "identity": {
            "lab_id": "fixture-lab",
            "rig_id": "fixture-rig",
            "map_revision": "edge-map-1",
            "calibration_id": "measured-fixture-calibration",
        },
        "tracks": [],
        "geofences": [],
        "alerts": [],
        "trajectory_status": "ok",
        "sensor_pose_world": np.eye(4).tolist(),
        "diagnostics": {"source_mode": "replay", "test_injection": True},
        "voxels": {"source_count": 1},
        "floor_risk": {},
        "traffic_heatmap": {"bounds_xy": [-1, 1, -1, 1], "shape": [2, 2]},
    }
    sections = {
        "voxels.keys": np.array([[0, 0, 0]], np.int32),
        "voxels.centers": np.array([[0.01 * sequence, 0, 0.5]], np.float32),
        "voxels.colors": np.array([[0, 0.7, 0.8]], np.float32),
        "voxels.flow": np.zeros((1, 3), np.float32),
        "floor_risk.centers": np.empty((0, 3), np.float32),
        "floor_risk.risk": np.empty(0, np.float32),
        "floor_risk.colors": np.empty((0, 3), np.float32),
        "traffic_heatmap.values": np.ones((2, 2), np.float32),
        "path": np.array([[0, 0, 0.5], [1, 0, 0.5]], np.float64),
    }
    keyframe = encode_packet(
        {
            "kind": "keyframe",
            "sequence": sequence,
            "base_sequence": None,
            "stream_id": stream,
            "scene": metadata,
            "sent_at_unix": time.time(),
        },
        sections,
    )
    incremental = (
        keyframe
        if kind == "keyframe"
        else encode_packet(
            {
                "kind": kind,
                "sequence": sequence,
                "base_sequence": base,
                "stream_id": stream,
                "scene": metadata,
                "sent_at_unix": time.time(),
            },
            sections,
        )
    )
    return EncodedScene(sequence, keyframe, incremental, metadata)


class FixtureRuntime:
    def __init__(self):
        self.stop = threading.Event()
        self.paused = False
        self.fail = False
        self.command_delay = 0
        self.sequence = 0
        self.stream = "fixture-" + uuid.uuid4().hex
        self.skip_next = False
        self.trajectory_delay = 0

    def start(self, hub, controls):
        self.hub = hub
        self.controls = controls

        def loop():
            while not self.stop.wait(0.04):
                controls.drain(self.apply)
                if not self.paused:
                    self.sequence += 2 if self.skip_next else 1
                    self.skip_next = False
                    hub.publish(
                        encoded(
                            self.sequence,
                            self.stream,
                            "delta" if self.sequence > 1 else "keyframe",
                            self.sequence - 1 if self.sequence > 1 else None,
                        )
                    )

        self.thread = threading.Thread(target=loop, name="fixture-no-sensor-edge")
        self.thread.start()

    def apply(self, command):
        if self.fail:
            raise ValueError("fixture planning failure")
        if command.action == "pause":
            self.paused = True
        if command.action == "resume":
            self.paused = False

    def bootstrap(self):
        return {
            "source_mode": "replay",
            "paused": self.paused,
            "source_active": True,
            "available_sources": [
                {"id": "fixture", "source": "replay", "recording_id": "fixture"}
            ],
            "capabilities": {action: True for action in ACTIONS},
            "replay": {"frame_count": 10000, "current_frame": self.sequence},
            "identity": {
                "lab_id": "fixture-lab",
                "rig_id": "fixture-rig",
                "map_revision": "edge-map-1",
                "calibration_id": "measured-fixture-calibration",
            },
            "error": None,
        }

    def trajectory_csv(self):
        time.sleep(self.trajectory_delay)
        return "time_s,x_m,y_m,z_m\n0,0,0,0.5\n1,1,0,0.5\n"

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)


class FixtureEdge:
    def __init__(self, tmp_path, cert, key):
        self.root = tmp_path
        self.cert = cert
        self.key = key
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.assets = tmp_path / "edge-assets"
        self.assets.mkdir()
        write_glb(
            self.assets / "tile.glb", [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]]
        )
        self.manifest = {
            "map_revision": "edge-map-1",
            "coordinate_frame": "world_z_up",
            "bounds": [-1, -1, 0, 1, 1, 2],
            "tiles": [
                {
                    "id": "t",
                    "bounds": [-1, -1, 0, 1, 1, 2],
                    "lods": [
                        {
                            "url": "/api/v2/assets/tile.glb",
                            "distance": 0,
                            "triangles": 1,
                        }
                    ],
                }
            ],
        }
        self.write_manifest()

    @property
    def url(self):
        return f"https://127.0.0.1:{self.port}"

    def write_manifest(self):
        (self.assets / "manifest.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )

    def start(self):
        self.runtime = FixtureRuntime()
        self.controls = ControlPlane("edge-map-1")
        self.hub = SceneHub()
        self.app = create_app(
            self.runtime,
            hub=self.hub,
            controls=self.controls,
            asset_root=self.assets,
            operator_token=EDGE_OPERATOR,
            viewer_token=EDGE_VIEWER,
            local_development=False,
        )

        @self.app.middleware("http")
        async def delay(request, call_next):
            if (
                request.method == "POST"
                and request.url.path == "/api/v2/commands"
                and self.runtime.command_delay
            ):
                await asyncio.sleep(self.runtime.command_delay)
            return await call_next(request)

        self.server = uvicorn.Server(
            uvicorn.Config(
                self.app,
                host="127.0.0.1",
                port=self.port,
                log_level="error",
                access_log=False,
                ssl_certfile=str(self.cert),
                ssl_keyfile=str(self.key),
                ws_max_size=4096,
                timeout_graceful_shutdown=1,
                timeout_keep_alive=1,
            )
        )
        self.thread = threading.Thread(
            target=self.server.run, name="isolated-fixture-TLS-edge"
        )
        self.thread.start()
        wait_until(lambda: self.server.started)

    def close(self):
        self.server.should_exit = True
        self.thread.join(timeout=8)
        assert not self.thread.is_alive()


@pytest.fixture(scope="module")
def tls_files(tmp_path_factory):
    root = tmp_path_factory.mktemp("gateway-tls")
    openssl = shutil.which("openssl")
    if not openssl:
        candidate = Path("C:/Program Files/Git/usr/bin/openssl.exe")
        if candidate.is_file():
            openssl = str(candidate)
    if not openssl:
        pytest.fail("TLS network tests require the OpenSSL CLI")
    cert = root / "fixture-cert.pem"
    key = root / "fixture-key.pem"
    config = root / "fixture-openssl.cnf"
    config.write_text(
        "[req]\ndistinguished_name=dn\n[dn]\nCN=localhost\n", encoding="ascii"
    )
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "2",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-config",
            str(config),
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1,DNS:localhost",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return cert, key


@pytest.fixture
def edge(tmp_path, tls_files):
    server = FixtureEdge(tmp_path, *tls_files)
    server.start()
    try:
        yield server
    finally:
        if server.thread.is_alive():
            server.close()


@contextmanager
def gateway_client(
    edge,
    tmp_path,
    *,
    upstream_token=EDGE_OPERATOR,
    ca=True,
    hub=None,
    **runtime_options,
):
    app = create_gateway_app(
        edge.url,
        upstream_token=upstream_token,
        asset_root=tmp_path / "gateway-cache",
        ca_file=edge.cert if ca else None,
        frontend_root=tmp_path / "frontend",
        operator_token=LOCAL_OPERATOR,
        viewer_token=LOCAL_VIEWER,
        local_development=False,
        poll_interval=0.15,
        reconnect_interval=0.15,
        operation_timeout=3.0,
        hub=hub,
        **runtime_options,
    )
    with TestClient(app, base_url="https://gateway.test") as client:
        assert (
            client.post("/api/v2/session", json={"token": LOCAL_OPERATOR}).status_code
            == 200
        )
        yield app, client


def command(app, request_id="request-1", action="pause", revision=None):
    controls = app.state.controls
    return {
        "request_id": request_id,
        "map_revision": controls.map_revision,
        "expected_revision": controls.revision if revision is None else revision,
        "action": action,
        "payload": {},
    }


def test_origin_and_asset_allowlists_never_accept_browser_destinations(tmp_path):
    for url in (
        "http://edge.internal",
        "https://user:secret@edge.local",
        "https://edge.local/api",
        "https://edge.local/?token=x",
        "file:///etc/passwd",
    ):
        with pytest.raises(ValueError):
            upstream_origin(url)
    runtime = GatewayRuntime(
        "https://edge.local", token=EDGE_OPERATOR, asset_root=tmp_path
    )
    for path in (
        "https://other.local/api/v2/assets/t.glb",
        "/api/v2/assets/../x.glb",
        "/api/v2/assets/%2e%2e/x.glb",
        "/api/v2/assets/x.glb?url=http://evil",
        "/api/v2/commands",
    ):
        with pytest.raises(ValueError):
            runtime._asset_path(path)
    assert runtime._asset_path("/api/v2/assets/rev_1/t.glb")[1] == "rev_1/t.glb"


def test_relay_preserves_sequence_source_calibration_and_recovers_gaps():
    relay = RelayAccumulator(max_voxels=2)
    first = relay.apply(encoded().keyframe)
    second = relay.apply(encoded(2, kind="delta", base=1).incremental)
    header, arrays = decode_packet(second.keyframe)
    assert (
        header["sequence"] == 2
        and header["stream_id"] == "fixture-edge"
        and header["kind"] == "keyframe"
    )
    assert (
        second.scene["source_mode"] == "replay"
        and second.scene["identity"]["calibration_id"] == "measured-fixture-calibration"
    )
    np.testing.assert_allclose(second.scene["voxels"]["centers"], [[0.02, 0, 0.5]])
    with pytest.raises(ValueError, match="Resynchronization"):
        relay.apply(encoded(4, kind="delta", base=3).incremental)
    assert relay.apply(encoded(5).keyframe).sequence == 5


def test_actual_TLS_edge_one_upstream_fans_out_and_exports_only_allowlisted_assets(
    edge, tmp_path
):
    with gateway_client(edge, tmp_path) as (app, client):
        runtime = app.state.gateway_runtime
        wait_until(
            lambda: runtime.bootstrap()["gateway"]["connected"]
            and app.state.hub.latest is not None
        )
        bootstrap = client.get("/api/v2/bootstrap").json()
        assert (
            bootstrap["source_mode"] == "replay"
            and bootstrap["identity"]["lab_id"] == "fixture-lab"
        )
        assert bootstrap["role"] == "operator" and bootstrap["capabilities"]["pause"]
        with client.websocket_connect("wss://gateway.test/ws/v2/scene") as one:
            with client.websocket_connect("wss://gateway.test/ws/v2/scene") as two:
                a, _ = decode_packet(one.receive_bytes())
                b, _ = decode_packet(two.receive_bytes())
                assert a["stream_id"] == b["stream_id"] == edge.runtime.stream
                assert a["kind"] == b["kind"] == "keyframe"
                assert edge.hub.status()["clients"] == 1
        manifest = client.get(bootstrap["map_manifest_url"]).json()
        url = manifest["tiles"][0]["lods"][0]["url"]
        assert "/rev_" in url and client.get(url).content[:4] == b"glTF"
        assert client.get("/api/v2/assets/unknown.glb").status_code == 404
        assert client.get("/api/v2/trajectory.csv").text.startswith("time_s,x_m")
        response = client.post("/api/v2/commands", json=command(app))
        assert response.status_code == 202 and response.json()["revision"] == 1
        wait_until(lambda: runtime.control_status()["applied_revision"] == 1)
        assert edge.runtime.paused
        assert runtime.bootstrap()["gateway"]["upstream_streams"] == 1
        assert app.state.hub.encoder.sequence == 0
    assert not runtime._thread.is_alive()


def test_local_viewer_and_upstream_viewer_cannot_control(edge, tmp_path):
    with gateway_client(edge, tmp_path) as (app, client):
        wait_until(
            lambda: app.state.gateway_runtime.bootstrap()["gateway"]["connected"]
        )
        client.post("/api/v2/session", json={"token": LOCAL_VIEWER})
        assert client.get("/api/v2/bootstrap").json()["role"] == "viewer"
        assert client.post("/api/v2/commands", json=command(app)).status_code == 403
        assert edge.controls.revision == 0
    with gateway_client(
        edge, tmp_path / "viewer-upstream", upstream_token=EDGE_VIEWER
    ) as (app, client):
        wait_until(
            lambda: app.state.gateway_runtime.bootstrap()["gateway"]["connected"]
        )
        assert not any(client.get("/api/v2/bootstrap").json()["capabilities"].values())
        response = client.post("/api/v2/commands", json=command(app))
        assert (
            response.status_code == 409
            and "does not permit" in response.json()["reason"]
        )
        assert edge.controls.revision == 0


def test_upstream_auth_failure_and_untrusted_TLS_are_visible_without_simulated_substitution(
    edge, tmp_path
):
    for token, ca in (
        ("wrong-upstream-token-never-browser-0001", True),
        (EDGE_OPERATOR, False),
    ):
        with gateway_client(edge, tmp_path / str(ca), upstream_token=token, ca=ca) as (
            app,
            client,
        ):
            wait_until(
                lambda: app.state.gateway_runtime.bootstrap()["gateway"]["reconnects"]
                > 0
            )
            result = client.get("/api/v2/bootstrap").json()
            assert result["source_mode"] == "unspecified" and result["latest"] is None
            assert result["error"] and token not in json.dumps(result)
            assert not any(result["capabilities"].values())
            if not ca:
                assert "CERTIFICATE_VERIFY_FAILED" in result["error"]


def test_upstream_command_rejection_and_applied_failure_are_preserved(edge, tmp_path):
    with gateway_client(edge, tmp_path) as (app, client):
        runtime = app.state.gateway_runtime
        wait_until(lambda: runtime.bootstrap()["gateway"]["connected"])
        rejected = client.post(
            "/api/v2/commands", json=command(app, revision=99)
        ).json()
        assert (
            rejected["status"] == "rejected"
            and rejected["revision"] == 0
            and "revision conflict" in rejected["reason"]
        )
        edge.runtime.fail = True
        accepted = client.post("/api/v2/commands", json=command(app, "failing")).json()
        assert accepted["status"] == "accepted"
        wait_until(lambda: bool(runtime.control_status()["history"]))
        history = client.get("/api/v2/commands").json()["history"]
        assert (
            history[-1]["status"] == "failed"
            and history[-1]["reason"] == "fixture planning failure"
        )


def test_gateway_HTTP_forwarding_does_not_block_ASGI_health_loop(edge, tmp_path):
    with gateway_client(edge, tmp_path) as (app, client):
        wait_until(
            lambda: app.state.gateway_runtime.bootstrap()["gateway"]["connected"]
        )
        edge.runtime.command_delay = 0.8
        result = []
        thread = threading.Thread(
            target=lambda: result.append(
                client.post("/api/v2/commands", json=command(app))
            )
        )
        thread.start()
        time.sleep(0.1)
        began = time.monotonic()
        assert client.get("/api/v2/health").status_code == 200
        assert time.monotonic() - began < 0.5
        thread.join(timeout=5)
        assert result[0].status_code == 202


def test_disconnect_keeps_original_frame_stale_and_reconnects_to_real_edge(
    edge, tmp_path
):
    with gateway_client(edge, tmp_path) as (app, client):
        runtime = app.state.gateway_runtime
        wait_until(lambda: runtime.bootstrap()["gateway"]["frames_relayed"] > 1)
        first_stream = edge.runtime.stream
        edge.close()
        wait_until(lambda: not runtime.bootstrap()["gateway"]["connected"])
        state = client.get("/api/v2/state").json()
        status = client.get("/api/v2/bootstrap").json()
        assert (
            state["source_mode"] == "replay"
            and status["error"]
            and status["gateway"]["stale"]
        )
        assert not any(status["capabilities"].values())
        edge.start()
        wait_until(
            lambda: runtime.bootstrap()["gateway"]["connected"]
            and app.state.hub.latest is not None
            and decode_packet(app.state.hub.latest.keyframe)[0]["stream_id"]
            != first_stream
        )
        assert edge.hub.status()["clients"] == 1


def test_gateway_module_has_no_ML_or_sensor_imports():
    code = """import sys, importlib.abc
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in ('torch','open3d','mujoco','cv2','ingestion','perception_ml'):raise RuntimeError('Forbidden gateway import: '+fullname)
sys.meta_path.insert(0,Guard())
import command_center.gateway
print('gateway has no ML or sensor imports')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert "no ML or sensor imports" in result.stdout


def test_relay_hub_coalesces_a_blocked_client_to_one_current_keyframe():
    class BlockedLoop:
        def __init__(self):
            self.callbacks = []

        def call_soon_threadsafe(self, callback, *args):
            self.callbacks.append((callback, args))

    hub = SceneHub().start()
    loop = BlockedLoop()
    delivered = []
    relay = RelayAccumulator()
    try:
        hub.subscribe(loop, lambda value, force: delivered.append((value, force)))
        for sequence in range(1, 15):
            count = hub.encoded_frames
            hub.publish(relay.apply(encoded(sequence).keyframe))
            wait_until(lambda: hub.encoded_frames > count)
        assert (
            len(loop.callbacks) == 1
            and hub.incoming.maxsize == 1
            and hub.incoming.qsize() <= 1
        )
        callback, args = loop.callbacks.pop()
        callback(*args)
        current, force = delivered[0]
        assert current.sequence == 14 and force
        assert decode_packet(current.keyframe)[0]["kind"] == "keyframe"
    finally:
        hub.close()


def test_actual_wire_gap_requests_resync_and_recovers_without_changing_source(
    edge, tmp_path
):
    with gateway_client(edge, tmp_path) as (app, client):
        runtime = app.state.gateway_runtime
        wait_until(lambda: runtime.bootstrap()["gateway"]["frames_relayed"] > 2)
        before = runtime.bootstrap()["gateway"]["frames_relayed"]
        edge.runtime.skip_next = True
        wait_until(lambda: runtime.bootstrap()["gateway"]["resync_requests"] > 0)
        wait_until(
            lambda: runtime.bootstrap()["gateway"]["frames_relayed"] > before + 3
        )
        assert client.get("/api/v2/state").json()["source_mode"] == "replay"


def test_remote_asset_origin_and_budget_failures_do_not_publish_or_accumulate_files(
    edge, tmp_path
):
    edge.manifest["tiles"][0]["lods"][0]["url"] = (
        "https://not-configured.invalid/api/v2/assets/tile.glb"
    )
    edge.write_manifest()
    with gateway_client(edge, tmp_path / "origin") as (app, client):
        wait_until(
            lambda: app.state.gateway_runtime.bootstrap()["gateway"]["connected"]
        )
        bootstrap = client.get("/api/v2/bootstrap").json()
        assert (
            "Cross-origin" in bootstrap["error"]
            and bootstrap["map_manifest_url"] is None
        )
        assert len(list(app.state.gateway_runtime.asset_root.glob(".stage_*"))) <= 1
    edge.manifest["tiles"][0]["lods"][0]["url"] = "/api/v2/assets/tile.glb"
    edge.write_manifest()
    with gateway_client(edge, tmp_path / "budget", max_asset_bytes=12) as (app, client):
        wait_until(
            lambda: app.state.gateway_runtime.bootstrap()["gateway"]["connected"]
        )
        assert client.get("/api/v2/bootstrap").json()["error"]
        time.sleep(0.4)
        assert len(list(app.state.gateway_runtime.asset_root.glob(".stage_*"))) <= 1
        assert not list(app.state.gateway_runtime.asset_root.rglob("*.glb"))
    assert not list(app.state.gateway_runtime.asset_root.glob(".stage_*"))


def test_forward_HTTP_concurrency_is_bounded_instead_of_queueing_unlimited_work(
    edge, tmp_path
):
    with gateway_client(edge, tmp_path) as (app, client):
        runtime = app.state.gateway_runtime
        wait_until(lambda: runtime.bootstrap()["gateway"]["connected"])
        edge.runtime.trajectory_delay = 0.8
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(runtime.trajectory_csv()))
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        wait_until(lambda: runtime.bootstrap()["gateway"]["http_in_flight"] == 4)
        with pytest.raises(ValueError, match="capacity"):
            runtime.trajectory_csv()
        for thread in threads:
            thread.join(timeout=5)
        assert (
            len(results) == 4 and runtime.bootstrap()["gateway"]["http_in_flight"] == 0
        )


def test_gateway_reclaims_only_its_bounded_cache_generations(tmp_path):
    for index in range(4):
        path = tmp_path / ("rev_" + str(index) * 24)
        path.mkdir()
        (path / "tile.glb").write_bytes(b"fixture")
        os.utime(path, (100 + index, 100 + index))
    notes = tmp_path / "user_notes"
    notes.mkdir()
    (notes / "keep.txt").write_text("keep")
    runtime = GatewayRuntime(
        "https://edge.local", token=EDGE_OPERATOR, asset_root=tmp_path
    )
    assert len(list(tmp_path.glob("rev_*"))) == 2
    assert (notes / "keep.txt").read_text() == "keep"
    with pytest.raises(ValueError):
        runtime._cache_path("../outside.glb")


def test_cookie_security_and_failure_details_are_not_downgraded(tmp_path):
    runtime = GatewayRuntime(
        "https://edge.local", token=EDGE_OPERATOR, asset_root=tmp_path
    )
    cookie = SimpleNamespace(
        name="aethertwin_session",
        value="valid_session.signature",
        secure=True,
        _rest={"HttpOnly": None},
    )
    runtime._client = SimpleNamespace(cookies=SimpleNamespace(jar=[cookie]))
    assert runtime._session_cookie() == "aethertwin_session=valid_session.signature"
    cookie.secure = False
    with pytest.raises(ValueError, match="Secure"):
        runtime._session_cookie()
    cookie.secure = True
    cookie._rest = {}
    with pytest.raises(ValueError, match="HttpOnly"):
        runtime._session_cookie()
    cookie._rest = {"HttpOnly": None}
    runtime.origin = "http://127.0.0.1:1234"
    with pytest.raises(ValueError, match="configure HTTPS"):
        runtime._session_cookie()
    assert runtime.safe_error(TimeoutError()) == "TimeoutError"
    assert EDGE_OPERATOR not in runtime.safe_error(ValueError("echo " + EDGE_OPERATOR))


def test_upstream_capability_strings_do_not_promote_controls(tmp_path):
    runtime = GatewayRuntime(
        "https://edge.local", token=EDGE_OPERATOR, asset_root=tmp_path
    )
    runtime._connected = True
    runtime._upstream_role = "operator"
    runtime._cache = {"capabilities": {"pause": "false", "resume": True}}
    assert runtime.capabilities()["pause"] is False
    assert runtime.capabilities()["resume"] is True

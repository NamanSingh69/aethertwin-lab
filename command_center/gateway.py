"""Private, validation-only edge relay. No sensor or inference modules are loaded.

One authenticated upstream WebSocket feeds the existing bounded SceneHub. HTTP
operations run on the relay's private asyncio loop; ASGI uses worker threads for
the synchronous control/export adapters. Credentials never enter browser state.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
from collections import deque
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import threading
import time
import tempfile
from urllib.parse import urlsplit

import httpx
import numpy as np
from websockets.asyncio.client import connect

from .auth import configured_token
from .controls import ACTIONS, validate_payload
from .protocol import (
    MAX_HEADER,
    MAX_PACKET,
    EncodedScene,
    SceneAccumulator,
    decode_packet,
    encode_packet,
)
from .service import SceneHub, create_app


# Upstream socket keepalive: a silent but open upstream is dropped and reconnected.
PING_INTERVAL_SECONDS = 10
PING_TIMEOUT_SECONDS = 10
# Failed map syncs back off per revision instead of refetching every poll.
ASSET_RETRY_MIN_SECONDS = 5
ASSET_RETRY_MAX_SECONDS = 300
# Browser-facing TLS 1.2 suites: forward secret AEAD only (TLS 1.3 suites are fixed by OpenSSL).
TLS_CIPHERS = "ECDHE+AESGCM:ECDHE+CHACHA20"


def upstream_origin(url):
    """Only a configured origin is accepted; browser input never selects hosts."""
    value = urlsplit(str(url))
    if (
        value.scheme not in ("https", "http")
        or not value.hostname
        or value.username
        or value.password
    ):
        raise ValueError("Configure an HTTP(S) upstream origin without URL credentials")
    if value.path not in ("", "/") or value.query or value.fragment:
        raise ValueError(
            "Upstream must be an origin without a path, query, or fragment"
        )
    try:
        loopback = ipaddress.ip_address(value.hostname).is_loopback
    except ValueError:
        loopback = value.hostname.lower() == "localhost"
    if value.scheme != "https" and not loopback:
        raise ValueError("Remote upstream connections require verified HTTPS")
    try:
        value.port
    except ValueError as exc:
        raise ValueError("Invalid upstream port") from exc
    return f"{value.scheme}://{value.netloc}"


class RelayAccumulator:
    """Bounded wire reconstruction preserving original stream/sequence/identity."""

    def __init__(self, max_voxels=100000):
        self.client = SceneAccumulator(max_voxels=max_voxels)

    def apply(self, packet):
        header, arrays = decode_packet(packet)
        self.client.apply(packet)
        keys = list(self.client.voxels)
        sections = {
            name: value
            for name, value in arrays.items()
            if not name.startswith("voxels.")
        }
        sections["voxels.keys"] = np.asarray(keys, np.int32).reshape(-1, 3)
        for j, name in enumerate(("centers", "colors", "flow")):
            sections["voxels." + name] = np.asarray(
                [self.client.voxels[key][j] for key in keys], np.float32
            ).reshape(-1, 3)
        metadata = {**header, "kind": "keyframe", "base_sequence": None}
        metadata.pop("sections", None)
        keyframe = encode_packet(metadata, sections)
        scene = copy.deepcopy(header["scene"])
        for group, names in (
            ("voxels", ("centers", "colors", "flow")),
            ("floor_risk", ("centers", "risk", "colors")),
            ("traffic_heatmap", ("values",)),
        ):
            scene[group] = dict(scene.get(group, {}))
            for name in names:
                value = sections.get(group + "." + name)
                if value is not None:
                    scene[group][name] = value.tolist()
        path = sections.get("path")
        scene["path"] = None if path is None or not len(path) else path.tolist()
        if "gemini_inlay" in sections:
            scene["gemini_inlay"] = sections["gemini_inlay"].tolist()
        return EncodedScene(header["sequence"], keyframe, packet, scene)


class ForwardControlPlane:
    """Local permission checks are followed by upstream capability/revision checks."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.actions = set(ACTIONS)

    @property
    def map_revision(self):
        return str(self.runtime.cached("map_revision", "unavailable"))

    @property
    def revision(self):
        return int(self.runtime.cached("control_revision", 0))

    def status(self):
        return self.runtime.control_status()

    def submit(self, data):
        request_id = data.get("request_id") if isinstance(data, dict) else None

        def reject(reason):
            return {
                "request_id": request_id,
                "status": "rejected",
                "revision": self.revision,
                "reason": str(reason),
            }

        try:
            if (
                not isinstance(request_id, str)
                or not request_id
                or len(request_id) > 128
            ):
                raise ValueError("A bounded request_id is required")
            action = data.get("action")
            if action not in self.actions:
                raise ValueError("Unsupported gateway command")
            if type(data.get("expected_revision")) is not int:
                raise ValueError("Control revision is required")
            if data.get("map_revision") != self.map_revision:
                raise ValueError(
                    "Upstream map revision changed; refresh before editing"
                )
            payload = validate_payload(action, data.get("payload", {}))
            if not self.runtime.capabilities().get(action, False):
                raise ValueError("Upstream session/source does not permit this command")
            command = {
                "request_id": request_id,
                "map_revision": data["map_revision"],
                "expected_revision": data["expected_revision"],
                "action": action,
                "payload": payload,
            }
            return self.runtime.invoke("command", command)
        except Exception as exc:
            return reject(self.runtime.safe_error(exc))


class GatewayRuntime:
    """Authenticated single-stream relay with bounded caches and visible failures."""

    def __init__(
        self,
        upstream_url,
        *,
        token=None,
        asset_root=None,
        ca_file=None,
        poll_interval=2.0,
        reconnect_interval=1.0,
        operation_timeout=10.0,
        max_voxels=100000,
        max_asset_bytes=64 * 1024 * 1024,
    ):
        self.origin = upstream_origin(upstream_url)
        self._token = configured_token(token, "AETHERTWIN_UPSTREAM_TOKEN")
        if not isinstance(self._token, str) or len(self._token) < 32:
            raise ValueError(
                "AETHERTWIN_UPSTREAM_TOKEN must contain at least 32 characters"
            )
        if not all(
            math.isfinite(value) and value > 0
            for value in (
                poll_interval,
                reconnect_interval,
                operation_timeout,
                max_asset_bytes,
                max_voxels,
            )
        ):
            raise ValueError("Gateway bounds and intervals must be positive and finite")
        self.poll_interval = float(poll_interval)
        self.reconnect_interval = float(reconnect_interval)
        self.operation_timeout = float(operation_timeout)
        self.max_voxels = int(max_voxels)
        self.max_asset_bytes = int(max_asset_bytes)
        self.asset_root = Path(asset_root or "artifacts/gateway_assets").resolve()
        self.asset_root.mkdir(parents=True, exist_ok=True)
        self.ssl_context = ssl.create_default_context(
            cafile=None if ca_file is None else str(ca_file)
        )
        self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(4)
        self._stopping = threading.Event()
        self._thread = None
        self._loop = None
        self._client = None
        self._stop_async = None
        self._hub = None
        self._controls = None
        self._cache = {}
        self._health = {}
        self._control_cache = {
            "revision": 0,
            "applied_revision": 0,
            "pending": 0,
            "history": [],
        }
        self._upstream_role = None
        self._connected = False
        self._error = "Connecting to configured upstream"
        self._asset_error = None
        self._last_packet = None
        self._asset_retry = None
        self._auth_lock = None
        self._source_processed = None
        self._frames = 0
        self._reconnects = 0
        self._resyncs = 0
        self._active_operations = 0
        self._asset_revision = None
        self._asset_generations = deque()
        previous = sorted(
            (
                path
                for path in self.asset_root.iterdir()
                if path.is_dir() and re.fullmatch(r"rev_[a-f0-9]{24}", path.name)
            ),
            key=lambda path: path.stat().st_mtime,
        )
        for path in previous[:-2]:
            self._remove_cache_directory(path)
        self._asset_generations.extend(path.name for path in previous[-2:])
        for path in self.asset_root.iterdir():
            if path.is_dir() and path.name.startswith(".stage_"):
                self._remove_cache_directory(path)

    def ready(self):
        """Upstream connected and its last scene packet is under 3 s old."""
        with self._lock:
            return (
                self._connected
                and self._last_packet is not None
                and time.time() - self._last_packet <= 3
            )

    def safe_error(self, error):
        return (str(error) or type(error).__name__).replace(self._token, "[redacted]")[
            :2048
        ]

    def cached(self, name, default=None):
        with self._lock:
            return copy.deepcopy(self._cache.get(name, default))

    def capabilities(self):
        with self._lock:
            allowed = self._cache.get("capabilities", {})
            return {
                action: bool(
                    self._connected
                    and self._upstream_role == "operator"
                    and allowed.get(action) is True
                )
                for action in ACTIONS
            }

    def control_status(self):
        with self._lock:
            return copy.deepcopy(self._control_cache)

    def bootstrap(self):
        with self._lock:
            names = (
                "identity",
                "source_mode",
                "hardware_status",
                "paused",
                "completed",
                "source_active",
                "available_sources",
                "replay",
                "session_id",
                "halo",
            )
            details = {
                name: copy.deepcopy(self._cache[name])
                for name in names
                if name in self._cache
            }
            upstream_error = self._cache.get("error")
            transport_error = self._error
            asset_error = self._asset_error
            connected = self._connected
            gateway = {
                "mode": "private_relay",
                "upstream_origin": self.origin,
                "connected": connected,
                "upstream_role": self._upstream_role,
                "frames_relayed": self._frames,
                "reconnects": self._reconnects,
                "resync_requests": self._resyncs,
                "last_packet_age_seconds": None
                if self._last_packet is None
                else max(0.0, time.time() - self._last_packet),
                "source_age_seconds": None
                if self._source_processed is None
                else max(0.0, time.time() - self._source_processed),
                "stale": not connected
                or self._last_packet is None
                or time.time() - self._last_packet > 3,
                "upstream_health": copy.deepcopy(self._health),
                "http_operation_capacity": 4,
                "http_in_flight": self._active_operations,
                "upstream_streams": int(connected),
            }
        details.update(
            capabilities=self.capabilities(),
            error=transport_error or asset_error or upstream_error,
            source_mode=details.get("source_mode", "unspecified"),
            gateway=gateway,
            map_manifest_url="/api/v2/assets/manifest.json"
            if self._asset_revision == self.cached("map_revision")
            and self._asset_revision is not None
            else None,
        )
        if not connected:
            details["source_active"] = False
        return details

    def start(self, hub, controls):
        if self._thread is not None:
            raise RuntimeError("Gateway already started")
        self._hub = hub
        self._controls = controls
        self._thread = threading.Thread(
            target=self._thread_main, name="aethertwin-upstream-relay", daemon=False
        )
        self._thread.start()

    def _thread_main(self):
        try:
            asyncio.run(self._run())
        except Exception as exc:
            with self._lock:
                self._connected = False
                self._error = "Gateway stopped: " + self.safe_error(exc)
        finally:
            self._loop = None

    async def _run(self):
        self._loop = asyncio.get_running_loop()
        self._stop_async = asyncio.Event()
        async with httpx.AsyncClient(
            verify=self.ssl_context,
            trust_env=False,
            follow_redirects=False,
            headers={"Accept-Encoding": "identity"},
            timeout=httpx.Timeout(
                self.operation_timeout, connect=min(5.0, self.operation_timeout)
            ),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        ) as client:
            self._client = client
            while not self._stopping.is_set():
                poll = None
                try:
                    await self._authenticate()
                    await self._refresh()
                    cookie = self._session_cookie()
                    ws_url = (
                        self.origin.replace("https://", "wss://", 1).replace(
                            "http://", "ws://", 1
                        )
                        + "/ws/v2/scene"
                    )
                    async with connect(
                        ws_url,
                        origin=self.origin,
                        additional_headers={"Cookie": cookie},
                        ssl=self.ssl_context if ws_url.startswith("wss:") else None,
                        proxy=None,
                        compression=None,
                        open_timeout=min(5.0, self.operation_timeout),
                        close_timeout=2,
                        max_size=MAX_PACKET,
                        max_queue=2,
                        ping_interval=PING_INTERVAL_SECONDS,
                        ping_timeout=PING_TIMEOUT_SECONDS,
                    ) as ws:
                        with self._lock:
                            self._connected = True
                            self._error = None
                        accumulator = RelayAccumulator(self.max_voxels)
                        awaiting_keyframe = True
                        poll = asyncio.create_task(self._poll())
                        while not self._stopping.is_set():
                            try:
                                packet = await asyncio.wait_for(ws.recv(), timeout=0.5)
                            except TimeoutError:
                                continue
                            if not isinstance(packet, bytes):
                                raise ValueError(
                                    "Upstream scene requires binary protocol 2"
                                )
                            try:
                                header, _ = decode_packet(packet)
                                if awaiting_keyframe and header["kind"] != "keyframe":
                                    raise ValueError("Resynchronization required")
                                encoded = await asyncio.to_thread(
                                    accumulator.apply, packet
                                )
                            except ValueError:
                                self._resyncs += 1
                                awaiting_keyframe = True
                                await ws.send(json.dumps({"type": "resync"}))
                                continue
                            awaiting_keyframe = False
                            self._hub.publish(encoded)
                            with self._lock:
                                self._frames += 1
                                self._last_packet = time.time()
                                stamp = encoded.scene.get("processed_at_unix")
                                if isinstance(stamp, (int, float)) and np.isfinite(
                                    stamp
                                ):
                                    self._source_processed = float(stamp)
                except Exception as exc:
                    with self._lock:
                        self._connected = False
                        self._error = "Upstream unavailable: " + self.safe_error(exc)
                        self._reconnects += 1
                finally:
                    if poll:
                        poll.cancel()
                        await asyncio.gather(poll, return_exceptions=True)
                    with self._lock:
                        self._connected = False
                if not self._stopping.is_set():
                    try:
                        await asyncio.wait_for(
                            self._stop_async.wait(), timeout=self.reconnect_interval
                        )
                    except TimeoutError:
                        pass
        self._client = None

    async def _fetch(self, method, path, *, body=None, limit=MAX_HEADER, reauth=True):
        if path not in (
            "/api/v2/session",
            "/api/v2/bootstrap",
            "/api/v2/bootstrap?include_latest=false",
            "/api/v2/health",
            "/api/v2/commands",
            "/api/v2/trajectory.csv",
        ):
            self._asset_path(path)
        async with self._client.stream(
            method, self.origin + path, json=body
        ) as response:
            if response.headers.get("content-encoding", "identity").lower() not in (
                "",
                "identity",
            ):
                raise ValueError(
                    "Compressed upstream HTTP responses are not accepted by the bounded relay"
                )
            data = bytearray()
            async for part in response.aiter_raw():
                if len(data) + len(part) > limit:
                    raise ValueError("Upstream HTTP response exceeds configured bound")
                data.extend(part)
            status = response.status_code
        if status == 401 and reauth and path != "/api/v2/session":
            await self._authenticate()
            return await self._fetch(method, path, body=body, limit=limit, reauth=False)
        return status, bytes(data)

    async def _json(self, method, path, *, body=None, limit=MAX_HEADER, reauth=True):
        status, data = await self._fetch(
            method, path, body=body, limit=limit, reauth=reauth
        )
        try:
            value = json.loads(
                data,
                parse_constant=lambda _: (_ for _ in ()).throw(
                    ValueError("Nonfinite upstream JSON")
                ),
            )
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"Upstream returned invalid JSON ({status})") from exc
        if not isinstance(value, dict):
            raise ValueError("Upstream JSON object required")
        return status, value

    async def _authenticate(self):
        # Single flight: concurrent 401s must not clear the jar under each other.
        if self._auth_lock is None:
            self._auth_lock = asyncio.Lock()
        async with self._auth_lock:
            await self._authenticate_once()

    async def _authenticate_once(self):
        self._client.cookies.clear()
        status, data = await self._json(
            "POST", "/api/v2/session", body={"token": self._token}, reauth=False
        )
        if status != 200:
            raise ValueError(f"Upstream authentication failed ({status})")
        if data.get("role") not in ("operator", "viewer"):
            raise ValueError("Upstream session role is invalid")
        self._session_cookie()
        with self._lock:
            self._upstream_role = data["role"]

    def _session_cookie(self):
        candidates = [
            cookie
            for cookie in self._client.cookies.jar
            if cookie.name == "aethertwin_session"
        ]
        if len(candidates) != 1 or len(candidates[0].value) > 2048:
            raise ValueError("Upstream did not issue a bounded session cookie")
        cookie = candidates[0]
        if not re.fullmatch(r"[A-Za-z0-9_.=-]+", cookie.value):
            raise ValueError("Invalid upstream session cookie value")
        if not any(str(key).lower() == "httponly" for key in cookie._rest):
            raise ValueError("Upstream session cookie must be HttpOnly")
        if self.origin.startswith("https:") and not cookie.secure:
            raise ValueError("HTTPS upstream session cookie must be Secure")
        if self.origin.startswith("http:") and cookie.secure:
            raise ValueError(
                "Upstream issued a Secure cookie; configure HTTPS for this edge"
            )
        return "aethertwin_session=" + cookie.value

    async def _refresh(self):
        status, data = await self._json("GET", "/api/v2/bootstrap?include_latest=false", limit=MAX_PACKET)
        if status != 200:
            raise ValueError(f"Upstream bootstrap failed ({status})")
        if data.get("protocol_version") != 2:
            raise ValueError("Upstream protocol 2 is required")
        if (
            not isinstance(data.get("capabilities"), dict)
            or not isinstance(data.get("map_revision"), str)
            or not 0 < len(data["map_revision"]) <= 128
        ):
            raise ValueError("Invalid upstream capabilities/map identity")
        if (
            type(data.get("control_revision")) is not int
            or data["control_revision"] < 0
        ):
            raise ValueError("Invalid upstream control revision")
        with self._lock:
            self._cache = {
                key: value
                for key, value in data.items()
                if key not in ("latest", "role", "stream_url", "csrf_token")
            }
        for path, target in (
            ("/api/v2/health", "_health"),
            ("/api/v2/commands", "_control_cache"),
        ):
            code, value = await self._json("GET", path)
            if code != 200:
                raise ValueError(f"Upstream status failed ({code})")
            if target == "_control_cache":
                if not isinstance(value.get("history", []), list) or any(
                    type(value.get(name)) is not int or value[name] < 0
                    for name in ("revision", "applied_revision", "pending")
                ):
                    raise ValueError("Invalid upstream command status")
                value = {**value, "history": value.get("history", [])[-128:]}
            with self._lock:
                setattr(self, target, value)
        if data.get("map_manifest_url"):
            revision = str(data.get("map_revision", "unavailable"))
            retry = self._asset_retry
            if retry and retry[0] == revision and time.monotonic() < retry[2]:
                return
            try:
                await self._sync_assets(data["map_manifest_url"], revision)
                self._asset_retry = None
            except Exception as exc:
                attempts = retry[1] + 1 if retry and retry[0] == revision else 1
                delay = min(
                    ASSET_RETRY_MAX_SECONDS, ASSET_RETRY_MIN_SECONDS * 2 ** (attempts - 1)
                )
                self._asset_retry = (revision, attempts, time.monotonic() + delay)
                with self._lock:
                    self._asset_error = (
                        "Upstream map assets unavailable: " + self.safe_error(exc)
                    )

    async def _poll(self):
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_async.wait(), timeout=self.poll_interval
                )
            except TimeoutError:
                pass
            if self._stopping.is_set():
                return
            try:
                await self._refresh()
                with self._lock:
                    if self._connected:
                        self._error = None
            except Exception as exc:
                with self._lock:
                    self._error = "Upstream status unavailable: " + self.safe_error(exc)

    def _asset_path(self, url):
        parsed = urlsplit(str(url))
        if parsed.scheme or parsed.netloc:
            if f"{parsed.scheme}://{parsed.netloc}" != self.origin:
                raise ValueError("Cross-origin upstream assets are forbidden")
        path = parsed.path
        if (
            parsed.query
            or parsed.fragment
            or not re.fullmatch(r"/api/v2/assets/[A-Za-z0-9_./-]+", path)
        ):
            raise ValueError("Asset path is not allowlisted")
        tail = path.removeprefix("/api/v2/assets/")
        if (
            ".." in PurePosixPath(tail).parts
            or tail.startswith("/")
            or not (tail.endswith(".glb") or tail == "manifest.json")
        ):
            raise ValueError("Unknown upstream asset")
        return path, tail

    def _cache_path(self, relative):
        path = (self.asset_root / relative).resolve()
        if not path.is_relative_to(self.asset_root) or path == self.asset_root:
            raise ValueError("Unsafe gateway cache path")
        return path

    def _remove_cache_directory(self, path):
        target = Path(path).resolve()
        if (
            not target.is_relative_to(self.asset_root)
            or target == self.asset_root
            or not (
                re.fullmatch(r"rev_[a-f0-9]{24}", target.name)
                or re.fullmatch(r"\.stage_[A-Za-z0-9_-]+", target.name)
            )
        ):
            raise ValueError("Unsafe gateway cache cleanup target")
        shutil.rmtree(target)

    async def _sync_assets(self, url, revision):
        if self._asset_revision == revision:
            return
        path, _ = self._asset_path(url)
        if not path.endswith("/manifest.json"):
            raise ValueError("Expected the allowlisted asset manifest")
        status, manifest = await self._json("GET", path)
        if (
            status != 200
            or manifest.get("map_revision") != revision
            or manifest.get("coordinate_frame") != "world_z_up"
        ):
            raise ValueError("Upstream asset identity mismatch")
        tiles = manifest.get("tiles")
        if not isinstance(tiles, list) or len(tiles) > 128:
            raise ValueError("Upstream tile count exceeds bound")
        folder = "rev_" + hashlib.sha256(revision.encode()).hexdigest()[:24]
        directory = Path(tempfile.mkdtemp(prefix=".stage_", dir=self.asset_root))
        result = copy.deepcopy(manifest)
        downloaded = {}
        total = 0
        try:
            for tile in result["tiles"]:
                lods = tile.get("lods")
                if not isinstance(lods, list) or not 1 <= len(lods) <= 8:
                    raise ValueError("Invalid upstream LOD directory")
                for lod in lods:
                    remote, tail = self._asset_path(lod.get("url", ""))
                    if not tail.endswith(".glb"):
                        raise ValueError("Only GLB tile assets may be mirrored")
                    if remote not in downloaded:
                        code, data = await self._fetch(
                            "GET",
                            remote,
                            limit=min(MAX_PACKET, self.max_asset_bytes - total),
                        )
                        if (
                            code != 200
                            or len(data) < 12
                            or data[:4] != b"glTF"
                            or int.from_bytes(data[4:8], "little") != 2
                            or int.from_bytes(data[8:12], "little") != len(data)
                        ):
                            raise ValueError("Invalid upstream GLB asset")
                        total += len(data)
                        if total > self.max_asset_bytes:
                            raise ValueError(
                                "Upstream asset revision exceeds cache budget"
                            )
                        local = (directory / tail).resolve()
                        if not local.is_relative_to(directory):
                            raise ValueError("Unsafe staged asset path")
                        local.parent.mkdir(parents=True, exist_ok=True)
                        await asyncio.to_thread(local.write_bytes, data)
                        downloaded[remote] = folder + "/" + tail
                    lod["url"] = "/api/v2/assets/" + downloaded[remote]
                    lod["file"] = downloaded[remote]
            encoded = json.dumps(
                result, separators=(",", ":"), allow_nan=False
            ).encode()
            final = self._cache_path(folder)
            if final.exists():
                await asyncio.to_thread(self._remove_cache_directory, final)
            await asyncio.to_thread(directory.rename, final)
            temporary = self._cache_path("manifest.json.tmp")
            await asyncio.to_thread(temporary.write_bytes, encoded)
            await asyncio.to_thread(
                temporary.replace, self.asset_root / "manifest.json"
            )
            if folder in self._asset_generations:
                self._asset_generations.remove(folder)
            self._asset_generations.append(folder)
            while len(self._asset_generations) > 2:
                old = self._asset_generations.popleft()
                await asyncio.to_thread(
                    self._remove_cache_directory, self._cache_path(old)
                )
            with self._lock:
                self._asset_revision = revision
                self._asset_error = None
        finally:
            if directory.exists():
                await asyncio.to_thread(self._remove_cache_directory, directory)

    async def _dispatch(self, operation, data):
        if operation == "command":
            status, result = await self._json("POST", "/api/v2/commands", body=data)
            if status not in (202, 409):
                raise ValueError(f"Upstream command failed ({status})")
            if (
                result.get("request_id") != data["request_id"]
                or result.get("status") not in ("accepted", "rejected")
                or type(result.get("revision")) is not int
            ):
                raise ValueError("Invalid upstream command acknowledgement")
            with self._lock:
                self._cache["control_revision"] = result["revision"]
            return result
        if operation == "trajectory":
            status, body = await self._fetch(
                "GET", "/api/v2/trajectory.csv", limit=MAX_PACKET
            )
            if status != 200:
                try:
                    reason = json.loads(body).get("detail", "No trajectory available")
                except (ValueError, AttributeError):
                    reason = "No trajectory available"
                raise ValueError(f"Upstream trajectory rejected ({status}): {reason}")
            return body.decode("utf-8")
        raise ValueError("Unknown gateway operation")

    def invoke(self, operation, data=None):
        if self._stopping.is_set() or self._loop is None or not self._loop.is_running():
            raise ValueError("Gateway upstream loop is unavailable")
        if not self._slots.acquire(blocking=False):
            raise ValueError("Gateway HTTP operation capacity reached")
        with self._lock:
            self._active_operations += 1
        try:
            future = asyncio.run_coroutine_threadsafe(
                asyncio.wait_for(
                    self._dispatch(operation, data), self.operation_timeout
                ),
                self._loop,
            )
            try:
                return future.result(timeout=self.operation_timeout + 1)
            except TimeoutError:
                future.cancel()
                raise ValueError("Upstream operation timed out")
        finally:
            with self._lock:
                self._active_operations -= 1
            self._slots.release()

    def trajectory_csv(self):
        if not self.bootstrap()["gateway"]["connected"]:
            raise ValueError("Upstream is disconnected; trajectory export unavailable")
        return self.invoke("trajectory")

    def close(self):
        self._stopping.set()
        if self._loop and self._stop_async:
            try:
                self._loop.call_soon_threadsafe(self._stop_async.set)
            except RuntimeError:
                pass
        if self._thread:
            self._thread.join(timeout=self.operation_timeout + 5)
            if self._thread.is_alive():
                raise RuntimeError("Gateway relay did not stop within its bound")


def create_gateway_app(
    upstream_url,
    *,
    asset_root=None,
    frontend_root=None,
    upstream_token=None,
    ca_file=None,
    host="127.0.0.1",
    operator_token=None,
    viewer_token=None,
    allowed_origins=(),
    local_development=True,
    hub=None,
    **runtime_options,
):
    runtime = GatewayRuntime(
        upstream_url,
        token=upstream_token,
        asset_root=asset_root,
        ca_file=ca_file,
        **runtime_options,
    )
    controls = ForwardControlPlane(runtime)
    app = create_app(
        runtime,
        hub=hub or SceneHub(),
        controls=controls,
        host=host,
        asset_root=runtime.asset_root,
        frontend_root=frontend_root,
        operator_token=operator_token,
        viewer_token=viewer_token,
        allowed_origins=allowed_origins,
        local_development=local_development,
        ready=runtime.ready,
    )
    app.state.gateway_runtime = runtime
    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default=os.environ.get("AETHERTWIN_UPSTREAM_URL"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument(
        "--asset-cache", type=Path, default=Path("artifacts/gateway_assets")
    )
    parser.add_argument(
        "--frontend",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "web/dist",
    )
    parser.add_argument(
        "--ca-file", type=Path, default=os.environ.get("AETHERTWIN_UPSTREAM_CA_FILE")
    )
    parser.add_argument("--ssl-certfile", type=Path)
    parser.add_argument("--ssl-keyfile", type=Path)
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[
            origin.strip()
            for origin in os.environ.get("AETHERTWIN_ALLOWED_ORIGINS", "").split(",")
            if origin.strip()
        ],
    )
    args = parser.parse_args(argv)
    if not args.upstream:
        parser.error("--upstream or AETHERTWIN_UPSTREAM_URL is required")
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        parser.error("Both TLS certificate and key are required")
    from .auth import is_loopback
    if not is_loopback(args.host) and not args.ssl_certfile:
        parser.error("Private network listeners require TLS certificate and key")
    app = create_gateway_app(
        args.upstream,
        asset_root=args.asset_cache,
        frontend_root=args.frontend,
        ca_file=args.ca_file,
        host=args.host,
        allowed_origins=args.allowed_origin,
    )
    import uvicorn
    from .auth import configure_audit_log

    configure_audit_log()
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        proxy_headers=False,
        ws_max_size=4096,
        access_log=False,
        timeout_graceful_shutdown=5,
        timeout_keep_alive=5,
        ssl_certfile=None if args.ssl_certfile is None else str(args.ssl_certfile),
        ssl_keyfile=None if args.ssl_keyfile is None else str(args.ssl_keyfile),
        ssl_ciphers=TLS_CIPHERS,
    )


if __name__ == "__main__":
    main()

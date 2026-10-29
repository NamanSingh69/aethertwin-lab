# Private AetherTwin gateway

The gateway serves the existing browser build and relays a configured edge. It
does not load sensor drivers, PyTorch, Open3D, MuJoCo, checkpoints, or perception
models. Its CPU work is limited to authentication, bounded wire validation/delta
reconstruction, serialization and HTTP/static-asset forwarding.

## Local or private-server setup

Use CPython 3.12 and an isolated environment. Install the matching hash lock:

```text
python -m pip install --require-hashes -r deployment/gateway/requirements-windows.lock
```

For x86-64 Linux use `requirements-linux.lock` instead. The profile contains 19
runtime packages, rooted at NumPy, FastAPI, uvicorn, httpx and websockets. No ML
or physical-device dependencies are installed. Build `web/dist` with `npm ci`
and `npm run build` in `web/`, or use the included build.

Configure `AETHERTWIN_UPSTREAM_URL` and `AETHERTWIN_UPSTREAM_TOKEN` in the
**server environment**. The upstream token must identify the intended edge
viewer/operator account and contain at least 32 characters. It is sent only in
the server-to-server session POST, never in a URL, browser response, asset or
WebSocket query. Gateway and edge credentials are separate.

```text
python -m command_center.gateway --upstream https://edge.example.internal:8080 --host 127.0.0.1 --port 8081 --ca-file /path/to/edge-ca.pem
```

An HTTP upstream is accepted only for a loopback address. Remote upstreams
require HTTPS; certificate and hostname verification stay enabled. A private CA
can be supplied with `--ca-file`; there is no insecure-verification option.
Redirects, environment-configured HTTP proxies and browser-selected upstream
destinations are not accepted.

For authenticated browser access, set separate `AETHERTWIN_OPERATOR_TOKEN` and
optionally `AETHERTWIN_VIEWER_TOKEN`, then provide `--ssl-certfile` and
`--ssl-keyfile`. Each credential must contain at least 32 characters. Network
binding requires an operator credential. Local loopback-only development with
no gateway credentials retains the existing service's explicit development
operator session. Production Compose requires separate operator/viewer credentials
and TLS; omit an optional viewer variable entirely for a CLI deployment rather
than setting it to an empty string.

An operator on the gateway can use only capabilities allowed by the upstream
credential/source. A local viewer cannot issue commands even if the upstream
account is an operator. An upstream viewer cannot be promoted by a local
operator. No relay command actuates a physical robot.

## Docker / Compose

The Dockerfile copies only the relay/service/auth/protocol modules, the lightweight
JSON serializer and `web/dist`. The Dockerfile-specific ignore file excludes
models, sensors, recordings, development environments and node_modules from the
build context. Runtime is an unprivileged UID 10001 with a read-only filesystem,
dropped capabilities and a dedicated writable asset volume.

From the project root, build locally without publication:

```text
docker build -f deployment/gateway/Dockerfile -t aethertwin-gateway:local .
docker compose -f deployment/gateway/compose.yaml up --build
```

Supply actual credentials through the server environment; `.env.example`
contains names only. The Compose TLS directory must contain `fullchain.pem`,
`privkey.pem`, and `upstream-ca.pem`, readable by container UID 10001. The last
file is the trusted edge CA bundle. For a publicly trusted upstream, omit the
Compose `--ca-file` pair and use the system trust store. TLS keys/certificates and
actual environment files are excluded from source. The default published bind
is localhost:8443; select a private bind address explicitly when needed.

The Python base version is pinned through `PYTHON_IMAGE`; an organization can
override this build argument with its reviewed digest. Python dependencies are
version/hash locked for CPython 3.12 x86-64. Docker's health check confirms the
listener; authenticated `/api/v2/health` includes actual upstream/cache status.
The default Python 3.12.16 base matches the
[official Bookworm image source](https://github.com/docker-library/python/blob/master/3.12/slim-bookworm/Dockerfile).

Docker container execution requires a running Linux Docker engine. No engine,
host firewall, network setting, driver, certificate trust store, physical sensor,
or firmware setting is changed by these files or tests.

## Relay and recovery contracts

- Exactly one authenticated upstream WebSocket serves all local clients. The
  upstream session is acquired with a verified HttpOnly cookie; HTTPS cookies
  must also be Secure. Tokens and cookies remain server side.
- Original stream identity, sequence, source, calibration identity and processing
  timestamps are retained. Each accepted delta yields a complete recoverable
  keyframe. A missing base requests upstream resynchronization.
- The upstream reader permits two queued WebSocket messages. The shared hub
  retains one incoming frame and one pending frame per subscriber; browser
  connections retain two outgoing packets and disconnect on slow sends. The
  default local client capacity is eight. HTTP forwarding permits four active
  operations and rejects excess work instead of adding an unbounded queue.
- Bootstrap, health, capabilities, halo and command history are cached every two
  seconds. Cache reads do not perform network I/O on the ASGI loop. Commands and
  CSV exports use bounded asynchronous upstream requests through the service's
  worker-thread adapters. Revision rejection and remote application failures
  remain visible; commands are never silently rebased or replayed as success.
- Disconnects retain the last original frame, mark transport/source age stale,
  disable forwarded controls and expose an error. Reconnect obtains a new
  authenticated session and keyframe. There is no simulated-data fallback.
- Only URLs declared by the authenticated same-origin asset manifest are fetched.
  Traversal, query-selected destinations, redirects and foreign origins are
  rejected. At most 128 tiles, eight LODs/tile and 64 MiB/revision are cached.
  Two committed revisions and one bounded staging revision are the maximum
  (192 MiB plus manifests). Failed staging is removed; commits are atomic.
  Use one gateway process per dedicated asset-cache directory.

## Tests and evidence

```text
python -m pytest tests/test_gateway.py -q
```

Tests create an isolated TLS fixture edge on a random localhost port. OpenSSL
creates temporary test certificates, trusted only by the test client; no system
certificate store is changed. Tests cover actual network/session authentication,
one-stream fanout, source/calibration/sequence preservation, delta resync,
disconnect/reconnect, viewer restrictions, revision/application failures,
allowlisted assets/cache limits, bounded forwarding and responsive ASGI health
requests. A guarded subprocess rejects ML and sensor imports.

Hash locks were generated from the downloaded platform wheels using
`lock_profile.py`; that script does not resolve or publish packages. Re-resolve
the five pinned inputs into a clean wheel directory before regenerating a lock.

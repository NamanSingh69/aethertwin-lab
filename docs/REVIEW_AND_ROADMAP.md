# Review and roadmap

Written 2026-10-30 at the end of the improvement branch
`claude/aethertwin-project-review-w5h7gr`. It records what was reviewed, what
changed, what was measured (and where), and a prioritised backlog. Everything
not explicitly measured below is **unverified**. Laptop numbers are not A2000
workstation numbers; cloud GPU numbers are not lab numbers; simulation is not
physical accuracy; `hardware_status` stays `not_tested`.

## Where measurements come from

| Label | Hardware and settings |
|---|---|
| Laptop | ASUS ROG Zephyrus M16, Intel i9-13900H, NVIDIA RTX 4070 Laptop GPU (driver 572.60), Windows 11, AC power, Windows "Turbo" plan, 240 Hz panel, Chrome 156.0.8078.49 headed through `web/tools/run-benchmark.mjs` (vsync on, 1920x1080 buffer, pixel ratio 1, 3 x 60 s). Raw runs: `artifacts/renderer_benchmarks/`. |
| Cloud | Modal T4, L4 and A10 (driver 610.57.04, torch 2.14.1+cu126), 4 reserved cores, synthetic simulator. Raw runs and limits: `reports/modal/`. |
| Software | pytest on Windows (Python 3.12.10) and on Modal T4 from the CUDA lock; Vitest; Playwright with SwiftShader (mock) and against the real simulator service (live). |

## Critique by area, and what this branch changed

### Architecture
The split between a measured-data pipeline, a revisioned command plane and a
presentation-only studio is sound, and the evidence-honesty rules are applied
consistently. Weak spots were a 1,400-line studio component (split into hooks
and components earlier on this branch) and a renderer class that rebuilt its
whole scene graph every frame (fixed below). The pipeline report could not say
where time went; it now reports `module_latency_ms` per stage.

### Performance (browser)
Before: every frame update disposed and recreated every overlay's geometry and
material and wrote 100,000 instance matrices through `setMatrixAt`. Now voxels
write only translation and colour in place with ranged uploads, overlays use
preallocated growable buffers, picking and markers are instanced, and
geofences rebuild only when they change.

| Laptop, balanced | Static 100k instances | Streaming, all overlays at 30 Hz |
|---|---|---|
| Before | 238.1-238.4 fps, p95 4.3-4.4 ms | 32.7-34.0 fps, p95 46-47 ms, update p95 26-27 ms |
| After | 237.0-238.6 fps, p95 4.3 ms | 237.3-238.6 fps, p95 4.4-5.3 ms, update p95 1.7-2.3 ms |

Static rendering was display-capped before and after (no regression); the
streaming case went from a CPU-bound 33 fps to the 240 Hz cap. Quality tiers
(`auto`, `performance` with on-demand rendering, `balanced`, `cinematic`),
adaptive pixel ratio, an offscreen pause and `data-*` hooks on the viewport
were added. Bundle: `three-vendor` 638.2 kB (baseline 635.7 kB); effects
(19.3 kB) and the meshopt decoder (26.5 kB) load lazily; the initial studio
load is 97.7 kB gzip, enforced by `npm run size`.

### Cinematic effects and camera
Bloom, fog, a slow decorative pulse from the sensor pose (1.2 m/s, every 10 s,
deliberately unlike the real scan), animated path dashes, breathing rings under
tracks and a breached-zone pulse, all labelled "Effects · presentation only",
loaded only for the cinematic tier, and motion-free under reduced motion.
Presets and track focus fly with an eased move; there is a one-time intro
dolly. Laptop cinematic runs stayed at the 240 Hz cap (static 239.0-239.3 fps,
streaming 238.3-238.9 fps), so the effects cost less than 4.17 ms per frame
there. An earlier batch measured 179-190 fps but did not record whether effects
were active, so it was discarded; GPU clock state probably explains the spread.
`auto` thresholds (cinematic after 10 s at p95 <= 16.7 ms, back after 3 s over
25 ms) are a starting point, not tuned on several devices.

### UI and UX
Added keyboard shortcuts with an accessible help dialog, a persisted quality
selector, self-dismissing notices (errors stay), and measured-map progress.
The new map-progress test exposed a real bug: failing tiles starved the rest of
the map and re-raised an error every second; failed tiles now wait 10 s.

### Testing
Playwright mock (21 tests, 42/42 with `--repeat-each=2`) including axe, live
(4/4 against the simulator), Vitest 44 (+1 optional), pytest 343 passed and 2
skipped on Windows, 334 passed and 1 skipped from the CUDA lock on Linux.
Local Playwright workers are capped at 4: SwiftShader saturates CPU threads,
and the default worker count timed out 10 of 12 tests on a 20-thread laptop.

### CI/CD
Workflows pin every action to a commit SHA, default to `contents: read`, use
per-ref concurrency and do not persist checkout credentials. The browser job
now runs typecheck, tests, build, a committed-`dist` drift check, the bundle
budget and the mock E2E suite; a live E2E job runs against the real simulator;
CodeQL covers TypeScript and Python; Dependabot groups npm, Actions and the
gateway image; a tag-driven release workflow attests artifacts and opens a
draft release and pushes the gateway image with an SBOM. The release workflow
has not run yet (it needs a `v*` tag).

### 3D assets and modelling
Tiles load through one `GLTFLoader` with `MeshoptDecoder`, so tiles compressed
with `EXT_meshopt_compression` decode in WebAssembly (the CSP already allows
`wasm-unsafe-eval`). To compress existing tiles:
`npx @gltf-transform/cli meshopt in.glb out.glb` (or `quantize` then
`meshopt`). The Python exporter (`command_center/assets.py`) writes plain GLB;
emitting meshopt there needs a Python meshoptimizer binding or a post-export
gltf-transform step. KTX2 textures need the Basis transcoder files served
same-origin and are not set up.

### IoT web server (gateway and ESP32 halo)
Service and gateway: unauthenticated `/healthz` and `/readyz` (status only),
per-peer sign-in rate limit with `Retry-After`, a JSON audit log without
credentials, token files for Compose secrets, HSTS on HTTPS, scene streams that
close at session expiry, single-flight upstream re-authentication, explicit
upstream ping/timeout, asset-sync backoff and a modern TLS policy. Compose uses
secrets, resource limits, log rotation and a liveness check; the Dockerfile
pins the base image by digest. Docker is not installed on the laptop, so the
container files were not built or run. Halo: the serial transport reconnects
after a lost link or a missing port, reports firmware error lines and keeps
shared state under its lock. The firmware is unchanged and nothing was tested
on an ESP32.

### Security
See "Security review" below.

## Cloud GPU benchmarks (Modal)

Full tables and limits: `reports/modal/README.md`. In short, on T4, L4 and A10:

- The perception model's forward pass takes 3.7-7.4 ms in fp32;
  `torch.compile` is exact and about 30 % faster; cudnn.benchmark changes
  nothing; TF32 helps Ampere/Ada slightly with ~0.009 output error.
- Mixed precision does not run as written, and a bf16 cast experiment was
  slower than fp32, so it was not adopted.
- The whole pipeline is ~385-486 ms p50 on CUDA versus ~500-617 ms on the
  container CPU. **Occupancy and planning dominate on CPU; the 35 ms target is
  not reachable with GPU changes alone.**
- Seeds without the bundled twin (`assets/twin_seed7`) spent ~1.75 s per
  frame in perception and tracking, about 26x seed 7. **Fixed after the
  measurement:** a runtime-built twin keeps its points in a disk tile store,
  and every background-distance query re-read and decompressed nearby tiles
  and rebuilt a KD-tree per tile per 256-point batch (~17,000 tile reads per
  frame). The store now keeps one exact global tree when it fits 2 M
  centroids, otherwise a point-budgeted LRU of per-tile trees. Laptop CPU,
  seed 101, 12 frames: perception + tracking p50 1,244 -> 120 ms (seed 7:
  115 ms), total p50 ~700 -> 310 ms; results are exact (tested against a
  global search).
- A 15-minute run (1,608 frames, p50 370 ms, p95 520 ms) kept memory flat.
- pytest from `requirements-ubuntu-cuda.lock`: 334 passed, 1 skipped.
- Total Modal spend for all runs: about USD 1 (billing report).
These are cloud GPUs with simulated sensors. The A10 shares the A2000's Ampere
generation but is a much larger part; no A2000 figure is claimed.

## Prioritised backlog

**P0, correctness and evidence**
1. Done: the seed-dependent perception slowdown was uncached disk-tile
   distance queries (see "Cloud GPU benchmarks"). Re-run the Modal sweep to
   confirm on cloud CPUs. [software]
2. Occupancy and planning latency: move ray clearing fully to the native
   backend, bound MPPI samples by deadline. Planning's p95 tail (laptop CPU,
   seed 7, 30 frames: p50 93 ms, p95 500 ms) comes from periodic replans
   (A* plus the scipy spline optimisation, ~200 ms each), not from the
   per-frame path check: batching that check across segments was exact but
   changed nothing measurable, so it was not kept.
   Re-run `tools/modal_bench.py --suite latency` and the laptop before/after. [software]
3. Physical commissioning per `docs/RASPBERRY_PI_WINDOWS.md` and the
   Pi-to-Windows raw-sensor transport (does not exist yet). [HW]

**P1, performance and platform**
4. Adopt `torch.compile` for inference behind a config flag with a warm-up and a
   parity check; measure on the A2000 itself. [software, then HW]
5. CUDA lock profile for Windows (`requirements-windows-cuda.lock`) validated on
   the A2000 workstation; record under `reports/` as a workstation profile. [HW]
6. ONNX Runtime / TensorRT export for the A2000 (fp16 needs the overflow and
   scatter dtype issues fixed first). [software, then HW]
7. Tune `auto` quality thresholds on at least an integrated GPU, the RTX
   laptop and the A2000 workstation browser. [HW]
8. OffscreenCanvas rendering in a worker, then a WebGPU renderer
   (`three/webgpu` + TSL) behind a flag; GPU-driven voxel LOD. [software]
9. Compressed telemetry deltas or WebTransport for remote viewing. [software]

**P2, assets and presentation**
10. Meshopt/quantised tiles from the exporter; KTX2 textures; Gaussian-splat or
    meshlet LOD for the measured twin. [software]
11. Effects polish: selective bloom per layer, a shader grid with distance fade.

**IoT and operations**
12. Build and run the gateway container (Docker on Linux), run a TLS scan
    against the cipher policy, document certificate rotation drills. [ops]
13. Halo [HW]: identify the board before flashing; add a device heartbeat or a
    periodic re-HELLO (the firmware already re-identifies on HELLO, but resets
    its link watchdog, which may flicker); inter-byte timeout and `0xA7`
    rescan in the firmware parser; `enableLoopWDT()` and `Wire.setTimeOut`.
    Keep `hardware_verified: false` until verified on a real ESP32 and ring.
14. Prometheus-style `/metrics` on loopback or behind a token; opt-in mDNS.
    Do not add Wi-Fi/BLE/OTA endpoints to the ESP32 without a strong need
    (secure boot, signed images and flash encryption would be required).
15. Session revocation (nonce denylist) for logout; the stream already closes
    at expiry.

## Security review

Checked on this branch before opening the pull request:

- **CSP**: no `unsafe-eval`; `wasm-unsafe-eval` only for the meshopt decoder;
  `blob:` for workers and glTF images. `style-src 'unsafe-inline'` is still
  needed (React style attributes and the benchmark page's inline style).
- **Dependencies**: `npm audit` (all and production only) reports 0
  vulnerabilities; Python stays hash-locked.
- **Independent review** of the branch diff found no critical or high issues.
  Fixed from it: secrets and keys excluded from Modal uploads, atomic per-peer
  sign-in limiting, real single-flight gateway re-authentication, expiry-close
  handling in the scene socket, attestation moved out of the build job, all
  composer passes disposed, no halo re-probing after a failed identity, and
  the secret-file permission note. Kept: `style-src 'unsafe-inline'`
  (benchmark.html has an inline style element).
- **Secrets**: no credentials in the repository, workflows or Compose files;
  the gateway reads secret files; Modal results contain no account data.
- **Input handling**: bounded JSON bodies, path checks on asset and SPA routes,
  WebSocket message and size limits (unchanged); new endpoints take no input.
- **Auth**: sign-in rate limit, audit log, session-bound streams; loopback
  development mode and its DNS-rebinding check are unchanged.
- **CI**: least privilege per job, SHA pins, no `pull_request_target`, tag name
  passed to shell through the environment.

# AetherTwin improvement work — handoff

Written 2026-10-29 at the end of a cloud Claude Code session, for a fresh local
Claude Code session (Windows, ASUS ROG Zephyrus M16 with an NVIDIA RTX laptop GPU)
that has **no memory of that session**. Read this file and `CLAUDE.md` completely
before doing anything. Then verify the baseline (section 11) before changing code.

> **Status 2026-10-30 (local Windows session):** the remaining plan below is
> done — renderer performance and effects, UX, Modal cloud GPU benchmarks and a
> Linux CUDA lock, CI/CD, IoT gateway and halo hardening, the review and
> roadmap doc. Results and the prioritised backlog are in
> `docs/REVIEW_AND_ROADMAP.md`; cloud results in `reports/modal/`; laptop GPU
> benchmark runs in `artifacts/renderer_benchmarks/`. Sections below are the
> original hand-off and are kept as history.

---

## 1. Goal and scope

### Original request (owner's words, condensed only for typos)
> Think about any and all ways to self improve in a critique review loop for this
> website in all aspects, covering all stacks possible like architecture design,
> heavy and faster optimizations, automations, UI/UX, proper E2E tests and CI/CD
> pipelines, setup for more 3D animations and modelling, and whatever else you can
> think of to improve the entire project in an E2E aspect for making it as cool as
> possible with a wow factor but also using the latest research and tech as within
> the given constraints and context we have of the project currently; now begin
> your work and give your best output, ask and stop for any questions you may have
> for connecting the repo and sending the work there.

Later addition: **"also make sure to look into IoT web server part and cover that
as well"** — i.e. the network-facing scene gateway and the ESP32 halo (section 6.5).

### Plan and working rules the owner set (keep following them)
Finish every remaining task to production quality, then open the PR. Plenty of
time is available; favour thoroughness over speed but use context efficiently.

The 6-task plan, in order:
1. Playwright E2E suite (mocked + live backend) with axe accessibility checks;
   the mock fixture must auto-install so mocked tests never reach a real backend
   through Vite's proxy; whole suite green. **(done)**
2. Refactor `App.tsx` into custom hooks and smaller components with **no
   behaviour change**; E2E stays green. **(done)**
3. Renderer performance, cinematic effects and camera animation. **Measure before
   and after; frame time must not regress.**
4. UX: keyboard shortcuts, help overlay, quality selector, toast auto-dismiss,
   map loading progress.
5. CI/CD: E2E jobs, dist drift check, bundle size budget, CodeQL, Dependabot,
   release workflow.
6. Review and roadmap doc (`docs/REVIEW_AND_ROADMAP.md`), then commit, push, open the PR.

How to work (owner's rules):
- **Plan first**: before each task write a short plan; keep the task list updated.
- Use subagents only where they genuinely save time or context (parallel research,
  broad searches, independent review). Not for small inline work.
- **Research before deciding**: where a choice depends on current best practice
  (Playwright/axe setup, CodeQL/Dependabot config, bundle budgets, renderer
  performance techniques), check current docs and follow them.
- **Verify everything**: after each task run build, typecheck, lint and relevant
  tests; fix failures before moving on; never mark a task done while anything is
  failing or half-finished; check the UI visually in a browser where useful.
- **Review your own work** after each major task (correctness, readability,
  regressions). **Before opening the PR do a full security review**: CSP,
  dependencies, secrets, input handling, CI permissions.
- Keep iterating on failures: debug root causes; do not stop or work around.
- Manage context: lean tool output, avoid re-reading large files, compact when
  needed; keep a brief `PROGRESS.md` (local, untracked — see section 9).
- Commit in logical, well-described chunks as each task completes.
- Workspace setup (CLAUDE.md, `.claude/settings.json`, hooks, agents) was to be
  committed separately as "chore: agent workspace setup" — done (`50e8c57`).

**Final report format when everything is done:** what changed per task; test and
CI status; anything deferred or uncertain; the PR link.

---

## 2. Decisions the owner made

- **Destination**: work goes to branch `claude/aethertwin-project-review-w5h7gr`
  on **`NamanSingh69/aethertwin`**, plus **one PR against `main`**. Not to
  `sumedhag28/aethertwin` (the URL in the original message): the cloud session
  could not attach it alongside NamanSingh69/aethertwin because both repos are
  named `aethertwin`. The owner can mirror the PR there later.
- **The App.tsx refactor is approved** (done, `af0a6b2`).
- **Agent workspace setup committed separately** (done, `50e8c57`).
- **Physical/GPU work is local**: the Zephyrus now, the A2000 workstation later.
  The cloud container has no GPU; its WebGL is SwiftShader (software).

---

## 3. Repository state

### Commits on the branch (oldest first)
| Hash | Summary |
|---|---|
| `b3c42c3` | (pre-existing) Import AetherTwin platform, trained assets, specifications and evidence |
| `c8fd70d` | (pre-existing) Record successful fresh-checkout simulation verification |
| `a31cad0` | Serve the studio gzip-compressed, content-hash cached (immutable `assets/`), `no-cache` HTML, same-origin CSP; remove committed pytest leftovers `tests/.runtime_validation*` and ignore them |
| `50e8c57` | chore: agent workspace setup (CLAUDE.md, `.claude/settings.json`, pre-commit hook, `test-runner` + `diff-reviewer` agents) |
| `3222c84` | Playwright E2E: mock project (fake service + binary WebSocket) and live project (real simulator), axe WCAG 2.1 AA, phone layout; brand-link a11y fix; Vitest limited to `tests/` |
| `af0a6b2` | Split `App.tsx` into `useStudioSession`, `useControlCommands`, `useSpatialRenderer` and `components/studio/*`; deterministic live route test |
| `9ca2c9d` | HANDOFF.md (this file) and a CLAUDE.md pointer to it |
| (next) | Halo: malformed snapshot shows disconnected; serial frames stop when status stalls (IoT finding 1, fixed with tests); HANDOFF update |

No PR has been opened yet. Check with `git log --oneline origin/main..HEAD`.

### Uncommitted work
None at hand-off. `PROGRESS.md` exists only in the cloud container (untracked,
listed in `.git/info/exclude`); recreate it locally (section 9).

### Task list
| # | Task | Status | "Done" means |
|---|---|---|---|
| 1 | Backend serving (gzip, caching, CSP) + hygiene | done | — |
| 2 | Playwright E2E (mock + live + axe) | done | — |
| 3 | App.tsx refactor | done | — |
| 4 | Renderer perf + cinematic effects + camera animation (plan task 3) | **next** | Benchmarked before/after on `/benchmark.html` (1080p, 100k instances) and in the live studio; p50/p95 frame time not worse at equal quality; effects are presentation-only, labelled, off under reduced motion; quality tiers; all suites green; screenshots reviewed |
| 5 | UX: shortcuts, help overlay, quality selector, toast auto-dismiss, map progress (plan task 4) | pending | Keyboard-only usable; shortcuts never fire while typing in inputs; help overlay is an accessible dialog (focus trap, Escape); E2E tests per feature; axe clean |
| 6 | CI/CD (plan task 5) | pending | Workflows below exist, are least-privilege, pass on the PR; Dependabot + CodeQL configured |
| 7 | IoT web server review & hardening (gateway + ESP32 halo) | finding 1 fixed; others pending (section 12). Next planned: unauthenticated `/healthz`+`/readyz` in `service.py`, per-peer login rate limit (429), `AETHERTWIN_*_TOKEN_FILE` support in `auth.py`, HSTS on HTTPS. Note `deployment/gateway/verification.json` pins `gateway.py`'s hash (historical evidence, no test reads it): if you edit `gateway.py`, record a new verification honestly rather than rewriting the old one | Findings triaged; software-verifiable fixes made with tests; hardware-dependent items documented as unverified |
| 8 | Review & roadmap doc, security review, PR (plan task 6) | pending | `docs/REVIEW_AND_ROADMAP.md`; security review notes; PR opened with the final report |

---

## 4. What we know about the codebase

### Layout and data flow
- Python 3.12 platform. `main.py` CLI modes: `sim`, `replay`, `build-twin`,
  `pretrain`, `train`, `evaluate`, `calibrate`, `live`, `hil-test`.
- Pipeline: SDK/simulation/recording → clock alignment + IMU deskew → ICP and
  RGB-D fusion → measured occupancy (tiled voxel hash, exact DDA ray clearing)
  → learned voxel semantics + scene flow (PointNet++/attention) → Kalman +
  Hungarian tracking → risk/geofences/traffic → A* / spline / MPPI planning →
  telemetry. Packages: `core/`, `ingestion/`, `slam_and_twin/`, `perception_ml/`,
  `tracking_and_navigation/`, `visualization/`, `diagnostics/`, `native/`
  (C ray backend, prebuilt `.so`/`.dll`), `hardware_halo/` (ESP32).
- `command_center/`: FastAPI service. `__main__.py` (CLI `python -m command_center`,
  default `127.0.0.1:8080`, refuses non-loopback without TLS + token),
  `runtime.py` (`BrowserRuntime` running the pipeline in a thread),
  `service.py` (`SceneHub` encoder thread with a 1-slot queue that coalesces;
  per-client asyncio queue of 2 that drops to a forced keyframe on overflow;
  routes below), `protocol.py` (binary encoder), `controls.py` (revisioned,
  idempotent command plane), `auth.py`, `commissioning.py` (bounded jobs),
  `assets.py` (GLB tile export with 3 LODs), `gateway.py` (private relay).
- Routes: `POST/DELETE /api/v2/session`, `GET /api/v2/bootstrap[?include_latest=false]`,
  `GET /api/v2/state`, `GET /api/v2/health`, `GET/POST /api/v2/commands`,
  `GET /api/v2/trajectory.csv`, `GET /api/v2/assets/{manifest.json|*.glb}`,
  `WS /ws/v2/scene`, commissioning routes, and a SPA fallback serving `web/dist`.
- `web/`: React 19.3, TypeScript 7.0 (native compiler), Vite 8.3 (rolldown),
  Three.js r186, Vitest 5, Playwright 1.63. After the refactor:
  - `src/App.tsx` (~420 lines) composes everything.
  - `src/hooks/useStudioSession.ts` bootstrap, `SceneStream`, 4 s polling,
    events (cap 64), latencies (cap 100), observation age (250 ms timer),
    control-history reconciliation, `retry`, `refresh`, `expectAcknowledgement`.
  - `src/hooks/useControlCommands.ts` `command(action, payload, revision)`;
    accepted ≠ applied; refreshes 700 ms later.
  - `src/hooks/useSpatialRenderer.ts` lazy `import("../scene/SpatialRenderer")`,
    mounts once, syncs layers/cutaway/selection/pick mode/map manifest.
  - `src/components/studio/` `TopBar`, `SourceSelect`, `LayerDock` (exports
    `layerInfo`), `ViewportHud` (heading, stats, empty, warnings, pick banner,
    footer), `RoutePanel`, `PipelinePanel`, `PlaybackBar`, `Overlays`
    (`EventsDrawer`, `Toast`, `LoginPanel`, `LiveHealthPanel`).
  - Pre-existing: `components/Inspector.tsx`, `ZoneEditor.tsx` (modal with focus
    trap), `CommissioningPanel.tsx`, `HaloMirror.tsx`, `Sparkline.tsx`, `Icon.tsx`;
    `lib/api.ts` (`requestJSON`, `sendCommand`, `SceneStream` with worker,
    WebSocket, reconnect every 3 s and 2 s snapshot fallback), `lib/status.ts`,
    `lib/types.ts`, `lib/geometry.ts`, new `lib/sources.ts`.
  - `src/scene/SpatialRenderer.ts` (~775 lines; single class: WebGLRenderer,
    OrbitControls, layer groups, 100k `InstancedMesh` voxels, overlays rebuilt
    every frame, GLB tiles with LOD, metrics), `protocol.ts` (decoder +
    `SceneAccumulator`), `decode.worker.ts`, `assets.ts`, `picking.ts`.
  - `src/benchmark.ts` + `benchmark.html`: static 100,000-instance render
    fixture, fixed 1920×1080 drawing buffer, pixel ratio 1, exports JSON.

### Telemetry protocol v2 (`docs/TELEMETRY_V2.md`)
Binary packet: magic `0x4e575441` (LE u32), version u16 = 2, JSON header length
u32 at byte 8, JSON header, zero padding to 8 bytes, then 8-byte-aligned typed
sections. Header: `protocol_version`, `kind` (`keyframe`|`delta`), `sequence`,
`base_sequence`, `stream_id`, `scene` (metadata), `sections[{name,dtype,shape,offset,length}]`
(≤32). Dtypes: `<i4` for `voxels.keys`/`voxels.deleted`, `<f8` for `path`,
`<f4` otherwise. Voxel identity = integer key tuple; deltas add/change/delete
by key; a missing base raises `ResyncRequired` and the client sends
`{"type":"resync"}`; the server answers with a keyframe. Limits: 16 MiB packet,
finite values, no duplicate keys, 100k voxels. `path` is sent every frame
(empty when no route). JSON fallback frames come from `/api/v2/state`.

### Auth, roles, CSRF, origin
- Loopback development mode (no tokens configured, loopback host): every
  loopback peer is `operator`; a non-loopback `Host` header gets 401
  (DNS-rebinding defence).
- With `AETHERTWIN_OPERATOR_TOKEN` / `AETHERTWIN_VIEWER_TOKEN` (≥32 chars, must
  differ): `POST /api/v2/session {token}` sets an HMAC-signed `aethertwin_session`
  cookie (HttpOnly, SameSite=Strict, Secure when not local, 1 h TTL; key is
  random per process, so restarts sign everyone out).
- Viewers read; only operators send commands (403 otherwise).
  `capabilities[action]` is true only for operators and supported actions.
- State-changing requests check `Origin` against the own origin plus
  `--allowed-origin` values (Vite dev origins are allowed by default).
- The plain service issues **no CSRF token**; protection is SameSite=Strict +
  Origin check. The gateway issues `csrf_token` in bootstrap; the client sends it
  as `X-CSRF-Token` when present.
- WebSocket: same role/origin checks; close codes 4401/4403/1013/1009/1003;
  client messages ≤1 KiB; uvicorn `ws_max_size=4096`.

### Committed `web/dist` and reproducible builds
`web/dist` (including `.vite/manifest.json`) is committed so a fresh clone runs
the Python service without Node. `npm run build` = `tsc --noEmit && vite build &&
node tools/check-production.mjs` (the last step asserts the benchmark entry is
isolated from the studio and writes `web/reports/production-startup.json`).
The build is byte-reproducible: rebuilding unchanged source leaves `git status`
clean. **Any source change must be committed together with the rebuilt `dist`**,
and CI should enforce this (planned dist-drift check).

### `SOURCE_MANIFEST.json` and the pre-commit hook
`SOURCE_MANIFEST.json` is a SHA-256 inventory of every tracked file
(`tools/repository_manifest.py`); CI runs `--check` and fails on any drift.
After staging: `python tools/repository_manifest.py` then `git add SOURCE_MANIFEST.json`.
`.claude/hooks/pre-commit-check.sh` (PreToolUse on Bash) blocks a command that
**starts with** `git commit` if the manifest is stale or staged web sources fail
`tsc`. On Windows the hook needs Git Bash (`bash` on PATH) — see section 7.

### Evidence-honesty convention (critical)
The project never implies unmeasured results: "Hardware not tested", "Synthetic
data · no hardware claim", "accepted" ≠ "applied", display layers do not change
backend algorithms, voxel glyphs are display cubes, predicted tracks are dashed,
unknown space stays blocked. Simulation numbers are not Pi/A2000/lab claims.
**Visual effects must be presentation-only, must not suggest measured phenomena
(e.g. a scan pulse must not look like the real LiDAR phase), must be labelled
and disabled under `prefers-reduced-motion`.** A laptop RTX result is not an
A2000 result.

### Python style
Dense: semicolon-joined statements, one-line `if`s, compact signatures (see
`command_center/service.py`). Match the surrounding style; do not reformat files.

### Baselines measured in the cloud container
- pytest: **330 passed, 2 skipped** (332 passed after the 2 halo tests) (~100 s, CPU torch, Python 3.12).
- Vitest: **41 passed, 1 skipped** (the optional live-service integration test).
- Playwright: **mock 12 passed** (repeat-each 3: 36/36), **live 4 passed**.
- Build (gzip): three-vendor 635.7 kB (159.4 kB gz), react-vendor 218.8 kB
  (68.3 kB gz), index 67.4 kB (20.9 kB gz), SpatialRenderer 14.0 kB (5.6 kB gz),
  CSS 32.6 kB (8.5 kB gz), decode worker 5.7 kB.
- Headless Chromium + SwiftShader, 1600×900, live simulator: **3–9 fps**. This is
  software rendering and says nothing about GPU performance; the real baseline
  must be taken locally on the RTX GPU (section 7).
- Simulator processing on 2 CPU threads: ~0.9–1.1 s per frame, ~1 fps.

---

## 5. Gotchas (all hit in practice)

- `vite preview`/`vite dev` proxy `/api` and `/ws` to `127.0.0.1:8080`. The mock
  fixture in `web/e2e/support/test.ts` is `{ auto: true }`; when it was not, tests
  that did not destructure `service` silently talked to a real local backend.
- The mock project serves `web/dist`: **run `npm run build` before
  `npm run e2e:mock`** after any source change.
- `@axe-core/playwright` depends on its own `playwright-core`; a mismatch breaks
  types. `web/package.json` has `"overrides": {"@axe-core/playwright":
  {"playwright-core": "$@playwright/test"}}`. Upgrade both together.
- `uv pip install --require-hashes -r requirements-ubuntu.lock` fails without
  `--index-strategy unsafe-best-match` (pip semantics across PyPI + PyTorch
  index). Plain `pip` works as is.
- Headless WebGL needs `--use-angle=swiftshader --enable-unsafe-swiftshader
  --ignore-gpu-blocklist` (set in `playwright.config.ts`). **Locally on the GPU,
  do not use those flags for performance numbers**; run headed Chrome.
- The pre-commit hook originally matched the substring `git commit` anywhere in a
  Bash command (including heredocs) and blocked unrelated commands; it now matches
  only commands that start with `git commit`. Run `git commit` as its own command.
- `pkill -f "<pattern>"` kills your own shell if the pattern appears in the
  command line; start/stop the service from a script or by PID.
- The gateway forwards to the service with `Accept-Encoding: identity`, so the new
  gzip middleware never compresses gateway traffic; the gateway does its own
  header handling.
- CSP (in `command_center/service.py`): `script-src 'self' 'wasm-unsafe-eval'`
  (needed for the meshopt glTF decoder, WebAssembly) and `blob:` in
  `img-src`/`connect-src`/`worker-src` (GLTFLoader embedded images, workers);
  `style-src 'unsafe-inline'` because `benchmark.html` has an inline `<style>`
  and React sets style attributes. CSP is not applied to `/api/*` JSON.
- The live route test: the simulated robot keeps driving and reaches its goal, so
  the UI's pre-filled goal and the path can be empty in a long-running service.
  The test now takes the robot position from `/api/v2/state` and a mirrored goal.
- The live E2E mutates shared state (pause/route), so the `live` project runs with
  one worker.
- `deployment/edge/__pycache__` exists on disk (ignored, not tracked).
- `requirements-windows.lock` pins **CPU** torch `2.14.1+cpu`; CUDA needs a
  separate profile (section 7).
- The Python suite emits a Starlette deprecation warning about `httpx`; harmless.

---

## 6. Remaining plan in detail

Cloud vs local: the cloud container has no GPU, so **anything whose acceptance
depends on real frame times, GPU memory or CUDA belongs locally**. Code, tests,
CI, docs and the security review can be done anywhere.

### 6.1 Task: renderer performance, effects, camera animation (local preferred)
Measure first: run `/benchmark.html` (1080p, 100k instances, export JSON) and the
live studio with the pipeline panel open (fps, frame p95, scene update ms, render
submit ms, draw calls) on the RTX GPU **before any change**; save under
`artifacts/` or `web/reports/` labelled with device, driver, browser, power mode.

Performance (in `src/scene/`):
1. Zero-allocation voxel upload: write translations straight into
   `instanceMatrix.array` (identity rotation/scale; only elements 12–14 change)
   and colours into `instanceColor.array`; mark only the used range for upload
   (`addUpdateRange`) instead of `setMatrixAt`/`setColorAt` per instance.
2. Batched, reusable overlays: preallocate geometry for flow (800 segments),
   risk (20k), traffic, RGB-D (20k), track boxes (256×12 edges) and path;
   update buffers and `setDrawRange` instead of disposing/recreating geometry
   and materials every frame. One `LineSegments` with vertex colours for all
   measured track boxes and one dashed for predicted.
3. Instanced picking: one `InstancedMesh` of unit boxes scaled per track;
   raycast returns `instanceId` → track id.
4. Adaptive DPR: track frame time; scale pixel ratio in steps between ~0.6 and
   `min(devicePixelRatio, 1.5)` with hysteresis.
5. Quality tiers `performance | balanced | cinematic` (+ auto). `performance`
   renders on demand (only when a frame, control change or camera move happens).
6. Pause the loop when `document.hidden` (rAF already throttles) and when the
   canvas is offscreen.
7. Keep `RenderMetrics` accurate; add `data-*` attributes (instances, tiles,
   quality, dpr) on the viewport host for E2E assertions.

Cinematic effects (lazy-loaded chunk, presentation-only, labelled, disabled under
reduced motion and in `performance`):
- Bloom (`EffectComposer` + `UnrealBloomPass` + `OutputPass`, or a cheaper
  selective/mip bloom), exponential fog, a shader grid with distance fade.
- Decorative radial pulse from the sensor pose on voxels and grid
  (`onBeforeCompile` time uniform) — label it decorative, never sync it to or
  imply the real L2 scan phase.
- Animated path dashes (dash offset), pulsing floor rings under tracks, a glow
  for the selected track, red pulse for breached zones.
- Camera: eased fly-to for presets and `focusTrack`; a one-time intro dolly;
  both instant under reduced motion.
- Asset setup for more 3D modelling: register `MeshoptDecoder`
  (`three/addons/libs/meshopt_decoder.module.js`) and optionally `KTX2Loader`
  on the `GLTFLoader` (CSP already allows `wasm-unsafe-eval`); document how to
  compress tiles (gltf-transform `meshopt`/quantize) and how the Python GLB
  exporter (`command_center/assets.py`) could emit `EXT_meshopt_compression`.
Acceptance: after vs before on the same device and settings; p95 frame time and
scene-update ms must not regress at `balanced`; bundle budget respected; E2E
green; screenshots checked; `benchmark.html` still works and the production
startup check passes.

### 6.2 Task: UX (anywhere)
- Shortcuts: `Space` pause/resume, `1`–`9` toggle layers (in `layerInfo` order),
  `C` cutaway, `F` focus selected track, `T`/`I`/`S` camera presets, `R` route
  panel, `E` events, `Esc` close the top-most panel/cancel picking, `?` help.
  Ignore when focus is in `input/select/textarea/[contenteditable]` or a modal is
  open; respect role/capabilities (controls still go through `command()`).
- Help overlay: accessible dialog listing shortcuts (focus trap, Escape, restore
  focus) — reuse the `ZoneEditor` focus-trap pattern or extract a `useFocusTrap` hook.
- Quality selector next to the camera preset (`Auto/Performance/Balanced/Cinematic`),
  persisted in `localStorage` (wrapped in try/catch).
- Toast: non-error notices auto-dismiss after ~5 s (pause on hover/focus);
  errors stay until dismissed.
- Map loading progress: renderer emits tile progress (`loaded/total`, failures)
  → small progress indicator in the viewport.
- Add mock E2E tests for each, rerun axe.

### 6.3 Task: CI/CD (anywhere; must pass on the PR)
Existing: `.github/workflows/ci.yml` — Python matrix (windows-latest, ubuntu-24.04;
manifest check, locked install, pytest) and a browser job (`npm ci`, test, build).
Add, with `permissions: contents: read` by default and job-level elevation only
where needed, plus `concurrency: {group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true}`:
- Browser job: `npm run typecheck`, `npm test`, `npm run build`, then
  **dist drift check** `git diff --exit-code -- web/dist` (and untracked files in
  `web/dist`), bundle budget script (e.g. `web/tools/check-bundle-size.mjs`
  reading `dist/.vite/manifest.json` and gzip sizes; budgets a little above the
  baselines in section 4), `npx playwright install --with-deps chromium`,
  `npm run e2e:mock`, upload `playwright-report/` and `test-results/` on failure.
- Live E2E job (ubuntu): locked Python install (cache pip), start
  `python -m command_center --mode sim --threads 2` in the background (or set
  `AETHERTWIN_PYTHON`), wait for `/api/v2/health`, `npx playwright test --project=live`.
- CodeQL (`github/codeql-action` v3+ with `languages: javascript-typescript, python`,
  `security-events: write` only in that workflow; schedule weekly).
- Dependabot (`.github/dependabot.yml`): `npm` in `/web` (group Playwright +
  axe; group React; group Vite/Vitest), `github-actions` in `/`; weekly. Python
  locks are hash-locked via `uv pip compile` (`tools/lock_dependencies.py`), so
  Dependabot pip updates are not appropriate; document the manual relock.
- Release workflow on `v*` tags: build wheel/sdist (`python -m build`), the web
  `dist`, checksums, GitHub Release (`contents: write` only there). Optionally
  build the gateway image (`deployment/gateway/Dockerfile`) to GHCR with
  `packages: write`, provenance/SBOM.
- Pin third-party actions to versions (ideally SHAs); never use
  `pull_request_target` with checkout of PR code.
- Python lint (optional): `ruff check --select F,E9` only (style rules would
  fight the dense style).
Research current docs for CodeQL, Dependabot grouping, and Playwright CI first.

### 6.4 Task: review & roadmap doc, security review, PR (anywhere)
- `docs/REVIEW_AND_ROADMAP.md`: critique by area (architecture, performance,
  automation, UI/UX, testing, CI/CD, 3D, IoT/gateway, security), what this branch
  changed, a prioritised backlog. Candidate roadmap items: WebGPU renderer
  (`three/webgpu` + TSL) behind a flag; OffscreenCanvas rendering in a worker;
  Gaussian-splat or meshlet LOD for the measured twin; meshopt/KTX2 assets;
  GPU-driven voxel LOD; WebTransport or compressed deltas for telemetry; the
  Pi-to-Windows raw-sensor transport; CUDA lock profile; TensorRT/ONNX export.
  Mark everything unverified that was not measured.
- Security review checklist: CSP (no `unsafe-eval`; is `unsafe-inline` for styles
  still required?), new dependencies (`npm audit`, licences), secrets (none in
  repo, workflows, compose), input handling (bounded JSON, path traversal in asset
  and SPA routes, WebSocket limits), auth/origin, gateway headers, CI permissions.
  Delegate an independent pass to the `diff-reviewer` agent.
- Push, then open **one** PR to `main` titled along the lines of "Studio
  hardening, E2E suites, refactor, renderer and CI improvements"; body = the final
  report format; end the body with the attribution lines in section 9.

### 6.5 IoT web server (gateway + ESP32 halo) — owner asked to cover it
Components: `command_center/gateway.py` (~900 lines, private HTTPS/WebSocket
relay that exposes the studio beyond loopback; adds `csrf_token`; forwards with
`Accept-Encoding: identity`), `deployment/gateway/` (Dockerfile, compose,
locks, README), `hardware_halo/` (ESP32 firmware `aether_halo.ino` +
`halo_protocol.h` over serial, Python `protocol.py`/`controller.py`/`state.py`,
virtual mirror in the studio). An independent review of these was started at
hand-off; its findings are in section 12 (if empty, re-run the review: read those
files and `tests/test_gateway.py`, `tests/test_hardware_halo.py`). Expected work:
make the gateway pass through the service's CSP/Cache-Control, confirm header and
message limits, harden the container (non-root, read-only FS, pinned base,
healthcheck, secrets via files), add `/healthz`-style liveness if missing,
document ESP32 options (OTA, watchdog) as **unverified** until the board is
identified. Anything that needs the ESP32 or Pi is local/hardware work.

---

## 7. Local Windows (Zephyrus M16) setup and GPU work

### Prerequisites
- Windows 11, latest **NVIDIA Studio/Game Ready driver**; check `nvidia-smi`.
- **Python 3.12** (python.org installer, "py launcher" enabled): `py -3.12 --version`.
- **Node.js 22 LTS or newer** (`node -v`); npm 10+.
- **Git for Windows** (includes Git Bash — needed by the pre-commit hook) and
  optionally the GitHub CLI.
- Google Chrome (stable) for headed benchmarking.
- Claude Code with this repo opened at the repo root.

### Clone and install (PowerShell, repo root)
```powershell
git clone https://github.com/NamanSingh69/aethertwin.git
cd aethertwin
git checkout claude/aethertwin-project-review-w5h7gr
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-windows.lock
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe tools\repository_manifest.py --check
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```
Expect about 330 passed (the Windows count historically differed by one test).

```powershell
cd web
npm ci
npm run typecheck
npm test
npm run build
git status --short   # must be empty: the build is reproducible
npx playwright install chromium
npm run e2e:mock
```
Live E2E (second terminal runs the service):
```powershell
# terminal 1, repo root
.\.venv\Scripts\python.exe -m command_center --mode sim --threads 4
# terminal 2, web\
$env:AETHERTWIN_SERVICE_URL = "http://127.0.0.1:8080"; npx playwright test --project=live
Remove-Item Env:AETHERTWIN_SERVICE_URL
```
Line endings: `.gitattributes` exists; if `git status` shows every file modified
after checkout, run `git config core.autocrlf false` and re-checkout, because the
manifest hashes exact bytes.

Claude Code hook on Windows: `.claude/settings.json` runs
`"$CLAUDE_PROJECT_DIR"/.claude/hooks/pre-commit-check.sh`. If hooks error under
PowerShell, make sure Git Bash is the shell Claude Code uses, or temporarily
replace the hook command with `bash .claude/hooks/pre-commit-check.sh`. Do not
commit a Windows-only change to the hook without keeping it working on Linux CI.

### Force Chrome onto the RTX GPU
- **MUX switch** (fastest, most representative): Armoury Crate → GPU Mode →
  **Ultimate** (dGPU only; needs reboot). Use this for benchmarks.
- Otherwise: Windows Settings → System → Display → Graphics → add
  `chrome.exe` → Options → **High performance** (NVIDIA). Also NVIDIA Control
  Panel → Manage 3D settings → Program settings → Chrome → High-performance GPU.
- Verify in `chrome://gpu`: "WebGL2: Hardware accelerated", ANGLE renderer names
  the RTX GPU (D3D11), not "Intel"/"AMD Radeon(TM) Graphics"/"SwiftShader".
  The studio's pipeline panel/`deviceInfo()` also reports the unmasked renderer.

### Thermals and power for repeatable benchmarks
- Plug in the charger. Armoury Crate: Operating Mode **Turbo** (or Performance;
  record which), fans fixed; disable Battery saver; Windows power mode **Best
  performance**.
- Let the machine idle 5 minutes; close other GPU apps; fix the browser window
  to 1920×1080 (or run `/benchmark.html`, whose buffer is fixed at 1920×1080).
- Run each measurement 3× and report median and spread; watch
  `nvidia-smi --query-gpu=clocks.gr,temperature.gpu,power.draw --format=csv -l 1`
  for throttling. Record driver version, Chrome version, GPU mode, power mode.

### CUDA environment and lock profile
Follow `docs/RASPBERRY_PI_WINDOWS.md` "Windows GPU environment":
```powershell
py -3.12 -m venv .venv-gpu
.\.venv-gpu\Scripts\python.exe -m pip install --require-hashes -r requirements-windows.lock
.\.venv-gpu\Scripts\python.exe -m pip install --force-reinstall --no-deps torch==2.14.1 --index-url https://download.pytorch.org/whl/cu126
.\.venv-gpu\Scripts\python.exe -m pip check
.\.venv-gpu\Scripts\python.exe -c "import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0)); x=torch.randn(512,512,device='cuda'); y=x@x; torch.cuda.synchronize(); print('GPU calculation passed')"
```
(Check that `cu126` is still a published index for torch 2.14.1; use the CUDA
build matching the installed driver.) To make it a validated profile, create
`requirements-windows-cuda.lock` with hashes, e.g. via `tools/lock_dependencies.py`
or `uv pip compile requirements.txt -c requirements-constraints.txt
--python-version 3.12 --python-platform x86_64-pc-windows-msvc --torch-backend cu126
--generate-hashes --emit-index-url -o requirements-windows-cuda.lock`; install it
into a clean venv with `--require-hashes`; run pytest and the GPU smoke; record
the result in `reports/` (as a laptop profile, not the A2000 profile).

### Run the studio on CUDA
```powershell
.\.venv-gpu\Scripts\python.exe main.py --mode sim --device cuda --headless --max-frames 30 --output artifacts\zephyrus-cuda-smoke
.\.venv-gpu\Scripts\python.exe -m command_center --mode sim --device cuda --threads 8 --open-browser
```
The headless run writes a benchmark report (processing ms, latency, memory,
route status). Compare with `--device cpu` on the same machine.

### Processing latency vs the 35 ms target
- Read per-stage `module_ms` in the studio's pipeline panel and in the report.
- Things to try, one at a time, measuring each: `torch.inference_mode()` (check
  it is already used), `torch.backends.cudnn.benchmark = True` with fixed input
  sizes, AMP/FP16 on CUDA (`perception_ml` already has an fp16 export path —
  see `tests/test_perception_preprocessing.py`), `torch.compile` on the model,
  pinned-memory host→device copies, reducing neighbour counts/strides in
  `perception` config, moving occupancy/DDA work to the native backend, ONNX
  Runtime/TensorRT export for the A2000. Always check accuracy against
  `artifacts/evaluation.json` after a numeric change.
- 35 ms is a target, not a result; report measured numbers with device and settings.

### Browser benchmark at 1080p
`http://127.0.0.1:8080/benchmark.html` (served by the Python service) → Start →
Export JSON. Save as e.g. `artifacts/browser_benchmark_zephyrus_<gpu>_<date>.json`.
Also measure the live studio: pipeline panel → fps, frame p95, scene update,
render submit, draw calls; 60 s per quality tier.

### Tuning quality-tier defaults
Pick defaults from measured numbers: e.g. `auto` selects `cinematic` only if the
p95 frame time stays under ~16.7 ms at 1080p for 10 s, else `balanced`, else
`performance`; adaptive DPR handles the rest. Record the thresholds and the data
used to choose them in the renderer code comments and the roadmap doc.

### Recording results honestly
Label every number with device (Zephyrus M16, the exact RTX laptop GPU model and
TGP), driver, OS, browser, power mode, settings, dataset (simulation seed), and
date. Never present laptop numbers as A2000 or lab performance; never present
simulation as physical accuracy. Keep `hardware_status` "not_tested" until a
physical rig is verified.

---

## 8. Physical hardware plan (later)

Target deployment: **Raspberry Pi 5** capture node with **Unitree 4D LiDAR L2 +
IMU** and **Orbbec Gemini 335L**; **ESP32** for status/halo; **Windows workstation
with NVIDIA A2000 12 GB, 32 GB RAM** for ML, mapping, tracking, planning and SSD
recording. Jetson and 2D LiDAR are no longer part of the plan (historical files
remain).

Follow, in order: `docs/RASPBERRY_PI_WINDOWS.md` (Pi preparation, L2 and camera
SDKs, wiring/addresses, individual device checks, Windows GPU environment),
`docs/PHYSICAL_COMMISSIONING.md` (scan, then fix the rig), `docs/BOUNDED_COMMISSIONING.md`,
`docs/CALIBRATION_OBSERVABILITY.md`, `docs/AETHERTWIN_ARCHITECTURE.md`,
`deployment/gateway/README.md`, `hardware_halo/README.md`, `docs/PROJECT_STATUS.md`.

Steps:
1. Pi 5: OS on the 32 GB microSD (OS/drivers only), SSD for captures, Ethernet to
   the workstation. Build/verify the L2 SDK and `pyorbbecsdk2` on the Pi; check
   each device alone, then both together (USB power stability, bandwidth).
2. **Missing piece: the Pi-to-Windows raw-sensor transport.** Design a bounded
   streaming protocol (timestamps on a common clock with residuals, calibration
   identity, sequence numbers, explicit drops, disconnect/reconnect, backpressure)
   and a Windows receiver that feeds the existing ingestion/recording paths. The
   existing scene gateway relays processed state and is **not** this transport.
3. Calibration: rigid mount, measure extrinsics with `--mode calibrate` from
   measured 3D-2D correspondences; check observability. Bundled calibration is
   simulation-only.
4. Short stationary and moving recordings (`--record`), replay them on Windows
   (`--mode replay`), then build the measured twin (`--mode build-twin`).
5. ESP32: identify the exact board and wiring **before flashing**; build with
   `hardware_halo/build_firmware.py`; validate outputs, watchdog and stale
   behaviour physically.
6. Live mode on the workstation (`--mode live --twin ... --config ...`), then a
   30-minute hardware run measuring GPU latency, memory, drops and stability
   (compare with `artifacts/stability_30min/`, which is a software run).
7. Private remote viewing through the gateway with TLS and tokens only after
   local operation works; keep raw sensor ports private.

Explicitly unverified today: physical capture, both SDKs on the Pi, calibration,
clock residuals, Ethernet/storage rates, A2000 performance and CUDA lock, real-lab
accuracy (models are trained on synthetic scenes), ESP32 board/wiring and
physical halo output, the raw-sensor transport (does not exist yet).

---

## 9. Constraints to keep

- Branch: only `claude/aethertwin-project-review-w5h7gr`. Never push elsewhere;
  **no force-push, no rebase/amend of pushed commits, no history rewrite**.
  Push with `git push -u origin claude/aethertwin-project-review-w5h7gr`.
- **Do not open extra PRs**: exactly one PR from this branch to `main`.
- Commit message trailer on every commit:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_018AFZ7QXfJeeazNkyyjAabE
  ```
  PR body ends with:
  ```
  🤖 Generated with [Claude Code](https://claude.com/claude-code)

  https://claude.ai/code/session_018AFZ7QXfJeeazNkyyjAabE
  ```
  (If the local session's own system instructions give different attribution
  lines, follow those and keep this format otherwise.)
- **No model identifiers** (model names/IDs) in commit messages, PR titles or
  bodies, code comments or any pushed file, other than the required trailer above.
- Regenerate `SOURCE_MANIFEST.json` for every commit; commit rebuilt `web/dist`
  with any web source change.
- Keep context lean: pipe output through `Select-Object -Last N` / `tail`, use
  `--reporter=line`, read only needed file ranges.
- Maintain `PROGRESS.md` at the repo root as a local, untracked note: add
  `PROGRESS.md` to `.git/info/exclude`; record done/in-progress/next and any
  numbers measured, so nothing is lost after compaction.
- Ask the owner before anything outward-facing or hard to reverse.

---

## 10. Ready-to-paste opening prompt for the local session

```text
You are continuing work on the AetherTwin repo (branch claude/aethertwin-project-review-w5h7gr
of NamanSingh69/aethertwin) on my Windows laptop (ASUS ROG Zephyrus M16, NVIDIA RTX laptop GPU).
A previous cloud session did the first half; you have no memory of it.

1. Read HANDOFF.md and CLAUDE.md in full before doing anything else. They are the source of truth
   for goals, decisions, constraints, gotchas and the remaining plan.
2. Create PROGRESS.md (local, untracked via .git/info/exclude) and keep it updated.
3. Verify the baseline exactly as in HANDOFF.md section 7 and section 11: manifest check, locked
   Python install, pytest, npm ci/typecheck/test/build (git must stay clean), Playwright mock and
   live suites. Report the numbers and any differences from the cloud baselines before changing code.
4. Then continue the remaining plan in order (HANDOFF.md section 6): renderer performance + effects +
   camera animation (measure on the RTX GPU before and after; no frame-time regression), UX, CI/CD,
   IoT gateway/halo hardening, review & roadmap doc, security review, then push and open the single
   PR to main. Plan before each task, research current best practice, verify everything, review
   your own work, commit in logical chunks with the required trailers, never force-push.
5. When finished, report: what changed per task, test and CI status, deferrals/uncertainties, PR link.
```

---

## 11. Baseline verification checklist (run first, locally)

1. `git log --oneline -8` shows the commits in section 3.
2. `python tools/repository_manifest.py --check` → "Verified N tracked files."
3. pytest → ~330 passed, 2 skipped.
4. `web`: `npm ci`, `npm run typecheck` (no output), `npm test` (41 passed, 1 skipped),
   `npm run build` then `git status --short` empty.
5. `npm run e2e:mock` → 12 passed. Live → 4 passed.
6. Open `http://127.0.0.1:8080/` in headed Chrome on the RTX GPU; confirm the twin,
   voxels, tracks and path render; note fps/frame p95 in the pipeline panel.

---

## 12. IoT web server review findings

An independent read-only review finished at hand-off: `tests/test_gateway.py` and
`tests/test_hardware_halo.py` pass (25 tests); `deployment/gateway/verification.json` and
`hardware_halo/firmware_verification.json` hashes match and both say `hardware_verified:false`.
**Its line numbers are unreliable** (files use very dense lines), so locate code by
function name. Finding 1 was confirmed by hand; the rest are unconfirmed leads.

Architecture: browser → gateway (HTTPS/WSS, its own tokens, HMAC cookie) → gateway logs in
upstream with `POST /api/v2/session`, keeps the cookie server-side, opens one upstream WebSocket
(16 MiB max, queue 2, no compression), rebuilds deltas into keyframes (`RelayAccumulator`), feeds a
local `SceneHub` (≤8 browsers), polls bootstrap/health/commands every 2 s, mirrors allowlisted GLBs
into a 2-generation cache; commands go through `ForwardControlPlane` (local role, then upstream).
The gateway parses upstream JSON and emits its own headers (its own CSP, `no-store`), never forwards
upstream `Set-Cookie`, and rejects compressed upstream bodies, so the new gzip/CSP service is
compatible. Halo: runtime → `HaloController.publish` → 30 Hz status thread (`_run`, computes
`halo_state`) → transport thread sends 16-byte CRC16 frames over USB-UART after a HELLO/IDENTITY
handshake → ESP32 (250 ms link watchdog, WS2812 via RMT, OLED). **The ESP32 has no network
server** (no WiFi/BLE/HTTP/OTA code).

Findings (severity | location | problem | fix | effort):
1. **FIXED** (tests `test_malformed_snapshot_*`, `test_stalled_status_worker_*`) — High | `hardware_halo/controller.py` `HaloController._run` | no try/except: a malformed snapshot (e.g. `processing_ms=None`) kills the status thread while the transport keeps sending the frozen state with fresh sequence numbers, so the firmware watchdog never trips and the ring can show a stale "clear" | catch errors and publish a watchdog/disconnected state; transport stops sending if `_ticks` has not advanced for >2 periods; add a test | S
2. High | `controller.py` transport + `aether_halo.ino` | ESP32 reset with the port still open → device unpaired and silently dropping frames while the host reports connected; no reconnect after handshake/write failure | reconnect loop with backoff, periodic re-HELLO or device heartbeat/ack | M, **needs hardware**
3. Medium | `aether_halo.ino` parser | no inter-byte timeout and no rescan for the `0xA7` start byte after a CRC failure | reset after >5 ms gap; shift to next `0xA7`; Python byte-stream mirror + fuzz test | S
4. Medium | `gateway.py` asset sync | failed sync retried every 2 s poll (up to 64 MiB each) | per-revision exponential backoff | S
5. Medium | `deployment/gateway/compose.yaml` | tokens in environment variables (visible via `docker inspect`) | Compose `secrets:` + `AETHERTWIN_*_FILE` support | S
6. Medium | `gateway.py` uvicorn launch | no explicit TLS policy or HSTS | TLS ≥1.2, modern ciphers, `Strict-Transport-Security` | S
7. Medium | `compose.yaml` healthcheck | TCP-only; upstream health only behind auth | unauthenticated `/healthz` (alive) and `/readyz` (upstream connected, not stale; status code only) | S
8. Medium | gateway upstream reader | silent-but-open upstream socket only marked stale | resync then reconnect after N s; explicit `ping_interval`/`ping_timeout` | S
9. Medium | `service.py` login, gateway logging | no login rate limit; no audit log (`access_log=False`) | per-IP token bucket on `/api/v2/session`; structured, token-redacted logs of auth failures and commands | M
10. Medium | tests | gaps: gateway headers, compressed-upstream rejection, asset retry, silent upstream; halo status-thread crash, re-pair, unplug, byte framing | add tests | M
11. Low | `service.py` WebSocket, `auth.py` | session checked only at upgrade, so streams outlive cookie expiry; logout cannot revoke | close at expiry; nonce denylist | S
12. Low | gateway `_authenticate` | clears the shared cookie jar during in-flight requests | single-flight with `asyncio.Lock` | S
13. Low | firmware loop | no loop watchdog, no `Wire` timeout | `enableLoopWDT()`, `Wire.setTimeOut(5)` | S, **needs hardware**
14. Low | controller | device error lines never read after handshake | bounded line buffer, expose `last_device_error` | S
15. Low | `deployment/gateway/Dockerfile` | base pinned by tag not digest; no `HEALTHCHECK`; pip in runtime image | digest pin, multi-stage, `HEALTHCHECK` | S
16. Low | `compose.yaml` | no memory/pids/CPU limits, no log rotation | add limits and `logging.max-size` | S
17. Low | `compose.yaml` | `--ca-file` hard-coded; `--allowed-origin` not configurable | env-driven | S
18. Low | gateway/controller counters | some shared flags updated without the lock; `_serial.close()` in an except can raise and end the transport thread | take the lock; guard the close | S

Suggested IoT pass ([HW] = needs hardware to verify): `/healthz` + `/readyz`; optional Prometheus
`/metrics` (loopback or token); login rate limiting + audit log; Compose secrets, digest pins, limits,
HSTS/TLS policy, certificate-reload procedure; upstream reconnect with explicit ping; opt-in mDNS;
halo reconnect/heartbeat [HW]; firmware framing fixes (simulate in Python, confirm [HW]); loop
watchdog and I2C timeout [HW]. ESP32 OTA or WiFi/BLE endpoints are a large new attack surface
(signed images, secure boot, flash encryption) and contradict the current USB-serial design —
recommend not adding them without a strong need. Keep `hardware_verified:false` until verified on a
real ESP32 and LED ring.

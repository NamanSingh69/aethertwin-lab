# AetherTwin browser command center

Local/self-hosted React, TypeScript, Vite and Three.js interface. No external
Site registration, remote fonts, publication, or robot actuation is involved.

From this folder:

```text
npm ci
npm test
npm run build
```

The Python command-center service serves `web/dist` at its configured local
address (normally `http://127.0.0.1:8080`). For frontend development, keep that
service running and use `npm run dev`; Vite prints `http://127.0.0.1:5173` and
proxies `/api` and `/ws` to port 8080. A port conflict fails visibly.

To run the optional read-only integration test against a running service,
set `AETHERTWIN_SERVICE_URL=http://127.0.0.1:8080` in the current shell, then run
`npm test -- tests/service.integration.test.ts`. Ordinary tests skip this test
when the service URL is absent. It reads a real binary frame and map manifest;
it does not pause processing or change the source.

## Browser end-to-end tests

Playwright runs two projects (`playwright.config.ts`):

- `mock` serves the production build with `vite preview` and answers every
  `/api/v2/*` request and the binary `/ws/v2/scene` stream from an in-test fake
  service (`e2e/support/mockService.ts`). It covers streaming, layers, controls,
  roles, sign-in, the zone dialog, axe WCAG 2.1 AA checks and phone layout.
  Run `npm run build` first, then `npm run e2e:mock`.
- `live` drives the real Python service in simulation mode: CSP, compression and
  caching headers, map tiles, pause/step/resume and route planning with CSV export.
  Set `AETHERTWIN_SERVICE_URL=http://127.0.0.1:8080` for a running service, or
  `AETHERTWIN_PYTHON=../.venv/bin/python` to let Playwright start one.

Headless runs use SwiftShader WebGL; they check behaviour, not GPU frame rates.

## Data and controls

- Worker decoding follows `../docs/TELEMETRY_V2.md`, validates the 16 MiB packet
  boundary, typed sections, alignment, uniqueness, finite geometry and sequence
  continuity. A gap requests a keyframe. The fetch fallback retains source data.
- The renderer runs outside React state. React receives metadata and measured
  statistics; large typed arrays go directly from the worker to the renderer.
- Voxel capacity is 100,000 instances. Display bounds are 800 flow segments,
  20,000 risk samples, 20,000 RGB-D points, 256 tracks, 64 zones, and 128 map
  tiles with two concurrent LOD requests. These are presentation limits.
- Voxel glyphs are 0.085 m display cubes, not authoritative occupied-cell
  volumes. Risk hotspots use relative risk >0.15. Flow vectors retain their
  reported world displacement without an invented velocity scale.
- Superseded GPU buffers/materials/textures are disposed. Worker queues coalesce
  frames; a missing delta base triggers resynchronization instead of guessing.
- Map bounds support both the exporter's flat six-value form and nested min/max
  corners. LOD distances are minimum switch thresholds. GLB stays world Z up.
- Controls require an operator role and an explicitly enabled capability.
  Commands carry map/control revisions. A queued acknowledgement is not an
  applied action. Geofence edits submit together against their starting revision.
- Route selection is a request to the backend planner. Unknown/occupied space
  remains blocked by the backend; CSV is available only for a reported safe path.
  Clicking a start/goal requires an explicit world-Z pick plane or an existing
  target Z. Live mode never auto-fills simulated endpoints.
- Commission opens bounded server jobs against registered source/twin/target IDs.
  Discovery reads SDK/resource availability without opening devices. Diagnostics,
  candidate calibration, static reconstruction, coverage and fixed-position
  registration expose individual check outcomes and downloadable evidence.
  Completion does not imply a passing check. Candidates never activate maps,
  calibration or poses. Live jobs remain unavailable without exclusive rig
  ownership; pausing acquisition alone is insufficient. Viewer sessions inspect
  evidence; only operators start/cancel jobs. No browser-supplied paths are sent.
- Zone distance is the signed distance from the selected entity **center** to
  the oriented zone boundary, not object-body clearance. Negative means inside.
- Descriptors show all ten reported measurements; missing uncertainty/shapes
  remain unavailable. No network explanation or hardware accuracy is implied.
- Predicted tracks use dashed, dimmer boxes. Inspector measurement age is in the
  source clock; the global observation-age warning remains separate and active.
- Replay seek, step and source choices are disabled when unsupported. The
  Commission workspace shows available reference/calibration evidence; it does
  not invent a completed physical commissioning procedure.
- The virtual halo consumes actual `scene.halo`/`bootstrap.halo` metadata.
  Its 24 pixels retain their uint8 RGB values. Rig-forward/index 0 appears up;
  rig-left/index 6 appears left. Physical verification remains explicit, and
  missing data is not converted to an invented clear state.

Layer visibility changes presentation only. The left dock intentionally contains
no 2D LiDAR control. Measured geometry remains the navigation authority; cutaway
clipping affects display only. Reduced-motion preference disables camera damping.

Actual browser rendering rate, source age and processing rate are separate UI
measurements. The 100,000-instance unit fixture checks decoder capacity, not a
60 FPS browser guarantee. Interactive and renderer performance acceptance must
use the running browser with actual source data.

`/benchmark.html` is a separate, explicitly labeled static 100,000-instance
render fixture. Its drawing buffer is fixed at 1920×1080 with pixel ratio 1 even
when CSS scales the canvas into a smaller embedded browser. It measures actual
rates/interval percentiles, reports hidden-tab state, and exports JSON. It does
not benchmark live perception or modify the selected backend source.

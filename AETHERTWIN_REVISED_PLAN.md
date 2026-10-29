# AetherTwin addendum — revised implementation plan

Status: **superseded by the user-approved AETHERTWIN_IMPLEMENTATION_PLAN.md on 2026-10-28; implementation is active**. The following preserves the earlier review-stage plan for provenance.

User decisions, 2026-10-28:

- Review and revise the addendum before implementing it.
- No physical sensors are connected.
- Report measured performance; retain 35 ms as a future optimization target.

The original `SPEC.md` and platform remain intact. The supplied addendum is
preserved verbatim in `PEAK_HIL_AND_UI_SPEC.md`, and its original blueprint is in
`references/AetherTwin_addendum_original.txt`. `reports/ADDENDUM_REVIEW.md`
records the technical corrections.

## 1. Scope and boundaries

Extend the existing platform with three capabilities: a diagnostic/self-test
runner, an optional telemetry halo, and a browser command center. Retain the
native Open3D/OpenCV studio, all perception/tracking/planning algorithms, the
eight existing CLI modes, and observation-only navigation authority.

Windows remains the simulation, replay, training and visualization platform;
Ubuntu 24.04 remains the primary live-sensor target. Keep Python 3.12 and the
existing reproducible dependencies. Add new Python/browser dependencies with
explicit platform locks when implementation is approved.

No sensor absence may silently turn live acquisition into simulated data.
Physical-device tests remain **not tested** until devices are connected and
measurements are collected. Robot actuation, firmware flashing, persistent host
network/firewall changes and external deployment remain outside automatic work.
Generating firmware source and documenting a reviewed setup are separate tasks.

## 2. Hardware and rig corrections

| Item | Revised requirement |
|---|---|
| Primary sensor | Unitree L2 through its official SDK; report actual model/serial/firmware, packet and cloud rates. Do not display an invented 20 Hz rate. |
| L2 power | Use the manufacturer-supplied or correctly specified adapter and connector for the actual unit. The cited manual specifies 12 V DC; account for startup/heating demands when sizing a rig supply. |
| RGB-D | Gemini **335L** remains the verified integration target. Other models require an explicit supported-device check. Default source depth is 1280×800 at 30 fps; enumerate actual color/depth/alignment combinations. |
| Camera USB | A compatible USB 3 data connection and adequate power are required. Orbbec specifies a host supply capability of 5 V/1.5 A or higher for the cited configuration; do not assume 900 mA is sufficient. |
| Optional S2 | Identify the exact model, supported SDK transport, adapter and supply before connection. Serial pathnames alone do not identify the device. |
| Optional halo | Choose an explicit MCU/LED/OLED board profile. Budget the actual LED and board current separately from the camera; verify pin assignments and signal levels from the chosen parts. |
| Mechanical tower | Preserve the three-tier visual concept, but design from manufacturer CAD, measured mounting geometry and clear optical fields of view. Treat example offsets as CAD priors, not measured calibration. |

Sources: [Unitree L2 manual](https://oss-global-cdn.unitree.com/static/Unitree%204D%20LiDAR%20L2%20User%20Manual.pdf),
[Gemini 335L specifications](https://www.orbbec.com/products/stereo-vision-camera/gemini-335l/),
[Orbbec power-delivery guidance](https://doc.orbbec.com/documentation/Orbbec%20Gemini%20330%20Series%20Documentation/Reference%20Design%20-%20Power%20Delivery%20%28Gemini%20330%20Series%29).
Profile alignment must follow the [official SDK alignment APIs](https://orbbec.github.io/pyorbbecsdk/source/5_Application_Guide/ApplicationGuide.html).

A future host-setup step must identify the actual network interface and required
device permissions, then produce reviewable commands. Do not run the pasted
subnet-wide firewall rules or world-writable USB rule as a generic prerequisite.
Ubuntu under WSL is software-test evidence; it is not evidence of USB/Ethernet
passthrough or successful physical capture.

## 3. Phase A — shared diagnostic and control contracts

Define immutable diagnostic snapshots before developing the extensions:

- Source: `sim`, `replay`, `live`, or explicitly marked test injection.
- Sensor state: discovered, identified, streaming, degraded, unavailable or
  permission required; include model, serial, transport, selected profiles,
  timestamps, coverage, clock residuals and dropped-frame counters when known.
- Per-stage execution time and real input/output array/tensor shapes. Record
  sample counts and configuration, rather than copying example dimensions.
- Test state: passed, failed or not tested, with evidence and the data source.
- Calibration identity, measured/estimated provenance, observability,
  uncertainty and active/candidate version.
- UI commands with revision, validation result and acknowledgement, applied
  through a bounded queue at a pipeline boundary.

Keep simulation labels and virtual test targets outside ordinary runtime sensor
inputs. A selected source/mode must be visible in every report and interface.

## 4. Phase B — diagnostic runner and calibration

Add `diagnostics/autonomous_hil_runner.py` and the `hil-test` CLI mode. Provide
explicit source selection; with the current hardware situation, the default
approved workflow is software verification using simulation or recording replay.

| Stage | Revised behavior and evidence |
|---|---|
| 1. Discovery | Enumerate available SDK devices and configured network/serial targets. Validate device identity and protocol responses. A USB vendor ID, open port or arbitrary UDP packet is insufficient. Do not scan an entire subnet by default. |
| 2. IMU audit | Collect a measured time interval, report actual rate, sample count, gaps, gyro bias, gravity norm and stationarity. Reject moving/insufficient data; do not assume exactly 400 samples or a fixed IMU rate. |
| 3. Calibration | Fit observed planes with uncertainty, refine only observable components, and validate against independent observations. Preserve the existing full-correspondence calibration path. |
| 4. Mapping repeatability | Construct a static reference from designated commissioning data and score separate observations. Report residual/repeatability statistics and latency percentiles. Call a metric a false-positive rate only when independent labels support that interpretation. |
| 5. Closed-loop verification | Inject a clearly marked virtual target into a separate verification stream/map copy. Test tracking velocity, rotated-box breaches, dynamic risk, path collision checks and safe no-path behavior. Preserve live observations and calibration. |
| 6. Report | Write JSON and Markdown with every stage's status, source, sample counts, settings, measurements, limits and evidence hashes. Preserve failed and incomplete stages. |

### Calibration observability

A single plane supplies a normal direction and an offset: it constrains two
relative tilt angles and translation along the plane normal. Yaw and the two
translations within the plane remain unobservable. This follows directly from
the plane constraint and must be reflected in rank/covariance reporting.

Floor alignment may refine a valid measured prior. It must not promote an
arbitrary CAD transform to a fully measured six-degree-of-freedom calibration.
Use nonparallel observed geometry or measured 3D/2D target correspondences for
full calibration, with degeneracy, overlap and hold-out residual checks. Compute
normal-direction translation in the actual coordinate frame; a bare Z-offset
formula is valid only under its stated alignment assumptions.

Write versioned candidate calibrations first. An explicitly configured automatic
promotion policy may activate a candidate only after its observability and
validation gates pass, with an atomic update and retained previous version.
Otherwise leave the active calibration intact and explain what is missing.

### Report semantics

Use separate fields such as `software_checks_passed`, `hardware_status`,
`hardware_checks_passed`, `calibration_status` and `performance_measurements`.
With no hardware, a simulated test may pass while hardware remains `not_tested`;
it must not produce an overall successful physical HIL claim. Optional devices
are skipped unless explicitly required by the selected test configuration.

Always provide an integrity digest. Add a cryptographic signature only when a
signing key and trust model have been configured; a checksum is not a signature
or independent certification.

## 5. Phase C — binary telemetry and local web service

Add a local FastAPI/uvicorn service, retaining the existing JSON telemetry as a
compatibility interface. Serve the command center on localhost:8080 by default,
with explicit port selection and visible startup failures.

Define a versioned binary protocol with magic/version, sequence number, capture
and processing timestamps, coordinate frame, units, calibration identity and a
section directory containing dtype, shape, offset and length. Contiguous typed
sections carry voxel positions/colors/flow, risk, RGB-D inlay and trajectories.
Small control/health metadata may use a structured header.

Validate packet sizes and shapes; document endianness and alignment. Use bounded
producer/client queues, coalesce obsolete frames, disconnect persistently slow
clients, and expose drop counters. Rendering must not block inference.

The browser may create typed-array views over the received buffer. Instance
transforms and GPU uploads still consume work and may copy data. Describe this
as efficient binary streaming, not unproven end-to-end zero-copy.

Support validated pause/resume, start/goal requests and versioned geofence edits.
Keep geofence monitoring distinct from a configured navigation keep-out policy.
No browser command actuates a physical robot or changes sensor firmware.

## 6. Phase D — AetherTwin spatial interface

Implement the proposed React/Three.js interface with a locked browser build:

- Four-zone layout: source/health header, sensor/layer dock, spatial viewport,
  entity/traffic inspector and collapsible pipeline graph.
- Preserve the supplied dark palette, typography, restrained glow and glass
  treatment, while keeping numbers, warnings and selected states readable.
- Render instanced semantic voxels, reconstructed geometry, flow, tracks,
  geofences, relative risk, RGB-D and safe trajectories. Keep layer visibility
  separate from whether the backend algorithm executes.
- Bind graph badges and tensor shapes to real measurements. Display unavailable
  values honestly. Clicking a node highlights the corresponding output layer.
- Provide all ten descriptor values in the entity inspector. A six-axis radar
  may be a labelled subset; it must not be called a complete 10D chart or proof
  of why the neural network made its decision.
- Show velocity, uncertainty and a precisely defined distance/clearance to the
  nearest oriented zone, with tests for rotated and intersecting geometry.
- Provide camera presets, entity picking, pause/step controls where supported,
  path requests/export and transactional OBB creation/translation/rotation/resize.
- Show source age, processing rate, sensor rates, rendering rate and stale data
  separately. Smooth presentation must not fabricate fresh sensor observations.

Retain the native Open3D studio. Browser and native inspection are separate
acceptance tasks; a successful generated render is not a completed interactive
desktop check.

## 7. Phase E — optional physical telemetry halo

Start with a virtual 24-pixel mirror driven by the same state machine planned for
hardware: clear/locked, nearest-person direction, moving equipment and critical
breach/TTC. Transform tracked world positions into the sensor/rig frame before
computing azimuth; explicitly configure LED-zero direction and clockwise order.

Specify the 16-byte packet before writing either endpoint: synchronization,
protocol version, sequence, state/payload fields, byte order, scaling and CRC.
Include a receiver watchdog and a documented disconnected/stale state. Keep the
30 Hz output loop separate from the slower perception loop, using the latest
snapshot and its age. Do not label repeated status packets as new sensor frames.

Identify the intended controller through an explicit compatible handshake and
configured port. Opening an arbitrary serial device must not send halo commands
to an S2 or unrelated board. Provide MCU/OLED/LED configuration, firmware source
and compilation checks. Flashing and physical output validation occur only in
the later connected-hardware phase.

## 8. Verification and release gates

| Gate | Required result |
|---|---|
| Existing platform regression | Original mathematics, perception, tracking, planning, replay and native-viewer checks continue to pass. |
| Software diagnostic mode | Deterministic sim/replay tests pass; absent hardware is clearly not tested. Failure injection produces truthful reports and non-success hardware status. |
| Calibration | Observable/unobservable dimensions are tested; candidates cannot overwrite a working full calibration after a degenerate floor-only fit. |
| Binary protocol | Round trips, malformed packets, missing sections, sequence/drop handling, bounded memory, reconnect and slow clients are tested. |
| Browser behavior | Source labels, real graph metrics, picking, layers, geofence edits, pause/resume, stale states and path export work end to end. |
| Visual performance | Measure renderer timing on this laptop with actual data and separately labelled stress fixtures, including a 100,000-instance scene. Treat 60 FPS as a target requiring measurement, not a constant badge. |
| Backend performance | Report p50/p95/p99 latency, acquisition/processing/presentation rates, memory, queues, drops and all sampling settings. **35 ms is a future target, not a completion gate.** |
| Halo software | Packet/CRC/sequence/watchdog and virtual mirror tests pass. Hardware delivery remains not tested until the intended controller is connected. |
| Physical HIL | After connection: identify devices, validate supported profiles and real packets, collect calibration/coverage evidence, then execute the applicable hardware stages. |
| Packaging | Deliver source, locks, protocol/control contracts, reports, setup and recovery instructions, virtual example and firmware source. No external publication is required. |

## 9. Proposed execution order after implementation approval

1. Complete and preserve the original release's outstanding verification work.
2. Add diagnostic/control contracts and truthful sim/replay diagnostic reports.
3. Add the binary codec and bounded localhost service.
4. Build the web interface against real simulator/replay snapshots.
5. Add interactive geofence controls and the virtual halo/protocol tests.
6. Add firmware source/build checks and actual-device discovery adapters.
7. Run software/browser/performance verification and package the extension.
8. Perform physical commissioning/HIL when the user has connected the rig and
   completed any reviewed host-permission setup.

No HIL, web-command-center or halo implementation is authorized by this review
alone. The next decision is whether to implement this revised plan in the same
project; no additional purchase or hardware connection is required to review it.

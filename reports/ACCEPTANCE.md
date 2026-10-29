# AetherTwin 2 acceptance

The integrated software release is verified in simulation and replay. Physical
Jetson, LiDAR, camera, ESP32, calibration and real-lab accuracy remain unverified.
release_validation.json is the current machine-readable index. Earlier reports
retain their original source/checkpoint hashes as historical evidence.

| Gate | Current evidence |
|---|---|
| Windows | 323 passed, no skips; dense observation integration enabled; windows-aethertwin.xml |
| Ubuntu 24.04 WSL2 | 322 passed, one Windows-only DLL skip; dense integration enabled; ubuntu-aethertwin-release.xml |
| Browser | 41 passed, one optional service-test skip; actual service flows independently checked; ../web/reports/vitest-release.json |
| Fresh Windows install | Dependency hashes and pip check, isolated imports, final wheel, simulation/replay; windows_installed_release.json |
| Ubuntu installed wheel | Actual simulation/replay/diagnostics/browser, four entrypoints and final static-file integrity; ubuntu_aethertwin.json |
| CLI | Simulation, raw record/replay, reconstruction, Point-MAE, training, evaluation, calibration and HIL; live failures never substitute simulation; Windows/Ubuntu workflow reports |
| Math/mapping | Stable SE(3), carried pointwise deskew, native/reference exact rays, collision-safe hashing, moving commissioning, map paging and unknown-space navigation |
| Calibration | Actual six-DoF rank/conditioning, planar PnP, frame-correct covariance, atomic candidate/history; calibration_observability_verification.json |
| Learned outputs | Canonical 24-epoch checkpoint, selected epoch 18; held-out mIoU 0.9851 vs 0.0971 majority baseline; dynamic flow EPE 0.02193m vs 0.03762m zero baseline; canonical_checkpoint.json |
| Tracking/planning | CA/CV, crossings/occlusion/timing, uncertainty, SAT/GIoU, TTC, traffic, ground/3D A*, spline/MPPI, swept checks, empty-frame hold and no-path |
| Private relay | Actual pipeline through verified TLS, exact arrays, applied controls, CSV, outage recording/recovery; ../artifacts/gateway_actual_pipeline/report.json |
| Commissioning | Six isolated job kinds, active reference unchanged, ambiguity rejection, clean shutdown; ../artifacts/commissioning_actual_jobs_final/report.json |
| ESP32 | Virtual halo/protocol/watchdog, C++/Python parity, official firmware compile; ../hardware_halo/firmware_verification.json; physical output pending |
| Classic desktop | Computer-use checks verified pause/resume, layers, goals, CSV, alerts and shutdown |
| Browser interaction | Layers, stale states, track/descriptors/uncertainty, module shapes, geofences, routes/no-path, CSV, seek/step, discovery, switching/reconnect; interactive_browser.json |
| Rendering | Color-corrected 100,000-instance 1080p fixture: 59.46 FPS over 60s, three draw calls; ../artifacts/browser_stress_1080p_100k.json |
| Endurance | 741 frames/1805.5s, no standby, zero recording drops, bounded memory and normal server exit; BENCHMARK.md |

Synthetic scores do not establish physical accuracy. Overall zero-flow EPE can
be better on a mostly static dataset; the dynamic comparison is separate. Small
smoke-training runs verify execution and never replace the canonical checkpoint.

The endurance backend stayed unchanged. Frontend availability/material fixes were
rebuilt during that run; the final renderer has a separate measured 60-second
test. Reports preserve this distinction and the earlier black-material image.
The 35ms processing target remains future work.

Current rig profiles exclude S2 acquisition/simulation and its SDK build; legacy
recordings remain readable. Measured occupancy governs navigation, unknown space
is blocked, and inferred visual closure has no free-space authority.

Remaining physical gates are in ../docs/PHYSICAL_COMMISSIONING.md. The ARM64
recipe needs a matching NVIDIA runtime and an Open3D wheel built for the actual
board. No ARM64 container execution, TensorRT GPU parity, firmware flashing,
physical actuation or external deployment is claimed. Jobs export candidates.

<details><summary>Historical acceptance report from the earlier source revision</summary>

## Earlier acceptance evidence

This release is a simulated research demonstration. Runtime perception uses
sensor observations; simulator labels are confined to training and scoring.
Physical devices, real calibration and real-lab accuracy remain unverified.

Windows: **180 passed**, zero failures or skips, in a fresh hash-locked Python
3.12 environment. Ubuntu 24.04 under WSL2: **179 passed, one Windows-only skip**.
The latest paused-HUD event regression also passes in the focused 23-test viewer
suite; follow-up counts overlap the full suite and are not additive.
The dense observation-only reconstruction/navigation integration was enabled on
both platforms. XML, logs and environment inventories are retained beside this
file. Static undefined-name checks also pass.

| Area | Implementation and evidence |
|---|---|
| Shared contracts and mathematics | Immutable sensor/snapshot boundaries; SI units, timestamps, calibration identity; analytic SE(3), near-zero/near-pi, projection and pointwise deskew regressions |
| Simulated sensing | MuJoCo dynamics and CPU ray casting; seeded scenes, RGB-D, IMU, LiDAR echoes and optional S2; original geometry excluded from the perception pipeline |
| Localization/reconstruction | FPFH/RANSAC, robust point-to-plane ICP and pose graph, measured TSDF/Poisson/floor alignment; inferred watertight visual mesh separately labelled |
| Occupancy/navigation authority | Exact DDA, tuple-safe hashing, free/solid/permeable evidence, decay and persistence; unknown space blocked |
| Neural perception | Packaged trained Point-MAE and seven-class/flow checkpoint; held-out synthetic scores in `../artifacts/evaluation.json` |
| Annotated-data training | Restartable recorded datasets, strict label sidecars and split checks, full checkpoint fine-tuning; synthetic example and scored I/O smoke run included |
| Tracking and analytics | CA/CV filters, gated Hungarian assignment, 3D GIoU/SAT, relative-motion TTC, directional risk, persistent traffic |
| Trajectories | Ground and volumetric A*, constrained splines/MPPI, swept checks, explicit no-path/hold behavior, CSV/JSON export |
| Replay | Versioned bounded NPZ chunks and matching twin; observation-only replay equivalence tested |
| Ubuntu | Ubuntu 24.04 under WSL2: non-live CLI modes and native SDK bindings compiled/imported; see `ubuntu_verification.json` |
| Windows rendering | Verified process-local software renderer; own generated geometry renders with normal teardown; SDK acquisition remains unverified |
| Presentation and telemetry | Open3D scene, OpenCV HUD, cutaway/layers/tracks/goals/export, bounded loopback WebSocket messages |
| Long-run behavior | Uninterrupted 30-minute full viewer run, 1,069 frames, clean exit, bounded memory and no dropped presentation snapshots; see `BENCHMARK.md` |

## Remaining acceptance gates

Interactive desktop inspection through the user-invoked computer-use skill is
pending Python application permission. Automated rendering and clean shutdown
do not establish that clicking controls, changing trajectories and inspecting
alerts worked through the desktop. The user elected to enable that access later.

The earlier `viewer_endurance_30min` run exited normally but included a Windows
Modern Standby/lid interruption. Its maximum latency includes that interruption;
it is not an uninterrupted responsiveness result. A fresh run uses a temporary
process power request and records unbiased active time separately from wall time.

Physical acquisition and field performance require connected L2/Gemini hardware,
measured calibration, lab recordings and independently annotated evaluation.
Those gates are outside the first simulated milestone and are not marked passed.

</details>

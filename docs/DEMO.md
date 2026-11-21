# Demo runbook (class submission, 2026-11-06 15:00)

Pick the highest tier that works on the day, say which one it is out loud, and keep its label on screen. Every tier
runs the real browser studio (`command_center`); they differ in where the sensor data comes from.

| Tier | When | Data | Say |
|---|---|---|---|
| **A. Live rig** | Step 8 of `docs/LAB_DAY.md` worked (Pi, L2, camera, commissioned twin) | the sensors over Wi-Fi | "live" |
| **B. Recorded-data rehearsal** | the rig or Wi-Fi fails on the day | a real bench recording replayed over the real transport on loopback | "recorded real data, not live" |
| **C. Simulation** | no twin and no recording usable | simulated sensors | "simulation" |

## Before you start (5 minutes)

- Windows power mode *Best performance*, and NVIDIA *Prefer maximum performance* for `python.exe` (the GPU clocks down
  between frames otherwise; `docs/LAB_DAY.md` step 8).
- Laptop on mains, sleep off, other rehearsals and services stopped (one rehearsal per machine; never stop processes by
  command-line pattern, stop them by PID or Ctrl+C in their own window).
- Use Chrome or Edge with hardware acceleration on (`chrome://gpu`: "Hardware accelerated"). The studio shows its own
  render rate next to the frame id; the Claude app's built-in browser pane renders in software (about 1 fps) and is
  not for the demo.
- `python tools/lab_preflight.py` should print no FAIL.

## Tier A: live rig

Run the step 8 command from `docs/LAB_DAY.md` (with `--parked`, from `.venv-gpu`), add `--open-browser`, or open
http://127.0.0.1:8080/ yourself. If the monitor calibration failed, run it with `--lidar-only` and the LiDAR-only twin
(LAB_DAY step 5 fallback): the studio then reports `calibration_measured: false`; call it LiDAR-only.

## Tier B: recorded real data (one command, ~1 minute to start)

```powershell
.venv\Scripts\python.exe tools\rehearse_live.py --recording $env:USERPROFILE\aethertwin_rec\wifi_stationary_60s --twin $env:USERPROFILE\aethertwin_demo\bench_twin_lidar_only --duration 1200 --open-browser
```

It prints `Studio: http://127.0.0.1:<port>/` and opens it once the service is up; the measured map finishes loading
after about 20 s. The twin in `%USERPROFILE%\aethertwin_demo` is a LiDAR-only bench twin (assumed 0.75 m LiDAR height, not
a commissioned twin of the lab; see its `README.txt`). If the lab commissioning on the day produced a twin and a
recording, use those instead (`--twin <step 6 twin> --recording <step 6 recording>`, plus `--calibration <json>` if it
was calibrated). The 60 s recording loops, and it has no people walking, so tracks and risk stay empty.

## Tier C: simulation

```powershell
.venv\Scripts\python.exe -m command_center --mode sim --open-browser
```

## What to show (5 to 7 minutes)

1. **"What is this?"** in the studio header (`/explainer.html`): the pipeline from sensors to twin, with sources (1 min).
2. **Explore**: the measured twin with the live point cloud on top; orbit and zoom; the **Sensors** dock (LiDAR,
   depth camera, virtual halo); turn **Layers** on and off (measured twin, semantic voxels, tracked entities, safe
   trajectory, scene flow, protected zones, risk hotspots).
3. **Tier A only:** one person walks slowly in front of the rig: tracked entity, risk hotspot, the virtual halo
   turning from clear to a warning (physical LEDs are not fitted; the halo is virtual and says so).
4. **Evidence:** `reports/submission/figures/INDEX.md` (figures 1 to 6) and the measured numbers below.
5. **Honesty:** the studio labels what is not verified (for example "Physical output not verified" on the halo, the
   RGB-D banner when camera frames are missing), and the state's `validity.hardware_verified` stays false until a
   measured 30-minute run on the rig. Say what was rehearsed and what was measured.

## Numbers you may quote (all measured except the labelled illustration; source in brackets)

- First Pi-to-laptop stream over Wi-Fi: 60 s, 716 frames, zero loss, 5.17 MB/s (`reports/hardware/pi/README.md`).
- A rig that never moved: pose spread 42 mm with 4 sweeps per scan and the IMU bias, 13.7 m with 1 sweep per scan
  (`docs/LAB_DAY.md`, overnight table). That is static precision (pose jitter), not accuracy.
- 30-minute parked live rehearsal on recorded real data: no errors, 2.6 cm ICP registration RMSE against the twin, memory
  flat (`reports/rehearsal/README.md`). The RMSE says the scan fits the twin, not error against independent ground truth.
  A rehearsal, not a hardware result.
- LiDAR-only fallback, 10-minute rehearsal on recorded real data: no errors, 2.7 cm ICP registration RMSE against the
  twin, p50 175 ms a frame (p95 776 ms, not clean: other jobs ran) (`reports/rehearsal/README.md`).
- Processing per frame (plain profile): p50 240 ms / p95 296 ms after the 5 November exact speed-ups (10-minute rehearsal;
  the first 30-minute run was p50 381 / p95 570 ms); the newest
  sweep is processed and older ones are dropped, so the picture stays current. The spec's 35 ms is a **future target**
  (`reports/ADDENDUM_REVIEW.md`), not met: the network is ~20 ms of it; point preparation, localization and the
  occupancy map take the rest.
- What that latency means physically, as a **conceptual illustration only, not a safety claim** (AetherTwin is not a
  safety device): someone walking fast at 2 m/s moves about 0.76 m in 381 ms and about 0.35 m in 175 ms, so the newest
  processed frame can show them that far behind where they are. Arithmetic on the p50s above, not a measurement.

Do not claim: a measured camera-LiDAR calibration unless step 5 produced one, accuracy while moving (only a SYNTHETIC
loop was measured, in simulation; its ATE is not a rig result), 2.6-2.7 cm or 42 mm as accuracy, 35 ms, physical LEDs, or a hardware-verified 30-minute run unless step 8 measured it.

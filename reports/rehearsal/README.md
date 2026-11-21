# Live-run rehearsals (laptop, loopback): NOT hardware results

`tools/rehearse_live.py` serves a real recorded session through `tools/replay_capture_node.py` over the real
raw-sensor transport on 127.0.0.1 and runs the real `command_center --mode live --parked` against it. It proves the
software path survives the duration; it says nothing about Wi-Fi, the Pi, a measured calibration or moving scenes.

## `live_parked_replay_30min.json` (2026-11-05, commit def8582, RTX 4070 laptop, `.venv-gpu`, `--device cuda`)

- Ran the full 1,800 s: runtime completed, no error, 0 frame errors, 0 source reopens needed.
- Transport: one session and one segment, 7.58 GB, no clock jumps, IMU sequence breaks, missing clouds or camera frames.
- Parked relocalization against the twin: accepted, fitness 0.976, RMSE 2.6 cm, correction 1.3 cm / 0.15 deg.
- Memory flat: 1.61 GB at start, 1.52 GB at the end, slope +0.03 MB/min over the second half (max 1.76 GB).
- Processing per frame p50 381 ms, p95 570 ms (localization 75, occupancy 119, perception and tracking 178, planning ~0 ms
  median): the 35 ms target is **not** met. 21,484 sweeps arrived; the live loop processes the newest one and dropped
  17,006 by design (latest-frame coalescing), so about 2.5 frames per second were processed.
- Before the overnight fixes the same rehearsal stopped processing for good within 2 minutes every time.

Twin: built from the same bench recording with the `hotspot_build.yaml` settings, a placeholder calibration
(`rehearsal-placeholder-NOT-A-CALIBRATION`) and an assumed 0.75 m LiDAR height. Not a commissioning result.

## `live_parked_fast_replay_30min.json` (2026-11-05, commit 5a50a73 plus the then-uncommitted `config/profiles/hotspot_parked_fast.yaml`)

Same rehearsal and twin as above, with the opt-in fast profile (in-memory dynamic occupancy map, planning grid
refreshed every 50 frames while no goal is set; results unchanged, speed only).

- Ran the full 1,800 s: no error, 0 frame errors, 0 source reopens; one transport segment, 7.48 GB, no clock jumps or
  IMU sequence breaks. Unlike the first run, 44 clouds (0.2 %) and 415 camera frames (2 %) were reported missing out of
  21,049 acquired; the cause is not established (other work ran on the laptop during part of this run).
- Parked relocalization: accepted, fitness 0.982, RMSE 2.7 cm, correction 1.0 cm / 0.16 deg.
- Processing per frame p50 275 ms, p95 350 ms (first run: 381 / 570 ms); occupancy median 60 ms (was 119). The 35 ms
  target is **not** met.
- Memory **bounded but not flat**: 5-minute medians 1657, 1692, 1712, 1618, 1650, 1670 MB (a sawtooth: the in-memory map
  prunes old cells), peaks of 2.03 GB in minutes 5-15, 1.68 GB at the end. The summary's second-half slope (+5.0 MB/min)
  is the rise after a drop at 15 min, not a trend over the run. Runs longer than 30 minutes with this profile are not
  verified; plain `hotspot.yaml` (first run) stayed flat.

## `live_lidar_only_replay_10min.json` (2026-11-05, commit 5a50a73 plus the then-uncommitted C28 LiDAR-only fallback)

The LAB_DAY step 5 fallback: no calibration on either end (replay node without `--calibration`, service with
`--lidar-only`), against a LiDAR-only twin built from the same bench recording (`hotspot_build.yaml`, assumed 0.75 m
LiDAR height; the build marked it `lidar_only_reference_eligible: true`, `live_reference_eligible: false`,
`camera_depth_used: false`).

- Ran the full 600 s: no error, 0 frame errors, 0 source reopens; one transport segment, 2.52 GB, no clock jumps, IMU
  breaks or missing clouds; 20 camera frames reported missing.
- The live service accepted the twin only because of `--lidar-only`; the state reported
  `validity.calibration_measured: false` with observations and localization valid.
- Parked relocalization: accepted, fitness 0.978, RMSE 2.7 cm, correction 1.1 cm / 0.04 deg.
- Processing p50 175 ms (localization 48, occupancy 38, perception and tracking 74 ms median); p95 776 ms is not a
  clean figure: the full test suite (8 parallel processes) and a studio browser session ran during part of the run.
  2,438 of 7,142 sweeps processed (newest-sweep policy), about 4 a second. Memory 1.50-1.75 GB, ending lower than it
  started.

## `live_parked_after_c30_c32_10min.json` (2026-11-05, commit e04c81a: idle planning grid skipped, depth-ray cache, batched deskew)

Same twin, placeholder calibration and plain `hotspot.yaml` as the first 30-minute run, 10 minutes. No error, 0 frame
errors, relocalized (fitness 0.986, RMSE 2.7 cm). Processing per frame **p50 303 ms, p95 403 ms** (first run: 381 / 570 ms;
that run was 30 minutes, this one 10), localization 64, occupancy 99, perception and tracking 135 ms median; 1,858 of
7,162 sweeps processed (about 3.1 a second, was 2.5). Memory flat (1.63 -> 1.65 GB, slope -0.2 MB/min). A documentation
agent was editing files during the run (light load). The 35 ms target is still not met.

## `live_parked_after_c37_c39_10min.json` (2026-11-05, commit 9a2d9ff: also exact tiled map, voxelize and frame-validation speed-ups)

Same twin, placeholder calibration, plain `hotspot.yaml` and 10 minutes, on a quiet machine. No error, 0 frame errors, no
missing clouds or camera frames, relocalized (fitness 0.973, RMSE 2.5 cm). Processing per frame **p50 240 ms, p95 296 ms**
(overnight start 381 / 570 ms; after C30-C32 303 / 403 ms); localization 59, occupancy 51 (was 99, then 119 in the first
run), perception and tracking 129 ms median. 2,423 of 7,161 sweeps processed (about 4.0 a second). Memory flat (slope
+0.02 MB/min). The 35 ms target is still not met.

## `live_parked_keepalive_10min.json` (2026-11-05, commit 991e8ea, opt-in GPU keep-alive ON: 2048 matmul every 10 ms)

Same twin, placeholder calibration and plain `hotspot.yaml` plus `perception.gpu_keepalive_ms: 10` and
`gpu_keepalive_matmul: 2048` (a scratch profile; the repository default stays 0), 10 minutes, quiet machine. No error, no
missing frames, relocalized (fitness 0.976, RMSE 2.6 cm), memory flat. Processing per frame **p50 204 ms, p95 239 ms**
(same setup without the keep-alive: 240 / 296 ms); perception and tracking 89 ms median (was 129). 2,798 of 7,161 sweeps
processed (about 4.7 a second). The extra power draw was not measured in this run (C46 in-process: about +8 W for this
setting). It shows that the laptop GPU's low power state between frames costs about 40 ms a frame; the owner's power
setting should recover this without the extra load.

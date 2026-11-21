# Lab session 2026-11-05 (afternoon): rig on the cart, LiDAR-only live run

Time was short, so the session took the LiDAR-only fallback of `docs/LAB_DAY.md` and cut, in order: the monitor
calibration, the commissioning push (it failed QA, below), the recorded walk session, the landmark spans and the
phone latency trials. Everything here is **LiDAR-only** (`calibration_measured: false`) and the live run lasted
**15 minutes**, so it is not the measured 30-minute run that `hardware_verified` needs (it stays false).

## Rig
Tape measurements and photos: [`rig_measurements.md`](rig_measurements.md), `photos/`. L2 upright on the cart, window
centre 9.0 cm above the floor; Gemini 336L 21 cm in front of it and 2.5 cm higher; cart 22.9 x 15.2 cm. Pi on the
laptop's Wi-Fi hotspot, L2 on the Pi's Ethernet (100 Mb/s), camera on the Pi's USB 3. The Pi logged one brief
undervoltage (2 s) after the camera's USB link reset; no throttling seen later.

## Recordings (raw data in `%USERPROFILE%/aethertwin_rec`, not in git)
| Recording | Result |
|---|---|
| `self_mask_parked`, 120 s, empty room, cart on the start mark | QA WARN only: 1,435 sweeps at 11.98 Hz, no transport losses, IMU 251 Hz, camera on every sweep; start-to-end ICP 5 mm / 0.25 deg; depth valid 46 % (low camera, mostly floor) |
| `commissioning_scan`, push | QA **FAIL**: capture stopped after 81 s (LiDAR / IMU / RGB-D sync timeout), turned at up to 109 deg/s (limit ~60), ended 0.38 m / 6 deg from the start. Not built. |

Self-mask: no persistent body returns above the floor, so no box is suggested (`self_mask_suggestion.json`; a correct
result for an upright L2 above the cart). Mounted IMU bias: `imu_bias_mounted.json`.

## Twin (from the stationary recording)
`main.py --mode build-twin --config config/profiles/hotspot_build.yaml --lidar-height 0.09` on `self_mask_parked`:
359 scans, no suspect jumps, start to end 0.05 m on a cart that did not move (static precision, not accuracy), floor
from the tape (`declared_lidar_height`, `measured: false`), `lidar_only_reference_eligible: true`. It covers what the
LiDAR sees from the start mark only. Files: `twin_static/`.

## 15-minute LiDAR-only live run (`--parked --lidar-only`, GPU, Wi-Fi)
| | |
|---|---|
| Parked localization | accepted on the first attempt: fitness 0.985, ICP RMSE 3.4 cm, correction 0.14 m / 1.3 deg (a fit to the twin, not accuracy) |
| Frames processed | 5,510 in 900 s: 6.1 a second end to end (the L2 sends ~12; the newest sweep is processed, older ones dropped by design) |
| Processing per frame | p50 140 ms, p95 216 ms, max 940 ms (localization 48, occupancy 37, perception and tracking 55 ms at p50) |
| Memory | 1.51 GB at start, 1.64 GB at the end, flat in the second half |
| Drops and errors | 0 dropped snapshots, telemetry error none |

The 35 ms target is not met. The phone latency clip was not usable (no trigger line, small and moving screen) and
was not scored, so there is **no world-to-display latency number**. Summary file: `live_run_lidar_only_15min/progress.json`.

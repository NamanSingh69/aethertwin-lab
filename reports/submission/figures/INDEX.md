# Submission figures

Generated 2026-11-04 21:24 UTC by `tools/submission_figures.py` in 25 s. Re-run:

```
python tools/submission_figures.py --recording C:/Users/user/aethertwin_rec/wifi_stationary_60s --recording C:/Users/user/aethertwin_rec/wifi_stationary_600s --reports reports/hardware --output reports/submission/figures --cloud-recording C:/Users/user/aethertwin_rec/wifi_stationary_600s --rgbd-frame 4000
```

Recordings: `wifi_stationary_60s` (716 frames, 60 s, motion: stationary, label: real devices via the Pi capture node); `wifi_stationary_600s` (7171 frames, 600 s, motion: stationary, label: real devices via the Pi capture node).

**Scope of all figures.** Real devices (Unitree L2, Orbbec Gemini 336L on USB 2 at 640x400, L2 built-in IMU) streamed from a Raspberry Pi 5 capture node to the laptop over Wi-Fi, rig standing still on a bench. Nothing here is calibrated (camera-LiDAR extrinsic, camera intrinsics beyond factory values, IMU), nothing is deskewed or masked, and nothing is compared with ground truth: these figures show that the sensors and the transport work and what the raw data look like, not accuracy, mapping quality or moving-robot behaviour.

## fig1a_l2_accumulation_plan.png

**What:** plan view (LiDAR frame, colour = height) of the real L2 returns from `wifi_stationary_600s` after 1 sweep (1 sweep; 5,141 returns, 1,681 occupied 5 cm plan cells), 12 sweeps (1 s; 62,540 returns, 7,500 occupied 5 cm plan cells), 120 sweeps (10 s; 626,206 returns, 13,388 occupied 5 cm plan cells), 718 sweeps (60 s; 3,746,172 returns, 16,056 occupied 5 cm plan cells). It shows how the L2's non-repetitive scan pattern densifies a stationary view over time; all returns are drawn, lowest per pixel on top.

**Does NOT show:** a map or SLAM result (the rig did not move; this is one viewpoint accumulated in the sensor frame), any accuracy, deskewing or self-masking (raw returns; the rig's own body may appear near the origin), or areas the L2 cannot see from this spot.

## fig1b_l2_perspective_coverage.png

**What:** the same accumulation of `wifi_stationary_600s` in perspective (colour = height; fading with distance is a depth cue only; returns above z = 1.84 m, 57% of them, likely ceiling, hidden so the room is visible), and the share of the final 58,753 occupied 5 cm voxels reached vs accumulation time (27 % after 1 s).

**Does NOT show:** coverage of a whole room or building (one stationary viewpoint), map quality, or accuracy; the voxel count includes ceiling returns and range outliers.

## fig2_rgb_depth_lidar_side_by_side.png

**What:** one real Gemini 336L colour frame and its depth map (frame 4000 of `wifi_stationary_600s`, 74% of pixels with depth, median 2.70 m), next to the L2 sweep of the same frame drawn in the LiDAR's own frame.

**Does NOT show:** LiDAR points projected into the image or any camera-LiDAR fusion: the extrinsic calibration has not been measured, so the panels are deliberately separate. Depth uses factory intrinsics (bring-up), not a calibration; no depth accuracy is claimed.

## fig3a_transport_timeseries.png

**What:** per-second frame rate, laptop-side transport bandwidth, node-to-host clock-offset change, clock error bound (half the best recent ping round trip), and camera-to-sweep pairing error, from the per-frame diagnostics in `wifi_stationary_60s` (median 12.0 Hz, mean 4.78 MB/s, offset drift range 0.88 ms, bound median 0.85 ms / max 17.81 ms, pairing p95 18.4 ms); `wifi_stationary_600s` (median 12.0 Hz, mean 4.86 MB/s, offset drift range 3.21 ms, bound median 0.85 ms / max 21.33 ms, pairing p95 18.2 ms).

**Does NOT show:** wired-transport, moving-robot or multi-hour behaviour; hardware time sync (none: hardware_sync is false); the clock bound limits the node-to-host mapping only and is not a camera-LiDAR synchronisation or calibration result.

## fig3b_transport_summary_table.png

**What:** a table of the headline numbers copied from `reports/hardware/pi/capture_report_wifi_stationary_600s.json`, `reports/hardware/pi/capture_report_wifi_stationary_60s.json` (duration, rates, drops, missing data, stalls, bandwidth, clock bound, arrival latency).

**Does NOT show:** anything re-measured: values are copied from the reports as written by the capture tool (one row, bytes / duration, is derived from two copied values). Rates, bandwidth and clock values in the reports are the receiver last-5-s or end-of-run snapshots, so they differ from the run averages in fig3a. The same stationary Wi-Fi limits apply.

## fig4_imu_still_stats.png

**What:** per-axis histograms of the L2 built-in IMU while still, and mean/bias per recording: `wifi_stationary_60s` 15,076 samples, |accel| 10.521 m/s^2 (gravity 9.807), z accel bias +0.715 m/s^2, gyro std 5.2 mrad/s max axis; `wifi_stationary_600s` 150,788 samples, |accel| 10.528 m/s^2 (gravity 9.807), z accel bias +0.722 m/s^2, gyro std 5.1 mrad/s max axis; plus the row from `reports/hardware/pi/imu_bias_still_60s.json`.

**Does NOT show:** an IMU calibration (one orientation cannot separate a z bias from a z scale error), temperature behaviour, or noise while moving; the spread may include bench vibration.

## fig5_sweep_start_gap.png

**What:** the start gap between consecutive real L2 sweeps (next start minus this start plus sweep duration, host clock): `wifi_stationary_60s` 715 gaps, 80 negative (11%), min -0.77 ms, max +0.75 ms, 0 beyond the -2 ms tolerance; `wifi_stationary_600s` 7,170 gaps, 943 negative (13%), min -0.76 ms, max +4.72 ms, 0 beyond the -2 ms tolerance. Negative gaps (overlaps) made the IMU deskewer raise "Overlapping sweeps" on real data; it now accepts overlaps up to 2 ms (`slam_and_twin/imu_deskew.py`, `OVERLAP_TOLERANCE_SECONDS`).

**Does NOT show:** that deskewing is accurate, or behaviour on a moving rig; the gaps are timestamp jitter of a stationary stream mapped onto the laptop clock.

## Other hardware evidence (not plotted)

Reports in `reports/hardware` without a figure: `device_check_gemini_usb2.json`, `device_check_l2_clockfit.json`, `device_check_l2_run1.json`, `device_check_l2_run2.json`, `link_test_esp32s3.json`, `link_test_esp32s3_usb.json`.

## fig6_live_rehearsal.png

**What:** the parked live service run by `tools/rehearse_live.py` for 30 minutes on the real bench recording `wifi_stationary_60s` replayed over the real raw-sensor transport on loopback (commit def8582): ran the full duration without a runtime error; processing p50 381 ms / p95 570 ms per frame against the 35 ms target (not met); memory 1613 -> 1516 MB; 7.58 GB over 1 transport segment(s). Source: `20261104T232840Z-792ce2/summary.json`.

**Does NOT show:** Wi-Fi delay or loss, the Pi, a measured camera-LiDAR calibration (a placeholder was used), a commissioned twin of the lab (built from the same bench recording with an assumed LiDAR height), or moving people: it shows that the software path runs the full duration.

## fig7_latency_progression.png

**What:** processing time per frame (p50 / p95) of the parked live service in five rehearsals by `tools/rehearse_live.py`, each on the same real bench recording replayed over the real raw-sensor transport on loopback. Plain `hotspot.yaml` profile, in order: `live_parked_replay_30min.json` (30 min, commit def8582) p50 381 ms / p95 570 ms; `live_parked_after_c30_c32_10min.json` (10 min, commit e04c81a) p50 303 ms / p95 403 ms; `live_parked_after_c37_c39_10min.json` (10 min, commit 9a2d9ff) p50 240 ms / p95 296 ms. Per-stage medians of these three runs: 1. first run: localization 75, occupancy 119, perception and tracking 178 ms; 2. after C30-C32: localization 64, occupancy 99, perception and tracking 135 ms; 3. after C37-C39: localization 59, occupancy 51, perception and tracking 129 ms. Comparators, not steps of the progression: `live_parked_fast_replay_30min.json` (30 min, commit 5a50a73 plus the then-uncommitted fast profile) p50 275 ms / p95 350 ms; `live_lidar_only_replay_10min.json` (10 min, commit 5a50a73 plus the then-uncommitted LiDAR-only fallback) p50 175 ms / p95 776 ms (p95 not clean: other jobs ran during part of the run). The 35 ms target is not met by any run. Between the plain runs only exact speed-ups (identical outputs) were applied. Source: the summaries in `reports/rehearsal/` (context in its README.md).

**Does NOT show:** hardware results (loopback, not Wi-Fi or the Pi; a placeholder calibration, none in the LiDAR-only run; a twin built from the same recording with an assumed LiDAR height), runs of equal length (30 vs 10 minutes, so the p95 values cover different spans), a controlled machine load (other work ran during some runs, see the README), the processing of every sweep (the live loop takes the newest one), or whether the 35 ms target can be reached.

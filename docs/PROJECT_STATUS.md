# Project status — GitHub source snapshot, 29 October 2026

This repository preserves the accumulated implementation, original requirements,
trained synthetic models, demonstration data, reconstruction assets, frontend,
firmware sources and measured software evidence. It is a research platform;
simulation verification does not establish physical lab accuracy.

## Current hardware decision

Raspberry Pi 5 captures Unitree L2 points/IMU and Orbbec Gemini 335L RGB-D.
A Windows workstation with a user-reported NVIDIA A2000, 12GB VRAM and 32GB RAM
performs ML, mapping, tracking, planning and SSD recording. ESP32 handles status
and halo peripherals. A 32GB microSD is for the Pi OS/drivers, not long captures.
Jetson and 2D LiDAR are not part of the active deployment plan.

## What exists

- Deterministic simulation, immutable contracts, synchronization, continuous
  deskew, registration, reconstruction, tiled occupancy and recording/replay.
- Trained synthetic semantic/flow and Point-MAE checkpoints, training/evaluation
  workflows, tracking, risk/geofences and collision-checked trajectories.
- React/Three.js command center, Open3D/OpenCV viewers, scene telemetry, private
  scene gateway, commissioning jobs, virtual halo and firmware build evidence.
- Windows/Ubuntu x86 CPU dependency locks, native ray implementation, source
  SDK bindings and repeatable tests.

## Evidence boundaries

`reports/release_validation.json` and `reports/ACCEPTANCE.md` describe the earlier
2.0.0 software release, including 323 Windows tests, 322 Ubuntu tests plus one
skip, and 41 browser tests plus one optional skip. Their hashes and performance
numbers are historical evidence, not a claim that this snapshot or new hardware
has rerun all release gates. The canonical synthetic model selected epoch 18
from 24 epochs; its scores are in `reports/canonical_checkpoint.json`.

This source snapshot also retains later recording-validation, occupancy
aggregation and hardware-readiness work. `maintenance/` is provisional: its
readiness helper still describes a legacy Jetson setup, has no public CLI, and
must not be used as the Pi commissioning tool. New repository validation is
reported separately in `reports/github_snapshot_validation.json`.

## Remaining work

1. Implement a bounded Pi-to-Windows raw-sensor transport and receiver with
   timestamps, calibration identity, disconnect handling and explicit drops.
   The existing scene gateway does not provide this function.
2. Verify both SDKs on the actual Pi and both sensors simultaneously. Measure
   USB power stability, Ethernet bandwidth, clock residuals and storage rates.
3. Create and validate a Windows CUDA dependency profile on the actual A2000.
   Existing locked Windows dependencies install CPU PyTorch.
4. Measure camera/LiDAR calibration and validate short stationary/moving
   recordings before the full lab scan. Supplied calibration is simulation-only.
5. Evaluate real labelled lab recordings; measure GPU latency, memory and a
   30-minute hardware run. 35ms remains a future processing target.
6. Identify the ESP32 board/wiring before flashing; validate physical outputs
   and watchdog behaviour. Configure authenticated private remote deployment.

Original specifications and approved historical plans are intentionally retained.
Where those mention Jetson, S2 or old sensor names, this current hardware decision
and the corrections in README take precedence for new deployment work.

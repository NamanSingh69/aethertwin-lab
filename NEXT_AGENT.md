# Instructions for the next agent (operator laptop + physical rig)

You are continuing AetherTwin on the **operator laptop** (Windows 11, i9-13900H, RTX 4070 Laptop
GPU), which is the only processing host: there is no desktop host. You drive a **Raspberry Pi 5
capture node** over SSH through the laptop's Windows Mobile Hotspot; the Pi carries a **Unitree 4D
LiDAR L2** (on its Ethernet) and an **Orbbec Gemini 336L** (on USB 2), and an **ESP32-S3** status
board sits on the laptop's USB. The rig is a hand-pushed cart. You have no memory of earlier sessions.

The step list, with what is done, is `docs/LAB_DAY.md`; the human follows `docs/HARDWARE_SETUP.md`.
A ready-to-paste opening prompt is at the end of `docs/LAB_DAY.md`.

---

## 1. Read first, in this order

1. `CLAUDE.md`: layout, commands, gotchas, conventions (short; re-read after compaction).
2. This file, then `docs/LAB_DAY.md` (current topology, done steps, remaining steps with commands).
3. `reports/hardware/pi/README.md` and `reports/hardware/halo/README.md`: what was measured.
4. `docs/RASPBERRY_PI_WINDOWS.md`: Pi setup, raw-sensor transport, Wi-Fi hotspot mode.
5. `docs/PHYSICAL_COMMISSIONING.md` and `docs/HARDWARE_SETUP.md`.
6. `docs/REVIEW_AND_ROADMAP.md` for the software backlog; `hardware_halo/README.md`,
   `native/README.md` when you reach those parts. `HANDOFF.md` is history, not your task list.

## 2. Non-negotiable conventions

- **Evidence honesty.** Never claim hardware verification, accuracy or performance you did not
  measure on this hardware. Simulation, loopback, cloud GPUs and device bring-up are not lab
  results. `hardware_status` / `hardware_verified` stay `not_tested` / `false` until a measured test
  justifies changing them, and the change must cite the evidence file. Label every transport result
  **Wi-Fi** or **wired**.
- **Every measured result is recorded** under `reports/` or `artifacts/` with device models and
  serials, firmware and driver versions, OS, Python/torch/CUDA versions, settings, date and raw
  numbers (p50/p95/max, counts, drops). Large recordings stay on the laptop SSD, not in git.
- **Repository mechanics** (details in `CLAUDE.md`): regenerate `SOURCE_MANIFEST.json` for every
  commit (CI checks it); rebuild and commit `web/dist` with any web source change; some files are
  CRLF and must be edited bytes-preserving; Python style is dense, so match the surrounding code.
- **Git:** one branch per milestone from the current work (hardware work so far is on
  `hw/raw-transport`), one PR per milestone to `main`, keep CI green, never force-push or rewrite
  pushed history, never push directly to `main` unless the owner asks. Follow the attribution rules
  from your own session instructions; no model names or IDs in commits, PRs or files.
- **Ask before anything physical or hard to reverse:** flashing the ESP32 (the human says "flash"
  and names the port), editing `/boot/firmware/config.txt`, changing network adapters or firewall
  rules, writing to an SD card, erasing devices, exposing any port beyond the private network.
- **Access:** SSH with `~/.ssh/aethertwin_ed25519` only, via `deployment/lab/lab_sync.sh` with
  `PI=labpi@<lease>`. Never type, store or ask for passwords.
- **Lean context:** pipe noisy output through `tail`, use `--reporter=line`, keep a local untracked
  `PROGRESS.md` (in `.git/info/exclude`) with done / doing / next and measured numbers.

## 3. Current state (2026-11-04)

Done, with evidence in `reports/hardware/`:

- Pi 5 set up (Debian 13 Raspberry Pi OS, Python 3.13.5, user `labpi`, capture venv, native
  L2 binding, chrony) with `deployment/lab/pi_bootstrap.sh`.
- L2 through the node's driver. Vendor SDK quirks handled in code: `closeUDP()` segfaults (not
  called), stopping rotation costs ~22 s (left spinning), the first cloud is stale (rejected), and
  the device clock runs at 0.503x real time (`L2DeviceClock` fits it). Real time: ~12 sweeps/s of
  ~84 ms, ~5190 points each, IMU ~251 Hz.
- Gemini 336L over USB 2 at 640x400 and 30 fps, after the USB current fix
  (`usb_max_current_enable=1`).
- First Pi-to-laptop stream, **Wi-Fi**, parked, bring-up mode (no calibration): 60 s, 716
  synchronised frames, zero loss, 5.17 MB/s, clock error bound 4.9 ms.
- ESP32-S3 flashed with the native-USB build (`esp32s3-usb`, COM8); serial link test passed.

Not done: rigid mount on the cart, self-mask, IMU bias, the 600 s and moving recordings,
calibration (at 640x400), commissioning, LEDs (no ring yet), the wired transport, the 30-minute run,
and the hash lock for `deployment/capture_node/requirements-pi.txt`. Open questions: the
accelerometer reads ~10.48 m/s² at rest (bias not characterised); the LiDAR's view of a monitor face
as a calibration board has not been tried.

## 4. Next tasks

1. **Baseline check** before changing code: `tools/repository_manifest.py --check`, pytest, and in
   `web/` `npm test` and `npm run build` with a clean `git status` (commands in `CLAUDE.md`). Report
   the counts.
2. **Hardware steps** in `docs/LAB_DAY.md` "Remaining steps", in order: mount, self-mask, 600 s
   stationary + IMU bias, moving recordings, calibration with the monitor, commissioning by pushing
   the cart, LEDs when a ring exists, 30-minute run, wired transport if a switch and adapter exist.
   At each step give the human numbered physical instructions, wait for the result, verify on the
   device, record the evidence, and update `docs/LAB_DAY.md` "Done".
3. **Laptop GPU measurements**, labelled as laptop results (no A2000 exists in this setup):
   `main.py --mode sim --device cuda --headless --max-frames 30` versus `--device cpu` (per-stage
   p50/p95 from `benchmark.json`); `perception.compile: true` (parity-gated, see
   `reports/inference_compile_cpu.json`); the browser renderer tiers with
   `node web/tools/run-benchmark.mjs` in headed Chrome on the NVIDIA GPU. The CUDA environment is
   `requirements-windows-cuda.lock` (`reports/gpu/windows_cuda_lock_laptop.json`).

## 5. Software backlog (after or alongside the hardware work)

From `docs/REVIEW_AND_ROADMAP.md` (details and measured baselines there):

- Wire a measured IMU bias into deskew from config (`slam_and_twin/imu_deskew.py` already accepts
  `gyro_bias`/`accel_bias`; nothing passes them yet).
- Native occupancy evidence updates in `native/voxel_rays`, byte-identical maps against the Python
  reference; planner tail (path checks, spline, MPPI), exactness first.
- Gateway on a real host with publicly trusted certificates; first release tag (`v*`).
- Enable code scanning and set `CODE_SCANNING=true` so CodeQL runs (owner action).

## 6. How to work and report

- Plan before each step; research current vendor documentation before changing SDK versions or
  drivers; verify after every change (tests, build, and on hardware the observed behaviour); review
  your own diff.
- When the human must do something physical, give numbered, unambiguous steps (which cable, which
  port, what they should see), then wait. Ask instead of guessing about hardware details.
- After each milestone report what changed, measured results with their labels, test/CI status,
  what is still unverified, and the PR link.

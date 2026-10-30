# Instructions for the next agent (Windows desktop + physical rig)

You are continuing AetherTwin on a **Windows desktop** (the processing host,
user-reported NVIDIA A2000 12 GB / 32 GB RAM) that will be connected to a
**Raspberry Pi 5 capture node** with a **Unitree 4D LiDAR L2**, an **Orbbec
Gemini 335L** and an **ESP32 halo**. You have no memory of earlier sessions.
Everything you need is in this repository on `main`.

A ready-to-paste opening prompt for you is at the end of this file. The person
you work with follows `docs/HARDWARE_SETUP.md` at the bench; you guide them.

---

## 1. Read first, in this order

1. `CLAUDE.md` — layout, commands, gotchas, conventions (short; re-read after compaction).
2. This file.
3. `docs/REVIEW_AND_ROADMAP.md` — what was changed and measured, and the prioritised backlog.
4. `docs/RASPBERRY_PI_WINDOWS.md` — Pi/Windows setup commands, addresses, SDK versions.
5. `docs/PHYSICAL_COMMISSIONING.md` — the ordered commissioning procedure.
6. `docs/HARDWARE_SETUP.md` — what the human does at the bench (wiring, mount, safety).
7. `hardware_halo/README.md`, `native/README.md`, `deployment/gateway/README.md` when you reach those parts.
8. `HANDOFF.md` is history from the improvement pass; useful background, not your task list.

## 2. Non-negotiable conventions

- **Evidence honesty.** Never claim hardware verification, accuracy or
  performance you did not measure on this hardware. Simulation, cloud GPUs and
  the earlier laptop are not lab results. `hardware_status` / `hardware_verified`
  stay `not_tested` / `false` until a measured test justifies changing them, and
  the change must cite the evidence file.
- **Every measured result is recorded** under `reports/` or `artifacts/` with:
  device models and serials, firmware and driver versions, OS, Python/torch/CUDA
  versions, settings, date, and raw numbers (p50/p95/max, counts, drops).
- **Repository mechanics** (details in `CLAUDE.md`): regenerate
  `SOURCE_MANIFEST.json` for every commit (CI checks it); rebuild and commit
  `web/dist` with any web source change; some files are CRLF and must be edited
  bytes-preserving; Python style is dense, so match the surrounding code.
- **Git:** work on a new branch from `main` per milestone (for example
  `hw/bringup`, `hw/raw-transport`, `hw/calibration`), open one PR per milestone
  to `main`, keep CI green, never force-push or rewrite pushed history, never
  push directly to `main` unless the owner asks. Follow the attribution and
  commit-trailer rules from your own session instructions. Do not put model names
  or IDs in commits, PRs or files.
- **Ask before anything physical or hard to reverse:** flashing the ESP32,
  changing network adapters or firewall rules, writing to the SD card, erasing
  devices, exposing any port beyond the private sensor network.
- **Lean context:** pipe noisy output through `Select-Object -Last N` / `tail`,
  use `--reporter=line`, keep a local untracked `PROGRESS.md` (add it to
  `.git/info/exclude`) with done / doing / next and measured numbers.

## 3. Phase 0 — verify the baseline on the desktop

Prerequisites (install if missing, then report versions): Git for Windows (Git
Bash is used by the repo's pre-commit hook), Python **3.12** with the `py`
launcher, Node.js **22 LTS or newer**, Chrome, the NVIDIA driver (`nvidia-smi`).

```powershell
git clone https://github.com/NamanSingh69/aethertwin.git
cd aethertwin
git config core.autocrlf false
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-windows.lock
.\.venv\Scripts\python.exe tools\repository_manifest.py --check
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Expected: manifest verifies; pytest ≈ 358 passed, 2 skipped (the count may
differ by one or two on Windows). Then:

```powershell
cd web
npm ci
npm run typecheck
npm test
npm run build
git status --short   # must print nothing: the committed dist is reproducible
npx playwright install chromium
npx playwright test --project=mock --reporter=line
```

Live E2E: start `.\.venv\Scripts\python.exe -m command_center --mode sim --threads 4`
in one terminal, then in `web\` set `$env:AETHERTWIN_SERVICE_URL="http://127.0.0.1:8080"`
and run `npx playwright test --project=live --reporter=line`. Report every count
and any difference before changing code.

## 4. Phase 1 — GPU environment and desktop measurements

1. Record `nvidia-smi` (GPU name, VRAM, driver, CUDA version), CPU, RAM, SSD.
2. Create `.venv-gpu` and install CUDA torch exactly as in
   `docs/RASPBERRY_PI_WINDOWS.md` ("Windows GPU environment"); check that the
   `cu126` index still serves `torch==2.14.1` and pick the CUDA build matching
   the installed driver.
3. Produce a hash-locked **Windows CUDA lock** (`requirements-windows-cuda.lock`),
   mirroring how `requirements-ubuntu-cuda.lock` was made (see
   `tools/lock_dependencies.py` and `reports/modal/README.md`); install it into
   a clean venv with `--require-hashes`, run pytest, and record the result.
4. Measure, labelled as **A2000 workstation** results:
   - `main.py --mode sim --device cuda --headless --max-frames 30` versus
     `--device cpu`: per-stage p50/p95 from `benchmark.json`.
   - `perception.compile: true` (parity-gated, see `reports/inference_compile_cpu.json`):
     compile time, steady p50/p95, `diagnostics.inference_compile` status;
     decide whether to warm up at service start and document the decision.
   - Browser renderer: `node web/tools/run-benchmark.mjs` (headed Chrome on the
     NVIDIA GPU; check `chrome://gpu`) in streaming mode for each quality tier;
     tune the `auto` tier thresholds from these numbers and document them.
5. Re-run the cloud suite if the owner has Modal configured on this device:
   `modal run tools/modal_bench.py --suite latency` (the stored numbers predate
   the perception, planner, occupancy and GC speedups).

## 5. Phase 2 — Pi bring-up with the human

Guide the human through `docs/HARDWARE_SETUP.md` sections 1–4 one step at a
time, waiting for their confirmation and outputs. From the desktop you can SSH
into the Pi (`ssh aether@192.168.1.2`) once its static address works. Capture
and commit as evidence (e.g. `reports/hardware/bringup_<date>.json` plus the
raw text): Pi OS and kernel, `vcgencmd get_throttled` before/after 10 minutes,
L2 serial/firmware and the SDK example output (points **and** IMU), camera
serial/firmware, `lsusb -t` speed, the enumerated depth/colour profiles, and
clock-sync status (`chronyc tracking`). Configure time sync between the desktop
and the Pi on the private network (the desktop or the Pi as the reference) and
record the residuals; capture timestamps must be comparable across hosts.

## 6. Phase 3 — build the Pi-to-Windows raw-sensor transport (main software task)

It does not exist yet. The existing scene gateway relays processed scenes, not
raw sensor data. Design it, implement it, test it without hardware, then on
hardware. Requirements:

**Pi side (capture service).** Runs on the Pi in a small venv built from Pi
packages, not the x86 locks. Uses the existing drivers
(`ingestion/lidar_l2_driver.py` with the pybind adapter in `native/` built on
the Pi against the pinned Unitree SDK, and `ingestion/gemini_333l_driver.py`
with `pyorbbecsdk2`). For each sweep it produces exactly what
`core.contracts.MultiModalSensorFrame` needs: L2 points with per-return
offsets, IMU samples (all of them, no gaps hidden), depth and colour with
camera timestamps, device timestamps **and** a common monotonic/UTC capture time,
sensor serials, calibration identity, sequence numbers.

**Wire protocol.** Versioned, length-prefixed binary messages over TCP on the
private network (bind only to 192.168.1.2; reject other peers; add a shared-key
HMAC handshake or mutual TLS so a stray device cannot inject frames). Separate
channels or message types for LiDAR, IMU and camera so a slow camera cannot
starve IMU. Lossless depth compression (e.g. zstd or PNG-16) and configurable
colour compression; measure the bandwidth budget (raw 1280×800×16-bit depth at
30 fps is ≈ 61 MB/s, close to gigabit capacity with colour).

**Flow control and faults.** Bounded queues on both ends with an explicit
policy (drop oldest camera frame, never silently drop IMU; count and report
every drop). Detect disconnects, stalls and clock jumps; reconnect with
backoff; on reconnect start a new segment rather than splicing time. A missing
modality is an explicit fault, never a simulated replacement.

**Windows side (receiver).** A source class with the same iterator contract as
`ingestion/live_source.py::LiveSensorSource`, so `main.py --mode live` and the
browser runtime can use it (for example `--source remote --capture-host
192.168.1.2`). Integrate with the existing recorder (`--record`) so raw
segments are written to the desktop SSD and replay with `--mode replay`.
Expose transport health (rates, drops, queue depths, clock residual, last error)
in the scene diagnostics so the studio's existing sensor lights and warnings
reflect it.

**Tests.** Unit tests with synthetic frames over loopback: framing, versioning,
HMAC/TLS rejection, reordering, drops, disconnect/reconnect, clock jumps,
bounded memory under a slow consumer, and record-then-replay equality. A
soak test (≥ 10 minutes) with synthetic data. Then on hardware: 1-minute,
10-minute stationary and moving recordings that replay on Windows, with
measured rates, drops and bandwidth committed as evidence.

Open a PR for the transport once the loopback tests and the first hardware
recordings pass. Keep the Pi capture code minimal and documented in
`docs/RASPBERRY_PI_WINDOWS.md`, including how to run it as a systemd service.

## 7. Phase 4 — calibration, commissioning and the halo

1. **Calibration** (`docs/PHYSICAL_COMMISSIONING.md` step 3, `--mode calibrate`):
   guide the human through `docs/HARDWARE_SETUP.md` section 6. Score on held-out
   poses; inspect six-degree observability and conditioning; a low fitted error
   alone is not acceptance. Store the calibration record with camera profile,
   units, transform direction, sensor serials and timestamp policy.
2. **IMU and deskew** (step 4): measure IMU rate, gaps, bias and clock residuals.
3. **Commissioning scan and twin** (steps 5–7, `--mode build-twin`): static
   declaration, coverage and unknown pockets, loop error, and the human's tape
   measurements as the independent accuracy check. Then relocalize at the fixed
   mount and set real start/goal and geofences.
4. **ESP32 halo** (step 9, `docs/HARDWARE_SETUP.md` section 5): confirm the board
   model and wiring with the human, build with
   `python hardware_halo/build_firmware.py --setup`, and **ask before flashing**
   (flashing needs the human's explicit go-ahead and the COM port they name).
   Set `hardware_halo: {enabled: true, port: <COM port>}` in the lab config
   (see `config/profiles/` and `command_center/runtime.py`). Walk through every colour state
   with the human watching (disconnected, watchdog, stale, each distance band)
   and record what they saw. Known firmware follow-ups from the review (device
   heartbeat, inter-byte timeout and 0xA7 rescan, loop watchdog, I²C timeout)
   should be implemented and verified on the board.

## 8. Phase 5 — 30-minute live run and evidence

Run the full live pipeline for 30 minutes with people moving (step 10). Record
acquisition age, per-stage latency percentiles against the 35 ms target, GPU and
CPU memory, queue depths and drops, render frame times, recording throughput,
Pi throttling/temperature and GPU temperature. Commit the evidence. Only then
propose changing `hardware_status` for the specific measured configuration, and
explain to the owner what is and is not verified.

## 9. Remaining software backlog (after or alongside the hardware work)

From `docs/REVIEW_AND_ROADMAP.md` (read it for details and measured baselines):

- **Native occupancy evidence updates** (biggest remaining CPU cost): move the
  per-voxel evidence update from the Python dict into `native/voxel_rays`
  (C), rebuild both the Linux `.so` and the Windows `.dll` (this desktop can
  build the DLL; use WSL or a Linux container for the `.so`), keep the Python
  path as the reference, and prove byte-identical maps and trajectories with
  old/new comparisons as earlier commits did.
- Planner tail: path checks, spline and MPPI (each ~13–50 ms per replan on CPU);
  exactness first (identical routes), then speed.
- Gateway on a real host with publicly trusted certificates; first release tag
  (`v*`) to exercise the release workflow.
- Enable code scanning and set the repository variable `CODE_SCANNING=true` so
  CodeQL runs (owner action).

## 10. How to work and report

- Plan before each phase; research current vendor documentation before
  changing SDK versions or drivers; verify after every change (tests, build,
  and on hardware the actual observed behaviour); review your own diff.
- When the human must do something physical, give numbered, unambiguous steps
  (which cable, which port, what they should see), then wait for their result.
  Ask clarifying questions when a hardware detail is unknown instead of guessing.
- After each phase report: what changed, measured results with their labels,
  test/CI status, what is still unverified, and the PR link.

---

## Ready-to-paste opening prompt for the next agent

```text
You are continuing the AetherTwin project on this Windows desktop, which will act as the
processing host for a physical rig (Raspberry Pi 5 capture node, Unitree 4D LiDAR L2,
Orbbec Gemini 335L, ESP32 halo). You have no memory of earlier work.

1. Clone https://github.com/NamanSingh69/aethertwin (branch main) and read, in order:
   CLAUDE.md, NEXT_AGENT.md, docs/REVIEW_AND_ROADMAP.md, docs/RASPBERRY_PI_WINDOWS.md,
   docs/PHYSICAL_COMMISSIONING.md and docs/HARDWARE_SETUP.md. NEXT_AGENT.md is your task
   list and rulebook; follow its conventions exactly (evidence honesty, manifest, committed
   web/dist, CRLF files, one branch and PR per milestone, no force-push, ask before
   flashing or network changes).
2. Create PROGRESS.md (local, untracked via .git/info/exclude) and keep it updated.
3. Do Phase 0 (baseline on this desktop) and report every count before changing code.
4. Continue through the phases in NEXT_AGENT.md: GPU environment and A2000 measurements,
   Pi bring-up with me at the bench, the Pi-to-Windows raw-sensor transport (the main
   software task), calibration, commissioning, the ESP32 halo, the 30-minute live run, then
   the remaining software backlog.
5. Whenever I need to do something physical, give me numbered, precise steps (what to plug
   where, what I should see, what to send back), wait for my result, and ask me questions
   instead of guessing about hardware details. Keep docs/HARDWARE_SETUP.md accurate as you
   learn the real devices.
6. Plan before each phase, research current vendor documentation before relying on it,
   verify everything on the real devices, review your own work, commit in logical chunks,
   and after each phase report what changed, measured results with their labels, test and
   CI status, what remains unverified, and the PR link.
```

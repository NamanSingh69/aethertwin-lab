# AetherTwin: case study

A LiDAR and RGB-D digital twin of a robotics lab, with a live browser studio. Student research project, K-Explore
Robotics, Kalinga Institute of Industrial Technology (KIIT), Bhubaneswar. This page is the one-document summary of the
project as it stands on 21 Nov 2026. Every number names what it measures and its source file; nothing here is an
accuracy claim.

- Public site (recorded replay, not live): https://aethertwin-showcase.vercel.app
- Source: https://github.com/NamanSingh69/aethertwin-lab
- Poster: `reports/poster/AetherTwin_poster_A3.pdf`. Demo runbook: `docs/DEMO.md`.
- Stack: Python 3.12, PyTorch, Open3D, FastAPI; React 19, TypeScript, Three.js, Playwright; Raspberry Pi 5,
  Unitree L2 LiDAR, Orbbec Gemini 336L, ESP32-S3 (serial link only; LEDs not fitted).
- Where it goes next: [`FUTURE_WORK.md`](FUTURE_WORK.md). Resume wording: [`RESUME_ENTRY.md`](RESUME_ENTRY.md).

## 1. The problem

Floor plans and photos lose a lab's 3D geometry, and a robot that only reacts to what is already close cannot share a
room with people comfortably. The goal is a measured 3D copy of the lab that stays current: what is there, what is
moving, where the risk is, and where a robot could safely go.

## 2. What was built

**Rig.** A hand-pushed cart (22.9 x 15.2 cm) carrying a Unitree 4D LiDAR L2 (upright, window 9.0 cm above the floor,
built-in IMU) and an Orbbec Gemini 336L depth camera (640x400), read by a Raspberry Pi 5. All processing runs on one
Windows laptop (RTX 4070 Laptop GPU) reached over its own Wi-Fi hotspot. No cloud. (`reports/lab/rig_measurements.md`)

**Pipeline** (Python 3.12, about 30,700 non-blank lines):

| Stage | What it does | Code |
|---|---|---|
| Capture | The Pi reads LiDAR, IMU and RGB-D and forwards raw data only | `ingestion/capture_node.py` |
| Transport | Own protocol over Wi-Fi: mutual HMAC authentication, a MAC and strict sequence number per message, clock sync | `ingestion/raw_transport.py` |
| Sync and deskew | One timeline for three sensors; each point moved back to where it was by IMU integration | `slam_and_twin/imu_deskew.py` |
| Register | ICP scan registration and a pose graph; a parked rig localizes once against the twin | `slam_and_twin/` |
| Map and twin | Log-odds occupancy, TSDF, Poisson display mesh; unknown space is never assumed free | `slam_and_twin/log_odds_octomap.py` |
| Perceive and track | Sparse point network for voxel semantics (trained on synthetic data only); 9-state Kalman tracker | `perception_ml/`, `tracking_and_navigation/` |
| Risk and plan | Directional risk field with time-to-collision, protected zones, A* plus B-spline planner (simulation only) | `tracking_and_navigation/` |
| Serve | FastAPI service, versioned binary scene protocol over WebSocket | `command_center/` |

**Studio** (React 19, TypeScript, Three.js, about 11,300 lines with tests): instanced rendering, streamed map tiles,
layer toggles, replay scrubbing, keyboard shortcuts, automatic quality tiers, and an explainer page with four
interactive labs. The same app runs live, in simulation and as the public static replay.

**Public site.** A landing page (results ledger, pipeline, rig, roadmap) plus the studio replaying 30 s of the 5 Nov
lab recording from static files. The exporter fails if an address, path or device identifier would leak into public
files, and a scripted gate (`tools/predeploy_check.py`) checks headers, leaks and projected traffic before any deploy.

## 3. Results (measured)

| Value | What it is | Conditions | Source |
|---|---|---|---|
| 15 min, 5,510 frames processed | Live run on the real rig over Wi-Fi | LiDAR-only; the newest sweep is processed and older ones skipped by design (the L2 sends about 12 a second); 0 dropped snapshots; memory flat 1.51 to 1.64 GB | `reports/lab/README.md` |
| p50 140 ms, p95 216 ms | Processing time per frame, live | About 6 frames a second; target 35 ms not met | `reports/lab/README.md` |
| ICP RMSE 3.4 cm, fitness 0.985 | Fit of the live scan to the twin | A fit to our own twin, not accuracy | `reports/lab/README.md` |
| 1,435 sweeps, 0 lost | 120 s lab recording on the cart | 11.98 Hz, IMU 251 Hz, a camera frame on every sweep | `reports/lab/README.md` |
| 7,171 frames, 0 lost in 600 s | Pi-to-laptop stream, bench | Clocks within +/-0.9 ms | `docs/LAB_DAY.md` |
| 381 to 240 ms p50 | Exact (same-output) speed-ups | Rehearsals on recorded data, not live (381 from a 30-minute run, 240 from a 10-minute run) | `reports/rehearsal/README.md` |
| 359 scans, 61,367 surface points | The lab twin | One stationary viewpoint; floor height from a tape | `reports/lab/README.md` |
| 713 Python tests passed | Automated suite, 21 Nov 2026 | Plus 88 web unit tests and Playwright end-to-end with axe | `reports/test_suite/README.md` |

## 4. Not measured, not done

- No camera-LiDAR calibration yet: the live run was LiDAR-only. The camera streams but is not fused.
- No accuracy against independent ground truth.
- No world-to-display latency (the phone clip from the lab could not be scored).
- The twin covers one viewpoint: the moving scan failed QA (81 s, turns up to 109 deg/s).
- The 35 ms target is not met, and `hardware_verified` stays false until a measured 30-minute run.
- Semantic labels on the lab data are not evaluated; the perception model is trained on synthetic data only.
- The cart is pushed by hand. Planning runs in simulation; no motor is driven.

## 5. Engineering practice

- **Evidence before claims.** The interface shows "Hardware not tested" until a measured run says otherwise; tests
  enforce that every explainer measurement and every landing-page ledger entry and chart bar cites a file in the
  repository.
- **Speed-ups that cannot change results.** Each optimisation carries a test that runs a verbatim copy of the old code
  and demands identical output.
- **A journal of every change.** About fifty cycles in `docs/aide2_tree.jsonl`, each with a hypothesis, a failing
  test first, public and private scores, and a keep-or-revert decision.
- **Local-first CI.** `tools/local_ci.py` runs the Windows and Ubuntu (WSL) suites on the laptop; hash-locked
  dependencies for Windows, Ubuntu and CUDA.
- **Security.** Authenticated sensor transport, strict CSP, leak-scanned public export, no functions on the public site.

## 6. What I would say in three minutes

1. The problem: a lab's 3D geometry and its moving contents, kept current.
2. The rig and the pipeline, pointing at the cart.
3. The studio on the lab recording: orbit, toggle layers, scrub.
4. The results table, reading each number with its condition.
5. The limits, unprompted.
6. Next: a calibrated full-room twin, then a rover that maps and navigates the lab itself.

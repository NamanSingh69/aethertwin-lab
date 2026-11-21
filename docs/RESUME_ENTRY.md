# AetherTwin on a resume

Wording that matches the evidence in the repository (21 Nov 2026). Each bullet lists its source so it survives an
interview question. Check the role line against what you personally did before using it.

## One-page resume (two bullets, replaces the current entry)

**AetherTwin - Lab Digital Twin** | Python, PyTorch, Open3D, FastAPI, React, Three.js | GitHub | Live demo

- Built a LiDAR + RGB-D digital twin of a robotics lab: a Raspberry Pi 5 sensor cart streaming over an authenticated
  protocol to a GPU laptop running deskew, ICP mapping, occupancy, tracking and path planning (30K+ lines Python,
  700+ tests).
- Ran it live on the real rig for 15 minutes (5,510 frames, 140 ms median per frame); in rehearsals on recorded
  data, cut processing time 37% (381 to 240 ms) with speed-ups proven output-identical; shipped a public 3D replay.

## Longer form (three or four bullets, for a robotics or systems role)

- Designed and built an end-to-end perception stack for a Unitree L2 LiDAR and Orbbec depth camera: IMU deskew, ICP
  registration with a pose graph, log-odds occupancy and TSDF mapping, Kalman tracking, risk fields and an A* +
  B-spline planner.
- Wrote a raw-sensor transport protocol (mutual HMAC authentication, per-message MAC, clock sync) that streamed 7,171
  frames over Wi-Fi in 10 minutes with zero loss.
- Built a React + Three.js studio on a binary WebSocket scene protocol, with instanced rendering and streamed map
  tiles; tested end to end with Playwright and automated accessibility checks.
- Reduced median frame time from 381 to 240 ms (rehearsals on recorded data) using optimisations that each ship with a test proving identical
  output; kept a journal of about 50 change cycles with a failing test first.

## Sources

| Claim | Source |
|---|---|
| 15 min live, 5,510 frames, p50 140 ms | `reports/lab/README.md` (LiDAR-only run, 5 Nov 2026) |
| 381 to 240 ms (37%) | `reports/rehearsal/README.md` (rehearsals on recorded data) |
| 7,171 frames, 0 lost, 600 s | `docs/LAB_DAY.md` (bench stream) |
| 30K+ lines Python | 30,736 non-blank lines in tracked `.py` files, counted 21 Nov 2026 |
| 700+ tests | 713 Python tests passed on 21 Nov 2026 (`reports/test_suite/README.md`) |

## Words to avoid, and why

- "0 dropped frames" for the live run: the loop processes the newest sweep and skips older ones by design (about
  half of what the LiDAR sends). What was zero is dropped scene snapshots and transport losses.
- "Accurate to 3.4 cm": 3.4 cm is how well a scan fits the project's own twin, not accuracy against ground truth.
- "Real-time": 140 ms a frame is about 6 frames a second; the 35 ms target is not met.
- "Autonomous robot" or "navigation" in the past tense: planning runs in simulation and the cart is pushed by hand.
  It belongs under future work (`FUTURE_WORK.md`).
- "Sensor fusion" as a result: the camera streams but is not yet calibrated to the LiDAR.

## If asked "what would you do next?"

A calibrated full-room twin with landmark accuracy, then LiDAR-inertial odometry so it localizes while moving, then
motorizing the cart so it reaches a clicked goal while avoiding people, then frontier exploration so it maps the lab
without anyone pushing it.

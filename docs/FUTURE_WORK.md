# Future work: from a pushed cart to a robot that maps the lab by itself

Everything here is planned, not done. Each phase is defined by the measurement that ends it, in the same style as the
results in [`CASE_STUDY.md`](CASE_STUDY.md). What exists today is listed per phase so the gap is visible. The
near-term bench steps are in `docs/LAB_DAY.md`; this document is the longer arc.

## Phase 1: a complete, calibrated twin of the lab

**Why first.** Every later phase localizes against the twin. Today's twin is from one viewpoint and LiDAR-only.

| Step | Exists | To do |
|---|---|---|
| Camera-LiDAR calibration | `tools/calibrate_rig.py`, target page, acceptance gates (2 px, 20 mm, 1 deg) | A target session with the monitor above LiDAR height |
| Moving commissioning scan | `main.py --mode build-twin`, recording QA, pose graph | A slow push (under 60 deg/s) ending on the start mark, with loop closure |
| Coloured, full-room twin | TSDF, Poisson mesh, photo appearance layer | Fuse RGB-D once calibrated |
| Accuracy | `tools/` landmark-span scorer | Tape distances between marked landmarks |
| Latency | Rehearsal tooling | 240 fps phone clip with a trigger line |

**Ends with:** landmark error as percentiles (not a single number), return-to-start error, world-to-display latency,
and a measured 30-minute run so `hardware_verified` can become true.

## Phase 2: localization while moving

**Why.** The current "merge four sweeps, then ICP" window works for a parked rig; while moving it drifted and stalled
in rehearsal. An L2 sweep holds about 5,300 points, too sparse to register frame to frame.

- Replace the fixed window with LiDAR-inertial odometry: an IMU-propagated state (iterated error-state Kalman filter)
  registered against a persistent local map, the approach of FAST-LIO2 and Point-LIO.
- Scan-to-twin relocalization at a low rate to remove drift; gravity from the IMU pins roll and pitch, so the search
  is x, y, z and yaw.
- Cross-check offline on our recordings against an open-source baseline (GLIM or FAST-LIO2) before trusting it live.

**Ends with:** trajectory error (ATE and RPE) on return-to-start loops with taped ground truth, and relocalization
success rate over perturbed starts.

## Phase 3: an autonomous rover that reaches a goal

**Goal.** Click a destination in the studio; the robot drives there and avoids people and obstacles on the way.

| Layer | Exists | To do |
|---|---|---|
| Base | Wheeled cart, ESP32-S3 serial link | Motor driver, wheel encoders, battery; a physical emergency stop that cuts motor power |
| Low-level control | - | Velocity PID on the ESP32, a watchdog that stops the motors when commands stop arriving |
| State estimate | Phase 2 odometry | Fuse wheel odometry with LiDAR-inertial odometry |
| Global plan | A* plus B-spline on the planning grid (simulation) | Run on the real twin, replan on map change |
| Local plan | Occupancy map, risk field with time-to-collision, protected zones | A local controller (DWA or MPPI) that respects the risk field and a speed-and-separation limit |
| Operator | Studio goal picking in simulation | Send goals to the real robot, with a visible stop control |
| Safety | Gateway command limits | Speed cap, geofences, stop on stale data, stop on lost localization |

Bring-up order: wheels off the ground, then tethered at walking pace, then free in an empty room, then with people.

**Ends with:** goal success rate over repeated runs, minimum clearance kept, stopping distance, and stops near people.
AetherTwin stays a research platform, not a safety device.

## Phase 4: self-scanning, and a twin that stays current

- **Frontier-based exploration:** the robot drives to the boundary between known and unknown space until the room is
  covered, choosing viewpoints by expected information gain. No one pushes the cart.
- **Change detection:** repeat patrols compare the live occupancy map with the twin and update what moved (a chair, a
  new bench), keeping people out of the static map.
- **Semantics from a small labelled set of our own data**, since public benchmarks use denser LiDARs.

**Ends with:** coverage against time, and precision and recall of detected changes on staged scenes.

## Phase 5: real time on the GPU

Processing is p50 140 ms live against a 35 ms target. At p50 the stages are localization 48, occupancy 37, perception
and tracking 55 ms (`reports/lab/README.md`); much of that is CPU-side array work.

- Move deskew, voxelization and occupancy updates onto GPU-resident tensors in one process.
- Stage timers with `torch.cuda.Event`; keep the same-output oracle tests for every step.

How close this gets to 35 ms is unknown until it is measured; the research notes hold an unverified 15 to 20 ms
estimate assembled from other projects' published figures.

**Ends with:** per-stage GPU timings on the real rig, with p50, p95, p99 and drops.

## Beyond

Multi-room and multi-floor twins, several robots sharing one twin, and a physical LED halo that shows the robot's
intent to people nearby (firmware exists, LEDs not fitted).

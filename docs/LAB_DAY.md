# Lab day: the real rig, what is done and what is left

The shortest path from the bring-up rig to a measured live run. People do only what needs hands,
eyes or an administrator click; the agent (Claude Code on the operator laptop) does everything else
over SSH. Background: `docs/HARDWARE_SETUP.md` (parts, power, mounting), `docs/RASPBERRY_PI_WINDOWS.md`
(commands, transport, hotspot) and `docs/PHYSICAL_COMMISSIONING.md` (procedure). Measured results
live in [`reports/hardware/pi/`](../reports/hardware/pi/README.md) and
[`reports/hardware/halo/`](../reports/hardware/halo/README.md). `hardware_verified` stays `false`:
device bring-up and one Wi-Fi stream are not a pipeline result.

## Topology (as built)

```
 Unitree L2 (192.168.1.62) ──Ethernet, 100 Mb/s── Pi 5 eth0 (192.168.1.2, static)
 Gemini 336L ──USB 2 cable (480M)── Pi 5
 Pi 5 wlan0 (DHCP, e.g. 192.168.137.37) ~~Wi-Fi~~ laptop Mobile Hotspot (192.168.137.1)
 ESP32-S3 status board ──native USB (COM8)── laptop
 Desktop monitor ──HDMI── Portronics USB-C hub ── laptop   (calibration target only)
 Power: L2 adapter and Pi 3 A wall supply on a floor power strip; cords trail from the cart.
```

The **laptop is the only processing host** (Windows 11, i9-13900H, RTX 4070 Laptop GPU): it runs
`command_center`, the recorder and the agent, and holds the repository. The Pi runs only the
capture node. Both ends use [`config/profiles/hotspot.yaml`](../config/profiles/hotspot.yaml)
(640x400 at 30 fps, zlib depth, JPEG colour, sweep-aligned camera forwarding). The rig is a
hand-pushed robot cart (2WD chassis; its Arduino Uno + L298N stay unpowered, batteries out, never
connected to AetherTwin) carrying the L2, the camera and the Pi.

Wired transport needs a switch and a USB-Ethernet adapter for the laptop (192.168.1.10, the wired
default `config/system_config.yaml`); do it **if those are available**. Not done yet.

## Conventions for every command

Use **`tools/rig.py`** from the laptop's repository root (PowerShell, cmd or Git Bash). It finds the
Pi on the hotspot, remembers it in `.local/rig.json`, and wraps every bench step. `--help` lists all
of them.

```sh
.venv\Scripts\python.exe tools/lab_preflight.py  # laptop side on arrival: GPU env, branch pushed, disk, profiles, key, power, COM8
.venv\Scripts\python.exe tools/rig.py find        # locate the Pi on the hotspot (once per lease)
.venv\Scripts\python.exe tools/rig.py status      # service, power, temperature, LiDAR link, camera, disks, code version
.venv\Scripts\python.exe tools/rig.py push        # ship the committed HEAD to the Pi (--rebuild if native/ changed)
.venv\Scripts\python.exe tools/rig.py node log -n 20
```

**Capture node:** it runs as the systemd service `aethertwin-capture` (enabled; starts on boot once
time sync is reached), with `config/profiles/hotspot.yaml` and `--bringup` set in
`/etc/default/aethertwin-capture`.
- `rig.py node start|stop|restart` drive the service. `node restart --live` drops `--bringup`, and
  is refused until a measured calibration is on both ends.
- `rig.py check --stop-node` stops the service for an exclusive device check and starts it again.
- Recordings go to `C:\Users\<you>\aethertwin_rec\<name>` (about 0.6 GB a minute) and never into git.

**Pi power and temperature:** `rig.py status` shows them; check before and after every long
recording. `0x50000` means under-voltage happened since boot: use the official 5 A supply.

The older `deployment/lab/lab_sync.sh` (`push-pi`, `push-secret`, `pi "command"`) still works. Set
`PI=labpi@<lease>` first.

Every recording's `capture_report.json` is copied to `reports/hardware/pi/capture_report_<link>_<motion>_<seconds>s.json`
(as for the 60 s one) and summarised in `reports/hardware/pi/README.md`, labelled Wi-Fi or wired.

## Done (evidence)

| Step | Result |
|---|---|
| Pi OS, SSH key, passwordless sudo, `pi_bootstrap.sh` packages/sdks/env/native/network/time/inventory | Debian 13 Raspberry Pi OS, Python 3.13.5, native binding imports: [Pi README](../reports/hardware/pi/README.md#host), [`bootstrap.log`](../reports/hardware/pi/bootstrap.log) |
| Hotspot link, Wi-Fi power save off on the Pi's hotspot connection | Pi on 192.168.137.37; set with `nmcli connection modify "<hotspot>" 802-11-wireless.powersave 2`, and the kernel logs `brcmf_cfg80211_set_power_mgmt: power save disabled` at boot |
| L2 through the node's driver, SDK quirks handled, device clock fitted | ~12 sweeps/s of ~84 ms, IMU ~251 Hz: [L2 section](../reports/hardware/pi/README.md#l2-lidar) |
| Gemini 336L over USB 2, after the USB current fix | 640x400 at 29.96 fps: [Camera section](../reports/hardware/pi/README.md#camera) |
| First Pi-to-laptop stream, **Wi-Fi**, parked, 60 s, bring-up | 716 frames, zero loss, 5.17 MB/s: [stream section](../reports/hardware/pi/README.md#first-end-to-end-stream-pi-to-laptop-over-the-wi-fi-hotspot) |
| ESP32-S3 flashed (UART build, then native-USB build on COM8), serial link test | link only, **LEDs not observed** (no ring): [halo README](../reports/hardware/halo/README.md) |
| 600 s stationary stream, **Wi-Fi**, on the bench | 7171 frames, zero loss, clock ±0.9 ms; 19 short under-voltage events on the 3 A charger |
| IMU at rest; parked pipeline re-anchors each sweep | accel bias 0.71 m/s² vertical, gyro bias < 1e-5 rad/s: [`imu_bias_still_60s.json`](../reports/hardware/pi/imu_bias_still_60s.json) |
| Capture node as an enabled systemd service; Pi package lock with hashes | [`requirements-pi.lock`](../deployment/capture_node/requirements-pi.lock); `tools/rig.py node ...` |

## Overnight software changes (2026-11-05, laptop only, no hardware)

Found by running the real bench recordings (`%USERPROFILE%/aethertwin_rec/wifi_stationary_60s`, `_600s`)
through the software for the first time. None of this needs the rig; all of it is in `hw/raw-transport`.

| Change | Why it matters today |
|---|---|
| Deskew accepts sweep overlaps up to 2 ms (`slam_and_twin/imu_deskew.py`) | Real L2 sweeps start up to 0.77 ms before the previous one ends (11-13 % of them). `build-twin` and the live pipeline crashed on the first one. |
| Pose-graph and ICP speed-ups (`core/se3_math.py` batched SE(3), `slam_and_twin/icp_pose_graph.py`) | The twin build took ~1.5 s per sweep on real data (a 600 s scan: 3+ hours). Same maths, evaluated in batches; keyframe geometry cached; at most 3 loop-closure ICPs per scan. |
| `--sweeps-per-scan N` for `build-twin` (`ingestion/sweep_merge.py`) | Registers N consecutive raw sweeps as one denser scan; every return keeps its own time and IMU pose. **Required on the L2:** one sweep per scan drifted 13.7 m on the real stationary recording, four stayed within 42 mm (pose spread of a rig that never moved: static precision, not accuracy). Use 4 with an IMU bias (below); `config/profiles/hotspot_build.yaml` sets both. |
| `live.imu_accel_bias: auto` (`slam_and_twin/imu_static_init.py`) | Estimates the bias along gravity from the cart's first 2 s at rest in each build. Real 600 s data: (0.0012, -0.0010, 0.719) m/s², bench measurement (0.0017, -0.0010, 0.715). Without a bias, 4 merged sweeps smear ~42 mm; with it ~2 mm. |
| Laptop tile budget for builds (`config/profiles/laptop.yaml`) and batched tile writes | The build spent most of its time evicting and re-committing map tiles. |
| A bring-up (uncalibrated) recording builds a LiDAR-only twin | It used to fail at the very end ("Depth integration requires camera calibration"); the report now says `camera_depth_used: false`. |
| `build-twin` prints progress, an ETA and a trajectory check (path, start-to-end, impossible jumps) | A diverged registration is visible at once: the one-sweep build above printed 61 jumps over 0.5 m. |
| **Floor from the tape when the L2 cannot see it** (`--lidar-height`, `mapping.lidar_height_m`) | An upright L2 returns nothing below its horizon. The real bench twin put its "floor" on the ceiling, 1.94 m above the LiDAR, and a synthetic moving build crashed at the floor fit after 16 min. Floors must now lie below the sensor; otherwise the tape-measured height is used along measured gravity (`floor_fit.method: declared_lidar_height`, `measured: false`). |
| **`command_center ... --parked`** for the 30-minute run | Live mode forced mobile odometry; on the overnight replay rehearsal it drifted, stalled and lost localization within ~40 real frames. Parked: localize once against the twin from 4 merged sweeps, then hold the station. |
| Live loop survives bad frames, IMU gaps, reconnects and CPU-starved sync | One exception, one stall or one 1 s sync timeout used to stop live processing for good. |
| Finite camera features for returns beside/behind the camera | Real returns in the camera plane overflowed the projection to NaN and stopped live after ~15 frames. |
| `.venv-gpu` built (torch 2.14.1+cu126, RTX 4070 check passed) | Step 8's command needs it; CPU-only processing was ~1 s per frame. |
| `tools/lab_preflight.py` | One command on arrival for the laptop side (PASS/WARN/FAIL with fixes); `rig.py status` covers the Pi. |
| `tools/recording_qa.py` ([docs/RECORDING_QA.md](RECORDING_QA.md)) | Run after **every** recording: PASS/WARN/FAIL in seconds, with a fix hint, before any long step. |
| `tools/targetless_extrinsics.py` ([docs/TARGETLESS_CALIBRATION.md](TARGETLESS_CALIBRATION.md)) | Camera-LiDAR cross-check and fallback from the parked recording, no target. Never measured, never written into `config/`. |
| `tools/submission_figures.py` (`reports/submission/figures/`) | Re-generates the report figures from today's recordings in ~25 s; then `tools/rehearsal_figure.py <rehearsal or live run folder>` adds `fig6_live_rehearsal.png`. |
| `tools/replay_capture_node.py`, `tools/rehearse_live.py` | Serve a recording over the real transport on loopback and run the real live service against it: the live command rehearsed on this laptop, safely even with several agent sessions. |

Measured on this laptop with five other jobs running: the real 60 s recording builds at about 6 sweeps/s
with `--sweeps-per-scan 4`, so a 600 s commissioning scan (~7200 sweeps) takes roughly 20 minutes.

## Remaining steps, in order

### 1. Rigid mount on the cart (human)

1. Fix the L2 and the camera to **one rigid plate** (or bracket) bolted to the cart, as in
   `docs/HARDWARE_SETUP.md` section 1; mark the forward arrow (+X). From here on nothing on the plate
   may move, or calibration is void. Keep the L2 **upright** (dome up), as the software expects tonight
   (floor from `--lidar-height`, gravity from the IMU). Know its blind spot: it returns nothing below its own
   height, so low obstacles (a box on the floor) are seen only by the camera's depth, in front of the cart.
2. Fix the Pi to the cart so no cable pulls on a sensor; strain-relieve the L2 Ethernet, the camera's
   USB cable and the power cords at the cart. Lay the trailing cords flat on the floor.
3. Tape-measure and send to the agent: LiDAR origin height above the floor; the cart's outer length,
   width and height (including anything on top); the LiDAR's forward/left offset from the footprint
   centre; the camera lens relative to the LiDAR (forward / left / up, cm). Three photos with a ruler.

### 2. Self-mask (human parks the cart, agent records)

Human: cart on a flat floor, nothing else static within 0.8 m of the LiDAR, cords flat, then step
outside 0.8 m and stay still or out of view. Agent (node in bring-up mode):

```sh
.venv\Scripts\python.exe tools/rig.py record self_mask_parked --duration 60 --motion stationary
.venv\Scripts\python.exe tools/suggest_self_mask.py %USERPROFILE%/aethertwin_rec/self_mask_parked --lidar-height <m> --robot-size <L> <W> <H> --lidar-offset <x> <y> --output reports/self_mask/suggestion.json
.venv\Scripts\python.exe tools/imu_bias_from_recording.py %USERPROFILE%/aethertwin_rec/self_mask_parked --output reports/hardware/pi/imu_bias_mounted.json
```

The same parked recording gives the IMU bias in the mounted orientation (step 3). Check it first, and
cross-check the camera pose without a target while the cart is still parked:

```sh
.venv\Scripts\python.exe tools/recording_qa.py %USERPROFILE%/aethertwin_rec/self_mask_parked --purpose self_mask
.venv\Scripts\python.exe tools/targetless_extrinsics.py %USERPROFILE%/aethertwin_rec/self_mask_parked --initial-guess <fwd>,<left>,<up> --output reports/targetless_calibration/self_mask_parked.json
```

The targetless estimate is a plausibility check and a better `--initial-guess` for step 5; it is
labelled `measured: false` and is never a substitute for the board calibration. Put boxes or furniture
1-3 m away at and above LiDAR height in the camera's view: a floor and one wall are degenerate.

Expect few or no boxes: the upright L2 returns nothing below its horizon (bench data: elevations 0-90 deg), so a
cart body mounted below the LiDAR is invisible to it; only parts at or above LiDAR height (cables rising to it, a
mast, a camera mounted higher) appear. On the real bench recording the tool ran in 10 s and correctly suggested no
box. "no persistent body returns above the floor" is then a correct result, not a failure.

The tool only suggests. The agent reviews the boxes and the footprint warnings with the human, then
proposes the `lidar_self_mask` change as a commit for the owner to approve.

### 3. Long stationary recording and IMU bias (**done on the bench**, repeat once mounted if wanted)

- **600 s over Wi-Fi:** done on the table before mounting. 7171 frames, zero losses, clock within
  ±0.9 ms ([Pi README](../reports/hardware/pi/README.md)).
- **IMU at rest:** accelerometer bias (0.0017, -0.0010, 0.7147) m/s², gyro bias below 1e-5 rad/s
  ([`imu_bias_still_60s.json`](../reports/hardware/pi/imu_bias_still_60s.json)), from
  `tools/imu_bias_from_recording.py`.
- **Parked drift:** the parked pipeline now re-anchors every sweep, so the bias cannot drift a parked
  rig.
- **Moving scans:** to apply the bias, set `live.imu_accel_bias` in `config/profiles/hotspot.yaml` to
  the measured vector, or to `auto` (estimated in each build from the first 2 s at rest; start every
  moving recording with the cart parked for 3 s). That is the owner's decision. Without changing the
  profile, a two-line file does it for one build (see step 6).

### 4. Moving recordings (human pushes, agent records)

Human: push the cart slowly (walking pace or less) around the area the cords reach, keeping the
laptop within Wi-Fi range. Agent: `rig.py record wifi_mobile_60s --duration 60 --motion mobile`,
then the same with `wifi_mobile_600s --duration 600`. Watch for new segments,
camera drops and clock jumps; a weak link shows up as `camera_dropped` and segment restarts.
Optionally measure the raw link first with `iperf3` (`docs/RASPBERRY_PI_WINDOWS.md`, hotspot step 2).
Start each push with the cart still for 3 s (the `auto` IMU bias needs it). After each recording:
`tools/recording_qa.py %USERPROFILE%/aethertwin_rec/wifi_mobile_60s --purpose mobile`.

### 5. Calibration with the monitor as the target (human and agent)

Calibration must be measured at **640x400**: the hotspot profile expects
`config/calibration_extrinsics_640x400.json`.

Human:
1. Connect the desktop monitor to the laptop through the Portronics hub's HDMI port; set Windows
   display scaling to 100 % for that monitor at its native resolution.
2. Open [`tools/screen_checkerboard.html`](../tools/screen_checkerboard.html) in a browser on that
   monitor (offline page). Tape-measure the lit picture area and the frame on each side in mm, enter
   them, click **Show target**, check the tape against "across 7 squares", press Esc, click
   **Copy numbers for Claude** and paste the line to the agent (square size, monitor face size).
3. Keep the monitor's face at least 0.3 m from walls and other flat surfaces (the extraction
   searches about that far around the board).

Agent: with the service in bring-up mode, record 300 s:
`.venv\Scripts\python.exe tools/rig.py record calib_targets --duration 300 --motion stationary`.

Human, during the recording: 12–20 holds of ~3 s each, moving between holds. **Keep the whole screen above the
LiDAR's height** (the upright L2 returns nothing below its horizontal plane, so a monitor on the floor is invisible to
it): use a chair, table or stool at or above LiDAR height, and vary height upwards. Swivel or tilt it on its stand so
the board appears near (~1 m) and far
(~2.5 m), left and right, turned 20–40° and tilted, always fully in the camera image. Nobody stands in
front of it; keep at least 0.3 m of air behind it where possible. An LCD may return weakly to the L2:
the agent checks the first holds in the LiDAR cloud before the human does all of them.

Agent, before solving: `tools/recording_qa.py %USERPROFILE%/aethertwin_rec/calib_targets --purpose calibration --cols 7 --rows 5 --square <m>`
counts the holds in seconds (FAIL under 10, WARN under 12); re-record now if it fails. Use the step 2
targetless estimate as `--initial-guess` (it is closer than the tape), and afterwards cross-check the
board result with `tools/targetless_extrinsics.py <parked recording> --initial-guess <...> --compare config/calibration_extrinsics_640x400.json`.

Agent: one command extracts the corners (two passes), solves, applies every acceptance check, and
writes the file only if all pass. It never overwrites without `--force`, which archives the old file.

```sh
.venv\Scripts\python.exe tools/calibrate_rig.py %USERPROFILE%/aethertwin_rec/calib_targets --cols 7 --rows 5 --square <square mm / 1000, from the page> --board-size <face W m> <face H m> --initial-guess <forward,left,up m> --output config/calibration_extrinsics_640x400.json
```

The checks, all required:
- at least 10 accepted holds and 2 held out;
- six-degree observability;
- training RMSE ≤ 2 px and RANSAC inlier share ≥ 0.75;
- held-out median ≤ 2 px and max ≤ 6 px;
- within 0.15 m and 15° of the tape guess;
- the two passes agree within 20 mm and 1°.

A recording that is not from the real capture node is refused (`--rehearsal` writes `measured: false`
instead). Rehearsed on synthetic data shaped like this rig: 14/14 holds, 0.05–0.24° and 3–6 mm
(`tests/test_calibrate_rig.py`; synthetic only). Then:

```sh
PI=labpi@<pi> bash deployment/lab/lab_sync.sh push-secret config/calibration_extrinsics_640x400.json
.venv\Scripts\python.exe tools/rig.py node restart --live
```

Both ends refuse to run if their files differ. Commit the calibration file and its working reports
(`<recording>_calibration/`, the JSON reports only, not the recording) under `reports/` as evidence.
`T_lidar_imu` is not written, so the loaders keep the factory IMU offset.

**If calibration fails and time runs short: LiDAR-only fallback.** Keep the node in bring-up mode (the service
default), record the commissioning scan without `--live`, build the twin as in step 6 (it comes out LiDAR-only:
`lidar_only_reference_eligible`), and run step 8 with `--lidar-only` instead of a calibrated live run. The camera is
not fused; the state reports `validity.calibration_measured: false`. Label every result from it LiDAR-only.
Rehearsed for 10 minutes on recorded bench data with no calibration on either end: no errors, relocalized with a
2.7 cm ICP registration RMSE against the twin (a fit residual, not error against independent ground truth), p50 175 ms a
frame (p95 776 ms, not clean: other jobs ran; the 35 ms target is not met) ([`reports/rehearsal/`](../reports/rehearsal/README.md)).
Demo tiers: [`DEMO.md`](DEMO.md).

### 6. Commissioning by pushing the cart (human and agent)

`docs/PHYSICAL_COMMISSIONING.md` steps 5–8. Human: clear people from the area, push the cart slowly
around it with overlap and back to the start (loop closure), then take 5–10 tape measurements (wall
to wall, bench length, door width). Agent: record with the calibrated node (`rig.py node restart
--live` first, then `rig.py record commissioning_scan --duration 600 --motion mobile --live`; the cart
stands still for the first 3 s), check it with
`tools/recording_qa.py %USERPROFILE%/aethertwin_rec/commissioning_scan --purpose commissioning`, then build with
`.venv\Scripts\python.exe main.py --mode build-twin --config config/profiles/hotspot_build.yaml --input %USERPROFILE%/aethertwin_rec/commissioning_scan --output %USERPROFILE%/aethertwin_twin --lidar-height <LiDAR origin height above the floor, m, from step 1>`
(`hotspot_build.yaml` = hotspot plus `imu_accel_bias: auto` and 4 sweeps per scan, build only;
`--lidar-height` places the floor, which an upright L2 never sees: check `floor_fit` in the build's
`reconstruction.json` says `declared_lidar_height` or a measured floor **below** the sensor). **Do not
build with plain `hotspot.yaml`:** at one sweep per scan the real 60 s bench recording, from a rig that
never moved, drifted 13.7 m and 18 deg (550 s); single ~5200-return L2 sweeps are too sparse to register.
With `hotspot_build.yaml` the same recording built in 183 s and its poses stayed within 42 mm (p95 27 mm)
and 0.39 deg of each other: the stationary noise floor (static precision, or pose jitter), not accuracy and not a moving result. It prints progress and an ETA. Then
check coverage, unknown pockets and loop error, and compare the tape measurements. Then the human
parks the cart where the live run will watch from; the agent relocalizes and sets real start/goal and
zones.

### 7. Halo LEDs (when a ring is available)

Wire the 24-pixel ring to GPIO18 with its own 5 V supply (`docs/HARDWARE_SETUP.md` section 6). Set
`hardware_halo: {enabled: true, port: COM8}`; opening COM8 resets the board, so expect about a second
of "disconnected". The human watches every colour state and the agent records what they saw. No
reflash is needed unless the firmware changes (then only on the human's "flash COM8").

### Lab-morning measurement protocol (about two hours)

Steps 1-8 in one timed plan, arranged so that each recording serves several measurements and the morning gives the
project its first numbers against **independent ground truth** (tape measure, floor marks, a phone clock). Today's
numbers are self-consistency only: 42 mm is the pose spread of a rig that never moved, 2.6-2.7 cm is an ICP residual
against the twin. Before the clock starts: `tools/lab_preflight.py` with no FAIL and `tools/rig.py status` healthy. The
minutes are a plan, not a measurement. Step 7 (halo LEDs) is not in it: no ring has been seen working.

**Floor tape (part of step 1).** A **start mark** where the live run will watch from: tape an L around one wheel and
a line along the cart's side, so the cart can go back to the same place and heading. A **walk line** 6-8 m long,
1-4 m from the LiDAR, with a waypoint every 1 m; a **crossing line** that crosses it near the middle; a **radial line**
straight out from the LiDAR with a mark every 0.5 m; a short **trigger line** 1.5-2 m in front of the cart. Tape each
mark's distance from the start mark and write it down.

| Min | Who | Command or action | Number it produces | If late |
|---|---|---|---|---|
| 0-15 | Team; agent writes the numbers down | Step 1: rigid mount, L2 upright; tape LiDAR height, cart size, LiDAR and camera offsets; three ruler photos. Floor tape as above | `--lidar-height`, `--robot-size`, `--lidar-offset`, the tape `--initial-guess` | Never cut |
| 15-25 | Agent; team out of the room (the L2 sees all around) | Steps 2-3 on the start mark, nobody in the room, boxes 1-3 m away at or above LiDAR height: `tools/rig.py record self_mask_parked --duration 300 --motion stationary`, then `tools/recording_qa.py ... --purpose self_mask`, `tools/suggest_self_mask.py`, `tools/imu_bias_from_recording.py`, `tools/targetless_extrinsics.py` (commands in step 2) | Self-mask suggestion; mounted IMU bias (m/s²); targetless camera pose (`measured: false`), the `--initial-guess` for step 5; the empty-room recording for the false-positive baseline (scored after the lab) | Shorten to 120 s |
| 25-50 | Both | Step 5: `tools/rig.py record calib_targets --duration 300 --motion stationary`, 12-20 holds with the screen above LiDAR height, `tools/recording_qa.py ... --purpose calibration`, `tools/calibrate_rig.py ... --initial-guess <targetless estimate>`, then `push-secret` and `tools/rig.py node restart --live` | An accepted `config/calibration_extrinsics_640x400.json` with its training and held-out px, or a documented failure | **Hard gate at minute 50** (below) |
| 50-62 | Team pushes; agent records | Step 6: `tools/rig.py record commissioning_scan --duration 600 --motion mobile --live` (without `--live` on the fallback); cart still for 3 s at both ends; **the push ends with the cart back on the start mark, same heading**; then `tools/recording_qa.py ... --purpose commissioning` | Twin input; the return-to-start error once built | Never cut |
| 62-85 | Agent builds; team tapes | Agent: `main.py --mode build-twin --config config/profiles/hotspot_build.yaml --input %USERPROFILE%/aethertwin_rec/commissioning_scan --output %USERPROFILE%/aethertwin_twin --lidar-height <m>` (~20 min). Team: landmark spans (below). Optional: a 5-minute phone video for the photoreal layer (`docs/LAB_APPEARANCE.md`) | The build's `Trajectory: ... path P m, start to end E m`: E is the **return-to-start error**; `floor_fit` below the sensor; 6-10 taped spans | Spans: 6 instead of 10. Phone video: cut first |
| 85-92 | Team walks; agent records | The **recorded walk session** below (`tools/rig.py record walks_scripted --duration 360`), then `recording_qa.py`; scored offline later with `tools/score_tracks.py` | False positives per minute, single-walk coverage and ID switches, crossing ID switches, detection range (track-level counts) | Radial walk first, then the crossing |
| 92-97 | Agent | Cart stays on the start mark. Start step 8 (calibrated, or `--lidar-only` on the fallback); wait for `parked_localization` to be accepted; draw a warning zone over the trigger line in the studio | `parked_localization` fitness, RMSE and correction (a fit to the twin, not accuracy) | Never cut |
| 97-127 | Team does the latency trials, then moves normally; agent watches telemetry | The step 8 run (`--duration 1800`): latency trials in minutes 3-10 (below), then people move normally | World-to-display latency, the run's p50/p95 per frame, memory, drops, Pi/GPU temperatures | 10 latency trials, then a 15-minute run (`--duration 900`) |

**Cut order if time runs short:** (1) the phone video; (2) the radial range walk; (3) the empty-room recording down to
120 s; (4) 10 latency trials instead of 20; (5) 6 landmark spans instead of 10; (6) the live run down to 15 minutes
(`--duration 900`), called a 15-minute run: it is not the measured 30-minute run that `hardware_verified` needs. Never
cut the tape heights and floor marks, the calibration gate (take the fallback instead), the push that ends on the start
mark, or the live run itself.

**Calibration gate at minute 50.** If `tools/calibrate_rig.py` has not written an accepted file by minute 50, stop
calibrating: no more holds, no `--force`, no loosened thresholds. Take the **LiDAR-only fallback** at the end of step 5
(node stays in bring-up mode, commissioning recorded without `--live`, step 8 with `--lidar-only`) and label every later
result LiDAR-only. The push, the build and the live run are worth more than another calibration attempt.

**Landmark spans (minutes 62-85, while the twin builds).** Two people with a tape measure. Pick 6-10 spans between
fixed, crisp features **at or above the LiDAR's height**: the L2 sees nothing below it, and the twin's floor comes from
the tape, so floor-level spans test nothing. Good spans: door width at head height, wall to wall at shoulder height,
window frame corners, the top edge of a cupboard, pillar edges. Spread them over the room in different directions, at
least two longer than 3 m. Tape each span **twice** and write on paper: name, from, to, tape 1 and tape 2 in mm; photograph
both ends. The agent copies the sheet into the evidence under `reports/`. Compare after the lab: open
`%USERPROFILE%/aethertwin_twin/mesh.ply` in a point-picking viewer (for example CloudCompare, not part of this
repository), pick the same two features for each span and note the twin distance. Report per span the signed error
(twin minus mean tape) and over all spans n, mean, RMSE and max, next to the spread between the two tape readings. Tape
and point-picking uncertainty are estimates; say so. `tools/score_landmarks.py` does the arithmetic and writes the
submission table:

```powershell
.venv\Scripts\python.exe tools/score_landmarks.py --spans reports/lab/spans.csv --points reports/lab/points.txt --twin %USERPROFILE%/aethertwin_twin --output reports/lab/landmarks.json --markdown reports/lab/landmarks.md
```

`spans.csv` columns: `name,from,to,tape1_mm,tape2_mm[,note]` (comma-separated). Points come in metres in the twin's
frame, as CSV `name,x,y,z`, JSON `{name:[x,y,z]}`, or a CloudCompare point-list export (`label,x,y,z`). Label each picked
point with its feature name. The CloudCompare layout support was written without a real export: check one before the lab.
The tool flags tape pairs that differ by more than 5 mm and reports both all spans and clean spans, plus spans over 3 m.

**Return-to-start error.** The push starts and ends on the start mark with the same heading, so the true start-to-end
distance is close to zero (write down how exactly the cart went back on the mark). The build's `start to end` (also
`commissioning.trajectory.start_to_end_m` in `%USERPROFILE%/aethertwin_twin/reconstruction.json`, next to `path_m`) is
then the return-to-start position error. It is computed after the build's loop closure, which pulls the end towards
the start, so call it the return-to-start error of the built trajectory, position only, not raw drift. The start/end
offset from `tools/recording_qa.py --purpose commissioning` is a screening hint, not this number.

**Recorded walk session (6 minutes, right after the twin build, before step 8), scored offline.** The cart stays on
its start mark and nobody touches the laptop. One raw recording holds all three walks, so `tools/score_tracks.py`
scores them automatically (it replays the recording through the real parked pipeline; no labelling):

```powershell
.venv\Scripts\python.exe tools/rig.py record walks_scripted --duration 360 --motion stationary
.venv\Scripts\python.exe tools/recording_qa.py %USERPROFILE%/aethertwin_rec/walks_scripted --purpose self_mask
```

| Rec. second | Walk | Scored with (`--recording %USERPROFILE%/aethertwin_rec/walks_scripted --twin %USERPROFILE%/aethertwin_twin`) |
|---|---|---|
| 0-60 | Nobody in view (doubles as a second false-positive baseline) | `--mode empty --start-s 0 --seconds 60` |
| 60-180 | **One person** walks the walk line at metronome pace (one waypoint every 2 s), 3 s still at each end; 3 passes | `--mode walk --start-s 60 --seconds 120 --line x1,y1,x2,y2` (walk-line ends picked in the twin like the landmarks) |
| 180-270 | **Two people crossing**: one on the walk line, one on the crossing line, meeting near the crossing point; 5 crossings | `--mode crossing --start-s 180 --seconds 90` |
| 270-360 | **Radial walk**: one person, standing, out along the radial line in 0.5 m steps, 3 s still on each mark | `--mode radial --start-s 270 --seconds 90` |

The empty-room recording from minutes 15-25 gives the main false-positive baseline: `--mode empty` on
`%USERPROFILE%/aethertwin_rec/self_mask_parked`. It is only an honest baseline if that recording is NOT the one the twin
was built from (a twin built from the same data gives 0 by construction: measured 0.0/min in-sample on the bench).
Objects present that day but missing from the twin show up as phantom tracks: on the 600 s bench recording a static
object 0.8 m from the LiDAR gave 1.5 false person tracks a minute by flickering into the pedestrian class
(`reports/lab/` will hold the lab's own numbers). Optional, off by default: `tracking.person_confirm_frames: 3` with
`tracking.person_confirm_window: 5` reports a track as a person only after it was a person in 3 of its last 5 observed
frames, which delays real people by 2 frames (behaviour-changing; C47). On that bench window it gave 1.0 false person
tracks a minute instead of 1.5, and 5 of 8 gave 0.5. Use it only after a walk has been scored with and without it.
The report lists the 20 longest false tracks with their position, so
each one can be explained. Add `--calibration <json>`, or `--lidar-only`, to match the twin. Report the
results as track-level counts from one room, one session and K people (`score_tracks.py` says so in every report),
not as MOTA/HOTA benchmark numbers. The range is for a standing person and does not cover the blind spot below LiDAR
height. Offline replay processes every sweep; the live loop drops older ones, so live tracking can only be equal or
coarser.

**Inside the 30-minute run** (step 8): minutes 0-3 nobody in view (the agent checks `parked_localization`), minutes
3-10 the **latency** trials below, then people move normally. A second phone films the laptop screen for the whole
run; a screen recorder on the laptop would load the run being measured. A raw recording of the run itself (`--record`)
stays off: it would add per-sweep disk writes to the run being measured, and its default budget
(`recording.max_disk_bytes`, 16 GiB) is less than 30 minutes at ~0.6 GB a minute.

**World-to-display latency (phone at 240 fps).** One phone in slow motion (240 fps, ~4.2 ms a frame), held still or on
a stand, with **both** the trigger line and the laptop screen in the shot. Before the first trial, decide which
on-screen change counts as the warning (the virtual halo leaving its clear state, or the track entering the warning
zone) and use the same one for all trials. The person stands 1 m back from the line for 3 s, then walks across at a
normal pace; 20 times. Step through each clip and count frames from the first frame with the foot over the line to the
first frame showing the warning: latency = frames x 1000 / 240 ms. This is world-to-display latency over Wi-Fi
(sensors, transport, processing, browser and screen; resolution about one phone frame plus one screen refresh). It is
not LED latency (no ring is fitted) and not a safety figure.

**After the lab (not in the two hours).**
- Landmark comparison, as above.
- Empty-room false positives, once the live run has ended (one rehearsal per machine):
  `tools/rehearse_live.py --recording %USERPROFILE%/aethertwin_rec/self_mask_parked --twin %USERPROFILE%/aethertwin_twin --duration 300 --open-browser`
  (plus `--calibration config/calibration_extrinsics_640x400.json` if step 5 produced one). Nobody was in the room, so
  every tracked entity that appears is a false positive: report tracks per minute and where they sit. Label it a
  rehearsal on recorded data.
- Figures: `tools/submission_figures.py`, then `tools/rehearsal_figure.py` (overnight table above).

### 8. 30-minute live run (human leaves it alone)

```powershell
.\.venv-gpu\Scripts\python.exe -m command_center --mode live --config config\profiles\hotspot.yaml --twin $env:USERPROFILE\aethertwin_twin --capture-host 192.168.137.37 --device cuda --parked --duration 1800
```

**`--parked`** (new): the cart stays put, so the pipeline localizes the station once against the twin
from the first 4 sweeps (it may be parked up to 1 m / 20 deg off the commissioning end pose), then holds
it every sweep. Without it, live mode runs mobile odometry, which in the overnight rehearsal drifted,
stalled on pose-graph optimisation and lost localization within ~40 real frames. The state's
`parked_localization` block shows fitness, RMSE and the correction; `validity.localization` stays false
until it is accepted. The live loop now skips a failing frame and stops only after 20 in a row
(`frame_errors` in the runtime status), and IMU gaps or reconnects re-seed deskew instead of failing.

**During the run, do not change the source in the studio's source selector.** Re-selecting closes the running
sensor link before opening the new one, so a Pi that does not answer within 15 s ends the run (read-only review,
`command_center/runtime.py` `select_source`; not changed before the lab because the node allows one session).

Live mode refuses to start without `--twin` (the step 6 twin; it must be `live_reference_eligible`).
`.venv-gpu` must exist first (`docs/RASPBERRY_PI_WINDOWS.md`, CUDA lock); without it use
`.venv\Scripts\python.exe ... --device cpu`. Rehearse the whole command first on recorded data with
`tools/rehearse_live.py --recording <dir> --twin <twin> --calibration <json> --duration 600` (loopback replay node +
the real live service, one rehearsal per machine, summary in `%USERPROFILE%/aethertwin_rehearsals/<run>/summary.json`).

Rehearsed overnight for the full 30 minutes on recorded bench data (GPU, `--parked`): no errors, one transport
session (7.58 GB), relocalized with a 2.6 cm ICP registration RMSE against the twin (a fit residual, not error against
independent ground truth), memory flat, ~2.5 processed frames/s (p50 381 ms, p95 570 ms a frame; the 35 ms target is not
met) ([`reports/rehearsal/`](../reports/rehearsal/README.md)). Expect the same on the rig, plus Wi-Fi effects. After
that run, exact (bit-identical or same-geometry) speed-ups (C30-C32, C37-C39) brought the same 10-minute parked rehearsal
to p50 240 ms / p95 296 ms a frame, occupancy 119 -> 51 ms, about 4 processed sweeps a second (`reports/rehearsal/`); still far
from 35 ms.

**Optional, faster:** `--config config\profiles\hotspot_parked_fast.yaml` in the same command (in-memory dynamic map,
planning grid refreshed every 50 frames while parked; same results). Its own 30-minute rehearsal: no errors, p50 275 ms,
p95 350 ms, memory bounded but a sawtooth up to 2.03 GB rather than flat
([`reports/rehearsal/`](../reports/rehearsal/README.md)), measured BEFORE the later speed-ups. Plain `hotspot.yaml` is the safe
default and is now faster (p50 240 ms).

**GPU power state (check before the run; the owner changes it):** the laptop GPU clocks down between live frames. The
camera network takes 3.2 ms back to back but 55-59 ms when called every 0.2 s, the live pace (C43 measurement).
On mains, set Windows power mode to *Best performance*. In the NVIDIA Control Panel (Manage 3D settings, Program
settings), set *Power management mode: Prefer maximum performance* for the venv's `python.exe`. Then rerun a
10-minute `tools/rehearse_live.py` to see what it changes. Untested on this laptop so far. If the setting cannot be
changed, the opt-in fallback is a real GPU load between frames (C46): `perception.gpu_keepalive_ms: 10` with
`gpu_keepalive_matmul: 2048` measured perception 139 -> ~107 ms on recorded frames for about +8 W; a 10-minute rehearsal
with it gave p50 204 / p95 239 ms a frame (240 / 296 ms without, `reports/rehearsal/`). Tiny keep-alive ops
do nothing: the driver raises clocks only under ~35-40 % sustained load. Mains power only.

Cart parked, people moving normally, laptop on mains with sleep disabled. Record acquisition age,
per-frame latency as p50/p95 and per stage against the 35 ms target (not met so far), memory, drops, render frame times and Pi/GPU temperatures.
This is a **Wi-Fi** result for this exact configuration; only then propose a `hardware_status` change.

### 9. Wired transport (if a switch and a USB-Ethernet adapter are available)

L2, Pi and the laptop's adapter (192.168.1.10, `deployment/lab/windows_sensor_adapter.ps1`, run by
the human as administrator) on the switch; node and host with `config/system_config.yaml`. Repeat
steps 3 and 4 and label them wired. Until then every transport result is Wi-Fi.

Also open for the agent: freeze `deployment/capture_node/requirements-pi.txt` into a hash lock from
the Pi's working venv.

## Ready-to-paste prompt for the lab-day agent

```text
You are continuing AetherTwin on the operator laptop (Windows 11, RTX 4070 Laptop GPU), the only
processing host. It holds the repository (private GitHub repo NamanSingh69/aethertwin) and reaches
the Raspberry Pi 5 capture node over its Windows Mobile Hotspot (laptop 192.168.137.1; Pi user
labpi on a DHCP lease such as 192.168.137.37). The Unitree L2 is on the Pi's Ethernet
(192.168.1.62), the Orbbec Gemini 336L on the Pi's USB over a USB 2 cable (640x400 at 30 fps), and
the ESP32-S3 status board on this laptop's native-USB port COM8 (no LED ring yet). The rig is a
hand-pushed cart whose motors are never connected. You have no memory of earlier work.

Read, in order: CLAUDE.md, docs/LAB_DAY.md (this procedure, with what is done), NEXT_AGENT.md (rules),
reports/hardware/pi/README.md and reports/hardware/halo/README.md (measured so far),
docs/RASPBERRY_PI_WINDOWS.md, docs/HARDWARE_SETUP.md and docs/PHYSICAL_COMMISSIONING.md. Follow
NEXT_AGENT.md's conventions exactly: evidence honesty (never claim verification you did not
measure; label every transport result Wi-Fi or wired), every result recorded under reports/ with
devices, serials, versions, settings, date and raw numbers; regenerate SOURCE_MANIFEST.json for
every commit; edit files byte-preserving (never Python's write_text on Windows); one branch and PR
per milestone; never force-push.

Access: SSH with ~/.ssh/aethertwin_ed25519 only, through deployment/lab/lab_sync.sh with
PI=labpi@<lease>. Never type, store or ask for passwords. Ask before flashing the ESP32 (the
human must say "flash" and name the port), before writing any SD card, before editing
/boot/firmware/config.txt, and before any network or firewall change. Keep the sensor network private.

Read "Overnight software changes" in docs/LAB_DAY.md first. Start with `python tools/lab_preflight.py` (laptop) and
`python tools/rig.py status` (Pi). Run tools/recording_qa.py after every
recording, before any long step. Build twins only with config/profiles/hotspot_build.yaml (one sweep per
scan does not register on the L2). Run the live command with --parked for a parked cart. Before each
commit run `python tools/fast_check.py --full` and read the counts; write the manifest with
`python tools/repository_manifest.py --index`. The team's class submission is due at 15:00: if a step
stalls, regenerate tools/submission_figures.py output from what has been recorded and say what is missing.

Continue docs/LAB_DAY.md "Remaining steps" from the first one not done. At each step tell the human
precisely what to do physically (numbered, what they should see), wait for their result, verify on
the device and record it. Keep a local untracked PROGRESS.md. After each milestone report what
changed, the measured numbers with their labels, test/CI status, what remains unverified, and the
PR link. Only after the 30-minute live run, propose a hardware_status change for the exact measured
configuration.
```

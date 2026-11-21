# Recording QA: check every recording before processing it

`tools/recording_qa.py` is a fast quality gate for a recording from `tools/rig.py record`. Run it
right after each recording, before any long step (self-mask, calibration, build-twin). If the
recording is bad you find out in seconds, while the cart and the people are still in place, not
after a long processing run. The tool makes one streaming pass over the chunks with the repo's
reader (`ReplaySource`, which also verifies each chunk's sha256) and reads `capture_report.json`
and `recording_status.json`. It changes nothing: it only reads and reports.

```sh
.venv\Scripts\python.exe tools/recording_qa.py %USERPROFILE%/aethertwin_rec/<name> --purpose <purpose> --json %USERPROFILE%/aethertwin_rec/<name>_qa.json
```

Purposes: `self_mask`, `stationary`, `calibration`, `mobile`, `commissioning`. Each check prints
**PASS**, **WARN** or **FAIL** with the measured value and a one-line fix. Exit code `0` means
everything passed, `1` means at least one warning, `2` means at least one failure.

- **FAIL:** re-record now, after the fix the hint gives.
- **WARN:** read the hint. Go on only if that step does not need what was flagged.

Checks:
- transport and recorder counters (lost clouds, camera or IMU frames, reconnects, clock jumps, recorder drops);
- frame count and rate against the requested duration, sweep-id gaps, and sweep start gaps (overlaps over 2 ms, holes over 50 ms);
- points per sweep and the range distribution;
- IMU rate and IMU coverage of every sweep;
- camera presence and age against the sweep end;
- depth: valid fraction and median depth;
- colour: over- and under-exposure, and blur (variance of the Laplacian);
- motion: 1 s IMU windows plus a coarse LiDAR ICP;
- start/end comparison (loop-closure hint);
- for calibration, a count of still checkerboard holds.

The motion, distance and start/end numbers are screening heuristics, not odometry or loop
closures, and the hold count is an estimate. The step's own tool, for example `calibrate_rig.py`,
is still the authority.

Run time on the bench recordings (Wi-Fi, parked):
- `wifi_stationary_60s`: 15–20 s;
- `wifi_stationary_600s` (5.6 GB): about 3 minutes.

Results: see "Validation" below.

Tests on synthetic recordings: `tests/test_recording_qa.py`.

## Step 2, self-mask

Run this after `rig.py record self_mask_parked --duration 60 --motion stationary`, before
`suggest_self_mask.py`:

```sh
.venv\Scripts\python.exe tools/recording_qa.py %USERPROFILE%/aethertwin_rec/self_mask_parked --purpose self_mask --json %USERPROFILE%/aethertwin_rec/self_mask_parked_qa.json
```

The `cart stayed still` check must PASS. It FAILs if the start-to-end LiDAR offset is over 0.1 m
or 2°, or if the gyro shows a turn. A FAIL means the self-mask boxes and the mounted IMU bias would
both be wrong, so re-record. If it WARNs about shaking at the listed seconds, someone bumped the
cart or a cord was pulled. Re-record if that happened more than once. A high `share_below_0.5m` in
`points per sweep and ranges` is expected here (the cart body) and is what the mask removes.

## Step 3, long stationary recording

Run the same command with `--purpose stationary`. On the bench 600 s recording the `cart stayed
still` check WARNs: 14 one-second accelerometer windows showed bumps, but the LiDAR start/end
offset stayed at 5 mm. That is acceptable for a bias estimate. Bumps throughout the recording are
not.

## Step 4, moving recordings

Run this after each `rig.py record wifi_mobile_60s|wifi_mobile_600s ... --motion mobile`:

```sh
.venv\Scripts\python.exe tools/recording_qa.py %USERPROFILE%/aethertwin_rec/wifi_mobile_60s --purpose mobile --json %USERPROFILE%/aethertwin_rec/wifi_mobile_60s_qa.json
```

Look at:
- `transport losses` and `sweep ids`: a weak link shows up there;
- `cart moved`: it FAILs if the cart was still more than 95 % of the time;
- `max angular rate`: it WARNs above 60°/s, which is too fast for deskew. Turn slower and re-record;
- `colour sharpness`: it WARNs if more than 25 % of frames are blurred.

`path_proxy_m` is a rough distance from a chained ICP. It is useful only to see that the cart
travelled about as far as it was pushed. The start/end hint is for information only for
`mobile`.

## Step 5, calibration targets

Run this after `rig.py record calib_targets --duration 300 --motion stationary`, before
`calibrate_rig.py`. Take `--square` from the checkerboard page:

```sh
.venv\Scripts\python.exe tools/recording_qa.py %USERPROFILE%/aethertwin_rec/calib_targets --purpose calibration --cols 7 --rows 5 --square <square m> --json %USERPROFILE%/aethertwin_rec/calib_targets_qa.json
```

`checkerboard holds` FAILs below 10 holds and WARNs below 12. `hold variety` WARNs if the holds do
not span about 0.8 m in distance and both sides of the image. `camera frames present` must PASS.
The hold count is a sampled estimate. To count with `calibrate_rig.py`'s own detector, add
`--exact-holds`: it is slower, about 0.12 s per frame, so roughly 7 minutes for 300 s. Re-record
with more or longer holds rather than running calibration on a FAIL. Cart motion only WARNs for
this purpose, because the calibration is internal to the rig.

## Step 6, commissioning scan

Run this after `rig.py record commissioning_scan --duration 600 --motion mobile --live`, before
`main.py --mode build-twin`:

```sh
.venv\Scripts\python.exe tools/recording_qa.py %USERPROFILE%/aethertwin_rec/commissioning_scan --purpose commissioning --json %USERPROFILE%/aethertwin_rec/commissioning_scan_qa.json
```

It FAILs if the recording came from the uncalibrated (bring-up) node. It runs the same checks as
step 4, plus the loop hint. `start and end look alike` WARNs when the last 3 s do not look like
the first 3 s at any heading: a voxel overlap below 0.7, or a start-to-end ICP offset over
0.75 m. In that case the scan probably has no loop closure. Push the cart back to the start
point and hold it still for 3 s at both ends. A 600 s scan takes about 3 minutes to check.

## Validation

- `wifi_stationary_60s` (real, Wi-Fi, parked, bring-up): PASS as `self_mask` and as
  `stationary`. 716 frames at 11.95 Hz, zero losses, sweep start gaps −0.77 to +0.75 ms,
  IMU 251 Hz, every sweep covered, camera present on every frame (|age| p95 18 ms), depth valid
  0.72 at a 2.47 m median, Laplacian variance about 1580, start-to-end ICP offset 5 mm / 0.35°.
  As `mobile` it FAILs (`cart moved`), as expected for a parked recording.
- `wifi_stationary_600s` (real, Wi-Fi, on the table, bring-up), `--purpose stationary`: WARN
  (exit 1) in 189 s. Every check PASSed except `cart stayed still`:
  - 7171 frames, zero losses;
  - sweep start gaps −0.76 to +4.72 ms, no overlap over 2 ms and no hole over 50 ms;
  - IMU 251 Hz, max gap 8.3 ms;
  - depth valid 0.74.

  `cart stayed still` WARNed on 14 accelerometer-only "shaking" windows (no gyro turn, max
  1.3°/s) while the start-to-end ICP offset was 5 mm / 0.08°. These look like bumps on the
  table, not a moved rig.

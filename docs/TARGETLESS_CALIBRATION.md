# Targetless camera-LiDAR extrinsics (fallback and cross-check)

`tools/targetless_extrinsics.py` estimates `T_lidar_camera` (Gemini 336L colour optical frame in the Unitree L2
frame) without a target. It registers the camera's median depth cloud to the L2 cloud accumulated over a parked
(stationary) recording. **It is not a board calibration.** The output file always has `measured: false`, method
`targetless_depth_icp` and a `targetless-depth-icp-NOT-MEASURED-...` id, so `load_measured_calibration` rejects it
for live fusion. The tool refuses any output path inside `config/`, so it can never overwrite
`config/calibration_extrinsics_640x400.json`. It also refuses an existing output file unless `--force` is given.

Use it for two things:

- **Cross-check** the screen-checkerboard result from `tools/calibrate_rig.py` with `--compare`.
- **Labelled fallback** if the LCD returns too weakly to the L2 and the board calibration fails. Any figure or
  claim built on this estimate must say it is targetless and unmeasured.

## Lab-day command

Run it on the parked self-mask recording, or on any 30-60 s recording with the cart still and people out of view:

    .venv\Scripts\python.exe tools/targetless_extrinsics.py %USERPROFILE%/aethertwin_rec/self_mask_parked ^
        --initial-guess <fwd>,<left>,<up> --compare config/calibration_extrinsics_640x400.json ^
        --output reports/targetless_calibration/self_mask_parked.json

- `--initial-guess` is the camera's optical centre in the LiDAR frame, measured with a tape (x forward, y left,
  z up, metres). It can also be a JSON file that holds `T_lidar_camera`.
- Leave out `--compare` if no board calibration exists yet.
- If the camera is not facing along LiDAR +x, add `--initial-rpy roll,pitch,yaw` (degrees). If its direction is
  unknown, widen the search, e.g. `--grid-yaw-deg 60 --grid-step 10`.
- A run takes about 1 minute for 60 s of data (716 frames).
- Exit codes: 0 means the verdict is PLAUSIBLE, 1 means any other verdict, 2 means the tool refused to run.

## Reading the result

The verdict is one of PLAUSIBLE, DEGENERATE, POOR_FIT, INCONSISTENT, AMBIGUOUS or IMPLAUSIBLE, followed by the
reasons. The checks behind it:

- **fitness**: the share of co-visible camera points that lie within 5 cm of a LiDAR plane.
- **point-to-plane RMSE**.
- **observability**: the min/max eigenvalue ratio of the normalised point-to-plane Hessian. Below 0.01 the
  verdict is DEGENERATE and the weakest direction is named. A floor plus a single wall leaves translation along
  the wall unobservable.
- **time-block spread**: the full pipeline is re-run on 4 disjoint time blocks. This is the honest uncertainty
  figure. The bootstrap figure is optimistic.
- **rival grid basins**.
- **projection check**: an independent number. Persistent LiDAR returns are projected into the camera and
  compared with the camera depth at each pixel.

Two pictures are written next to the JSON:

- `<output>.estimate.png` shows the projected returns on the RGB frame: green where they agree with the camera
  depth, red where the LiDAR is nearer, blue where it is farther. Look at edges such as pillars, cabinets and
  box corners.
- `<output>.compared.png` shows the same for the `--compare` file.

To check the board result: the board result should agree with the targetless estimate to within the time-block
spread. Its projection check on the same data should not be worse.

## Scene requirements (important)

An upright L2 only returns points above its own horizontal plane. In the bench recording, elevations ran from
about 0 to 90 degrees. Two consequences follow:

- Only the part of the camera view above the LiDAR height can be matched, and the floor never constrains the fit.
- Vertical translation is constrained only by horizontal edges and surfaces above LiDAR height.

So when parking the rig, face it at 2-3 boxes or pieces of furniture 1-3 m away, at or above LiDAR height, at
different angles. A room corner also helps. Avoid a single flat wall. Black leather and glass return poorly.

## Bench result (2026-11-04 recording `wifi_stationary_60s`, sensors loose on a table, not the cart mount)

This recording only shows that the method is plausible. It does not validate the cart mount.

- Depth units: metres (median 2.48 m). Depth is hardware-aligned to colour, and the recorded factory colour
  intrinsics were used.
- Estimate: camera at [0.118, 0.125, 0.119] m from the L2 origin (forward, left, up). Rotation from the nominal
  axes, as roll/pitch/yaw: -2.7 / +0.8 / -35.1 degrees.
- Physical plausibility: the camera was evidently turned about 35 degrees right of LiDAR +x on the table. The
  offset is about 21 cm.
- Fit: 90.7% of co-visible camera points matched, RMSE 16 mm. Eigenvalue ratio 0.016, above the 0.01 limit.
- Starting points: a start at yaw 0 and a start at yaw -30 converged to the same transform (difference 0.0 mm).
- Projection check: 70% of the projected returns agree with the camera depth within 5 cm.
- Verdict: **INCONSISTENT**. The time blocks disagree by up to 29 mm / 1.2 degrees, and two rival basins
  2.8 cm / 1.2 degrees away fit almost as well.
- Honest reading: on this cluttered bench scene the extrinsic is known to about 3 cm / 1 degree, not better.
- With the stricter planarity filter (`--max-curvature 0.05`), the result was DEGENERATE: chairs and people were
  discarded, leaving mostly one wall.

Synthetic validation is in `tests/test_targetless_extrinsics.py`: a ray-cast room with slanted walls and rotated
boxes, starting from a guess perturbed by 10 cm / 5 degrees. The estimate recovers the transform to about 1 mm /
0.05 degrees over 6 seeds. The floor-plus-one-wall scene is flagged DEGENERATE along LiDAR y.

## Limitations

- It is only as good as the overlap between what the LiDAR and the camera see. The result depends on the scene
  and on the density of the accumulated cloud (that dependence is what the time-block spread measures).
- It assumes the sensors are rigid and still for the whole recording, and that the factory colour intrinsics are
  right.
- There is no time-offset or motion handling. Moving people or objects are filtered only partly: by LiDAR voxel
  persistence and by the per-pixel depth median.
- Depth bias at range (Gemini error grows roughly with z squared) can bias the translation. The scale ratio is
  printed as a diagnostic only.

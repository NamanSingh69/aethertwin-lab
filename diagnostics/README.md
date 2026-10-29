# Observed-data diagnostics and isolated virtual checks

`run_diagnostics(config, source='sim', input_path=None, twin_path=None,
output=Path(...), max_frames=8, root=None)` runs the ordinary deployed pipeline
on sensor observations. It returns a JSON-safe report and writes `report.json`,
`report.md` and `integrity.json`. Each output directory is single-use so prior
evidence is preserved. There is no `all_passed` shortcut.

```sh
python -m diagnostics --source sim --max-frames 8 --output artifacts/my_diagnostic
python -m diagnostics --source replay --input assets/sample_recording --output artifacts/replay_diagnostic
python -m diagnostics --source live --twin /path/to/measured_twin --config /path/to/lab.yaml --output artifacts/live_diagnostic
```

The API accepts a configuration dictionary; the standalone CLI reads the same
YAML as the main application. Live mode requires an explicitly chosen twin and
the ordinary measured-calibration/SDK prerequisites. Initialization failures
produce failed evidence and never switch to simulation. There is no firmware,
network, firewall or calibration-promotion operation in this runner.

## Stages and interpretation

- **SDK/device discovery:** Offline modes report SDK import-spec availability
  but mark physical discovery `not_tested`. Only explicitly requested live
  initialization opens configured devices. No unrelated serial ports are probed.
- **Ordinary pipeline:** Actual sensor frames pass through deskew, localization,
  multimodal preparation, the packaged checkpoint, tracking and navigation.
  No simulator labels, object identities or scene mesh are read by the runner.
- **IMU observations:** The report measures rate, timestamp gaps, complete-sweep
  coverage, gyro RMS and specific-force magnitude. A stationary *candidate*
  is descriptive: an IMU cannot distinguish rest from constant translation.
- **Modality coverage:** Missing IMU, LiDAR, calibrated RGB-D, or empty data is
  visible. Calibration identity/measured flags are recorded; their presence
  does not prove calibration accuracy.
- **Floor alignment:** A seeded robust fit uses measured current voxel
  centroids and a gravity-aligned initial world. Its constant-plane Jacobian
  has rank three: roll, pitch and height. Translation in X/Y and yaw remain
  unobservable. Only a 3×3 observable covariance is reported. Degenerate or
  spatially weak support fails; no complete six-degree calibration is invented.
- **Map residuals/repeatability:** Statistics require appropriate independent
  capture provenance. Simulation advances past separate commissioning scans
  before diagnostic captures. External replay requires
  `manifest.metadata.independent_validation: true`; exact reuse of known
  commissioning observations is rejected. Otherwise these stages are
  `not_tested`, not assumed successful. Residuals are registration-conditioned
  distances to the measured map, including dynamics and new obstacles. Later
  floor observations are compared with the first capture's plane using an
  explicit 0.12 m floor-patch selection. These descriptive numbers do not prove
  world-pose accuracy, whole-lab coverage, semantics or extrinsic accuracy.
- **Virtual closed loop:** Separate, clearly marked virtual detections exercise
  both CA/CV filters, identity persistence, geofence entry/exit, relative-motion
  TTC and a blocked standalone route. They never enter the active perception
  stream or map. Before/after active-map digests verify that separation.
- **Independent extrinsic and physical target accuracy:** These remain
  `not_tested` without measured targets/annotations. A passing simulated run
  cannot establish hardware field performance.

Every stage is `passed`, `failed` or `not_tested`. `software_checks_passed`
requires successful source/pipeline, IMU, modality, observable-floor and isolated
virtual checks. Optional map statistics may remain untested when independence
is unknown. `hardware_checks_passed` separately requires physical discovery,
independent extrinsic evidence and physical target accuracy; the latter gates
remain pending in this simulated release.

## Evidence and resource limits

The report records exact source/settings, checkpoint/map hashes, sample counts,
limits, pending real validation and a SHA256 of canonical report JSON excluding
its integrity field. `integrity.json` hashes the delivered JSON and Markdown.
No calibration file or commissioned map is overwritten.

The frame budget is explicit (1–256). Only unique IMUs and bounded current
centroid samples are retained, not full RGB-D frames. Optional
`diagnostics.max_imu_samples` defaults to 100,000 and
`diagnostics.max_points_per_frame` to 2,000. Exceeding the IMU budget fails that
stage; point selection is deterministic and recorded. Existing perception/map
budgets remain active. Commissioning-content fingerprints inspect at most 64
reference frames; larger reference sequences do not receive an unsupported
independence claim.

`tests/test_diagnostics.py` includes real simulated/replayed observations,
live-initialization failure injection, missing modalities/empty inputs,
degenerate floor support, bounded IMU storage, integrity verification and
separate virtual injections. Physical hardware tests remain unverified.

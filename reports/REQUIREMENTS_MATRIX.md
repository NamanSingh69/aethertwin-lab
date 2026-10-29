# Requirement evidence

Current AetherTwin 2 evidence is indexed in ACCEPTANCE.md and release_validation.json.
Final suites: Windows323, Ubuntu322+1skip, browser41+1optional.
The original capabilities are retained; current rig profiles exclude S2.

The supplied `SPEC.md` and the approved implementation plan remain the scope.
“Synthetic verified” means observations or analytic fixtures were exercised;
it does not imply physical acquisition or real-lab accuracy. XML/log files in
this folder identify the exact executed tests. Checkpoint and archive provenance
identify which source and data definitions were used.

| Requirement | Authoritative implementation/evidence | State |
|---|---|---|
| Preserve supplied specification and repository organization | `SPEC.md`, source inventory in `SOURCE_MANIFEST.json` | Delivered |
| Python 3.12, isolated Windows/Ubuntu dependency locks | Hash locks, `requirements-constraints.txt`, environment reports, clean Windows install | Verified |
| SI units, coordinate frames, timestamps, capabilities and calibration identity | `core/contracts.py`, recording manifests, contract/projection tests | Synthetic verified |
| Validated immutable arrays/snapshots and label separation | Sensor/GT/diff/snapshot boundary tests; runtime pipeline accepts no simulator truth | Verified |
| Stable SO(3)/SE(3), composition/interpolation/frame conversion | `se3_math.py`; analytic exponential, adjoint, near-zero/near-pi tests | Verified |
| Deterministic architecture, benches, people, carts, obstacles, cables and foliage | MuJoCo scene generator and seeded physics/raycast tests | Synthetic verified |
| CPU LiDAR/RGB-D rays, occlusion, material echoes, noise and timing | Simulator, per-beam echo and camera tests | Synthetic verified |
| Independent stationary/mobile poses, semantics, flow and correspondences | Clean material pairs before noise; `test_simulator_correspondences.py`; separate example sidecars | Synthetic verified |
| Chunked NPZ recording, calibration metadata and deterministic replay | Recorder/replayer tests; raw frame buffer is chunk-bounded | Verified |
| Observation-only automatic commissioning | Commissioning integration test, measured map and free-space assets | Synthetic verified |
| Official L2 binding and unavailable return metadata | Native SDK build/import evidence; ring never used as echo index | API/build verified; hardware pending |
| Gemini SDK v2 supported depth profiles | Enumerated exact source depth, independent color profiles, SDK D2C pairing | API/unit verified; hardware pending |
| RGB-D output calibration and alignment | Delivered profiles/K/distortion/shape validation; HW and documented SW paths | API/unit verified; hardware pending |
| Legacy S2 serial/UDP/nodding (inactive current rig) | Native binding, calibrated cloud/origin and encoder freshness checks | API/build verified; hardware pending |
| No silent simulation fallback in live mode | Required measured calibration, explicit device/import/profile/sync errors | Verified in failure tests |
| Buffered clock synchronization and missing/dropped diagnostics | Synchronizer/clock tests; raw clocks and exposure gaps retained | Synthetic verified; physical synchronization pending |
| Per-point deskew, carried state and inter-sweep gaps | Pointwise IMU trajectory, per-return origins, covered-gap and no-extrapolation tests | Synthetic verified |
| Distortion-aware painting, bilinear sampling and depth gates | Shared projection/backprojection/painting, round-trip and occlusion tests | Synthetic verified |
| Calibrated RGB-D densification | Shared training/runtime feature path, effective capture pose and dense labels | Synthetic verified |
| FPFH/RANSAC, robust point-plane ICP and loop/pose graph | Registration diagnostics and rigid/moving/degenerate regressions | Synthetic verified |
| Floor alignment and range-adaptive differencing | Elevated scanner-origin and world-translation invariance tests | Synthetic verified |
| TSDF and oriented Poisson reconstruction | Rebuilt observation assets, topology reports and repeated native stability run | Synthetic verified |
| Watertight visual surface distinct from measured collision authority | `default_watertight_mesh.json`, optional inferred wireframe, measured occupancy | Verified |
| Collision-safe hashing and exact DDA | Deliberate collisions, hand-calculated rays, scalar/batched parity and boundary tests | Verified |
| Free/solid/permeable evidence, persistence, decay and serialization | Voxel-map tests and record/replay occupancy parity | Verified |
| Never clear behind final return or from incorrect moving origin | Multi-return and moving-ray regressions | Verified |
| Learned VFE, multiscale PointNet++, local sparse attention | Neural hierarchy/gradient tests and packaged learned weights | Verified |
| Range/camera cross-attention and temporal correlation | Spatial neighbor search, configurable range resolution and missing-modality tests | Verified |
| Ten geometric descriptors | Eight specified descriptors plus eigenvalue sum/height standard deviation, degenerate-neighborhood tests | Verified |
| Camera features and seven-class semantic/flow training | Training CLI, synthetic checkpoint/history and independent held-out evaluation | Verified; canonical checkpoint trained and evaluated |
| Focal/Lovasz, masked flow, rigidity losses | Gradient and loss tests | Verified |
| 60% masked Point-MAE with Chamfer reconstruction | Shared backbone checkpoint/history and pretraining tests/CLI runs | Verified |
| Independent scene/trajectory splits and no runtime labels | Split leakage guards, causal sequences, poisoned-debug-pose tests | Verified |
| Supervised recorded-data fine-tuning | Annotated example, strict sidecar loader and scored fine-tuning smoke run | Synthetic verified |
| CA9/CV6 tracking, uncertainty and gated Hungarian matching | Irregular timing, crossing/occlusion, covariance and appearance tests | Verified |
| Oriented GIoU and complete OBB SAT | Independent convex volumes and rotated crossing boxes with no contained corners | Verified |
| Anisotropic risk, relative-motion TTC and persistent traffic | Directional field, moving protected-object TTC and elapsed-time heatmap tests | Verified |
| Ground and volumetric A*, obstacle/unknown inflation | Blocked/narrow/3D tests and observation-only route integration | Verified |
| Cubic spline/MPPI and swept trajectory checks | Intervening collision, dynamic track, precision and off-center ground regressions | Verified |
| Generate, display, export and simulate trajectories | Safe-hold pipeline, actual numeric CSV plus metadata, viewer callback wiring | Computer-use verified |
| Separate processing/presentation loops and bounded queues | Queue capacity2, immutable snapshots, viewer metrics and shutdown | Verified |
| Open3D mesh/voxels/flow/tracks/geofences/risk/RGB-D/path overlays | Runtime generated-scene export and normal renderer teardown | Computer-use verified |
| OpenCV HUD, traffic, alerts and statistics | Actual snapshot HUD rendering and layer tests | Computer-use verified |
| Pause, layers, track inspection and start/goal controls | Callback/input validation plus integrated viewer startup | Computer-use verified |
| Versioned optional localhost WebSocket telemetry | Strict JSON, infinity handling, bounded queues and actual client/shutdown test | Verified |
| All eight CLI modes and common controls | Windows/Ubuntu CLI evidence, installed-wheel startup and failure tests | Verified without physical live acquisition |
| Explicit sampling/resolution and measured performance | Configuration, checkpoint sampling metadata and `BENCHMARK.md` | Verified |
| Thirty-minute memory/responsiveness run | Full viewer run, active-power clock, process samples and normal exit | Current bounded run verified; see BENCHMARK.md |
| Source, weights, recordings, reconstruction, setup and results | Delivery archive/inventory and README | Delivered; current hashes in release_validation.json and SOURCE_MANIFEST.json |
| Physical devices and real-lab quality marked unverified | README, native reports and release status | Explicit later validation gate |
| No robot actuation, firmware changes or external deployment | Source dataflow simulates accepted paths; no actuator output module | Scope preserved |

Desktop and browser interaction checks are complete. Physical acquisition,
ARM64/GPU runtime and real-lab accuracy remain separate later validation gates.
No automated check substitutes for those measurements.

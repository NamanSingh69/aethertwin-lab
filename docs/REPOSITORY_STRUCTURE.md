# Repository structure and specification mapping

The repository root is the `lidar_lab_digital_twin/` directory requested in
section 4 of `SPEC.md`. GitHub's repository name is AetherTwin; no extra nested
project directory is required after cloning.

```text
aethertwin/
├── SPEC.md                         # Original supplied specification, unchanged
├── PEAK_HIL_AND_UI_SPEC.md          # Supplied addendum
├── AETHERTWIN_*PLAN.md              # Historical approved plans
├── README.md
├── pyproject.toml / setup.py
├── requirements*.txt / *.lock      # Python dependencies and CPU locks
├── config/                         # Configuration and calibration fixtures
├── core/                           # Contracts, SE(3), pipeline, resources
├── ingestion/                      # L2, RGB-D, synchronization, replay, simulation
├── slam_and_twin/                   # Deskew, registration, reconstruction, occupancy
├── perception_ml/                   # Descriptors, models, training, inference
├── tracking_and_navigation/        # Tracking, risk, traffic, trajectories
├── visualization/                  # Open3D, OpenCV, JSON telemetry
├── main.py                         # Unified CLI
├── command_center/                 # Browser API, jobs, private scene gateway
├── web/                            # React/TypeScript/Three.js source and built UI
├── native/                         # Exact ray backend and SDK binding source
├── hardware_halo/                  # ESP32 protocol, virtual halo, firmware
├── diagnostics/                    # Observation and HIL diagnostics
├── maintenance/                    # Provisional preparation/readiness helpers
├── assets/                         # Checkpoints, sample recordings, twin, renderer
├── deployment/                     # Service recipes; NVIDIA edge recipe is legacy
├── tests/                          # Python regression suites
├── tools/                          # Build, verification, packaging and benchmarks
├── reports/                        # Historical and current measured evidence
├── artifacts/                      # Selected reproducible acceptance evidence
├── docs/                           # Architecture, setup, status and contributing
├── references/                     # Source references
├── .github/workflows/              # Repeatable software checks
└── SOURCE_MANIFEST.json             # SHA256 inventory of this Git snapshot
```

The original core folders and entrypoint are retained. Intentional corrections:

- `ingestion/gemini_333l_driver.py` retains the requested filename for import
  compatibility; the active camera target is Gemini 335L.
- The optional `rplidar_s2_driver.py` in the original tree is retained for legacy
  compatibility but excluded from active acquisition/configuration as requested.
  Legacy recorded S2 fields remain readable.
- L2 echo index/count metadata is unavailable in the official point structure;
  multi-return algorithms are exercised with simulation and compatible recordings.
- Added modules support later training, native acceleration, web UI and deployment.

Virtual environments, node_modules, caches, duplicate wheels/archives, local
credentials and temporary/failed runs are excluded. The curated `artifacts/`
allowlist follows `tools/package_delivery.py`; original local files are not deleted.
Bundled checkpoints, sample recordings, reconstructed assets, frontend build and
third-party renderer notices are included so a clone can run the demonstration.

The original generated UI planning reference is preserved in
[command-center-concept.png](images/command-center-concept.png). It is a design
concept, not a screenshot of measured hardware operation.

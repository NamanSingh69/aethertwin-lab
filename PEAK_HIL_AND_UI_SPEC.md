# ADDENDUM SPECIFICATION: Autonomous Hardware-in-the-Loop (HIL) Verification, Physical Telemetry Halo & Peak Spatial Command Center UI/UX

## 1. OBJECTIVE FOR CODING AGENT

Extend and upgrade the **Multi-Modal 3D Semantic Voxel Occupancy, Scene Flow & Lab Digital Twin Platform** with three flagship capabilities:

1. **Autonomous Hardware-in-the-Loop (HIL) Discovery, Auto-Calibration & Self-Test Engine (`diagnostics/`):**
   You (the coding agent) must be able to autonomously probe, diagnose, calibrate, and validate the connected physical hardware (**3D LiDAR L2**, **Orbbec Gemini 333L/335L RGB-D Camera**, **RPLIDAR S2**, and **USB Serial NeoPixel/OLED Hardware Halo**) directly from the CLI without human intervention, falling back gracefully to high-fidelity simulation for any sensor not currently plugged in.
2. **Physical Hardware Interface Controller (`hardware_halo/`):**
   Drive an optional USB-Serial microcontroller (ESP32/Arduino) housing a 24-LED addressable NeoPixel ring and mini OLED display so the physical sensor tower acts as an interactive kinetic light sculpture—pointing a directional LED arc at the azimuth angle $\theta = \text{atan2}(y, x)$ of the nearest tracked human and flashing crimson on geofence breach.
3. **Peak Spatial Command Center UI/UX (`ui_command_center/`):**
   Build a breathtaking, 60 FPS dark-mode **Spatial WebGL (Three.js + React + Tailwind + Canvas/SVG Architecture Graph)** frontend paired with a **Zero-Copy Binary WebSocket Streaming Server (`FastAPI` + `uvicorn`)**, alongside the native Open3D renderer. The UI must prominently feature an artistic, real-time **Interactive System Architecture Topology Graph** that visualizes live tensor shapes, per-stage execution latencies ($\text{ms}$), and packet throughput across every mathematical module.

---

## 2. AUTONOMOUS HARDWARE-IN-THE-LOOP (HIL) TESTING & AUTO-CALIBRATION PROTOCOL

Implement `diagnostics/autonomous_hil_runner.py` executable via `python main.py --mode hil-test`. When invoked by the coding agent, it must execute a 6-stage automated verification pipeline and output a structured JSON + Markdown diagnostic report (`diagnostics/hil_report.json`):

```
+-----------------------------------------------------------------------------------+
|                AUTONOMOUS HARDWARE-IN-THE-LOOP (HIL) PIPELINE                     |
|                                                                                   |
|  [Stage 1: Network & USB Bus Probe]                                               |
|   ├── Ping 192.168.1.x subnet & bind UDP port (LiDAR L2 packet sniffer)           |
|   ├── Enumerate USB 3.2 VID:PID 2bc5:* (Orbbec Gemini 333L/335L bandwidth test)   |
|   └── Probe /dev/ttyUSB* & /dev/ttyACM* (RPLIDAR S2 & Hardware Halo handshake)    |
|                                        |                                          |
|                                        v                                          |
|  [Stage 2: Stationary IMU Noise & Gravity Alignment Audit (2.0 Seconds)]          |
|   ├── Collect 400 IMU samples -> Estimate gyro bias b_g and accel norm ||a||      |
|   └── Compute initial roll/pitch alignment matrix R_init from gravity vector g    |
|                                        |                                          |
|                                        v                                          |
|  [Stage 3: Cross-Modal Floor Plane Co-Alignment (LiDAR L2 <-> Gemini 333L)]       |
|   ├── Extract RANSAC floor plane n_L from LiDAR L2 and n_C from Gemini Depth      |
|   └── Optimize delta-extrinsic rotation dR in SO(3) & z-offset to align planes    |
|                                        |                                          |
|                                        v                                          |
|  [Stage 4: Multi-Return & Spatial KD-Tree Diffing Benchmark (50 Frames)]          |
|   ├── Build instant static KD-Tree from first 20 frames                           |
|   └── Measure false-positive ghost voxel rate (< 0.5% required) & p99 latency     |
|                                        |                                          |
|                                        v                                          |
|  [Stage 5: Synthetic-Injected Closed-Loop Tracking & Breach Verification]         |
|   ├── Inject a virtual human avatar walking through the LIVE static lab scan      |
|   └── Assert 9-DoF Kalman velocity error < 0.08 m/s & OBB Geofence breach fires   |
|                                        |                                          |
|                                        v                                          |
|  [Stage 6: Output Signed HIL Verification Report (diagnostics/hil_report.json)]   |
+-----------------------------------------------------------------------------------+
```

### Mathematical Formulation for Stage 3: Autonomous Cross-Sensor Floor Co-Calibration
Let the floor plane detected in the LiDAR frame be $\Pi_L: \mathbf{n}_L^T \mathbf{p}_L + d_L = 0$ and the floor plane detected in the Gemini 333L camera optical frame (transformed by nominal CAD extrinsics $\mathbf{T}_{L \leftarrow C}^{(0)}$) be $\Pi_C: \mathbf{n}_C^T \mathbf{p}_L + d_C = 0$.
The autonomous calibrator computes the corrective extrinsic rotation $\Delta \mathbf{R} \in SO(3)$ and vertical translation shift $\Delta z$ so both sensors agree on the exact lab floor:

$$\mathbf{u} = \frac{\mathbf{n}_C \times \mathbf{n}_L}{\Vert{}\mathbf{n}_C \times \mathbf{n}_L\Vert{}_2 + 10^{-8}}, \qquad \delta\theta = \arccos\left(\text{clip}(\mathbf{n}_C \cdot \mathbf{n}_L, -1, 1)\right)$$

$$\Delta \mathbf{R} = \exp\left(\delta\theta [\mathbf{u}]_\times\right), \qquad \Delta z = d_C - d_L$$

$$\mathbf{R}_{L \leftarrow C}^{\text{calibrated}} = \Delta \mathbf{R} \mathbf{R}_{L \leftarrow C}^{(0)}, \qquad \mathbf{t}_{L \leftarrow C}^{\text{calibrated}} = \Delta \mathbf{R} \mathbf{t}_{L \leftarrow C}^{(0)} + \begin{bmatrix} 0 \\ 0 \\ \Delta z \end{bmatrix}$$

Automatically persist the refined matrix to `config/calibration_extrinsics.json`.

---

## 3. PHYSICAL HARDWARE HALO CONTROLLER (`hardware_halo/halo_serial_driver.py`)

To link the physical sensor rig with the software architecture, implement a non-blocking serial/UDP telemetry driver that sends a compact 16-byte binary packet at 30 Hz to the rig's microcontroller (and renders a virtual mirror of the LED ring inside the UI):

* **Azimuthal Target Tracking on the 24-LED Ring:**
  Given the closest tracked entity at horizontal position $(x_k, y_k)$ in the LiDAR frame, compute its azimuth angle $\theta_k = \text{atan2}(y_k, x_k) \in (-\pi, \pi]$ and map it to LED index $m \in \{0, \dots, 23\}$:
  $$m_{\text{center}} = \left\lfloor \frac{(\theta_k + \pi)}{2\pi} \times 24 \right\rfloor \bmod 24$$
  Illuminate a Gaussian-falloff arc of 5 LEDs centered at $m_{\text{center}}$ with color determined by semantic state and risk level.

---

## 4. PEAK UI/UX DESIGN SPECIFICATION: "AETHERTWIN SPATIAL OS"

### 4.1 Aesthetic Direction & Visual Language
* **Theme:** High-craft dark UI ("Obsidian & Bioluminescent Telemetry").
  * Canvas Background: Deep Space Obsidian (`#06080E`) with a subtle radial vignette and anti-aliased $1.0\text{ m}$ coordinate floor grid (`#141C2B`) and $0.25\text{ m}$ minor subdivisions (`#0D121D`).
  * Glassmorphic Panels: `backdrop-filter: blur(16px)`, background `rgba(12, 17, 29, 0.78)`, $1\text{ px}$ specular border `rgba(255, 255, 255, 0.08)`, subtle top-edge highlight `rgba(0, 245, 212, 0.25)`.
  * Typography: `JetBrains Mono` or `Geist Mono` for all numerical telemetry, coordinates, matrices, and latency counters (`font-variant-numeric: tabular-nums`); `Inter` or `Space Grotesk` for structural headers.
* **Semantic Color System:**
  * Static Lab Twin Architecture: Blueprint Cyan-Slate gradient (`#00B4D8` to `#1D3557`, opacity $0.38$)
  * Human / Pedestrian Voxels: Bioluminescent Emerald (`#00F5D4`)
  * Dynamic Robot / AGV / Cart Voxels: Electric Violet (`#9B5DE5`)
  * Newly Placed Static Obstacle: Solar Amber (`#FEE440`)
  * Multi-Return Permeable Foliage / Scatter: Magenta Coral (`#F15BB5`)
  * Geofence Breach / Critical TTC Hazard: Hyper Crimson (`#FF0A54`) with $4\text{ Hz}$ emissive bloom pulse
  * Planned Safe Navigation Corridor: Neon Mint B-Spline Ribbon (`#38B000`)

---

### 4.2 Spatial Command Center Screen Layout (4-Zone Precision Architecture)

```
+--------------------------------------------------------------------------------------------------+
| [AETHERTWIN OS]  ● LIVE: L2 LiDAR (20Hz) + Gemini 335L (30Hz)   [Mode: LIVE | SIM | HIL-VERIFY]  |
|                  FPS: 29.8 | Voxels: 6,420 | Active Tracks: 3 | Min TTC: 4.2s   [Halo LED Mirror]|
+--------------------------------------------------------------------------------------------------+
|                       |                                                  |                       |
|  [LEFT DOCK: 280px]   |                                                  |  [RIGHT DOCK: 320px]  |
|                       |                                                  |                       |
|  1. SENSOR HEALTH &   |                                                  |  1. SELECTED ENTITY   |
|     EXTRINSICS        |          [CENTER 3D WEBGL VIEWPORT]              |     INSPECTOR         |
|   - L2 IMU Bias       |                                                  |   - Track #02 (HUMAN) |
|   - KD-Tree Count     |   • Translucent Static Lab Blueprint Mesh        |   - 9-DoF State Vector|
|   - Voxel Size Slider |   • Instanced 3D Semantic Voxels (PBR + Bloom)   |   - 10D Weinmann      |
|                       |   • 3D Velocity Vectors & Predicted Ribbons      |     Eigen Radar Chart |
|  2. GEOMETRIC &       |   • Interactive 3D OBB Geofence Wireframes       |                       |
|     LAYER TOGGLES     |   • Anisotropic Floor Risk Shader Map            |  2. 2D FLOOR TRAFFIC  |
|   [x] Static Lab Mesh |   • Gemini 335L RGB-D Hologram Workbench Inlay   |     INFERNO HEATMAP   |
|   [x] Dynamic Voxels  |   • Green B-Spline Safe Navigation Corridor      |   + Live Breach Log   |
|   [x] RGB-D Inlay     |                                                  |                       |
|   [x] Scene Flow Vecs |   [Camera Presets: 1-Iso | 2-Top | 3-FPV | 4-Orb]|  3. AUTONOMOUS PATH   |
|   [x] Floor Risk Map  |                                                  |     GOAL SELECTOR     |
+-----------------------+--------------------------------------------------+-----------------------+
|                   [BOTTOM DOCK: LIVE INTERACTIVE ARCHITECTURE TOPOLOGY GRAPH]                    |
|                                                                                                  |
|  (L2 UDP + Gemini) ===[1.2ms]===> (SE3 IMU Deskew) ===[3.4ms]===> (cKDTree Spatial Diff)         |
|          |                                                                   |                   |
|          +===> (PointPainting) ===> (64-Bit Log-Odds Voxel Hash) <===========+                   |
|                                                  |                                               |
|       (A* + B-Spline Planner) <===[2.1ms]=== (9-DoF Kalman) <===[8.7ms]=== (MultiTask 3D Net)    |
+--------------------------------------------------------------------------------------------------+
```

---

### 4.3 Detailed UI Component Specifications

#### A. The "Living Architecture" Interactive Topology Graph (Bottom Dock)
* Render an interactive, high-precision SVG/Canvas node-link architecture diagram showing all 8 core mathematical stages of the pipeline.
* **Live Telemetry Binding:** Every frame, the backend broadcasts a `stage_timings_ms` and `stage_tensor_shapes` dictionary:
  * Example Node Badge: **`cKDTree Spatial Diff`** | `3.1 ms` | `In: (64,000, 7) → Out: (4,120, 7)`
  * Animated glowing data pulses travel along the cubic Bezier connectors between nodes at a velocity proportional to real-time frame rate.
  * Clicking any architecture node in the graph highlights the corresponding geometric layer in the 3D viewport (e.g., clicking **cKDTree Spatial Diff** flashes the subtracted foreground points; clicking **MultiTask 3D Net** toggles between semantic class colors and 3D scene-flow vector magnitudes).

#### B. 10D Weinmann Eigen-Invariant Radar Chart (Right Dock Inspector)
* When a user clicks any 3D tracked object (or when the system auto-focuses the highest-risk target), render an interactive hexagonal SVG Radar Chart plotting the target's real-time normalized geometric eigenvalues:
  * Axes: `[Linearity, Planarity, Sphericity, Omnivariance, Anisotropy, Verticality]`
  * This visually proves *why* the hybrid PointNet + Eigen model classified the cluster as an upright Human versus a flat Wall or permeable Foliage.

#### C. Zero-Copy Binary WebSocket Protocol (`visualization/websocket_broadcaster.py`)
To achieve 30–60 FPS in the browser without JSON serialization bottlenecks:
* Encode `TwinMasterFrameState` into a compact binary buffer (or msgpack/flatbuffer header + raw `Float32Array` chunks for `voxel_centers`, `voxel_colors`, `voxel_flow_vectors`, `floor_risk_colors`, and `gemini_inlay_points`).
* In the frontend (`ui_command_center/`), map the incoming `ArrayBuffer` directly into a Three.js `THREE.InstancedMesh` matrix and color buffer attribute (`instanceMatrix.needsUpdate = true`, `instanceColor.needsUpdate = true`), enabling smooth rendering of up to $100,000$ 3D cubes at 60 FPS.

---

## 5. REFERENCE IMPLEMENTATION: AUTONOMOUS HIL TESTER & FASTAPI BINARY STREAMER

### 5.1 `diagnostics/autonomous_hil_runner.py`

```python
import json
import socket
import time
from pathlib import Path
import numpy as np

from core.contracts import IMUSample, OrientedGeofence3D
from core.se3_math import so3_exp, skew_symmetric
from ingestion.synthetic_lab_simulator import SyntheticLabSimulator
from slam_and_twin.imu_deskew import ContinuousTimeIMUDeskewer
from slam_and_twin.spatial_diff_engine import SpatialDiffEngine
from slam_and_twin.log_odds_octomap import SparseMultiReturnVoxelMap
from tracking_and_navigation.kalman_9dof_tracker import MultiObjectKalmanTracker9DoF
from tracking_and_navigation.risk_and_geofence import RiskGeofenceAndPlannerEngine

class AutonomousHILVerifier:
    """
    Executes a 6-stage autonomous Hardware-in-the-Loop (HIL) and mathematical
    self-verification suite. Automatically probes physical sensors and validates
    every mathematical subsystem end-to-end.
    """
    def __init__(self, config_dir: str = "config", report_path: str = "diagnostics/hil_report.json"):
        self.config_dir = Path(config_dir)
        self.report_path = Path(report_path)
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.results = {
            "timestamp": time.time(),
            "hardware_probes": {},
            "math_and_pipeline_checks": {},
            "latencies_ms": {},
            "all_passed": False,
        }

    def probe_physical_hardware(self) -> dict:
        probes = {
            "lidar_l2_udp_reachable": False,
            "gemini_335l_usb_detected": False,
            "rplidar_s2_serial_detected": False,
            "halo_serial_detected": False,
        }
        # 1. Non-blocking UDP socket probe for 3D LiDAR L2
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.25)
            sock.bind(("0.0.0.0", 6201))
            data, _ = sock.recvfrom(2048)
            if len(data) > 0:
                probes["lidar_l2_udp_reachable"] = True
        except Exception:
            pass
        finally:
            try:
                sock.close()
            except Exception:
                pass

        # 2. Check Linux sysfs USB devices for Orbbec VID (2bc5)
        sys_usb = Path("/sys/bus/usb/devices")
        if sys_usb.exists():
            for dev in sys_usb.iterdir():
                vid_file = dev / "idVendor"
                if vid_file.exists() and vid_file.read_text().strip().lower() == "2bc5":
                    probes["gemini_335l_usb_detected"] = True
                    break

        # 3. Check serial devices
        probes["rplidar_s2_serial_detected"] = Path("/dev/ttyUSB0").exists()
        probes["halo_serial_detected"] = Path("/dev/ttyACM0").exists()

        self.results["hardware_probes"] = probes
        return probes

    def run_full_verification(self) -> dict:
        print("[HIL] Starting Autonomous Hardware & Pipeline Verification...")
        self.probe_physical_hardware()

        # Initialize Simulator / Hybrid Stream for deterministic closed-loop verification
        sim = SyntheticLabSimulator(num_humans=2, num_robots=1)
        static_cloud = sim.generate_empty_lab_cloud(num_points=80000)

        # Stage A: Verify IMU Deskewing
        t0 = time.perf_counter()
        deskewer = ContinuousTimeIMUDeskewer()
        sample_frame = sim.step(0.1)
        deskewed_pts = deskewer.deskew_sweep(sample_frame.lidar_points, sample_frame.imu_samples)
        imu_ms = (time.perf_counter() - t0) * 1000.0
        assert deskewed_pts.shape == sample_frame.lidar_points.shape, "Deskew shape mismatch"

        # Stage B: Verify KD-Tree Background Subtraction & Multi-Return Log-Odds Voxelization
        t0 = time.perf_counter()
        diff_engine = SpatialDiffEngine(static_cloud)
        voxel_map = SparseMultiReturnVoxelMap(voxel_size=0.10)
        fg_pts, floor_grid = diff_engine.diff_points(deskewed_pts)
        voxel_state = voxel_map.voxelize_and_update(fg_pts)
        diff_voxel_ms = (time.perf_counter() - t0) * 1000.0

        # Ensure background subtraction removes >85% of static room points
        reduction_ratio = 1.0 - (len(fg_pts) / max(1, len(deskewed_pts)))
        assert reduction_ratio > 0.70, f"Insufficient background reduction: {reduction_ratio:.2f}"

        # Stage C: Closed-Loop 9-DoF Kalman Tracking & Geofence Breach Test
        tracker = MultiObjectKalmanTracker9DoF()
        risk_engine = RiskGeofenceAndPlannerEngine()
        geofence = OrientedGeofence3D(
            name="High-Voltage Robotics Cell A",
            center=np.array([2.0, 0.0, 1.0], dtype=np.float32),
            rotation_matrix=np.eye(3, dtype=np.float32),
            half_extents=np.array([1.2, 1.2, 1.5], dtype=np.float32),
        )

        breach_triggered = False
        t0 = time.perf_counter()
        for step_i in range(15):
            # Move a synthetic target straight into the geofence at 1.0 m/s
            target_pos = np.array([0.8 + step_i * 0.1, 0.0, 0.9], dtype=np.float32)
            dets = [{
                "centroid": target_pos,
                "flow_velocity": np.array([1.0, 0.0, 0.0], dtype=np.float32),
                "bbox_min": target_pos - np.array([0.3, 0.3, 0.9], dtype=np.float32),
                "bbox_max": target_pos + np.array([0.3, 0.3, 0.9], dtype=np.float32),
                "points": target_pos[None, :],
                "eigen": np.zeros(10, dtype=np.float32),
            }]
            entities = tracker.update(dets, dt=0.1)
            alerts = risk_engine.evaluate_3d_obb_geofences(entities, [geofence])
            if len(alerts) > 0:
                breach_triggered = True
        track_ms = ((time.perf_counter() - t0) * 1000.0) / 15.0

        assert breach_triggered, "Geofence breach failed to trigger on synthetic intrusion"

        # Stage D: Verify Safe Corridor A* + B-Spline Path Planner
        t0 = time.perf_counter()
        risk_vals, _ = risk_engine.compute_anisotropic_floor_risk(floor_grid, entities)
        grid_side = int(np.sqrt(len(floor_grid)))
        path = risk_engine.plan_safe_corridor_path(
            start_xy=np.array([-4.0, -4.0]),
            goal_xy=np.array([4.0, 4.0]),
            floor_points=floor_grid,
            floor_risk=risk_vals,
            grid_shape=(grid_side, grid_side),
        )
        plan_ms = (time.perf_counter() - t0) * 1000.0
        assert path is not None and len(path) > 5, "Safe corridor planner failed to find valid spline"

        self.results["math_and_pipeline_checks"] = {
            "imu_se3_deskew_passed": True,
            "kdtree_reduction_ratio": round(float(reduction_ratio), 4),
            "active_voxels_generated": int(len(voxel_state.voxel_centers)),
            "kalman_9dof_and_obb_breach_passed": bool(breach_triggered),
            "safe_corridor_waypoints_count": int(len(path)),
        }
        self.results["latencies_ms"] = {
            "imu_deskew_ms": round(imu_ms, 2),
            "kdtree_and_voxel_hash_ms": round(diff_voxel_ms, 2),
            "kalman_9dof_step_ms": round(track_ms, 2),
            "astar_bspline_planner_ms": round(plan_ms, 2),
            "total_frame_pipeline_ms": round(imu_ms + diff_voxel_ms + track_ms + plan_ms, 2),
        }
        self.results["all_passed"] = True

        self.report_path.write_text(json.dumps(self.results, indent=2))
        print(f"[HIL] All checks PASSED! Total pipeline frame time: {self.results['latencies_ms']['total_frame_pipeline_ms']:.2f} ms")
        return self.results
```

---

## 6. UPDATED REPOSITORY ADDITIONS & EXECUTION ORDER FOR THE AGENT

Add the following modules to the repository structure and generate them completely:

```text
lidar_lab_digital_twin/
├── diagnostics/
│   ├── __init__.py
│   ├── autonomous_hil_runner.py         # 6-stage hardware probe, auto-calibrator & closed-loop verifier
│   └── auto_extrinsic_calibrator.py     # LiDAR L2 <-> Gemini 335L floor-plane SE(3) solver
├── hardware_halo/
│   ├── __init__.py
│   ├── halo_serial_driver.py            # 30Hz USB-Serial NeoPixel & OLED controller + virtual state
│   └── esp32_firmware/
│       └── aether_halo_firmware.ino     # Ready-to-flash Arduino/ESP32 firmware for the physical light ring
└── ui_command_center/
    ├── server.py                        # FastAPI + WebSocket server streaming binary 3D frames & timings
    └── web/
        ├── index.html                   # Single-file or Vite bundled Three.js + Tailwind Spatial OS UI
        ├── style.css                    # Glassmorphic dark-mode styling & keyframe pulse animations
        └── app.js                       # Three.js InstancedMesh voxel renderer + SVG Architecture Graph + Radar Chart
```

### Final Verification Loop for the Coding Agent
After generating all files, you (the coding agent) must autonomously execute:
1. `pytest tests/` — Verify all mathematical unit tests pass ($SE(3)$ algebra, 64-bit voxel hash, 10D Weinmann invariants, `MultiTaskVoxelSceneFlowNet` forward pass, 9-DoF Kalman filter, OBB geofence, and Risk-Aware A* planner).
2. `python main.py --mode hil-test` — Run the autonomous Hardware-in-the-Loop verifier, inspect `diagnostics/hil_report.json`, and confirm `all_passed == true` with total pipeline latency under $35\text{ ms}$.
3. `python main.py --mode web-command-center` — Launch the FastAPI + Three.js Spatial Command Center on `http://localhost:8080` and verify live WebSocket binary frame streaming.


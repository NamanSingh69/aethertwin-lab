// Explainer content, shared by the studio's explainer page and the standalone
// shareable build. Every number here is a recorded measurement with its source
// file and where it was measured; nothing is a hardware or accuracy claim.
import type { LayerName } from "../lib/types";

export type Audience = "simple" | "engineer";
export type LabId = "deskew" | "logodds" | "shape" | "risk";

export interface Chapter {
  id: string;
  short: string;
  title: string;
  /** Plain-language explanation with an analogy. */
  simple: string;
  /** What the implementation actually does, for engineers. */
  engineer: string;
  problem: string;
  approach: string;
  /** Repository paths that implement the chapter. */
  code: string[];
  lab?: LabId;
  /** Studio view that shows this stage: layers to enable and a camera preset. */
  studio?: { layers: LayerName[]; view: "isometric" | "top" | "sensor" };
}

export const ONE_LINER =
  "AetherTwin turns a room into a live 3D copy on your screen, using a laser scanner and a depth camera, so you can see what is there, what is moving, and where a robot can safely go.";

export const PITCH: Record<Audience, string> = {
  simple:
    "Imagine a live map of one room that updates many times a second. Sensors scan the space; the software builds a 3D model, follows people and objects as they move, works out which areas are risky, and plans safe paths. You watch it all in a web browser, and a small ring of lights on the device shows whether everything is healthy.",
  engineer:
    "A perception stack for indoor spaces: LiDAR and RGB-D capture on a Raspberry Pi feed a GPU workstation that runs IMU motion compensation, a log-odds voxel map, a sparse multimodal point network with geometric descriptors, constant-acceleration Kalman tracking, a directional risk field with exact time-to-collision, and an A* plus B-spline planner. A FastAPI service streams a binary scene protocol to a React and Three.js studio.",
};

export const CHAPTERS: Chapter[] = [
  {
    id: "overview",
    short: "Big picture",
    title: "A living 3D copy of a physical space",
    simple:
      "Think of an air-traffic-control screen, but for one room. Instead of planes there are people, carts and robots, and instead of radar there is a spinning laser and a depth camera. The goal is that robots and people can share the space without surprises.",
    engineer:
      "Each frame flows through ingestion, deskew, occupancy mapping, perception, tracking, risk and planning. The service publishes the result over a versioned binary WebSocket protocol; the studio renders it with GPU instancing and streams map tiles on demand.",
    problem:
      "Robots usually react only when something is already close, and a single sensor misses things in glare, darkness or behind other objects.",
    approach:
      "Combine two kinds of sensors into one shared 3D model, predict where moving things are going, and plan around them early.",
    code: ["core/pipeline.py", "command_center/service.py", "web/src/scene/SpatialRenderer.ts"],
    studio: { layers: ["map", "voxels", "tracks", "path", "geofences"], view: "isometric" },
  },
  {
    id: "sensors",
    short: "Sensors",
    title: "A laser scanner plus a depth camera",
    simple:
      "The laser scanner is like a bat's sonar: it measures distance very well but sees no colour. The depth camera is like our eyes: it sees colour and shape but struggles in glare and at range. Together they cover each other's blind spots.",
    engineer:
      "Planned rig: a Unitree 4D LiDAR L2 (with its built-in IMU) and an Orbbec Gemini 335L RGB-D camera on a Raspberry Pi 5, streaming to the workstation. Packets are timestamped and buffered in bounded queues so a slow stage drops old data visibly instead of growing memory.",
    problem: "One sensor alone fails in predictable conditions.",
    approach: "Fuse two sensor types in one timeline, with bounded buffers and visible drop counters.",
    code: ["ingestion/", "native/"],
    studio: { layers: ["map", "rgbd"], view: "sensor" },
  },
  {
    id: "deskew",
    short: "Un-blurring",
    title: "Removing motion smear from laser scans",
    simple:
      "A laser scan takes a fraction of a second to sweep around. If the sensor moves during the sweep, straight walls come out bent, like a photo taken while walking. A motion sensor records how the device moved, so every dot can be put back where it really was.",
    engineer:
      "ContinuousTimeIMUDeskewer integrates gyro and accelerometer readings into a pose for each point's timestamp (SO(3) exponential map for rotation), then transforms each point by its own pose instead of one pose per sweep.",
    problem: "Motion during a sweep bends straight surfaces.",
    approach: "Give every point its own sensor pose from IMU integration.",
    code: ["slam_and_twin/imu_deskew.py", "core/se3_math.py"],
    lab: "deskew",
    studio: { layers: ["map", "voxels"], view: "sensor" },
  },
  {
    id: "mapping",
    short: "Mapping",
    title: "A map that keeps score of what it has seen",
    simple:
      "The room is divided into small cubes. Every time a laser beam passes through a cube, that cube gets a point for 'empty'; every time a beam stops in a cube, it gets points for 'solid'. Old evidence slowly fades, so a chair that was moved stops showing up.",
    engineer:
      "A log-odds voxel map (0.18 m cells by default): free cells along each ray lose 0.4, hit cells gain 0.85 (0.25 for see-through surfaces), values are clamped to ±4 and decay by 0.96 per 0.1 s. Cells are keyed by integer coordinates; a 64-bit prime hash only buckets them.",
    problem: "Single scans are noisy and the world changes.",
    approach: "Accumulate evidence per cell with ray casting, clamping and decay.",
    code: ["slam_and_twin/log_odds_octomap.py", "slam_and_twin/tiled_map.py"],
    lab: "logodds",
    studio: { layers: ["voxels"], view: "top" },
  },
  {
    id: "perception",
    short: "Understanding",
    title: "Telling a wall from a pole from a person",
    simple:
      "Even in the dark you can tell a flat wall from a thin pole by touch. The software does the same with the 3D dots around each point: are they spread flat, along a line, or in a blob? A neural network combines those shape clues with camera colour to label what each part of the scene is.",
    engineer:
      "Ten eigenvalue descriptors per point (linearity, planarity, scattering, omnivariance, anisotropy, eigenentropy, curvature, verticality, eigenvalue sum, height spread) from 16-neighbour covariances feed a sparse multimodal PointNet++-style network with camera, range-image and voxel encoders.",
    problem: "Raw points carry no meaning.",
    approach: "Describe local shape geometrically, then classify with a learned model.",
    code: ["perception_ml/eigen_descriptors.py", "perception_ml/hybrid_3d_network.py"],
    lab: "shape",
    studio: { layers: ["voxels", "tracks"], view: "isometric" },
  },
  {
    id: "tracking",
    short: "Tracking",
    title: "Following things as they move",
    simple:
      "Once something is recognised, the system keeps a running guess of where it is, how fast it is going and whether it is speeding up, like a goalkeeper reading a ball. Each new scan corrects the guess.",
    engineer:
      "Constant-acceleration Kalman filters (nine states: position, velocity, acceleration) with gated, multi-cue Hungarian association; a constant-velocity model is also available. Scene flow supplies displacement between frames.",
    problem: "Detections arrive as unconnected snapshots.",
    approach: "Associate detections over time and estimate motion with uncertainty.",
    code: ["tracking_and_navigation/kalman_9dof_tracker.py"],
    studio: { layers: ["tracks", "flow"], view: "isometric" },
  },
  {
    id: "risk",
    short: "Risk",
    title: "Safety bubbles that stretch with speed",
    simple:
      "Around every moving person there is an invisible bubble of caution. When they walk faster, the bubble stretches out in front of them, because that is where they will be next. The system also works out exactly how many seconds remain before two things would touch if nothing changed.",
    engineer:
      "A directional Gaussian per entity: the forward spread grows with speed (σ = 0.65 + 0.75·v ahead, 0.65 + 0.20·v sideways), risks combine as 1 − Π(1 − rᵢ). Time to collision solves the constant-velocity sphere-contact quadratic with a numerically stable root. Protected zones are oriented boxes.",
    problem: "Distance alone ignores where people are heading.",
    approach: "Model risk directionally and predict contact time exactly under a stated motion model.",
    code: ["tracking_and_navigation/risk_and_geofence.py"],
    lab: "risk",
    studio: { layers: ["risk", "geofences", "tracks"], view: "top" },
  },
  {
    id: "planning",
    short: "Planning",
    title: "Finding a safe, smooth route",
    simple:
      "Like a satnav that also avoids crowds: first it finds any safe route through the grid, then it smooths it into a curve a robot can actually drive, keeping away from walls and risky areas. It re-plans when things move.",
    engineer:
      "A* over a clearance grid with risk costs and moving-entity checks, followed by a cubic B-spline optimised with L-BFGS-B (smoothness, clearance, risk and reference terms, analytic gradient). The spline is accepted only if it stays clear and does not cost more.",
    problem: "Grid paths are jagged; straight lines ignore risk.",
    approach: "Search, then optimise a smooth curve with hard clearance checks.",
    code: ["tracking_and_navigation/safe_corridor_planner.py"],
    studio: { layers: ["path", "risk", "map"], view: "top" },
  },
  {
    id: "studio",
    short: "Studio & halo",
    title: "Seeing it live, on screen and on the device",
    simple:
      "Everything shows up in a browser: the 3D room, moving things, risk areas and the planned route. A ring of lights on the device mirrors the system's health, so you know at a glance if something is wrong without opening a laptop.",
    engineer:
      "FastAPI service with role-based auth and a binary scene protocol (v2) decoded in a Web Worker; Three.js renderer with instanced geometry, streamed map tiles and quality tiers. The ESP32 halo receives framed status over serial and fails to a disconnected state on malformed or stale input.",
    problem: "Data nobody can see does not help anyone.",
    approach: "A fast live view in the browser plus a glanceable physical indicator.",
    code: ["command_center/", "web/src/", "hardware_halo/"],
    studio: { layers: ["map", "voxels", "tracks", "path", "geofences", "risk"], view: "isometric" },
  },
];

export type Evidence = "laptop" | "cloud" | "ci";

export interface Measurement {
  label: string;
  before?: string;
  after: string;
  note: string;
  where: Evidence;
  source: string;
}

export const EVIDENCE_LABEL: Record<Evidence, string> = {
  laptop: "Measured on the development laptop (RTX 4070 Laptop GPU, simulated sensors)",
  cloud: "Measured on a rented cloud GPU, simulated sensors",
  ci: "Automated tests in CI",
};

export const MEASUREMENTS: Measurement[] = [
  {
    label: "3D view with live data streaming",
    before: "33-34 fps",
    after: "237-238.6 fps",
    note: "The display's 240 Hz refresh cap; scene update p95 dropped from 26-27 ms to 1.7-2.3 ms.",
    where: "laptop",
    source: "artifacts/renderer_benchmarks/",
  },
  {
    label: "Perception and tracking per frame (seed 101, p50)",
    before: "1,244 ms",
    after: "120 ms",
    note: "Disk-tiled map queries now reuse cached search trees.",
    where: "laptop",
    source: "docs/REVIEW_AND_ROADMAP.md",
  },
  {
    label: "Same stage on an A10 cloud GPU (3 seeds, p50)",
    after: "67 / 68 / 82 ms",
    note: "Confirms the fix across scenarios. Cloud GPUs are not the target workstation.",
    where: "cloud",
    source: "reports/modal/sweep_a10_20261030T071848Z.json",
  },
  {
    label: "Route planning with risk (60 routes, p50 / p95)",
    before: "176 / 291 ms",
    after: "74 / 129 ms",
    note: "Analytic gradients for the smoothing step; route cost changed by under 0.3%.",
    where: "laptop",
    source: "reports/planner/",
  },
  {
    label: "Perception network forward pass (fp32)",
    after: "3.7-7.4 ms",
    note: "Across T4, L4 and A10 cloud GPUs.",
    where: "cloud",
    source: "reports/modal/",
  },
  {
    label: "15-minute stability run",
    after: "1,608 frames, memory flat",
    note: "p50 370 ms, p95 520 ms end to end on an A10.",
    where: "cloud",
    source: "reports/modal/stability_a10_20261029T234211Z.json",
  },
];

export type ProofStatus = "done" | "partial" | "not_yet";

export const PROOF: Array<{ item: string; status: ProofStatus; detail: string }> = [
  { item: "Full pipeline runs end to end", status: "done", detail: "On simulated sensors and recorded replays." },
  { item: "Automated tests", status: "done", detail: "Python, web unit, browser end-to-end and accessibility suites run in CI on Ubuntu and Windows." },
  { item: "Speed on a GPU machine", status: "partial", detail: "Measured on a laptop and cloud GPUs, not yet on the lab workstation." },
  { item: "Real sensors connected", status: "not_yet", detail: "Hardware bring-up is the next phase." },
  { item: "Calibration between laser and camera", status: "not_yet", detail: "Procedure is written; needs the physical rig." },
  { item: "Accuracy of detection, tracking and mapping", status: "not_yet", detail: "Cannot be claimed until measured against real ground truth." },
  { item: "Status light (halo) on real hardware", status: "not_yet", detail: "Firmware and protocol are tested in software only." },
];

export const ROADMAP: Array<{ phase: string; detail: string }> = [
  { phase: "Workstation baseline", detail: "Install and re-run every test and benchmark on the lab PC." },
  { phase: "Sensor bring-up", detail: "Connect the Raspberry Pi, laser scanner and depth camera; record raw data." },
  { phase: "Raw-sensor transport", detail: "Stream sensor data from the Pi to the workstation with timing and drop counts." },
  { phase: "Calibration and commissioning", detail: "Align the sensors, map the lab, connect the halo." },
  { phase: "30-minute live run", detail: "Record evidence: frame rates, drops, latency and memory on real hardware." },
  { phase: "Photoreal lab view", detail: "Optional: a walkthrough video turned into a 3D visual layer, aligned to the measured map and labelled as visual only." },
];

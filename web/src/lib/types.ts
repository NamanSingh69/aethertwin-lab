export type Vec3 = [number, number, number];
export type Numeric = Float32Array | Float64Array | Int32Array;
export interface HaloSnapshot {
  pixel_count: number;
  pixels: number[][];
  source: string;
  state_name: string;
  stale: boolean;
  watchdog: boolean;
  disconnected: boolean;
  source_age_ms: number | null;
  processing_latency_ms?: number;
  nearest_track_id: number | null;
  distance_m: number | null;
  azimuth_degrees: number | null;
  brightness: number;
  risk_scale: number;
  virtual: boolean;
  physical_verified: boolean;
  reason: string;
}
export interface Track {
  track_id: number;
  semantic_class: number;
  semantic_name: string;
  class_probs: number[];
  confidence: number;
  position: Vec3;
  velocity: Vec3;
  acceleration?: Vec3;
  obb_corners: Vec3[];
  predicted_trajectory: Vec3[];
  ttc_seconds: number | null;
  ttc_status?: string;
  breached_geofences: string[];
  eigen_invariants?: number[];
  covariance?: number[][];
  uncertainty?: number | Record<string, number>;
  member_count?: number;
  speed?: number;
  heading_rad?: number;
  state_kind?: "measurement_updated" | "predicted" | "unspecified";
  last_observed_timestamp?: number | null;
  measurement_age_seconds?: number | null;
}
export interface Zone {
  name: string;
  center: Vec3;
  half_extents: Vec3;
  rotation_matrix?: number[][];
  yaw?: number;
  severity_level: number;
  is_breached?: boolean;
  breaching_track_ids?: number[];
}
export interface Metadata {
  schema_version: number;
  frame_id: number;
  timestamp: number;
  fps: number;
  coordinate_frame?: string;
  tracks: Track[];
  geofences: Zone[];
  alerts: string[];
  trajectory_status: string;
  sensor_pose_world: number[][];
  source_mode?: string;
  processed_at_unix?: number;
  validity?: Record<string, boolean>;
  identity?: Record<string, string>;
  halo?: HaloSnapshot;
  diagnostics: Record<string, any>;
  voxels?: Record<string, any>;
  floor_risk?: Record<string, any>;
  traffic_heatmap?: Record<string, any>;
  path?: number[][] | null;
  gemini_inlay?: number[][] | null;
}
export interface SceneFrame {
  scene: Metadata;
  arrays: Record<string, Numeric>;
  sequence: number;
  streamId: string;
  receivedAt: number;
  sentAt?: number;
}
export type Bounds = number[] | number[][];
export interface Manifest {
  map_revision: string;
  coordinate_frame: "world_z_up";
  bounds: Bounds;
  tiles: {
    id: string;
    bounds: Bounds;
    lods: { url: string; distance: number; triangles: number }[];
  }[];
}
export interface Bootstrap {
  protocol_version: number;
  identity: string | { name?: string; role?: string; [key: string]: unknown };
  role: "viewer" | "operator";
  source_mode: string;
  hardware_status: string;
  map_revision: string;
  map_manifest_url: string;
  /** Visual-only photo capture of the lab, when the service was started with one. */
  appearance_url?: string | null;
  stream_url?: string;
  control_revision: number;
  capabilities: Record<string, any>;
  latest: Metadata | null;
  paused?: boolean;
  error?: string | null;
  source_active?: boolean;
  recording_id?: string;
  available_sources?: Array<
    | string
    | { id?: string; source: string; label?: string; recording_id?: string }
  >;
  replay?: { frame_count?: number; current_frame?: number; duration?: number };
  csrf_token?: string;
  halo?: HaloSnapshot;
}
export interface Ack {
  request_id: string;
  status: "accepted" | "rejected";
  revision: number;
  reason?: string;
}
export type LayerName =
  | "map"
  | "voxels"
  | "flow"
  | "tracks"
  | "geofences"
  | "risk"
  | "traffic"
  | "path"
  | "rgbd"
  | "capture";
export type Layers = Record<LayerName, boolean>;
export const initialLayers: Layers = {
  map: true,
  voxels: true,
  flow: true,
  tracks: true,
  geofences: true,
  risk: true,
  traffic: false,
  path: true,
  rgbd: false,
  capture: false,
};
export const palette = [
  "#304d5c",
  "#24b7d8",
  "#21e5b0",
  "#b388ff",
  "#ffd165",
  "#d8f46d",
  "#f381ba",
];
export const semanticNames = [
  "Ground",
  "Structure",
  "Person",
  "Robot cart",
  "New obstacle",
  "Cable",
  "Foliage",
];
export const descriptorNames = [
  "Linearity",
  "Planarity",
  "Scattering",
  "Omnivariance",
  "Anisotropy",
  "Eigenentropy",
  "Surface variation",
  "Verticality",
  "Eigenvalue sum",
  "Height deviation",
];

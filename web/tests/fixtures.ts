import type { Metadata, Track } from "../src/lib/types";
export const track: Track = {
  track_id: 7,
  semantic_class: 3,
  semantic_name: "DYNAMIC_ROBOT_CART",
  class_probs: [0, 0, 0, 0.9, 0.1, 0, 0],
  confidence: 0.9,
  position: [1, 2, 0.5],
  velocity: [0.3, 0, 0],
  acceleration: [0, 0, 0],
  obb_corners: [
    [0, 1, 0],
    [0, 1, 1],
    [0, 3, 0],
    [0, 3, 1],
    [2, 1, 0],
    [2, 1, 1],
    [2, 3, 0],
    [2, 3, 1],
  ],
  predicted_trajectory: [
    [1, 2, 0.5],
    [1.3, 2, 0.5],
  ],
  ttc_seconds: null,
  breached_geofences: [],
  eigen_invariants: Array.from({ length: 10 }, (_, i) => i * 0.01),
  covariance: [
    [0.04, 0, 0],
    [0, 0.09, 0],
    [0, 0, 0.16],
  ],
};
export const scene: Metadata = {
  schema_version: 1,
  frame_id: 10,
  timestamp: 1,
  fps: 4,
  coordinate_frame: "world_z_up",
  tracks: [track],
  geofences: [],
  alerts: [],
  trajectory_status: "ok",
  sensor_pose_world: [
    [1, 0, 0, 0],
    [0, 1, 0, -4],
    [0, 0, 1, 1.6],
    [0, 0, 0, 1],
  ],
  diagnostics: { source_mode: "sim", processing_ms: 250 },
  voxels: { centers: [[0, 0, 1]], colors: [[0, 1, 0.5]], flow: [[0.1, 0, 0]] },
  path: [
    [0, 0, 0.6],
    [1, 0, 0.6],
  ],
};
export type Part = {
  name: string;
  data: Float32Array | Float64Array | Int32Array;
  shape: number[];
};
export function packet(
  parts: Part[],
  options: Record<string, unknown> = {},
  mutate?: (header: any) => void,
) {
  let offset = 0;
  const sections = parts.map((p) => {
    offset = Math.ceil(offset / 8) * 8;
    const s = {
      name: p.name,
      dtype:
        p.data instanceof Float64Array
          ? "<f8"
          : p.data instanceof Int32Array
            ? "<i4"
            : "<f4",
      shape: p.shape,
      offset,
      length: p.data.byteLength,
    };
    offset += p.data.byteLength;
    return s;
  });
  const header = {
    protocol_version: 2,
    kind: "keyframe",
    sequence: 1,
    base_sequence: null,
    stream_id: "test-stream",
    scene,
    sections,
    ...options,
  };
  mutate?.(header);
  const bytes = new TextEncoder().encode(JSON.stringify(header)),
    start = Math.ceil((12 + bytes.length) / 8) * 8,
    buffer = new ArrayBuffer(start + offset),
    view = new DataView(buffer);
  view.setUint32(0, 0x4e575441, true);
  view.setUint16(4, 2, true);
  view.setUint32(8, bytes.length, true);
  new Uint8Array(buffer, 12, bytes.length).set(bytes);
  parts.forEach((p, i) =>
    new Uint8Array(buffer, start + sections[i].offset, p.data.byteLength).set(
      new Uint8Array(p.data.buffer, p.data.byteOffset, p.data.byteLength),
    ),
  );
  return buffer;
}
export function voxels(keys: number[][], centers = keys): Part[] {
  return [
    {
      name: "voxels.keys",
      data: new Int32Array(keys.flat()),
      shape: [keys.length, 3],
    },
    {
      name: "voxels.centers",
      data: new Float32Array(centers.flat()),
      shape: [keys.length, 3],
    },
    {
      name: "voxels.colors",
      data: new Float32Array(keys.flatMap(() => [0, 0.7, 0.8])),
      shape: [keys.length, 3],
    },
    {
      name: "voxels.flow",
      data: new Float32Array(keys.length * 3),
      shape: [keys.length, 3],
    },
  ];
}

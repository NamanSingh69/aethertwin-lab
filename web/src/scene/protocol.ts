import type { Metadata, Numeric, SceneFrame } from "../lib/types";
export const MAX_PACKET = 16 * 1024 * 1024,
  MAX_VOXELS = 100000;
interface Section {
  name: string;
  dtype: "<f4" | "<f8" | "<i4";
  shape: number[];
  offset: number;
  length: number;
}
export interface Header {
  protocol_version: number;
  kind: "keyframe" | "delta";
  sequence: number;
  base_sequence: number | null;
  stream_id: string;
  sent_at_unix?: number;
  scene: Metadata;
  sections: Section[];
}
export class ResyncRequired extends Error {}
export function decodePacket(buffer: ArrayBuffer): {
  header: Header;
  arrays: Record<string, Numeric>;
} {
  if (buffer.byteLength < 12 || buffer.byteLength > MAX_PACKET)
    throw new Error("Invalid packet size");
  const view = new DataView(buffer);
  if (
    view.getUint32(0, true) !== 0x4e575441 ||
    view.getUint16(4, true) !== 2 ||
    view.getUint16(6, true) !== 0
  )
    throw new Error("Unsupported scene protocol");
  const length = view.getUint32(8, true);
  if (length > 1024 * 1024 || 12 + length > buffer.byteLength)
    throw new Error("Invalid scene header");
  const header: Header = JSON.parse(
    new TextDecoder("utf-8", { fatal: true }).decode(
      new Uint8Array(buffer, 12, length),
    ),
  );
  if (
    header.protocol_version !== 2 ||
    !["keyframe", "delta"].includes(header.kind) ||
    !Number.isSafeInteger(header.sequence) ||
    header.sequence < 0 ||
    typeof header.stream_id !== "string" ||
    !header.stream_id ||
    !header.scene ||
    !Array.isArray(header.sections) ||
    header.sections.length > 32
  )
    throw new Error("Invalid scene directory");
  const start = Math.ceil((12 + length) / 8) * 8;
  if (start > buffer.byteLength) throw new Error("Missing header padding");
  for (let i = 12 + length; i < start; i++)
    if (view.getUint8(i) !== 0) throw new Error("Nonzero header padding");
  const allowed = new Set([
    "voxels.keys",
    "voxels.centers",
    "voxels.colors",
    "voxels.flow",
    "voxels.deleted",
    "floor_risk.centers",
    "floor_risk.risk",
    "floor_risk.colors",
    "traffic_heatmap.values",
    "path",
    "gemini_inlay",
  ]);
  const arrays: Record<string, Numeric> = {},
    ranges: Array<[number, number]> = [];
  for (const s of header.sections) {
    const bytes = s.dtype === "<f8" ? 8 : 4;
    if (
      !allowed.has(s.name) ||
      !["<f4", "<f8", "<i4"].includes(s.dtype) ||
      typeof s.name !== "string" ||
      Object.hasOwn(arrays, s.name) ||
      !Array.isArray(s.shape) ||
      s.shape.length > 3 ||
      s.shape.some((n) => !Number.isSafeInteger(n) || n < 0) ||
      !Number.isSafeInteger(s.offset) ||
      s.offset < 0 ||
      s.offset % bytes !== 0 ||
      !Number.isSafeInteger(s.length) ||
      s.length < 0
    )
      throw new Error("Invalid section");
    const count = s.shape.reduce((a, b) => a * b, 1);
    if (
      !Number.isSafeInteger(count) ||
      count * bytes !== s.length ||
      start + s.offset + s.length > buffer.byteLength
    )
      throw new Error("Section shape or bounds mismatch");
    if (
      ranges.some(
        ([a, b]) => s.length > 0 && s.offset < b && s.offset + s.length > a,
      )
    )
      throw new Error("Overlapping sections");
    ranges.push([s.offset, s.offset + s.length]);
    arrays[s.name] =
      s.dtype === "<f8"
        ? new Float64Array(buffer, start + s.offset, count)
        : s.dtype === "<i4"
          ? new Int32Array(buffer, start + s.offset, count)
          : new Float32Array(buffer, start + s.offset, count);
    if (s.dtype !== "<i4")
      for (const value of arrays[s.name])
        if (!Number.isFinite(value)) throw new Error("Nonfinite scene data");
  }
  const shapes: Record<string, number | undefined> = {
    "voxels.keys": 3,
    "voxels.centers": 3,
    "voxels.colors": 3,
    "voxels.flow": 3,
    "voxels.deleted": 3,
    "floor_risk.centers": 3,
    "floor_risk.colors": 3,
    path: 3,
    gemini_inlay: 6,
  };
  for (const s of header.sections)
    if (
      shapes[s.name] !== undefined &&
      (s.shape.length !== 2 || s.shape[1] !== shapes[s.name])
    )
      throw new Error("Invalid spatial array shape");
  for (const s of header.sections)
    if (
      (s.name === "voxels.keys" || s.name === "voxels.deleted") &&
      s.dtype !== "<i4"
    )
      throw new Error("Voxel coordinates must be integers");
  for (const s of header.sections)
    if (
      (s.name === "floor_risk.risk" && s.shape.length !== 1) ||
      (s.name === "traffic_heatmap.values" && s.shape.length !== 2)
    )
      throw new Error("Invalid raster shape");
  const floorCount = arrays["floor_risk.risk"]?.length;
  if (
    floorCount !== undefined &&
    (arrays["floor_risk.centers"]?.length !== floorCount * 3 ||
      arrays["floor_risk.colors"]?.length !== floorCount * 3)
  )
    throw new Error("Unaligned floor risk sections");
  return { header, arrays };
}
export class SceneAccumulator {
  private sequence = -1;
  private stream = "";
  private count = 0;
  private lookup = new Map<string, number>();
  private keys = new Int32Array(MAX_VOXELS * 3);
  private centers = new Float32Array(MAX_VOXELS * 3);
  private colors = new Float32Array(MAX_VOXELS * 3);
  private flow = new Float32Array(MAX_VOXELS * 3);
  reset() {
    this.sequence = -1;
    this.stream = "";
    this.count = 0;
    this.lookup.clear();
  }
  apply(buffer: ArrayBuffer): SceneFrame {
    const { header: h, arrays: a } = decodePacket(buffer);
    if (
      h.kind === "delta" &&
      (h.stream_id !== this.stream ||
        h.base_sequence !== this.sequence ||
        h.sequence <= this.sequence)
    )
      throw new ResyncRequired("Scene sequence gap; awaiting keyframe");
    const keys = a["voxels.keys"];
    const n = keys ? keys.length / 3 : 0;
    if (
      !keys ||
      n > MAX_VOXELS ||
      ["centers", "colors", "flow"].some(
        (k) => a["voxels." + k]?.length !== n * 3,
      )
    )
      throw new Error("Unaligned voxel sections");
    const unique = new Set<string>();
    for (let i = 0; i < n; i++) {
      const key = Array.prototype.join.call(
        keys.subarray(i * 3, i * 3 + 3),
        ",",
      );
      if (unique.has(key)) throw new Error("Duplicate voxel coordinates");
      unique.add(key);
    }
    const deletedKeys = new Set<string>();
    const deletedArray = a["voxels.deleted"];
    if (deletedArray)
      for (let i = 0; i < deletedArray.length; i += 3) {
        const key = Array.prototype.join.call(
          deletedArray.subarray(i, i + 3),
          ",",
        );
        if (deletedKeys.has(key))
          throw new Error("Duplicate deleted coordinates");
        deletedKeys.add(key);
      }
    let projected = h.kind === "keyframe" ? 0 : this.count;
    if (h.kind === "delta")
      for (const key of deletedKeys) if (this.lookup.has(key)) projected--;
    for (const key of unique)
      if (
        h.kind === "keyframe" ||
        !this.lookup.has(key) ||
        deletedKeys.has(key)
      )
        projected++;
    if (projected > MAX_VOXELS) throw new Error("Voxel capacity exceeded");
    if (h.kind === "keyframe") {
      this.count = 0;
      this.lookup.clear();
    }
    const deleted = a["voxels.deleted"];
    if (deleted)
      for (let i = 0; i < deleted.length; i += 3) {
        const key = Array.prototype.join.call(deleted.subarray(i, i + 3), ",");
        const index = this.lookup.get(key);
        if (index === undefined) continue;
        this.lookup.delete(key);
        const last = --this.count;
        if (index !== last) {
          for (const arr of [this.keys, this.centers, this.colors, this.flow])
            arr.copyWithin(index * 3, last * 3, last * 3 + 3);
          this.lookup.set(
            Array.prototype.join.call(
              this.keys.subarray(index * 3, index * 3 + 3),
              ",",
            ),
            index,
          );
        }
      }
    for (let i = 0; i < n; i++) {
      const key = Array.prototype.join.call(
        keys.subarray(i * 3, i * 3 + 3),
        ",",
      );
      let index = this.lookup.get(key);
      if (index === undefined) {
        if (this.count >= MAX_VOXELS)
          throw new Error("Voxel capacity exceeded");
        index = this.count++;
        this.lookup.set(key, index);
      }
      this.keys.set(keys.subarray(i * 3, i * 3 + 3), index * 3);
      this.centers.set(
        a["voxels.centers"].subarray(i * 3, i * 3 + 3),
        index * 3,
      );
      this.colors.set(a["voxels.colors"].subarray(i * 3, i * 3 + 3), index * 3);
      this.flow.set(a["voxels.flow"].subarray(i * 3, i * 3 + 3), index * 3);
    }
    this.sequence = h.sequence;
    this.stream = h.stream_id;
    const output: Record<string, Numeric> = {};
    for (const [key, value] of Object.entries(a))
      if (!key.startsWith("voxels.")) output[key] = value;
    output["voxels.centers"] = this.centers.slice(0, this.count * 3);
    output["voxels.colors"] = this.colors.slice(0, this.count * 3);
    output["voxels.flow"] = this.flow.slice(0, this.count * 3);
    return {
      scene: h.scene,
      arrays: output,
      sequence: h.sequence,
      streamId: h.stream_id,
      receivedAt: Date.now(),
      sentAt: h.sent_at_unix,
    };
  }
}
export function jsonFrame(scene: Metadata): SceneFrame {
  const arrays: Record<string, Numeric> = {};
  const add = (key: string, value: any) => {
    if (Array.isArray(value)) arrays[key] = new Float32Array(value.flat(2));
  };
  for (const name of ["centers", "colors", "flow"])
    add("voxels." + name, scene.voxels?.[name]);
  for (const name of ["centers", "risk", "colors"])
    add("floor_risk." + name, scene.floor_risk?.[name]);
  add("traffic_heatmap.values", scene.traffic_heatmap?.values);
  add("path", scene.path);
  add("gemini_inlay", scene.gemini_inlay);
  return {
    scene,
    arrays,
    sequence: scene.frame_id,
    streamId: "json",
    receivedAt: Date.now(),
  };
}

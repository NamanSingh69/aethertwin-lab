// Showcase format v1: a recorded session as static files (no service).
// <base>/index.json describes gzipped chunks of records; each record is a
// uint32 LE length N, a float64 LE t_s (seconds since replay start) and N bytes
// of one scene protocol v2 packet, byte-identical to /ws/v2/scene. The first
// packet of every chunk is a keyframe, so a chunk boundary is a seek point.
import { MAX_PACKET } from "../scene/protocol";

export const SHOWCASE_FORMAT = "aethertwin-showcase";
export const SHOWCASE_VERSION = 1;
const RECORD_HEADER = 12;
const MAX_CHUNK_BYTES = 256 * 1024 * 1024;

export interface ShowcaseSource {
  recording: string;
  commit: string;
  calibration: "measured" | "placeholder" | "none";
  lidar_height: "measured" | "assumed";
  hardware_verified: boolean;
  note: string;
}
export interface ShowcaseChunk {
  file: string;
  first_frame: number;
  frames: number;
  start_s: number;
  end_s: number;
  bytes: number;
}
export interface ShowcaseIndex {
  format: typeof SHOWCASE_FORMAT;
  version: typeof SHOWCASE_VERSION;
  label: string;
  recorded_at: string;
  duration_s: number;
  frame_count: number;
  source: ShowcaseSource;
  bootstrap: string;
  assets_prefix: string;
  chunks: ShowcaseChunk[];
}
export interface ReplayRecord {
  /** Seconds since replay start. */
  t: number;
  /** One scene protocol v2 packet (a view into the decompressed chunk). */
  packet: Uint8Array;
}

const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);
const count = (value: unknown): value is number =>
  Number.isInteger(value) && (value as number) >= 0;
/** A relative path inside the showcase folder: no scheme, root or parent steps. */
function relativePath(value: unknown, what: string): string {
  if (
    typeof value !== "string" ||
    !value ||
    /^[a-z][a-z0-9+.-]*:|^[/\\]|(^|[/\\])\.\.([/\\]|$)/i.test(value)
  )
    throw new Error(`Showcase index: ${what} must be a relative path`);
  return value;
}

/** Validates index.json. Unknown provenance values are kept but never shown as measured. */
export function parseShowcaseIndex(value: unknown): ShowcaseIndex {
  const index = value as Partial<ShowcaseIndex> | null;
  if (!index || typeof index !== "object" || index.format !== SHOWCASE_FORMAT)
    throw new Error("Not an AetherTwin showcase index");
  if (index.version !== SHOWCASE_VERSION)
    throw new Error(`Unsupported showcase version ${String(index.version)}`);
  if (!Array.isArray(index.chunks) || !index.chunks.length)
    throw new Error("Showcase index has no chunks");
  let next = 0;
  const chunks = index.chunks.map((chunk, i) => {
    if (
      !chunk ||
      !count(chunk.first_frame) ||
      !count(chunk.frames) ||
      chunk.frames < 1 ||
      chunk.first_frame !== next ||
      !finite(chunk.start_s) ||
      !finite(chunk.end_s) ||
      chunk.end_s < chunk.start_s
    )
      throw new Error(`Showcase index: invalid chunk ${i}`);
    next += chunk.frames;
    return { ...chunk, file: relativePath(chunk.file, `chunk ${i} file`) };
  });
  const source = (index.source ?? {}) as Partial<ShowcaseSource>;
  return {
    format: SHOWCASE_FORMAT,
    version: SHOWCASE_VERSION,
    label: typeof index.label === "string" ? index.label : "Recorded replay - not live",
    recorded_at: typeof index.recorded_at === "string" ? index.recorded_at : "",
    duration_s: finite(index.duration_s) ? index.duration_s : chunks[chunks.length - 1].end_s,
    frame_count: next,
    source: {
      recording: typeof source.recording === "string" ? source.recording : "",
      commit: typeof source.commit === "string" ? source.commit : "",
      calibration: source.calibration as ShowcaseSource["calibration"],
      lidar_height: source.lidar_height as ShowcaseSource["lidar_height"],
      hardware_verified: source.hardware_verified === true,
      note: typeof source.note === "string" ? source.note : "",
    },
    bootstrap: relativePath(index.bootstrap ?? "bootstrap.json", "bootstrap"),
    assets_prefix:
      typeof index.assets_prefix === "string" && index.assets_prefix
        ? relativePath(index.assets_prefix, "assets_prefix")
        : "",
    chunks,
  };
}

/** Splits a decompressed chunk into records; timestamps must not go backwards. */
export function parseRecords(bytes: Uint8Array): ReplayRecord[] {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const records: ReplayRecord[] = [];
  let offset = 0,
    last = -Infinity;
  while (offset < bytes.byteLength) {
    if (offset + RECORD_HEADER > bytes.byteLength)
      throw new Error("Showcase chunk is truncated (record header)");
    const length = view.getUint32(offset, true),
      t = view.getFloat64(offset + 4, true);
    if (length < 12 || length > MAX_PACKET)
      throw new Error(`Showcase record has an invalid length ${length}`);
    if (offset + RECORD_HEADER + length > bytes.byteLength)
      throw new Error("Showcase chunk is truncated (record body)");
    if (!Number.isFinite(t) || t < 0) throw new Error("Showcase record has an invalid timestamp");
    if (t < last) throw new Error("Showcase record timestamps go backwards");
    last = t;
    records.push({ t, packet: bytes.subarray(offset + RECORD_HEADER, offset + RECORD_HEADER + length) });
    offset += RECORD_HEADER + length;
  }
  if (!records.length) throw new Error("Showcase chunk has no records");
  return records;
}

/**
 * Native gzip decoding. A server that already decoded the body (it sent
 * `Content-Encoding: gzip` for the .gz file) hands over the plain records.
 */
export async function gunzip(data: ArrayBuffer): Promise<Uint8Array> {
  const bytes = new Uint8Array(data);
  if (bytes.byteLength > MAX_CHUNK_BYTES) throw new Error("Showcase chunk is too large");
  if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) return bytes;
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  const out = new Uint8Array(await new Response(stream).arrayBuffer());
  if (out.byteLength > MAX_CHUNK_BYTES) throw new Error("Showcase chunk is too large");
  return out;
}

/** The chunk whose range holds `frame` (clamped to the recording). */
export function chunkIndexForFrame(index: ShowcaseIndex, frame: number) {
  const target = Math.max(0, Math.min(index.frame_count - 1, Math.floor(frame)));
  let found = 0;
  index.chunks.forEach((chunk, i) => {
    if (chunk.first_frame <= target) found = i;
  });
  return found;
}

/**
 * Absolute data base URL with a trailing slash. Order: `?data=` (same origin
 * only, so a link cannot dress foreign data in this site's provenance), the
 * build-time VITE_SHOWCASE_BASE (may be a CDN), then "./showcase/".
 */
export function resolveShowcaseBase(search: string, href: string, configured?: string) {
  const slash = (url: URL) => {
    if (!url.pathname.endsWith("/")) url.pathname += "/";
    url.search = "";
    url.hash = "";
    return url.href;
  };
  const requested = new URLSearchParams(search).get("data");
  if (requested) {
    try {
      const url = new URL(requested, href);
      if (url.origin === new URL(href).origin && /^https?:$/.test(url.protocol)) return slash(url);
    } catch {
      // Fall through to the configured base.
    }
  }
  return slash(new URL(configured || "./showcase/", href));
}

function recordedDate(value: string) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return value || "an unstated date";
  return `${date.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/** Banner text. Everything not stated as measured reads as not measured. */
export function provenance(index: ShowcaseIndex) {
  const s = index.source;
  const calibration = ["measured", "placeholder", "none"].includes(s.calibration)
    ? s.calibration
    : "not stated";
  const height = ["measured", "assumed"].includes(s.lidar_height) ? s.lidar_height : "not stated";
  const details = [
    `Recording ${s.recording || "not named"}`,
    `Calibration: ${calibration}`,
    `LiDAR height: ${height}`,
    s.hardware_verified ? "Hardware verification stated by the export" : "Hardware not verified",
  ];
  if (s.commit) details.push(`Commit ${s.commit.slice(0, 7)}`);
  return {
    headline: `Recorded replay of ${recordedDate(index.recorded_at)}, not live`,
    details,
    note: s.note,
  };
}

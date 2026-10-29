import type { Bootstrap, Metadata } from "./types";
import type { Numeric, Vec3 } from "./types";
export function sourceLabel(
  scene: Metadata | null,
  bootstrap: Bootstrap | null,
) {
  const source =
    scene?.source_mode ??
    scene?.diagnostics.source_mode ??
    bootstrap?.source_mode;
  return (
    (
      { sim: "Simulation", replay: "Replay", live: "Live" } as Record<
        string,
        string
      >
    )[source] ?? "Source unavailable"
  );
}
export interface SensorFreshness {
  ageSeconds: number | null;
  transport?: string;
}
export interface SensorIndicator {
  tone: "mint" | "amber" | "muted";
  label: string;
  title: string;
  count: number | null;
}
/** Availability is based on reported observations, never presence of a scene. */
export function reportedSensorStatus(
  scene: Metadata | null,
  sensor: "lidar" | "depth",
  freshness: SensorFreshness,
): SensorIndicator {
  const d = scene?.diagnostics ?? {};
  const raw = d.observation_counts?.[sensor];
  const count =
    typeof raw === "number" && Number.isInteger(raw) && raw >= 0 ? raw : null;
  const countInvalid = raw !== undefined && count === null;
  const invalid =
    scene?.validity?.observations === false ||
    countInvalid ||
    (sensor === "depth"
      ? d.camera_available === false || d.painting === "camera_unavailable"
      : d.lidar_available === false);
  const age = freshness.ageSeconds;
  const ageKnown = typeof age === "number" && Number.isFinite(age) && age >= 0;
  const stale =
    scene !== null &&
    ((ageKnown && age > 3) || freshness.transport === "offline");
  const tone =
    invalid || count === 0 || stale
      ? "amber"
      : count !== null && count > 0 && ageKnown
        ? "mint"
        : "muted";
  const name = sensor === "lidar" ? "3D LiDAR" : "Depth camera";
  const state = invalid
    ? "invalid or unavailable observations"
    : count === 0
      ? "zero reported observations"
      : stale
        ? "stale or disconnected observations"
        : tone === "mint"
          ? "fresh observations"
          : "observation availability unknown";
  const countText =
    count === null
      ? countInvalid
        ? "invalid reported count"
        : "count not reported"
      : `${count} reported points`;
  return {
    tone,
    count,
    label: `${name}: ${state}`,
    title: `${name}: ${countText}; ${ageKnown ? `observation age ${age.toFixed(2)} s` : "observation age unknown"}${freshness.transport ? `; browser transport ${freshness.transport}` : ""}; ${state}. Observation availability does not establish physical hardware verification.`,
  };
}
export function sensorWarnings(
  scene: Metadata | null,
  freshness?: SensorFreshness,
) {
  const d = scene?.diagnostics ?? {};
  const warnings: string[] = [];
  if (d.s2_degraded === true || d.s2_error)
    warnings.push(
      "Optional S2 degraded" + (d.s2_error ? `: ${d.s2_error}` : ""),
    );
  if (d.painting === "camera_unavailable" || d.camera_available === false)
    warnings.push(
      "RGB-D unavailable" + (d.camera_error ? `: ${d.camera_error}` : ""),
    );
  const counts = d.observation_counts;
  const declared =
    counts && typeof counts === "object" ? Object.values(counts) : [];
  const allEmpty =
    declared.length > 0 &&
    declared.every(
      (value) =>
        typeof value === "number" && Number.isInteger(value) && value === 0,
    );
  if (scene?.validity?.observations === false || allEmpty)
    warnings.push(
      "No valid current observations; retained tracks or map may remain visible.",
    );
  else {
    if (counts?.lidar === 0)
      warnings.push("3D LiDAR reports zero current points.");
    if (
      counts?.depth === 0 &&
      d.painting !== "camera_unavailable" &&
      d.camera_available !== false
    )
      warnings.push("Depth camera reports zero current points.");
  }
  if (scene?.validity?.localization === false)
    warnings.push(
      "Localization invalid; motion and navigation must remain held.",
    );
  else if (d.navigation_observations_valid === false)
    warnings.push("Navigation observations invalid; motion remains held.");
  if (
    freshness &&
    scene &&
    (freshness.transport === "offline" ||
      (typeof freshness.ageSeconds === "number" &&
        Number.isFinite(freshness.ageSeconds) &&
        freshness.ageSeconds > 3))
  )
    warnings.push(
      "Observations stale or disconnected; retained data may remain visible.",
    );
  return warnings;
}
export function canControl(bootstrap: Bootstrap | null, action: string) {
  return (
    bootstrap?.role === "operator" && bootstrap.capabilities?.[action] === true
  );
}
export function metric(value: unknown, digits = 1) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits)
    : "—";
}
export function formatDuration(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const m = Math.floor(seconds / 60),
    s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
export function observationIdentity(scene: Metadata, stream: string) {
  return `${scene.diagnostics.runtime_session_id ?? stream}:${scene.source_mode ?? scene.diagnostics.source_mode ?? ""}:${scene.frame_id}:${scene.timestamp}`;
}
/** Keep fallback bulk arrays out of React state as well as binary buffers. */
export function metadataOnly(scene: Metadata): Metadata {
  const omit = (value: Record<string, any> | undefined, names: string[]) =>
    value
      ? Object.fromEntries(
          Object.entries(value).filter(([key]) => !names.includes(key)),
        )
      : undefined;
  const { path: _path, gemini_inlay: _inlay, ...rest } = scene;
  return {
    ...rest,
    voxels: omit(scene.voxels, [
      "centers",
      "colors",
      "flow",
      "keys",
      "deleted",
    ]),
    floor_risk: omit(scene.floor_risk, [
      "centers",
      "risk",
      "colors",
      "source_indices",
    ]),
    traffic_heatmap: omit(scene.traffic_heatmap, ["values"]),
  };
}
export function planningDefaults(
  scene: Metadata,
  path: Numeric | undefined,
  currentMode: string,
): { start: Vec3 | null; goal: Vec3 | null } {
  const source = scene.source_mode ?? scene.diagnostics.source_mode;
  if (
    !["sim", "replay"].includes(currentMode) ||
    source !== currentMode ||
    scene.diagnostics.planning_requested === false
  )
    return { start: null, goal: null };
  const robot = scene.diagnostics.robot_position;
  return {
    start:
      Array.isArray(robot) && robot.length === 3 && robot.every(Number.isFinite)
        ? ([...robot] as Vec3)
        : null,
    goal:
      path && path.length >= 3 ? (Array.from(path.slice(-3)) as Vec3) : null,
  };
}

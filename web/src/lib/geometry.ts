import type { Track, Vec3, Zone } from "./types";
export function zoneYaw(zone: Zone) {
  return (
    zone.yaw ??
    Math.atan2(
      zone.rotation_matrix?.[1]?.[0] ?? 0,
      zone.rotation_matrix?.[0]?.[0] ?? 1,
    )
  );
}
/** Signed distance from the entity CENTER to an oriented zone boundary, metres. */
export function centerZoneClearance(point: Vec3, zone: Zone): number {
  const p = point.map((v, i) => v - zone.center[i]);
  let local: number[];
  if (zone.rotation_matrix)
    local = [0, 1, 2].map((i) =>
      p.reduce((sum, v, j) => sum + v * zone.rotation_matrix![j][i], 0),
    );
  else {
    const y = zoneYaw(zone),
      c = Math.cos(y),
      s = Math.sin(y);
    local = [c * p[0] + s * p[1], -s * p[0] + c * p[1], p[2]];
  }
  const q = local.map((v, i) => Math.abs(v) - zone.half_extents[i]);
  return (
    Math.hypot(...q.map((v) => Math.max(v, 0))) + Math.min(Math.max(...q), 0)
  );
}
export function positionStd(track: Track): Vec3 | null {
  const c = track.covariance;
  if (
    !c ||
    c.length < 3 ||
    [0, 1, 2].some((i) => !Number.isFinite(c[i]?.[i]) || c[i][i] < 0)
  )
    return null;
  return [Math.sqrt(c[0][0]), Math.sqrt(c[1][1]), Math.sqrt(c[2][2])];
}
export function parsePoint(value: string): Vec3 {
  const values = value.split(",").map((v) => Number(v.trim()));
  if (
    values.length !== 3 ||
    values.some((v) => !Number.isFinite(v)) ||
    value.split(",").some((v) => !v.trim())
  )
    throw new Error("Enter three coordinates: x, y, z.");
  return values as Vec3;
}
export function pickHeight(
  targetText: string,
  planeText: string,
): number | null {
  try {
    return parsePoint(targetText)[2];
  } catch {
    /* An operator may specify a plane before XY. */
  }
  if (!planeText.trim()) return null;
  const value = Number(planeText);
  return Number.isFinite(value) ? value : null;
}
export function validZone(zone: Zone) {
  return (
    Boolean(zone.name.trim()) &&
    [...zone.center, ...zone.half_extents, zone.yaw ?? 0].every(
      Number.isFinite,
    ) &&
    zone.half_extents.every((v) => v > 0 && v < 100) &&
    [1, 2, 3].includes(zone.severity_level)
  );
}

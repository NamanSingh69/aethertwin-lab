import type { Bounds, Vec3 } from "../lib/types";
export function normalizeBounds(bounds: Bounds): [Vec3, Vec3] {
  const values = bounds.flat();
  if (
    values.length !== 6 ||
    !values.every(Number.isFinite) ||
    [0, 1, 2].some((i) => values[i] > values[i + 3])
  )
    throw new Error("Invalid asset bounds");
  return [values.slice(0, 3) as Vec3, values.slice(3, 6) as Vec3];
}
/** LOD distance is the minimum range at which that level becomes active. */
export function chooseLOD<T extends { distance: number }>(
  lods: T[],
  distance: number,
): T | undefined {
  const ordered = [...lods].sort((a, b) => a.distance - b.distance);
  return ordered.filter((l) => distance >= l.distance).at(-1) ?? ordered[0];
}

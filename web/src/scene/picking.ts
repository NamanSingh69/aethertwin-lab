import { Plane, Vector3, type Ray } from "three";
import type { Vec3 } from "../lib/types";
export function floorPickPoint(ray: Ray, height: number | null): Vec3 | null {
  if (height === null || !Number.isFinite(height)) return null;
  const point = new Vector3();
  if (!ray.intersectPlane(new Plane(new Vector3(0, 0, 1), -height), point))
    return null;
  return [point.x, point.y, point.z];
}

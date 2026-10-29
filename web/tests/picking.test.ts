import { it, expect } from "vitest";
import { Ray, Vector3 } from "three";
import { floorPickPoint } from "../src/scene/picking";
import { pickHeight } from "../src/lib/geometry";
import { planningDefaults } from "../src/lib/status";
import { scene } from "./fixtures";
it("requires an explicit target or editor plane height with no simulation fallback", () => {
  expect(pickHeight("", "")).toBeNull();
  expect(pickHeight("1, 2, 3.75", "")).toBe(3.75);
  expect(pickHeight("", "2.25")).toBe(2.25);
  expect(pickHeight("", "0")).toBe(0);
  expect(pickHeight("", "Infinity")).toBeNull();
});
it("picks the actual requested world-Z plane and refuses an unspecified plane", () => {
  const ray = new Ray(new Vector3(1, 2, 10), new Vector3(0, 0, -1));
  expect(floorPickPoint(ray, 3.75)).toEqual([1, 2, 3.75]);
  expect(floorPickPoint(ray, -1.5)).toEqual([1, 2, -1.5]);
  expect(floorPickPoint(ray, null)).toBeNull();
});
it("does not reuse simulation robot/path endpoints for a live draft", () => {
  const observed = {
    ...scene,
    diagnostics: {
      ...scene.diagnostics,
      robot_position: [-1, -4, 0.625],
      planning_requested: true,
    },
  };
  const path = new Float32Array([1, 2, 0.625]);
  expect(planningDefaults(observed, path, "live")).toEqual({
    start: null,
    goal: null,
  });
  expect(
    planningDefaults({ ...observed, source_mode: "live" }, path, "live"),
  ).toEqual({ start: null, goal: null });
  expect(planningDefaults(observed, path, "sim").start).toEqual([
    -1, -4, 0.625,
  ]);
});

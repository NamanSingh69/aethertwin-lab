import * as T from "three";
import { describe, expect, it } from "vitest";
import { GrowableGeometry } from "../src/scene/buffers";

const points = [
  [0, 0, 0],
  [1, 0, 0],
  [1, 2, 0],
  [4, 6, 0],
  [4, 6, 2],
  [0, 0, 1],
];

function filled(count: number) {
  const buffer = new GrowableGeometry({ position: 3, lineDistance: 1 }, 2);
  buffer.reserve(count);
  buffer.array("position").set(points.slice(0, count).flat());
  return buffer;
}

function threeDistances(make: (g: T.BufferGeometry) => T.Line, count: number) {
  const geometry = new T.BufferGeometry().setAttribute(
    "position",
    new T.Float32BufferAttribute(points.slice(0, count).flat(), 3),
  );
  const line = make(geometry);
  line.computeLineDistances();
  return Array.from(geometry.getAttribute("lineDistance").array);
}

describe("GrowableGeometry", () => {
  it.each([
    ["Line", false, (g: T.BufferGeometry) => new T.Line(g)],
    ["LineSegments", true, (g: T.BufferGeometry) => new T.LineSegments(g)],
  ])("matches three's dash distances for %s", (_, segments, make) => {
    const buffer = filled(6);
    buffer.lineDistances(6, segments);
    const ours = Array.from(buffer.array("lineDistance").slice(0, 6));
    threeDistances(make, 6).forEach((value, i) =>
      expect(ours[i]).toBeCloseTo(value, 5),
    );
  });

  it("grows by doubling, keeps attributes in sync and uploads only the used range", () => {
    const buffer = new GrowableGeometry({ position: 3, color: 3 }, 16);
    const first = buffer.array("position");
    buffer.reserve(10);
    expect(buffer.array("position")).toBe(first);
    buffer.reserve(17);
    expect(buffer.array("position").length).toBe(32 * 3);
    expect(buffer.array("color").length).toBe(32 * 3);
    buffer.commit(5);
    const position = buffer.geometry.getAttribute("position") as T.BufferAttribute;
    expect(position.updateRanges).toEqual([{ start: 0, count: 15 }]);
    expect(buffer.geometry.drawRange).toEqual({ start: 0, count: 5 });
    buffer.commit(0);
    expect(position.updateRanges).toEqual([]);
    expect(buffer.geometry.drawRange.count).toBe(0);
  });
});

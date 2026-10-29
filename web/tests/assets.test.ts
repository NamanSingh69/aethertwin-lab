import { describe, it, expect } from "vitest";
import { normalizeBounds, chooseLOD } from "../src/scene/assets";
describe("actual static asset contract", () => {
  it("accepts flat exporter bounds and nested bounds without rotating Z up", () => {
    expect(normalizeBounds([-6, -5, 0, 6, 5, 3.5])).toEqual([
      [-6, -5, 0],
      [6, 5, 3.5],
    ]);
    expect(
      normalizeBounds([
        [-6, -5, 0],
        [6, 5, 3.5],
      ]),
    ).toEqual([
      [-6, -5, 0],
      [6, 5, 3.5],
    ]);
  });
  it("rejects reversed, incomplete and nonfinite bounds", () => {
    for (const bounds of [
      [0, 0, 0, -1, 1, 1],
      [0, 0, 0, 1, 1],
      [0, 0, 0, 1, 1, Infinity],
    ])
      expect(() => normalizeBounds(bounds)).toThrow();
  });
  it("selects LOD using minimum switch distances from the Python exporter", () => {
    const lods = [
      { distance: 0, id: "full" },
      { distance: 8, id: "medium" },
      { distance: 18, id: "coarse" },
    ];
    expect(chooseLOD(lods, 3)?.id).toBe("full");
    expect(chooseLOD(lods, 8)?.id).toBe("medium");
    expect(chooseLOD(lods, 15)?.id).toBe("medium");
    expect(chooseLOD(lods, 18)?.id).toBe("coarse");
    expect(chooseLOD(lods, 90)?.id).toBe("coarse");
  });
});

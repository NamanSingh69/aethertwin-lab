import { describe, expect, it } from "vitest";
import { alignmentSummary, parseAppearance } from "../src/lib/appearance";

const valid = {
  schema_version: 1,
  kind: "gltf",
  file: "lab.glb",
  visual_only: true,
  transform: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
  alignment: { method: "measured_points", rmse_m: 0.031, checked_points: 6 },
};

describe("photo-capture manifest", () => {
  it("accepts a visual-only glTF capture and summarises its alignment", () => {
    const appearance = parseAppearance(valid);
    expect(appearance.file).toBe("lab.glb");
    expect(alignmentSummary(appearance)).toBe("Aligned, error 3.1 cm over 6 points");
    expect(alignmentSummary(parseAppearance({ ...valid, alignment: { method: "none", rmse_m: null, checked_points: null } }))).toBe(
      "Not aligned to the measured map",
    );
  });

  it.each([
    [{ visual_only: false }, "visual only"],
    [{ kind: "gaussian_splat" }, "kind"],
    [{ file: "../lab.glb" }, ".glb name"],
    [{ transform: [1, 0, 0] }, "16 finite"],
    [{ schema_version: 2 }, "version"],
  ])("rejects %o", (change, message) => {
    expect(() => parseAppearance({ ...valid, ...change })).toThrow(message);
  });
});

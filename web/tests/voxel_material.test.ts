import { it, expect } from "vitest";
import * as T from "three";
import { createSemanticVoxelMesh } from "../src/scene/SpatialRenderer";

it("uses independent instanced RGB without multiplying a missing box vertex-color attribute", () => {
  const mesh = createSemanticVoxelMesh(3);
  try {
    // A colorless BoxGeometry + vertexColors:true enables USE_COLOR with a
    // missing vertex attribute. Its default black would erase instanceColor.
    expect(mesh.geometry.getAttribute("color")).toBeUndefined();
    expect(mesh.material.vertexColors).toBe(false);
    expect(mesh.material.color.toArray()).toEqual([1, 1, 1]);
    expect(mesh.instanceColor).not.toBeNull();
    expect(mesh.instanceColor!.itemSize).toBe(3);
    const actual = new T.Color();
    const reported = [
      [0.15, 0.45, 0.75],
      [0.3, 0.6, 0.75],
      [0.55, 0.72, 0.75],
    ];
    reported.forEach((rgb, index) =>
      mesh.setColorAt(
        index,
        new T.Color().setRGB(...(rgb as [number, number, number])),
      ),
    );
    mesh.count = reported.length;
    reported.forEach((rgb, index) => {
      mesh.getColorAt(index, actual);
      actual
        .toArray()
        .forEach((channel, axis) => expect(channel).toBeCloseTo(rgb[axis], 6));
    });
    expect(mesh.count).toBe(3);
  } finally {
    mesh.geometry.dispose();
    mesh.material.dispose();
    mesh.dispose();
  }
});

import { describe, it, expect } from "vitest";
import {
  SceneAccumulator,
  decodePacket,
  jsonFrame,
  ResyncRequired,
  MAX_PACKET,
} from "../src/scene/protocol";
import { packet, voxels, scene } from "./fixtures";
describe("binary scene protocol", () => {
  it("decodes little-endian sections, padded offsets and float64 trajectories", () => {
    const decoded = decodePacket(
      packet([
        ...voxels([[1, -2, 3]]),
        {
          name: "path",
          data: new Float64Array([0.1, 0.2, 0.3, 2, 3, 4]),
          shape: [2, 3],
        },
      ]),
    );
    expect(decoded.header.sequence).toBe(1);
    expect(Array.from(decoded.arrays["voxels.keys"])).toEqual([1, -2, 3]);
    expect(decoded.arrays.path).toBeInstanceOf(Float64Array);
    expect(decoded.arrays.path[0]).toBe(0.1);
  });
  it("applies keyed upserts, deletes and full nonvoxel replacement", () => {
    const cache = new SceneAccumulator();
    cache.apply(
      packet(
        voxels([
          [0, 0, 0],
          [1, 0, 0],
        ]),
      ),
    );
    const frame = cache.apply(
      packet(
        [
          ...voxels(
            [
              [1, 0, 0],
              [2, 0, 0],
            ],
            [
              [4, 5, 6],
              [7, 8, 9],
            ],
          ),
          {
            name: "voxels.deleted",
            data: new Int32Array([0, 0, 0]),
            shape: [1, 3],
          },
          { name: "path", data: new Float64Array([1, 2, 3]), shape: [1, 3] },
        ],
        { kind: "delta", sequence: 2, base_sequence: 1 },
      ),
    );
    expect(Array.from(frame.arrays["voxels.centers"])).toEqual([
      4, 5, 6, 7, 8, 9,
    ]);
    expect(frame.arrays.path.length).toBe(3);
    const replaced = cache.apply(packet(voxels([]), { sequence: 3 }));
    expect(replaced.arrays["voxels.centers"].length).toBe(0);
    expect(replaced.arrays.path).toBeUndefined();
  });
  it("requests resync on sequence gaps or changed delta streams", () => {
    const cache = new SceneAccumulator();
    cache.apply(packet(voxels([[0, 0, 0]])));
    expect(() =>
      cache.apply(
        packet(voxels([]), { kind: "delta", sequence: 4, base_sequence: 3 }),
      ),
    ).toThrow(ResyncRequired);
    expect(() =>
      cache.apply(
        packet(voxels([]), {
          kind: "delta",
          sequence: 2,
          base_sequence: 1,
          stream_id: "another",
        }),
      ),
    ).toThrow(ResyncRequired);
    const valid = cache.apply(
      packet(voxels([[2, 0, 0]]), {
        kind: "delta",
        sequence: 2,
        base_sequence: 1,
      }),
    );
    expect(valid.arrays["voxels.centers"].length).toBe(6);
  });
  it("supports clean reconnect keyframes with reset sequence", () => {
    const cache = new SceneAccumulator();
    cache.apply(packet(voxels([[1, 0, 0]]), { sequence: 100 }));
    const frame = cache.apply(
      packet(voxels([[2, 0, 0]]), { stream_id: "new", sequence: 0 }),
    );
    expect(Array.from(frame.arrays["voxels.centers"])).toEqual([2, 0, 0]);
  });
  it("rejects overlapping, duplicate, mismatched and out-of-bounds directories", () => {
    for (const mutate of [
      (h: any) => (h.sections[1].offset = 0),
      (h: any) => (h.sections[1].name = "voxels.keys"),
      (h: any) => (h.sections[1].shape = [999, 3]),
      (h: any) => (h.sections[1].offset = 999999),
      (h: any) => (h.sections[1].name = "__proto__"),
    ])
      expect(() =>
        decodePacket(packet(voxels([[0, 0, 0]]), {}, mutate)),
      ).toThrow();
  });
  it("rejects unsupported magic/version/flags and truncated prefixes", () => {
    for (const byte of [0, 4, 6]) {
      const buffer = packet(voxels([]));
      new Uint8Array(buffer)[byte] = 42;
      expect(() => decodePacket(buffer)).toThrow();
    }
    expect(() => decodePacket(new ArrayBuffer(10))).toThrow();
    expect(() => decodePacket(new ArrayBuffer(MAX_PACKET + 1))).toThrow();
  });
  it("rejects nonfinite geometry and duplicate cell keys before cache mutation", () => {
    expect(() =>
      decodePacket(packet(voxels([[0, 0, 0]], [[NaN, 0, 0]]))),
    ).toThrow("Nonfinite");
    const cache = new SceneAccumulator();
    cache.apply(packet(voxels([[0, 0, 0]])));
    expect(() =>
      cache.apply(
        packet(
          voxels([
            [1, 0, 0],
            [1, 0, 0],
          ]),
          { kind: "delta", sequence: 2, base_sequence: 1 },
        ),
      ),
    ).toThrow("Duplicate");
    const next = cache.apply(
      packet(voxels([]), { kind: "delta", sequence: 2, base_sequence: 1 }),
    );
    expect(next.arrays["voxels.centers"].length).toBe(3);
  });
  it("handles the explicitly bounded 100000-instance stress fixture", () => {
    const n = 100000,
      keys = Array.from({ length: n }, (_, i) => [i, 0, 0]);
    const cache = new SceneAccumulator();
    const result = cache.apply(packet(voxels(keys)));
    expect(result.arrays["voxels.centers"].length).toBe(n * 3);
    expect(() =>
      cache.apply(
        packet(voxels([[n, 0, 0]]), {
          kind: "delta",
          sequence: 2,
          base_sequence: 1,
        }),
      ),
    ).toThrow("capacity");
    const result2 = cache.apply(
      packet(voxels([]), { kind: "delta", sequence: 2, base_sequence: 1 }),
    );
    expect(result2.arrays["voxels.centers"].length).toBe(n * 3);
  }, 10000);
  it("normalizes the real v1 snapshot fallback without introducing labels", () => {
    const frame = jsonFrame(scene);
    expect(frame.arrays["voxels.centers"]).toBeInstanceOf(Float32Array);
    expect(Array.from(frame.arrays.path)).toEqual([
      0,
      0,
      expect.closeTo(0.6),
      1,
      0,
      expect.closeTo(0.6),
    ]);
    expect(frame.scene).toBe(scene);
  });
});

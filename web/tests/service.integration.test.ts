import { describe, it, expect } from "vitest";
import { SceneAccumulator } from "../src/scene/protocol";
import { normalizeBounds } from "../src/scene/assets";
const base = process.env.AETHERTWIN_SERVICE_URL;
describe.skipIf(!base)("running local service integration", () => {
  it("loads bootstrap/map and decodes an actual binary keyframe without mutating the source", async () => {
    const bootstrap = await fetch(`${base}/api/v2/bootstrap`).then((r) =>
      r.json(),
    );
    expect(bootstrap.protocol_version).toBe(2);
    if (bootstrap.map_manifest_url) {
      const manifest = await fetch(
        new URL(bootstrap.map_manifest_url, base),
      ).then((r) => r.json());
      expect(normalizeBounds(manifest.bounds)).toHaveLength(2);
      expect(manifest.coordinate_frame).toBe("world_z_up");
    }
    const url = new URL(bootstrap.stream_url ?? "/ws/v2/scene", base);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const buffer = await new Promise<ArrayBuffer>((resolve, reject) => {
      const socket = new WebSocket(url);
      socket.binaryType = "arraybuffer";
      const timeout = setTimeout(() => {
        socket.close();
        reject(new Error("Timed out awaiting actual keyframe"));
      }, 10000);
      socket.onerror = () => {
        clearTimeout(timeout);
        socket.close();
        reject(new Error("WebSocket failed"));
      };
      socket.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          clearTimeout(timeout);
          socket.close();
          resolve(event.data);
        }
      };
    });
    const frame = new SceneAccumulator().apply(buffer);
    expect(frame.scene.coordinate_frame).toBe("world_z_up");
    expect(frame.arrays["voxels.centers"].length).toBeLessThanOrEqual(300000);
    expect(frame.scene.diagnostics.source_mode).toBeTruthy();
  }, 20000);
});

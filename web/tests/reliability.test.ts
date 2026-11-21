import { afterEach, describe, expect, it, vi } from "vitest";
import * as T from "three";
import { SceneStream } from "../src/lib/api";
import { unlitCaptureMaterial, watchContextLoss } from "../src/scene/SpatialRenderer";
import { livePollingEnabled } from "../src/explainer/Explainer";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SceneStream decode failures", () => {
  it("reports a malformed packet as a decode error, not a resync", () => {
    let worker: any;
    vi.stubGlobal(
      "Worker",
      class {
        onmessage: ((event: { data: unknown }) => void) | null = null;
        constructor() {
          worker = this;
        }
        postMessage() {}
        terminate() {}
      },
    );
    const sent: string[] = [];
    vi.stubGlobal(
      "WebSocket",
      class {
        static OPEN = 1;
        readyState = 1;
        binaryType = "";
        send(data: string) {
          sent.push(data);
        }
        close() {}
      },
    );
    vi.stubGlobal("window", { location: { href: "http://studio.test/" } });
    vi.stubGlobal("location", { protocol: "http:", href: "http://studio.test/" });
    const statuses: Array<[string, string | undefined]> = [];
    const stream = new SceneStream("/ws/v2/scene", () => {}, (s, m) => statuses.push([s, m]));
    worker.onmessage({ data: { type: "error", message: "Unaligned voxel sections" } });
    expect(statuses.at(-1)).toEqual(["error", "Scene decode failed: Unaligned voxel sections"]);
    expect(statuses.some(([s]) => s === "resync")).toBe(false);
    // A genuine sequence gap is still a resync that asks the server for a keyframe.
    worker.onmessage({ data: { type: "resync", message: "Scene sequence gap; awaiting keyframe" } });
    expect(statuses.at(-1)?.[0]).toBe("resync");
    expect(sent.filter((s) => s.includes("resync")).length).toBeGreaterThan(0);
    stream.close();
  });
});

describe("photo-capture materials", () => {
  it("keeps the base colour map and disposes the textures it drops", () => {
    const map = new T.Texture(),
      normal = new T.Texture(),
      roughness = new T.Texture();
    const original = new T.MeshStandardMaterial({ map, normalMap: normal, roughnessMap: roughness });
    const disposed: string[] = [];
    for (const [name, texture] of [["map", map], ["normal", normal], ["roughness", roughness]] as const)
      texture.addEventListener("dispose", () => disposed.push(name));
    const unlit = unlitCaptureMaterial(original, false);
    expect(unlit).toBeInstanceOf(T.MeshBasicMaterial);
    expect(unlit.map).toBe(map);
    expect(disposed.sort()).toEqual(["normal", "roughness"]);
  });
});

describe("WebGL context loss", () => {
  it("prevents the default so the context can be restored, and reports both events", () => {
    const canvas = new EventTarget();
    const seen: string[] = [];
    const stop = watchContextLoss(canvas, {
      lost: () => seen.push("lost"),
      restored: () => seen.push("restored"),
    });
    const lost = new Event("webglcontextlost", { cancelable: true });
    canvas.dispatchEvent(lost);
    expect(lost.defaultPrevented).toBe(true);
    canvas.dispatchEvent(new Event("webglcontextrestored"));
    expect(seen).toEqual(["lost", "restored"]);
    stop();
    canvas.dispatchEvent(new Event("webglcontextlost", { cancelable: true }));
    expect(seen).toHaveLength(2);
  });
});

describe("explainer live-state polling", () => {
  it("never polls the service from the standalone explainer", () => {
    expect(livePollingEnabled(true)).toBe(false);
    expect(livePollingEnabled(false)).toBe(true);
  });
});

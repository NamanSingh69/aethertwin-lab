import { afterEach, describe, expect, it, vi } from "vitest";
import { gzipSync as nodeGzip } from "node:zlib";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ResyncRequired, SceneAccumulator } from "../src/scene/protocol";
import {
  chunkIndexForFrame,
  gunzip,
  parseRecords,
  parseShowcaseIndex,
  provenance,
  resolveShowcaseBase,
  type ShowcaseIndex,
} from "../src/showcase/format";
import { ShowcaseStream, type DecoderPort } from "../src/showcase/ShowcaseStream";
import { startShowcase } from "../src/showcase/session";
import { ShowcaseBanner } from "../src/showcase/ShowcaseBanner";
import type { SceneFrame } from "../src/lib/types";
import { packet, voxels } from "./fixtures";

/**
 * Chunk decompression runs on the platform's thread pool, so it finishes after
 * an unknown number of event-loop turns. The fake clock awaits these promises
 * instead of guessing a turn count (which failed on a loaded machine).
 */
const inflight = vi.hoisted(() => new Set<Promise<unknown>>());
vi.mock("../src/showcase/format", async (original) => {
  const actual = await original<typeof import("../src/showcase/format")>();
  return {
    ...actual,
    gunzip: (data: ArrayBuffer) => {
      const work = actual.gunzip(data);
      const tracked = work.then(
        () => void inflight.delete(tracked),
        () => void inflight.delete(tracked),
      );
      inflight.add(tracked);
      return work;
    },
  };
});

const BASE = "https://showcase.test/showcase/";
/** A copy: a Node Buffer may be a view into a larger shared pool. */
const gzipSync = (bytes: Uint8Array) => Uint8Array.from(nodeGzip(bytes));

/** One scene protocol v2 packet: a keyframe, or a delta on the previous sequence. */
function scenePacket(sequence: number, keyframe: boolean) {
  return packet(voxels([[sequence, 0, 0]]), {
    kind: keyframe ? "keyframe" : "delta",
    sequence,
    base_sequence: keyframe ? null : sequence - 1,
    stream_id: "recorded",
  });
}

/** Records as the exporter writes them: uint32 LE length, float64 LE t_s, packet bytes. */
function records(entries: Array<[number, ArrayBuffer]>) {
  const total = entries.reduce((sum, [, p]) => sum + 12 + p.byteLength, 0);
  const out = new Uint8Array(total),
    view = new DataView(out.buffer);
  let offset = 0;
  for (const [t, p] of entries) {
    view.setUint32(offset, p.byteLength, true);
    view.setFloat64(offset + 4, t, true);
    out.set(new Uint8Array(p), offset + 12);
    offset += 12 + p.byteLength;
  }
  return out;
}

/** Two chunks of three frames each (keyframe first), frames every 100 ms. */
function fixture() {
  const times = [0, 0.1, 0.2, 0.3, 0.4, 0.5];
  const packets = times.map((_, i) => scenePacket(i + 1, i % 3 === 0));
  const chunk = (start: number) =>
    gzipSync(records([0, 1, 2].map((k) => [times[start + k], packets[start + k]])));
  const files = new Map<string, Uint8Array>([
    [`${BASE}chunks/0000.bin.gz`, chunk(0)],
    [`${BASE}chunks/0001.bin.gz`, chunk(3)],
  ]);
  const index: ShowcaseIndex = parseShowcaseIndex({
    format: "aethertwin-showcase",
    version: 1,
    label: "Recorded replay - not live",
    recorded_at: "2026-11-05T09:30:00Z",
    duration_s: 0.6,
    frame_count: 6,
    source: {
      recording: "wifi_stationary_60s",
      commit: "e69a47d0123456789",
      calibration: "placeholder",
      lidar_height: "assumed",
      hardware_verified: false,
      note: "Parked bench, not hardware-verified.",
    },
    bootstrap: "bootstrap.json",
    assets_prefix: "assets/rev1/",
    chunks: [
      { file: "chunks/0000.bin.gz", first_frame: 0, frames: 3, start_s: 0, end_s: 0.2, bytes: files.get(`${BASE}chunks/0000.bin.gz`)!.byteLength },
      { file: "chunks/0001.bin.gz", first_frame: 3, frames: 3, start_s: 0.3, end_s: 0.5, bytes: files.get(`${BASE}chunks/0001.bin.gz`)!.byteLength },
    ],
  });
  files.set(`${BASE}index.json`, new TextEncoder().encode(JSON.stringify(index)));
  files.set(
    `${BASE}bootstrap.json`,
    new TextEncoder().encode(
      JSON.stringify({
        role: "operator",
        csrf_token: "must-not-survive",
        capabilities: { pause: true, select_source: true },
        source_mode: "live",
        hardware_status: "verified",
        map_revision: "rev1",
        map_manifest_url: "/api/v2/assets/rev1/manifest.json",
        stream_url: "/ws/v2/scene",
      }),
    ),
  );
  const fetchImpl = vi.fn(async (input: RequestInfo | URL) => {
    const body = files.get(String(input));
    return body
      ? new Response(body.slice().buffer)
      : new Response("missing", { status: 404 });
  });
  return { index, fetchImpl, packets, times };
}

/** Manually advanced clock for deterministic playback timing. */
function fakeClock() {
  let now = 0,
    id = 0;
  const timers = new Map<number, { at: number; fn: () => void }>();
  const flush = async () => {
    // setImmediate, not setTimeout: Windows timers tick in ~15 ms steps.
    for (;;) {
      for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r));
      if (!inflight.size) break;
      await Promise.all([...inflight]);
    }
  };
  return {
    now: () => now,
    schedule: (fn: () => void, ms: number) => {
      timers.set(++id, { at: now + Math.max(0, ms), fn });
      return id;
    },
    cancel: (handle: unknown) => void timers.delete(handle as number),
    flush,
    async advance(ms: number) {
      const target = now + ms;
      await flush();
      for (;;) {
        const due = [...timers.entries()]
          .filter(([, t]) => t.at <= target)
          .sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = due[1].at;
        due[1].fn();
        await flush();
      }
      now = target;
      await flush();
    },
  };
}

/** The real accumulator behind an asynchronous, worker-shaped port. */
function fakeDecoder(): DecoderPort & { posted: any[] } {
  const accumulator = new SceneAccumulator();
  const port: DecoderPort & { posted: any[] } = {
    posted: [],
    onmessage: null,
    postMessage(message: any) {
      port.posted.push(message);
      if (message.type === "reset") {
        accumulator.reset();
        return;
      }
      let reply: any;
      try {
        reply = { type: "frame", frame: accumulator.apply(message.buffer) };
      } catch (error) {
        reply = {
          type: error instanceof ResyncRequired ? "resync" : "error",
          message: String(error),
        };
      }
      queueMicrotask(() => port.onmessage?.({ data: reply } as MessageEvent));
    },
    terminate() {},
  };
  return port;
}

function player(loop = true) {
  const { index, fetchImpl } = fixture();
  const clock = fakeClock();
  const frames: SceneFrame[] = [];
  const statuses: string[] = [];
  const decoder = fakeDecoder();
  const stream = new ShowcaseStream(
    BASE,
    index,
    (frame) => frames.push(frame),
    (status) => statuses.push(status),
    {
      fetch: fetchImpl as unknown as typeof fetch,
      createDecoder: () => decoder,
      now: clock.now,
      schedule: clock.schedule,
      cancel: clock.cancel,
      loop,
    },
  );
  return { stream, clock, frames, statuses, decoder, fetchImpl };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("showcase format", () => {
  it("gunzips a multi-record chunk and returns byte-identical packets in order", async () => {
    const a = scenePacket(1, true),
      b = scenePacket(2, false),
      c = scenePacket(3, false);
    const gz = gzipSync(records([[0, a], [0.125, b], [0.5, c]]));
    const parsed = parseRecords(await gunzip(gz.slice().buffer));
    expect(parsed.map((r) => r.t)).toEqual([0, 0.125, 0.5]);
    expect(Array.from(parsed[1].packet)).toEqual(Array.from(new Uint8Array(b)));
    expect(parsed[2].packet.byteLength).toBe(c.byteLength);
  });
  it("accepts an already-decompressed chunk (server-side Content-Encoding)", async () => {
    const raw = records([[0, scenePacket(1, true)]]);
    expect(parseRecords(await gunzip(raw.slice().buffer))).toHaveLength(1);
  });
  it("rejects truncated records and backwards timestamps", () => {
    const raw = records([[0, scenePacket(1, true)]]);
    expect(() => parseRecords(raw.subarray(0, raw.byteLength - 1))).toThrow(/truncated/i);
    const backwards = records([[1, scenePacket(1, true)], [0.5, scenePacket(2, false)]]);
    expect(() => parseRecords(backwards)).toThrow(/backwards/i);
  });
  it("validates the index format and version", () => {
    const { index } = fixture();
    expect(() => parseShowcaseIndex({ ...index, format: "other" })).toThrow();
    expect(() => parseShowcaseIndex({ ...index, version: 2 })).toThrow();
    expect(() => parseShowcaseIndex({ ...index, chunks: [] })).toThrow();
    expect(chunkIndexForFrame(index, 0)).toBe(0);
    expect(chunkIndexForFrame(index, 4)).toBe(1);
    expect(chunkIndexForFrame(index, 99)).toBe(1);
  });
  it("resolves the data base: default, build-time setting, and same-origin ?data= only", () => {
    const href = "https://site.test/studio/index.html";
    expect(resolveShowcaseBase("", href)).toBe("https://site.test/studio/showcase/");
    expect(resolveShowcaseBase("?data=demo2", href)).toBe("https://site.test/studio/demo2/");
    expect(resolveShowcaseBase("?data=https://evil.example/x/", href)).toBe(
      "https://site.test/studio/showcase/",
    );
    expect(resolveShowcaseBase("", href, "https://cdn.test/replay")).toBe("https://cdn.test/replay/");
  });
});

describe("ShowcaseStream", () => {
  it("feeds recorded packets to the decoder at their recorded timing, in timestamp order", async () => {
    const { stream, clock, frames, statuses } = player();
    await clock.flush();
    expect(frames.map((f) => f.sequence)).toEqual([1]);
    expect(statuses).toContain("replay");
    await clock.advance(50);
    expect(frames).toHaveLength(1);
    await clock.advance(60);
    expect(frames.map((f) => f.sequence)).toEqual([1, 2]);
    await clock.advance(400);
    expect(frames.map((f) => f.sequence)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(stream.state.frame).toBe(5);
    stream.close();
  });
  it("fetches chunks lazily, one ahead", async () => {
    const { stream, clock, fetchImpl } = player();
    await clock.flush();
    const urls = fetchImpl.mock.calls.map(([url]) => String(url));
    expect(urls).toEqual([`${BASE}chunks/0000.bin.gz`, `${BASE}chunks/0001.bin.gz`]);
    stream.close();
  });
  it("seeks to the keyframe that starts the chunk holding the frame", async () => {
    const { stream, clock, frames, decoder } = player();
    await clock.flush();
    stream.seek(4);
    await clock.flush();
    expect(decoder.posted.some((m) => m.type === "reset")).toBe(true);
    const next = frames[frames.length - 1];
    expect(next.sequence).toBe(4);
    expect(stream.state.frame).toBe(3);
    await clock.advance(250);
    expect(frames.slice(-3).map((f) => f.sequence)).toEqual([4, 5, 6]);
    stream.close();
  });
  it("loops back to the first keyframe, or ends paused without looping", async () => {
    const looping = player(true);
    await looping.clock.advance(700);
    expect(looping.frames.map((f) => f.sequence).slice(0, 7)).toEqual([1, 2, 3, 4, 5, 6, 1]);
    looping.stream.close();
    const once = player(false);
    await once.clock.advance(1500);
    expect(once.frames.map((f) => f.sequence)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(once.stream.state.paused).toBe(true);
    expect(once.stream.state.ended).toBe(true);
    once.stream.close();
  });
  it("pauses, steps and resumes; observation age follows the recorded timeline", async () => {
    const { stream, clock, frames } = player();
    await clock.advance(120);
    stream.pause();
    const count = frames.length;
    await clock.advance(10_000);
    expect(frames).toHaveLength(count);
    // Wall-clock time passed; recorded time did not, so nothing is stale.
    expect(stream.observationAge()).toBeLessThan(0.1);
    stream.step();
    await clock.flush();
    expect(frames).toHaveLength(count + 1);
    stream.play();
    await clock.advance(1000);
    expect(frames.length).toBeGreaterThan(count + 1);
    stream.close();
  });
  it("reports a decode failure as an error, not a resync", async () => {
    const { index, fetchImpl } = fixture();
    const clock = fakeClock();
    const statuses: Array<[string, string | undefined]> = [];
    const decoder = fakeDecoder();
    const broken = { ...decoder, postMessage(message: any) {
      if (message.type === "decode") queueMicrotask(() => broken.onmessage?.({ data: { type: "error", message: "bad packet" } } as MessageEvent));
    } } as DecoderPort;
    const stream = new ShowcaseStream(BASE, index, () => {}, (s, m) => statuses.push([s, m]), {
      fetch: fetchImpl as unknown as typeof fetch,
      createDecoder: () => broken,
      now: clock.now,
      schedule: clock.schedule,
      cancel: clock.cancel,
    });
    await clock.flush();
    expect(statuses.some(([s, m]) => s === "error" && /bad packet/.test(m ?? ""))).toBe(true);
    expect(statuses.some(([s]) => s === "resync")).toBe(false);
    stream.close();
  });
});

describe("showcase session", () => {
  it("makes no request to /api or /ws and keeps only allowlisted bootstrap fields", async () => {
    const { fetchImpl } = fixture();
    vi.stubGlobal("fetch", fetchImpl);
    const socket = vi.fn(() => {
      throw new Error("WebSocket must not be opened in showcase mode");
    });
    vi.stubGlobal("WebSocket", socket);
    const bootstraps: any[] = [];
    const frames: SceneFrame[] = [];
    const clock = fakeClock();
    const session = await startShowcase({
      base: BASE,
      onBootstrap: (b) => bootstraps.push(b),
      onFrame: (f) => frames.push(f),
      onStatus: () => {},
      onPlayback: () => {},
      createDecoder: fakeDecoder,
      clock,
    });
    await clock.advance(700);
    session.stream.close();
    expect(frames.length).toBeGreaterThan(0);
    expect(socket).not.toHaveBeenCalled();
    const urls = fetchImpl.mock.calls.map(([url]) => String(url));
    expect(urls.length).toBeGreaterThan(2);
    for (const url of urls) {
      expect(url.startsWith(BASE)).toBe(true);
      expect(url).not.toMatch(/\/(api|ws)\//);
    }
    const bootstrap = bootstraps[0];
    expect(bootstrap.role).toBe("viewer");
    expect(bootstrap.source_mode).toBe("replay");
    expect(bootstrap.hardware_status).toBe("not_tested");
    expect(bootstrap.capabilities).toEqual({});
    expect(bootstrap.csrf_token).toBeUndefined();
    expect(bootstrap.stream_url).toBeUndefined();
    expect(bootstrap.map_manifest_url).toBe(`${BASE}assets/rev1/manifest.json`);
    expect(bootstrap.replay.frame_count).toBe(6);
  });
});

describe("showcase banner", () => {
  it("says it is a recorded replay, not live, with the provenance", () => {
    const { index } = fixture();
    const html = renderToStaticMarkup(createElement(ShowcaseBanner, { index }));
    expect(html).toContain("Recorded replay of 2026-11-05 09:30 UTC, not live");
    expect(html).toContain("wifi_stationary_60s");
    expect(html).toContain("Calibration: placeholder");
    expect(html).toContain("LiDAR height: assumed");
    expect(html).toContain("Hardware not verified");
    expect(html).not.toMatch(/<button/);
    const text = provenance(index);
    expect(text.headline).toMatch(/not live/);
  });
  it("still says not live before the index has loaded or when it fails", () => {
    expect(renderToStaticMarkup(createElement(ShowcaseBanner, { index: null }))).toContain("not live");
    expect(
      renderToStaticMarkup(createElement(ShowcaseBanner, { index: null, error: "404" })),
    ).toContain("not live");
  });
  it("never shows an unknown calibration or LiDAR height as measured", () => {
    const { index } = fixture();
    const odd = { ...index, source: { ...index.source, calibration: "great" as any, lidar_height: "" as any } };
    const text = provenance(odd).details.join(" ");
    expect(text).toContain("Calibration: not stated");
    expect(text).toContain("LiDAR height: not stated");
  });
});

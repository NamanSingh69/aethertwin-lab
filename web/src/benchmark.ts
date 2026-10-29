import { SpatialRenderer, type RenderMetrics } from "./scene/SpatialRenderer";
import type { SceneFrame } from "./lib/types";
const host = document.querySelector<HTMLElement>("#canvas")!,
  output = document.querySelector<HTMLElement>("#results")!,
  start = document.querySelector<HTMLButtonElement>("#start")!,
  download = document.querySelector<HTMLButtonElement>("#export")!;
let renderer: SpatialRenderer | null = null,
  report: Record<string, unknown> | null = null;
let cleanupVisibility = () => {};
const params = new URLSearchParams(location.search);
/** Deterministic pseudo-random sequence so every run sees identical frames. */
function random(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}
function fixture(): SceneFrame {
  const n = 100000,
    centers = new Float32Array(n * 3),
    colors = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    centers[i * 3] = ((i % 50) - 25) * 0.13;
    centers[i * 3 + 1] = ((Math.floor(i / 50) % 50) - 25) * 0.13;
    centers[i * 3 + 2] = Math.floor(i / 2500) * 0.13;
    colors[i * 3] = 0.15 + (i % 7) / 15;
    colors[i * 3 + 1] = 0.45 + (i % 5) / 15;
    colors[i * 3 + 2] = 0.75;
  }
  return {
    scene: {
      schema_version: 1,
      frame_id: 0,
      timestamp: 0,
      fps: 0,
      coordinate_frame: "world_z_up",
      tracks: [],
      geofences: [],
      alerts: [],
      trajectory_status: "not_requested",
      sensor_pose_world: [
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, 1, 0],
        [0, 0, 0, 1],
      ],
      diagnostics: {
        source_mode: "test_injection",
        synthetic: true,
        fixture: "100000_static_render_instances",
      },
    },
    arrays: {
      "voxels.centers": centers,
      "voxels.colors": colors,
      "voxels.flow": new Float32Array(n * 3),
    },
    sequence: 0,
    streamId: "explicit-render-stress-fixture",
    receivedAt: Date.now(),
  };
}
/**
 * Streaming fixture: the static voxels plus every overlay at the renderer's
 * caps, regenerated per tick so per-frame update cost (allocation, upload,
 * garbage collection) is part of the measurement.
 */
function streamingFrame(base: SceneFrame, tick: number): SceneFrame {
  const rand = random(tick + 1),
    t = tick / 30,
    n = 100000,
    flow = new Float32Array(n * 3);
  for (let i = 0; i < n * 3; i += 3 * 97) {
    flow[i] = 0.05 * Math.sin(t + i);
    flow[i + 1] = 0.05 * Math.cos(t + i);
  }
  const tracks = Array.from({ length: 48 }, (_, id) => {
    const x = 5 * Math.sin(t * 0.3 + id),
      y = 4 * Math.cos(t * 0.2 + id * 1.7),
      h = 0.4 + (id % 4) * 0.3;
    const corners = Array.from({ length: 8 }, (_, c) => [
      x + (c & 1 ? 0.3 : -0.3),
      y + (c & 2 ? 0.25 : -0.25),
      c & 4 ? h : 0,
    ]) as [number, number, number][];
    return {
      track_id: id,
      semantic_class: id % 6,
      semantic_name: "fixture",
      class_probs: [],
      confidence: 0.9,
      position: [x, y, h / 2] as [number, number, number],
      velocity: [0, 0, 0] as [number, number, number],
      obb_corners: corners,
      predicted_trajectory: Array.from(
        { length: 12 },
        (_, k) => [x + k * 0.1, y + k * 0.05, h / 2] as [number, number, number],
      ),
      ttc_seconds: null,
      breached_geofences: id % 11 === 0 ? ["fixture"] : [],
      state_kind: id % 5 === 0 ? ("predicted" as const) : ("measurement_updated" as const),
    };
  });
  const geofences = Array.from({ length: 8 }, (_, z) => ({
    name: `fixture-${z}`,
    center: [z - 4, (z % 3) - 1, 0.5] as [number, number, number],
    half_extents: [0.4, 0.3, 0.5] as [number, number, number],
    yaw: z * 0.2,
    severity_level: 1,
    is_breached: z === tick % 8,
  }));
  const path = new Float64Array(200 * 3);
  for (let i = 0; i < 200; i++) {
    path[i * 3] = -5 + i * 0.05;
    path[i * 3 + 1] = Math.sin(i * 0.05 + t);
    path[i * 3 + 2] = 0.05;
  }
  const cells = 20000,
    riskCenters = new Float32Array(cells * 3),
    risk = new Float32Array(cells);
  for (let i = 0; i < cells; i++) {
    riskCenters[i * 3] = ((i % 200) - 100) * 0.06;
    riskCenters[i * 3 + 1] = (Math.floor(i / 200) - 50) * 0.06;
    risk[i] = rand();
  }
  const traffic = new Float32Array(64 * 64).map(() => rand());
  const rgbd = new Float32Array(20000 * 6);
  for (let i = 0; i < 20000; i++) {
    rgbd[i * 6] = 2 + rand();
    rgbd[i * 6 + 1] = -1 + 2 * rand();
    rgbd[i * 6 + 2] = 2 * rand();
    rgbd[i * 6 + 3] = rgbd[i * 6 + 4] = rgbd[i * 6 + 5] = rand();
  }
  return {
    ...base,
    scene: {
      ...base.scene,
      frame_id: tick,
      tracks,
      geofences,
      traffic_heatmap: { bounds_xy: [-6, 6, -5, 5], shape: [64, 64] },
      diagnostics: {
        ...base.scene.diagnostics,
        fixture: "100000_instances_streaming_all_overlays_30hz",
      },
    },
    arrays: {
      ...base.arrays,
      "voxels.flow": flow,
      path,
      "floor_risk.centers": riskCenters,
      "floor_risk.risk": risk,
      "traffic_heatmap.values": traffic,
      gemini_inlay: rgbd,
    },
    sequence: tick,
  };
}
function percentiles(values: number[]) {
  const ordered = [...values].sort((a, b) => a - b);
  const at = (p: number) =>
    ordered[Math.min(ordered.length - 1, Math.floor(ordered.length * p))] ??
    null;
  return { samples: values.length, p50: at(0.5), p95: at(0.95), p99: at(0.99) };
}
start.onclick = () => {
  cleanupVisibility();
  let wasHidden = document.hidden;
  const changed = () => {
    wasHidden ||= document.hidden;
  };
  document.addEventListener("visibilitychange", changed);
  cleanupVisibility = () =>
    document.removeEventListener("visibilitychange", changed);
  renderer?.dispose();
  start.disabled = true;
  download.disabled = true;
  const duration = Number(
    document.querySelector<HTMLSelectElement>("#duration")!.value,
  );
  const streaming =
    document.querySelector<HTMLSelectElement>("#mode")!.value === "streaming";
  const began = performance.now();
  const windows: RenderMetrics[] = [];
  const updateSamples: number[] = [];
  let done = false,
    ticker = 0;
  try {
    renderer = new SpatialRenderer(
      host,
      () => {},
      () => {},
      (metrics) => {
        if (done) return;
        const elapsed = (performance.now() - began) / 1000;
        if (elapsed > 5) windows.push(metrics);
        const rates = windows.map((m) => m.fps);
        report = {
          test: streaming
            ? "explicit_streaming_100000_instance_all_overlay_fixture"
            : "explicit_static_100000_instance_render_fixture",
          update_rate_hz: streaming ? 30 : 0,
          update_cpu_ms_after_warmup: streaming
            ? percentiles(updateSamples)
            : null,
          source: "synthetic_test_injection",
          runtime_perception_benchmarked: false,
          drawing_buffer: metrics.drawingBuffer,
          css_canvas: [host.clientWidth, host.clientHeight],
          pixel_ratio: 1,
          elapsed_seconds: elapsed,
          warmup_seconds: 5,
          sampled_one_second_windows: windows.length,
          mean_render_fps: rates.length
            ? rates.reduce((a, b) => a + b, 0) / rates.length
            : null,
          minimum_window_fps: rates.length ? Math.min(...rates) : null,
          frame_interval_ms_last_1200: {
            p50: metrics.frameMsP50,
            p95: metrics.frameMsP95,
            p99: metrics.frameMsP99,
          },
          instances: metrics.instances,
          draw_calls: metrics.drawCalls,
          triangles: metrics.triangles,
          gpu_geometries: metrics.geometries,
          last_update_cpu_ms: metrics.updateMs,
          last_render_submit_cpu_ms: metrics.renderSubmitMs,
          device: renderer?.deviceInfo(),
          completed: elapsed >= duration,
          page_visibility: document.visibilityState,
          hidden_during_measurement: wasHidden,
        };
        output.textContent = JSON.stringify(report, null, 2);
        if (elapsed >= duration) {
          done = true;
          clearInterval(ticker);
          renderer?.freeze();
          (window as unknown as { benchmarkReport: unknown }).benchmarkReport =
            report;
          start.disabled = false;
          download.disabled = false;
          cleanupVisibility();
        }
      },
      { fixedBuffer: [1920, 1080], pixelRatio: 1 },
    );
    const base = fixture();
    renderer.updateFrame(base);
    if (streaming) {
      let tick = 0;
      ticker = window.setInterval(() => {
        if (done || !renderer) return;
        const frame = streamingFrame(base, ++tick),
          before = performance.now();
        renderer.updateFrame(frame);
        if ((performance.now() - began) / 1000 > 5)
          updateSamples.push(performance.now() - before);
      }, 1000 / 30);
    }
  } catch (error) {
    clearInterval(ticker);
    cleanupVisibility();
    output.textContent = String(error);
    start.disabled = false;
  }
};
download.onclick = () => {
  if (!report) return;
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "aethertwin-renderer-benchmark.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
for (const id of ["mode", "duration"]) {
  const value = params.get(id),
    select = document.querySelector<HTMLSelectElement>(`#${id}`)!;
  if (value && [...select.options].some((o) => o.value === value))
    select.value = value;
}
if (params.get("autostart") === "1") start.click();
window.addEventListener(
  "pagehide",
  () => {
    cleanupVisibility();
    renderer?.dispose();
  },
  { once: true },
);

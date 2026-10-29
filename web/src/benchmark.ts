import { SpatialRenderer, type RenderMetrics } from "./scene/SpatialRenderer";
import type { SceneFrame } from "./lib/types";
const host = document.querySelector<HTMLElement>("#canvas")!,
  output = document.querySelector<HTMLElement>("#results")!,
  start = document.querySelector<HTMLButtonElement>("#start")!,
  download = document.querySelector<HTMLButtonElement>("#export")!;
let renderer: SpatialRenderer | null = null,
  report: Record<string, unknown> | null = null;
let cleanupVisibility = () => {};
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
  const began = performance.now();
  const windows: RenderMetrics[] = [];
  let done = false;
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
          test: "explicit_static_100000_instance_render_fixture",
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
          renderer?.freeze();
          start.disabled = false;
          download.disabled = false;
          cleanupVisibility();
        }
      },
      { fixedBuffer: [1920, 1080], pixelRatio: 1 },
    );
    renderer.updateFrame(fixture());
  } catch (error) {
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
window.addEventListener(
  "pagehide",
  () => {
    cleanupVisibility();
    renderer?.dispose();
  },
  { once: true },
);

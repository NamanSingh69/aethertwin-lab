// Drive /benchmark.html in headed Chrome on the real GPU and save each run.
// Usage: node tools/run-benchmark.mjs --url http://127.0.0.1:4173 --mode streaming
//          --duration 60 --runs 3 [--uncapped] [--label zephyrus-rtx4070] --out ../artifacts/x.json
// Never pass SwiftShader flags here: software WebGL says nothing about GPU cost.
import { writeFileSync } from "node:fs";
import { parseArgs } from "node:util";
import { chromium } from "@playwright/test";

const { values: args } = parseArgs({
  options: {
    url: { type: "string", default: "http://127.0.0.1:4173" },
    mode: { type: "string", default: "static" },
    quality: { type: "string", default: "balanced" },
    duration: { type: "string", default: "60" },
    runs: { type: "string", default: "3" },
    uncapped: { type: "boolean", default: false },
    label: { type: "string" },
    out: { type: "string" },
  },
});
const { url, mode, uncapped } = args,
  duration = Number(args.duration),
  runs = Number(args.runs);
if (!["static", "streaming"].includes(mode) || ![15, 60].includes(duration) || !(runs >= 1))
  throw new Error("--mode static|streaming, --duration 15|60, --runs >= 1");
// Windows throttles requestAnimationFrame to ~1 Hz when another window covers
// the browser, even though the page still reports itself visible.
const flags = [
  "--force_high_performance_gpu",
  "--ignore-gpu-blocklist",
  "--disable-features=CalculateNativeWinOcclusion",
];
if (uncapped) flags.push("--disable-gpu-vsync", "--disable-frame-rate-limit");
const results = [];
// Throttled pages get requestAnimationFrame about once per second; real frames
// here stay far below 500 ms, so count gaps that long from an independent loop
// (after the page's 5 s warm-up, which includes fixture generation).
const watchGaps = () => {
  let last = performance.now();
  const began = last;
  window.throttleGaps = 0;
  const tick = (now) => {
    if (now - began > 6000 && now - last > 500) window.throttleGaps++;
    last = now;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};
for (let attempt = 1, run = 0; run < runs && attempt <= runs * 2; attempt++) {
  const browser = await chromium.launch({ channel: "chrome", headless: false, args: flags });
  try {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await page.addInitScript(watchGaps);
    await page.goto(`${url}/benchmark.html?mode=${mode}&quality=${args.quality}&duration=${duration}&autostart=1`);
    await page.bringToFront();
    const report = await page.waitForFunction(() => window.benchmarkReport, null, {
      timeout: (duration + 60) * 1000,
      polling: 1000,
    });
    const value = await report.jsonValue();
    const gaps = await page.evaluate(() => window.throttleGaps);
    const throttled = value.hidden_during_measurement || gaps > 0;
    // A cinematic run only counts if post-processing actually ran.
    const effectsMissing = args.quality === "cinematic" && !value.effects?.postprocessing;
    if (!throttled && !effectsMissing) run++;
    results.push({
      attempt,
      valid: !throttled && !effectsMissing,
      throttle_gaps_over_500ms: gaps,
      effects_missing: effectsMissing,
      ...value,
    });
    const last = results.at(-1);
    console.log(
      (throttled ? "INVALID (throttled) " : effectsMissing ? "INVALID (no effects) " : "") +
      `run ${attempt}: fps ${last.mean_render_fps?.toFixed(1)} p95 ${last.frame_interval_ms_last_1200.p95?.toFixed(2)} ms` +
        (last.update_cpu_ms_after_warmup ? ` update p95 ${last.update_cpu_ms_after_warmup.p95?.toFixed(2)} ms` : "") +
        ` hidden ${last.hidden_during_measurement} gpu ${last.device?.renderer}`,
    );
  } finally {
    await browser.close();
  }
}
const output = {
  label: args.label ?? null,
  recorded_at: new Date().toISOString(),
  browser: "Chrome stable (Playwright channel=chrome), headed",
  vsync: uncapped ? "disabled (--disable-gpu-vsync --disable-frame-rate-limit)" : "enabled",
  mode,
  quality: args.quality,
  duration_seconds: duration,
  valid_runs: results.filter((r) => r.valid).length,
  runs: results,
};
if (args.out) writeFileSync(args.out, JSON.stringify(output, null, 2) + "\n");
else console.log(JSON.stringify(output, null, 2));

// Captures the landing page's own images from a running showcase preview:
//   node tools/capture-landing.mjs http://127.0.0.1:4180 [outDir]
// Writes studio.jpg (the studio replaying the export) and og.jpg (link preview)
// into src/landing/img/, plus full-page review shots into outDir when given.
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { chromium } from "@playwright/test";

const [base = "http://127.0.0.1:4180", review] = process.argv.slice(2);
const img = resolve(import.meta.dirname, "..", "src", "landing", "img");
const browser = await chromium.launch({
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"],
});
try {
  const studio = await browser.newPage({ viewport: { width: 1440, height: 810 }, deviceScaleFactor: 1 });
  await studio.goto(`${base}/replay.html`);
  await studio.locator(".connection-pill", { hasText: "Recorded replay" }).waitFor({ timeout: 60_000 });
  // Let the map tiles finish streaming before the picture.
  await studio.waitForFunction(() => !/Loading measured map/.test(document.body.innerText), null, { timeout: 120_000 }).catch(() => {});
  await studio.waitForTimeout(4000);
  await studio.screenshot({ path: resolve(img, "studio.jpg"), type: "jpeg", quality: 82 });
  await studio.close();

  const og = await browser.newPage({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1, reducedMotion: "reduce" });
  await og.goto(`${base}/`);
  // Link-preview crop: wordmark, headline and the plate with its points. The
  // lede and buttons are hidden for this picture only (unreadable at card size).
  await og.addStyleTag({ content: ".topbar nav, .topbar .btn, .hero-row, #plate-hint { display: none !important; } .plate { margin-top: 28px !important; }" });
  await og.waitForTimeout(1500);
  await og.screenshot({ path: resolve(img, "og.jpg"), type: "jpeg", quality: 84 });
  await og.close();

  if (review) {
    mkdirSync(review, { recursive: true });
    // Viewport-sized shots down the page (a full-page capture distorts svh units).
    for (const [name, viewport] of [["desktop", { width: 1440, height: 900 }], ["laptop", { width: 1100, height: 760 }], ["mobile", { width: 390, height: 844 }]]) {
      const page = await browser.newPage({ viewport, deviceScaleFactor: 1, reducedMotion: "reduce" });
      await page.goto(`${base}/`);
      await page.waitForTimeout(1200);
      const total = await page.evaluate(() => document.documentElement.scrollHeight);
      for (let y = 0, shot = 0; y < total; y += viewport.height, shot++) {
        await page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), y);
        await page.waitForTimeout(250);
        await page.screenshot({ path: resolve(review, `${name}-${String(shot).padStart(2, "0")}.png`) });
      }
      await page.close();
    }
  }
} finally {
  await browser.close();
}

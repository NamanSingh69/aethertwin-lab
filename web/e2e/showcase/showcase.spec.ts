// The static showcase build (vite.showcase.config.ts) replaying the committed
// fixture export in e2e/showcase/fixture (tools/export_showcase.py on
// assets/sample_recording, a TEST EXPORT). These tests import @playwright/test
// directly, never e2e/support/test.ts: its AUTO `service` fixture would install
// the mock service, and the showcase must work with no service at all.
import { readFileSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { test as base, expect, type Page } from "@playwright/test";

interface FixtureIndex {
  recorded_at: string;
  frame_count: number;
  source: { recording: string; commit: string; calibration: string; lidar_height: string; note: string };
  chunks: Array<{ first_frame: number; frames: number }>;
}
const index = JSON.parse(
  readFileSync(new URL("./fixture/index.json", import.meta.url), "utf8"),
) as FixtureIndex;

/** A request or socket to the command service is a bug in a static replay. */
const SERVICE_PATH = /^\/(api|ws)(\/|$)/;

const test = base.extend<{ guard: { service: string[]; sockets: string[]; errors: string[] } }>({
  // Automatic for every showcase test: no service traffic, no WebSocket, no console error.
  guard: [
    async ({ page, context }, use) => {
      const guard = { service: [] as string[], sockets: [] as string[], errors: [] as string[] };
      // The context sees requests from every page and worker (the decoder runs in a worker).
      context.on("request", (request) => {
        const url = new URL(request.url());
        if (SERVICE_PATH.test(url.pathname) || /^wss?:$/.test(url.protocol)) guard.service.push(request.url());
      });
      page.on("websocket", (socket) => guard.sockets.push(socket.url()));
      page.on("pageerror", (error) => guard.errors.push(`pageerror: ${error.message}`));
      page.on("console", (message) => {
        if (message.type() === "error") guard.errors.push(message.text());
      });
      await use(guard);
      expect(guard.service, "requests to /api or /ws").toEqual([]);
      expect(guard.sockets, "WebSockets opened").toEqual([]);
      expect(guard.errors, "console errors").toEqual([]);
    },
    { auto: true },
  ],
});

const frameId = async (page: Page) =>
  Number(await page.locator(".viewport-stats strong").first().innerText());
const pill = (page: Page) => page.locator(".connection-pill");

async function openReplay(page: Page) {
  // The site root is the landing page; the studio replay lives at /replay (replay.html).
  await page.goto("/replay");
  await expect(pill(page)).toHaveText("Recorded replay");
  await expect(page.locator(".viewport-stats strong").first()).toHaveText(/^\d{5}$/);
}

test.describe("static showcase", () => {
  test("shows the permanent not-live banner with the export's provenance", async ({ page }) => {
    await openReplay(page);
    await expect(page).toHaveTitle(/Recorded replay, not live/);
    const banner = page.getByRole("complementary", { name: "Replay provenance" });
    await expect(banner).toBeVisible();
    const date = new Date(index.recorded_at).toISOString().slice(0, 16).replace("T", " ");
    await expect(banner.locator("strong")).toHaveText(`Recorded replay of ${date} UTC, not live`);
    const s = index.source;
    for (const detail of [
      `Recording ${s.recording}`,
      `Calibration: ${s.calibration}`,
      `LiDAR height: ${s.lidar_height}`,
      "Hardware not verified",
      `Commit ${s.commit.slice(0, 7)}`,
    ])
      await expect(banner.getByText(detail, { exact: true })).toBeVisible();
    // The fixture is a synthetic test export and says so.
    expect(s.note).toContain("TEST EXPORT");
    await expect(banner.locator(".showcase-note")).toHaveText(s.note);
    // Permanent: nothing in the banner dismisses it.
    await expect(banner.getByRole("button")).toHaveCount(0);
    await expect(page.locator(".hardware-badge")).toHaveText("Hardware not tested");
  });

  test("frames advance, pause holds the frame and resume continues", async ({ page }) => {
    await openReplay(page);
    const first = await frameId(page);
    await expect.poll(() => frameId(page)).not.toBe(first);

    await page.getByRole("button", { name: "Pause processing" }).click();
    await expect(pill(page)).toHaveText("Recorded replay · paused");
    // A frame already in the decoder when pausing may still land.
    await page.waitForTimeout(300);
    const held = await frameId(page);
    // Longer than one loop of the fixture (0.6 s of recording).
    await page.waitForTimeout(1500);
    expect(await frameId(page)).toBe(held);

    // Each step shows exactly one more recorded frame, wrapping at the end; a
    // double click is two steps, and the caption must follow both.
    const caption = page.locator(".timeline-caption .mono");
    const position = async () => Number((await caption.innerText()).split(" / ")[0]);
    const before = await position();
    await page.getByRole("button", { name: "Process one frame" }).dblclick();
    await expect(caption).toHaveText(`${((before + 1) % index.frame_count) + 1} / ${index.frame_count} frames`);
    await expect(pill(page)).toHaveText("Recorded replay · paused");

    await page.getByRole("button", { name: "Resume processing" }).click();
    await expect(pill(page)).toHaveText("Recorded replay");
    await expect.poll(() => frameId(page)).not.toBe(held);
  });

  test("seeks to the keyframe of a later chunk", async ({ page }) => {
    expect(index.chunks.length).toBeGreaterThanOrEqual(2);
    await openReplay(page);
    await page.getByRole("button", { name: "Pause processing" }).click();
    await expect(pill(page)).toHaveText("Recorded replay · paused");
    const caption = page.locator(".timeline-caption .mono");
    const timeline = page.getByRole("slider", { name: "Replay frame" });
    await expect(timeline).toBeEnabled();
    // Home first: End on a slider already at the last frame (paused there) changes nothing.
    await timeline.focus();
    await page.keyboard.press("Home");
    await expect(caption).toHaveText(`1 / ${index.frame_count} frames`);
    // End selects the last frame; the replay snaps to the keyframe opening its chunk.
    await page.keyboard.press("End");
    const last = index.chunks[index.chunks.length - 1];
    await expect(caption).toHaveText(`${last.first_frame + 1} / ${index.frame_count} frames`);
    await expect(pill(page)).toHaveText("Recorded replay · paused");
    // Home goes back to the first chunk.
    await page.keyboard.press("Home");
    await expect(caption).toHaveText(`1 / ${index.frame_count} frames`);
  });

  test("the explainer link opens the explainer page", async ({ page }) => {
    await openReplay(page);
    await page.getByRole("link", { name: "What is this?" }).click();
    await expect(page).toHaveURL(/\/explainer$/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("live 3D copy");
  });

  test("the banner links back to the landing page", async ({ page }) => {
    await openReplay(page);
    await page.getByRole("complementary", { name: "Replay provenance" }).getByRole("link", { name: "About this project" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("measured in 3D");
  });

  test("the explainer opens the studio at /replay, not the landing page", async ({ page }) => {
    await page.goto("/explainer.html");
    await page.getByRole("link", { name: "Open the 3D studio" }).click();
    await expect(page).toHaveURL(/\/replay$/);
    // The studio boots after a click here (no page.goto to wait on), under parallel software rendering.
    await expect(pill(page)).toHaveText("Recorded replay", { timeout: 30_000 });
  });

  test("has no serious or critical axe violations", async ({ page }) => {
    await openReplay(page);
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const serious = result.violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).slice(0, 4).join(", ")}`);
    expect(serious).toEqual([]);
  });
});

test.describe("showcase landing page", () => {
  // Reduced motion draws the plate once instead of animating it, which keeps these
  // tests deterministic beside the software-rendered studio tests.
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
  });

  test("opens light: no replay data, no 3D libraries, under 1 MB", async ({ page }) => {
    const requests: Array<{ path: string; bytes: number }> = [];
    page.on("response", async (response) => {
      const body = await response.body().catch(() => Buffer.alloc(0));
      requests.push({ path: new URL(response.url()).pathname, bytes: body.length });
    });
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("A robotics lab, measured in 3D from a hand-pushed cart.");
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForLoadState("networkidle");
    const paths = requests.map((r) => r.path);
    expect(paths.filter((path) => path.startsWith("/showcase/")), "replay data fetched by the landing page").toEqual([]);
    expect(paths.filter((path) => /three-vendor|react-vendor|SpatialRenderer/.test(path))).toEqual([]);
    expect(requests.length, paths.join(" ")).toBeLessThanOrEqual(15);
    expect(requests.reduce((sum, r) => sum + r.bytes, 0)).toBeLessThan(1_000_000);
  });

  test("says what it is: recorded, with the plate's figures from the export metadata", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Recorded, not live.", { exact: false })).toBeVisible();
    await expect(page.locator(".meta-row .flag")).toContainText("not live");
    await expect(page.locator("#read-points")).toHaveText(/^[\d,]+ of [\d,]+$/);
    await expect(page.locator("#read-extent")).toHaveText(/^\d+\.\d × \d+\.\d × \d+\.\d m$/);
    // The plate's canvas has been drawn on (not blank).
    await expect
      .poll(() =>
        page.locator("#twin-canvas").evaluate((canvas: HTMLCanvasElement) => {
          const data = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
          let lit = 0;
          for (let i = 3; i < data.length; i += 4) if (data[i]! > 0) lit++;
          return lit;
        }),
      )
      .toBeGreaterThan(2000);
    await expect(page.getByText("Not measured, not done")).toBeVisible();
  });

  test("the primary action opens the recorded replay", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Launch the 3D replay" }).first().click();
    await expect(page).toHaveURL(/\/replay$/);
    // The studio boots after a click here (no page.goto to wait on), under parallel software rendering.
    await expect(pill(page)).toHaveText("Recorded replay", { timeout: 30_000 });
  });

  test("links to the studio and explainer by clean URL (no .html redirect on Vercel)", async ({ page }) => {
    await page.goto("/");
    const hrefs = await page.locator("a[href]").evaluateAll((links) => links.map((a) => a.getAttribute("href") ?? ""));
    expect(hrefs.filter((href) => /(replay|explainer)\.html/.test(href))).toEqual([]);
    expect(hrefs).toContain("./replay");
    await page.goto("/explainer");
    await expect(page.getByRole("link", { name: "Open the 3D studio" })).toHaveAttribute("href", "./replay");
  });

  for (const width of [375, 1440]) {
    test(`navigation links are at least 44 px tall at ${width} px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      const small = await page
        .locator(".topbar a, .colophon nav a, a.more, .btn")
        .evaluateAll((links) =>
          links
            .map((a) => ({ text: (a.textContent ?? "").trim().slice(0, 24), box: a.getBoundingClientRect() }))
            .filter(({ box }) => box.width > 0 && box.height > 0 && (box.height < 44 || box.width < 44))
            .map(({ text, box }) => `${text} ${Math.round(box.height)}px`),
        );
      expect(small).toEqual([]);
    });
  }

  test("fits a phone without sideways scrolling and keeps its targets tappable", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
    for (const link of await page.locator(".btn").all()) {
      const box = await link.boundingBox();
      if (box) expect(box.height, await link.innerText()).toBeGreaterThanOrEqual(34);
    }
    const primary = await page.getByRole("link", { name: "Launch the 3D replay" }).first().boundingBox();
    expect(primary!.height).toBeGreaterThanOrEqual(44);
  });

  test("has no serious or critical axe violations", async ({ page }) => {
    await page.goto("/");
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    const serious = result.violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).slice(0, 4).join(", ")}`);
    expect(serious).toEqual([]);
  });
});

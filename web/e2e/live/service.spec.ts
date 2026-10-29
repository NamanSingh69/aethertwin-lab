import { test, expect } from "@playwright/test";
import { collectErrors } from "../support/test";

/**
 * Runs against the real Python service in simulation mode. Nothing here claims
 * hardware behaviour; it checks that the shipped build, the HTTP/WebSocket
 * contract and the processing loop work together.
 */
const frame = (page: import("@playwright/test").Page) =>
  page
    .locator(".viewport-stats strong")
    .first()
    .innerText()
    .then((text) => Number(text));

test("studio loads under the production CSP, streams frames and loads measured map tiles", async ({
  page,
}) => {
  const errors = collectErrors(page);
  const tiles: string[] = [];
  page.on("response", (response) => {
    if (/\/api\/v2\/assets\/.+\.glb$/.test(response.url()) && response.ok())
      tiles.push(response.url());
  });
  await page.goto("/");
  await expect(page.getByText("Connected", { exact: true })).toBeVisible({ timeout: 60_000 });
  const first = await frame(page);
  await expect.poll(() => frame(page), { timeout: 60_000 }).toBeGreaterThan(first);
  await expect.poll(() => tiles.length, { timeout: 60_000 }).toBeGreaterThan(0);
  await expect(page.locator(".track-row").first()).toBeVisible({ timeout: 60_000 });
  expect(errors).toEqual([]);
});

test("hashed assets are compressed and immutable; HTML revalidates", async ({ request }) => {
  const html = await request.get("/");
  expect(html.headers()["cache-control"]).toBe("no-cache");
  expect(html.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
  const script = (await html.text()).match(/src="(\/assets\/[^"]+\.js)"/)?.[1];
  expect(script).toBeTruthy();
  const asset = await request.get(script!, { headers: { "accept-encoding": "gzip" } });
  expect(asset.headers()["cache-control"]).toContain("immutable");
  expect(asset.headers()["content-encoding"]).toBe("gzip");
});

test("pause, single step and resume act on the real processing loop", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Connected", { exact: true })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Pause processing" }).click();
  await expect(page.getByRole("button", { name: "Resume processing" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Paused", { exact: true })).toBeVisible();
  const paused = await frame(page);
  await page.waitForTimeout(2_500);
  expect(await frame(page)).toBe(paused);
  await page.getByRole("button", { name: "Process one frame" }).click();
  await expect.poll(() => frame(page), { timeout: 30_000 }).toBeGreaterThan(paused);
  await page.getByRole("button", { name: "Resume processing" }).click();
  await expect(page.getByRole("button", { name: "Pause processing" })).toBeVisible({ timeout: 30_000 });
});

test("a requested route is planned and exported as CSV", async ({ page, request }) => {
  // The simulated robot keeps driving, so a path can legitimately be empty once
  // it arrives. Use the service's own reported robot position as the start and a
  // fixed open floor point in the simulated lab as the goal.
  type State = { trajectory_status?: string; path?: number[][] | null; diagnostics: { robot_position?: number[] } };
  const state = async () => (await (await request.get("/api/v2/state")).json()) as State;
  await expect.poll(async () => (await state()).diagnostics.robot_position?.length, { timeout: 60_000 }).toBe(3);
  const robot = (await state()).diagnostics.robot_position!;
  await page.goto("/");
  await expect(page.getByText("Connected", { exact: true })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Plan a safe route" }).click();
  await page.getByLabel("Start · x, y, z (m)").fill(robot.map((v) => v.toFixed(3)).join(", "));
  const goal = [-robot[0] * 0.5, -robot[1] * 0.5, robot[2]];
  await page.getByLabel("Goal · x, y, z (m)").fill(goal.map((v) => v.toFixed(3)).join(", "));
  await page.getByRole("button", { name: "Plan route" }).click();
  await expect(page.getByText(/Applied set start goal/)).toBeVisible({ timeout: 60_000 });
  await expect
    .poll(async () => {
      const current = await state();
      return current.trajectory_status === "ok" && (current.path?.length ?? 0) >= 2;
    }, { timeout: 60_000 })
    .toBe(true);
  const csv = await request.get("/api/v2/trajectory.csv");
  expect(csv.ok()).toBe(true);
  expect((await csv.text()).trim().split("\n").length).toBeGreaterThan(2);
});

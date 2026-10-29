import { test, expect, frameNumber } from "../support/test";

test.describe("studio shell", () => {
  test("streams binary frames into the 3D viewport without errors", async ({
    page,
    service,
    errors,
  }) => {
    await page.goto("/");
    await expect(page.getByText("Connected", { exact: true })).toBeVisible();
    await expect(page.locator(".viewport canvas")).toBeVisible();
    const first = await frameNumber(page);
    await expect.poll(() => frameNumber(page)).toBeGreaterThan(first);
    await expect(page.locator(".source-badge")).toHaveText(/Simulation/);
    await expect(page.getByRole("img", { name: /3D LiDAR: fresh observations/ })).toBeVisible();
    expect(service.sockets.length).toBe(1);
    expect(errors).toEqual([]);
  });

  test("layer toggles change only presentation state", async ({ page, service }) => {
    await page.goto("/");
    const flow = page.getByRole("checkbox", { name: "Scene flow" });
    await expect(flow).toBeChecked();
    await page.locator(".layer-row", { hasText: "Scene flow" }).click();
    await expect(flow).not.toBeChecked();
    await expect(page.locator(".section-title", { hasText: "Layers" })).toContainText("6");
    expect(service.commands).toEqual([]);
  });

  test("pause is queued, then reported as applied", async ({ page, service }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Pause processing" }).click();
    await expect(page.getByRole("status").filter({ hasText: /Queued pause|Applied pause/ })).toBeVisible();
    await expect.poll(() => service.commands.map((c) => c.action)).toEqual(["pause"]);
    await expect(page.getByRole("button", { name: "Resume processing" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: "Process one frame" })).toBeEnabled();
  });

  test("route planning sends explicit finite endpoints", async ({ page, service }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Plan a safe route" }).click();
    const start = page.getByLabel("Start · x, y, z (m)");
    const goal = page.getByLabel("Goal · x, y, z (m)");
    await start.fill("-4, -3, 0.3");
    await goal.fill("not a point");
    await expect(page.getByRole("button", { name: "Plan route" })).toBeDisabled();
    await goal.fill("3, 2.5, 0.3");
    await page.getByRole("button", { name: "Plan route" }).click();
    await expect.poll(() => service.commands.at(-1)).toMatchObject({
      action: "set_start_goal",
      payload: { start: [-4, -3, 0.3], goal: [3, 2.5, 0.3] },
    });
  });

  test("selecting a tracked entity opens the inspector", async ({ page }) => {
    await page.goto("/");
    await page.locator(".track-row").filter({ hasText: "Person" }).first().click();
    await expect(page.getByRole("button", { name: "Focus on selected entity" })).toBeVisible();
    await page.getByRole("button", { name: "Clear selected entity" }).click();
    await expect(page.getByText("Explore the scene")).toBeVisible();
  });

  test("zone editor traps focus and restores it on Escape", async ({ page }) => {
    await page.goto("/");
    const opener = page.getByRole("button", { name: /Protected zones/ });
    await expect(opener).toBeEnabled();
    await opener.click();
    const modal = page.locator(".modal");
    await expect(modal).toBeVisible();
    for (let i = 0; i < 25; i++) await page.keyboard.press("Tab");
    await expect(modal.locator(":focus")).toHaveCount(1);
    await page.keyboard.press("Escape");
    await expect(modal).toBeHidden();
    await expect(opener).toBeFocused();
  });

  test("events drawer lists streamed alerts", async ({ page }) => {
    await page.goto("/");
    const button = page.locator(".event-button");
    await expect(button).toContainText(/[1-9]\d* events/);
    await button.click();
    await expect(page.locator(".events-drawer")).toContainText("person inside protected zone");
  });
});

test.describe("viewer role", () => {
  test.use({ mockOptions: { role: "viewer" } });
  test("controls stay visible but disabled", async ({ page, service }) => {
    await page.goto("/");
    await expect(page.locator(".role-badge")).toHaveText("viewer");
    await expect(page.getByRole("button", { name: "Pause processing" })).toBeDisabled();
    await page.getByRole("button", { name: "Plan a safe route" }).click();
    await expect(page.getByRole("button", { name: "Plan route" })).toBeDisabled();
    expect(service.commands).toEqual([]);
  });
});

test.describe("private session", () => {
  test.use({ mockOptions: { requireLogin: true } });
  test("signs in with a credential and then streams", async ({ page, errors }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Connect to AetherTwin" })).toBeVisible();
    const credential = page.getByLabel("Session credential");
    await credential.fill("wrong-credential");
    await credential.press("Enter");
    await expect(page.getByRole("alert").filter({ hasText: "Invalid access credential" })).toBeVisible();
    await credential.fill("e2e-operator-credential-0000000000000");
    await page.getByRole("button", { name: "Open session" }).click();
    await expect(page.getByText("Connected", { exact: true })).toBeVisible();
    // The failed attempts are expected 401 responses, not application errors.
    expect(errors.filter((e) => !/401/.test(e))).toEqual([]);
  });
});

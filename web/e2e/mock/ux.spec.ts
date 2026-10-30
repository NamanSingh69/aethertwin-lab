import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "../support/test";

// The first streamed frame can take a while when several software-WebGL workers share the CPU.
const connected = (page: import("@playwright/test").Page) =>
  expect(page.getByText("Connected", { exact: true })).toBeVisible({ timeout: 20_000 });

test.describe("keyboard shortcuts", () => {
  test("? opens an accessible shortcut dialog that owns the keyboard", async ({ page }) => {
    await page.goto("/");
    await connected(page);
    const opener = page.getByRole("button", { name: "Keyboard shortcuts" });
    await opener.focus();
    await page.keyboard.press("?");
    const dialog = page.getByRole("dialog", { name: "Shortcuts" });
    await expect(dialog).toBeVisible();
    for (let i = 0; i < 6; i++) await page.keyboard.press("Tab");
    await expect(dialog.locator(":focus")).toHaveCount(1);
    // Global shortcuts pause while the dialog is open.
    await page.keyboard.press("e");
    await expect(page.locator(".events-drawer")).toHaveCount(0);
    const axe = await new AxeBuilder({ page }).include(".help-modal").analyze();
    expect(axe.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""))).toEqual([]);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
  });

  test("panels and layers toggle from the keyboard, never while typing", async ({ page, service }) => {
    await page.goto("/");
    await connected(page);
    await page.keyboard.press("e");
    await expect(page.locator(".events-drawer")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator(".events-drawer")).toHaveCount(0);
    const voxels = page.getByRole("checkbox", { name: "Semantic voxels" });
    await expect(voxels).toBeChecked();
    await page.keyboard.press("2");
    await expect(voxels).not.toBeChecked();
    await page.keyboard.press("2");
    await expect(voxels).toBeChecked();
    await page.keyboard.press("r");
    const route = page.locator(".route-panel");
    await expect(route).toBeVisible();
    const field = route.locator("input").first();
    await field.click();
    await page.keyboard.type("e2");
    await expect(page.locator(".events-drawer")).toHaveCount(0);
    await expect(voxels).toBeChecked();
    await field.evaluate((element) => (element as HTMLElement).blur());
    await page.keyboard.press("Escape");
    await expect(route).toBeHidden();
    // Layer and panel shortcuts are presentation only.
    expect(service.commands).toEqual([]);
  });

  test("space pauses through the command plane", async ({ page, service }) => {
    await page.goto("/");
    await connected(page);
    await page.keyboard.press(" ");
    await expect.poll(() => service.commands.map((c) => c.action)).toEqual(["pause"]);
  });

  test.describe("as a viewer", () => {
    test.use({ mockOptions: { role: "viewer" } });
    test("space does not send controls", async ({ page, service }) => {
      await page.goto("/");
      await connected(page);
      await page.keyboard.press(" ");
      await page.waitForTimeout(500);
      expect(service.commands).toEqual([]);
    });
  });
});

test("render quality applies to the renderer and persists", async ({ page }) => {
  await page.goto("/");
  await connected(page);
  const viewport = page.locator(".viewport");
  const select = page.getByLabel("Render quality");
  await expect(select).toHaveValue("auto");
  await select.selectOption("performance");
  await expect(viewport).toHaveAttribute("data-quality", "performance");
  await expect(viewport).toHaveAttribute("data-render-mode", "on-demand");
  await page.reload();
  await connected(page);
  await expect(page.getByLabel("Render quality")).toHaveValue("performance");
  await expect(viewport).toHaveAttribute("data-quality", "performance");
  await page.keyboard.press("q");
  await expect(page.getByLabel("Render quality")).toHaveValue("balanced");
  await expect(viewport).toHaveAttribute("data-render-mode", "continuous");
});

test("notices dismiss themselves after a few seconds", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Pause processing" }).click();
  const notice = page.locator(".toast:not(.error)");
  await expect(notice).toBeVisible();
  await expect(notice).toBeHidden({ timeout: 15_000 });
});

test.describe("measured map", () => {
  test.use({ mockOptions: { mapTiles: 3 } });
  test("tile progress reports failures and the error stays until dismissed", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator(".map-progress")).toHaveText(
      "Measured map: 3 of 3 tiles failed to load",
      { timeout: 15_000 },
    );
    const error = page.locator(".toast.error");
    await expect(error).toBeVisible();
    await page.waitForTimeout(6_000);
    await expect(error).toBeVisible();
    await error.getByRole("button", { name: "Dismiss message" }).click();
    await expect(page.locator(".map-progress")).toBeVisible();
  });
});

test.describe("cinematic quality", () => {
  // Bloom in software WebGL is expensive; a small canvas keeps parallel workers responsive.
  test.use({ viewport: { width: 800, height: 450 } });
  test("loads presentation-only effects lazily and labels them", async ({ page, errors }) => {
    await page.goto("/");
    // The header status pill is hidden at this width; wait for streamed instances instead.
    await expect(page.locator(".viewport")).toHaveAttribute("data-instances", /^[1-9]/);
    await page.getByLabel("Render quality").selectOption("cinematic");
    const viewport = page.locator(".viewport");
    await expect(viewport).toHaveAttribute("data-effects", "cinematic", { timeout: 15_000 });
    await expect(viewport).toHaveAttribute("data-motion", "true");
    await expect(page.getByText("Effects · presentation only")).toBeVisible();
    expect(errors).toEqual([]);
  });

  test.describe("with reduced motion", () => {
    test.use({ reducedMotion: "reduce" });
    test("keeps decorative motion off", async ({ page }) => {
      await page.goto("/");
      // The header status pill is hidden at this width; wait for streamed instances instead.
    await expect(page.locator(".viewport")).toHaveAttribute("data-instances", /^[1-9]/);
      await page.getByLabel("Render quality").selectOption("cinematic");
      const viewport = page.locator(".viewport");
      await expect(viewport).toHaveAttribute("data-effects", "cinematic", { timeout: 15_000 });
      await expect(viewport).toHaveAttribute("data-motion", "false");
    });
  });
});

import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "../support/test";

test.describe("explainer", () => {
  test("walks through the stages and switches audience", async ({ page }) => {
    await page.goto("/explainer.html");
    await expect(page.getByRole("heading", { level: 1 })).toContainText("live 3D copy");
    await expect(page.getByText("Hardware: not tested", { exact: false }).or(page.getByText("No real sensors"))).toBeVisible();

    await page.getByRole("button", { name: "Next step" }).click();
    await expect(page).toHaveURL(/#sensors$/);
    await page.getByRole("navigation", { name: "Pipeline stages" }).getByRole("button", { name: /Risk/ }).click();
    await expect(page.getByRole("heading", { name: "Safety bubbles that stretch with speed" })).toBeVisible();
    await expect(page.getByText(/Time to contact:/)).toBeVisible();

    await page.getByRole("radio", { name: "For engineers" }).click();
    await expect(page.getByText("tracking_and_navigation/risk_and_geofence.py").first()).toBeVisible();
    await expect(page.getByText(/stable quadratic root/)).toBeVisible();
  });

  test("labs respond to input", async ({ page }) => {
    await page.goto("/explainer.html#mapping");
    const readout = page.getByText(/Scans: \d+/);
    await expect(readout).toContainText("Scans: 0");
    await page.getByRole("button", { name: "Scan" }).click();
    await page.getByRole("button", { name: "Scan" }).click();
    await expect(readout).toContainText("Scans: 2");
    await expect(readout).not.toContainText("score 0.00");

    await page.goto("/explainer.html#perception");
    await page.getByRole("button", { name: "Thin pole" }).click();
    await expect(page.getByRole("button", { name: "Thin pole" })).toHaveAttribute("aria-pressed", "true");
  });

  test("links a stage to the studio with its layers and view", async ({ page }) => {
    await page.goto("/explainer.html#risk");
    await page.getByRole("link", { name: "Show this in the 3D studio" }).click();
    await expect(page).toHaveURL(/\?layers=risk%2Cgeofences%2Ctracks&view=top/);
    await expect(page.getByRole("checkbox", { name: "Risk hotspots" })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: "Safe trajectory" })).not.toBeChecked();
    await expect(page.getByRole("link", { name: "What is this?" })).toHaveAttribute("href", "explainer.html");
  });

  test("has no serious or critical axe violations", async ({ page }) => {
    for (const hash of ["#overview", "#risk", "#perception"]) {
      await page.goto(`/explainer.html${hash}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      const serious = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(serious.map((v) => `${hash} ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    }
  });

  test("fits a phone screen without sideways scrolling", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto("/explainer.html#deskew");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  });
});

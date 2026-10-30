import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "../support/test";

type Violation = Awaited<ReturnType<AxeBuilder["analyze"]>>["violations"][number];

/** Moderate/minor findings are reported by axe but do not fail the suite. */
const serious = (violations: Violation[]) =>
  violations.filter((v) => v.impact === "serious" || v.impact === "critical");

function describe(violations: Violation[]) {
  return violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).slice(0, 4).join(", ")}`);
}

test.describe("accessibility", () => {
  test("studio has no serious or critical axe violations", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Connected", { exact: true })).toBeVisible();
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(describe(serious(result.violations))).toEqual([]);
  });

  test("route panel and events drawer have no serious violations", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Plan a safe route" }).click();
    await page.locator(".event-button").click();
    const result = await new AxeBuilder({ page })
      .include(".route-panel")
      .include(".events-drawer")
      .analyze();
    expect(describe(serious(result.violations))).toEqual([]);
  });
});

test.describe("small screens", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  test("keeps workspaces, source and entity selection reachable", async ({ page }) => {
    await page.goto("/");
    // The header status pill is intentionally hidden at phone width; wait for streamed frames.
    await expect(page.locator(".viewport-stats strong").first()).toHaveText(/^\d{5}$/);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
    await page.getByRole("button", { name: "Toggle layer dock" }).click();
    const workspaces = page.getByRole("navigation", { name: "All workspaces" });
    await expect(workspaces).toBeVisible();
    await expect(page.getByLabel("Mobile data source")).toBeVisible();
    await page.getByLabel("Select tracked entity").selectOption({ index: 1 });
    await expect(page.getByRole("button", { name: "Focus on selected entity" })).toBeVisible();
  });
});

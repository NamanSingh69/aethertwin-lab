import { test as base, expect, type Page } from "@playwright/test";
import { MockService, type MockOptions } from "./mockService";

/** Console errors and uncaught exceptions fail a test unless explicitly expected. */
export function collectErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  return errors;
}

export const test = base.extend<{
  mockOptions: MockOptions;
  service: MockService;
  errors: string[];
}>({
  mockOptions: [{}, { option: true }],
  errors: async ({ page }, use) => {
    const errors = collectErrors(page);
    await use(errors);
  },
  // Automatic: no test may fall through to Vite's /api proxy and a real service.
  service: [
    async ({ page, mockOptions }, use) => {
      const service = new MockService(page, mockOptions);
      await service.install();
      await use(service);
      service.dispose();
    },
    { auto: true },
  ],
});

/** Wait until the stream has delivered at least `frames` frames after the current one. */
export async function frameNumber(page: Page) {
  const text = await page.locator(".viewport-stats strong").first().innerText();
  return Number(text);
}

export { expect };

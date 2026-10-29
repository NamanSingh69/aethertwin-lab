import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

/**
 * Two browser suites:
 * - `mock`: the production build (`vite preview`) against an in-test fake
 *   service (HTTP routes + a binary WebSocket). Deterministic, no Python.
 * - `live`: the real Python service in simulation mode. Set
 *   AETHERTWIN_SERVICE_URL to an already running service, or set
 *   AETHERTWIN_PYTHON to let Playwright start one.
 */
const serviceURL =
  process.env.AETHERTWIN_SERVICE_URL ??
  (process.env.AETHERTWIN_PYTHON ? "http://127.0.0.1:8080" : undefined);
const previewPort = Number(process.env.E2E_PREVIEW_PORT ?? 4173);
// Headless CI machines have no GPU; WebGL needs an explicit software adapter.
const webgl = {
  launchOptions: {
    args: [
      "--use-angle=swiftshader",
      "--enable-unsafe-swiftshader",
      "--ignore-gpu-blocklist",
    ],
  },
};

export default defineConfig({
  testDir: "e2e",
  outputDir: "test-results",
  fullyParallel: true,
  // Each SwiftShader context saturates several CPU threads; locally the default
  // (half the cores) timed out 10 of 12 tests on a 20-thread laptop. CI runners
  // have few cores, so they keep Playwright's default.
  workers: process.env.CI ? undefined : Number(process.env.E2E_WORKERS ?? 4),
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI
    ? [["github"], ["html", { open: "never" }], ["junit", { outputFile: "test-results/e2e-junit.xml" }]]
    : [["list"]],
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "mock",
      testDir: "e2e/mock",
      use: {
        ...devices["Desktop Chrome"],
        ...webgl,
        viewport: { width: 1600, height: 900 },
        baseURL: `http://127.0.0.1:${previewPort}`,
      },
    },
    ...(serviceURL
      ? [
          {
            name: "live",
            testDir: "e2e/live",
            // One backend instance holds shared processing state.
            fullyParallel: false,
            workers: 1,
            timeout: 120_000,
            use: {
              ...devices["Desktop Chrome"],
              ...webgl,
              viewport: { width: 1600, height: 900 },
              baseURL: serviceURL,
            },
          },
        ]
      : []),
  ],
  webServer: [
    {
      command: `npx vite preview --host 127.0.0.1 --port ${previewPort} --strictPort`,
      url: `http://127.0.0.1:${previewPort}/`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    ...(process.env.AETHERTWIN_PYTHON && !process.env.AETHERTWIN_SERVICE_URL
      ? [
          {
            command: `"${process.env.AETHERTWIN_PYTHON}" -m command_center --mode sim --threads 2 --output web/test-results/service`,
            cwd: fileURLToPath(new URL("..", import.meta.url)),
            url: "http://127.0.0.1:8080/",
            reuseExistingServer: !process.env.CI,
            timeout: 240_000,
            stdout: "pipe" as const,
          },
        ]
      : []),
  ],
});

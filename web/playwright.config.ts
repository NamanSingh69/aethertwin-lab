import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

/**
 * Three browser suites:
 * - `mock`: the production build (`vite preview`) against an in-test fake
 *   service (HTTP routes + a binary WebSocket). Deterministic, no Python.
 * - `live`: the real Python service in simulation mode. Set
 *   AETHERTWIN_SERVICE_URL to an already running service, or set
 *   AETHERTWIN_PYTHON to let Playwright start one.
 * - `showcase`: the static showcase build (`npm run build:showcase`) replaying
 *   the committed fixture export in e2e/showcase/fixture, with no service.
 *   Its specs use @playwright/test directly, so the AUTO mock `service`
 *   fixture of e2e/support/test.ts never runs for it.
 */
const serviceURL =
  process.env.AETHERTWIN_SERVICE_URL ??
  (process.env.AETHERTWIN_PYTHON ? "http://127.0.0.1:8080" : undefined);
const previewPort = Number(process.env.E2E_PREVIEW_PORT ?? 4173);
const showcasePort = Number(process.env.E2E_SHOWCASE_PORT ?? 4185);

/**
 * Playwright starts every webServer whatever --project selects. A server runs
 * only when its project is selected (or no --project filter is given), so a
 * mock run does not build the showcase and a showcase run needs no mock port.
 * Only the main process reads this; workers never start servers.
 */
const selectedProjects = (() => {
  const names: string[] = [];
  const argv = process.argv;
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--project" && argv[i + 1]) names.push(argv[++i]);
    else if (argv[i].startsWith("--project=")) names.push(argv[i].slice("--project=".length));
  }
  return names.length && !names.some((name) => /[*?]/.test(name)) ? names : null;
})();
const selected = (project: string) => !selectedProjects || selectedProjects.includes(project);
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
    {
      name: "showcase",
      testDir: "e2e/showcase",
      use: {
        ...devices["Desktop Chrome"],
        ...webgl,
        viewport: { width: 1600, height: 900 },
        baseURL: `http://127.0.0.1:${showcasePort}`,
        // The replay renders a full twin mesh every frame; recording video of it
        // under SwiftShader made closing a context exceed the test timeout.
        video: "off",
      },
    },
  ],
  webServer: [
    ...(selected("mock")
      ? [
          {
            command: `npx vite preview --host 127.0.0.1 --port ${previewPort} --strictPort`,
            url: `http://127.0.0.1:${previewPort}/`,
            reuseExistingServer: !process.env.CI,
            timeout: 60_000,
          },
        ]
      : []),
    ...(selected("showcase")
      ? [
          {
            // Always a fresh build of showcase-dist with the fixture data: a
            // reused server could be serving another export.
            command: `npm run build:showcase && npx vite preview --config vite.showcase.config.ts --host 127.0.0.1 --port ${showcasePort} --strictPort`,
            env: { SHOWCASE_DATA_DIR: fileURLToPath(new URL("e2e/showcase/fixture", import.meta.url)) },
            url: `http://127.0.0.1:${showcasePort}/`,
            reuseExistingServer: false,
            timeout: 180_000,
          },
        ]
      : []),
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

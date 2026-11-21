// Static showcase: a landing page (landing.html, served as the site root) and
// the studio replaying a recorded session from static files (replay.html),
// with no service at all. `npm run build:showcase` writes showcase-dist/ (not
// committed; Vercel serves it). Replay data (index.json, bootstrap.json,
// chunks/, assets/) is copied from SHOWCASE_DATA_DIR (default web/showcase-data)
// to showcase-dist/showcase/, the default base the page fetches from.
import { cpSync, existsSync, readdirSync, readFileSync, renameSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const root = import.meta.dirname;
const outDir = resolve(root, "showcase-dist");
const dataDir = resolve(root, process.env.SHOWCASE_DATA_DIR ?? "showcase-data");
// A local icon, so browsers do not request /favicon.ico on every visit.
const FAVICON =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#080d14"/>' +
  '<path d="M7 23 16 7l9 16" fill="none" stroke="#21e5b0" stroke-width="3" stroke-linejoin="round"/>' +
  '<circle cx="16" cy="19" r="2.6" fill="#f1bf67"/></svg>\n';

/** Total size of the replay export, as the landing page states it ("22 MB"). */
function replaySize(): string {
  if (!existsSync(dataDir)) return "unknown (no replay data in this build)";
  let bytes = 0;
  for (const entry of readdirSync(dataDir, { recursive: true, withFileTypes: true }))
    if (entry.isFile()) bytes += statSync(resolve(entry.parentPath, entry.name)).size;
  return bytes >= 1e6 ? `${Math.round(bytes / 1e6)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} kB`;
}

function showcaseStatic(): Plugin {
  return {
    name: "aethertwin-showcase-static",
    apply: "build",
    transformIndexHtml(html, context) {
      // The studio page plays the recording; the explainer page keeps its title.
      const studio = /(^|\/)index\.html$/.test(context.path);
      const landing = /(^|\/)landing\.html$/.test(context.path);
      return {
        html: studio
          ? html.replace(
              /<title>([^<]*)<\/title>/,
              (_, title: string) => `<title>${title} · Recorded replay, not live</title>`,
            )
          : landing
            ? html.replaceAll("%REPLAY_SIZE%", replaySize())
            : html,
        tags: [
          {
            tag: "link",
            attrs: { rel: "icon", type: "image/svg+xml", href: "./favicon.svg" },
            injectTo: "head",
          },
        ],
      };
    },
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "favicon.svg", source: FAVICON });
      // Link-preview image at a stable URL (the landing page's og:image).
      this.emitFile({ type: "asset", fileName: "og.jpg", source: readFileSync(resolve(root, "src/landing/img/og.jpg")) });
    },
    closeBundle() {
      // The site opens on the landing page; the studio replay is one click away
      // at replay.html, so a first visit never downloads the replay data.
      renameSync(resolve(outDir, "index.html"), resolve(outDir, "replay.html"));
      renameSync(resolve(outDir, "landing.html"), resolve(outDir, "index.html"));
      if (!existsSync(dataDir)) {
        console.warn(
          `[showcase] No replay data at ${dataDir}; the page will report that the recorded replay could not be loaded. ` +
            "Set SHOWCASE_DATA_DIR to the exporter's output folder.",
        );
        return;
      }
      // Replay data hosted elsewhere (VITE_SHOWCASE_BASE, docs/SHOWCASE.md section 4): keep it out of the upload, so
      // the website host serves only the pages and code, never the 22 MB a replay view reads.
      if (process.env.VITE_SHOWCASE_BASE) {
        console.log(`[showcase] Replay data is served from ${process.env.VITE_SHOWCASE_BASE}; not copied into showcase-dist/`);
        return;
      }
      cpSync(dataDir, resolve(outDir, "showcase"), { recursive: true });
      console.log(`[showcase] Copied replay data from ${dataDir} to showcase-dist/showcase/`);
    },
  };
}

export default defineConfig({
  plugins: [react(), showcaseStatic()],
  base: "./",
  // Never copy web/public; the showcase output holds only the build and the replay data.
  publicDir: false,
  define: { "import.meta.env.VITE_SHOWCASE": JSON.stringify("1") },
  // No /api or /ws proxy: a request to the service in preview is a bug and must 404.
  preview: { proxy: {} },
  build: {
    target: "es2022",
    outDir,
    emptyOutDir: true,
    // Public deploy: no sourcemaps.
    sourcemap: false,
    chunkSizeWarningLimit: 850,
    rolldownOptions: {
      input: { index: "index.html", explainer: "explainer.html", landing: "landing.html" },
      output: {
        comments: { legal: false },
        codeSplitting: {
          groups: [
            { name: "three-vendor", test: /[\\/]node_modules[\\/]three[\\/](?!examples[\\/]jsm[\\/](postprocessing|shaders|libs)[\\/])/, priority: 20 },
            { name: "react-vendor", test: /[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/, priority: 20 },
          ],
        },
      },
    },
  },
});

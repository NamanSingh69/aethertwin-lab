// Checks the static showcase build (run by `npm run build:showcase`): no
// sourcemaps, no route to the live service left in the bundle, a local icon,
// all three pages present (landing at the root, studio at replay.html), and a
// landing page that loads neither the studio nor the 3D libraries. The replay data under showcase/ is not code and is
// only counted.
import { readdir, readFile, stat } from "node:fs/promises";
import { gzipSync } from "node:zlib";
import { join, relative, resolve } from "node:path";

const out = resolve(import.meta.dirname, "..", "showcase-dist");
const FORBIDDEN = ["/api/v2", "/ws/v2", "ws://", "wss://", "sourceMappingURL"];
const failures = [];

async function walk(dir) {
  const files = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(path)));
    else files.push(path);
  }
  return files;
}

const files = await walk(out);
// With an external data base the replay must not also be uploaded to the website host.
if (process.env.VITE_SHOWCASE_BASE && files.some((f) => relative(out, f).replaceAll("\\", "/").startsWith("showcase/")))
  failures.push(`showcase/ is in the build although VITE_SHOWCASE_BASE=${process.env.VITE_SHOWCASE_BASE} serves the replay data`);
let codeBytes = 0,
  codeGzip = 0,
  dataBytes = 0;
for (const file of files) {
  const name = relative(out, file).replaceAll("\\", "/");
  if (name.startsWith("showcase/")) {
    dataBytes += (await stat(file)).size;
    continue;
  }
  if (name.endsWith(".map")) failures.push(`sourcemap emitted: ${name}`);
  if (!/\.(js|css|html|svg)$/.test(name)) continue;
  const text = await readFile(file, "utf8");
  codeBytes += Buffer.byteLength(text);
  codeGzip += gzipSync(text, { level: 9 }).length;
  for (const needle of FORBIDDEN)
    if (text.includes(needle)) failures.push(`${name} contains "${needle}"`);
}
for (const page of ["index.html", "replay.html", "explainer.html", "favicon.svg", "og.jpg"])
  if (!files.some((file) => relative(out, file).replaceAll("\\", "/") === page))
    failures.push(`missing ${page}`);
const pages = {};
for (const page of ["index.html", "replay.html", "explainer.html"]) {
  const html = (pages[page] = await readFile(join(out, page), "utf8").catch(() => ""));
  if (!/<link[^>]+rel="icon"/.test(html)) failures.push(`${page} has no <link rel="icon">`);
}
// The root is the landing page: it must not pull in the studio or the replay
// data, and it must say plainly that the replay is a recording.
const landingScripts = [...pages["index.html"].matchAll(/(?:src|href)="\.\/(assets\/[^"]+\.js)"/g)].map((m) => m[1]);
for (const script of landingScripts)
  if (/three-vendor|react-vendor/.test(script)) failures.push(`landing page loads ${script}`);
if (dataBytes && pages["index.html"].includes("no replay data in this build"))
  failures.push("landing page states no replay size");
if (/%[A-Z_]+%/.test(pages["index.html"])) failures.push("landing page has an unfilled %PLACEHOLDER%");
if (!pages["index.html"].replace(/\s+/g, " ").includes("Recorded, not live")) failures.push('landing page does not say "Recorded, not live"');
if (!/Recorded replay, not live<\/title>/.test(pages["replay.html"])) failures.push("replay.html is not the studio replay page");

const kB = (bytes) => (bytes / 1024).toFixed(1);
console.log(
  `showcase-dist: code ${kB(codeBytes)} kB (${kB(codeGzip)} kB gz), replay data ${dataBytes ? `${kB(dataBytes)} kB` : "none (set SHOWCASE_DATA_DIR)"}`,
);
if (failures.length) {
  for (const failure of failures) console.error(`FAIL ${failure}`);
  process.exit(1);
}
console.log("Showcase build checks passed: no sourcemaps, no service routes, local icon.");

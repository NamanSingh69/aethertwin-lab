// Bundle budget: gzip size per emitted chunk, read from the Vite manifest.
// Budgets sit a little above the measured sizes so growth is a deliberate
// change to this file, not an accident. Run after `npm run build`.
import { readFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
// Gzip kB per chunk name (the part of the file name before the content hash).
const BUDGET_KB = {
  "three-vendor": 168,
  "react-vendor": 72,
  index: 25,
  SpatialRenderer: 11,
  effects: 6,
  "meshopt_decoder.module": 8,
  benchmark: 4,
  explainer: 14,
  viewParams: 1,
  "spatial-contracts": 2,
  "decode.worker": 4,
  css: 10,
  "preload-helper": 2,
  "rolldown-runtime": 1,
  "modulepreload-polyfill": 1,
};
// Everything the studio fetches before its first paint (entry, its static imports, CSS).
const INITIAL_BUDGET_KB = 105;

const manifest = JSON.parse(await readFile(resolve(root, "dist/.vite/manifest.json"), "utf8"));
const files = new Map();
for (const chunk of Object.values(manifest))
  for (const file of [chunk.file, ...(chunk.css ?? [])]) files.set(file, chunk);
const name = (file) =>
  file.endsWith(".css") ? "css" : file.replace(/^assets\//, "").replace(/-[\w-]{8}\.js$/, "");
const gzipKB = async (file) => gzipSync(await readFile(resolve(root, "dist", file)), { level: 9 }).length / 1024;

const rows = [];
let failed = false;
for (const file of [...files.keys()].filter((f) => /\.(js|css)$/.test(f))) {
  const chunk = name(file), size = await gzipKB(file), budget = BUDGET_KB[chunk];
  const over = budget === undefined || size > budget;
  failed ||= over;
  rows.push({ chunk, file, gzip_kb: +size.toFixed(2), budget_kb: budget ?? null, ok: !over });
}
const initial = new Set();
const visit = (key) => {
  const chunk = manifest[key];
  if (!chunk || initial.has(chunk.file)) return;
  initial.add(chunk.file);
  for (const css of chunk.css ?? []) initial.add(css);
  for (const dependency of chunk.imports ?? []) visit(dependency);
};
visit("index.html");
let initialKB = 0;
for (const file of initial) if (/\.(js|css)$/.test(file)) initialKB += await gzipKB(file);
failed ||= initialKB > INITIAL_BUDGET_KB;

for (const row of rows.sort((a, b) => b.gzip_kb - a.gzip_kb))
  console.log(`${row.ok ? "ok  " : "OVER"} ${row.chunk.padEnd(24)} ${row.gzip_kb.toFixed(2).padStart(7)} kB gz  (budget ${row.budget_kb ?? "none"})`);
console.log(`${initialKB <= INITIAL_BUDGET_KB ? "ok  " : "OVER"} ${"initial studio load".padEnd(24)} ${initialKB.toFixed(2).padStart(7)} kB gz  (budget ${INITIAL_BUDGET_KB})`);
if (failed) {
  console.error("Bundle budget exceeded, or a new chunk has no budget. Update BUDGET_KB deliberately.");
  process.exit(1);
}

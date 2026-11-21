// Export the A3 poster PDF from reports/poster/poster_a3.html with the installed Chrome (Windows: Skia/PDF, the same
// output as Chrome's print dialog with A3 and background graphics on). Run from web/: node tools/export-poster.mjs
// Optional: node tools/export-poster.mjs <html> <out.pdf>. It refuses to write when the poster's text runs past the page.
import { chromium } from 'playwright-core';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const html = path.resolve(process.argv[2] ?? path.join(repo, 'reports/poster/poster_a3.html'));
const out = path.resolve(process.argv[3] ?? path.join(repo, 'reports/poster/AetherTwin_poster_A3.pdf'));

const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const page = await browser.newPage();
  await page.goto(pathToFileURL(html).href, { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  await page.emulateMedia({ media: 'print' });
  const fit = await page.evaluate(() => {
    const p = document.querySelector('.page');
    if (!p) return { error: 'no .page element' };
    const box = p.getBoundingClientRect();
    let bottom = 0, right = 0;
    for (const e of p.querySelectorAll('*')) {
      const r = e.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      bottom = Math.max(bottom, r.bottom); right = Math.max(right, r.right);
    }
    // the title box must hold its text (the header lines once ran 1.3 mm past it)
    const t = document.querySelector('.titlebox'); const tb = t?.getBoundingClientRect();
    const kids = t ? [...t.children].map((c) => c.getBoundingClientRect()) : [];
    const header = t ? { top: tb.top - Math.min(...kids.map((k) => k.top)), bottom: Math.max(...kids.map((k) => k.bottom)) - tb.bottom } : null;
    return { overBottom: bottom - box.bottom, overRight: right - box.right, header };
  });
  if (fit.error) throw new Error(fit.error);
  const bad = fit.overBottom > 0.5 || fit.overRight > 0.5 || (fit.header && (fit.header.top > 0.5 || fit.header.bottom > 0.5));
  console.log(JSON.stringify({ html: path.relative(repo, html), ...fit }));
  if (bad) throw new Error('Poster content runs past the page or the title box; fix the HTML before exporting.');
  await page.pdf({ path: out, preferCSSPageSize: true, printBackground: true });
  console.log(`Wrote ${path.relative(repo, out)}. Check it: one A3 page (pdfinfo), fonts embedded (pdffonts).`);
} finally {
  await browser.close();
}

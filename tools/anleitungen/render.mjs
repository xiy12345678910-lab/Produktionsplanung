// Wandelt die von build.py erzeugten HTML-Anleitungen in PDF um (Chromium über Playwright).
// Aufruf: node render.mjs <html-ordner> <pdf-ordner>
import { execSync } from 'node:child_process';
import { readdirSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const [src, out] = process.argv.slice(2).map(p => path.resolve(p));
mkdirSync(out, { recursive: true });
const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
const page = await browser.newPage();
for (const f of readdirSync(src).filter(f => f.endsWith('.html')).sort()) {
  await page.goto(pathToFileURL(path.join(src, f)).href, { waitUntil: 'load' });
  const title = await page.title();
  await page.pdf({
    path: path.join(out, f.replace(/\.html$/, '.pdf')), format: 'A4', printBackground: true, preferCSSPageSize: true,
    displayHeaderFooter: true,
    headerTemplate: '<span></span>',
    footerTemplate: `<div style="font-size:7.5pt;color:#667085;width:100%;padding:0 15mm;display:flex;justify-content:space-between;font-family:Arial,sans-serif"><span>${title} · Produktionsplanung</span><span>Seite <span class="pageNumber"></span> von <span class="totalPages"></span></span></div>`,
  });
  console.log('PDF', f);
}
await browser.close();

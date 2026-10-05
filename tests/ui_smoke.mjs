#!/usr/bin/env node
// UI-Smoketest (ab V12.7.5): startet server.py mit einer leeren Datenbank in einem Temp-Ordner
// und prüft die Oberfläche im Browser (Playwright/Chromium).
//
// Aufruf:  node tests/ui_smoke.mjs [--shots <ordner>]
// Voraussetzung: Python 3 und Node mit Playwright (lokal oder global installiert).
// Läuft nie gegen den Live-Datenordner.
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18777;
const BASE = `http://127.0.0.1:${PORT}/`;
const USER = 'admin', PASS = 'Smoke-Test-123';
const shotsArg = process.argv.indexOf('--shots');
const SHOTS = shotsArg > 0 ? path.resolve(process.argv[shotsArg + 1]) : null;
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}

const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-ui-'));
const py = `
import ipaddress, sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR = Path(${JSON.stringify(dataDir)})
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
server.init_db()
server.create_or_reset_admin(${JSON.stringify(USER)}, ${JSON.stringify(PASS)})
httpd = server.MPHTTPServer(("127.0.0.1", ${PORT}), server.Handler)
print("READY", flush=True)
httpd.serve_forever()
`;
const srv = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'] });
await new Promise((resolve, reject) => {
  const t = setTimeout(() => reject(new Error('Server startet nicht')), 20000);
  srv.stdout.on('data', d => { if (String(d).includes('READY')) { clearTimeout(t); resolve(); } });
  srv.on('exit', c => reject(new Error('Server beendet: ' + c)));
});

const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
const errors = [];

async function login(page) {
  await page.goto(BASE);
  await page.fill('#loginUser', USER);
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(400);
}
const VIEWS = [['navPlan', 'overview'], ['navList', 'orders'], ['navOrders', 'projects'], ['navPersonnel', 'personnel'], ['navGF', 'gf'], ['navSystem', 'settings']];

try {
  // ------------------------------------------------------------------ Ansichten, Überlauf, Beschriftungen
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    const page = await browser.newPage({ viewport: { width: w, height: h } });
    page.on('pageerror', e => errors.push(`${w}px: ${e.message}`));
    await login(page);
    for (const [nav, view] of VIEWS) {
      const btn = page.locator('#' + nav);
      check(await btn.isVisible(), `${w}px: Navigation "${nav}" sichtbar`);
      await btn.click();
      await page.waitForTimeout(350);
      const r = await page.evaluate(() => {
        const vis = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
        const view = document.querySelector('.view.active');
        const unlabeled = [...view.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(vis)
          .filter(c => !(c.labels && c.labels.length) && !c.getAttribute('aria-label') && !c.getAttribute('aria-labelledby'))
          .map(c => c.id || c.outerHTML.slice(0, 60));
        const nameless = [...document.querySelectorAll('button')].filter(vis)
          .filter(b => !(b.getAttribute('aria-label') || (b.textContent || '').trim().replace(/[^\p{L}\p{N}]/gu, '')))
          .map(b => b.id || b.outerHTML.slice(0, 60));
        return { sw: document.documentElement.scrollWidth, iw: innerWidth, active: view.id, unlabeled, nameless };
      });
      check(r.active === view, `${w}px: ${nav} öffnet Ansicht ${view} (${r.active})`);
      check(r.sw <= r.iw + 1, `${w}px/${view}: kein seitlicher Seiten-Scroll (${r.sw} ≤ ${r.iw})`);
      check(!r.unlabeled.length, `${w}px/${view}: alle Felder beschriftet ${r.unlabeled.slice(0, 3).join(', ')}`);
      check(!r.nameless.length, `${w}px/${view}: alle Symbol-Buttons benannt ${r.nameless.slice(0, 3).join(', ')}`);
      if (SHOTS) await page.screenshot({ path: path.join(SHOTS, `${w}_${view}.png`), fullPage: true });
    }
    await page.close();
  }

  // ------------------------------------------------------------------ Dialoge: Rolle, Escape, Fokus
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', e => errors.push(e.message));
  await login(page);
  await page.click('#navPlan');
  await page.click('#quickAdd');
  await page.waitForTimeout(200);
  const dlg = await page.evaluate(() => { const m = document.getElementById('orderModal'); return { role: m.getAttribute('role'), modal: m.getAttribute('aria-modal'), label: !!document.getElementById(m.getAttribute('aria-labelledby') || '_'), inside: m.contains(document.activeElement) }; });
  check(dlg.role === 'dialog' && dlg.modal === 'true' && dlg.label, 'Auftragsdialog: role=dialog, aria-modal, Überschrift verknüpft');
  check(dlg.inside, 'Auftragsdialog: Fokus liegt im Dialog');
  for (let i = 0; i < 40; i++) await page.keyboard.press('Tab');
  check(await page.evaluate(() => document.getElementById('orderModal').contains(document.activeElement)), 'Auftragsdialog: Tab bleibt im Dialog');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(150);
  check(!(await page.evaluate(() => document.getElementById('orderModal').classList.contains('show'))), 'Escape schließt den Auftragsdialog');
  check(await page.evaluate(() => document.activeElement?.id === 'quickAdd'), 'Fokus kehrt zu "+ Auftrag" zurück');
  check(await page.evaluate(() => document.getElementById('toast').getAttribute('aria-live') === 'polite'), 'Toast wird vorgelesen (aria-live)');

  // ------------------------------------------------------------------ eigener Eingabedialog statt prompt()
  await page.click('#navSystem');
  await page.waitForTimeout(300);
  const addBtn = page.locator('[data-res-add="cnc|machine"]');
  await addBtn.click();
  await page.waitForSelector('#askModal.show');
  check(await page.inputValue('#askInput') !== '', 'Neue Maschine: Eingabedialog mit Vorschlag');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(150);
  const count = () => page.evaluate(() => document.querySelectorAll('[data-res-del]').length);
  const before = await count();
  await addBtn.click();
  await page.waitForSelector('#askModal.show');
  await page.fill('#askInput', 'Smoke Maschine');
  await page.keyboard.press('Enter');
  await page.waitForTimeout(600);
  check(await count() === before + 1, `Neue Maschine per Dialog angelegt (${before} → ${await count()})`);
  const del = page.locator('.resRow:has(input[value="Smoke Maschine"]) [data-res-del]');
  await del.click();
  await page.waitForSelector('#askModal.show');
  check(await page.evaluate(() => document.getElementById('askOk').classList.contains('danger')), 'Entfernen: Bestätigung als Gefahr-Aktion');
  await page.click('#askOk');
  await page.waitForTimeout(600);
  check(await count() === before, 'Maschine über × entfernt (Knopf funktioniert wieder)');

  // ------------------------------------------------------------------ Fokus bleibt bei Änderung eines anderen Benutzers
  const name = page.locator('.resRow input[data-f="name"]').first();
  await name.click();
  await name.fill('Maschine 1 (tippe gerade)');
  const other = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await login(other);
  await other.click('#navSystem');
  await other.waitForTimeout(300);
  const setup = other.locator('.resRow input[data-f="setupMinutes"]').nth(1);
  await setup.fill('7');
  await setup.press('Tab');
  await page.waitForFunction(() => [...document.querySelectorAll('.resRow input[data-f="setupMinutes"]')].some(x => x.value === '7'), null, { timeout: 20000 });
  const kept = await page.evaluate(() => ({ v: document.activeElement?.value, f: document.activeElement?.dataset?.f }));
  check(kept.f === 'name' && kept.v === 'Maschine 1 (tippe gerade)', `Eingabe bleibt bei fremder Änderung erhalten (${JSON.stringify(kept)})`);
  await other.close();

  // ------------------------------------------------------------------ Priorität per Tastatur/Knopf (↑/↓)
  await page.click('#navPlan');
  for (const fs of ['FS 9001', 'FS 9002']) {
    await page.click('#quickAdd');
    await page.waitForSelector('#orderModal.show');
    await page.fill('#qFS', fs);
    await page.fill('#qHours', '4');
    await page.click('#createOrder');
    await page.waitForTimeout(700);
    if (await page.evaluate(() => document.getElementById('moveModal').classList.contains('show'))) { await page.click('#confirmMove'); await page.waitForTimeout(500); }
  }
  await page.click('#navList');
  await page.waitForTimeout(300);
  const orderRows = () => page.evaluate(() => [...document.querySelectorAll('#ordersBody tr[data-id] strong')].map(x => x.textContent));
  const beforeRows = await orderRows();
  check(beforeRows.length === 2, `Zwei Aufträge angelegt (${beforeRows.join(', ')})`);
  check(await page.evaluate(() => document.querySelector('#ordersBody [data-prio-move$="|-1"]')?.disabled === true), 'Oberster Auftrag: "Priorität höher" gesperrt');
  await page.locator('#ordersBody [data-prio-move$="|1"]').first().click();
  await page.waitForTimeout(500);
  if (await page.evaluate(() => document.getElementById('moveModal').classList.contains('show'))) { await page.click('#confirmMove'); await page.waitForTimeout(500); }
  const afterRows = await orderRows();
  check(afterRows[0] === beforeRows[1] && afterRows[1] === beforeRows[0], `"Priorität niedriger" tauscht die Reihenfolge (${afterRows.join(', ')})`);

  // ------------------------------------------------------------------ Projektfenster groß, Felder im Raster
  await page.click('#navOrders');
  await page.waitForTimeout(300);
  await page.click('#projectNew');
  await page.waitForSelector('#newProjectModal.show');
  await page.fill('#npCustomer', 'Smoke Kunde');
  await page.click('#npCreate');
  await page.waitForTimeout(600);
  if (!(await page.evaluate(() => document.getElementById('projectModal').classList.contains('show')))) await page.locator('#projectBoard [data-project-open]').first().click();
  await page.waitForSelector('#projectModal.show');
  const pm = await page.evaluate(() => { const b = document.querySelector('#projectModal .pModalBox').getBoundingClientRect(); const over = [...document.querySelectorAll('#projectModal .pGrid input,#projectModal .pGrid select')].filter(x => x.getBoundingClientRect().right > x.closest('.pGrid').getBoundingClientRect().right + 1).length; return { w: b.width, h: b.height, vw: innerWidth, vh: innerHeight, over }; });
  check(pm.w >= Math.min(1800, pm.vw * 0.95) - 2 && pm.h >= pm.vh * 0.9, `Projektfenster nutzt den Bildschirm (${Math.round(pm.w)}×${Math.round(pm.h)} bei ${pm.vw}×${pm.vh})`);
  check(pm.over === 0, `Projekt-Stammdaten: kein Feld ragt über den Rand (${pm.over})`);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(150);

  // ------------------------------------------------------------------ Personal ohne Mitarbeiter, Produktionsmodus
  await page.click('#navPersonnel');
  await page.waitForTimeout(300);
  check(await page.locator('#staffCoverage .covHead.info').isVisible(), 'Personal ohne Mitarbeiter: Hinweis statt Unterbesetzungs-Liste');
  await page.click('#modeProduction');
  await page.waitForTimeout(300);
  check((await page.textContent('#navPlan .txt')) === 'Produktion', 'Navigation zeigt "Produktion" im Produktionsmodus');
  await page.close();
} catch (e) {
  check(false, 'Ablauf: ' + (e?.message || e));
} finally {
  check(!errors.length, 'keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser.close();
  srv.kill();
  try { rmSync(dataDir, { recursive: true, force: true }); } catch {}
}
const failed = results.filter(r => !r[0]).length;
console.log(`\n${results.length - failed}/${results.length} bestanden`);
process.exit(failed ? 1 : 0);

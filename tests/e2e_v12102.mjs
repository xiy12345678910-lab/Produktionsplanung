#!/usr/bin/env node
// E2E Hotfix V12.10.2 (Audit V-03, V-12): Client verliert keine Änderungen, CSV/Arbeitstage/Ortszeit.
// Startet server.py mit leerer Datenbank in einem Temp-Ordner und prüft im Browser (Playwright/Chromium).
//
// Aufruf:  node tests/e2e_v12102.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18791;
const BASE = `http://127.0.0.1:${PORT}/`;
const USER = 'admin', PASS = 'Hotfix-Test-123';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}

const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-v12102-'));
const py = `
import ipaddress, sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR = Path(${JSON.stringify(dataDir)})
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
server.init_db(seed="werbetechnik")
server.create_or_reset_admin(${JSON.stringify(USER)}, ${JSON.stringify(PASS)})
httpd = server.MPHTTPServer(("127.0.0.1", ${PORT}), server.Handler)
print("READY", flush=True)
httpd.serve_forever()
`;
const srv = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, MP_CONFIG_DIR: path.join(dataDir, 'config') } });
await new Promise((resolve, reject) => {
  const t = setTimeout(() => reject(new Error('Server startet nicht')), 20000);
  srv.stdout.on('data', d => { if (String(d).includes('READY')) { clearTimeout(t); resolve(); } });
  srv.on('exit', c => reject(new Error('Server beendet: ' + c)));
});

const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
const errors = [];

// Das Skript läuft in einer IIFE. Nur im Test: Hook vor dem IIFE-Ende einschleusen, der Funktionen im
// IIFE-Scope auswertet (direktes eval). Die CSP der Testantwort wird dafür entfernt; Produktivcode bleibt unverändert.
async function installHook(page) {
  await page.route(u => new URL(u).pathname === '/' || new URL(u).pathname === '/index.html', async route => {
    const resp = await route.fetch();
    let body = await resp.text();
    const end = body.lastIndexOf('})();');
    body = body.slice(0, end) + 'window.__t=src=>eval(src);\n' + body.slice(end);
    const headers = { ...resp.headers() };
    delete headers['content-security-policy'];
    delete headers['content-length'];
    await route.fulfill({ status: resp.status(), headers, body });
  });
}
const ev = (page, fn, arg) => page.evaluate(([src, a]) => window.__t('(' + src + ')')(a), [fn.toString(), arg]);

async function login(page) {
  await installHook(page);
  await page.goto(BASE);
  await page.fill('#loginUser', USER);
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(400);
}
const until = async (page, fn, arg, ms = 8000) => { try { await page.waitForFunction(([src, a]) => window.__t('(' + src + ')')(a), [fn.toString(), arg], { timeout: ms, polling: 100 }); return true; } catch { return false; } };

try {
  const ctx = await browser.newContext({ acceptDownloads: true, timezoneId: 'Europe/Berlin' });
  const page = await ctx.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await login(page);

  // ------------------------------------------------------------------ F-H2: lokaler Cache schlank, Quota blockiert nicht
  const rev0 = await ev(page, () => serverRevision);
  await ev(page, () => { data.departments[0].name = data.departments[0].name.slice(0, 50) + ' A'; save('Test Cache', 'A'); });
  check(await until(page, r => serverRevision > r && !remoteDirty, rev0), 'Änderung gespeichert (Revision erhöht)');
  const cache = await ev(page, () => JSON.parse(localStorage.getItem(KEY) || '{}'));
  check(cache.workSteps && !('audit' in cache) && !('history' in cache) && !('planVersions' in cache), 'localStorage-Cache ohne Audit/Historie/Planversionen');

  const rev1 = await ev(page, () => serverRevision);
  const quota = await ev(page, () => {
    const orig = Storage.prototype.setItem;
    Storage.prototype.setItem = function () { throw new DOMException('voll', 'QuotaExceededError'); };
    try { data.departments[0].name = data.departments[0].name.replace(/ [AB]$/, '') + ' B'; return save('Test Quota', 'B'); }
    finally { setTimeout(() => { Storage.prototype.setItem = orig; }, 2000); }
  });
  check(quota === true, 'save() bei QuotaExceededError erfolgreich (kein „SERVER OFFLINE“)');
  check(await until(page, r => serverRevision > r && !remoteDirty, rev1), 'Änderung trotz vollem localStorage beim Server angekommen');
  check(await ev(page, () => serverReachable), 'Server bleibt als erreichbar markiert');

  // ------------------------------------------------------------------ F-M4/F-M3: offline erst nach 3 Fehlern, Backoff
  let aborted = 0;
  await page.route('**/api/revision**', r => { aborted++; return r.abort(); });
  await ev(page, () => { revisionPollInFlight = false; });
  const one = await until(page, () => pollFailures >= 1, null, 5000);
  check(one && await ev(page, () => serverReachable), 'Ein fehlgeschlagener Poll schaltet nicht auf offline');
  check(await until(page, () => !serverReachable, null, 15000), 'Nach 3 Fehlern in Folge: SERVER OFFLINE');
  const n = aborted;
  await page.waitForTimeout(1500);
  check(aborted - n <= 2, `Backoff: keine Poll-Flut bei Fehlern (${aborted - n} Anfragen in 1,5 s)`);
  await page.unroute('**/api/revision**');
  check(await until(page, () => serverReachable && pollFailures === 0, null, 40000), 'Nach Serverrückkehr wieder online');

  // ------------------------------------------------------------------ F-M2: Arbeitstage mit Kalender-Ausnahmen
  const wd = await ev(page, () => {
    const keep = data.exceptions;
    data.exceptions = [{ date: '2026-10-07', mode: '0' }, { date: '2026-10-10', mode: '1' }];
    const r = { between: workDaysBetween('2026-10-05', '2026-10-11'), diff: workDayDiff(new Date(2026, 9, 5), new Date(2026, 9, 12)), plain: workDaysBetween('2026-10-12', '2026-10-18') };
    data.exceptions = keep;
    return r;
  });
  check(wd.between === 5, `Arbeitstage Mo–So mit 1 Feiertag + 1 Sonderschicht Sa = 5 (${wd.between})`);
  check(wd.diff === 5, `workDayDiff berücksichtigt Feiertag/Sonderschicht (${wd.diff})`);
  check(wd.plain === 5, `Woche ohne Ausnahme = 5 Arbeitstage (${wd.plain})`);

  // ------------------------------------------------------------------ N9: UTC-Zeitstempel als Ortsdatum
  const ld = await ev(page, () => [localDayKey('2026-10-04T23:30:00.000Z'), localDayKey('2026-10-04T10:00'), localDayKey('2026-10-04')]);
  check(ld[0] === '2026-10-05', `Fertigmeldung 01:30 Uhr Berlin zählt zum 05.10. (${ld[0]})`);
  check(ld[1] === '2026-10-04' && ld[2] === '2026-10-04', 'Ortszeit-/Datumswerte unverändert');

  // ------------------------------------------------------------------ F-M1: CSV Ortszeit, Formelschutz, Dateiname
  const [dl] = await Promise.all([page.waitForEvent('download'), ev(page, () => {
    const m = data.machines[0];
    data.workSteps.push({ id: 'csv_t1', machineId: m.id, departmentId: m.departmentId || 'cnc', order: '=HYPERLINK("x")', fa: '=HYPERLINK("x")', description: '+SUMME(1)', articleNo: '@A', hours: 2, targetQty: 10, status: 'planned', pos: 999, planningType: 'MACHINE' });
    try { exportCSV(); } finally { data.workSteps = data.workSteps.filter(x => x.id !== 'csv_t1'); }
  })]);
  const name = dl.suggestedFilename();
  check(/^Produktionsplanung_Auftraege_\d{4}-\d\d-\d\d\.csv$/.test(name), `CSV-Dateiname ohne „CNC“ (${name})`);
  const csv = readFileSync(await dl.path(), 'utf8');
  check(csv.includes(`"'=HYPERLINK(""x"")"`) && csv.includes(`"'+SUMME(1)"`) && csv.includes(`"'@A"`), 'CSV: Formel-Zellen mit \' entschärft');
  check(!/\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z/.test(csv), 'CSV: keine UTC-ISO-Zeitstempel mehr');

  // ------------------------------------------------------------------ F-H1: verworfene Änderung bleibt abrufbar
  await ev(page, () => keepConflictCopy({ test: 1 }, ['Auftrag X']));
  check(await ev(page, () => document.getElementById('conflictCopyBtn').style.display !== 'none' && !!localStorage.getItem(KEY + '_KONFLIKT')), 'Konfliktkopie gespeichert, Knopf sichtbar');
  const [dl2] = await Promise.all([page.waitForEvent('download'), ev(page, () => downloadConflictCopy())]);
  const cc = JSON.parse(readFileSync(await dl2.path(), 'utf8'));
  check(cc.data?.test === 1 && cc.conflicts[0] === 'Auftrag X', 'Konfliktkopie als JSON herunterladbar');

  // ------------------------------------------------------------------ F-H4: beforeunload bei ungespeicherter Änderung
  let dialog = null;
  page.on('dialog', d => { dialog = d.type(); d.dismiss().catch(() => {}); });
  await ev(page, () => { clearTimeout(remoteTimer); remoteDirty = true; remoteSaving = true; });
  await page.close({ runBeforeUnload: true });
  await new Promise(r => setTimeout(r, 800));
  check(dialog === 'beforeunload', `Schließen mit ungespeicherter Änderung warnt (${dialog})`);
  await ctx.close();
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  await browser.close();
  srv.kill();
  try { rmSync(dataDir, { recursive: true, force: true }); } catch {}
}
check(!errors.length, `Keine JavaScript-Fehler ${errors.slice(0, 3).join(' | ')}`);
const failed = results.filter(r => !r[0]);
console.log(`\n${results.length - failed.length}/${results.length} bestanden`);
process.exit(failed.length ? 1 : 0);

#!/usr/bin/env node
// E2E Bereiche (ab V12.8.2): GF legt Produktions- und Entwicklungsbereich an; Bereichsrolle eines
// Entwicklungsbereichs sieht nur Projekte. Startet server.py mit Testdaten in einem Temp-Ordner.
//
// Aufruf:  node tests/e2e_departments.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18783;
const BASE = `http://127.0.0.1:${PORT}/`;
const PASS = 'E2E-Test-1234';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-dep-'));
const py = `
import ipaddress, sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR = Path(${JSON.stringify(dataDir)})
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
server.init_db(seed="werbetechnik")
server.create_or_reset_admin("admin", ${JSON.stringify(PASS)})
with server.DB_LOCK, server.db_session() as con:
    salt, digest = server.hash_password(${JSON.stringify(PASS)})
    con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", ("chef", salt, digest, "gf", "", server.now_iso(), server.now_iso()))
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
async function open(user) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, timezoneId: 'Europe/Berlin' });
  page.on('pageerror', e => errors.push(`${user}: ${e.message}`));
  await page.goto(BASE);
  await page.fill('#loginUser', user);
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(400);
  return page;
}
const serverState = page => page.evaluate(async () => (await (await fetch('/api/state', { cache: 'no-store' })).json()).data);
const errText = page => page.evaluate(() => document.getElementById('errorModal')?.classList.contains('show') ? document.getElementById('errorModal').innerText.replace(/\s+/g, ' ') : '');
// Stand holen, in Node verändern, zurückschreiben (die Seite erlaubt kein eval – CSP)
async function putState(page, mutate) {
  const r = await page.evaluate(async () => (await fetch('/api/state', { cache: 'no-store' })).json());
  mutate(r.data);
  return page.evaluate(async body => {
    const res = await fetch('/api/state', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': (await (await fetch('/api/health')).json()).version }, body: JSON.stringify(body) });
    return [res.status, (await res.json()).errorCode || 'ok'];
  }, { revision: r.revision, data: r.data, action: 'Test' });
}

try {
  const gf = await open('chef');
  check(await gf.evaluate(() => document.querySelector('.view.active')?.id) === 'gf', 'GF startet in der GF-Ansicht');
  await gf.evaluate(() => { document.getElementById('gfDeptPanel').open = true; });
  check(await gf.locator('#gfDeptAdmin .depRow').count() === 6, 'GF: 6 vorhandene Bereiche gelistet');

  await gf.fill('#depNewName', 'Lackierung');
  await gf.selectOption('#depNewRes', 'line');
  await gf.fill('#depNewResName', 'Lackierlinie');
  await gf.selectOption('#depNewShift', '2');
  await gf.fill('#depNewSetup', '15');
  await gf.click('#depNewAdd');
  await gf.waitForTimeout(800);
  let st = await serverState(gf);
  const lack = st.departments.find(d => d.name === 'Lackierung');
  const lm = lack && st.machines.find(m => m.departmentId === lack.id);
  check(lack && !lack.kind, `Produktionsbereich Lackierung angelegt ${await errText(gf)}`);
  check(lm && lm.name === 'Lackierlinie' && lm.kind === 'line' && lm.defaultShiftMode === '2' && lm.setupMinutes === 15, 'Erste Linie mit Schichtmodell 2-schichtig und 15 min Umrüsten');

  await gf.evaluate(() => { document.getElementById('gfDeptPanel').open = true; });
  check(await gf.evaluate(() => [...document.querySelectorAll('#depNewKind option')].map(o => o.textContent).join('|')) === 'Produktion|Entwicklung & Vertrieb', 'Nur zwei Bereichsarten: Produktion | Entwicklung & Vertrieb');
  await gf.fill('#depNewName', 'Musterbau');
  await gf.selectOption('#depNewKind', 'development');
  check(!(await gf.locator('#depNewRes').isVisible()), 'Entwicklung: keine Maschinen-Felder');
  await gf.click('#depNewAdd');
  await gf.waitForTimeout(800);
  st = await serverState(gf);
  const dev = st.departments.find(d => d.name === 'Musterbau');
  check(dev?.kind === 'development' && !st.machines.some(m => m.departmentId === dev.id), 'Entwicklungsbereich Musterbau ohne Maschine angelegt');

  check(await gf.evaluate(() => ![...document.querySelectorAll('#gfDepartmentGrid *')].some(x => x.children.length === 0 && x.textContent.trim() === 'Musterbau')), 'GF-Raster zeigt nur Produktionsbereiche');
  check(await gf.evaluate(id => ![...document.getElementById('departmentScopeSelect').options].some(o => o.value === id), dev.id), 'Bereichsauswahl ohne Entwicklungsbereich');
  check(await gf.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'GF-Ansicht: kein seitlicher Seiten-Scroll');

  // Umbenennen + Deaktivieren
  await gf.evaluate(() => { document.getElementById('gfDeptPanel').open = true; });
  const nm = gf.locator(`[data-dep="${dev.id}"][data-k="name"]`);
  await nm.fill('Musterbau & Entwicklung');
  await nm.press('Tab');
  await gf.waitForTimeout(700);
  await gf.evaluate(() => { document.getElementById('gfDeptPanel').open = true; });
  await gf.locator(`[data-dep="${dev.id}"][data-k="active"]`).uncheck();
  await gf.waitForTimeout(700);
  st = await serverState(gf);
  const dev2 = st.departments.find(d => d.id === dev.id);
  check(dev2.name === 'Musterbau & Entwicklung' && dev2.active === false, 'GF benennt um und deaktiviert');

  // Art ändern bei Bereich mit Maschinen gesperrt
  await gf.evaluate(() => { document.getElementById('gfDeptPanel').open = true; });
  await gf.selectOption(`[data-dep="cnc"][data-k="kind"]`, 'development');
  await gf.waitForTimeout(400);
  check((await errText(gf)).includes('MP-DEPT-004'), 'CNC (mit Maschinen) kann nicht Entwicklung & Vertrieb werden (MP-DEPT-004)');
  await gf.keyboard.press('Escape');

  // Server-Rechte der GF
  const delRes = await putState(gf, d => { d.departments = d.departments.filter(x => x.name !== 'Musterbau & Entwicklung'); });
  check(delRes[0] === 403, `GF: Bereich löschen per API abgelehnt (${delRes.join(' ')})`);
  check((await putState(gf, d => { d.machines[0].setupMinutes = 33; }))[0] === 403, 'GF: bestehende Maschine ändern per API abgelehnt');
  await gf.close();

  // Bereichsleitung eines Entwicklungsbereichs: nur Projekte
  const admin = await open('admin');
  await putState(admin, d => { delete d.departments.find(x => x.id === dev.id).active; });
  const created = await admin.evaluate(async id => (await fetch('/api/users', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': (await (await fetch('/api/health')).json()).version }, body: JSON.stringify({ username: 'devlead', password: 'E2E-Test-1234', role: 'department_lead', departmentId: id }) })).status, dev.id);
  check(created === 201, 'Admin legt Leitung für Entwicklungsbereich an');
  check((await putState(admin, d => { d.machines.push({ ...d.machines[0], id: 'mx1', departmentId: dev.id }); }))[1] === 'MP-DEPT-004', 'Server: Maschine im Entwicklungsbereich abgelehnt (auch Admin)');
  const seeded = await putState(admin, d => { d.projects = [{ id: 'p1', number: 'P-2026-0001', phase: 'inquiry', name: 'Test', customer: 'K', ab: '', dueDate: '', log: [], processes: [{ id: 'pr1', areaId: dev.id, title: 'Muster bauen', status: 'open', startDate: '2026-10-06', dueDate: '2026-10-09' }] }]; });
  check(seeded[0] === 200, `Projekt mit Aufgabe im Entwicklungsbereich (${seeded.join(' ')})`);
  await admin.reload(); await admin.waitForTimeout(600);
  await admin.click('#navOrders'); await admin.waitForTimeout(300);
  await admin.locator('[data-project-open="p1"]').first().click(); await admin.waitForTimeout(500);
  check(await admin.evaluate(() => document.querySelector('#projectModal .modalBox').offsetWidth) > 1000, 'Projektfenster nutzt die Breite (Status sichtbar)');
  check(await admin.evaluate(() => { const g = [...document.querySelectorAll('#projectModal optgroup')].map(x => x.label); return g.join('|'); }) === 'Entwicklung & Vertrieb|Produktion', 'Bereichsauswahl im Projekt gegliedert: Entwicklung & Vertrieb | Produktion');
  await admin.close();

  const devlead = await open('devlead');
  check(await devlead.evaluate(() => document.querySelector('.view.active')?.id) === 'projects', 'Entwicklungs-Leitung startet in Projekten');
  check(!(await devlead.locator('#navPlan').isVisible()) && !(await devlead.locator('#navPersonnel').isVisible()) && !(await devlead.locator('#navSystem').isVisible()), 'Entwicklungs-Leitung: kein Wochenplan/Personal/System');
  await devlead.locator('[data-project-open="p1"]').first().click(); await devlead.waitForTimeout(500);
  check(await devlead.locator('[data-fexp="pr1"] .fPill').isVisible(), 'Entwicklungs-Leitung sieht den Status ihrer Aufgabe');
  await devlead.click('[data-fexp="pr1"]'); await devlead.waitForTimeout(300);
  check(!(await devlead.locator('[data-proc-status="pr1"]').isDisabled()), 'Entwicklungs-Leitung kann den Status melden');
  check(!(await devlead.locator('#projectModal [data-proc-take]').count()), 'Entwicklungsbereich: kein "Als Auftrag einplanen"');
  await devlead.selectOption('[data-proc-status="pr1"]', 'in_progress'); await devlead.waitForTimeout(800);
  check((await serverState(devlead)).projects[0].processes[0].status === 'in_progress', 'Status "In Arbeit" gespeichert');
  await devlead.close();
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

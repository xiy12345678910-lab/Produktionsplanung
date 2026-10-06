#!/usr/bin/env node
// E2E V12.17.1: Personal-Gate gilt auch fuer Linien (crew). 0 Mitarbeiter + Gate EIN => nicht als Produktion eingeplant.
// Startet server.py mit leerer Datenbank in einem Temp-Ordner und prüft im Browser (Playwright/Chromium).
//
// Aufruf:  node tests/e2e_gate_lines.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18799;
const BASE = `http://127.0.0.1:${PORT}/`;
const USER = 'admin', PASS = 'Gate-Test-1234';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}

const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-gate-'));
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
const srv = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'] });
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
  const ctx = await browser.newContext({ timezoneId: 'Europe/Berlin' });
  const page = await ctx.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await login(page);

  // Linie "Konfektion 1" (1 Pers.), Auftrag 40 h, keine Mitarbeiter
  await ev(page, () => {
    const dep = data.departments.find(d => d.active !== false && String(d.kind || 'production') === 'production');
    data.machines.push({ id: 'kf1', name: 'Konfektion 1', departmentId: dep.id, kind: 'line', crew: 1, setupMinutes: 0, start: dateKey(monday(new Date())) + 'T06:30', committedUntil: '', defaultShiftMode: '1', staffRequired: 0 });
    data.employees = []; data.personnelAssignments = []; data.personnelAbsences = [];
    data.workSteps.push({ id: 'ws_rrs', projectId: '', sequence: 1, departmentId: dep.id, planningType: 'MACHINE', predecessorIds: [], pos: 1, machineId: 'kf1', altMachineId: '', allowAlternative: false, fs: 'rrs', ab: '', wt: '', order: 'rrs', articleNo: '', description: '', targetQty: 0, hours: 40, status: 'planned', direction: 'forward', anchorMode: 'none', requiredStart: '', requiredFinish: '', dueDate: '' });
    data.personnelGate = false;
  });
  const sched = () => ev(page, () => { const r = calcSchedule()['ws_rrs']; return { start: !!r.start, segs: r.segments.length, conflict: r.conflict || '', unstaffed: !!r.unstaffed }; });

  const off = await sched();
  check(off.start && off.segs > 0 && !off.conflict, `Gate AUS: Linie wird eingeplant wie bisher (${off.segs} Segmente)`);

  await ev(page, () => { data.personnelGate = true; });
  const on = await sched();
  check(!on.start && on.segs === 0 && on.unstaffed, 'Gate EIN, 0 Mitarbeiter: Auftrag nicht als Produktion eingeplant, als unbesetzt markiert');
  check(/MP-PERS-003/.test(on.conflict), `Konflikttext mit Code (${on.conflict})`);
  const short = await ev(page, () => coverageForWeek(monday(new Date())).filter(x => !x.ok && x.req > 0 && x.machine.id === 'kf1').length);
  check(short > 0, `Unterbesetzte Schichten der Linie > 0 (${short})`);
  await ev(page, () => { renderAll(); });
  const ui = await page.evaluate(() => ({
    pill: document.querySelector('#conflictTray [data-conflict-edit]')?.innerHTML || '',
    staffBtn: !!document.querySelector('#conflictTray [data-conflict-staff]'),
    late: document.getElementById('mLate').textContent,
  }));
  check(ui.pill.includes('👤⚠') && ui.staffBtn, 'Markierung mit Symbol und Aktion „Personal zuordnen“ im Wochenplan');
  check(Number(ui.late) >= 1, `Termin gefährdet zählt den unbesetzten Auftrag (${ui.late})`);
  await page.click('#conflictTray [data-conflict-staff]');
  check(await ev(page, () => data.ui.view === 'personnel'), 'Aktion führt zur Personalplanung');
  const sh = await ev(page, () => { renderPersonnel(); return document.getElementById('staffShortages').textContent; });
  check(Number(sh) > 0, `Kachel „Unterbesetzte Schichten“ > 0 bei 0 Mitarbeitern (${sh})`);

  // Mitarbeiter zuordnen -> wird eingeplant
  await ev(page, () => {
    const dep = machine('kf1').departmentId;
    data.employees.push({ id: 'e_t1', name: 'Thomsen', active: true, departmentId: dep, skills: ['kf1'], homeMachineId: 'kf1', homeShift: 'auto', employmentType: 'permanent', weeklyHours: 40, function: '' });
  });
  const staffed = await sched();
  check(staffed.start && staffed.segs > 0 && !staffed.unstaffed && !staffed.conflict, `Mit zugeordnetem Mitarbeiter wird eingeplant (${staffed.segs} Segmente)`);

  // V12.19.0: Besetzung der Linie (crew) ist die MAXIMALE Besetzung; weniger Personen reichen
  await ev(page, () => { machine('kf1').crew = 2; });
  const crew2 = await sched();
  check(crew2.start && !crew2.unstaffed, 'Crew 2 (Maximum) mit nur 1 Mitarbeiter: wird eingeplant');
  await ev(page, () => { machine('kf1').staffRequired = 2; });
  const min2 = await sched();
  check(!min2.start && min2.unstaffed, 'Mindestpersonal 2 mit nur 1 Mitarbeiter: unbesetzt');
  await ev(page, () => { machine('kf1').staffRequired = 0; });
  await ev(page, () => { data.personnelGate = false; });
  const off2 = await sched();
  check(off2.start, 'Gate AUS: weiterhin nur Planung');
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

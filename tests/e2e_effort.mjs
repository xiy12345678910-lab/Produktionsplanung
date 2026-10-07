#!/usr/bin/env node
// E2E V12.18.0: Dauer nach Besetzung (P18) und Personal je Parallelplatz (P16).
// Nutzerbeispiele: 40 Ph mit 1 / 2 / 4 Mitarbeitern = 40 / 20 / 10 h; 8 Ph mit 1 / 2 Mitarbeitern = 8 / 4 h.
// Startet server.py mit leerer Datenbank in einem Temp-Ordner und prüft im Browser (Playwright/Chromium).
//
// Aufruf:  node tests/e2e_effort.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18821;
const BASE = `http://127.0.0.1:${PORT}/`;
const USER = 'admin', PASS = 'Effort-Test-1234';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}

const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };
const near = (a, b, eps = 0.01) => Math.abs(a - b) < eps;

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-effort-'));
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

// Das Skript läuft in einer IIFE. Nur im Test: Hook vor dem IIFE-Ende einschleusen (CSP der Testantwort entfernt).
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

try {
  const ctx = await browser.newContext({ timezoneId: 'Europe/Berlin' });
  const page = await ctx.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await login(page);

  // Grundaufbau: Bereich, Ressource "kf1" (Konfektion) mit n Mitarbeitern, Auftrag "o1" mit h Stunden.
  // cfg: {kind, crew, staffRequired, lanes, laneStaff, effort, crewMax, gate, emps, hours, orders}
  const setup = async cfg => { await ev(page, async () => await flushNow()); return ev(page, c => {
    const dep = data.departments.find(d => d.active !== false && String(d.kind || 'production') === 'production');
    data.machines = data.machines.filter(m => m.id !== 'kf1');
    const m = { id: 'kf1', name: 'Konfektion 1', departmentId: dep.id, kind: c.kind || 'line', crew: c.crew || 1, setupMinutes: c.setup || 0, start: dateKey(monday(new Date())) + 'T06:30', committedUntil: '', defaultShiftMode: '1', staffRequired: c.staffRequired || 0, effortScaling: !!c.effort, crewMax: c.crewMax || 0, laneStaff: c.laneStaff || {} };
    if (c.lanes > 1) m.lanes = c.lanes;
    data.machines.push(m);
    data.employees = []; data.personnelAssignments = []; data.personnelAbsences = [];
    for (let i = 1; i <= (c.emps || 0); i++) data.employees.push({ homeLaneIndex:c.lanes>1&&c.laneStaff? (i<=Number(c.laneStaff['1']||1)?1:2):1,id: 'e_' + i, name: 'MA ' + i, active: true, departmentId: dep.id, skills: ['kf1'], homeMachineId: 'kf1', homeShift: 'auto', employmentType: 'permanent', weeklyHours: 40, function: '' });
    data.workSteps = [];
    (c.orders || [c.hours || 40]).forEach((h, i) => data.workSteps.push({ id: 'o' + (i + 1), projectId: '', sequence: i + 1, departmentId: dep.id, planningType: 'MACHINE', predecessorIds: [], pos: i + 1, machineId: 'kf1', altMachineId: '', allowAlternative: false, fa: 'FA' + (i + 1), ab: '', wt: '', order: 'FA' + (i + 1), articleNo: '', description: '', targetQty: 0, hours: h, status: 'planned', direction: 'forward', anchorMode: 'none', requiredStart: '', requiredFinish: '', dueDate: '' }));
    data.personnelGate = !!c.gate;
  }, cfg); };
  const plan = (id = 'o1') => ev(page, id => {
    const r = calcSchedule()[id];
    const segs = r.segments || [];
    return { start: !!r.start, unstaffed: !!r.unstaffed, conflict: r.conflict || '', wall: segs.reduce((t, s) => t + (s.end - s.start) / 3600000, 0), eff: segs.reduce((t, s) => t + (s.end - s.start) / 3600000 * (s.crew || 0), 0), crews: segs.map(s => s.crew || 0), days: segs.map(s => dateKey(s.start)), text: r.effort ? effortText(r.effort) : '', tip: r.effort ? effortTip(r.effort) : '', first: segs[0] ? segs[0].start.getTime() : 0, last: segs.length ? segs[segs.length - 1].end.getTime() : 0 };
  }, id);

  // ---- Schalter aus: feste Dauer wie bisher ----
  await setup({ effort: false, crew: 1, gate: false, hours: 40 });
  let p = await plan();
  check(p.start && near(p.wall, 40), `Schalter aus, crew 1: feste Dauer 40 h (${p.wall.toFixed(2)})`);
  check(p.crews.every(c => c === 0) && p.text === '', 'Schalter aus: keine Besetzungsangaben an den Segmenten');
  await setup({ effort: false, crew: 2, gate: false, hours: 40 });
  p = await plan();
  check(near(p.wall, 20), `Schalter aus, Linie crew 2: bisherige Logik Personenstunden / crew = 20 h (${p.wall.toFixed(2)})`);
  await setup({ effort: false, crew: 1, gate: true, emps: 4, hours: 40 });
  p = await plan();
  check(near(p.wall, 40), `Schalter aus, Gate EIN mit 4 zugeordneten Mitarbeitern: weiter 40 h (${p.wall.toFixed(2)})`);

  // ---- Nutzerbeispiele, Gate EIN: tatsächlich zugeordnete Mitarbeiter ----
  for (const [ph, emps, want] of [[40, 1, 40], [40, 2, 20], [40, 4, 10], [8, 1, 8], [8, 2, 4]]) {
    await setup({ effort: true, gate: true, emps, hours: ph });
    p = await plan();
    check(p.start && near(p.wall, want) && near(p.eff, ph), `Gate EIN: ${ph} Ph mit ${emps} MA = ${want} h (${p.wall.toFixed(2)} h, ${p.eff.toFixed(2)} Ph)`);
  }
  await setup({ effort: true, gate: true, emps: 2, hours: 40 });
  p = await plan();
  check(p.text === '40 Ph · 2 Pers. → 20 h', `Anzeige am Auftrag: "${p.text}"`);
  check(/Pers\./.test(p.tip), `Tooltip nennt die Besetzung je Tag: "${p.tip.slice(0, 80)}"`);

  // ---- Obergrenze ----
  await setup({ effort: true, gate: true, emps: 4, crewMax: 3, hours: 40 });
  p = await plan();
  check(near(p.wall, 40 / 3), `Max. Besetzung 3 bei 4 Mitarbeitern: 13,33 h (${p.wall.toFixed(2)})`);

  // ---- Gemischte Besetzung über Tage: erster Tag 2 MA, danach 4 MA ----
  await setup({ effort: true, gate: true, emps: 4, hours: 40 });
  const d1 = (await plan()).days[0];
  await ev(page, d => { data.personnelAbsences = [{ employeeId: 'e_3', date: d, label: 'Urlaub' }, { employeeId: 'e_4', date: d, label: 'Urlaub' }]; }, d1);
  p = await plan();
  const firstDay = p.crews.filter((c, i) => p.days[i] === d1), later = p.crews.filter((c, i) => p.days[i] !== d1);
  check(firstDay.length > 0 && firstDay.every(c => c === 2) && later.length > 0 && later.every(c => c === 4), `Erster Tag 2 MA, danach 4 MA (${[...new Set(p.crews)].join('/')})`);
  check(near(p.eff, 40), `Restarbeit wird schichtweise abgearbeitet: Summe = 40 Ph (${p.eff.toFixed(2)})`);
  check(p.text.includes('2–4 Pers.'), `Anzeige bei wechselnder Besetzung: "${p.text}"`);

  // ---- Änderung der Zuordnung: sofort neu geplant ----
  await setup({ effort: true, gate: true, emps: 2, hours: 40 });
  const w2 = (await plan()).wall;
  await ev(page, () => { data.employees.push({ id: 'e_9', name: 'MA 9', active: true, departmentId: machine('kf1').departmentId, skills: ['kf1'], homeMachineId: 'kf1', homeShift: 'auto', employmentType: 'permanent', weeklyHours: 40, function: '' }); data.employees.push({ id: 'e_10', name: 'MA 10', active: true, departmentId: machine('kf1').departmentId, skills: ['kf1'], homeMachineId: 'kf1', homeShift: 'auto', employmentType: 'permanent', weeklyHours: 40, function: '' }); });
  const w4 = (await plan()).wall;
  check(near(w2, 20) && near(w4, 10), `Zwei Mitarbeiter mehr zugeordnet: 20 h -> 10 h (${w2.toFixed(1)} -> ${w4.toFixed(1)})`);
  const ui = await ev(page, () => { renderAll(); return { row: document.querySelector('#ordersBody tr')?.innerText || '', tip: document.querySelector('#ordersBody [title^="Dauer nach Besetzung"]')?.getAttribute('title') || '', kpi: document.getElementById('mLate')?.textContent }; });
  check(/40 Ph · 4 Pers\. → 10 h/.test(ui.row), 'Auftragsliste zeigt "40 Ph · 4 Pers. → 10 h" nach der Änderung');
  check(ui.tip.length > 20, 'Tooltip am Auftrag vorhanden');

  // ---- Gate AUS: es zählt die geplante crew ----
  await setup({ effort: true, kind: 'line', crew: 4, gate: false, emps: 1, hours: 40 });
  p = await plan();
  check(near(p.wall, 10), `Gate AUS, crew 4 (nur 1 MA zugeordnet): 10 h (${p.wall.toFixed(2)})`);
  await setup({ effort: true, kind: 'machine', crew: 2, gate: false, hours: 8 });
  p = await plan();
  check(near(p.wall, 4), `Gate AUS, Maschine crew 2: 8 Ph = 4 h (${p.wall.toFixed(2)})`);
  await setup({ effort: true, kind: 'line', crew: 4, crewMax: 2, gate: false, hours: 40 });
  p = await plan();
  check(near(p.wall, 20), `Gate AUS, crew 4 mit Max. Besetzung 2: 20 h (${p.wall.toFixed(2)})`);

  // ---- Unter Mindestbesetzung bleibt der Auftrag unbesetzt ----
  await setup({ effort: true, kind: 'machine', staffRequired: 2, gate: true, emps: 1, hours: 40 });
  p = await plan();
  check(!p.start && p.unstaffed && /MP-PERS-003/.test(p.conflict), `Mindestbesetzung 2, 1 MA: unbesetzt (${p.conflict.slice(0, 40)})`);
  await setup({ effort: true, kind: 'machine', staffRequired: 2, gate: true, emps: 2, hours: 40 });
  p = await plan();
  check(p.start && near(p.wall, 20), `Mindestbesetzung 2, 2 MA: eingeplant, 20 h (${p.wall.toFixed(2)})`);
  await setup({ effort: true, gate: true, emps: 0, hours: 40 });
  p = await plan();
  check(!p.start && p.unstaffed, 'Linie ohne Mitarbeiter bei Gate EIN: unbesetzt');

  // ---- Umrüstzeit zählt als Wandzeit, nicht als Personenstunden ----
  await setup({ effort: true, gate: true, emps: 2, setup: 30, hours: 40 });
  p = await plan();
  check(near(p.eff, 40) && near(p.wall, 20.5), `Umrüsten 30 min + 40 Ph mit 2 MA = 20,5 h (${p.wall.toFixed(2)})`);

  // ---- Freigabe + Server: Plan wird gespeichert (Server prüft Personenstunden und Besetzung) ----
  await setup({ effort: true, gate: true, emps: 2, hours: 40 });
  const rel = await ev(page, async () => {
    const o = data.workSteps.find(x => x.id === 'o1');
    if (!releaseOrder(o)) return { ok: false };
    save('Test Freigabe', 'o1');
    const flushed = await flushNow();
    const r = await fetch('/api/state', { credentials: 'same-origin' }).then(x => x.json());
    const st = (r.data.workSteps || []).find(x => x.id === 'o1');
    return { ok: true, flushed, status: st?.status, effort: st?.baselinePlan?.effort === true, crews: [...new Set((st?.baselinePlan?.segments || []).map(s => s.crew))], shown: calcSchedule()['o1'].effort ? effortText(calcSchedule()['o1'].effort) : '' };
  });
  check(rel.ok && rel.flushed && rel.status === 'released' && rel.effort, `Freigabe wird vom Server angenommen (${JSON.stringify(rel.crews)})`);
  check(rel.shown === '40 Ph · 2 Pers. → 20 h', `Freigegebener Auftrag zeigt den eingefrorenen Plan: "${rel.shown}"`);

  // ---- Einstellungen: Schalter als Symbol mit Tooltip ----
  await setup({ effort: false, gate: false, hours: 40 });
  const st = await ev(page, () => {
    renderMachineSettings(true, false, '');
    const cb = document.querySelector('#machineSettings input[data-m="kf1"][data-f="effortScaling"]');
    const mx = document.querySelector('#machineSettings input[data-m="kf1"][data-f="crewMax"]');
    if (!cb) return { found: false };
    const before = machine('kf1').effortScaling;
    cb.checked = true; cb.dispatchEvent(new Event('change', { bubbles: true }));
    return { found: true, title: cb.getAttribute('title') || '', aria: cb.getAttribute('aria-label') || '', maxTitle: mx?.getAttribute('title') || '', before, after: machine('kf1').effortScaling };
  });
  check(st.found && st.title.length > 10 && st.aria && st.maxTitle, 'Einstellungen: Schalter und Max. Besetzung mit Tooltip und aria-label');
  check(st.before === false && st.after === true, 'Schalter in den Einstellungen schaltet "Dauer nach Besetzung" ein');

  // ---- P16: Personal je Parallelplatz ----
  await setup({ effort: false, kind: 'machine', staffRequired: 1, lanes: 2, laneStaff: { 1: 1, 2: 2 }, gate: true, emps: 1, orders: [8, 8] });
  const overlap = async () => ev(page, () => { const s = calcSchedule(), a = s.o1, b = s.o2; if (!a.start || !b.start) return 'unplanned:' + !!a.start + !!b.start; return b.start < a.end && a.start < b.end ? 'parallel' : 'seriell'; });
  check(await overlap() === 'seriell', 'Platz 2 braucht 2 Personen: mit 1 MA laufen die Aufträge nacheinander');
  await ev(page, () => { for (let i = 2; i <= 3; i++) data.employees.push({ homeLaneIndex:2,id: 'e_' + i, name: 'MA ' + i, active: true, departmentId: machine('kf1').departmentId, skills: ['kf1'], homeMachineId: 'kf1', homeShift: 'auto', employmentType: 'permanent', weeklyHours: 40, function: '' }); });
  check(await overlap() === 'parallel', 'Bedarf Platz 1 + Platz 2 = 3 Personen: mit 3 MA laufen beide parallel');
  await setup({ effort: false, kind: 'machine', staffRequired: 1, lanes: 2, laneStaff: {}, gate: true, emps: 1, orders: [8, 8] });
  check(await overlap() === 'seriell', 'Ein Mitarbeiter belegt einen Parallelplatz; zweiter Auftrag läuft danach');
  await setup({ effort: false, kind: 'machine', staffRequired: 1, lanes: 2, laneStaff: { 1: 1, 2: 2 }, gate: true, emps: 1, orders: [8] });
  check((await plan('o1')).start, 'Ein belegter Platz: Bedarf = Bedarf Platz 1 (1 MA genügt)');

  // ---- Nachkalkulation: Ist- und Personenstunden bleiben konsistent ----
  const ev2 = await ev(page, () => {
    const seg = [{ start: '2026-09-07T06:30:00.000Z', end: '2026-09-07T11:30:00.000Z', shift: 'single' }, { start: '2026-09-08T06:30:00.000Z', end: '2026-09-08T11:30:00.000Z', shift: 'single' }];
    const a = evalRecord({ machineId: 'kf1', hours: 40, actualSegments: seg, actualPersonHours: 40 });
    const b = evalRecord({ machineId: 'kf1', hours: 10, actualSegments: seg });
    return { ist: a.ist, run: a.run, crew: a.crew, soll: a.soll, istOld: b.ist, crewOld: b.crew };
  });
  check(near(ev2.ist, 40) && near(ev2.run, 10) && near(ev2.crew, 4) && near(ev2.soll, 40), `Auswertung: 10 h Laufzeit mit 4 Pers. = 40 Ph Ist gegen 40 Ph Soll (${ev2.ist}/${ev2.soll})`);
  check(near(ev2.istOld, ev2.run * ev2.crewOld), 'Auswertung ohne Personenstunden im Datensatz rechnet wie bisher (Laufzeit x crew)');
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

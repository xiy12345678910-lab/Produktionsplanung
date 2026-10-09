#!/usr/bin/env node
// #84: Sonderschichten je Maschine/Bereich (Sa/So, Überstunden) im Schichtkalender.
// node tests/e2e_day_rules.mjs
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18982, BASE = `http://127.0.0.1:${PORT}/`, PASS = 'E2E-Day-1234';
const tmp = mkdtempSync(path.join(tmpdir(), 'mp-day-'));
const py = `
import ipaddress,sys
from pathlib import Path
sys.path.insert(0,${JSON.stringify(SRC)})
import server
server.DATA_DIR=Path(${JSON.stringify(tmp)})
server.DB_PATH=server.DATA_DIR/'maschinenplanung.sqlite3'
server.ALLOWED_NETWORK=ipaddress.ip_network('127.0.0.0/8')
server.init_db(seed='werbetechnik')
server.create_or_reset_admin('admin',${JSON.stringify(PASS)})
httpd=server.MPHTTPServer(('127.0.0.1',${PORT}),server.Handler)
print('READY',flush=True)
httpd.serve_forever()
`;
const srv = spawn('python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, MP_CONFIG_DIR: path.join(tmp, 'config') } });
const results = [], errors = [];
const check = (ok, label) => { results.push(!!ok); console.log((ok ? 'PASS ' : 'FAIL ') + label); };
let browser;
try {
  await new Promise((resolve, reject) => { const t = setTimeout(() => reject(new Error('Server start timeout')), 20000); srv.stdout.on('data', d => { if (String(d).includes('READY')) { clearTimeout(t); resolve(); } }); srv.on('exit', c => reject(new Error('Server exit ' + c))); });
  const { chromium } = await import('playwright'); browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, timezoneId: 'Europe/Berlin' });
  page.on('pageerror', e => errors.push(e.message));
  // Nur im Test: Zugriff auf die Planungslogik in der IIFE (CSP der Testantwort entfernt).
  await page.route(u => ['/', '/index.html'].includes(new URL(u).pathname), async route => {
    const resp = await route.fetch(); let body = await resp.text(); const end = body.lastIndexOf('})();');
    body = body.slice(0, end) + 'window.__t=src=>eval(src);\n' + body.slice(end);
    const headers = { ...resp.headers() }; delete headers['content-security-policy']; delete headers['content-length'];
    await route.fulfill({ status: resp.status(), headers, body });
  });
  const ev = (fn, arg) => page.evaluate(([src, a]) => window.__t('(' + src + ')')(a), [fn.toString(), arg]);
  await page.goto(BASE); await page.fill('#loginUser', 'admin'); await page.fill('#loginPassword', PASS); await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show')); await page.waitForTimeout(400);


  // System → Maschinen & Schichtkalender
  const info = await ev(() => { data.ui.view = 'settings'; data.ui.sysTab = 'machines'; switchView('settings'); renderAll();
    const cnc = data.machines.filter(m => deptOfMachine(m.id) === 'cnc').map(m => m.id), d = new Date(), sat = new Date(d); sat.setDate(d.getDate() + ((6 - d.getDay() + 7) % 7 || 7));
    const sun = new Date(sat); sun.setDate(sat.getDate() + 1); const mon = new Date(sat); mon.setDate(sat.getDate() + 2);
    return { cnc, sat: dateKey(sat), sun: dateKey(sun), mon: dateKey(mon), start: data.shiftTemplates.single.start }; });
  await page.waitForTimeout(400);
  await page.selectOption('#ruleScope', 'days');
  check(await page.locator('#ruleFrom').isVisible() && await page.locator('#ruleExtra').isVisible() && !(await page.locator('#ruleYear').isVisible()) && !(await page.locator('#ruleWeek').isVisible()), 'Gilt für „Tage“: Von/Bis und Überstunden statt Jahr/KW');
  check(await page.locator('#ruleMachine option[value="dep:cnc"]').count() === 1, 'Ziel: ganzer Bereich wählbar');

  // Sa+So für den ganzen Bereich CNC 1-schichtig
  const satWork0 = await ev(s => workIntervalsForDateRaw(parseLocal(s.sat), s.cnc[0]).length, info);
  await page.selectOption('#ruleMachine', 'dep:cnc'); await page.fill('#ruleFrom', info.sat); await page.fill('#ruleTo', info.sun); await page.selectOption('#ruleMode', '1');
  await page.click('#addRule'); await page.waitForTimeout(800);
  const serverData = () => page.evaluate(async () => (await (await fetch('/api/state', { cache: 'no-store' })).json()).data);
  let st = await serverData();
  check(satWork0 === 0 && st.dayRules.length === info.cnc.length * 2 && st.dayRules.every(r => r.mode === '1' && info.cnc.includes(r.machineId)), `Sa+So für alle ${info.cnc.length} CNC-Maschinen gespeichert (${st.dayRules.length})`);
  const sat = await ev(s => ({ sat: workIntervalsForDateRaw(parseLocal(s.sat), s.cnc[0]).length, sun: workIntervalsForDateRaw(parseLocal(s.sun), s.cnc[0]).length, other: workIntervalsForDateRaw(parseLocal(s.sat), data.machines.find(m => deptOfMachine(m.id) !== 'cnc').id).length }), info);
  check(sat.sat > 0 && sat.sun > 0 && sat.other === 0, `Planung: CNC arbeitet Sa/So, andere Bereiche nicht (${JSON.stringify(sat)})`);

  // Überstunden: eine Maschine, Montag, Betrieb wie geplant, +1:30
  const before = await ev(s => workIntervalsForDateRaw(parseLocal(s.mon), s.cnc[0]).at(-1).end.getTime(), info);
  await page.selectOption('#ruleMachine', info.cnc[0]); await page.fill('#ruleFrom', info.mon); await page.fill('#ruleTo', info.mon); await page.selectOption('#ruleMode', ''); await page.fill('#ruleExtra', '1:30');
  await page.click('#addRule'); await page.waitForTimeout(800);
  const after = await ev(s => workIntervalsForDateRaw(parseLocal(s.mon), s.cnc[0]).at(-1).end.getTime(), info);
  check(after - before === 90 * 60000, `Überstunden +1:30 verlängern das Tagesende (${(after - before) / 60000} min)`);
  check(await page.locator('[data-dayrule-del]').count() === info.cnc.length + 1, 'Liste fasst Sa–So je Maschine zusammen, Überstunden eigene Zeile');

  // Ungültig: mehr als 4 h Überstunden
  await page.fill('#ruleExtra', '5:00'); await page.click('#addRule'); await page.waitForTimeout(300);
  check((await page.evaluate(() => document.getElementById('errorModal').innerText)).includes('MP-CAL-015'), 'Überstunden über 4:00 → MP-CAL-015');
  await page.click('#closeError'); await page.fill('#ruleExtra', '');

  // Auftrag am Samstag einplanen und direkt freigeben – Server prüft den Kalender mit
  await ev(s => { openOrderDialog({ departmentId: 'cnc' }); document.getElementById('qMachine').value = s.cnc[0]; document.getElementById('qMachine').dispatchEvent(new Event('change')); }, info);
  await page.fill('#qFA', 'FA-SAMSTAG'); await page.fill('#qHours', '2'); await page.selectOption('#qPlanType', 'start-hard');
  await page.fill('#qAnchor', `${info.sat}T${info.start}`); await page.selectOption('#qStatus', 'released');
  await page.click('#createOrder'); await page.waitForTimeout(1200);
  const err = await page.evaluate(() => document.getElementById('errorModal').classList.contains('show') ? document.getElementById('errorModal').innerText.replace(/\s+/g, ' ') : '');
  st = await serverData();
  const o = st.workSteps.find(w => w.fa === 'FA-SAMSTAG');
  check(o && o.status === 'released' && String(o.baselinePlan?.start || '').slice(0, 10) === info.sat, `Freigabe am Samstag vom Server akzeptiert (${o?.status} ${o?.baselinePlan?.start || ''} ${err})`);

  // Überstunden je Mitarbeiter: Sa/So erscheinen im Personal-Wochenplan, Einteilung am Samstag zählt als Überstunden
  const pw = await ev(s => { const m0 = s.cnc[0]; data.employees.push({ id: 'e_sat', name: 'Samstag Test', departmentId: 'cnc', active: true, weeklyHours: 40, workingDays: [1, 2, 3, 4, 5], skills: [m0], employmentType: 'permanent', homeShift: 'auto' }); save('Testmitarbeiter');
    data.ui.week = dateKey(monday(parseLocal(s.sat))); data.ui.view = 'personnel'; data.ui.personnelTab = 'week'; switchView('personnel'); renderAll();
    return { cols: document.querySelectorAll('#personnelHead th').length, mon: dateKey(monday(parseLocal(s.sat))) }; }, info);
  check(pw.cols === 8, `Wochenplan mit Sa/So-Spalten, weil CNC am Wochenende arbeitet (${pw.cols} Spalten)`);
  await page.waitForTimeout(400);
  await page.selectOption(`[data-person-cell="e_sat"][data-date="${info.sat}"]`, `${info.cnc[0]}|single`); await page.waitForTimeout(800);
  st = await serverData();
  const satA = st.personnelAssignments.find(a => a.employeeId === 'e_sat' && a.date === info.sat);
  check(satA && satA.overtime === true, `Einteilung am Samstag gespeichert und als Überstunden markiert (${JSON.stringify(satA && [satA.start, satA.end, satA.overtime])})`);
  check(await page.locator(`td.personnelCell.overtime [data-person-cell="e_sat"][data-date="${info.sat}"]`).count() === 1, 'Überstunden-Zelle farblich markiert');
  const longDay = await ev(s => { setPersonnelCell('e_sat', s.mon, `${s.cnc0}|single`); setPersonnelTime('e_sat', s.mon, 'end', '18:00'); const a = personnelAssignment('e_sat', s.mon); return { end: a?.end, ot: a?.overtime }; }, { mon: pw.mon, cnc0: info.cnc[0] });
  check(longDay.end === '18:00' && longDay.ot === true, `längerer Werktag bleibt als Überstunden stehen (${JSON.stringify(longDay)})`);
  await page.waitForTimeout(900); st = await serverData();
  const monA = st.personnelAssignments.find(a => a.employeeId === 'e_sat' && a.date === pw.mon);
  check(monA?.end === '18:00' && monA?.overtime === true, `Server speichert den längeren Tag (${JSON.stringify(monA && [monA.end, monA.overtime])})`);
  const plain = await ev(s => { const d = parseLocal(s.mon); d.setDate(d.getDate() + 14); data.ui.week = dateKey(d); renderAll(); return document.querySelectorAll('#personnelHead th').length; }, { mon: pw.mon });
  check(plain === 6, `Woche ohne Wochenendarbeit: nur Mo–Fr (${plain} Spalten)`);

  // Löschen: Überstunden-Zeile entfernen
  await ev(() => { data.ui.view = 'settings'; data.ui.sysTab = 'machines'; switchView('settings'); renderAll(); }); await page.waitForTimeout(300);
  await page.locator(`[data-dayrule-del="${info.cnc[0]}|${info.mon}|${info.mon}"]`).click(); await page.waitForTimeout(700);
  st = await serverData();
  check(!st.dayRules.some(r => r.date === info.mon), 'Überstunden-Zeile gelöscht');

  // KW-Regel für einen ganzen Bereich gilt für alle Maschinen
  await page.selectOption('#ruleScope', 'week'); await page.selectOption('#ruleMachine', 'dep:cnc'); await page.selectOption('#ruleMode', '2'); await page.click('#addRule'); await page.waitForTimeout(800);
  st = await serverData();
  check(info.cnc.every(id => st.weekRules.some(r => r.machineId === id && r.mode === '2')), 'KW-Regel für Bereich CNC auf alle Maschinen angewandt');
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  check(errors.length === 0, 'Keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser?.close(); srv.kill(); rmSync(tmp, { recursive: true, force: true });
}
console.log(`\n${results.filter(Boolean).length}/${results.length} bestanden`);
process.exit(results.every(Boolean) ? 0 : 1);

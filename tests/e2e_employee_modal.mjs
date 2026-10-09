#!/usr/bin/env node
// #84: Mitarbeiter-Popup – Klick auf einen Mitarbeiter öffnet Verwaltung und Auswertung.
// node tests/e2e_employee_modal.mjs
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18994, BASE = `http://127.0.0.1:${PORT}/`, PASS = 'E2E-Emp-1234';
const tmp = mkdtempSync(path.join(tmpdir(), 'mp-emp-'));
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


  // Testdaten: ein Mitarbeiter mit Stammmaschine, zwei Urlaubstage im aktuellen Monat
  const setup = await ev(() => {
    const m = data.machines.find(x => x.active !== false), dep = deptOfMachine(m.id), today = new Date();
    const d1 = dateKey(new Date(today.getFullYear(), today.getMonth(), 2)), d2 = dateKey(new Date(today.getFullYear(), today.getMonth(), 3));
    data.employees = [{ id: 'e_pop', name: 'Popup Test', function: 'Einrichter', employmentType: 'permanent', departmentId: dep, weeklyHours: 40, workingDays: [1, 2, 3, 4, 5], active: true, skills: [m.id], homeMachineId: m.id, homeShift: 'auto' }];
    data.personnelAbsences = [{ employeeId: 'e_pop', date: d1, label: 'Urlaub' }, { employeeId: 'e_pop', date: d2, label: 'Urlaub' }];
    save('Testdaten'); data.ui.view = 'personnel'; data.ui.personnelTab = 'staff'; switchView('personnel'); renderAll();
    return { mid: m.id, mname: m.name, dep };
  });
  await page.waitForTimeout(500);
  check(await page.locator('#employeeList [data-emp-open="e_pop"]').count() >= 1, 'Mitarbeiterliste: Eintrag öffnet das Popup');
  check(await page.locator('#employeeList [data-emp-skill], #employeeList [data-emp-home], #employeeList [data-emp-homeshift]').count() === 0, 'Mitarbeiterliste kompakt: keine Bearbeitungsfelder mehr in der Zeile');

  await page.locator('#employeeList button.empOpen[data-emp-open="e_pop"]').first().click();
  await page.waitForSelector('#empModal.show');
  check((await page.textContent('#empModalTitle')) === 'Popup Test', 'Klick auf den Namen öffnet das Popup des Mitarbeiters');
  check(await page.locator('#empModal [data-emp-skill="e_pop"]').count() >= 1 && await page.locator('#empModal [data-emp-home="e_pop"]').count() === 1 && await page.locator('#empModal [data-emp-default-times="e_pop"]').count() === 1, 'Verwaltung im Popup: Qualifikationen, Stammmaschine, Stammzeiten');

  // Verwaltung: Schicht ändern speichert auf dem Server, Popup bleibt offen
  await page.selectOption('#empModal [data-emp-homeshift="e_pop"]', 'late');
  await page.waitForTimeout(700);
  const srvShift = await page.evaluate(async () => (await (await fetch('/api/state', { cache: 'no-store' })).json()).data.employees.find(e => e.id === 'e_pop')?.homeShift);
  check(srvShift === 'late' && await page.locator('#empModal.show').count() === 1 && await page.inputValue('#empModal [data-emp-homeshift="e_pop"]') === 'late', `Schicht im Popup geändert und gespeichert (${srvShift}), Popup bleibt offen`);
  check((await page.textContent('#employeeList')).includes('Schicht Spät'), 'Liste zeigt die neue Schicht');

  // Auswertung: Stunden, Abwesenheiten, Aufträge & Maschinen, Einsatz je Bereich
  const txt = () => page.evaluate(() => document.getElementById('empModalBody').innerText.replace(/\s+/g, ' '));
  let t = await txt();
  check(/Soll \(Vertrag\) \d/.test(t) && /Geplant [\d,]+ h/.test(t), 'Auswertung: Soll und geplante Stunden');
  check(/Urlaub \(Zeitraum\) 2 T/.test(t) && /Urlaub \d{4} 2 T/.test(t), `Auswertung: 2 Urlaubstage im Zeitraum und im Jahr`);
  check(t.includes('Einsatz je Bereich') && (t.includes('Stammbereich') || t.includes('Keine geplanten Einsätze')), 'Auswertung: Einsatz je Bereich');
  // Aufträge: gemeldete Laufzeit aus der Nachkalkulation (evalRecords) wird dem Mitarbeiter zugeordnet
  await ev(s => { const orig = evalRecords; window.__origEval = orig; evalRecords = () => [{ h: { fa: 'FA-4711', actualFinishedAt: new Date().toISOString() }, mid: s.mid, dep: s.dep, staff: { 'Popup Test': 3.5, 'Andere': 2 }, good: 120, scrap: 3 }]; renderEmpModal(); }, setup);
  t = await txt();
  check(t.includes('FA-4711') && t.includes(setup.mname) && /Ist an Aufträgen 3,5 h/.test(t), `Auswertung: Auftrag, Maschine und 3,5 h Ist`);
  await ev(() => { evalRecords = window.__origEval; });

  // Zeitraum: Jahr und ungültiger Zeitraum
  await page.click('#empModal [data-emp-range="year"]'); await page.waitForTimeout(200);
  const y = new Date().getFullYear();
  check(await page.inputValue('#empEvalFrom') === `${y}-01-01` && await page.inputValue('#empEvalTo') === `${y}-12-31`, 'Zeitraum Jahr setzt 01.01.–31.12.');
  await page.fill('#empEvalFrom', `${y}-12-31`); await page.fill('#empEvalTo', `${y}-01-01`); await page.dispatchEvent('#empEvalTo', 'change'); await page.waitForTimeout(200);
  check((await page.evaluate(() => document.getElementById('errorModal')?.innerText || '')).includes('MP-PERS-036'), 'Ungültiger Zeitraum → MP-PERS-036');
  await page.click('#closeError'); await page.waitForTimeout(150);

  // Ohne Recht "Personal": Popup nur lesen
  const ro = await ev(() => { const orig = serverReachable; serverReachable = false; renderEmpModal(); const n = [...document.querySelectorAll('#empModal .empManage select, #empModal .empManage input, #empModal .empManage button')]; const r = !can('personnel') && n.length > 0 && n.every(x => x.disabled); serverReachable = orig; renderEmpModal(); return r; });
  check(ro, 'Ohne Recht Personal: Verwaltung im Popup gesperrt');

  // Wochenplan: Name öffnet ebenfalls das Popup; Esc schließt
  await page.keyboard.press('Escape'); await page.waitForTimeout(200);
  check(await page.locator('#empModal.show').count() === 0, 'Esc schließt das Popup');
  await ev(() => { data.ui.personnelTab = 'week'; renderAll(); }); await page.waitForTimeout(300);
  await page.locator('#personnelBody [data-emp-open="e_pop"]').first().click(); await page.waitForTimeout(200);
  check(await page.locator('#empModal.show').count() === 1 && await page.locator('#personnelBody [data-emp-hours="e_pop"]').count() === 1, 'Wochenplan: Name öffnet das Popup, Arbeitszeit-Knopf bleibt');
  await page.click('#empModalClose');
  // #84 UI: "Platz" nur bei mehreren Plätzen, Gate-Schalter in der Kachel, Reiter neben der KW-Wahl
  const ui = await ev(() => ({ lanes: document.querySelectorAll('#personnelBody [data-person-lane]').length, single: data.machines.every(m => machineLanes(m.id) <= 1), gateInCard: !!document.getElementById('personnelGate')?.closest('.card'), tabsRow: !!document.querySelector('.pHeadRow .tabBar') && !!document.querySelector('.pHeadRow .weekbar') }));
  check(ui.single && ui.lanes === 0 && ui.gateInCard && ui.tabsRow, `Personal: kein „Platz“ bei Einzelplätzen, Gate in der Kachel, Reiter neben KW (${JSON.stringify(ui)})`);
  check(!(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)), 'Kein seitlicher Seiten-Scroll');
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  check(errors.length === 0, 'Keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser?.close(); srv.kill(); rmSync(tmp, { recursive: true, force: true });
}
console.log(`\n${results.filter(Boolean).length}/${results.length} bestanden`);
process.exit(results.every(Boolean) ? 0 : 1);

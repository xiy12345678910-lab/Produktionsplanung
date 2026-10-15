#!/usr/bin/env node
// V12.29.0: Stunden als h:mm, Prio je Abteilung, "FA einplanen" mit vorbelegter Ressource/Takt/Laufzeit.
// node tests/e2e_v12290.mjs
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18987, BASE = `http://127.0.0.1:${PORT}/`, PASS = 'E2E-V1229-1234';
const tmp = mkdtempSync(path.join(tmpdir(), 'mp-v12290-'));
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



  // (1) Format- und Eingabehelfer
  const f = await ev(() => ({ a: fmtH(8.75), b: fmtH(-5), c: fmtH(0), d: fmtH(1/60), p1: parseHoursIn('8:45'), p2: parseHoursIn('8,75'), p3: parseHoursIn('8.5'), p4: parseHoursIn(''), p5: Number.isNaN(parseHoursIn('8:75')), p6: Number.isNaN(parseHoursIn('abc')) }));
  check(f.a === '8:45' && f.b === '-5:00' && f.c === '0:00' && f.d === '0:01', `fmtH zeigt h:mm (${JSON.stringify(f)})`);
  check(f.p1 === 8.75 && f.p2 === 8.75 && f.p3 === 8.5 && f.p4 === 0 && f.p5 && f.p6, 'Eingabe h:mm und Dezimal (Komma/Punkt) werden gelesen, Unsinn wird abgewiesen');

  // (2) Prio je Abteilung: zwei Bereiche, Ränge zählen je Bereich ab 1; fertige Aufträge zählen nicht
  const pr = await ev(() => {
    const deps = [...new Set(data.machines.filter(m => m.active !== false).map(m => deptOfMachine(m.id)))].slice(0, 2);
    const mk = (id, dep, pos, status = 'planned') => ({ id, sequence: pos, planningType: 'MACHINE', pos, departmentId: dep, projectId: '', predecessorIds: [], fa: id.toUpperCase(), ab: '', wt: '', machineId: data.machines.find(m => deptOfMachine(m.id) === dep).id, altMachineId: '', allowAlternative: false, order: id, articleNo: '', description: '', targetQty: 100, dueDate: '', baselinePlan: null, hours: 2, goodQty: 0, scrapQty: 0, status, direction: 'forward', anchorMode: 'none', requiredStart: '', requiredFinish: '', createdAt: new Date().toISOString(), lockedStart: '', lockedSegments: [], actualStartedAt: '', runningSince: '', pausedAt: '', pauseIntervals: [], remainingHours: null, lastStatusCheckAt: '' });
    data.workSteps = [mk('a1', deps[0], 10), mk('b1', deps[1], 20), mk('a2', deps[0], 30), mk('b2', deps[1], 40)];
    save('Testdaten'); data.workSteps.push(mk('a0', deps[0], 5, 'done')); const r = prioRankMap(); data.workSteps = data.workSteps.filter(o => o.id !== 'a0'); return { deps, a1: r.get('a1'), a2: r.get('a2'), b1: r.get('b1'), b2: r.get('b2'), a0: r.get('a0') ?? null };
  });
  check(pr.deps.length === 2 && pr.a1 === 1 && pr.a2 === 2 && pr.b1 === 1 && pr.b2 === 2 && pr.a0 === null, `Prio je Abteilung, fertige zählen nicht (${JSON.stringify(pr)})`);
  await ev(() => { data.ui.departmentScope = ''; switchView('orders'); renderAll(); }); await page.waitForTimeout(400);
  const pills = await page.evaluate(() => [...document.querySelectorAll('#ordersBody tr[data-id]')].map(tr => tr.dataset.id + '=' + (tr.querySelector('.qtyPill')?.textContent || '')));
  check(pills.includes('b1=#1') && pills.includes('a2=#2') && pills.includes('b2=#2'), `Auftragsliste zeigt Prio je Abteilung (${pills.join(', ')})`);
  const hv = await page.locator('#ordersBody tr[data-id="a1"] input[data-f="hours"]').inputValue();
  check(hv === '2:00', `Auftragsliste: Sollstunden als h:mm (${hv})`);
  await page.locator('#ordersBody tr[data-id="a1"] input[data-f="hours"]').fill('2:30'); await page.locator('#ordersBody tr[data-id="a1"] input[data-f="hours"]').dispatchEvent('change'); await page.waitForTimeout(300);
  await page.click('#confirmMove'); await page.waitForTimeout(300);
  const h2 = await ev(() => data.workSteps.find(o => o.id === 'a1').hours);
  check(h2 === 2.5, `Eingabe 2:30 in der Auftragsliste = 2,5 Stunden (${h2})`);

  // (3) FA einplanen: Ressource, Takt und Laufzeit vorbelegt
  const pl = await ev(() => {
    const dep = deptOfMachine(data.machines.find(m => m.active !== false).id), ms = data.machines.filter(m => deptOfMachine(m.id) === dep && m.active !== false);
    ms.forEach(m => { delete m.takte }); ms[ms.length - 1].takte = [{ id: 't_std', name: 'Standard', sec: 36, parts: 1 }];
    const o = { ...data.workSteps.find(x => x.id === 'a2'), id: 'pl1', fa: 'FA-PL1', departmentId: dep, machineId: '', targetQty: 500, hours: 0, handoffUnassigned: true, pos: 50, sequence: 50 };
    data.workSteps.push(o); save('Testdaten'); renderAll(); openPlanPopup('pl1');
    return { target: ms[ms.length - 1].id, res: document.getElementById('planResource').value, takt: document.getElementById('planTakt').value, hours: document.getElementById('planHours').value, ok: !document.getElementById('planOk').disabled };
  });
  // 500 Stk × 36 s = 5 h
  check(pl.res === pl.target && pl.takt === 't_std' && pl.hours === '5:00', `Einplanen: Ressource mit Takt, Takt und Laufzeit vorbelegt (${JSON.stringify(pl)})`);
  check(pl.ok, 'Einplanen: „Einplanung übernehmen“ sofort möglich');
  await page.click('#planCancel'); await page.waitForTimeout(150);

  // (4) Umrüsten in der Belegung, Trocknung nur mit Standard-Trocknung, Termin direkt im Popup
  const pc = await ev(() => {
    const o = data.workSteps.find(x => x.id === 'pl1'), dep = departmentById(o.departmentId), m = machine('m3'); m.setupMinutes = 30; delete dep.dryingHours; renderAll(); openPlanPopup('pl1');
    const r = { calc: document.getElementById('planCalc').textContent, dryHidden: document.getElementById('planDryField').style.display === 'none' };
    document.getElementById('planCancel').click(); dep.dryingHours = 2; openPlanPopup('pl1'); r.dryShown = document.getElementById('planDryField').style.display === ''; delete dep.dryingHours; return r;
  });
  check(/Laufzeit 5:00 h/.test(pc.calc) && /Umrüsten 0:30 h/.test(pc.calc) && /Belegung 5:30 h/.test(pc.calc), `Laufzeit aus Takt × Menge + Umrüsten = Belegung (${pc.calc})`);
  check(pc.dryHidden && pc.dryShown, 'Trocknung nur bei Bereich mit Standard-Trocknung (z. B. Siebdruck)');
  const start = await ev(() => { const d = new Date(); d.setDate(d.getDate() + 7); while ([0, 6].includes(d.getDay())) d.setDate(d.getDate() + 1); return dateKey(d) + 'T10:00'; });
  await page.selectOption('#planType', 'start-hard'); await page.fill('#planAnchor', start); await page.waitForTimeout(300);
  const prevText = await page.locator('#planPreview').innerText();
  check(await page.locator('#planOk').isEnabled() && /Start/.test(prevText), `Termin im Popup: Start fix ${start} in der Vorschau (${prevText.replace(/\s+/g, ' ')})`);
  await page.click('#planOk'); await page.locator('#moveModal.show').waitFor(); await page.click('#confirmMove'); await page.waitForTimeout(400);
  const saved = await ev(() => { const o = data.workSteps.find(x => x.id === 'pl1'); return { anchorMode: o.anchorMode, requiredStart: o.requiredStart, machineId: o.machineId, hours: o.hours, unassigned: o.handoffUnassigned }; });
  check(saved.anchorMode === 'hard' && saved.requiredStart === start && saved.machineId === 'm3' && saved.hours === 5 && saved.unassigned === false, `Übernommen: Ressource, Laufzeit und fixer Start (${JSON.stringify(saved)})`);

  // (5) Tiefziehen: mehrere FA per Mehrfachauswahl auf ein neues Format
  const fm = await ev(() => {
    const dep = (data.departments || []).find(d => d.formats === true && data.machines.some(m => deptOfMachine(m.id) === d.id)); if (!dep) return { skip: true };
    const mid = data.machines.find(m => deptOfMachine(m.id) === dep.id).id, base = data.workSteps.find(x => x.id === 'a2');
    const mk = (id, pos) => ({ ...base, id, fa: id.toUpperCase(), order: id, departmentId: dep.id, machineId: '', handoffUnassigned: true, pos, sequence: pos, targetQty: 300, hours: 0 });
    data.formats = []; data.workSteps.push(mk('tz1', 60), mk('tz2', 70), mk('tz3', 80)); save('Testdaten'); renderAll(); openPlanPopup('tz1');
    return { dep: dep.id, shown: document.getElementById('planFmtBox').style.display === '', rows: document.querySelectorAll('[data-plan-fmt]').length };
  });
  check(!fm.skip && fm.shown && fm.rows === 3, `Format-Bereich: Mehrfachauswahl mit allen offenen FA des Bereichs (${JSON.stringify(fm)})`);
  await page.check('[data-plan-fmt="tz2"]'); await page.check('[data-plan-fmt="tz3"]');
  check(/3 FA/.test(await page.locator('#planFmtGo').innerText()), 'Knopf zählt die ausgewählten FA');
  await page.click('#planFmtGo'); await page.waitForTimeout(600);
  const fr = await ev(() => { const f = (data.formats || [])[0]; return { n: (data.formats || []).length, steps: (f?.tools || []).map(t => t.stepId).sort(), open: document.getElementById('formatsModal').classList.contains('show') }; });
  check(fr.n === 1 && fr.steps.join() === 'tz1,tz2,tz3' && fr.open, `Ein Format mit allen drei FA, Formateditor offen (${JSON.stringify(fr)})`);
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  check(errors.length === 0, 'Keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser?.close(); srv.kill(); rmSync(tmp, { recursive: true, force: true });
}
console.log(`\n${results.filter(Boolean).length}/${results.length} bestanden`);
process.exit(results.every(Boolean) ? 0 : 1);

#!/usr/bin/env node
// V12.27.0: Takt mit Einheit (Takte/Stunde, Sekunden, Minuten) und Takt je Produkt im Einplanen-Popup.
// node tests/e2e_takt.mjs
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18996, BASE = `http://127.0.0.1:${PORT}/`, PASS = 'E2E-Takt-1234';
const tmp = mkdtempSync(path.join(tmpdir(), 'mp-takt-'));
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

  // (a) Einheit: Eingabe -> Sekunden
  const conv = await ev(() => ({ h: taktToSec(1200, 'perHour'), m: taktToSec('0,5', 'min'), s: taktToSec(3, 's'), lbl: taktLabel({ sec: 3, unit: 'perHour' }), val: taktValue({ sec: 30, unit: 'min' }) }));
  check(conv.h === 3 && conv.m === 30 && conv.s === 3 && conv.val === 0.5 && conv.lbl.startsWith('1200 Takte/h'), `Einheiten-Umrechnung (${JSON.stringify(conv)})`);
  // UI: "Takt anlegen" mit Einheit
  const mid = await ev(() => { const m = data.machines.find(x => x.active !== false); m.takte = []; save('t'); return m.id; });
  await ev(() => { switchView('settings'); renderAll(); });
  await page.waitForTimeout(300);
  await page.evaluate(id => { const d = document.querySelector(`[data-fmt-mach="${id}"]`); if (d) d.open = true; }, mid);
  const addBtn = page.locator(`[data-fmt-add="${mid}"]`);
  check(await addBtn.count() === 1, 'Takte-Abschnitt auch für Nicht-Format-Maschine sichtbar');
  await addBtn.evaluate(b => b.click());
  await page.waitForSelector('#askModal.show');
  await page.fill('#askInput', '1200'); await page.selectOption('#askSelect', 'perHour'); await page.click('#askOk');
  await page.waitForFunction(() => document.getElementById('askTitle').textContent === 'Takt anlegen' && document.getElementById('askSelectField').style.display === 'none');
  await page.fill('#askInput', 'Blockbodenbeutel 300 g'); await page.click('#askOk');
  await page.waitForTimeout(400);
  const t1 = await ev(id => machine(id).takte[0], mid);
  check(t1 && t1.sec === 3 && t1.unit === 'perHour', `1200 Takte/Stunde -> 3 s (${JSON.stringify(t1)})`);
  const shown = await page.evaluate(t => document.querySelector(`[data-fmt-t="${t}"][data-k="sec"]`).value, t1.id);
  check(shown === '1200', 'Takt-Zeile zeigt 1200 in der gewählten Einheit');
  const setEl = (tid, k, v) => page.evaluate(([t, k, v]) => { const el = document.querySelector(`[data-fmt-t="${t}"][data-k="${k}"]`); el.value = v; el.dispatchEvent(new Event('change', { bubbles: true })); }, [tid, k, v]);
  const openD = () => page.evaluate(id => { const d = document.querySelector(`[data-fmt-mach="${id}"]`); if (d) d.open = true; }, mid);
  await openD();
  await ev(() => { data.ui.sysTab = 'machines'; renderAll(); }); await openD();
  const taktW = await page.evaluate(t => Object.fromEntries(['unit', 'parts'].map(k => [k, document.querySelector(`[data-fmt-t="${t}"][data-k="${k}"]`).getBoundingClientRect().width])), t1.id);
  check(taktW.unit >= 110 && taktW.parts >= 50, `Takt-Zeile: Einheit lesbar und "Stück je Takt" nicht zusammengedrückt (${JSON.stringify(taktW)})`);
  await setEl(t1.id, 'unit', 'min'); await page.waitForTimeout(300);
  await openD();
  await setEl(t1.id, 'sec', '0.5'); await page.waitForTimeout(300);
  const t2 = await ev(id => ({ ...machine(id).takte[0] }), mid);
  check(t2.sec === 30 && t2.unit === 'min', `0,5 min -> 30 s (${JSON.stringify(t2)})`);
  // (b) Planungs-Popup mit Takt (3 s, 2 Stück je Takt, Menge 2400 -> 1 h)
  await ev(id => { const m = machine(id); m.takte = [{ id: 'tk_t', name: 'Doy 500 g', sec: 3, parts: 2 }]; const o = { id: 'ws_tk', planningType: 'MACHINE', fa: 'FA-TAKT', order: 'FA-TAKT', departmentId: m.departmentId, machineId: '', hours: 0, pos: 1, predecessorIds: [], direction: 'forward', anchorMode: 'none', requiredStart: '', requiredFinish: '', altMachineId: '', allowAlternative: false, lockedSegments: [], pauseIntervals: [], baselinePlan: null, goodQty: 0, scrapQty: 0 }; data.workSteps = [o]; window.__po = o.id; o.targetQty = 2400; o.status = 'planned'; delete o.taktId; sessionUser.role = 'admin'; openPlanPopup(o.id); }, mid);
  await page.waitForSelector('#planModal.show');
  const dept = await ev(() => data.workSteps.find(x => x.id === window.__po).departmentId);
  const rid = await ev(d => data.machines.find(x => x.departmentId === d && x.takte && x.takte.length).id, dept);
  await page.selectOption('#planResource', rid);
  check(await page.locator('#planTaktField').isVisible(), 'Takt-Auswahl sichtbar bei Ressource mit Takten');
  await page.selectOption('#planTakt', 'tk_t');
  const hv = await page.inputValue('#planHours');
  check(Number(hv) === 1, `Laufzeit aus Takt x Menge = 1 h (${hv})`);
  await page.fill('#planHours', '2'); check(await page.inputValue('#planHours') === '2', 'Laufzeit bleibt editierbar');
  await page.click('#planCancel');
  // Menge 0 -> Hinweis
  await ev(() => { data.workSteps.find(x => x.id === window.__po).targetQty = 0; openPlanPopup(window.__po); });
  await page.selectOption('#planResource', rid); await page.selectOption('#planTakt', 'tk_t');
  check((await page.textContent('#planTaktHint')).includes('Menge fehlt'), 'Hinweis "Menge fehlt" bei Menge 0');
  await page.click('#planCancel');
  // (b2) V12.27.0: Bereich ohne Mengenmeldung blendet Mengenfelder der Fertigmeldung aus
  await ev(() => { const o = data.workSteps[0]; window.__nq = o.id; o.status = 'running'; o.actualStartedAt = new Date(Date.now() - 36e5).toISOString(); departmentById(o.departmentId).noQuantity = true; openFinish(o.id); });
  check(await ev(() => deptNoQty(data.workSteps[0].departmentId)), 'deptNoQty erkennt Bereich ohne Mengenmeldung');
  check(!(await page.locator('#finishGood').isVisible()) && !(await page.locator('#finishScrap').isVisible()), 'Fertigmeldung ohne Mengenfelder bei noQuantity');
  await ev(() => { document.getElementById('finishModal').classList.remove('show'); const o = data.workSteps[0]; delete departmentById(o.departmentId).noQuantity; openFinish(o.id); });
  check(await page.locator('#finishGood').isVisible(), 'Gegenprobe: Mengenfelder sichtbar ohne noQuantity');
  await ev(() => { document.getElementById('finishModal').classList.remove('show'); });
  // (b3) FA-Anlage in einem Bereich ohne Mengenmeldung (z. B. Formbau): keine Menge, kein Takt-Rechner, Titel ist Pflicht
  const qState = () => ev(() => ({ qty: document.getElementById('qQty').closest('.field').style.display, calc: document.getElementById('qCalcBox').style.display, label: document.querySelector('#qDescField label').textContent, ph: document.getElementById('qDesc').placeholder }));
  const nqDep = await ev(() => { const d = departmentById(data.workSteps[0].departmentId); d.noQuantity = true; openOrderDialog({ departmentId: d.id }); return d.id; });
  let q = await qState();
  check(q.qty === 'none' && q.calc === 'none' && q.label.endsWith('*') && q.ph !== 'optional', `FA-Dialog ohne Mengenmeldung: Menge und Takt-Rechner aus, Titel Pflicht (${JSON.stringify(q)})`);
  const rowAligned = await page.evaluate(() => { const a = document.getElementById('qDue'), b = document.getElementById('qAltMachine'); return b.closest('.field').style.display === 'none' || Math.abs(a.getBoundingClientRect().height - b.getBoundingClientRect().height) < 4; });
  check(rowAligned, 'FA-Dialog: Alternative Maschine so hoch wie AV-Termin (nicht gestreckt)');
  await ev(id => { delete departmentById(id).noQuantity; document.getElementById('orderModal').classList.remove('show'); openOrderDialog({ departmentId: id }); }, nqDep);
  q = await qState();
  check(q.qty === '' && q.label === 'Artikel / Beschreibung' && q.ph === 'optional', `Gegenprobe: normaler Bereich zeigt Menge, Titel optional (${JSON.stringify(q)})`);
  await ev(() => { document.getElementById('orderModal').classList.remove('show'); });
  // (c) Server lehnt ungueltige Einheit / Stueck je Takt ab
  const put = (mut) => page.evaluate(async (m) => { const s = await (await fetch('/api/state')).json(), h = await (await fetch('/api/health')).json(); const mac = s.data.machines[0]; mac.takte = [{ id: 'tk_x', name: 'X', sec: 3, ...m }]; const r = await fetch('/api/state', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': h.version }, body: JSON.stringify({ revision: s.revision, data: s.data }) }); return [r.status, (await r.json()).errorCode]; }, mut);
  const b1 = await put({ unit: 'xyz' }), b2 = await put({ parts: 0 }), b3 = await put({ parts: 2.5 });
  check(b1[0] === 400 && b1[1] === 'MP-MACH-014', `Server lehnt Einheit 'xyz' ab (${b1})`);
  check(b2[0] === 400 && b3[0] === 400, `Server lehnt Stück je Takt 0 / 2,5 ab (${b2} ${b3})`);
  const ok1 = await put({ unit: 'perHour', parts: 2 });
  check(ok1[0] === 200, `Server akzeptiert perHour + parts 2 (${ok1})`);
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  check(errors.length === 0, 'Keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser?.close(); srv.kill(); rmSync(tmp, { recursive: true, force: true });
}
console.log(`\n${results.filter(Boolean).length}/${results.length} bestanden`);
process.exit(results.every(Boolean) ? 0 : 1);

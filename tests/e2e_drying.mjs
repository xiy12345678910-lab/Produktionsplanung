#!/usr/bin/env node
// V12.24.0: Trocknungs-/Wartezeit nach einem Arbeitsgang (z. B. Siebdruck).
// Maschine ist während der Trocknung frei; nur der Nachfolger desselben FA wartet.
// node tests/e2e_drying.mjs
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18989, BASE = `http://127.0.0.1:${PORT}/`, PASS = 'E2E-Trocknung-1234';
const tmp = mkdtempSync(path.join(tmpdir(), 'mp-drying-'));
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

  // Siebdruck-FA (Vorgänger) ist gerade fertig gemeldet; Nachfolger auf anderer Maschine, dazu ein anderer FA auf der Siebdruck-Maschine.
  const run = drying => ev(h => {
    const ms = data.machines.filter(m => m.active !== false && m.kind !== 'line'), m0 = ms[0], m1 = ms.find(m => m.id !== m0.id);
    const now = new Date(), fin = new Date(now.getTime() - 60000), base = { planningType: 'MACHINE', status: 'planned', direction: 'forward', anchorMode: 'none', requiredStart: '', requiredFinish: '', altMachineId: '', allowAlternative: false, lockedSegments: [], pauseIntervals: [], baselinePlan: null, targetQty: 10, goodQty: 0, scrapQty: 0 };
    data.history = [{ id: 'h_dry', originalOrderId: 'ws_pred', recordType: 'done', status: 'done', machineId: m0.id, departmentId: m0.departmentId, fa: 'FA-DRY', order: 'FA-DRY', hours: 1, actualStartedAt: new Date(fin.getTime() - 3600000).toISOString(), actualFinishedAt: fin.toISOString(), ...(h ? { dryingHours: h } : {}) }];
    data.workSteps = [
      { ...base, id: 'ws_succ', fa: 'FA-DRY', order: 'FA-DRY', machineId: m1.id, departmentId: m1.departmentId, hours: 1, pos: 10, predecessorIds: ['ws_pred'] },
      { ...base, id: 'ws_other', fa: 'FA-OTHER', order: 'FA-OTHER', machineId: m0.id, departmentId: m0.departmentId, hours: 1, pos: 20, predecessorIds: [] }];
    const r = calcSchedule(), iso = d => d ? new Date(d).toISOString() : null;
    return { fin: fin.toISOString(), succ: iso(r.ws_succ?.start), other: iso(r.ws_other?.start), readyAt: iso(r.ws_succ?.readyAt), warn: r.ws_succ?.warning || '', conflict: r.ws_succ?.conflict || '' };
  }, drying);
  const none = await run(0), dry = await run(5);
  const finMs = Date.parse(dry.fin);
  check(none.succ && dry.succ, `Nachfolger ist in beiden Fällen planbar (${none.conflict}${dry.conflict})`);
  check(Date.parse(dry.succ) >= finMs + 5 * 3600000, `Mit 5 h Trocknung startet der Nachfolger frühestens 5 h nach Fertigmeldung (${dry.succ})`);
  check(Date.parse(dry.succ) >= Date.parse(none.succ), 'Trocknung verschiebt den Nachfolger nie nach vorne');
  check(Math.abs(Date.parse(dry.other) - Date.parse(none.other)) < 60000, `Siebdruck-Maschine ist während der Trocknung frei: anderer FA startet wie ohne Trocknung (${dry.other}, Trocknung bis ${dry.readyAt})`);
  check(dry.readyAt && dry.warn.includes('Trocknung'), 'Planung meldet „Wartet auf Trocknung des Vorgängers bis …“');

  // Bereichsstandard greift, wenn der Arbeitsgang selbst keine Trocknung trägt; Arbeitsgang-Wert hat Vorrang.
  const viaDept = await ev(() => { const h = data.history[0], d = departmentById(h.departmentId); delete h.dryingHours; d.dryingHours = 3; const a = dryingHoursOf(h); h.dryingHours = 0.5; const b = dryingHoursOf(h); delete d.dryingHours; return [a, b]; });
  check(viaDept[0] === 3 && viaDept[1] === 0.5, `Standard je Bereich (3 h), Wert am Arbeitsgang hat Vorrang (0,5 h) ${JSON.stringify(viaDept)}`);

  // hh:mm-Format und Beschriftung der Bereiche (Entwicklung ohne Planungslogik, Bereich ohne Ressource als Warnung).
  const fmt = await ev(() => [fmtHHMM(2.5), fmtHHMM(0.25), fmtHHMM(0), parseHHMM('2:30'), parseHHMM('1'), parseHHMM('2:75'), parseHHMM('721'), parseHHMM('')]);
  check(JSON.stringify(fmt) === JSON.stringify(['2:30', '0:15', '', 2.5, 1, null, null, 0]), `hh:mm: Anzeige und Eingabe (${JSON.stringify(fmt)})`);
  const labels = await ev(() => { const d = { id: 'dx_empty', name: 'Leer', planningType: 'MACHINE' }; data.departments.push(d); const a = deptLogicLabel(d); data.departments.pop(); return a; });
  check(labels.includes('keine Maschine'), `Produktionsbereich ohne Ressource wird als nicht planbar markiert („${labels}“)`);
  await page.click('#navSystem'); await page.locator('[data-systab="functions"]').click(); await page.waitForTimeout(300);
  const sysText = await page.locator('#adminDepartmentControls').innerText();
  check(/^Bereiche/m.test(sysText) && !sysText.includes('Produktionsbereiche'), 'System: Überschrift „Bereiche“');
  check(await page.locator('[data-dep-dry]').first().getAttribute('placeholder') === '0:00', 'System: Standard-Trocknung als hh:mm');

  // Reinigung nach FA: Maschine belegt, Ergebnis des FA und Nachfolger unverändert, Fertigmeldung blockiert die Maschine.
  const clean = await ev(() => {
    const ms0 = data.machines.filter(m => m.active !== false && m.kind !== 'line'), m0 = ms0[0], m1 = ms0[1], iso = d => d ? new Date(d).toISOString() : null;
    const base = { planningType: 'MACHINE', status: 'planned', direction: 'forward', anchorMode: 'none', requiredStart: '', requiredFinish: '', altMachineId: '', allowAlternative: false, lockedSegments: [], pauseIntervals: [], baselinePlan: null, targetQty: 10, goodQty: 0, scrapQty: 0 };
    const saved = { history: data.history, ws: data.workSteps, cu: m0.cleanupMinutes };
    const mk = id => ({ ...base, id, fa: 'FA-' + id, order: 'FA-' + id, machineId: m0.id, departmentId: m0.departmentId, hours: 1, predecessorIds: [] });
    const run = cu => {
      if (cu) m0.cleanupMinutes = cu; else delete m0.cleanupMinutes;
      data.history = [];
      data.workSteps = [{ ...mk('c1'), pos: 10 }, { ...mk('c2'), pos: 20 }];
      const r = calcSchedule();
      return { end1: iso(r.c1.end), s1: iso(r.c1.start), s2: iso(r.c2.start), late1: !!r.c1.late, warn: r.c1.warning || '', cleanup: r.c1.cleanup ? iso(r.c1.cleanup.end) : null, minEnd: iso(buildSegments(new Date(r.c1.end), cu / 60, m0.id).end) };
    };
    const none = run(0), with2 = run(120);
    const succ = cu => {
      if (cu) m0.cleanupMinutes = cu; else delete m0.cleanupMinutes;
      const f = new Date(Date.now() - 60000);
      data.history = [{ id: 'h_cs', originalOrderId: 'ws_ps', recordType: 'done', status: 'done', machineId: m0.id, departmentId: m0.departmentId, fa: 'FA-S', order: 'FA-S', hours: 1, actualStartedAt: new Date(f.getTime() - 3600000).toISOString(), actualFinishedAt: f.toISOString() }];
      data.workSteps = [{ ...mk('c3'), machineId: m1.id, departmentId: m1.departmentId, pos: 30, predecessorIds: ['ws_ps'] }];
      return iso(calcSchedule().c3?.start);
    };
    none.s3 = succ(0); with2.s3 = succ(120);
    const fin = new Date(Date.now() - 60000);
    m0.cleanupMinutes = 120; data.workSteps = [];
    data.history = [{ id: 'h_cl', originalOrderId: 'ws_cl', recordType: 'done', status: 'done', machineId: m0.id, departmentId: m0.departmentId, fa: 'FA-CL', order: 'FA-CL', hours: 1, actualStartedAt: new Date(fin.getTime() - 3600000).toISOString(), actualFinishedAt: fin.toISOString() }];
    const floor = machineFloor(m0).getTime(), expect = buildSegments(fin, 2, m0.id).end.getTime();
    data.history = saved.history; data.workSteps = saved.ws; if (saved.cu) m0.cleanupMinutes = saved.cu; else delete m0.cleanupMinutes;
    return { none, with2, floor, expect, fin: fin.getTime() };
  });
  const near = (a, b) => Math.abs(Date.parse(a) - Date.parse(b)) < 5000;
  check(near(clean.none.end1, clean.with2.end1) && near(clean.none.s1, clean.with2.s1) && clean.none.late1 === clean.with2.late1, 'Reinigung: Start/Ende/Verspätung des FA unverändert');
  check(Date.parse(clean.with2.s2) >= Date.parse(clean.with2.minEnd) && Date.parse(clean.with2.s2) >= Date.parse(clean.with2.end1) + 0, `Reinigung: 2. FA startet erst nach 2 h Reinigung (${clean.with2.end1} -> ${clean.with2.s2})`);
  check(Date.parse(clean.with2.s2) > Date.parse(clean.none.s2), 'Reinigung: 2. FA startet später als ohne Reinigung');
  check(clean.with2.s3 && near(clean.with2.s3, clean.none.s3), `Reinigung: Nachfolger desselben FA wartet nicht auf die Reinigung (${clean.with2.s3} vs. ${clean.none.s3})`);
  check(near(clean.with2.cleanup, clean.with2.minEnd) && clean.with2.warn.includes('Reinigung') && !clean.none.cleanup, 'Reinigung: Anzeige/Hinweis nur mit Reinigungszeit');
  check(clean.floor >= clean.expect && clean.floor >= clean.fin + 7200000 * 0 + 1, `Reinigung: Fertigmeldung blockiert die Maschine inkl. Reinigung (machineFloor ${new Date(clean.floor).toISOString()})`);
  const invalid = await ev(() => { const m = data.machines[0]; const el = { value: '25:00' }; return [Number.isFinite(parseHHMM('25')) && parseHHMM('25') > 24, parseHHMM('2:00')]; });
  check(invalid[0] && invalid[1] === 2, 'Reinigung: hh:mm-Eingabe (25:00 ungültig, 2:00 = 2 h)');

  // Server: Trocknungszeit wird geprüft; Bereichsleitung darf sie am AV-FA nicht ändern (Feldliste der AV).
  const bad = await page.evaluate(async () => { const s = await (await fetch('/api/state')).json(), h = await (await fetch('/api/health')).json(); s.data.departments[0].dryingHours = 1000; const r = await fetch('/api/state', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': h.version }, body: JSON.stringify({ revision: s.revision, data: s.data }) }); return [r.status, (await r.json()).errorCode]; });
  check(bad[0] === 400 && bad[1] === 'MP-DEPT-007', `Server lehnt Trocknungszeit > 720 h ab (${bad})`);
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  check(errors.length === 0, 'Keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser?.close(); srv.kill(); rmSync(tmp, { recursive: true, force: true });
}
console.log(`\n${results.filter(Boolean).length}/${results.length} bestanden`);
process.exit(results.every(Boolean) ? 0 : 1);

#!/usr/bin/env node
// #47 Block F: Chargen je Maschine eintragbar und in der Planung wirksam.
// Maschineneinstellung "Charge" -> FA mit Menge -> berechnete Laufzeit (Chip), Plan/Ende, Einplanen-Popup,
// Freigabe mit eingefrorener Charge (Server), Reload, Parität Client/Server, 390 px ohne Seiten-Scroll.
// node tests/e2e_batch.mjs
import { spawn, spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18998, BASE = `http://127.0.0.1:${PORT}/`, PASS = 'E2E-Charge-1234';
const tmp = mkdtempSync(path.join(tmpdir(), 'mp-batch-'));
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
  const login = async () => {
    await page.goto(BASE); await page.fill('#loginUser', 'admin'); await page.fill('#loginPassword', PASS); await page.click('#loginBtn');
    await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show')); await page.waitForTimeout(400);
  };
  const serverState = () => page.evaluate(async () => (await (await fetch('/api/state')).json()).data);
  const waitServer = async (pred, label) => { for (let i = 0; i < 40; i++) { const s = await serverState(); if (pred(s)) return s; await page.waitForTimeout(250); } throw new Error('Server-Stand nicht erreicht: ' + label); };
  await login();

  // Maschine ohne Takte-Bereich mit AV-Stunden, keine Linie, keine Dauer nach Besetzung; Umrüsten 30 min.
  const mid = await ev(() => { const m = data.machines.find(x => x.active !== false && x.kind !== 'line' && x.effortScaling !== true && !isConfectionDepartment(x.departmentId)); m.setupMinutes = 30; m.takte = [{ id: 'tk_b', name: 'Takt', sec: 3 }]; save('t'); return m.id; });
  check(!!mid, `Testmaschine gefunden (${mid})`);
  check(await ev(id => !machine(id).batch && batchPlanOf({ machineId: id, targetQty: 300, hours: 1, status: 'planned' }) === null, mid), 'Standard: Charge aus, keine Verhaltensänderung');

  // (1) Maschineneinstellungen: Charge einschalten und Werte über die Oberfläche eintragen
  await ev(() => { data.ui.sysTab = 'machines'; switchView('settings'); renderAll(); }); await page.waitForTimeout(300);
  const det = `[data-batch-mach="${mid}"]`;
  check(await page.locator(det).count() === 1, 'Charge-Abschnitt je Maschine sichtbar');
  check((await page.locator(`${det} summary`).innerText()).includes('aus'), 'Zusammenfassung zeigt „Charge: aus“');
  await page.evaluate(d => { document.querySelector(d).open = true; }, det);
  await page.locator(`[data-mb="${mid}"][data-bf="on"]`).check(); await page.waitForTimeout(300);
  check(await ev(id => !!machine(id).batch, mid), 'Checkbox schaltet Charge ein');
  const setF = async (f, v) => { await page.evaluate(([id, f, v]) => { const d = document.querySelector(`[data-batch-mach="${id}"]`); d.open = true; const el = document.querySelector(`[data-mb="${id}"][data-bf="${f}"]`); el.value = v; el.dispatchEvent(new Event('change', { bubbles: true })); }, [mid, f, v]); await page.waitForTimeout(200); };
  await setF('size', '100'); await setF('minutes', '2:00'); await setF('parallel', '1'); await setF('cleanMinutes', '0:15');
  const cfg = await ev(id => ({ ...machine(id).batch }), mid);
  check(cfg.size === 100 && cfg.minutes === 120 && cfg.parallel === 1 && cfg.cleanMinutes === 15 && cfg.setup === 'order' && cfg.partial === 'full', `Werte gespeichert ${JSON.stringify(cfg)}`);
  const summary = await page.locator(`${det} summary`).innerText();
  check(summary.includes('100 Stk') && summary.includes('2:00') && summary.includes('0:15'), `Zusammenfassung „${summary}“`);
  // Ungültige Eingaben werden abgewiesen, Wert bleibt
  await setF('size', '0'); await setF('minutes', 'abc'); await setF('parallel', '0');
  check(JSON.stringify(await ev(id => machine(id).batch, mid)) === JSON.stringify(cfg), 'Ungültige Eingaben (0 Stk, „abc“, 0 gleichzeitig) ändern nichts');
  await page.evaluate(() => document.querySelectorAll('.modal.show [data-close], #errorModal.show button').forEach(b => b.click())).catch(() => {});
  const s1 = await waitServer(s => s.machines.find(m => m.id === mid)?.batch?.cleanMinutes === 15, 'Charge gespeichert');
  check(JSON.stringify(s1.machines.find(m => m.id === mid).batch) === JSON.stringify(cfg), 'Server hat die Charge gespeichert');

  // (2) FA mit Menge 300: 3 Chargen × 2:00 + Reinigung 2 × 0:15 = 6,5 h (+ 0:30 Umrüsten je Auftrag)
  await ev(id => { const m = machine(id); data.workSteps = data.workSteps.filter(o => o.id !== 'ws_bat'); data.workSteps.push({ id: 'ws_bat', sequence: 10, planningType: 'MACHINE', fa: 'FA-CHARGE', order: 'FA-CHARGE', departmentId: m.departmentId, machineId: id, hours: 1, targetQty: 300, pos: 1, predecessorIds: [], direction: 'forward', anchorMode: 'none', requiredStart: '', requiredFinish: '', altMachineId: '', allowAlternative: false, lockedSegments: [], pauseIntervals: [], baselinePlan: null, goodQty: 0, scrapQty: 0, status: 'planned', projectId: '', ab: '', wt: '', description: 'Röstkaffee' }); save('FA'); }, mid);
  await waitServer(s => s.workSteps.some(o => o.id === 'ws_bat'), 'FA gespeichert');
  const plan = await ev(() => { const r = calcSchedule().ws_bat, o = data.workSteps.find(x => x.id === 'ws_bat'), bp = batchPlanOf(o); const segH = (r?.segments || []).reduce((a, s) => a + (s.end - s.start) / 36e5, 0); const exp = r?.start ? buildSegments(new Date(r.start), bp.hours + 0.5, o.machineId).end : null; return { hours: bp?.hours, text: batchText(bp), segH, start: r?.start?.toISOString?.(), end: r?.end?.toISOString?.(), exp: exp?.toISOString?.(), conflict: r?.conflict || '' }; });
  check(plan.hours === 6.5, `Planstunden aus Charge = 6,5 h (${plan.hours})`);
  check(plan.text === '3 Chargen × 2:00 + Reinigung 2 × 0:15', `Erklärung „${plan.text}“`);
  check(Math.abs(plan.segH - 7) < 1e-6, `Planbalken = 6,5 h Charge + 0,5 h Umrüsten = 7 h (${plan.segH})`);
  check(plan.end && plan.end === plan.exp && !plan.conflict, `Endzeit = Start + 7 h Arbeitszeit (${plan.start} → ${plan.end})`);
  await ev(() => { switchView('orders'); renderAll(); }); await page.waitForTimeout(300);
  const row = page.locator('#ordersBody tr[data-id="ws_bat"]');
  const hIn = row.locator('input[data-f="hours"]');
  check(await hIn.isDisabled() && Number(await hIn.inputValue()) === 6.5, `FA-Liste: Sollstunden 6,5 berechnet und gesperrt (${await hIn.inputValue()})`);
  const chip = (await row.locator('[data-batch-chip]').innerText()).trim();
  check(chip === '3 Chargen × 2:00 + Reinigung 2 × 0:15 = 6:30 h', `FA-Liste: Erklärungs-Chip „${chip}“`);
  await ev(() => { switchView('overview'); renderAll(); }); await page.waitForTimeout(300);
  const boardTxt = await page.locator('[data-board-order="ws_bat"]').first().innerText().catch(() => '');
  check(boardTxt.includes('6,5 h · 3 Ch.'), `Plantafel zeigt Charge-Stunden („${boardTxt.replace(/\s+/g, ' ').slice(0, 120)}“)`);

  // (3) Vorrang und Varianten (Client = Server-Formel)
  const v = await ev(id => {
    const o = data.workSteps.find(x => x.id === 'ws_bat'), m = machine(id), keep = { ...m.batch }, out = {};
    o.hours = 99; out.manualIgnored = effHours(o); o.hours = 1;
    o.targetQty = 0; out.noQty = effHours(o); o.targetQty = 300; o.hours = 1;
    m.batch = { ...keep, parallel: 2 }; out.par = batchPlanOf(o).hours;            // 3 Chargen, 2 gleichzeitig -> 2 Durchgänge: 4 h + 0:15
    m.batch = { ...keep, setup: 'batch' }; out.setupBatch = batchPlanOf(o).hours;  // + 2 × 0:30
    out.setupText = batchText(batchPlanOf(o));
    o.targetQty = 250; m.batch = { ...keep, partial: 'prorata' }; out.prorata = batchPlanOf(o).hours; out.prorataText = batchText(batchPlanOf(o)); // 2 × 2:00 + 1:00 + 2 × 0:15
    m.batch = keep; o.targetQty = 300;
    return out;
  }, mid);
  check(v.manualIgnored === 6.5, `Charge hat Vorrang vor manuellen Sollstunden (${v.manualIgnored})`);
  check(v.noQty === 1, `Ohne Menge gelten die manuellen Sollstunden (${v.noQty})`);
  check(v.par === 4.25, `2 Chargen gleichzeitig: 2 Durchgänge × 2:00 + 0:15 = 4,25 h (${v.par})`);
  check(v.setupBatch === 7.5 && v.setupText.includes('Rüsten 2 × 0:30'), `Rüsten je Charge: +2 × 0:30 = 7,5 h (${v.setupBatch}, „${v.setupText}“)`);
  check(v.prorata === 5.5 && v.prorataText.includes('anteilig 1:00'), `Teilcharge anteilig: 5,5 h (${v.prorata}, „${v.prorataText}“)`);
  const cases = [[{ size: 100, minutes: 120, cleanMinutes: 15 }, 300, 30], [{ size: 250, minutes: 95, parallel: 3, cleanMinutes: 7, setup: 'batch', partial: 'prorata' }, 1001, 45], [{ size: 33.3, minutes: 47, parallel: 4, cleanMinutes: 13, setup: 'batch', partial: 'prorata' }, 999, 20], [{ size: 7, minutes: 61 }, 1, 0]];
  const pyRes = spawnSync('python3', ['-c', `import json,sys\nsys.path.insert(0,${JSON.stringify(SRC)})\nfrom release_gates import batch_plan\nprint(json.dumps([batch_plan(c,q,s)["hours"] for c,q,s in json.loads(sys.argv[1])]))`, JSON.stringify(cases)], { encoding: 'utf8' });
  const cl = await ev(cs => cs.map(([c, q, s]) => batchPlanFor(c, q, s).hours), cases);
  check(pyRes.status === 0 && JSON.stringify(JSON.parse(pyRes.stdout)) === JSON.stringify(cl), `Parität Client/Server (${JSON.stringify(cl)} vs. ${pyRes.stdout.trim()})`);

  // (4) Einplanen-Popup: Laufzeit aus Charge, Takt ausgeblendet
  await ev(id => { const o = data.workSteps.find(x => x.id === 'ws_bat'); o.machineId = ''; window.__mid = id; openPlanPopup('ws_bat'); }, mid);
  await page.waitForSelector('#planModal.show');
  await page.selectOption('#planResource', mid); await page.waitForTimeout(200);
  check(Number(await page.inputValue('#planHours')) === 6.5 && await page.locator('#planHours').isDisabled(), `Popup: Laufzeit 6,5 h aus Charge, gesperrt (${await page.inputValue('#planHours')})`);
  check(!(await page.locator('#planTaktField').isVisible()), 'Popup: Takt-Auswahl bei Charge ausgeblendet (Charge > Takt)');
  check((await page.locator('#planBatchHint').innerText()).includes('3 Chargen × 2:00'), 'Popup: Erklärungs-Chip sichtbar');
  await page.click('#planOk'); await page.waitForSelector('#moveModal.show'); await page.click('#confirmMove'); await page.waitForTimeout(300);
  const after = await ev(() => { const o = data.workSteps.find(x => x.id === 'ws_bat'); return { mid: o.machineId, hours: o.hours, takt: o.taktId || '' }; });
  check(after.mid === mid && after.hours === 6.5 && !after.takt, `Popup übernommen: Ressource + Laufzeit 6,5 h, kein Takt (${JSON.stringify(after)})`);
  await waitServer(s => s.workSteps.find(o => o.id === 'ws_bat')?.machineId === mid, 'Einplanung gespeichert');

  // (5) Freigabe: Charge wird eingefroren, Server akzeptiert; spätere Maschinenänderung wirkt nicht auf den freigegebenen FA
  const rel = await ev(() => { const o = data.workSteps.find(x => x.id === 'ws_bat'); const ok = releaseOrder(o); if (ok) save('Freigabe'); return { ok: !!ok, b: o.baselinePlan?.batch, hours: o.hours }; });
  check(rel.ok && rel.b && rel.b.qty === 300 && rel.b.hours === 6.5 && rel.hours === 6.5, `Freigabe friert die Charge ein ${JSON.stringify(rel)}`);
  const s2 = await waitServer(s => s.workSteps.find(o => o.id === 'ws_bat')?.status === 'released', 'Freigabe gespeichert');
  check(s2.workSteps.find(o => o.id === 'ws_bat').baselinePlan.batch.hours === 6.5, 'Server hat die Freigabe mit Charge angenommen');
  const frozen = await ev(id => { machine(id).batch.minutes = 60; const r = calcSchedule().ws_bat; const h = batchPlanOf(data.workSteps.find(x => x.id === 'ws_bat')).hours; machine(id).batch.minutes = 120; return h; }, mid);
  check(frozen === 6.5, `Freigegebener FA nutzt die eingefrorene Charge (${frozen})`);

  // (6) Reload: Charge bleibt erhalten (Client-Migration übernimmt das Feld)
  await page.reload(); await page.waitForTimeout(800);
  if (await page.locator('#loginModal.show').count()) await login();
  await page.waitForTimeout(400);
  const re = await ev(id => ({ b: machine(id)?.batch, h: batchPlanOf(data.workSteps.find(x => x.id === 'ws_bat'))?.hours }), mid);
  check(re.b?.minutes === 120 && re.b?.cleanMinutes === 15 && re.h === 6.5, `Nach Reload: Charge und Planstunden erhalten ${JSON.stringify(re)}`);

  const mig = await ev(() => { const m = data.machines[0]; return migrate({ ...data, machines: [{ ...m, cleanupMinutes: 30, takte: [{ id: 'tk1', name: 'A', sec: 3, unit: 'perHour', parts: 2 }] }] }).machines[0]; });
  check(mig.cleanupMinutes === 30 && mig.takte[0].unit === 'perHour' && mig.takte[0].parts === 2, `Laden/Synchronisieren behält Reinigung und Takt-Einheit/Stück je Takt ${JSON.stringify({ cu: mig.cleanupMinutes, t: mig.takte })}`);
  // (7) Server lehnt ungültige Charge ab
  const put = mut => page.evaluate(async ([id, b]) => { const s = await (await fetch('/api/state')).json(), h = await (await fetch('/api/health')).json(); s.data.machines.find(m => m.id === id).batch = b; const r = await fetch('/api/state', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': h.version }, body: JSON.stringify({ revision: s.revision, data: s.data }) }); return [r.status, (await r.json()).errorCode]; }, [mid, mut]);
  const bad = await put({ size: 0, minutes: 60 });
  check(bad[0] === 400 && bad[1] === 'MP-MACH-020', `Server lehnt Chargengröße 0 ab (${bad})`);

  // (8) Handy-Breite 390 px: Einstellungen und FA-Liste ohne seitlichen Seiten-Scroll
  await page.setViewportSize({ width: 390, height: 844 });
  for (const view of ['settings', 'orders']) {
    await ev(v => { data.ui.sysTab = 'machines'; switchView(v); renderAll(); }, view); await page.waitForTimeout(300);
    await page.evaluate(d => { const x = document.querySelector(d); if (x) x.open = true; }, det);
    const w = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, win: innerWidth }));
    check(w.doc <= w.win + 1, `390 px (${view}): kein seitlicher Seiten-Scroll (${w.doc}/${w.win})`);
  }
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  check(errors.length === 0, 'Keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser?.close(); srv.kill(); rmSync(tmp, { recursive: true, force: true });
}
console.log(`\n${results.filter(Boolean).length}/${results.length} bestanden`);
process.exit(results.every(Boolean) ? 0 : 1);

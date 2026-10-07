#!/usr/bin/env node
// Scheduler Selbsttest muss unabhängig von gespeicherten Maschinen-/Schichtregeln laufen.
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18962, PW = 'Selftest-Passwort-1';
async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const dirs = [], results = [];
const check = (ok, label) => { results.push(!!ok); console.log((ok ? 'PASS ' : 'FAIL ') + label); };
const ev = (page, fn) => page.evaluate(src => window.__t('(' + src + ')')(), fn.toString());
const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-selftest-'));
const cfgDir = mkdtempSync(path.join(tmpdir(), 'mp-selftest-cfg-')); dirs.push(dataDir, cfgDir);
const py = `
import ipaddress, sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR=Path(${JSON.stringify(dataDir)})
server.DB_PATH=server.DATA_DIR/'maschinenplanung.sqlite3'
server.ALLOWED_NETWORK=ipaddress.ip_network('127.0.0.0/8')
server.init_db('metall_cnc')
server.load_config()
server.create_or_reset_admin('admin', ${JSON.stringify(PW)})
s=server.MPHTTPServer(('127.0.0.1', ${PORT}), server.Handler)
print('READY', flush=True)
s.serve_forever()
`;
let srv, browser;
try {
  srv = spawn('python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, MP_CONFIG_DIR: cfgDir } });
  await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('Server startet nicht')), 20000);
    srv.stdout.on('data', d => { if (String(d).includes('READY')) { clearTimeout(t); resolve(); } });
    srv.on('exit', c => reject(new Error('Server beendet: ' + c)));
  });
  const { chromium } = await loadPlaywright(); browser = await chromium.launch();
  const page = await browser.newPage({ timezoneId: 'Europe/Berlin' });
  const pageErrors = []; let writes = 0;
  page.on('pageerror', e => pageErrors.push(e.message));
  await page.route(u => new URL(u).pathname === '/' || new URL(u).pathname === '/index.html', async route => {
    const resp = await route.fetch(); let body = await resp.text();
    const end = body.lastIndexOf('})();');
    body = body.slice(0, end) + 'window.__t=src=>eval(src);\n' + body.slice(end);
    const headers = { ...resp.headers() }; delete headers['content-security-policy']; delete headers['content-length'];
    await route.fulfill({ status: resp.status(), headers, body });
  });
  await page.route('**/api/state', async route => { if (!['GET', 'HEAD'].includes(route.request().method())) writes++; await route.continue(); });
  await page.goto(`http://127.0.0.1:${PORT}/`);
  await page.fill('#loginUser', 'admin'); await page.fill('#loginPassword', PW); await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(800);
  await page.evaluate(() => { const el = document.getElementById('setupModal'); if (el) { el.classList.remove('show'); el.setAttribute('aria-hidden','true'); } });
  await page.click('#navSystem'); await page.click('[data-systab=company]'); await page.waitForSelector('#testTimeLogic');
  const stateBefore = await page.evaluate(async () => (await (await fetch('/api/state')).json()));

  const before = await ev(page, () => {
    data = defaultData();
    window.__liveData = data;
    planningLane = { mid:'live-stale', lane:9 }; schedBlocks = { 'live-stale':[] };
    window.__livePlanningLane = planningLane; window.__liveSchedBlocks = schedBlocks;
    data.shiftTemplates.single = { name:'Sonder', start:'08:10', end:'12:00', breaks:[] };
    data.shiftTemplates.fridaySingle = { name:'Sonder Freitag', start:'09:00', end:'10:00', breaks:[] };
    data.machines[0].defaultShiftMode = '0';
    data.yearRules.push({ machineId:data.machines[0].id, year:2026, mode:'0' });
    data.weekRules.push({ machineId:data.machines[1].id, year:2026, week:37, mode:'2' });
    data.exceptions.push({ date:'2026-09-07', mode:'0', label:'Sperre' });
    data.machineBlocks.push({ id:'live-block', machineId:data.machines[1].id, start:'2026-09-07T06:30', end:'2026-09-07T12:00', label:'Live block' });
    data.personnelGate = true;
    data.machines = data.machines.filter(m => m.id !== 'm1');
    window.__liveRefs = Object.fromEntries(['machines','shiftTemplates','operatorCapacity','yearRules','weekRules','exceptions','machineBlocks','workSteps','audit'].map(k => [k, data[k]]));
    window.__beforeSnapshot = JSON.stringify(data);
    return { snapshot: window.__beforeSnapshot, revision:data.meta.revision };
  });
  await page.click('#testTimeLogic');
  await page.waitForFunction(() => /PASS · isolierter Standard-Teststand/.test(document.querySelector('#timeTestResult').textContent));
  const good = await ev(page, () => ({
    sameData: data === window.__liveData,
    sameScratch: planningLane === window.__livePlanningLane && schedBlocks === window.__liveSchedBlocks,
    sameRefs: Object.entries(window.__liveRefs).every(([k,v]) => data[k] === v),
    sameValue: JSON.stringify(data) === window.__beforeSnapshot,
    machineMissing: !data.machines.some(m => m.id === 'm1'),
    text: document.querySelector('#timeTestResult').innerText
  }));
  // Keep the before-image in page state so equality covers all live data and nested references.
  // The fixture run itself must not alter the live object, its references, or server state.
  check(good.sameData && good.sameScratch && good.sameRefs && good.sameValue && good.machineMissing, 'Live-Objekt und alle überwachten Referenzen trotz Sonderdaten und fehlendem m1 erhalten');
  check(/8:45 h endet Mo 16:00/.test(good.text) && /Rückwärts 8:45 startet 06:30/.test(good.text), 'Vorwärts- und Rückwärtsberechnung folgen dem Standard-Teststand');
  check(/Freitag 5 h endet 11:45/.test(good.text) && /Finite Bediener erkennt 2\/1/.test(good.text), 'Freitag und Bedienerkonflikt geprüft');
  check(!/✗/.test(good.text), 'Alle angezeigten Scheduler-Selbsttests bestehen');

  // Inject a scheduler failure and verify finally restores the exact live references.
  const threw = await ev(page, () => {
    const old = buildSegments; buildSegments = () => { throw new Error('injected selftest failure'); };
    try { runSchedulerTests(); } finally { buildSegments = old; }
    return data === window.__liveData && planningLane === window.__livePlanningLane && schedBlocks === window.__liveSchedBlocks && Object.entries(window.__liveRefs).every(([k,v]) => data[k] === v) && !data.machines.some(m => m.id === 'm1') && /injected selftest failure/.test(document.querySelector('#timeTestResult').textContent);
  });
  check(threw, 'Auch bei Berechnungsfehlern bleiben Live-Daten und Referenzen erhalten');
  const state = await page.evaluate(async () => (await (await fetch('/api/state')).json()));
  check(state.revision === stateBefore.revision && JSON.stringify(state.data.audit) === JSON.stringify(stateBefore.data.audit), 'Selbsttest ändert weder Serverrevision noch Auditprotokoll');
  check(writes === 0, 'Selbsttest sendet keine Schreibanfrage an /api/state');
  check(pageErrors.length === 0, 'Keine Browserfehler: ' + pageErrors.join(', '));
} catch (e) {
  console.error(e); results.push(false);
} finally {
  if (browser) await browser.close();
  if (srv) { srv.kill('SIGTERM'); await new Promise(resolve => srv.once('exit', resolve)); }
  for (const d of dirs) rmSync(d, { recursive: true, force: true });
}
if (!results.length || results.some(x => !x)) process.exitCode = 1;

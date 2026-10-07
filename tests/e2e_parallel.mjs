#!/usr/bin/env node
// E2E Parallelbelegung (ab V12.8.1): Linie mit 2 Parallelplätzen fährt zwei Aufträge gleichzeitig,
// der dritte folgt. Startet server.py mit Testdaten in einem Temp-Ordner.
//
// Aufruf:  node tests/e2e_parallel.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18781;
const BASE = `http://127.0.0.1:${PORT}/`;
const PASS = 'E2E-Test-1234';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-par-'));
const py = `
import ipaddress, json, sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR = Path(${JSON.stringify(dataDir)})
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
server.init_db(seed="werbetechnik")
server.create_or_reset_admin("admin", ${JSON.stringify(PASS)})
with server.DB_LOCK, server.db_session() as con:
    old = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
    new = json.loads(json.dumps(old))
    new["machines"].append({"id": "k1", "name": "Linie K1", "departmentId": "konf1", "kind": "line", "crew": 1, "lanes": 2, "setupMinutes": 0, "start": "2026-09-07T06:30", "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 0})
    def step(i):
        fa = f"FA 800{i}"
        return {"id": f"ws_k{i}", "sequence": i * 10, "planningType": "MACHINE", "pos": i * 10, "departmentId": "konf1", "projectId": "", "predecessorIds": [], "fa": fa, "ab": "", "wt": "", "machineId": "k1", "altMachineId": "", "allowAlternative": False, "order": fa, "articleNo": "", "description": "", "targetQty": 0, "dueDate": "", "baselinePlan": None, "hours": 4, "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none", "requiredStart": "", "requiredFinish": "", "createdAt": "2026-10-01T08:00:00Z", "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "", "pausedAt": "", "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": ""}
    new["workSteps"] += [step(1), step(2), step(3)]
    ok, code, reason = server.validate_state(old, new)
    if not ok:
        print("SEED-FEHLER", code, reason, flush=True); sys.exit(1)
    bad = json.loads(json.dumps(new)); bad["machines"][-1]["lanes"] = 0
    print("LANES0", server.validate_state(old, bad)[1], flush=True)
    con.execute("UPDATE state SET json=?, revision=revision+1 WHERE id=1", (json.dumps(new, ensure_ascii=False),))
httpd = server.MPHTTPServer(("127.0.0.1", ${PORT}), server.Handler)
print("READY", flush=True)
httpd.serve_forever()
`;
let seedOut = '';
const srv = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'] });
await new Promise((resolve, reject) => {
  const t = setTimeout(() => reject(new Error('Server startet nicht')), 20000);
  srv.stdout.on('data', d => { const s = String(d); seedOut += s; if (s.includes('SEED-FEHLER')) reject(new Error(s)); if (s.includes('READY')) { clearTimeout(t); resolve(); } });
  srv.on('exit', c => reject(new Error('Server beendet: ' + c)));
});
check(seedOut.includes('LANES0 MP-MACH-015'), 'Server: Parallelplätze 0 abgelehnt (MP-MACH-015)');

const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
const errors = [];
const serverState = page => page.evaluate(async () => (await (await fetch('/api/state', { cache: 'no-store' })).json()).data);

try {
  // Browser in derselben Zeitzone wie der Server (release_gates: MP_TIMEZONE, Standard Europe/Berlin)
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, timezoneId: 'Europe/Berlin' });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(BASE);
  await page.fill('#loginUser', 'admin');
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(400);

  const plan = async () => {
    await page.click('#navList');
    await page.waitForTimeout(400);
    return page.evaluate(() => Object.fromEntries([...document.querySelectorAll('#ordersBody tr[data-id]')].map(tr => [tr.querySelector('strong').textContent, { start: tr.querySelector('.planWhen').textContent, end: tr.querySelector('.planWhen + .reqDate').textContent, conflict: tr.querySelector('.conflictText')?.textContent || '' }])));
  };
  let p = await plan();
  check(p['FA 8001'] && p['FA 8001'].start !== '—', `FA 8001 geplant (${p['FA 8001']?.start})`);
  check(p['FA 8001']?.start === p['FA 8002']?.start, `2 Parallelplätze: FA 8001 und FA 8002 starten gleichzeitig (${p['FA 8001']?.start} / ${p['FA 8002']?.start})`);
  check(p['FA 8003']?.start !== p['FA 8001']?.start && p['FA 8003']?.start === p['FA 8001']?.end.replace(/^bis /, ''), `FA 8003 folgt, sobald ein Platz frei wird (${p['FA 8003']?.start})`);

  // Parallelplätze in den Einstellungen auf 1 → alles nacheinander
  await page.click('#navSystem');
  await page.waitForTimeout(300);
  const lanes = page.locator('[data-m="k1"][data-f="lanes"]');
  check(await lanes.inputValue() === '2', 'Einstellungen: Spalte "Parallel" zeigt 2');
  await lanes.fill('1');
  await lanes.press('Tab');
  await page.waitForTimeout(700);
  let st = await serverState(page);
  check(!('lanes' in st.machines.find(m => m.id === 'k1')), 'Parallel = 1 gespeichert (Feld entfällt)');
  p = await plan();
  check(new Set([p['FA 8001']?.start, p['FA 8002']?.start, p['FA 8003']?.start]).size === 3, 'Ohne Parallelplätze: drei Aufträge nacheinander');

  await page.click('#navSystem');
  await page.waitForTimeout(300);
  await lanes.fill('2');
  await lanes.press('Tab');
  await page.waitForTimeout(700);
  st = await serverState(page);
  check(st.machines.find(m => m.id === 'k1').lanes === 2, 'Parallel = 2 wieder gespeichert');
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Einstellungen: kein seitlicher Seiten-Scroll mit neuer Spalte');

  // Zwei Aufträge freigeben: Server prüft MP-PLAN-058 spurfähig
  p = await plan();
  for (const fa of ['FA 8001', 'FA 8002']) {
    await page.locator(`#ordersBody tr:has(strong:text-is("${fa}")) [data-act="prodstart"]`).click();
    await page.waitForSelector('#prodStartModal.show, .modal.show #psReleaseOnly', { timeout: 5000 }).catch(() => {});
    await page.click('#psReleaseOnly');
    await page.waitForTimeout(300);
    const em = await page.evaluate(() => document.getElementById('errorModal')?.classList.contains('show') ? document.getElementById('errorModal').innerText.replace(/\s+/g, ' ') : '');
    if (em) { console.log('MELDUNG', fa, em.slice(0, 300)); await page.keyboard.press('Escape'); }
    await page.waitForTimeout(900);
  }
  st = await serverState(page);
  const rel = st.workSteps.filter(o => o.status === 'released').map(o => o.fa).sort();
  check(rel.join(',') === 'FA 8001,FA 8002', `Zwei gleichzeitige Freigaben auf einer Linie mit 2 Plätzen akzeptiert (${rel.join(', ')})`);
  const errShown = await page.evaluate(() => document.getElementById('errorModal')?.classList.contains('show') ? document.getElementById('errorModal').innerText.replace(/\s+/g, ' ') : '');
  check(!errShown, 'Keine Fehlermeldung bei paralleler Freigabe ' + errShown.slice(0, 160));
  const s1 = st.workSteps.find(o => o.fa === 'FA 8001').baselinePlan.segments[0], s2 = st.workSteps.find(o => o.fa === 'FA 8002').baselinePlan.segments[0];
  check(new Date(s1.start) < new Date(s2.end) && new Date(s2.start) < new Date(s1.end), `Freigegebene Planstände überlappen (${s1.start} / ${s2.start})`);

  // Server: dritte überlappende Freigabe wird abgelehnt (Kopie von FA 8002 mit gleichem Planstand)
  const denied = await page.evaluate(async () => {
    const r = await (await fetch('/api/state', { cache: 'no-store' })).json();
    const a = r.data.workSteps.find(o => o.fa === 'FA 8002'), c = r.data.workSteps.find(o => o.fa === 'FA 8003');
    c.status = 'released';
    c.baselinePlan = JSON.parse(JSON.stringify(a.baselinePlan));
    c.baselinePlan.segments = c.baselinePlan.segments.map(s => ({ ...s, orderId: c.id }));
    const res = await fetch('/api/state', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': (await (await fetch('/api/health')).json()).version }, body: JSON.stringify({ revision: r.revision, data: r.data, action: 'Test' }) });
    const j = await res.json();
    return [res.status, j.errorCode, j.error];
  });
  check(denied[0] === 400 && denied[1] === 'MP-PLAN-058', `Dritte überlappende Freigabe abgelehnt (${denied.join(' ')})`);

  await page.click('#navSystem');
  await page.waitForTimeout(300);
  await lanes.fill('1');
  await lanes.press('Tab');
  await page.waitForTimeout(500);
  const err = await page.evaluate(() => document.getElementById('errorModal')?.classList.contains('show') ? document.getElementById('errorModal').innerText : '');
  check(err.includes('MP-MACH-016'), 'Parallelplätze verringern bei Freigaben gesperrt (MP-MACH-016)');
  await page.close();
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

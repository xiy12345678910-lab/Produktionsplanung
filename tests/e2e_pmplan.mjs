#!/usr/bin/env node
// E2E PM-Vorplan vs. AV vs. Ist (ab V12.8.3): AV überschreibt einen PM-Termin → PM-Vorplan wird
// gesichert, PM-Termine sind gesperrt, Vergleichstabelle zeigt die Abweichung in Arbeitstagen.
//
// Aufruf:  node tests/e2e_pmplan.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18785;
const BASE = `http://127.0.0.1:${PORT}/`;
const PASS = 'E2E-Test-1234';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-pm-'));
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
    new["projects"] = [{"id": "p1", "number": "P-2026-001", "phase": "accepted", "name": "Gehäuse", "customer": "Kunde A", "ab": "AB-77", "dueDate": "2026-11-30", "log": [],
                        "processes": [{"id": "pr1", "areaId": "cnc", "title": "Fräsen", "status": "open", "startDate": "2026-10-12", "dueDate": "2026-10-16"},
                                      {"id": "pr2", "areaId": "engineering", "title": "Zeichnung", "status": "open", "startDate": "2026-10-06", "dueDate": "2026-10-09"}]}]
    ok, code, reason = server.validate_state(old, new)
    if not ok:
        print("SEED-FEHLER", code, reason, flush=True); sys.exit(1)
    con.execute("UPDATE state SET json=?, revision=revision+1 WHERE id=1", (json.dumps(new, ensure_ascii=False),))
    for name, role in (("av", "production_planning"), ("pm", "project_management")):
        salt, digest = server.hash_password(${JSON.stringify(PASS)})
        con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", (name, salt, digest, role, "", server.now_iso(), server.now_iso()))
httpd = server.MPHTTPServer(("127.0.0.1", ${PORT}), server.Handler)
print("READY", flush=True)
httpd.serve_forever()
`;
const srv = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, MP_CONFIG_DIR: path.join(dataDir, 'config') } });
await new Promise((resolve, reject) => {
  const t = setTimeout(() => reject(new Error('Server startet nicht')), 20000);
  srv.stdout.on('data', d => { const s = String(d); if (s.includes('SEED-FEHLER')) reject(new Error(s)); if (s.includes('READY')) { clearTimeout(t); resolve(); } });
  srv.on('exit', c => reject(new Error('Server beendet: ' + c)));
});

const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
const errors = [];
async function open(user) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, timezoneId: 'Europe/Berlin' });
  page.on('pageerror', e => errors.push(`${user}: ${e.message}`));
  await page.goto(BASE);
  await page.fill('#loginUser', user);
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(400);
  return page;
}
const serverState = page => page.evaluate(async () => (await (await fetch('/api/state', { cache: 'no-store' })).json()).data);
const errText = page => page.evaluate(() => document.getElementById('errorModal')?.classList.contains('show') ? document.getElementById('errorModal').innerText.replace(/\s+/g, ' ') : '');
// Projektkarte öffnen, bis die Prozess-Felder sichtbar sind
async function openProject(page) {
  if (!(await page.locator('[data-fexp="pr1"]').count())) {
    await page.click('#navOrders');
    await page.waitForTimeout(400);
    await page.locator('[data-project-open="p1"]').first().click();
    await page.waitForTimeout(500);
  }
  for (const id of ['pr1', 'pr2']) {
    if (!(await page.locator(`[data-proc-due="${id}"]`).count())) {
      await page.locator(`[data-fexp="${id}"]`).click();
      await page.waitForTimeout(300);
    }
  }
}

try {
  const av = await open('av');
  await openProject(av);
  const due = av.locator('[data-proc-due="pr1"]');
  check(await due.count() === 1 && !(await due.isDisabled()), 'AV: Fälligkeit des Fertigungsprozesses änderbar');
  await due.fill('2026-10-21');
  await due.press('Tab');
  await av.waitForTimeout(900);
  let st = await serverState(av);
  let pr = st.projects[0].processes.find(x => x.id === 'pr1');
  check(pr.dueDate === '2026-10-21' && pr.pmPlan?.dueDate === '2026-10-16' && pr.pmPlan?.startDate === '2026-10-12' && pr.pmPlan?.by === 'av', `PM-Vorplan beim ersten AV-Überschreiben gesichert ${await errText(av)}`);
  await openProject(av);
  const start = av.locator('[data-proc-start="pr1"]');
  await start.fill('2026-10-14');
  await start.press('Tab');
  await av.waitForTimeout(900);
  st = await serverState(av);
  pr = st.projects[0].processes.find(x => x.id === 'pr1');
  check(pr.startDate === '2026-10-14' && pr.pmPlan.startDate === '2026-10-12', 'Zweite AV-Änderung lässt den PM-Vorplan unverändert');
  // Fokus verlassen und Projektfenster neu zeichnen (während der Eingabe bleibt es stehen)
  await av.evaluate(() => document.activeElement?.blur());
  await av.locator('[data-fexp="pr2"]').click();
  await av.waitForTimeout(300);
  const row = await av.evaluate(() => [...document.querySelectorAll('.pmAvTable tbody tr')].map(tr => [...tr.children].map(td => td.textContent.trim())));
  const cnc = row.find(r => r[0] === 'CNC');
  check(cnc && cnc[1].startsWith('16.10.') && cnc[2] === '21.10.' && cnc[3] === '+3 AT', `Vergleich: PM 16.10. → AV 21.10. = +3 AT (${JSON.stringify(cnc)})`);
  check(!row.some(r => r[0].includes('Konstruktion')), 'Vergleich zeigt nur Produktionsbereiche');
  check(await av.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Projekte: kein seitlicher Seiten-Scroll');
  await av.close();

  const pm = await open('pm');
  await openProject(pm);
  check(await pm.locator('[data-proc-due="pr1"]').isDisabled(), 'PM: Termine des von der AV übernommenen Prozesses gesperrt');
  check(!(await pm.locator('[data-proc-due="pr2"]').isDisabled()), 'PM: Termine eigener Prozesse weiter änderbar');
  check(await pm.locator('.pmAvTable').count() === 1, 'PM sieht die Vergleichstabelle');
  const res = await pm.evaluate(async () => {
    const r = await (await fetch('/api/state', { cache: 'no-store' })).json();
    r.data.projects[0].processes.find(x => x.id === 'pr1').dueDate = '2026-10-16';
    const x = await fetch('/api/state', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': (await (await fetch('/api/health')).json()).version }, body: JSON.stringify({ revision: r.revision, data: r.data, action: 'Test' }) });
    return [x.status, (await x.json()).errorCode];
  });
  check(res[0] === 403, `PM: Terminänderung per API nach AV-Übernahme abgelehnt (${res.join(' ')})`);
  await pm.close();
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

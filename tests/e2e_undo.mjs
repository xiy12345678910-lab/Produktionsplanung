#!/usr/bin/env node
// E2E Undo/Redo (ab V12.12.0): Prio verschieben → Rückgängig → Wiederholen, Fremdänderung blockiert
// Rückgängig, Strg+Z im Eingabefeld bleibt Browser-Undo, Lesende sehen keine Knöpfe.
//
// Aufruf:  node tests/e2e_undo.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18797;
const BASE = `http://127.0.0.1:${PORT}/`;
const PASS = 'E2E-Test-1234';
async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-undo-'));
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
    def step(i, dep, mid, **kw):
        fs = f"FS 70{i:02d}"
        x = {"id": f"ws_r{i}", "sequence": i * 10, "planningType": "MACHINE", "pos": i * 10, "departmentId": dep, "projectId": "", "predecessorIds": [], "fs": fs, "ab": "AB-500", "wt": "", "machineId": mid, "altMachineId": "", "allowAlternative": False, "order": fs, "articleNo": "A-1", "description": "Rundgang", "targetQty": 20, "dueDate": "2026-10-23", "baselinePlan": None, "hours": 5, "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none", "requiredStart": "", "requiredFinish": "", "createdAt": "2026-10-01T08:00:00Z", "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "", "pausedAt": "", "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": ""}
        x.update(kw)
        return x
    tf = next(m["id"] for m in new["machines"] if m["departmentId"] == "thermoforming")
    new["workSteps"] += [step(1, "cnc", "m1"), step(2, "cnc", "m2"), step(3, "thermoforming", tf)]
    new["employees"] = [{"id": "e1", "name": "Max Fräser", "departmentId": "cnc", "active": True, "skills": ["m1"]}, {"id": "e2", "name": "Ute Zieher", "departmentId": "thermoforming", "active": True, "skills": []}]
    new["projects"] = [{"id": "p1", "number": "P-2026-001", "phase": "accepted", "name": "Gehäuse", "customer": "Kunde A", "ab": "AB-500", "dueDate": "2026-11-30", "log": [],
                        "processes": [{"id": "pr1", "areaId": "cnc", "title": "Fräsen", "status": "open", "startDate": "2026-10-12", "dueDate": "2026-10-16"}]},
                       {"id": "p2", "number": "P-2026-002", "phase": "inquiry", "name": "Anfrage Schale", "customer": "Kunde B", "ab": "", "dueDate": "", "log": [], "processes": []}]
    ok, code, reason = server.validate_state(old, new)
    if not ok:
        print("SEED-FEHLER", code, reason, flush=True); sys.exit(1)
    con.execute("UPDATE state SET json=?, revision=revision+1 WHERE id=1", (json.dumps(new, ensure_ascii=False),))
    for name, role, dep in ${JSON.stringify([["admin2", "admin", ""], ["viewer", "viewer", ""]])}:
        salt, digest = server.hash_password(${JSON.stringify(PASS)})
        con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", (name, salt, digest, role, dep, server.now_iso(), server.now_iso()))
httpd = server.MPHTTPServer(("127.0.0.1", ${PORT}), server.Handler)
print("READY", flush=True)
httpd.serve_forever()
`;
const srv = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'] });
await new Promise((resolve, reject) => {
  const t = setTimeout(() => reject(new Error('Server startet nicht')), 20000);
  srv.stdout.on('data', d => { const s = String(d); if (s.includes('SEED-FEHLER')) reject(new Error(s)); if (s.includes('READY')) { clearTimeout(t); resolve(); } });
  srv.on('exit', c => reject(new Error('Server beendet: ' + c)));
});

const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
const errors = [];
async function open(user) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, timezoneId: 'Europe/Berlin' });
  page.on('pageerror', e => errors.push(`${user}: ${e.message}`));
  await page.goto(BASE);
  await page.fill('#loginUser', user);
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(500);
  return page;
}
// Serverstand: Reihenfolge der CNC-Aufträge nach pos
const serverOrder = page => page.evaluate(async () => {
  const v = document.title.match(/V([\d.]+)/)[1];
  const r = await fetch('/api/state', { headers: { 'X-MP-Client-Version': v } });
  const b = await r.json();
  return { rev: b.revision, ids: b.data.workSteps.filter(x => x.departmentId === 'cnc').sort((a, c) => a.pos - c.pos).map(x => x.id).join(',') };
});
const prio = async (page, sel) => { await page.click(sel); await page.waitForSelector('#moveModal.show', { timeout: 5000 }); await page.click('#confirmMove'); };
const settle = async page => { await page.waitForFunction(() => /gespeichert/i.test(document.getElementById('saveState').textContent), null, { timeout: 8000 }).catch(() => {}); await page.waitForTimeout(400); };

try {
  const a = await open('admin');
  await a.click('#navList'); await a.waitForTimeout(300);
  check(await a.locator('#undoBtn').isVisible() && await a.locator('#undoBtn').isDisabled(), 'Knöpfe ↶ ↷ sichtbar, ohne Aktion deaktiviert');
  const s0 = await serverOrder(a);
  await prio(a, '[data-prio-move="ws_r1|1"]'); await settle(a);
  const s1 = await serverOrder(a);
  check(s1.ids !== s0.ids && s1.rev > s0.rev, `Prio ↓ verschiebt Auftrag (${s0.ids} → ${s1.ids})`);
  const title = await a.getAttribute('#undoBtn', 'title');
  check(/Rückgängig: /.test(title) && await a.locator('#undoBtn').isEnabled(), `Tooltip nennt die Aktion („${title}“)`);
  await a.click('#undoBtn'); await settle(a);
  const s2 = await serverOrder(a);
  check(s2.ids === s0.ids && s2.rev > s1.rev, `Rückgängig: Reihenfolge wie vorher, über den Server gespeichert (${s2.ids})`);
  check(await a.locator('#redoBtn').isEnabled(), 'Wiederholen verfügbar');
  await a.locator('body').click({ position: { x: 5, y: 5 } }).catch(() => {});
  await a.keyboard.press('Control+y'); await settle(a);
  const s3 = await serverOrder(a);
  check(s3.ids === s1.ids, `Strg+Y wiederholt (${s3.ids})`);
  // Strg+Z im Eingabefeld: kein App-Undo
  const input = a.locator('#orders input:not([type=hidden]):visible').first();
  if (await input.count()) {
    await input.click(); await input.type('x');
    await a.keyboard.press('Control+z'); await a.waitForTimeout(600);
    const s4 = await serverOrder(a);
    check(s4.rev === s3.rev, 'Strg+Z im Eingabefeld löst kein App-Rückgängig aus');
    await input.fill(''); await a.locator('h2, h1').first().click().catch(() => {});
  } else check(false, 'Kein Eingabefeld in der Auftragsliste gefunden');

  // Fremdänderung am selben Auftrag → Rückgängig wird abgelehnt
  const b = await open('admin2');
  await b.click('#navList'); await b.waitForTimeout(300);
  await prio(b, '[data-prio-move="ws_r1|-1"]'); await settle(b);
  const sb = await serverOrder(b);
  await a.waitForFunction(r => true, null).catch(() => {});
  await a.waitForTimeout(2500);   // Long-Poll holt den Fremdstand
  await a.click('#undoBtn'); await a.waitForTimeout(800);
  const toastText = await a.evaluate(() => [...document.querySelectorAll('.toast, #toast, [role="status"]')].map(x => x.textContent).join(' '));
  const s5 = await serverOrder(a);
  check(s5.rev === sb.rev && s5.ids === sb.ids, 'Rückgängig nach Fremdänderung ändert nichts am Server');
  check(/Inzwischen von admin2 geändert/.test(toastText), `Meldung nennt den anderen Benutzer („${toastText.trim().slice(0, 80)}“)`);
  check(await a.locator('#undoBtn').isDisabled(), 'Abgelehnter Eintrag wird verworfen');

  // Lesende: keine Knöpfe
  const v = await open('viewer');
  check(!(await v.locator('#undoBtn').isVisible()) && !(await v.locator('#redoBtn').isVisible()), 'Lesende sehen keine ↶ ↷');
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

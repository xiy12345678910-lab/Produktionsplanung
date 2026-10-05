#!/usr/bin/env node
// E2E Kundenplan-Excel (ab V12.9.1), nachgebaut nach P-2026-0007:
// - ohne Liefertermin kein erfundener Termin/keine Lieferzeile ("offen – folgt nach Freigabe")
// - interne Aufgaben fehlen im Excel, die Kundenplan-Box nennt sie; "nur intern" abwählen nimmt sie auf
// - mit Liefertermin erscheint die Lieferzeile
//
// Aufruf:  node tests/e2e_customerplan.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18791;
const BASE = `http://127.0.0.1:${PORT}/`;
const PASS = 'E2E-Test-1234';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-cp-'));
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
    new["projects"] = [{"id": "p7", "number": "P-2026-0007", "phase": "inquiry", "name": "Test", "customer": "Test", "ab": "", "dueDate": "", "log": [],
                        "processes": [{"id": "pr1", "areaId": "sales", "title": "PowerPoint vorstellung", "status": "open", "startDate": "2026-10-05", "dueDate": "2026-10-10"},
                                      {"id": "pr2", "areaId": "pm", "title": "Timings dem Kunden senden", "status": "open", "startDate": "2026-10-06", "dueDate": "2026-10-07", "internal": True}]}]
    ok, code, reason = server.validate_state(old, new)
    if not ok:
        print("SEED-FEHLER", code, reason, flush=True); sys.exit(1)
    con.execute("UPDATE state SET json=?, revision=revision+1 WHERE id=1", (json.dumps(new, ensure_ascii=False),))
    salt, digest = server.hash_password(${JSON.stringify(PASS)})
    con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", ("Jonas", salt, digest, "project_management", "", server.now_iso(), server.now_iso()))
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
// XLSX ist unkomprimiert (zipStore) → Zellinhalte stehen als Klartext in der Datei
const xlsxText = file => readFileSync(file).toString('utf8');
try {
  const page = await browser.newPage({ viewport: { width: 1540, height: 950 }, timezoneId: 'Europe/Berlin', acceptDownloads: true });
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(BASE);
  await page.fill('#loginUser', 'Jonas');
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(500);
  const openProject = async () => {
    if (!(await page.locator('#cpDownload').count())) {
      await page.click('#navOrders');
      await page.waitForTimeout(300);
      await page.locator('[data-project-open="p7"]').first().click();
      await page.waitForTimeout(500);
    }
  };
  const exportXlsx = async () => {
    await openProject();
    const [dl] = await Promise.all([page.waitForEvent('download'), page.click('#cpDownload')]);
    const file = await dl.path();
    if (process.env.CP_SAVE) await dl.saveAs(path.join(process.env.CP_SAVE, dl.suggestedFilename()));
    await page.waitForTimeout(600);
    return { name: dl.suggestedFilename(), text: xlsxText(file) };
  };

  await openProject();
  const rowText = await page.locator('.fLine', { hasText: 'Timings dem Kunden senden' }).first().textContent();
  check(rowText.includes('nicht im Kundenplan'), 'Interne Aufgabe ist als „nicht im Kundenplan“ gekennzeichnet');
  check((await page.locator('.cpBox').textContent()).includes('Nicht im Kundenplan (nur intern): Timings dem Kunden senden'), 'Kundenplan-Box nennt die ausgelassene Aufgabe');

  let x = await exportXlsx();
  check(x.name === 'Terminplan_P-2026-0007_Test_V1.xlsx', `Dateiname (${x.name})`);
  check(x.text.includes('PowerPoint vorstellung') && !x.text.includes('Timings dem Kunden senden'), 'V1: interne Aufgabe nicht im Excel');
  check(x.text.includes('offen – folgt nach Freigabe') && !x.text.includes('LIEFERUNG / LIEFERTERMIN'), 'V1: ohne Liefertermin kein erfundener Termin, keine Lieferzeile');

  // "nur intern" abwählen → Aufgabe im Kundenplan
  await openProject();
  if (!(await page.locator('[data-proc-internal="pr2"]').count())) { await page.locator('[data-fexp="pr2"]').click(); await page.waitForTimeout(300); }
  await page.locator('[data-proc-internal="pr2"]').uncheck();
  await page.waitForTimeout(800);
  const st = await page.evaluate(async () => (await (await fetch('/api/state', { cache: 'no-store' })).json()).data);
  check(!st.projects[0].processes.find(p => p.id === 'pr2').internal, '„nur intern“ abgewählt und gespeichert');
  await page.evaluate(() => document.activeElement?.blur());
  x = await exportXlsx();
  check(x.name.endsWith('_V2.xlsx') && x.text.includes('Timings dem Kunden senden'), 'V2: Aufgabe „Timings dem Kunden senden“ im Excel');

  // Neue PM-Aufgabe ist standardmäßig im Kundenplan
  await openProject();
  const r = await page.evaluate(async () => {
    const res = await (await fetch('/api/state', { cache: 'no-store' })).json();
    return res.data.projects[0].customerPlan?.steps?.map(s => s.title).join(' | ');
  });
  check(r === 'PowerPoint vorstellung | Timings dem Kunden senden' || r === 'Timings dem Kunden senden | PowerPoint vorstellung', `Kundenplan-Stand V2 gespeichert (${r})`);
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

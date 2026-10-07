#!/usr/bin/env node
// Abnahme-Rundgang (ab V12.9.1): jede Rolle × jede sichtbare Ansicht × Desktop/Handy.
// Prüft: keine JavaScript-Fehler, kein seitlicher Seiten-Scroll, Felder/Symbol-Knöpfe beschriftet,
// Navigation zeigt nur erlaubte Ansichten. Optional Bildschirmfotos: --shots <ordner>
//
// Aufruf:  node tests/e2e_roles.mjs [--shots <ordner>]
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18789;
const BASE = `http://127.0.0.1:${PORT}/`;
const PASS = 'E2E-Test-1234';
const shotDir = process.argv.includes('--shots') ? path.resolve(process.argv[process.argv.indexOf('--shots') + 1]) : '';
if (shotDir) mkdirSync(shotDir, { recursive: true });

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); if (!ok) console.log('FAIL ' + label); };

// Rolle → Benutzer, Bereich, erwartete Navigation (Reihenfolge egal). Mit „System“ liegen Historie/Report dort als Reiter.
const ROLES = [
  ['admin', 'admin', '', ['navPlan', 'navList', 'navOrders', 'navPersonnel', 'navGF', 'navSystem']],
  ['gf', 'gf', '', ['navPlan', 'navList', 'navOrders', 'navGF', 'navSystem']],
  ['lead', 'department_lead', 'cnc', ['navPlan', 'navList', 'navOrders', 'navPersonnel', 'navSystem']],
  ['deputy', 'department_deputy', 'thermoforming', ['navPlan', 'navList', 'navOrders', 'navPersonnel', 'navSystem']],
  ['viewer', 'viewer', '', ['navPlan', 'navList', 'navOrders', 'navPersonnel', 'navHistory', 'navReport']],
  ['pm', 'project_management', '', ['navOrders', 'navHistory', 'navReport']],
  ['av', 'production_planning', '', ['navPlan', 'navList', 'navOrders', 'navHistory', 'navReport']],
  ['sales', 'sales', '', ['navOrders', 'navReport']],
];
const NAV_ALL = ['navPlan', 'navList', 'navOrders', 'navPersonnel', 'navGF', 'navSystem', 'navHistory', 'navReport'];

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-roles-'));
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
        fs = f"FA 70{i:02d}"
        x = {"id": f"ws_r{i}", "sequence": i * 10, "planningType": "MACHINE", "pos": i * 10, "departmentId": dep, "projectId": "", "predecessorIds": [], "fa": fs, "ab": "AB-500", "wt": "", "machineId": mid, "altMachineId": "", "allowAlternative": False, "order": fs, "articleNo": "A-1", "description": "Rundgang", "targetQty": 20, "dueDate": "2026-10-23", "baselinePlan": None, "hours": 5, "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none", "requiredStart": "", "requiredFinish": "", "createdAt": "2026-10-01T08:00:00Z", "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "", "pausedAt": "", "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": ""}
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
    for name, role, dep in ${JSON.stringify(ROLES.filter(r => r[0] !== 'admin').map(r => [r[0], r[1], r[2]]))}:
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
let views = 0;
try {
  for (const [user, , , navs] of ROLES) {
    for (const [w, h] of [[1440, 900], [390, 844]]) {
      const page = await browser.newPage({ viewport: { width: w, height: h }, timezoneId: 'Europe/Berlin' });
      page.on('pageerror', e => errors.push(`${user}@${w}: ${e.message}`));
      await page.goto(BASE);
      await page.fill('#loginUser', user);
      await page.fill('#loginPassword', PASS);
      await page.click('#loginBtn');
      await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
      await page.waitForTimeout(500);
      const visible = [];
      for (const n of NAV_ALL) if (await page.locator('#' + n).isVisible()) visible.push(n);
      check(visible.sort().join() === [...navs].sort().join(), `${user}@${w}: Navigation ${visible.join(',')} (erwartet ${navs.join(',')})`);
      if (user === 'gf') {
        await page.click('#navPlan'); await page.waitForTimeout(300);
        const ro = await page.evaluate(() => ({ view: document.querySelector('.view.active')?.id, add: !!document.getElementById('quickAdd')?.offsetParent, drag: document.querySelectorAll('#overview [draggable="true"]').length, chips: document.querySelectorAll('#overview .jobchip').length }));
        check(ro.view === 'overview' && ro.chips > 0, `gf@${w}: Wochenplan sichtbar (${ro.chips} Aufträge)`);
        check(!ro.add && ro.drag === 0, `gf@${w}: Wochenplan nur lesen (kein + Auftrag, nicht verschiebbar)`);
      }
      const targets = visible.map(n => ['#' + n]);
      if (visible.includes('navSystem')) for (const sub of ['#sysAudit', '#sysHistory', '#sysReport']) targets.push(['#navSystem', sub]);
      for (const steps of targets) {
        let reachable = true;
        for (const sel of steps) {
          const l = page.locator(sel);
          if (!(await l.isVisible()) || !(await l.isEnabled())) { reachable = false; break; }
          await l.click(); await page.waitForTimeout(200);
        }
        if (!reachable) continue;
        await page.waitForTimeout(350);
        views++;
        const r = await page.evaluate(() => {
          const vis = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
          const view = document.querySelector('.view.active');
          const unlabeled = [...view.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(vis)
            .filter(c => !(c.labels && c.labels.length) && !c.getAttribute('aria-label') && !c.getAttribute('aria-labelledby') && !c.getAttribute('title'))
            .map(c => c.id || c.outerHTML.slice(0, 70));
          const nameless = [...document.querySelectorAll('button')].filter(vis)
            .filter(b => !(b.getAttribute('aria-label') || b.getAttribute('title') || (b.textContent || '').trim().replace(/[^\p{L}\p{N}]/gu, '')))
            .map(b => b.id || b.outerHTML.slice(0, 70));
          const err = document.getElementById('errorModal')?.classList.contains('show') ? document.getElementById('errorModal').innerText.replace(/\s+/g, ' ').slice(0, 160) : '';
          // Verursacher eines Überlaufs: äußerste Elemente, die rechts über den Bildschirm ragen
          const wide = document.documentElement.scrollWidth > innerWidth + 1 ? [...view.querySelectorAll('*')].filter(e => e.getClientRects().length && e.getBoundingClientRect().right > innerWidth + 1 && !(e.parentElement && e.parentElement.getBoundingClientRect().right > innerWidth + 1 && e.parentElement !== view)).slice(0, 3).map(e => `${e.tagName.toLowerCase()}${e.id ? '#' + e.id : ''}.${String(e.className).split(' ')[0]}`) : [];
          return { id: view?.id, sw: document.documentElement.scrollWidth, iw: innerWidth, unlabeled, nameless, err, wide };
        });
        const tag = `${user}@${w}/${r.id}`;
        check(r.sw <= r.iw + 1, `${tag}: kein seitlicher Seiten-Scroll (${r.sw} > ${r.iw}: ${r.wide.join(', ')})`);
        check(!r.unlabeled.length, `${tag}: Felder ohne Beschriftung: ${r.unlabeled.slice(0, 3).join(' | ')}`);
        check(!r.nameless.length, `${tag}: Knöpfe ohne Namen: ${r.nameless.slice(0, 3).join(' | ')}`);
        check(!r.err, `${tag}: Fehlermeldung beim Öffnen: ${r.err}`);
        if (shotDir) await page.screenshot({ path: path.join(shotDir, `${user}_${w}_${r.id}.png`), fullPage: false });
      }
      // Server-Schutz: verbotene Ansicht per Hash/Funktion nicht erreichbar
      await page.close();
    }
  }
  check(views > 40, `Rundgang: ${views} Ansichten geprüft`);
} catch (e) {
  check(false, 'Ablauf: ' + (e?.message || e));
} finally {
  check(!errors.length, 'keine JavaScript-Fehler ' + errors.slice(0, 5).join(' | '));
  await browser.close();
  srv.kill();
  try { rmSync(dataDir, { recursive: true, force: true }); } catch {}
}
const failed = results.filter(r => !r[0]).length;
console.log(`\n${results.length - failed}/${results.length} bestanden`);
process.exit(failed ? 1 : 0);

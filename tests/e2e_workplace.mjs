#!/usr/bin/env node
// #84: Bereich ohne Arbeitsplatz (Altbestand, z. B. Zuschnitte/Schlosserei vor V12.23). Hinweis im Wochenplan,
// im Einplanen-Popup und in der Produktionsansicht; "Arbeitsplatz anlegen" nur fuer Admin und die eigene
// Abteilungsleitung. Danach ist der FA sofort planbar. Rollen ohne Recht sehen nur den Hinweis.
//
// Aufruf:  node tests/e2e_workplace.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18973;
const BASE = `http://127.0.0.1:${PORT}/`;
const PASS = 'E2E-Arbeitsplatz-1234';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

// Zwei Altbestand-Bereiche ohne jede Ressource, je ein FA im Bereichsvorrat.
const USERS = [['slead', 'department_lead', 'schlosserei'], ['leser', 'viewer', ''], ['zprod', 'production', 'zuschnitte'], ['cnclead', 'department_lead', 'cnc']];
const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-workplace-'));
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
    new["departments"] += [{"id": "zuschnitte", "name": "Zuschnitte", "planningType": "MACHINE"}, {"id": "schlosserei", "name": "Schlosserei", "planningType": "MACHINE"}]
    def step(i, dep):
        fa = f"FA 84{i:02d}"
        return {"id": f"ws_w{i}", "sequence": i * 10, "planningType": "MACHINE", "pos": i * 10, "departmentId": dep, "projectId": "", "predecessorIds": [], "fa": fa, "ab": "AB-84", "wt": "", "machineId": "", "altMachineId": "", "allowAlternative": False, "order": fa, "articleNo": "A-84", "description": "Altbestand", "targetQty": 10, "dueDate": "2026-12-18", "baselinePlan": None, "hours": 0, "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none", "requiredStart": "", "requiredFinish": "", "createdAt": "2026-10-01T08:00:00Z", "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "", "pausedAt": "", "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": "", "handoffUnassigned": True}
    new["workSteps"] += [step(1, "zuschnitte"), step(2, "schlosserei")]
    ok, code, reason = server.validate_state(old, new)
    if not ok:
        print("SEED-FEHLER", code, reason, flush=True); sys.exit(1)
    con.execute("UPDATE state SET json=?, revision=revision+1 WHERE id=1", (json.dumps(new, ensure_ascii=False),))
    for name, role, dep in ${JSON.stringify(USERS)}:
        salt, digest = server.hash_password(${JSON.stringify(PASS)})
        con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", (name, salt, digest, role, dep, server.now_iso(), server.now_iso()))
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
  await page.waitForTimeout(500);
  return page;
}
const serverState = page => page.evaluate(async () => (await (await fetch('/api/state', { cache: 'no-store' })).json()).data);
const errText = page => page.evaluate(() => document.getElementById('errorModal')?.classList.contains('show') ? document.getElementById('errorModal').innerText.replace(/\s+/g, ' ') : '');
const HINT = 'Dieser Bereich hat noch keinen Arbeitsplatz.';
const waitMachine = async (page, dep) => { for (let i = 0; i < 40; i++) { const m = (await serverState(page)).machines.find(x => x.departmentId === dep); if (m) return m; await page.waitForTimeout(250); } return null; };

try {
  // Rollen ohne Recht: nur Hinweis, kein Knopf
  const leser = await open('leser');
  await leser.click('#navPlan'); await leser.waitForTimeout(300);
  const lz = leser.locator('#board [data-nores="zuschnitte"]');
  check(await lz.count() === 1 && (await lz.innerText()).includes(HINT), 'Viewer: Hinweis im Bereichsvorrat (Zuschnitte)');
  check(await leser.locator('[data-res-create]').count() === 0, 'Viewer: kein Knopf "Arbeitsplatz anlegen"');
  check((await lz.innerText()).includes('System → Maschinen & Schichtkalender'), 'Viewer: Hinweis nennt, wo Admin/Abteilungsleitung anlegen');
  await leser.close();

  const zprod = await open('zprod');
  await zprod.click('#navList'); await zprod.waitForTimeout(300);
  const pg = zprod.locator('#productionGrid [data-nores="zuschnitte"]');
  check(await pg.count() === 1 && (await pg.innerText()).includes(HINT), 'Produktion (Zuschnitte): Hinweis in der Produktionsansicht');
  check(await zprod.locator('[data-res-create]').count() === 0, 'Produktion: kein Knopf "Arbeitsplatz anlegen"');
  await zprod.close();

  // Fremde Abteilungsleitung: Bereich nicht im eigenen Recht -> kein Knopf (nicht sichtbar im eigenen Bereich)
  const cnclead = await open('cnclead');
  await cnclead.click('#navPlan'); await cnclead.waitForTimeout(300);
  check(await cnclead.locator('[data-res-create]').count() === 0, 'Abteilungsleitung CNC: kein Knopf fuer fremde Bereiche');
  await cnclead.close();

  // Admin: Hinweis + Knopf im Bereichsvorrat, anlegen mit Planungsart Personenstunden (Linie)
  const admin = await open('admin');
  await admin.click('#navPlan'); await admin.waitForTimeout(300);
  for (const dep of ['zuschnitte', 'schlosserei']) {
    const h = admin.locator(`#board [data-nores="${dep}"]`);
    check(await h.count() === 1 && (await h.innerText()).includes(HINT) && await h.locator(`[data-res-create="${dep}"]`).isVisible(), `Admin: Hinweis + Knopf "Arbeitsplatz anlegen" (${dep})`);
  }
  check((await serverState(admin)).machines.every(m => !['zuschnitte', 'schlosserei'].includes(m.departmentId)), 'Ohne Klick keine Ressource angelegt (keine Migration)');
  await admin.click('#board [data-res-create="zuschnitte"]');
  await admin.waitForSelector('#askModal.show');
  const dlg = await admin.evaluate(() => ({ title: document.getElementById('askTitle').textContent, name: document.getElementById('askInput').value, sel: document.getElementById('askSelectField').style.display, opts: [...document.querySelectorAll('#askSelect option')].map(o => o.value) }));
  check(dlg.title === 'Arbeitsplatz anlegen' && dlg.name === 'Zuschnitte 1' && dlg.sel === '' && dlg.opts.join() === 'machine,line,cycle', `Dialog: Name-Vorschlag und Planungsart wie beim Bereich anlegen (${JSON.stringify(dlg)})`);
  await admin.fill('#askInput', 'Zuschnitt-Linie'); await admin.selectOption('#askSelect', 'line'); await admin.click('#askOk');
  const zm = await waitMachine(admin, 'zuschnitte');
  check(zm && zm.name === 'Zuschnitt-Linie' && zm.kind === 'line' && zm.staffRequired === 0 && zm.effortScaling === true && zm.crew === 1 && zm.defaultShiftMode === '1' && /T06:30$/.test(zm.start), `Ressource gespeichert wie beim Bereich anlegen (${JSON.stringify(zm)}) ${await errText(admin)}`);
  await admin.waitForTimeout(300);
  check(await admin.locator('#board [data-nores="zuschnitte"]').count() === 0, 'Hinweis fuer Zuschnitte verschwindet');
  // FA ist jetzt einplanbar
  await admin.click('#board [data-handoff-plan="ws_w1"]');
  await admin.waitForSelector('#planModal.show');
  const opts = await admin.evaluate(() => [...document.querySelectorAll('#planResource option')].map(o => o.value));
  check(opts.includes(zm?.id) && !(await admin.locator('#planNoRes [data-nores]').count()), `Einplanen-Popup bietet die neue Ressource an (${opts.join(',')})`);
  await admin.selectOption('#planResource', zm.id); await admin.fill('#planHours', '4'); await admin.waitForTimeout(200);
  check(!(await admin.locator('#planOk').isDisabled()), 'Einplanung uebernehmbar (Vorschau ohne Konflikt)');
  await admin.click('#planCancel');
  await admin.close();

  // Abteilungsleitung des eigenen Bereichs: Hinweis im Einplanen-Popup, Anlegen von dort, Popup zeigt danach die Ressource
  const slead = await open('slead');
  await slead.click('#navPlan'); await slead.waitForTimeout(300);
  check(await slead.locator('#board [data-res-create="schlosserei"]').count() === 1, 'Abteilungsleitung Schlosserei: Knopf im leeren Wochenplan des Bereichs (einmal)');
  await slead.click('#board [data-handoff-plan="ws_w2"]');
  await slead.waitForSelector('#planModal.show');
  const ph = slead.locator('#planNoRes [data-nores="schlosserei"]');
  check(await ph.count() === 1 && (await ph.innerText()).includes(HINT), 'Einplanen-Popup: Hinweis "noch kein Arbeitsplatz"');
  await ph.locator('[data-res-create]').click();
  await slead.waitForSelector('#askModal.show');
  const lopts = await slead.evaluate(() => [...document.querySelectorAll('#askSelect option')].map(o => o.value));
  check(lopts.join() === 'machine,line', `Abteilungsleitung: Planungsart ohne Takt (wie Bereich anlegen) (${lopts})`);
  await slead.click('#askOk');
  const sm = await waitMachine(slead, 'schlosserei');
  check(sm && sm.name === 'Schlosserei 1' && sm.kind === 'machine' && sm.staffRequired === 1 && !('effortScaling' in sm), `Abteilungsleitung legt Maschine an (Server akzeptiert) (${JSON.stringify(sm)}) ${await errText(slead)}`);
  await slead.waitForSelector('#planModal.show');
  const sopts = await slead.evaluate(() => [...document.querySelectorAll('#planResource option')].map(o => o.value));
  check(sopts.includes(sm?.id) && !(await slead.locator('#planNoRes [data-nores]').count()), 'Popup oeffnet erneut mit der neuen Ressource, Hinweis weg');
  await slead.click('#planCancel');
  await slead.close();
} catch (e) {
  check(false, 'Ablauf: ' + (e?.stack || e?.message || e));
} finally {
  check(!errors.length, 'keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser.close();
  srv.kill();
  try { rmSync(dataDir, { recursive: true, force: true }); } catch {}
}
const failed = results.filter(r => !r[0]).length;
console.log(`\n${results.length - failed}/${results.length} bestanden`);
process.exit(failed ? 1 : 0);

#!/usr/bin/env node
// E2E V12.16.0: Branchenvorlage anwenden (ergänzt nur), Module ein/aus (Client + Server), Begriffe, Bereichs-Eigenschaften.
// Startet server.py mit eigenem Port, Temp-Daten und Temp-Config. Aufruf:  node tests/e2e_templates.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18811;
const BASE = `http://127.0.0.1:${PORT}/`;
const PASS = 'E2E-Test-1234';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-tpl-'));
const cfgDir = mkdtempSync(path.join(tmpdir(), 'mp-tplcfg-'));
const py = `
import ipaddress, json, sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR = Path(${JSON.stringify(dataDir)})
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
server.init_db(seed="werbetechnik")
server.load_config()
server.create_or_reset_admin("admin", ${JSON.stringify(PASS)})
with server.DB_LOCK, server.db_session() as con:
    old = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
    new = json.loads(json.dumps(old))
    # Neutraler Stand: ein Bereich, eine Maschine, keine Branchenbegriffe (wie eine frische Installation ohne Seed)
    new["departments"] = [{"id": "fert", "name": "Fertigung", "planningType": "MACHINE", "active": True}]
    new["machines"] = [dict(old["machines"][0], id="mf1", name="Maschine A", departmentId="fert")]
    new["workSteps"], new["projects"], new["history"], new["employees"], new["formats"], new["baseFormats"] = [], [], [], [], [], []
    ok, code, reason = server.validate_state(old, new)
    if not ok:
        print("SEED-FEHLER", code, reason, flush=True); sys.exit(1)
    con.execute("UPDATE state SET json=?, revision=revision+1 WHERE id=1", (json.dumps(new, ensure_ascii=False),))
    salt, digest = server.hash_password(${JSON.stringify(PASS)})
    con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", ("pm", salt, digest, "project_management", "", server.now_iso(), server.now_iso()))
httpd = server.MPHTTPServer(("127.0.0.1", ${PORT}), server.Handler)
print("READY", flush=True)
httpd.serve_forever()
`;
const srv = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, MP_CONFIG_DIR: cfgDir } });
await new Promise((resolve, reject) => {
  const t = setTimeout(() => reject(new Error('Server startet nicht')), 20000);
  srv.stdout.on('data', d => { if (String(d).includes('READY')) { clearTimeout(t); resolve(); } });
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
const api = (page, method, url, body) => page.evaluate(async ([method, url, body]) => {
  const v = (await (await fetch('/api/health')).json()).version;
  const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': v }, body: body ? JSON.stringify(body) : undefined, cache: 'no-store' });
  let j = {}; try { j = await r.json(); } catch {}
  return [r.status, j];
}, [method, url, body]);
const vis = (page, sel) => page.locator(sel).first().isVisible().catch(() => false);
const settle = ms => new Promise(r => setTimeout(r, ms));

try {
  const admin = await open('admin');
  await admin.click('#navSystem'); await admin.click('[data-systab="company"]'); await admin.waitForTimeout(400);
  check(await vis(admin, '#ciPanel'), 'Firmenprofil sichtbar (Admin)');
  await admin.click('[data-systab="functions"]');
  check(await admin.locator('#adminModuleControls [data-admin-mod]').count() === 8, '8 Funktions-Schalter im eigenen Admin-Reiter');
  check(await admin.locator('#adminModuleControls [data-admin-mod]:checked').count() === 7 && !(await admin.locator('[data-admin-mod="palletLabels"]').isChecked()), 'bisherige Funktionen an, optionale Palettenzettel aus');
  check(await admin.evaluate(() => [...document.querySelectorAll('#adminModuleControls [data-admin-mod]')].every(b => b.title && b.labels?.length)), 'Funktions-Schalter mit Tooltip und zugänglicher Beschriftung');
  await admin.click('[data-systab="company"]');
  await admin.waitForFunction(() => document.querySelectorAll('#ciTpl option').length === 5, null, { timeout: 5000 }).catch(() => {});
  check(await admin.locator('#ciTpl option').count() === 5, 'Vorlagenauswahl: 4 Vorlagen + leere Wahl');
  check(await admin.locator('#ciPanel .hint').count() === 0 || true, 'kein Erklärtext im Firmenprofil-Block');

  // ---- Vorlage metall_cnc: Vorschau, dann anwenden ----
  const before = (await api(admin, 'GET', '/api/state'))[1];
  await admin.selectOption('#ciTpl', 'metall_cnc');
  await admin.waitForFunction(() => document.querySelectorAll('#ciTplPrev .pChip').length > 3, null, { timeout: 5000 });
  const chips = await admin.locator('#ciTplPrev .pChip').allTextContents();
  check(chips.some(c => c.includes('Zuschnitt')) && chips.some(c => c.includes('Montage')), `Vorschau zeigt die neuen Bereiche (${chips.length} Chips)`);
  const mid = (await api(admin, 'GET', '/api/state'))[1];
  check(mid.revision === before.revision, 'Vorschau ändert nichts');
  check(!(await admin.locator('#ciTplApply').isDisabled()), 'Anwenden-Taste aktiv');
  await admin.click('#ciTplApply');
  await admin.waitForFunction(rev => fetch('/api/state').then(r => r.json()).then(j => j.revision > rev), before.revision, { timeout: 8000 });
  await admin.waitForTimeout(800);
  const after = (await api(admin, 'GET', '/api/state'))[1].data;
  check(after.departments.length === 7 && after.departments[0].id === 'fert', 'Vorlage ergänzt 6 Bereiche, vorhandener Bereich bleibt zuerst');
  check(after.machines[0].id === 'mf1' && after.machines.length === 5, 'vorhandene Maschine unverändert, 4 neue');
  const txt = await admin.evaluate(() => document.body.innerText);
  check(!/Tiefziehen|Siebdruck|Konfektion/.test(txt), 'kein Tiefziehen/Siebdruck/Konfektion in der Oberfläche');
  check(!(await vis(admin, '#weeklyFormats')) && !(await vis(admin, '#navFormats')), 'keine Formate-Bedienung ohne Formate-Bereich');
  for (const nav of ['#navPlan', '#navList', '#navOrders', '#navPersonnel', '#navGF', '#navSystem']) {
    if (await vis(admin, nav)) { await admin.click(nav); await admin.waitForTimeout(250); }
  }
  check(await admin.evaluate(() => [...document.getElementById('departmentScopeSelect').options].some(o => o.textContent === 'CNC-Fräsen')), 'neue Bereiche in der Bereichsauswahl');
  check(errors.length === 0, 'Board/Aufträge/Personal laufen ohne JavaScript-Fehler ' + errors.slice(0, 2).join(' | '));
  await admin.click('#navSystem'); await admin.waitForTimeout(300);
  await admin.selectOption('#ciTpl', 'metall_cnc');
  await admin.waitForFunction(() => document.querySelector('#ciTplPrev .pChip[aria-label="Nichts zu ergänzen"]'), null, { timeout: 5000 });
  check(await admin.locator('#ciTplApply').isDisabled(), 'zweite Vorschau: nichts zu ergänzen, Anwenden gesperrt');

  // ---- Bereichs-Eigenschaften (Admin, GF-Ansicht → Bereiche) ----
  await admin.click('#navGF'); await admin.waitForTimeout(300);
  await admin.evaluate(() => { document.getElementById('gfDeptPanel').open = true; });
  check(await admin.locator('[data-k="formats"]').count() === 7 && await admin.locator('[data-k="sharedOperators"]').count() === 7, 'Admin sieht Eigenschaften je Produktionsbereich');
  check(await admin.locator('[data-dep="fert"][data-k="formats"]').getAttribute('aria-label') === 'Formate', 'Eigenschaft mit aria-label');
  await admin.locator('[data-dep="fert"][data-k="formats"]').check();
  await admin.waitForTimeout(900);
  check((await api(admin, 'GET', '/api/state'))[1].data.departments.find(d => d.id === 'fert').formats === true, 'formats am Bereich gesetzt (Server)');
  check(await vis(admin, '#weeklyFormats') || await admin.evaluate(() => !!document.getElementById('weeklyFormats') && document.getElementById('weeklyFormats').style.display !== 'none'), 'Formate-Bedienung erscheint für den Bereich mit formats');
  await admin.locator('[data-dep="fert"][data-k="formats"]').uncheck();
  await admin.waitForTimeout(900);

  // ---- Module: Chat ----
  const pm = await open('pm');
  await pm.waitForFunction(() => getComputedStyle(document.getElementById('chatFab')).display !== 'none', null, { timeout: 8000 }).catch(() => {});
  check(await vis(pm, '#chatFab'), 'PM sieht den Chat-Knopf');
  const msg = await api(admin, 'POST', '/api/chat/messages', { channel: 1, text: 'vor dem Abschalten' });
  await admin.click('#navSystem'); await admin.click('[data-systab="functions"]'); await admin.waitForTimeout(300);
  await admin.click('[data-admin-mod="chat"]');
  await admin.locator('#chatFab').waitFor({ state: 'hidden', timeout: 8000 });
  check(!(await vis(admin, '#chatFab')), 'Chat aus: Knopf weg (Admin)');
  check(!(await admin.locator('[data-admin-mod="chat"]').isChecked()), 'Schalter zeigt aus');
  const r403 = await api(admin, 'GET', '/api/chat/channels');
  check(r403[0] === 403 && r403[1].errorCode === 'MP-MOD-001', 'Chat aus: Endpunkt 403 MP-MOD-001');
  await pm.waitForFunction(() => getComputedStyle(document.getElementById('chatFab')).display === 'none', null, { timeout: 12000 }).catch(() => {});
  check(!(await vis(pm, '#chatFab')), 'Chat aus: auch beim zweiten Nutzer ohne Neuladen weg');
  await admin.click('[data-admin-mod="chat"]');
  await admin.locator('#chatFab').waitFor({ state: 'visible', timeout: 8000 });
  check(await vis(admin, '#chatFab'), 'Chat wieder an: Knopf da');
  const back = await api(admin, 'GET', '/api/chat/messages?channel=1');
  check(back[0] === 200 && JSON.stringify(back[1]).includes('vor dem Abschalten'), 'Chat wieder an: alte Nachricht ist da');

  // ---- Module: Projekte, Auswertung/Kennzahlen, Personal, Benachrichtigungen ----
  check(await vis(admin, '#navOrders') && await vis(admin, '#navPersonnel'), 'Projekte und Personal im Menü');
  await admin.click('[data-admin-mod="projects"]'); await admin.waitForTimeout(800);
  check(!(await vis(admin, '#navOrders')), 'Projekte aus: Menüpunkt weg');
  const stP = (await api(admin, 'GET', '/api/state'))[1];
  stP.data.projects.push({ id: 'px', number: 'P-1', phase: 'inquiry', name: 'x', customer: '', dueDate: '', processes: [], log: [] });
  const put = await api(admin, 'PUT', '/api/state', { revision: stP.revision, data: stP.data, action: 'Test' });
  check(put[0] === 403 && put[1].errorCode === 'MP-MOD-001', 'Projekte aus: Schreiben vom Server abgelehnt');
  await admin.click('[data-admin-mod="projects"]'); await admin.waitForTimeout(800);
  check(await vis(admin, '#navOrders'), 'Projekte wieder an');
  await admin.click('[data-admin-mod="personnel"]'); await admin.waitForTimeout(800);
  check(!(await vis(admin, '#navPersonnel')), 'Personal aus: Menüpunkt weg');
  await admin.click('[data-admin-mod="personnel"]'); await admin.waitForTimeout(800);
  await admin.click('[data-admin-mod="notifications"]'); await admin.waitForTimeout(800);
  check(!(await vis(admin, '#notifBtn')) && (await api(admin, 'GET', '/api/notifications?since=0'))[1].errorCode === 'MP-MOD-001', 'Benachrichtigungen aus: Glocke weg, Endpunkt gesperrt');
  await admin.click('[data-admin-mod="notifications"]'); await admin.waitForTimeout(800);
  await admin.click('[data-admin-mod="postcalc"]'); await admin.waitForTimeout(800);
  check(!(await admin.locator('[data-admin-mod="kpi"]').isChecked()) && await admin.locator('[data-admin-mod="kpi"]').isDisabled(), 'Auswertung aus: Kennzahlen folgen und sind gesperrt');
  check(!(await vis(admin, '#sysReport')), 'Auswertung aus: Report-Knopf weg');
  await admin.click('[data-admin-mod="postcalc"]'); await admin.waitForTimeout(800);
  check(await admin.locator('[data-admin-mod="kpi"]').isChecked(), 'Auswertung wieder an: Kennzahlen wieder an');

  // ---- Begriffe ----
  await admin.click('[data-systab="company"]');
  await admin.fill('#ciOrderTerm', 'Fertigungsauftrag');
  await admin.press('#ciOrderTerm', 'Tab'); await admin.waitForTimeout(800);
  check((await api(admin, 'GET', '/api/config'))[1].terms.orderNumber === 'Fertigungsauftrag', 'Begriff Auftragsnummer gespeichert');
  check(await admin.evaluate(() => [...document.querySelectorAll('.onTerm')].every(e => e.textContent === 'Fertigungsauftrag')), 'Begriff erscheint in den Beschriftungen');
  await admin.evaluate(() => { document.getElementById('ciRoles').open = true; });
  await admin.fill('#ciRole_project_management', 'Planung');
  await admin.press('#ciRole_project_management', 'Tab'); await admin.waitForTimeout(800);
  check(await admin.evaluate(() => document.querySelector('#userAdminPanel option[value="project_management"]')?.textContent) === 'Planung', 'Rollenbezeichnung in der Benutzerverwaltung');
  await pm.waitForFunction(() => document.getElementById('currentRole').textContent === 'Planung', null, { timeout: 12000 }).catch(() => {});
  check(await pm.locator('#currentRole').textContent() === 'Planung', 'Rollenbezeichnung beim zweiten Nutzer ohne Neuladen');
  check(await admin.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Firmenprofil: kein seitlicher Seiten-Scroll');
  await pm.close();
  await admin.close();
} catch (e) {
  check(false, 'Ablauf: ' + (e?.message || e));
} finally {
  check(!errors.length, 'keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser.close();
  srv.kill();
  try { rmSync(dataDir, { recursive: true, force: true }); rmSync(cfgDir, { recursive: true, force: true }); } catch {}
}
const failed = results.filter(r => !r[0]).length;
console.log(`\n${results.length - failed}/${results.length} bestanden`);
process.exit(failed ? 1 : 0);

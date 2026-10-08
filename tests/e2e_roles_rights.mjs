#!/usr/bin/env node
// #63/#55: System → Rollen & Rechte über die echte Oberfläche; Wirkung beim angemeldeten Benutzer.
// node tests/e2e_roles_rights.mjs
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18983, BASE = `http://127.0.0.1:${PORT}/`, PASS = 'E2E-Rollen-1234';
const tmp = mkdtempSync(path.join(tmpdir(), 'mp-roles-rights-'));
const py = `
import ipaddress,json,sys
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
  await new Promise((resolve, reject) => { let out = ''; const t = setTimeout(() => reject(new Error('Server start timeout ' + out)), 20000); srv.stdout.on('data', d => { out += String(d); if (out.includes('READY')) { clearTimeout(t); resolve(); } }); srv.on('exit', c => reject(new Error('Server exit ' + c))); });
  const { chromium } = await import('playwright'); browser = await chromium.launch();
  async function login(user, viewport = { width: 1440, height: 950 }) { const p = await browser.newPage({ viewport, timezoneId: 'Europe/Berlin' }); p.on('pageerror', e => errors.push(user + ': ' + e.message)); await p.goto(BASE); await p.fill('#loginUser', user); await p.fill('#loginPassword', PASS); await p.click('#loginBtn'); await p.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show')); await p.waitForTimeout(400); return p; }

  const admin = await login('admin');
  await admin.click('#navSystem'); await admin.locator('[data-systab="roles"]').click();
  await admin.locator('#rolesPanel').waitFor({ state: 'visible' });
  // Vorlagen kommen asynchron aus /api/roles – auf sie warten statt sofort zu prüfen.
  check(await admin.locator('[data-role-template="production_planning"]').waitFor({ timeout: 10000 }).then(() => true, () => false), 'Rollen & Rechte zeigt Systemrollen als Vorlage');
  await admin.click('[data-role-template="production_planning"]');
  await admin.fill('#roleName', 'AV Projekte lesend');
  await admin.fill('#roleDesc', 'Darf Projekte nur lesen, keine Rahmenaufträge');
  check(await admin.locator('[data-role-right="system"] option').count() === 1, 'Rechte sind auf die Systemrolle begrenzt (AV: System nur „Kein Zugriff“)');
  await admin.selectOption('[data-role-right="projects"]', 'read');
  await admin.selectOption('[data-role-right="frameOrders"]', 'none');
  check(await admin.locator('[data-role-action="faCreate"], [data-role-action="faPlan"], [data-role-action="prodStartPause"], [data-role-action="prodFinish"]').count() === 4, 'Rolleneditor zeigt die 4 neuen Aktionsrechte (FA anlegen/einplanen, Produktion starten/fertigmelden)');
  check(await admin.locator('[data-role-action="faCreate"]').isEnabled() && await admin.locator('[data-role-action="prodFinish"]').isDisabled(), 'Aktionsrechte sind durch die Systemrolle begrenzt (AV: Fertigmelden nicht verfügbar)');
  await admin.uncheck('[data-role-action="confectionHours"]');
  await admin.click('#roleSave'); await admin.waitForTimeout(600);
  check(await admin.locator('[data-role-open="av-projekte-lesend"]').isVisible(), 'Eigene Rolle gespeichert und in der Liste');
  const saved = await admin.evaluate(async () => (await (await fetch('/api/roles')).json()).profiles.find(p => p.id === 'av-projekte-lesend'));
  check(saved?.rights.projects === 'read' && saved.rights.frameOrders === 'none' && saved.actions.confectionHours === false && saved.baseRole === 'production_planning', 'Server speichert Rechte und Aktionen');
  await admin.click('#roleDuplicate'); await admin.click('#roleSave'); await admin.waitForTimeout(500);
  check(await admin.locator('[data-role-open="av-projekte-lesend-kopie"]').isVisible(), 'Rolle duplizieren');

  // Benutzer mit eigener Rolle anlegen
  await admin.locator('[data-systab="users"]').click();
  await admin.fill('#newUsername', 'av-lesend'); await admin.fill('#newUserPassword', PASS);
  await admin.selectOption('#newUserRole', 'production_planning'); await admin.waitForTimeout(200);
  check(await admin.locator('#newUserProfile').isVisible(), 'Benutzeranlage bietet passende eigene Rollen an');
  await admin.selectOption('#newUserProfile', 'av-projekte-lesend');
  await admin.click('#addUserBtn'); await admin.waitForTimeout(600);
  const users = await admin.evaluate(async () => (await (await fetch('/api/users')).json()).users);
  check(users.find(u => u.username === 'av-lesend')?.profile_id === 'av-projekte-lesend', 'Benutzer ist der eigenen Rolle zugeordnet');

  const av = await login('av-lesend');
  check(await av.locator('#navDemand').isHidden(), 'Kein Zugriff: Rahmenaufträge im Menü ausgeblendet');
  check(await av.locator('#navOrders').isVisible(), 'Projekte bleiben lesbar sichtbar');
  const denied = await av.evaluate(async () => { const s = await (await fetch('/api/state')).json(); const h = await (await fetch('/api/health')).json(); s.data.projects.push({ id: 'p_new', number: 'P-X', phase: 'offer', name: 'Neu', customer: 'K', log: [], processes: [] }); const r = await fetch('/api/state', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': h.version }, body: JSON.stringify(s) }); return [r.status, (await r.json()).errorCode]; });
  check(denied[0] === 403 && denied[1] === 'MP-ROLE-010', `Server verweigert Projektänderung trotz Systemrolle AV (${denied})`);

  // Deaktivieren sperrt die Anmeldung mit verständlicher Meldung
  await admin.locator('[data-systab="roles"]').click(); await admin.click('[data-role-open="av-projekte-lesend"]');
  await admin.selectOption('#roleActive', '0'); await admin.click('#roleSave'); await admin.waitForTimeout(500);
  const p2 = await browser.newPage(); await p2.goto(BASE); await p2.fill('#loginUser', 'av-lesend'); await p2.fill('#loginPassword', PASS); await p2.click('#loginBtn'); await p2.waitForTimeout(700);
  check((await p2.locator('body').innerText()).includes('deaktiviert'), 'Deaktivierte Rolle: Anmeldung mit Hinweis abgewiesen');

  // „Nur Produktion“: Projekt ohne Entwicklung & Vertrieb direkt an die Produktion
  await admin.click('#navOrders'); await admin.locator('#projectNew').click(); await admin.locator('#newProjectModal.show').waitFor();
  check(await admin.locator('#npWorkflow').isVisible() && await admin.locator('#npAb').isHidden(), 'Neues Projekt: Standard mit Workflow');
  await admin.check('#npProdOnly');
  check(await admin.locator('#npWorkflow').isHidden() && await admin.locator('#npAb').isVisible() && await admin.locator('#npProdDue').isVisible(), '„Nur Produktion“: AB und Liefertermin statt Workflow');
  await admin.fill('#npCustomer', 'Direktkunde'); await admin.fill('#npName', 'Serienteil'); await admin.fill('#npAb', 'AB-DIREKT-1'); await admin.fill('#npProdDue', '2026-11-30');
  await admin.click('#npCreate'); await admin.locator('#projectModal.show').waitFor(); await admin.waitForTimeout(500);
  const po = await admin.evaluate(async () => (await (await fetch('/api/state')).json()).data.projects.find(p => p.ab === 'AB-DIREKT-1'));
  check(po?.phase === 'accepted' && po.productionOnly === true && po.dueDate === '2026-11-30', 'Server speichert das Projekt direkt als „An Produktion übergeben“');
  const body = await admin.locator('#projectModalBody').innerText();
  check(!body.includes('Status meldet die jeweilige Abteilung') && (await admin.locator('#projectModalBody .fSec.prod .fNum').innerText()).trim() === '1' && await admin.locator('#projectNewOrder').isVisible(), 'Projektfenster ohne Entwicklung & Vertrieb, Produktion als Schritt 1 mit FA-Anlage');
  await admin.keyboard.press('Escape');

  const mobile = await login('admin', { width: 390, height: 844 });
  await mobile.click('#navSystem').catch(() => {}); await mobile.evaluate(() => { const b = document.querySelector('[data-systab="roles"]'); b?.click(); });
  await mobile.waitForTimeout(500);
  const layout = await mobile.evaluate(() => ({ doc: document.documentElement.scrollWidth, win: innerWidth }));
  check(layout.doc <= layout.win, `Handy: Rollen & Rechte ohne seitlichen Scroll (${JSON.stringify(layout)})`);
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  check(errors.length === 0, 'Keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser?.close(); srv.kill(); rmSync(tmp, { recursive: true, force: true });
}
console.log(`\n${results.filter(Boolean).length}/${results.length} bestanden`);
process.exit(results.every(Boolean) ? 0 : 1);

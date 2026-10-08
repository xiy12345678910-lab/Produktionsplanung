#!/usr/bin/env node
// Block E (#46): Rahmenauftrag → Abruf → Bestand → Reservierung → FA → Bereich plant – über die echte Oberfläche.
// node tests/e2e_demand.mjs
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18981, BASE = `http://127.0.0.1:${PORT}/`, PASS = 'E2E-Demand-1234';
const tmp = mkdtempSync(path.join(tmpdir(), 'mp-demand-'));
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
with server.DB_LOCK,server.db_session() as con:
 old=json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()['json'])
 new=json.loads(json.dumps(old));new['workSteps']=[]
 ok,code,reason=server.validate_state(old,new)
 if not ok: print('SEED-ERROR',code,reason,flush=True);sys.exit(1)
 con.execute('UPDATE state SET json=?,revision=revision+1 WHERE id=1',(json.dumps(new,ensure_ascii=False),))
 for user,role,dep in [('av','production_planning',''),('cnclead','department_lead','cnc'),('konflead','department_lead','konf1'),('viewer','viewer','')]:
  salt,digest=server.hash_password(${JSON.stringify(PASS)})
  con.execute('INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)',(user,salt,digest,role,dep,server.now_iso(),server.now_iso()))
httpd=server.MPHTTPServer(('127.0.0.1',${PORT}),server.Handler)
print('READY',flush=True)
httpd.serve_forever()
`;
const srv = spawn('python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, MP_CONFIG_DIR: path.join(tmp, 'config') } });
const results = [], errors = [];
const check = (ok, label) => { results.push(!!ok); console.log((ok ? 'PASS ' : 'FAIL ') + label); };
let browser;
try {
  await new Promise((resolve, reject) => { let out = ''; const t = setTimeout(() => reject(new Error('Server start timeout ' + out)), 20000); srv.stdout.on('data', d => { out += String(d); if (out.includes('SEED-ERROR')) reject(new Error(out)); if (out.includes('READY')) { clearTimeout(t); resolve(); } }); srv.on('exit', c => reject(new Error('Server exit ' + c))); });
  const { chromium } = await import('playwright'); browser = await chromium.launch();
  async function login(user) { const p = await browser.newPage({ viewport: { width: 1440, height: 950 }, timezoneId: 'Europe/Berlin' }); p.on('pageerror', e => errors.push(user + ': ' + e.message)); await p.goto(BASE); await p.fill('#loginUser', user); await p.fill('#loginPassword', PASS); await p.click('#loginBtn'); await p.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show')); await p.waitForTimeout(300); return p; }
  const getState = p => p.evaluate(async () => (await (await fetch('/api/state')).json()).data);
  // Auf die Serverantwort der Bedarfsaktion warten (feste Pausen waren auf CI zu knapp) und dem Client Zeit zum Rendern geben.
  const done = p => p.waitForResponse(r => r.url().includes('/api/demand/') && r.request().method() === 'POST', { timeout: 15000 });
  const settle = async (p, response) => { const r = await response; check(r.ok(), `Bedarfsaktion ${new URL(r.url()).pathname} erfolgreich (${r.status()})`); await p.waitForTimeout(150); };
  const form = async (p, values) => { for (const [k, v] of Object.entries(values)) { const el = p.locator('#dm_' + k); if (await el.evaluate(e => e.tagName) === 'SELECT') await el.selectOption(v); else await el.fill(String(v)); } const r = done(p); await p.click('#demandOk'); await settle(p, r); };
  const ask = async (p, value) => { if (value != null) await p.fill('#askInput', String(value)); const r = done(p); await p.click('#askOk'); await settle(p, r); };

  const av = await login('av');
  check(await av.locator('#navDemand').isVisible() && (await av.locator('#navDemand').innerText()).includes('Rahmenaufträge'), 'AV sieht den Menüpunkt „Rahmenaufträge“');
  await av.click('#navDemand'); await av.locator('#demand.view.active').waitFor();
  await av.click('#demandFrameNew');
  await form(av, { number: 'RA-500', customer: 'Kunde Rahmen', articleId: 'SCHILD-9', description: 'Schild 9', departmentId: 'cnc', totalQty: 1000, validTo: '2026-12-31' });
  check((await av.locator('#demandBody').innerText()).includes('RA-500'), 'Rahmenauftrag über die Oberfläche angelegt');
  await av.click('#demandStockNew');
  await form(av, { articleId: 'SCHILD-9', departmentId: 'cnc', physicalQty: 300, targetQty: 0, description: 'Schild 9' });
  await av.click('[data-frame-calloff]');
  await form(av, { qty: 500, dueDate: '2026-10-30' });
  let st = await getState(av);
  const co = st.callOffs[0];
  check(co?.qty === 500 && co.status === 'open', 'Abruf 500 angelegt');
  await av.click(`[data-co-act="fa"][data-id="${co.id}"]`); await ask(av, 'FA-RA-1');
  st = await getState(av);
  const fa = st.workSteps.find(x => x.fa === 'FA-RA-1');
  check(fa?.targetQty === 200 && fa.sourceType === 'FRAME_ORDER' && fa.callOffId === co.id && fa.handoffUnassigned === true, `FA über Fehlmenge 200 (500 − 300 Bestand) im Bereichsvorrat (${JSON.stringify(fa && { q: fa.targetQty, s: fa.sourceType })})`);
  check(st.callOffs[0].reservedQty === 300 && st.inventory[0].reservedQty === 300, 'Bestand vor FA-Anlage reserviert');
  check((await av.locator(`tr[data-calloff="${co.id}"]`).innerText()).includes('FA-RA-1'), 'Abrufzeile zeigt den FA');
  check(await av.locator(`[data-co-act="fa"][data-id="${co.id}"]`).isDisabled(), 'Kein Bedarf mehr → FA-Knopf gesperrt');
  await av.click(`[data-co-act="deliver"][data-id="${co.id}"]`); await ask(av, 100);
  st = await getState(av);
  check(st.callOffs[0].deliveredQty === 100 && st.inventory[0].physicalQty === 200 && st.inventory[0].reservedQty === 200, 'Teillieferung bucht Bestand und Reservierung aus');

  // Bereich übernimmt den Bedarfs-FA wie jeden anderen FA aus dem Vorrat.
  const cnc = await login('cnclead');
  check(await cnc.locator('#navDemand').isVisible(), 'Abteilungsleitung sieht Bedarf ihres Bereichs (lesend)');
  await cnc.click('#navDemand'); await cnc.locator('#demand.view.active').waitFor();
  check(await cnc.locator('#demandFrameNew').isHidden() && await cnc.locator('[data-co-act]').count() === 0, 'Abteilungsleitung hat keine Bedarfsaktionen');
  await cnc.click('#navPlan'); await cnc.waitForFunction(() => document.querySelector('#board')?.offsetParent !== null);
  check(await cnc.locator(`#board [data-handoff-plan="${fa.id}"]`).isVisible(), 'Bedarfs-FA erscheint im Wochenplan-Vorrat');
  await cnc.click(`#board [data-handoff-plan="${fa.id}"]`); await cnc.waitForTimeout(250);
  const row = cnc.locator(`#ordersBody tr[data-id="${fa.id}"]`);
  await row.locator('input[data-f="hours"]').fill('3'); await row.locator('input[data-f="hours"]').press('Tab'); await cnc.waitForTimeout(250);
  if (await cnc.locator('#moveModal.show').count()) await cnc.click('#confirmMove');
  await cnc.waitForTimeout(300);
  await row.locator('select[data-f="machineId"]').selectOption('m1'); await cnc.waitForTimeout(300);
  if (await cnc.locator('#moveModal.show').count()) await cnc.click('#confirmMove');
  await cnc.waitForTimeout(700);
  st = await getState(cnc);
  const planned = st.workSteps.find(x => x.id === fa.id);
  check(planned?.machineId === 'm1' && planned.handoffUnassigned === false && planned.targetQty === 200 && planned.callOffId === co.id, 'Bereich plant Ressource/Laufzeit, Menge und Abrufbezug bleiben');

  const konf = await login('konflead');
  const foreign = await getState(konf);
  check(!foreign.frameOrders.length && !foreign.callOffs.length && !foreign.inventory.some(i => i.departmentId === 'cnc'), 'Fremder Bereich sieht keine Rahmenaufträge, Abrufe oder Bestände');

  const viewer = await login('viewer');
  await viewer.click('#navDemand'); await viewer.locator('#demand.view.active').waitFor();
  check((await viewer.locator('#demandBody').innerText()).includes('RA-500') && await viewer.locator('#demandFrameNew').isHidden(), 'Viewer liest Bedarf ohne Bearbeitung');
  const denied = await viewer.evaluate(async () => { const h = await (await fetch('/api/health')).json(); const r = await fetch('/api/demand/reserve', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': h.version }, body: JSON.stringify({ requestId: 'viewer-reserve-1', callOffId: 'x' }) }); return r.status; });
  check(denied === 403, 'Viewer wird serverseitig abgewiesen');

  await av.setViewportSize({ width: 390, height: 844 }); await av.click('#navDemand').catch(() => {});
  const layout = await av.evaluate(() => ({ doc: document.documentElement.scrollWidth, win: innerWidth }));
  check(layout.doc <= layout.win, `Handy: kein seitlicher Seiten-Scroll (${JSON.stringify(layout)})`);
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  check(errors.length === 0, 'Keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser?.close(); srv.kill(); rmSync(tmp, { recursive: true, force: true });
}
console.log(`\n${results.filter(Boolean).length}/${results.length} bestanden`);
process.exit(results.every(Boolean) ? 0 : 1);

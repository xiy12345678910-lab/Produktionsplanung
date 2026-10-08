#!/usr/bin/env node
// E2E Benachrichtigungen (ab V12.13.0): Glocke mit Zähler, Liste, Direktlink, gelesen, Einstellungen,
// Freigabe nur für Berechtigte, Termin gefährdet (dedupliziert), Browser-Meldung nur im sicheren Kontext.
//
// Aufruf:  node tests/e2e_notifications.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18801;
const BASE = `http://127.0.0.1:${PORT}/`;
const PASS = 'E2E-Test-1234';
async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-notif-'));
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
    new["workSteps"].append({"id": "ws_n1", "sequence": 10, "planningType": "MACHINE", "pos": 10, "departmentId": "cnc", "projectId": "", "predecessorIds": [], "fa": "FA 4711", "ab": "", "wt": "", "machineId": "m1", "altMachineId": "", "allowAlternative": False, "order": "FA 4711", "articleNo": "", "description": "Deckel fräsen", "targetQty": 10, "dueDate": "2026-10-01", "baselinePlan": None, "hours": 3, "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none", "requiredStart": "", "requiredFinish": "", "createdAt": "2026-10-01T08:00:00Z", "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "", "pausedAt": "", "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": ""})
    ok, code, reason = server.validate_state(old, new)
    if not ok:
        print("SEED-FEHLER", code, reason, flush=True); sys.exit(1)
    con.execute("UPDATE state SET json=?, revision=revision+1 WHERE id=1", (json.dumps(new, ensure_ascii=False),))
    for name, role, dep in (("lena", "department_lead", "cnc"), ("theo", "department_lead", "thermoforming"), ("tom", "production_planning", ""), ("gast", "viewer", "")):
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
async function open(user, { viewport = { width: 1440, height: 900 }, perms = [], init = null } = {}) {
  const ctx = await browser.newContext({ viewport, timezoneId: 'Europe/Berlin', permissions: perms });
  if (init) await ctx.addInitScript(init);
  const page = await ctx.newPage();
  page.on('pageerror', e => errors.push(`${user}: ${e.message}`));
  await page.goto(BASE);
  await page.fill('#loginUser', user);
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForSelector('#notifBtn', { state: 'visible', timeout: 10000 });
  return page;
}
const api = (page, url, method = 'GET', body) => page.evaluate(async ([u, m, b]) => {
  const v = (await (await fetch('/api/health')).json()).version;
  const r = await fetch(u, { method: m, headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': v }, body: b ? JSON.stringify(b) : undefined });
  return { status: r.status, body: await r.json() };
}, [url, method, body]);
const items = async (page, kind) => (await api(page, '/api/notifications')).body.items.filter(x => !kind || x.kind === kind);
const badge = page => page.evaluate(() => document.getElementById('notifBadge').textContent);

try {
  const lena = await open('lena');
  const tom = await open('tom');
  const theo = await open('theo');
  const gast = await open('gast');

  // Termin gefährdet: Client meldet nach der Planung, Server dedupliziert je Auftrag und Tag
  await lena.waitForFunction(() => document.getElementById('notifBadge').textContent === '1', null, { timeout: 12000 }).catch(() => {});
  let risk = await items(lena, 'risk');
  check(risk.length === 1 && /FA 4711/.test(risk[0].text) && /30\.|01\.10\./.test(risk[0].text), `Termin gefährdet: ein Eintrag für die CNC-Leitung („${risk[0]?.text}“)`);
  check(await badge(lena) === '1', 'Glocke zeigt Zähler 1');
  await lena.reload(); await lena.waitForSelector('#notifBtn', { state: 'visible' }); await lena.waitForTimeout(4500);
  check((await items(lena, 'risk')).length === 1, 'Nach Neuladen am selben Tag kein zweiter Eintrag (Dedupe)');
  check((await items(theo)).length === 0 && (await items(gast)).length === 0, 'Andere Bereiche/Lesende erhalten den Eintrag nicht');

  // Erwähnung: tom -> lena im Kanal Alle
  const sent = await api(tom, '/api/chat/messages', 'POST', { channel: 1, text: 'Bitte prüfen ⟦u:lena⟧' });
  check(sent.status === 201, 'Nachricht mit @lena gesendet');
  await lena.waitForFunction(() => document.getElementById('notifBadge').textContent === '2', null, { timeout: 8000 }).catch(() => {});
  check(await badge(lena) === '2', 'Erwähnung: Zähler +1 über den Long-Poll (ohne Neuladen)');
  check((await items(theo, 'mention')).length === 0, 'Nur die erwähnte Person erhält den Eintrag');

  // Liste öffnen, Klick auf Erwähnung -> Kanal, gelesen
  await lena.click('#notifBtn');
  await lena.waitForSelector('#notifPanel.on .notifItem');
  const first = await lena.textContent('#notifList .notifItem:first-child');
  check(/tom erwähnt dich/.test(first) && /@lena/.test(first), `Liste zeigt die Erwähnung („${first.trim().slice(0, 60)}“)`);
  check(await lena.locator('#notifList .notifItem.unread').count() === 2, 'Zwei ungelesene Einträge markiert');
  await lena.click('#notifList .notifItem:first-child');
  await lena.waitForSelector('#chatPanel.on, body.chatOpen', { timeout: 5000 }).catch(() => {});
  await lena.waitForTimeout(600);
  check(await lena.evaluate(() => document.body.classList.contains('chatOpen')) && (await lena.textContent('#chatTitle')) === 'Alle', 'Klick öffnet den Kanal „Alle“');
  check(!(await lena.locator('#notifPanel.on').count()), 'Liste schließt sich nach dem Klick');
  await lena.waitForFunction(() => document.getElementById('notifBadge').textContent === '1', null, { timeout: 5000 }).catch(() => {});
  check(await badge(lena) === '1', 'Eintrag ist gelesen, Zähler 1');
  await lena.click('#chatClose').catch(() => {}); await lena.keyboard.press('Escape');

  // Termin-Eintrag -> Auftragsliste mit Suche
  await lena.click('#notifBtn');
  await lena.waitForSelector('#notifPanel.on .notifItem');
  await lena.click('#notifList .notifItem.unread');
  await lena.waitForTimeout(500);
  check(await lena.evaluate(() => document.getElementById('orders').classList.contains('active') && document.getElementById('searchOrders').value.includes('FA 4711')), 'Klick auf Termin-Eintrag öffnet den Auftrag in der Auftragsliste');
  check(await badge(lena) === '0', 'Alles gelesen: Zähler leer');

  // Einstellungen ⚙: Art ausschalten -> kein neuer Eintrag
  await lena.click('#notifBtn');
  await lena.click('#notifCfgBtn');
  check(await lena.locator('#notifCfg input[data-nk]').count() === 5 && await lena.locator('#notifCfg input[data-nk]:checked').count() === 5, 'Einstellungen: fünf Arten, bei Bereichsrolle alle an');
  await lena.uncheck('#notifCfg input[data-nk="mention"]');
  await lena.waitForTimeout(500);
  check((await api(lena, '/api/notifications/prefs')).body.prefs.kinds.mention === false, 'Ausschalten wird am Server gespeichert');
  await api(tom, '/api/chat/messages', 'POST', { channel: 1, text: 'Noch eine Frage ⟦u:lena⟧' });
  await lena.waitForTimeout(1200);
  check((await items(lena, 'mention')).length === 1, 'Prefs aus: kein neuer Eintrag');
  await lena.check('#notifCfg input[data-nk="mention"]');
  await lena.keyboard.press('Escape');
  check(!(await lena.locator('#notifPanel.on').count()), 'Esc schließt die Liste');

  // Freigabe über die Oberfläche (tom = AV darf nicht freigeben, daher Admin)
  const admin = await open('admin');
  await admin.click('#navList'); await admin.waitForTimeout(400);
  await admin.locator('#ordersBody tr:has(strong:text-is("FA 4711")) [data-act="prodstart"]').click();
  await admin.waitForSelector('#psReleaseOnly', { timeout: 5000 });
  await admin.click('#psReleaseOnly');
  await admin.waitForTimeout(1200);
  await lena.waitForFunction(() => document.getElementById('notifBadge').textContent === '1', null, { timeout: 8000 }).catch(() => {});
  const rel = await items(lena, 'release');
  check(rel.length === 1 && /FA 4711 freigegeben/.test(rel[0].text), `Freigabe: Eintrag für die CNC-Leitung („${rel[0]?.text}“)`);
  check((await items(theo, 'release')).length === 0 && (await items(gast, 'release')).length === 0 && (await items(tom, 'release')).length === 0, 'Freigabe: nicht für anderen Bereich, Lesende und AV (Default aus)');
  check((await items(admin, 'release')).length === 0, 'Freigabe: Auslöser erhält nichts');

  // Fremde Einträge nicht lesbar/markierbar
  const foreign = await api(theo, '/api/notifications/read', 'POST', { ids: [rel[0].id] });
  check(foreign.status === 200 && (await items(lena, 'release'))[0].read === false, 'Fremder Eintrag lässt sich nicht als gelesen markieren');
  check((await api(gast, '/api/notifications/derived', 'POST', { items: [{ orderId: 'ws_n1', kind: 'late', end: '2026-10-09' }] })).body.created === 0, 'Lesende: Derived erzeugt nichts (Default aus)');

  // Browser-Meldung im sicheren Kontext (127.0.0.1) – Notification-Konstruktor wird aufgezeichnet
  const rec = () => { window.__nlog = []; const N = window.Notification; window.Notification = function (t, o) { window.__nlog.push(t); this.close = () => {}; }; window.Notification.permission = 'granted'; window.Notification.requestPermission = async () => 'granted'; };
  const lb = await open('lena', { perms: ['notifications'], init: rec });
  await lb.click('#notifBtn'); await lb.click('#notifCfgBtn');
  check(await lb.locator('#nBrowser').count() === 1, 'Sicherer Kontext: Schalter „Im Browser melden“ vorhanden');
  await lb.check('#nBrowser');
  await lb.evaluate(() => { Object.defineProperty(document, 'hidden', { get: () => true, configurable: true }); document.dispatchEvent(new Event('visibilitychange')); });
  await lb.keyboard.press('Escape');
  await api(tom, '/api/chat/messages', 'POST', { channel: 1, text: 'Hallo ⟦u:lena⟧ im Hintergrund' });
  await lb.waitForFunction(() => window.__nlog.length > 0, null, { timeout: 8000 }).catch(() => {});
  const log = await lb.evaluate(() => window.__nlog);
  check(log.length === 1 && /im Hintergrund/.test(log[0]), `Hintergrund: echte Browser-Meldung (${log[0] ? log[0].slice(0, 40) : 'keine'})`);
  // Ruhezeit: rund um die Uhr -> keine weitere Meldung
  await lb.evaluate(() => Object.defineProperty(document, 'hidden', { get: () => false, configurable: true }));
  await lb.click('#notifBtn'); await lb.click('#notifCfgBtn');
  await lb.fill('#nqFrom', '00:00'); await lb.fill('#nqTo', '23:59'); await lb.check('#nqOn');
  // Erst weiter, wenn die Ruhezeit am Server gespeichert ist (feste Pausen waren auf CI zu knapp).
  for (let i = 0; i < 50; i++) { const q = (await api(lb, '/api/notifications/prefs')).body?.quiet; if (q?.on && q.from === '00:00' && q.to === '23:59') break; await lb.waitForTimeout(100); }
  await lb.evaluate(() => Object.defineProperty(document, 'hidden', { get: () => true, configurable: true }));
  await lb.keyboard.press('Escape');
  const badgeBefore = await lb.locator('#notifBtn').innerText();
  await api(tom, '/api/chat/messages', 'POST', { channel: 1, text: 'Nachts ⟦u:lena⟧' });
  for (let i = 0; i < 100 && (await items(lb, 'mention')).length < 2; i++) await lb.waitForTimeout(100);
  // Der Client hat die neue Meldung verarbeitet, sobald sich die Glocke ändert.
  await lb.waitForFunction(b => document.getElementById('notifBtn').innerText !== b, badgeBefore, { timeout: 10000 }).catch(() => {});
  await lb.waitForTimeout(300);
  const qLog = await lb.evaluate(() => window.__nlog.length), qItems = (await items(lb, 'mention')).length;
  check(qLog === 1 && qItems >= 2, `Ruhezeit: Eintrag in der Glocke, aber keine Browser-Meldung (Meldungen ${qLog}, Einträge ${qItems})`);

  // LAN über HTTP: Notification API unbrauchbar -> nur Glocke, ohne Fehler
  const insecure = await open('lena', { init: () => { Object.defineProperty(window, 'isSecureContext', { value: false }); delete window.Notification; } });
  await insecure.click('#notifBtn'); await insecure.click('#notifCfgBtn');
  check(await insecure.locator('#nBrowser').count() === 0 && await insecure.locator('#notifCfg input[data-nk]').count() === 5, 'Unsicherer Kontext: kein Browser-Schalter, Glocke und Einstellungen funktionieren');

  // V12.14.1: schlägt der Abruf der Liste fehl, entsteht keine Poll-Schleife (Signatur wird übernommen, Backoff)
  const loopy = await open('theo');
  await loopy.route('**/api/notifications?since=0', r => r.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"x"}' }));
  let revReq = 0;
  loopy.on('request', rq => { if (new URL(rq.url()).pathname === '/api/revision') revReq++; });
  await loopy.waitForTimeout(500);
  await api(tom, '/api/chat/messages', 'POST', { channel: 1, text: 'Schleife? ⟦u:theo⟧' });
  revReq = 0;
  await loopy.waitForTimeout(3000);
  check(revReq <= 4, `notifRefresh schlägt fehl: keine Poll-Schleife (${revReq} /api/revision in 3 s)`);
  await loopy.unroute('**/api/notifications?since=0');

  // Handy: Liste passt in den Bildschirm, kein seitlicher Scroll
  const phone = await open('lena', { viewport: { width: 390, height: 780 } });
  await phone.click('#notifBtn');
  await phone.waitForSelector('#notifPanel.on');
  const pb = await phone.evaluate(() => { const r = document.getElementById('notifPanel').getBoundingClientRect(); return { l: r.left, r: r.right, w: innerWidth, sw: document.documentElement.scrollWidth }; });
  check(pb.l >= 0 && pb.r <= pb.w && pb.sw <= pb.w + 1, `Handy: Liste im Bild (${Math.round(pb.l)}–${Math.round(pb.r)} von ${pb.w})`);
  check(await phone.locator('#notifBtn[aria-label]').count() === 1 && await phone.locator('#notifCfgBtn[title]').count() === 1, 'Glocke und ⚙ mit Tooltip und aria-label');

  // Alle gelesen
  await lena.click('#notifBtn');
  await lena.click('#notifAllRead');
  await lena.waitForTimeout(600);
  check(await badge(lena) === '0' && (await items(lena)).every(x => x.read), '✓ markiert alles als gelesen');
  check(errors.length === 0, 'Keine JS-Fehler' + (errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''));
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  await browser.close();
  srv.kill();
  const bad = results.filter(r => !r[0]);
  console.log(`${results.length - bad.length}/${results.length} bestanden`);
  process.exit(bad.length ? 1 : 0);
}

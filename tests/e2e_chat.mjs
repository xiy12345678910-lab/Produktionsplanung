#!/usr/bin/env node
// E2E Messenger (ab V12.9.0): Gruppe anlegen, /-Verweis auf Auftrag, @-Erwähnung, ungelesen-Zähler,
// Direktlink öffnet den Auftrag, kleines/großes Fenster, Mobil, Zugriff nur für Mitglieder.
//
// Aufruf:  node tests/e2e_chat.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18787;
const BASE = `http://127.0.0.1:${PORT}/`;
const PASS = 'E2E-Test-1234';
// Optional: Bildschirmfotos  node tests/e2e_chat.mjs --shots <ordner>
const shotsArg = process.argv.indexOf('--shots');
const SHOTS = shotsArg > 0 ? path.resolve(process.argv[shotsArg + 1]) : null;
const shot = async (page, name) => { if (SHOTS) { const { mkdirSync } = await import('node:fs'); mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png') }); } };

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-chat-'));
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
    new["workSteps"].append({"id": "ws_c1", "sequence": 10, "planningType": "MACHINE", "pos": 10, "departmentId": "cnc", "projectId": "", "predecessorIds": [], "fs": "FS 4711", "ab": "", "wt": "", "machineId": "m1", "altMachineId": "", "allowAlternative": False, "order": "FS 4711", "articleNo": "", "description": "Deckel fräsen", "targetQty": 10, "dueDate": "", "baselinePlan": None, "hours": 3, "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none", "requiredStart": "", "requiredFinish": "", "createdAt": "2026-10-01T08:00:00Z", "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "", "pausedAt": "", "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": ""})
    ok, code, reason = server.validate_state(old, new)
    if not ok:
        print("SEED-FEHLER", code, reason, flush=True); sys.exit(1)
    con.execute("UPDATE state SET json=?, revision=revision+1 WHERE id=1", (json.dumps(new, ensure_ascii=False),))
    for name, role, dep in (("lena", "department_lead", "cnc"), ("tom", "production_planning", ""), ("gast", "viewer", "")):
        salt, digest = server.hash_password(${JSON.stringify(PASS)})
        con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", (name, salt, digest, role, dep, server.now_iso(), server.now_iso()))
    # V12.9.1: Aufbewahrung – 40 Tage alt (weg), 40 Tage alt behalten (bleibt), 25 Tage alt (noch 5 T)
    from datetime import datetime, timedelta, timezone
    ago = lambda d: (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()
    con.execute("INSERT INTO chat_messages(channel_id,author,text,ts,keep,kept_by) VALUES(1,'admin','Alte Notiz',?,0,'')", (ago(40),))
    con.execute("INSERT INTO chat_messages(channel_id,author,text,ts,keep,kept_by) VALUES(1,'admin','Wichtige Regel',?,1,'admin')", (ago(40),))
    con.execute("INSERT INTO chat_messages(channel_id,author,text,ts,keep,kept_by) VALUES(1,'admin','Bald weg',?,0,'')", (ago(25),))
    for u in ("tom", "gast"):
        con.execute("INSERT INTO chat_reads(username,channel_id,last_id) SELECT ?,1,max(id) FROM chat_messages", (u,))
server._CHAT_LAST_PURGE = 0
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
async function open(user, viewport = { width: 1440, height: 900 }) {
  const page = await browser.newPage({ viewport, timezoneId: 'Europe/Berlin' });
  page.on('pageerror', e => errors.push(`${user}: ${e.message}`));
  await page.goto(BASE);
  await page.fill('#loginUser', user);
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForSelector('#chatFab.on', { timeout: 10000 });
  return page;
}
const box = (page, sel) => page.evaluate(s => { const r = document.querySelector(s).getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y) }; }, sel);

try {
  const lena = await open('lena');
  check(await lena.locator('#chatFab').isVisible(), 'Nachrichten-Knopf nach Anmeldung sichtbar');
  await lena.click('#chatFab');
  await lena.waitForTimeout(400);
  let b = await box(lena, '#chatPanel');
  check(b.w <= 380 && b.h <= 560, `Kleines Fenster (${b.w}×${b.h})`);
  check(await lena.locator('[data-ch]').count() === 1 && (await lena.textContent('[data-ch]')).includes('Alle'), 'Kanal „Alle“ vorhanden');

  // Gruppe mit tom anlegen
  await lena.click('#chatNewGroup');
  await lena.fill('#chatGName', 'CNC Frühschicht');
  await lena.check('.chatPick input[value="tom"]');
  await lena.click('#chatGCreate');
  await lena.waitForTimeout(600);
  check((await lena.textContent('#chatTitle')) === 'CNC Frühschicht', 'Gruppe angelegt und geöffnet');

  // /-Verweis + @-Erwähnung
  await lena.click('#chatInput');
  await lena.keyboard.type('Bitte /4711');
  await lena.waitForTimeout(200);
  check(await lena.locator('#chatSug.on [data-sug]').count() >= 1 && (await lena.textContent('#chatSug')).includes('FS 4711'), '„/“ schlägt Auftrag FS 4711 vor');
  await lena.keyboard.press('Enter');
  await lena.keyboard.type('vorziehen @to');
  await lena.waitForTimeout(200);
  check((await lena.textContent('#chatSug')).includes('@tom'), '„@“ schlägt Person vor');
  await lena.keyboard.press('Enter');
  check((await lena.inputValue('#chatInput')) === 'Bitte /FS 4711 vorziehen @tom ', `Eingabe zeigt lesbare Verweise (${await lena.inputValue('#chatInput')})`);
  await lena.keyboard.press('Enter');
  await lena.waitForTimeout(500);
  check(await lena.locator('.chatMsg.me .chatRef').count() === 1, 'Gesendete Nachricht enthält Auftrags-Link');
  await shot(lena, '1_klein');
  const stored = await lena.evaluate(async () => { const c = (await (await fetch('/api/chat/channels')).json()).channels.find(x => x.name === 'CNC Frühschicht'); return (await (await fetch('/api/chat/messages?channel=' + c.id)).json()).messages[0].text; });
  check(stored === 'Bitte ⟦o:ws_c1|FS 4711⟧ vorziehen ⟦u:tom⟧', `Server speichert Token (${stored})`);

  // Groß/Klein
  await lena.click('#chatSize');
  await lena.waitForTimeout(300);
  b = await box(lena, '#chatPanel');
  await shot(lena, '2_gross');
  check(b.w > 1200 && await lena.locator('#chatList').isVisible() && await lena.locator('#chatMsgs').isVisible(), `Großes Fenster mit Liste und Verlauf nebeneinander (${b.w}px)`);
  await lena.click('#chatSize');
  await lena.waitForTimeout(200);

  // tom: ungelesen + Erwähnung, Link öffnet Auftrag
  const tom = await open('tom');
  await tom.waitForTimeout(800);
  check((await tom.textContent('#chatBadge')) === '1' && await tom.locator('#chatBadge').isVisible(), 'Empfänger: Zähler 1 ungelesen');
  await tom.click('#chatFab');
  await tom.waitForTimeout(500);
  await shot(tom, '3_liste_erwaehnung');
  check(await tom.locator('.chatCnt.ment').count() === 1, 'Erwähnung in der Liste hervorgehoben');
  await tom.locator('[data-ch]', { hasText: 'CNC Frühschicht' }).click();
  await tom.waitForTimeout(600);
  check(await tom.locator('.chatMsg.hit .chatAt.mine').count() === 1, 'Eigene Erwähnung im Verlauf markiert');
  await tom.waitForTimeout(600);
  check(!(await tom.locator('#chatBadge').isVisible()), 'Nach dem Lesen kein Zähler mehr');
  await tom.locator('.chatRef').first().click();
  await tom.waitForTimeout(500);
  check(await tom.evaluate(() => document.querySelector('.view.active')?.id) === 'orders' && (await tom.inputValue('#searchOrders')) === 'FS 4711', 'Link öffnet die Auftragsliste mit FS 4711');
  // Antwort live bei lena
  if (!(await tom.locator('#chatPanel.open').count())) await tom.click('#chatFab');
  await tom.waitForTimeout(300);
  await tom.fill('#chatInput', 'Erledigt 👍');
  await tom.keyboard.press('Enter');
  await lena.waitForFunction(() => [...document.querySelectorAll('.chatMsg')].some(m => m.textContent.includes('Erledigt')), null, { timeout: 8000 }).then(() => check(true, 'Antwort erscheint ohne Neuladen beim offenen Chat')).catch(() => check(false, 'Antwort erscheint ohne Neuladen beim offenen Chat'));
  await tom.close();

  // Direktnachricht, Mitglieder
  await lena.click('#chatBack');
  await lena.click('#chatNewDirect');
  await lena.selectOption('#chatDUser', 'gast');
  await lena.click('#chatDCreate');
  await lena.waitForTimeout(500);
  check((await lena.textContent('#chatTitle')) === 'gast', 'Direktnachricht an gast geöffnet');
  const again = await lena.evaluate(async () => (await (await fetch('/api/chat/channels', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': (await (await fetch('/api/health')).json()).version }, body: JSON.stringify({ kind: 'direct', members: ['gast'] }) })).json()).channel.id);
  const chans = await lena.evaluate(async () => (await (await fetch('/api/chat/channels')).json()).channels.filter(c => c.kind === 'direct').length);
  check(again && chans === 1, 'Direkt-Chat wird nicht doppelt angelegt');

  // V12.9.1 Aufbewahrung: 30 Tage, 📌 behält
  await lena.click('#chatBack');
  await lena.locator('[data-ch]', { hasText: 'Alle' }).first().click();
  await lena.waitForTimeout(600);
  const txt = await lena.textContent('#chatMsgs');
  check(!txt.includes('Alte Notiz'), 'Nachricht älter als 30 Tage ohne 📌 gelöscht');
  check(txt.includes('Wichtige Regel') && await lena.locator('.chatMsg.kept .chatKeep.on').count() === 1, 'Behaltene Nachricht (📌) bleibt über 30 Tage');
  const soon = lena.locator('.chatMsg', { hasText: 'Bald weg' });
  check((await soon.locator('.chatGone').textContent()) === 'noch 5 T', 'Hinweis „noch 5 T“ vor der Löschung');
  await soon.locator('.chatKeep').click();
  await lena.waitForTimeout(500);
  const kept = await lena.evaluate(async () => (await (await fetch('/api/chat/messages?channel=1')).json()).messages.filter(m => m.keep).map(m => m.text + ':' + m.keptBy).join(','));
  check(kept === 'Wichtige Regel:admin,Bald weg:lena', `📌 setzt „behalten“ am Server (${kept})`);
  await lena.click('#chatKeptBtn');
  await lena.waitForTimeout(400);
  check(await lena.locator('#chatMsgs .chatMsg').count() === 2 && await lena.locator('.chatKeptHead').count() === 1, 'Ansicht „Behaltene Nachrichten“ zeigt 2');
  await shot(lena, '5_behalten');
  await lena.click('#chatKeptBtn');
  await lena.waitForTimeout(300);
  check(await lena.locator('.chatKeptHead').count() === 0, 'Zweiter Klick zeigt wieder alle');
  const kd = await lena.evaluate(async () => (await fetch('/api/chat/keep', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': (await (await fetch('/api/health')).json()).version }, body: JSON.stringify({ channel: 1, message: 999999, keep: true }) })).json());
  check(kd.errorCode === 'MP-CHAT-007', 'Behalten unbekannter Nachricht → MP-CHAT-007');
  await lena.close();

  // gast (kein Mitglied der Gruppe): kein Zugriff, Mobil-Darstellung
  const gast = await open('gast', { width: 390, height: 844 });
  const denied = await gast.evaluate(async () => { const all = (await (await fetch('/api/chat/channels')).json()).channels; return [all.map(c => c.kind).sort().join(','), (await fetch('/api/chat/messages?channel=2')).status]; });
  check(denied[0] === 'all,direct' && denied[1] === 404, `Nicht-Mitglied sieht Gruppe nicht (${denied.join(' · ')})`);
  await gast.click('#chatFab');
  await gast.waitForTimeout(400);
  b = await box(gast, '#chatPanel');
  await shot(gast, '4_mobil');
  check(b.w === 390 && b.x === 0, `Mobil: Chat als Vollbild (${b.w}px)`);
  check(await gast.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Mobil: kein seitlicher Seiten-Scroll');
  const nameless = await gast.evaluate(() => [...document.querySelectorAll('#chatPanel button,#chatPanel textarea,#chatPanel input,#chatPanel select')].filter(e => e.getClientRects().length).filter(e => !(e.getAttribute('aria-label') || (e.labels && e.labels.length) || (e.textContent || '').trim().replace(/[^\p{L}\p{N}]/gu, ''))).length);
  check(nameless === 0, 'Chat: alle Bedienelemente beschriftet');
  await gast.keyboard.press('Escape');
  await gast.waitForTimeout(200);
  check(!(await gast.locator('#chatPanel').isVisible()), 'Escape schließt den Chat');
  await gast.close();
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

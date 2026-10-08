#!/usr/bin/env node
// E2E V12.17.0: Einrichtungsassistent (nur Neuinstallation), neutraler Start, Projektbereiche aus der Config,
// Bestand ohne Assistent. Zwei Server mit eigenen Ports (18821 neu, 18823 Bestand). Aufruf:  node tests/e2e_setup.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT_NEW = 18821, PORT_OLD = 18823;
const START_PW = 'Start-Passwort-1', NEW_PW = 'Neues-Passwort-2026';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dirs = [];
function startServer(port, legacy) {
  const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-su-'));
  const cfgDir = mkdtempSync(path.join(tmpdir(), 'mp-sucfg-'));
  dirs.push(dataDir, cfgDir);
  const py = `
import ipaddress, json, sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR = Path(${JSON.stringify(dataDir)})
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
server.init_db(${legacy ? '"werbetechnik"' : ''})
server.load_config()
server.create_or_reset_admin("admin", ${JSON.stringify(START_PW)})
${legacy ? `
with server.DB_LOCK, server.db_session() as con:
    con.execute("UPDATE state SET revision=revision+1 WHERE id=1")
` : ''}
httpd = server.MPHTTPServer(("127.0.0.1", ${port}), server.Handler)
print("READY", flush=True)
httpd.serve_forever()
`;
  const srv = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, MP_CONFIG_DIR: cfgDir } });
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('Server startet nicht')), 20000);
    srv.stdout.on('data', d => { if (String(d).includes('READY')) { clearTimeout(t); resolve(srv); } });
    srv.on('exit', c => reject(new Error('Server beendet: ' + c)));
  });
}
const srvNew = await startServer(PORT_NEW, false);
const srvOld = await startServer(PORT_OLD, true);

const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
const errors = [];

async function installHook(page) {
  await page.route(u => new URL(u).pathname === '/' || new URL(u).pathname === '/index.html', async route => {
    const resp = await route.fetch();
    let body = await resp.text();
    const end = body.lastIndexOf('})();');
    body = body.slice(0, end) + 'window.__t=src=>eval(src);\n' + body.slice(end);
    const headers = { ...resp.headers() };
    delete headers['content-security-policy'];
    delete headers['content-length'];
    await route.fulfill({ status: resp.status(), headers, body });
  });
}
const ev = (page, fn, arg) => page.evaluate(([src, a]) => window.__t('(' + src + ')')(a), [fn.toString(), arg]);
async function open(port, user, pw, hook = false) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, timezoneId: 'Europe/Berlin' });
  page.on('pageerror', e => errors.push(`${user}: ${e.message}`));
  if (hook) await installHook(page);
  await page.goto(`http://127.0.0.1:${port}/`);
  await page.fill('#loginUser', user);
  await page.fill('#loginPassword', pw);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.mouse.move(5, 5);   // Zeiger vom Anmelde-Knopf weg (Hover-Versatz des Weiter-Knopfs an gleicher Stelle)
  await page.waitForTimeout(700);
  return page;
}
const api = (page, method, url, body) => page.evaluate(async ([method, url, body]) => {
  const v = (await (await fetch('/api/health')).json()).version;
  const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': v }, body: body ? JSON.stringify(body) : undefined, cache: 'no-store' });
  let j = {}; try { j = await r.json(); } catch {}
  return [r.status, j];
}, [method, url, body]);
const shown = page => page.evaluate(() => document.getElementById('setupModal').classList.contains('show'));
const title = page => page.locator('#setupTitle').textContent();

try {
  // ================= Neuinstallation =================
  const a = await open(PORT_NEW, 'admin', START_PW, true);
  check(await shown(a), 'Neuinstallation: Assistent erscheint beim ersten Admin-Login');
  const st0 = (await api(a, 'GET', '/api/state'))[1];
  check(st0.data.departments.length === 0 && st0.data.machines.length === 0, 'neutraler Start: keine Bereiche, keine Maschinen');
  check(await a.locator('#setupDots .sDot').count() === 4, 'vier Fortschrittspunkte (Firma, Funktionen, Bereiche, Benutzer)');
  check(await a.evaluate(() => [...document.querySelectorAll('#setupDots .sDot')].every(d => d.title && d.getAttribute('aria-label'))), 'Fortschrittspunkte mit Tooltip und aria-label');
  check(await a.evaluate(() => document.querySelectorAll('#setupBody p').length === 0 && [...document.querySelectorAll('#setupBody .hint')].every(h => h.textContent.trim().length <= 2)), 'Schritt 1 ohne Erklärtext');
  check((await api(a, 'GET', '/api/config'))[1].adminPwUnchanged === true, 'Server meldet Startpasswort unverändert');

  // Schritt 1: Firma
  check(/Firma/.test(await title(a)), 'Schritt 1: Firma');
  await a.fill('#suName', 'Muster Metallbau GmbH');
  await a.fill('#suColor', '#aa3300');
  await a.click('#setupNext');
  await a.waitForFunction(() => /Funktionen/.test(document.getElementById('setupTitle').textContent));
  const c1 = (await api(a, 'GET', '/api/config'))[1];
  check(c1.company.name === 'Muster Metallbau GmbH' && c1.company.uiAccent.toLowerCase() === '#aa3300', 'Firmenname und Farbe gespeichert');
  check(c1.setupDone === false, 'Abschluss noch nicht gesetzt');

  // Schritt "Branche" entfällt (#51/#53): kein Kartenraster, keine Vorlage angewendet, Bereiche bleiben leer
  check(!(await a.evaluate(() => [...document.querySelectorAll('#setupDots .sDot')].some(d => d.title === 'Branche'))) && await a.locator('#suCards').count() === 0, 'Schritt Branche existiert nicht mehr');
  check(await a.evaluate(() => [...document.querySelectorAll('#setupDots .sDot')].map(d => d.title).join(',')) === 'Firma,Funktionen,Bereiche,Benutzer', 'Schritte: Firma, Funktionen, Bereiche, Benutzer');
  const st1 = (await api(a, 'GET', '/api/state'))[1].data;
  check(st1.departments.length === 0 && st1.machines.length === 0, 'ohne Vorlage bleiben Bereiche und Maschinen leer');
  check(await a.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Assistent ohne horizontales Scrollen (Desktop)');

  // Schritt 2: Funktionen
  check(await a.locator('#suMods [data-mod-sw]').count() === 11, 'elf Funktions-Schalter inklusive Palettenzettel, Rahmenaufträge, GF-Steuerung und Historie');
  check(await a.locator('#suMods [data-mod-sw="palletLabels"]').getAttribute('aria-pressed') === 'false', 'Palettenzettel sind bei neuer Einrichtung ausgeschaltet');
  await a.click('#suMods [data-mod-sw="chat"]');
  await a.waitForFunction(() => document.querySelector('#suMods [data-mod-sw="chat"]').getAttribute('aria-pressed') === 'false');
  check((await api(a, 'GET', '/api/config'))[1].modules.chat === false, 'Modul Nachrichten abgeschaltet');
  await a.click('#setupNext');
  await a.waitForFunction(() => /Bereiche/.test(document.getElementById('setupTitle').textContent));

  // Schritt 3: Bereiche/Maschinen (leerer Start, Bereiche werden hier angelegt)
  check(await a.locator('#setupBody [data-sd]').count() === 0, 'leerer Start: noch keine Bereiche');
  for (const n of ['Zuschnitt', 'Montage']) {
    await a.fill('#suDepNew', n); await a.click('#suDepAdd');
  }
  await a.waitForFunction(() => document.querySelectorAll('#setupBody [data-sd]').length === 2);
  await a.fill('#suDepNew', 'Lackiererei');
  await a.click('#suDepAdd');
  await a.waitForFunction(() => document.querySelectorAll('#setupBody [data-sd]').length === 3);
  const lackId = await a.evaluate(() => [...document.querySelectorAll('#setupBody [data-sd]')].find(i => i.value === 'Lackiererei').dataset.sd);
  await a.click(`[data-sm="${lackId}|1"]`);
  await a.waitForFunction(id => document.querySelector(`[data-sm="${id}|-1"]`) && /2/.test(document.querySelector(`[data-sm="${id}|-1"]`).nextElementSibling.textContent), lackId);
  const firstDep = await a.locator('#setupBody [data-sd]').first().getAttribute('data-sd');
  await a.locator('#setupBody [data-sd]').first().fill('Zuschnitt Halle 1');
  await a.locator('#setupBody [data-sd]').first().press('Tab');
  await a.waitForTimeout(1200);
  const st2 = (await api(a, 'GET', '/api/state'))[1].data;
  check(st2.departments.some(d => d.name === 'Lackiererei') && st2.machines.filter(m => m.departmentId === lackId).length === 2, 'neuer Bereich mit zwei Maschinen gespeichert');
  check(st2.departments.find(d => d.id === firstDep).name === 'Zuschnitt Halle 1', 'Bereich umbenannt');
  await a.click('#setupNext');
  await a.waitForFunction(() => /Benutzer/.test(document.getElementById('setupTitle').textContent));

  // Schritt 4: Passwort + Benutzer
  check(await a.locator('#suPw').count() === 1, 'Passwortfelder sichtbar (Startpasswort)');
  await a.fill('#suPwCur', START_PW); await a.fill('#suPwNew', 'kurz'); await a.fill('#suPwRep', 'kurz');
  await a.click('#suPwSave');
  check((await a.locator('#suPwErr').textContent()).length > 0, 'zu kurzes Passwort wird abgewiesen');
  await a.fill('#suPwNew', NEW_PW); await a.fill('#suPwRep', NEW_PW);
  await a.click('#suPwSave');
  await a.waitForFunction(() => !document.getElementById('suPw'));
  check(true, 'Passwort geändert, Felder verschwinden');
  await a.fill('#suUser', 'lena'); await a.fill('#suUserPw', 'Lena-Passwort-1');
  await a.selectOption('#suUserRole', 'viewer');
  await a.click('#suUserAdd');
  await a.waitForFunction(() => document.querySelectorAll('#suAdded .pChip').length === 1);
  check((await api(a, 'GET', '/api/users'))[1].users?.some(u => u.username === 'lena') ?? JSON.stringify((await api(a, 'GET', '/api/users'))[1]).includes('lena'), 'Benutzer angelegt');
  check(await a.locator('#setupSkip').isHidden(), 'letzter Schritt: nur Fertig');
  await a.click('#setupNext');
  await a.waitForFunction(() => !document.getElementById('setupModal').classList.contains('show'));
  const c2 = (await api(a, 'GET', '/api/config'))[1];
  check(c2.setupDone === true && c2.adminPwUnchanged === undefined, 'Fertig: setupDone gesetzt');

  // Projektbereiche aus der Config
  const areas0 = await ev(a, () => projectAreas().map(x => x.id).slice(0, 7).join(','));
  check(areas0 === 'sales,pm,engineering,calculation,purchasing,quality,av', 'Projektbereiche = die sieben aus der Config');
  const cfgRev = (await api(a, 'GET', '/api/config'))[1].revision;
  const pa = (await api(a, 'GET', '/api/config'))[1].projectAreas.concat([{ id: 'labor', name: 'Labor' }]);
  const pr = await api(a, 'PATCH', '/api/config', { revision: cfgRev, projectAreas: pa });
  check(pr[0] === 200, 'Projektbereiche per Config erweiterbar');
  await a.reload(); await a.waitForTimeout(1200);
  check(await ev(a, () => projectAreas().some(x => x.id === 'labor' && x.name === 'Labor')), 'Client nutzt projectAreas aus der Config');
  check(!(await shown(a)), 'nach Reload kein Assistent mehr');

  // Erneut aufrufbar (Firmenprofil), ohne Löschfunktionen
  await a.click('#navSystem'); await a.click('[data-systab="company"]'); await a.waitForTimeout(300);
  check(await a.locator('#ciSetup').getAttribute('aria-label') !== null && await a.locator('#ciSetup').getAttribute('title') !== null, 'Assistent-Knopf im Firmenprofil mit Tooltip und aria-label');
  await a.click('#ciSetup');
  check(await shown(a), 'Assistent erneut aufrufbar');
  await a.click('#setupSkip'); await a.click('#setupSkip');
  check(await a.locator('#setupBody [data-sx]').count() === 0, 'erneuter Aufruf: kein Entfernen von Bereichen');
  await a.click('#setupClose');
  check(!(await shown(a)), 'Schließen ohne Änderung');

  // Neues Passwort gilt
  const b = await open(PORT_NEW, 'admin', NEW_PW);
  check(!(await shown(b)), 'zweiter Login: kein Assistent');
  await b.close();
  const bad = await (async () => { const p = await browser.newPage(); await p.goto(`http://127.0.0.1:${PORT_NEW}/`); const r = await p.evaluate(async pw => (await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': (await (await fetch('/api/health')).json()).version }, body: JSON.stringify({ username: 'admin', password: pw }) })).status, START_PW); await p.close(); return r; })();
  check(bad === 401, 'Startpasswort gilt nicht mehr (' + bad + ')');
  await a.close();
} catch (e) {
  check(false, 'Neuinstallation: ' + (e?.message || e));
}

try {
  // ================= Abbruch/Wiederaufnahme =================
  // (eigener frischer Server wäre nötig; hier: Bestand prüfen)
  const o = await open(PORT_OLD, 'admin', START_PW);
  check(!(await shown(o)), 'Bestand: kein Assistent');
  const oc = (await api(o, 'GET', '/api/config'))[1];
  check(oc.setupDone === true && oc.adminPwUnchanged === undefined, 'Bestand: setupDone=true');
  const os = (await api(o, 'GET', '/api/state'))[1].data;
  check(os.departments.length === 6 && os.departments[0].id === 'cnc', 'Bestand: alle Bereiche unverändert');
  check(oc.projectAreas.map(x => x.id).join(',') === 'sales,pm,engineering,calculation,purchasing,quality,av', 'Bestand: sieben Projektbereiche in der Config');
  await o.reload(); await o.waitForTimeout(800);
  check(!(await shown(o)), 'Bestand: auch nach Reload kein Assistent');
  await o.close();
} catch (e) {
  check(false, 'Bestand: ' + (e?.message || e));
}

try {
  // ================= Schließen = später fortsetzen =================
  const dirsBefore = dirs.length;
  const srv3 = await startServer(18825, false);
  const c = await open(18825, 'admin', START_PW);
  check(await shown(c), 'frischer Server: Assistent');
  await c.fill('#suName', 'Teilstand AG');
  await c.click('#setupNext');
  await c.waitForFunction(() => /Funktionen/.test(document.getElementById('setupTitle').textContent));
  await c.click('#setupClose');
  check(!(await shown(c)), 'Schließen blendet den Assistenten aus');
  const cc = (await api(c, 'GET', '/api/config'))[1];
  check(cc.setupDone === false && cc.company.name === 'Teilstand AG', 'Abbruch: bisheriger Stand bleibt, Einrichtung nicht abgeschlossen');
  await c.close();
  const c2 = await open(18825, 'admin', START_PW);
  check(await shown(c2), 'nächster Login: Assistent erscheint wieder');
  check(await c2.locator('#suName').inputValue() === 'Teilstand AG', 'Name aus dem Teilstand vorbelegt');
  // Mobil (390px): kein horizontales Scrollen in keinem Schritt
  await c2.setViewportSize({ width: 390, height: 800 });
  let wide = 0;
  for (let i = 0; i < 4; i++) {
    if (await c2.evaluate(() => document.documentElement.scrollWidth > window.innerWidth || document.getElementById('setupBody').scrollWidth > document.getElementById('setupBody').clientWidth + 1)) wide++;
    if (i < 3) await c2.click('#setupSkip');
  }
  check(wide === 0, 'Assistent bei 390px ohne horizontales Scrollen (' + wide + ' breite Schritte)');
  for (let i = 0; i < 3; i++) await c2.click('#setupBack');
  // alles überspringen
  for (let i = 0; i < 3; i++) await c2.click('#setupSkip');
  await c2.click('#setupNext');
  await c2.waitForFunction(() => !document.getElementById('setupModal').classList.contains('show'));
  check((await api(c2, 'GET', '/api/config'))[1].setupDone === true, 'alle Schritte übersprungen: abgeschlossen');
  check((await api(c2, 'GET', '/api/state'))[1].data.departments.length === 0, 'übersprungene Schritte ändern nichts');
  await c2.click('#navPlan').catch(() => {});
  await c2.waitForTimeout(500);
  await c2.close();
  srv3.kill();
} catch (e) {
  check(false, 'Abbruch: ' + (e?.message || e));
}

check(!errors.length, 'keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
await browser.close();
srvNew.kill(); srvOld.kill();
for (const d of dirs) { try { rmSync(d, { recursive: true, force: true }); } catch {} }
const failed = results.filter(r => !r[0]).length;
console.log(`\n${results.length - failed}/${results.length} bestanden`);
process.exit(failed ? 1 : 0);

#!/usr/bin/env node
// V12.21.0: Oberfläche über HTTPS – Anmeldung, Secure-Cookie, Speichern und Long-Poll.
// node tests/e2e_https.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18971, BASE = `https://127.0.0.1:${PORT}/`, PASS = 'Https-Test-Passwort-1234';
async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  return import(pathToFileURL(path.join(execSync('npm root -g').toString().trim(), 'playwright', 'index.mjs')).href);
}
const results = [], errors = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };
const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-https-'));
const py = `
import ipaddress, sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR=Path(${JSON.stringify(dataDir)})
server.DB_PATH=server.DATA_DIR/'maschinenplanung.sqlite3'
server.ALLOWED_NETWORK=ipaddress.ip_network('127.0.0.0/8')
server.init_db(seed='werbetechnik')
server.create_or_reset_admin('admin',${JSON.stringify(PASS)})
server.tls_setup('127.0.0.1')
httpd=server.MPHTTPServer(('127.0.0.1',${PORT}),server.Handler,ssl_context=server.tls_context())
print('READY',flush=True)
httpd.serve_forever()
`;
const srv = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, MP_CONFIG_DIR: path.join(dataDir, 'config') } });
await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('Server startet nicht')), 60000);
  srv.stdout.on('data', d => { if (String(d).includes('READY')) { clearTimeout(timer); resolve(); } });
  srv.on('exit', c => reject(new Error('Server beendet: ' + c)));
});
const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
try {
  // Die Firmen-CA ist im Testbrowser nicht installiert; die Zertifikatsprüfung selbst deckt tests/test_tls.py ab.
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, timezoneId: 'Europe/Berlin' }), page = await ctx.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(BASE);
  check(page.url().startsWith('https://'), 'Oberfläche lädt über HTTPS');
  check((await page.locator('#loginAccessUrl').textContent()).startsWith('https://127.0.0.1'), 'Anmeldemaske zeigt die HTTPS-Adresse');
  await page.fill('#loginUser', 'admin'); await page.fill('#loginPassword', PASS); await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'), null, { timeout: 15000 });
  const cookie = (await ctx.cookies()).find(c => c.name === 'mp_session');
  check(cookie?.secure === true && cookie?.httpOnly === true, 'Sitzungscookie ist Secure und HttpOnly');
  // #52: Mit HTTPS keine HTTPS-Empfehlung im Systemstatus, TLS = an.
  await page.click('#navSystem');
  const status = await page.waitForFunction(() => document.getElementById('adminStatusPanel')?.innerText || '', null, { timeout: 15000 }).then(h => h.jsonValue()).catch(() => '');
  check(/Verschlüsselung \(TLS\)\s*an/.test(status) && !status.includes('HTTPS nicht eingerichtet'), 'Systemstatus: TLS an, keine HTTPS-Warnung');
  await page.waitForFunction(() => /Server gespeichert|Server verbunden|gespeichert/i.test(document.querySelector('#saveState')?.textContent || ''), null, { timeout: 15000 }).catch(() => {});
  const saved = await page.evaluate(async () => {
    const st = await (await fetch('/api/state')).json(); const h = await (await fetch('/api/health')).json();
    st.data.ui = { ...(st.data.ui || {}), week: st.data.ui?.week || '2026-10-05' };
    const r = await fetch('/api/state', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': h.version }, body: JSON.stringify({ revision: st.revision, data: st.data, action: 'HTTPS-Test' }) });
    return r.status;
  });
  check(saved === 200, `Speichern über HTTPS (Origin https) wird angenommen (${saved})`);
  const poll = await page.evaluate(async () => (await fetch('/api/revision?since=-1&wait=0')).status);
  check(poll === 200, 'Revision-Abfrage (Long-Poll) über HTTPS');
  await ctx.close();
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  check(errors.length === 0, 'Keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser.close(); srv.kill(); rmSync(dataDir, { recursive: true, force: true });
}
console.log(`\n${results.filter(x => x[0]).length}/${results.length} bestanden`);
process.exit(results.every(x => x[0]) ? 0 : 1);

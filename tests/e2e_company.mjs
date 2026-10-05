#!/usr/bin/env node
// E2E Firmenprofil (ab V12.15.0): Name, Farbe, Logo, Begriffe kommen aus /api/config.
//  1. Neue leere Installation zeigt neutrale Werte.
//  2. Bestands-DB mit altem Seed-Stand (ohne data.ci) ergibt nach dem Update dieselbe Anzeige:
//     Name, Farbe, Logo (Byte-genau), Begriff, Bereiche.
//  3. Admin ändert Name, Farbe, Logo und Begriff; alle anderen Clients sehen es ohne Neuladen.
//     Nicht-Admins werden serverseitig abgewiesen; unsichere Logos werden abgelehnt.
//
// Aufruf:  node tests/e2e_company.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import zlibMod from 'node:zlib';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT_ADMIN = 18803, PORT_SEED = 18805, PORT_NEUTRAL = 18807;
const PASS = 'E2E-Test-1234';
async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const SEED = JSON.parse(readFileSync(path.join(SRC, 'tools', 'legacy_employer_seed.json'), 'utf8'));
const SEED_LOGO_BYTES = Buffer.from(SEED.logoDataUrl.split(',')[1], 'base64');
// kleines gültiges PNG (rot, 4x4)
const crcTable = (() => { const t = []; for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
const crc = b => { let c = 0xffffffff; for (const x of b) c = crcTable[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
const chunk = (type, data) => { const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(type), data]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([len, td, c]); };
const makePng = (w, h, rgb) => {
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array.from({ length: w }, () => rgb).flat())]);
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr), chunk('IDAT', zlibMod.deflateSync(Buffer.concat(Array(h).fill(row)))), chunk('IEND', Buffer.alloc(0))]);
};
const PNG_RED = makePng(4, 4, [226, 56, 42]);
const GOOD_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20" width="40" height="20"><rect width="40" height="20" fill="#0a7"/></svg>';
const EVIL_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>';

function startServer(port, kind) {
  const cfgDir = mkdtempSync(path.join(tmpdir(), `mp-company-${kind}-cfg-`));
  const dataDir = mkdtempSync(path.join(tmpdir(), `mp-company-${kind}-`));
  const py = `
import ipaddress, json, os, sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR = Path(${JSON.stringify(dataDir)})
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
server.PBKDF2_ITERS = 1000
server.init_db()
server.create_or_reset_admin("admin", ${JSON.stringify(PASS)})
with server.DB_LOCK, server.db_session() as con:
    for name, role, dep in ${JSON.stringify([["pm", "project_management", ""], ["viewer", "viewer", ""]])}:
        salt, digest = server.hash_password(${JSON.stringify(PASS)})
        con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", (name, salt, digest, role, dep, server.now_iso(), server.now_iso()))
    if ${JSON.stringify(kind)} == "oldseed":
        # Bestand wie vor V12.14: alter Seed-Stand (Bereichsnamen mit Personen), kein data.ci, mehrfach gespeichert
        st = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
        for d in st["departments"]:
            if d["id"] == "konf1": d["name"] = "Konfektion 1 \\u2013 Thomsen"
            if d["id"] == "konf2": d["name"] = "Konfektion 2 \\u2013 Keller"
        st.pop("ci", None)
        con.execute("UPDATE state SET json=?, revision=5", (json.dumps(st, ensure_ascii=False),))
cfg, warn = server.load_config()   # wie beim Start von main(): Migration vor dem Port-Bind
httpd = server.MPHTTPServer(("127.0.0.1", ${port}), server.Handler)
print("READY", flush=True)
httpd.serve_forever()
`;
  const proc = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], {
    stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, MP_CONFIG_DIR: cfgDir },
  });
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('Server startet nicht')), 25000);
    proc.stdout.on('data', d => { if (String(d).includes('READY')) { clearTimeout(t); resolve({ proc, cfgDir, base: `http://127.0.0.1:${port}/` }); } });
    proc.on('exit', c => reject(new Error('Server beendet: ' + c)));
  });
}

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

const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
const errors = [];
async function open(base, user) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, timezoneId: 'Europe/Berlin' });
  page.on('pageerror', e => errors.push(`${user}: ${e.message}`));
  await installHook(page);
  await page.goto(base);
  await page.fill('#loginUser', user);
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForFunction(() => window.__t && window.__t('CFG!==null'), null, { timeout: 8000 });
  await page.waitForTimeout(300);
  return page;
}
const snap = page => ev(page, () => {
  const ci = ciSettings(), bm = document.querySelector('.brandMark');
  return {
    h1: document.querySelector('.brand h1').textContent, title: document.title, logoImg: bm.querySelector('img')?.getAttribute('src') || '',
    mark: bm.textContent, accent: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(),
    brand: getComputedStyle(document.documentElement).getPropertyValue('--brand').trim(),
    company: ci.company, color: ci.color, font: ci.font, logo: ci.logo, pn: pnLabel(), qLabel: document.getElementById('qWTLabel').textContent,
    depts: data.departments.map(d => d.name), cfgRev: CFG.revision,
  };
});
const waitFor = async (page, fn, arg, ms = 10000) => { try { await page.waitForFunction(([src, a]) => window.__t('(' + src + ')')(a), [fn.toString(), arg], { timeout: ms, polling: 100 }); return true; } catch { return false; } };
const apiFetch = (page, method, url, body) => page.evaluate(async ([m, u, b]) => {
  const r = await fetch(u, { method: m, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': document.title.match(/V([\d.]+)/)?.[1] || '' }, body: b ? JSON.stringify(b) : undefined });
  let j = null; try { j = await r.json(); } catch {}
  return { status: r.status, body: j };
}, [method, url, body]);

const servers = [];
try {
  // ------------------------------------------------------------------ 1. Neue leere Installation: neutral
  const N = await startServer(PORT_NEUTRAL, 'neutral'); servers.push(N);
  const pn = await open(N.base, 'admin');
  const s1 = await snap(pn);
  check(s1.h1 === 'Produktionsplanung' && s1.company === '' && s1.logo === '' && s1.logoImg === '' && s1.mark === 'PP', 'Neue Installation: neutrale Werte (kein Name, kein Logo)');
  check(s1.depts.every(n => !/Thomsen|Keller/.test(n)) && s1.depts.includes('Konfektion 1') && s1.depts.includes('Konfektion 2'), 'Neue Installation: Bereiche ohne Personennamen');
  check(s1.pn === 'Projektnummer' && s1.color === '#1f5eff' && s1.accent === '#1f5eff', 'Neue Installation: neutrale Begriffe und Farben');
  const html1 = await pn.content();
  check(!/Thomsen|Keller|ART OF DISPLAY|\/9j\/4AAQ/.test(html1), 'Neue Installation: Seite enthält keine Arbeitgeberdaten');
  await pn.close();

  // ------------------------------------------------------------------ 2. Bestands-DB mit altem Seed-Stand
  const S = await startServer(PORT_SEED, 'oldseed'); servers.push(S);
  const ps = await open(S.base, 'admin');
  const s2 = await snap(ps);
  check(s2.company === SEED.company.name, 'Bestand: Firmenname wie bisher');
  check(s2.color.toUpperCase() === SEED.company.color && s2.font === SEED.company.font, 'Bestand: Farbe und Schrift wie bisher');
  check(s2.logo === SEED.logoDataUrl, 'Bestand: Logo im Client byte-gleich zum bisherigen Standardlogo');
  const served = await ps.evaluate(async () => { const r = await fetch('/api/config/logo', { credentials: 'same-origin' }); return { type: r.headers.get('content-type'), b64: btoa(String.fromCharCode(...new Uint8Array(await r.arrayBuffer()))) }; });
  check(served.type === 'image/jpeg' && served.b64 === SEED_LOGO_BYTES.toString('base64'), 'Bestand: /api/config/logo liefert das bisherige Logo (image/jpeg)');
  check(s2.logoImg.startsWith('/api/config/logo') && s2.accent === '#1f5eff', 'Bestand: Logo im Kopf, Akzentfarbe unverändert');
  check(s2.pn === SEED.terms.projectNumber + ' / Projektnummer' && s2.qLabel === s2.pn, 'Bestand: Begriff wie bisher');
  check(s2.depts.includes('Konfektion 1 – Thomsen') && s2.depts.includes('Konfektion 2 – Keller') && s2.depts.length === 6, 'Bestand: Bereiche unverändert (6, Namen aus der Datenbank)');
  const cfgS = await apiFetch(ps, 'GET', '/api/config');
  check(cfgS.body.terms.projectNumber === 'WT' && cfgS.body.projectAreas.length === 7 && Object.values(cfgS.body.modules).every(Boolean), 'Bestand: Begriff WT, 7 Projekt-Bereiche, alle Module');
  await ps.close();

  // ------------------------------------------------------------------ 3. Admin ändert, alle sehen es
  const A = await startServer(PORT_ADMIN, 'admin'); servers.push(A);
  const admin = await open(A.base, 'admin');
  const viewer = await open(A.base, 'viewer');
  const pm = await open(A.base, 'pm');
  const before = await snap(viewer);
  check(await ev(admin, () => { data.ui.sysTab = 'company'; switchView('settings'); renderSettings(); return document.getElementById('ciPanel').style.display !== 'none' }), 'Admin sieht das Firmenprofil');
  check(await ev(viewer, () => { switchView('settings'); renderSettings(); return !document.getElementById('ciPanel') || document.getElementById('ciPanel').style.display === 'none' }), 'Lesender sieht das Firmenprofil nicht');
  check(await ev(pm, () => { switchView('settings'); renderSettings(); return !document.getElementById('ciPanel') || document.getElementById('ciPanel').style.display === 'none' }), 'Projektmanagement sieht das Firmenprofil nicht');

  // Vorschau sofort (schon beim Tippen, vor dem Speichern)
  await admin.fill('#ciProduct', 'Muster Planung');
  check((await snap(admin)).h1 === 'Muster Planung' && (await snap(admin)).cfgRev === before.cfgRev, 'Vorschau sofort: Programmname erscheint beim Tippen, noch nicht gespeichert');
  await admin.press('#ciProduct', 'Tab');   // change -> PATCH
  check(await waitFor(viewer, () => document.querySelector('.brand h1').textContent === 'Muster Planung'), 'Lesender Client sieht neuen Programmnamen ohne Neuladen');

  await admin.fill('#ciCompany', 'Muster *Werbung* GmbH');
  await admin.press('#ciCompany', 'Tab');
  await admin.fill('#ciTerm', 'Auftrag-Nr');
  await admin.press('#ciTerm', 'Tab');
  for (const [id, val] of [['#ciAccent', '#00aa55'], ['#ciColor', '#aa0033']]) {
    await admin.$eval(id, (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }, val);
    await admin.waitForTimeout(250);
  }
  check((await snap(admin)).brand.toLowerCase().includes('00aa55') || (await snap(admin)).accent === '#00aa55', 'Vorschau sofort: Akzentfarbe wirkt im Admin-Client (--accent/--brand)');
  await admin.setInputFiles('#ciLogo', { name: 'logo.png', mimeType: 'image/png', buffer: PNG_RED });
  const seenBy = async page => waitFor(page, () => ciSettings().company === 'Muster *Werbung* GmbH' && ciSettings().color === '#aa0033' && ciSettings().logo.startsWith('data:image/png') && document.querySelector('.brandMark img') && pnLabel() === 'Auftrag-Nr / Projektnummer');
  check(await seenBy(viewer), 'Lesender Client: Name, Dokumentfarbe, Logo und Begriff übernommen');
  check(await seenBy(pm), 'Projektmanagement-Client: Name, Dokumentfarbe, Logo und Begriff übernommen');
  check(await seenBy(admin), 'Admin-Client: alles übernommen');
  for (const [who, page] of [['Lesend', viewer], ['PM', pm], ['Admin', admin]]) {
    const s = await snap(page);
    check(s.h1 === 'Muster Planung' && s.accent === '#00aa55' && s.title.includes('Muster Werbung GmbH') && s.logoImg.startsWith('/api/config/logo') && s.qLabel === 'Auftrag-Nr / Projektnummer',
      `${who}: Kopf, Akzent, Titel, Logo und Label stimmen`);
  }
  const served2 = await viewer.evaluate(async () => { const r = await fetch('/api/config/logo', { credentials: 'same-origin' }); return { type: r.headers.get('content-type'), n: (await r.arrayBuffer()).byteLength }; });
  check(served2.type === 'image/png' && served2.n === PNG_RED.length, 'Logo-Endpunkt liefert das hochgeladene PNG mit Content-Type image/png');

  // Nicht-Admin wird serverseitig abgewiesen
  const cur = (await apiFetch(admin, 'GET', '/api/config')).body;
  for (const [who, page] of [['Lesend', viewer], ['PM', pm]]) {
    const r = await apiFetch(page, 'PATCH', '/api/config', { revision: cur.revision, company: { name: 'Hack AG' } });
    check(r.status === 403, `${who}: Änderung des Firmenprofils verweigert (403)`);
    const l = await apiFetch(page, 'POST', '/api/config/logo', { revision: cur.revision, contentType: 'image/png', data: PNG_RED.toString('base64') });
    check(l.status === 403, `${who}: Logo-Upload verweigert (403)`);
  }
  const after = (await apiFetch(admin, 'GET', '/api/config')).body;
  check(after.revision === cur.revision && after.company.name === 'Muster *Werbung* GmbH', 'Verweigerte Änderungen lassen die Config unverändert');

  // Konflikt bei veralteter Revision
  const stale = await apiFetch(admin, 'PATCH', '/api/config', { revision: cur.revision - 1, company: { name: 'Alt' } });
  check(stale.status === 409 && stale.body.errorCode === 'MP-CFG-004', 'Veraltete Revision: 409 MP-CFG-004');

  // Unsicheres SVG wird abgelehnt, gutes SVG angenommen und für Excel gerastert
  await admin.setInputFiles('#ciLogo', { name: 'evil.svg', mimeType: 'image/svg+xml', buffer: Buffer.from(EVIL_SVG) });
  check(await waitFor(admin, () => /MP-CFG-005/.test(document.getElementById('toast').textContent) || /MP-CFG-005/.test(document.body.innerText)), 'Unsicheres SVG: Meldung MP-CFG-005');
  await admin.click('#closeError');
  check((await apiFetch(admin, 'GET', '/api/config')).body.revision === cur.revision, 'Unsicheres SVG ändert nichts');
  await admin.setInputFiles('#ciLogo', { name: 'good.svg', mimeType: 'image/svg+xml', buffer: Buffer.from(GOOD_SVG) });
  check(await waitFor(viewer, () => CFG.company.logoType === 'image/svg+xml' && ciSettings().logo.startsWith('data:image/png') && ciSettings().logoW > 0), 'Sauberes SVG: alle Clients sehen es, Excel-Logo wird zu PNG gerastert');

  // Logo entfernen
  await admin.click('#ciLogoRemove');
  check(await waitFor(viewer, () => !document.querySelector('.brandMark img') && ciSettings().logo === ''), 'Logo entfernen: Kopf zeigt wieder das Kürzel, Excel ohne Logo');

  // Persistenz: nach Neuladen weiterhin gültig
  await admin.reload();
  const s3 = await (async () => { await admin.waitForFunction(() => window.__t && (() => { try { return window.__t('CFG!==null') } catch (e) { return false } })(), null, { timeout: 8000 }).catch(() => {}); return snap(admin).catch(() => null); })();
  check(s3 && s3.h1 === 'Muster Planung' && s3.company === 'Muster *Werbung* GmbH' && s3.color === '#aa0033', 'Nach Neuladen: Werte stammen aus der Config-Datei');

  check(errors.length === 0, 'Keine JS-Fehler: ' + errors.slice(0, 3).join(' | '));
} catch (e) {
  check(false, 'Testlauf: ' + (e && e.stack || e));
} finally {
  await browser.close();
  for (const s of servers) s.proc.kill();
}
const failed = results.filter(r => !r[0]).length;
console.log(`${results.length - failed}/${results.length} bestanden`);
process.exit(failed ? 1 : 0);

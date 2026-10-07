#!/usr/bin/env node
// E2E Darstellung (ab V12.11.0): Hell / Dunkel / Halle über alle Ansichten.
// Prüft je Theme: keine JS-Fehler, Kontrast aller sichtbaren Texte (Dunkel ≥ 4,5:1, Halle ≥ 7:1),
// Halle: keine Schrift unter 14 px; Umschalter, Speicherung je Gerät, URL-Parameter, Druck bleibt hell.
// Bildschirmfotos: --shots <ordner>
//
// Aufruf:  node tests/e2e_theme.mjs [--shots <ordner>]
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18795;
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
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-theme-'));
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
        fa = f"FA 70{i:02d}"
        x = {"id": f"ws_r{i}", "sequence": i * 10, "planningType": "MACHINE", "pos": i * 10, "departmentId": dep, "projectId": "", "predecessorIds": [], "fa": fa, "ab": "AB-500", "wt": "", "machineId": mid, "altMachineId": "", "allowAlternative": False, "order": fa, "articleNo": "A-1", "description": "Rundgang", "targetQty": 20, "dueDate": "2026-10-23", "baselinePlan": None, "hours": 5, "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none", "requiredStart": "", "requiredFinish": "", "createdAt": "2026-10-01T08:00:00Z", "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "", "pausedAt": "", "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": ""}
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
    for name, role, dep in ${JSON.stringify([])}:
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

// Im Browser: alle sichtbaren Textelemente mit Kontrast unter dem Ziel bzw. Schrift unter minPx.
const AUDIT = ({ target, minPx }) => {
  const parse = c => { const k = c.match(/color\(srgb ([^)]+)\)/); if (k) { const p = k[1].split(/[ /]+/).filter(Boolean).map(Number); return { r: p[0] * 255, g: p[1] * 255, b: p[2] * 255, a: p.length > 3 ? p[3] : 1 }; } const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const blend = (top, bot) => ({ r: top.r * top.a + bot.r * (1 - top.a), g: top.g * top.a + bot.g * (1 - top.a), b: top.b * top.a + bot.b * (1 - top.a), a: 1 });
  const L = c => { const f = v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }; return .2126 * f(c.r) + .7152 * f(c.g) + .0722 * f(c.b); };
  const ratio = (a, b) => { const x = L(a), y = L(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); };
  const bgOf = el => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;   // Verlauf/Bild: nicht messbar
      const c = parse(cs.backgroundColor);
      if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; }
    }
    let bg = { r: 255, g: 255, b: 255, a: 1 };
    for (let i = layers.length - 1; i >= 0; i--) bg = blend(layers[i], bg);
    return bg;
  };
  const out = [], small = [];
  const desc = e => (e.id ? '#' + e.id : e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.') : '')) + ' „' + (e.textContent || '').trim().slice(0, 25) + '“';
  for (const e of document.querySelectorAll('body *')) {
    if (!['SCRIPT', 'STYLE', 'OPTION'].includes(e.tagName) && [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) {
      const r = e.getClientRects(); if (!r.length) continue;
      const cs = getComputedStyle(e);
      if (cs.visibility === 'hidden' || +cs.opacity === 0 || e.closest('[aria-hidden="true"],[disabled],.modal:not(.show)')) continue;
      let op = 1; for (let x = e; x; x = x.parentElement) op *= +getComputedStyle(x).opacity; if (op < .5) continue;   // bewusst ausgegraut
      const bg = bgOf(e); const fg = parse(cs.color); if (!bg || !fg) continue;
      const q = ratio(fg.a < 1 ? blend(fg, bg) : fg, bg);
      if (q < target) out.push(`${desc(e)} ${q.toFixed(2)} (${cs.color} auf rgb(${bg.r | 0},${bg.g | 0},${bg.b | 0}))`);
      if (minPx && parseFloat(cs.fontSize) < minPx - .01) small.push(`${desc(e)} ${cs.fontSize}`);
    }
  }
  return { out, small };
};

async function login(page) {
  await page.goto(BASE);
  await page.fill('#loginUser', 'admin');
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(500);
}
const TARGETS = [['#navPlan'], ['#navList'], ['#navOrders'], ['#navPersonnel'], ['#navGF'], ['#navSystem'], ['#navSystem', '#sysAudit'], ['#navSystem', '#sysHistory'], ['#navSystem', '#sysReport']];
const GOAL = { dark: 4.5, hall: 7 };

try {
  // ------------------------------------------------------------------ Alle Ansichten je Theme
  for (const theme of ['light', 'dark', 'hall']) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, timezoneId: 'Europe/Berlin' });
    page.on('pageerror', e => errors.push(`${theme}: ${e.message}`));
    await page.addInitScript(t => { try { localStorage.setItem('mp_theme', t); } catch {} }, theme);
    await login(page);
    check(await page.evaluate(() => document.documentElement.dataset.theme) === theme, `${theme}: Theme aktiv`);
    const fontScale = await page.evaluate(() => Number(getComputedStyle(document.documentElement).getPropertyValue('--fs')));
    check(fontScale === (theme === 'hall' ? 1.35 : 1), `${theme}: Schriftgröße verwendet den vorgesehenen Skalierungsfaktor (${fontScale})`);
    const bad = [], small = [], overflow = [];
    for (const steps of TARGETS) {
      for (const sel of steps) { await page.click(sel); await page.waitForTimeout(250); }
      await page.waitForTimeout(250);
      const view = await page.evaluate(() => document.querySelector('.view.active')?.id);
      if (theme !== 'light') {
        const r = await page.evaluate(AUDIT, { target: GOAL[theme], minPx: theme === 'hall' ? 14 : 0 });
        bad.push(...r.out.map(x => `${view}: ${x}`)); small.push(...r.small.map(x => `${view}: ${x}`));
      }
      const sw = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: innerWidth, out: [...document.querySelectorAll('.view.active button, .view.active input, .view.active select')].filter(e => e.getClientRects().length && e.getBoundingClientRect().right > innerWidth + 1 && !e.closest('[style*="overflow"], .boardWrap, .tableWrap, .scroll')).map(e => e.id || e.textContent.trim().slice(0, 20)).slice(0, 5) }));
      if (sw.sw > sw.iw + 1 || sw.out.length) overflow.push(`${view}: ${sw.sw}>${sw.iw} ${sw.out.join(',')}`);
      if (shotDir) await page.screenshot({ path: path.join(shotDir, `${theme}_${steps.at(-1).slice(1)}.png`), fullPage: false });
    }
    // Dialog „+ Auftrag“
    await page.click('#navPlan'); await page.waitForTimeout(200);
    if (await page.locator('#quickAdd').isVisible()) {
      await page.click('#quickAdd'); await page.waitForTimeout(300);
      if (theme !== 'light') { const r = await page.evaluate(AUDIT, { target: GOAL[theme], minPx: theme === 'hall' ? 14 : 0 }); bad.push(...r.out.map(x => `Dialog: ${x}`)); small.push(...r.small.map(x => `Dialog: ${x}`)); }
      if (shotDir) await page.screenshot({ path: path.join(shotDir, `${theme}_dialog.png`) });
      await page.keyboard.press('Escape');
    }
    if (theme !== 'light') {
      const uniq = [...new Set(bad)];
      check(!uniq.length, `${theme}: Kontrast ≥ ${GOAL[theme]}:1 für alle sichtbaren Texte (${uniq.length} Verstöße) ${uniq.slice(0, 12).join(' | ')}`);
    }
    check(!overflow.length, `${theme}: nichts ragt seitlich aus dem Bild (${overflow.length}) ${overflow.slice(0, 6).join(' | ')}`);
    if (theme === 'hall') {
      await page.click('#navPlan'); await page.waitForTimeout(300);
      const sym = await page.evaluate(() => { const c = document.querySelector('.jobchip'); if (!c) return 'kein Chip'; c.classList.add('released'); const v = getComputedStyle(c, '::after').content; c.classList.remove('released'); return v; });
      check(sym.includes('✓'), `hall: Status zusätzlich als Symbol (freigegeben → ${sym})`);
      const u = [...new Set(small)];
      check(!u.length, `hall: keine Schrift unter 14 px (${u.length}) ${u.slice(0, 12).join(' | ')}`);
    }
    await page.close();
  }

  // ------------------------------------------------------------------ Umschalter, Speicherung, URL, Druck
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, colorScheme: 'dark', timezoneId: 'Europe/Berlin' });
  page.on('pageerror', e => errors.push(`toggle: ${e.message}`));
  await login(page);
  check(await page.evaluate(() => document.documentElement.dataset.theme) === 'dark', 'Ohne Auswahl folgt das Theme der Systemeinstellung (dunkel)');
  await page.click('#themeBtn');
  check(await page.evaluate(() => document.documentElement.dataset.theme) === 'hall', 'Klick: Dunkel → Halle');
  check((await page.getAttribute('#themeBtn', 'title')).includes('Halle'), 'Knopf-Tooltip nennt aktuelles Theme');
  await page.reload(); await page.waitForTimeout(400);
  check(await page.evaluate(() => document.documentElement.dataset.theme) === 'hall', 'Auswahl bleibt nach Neuladen (je Gerät)');
  await page.click('#themeBtn');
  check(await page.evaluate(() => document.documentElement.dataset.theme) === 'light', 'Klick: Halle → Hell');
  await page.emulateMedia({ media: 'print' });
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  const printBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  check(printBg === 'rgb(244, 247, 251)' || printBg === 'rgb(255, 255, 255)', `Druck bleibt hell auch im Dunkel-Modus (${printBg})`);
  await page.emulateMedia({ media: 'screen' });
  await page.goto(BASE + '?theme=hall&view=orders'); await page.waitForTimeout(700);
  const u = await page.evaluate(() => ({ t: document.documentElement.dataset.theme, v: document.querySelector('.view.active')?.id, saved: localStorage.getItem('mp_theme') }));
  check(u.t === 'hall' && u.v === 'orders', `URL ?theme=hall&view=orders öffnet Auftragsliste im Hallenmodus (${u.t}/${u.v})`);
  check(u.saved === 'light', 'URL-Theme überschreibt die gespeicherte Auswahl nicht');
  const accent = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--brand').trim());
  check(accent.includes('color-mix') || accent.startsWith('rgb') || accent.startsWith('#'), `Akzentfarbe im Hallenmodus abgedunkelt (${accent.slice(0, 40)})`);
  await page.close();
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  await browser.close();
  srv.kill();
  try { rmSync(dataDir, { recursive: true, force: true }); } catch {}
}
check(!errors.length, `Keine JavaScript-Fehler ${errors.slice(0, 3).join(' | ')}`);
const failed = results.filter(r => !r[0]);
console.log(`\n${results.length - failed.length}/${results.length} bestanden`);
process.exit(failed.length ? 1 : 0);

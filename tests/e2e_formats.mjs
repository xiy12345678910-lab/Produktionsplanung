#!/usr/bin/env node
// E2E Formatplanung Tiefziehen (ab V12.8.0): startet server.py mit Testdaten in einem Temp-Ordner
// und spielt den Ablauf im Browser durch (Abteilungsleiter, AV, Viewer, fremder Bereich).
//
// Aufruf:  node tests/e2e_formats.mjs
// Voraussetzung: Python 3 und Node mit Playwright. Läuft nie gegen den Live-Datenordner.
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18779;
const BASE = `http://127.0.0.1:${PORT}/`;
const PASS = 'E2E-Test-1234';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}

const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

// Testdaten: Tiefziehmaschine + zwei geplante Tiefzieh-Aufträge, Benutzer je Rolle.
const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-fmt-'));
const py = `
import ipaddress, json, sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR = Path(${JSON.stringify(dataDir)})
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
server.init_db()
server.create_or_reset_admin("admin", ${JSON.stringify(PASS)})
with server.DB_LOCK, server.db_session() as con:
    old = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
    new = json.loads(json.dumps(old))
    new["machines"].append({"id": "tz1", "name": "TZ 1", "departmentId": "thermoforming", "setupMinutes": 0, "start": "2026-09-07T06:30", "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 1, "crew": 1})
    def step(i, fs, qty):
        return {"id": f"ws_tz{i}", "sequence": i * 10, "planningType": "MACHINE", "pos": i * 10, "departmentId": "thermoforming", "projectId": "", "predecessorIds": [], "fs": fs, "ab": "", "wt": "", "machineId": "tz1", "altMachineId": "", "allowAlternative": False, "order": fs, "articleNo": "", "description": f"Schale {i}", "targetQty": qty, "dueDate": "", "baselinePlan": None, "hours": 2, "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none", "requiredStart": "", "requiredFinish": "", "createdAt": "2026-10-01T08:00:00Z", "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "", "pausedAt": "", "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": ""}
    new["workSteps"] += [step(1, "FS 7001", 100), step(2, "FS 7002", 60)]
    ok, code, reason = server.validate_state(old, new)
    if not ok:
        print("SEED-FEHLER", code, reason, flush=True); sys.exit(1)
    con.execute("UPDATE state SET json=?, revision=revision+1 WHERE id=1", (json.dumps(new, ensure_ascii=False),))
    for name, role, dep in (("tzlead", "department_lead", "thermoforming"), ("av", "production_planning", ""), ("leser", "viewer", ""), ("cnclead", "department_lead", "cnc")):
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

async function open(user) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.on('pageerror', e => errors.push(`${user}: ${e.message}`));
  await page.goto(BASE);
  await page.fill('#loginUser', user);
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(400);
  return page;
}
// Serverstand direkt lesen (mit der Sitzung der Seite)
const serverState = page => page.evaluate(async () => (await (await fetch('/api/state', { cache: 'no-store' })).json()).data);
// Wartet, bis der Client gespeichert hat und der Server die Bedingung erfüllt.
async function until(page, fn, label, ms = 8000) {
  const t0 = Date.now();
  let st;
  while (Date.now() - t0 < ms) {
    st = await serverState(page);
    try { if (fn(st)) { check(true, label); return st; } } catch {}
    await page.waitForTimeout(250);
  }
  const err = await page.evaluate(() => document.getElementById('errorModal')?.classList.contains('show') ? document.getElementById('errorModal').innerText.replace(/\s+/g, ' ').slice(0, 300) : '');
  check(false, label + (err ? ' · Meldung: ' + err : ''));
  return st;
}
async function answer(page, value) {
  await page.waitForSelector('#askModal.show');
  if (value !== undefined) await page.fill('#askInput', String(value));
  await page.click('#askOk');
  await page.waitForTimeout(300);
}
const openDetails = page => page.evaluate(() => document.querySelectorAll('#formats details.fmMaster').forEach(d => d.open = true));

try {
  // ------------------------------------------------------------------ Abteilungsleitung Tiefziehen
  const lead = await open('tzlead');
  check(await lead.locator('#navFormats').isVisible(), 'Abteilungsleitung: Navigation "Formate" sichtbar');
  await lead.click('#navFormats');
  await lead.waitForTimeout(300);
  check(await lead.evaluate(() => document.querySelector('.view.active')?.id) === 'formats', 'Ansicht "Formate" öffnet');
  check(await lead.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'Formate: kein seitlicher Seiten-Scroll');

  await openDetails(lead);
  await lead.click('#fmgDefaults');
  await until(lead, s => (s.baseFormats || []).filter(g => g.departmentId === 'thermoforming').length === 4, 'Grundformate (4 Vorschläge) auf dem Server');

  await openDetails(lead);
  await lead.click('[data-fmt-add="tz1"]');
  await answer(lead, 12);
  await answer(lead, 'Standard');
  await until(lead, s => s.machines.find(m => m.id === 'tz1')?.takte?.[0]?.sec === 12, 'Takt 12 s an Maschine TZ 1 gespeichert');

  await lead.click('#fmNew');
  let st = await until(lead, s => (s.formats || []).length === 1, 'Neues Format angelegt');
  const fmtNumber = st.formats[0].number;
  check(st.formats[0].baseId && st.formats[0].L === 1000 || st.formats[0].L > 0, `Format hat Grundformat/Maße (${st.formats[0].L}×${st.formats[0].B})`);

  // Größtes Grundformat wählen, damit alle Werkzeuge passen
  const big = st.baseFormats.find(g => g.L === 1500);
  await lead.selectOption('#fmBaseSel', big.id);
  await until(lead, s => s.formats[0].L === 1500 && s.formats[0].B === 1000, 'Grundformat 1500×1000 übernommen');

  check(await lead.locator('[data-fm-take]').count() === 2, 'Zwei offene Tiefzieh-Aufträge „Auf Format“ angeboten');
  await lead.locator('[data-fm-take]').first().click();
  await lead.waitForTimeout(400);
  await lead.locator('[data-fm-take]').first().click();
  st = await until(lead, s => s.formats[0].tools.length === 2 && s.formats[0].tools.every(t => t.stepId), 'Beide Aufträge auf dem Format (mit Verknüpfung)');

  const t1 = st.formats[0].tools.find(t => t.fs === 'FS 7001').id;
  const setTool = async (id, k, v) => { const el = lead.locator(`[data-fmtool="${id}"][data-k="${k}"]`); await el.fill(String(v)); await el.press('Tab'); await lead.waitForTimeout(350); };
  await setTool(t1, 'wkz', 'wkz-100');
  await setTool(t1, 'l', 300);
  await setTool(t1, 'b', 200);
  await setTool(t1, 'h', 80);
  await setTool(t1, 'n', 2);
  st = await until(lead, s => { const t = s.formats[0].tools.find(x => x.id === t1); return t.wkz === 'WKZ-100' && t.l === 300 && t.n === 2; }, 'WKZ-Nr. (Großschrift), Maße und Nutzen gespeichert');

  // Laufzeit: FS 7001 100 Stk / 2 Nutzen = 50 Takte, FS 7002 60 Stk / 1 = 60 Takte → 60 Takte × 12 s = 0,2 h
  const kpi = await lead.evaluate(() => [...document.querySelectorAll('.fmKpis b')].map(b => b.textContent));
  check(kpi[0] === '60', `Takte = 60 (${kpi[0]})`);
  check(kpi[2] === '0,2 h', `Laufzeit = 0,2 h (${kpi.join(' | ')} · Takt-ID ${st.formats[0].taktId} · Maschine ${st.formats[0].machineId} · Takte ${JSON.stringify(st.machines.find(m => m.id === 'tz1').takte)})`);
  check(await lead.locator('#fmSvg .fmTool').count() === 3, 'Draufsicht zeigt 3 Werkzeuge (2 Nutzen + 1)');

  await lead.click('#fmPlan');
  await answer(lead);
  st = await until(lead, s => s.workSteps.some(o => o.formatId === s.formats[0].id), 'Format-Auftrag im Wochenplan (Server)');
  const fo = st.workSteps.find(o => o.formatId);
  check(!st.workSteps.some(o => ['ws_tz1', 'ws_tz2'].includes(o.id)), 'Übernommene Einzelaufträge ersetzt');
  check(fo.fs === fmtNumber && fo.machineId === 'tz1' && Math.abs(fo.hours - 0.2) < 1e-9 && fo.targetQty === 160, `Format-Auftrag: FS ${fo.fs}, ${fo.hours} h, ${fo.targetQty} Stk`);
  check(st.formats[0].workStepId === fo.id, 'Format kennt seinen Auftrag');

  // Änderung am Format aktualisiert den geplanten Auftrag
  await setTool(t1, 'n', 1);
  await until(lead, s => Math.abs(s.workSteps.find(o => o.formatId).hours - 100 * 12 / 3600) < 0.01, 'Nutzen-Änderung aktualisiert Laufzeit des Format-Auftrags');

  await lead.click('#fmStore');
  await lead.waitForSelector('#askModal.show');
  check(await lead.evaluate(() => document.getElementById('askInput').getAttribute('list') === 'fmLagerList'), 'Lagerort-Dialog mit Vorschlagsliste');
  await answer(lead, 'Regal A3');
  st = await until(lead, s => s.formats[0].status === 'stored' && s.formats[0].lager?.ort === 'Regal A3' && s.formats[0].lager?.by === 'tzlead', 'Eingelagert mit Lagerort und Benutzer');

  await lead.fill('#fmSearch', 'wkz-100');
  await lead.waitForTimeout(300);
  const hit = await lead.evaluate(() => document.querySelector('.fmItem.stored')?.textContent || '');
  check(hit.includes('Regal A3') && hit.includes('tzlead hat es hier abgelegt'), 'Suche nach WKZ-Nr. findet eingelagertes Format mit Lagerort');
  await lead.fill('#fmSearch', 'FS 7002');
  await lead.waitForTimeout(300);
  check(await lead.locator('.fmItem.stored').count() === 1, 'Suche nach FS-Nr. findet eingelagertes Format');
  await lead.fill('#fmSearch', 'gibtsnicht');
  await lead.waitForTimeout(300);
  check(await lead.locator('.fmItem').count() === 0, 'Suche ohne Treffer zeigt kein Format');
  await lead.fill('#fmSearch', '');
  await lead.waitForTimeout(200);

  // WKZ-Maße aus früherem Format übernehmen
  await lead.click('#fmNew');
  await until(lead, s => s.formats.length === 2, 'Zweites Format angelegt');
  await lead.click('#fmAddTool');
  st = await until(lead, s => s.formats[1].tools.length === 1, 'Werkzeug manuell hinzugefügt');
  const t2 = st.formats[1].tools[0].id;
  await setTool(t2, 'wkz', 'WKZ-100');
  await until(lead, s => { const t = s.formats[1].tools[0]; return t.l === 300 && t.b === 200 && t.h === 80; }, 'WKZ-Maße aus früherem Format übernommen');
  await lead.close();

  // ------------------------------------------------------------------ AV: Formate ja, Stammdaten nein
  const av = await open('av');
  await av.click('#navFormats');
  await av.waitForTimeout(300);
  await openDetails(av);
  check(await av.locator('#fmNew').count() === 1, 'AV: darf Formate anlegen');
  check(await av.locator('#fmgAdd').count() === 0 && await av.locator('[data-fmt-add]').count() === 0, 'AV: keine Pflege von Grundformaten/Takten');
  await av.click('#fmNew');
  await until(av, s => s.formats.length === 3, 'AV: Format auf dem Server gespeichert');
  const avDenied = await av.evaluate(async () => {
    const r = await (await fetch('/api/state', { cache: 'no-store' })).json();
    r.data.baseFormats.push({ id: 'fg_av', departmentId: 'thermoforming', name: 'AV', L: 600, B: 400 });
    const res = await fetch('/api/state', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': (await (await fetch('/api/health')).json()).version }, body: JSON.stringify({ revision: r.revision, data: r.data, action: 'Test' }) });
    return res.status;
  });
  check(avDenied === 403, `AV: Grundformat per API abgelehnt (${avDenied})`);
  await av.close();

  // ------------------------------------------------------------------ Viewer: nur lesen
  const viewer = await open('leser');
  const vis = await viewer.locator('#navFormats').isVisible();
  if (vis) {
    await viewer.click('#navFormats');
    await viewer.waitForTimeout(300);
    check(await viewer.locator('#fmNew').count() === 0, 'Viewer: kein „+ Neues Format“');
    check(await viewer.evaluate(() => [...document.querySelectorAll('#fmEdit input,#fmEdit select')].every(x => x.disabled)), 'Viewer: Formatfelder gesperrt');
  } else check(true, 'Viewer: Formate ausgeblendet');
  await viewer.close();

  // ------------------------------------------------------------------ fremde Abteilungsleitung: Server sperrt
  const cnc = await open('cnclead');
  const cncDenied = await cnc.evaluate(async () => {
    const r = await (await fetch('/api/state', { cache: 'no-store' })).json();
    r.data.formats[0].name = 'fremd';
    const res = await fetch('/api/state', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-MP-Client-Version': (await (await fetch('/api/health')).json()).version }, body: JSON.stringify({ revision: r.revision, data: r.data, action: 'Test' }) });
    return [res.status, (await res.json()).code];
  });
  check(cncDenied[0] === 403, `CNC-Leitung: Format Tiefziehen per API abgelehnt (${cncDenied.join(' ')})`);
  await cnc.close();
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

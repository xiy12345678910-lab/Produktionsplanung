#!/usr/bin/env node
// V12.28.1: Werkstattfertigung (Bereich ohne Mengenmeldung, z. B. Formbau). Menschen an Arbeitsplaetzen statt
// Maschinen/Linien: im FA-Dialog kein AV-Termin/WT/Alternative, "Arbeitsplatz", "Starttermin"/"Endtermin",
// Artikel/Beschreibung gross oben, FA unauffaellig. Auftragsliste ohne Menge/Alternative, Spalte "Endtermin".
// "Termin planen" ohne AV-Termin, dafuer "Produktion starten" (gleicher Weg wie ▶). Gegenprobe CNC unveraendert.
//
// Aufruf:  node tests/e2e_werkstatt.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18957;
const BASE = `http://127.0.0.1:${PORT}/`;
const PASS = 'E2E-Werkstatt-1234';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}
const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const USERS = [['fblead', 'department_lead', 'formbau'], ['cnclead', 'department_lead', 'cnc']];
const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-werkstatt-'));
const py = `
import ipaddress, json, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR = Path(${JSON.stringify(dataDir)})
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
server.init_db(seed="werbetechnik")
# Feste Uhr (Mi 07.10.2026, 07:00 Berlin, in der Schicht und ausserhalb der Pause): "Produktion starten"
# prueft serverseitig die Arbeitszeit; mit der echten Uhr haengt das Ergebnis von der Laufzeit ab (CI 09:00 = Pause).
clock_start = time.monotonic()
server.now_iso = lambda: (datetime(2026, 10, 7, 5, tzinfo=timezone.utc) + timedelta(seconds=time.monotonic() - clock_start)).isoformat(timespec="seconds").replace("+00:00", "Z")
server.create_or_reset_admin("admin", ${JSON.stringify(PASS)})
with server.DB_LOCK, server.db_session() as con:
    old = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
    new = json.loads(json.dumps(old))
    new["departments"].append({"id": "formbau", "name": "Formbau", "planningType": "MACHINE", "noQuantity": True})
    base = next(m for m in new["machines"] if m["departmentId"] == "cnc")
    for i in (1, 2):
        m = json.loads(json.dumps(base))
        m.update({"id": f"fb{i}", "name": f"Formbau-Platz {i}", "departmentId": "formbau", "kind": "line", "crew": 1, "effortScaling": True})
        new["machines"].append(m)
    def step(sid, dep, mid, fa, desc):
        return {"id": sid, "sequence": 10 if dep == "cnc" else 20, "planningType": "MACHINE", "pos": 10 if dep == "cnc" else 20, "departmentId": dep, "projectId": "", "predecessorIds": [], "fa": fa, "ab": "", "wt": "", "machineId": mid, "altMachineId": "", "allowAlternative": False, "order": fa or desc, "articleNo": "", "description": desc, "targetQty": 0 if dep == "formbau" else 25, "dueDate": "2026-12-18", "baselinePlan": None, "hours": 4, "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none", "requiredStart": "", "requiredFinish": "", "createdAt": "2026-10-01T08:00:00Z", "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "", "pausedAt": "", "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": ""}
    new["workSteps"] += [step("ws_cnc", "cnc", base["id"], "FA 5001", "Frontplatte"), step("ws_fb", "formbau", "fb1", "", "Form Leuchtkasten")]
    ok, code, reason = server.validate_state(old, new)
    if not ok:
        print("SEED-FEHLER", code, reason, flush=True); sys.exit(1)
    con.execute("UPDATE state SET json=?, revision=revision+1 WHERE id=1", (json.dumps(new, ensure_ascii=False),))
    for name, role, dep in ${JSON.stringify(USERS)}:
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
async function open(user) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, timezoneId: 'Europe/Berlin' });
  await page.clock.install({ time: new Date('2026-10-07T05:00:00Z') });
  page.on('pageerror', e => errors.push(`${user}: ${e.message}`));
  await page.goto(BASE);
  await page.fill('#loginUser', user);
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(500);
  return page;
}
const serverState = page => page.evaluate(async () => (await (await fetch('/api/state', { cache: 'no-store' })).json()).data);
const errText = page => page.evaluate(() => document.getElementById('errorModal')?.classList.contains('show') ? document.getElementById('errorModal').innerText.replace(/\s+/g, ' ') : '');
const waitStep = async (page, pred) => { for (let i = 0; i < 40; i++) { const s = (await serverState(page)).workSteps.find(pred); if (s) return s; await page.waitForTimeout(250); } return null; };
// Sichtbare Feld-Beschriftungen im FA-Dialog
const dialogInfo = page => page.evaluate(() => {
  const vis = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const box = document.querySelector('#orderModal .avOrderDialog'), cs = id => getComputedStyle(document.getElementById(id));
  return {
    labels: [...box.querySelectorAll('.field > label')].filter(vis).map(l => l.textContent.trim()),
    wt: vis(document.getElementById('qWT')), alt: vis(document.getElementById('qAltMachine')), qty: vis(document.getElementById('qQty')),
    machineLabel: document.getElementById('qMachineLabel').textContent, dueLabel: document.getElementById('qDueLabel').textContent,
    anchorLabel: document.getElementById('qAnchorLabel').textContent, dueHint: document.getElementById('qDueHint')?.textContent || '',
    faSize: parseFloat(cs('qFA').fontSize), faWeight: Number(cs('qFA').fontWeight), descSize: parseFloat(cs('qDesc').fontSize), descWeight: Number(cs('qDesc').fontWeight),
    descTop: document.getElementById('qDescField').getBoundingClientRect().top, faTop: document.getElementById('qFAField').getBoundingClientRect().top,
    noQty: box.classList.contains('noQty'), focus: document.activeElement?.id || '',
  };
});
const ordersInfo = page => page.evaluate(() => ({
  heads: [...document.querySelectorAll('.ordersTable thead th')].filter(th => th.getClientRects().length).map(th => th.textContent.trim()),
  hint: document.getElementById('ordersHint')?.textContent || document.querySelector('#orders .sectionHead .hint').textContent,
  filterAll: document.querySelector('#filterMachine option')?.textContent || '',
  sub: document.getElementById('pageSub').textContent,
  rows: Object.fromEntries([...document.querySelectorAll('#ordersBody tr[data-id]')].map(tr => [tr.dataset.id, {
    qty: !!tr.querySelector('[data-f="targetQty"]'), alt: !!tr.querySelector('[data-f="altMachineId"]'), altText: tr.innerText.includes('Alternative'),
    title: tr.querySelector('.ordTitle')?.value || tr.querySelector('.ordTitle')?.textContent || '', cells: tr.children.length,
  }])),
}));

try {
  // ---- Abteilungsleitung Formbau (Bereich ohne Mengenmeldung) ----
  const fb = await open('fblead');
  await fb.click('#navPlan'); await fb.waitForTimeout(300);
  const plan = await fb.evaluate(() => ({ sub: document.getElementById('pageSub').textContent, corner: document.querySelector('#board .bhCorner')?.textContent || '', late: document.getElementById('mLateSub')?.textContent || document.querySelector('#mLate')?.parentElement?.querySelector('.metricSub')?.textContent || '' }));
  check(plan.sub.includes('Formbau · Arbeitsplätze · Endtermine') && !plan.sub.includes('AV-Termin'), `Wochenplan-Kopf: Arbeitsplätze · Endtermine (${plan.sub})`);
  check(plan.corner === 'Arbeitsplatz', `Wochenplan: Ressourcenspalte heisst Arbeitsplatz (${plan.corner})`);
  check(plan.late.startsWith('Endtermin überschritten'), `KPI: Endtermin überschritten / nicht planbar (${plan.late})`);

  await fb.click('#navList'); await fb.waitForTimeout(300);
  await fb.click('#addOrder2'); await fb.waitForSelector('#orderModal.show');
  const d = await dialogInfo(fb);
  check(!d.labels.some(l => /AV-Termin/.test(l)) && !d.wt && !d.alt && !d.labels.some(l => /Projektnummer|WT|Alternative/.test(l)), `FA-Dialog Formbau: kein AV-Termin, kein WT/Projektnummer, keine Alternative (${d.labels.join(' | ')})`);
  check(d.machineLabel === 'Arbeitsplatz' && d.labels.includes('Arbeitsplatz') && !d.labels.some(l => /Maschine|Linie/.test(l)), `FA-Dialog Formbau: Ressource heisst Arbeitsplatz (${d.machineLabel})`);
  check(d.dueLabel === 'Endtermin' && d.labels.includes('Endtermin') && d.anchorLabel === 'Starttermin' && d.labels.includes('Starttermin') && d.dueHint.includes('Endtermin'), `FA-Dialog Formbau: Starttermin und Endtermin (${d.anchorLabel} / ${d.dueLabel} / ${d.dueHint})`);
  check(d.noQty && d.descSize > d.faSize * 1.2 && d.descWeight >= 700 && d.faWeight < 600 && d.descTop < d.faTop, `FA-Dialog Formbau: Artikel/Beschreibung gross, fett und oben, FA unauffaellig (${JSON.stringify({ descSize: d.descSize, faSize: d.faSize, descWeight: d.descWeight, faWeight: d.faWeight })})`);
  check(!d.qty && d.focus === 'qDesc' && !d.labels.includes('Konfektionsstunden') && d.labels.some(l => /^(Sollstunden|Personenstunden) \*$/.test(l)), `FA-Dialog Formbau: keine Menge, Stunden Pflicht, Fokus auf Titel (${d.focus})`);
  await fb.fill('#qDesc', 'Form Messestand'); await fb.fill('#qHours', '6');
  const resp = fb.waitForResponse(r => r.url().includes('/api/state') && r.request().method() !== 'GET', { timeout: 10000 }).catch(() => null);
  await fb.click('#createOrder');
  const r = await resp;
  const created = await waitStep(fb, s => s.description === 'Form Messestand');
  check(r?.status() === 200 && created && created.departmentId === 'formbau' && !created.altMachineId && !created.wt && created.hours === 6, `Anlegen ohne FA mit Titel + Stunden: Server ${r?.status()} (${JSON.stringify(created && { fa: created.fa, order: created.order, alt: created.altMachineId, wt: created.wt })}) ${await errText(fb)}`);
  await fb.waitForTimeout(300);
  const ol = await ordersInfo(fb);
  check(!ol.heads.includes('Menge') && ol.heads.includes('Endtermin') && ol.heads.includes('Arbeitsplatz') && !ol.heads.some(h => /AV-Termin|Maschine/.test(h)), `Auftragsliste Formbau: Spalten ohne Menge, mit Arbeitsplatz/Endtermin (${ol.heads.join(' | ')})`);
  check(ol.hint.includes('Rot = Endtermin') && ol.sub.includes('Endtermin') && !ol.sub.includes('AV-Termin') && ol.filterAll === 'Alle Arbeitsplätze', `Auftragsliste Formbau: Hinweise/Filter (${ol.sub} · ${ol.hint} · ${ol.filterAll})`);
  const fr = ol.rows.ws_fb || {};
  check(fr.title === 'Form Leuchtkasten' && !fr.qty && !fr.alt && !fr.altText && fr.cells === ol.heads.length, `Auftragsliste Formbau: Titel als Hauptzeile, keine Menge, keine Alternative (${JSON.stringify(fr)})`);
  // V12.28.2: Abteilungsleitung aendert Endtermin und Titel nachtraeglich; ohne FA zieht der Auftragsname mit.
  if (created) {
    const row = `#ordersBody tr[data-id="${created.id}"]`;
    check(await fb.locator(`${row} [data-f="dueDate"]`).isEnabled(), 'Auftragsliste Formbau: Endtermin fuer die Abteilungsleitung aenderbar');
    await fb.fill(`${row} [data-f="dueDate"]`, '2026-12-01'); await fb.press(`${row} [data-f="dueDate"]`, 'Tab');
    const due = await waitStep(fb, s => s.id === created.id && s.dueDate === '2026-12-01');
    check(!!due, `Endtermin nachtraeglich geaendert und am Server gespeichert (${due?.dueDate || 'nein'}) ${await errText(fb)}`);
    await fb.fill(`${row} .ordTitle`, 'Form Messestand XL'); await fb.press(`${row} .ordTitle`, 'Tab');
    const ren = await waitStep(fb, s => s.id === created.id && s.description === 'Form Messestand XL');
    check(ren && ren.order === 'Form Messestand XL' && ren.fa === 'Form Messestand XL' && ren.faNumber === ren.fa, `Titel ohne FA geaendert: Auftragsname zieht mit (${JSON.stringify(ren && { order: ren.order, fa: ren.fa })})`);
  }
  // Termin planen -> Produktion starten
  await fb.click('#ordersBody tr[data-id="ws_fb"] [data-act="start"]'); await fb.waitForSelector('#startModal.show');
  const sm = await fb.evaluate(() => ({ text: document.querySelector('#startModal .modalBox').innerText, due: document.getElementById('manualDue').getClientRects().length > 0, btn: document.getElementById('startProdNow').getClientRects().length > 0 }));
  check(!sm.text.includes('AV-Termin') && !sm.due && sm.btn && sm.text.includes('Produktion starten'), `Termin planen (Formbau): kein AV-Termin, Knopf "Produktion starten" (${sm.text.replace(/\s+/g, ' ').slice(0, 200)})`);
  await fb.click('#startProdNow'); await fb.waitForSelector('#prodStartModal.show');
  await fb.click('#psConfirm');
  const running = await waitStep(fb, s => s.id === 'ws_fb' && s.status === 'running');
  check(!!running, `"Produktion starten" startet den Auftrag (Status laufend) ${await errText(fb)}`);
  await fb.close();

  // ---- Gegenprobe: Abteilungsleitung CNC unveraendert ----
  const cnc = await open('cnclead');
  await cnc.click('#navPlan'); await cnc.waitForTimeout(300);
  const cplan = await cnc.evaluate(() => ({ sub: document.getElementById('pageSub').textContent, corner: document.querySelector('#board .bhCorner')?.textContent || '', late: document.getElementById('mLateSub')?.textContent || document.querySelector('#mLate')?.parentElement?.querySelector('.metricSub')?.textContent || '' }));
  check(cplan.sub.includes('Maschinen & Linien · AV-Termine') && cplan.corner !== 'Arbeitsplatz' && cplan.late.startsWith('AV-Termin'), `Gegenprobe CNC Wochenplan unveraendert (${cplan.sub} · ${cplan.corner} · ${cplan.late})`);
  await cnc.click('#navList'); await cnc.waitForTimeout(300);
  const col = await ordersInfo(cnc);
  check(col.heads.includes('Menge') && col.heads.includes('AV-Termin') && col.heads.includes('Maschine / Linie') && !col.heads.includes('Endtermin'), `Gegenprobe CNC: Spalten Maschine / Linie, Menge, AV-Termin (${col.heads.join(' | ')})`);
  const cr = col.rows.ws_cnc || {};
  check(cr.qty && cr.alt && cr.altText, `Gegenprobe CNC: Menge und Alternative in der Zeile (${JSON.stringify(cr)})`);
  check(await cnc.locator('#ordersBody tr[data-id="ws_cnc"] [data-f="dueDate"]').isDisabled(), 'Gegenprobe CNC: AV-Termin fuer die Abteilungsleitung gesperrt');
  await cnc.click('#addOrder2'); await cnc.waitForSelector('#orderModal.show');
  const cd = await dialogInfo(cnc);
  check(cd.labels.some(l => l.startsWith('AV-Termin')) && cd.alt && cd.qty && cd.machineLabel === 'Maschine' && !cd.noQty && cd.faWeight >= 700 && cd.focus === 'qFA', `Gegenprobe CNC: FA-Dialog mit AV-Termin, Maschine, Menge, Alternative, FA gross (${cd.labels.join(' | ')})`);
  await cnc.click('#cancelModal');
  await cnc.click('#ordersBody tr[data-id="ws_cnc"] [data-act="start"]'); await cnc.waitForSelector('#startModal.show');
  const csm = await cnc.evaluate(() => ({ text: document.querySelector('#startModal .modalBox').innerText, due: document.getElementById('manualDue').getClientRects().length > 0, btn: document.getElementById('startProdNow')?.getClientRects().length > 0 }));
  check(csm.text.includes('AV-Termin') && csm.due && !csm.btn, 'Gegenprobe CNC: Termin planen mit AV-Termin, ohne Knopf "Produktion starten"');
  await cnc.click('#cancelStart');
  await cnc.close();

  // ---- Gemischte Ansicht (Admin, alle Bereiche): je Zeile ----
  const admin = await open('admin');
  await admin.click('#navList'); await admin.waitForTimeout(300);
  const al = await ordersInfo(admin);
  const acr = al.rows.ws_cnc || {}, afr = Object.entries(al.rows).find(([, v]) => v.title === 'Form Messestand XL')?.[1] || {};
  check(al.heads.includes('Menge') && al.heads.includes('AV-Termin') && acr.qty && acr.alt && afr.title && !afr.qty && !afr.alt && afr.cells === acr.cells, `Alle Bereiche: allgemeine Spalten, Formbau-Zeile ohne Menge/Alternative (${JSON.stringify({ acr, afr })})`);
  // Planungsart-Bezeichnung (global, gespeicherter Wert bleibt 'line')
  await admin.click('#navGF'); await admin.waitForTimeout(400);
  const logic = await admin.evaluate(() => ({ opts: [...document.querySelectorAll('#gfDeptAdmin option')].map(o => o.value + '=' + o.textContent), rowText: [...document.querySelectorAll('#gfDeptAdmin .depRow')].map(r => r.textContent).join(' | ') }));
  check(logic.opts.includes('line=Personenstunden · Werkstatt/Arbeitsplatz (Besetzung teilt die Stunden)') && !logic.opts.some(o => o.includes('Personenstunden · Linie')), `Planungsart: Personenstunden · Werkstatt/Arbeitsplatz (${[...new Set(logic.opts.filter(o => o.startsWith('line=')))].join(', ')})`);
  check(logic.rowText.includes('Personenstunden · Werkstatt/Arbeitsplatz') && !logic.rowText.includes('Personenstunden · Linie'), 'Bereichsliste: Formbau als Personenstunden · Werkstatt/Arbeitsplatz (' + logic.rowText.replace(/\s+/g, ' ').slice(0, 600) + ')');
  await admin.close();
} catch (e) {
  check(false, 'Ablauf: ' + (e?.stack || e?.message || e));
} finally {
  check(!errors.length, 'keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser.close();
  srv.kill();
  try { rmSync(dataDir, { recursive: true, force: true }); } catch {}
}
const failed = results.filter(r => !r[0]).length;
console.log(`\n${results.length - failed}/${results.length} bestanden`);
process.exit(failed ? 1 : 0);

#!/usr/bin/env node
// Production users can follow their own project's live progress without gaining project write access.
// node tests/e2e_project_live.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18837, BASE = `http://127.0.0.1:${PORT}/`, PASS = 'Project-Live-Test-1234';
async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  return import(pathToFileURL(path.join(execSync('npm root -g').toString().trim(), 'playwright', 'index.mjs')).href);
}
const results = [], errors = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };
const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-project-live-'));
const py = `
import ipaddress, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR=Path(${JSON.stringify(dataDir)})
server.DB_PATH=server.DATA_DIR/'maschinenplanung.sqlite3'
server.ALLOWED_NETWORK=ipaddress.ip_network('127.0.0.0/8')
server.init_db(seed='werbetechnik')
clock_start=time.monotonic()
server.now_iso=lambda:(datetime(2026,10,7,5,tzinfo=timezone.utc)+timedelta(seconds=time.monotonic()-clock_start)).isoformat(timespec='seconds').replace('+00:00','Z')
server.create_or_reset_admin('admin',${JSON.stringify(PASS)})
httpd=server.MPHTTPServer(('127.0.0.1',${PORT}),server.Handler)
print('READY',flush=True)
httpd.serve_forever()
`;
const srv = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'] });
await new Promise((resolve, reject) => {
  const timeout = setTimeout(() => reject(new Error('Server startet nicht')), 20000);
  srv.stdout.on('data', d => { if (String(d).includes('READY')) { clearTimeout(timeout); resolve(); } });
  srv.on('exit', c => reject(new Error('Server beendet: ' + c)));
});
const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
async function installHook(page) {
  await page.route(u => ['/', '/index.html'].includes(new URL(u).pathname), async route => {
    const resp = await route.fetch(); let body = await resp.text();
    const end = body.lastIndexOf('})();');
    body = body.slice(0, end) + 'window.__t=src=>eval(src);\n' + body.slice(end);
    const headers = { ...resp.headers() }; delete headers['content-security-policy']; delete headers['content-length'];
    await route.fulfill({ status: resp.status(), headers, body });
  });
}
const ev = (page, fn, arg) => page.evaluate(([src, a]) => window.__t('(' + src + ')')(a), [fn.toString(), arg]);
async function login(page, username) {
  await page.clock.install({ time: new Date('2026-10-07T05:00:00Z') });
  await installHook(page); await page.goto(BASE);
  await page.waitForFunction(() => document.getElementById('loginModal')?.classList.contains('show'), null, { timeout: 8000 });
  await page.fill('#loginUser', username); await page.fill('#loginPassword', PASS); await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForFunction(u => window.__t('lastServerState !== null && serverReachable && sessionUser.username === ' + JSON.stringify(u)), username);
}

try {
  const adminCtx = await browser.newContext({ timezoneId: 'Europe/Berlin' }), admin = await adminCtx.newPage();
  admin.on('pageerror', e => errors.push('admin: ' + e.message));
  await login(admin, 'admin');
  const seed = await ev(admin, async () => {
    const machine = data.machines[0], departmentId = machine.departmentId;
    const role = await api('/api/users', { method: 'POST', body: JSON.stringify({ username: 'prod-live', password: 'Project-Live-Test-1234', role: 'production', departmentId }) });
    if (role.r.status !== 201) throw new Error('Rolle: ' + role.r.status + ' department=' + departmentId + ' machines=' + JSON.stringify(data.machines.slice(0, 2)) + ' departments=' + JSON.stringify(data.departments.slice(0, 2)) + ' ' + JSON.stringify(role.body));
    machine.start = '2026-10-07T06:00'; machine.setupMinutes = 0;
    const project = { id: 'p_live', number: 'P-100', customer: 'Live Kunde', name: 'Live Projekt', phase: 'accepted', ab: 'AB-100', dueDate: '2026-10-30', processes: [], log: [], customerPlan: { version: 1, dueDate: '2026-10-30', steps: [{ areaId: departmentId, title: 'Geheimtermin', startDate: '2026-10-07', dueDate: '2026-10-10' }], secret: 'KALKULATION' }, offer: {}, development: { secret: 'ENTWICKLUNG' } };
    data.projects.push(project);
    const step = { id: 'fa_live', fa: 'FA-LIVE-1', faNumber: 'FA-LIVE-1', sourceType: 'PROJECT', sourceId: 'p_live', projectId: 'p_live', departmentId, planningType: 'MACHINE', sequence: nextProjectSequence('p_live'), pos: nextOrderPos(), machineId: machine.id, altMachineId: '', allowAlternative: false, order: 'FA-LIVE-1', articleNo: 'ART-1', description: 'Live-Testauftrag', hours: 8, targetQty: 100, goodQty: 0, scrapQty: 0, remainingQty: 100, status: 'planned', direction: 'forward', anchorMode: 'none', predecessorIds: [], baselinePlan: null };
    const other = data.machines.find(m => m.departmentId !== departmentId);
    data.workSteps.push(step);
    if (other) {
      data.projects.push({ id: 'p_foreign', number: 'P-SECRET', customer: 'Foreign Customer', phase: 'pm', processes: [], log: [] });
      const foreignPos = nextOrderPos();
      data.workSteps.push({ ...step, id: 'fa_foreign', fa: 'FA-SECRET-1', order: 'FA-SECRET-1', sequence: foreignPos, pos: foreignPos, projectId: 'p_foreign', sourceId: 'p_foreign', machineId: other.id, departmentId: other.departmentId });
    }
    const ok = releaseOrder(step);
    if (!ok) throw new Error('FA kann nicht freigegeben werden');
    data.workSteps.push({ ...step, id: 'fa_plan', fa: 'FA-PLAN-1', faNumber: 'FA-PLAN-1', order: 'FA-PLAN-1', sequence: nextProjectSequence('p_live'), pos: nextOrderPos(), hours: 8, status: 'planned', baselinePlan: null });
    save('Live-Projekt-Testfixture');
    if (!await flushNow()) throw new Error(document.getElementById('errorModal').textContent);
    return { departmentId, hasForeign: !!other };
  });
  const productionCtx = await browser.newContext({ timezoneId: 'Europe/Berlin' }), production = await productionCtx.newPage();
  production.on('pageerror', e => errors.push('production: ' + e.message));
  await login(production, 'prod-live');
  check(await ev(production, () => viewAllowed('projects')), 'Production-Rolle darf Projekte öffnen');
  await production.click('#navOrders');
  await production.locator('[data-project-open="p_live"]').waitFor({ state: 'visible' });
  await production.click('[data-project-open="p_live"]');
  const rowSelector = '[data-testid="project-production-row-fa_live"]';
  await production.getByTestId('project-live-scope').waitFor({ state: 'visible' });
  check((await production.getByTestId('project-live-scope').textContent()).includes('Ihr Bereich'), 'Projektmodal kennzeichnet den eigenen Bereich');
  check(await production.getByTestId('project-production-progress').isVisible(), 'Bereichsfortschritt im Projektmodal sichtbar');
  check(await production.locator('[data-project-open="p_foreign"]').count() === 0 && !(await production.locator('#projectModalBody').textContent()).includes('FA-SECRET-1'), 'Fremdprojekt und fremde FA bleiben unsichtbar');
  const modalText = await production.locator('#projectModalBody').textContent();
  check(modalText.includes('Freigegeben'), 'Projektmodal zeigt den FA-Status');
  await production.locator('[data-testid="project-production-row-fa_live"] .fLine').click();
  check((await production.locator(rowSelector).textContent()).includes('Plan'), 'Projektmodal zeigt die FA-Planinformationen');
  check(!/KALKULATION|ANGEBOT|ENTWICKLUNG/.test(modalText), 'Kommerzielle Projektfelder sind nicht sichtbar');
  check(await production.locator('#cpDownload').count() === 0 && !(await production.locator('#projectModalBody').textContent()).includes('Geheimtermin'), 'Kundenplan und Kundenausgangsexport bleiben ausgeblendet');
  check(await production.locator(rowSelector).count() === 1 && (await production.locator(rowSelector).textContent()).includes('Gut 0') && (await production.locator(rowSelector).textContent()).includes('Rest 100'), 'FA-Zeile zeigt aktuelle Gut-/Ausschuss-/Restmengen');
  const planRowSelector = '[data-testid="project-production-row-fa_plan"]';
  await production.locator(planRowSelector + ' .fLine').click();
  check((await production.locator(planRowSelector).textContent()).includes('8 h'), 'Offene Plan-FA zeigt Sollstunden');
  check(await production.locator('#projectModalBody input:enabled, #projectModalBody select:enabled, #projectModalBody [data-project-go], #projectModalBody [data-proc-add], #projectModalBody [data-proc-del], #projectModalBody [data-proc-take], #projectModalBody [data-fshow]').count() === 0, 'Produktionsansicht im Projektmodal hat keine editierbaren Felder oder Aktionen');
  const initial = modalText;
  const planUpdated = await ev(admin, async () => {
    const step = data.workSteps.find(x => x.id === 'fa_plan'); step.hours = 12;
    save('Planstunden geändert'); const ok = await flushNow(); return { ok, error: document.getElementById('errorModal').textContent };
  });
  check(planUpdated.ok, 'Admin ändert Planstunden des FA' + (planUpdated.ok ? '' : ' · ' + planUpdated.error));
  if (!planUpdated.ok) throw new Error('Planstunden konnten nicht gespeichert: ' + planUpdated.error);
  await production.waitForFunction(() => {
    const row = document.querySelector('[data-testid="project-production-row-fa_plan"]');
    return row?.textContent.includes('12 h');
  }, null, { timeout: 12000 });
  check(true, 'Offenes Projektmodal aktualisiert Planstunden ohne Reload');
  const response = await ev(admin, async () => {
    const start = await api('/api/production/fa_live/start', { method: 'POST', body: JSON.stringify({ requestId: 'project-live-start-1' }) });
    if (start.r.status !== 200) return { step: 'start', status: start.r.status, body: start.body };
    const partial = await api('/api/production/fa_live/partial', { method: 'POST', body: JSON.stringify({ requestId: 'project-live-partial-1', goodQty: 30, scrapQty: 5 }) });
    return { step: 'partial', status: partial.r.status, body: partial.body };
  });
  check(response.status === 200, 'Admin meldet Produktion über Produktions-API (HTTP ' + response.status + ')');
  await production.waitForFunction(() => {
    const text = document.querySelector('[data-testid="project-production-row-fa_live"]')?.textContent || '';
    return text.includes('Gut 30') && text.includes('Ausschuss 5') && text.includes('Rest 65');
  }, null, { timeout: 12000 });
  check(true, 'Geöffneter Projektmodal zeigt neue Gut-/Ausschuss-/Restmengen ohne Reload');
  const finished = await ev(admin, async () => {
    const result = await api('/api/production/fa_live/finish', { method: 'POST', body: JSON.stringify({ requestId: 'project-live-finish-1', goodQty: 65, scrapQty: 0 }) });
    return result.r.status;
  });
  check(finished === 200, 'Admin meldet FA über Produktions-API fertig (HTTP ' + finished + ')');
  await production.waitForFunction(() => {
    const text = document.querySelector('[data-testid="project-production-row-fa_live"]')?.textContent || '';
    return text.includes('Gut 95') && text.includes('Ausschuss 5') && text.includes('Rest 0');
  }, null, { timeout: 12000 });
  check(true, 'Geöffneter Projektmodal zeigt Fertigmeldung ohne Reload');
  const prodState = await ev(production, async () => {
    const r = await api('/api/state'); return { status: r.r.status, data: r.body.data };
  });
  check(prodState.status === 200 && !prodState.data.projects[0].customerPlan && !prodState.data.projects[0].offer && !prodState.data.projects[0].development, 'Production-API maskiert Kundenplan, Angebot und Entwicklung');
  const attemptedWrite = await ev(production, async () => {
    const r = await api('/api/state');
    r.body.data.projects[0].customer = 'Manipuliert';
    const write = await api('/api/state', { method: 'PUT', body: JSON.stringify({ revision: r.body.revision, data: r.body.data }) });
    return write.r.status;
  });
  check(attemptedWrite === 403, 'Production kann Projekt-/State-Daten nicht schreiben (HTTP 403)');
  await productionCtx.close(); await adminCtx.close();
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + e.stack);
} finally {
  check(errors.length === 0, 'Keine JavaScript-Fehler ' + errors.join(' | '));
  await browser.close(); srv.kill(); rmSync(dataDir, { recursive: true, force: true });
}
console.log(`\n${results.filter(x => x[0]).length}/${results.length} bestanden`);
process.exit(results.every(x => x[0]) ? 0 : 1);

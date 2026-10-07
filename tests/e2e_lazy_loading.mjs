#!/usr/bin/env node
// Regression: render only the authorized active view and refresh cached plans on server revisions.
// node tests/e2e_lazy_loading.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18935, BASE = `http://127.0.0.1:${PORT}/`, PASS = 'Lazy-Loading-Test-1234';
async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  return import(pathToFileURL(path.join(execSync('npm root -g').toString().trim(), 'playwright', 'index.mjs')).href);
}
const results = [], errors = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };
const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-lazy-loading-'));
const py = `
import ipaddress, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR=Path(${JSON.stringify(dataDir)})
server.DB_PATH=server.DATA_DIR/'maschinenplanung.sqlite3'
server.ALLOWED_NETWORK=ipaddress.ip_network('127.0.0.0/8')
server.init_db(seed='werbetechnik')
server.now_iso=lambda:datetime(2026,10,8,5,tzinfo=timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
server.create_or_reset_admin('admin',${JSON.stringify(PASS)})
with server.DB_LOCK, server.db_session() as con:
    old=json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()['json'])
    new=json.loads(json.dumps(old))
    m=next(x for x in new['machines'] if x['id']=='m1')
    m.update(start='2026-10-08T06:00',setupMinutes=0,staffRequired=1,kind='machine')
    new['personnelGate']=True
    new['ui'].update(week='2026-10-05',view='overview',mode='planning')
    new['employees']=[{'id':'e_lazy','name':'Lazy Test Person','departmentId':'cnc','skills':['m1'],'homeMachineId':'m1','homeShift':'auto','weeklyHours':40,'active':True}]
    new['personnelAssignments']=[]
    new['projects']=[{'id':'p_lazy','number':'P-LAZY-1','phase':'accepted','name':'Initial Lazy Project','customer':'Testkunde','ab':'AB-LAZY-1','dueDate':'2026-10-30','processes':[],'log':[]}]
    new['workSteps']=[{'id':'fa_lazy','fa':'FA-LAZY-1','faNumber':'FA-LAZY-1','sourceType':'PROJECT','sourceId':'p_lazy','projectId':'p_lazy','departmentId':'cnc','planningType':'MACHINE','sequence':10,'pos':10,'machineId':'m1','altMachineId':'','allowAlternative':False,'order':'FA-LAZY-1','articleNo':'A-1','description':'Lazy live test','hours':1,'targetQty':100,'goodQty':0,'scrapQty':0,'remainingQty':100,'status':'planned','direction':'forward','anchorMode':'none','predecessorIds':[],'baselinePlan':None,'dueDate':'2026-10-30'}]
    ok,code,reason=server.validate_state(old,new)
    if not ok: print('SEED-FEHLER',code,reason,flush=True);sys.exit(1)
    con.execute('UPDATE state SET json=?,revision=revision+1 WHERE id=1',(json.dumps(new,ensure_ascii=False),))
    for username,role,dep in [('prod_lazy','production','cnc'),('viewer_lazy','viewer','')]:
        salt,digest=server.hash_password(${JSON.stringify(PASS)})
        con.execute('INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)',(username,salt,digest,role,dep,server.now_iso(),server.now_iso()))
httpd=server.MPHTTPServer(('127.0.0.1',${PORT}),server.Handler)
print('READY',flush=True)
httpd.serve_forever()
`;
const srv = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'] });
await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('Server startet nicht')), 20000);
  srv.stdout.on('data', d => { const s = String(d); if (s.includes('SEED-FEHLER')) reject(new Error(s)); if (s.includes('READY')) { clearTimeout(timer); resolve(); } });
  srv.on('exit', c => reject(new Error('Server beendet: ' + c)));
});
const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
async function installHook(page) {
  await page.route(u => ['/', '/index.html'].includes(new URL(u).pathname), async route => {
    const resp = await route.fetch(); let body = await resp.text();
    const anchor = 'renderAll();switchMode(data.ui.mode';
    if (!body.includes(anchor)) throw new Error('Initialer Renderanker fehlt');
    const instrument = `
window.__lazyCalls={};window.__lazyFirst=null;
window.__lazyReset=()=>{for(const k of Object.keys(window.__lazyCalls))window.__lazyCalls[k]=0};
window.__lazyCount=k=>window.__lazyCalls[k]||0;
window.__t=src=>eval(src);
for(const n of ['calcSchedule','renderAllViews','renderMetrics','renderBoard','renderOrders','renderProduction','renderPersonnel','renderAbsences','renderHistory','renderReport','renderAudit','renderSettings','renderGF','renderProjects','renderDepartments','renderFormats','renderProjectModal','absenceImpact']){
  {const old=eval(n);eval(n+'=(...args)=>{window.__lazyCalls[n]=(window.__lazyCalls[n]||0)+1;const result=old(...args);if(n==="renderAllViews"&&!window.__lazyFirst&&renderDataReady())window.__lazyFirst={view:data.ui.view,role:sessionUser.role,calls:{...window.__lazyCalls}};return result}')}
}
`;
    body = body.replace(anchor, instrument + '\n' + anchor);
    const headers = { ...resp.headers() }; delete headers['content-security-policy']; delete headers['content-length'];
    await route.fulfill({ status: resp.status(), headers, body });
  });
}
const ev = (page, fn, arg) => page.evaluate(([src, a]) => window.__t('(' + src + ')')(a), [fn.toString(), arg]);
async function login(page, username) {
  await page.clock.install({time:new Date('2026-10-08T05:00:00Z')});
  await installHook(page); await page.goto(BASE);
  await page.fill('#loginUser', username); await page.fill('#loginPassword', PASS); await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForFunction(u => window.__t('lastServerState!==null && serverReachable && sessionUser.username===' + JSON.stringify(u)), username);
}
const viewIs = (page, id) => page.waitForFunction(v => document.querySelector('.view.active')?.id === v, id);
const count = (page, fn, name) => ev(page, () => window.__lazyCount(name));
const sleepless = async (page, fn, arg) => page.waitForFunction(([src, value]) => window.__t('(' + src + ')')(value), [fn.toString(), arg], { timeout: 8000, polling: 100 });

try {
  const adminCtx = await browser.newContext({ timezoneId: 'Europe/Berlin' }), admin = await adminCtx.newPage();
  admin.on('pageerror', e => errors.push('admin: ' + e.message));
  await admin.clock.install({time:new Date('2026-10-08T05:00:00Z')});
  await installHook(admin); await admin.goto(BASE);
  await admin.fill('#loginUser', 'admin'); await admin.fill('#loginPassword', PASS); await admin.click('#loginBtn');
  await admin.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await sleepless(admin, () => lastServerState !== null && serverReachable, null);
  await sleepless(admin, () => !!window.__lazyFirst, null);
  const first = await ev(admin, () => window.__lazyFirst);
  const firstCount = key => first.calls[key] || 0;
  check(first.view === 'overview' && firstCount('renderAllViews') > 0 && firstCount('renderPersonnel') === 0 && firstCount('renderHistory') === 0 && firstCount('renderSettings') === 0 && firstCount('renderProjects') === 0,
    `Erster akzeptierter Render initialisiert nur die aktive erlaubte Übersicht (${JSON.stringify(first)})`);
  await ev(admin, () => window.__lazyReset());

  await admin.click('#navPersonnel'); await viewIs(admin, 'personnel');
  await sleepless(admin, () => window.__lazyCount('renderPersonnel') > 0, null);
  const personnelCalls = await ev(admin, () => ({ personnel: window.__lazyCount('renderPersonnel'), history: window.__lazyCount('renderHistory'), settings: window.__lazyCount('renderSettings'), projects: window.__lazyCount('renderProjects'), schedules: window.__lazyCount('calcSchedule') }));
  check(personnelCalls.personnel > 0 && personnelCalls.history === 0 && personnelCalls.settings === 0 && personnelCalls.projects === 0,
    `Erst beim Öffnen wird Personal gerendert; inaktive Renderer bleiben unangetastet (${JSON.stringify(personnelCalls)})`);

  await ev(admin, async () => { await notifSavePrefs({ kinds: { risk: false } }); });
  const riskOff = await ev(admin, async () => {
    const rows=[];
    for (const v of ['personnel','history','settings']) {
      window.__lazyReset(); switchView(v); renderAll();
      rows.push({view:v,schedule:window.__lazyCount('calcSchedule'),personnel:window.__lazyCount('renderPersonnel'),history:window.__lazyCount('renderHistory'),settings:window.__lazyCount('renderSettings')});
    }
    return rows;
  });
  check(riskOff.every(x => x.schedule === 0), 'Bei ausgeschalteten Risiko-Benachrichtigungen startet Personal/Historie/System keinen Scheduler: ' + JSON.stringify(riskOff));
  check(riskOff.find(x=>x.view==='history').history>0 && riskOff.find(x=>x.view==='settings').settings>0,
    'Historie und System initialisieren sich beim Öffnen der jeweiligen Ansicht');

  // Return to planning and warm one schedule snapshot, then let another session change real staffing.
  await ev(admin, () => { switchView('overview'); renderAll(); return calcSchedule().fa_lazy?.conflict || ''; });
  const baseline = await ev(admin, () => {const s=calcSchedule().fa_lazy||{};return {value:s.conflict||'',start:s.start?.toISOString()||'',segments:(s.segments||[]).length,revision:serverRevision}});
  await ev(admin, () => { window.__lazyReset(); window.__lazyBaseRevision=serverRevision; });
  const secondCtx = await browser.newContext({ timezoneId: 'Europe/Berlin' }), second = await secondCtx.newPage();
  second.on('pageerror', e => errors.push('second: ' + e.message)); await login(second, 'admin');
  const staffing = await ev(second, async () => {
    data.personnelAssignments=[{employeeId:'e_lazy',date:'2026-10-08',machineId:'m1',shift:'single',laneIndex:1,start:'06:00',end:'14:30',breaks:[{start:'09:00',end:'09:15'},{start:'12:00',end:'12:15'}]}];
    data.projects.find(p=>p.id==='p_lazy').name='Server Revision Project';
    save('Staffing für Lazy-Load'); return await flushNow();
  });
  check(staffing, 'Zweite Sitzung speichert geänderte Personalbesetzung über echte State-Revision');
  const updated = await sleepless(admin, () => serverRevision > window.__lazyBaseRevision && window.__lazyCount('calcSchedule') > 0, null).then(() => true, () => false);
  check(updated, 'Aktive Planung berechnet nach neuer Serverrevision den Scheduler frisch');
  const afterStaffing = await ev(admin, () => {const s=calcSchedule().fa_lazy||{};return {value:s.conflict||'',revision:serverRevision,plan:s.start?.toISOString()||'',segments:(s.segments||[]).length}});
  check((baseline.value !== afterStaffing.value || baseline.segments !== afterStaffing.segments) && baseline.revision < afterStaffing.revision,
    `Personalrevision ändert sichtbaren Schedulerstand (${JSON.stringify(baseline)} → ${JSON.stringify(afterStaffing)})`);
  const released = await ev(admin, async () => {
    const step=data.workSteps.find(x=>x.id==='fa_lazy');
    const ok=releaseOrder(step)&&save('Lazy-FA freigegeben');
    return {ok:ok&&await flushNow(),error:document.getElementById('errorModal').textContent};
  });
  check(released.ok, 'Aktueller Plan gibt FA nach geänderter Besetzung frei' + (released.ok?'':' · '+released.error));
  await secondCtx.close();

  // A view not visited yet must be populated from the newest server state at first open.
  const beforeProjects = await ev(admin, () => window.__lazyCount('renderProjects'));
  await admin.click('#navOrders'); await viewIs(admin, 'projects');
  await admin.locator('[data-project-open="p_lazy"]').waitFor({ state: 'visible' });
  check(beforeProjects === 0 && (await admin.locator('#projects').textContent()).includes('Server Revision Project'), 'Zuvor inaktive Projektansicht rendert beim ersten Öffnen mit aktuellen Serverdaten');

  // Production project modal stays live even though it is read-only.
  const prodCtx = await browser.newContext({ timezoneId: 'Europe/Berlin' }), prod = await prodCtx.newPage();
  prod.on('pageerror', e => errors.push('production: ' + e.message)); await login(prod, 'prod_lazy');
  await prod.click('#navOrders'); await prod.locator('[data-project-open="p_lazy"]').waitFor({ state: 'visible' });
  await prod.click('[data-project-open="p_lazy"]'); await prod.getByTestId('project-live-scope').waitFor({ state: 'visible' });
  await ev(admin, async () => {
    const start=await api('/api/production/fa_lazy/start',{method:'POST',body:JSON.stringify({requestId:'lazy-live-start-01'})});
    if(start.r.status!==200)throw new Error('Start HTTP '+start.r.status+' '+JSON.stringify(start.body));
    const part=await api('/api/production/fa_lazy/partial',{method:'POST',body:JSON.stringify({requestId:'lazy-live-part-01',goodQty:30,scrapQty:2})});
    if(part.r.status!==200)throw new Error('Teil HTTP '+part.r.status);
  });
  await prod.waitForFunction(() => document.querySelector('[data-testid="project-production-row-fa_lazy"]')?.textContent.includes('Gut 30') && document.querySelector('[data-testid="project-production-row-fa_lazy"]')?.textContent.includes('Rest 68'), null, { timeout: 12000 });
  check(true, 'Geöffnetes read-only Projektmodal empfängt Produktionsfortschritt live per Revision-Polling');

  // A module change and direct navigation must revoke visibility/initialization immediately.
  await ev(admin, async () => { await cfgSend('/api/config','PATCH',{modules:{projects:false}}); await loadConfig(); renderAll(); });
  await ev(prod, async () => { await loadConfig(); renderAll(); });
  const revoked = await ev(prod, () => {
    window.__lazyReset(); const allowed=viewAllowed('projects'); switchView('projects'); renderAll();
    return {allowed,projects:window.__lazyCount('renderProjects'),view:data.ui.view,modal:document.getElementById('projectModal').classList.contains('show')};
  });
  check(!revoked.allowed && revoked.projects===0 && revoked.view==='production' && !revoked.modal,
    `Modulwechsel widerruft Projektansicht, Modal und Direktnavigation (${JSON.stringify(revoked)})`);
  const viewerCtx = await browser.newContext({ timezoneId: 'Europe/Berlin' }), viewer = await viewerCtx.newPage();
  viewer.on('pageerror', e => errors.push('viewer: ' + e.message)); await login(viewer, 'viewer_lazy');
  const deniedRole = await ev(viewer, () => {
    window.__lazyReset(); switchView('settings'); renderAll();
    return {allowed:viewAllowed('settings'),view:data.ui.view,settings:window.__lazyCount('renderSettings'),navSystem:getComputedStyle(document.getElementById('navSystem')).display};
  });
  check(!deniedRole.allowed && deniedRole.view!=='settings', 'Tatsächliche Viewer-Rolle verwirft direkte Navigation in System');
  check(deniedRole.settings===0 && deniedRole.navSystem==='none', 'Rollenwechsel entfernt gesperrte Systemansicht und Renderer');
  await adminCtx.close(); await prodCtx.close(); await viewerCtx.close();
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  check(errors.length===0, 'Keine JavaScript-Fehler ' + errors.slice(0, 4).join(' | '));
  await browser.close(); srv.kill(); rmSync(dataDir, { recursive: true, force: true });
}
console.log(`\n${results.filter(x=>x[0]).length}/${results.length} bestanden`);
process.exit(results.every(x=>x[0])?0:1);

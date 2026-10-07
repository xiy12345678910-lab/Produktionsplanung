#!/usr/bin/env node
// E2E V12.19.1: Personalzeiten und unnötige Abwesenheitssimulation.
// Startet server.py mit leerer Datenbank in einem Temp-Ordner und prüft im Browser (Playwright/Chromium).
//
// Aufruf:  node tests/e2e_personnel_performance.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18931;
const BASE = `http://127.0.0.1:${PORT}/`;
const USER = 'admin', PASS = 'Gate-Test-1234';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}

const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-personnel-'));
const py = `
import ipaddress, sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR = Path(${JSON.stringify(dataDir)})
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
server.init_db(seed="werbetechnik")
server.create_or_reset_admin(${JSON.stringify(USER)}, ${JSON.stringify(PASS)})
httpd = server.MPHTTPServer(("127.0.0.1", ${PORT}), server.Handler)
print("READY", flush=True)
httpd.serve_forever()
`;
const srv = spawn(process.platform === 'win32' ? 'python' : 'python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'] });
await new Promise((resolve, reject) => {
  const t = setTimeout(() => reject(new Error('Server startet nicht')), 20000);
  srv.stdout.on('data', d => { if (String(d).includes('READY')) { clearTimeout(t); resolve(); } });
  srv.on('exit', c => reject(new Error('Server beendet: ' + c)));
});

const { chromium } = await loadPlaywright();
const browser = await chromium.launch();
const errors = [];

// Das Skript läuft in einer IIFE. Nur im Test: Hook vor dem IIFE-Ende einschleusen, der Funktionen im
// IIFE-Scope auswertet (direktes eval). Die CSP der Testantwort wird dafür entfernt; Produktivcode bleibt unverändert.
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

async function login(page) {
  await installHook(page);
  await page.goto(BASE);
  await page.fill('#loginUser', USER);
  await page.fill('#loginPassword', PASS);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(400);
}
const until = async (page, fn, arg, ms = 8000) => { try { await page.waitForFunction(([src, a]) => window.__t('(' + src + ')')(a), [fn.toString(), arg], { timeout: ms, polling: 100 }); return true; } catch { return false; } };

try {
  const ctx = await browser.newContext({ timezoneId: 'Europe/Berlin' });
  const page = await ctx.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await login(page);
  const persistedFixture = await ev(page, async () => {
    document.getElementById('setupModal')?.classList.remove('show');
    const m=data.machines[0];m.defaultShiftMode='1';data.yearRules=[];data.weekRules=[];data.exceptions=[];data.ui.week='2026-10-05';
    data.employees=[{id:'e_reload',name:'Persistente Zeiten',departmentId:m.departmentId,skills:[m.id],homeMachineId:m.id,homeShift:'auto',active:true,weeklyHours:40,standardPersonnelTimes:{single:{name:'Individuell',start:'07:00',end:'15:00',breaks:[{start:'10:00',end:'10:15'},{start:'12:00',end:'12:30'}]},fridaySingle:{name:'Freitag',start:'07:00',end:'11:45',breaks:[{start:'09:00',end:'09:15'}]}}},{id:'e_part',name:'Teilzeit',departmentId:m.departmentId,skills:[m.id],active:true,weeklyHours:20}];
    data.personnelAssignments=[];data.personnelAbsences=[];save('Personalprüfbestand');if(!await flushNow())throw new Error('Fixture speichern fehlgeschlagen');switchView('personnel');renderPersonnel();return m.id;
  });
  for(const date of ['2026-10-05','2026-10-06','2026-10-07']){
    await page.selectOption(`[data-person-cell="e_part"][data-date="${date}"]`,persistedFixture+'|single');
    await ev(page,async()=>{if(!await flushNow())throw new Error('Teilzeit speichern fehlgeschlagen')});
  }
  await page.click('[data-ptime-toggle="e_reload|2026-10-05"]');
  await page.fill('[data-person-time="end"][data-employee="e_reload"][data-date="2026-10-05"]','14:00');
  await page.locator('[data-person-time="end"][data-employee="e_reload"][data-date="2026-10-05"]').blur();
  await ev(page,async()=>{if(!await flushNow())throw new Error('Tageszeit speichern fehlgeschlagen')});
  await page.reload();await page.waitForTimeout(800);
  const reloaded=await ev(page,()=>({custom:employee('e_reload')?.standardPersonnelTimes?.single?.start,day:personnelAssignment('e_reload','2026-10-05')?.end,part:data.personnelAssignments.filter(a=>a.employeeId==='e_part').map(personnelNetHours)}));
  check(reloaded.custom==='07:00'&&reloaded.day==='14:00'&&reloaded.part.length===3&&reloaded.part.every(h=>h===4),'Echte Teilzeit-Zuordnung und Tageszeitbearbeitung bleiben nach Server-Speicherung und Neuladen erhalten');
  const capacity=await ev(page,()=>{const keep=data.exceptions,abs=data.personnelAbsences;const e=employee('e_reload'),ws=parseLocal('2026-10-05'),before=employeeWeekAvailableHours(e,ws);data.exceptions=[{date:'2026-10-06',mode:'0',label:'Betriebsferien'}];const closed=employeeWeekAvailableHours(e,ws);data.exceptions=[];data.personnelAbsences=[{employeeId:e.id,date:'2026-10-06',label:'Krank'}];const absent=employeeWeekAvailableHours(e,ws);data.personnelAbsences=[];const temp={...e,id:'e_temp',homeMachineId:'',employmentType:'temporary',tempStatus:'approved',tempFrom:'2026-10-07',tempTo:'2026-10-09'};const temporary=employeeWeekAvailableHours(temp,ws);data.exceptions=keep;data.personnelAbsences=abs;return {before,closed,absent,temporary};});
  check(Math.abs(capacity.before-capacity.closed-7.25)<.001&&Math.abs(capacity.before-capacity.absent-7.25)<.001&&capacity.temporary===22.5,'Betriebsferien, Abwesenheit und genehmigter Leihzeitraum begrenzen die verfügbare Kapazität');
  const vals = await ev(page, () => {
    const e = {id:'e_perf',weeklyHours:40,workingDays:[1,2,3,4,5]};
    const mo = employeeDailyHours(e,'2026-10-05'), fr = employeeDailyHours(e,'2026-10-09');
    const prior = calcSchedule; let calls=0; calcSchedule=()=>{calls++;return {}};
    const gate=data.personnelGate, abs=data.personnelAbsences;
    data.personnelGate=false; data.personnelAbsences=[]; const off=absenceImpact(''); const offCalls=calls;
    data.personnelGate=true; const empty=absenceImpact(''); const emptyCalls=calls;
    data.personnelGate=gate; data.personnelAbsences=abs; calcSchedule=prior;
    return {mo,fr,off:off.length,empty:empty.length,offCalls,emptyCalls};
  });
  check(vals.mo===8.75 && vals.fr===5, `Standard-Nettozeit stimmt (${vals.mo}/${vals.fr} h)`);
  check(vals.off===0 && vals.empty===0 && vals.offCalls===0 && vals.emptyCalls===0, 'Abwesenheitsvergleich überspringt Berechnung bei Gate AUS und ohne Abwesenheiten');
  const clicked = await ev(page, () => {
    const ws=monday(parseLocal(data.ui.week)), dep=data.departments.find(d=>d.active!==false)?.id||'cnc';
    data.employees=[{id:'e_click',name:'Testperson',departmentId:dep,active:true,weeklyHours:40,workingDays:[1,2,3,4,5],skills:[]}];
    renderPersonnel(); return !!document.querySelector('#personnelBody [data-emp-hours="e_click"]');
  });
  check(clicked, 'Wochenplan-Name bietet Arbeitszeit-/Planstundenbearbeitung');
  const persistent = await ev(page, () => {
    const e={id:'e_custom',name:'Individuell',active:true,weeklyHours:40,workingDays:[1,2,3,4,5],departmentId:'cnc',skills:['m1'],homeMachineId:'m1',homeShift:'auto',standardPersonnelTimes:{single:{name:'Individuell',start:'07:00',end:'15:00',breaks:[{start:'10:00',end:'10:15'},{start:'12:00',end:'12:30'}]},fridaySingle:{name:'Individuell',start:'07:00',end:'11:45',breaks:[{start:'09:00',end:'09:15'},{start:'',end:''}]}}};
    const a=homePersonnelAssignment(e.id,'2026-10-05');data.employees.push(e);const b=homePersonnelAssignment(e.id,'2026-10-05');return {before:a?.start||'',after:b?.start||'',breaks:b?.breaks?.length||0};
  });
  check(persistent.after==='07:00' && persistent.breaks===2, 'Dauerhafte persönliche Schichtzeiten und Pausen steuern Stammzuordnung');
  const invalidation = await ev(page, () => {
    const original=calcSchedule;let calls=0;calcSchedule=function(...args){calls++;return original(...args)};
    data.personnelGate=false;data.personnelAbsences=[];const start=calls;renderAll();const afterRender=calls;
    data.personnelGate=true;renderAll();const afterGate=calls;
    data.personnelAbsences=[{employeeId:'e_custom',date:dateKey(monday(parseLocal(data.ui.week))),label:'Krank'}];const beforeImpact=calls;absenceImpact('');const afterImpact=calls;calcSchedule=original;
    return {render:afterRender-start,gate:afterGate-afterRender,absence:afterImpact-beforeImpact};
  });
  check(invalidation.render===1 && invalidation.gate===1 && invalidation.absence===2, `Planung wird bei Render/Gate/Abwesenheit sofort neu berechnet (${invalidation.render}/${invalidation.gate}/${invalidation.absence})`);
  const perf = await ev(page, () => {
    const mid=data.machines[0]?.id||'m1', day=dateKey(monday(new Date()));data.personnelGate=false;data.personnelAbsences=[];data.workSteps=Array.from({length:200},(_,i)=>({id:'perf_'+i,projectId:'perf',sequence:i+1,departmentId:machine(mid)?.departmentId||'cnc',planningType:'MACHINE',predecessorIds:[],pos:i+1,machineId:mid,altMachineId:'',allowAlternative:false,order:'Perf '+i,hours:1,status:'planned',direction:'forward',anchorMode:'none',requiredStart:'',requiredFinish:'',dueDate:''}));
    const original=calcSchedule;let calls=0;calcSchedule=function(...args){calls++;return original(...args)};const t=performance.now();renderAll();const elapsed=performance.now()-t;calcSchedule=original;return {orders:data.workSteps.length,calls,elapsed};
  });
  console.log(`PERF fixture=${perf.orders} planned orders: renderAll ${perf.elapsed.toFixed(1)} ms, calcSchedule calls ${perf.calls}; V12.19.0 source performed 3 calls/renderAll (base + GF + projects)`);
  const expectedCalls=process.env.PERSONNEL_BENCH_BASELINE==='1'?3:1;check(perf.calls===expectedCalls, `${expectedCalls} Schedulerlauf/-läufe für den Vergleich (${perf.calls})`);
  await ctx.close();
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

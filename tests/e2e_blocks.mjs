#!/usr/bin/env node
// A–D: AV project creation, authoritative production, pallet labels and user administration.
// node tests/e2e_blocks.mjs
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = 18829;
const BASE = `http://127.0.0.1:${PORT}/`;
const USER = 'admin', PASS = 'Effort-Test-1234';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  const globalRoot = execSync('npm root -g').toString().trim();
  return import(pathToFileURL(path.join(globalRoot, 'playwright', 'index.mjs')).href);
}

const results = [];
const check = (ok, label) => { results.push([!!ok, label]); console.log((ok ? 'PASS ' : 'FAIL ') + label); };
const near = (a, b, eps = 0.01) => Math.abs(a - b) < eps;

const dataDir = mkdtempSync(path.join(tmpdir(), 'mp-effort-'));
const py = `
import ipaddress, sys
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(SRC)})
import server
server.DATA_DIR = Path(${JSON.stringify(dataDir)})
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
server.init_db(seed="werbetechnik")
server.now_iso = lambda: '2026-10-07T05:00:00Z'
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

// Das Skript läuft in einer IIFE. Nur im Test: Hook vor dem IIFE-Ende einschleusen (CSP der Testantwort entfernt).
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

async function login(page,user=USER,pass=PASS) {
  await page.clock.install({time:new Date('2026-10-07T05:00:00Z')});
  await installHook(page);
  await page.goto(BASE);
  await page.fill('#loginUser', user);
  await page.fill('#loginPassword', pass);
  await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show'));
  await page.waitForTimeout(400);
}

try {
 const ctx=await browser.newContext({timezoneId:'Europe/Berlin'}),page=await ctx.newPage();page.on('pageerror',e=>errors.push(e.message));await login(page);
 await ev(page,()=>{data.ui.sysTab='users';switchView('settings');renderSettings()});await page.selectOption('#newUserRole','production_planning');
 check(!(await page.locator('#newUserDepartment').isVisible()),'AV: Bereichsfeld ausgeblendet');
 await page.selectOption('#newUserRole','department_lead');check(await page.locator('#newUserDepartment').isVisible(),'Bereichsrolle: Bereichsfeld sichtbar');
 await page.selectOption('#newUserRole','admin');check(!(await page.locator('#newUserDepartment').isVisible()),'Globale Rolle: Bereichsfeld ausgeblendet');
 check(await page.locator('[data-user-delete]').count()===0,'Eigenes Konto hat keine Löschaktion');
 const dep=await ev(page,()=>data.departments[0].id);
 const created=await ev(page,async()=>{const r=await api('/api/users',{method:'POST',body:JSON.stringify({username:'avtest',password:'Effort-Test-1234',role:'production_planning',departmentId:data.departments[0].id})});return r.body});
 check(created.departmentId==='','Globale Rolle entfernt früheren Bereich serverseitig');
 const handoff=await ev(page,async()=>{const m=data.machines.find(x=>x.kind==='line');m.crew=4;m.crewMax=4;m.effortScaling=true;m.start='2026-10-05T06:30';save('Konfektionslinie');await flushNow();return {id:m.id,departmentId:m.departmentId}});
 const avctx=await browser.newContext({timezoneId:'Europe/Berlin'}),av=await avctx.newPage();await login(av,'avtest',PASS);
 await ev(av,()=>switchView('projects'));await av.click('#projectNew');await av.fill('#npCustomer','Testkunde');await av.fill('#npName','A-D Produktion');await av.click('#npCreate');await ev(av,async()=>await flushNow());
 const project=await ev(av,()=>data.projects.find(p=>p.name==='A-D Produktion'));
 check(!!project?.id,'AV erstellt ein Projekt über die reale Oberfläche');
 await ev(av,id=>openProject(id),project.id);await av.fill('[data-pfield="ab"]','AB-4711');await av.fill('[data-pfield="dueDate"]','2026-10-30');await av.locator('[data-pfield="dueDate"]').blur();
 await ev(av,async()=>await flushNow());await av.locator('[data-project-go="accepted"]').click();await ev(av,async()=>await flushNow());await ev(av,()=>closeProject());
 check(await ev(av,id=>projectById(id).phase,project.id)==='accepted','AV übergibt das Projekt mit AB und Termin an die Produktion');
 for(const [fa,hours] of [['AV-4711-A','16'],['AV-4711-B','']]){
  await ev(av,args=>openOrderDialog({projectId:args.pid,departmentId:args.departmentId}),{pid:project.id,departmentId:handoff.departmentId});await av.selectOption('#qMachine',handoff.id);await av.fill('#qFA',fa);await av.fill('#qHours',hours);await av.fill('#qQty','100');await av.click('#createOrder');await ev(av,async()=>await flushNow());
 }
 check(await ev(av,id=>data.workSteps.filter(o=>o.projectId===id).length,project.id)===2,'AV legt mehrere FA mit Stunden und Menge am selben Projekt an');
 const second=await ev(av,()=>data.workSteps.find(o=>o.fa==='AV-4711-B').id);
 check(await ev(av,id=>calcSchedule()[id]?.conflict,second)==='Konfektionsstunden fehlen','Konfektions-FA ohne Stunden bleibt sichtbar und als fehlend markiert');
 await ev(av,id=>openProject(id),project.id);await av.locator(`[data-fexp="${second}"]`).click();await av.fill(`[data-fa-id="${second}"][data-fa-field="hours"]`,'8');await av.locator(`[data-fa-id="${second}"][data-fa-field="hours"]`).blur();await ev(av,async()=>await flushNow());
 check(await ev(av,id=>data.workSteps.find(o=>o.id===id).hours,second)===8,'AV ergänzt Konfektionsstunden direkt am bestehenden FA');await ev(av,()=>closeProject());
 await ev(page,async()=>await syncFromServer());
 await ev(page,async dep=>{await api('/api/users',{method:'POST',body:JSON.stringify({username:'leadtest',password:'Effort-Test-1234',role:'department_lead',departmentId:dep})});const m=data.machines.find(x=>x.departmentId!==dep);data.workSteps.push({id:'foreign_secret',fa:'SECRET-OTHER-AREA',departmentId:m.departmentId,machineId:m.id,planningType:'MACHINE',sequence:50,pos:50,order:'SECRET-OTHER-AREA',hours:1,targetQty:1,status:'planned',direction:'forward',anchorMode:'none',predecessorIds:[]});save('Bereichsfixture');return await flushNow()},handoff.departmentId);
 const leadctx=await browser.newContext({timezoneId:'Europe/Berlin'}),lead=await leadctx.newPage();await login(lead,'leadtest',PASS);
 check(await ev(lead,()=>data.workSteps.some(o=>o.fa==='AV-4711-A')&&!data.workSteps.some(o=>o.fa==='SECRET-OTHER-AREA')),'Bereich übernimmt AV-FA und erhält keine fremden FA vom Server');
 check(await ev(lead,async()=>{const original=await api('/api/state');original.body.data.workSteps[0].hours=99;return (await api('/api/state',{method:'PUT',body:JSON.stringify({revision:original.body.revision,data:original.body.data})})).r.status})===403,'Manipulierte Bereichsanfrage kann die AV-Konfektionsstunden nicht ändern');
 await ev(lead,()=>{data.ui.week='2026-10-05';switchView('orders');renderAll()});
 const handed=await ev(lead,()=>data.workSteps.find(o=>o.fa==='AV-4711-A').id);
 check(await ev(lead,id=>previewPlanningMutation(id,o=>applyPlanType(o,'start-soft','2026-10-07T08:00')),handed),'Bereich plant den übergebenen FA operativ');
 await lead.click('#confirmMove');await ev(lead,async()=>await flushNow());
 const csvWait=lead.waitForEvent('download');await ev(lead,()=>exportCSV());const csv=await csvWait;const stream=await csv.createReadStream();let csvText='';for await(const chunk of stream)csvText+=chunk;
 check(csvText.includes('AV-4711-A')&&csvText.includes('AB-4711')&&!csvText.includes('SECRET-OTHER-AREA'),'CSV enthält FA und AB ausschließlich des eigenen Bereichs');
 check(await ev(lead,()=>{renderWeeklyPrint();const t=document.getElementById('weeklyPrintBody').textContent;return t.includes('AV-4711-A')&&t.includes('AB-4711')&&!t.includes('SECRET-OTHER-AREA')}),'PDF-Druckvorlage enthält ausschließlich den eigenen Bereich');
 await leadctx.close();await ev(page,async()=>await syncFromServer());
 await page.route('**/api/state',async route=>{if(route.request().method()!=='GET')return route.continue();const response=await route.fetch();await new Promise(resolve=>setTimeout(resolve,200));await route.fulfill({response})});
 const preserved=await ev(page,async()=>{const pending=syncFromServer();await new Promise(resolve=>setTimeout(resolve,30));data.workSteps[0].description='Lokale Bearbeitung erhalten';save('Bearbeitung');await pending;return data.workSteps[0].description==='Lokale Bearbeitung erhalten'});
 check(preserved,'Bereits laufender Datenabruf überschreibt keine neue Bearbeitung');await ev(page,async()=>await flushNow());await page.unroute('**/api/state');
 const prepared=await ev(page,async pid=>{const m=data.machines[0];m.start='2026-10-05T06:30';m.setupMinutes=0;m.kind='line';m.crew=4;m.crewMax=4;m.effortScaling=true;m.staffRequired=0;data.personnelGate=false;data.palletTemplates=[{id:'real_tpl',name:'Versand',fromAddress:'Testfirma',toAddress:'Testkunde',shelfLifeDays:30}];data.workSteps.push({id:'runtime_fa',fa:'4711',faNumber:'4711',sourceType:'PROJECT',sourceId:pid,projectId:pid,departmentId:m.departmentId,planningType:'MACHINE',sequence:30,pos:30,machineId:m.id,altMachineId:'',allowAlternative:false,order:'4711',ab:'',wt:'',predecessorIds:[],hours:8,targetQty:100,articleNo:'ART-1',description:'Testartikel',status:'planned',direction:'forward',anchorMode:'none',requiredStart:'',requiredFinish:'',baselinePlan:null});if(!save('FA angelegt')||!await flushNow())return false;const loaded=await api('/api/state');if(!loaded.body.data.workSteps.some(x=>x.id==='runtime_fa'))throw new Error(document.getElementById('errorModal').textContent);return true},project.id);
 check(prepared,'Kanonischer FA mit Projekt- und Bereichsreferenz gespeichert');
 const released=await ev(page,async()=>{const o=data.workSteps.find(x=>x.id==='runtime_fa');if(!releaseOrder(o)||!save('Freigabe'))return false;return await flushNow()});
 check(released,'Ein gemeinsamer Scheduler gibt den FA frei');
 const started=!!await ev(page,async()=>await prodStart('runtime_fa'));check(started,'Start erfasst Runtime serverseitig'+(started?'':' · '+await page.textContent('#errorModal')));
 check(!!await ev(page,async()=>await prodPause('runtime_fa')),'Pause über denselben Runtimepfad');
 check(!!await ev(page,async()=>await prodResume('runtime_fa')),'Fortsetzen über denselben Runtimepfad');
 await ev(page,()=>switchView('production'));await page.click('[data-prod-partial="runtime_fa"]');await page.fill('#askInput','30');await page.click('#askOk');await page.fill('#askInput','2');await page.click('#askOk');await page.waitForFunction(()=>document.querySelector('[data-prod-field="goodQty"]')?.value==='30');
 check(await ev(page,()=>data.workSteps.find(x=>x.id==='runtime_fa').goodQty)===30,'Teilfertigmeldung kumuliert Gutmenge');
 const label=await ev(page,async()=>{const result=await productionAction('runtime_fa','label',{quantity:30,templateId:'real_tpl'});return result&&data.palletLabels.at(-1)});
 check(label.quantity===30&&label.fromAddress==='Testfirma'&&label.bestBefore&&label.barcode,'Palettenetikett enthält Menge, Adresse, MHD und Barcode');
 check(await ev(page,()=>code39(data.palletLabels.at(-1).barcode).includes('<rect')),'Barcode wird als druckbare Balken dargestellt');
 const finished=await ev(page,async()=>await productionAction('runtime_fa','finish',{goodQty:68,scrapQty:0}));
 check(!!finished&&await ev(page,()=>data.history.find(x=>x.originalOrderId==='runtime_fa').goodQty)===98,'Fertigmeldung berücksichtigt vorherige Teilmengen');
 check(await ev(page,()=>data.history.find(x=>x.originalOrderId==='runtime_fa').projectId)===project.id,'Projektverknüpfung bleibt bis in die Ist-Historie erhalten');
 await ev(page,()=>{data.ui.sysTab='users';switchView('settings')});await ev(page,async()=>await renderUserAdmin());
 check(await page.locator('[data-user-delete]').count()===2,'Admin sieht die Löschaktion für andere Konten');
 await page.locator(`[data-user-delete="${created.id}"]`).click();await page.click('#askOk');await page.waitForFunction(()=>document.querySelectorAll('[data-user-delete]').length===1);
 check(await page.locator('[data-user-delete]').count()===1,'Löschen aktualisiert die Benutzerliste');
  // UI contract with transport fixtures; installer/release verification is exercised by test_updates.py.
 let updateBody={currentVersion:'12.19.0',available:false,job:null,installSupported:true};
 await page.route('**/api/updates',r=>r.fulfill({json:updateBody}));await ev(page,()=>refreshAdminUpdates(true));
 check(await page.locator('#installUpdate').count()===0,'Kein Updateknopf im Normalzustand');
 updateBody={...updateBody,available:true,version:'12.20.0',notes:'Allgemeines Folge-Release'};await ev(page,()=>refreshAdminUpdates(true));
 check(await page.locator('#installUpdate').isVisible()&&(await page.textContent('#adminUpdatePanel')).includes('12.20.0'),'Admin sieht Versionen, Release Notes und Installationsknopf');
 await av.route('**/api/updates',r=>r.fulfill({json:updateBody}));await ev(av,()=>refreshAdminUpdates(true));
 check(await av.locator('#adminUpdatePanel').count()===0,'AV erhält keinen Updatehinweis');
 await page.route('**/api/updates/install',r=>{updateBody={...updateBody,job:{jobId:'ui-job',version:'12.20.0',stage:'download'}};return r.fulfill({status:202,json:{ok:true}})});
 await page.click('#installUpdate');await page.waitForFunction(()=>document.getElementById('adminUpdatePanel')?.textContent.includes('Download'));check((await page.textContent('#adminUpdatePanel')).includes('Download'),'Update zeigt serverseitigen Fortschritt nach dem Start');
 await page.reload();await page.waitForFunction(()=>document.getElementById('adminUpdatePanel')?.textContent.includes('Download'));
 check(await page.locator('#installUpdate').count()===0,'Browser-Neuladen erhält den laufenden Updatezustand');
 updateBody={currentVersion:'12.19.0',available:false,installSupported:true,job:{stage:'complete',version:'12.19.0'}};await ev(page,()=>refreshAdminUpdates(true));
 check(await page.locator('#adminUpdatePanel').count()===0,'Updatehinweis verschwindet nach erfolgreichem Update');
 await avctx.close();await ctx.close();
} catch(e) { check(false,'Unerwarteter Fehler: '+e.stack); }
finally {check(errors.length===0,'Keine JavaScript-Fehler '+errors.join(' | '));await browser.close();srv.kill();rmSync(dataDir,{recursive:true,force:true});}
console.log(`\n${results.filter(x=>x[0]).length}/${results.length} bestanden`);
process.exit(results.every(x=>x[0])?0:1);

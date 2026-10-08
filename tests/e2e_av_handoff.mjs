#!/usr/bin/env node
// AV-Fertigungsauftrag: breite Erfassung, stabile Bereichsfolge und Übergabe ohne Kapazitätsbelegung.
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
const SRC=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),PORT=18798,BASE=`http://127.0.0.1:${PORT}/`,PASS='E2E-Handoff-1234';
const tmp=mkdtempSync(path.join(tmpdir(),'mp-av-handoff-'));
const py=`
import ipaddress,json,sys
from pathlib import Path
sys.path.insert(0,${JSON.stringify(SRC)})
import server
server.DATA_DIR=Path(${JSON.stringify(tmp)})
server.DB_PATH=server.DATA_DIR/'maschinenplanung.sqlite3'
server.ALLOWED_NETWORK=ipaddress.ip_network('127.0.0.0/8')
server.init_db(seed='werbetechnik')
server.create_or_reset_admin('admin',${JSON.stringify(PASS)})
with server.DB_LOCK,server.db_session() as con:
 old=json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()['json'])
 new=json.loads(json.dumps(old))
 new['workSteps']=[]
 new['machines'].append({**new['machines'][0],'id':'foreign_resource','name':'Fremder Bereich','departmentId':'thermoforming'})
 new['workSteps'].append({'id':'legacy-manual-cnc','sequence':1,'planningType':'MACHINE','pos':1,'departmentId':'cnc','projectId':'','predecessorIds':[],'fa':'FA-MANUAL-1001','faNumber':'FA-MANUAL-1001','ab':'','wt':'','machineId':'m1','altMachineId':'','allowAlternative':False,'order':'FA-MANUAL-1001','articleNo':'','description':'Manueller Legacy-Auftrag','targetQty':10,'dueDate':'','baselinePlan':None,'hours':3,'goodQty':0,'scrapQty':0,'status':'planned','direction':'forward','anchorMode':'none','requiredStart':'','requiredFinish':'','createdAt':server.now_iso(),'lockedStart':'','lockedSegments':[],'actualStartedAt':'','runningSince':'','pausedAt':'','pauseIntervals':[],'remainingHours':None,'lastStatusCheckAt':''})
 new['projects']=[{'id':'p1','number':'P-2026-001','phase':'accepted','name':'Gehäuse','customer':'Kunde A','ab':'AB-500','dueDate':'2026-11-30','log':[],'processes':[]}]
 ok,code,reason=server.validate_state(old,new)
 if not ok: print('SEED-ERROR',code,reason,flush=True);sys.exit(1)
 con.execute('UPDATE state SET json=?,revision=revision+1 WHERE id=1',(json.dumps(new,ensure_ascii=False),))
 for user,role,dep in [('av','production_planning',''),('cnclead','department_lead','cnc'),('konflead','department_lead','konf1')]:
  salt,digest=server.hash_password(${JSON.stringify(PASS)})
  con.execute('INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)',(user,salt,digest,role,dep,server.now_iso(),server.now_iso()))
httpd=server.MPHTTPServer(('127.0.0.1',${PORT}),server.Handler)
print('READY',flush=True)
httpd.serve_forever()
`;
const srv=spawn('python3',['-c',py],{stdio:['ignore','pipe','inherit'], env: { ...process.env, MP_CONFIG_DIR: path.join(tmp, 'config') } });
const checks=[];const check=(v,label)=>{checks.push(!!v);if(!v)console.log('FAIL '+label)};
try{
 await new Promise((resolve,reject)=>{let out='';const t=setTimeout(()=>reject(new Error('Server start timeout '+out)),20000);srv.stdout.on('data',d=>{out+=String(d);if(out.includes('SEED-ERROR'))reject(new Error(out));if(out.includes('READY')){clearTimeout(t);resolve()}});srv.on('exit',c=>reject(new Error('Server exit '+c)))});
 const {chromium}=await import('playwright');const browser=await chromium.launch();
 async function login(user){const p=await browser.newPage({viewport:{width:1440,height:950},timezoneId:'Europe/Berlin'});p.on('pageerror',e=>checks.push(false));await p.goto(BASE);await p.fill('#loginUser',user);await p.fill('#loginPassword',PASS);await p.click('#loginBtn');await p.waitForFunction(()=>!document.getElementById('loginModal').classList.contains('show'));await p.waitForTimeout(300);return p}
 const av=await login('av');await av.click('#navPlan');await av.waitForTimeout(200);await av.click('#quickAdd');
 check(await av.locator('#orderModal .avOrderDialog').isVisible(),'AV sees wide FA dialog');
 check((await av.locator('#qDialogTitle').innerText())==='Fertigungsauftrag anlegen','AV sees production order title');
 check(await av.locator('#qAvDepartmentsField').isVisible(),'AV can choose production departments');
 check(!(await av.locator('#qMachine').isVisible()),'AV has no resource assignment field');
 check(!(await av.locator('#qHours').isVisible()),'generic hours field is hidden for AV');
 check(!(await av.locator('#qCalcBox').isVisible()),'machine cycle calculator is hidden for AV');
 check((await av.locator('#qFAField label').innerText()).includes('FA · Fertigungsauftrag'),'FA identity is explicit in the AV header');
 check(Number.parseFloat(await av.locator('#qFA').evaluate(e=>getComputedStyle(e).fontSize))>20,'FA field is prominent in the order header');
 await av.fill('#qFA','FA-AV-5701');await av.selectOption('#qProject','p1');await av.fill('#qQty','120');await av.fill('#qDue','2026-10-30');
 await av.selectOption('[data-av-department="0"]','cnc');await av.click('#qAvAddDepartment');await av.selectOption('[data-av-department="1"]','konf1');await av.click('#qAvAddDepartment');await av.selectOption('[data-av-department="2"]','konf2');
 check(await av.locator('[data-av-hours="0"]').count()===0,'non-confection row has no editable hours');
 check(await av.locator('[data-av-hours="1"]').isVisible()&&await av.locator('[data-av-hours="2"]').isVisible(),'each confection row has its own editable AV hours');
 check(await av.locator('[data-av-predecessor="1"]').inputValue()==='cnc','predecessor is linked by selected department');
 await av.locator('[data-av-due="0"]').fill('2026-10-22');await av.locator('[data-av-due="1"]').fill('2026-10-26');await av.locator('[data-av-due="2"]').fill('2026-10-30');
 await av.locator('[data-av-hours="1"]').fill('3.5');await av.locator('[data-av-hours="2"]').fill('5.25');
 await av.screenshot({path:'/tmp/av-handoff-dialog-desktop.png'});
 await av.setViewportSize({width:390,height:844});
 const mobileLayout=await av.evaluate(()=>{const box=document.querySelector('#orderModal .avOrderDialog'),rows=[...document.querySelectorAll('.avDepartmentRow')];return {windowWidth:innerWidth,documentWidth:document.documentElement.scrollWidth,boxWidth:box.clientWidth,boxScrollWidth:box.scrollWidth,rowWidths:rows.map(x=>[x.clientWidth,x.scrollWidth])}});
 check(mobileLayout.documentWidth<=mobileLayout.windowWidth&&mobileLayout.boxScrollWidth<=mobileLayout.boxWidth&&mobileLayout.rowWidths.every(([w,s])=>s<=w),`390px dialog has no horizontal overflow (${JSON.stringify(mobileLayout)})`);
 check(await av.locator('#qFA,#qProject,#qQty,#qDue,[data-av-department],[data-av-predecessor],[data-av-due],[data-av-hours],#qAvAddDepartment').evaluateAll(xs=>xs.every(x=>x.offsetParent!==null&&(!('disabled'in x)||!x.disabled))),'390px AV controls remain visible and enabled');
 await av.locator('[data-av-hours="2"]').fill('5.25');await av.click('#qAvAddDepartment');check(await av.locator('[data-av-department]').count()===4,'390px add-area control works');await av.locator('[data-av-remove="3"]').click();check(await av.locator('[data-av-department]').count()===3,'390px remove-area control works');
 await av.locator('#qAvDepartmentsField').evaluate(el=>{const box=el.closest('.avOrderDialog');box.scrollTop=box.scrollHeight});
 await av.screenshot({path:'/tmp/av-handoff-dialog-filled.png'});
 await av.click('#createOrder');await av.waitForTimeout(900);
 const getState=async p=>p.evaluate(async()=>((await(await fetch('/api/state')).json()).data));let st=await getState(av);const rows=st.workSteps.filter(x=>x.fa==='FA-AV-5701');
 check(rows.length===3,'one stable work step per selected department');
 check(rows[0]?.departmentId==='cnc'&&rows[1]?.departmentId==='konf1'&&rows[2]?.departmentId==='konf2'&&rows[1]?.predecessorIds?.[0]===rows[0]?.id&&rows[2]?.predecessorIds?.[0]===rows[1]?.id,'ordered stable predecessor references');
 check(rows.every(x=>x.machineId==='')&&rows[0]?.hours===0&&rows[1]?.hours===3.5&&rows[2]?.hours===5.25,'no resource assigned; distinct hours only on confection rows');
 check(rows[0]?.dueDate==='2026-10-22'&&rows[1]?.dueDate==='2026-10-26'&&rows[2]?.dueDate==='2026-10-30','per department deadlines saved');
 await av.click('#quickAdd');await av.fill('#qFA','FA-AV-INTEGRITY');await av.selectOption('#qProject','p1');await av.fill('#qQty','3');await av.fill('#qDue','2026-11-05');
 await av.selectOption('[data-av-department="0"]','cnc');await av.click('#qAvAddDepartment');await av.selectOption('[data-av-department="1"]','konf1');await av.click('#qAvAddDepartment');await av.selectOption('[data-av-department="2"]','konf2');
 await av.locator('[data-av-due="0"]').fill('2026-11-01');await av.locator('[data-av-due="1"]').fill('2026-11-03');await av.locator('[data-av-due="2"]').fill('2026-11-05');await av.locator('[data-av-hours="1"]').fill('2.25');await av.locator('[data-av-hours="2"]').fill('6.5');
 await av.locator('[data-av-move="2|-1"]').click();
 check(await av.locator('[data-av-department="1"]').inputValue()==='konf2'&&await av.locator('[data-av-hours="1"]').inputValue()==='6.5'&&await av.locator('[data-av-due="1"]').inputValue()==='2026-11-05'&&await av.locator('[data-av-predecessor="1"]').inputValue()==='konf1','reorder keeps department hours, deadline, and predecessor together');
 check(await av.locator('[data-av-department="2"]').inputValue()==='konf1'&&await av.locator('[data-av-hours="2"]').inputValue()==='2.25'&&await av.locator('[data-av-due="2"]').inputValue()==='2026-11-03'&&await av.locator('[data-av-predecessor="2"]').inputValue()==='cnc','reorder preserves the second department row data');
 await av.locator('[data-av-remove="0"]').click();
 check(await av.locator('[data-av-department]').count()===2&&await av.locator('[data-av-predecessor]').evaluateAll(xs=>xs.every(x=>x.value!=='cnc')),'removing predecessor clears all dependent selections without stale references');
 await av.locator('[data-av-predecessor="0"]').selectOption('konf1');await av.locator('[data-av-predecessor="1"]').selectOption('konf2');await av.click('#createOrder');await av.locator('#errorModal.show').waitFor();
 check((await av.locator('#errorMessage').innerText()).includes('Abhängigkeitsschleife'),'dependency cycle is clearly rejected');await av.click('#closeError');
 st=await getState(av);check(!st.workSteps.some(x=>x.fa==='FA-AV-INTEGRITY'),'rejected dependency cycle does not save partial orders');await av.click('#cancelModal');
 await av.setViewportSize({width:1440,height:950});
const denied=async(patch,id)=>av.evaluate(async([stepId,changes])=>{const state=await(await fetch('/api/state')).json();Object.assign(state.data.workSteps.find(x=>x.id===stepId),changes);const health=await(await fetch('/api/health')).json();const response=await fetch('/api/state',{method:'PUT',headers:{'Content-Type':'application/json','X-MP-Client-Version':health.version},body:JSON.stringify({revision:state.revision,data:state.data,action:'Unauthorized AV edit'})});return response.status},[id,patch]);
const denyMachine=await denied({machineId:'m1',handoffUnassigned:false,hours:4},rows[0]?.id);check(denyMachine===403,`server rejects AV resource assignment (${denyMachine})`);
const denyFlag=await denied({handoffUnassigned:false,machineId:'m1',hours:4},rows[0]?.id);check(denyFlag===403,`server rejects AV takeover marker bypass (${denyFlag})`);
const denyHours=await denied({hours:6},rows[0]?.id);check(denyHours===403,`server rejects AV CNC hours (${denyHours})`);
 let cnc=await login('cnclead');await cnc.click('#navPlan');await cnc.waitForTimeout(250);await cnc.click('#quickAdd');
 const deptDialog=await cnc.evaluate(async()=>{const s=await(await fetch('/api/state')).json();return {departments:[...document.querySelector('#qDept').options].map(x=>x.value),machines:[...document.querySelector('#qMachine').options].map(x=>x.value),machineDepartments:[...document.querySelector('#qMachine').options].map(x=>s.data.machines.find(m=>m.id===x.value)?.departmentId),title:document.querySelector('#qDialogTitle').textContent,hasMachine:!!document.querySelector('#qMachine').offsetParent,hasHours:!!document.querySelector('#qHours').offsetParent,handoff:!!document.querySelector('#qAvDepartmentsField').offsetParent}});
 check(deptDialog.departments.length===1&&deptDialog.departments[0]==='cnc'&&deptDialog.machines.length>0&&deptDialog.machineDepartments.every(id=>id==='cnc')&&deptDialog.title==='Fertigungsauftrag anlegen'&&deptDialog.hasMachine&&deptDialog.hasHours&&!deptDialog.handoff,'department lead keeps the common FA dialog with own resources and runtime');
 const foreignResource=await cnc.evaluate(async()=>{const s=await(await fetch('/api/state')).json(),o=s.data.workSteps.find(x=>x.id==='legacy-manual-cnc');o.machineId='foreign_resource';o.departmentId='thermoforming';const h=await(await fetch('/api/health')).json(),r=await fetch('/api/state',{method:'PUT',headers:{'Content-Type':'application/json','X-MP-Client-Version':h.version},body:JSON.stringify({revision:s.revision,data:s.data,action:'Foreign resource rejection test'})});return r.status});
 check(foreignResource===403,`server rejects a foreign-department resource (${foreignResource})`);await cnc.click('#cancelModal');await cnc.click('#navList');await cnc.waitForTimeout(250);
const legacyId='legacy-manual-cnc',legacyRow=cnc.locator(`#ordersBody tr[data-id="${legacyId}"]`);
check(await legacyRow.count()===1,'manueller Legacy-Auftrag ohne AV-Übergabeflag ist im Bereich sichtbar');
await legacyRow.locator('select[data-f="machineId"]').selectOption('m2');await cnc.waitForSelector('#moveModal.show');await cnc.click('#confirmMove');
await cnc.waitForFunction(async id=>{const s=await(await fetch('/api/state')).json(),o=s.data.workSteps.find(x=>x.id===id);return o?.machineId==='m2'&&!Object.prototype.hasOwnProperty.call(o,'handoffUnassigned')},legacyId);
check(true,'Maschinenwechsel erhält beim Legacy-Auftrag das fehlende AV-Übergabeflag');
await legacyRow.locator('input[data-f="targetQty"]').fill('44');await legacyRow.locator('input[data-f="targetQty"]').press('Tab');
await cnc.waitForFunction(async id=>{const s=await(await fetch('/api/state')).json();return s.data.workSteps.find(x=>x.id===id)?.targetQty===44},legacyId);
check(true,'Bereich kann beim Legacy-Auftrag die Sollmenge ändern');
await cnc.waitForFunction(()=>document.querySelector('#saveState')?.textContent==='Server gespeichert');
const legacyFaResult=await cnc.evaluate(async id=>{const s=await(await fetch('/api/state')).json(),o=s.data.workSteps.find(x=>x.id===id);o.fa=o.faNumber='FA-MANUAL-1002';o.order='FA-MANUAL-1002';const h=await(await fetch('/api/health')).json(),r=await fetch('/api/state',{method:'PUT',headers:{'Content-Type':'application/json','X-MP-Client-Version':h.version},body:JSON.stringify({revision:s.revision,data:s.data,action:'Legacy order edit'})});return {status:r.status,body:await r.text()}},legacyId);
check(legacyFaResult.status===200,`Bereich kann beim Legacy-Auftrag die FA ändern (${legacyFaResult.status}: ${legacyFaResult.body})`);
await cnc.close();cnc=await login('cnclead');await cnc.click('#navList');await cnc.waitForTimeout(250);const deleteRow=cnc.locator(`#ordersBody tr[data-id="${legacyId}"]`);
check(await deleteRow.locator('button[data-act="delete"]').isEnabled(),'Bereich darf Legacy-Auftrag löschen');
await deleteRow.locator('button[data-act="delete"]').click();await cnc.locator('#askModal.show').waitFor();await cnc.click('#askOk');
await cnc.waitForFunction(async id=>{const s=await(await fetch('/api/state')).json();return !s.data.workSteps.some(x=>x.id===id)},legacyId);
check(true,'Bereich kann den Legacy-Auftrag löschen');
await cnc.click('#navPlan');await cnc.waitForFunction(()=>document.querySelector('#board')?.offsetParent!==null);
 check(await cnc.locator(`#board [data-handoff-plan="${rows[0]?.id}"]`).isVisible(),'matching department gets Plan action in the week backlog');await cnc.screenshot({path:'/tmp/av-handoff-backlog.png'});
await cnc.fill('#avBacklogSearch','not found');check(await cnc.locator('#board [data-backlog-card]:visible').count()===0,'department backlog search filters empty results');await cnc.fill('#avBacklogSearch','Kunde A');check(await cnc.locator('#board [data-backlog-card]:visible').count()===1,'department backlog search finds customer');
 await cnc.click(`#board [data-handoff-plan="${rows[0]?.id}"]`);await cnc.locator('#planModal.show').waitFor();check(await cnc.locator('#planResource').isVisible(),'Planen öffnet das Einplanungsfenster');await cnc.click('#planList');await cnc.waitForTimeout(250);
 const row=cnc.locator(`#ordersBody tr[data-id="${rows[0]?.id}"]`);await row.locator('input[data-f="hours"]').fill('4');await row.locator('input[data-f="hours"]').press('Tab');await cnc.waitForTimeout(250);if(await cnc.locator('#moveModal.show').count())await cnc.click('#confirmMove');await cnc.waitForTimeout(300);await row.locator('select[data-f="machineId"]').selectOption('m1');
 check(await cnc.locator('#moveModal.show').count()===1,'resource assignment opens planning impact preview');await cnc.screenshot({path:'/tmp/av-handoff-plan-preview.png'});
 await cnc.click('#confirmMove');await cnc.waitForTimeout(700);st=await getState(cnc);const assigned=st.workSteps.find(x=>x.id===rows[0]?.id);
 check(assigned?.handoffUnassigned===false&&assigned?.machineId==='m1'&&assigned?.hours===4,'department takes ownership with resource and runtime');
const leadDenied=await cnc.evaluate(async id=>{const s=await(await fetch('/api/state')).json();Object.assign(s.data.workSteps.find(x=>x.id===id),{fa:'TAMPERED',faNumber:'TAMPERED'});const h=await(await fetch('/api/health')).json();const r=await fetch('/api/state',{method:'PUT',headers:{'Content-Type':'application/json','X-MP-Client-Version':h.version},body:JSON.stringify({revision:s.revision,data:s.data,action:'Unauthorized dept edit'})});return r.status},rows[0]?.id);check(leadDenied===403,`server protects AV FA identity from department edits (${leadDenied})`);
 const conf=await login('konflead');await conf.click('#navPlan');await conf.waitForTimeout(250);
 check(await conf.locator(`#board [data-handoff-plan="${rows[1]?.id}"]`).isVisible(),'later step visible to its department');
 check((await conf.locator('#board').innerText()).includes('Vorgänger offen'),'backlog explains open predecessor');
 const admin=await login('admin');await admin.click('#navPlan');await admin.waitForTimeout(200);await admin.click('#quickAdd');
 check(await admin.locator('#qDialogTitle').innerText()==='Fertigungsauftrag anlegen'&&await admin.locator('#qFAField label').innerText()==='FA · Fertigungsauftrag *','admin quick order uses the prominent common FA header');
 check(await admin.locator('#qMachine').isVisible(),'standard order dialog retains resource selector');
 check(await admin.locator('#qHours').isVisible(),'standard order dialog retains its hours field');
 check(!(await admin.locator('#qAvDepartmentsField').isVisible()),'AV handoff editor stays hidden from admin quick orders');
 await admin.click('#cancelModal');await admin.click('#navOrders');await admin.locator('[data-project-open="p1"]').click();await admin.locator('#projectNewOrder').click();
 check(await admin.locator('#qAvDepartmentsField').isVisible()&&!(await admin.locator('#qMachine').isVisible())&&!(await admin.locator('#qHours').isVisible()),'admin project FA opens the resource-free department handoff dialog');
 await admin.fill('#qFA','FA-ADMIN-PROJECT');await admin.fill('#qQty','12');await admin.fill('#qDue','2026-11-30');await admin.selectOption('[data-av-department="0"]','cnc');
 check(await admin.locator('[data-av-hours="0"]').count()===0,'admin handoff does not offer hours for CNC');await admin.selectOption('[data-av-department="0"]','konf1');
 check(await admin.locator('[data-av-hours="0"]').isVisible(),'admin handoff exposes editable hours only for confection');await admin.fill('[data-av-hours="0"]','2.5');await admin.click('#createOrder');
 for(let i=0;i<50&&!(await getState(admin)).workSteps.some(x=>x.fa==='FA-ADMIN-PROJECT');i++)await admin.waitForTimeout(200);
 const adminHandoff=await getState(admin);const adminRows=adminHandoff.workSteps.filter(x=>x.fa==='FA-ADMIN-PROJECT');
 check(adminRows.length===1&&adminRows[0].projectId==='p1'&&adminRows[0].departmentId==='konf1'&&adminRows[0].handoffUnassigned===true&&!adminRows[0].machineId&&adminRows[0].hours===2.5,'admin project FA is saved in the correct department backlog without resource assignment '+JSON.stringify(adminRows));
 await browser.close();console.log(`AV handoff E2E: ${checks.filter(Boolean).length}/${checks.length} checks passed`);
 if(checks.includes(false))process.exitCode=1;
}catch(e){console.error(e);process.exitCode=1}finally{srv.kill('SIGTERM');try{rmSync(tmp,{recursive:true,force:true})}catch{}}

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
const srv=spawn('python3',['-c',py],{stdio:['ignore','pipe','inherit']});
const checks=[];const check=(v,label)=>{checks.push(!!v);if(!v)console.log('FAIL '+label)};
try{
 await new Promise((resolve,reject)=>{let out='';const t=setTimeout(()=>reject(new Error('Server start timeout '+out)),20000);srv.stdout.on('data',d=>{out+=String(d);if(out.includes('SEED-ERROR'))reject(new Error(out));if(out.includes('READY')){clearTimeout(t);resolve()}});srv.on('exit',c=>reject(new Error('Server exit '+c)))});
 const {chromium}=await import('playwright');const browser=await chromium.launch();
 async function login(user){const p=await browser.newPage({viewport:{width:1440,height:950},timezoneId:'Europe/Berlin'});p.on('pageerror',e=>checks.push(false));await p.goto(BASE);await p.fill('#loginUser',user);await p.fill('#loginPassword',PASS);await p.click('#loginBtn');await p.waitForFunction(()=>!document.getElementById('loginModal').classList.contains('show'));await p.waitForTimeout(300);return p}
 const av=await login('av');await av.click('#navPlan');await av.waitForTimeout(200);await av.click('#quickAdd');
 check(await av.locator('#orderModal .avOrderDialog').isVisible(),'AV sees wide FA dialog');
 check(await av.locator('#qAvDepartmentsField').isVisible(),'AV can choose production departments');
 check(!(await av.locator('#qMachine').isVisible()),'AV has no resource assignment field');await av.screenshot({path:'/tmp/av-handoff-dialog.png'});
 await av.fill('#qFA','FA-AV-5701');await av.selectOption('#qProject','p1');await av.fill('#qQty','120');await av.fill('#qDue','2026-10-30');
 await av.selectOption('#qAvDepartments',['cnc','konf1']);
 await av.locator('[data-av-due="cnc"]').fill('2026-10-22');await av.locator('[data-av-due="konf1"]').fill('2026-10-30');
 await av.fill('#qHours','3.5');await av.click('#createOrder');await av.waitForTimeout(900);
 const getState=async p=>p.evaluate(async()=>((await(await fetch('/api/state')).json()).data));let st=await getState(av);const rows=st.workSteps.filter(x=>x.fa==='FA-AV-5701');
 check(rows.length===2,'one stable work step per selected department');
 check(rows[0]?.departmentId==='cnc'&&rows[1]?.departmentId==='konf1'&&rows[1]?.predecessorIds?.[0]===rows[0]?.id,'ordered stable predecessor reference');
 check(rows[0]?.machineId===''&&rows[1]?.machineId===''&&rows[0]?.hours===0&&rows[1]?.hours===3.5,'no resource assigned; only confection hours captured');
 check(rows[0]?.dueDate==='2026-10-22'&&rows[1]?.dueDate==='2026-10-30','per department deadlines saved');
const denied=async(patch,id)=>av.evaluate(async([stepId,changes])=>{const state=await(await fetch('/api/state')).json();Object.assign(state.data.workSteps.find(x=>x.id===stepId),changes);const health=await(await fetch('/api/health')).json();const response=await fetch('/api/state',{method:'PUT',headers:{'Content-Type':'application/json','X-MP-Client-Version':health.version},body:JSON.stringify({revision:state.revision,data:state.data,action:'Unauthorized AV edit'})});return response.status},[id,patch]);
const denyMachine=await denied({machineId:'m1',handoffUnassigned:false,hours:4},rows[0]?.id);check(denyMachine===403,`server rejects AV resource assignment (${denyMachine})`);
const denyFlag=await denied({handoffUnassigned:false,machineId:'m1',hours:4},rows[0]?.id);check(denyFlag===403,`server rejects AV takeover marker bypass (${denyFlag})`);
const denyHours=await denied({hours:6},rows[0]?.id);check(denyHours===403,`server rejects AV CNC hours (${denyHours})`);
 let cnc=await login('cnclead');await cnc.click('#navPlan');await cnc.waitForTimeout(250);await cnc.click('#navList');await cnc.waitForTimeout(250);
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
 await cnc.click(`#board [data-handoff-plan="${rows[0]?.id}"]`);await cnc.waitForTimeout(250);
 const row=cnc.locator(`#ordersBody tr[data-id="${rows[0]?.id}"]`);await row.locator('input[data-f="hours"]').fill('4');await row.locator('input[data-f="hours"]').press('Tab');await cnc.waitForTimeout(250);if(await cnc.locator('#moveModal.show').count())await cnc.click('#confirmMove');await cnc.waitForTimeout(300);await row.locator('select[data-f="machineId"]').selectOption('m1');
 check(await cnc.locator('#moveModal.show').count()===1,'resource assignment opens planning impact preview');await cnc.screenshot({path:'/tmp/av-handoff-plan-preview.png'});
 await cnc.click('#confirmMove');await cnc.waitForTimeout(700);st=await getState(cnc);const assigned=st.workSteps.find(x=>x.id===rows[0]?.id);
 check(assigned?.handoffUnassigned===false&&assigned?.machineId==='m1'&&assigned?.hours===4,'department takes ownership with resource and runtime');
const leadDenied=await cnc.evaluate(async id=>{const s=await(await fetch('/api/state')).json();Object.assign(s.data.workSteps.find(x=>x.id===id),{fa:'TAMPERED',faNumber:'TAMPERED'});const h=await(await fetch('/api/health')).json();const r=await fetch('/api/state',{method:'PUT',headers:{'Content-Type':'application/json','X-MP-Client-Version':h.version},body:JSON.stringify({revision:s.revision,data:s.data,action:'Unauthorized dept edit'})});return r.status},rows[0]?.id);check(leadDenied===403,`server protects AV FA identity from department edits (${leadDenied})`);
 const conf=await login('konflead');await conf.click('#navPlan');await conf.waitForTimeout(250);
 check(await conf.locator(`#board [data-handoff-plan="${rows[1]?.id}"]`).isVisible(),'later step visible to its department');
 check((await conf.locator('#board').innerText()).includes('Vorgänger offen'),'backlog explains open predecessor');
 await browser.close();console.log(`AV handoff E2E: ${checks.filter(Boolean).length}/${checks.length} checks passed`);
 if(checks.includes(false))process.exitCode=1;
}catch(e){console.error(e);process.exitCode=1}finally{srv.kill('SIGTERM');try{rmSync(tmp,{recursive:true,force:true})}catch{}}

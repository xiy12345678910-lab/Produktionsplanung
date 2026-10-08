#!/usr/bin/env node
// Wochenplan: geplanten FA per Drag & Drop auf andere Ressource/Tag ziehen → Auswirkungsprüfung → gespeichert.
// node tests/e2e_dnd.mjs
import { spawn } from 'node:child_process';import { mkdtempSync, rmSync } from 'node:fs';import { tmpdir } from 'node:os';import path from 'node:path';
const results=[];const check=(ok,label)=>{results.push(!!ok);console.log((ok?'PASS ':'FAIL ')+label)};
const SRC='/home/user/Produktionsplanung',PORT=18991,PASS='Dnd-Test-1234',tmp=mkdtempSync(path.join(tmpdir(),'mp-dnd-'));
const py=`
import ipaddress,json,sys
from pathlib import Path
sys.path.insert(0,${JSON.stringify(SRC)})
import server
server.DATA_DIR=Path(${JSON.stringify(tmp)});server.DB_PATH=server.DATA_DIR/'x.sqlite3'
server.ALLOWED_NETWORK=ipaddress.ip_network('127.0.0.0/8');server.init_db(seed='werbetechnik');server.create_or_reset_admin('admin',${JSON.stringify(PASS)})
with server.DB_LOCK,server.db_session() as con:
 old=json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()['json']);new=json.loads(json.dumps(old))
 new['workSteps']=[{'id':'w1','sequence':10,'planningType':'MACHINE','pos':10,'departmentId':'cnc','projectId':'','predecessorIds':[],'fa':'FA-DND','faNumber':'FA-DND','order':'FA-DND','ab':'','wt':'','machineId':'m1','altMachineId':'','allowAlternative':False,'articleNo':'','description':'','targetQty':1,'dueDate':'','baselinePlan':None,'hours':3,'goodQty':0,'scrapQty':0,'status':'planned','direction':'forward','anchorMode':'none','requiredStart':'','requiredFinish':''}]
 ok,c,r=server.validate_state(old,new);print('SEED',ok,c,r,flush=True)
 con.execute('UPDATE state SET json=?,revision=revision+1 WHERE id=1',(json.dumps(new),))
httpd=server.MPHTTPServer(('127.0.0.1',${PORT}),server.Handler);print('READY',flush=True);httpd.serve_forever()`;
const srv=spawn('python3',['-c',py],{stdio:['ignore','pipe','inherit'],env:{...process.env,MP_CONFIG_DIR:path.join(tmp,'config')}});
await new Promise(r=>srv.stdout.on('data',d=>{if(String(d).includes('READY'))r()}));
const {chromium}=await import('playwright');const b=await chromium.launch();const p=await b.newPage({viewport:{width:1440,height:950},timezoneId:'Europe/Berlin'});
const errors=[];p.on('pageerror',e=>errors.push(e.message));
await p.clock.install({time:new Date('2026-10-12T06:00:00Z')});
await p.goto(`http://127.0.0.1:${PORT}/`);await p.fill('#loginUser','admin');await p.fill('#loginPassword',PASS);await p.click('#loginBtn');await p.waitForTimeout(1200);
await p.click('#navPlan');await p.waitForTimeout(500);
const chip=p.locator('[data-board-order="w1"]').first();check(await chip.getAttribute('draggable')==='true','Geplanter FA ist im Wochenplan ziehbar');
const target=p.locator('[data-machine="m2"][data-date]').nth(2);
await chip.dragTo(target);await p.waitForTimeout(600);
check(await p.locator('#moveModal.show').count()===1,'Ablegen öffnet die Auswirkungsprüfung');
if(await p.locator('#moveModal.show').count()){await p.click('#confirmMove');await p.waitForTimeout(800)}
const st=await p.evaluate(async()=>(await (await fetch('/api/state')).json()).data.workSteps[0]);check(st.machineId==='m2'&&String(st.requiredStart).startsWith('2026-10-14')&&st.anchorMode==='soft',`Server speichert neue Ressource und Wunschtag (${st.machineId} ${st.requiredStart} ${st.anchorMode})`);
check(errors.length===0,'Keine JavaScript-Fehler '+errors.join(' | '));
await b.close();srv.kill();rmSync(tmp,{recursive:true,force:true});
console.log(`\n${results.filter(Boolean).length}/${results.length} bestanden`);process.exit(results.every(Boolean)?0:1);

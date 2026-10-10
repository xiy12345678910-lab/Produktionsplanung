#!/usr/bin/env node
// #84: Bereich ohne Maschine/Linie – das Einplanungsfenster nennt den Grund (MP-PLAN-073) und führt zu den Maschinen.
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const SRC=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),PORT=18831,BASE=`http://127.0.0.1:${PORT}/`,PASS='E2E-NoRes-1234';
const tmp=mkdtempSync(path.join(tmpdir(),'mp-nores-'));
const py=`
import ipaddress,sys
from pathlib import Path
sys.path.insert(0,${JSON.stringify(SRC)})
import server
server.DATA_DIR=Path(${JSON.stringify(tmp)})
server.DB_PATH=server.DATA_DIR/'maschinenplanung.sqlite3'
server.ALLOWED_NETWORK=ipaddress.ip_network('127.0.0.0/8')
server.init_db(seed='werbetechnik')
server.create_or_reset_admin('admin',${JSON.stringify(PASS)})
httpd=server.MPHTTPServer(('127.0.0.1',${PORT}),server.Handler)
print('READY',flush=True)
httpd.serve_forever()
`;
const srv=spawn('python3',['-c',py],{stdio:['ignore','pipe','inherit'],env:{...process.env,MP_CONFIG_DIR:path.join(tmp,'config')}});
const checks=[];const check=(v,label)=>{checks.push(!!v);if(!v)console.log('FAIL '+label)};
try{
 await new Promise((resolve,reject)=>{let out='';const t=setTimeout(()=>reject(new Error('Server start timeout '+out)),20000);srv.stdout.on('data',d=>{out+=String(d);if(out.includes('READY')){clearTimeout(t);resolve()}});srv.on('exit',c=>reject(new Error('Server exit '+c)))});
 const {chromium}=await import('playwright');const browser=await chromium.launch();
 const p=await browser.newPage({viewport:{width:1440,height:950},timezoneId:'Europe/Berlin'});const errs=[];p.on('pageerror',e=>errs.push(String(e)));
 // Nur im Test: Zugriff auf die IIFE (CSP der Testantwort entfernt), wie e2e_takt.
 await p.route(u=>['/','/index.html'].includes(new URL(u).pathname),async route=>{const resp=await route.fetch();let body=await resp.text();const end=body.lastIndexOf('})();');body=body.slice(0,end)+'window.__t=src=>eval(src);\n'+body.slice(end);const headers={...resp.headers()};delete headers['content-security-policy'];delete headers['content-length'];await route.fulfill({status:resp.status(),headers,body})});
 const ev=fn=>p.evaluate(src=>window.__t('('+src+')')(),fn.toString());
 await p.goto(BASE);await p.fill('#loginUser','admin');await p.fill('#loginPassword',PASS);await p.click('#loginBtn');
 await p.waitForFunction(()=>!document.getElementById('loginModal').classList.contains('show'));await p.waitForTimeout(300);
 // Nur im Client: Bereich ohne Ressource plus ein Auftrag darin (keine Speicherung nötig).
 await ev(()=>{data.departments.push({id:'zuschnitte',name:'Zuschnitte',planningType:'MACHINE',active:true});data.workSteps.push({id:'nores-1',departmentId:'zuschnitte',fa:'FA-NORES-1',order:'FA-NORES-1',description:'Zuschnitt',targetQty:5,hours:2,machineId:'',status:'planned',predecessorIds:[]});openPlanPopup('nores-1')});
 await p.locator('#planModal.show').waitFor();
 const txt=await p.locator('#planPreview').innerText();
 check(txt.includes('MP-PLAN-073')&&txt.includes('Zuschnitte'),'Vorschau nennt MP-PLAN-073 und den Bereich');
 check(await p.locator('#planResource').isDisabled(),'Ressourcenauswahl ist gesperrt');
 check(await p.locator('#planOk').isDisabled(),'Übernehmen ist gesperrt');
 await p.click('#planToMachines');await p.waitForTimeout(300);
 check(!(await p.locator('#planModal').evaluate(e=>e.classList.contains('show'))),'Fenster schließt');
 check(await p.locator('#settings.view.active').count()===1&&await p.locator('#machineSettingsPanel').isVisible(),'System → Maschinen & Schichtkalender ist offen');
 // Bereich mit Maschine: normale Auswahl bleibt.
 await ev(()=>{const o=data.workSteps.find(x=>x.departmentId==='cnc')||data.workSteps.find(x=>x.id==='nores-1');o.departmentId='cnc';openPlanPopup(o.id)});
 await p.locator('#planModal.show').waitFor();
 check(!(await p.locator('#planResource').isDisabled())&&!(await p.locator('#planPreview').innerText()).includes('MP-PLAN-073'),'Bereich mit Maschine: Auswahl frei, kein Hinweis');
 check(!errs.length,'keine Seitenfehler '+errs.join(' | '));
 await browser.close();
}finally{srv.kill();rmSync(tmp,{recursive:true,force:true})}
const ok=checks.every(Boolean);console.log(`${checks.filter(Boolean).length}/${checks.length} Prüfungen ${ok?'OK':'FEHLER'}`);process.exit(ok?0:1);

#!/usr/bin/env node
// Focused browser coverage for admin user layout/filters and the optional pallet module.
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'), PORT=18841, BASE=`http://127.0.0.1:${PORT}/`, PASS='E2E-Test-1234';
async function loadPlaywright(){try{return await import('playwright')}catch{}const root=execSync('npm root -g').toString().trim();return import(pathToFileURL(path.join(root,'playwright','index.mjs')).href)}
const results=[],check=(ok,label)=>{results.push(!!ok);if(!ok)console.log('FAIL '+label)};
const dir=mkdtempSync(path.join(tmpdir(),'mp-admin-v12191-'));
const py=`import ipaddress,json,sys
from pathlib import Path
sys.path.insert(0,${JSON.stringify(SRC)})
import server
server.DATA_DIR=Path(${JSON.stringify(dir)});server.DB_PATH=server.DATA_DIR/'maschinenplanung.sqlite3';server.ALLOWED_NETWORK=ipaddress.ip_network('127.0.0.0/8')
server.init_db(seed='werbetechnik');server.create_or_reset_admin('admin',${JSON.stringify(PASS)})
with server.DB_LOCK,server.db_session() as con:
 old=json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()['json']);new=json.loads(json.dumps(old));m=new['machines'][0]
 new['workSteps'].append({'id':'ws_admin_test','sequence':10,'planningType':'MACHINE','pos':10,'departmentId':m.get('departmentId') or 'cnc','projectId':'','predecessorIds':[],'fa':'FA-ADMIN-TEST','ab':'','wt':'','machineId':m['id'],'altMachineId':'','allowAlternative':False,'order':'FA-ADMIN-TEST','articleNo':'A-1','description':'Admin browser test','targetQty':1,'dueDate':'','baselinePlan':None,'hours':1,'goodQty':0,'scrapQty':0,'status':'planned','direction':'forward','anchorMode':'none','requiredStart':'','requiredFinish':'','createdAt':server.now_iso(),'lockedStart':'','lockedSegments':[],'actualStartedAt':'','runningSince':'','pausedAt':'','pauseIntervals':[],'remainingHours':None,'lastStatusCheckAt':''})
 ok,code,reason=server.validate_state(old,new)
 if not ok: raise RuntimeError(f'seed validation {code}: {reason}')
 con.execute('UPDATE state SET json=?,revision=revision+1 WHERE id=1',(json.dumps(new,ensure_ascii=False),))
httpd=server.MPHTTPServer(('127.0.0.1',${PORT}),server.Handler);print('READY',flush=True);httpd.serve_forever()`;
const srv=spawn('python3',['-c',py],{stdio:['ignore','pipe','inherit'], env: { ...process.env, MP_CONFIG_DIR: path.join(dir, 'config') } });
try{
 await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(new Error('server timeout')),20000);srv.stdout.on('data',d=>{if(String(d).includes('READY')){clearTimeout(t);resolve()}});srv.on('exit',c=>reject(new Error('server exit '+c)))});
 const {chromium}=await loadPlaywright(),browser=await chromium.launch();
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}});page.on('pageerror',e=>check(false,'page error '+e.message));
  await page.goto(BASE);await page.fill('#loginUser','admin');await page.fill('#loginPassword',PASS);await page.click('#loginBtn');await page.waitForFunction(()=>!document.getElementById('loginModal').classList.contains('show'));
  await page.click('#navSystem');await page.waitForTimeout(200);await page.click('[data-systab="users"]');await page.waitForTimeout(300);
  // #52: HTTP-Betrieb (kein config/tls) -> Systemstatus empfiehlt HTTPS.
  const status=await page.waitForFunction(()=>document.getElementById('adminStatusPanel')?.innerText||'',null,{timeout:15000}).then(h=>h.jsonValue()).catch(()=>'');
  check(status.includes('HTTPS nicht eingerichtet')&&status.includes('HTTPS_Einrichten.ps1'),'system status warns: HTTPS nicht eingerichtet (HTTP)');
  const initial=await page.evaluate(()=>({viewport:innerWidth,body:document.body.scrollWidth,grid:document.querySelector('.userCreateGrid').getBoundingClientRect().toJSON(),main:document.querySelector('.main').getBoundingClientRect().toJSON(),fields:['newUsername','newUserPassword','newUserRole','newUserDepartment'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return [r.left,r.right,r.top,r.bottom]})}));
  check(initial.fields.every(r=>r[1]===r[0]||r[0]>=0&&r[1]<=390&&r[3]>r[2]),'user creation fields fit narrow viewport '+JSON.stringify(initial));
  await page.setViewportSize({width:1440,height:900});const desktop=await page.evaluate(()=>['newUsername','newUserPassword','newUserRole','newUserDepartment'].map(id=>document.getElementById(id).getBoundingClientRect().toJSON()));check(desktop.every(r=>r.left>=0&&r.right<=1440&&r.width>0),'user fields fit desktop viewport');check(desktop.every((a,i)=>desktop.slice(i+1).every(b=>a.bottom<=b.top||b.bottom<=a.top||a.right<=b.left||b.right<=a.left)),'desktop form fields do not overlap');await page.setViewportSize({width:390,height:844});
  await page.fill('#newUsername','GlobalOps');await page.fill('#newUserPassword',PASS);await page.selectOption('#newUserRole','project_management');
  check(!(await page.locator('#newUserDepartment').isVisible()),'global role hides department selector');await page.click('#addUserBtn');await page.waitForTimeout(400);
  await page.fill('#newUsername','ScopedOps');await page.fill('#newUserPassword',PASS);await page.selectOption('#newUserRole','department_lead');
  await page.selectOption('#newUserDepartment',{index:1});await page.click('#addUserBtn');await page.waitForTimeout(400);
  await page.fill('#userSearch','ScopedOps');await page.waitForFunction(()=>{const t=document.getElementById('userAdminList').innerText;return t.includes('ScopedOps')&&!t.includes('GlobalOps')});check(true,'username search filters list');
  await page.fill('#userSearch','');await page.selectOption('#userRoleFilter','department_lead');await page.waitForFunction(()=>{const t=document.getElementById('userAdminList').innerText;return t.includes('ScopedOps')&&!t.includes('GlobalOps')});check(true,'role filter works');
  await page.selectOption('#userRoleFilter','');await page.selectOption('#userDepartmentFilter',{index:1});await page.waitForFunction(()=>document.getElementById('userAdminList').innerText.includes('ScopedOps'));check(true,'department filter works');await page.selectOption('#userRoleFilter','department_lead');await page.waitForFunction(()=>{const t=document.getElementById('userAdminList').innerText;return t.includes('ScopedOps')&&!t.includes('GlobalOps')});check(true,'role and department filters combine');
  await page.fill('#userSearch','no-such-user');await page.waitForFunction(()=>document.getElementById('userAdminList').innerText.includes('Keine Benutzer'));check(true,'empty state shown');await page.click('#userFilterReset');await page.waitForFunction(()=>!document.getElementById('userAdminList').innerText.includes('Keine Benutzer'));
  // #84: Passwort für einen Benutzer setzen – zweimal eingeben, Tippfehler werden abgefangen, Login mit neuem Passwort geht.
  const scopedBtn=await page.evaluate(()=>[...document.querySelectorAll('#userAdminList [data-user-password]')].find(b=>(b.closest('.userRow,tr,div')?.innerText||'').includes('ScopedOps'))?.dataset.userPassword);
  await page.click(`#userAdminList [data-user-password="${scopedBtn}"]`);await page.waitForSelector('#askModal.show');
  check((await page.textContent('#askTitle')).includes('ScopedOps'),'Passwort-Dialog nennt den Benutzer');
  await page.fill('#askInput','NeuPasswort-2026');await page.click('#askOk');await page.waitForTimeout(150);await page.fill('#askInput','NeuPasswort-2027');await page.click('#askOk');await page.waitForTimeout(300);
  check((await page.evaluate(()=>document.getElementById('errorModal').innerText)).includes('MP-AUTH-024'),'abweichende Wiederholung wird abgewiesen');await page.click('#closeError');
  await page.click(`#userAdminList [data-user-password="${scopedBtn}"]`);await page.waitForSelector('#askModal.show');
  await page.fill('#askInput','NeuPasswort-2026');await page.click('#askOk');await page.waitForTimeout(150);await page.fill('#askInput','NeuPasswort-2026');await page.click('#askOk');await page.waitForTimeout(700);
  // #84: Fehlerdialog landet im Fehlerprotokoll; Systemstatus zeigt "Fehler seit Update" mit Code.
  await page.click('#navPlan');await page.waitForTimeout(300);await page.click('#navSystem');await page.waitForTimeout(300);
  await page.waitForSelector('#adminStatusPanel #errLogCodes',{timeout:8000}).catch(()=>{});
  check((await page.locator('#adminStatusPanel').innerText().catch(()=>'')).includes('MP-AUTH-024'),'Systemstatus: Fehler seit Update zeigt MP-AUTH-024 aus dem Fehlerdialog');
  {const ctx=await browser.newContext();const v=(await (await ctx.request.get(BASE+'api/health')).json()).version;const r=await ctx.request.post(BASE+'api/login',{headers:{'X-MP-Client-Version':v,'Origin':BASE.slice(0,-1)},data:{username:'ScopedOps',password:'NeuPasswort-2026'}});check(r.status()===200,`Login mit neu gesetztem Passwort (${r.status()} ${await r.text()})`);await ctx.close()}
  await page.click('[data-systab="functions"]');await page.waitForTimeout(250);check(await page.locator('[data-admin-mod="palletLabels"]').isVisible(),'module control is in own admin tab');
  check(!(await page.locator('[data-admin-mod="palletLabels"]').isChecked()),'pallet labels default off');
  const gate=await page.evaluate(async()=>{const version=(await(await fetch('/api/health')).json()).version;const r=await fetch('/api/production/not-a-real-order/label',{method:'POST',headers:{'Content-Type':'application/json','X-MP-Client-Version':version},body:JSON.stringify({requestId:'test-off-0001',quantity:1})});return [r.status,(await r.json()).errorCode]});check(gate[0]===403&&gate[1]==='MP-MOD-001','disabled module rejects label API '+JSON.stringify(gate));
  await page.locator('[data-admin-mod="palletLabels"]').check();await page.waitForTimeout(350);check(await page.locator('#palletTemplatePanel').isVisible(),'enabling module reveals template design');
  await page.click('#addPalletTemplate');await page.waitForTimeout(200);check(await page.locator('#palletTemplatePanel .tableCard').isVisible(),'print template preview is shown');
  await page.locator('#palletTemplatePanel [data-key="accentColor"]').first().fill('#aabbcc');await page.locator('#palletTemplatePanel [data-key="showBarcode"]').first().uncheck();await page.waitForTimeout(350);
  await page.evaluate(()=>window.print=()=>{window.__palletPrint={html:document.getElementById('weeklyPrintBody').innerHTML,style:document.getElementById('weeklyPrintBody').getAttribute('style')}});await page.click('#navList');await page.waitForTimeout(250);await page.locator('#ordersBody [data-act="label"]').first().click();await page.fill('#askInput','1');await page.click('#askOk');await page.waitForFunction(()=>!!window.__palletPrint,{timeout:10000});const printed=await page.evaluate(()=>window.__palletPrint);check(!printed.html.includes('<svg'),'template barcode option controls actual print output');check(printed.style.includes('#aabbcc'),'template accent color reaches actual print output');
  await page.click('#navSystem');await page.waitForTimeout(150);await page.click('[data-systab="functions"]');await page.waitForTimeout(150);await page.locator('[data-admin-mod="palletLabels"]').uncheck();await page.click('#askOk');await page.waitForTimeout(350);check(!(await page.locator('#palletTemplatePanel').isVisible()),'disabling hides template management');
  await page.locator('[data-admin-mod="palletLabels"]').check();check(await page.locator('#palletTemplatePanel .tableCard').first().waitFor({timeout:10000}).then(()=>true,()=>false),'template survives disable and reactivation');
  // V12.23.0: System → Bereich anlegen mit Planungslogik (gleiche Implementierung wie GF-Steuerung).
  await page.fill('#adminNewDepartment','Siebdruck 2');await page.selectOption('#adminNewDepartmentLogic','cycle');await page.click('#adminAddDepartment');await page.waitForTimeout(600);
  await page.fill('#adminNewDepartment','Montage');await page.selectOption('#adminNewDepartmentLogic','line');await page.click('#adminAddDepartment');await page.waitForTimeout(600);
  {const st=await page.evaluate(async()=>(await(await fetch('/api/state')).json()).data),sd=st.departments.find(d=>d.name==='Siebdruck 2'),mo=st.departments.find(d=>d.name==='Montage'),res=id=>st.machines.filter(m=>m.departmentId===id);
   check(sd?.formats===true&&res(sd.id).length===1&&res(sd.id)[0].kind==='machine','System: Bereich mit Planungslogik Takt bekommt Formate und eine Maschine');
   check(mo&&!mo.formats&&res(mo.id)[0]?.kind==='line'&&res(mo.id)[0]?.effortScaling===true,'System: Bereich mit Planungslogik Personenstunden bekommt eine Linie');
   check((await page.locator('#adminDepartmentControls').innerText()).includes('Takt · Formate'),'System: Planungslogik je Bereich sichtbar')}
  // V12.23.0: GF-Steuerung und Historie abschaltbar – Menüpunkte verschwinden, Wiedereinschalten stellt sie her.
  check(await page.locator('#navGF').isVisible(),'GF-Steuerung visible while enabled');
  await page.locator('[data-admin-mod="gf"]').uncheck();await page.locator('[data-admin-mod="history"]').uncheck();await page.waitForFunction(()=>{const off=document.documentElement.getAttribute('data-mod-off')||'';return /\bgf\b/.test(off)&&/\bhistory\b/.test(off)},null,{timeout:5000}).catch(()=>{});
  check(await page.locator('#navGF').isHidden()&&await page.evaluate(()=>{const off=document.documentElement.getAttribute('data-mod-off')||'';return /\bgf\b/.test(off)&&/\bhistory\b/.test(off)}),'disabled GF-Steuerung/Historie hide menu and views');
  await page.locator('[data-admin-mod="gf"]').check();await page.locator('[data-admin-mod="history"]').check();await page.waitForFunction(()=>!/\b(gf|history)\b/.test(document.documentElement.getAttribute('data-mod-off')||''),null,{timeout:5000}).catch(()=>{});
  check(await page.locator('#navGF').isVisible()&&await page.evaluate(()=>!/\b(gf|history)\b/.test(document.documentElement.getAttribute('data-mod-off')||'')),'re-enabling restores GF-Steuerung/Historie');
  // V12.23.0: GF-Steuerung und Historie abschaltbar – Menüpunkte verschwinden, Wiedereinschalten stellt sie her.
  check(await page.locator('#navGF').isVisible(),'GF-Steuerung visible while enabled');
  await page.locator('[data-admin-mod="gf"]').uncheck();await page.locator('[data-admin-mod="history"]').uncheck();await page.waitForFunction(()=>{const off=document.documentElement.getAttribute('data-mod-off')||'';return /\bgf\b/.test(off)&&/\bhistory\b/.test(off)},null,{timeout:5000}).catch(()=>{});
  check(await page.locator('#navGF').isHidden()&&await page.evaluate(()=>{const off=document.documentElement.getAttribute('data-mod-off')||'';return /\bgf\b/.test(off)&&/\bhistory\b/.test(off)}),'disabled GF-Steuerung/Historie hide menu and views');
  await page.locator('[data-admin-mod="gf"]').check();await page.locator('[data-admin-mod="history"]').check();await page.waitForFunction(()=>!/\b(gf|history)\b/.test(document.documentElement.getAttribute('data-mod-off')||''),null,{timeout:5000}).catch(()=>{});
  check(await page.locator('#navGF').isVisible()&&await page.evaluate(()=>!/\b(gf|history)\b/.test(document.documentElement.getAttribute('data-mod-off')||'')),'re-enabling restores GF-Steuerung/Historie');
  const errors=await page.locator('#errorModal.show').count();check(errors===0,'no admin error dialog');await page.close();
 }finally{await browser.close()}
}finally{srv.kill('SIGTERM');rmSync(dir,{recursive:true,force:true})}
console.log(`${results.filter(Boolean).length}/${results.length} bestanden`);if(results.includes(false))process.exit(1);

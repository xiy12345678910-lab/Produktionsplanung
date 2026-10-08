#!/usr/bin/env node
// #52 Lizenz: Admin-Panel, Upload (abgewiesen/gültig), Banner Kulanz/Nur Lesen, Sperre im UI und am Server, 390 px ohne Querscroll.
// Der Server läuft über tests/license_test_server.py mit Test-Schlüsselpaar und verstellbarer Uhr (clock_offset).
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SRC=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'), PORT=18852, BASE=`http://127.0.0.1:${PORT}/`, PASS='E2E-Lizenz-1234';
async function loadPlaywright(){try{return await import('playwright')}catch{}const root=execSync('npm root -g').toString().trim();return import(pathToFileURL(path.join(root,'playwright','index.mjs')).href)}
const results=[],check=(ok,label)=>{results.push(!!ok);console.log((ok?'ok   ':'FAIL ')+label)};
const dir=mkdtempSync(path.join(tmpdir(),'mp-e2e-license-'));
const srv=spawn('python3',[path.join(SRC,'tests','license_test_server.py'),dir,String(PORT),PASS],{stdio:['ignore','pipe','inherit']});
const DAY=86400;
async function login(page,user){await page.goto(BASE);await page.fill('#loginUser',user);await page.fill('#loginPassword',PASS);await page.click('#loginBtn');await page.waitForFunction(()=>!document.getElementById('loginModal').classList.contains('show'))}
const noHScroll=page=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1&&document.body.scrollWidth<=innerWidth+1);
async function closeError(page){const code=await page.locator('#errorModal.show #errorCode').textContent({timeout:5000}).catch(()=>'');if(code)await page.click('#closeError');return code}
try{
 await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(new Error('server timeout')),60000);srv.stdout.on('data',d=>{if(String(d).includes('READY')){clearTimeout(t);resolve()}});srv.on('exit',c=>reject(new Error('server exit '+c)))});
 const {chromium}=await loadPlaywright(),browser=await chromium.launch();
 try{
  const ctx=await browser.newContext({viewport:{width:390,height:844}}),page=await ctx.newPage();page.on('pageerror',e=>check(false,'page error '+e.message));
  await login(page,'admin');
  // Kulanz: Admin sieht Hinweis, Lizenzpanel zeigt "fehlt" und 30 Tage.
  await page.waitForFunction(()=>!document.getElementById('licBanner').hidden,null,{timeout:10000});
  check((await page.locator('#licBanner').innerText()).includes('noch 30 Tage Kulanz'),'Admin-Banner in der Kulanz');
  await page.click('#navSystem');
  const panel=await page.waitForFunction(()=>document.getElementById('adminLicensePanel')?.innerText||'',null,{timeout:15000}).then(h=>h.jsonValue());
  check(panel.includes('Lizenz')&&panel.includes('fehlt')&&panel.includes('noch 30 Tage'),'Lizenzpanel: Status fehlt, Kulanz 30 Tage');
  check(await noHScroll(page),'390 px: kein Querscroll (Kulanz)');
  const made=await page.evaluate(async pw=>{const CLIENT_VERSION=(await(await fetch('/api/health')).json()).version;const r=await fetch('/api/users',{method:'POST',headers:{'Content-Type':'application/json','X-MP-Client-Version':CLIENT_VERSION},body:JSON.stringify({username:'leser',password:pw,role:'viewer'})});return r.status},PASS);
  check(made===201,'Benutzer anlegen in der Kulanz möglich');
  // Kulanz abgelaufen -> nur Lesen
  writeFileSync(path.join(dir,'clock_offset'),String(31*DAY));
  await page.reload();await page.waitForFunction(()=>document.getElementById('licBanner').classList.contains('ro'),null,{timeout:15000});
  check((await page.locator('#licBanner').innerText()).includes('Nur Lesen'),'Banner: Nur Lesen');
  await page.click('#navSystem');await page.waitForFunction(()=>(document.getElementById('adminLicensePanel')?.innerText||'').includes('nur Lesen'),null,{timeout:15000});
  check(await page.locator('#operatorSingle').isDisabled(),'Einstellungen im UI gesperrt');
  check(await page.locator('#licFile').isEnabled(),'Lizenz-Upload bleibt bedienbar');
  // Selbst wenn ein Feld im Browser freigeschaltet wird, verweigert save() das Speichern.
  await page.evaluate(()=>{const e=document.getElementById('operatorSingle');e.disabled=false;e.value='4';e.dispatchEvent(new Event('change'))});
  check(await closeError(page)==='MP-LIC-001','Fehlermeldung MP-LIC-001 im UI');
  const put=await page.evaluate(async()=>{const CLIENT_VERSION=(await(await fetch('/api/health')).json()).version;const s=await(await fetch('/api/state')).json();const r=await fetch('/api/state',{method:'PUT',headers:{'Content-Type':'application/json','X-MP-Client-Version':CLIENT_VERSION},body:JSON.stringify({revision:s.revision,data:s.data})});return [r.status,(await r.json()).errorCode]});
  check(put[0]===403&&put[1]==='MP-LIC-001','Server lehnt Speichern ab '+JSON.stringify(put));
  check(await noHScroll(page),'390 px: kein Querscroll (nur Lesen)');
  // Andere Rolle sieht den Nur-Lesen-Hinweis.
  const ctx2=await browser.newContext({viewport:{width:390,height:844}}),vp=await ctx2.newPage();vp.on('pageerror',e=>check(false,'page error (leser) '+e.message));
  await login(vp,'leser');await vp.waitForFunction(()=>document.getElementById('licBanner').classList.contains('ro'),null,{timeout:15000});
  const vt=await vp.locator('#licBanner').innerText();check(vt.includes('Nur Lesen')&&vt.includes('Admin ansprechen'),'Leser sieht Nur-Lesen-Banner');
  check(await noHScroll(vp),'390 px: kein Querscroll (Leser)');
  // Upload: falsche Firma und falsche Signatur abgewiesen, gültige Datei hebt die Sperre auf.
  await page.setInputFiles('#licFile',path.join(dir,'wrong_tenant.key'));check(await closeError(page)==='MP-LIC-004','Upload falsche Firma abgewiesen');
  await page.setInputFiles('#licFile',path.join(dir,'bad_signature.key'));check(await closeError(page)==='MP-LIC-002','Upload falsche Signatur abgewiesen');
  await page.setInputFiles('#licFile',path.join(dir,'valid.key'));
  await page.waitForFunction(()=>(document.getElementById('adminLicensePanel')?.innerText||'').includes('E2E Test GmbH'),null,{timeout:15000});
  const ok=await page.locator('#adminLicensePanel').innerText();check(ok.includes('gültig')&&ok.includes('unbefristet'),'Panel: gültig, unbefristet');
  check(await page.locator('#licBanner').isHidden(),'Banner verschwindet nach gültiger Lizenz');
  await page.waitForFunction(()=>!document.getElementById('operatorSingle').disabled,null,{timeout:10000}).then(()=>check(true,'Bearbeiten wieder möglich'),()=>check(false,'Bearbeiten wieder möglich'));
  await vp.waitForFunction(()=>document.getElementById('licBanner').hidden,null,{timeout:30000}).then(()=>check(true,'Leser: Banner verschwindet per Long-Poll'),()=>check(false,'Leser: Banner verschwindet per Long-Poll'));
  check(await page.locator('#errorModal.show').count()===0,'kein offener Fehlerdialog');
  await ctx2.close();await ctx.close();
 }finally{await browser.close()}
}finally{srv.kill('SIGTERM');rmSync(dir,{recursive:true,force:true})}
console.log(`${results.filter(Boolean).length}/${results.length} bestanden`);if(results.includes(false))process.exit(1);

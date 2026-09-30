import { execSync } from 'node:child_process'; import path from 'node:path'; import { pathToFileURL } from 'node:url';
const g=execSync('npm root -g').toString().trim();
const { chromium } = await import(pathToFileURL(path.join(g,'playwright','index.mjs')).href);
const IMG=process.argv[2]; const PORT=process.env.MP_DEMO_PORT||'18999'; const only=(process.argv[3]||'').split(',').filter(Boolean);
const b=await chromium.launch(); const log=[];
async function session(user,pass='password123'){const ctx=await b.newContext({viewport:{width:1366,height:820},locale:'de-DE',timezoneId:'Europe/Berlin',acceptDownloads:true});await ctx.addInitScript(()=>{window.print=()=>{}});const p=await ctx.newPage();
 p.on('pageerror',e=>log.push(user+' pageerror '+e.message));await p.goto(`http://127.0.0.1:${PORT}/`);await p.fill('#loginUser',user);await p.fill('#loginPassword',pass);await p.click('#loginBtn');
 await p.waitForFunction(()=>!document.getElementById('loginModal').classList.contains('show'));await p.waitForTimeout(700);return p}
const W=(p,ms=400)=>p.waitForTimeout(ms);
async function reveal(p,sel){const l=p.locator(sel).first();if(await l.isVisible().catch(()=>false))return true;const tabs=p.locator('[data-systab]:visible');for(let i=0;i<await tabs.count();i++){await tabs.nth(i).click();await W(p,250);if(await l.isVisible().catch(()=>false))return true}return false}
async function shot(p,name,sel){await W(p,250);if(sel&&!(await reveal(p,sel))){log.push('not visible: '+name);return}if(sel){const l=p.locator(sel).first();await l.scrollIntoViewIfNeeded().catch(()=>{});await l.screenshot({path:`${IMG}/${name}.jpg`,type:'jpeg',quality:82})}else await p.screenshot({path:`${IMG}/${name}.jpg`,type:'jpeg',quality:82})}
async function err(p,label){const e=await p.evaluate(()=>{const m=document.getElementById('errorModal');return m?.classList.contains('show')?m.innerText.replace(/\s+/g,' ').slice(0,200):''});if(e){log.push(label+' ERROR: '+e);await p.keyboard.press('Escape');await W(p,200)}return e}
async function confirmAny(p){for(const id of ['askOk','confirmMove']){const l=p.locator('#'+id);if(await l.isVisible().catch(()=>false)){await l.click();await W(p,500)}}}
const want=r=>!only.length||only.includes(r);

// Erstellt die Bildschirmfotos für die Anleitungen, indem jede Rolle ihren Ablauf wirklich durchspielt.
// Aufruf: node capture.mjs <bildordner> [rollen,…]  (Demo-Server muss laufen, siehe README.md)
// ---------- Bereichsleitungen (je Abteilung)
for (const [user,dep] of [['lead_cnc','cnc'],['lead_k1','konf1'],['lead_sd','screenprint'],['lead_tz','thermoforming']]) { if(!want(user))continue;
 const p=await session(user);
 await p.click('#navPlan');await shot(p,`${dep}_01_wochenplan`);
 await shot(p,`${dep}_02_projektaufgaben`,'#deptProcessPanel');
 await p.click('#navList');await W(p);await shot(p,`${dep}_03_auftraege`);
 // Freigeben: erster Auftrag der Liste
 const rel=p.locator('#orders [data-act="release"]:not([disabled])').first();
 if(await rel.count()){await rel.click();await W(p,600);await shot(p,`${dep}_04_freigabe_dialog`);await confirmAny(p);await err(p,user+' release');await W(p,500);await shot(p,`${dep}_05_freigegeben`)}
 // Produktion
 await p.click('#modeProduction');await W(p,600);await shot(p,`${dep}_06_produktion_bereit`);
 const st=p.locator('[data-prod-start]:not([disabled])').first();
 if(await st.count()){await st.click();await W(p,600);await shot(p,`${dep}_07_start_dialog`);await confirmAny(p);await err(p,user+' start');await W(p,600);await shot(p,`${dep}_08_laeuft`);
  const pa=p.locator('[data-prod-pause]').first(); if(await pa.count()){await pa.click();await W(p,500);await confirmAny(p);await err(p,user+' pause');await shot(p,`${dep}_09_pausiert`);
   const re=p.locator('[data-prod-resume]:not([disabled])').first(); if(await re.count()){await re.click();await W(p,500);await confirmAny(p);await err(p,user+' resume')}}
  const fi=p.locator('[data-prod-finish]').first(); if(await fi.count()){await fi.click();await W(p,600);await shot(p,`${dep}_10_fertig_dialog`,'#finishModal .modalBox');await p.fill('#finishGood','100');await p.fill('#finishScrap','2');await p.click('#confirmFinish');await W(p,600);await confirmAny(p);await err(p,user+' finish');await shot(p,`${dep}_11_nach_fertig`)}}
 else log.push(user+': kein startbarer Auftrag');
 await p.click('#modePlanning');await W(p,400);
 // Personal
 await p.click('#navPersonnel');await W(p);await shot(p,`${dep}_12_personal_woche`);
 await p.click('[data-ptab=staff]');await W(p);await shot(p,`${dep}_13_mitarbeiter`);
 await p.click('[data-ptab=absence]');await W(p);await shot(p,`${dep}_14_abwesenheit`);
 // System
 await p.click('#navSystem');await W(p);await shot(p,`${dep}_15_system`);
 await shot(p,`${dep}_16_benutzer`,'#userAdminPanel, .panel:has(h3:text("Benutzer & Rechte"))');
 await p.context().close();
}
// ---------- Arbeitsvorbereitung
if(want('av')){const p=await session('av');
 await shot(p,'av_01_start');
 await p.click('#navList');await W(p);await p.click('#orders button:has-text("+ Neuer Auftrag")');await W(p,500);
 await p.selectOption('#qDept','konf1');await W(p,300);await p.fill('#qFS','FS-26-5300');
 const opts=await p.locator('#qProject option').allTextContents();const pi=opts.findIndex(t=>t.includes('302'));if(pi>=0)await p.selectOption('#qProject',{index:pi});
 await p.fill('#qDesc','Halter montiert');await p.fill('#qQty','200');await p.fill('#qHours','12');await p.fill('#qDue','2026-10-16');await W(p,300);
 await shot(p,'av_02_neuer_auftrag','#orderModal .modalBox');await p.click('#createOrder');await W(p,800);await confirmAny(p);await err(p,'av create');
 await shot(p,'av_03_nach_anlegen');
 await p.click('#navOrders');await W(p);await shot(p,'av_04_projekte');
 await p.locator('#projects').getByText('AB-26-303').first().click();await W(p,700);await shot(p,'av_05_projekt','#projectModal .modalBox');
 await p.context().close();}
// ---------- Projektmanagement
if(want('pm')){const p=await session('pm');
 await shot(p,'pm_01_tafel');
 await p.click('#projects button:has-text("+ Neues Projekt")');await W(p,400);
 await p.fill('#npCustomer','Stihl');await p.fill('#npName','Verkleidung');await p.fill('#npContact','Hr. Maier');await p.fill('#npDue','2026-12-04');
 const wf=await p.locator('#npWorkflow option').count(); if(wf>1)await p.selectOption('#npWorkflow',{index:1});
 await shot(p,'pm_02_neues_projekt','#newProjectModal .modalBox');await p.click('#npCreate');await W(p,900);await err(p,'pm create');
 await shot(p,'pm_03_projekt_offen','#projectModal .modalBox');
 
 await p.keyboard.press('Escape');await W(p,300);
 await p.locator('#projects').getByText('P-2026-1041').first().click();await W(p,700);await shot(p,'pm_05_projekt_bestand','#projectModal .modalBox');
 await p.keyboard.press('Escape');await W(p,300);
 await p.click('#navHistory');await W(p);await shot(p,'pm_06_historie');
 await p.context().close();}
// ---------- Vertrieb
if(want('sales')){const p=await session('sales');await shot(p,'sales_01_tafel');
 await p.locator('#projects').getByText('P-2026-1040').first().click();await W(p,700);await shot(p,'sales_02_projekt','#projectModal .modalBox');await p.keyboard.press('Escape');
 await p.click('#navReport');await W(p);await shot(p,'sales_03_report');await p.context().close();}
// ---------- Lesend
if(want('viewer')){const p=await session('viewer');await shot(p,'viewer_01_start');await p.context().close();}
// ---------- GF
if(want('gf')){const p=await session('gf');
 await shot(p,'gf_01_uebersicht');
 await shot(p,'gf_02_nachkalkulation','#evalPanel');
 await shot(p,'gf_03_abwesenheit','.panel:has(h3:text("Urlaub & Krankheit"))');
 await shot(p,'gf_04_leiharbeiter','.panel:has(h3:text("Leiharbeiter-Anfragen"))');
 await shot(p,'gf_05_einsatz','.panel:has(h3:text("Mitarbeitereinsatz dieser KW"))');
 await shot(p,'gf_06_auftraege','.panel:has(h3:text("Auftragsübersicht je Bereich"))');
 await p.click('#navOrders');await W(p);await shot(p,'gf_07_projekte');
 await p.click('#navGF');await W(p);await p.click('button:has-text("Monatsreport")');await W(p);await shot(p,'gf_08_report');
 await p.click('#navSystem');await W(p);await shot(p,'gf_09_abteilungen','#departmentsPanel');
 await p.context().close();}
// ---------- Admin
if(want('admin')){const p=await session('admin','adminpass1');
 await shot(p,'admin_01_wochenplan');
 await p.click('#navSystem');await W(p);await shot(p,'admin_02_system');await shot(p,'admin_15_abteilungen','#departmentsPanel');
 for (const [n,h] of [['03_maschinen','Maschinen & Linien'],['04_ci','Firmen-CI für Kundenpläne'],['05_schichten','Schichtzeiten & Pausen'],['06_kalender','Schichtkalender · Abweichungen'],['07_feiertage','Feiertage & Betriebsferien'],['08_stillstand','Maschinenstillstand / Wartung'],['09_daten','Darstellung & Daten'],['10_benutzer','Benutzer & Rechte'],['11_server','Server-Betrieb']])
   await shot(p,'admin_'+n,`#settings .panel:has(h3:text("${h}"))`).catch(e=>log.push('admin '+n+' '+e.message.slice(0,80)));
 await p.click('#sysAudit');await W(p);await shot(p,'admin_12_aenderungen');
 await p.click('#navSystem');await p.click('#sysHistory');await W(p);await shot(p,'admin_13_historie');
 await p.click('#navPersonnel');await W(p);await shot(p,'admin_14_personal');
 await p.context().close();}
console.log(log.join('\n')||'no errors'); await b.close();

#!/usr/bin/env node
// Phase 0 (#73): Referenzstand der Planungslogik (Scheduler) + Laufzeit-Benchmark.
// Fester Zeitpunkt und deterministisch erzeugte FA → Ergebnis (Start/Ende/Ressource/Konflikt) muss der Referenz
// tests/golden/schedule.json entsprechen. So fällt bei Umbauten (Phase 1–4) jede ungewollte Planänderung auf.
// Benchmark: 300 FA (V12.26.0 ~0,3 s lokal; vorher 2,5 s). Last: MP_BENCH_ORDERS=1500 (vorher 186 s, jetzt ~9 s).
// node tests/e2e_golden_schedule.mjs            vergleichen
// node tests/e2e_golden_schedule.mjs --update   Referenz bewusst neu schreiben
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const GOLDEN = path.join(SRC, 'tests', 'golden', 'schedule.json');
const UPDATE = process.argv.includes('--update');
const PORT = 18990, BASE = `http://127.0.0.1:${PORT}/`, PASS = 'E2E-Golden-1234';
// Montag 05.10.2026 05:00 Berlin – vor Schichtbeginn, damit die Woche vollständig planbar ist.
const NOW = new Date('2026-10-05T03:00:00Z');
const BENCH_ORDERS = Number(process.env.MP_BENCH_ORDERS || 300), BENCH_LIMIT_MS = Number(process.env.MP_BENCH_LIMIT_MS || 5000);
const tmp = mkdtempSync(path.join(tmpdir(), 'mp-golden-'));
const py = `
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
const srv = spawn('python3', ['-c', py], { stdio: ['ignore', 'pipe', 'inherit'], env: { ...process.env, MP_CONFIG_DIR: path.join(tmp, 'config') } });
const results = [], errors = [];
const check = (ok, label) => { results.push(!!ok); console.log((ok ? 'PASS ' : 'FAIL ') + label); };
let browser;
try {
  await new Promise((resolve, reject) => { const t = setTimeout(() => reject(new Error('Server start timeout')), 20000); srv.stdout.on('data', d => { if (String(d).includes('READY')) { clearTimeout(t); resolve(); } }); srv.on('exit', c => reject(new Error('Server exit ' + c))); });
  const { chromium } = await import('playwright'); browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 }, timezoneId: 'Europe/Berlin' });
  page.on('pageerror', e => errors.push(e.message));
  await page.clock.setFixedTime(NOW);
  await page.route(u => ['/', '/index.html'].includes(new URL(u).pathname), async route => {
    const resp = await route.fetch(); let body = await resp.text(); const end = body.lastIndexOf('})();');
    body = body.slice(0, end) + 'window.__t=src=>eval(src);\n' + body.slice(end);
    const headers = { ...resp.headers() }; delete headers['content-security-policy']; delete headers['content-length'];
    await route.fulfill({ status: resp.status(), headers, body });
  });
  const ev = (fn, arg) => page.evaluate(([src, a]) => window.__t('(' + src + ')')(a), [fn.toString(), arg]);
  await page.goto(BASE); await page.fill('#loginUser', 'admin'); await page.fill('#loginPassword', PASS); await page.click('#loginBtn');
  await page.waitForFunction(() => !document.getElementById('loginModal').classList.contains('show')); await page.waitForTimeout(400);

  // Deterministische FA je Maschine (LCG statt Math.random): Vorwärts, Rückwärts mit Termin, Wunschstart, Vorgänger, Trocknung.
  const build = n => ev(cfg => {
    let seed = 42; const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
    const ms = data.machines.filter(m => m.active !== false), base = { planningType: 'MACHINE', status: 'planned', altMachineId: '', allowAlternative: false, lockedSegments: [], pauseIntervals: [], baselinePlan: null, goodQty: 0, scrapQty: 0, projectId: '' };
    const day = k => { const d = new Date(Date.now() + k * 86400000); return dateKey(d); };
    data.history = []; data.workSteps = [];
    for (let i = 0; i < cfg.n; i++) {
      const m = ms[i % ms.length], kind = Math.floor(rnd() * 4), id = 'g' + i;
      const o = { ...base, id, fa: 'FA-G' + i, order: 'FA-G' + i, machineId: m.id, departmentId: m.departmentId, hours: Math.round((1 + rnd() * 15) * 4) / 4, targetQty: 10 + Math.floor(rnd() * 500), pos: (i + 1) * 10, predecessorIds: [], direction: 'forward', anchorMode: 'none', requiredStart: '', requiredFinish: '', dueDate: day(3 + Math.floor(rnd() * 20)) };
      if (kind === 1) Object.assign(o, { direction: 'backward', anchorMode: 'soft', requiredFinish: day(5 + Math.floor(rnd() * 15)) + 'T14:00' });
      if (kind === 2) Object.assign(o, { anchorMode: 'soft', requiredStart: day(1 + Math.floor(rnd() * 10)) + 'T08:00' });
      data.workSteps.push(o);
    }
    // Abgeschlossener Vorgänger mit Trocknung (V12.24.0) für die ersten FA jeder 10er-Gruppe.
    if (cfg.n) { data.history = [{ id: 'gh0', originalOrderId: 'gdone', recordType: 'done', status: 'done', machineId: ms[0].id, departmentId: ms[0].departmentId, fa: 'FA-GDONE', order: 'FA-GDONE', hours: 1, actualStartedAt: new Date(Date.now() - 7200000).toISOString(), actualFinishedAt: new Date(Date.now() - 3600000).toISOString(), dryingHours: 6 }]; for (let i = 0; i < cfg.n; i += 10) data.workSteps[i].predecessorIds = ['gdone']; }
    const t0 = performance.now(), r = calcSchedule(), ms_ = performance.now() - t0;
    const iso = d => d ? new Date(d).toISOString() : null;
    const out = {}; for (const o of data.workSteps) { const x = r[o.id] || {}; out[o.id] = [iso(x.start), iso(x.end), x.assignedMachineId || '', (x.segments || []).length, x.conflict || '', !!x.late]; }
    return { ms: ms_, out, machines: ms.length };
  }, { n });

  const golden = await build(120);
  check(Object.values(golden.out).every(x => x[0] || x[4]), `Referenzlauf: jeder FA ist geplant oder hat einen Konflikt (${golden.machines} Ressourcen, 120 FA)`);
  const text = JSON.stringify(golden.out, null, 1) + '\n';
  if (UPDATE || !existsSync(GOLDEN)) { mkdirSync(path.dirname(GOLDEN), { recursive: true }); writeFileSync(GOLDEN, text); console.log(`WRITE schedule.json (${text.length} Bytes)`); }
  else {
    const ref = JSON.parse(readFileSync(GOLDEN, 'utf8')), diff = Object.keys({ ...ref, ...golden.out }).filter(k => JSON.stringify(ref[k]) !== JSON.stringify(golden.out[k]));
    check(diff.length === 0, `Planungsergebnis entspricht der Referenz${diff.length ? ` – ${diff.length} Abweichungen, z. B. ${diff.slice(0, 3).map(k => `${k}: ${JSON.stringify(ref[k])} → ${JSON.stringify(golden.out[k])}`).join(' | ')}. Gewollt? node tests/e2e_golden_schedule.mjs --update` : ''}`);
  }
  const again = await build(120);
  check(JSON.stringify(again.out) === JSON.stringify(golden.out), 'Scheduler ist deterministisch (zweiter Lauf identisch)');

  // Gegenprobe: dieselbe Planung mit der alten linearen Belegungsprüfung und ohne Kalender-Zwischenspeicher (V12.26.0).
  {
    const n = Number(process.env.MP_EQUIV || 300);
    // Alte (lineare) Fassungen aus V12.25.0, unverändert übernommen.
    const OLD = { laneHit: 'function laneHit(segments,blocks,lanes,latest=false){let hit=null;for(const s of segments){const rel=blocks.filter(b=>segOverlap(s,b));if(rel.length<lanes)continue;const t0=s.start.getTime(),t1=s.end.getTime(),pts=[...new Set([t0,t1,...rel.flatMap(b=>[b.start.getTime(),b.end.getTime()])])].filter(t=>t>=t0&&t<=t1).sort((a,b)=>a-b);for(let i=0;i<pts.length-1;i++){const m=(pts[i]+pts[i+1])/2,act=rel.filter(b=>b.start.getTime()<m&&b.end.getTime()>m);if(act.length<lanes)continue;const h={start:new Date(Math.max(...act.map(b=>b.start.getTime()))),end:new Date(Math.min(...act.map(b=>b.end.getTime()))),lanes,count:act.length};if(!hit||(latest?h.end>hit.end:h.start<hit.start))hit=h}}return hit}', operatorConflict: "function operatorConflict(candidate,globalSegs,mid=''){if(!candidate.length)return null;if(mid&&!deptShared(deptOfMachine(mid)))return null;globalSegs=globalSegs.filter(x=>!x.machineId||deptShared(deptOfMachine(x.machineId)));const all=[...globalSegs.map(s=>({...s,candidate:false})),...candidate.map(s=>({...s,candidate:true}))];const min=Math.min(...candidate.map(s=>s.start.getTime())),max=Math.max(...candidate.map(s=>s.end.getTime()));const events=[...new Set(all.filter(s=>s.end>min&&s.start<max).flatMap(s=>[Math.max(min,s.start.getTime()),Math.min(max,s.end.getTime())]))].sort((a,b)=>a-b);for(let i=0;i<events.length-1;i++){const a=events[i],b=events[i+1];if(b<=a)continue;const mid=(a+b)/2,active=all.filter(s=>s.start.getTime()<mid&&s.end.getTime()>mid);if(!active.some(s=>s.candidate))continue;const cap=Math.min(...active.map(s=>operatorCapForShift(s.shift)));if(active.length>cap)return {start:new Date(a),end:new Date(b),count:active.length,cap}}return null}" };
    const linear = () => ev(src => { window.__fn = { firstHit, laneHit, operatorConflict, workIntervalsForDate }; const o = eval('(' + src + ')'); firstHit = function (segments, blocks, lanes = 1) { if (lanes > 1) return laneHit(segments, blocks, lanes); let hit = null; for (const s of segments) for (const b of blocks) if (segOverlap(s, b) && (!hit || b.start < hit.start)) hit = b; return hit }; laneHit = o.laneHit; operatorConflict = o.operatorConflict; workIntervalsForDate = workIntervalsForDateRaw; }, '{laneHit:' + OLD.laneHit + ',operatorConflict:' + OLD.operatorConflict + '}');
    const restore = () => ev(() => { ({ firstHit, laneHit, operatorConflict, workIntervalsForDate } = window.__fn); });
    const compare = async (label, setup) => {
      await ev(setup); const f = await build(n); await linear(); const sl = await build(n); await restore();
      const diff = Object.keys(f.out).filter(k => JSON.stringify(f.out[k]) !== JSON.stringify(sl.out[k]));
      check(diff.length === 0, `Gegenprobe ${label} ${n} FA: optimierte und lineare Logik identisch (${diff.length} Abweichungen; ${Math.round(f.ms)} ms vs ${Math.round(sl.ms)} ms)`);
    };
    await compare('1 Platz', () => {});
    // Parallelplätze (laneHit) und gemeinsame Bediener (operatorConflict) aktiv.
    await compare('2 Plätze + gemeinsame Bediener', () => { window.__lanes = data.machines.map(m => [m, m.lanes]); data.machines.forEach((m, k) => { if (k % 2 === 0) m.lanes = 2 }); window.__shared = data.departments.map(d => [d, d.sharedOperators]); data.departments.forEach(d => { d.sharedOperators = true }) });
    await ev(() => { for (const [m, l] of window.__lanes) { if (l === undefined) delete m.lanes; else m.lanes = l } for (const [d, v] of window.__shared) { if (v === undefined) delete d.sharedOperators; else d.sharedOperators = v } });
  }
  const bench = await build(BENCH_ORDERS);
  console.log(`BENCH Scheduler: ${BENCH_ORDERS} FA auf ${bench.machines} Ressourcen in ${Math.round(bench.ms)} ms`);
  check(bench.ms < BENCH_LIMIT_MS, `Benchmark: ${BENCH_ORDERS} FA in ${Math.round(bench.ms)} ms (Grenze ${BENCH_LIMIT_MS} ms)`);
} catch (e) {
  check(false, 'Unerwarteter Fehler: ' + (e.stack || e.message));
} finally {
  check(errors.length === 0, 'Keine JavaScript-Fehler ' + errors.slice(0, 3).join(' | '));
  await browser?.close(); srv.kill(); rmSync(tmp, { recursive: true, force: true });
}
console.log(`\n${results.filter(Boolean).length}/${results.length} bestanden`);
process.exit(results.every(Boolean) ? 0 : 1);

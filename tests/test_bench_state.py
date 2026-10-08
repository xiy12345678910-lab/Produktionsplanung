#!/usr/bin/env python3
"""Phase 0 (#73): Server-Benchmark mit großem Datenstand.

Speichert und lädt einen Datenstand mit vielen geplanten FA über die echte HTTP-API (PUT/GET /api/state,
inkl. Prüfung, Normalisierung, Schwärzung und Audit) und misst die Laufzeit. Grenzen sind großzügig
(CI ist langsamer); der Test soll Rückschritte um Größenordnungen auffangen.

Aufruf:  python tests/test_bench_state.py           (Standard: 2000 FA)
         MP_BENCH_FA=5000 python tests/test_bench_state.py
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix='mp-bench-'))
os.environ['MP_CONFIG_DIR'] = str(TMP / 'config')
import server  # noqa: E402

N = int(os.environ.get('MP_BENCH_FA', '2000'))
LIMIT_PUT, LIMIT_GET = float(os.environ.get('MP_BENCH_PUT_S', '15')), float(os.environ.get('MP_BENCH_GET_S', '5'))
server.DATA_DIR = TMP / 'data'
server.DB_PATH = server.DATA_DIR / 'maschinenplanung.sqlite3'
server.PBKDF2_ITERS = 1000
server.init_db(seed='werbetechnik')
server.load_config()
server.ALLOWED_NETWORK = server.ipaddress.ip_network('127.0.0.0/8')
server.Handler.log_message = lambda *a, **k: None
PASS = 'Bench-Test-Passwort-1'
server.create_or_reset_admin('admin', PASS)
httpd = server.MPHTTPServer(('127.0.0.1', 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def req(method, path, body=None, cookie=''):
    conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=300)
    conn.request(method, path, json.dumps(body) if body is not None else None,
                 {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION, 'Cookie': cookie})
    r = conn.getresponse(); st = r.status; ck = (r.getheader('Set-Cookie') or '').split(';')[0]
    payload = json.loads(r.read() or b'{}'); conn.close()
    return st, payload, ck


results = []
def check(ok, label):
    results.append(bool(ok)); print(('PASS ' if ok else 'FAIL ') + label)


_, _, ck = req('POST', '/api/login', {'username': 'admin', 'password': PASS})
st, state, _ = req('GET', '/api/state', None, ck)
data = state['data']
machines = [m for m in data['machines'] if m.get('active') is not False]
data['workSteps'] = [{
    'id': f'b{i}', 'sequence': (i + 1) * 10, 'planningType': 'MACHINE', 'pos': (i + 1) * 10, 'departmentId': m['departmentId'], 'projectId': '',
    'predecessorIds': [], 'fa': f'FA-B{i}', 'faNumber': f'FA-B{i}', 'order': f'FA-B{i}', 'ab': '', 'wt': '', 'machineId': m['id'],
    'altMachineId': '', 'allowAlternative': False, 'articleNo': '', 'description': f'Bench {i}', 'targetQty': 100, 'dueDate': '2026-12-18',
    'baselinePlan': None, 'hours': 4, 'goodQty': 0, 'scrapQty': 0, 'status': 'planned', 'direction': 'forward', 'anchorMode': 'none',
    'requiredStart': '', 'requiredFinish': '', 'createdAt': '2026-10-07T05:00:00Z'}
    for i, m in ((i, machines[i % len(machines)]) for i in range(N))]

t0 = time.perf_counter()
st, body, _ = req('PUT', '/api/state', {'revision': state['revision'], 'data': data, 'action': 'Benchmark'}, ck)
put_s = time.perf_counter() - t0
check(st == 200, f'Großer Datenstand gespeichert ({N} FA) {"" if st == 200 else body}')
print(f'BENCH PUT /api/state {N} FA: {put_s:.2f} s')
check(st == 200 and put_s < LIMIT_PUT, f'Speichern {N} FA in {put_s:.2f} s (Grenze {LIMIT_PUT:.0f} s)')

t0 = time.perf_counter()
st, state2, _ = req('GET', '/api/state', None, ck)
get_s = time.perf_counter() - t0
print(f'BENCH GET /api/state {N} FA: {get_s:.2f} s ({len(json.dumps(state2)) // 1024} KB)')
check(st == 200 and len(state2['data']['workSteps']) == N, 'Datenstand vollständig geladen')
check(st == 200 and get_s < LIMIT_GET, f'Laden {N} FA in {get_s:.2f} s (Grenze {LIMIT_GET:.0f} s)')

# Kleine Folgeänderung auf großem Stand: muss schnell bleiben (Diff/Audit nur der Änderung).
if not state2['data']['workSteps']:
    sys.exit(1)
state2['data']['workSteps'][0]['description'] = 'geändert'
t0 = time.perf_counter()
st, body, _ = req('PUT', '/api/state', {'revision': state2['revision'], 'data': state2['data'], 'action': 'Benchmark klein'}, ck)
small_s = time.perf_counter() - t0
print(f'BENCH PUT kleine Änderung bei {N} FA: {small_s:.2f} s')
check(st == 200 and small_s < LIMIT_PUT, f'Kleine Änderung gespeichert in {small_s:.2f} s')

httpd.shutdown(); httpd.server_close()
print(f'\n{sum(results)}/{len(results)} bestanden')
sys.exit(0 if all(results) else 1)

#!/usr/bin/env python3
"""Phase 0 (#73): Referenzstände („Golden“) des Servers vor den großen Umbauten.

Hält das heutige Verhalten fest, damit die Backend-Trennung (Phase 1) und das Datenmodell (Phase 2)
nichts unbemerkt verändern:
  1. Datenstand nach Migration/Normalisierung des realistischen Test-Datenstands (make_test_db.py)
  2. Sichtbare Daten je Rolle (Bereichs-/Rechte-Schwärzung)
  3. Rechte-Matrix: Rolle × Endpunkt → HTTP-Status (+ Fehlercode)
  4. Schreib-Matrix: Rolle × Datenbereich (Schlüssel von /api/state) → Status/Fehlercode einer minimalen Änderung

Aufruf:  python tests/test_golden.py            # vergleichen
         python tests/test_golden.py --update   # Referenz bewusst neu schreiben (nur bei gewollter Änderung)
Arbeitet nur in Temp-Ordnern.
"""
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / 'tests' / 'golden'
UPDATE = '--update' in sys.argv
TMP = Path(tempfile.mkdtemp(prefix='mp-golden-'))
os.environ['MP_CONFIG_DIR'] = str(TMP / 'config')
DATA = TMP / 'data'
subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_db.py'), str(DATA)], check=True, stdout=subprocess.DEVNULL, env={**os.environ})
sys.path.insert(0, str(ROOT))
import server  # noqa: E402

server.DATA_DIR = DATA
server.DB_PATH = DATA / 'maschinenplanung.sqlite3'
server.PBKDF2_ITERS = 1000
server.now_iso = lambda: '2026-10-07T05:00:00Z'
server.init_db(seed='werbetechnik')  # migriert den vorhandenen Datenstand (idempotent)
server.load_config()
server.ALLOWED_NETWORK = server.ipaddress.ip_network('127.0.0.0/8')
server.Handler.log_message = lambda *a, **k: None
PASS = 'Golden-Test-Passwort-1'
server.create_or_reset_admin('admin', PASS)
httpd = server.MPHTTPServer(('127.0.0.1', 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()

TS = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$')
VOLATILE = {'revision', 'serverRevision', 'lastLoginAt', 'sessionExpires'}


def mask(x):
    """Zeitstempel und Laufzeitwerte neutralisieren; Struktur und fachliche Werte bleiben."""
    if isinstance(x, dict):
        return {k: ('<volatile>' if k in VOLATILE else mask(v)) for k, v in sorted(x.items())}
    if isinstance(x, list):
        return [mask(v) for v in x]
    if isinstance(x, str) and TS.match(x):
        return '<ts>'
    return x


def req(method, path, body=None, cookie=''):
    conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=20)
    conn.request(method, path, json.dumps(body) if body is not None else None,
                 {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION, 'Cookie': cookie})
    r = conn.getresponse(); status = r.status; ck = (r.getheader('Set-Cookie') or '').split(';')[0]
    raw = r.read(); conn.close()
    try:
        payload = json.loads(raw or b'{}')
    except ValueError:
        payload = {}
    return status, payload, ck


def login(name):
    st, body, ck = req('POST', '/api/login', {'username': name, 'password': PASS})
    assert st == 200, (name, st, body)
    return ck


ADMIN = login('admin')
STATE = req('GET', '/api/state', None, ADMIN)[1]['data']
DEP = next(d['id'] for d in STATE['departments'] if d.get('kind', 'production') == 'production' and d.get('active') is not False)
USERS = [('gf1', 'gf', ''), ('pm1', 'project_management', ''), ('av1', 'production_planning', ''), ('vt1', 'sales', ''),
         ('lead1', 'department_lead', DEP), ('dep1', 'department_deputy', DEP), ('prod1', 'production', DEP), ('view1', 'viewer', '')]
for name, role, dep in USERS:
    st, body, _ = req('POST', '/api/users', {'username': name, 'password': PASS, 'role': role, 'departmentId': dep}, ADMIN)
    assert st == 201, (name, st, body)
COOKIES = {'admin': ADMIN, **{name: login(name) for name, _, _ in USERS}}

GET_ENDPOINTS = ['/api/state', '/api/revision', '/api/session', '/api/config', '/api/users', '/api/roles', '/api/history-archive',
                 '/api/notifications', '/api/chat/channels', '/api/updates', '/api/templates']


def visible(data):
    """Je Sammlung: sichtbare Datensatz-IDs (bzw. Anzahl/Typ) – zeigt die Schwärzung je Rolle."""
    out = {}
    for k, v in sorted(data.items()):
        if isinstance(v, list):
            ids = [str(x.get('id')) for x in v if isinstance(x, dict) and x.get('id') is not None]
            out[k] = sorted(ids) if len(ids) == len(v) else len(v)
        else:
            out[k] = type(v).__name__
    return out


snap_state = mask(STATE)
snap_scope, snap_perm = {}, {}
for user, ck in COOKIES.items():
    perm = {}
    for path in GET_ENDPOINTS:
        st, body, _ = req('GET', path, None, ck)
        perm['GET ' + path] = [st, body.get('errorCode', '')] if st >= 400 else [st]
        if path == '/api/state' and st == 200:
            snap_scope[user] = visible(body['data'])
            # Unveränderter Datenstand zurück: muss für jede Rolle erlaubt sein (kein Schreiben).
            st2, b2, _ = req('PUT', '/api/state', {'revision': body['revision'], 'data': body['data'], 'action': 'Golden no-op'}, ck)
            perm['PUT /api/state (unverändert)'] = [st2, b2.get('errorCode', '')] if st2 >= 400 else [st2]
    st, body, _ = req('POST', '/api/demand/reserve', {'requestId': f'golden-{user}', 'callOffId': 'x'}, ck)
    perm['POST /api/demand/reserve (unbekannt)'] = [st, body.get('errorCode', '')]
    st, body, _ = req('POST', '/api/production/x_unbekannt/start', {'requestId': f'golden-p-{user}'}, ck)
    perm['POST /api/production/<unbekannt>/start'] = [st, body.get('errorCode', '')]
    snap_perm[user] = perm

# --- 4. Schreib-Matrix: je Rolle und Top-Level-Schlüssel eine minimale Änderung per PUT /api/state ---------------
def admin_state():
    b = req('GET', '/api/state', None, ADMIN)[1]
    return b['revision'], b['data']


# #73 Phase 0: Sammlungen, die der Test-Datenstand leer lässt, mit je einem gültigen Datensatz füllen,
# damit die Schreib-Matrix sie prüft. Erst nach Datenstand-, Sichtbarkeits- und Rechte-Referenz (die bleiben unverändert).
# Planbare Sammlungen speichert der Admin per PUT (Server prüft sie dabei); serverseitig geführte Sammlungen
# (Produktion, Bedarf, Historie) entstehen nur über eigene Endpunkte und werden deshalb direkt in die Datenbank geschrieben.
def seed_empty_collections():
    rev, data = admin_state()
    mach = next(m for m in data['machines'] if m.get('departmentId') == DEP)
    emp = next(e for e in data['employees'] if e.get('departmentId') == DEP)
    fmt_dep = next((d['id'] for d in data['departments'] if d.get('formats')), DEP)
    plannable = {
        'yearRules': [{'machineId': mach['id'], 'year': 2026, 'mode': '1'}],
        'weekRules': [{'machineId': mach['id'], 'year': 2026, 'week': 44, 'mode': '1'}],
        'dayRules': [{'machineId': mach['id'], 'date': '2026-10-17', 'mode': '1', 'extraMinutes': 0}],
        'exceptions': [{'date': '2026-12-24', 'mode': '0'}],
        'machineBlocks': [{'id': 'blk_golden', 'machineId': mach['id'], 'start': '2026-10-20T06:00', 'end': '2026-10-20T08:00', 'note': 'Wartung'}],
        'personnelAssignments': [{'employeeId': emp['id'], 'machineId': mach['id'], 'date': '2026-10-12', 'shift': 'early', 'start': '06:00', 'end': '14:00'}],
        'personnelAbsences': [{'employeeId': emp['id'], 'date': '2026-10-13', 'label': 'Urlaub'}],
        'weeklyEmployeeDeployments': [{'employeeId': emp['id'], 'weekStart': '2026-10-12', 'departmentId': DEP}],
        'departmentStaffNeeds': [{'departmentId': DEP, 'weekStart': '2026-10-12', 'requested': 1}],
        'baseFormats': [{'id': 'bf_golden', 'departmentId': fmt_dep, 'name': 'Platte 1000', 'L': 1000, 'B': 1000}],
        'formats': [{'id': 'fmt_golden', 'number': 'F-GOLD', 'departmentId': fmt_dep, 'name': 'Golden-Format', 'L': 1000, 'B': 800}],
        'planVersions': [{'id': 'pv_golden', 'name': 'Golden-Stand', 'createdAt': '2026-10-07T05:00:00Z'}],
    }
    for key, recs in plannable.items():
        if data.get(key):
            continue
        data[key] = recs
        rev, cur = admin_state()
        cur[key] = recs
        if key == 'personnelAssignments':  # Zuordnung braucht die Maschinenfreigabe des Mitarbeiters
            for e in cur['employees']:
                if e['id'] == emp['id']:
                    e['skills'] = sorted(set(e.get('skills') or []) | {mach['id']})
        st, body, _ = req('PUT', '/api/state', {'revision': rev, 'data': cur, 'action': 'Golden Seed ' + key}, ADMIN)
        assert st == 200, ('seed', key, st, body)
    # palletTemplates: Funktion Palettenetiketten ist im Test-Datenstand aus, Admin-PUT waere MP-MOD-001.
    server_owned = {
        'palletTemplates': [{'id': 'tpl_golden', 'name': 'Standard', 'shelfLifeDays': 30, 'fromAddress': 'Firma A', 'toAddress': 'Kunde B'}],
        'productionEvents': [{'id': 'ev_golden', 'departmentId': DEP, 'orderId': 'o_golden', 'type': 'start', 'ts': '2026-10-07T05:00:00Z'}],
        'history': [{'id': 'h_golden', 'originalOrderId': 'old_golden', 'recordType': 'done', 'fa': 'FA-ALT', 'departmentId': DEP, 'machineId': mach['id'], 'goodQty': 1, 'scrapQty': 0, 'finishedAt': '2026-10-01T10:00:00Z'}],
        'frameOrders': [{'id': 'fo_golden', 'number': 'RA-GOLD', 'customer': 'K', 'articleId': 'A', 'departmentId': DEP, 'totalQty': 10, 'status': 'open'}],
    }
    with server.db_session() as con:
        s = json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
        for key, recs in server_owned.items():
            if not s.get(key):
                s[key] = recs
        con.execute('UPDATE state SET json=?,revision=revision+1 WHERE id=1', (json.dumps(s),))


seed_empty_collections()


ORIG_DATA = json.loads(json.dumps(admin_state()[1]))
PROBE_FIELDS = ('name', 'note', 'description', 'label')


def probe_change(data, key):
    """Minimale Änderung von data[key]; liefert (neuer Wert, None) oder (None, Skip-Marke)."""
    v = data.get(key)
    if isinstance(v, list):
        if not v:
            return None, 'empty'
        rec = v[0]
        if not isinstance(rec, dict):
            return None, 'nondict-skip'
        new = list(v); rec = dict(rec)
        field = next((f for f in PROBE_FIELDS if isinstance(rec.get(f), str)), None)
        if field:
            rec[field] = rec[field] + ' x'
        else:
            rec['goldenProbe'] = 'x'
        new[0] = rec
        return new, None
    if isinstance(v, dict):
        return {**v, 'goldenProbe': 'x'}, None
    return None, 'scalar-skip'


WRITE_KEYS = [k for k in sorted(ORIG_DATA) if k not in ('meta', 'ui', 'audit')]
snap_write = {}
for user, ck in COOKIES.items():
    row = {}
    for key in WRITE_KEYS:
        st, body, _ = req('GET', '/api/state', None, ck)
        if st != 200:
            row[key] = ['get-' + str(st)]
            continue
        data = json.loads(json.dumps(body['data']))
        new, skip = probe_change(data, key)
        if skip:
            row[key] = skip
            continue
        data[key] = new
        st, b2, _ = req('PUT', '/api/state', {'revision': body['revision'], 'data': data, 'action': 'Golden Schreib-Matrix'}, ck)
        row[key] = [st, b2.get('errorCode', '')] if st >= 400 else [st]
        if st == 200:  # Fixture wiederherstellen
            rev, _cur = admin_state()
            rs, rb, _ = req('PUT', '/api/state', {'revision': rev, 'data': ORIG_DATA, 'action': 'Golden Restore'}, ADMIN)
            assert rs == 200, ('restore', user, key, rs, rb)
    snap_write[user] = row

print('Schreib-Matrix (ok=200, Zahl=HTTP-Status, -=übersprungen):')
for user, row in snap_write.items():
    cells = []
    for key, r in row.items():
        if isinstance(r, str):
            continue
        cells.append(key + '=' + ('ok' if r[0] == 200 else str(r[0])))
    ok_keys = [k for k, r in row.items() if not isinstance(r, str) and r[0] == 200]
    print(f'  {user:6} ok: {",".join(ok_keys) or "-"} | ' + ' '.join(c for c in cells if not c.endswith('=ok')))
print()

SNAPS = {'state_fixture.json': snap_state, 'scope_by_role.json': snap_scope, 'permissions.json': snap_perm, 'write_matrix.json': snap_write}
results = []
GOLDEN.mkdir(exist_ok=True)
for name, snap in SNAPS.items():
    path = GOLDEN / name
    text = json.dumps(snap, ensure_ascii=False, indent=1, sort_keys=True) + '\n'
    if UPDATE or not path.exists():
        path.write_text(text, encoding='utf-8')
        print(f'WRITE {name} ({len(text)} Bytes)')
        results.append(True)
        continue
    ok = path.read_text(encoding='utf-8') == text
    results.append(ok)
    print(('PASS ' if ok else 'FAIL ') + f'{name} entspricht der Referenz')
    if not ok:
        import difflib
        diff = list(difflib.unified_diff(path.read_text(encoding='utf-8').splitlines(), text.splitlines(), 'referenz', 'aktuell', lineterm='', n=1))
        print('\n'.join(diff[:40]) + ('\n…' if len(diff) > 40 else ''))
        print('  Gewollte Änderung? Dann: python tests/test_golden.py --update und die Referenz mit committen.')

httpd.shutdown(); httpd.server_close()
print(f'\n{sum(results)}/{len(results)} bestanden')
sys.exit(0 if all(results) else 1)

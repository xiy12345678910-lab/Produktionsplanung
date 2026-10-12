#!/usr/bin/env python3
"""V12.27.0: Bereiche ohne Mengenmeldung (noQuantity, z. B. Formbau): nur Start/Pause/Stopp, keine Mengen.

Aufruf:  python tests/test_no_quantity.py   (nur Temp-Ordner, nie Live-Daten)
"""
import copy
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix='mp-noqty-'))
os.environ['MP_CONFIG_DIR'] = str(TMP / 'config')
import server  # noqa: E402

server.DATA_DIR = TMP / 'data'
server.DB_PATH = server.DATA_DIR / 'maschinenplanung.sqlite3'
server.PBKDF2_ITERS = 1000
server.init_db(seed='werbetechnik')
server.load_config()
with server.db_session() as con:
    BASE = json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
M = BASE['machines'][0]
DEP = M['departmentId']
server.now_iso = lambda: '2026-10-07T05:00:00Z'
PASS = 'NoQty-Test-Passwort-1'


class NoQuantity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.ALLOWED_NETWORK = server.ipaddress.ip_network('127.0.0.0/8')
        server.create_or_reset_admin('admin', PASS)
        server.Handler.log_message = lambda *a, **k: None
        cls.httpd = server.MPHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.admin = cls.login('admin')

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close()

    @classmethod
    def req(cls, method, path, body=None, cookie=''):
        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=15)
        headers = {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION, 'Cookie': cookie}
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = conn.getresponse(); status = r.status; ck = (r.getheader('Set-Cookie') or '').split(';')[0]
        payload = json.loads(r.read() or '{}'); conn.close()
        return status, payload, ck

    @classmethod
    def login(cls, name):
        return cls.req('POST', '/api/login', {'username': name, 'password': PASS})[2]

    def db(self):
        with server.db_session() as con:
            return json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0])

    def put_db(self, s):
        with server.db_session() as con:
            con.execute('UPDATE state SET json=?,revision=revision+1 WHERE id=1', (json.dumps(s),))

    def order(self, oid):
        seg = {'start': '2026-10-07T07:00', 'end': '2026-10-07T09:00', 'shift': 'single'}
        return {'id': oid, 'sequence': 10, 'planningType': 'MACHINE', 'pos': 10, 'departmentId': DEP, 'projectId': '', 'predecessorIds': [],
                'fa': oid, 'faNumber': oid, 'order': oid, 'ab': '', 'wt': '', 'machineId': M['id'], 'description': 'Form A', 'targetQty': 7,
                'hours': 2, 'goodQty': 0, 'scrapQty': 0, 'status': 'released', 'direction': 'forward', 'anchorMode': 'none',
                'baselinePlan': {'machineId': M['id'], 'crew': 1, 'setupMinutes': 0, 'start': seg['start'], 'end': seg['end'], 'segments': [seg]}}

    n = 0

    def act(self, oid, action, body=None):
        NoQuantity.n += 1
        return self.req('POST', f'/api/production/{oid}/{action}', {'requestId': f'noqty-req-{NoQuantity.n:05d}', **(body or {})}, self.admin)

    def test_flag_validation_and_runtime(self):
        st, state, _ = self.req('GET', '/api/state', None, self.admin)
        bad = copy.deepcopy(state); bad['data']['departments'][0]['noQuantity'] = 'ja'
        st, body, _ = self.req('PUT', '/api/state', bad, self.admin)
        self.assertEqual((st, body.get('errorCode')), (400, 'MP-DEPT-007'))
        good = copy.deepcopy(state)
        for d in good['data']['departments']:
            if d['id'] == DEP:
                d['noQuantity'] = True
        st, body, _ = self.req('PUT', '/api/state', good, self.admin)
        self.assertEqual(st, 200, body)
        s = self.db(); s['workSteps'].append(self.order('fa_nq')); self.put_db(s)
        self.assertEqual(self.act('fa_nq', 'start')[0], 200)
        st, body, _ = self.act('fa_nq', 'partial', {'goodQty': 2})
        self.assertEqual((st, body.get('errorCode')), (400, 'MP-PROD-060'))
        st, body, _ = self.act('fa_nq', 'finish', {'goodQty': 5})
        self.assertEqual((st, body.get('errorCode')), (400, 'MP-PROD-060'))
        st, body, _ = self.act('fa_nq', 'finish')
        self.assertEqual(st, 200, body)
        h = next(x for x in self.db()['history'] if x.get('originalOrderId') == 'fa_nq')
        self.assertEqual((h['goodQty'], h['scrapQty'], h['status']), (0, 0, 'done'))

    def test_normal_department_unchanged(self):
        s = self.db()
        for d in s['departments']:
            if d['id'] == DEP:
                d.pop('noQuantity', None)
        s['workSteps'].append(self.order('fa_std')); self.put_db(s)
        self.assertEqual(self.act('fa_std', 'start')[0], 200)
        self.assertEqual(self.act('fa_std', 'partial', {'goodQty': 2})[0], 200)

    def test_order_without_fa_uses_title(self):
        # #84: Bereiche ohne Mengenmeldung legen eigene Auftraege an; FA/AB/WT optional, der Titel traegt den Auftrag.
        s = copy.deepcopy(BASE)
        for d in s['departments']:
            if d['id'] == DEP:
                d['noQuantity'] = True
        o = self.order('fa_title'); o.update(fa='', faNumber='', order='Form A', status='planned', baselinePlan=None)
        s['workSteps'].append(o)
        self.assertTrue(server.validate_state(BASE, s)[0], server.validate_state(BASE, s))
        o['order'] = ''
        self.assertEqual(server.validate_state(BASE, s)[1], 'MP-STEP-014')


    def test_department_may_change_due_date_only_without_quantity(self):
        # V12.28.2: In Werkstattfertigung pflegt die Abteilung den Endtermin auch nachtraeglich; sonst die AV.
        for noqty, allowed in ((True, True), (False, False)):
            old = copy.deepcopy(BASE)
            for d in old['departments']:
                if d['id'] == DEP:
                    d['noQuantity'] = noqty
            o = self.order('fa_due'); o.update(status='planned', baselinePlan=None, dueDate='2026-11-02')
            old['workSteps'].append(o)
            new = copy.deepcopy(old)
            next(x for x in new['workSteps'] if x['id'] == 'fa_due')['dueDate'] = '2026-11-20'
            ok, msg = server.department_change_allowed(old, new, DEP)
            self.assertEqual(ok, allowed, (noqty, msg))
            if not allowed:
                self.assertIn('AV-Termin', msg)
        # AV-Uebergabe in den Werkstattbereich: der AV-Termin bleibt Sache der AV.
        new2 = copy.deepcopy(old); old2 = copy.deepcopy(old)
        for d in (old2, new2):
            for dep in d['departments']:
                if dep['id'] == DEP:
                    dep['noQuantity'] = True
            next(x for x in d['workSteps'] if x['id'] == 'fa_due')['handoffUnassigned'] = False
        next(x for x in new2['workSteps'] if x['id'] == 'fa_due')['dueDate'] = '2026-11-20'
        ok, msg = server.department_change_allowed(old2, new2, DEP)
        self.assertFalse(ok, msg)


    def test_department_may_change_hours_on_line_resource_only_without_quantity(self):
        # V12.28.3: Werkstatt-Arbeitsplatz (kind line, effortScaling) - Stunden pflegt die Abteilung; sonst die AV.
        for noqty, allowed in ((True, True), (False, False)):
            old = copy.deepcopy(BASE)
            for d in old['departments']:
                if d['id'] == DEP:
                    d['noQuantity'] = noqty
            for m in old['machines']:
                if m['id'] == M['id']:
                    m.update(kind='line', effortScaling=True)
            o = self.order('fa_hours'); o.update(status='planned', baselinePlan=None, hours=4)
            old['workSteps'].append(o)
            new = copy.deepcopy(old)
            next(x for x in new['workSteps'] if x['id'] == 'fa_hours')['hours'] = 6
            ok, msg = server.department_change_allowed(old, new, DEP)
            self.assertEqual(ok, allowed, (noqty, msg))
            if not allowed:
                self.assertIn('Konfektionsstunden', msg)


if __name__ == '__main__':
    unittest.main()

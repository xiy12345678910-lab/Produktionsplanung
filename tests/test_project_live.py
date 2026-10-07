#!/usr/bin/env python3
"""Project read scope for production users and existing production API access."""
import copy
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix='mp-project-live-'))
os.environ['MP_CONFIG_DIR'] = str(TMP / 'config')
sys.path.insert(0, str(ROOT))
import server

server.DATA_DIR = TMP / 'data'
server.DB_PATH = server.DATA_DIR / 'maschinenplanung.sqlite3'
server.PBKDF2_ITERS = 1000
server.init_db(seed='werbetechnik')
server.load_config()
# HTTP production starts must remain valid outside working hours and weekends.
server.now_iso = lambda: '2026-10-07T05:00:00Z'
with server.db_session() as con:
    BASE = json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
MACHINE = BASE['machines'][0]
OWN = MACHINE['departmentId']
OTHER_MACHINE = next((m for m in BASE['machines'] if m['departmentId'] != OWN), None)
OTHER = OTHER_MACHINE['departmentId'] if OTHER_MACHINE else 'foreign-department'


def fixture():
    s = copy.deepcopy(BASE)
    s['projects'] = [{
        'id': 'p_live', 'number': 'P-100', 'customer': 'Kunde sichtbar', 'name': 'Projekt Live',
        'phase': 'accepted', 'ab': 'AB-100', 'dueDate': '2026-10-30',
        'customerPlan': {'secret': 'KUNDENKALKULATION'}, 'offer': {'secret': 'ANGEBOT'},
        'development': {'secret': 'ENTWICKLUNG'},
        'processes': [{'id': 'pr_own', 'areaId': OWN, 'title': 'Eigener Bereich', 'status': 'open'},
                      {'id': 'pr_foreign', 'areaId': OTHER, 'title': 'Fremder Bereich', 'status': 'open'}],
        'log': [{'id': 'log_other', 'actor': 'av', 'text': 'AV vertraulich'}],
    }, {
        'id': 'p_foreign', 'number': 'P-SECRET', 'customer': 'Fremdkunde', 'phase': 'accepted',
        'processes': [], 'log': [],
    }]
    common = {'sourceType': 'PROJECT', 'planningType': 'MACHINE', 'projectId': 'p_live', 'machineId': MACHINE['id'],
              'departmentId': OWN, 'hours': 8, 'targetQty': 100, 'goodQty': 0, 'scrapQty': 0,
              'remainingQty': 100, 'status': 'released', 'direction': 'forward', 'anchorMode': 'none',
              'predecessorIds': [], 'baselinePlan': {'machineId': MACHINE['id'], 'crew': 1}}
    s['workSteps'] = [{**common, 'id': 'fa_live', 'fa': 'FA-LIVE-1', 'order': 'FA-LIVE-1'}]
    if OTHER_MACHINE:
        s['workSteps'].append({**common, 'id': 'fa_foreign', 'fa': 'FA-SECRET-1', 'order': 'FA-SECRET-1',
                               'projectId': 'p_foreign', 'departmentId': OTHER, 'machineId': OTHER_MACHINE['id']})
    s['history'] = []
    return s


class ProjectLiveScope(unittest.TestCase):
    def test_production_project_snapshot_is_department_scoped_and_masks_commercial_data(self):
        s = fixture()
        user = {'id': 2, 'username': 'prod', 'role': 'production', 'department_id': OWN}
        shown = server.redact_state(s, user)
        self.assertEqual([p['id'] for p in shown['projects']], ['p_live'])
        self.assertEqual([x['id'] for x in shown['workSteps']], ['fa_live'])
        self.assertEqual([p['id'] for p in shown['projects'][0]['processes']], ['pr_own'])
        payload = json.dumps(shown, ensure_ascii=False)
        for secret in ('P-SECRET', 'Fremdkunde', 'FA-SECRET-1', 'KUNDENKALKULATION', 'ANGEBOT', 'ENTWICKLUNG', 'AV vertraulich'):
            self.assertNotIn(secret, payload)
        project = shown['projects'][0]
        for field in ('customerPlan', 'offer', 'development'):
            self.assertFalse(project.get(field), f'{field} muss für production maskiert sein')
        # The production role continues to use operational production endpoints.
        server.production_apply(s, user, 'fa_live', 'start', {'requestId': 'prod-live-start-01'}, '2026-10-07T05:00:00Z')
        server.production_apply(s, user, 'fa_live', 'partial', {'requestId': 'prod-live-part-01', 'goodQty': 25}, '2026-10-07T05:15:00Z')
        refreshed = server.redact_state(s, user)
        self.assertEqual(refreshed['workSteps'][0]['goodQty'], 25)
        self.assertEqual(refreshed['workSteps'][0]['remainingQty'], 75)


class ProjectLiveHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.ALLOWED_NETWORK = server.ipaddress.ip_network('127.0.0.0/8')
        server.create_or_reset_admin('admin', 'Test-Passwort-1')
        cls.httpd = server.MPHTTPServer(('127.0.0.1', 0), server.Handler)
        server.Handler.log_message = lambda *a, **k: None
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        _, _, cls.admin_cookie = cls.req_static('POST', '/api/login', {'username': 'admin', 'password': 'Test-Passwort-1'}, '')
        _, user, _ = cls.req_static('POST', '/api/users', {'username': 'project-prod', 'password': 'Test-Passwort-1', 'role': 'production', 'departmentId': OWN}, cls.admin_cookie)
        cls.prod_id = user['id']
        _, _, cls.prod_cookie = cls.req_static('POST', '/api/login', {'username': 'project-prod', 'password': 'Test-Passwort-1'}, '')

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    @classmethod
    def req_static(cls, method, path, body=None, cookie=None):
        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=10)
        headers = {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION}
        if cookie is not None:
            headers['Cookie'] = cookie
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = conn.getresponse()
        status = response.status
        set_cookie = (response.getheader('Set-Cookie') or '').split(';')[0]
        payload = json.loads(response.read() or '{}')
        conn.close()
        return status, payload, set_cookie

    def setUp(self):
        with server.db_session() as con:
            con.execute('DELETE FROM production_requests')
            con.execute('UPDATE state SET json=?,revision=10 WHERE id=1', (json.dumps(fixture()),))

    def req(self, method, path, body=None):
        return self.req_static(method, path, body, self.prod_cookie)

    def test_production_can_read_own_project_but_cannot_write_state(self):
        status, snapshot, _ = self.req('GET', '/api/state')
        self.assertEqual(status, 200)
        self.assertEqual([x['id'] for x in snapshot['data']['projects']], ['p_live'])
        self.assertEqual([x['id'] for x in snapshot['data']['workSteps']], ['fa_live'])
        self.assertEqual(self.req('PUT', '/api/state', {'revision': 10, 'data': snapshot['data']})[0], 403)
        self.assertEqual(self.req('POST', '/api/production/fa_live/start', {'requestId': 'http-live-start-01'})[0], 200)


if __name__ == '__main__':
    unittest.main()

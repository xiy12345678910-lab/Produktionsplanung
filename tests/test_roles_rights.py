#!/usr/bin/env python3
"""#63/#55: Eigene Rollen (Rollenprofile) schränken Systemrollen je Funktion/Aktion serverseitig ein.

Aufruf:  python tests/test_roles_rights.py
Arbeitet nur in Temp-Ordnern, nie mit Live-Daten.
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
TMP = Path(tempfile.mkdtemp(prefix='mp-roles-'))
os.environ['MP_CONFIG_DIR'] = str(TMP / 'config')
import server  # noqa: E402

server.DATA_DIR = TMP / 'data'
server.DB_PATH = server.DATA_DIR / 'maschinenplanung.sqlite3'
server.PBKDF2_ITERS = 1000
server.init_db(seed='werbetechnik')
server.load_config()
with server.db_session() as con:
    BASE = json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
DEP = BASE['machines'][0]['departmentId']
KONF = next(d['id'] for d in BASE['departments'] if 'konf' in d['id'])
server.now_iso = lambda: '2026-10-07T05:00:00Z'
PASS = 'Rollen-Test-Passwort-1'


class RoleProfiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.ALLOWED_NETWORK = server.ipaddress.ip_network('127.0.0.0/8')
        server.create_or_reset_admin('admin', PASS)
        server.Handler.log_message = lambda *a, **k: None
        cls.httpd = server.MPHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.admin = cls.login('admin')[1]
        s = copy.deepcopy(BASE)
        s['projects'] = [{'id': 'p1', 'number': 'P-1', 'phase': 'accepted', 'name': 'Projekt', 'customer': 'K', 'ab': 'AB-1', 'dueDate': '2026-11-30', 'log': [], 'processes': []}]
        s['frameOrders'] = [{'id': 'fo1', 'number': 'RA-1', 'customer': 'K', 'articleId': 'A', 'departmentId': DEP, 'totalQty': 10, 'status': 'open'}]
        s['workSteps'] = [{'id': 'ws_k', 'sequence': 10, 'planningType': 'MACHINE', 'pos': 10, 'departmentId': KONF, 'projectId': 'p1', 'predecessorIds': [], 'fa': 'FA-K', 'faNumber': 'FA-K', 'order': 'FA-K', 'ab': 'AB-1', 'wt': '', 'machineId': '', 'altMachineId': '', 'allowAlternative': False, 'articleNo': '', 'description': '', 'targetQty': 5, 'dueDate': '2026-11-20', 'baselinePlan': None, 'hours': 2, 'goodQty': 0, 'scrapQty': 0, 'status': 'planned', 'direction': 'forward', 'anchorMode': 'none', 'requiredStart': '', 'requiredFinish': '', 'handoffUnassigned': True, 'sourceType': 'PROJECT', 'sourceId': 'p1'}]
        s['employees'] = [{'id': 'e1', 'name': 'Anna Beispiel', 'departmentId': DEP, 'skills': [], 'homeMachineId': '', 'homeShift': 'auto', 'weeklyHours': 40, 'active': True}]
        s['history'] = [{'id': 'h1', 'originalOrderId': 'old1', 'recordType': 'done', 'fa': 'FA-ALT', 'departmentId': DEP, 'machineId': BASE['machines'][0]['id'], 'goodQty': 1, 'scrapQty': 0, 'finishedAt': '2026-10-01T10:00:00Z'}]
        with server.db_session() as con:
            con.execute('UPDATE state SET json=?,revision=revision+1 WHERE id=1', (json.dumps(s),))

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
        status, body, ck = cls.req('POST', '/api/login', {'username': name, 'password': PASS})
        return status, ck, body

    def user(self, name, role, dep='', profile=''):
        st, body, _ = self.req('POST', '/api/users', {'username': name, 'password': PASS, 'role': role, 'departmentId': dep, **({'profileId': profile} if profile else {})}, self.admin)
        self.assertEqual(st, 201, body)
        return body

    def put_role(self, rid, body, cookie=None):
        return self.req('PUT', '/api/roles/' + rid, body, cookie or self.admin)

    def test_profile_validation_and_management(self):
        self.assertEqual(self.put_role('nur-admin', {'name': 'X', 'baseRole': 'admin'})[0], 400, 'Admin bleibt immer vollständig')
        self.assertEqual(self.put_role('kaputt', {'name': 'Y', 'baseRole': 'viewer', 'rights': {'projects': 'alles'}})[0], 400)
        self.assertEqual(self.put_role('Gross', {'name': 'Z', 'baseRole': 'viewer'})[0], 404, 'ungültige ID erreicht keinen Endpunkt')
        self.assertEqual(self.put_role('lesend-ohne-personal', {'name': 'Lesend ohne Personal', 'baseRole': 'viewer', 'rights': {'personnel': 'none'}})[0], 200)
        self.assertEqual(self.put_role('doppelt', {'name': 'lesend ohne personal', 'baseRole': 'viewer'})[0], 409, 'Name eindeutig')
        st, cat, _ = self.req('GET', '/api/roles', None, self.admin)
        self.assertEqual(st, 200)
        self.assertTrue(any(p['id'] == 'lesend-ohne-personal' for p in cat['profiles']))
        self.assertIn(['frameOrders', 'Rahmenaufträge'], cat['functions'])
        self.user('leser1', 'viewer', '', 'lesend-ohne-personal')
        self.assertEqual(self.put_role('lesend-ohne-personal', {'name': 'Lesend ohne Personal', 'baseRole': 'gf'})[0], 409, 'Systemrolle einer zugewiesenen Rolle bleibt fest')
        self.assertEqual(self.req('DELETE', '/api/roles/lesend-ohne-personal', None, self.admin)[0], 409, 'zugewiesene Rolle nicht löschbar')
        _, ck, body = self.login('leser1')
        self.assertEqual(body['user']['rights']['personnel'], 'none')
        _, state, _ = self.req('GET', '/api/state', None, ck)
        self.assertEqual(state['data']['employees'], [], 'Kein Zugriff blendet Personaldaten aus')
        self.user('leser-voll', 'viewer')
        _, state_full, _ = self.req('GET', '/api/state', None, self.login('leser-voll')[1])
        self.assertEqual([e['id'] for e in state_full['data']['employees']], ['e1'], 'Gegenprobe: Systemrolle sieht Personal')
        # Deaktivieren: bestehende Sitzung endet, Anmeldung wird mit Hinweis abgewiesen
        self.assertEqual(self.put_role('lesend-ohne-personal', {'name': 'Lesend ohne Personal', 'baseRole': 'viewer', 'active': False, 'rights': {'personnel': 'none'}})[0], 200)
        self.assertEqual(self.req('GET', '/api/state', None, ck)[0], 401)
        st, _, body = self.login('leser1')
        self.assertEqual((st, body.get('errorCode')), (403, 'MP-ROLE-002'))
        # Benutzer zurück auf Systemrolle, danach Rolle löschbar
        uid = next(u['id'] for u in self.req('GET', '/api/users', None, self.admin)[1]['users'] if u['username'] == 'leser1')
        self.assertEqual(self.req('PATCH', f'/api/users/{uid}', {'profileId': ''}, self.admin)[0], 200)
        self.assertEqual(self.req('DELETE', '/api/roles/lesend-ohne-personal', None, self.admin)[0], 200)
        self.assertEqual(self.login('leser1')[0], 200)

    def test_av_profile_is_enforced_on_server(self):
        self.assertEqual(self.put_role('av-eingeschraenkt', {'name': 'AV eingeschränkt', 'baseRole': 'production_planning', 'rights': {'projects': 'read', 'frameOrders': 'none'}, 'actions': {'confectionHours': False}})[0], 200)
        self.assertEqual(self.req('POST', '/api/users', {'username': 'av-falsch', 'password': PASS, 'role': 'viewer', 'profileId': 'av-eingeschraenkt'}, self.admin)[0], 400, 'Profil passt nicht zur Systemrolle')
        self.user('av-r', 'production_planning', '', 'av-eingeschraenkt')
        _, ck, body = self.login('av-r')
        self.assertEqual((body['user']['profileName'], body['user']['actions']['confectionHours']), ('AV eingeschränkt', False))
        _, state, _ = self.req('GET', '/api/state', None, ck)
        self.assertEqual(state['data']['frameOrders'], [], 'Rahmenaufträge ausgeblendet')
        # Unverändertes Speichern bleibt möglich und verliert keine ausgeblendeten Daten
        st, r, _ = self.req('PUT', '/api/state', state, ck)
        self.assertEqual(st, 200, r)
        with server.db_session() as con:
            saved = json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
        self.assertEqual([f['id'] for f in saved['frameOrders']], ['fo1'], 'ausgeblendete Rahmenaufträge bleiben erhalten')
        changed = copy.deepcopy(state); changed['revision'] = r['revision']; changed['data']['projects'][0]['name'] = 'Geändert'
        st, body, _ = self.req('PUT', '/api/state', changed, ck)
        self.assertEqual((st, body.get('errorCode')), (403, 'MP-ROLE-010'), 'Leserecht auf Projekte')
        hours = copy.deepcopy(state); hours['revision'] = r['revision']; hours['data']['workSteps'][0]['hours'] = 4
        st, body, _ = self.req('PUT', '/api/state', hours, ck)
        self.assertEqual((st, body.get('errorCode')), (403, 'MP-ROLE-010'), 'Konfektionsstunden gesperrt')
        st, body, _ = self.req('POST', '/api/demand/frame-order-save', {'requestId': 'role-demand-001', 'number': 'RA-9', 'customer': 'K', 'articleId': 'A', 'departmentId': DEP, 'totalQty': 1}, ck)
        self.assertEqual((st, body.get('errorCode')), (403, 'MP-ROLE-001'), 'Bedarfsaktion ohne Recht')
        # Ohne Profil darf dieselbe Systemrolle alles wie bisher
        self.user('av-voll', 'production_planning')
        _, ck_full, _ = self.login('av-voll')
        self.assertEqual(self.req('POST', '/api/demand/frame-order-save', {'requestId': 'role-demand-002', 'number': 'RA-10', 'customer': 'K', 'articleId': 'A', 'departmentId': DEP, 'totalQty': 1}, ck_full)[0], 200)

    def test_actions_and_endpoints(self):
        self.assertEqual(self.put_role('prod-lesend', {'name': 'Produktion nur lesen', 'baseRole': 'production', 'rights': {'chat': 'none', 'history': 'none'}, 'actions': {'production': False}})[0], 200)
        self.user('prod-r', 'production', DEP, 'prod-lesend')
        _, ck, _ = self.login('prod-r')
        st, body, _ = self.req('POST', '/api/production/ws_k/start', {'requestId': 'role-prod-start-1'}, ck)
        self.assertEqual((st, body.get('errorCode')), (403, 'MP-ROLE-001'))
        self.assertEqual(self.req('GET', '/api/chat/channels', None, ck)[0], 403)
        self.assertEqual(self.req('GET', '/api/history-archive', None, ck)[0], 403)
        _, state, _ = self.req('GET', '/api/state', None, ck)
        self.assertEqual(state['data']['history'], [], 'Historie ausgeblendet')
        self.user('prod-voll', 'production', DEP)
        _, full, _ = self.req('GET', '/api/state', None, self.login('prod-voll')[1])
        self.assertEqual([h['id'] for h in full['data']['history']], ['h1'], 'Gegenprobe: Produktion sieht Historie')
        self.assertEqual(self.put_role('leitung-ohne-benutzer', {'name': 'Leitung ohne Benutzerverwaltung', 'baseRole': 'department_lead', 'actions': {'userAdmin': False}})[0], 200)
        self.user('lead-r', 'department_lead', DEP, 'leitung-ohne-benutzer')
        _, ck_lead, _ = self.login('lead-r')
        self.assertEqual(self.req('GET', '/api/users', None, ck_lead)[0], 403)
        self.assertEqual(self.req('POST', '/api/users', {'username': 'x-leser', 'password': PASS, 'role': 'viewer'}, ck_lead)[0], 403)
        self.user('lead-voll', 'department_lead', DEP)
        _, ck_lv, _ = self.login('lead-voll')
        self.assertEqual(self.req('POST', '/api/users', {'username': 'x-leser2', 'password': PASS, 'role': 'viewer', 'profileId': 'prod-lesend'}, ck_lv)[0], 403, 'Profile weist nur der Admin zu')
        self.assertEqual(self.req('GET', '/api/roles', None, ck_lv)[0], 403)
        self.assertEqual(self.put_role('von-leitung', {'name': 'X', 'baseRole': 'viewer'}, ck_lv)[0], 403)
        # Rechteänderung wirkt sofort: Sitzung wird beendet
        self.assertEqual(self.put_role('prod-lesend', {'name': 'Produktion nur lesen', 'baseRole': 'production', 'rights': {'chat': 'edit'}, 'actions': {'production': False}})[0], 200)
        self.assertEqual(self.req('GET', '/api/state', None, ck)[0], 401)


class ProductionOnlyProjects(RoleProfiles):
    '''„Nur Produktion“: Projekt direkt an die Produktion, ohne Entwicklung & Vertrieb.'''
    def test_production_only_project(self):
        self.user('pm1', 'project_management')
        _, ck, _ = self.login('pm1')
        def save(mutate):
            _, state, _ = self.req('GET', '/api/state', None, ck)
            mutate(state['data'])
            return self.req('PUT', '/api/state', state, ck)
        base = {'number': 'P-PO-1', 'customer': 'Kunde', 'name': 'Teil', 'log': [], 'processes': [], 'offer': {}}
        st, r, _ = save(lambda d: d['projects'].append({**base, 'id': 'p_po0', 'phase': 'accepted', 'ab': 'AB-PO-0', 'dueDate': '2026-11-30'}))
        self.assertEqual(st, 403, f'Direkt angenommen nur mit „Nur Produktion“ {r}')
        st, r, _ = save(lambda d: d['projects'].append({**base, 'id': 'p_po1', 'phase': 'accepted', 'productionOnly': True, 'ab': 'AB-PO-1', 'dueDate': ''}))
        self.assertEqual((st, r.get('errorCode')), (400, 'MP-PM-009'), 'AB und Liefertermin bleiben Pflicht')
        st, r, _ = save(lambda d: d['projects'].append({**base, 'id': 'p_po2', 'phase': 'accepted', 'productionOnly': True, 'ab': 'AB-PO-2', 'dueDate': '2026-11-30'}))
        self.assertEqual(st, 200, r)
        st, r, _ = save(lambda d: next(p for p in d['projects'] if p['id'] == 'p_po2').update(productionOnly=False))
        self.assertEqual(st, 403, '„Nur Produktion“ bleibt nach dem Anlegen fest')
        st, r, _ = save(lambda d: d['projects'].append({**base, 'id': 'p_po3', 'phase': 'pm', 'productionOnly': 'ja'}))
        self.assertEqual(st, 400, 'Feld muss ja/nein sein')

    # Basisklassen-Tests nicht doppelt ausführen
    test_profile_validation_and_management = None
    test_av_profile_is_enforced_on_server = None
    test_actions_and_endpoints = None


class ParallelProjects(RoleProfiles):
    '''V12.23.0: Keine Übergabe-Schritte – PM und AV legen an und arbeiten gleichzeitig.'''
    def test_pm_and_av_work_in_parallel(self):
        self.user('pmpar', 'project_management')
        self.user('avpar', 'production_planning')
        _, ck_pm, _ = self.login('pmpar')
        _, ck_av, _ = self.login('avpar')
        def save(ck, mutate):
            _, state, _ = self.req('GET', '/api/state', None, ck)
            mutate(state['data'])
            return self.req('PUT', '/api/state', state, ck)
        proj = lambda d, pid: next(p for p in d['projects'] if p['id'] == pid)
        base = {'customer': 'Kunde', 'name': 'Teil', 'processes': [], 'offer': {}}
        st, r, _ = save(ck_av, lambda d: d['projects'].append({**base, 'id': 'p_av', 'number': 'P-AV-1', 'phase': 'pm', 'log': [{'id': 'l1', 'actor': 'avpar', 'text': 'Projekt angelegt', 'ts': '2026-10-08T08:00:00Z'}]}))
        self.assertEqual(st, 200, f'AV legt Projekt an {r}')
        st, r, _ = save(ck_pm, lambda d: proj(d, 'p_av').update(name='Teil PM'))
        self.assertEqual(st, 200, f'PM bearbeitet das AV-Projekt ohne Übergabe {r}')
        st, r, _ = save(ck_av, lambda d: proj(d, 'p_av').update(phase='closed'))
        self.assertEqual((st, r.get('errorCode')), (400, 'MP-PM-008'), 'Produktion fertig nur mit AB')
        st, r, _ = save(ck_av, lambda d: proj(d, 'p_av').update(phase='closed', ab='AB-AV-1'))
        self.assertEqual(st, 200, f'Direkt von „In Arbeit“ zu „Produktion fertig“ {r}')
        st, r, _ = save(ck_pm, lambda d: d['projects'].append({**base, 'id': 'p_pm', 'number': 'P-PM-1', 'phase': 'pm', 'ab': 'AB-PM-1', 'dueDate': '2026-11-30', 'log': []}))
        self.assertEqual(st, 200, r)
        st, r, _ = save(ck_pm, lambda d: proj(d, 'p_pm').update(phase='accepted'))
        self.assertEqual(st, 200, f'Altphase „accepted“ bleibt für ältere Clients erlaubt {r}')

    test_profile_validation_and_management = None
    test_av_profile_is_enforced_on_server = None
    test_actions_and_endpoints = None


if __name__ == '__main__':
    unittest.main(verbosity=2)

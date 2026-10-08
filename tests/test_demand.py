#!/usr/bin/env python3
"""Block E (#46): Rahmenauftrag → Abruf → Bestand → Reservierung → Fehlmenge → FA → Produktion → Bestand/Lieferung.

Aufruf:  python tests/test_demand.py
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix='mp-demand-'))
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
OTHER = next(d['id'] for d in BASE['departments'] if d['id'] != DEP and d.get('kind', 'production') == 'production')
# Wie tests/test_blocks.py: Mittwoch 07:00 Ortszeit, innerhalb der Schicht von M.
server.now_iso = lambda: '2026-10-07T05:00:00Z'
PASS = 'Demand-Test-Passwort-1'


class DemandFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.ALLOWED_NETWORK = server.ipaddress.ip_network('127.0.0.0/8')
        server.create_or_reset_admin('admin', PASS)
        server.Handler.log_message = lambda *a, **k: None
        cls.httpd = server.MPHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.admin = cls.login('admin')
        for name, role, dep in [('av', 'production_planning', ''), ('lead', 'department_lead', DEP), ('lead2', 'department_lead', OTHER), ('prod', 'production', DEP), ('view', 'viewer', '')]:
            status, _, _ = cls.req('POST', '/api/users', {'username': name, 'password': PASS, 'role': role, 'departmentId': dep}, cls.admin)
            assert status == 201, (name, status)
        cls.av, cls.lead, cls.lead2, cls.prod, cls.view = (cls.login(n) for n in ('av', 'lead', 'lead2', 'prod', 'view'))

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close()

    @classmethod
    def login(cls, name):
        _, _, ck = cls.req('POST', '/api/login', {'username': name, 'password': PASS}, '')
        return ck

    @classmethod
    def req(cls, method, path, body=None, cookie=''):
        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=15)
        headers = {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION, 'Cookie': cookie}
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        r = conn.getresponse(); status = r.status; ck = (r.getheader('Set-Cookie') or '').split(';')[0]
        payload = json.loads(r.read() or '{}'); conn.close()
        return status, payload, ck

    n = 0

    def act(self, action, body, cookie=None, rid=None):
        DemandFlow.n += 1
        return self.req('POST', '/api/demand/' + action, {'requestId': rid or f'demand-req-{DemandFlow.n:05d}', **body}, cookie or self.av)

    def prod_act(self, oid, action, body=None, rid=None):
        DemandFlow.n += 1
        return self.req('POST', f'/api/production/{oid}/{action}', {'requestId': rid or f'prod-req-{DemandFlow.n:05d}', **(body or {})}, self.lead)

    def db(self):
        with server.db_session() as con:
            return json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0])

    def put_db(self, state):
        with server.db_session() as con:
            con.execute('UPDATE state SET json=?,revision=revision+1 WHERE id=1', (json.dumps(state),))

    def inv(self, state=None):
        return next(x for x in (state or self.db())['inventory'] if x['articleId'] == 'ART-9' and x['departmentId'] == DEP)

    def plan_and_release(self, oid):
        """Abteilung plant (Ressource/Laufzeit) und gibt frei – hier direkt im Datenstand wie in tests/test_blocks.py."""
        s = self.db()
        step = next(x for x in s['workSteps'] if x['id'] == oid)
        seg = {'start': '2026-10-07T07:00', 'end': '2026-10-07T09:00', 'shift': 'single'}
        step.update(handoffUnassigned=False, machineId=M['id'], hours=2, status='released',
                    baselinePlan={'machineId': M['id'], 'crew': 1, 'setupMinutes': 0, 'start': seg['start'], 'end': seg['end'], 'segments': [seg]})
        self.put_db(s)

    def test_frame_order_flow(self):
        # 1. Rahmenauftrag
        st, r, _ = self.act('frame-order-save', {'number': 'RA-100', 'customer': 'Kunde A', 'articleId': 'ART-9', 'description': 'Schild 9', 'departmentId': DEP, 'totalQty': 10000})
        self.assertEqual(st, 200, r); fo = r['event']['frameOrderId']
        self.assertEqual(self.act('frame-order-save', {'number': 'RA-100', 'customer': 'X', 'articleId': 'A', 'departmentId': DEP, 'totalQty': 1})[0], 409, 'Rahmennummer eindeutig')
        # 2. Abruf, Rahmenrest begrenzt
        st, r, _ = self.act('call-off-create', {'frameOrderId': fo, 'qty': 5000, 'dueDate': '2026-10-30'}); self.assertEqual(st, 200, r); c1 = r['event']['callOffId']
        self.assertEqual(self.act('call-off-create', {'frameOrderId': fo, 'qty': 6000, 'dueDate': '2026-11-30'})[0], 409, 'Abruf > Rahmenrest')
        # 3. Bestand 2.000, reservieren
        self.assertEqual(self.act('stock-save', {'articleId': 'ART-9', 'departmentId': DEP, 'physicalQty': 2000})[0], 200)
        st, r, _ = self.act('reserve', {'callOffId': c1}); self.assertEqual(r['event']['reservedQty'], 2000)
        self.assertEqual((self.inv()['physicalQty'], self.inv()['reservedQty']), (2000, 2000))
        # 4. Fehlmenge → FA 3.000, idempotent
        st, r, _ = self.act('fa-create', {'callOffId': c1, 'fa': 'FA-E1'}, rid='fa-create-fixed-01'); self.assertEqual(st, 200, r)
        self.assertEqual(r['event']['qty'], 3000); oid = r['event']['orderId']
        st, r2, _ = self.act('fa-create', {'callOffId': c1, 'fa': 'FA-E1'}, rid='fa-create-fixed-01')
        self.assertTrue(r2['replayed']); self.assertEqual(sum(1 for x in self.db()['workSteps'] if x.get('callOffId') == c1), 1, 'Retry erzeugt keinen zweiten FA')
        self.assertEqual(self.act('fa-create', {'callOffId': c1, 'fa': 'FA-E1b'})[0], 409, 'Kein Bedarf mehr → kein zweiter FA')
        step = next(x for x in self.db()['workSteps'] if x['id'] == oid)
        self.assertEqual((step['sourceType'], step['sourceId'], step['frameOrderId'], step['departmentId'], step['handoffUnassigned']), ('FRAME_ORDER', c1, fo, DEP, True))
        # 5. Zweiter Abruf konkurriert um Bestand: nichts mehr frei
        st, r, _ = self.act('call-off-create', {'frameOrderId': fo, 'qty': 3000, 'dueDate': '2026-11-15'}); c2 = r['event']['callOffId']
        self.assertEqual(self.act('reserve', {'callOffId': c2})[1]['event']['reservedQty'], 0, 'keine Doppelreservierung')
        st, r, _ = self.act('fa-create', {'callOffId': c2, 'fa': 'FA-E2'}); self.assertEqual(r['event']['qty'], 3000); oid2 = r['event']['orderId']
        # 6. Direkter State-PUT darf Bedarfs-FA nicht fälschen
        _, body, _ = self.req('GET', '/api/state', None, self.lead)
        mine = next(x for x in body['data']['workSteps'] if x['id'] == oid2)
        for change in ({'targetQty': 9999}, {'callOffId': 'x'}, {'articleId': 'B'}):
            b = copy.deepcopy(body); next(x for x in b['data']['workSteps'] if x['id'] == oid2).update(change)
            self.assertEqual(self.req('PUT', '/api/state', b, self.lead)[0], 403, change)
        b = copy.deepcopy(body); b['data']['workSteps'] = [x for x in b['data']['workSteps'] if x['id'] != oid2]
        self.assertEqual(self.req('PUT', '/api/state', b, self.lead)[0], 403, 'Löschen nur per Abrufstorno')
        b = copy.deepcopy(body); b['data']['workSteps'].append({**mine, 'id': 'ws_forged', 'pos': 99999, 'sequence': 99999, 'fa': 'X', 'faNumber': 'X', 'order': 'X'})
        self.assertEqual(self.req('PUT', '/api/state', b, self.lead)[0], 403, 'Neue Bedarfs-FA nur serverseitig')
        b = copy.deepcopy(body); b['data']['callOffs'][0]['qty'] = 1
        self.assertEqual(self.req('PUT', '/api/state', b, self.lead)[0], 403, 'Abrufe nur über /api/demand')
        # 7. Abteilung plant, Produktion: Start, Pause, Fortsetzen, Teil, Fertig
        self.plan_and_release(oid)
        for action, extra in [('start', {}), ('pause', {}), ('resume', {}), ('partial', {'goodQty': 1000, 'scrapQty': 10})]:
            st, r, _ = self.prod_act(oid, action, extra); self.assertEqual(st, 200, (action, r))
        self.assertEqual(self.inv()['physicalQty'], 2000, 'Teilmeldung bucht noch nicht in den Bestand')
        st, r, _ = self.prod_act(oid, 'finish', {'goodQty': 1990}, rid='finish-fixed-001'); self.assertEqual(st, 200, r)
        self.assertEqual(self.prod_act(oid, 'finish', {'goodQty': 1990}, rid='finish-fixed-001')[1]['replayed'], True)
        s = self.db(); inv = self.inv(s); c = next(x for x in s['callOffs'] if x['id'] == c1)
        self.assertEqual((inv['physicalQty'], inv['reservedQty'], c['reservedQty']), (4990, 4990, 4990), 'Gutmenge einmal gebucht und für den Abruf reserviert')
        hist = next(h for h in s['history'] if h['originalOrderId'] == oid)
        self.assertEqual((hist['goodQty'], hist['scrapQty'], hist['callOffId'], hist['sourceType'], hist.get('reservedForCallOff')), (2990, 10, c1, 'FRAME_ORDER', 2990))
        # Ausschuss: Restbedarf 10 bleibt offen und erzeugt einen Nach-FA
        st, r, _ = self.act('fa-create', {'callOffId': c1, 'fa': 'FA-E1-N'}); self.assertEqual(r['event']['qty'], 10)
        # 8. Lieferung, Rahmenrest
        self.assertEqual(self.act('deliver', {'callOffId': c1, 'qty': 6000})[0], 409, 'nur reservierte Menge lieferbar')
        st, r, _ = self.act('deliver', {'callOffId': c1}); self.assertEqual(r['event']['qty'], 4990)
        s = self.db(); inv = self.inv(s); c = next(x for x in s['callOffs'] if x['id'] == c1)
        self.assertEqual((inv['physicalQty'], inv['reservedQty'], c['deliveredQty'], c['status']), (0, 0, 4990, 'open'))
        called = sum(x['qty'] for x in s['callOffs'] if x['frameOrderId'] == fo)
        self.assertEqual(10000 - called, 2000, 'Rahmenrest korrekt')
        # 9. Abruf reduzieren: geplanter FA wird gekürzt; stornieren: FA entfällt
        st, r, _ = self.act('call-off-update', {'callOffId': c2, 'qty': 1000}); self.assertEqual(st, 200, r)
        self.assertEqual(next(x for x in self.db()['workSteps'] if x['id'] == oid2)['targetQty'], 1000)
        self.assertEqual(self.act('call-off-update', {'callOffId': c2, 'qty': 99999})[0], 409, 'Erhöhung über Rahmenrest')
        st, r, _ = self.act('call-off-cancel', {'callOffId': c2}); self.assertEqual(st, 200, r)
        s = self.db()
        self.assertFalse(any(x['id'] == oid2 for x in s['workSteps']), 'geplanter FA entfällt beim Storno')
        self.assertEqual(next(x for x in s['callOffs'] if x['id'] == c2)['status'], 'cancelled')
        self.assertEqual(self.act('call-off-cancel', {'callOffId': c2})[0], 409)
        # 10. Gestarteter FA bleibt bei Reduzierung, Mehrmenge geht in freien Bestand
        nfa = next(x['id'] for x in s['workSteps'] if x.get('fa') == 'FA-E1-N')
        self.plan_and_release(nfa); self.assertEqual(self.prod_act(nfa, 'start')[0], 200)
        self.assertEqual(self.act('call-off-update', {'callOffId': c1, 'qty': 4990})[0], 200)
        self.assertTrue(any(x['id'] == nfa for x in self.db()['workSteps']), 'laufender FA wird nicht still entfernt')
        st, r, _ = self.prod_act(nfa, 'abort', {'goodQty': 10, 'reason': 'Abruf reduziert'}); self.assertEqual(st, 200, r)
        s = self.db(); inv = self.inv(s); c = next(x for x in s['callOffs'] if x['id'] == c1)
        self.assertEqual((inv['physicalQty'], inv['reservedQty'], c['status']), (10, 0, 'delivered'), 'Abbruch bucht Gutteile in den freien Bestand')

    def test_stock_requirement_and_project_fa_side_by_side(self):
        self.assertEqual(self.act('stock-save', {'articleId': 'ART-S', 'departmentId': DEP, 'physicalQty': 6000, 'targetQty': 10000, 'description': 'Lagerartikel'})[0], 200)
        inv = next(x for x in self.db()['inventory'] if x['articleId'] == 'ART-S')
        st, r, _ = self.act('fa-create', {'inventoryId': inv['id'], 'fa': 'FA-S1'}); self.assertEqual((st, r['event']['qty']), (200, 4000))
        self.assertEqual(self.act('fa-create', {'inventoryId': inv['id'], 'fa': 'FA-S2'})[0], 409, 'Bestandsbedarf nicht doppelt')
        step = next(x for x in self.db()['workSteps'] if x.get('fa') == 'FA-S1')
        self.assertEqual((step['sourceType'], step['stockRequirementId']), ('STOCK_REQUIREMENT', inv['id']))
        self.assertEqual(self.act('stock-save', {'inventoryId': inv['id'], 'physicalQty': 10})[0], 200)
        self.assertEqual(self.act('stock-save', {'articleId': 'ART-S', 'departmentId': 'gibt-es-nicht', 'physicalQty': 1})[0], 400)

    def test_concurrent_reservations_never_exceed_stock(self):
        st, r, _ = self.act('frame-order-save', {'number': 'RA-RACE', 'customer': 'K', 'articleId': 'ART-R', 'departmentId': DEP, 'totalQty': 100})
        fo = r['event']['frameOrderId']
        calls = [self.act('call-off-create', {'frameOrderId': fo, 'qty': 40, 'dueDate': '2026-10-30'})[1]['event']['callOffId'] for _ in range(2)]
        self.act('stock-save', {'articleId': 'ART-R', 'departmentId': DEP, 'physicalQty': 50})
        with ThreadPoolExecutor(max_workers=2) as ex:
            results = list(ex.map(lambda c: self.act('reserve', {'callOffId': c}), calls))
        self.assertEqual(sorted(x[1]['event']['reservedQty'] for x in results), [10, 40])
        inv = next(x for x in self.db()['inventory'] if x['articleId'] == 'ART-R')
        self.assertEqual(inv['reservedQty'], 50)

    def test_legacy_history_does_not_block_demand_actions(self):
        # Altbestand: Historieneintrag ohne kanonische FA-/Quellfelder (vor V12.19) darf Bedarfsaktionen nicht sperren.
        s = self.db()
        s['history'].append({'id': 'h_legacy', 'originalOrderId': 'old_legacy', 'recordType': 'done', 'order': 'ALT-1', 'machineId': M['id'], 'goodQty': 1, 'scrapQty': 0, 'finishedAt': '2026-09-01T10:00:00Z'})
        self.put_db(s)
        st, r, _ = self.act('frame-order-save', {'number': 'RA-LEGACY', 'customer': 'K', 'articleId': 'ART-L', 'departmentId': DEP, 'totalQty': 5})
        self.assertEqual(st, 200, r)

    def test_roles_and_scope(self):
        st, r, _ = self.act('frame-order-save', {'number': 'RA-SCOPE', 'customer': 'K', 'articleId': 'ART-X', 'departmentId': DEP, 'totalQty': 10})
        for ck in (self.lead, self.prod, self.view):
            self.assertEqual(self.act('call-off-create', {'frameOrderId': r['event']['frameOrderId'], 'qty': 1, 'dueDate': '2026-10-30'}, ck)[0], 403)
        self.assertEqual(self.act('frame-order-save', {'number': 'RA-ADM', 'customer': 'K', 'articleId': 'A', 'departmentId': DEP, 'totalQty': 1}, self.admin)[0], 200)
        _, own, _ = self.req('GET', '/api/state', None, self.lead)
        _, foreign, _ = self.req('GET', '/api/state', None, self.lead2)
        self.assertTrue(any(f['number'] == 'RA-SCOPE' for f in own['data']['frameOrders']))
        self.assertFalse(any(f['departmentId'] == DEP for f in foreign['data']['frameOrders']), 'fremder Bereich sieht keine Rahmenaufträge')
        self.assertFalse(any(x['departmentId'] == DEP for x in foreign['data']['inventory']))
        self.assertEqual(self.req('POST', '/api/demand/unbekannt', {'requestId': 'unknown-action-1'}, self.av)[0], 404)
        self.assertEqual(self.act('frame-order-save', {'number': 'RA-BAD', 'customer': 'K', 'articleId': 'A', 'departmentId': DEP, 'totalQty': -5})[0], 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)

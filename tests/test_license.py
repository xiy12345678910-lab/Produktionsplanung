#!/usr/bin/env python3
"""#52 Lizenzschlüssel: Signaturprüfung, inaktiv ohne öffentlichen Schlüssel, 30 Tage Kulanz, Uhr-Rückstellung,
serverseitige Schreibsperre (nur Lesen) und Upload-Prüfung. Arbeitet nur in Temp-Ordnern; das Test-Schlüsselpaar
entsteht zur Laufzeit und wird nur über das Modulattribut mp_license.PUBLIC_KEY eingesetzt (nie per Umgebung)."""
import base64
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix='mp-lic-'))
os.environ['MP_CONFIG_DIR'] = str(TMP / 'config')
import server  # noqa: E402
import mp_license  # noqa: E402

server.DATA_DIR = TMP / 'data'
server.DB_PATH = server.DATA_DIR / 'maschinenplanung.sqlite3'
server.PBKDF2_ITERS = 1000
server.init_db(seed='werbetechnik')
server.load_config()
PASS = 'Lizenz-Test-Passwort-1'
TENANT = server.current_config()['tenantId']
KEY = server.rsa_generate(2048)
OTHER = server.rsa_generate(2048)
PUB = {'n': f"{KEY['n']:x}", 'e': KEY['e']}
DAY = 86400
NOW = [time.time()]
mp_license.clock = lambda: NOW[0]


def lic(tenant=TENANT, expires=None, key=KEY, licensee='Test GmbH', issued='2026-01-01'):
    return mp_license.make_license({'id': 'L-TEST-1', 'licensee': licensee, 'tenantId': tenant, 'issued': issued, 'expires': expires}, key)


def reset_meta():
    with server.db_session() as con:
        con.execute('DELETE FROM app_meta')


class Crypto(unittest.TestCase):
    k = (KEY['n'], KEY['e'])
    today = date(2026, 10, 9)

    def test_valid_perpetual(self):
        r = mp_license.check(lic(), TENANT, self.today, self.k)
        self.assertEqual(r['status'], 'valid')
        self.assertIsNone(r['expires'])
        self.assertNotIn('expiresInDays', r)

    def test_valid_with_expiry(self):
        r = mp_license.check(lic(expires='2026-10-09'), TENANT, self.today, self.k)   # Ablauftag gilt noch
        self.assertEqual((r['status'], r['expiresInDays']), ('valid', 0))

    def test_expired(self):
        self.assertEqual(mp_license.check(lic(expires='2026-10-08'), TENANT, self.today, self.k)['status'], 'expired')

    def test_tampered_payload(self):
        obj = json.loads(lic())
        obj['payload']['licensee'] = 'Andere GmbH'
        self.assertEqual(mp_license.check(json.dumps(obj).encode(), TENANT, self.today, self.k)['status'], 'invalid')
        obj = json.loads(lic(expires='2026-12-31'))
        obj['payload']['expires'] = None
        self.assertEqual(mp_license.check(json.dumps(obj).encode(), TENANT, self.today, self.k)['status'], 'invalid')

    def test_tampered_signature(self):
        obj = json.loads(lic())
        sig = bytearray(base64.b64decode(obj['signature']))
        sig[-1] ^= 1
        obj['signature'] = base64.b64encode(bytes(sig)).decode()
        self.assertEqual(mp_license.check(json.dumps(obj).encode(), TENANT, self.today, self.k)['status'], 'invalid')
        obj['signature'] = base64.b64encode(bytes(sig[:-1])).decode()          # falsche Länge
        self.assertEqual(mp_license.check(json.dumps(obj).encode(), TENANT, self.today, self.k)['status'], 'invalid')
        obj['signature'] = base64.b64encode(KEY['n'].to_bytes(256, 'big')).decode()   # s >= n
        self.assertEqual(mp_license.check(json.dumps(obj).encode(), TENANT, self.today, self.k)['status'], 'invalid')
        obj['signature'] = 'kein base64!'
        self.assertEqual(mp_license.check(json.dumps(obj).encode(), TENANT, self.today, self.k)['status'], 'invalid')

    def test_wrong_key(self):
        self.assertEqual(mp_license.check(lic(key=OTHER), TENANT, self.today, self.k)['status'], 'invalid')

    def test_wrong_tenant(self):
        r = mp_license.check(lic(tenant='andere-firma'), TENANT, self.today, self.k)
        self.assertEqual(r['status'], 'wrong_tenant')
        self.assertIn('andere-firma', r['error'])

    def test_missing_and_inactive(self):
        self.assertEqual(mp_license.check(None, TENANT, self.today, self.k)['status'], 'missing')
        self.assertEqual(mp_license.check(lic(), TENANT, self.today, None)['status'], 'inactive')

    def test_canonical_and_small_key(self):
        self.assertEqual(mp_license.canonical({'b': 'ä', 'a': 1}), '{"a":1,"b":"ä"}'.encode('utf-8'))
        small = server.rsa_generate(1024)
        data = b'x'
        self.assertFalse(mp_license.verify(data, mp_license.sign(data, small), small['n'], small['e']))
        self.assertTrue(mp_license.verify(data, mp_license.sign(data, KEY), KEY['n'], KEY['e']))

    def test_shipped_flat_without_tool(self):
        import re
        common = (ROOT / 'MP_Common.ps1').read_text(encoding='utf-8-sig')
        app = re.findall(r"'([^']+)'", re.search(r'\$MP_AppFiles\s*=\s*@\((.*?)\n\)', common, re.S).group(1))
        self.assertIn('mp_license.py', app, 'mp_license.py wird ausgeliefert (flach, kein Unterordner)')
        self.assertFalse(any(n.startswith('tools') or n.endswith(('.pem', '.key')) for n in app), 'Werkzeug/Schlüssel nie im Paket')
        import app_updates
        self.assertNotIn('mp_license.py', app_updates.validate_manifest.__code__.co_consts, 'nicht Pflichtdatei im Manifest')

    def test_payload_validation(self):
        for bad in (b'', b'{', b'[]', json.dumps({'payload': {}, 'signature': ''}).encode(), b'x' * (mp_license.MAX_FILE + 1)):
            with self.assertRaises(mp_license.LicenseError):
                mp_license.parse(bad)
        with self.assertRaises(mp_license.LicenseError):
            lic(expires='2025-01-01')   # vor dem Ausstellungsdatum

    def test_repo_ships_inactive_or_strong_key(self):
        src = (ROOT / 'mp_license.py').read_text(encoding='utf-8')
        self.assertNotIn('PRIVATE KEY', src)
        line = next(l for l in src.splitlines() if l.startswith('PUBLIC_KEY = '))
        n = eval(line.split('=', 1)[1])['n']   # noqa: S307 (eigene Quelldatei)
        self.assertTrue(n == '' or int(n, 16).bit_length() >= 3072, 'öffentlicher Schlüssel leer oder >= 3072 Bit')


class Grace(unittest.TestCase):
    def setUp(self):
        mp_license.PUBLIC_KEY = dict(PUB)
        NOW[0] = time.time()
        reset_meta()

    def tearDown(self):
        mp_license.PUBLIC_KEY = {'n': '', 'e': 65537}

    def ev(self, raw=None):
        with server.db_session() as con:
            return mp_license.evaluate(con, raw, TENANT)

    def meta(self):
        with server.db_session() as con:
            return {r[0]: r[1] for r in con.execute('SELECT key,value FROM app_meta')}

    def test_inactive_touches_nothing(self):
        mp_license.PUBLIC_KEY = {'n': '', 'e': 65537}
        r = self.ev()
        self.assertEqual((r['status'], r['readOnly'], r['statusText']), ('inactive', False, 'nicht aktiv'))
        self.assertEqual(self.meta(), {})

    def test_grace_persisted_and_read_only_after_30_days(self):
        t0 = NOW[0]
        r = self.ev()
        self.assertEqual((r['status'], r['readOnly'], r['graceDaysLeft']), ('missing', False, 30))
        start = self.meta()[mp_license.META_GRACE]
        NOW[0] = t0 + 10 * DAY
        r = self.ev()
        self.assertEqual((r['graceDaysLeft'], self.meta()[mp_license.META_GRACE]), (20, start))
        NOW[0] = t0 + 30 * DAY - 60
        self.assertFalse(self.ev()['readOnly'])
        NOW[0] = t0 + 30 * DAY
        r = self.ev()
        self.assertEqual((r['readOnly'], r['graceDaysLeft']), (True, 0))

    def test_clock_rollback(self):
        t0 = NOW[0]
        self.ev()
        NOW[0] = t0 + 31 * DAY
        self.assertTrue(self.ev()['readOnly'])
        NOW[0] = t0 + DAY      # Uhr zurückgestellt
        self.assertTrue(self.ev()['readOnly'])
        self.assertEqual(int(float(self.meta()[mp_license.META_SEEN])), int(t0 + 31 * DAY))
        # auch ein Ablaufdatum lässt sich nicht durch Zurückstellen umgehen
        exp = date.fromtimestamp(t0 + 10 * DAY).isoformat()
        self.assertEqual(self.ev(lic(expires=exp))['status'], 'expired')

    def test_valid_license_ends_grace_and_expiry_restarts_it(self):
        t0 = NOW[0]
        self.ev()
        NOW[0] = t0 + 31 * DAY
        self.assertTrue(self.ev()['readOnly'])
        exp = date.fromtimestamp(t0 + 40 * DAY).isoformat()
        r = self.ev(lic(expires=exp))
        self.assertEqual((r['status'], r['readOnly']), ('valid', False))
        self.assertNotIn(mp_license.META_GRACE, self.meta())
        NOW[0] = t0 + 42 * DAY
        r = self.ev(lic(expires=exp))
        self.assertEqual((r['status'], r['readOnly'], r['graceDaysLeft']), ('expired', False, 30))

    def test_invalid_and_wrong_tenant_count_as_missing(self):
        for raw in (lic(key=OTHER), lic(tenant='andere-firma'), b'kaputt'):
            reset_meta()
            NOW[0] = time.time()
            self.assertFalse(self.ev(raw)['readOnly'])
            NOW[0] += 30 * DAY
            self.assertTrue(self.ev(raw)['readOnly'])


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.ALLOWED_NETWORK = server.ipaddress.ip_network('127.0.0.0/8')
        server.create_or_reset_admin('admin', PASS)
        server.Handler.log_message = lambda *a, **k: None
        cls.httpd = server.MPHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.admin = cls.login('admin')
        st, raw, _ = cls.req('POST', '/api/users', {'username': 'leser', 'password': PASS, 'role': 'viewer'}, cls.admin)
        assert st == 201, raw
        cls.viewer = cls.login('leser')

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close()
        mp_license.PUBLIC_KEY = {'n': '', 'e': 65537}

    def setUp(self):
        mp_license.PUBLIC_KEY = dict(PUB)
        reset_meta()
        server.license_path().unlink(missing_ok=True)
        NOW[0] = time.time()
        server.license_status()          # Kulanz beginnt jetzt
        NOW[0] += 31 * DAY               # ... und ist abgelaufen

    @classmethod
    def req(cls, method, path, body=None, cookie=''):
        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=15)
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION, 'Cookie': cookie})
        r = conn.getresponse(); raw = r.read(); hdr = dict(r.getheaders()); conn.close()
        return r.status, raw, hdr

    @classmethod
    def login(cls, name, pw=PASS):
        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=15)
        conn.request('POST', '/api/login', json.dumps({'username': name, 'password': pw}), {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION})
        r = conn.getresponse(); r.read(); ck = (r.getheader('Set-Cookie') or '').split(';')[0]; conn.close()
        assert ck, name
        return ck

    def state(self):
        st, raw, _ = self.req('GET', '/api/state', None, self.admin)
        self.assertEqual(st, 200)
        return json.loads(raw)

    def put_state(self):
        s = self.state()
        s['data']['operatorCapacity']['single'] = int(s['data']['operatorCapacity']['single']) % 5 + 1
        return self.req('PUT', '/api/state', {'revision': s['revision'], 'data': s['data'], 'action': 'Test'}, self.admin)

    def assertBlocked(self, res, label):
        st, raw, _ = res
        self.assertEqual(st, 403, f'{label}: {raw[:200]}')
        body = json.loads(raw)
        self.assertEqual(body['errorCode'], 'MP-LIC-001', label)
        self.assertIn('nur Lesen', body['error'])
        self.assertTrue(body['license']['readOnly'])

    def test_writes_blocked_when_read_only(self):
        self.assertBlocked(self.put_state(), 'PUT /api/state')
        self.assertBlocked(self.req('POST', '/api/production/ws1/start', {'requestId': 'lic-test-0001'}, self.admin), 'Produktionsaktion')
        self.assertBlocked(self.req('POST', '/api/demand/stock-save', {'requestId': 'lic-test-0002'}, self.admin), 'Bedarfsaktion')
        self.assertBlocked(self.req('POST', '/api/users', {'username': 'neu1', 'password': PASS, 'role': 'viewer'}, self.admin), 'Benutzer anlegen')
        self.assertBlocked(self.req('PATCH', '/api/users/2', {'active': False}, self.admin), 'Benutzer ändern')
        self.assertBlocked(self.req('DELETE', '/api/users/2', None, self.admin), 'Benutzer löschen')
        self.assertBlocked(self.req('PUT', '/api/roles/test-rolle', {'name': 'X'}, self.admin), 'Rolle speichern')
        self.assertBlocked(self.req('PATCH', '/api/config', {'company': {'name': 'X'}}, self.admin), 'Firmenprofil')
        self.assertBlocked(self.req('POST', '/api/config/logo', {'data': ''}, self.admin), 'Logo')
        self.assertBlocked(self.req('POST', '/api/templates/apply', {}, self.admin), 'Vorlage anwenden')
        self.assertBlocked(self.req('POST', '/api/chat/channels/1/messages', {'text': 'hallo'}, self.viewer), 'Chat')
        self.assertBlocked(self.req('PUT', '/api/notifications/prefs', {}, self.viewer), 'Benachrichtigungen')
        self.assertBlocked(self.req('POST', '/api/state', {}, ''), 'auch ohne Anmeldung keine Schreibanfrage')
        with server.db_session() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM users WHERE username='neu1'").fetchone())
            self.assertEqual(con.execute("SELECT active FROM users WHERE username='leser'").fetchone()[0], 1)

    def test_reads_login_password_diagnostics_still_work(self):
        for path in ('/api/state', '/api/session', '/api/config', '/api/users', '/api/diagnostics', '/api/diagnostics?download=1', '/api/revision?since=-1', '/api/license'):
            self.assertEqual(self.req('GET', path, None, self.admin)[0], 200, path)
        st, raw, _ = self.req('GET', '/api/session', None, self.viewer)
        self.assertEqual(json.loads(raw)['license'], {'active': True, 'status': 'missing', 'statusText': 'fehlt', 'readOnly': True})
        st, raw, _ = self.req('GET', '/api/license', None, self.viewer)
        self.assertNotIn('graceDaysLeft', json.loads(raw))
        d = json.loads(self.req('GET', '/api/diagnostics', None, self.admin)[1])
        self.assertTrue(d['license']['readOnly'])
        self.assertTrue(any(w.startswith('Lizenz fehlt') for w in d['warnings']))
        self.login('leser')
        st, raw, _ = self.req('POST', '/api/password', {'currentPassword': PASS, 'newPassword': 'Neues-Passwort-22'}, self.viewer)
        self.assertEqual(st, 200, raw)
        self.assertEqual(self.req('POST', '/api/password', {'currentPassword': 'Neues-Passwort-22', 'newPassword': PASS}, self.viewer)[0], 200)
        self.assertEqual(self.req('POST', '/api/logout', {}, self.login('leser'))[0], 200)
        st, raw, _ = self.req('POST', '/api/updates/install', {}, self.admin)
        self.assertNotEqual(json.loads(raw).get('errorCode'), 'MP-LIC-001', 'Update bleibt möglich')

    def test_grace_period_allows_writes(self):
        NOW[0] -= 2 * DAY      # 29 Tage vergangen, 1 Tag Kulanz übrig
        st, raw, _ = self.put_state()
        self.assertEqual(st, 200, raw)
        self.assertEqual(json.loads(self.req('GET', '/api/session', None, self.admin)[1])['license']['graceDaysLeft'], 1)

    def test_inactive_never_blocks(self):
        mp_license.PUBLIC_KEY = {'n': '', 'e': 65537}
        st, raw, _ = self.put_state()
        self.assertEqual(st, 200, raw)
        lic_ = json.loads(self.req('GET', '/api/license', None, self.admin)[1])
        self.assertEqual((lic_['status'], lic_['readOnly'], lic_['active']), ('inactive', False, False))

    def test_upload_validation_and_unlock(self):
        path = server.license_path()
        cases = (('kein json', 'MP-LIC-003'), (lic(key=OTHER).decode(), 'MP-LIC-002'),
                 (lic(tenant='andere-firma').decode(), 'MP-LIC-004'), (lic(expires='2026-01-02').decode(), 'MP-LIC-005'))
        for content, code in cases:
            st, raw, _ = self.req('POST', '/api/license', {'content': content}, self.admin)
            self.assertEqual((st, json.loads(raw)['errorCode']), (400, code), raw)
            self.assertFalse(path.exists(), 'abgelehnte Datei wird nicht geschrieben')
        self.assertEqual(self.req('POST', '/api/license', {'content': lic().decode()}, self.viewer)[0], 403)
        self.assertFalse(path.exists())
        st, raw, _ = self.req('POST', '/api/license', {'content': lic().decode()}, self.admin)
        self.assertEqual(st, 200, raw)
        body = json.loads(raw)['license']
        self.assertEqual((body['status'], body['licensee'], body['readOnly'], body['expires']), ('valid', 'Test GmbH', False, None))
        self.assertNotIn('signature', json.dumps(body))
        self.assertEqual(path.read_bytes(), lic())
        st, raw, _ = self.put_state()
        self.assertEqual(st, 200, raw)
        # Ersetzen behält die alte Datei als .alt; Entfernen legt sie beiseite (nicht löschen).
        self.assertEqual(self.req('POST', '/api/license', {'content': lic(licensee='Neu GmbH').decode()}, self.admin)[0], 200)
        self.assertEqual(path.with_name(path.name + '.alt').read_bytes(), lic())
        st, raw, _ = self.req('DELETE', '/api/license', None, self.admin)
        self.assertEqual(st, 200, raw)
        self.assertFalse(path.exists())
        self.assertTrue(path.with_name(path.name + '.entfernt').exists())
        self.assertEqual(json.loads(raw)['license']['status'], 'missing')
        self.assertEqual(self.req('DELETE', '/api/license', None, self.admin)[0], 404)
        with server.db_session() as con:
            acts = [r[0] for r in con.execute("SELECT action FROM server_audit WHERE action LIKE 'Lizenz%'")]
        self.assertIn('Lizenz installiert', acts)
        self.assertIn('Lizenz entfernt', acts)

    def test_upload_while_inactive_checks_form_and_tenant(self):
        mp_license.PUBLIC_KEY = {'n': '', 'e': 65537}
        self.assertEqual(json.loads(self.req('POST', '/api/license', {'content': lic(tenant='andere-firma').decode()}, self.admin)[1])['errorCode'], 'MP-LIC-004')
        self.assertEqual(self.req('POST', '/api/license', {'content': lic().decode()}, self.admin)[0], 200)
        server.license_path().unlink()


if __name__ == '__main__':
    unittest.main(verbosity=1)

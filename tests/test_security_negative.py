#!/usr/bin/env python3
"""#52 K1: Negativtests Sicherheit: Sitzung nach Logout/Ablauf/Sperre, Body-Limit, ungueltiges JSON,
Logo-/Lizenz-Upload, fremder Origin, Path Traversal auf statische Dateien.

Aufruf:  python tests/test_security_negative.py
Arbeitet nur in Temp-Ordnern, nie mit Live-Daten.
"""
import base64
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix='mp-secneg-'))
os.environ['MP_CONFIG_DIR'] = str(TMP / 'config')
import server  # noqa: E402

server.DATA_DIR = TMP / 'data'
server.DB_PATH = server.DATA_DIR / 'maschinenplanung.sqlite3'
server.PBKDF2_ITERS = 1000
server.init_db(seed='werbetechnik')
server.load_config()
PASS = 'Sicherheit-Test-Passwort-1'
PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 32


class SecurityNegative(unittest.TestCase):
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
    def req(cls, method, path, body=None, cookie='', headers=None, raw=None):
        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=15)
        h = {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION, 'Cookie': cookie}
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body) if body is not None else None)
        conn.request(method, path, data, h)
        r = conn.getresponse()
        text = r.read()
        conn.close()
        try:
            payload = json.loads(text or b'{}')
        except ValueError:
            payload = {'_raw': text}
        return r.status, payload, text

    @classmethod
    def login(cls, name):
        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=15)
        conn.request('POST', '/api/login', json.dumps({'username': name, 'password': PASS}),
                     {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION})
        r = conn.getresponse(); r.read()
        assert r.status == 200, r.status
        ck = r.getheader('Set-Cookie').split(';')[0]
        conn.close()
        return ck

    def make_user(self, name, role='viewer'):
        st, body, _ = self.req('POST', '/api/users', {'username': name, 'password': PASS, 'role': role, 'departmentId': ''}, self.admin)
        self.assertEqual(st, 201, body)
        return body['id'] if 'id' in body else body['user']['id']

    def user_count(self):
        with server.db_session() as con:
            return con.execute('SELECT count(*) FROM users').fetchone()[0]

    # --- Sitzung -------------------------------------------------------------------------------
    def test_logout_invalidates_session_server_side(self):
        self.make_user('lo1')
        ck = self.login('lo1')
        self.assertEqual(self.req('GET', '/api/session', None, ck)[0], 200)
        self.assertEqual(self.req('POST', '/api/logout', {}, ck)[0], 200)
        st, body, _ = self.req('GET', '/api/session', None, ck)
        self.assertEqual((st, body.get('errorCode')), (401, 'MP-AUTH-001'))
        st, body, _ = self.req('GET', '/api/state', None, ck)
        self.assertEqual(st, 401)
        self.assertNotIn('state', body)
        with server.db_session() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM sessions s JOIN users u ON u.id=s.user_id WHERE u.username=?', ('lo1',)).fetchone()[0], 0)

    def test_expired_session_rejected_without_data(self):
        self.make_user('ex1')
        ck = self.login('ex1')
        self.assertEqual(self.req('GET', '/api/state', None, ck)[0], 200)
        with server.db_session() as con:
            con.execute('UPDATE sessions SET expires_at=? WHERE user_id=(SELECT id FROM users WHERE username=?)', (int(time.time()) - 5, 'ex1'))
        for method, path in (('GET', '/api/session'), ('GET', '/api/state'), ('GET', '/api/users'), ('POST', '/api/chat/messages')):
            st, body, text = self.req(method, path, {} if method == 'POST' else None, ck)
            self.assertEqual((st, body.get('errorCode')), (401, 'MP-AUTH-001'), path)
            self.assertNotIn(b'machines', text)

    def test_unknown_and_forged_cookie_rejected(self):
        for ck in ('mp_session=', 'mp_session=nichtvorhanden', 'mp_session=' + 'a' * 64, 'mp_session=%00', 'garbage'):
            self.assertEqual(self.req('GET', '/api/session', None, ck)[0], 401, ck)

    def test_session_of_deactivated_user_rejected(self):
        uid = self.make_user('lock1')
        ck = self.login('lock1')
        self.assertEqual(self.req('GET', '/api/session', None, ck)[0], 200)
        # Direkt in der DB gesperrt (Sitzungszeile bleibt): session_user() muss active pruefen.
        with server.db_session() as con:
            con.execute('UPDATE users SET active=0 WHERE id=?', (uid,))
        self.assertEqual(self.req('GET', '/api/session', None, ck)[0], 401)
        self.assertEqual(self.req('GET', '/api/state', None, ck)[0], 401)

    def test_deactivation_via_api_kills_sessions_and_login(self):
        uid = self.make_user('lock2')
        ck = self.login('lock2')
        st, body, _ = self.req('PATCH', f'/api/users/{uid}', {'active': False}, self.admin)
        self.assertEqual(st, 200, body)
        self.assertEqual(self.req('GET', '/api/session', None, ck)[0], 401)
        st, body, _ = self.req('POST', '/api/login', {'username': 'lock2', 'password': PASS})
        self.assertEqual((st, body.get('errorCode')), (401, 'MP-AUTH-004'))

    def test_session_of_deleted_user_rejected(self):
        uid = self.make_user('del1')
        ck = self.login('del1')
        self.assertEqual(self.req('GET', '/api/session', None, ck)[0], 200)
        st, body, _ = self.req('DELETE', f'/api/users/{uid}', None, self.admin)
        self.assertEqual(st, 200, body)
        self.assertEqual(self.req('GET', '/api/session', None, ck)[0], 401)
        self.assertEqual(self.req('GET', '/api/state', None, ck)[0], 401)

    def test_role_change_invalidates_sessions(self):
        uid = self.make_user('rc1')
        ck = self.login('rc1')
        st, body, _ = self.req('PATCH', f'/api/users/{uid}', {'role': 'sales', 'departmentId': ''}, self.admin)
        self.assertEqual(st, 200, body)
        self.assertEqual(self.req('GET', '/api/session', None, ck)[0], 401, 'Rollenwechsel beendet alte Sitzung')

    def test_unauthenticated_write_is_401_before_body(self):
        before = self.user_count()
        st, body, _ = self.req('POST', '/api/users', {'username': 'x1', 'password': PASS, 'role': 'viewer'})
        self.assertEqual((st, body.get('errorCode')), (401, 'MP-AUTH-001'))
        self.assertEqual(self.user_count(), before)

    # --- Body / JSON -----------------------------------------------------------------------------
    def test_oversized_body_rejected_server_stays_responsive(self):
        # Content-Length ueber MAX_BODY, Body wird nicht gesendet: Antwort kommt sofort, Verbindung wird geschlossen.
        s = socket.create_connection(('127.0.0.1', self.port), timeout=10)
        s.sendall((f'POST /api/users HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nCookie: {self.admin}\r\n'
                   f'Content-Type: application/json\r\nX-MP-Client-Version: {server.APP_VERSION}\r\n'
                   f'Content-Length: {server.MAX_BODY + 1}\r\n\r\n').encode())
        head = s.recv(4096).decode('latin-1')
        s.close()
        status = int(head.split(' ', 2)[1])
        self.assertIn(status, (400, 413), head)
        self.assertIn('MP-DATA-013', head)
        self.assertEqual(self.req('GET', '/api/health')[0], 200)

    def test_oversized_login_body_and_bad_content_length(self):
        st, body, _ = self.req('POST', '/api/login', raw=b'{' + b' ' * (server.MAX_AUTH_BODY + 5) + b'}')
        self.assertEqual((st, body.get('errorCode')), (400, 'MP-DATA-013'))
        for cl in ('abc', '-1', '0'):
            s = socket.create_connection(('127.0.0.1', self.port), timeout=10)
            s.sendall((f'POST /api/login HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nX-MP-Client-Version: {server.APP_VERSION}\r\n'
                       f'Content-Length: {cl}\r\n\r\n').encode())
            self.assertEqual(int(s.recv(4096).decode('latin-1').split(' ', 2)[1]), 400, cl)
            s.close()
        self.assertEqual(self.req('GET', '/api/health')[0], 200)

    def test_invalid_json_on_write_endpoints_is_400_without_state_change(self):
        before = self.user_count()
        with server.db_session() as con:
            rev = con.execute('SELECT revision FROM state WHERE id=1').fetchone()[0]
        cfg_rev = server.config_revision()
        bad = (b'{kaputt', b'[1,2]', b'"text"', b'null', b'{"a": NaN}', b'{"a": Infinity}', b'\xff\xfe\x00', b'')
        for path in ('/api/users', '/api/config/logo', '/api/license', '/api/chat/messages'):
            for raw in bad:
                st, body, _ = self.req('POST', path, raw=raw, cookie=self.admin)
                self.assertEqual((st, body.get('errorCode')), (400, 'MP-DATA-013'), (path, raw))
        for raw in bad:
            st, body, _ = self.req('PATCH', '/api/config', raw=raw, cookie=self.admin)
            self.assertEqual(st, 400, raw)
        self.assertEqual(self.user_count(), before)
        with server.db_session() as con:
            self.assertEqual(con.execute('SELECT revision FROM state WHERE id=1').fetchone()[0], rev)
        self.assertEqual(server.config_revision(), cfg_rev)

    def test_wrong_content_type_does_not_bypass_validation(self):
        before = self.user_count()
        for ctype in ('text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data; boundary=x'):
            st, body, _ = self.req('POST', '/api/users', raw=b'username=evil&password=x&role=admin', cookie=self.admin, headers={'Content-Type': ctype})
            self.assertEqual((st, body.get('errorCode')), (400, 'MP-DATA-013'), ctype)
        self.assertEqual(self.user_count(), before)

    # --- Logo --------------------------------------------------------------------------------------
    def logo(self, data, ctype, cookie=None):
        return self.req('POST', '/api/config/logo', {'revision': server.config_revision(), 'contentType': ctype,
                                                     'data': base64.b64encode(data).decode() if isinstance(data, bytes) else data}, self.admin if cookie is None else cookie)

    def test_invalid_logo_uploads_rejected_config_unchanged(self):
        cfg_before = json.dumps(server.current_config(), sort_keys=True)
        files_before = sorted(p.name for p in server.CONFIG_DIR.iterdir())
        huge = PNG + b'\0' * (server.CONFIG_LOGO_MAX + 1)
        cases = {
            'falscher MIME': (PNG, 'application/pdf'),
            'text/html': (b'<html></html>', 'text/html'),
            'leer': (b'', 'image/png'),
            'zu gross': (huge, 'image/png'),
            'PNG-Typ, JPG-Inhalt': (b'\xff\xd8\xff\xe0' + b'\0' * 20, 'image/png'),
            'JPG-Typ, PNG-Inhalt': (PNG, 'image/jpeg'),
            'PHP als PNG': (b'<?php system($_GET[0]); ?>', 'image/png'),
            'SVG kein XML': (b'not xml at all', 'image/svg+xml'),
            'SVG DOCTYPE/Entity': (b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>', 'image/svg+xml'),
        }
        for label, (data, ctype) in cases.items():
            st, body, _ = self.logo(data, ctype)
            self.assertEqual((st, body.get('errorCode')), (400, 'MP-CFG-005'), label)
        st, body, _ = self.logo('***nicht base64***', 'image/png')
        self.assertEqual(st, 400)
        self.assertEqual(self.logo(PNG, 'image/png', cookie='')[0], 401)
        self.assertEqual(json.dumps(server.current_config(), sort_keys=True), cfg_before)
        self.assertEqual(sorted(p.name for p in server.CONFIG_DIR.iterdir()), files_before)

    def test_malicious_svg_is_sanitized_or_rejected_never_stored_active(self):
        evil = (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><script>alert(1)</script>'
                b'<rect width="1" height="1" onclick="x()"/><a href="javascript:alert(1)"><circle r="1"/></a>'
                b'<image href="http://evil.example/x.png"/><foreignObject><iframe/></foreignObject></svg>')
        st, body, _ = self.logo(evil, 'image/svg+xml')
        if st == 200:
            stored = (server.CONFIG_DIR / 'logo.svg').read_bytes().lower()
            for bad in (b'<script', b'onload', b'onclick', b'javascript:', b'evil.example', b'foreignobject', b'iframe'):
                self.assertNotIn(bad, stored, bad)
            st2, _, text = self.req('GET', '/api/config/logo', None, self.admin)
            self.assertEqual(st2, 200)
            self.assertNotIn(b'<script', text.lower())
            self.req('PATCH', '/api/config', {'revision': server.config_revision(), 'company': {'logoFile': ''}}, self.admin)
        else:
            self.assertEqual((st, body.get('errorCode')), (400, 'MP-CFG-005'))

    def test_logo_upload_requires_admin(self):
        self.make_user('logoview')
        ck = self.login('logoview')
        before = sorted(p.name for p in server.CONFIG_DIR.iterdir())
        self.assertEqual(self.logo(PNG, 'image/png', cookie=ck)[0], 403)
        self.assertEqual(sorted(p.name for p in server.CONFIG_DIR.iterdir()), before)

    # --- Lizenz -----------------------------------------------------------------------------------
    def test_license_garbage_rejected_nothing_written(self):
        path = server.license_path()
        existed = path.exists()
        cfg_files = sorted(p.name for p in server.CONFIG_DIR.iterdir())
        for content in ('', 'Muell', '{}', '{"payload": 1}', '\x00\x01\x02', 'A' * 100000, '[]', '{"payload": {}, "signature": "zz"}'):
            st, body, _ = self.req('POST', '/api/license', {'content': content}, self.admin)
            self.assertEqual(st, 400, (content[:20], body))
            self.assertTrue(str(body.get('errorCode', '')).startswith('MP-LIC-'), body)
        for content in (None, 5, ['x'], {'a': 1}):
            st, body, _ = self.req('POST', '/api/license', {'content': content}, self.admin)
            self.assertEqual((st, body.get('errorCode')), (400, 'MP-LIC-003'), content)
        self.assertEqual(path.exists(), existed)
        self.assertEqual(sorted(p.name for p in server.CONFIG_DIR.iterdir()), cfg_files)

    def test_license_upload_requires_session_and_admin(self):
        self.assertEqual(self.req('POST', '/api/license', {'content': 'x'})[0], 401)
        self.make_user('licview')
        self.assertEqual(self.req('POST', '/api/license', {'content': 'x'}, self.login('licview'))[0], 403)
        self.assertEqual(self.req('DELETE', '/api/license', None, self.login('licview'))[0], 403)

    # --- Origin ------------------------------------------------------------------------------------
    def test_cross_origin_writes_rejected_with_session(self):
        before = self.user_count()
        for origin in ('http://evil.example', 'https://127.0.0.1:%d' % self.port, 'http://127.0.0.1:1', 'http://127.0.0.1.evil.example:%d' % self.port):
            st, body, _ = self.req('POST', '/api/users', {'username': 'xo', 'password': PASS, 'role': 'viewer'}, self.admin, {'Origin': origin})
            self.assertEqual((st, body.get('errorCode')), (403, 'MP-REQ-403'), origin)
            st, _, _ = self.req('DELETE', '/api/license', None, self.admin, {'Origin': origin})
            self.assertEqual(st, 403, origin)
            st, _, _ = self.req('PATCH', '/api/config', {'revision': 0}, self.admin, {'Origin': origin})
            self.assertEqual(st, 403, origin)
        self.assertEqual(self.user_count(), before)
        # Reads sind nicht Origin-geprueft (kein CORS-Freigabe-Header), aber die Antwort traegt keine CORS-Freigabe.
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request('GET', '/api/health', headers={'Origin': 'http://evil.example'})
        r = conn.getresponse(); r.read(); conn.close()
        self.assertIsNone(r.getheader('Access-Control-Allow-Origin'))

    # --- Path Traversal ---------------------------------------------------------------------------
    def test_static_path_traversal_never_serves_files(self):
        secret = b'machines'
        paths = ['/../server.py', '/..%2fserver.py', '/%2e%2e/server.py', '/%2e%2e%2fconfig/firma.json', '/config/firma.json',
                 '/data/maschinenplanung.sqlite3', '/server.py', '/mp_config.py', '/backups/', '/Backup_Datenbank.py',
                 '/config/license.json', '/index.html/../server.py', '/static/../server.py', '/..\\server.py',
                 '/%252e%252e/server.py', '/....//server.py', '/\x00', '//etc/passwd', '/etc/passwd', '/.git/config']
        for p in paths:
            for cookie in ('', self.admin):
                try:
                    conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
                    conn.putrequest('GET', p.replace('\x00', '%00'), skip_accept_encoding=True)
                    conn.putheader('Cookie', cookie)
                    conn.endheaders()
                    r = conn.getresponse(); body = r.read(); conn.close()
                except (http.client.HTTPException, OSError) as e:
                    self.fail(f'{p}: {e}')
                self.assertIn(r.status, (400, 401, 403, 404), (p, r.status))
                for needle in (b'import ', b'SQLite format', b'tenantId', b'root:x:'):
                    self.assertNotIn(needle, body, (p, needle))
        self.assertEqual(self.req('GET', '/api/health')[0], 200)

    def test_index_still_served(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        conn.request('GET', '/'); r = conn.getresponse(); r.read(); conn.close()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.getheader('X-Content-Type-Options'), 'nosniff')


if __name__ == '__main__':
    unittest.main(verbosity=2)

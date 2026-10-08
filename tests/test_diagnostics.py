#!/usr/bin/env python3
"""#52 K6: /api/diagnostics nur fuer Admin, ohne Geheimnisse und Inhalte. Arbeitet nur in Temp-Ordnern."""
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
TMP = Path(tempfile.mkdtemp(prefix='mp-diag-'))
os.environ['MP_CONFIG_DIR'] = str(TMP / 'config')
import server  # noqa: E402

server.DATA_DIR = TMP / 'data'
server.DB_PATH = server.DATA_DIR / 'maschinenplanung.sqlite3'
server.PBKDF2_ITERS = 1000
server.init_db(seed='werbetechnik')
server.load_config()
PASS = 'Diag-Test-Passwort-1'
ORDER_TEXT = 'GEHEIME-AUFTRAGSBESCHREIBUNG-4711'
CHAT_TEXT = 'GEHEIMER-CHATTEXT-0815'


class Diagnostics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.ALLOWED_NETWORK = server.ipaddress.ip_network('127.0.0.0/8')
        server.create_or_reset_admin('admin', PASS)
        server.Handler.log_message = lambda *a, **k: None
        cls.httpd = server.MPHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.admin = cls.login('admin')
        with server.db_session() as con:
            st = json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0])
            st['workSteps'] = [{'id': 'ws1', 'fa': 'FA-1', 'order': 'FA-1', 'description': ORDER_TEXT}]
            con.execute('UPDATE state SET json=? WHERE id=1', (json.dumps(st),))
            ch = con.execute('SELECT id FROM chat_channels LIMIT 1').fetchone()
            if not ch:
                con.execute("INSERT INTO chat_channels(kind,name,members,created_by,created_at) VALUES('all','Alle','[]','admin',?)", (server.now_iso(),))
                ch = con.execute('SELECT id FROM chat_channels LIMIT 1').fetchone()
            con.execute('INSERT INTO chat_messages(channel_id,author,text,ts) VALUES(?,?,?,?)', (ch[0], 'admin', CHAT_TEXT, server.now_iso()))
            con.execute('INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,NULL)',
                        (server.now_iso(), 'admin', 'Update fehlgeschlagen', json.dumps({'version': '99.0.0', 'stage': 'failed', 'error': 'Testfehler'}),))
        with server.db_session() as con:
            dep = json.loads(con.execute('SELECT json FROM state WHERE id=1').fetchone()[0])['departments'][0]['id']
        for name, role in (('viewer1', 'viewer'), ('gf1', 'gf'), ('prod1', 'production'), ('av1', 'production_planning'), ('pm1', 'project_management'), ('sales1', 'sales')):
            st, _, _ = cls.req('POST', '/api/users', {'username': name, 'password': PASS, 'role': role, 'departmentId': dep if role == 'production' else ''}, cls.admin)
            assert st == 201, (name, st)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown(); cls.httpd.server_close()

    @classmethod
    def req(cls, method, path, body=None, cookie=''):
        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=15)
        conn.request(method, path, json.dumps(body) if body is not None else None,
                     {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION, 'Cookie': cookie})
        r = conn.getresponse(); raw = r.read(); hdr = dict(r.getheaders()); conn.close()
        return r.status, raw, hdr

    @classmethod
    def login(cls, name):
        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=15)
        conn.request('POST', '/api/login', json.dumps({'username': name, 'password': PASS}), {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION})
        r = conn.getresponse(); r.read(); ck = (r.getheader('Set-Cookie') or '').split(';')[0]; conn.close()
        return ck

    def test_admin_gets_expected_keys(self):
        st, raw, hdr = self.req('GET', '/api/diagnostics', None, self.admin)
        self.assertEqual(st, 200)
        d = json.loads(raw)
        for k in ('product', 'database', 'disk', 'backup', 'updates', 'modules', 'config', 'counts', 'warnings'):
            self.assertIn(k, d)
        self.assertEqual(d['product']['version'], server.APP_VERSION)
        self.assertEqual(d['database']['integrity'], 'ok')
        self.assertEqual(d['counts']['workSteps'], 1)
        self.assertGreaterEqual(d['counts']['users'], 1)
        self.assertIn('Letztes Update fehlgeschlagen', d['warnings'])
        self.assertIn('Kein Backup gefunden', d['warnings'])
        self.assertIn('tls', d['config'])
        # #52: Testserver laeuft ohne TLS -> Empfehlung im selben Stil wie die anderen Warnungen.
        self.assertFalse(d['config']['tls'])
        self.assertIn(server.DIAG_WARN_NO_TLS, d['warnings'])
        self.assertIn('HTTPS nicht eingerichtet', server.DIAG_WARN_NO_TLS)
        self.assertIn('HTTPS_Einrichten.ps1', server.DIAG_WARN_NO_TLS)

    def test_tls_warning_only_without_tls(self):
        on, off = server.build_diagnostics(True), server.build_diagnostics(False)
        self.assertTrue(on['config']['tls'])
        self.assertNotIn(server.DIAG_WARN_NO_TLS, on['warnings'])
        self.assertFalse(off['config']['tls'])
        self.assertEqual(off['warnings'].count(server.DIAG_WARN_NO_TLS), 1)
        # Die uebrigen Warnungen bleiben gleich.
        self.assertEqual([w for w in off['warnings'] if w != server.DIAG_WARN_NO_TLS], on['warnings'])

    def test_other_roles_forbidden(self):
        self.assertEqual(self.req('GET', '/api/diagnostics')[0], 401)
        for name in ('viewer1', 'gf1', 'prod1', 'av1', 'pm1', 'sales1'):
            st, raw, _ = self.req('GET', '/api/diagnostics', None, self.login(name))
            self.assertEqual(st, 403, name)
            self.assertEqual(json.loads(raw).get('errorCode'), 'MP-AUTH-002')
            self.assertEqual(self.req('GET', '/api/diagnostics?download=1', None, self.login(name))[0], 403)

    def test_no_secrets_or_content(self):
        secrets_ = []
        with server.db_session() as con:
            for c in [c[1] for c in con.execute('PRAGMA table_info(users)')]:
                if any(x in c for x in ('hash', 'salt', 'pass')):
                    secrets_ += [r[0] for r in con.execute(f'SELECT {c} FROM users')]
            secrets_ += [r[0] for r in con.execute('SELECT token_hash FROM sessions')]
        secrets_ = [str(x) for x in secrets_ if x and len(str(x)) >= 8]
        self.assertTrue(secrets_, 'Testdaten fuer Geheimnisse fehlen')
        for url in ('/api/diagnostics', '/api/diagnostics?download=1'):
            text = self.req('GET', url, None, self.admin)[1].decode('utf-8')
            for needle in secrets_ + [ORDER_TEXT, CHAT_TEXT, PASS, self.admin.split('=', 1)[1]]:
                self.assertNotIn(needle, text)
            self.assertNotIn('password', text.lower())

    def test_download_header(self):
        st, raw, hdr = self.req('GET', '/api/diagnostics?download=1', None, self.admin)
        self.assertEqual(st, 200)
        cd = hdr.get('Content-Disposition', '')
        self.assertTrue(cd.startswith('attachment; filename="diagnose-%s-' % server.APP_VERSION) and cd.endswith('.json"'), cd)
        self.assertEqual(json.loads(raw)['product']['version'], server.APP_VERSION)


if __name__ == '__main__':
    unittest.main()

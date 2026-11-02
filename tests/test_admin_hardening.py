#!/usr/bin/env python3
"""Negativtests: Host-Header (DNS-Rebinding), letzter Admin, Transaktionen (Rollback), Chat-Rechte,
Passwort-Regeln, Log-Escaping.

Aufruf:  python tests/test_admin_hardening.py
Arbeitet nur in Temp-Ordnern, nie mit Live-Daten.
"""
import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix='mp-adminhard-'))
os.environ['MP_CONFIG_DIR'] = str(TMP / 'config')
import server  # noqa: E402

server.DATA_DIR = TMP / 'data'
server.DB_PATH = server.DATA_DIR / 'maschinenplanung.sqlite3'
server.PBKDF2_ITERS = 1000
server.init_db(seed='werbetechnik')
server.load_config()
PASS = 'Haertung-Test-Passwort-1'
ORIG_LOG = server.Handler.log_message


class Hardening(unittest.TestCase):
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
        h = {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION, 'Cookie': cookie}
        conn.request(method, path, json.dumps(body) if body is not None else None, h)
        r = conn.getresponse(); text = r.read(); conn.close()
        try:
            payload = json.loads(text or b'{}')
        except ValueError:
            payload = {}
        return r.status, payload

    @classmethod
    def login(cls, name, pw=PASS):
        conn = http.client.HTTPConnection('127.0.0.1', cls.port, timeout=15)
        conn.request('POST', '/api/login', json.dumps({'username': name, 'password': pw}),
                     {'Content-Type': 'application/json', 'X-MP-Client-Version': server.APP_VERSION})
        r = conn.getresponse(); r.read()
        assert r.status == 200, (name, r.status)
        ck = r.getheader('Set-Cookie').split(';')[0]
        conn.close()
        return ck

    def make_user(self, name, role='viewer'):
        st, body = self.req('POST', '/api/users', {'username': name, 'password': PASS, 'role': role, 'departmentId': ''}, self.admin)
        self.assertEqual(st, 201, body)
        return body['id']

    def q(self, sql, args=()):
        with server.db_session() as con:
            return con.execute(sql, args).fetchall()

    # --- Host-Header -----------------------------------------------------------------------------
    def test_host_suffix_only_for_own_domain(self):
        old = server.LOCAL_HOST_NAMES
        server.LOCAL_HOST_NAMES = {'pc01', 'pc01.firma.local'}
        try:
            ok = lambda h: server.host_name_allowed(h, '192.168.1.5')
            self.assertTrue(ok('pc01'))
            self.assertTrue(ok('pc01.firma.local'))
            self.assertTrue(ok('PC01.Firma.Local.'))
            self.assertFalse(ok('pc01.angreifer.de'))
            self.assertFalse(ok('pc01.firma.local.angreifer.de'))
            self.assertFalse(ok('pc01.local'))
            self.assertFalse(ok('evil.firma.local'))
            server.EXTRA_ALLOWED_HOSTS.add('pc01.alias.de')
            self.assertTrue(ok('pc01.alias.de'))
            server.EXTRA_ALLOWED_HOSTS.discard('pc01.alias.de')
        finally:
            server.LOCAL_HOST_NAMES = old

    # --- letzter Admin -----------------------------------------------------------------------------
    def test_patch_cannot_remove_last_active_admin(self):
        b = self.make_user('adm2', 'admin')
        # Akteur wird nach der Rechtepruefung deaktiviert (Wettlauf): nur adm2 bleibt aktiver Admin.
        orig = server.Handler.require_current_client
        def racing(self_):
            self.q_exec("UPDATE users SET active=0 WHERE username='admin'")
            return orig(self_)
        server.Handler.require_current_client = racing
        try:
            st, body = self.req('PATCH', f'/api/users/{b}', {'active': False}, self.admin)
            self.assertEqual((st, body.get('errorCode')), (400, 'MP-AUTH-016'))
            self.q_exec("UPDATE users SET active=1 WHERE username='admin'")
            st, body = self.req('PATCH', f'/api/users/{b}', {'role': 'viewer', 'departmentId': ''}, self.admin)
            self.assertEqual((st, body.get('errorCode')), (400, 'MP-AUTH-016'))
        finally:
            server.Handler.require_current_client = orig
            self.q_exec("UPDATE users SET active=1 WHERE username='admin'")
        self.assertEqual(self.q("SELECT role,active FROM users WHERE id=?", (b,))[0]['active'], 1)

    def q_exec(self, sql, args=()):
        with server.db_session() as con:
            con.execute(sql, args)

    def test_patch_demote_other_admin_ok_when_one_remains(self):
        b = self.make_user('adm3', 'admin')
        st, body = self.req('PATCH', f'/api/users/{b}', {'role': 'viewer', 'departmentId': ''}, self.admin)
        self.assertEqual(st, 200, body)

    # --- Transaktionen -------------------------------------------------------------------------------
    def test_patch_user_rolls_back_on_error(self):
        uid = self.make_user('tx1')
        ck = self.login('tx1')
        orig = server.login_attempts_clear_user
        def boom(_):
            raise RuntimeError('boom')
        server.login_attempts_clear_user = boom
        try:
            st, _ = self.req('PATCH', f'/api/users/{uid}', {'password': 'Neues-Passwort-77', 'active': False}, self.admin)
            self.assertEqual(st, 500)
        finally:
            server.login_attempts_clear_user = orig
        row = self.q("SELECT active FROM users WHERE id=?", (uid,))[0]
        self.assertEqual(row['active'], 1)                      # UPDATE zurueckgerollt
        self.assertEqual(self.req('GET', '/api/session', None, ck)[0], 200)   # Sitzung nicht geloescht
        self.login('tx1')                                       # altes Passwort gilt weiter

    def test_create_user_rolls_back_audit_failure(self):
        # Audit-Eintrag scheitert (Tabelle umbenannt) -> Benutzer darf nicht angelegt bleiben.
        self.q_exec("ALTER TABLE server_audit RENAME TO server_audit_x")
        try:
            st, _ = self.req('POST', '/api/users', {'username': 'tx2', 'password': PASS, 'role': 'viewer', 'departmentId': ''}, self.admin)
            self.assertEqual(st, 500)
        finally:
            self.q_exec("ALTER TABLE server_audit_x RENAME TO server_audit")
        self.assertEqual(self.q("SELECT count(*) c FROM users WHERE username='tx2'")[0]['c'], 0)

    def test_chat_post_rolls_back(self):
        self.make_user('cx1')
        ck = self.login('cx1')
        st, ch = self.req('POST', '/api/chat/channels', {'kind': 'group', 'name': 'TX', 'members': ['admin']}, ck)
        self.assertEqual(st, 201, ch)
        cid = ch['channel']['id']
        orig = server.notify_chat_message
        def boom(*a, **k):
            raise RuntimeError('boom')
        server.notify_chat_message = boom
        try:
            st, _ = self.req('POST', '/api/chat/messages', {'channel': cid, 'text': 'hallo'}, ck)
            self.assertEqual(st, 500)
        finally:
            server.notify_chat_message = orig
        self.assertEqual(self.q("SELECT count(*) c FROM chat_messages WHERE channel_id=?", (cid,))[0]['c'], 0)

    # --- Chat ------------------------------------------------------------------------------------------
    def test_chat_unkeep_only_by_setter_or_admin(self):
        self.make_user('ck1'); self.make_user('ck2')
        c1, c2 = self.login('ck1'), self.login('ck2')
        st, ch = self.req('POST', '/api/chat/channels', {'kind': 'group', 'name': 'K', 'members': ['ck2']}, c1)
        self.assertEqual(st, 201, ch)
        cid = ch['channel']['id']
        st, m = self.req('POST', '/api/chat/messages', {'channel': cid, 'text': 'wichtig'}, c1)
        mid = m['message']['id']
        self.assertEqual(self.req('POST', '/api/chat/keep', {'channel': cid, 'message': mid, 'keep': True}, c1)[0], 200)
        st, body = self.req('POST', '/api/chat/keep', {'channel': cid, 'message': mid, 'keep': False}, c2)
        self.assertEqual((st, body.get('errorCode')), (403, 'MP-CHAT-008'))
        self.assertEqual(self.q("SELECT keep FROM chat_messages WHERE id=?", (mid,))[0]['keep'], 1)
        self.assertEqual(self.req('POST', '/api/chat/keep', {'channel': cid, 'message': mid, 'keep': False}, c1)[0], 200)

    def test_chat_inactive_member_kept_on_group_change(self):
        self.make_user('cm1'); u2 = self.make_user('cm2'); self.make_user('cm3')
        c1 = self.login('cm1')
        st, ch = self.req('POST', '/api/chat/channels', {'kind': 'group', 'name': 'M', 'members': ['cm2']}, c1)
        self.assertEqual(st, 201, ch)
        cid = ch['channel']['id']
        self.q_exec("UPDATE users SET active=0 WHERE id=?", (u2,))
        st, body = self.req('POST', '/api/chat/members', {'channel': cid, 'add': ['cm3']}, c1)
        self.assertEqual(st, 200, body)
        members = json.loads(self.q("SELECT members FROM chat_channels WHERE id=?", (cid,))[0]['members'])
        self.assertIn('cm2', members); self.assertIn('cm3', members)
        # Neu hinzufuegen eines inaktiven Kontos bleibt verboten.
        u4 = self.make_user('cm4'); self.q_exec("UPDATE users SET active=0 WHERE id=?", (u4,))
        st, body = self.req('POST', '/api/chat/members', {'channel': cid, 'add': ['cm4']}, c1)
        self.assertEqual((st, body.get('errorCode')), (400, 'MP-CHAT-003'))

    # --- Passwort / Admin-Reset ------------------------------------------------------------------------
    def test_new_password_must_differ(self):
        self.make_user('pw1')
        ck = self.login('pw1')
        st, body = self.req('POST', '/api/password', {'currentPassword': PASS, 'newPassword': PASS}, ck)
        self.assertEqual((st, body.get('errorCode')), (400, 'MP-AUTH-026'))
        st, _ = self.req('POST', '/api/password', {'currentPassword': PASS, 'newPassword': 'Anderes-Passwort-22'}, ck)
        self.assertEqual(st, 200)

    def test_create_or_reset_admin_applies_policy(self):
        with self.assertRaises(ValueError):
            server.create_or_reset_admin('admin9', 'kurz1')
        with self.assertRaises(ValueError):
            server.create_or_reset_admin('admin9', 'nurbuchstaben-lang')

    # --- Log -----------------------------------------------------------------------------------------------
    def test_log_message_escapes_control_chars(self):
        class Fake:
            def address_string(self): return '127.0.0.1'
        buf = io.StringIO()
        with redirect_stdout(buf):
            ORIG_LOG(Fake(), '%s', 'GET /x\r\nFAKE 200\x1b[31m')
        out = buf.getvalue()
        self.assertEqual(out.count('\n'), 1)
        self.assertNotIn('\r', out); self.assertNotIn('\x1b', out)
        self.assertIn('\\x0d\\x0a', out)


if __name__ == '__main__':
    unittest.main(verbosity=1)

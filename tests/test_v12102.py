#!/usr/bin/env python3
"""Hotfix V12.10.2: Login-Härtung, Transport-Schutz, Rechte/Validierung (Audit V-02, V-04).

Aufruf:  python tests/test_v12102.py
Startet den echten HTTP-Server auf 127.0.0.1 mit frischem Datenstand im Temp-Ordner.
"""
from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
import server  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    RESULTS.append((bool(cond), label))
    print(("PASS " if cond else "FAIL ") + label)


tmp = Path(tempfile.mkdtemp(prefix="mp-v12102-"))
server.DATA_DIR = tmp
server.DB_PATH = tmp / "maschinenplanung.sqlite3"
server.PBKDF2_ITERS = 1000  # Test schneller; Sperrlogik ist unabhängig von der Iterationszahl
server.init_db()
PW = "Test-Passwort-1"
with server.db_session() as con:
    for name, role in (("admin", "admin"), ("viewer", "viewer")):
        salt, digest = server.hash_password(PW)
        con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,'',1,?,?)",
                    (name, salt, digest, role, server.now_iso(), server.now_iso()))

server.ALLOWED_NETWORK = server.ipaddress.ip_network("127.0.0.0/8")
httpd = server.MPHTTPServer(("127.0.0.1", 0), server.Handler)
server.Handler.log_message = lambda *a, **k: None
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
HOST = f"127.0.0.1:{PORT}"


def req(method: str, path: str, body=None, headers=None, raw: bytes | None = None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    h = {"Content-Type": "application/json", "X-MP-Client-Version": server.APP_VERSION, "Host": HOST}
    h.update(headers or {})
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    c.putrequest(method, path, skip_host=True)
    for k, v in h.items():
        if v is not None:
            c.putheader(k, v)
    if data is not None:
        c.putheader("Content-Length", str(len(data)))
    c.endheaders(data)
    r = c.getresponse()
    txt = r.read()
    c.close()
    try:
        return r.status, json.loads(txt), r.getheader("Set-Cookie")
    except ValueError:
        return r.status, {}, r.getheader("Set-Cookie")


def login(user, pw):
    return req("POST", "/api/login", {"username": user, "password": pw})


# --- V-02 / B-H2: Sperre je Benutzer, eigener Login setzt fremde Fehlversuche nicht zurück -------------
for _ in range(server.LOGIN_MAX_PER_IP_USER):
    login("admin", "falsch")
st, _, _ = login("viewer", PW)
check(st == 200, "Viewer kann sich trotz Fehlversuchen auf admin anmelden")
st, b, _ = login("admin", PW)
check(st == 429 and b.get("errorCode") == "MP-AUTH-003", "admin bleibt gesperrt – Viewer-Login löscht fremde Fehlversuche nicht")
server.LOGIN_FAILS.clear()

# Race: parallele Versuche zählen alle (Reservierung vor der Passwortprüfung)
stamps = [server.login_attempt_reserve("10.0.0.9", "admin") for _ in range(server.LOGIN_MAX_PER_IP_USER + 3)]
check(sum(s is not None for s in stamps) == server.LOGIN_MAX_PER_IP_USER, "Reservierung begrenzt parallele Versuche auf das Limit")
server.LOGIN_FAILS.clear()

# Sperre je Benutzer über mehrere IPs
for i in range(server.LOGIN_MAX_PER_USER):
    server.login_attempt_reserve(f"10.1.0.{i}", "admin")
check(server.login_attempt_reserve("10.2.0.1", "admin") is None, "Sperre je Benutzer greift auch bei wechselnden IPs")
server.LOGIN_FAILS.clear()

# Erfolg zählt nicht gegen die IP, frühere IP-Fehlversuche bleiben
for _ in range(3):
    server.login_attempt_reserve("10.3.0.1", "x")
s = server.login_attempt_reserve("10.3.0.1", "viewer")
server.login_attempt_succeeded("10.3.0.1", "viewer", s)
check(len(server.LOGIN_FAILS.get("ip:10.3.0.1", [])) == 3, "Erfolg entfernt nur die eigene Reservierung vom IP-Zähler")
server.LOGIN_FAILS.clear()

# N3: Passwortwechsel unterliegt der Sperre
st, _, cookie = login("viewer", PW)
ck = {"Cookie": cookie.split(";", 1)[0]}
for _ in range(server.LOGIN_MAX_PER_IP_USER):
    req("POST", "/api/password", {"currentPassword": "falsch", "newPassword": "Neu-Passwort-1"}, ck)
st, b, _ = req("POST", "/api/password", {"currentPassword": PW, "newPassword": "Neu-Passwort-1"}, ck)
check(st == 429, f"Passwortwechsel nach Fehlversuchen gesperrt ({st})")
server.LOGIN_FAILS.clear()

# --- B-M1 / N6: Body-Limit und Client-Header beim Login -------------------------------------------------
st, b, _ = req("POST", "/api/login", raw=b"{" + b" " * (server.MAX_AUTH_BODY + 10) + b"}")
check(st == 400, f"Login-Body über {server.MAX_AUTH_BODY} Byte abgelehnt ({st})")
st, b, _ = req("POST", "/api/login", {"username": "viewer", "password": PW}, {"X-MP-Client-Version": None})
check(st == 403 and b.get("errorCode") == "MP-REQ-403", "Login ohne X-MP-Client-Version abgelehnt (Login-CSRF)")
st, b, _ = req("POST", "/api/chat/messages", raw=b"{}" , headers={"Cookie": None})
check(st == 401, "POST ohne Sitzung: 401 vor dem Lesen des Body")
check(server.Handler.timeout == 30, "Socket-Timeout 30 s gesetzt")

# --- B-M2: Host/Origin --------------------------------------------------------------------------------
st, b, _ = req("GET", "/api/health", headers={"Host": f"evil.example:{PORT}"})
check(st == 421 and b.get("errorCode") == "MP-REQ-421", "Fremder Host-Header (DNS-Rebinding) → 421")
st, _, _ = req("GET", "/api/health")
check(st == 200, "Host = Server-IP erlaubt")
check(server.host_name_allowed(next(iter(server.LOCAL_HOST_NAMES), "x"), "192.168.1.5"), "Eigener PC-Name erlaubt")
st, b, _ = req("POST", "/api/login", {"username": "viewer", "password": PW}, {"Origin": "http://evil.example"})
check(st == 403, "Schreibzugriff mit fremdem Origin abgelehnt")
st, _, _ = req("POST", "/api/login", {"username": "viewer", "password": PW}, {"Origin": f"http://{HOST}"})
check(st == 200, "Schreibzugriff mit eigenem Origin erlaubt")

# --- N2: globale 500-Hülle --------------------------------------------------------------------------
st, _, cookie = login("admin", PW)
ack = {"Cookie": cookie.split(";", 1)[0]}
orig = server.chat_post
server.chat_post = lambda *a, **k: 1 / 0
st, b, _ = req("POST", "/api/chat/members", {"add": 5}, ack)
server.chat_post = orig
check(st == 500 and b.get("errorCode") == "MP-SRV-500", f"Unerwarteter Fehler → 500 MP-SRV-500 statt Verbindungsabbruch ({st})")

# --- N12: active muss bool sein ------------------------------------------------------------------------
with server.db_session() as con:
    vid = con.execute("SELECT id FROM users WHERE username='viewer'").fetchone()["id"]
st, b, _ = req("PATCH", f"/api/users/{vid}", {"active": "false"}, ack)
check(st == 400, "PATCH active='false' (String) abgelehnt")

# --- B-M5: AV ändert keine Formate --------------------------------------------------------------------
old = {"formats": [], "workSteps": []}
new = {"formats": [{"id": "f1"}], "workSteps": []}
ok, reason = server.production_planning_change_allowed(old, new)
check(not ok and "formats" in reason, "Arbeitsvorbereitung darf 'formats' nicht ändern")

# --- N8: Formatverknüpfung ------------------------------------------------------------------------------
fmt = lambda wsid: {"id": "f1", "number": "F-1", "departmentId": "tz", "L": 1000, "B": 800, "workStepId": wsid}  # noqa: E731
base = {"machines": [], "baseFormats": [], "history": [{"originalOrderId": "done1"}], "workSteps": [{"id": "s1"}]}
ok, code, _ = server.validate_formats({}, {**base, "formats": [fmt("weg")]}, {"tz"})
check(not ok and code == "MP-FMT-010", "Neue Verknüpfung auf unbekannten Auftrag abgelehnt")
ok, _, _ = server.validate_formats({"formats": [fmt("weg")]}, {**base, "formats": [fmt("weg")]}, {"tz"})
check(ok, "Bestehende Verknüpfung auf gelöschten Auftrag bleibt erlaubt (Altbestand)")
ok, _, _ = server.validate_formats({}, {**base, "formats": [fmt("s1")]}, {"tz"})
check(ok, "Verknüpfung auf vorhandenen Auftrag erlaubt")
ok, _, _ = server.validate_formats({}, {**base, "formats": [fmt("done1")]}, {"tz"})
check(ok, "Verknüpfung auf fertigen Auftrag (Historie) erlaubt")

# --- N1: Typprüfung meta/ui/planVersions ---------------------------------------------------------------
with server.db_session() as con:
    state = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
for key, val in (("meta", "x"), ("ui", []), ("planVersions", {}), ("planVersions", ["x"])):
    ok, code, _ = server.validate_state(state, {**state, key: val})
    check(not ok and code == "MP-DATA-014", f"{key}={val!r} → MP-DATA-014")
st, b, _ = req("GET", "/api/state", headers=ack)
rev = b.get("revision")
st, b, _ = req("PUT", "/api/state", {"revision": rev, "data": {**b["data"], "meta": "kaputt"}}, ack)
check(st == 400 and b.get("errorCode") == "MP-DATA-014", f"PUT mit meta als String → 400 statt Abbruch ({st})")

httpd.shutdown()
failed = [x for x in RESULTS if not x[0]]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

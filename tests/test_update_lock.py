#!/usr/bin/env python3
"""Rollback = nur Code (Entscheidung 09.10.2026): Das automatische Zurueckspielen der Datenbank in UPDATE_LIVE.ps1
darf keine Benutzerdaten verwerfen.

Beweis in zwei Teilen:
  1. Server: Solange updates\\installing existiert (UPDATE_LIVE.ps1 ab Schritt 3 bis zum bestandenen Health- und
     Sicherheitscheck, Umzug_Exportieren.ps1 bis zum Umzug), lehnt JEDE schreibende Route mit 503/MP-UPD-003 ab.
     Die Routenliste wird aus server.py gelesen: eine neue schreibende Route ohne Sperre faellt hier auf.
     Der Datenbankinhalt (alle Tabellen ausser sessions) bleibt dabei byte-gleich; Lesen bleibt moeglich.
  2. UPDATE_LIVE.ps1 (statisch): Sperre vor dem Stopp, DB-Rueckspielen nur nach dem Start der neuen Version,
     Entsperren erst nach $Committed, kein Rollback nach $Committed, Sperre im Rollback erst nach dem Health-Check
     des alten Stands entfernt; Rueckstufung nur bewusst (-Rueckstufen) und ohne alte Sicherung.

Aufruf:  python tests/test_update_lock.py      (nur Temp-Ordner)
"""
import hashlib
import http.client
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="mp-updlock-"))
os.environ["MP_CONFIG_DIR"] = str(TMP / "config")
import server  # noqa: E402

server.DATA_DIR = TMP / "data"
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.PBKDF2_ITERS = 1000
server.init_db(seed="werbetechnik")
server.load_config()
server.BASE = TMP          # Sperrdatei TMP/updates/installing statt im Repo
LOCK = TMP / "updates" / "installing"
PASS = "Sperre-Test-Passwort-1"
RESULTS = []


def check(cond, label):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + label)


server.ALLOWED_NETWORK = server.ipaddress.ip_network("127.0.0.0/8")
server.create_or_reset_admin("admin", PASS)
server.Handler.log_message = lambda *a, **k: None
httpd = server.MPHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def req(method, path, body=None, cookie=""):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h = {"Content-Type": "application/json", "X-MP-Client-Version": server.APP_VERSION, "Cookie": cookie}
    conn.request(method, path, json.dumps(body) if body is not None else None, h)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    try:
        return r.status, json.loads(raw or b"{}"), r.getheader("Set-Cookie") or ""
    except ValueError:
        return r.status, {}, ""


def login():
    st, body, ck = req("POST", "/api/login", {"username": "admin", "password": PASS})
    assert st == 200, (st, body)
    return ck.split(";")[0]


def db_fingerprint():
    """Alle Tabellen ausser sessions (Anmeldungen sind keine Produktionsdaten)."""
    con = sqlite3.connect(server.DB_PATH)
    try:
        h = hashlib.sha256()
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            if name == "sessions":
                continue
            h.update(name.encode())
            for row in con.execute(f'SELECT * FROM "{name}" ORDER BY 1'):
                h.update(repr(row).encode())
        return h.hexdigest()
    finally:
        con.close()


# ---------- 1a. Alle schreibenden Routen aus server.py ----------
src = (ROOT / "server.py").read_text(encoding="utf-8")


def body_of(name):
    start = src.index(f"    def {name}(self")
    nxt = re.search(r"\n    def |\n    do_OPTIONS|\n\S", src[start + 10:])
    return src[start:start + 10 + nxt.start()]


write_src = "".join(body_of(n) for n in ("_do_POST", "change_own_password", "_do_PUT", "_do_PATCH", "_do_DELETE", "_config_patch", "save_role_profile"))
routes = set(re.findall(r'"(/api/[^"]*)"', write_src))
AUTH = {"/api/login", "/api/logout"}   # nur Sitzungen (keine Produktionsdaten); Anmelden muss waehrend der Sperre gehen
# Literal aus server.py -> (Methode, Pfad, Body) einer echten Anfrage
COVERED = {
    "/api/updates/install": [("POST", "/api/updates/install", {})],
    "/api/license": [("POST", "/api/license", {"data": "e30="}), ("DELETE", "/api/license", None)],
    "/api/config/logo": [("POST", "/api/config/logo", {"data": "", "contentType": "image/png"})],
    "/api/templates/apply": [("POST", "/api/templates/apply", {"template": "metall_cnc"})],
    "/api/chat/": [("POST", "/api/chat/messages", {"channelId": "x", "text": "hallo"})],
    "/api/notifications/": [("POST", "/api/notifications/read", {"ids": []})],
    "/api/production/([A-Za-z0-9_-]{1,80})/(release|start|pause|resume|partial|finish|abort|label)": [("POST", "/api/production/ws1/start", {"requestId": "r1"})],
    "/api/demand/([a-z-]{1,40})": [("POST", "/api/demand/frame-order", {"requestId": "r2"})],
    "/api/users": [("POST", "/api/users", {"username": "neu", "password": PASS, "role": "viewer"})],
    "/api/password": [("POST", "/api/password", {"oldPassword": PASS, "newPassword": PASS + "x"})],
    "/api/roles/([a-z0-9_-]{2,40})": [("PUT", "/api/roles/testrolle", {"name": "x"}), ("DELETE", "/api/roles/testrolle", None)],
    "/api/users/(\\d+)": [("DELETE", "/api/users/1", None)],
    "/api/users/": [("PATCH", "/api/users/1", {"active": False})],
    "/api/config": [("PATCH", "/api/config", {"company": {"name": "Gesperrt GmbH"}}), ("PUT", "/api/config", {"company": {"name": "Gesperrt GmbH"}})],
    "/api/notifications/prefs": [("PUT", "/api/notifications/prefs", {"mute": True})],
    "/api/state": [("PUT", "/api/state", {"revision": 1, "data": {}})],
}
missing = sorted(routes - AUTH - set(COVERED))
check(len(routes) >= 15 and not missing, f"{len(routes)} schreibende Routen in server.py gefunden, alle im Test abgedeckt" + (f" (fehlen: {missing})" if missing else ""))

# ---------- 1b. Sperre aktiv (wie UPDATE_LIVE.ps1 Schritt 3) ----------
cookie = login()
LOCK.parent.mkdir(parents=True)
LOCK.write_text("12.99.0")
before = db_fingerprint()
for literal, calls in COVERED.items():
    for method, path, body in calls:
        st, payload, _ = req(method, path, body, cookie)
        check(st == 503 and payload.get("errorCode") == "MP-UPD-003", f"Sperre: {method} {path} -> {st} {payload.get('errorCode')}")
check(db_fingerprint() == before, "Datenbank (alle Tabellen ausser sessions) waehrend der Sperre unveraendert")
st, payload, _ = req("GET", "/api/state", None, cookie)
check(st == 200 and "data" in payload, "Lesen bleibt waehrend der Sperre moeglich")
st, payload, _ = req("POST", "/api/login", {"username": "admin", "password": PASS})
check(st == 200, "Anmelden bleibt moeglich (schreibt nur sessions)")
st, payload, _ = req("PUT", "/api/state", {"revision": 1, "data": {}}, cookie)
check("Update" in payload.get("error", ""), "Meldung waehrend eines Updates nennt das Update")

# ---------- 1c. Sperre durch Umzug_Exportieren.ps1 ----------
LOCK.write_text("UMZUG V12.27.0 2026-10-09 12:00:00")
st, payload, _ = req("PUT", "/api/state", {"revision": 1, "data": {}}, cookie)
check(st == 503 and payload.get("errorCode") == "MP-UPD-003" and "Umzug" in payload.get("error", ""), "Umzugssperre: 503/MP-UPD-003 mit Hinweis auf den Umzug")
check(db_fingerprint() == before, "Datenbank auch unter der Umzugssperre unveraendert")

# ---------- 1d. Ohne Sperre wird wieder geschrieben (Gegenprobe) ----------
LOCK.unlink()
st, payload, _ = req("POST", "/api/users", {"username": "nachher", "password": PASS, "role": "viewer"}, cookie)
check(st == 201 and db_fingerprint() != before, "Gegenprobe: ohne Sperre wird geschrieben (201)")

httpd.shutdown()
httpd.server_close()

# ---------- 2. UPDATE_LIVE.ps1: Reihenfolge der Sperre und des Rollbacks ----------
upd = (ROOT / "UPDATE_LIVE.ps1").read_text(encoding="utf-8-sig")
i_lock = upd.index("[IO.File]::WriteAllText($Maintenance")
i_stop = upd.index("Stop-MPServer $OldBase")
i_started = upd.index("$NewStarted = $true")
i_start = upd.index("Start-ScheduledTask -TaskName $MP_TaskName")
i_health = upd.index("Wait-MPHealth $TargetBase $NewVersion")
i_private = upd.index("Assert-MPPrivatePaths $health.Config")
i_commit = upd.index("$Committed = $true")
i_unlock = upd.index("Remove-Item -LiteralPath $Maintenance")
i_catch = upd.index("\ncatch {")
check(i_lock < i_stop < i_started < i_start < i_health < i_private < i_commit < i_unlock < i_catch,
      "Sperre vor dem Stopp; Start, Health- und Sicherheitscheck; erst dann $Committed und Entsperren")
check(upd.index("$NewStarted = $true", i_started) + 60 > i_start, "$NewStarted wird unmittelbar vor dem Start der neuen Version gesetzt")
catch = upd[i_catch:]
i_ccommit = catch.index("if ($Committed)")
check(i_ccommit < catch.index("Rollback wird ausgefuehrt") and "return" in catch[i_ccommit:catch.index("Rollback wird ausgefuehrt")],
      "nach $Committed kein Rollback (keine Daten nach dem Entsperren werden verworfen)")
restore = catch.index("maschinenplanung_vor_update.sqlite3') -Destination")
check("if ($FinalBackup -and $NewStarted)" in catch[:restore] and catch[:restore].rindex("if ($FinalBackup -and $NewStarted)") > catch.index("Rollback wird ausgefuehrt"),
      "Datenbank wird nur zurueckgespielt, wenn die neue Version gestartet war (Migration moeglich)")
check("fehlversuch_V" in catch[:restore], "ersetzte Datenbank bleibt in update_backups\\fehlversuch_V* erhalten")
i_restored = catch.index("if ($restored)")
check(catch.rindex("Remove-Item -LiteralPath $Maintenance") > i_restored and catch.count("Remove-Item -LiteralPath $Maintenance") == 2, "Rollback entsperrt erst nach erfolgreichem Health-Check des alten Stands")
check(upd.count("Remove-Item -LiteralPath $Maintenance") == 3, "Sperre wird nur an drei Stellen entfernt (Erfolg, Nacharbeit nach $Committed, Rollback ok)")
check("-not $Rueckstufen" in upd and "$IsDowngrade" in upd and "Get-MPPackageVersion $OldBase" in upd[:upd.index("$IsDowngrade")],
      "Downgrade nur mit -Rueckstufen; Pruefung auch gegen die installierte Version (Server gestoppt)")
check("Join-Path $ScriptDir 'Backup_Datenbank.py'" in upd and "Join-Path $NewSource 'Backup_Datenbank.py'" not in upd,
      "Backups immer mit dem Code des ausfuehrenden (neuesten) Updaters")
check("-Paket" in upd and "MP-UPD-007" in upd and "Get-MPAppFileList $NewSource" in upd, "-Paket (aelteres Paket) nur mit -Rueckstufen, Dateiliste aus dem Paket")
gh = (ROOT / "Update_von_GitHub.ps1").read_text(encoding="utf-8-sig")
check("[switch]$Rueckstufen" in gh and "-Paket `$pkg -Rueckstufen" in gh, "Update_von_GitHub.ps1 -Rueckstufen nutzt den installierten Updater mit -Paket")
codes = (ROOT / "FEHLERCODES.txt").read_text(encoding="utf-8")
check(re.search(r"^MP-UPD-007\s", codes, re.M) is not None, "FEHLERCODES.txt nennt MP-UPD-007")
doc = (ROOT / "docs" / "ROLLBACK.md").read_text(encoding="utf-8")
check(all(k in doc for k in ("-Rueckstufen", "MP-UPD-003", "Wartungssperre", "Update_von_GitHub.ps1 -Tag")), "docs/ROLLBACK.md beschreibt Sperre, Rueckstufung und Befehl")

print(f"\n{sum(RESULTS)}/{len(RESULTS)} bestanden")
sys.exit(0 if all(RESULTS) else 1)

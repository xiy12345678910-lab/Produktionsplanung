#!/usr/bin/env python3
"""V12.21.0: HTTPS im LAN (Firmen-CA, Serverzertifikat, TLS-Server, Secure-Cookie, Origin).

Aufruf:  python tests/test_tls.py
Arbeitet nur in Temp-Ordnern (MP_CONFIG_DIR), nie mit Live-Daten.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import warnings
warnings.simplefilter("ignore", DeprecationWarning)
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
TMP = Path(tempfile.mkdtemp(prefix="mp-tls-"))
os.environ["MP_CONFIG_DIR"] = str(TMP / "config")
import server  # noqa: E402

RESULTS: list[bool] = []


def check(cond, label):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + label)


server.DATA_DIR = TMP / "data"
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = server.ipaddress.ip_network("127.0.0.0/8")
server.Handler.log_message = lambda *a, **k: None
server.init_db(seed="werbetechnik")
server.create_or_reset_admin("admin", "Tls-Test-Passwort-1")

# Ohne Zertifikat bleibt alles wie bisher (HTTP).
check(server.tls_context() is None, "Ohne config/tls läuft der Server weiter per HTTP")

# CLI legt CA und Serverzertifikat an und beendet sich (kein --allowed-subnet nötig).
env = {**os.environ, "MP_CONFIG_DIR": str(TMP / "config")}
cli = subprocess.run([sys.executable, "-I", str(SRC / "server.py"), "--tls-einrichten", "--host", "127.0.0.1", "--tls-name", "mp.firma.local"],
                     env=env, capture_output=True, text=True, timeout=120)
check(cli.returncode == 0 and "HTTPS eingerichtet" in cli.stdout and "neu angelegt" in cli.stdout, "--tls-einrichten legt Firmen-CA und Serverzertifikat an: " + (cli.stdout + cli.stderr).strip()[:300])
tls = TMP / "config" / "tls"
check(all((tls / n).exists() for n in ("firmen-ca.crt", "firmen-ca.key", "server.crt", "server.key")), "config/tls enthält CA und Serverzertifikat mit Schlüsseln")
if os.name != "nt":
    check(all(((tls / n).stat().st_mode & 0o077) == 0 for n in ("firmen-ca.key", "server.key")), "Private Schlüssel nur für den Besitzer lesbar")
ca_before = (tls / "firmen-ca.crt").read_bytes()
cert_before = (tls / "server.crt").read_bytes()
cli2 = subprocess.run([sys.executable, "-I", str(SRC / "server.py"), "--tls-einrichten", "--host", "127.0.0.1"],
                      env=env, capture_output=True, text=True, timeout=120)
check(cli2.returncode == 0 and "weiterverwendet" in cli2.stdout and (tls / "firmen-ca.crt").read_bytes() == ca_before and (tls / "server.crt").read_bytes() != cert_before,
      "Erneuerung behält die Firmen-CA (kein neuer Import nötig) und stellt ein neues Serverzertifikat aus")
bad = subprocess.run([sys.executable, "-I", str(SRC / "server.py"), "--tls-einrichten", "--host", "kein-ip"], env=env, capture_output=True, text=True, timeout=60)
check(bad.returncode == 2 and "FEHLER" in bad.stderr, "Ungültige Server-IP wird abgewiesen")

ctx = server.tls_context()
check(ctx is not None, "Mit Zertifikat liefert tls_context() einen TLS-Kontext")
httpd = server.MPHTTPServer(("127.0.0.1", 0), server.Handler, ssl_context=ctx)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()

client = ssl.create_default_context(cafile=str(tls / "firmen-ca.crt"))


def req(method, path, body=None, host="127.0.0.1", origin=None, cookie=""):
    conn = http.client.HTTPSConnection(host, port, context=client, timeout=10)
    headers = {"Content-Type": "application/json", "X-MP-Client-Version": server.APP_VERSION, "Host": f"127.0.0.1:{port}"}
    if origin:
        headers["Origin"] = origin
    if cookie:
        headers["Cookie"] = cookie
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    r = conn.getresponse()
    data = r.read()
    return r.status, r.getheader("Set-Cookie") or "", data, conn.sock.version() if conn.sock else ""


try:
    status, _, data, _ = req("GET", "/api/health")
    check(status == 200 and json.loads(data)["ok"], "HTTPS-Health antwortet; Zertifikat wird gegen die Firmen-CA inkl. IP-Name geprüft")
    raw = socket.create_connection(("127.0.0.1", port), timeout=5)
    with client.wrap_socket(raw, server_hostname="127.0.0.1") as s:
        check(s.version() in {"TLSv1.2", "TLSv1.3"}, f"TLS-Version mindestens 1.2 ({s.version()})")
    old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    old.check_hostname, old.verify_mode = False, ssl.CERT_NONE
    try:
        old.maximum_version = ssl.TLSVersion.TLSv1_1
        old.minimum_version = ssl.TLSVersion.TLSv1
        with old.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=5)) as s:
            refused = False
    except (ssl.SSLError, OSError, ValueError):
        refused = True
    check(refused, "TLS 1.0/1.1 wird abgelehnt")
    try:
        with ssl.create_default_context().wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=5), server_hostname="127.0.0.1"):
            untrusted_ok = True
    except ssl.SSLCertVerificationError:
        untrusted_ok = False
    check(not untrusted_ok, "Ohne importierte Firmen-CA ist das Zertifikat nicht vertrauenswürdig (keine stille Annahme)")
    plain = socket.create_connection(("127.0.0.1", port), timeout=5)
    plain.sendall(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    try:
        answer = plain.recv(100)
    except OSError:
        answer = b""
    plain.close()
    check(b"200 OK" not in answer, "Unverschlüsseltes HTTP erhält auf dem HTTPS-Port keine Daten")
    status, _, data, _ = req("GET", "/api/health")
    check(status == 200, "Server läuft nach fehlerhaften Handshakes weiter")
    origin = f"https://127.0.0.1:{port}"
    status, cookie, data, _ = req("POST", "/api/login", {"username": "admin", "password": "Tls-Test-Passwort-1"}, origin=origin)
    check(status == 200 and "; Secure" in cookie and "HttpOnly" in cookie, "Login per HTTPS setzt das Sitzungscookie mit Secure: " + cookie.split(";", 1)[-1])
    session = cookie.split(";")[0]
    status, _, _, _ = req("GET", "/api/state", cookie=session)
    check(status == 200, "Sitzung funktioniert über HTTPS")
    status, _, _, _ = req("POST", "/api/logout", {}, origin=f"http://127.0.0.1:{port}", cookie=session)
    check(status == 403, "Schreibzugriff mit http-Origin wird bei HTTPS-Betrieb abgewiesen")
    status, cookie, _, _ = req("POST", "/api/logout", {}, origin=origin, cookie=session)
    check(status == 200 and "; Secure" in cookie and "Max-Age=0" in cookie, "Logout löscht das Secure-Cookie")
finally:
    httpd.shutdown()
    httpd.server_close()

# Automatische Erneuerung: neue LAN-IP bzw. bald ablaufendes Zertifikat (CA bleibt).
check(server.tls_renew_reason("127.0.0.1") == "", "Gültiges Zertifikat für die Server-IP braucht keine Erneuerung")
check("fehlt im Zertifikat" in server.tls_renew_reason("127.0.0.2"), "Geänderte LAN-IP wird erkannt")
old_days = server.TLS_SERVER_DAYS
server.TLS_SERVER_DAYS = 10
server.tls_setup("127.0.0.1")
check("läuft am" in server.tls_renew_reason("127.0.0.1"), "Ablauf in weniger als 30 Tagen wird erkannt")
server.TLS_SERVER_DAYS = old_days
server.tls_setup("127.0.0.2")
check(server.tls_renew_reason("127.0.0.2") == "" and (tls / "firmen-ca.crt").read_bytes() == ca_before, "Erneuerung für neue IP behält die Firmen-CA")

# Beschädigtes Zertifikat: Start bricht mit Fehlercode ab statt still auf HTTP zu fallen.
(tls / "server.crt").write_text("kaputt", encoding="ascii")
try:
    server.tls_context()
    broken = False
except (OSError, ValueError):
    broken = True
check(broken, "Beschädigtes Zertifikat wird erkannt (Server startet nicht unverschlüsselt)")

# #52: HTTPS als Standard bei Neuinstallation (statische Pruefung der Windows-Skripte; echter Lauf in tests/ci_windows_deploy.ps1).
def ps(name):
    return (SRC / name).read_text(encoding="utf-8-sig")


common, setup, https_ps = ps("MP_Common.ps1"), ps("Setup_Windows.ps1"), ps("HTTPS_Einrichten.ps1")
check("function Install-MPTls(" in common and common.count("'--tls-einrichten'") == 1, "MP_Common.ps1: Install-MPTls ist die einzige Stelle mit --tls-einrichten")
check("Install-MPTls $Base $PythonExe" in https_ps and "--tls-einrichten" not in https_ps and "X509Store" not in https_ps, "HTTPS_Einrichten.ps1 nutzt Install-MPTls (keine zweite Implementierung)")
check("param([switch]$OhneHttps)" in setup and "elseif ($OhneHttps)" in setup, "Setup_Windows.ps1: Opt-out -OhneHttps")
i_tls = setup.index("Install-MPTls $Base")
check(setup.index("$IsNewInstall = ") < setup.index("Copy-MPAppFile") < i_tls and "elseif (-not $IsNewInstall)" in setup[:i_tls],
      "Setup_Windows.ps1: Neuinstallation wird vor dem Kopieren erkannt; bestehende Installation bekommt kein TLS")
check(all(m in setup[:setup.index("$IsNewInstall = ")] for m in ("LAN_CONFIG.json", "Get-ScheduledTask", "backups")), "Setup_Windows.ps1: Bestand = LAN_CONFIG.json, Servertask oder Backups")
check(i_tls < setup.index("Start-ScheduledTask"), "Setup_Windows.ps1: HTTPS wird vor dem ersten Serverstart eingerichtet")
check("$TlsBefore = (Test-Path -LiteralPath (Get-MPTlsDir $Base))" in setup and setup.index("if ($TlsBefore)") < i_tls,
      "Setup_Windows.ps1: vorhandenes config\\tls (z. B. importierte Firmen-CA) bleibt unangetastet")
fallback = setup[i_tls:setup.index("Remove-MPLegacyTasks")]
check("catch" in fallback and "Remove-MPTls $Base" in fallback and "MP-TLS-002" in fallback, "Setup_Windows.ps1: Fehler bei der Einrichtung -> config\\tls entfernen, Warnung MP-TLS-002, weiter mit HTTP")
check("MP-TLS-003" in setup and setup.count("Remove-MPTls $Base") == 2 and "$TlsCreated" in setup, "Setup_Windows.ps1: kein HTTPS-Health -> Rueckfall auf HTTP (MP-TLS-003), nur fuer eben angelegtes TLS")
inst = ps("INSTALLIEREN_ALS_ADMIN.ps1")
check("param([switch]$OhneHttps)" in inst and "-OhneHttps:$OhneHttps" in inst, "INSTALLIEREN_ALS_ADMIN.ps1 reicht -OhneHttps durch")
# Updates aendern den TLS-Zustand nie (kein Anlegen, kein Entfernen).
upd = {n: (SRC / n).read_text(encoding="utf-8-sig") for n in ("UPDATE_LIVE.ps1", "Update_von_GitHub.ps1", "app_updates.py")}
check(not any(x in text for text in upd.values() for x in ("--tls-einrichten", "Install-MPTls", "Remove-MPTls", "tls_setup", "HTTPS_Einrichten")),
      "Updatepfad (UPDATE_LIVE.ps1, Update_von_GitHub.ps1, app_updates.py) legt kein TLS an und entfernt keins")
codes = (SRC / "FEHLERCODES.txt").read_text(encoding="utf-8")
check("MP-TLS-002" in codes and "MP-TLS-003" in codes, "FEHLERCODES.txt nennt MP-TLS-002 und MP-TLS-003")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{sum(RESULTS)}/{len(RESULTS)} bestanden")
sys.exit(0 if all(RESULTS) else 1)

#!/usr/bin/env python3
"""Hotfix V12.17.2: Client-Abbruch (WinError 10053/10054, EPIPE) ist kein MP-SRV-500.

Aufruf:  python tests/test_client_abort.py
"""
from __future__ import annotations

import http.client
import io
import json
import socket
import struct
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
import server  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    RESULTS.append((bool(cond), label))
    print(("PASS " if cond else "FAIL ") + label)


tmp = Path(tempfile.mkdtemp(prefix="mp-abort-"))
server.DATA_DIR = tmp
server.DB_PATH = tmp / "maschinenplanung.sqlite3"
server.init_db(seed="werbetechnik")
server.ALLOWED_NETWORK = server.ipaddress.ip_network("127.0.0.0/8")
server.Handler.log_message = lambda *a, **k: None
httpd = server.MPHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()

# Alle stderr-Zeilen des Servers mitschneiden (dort steht MP-SRV-500).
buf = io.StringIO()
real_stderr = sys.stderr
sys.stderr = buf


def health_ok() -> bool:
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    c.putrequest("GET", "/api/health", skip_host=True)
    c.putheader("Host", f"127.0.0.1:{PORT}")
    c.endheaders()
    r = c.getresponse(); r.read(); c.close()
    return r.status == 200


# 1) Echte Verbindungen hart schliessen (RST), waehrend der Server antwortet bzw. wartet.
for path in ("/api/revision?since=999999&wait=1500&nsig=0%3A0&crev=0", "/api/health", "/api/state"):
    for _ in range(5):
        s = socket.create_connection(("127.0.0.1", PORT))
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\n\r\n".encode())
        s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        s.close()
time.sleep(2.2)  # Long-Poll-Wartezeit verstreichen lassen, damit der Server auf die tote Verbindung schreibt
check(health_ok(), "Server antwortet nach hartem Verbindungsabbruch weiter")

# 2) Jede Abbruch-Ausnahme direkt im Handler: keine 500-Antwort, kein MP-SRV-500.
for exc in (ConnectionAbortedError(10053, "Eine bestehende Verbindung wurde softwaregesteuert\r\ndurch den Hostcomputer abgebrochen"),
            ConnectionResetError(10054, "reset"), BrokenPipeError(32, "pipe")):
    sent = []

    class H(server.Handler):
        def __init__(self):  # ohne Socket
            self.command = "GET"; self.path = "/api/revision?since=1"; self.close_connection = False

        def request_origin_ok(self, write):
            return True

        def json_response(self, *a, **k):
            sent.append(a)

    def boom():
        raise exc

    h = H()
    try:
        h._guarded(boom, False)
        raised = False
    except Exception:
        raised = True
    check(not raised and not sent and h.close_connection, f"{type(exc).__name__}: kein Folgefehler, keine 500-Antwort")

# 3) Echte Fehler bleiben 500 mit MP-SRV-500, Meldung einzeilig.
sent = []
class H2(server.Handler):
    def __init__(self):
        self.command = "GET"; self.path = "/api/x"; self.close_connection = False
    def request_origin_ok(self, write):
        return True
    def json_response(self, code, body, cookie=None):
        sent.append(code)
H2()._guarded(lambda: (_ for _ in ()).throw(ValueError("zeile1\r\nzeile2")), False)
check(sent == [500], "echter Fehler liefert weiter 500")

sys.stderr = real_stderr
log = buf.getvalue()
check("ConnectionAbortedError" not in log and "ConnectionResetError" not in log and "BrokenPipe" not in log,
      "kein Abbruch-Eintrag im Log")
check(log.count("MP-SRV-500") == 1 and "ValueError: zeile1 zeile2" in log, "nur der echte Fehler steht als MP-SRV-500 im Log (einzeilig)")

httpd.shutdown()
failed = [x for x in RESULTS if not x[0]]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

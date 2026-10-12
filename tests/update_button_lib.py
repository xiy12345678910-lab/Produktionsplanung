#!/usr/bin/env python3
"""#84: Bausteine fuer den Ende-zu-Ende-Test des Admin-Buttons "Update installieren".

Genutzt von
  - tests/ci_windows_update_button.ps1  (echte Windows-Installation, echter Worker und UPDATE_LIVE.ps1)
  - tests/test_update_button.py         (Linux/alle Plattformen: echter server.py-Prozess bis zum Installer)

Nur Standardbibliothek. Kommandozeile (Ausgabe PASS/FAIL je Pruefung, Exitcode 0 nur wenn alles bestanden ist):
  python tests/update_button_lib.py lower-version --dir D --version V
  python tests/update_button_lib.py build   --root R --output O --commit SHA
  python tests/update_button_lib.py serve   --dir O --port-file F [--log L]          (GitHub-Release-API-Nachbildung)
  python tests/update_button_lib.py seed    --url U --admin-password P [--cafile C]
  python tests/update_button_lib.py trigger --url U --admin-password P --expect-version V --out job.json [--cafile C]
  python tests/update_button_lib.py wait    --url U --admin-password P --expect-version V --job-id J [--timeout S] [--cafile C]
  python tests/update_button_lib.py verify  --url U --admin-password P --expect-version V --job-id J [--mock-log L] [--cafile C]

Die Nachbildung (serve) liefert genau die Antworten, die app_updates.release_info von api.github.com erwartet
(releases/latest, commits/<tag>, compare/main...<sha>, releases/assets/<id>). Der Updater spricht sie nur an, wenn
server.py mit MP_UPDATE_TEST_API=http://127.0.0.1:<port>/repos/ gestartet wurde (siehe app_updates.api_root).
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Die "vorher angelegte Aenderung", die das Update ueberstehen muss.
TEST_USER = "update_knopf"
TEST_USER_PASSWORD = "Update-Knopf-Test-1"
MARKER_EMPLOYEE = {"id": "e_updknopf", "name": "Update Knopf Pruefling", "departmentId": "", "active": True, "skills": []}
MARKER_ACTION = "Knopftest Daten"  # bewusst nicht "Update..." (Diagnose listet Audit-Aktionen LIKE 'Update%')
MANIFEST_ID, PACKAGE_ID = 1, 2


# --------------------------------------------------------------------------------------------
# Paketvorbereitung
# --------------------------------------------------------------------------------------------


def lower_version(folder: Path, new: str) -> str:
    """Setzt APP_VERSION (server.py) und CLIENT_VERSION (index.html) auf eine kuenstlich niedrigere Version.
    Genau die beiden Stellen, die Updater (unpack_package) und Health-Check auswerten. Rueckgabe: alte Version."""
    srv, html = folder / "server.py", folder / "index.html"
    s, h = _read_exact(srv), _read_exact(html)
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', s, re.M)
    if not m or not re.fullmatch(r"\d+\.\d+\.\d+", new):
        raise ValueError("APP_VERSION nicht gefunden oder Zielversion ungueltig")
    old = m.group(1)
    if tuple(map(int, new.split("."))) >= tuple(map(int, old.split("."))):
        raise ValueError(f"{new} ist nicht niedriger als {old}")
    s2 = re.sub(r'^(APP_VERSION\s*=\s*)"[^"]+"', lambda x: x.group(1) + '"' + new + '"', s, count=1, flags=re.M)
    h2, n = re.subn(r"(CLIENT_VERSION\s*=\s*)'[^']+'", lambda x: x.group(1) + "'" + new + "'", h, count=1)
    if n != 1:
        raise ValueError("CLIENT_VERSION nicht gefunden")
    _write_exact(srv, s2)
    _write_exact(html, h2)
    return old


def _read_exact(path: Path) -> str:
    """Text mit unveraenderten Zeilenenden (\r\n bleibt) und ggf. BOM als erstes Zeichen."""
    return path.read_bytes().decode("utf-8")


def _write_exact(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def lower_of(version: str) -> str:
    """Naechst niedrigere Version fuer den Test: X.Y.Z -> X.Y.(Z-1), X.Y.0 -> X.(Y-1).99."""
    a, b, c = map(int, version.split("."))
    if c > 0:
        return f"{a}.{b}.{c - 1}"
    if b > 0:
        return f"{a}.{b - 1}.99"
    raise ValueError("Version zu niedrig fuer den Test")


def build_package(root: Path, output: Path, commit: str) -> dict:
    """Baut das Release-Paket mit genau dem Code von tools/build_release.py (build + verify)."""
    sys.path.insert(0, str(ROOT / "tools"))
    import build_release  # noqa: E402
    build_release.build(root, output, commit)
    return build_release.verify(output, commit)


# --------------------------------------------------------------------------------------------
# GitHub-Release-API-Nachbildung
# --------------------------------------------------------------------------------------------


class MockGitHub:
    def __init__(self, release_dir: Path, log: Path | None = None):
        self.dir = Path(release_dir)
        self.manifest = json.loads((self.dir / "update-manifest.json").read_text(encoding="utf-8"))
        self.version = self.manifest["version"]
        self.zip_name = f"produktionsplanung-v{self.version}.zip"
        self.log = Path(log) if log else None
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        self.httpd = None

    def route(self, path: str):
        m = re.fullmatch(r"/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(.+)", path)
        if not m:
            return 404, "application/json", b'{"message":"Not Found"}'
        rest, tag, sha = m.group(1), "v" + self.version, self.manifest["commit"]
        if rest == "releases/latest":
            body = {"tag_name": tag, "draft": False, "prerelease": False, "body": f"Testrelease {tag} (#84, lokale Nachbildung)",
                    "assets": [{"name": "update-manifest.json", "id": MANIFEST_ID}, {"name": self.zip_name, "id": PACKAGE_ID}]}
            return 200, "application/json", json.dumps(body).encode()
        if rest == "commits/" + tag:
            return 200, "application/json", json.dumps({"sha": sha}).encode()
        if rest == "compare/main..." + sha:
            return 200, "application/json", b'{"status":"identical"}'
        if rest == f"releases/assets/{MANIFEST_ID}":
            return 200, "application/octet-stream", (self.dir / "update-manifest.json").read_bytes()
        if rest == f"releases/assets/{PACKAGE_ID}":
            return 200, "application/octet-stream", (self.dir / self.zip_name).read_bytes()
        return 404, "application/json", b'{"message":"Not Found"}'

    def record(self, entry: dict):
        with self.lock:
            self.requests.append(entry)
            if self.log:
                with self.log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")

    def start(self, port: int = 0) -> int:
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                status, ctype, raw = mock.route(self.path.split("?")[0])
                mock.record({"path": self.path, "status": status, "auth": bool(self.headers.get("Authorization")),
                             "accept": self.headers.get("Accept", ""), "client": self.client_address[0]})
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        # Nur Loopback: die Nachbildung ist von keinem anderen Rechner erreichbar.
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.httpd.server_address[1]

    @property
    def api(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/repos/"

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


# --------------------------------------------------------------------------------------------
# HTTP-Client wie der Browser (Cookie-Sitzung, X-MP-Client-Version), HTTP oder HTTPS mit Firmen-CA
# --------------------------------------------------------------------------------------------


class Client:
    def __init__(self, url: str, cafile: str | None = None):
        m = re.fullmatch(r"(https?)://([^:/]+):(\d+)/?", url)
        if not m:
            raise ValueError(f"URL ungueltig: {url}")
        self.scheme, self.host, self.port = m.group(1), m.group(2), int(m.group(3))
        self.ctx = ssl.create_default_context(cafile=cafile) if self.scheme == "https" else None
        self.cookie = ""
        self.version = ""

    def call(self, method: str, path: str, body=None, timeout: float = 30):
        if self.scheme == "https":
            c = http.client.HTTPSConnection(self.host, self.port, timeout=timeout, context=self.ctx)
        else:
            c = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        h = {"Content-Type": "application/json", "X-MP-Client-Version": self.version or "?",
             "Origin": f"{self.scheme}://{self.host}:{self.port}"}
        if self.cookie:
            h["Cookie"] = self.cookie
        try:
            c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
            r = c.getresponse()
            raw = r.read()
            sc = (r.getheader("Set-Cookie") or "").split(";")[0]
            if sc.startswith("mp_session=") and sc != "mp_session=":
                self.cookie = sc
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                data = raw
            return r.status, data
        finally:
            c.close()

    def health(self, timeout: float = 10) -> str:
        s, d = self.call("GET", "/api/health", timeout=timeout)
        self.version = str(d.get("version", "")) if s == 200 and isinstance(d, dict) else ""
        return self.version

    def login(self, user: str, pw: str) -> int:
        self.cookie = ""
        self.health()
        return self.call("POST", "/api/login", {"username": user, "password": pw})[0]


class Checks:
    def __init__(self):
        self.failed = 0

    def __call__(self, ok, label: str) -> bool:
        print(("PASS " if ok else "FAIL ") + label, flush=True)
        if not ok:
            self.failed += 1
        return bool(ok)


# --------------------------------------------------------------------------------------------
# Ablauf: Aenderung anlegen, Update ueber den Button ausloesen, warten, pruefen
# --------------------------------------------------------------------------------------------


def seed(url: str, admin_pw: str, cafile: str | None, check: Checks) -> None:
    adm = Client(url, cafile)
    if not check(adm.login("admin", admin_pw) == 200, "Admin-Login (POST /api/login mit X-MP-Client-Version)"):
        return
    s, d = adm.call("POST", "/api/users", {"username": TEST_USER, "password": TEST_USER_PASSWORD, "role": "viewer", "departmentId": ""})
    check(s == 201, f"Benutzer {TEST_USER} angelegt ({s})")
    s, st = adm.call("GET", "/api/state")
    if not check(s == 200 and isinstance(st, dict), f"Datenstand lesen ({s})"):
        return
    data = st["data"]
    if not data.get("machines"):
        # Neuinstallation ohne Bereiche (ab V12.17.0): einen Bereich mit Maschine ergaenzen (Speichern verlangt eine Maschine).
        data.setdefault("departments", []).append({"id": "upd", "name": "Update-Test", "planningType": "MACHINE", "active": True})
        data["machines"] = [{"id": "m_upd", "name": "Maschine Update-Test", "departmentId": "upd", "setupMinutes": 0, "start": "2026-10-12T06:30",
                             "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 1}]
    data.setdefault("employees", []).append(dict(MARKER_EMPLOYEE, departmentId=data["machines"][0].get("departmentId", "")))
    s, d = adm.call("PUT", "/api/state", {"revision": st["revision"], "data": data, "action": MARKER_ACTION, "detail": ""})
    check(s == 200, f"Aenderung gespeichert: Mitarbeiter {MARKER_EMPLOYEE['name']} ({s} {d if s != 200 else ''})")


def poll_updates(adm: Client, expect: str, timeout: float = 90) -> dict:
    """GET /api/updates startet beim ersten Aufruf die Pruefung im Hintergrund; warten bis Ergebnis oder Fehler."""
    end = time.time() + timeout
    last: dict = {}
    while time.time() < end:
        s, d = adm.call("GET", "/api/updates")
        if s == 200 and isinstance(d, dict):
            last = d
            if d.get("available") or d.get("checkError"):
                return d
        time.sleep(1)
    return last


def trigger(url: str, admin_pw: str, expect: str, out: Path | None, cafile: str | None, check: Checks,
            expect_install: int = 202) -> dict:
    adm = Client(url, cafile)
    if not check(adm.login("admin", admin_pw) == 200, "Admin-Login vor dem Update"):
        return {}
    d = poll_updates(adm, expect)
    print("    /api/updates: " + json.dumps({k: d.get(k) for k in ("currentVersion", "available", "version", "checkError", "installSupported")}, ensure_ascii=False), flush=True)
    check(d.get("available") is True and d.get("version") == expect, f"/api/updates findet die neue Version V{expect} (installiert V{d.get('currentVersion')})")
    check(not d.get("checkError"), f"keine Fehlermeldung der Updatepruefung ({d.get('checkError') or '-'})")
    s, r = adm.call("POST", "/api/updates/install", {})
    print(f"    POST /api/updates/install -> {s} {json.dumps(r, ensure_ascii=False)[:300]}", flush=True)
    job = (r or {}).get("job") or {} if isinstance(r, dict) else {}
    check(s == expect_install, f"POST /api/updates/install -> {s} (erwartet {expect_install})")
    if expect_install == 202:
        check(re.fullmatch(r"[0-9a-f]{24}", str(job.get("jobId") or "")) and job.get("version") == expect, f"Auftrag {job.get('jobId')} fuer V{job.get('version')} gestartet")
    if out:
        Path(out).write_text(json.dumps(job), encoding="utf-8")
    return job


def wait(url: str, admin_pw: str, expect: str, job_id: str, timeout: float, cafile: str | None, check: Checks) -> dict:
    """Wartet, bis der Server die neue Version meldet UND der Auftrag abgeschlossen (oder gescheitert) ist.
    Waehrend Stopp/Neustart antwortet der Server nicht; das ist erwartet."""
    end = time.time() + timeout
    stages, job = [], {}
    c = Client(url, cafile)
    while time.time() < end:
        try:
            v = c.health(timeout=5)
            if v and not c.cookie:
                c.login("admin", admin_pw)
            if v and c.cookie:
                s, d = c.call("GET", "/api/updates")
                if s == 401:
                    c.cookie = ""
                    continue
                job = (d.get("job") or {}) if s == 200 and isinstance(d, dict) else {}
                if job.get("stage") and (not stages or stages[-1] != job["stage"]):
                    stages.append(job["stage"])
                    print(f"    Stand: V{v}, Auftrag {job.get('jobId')} Phase {job['stage']}", flush=True)
                if job.get("jobId") == job_id and job.get("stage") in ("complete", "failed") and (v == expect or job["stage"] == "failed"):
                    break
        except (OSError, http.client.HTTPException, ssl.SSLError):
            pass
        time.sleep(3)
    check(job.get("jobId") == job_id and job.get("stage") == "complete", f"Auftrag abgeschlossen (Phase {job.get('stage')}, Fehler {job.get('error') or '-'}, Verlauf {' > '.join(stages)})")
    return job


def verify(url: str, admin_pw: str, expect: str, job_id: str, mock_log: Path | None, cafile: str | None, check: Checks) -> None:
    adm = Client(url, cafile)
    check(adm.health() == expect, f"/api/health meldet V{expect} ({adm.version})")
    if not check(adm.login("admin", admin_pw) == 200, "Admin-Login nach dem Update"):
        return
    u = Client(url, cafile)
    check(u.login(TEST_USER, TEST_USER_PASSWORD) == 200, f"vorher angelegter Benutzer {TEST_USER} meldet sich an")
    s, st = adm.call("GET", "/api/state")
    emps = (st.get("data") or {}).get("employees") or [] if s == 200 and isinstance(st, dict) else []
    check(any(e.get("id") == MARKER_EMPLOYEE["id"] and e.get("name") == MARKER_EMPLOYEE["name"] for e in emps), "vorher gespeicherte Aenderung (Mitarbeiter) ist erhalten")
    s, d = adm.call("GET", "/api/updates")
    job = (d.get("job") or {}) if s == 200 and isinstance(d, dict) else {}
    check(s == 200 and d.get("currentVersion") == expect and not d.get("available"), f"/api/updates: V{d.get('currentVersion')}, kein weiteres Update angeboten")
    check(job.get("jobId") == job_id and job.get("stage") == "complete" and not job.get("rolledBack"), f"Auftragsstatus complete ohne Rollback ({job})")
    s, r = adm.call("POST", "/api/updates/install", {})
    check(s == 409, f"erneutes Installieren ohne neueres Release abgewiesen ({s})")
    s, diag = adm.call("GET", "/api/diagnostics")
    ups = diag.get("updates") or [] if s == 200 and isinstance(diag, dict) else []
    actions = [(x.get("action"), x.get("version")) for x in ups]
    print("    Audit (neueste zuerst): " + "; ".join(f"{a} V{v}" for a, v in actions), flush=True)
    check(ups and ups[0].get("action") == "Update: complete" and ups[0].get("version") == expect, "Audit: letzter Update-Eintrag ist 'Update: complete' der neuen Version")
    check(("Update gestartet", expect) in actions, "Audit: 'Update gestartet' vorhanden")
    check(not any(a == "Update fehlgeschlagen" for a, _ in actions), "Audit: kein 'Update fehlgeschlagen'")
    if mock_log:
        reqs = [json.loads(line) for line in Path(mock_log).read_text(encoding="utf-8").splitlines() if line.strip()]
        paths = [r["path"] for r in reqs]
        check(any(p.endswith(f"/releases/assets/{PACKAGE_ID}") for p in paths), f"Paket wurde von der Testquelle geladen ({len(reqs)} Anfragen)")
        check(all(r["status"] == 200 for r in reqs), "Testquelle: alle Anfragen beantwortet (200)")
        check(not any(r["auth"] for r in reqs), "Testquelle bekam nie einen Authorization-Header")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("lower-version"); p.add_argument("--dir", required=True); p.add_argument("--version")
    p = sub.add_parser("build"); p.add_argument("--root", required=True); p.add_argument("--output", required=True); p.add_argument("--commit", required=True)
    p = sub.add_parser("serve"); p.add_argument("--dir", required=True); p.add_argument("--port-file", required=True); p.add_argument("--log")
    for name in ("seed", "trigger", "wait", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--url", required=True); p.add_argument("--admin-password", required=True); p.add_argument("--cafile")
        if name != "seed":
            p.add_argument("--expect-version", required=True)
        if name == "trigger":
            p.add_argument("--out")
        if name in ("wait", "verify"):
            p.add_argument("--job-id", required=True)
        if name == "wait":
            p.add_argument("--timeout", type=float, default=1200)
        if name == "verify":
            p.add_argument("--mock-log")
    a = ap.parse_args(argv)
    check = Checks()
    if a.cmd == "lower-version":
        folder = Path(a.dir)
        cur = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', (folder / "server.py").read_text(encoding="utf-8-sig"), re.M).group(1)
        target = a.version or lower_of(cur)
        lower_version(folder, target)
        print(target)
        return 0
    if a.cmd == "build":
        m = build_package(Path(a.root), Path(a.output), a.commit)
        print(f"Testrelease V{m['version']} gebaut: {len(m['files'])} Dateien, Commit {m['commit'][:12]}")
        return 0
    if a.cmd == "serve":
        mock = MockGitHub(Path(a.dir), Path(a.log) if a.log else None)
        port = mock.start(0)
        tmp = Path(a.port_file + ".tmp")
        tmp.write_text(str(port), encoding="ascii")
        tmp.replace(a.port_file)
        print(f"GitHub-Nachbildung fuer V{mock.version} auf http://127.0.0.1:{port}/repos/", flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0
    if a.cmd == "seed":
        seed(a.url, a.admin_password, a.cafile, check)
    elif a.cmd == "trigger":
        trigger(a.url, a.admin_password, a.expect_version, Path(a.out) if a.out else None, a.cafile, check)
    elif a.cmd == "wait":
        wait(a.url, a.admin_password, a.expect_version, a.job_id, a.timeout, a.cafile, check)
    elif a.cmd == "verify":
        verify(a.url, a.admin_password, a.expect_version, a.job_id, Path(a.mock_log) if a.mock_log else None, a.cafile, check)
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""V12.15.0: /api/config (Rechte, Validierung, Logo, Konflikt, Long-Poll) über echtes HTTP, ohne Browser.

Aufruf:  python tests/test_config_api.py
Arbeitet nur in Temp-Ordnern (MP_CONFIG_DIR), nie mit Live-Daten.
"""
from __future__ import annotations

import base64
import http.client
import json
import os
import struct
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
tmp = Path(tempfile.mkdtemp(prefix="mp-cfgapi-"))
os.environ["MP_CONFIG_DIR"] = str(tmp / "config")
import server  # noqa: E402

RESULTS: list[bool] = []


def check(cond, label):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + label)


server.DATA_DIR = tmp / "data"
server.DATA_DIR.mkdir()
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.PBKDF2_ITERS = 1000
server.ALLOWED_NETWORK = server.ipaddress.ip_network("127.0.0.0/8")
server.init_db()
server.load_config()
ROLES = [("adm", "admin", ""), ("pm1", "project_management", ""), ("gf1", "gf", ""), ("lead", "department_lead", "cnc"),
         ("view", "viewer", ""), ("sales1", "sales", "")]
with server.db_session() as con:
    for n, r, d in ROLES:
        salt, digest = server.hash_password("Test-Passwort-1")
        con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)",
                    (n, salt, digest, r, d, server.now_iso(), server.now_iso()))
httpd = server.MPHTTPServer(("127.0.0.1", 0), server.Handler)
server.Handler.log_message = lambda *a, **k: None
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def raw(method, path, body=None, cookie=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h = {"Content-Type": "application/json", "X-MP-Client-Version": server.APP_VERSION}
    if cookie:
        h["Cookie"] = cookie
    h.update(headers or {})
    c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
    r = c.getresponse()
    data = r.read()
    out = (r.status, data, r)
    c.close()
    return out


def req(method, path, body=None, cookie=None):
    s, data, r = raw(method, path, body, cookie)
    return s, json.loads(data or b"{}"), (r.getheader("Set-Cookie") or "").split(";")[0]


COOK = {}
for n, _, _ in ROLES:
    s, _, ck = req("POST", "/api/login", {"username": n, "password": "Test-Passwort-1"})
    COOK[n] = ck


def png(w=8, h=8):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + b"\xe2\x38\x2a" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def upload(who, data, ctype, rev=None):
    cfg = req("GET", "/api/config", cookie=COOK["adm"])[1]
    return req("POST", "/api/config/logo", {"revision": cfg["revision"] if rev is None else rev, "contentType": ctype,
                                           "data": base64.b64encode(data).decode()}, COOK[who])


# --- Rechte
check(raw("GET", "/api/config")[0] == 401, "GET ohne Anmeldung: 401")
for n, _, _ in ROLES:
    s, b, _ = req("GET", "/api/config", cookie=COOK[n])
    check(s == 200 and b["company"]["productName"] == "Produktionsplanung" and "license" not in b and "update" not in b and "tenantId" not in b,
          f"GET /api/config als {n}: öffentlich, ohne Lizenz/Update/Mandant")
rev0 = req("GET", "/api/config", cookie=COOK["adm"])[1]["revision"]
for n in ("pm1", "gf1", "lead", "view", "sales1"):
    s, b, _ = req("PATCH", "/api/config", {"revision": rev0, "company": {"name": "X"}}, COOK[n])
    check(s == 403, f"PATCH als {n}: 403")
    s, b, _ = req("PUT", "/api/config", {"revision": rev0, "company": {"name": "X"}}, COOK[n])
    check(s == 403, f"PUT als {n}: 403")
    check(upload(n, png(), "image/png")[0] == 403, f"Logo-Upload als {n}: 403")
check(req("GET", "/api/config", cookie=COOK["adm"])[1]["revision"] == rev0 and server.CONFIG_PATH.exists()
      and not list(server.CONFIG_DIR.glob("firma.json.bak-*")), "Verweigerte Schreibversuche ändern nichts")
check(req("PATCH", "/api/config", {"revision": rev0, "company": {"name": "X"}})[0] == 401, "PATCH ohne Anmeldung: 401")
s, _, _ = raw("PATCH", "/api/config", {"revision": rev0, "company": {"name": "X"}}, COOK["adm"], {"X-MP-Client-Version": "1.0.0"})
check(s == 426, "PATCH mit veraltetem Client: 426")

# --- Schreiben, .bak, Revision, atomar
s, b, _ = req("PATCH", "/api/config", {"revision": rev0, "company": {"name": "Muster GmbH", "color": "#112233", "uiAccent": "#445566",
                                                                      "font": "Verdana", "address": "Weg 1", "footer": "F"},
                                        "terms": {"projectNumber": "Auftragsnr"}, "locale": {"timezone": "Europe/Vienna"},
                                        "modules": {"chat": False}}, COOK["adm"])
check(s == 200 and b["config"]["company"]["name"] == "Muster GmbH" and b["config"]["revision"] == rev0 + 1, "PATCH Admin: gespeichert, Revision +1")
g = req("GET", "/api/config", cookie=COOK["view"])[1]
check(g["company"]["color"] == "#112233" and g["company"]["uiAccent"] == "#445566" and g["terms"]["projectNumber"] == "Auftragsnr"
      and g["locale"]["timezone"] == "Europe/Vienna" and g["modules"]["chat"] is False and g["modules"]["kpi"] is True,
      "Alle Rollen lesen die neuen Werte (Abschnitte werden zusammengeführt)")
disk = json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))
check(disk["company"]["name"] == "Muster GmbH" and disk["tenantId"] == "firma" and disk["license"] == {"file": "lizenz.key"} and disk["revision"] == rev0 + 1,
      "firma.json enthält Änderung, Rest unverändert")
check(len(list(server.CONFIG_DIR.glob("firma.json.bak-*"))) >= 1 and not list(server.CONFIG_DIR.glob("*.tmp")), ".bak angelegt, keine .tmp")
with server.db_session() as con:
    check(con.execute("SELECT COUNT(*) FROM server_audit WHERE action='Firmenprofil geändert' AND username='adm'").fetchone()[0] == 1, "Audit-Eintrag 'Firmenprofil geändert'")
s, b, _ = req("PUT", "/api/config", {"revision": rev0 + 1, "company": {"name": "Muster GmbH"}}, COOK["adm"])
check(s == 200 and b.get("unchanged") and b["config"]["revision"] == rev0 + 1, "Unveränderter Inhalt: keine neue Revision")

# --- Konflikt
rev1 = rev0 + 1
s, b, _ = req("PATCH", "/api/config", {"revision": rev0, "company": {"name": "Alt"}}, COOK["adm"])
check(s == 409 and b["errorCode"] == "MP-CFG-004" and b["revision"] == rev1 and b["config"]["company"]["name"] == "Muster GmbH", "Veraltete Revision: 409 MP-CFG-004 mit aktuellem Stand")
check(req("GET", "/api/config", cookie=COOK["adm"])[1]["company"]["name"] == "Muster GmbH", "Konflikt ändert nichts")
check(req("PATCH", "/api/config", {"company": {"name": "Alt"}}, COOK["adm"])[0] == 400, "Ohne revision: 400")
check(req("PATCH", "/api/config", {"revision": "1", "company": {"name": "Alt"}}, COOK["adm"])[0] == 400, "revision als Text: 400")

# --- Validierung
bad = {
    "Hex": {"company": {"color": "rot"}}, "Hex uiAccent": {"company": {"uiAccent": "#12"}}, "Name Typ": {"company": {"name": 5}},
    "Name lang": {"company": {"name": "x" * 121}}, "Modul Typ": {"modules": {"chat": "ja"}}, "Sprache": {"locale": {"language": "fr"}},
    "tenantId": {"tenantId": "abc"}, "template": {"template": "x"}, "license": {"license": {"file": "x"}}, "update": {"update": {"source": "x"}},
    "unbekanntes Feld": {"company": {"foo": 1}}, "unbekannter Abschnitt": {"foo": {}}, "logoFile setzen": {"company": {"logoFile": "../x.png"}},
    "Abschnitt kein Objekt": {"company": "x"}, "Bereiche leer": {"projectAreas": []}, "Bereiche doppelt": {"projectAreas": [{"id": "a", "name": "A"}, {"id": "a", "name": "B"}]},
    "Begriff lang": {"terms": {"projectNumber": "x" * 31}},
}
r = req("GET", "/api/config", cookie=COOK["adm"])[1]["revision"]
for label, patch in bad.items():
    s, b, _ = req("PATCH", "/api/config", {"revision": r, **patch}, COOK["adm"])
    check(s == 400 and b["errorCode"] == "MP-CFG-002", f"Ungültig abgelehnt ({label})")
check(req("GET", "/api/config", cookie=COOK["adm"])[1]["revision"] == r, "Ungültige Anfragen ändern die Revision nicht")
s, b, _ = req("PATCH", "/api/config", {"revision": r, "projectAreas": [{"id": "x1", "name": "Bereich X"}]}, COOK["adm"])
check(s == 200 and b["config"]["projectAreas"] == [{"id": "x1", "name": "Bereich X"}], "projectAreas gültig ersetzt")

# --- Logo
rv = req("GET", "/api/config", cookie=COOK["adm"])[1]["revision"]
s, b, _ = upload("adm", png(), "image/png")
check(s == 200 and b["config"]["company"]["logoUrl"].startswith("/api/config/logo") and b["config"]["company"]["logoType"] == "image/png", "PNG-Upload angenommen")
s, data, resp = raw("GET", "/api/config/logo", cookie=COOK["view"])
check(s == 200 and data == png() and resp.getheader("Content-Type") == "image/png" and resp.getheader("X-Content-Type-Options") == "nosniff", "Logo-Endpunkt: Bytes, Content-Type image/png, nosniff (alle Rollen)")
check(raw("GET", "/api/config/logo")[0] == 401, "Logo ohne Anmeldung: 401")
etag = resp.getheader("ETag")
check(raw("GET", "/api/config/logo", cookie=COOK["view"], headers={"If-None-Match": etag})[0] == 304, "Logo: ETag -> 304")
check((server.CONFIG_DIR / "logo.png").read_bytes() == png() and json.loads(server.CONFIG_PATH.read_text())["company"]["logoFile"] == "logo.png", "Logo liegt als Datei unter config/")
jpg = b"\xff\xd8\xff\xe0" + b"\x00" * 40
s, b, _ = upload("adm", jpg, "image/jpeg")
check(s == 200 and not (server.CONFIG_DIR / "logo.png").exists() and (server.CONFIG_DIR / "logo.jpg").exists(), "JPG ersetzt PNG, alte Datei entfernt")
s, data, resp = raw("GET", "/api/config/logo", cookie=COOK["adm"])
check(resp.getheader("Content-Type") == "image/jpeg", "Content-Type image/jpeg")
check(list(server.CONFIG_DIR.glob("logo.*.bak-*")), "Vorheriges Logo als .bak aufgehoben")
# Ablehnungen
cur = req("GET", "/api/config", cookie=COOK["adm"])[1]["revision"]
rej = {
    "PNG-Inhalt falsch": (b"GIF89a" + b"x" * 20, "image/png"), "JPG-Inhalt falsch": (png(), "image/jpeg"), "GIF-Typ": (b"GIF89a", "image/gif"),
    "zu groß": (png() + b"\x00" * (server.CONFIG_LOGO_MAX + 1), "image/png"), "leer": (b"", "image/png"),
    "HTML als SVG": (b"<html><script>alert(1)</script></html>", "image/svg+xml"),
}
for label, (data, ct) in rej.items():
    s, b, _ = upload("adm", data, ct, cur)
    check(s == 400 and b["errorCode"] == "MP-CFG-005", f"Logo abgelehnt ({label})")
check(req("POST", "/api/config/logo", {"revision": cur, "contentType": "image/png", "data": "***"}, COOK["adm"])[0] == 400, "Logo: kaputtes Base64 -> 400")
check(req("GET", "/api/config", cookie=COOK["adm"])[1]["revision"] == cur and (server.CONFIG_DIR / "logo.jpg").exists(), "Abgelehnte Uploads lassen Config und Logo unberührt")
check(upload("adm", png(), "image/png", cur - 1)[0] == 409, "Logo-Upload mit veralteter Revision: 409")

# --- SVG
OKSVG = b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" viewBox="0 0 10 10" inkscape:version="1"><metadata>x</metadata><defs><linearGradient id="g"><stop offset="0" stop-color="#f00"/></linearGradient></defs><rect width="10" height="10" fill="url(#g)"/><use xlink:href="#g"/></svg>'
s, b, _ = upload("adm", OKSVG, "image/svg+xml")
check(s == 200 and b["config"]["company"]["logoType"] == "image/svg+xml", "Sauberes SVG angenommen")
s, data, resp = raw("GET", "/api/config/logo", cookie=COOK["adm"])
check(resp.getheader("Content-Type") == "image/svg+xml" and "sandbox" in (resp.getheader("Content-Security-Policy") or "")
      and b"inkscape" not in data and b"metadata" not in data and b"<rect" in data, "SVG: Content-Type, CSP-Sandbox, Editor-Metadaten entfernt")
evil = {
    "script": b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    "onload": b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect/></svg>',
    "onclick": b'<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="x()"/></svg>',
    "foreignObject": b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div/></foreignObject></svg>',
    "image extern": b'<svg xmlns="http://www.w3.org/2000/svg"><image href="http://evil/x.png"/></svg>',
    "a javascript": b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><rect/></a></svg>',
    "use extern": b'<svg xmlns="http://www.w3.org/2000/svg"><use href="http://evil/x.svg#a"/></svg>',
    "animate": b'<svg xmlns="http://www.w3.org/2000/svg"><rect><animate attributeName="x" to="1"/></rect></svg>',
    "set": b'<svg xmlns="http://www.w3.org/2000/svg"><set attributeName="onload" to="x"/></svg>',
    "css url extern": b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(http://evil/x)"/></svg>',
    "css import": b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import "http://evil/x.css";</style></svg>',
    "javascript-Wert": b'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="javascript:alert(1)"/></svg>',
    "DOCTYPE/Entity": b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg"><text>&a;</text></svg>',
    "kein SVG-Wurzel": b'<html xmlns="http://www.w3.org/1999/xhtml"><body/></html>',
    "kaputtes XML": b'<svg xmlns="http://www.w3.org/2000/svg"><rect></svg>',
    "iframe-Namespace": b'<svg xmlns="http://www.w3.org/2000/svg"><iframe src="x"/></svg>',
}
cur = req("GET", "/api/config", cookie=COOK["adm"])[1]["revision"]
for label, data in evil.items():
    s, b, _ = upload("adm", data, "image/svg+xml", cur)
    check(s == 400 and b["errorCode"] == "MP-CFG-005", f"Unsicheres SVG abgelehnt ({label})")
check(req("GET", "/api/config", cookie=COOK["adm"])[1]["revision"] == cur, "Abgelehnte SVGs ändern nichts")
check(server.svg_sanitize(b'<svg xmlns="http://www.w3.org/2000/svg"><g><path d="M0 0L1 1"/></g></svg>').startswith(b"<?xml"), "svg_sanitize liefert serialisiertes SVG")
# Beschädigte/manipulierte SVG-Datei in config/ -> Validierung beim Laden
(server.CONFIG_DIR / "logo.svg").write_bytes(evil["script"])
cfgd = json.loads(server.CONFIG_PATH.read_text())
check(any("SVG" in e for e in server.validate_config(cfgd)), "Manipuliertes SVG in config/ wird beim Laden erkannt")
(server.CONFIG_DIR / "logo.svg").write_bytes(server.svg_sanitize(OKSVG))

# --- Logo entfernen
cur = req("GET", "/api/config", cookie=COOK["adm"])[1]["revision"]
s, b, _ = req("PATCH", "/api/config", {"revision": cur, "company": {"logoFile": ""}}, COOK["adm"])
check(s == 200 and b["config"]["company"]["logoUrl"] == "" and not (server.CONFIG_DIR / "logo.svg").exists(), "Logo entfernen: Datei weg, URL leer")
check(raw("GET", "/api/config/logo", cookie=COOK["adm"])[0] == 404, "Logo-Endpunkt ohne Logo: 404")
check(list(server.CONFIG_DIR.glob("logo.svg.bak-*")), "Entferntes Logo als .bak aufgehoben")

# --- Long-Poll: Config-Änderung weckt alle
_, rv, _ = req("GET", "/api/revision", cookie=COOK["view"])
check(rv.get("cfg") == req("GET", "/api/config", cookie=COOK["view"])[1]["revision"], "/api/revision nennt Config-Revision (cfg)")
crev = rv["cfg"]
box = {}


def poll():
    t0 = time.monotonic()
    box["r"] = req("GET", f"/api/revision?since={rv['revision']}&wait=8000&crev={crev}", cookie=COOK["view"])
    box["dt"] = time.monotonic() - t0


th = threading.Thread(target=poll)
th.start()
time.sleep(0.5)
req("PATCH", "/api/config", {"revision": crev, "company": {"name": "Neu GmbH"}}, COOK["adm"])
th.join(10)
check(box["dt"] < 3 and box["r"][1]["cfg"] == crev + 1, f"Config-Änderung weckt wartende Clients ({box['dt']:.1f}s)")
t0 = time.monotonic()
req("GET", f"/api/revision?since={rv['revision']}&wait=1500&crev={crev + 1}", cookie=COOK["view"])
check(time.monotonic() - t0 >= 1.3, "Ohne Änderung wartet der Long-Poll weiter")

# --- schemaVersion zu neu: schreibgeschützt
d = json.loads(server.CONFIG_PATH.read_text())
d["schemaVersion"] = 99
server.CONFIG_PATH.write_text(json.dumps(d), encoding="utf-8")
server.CURRENT_CFG = None
cur = req("GET", "/api/config", cookie=COOK["adm"])[1]
check(cur["readOnly"] is True, "schemaVersion zu neu: readOnly gemeldet")
s, b, _ = req("PATCH", "/api/config", {"revision": cur["revision"], "company": {"name": "Z"}}, COOK["adm"])
check(s == 409 and b["errorCode"] == "MP-CFG-003" and json.loads(server.CONFIG_PATH.read_text())["schemaVersion"] == 99, "Schreiben bei zu neuer Datei: 409 MP-CFG-003, Datei unverändert")

# --- Zeitzone aus der Config (Env bleibt Override)
import subprocess  # noqa: E402
tzd = tmp / "tzcfg"
tzd.mkdir()
(tzd / "firma.json").write_text(json.dumps({"locale": {"timezone": "Asia/Tokyo"}}), encoding="utf-8")


def tz(env_extra):
    env = {**os.environ, "MP_CONFIG_DIR": str(tzd), **env_extra}
    env.pop("MP_TIMEZONE", None) if "MP_TIMEZONE" not in env_extra else None
    return subprocess.run([sys.executable, "-c", "import release_gates as r;print(r.LOCAL_TZ)"], cwd=SRC, env=env, capture_output=True, text=True).stdout.strip()


check(tz({}) == "Asia/Tokyo", "Zeitzone aus locale.timezone der Config")
check(tz({"MP_TIMEZONE": "Europe/Paris"}) == "Europe/Paris", "MP_TIMEZONE überschreibt die Config")
(tzd / "firma.json").write_text(json.dumps({"locale": {"timezone": "Kein/Ort"}}), encoding="utf-8")
check(tz({}) == "Europe/Berlin", "Ungültige Zeitzone: Fallback Europe/Berlin")

httpd.shutdown()
failed = RESULTS.count(False)
print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

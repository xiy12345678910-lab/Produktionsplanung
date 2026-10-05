#!/usr/bin/env python3
"""V12.17.0 (C1): Einrichtungsassistent serverseitig. Neutraler Start, setupDone nur bei echter Neuinstallation,
Bestand nie, Schreibrechte, Passwortschritt, Projektbereiche aus der Vorlage (nur ergänzend).

Aufruf:  python tests/test_setup.py     (nur Temp-Ordner)
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
ROOT = Path(tempfile.mkdtemp(prefix="mp-setup-"))
os.environ["MP_CONFIG_DIR"] = str(ROOT / "config0")
import server  # noqa: E402

RESULTS: list[bool] = []
server.PBKDF2_ITERS = 1000
server.ALLOWED_NETWORK = server.ipaddress.ip_network("127.0.0.0/8")
server.Handler.log_message = lambda *a, **k: None
N = [0]


def check(cond, label):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + label)


def fresh(seed="neutral", bump=False, cfg_text=None):
    """Neue Umgebung (Daten + Config) und init_db -> load_config, wie ein Serverstart."""
    N[0] += 1
    base = ROOT / f"env{N[0]}"
    server.DATA_DIR = base / "data"
    server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
    server.CONFIG_DIR = base / "config"
    server.CONFIG_PATH = server.CONFIG_DIR / "firma.json"
    server.CURRENT_CFG = None
    server.CONFIG_DIR.mkdir(parents=True)
    if cfg_text is not None:
        server.CONFIG_PATH.write_text(cfg_text, encoding="utf-8")
    server.init_db(seed=seed)
    if bump:
        with server.DB_LOCK, server.db_session() as con:
            con.execute("UPDATE state SET revision=revision+1 WHERE id=1")
    return server.load_config()[0]


def state():
    with server.db_session() as con:
        return json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])


# --- 1. Neuinstallation: neutral + Assistent
cfg = fresh()
st = state()
check(st["departments"] == [] and st["machines"] == [], "Neuinstallation: neutraler Seed ohne Bereiche und Maschinen")
check(st["workSteps"] == [] and st["projects"] == [] and st["employees"] == [], "Neuinstallation: keine Planungsdaten")
check(set(st["shiftTemplates"]) == {"single", "fridaySingle", "early", "late"}, "Neuinstallation: Schichtmodelle bleiben als Grundlage")
check(cfg["setupDone"] is False, "Neuinstallation: setupDone=false in firma.json")
check(json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))["setupDone"] is False, "setupDone steht in der Datei")
check(server.public_config()["setupDone"] is False, "public_config meldet setupDone=false")
check([a["id"] for a in cfg["projectAreas"]] == ["sales", "pm", "engineering", "calculation", "purchasing", "quality", "av"], "Neuinstallation: sieben Projektbereiche in der Config")
cfg2, _ = server.load_config()
check(cfg2["setupDone"] is False, "erneuter Start ohne Änderung: weiterhin setupDone=false (idempotent)")

# --- 2. Bestand: nie ein Assistent
cfg = fresh(seed="werbetechnik")
check(cfg["setupDone"] is True, "Altstand (Daten vorhanden, Revision 1): setupDone=true")
check({"cnc", "thermoforming"} <= {d["id"] for d in state()["departments"]} and any(m["id"] == "m1" for m in state()["machines"]), "werbetechnik-Seed bleibt für Tests erhalten (CNC, Tiefziehen, Maschine m1)")
cfg = fresh(bump=True)
check(cfg["setupDone"] is True, "Revision > 1 (leere Bereiche): setupDone=true")
cfg = fresh(cfg_text=json.dumps({"schemaVersion": 1, "tenantId": "firma", "company": {"name": "Alt GmbH"}}))
check(cfg["setupDone"] is True and cfg["company"]["name"] == "Alt GmbH", "vorhandene Config ohne setupDone-Feld (Update): true ergänzt, Name bleibt")
check([a["id"] for a in cfg["projectAreas"]] == ["sales", "pm", "engineering", "calculation", "purchasing", "quality", "av"], "vorhandene Config ohne projectAreas: sieben Bereiche ergänzt")
custom = [{"id": "sales", "name": "Verkauf"}, {"id": "lab", "name": "Labor"}]
cfg = fresh(cfg_text=json.dumps({"schemaVersion": 1, "company": {"name": "X"}, "projectAreas": custom, "setupDone": False}))
check(cfg["setupDone"] is False and cfg["projectAreas"] == custom, "vorhandene Config bleibt unangetastet (setupDone=false, eigene projectAreas)")
bad = {"schemaVersion": 1, "company": {"name": "Y"}, "setupDone": "ja"}
check(any("setupDone" in e for e in server.validate_config(bad)), "validate_config: setupDone muss true/false sein")

# --- 3. Firma_Einrichten (Vorlage/neutral) = bewusst eingerichtet
N[0] += 1
base = ROOT / f"env{N[0]}"
server.DATA_DIR = base / "data"
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.CONFIG_DIR = base / "config"
server.CONFIG_PATH = server.CONFIG_DIR / "firma.json"
server.CURRENT_CFG = None
server.init_db()
check(server.setup_firma_from_template("neutral") == 0 and json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))["setupDone"] is True,
      "Firma_Einrichten -Neutral: bewusst neutral, kein Assistent")

# --- 4. HTTP: Rechte, Passwortschritt, Projektbereiche aus der Vorlage
fresh()
for n, r in (("adm", "admin"), ("pm1", "project_management")):
    salt, digest = server.hash_password("Start-Pass-1")
    ts = server.now_iso()
    with server.db_session() as con:
        con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)",
                    (n, salt, digest, r, "", ts, ts))
httpd = server.MPHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def req(method, path, body=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h = {"Content-Type": "application/json", "X-MP-Client-Version": server.APP_VERSION}
    if cookie:
        h["Cookie"] = cookie
    c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
    r = c.getresponse()
    raw = r.read()
    ck = (r.getheader("Set-Cookie") or "").split(";")[0]
    c.close()
    return r.status, json.loads(raw or b"{}"), ck


COOK = {n: req("POST", "/api/login", {"username": n, "password": "Start-Pass-1"})[2] for n in ("adm", "pm1")}
s, c, _ = req("GET", "/api/config", cookie=COOK["adm"])
check(s == 200 and c["setupDone"] is False and c["adminPwUnchanged"] is True, "Admin sieht setupDone=false und Startpasswort unverändert")
s, c2, _ = req("GET", "/api/config", cookie=COOK["pm1"])
check(s == 200 and c2["setupDone"] is False and "adminPwUnchanged" not in c2, "andere Rolle sieht kein Passwort-Kennzeichen")
s, b, _ = req("PATCH", "/api/config", {"revision": c["revision"], "setupDone": True}, COOK["pm1"])
check(s == 403, "setupDone setzen: nur Admin")
s, b, _ = req("PATCH", "/api/config", {"revision": c["revision"], "setupDone": "ja"}, COOK["adm"])
check(s == 400, "setupDone nur als true/false")
s, b, _ = req("POST", "/api/password", {"currentPassword": "Start-Pass-1", "newPassword": "Neues-Pass-2026"}, COOK["adm"])
check(s == 200, "Admin ändert das Startpasswort")
s, c, _ = req("GET", "/api/config", cookie=COOK["adm"])
check(c["adminPwUnchanged"] is False, "nach der Änderung kein Passwortschritt mehr")

# Vorlage mit neuem Projektbereich: nur ergänzend, nie umbenennen/entfernen
real = server.load_templates
tpl = dict(real()["metall_cnc"])
tpl["projectAreas"] = [{"id": "sales", "name": "UMBENANNT"}, {"id": "lab", "name": "Labor"}, {"id": "BAD ID", "name": "x"}]
server.load_templates = lambda: {**real(), "metall_cnc": tpl}
s, b, _ = req("POST", "/api/templates/apply", {"id": "metall_cnc", "dryRun": True}, COOK["adm"])
check(s == 200 and b["summary"]["projectAreas"] == ["Labor"], "Vorschau nennt den neuen Projektbereich")
s, c, _ = req("GET", "/api/config", cookie=COOK["adm"])
check([a["id"] for a in c["projectAreas"]] == ["sales", "pm", "engineering", "calculation", "purchasing", "quality", "av"], "Vorschau ändert die Config nicht")
s, b, _ = req("POST", "/api/templates/apply", {"id": "metall_cnc"}, COOK["adm"])
s2, c, _ = req("GET", "/api/config", cookie=COOK["adm"])
ids = [a["id"] for a in c["projectAreas"]]
check(s == 200 and ids[-1] == "lab" and len(ids) == 8, "Vorlage ergänzt den Projektbereich 'lab'")
check(next(a for a in c["projectAreas"] if a["id"] == "sales")["name"] == "Vertrieb", "vorhandener Projektbereich wird nicht umbenannt")
check(len(state()["departments"]) == 6, "Vorlage füllt die Bereiche der neutralen Installation")
s, b, _ = req("POST", "/api/templates/apply", {"id": "metall_cnc"}, COOK["adm"])
check(s == 200 and b.get("unchanged") is True, "zweites Anwenden: nichts zu tun (idempotent)")
server.load_templates = real
s, c, _ = req("GET", "/api/config", cookie=COOK["adm"])
s, b, _ = req("PATCH", "/api/config", {"revision": c["revision"], "setupDone": True}, COOK["adm"])
check(s == 200 and b["config"]["setupDone"] is True, "Admin schließt die Einrichtung ab")
s, c, _ = req("GET", "/api/config", cookie=COOK["adm"])
check(c["setupDone"] is True and "adminPwUnchanged" not in c, "danach setupDone=true, kein Passwort-Kennzeichen")
check(json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))["setupDone"] is True, "setupDone=true steht in firma.json")
httpd.shutdown()

print(f"\n{sum(RESULTS)}/{len(RESULTS)} bestanden")
sys.exit(0 if all(RESULTS) else 1)

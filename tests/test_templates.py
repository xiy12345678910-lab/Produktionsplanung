#!/usr/bin/env python3
"""V12.16.0: Branchenvorlagen, Module ein/aus, Bereichs-Eigenschaften (Server, über echtes HTTP).

Aufruf:  python tests/test_templates.py
Arbeitet nur in Temp-Ordnern, nie mit Live-Daten.
"""
from __future__ import annotations

import copy
import http.client
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
tmp = Path(tempfile.mkdtemp(prefix="mp-tpl-"))
os.environ["MP_CONFIG_DIR"] = str(tmp / "config")
import server  # noqa: E402

RESULTS: list[bool] = []


def check(cond, label, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""))


server.DATA_DIR = tmp / "data"
server.DATA_DIR.mkdir()
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
server.PBKDF2_ITERS = 1000
server.ALLOWED_NETWORK = server.ipaddress.ip_network("127.0.0.0/8")
server.init_db(seed="werbetechnik")
server.load_config()
USERS = [("adm", "admin", ""), ("pm1", "project_management", ""), ("lead", "department_lead", "cnc"), ("view", "viewer", "")]
with server.db_session() as con:
    for n, r, d in USERS:
        salt, digest = server.hash_password("Test-Passwort-1")
        con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)",
                    (n, salt, digest, r, d, server.now_iso(), server.now_iso()))
httpd = server.MPHTTPServer(("127.0.0.1", 0), server.Handler)
server.Handler.log_message = lambda *a, **k: None
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def req(method, path, body=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h = {"Content-Type": "application/json", "X-MP-Client-Version": server.APP_VERSION}
    if cookie:
        h["Cookie"] = cookie
    c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
    r = c.getresponse()
    data = r.read()
    out = (r.status, json.loads(data or b"{}"), (r.getheader("Set-Cookie") or "").split(";")[0])
    c.close()
    return out


COOK = {n: req("POST", "/api/login", {"username": n, "password": "Test-Passwort-1"})[2] for n, _, _ in USERS}


def state():
    s, b, _ = req("GET", "/api/state", cookie=COOK["adm"])
    return b["revision"], b["data"]


def put(data, rev, who="adm"):
    return req("PUT", "/api/state", {"revision": rev, "data": data, "action": "Test"}, COOK[who])


def cfg_set(patch):
    rev = req("GET", "/api/config", cookie=COOK["adm"])[1]["revision"]
    return req("PATCH", "/api/config", {"revision": rev, **patch}, COOK["adm"])


# ---------- 1. Vorlagendateien ----------
tpls = server.load_templates()
check(set(tpls) == {"werbetechnik", "metall_cnc", "leer", "demo"}, f"4 Vorlagendateien vorhanden und gültig ({sorted(tpls)})")
common = (SRC / "MP_Common.ps1").read_text(encoding="utf-8-sig")
check(all(f"'vorlage_{t}.json'" in common for t in tpls), "Vorlagen stehen in $MP_AppFiles (Update liefert sie mit)")
check(all(t in server.CONFIG_TEMPLATES for t in tpls), "Vorlagen-IDs sind in der Config als Vorlage zulässig")

# ---------- 2. Rechte ----------
s, b, _ = req("GET", "/api/templates", cookie=COOK["adm"])
check(s == 200 and {t["id"] for t in b["templates"]} == set(tpls), "GET /api/templates (Admin) liefert alle Vorlagen mit Vorschau-Daten")
mc = next(t for t in b["templates"] if t["id"] == "metall_cnc")
check(len(mc["departments"]) == 6 and mc["machines"] >= 4 and mc["processTemplates"], "Vorschau nennt Bereiche, Maschinen und Abläufe")
check(all(req("GET", "/api/templates", cookie=COOK[n])[0] == 403 for n in ("pm1", "lead", "view")), "GET /api/templates: andere Rollen 403")
check(req("GET", "/api/templates")[0] == 401, "GET /api/templates ohne Sitzung 401")
check(all(req("POST", "/api/templates/apply", {"id": "leer"}, COOK[n])[0] == 403 for n in ("pm1", "lead", "view")), "Anwenden: andere Rollen 403")
check(req("POST", "/api/templates/apply", {"id": "gibtsnicht"}, COOK["adm"])[0] == 404, "unbekannte Vorlage 404 (MP-TPL-040)")

# ---------- 3. Anwenden ergänzt nur ----------
rev0, d0 = state()
before = copy.deepcopy(d0)
s, b, _ = req("POST", "/api/templates/apply", {"id": "metall_cnc", "dryRun": True}, COOK["adm"])
rev1, d1 = state()
check(s == 200 and b["dryRun"] and len(b["summary"]["departments"]) == 6 and rev1 == rev0 and d1 == d0, "Vorschau (dryRun) ändert nichts, nennt 6 neue Bereiche")
s, b, _ = req("POST", "/api/templates/apply", {"id": "metall_cnc"}, COOK["adm"])
rev2, d2 = state()
check(s == 200 and rev2 == rev0 + 1, "Anwenden metall_cnc erhöht die Revision um 1")
ok = True
for key, val in before.items():
    if key in ("meta",):
        continue
    if isinstance(val, list):
        old_ids = [x.get("id") if isinstance(x, dict) else x for x in val]
        new_ids = [x.get("id") if isinstance(x, dict) else x for x in d2[key]]
        ok &= new_ids[:len(old_ids)] == old_ids and all(a == c for a, c in zip(val, d2[key]))
    elif isinstance(val, dict) and key in ("shiftTemplates",):
        ok &= all(d2[key][k] == v for k, v in val.items())
    else:
        ok &= d2[key] == val
check(ok, "Vorhandene Bereiche, Maschinen, Schichten, Abläufe bleiben unverändert (nur angehängt)")
names = [d["name"] for d in d2["departments"]]
check({"Zuschnitt", "CNC-Fräsen", "Drehen", "Schweißen", "Oberfläche", "Montage"} <= set(names), "neue Bereiche aus der Vorlage da")
check(all(m["departmentId"] in {d["id"] for d in d2["departments"]} for m in d2["machines"]) and len(d2["machines"]) == len(before["machines"]) + 4, "Maschinen nur für neue Bereiche ergänzt")
check(next(d for d in d2["departments"] if d["id"] == "fraesen").get("sharedOperators") is True, "Eigenschaft sharedOperators kommt aus der Vorlage")
check(not any(d.get("formats") for d in d2["departments"] if d["id"] in {"zuschnitt", "fraesen", "drehen", "schweissen", "oberflaeche", "montage"}), "metall_cnc: kein Formatbereich")
s, b, _ = req("POST", "/api/templates/apply", {"id": "metall_cnc"}, COOK["adm"])
rev3, d3 = state()
check(s == 200 and b.get("unchanged") and rev3 == rev2 and d3 == d2, "zweites Anwenden ist ein No-op (idempotent, keine Revision)")
audit = [a for a in d3.get("audit", [])]
ok_v, reason, code = True, "", ""
valid, ecode, reason = server.validate_state(d0, d2)
check(valid, "Ergebnis besteht validate_state", f"{ecode} {reason}")
# Bestehende Daten werden nie überschrieben: Bereich mit gleichem Namen/gleicher ID wird übersprungen
dd = copy.deepcopy(d3)
dd["departments"][0]["name"] = "Zuschnitt"
rr, dx = state()
s, b, _ = put(dd, rr)
s, b, _ = req("POST", "/api/templates/apply", {"id": "demo", "dryRun": True}, COOK["adm"])
check(s == 200 and "Zuschnitt" not in b["summary"]["departments"], "gleichnamiger Bereich wird nicht doppelt angelegt")
for t in ("werbetechnik", "leer", "demo"):
    s, b, _ = req("POST", "/api/templates/apply", {"id": t, "dryRun": True}, COOK["adm"])
    check(s == 200, f"Vorschau {t} funktioniert")

# alle Vorlagen auf dem Seed-Stand (Bestandsform) und einem Minimalstand: gültig
import importlib  # noqa: E402

for tid, t in tpls.items():
    for label, base in (("Seed", before), ("Minimal", {**before, "departments": [{"id": "fert", "name": "Fertigung", "planningType": "MACHINE", "active": True}],
                                                     "machines": [{**before["machines"][0], "departmentId": "fert"}], "workSteps": [], "history": [], "projects": []})):
        new, summ = server.template_merge(base, t)
        valid, ecode, reason = server.validate_state(base, new)
        check(valid, f"Vorlage {tid} auf {label}-Stand besteht validate_state", f"{ecode} {reason}")
new, summ = server.template_merge(before, tpls["werbetechnik"])
check(not summ["departments"] and not summ["machines"] and not summ["shiftTemplates"] and not summ["processTemplates"],
      "werbetechnik auf dem Bestand (Arbeitgeber-Form): nichts zu ergänzen")

# Module der Vorlage nur auf Wunsch
before_mods = req("GET", "/api/config", cookie=COOK["adm"])[1]["modules"]
s, b, _ = req("POST", "/api/templates/apply", {"id": "leer"}, COOK["adm"])
check(req("GET", "/api/config", cookie=COOK["adm"])[1]["modules"] == before_mods, "ohne Wunsch bleiben die Module unverändert")
s, b, _ = req("POST", "/api/templates/apply", {"id": "leer", "modules": True, "dryRun": True}, COOK["adm"])
check(s == 200 and b["summary"]["modules"] == {"formats": False}, "Vorschau nennt die Modul-Änderung der Vorlage (formats aus)")
s, b, _ = req("POST", "/api/templates/apply", {"id": "leer", "modules": True}, COOK["adm"])
check(req("GET", "/api/config", cookie=COOK["adm"])[1]["modules"]["formats"] is False, "mit Wunsch werden die Module der Vorlage übernommen")
cfg_set({"modules": {"formats": True}})

# ---------- 4. Migration der Bereichs-Eigenschaften ----------
with server.db_session() as con:
    st = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
    for d in st["departments"]:
        d.pop("formats", None)
        d.pop("sharedOperators", None)
        if d["id"] == "cnc":
            d["sharedOperators"] = False        # vorhandener Wert bleibt
    con.execute("UPDATE state SET json=? WHERE id=1", (json.dumps(st),))
    server.migrate_state_v1216(con)
    a = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
    server.migrate_state_v1216(con)
    b2 = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
dm = {d["id"]: d for d in a["departments"]}
check(dm["thermoforming"].get("formats") is True, "Migration setzt thermoforming.formats")
check(dm["cnc"].get("sharedOperators") is False, "Migration überschreibt einen vorhandenen Wert nicht")
check(all("formats" not in d for i, d in dm.items() if i != "thermoforming") and a == b2, "Migration ist additiv und idempotent")
with server.db_session() as con:
    st = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
    for d in st["departments"]:
        if d["id"] == "cnc":
            d.pop("sharedOperators", None)
    con.execute("UPDATE state SET json=? WHERE id=1", (json.dumps(st),))
    server.migrate_state_v1216(con)
check(next(d for d in json.loads(server.db().execute("SELECT json FROM state WHERE id=1").fetchone()["json"])["departments"] if d["id"] == "cnc").get("sharedOperators") is True,
      "fehlende Eigenschaft wird gesetzt (cnc.sharedOperators)")

# Validierung der Eigenschaften
rev, d = state()
bad = copy.deepcopy(d)
bad["departments"][0]["formats"] = "ja"
s, b, _ = put(bad, rev)
check(s == 400 and b.get("errorCode") == "MP-DEPT-007", "formats muss true/false sein (MP-DEPT-007)")
check(server.default_dept_id({"departments": [{"id": "a", "active": False}, {"id": "b", "kind": "sales"}, {"id": "c"}]}) == "c", "Standardbereich = erster aktiver Produktionsbereich")
check(server.default_dept_id(d) == d["departments"][0]["id"] and server.default_dept_id({}) == "cnc", "Standardbereich Bestand/Leer")

# ---------- 5. Module ----------
rev, d = state()
chan = req("GET", "/api/chat/channels", cookie=COOK["pm1"])[1]
cid = chan["channels"][0]["id"]
s, b, _ = req("POST", "/api/chat/messages", {"channel": cid, "text": "Hallo vor dem Abschalten"}, COOK["pm1"])
check(s in (200, 201), "Chat vor dem Abschalten funktioniert")
s, b, _ = cfg_set({"modules": {"chat": False}})
check(s == 200 and b["config"]["modules"]["chat"] is False, "Modul chat per PATCH abschaltbar (Admin)")
cfg_set({"modules": {"chat": False}})
for m, path, body in (("GET", "/api/chat/channels", None), ("GET", f"/api/chat/messages?channel={cid}", None), ("POST", "/api/chat/messages", {"channel": cid, "text": "x"})):
    s, b, _ = req(m, path, body, COOK["pm1"])
    check(s == 403 and b.get("errorCode") == "MP-MOD-001", f"chat aus: {m} {path.split('?')[0]} -> 403 MP-MOD-001")
cfg_set({"modules": {"chat": True}})
s, b, _ = req("GET", f"/api/chat/messages?channel={cid}", cookie=COOK["pm1"])
check(s == 200 and any("vor dem Abschalten" in str(m.get("text")) for m in b.get("messages", [])), "chat wieder an: alte Nachrichten sind da")

cfg_set({"modules": {"notifications": False}})
check(req("GET", "/api/notifications?since=0", cookie=COOK["pm1"])[1].get("errorCode") == "MP-MOD-001", "notifications aus: GET 403 MP-MOD-001")
check(req("PUT", "/api/notifications/prefs", {"kinds": {}}, COOK["pm1"])[1].get("errorCode") == "MP-MOD-001", "notifications aus: PUT prefs 403")
check(req("POST", "/api/notifications/read", {"all": True}, COOK["pm1"])[1].get("errorCode") == "MP-MOD-001", "notifications aus: POST 403")
cfg_set({"modules": {"notifications": True}})
check(req("GET", "/api/notifications?since=0", cookie=COOK["pm1"])[0] == 200, "notifications wieder an")

# Datensammlungen
rev, d = state()
nd = copy.deepcopy(d)
nd["projects"] = nd.get("projects") or []
cfg_set({"modules": {"projects": False}})
chg = copy.deepcopy(d)
chg["projects"].append({"id": "px1", "number": "P-1", "name": "Neu", "phase": "inquiry", "processes": [], "log": []})
s, b, _ = put(chg, rev)
check(s == 403 and b.get("errorCode") == "MP-MOD-001", "projects aus: Projekt anlegen -> 403 MP-MOD-001")
chg = copy.deepcopy(d)
chg["processTemplates"] = chg["processTemplates"][:-1]
s, b, _ = put(chg, rev)
check(s == 403 and b.get("errorCode") == "MP-MOD-001", "projects aus: Ablauf-Vorlagen ändern -> 403")
chg = copy.deepcopy(d)
chg["ui"]["accent"] = "#112233"
s, b, _ = put(chg, rev)
check(s == 200, "projects aus: andere Daten bleiben schreibbar")
cfg_set({"modules": {"projects": True}})
rev, d = state()
chg = copy.deepcopy(d)
chg["projects"].append({"id": "px1", "number": "P-1", "name": "Neu", "phase": "inquiry", "processes": [], "log": [], "customer": "", "dueDate": ""})
s, b, _ = put(chg, rev)
check(s == 200, f"projects wieder an: Schreiben geht ({b.get('errorCode', '')})")
rev, d = state()
check(any(p["id"] == "px1" for p in d["projects"]), "projects: Daten waren vollständig erhalten")

rev, d = state()
cfg_set({"modules": {"personnel": False}})
chg = copy.deepcopy(d)
chg["employees"].append({"id": "e9", "name": "Test Person", "type": "permanent", "departmentId": d["departments"][0]["id"], "weeklyHours": 40, "active": True})
s, b, _ = put(chg, rev)
check(s == 403 and b.get("errorCode") == "MP-MOD-001", "personnel aus: Mitarbeiter anlegen -> 403")
cfg_set({"modules": {"personnel": True}})
cfg_set({"modules": {"formats": False}})
chg = copy.deepcopy(d)
chg["baseFormats"] = [{"id": "g1", "name": "G", "departmentId": "thermoforming", "L": 1000, "B": 800}]
s, b, _ = put(chg, rev)
check(s == 403 and b.get("errorCode") == "MP-MOD-001", "formats aus: Grundformat anlegen -> 403")
cfg_set({"modules": {"formats": True}})

# Abhängigkeit kpi -> postcalc
cfg_set({"modules": {"postcalc": False}})
m = req("GET", "/api/config", cookie=COOK["pm1"])[1]["modules"]
check(m["postcalc"] is False and m["kpi"] is False, "postcalc aus -> kpi wirkt ebenfalls aus (Abhängigkeit)")
cfg_set({"modules": {"postcalc": True}})
check(req("GET", "/api/config", cookie=COOK["pm1"])[1]["modules"]["kpi"] is True, "postcalc an -> kpi wieder an")
check(cfg_set({"modules": {"unbekannt": True}})[0] == 400, "unbekanntes Modul wird abgelehnt")
check(cfg_set({"modules": {"chat": "ja"}})[0] == 400, "Modul-Schalter muss true/false sein")
check(all(cfg_set({"modules": {"chat": True}})[0] == 200 for _ in [0]) and all(req("PATCH", "/api/config", {"revision": 0, "modules": {"chat": False}}, COOK[n])[0] == 403 for n in ("pm1", "lead", "view")),
      "Module schalten nur Admin (andere Rollen 403)")

# Rollenbezeichnungen
s, b, _ = cfg_set({"terms": {"roleLabels": {"production_planning": "Planung", "viewer": "Nur lesen"}}})
check(s == 200 and b["config"]["terms"]["roleLabels"]["production_planning"] == "Planung", "Rollenbezeichnungen speicherbar")
check(cfg_set({"terms": {"roleLabels": {"chef": "x"}}})[0] == 400, "unbekannte Rolle in roleLabels abgelehnt")
check(cfg_set({"terms": {"roleLabels": {"viewer": "x" * 41}}})[0] == 400, "zu langer Rollenname abgelehnt")
s, b, _ = cfg_set({"terms": {"orderNumber": "FS"}})
check(s == 200 and b["config"]["terms"]["orderNumber"] == "FS", "Begriff Auftragsnummer speicherbar")

print(f"\n{sum(RESULTS)}/{len(RESULTS)} bestanden")
sys.exit(0 if all(RESULTS) else 1)

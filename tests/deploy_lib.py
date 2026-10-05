#!/usr/bin/env python3
"""Gemeinsame Bausteine für den Datenerhalt-Test bei Updates (V12.15.1).

Genutzt von
  - tests/test_deploy_upgrade.py   (Dateisystem-Simulation von UPDATE_LIVE.ps1, alle Plattformen)
  - tests/ci_windows_deploy.ps1    (echtes Windows-Deploy in CI, ruft die Kommandozeile unten auf)

Nur Standardbibliothek. Daten werden ausschließlich über die HTTP-API des ALTEN Servers angelegt
(Benutzer, Planungsdaten, Chat, Benachrichtigungen, Firmen-CI); der Vergleich ist rein additiv:
Felder, Zeilen und Dateien des Bestands müssen nach dem Update unverändert da sein, erlaubt sind nur NEUE Felder.

Kommandozeile (für CI):
  python tests/deploy_lib.py seed        --url U --admin-password P --scenario ci|noci --out seed.json
  python tests/deploy_lib.py fingerprint --url U --seed seed.json --out fp.json
  python tests/deploy_lib.py verify      --url U --seed seed.json --fingerprint fp.json [--expect-config-from-ci]
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import sqlite3
import struct
import sys
import tempfile
import zlib
from pathlib import Path

# --------------------------------------------------------------------------------------------
# Paketlisten aus MP_Common.ps1
# --------------------------------------------------------------------------------------------


def ps_list(text: str, name: str) -> list[str]:
    m = re.search(r"\$" + name + r"\s*=\s*@\((.*?)\n\)", text, re.S)
    return re.findall(r"'([^']+)'", m.group(1)) if m else []


def package_lists(common_text: str) -> tuple[list[str], list[str]]:
    return ps_list(common_text, "MP_AppFiles"), ps_list(common_text, "MP_ObsoleteFiles")


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tree_hashes(root: Path, skip_top: tuple[str, ...] = ()) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.split("/")[0] in skip_top or "__pycache__" in rel.split("/"):
            continue
        out[rel] = sha256(p)
    return out


# --------------------------------------------------------------------------------------------
# Additiver Vergleich
# --------------------------------------------------------------------------------------------


def additive_diff(old, new, path: str = "", fill=()) -> list[str]:
    """Findet alles, was von 'old' nach 'new' verloren ging oder geändert wurde. Neue Felder in Objekten sind erlaubt;
    Listen müssen gleich lang sein (Elemente werden einzeln additiv verglichen).
    fill: Pfade, deren LEERER bzw. Standard-Wert (""/Standardfarbe/-schrift) ergänzt werden darf (Migration füllt nur Leeres)."""
    if isinstance(old, dict):
        if not isinstance(new, dict):
            return [f"{path or '/'}: Objekt wurde {type(new).__name__}"]
        out = []
        for k, v in old.items():
            if k not in new:
                out.append(f"{path}/{k}: Feld fehlt nach dem Update")
            else:
                out += additive_diff(v, new[k], f"{path}/{k}", fill)
        return out
    if isinstance(old, list):
        if not isinstance(new, list):
            return [f"{path}: Liste wurde {type(new).__name__}"]
        if len(old) != len(new):
            return [f"{path}: Listenlänge {len(old)} -> {len(new)}"]
        out = []
        for i, (a, b) in enumerate(zip(old, new)):
            out += additive_diff(a, b, f"{path}[{i}]", fill)
        return out
    if old == new or (path in fill and old in ("", "#1f5eff", "Arial")):
        return []
    return [f"{path}: {old!r} -> {new!r}"]


# Metadaten, die eine Migration beim Start setzen darf (kein Fachinhalt).
STATE_META_COLUMNS = {"updated_at", "updated_by"}
SKIP_TABLES = {"sessions"}


def dump_db(db_path: Path) -> dict:
    """Alle Tabellen (ohne Sitzungen) als {tabelle: {key: {spalte: wert}}}; JSON-Spalten sind geparst."""
    tmp = Path(tempfile.mkdtemp(prefix="mp-dump-"))
    try:
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db_path) + suffix)
            if src.exists():
                shutil.copy2(src, tmp / ("db.sqlite3" + suffix))
        con = sqlite3.connect(tmp / "db.sqlite3")
        con.row_factory = sqlite3.Row
        try:
            out = {}
            tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
            for t in tables:
                if t in SKIP_TABLES:
                    continue
                cols = con.execute(f"PRAGMA table_info({t})").fetchall()
                pk = [c["name"] for c in sorted(cols, key=lambda c: c["pk"]) if c["pk"]] or ["rowid"]
                rows = {}
                for r in con.execute(f"SELECT rowid AS rowid, * FROM {t}"):
                    d = dict(r)
                    if isinstance(d.get("json"), str):
                        try:
                            d["json"] = json.loads(d["json"])
                        except ValueError:
                            pass
                    key = "|".join(str(d[k]) for k in pk)
                    if pk != ["rowid"]:
                        d.pop("rowid", None)
                    rows[key] = d
                out[t] = rows
            return out
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def compare_dumps(a: dict, b: dict, exact: bool = False) -> list[str]:
    """exact=False: a ist Teilmenge von b (nur neue Felder; keine Zeile weniger/mehr, nichts geändert).
    exact=True: identisch (zweites Update)."""
    problems = []
    for t, rows in a.items():
        if t not in b:
            problems.append(f"Tabelle {t} fehlt")
            continue
        if len(rows) != len(b[t]):
            problems.append(f"Tabelle {t}: {len(rows)} -> {len(b[t])} Zeilen")
        for key, row in rows.items():
            nrow = b[t].get(key)
            if nrow is None:
                problems.append(f"Tabelle {t}: Zeile {key} fehlt")
                continue
            for col, val in row.items():
                if t == "state" and col in STATE_META_COLUMNS and not exact:
                    continue
                if col not in nrow:
                    problems.append(f"{t}[{key}].{col} fehlt")
                elif exact:
                    if val != nrow[col]:
                        problems.append(f"{t}[{key}].{col} geändert")
                elif isinstance(val, (dict, list)):
                    problems += [f"{t}[{key}].json{p}" for p in additive_diff(val, nrow[col])]
                elif val != nrow[col]:
                    problems.append(f"{t}[{key}].{col}: {str(val)[:60]!r} -> {str(nrow[col])[:60]!r}")
            if exact:
                extra = set(nrow) - set(row)
                if extra:
                    problems.append(f"{t}[{key}]: neue Spalten {sorted(extra)}")
    if exact:
        for t in b:
            if t not in a:
                problems.append(f"Tabelle {t} neu")
    return problems


# --------------------------------------------------------------------------------------------
# HTTP-API
# --------------------------------------------------------------------------------------------


class Api:
    def __init__(self, url: str):
        u = re.match(r"^https?://([^:/]+):(\d+)", url)
        if not u:
            raise ValueError(f"URL ungültig: {url}")
        self.host, self.port = u.group(1), int(u.group(2))
        self.cookie = ""
        self.version = ""

    def call(self, method: str, path: str, body=None, version: str | None = None):
        c = http.client.HTTPConnection(self.host, self.port, timeout=30)
        h = {"Content-Type": "application/json", "X-MP-Client-Version": version or self.version,
             "Origin": f"http://{self.host}:{self.port}"}
        if self.cookie:
            h["Cookie"] = self.cookie
        try:
            c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
            r = c.getresponse()
            data = r.read()
            sc = (r.getheader("Set-Cookie") or "").split(";")[0]
            if sc.startswith("mp_session=") and sc != "mp_session=":
                self.cookie = sc
            ctype = r.getheader("Content-Type") or ""
            return r.status, (json.loads(data or b"{}") if "json" in ctype else data)
        finally:
            c.close()

    def health(self) -> str:
        s, d = self.call("GET", "/api/health")
        self.version = str(d.get("version", "")) if s == 200 and isinstance(d, dict) else ""
        return self.version

    def login(self, user: str, pw: str) -> int:
        self.cookie = ""
        if not self.version:
            self.health()
        return self.call("POST", "/api/login", {"username": user, "password": pw})[0]


def png_bytes(seed: int, size: int = 48) -> bytes:
    """Kleines, gültiges, je Seed verschiedenes PNG (RGB)."""
    rows = b""
    for y in range(size):
        rows += b"\x00" + b"".join(bytes(((x * 5 + seed) % 256, (y * 5 + seed * 3) % 256, (x + y + seed * 7) % 256)) for x in range(size))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


CI_VALUE = {"company": "Muster Metallbau GmbH", "address": "Hauptstraße 1 · 12345 Musterstadt", "footer": "Wir liefern pünktlich.",
            "font": "Georgia", "color": "#C0392B"}
ACCENT = "#2A9D8F"
USERS = [  # (Benutzer, Rolle, Bereich)
    ("gf1", "gf", ""), ("lead_cnc", "department_lead", "cnc"), ("viewer1", "viewer", ""),
    ("pm1", "project_management", ""), ("av1", "production_planning", ""), ("sales1", "sales", ""),
    ("deputy_tf", "department_deputy", "thermoforming"), ("ehemalig", "viewer", ""),
]


class SeedError(Exception):
    pass


def _expect(cond, what):
    if not cond:
        raise SeedError(what)


def seed(url: str, admin_user: str, admin_pw: str, scenario: str = "ci") -> dict:
    """Legt realistische Daten über die API an. scenario 'ci': Firmen-CI (Name, Farbe, Logo) in data.ci; 'noci': keine."""
    adm = Api(url)
    ver = adm.health()
    _expect(adm.login(admin_user, admin_pw) == 200, "Admin-Login")
    rec = {"version": ver, "scenario": scenario, "adminUser": admin_user, "adminPassword": admin_pw, "users": [], "features": {}}
    # --- Benutzer (inkl. geändertes Passwort und deaktivierter Benutzer) ---
    ids = {}
    for i, (name, role, dep) in enumerate(USERS):
        pw = f"Pässwort-{i}-Ü#2026"
        s, d = adm.call("POST", "/api/users", {"username": name, "password": pw, "role": role, "departmentId": dep})
        if s != 201:
            continue
        ids[name] = d.get("id")
        rec["users"].append({"username": name, "password": pw, "role": role, "departmentId": dep, "active": True})
    _expect(len(rec["users"]) >= 5, "Benutzer angelegt")
    by = {u["username"]: u for u in rec["users"]}
    if "viewer1" in by:
        c = Api(url)
        c.version = ver
        _expect(c.login("viewer1", by["viewer1"]["password"]) == 200, "viewer1-Login")
        newpw = "Neues-Passwort-ß-77"
        s, _ = c.call("POST", "/api/password", {"currentPassword": by["viewer1"]["password"], "newPassword": newpw})
        _expect(s == 200, "Passwort ändern")
        by["viewer1"]["password"] = newpw
    if "ehemalig" in by:
        s, _ = adm.call("PATCH", f"/api/users/{ids['ehemalig']}", {"role": "viewer", "departmentId": "", "active": False})
        _expect(s == 200, "Benutzer deaktivieren")
        by["ehemalig"]["active"] = False
    # --- Planungsdaten ---
    s, st = adm.call("GET", "/api/state")
    _expect(s == 200, "Datenstand lesen")
    new = copy.deepcopy(st["data"])
    # Neutraler Startbestand (ab V12.17.0 ohne Bereiche/Maschinen): Testbereich CNC mit drei Maschinen ergaenzen.
    if not [m for m in new["machines"] if m.get("departmentId") == "cnc"]:
        if not any(d.get("id") == "cnc" for d in new.get("departments", [])):
            new.setdefault("departments", []).append({"id": "cnc", "name": "CNC", "planningType": "MACHINE", "active": True, "sharedOperators": True})
        for i in (1, 2, 3):
            new["machines"].append({"id": f"m{i}", "name": f"Maschine {i}", "departmentId": "cnc", "setupMinutes": 0, "start": "2026-09-07T06:30",
                                    "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 1})
    machines = new["machines"]
    cnc = [m["id"] for m in machines if m.get("departmentId") == "cnc"]
    tf = [m["id"] for m in machines if m.get("departmentId") == "thermoforming"]
    _expect(len(cnc) >= 2, "CNC-Maschinen im Datenstand")

    def step(i, dep, mid, **kw):
        fs = f"FS 80{i:02d}"
        x = {"id": f"ws_up{i}", "sequence": i * 10, "planningType": "MACHINE", "pos": i * 10, "departmentId": dep, "projectId": "", "predecessorIds": [],
             "fs": fs, "ab": "AB-777", "wt": "", "machineId": mid, "altMachineId": "", "allowAlternative": False, "order": fs, "articleNo": f"Ä-{i}",
             "description": f"Gehäuse Größe {i} (Übergröße)", "targetQty": 20 + i, "dueDate": "2026-11-20", "baselinePlan": None, "hours": 4.5 + i,
             "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none", "requiredStart": "", "requiredFinish": "",
             "createdAt": "2026-10-01T08:00:00Z", "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "", "pausedAt": "",
             "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": ""}
        x.update(kw)
        return x

    new["workSteps"] = [step(1, "cnc", cnc[0]), step(2, "cnc", cnc[1], status="planned"), step(3, "cnc", cnc[0], sequence=30)]
    if tf:
        new["workSteps"].append(step(4, "thermoforming", tf[0]))
    new["employees"] = [{"id": "e_up1", "name": "Max Fräser", "departmentId": "cnc", "active": True, "skills": cnc[:1]},
                        {"id": "e_up2", "name": "Eva Dreher-Müller", "departmentId": "cnc", "active": True, "skills": cnc[:2]}]
    new["projects"] = [{"id": "p_up1", "number": "WT-2026-001", "phase": "accepted", "name": "Gehäuse", "customer": "Kunde Ä", "ab": "AB-777",
                        "dueDate": "2026-11-30", "log": [], "processes": [{"id": "pr_up1", "areaId": "cnc", "title": "Fräsen", "status": "open", "startDate": "2026-10-12", "dueDate": "2026-10-16"}]},
                       {"id": "p_up2", "number": "WT-2026-002", "phase": "inquiry", "name": "Anfrage Schale", "customer": "Kunde B", "ab": "", "dueDate": "", "log": [], "processes": []}]
    new["personnelAbsences"] = [{"employeeId": "e_up1", "date": "2026-11-02", "label": "Urlaub"}, {"employeeId": "e_up2", "date": "2026-11-03", "label": "Krank"}]
    if "baseFormats" in new and tf:
        new["baseFormats"] = [{"id": "bf_up1", "departmentId": "thermoforming", "name": "Groß", "L": 1500, "B": 1000}]
        new["formats"] = [{"id": "fm_up1", "number": "F-0001", "departmentId": "thermoforming", "name": "Schale 'Ü'", "status": "active", "baseId": "bf_up1",
                           "machineId": tf[0], "L": 1500, "B": 1000, "H": 60, "rand": 10, "log": [],
                           "tools": [{"id": "t_up1", "wkz": "WKZ-100", "fs": "FS 8004", "order": "FS 8004", "article": "Ä-4", "l": 300, "b": 200, "h": 40, "n": 2, "qty": 10}]}]
        rec["features"]["formats"] = True
    new.setdefault("ui", {})["accent"] = ACCENT
    rec["accent"] = ACCENT
    if scenario == "ci":
        logo = "data:image/png;base64," + base64.b64encode(png_bytes(11)).decode()
        new["ci"] = dict(CI_VALUE, logo=logo)
        rec["ci"] = new["ci"]
    s, d = adm.call("PUT", "/api/state", {"revision": st["revision"], "data": new, "action": "Upgrade-Test Daten", "detail": ""})
    _expect(s == 200, f"Datenstand speichern: {s} {d}")
    rev = d["revision"]
    # zweiter Schreibvorgang: Änderung + weitere Abwesenheit (erzeugt Benachrichtigungen, Revision 3)
    new2 = copy.deepcopy(d["data"])
    new2["workSteps"][0]["description"] += " (Änderung)"
    new2["personnelAbsences"].append({"employeeId": "e_up1", "date": "2026-11-04", "label": "Urlaub"})
    s, d = adm.call("PUT", "/api/state", {"revision": rev, "data": new2, "action": "Upgrade-Test Änderung", "detail": "ws_up1"})
    _expect(s == 200, f"zweiter Speichervorgang: {s} {d}")
    # --- Firmenprofil: ab V12.15.0 pflegt der Admin es über /api/config (vorher nur data.ci/data.ui) ---
    s, cfg = adm.call("GET", "/api/config")
    if s == 200 and isinstance(cfg, dict):
        co = {"uiAccent": ACCENT}
        if scenario == "ci":
            co.update({"name": CI_VALUE["company"], "color": CI_VALUE["color"], "address": CI_VALUE["address"], "footer": CI_VALUE["footer"], "font": CI_VALUE["font"]})
        s, r = adm.call("PATCH", "/api/config", {"revision": cfg["revision"], "company": co})
        _expect(s == 200, f"Firmenprofil per API setzen: {s} {r}")
        if scenario == "ci":
            s, r = adm.call("POST", "/api/config/logo", {"revision": r["config"]["revision"], "contentType": "image/png",
                                                         "data": rec["ci"]["logo"].split(",", 1)[1]})
            _expect(s == 200, f"Logo per API setzen: {s} {r}")
        rec["features"]["configApi"] = True
    # --- Chat ---
    s, _ = adm.call("GET", "/api/chat/channels")
    if s == 200 and "lead_cnc" in by and "viewer1" in by:
        s, g = adm.call("POST", "/api/chat/channels", {"kind": "group", "name": "Frühschicht Ü", "members": ["lead_cnc", "viewer1"]})
        if s == 201:
            cid = g["channel"]["id"]
            adm.call("POST", "/api/chat/messages", {"channel": cid, "text": "Guten Morgen, Auftrag FS 8001 läuft ⟦u:viewer1⟧"})
            lead = Api(url)
            lead.version = ver
            if lead.login("lead_cnc", by["lead_cnc"]["password"]) == 200:
                lead.call("POST", "/api/chat/messages", {"channel": cid, "text": "Verstanden – Größe 1 ist fertig."})
            s, dm = adm.call("POST", "/api/chat/channels", {"kind": "direct", "members": ["pm1"]})
            if s in (200, 201):
                adm.call("POST", "/api/chat/messages", {"channel": dm["channel"]["id"], "text": "Direkt: Termin AB-777 bestätigt."})
            rec["features"]["chat"] = True
    # --- Benachrichtigungen ---
    if "viewer1" in by:
        v = Api(url)
        v.version = ver
        if v.login("viewer1", by["viewer1"]["password"]) == 200:
            s, _ = v.call("PUT", "/api/notifications/prefs", {"quiet": {"on": True, "from": "21:00", "to": "05:30"}})
            if s == 200:
                rec["features"]["notifications"] = True
    return rec


# --------------------------------------------------------------------------------------------
# Fingerabdruck über die API (für das echte Windows-Deploy, wo die Datenbank nicht direkt gelesen wird)
# --------------------------------------------------------------------------------------------
API_VOLATILE = {"unread", "mentions", "readId", "updatedAt", "sig", "since", "crev", "revision"}


def _strip(o):
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if k not in API_VOLATILE}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


def fingerprint(url: str, rec: dict) -> dict:
    fp = {"logins": {}, "per_user": {}}
    adm = Api(url)
    adm.health()
    for u in [{"username": rec["adminUser"], "password": rec["adminPassword"], "active": True}] + rec["users"]:
        a = Api(url)
        a.version = adm.version
        code = a.login(u["username"], u["password"])
        fp["logins"][u["username"]] = code
        if code != 200:
            continue
        per = {}
        s, d = a.call("GET", "/api/state")
        if s == 200:
            per["state"] = _strip(d["data"]) if u["username"] == rec["adminUser"] else None
            if per["state"] is None:
                del per["state"]
        if u["username"] == rec["adminUser"]:
            s, d = a.call("GET", "/api/users")
            if s == 200:
                per["users"] = d
        s, d = a.call("GET", "/api/chat/channels")
        if s == 200:
            per["channels"] = _strip(d.get("channels"))
            per["messages"] = {}
            for ch in d.get("channels") or []:
                s2, m = a.call("GET", f"/api/chat/messages?channel={ch['id']}")
                if s2 == 200:
                    per["messages"][str(ch["id"])] = _strip(m.get("messages"))
        s, d = a.call("GET", "/api/notifications")
        if s == 200:
            per["notifications"] = _strip(d.get("items"))
            per["prefs"] = d.get("prefs")
        fp["per_user"][u["username"]] = per
    return fp


def verify(url: str, rec: dict, fp_old: dict, expect_ci_config: bool = True, company: bool = True) -> list[str]:
    """Nach dem Update: gleiche Anmeldungen, alle Daten additiv gleich, Firmenprofil entspricht dem Bestand."""
    problems = []
    fp_new = fingerprint(url, rec)
    for u, code in fp_old["logins"].items():
        if fp_new["logins"].get(u) != code:
            problems.append(f"Login {u}: {code} -> {fp_new['logins'].get(u)}")
    problems += [f"Daten{p}" for p in additive_diff(fp_old["per_user"], fp_new["per_user"])]
    if company:
        problems += check_company(url, rec, expect_ci_config)
    return problems


def check_company(url: str, rec: dict, expect_ci_config: bool = True, template: dict | None = None) -> list[str]:
    """Anzeige-Name, Farbe, Logo und UI-Akzent gleich wie vorher (aus data.ci bzw. der Vorlage)."""
    out = []
    a = Api(url)
    a.health()
    if a.login(rec["adminUser"], rec["adminPassword"]) != 200:
        return ["Admin-Login für Firmenprofil-Prüfung fehlgeschlagen"]
    s, cfg = a.call("GET", "/api/config")
    if s != 200:
        return [f"/api/config: HTTP {s}"]
    co = cfg.get("company", {})
    ci = rec.get("ci")
    if ci:
        if co.get("name") != ci["company"]:
            out.append(f"Firmenname {co.get('name')!r} != {ci['company']!r}")
        if str(co.get("color", "")).lower() != ci["color"].lower():
            out.append(f"Firmenfarbe {co.get('color')!r} != {ci['color']!r}")
        s, raw = a.call("GET", "/api/config/logo")
        want = base64.b64decode(ci["logo"].split(",", 1)[1])
        if s != 200 or raw != want:
            out.append(f"Logo weicht ab (HTTP {s})")
        if co.get("address") != ci["address"] or co.get("footer") != ci["footer"]:
            out.append("Adresse/Fußzeile aus data.ci nicht übernommen")
    if template:
        tc = template.get("company", {})
        if co.get("name") != tc.get("name"):
            out.append(f"Firmenname {co.get('name')!r} != Vorlage {tc.get('name')!r}")
        if tc.get("color") and str(co.get("color", "")).lower() != tc["color"].lower():
            out.append("Firmenfarbe weicht von der Vorlage ab")
        if template.get("logoDataUrl"):
            s, raw = a.call("GET", "/api/config/logo")
            if s != 200 or raw != base64.b64decode(template["logoDataUrl"].split(",", 1)[1]):
                out.append("Logo weicht von der Vorlage ab")
    if rec.get("accent") and str(co.get("uiAccent", "")).lower() != rec["accent"].lower():
        out.append(f"UI-Akzent {co.get('uiAccent')!r} != {rec['accent']!r}")
    return out


# --------------------------------------------------------------------------------------------
# Hilfen für Server-Prozesse (Dateisystem-Test)
# --------------------------------------------------------------------------------------------


def find_lan_ip() -> str | None:
    """Eine echte (nicht-Loopback) IPv4-Adresse dieses Rechners; der Server bindet nie an 127.x."""
    cands = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))
        cands.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        cands += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    for ip in cands:
        if ip and not ip.startswith("127.") and not ip.startswith("0."):
            return ip
    return None


def free_port(ip: str) -> int:
    s = socket.socket()
    s.bind((ip, 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("seed")
    a.add_argument("--url", required=True); a.add_argument("--admin-user", default="admin"); a.add_argument("--admin-password", required=True)
    a.add_argument("--scenario", default="ci"); a.add_argument("--out", required=True)
    b = sub.add_parser("fingerprint")
    b.add_argument("--url", required=True); b.add_argument("--seed", required=True); b.add_argument("--out", required=True)
    c = sub.add_parser("verify")
    c.add_argument("--url", required=True); c.add_argument("--seed", required=True); c.add_argument("--fingerprint", required=True)
    c.add_argument("--template")
    c.add_argument("--no-company", action="store_true", help="Firmenprofil nicht prüfen (alter Server ohne /api/config)")
    d = sub.add_parser("same")
    d.add_argument("--a", required=True); d.add_argument("--b", required=True)
    args = ap.parse_args()
    if args.cmd == "seed":
        rec = seed(args.url, args.admin_user, args.admin_password, args.scenario)
        Path(args.out).write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Testdaten angelegt: {len(rec['users'])} Benutzer, Funktionen {sorted(rec['features'])}")
        return 0
    if args.cmd == "same":
        a = json.loads(Path(args.a).read_text(encoding="utf-8"))
        b = json.loads(Path(args.b).read_text(encoding="utf-8"))
        print("PASS Fingerabdrücke identisch" if a == b else "FAIL Fingerabdrücke unterscheiden sich: " + "; ".join(additive_diff(a, b)[:8] or ["(nur neue Felder)"]))
        return 0 if a == b else 1
    rec = json.loads(Path(args.seed).read_text(encoding="utf-8"))
    if args.cmd == "fingerprint":
        Path(args.out).write_text(json.dumps(fingerprint(args.url, rec), ensure_ascii=False, indent=1), encoding="utf-8")
        print("Fingerabdruck geschrieben")
        return 0
    old = json.loads(Path(args.fingerprint).read_text(encoding="utf-8"))
    tpl = json.loads(Path(args.template).read_text(encoding="utf-8-sig")) if args.template else None
    problems = verify(args.url, rec, old, company=not args.no_company)
    if tpl:
        problems += check_company(args.url, dict(rec, ci=None), True, tpl)
    for p in problems:
        print("FAIL " + p)
    print("PASS Daten nach dem Update vollständig und gleich" if not problems else f"{len(problems)} Abweichung(en)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

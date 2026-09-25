#!/usr/bin/env python3
"""Produktionsplanung V12 LAN server.
Standard library only: ThreadingHTTPServer + SQLite + PBKDF2 sessions.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import math
import ipaddress
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_VERSION = "12.7.1"
HOST = os.environ.get("MP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MP_PORT", "8765"))
BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    # Anhängen statt voranstellen: Standardbibliothek hat immer Vorrang vor Dateien im Programmordner.
    sys.path.append(str(BASE))
from release_gates import validate_release_feasibility

DATA_DIR = BASE / "data"
DB_PATH = DATA_DIR / "maschinenplanung.sqlite3"
INDEX_PATH = BASE / "index.html"
SESSION_TTL = 12 * 60 * 60
MAX_BODY = 8 * 1024 * 1024
PBKDF2_ITERS = 310_000
DB_LOCK = threading.RLock()
REVISION_CONDITION = threading.Condition()
LOGIN_LOCK = threading.Lock()
LOGIN_FAILS: dict[str, list[float]] = {}

ROLES = {"admin", "gf", "department_lead", "department_deputy", "viewer", "project_management", "production_planning", "sales"}
WRITE_ROLES = {"admin", "gf", "department_lead", "department_deputy", "project_management", "production_planning", "sales"}
DEPARTMENT_ROLES = {"department_lead", "department_deputy"}
USER_MANAGER_ROLES = {"admin", "department_lead", "department_deputy"}
LOCAL_USER_ROLES = {"department_lead", "department_deputy", "viewer"}
# Welche Rollen eine Bereichsrolle im eigenen Bereich anlegen/ändern darf.
# Leitungen verwalten Stellvertretungen und Lesende; Stellvertretungen nur Lesende.
# Leitungskonten selbst verwaltet ausschließlich der Admin.
MANAGEABLE_ROLES = {
    "department_lead": {"department_deputy", "viewer"},
    "department_deputy": {"viewer"},
}
ALLOWED_NETWORK = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=15000")
    return con


@contextmanager
def db_session():
    """Open a connection, commit/rollback like ``with con`` and always close it."""
    con = db()
    try:
        with con:
            yield con
    finally:
        con.close()


def migrate_users_schema(con: sqlite3.Connection) -> None:
    """Migrate an existing V11 users table without changing users or privileges.

    V11 had no department_id and its CHECK constraint only knew
    admin/planner/production/viewer. V12.4 introduced a new role model but
    CREATE TABLE IF NOT EXISTS cannot upgrade an existing SQLite table.
    Legacy planner/production roles are preserved deliberately and remain
    fail-closed in V12 until an admin explicitly assigns a V12 role.
    Sessions are ephemeral and are reset because they reference users.
    """
    cols = {row["name"] for row in con.execute("PRAGMA table_info(users)")}
    table = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    create_sql = str(table["sql"] or "") if table else ""
    required_roles = ("'gf'", "'department_lead'", "'department_deputy'", "'project_management'", "'production_planning'", "'sales'")
    if "department_id" in cols and all(role in create_sql for role in required_roles):
        return

    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute("DROP TABLE IF EXISTS sessions")
        con.execute("ALTER TABLE users RENAME TO users_legacy_v124")
        legacy_cols = {row["name"] for row in con.execute("PRAGMA table_info(users_legacy_v124)")}
        con.execute(
            """CREATE TABLE users(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL UNIQUE COLLATE NOCASE,
              salt TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('admin','gf','department_lead','department_deputy','viewer','project_management','production_planning','sales','planner','production')),
              department_id TEXT NOT NULL DEFAULT '',
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )"""
        )
        department_expr = "department_id" if "department_id" in legacy_cols else "''"
        con.execute(
            f"""INSERT INTO users(id,username,salt,password_hash,role,department_id,active,created_at,updated_at)
                SELECT id,username,salt,password_hash,role,{department_expr},active,created_at,updated_at
                FROM users_legacy_v124"""
        )
        con.execute("DROP TABLE users_legacy_v124")
        con.execute(
            """CREATE TABLE sessions(
              token_hash TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires_at INTEGER NOT NULL,
              created_at TEXT NOT NULL
            )"""
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    print("DB-MIGRATION users: Rollenschema aktualisiert (u. a. Arbeitsvorbereitung); Sitzungen zurückgesetzt")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with DB_LOCK, db_session() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL UNIQUE COLLATE NOCASE,
              salt TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('admin','gf','department_lead','department_deputy','viewer','project_management','production_planning','sales','planner','production')),
              department_id TEXT NOT NULL DEFAULT '',
              active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions(
              token_hash TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires_at INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state(
              id INTEGER PRIMARY KEY CHECK(id=1),
              revision INTEGER NOT NULL,
              json TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              updated_by TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS server_audit(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts TEXT NOT NULL,
              username TEXT NOT NULL,
              action TEXT NOT NULL,
              detail TEXT NOT NULL,
              revision INTEGER
            );
            """
        )
        migrate_users_schema(con)
        row = con.execute("SELECT id FROM state WHERE id=1").fetchone()
        if not row:
            initial = {
                "version": 12,
                "meta": {"revision": 1, "actor": "Server", "storage": "server", "createdAt": now_iso(), "serverReady": True},
                "machines": [
                    {"id": "m1", "name": "Maschine 1", "departmentId": "cnc", "setupMinutes": 0, "start": "2026-09-07T06:30", "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 1},
                    {"id": "m2", "name": "Maschine 2", "departmentId": "cnc", "setupMinutes": 0, "start": "2026-09-07T06:30", "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 1},
                    {"id": "m3", "name": "Maschine 3", "departmentId": "cnc", "setupMinutes": 0, "start": "2026-09-07T06:30", "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 1},
                ],
                "shiftTemplates": {
                    "single": {"name": "1-Schicht Mo–Do", "start": "06:30", "end": "16:00", "breaks": [{"start": "09:00", "end": "09:15"}, {"start": "12:00", "end": "12:30"}]},
                    "fridaySingle": {"name": "1-Schicht Freitag", "start": "06:30", "end": "11:45", "breaks": [{"start": "09:00", "end": "09:15"}, {"start": "", "end": ""}]},
                    "early": {"name": "2-Schicht · Früh", "start": "06:00", "end": "14:30", "breaks": [{"start": "09:00", "end": "09:15"}, {"start": "12:00", "end": "12:15"}]},
                    "late": {"name": "2-Schicht · Spät", "start": "14:30", "end": "22:30", "breaks": [{"start": "17:00", "end": "17:15"}, {"start": "19:00", "end": "19:15"}]},
                },
                "departments": [
                    {"id": "cnc", "name": "CNC", "planningType": "MACHINE", "active": True},
                    {"id": "konf1", "name": "Konfektion 1 – Thomsen", "planningType": "LABOR_HOURS", "active": True},
                    {"id": "konf2", "name": "Konfektion 2 – Keller", "planningType": "LABOR_HOURS", "active": True},
                    {"id": "konf3", "name": "Konfektion 3", "planningType": "LABOR_HOURS", "active": True},
                    {"id": "screenprint", "name": "Siebdruck", "planningType": "PROCESS", "active": True},
                    {"id": "thermoforming", "name": "Tiefziehen", "planningType": "CYCLE", "active": True}
                ],
                "projects": [],
                "workSteps": [],
                "yearRules": [], "weekRules": [], "exceptions": [],
                "operatorCapacity": {"single": 3, "early": 3, "late": 3},
                "employees": [], "personnelAssignments": [], "personnelAbsences": [], "weeklyEmployeeDeployments": [], "departmentStaffNeeds": [], "personnelGate": False,
                "history": [], "audit": [], "planVersions": [],
                "ui": {"accent": "#1f5eff", "cellH": 90},
            }
            con.execute("INSERT INTO state(id,revision,json,updated_at,updated_by) VALUES(1,1,?,?,?)", (json.dumps(initial, ensure_ascii=False), now_iso(), "Server"))
        migrate_state_v1242(con)
        migrate_state_v1243(con)
        migrate_state_v1244(con)
        migrate_state_v1260(con)
        migrate_state_v1261(con)
        migrate_state_v1270(con)
        normalize_state_v1270(con)


def migrate_state_v1242(con: sqlite3.Connection) -> None:
    row = con.execute("SELECT revision,json FROM state WHERE id=1").fetchone()
    if not row:
        return
    raw_json = row["json"] if hasattr(row, "keys") else row[1]
    state = json.loads(raw_json)
    changed = False
    for key in ("weeklyEmployeeDeployments", "departmentStaffNeeds"):
        if not isinstance(state.get(key), list):
            state[key] = []
            changed = True
    if changed:
        con.execute("UPDATE state SET json=?,updated_at=?,updated_by=? WHERE id=1",
                    (json.dumps(state, ensure_ascii=False, separators=(",", ":")), now_iso(), "V12.4.2 migration"))
        print("DB-MIGRATION state: V12.4.2 workforce arrays added")


def migrate_state_v1243(con: sqlite3.Connection) -> None:
    row = con.execute("SELECT revision,json FROM state WHERE id=1").fetchone()
    if not row:
        return
    raw = row["json"] if hasattr(row, "keys") else row[1]
    state = json.loads(raw)
    changed = False
    if not isinstance(state.get("workSteps"), list):
        machines = {str(m.get("id")): m for m in (state.get("machines") or []) if isinstance(m, dict)}
        machine_ids = list(machines)
        first_machine = machine_ids[0] if machine_ids else ""
        steps = []
        valid_status = {"planned", "released", "running", "paused"}
        for i, old in enumerate(state.get("orders") or []):
            if not isinstance(old, dict):
                continue
            x = dict(old)
            mid = str(x.get("machineId") or first_machine)
            if mid not in machines:
                mid = first_machine
            alt = str(x.get("altMachineId") or "")
            if alt not in machines or alt == mid:
                alt = ""
            order = str(x.get("order") or "").strip()
            fs = str(x.get("fs") or x.get("fertigungsauftrag") or x.get("fertigungsschein") or order).strip()
            try:
                seq = float(x.get("sequence") or x.get("pos") or ((i + 1) * 10))
            except Exception:
                seq = float((i + 1) * 10)
            x.update({
                "id": str(x.get("id") or f"legacy_o_{i+1}"),
                "planningType": "MACHINE", "sequence": seq,
                "predecessorIds": list(x.get("predecessorIds") or []),
                "departmentId": str(x.get("departmentId") or (machines.get(mid) or {}).get("departmentId") or "cnc"),
                "projectId": str(x.get("projectId") or ""),
                "fs": fs, "ab": str(x.get("ab") or ""), "wt": str(x.get("wt") or ""),
                "machineId": mid, "altMachineId": alt, "allowAlternative": bool(alt),
                "order": order or fs, "status": str(x.get("status") or "planned") if str(x.get("status") or "planned") in valid_status else "planned",
            })
            steps.append(x)
        dept_types = {str(d.get("id")): str(d.get("planningType") or "LABOR_HOURS") for d in (state.get("departments") or []) if isinstance(d, dict)}
        for j, old in enumerate(state.get("departmentPlans") or []):
            if not isinstance(old, dict):
                continue
            x = dict(old); did = str(x.get("departmentId") or "")
            x.update({"id": str(x.get("id") or f"legacy_dp_{j+1}"), "planningType": str(x.get("planningType") or dept_types.get(did) or "LABOR_HOURS"), "sequence": float(x.get("sequence") or ((len(steps)+j+1)*10)), "predecessorIds": list(x.get("predecessorIds") or []), "projectId": str(x.get("projectId") or ""), "fs": str(x.get("fs") or x.get("order") or ""), "ab": str(x.get("ab") or ""), "wt": str(x.get("wt") or ""), "status": str(x.get("status") or "planned")})
            steps.append(x)
        state["workSteps"] = steps
        changed = True
        print(f"DB-MIGRATION state: legacy orders/departmentPlans -> workSteps ({len(steps)})")
    projects = {str(p.get("id")): p for p in (state.get("projects") or []) if isinstance(p, dict)}
    for step in (state.get("workSteps") or []):
        if not isinstance(step, dict):
            continue
        if step.get("planningType") == "MACHINE" and not str(step.get("fs") or "").strip():
            step["fs"] = str(step.get("order") or "").strip()
            changed = True
        pid = str(step.get("projectId") or "")
        linked = projects.get(pid)
        if linked:
            if not str(step.get("ab") or "").strip():
                step["ab"] = str(linked.get("ab") or "").strip(); changed = True
            if not str(step.get("wt") or "").strip() and linked.get("wt"):
                step["wt"] = str(linked.get("wt") or "").strip(); changed = True
    if changed:
        con.execute("UPDATE state SET json=?,updated_at=?,updated_by=? WHERE id=1", (json.dumps(state, ensure_ascii=False, separators=(",", ":")), now_iso(), "V12.4.3 hierarchy migration"))
        print("DB-MIGRATION state: V12.4.3 FS/AB/WT hierarchy normalized")


def migrate_state_v1244(con: sqlite3.Connection) -> None:
    """Retire the legacy ``orders`` shadow array (MP-AUD-012/025).

    V12.4.3 kept the pre-V12 ``orders`` list in the state although only
    ``workSteps`` is maintained. The stale copy could be resurrected by old
    code or restores. It is archived verbatim in ``legacy_orders_archive`` and
    removed from the live state – but only if every legacy ID exists in
    ``workSteps``. Otherwise the server refuses to start instead of silently
    dropping data.
    """
    row = con.execute("SELECT revision,json FROM state WHERE id=1").fetchone()
    if not row:
        return
    state = json.loads(row["json"])
    if "orders" not in state:
        return
    legacy = state.get("orders")
    steps = state.get("workSteps")
    if not isinstance(steps, list):
        raise SystemExit("FEHLER MP-MIG-001: workSteps fehlt; Legacy-orders können nicht sicher stillgelegt werden.")
    step_ids = {str(x.get("id")) for x in steps if isinstance(x, dict)}
    legacy_ids = {str(x.get("id")) for x in (legacy or []) if isinstance(x, dict) and x.get("id")}
    missing = sorted(legacy_ids - step_ids)
    if missing:
        raise SystemExit(f"FEHLER MP-MIG-002: {len(missing)} Legacy-Auftrag/Aufträge fehlen in workSteps (z. B. {missing[0]}). Migration abgebrochen, Daten unverändert.")
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(
            """CREATE TABLE IF NOT EXISTS legacy_orders_archive(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              archived_at TEXT NOT NULL,
              state_revision INTEGER NOT NULL,
              json TEXT NOT NULL
            )"""
        )
        con.execute(
            "INSERT INTO legacy_orders_archive(archived_at,state_revision,json) VALUES(?,?,?)",
            (now_iso(), int(row["revision"]), json.dumps(legacy, ensure_ascii=False)),
        )
        del state["orders"]
        con.execute(
            "UPDATE state SET json=?,updated_at=?,updated_by=? WHERE id=1",
            (json.dumps(state, ensure_ascii=False, separators=(",", ":")), now_iso(), "V12.4.4 migration"),
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    print(f"DB-MIGRATION state: V12.4.4 legacy orders ({len(legacy_ids)}) archiviert und aus dem Live-State entfernt")


def migrate_state_v1260(con: sqlite3.Connection) -> None:
    """Projekt-Lebenszyklus: bestehende AB-Datensätze werden zu angenommenen Projekten
    mit Projektnummer; Projekte ohne AB starten als Eingang."""
    row = con.execute("SELECT json FROM state WHERE id=1").fetchone()
    if not row:
        return
    state = json.loads(row["json"])
    projects = state.get("projects")
    if not isinstance(projects, list) or all(isinstance(p, dict) and p.get("phase") and p.get("number") for p in projects):
        return
    used = {str(p.get("number")) for p in projects if isinstance(p, dict) and p.get("number")}
    seq = 0
    for p in projects:
        if not isinstance(p, dict):
            continue
        if not p.get("number"):
            year = str(p.get("createdAt") or now_iso())[:4]
            while True:
                seq += 1
                candidate = f"P-{year}-{seq:04d}"
                if candidate not in used:
                    break
            p["number"] = candidate
            used.add(candidate)
        if not p.get("phase"):
            p["phase"] = "accepted" if str(p.get("ab") or "").strip() else "inquiry"
        p.setdefault("processes", [])
        p.setdefault("log", [])
        p.setdefault("customer", "")
        p.setdefault("dueDate", "")
    con.execute("UPDATE state SET json=?,updated_at=?,updated_by=? WHERE id=1",
                (json.dumps(state, ensure_ascii=False, separators=(",", ":")), now_iso(), "V12.6 migration"))
    print(f"DB-MIGRATION state: V12.6 Projekt-Lebenszyklus ({len(projects)} Projekt/e)")


DEFAULT_PM_TEMPLATES = [
    {"id": "tpl_pm_repeat", "kind": "pm", "name": "Wiederholteil", "steps": [
        {"areaId": "calculation", "title": "Preis / Kalkulation prüfen", "offsetDays": 0, "durationDays": 2},
        {"areaId": "pm", "title": "Angebot erstellen", "offsetDays": 2, "durationDays": 1}]},
    {"id": "tpl_pm_new", "kind": "pm", "name": "Neuteil", "steps": [
        {"areaId": "engineering", "title": "Machbarkeit prüfen", "offsetDays": 0, "durationDays": 3},
        {"areaId": "purchasing", "title": "Material / Zukauf anfragen", "offsetDays": 0, "durationDays": 5},
        {"areaId": "calculation", "title": "Kalkulation", "offsetDays": 3, "durationDays": 3},
        {"areaId": "pm", "title": "Angebot erstellen", "offsetDays": 6, "durationDays": 1}]},
    {"id": "tpl_pm_dev", "kind": "pm", "name": "Entwicklung / Muster", "steps": [
        {"areaId": "engineering", "title": "Konstruktion / Entwicklung", "offsetDays": 0, "durationDays": 10},
        {"areaId": "cnc", "title": "Muster CNC", "offsetDays": 10, "durationDays": 5},
        {"areaId": "konf1", "title": "Muster Konfektion", "offsetDays": 10, "durationDays": 5},
        {"areaId": "calculation", "title": "Kalkulation", "offsetDays": 10, "durationDays": 5},
        {"areaId": "sales", "title": "Kundenfreigabe Muster einholen", "offsetDays": 15, "durationDays": 7},
        {"areaId": "pm", "title": "Angebot erstellen", "offsetDays": 22, "durationDays": 2}]},
]


def migrate_state_v1261(con: sqlite3.Connection) -> None:
    """Prozessabläufe werden Daten: Standard-Abläufe des PM einmalig anlegen."""
    row = con.execute("SELECT json FROM state WHERE id=1").fetchone()
    if not row:
        return
    state = json.loads(row["json"])
    if isinstance(state.get("processTemplates"), list):
        return
    dept_ids = {str(d.get("id")) for d in (state.get("departments") or []) if isinstance(d, dict)}
    fixed = {"sales", "pm", "engineering", "calculation", "purchasing", "quality", "av"}
    templates = []
    for t in DEFAULT_PM_TEMPLATES:
        steps = [dict(st) for st in t["steps"] if st["areaId"] in fixed or st["areaId"] in dept_ids]
        if steps:
            templates.append({**t, "steps": steps, "createdBy": "System", "updatedAt": now_iso()})
    state["processTemplates"] = templates
    # frühere feste Workflow-Schlüssel auf die neuen Ablauf-IDs abbilden
    legacy = {"repeat": "tpl_pm_repeat", "new": "tpl_pm_new", "development": "tpl_pm_dev"}
    for p in state.get("projects") or []:
        if isinstance(p, dict) and p.get("workflow") in legacy:
            p["workflow"] = legacy[p["workflow"]]
    con.execute("UPDATE state SET json=?,updated_at=?,updated_by=? WHERE id=1",
                (json.dumps(state, ensure_ascii=False, separators=(",", ":")), now_iso(), "V12.6.1 migration"))
    print(f"DB-MIGRATION state: V12.6.1 Prozessabläufe angelegt ({len(templates)} PM-Standardabläufe)")


def _legacy_step_hours(x: dict) -> float:
    """Stunden eines alten Bereichs-Arbeitsgangs (Konfektion/Siebdruck/Tiefziehen)."""
    ptype = str(x.get("planningType") or "")
    def num(k, d=0.0):
        v = _finite_float(x.get(k, d))
        return max(0.0, v) if v is not None else d
    if ptype == "CYCLE":
        qty, cycle, parts = num("quantity"), num("cycleSeconds"), max(1.0, num("partsPerCycle", 1.0))
        run = math.ceil(qty / parts) * cycle / 3600.0 if qty and cycle else 0.0
        return run + num("setupMinutes") / 60.0
    return num("requiredHours")


def migrate_state_v1270(con: sqlite3.Connection) -> None:
    """V12.7: Alle Produktionsbereiche werden wie CNC über Ressourcen geplant.

    - Konfektion (Stundenplanung): je Bereich eine Linie mit Besetzung (Personen).
    - Siebdruck/Tiefziehen: eigene Maschinen (falls noch keine existiert, wird eine angelegt).
    - Offene Bereichs-Arbeitsgänge werden zu Wochenplan-Aufträgen (Planwoche -> Startwunsch),
      erledigte/entfallene wandern unverändert lesbar in die Historie.
    """
    row = con.execute("SELECT json FROM state WHERE id=1").fetchone()
    if not row:
        return
    state = json.loads(row["json"])
    departments = [d for d in (state.get("departments") or []) if isinstance(d, dict)]
    legacy = {str(d.get("id")): str(d.get("planningType") or "") for d in departments if str(d.get("planningType") or "") != "MACHINE"}
    if not legacy:
        return
    machines = [m for m in (state.get("machines") or []) if isinstance(m, dict)]
    for m in machines:
        m.setdefault("crew", 1)
    employees = [e for e in (state.get("employees") or []) if isinstance(e, dict)]
    used_mids = {str(m.get("id")) for m in machines}
    start_default = next((str(m.get("start")) for m in machines if m.get("start")), "2026-09-07T06:30")
    dept_machine: dict[str, str] = {}
    created = 0
    for d in departments:
        did = str(d.get("id"))
        if did not in legacy:
            continue
        own = [m for m in machines if str(m.get("departmentId") or "cnc") == did]
        if own:
            dept_machine[did] = str(own[0].get("id"))
        else:
            base = re.sub(r"[^A-Za-z0-9_-]", "", f"res_{did}")[:60] or "res"
            mid, n = base, 1
            while mid in used_mids:
                n += 1
                mid = f"{base}_{n}"
            used_mids.add(mid)
            is_line = legacy[did] == "LABOR_HOURS"
            crew = max(1, sum(1 for e in employees if e.get("active", True) and str(e.get("departmentId")) == did)) if is_line else 1
            if is_line:
                for e in employees:
                    if e.get("active", True) and str(e.get("departmentId")) == did:
                        e["skills"] = list(dict.fromkeys([*(e.get("skills") or []), mid]))
            machines.append({"id": mid, "name": (f"Linie {d.get('name') or did}" if is_line else f"{d.get('name') or did} 1"),
                             "departmentId": did, "kind": "line" if is_line else "machine", "crew": crew,
                             "setupMinutes": 0, "start": start_default, "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 0})
            dept_machine[did] = mid
            created += 1
        d["planningType"] = "MACHINE"
    state["machines"] = machines

    steps = [x for x in (state.get("workSteps") or []) if isinstance(x, dict)]
    positions = [p for p in (_finite_float(x.get("pos")) for x in steps if x.get("planningType") == "MACHINE") if p is not None]
    pos = (max(positions) if positions else 0.0)
    history = state.get("history") if isinstance(state.get("history"), list) else []
    kept, converted, archived = [], 0, 0
    ts = now_iso()
    for x in steps:
        did = str(x.get("departmentId") or "")
        if x.get("planningType") == "MACHINE" or did not in legacy:
            kept.append(x)
            continue
        mid = dept_machine[did]
        hours_total = _legacy_step_hours(x)
        done_h = max(0.0, _finite_float(x.get("doneHours", 0)) or 0.0)
        fs = str(x.get("fs") or x.get("order") or "").strip() or str(x.get("id"))
        common = {"projectId": str(x.get("projectId") or ""), "fs": fs, "ab": str(x.get("ab") or ""), "wt": str(x.get("wt") or ""),
                  "order": fs, "articleNo": "", "description": "", "targetQty": int(max(0, _finite_float(x.get("quantity", 0)) or 0)),
                  "departmentId": did, "machineId": mid, "createdAt": str(x.get("createdAt") or ts)}
        status = str(x.get("status") or "planned")
        if status in {"done", "cancelled"}:
            history.insert(0, {**common, "id": f"h_mig_{x.get('id')}"[:80], "originalOrderId": str(x.get("id")),
                               "status": status, "recordType": status, "machineName": next((str(m.get("name")) for m in machines if str(m.get("id")) == mid), ""),
                               "hours": round(hours_total, 4), "goodQty": 0, "scrapQty": 0, "finishedAt": "", "migratedDoneAt": str(x.get("doneAt") or ""),
                               "actualStartedAt": "", "actualFinishedAt": "", "plannedSegments": [], "actualSegments": [],
                               "migratedFrom": str(x.get("planningType") or ""), "snapshotVersion": 12})
            archived += 1
            continue
        pos += 10
        hours = round(max(0.25, hours_total - done_h), 4)
        week = str(x.get("planningWeek") or "")
        rs = f"{week}T06:30" if _valid_date_key(week) else ""
        kept.append({**common, "id": str(x.get("id")), "planningType": "MACHINE", "sequence": x.get("sequence") or pos,
                     "predecessorIds": list(x.get("predecessorIds") or []), "pos": pos, "altMachineId": "", "allowAlternative": False,
                     "baselinePlan": None, "hours": hours, "goodQty": 0, "scrapQty": 0, "status": "planned",
                     "direction": "forward", "anchorMode": "soft" if rs else "none", "requiredStart": rs, "requiredFinish": "",
                     "dueDate": "", "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "",
                     "pausedAt": "", "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": "",
                     "migratedFrom": str(x.get("planningType") or ""), "migratedDoneHours": done_h})
        converted += 1
    state["workSteps"] = kept
    state["history"] = history
    con.execute("UPDATE state SET json=?,updated_at=?,updated_by=? WHERE id=1",
                (json.dumps(state, ensure_ascii=False, separators=(",", ":")), now_iso(), "V12.7 migration"))
    print(f"DB-MIGRATION state: V12.7 Bereiche auf Ressourcenplanung ({created} Ressource/n angelegt, {converted} Auftrag/Aufträge übernommen, {archived} erledigte in Historie)")


def normalize_state_v1270(con: sqlite3.Connection) -> None:
    """Felder, die der Browser beim Laden ergänzt, auch serverseitig anlegen.

    Sonst sieht der Server beim ersten Speichern eine "Änderung" an Maschinen/Sperrzeiten,
    die Rollen wie die Arbeitsvorbereitung nicht ändern dürfen.
    """
    row = con.execute("SELECT json FROM state WHERE id=1").fetchone()
    if not row:
        return
    state = json.loads(row["json"])
    changed = False
    if not isinstance(state.get("machineBlocks"), list):
        state["machineBlocks"] = []
        changed = True
    for m in state.get("machines") or []:
        if not isinstance(m, dict):
            continue
        kind = "line" if m.get("kind") == "line" else "machine"
        crew_raw = _finite_float(m.get("crew", 1))
        crew = int(max(1, min(99, math.floor(crew_raw)))) if crew_raw is not None else 1
        if m.get("kind") != kind or m.get("crew") != crew:
            m["kind"], m["crew"] = kind, crew
            changed = True
    if changed:
        con.execute("UPDATE state SET json=?,updated_at=?,updated_by=? WHERE id=1",
                    (json.dumps(state, ensure_ascii=False, separators=(",", ":")), now_iso(), "V12.7 normalize"))
        print("DB-MIGRATION state: V12.7 Datenstand normalisiert (Maschinen-Typ/Besetzung, Sperrzeiten)")


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERS)
    return base64.b64encode(salt).decode(), base64.b64encode(digest).decode()


def verify_password(password: str, salt_b64: str, digest_b64: str) -> bool:
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(digest_b64)
    got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERS)
    return hmac.compare_digest(got, expected)


DUMMY_SALT, DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def create_or_reset_admin(username: str, password: str) -> None:
    if len(username.strip()) < 2:
        raise ValueError("Benutzername zu kurz")
    if len(password) < 8:
        raise ValueError("Passwort muss mindestens 8 Zeichen haben")
    salt, digest = hash_password(password)
    ts = now_iso()
    with DB_LOCK, db_session() as con:
        row = con.execute("SELECT id FROM users WHERE username=?", (username.strip(),)).fetchone()
        if row:
            con.execute("UPDATE users SET salt=?,password_hash=?,role='admin',active=1,updated_at=? WHERE id=?", (salt, digest, ts, row["id"]))
            con.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
        else:
            con.execute("INSERT INTO users(username,salt,password_hash,role,active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)", (username.strip(), salt, digest, "admin", ts, ts))
    print(f"Admin '{username.strip()}' ist eingerichtet.")


def canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def mp_error(code: str, message: str, **extra) -> dict:
    body = {"errorCode": code, "error": message}
    body.update(extra)
    return body


def _clock_minutes(value) -> int | None:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return None
    try:
        h, m = int(value[:2]), int(value[3:])
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def _template_valid(t: dict) -> bool:
    if not isinstance(t, dict):
        return False
    start, end = _clock_minutes(t.get("start")), _clock_minutes(t.get("end"))
    if start is None or end is None or end <= start:
        return False
    ranges = []
    for b in (t.get("breaks") or [])[:2]:
        if not isinstance(b, dict):
            return False
        a, z = b.get("start", ""), b.get("end", "")
        if not a and not z:
            continue
        aa, zz = _clock_minutes(a), _clock_minutes(z)
        if aa is None or zz is None or zz <= aa or aa < start or zz > end:
            return False
        ranges.append((aa, zz))
    ranges.sort()
    return all(ranges[i][0] >= ranges[i - 1][1] for i in range(1, len(ranges)))


_LOCAL_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _valid_local_datetime(value, allow_empty: bool = True) -> bool:
    if value in (None, ""):
        return allow_empty
    if not isinstance(value, str) or not _LOCAL_DT_RE.fullmatch(value):
        return False
    dt = _parse_iso(value)
    return bool(dt and dt.tzinfo is None)


def _week_start_key(date_key: str) -> str:
    d = datetime.strptime(str(date_key), "%Y-%m-%d")
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def _deployment_map(state: dict) -> dict:
    """(Mitarbeiter-ID, KW-Montag) -> Einsatzbereich laut GF-KW-Einsatz."""
    return {(str(x.get("employeeId")), str(x.get("weekStart"))): str(x.get("departmentId") or "")
            for x in (state.get("weeklyEmployeeDeployments") or []) if isinstance(x, dict)}


def _valid_date_key(value) -> bool:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _finite_float(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _nonnegative_int(value) -> bool:
    n = _finite_float(value)
    return n is not None and n >= 0 and n.is_integer()


def _ordered_dt_pair(start_value, end_value) -> tuple[datetime, datetime] | None:
    a, z = _parse_iso(start_value), _parse_iso(end_value)
    if not a or not z or ((a.tzinfo is None) != (z.tzinfo is None)):
        return None
    try:
        return (a, z) if z > a else None
    except TypeError:
        return None


def _validate_segments(segments, *, allow_empty: bool = False) -> tuple[bool, str, datetime | None, datetime | None]:
    if not isinstance(segments, list) or (not allow_empty and not segments):
        return False, "Segmentliste fehlt oder ist leer.", None, None
    first = last = prev_end = None
    awareness = None
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            return False, f"Segment {i + 1} ist ungültig.", None, None
        pair = _ordered_dt_pair(seg.get("start"), seg.get("end"))
        if not pair:
            return False, f"Segment {i + 1} hat ungültige Start-/Endzeit.", None, None
        a, z = pair
        seg_awareness = a.tzinfo is not None
        if awareness is None:
            awareness = seg_awareness
        elif awareness != seg_awareness:
            return False, "Segmentzeiten mischen lokale Zeiten und Zeitzonen.", None, None
        if seg.get("shift") not in {"single", "early", "late"}:
            return False, f"Segment {i + 1} hat eine ungültige Schicht.", None, None
        if prev_end is not None:
            try:
                if a < prev_end:
                    return False, "Plansegmente überlappen oder sind nicht sortiert.", None, None
            except TypeError:
                return False, "Plansegmente verwenden inkompatible Zeitformate.", None, None
        first = first or a
        last = z
        prev_end = z
    return True, "", first, last


def _segments_hours(segments) -> float | None:
    ok, _, _, _ = _validate_segments(segments, allow_empty=True)
    if not ok:
        return None
    total = 0.0
    for seg in segments:
        pair = _ordered_dt_pair(seg.get("start"), seg.get("end"))
        if not pair:
            return None
        a, z = pair
        total += (z - a).total_seconds() / 3600.0
    return total


def _plan_type(o: dict) -> str:
    if o.get("direction") == "backward":
        return "finish-hard" if o.get("anchorMode") == "hard" else "finish-soft"
    if o.get("anchorMode") == "none":
        return "auto"
    return "start-hard" if o.get("anchorMode") == "hard" else "start-soft"


def _validate_baseline(o: dict, midset: set[str]) -> tuple[bool, str]:
    """Freigabeplan prüfen. Dauer = Sollstunden / eingefrorene Besetzung + Umrüstzeit."""
    b = o.get("baselinePlan")
    if not isinstance(b, dict):
        return False, "Freigabeplan fehlt."
    bmid = str(b.get("machineId", ""))
    # A running order may have started on its pre-approved alternative machine.
    # The baseline intentionally remains the immutable RELEASE snapshot and can
    # therefore reference the former primary/alternative assignment. The
    # released->running transition check below proves that such a switch was
    # actually pre-planned; running/paused fields are frozen afterwards.
    if str(o.get("status", "planned")) == "released":
        allowed = {str(o.get("machineId", ""))}
        if o.get("allowAlternative") and o.get("altMachineId"):
            allowed.add(str(o.get("altMachineId")))
        if bmid not in midset or bmid not in allowed:
            return False, "Freigabeplan verweist auf eine nicht zulässige Einsatzmaschine."
    elif bmid not in midset:
        return False, "Freigabeplan verweist auf eine unbekannte Einsatzmaschine."
    pair = _ordered_dt_pair(b.get("start"), b.get("end"))
    if not pair:
        return False, "Freigabeplan enthält ungültige Start-/Endzeit."
    ok, reason, first, last = _validate_segments(b.get("segments"), allow_empty=False)
    if not ok:
        return False, f"Freigabeplan: {reason}"
    a, z = pair
    try:
        if first != a or last != z:
            return False, "Freigabeplan Start/Ende stimmen nicht mit den Segmenten überein."
    except TypeError:
        return False, "Freigabeplan verwendet inkompatible Zeitformate."
    planned_hours = _segments_hours(b.get("segments"))
    expected_hours = _finite_float(o.get("hours"))
    frozen_setup = _finite_float(b.get("setupMinutes", 0))
    if frozen_setup is None or frozen_setup < 0 or frozen_setup > 1440:
        return False, "Freigabeplan enthält eine ungültige eingefrorene Umrüstzeit."
    frozen_crew = _finite_float(b.get("crew", 1))
    if frozen_crew is None or frozen_crew < 1 or not frozen_crew.is_integer() or frozen_crew > 99:
        return False, "Freigabeplan enthält eine ungültige eingefrorene Besetzung."
    expected_total = None if expected_hours is None else expected_hours / frozen_crew + frozen_setup / 60.0
    if planned_hours is None or expected_total is None or abs(planned_hours - expected_total) > 0.01:
        return False, "Freigabeplan-Dauer stimmt nicht mit Sollstunden plus eingefrorener Umrüstzeit überein."
    if b.get("planType") not in (None, "", _plan_type(o)):
        return False, "Freigabeplan passt nicht zur Planart des Auftrags."
    expected_anchor = o.get("requiredFinish") if o.get("direction") == "backward" else o.get("requiredStart")
    if "anchor" in b and str(b.get("anchor") or "") != str(expected_anchor or ""):
        return False, "Freigabeplan passt nicht zum hinterlegten Terminanker."
    return True, ""


def _personnel_assignment_valid(a: dict) -> tuple[bool, str, str]:
    start, end = _clock_minutes(a.get("start")), _clock_minutes(a.get("end"))
    if start is None or end is None:
        return False, "MP-PERS-020", "Ungültige Mitarbeiter-Uhrzeit."
    if end <= start:
        return False, "MP-PERS-021", "Mitarbeiter-Ende muss nach Start liegen."
    ranges = []
    for b in (a.get("breaks") or [])[:2]:
        if not isinstance(b, dict):
            return False, "MP-PERS-022", "Ungültige Mitarbeiter-Pause."
        bs, be = b.get("start", ""), b.get("end", "")
        if not bs and not be:
            continue
        x, y = _clock_minutes(bs), _clock_minutes(be)
        if x is None or y is None:
            return False, "MP-PERS-022", "Ungültige Mitarbeiter-Pausenzeit."
        if y <= x or x < start or y > end:
            return False, "MP-PERS-023", "Mitarbeiter-Pause muss innerhalb der Arbeitszeit liegen."
        ranges.append((x, y))
    ranges.sort()
    for i in range(1, len(ranges)):
        if ranges[i][0] < ranges[i - 1][1]:
            return False, "MP-PERS-024", "Mitarbeiter-Pausen dürfen sich nicht überlappen."
    return True, "", ""


PROJECT_PHASES = ("inquiry", "pm", "offer_sent", "accepted", "lost", "closed")
PROJECT_POST_ACCEPT = {"accepted", "closed"}
PROCESS_STATUS = {"open", "in_progress", "waiting", "done", "cancelled"}


def validate_projects(old: dict, projects: list) -> tuple[bool, str, str]:
    """Projekt-Lebenszyklus: Eingang Vertrieb -> Projektmanagement -> Angebot ->
    Annahme (AB + Liefertermin) -> Arbeitsvorbereitung/Produktion -> Abschluss."""
    old_map = {str(p.get("id")): p for p in (old.get("projects") or []) if isinstance(p, dict)}
    seen_ids, seen_ab, seen_no = set(), set(), set()
    for project in projects:
        if not isinstance(project, dict):
            return False, "MP-PM-002", "Projekt ist kein Objekt."
        pid = str(project.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", pid) or pid in seen_ids:
            return False, "MP-PM-002", "Projekt benötigt eine eindeutige interne ID."
        seen_ids.add(pid)
        number = str(project.get("number") or "").strip()
        label = number or pid
        if not number or len(number) > 40 or number.casefold() in seen_no:
            return False, "MP-PM-006", f"Projekt '{label}': Projektnummer fehlt oder ist doppelt."
        seen_no.add(number.casefold())
        phase = str(project.get("phase") or "")
        if phase not in PROJECT_PHASES:
            return False, "MP-PM-007", f"Projekt '{label}' hat eine ungültige Phase."
        ab = str(project.get("ab") or "").strip()
        if len(ab) > 80 or (ab and ab.casefold() in seen_ab):
            return False, "MP-PM-003", f"Projekt '{label}': AB ist zu lang oder bereits vergeben."
        if ab:
            seen_ab.add(ab.casefold())
        if phase in PROJECT_POST_ACCEPT and not ab:
            return False, "MP-PM-008", f"Projekt '{label}': Ab Annahme ist die AB-Nummer Pflicht."
        due = project.get("dueDate")
        if due not in (None, "") and not _valid_date_key(due):
            return False, "MP-PM-009", f"Projekt '{label}': Liefertermin ist ungültig."
        before = old_map.get(pid)
        if phase == "accepted" and (not before or str(before.get("phase") or "") != "accepted") and not _valid_date_key(due):
            return False, "MP-PM-009", f"Projekt '{label}': Für die Annahme sind AB-Nummer und Liefertermin Pflicht."
        for f, limit in (("wt", 80), ("name", 200), ("customer", 200), ("contact", 200), ("note", 2000)):
            if len(str(project.get(f) or "")) > limit:
                return False, "MP-PM-002", f"Projekt '{label}': Feld '{f}' ist zu lang."
        if not isinstance(project.get("development") or {}, dict) or not isinstance(project.get("offer") or {}, dict):
            return False, "MP-PM-002", f"Projekt '{label}': Angebots-/Entwicklungsdaten sind ungültig."
        processes = project.get("processes") or []
        if not isinstance(processes, list):
            return False, "MP-PM-010", f"Projekt '{label}': Prozessliste ist ungültig."
        seen_proc = set()
        for pr in processes:
            if not isinstance(pr, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(pr.get("id") or "")) or str(pr.get("id")) in seen_proc:
                return False, "MP-PM-010", f"Projekt '{label}': Prozess ohne eindeutige ID."
            seen_proc.add(str(pr.get("id")))
            if not str(pr.get("areaId") or "").strip() or not str(pr.get("title") or "").strip() or len(str(pr.get("title"))) > 200:
                return False, "MP-PM-010", f"Projekt '{label}': Prozess benötigt Bereich und Aufgabe."
            if str(pr.get("status") or "open") not in PROCESS_STATUS:
                return False, "MP-PM-010", f"Projekt '{label}': Prozess hat einen ungültigen Status."
            if len(str(pr.get("workStepId") or "")) > 80:
                return False, "MP-PM-010", f"Projekt '{label}': Prozess verweist auf eine ungültige FS."
            if pr.get("dueDate") not in (None, "") and not _valid_date_key(pr.get("dueDate")):
                return False, "MP-PM-010", f"Projekt '{label}': Prozess hat ein ungültiges Fälligkeitsdatum."
            if pr.get("startDate") not in (None, "") and not _valid_date_key(pr.get("startDate")):
                return False, "MP-PM-010", f"Projekt '{label}': Prozess hat ein ungültiges Startdatum."
            if _valid_date_key(pr.get("startDate")) and _valid_date_key(pr.get("dueDate")) and str(pr["startDate"]) > str(pr["dueDate"]):
                return False, "MP-PM-016", f"Projekt '{label}': Prozess „{pr.get('title')}“ startet nach seiner Fälligkeit."
        log = project.get("log") or []
        if not isinstance(log, list) or any(not isinstance(x, dict) or not x.get("id") for x in log):
            return False, "MP-PM-011", f"Projekt '{label}': Verlauf ist ungültig."
    return True, "", ""


TEMPLATE_KINDS = {"pm", "av"}


def validate_process_templates(templates) -> tuple[bool, str, str]:
    """Gespeicherte Prozessabläufe: PM (bis Angebotsannahme) und AV (Fertigung)."""
    if templates in (None, ""):
        return True, "", ""
    if not isinstance(templates, list):
        return False, "MP-TPL-001", "Prozessabläufe sind ungültig."
    ids, names = set(), set()
    for t in templates:
        if not isinstance(t, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(t.get("id") or "")) or str(t.get("id")) in ids:
            return False, "MP-TPL-001", "Prozessablauf benötigt eine eindeutige ID."
        ids.add(str(t.get("id")))
        kind, name = str(t.get("kind") or ""), str(t.get("name") or "").strip()
        if kind not in TEMPLATE_KINDS or not name or len(name) > 80 or (kind, name.casefold()) in names:
            return False, "MP-TPL-002", f"Prozessablauf „{name or t.get('id')}“: Name fehlt, ist zu lang oder doppelt."
        names.add((kind, name.casefold()))
        steps = t.get("steps")
        if not isinstance(steps, list) or not steps or len(steps) > 60:
            return False, "MP-TPL-003", f"Prozessablauf „{name}“ benötigt 1 bis 60 Schritte."
        for st in steps:
            if not isinstance(st, dict) or not str(st.get("areaId") or "").strip() or not str(st.get("title") or "").strip() or len(str(st.get("title"))) > 200:
                return False, "MP-TPL-003", f"Prozessablauf „{name}“: Schritt benötigt Bereich und Aufgabe."
            for f in ("offsetDays", "durationDays"):
                v = _finite_float(st.get(f, 0))
                if v is None or v < 0 or v > 3650 or not v.is_integer():
                    return False, "MP-TPL-003", f"Prozessablauf „{name}“: {f} muss eine ganze Zahl von 0 bis 3650 sein."
    return True, "", ""


def templates_change_allowed(old: dict, new: dict, role: str) -> tuple[bool, str]:
    """PM pflegt nur PM-Abläufe, AV nur AV-Abläufe; Admin alles; andere Rollen nichts."""
    kind = {"project_management": "pm", "production_planning": "av"}.get(role)
    a, b = _record_map(old.get("processTemplates")), _record_map(new.get("processTemplates"))
    for tid in set(a) | set(b):
        if canonical(a.get(tid)) == canonical(b.get(tid)):
            continue
        for side in (a.get(tid), b.get(tid)):
            if side is not None and (not kind or str(side.get("kind")) != kind):
                return False, f"Prozessablauf „{side.get('name')}“ darf von dieser Rolle nicht geändert werden."
    return True, ""


def validate_state(old: dict, new: dict) -> tuple[bool, str, str]:
    """Server-side invariants. Browser checks are convenience only."""
    if not isinstance(new, dict):
        return False, "MP-DATA-010", "Datenstand ist kein Objekt."
    machines = new.get("machines")
    work_steps = new.get("workSteps")
    departments = new.get("departments") or []
    projects = new.get("projects") or []
    if not isinstance(machines, list) or not machines:
        return False, "MP-DATA-011", "Mindestens eine Maschine ist erforderlich."
    if not isinstance(work_steps, list):
        return False, "MP-STEP-001", "Arbeitsgangliste fehlt."
    if not isinstance(departments, list) or not departments:
        return False, "MP-DEPT-001", "Produktionsbereiche fehlen."

    dept_ids = set()
    dept_types = {}
    valid_types = {"MACHINE", "LABOR_HOURS", "PROCESS", "CYCLE"}
    for dep in departments:
        if not isinstance(dep, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(dep.get("id") or "")):
            return False, "MP-DEPT-002", "Produktionsbereich hat eine ungültige ID."
        did = str(dep["id"])
        ptype = str(dep.get("planningType") or "")
        if did in dept_ids:
            return False, "MP-DEPT-002", f"Doppelte Bereichs-ID '{did}'."
        if ptype not in valid_types:
            return False, "MP-DEPT-003", f"Bereich '{did}' hat einen ungültigen Planungstyp."
        dept_ids.add(did)
        dept_types[did] = ptype

    if not isinstance(projects, list):
        return False, "MP-PM-001", "Projekt-/Auftragsstamm ist ungültig."
    ok, code, reason = validate_projects(old, projects)
    if not ok:
        return False, code, reason
    ok, code, reason = validate_process_templates(new.get("processTemplates"))
    if not ok:
        return False, code, reason
    seen_project_ids = {str(p.get("id")) for p in projects}

    project_ids = seen_project_ids
    step_ids = set()
    sequence_keys = set()
    for step in work_steps:
        if not isinstance(step, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(step.get("id") or "")):
            return False, "MP-STEP-002", "Arbeitsgang benötigt eine gültige ID."
        sid = str(step["id"])
        if sid in step_ids:
            return False, "MP-STEP-002", f"Doppelte Arbeitsgang-ID '{sid}'."
        step_ids.add(sid)
        pid = str(step.get("projectId") or "")
        if pid and pid not in project_ids:
            return False, "MP-STEP-003", f"Arbeitsgang '{sid}' verweist auf einen unbekannten AB-Auftrag."
        fs = str(step.get("fs") or step.get("order") or "").strip()
        ab_ref = str(step.get("ab") or "").strip()
        wt_ref = str(step.get("wt") or "").strip()
        if not fs:
            return False, "MP-STEP-014", f"Produktionsauftrag '{sid}' benötigt eine FS."
        if str(step.get("status") or "planned") not in {"planned", "released", "running", "paused", "done", "cancelled"}:
            return False, "MP-STEP-017", f"Arbeitsgang '{sid}' hat einen ungültigen Status."
        if any(len(v) > 80 for v in (fs, ab_ref, wt_ref)):
            return False, "MP-STEP-014", f"Arbeitsgang '{sid}': FS/AB/WT ist zu lang."
        if pid:
            linked = next((p for p in projects if str(p.get("id")) == pid), None)
            if linked and ab_ref and ab_ref.casefold() != str(linked.get("ab") or "").strip().casefold():
                return False, "MP-STEP-015", f"Arbeitsgang '{sid}': AB passt nicht zur Projektverknüpfung."
            if linked and wt_ref and linked.get("wt") and wt_ref.casefold() != str(linked.get("wt") or "").strip().casefold():
                return False, "MP-STEP-016", f"Arbeitsgang '{sid}': WT passt nicht zur AB-Verknüpfung."
        did = str(step.get("departmentId") or "")
        if did not in dept_ids:
            return False, "MP-STEP-004", f"Arbeitsgang '{sid}' verweist auf einen unbekannten Produktionsbereich."
        ptype = str(step.get("planningType") or "")
        if ptype not in valid_types or ptype != dept_types.get(did):
            return False, "MP-STEP-005", f"Arbeitsgang '{sid}' hat einen Planungstyp, der nicht zum Bereich passt."
        seq = _finite_float(step.get("sequence"))
        if seq is None or seq <= 0:
            return False, "MP-STEP-006", f"Arbeitsgang '{sid}' hat eine ungültige Reihenfolge."
        seq_key = (pid, float(seq))
        if seq_key in sequence_keys:
            return False, "MP-STEP-006", f"Auftrag hat doppelte Arbeitsgang-Reihenfolge {seq:g}."
        sequence_keys.add(seq_key)

        predecessors = step.get("predecessorIds") or []
        if not isinstance(predecessors, list) or len(predecessors) != len(set(map(str, predecessors))):
            return False, "MP-STEP-007", f"Arbeitsgang '{sid}' hat ungültige Vorgänger."
        if sid in {str(x) for x in predecessors}:
            return False, "MP-STEP-007", f"Arbeitsgang '{sid}' darf nicht sein eigener Vorgänger sein."
        if predecessors and not pid:
            return False, "MP-STEP-007", f"Arbeitsgang '{sid}': Vorgänger sind nur innerhalb eines AB-Auftrags zulässig."

        if ptype != "MACHINE":
            for field in ("requiredHours", "dryMinutes", "quantity", "cycleSeconds", "partsPerCycle", "setupMinutes", "staffRequired"):
                value = _finite_float(step.get(field, 0))
                if value is None or value < 0:
                    return False, "MP-STEP-008", f"Arbeitsgang '{sid}' enthält ungültigen Wert '{field}'."
            done_hours = _finite_float(step.get("doneHours", 0))
            if done_hours is None or done_hours < 0:
                return False, "MP-STEP-018", f"Arbeitsgang '{sid}': erledigte Stunden müssen endlich und nicht negativ sein."
            if step.get("planningWeek") not in (None, "") and not _valid_date_key(step.get("planningWeek")):
                return False, "MP-STEP-019", f"Arbeitsgang '{sid}' hat eine ungültige Planwoche."
            if ptype == "LABOR_HOURS" and _finite_float(step.get("requiredHours", 0)) <= 0:
                return False, "MP-STEP-009", f"Konfektions-Arbeitsgang '{sid}' benötigt Stunden größer 0."
            if ptype == "PROCESS" and _finite_float(step.get("requiredHours", 0)) <= 0:
                return False, "MP-STEP-009", f"Prozess-Arbeitsgang '{sid}' benötigt aktive Stunden größer 0."
            if ptype == "CYCLE":
                qty = _finite_float(step.get("quantity", 0))
                cycle = _finite_float(step.get("cycleSeconds", 0))
                parts = _finite_float(step.get("partsPerCycle", 1))
                staff = _finite_float(step.get("staffRequired", 1))
                setup = _finite_float(step.get("setupMinutes", 0))
                if qty is None or qty <= 0 or cycle is None or cycle <= 0 or parts is None or parts <= 0:
                    return False, "MP-STEP-009", f"Takt-Arbeitsgang '{sid}' benötigt Menge, Taktzeit und Teile/Takt größer 0."
                if staff is None or staff < 1 or not staff.is_integer() or staff > 99:
                    return False, "MP-STEP-011", f"Takt-Arbeitsgang '{sid}' benötigt einen ganzzahligen Personalbedarf von 1 bis 99."
                if setup is None or setup < 0 or setup > 1440:
                    return False, "MP-STEP-012", f"Takt-Arbeitsgang '{sid}' hat eine ungültige Rüstzeit."

    by_id = {str(x.get("id")): x for x in work_steps}
    for step in work_steps:
        for predecessor_id in (step.get("predecessorIds") or []):
            predecessor = by_id.get(str(predecessor_id))
            if not predecessor or str(predecessor.get("projectId")) != str(step.get("projectId")):
                return False, "MP-STEP-007", f"Arbeitsgang '{step.get('id')}' verweist auf einen ungültigen Vorgänger."

    graph = {sid: [str(x) for x in (step.get("predecessorIds") or [])] for sid, step in by_id.items()}
    visiting, visited = set(), set()
    def visit_step(sid):
        if sid in visiting:
            return False
        if sid in visited:
            return True
        visiting.add(sid)
        for predecessor_id in graph.get(sid, []):
            if not visit_step(predecessor_id):
                return False
        visiting.remove(sid)
        visited.add(sid)
        return True
    for sid in graph:
        if not visit_step(sid):
            return False, "MP-STEP-010", "Arbeitsgang-Abhängigkeiten enthalten einen Zyklus."

    orders = [step for step in work_steps if step.get("planningType") == "MACHINE"]

    mids = []
    for m in machines:
        if not isinstance(m, dict) or not str(m.get("id", "")).strip():
            return False, "MP-MACH-001", "Maschine ohne gültige ID."
        mid = str(m["id"])
        mids.append(mid)
        if not str(m.get("name", "")).strip():
            return False, "MP-MACH-003", f"Maschine '{mid}' hat keinen Namen."
        if not _valid_local_datetime(m.get("start"), allow_empty=False):
            return False, "MP-MACH-004", f"Maschine '{m.get('name') or mid}' hat eine ungültige Planbasis."
        committed = m.get("committedUntil")
        if committed not in (None, "") and _parse_iso(committed) is None:
            return False, "MP-MACH-005", f"Maschine '{m.get('name') or mid}' hat eine ungültige Historiengrenze."
        if str(m.get("defaultShiftMode", "1")) not in {"0", "1", "2"}:
            return False, "MP-MACH-006", f"Maschine '{m.get('name') or mid}' hat einen ungültigen Schichtmodus."
        sr = _finite_float(m.get("staffRequired", 1))
        if sr is None or sr < 0 or not sr.is_integer() or sr > 99:
            return False, "MP-MACH-007", f"Maschine '{m.get('name') or mid}' hat einen ungültigen Personalbedarf."
        if str(m.get("departmentId") or "cnc") not in dept_ids:
            return False, "MP-MACH-008", f"Maschine '{m.get('name') or mid}' verweist auf einen unbekannten Bereich."
        setup = _finite_float(m.get("setupMinutes", 0))
        if setup is None or setup < 0 or setup > 1440:
            return False, "MP-MACH-009", f"Umrüstzeit von Maschine '{m.get('name') or mid}' ist ungültig."
        crew = _finite_float(m.get("crew", 1))
        if crew is None or crew < 1 or not crew.is_integer() or crew > 99:
            return False, "MP-MACH-010", f"Besetzung von '{m.get('name') or mid}' muss eine ganze Zahl von 1 bis 99 sein."
        if str(m.get("kind") or "machine") not in {"machine", "line"}:
            return False, "MP-MACH-011", f"Ressource '{m.get('name') or mid}' hat einen ungültigen Typ."
    if len(mids) != len(set(mids)):
        return False, "MP-MACH-002", "Doppelte Maschinen-ID."
    midset = set(mids)
    machine_map = {str(m.get("id")): m for m in machines if isinstance(m, dict)}

    templates = new.get("shiftTemplates") or {}
    for key in ("single", "fridaySingle", "early", "late"):
        if not _template_valid(templates.get(key, {})):
            return False, "MP-CAL-010", f"Schichtvorlage '{key}' enthält ungültige oder überlappende Zeiten/Pausen."

    capacities = new.get("operatorCapacity") or {}
    for key in ("single", "early", "late"):
        cap = _finite_float(capacities.get(key))
        if cap is None or cap < 1 or not cap.is_integer() or cap > 99:
            return False, "MP-CAL-011", f"Bedienerkapazität '{key}' muss eine ganze Zahl von 1 bis 99 sein."

    if not isinstance(new.get("personnelGate", False), bool):
        return False, "MP-PERS-027", "Personal-Gate muss ein boolescher Wert sein."

    year_rules = new.get("yearRules") or []
    if not isinstance(year_rules, list):
        return False, "MP-CAL-012", "Jahresregeln sind ungültig."
    yr_keys = set()
    for r in year_rules:
        year = _finite_float(r.get("year")) if isinstance(r, dict) else None
        key = (str(r.get("machineId", "")), int(year) if year is not None and year.is_integer() else None) if isinstance(r, dict) else ("", None)
        if not isinstance(r, dict) or key[0] not in midset or year is None or not year.is_integer() or not (2020 <= year <= 2100) or str(r.get("mode")) not in {"0", "1", "2"} or key in yr_keys:
            return False, "MP-CAL-012", "Jahresregel enthält ungültige/duplizierte Maschine, Jahr- oder Schichtdaten."
        yr_keys.add(key)

    week_rules = new.get("weekRules") or []
    if not isinstance(week_rules, list):
        return False, "MP-CAL-013", "Wochenregeln sind ungültig."
    wr_keys = set()
    for r in week_rules:
        year = _finite_float(r.get("year")) if isinstance(r, dict) else None
        week = _finite_float(r.get("week")) if isinstance(r, dict) else None
        key = (str(r.get("machineId", "")), int(year) if year is not None and year.is_integer() else None, int(week) if week is not None and week.is_integer() else None) if isinstance(r, dict) else ("", None, None)
        if not isinstance(r, dict) or key[0] not in midset or year is None or week is None or not year.is_integer() or not week.is_integer() or not (2020 <= year <= 2100) or not (1 <= week <= 53) or str(r.get("mode")) not in {"0", "1", "2"} or key in wr_keys:
            return False, "MP-CAL-013", "Wochenregel enthält ungültige/duplizierte Maschine, Jahr-, KW- oder Schichtdaten."
        wr_keys.add(key)

    exceptions = new.get("exceptions") or []
    if not isinstance(exceptions, list):
        return False, "MP-CAL-014", "Kalender-Ausnahmen sind ungültig."
    ex_dates = set()
    for e in exceptions:
        date = str(e.get("date", "")) if isinstance(e, dict) else ""
        if not isinstance(e, dict) or not _valid_date_key(date) or str(e.get("mode")) not in {"0", "1", "2"} or date in ex_dates:
            return False, "MP-CAL-014", "Kalender-Ausnahme enthält ungültiges/dupliziertes Datum oder Schichtmodus."
        ex_dates.add(date)

    employees = new.get("employees") or []
    if not isinstance(employees, list):
        return False, "MP-PERS-001", "Mitarbeiterliste ist ungültig."
    eids = []
    for e in employees:
        if not isinstance(e, dict) or not str(e.get("id", "")).strip():
            return False, "MP-PERS-001", "Mitarbeiter ohne gültige ID."
        eid = str(e["id"])
        eids.append(eid)
        if not str(e.get("name", "")).strip():
            return False, "MP-PERS-026", f"Mitarbeiter '{eid}' hat keinen Namen."
        skills = e.get("skills") or []
        if not isinstance(skills, list) or any(str(x) not in midset for x in skills):
            return False, "MP-PERS-025", f"Mitarbeiter '{e.get('name') or eid}' besitzt eine ungültige Maschinenfreigabe."
        hm = str(e.get("homeMachineId", "") or "")
        if hm and (hm not in midset or hm not in {str(x) for x in skills}):
            return False, "MP-PERS-025", f"Stammmaschine von '{e.get('name') or eid}' ist nicht freigegeben."
        if str(e.get("homeShift", "auto")) not in {"auto", "early", "late"}:
            return False, "MP-PERS-001", f"Stammschicht von '{e.get('name') or eid}' ist ungültig."
        if str(e.get("employmentType", "permanent")) not in {"permanent", "temporary"}:
            return False, "MP-PERS-028", f"Personaltyp von '{e.get('name') or eid}' ist ungültig."
        if str(e.get("departmentId") or "") not in dept_ids:
            return False, "MP-PERS-029", f"Mitarbeiter '{e.get('name') or eid}' verweist auf einen unbekannten Bereich."
        weekly = _finite_float(e.get("weeklyHours", 40))
        if weekly is None or weekly < 0 or weekly > 80:
            return False, "MP-PERS-030", f"Wochenstunden von '{e.get('name') or eid}' sind ungültig."
    if len(eids) != len(set(eids)):
        return False, "MP-PERS-001", "Doppelte Mitarbeiter-ID."
    eidset = set(eids)

    assignment_keys = set()
    machine_dept_new = {str(m.get("id")): str(m.get("departmentId") or "cnc") for m in (new.get("machines") or []) if isinstance(m, dict)}
    deployment_new = _deployment_map(new)
    # Unveraenderte Zuordnungen nicht erneut gegen Freigabe/KW-Einsatz pruefen: Nimmt die GF
    # einen KW-Einsatz zurueck, darf das nicht jeden weiteren Speichervorgang blockieren.
    old_assignments = {(str(x.get("employeeId")), str(x.get("date"))): x for x in (old.get("personnelAssignments") or []) if isinstance(x, dict)}
    assignments = new.get("personnelAssignments") or []
    if not isinstance(assignments, list):
        return False, "MP-PERS-001", "Personalzuordnungen sind ungültig."
    for a in assignments:
        if not isinstance(a, dict):
            return False, "MP-PERS-001", "Personalzuordnung ist ungültig."
        eid, mid, date = str(a.get("employeeId", "")), str(a.get("machineId", "")), a.get("date")
        if eid not in eidset or mid not in midset or not _valid_date_key(date) or a.get("shift") not in {"single", "early", "late"}:
            return False, "MP-PERS-001", "Personalzuordnung verweist auf ungültige Mitarbeiter-, Maschinen-, Datums- oder Schichtdaten."
        employee = next((e for e in employees if str(e.get("id")) == eid), None)
        # Per KW in einen anderen Bereich eingesetzte Mitarbeiter (z. B. Leiharbeiter) duerfen
        # dort jede Ressource des Einsatzbereichs besetzen.
        deployed = deployment_new.get((eid, _week_start_key(date))) if employee else None
        deployed_ok = bool(deployed) and deployed != str(employee.get("departmentId") or "") and machine_dept_new.get(mid) == deployed
        unchanged = canonical(old_assignments.get((eid, str(date)))) == canonical(a)
        if employee and not unchanged and not deployed_ok and mid not in {str(x) for x in (employee.get("skills") or [])}:
            return False, "MP-PERS-025", f"Mitarbeiter '{employee.get('name') or eid}' ist für Maschine '{mid}' nicht freigegeben."
        key = (eid, str(date))
        if key in assignment_keys:
            return False, "MP-PERS-001", f"Mitarbeiter '{eid}' hat am {date} mehrere explizite Zuordnungen."
        assignment_keys.add(key)
        ok, code, reason = _personnel_assignment_valid(a)
        if not ok:
            return False, code, reason

    absence_keys = set()
    absences = new.get("personnelAbsences") or []
    if not isinstance(absences, list):
        return False, "MP-PERS-001", "Abwesenheitsliste ist ungültig."
    for a in absences:
        if not isinstance(a, dict) or str(a.get("employeeId", "")) not in eidset or not _valid_date_key(a.get("date")):
            return False, "MP-PERS-001", "Ungültige Mitarbeiter-Abwesenheit."
        key = (str(a.get("employeeId")), str(a.get("date")))
        if key in absence_keys:
            return False, "MP-PERS-001", "Doppelte Mitarbeiter-Abwesenheit."
        absence_keys.add(key)

    deployments = new.get("weeklyEmployeeDeployments") or []
    if not isinstance(deployments, list):
        return False, "MP-PERS-031", "KW-Einsatzliste ist ungültig."
    deployment_keys = set()
    for x in deployments:
        if not isinstance(x, dict):
            return False, "MP-PERS-031", "KW-Einsatz ist ungültig."
        eid = str(x.get("employeeId") or "")
        did = str(x.get("departmentId") or "")
        wk = str(x.get("weekStart") or "")
        if eid not in eidset or did not in dept_ids or not _valid_date_key(wk):
            return False, "MP-PERS-031", "KW-Einsatz verweist auf unbekannten Mitarbeiter/Bereich oder ungültige Woche."
        key = (eid, wk)
        if key in deployment_keys:
            return False, "MP-PERS-031", "Ein Mitarbeiter darf je KW nur einen Bereichseinsatz besitzen."
        deployment_keys.add(key)

    needs = new.get("departmentStaffNeeds") or []
    if not isinstance(needs, list):
        return False, "MP-PERS-032", "Personalbedarfe sind ungültig."
    need_keys = set()
    for x in needs:
        if not isinstance(x, dict):
            return False, "MP-PERS-032", "Personalbedarf ist ungültig."
        did = str(x.get("departmentId") or "")
        wk = str(x.get("weekStart") or "")
        requested = x.get("requested", 0)
        confirmed = x.get("confirmed")
        if did not in dept_ids or not _valid_date_key(wk):
            return False, "MP-PERS-032", "Personalbedarf verweist auf unbekannten Bereich oder ungültige Woche."
        if isinstance(requested, bool) or not isinstance(requested, int) or requested < 0 or requested > 999:
            return False, "MP-PERS-032", "Gemeldeter Mitarbeiterbedarf muss ganzzahlig zwischen 0 und 999 sein."
        if confirmed is not None and (isinstance(confirmed, bool) or not isinstance(confirmed, int) or confirmed < 0 or confirmed > 999):
            return False, "MP-PERS-032", "GF-Bestätigung muss ganzzahlig zwischen 0 und 999 sein."
        key = (did, wk)
        if key in need_keys:
            return False, "MP-PERS-032", "Je Bereich und KW ist nur ein Personalbedarf erlaubt."
        need_keys.add(key)

    seen_orders = set()
    seen_pos = set()
    locked_by_machine: dict[str, str] = {}
    old_orders = {str(o.get("id")): o for o in (old.get("workSteps") or []) if isinstance(o, dict) and o.get("id") and o.get("planningType") == "MACHINE"}
    planning_fields = (
        "projectId", "fs", "ab", "wt", "sequence", "departmentId", "planningType", "predecessorIds", "pos", "machineId", "altMachineId", "allowAlternative", "order", "articleNo", "description",
        "targetQty", "hours", "direction", "anchorMode", "requiredStart", "requiredFinish", "createdAt", "baselinePlan"
    )
    core_without_machine_choice = tuple(f for f in planning_fields if f not in {"machineId", "altMachineId", "allowAlternative"})
    allowed_transitions = {
        "planned": {"planned", "released"},
        "released": {"released", "planned", "running"},
        "running": {"running", "paused"},
        "paused": {"paused", "running"},
    }

    for o in orders:
        if not isinstance(o, dict) or not str(o.get("id", "")).strip():
            return False, "MP-PLAN-001", "Auftrag ohne gültige ID."
        oid = str(o["id"])
        if oid in seen_orders:
            return False, "MP-PLAN-002", f"Doppelte Auftrag-ID '{oid}'."
        seen_orders.add(oid)

        pos = _finite_float(o.get("pos"))
        if pos is None or pos <= 0 or pos in seen_pos:
            return False, "MP-PLAN-044", f"Auftrag '{o.get('order') or oid}': Prioritätsposition muss eindeutig, endlich und größer 0 sein."
        seen_pos.add(pos)

        mid = str(o.get("machineId", ""))
        alt = str(o.get("altMachineId", "") or "")
        if mid not in midset:
            return False, "MP-PLAN-003", f"Auftrag '{o.get('order') or oid}' verweist auf eine unbekannte Hauptmaschine."
        order_department = str(o.get("departmentId") or next((m.get("departmentId") or "cnc" for m in machines if str(m.get("id")) == mid), "cnc"))
        if order_department not in dept_ids:
            return False, "MP-DEPT-004", f"Auftrag '{o.get('order') or oid}' verweist auf einen unbekannten Bereich."
        for used_mid in (mid, alt):
            if used_mid and str((machine_map.get(used_mid) or {}).get("departmentId") or "cnc") != order_department:
                return False, "MP-DEPT-005", f"Auftrag '{o.get('order') or oid}' ist einer Maschine aus einem anderen Bereich zugeordnet."
        project_id = str(o.get("projectId") or "")
        if project_id and project_id not in project_ids:
            return False, "MP-PM-004", f"Auftrag '{o.get('order') or oid}' verweist auf einen unbekannten AB-Auftrag."
        if alt and (alt not in midset or alt == mid):
            return False, "MP-PLAN-033", f"Auftrag '{o.get('order') or oid}': Alternative Maschine muss existieren und von der Hauptmaschine abweichen."
        if bool(o.get("allowAlternative")) != bool(alt):
            return False, "MP-PLAN-033", f"Auftrag '{o.get('order') or oid}': Alternativmaschinen-Schalter und Alternativmaschine sind inkonsistent."

        status = str(o.get("status", "planned"))
        if status not in {"planned", "released", "running", "paused"}:
            return False, "MP-PLAN-004", f"Auftrag '{o.get('order') or oid}' hat einen ungültigen Status."

        hours = _finite_float(o.get("hours") or 0)
        if hours is None or hours < 0 or (status in {"released", "running", "paused"} and hours <= 0):
            return False, "MP-PLAN-034", f"Auftrag '{o.get('order') or oid}': Sollstunden müssen endlich und für freigegebene/laufende Aufträge größer 0 sein."
        if not _nonnegative_int(o.get("targetQty") or 0):
            return False, "MP-PLAN-035", f"Auftrag '{o.get('order') or oid}': Sollmenge muss eine ganze, nicht negative Zahl sein."
        if not _nonnegative_int(o.get("goodQty") or 0) or not _nonnegative_int(o.get("scrapQty") or 0):
            return False, "MP-PROD-024", f"Auftrag '{o.get('order') or oid}': Gutmenge und Ausschuss müssen ganze, nicht negative Zahlen sein."

        direction = str(o.get("direction", "forward"))
        anchor_mode = str(o.get("anchorMode", "none"))
        rs, rf = o.get("requiredStart", ""), o.get("requiredFinish", "")
        if direction not in {"forward", "backward"} or anchor_mode not in {"none", "soft", "hard"}:
            return False, "MP-PLAN-046", f"Auftrag '{o.get('order') or oid}' hat eine ungültige Terminierungsart."
        if direction == "forward" and anchor_mode == "none":
            if rs or rf:
                return False, "MP-PLAN-046", f"Auftrag '{o.get('order') or oid}': Automatik darf keinen Terminanker enthalten."
        elif direction == "forward":
            if not _valid_local_datetime(rs, allow_empty=False) or rf:
                return False, "MP-PLAN-046", f"Auftrag '{o.get('order') or oid}': Starttermin ist ungültig oder Fertigtermin gleichzeitig gesetzt."
        else:
            if anchor_mode == "none" or not _valid_local_datetime(rf, allow_empty=False) or rs:
                return False, "MP-PLAN-046", f"Auftrag '{o.get('order') or oid}': Rückwärtsplanung benötigt genau einen gültigen Fertigtermin."

        due = str(o.get("dueDate") or "")
        if due and not _valid_date_key(due):
            return False, "MP-PLAN-060", f"Auftrag '{o.get('order') or oid}': AV-Termin (fertig bis) ist ungültig."
        if due and status == "planned" and anchor_mode != "none":
            before = old_orders.get(oid) or {}
            anchor_changed = any(canonical(before.get(f)) != canonical(o.get(f)) for f in ("direction", "anchorMode", "requiredStart", "requiredFinish", "dueDate"))
            anchor_day = str((rf if direction == "backward" else rs) or "")[:10]
            if anchor_changed and anchor_day and anchor_day > due:
                return False, "MP-PLAN-062", f"Auftrag '{o.get('order') or oid}': Termin {anchor_day} liegt nach dem AV-Termin {due}. Früher planen ist erlaubt, später nicht."

        oo_pre = old_orders.get(oid)
        if oo_pre:
            old_status_pre = str(oo_pre.get("status", "planned"))
            if old_status_pre == "released" and status == "released":
                for f in planning_fields:
                    if canonical(oo_pre.get(f)) != canonical(o.get(f)):
                        return False, "MP-PLAN-032", f"Freigegebener Auftrag '{o.get('order') or oid}' ist eingefroren. Freigabe zuerst zurücknehmen."
            if old_status_pre in {"running", "paused"}:
                for f in planning_fields:
                    if canonical(oo_pre.get(f)) != canonical(o.get(f)):
                        return False, "MP-PROD-016", f"Laufender/pausierter Auftrag '{o.get('order') or oid}' darf Planungsfeld '{f}' nicht verändern."

        if status in {"planned", "released"}:
            dirty_live = bool(o.get("actualStartedAt") or o.get("runningSince") or o.get("pausedAt") or o.get("lockedStart") or (o.get("lockedSegments") or []) or (o.get("pauseIntervals") or []) or o.get("remainingHours") not in (None, ""))
            if dirty_live:
                return False, "MP-PROD-031", f"Auftrag '{o.get('order') or oid}': Geplant/Freigegeben darf keine versteckten Live-Produktionsdaten enthalten."

        if status in {"released", "running", "paused"}:
            ok, reason = _validate_baseline(o, midset)
            if not ok:
                return False, "MP-PLAN-047", f"Auftrag '{o.get('order') or oid}': {reason}"
            old_for_release = old_orders.get(oid)
            if status == "released" and (not old_for_release or str(old_for_release.get("status", "planned")) == "planned"):
                baseline = o.get("baselinePlan") or {}
                bmid = str(baseline.get("machineId") or "")
                frozen_setup = _finite_float(baseline.get("setupMinutes"))
                current_setup = _finite_float((machine_map.get(bmid) or {}).get("setupMinutes", 0))
                if frozen_setup is None or current_setup is None or abs(frozen_setup - current_setup) > 1e-9:
                    return False, "MP-PLAN-055", f"Auftrag '{o.get('order') or oid}': Freigabe muss die aktuell gültige Umrüstzeit der Einsatzmaschine einfrieren."
                frozen_crew = _finite_float(baseline.get("crew", 1))
                current_crew = _finite_float((machine_map.get(bmid) or {}).get("crew", 1))
                if frozen_crew is None or current_crew is None or abs(frozen_crew - current_crew) > 1e-9:
                    return False, "MP-PLAN-061", f"Auftrag '{o.get('order') or oid}': Freigabe muss die aktuell gültige Besetzung der Linie einfrieren."

        if status in {"running", "paused"}:
            actual_start = _parse_iso(o.get("actualStartedAt"))
            if actual_start is None:
                return False, "MP-PROD-013", f"Auftrag '{o.get('order') or oid}': Produktionsstart fehlt oder ist ungültig."
            rem = _finite_float(o.get("remainingHours"))
            if rem is None or rem <= 0:
                return False, "MP-PROD-014", f"Auftrag '{o.get('order') or oid}': Reststunden müssen während der Produktion größer 0 und endlich sein."
            if _parse_iso(o.get("lockedStart")) is None:
                return False, "MP-PROD-018", f"Auftrag '{o.get('order') or oid}': Start des gesperrten Laufplans fehlt oder ist ungültig."
            ok, reason, _, _ = _validate_segments(o.get("lockedSegments"), allow_empty=False)
            if not ok:
                return False, "MP-PROD-015", f"Auftrag '{o.get('order') or oid}': Laufplan ungültig – {reason}"
            locked_hours = _segments_hours(o.get("lockedSegments"))
            if locked_hours is None or abs(locked_hours - rem) > 0.01:
                return False, "MP-PROD-018", f"Auftrag '{o.get('order') or oid}': Laufplan-Dauer stimmt nicht mit den Reststunden überein."
            pauses = o.get("pauseIntervals") or []
            if not isinstance(pauses, list):
                return False, "MP-PROD-019", f"Auftrag '{o.get('order') or oid}': Pausenverlauf ist ungültig."
            open_pauses = 0
            previous_end = None
            for pause in pauses:
                if not isinstance(pause, dict) or _parse_iso(pause.get("start")) is None:
                    return False, "MP-PROD-019", f"Auftrag '{o.get('order') or oid}': Pausenverlauf enthält ungültige Startzeit."
                ps = _parse_iso(pause.get("start"))
                if (ps.tzinfo is None) != (actual_start.tzinfo is None):
                    return False, "MP-PROD-019", f"Auftrag '{o.get('order') or oid}': Pausen und Produktionsstart verwenden inkompatible Zeitformate."
                if ps < actual_start:
                    return False, "MP-PROD-019", f"Auftrag '{o.get('order') or oid}': Pause liegt vor dem Produktionsstart."
                if previous_end is not None and ((ps.tzinfo is None) != (previous_end.tzinfo is None) or ps < previous_end):
                    return False, "MP-PROD-019", f"Auftrag '{o.get('order') or oid}': Pausen sind überlappend, unsortiert oder inkompatibel formatiert."
                if pause.get("end") in (None, ""):
                    open_pauses += 1
                    previous_end = ps
                else:
                    pair = _ordered_dt_pair(pause.get("start"), pause.get("end"))
                    if not pair or ((pair[1].tzinfo is None) != (actual_start.tzinfo is None)):
                        return False, "MP-PROD-019", f"Auftrag '{o.get('order') or oid}': Pause hat ungültige oder inkompatible Start-/Endzeit."
                    previous_end = pair[1]
            if (status == "paused" and open_pauses != 1) or (status == "running" and open_pauses != 0):
                return False, "MP-PROD-019", f"Auftrag '{o.get('order') or oid}': Offene Pause passt nicht zum Produktionsstatus."
            if status == "paused" and _parse_iso(o.get("pausedAt")) is None:
                return False, "MP-PROD-019", f"Auftrag '{o.get('order') or oid}': Pausenzeitpunkt fehlt."
            if status == "running" and _parse_iso(o.get("runningSince")) is None:
                return False, "MP-PROD-019", f"Auftrag '{o.get('order') or oid}': Laufzeitpunkt fehlt."

        if status in {"running", "paused"}:
            if mid in locked_by_machine and locked_by_machine[mid] != oid:
                return False, "MP-PROD-010", f"Auf Maschine '{mid}' sind mehrere laufende/pausierte Aufträge hinterlegt."
            locked_by_machine[mid] = oid

        oo = old_orders.get(oid)
        if not oo:
            if status in {"running", "paused"}:
                return False, "MP-PLAN-048", f"Auftrag '{o.get('order') or oid}' darf nicht direkt als laufend/pausiert angelegt werden."
            continue

        old_status = str(oo.get("status", "planned"))
        if status not in allowed_transitions.get(old_status, {old_status}):
            return False, "MP-PLAN-049", f"Ungültiger Statuswechsel bei '{o.get('order') or oid}': {old_status} → {status}."

        if old_status == "released" and status == "released":
            for f in planning_fields:
                if canonical(oo.get(f)) != canonical(o.get(f)):
                    return False, "MP-PLAN-032", f"Freigegebener Auftrag '{o.get('order') or oid}' ist eingefroren. Freigabe zuerst zurücknehmen."

        if old_status == "released" and status == "planned":
            if o.get("baselinePlan") is not None:
                return False, "MP-PLAN-032", f"Freigabeplan von '{o.get('order') or oid}' muss beim Zurücknehmen der Freigabe entfernt werden."
            for f in tuple(x for x in planning_fields if x != "baselinePlan"):
                if canonical(oo.get(f)) != canonical(o.get(f)):
                    return False, "MP-PLAN-032", f"Freigabe von '{o.get('order') or oid}' zuerst separat zurücknehmen; Umplanung erst danach."

        if old_status == "released" and status == "running":
            for f in core_without_machine_choice:
                if canonical(oo.get(f)) != canonical(o.get(f)):
                    return False, "MP-PLAN-032", f"Produktionsstart darf Planungsfeld '{f}' von '{o.get('order') or oid}' nicht verändern."
            if mid != str(oo.get("machineId", "")):
                allowed_alt = str(oo.get("altMachineId", "") or "") if oo.get("allowAlternative") else ""
                if not allowed_alt or mid != allowed_alt or str(o.get("altMachineId", "") or "") or bool(o.get("allowAlternative")):
                    return False, "MP-PLAN-033", f"Produktionsstart von '{o.get('order') or oid}' darf nur auf der vorgeplanten Alternativmaschine wechseln."
            else:
                if canonical(oo.get("altMachineId")) != canonical(o.get("altMachineId")) or canonical(oo.get("allowAlternative")) != canonical(o.get("allowAlternative")):
                    return False, "MP-PLAN-033", f"Produktionsstart von '{o.get('order') or oid}' darf die Alternativmaschinenplanung nicht verändern."

        if old_status in {"running", "paused"}:
            for f in planning_fields:
                if canonical(oo.get(f)) != canonical(o.get(f)):
                    return False, "MP-PROD-016", f"Laufender/pausierter Auftrag '{o.get('order') or oid}' darf Planungsfeld '{f}' nicht verändern."
            if canonical(oo.get("actualStartedAt")) != canonical(o.get("actualStartedAt")):
                return False, "MP-PROD-029", f"Produktionsstart von '{o.get('order') or oid}' ist unveränderlich."
            if old_status == "running" and status == "running" and canonical(oo.get("runningSince")) != canonical(o.get("runningSince")):
                return False, "MP-PROD-032", f"Laufbeginn der aktuellen Produktionsphase von '{o.get('order') or oid}' darf ohne Pause/Fortsetzen nicht verändert werden."
            if old_status == "paused" and status == "paused" and canonical(oo.get("pausedAt")) != canonical(o.get("pausedAt")):
                return False, "MP-PROD-032", f"Pausenbeginn von '{o.get('order') or oid}' darf während derselben Pause nicht verändert werden."

    # History is append-only on the server. This makes the displayed Ist-History a real invariant.
    old_hist = {str(h.get("id")): h for h in (old.get("history") or []) if isinstance(h, dict) and h.get("id")}
    new_history = new.get("history") or []
    if not isinstance(new_history, list):
        return False, "MP-HIST-001", "Historie ist ungültig."
    seen_hist = set()
    new_hist = {}
    for h in new_history:
        if not isinstance(h, dict) or not str(h.get("id", "")).strip():
            return False, "MP-HIST-001", "Historieneintrag ohne gültige ID."
        hid = str(h["id"])
        if hid in seen_hist:
            return False, "MP-HIST-001", f"Doppelte Historien-ID '{hid}'."
        seen_hist.add(hid)
        new_hist[hid] = h
        if (h.get("recordType") or "done") not in {"done", "cancelled"}:
            return False, "MP-HIST-002", f"Historieneintrag '{hid}' hat einen ungültigen Typ."
    for hid, h in old_hist.items():
        if hid not in new_hist or canonical(h) != canonical(new_hist[hid]):
            return False, "MP-HIST-003", f"Bestehender Historieneintrag '{hid}' darf nicht verändert oder gelöscht werden."
    added_hist = [h for hid, h in new_hist.items() if hid not in old_hist]
    added_by_order: dict[str, list[dict]] = {}
    historic_order_ids = set()
    for h in new_history:
        oid = str(h.get("originalOrderId", "") or "")
        if oid:
            historic_order_ids.add(oid)
    collision = seen_orders & historic_order_ids
    if collision:
        return False, "MP-HIST-005", f"Aktive Auftrag-ID '{sorted(collision)[0]}' existiert bereits in der Ist-Historie."
    for h in added_hist:
        oid = str(h.get("originalOrderId", "") or "")
        if oid:
            added_by_order.setdefault(oid, []).append(h)
        if h.get("actualStartedAt") not in (None, "") or h.get("actualFinishedAt") not in (None, ""):
            pair = _ordered_dt_pair(h.get("actualStartedAt"), h.get("actualFinishedAt"))
            if not pair:
                return False, "MP-HIST-006", f"Historieneintrag '{h.get('id')}' hat ungültige Ist-Start-/Endzeiten."
        if not _nonnegative_int(h.get("goodQty") or 0) or not _nonnegative_int(h.get("scrapQty") or 0):
            return False, "MP-HIST-006", f"Historieneintrag '{h.get('id')}' enthält ungültige Gut-/Ausschussmengen."
        if (h.get("recordType") or "done") == "cancelled" and h.get("actualStartedAt") and not str(h.get("abortReason", "")).strip():
            return False, "MP-HIST-006", f"Produktionsabbruch '{h.get('id')}' benötigt einen Abbruchgrund."
        actual_segments = h.get("actualSegments") or []
        if actual_segments:
            ok, reason, seg_first, seg_last = _validate_segments(actual_segments, allow_empty=True)
            if not ok:
                return False, "MP-HIST-006", f"Historieneintrag '{h.get('id')}': Ist-Segmente ungültig – {reason}"
            actual_pair = _ordered_dt_pair(h.get("actualStartedAt"), h.get("actualFinishedAt"))
            if actual_pair:
                a, z = actual_pair
                try:
                    if seg_first < a or seg_last > z:
                        return False, "MP-HIST-006", f"Historieneintrag '{h.get('id')}': Ist-Segmente liegen außerhalb von Ist-Start/-Ende."
                except TypeError:
                    return False, "MP-HIST-006", f"Historieneintrag '{h.get('id')}': Ist-Zeiten verwenden inkompatible Formate."

    removed = set(old_orders) - seen_orders
    history_copy_fields = ("machineId", "projectId", "fs", "ab", "wt", "order", "articleNo", "description", "targetQty", "hours", "direction", "anchorMode", "requiredStart", "requiredFinish", "createdAt", "baselinePlan")
    for oid in removed:
        old_order = old_orders[oid]
        old_status = str(old_order.get("status", "planned"))
        linked = added_by_order.get(oid, [])
        if old_status in {"running", "paused"}:
            if len(linked) != 1 or (linked[0].get("recordType") or "done") not in {"done", "cancelled"}:
                return False, "MP-PROD-017", f"Entfernter Produktionsauftrag '{old_order.get('order') or oid}' benötigt genau eine Fertig-/Abbruchhistorie."
            hist = linked[0]
            for field in history_copy_fields + ("actualStartedAt",):
                if canonical(old_order.get(field)) != canonical(hist.get(field)):
                    return False, "MP-HIST-007", f"Fertig-/Abbruchhistorie von '{old_order.get('order') or oid}' verändert kopiertes Feld '{field}'."
            if not _ordered_dt_pair(hist.get("actualStartedAt"), hist.get("actualFinishedAt")):
                return False, "MP-HIST-007", f"Fertig-/Abbruchhistorie von '{old_order.get('order') or oid}' benötigt gültige Ist-Start-/Endzeit."
        elif old_status == "released":
            if len(linked) != 1 or (linked[0].get("recordType") or "done") != "cancelled":
                return False, "MP-PLAN-052", f"Freigegebener Auftrag '{old_order.get('order') or oid}' darf nur mit Stornohistorie entfernt werden."
            hist = linked[0]
            for field in history_copy_fields:
                if canonical(old_order.get(field)) != canonical(hist.get(field)):
                    return False, "MP-HIST-007", f"Stornohistorie von '{old_order.get('order') or oid}' verändert kopiertes Feld '{field}'."
        elif len(linked) > 1:
            return False, "MP-HIST-004", f"Auftrag '{old_order.get('order') or oid}' besitzt mehrere neue Historieneinträge."

    blocks = new.get("machineBlocks") or []
    if not isinstance(blocks, list):
        return False, "MP-CAL-020", "Maschinensperren sind ungültig."
    block_ids = set()
    for b in blocks:
        if not isinstance(b, dict) or str(b.get("machineId", "")) not in midset:
            return False, "MP-CAL-020", "Maschinensperre verweist auf eine unbekannte Maschine."
        bid = str(b.get("id", "") or "")
        if not bid or bid in block_ids:
            return False, "MP-CAL-022", "Maschinensperre benötigt eine eindeutige ID."
        block_ids.add(bid)
        if not _valid_local_datetime(b.get("start"), allow_empty=False) or not _valid_local_datetime(b.get("end"), allow_empty=False):
            return False, "MP-CAL-021", "Maschinensperre enthält ungültige lokale Von-/Bis-Zeit."
        pair = _ordered_dt_pair(b.get("start"), b.get("end"))
        if not pair:
            return False, "MP-CAL-021", "Maschinensperre: Bis muss nach Von liegen."

    feasible, gate_code, gate_reason = validate_release_feasibility(old, new)
    if not feasible:
        return False, gate_code, gate_reason

    return True, "", ""

def _need_key(x: dict) -> tuple[str, str]:
    return str(x.get("departmentId")), str(x.get("weekStart"))


def _need_map(state: dict) -> dict:
    return {_need_key(x): x for x in (state.get("departmentStaffNeeds") or []) if isinstance(x, dict)}


def gf_change_allowed(old: dict, new: dict) -> tuple[bool, str]:
    allowed_root = {"departmentStaffNeeds", "weeklyEmployeeDeployments", "exceptions", "audit", "meta", "ui"}
    for key in set(old) | set(new):
        if key not in allowed_root and canonical(old.get(key)) != canonical(new.get(key)):
            return False, f"GF darf operative Produktionsdaten '{key}' nicht ändern."
    # MP-AUD-002: Die Abteilung meldet (requested), die GF bestätigt (confirmed).
    old_needs, new_needs = _need_map(old), _need_map(new)
    for key in set(old_needs) | set(new_needs):
        a, b = old_needs.get(key), new_needs.get(key)
        if canonical(a) == canonical(b):
            continue
        if b is None:
            return False, "GF darf Personalbedarfsmeldungen nicht löschen."
        if a is None:
            if b.get("requested", 0) != 0:
                return False, "GF darf keinen Bedarf im Namen der Abteilung melden."
            continue
        if a.get("requested") != b.get("requested"):
            return False, "GF darf den gemeldeten Bedarf der Abteilung nicht ändern, nur bestätigen."
        extra = {k for k in set(a) | set(b) if k not in {"confirmed", "updatedAt"} and canonical(a.get(k)) != canonical(b.get(k))}
        if extra:
            return False, f"GF darf beim Personalbedarf nur die Bestätigung ändern ({sorted(extra)[0]})."
    return True, ""


def project_management_change_allowed(old: dict, new: dict) -> tuple[bool, str]:
    allowed_root = {"projects", "processTemplates", "audit", "meta", "ui"}
    for key in set(old) | set(new):
        if key not in allowed_root and canonical(old.get(key)) != canonical(new.get(key)):
            return False, f"Projektmanagement darf Produktionsdaten '{key}' nicht ändern."
    return True, ""


def sales_change_allowed(old: dict, new: dict) -> tuple[bool, str]:
    allowed_root = {"projects", "audit", "meta", "ui"}
    for key in set(old) | set(new):
        if key not in allowed_root and canonical(old.get(key)) != canonical(new.get(key)):
            return False, f"Vertrieb darf '{key}' nicht ändern."
    return True, ""


# Phasenwechsel je Rolle (Admin: alle). Nach der Annahme wird der Produktionsstand
# nicht gespeichert, sondern aus den verknüpften FS abgeleitet.
PROJECT_TRANSITIONS = {
    "sales": {("inquiry", "pm"), ("inquiry", "lost"), ("offer_sent", "accepted"), ("offer_sent", "lost")},
    "project_management": {("inquiry", "pm"), ("inquiry", "lost"), ("pm", "offer_sent"), ("pm", "lost"), ("offer_sent", "pm"),
                           ("offer_sent", "accepted"), ("offer_sent", "lost"), ("lost", "pm"), ("accepted", "closed")},
    "production_planning": {("accepted", "closed")},
}
PROJECT_BASE_FIELDS = {"customer", "contact", "name", "note", "wt", "workflow"}
PROJECT_FIELDS = {
    "sales": PROJECT_BASE_FIELDS | {"ab", "dueDate", "phase", "log", "updatedAt", "processes"},
    "project_management": PROJECT_BASE_FIELDS | {"ab", "dueDate", "phase", "log", "updatedAt", "processes", "offer", "development"},
    "production_planning": {"phase", "log", "updatedAt", "processes"},
    "department": {"log", "updatedAt", "processes"},
}
PROCESS_SELF_FIELDS = {"status", "note", "owner", "workStepId"}
# PM und AV planen Termine und verwalten; Rückmeldungen (Status) kommen aus den Abteilungen.
PROCESS_TIMING_FIELDS = {"startDate", "dueDate", "owner", "note", "workStepId"}
PM_STATUS_AREAS = {"engineering", "calculation", "purchasing", "quality", "pm"}


def project_changes_allowed(old: dict, new: dict, role: str, username: str, department_id: str = "") -> tuple[bool, str]:
    """Feldgenaue Rechte für Projekte. Admin wird vorher ausgenommen."""
    kind = "department" if role in DEPARTMENT_ROLES else role
    # Eigene Prozessbereiche: Abteilung -> ihr Bereich, Vertrieb -> "sales",
    # Arbeitsvorbereitung -> alle Fertigungsbereiche (Übernahme in die Fertigung).
    if kind == "department":
        own_areas = {department_id} if department_id else set()
    elif role == "sales":
        own_areas = {"sales"}
    elif role == "production_planning":
        own_areas = {str(d.get("id")) for d in (new.get("departments") or []) if isinstance(d, dict)} | {"av"}
    else:
        own_areas = set()
    fields = PROJECT_FIELDS.get(kind, set())
    om, nm = _record_map(old.get("projects")), _record_map(new.get("projects"))
    for pid in set(om) | set(nm):
        a, b = om.get(pid), nm.get(pid)
        if canonical(a) == canonical(b):
            continue
        label = str((b or a).get("number") or pid)
        if a is None:
            if role == "sales" and str(b.get("phase")) == "inquiry":
                pass
            elif role == "project_management" and str(b.get("phase")) in {"inquiry", "pm"}:
                pass
            else:
                return False, f"Diese Rolle darf kein Projekt in dieser Phase anlegen ({label})."
            if any(str(x.get("actor") or "") != username for x in (b.get("log") or [])):
                return False, f"Projekt {label}: Verlaufseinträge müssen den angemeldeten Benutzer tragen."
            continue
        if b is None:
            if role != "project_management" or str(a.get("phase")) not in {"inquiry", "pm", "lost"}:
                return False, f"Projekt {label} darf von dieser Rolle bzw. in dieser Phase nicht gelöscht werden."
            continue
        changed = {k for k in set(a) | set(b) if canonical(a.get(k)) != canonical(b.get(k))}
        extra = changed - fields
        if extra:
            return False, f"Projekt {label}: Feld '{sorted(extra)[0]}' darf von dieser Rolle nicht geändert werden."
        pa, pb = str(a.get("phase")), str(b.get("phase"))
        if pa != pb and (pa, pb) not in PROJECT_TRANSITIONS.get(role, set()):
            return False, f"Projekt {label}: Phasenwechsel {pa} → {pb} ist für diese Rolle nicht erlaubt."
        if role == "sales" and (changed & PROJECT_BASE_FIELDS) and pa not in {"inquiry", "pm", "offer_sent"}:
            return False, f"Projekt {label}: Nach der Annahme ändert der Vertrieb die Stammdaten nicht mehr."
        if role == "sales" and (changed & {"ab", "dueDate"}) and not (pa in {"inquiry", "pm", "offer_sent"} or pb == "accepted"):
            return False, f"Projekt {label}: AB und Liefertermin setzt der Vertrieb nur bis zur Annahme."
        if "processes" in changed:
            if kind == "project_management":
                ok, reason = _pm_process_change(a, b, label)
            elif role == "production_planning":
                ok, reason = _av_process_change(a, b, own_areas, label)
            else:
                ok, reason = _own_area_process_change(a, b, own_areas, label, PROCESS_SELF_FIELDS)
            if not ok:
                return False, reason
        old_log = {str(x.get("id")): x for x in (a.get("log") or []) if isinstance(x, dict)}
        new_log = {str(x.get("id")): x for x in (b.get("log") or []) if isinstance(x, dict)}
        for lid, entry in old_log.items():
            if lid not in new_log or canonical(entry) != canonical(new_log[lid]):
                return False, f"Projekt {label}: Verlaufseinträge dürfen nicht verändert oder gelöscht werden."
        for lid, entry in new_log.items():
            if lid not in old_log and str(entry.get("actor") or "") != username:
                return False, f"Projekt {label}: Neue Verlaufseinträge müssen den angemeldeten Benutzer tragen."
    return True, ""


def _pm_process_change(a: dict, b: dict, label: str) -> tuple[bool, str]:
    """PM verwaltet Prozesse (anlegen, zuweisen, Termine). Status meldet die Abteilung;
    nur für Bereiche ohne eigenes Konto (Konstruktion, Kalkulation, Einkauf, QS, PM) setzt ihn das PM."""
    pa = {str(x.get("id")): x for x in (a.get("processes") or []) if isinstance(x, dict)}
    pb = {str(x.get("id")): x for x in (b.get("processes") or []) if isinstance(x, dict)}
    for pid, y in pb.items():
        x = pa.get(pid)
        before = str((x or {}).get("status") or "open")
        after = str(y.get("status") or "open")
        if before != after and str(y.get("areaId")) not in PM_STATUS_AREAS:
            return False, f"Projekt {label}: Den Status von „{y.get('title')}“ meldet die zuständige Abteilung, nicht das Projektmanagement."
        if x is None and after != "open" and str(y.get("areaId")) not in PM_STATUS_AREAS:
            return False, f"Projekt {label}: Neue Abteilungsprozesse starten mit Status „Offen“."
    return True, ""


def _av_process_change(a: dict, b: dict, own_areas: set, label: str) -> tuple[bool, str]:
    """AV plant Fertigungsabläufe: Prozesse in Fertigungsbereichen anlegen, terminieren,
    offene entfernen. Den Status meldet die Abteilung."""
    pa = {str(x.get("id")): x for x in (a.get("processes") or []) if isinstance(x, dict)}
    pb = {str(x.get("id")): x for x in (b.get("processes") or []) if isinstance(x, dict)}
    for pid in set(pa) | set(pb):
        x, y = pa.get(pid), pb.get(pid)
        if canonical(x) == canonical(y):
            continue
        for side in (x, y):
            if side is not None and str(side.get("areaId")) not in own_areas:
                return False, f"Projekt {label}: Arbeitsvorbereitung plant nur Prozesse in Fertigungsbereichen."
        if x is None:
            if str(y.get("status") or "open") != "open":
                return False, f"Projekt {label}: Neue Prozesse starten mit Status „Offen“."
            continue
        if y is None:
            if str(x.get("status") or "open") != "open" or x.get("workStepId"):
                return False, f"Projekt {label}: Nur offene, noch nicht übernommene Prozesse dürfen entfernt werden."
            continue
        diff = {k for k in set(x) | set(y) if canonical(x.get(k)) != canonical(y.get(k))}
        if diff - PROCESS_TIMING_FIELDS:
            return False, f"Projekt {label}: Feld '{sorted(diff - PROCESS_TIMING_FIELDS)[0]}' am Prozess „{y.get('title')}“ ist für die Arbeitsvorbereitung nicht änderbar."
    return True, ""


def _own_area_process_change(a: dict, b: dict, own_areas: set, label: str, allowed_fields: set = PROCESS_SELF_FIELDS) -> tuple[bool, str]:
    """Bereiche/Vertrieb/AV pflegen nur Status, Notiz und Verantwortlichen ihrer eigenen Prozesse."""
    pa = {str(x.get("id")): x for x in (a.get("processes") or []) if isinstance(x, dict)}
    pb = {str(x.get("id")): x for x in (b.get("processes") or []) if isinstance(x, dict)}
    if set(pa) != set(pb):
        return False, f"Projekt {label}: Prozesse anlegen oder entfernen darf nur das Projektmanagement."
    for pid, x in pa.items():
        y = pb[pid]
        if canonical(x) == canonical(y):
            continue
        if str(x.get("areaId")) not in own_areas or str(y.get("areaId")) not in own_areas:
            return False, f"Projekt {label}: Nur Prozesse des eigenen Bereichs dürfen geändert werden."
        diff = {k for k in set(x) | set(y) if canonical(x.get(k)) != canonical(y.get(k))}
        if diff - allowed_fields:
            return False, f"Projekt {label}: Feld '{sorted(diff - allowed_fields)[0]}' am Prozess „{y.get('title')}“ ist für diese Rolle nicht änderbar."
    return True, ""


AUDIT_CAP = 1000


def audit_change_allowed(old: dict, new: dict, username: str) -> tuple[bool, str]:
    """MP-AUD-014 (Teil): Das Client-Protokoll ist für Nicht-Admins append-only.

    Bestehende Einträge dürfen nicht verändert werden. Entfernen ist nur beim
    Kappen auf AUDIT_CAP erlaubt und nur für die ältesten Einträge. Neue
    Einträge müssen den angemeldeten Benutzer als Akteur tragen.
    """
    old_list = [x for x in (old.get("audit") or []) if isinstance(x, dict)]
    new_list = new.get("audit") or []
    if not isinstance(new_list, list) or any(not isinstance(x, dict) or not x.get("id") for x in new_list):
        return False, "Protokolleinträge benötigen eine ID."
    om = {str(x.get("id")): x for x in old_list if x.get("id")}
    nm = {str(x.get("id")): x for x in new_list}
    if len(nm) != len(new_list):
        return False, "Doppelte Protokoll-ID."
    removed = [x for i, x in om.items() if i not in nm]
    for i, x in om.items():
        if i in nm and canonical(x) != canonical(nm[i]):
            return False, "Bestehende Protokolleinträge dürfen nicht verändert werden."
    if removed:
        # Der Client kappt mit unshift()+slice(0, AUDIT_CAP): Es fallen immer die
        # letzten (ältesten) Positionen weg. Positionsbasiert statt Zeitstempel,
        # weil Browseruhren voneinander abweichen können.
        old_order = [str(x.get("id")) for x in old_list if x.get("id")]
        removed_ids = {str(x.get("id")) for x in removed}
        if len(new_list) < AUDIT_CAP or set(old_order[-len(removed_ids):]) != removed_ids:
            return False, "Protokolleinträge dürfen nicht gelöscht werden."
    for i, x in nm.items():
        if i not in om and str(x.get("actor") or "") != username:
            return False, "Neue Protokolleinträge müssen den angemeldeten Benutzer als Akteur tragen."
    return True, ""


def production_planning_change_allowed(old: dict, new: dict) -> tuple[bool, str]:
    """Arbeitsvorbereitung: legt Aufträge/Arbeitsgänge in allen Bereichen an und plant sie.

    Erlaubt sind nur Änderungen an geplanten Arbeitsgängen (anlegen, ändern,
    löschen, AB-Verknüpfung). Freigabe, Produktion, Fertigmeldung, Personal,
    Maschinen und Einstellungen bleiben bei Bereichen/Admin.
    """
    allowed_root = {"workSteps", "projects", "processTemplates", "audit", "meta", "ui", "planVersions"}
    for key in set(old) | set(new):
        if key not in allowed_root and canonical(old.get(key)) != canonical(new.get(key)):
            return False, f"Arbeitsvorbereitung darf '{key}' nicht ändern."
    a, b = _record_map(old.get("workSteps")), _record_map(new.get("workSteps"))
    for rid in set(a) | set(b):
        if canonical(a.get(rid)) == canonical(b.get(rid)):
            continue
        for side in (a.get(rid), b.get(rid)):
            if side is not None and str(side.get("status") or "planned") != "planned":
                return False, f"Arbeitsvorbereitung ändert nur geplante Aufträge ('{side.get('fs') or side.get('order') or rid}' ist {side.get('status')})."
        before, after = a.get(rid), b.get(rid)
        done_before = _finite_float((before or {}).get("doneHours", 0)) or 0.0
        done_after = _finite_float((after or {}).get("doneHours", 0)) or 0.0
        if after is not None and done_after != done_before:
            return False, f"Erledigte Stunden meldet die Abteilung, nicht die Arbeitsvorbereitung ('{after.get('fs') or rid}')."
        for f in ("goodQty", "scrapQty"):
            if after is not None and canonical((before or {}).get(f, 0) or 0) != canonical(after.get(f, 0) or 0):
                return False, f"Produktionsmengen meldet die Abteilung, nicht die Arbeitsvorbereitung ('{after.get('fs') or rid}')."
    return True, ""


def _record_map(items):
    return {str(x.get("id")): x for x in (items or []) if isinstance(x, dict) and x.get("id")}


def department_change_allowed(old: dict, new: dict, department_id: str) -> tuple[bool, str]:
    if not department_id:
        return False, "Kein Bereich am Benutzer hinterlegt."
    globally_allowed = {"audit", "meta", "ui", "planVersions", "departmentStaffNeeds"}
    scoped = {"workSteps", "machines", "machineBlocks", "employees", "personnelAssignments", "personnelAbsences", "history", "yearRules", "weekRules", "projects"}
    for key in set(old) | set(new):
        if key in globally_allowed or key in scoped:
            continue
        if canonical(old.get(key)) != canonical(new.get(key)):
            return False, f"Bereichsrolle darf globale Einstellung '{key}' nicht ändern."

    if canonical(old.get("exceptions")) != canonical(new.get("exceptions")):
        return False, "Globale Betriebsferien/Kalender-Ausnahmen dürfen nur GF/Admin ändern."
    for key in ("shiftTemplates", "operatorCapacity"):
        if canonical(old.get(key)) != canonical(new.get(key)) and department_id != "cnc":
            return False, f"Globale CNC-Einstellung '{key}' darf dieser Bereich nicht ändern."
    if canonical(old.get("weeklyEmployeeDeployments")) != canonical(new.get("weeklyEmployeeDeployments")):
        return False, "Interne KW-Versetzungen dürfen nur GF/Admin ändern."
    old_needs = {(str(x.get("departmentId")), str(x.get("weekStart"))): x for x in (old.get("departmentStaffNeeds") or []) if isinstance(x, dict)}
    new_needs = {(str(x.get("departmentId")), str(x.get("weekStart"))): x for x in (new.get("departmentStaffNeeds") or []) if isinstance(x, dict)}
    for key in set(old_needs) | set(new_needs):
        a, b = old_needs.get(key), new_needs.get(key)
        if canonical(a) == canonical(b):
            continue
        if key[0] != department_id:
            return False, "Personalbedarf eines fremden Bereichs darf nicht geändert werden."
        if a is not None and b is not None and a.get("confirmed") != b.get("confirmed"):
            # Neue Meldung -> Bestätigung verfällt (zurück auf offen). Sonst nur GF.
            if not (b.get("confirmed") is None and a.get("requested") != b.get("requested")):
                return False, "GF-Bestätigung darf die Abteilung nicht ändern."
        if a is None and b is not None and b.get("confirmed") is not None:
            return False, "Neue Bedarfsmeldung darf keine GF-Bestätigung setzen."
        if a is not None and b is None and a.get("confirmed") is not None:
            return False, "Von der GF bestätigter Personalbedarf darf nicht gelöscht werden."

    # Zuordnung aus dem ALTEN Stand (Fallback: neu angelegte Datensätze), damit ein
    # Umhängen im selben Speichervorgang keine fremden Sperren/Regeln freischaltet.
    machine_dept = {str(m.get("id")): str(m.get("departmentId") or "cnc") for m in (new.get("machines") or []) if isinstance(m, dict)}
    machine_dept.update({str(m.get("id")): str(m.get("departmentId") or "cnc") for m in (old.get("machines") or []) if isinstance(m, dict)})
    old_orders = {k:v for k,v in _record_map(old.get("workSteps")).items() if v.get("planningType") == "MACHINE"}
    new_orders = {k:v for k,v in _record_map(new.get("workSteps")).items() if v.get("planningType") == "MACHINE"}
    employee_dept = {str(e.get("id")): str(e.get("departmentId") or "") for e in (new.get("employees") or []) if isinstance(e, dict)}
    employee_dept.update({str(e.get("id")): str(e.get("departmentId") or "") for e in (old.get("employees") or []) if isinstance(e, dict)})

    # MP-AUD-032: Bei Änderungen müssen alter UND neuer Stand im eigenen Bereich liegen.
    # Sonst könnte ein Datensatz durch Umhängen des Bereichs "übernommen" werden.
    def changed_records(name):
        a, b = _record_map(old.get(name)), _record_map(new.get(name))
        for rid in set(a) | set(b):
            if canonical(a.get(rid)) != canonical(b.get(rid)):
                for side in (a.get(rid), b.get(rid)):
                    if side is not None:
                        yield side

    def changed_keyed(name, key_fn):
        a = {key_fn(x): x for x in (old.get(name) or []) if isinstance(x, dict)}
        b = {key_fn(x): x for x in (new.get(name) or []) if isinstance(x, dict)}
        for rid in set(a) | set(b):
            if canonical(a.get(rid)) != canonical(b.get(rid)):
                for side in (a.get(rid), b.get(rid)):
                    if side is not None:
                        yield side

    for rec in changed_records("machines"):
        if str(rec.get("departmentId") or "cnc") != department_id:
            return False, "Maschine gehört nicht zum eigenen Bereich."
    for rec in changed_records("workSteps"):
        did = str(rec.get("departmentId") or machine_dept.get(str(rec.get("machineId")), "cnc"))
        if did != department_id:
            return False, "Arbeitsgang gehört nicht zum eigenen Bereich."
    old_steps = _record_map(old.get("workSteps"))
    for rid, rec in _record_map(new.get("workSteps")).items():
        before = old_steps.get(rid)
        if before is not None and str(before.get("dueDate") or "") != str(rec.get("dueDate") or ""):
            return False, "Den AV-Termin (fertig bis) legt die Arbeitsvorbereitung fest. Die Abteilung plant innerhalb dieses Termins."
    for rec in changed_records("machineBlocks"):
        if machine_dept.get(str(rec.get("machineId")), "") != department_id:
            return False, "Maschinensperre gehört nicht zum eigenen Bereich."
    for rec in changed_keyed("yearRules", lambda x: f"{x.get('machineId')}|{x.get('year')}"):
        if machine_dept.get(str(rec.get("machineId")), "") != department_id:
            return False, "Jahres-Schichtregel gehört nicht zum eigenen Bereich."
    for rec in changed_keyed("weekRules", lambda x: f"{x.get('machineId')}|{x.get('year')}|{x.get('week')}"):
        if machine_dept.get(str(rec.get("machineId")), "") != department_id:
            return False, "KW-Schichtregel gehört nicht zum eigenen Bereich."
    for rec in changed_records("employees"):
        if str(rec.get("departmentId") or "") != department_id:
            return False, "Mitarbeiter gehört nicht zum eigenen Bereich."
    # KW-Einsatz (nur GF/Admin änderbar, daher aus dem alten Stand): In der Einsatz-KW
    # plant der aufnehmende Bereich den Mitarbeiter, nicht der Stammbereich.
    deployment_old = _deployment_map(old)

    def employee_dept_on(rec):
        eid = str(rec.get("employeeId"))
        try:
            wk = _week_start_key(rec.get("date"))
        except (TypeError, ValueError):
            wk = ""
        return deployment_old.get((eid, wk)) or employee_dept.get(eid, "")

    for rec in changed_keyed("personnelAssignments", lambda x: f"{x.get('employeeId')}|{x.get('date')}"):
        if employee_dept_on(rec) != department_id:
            return False, "Personalzuordnung gehört nicht zum eigenen Bereich."
    for rec in changed_keyed("personnelAbsences", lambda x: f"{x.get('employeeId')}|{x.get('date')}"):
        if employee_dept_on(rec) != department_id:
            return False, "Abwesenheit gehört nicht zum eigenen Bereich."
    for rec in changed_records("history"):
        oid = str(rec.get("originalOrderId") or "")
        source = old_orders.get(oid) or new_orders.get(oid) or {}
        did = str(source.get("departmentId") or machine_dept.get(str(source.get("machineId")), "cnc"))
        if did != department_id:
            return False, "Historieneintrag gehört nicht zum eigenen Bereich."
    return True, ""


def prune_sessions(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))


def failed_login_blocked(ip: str) -> bool:
    now = time.time()
    with LOGIN_LOCK:
        xs = [t for t in LOGIN_FAILS.get(ip, []) if now - t < 600]
        LOGIN_FAILS[ip] = xs
        return len(xs) >= 8


def record_failed_login(ip: str) -> None:
    with LOGIN_LOCK:
        LOGIN_FAILS.setdefault(ip, []).append(time.time())


def clear_failed_login(ip: str) -> None:
    with LOGIN_LOCK:
        LOGIN_FAILS.pop(ip, None)


def client_ip_allowed(ip: str) -> bool:
    if ALLOWED_NETWORK is None:
        return False
    try:
        return ipaddress.ip_address(ip) in ALLOWED_NETWORK
    except ValueError:
        return False


class MPHTTPServer(ThreadingHTTPServer):
    # 30 gleichzeitige Browser + kurze Bursts bei Login/Reload/Long-Poll-Reconnect.
    request_queue_size = 128
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = f"ProduktionsplanungV{APP_VERSION}/1.0"

    def handle(self):
        # Defense in depth: clients outside the configured LAN subnet are
        # dropped before HTTP parsing, even if a firewall/router rule is wrong.
        if not client_ip_allowed(self.client_address[0]):
            try:
                self.request.close()
            finally:
                return
        try:
            return super().handle()
        except (BrokenPipeError, ConnectionResetError):
            # Browser hat die Verbindung (z. B. Long-Poll beim Schließen des Tabs) beendet.
            return

    def log_message(self, fmt, *args):
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))

    def log_request(self, code="-", size="-"):
        # Nur Fehlerantworten protokollieren; normale Anfragen würden das Server-Log fluten.
        try:
            if int(code) < 400:
                return
        except (TypeError, ValueError):
            pass
        super().log_request(code, size)

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def json_response(self, code: int, body: dict, cookie: str | None = None):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Ungültige Content-Length")
        if n <= 0 or n > MAX_BODY:
            raise ValueError("Ungültige oder zu große Anfrage")
        raw = self.rfile.read(n)
        def reject_constant(value):
            raise ValueError(f"Nicht standardkonforme JSON-Zahl: {value}")
        obj = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
        if not isinstance(obj, dict):
            raise ValueError("JSON-Objekt erwartet")
        return obj

    def session_user(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        token = c.get("mp_session")
        if not token:
            return None
        token_hash = hashlib.sha256(token.value.encode()).hexdigest()
        with DB_LOCK, db_session() as con:
            prune_sessions(con)
            row = con.execute(
                "SELECT u.id,u.username,u.role,u.department_id,u.active,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
                (token_hash,),
            ).fetchone()
            if not row or not row["active"] or row["expires_at"] < int(time.time()):
                return None
            return dict(row)

    def require_user(self, roles=None):
        user = self.session_user()
        if not user:
            self.json_response(401, mp_error("MP-AUTH-001", "Nicht angemeldet."))
            return None
        if roles and user["role"] not in roles:
            self.json_response(403, mp_error("MP-AUTH-002", "Keine Berechtigung."))
            return None
        return user

    def require_current_client(self) -> bool:
        client_version = str(self.headers.get("X-MP-Client-Version", "")).strip()
        if client_version != APP_VERSION:
            self.json_response(426, mp_error("MP-SYNC-002", "Client-Version veraltet. Seite vollständig neu laden.", serverVersion=APP_VERSION))
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            return self.json_response(200, {"ok": True, "version": APP_VERSION})
        if path == "/api/session":
            user = self.require_user()
            if not user:
                return
            return self.json_response(200, {"user": {"id": user["id"], "username": user["username"], "role": user["role"], "departmentId": user.get("department_id", "")}})
        if path == "/api/state":
            user = self.require_user()
            if not user:
                return
            with DB_LOCK, db_session() as con:
                row = con.execute("SELECT revision,json,updated_at,updated_by FROM state WHERE id=1").fetchone()
            return self.json_response(200, {"revision": row["revision"], "data": json.loads(row["json"]), "updatedAt": row["updated_at"], "updatedBy": row["updated_by"]})
        if path == "/api/revision":
            user = self.require_user()
            if not user:
                return
            qs = parse_qs(parsed.query)
            try:
                since = int(qs.get("since", ["-1"])[0])
            except Exception:
                since = -1
            try:
                wait_ms = max(0, min(25000, int(qs.get("wait", ["0"])[0])))
            except Exception:
                wait_ms = 0
            deadline = time.monotonic() + wait_ms / 1000.0
            with REVISION_CONDITION:
                while True:
                    with DB_LOCK, db_session() as con:
                        row = con.execute("SELECT revision,updated_at,updated_by FROM state WHERE id=1").fetchone()
                    if int(row["revision"]) != since or wait_ms <= 0:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    REVISION_CONDITION.wait(remaining)
            return self.json_response(200, {"revision": row["revision"], "updatedAt": row["updated_at"], "updatedBy": row["updated_by"], "version": APP_VERSION})
        if path == "/api/users":
            user = self.require_user(USER_MANAGER_ROLES)
            if not user:
                return
            with DB_LOCK, db_session() as con:
                if user["role"] == "admin":
                    rows = con.execute("SELECT id,username,role,department_id,active,created_at,updated_at FROM users ORDER BY username COLLATE NOCASE").fetchall()
                else:
                    rows = con.execute("SELECT id,username,role,department_id,active,created_at,updated_at FROM users WHERE department_id=? ORDER BY username COLLATE NOCASE", (str(user.get("department_id") or ""),)).fetchall()
                    manageable = MANAGEABLE_ROLES.get(user["role"], set())
                    rows = [r for r in rows if r["role"] in manageable]
            return self.json_response(200, {"users": [dict(r) for r in rows]})
        if path in {"/", "/index.html"}:
            return self.serve_file(INDEX_PATH, "text/html; charset=utf-8")
        # Self-contained page: never expose files from BASE/data/backups/scripts.
        self.send_error(404)
        return

    def serve_file(self, path: Path, ctype: str):
        if not path.exists():
            self.send_error(404, "index.html fehlt")
            return
        raw = path.read_bytes()
        self.send_response(200)
        self._security_headers()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self.read_json()
        except Exception as e:
            return self.json_response(400, mp_error("MP-DATA-013", str(e)))
        if path == "/api/login":
            ip = self.client_address[0]
            if failed_login_blocked(ip):
                return self.json_response(429, mp_error("MP-AUTH-003", "Zu viele Fehlversuche. Bitte später erneut versuchen."))
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))
            with DB_LOCK, db_session() as con:
                row = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            # Passwortprüfung (~0,2 s) bewusst außerhalb der globalen DB-Sperre.
            # Unbekannte Benutzer durchlaufen dieselbe Rechenzeit (kein Timing-Hinweis).
            if row:
                ok = verify_password(password, row["salt"], row["password_hash"]) and bool(row["active"])
            else:
                verify_password(password, DUMMY_SALT, DUMMY_HASH)
                ok = False
            if not ok:
                record_failed_login(ip)
                return self.json_response(401, mp_error("MP-AUTH-004", "Benutzer oder Passwort falsch."))
            clear_failed_login(ip)
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            expires = int(time.time()) + SESSION_TTL
            with DB_LOCK, db_session() as con:
                con.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (token_hash, row["id"], expires, now_iso()))
            cookie = f"mp_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}"
            return self.json_response(200, {"user": {"id": row["id"], "username": row["username"], "role": row["role"], "departmentId": row["department_id"]}}, cookie)
        if path == "/api/logout":
            c = SimpleCookie(self.headers.get("Cookie", ""))
            token = c.get("mp_session")
            if token:
                th = hashlib.sha256(token.value.encode()).hexdigest()
                with DB_LOCK, db_session() as con:
                    con.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
            return self.json_response(200, {"ok": True}, "mp_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")
        if path == "/api/users":
            user = self.require_user(USER_MANAGER_ROLES)
            if not user:
                return
            if not self.require_current_client():
                return
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))
            role = str(body.get("role", "viewer"))
            department_id = str(body.get("departmentId", "") or "").strip()
            if user["role"] != "admin":
                own = str(user.get("department_id") or "")
                if not own or role not in MANAGEABLE_ROLES.get(user["role"], set()):
                    return self.json_response(403, mp_error("MP-AUTH-021", "Diese Rolle darf im eigenen Bereich nur Stellvertretungen (Leitung) bzw. Lesende (Stellvertretung) anlegen."))
                department_id = own
            if len(username) < 2 or len(password) < 8 or role not in ROLES or (role in DEPARTMENT_ROLES and not department_id):
                return self.json_response(400, mp_error("MP-AUTH-010", "Benutzername, Passwort, Rolle oder Bereich ungültig."))
            salt, digest = hash_password(password)
            ts = now_iso()
            try:
                with DB_LOCK, db_session() as con:
                    cur = con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)", (username, salt, digest, role, department_id, ts, ts))
                    con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,NULL)", (ts, user["username"], "Benutzer angelegt", username))
                return self.json_response(201, {"id": cur.lastrowid, "username": username, "role": role, "departmentId": department_id, "active": True})
            except sqlite3.IntegrityError:
                return self.json_response(409, mp_error("MP-AUTH-013", "Benutzername existiert bereits."))
        if path == "/api/password":
            return self.change_own_password(body)
        return self.json_response(404, mp_error("MP-REQ-404", "Nicht gefunden."))

    def change_own_password(self, body: dict):
        """Self-service: jede angemeldete Rolle darf das eigene Passwort ändern."""
        user = self.require_user()
        if not user:
            return
        if not self.require_current_client():
            return
        current = str(body.get("currentPassword", ""))
        new_pw = str(body.get("newPassword", ""))
        if len(new_pw) < 8:
            return self.json_response(400, mp_error("MP-AUTH-017", "Passwort muss mindestens 8 Zeichen haben."))
        with DB_LOCK, db_session() as con:
            row = con.execute("SELECT salt,password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not row or not verify_password(current, row["salt"], row["password_hash"]):
            record_failed_login(self.client_address[0])
            return self.json_response(403, mp_error("MP-AUTH-023", "Aktuelles Passwort ist falsch."))
        salt, digest = hash_password(new_pw)
        c = SimpleCookie(self.headers.get("Cookie", ""))
        keep = hashlib.sha256(c["mp_session"].value.encode()).hexdigest()
        with DB_LOCK, db_session() as con:
            con.execute("UPDATE users SET salt=?,password_hash=?,updated_at=? WHERE id=?", (salt, digest, now_iso(), user["id"]))
            # Andere Sitzungen dieses Kontos beenden, die aktuelle bleibt gültig.
            con.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (user["id"], keep))
            con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,NULL)", (now_iso(), user["username"], "Eigenes Passwort geändert", user["username"]))
        return self.json_response(200, {"ok": True})

    def _method_not_allowed(self):
        self.json_response(405, mp_error("MP-REQ-405", "Methode nicht erlaubt."))

    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    def do_PATCH(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/users/"):
            return self.json_response(404, mp_error("MP-REQ-404", "Nicht gefunden."))
        user = self.require_user(USER_MANAGER_ROLES)
        if not user:
            return
        if not self.require_current_client():
            return
        try:
            uid = int(path.rsplit("/", 1)[1])
            body = self.read_json()
        except Exception:
            return self.json_response(400, mp_error("MP-REQ-002", "Ungültige Anfrage."))
        with DB_LOCK, db_session() as con:
            target = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not target:
                return self.json_response(404, mp_error("MP-AUTH-014", "Benutzer nicht gefunden."))
            role = str(body.get("role", target["role"]))
            department_id = str(body.get("departmentId", target["department_id"]) or "").strip()
            active = 1 if body.get("active", bool(target["active"])) else 0
            if uid == user["id"]:
                return self.json_response(400, mp_error("MP-AUTH-016", "Das eigene Konto wird hier nicht geändert. Eigenes Passwort über „Passwort ändern“ setzen."))
            if user["role"] != "admin":
                own = str(user.get("department_id") or "")
                manageable = MANAGEABLE_ROLES.get(user["role"], set())
                if str(target["department_id"] or "") != own or department_id != own or target["role"] not in manageable or role not in manageable:
                    return self.json_response(403, mp_error("MP-AUTH-021", "Benutzerverwaltung ist auf untergeordnete Rollen im eigenen Bereich begrenzt."))
            if role not in ROLES or (role in DEPARTMENT_ROLES and not department_id):
                return self.json_response(400, mp_error("MP-AUTH-015", "Ungültige Rolle oder Bereich fehlt."))
            fields = ["role=?", "department_id=?", "active=?", "updated_at=?"]
            vals = [role, department_id, active, now_iso()]
            if "password" in body:
                pw = str(body["password"])
                if len(pw) < 8:
                    return self.json_response(400, mp_error("MP-AUTH-017", "Passwort muss mindestens 8 Zeichen haben."))
                salt, digest = hash_password(pw)
                fields += ["salt=?", "password_hash=?"]
                vals += [salt, digest]
            vals.append(uid)
            con.execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", vals)
            if not active or "password" in body:
                con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,NULL)", (now_iso(), user["username"], "Benutzer geändert", target["username"]))
        return self.json_response(200, {"ok": True})

    def do_PUT(self):
        path = urlparse(self.path).path
        if path != "/api/state":
            return self.json_response(404, mp_error("MP-REQ-404", "Nicht gefunden."))
        user = self.require_user(WRITE_ROLES)
        if not user:
            return
        if not self.require_current_client():
            return
        try:
            body = self.read_json()
            expected = int(body.get("revision"))
            incoming = body.get("data")
            if not isinstance(incoming, dict):
                raise ValueError("Datenobjekt fehlt")
            if len(canonical(incoming).encode("utf-8")) > MAX_BODY:
                raise ValueError("Datenstand zu groß")
        except Exception as e:
            return self.json_response(400, mp_error("MP-DATA-013", str(e)))
        action = str(body.get("action", "Gespeichert"))[:160]
        detail = str(body.get("detail", ""))[:500]
        with DB_LOCK, db_session() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT revision,json FROM state WHERE id=1").fetchone()
            if expected != row["revision"]:
                con.execute("ROLLBACK")
                return self.json_response(409, mp_error("MP-SYNC-001", "Revision veraltet.", revision=row["revision"]))
            old = json.loads(row["json"])
            # Legacy-Schattenkopie (V12.4.3 und älter) nie wieder in den Live-State übernehmen.
            incoming.pop("orders", None)
            valid, error_code, reason = validate_state(old, incoming)
            if not valid:
                con.execute("ROLLBACK")
                return self.json_response(400, mp_error(error_code, reason))
            if user["role"] == "gf":
                ok, reason = gf_change_allowed(old, incoming)
                if not ok:
                    con.execute("ROLLBACK")
                    return self.json_response(403, mp_error("MP-GF-030", reason))
            elif user["role"] == "project_management":
                ok, reason = project_management_change_allowed(old, incoming)
                if not ok:
                    con.execute("ROLLBACK")
                    return self.json_response(403, mp_error("MP-PM-030", reason))
            elif user["role"] == "sales":
                ok, reason = sales_change_allowed(old, incoming)
                if not ok:
                    con.execute("ROLLBACK")
                    return self.json_response(403, mp_error("MP-SALES-030", reason))
            elif user["role"] == "production_planning":
                ok, reason = production_planning_change_allowed(old, incoming)
                if not ok:
                    con.execute("ROLLBACK")
                    return self.json_response(403, mp_error("MP-AV-030", reason))
            elif user["role"] in DEPARTMENT_ROLES:
                ok, reason = department_change_allowed(old, incoming, str(user.get("department_id") or ""))
                if not ok:
                    con.execute("ROLLBACK")
                    return self.json_response(403, mp_error("MP-DEPT-030", reason))
            if user["role"] != "admin" and canonical(old.get("processTemplates")) != canonical(incoming.get("processTemplates")):
                ok, reason = templates_change_allowed(old, incoming, user["role"])
                if not ok:
                    con.execute("ROLLBACK")
                    return self.json_response(403, mp_error("MP-TPL-030", reason))
            if user["role"] != "admin" and canonical(old.get("projects")) != canonical(incoming.get("projects")):
                ok, reason = project_changes_allowed(old, incoming, user["role"], user["username"], str(user.get("department_id") or ""))
                if not ok:
                    con.execute("ROLLBACK")
                    return self.json_response(403, mp_error("MP-PM-031", reason))
            if user["role"] != "admin":
                ok, reason = audit_change_allowed(old, incoming, user["username"])
                if not ok:
                    con.execute("ROLLBACK")
                    return self.json_response(403, mp_error("MP-LOG-001", reason))
            # MP-AUD-017: fachlich unveränderter Stand erzeugt keine neue Revision.
            if canonical({k: v for k, v in incoming.items() if k != "meta"}) == canonical({k: v for k, v in old.items() if k != "meta"}):
                con.execute("ROLLBACK")
                return self.json_response(200, {"ok": True, "revision": row["revision"], "data": old, "unchanged": True})
            new_revision = row["revision"] + 1
            incoming.setdefault("meta", {})
            incoming["meta"]["serverRevision"] = new_revision
            incoming["meta"]["actor"] = user["username"]
            incoming["meta"]["storage"] = "server"
            raw = json.dumps(incoming, ensure_ascii=False, separators=(",", ":"))
            ts = now_iso()
            con.execute("UPDATE state SET revision=?,json=?,updated_at=?,updated_by=? WHERE id=1", (new_revision, raw, ts, user["username"]))
            con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,?)", (ts, user["username"], action, detail, new_revision))
            con.execute("COMMIT")
        with REVISION_CONDITION:
            REVISION_CONDITION.notify_all()
        return self.json_response(200, {"ok": True, "revision": new_revision, "data": incoming})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--init-admin", metavar="USERNAME")
    ap.add_argument("--allowed-subnet", help="Pflicht im Serverbetrieb, z. B. 192.168.178.0/24")
    args = ap.parse_args()
    global ALLOWED_NETWORK
    if not args.init_admin:
        if not args.allowed_subnet:
            print("FEHLER: --allowed-subnet ist im Serverbetrieb Pflicht.", file=sys.stderr)
            raise SystemExit(2)
        try:
            ALLOWED_NETWORK = ipaddress.ip_network(args.allowed_subnet, strict=False)
            bind_ip = ipaddress.ip_address(args.host)
        except ValueError as e:
            print(f"FEHLER: Ungültige LAN-Adresse/Subnetz: {e}", file=sys.stderr)
            raise SystemExit(2)
        if bind_ip.is_unspecified or bind_ip.is_loopback or bind_ip not in ALLOWED_NETWORK:
            print("FEHLER: Server muss an eine konkrete LAN-IP innerhalb --allowed-subnet gebunden werden.", file=sys.stderr)
            raise SystemExit(2)
    init_db()
    if args.init_admin:
        password = os.environ.get("MP_ADMIN_PASSWORD")
        if not password:
            import getpass
            password = getpass.getpass("Admin-Passwort: ")
        create_or_reset_admin(args.init_admin, password)
        return
    with DB_LOCK, db_session() as con:
        admins = con.execute("SELECT COUNT(*) c FROM users WHERE role='admin' AND active=1").fetchone()["c"]
    if not admins:
        print("FEHLER: Kein aktiver Admin. Zuerst: server.py --init-admin <name>", file=sys.stderr)
        raise SystemExit(2)
    if not INDEX_PATH.exists():
        print(f"FEHLER: {INDEX_PATH} fehlt.", file=sys.stderr)
        raise SystemExit(2)
    httpd = MPHTTPServer((args.host, args.port), Handler)
    print(f"Maschinenplanung V{APP_VERSION} LAN-only läuft auf http://{args.host}:{args.port}")
    print(f"Erlaubtes LAN-Subnetz: {ALLOWED_NETWORK}")
    print(f"Datenbank: {DB_PATH}")
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()

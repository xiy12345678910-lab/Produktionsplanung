#!/usr/bin/env python3
"""Rollen & Rechte: Systemrollen, Rollenprofile (Funktions- und Aktionsrechte), Rechteprüfung beim Speichern.

#73 Phase 1: aus server.py herausgelöst (flaches Modul neben server.py, Teil von $MP_AppFiles). Nur Standardbibliothek.
server.py importiert alle Namen von hier und bietet sie weiter an (server.ROLES, server.attach_rights ...).
Hier liegen nur Konstanten und reine Prüffunktionen; Funktionen mit Datenbankzugriff bekommen die Verbindung als Parameter.
Keine veränderlichen Modulwerte (Tests ersetzen hier nichts).
"""
from __future__ import annotations

import json
import re
import sqlite3


# Allgemeine Vergleichshelfer (auch von server.py genutzt und von dort weiter angeboten).
def canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_map(items):
    return {str(x.get("id")): x for x in (items or []) if isinstance(x, dict) and x.get("id")}


# ---------------------------------------------------------------------------------------------
# Systemrollen
# ---------------------------------------------------------------------------------------------
ROLES = {"admin", "gf", "department_lead", "department_deputy", "viewer", "project_management", "production_planning", "sales", "production"}
WRITE_ROLES = {"admin", "gf", "department_lead", "department_deputy", "project_management", "production_planning", "sales"}
DEPARTMENT_ROLES = {"department_lead", "department_deputy"}
SCOPED_ROLES = DEPARTMENT_ROLES | {"viewer", "production"}
USER_MANAGER_ROLES = {"admin", "department_lead", "department_deputy"}
# Welche Rollen eine Bereichsrolle im eigenen Bereich anlegen/ändern darf.
# Leitungen verwalten Stellvertretungen und Lesende; Stellvertretungen nur Lesende.
# Leitungskonten selbst verwaltet ausschließlich der Admin.
MANAGEABLE_ROLES = {
    "department_lead": {"department_deputy", "viewer"},
    "department_deputy": {"viewer"},
}


# ---------------------------------------------------------------------------------------------
# V12.23.0 (#63/#55): Rollen & Rechte. Eigene Rollen (Rollenprofile) beruhen auf einer Systemrolle und
# schränken sie je Funktion (Kein Zugriff / Lesen / Bearbeiten) und je Aktion ein – nie darüber hinaus.
# Durchsetzung am Server: Datenstand (ausblenden), Speichern (Leserecht = unveränderlich) und Endpunkte.
# ---------------------------------------------------------------------------------------------
ROLE_FUNCTIONS = (("planning", "Planung & Produktion"), ("projects", "Projekte"), ("frameOrders", "Rahmenaufträge"),
                  ("personnel", "Personal"), ("formats", "Formate"), ("gf", "GF-Steuerung"), ("report", "Report"),
                  ("history", "Historie"), ("chat", "Chat"), ("notifications", "Benachrichtigungen"), ("system", "System"))
# V12.27.0 (#55): „Produktion melden" ist in Starten/Pausieren und Fertigmelden geteilt; FA-Rechte sind neu.
# Der alte Schlüssel „production" wird nur noch gelesen (false => beide neuen Rechte false).
ROLE_ACTIONS = (("faCreate", "FA anlegen"), ("faPlan", "FA einplanen"), ("prodStartPause", "Produktion starten/pausieren"),
                ("prodFinish", "Produktion fertigmelden"), ("confectionHours", "Konfektionsstunden vorgeben"),
                ("userAdmin", "Benutzer verwalten"), ("updates", "Updates installieren"))
LEGACY_PRODUCTION_ACTION = "production"
FA_PLAN_FIELDS = ("machineId", "altMachineId", "allowAlternative", "laneIndex", "pos", "direction", "anchorMode",
                  "requiredStart", "requiredFinish", "dryingHours", "taktId")


def effective_actions(actions) -> dict:
    """Aktionsrechte je Schlüssel (Standard ja); alter Schlüssel „production"=false sperrt beide neuen Produktionsrechte."""
    actions = actions if isinstance(actions, dict) else {}
    out = {k: bool(actions.get(k, True)) for k, _ in ROLE_ACTIONS}
    if actions.get(LEGACY_PRODUCTION_ACTION) is False:
        for k in ("prodStartPause", "prodFinish"):
            if k not in actions:
                out[k] = False
    return out
ROLE_LEVELS = ("none", "read", "edit")
# Bei „Lesen" unveränderlich, bei „Kein Zugriff" zusätzlich ausgeblendet (FUNCTION_HIDDEN).
FUNCTION_DATA = {
    "planning": ("workSteps", "machineBlocks", "planVersions"),
    "projects": ("projects", "processTemplates"),
    "personnel": ("employees", "personnelAssignments", "personnelAbsences", "weeklyEmployeeDeployments", "departmentStaffNeeds"),
    "formats": ("formats", "baseFormats"),
    "history": ("history",),
    "system": ("machines", "departments", "shiftTemplates", "yearRules", "weekRules", "exceptions", "operatorCapacity", "personnelGate", "palletTemplates"),
}
# Planung und System bleiben sichtbar: Scheduler und Projekt-Liveansicht brauchen Ressourcen und FA.
# V12.27.0 (Rechte-Audit): Schreibbare Schlüssel der GF-Rolle (siehe gf_change_allowed); gilt nur für Benutzer der Rolle gf.
GF_FUNCTION_KEYS = ("departmentStaffNeeds", "weeklyEmployeeDeployments", "exceptions", "employees", "departments", "machines")
FUNCTION_HIDDEN = {"projects": ("projects", "processTemplates"), "personnel": FUNCTION_DATA["personnel"],
                   "formats": FUNCTION_DATA["formats"], "frameOrders": ("frameOrders", "callOffs"), "history": ("history",)}


def migrate_role_profiles(con: sqlite3.Connection) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS role_profiles(id TEXT PRIMARY KEY, json TEXT NOT NULL, updated_at TEXT NOT NULL)")
    if "profile_id" not in {row["name"] for row in con.execute("PRAGMA table_info(users)")}:
        con.execute("ALTER TABLE users ADD COLUMN profile_id TEXT NOT NULL DEFAULT ''")


def resolve_profile_id(con, actor: dict, role: str, requested, current: str = "") -> str:
    """Rollenprofil für einen Benutzer prüfen: nur Admin weist zu; Profil aktiv und zur Systemrolle passend."""
    if requested is None:
        found = con.execute("SELECT json FROM role_profiles WHERE id=?", (current,)).fetchone() if current else None
        return current if found and json.loads(found["json"]).get("baseRole") == role else ""
    pid = str(requested or "")
    if pid and actor["role"] != "admin":
        raise PermissionError("Rollenprofile weist nur der Admin zu.")
    if not pid:
        return ""
    found = con.execute("SELECT json FROM role_profiles WHERE id=?", (pid,)).fetchone()
    profile = json.loads(found["json"]) if found else None
    if not profile or not profile.get("active", True) or profile.get("baseRole") != role:
        raise ValueError("Rollenprofil fehlt, ist deaktiviert oder passt nicht zur Systemrolle.")
    return pid


def role_risk_warnings(profile) -> list:
    """V12.27.0 (#55): Hinweise zu riskanten Rechte-Kombinationen eines Rollenprofils. Rein beratend – blockiert nie das Speichern
    und ändert keine Rechteprüfung. Regeln nur dort, wo das Datenmodell sie hergibt (Aktionen sind standardmäßig erlaubt)."""
    profile = profile if isinstance(profile, dict) else {}
    actions = effective_actions(profile.get("actions"))
    rights = profile.get("rights") if isinstance(profile.get("rights"), dict) else {}
    base = profile.get("baseRole")
    planning_edit = rights.get("planning", "edit") == "edit"
    manager = base in USER_MANAGER_ROLES  # nur hier wirkt „Benutzer verwalten“ überhaupt
    out = []
    if manager and actions["userAdmin"] and planning_edit and (actions["faCreate"] or actions["faPlan"] or actions["prodFinish"]):
        out.append({"code": "MP-ROLE-011", "text": "Benutzer verwalten zusammen mit operativen Rechten (FA anlegen/einplanen, Produktion fertigmelden): "
                    "Vier-Augen-Prinzip fehlt, die Rolle könnte sich selbst Rechte geben und damit arbeiten."})
    if manager and planning_edit and actions["faCreate"] and actions["prodFinish"]:  # beide Rechte hat nur Bereichsleiter/Vertretung
        out.append({"code": "MP-ROLE-012", "text": "FA anlegen und Produktion fertigmelden in einer Rolle: Aufträge können ohne zweite Kontrolle angelegt und selbst fertiggemeldet werden."})
    if manager and actions["userAdmin"] and rights.get("system", "edit") == "edit":
        out.append({"code": "MP-ROLE-013", "text": "Benutzer verwalten zusammen mit Bearbeitungsrecht für System: Konten und Systemeinstellungen (Maschinen, Bereiche, Schichten) lassen sich ohne Gegenkontrolle ändern."})
    # Unveränderte Kopie (alle Rechte „Bearbeiten“, alle Aktionen erlaubt): nur ein Hinweis statt aller Einzelwarnungen.
    if len(out) > 1 and all(rights.get(k, "edit") == "edit" for k, _ in ROLE_FUNCTIONS) and all(actions.values()):
        label = {"department_lead": "Abteilungsleiter", "department_deputy": "Stellv. Abteilungsleiter"}.get(base, base)
        out = [{"code": "MP-ROLE-014", "text": f"Unveränderte Kopie der Rolle {label}: nur der Name ist anders, die Rechte sind dieselben "
                "(u. a. Benutzer verwalten, FA anlegen und fertigmelden, System bearbeiten)."}]
    return out


def role_profiles(con) -> dict:
    out = {r["id"]: json.loads(r["json"]) for r in con.execute("SELECT id,json FROM role_profiles ORDER BY id")}
    for p in out.values():
        p["actions"] = effective_actions(p.get("actions"))
    return out


def attach_rights(user: dict, profile: dict | None) -> dict:
    """Effektive Rechte am Benutzer: Profil schränkt die Systemrolle ein; ohne Profil volle Systemrolle."""
    user["profileId"] = profile["id"] if profile else ""
    user["profileName"] = profile["name"] if profile else ""
    user["rights"] = {k: (profile or {}).get("rights", {}).get(k, "edit") for k, _ in ROLE_FUNCTIONS}
    user["actions"] = effective_actions((profile or {}).get("actions"))
    return user


def user_level(user: dict, function: str) -> str:
    return (user.get("rights") or {}).get(function, "edit")


def av_hours_department(state_or_deps, did) -> bool:
    """V12.27.0: Bereich, für den die AV die Sollstunden vorgibt (Bereichs-Eigenschaft avHours, nicht der Name)."""
    deps = state_or_deps.get("departments") if isinstance(state_or_deps, dict) else state_or_deps
    return any(isinstance(d, dict) and str(d.get("id")) == str(did) and d.get("avHours") is True for d in (deps or []))


def user_action(user: dict, action: str) -> bool:
    return bool((user.get("actions") or {}).get(action, True))


def user_public(user: dict) -> dict:
    return {"id": user["id"], "username": user["username"], "role": user["role"], "departmentId": user.get("department_id", ""),
            "profileId": user.get("profileId", ""), "profileName": user.get("profileName", ""),
            "rights": user.get("rights") or {}, "actions": user.get("actions") or {}}


def validate_role_profile(body: dict, rid: str) -> dict:
    if not re.fullmatch(r"[a-z0-9_-]{2,40}", rid):
        raise ValueError("Rollen-ID: 2–40 Zeichen a–z, 0–9, _ oder -.")
    name = str(body.get("name") or "").strip()
    if not 1 <= len(name) <= 60:
        raise ValueError("Rollenname fehlt oder ist zu lang (max. 60).")
    base = body.get("baseRole")
    if base not in ROLES - {"admin"}:
        raise ValueError("Basisrolle ungültig (Admin ist immer vollständig berechtigt).")
    rights = body.get("rights") or {}
    actions = body.get("actions") or {}
    if not isinstance(rights, dict) or any(k not in dict(ROLE_FUNCTIONS) or v not in ROLE_LEVELS for k, v in rights.items()):
        raise ValueError("Rechte je Funktion: nur none, read oder edit.")
    if not isinstance(actions, dict) or any((k not in dict(ROLE_ACTIONS) and k != LEGACY_PRODUCTION_ACTION) or not isinstance(v, bool) for k, v in actions.items()):
        raise ValueError("Aktionsrechte: nur ja/nein.")
    return {"id": rid, "name": name, "baseRole": base, "description": str(body.get("description") or "").strip()[:300],
            "active": body.get("active", True) is not False,
            "rights": {k: rights.get(k, "edit") for k, _ in ROLE_FUNCTIONS}, "actions": effective_actions(actions)}


def restrict_state_for_rights(out: dict, user: dict) -> None:
    for function, keys in FUNCTION_HIDDEN.items():
        if user_level(user, function) == "none":
            for key in keys:
                out[key] = []


def rights_change_error(old: dict, incoming: dict, user: dict) -> str:
    """Leserechte und gesperrte Aktionen beim Speichern; leerer Text = erlaubt."""
    for function, keys in FUNCTION_DATA.items():
        if user_level(user, function) != "edit":
            label = dict(ROLE_FUNCTIONS)[function]
            for key in keys:
                if canonical(old.get(key)) != canonical(incoming.get(key)):
                    return f"Für „{label}“ besteht nur Leserecht."
    if user.get("role") == "gf" and user_level(user, "gf") != "edit":
        for key in GF_FUNCTION_KEYS:
            if canonical(old.get(key)) != canonical(incoming.get(key)):
                return "Für „GF-Steuerung“ besteht nur Leserecht."
    before_ws = _record_map(old.get("workSteps"))
    after_ws = _record_map(incoming.get("workSteps"))
    if not user_action(user, "faCreate") and any(rid not in before_ws for rid in after_ws):
        return "FA anlegen ist für diese Rolle gesperrt."
    if not user_action(user, "faPlan"):
        for rid, after in after_ws.items():
            prev = before_ws.get(rid)
            if prev is None:
                continue
            if any(canonical(prev.get(f)) != canonical(after.get(f)) for f in FA_PLAN_FIELDS) or \
                    (canonical(prev.get("handoffUnassigned")) != canonical(after.get("handoffUnassigned")) and prev.get("handoffUnassigned") and not after.get("handoffUnassigned")):
                return "FA einplanen ist für diese Rolle gesperrt."
    if not user_action(user, "confectionHours"):
        before = _record_map(old.get("workSteps"))
        for rid, after in _record_map(incoming.get("workSteps")).items():
            prev = before.get(rid) or {}
            dep = str(after.get("departmentId") or "")
            confection = av_hours_department(old, dep) or after.get("planningType") == "LABOR_HOURS"
            if confection and (canonical(prev.get("hours", 0)) != canonical(after.get("hours", 0)) or canonical(prev.get("requiredHours")) != canonical(after.get("requiredHours"))):
                return "Konfektionsstunden vorgeben ist für diese Rolle gesperrt."
    return ""

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
import os
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MP_DEBUG_ABORTS = os.environ.get('MP_DEBUG_ABORTS') == '1'
APP_VERSION = "12.27.0"
HOST = os.environ.get("MP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MP_PORT", "8765"))
BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    # Anhängen statt voranstellen: Standardbibliothek hat immer Vorrang vor Dateien im Programmordner.
    sys.path.append(str(BASE))
from release_gates import machine_lanes, validate_release_feasibility, local_dt, work_intervals, personnel_assignment, can_staff, is_absent, LOCAL_TZ, make_segments, hits_machine_block, overlaps, clock_minutes
from app_updates import UpdateManager

DATA_DIR = BASE / "data"
DB_PATH = DATA_DIR / "maschinenplanung.sqlite3"
INDEX_PATH = BASE / "index.html"


# ---------------------------------------------------------------------------------------------
# V12.14.0: Firmenkonfiguration config/firma.json (+ Logo) AUSSERHALB des Programmpakets.
# Updates (UPDATE_LIVE.ps1) kopieren nur $MP_AppFiles und fassen config\ nie an.
# Nur Standardbibliothek. Ungültige Datei = Start verweigert (MP-CFG-001/002), nichts wird überschrieben.
# ---------------------------------------------------------------------------------------------
CONFIG_DIR = Path(os.environ.get("MP_CONFIG_DIR") or (BASE / "config"))
CONFIG_PATH = CONFIG_DIR / "firma.json"
CONFIG_SCHEMA = 1
CONFIG_KEEP_BAK = 20
CONFIG_LOGO_MAX = 420 * 1024
CONFIG_LOCK = threading.RLock()
CONFIG_MODULES = ("projects", "formats", "personnel", "chat", "notifications", "postcalc", "kpi", "palletLabels", "frameOrders", "gf", "history")
CONFIG_MODULE_DEFAULTS = {k: True for k in CONFIG_MODULES} | {"palletLabels": False}
CONFIG_TEMPLATES = {"werbetechnik", "neutral", "metall_cnc", "leer", "demo"}
CONFIG_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
CONFIG_TENANT = re.compile(r"^[a-z0-9-]{3,32}$")
CONFIG_PROJECT_AREAS = [
    {"id": "sales", "name": "Vertrieb"}, {"id": "pm", "name": "Projektmanagement"},
    {"id": "engineering", "name": "Konstruktion / Entwicklung"}, {"id": "calculation", "name": "Kalkulation"},
    {"id": "purchasing", "name": "Einkauf"}, {"id": "quality", "name": "Qualitätssicherung"},
    {"id": "av", "name": "Arbeitsvorbereitung"},
]


class ConfigError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def config_defaults() -> dict:
    """Neutrale Grundwerte (neue Installation, fehlende Felder)."""
    return {
        "schemaVersion": CONFIG_SCHEMA,
        "tenantId": "firma",
        "company": {"name": "", "productName": "Produktionsplanung", "logoFile": "", "color": "#1f5eff",
                    "uiAccent": "#1f5eff", "font": "Arial", "address": "", "footer": ""},
        "locale": {"language": "de", "timezone": "Europe/Berlin", "holidayRegion": ""},
        "terms": {"projectNumber": "Projekt", "orderNumber": "Auftrag", "roleLabels": {}},
        "template": "neutral",
        "modules": dict(CONFIG_MODULE_DEFAULTS),
        "projectAreas": [dict(a) for a in CONFIG_PROJECT_AREAS],
        "license": {"file": "lizenz.key"},
        "update": {"channel": "stable", "source": ""},
        "setupDone": True,
    }


# V12.15.0: Die bisher fest eingebauten Werte des Bestandskunden stehen NICHT mehr im Programmpaket.
# Für die Erstmigration (Bestand ohne firma.json) werden sie, falls vorhanden, aus tools/legacy_employer_seed.json
# gelesen (Entwickler-Repo, nicht in $MP_AppFiles). Ohne Datei bleibt es bei data.ci + neutralen Werten.
LEGACY_SEED_PATH = Path(os.environ.get("MP_LEGACY_SEED") or (BASE / "tools" / "legacy_employer_seed.json"))


def legacy_seed() -> dict:
    try:
        d = json.loads(LEGACY_SEED_PATH.read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _merge_missing(cur, default):
    """Ergänzt nur fehlende Schlüssel; vorhandene Werte und unbekannte Felder bleiben."""
    if not isinstance(cur, dict) or not isinstance(default, dict):
        return cur
    out = dict(cur)
    for k, v in default.items():
        out[k] = _merge_missing(out[k], v) if k in out else json.loads(json.dumps(v))
    return out


def validate_config(cfg) -> list[str]:
    """Liefert Fehler als 'feld: grund'. Unbekannte Felder werden toleriert."""
    errs: list[str] = []
    if not isinstance(cfg, dict):
        return ["(Datei): Objekt erwartet"]

    def sec(name):
        v = cfg.get(name)
        if v is None:
            return {}
        if not isinstance(v, dict):
            errs.append(f"{name}: Objekt erwartet")
            return {}
        return v

    def text(sect, d, key, mx):
        v = d.get(key)
        if v is not None and (not isinstance(v, str) or len(v) > mx):
            errs.append(f"{sect}.{key}: Text bis {mx} Zeichen erwartet")

    sv = cfg.get("schemaVersion")
    if sv is not None and (not isinstance(sv, int) or isinstance(sv, bool) or sv < 1):
        errs.append("schemaVersion: ganze Zahl >= 1 erwartet")
    rv = cfg.get("revision")
    if rv is not None and (not isinstance(rv, int) or isinstance(rv, bool) or rv < 0):
        errs.append("revision: ganze Zahl >= 0 erwartet")
    t = cfg.get("tenantId")
    if t is not None and not (isinstance(t, str) and CONFIG_TENANT.match(t)):
        errs.append("tenantId: [a-z0-9-], 3-32 Zeichen")
    tp = cfg.get("template")
    if tp is not None and tp not in CONFIG_TEMPLATES:
        errs.append("template: unbekannte Vorlage")
    c = sec("company")
    for k in ("name", "productName", "font", "address", "footer"):
        text("company", c, k, 600 if k in ("address", "footer") else 120)
    for k in ("color", "uiAccent"):
        if c.get(k) is not None and not (isinstance(c[k], str) and CONFIG_HEX.match(c[k])):
            errs.append(f"company.{k}: Hex-Farbe #RRGGBB erwartet")
    lf = c.get("logoFile")
    if lf not in (None, ""):
        if not (isinstance(lf, str) and re.match(r"^[A-Za-z0-9_.-]+\.(png|jpe?g|svg)$", lf)):
            errs.append("company.logoFile: Dateiname .png/.jpg/.svg im config-Ordner erwartet")
        else:
            p = CONFIG_DIR / lf
            if not p.is_file():
                errs.append(f"company.logoFile: Datei {lf} fehlt")
            elif p.stat().st_size > CONFIG_LOGO_MAX:
                errs.append(f"company.logoFile: Datei größer als {CONFIG_LOGO_MAX // 1024} KB")
            elif lf.lower().endswith(".svg"):
                try:
                    svg_sanitize(p.read_bytes())
                except ValueError as e:
                    errs.append(f"company.logoFile: SVG nicht zulässig ({e})")
    lo = sec("locale")
    if lo.get("language") is not None and lo["language"] not in ("de", "en"):
        errs.append("locale.language: de oder en")
    text("locale", lo, "timezone", 64)
    text("locale", lo, "holidayRegion", 16)
    te = sec("terms")
    text("terms", te, "projectNumber", 30)
    text("terms", te, "orderNumber", 30)
    if te.get("roleLabels") is not None and not isinstance(te["roleLabels"], dict):
        errs.append("terms.roleLabels: Objekt erwartet")
    elif isinstance(te.get("roleLabels"), dict) and any(
            k not in ROLES or not isinstance(v, str) or len(v) > 40 for k, v in te["roleLabels"].items()):
        errs.append("terms.roleLabels: Rollen-ID und Text bis 40 Zeichen erwartet")
    m = sec("modules")
    for k, v in m.items():
        if k in CONFIG_MODULES and not isinstance(v, bool):
            errs.append(f"modules.{k}: true/false erwartet")
    pa = cfg.get("projectAreas")
    if pa is not None:
        ok = isinstance(pa, list) and pa and all(
            isinstance(a, dict) and isinstance(a.get("id"), str) and re.match(r"^[a-z0-9_-]{1,30}$", a["id"])
            and isinstance(a.get("name"), str) and 0 < len(a["name"]) <= 60 for a in pa)
        if not ok or len({a["id"] for a in pa}) != len(pa):
            errs.append("projectAreas: Liste aus {id, name} (eindeutige id) erwartet")
    if cfg.get("setupDone") is not None and not isinstance(cfg["setupDone"], bool):
        errs.append("setupDone: true/false erwartet")
    sec("license")
    u = sec("update")
    text("update", u, "channel", 20)
    text("update", u, "source", 300)
    return errs


def config_last_bak() -> str:
    baks = sorted(CONFIG_DIR.glob("firma.json.bak-*"), key=lambda p: p.name, reverse=True)
    for p in baks:
        try:
            if not validate_config(json.loads(p.read_text(encoding="utf-8-sig"))):
                return str(p)
        except (OSError, ValueError):
            continue
    return ""


def _config_fail(code: str, detail: str) -> ConfigError:
    bak = config_last_bak()
    msg = f"{code} {CONFIG_PATH}: {detail}" + (f" · letzte gültige Sicherung: {bak}" if bak else "")
    return ConfigError(code, msg)


def read_config_file() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise _config_fail("MP-CFG-001", f"nicht lesbar oder kein JSON ({e.__class__.__name__})")
    errs = validate_config(cfg)
    if errs:
        raise _config_fail("MP-CFG-002", "; ".join(errs[:5]))
    return cfg


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def save_config(cfg: dict) -> None:
    """Validiert, sichert die bisherige Datei als .bak (nur bei geänderter Datei, letzte 20) und schreibt atomar."""
    errs = validate_config(cfg)
    if errs:
        raise ConfigError("MP-CFG-002", f"{CONFIG_PATH}: " + "; ".join(errs[:5]))
    if int(cfg.get("schemaVersion", CONFIG_SCHEMA)) > CONFIG_SCHEMA:
        raise ConfigError("MP-CFG-003", "schemaVersion neuer als dieses Programm: nicht geschrieben")
    data = (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with CONFIG_LOCK:
        if CONFIG_PATH.exists():
            if CONFIG_PATH.read_bytes() == data:
                return
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            bak = CONFIG_DIR / f"firma.json.bak-{stamp}"
            n = 1
            while bak.exists():
                n += 1
                bak = CONFIG_DIR / f"firma.json.bak-{stamp}-{n}"
            shutil.copy2(CONFIG_PATH, bak)
            for old in sorted(CONFIG_DIR.glob("firma.json.bak-*"), key=lambda p: p.name, reverse=True)[CONFIG_KEEP_BAK:]:
                old.unlink(missing_ok=True)
        _atomic_write(CONFIG_PATH, data)


def _legacy_logo(ci: dict, seed: dict) -> tuple[str, bytes]:
    """Logo des Bestands: data.ci.logo, sonst das der Legacy-Seed-Datei. ('', b'') wenn keins/zu groß."""
    cands = []
    if isinstance(ci, dict) and isinstance(ci.get("logo"), str):
        cands.append(ci["logo"])
    if isinstance(seed.get("logoDataUrl"), str):
        cands.append(seed["logoDataUrl"])
    for s in cands:
        m = re.match(r"^data:image/(png|jpeg);base64,(.+)$", s, re.S)
        if not m:
            continue
        try:
            raw = base64.b64decode(m.group(2), validate=True)
        except ValueError:
            continue
        if 0 < len(raw) <= CONFIG_LOGO_MAX:
            return ("logo.png" if m.group(1) == "png" else "logo.jpg"), raw
    return "", b""


def _existing_state() -> dict | None:
    """Liest (read-only) den Datenstand. None = keine Bestandsdatenbank (Revision <= 1 oder keine)."""
    if not DB_PATH.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT revision,json FROM state WHERE id=1").fetchone()
        finally:
            con.close()
        if not row or int(row[0]) <= 1:
            return None
        st = json.loads(row[1])
        return st if isinstance(st, dict) else {}
    except (sqlite3.Error, ValueError, TypeError):
        return None


def install_is_fresh() -> bool:
    """V12.17.0: Neuinstallation = keine Datenbank oder Revision <= 1 ohne jede Planungsdaten. Nur dann startet der
    Einrichtungsassistent (setupDone=false). Bestand (Revision > 1 oder vorhandene Daten) gilt immer als eingerichtet."""
    if not DB_PATH.exists():
        return True
    try:
        con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT revision,json FROM state WHERE id=1").fetchone()
        finally:
            con.close()
        if not row:
            return True
        if int(row[0]) > 1:
            return False
        st = json.loads(row[1])
        if not isinstance(st, dict):
            return False
        return not any(st.get(k) for k in ("departments", "machines", "workSteps", "projects", "employees", "history", "exceptions"))
    except (sqlite3.Error, ValueError, TypeError):
        return False


NEUTRAL_HINT = ("Vor dem Update 'Firma_Einrichten.ps1 -Vorlage <datei>' ausfuehren (Vorlage vom Entwickler) "
                "oder bewusst neutral starten mit 'Firma_Einrichten.ps1 -Neutral'.")


def _legacy_bestand_without_identity() -> bool:
    """True: Bestand (Revision > 1) ohne firma.json, ohne Firmenname/Logo in data.ci und ohne Vorlage -> Start wäre still neutral."""
    if CONFIG_PATH.exists():
        return False
    st = _existing_state()
    if st is None:
        return False
    ci = st.get("ci") if isinstance(st.get("ci"), dict) else {}
    seed = legacy_seed()
    sco = seed.get("company") if isinstance(seed.get("company"), dict) else {}
    has_name = (isinstance(ci.get("company"), str) and bool(ci["company"].strip())) or bool(sco.get("name"))
    has_logo = bool(_legacy_logo(ci, seed)[0])
    return not (has_name or has_logo)


def check_firma_config_start() -> None:
    """V12.15.1: Ein Bestand darf nie still neutral starten (Name/Logo weg). Harter Stopp mit MP-CFG-006."""
    if _legacy_bestand_without_identity():
        raise ConfigError("MP-CFG-006", f"MP-CFG-006 Bestandsdatenbank ohne config\\firma.json und ohne Firmenname/Logo in den Daten. "
                                        f"Start gestoppt, damit Name und Logo nicht still verloren gehen. {NEUTRAL_HINT}")


def migrate_firma_config(seed: dict | None = None, neutral_ok: bool = False) -> dict:
    """Fehlt die Datei: aus data.ci (+ optionaler Legacy-Seed-Datei) bzw. neutral anlegen. Idempotent.
    Bestand ohne Firmenname/Logo: MP-CFG-006 (außer neutral_ok). Neuinstallation (Revision <= 1) bleibt neutral."""
    cfg = config_defaults()
    st = _existing_state()
    by_template = seed is not None
    if by_template and st is None:
        st = {}          # Einrichtung per Vorlage (Firma_Einrichten): wie ein Bestand behandeln
    if st is not None:
        ci = st.get("ci") if isinstance(st.get("ci"), dict) else {}
        seed = legacy_seed() if seed is None else seed
        if seed:
            cfg = _merge_missing({k: v for k, v in seed.items() if k not in ("logoDataUrl", "_hinweis")}, cfg)
        co = cfg["company"]
        for k in ("address", "footer", "font"):
            if isinstance(ci.get(k), str) and ci[k]:
                co[k] = ci[k][:600 if k != "font" else 60]
        if isinstance(ci.get("company"), str) and ci["company"].strip():
            co["name"] = ci["company"].strip()[:120]
        if isinstance(ci.get("color"), str) and CONFIG_HEX.match(ci["color"]):
            co["color"] = ci["color"]
        name, raw = _legacy_logo(ci, seed)
        if not name and isinstance(co.get("logoFile"), str) and co["logoFile"]:
            co["logoFile"] = ""    # Verweis der Vorlage ohne Datei nicht übernehmen
        if not co.get("name") and not name and not neutral_ok:
            raise ConfigError("MP-CFG-006", f"MP-CFG-006 Bestandsdatenbank ohne Firmenname/Logo. {NEUTRAL_HINT}")
        if name:
            _atomic_write(CONFIG_DIR / name, raw)
            co["logoFile"] = name
        acc = (st.get("ui") or {}).get("accent") if isinstance(st.get("ui"), dict) else None
        if isinstance(acc, str) and CONFIG_HEX.match(acc):
            co["uiAccent"] = acc
    # Per Vorlage ohne Datenbank: Akzent aus data.ui.accent beim ersten Serverstart nachziehen.
    cfg["uiAccentSynced"] = not (by_template and not _existing_state())
    cfg["ciSynced"] = True
    # V12.17.0: nur eine echte Neuinstallation ohne Vorlage/Daten bekommt den Einrichtungsassistenten.
    if st is None and not by_template and install_is_fresh():
        cfg["setupDone"] = False
    save_config(cfg)
    return cfg


def setup_firma_from_template(arg: str) -> int:
    """V12.15.1: 'server.py --firma-einrichten <vorlage.json|neutral>' schreibt config\\firma.json (nur wenn sie fehlt)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        print(f"{CONFIG_PATH} existiert bereits - nichts geaendert.", file=sys.stderr)
        return 4
    if arg.lower() == "neutral":
        migrate_firma_config(seed={}, neutral_ok=True)
        print(f"Neutrale Firmenkonfiguration angelegt: {CONFIG_PATH}")
        return 0
    try:
        seed = json.loads(Path(arg).read_text(encoding="utf-8-sig"))
        if not isinstance(seed, dict):
            raise ValueError("kein JSON-Objekt")
    except (OSError, ValueError) as e:
        print(f"FEHLER: Vorlage nicht lesbar: {e}", file=sys.stderr)
        return 2
    try:
        cfg = migrate_firma_config(seed=seed)
    except ConfigError as e:
        print(f"FEHLER: {e.message}", file=sys.stderr)
        return 3
    print(f"Firmenkonfiguration angelegt: {CONFIG_PATH} ({cfg['company'].get('name') or 'ohne Name'})")
    return 0


def _fill_company_from_ci(cfg: dict) -> None:
    """Nur wenn die Config noch keinen Firmennamen und kein Logo hat: Name/Logo/Adresse/Fuß/Schrift/Farbe aus data.ci
    übernehmen (ergänzt leere Felder, ändert nie vorhandene). Idempotent über das Kennzeichen ciSynced."""
    co = cfg.setdefault("company", {})
    if co.get("name") or co.get("logoFile"):
        return
    st = _existing_state()
    ci = st.get("ci") if isinstance(st, dict) and isinstance(st.get("ci"), dict) else None
    if not ci:
        return
    dflt = config_defaults()["company"]
    name = ci["company"].strip()[:120] if isinstance(ci.get("company"), str) else ""
    lname, raw = _legacy_logo(ci, {})
    if not name and not lname:
        return
    if name:
        co["name"] = name
    for k, mx in (("address", 600), ("footer", 600), ("font", 60)):
        if isinstance(ci.get(k), str) and ci[k] and co.get(k, dflt[k]) in ("", dflt[k]):
            co[k] = ci[k][:mx]
    if isinstance(ci.get("color"), str) and CONFIG_HEX.match(ci["color"]) and co.get("color", dflt["color"]) == dflt["color"]:
        co["color"] = ci["color"]
    if lname:
        _atomic_write(CONFIG_DIR / lname, raw)
        co["logoFile"] = lname


CURRENT_CFG: dict | None = None


def load_config() -> tuple[dict, list[str]]:
    """Start-Prüfung. Liefert (Konfiguration, Warnungen). Wirft ConfigError (MP-CFG-001/002) bei ungültiger Datei."""
    global CURRENT_CFG
    warnings: list[str] = []
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CURRENT_CFG = migrate_firma_config()
        return CURRENT_CFG, warnings
    cfg = read_config_file()
    if int(cfg.get("schemaVersion", 1)) > CONFIG_SCHEMA:
        warnings.append(f"MP-CFG-003 {CONFIG_PATH}: schemaVersion {cfg['schemaVersion']} ist neuer als bekannt "
                        f"({CONFIG_SCHEMA}); Datei wird nur gelesen, nicht geändert.")
        CURRENT_CFG = cfg
        return cfg, warnings
    merged = _merge_missing(cfg, config_defaults())
    merged["schemaVersion"] = max(int(cfg.get("schemaVersion", 1)), CONFIG_SCHEMA)
    # V12.15.0: UI-Akzent wandert einmalig aus dem Datenstand (data.ui.accent) in die Config, damit die Anzeige gleich bleibt.
    if not merged.get("uiAccentSynced"):
        st = _existing_state()
        acc = ((st or {}).get("ui") or {}).get("accent") if st else None
        if isinstance(acc, str) and CONFIG_HEX.match(acc):
            merged["company"]["uiAccent"] = acc
        merged["uiAccentSynced"] = True
    # V12.15.1: Bestand aus V12.14.x (Config neutral, Firmen-CI nur in data.ci) behält Name/Logo: einmalig leere Felder füllen.
    if not merged.get("ciSynced"):
        _fill_company_from_ci(merged)
        merged["ciSynced"] = True
    if merged != cfg:
        save_config(merged)
    CURRENT_CFG = merged
    return merged, warnings


# ---------------------------------------------------------------------------------------------
# V12.15.0: Config-API (GET alle angemeldeten Rollen, Schreiben nur Admin), Logo-Ablage, SVG-Entschärfung.
# ---------------------------------------------------------------------------------------------
CONFIG_WRITE_SECTIONS = {
    "company": ("name", "productName", "color", "uiAccent", "font", "address", "footer", "logoFile"),
    "locale": ("language", "timezone", "holidayRegion"),
    "terms": ("projectNumber", "orderNumber", "roleLabels"),
    "modules": CONFIG_MODULES,
}
LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/svg+xml": "svg"}
LOGO_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml"}
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
SVG_TAGS = {
    "svg", "g", "defs", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
    "lineargradient", "radialgradient", "stop", "clippath", "mask", "symbol", "use", "title", "desc", "style",
}
SVG_BAD_VALUE = re.compile(r"javascript:|data:|vbscript:|@import|expression\s*\(|behaviou?r\s*:|-moz-binding|<|&#", re.I)
SVG_URL = re.compile(r"url\s*\(\s*['\"]?\s*([^)'\"\s]*)", re.I)


def _svg_value_ok(v: str) -> bool:
    if SVG_BAD_VALUE.search(v):
        return False
    return all(m.startswith("#") for m in SVG_URL.findall(v))


def svg_sanitize(raw: bytes) -> bytes:
    """Prüft und entschärft ein SVG streng nach Positivliste. Wirft ValueError(grund) bei allem Unklaren
    (Script, foreignObject, image, a, Animation, externe Verweise, Event-Handler, DOCTYPE/ENTITY ...)."""
    if len(raw) > CONFIG_LOGO_MAX:
        raise ValueError("zu groß")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("kein UTF-8")
    if re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
        raise ValueError("DOCTYPE/ENTITY nicht erlaubt")
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ValueError("kein gültiges XML")
    if root.tag != f"{{{SVG_NS}}}svg":
        raise ValueError("Wurzel ist nicht svg")
    count = 0

    def clean(el, depth):
        nonlocal count
        count += 1
        if count > 5000 or depth > 40:
            raise ValueError("zu komplex")
        for k in list(el.attrib):
            v = el.attrib[k]
            local = k.rsplit("}", 1)[-1].lower()
            ns = k[1:].split("}", 1)[0] if k.startswith("{") else ""
            if local.startswith("on"):
                raise ValueError("Event-Handler")
            if local == "href":
                if not v.startswith("#") or ns not in ("", XLINK_NS):
                    raise ValueError("externer Verweis")
            elif ns and ns != XLINK_NS and ns != "http://www.w3.org/XML/1998/namespace":
                del el.attrib[k]          # Editor-Metadaten (inkscape:, sodipodi: ...) entfernen
                continue
            if not _svg_value_ok(v):
                raise ValueError("unzulässiger Attributwert")
        for ch in list(el):
            if not isinstance(ch.tag, str):
                el.remove(ch)
                continue
            ns, _, name = ch.tag[1:].partition("}") if ch.tag.startswith("{") else ("", "", ch.tag)
            if ns != SVG_NS:
                el.remove(ch)             # fremde Namensräume (metadata, namedview ...) entfernen
                continue
            if name.lower() == "metadata":
                el.remove(ch)
                continue
            if name.lower() not in SVG_TAGS:
                raise ValueError(f"Element {name} nicht erlaubt")
            clean(ch, depth + 1)
        if el.tag.endswith("}style") and not _svg_value_ok(el.text or ""):
            raise ValueError("unzulässiges CSS")

    clean(root, 0)
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    out = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(out) > CONFIG_LOGO_MAX:
        raise ValueError("zu groß")
    return out


def logo_check(data: bytes, ctype: str) -> tuple[str, bytes]:
    """(Dateiendung, zu speichernde Bytes) oder ValueError(grund)."""
    ext = LOGO_TYPES.get(ctype)
    if not ext:
        raise ValueError("Logo nur als PNG, JPG oder SVG")
    if not data or len(data) > CONFIG_LOGO_MAX:
        raise ValueError(f"Logo max. {CONFIG_LOGO_MAX // 1024} KB")
    if ext == "png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Datei ist kein PNG")
    if ext == "jpg" and not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("Datei ist kein JPG")
    if ext == "svg":
        data = svg_sanitize(data)
    return ext, data


def current_config() -> dict:
    global CURRENT_CFG
    if CURRENT_CFG is None:
        try:
            CURRENT_CFG = _merge_missing(read_config_file(), config_defaults()) if CONFIG_PATH.exists() else config_defaults()
        except ConfigError:
            CURRENT_CFG = config_defaults()
    return CURRENT_CFG


def config_revision() -> int:
    r = current_config().get("revision", 0)
    return r if isinstance(r, int) and not isinstance(r, bool) else 0


def public_config(cfg: dict | None = None) -> dict:
    """Branding-Teil für alle angemeldeten Rollen (ohne Lizenz, Update-Quelle, Mandanten-ID)."""
    cfg = cfg or current_config()
    d = config_defaults()
    c = {**d["company"], **(cfg.get("company") or {})}
    lf = c.get("logoFile") or ""
    rev = config_revision()
    return {
        "revision": rev,
        "company": {
            "name": c["name"], "productName": c["productName"], "color": c["color"], "uiAccent": c["uiAccent"],
            "font": c["font"], "address": c["address"], "footer": c["footer"],
            "logoUrl": f"/api/config/logo?v={rev}" if lf else "",
            "logoType": LOGO_MIME.get(lf.rsplit(".", 1)[-1].lower(), "") if lf else "",
        },
        "locale": {**d["locale"], **(cfg.get("locale") or {})},
        "terms": {**d["terms"], **(cfg.get("terms") or {})},
        "modules": modules_effective(cfg),
        "projectAreas": cfg.get("projectAreas") or d["projectAreas"],
        "readOnly": int(cfg.get("schemaVersion", 1)) > CONFIG_SCHEMA,
        "setupDone": cfg.get("setupDone") is not False,
    }


# ---------------------------------------------------------------------------------------------
# V12.16.0: Module ein/aus. Aus = Ansicht/Menü/Aktionen weg, Endpunkte und Schreibzugriffe gesperrt (MP-MOD-001),
# die Daten bleiben vollständig erhalten. kpi hängt an postcalc (aus -> auch kpi aus).
# ---------------------------------------------------------------------------------------------
MODULE_LABELS = {"projects": "Projekte", "formats": "Formate", "personnel": "Personal", "chat": "Nachrichten",
                 "notifications": "Benachrichtigungen", "postcalc": "Auswertung", "kpi": "Kennzahlen", "palletLabels": "Palettenetiketten",
                 "frameOrders": "Rahmenaufträge", "gf": "GF-Steuerung", "history": "Historie"}
MODULE_DEPS = {"kpi": ("postcalc",)}
# Datensammlungen im Datenstand, die ein Modul besitzt (Schreiben nur bei eingeschaltetem Modul).
MODULE_STATE_KEYS = {
    "projects": ("projects", "processTemplates"),
    "formats": ("formats", "baseFormats"),
    "personnel": ("employees", "personnelAssignments", "personnelAbsences", "weeklyEmployeeDeployments",
                  "departmentStaffNeeds", "personnelGate"),
    "palletLabels": ("palletTemplates", "palletLabels"),
    "frameOrders": ("frameOrders", "callOffs"),
}


def modules_effective(cfg: dict | None = None) -> dict:
    cfg = cfg or current_config()
    m = {**CONFIG_MODULE_DEFAULTS, **{k: v for k, v in (cfg.get("modules") or {}).items() if k in CONFIG_MODULES}}
    for k, deps in MODULE_DEPS.items():
        if not all(m.get(d, True) for d in deps):
            m[k] = False
    return m


def module_on(name: str) -> bool:
    return bool(modules_effective().get(name, CONFIG_MODULE_DEFAULTS.get(name, True)))


def module_error(name: str) -> dict:
    return mp_error("MP-MOD-001", f"Funktion „{MODULE_LABELS.get(name, name)}“ ist abgeschaltet.", module=name)


def _norm_coll(v):
    return canonical(v if v not in (None, "", False, [], {}) else None)


def module_state_guard(old: dict, new: dict) -> tuple[str, str]:
    """('', '') wenn erlaubt, sonst (Modul, Meldung): ein abgeschaltetes Modul darf seine Datensammlungen nicht ändern."""
    mods = modules_effective()
    for mod, keys in MODULE_STATE_KEYS.items():
        if mods.get(mod, True):
            continue
        for k in keys:
            if _norm_coll(old.get(k)) != _norm_coll(new.get(k)):
                return mod, f"Funktion „{MODULE_LABELS[mod]}“ ist abgeschaltet: '{k}' kann nicht geändert werden."
    return "", ""


def _backup_logo(name: str) -> None:
    """Vorheriges Logo als logo.<ext>.bak-<zeit> aufheben (letzte 5)."""
    prev = (CONFIG_DIR / name) if name else None
    if prev and prev.is_file():
        shutil.copy2(prev, CONFIG_DIR / f"{name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        for old in sorted(CONFIG_DIR.glob("logo.*.bak-*"), key=lambda p: p.name, reverse=True)[5:]:
            old.unlink(missing_ok=True)


def config_apply(body: dict, logo: tuple[str, bytes] | None = None, remove_logo: bool = False) -> tuple[int, dict, str]:
    """Wendet eine Änderung an. Rückgabe (HTTP-Status, Antwort, Audit-Detail). Revision wird geprüft."""
    with CONFIG_LOCK:
        cfg = json.loads(json.dumps(current_config()))
        if int(cfg.get("schemaVersion", 1)) > CONFIG_SCHEMA:
            return 409, mp_error("MP-CFG-003", "Firmenprofil ist schreibgeschützt (Datei stammt von neuerer Version)."), ""
        rev = body.get("revision")
        if not isinstance(rev, int) or isinstance(rev, bool):
            return 400, mp_error("MP-CFG-002", "revision fehlt."), ""
        if rev != config_revision():
            return 409, mp_error("MP-CFG-004", "Firmenprofil wurde inzwischen geändert.", revision=config_revision(),
                                 config=public_config(cfg)), ""
        errs: list[str] = []
        changed: list[str] = []
        old_logo = (cfg.get("company") or {}).get("logoFile") or ""
        if (body.get("company") or {}).get("logoFile") == "" if isinstance(body.get("company"), dict) else False:
            remove_logo = True
        for k in body:
            if k != "revision" and k not in CONFIG_WRITE_SECTIONS and k not in ("projectAreas", "setupDone"):
                errs.append(f"{k}: nicht änderbar")
        if "setupDone" in body:
            if not isinstance(body["setupDone"], bool):
                errs.append("setupDone: true/false erwartet")
            elif (cfg.get("setupDone") is not False) != body["setupDone"]:
                cfg["setupDone"] = body["setupDone"]
                changed.append("setupDone")
        for sec, allowed in CONFIG_WRITE_SECTIONS.items():
            part = body.get(sec)
            if part is None:
                continue
            if not isinstance(part, dict):
                errs.append(f"{sec}: Objekt erwartet")
                continue
            for k, v in part.items():
                if k not in allowed:
                    errs.append(f"{sec}.{k}: nicht änderbar")
                elif k == "logoFile" and v != "":
                    errs.append("company.logoFile: nur '' (entfernen); Logo per Upload setzen")
                elif (cfg.setdefault(sec, {}).get(k) != v):
                    cfg[sec][k] = v
                    changed.append(f"{sec}.{k}")
        if "projectAreas" in body and body["projectAreas"] != cfg.get("projectAreas"):
            cfg["projectAreas"] = body["projectAreas"]
            changed.append("projectAreas")
        if errs:
            return 400, mp_error("MP-CFG-002", "; ".join(errs[:5])), ""
        new_file = ""
        if logo:
            new_file = f"logo.{logo[0]}"
            cfg["company"]["logoFile"] = new_file
            changed.append("company.logo")
        if not changed:
            return 200, {"ok": True, "unchanged": True, "config": public_config(cfg)}, ""
        # Zuerst Logo-Datei bereitstellen (Validierung prüft ihre Existenz), dann firma.json atomar schreiben.
        if new_file:
            probe = [e for e in validate_config({**cfg, "company": {**cfg["company"], "logoFile": ""}})]
            if probe:
                return 400, mp_error("MP-CFG-002", "; ".join(probe[:5])), ""
            _backup_logo(old_logo)
            _atomic_write(CONFIG_DIR / new_file, logo[1])
        else:
            probe = validate_config(cfg)
            if probe:
                return 400, mp_error("MP-CFG-002", "; ".join(probe[:5])), ""
            if remove_logo:
                _backup_logo(old_logo)
        cfg["revision"] = config_revision() + 1
        try:
            save_config(cfg)
        except ConfigError as e:
            return 400, mp_error(e.code, e.message), ""
        global CURRENT_CFG
        CURRENT_CFG = cfg
        if old_logo and old_logo != cfg["company"].get("logoFile") and (CONFIG_DIR / old_logo).is_file():
            (CONFIG_DIR / old_logo).unlink(missing_ok=True)
        return 200, {"ok": True, "config": public_config(cfg)}, ", ".join(changed)[:480]
SESSION_TTL = 12 * 60 * 60
MAX_BODY = 8 * 1024 * 1024
PBKDF2_ITERS = 310_000
DB_LOCK = threading.RLock()
REVISION_CONDITION = threading.Condition()
LOGIN_LOCK = threading.Lock()
# V12.10.2: Fehlversuche je IP, je Benutzer und je (IP, Benutzer); Schlüssel "ip:…", "user:…", "ipuser:…".
LOGIN_FAILS: dict[str, list[float]] = {}
LOGIN_WINDOW = 600
LOGIN_MAX_PER_IP_USER = 8
LOGIN_MAX_PER_USER = 20
LOGIN_MAX_PER_IP = 30
# Login/Logout/Passwort brauchen nur wenige Byte; großer Body vor der Anmeldung = Angriffsfläche.
MAX_AUTH_BODY = 16 * 1024
# Begrenzt gleichzeitige Verbindungen (30 Browser mit Long-Poll + Reserve).
MAX_CONNECTIONS = 256
# Zusätzlich erlaubte Host-Namen (Komma-getrennt), z. B. DNS-Alias des Servers.
EXTRA_ALLOWED_HOSTS = {h.strip().lower() for h in os.environ.get("MP_ALLOWED_HOSTS", "").split(",") if h.strip()}

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
ALLOWED_NETWORK = None
AUTH_POST_PATHS = {"/api/login", "/api/logout", "/api/password"}
UPDATE_MANAGER = None


def update_manager():
    global UPDATE_MANAGER
    with DB_LOCK:
        if UPDATE_MANAGER is None:
            UPDATE_MANAGER = UpdateManager(BASE, APP_VERSION, lambda: CURRENT_CFG or {})
        return UPDATE_MANAGER


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


# ---------------------------------------------------------------------------------------------
# V12.23.0 (#63/#55): Rollen & Rechte. Eigene Rollen (Rollenprofile) beruhen auf einer Systemrolle und
# schränken sie je Funktion (Kein Zugriff / Lesen / Bearbeiten) und je Aktion ein – nie darüber hinaus.
# Durchsetzung am Server: Datenstand (ausblenden), Speichern (Leserecht = unveränderlich) und Endpunkte.
# ---------------------------------------------------------------------------------------------
ROLE_FUNCTIONS = (("planning", "Planung & Produktion"), ("projects", "Projekte"), ("frameOrders", "Rahmenaufträge"),
                  ("personnel", "Personal"), ("formats", "Formate"), ("gf", "GF-Steuerung"), ("report", "Report"),
                  ("history", "Historie"), ("chat", "Chat"), ("notifications", "Benachrichtigungen"), ("system", "System"))
ROLE_ACTIONS = (("production", "Produktion melden (Start, Pause, Fertig)"), ("confectionHours", "Konfektionsstunden vorgeben"),
                ("userAdmin", "Benutzer verwalten"), ("updates", "Updates installieren"))
ROLE_LEVELS = ("none", "read", "edit")
# Bei „Lesen" unveränderlich, bei „Kein Zugriff" zusätzlich ausgeblendet (FUNCTION_HIDDEN).
FUNCTION_DATA = {
    "planning": ("workSteps", "machineBlocks", "planVersions"),
    "projects": ("projects", "processTemplates"),
    "personnel": ("employees", "personnelAssignments", "personnelAbsences", "weeklyEmployeeDeployments", "departmentStaffNeeds"),
    "formats": ("formats", "baseFormats"),
    "system": ("machines", "departments", "shiftTemplates", "yearRules", "weekRules", "exceptions", "operatorCapacity", "personnelGate", "palletTemplates"),
}
# Planung und System bleiben sichtbar: Scheduler und Projekt-Liveansicht brauchen Ressourcen und FA.
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


def role_profiles(con) -> dict:
    return {r["id"]: json.loads(r["json"]) for r in con.execute("SELECT id,json FROM role_profiles ORDER BY id")}


def attach_rights(user: dict, profile: dict | None) -> dict:
    """Effektive Rechte am Benutzer: Profil schränkt die Systemrolle ein; ohne Profil volle Systemrolle."""
    user["profileId"] = profile["id"] if profile else ""
    user["profileName"] = profile["name"] if profile else ""
    user["rights"] = {k: (profile or {}).get("rights", {}).get(k, "edit") for k, _ in ROLE_FUNCTIONS}
    user["actions"] = {k: bool((profile or {}).get("actions", {}).get(k, True)) for k, _ in ROLE_ACTIONS}
    return user


def user_level(user: dict, function: str) -> str:
    return (user.get("rights") or {}).get(function, "edit")


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
    if not isinstance(actions, dict) or any(k not in dict(ROLE_ACTIONS) or not isinstance(v, bool) for k, v in actions.items()):
        raise ValueError("Aktionsrechte: nur ja/nein.")
    return {"id": rid, "name": name, "baseRole": base, "description": str(body.get("description") or "").strip()[:300],
            "active": body.get("active", True) is not False,
            "rights": {k: rights.get(k, "edit") for k, _ in ROLE_FUNCTIONS}, "actions": {k: actions.get(k, True) for k, _ in ROLE_ACTIONS}}


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
    if not user_action(user, "confectionHours"):
        dep_names = {str(d.get("id")): str(d.get("name") or "") for d in old.get("departments") or [] if isinstance(d, dict)}
        before = _record_map(old.get("workSteps"))
        for rid, after in _record_map(incoming.get("workSteps")).items():
            prev = before.get(rid) or {}
            dep = str(after.get("departmentId") or "")
            confection = "konf" in dep.casefold() or "konf" in dep_names.get(dep, "").casefold() or after.get("planningType") == "LABOR_HOURS"
            if confection and (canonical(prev.get("hours", 0)) != canonical(after.get("hours", 0)) or canonical(prev.get("requiredHours")) != canonical(after.get("requiredHours"))):
                return "Konfektionsstunden vorgeben ist für diese Rolle gesperrt."
    return ""


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


# V12.17.0: Startbestand der Werbetechnik (bis V12.16.x fest im Seed). Nur noch für Tests (init_db(seed="werbetechnik")).
LEGACY_MACHINES = [
    {"id": "m1", "name": "Maschine 1", "departmentId": "cnc", "setupMinutes": 0, "start": "2026-09-07T06:30", "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 1, "effortScaling": False, "crewMax": 0, "laneStaff": {}},
    {"id": "m2", "name": "Maschine 2", "departmentId": "cnc", "setupMinutes": 0, "start": "2026-09-07T06:30", "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 1, "effortScaling": False, "crewMax": 0, "laneStaff": {}},
    {"id": "m3", "name": "Maschine 3", "departmentId": "cnc", "setupMinutes": 0, "start": "2026-09-07T06:30", "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 1, "effortScaling": False, "crewMax": 0, "laneStaff": {}},
]
LEGACY_DEPARTMENTS = [
    {"id": "cnc", "name": "CNC", "planningType": "MACHINE", "active": True, "sharedOperators": True},
    {"id": "konf1", "name": "Konfektion 1", "planningType": "LABOR_HOURS", "active": True},
    {"id": "konf2", "name": "Konfektion 2", "planningType": "LABOR_HOURS", "active": True},
    {"id": "konf3", "name": "Konfektion 3", "planningType": "LABOR_HOURS", "active": True},
    {"id": "screenprint", "name": "Siebdruck", "planningType": "PROCESS", "active": True},
    {"id": "thermoforming", "name": "Tiefziehen", "planningType": "CYCLE", "active": True, "formats": True},
]


def init_db(seed: str = "neutral") -> None:
    """seed="neutral": Neuinstallation ohne Bereiche/Maschinen (V12.17.0, der Einrichtungsassistent setzt sie auf).
    seed="werbetechnik": alter Startbestand, nur für Tests und Entwicklung."""
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
            CREATE TABLE IF NOT EXISTS history_archive(
              id TEXT PRIMARY KEY,
              finished_at TEXT NOT NULL,
              archived_at TEXT NOT NULL,
              json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS history_archive_finished ON history_archive(finished_at);
            CREATE TABLE IF NOT EXISTS chat_channels(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL CHECK(kind IN ('all','group','direct')),
              name TEXT NOT NULL DEFAULT '',
              members TEXT NOT NULL DEFAULT '[]',
              created_by TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_messages(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              channel_id INTEGER NOT NULL REFERENCES chat_channels(id) ON DELETE CASCADE,
              author TEXT NOT NULL,
              text TEXT NOT NULL,
              ts TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chat_messages_channel ON chat_messages(channel_id, id);
            CREATE TABLE IF NOT EXISTS chat_reads(
              username TEXT NOT NULL COLLATE NOCASE,
              channel_id INTEGER NOT NULL,
              last_id INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(username, channel_id)
            );
            CREATE TABLE IF NOT EXISTS notifications(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL COLLATE NOCASE,
              kind TEXT NOT NULL,
              ref_type TEXT NOT NULL DEFAULT '',
              ref_id TEXT NOT NULL DEFAULT '',
              text TEXT NOT NULL,
              created_at TEXT NOT NULL,
              read_at TEXT,
              dedupe TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS notifications_user ON notifications(username, id);
            CREATE UNIQUE INDEX IF NOT EXISTS notifications_dedupe ON notifications(username, dedupe) WHERE dedupe<>'';
            CREATE TABLE IF NOT EXISTS notification_prefs(
              username TEXT PRIMARY KEY COLLATE NOCASE,
              json TEXT NOT NULL
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
        con.execute("CREATE TABLE IF NOT EXISTS production_requests(username TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(username,request_id))")
        con.execute("CREATE TABLE IF NOT EXISTS retired_usernames(username TEXT PRIMARY KEY COLLATE NOCASE,retired_at TEXT NOT NULL)")
        migrate_users_schema(con)
        migrate_role_profiles(con)
        con.execute("UPDATE users SET department_id='' WHERE role IN ('admin','gf','project_management','production_planning','sales') AND department_id<>''")
        row = con.execute("SELECT id FROM state WHERE id=1").fetchone()
        if not row:
            initial = {
                "version": 12,
                "meta": {"revision": 1, "actor": "Server", "storage": "server", "createdAt": now_iso(), "serverReady": True},
                "shiftTemplates": {
                    "single": {"name": "1-Schicht Mo–Do", "start": "06:30", "end": "16:00", "breaks": [{"start": "09:00", "end": "09:15"}, {"start": "12:00", "end": "12:30"}]},
                    "fridaySingle": {"name": "1-Schicht Freitag", "start": "06:30", "end": "11:45", "breaks": [{"start": "09:00", "end": "09:15"}, {"start": "", "end": ""}]},
                    "early": {"name": "2-Schicht · Früh", "start": "06:00", "end": "14:30", "breaks": [{"start": "09:00", "end": "09:15"}, {"start": "12:00", "end": "12:15"}]},
                    "late": {"name": "2-Schicht · Spät", "start": "14:30", "end": "22:30", "breaks": [{"start": "17:00", "end": "17:15"}, {"start": "19:00", "end": "19:15"}]},
                },
                "departments": [],
                "machines": [],
                "projects": [],
                "workSteps": [],
                "yearRules": [], "weekRules": [], "exceptions": [],
                "operatorCapacity": {"single": 3, "early": 3, "late": 3},
                "employees": [], "personnelAssignments": [], "personnelAbsences": [], "weeklyEmployeeDeployments": [], "departmentStaffNeeds": [], "personnelGate": False,
                "history": [], "audit": [], "planVersions": [],
                "ui": {"accent": "#1f5eff", "cellH": 90},
            }
            if seed == "werbetechnik":
                initial["machines"] = LEGACY_MACHINES
                initial["departments"] = LEGACY_DEPARTMENTS
            con.execute("INSERT INTO state(id,revision,json,updated_at,updated_by) VALUES(1,1,?,?,?)", (json.dumps(initial, ensure_ascii=False), now_iso(), "Server"))
        migrate_state_v1242(con)
        migrate_state_v1243(con)
        migrate_state_v1244(con)
        migrate_state_v1260(con)
        migrate_state_v1261(con)
        migrate_state_v1270(con)
        normalize_state_v1270(con)
        migrate_state_v1280(con)
        migrate_state_v1216(con)
        migrate_state_v1218(con)
        migrate_state_v1219(con)
        if not con.execute("SELECT 1 FROM chat_channels WHERE kind='all'").fetchone():
            con.execute("INSERT INTO chat_channels(kind,name,members,created_by,created_at) VALUES('all','Alle','[]','Server',?)", (now_iso(),))
        migrate_chat_v1291(con)
        chat_purge(con, force=True)
        notif_purge(con, force=True)
        archive_history_on_start(con)


def migrate_state_v1280(con: sqlite3.Connection) -> None:
    """V12.8.0: Formatlisten anlegen. Der Client führt sie immer; fehlten sie im Serverstand,
    sähe der erste Speichervorgang von GF/PM/Vertrieb wie eine unerlaubte Änderung aus."""
    row = con.execute("SELECT json FROM state WHERE id=1").fetchone()
    if not row:
        return
    state = json.loads(row["json"])
    changed = False
    for key in ("formats", "baseFormats"):
        if not isinstance(state.get(key), list):
            state[key] = []
            changed = True
    if changed:
        con.execute("UPDATE state SET json=?,updated_at=?,updated_by=? WHERE id=1",
                    (json.dumps(state, ensure_ascii=False, separators=(",", ":")), now_iso(), "V12.8.0 migration"))
        print("DB-MIGRATION state: V12.8.0 Formatlisten angelegt")


FA_SOURCE_TYPES = {"PROJECT", "FRAME_ORDER", "STOCK_REQUIREMENT"}


def normalize_fa_state(state: dict) -> None:
    """Add canonical references without deleting or renumbering legacy records."""
    machine_dept = {str(m.get("id")): m.get("departmentId") for m in state.get("machines", [])}
    def record(x):
        if not isinstance(x, dict):
            return
        number = str(x.get("fa") or x.get("faNumber") or x.get("fs") or x.get("order") or "")
        if not x.get("fa"):
            x["fa"] = number
        if not x.get("faNumber"):
            x["faNumber"] = number
        if not x.get("departmentId") and machine_dept.get(str(x.get("machineId"))):
            x["departmentId"] = machine_dept[str(x["machineId"])]
        source = "FRAME_ORDER" if x.get("frameOrderId") or x.get("callOffId") else "STOCK_REQUIREMENT" if x.get("stockRequirementId") else "PROJECT"
        x.setdefault("sourceType", source)
        x.setdefault("sourceId", str(x.get("callOffId") or x.get("frameOrderId") or x.get("stockRequirementId") or x.get("projectId") or x.get("id") or ""))
    for key in ("productionEvents", "palletLabels", "palletTemplates", "inventory", "frameOrders", "callOffs"):
        state.setdefault(key, [])
    for key in ("workSteps", "history", "orders"):
        for x in state.get(key) or []:
            record(x)
    for f in state.get("formats") or []:
        for t in f.get("tools") or []:
            t.setdefault("fa", str(t.get("fs") or t.get("order") or ""))
    for v in state.get("planVersions") or []:
        if isinstance(v.get("payload"), dict):
            normalize_fa_state(v["payload"])


def migrate_state_v1219(con: sqlite3.Connection) -> None:
    row = con.execute("SELECT json FROM state WHERE id=1").fetchone()
    if not row:
        return
    state = json.loads(row["json"])
    before = canonical(state)
    normalize_fa_state(state)
    if canonical(state) != before:
        con.execute("UPDATE state SET json=? WHERE id=1", (json.dumps(state, ensure_ascii=False),))
    # Archived JSON remains immutable; the read boundary normalizes a copy.


def migrate_state_v1218(con: sqlite3.Connection) -> None:
    """V12.18.0: Dauer nach Besetzung (effortScaling, Default aus), maximale Besetzung (crewMax, 0 = keine Obergrenze) und
    Personal je Parallelplatz (laneStaff, leer). Nur additiv und idempotent: fehlende Felder bekommen den Default,
    vorhandene Werte bleiben. Bestehende Auftraege aendern ihre Dauer dadurch nicht (Schalter aus)."""
    row = con.execute("SELECT json FROM state WHERE id=1").fetchone()
    if not row:
        return
    state = json.loads(row["json"])
    changed = False
    for m in state.get("machines") or []:
        if not isinstance(m, dict):
            continue
        for key, default in (("effortScaling", False), ("crewMax", 0), ("laneStaff", {})):
            if key not in m:
                m[key] = default
                changed = True
    if changed:
        con.execute("UPDATE state SET json=?,updated_at=?,updated_by=? WHERE id=1",
                    (json.dumps(state, ensure_ascii=False, separators=(",", ":")), now_iso(), "V12.18.0 migration"))
        print("DB-MIGRATION state: V12.18.0 Dauer nach Besetzung (effortScaling, crewMax, laneStaff) ergaenzt")


def migrate_state_v1216(con: sqlite3.Connection) -> None:
    """V12.16.0: Bereichs-Eigenschaften statt fester Bereichs-IDs. Nur additiv und idempotent: thermoforming.formats=true und
    cnc.sharedOperators=true, wenn die Eigenschaft dort noch FEHLT (ein vorhandener Wert, auch false, bleibt)."""
    row = con.execute("SELECT json FROM state WHERE id=1").fetchone()
    if not row:
        return
    state = json.loads(row["json"])
    changed = False
    for d in state.get("departments") or []:
        if not isinstance(d, dict):
            continue
        for did, prop in (("thermoforming", "formats"), ("cnc", "sharedOperators")):
            if d.get("id") == did and prop not in d:
                d[prop] = True
                changed = True
    if changed:
        con.execute("UPDATE state SET json=?,updated_at=?,updated_by=? WHERE id=1",
                    (json.dumps(state, ensure_ascii=False, separators=(",", ":")), now_iso(), "V12.16.0 migration"))
        print("DB-MIGRATION state: V12.16.0 Bereichs-Eigenschaften (formats, sharedOperators)")


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
            fa = str(x.get("fa") or x.get("fs") or x.get("fertigungsauftrag") or x.get("fertigungsschein") or order).strip()
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
                "fa": fa, "ab": str(x.get("ab") or ""), "wt": str(x.get("wt") or ""),
                "machineId": mid, "altMachineId": alt, "allowAlternative": bool(alt),
                "order": order or fa, "status": str(x.get("status") or "planned") if str(x.get("status") or "planned") in valid_status else "planned",
            })
            steps.append(x)
        dept_types = {str(d.get("id")): str(d.get("planningType") or "LABOR_HOURS") for d in (state.get("departments") or []) if isinstance(d, dict)}
        for j, old in enumerate(state.get("departmentPlans") or []):
            if not isinstance(old, dict):
                continue
            x = dict(old); did = str(x.get("departmentId") or "")
            x.update({"id": str(x.get("id") or f"legacy_dp_{j+1}"), "planningType": str(x.get("planningType") or dept_types.get(did) or "LABOR_HOURS"), "sequence": float(x.get("sequence") or ((len(steps)+j+1)*10)), "predecessorIds": list(x.get("predecessorIds") or []), "projectId": str(x.get("projectId") or ""), "fa": str(x.get("fa") or x.get("fs") or x.get("order") or ""), "ab": str(x.get("ab") or ""), "wt": str(x.get("wt") or ""), "status": str(x.get("status") or "planned")})
            steps.append(x)
        state["workSteps"] = steps
        changed = True
        print(f"DB-MIGRATION state: legacy orders/departmentPlans -> workSteps ({len(steps)})")
    projects = {str(p.get("id")): p for p in (state.get("projects") or []) if isinstance(p, dict)}
    for step in (state.get("workSteps") or []):
        if not isinstance(step, dict):
            continue
        if step.get("planningType") == "MACHINE" and not str(step.get("fa") or "").strip():
            step["fa"] = str(step.get("order") or "").strip()
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
        print("DB-MIGRATION state: V12.4.3 FA/AB/WT hierarchy normalized")


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
                             "setupMinutes": 0, "start": start_default, "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 0, "effortScaling": False, "crewMax": 0, "laneStaff": {}})
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
        fa = str(x.get("fa") or x.get("fs") or x.get("order") or "").strip() or str(x.get("id"))
        common = {"projectId": str(x.get("projectId") or ""), "fa": fa, "ab": str(x.get("ab") or ""), "wt": str(x.get("wt") or ""),
                  "order": fa, "articleNo": "", "description": "", "targetQty": int(max(0, _finite_float(x.get("quantity", 0)) or 0)),
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
        # V12.23.0: Ressourcentyp workplace (Arbeitsplatz/Prozessplatz) plant wie eine Maschine.
        kind = m.get("kind") if m.get("kind") in {"line", "workplace"} else "machine"
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


# --------------------------------------------------------------------------- V12.9.0 Messenger
# Eigene Tabellen statt Live-State: Nachrichten erhöhen keine Planungsrevision und kollidieren nicht
# mit Planänderungen. Erwähnungen (/FA, /Projekt, /Format, @Benutzer) speichert der Client als Token
# im Text; der Server speichert nur Text und prüft Mitgliedschaft.
CHAT_MAX_TEXT = 2000
CHAT_MAX_MEMBERS = 100
# V12.9.1: Nachrichten verschwinden nach CHAT_RETENTION_DAYS Tagen, außer sie sind auf „Behalten“ gesetzt.
CHAT_RETENTION_DAYS = 30
CHAT_PURGE_EVERY = 3600
_CHAT_LAST_PURGE = 0.0
CHAT_MSG_COLS = "id,author,text,ts,keep,kept_by"


def migrate_chat_v1291(con: sqlite3.Connection) -> None:
    cols = {r["name"] for r in con.execute("PRAGMA table_info(chat_messages)").fetchall()}
    if "keep" not in cols:
        con.execute("ALTER TABLE chat_messages ADD COLUMN keep INTEGER NOT NULL DEFAULT 0")
    if "kept_by" not in cols:
        con.execute("ALTER TABLE chat_messages ADD COLUMN kept_by TEXT NOT NULL DEFAULT ''")


def chat_purge(con: sqlite3.Connection, force: bool = False) -> int:
    """Löscht nicht behaltene Nachrichten älter als CHAT_RETENTION_DAYS (höchstens einmal pro Stunde)."""
    global _CHAT_LAST_PURGE
    now = time.time()
    if not force and now - _CHAT_LAST_PURGE < CHAT_PURGE_EVERY:
        return 0
    _CHAT_LAST_PURGE = now
    cutoff = (datetime.now(timezone.utc) - timedelta(days=CHAT_RETENTION_DAYS)).isoformat()
    n = con.execute("DELETE FROM chat_messages WHERE keep=0 AND ts<?", (cutoff,)).rowcount
    if n:
        print(f"CHAT: {n} Nachricht(en) älter als {CHAT_RETENTION_DAYS} Tage gelöscht", flush=True)
    return n


def _chat_msg(row) -> dict:
    return {"id": row["id"], "author": row["author"], "text": row["text"], "ts": row["ts"], "keep": bool(row["keep"]), "keptBy": row["kept_by"]}


def _chat_channel_for(con, channel_id, username: str):
    row = con.execute("SELECT * FROM chat_channels WHERE id=?", (channel_id,)).fetchone()
    if not row:
        return None
    if row["kind"] == "all":
        return row
    members = {str(x).casefold() for x in json.loads(row["members"] or "[]")}
    return row if username.casefold() in members else None


def _chat_channel_json(con, row, username: str) -> dict:
    last = con.execute("SELECT id,author,text,ts FROM chat_messages WHERE channel_id=? ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
    read = con.execute("SELECT last_id FROM chat_reads WHERE username=? AND channel_id=?", (username, row["id"])).fetchone()
    read_id = read["last_id"] if read else 0
    unread = con.execute("SELECT COUNT(*) FROM chat_messages WHERE channel_id=? AND id>? AND author<>? COLLATE NOCASE", (row["id"], read_id, username)).fetchone()[0]
    # Erwähnung in Python prüfen: SQLite-lower() kennt nur ASCII (Ö/ß in Benutzernamen).
    tag = f"⟦u:{username.casefold()}⟧"
    mention = 0
    if unread:
        mention = sum(1 for r in con.execute("SELECT text FROM chat_messages WHERE channel_id=? AND id>? AND author<>? COLLATE NOCASE AND instr(text, '⟦u:')>0 ORDER BY id DESC LIMIT 500",
                                             (row["id"], read_id, username)).fetchall() if tag in r["text"].casefold())
    return {"id": row["id"], "kind": row["kind"], "name": row["name"], "members": json.loads(row["members"] or "[]"), "createdBy": row["created_by"],
            "last": dict(last) if last else None, "unread": unread, "mentions": mention, "readId": read_id}


def chat_get(con, user: dict, path: str, qs: dict) -> tuple[int, dict]:
    username = user["username"]
    if path == "/api/chat/users":
        rows = con.execute("SELECT username,role,department_id FROM users WHERE active=1 ORDER BY username COLLATE NOCASE").fetchall()
        return 200, {"users": [{"username": r["username"], "role": r["role"], "departmentId": r["department_id"]} for r in rows]}
    if path == "/api/chat/channels":
        chat_purge(con)
        out = []
        for row in con.execute("SELECT * FROM chat_channels ORDER BY id").fetchall():
            if _chat_channel_for(con, row["id"], username):
                out.append(_chat_channel_json(con, row, username))
        return 200, {"channels": out, "me": username, "retentionDays": CHAT_RETENTION_DAYS}
    if path == "/api/chat/messages":
        try:
            cid = int(qs.get("channel", ["0"])[0])
            after = max(0, int(qs.get("after", ["0"])[0]))
            before = max(0, int(qs.get("before", ["0"])[0]))
        except ValueError:
            return 400, mp_error("MP-CHAT-001", "Kanal/Position ungültig.")
        if not _chat_channel_for(con, cid, username):
            return 404, mp_error("MP-CHAT-002", "Unterhaltung nicht gefunden oder kein Mitglied.")
        if before:
            rows = con.execute(f"SELECT {CHAT_MSG_COLS} FROM chat_messages WHERE channel_id=? AND id<? ORDER BY id DESC LIMIT 100", (cid, before)).fetchall()[::-1]
        elif after:
            rows = con.execute(f"SELECT {CHAT_MSG_COLS} FROM chat_messages WHERE channel_id=? AND id>? ORDER BY id LIMIT 500", (cid, after)).fetchall()
        else:
            rows = con.execute(f"SELECT {CHAT_MSG_COLS} FROM chat_messages WHERE channel_id=? ORDER BY id DESC LIMIT 100", (cid,)).fetchall()[::-1]
        kept = []
        if qs.get("kept"):
            kept = [_chat_msg(r) for r in con.execute(f"SELECT {CHAT_MSG_COLS} FROM chat_messages WHERE channel_id=? AND keep=1 ORDER BY id DESC LIMIT 200", (cid,)).fetchall()]
        return 200, {"messages": [_chat_msg(r) for r in rows], "kept": kept, "retentionDays": CHAT_RETENTION_DAYS}
    return 404, mp_error("MP-REQ-404", "Nicht gefunden.")


def _chat_members(con, raw, me: str) -> tuple[list | None, str]:
    if not isinstance(raw, list) or len(raw) > CHAT_MAX_MEMBERS:
        return None, "Mitgliederliste ist ungültig."
    known = {r["username"].casefold(): r["username"] for r in con.execute("SELECT username FROM users WHERE active=1").fetchall()}
    out = []
    for name in [me, *raw]:
        key = str(name or "").strip().casefold()
        if key not in known:
            return None, f"Benutzer '{name}' ist unbekannt oder inaktiv."
        if known[key] not in out:
            out.append(known[key])
    return out, ""


def chat_post(con, user: dict, path: str, body: dict) -> tuple[int, dict]:
    username = user["username"]
    ts = now_iso()
    if path == "/api/chat/channels":
        kind = str(body.get("kind") or "group")
        members, err = _chat_members(con, body.get("members") or [], username)
        if members is None:
            return 400, mp_error("MP-CHAT-003", err)
        if kind == "direct":
            if len(members) != 2:
                return 400, mp_error("MP-CHAT-003", "Direktnachricht: genau eine andere Person wählen.")
            for row in con.execute("SELECT * FROM chat_channels WHERE kind='direct'").fetchall():
                if {x.casefold() for x in json.loads(row["members"])} == {x.casefold() for x in members}:
                    return 200, {"channel": _chat_channel_json(con, row, username)}
            name = ""
        elif kind == "group":
            name = str(body.get("name") or "").strip()
            if not name or len(name) > 60:
                return 400, mp_error("MP-CHAT-004", "Gruppenname fehlt oder ist zu lang (max. 60).")
            if len(members) < 2:
                return 400, mp_error("MP-CHAT-003", "Gruppe: mindestens eine weitere Person wählen.")
        else:
            return 400, mp_error("MP-CHAT-003", "Unbekannte Art der Unterhaltung.")
        cur = con.execute("INSERT INTO chat_channels(kind,name,members,created_by,created_at) VALUES(?,?,?,?,?)", (kind, name, json.dumps(members, ensure_ascii=False), username, ts))
        row = con.execute("SELECT * FROM chat_channels WHERE id=?", (cur.lastrowid,)).fetchone()
        return 201, {"channel": _chat_channel_json(con, row, username)}
    try:
        cid = int(body.get("channel") or 0)
    except (TypeError, ValueError):
        return 400, mp_error("MP-CHAT-001", "Kanal ungültig.")
    row = _chat_channel_for(con, cid, username)
    if not row:
        return 404, mp_error("MP-CHAT-002", "Unterhaltung nicht gefunden oder kein Mitglied.")
    if path == "/api/chat/messages":
        text = str(body.get("text") or "").strip()
        if not text:
            return 400, mp_error("MP-CHAT-005", "Nachricht ist leer.")
        if len(text) > CHAT_MAX_TEXT:
            return 400, mp_error("MP-CHAT-005", f"Nachricht ist zu lang (max. {CHAT_MAX_TEXT} Zeichen).")
        cur = con.execute("INSERT INTO chat_messages(channel_id,author,text,ts) VALUES(?,?,?,?)", (cid, username, text, ts))
        con.execute("INSERT INTO chat_reads(username,channel_id,last_id) VALUES(?,?,?) ON CONFLICT(username,channel_id) DO UPDATE SET last_id=excluded.last_id", (username, cid, cur.lastrowid))
        notify_chat_message(con, row, username, text)
        return 201, {"message": {"id": cur.lastrowid, "author": username, "text": text, "ts": ts, "keep": False, "keptBy": ""}}
    if path == "/api/chat/keep":
        try:
            mid = int(body.get("message") or 0)
        except (TypeError, ValueError):
            mid = 0
        msg = con.execute(f"SELECT {CHAT_MSG_COLS} FROM chat_messages WHERE id=? AND channel_id=?", (mid, cid)).fetchone()
        if not msg:
            return 404, mp_error("MP-CHAT-007", "Nachricht nicht gefunden (bereits gelöscht?).")
        keep = bool(body.get("keep"))
        con.execute("UPDATE chat_messages SET keep=?, kept_by=? WHERE id=?", (1 if keep else 0, username if keep else "", mid))
        return 200, {"message": _chat_msg(con.execute(f"SELECT {CHAT_MSG_COLS} FROM chat_messages WHERE id=?", (mid,)).fetchone())}
    if path == "/api/chat/read":
        try:
            last = max(0, int(body.get("lastId") or 0))
        except (TypeError, ValueError):
            return 400, mp_error("MP-CHAT-001", "Position ungültig.")
        con.execute("INSERT INTO chat_reads(username,channel_id,last_id) VALUES(?,?,?) ON CONFLICT(username,channel_id) DO UPDATE SET last_id=max(last_id,excluded.last_id)", (username, cid, last))
        # Gelesener Kanal: die zugehörigen Glocken-Einträge sind damit ebenfalls erledigt.
        con.execute("UPDATE notifications SET read_at=? WHERE username=? AND ref_type='chat' AND ref_id=? AND read_at IS NULL", (now_iso(), username, str(cid)))
        return 200, {"ok": True}
    if path == "/api/chat/members":
        if row["kind"] != "group":
            return 400, mp_error("MP-CHAT-006", "Mitglieder ändern nur bei Gruppen.")
        if row["created_by"].casefold() != username.casefold() and user["role"] != "admin":
            return 403, mp_error("MP-CHAT-006", "Mitglieder ändert, wer die Gruppe angelegt hat (oder Admin).")
        current = json.loads(row["members"])
        members, err = _chat_members(con, [*current, *(body.get("add") or [])], row["created_by"])
        if members is None:
            return 400, mp_error("MP-CHAT-003", err)
        remove = {str(x).casefold() for x in (body.get("remove") or [])} - {row["created_by"].casefold()}
        members = [m for m in members if m.casefold() not in remove]
        name = str(body.get("name") or row["name"]).strip()
        if not name or len(name) > 60:
            return 400, mp_error("MP-CHAT-004", "Gruppenname fehlt oder ist zu lang (max. 60).")
        con.execute("UPDATE chat_channels SET members=?, name=? WHERE id=?", (json.dumps(members, ensure_ascii=False), name, cid))
        return 200, {"channel": _chat_channel_json(con, con.execute("SELECT * FROM chat_channels WHERE id=?", (cid,)).fetchone(), username)}
    return 404, mp_error("MP-REQ-404", "Nicht gefunden.")


# --------------------------------------------------------------------------- V12.13.0 Benachrichtigungen
# Wie der Chat in eigenen Tabellen (keine Planungsrevision). Ereignisse entstehen beim PUT /api/state aus dem
# Diff alt -> neu, aus dem Chat (Erwähnung/Direktnachricht) und aus Client-Meldungen (gefährdeter Liefertermin).
# Jeder sieht nur die eigenen Einträge; Empfänger werden beim Erzeugen nach Rolle/Bereich gefiltert.
NOTIF_KINDS = ("mention", "release", "risk", "block", "loan")
NOTIF_RETENTION_DAYS = 30
NOTIF_PURGE_EVERY = 3600
NOTIF_MAX_PER_USER = 500
NOTIF_MAX_DERIVED = 50
NOTIF_LIST_LIMIT = 50
_NOTIF_LAST_PURGE = 0.0
NOTIF_QUIET_DEFAULT = {"on": False, "from": "20:00", "to": "06:00"}
_ACTIVE_STEP = {"planned", "released", "running", "paused"}
_MENTION_TOKEN = re.compile(r"⟦([opfu]):([^|⟧]{1,80})(?:\|([^⟧]{0,80}))?⟧")


def notif_default_kinds(role: str) -> dict:
    dept = role in DEPARTMENT_ROLES
    return {
        "mention": True,
        "release": dept,
        "risk": dept or role in {"project_management", "sales", "gf"},
        "block": dept or role == "production_planning",
        "loan": dept or role == "gf",
    }


def notif_prefs(con, username: str, role: str) -> dict:
    kinds = notif_default_kinds(role)
    quiet = dict(NOTIF_QUIET_DEFAULT)
    row = con.execute("SELECT json FROM notification_prefs WHERE username=?", (username,)).fetchone()
    if row:
        try:
            stored = json.loads(row["json"])
        except ValueError:
            stored = {}
        for k, v in (stored.get("kinds") or {}).items():
            if k in kinds:
                kinds[k] = bool(v)
        q = stored.get("quiet") or {}
        if _clock_minutes(q.get("from")) is not None and _clock_minutes(q.get("to")) is not None:
            quiet = {"on": bool(q.get("on")), "from": q["from"], "to": q["to"]}
    return {"kinds": kinds, "quiet": quiet}


def notif_prefs_validate(body) -> tuple[dict | None, str]:
    if not isinstance(body, dict):
        return None, "Einstellungen ungültig."
    out: dict = {}
    if "kinds" in body:
        kinds = body["kinds"]
        if not isinstance(kinds, dict) or set(kinds) - set(NOTIF_KINDS) or any(not isinstance(v, bool) for v in kinds.values()):
            return None, "Ereignisarten ungültig."
        out["kinds"] = kinds
    if "quiet" in body:
        q = body["quiet"]
        if not isinstance(q, dict) or set(q) - {"on", "from", "to"} or not isinstance(q.get("on", False), bool) \
                or _clock_minutes(q.get("from", "20:00")) is None or _clock_minutes(q.get("to", "06:00")) is None:
            return None, "Ruhezeit ungültig (HH:MM)."
        out["quiet"] = {"on": q.get("on", False), "from": q.get("from", "20:00"), "to": q.get("to", "06:00")}
    return out, ""


def notif_prefs_save(con, username: str, role: str, patch: dict) -> dict:
    row = con.execute("SELECT json FROM notification_prefs WHERE username=?", (username,)).fetchone()
    try:
        stored = json.loads(row["json"]) if row else {}
    except ValueError:
        stored = {}
    if "kinds" in patch:
        stored["kinds"] = {**(stored.get("kinds") or {}), **patch["kinds"]}
    if "quiet" in patch:
        stored["quiet"] = patch["quiet"]
    con.execute("INSERT INTO notification_prefs(username,json) VALUES(?,?) ON CONFLICT(username) DO UPDATE SET json=excluded.json",
                (username, json.dumps(stored, ensure_ascii=False)))
    return notif_prefs(con, username, role)


def notif_purge(con: sqlite3.Connection, force: bool = False) -> int:
    """Löscht Benachrichtigungen älter als NOTIF_RETENTION_DAYS (höchstens einmal pro Stunde)."""
    global _NOTIF_LAST_PURGE
    now = time.time()
    if not force and now - _NOTIF_LAST_PURGE < NOTIF_PURGE_EVERY:
        return 0
    _NOTIF_LAST_PURGE = now
    cutoff = (datetime.now(timezone.utc) - timedelta(days=NOTIF_RETENTION_DAYS)).isoformat()
    n = con.execute("DELETE FROM notifications WHERE created_at<?", (cutoff,)).rowcount
    if n:
        print(f"NOTIF: {n} Benachrichtigung(en) älter als {NOTIF_RETENTION_DAYS} Tage gelöscht", flush=True)
    return n


def notif_sig(con, username: str) -> dict:
    row = con.execute("SELECT COALESCE(MAX(id),0) AS last, COALESCE(SUM(read_at IS NULL),0) AS unread FROM notifications WHERE username=?", (username,)).fetchone()
    last, unread = int(row["last"]), int(row["unread"])
    return {"last": last, "unread": unread, "sig": f"{last}:{unread}"}


def _notif_row(r) -> dict:
    return {"id": r["id"], "kind": r["kind"], "refType": r["ref_type"], "refId": r["ref_id"], "text": r["text"], "createdAt": r["created_at"], "read": r["read_at"] is not None}


def notif_add(con, username: str, role: str, kind: str, ref_type: str, ref_id, text: str, dedupe: str = "") -> bool:
    """Legt einen Eintrag an, wenn die Art für diesen Benutzer eingeschaltet ist (und nicht doppelt)."""
    if kind not in NOTIF_KINDS or not notif_prefs(con, username, role)["kinds"].get(kind):
        return False
    cur = con.execute("INSERT OR IGNORE INTO notifications(username,kind,ref_type,ref_id,text,created_at,dedupe) VALUES(?,?,?,?,?,?,?)",
                      (username, kind, ref_type, str(ref_id), text[:240], now_iso(), dedupe))
    if cur.rowcount:
        con.execute("DELETE FROM notifications WHERE username=? AND id NOT IN (SELECT id FROM notifications WHERE username=? ORDER BY id DESC LIMIT ?)",
                    (username, username, NOTIF_MAX_PER_USER))
    return bool(cur.rowcount)


def _notif_users(con) -> list:
    return [dict(r) for r in con.execute("SELECT username,role,department_id FROM users WHERE active=1").fetchall()]


def notif_sees_dept(u: dict, dept_id: str) -> bool:
    """Bereichsrollen sehen nur den eigenen Bereich; alle anderen Rollen sehen alle Bereiche."""
    if u["role"] in DEPARTMENT_ROLES:
        return bool(dept_id) and str(u.get("department_id") or "") == dept_id
    return True


def _short(d) -> str:
    s = str(d or "")[:10]
    return f"{s[8:10]}.{s[5:7]}." if _valid_date_key(s) else ""


def _berlin_today() -> str:
    try:
        from release_gates import LOCAL_TZ
        return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d") if LOCAL_TZ else datetime.now().strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def default_dept_id(state: dict) -> str:
    """V12.16.0: Ersatz für den festen Rückgriff auf 'cnc': erster aktiver Produktionsbereich (Fallback 'cnc' nur ohne Bereiche).
    Bei der Bestandsdatenbank bleibt das 'cnc', weil der Bereich dort an erster Stelle steht."""
    for d in (state or {}).get("departments") or []:
        if isinstance(d, dict) and d.get("id") and d.get("active") is not False and str(d.get("kind") or "production") == "production":
            return str(d["id"])
    return "cnc"


def _step_dept(step: dict, machine_dept: dict, dd: str = "cnc") -> str:
    return str(step.get("departmentId") or machine_dept.get(str(step.get("machineId")), dd))


def notify_from_diff(con, old: dict, new: dict, actor: str) -> int:
    """Erzeugt Einträge aus Freigabe/Rücknahme, Maschinensperre, Abwesenheit und Leiharbeiter-Anfrage."""
    dd = default_dept_id(new)
    users = _notif_users(con)
    machine_dept = {str(m.get("id")): str(m.get("departmentId") or dd) for m in (new.get("machines") or []) if isinstance(m, dict)}
    machine_name = {str(m.get("id")): str(m.get("name") or m.get("id")) for m in (new.get("machines") or []) if isinstance(m, dict)}
    dept_name = {str(d.get("id")): str(d.get("name") or d.get("id")) for d in (new.get("departments") or []) if isinstance(d, dict)}
    made = 0

    def send(recipients, kind, ref_type, ref_id, text, dedupe=""):
        nonlocal made
        for u in recipients:
            if u["username"].casefold() != actor.casefold() and notif_add(con, u["username"], u["role"], kind, ref_type, ref_id, text, dedupe):
                made += 1

    old_steps = {str(s.get("id")): s for s in (old.get("workSteps") or []) if isinstance(s, dict)}
    active_by_machine: dict = {}
    for s in (new.get("workSteps") or []):
        if isinstance(s, dict) and str(s.get("status") or "planned") in _ACTIVE_STEP and str(s.get("planningType") or "MACHINE") == "MACHINE":
            active_by_machine[str(s.get("machineId"))] = active_by_machine.get(str(s.get("machineId")), 0) + 1
    for s in (new.get("workSteps") or []):
        if not isinstance(s, dict) or str(s.get("planningType") or "MACHINE") != "MACHINE":
            continue
        before = old_steps.get(str(s.get("id")))
        if not before:
            continue
        a, b = str(before.get("status") or "planned"), str(s.get("status") or "planned")
        if a == b or {a, b} != {"planned", "released"}:
            continue
        did = _step_dept(s, machine_dept, dd)
        label = str(s.get("fa") or s.get("order") or s.get("id"))
        text = f"{label} freigegeben" if b == "released" else f"{label} zurückgezogen"
        send([u for u in users if notif_sees_dept(u, did)], "release", "order", s.get("id"), f"{text} · {actor}")

    old_blocks = {str(x.get("id")) for x in (old.get("machineBlocks") or []) if isinstance(x, dict)}
    for blk in (new.get("machineBlocks") or []):
        if not isinstance(blk, dict) or str(blk.get("id")) in old_blocks:
            continue
        mid = str(blk.get("machineId"))
        n = active_by_machine.get(mid, 0)
        if not n:
            continue
        did = machine_dept.get(mid, dd)
        a, z = _short(blk.get("start")), _short(blk.get("end"))
        span = a + ("–" + z if z and z != a else "")
        send([u for u in users if notif_sees_dept(u, did)], "block", "machine", mid,
             f"Sperre {machine_name.get(mid, mid)} {span}: {n} {'Auftrag' if n == 1 else 'Aufträge'} betroffen", f"blk|{blk.get('id')}")

    old_abs = {(str(a.get("employeeId")), str(a.get("date"))) for a in (old.get("personnelAbsences") or []) if isinstance(a, dict)}
    emp = {str(e.get("id")): e for e in (new.get("employees") or []) if isinstance(e, dict)}
    fresh: dict = {}
    for a in (new.get("personnelAbsences") or []):
        if isinstance(a, dict) and (str(a.get("employeeId")), str(a.get("date"))) not in old_abs:
            fresh.setdefault(str(a.get("employeeId")), []).append(str(a.get("date")))
    for eid, days in fresh.items():
        e = emp.get(eid)
        if not e:
            continue
        did = str(e.get("departmentId") or "")
        open_steps = sum(c for m, c in active_by_machine.items() if machine_dept.get(m) == did)
        if not open_steps:
            continue
        days.sort()
        span = _short(days[0]) + ("–" + _short(days[-1]) if len(days) > 1 else "")
        # Kein Abwesenheitsgrund im Text (Datenschutz): nur Name, Zeitraum und Bereich.
        send([u for u in users if notif_sees_dept(u, did) and u["role"] != "viewer"], "block", "employee", eid,
             f"Abwesenheit {e.get('name') or eid} {span} · {dept_name.get(did, did)}: {open_steps} {'Auftrag' if open_steps == 1 else 'Aufträge'} offen", f"abs|{eid}|{days[0]}|{days[-1]}")

    old_emp = {str(e.get("id")): e for e in (old.get("employees") or []) if isinstance(e, dict)}
    for eid, e in emp.items():
        if str(e.get("employmentType")) != "temporary":
            continue
        before = old_emp.get(eid) or {}
        a = before.get("tempStatus") if before.get("employmentType") == "temporary" else None
        b = e.get("tempStatus")
        did = str(e.get("departmentId") or "")
        key = f"loan|{eid}|{b}|{e.get('tempFrom')}|{e.get('tempTo')}"
        span = f"{_short(e.get('tempFrom'))}–{_short(e.get('tempTo'))}"
        if b == "requested" and (a != "requested" or (before.get("tempFrom"), before.get("tempTo")) != (e.get("tempFrom"), e.get("tempTo"))):
            send([u for u in users if u["role"] == "gf"], "loan", "employee", eid, f"Leiharbeiter angefragt: {e.get('name') or eid} {span} · {dept_name.get(did, did)}", key)
        elif b in {"approved", "rejected"} and a != b:
            asker = str(e.get("tempBy") or "").casefold()
            targets = [u for u in users if u["username"].casefold() == asker] or [u for u in users if u["role"] in DEPARTMENT_ROLES and str(u.get("department_id") or "") == did]
            send(targets, "loan", "employee", eid, f"Leiharbeiter {'genehmigt' if b == 'approved' else 'abgelehnt'}: {e.get('name') or eid} {span}", key)
    return made


def notify_chat_message(con, channel, author: str, text: str) -> int:
    """Erwähnung (@) in jedem Kanal, jede Nachricht in einer Direktunterhaltung."""
    users = {u["username"].casefold(): u for u in _notif_users(con)}
    if channel["kind"] == "all":
        member_keys = set(users)
    else:
        member_keys = {str(x).casefold() for x in json.loads(channel["members"] or "[]")}
    mentioned = {m.group(2).casefold() for m in _MENTION_TOKEN.finditer(text) if m.group(1) == "u"}
    targets = set()
    for key in member_keys:
        if key == author.casefold() or key not in users:
            continue
        if key in mentioned or channel["kind"] == "direct":
            targets.add(key)
    preview = _MENTION_TOKEN.sub(lambda m: ("@" + m.group(2)) if m.group(1) == "u" else "/" + (m.group(3) or m.group(2)), text).replace("\n", " ")
    preview = preview[:90] + ("…" if len(preview) > 90 else "")
    made = 0
    for key in targets:
        u = users[key]
        body = f"{author}: {preview}" if channel["kind"] == "direct" else f"{author} erwähnt dich: {preview}"
        if notif_add(con, u["username"], u["role"], "mention", "chat", channel["id"], body):
            made += 1
    return made


def notif_get(con, user: dict, path: str, qs: dict) -> tuple[int, dict]:
    username, role = user["username"], user["role"]
    if path == "/api/notifications/prefs":
        return 200, {"prefs": notif_prefs(con, username, role)}
    if path == "/api/notifications":
        notif_purge(con)
        try:
            since = max(0, int(qs.get("since", ["0"])[0]))
        except ValueError:
            return 400, mp_error("MP-NOTIF-001", "Position ungültig.")
        rows = con.execute("SELECT * FROM notifications WHERE username=? AND id>? ORDER BY id DESC LIMIT ?", (username, since, NOTIF_LIST_LIMIT)).fetchall()
        return 200, {"items": [_notif_row(r) for r in rows], **notif_sig(con, username), "prefs": notif_prefs(con, username, role), "retentionDays": NOTIF_RETENTION_DAYS}
    return 404, mp_error("MP-REQ-404", "Nicht gefunden.")


def notif_post(con, user: dict, path: str, body: dict, state: dict | None = None) -> tuple[int, dict]:
    username, role = user["username"], user["role"]
    if path == "/api/notifications/read":
        if body.get("all"):
            con.execute("UPDATE notifications SET read_at=? WHERE username=? AND read_at IS NULL", (now_iso(), username))
        else:
            ids = body.get("ids")
            if not isinstance(ids, list) or len(ids) > 200 or any(not isinstance(i, int) or isinstance(i, bool) for i in ids):
                return 400, mp_error("MP-NOTIF-001", "Einträge ungültig.")
            con.executemany("UPDATE notifications SET read_at=? WHERE username=? AND id=? AND read_at IS NULL", [(now_iso(), username, i) for i in ids])
        return 200, {"ok": True, **notif_sig(con, username)}
    if path == "/api/notifications/derived":
        items = body.get("items")
        if not isinstance(items, list) or len(items) > NOTIF_MAX_DERIVED:
            return 400, mp_error("MP-NOTIF-001", f"Höchstens {NOTIF_MAX_DERIVED} Einträge je Meldung.")
        state = state or {}
        steps = {str(s.get("id")): s for s in (state.get("workSteps") or []) if isinstance(s, dict)}
        dd = default_dept_id(state)
        machine_dept = {str(m.get("id")): str(m.get("departmentId") or dd) for m in (state.get("machines") or []) if isinstance(m, dict)}
        today, made = _berlin_today(), 0
        me = {"username": username, "role": role, "department_id": user.get("department_id") or ""}
        for it in items:
            if not isinstance(it, dict) or it.get("kind") not in {"late", "tight"} or not _valid_date_key(it.get("end")):
                return 400, mp_error("MP-NOTIF-001", "Meldung ungültig.")
            s = steps.get(str(it.get("orderId")))
            # Nur echte, aktive Aufträge mit Termin und nur aus sichtbaren Bereichen; der Termin kommt vom Server.
            if not s or str(s.get("status") or "planned") not in _ACTIVE_STEP or not _valid_date_key(s.get("dueDate")) \
                    or not notif_sees_dept(me, _step_dept(s, machine_dept, dd)):
                continue
            label = str(s.get("fa") or s.get("order") or s.get("id"))
            text = f"{label}: Plan-Ende {_short(it['end'])} nach Termin {_short(s['dueDate'])}" if it["kind"] == "late" \
                else f"{label}: Puffer unter 1 Arbeitstag (Termin {_short(s['dueDate'])})"
            if notif_add(con, username, role, "risk", "order", s["id"], text, f"risk|{s['id']}|{today}"):
                made += 1
        return 200, {"created": made, **notif_sig(con, username)}
    return 404, mp_error("MP-REQ-404", "Nicht gefunden.")


def notif_put(con, user: dict, path: str, body: dict) -> tuple[int, dict]:
    if path == "/api/notifications/prefs":
        patch, err = notif_prefs_validate(body)
        if patch is None:
            return 400, mp_error("MP-NOTIF-002", err)
        return 200, {"prefs": notif_prefs_save(con, user["username"], user["role"], patch)}
    return 404, mp_error("MP-REQ-404", "Nicht gefunden.")


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
    if b.get("effort") is True:
        # V12.18.0 Dauer nach Besetzung: Sollstunden = Personenstunden = Summe(Dauer x Besetzung des Segments); Umruestsegmente tragen setup=true.
        work_eff = setup_wall = 0.0
        for seg in b.get("segments") or []:
            pr = _ordered_dt_pair(seg.get("start"), seg.get("end"))
            wall = (pr[1] - pr[0]).total_seconds() / 3600.0
            if seg.get("setup") is True:
                setup_wall += wall
                continue
            cr = _finite_float(seg.get("crew"))
            if cr is None or cr < 1 or not cr.is_integer() or cr > 99:
                return False, "Freigabeplan enthält ein Segment ohne gültige Besetzung."
            work_eff += wall * cr
        if expected_hours is None or abs(work_eff - expected_hours) > 0.01 or abs(setup_wall - frozen_setup / 60.0) > 0.01:
            return False, "Freigabeplan-Dauer stimmt nicht mit den Personenstunden und der Besetzung je Schicht überein."
    else:
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
    breaks = a.get("breaks") or []
    if not isinstance(breaks, list) or len(breaks) > 2:
        return False, "MP-PERS-022", "Mitarbeiter-Pausen müssen eine Liste mit höchstens zwei Pausen sein."
    ranges = []
    for b in breaks:
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


TEMP_STATUS = {"requested", "approved", "rejected"}
TEMP_DECISION_FIELDS = {"tempStatus", "tempDecidedBy", "tempNote"}


def _temp_request_change(a: dict | None, b: dict | None, role: str) -> tuple[bool, str]:
    """Leiharbeiter-Anfragen: die Abteilung fragt an (Zeitraum), die GF entscheidet."""
    if b is None:
        return True, ""
    name = str(b.get("name") or b.get("id"))
    if role == "gf":
        if a is None:
            return False, "GF legt keine Mitarbeiter an."
        diff = {k for k in set(a) | set(b) if canonical(a.get(k)) != canonical(b.get(k))}
        if diff - TEMP_DECISION_FIELDS or str(b.get("employmentType")) != "temporary" or b.get("tempStatus") not in {"approved", "rejected"}:
            return False, f"GF entscheidet bei '{name}' nur über die Leiharbeiter-Anfrage."
        return True, ""
    # Abteilung
    was_temp = a is not None and str(a.get("employmentType")) == "temporary"
    if was_temp and str(b.get("employmentType")) != "temporary":
        return False, f"'{name}': Leiharbeiter in Festangestellte umwandeln darf nur der Admin."
    if str(b.get("employmentType")) != "temporary":
        return True, ""
    old_status = (a or {}).get("tempStatus") if was_temp else None
    new_status = b.get("tempStatus")
    if a is None or not was_temp:
        if new_status != "requested":
            return False, f"'{name}': Neue Leiharbeiter werden als Anfrage angelegt (GF genehmigt)."
        return True, ""
    period_changed = (a.get("tempFrom"), a.get("tempTo")) != (b.get("tempFrom"), b.get("tempTo"))
    if new_status != old_status:
        if new_status != "requested" or not period_changed:
            return False, f"'{name}': Genehmigen/Ablehnen ist Sache der GF."
    elif old_status == "approved" and period_changed:
        return False, f"'{name}': Geänderter Zeitraum muss neu angefragt werden."
    for f in ("tempDecidedBy", "tempNote"):
        if a.get(f) != b.get(f) and b.get(f):
            return False, f"'{name}': GF-Entscheidung darf die Abteilung nicht ändern."
    return True, ""


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
        if "quantity" in project and not _nonnegative_int(project["quantity"]):
            return False, "MP-PM-002", f"Projekt '{label}': Menge muss eine ganze nicht negative Zahl sein."
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
        if "productionOnly" in project and not isinstance(project.get("productionOnly"), bool):
            return False, "MP-PM-002", f"Projekt '{label}': „Nur Produktion“ ist ungültig."
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
                return False, "MP-PM-010", f"Projekt '{label}': Prozess verweist auf eine ungültige FA."
            if pr.get("dueDate") not in (None, "") and not _valid_date_key(pr.get("dueDate")):
                return False, "MP-PM-010", f"Projekt '{label}': Prozess hat ein ungültiges Fälligkeitsdatum."
            if pr.get("startDate") not in (None, "") and not _valid_date_key(pr.get("startDate")):
                return False, "MP-PM-010", f"Projekt '{label}': Prozess hat ein ungültiges Startdatum."
            if _valid_date_key(pr.get("startDate")) and _valid_date_key(pr.get("dueDate")) and str(pr["startDate"]) > str(pr["dueDate"]):
                return False, "MP-PM-016", f"Projekt '{label}': Prozess „{pr.get('title')}“ startet nach seiner Fälligkeit."
            pm_plan = pr.get("pmPlan")
            if pm_plan is not None and (not isinstance(pm_plan, dict) or set(pm_plan) - {"startDate", "dueDate", "at", "by"}
                                        or any(pm_plan.get(k) not in (None, "") and not _valid_date_key(pm_plan.get(k)) for k in ("startDate", "dueDate"))
                                        or len(str(pm_plan.get("by") or "")) > 80 or len(str(pm_plan.get("at") or "")) > 40):
                return False, "MP-PM-017", f"Projekt '{label}': PM-Vorplan von „{pr.get('title')}“ ist ungültig."
        cp = project.get("customerPlan")
        if cp is not None:
            # Kundenplan = an den Kunden gegebener PM-Terminplan (Stand), Vergleichsbasis für die AV.
            steps = cp.get("steps") if isinstance(cp, dict) else None
            version = cp.get("version") if isinstance(cp, dict) else None
            if (not isinstance(steps, list) or len(steps) > 300 or isinstance(version, bool) or not isinstance(version, int) or version < 1
                    or (cp.get("dueDate") not in (None, "") and not _valid_date_key(cp.get("dueDate")))):
                return False, "MP-PM-040", f"Projekt '{label}': Kundenplan ist ungültig."
            for st in steps:
                if (not isinstance(st, dict) or not str(st.get("areaId") or "").strip() or len(str(st.get("title") or "")) > 200
                        or not _valid_date_key(st.get("startDate")) or not _valid_date_key(st.get("dueDate"))):
                    return False, "MP-PM-040", f"Projekt '{label}': Kundenplan enthält einen ungültigen Termin."
        log = project.get("log") or []
        if not isinstance(log, list) or any(not isinstance(x, dict) or not x.get("id") for x in log):
            return False, "MP-PM-011", f"Projekt '{label}': Verlauf ist ungültig."
        if len({str(x["id"]) for x in log}) != len(log):
            return False, "MP-PM-011", f"Projekt '{label}': Verlauf benötigt eindeutige IDs."
    return True, "", ""


# --------------------------------------------------------------------------- Tiefziehen: Formate
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
FORMAT_STATUS = {"active", "stored"}


def _num_in(v, lo, hi, integer=False):
    x = _finite_float(v)
    return x is not None and lo <= x <= hi and (not integer or x.is_integer())


def _validate_machine_format_fields(m: dict) -> tuple[bool, str]:
    """Tiefziehen: Takte je Maschine (Name + Sekunden) und maximale Formatgröße/Ziehtiefe."""
    for f in ("maxL", "maxB", "maxH"):
        if m.get(f) not in (None, "") and not _num_in(m.get(f), 0, 10000):
            return False, f"{f} ist ungültig."
    takte = m.get("takte")
    if takte in (None, ""):
        return True, ""
    if not isinstance(takte, list) or len(takte) > 30:
        return False, "Taktliste ist ungültig (max. 30)."
    seen = set()
    for t in takte:
        if not isinstance(t, dict) or not _SAFE_ID.fullmatch(str(t.get("id") or "")) or str(t.get("id")) in seen:
            return False, "Takt benötigt eine eindeutige ID."
        seen.add(str(t.get("id")))
        if not str(t.get("name") or "").strip() or len(str(t.get("name"))) > 40:
            return False, "Taktname fehlt oder ist zu lang."
        if not _num_in(t.get("sec"), 1, 3600):
            return False, f"Takt „{t.get('name')}“: Sekunden müssen zwischen 1 und 3600 liegen."
    return True, ""


def validate_formats(old: dict, new: dict, dept_ids: set) -> tuple[bool, str, str]:
    """Grundformate und Formate (Tiefziehen)."""
    dd = default_dept_id(new)
    base = new.get("baseFormats")
    if base in (None, ""):
        base = []
    if not isinstance(base, list) or len(base) > 200:
        return False, "MP-FMT-001", "Grundformate sind ungültig."
    base_ids = set()
    for g in base:
        if not isinstance(g, dict) or not _SAFE_ID.fullmatch(str(g.get("id") or "")) or str(g.get("id")) in base_ids:
            return False, "MP-FMT-001", "Grundformat benötigt eine eindeutige ID."
        base_ids.add(str(g.get("id")))
        if str(g.get("departmentId") or "") not in dept_ids:
            return False, "MP-FMT-001", f"Grundformat '{g.get('name')}' verweist auf einen unbekannten Bereich."
        if not str(g.get("name") or "").strip() or len(str(g.get("name"))) > 60:
            return False, "MP-FMT-001", "Grundformat: Name fehlt oder ist zu lang."
        if not _num_in(g.get("L"), 100, 5000, True) or not _num_in(g.get("B"), 100, 5000, True):
            return False, "MP-FMT-001", f"Grundformat '{g.get('name')}': Länge/Breite 100–5000 mm."
        # V12.10.0: Grundformat für mehrere Maschinen; leer/fehlend = alle Maschinen des Bereichs
        mids = g.get("machineIds")
        if mids is not None:
            dep_machines = {str(m.get("id")) for m in (new.get("machines") or []) if isinstance(m, dict) and str(m.get("departmentId") or dd) == str(g.get("departmentId"))}
            if not isinstance(mids, list) or not mids or len(mids) > 50 or len(set(map(str, mids))) != len(mids) or any(str(x) not in dep_machines for x in mids):
                return False, "MP-FMT-011", f"Grundformat '{g.get('name')}': Maschinenauswahl ungültig (nur Maschinen des Bereichs)."
    formats = new.get("formats")
    if formats in (None, ""):
        formats = []
    if not isinstance(formats, list) or len(formats) > 5000:
        return False, "MP-FMT-002", "Formatliste ist ungültig."
    machine_dept = {str(m.get("id")): str(m.get("departmentId") or dd) for m in (new.get("machines") or []) if isinstance(m, dict)}
    step_ids = {str(x.get("id")) for x in (new.get("workSteps") or []) if isinstance(x, dict)}
    # Fertige Aufträge wandern in die Historie; das Format behält die Verknüpfung (originalOrderId).
    step_ids |= {str(h.get("originalOrderId")) for h in (new.get("history") or []) if isinstance(h, dict) and h.get("originalOrderId")}
    old_links = {str(f.get("id")): str(f.get("workStepId") or "") for f in (old.get("formats") or []) if isinstance(f, dict)}
    ids, numbers = set(), set()
    for f in formats:
        if not isinstance(f, dict) or not _SAFE_ID.fullmatch(str(f.get("id") or "")) or str(f.get("id")) in ids:
            return False, "MP-FMT-002", "Format benötigt eine eindeutige ID."
        ids.add(str(f.get("id")))
        number = str(f.get("number") or "").strip()
        label = number or str(f.get("id"))
        if not number or len(number) > 20 or number.casefold() in numbers:
            return False, "MP-FMT-003", f"Format '{label}': Formatnummer fehlt oder ist doppelt."
        numbers.add(number.casefold())
        did = str(f.get("departmentId") or "")
        if did not in dept_ids:
            return False, "MP-FMT-002", f"Format '{label}' verweist auf einen unbekannten Bereich."
        if len(str(f.get("name") or "")) > 120:
            return False, "MP-FMT-002", f"Format '{label}': Name ist zu lang."
        status = str(f.get("status") or "active")
        if status not in FORMAT_STATUS:
            return False, "MP-FMT-002", f"Format '{label}' hat einen ungültigen Status."
        if f.get("baseId") not in (None, "") and str(f.get("baseId")) not in base_ids:
            return False, "MP-FMT-004", f"Format '{label}' verweist auf ein unbekanntes Grundformat."
        mid = str(f.get("machineId") or "")
        if mid and machine_dept.get(mid) != did:
            return False, "MP-FMT-005", f"Format '{label}': Maschine gehört nicht zum Bereich."
        if not _num_in(f.get("L"), 100, 5000, True) or not _num_in(f.get("B"), 100, 5000, True):
            return False, "MP-FMT-002", f"Format '{label}': Länge/Breite 100–5000 mm."
        if not _num_in(f.get("H", 0), 0, 2000) or not _num_in(f.get("rand", 0), 0, 1000):
            return False, "MP-FMT-002", f"Format '{label}': Ziehtiefe/Rand ungültig."
        tools = f.get("tools") or []
        if not isinstance(tools, list) or len(tools) > 60:
            return False, "MP-FMT-006", f"Format '{label}': Werkzeugliste ist ungültig (max. 60)."
        tool_ids = set()
        for t in tools:
            if not isinstance(t, dict) or not _SAFE_ID.fullmatch(str(t.get("id") or "")) or str(t.get("id")) in tool_ids:
                return False, "MP-FMT-006", f"Format '{label}': Werkzeug ohne eindeutige ID."
            tool_ids.add(str(t.get("id")))
            for k, lim in (("wkz", 40), ("fa", 40), ("order", 80), ("article", 120), ("stepId", 80), ("projectId", 80)):
                if len(str(t.get(k) or "")) > lim:
                    return False, "MP-FMT-006", f"Format '{label}': Feld '{k}' ist zu lang."
            if not all(_num_in(t.get(k), 1, 5000) for k in ("l", "b", "h")):
                return False, "MP-FMT-006", f"Format '{label}': Werkzeugmaße 1–5000 mm."
            if not _num_in(t.get("n"), 1, 500, True) or not _num_in(t.get("qty", 0), 0, 10_000_000, True):
                return False, "MP-FMT-006", f"Format '{label}': Nutzen (1–500) bzw. Menge ungültig."
        layout = f.get("layout")
        if layout is not None and (not isinstance(layout, dict) or len(layout) > 5000):
            return False, "MP-FMT-007", f"Format '{label}': Layout ist ungültig."
        lager = f.get("lager")
        if status == "stored":
            if not isinstance(lager, dict) or not str(lager.get("ort") or "").strip() or len(str(lager.get("ort"))) > 120:
                return False, "MP-FMT-008", f"Format '{label}': Eingelagert ohne Lagerort."
        elif lager not in (None, ""):
            return False, "MP-FMT-008", f"Format '{label}': Lagerort nur bei eingelagerten Formaten."
        log = f.get("log") or []
        if not isinstance(log, list) or len(log) > 500 or any(not isinstance(x, dict) for x in log):
            return False, "MP-FMT-009", f"Format '{label}': Verlauf ist ungültig."
        wsid = str(f.get("workStepId") or "")
        # V12.10.2 (N8): neue Verknüpfung muss auf einen vorhandenen Auftrag zeigen. Bestehende
        # Verknüpfungen auf gelöschte Aufträge bleiben erlaubt, sonst blockiert ein Altbestand jedes Speichern.
        if wsid and (len(wsid) > 80 or (wsid not in step_ids and wsid != old_links.get(str(f.get("id")), ""))):
            return False, "MP-FMT-010", f"Format '{label}': Verknüpfung zum Auftrag ist ungültig."
    return True, "", ""


TEMPLATE_KINDS = {"pm", "av"}
# V12.8.2: Bereichsarten. Produktion plant über Maschinen/Linien, Vertrieb/Entwicklung nur Projektaufgaben.
DEPARTMENT_KINDS = {"production", "sales", "development"}
PROJECT_FIXED_AREA_IDS = {"sales", "pm", "engineering", "calculation", "purchasing", "quality", "av"}


def production_department_ids(state: dict) -> set:
    return {str(d.get("id")) for d in (state.get("departments") or []) if isinstance(d, dict) and str(d.get("kind") or "production") == "production"}


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
    dd = default_dept_id(new)
    projects = new.get("projects") or []
    if not isinstance(machines, list) or not machines:
        return False, "MP-DATA-011", "Mindestens eine Maschine ist erforderlich."
    if not isinstance(work_steps, list):
        return False, "MP-STEP-001", "Arbeitsgangliste fehlt."
    if not isinstance(departments, list) or not departments:
        return False, "MP-DEPT-001", "Produktionsbereiche fehlen."
    # V12.10.2 (N1): Wurzelfelder ohne Fachprüfung zumindest typisieren (sonst TypeError beim Speichern).
    for key, typ, what in (("meta", dict, "Objekt"), ("ui", dict, "Objekt"), ("planVersions", list, "Liste")):
        if new.get(key) is not None and not isinstance(new.get(key), typ):
            return False, "MP-DATA-014", f"Feld '{key}' muss ein {what} sein."
    if any(not isinstance(v, dict) for v in new.get("planVersions") or []):
        return False, "MP-DATA-014", "Planversionen sind ungültig."

    dept_ids = set()
    dept_types = {}
    dept_kinds = {}
    dept_labels = {}
    valid_types = {"MACHINE", "LABOR_HOURS", "PROCESS", "CYCLE"}
    for dep in departments:
        if not isinstance(dep, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(dep.get("id") or "")):
            return False, "MP-DEPT-002", "Produktionsbereich hat eine ungültige ID."
        did = str(dep["id"])
        ptype = str(dep.get("planningType") or "")
        if any(k in dep and not isinstance(dep[k], bool) for k in ("formats", "sharedOperators")):
            return False, "MP-DEPT-007", f"Bereich '{did}': Eigenschaften formats/sharedOperators sind true/false."
        if "dryingHours" in dep and (_finite_float(dep["dryingHours"]) is None or not 0 <= float(dep["dryingHours"]) <= 720 or isinstance(dep["dryingHours"], bool)):
            return False, "MP-DEPT-007", f"Bereich '{did}': Trocknungszeit muss 0–720 Stunden sein."
        if did in dept_ids:
            return False, "MP-DEPT-002", f"Doppelte Bereichs-ID '{did}'."
        if ptype not in valid_types:
            return False, "MP-DEPT-003", f"Bereich '{did}' hat einen ungültigen Planungstyp."
        kind = str(dep.get("kind") or "production")
        if kind not in DEPARTMENT_KINDS:
            return False, "MP-DEPT-004", f"Bereich '{did}' hat eine ungültige Art."
        name = str(dep.get("name") or "").strip()
        if not name or len(name) > 60:
            return False, "MP-DEPT-005", f"Bereich '{did}': Name fehlt oder ist zu lang (max. 60)."
        if did in PROJECT_FIXED_AREA_IDS:
            return False, "MP-DEPT-005", f"Bereichs-ID '{did}' ist für feste Projektbereiche reserviert."
        dept_ids.add(did)
        dept_types[did] = ptype
        dept_kinds[did] = kind
        dept_labels[did] = name

    # V12.8.2: Vertrieb/Entwicklung bearbeiten nur Projektaufgaben – keine Maschinen, Aufträge, Formate.
    for name, what in (("machines", "Maschine/Linie"), ("workSteps", "Auftrag"), ("formats", "Format"), ("baseFormats", "Grundformat")):
        for rec in new.get(name) or []:
            if isinstance(rec, dict) and dept_kinds.get(str(rec.get("departmentId") or dd), "production") != "production":
                return False, "MP-DEPT-004", f"Bereich '{rec.get('departmentId')}' ist kein Produktionsbereich – {what} nicht zulässig."
    # Removing a department must not orphan its productive records, even if the
    # client also removes those records in the same request. Empty setup resources
    # can still be replaced; current resource references are checked below.
    removed_depts = {str(d.get("id")) for d in old.get("departments") or [] if isinstance(d, dict)} - dept_ids
    for did in removed_depts:
        linked = any(isinstance(x, dict) and str(x.get("departmentId") or "") == did
                     for state in (old, new)
                     for key in ("workSteps", "history", "productionEvents", "palletLabels", "inventory", "formats", "baseFormats")
                     for x in state.get(key) or [])
        linked = linked or any(isinstance(proc, dict) and str(proc.get("areaId") or "") == did
                               for state in (old, new) for project in state.get("projects") or []
                               if isinstance(project, dict) for proc in project.get("processes") or [])
        if linked:
            return False, "MP-DEPT-006", f"Bereich '{did}' hat verknüpfte Produktionsdaten. Bitte deaktivieren statt löschen."

    # Deactivation is additive and keeps every linked order/resource/history record intact.

    if not isinstance(projects, list):
        return False, "MP-PM-001", "Projekt-/Auftragsstamm ist ungültig."
    ok, code, reason = validate_projects(old, projects)
    if not ok:
        return False, code, reason
    ok, code, reason = validate_process_templates(new.get("processTemplates"))
    if not ok:
        return False, code, reason
    ok, code, reason = validate_formats(old, new, dept_ids)
    if not ok:
        return False, code, reason
    templates = new.get("palletTemplates", [])
    if not isinstance(templates, list):
        return False, "MP-PROD-048", "Etikettenvorlagen sind ungültig."
    tids = set()
    for t in templates:
        if not isinstance(t, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(t.get("id") or "")) or t["id"] in tids or not _nonnegative_int(t.get("shelfLifeDays", 0)) or t.get("shelfLifeDays", 0) > 36500 or any(not isinstance(t.get(k, ""), str) or len(t.get(k, "")) > 1000 for k in ("name", "fromAddress", "toAddress")):
            return False, "MP-PROD-048", "Etikettenvorlage ist ungültig."
        tids.add(t["id"])
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
        fa = str(step.get("fa") or step.get("order") or "").strip()
        ab_ref = str(step.get("ab") or "").strip()
        wt_ref = str(step.get("wt") or "").strip()
        if not fa:
            return False, "MP-STEP-014", f"Produktionsauftrag '{sid}' benötigt eine FA."
        if str(step.get("status") or "planned") not in {"planned", "released", "running", "paused", "done", "cancelled"}:
            return False, "MP-STEP-017", f"Arbeitsgang '{sid}' hat einen ungültigen Status."
        if any(len(v) > 80 for v in (fa, ab_ref, wt_ref)):
            return False, "MP-STEP-014", f"Arbeitsgang '{sid}': FA/AB/WT ist zu lang."
        if "avNote" in step and (not isinstance(step["avNote"], str) or len(step["avNote"]) > 500):
            return False, "MP-STEP-014", f"Arbeitsgang '{sid}': AV-Notiz ist ungültig oder zu lang."
        if "dryingHours" in step and (isinstance(step["dryingHours"], bool) or _finite_float(step["dryingHours"]) is None or not 0 <= float(step["dryingHours"]) <= 720):
            return False, "MP-STEP-014", f"Arbeitsgang '{sid}': Trocknungszeit muss 0–720 Stunden sein."
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

        if str(step.get("status")) in {"released", "running"}:
            completed = {str(h.get("originalOrderId")) for h in new.get("history") or [] if h.get("recordType", "done") == "done"}
            completed |= {str(x.get("id")) for x in work_steps if x.get("status") == "done"}
            if any(str(i) not in completed for i in step.get("predecessorIds") or []):
                return False, "MP-PROD-046", "Vorgänger offen."
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
            if ptype == "LABOR_HOURS" and str(step.get("status") or "planned") != "planned" and _finite_float(step.get("requiredHours", 0)) <= 0:
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

    by_id = {str(x.get("originalOrderId")): x for x in new.get("history") or [] if x.get("recordType", "done") == "done"}
    by_id.update({str(x.get("id")): x for x in work_steps})
    for step in work_steps:
        for predecessor_id in (step.get("predecessorIds") or []):
            predecessor = by_id.get(str(predecessor_id))
            if not predecessor or str(predecessor.get("projectId")) != str(step.get("projectId")):
                return False, "MP-STEP-007", f"Arbeitsgang '{step.get('id')}' verweist auf einen ungültigen Vorgänger."

    graph = {sid: [str(x) for x in (step.get("predecessorIds") or [])] for sid, step in by_id.items() if sid in step_ids}
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

    for step in work_steps:
        source = step.get("sourceType", "PROJECT")
        if source not in FA_SOURCE_TYPES or not isinstance(step.get("sourceId", ""), str) or ("sourceId" in step and not step["sourceId"]):
            return False, "MP-FA-001", "FA-Herkunft ist ungültig."
        if step.get("faNumber") is not None and step.get("faNumber") != step.get("fa"):
            return False, "MP-FA-001", "FA-Nummer und Referenz sind inkonsistent."
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
        if str(m.get("departmentId") or dd) not in dept_ids:
            return False, "MP-MACH-008", f"Maschine '{m.get('name') or mid}' verweist auf einen unbekannten Bereich."
        setup = _finite_float(m.get("setupMinutes", 0))
        if setup is None or setup < 0 or setup > 1440:
            return False, "MP-MACH-009", f"Umrüstzeit von Maschine '{m.get('name') or mid}' ist ungültig."
        cleanup = _finite_float(m.get("cleanupMinutes", 0))
        if cleanup is None or cleanup < 0 or cleanup > 1440 or not cleanup.is_integer():
            return False, "MP-MACH-018", f"Reinigungszeit von Maschine '{m.get('name') or mid}' ist ungültig (0–24 h)."
        ok, reason = _validate_machine_format_fields(m)
        if not ok:
            return False, "MP-MACH-014", f"Maschine '{m.get('name') or mid}': {reason}"
        crew = _finite_float(m.get("crew", 1))
        if crew is None or crew < 1 or not crew.is_integer() or crew > 99:
            return False, "MP-MACH-010", f"Besetzung von '{m.get('name') or mid}' muss eine ganze Zahl von 1 bis 99 sein."
        if m.get("effortScaling") not in (None, True, False):
            return False, "MP-MACH-017", f"'Dauer nach Besetzung' von '{m.get('name') or mid}' muss ein / aus sein."
        if m.get("crewMax") not in (None, "") and (isinstance(m.get("crewMax"), bool) or not _num_in(m.get("crewMax"), 0, 99, True)):
            return False, "MP-MACH-018", f"Maximale Besetzung von '{m.get('name') or mid}' muss eine ganze Zahl von 0 bis 99 sein."
        lane_staff = m.get("laneStaff")
        if lane_staff not in (None, ""):
            if not isinstance(lane_staff, dict) or any(
                    not re.fullmatch(r"[1-9]|1[0-9]|20", str(k)) or isinstance(v, bool) or not _num_in(v, 0, 9, True) for k, v in lane_staff.items()):
                return False, "MP-MACH-019", f"Personal je Parallelplatz von '{m.get('name') or mid}' muss Platz 1-20 mit ganzen Zahlen 0-9 enthalten."
        lanes = m.get("lanes")
        if lanes not in (None, "") and not _num_in(lanes, 1, 20, True):
            return False, "MP-MACH-015", f"Parallelplätze von '{m.get('name') or mid}' müssen eine ganze Zahl von 1 bis 20 sein."
        if str(m.get("kind") or "machine") not in {"machine", "line", "workplace"}:
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

    ci = new.get("ci")
    if ci is not None:
        if not isinstance(ci, dict):
            return False, "MP-CI-001", "Firmen-CI ist ungültig."
        if any(len(str(ci.get(f) or "")) > 300 for f in ("company", "address", "footer")) or len(str(ci.get("font") or "")) > 60:
            return False, "MP-CI-001", "Firmen-CI: Text ist zu lang."
        if ci.get("color") not in (None, "") and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(ci.get("color"))):
            return False, "MP-CI-001", "Firmen-CI: Farbe muss #RRGGBB sein."
        logo = ci.get("logo") or ""
        if logo and (not isinstance(logo, str) or len(logo) > 420_000 or not re.match(r"data:image/(png|jpeg);base64,[A-Za-z0-9+/=]+$", logo)):
            return False, "MP-CI-002", "Firmen-CI: Logo muss PNG/JPG (max. 300 KB) sein."

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
        if "homeLaneIndex" in e and (not hm or isinstance(e["homeLaneIndex"], bool) or not isinstance(e["homeLaneIndex"], int) or not 1 <= e["homeLaneIndex"] <= machine_lanes(new, hm)):
            return False, "MP-PERS-034", "Stamm-Parallelplatz ist ungültig."
        if hm and (hm not in midset or hm not in {str(x) for x in skills}):
            return False, "MP-PERS-025", f"Stammmaschine von '{e.get('name') or eid}' ist nicht freigegeben."
        if str(e.get("homeShift", "auto")) not in {"auto", "early", "late"}:
            return False, "MP-PERS-001", f"Stammschicht von '{e.get('name') or eid}' ist ungültig."
        if str(e.get("employmentType", "permanent")) not in {"permanent", "temporary"}:
            return False, "MP-PERS-028", f"Personaltyp von '{e.get('name') or eid}' ist ungültig."
        if e.get("tempStatus") is not None:
            if (e.get("tempStatus") not in TEMP_STATUS or not _valid_date_key(e.get("tempFrom")) or not _valid_date_key(e.get("tempTo"))
                    or str(e["tempFrom"]) > str(e["tempTo"]) or len(str(e.get("tempNote") or "")) > 200):
                return False, "MP-PERS-033", f"Leiharbeiter-Anfrage von '{e.get('name') or eid}' ist ungültig (Zeitraum/Status)."
        if str(e.get("departmentId") or "") not in dept_ids:
            return False, "MP-PERS-029", f"Mitarbeiter '{e.get('name') or eid}' verweist auf einen unbekannten Bereich."
        custom_times = e.get("standardPersonnelTimes")
        if custom_times is not None:
            if (not isinstance(custom_times, dict) or set(custom_times) != {"single", "fridaySingle"}
                    or any(not isinstance(custom_times.get(k), dict)
                           or not isinstance(custom_times[k].get("breaks", []), list)
                           or len(custom_times[k].get("breaks", [])) > 2
                           or not _template_valid(custom_times.get(k)) for k in ("single", "fridaySingle"))):
                return False, "MP-PERS-035", f"Dauerhafte Mitarbeiterzeiten von '{e.get('name') or eid}' sind ungültig."
        days = e.get("workingDays", [1, 2, 3, 4, 5])
        daily = e.get("dailyHours", {})
        if (not isinstance(days, list) or not days or len(days) != len(set(map(str, days)))
                or any(isinstance(d, bool) or not isinstance(d, int) or d not in range(1, 8) for d in days)
                or not isinstance(daily, dict) or any(str(k) not in set(map(str, days)) or _finite_float(v) is None or not 0 <= float(v) <= 16 for k, v in daily.items())):
            return False, "MP-PERS-035", "Individuelle Arbeitstage/-stunden sind ungültig."
        weekly = _finite_float(e.get("weeklyHours", 40))
        if weekly is None or weekly < 0 or weekly > 80:
            return False, "MP-PERS-030", f"Wochenstunden von '{e.get('name') or eid}' sind ungültig."
        if sum(float(v) for v in daily.values()) > weekly+0.001:
            return False, "MP-PERS-035", "Tagesstunden überschreiten die Wochenstunden."
    if len(eids) != len(set(eids)):
        return False, "MP-PERS-001", "Doppelte Mitarbeiter-ID."
    eidset = set(eids)

    assignment_keys = set()
    machine_dept_new = {str(m.get("id")): str(m.get("departmentId") or dd) for m in (new.get("machines") or []) if isinstance(m, dict)}
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
        if employee and not unchanged and mid not in {str(x) for x in (employee.get("skills") or [])}:
            return False, "MP-PERS-025", f"Mitarbeiter '{employee.get('name') or eid}' ist für Maschine '{mid}' nicht freigegeben."
        if employee and not unchanged:
            effective = deployed or str(employee.get("departmentId") or "")
            if machine_dept_new.get(mid) != effective:
                return False, "MP-PERS-034", "Mitarbeiter ist nicht im Einsatzbereich der Ressource."
        lane = a.get("laneIndex", 1)
        if isinstance(lane, bool) or not isinstance(lane, int) or not 1 <= lane <= machine_lanes(new, mid):
            return False, "MP-PERS-034", "Parallelplatz der Personalzuordnung ist ungültig."
        key = (eid, str(date))
        if key in assignment_keys:
            return False, "MP-PERS-001", f"Mitarbeiter '{eid}' hat am {date} mehrere explizite Zuordnungen."
        assignment_keys.add(key)
        ok, code, reason = _personnel_assignment_valid(a)
        if not ok:
            return False, code, reason

    # Explicit hours cannot exceed the contractual weekly capacity (breaks excluded).
    weekly_assigned = {}
    for a in assignments:
        key = (str(a["employeeId"]), _week_start_key(a["date"]))
        minutes = _clock_minutes(a["end"]) - _clock_minutes(a["start"])
        minutes -= sum((_clock_minutes(b.get("end")) or 0) - (_clock_minutes(b.get("start")) or 0) for b in a.get("breaks") or [] if b.get("start") and b.get("end"))
        weekly_assigned[key] = weekly_assigned.get(key, 0) + minutes / 60
    for (eid, week), hours in weekly_assigned.items():
        employee = next(e for e in employees if str(e["id"]) == eid)
        changed = any(str(a["employeeId"]) == eid and _week_start_key(a["date"]) == week and canonical(old_assignments.get((eid, a["date"]))) != canonical(a) for a in assignments)
        if changed and hours > float(employee.get("weeklyHours", 40)) + 0.001:
            return False, "MP-PERS-035", "Personalplanung überschreitet die individuellen Wochenstunden."

    absence_keys = set()
    absences = new.get("personnelAbsences") or []
    if not isinstance(absences, list):
        return False, "MP-PERS-001", "Abwesenheitsliste ist ungültig."
    for a in absences:
        if not isinstance(a, dict) or str(a.get("employeeId", "")) not in eidset or not _valid_date_key(a.get("date")) or len(str(a.get("label") or "")) > 40:
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
    locked_by_machine: dict[str, set] = {}
    old_orders = {str(o.get("id")): o for o in (old.get("workSteps") or []) if isinstance(o, dict) and o.get("id") and o.get("planningType") == "MACHINE"}
    planning_fields = (
        "projectId", "fa", "faNumber", "sourceType", "sourceId", "frameOrderId", "callOffId", "stockRequirementId", "ab", "wt", "sequence", "departmentId", "planningType", "predecessorIds", "pos", "machineId", "altMachineId", "allowAlternative", "order", "articleNo", "description",
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
        handoff = o.get("handoffUnassigned", False)
        ptype = str(o.get("planningType") or "")
        if "handoffUnassigned" in o and not isinstance(handoff, bool):
            return False, "MP-PLAN-065", f"Auftrag '{o.get('order') or oid}': Bereichsübergabe ist ungültig."
        if handoff and (ptype != "MACHINE" or str(o.get("status", "planned")) != "planned" or mid or alt or o.get("allowAlternative") or o.get("baselinePlan") is not None or o.get("anchorMode", "none") != "none" or o.get("direction", "forward") != "forward"):
            return False, "MP-PLAN-065", f"Auftrag '{o.get('order') or oid}': Unzugeordnete Bereichsübergabe darf keine Ressource oder operative Planung enthalten."
        if not handoff and mid not in midset:
            return False, "MP-PLAN-003", f"Auftrag '{o.get('order') or oid}' verweist auf eine unbekannte Hauptmaschine."
        order_department = str(o.get("departmentId") or next((m.get("departmentId") or dd for m in machines if str(m.get("id")) == mid), dd))
        if order_department not in dept_ids:
            return False, "MP-DEPT-004", f"Auftrag '{o.get('order') or oid}' verweist auf einen unbekannten Bereich."
        for used_mid in (mid, alt):
            if used_mid and str((machine_map.get(used_mid) or {}).get("departmentId") or dd) != order_department:
                return False, "MP-DEPT-005", f"Auftrag '{o.get('order') or oid}' ist einer Maschine aus einem anderen Bereich zugeordnet."
        project_id = str(o.get("projectId") or "")
        if project_id and project_id not in project_ids:
            return False, "MP-PM-004", f"Auftrag '{o.get('order') or oid}' verweist auf einen unbekannten AB-Auftrag."
        if alt and (alt not in midset or alt == mid):
            return False, "MP-PLAN-033", f"Auftrag '{o.get('order') or oid}': Alternative Maschine muss existieren und von der Hauptmaschine abweichen."
        if bool(o.get("allowAlternative")) != bool(alt):
            return False, "MP-PLAN-033", f"Auftrag '{o.get('order') or oid}': Alternativmaschinen-Schalter und Alternativmaschine sind inkonsistent."

        lane = o.get("laneIndex")
        if lane is not None and (isinstance(lane, bool) or not isinstance(lane, int) or not 1 <= lane <= machine_lanes(new, mid)):
            return False, "MP-PERS-034", "FA-Parallelplatz ist ungültig."
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
                if baseline.get("effort") is not True and (frozen_crew is None or current_crew is None or abs(frozen_crew - current_crew) > 1e-9):
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
            # V12.8.1: bis zu 'lanes' laufende/pausierte Aufträge je Maschine/Linie (Parallelbelegung)
            running_here = locked_by_machine.setdefault(mid, set())
            running_here.add(oid)
            if len(running_here) > machine_lanes(new, mid):
                return False, "MP-PROD-010", f"Auf Maschine '{mid}' sind mehr laufende/pausierte Aufträge hinterlegt als Parallelplätze."

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
    history_copy_fields = ("machineId", "projectId", "fa", "ab", "wt", "order", "articleNo", "description", "targetQty", "hours", "direction", "anchorMode", "requiredStart", "requiredFinish", "createdAt", "baselinePlan")
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


def _gf_departments_change(old: dict, new: dict) -> tuple[bool, str]:
    """V12.8.2: GF legt Bereiche an (Name, Art, aktiv) und richtet einem neuen Produktionsbereich
    die erste Maschine/Linie ein. Bereiche werden nicht gelöscht, nur deaktiviert."""
    dd = default_dept_id(old)
    da, db = _record_map(old.get("departments")), _record_map(new.get("departments"))
    if set(da) - set(db):
        return False, "GF löscht keine Bereiche – stattdessen deaktivieren."
    for did, b in db.items():
        a = da.get(did)
        if a is None:
            if set(b) - {"id", "name", "kind", "active", "planningType"} or str(b.get("planningType") or "") != "MACHINE":
                return False, "Neuer Bereich: nur Name, Art und aktiv (Planung über Maschinen/Linien)."
            continue
        diff = {k for k in set(a) | set(b) if canonical(a.get(k)) != canonical(b.get(k))}
        if diff - {"name", "kind", "active"}:
            return False, f"GF ändert an Bereichen nur Name, Art und aktiv ({sorted(diff)[0]})."
    ma, mb = _record_map(old.get("machines")), _record_map(new.get("machines"))
    old_with_machines = {str(m.get("departmentId") or dd) for m in ma.values()}
    for mid in set(ma) | set(mb):
        a, b = ma.get(mid), mb.get(mid)
        if canonical(a) == canonical(b):
            continue
        if a is not None:
            return False, "Bestehende Maschinen/Linien pflegt der Bereich bzw. Admin."
        if str(b.get("departmentId") or dd) in old_with_machines:
            return False, "GF legt nur die erste Maschine/Linie eines Bereichs an."
    return True, ""


def gf_change_allowed(old: dict, new: dict) -> tuple[bool, str]:
    allowed_root = {"departmentStaffNeeds", "weeklyEmployeeDeployments", "exceptions", "audit", "meta", "ui", "employees", "departments", "machines"}
    for key in set(old) | set(new):
        if key not in allowed_root and canonical(old.get(key)) != canonical(new.get(key)):
            return False, f"GF darf operative Produktionsdaten '{key}' nicht ändern."
    ok, reason = _gf_departments_change(old, new)
    if not ok:
        return False, reason
    ea, eb = _record_map(old.get("employees")), _record_map(new.get("employees"))
    if set(ea) != set(eb):
        return False, "GF legt keine Mitarbeiter an und entfernt keine."
    for eid in eb:
        if canonical(ea[eid]) != canonical(eb[eid]):
            ok, reason = _temp_request_change(ea[eid], eb[eid], "gf")
            if not ok:
                return False, reason
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


# Phasenwechsel je Rolle (Admin: alle). Der Produktionsstand wird nicht gespeichert,
# sondern aus den verknüpften FA abgeleitet.
# V12.23.0: Keine Übergabe mehr – PM (Entwicklung & Vertrieb) und AV (Produktion) arbeiten ab Anlage
# gleichzeitig. Offene Projekte (inquiry/pm/offer_sent/accepted) werden direkt „Produktion fertig“;
# der Wechsel nach „accepted“ bleibt für Altbestände und ältere Clients erlaubt.
_PROJECT_OPEN_PHASES = ("inquiry", "pm", "offer_sent", "accepted")
PROJECT_TRANSITIONS = {
    role: {(a, "accepted") for a in _PROJECT_OPEN_PHASES if a != "accepted"} | {(a, "closed") for a in _PROJECT_OPEN_PHASES}
    for role in ("project_management", "production_planning")
}
PROJECT_BASE_FIELDS = {"customer", "contact", "name", "note", "wt", "workflow", "quantity"}
PROJECT_FIELDS = {
    "sales": PROJECT_BASE_FIELDS | {"ab", "dueDate", "phase", "log", "updatedAt", "processes"},
    "project_management": PROJECT_BASE_FIELDS | {"ab", "dueDate", "phase", "log", "updatedAt", "processes", "offer", "development", "customerPlan"},
    "production_planning": PROJECT_BASE_FIELDS | {"ab", "dueDate", "phase", "log", "updatedAt", "processes"},
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
        own_areas = production_department_ids(new) | {"av"}
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
            elif role in {"project_management", "production_planning"} and str(b.get("phase")) in {"inquiry", "pm"}:
                pass
            elif role in {"project_management", "production_planning"} and str(b.get("phase")) == "accepted" and b.get("productionOnly") is True:
                pass  # V12.23.0: „Nur Produktion“ – direkt an die Produktion (AB/Termin prüft validate_projects)
            else:
                return False, f"Diese Rolle darf kein Projekt in dieser Phase anlegen ({label})."
            if any(str(x.get("actor") or "") != username for x in (b.get("log") or [])):
                return False, f"Projekt {label}: Verlaufseinträge müssen den angemeldeten Benutzer tragen."
            continue
        if b is None:
            if role != "project_management" or str(a.get("phase")) not in {"inquiry", "pm", "offer_sent", "lost"}:
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
        if x is not None and canonical(x.get("pmPlan")) != canonical(y.get("pmPlan")):
            return False, f"Projekt {label}: Den PM-Vorplan von „{y.get('title')}“ sichert nur die Arbeitsvorbereitung."
        if x is not None and x.get("pmPlan") and any(canonical(x.get(k)) != canonical(y.get(k)) for k in ("startDate", "dueDate")):
            return False, f"Projekt {label}: „{y.get('title')}“ wird von der Arbeitsvorbereitung terminiert – PM-Termine sind gesperrt."
    for pid, x in pa.items():
        if pid not in pb and x.get("pmPlan"):
            return False, f"Projekt {label}: „{x.get('title')}“ ist von der Arbeitsvorbereitung übernommen und kann nicht entfernt werden."
    return True, ""


def _pm_plan_snapshot_ok(x: dict, y: dict) -> bool:
    """V12.8.3: Beim ersten Überschreiben durch die AV wird der PM-Termin unverändert gesichert."""
    a, b = x.get("pmPlan"), y.get("pmPlan")
    if canonical(a) == canonical(b):
        return True
    if a or not isinstance(b, dict) or set(b) - {"startDate", "dueDate", "at", "by"}:
        return False
    return str(b.get("startDate") or "") == str(x.get("startDate") or "") and str(b.get("dueDate") or "") == str(x.get("dueDate") or "")


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
        if "pmPlan" in diff:
            if not _pm_plan_snapshot_ok(x, y):
                return False, f"Projekt {label}: PM-Vorplan von „{y.get('title')}“ darf nur einmal unverändert gesichert werden."
            diff.discard("pmPlan")
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
    # V12.10.2: "formats" entfernt – Formate pflegen nur Tiefzieh-Leitung/-Stellvertretung und Admin (V12.10.0).
    allowed_root = {"workSteps", "projects", "processTemplates", "audit", "meta", "ui", "planVersions"}
    for key in set(old) | set(new):
        if key not in allowed_root and canonical(old.get(key)) != canonical(new.get(key)):
            return False, f"Arbeitsvorbereitung darf '{key}' nicht ändern."
    a, b = _record_map(old.get("workSteps")), _record_map(new.get("workSteps"))
    dep_names = {str(d.get("id")): str(d.get("name") or d.get("id") or "") for d in (new.get("departments") or []) if isinstance(d, dict)}
    for rid in set(a) | set(b):
        if canonical(a.get(rid)) == canonical(b.get(rid)):
            continue
        for side in (a.get(rid), b.get(rid)):
            if side is not None and str(side.get("status") or "planned") != "planned":
                return False, f"Arbeitsvorbereitung ändert nur geplante Aufträge ('{side.get('fa') or side.get('order') or rid}' ist {side.get('status')})."
        before, after = a.get(rid), b.get(rid)
        if before is not None and after is not None and "handoffUnassigned" in before and "handoffUnassigned" not in after:
            return False, "Die AV-Herkunft einer Bereichsübergabe bleibt erhalten."
        if before is not None and after is not None and canonical(before.get("handoffUnassigned", False)) != canonical(after.get("handoffUnassigned", False)):
            return False, "Die Bereichsplanung übernimmt offene AV-Aufträge über die Planen-Aktion."
        if before is None and after is not None:
            did = str(after.get("departmentId") or "")
            is_confection = "konf" in did.casefold() or "konf" in dep_names.get(did, "").casefold()
            if not after.get("handoffUnassigned") or str(after.get("machineId") or "") or str(after.get("altMachineId") or "") or after.get("allowAlternative") or after.get("baselinePlan") is not None or after.get("planningWeek") or str(after.get("direction") or "forward") != "forward" or str(after.get("anchorMode") or "none") != "none" or after.get("requiredStart") or after.get("requiredFinish") or after.get("laneIndex"):
                return False, "Neue AV-Aufträge müssen ohne Ressource und Terminanker an die Bereichsplanung gehen."
            if not is_confection and (_finite_float(after.get("hours", 0)) or 0) > 0:
                return False, "Arbeitsvorbereitung darf Stunden nur für Konfektion vorgeben."
        if after is not None and canonical((before or {}).get("hours", 0)) != canonical(after.get("hours", 0)):
            dept = str(after.get("departmentId") or "")
            if ("konf" not in dept.casefold() and "konf" not in dep_names.get(dept, "").casefold()) and (_finite_float(after.get("hours", 0)) or 0) > 0:
                return False, "Arbeitsvorbereitung darf Stunden nur für Konfektion vorgeben."
        if before and after:
            for field in ("machineId", "altMachineId", "allowAlternative", "laneIndex", "pos", "direction", "anchorMode", "requiredStart", "requiredFinish", "planningWeek", "baselinePlan"):
                if canonical(before.get(field)) != canonical(after.get(field)):
                    return False, "Operative Planung übernimmt die zuständige Abteilung."
        # V12.24.0: Trocknung nach dem Arbeitsgang entscheidet die Abteilungsleitung, nicht die AV.
        if after is not None and canonical((before or {}).get("dryingHours")) != canonical(after.get("dryingHours")):
            return False, "Die Trocknungszeit legt die Abteilungsleitung fest."
        done_before = _finite_float((before or {}).get("doneHours", 0)) or 0.0
        done_after = _finite_float((after or {}).get("doneHours", 0)) or 0.0
        if after is not None and done_after != done_before:
            return False, f"Erledigte Stunden meldet die Abteilung, nicht die Arbeitsvorbereitung ('{after.get('fa') or rid}')."
        for f in ("goodQty", "scrapQty"):
            if after is not None and canonical((before or {}).get(f, 0) or 0) != canonical(after.get(f, 0) or 0):
                return False, f"Produktionsmengen meldet die Abteilung, nicht die Arbeitsvorbereitung ('{after.get('fa') or rid}')."
    return True, ""


def _record_map(items):
    return {str(x.get("id")): x for x in (items or []) if isinstance(x, dict) and x.get("id")}


def department_change_allowed(old: dict, new: dict, department_id: str) -> tuple[bool, str]:
    dd = default_dept_id(new)
    if not department_id:
        return False, "Kein Bereich am Benutzer hinterlegt."
    globally_allowed = {"audit", "meta", "ui", "planVersions", "departmentStaffNeeds"}
    scoped = {"workSteps", "machines", "machineBlocks", "employees", "personnelAssignments", "personnelAbsences", "history", "yearRules", "weekRules", "projects", "formats", "baseFormats"}
    # Sprechende Meldungen für häufige Fälle, danach die allgemeine Regel.
    if canonical(old.get("exceptions")) != canonical(new.get("exceptions")):
        return False, "Globale Betriebsferien/Kalender-Ausnahmen dürfen nur GF/Admin ändern."
    for key in ("shiftTemplates", "operatorCapacity"):
        if canonical(old.get(key)) != canonical(new.get(key)):
            return False, f"Globale Schicht-Einstellung '{key}' ändert nur der Admin."
    if canonical(old.get("weeklyEmployeeDeployments")) != canonical(new.get("weeklyEmployeeDeployments")):
        return False, "Interne KW-Versetzungen dürfen nur GF/Admin ändern."
    for key in set(old) | set(new):
        if key in globally_allowed or key in scoped:
            continue
        if canonical(old.get(key)) != canonical(new.get(key)):
            return False, f"Bereichsrolle darf globale Einstellung '{key}' nicht ändern."
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
    machine_dept = {str(m.get("id")): str(m.get("departmentId") or dd) for m in (new.get("machines") or []) if isinstance(m, dict)}
    machine_dept.update({str(m.get("id")): str(m.get("departmentId") or dd) for m in (old.get("machines") or []) if isinstance(m, dict)})
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
        if str(rec.get("departmentId") or dd) != department_id:
            return False, "Maschine gehört nicht zum eigenen Bereich."
    for name, what in (("formats", "Format"), ("baseFormats", "Grundformat")):
        for rec in changed_records(name):
            if str(rec.get("departmentId") or "") != department_id:
                return False, f"{what} gehört nicht zum eigenen Bereich."
    for rec in changed_records("workSteps"):
        did = str(rec.get("departmentId") or machine_dept.get(str(rec.get("machineId")), dd))
        if did != department_id:
            return False, "Arbeitsgang gehört nicht zum eigenen Bereich."
    old_steps = _record_map(old.get("workSteps"))
    new_steps = _record_map(new.get("workSteps"))
    if any("handoffUnassigned" in before and rid not in new_steps for rid, before in old_steps.items()):
        return False, "AV-Aufträge können nicht durch Löschen aus der Bereichsplanung entfernt werden."
    for rid, rec in new_steps.items():
        before = old_steps.get(rid)
        if before is not None:
            if rec.get("handoffUnassigned") is True and (str(rec.get("machineId") or "") or str(rec.get("status") or "planned") != "planned"):
                return False, "Unzugeordnete AV-Aufträge bleiben ohne Ressource und Freigabe."
            if canonical(before.get("handoffUnassigned", False)) != canonical(rec.get("handoffUnassigned", False)):
                if before.get("handoffUnassigned") is not True or rec.get("handoffUnassigned") is not False:
                    return False, "Eine Bereichsübergabe kann nur einmal übernommen werden."
                mid = str(rec.get("machineId") or "")
                machine = next((m for m in (new.get("machines") or []) if str(m.get("id")) == mid), None)
                if not machine or machine_dept.get(mid, "") != department_id or _finite_float(rec.get("hours", 0)) is None or _finite_float(rec.get("hours", 0)) <= 0:
                    return False, "Zum Einplanen braucht der Bereich eine eigene Ressource und Maschinenlaufzeit."
            if "handoffUnassigned" in before:
                if "handoffUnassigned" not in rec:
                    return False, "Die AV-Herkunft einer Bereichsübergabe bleibt erhalten."
                for field in ("fa", "faNumber", "projectId", "ab", "wt", "targetQty", "sequence", "predecessorIds", "avNote"):
                    if canonical(before.get(field)) != canonical(rec.get(field)):
                        return False, "FA, Projekt, Menge und Vorgänger pflegt die Arbeitsvorbereitung."
        resource = next((m for m in old.get("machines") or [] if m.get("id") == (before or {}).get("machineId")), {})
        dep = str((before or rec).get("departmentId") or "")
        dep_name = next((str(d.get("name") or "") for d in (old.get("departments") or []) if str(d.get("id")) == dep), "")
        if before is not None and ("konf" in dep.casefold() or "konf" in dep_name.casefold() or resource.get("kind") == "line" or resource.get("effortScaling")) and canonical(before.get("hours")) != canonical(rec.get("hours")):
            return False, "Konfektionsstunden pflegt die Arbeitsvorbereitung."
        if before is not None and before.get("planningType") == "LABOR_HOURS" and canonical(before.get("requiredHours")) != canonical(rec.get("requiredHours")):
            return False, "Konfektionsstunden pflegt die Arbeitsvorbereitung."
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
    old_emps, new_emps = _record_map(old.get("employees")), _record_map(new.get("employees"))
    for eid, rec in new_emps.items():
        if canonical(old_emps.get(eid)) != canonical(rec):
            ok, reason = _temp_request_change(old_emps.get(eid), rec, "department")
            if not ok:
                return False, reason
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
        did = str(source.get("departmentId") or machine_dept.get(str(source.get("machineId")), dd))
        if did != department_id:
            return False, "Historieneintrag gehört nicht zum eigenen Bereich."
    return True, ""


# --------------------------------------------------------------------------- Historie-Archiv
# Die Ist-Historie wächst mit jedem fertigen Auftrag. Weil jeder Speichervorgang den ganzen
# Datenstand überträgt (MAX_BODY), wandern ältere Einträge in die Tabelle history_archive.
# Einträge offener Projekte bleiben im Live-Stand (Produktionskette/Projektstatus).
HISTORY_LIVE_CAP = 1500


def _history_finished_at(h: dict) -> str:
    return str(h.get("finishedAt") or h.get("cancelledAt") or h.get("actualFinishedAt") or "")


def archive_excess_history(con: sqlite3.Connection, state: dict) -> list[str]:
    """Verschiebt die ältesten Historieneinträge über HISTORY_LIVE_CAP ins Archiv.

    Ändert ``state`` in place und gibt die archivierten IDs zurück. Muss in derselben
    Transaktion laufen, die den neuen Live-Stand schreibt.
    """
    history = state.get("history")
    if not isinstance(history, list) or len(history) <= HISTORY_LIVE_CAP:
        return []
    open_projects = {str(p.get("id")) for p in (state.get("projects") or [])
                     if isinstance(p, dict) and str(p.get("phase") or "") not in {"closed", "lost"}}
    excess = len(history) - HISTORY_LIVE_CAP
    # Neueste Einträge stehen vorne (unshift): von hinten archivieren.
    move: set[int] = set()
    for i in range(len(history) - 1, -1, -1):
        if len(move) >= excess:
            break
        h = history[i]
        if isinstance(h, dict) and h.get("id") and str(h.get("projectId") or "") not in open_projects:
            move.add(i)
    if not move:
        return []
    ts = now_iso()
    moved = []
    for i in sorted(move):
        h = history[i]
        con.execute("INSERT OR REPLACE INTO history_archive(id,finished_at,archived_at,json) VALUES(?,?,?,?)",
                    (str(h["id"]), _history_finished_at(h), ts, json.dumps(h, ensure_ascii=False, separators=(",", ":"))))
        moved.append(str(h["id"]))
    state["history"] = [h for i, h in enumerate(history) if i not in move]
    return moved


def archive_history_on_start(con: sqlite3.Connection) -> None:
    row = con.execute("SELECT json FROM state WHERE id=1").fetchone()
    if not row:
        return
    state = json.loads(row["json"])
    if len(state.get("history") or []) <= HISTORY_LIVE_CAP:
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        moved = archive_excess_history(con, state)
        if moved:
            con.execute("UPDATE state SET json=?,updated_at=?,updated_by=? WHERE id=1",
                        (json.dumps(state, ensure_ascii=False, separators=(",", ":")), now_iso(), "Historie-Archiv"))
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    if moved:
        print(f"DB-WARTUNG: {len(moved)} ältere Historieneinträge ins Archiv verschoben")


def strip_archived_history(con: sqlite3.Connection, old: dict, incoming: dict) -> None:
    """Browser, die noch archivierte Einträge im Speicher haben, schicken sie nicht zurück in den Live-Stand."""
    history = incoming.get("history")
    if not isinstance(history, list):
        return
    live = {str(h.get("id")) for h in (old.get("history") or []) if isinstance(h, dict)}
    candidates = [str(h.get("id")) for h in history if isinstance(h, dict) and h.get("id") and str(h.get("id")) not in live]
    if not candidates:
        return
    archived = set()
    for i in range(0, len(candidates), 500):
        chunk = candidates[i:i + 500]
        archived |= {r[0] for r in con.execute(f"SELECT id FROM history_archive WHERE id IN ({','.join('?' * len(chunk))})", chunk)}
    if archived:
        incoming["history"] = [h for h in history if not (isinstance(h, dict) and str(h.get("id")) in archived)]


# --------------------------------------------------------------------------- Abwesenheitsgrund
# Urlaub/Krank/Sonstiges sind Personaldaten (Krankheit = Gesundheitsdaten). Den Grund sehen nur
# Admin, GF und die Leitung/Stellvertretung des Bereichs, in dem der Mitarbeiter geplant wird.
# Alle anderen erhalten "Abwesend" – schon vom Server, nicht erst in der Oberfläche.
ABSENCE_REASONS = ("Urlaub", "Krank", "Sonstiges")
ABSENCE_MASK = "Abwesend"
ABSENCE_AUDIT_ACTIONS = {f"{x} eingetragen" for x in ABSENCE_REASONS}
ABSENCE_FULL_ROLES = {"admin", "gf"}


def _absence_visible(state: dict, user: dict):
    """Liefert eine Funktion rec -> bool (Grund sichtbar)."""
    role = str(user.get("role") or "")
    if role in ABSENCE_FULL_ROLES:
        return lambda rec: True
    if role not in DEPARTMENT_ROLES or not user.get("department_id"):
        return lambda rec: False
    own = str(user.get("department_id"))
    emp_dept = {str(e.get("id")): str(e.get("departmentId") or "") for e in (state.get("employees") or []) if isinstance(e, dict)}
    deployments = _deployment_map(state)

    def visible(rec):
        eid = str(rec.get("employeeId"))
        if emp_dept.get(eid) == own:
            return True
        try:
            return deployments.get((eid, _week_start_key(rec.get("date")))) == own
        except (TypeError, ValueError):
            return False
    return visible


def _masked_absence(rec: dict) -> dict:
    return {**rec, "label": ABSENCE_MASK} if str(rec.get("label") or "") in ABSENCE_REASONS else rec


def _masked_audit(rec: dict) -> dict:
    if rec.get("collection") == "personnelAbsences" and rec.get("field") == "label":
        return {**rec, "old": ABSENCE_MASK if rec.get("old") else None, "new": ABSENCE_MASK if rec.get("new") else None}
    if str(rec.get("action") or "") in ABSENCE_AUDIT_ACTIONS:
        return {**rec, "action": "Abwesenheit eingetragen"}
    return rec


class ProductionError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status


def production_access(state, user, oid):
    order = next((x for x in state.get("workSteps") or [] if str(x.get("id")) == oid), None)
    hist = next((x for x in state.get("history") or [] if str(x.get("originalOrderId")) == oid), None)
    rec = order or hist
    if not rec:
        raise ProductionError("MP-PROD-040", "FA nicht gefunden.", 404)
    if user["role"] != "admin" and (user["role"] not in DEPARTMENT_ROLES | {"production"} or str(user.get("department_id") or "") != str(rec.get("departmentId") or "")):
        raise ProductionError("MP-PROD-041", "Keine Produktionsrechte für diesen Bereich.", 403)
    return order, hist


def production_capacity(state, order):
    """Freeze relevant staffing/calendar inputs at each start or resume."""
    mid, did = order.get("machineId"), order.get("departmentId")
    employees = [dict(e) for e in state.get("employees") or [] if mid in e.get("skills", []) and (e.get("departmentId") == did or any(x.get("employeeId") == e.get("id") and x.get("departmentId") == did for x in state.get("weeklyEmployeeDeployments") or []))]
    eids = {e["id"] for e in employees}
    return {"personnelGate": state.get("personnelGate", False), "machines": [dict(m) for m in state.get("machines") or [] if m.get("id") == mid], "employees": employees, "personnelAssignments": [dict(x) for x in state.get("personnelAssignments") or [] if x.get("employeeId") in eids], "personnelAbsences": [{"employeeId": x.get("employeeId"), "date": x.get("date")} for x in state.get("personnelAbsences") or [] if x.get("employeeId") in eids], "weeklyEmployeeDeployments": [dict(x) for x in state.get("weeklyEmployeeDeployments") or [] if x.get("employeeId") in eids], "shiftTemplates": json.loads(json.dumps(state.get("shiftTemplates") or {})), "exceptions": state.get("exceptions") or [], "yearRules": [dict(x) for x in state.get("yearRules") or [] if x.get("machineId") == mid], "weekRules": [dict(x) for x in state.get("weekRules") or [] if x.get("machineId") == mid], "machineBlocks": [dict(x) for x in state.get("machineBlocks") or [] if x.get("machineId") == mid]}


def production_windows(state, order, day, fallback_crew=1):
    mid = str(order.get("machineId") or "")
    dk, lane = day.strftime("%Y-%m-%d"), order.get("laneIndex", 1)
    m = next((m for m in state.get("machines") or [] if str(m.get("id")) == mid), {})
    minimum = max(1, int((m.get("laneStaff") or {}).get(str(lane), m.get("staffRequired", 0)) or 0))
    maximum = int(m.get("crewMax") or (99 if m.get("effortScaling") else m.get("crew") or fallback_crew))
    roster = []
    if mid and state.get("personnelGate"):
        for e in state.get("employees") or []:
            if not e.get("active", True) or is_absent(state, e, dk) or not can_staff(state, e, mid, day):
                continue
            assignment = personnel_assignment(state, e, day, dk)
            if assignment and assignment.get("machineId") == mid and assignment.get("laneIndex", 1) == lane:
                roster.append((e, assignment))
    base = work_intervals(state, mid, day) if mid else [(day.replace(hour=0, minute=0, second=0, microsecond=0), day.replace(hour=0, minute=0, second=0, microsecond=0)+timedelta(days=1), "single")]
    for a, z, shift in base:
        points = {a, z}
        for _, assignment in roster:
            for value in [assignment.get("start"), assignment.get("end"), *[b.get(k) for b in assignment.get("breaks") or [] for k in ("start", "end")]]:
                minute = clock_minutes(value)
                if minute is not None:
                    point = day.replace(hour=minute//60, minute=minute%60, second=0, microsecond=0)
                    if a < point < z:
                        points.add(point)
        for block in state.get("machineBlocks") or []:
            for key in ("start", "end"):
                point = local_dt(block.get(key))
                if point and a < point < z:
                    points.add(point)
        points = sorted(points)
        for x, y in zip(points, points[1:]):
            at = x+(y-x)/2
            clock = at.strftime("%H:%M")
            people = [{"id": str(e["id"]), "name": str(e["name"])} for e, assignment in roster if assignment.get("start", "") <= clock < assignment.get("end", "") and not any(b.get("start", "") <= clock < b.get("end", "") for b in assignment.get("breaks") or [] if b.get("start") and b.get("end"))]
            crew = min(maximum, len(people)) if mid and state.get("personnelGate") else min(maximum, fallback_crew)
            blocked = hits_machine_block(state, {"start": x, "end": y, "machineId": mid}) if mid else False
            yield x, y, shift, crew, people[:maximum], (crew >= minimum and not blocked)


def production_staff(state, order, stamp):
    moment = local_dt(stamp)
    fallback = int((order.get("baselinePlan") or {}).get("crew") or next((m.get("crew") for m in state.get("machines") or [] if m.get("id") == order.get("machineId")), 1) or 1)
    for a, z, _, crew, people, available in production_windows(state, order, moment, fallback):
        if a <= moment < z and available:
            return people, crew
    raise ProductionError("MP-PERS-034", "Qualifiziertes Personal am Parallelplatz fehlt.")


def production_segments(state, order, start, hours, effort=False, setup=0, fallback_crew=1):
    cur = local_dt(start)
    left, setup_left, segs = max(0.01, hours), setup, []
    for offset in range(730):
        day = cur+timedelta(days=offset)
        for a, z, shift, crew, people, available in production_windows(state, order, day, fallback_crew):
            a = max(a, cur)
            if z <= a or not available:
                continue
            while z > a and (left > 1e-8 or setup_left > 1e-8):
                is_setup = setup_left > 1e-8
                rate = 1 if is_setup or not effort else crew
                take = min(setup_left if is_setup else left/rate, (z-a).total_seconds()/3600)
                end = a+timedelta(hours=take)
                segs.append({"start": a.replace(tzinfo=LOCAL_TZ).isoformat(), "end": end.replace(tzinfo=LOCAL_TZ).isoformat(), "shift": shift, "laneIndex": order.get("laneIndex", 1), "crew": crew, "employees": people, **({"setup": True} if is_setup else {})})
                if is_setup:
                    setup_left -= take
                else:
                    left -= take*rate
                a = end
            if left <= 1e-8 and setup_left <= 1e-8:
                return segs
    raise ProductionError("MP-PROD-042", "Keine verfügbare Arbeits-/Personalzeit gefunden.")


def production_actual(state, order, end):
    segments = []
    for phase in order.get("productionPhases") or []:
        a, z = local_dt(phase["start"]), local_dt(phase.get("end") or end)
        if z < a:
            raise ProductionError("MP-PROD-043", "Produktionszeit ist ungültig.")
        frozen = phase.get("capacity") or state
        day = a.replace(hour=0, minute=0, second=0, microsecond=0)
        while day <= z:
            for x, y, shift, crew, people, _ in production_windows(frozen, order, day, phase["crew"]):
                x, y = max(a, x), min(z, y)
                blocked = order.get("machineId") and hits_machine_block(frozen, {"start": x, "end": y, "machineId": order["machineId"]})
                if y > x and not blocked:
                    segments.append({"start": x.replace(tzinfo=LOCAL_TZ).isoformat(), "end": y.replace(tzinfo=LOCAL_TZ).isoformat(), "shift": shift, "laneIndex": order.get("laneIndex", 1), "crew": crew, "employees": people if phase.get("capacity") else phase["employees"]})
            day += timedelta(days=1)
    wall = sum((local_dt(x["end"])-local_dt(x["start"])).total_seconds()/3600 for x in segments)
    person = sum((local_dt(x["end"])-local_dt(x["start"])).total_seconds()/3600*x["crew"] for x in segments)
    return segments, wall, person


def production_replan(state, order, stamp, crew):
    segments, wall, person = production_actual(state, order, stamp)
    setup = float((order.get("baselinePlan") or {}).get("setupMinutes") or 0)/60
    setup_person, setup_left = 0, setup
    for x in segments:
        take = min(setup_left, (local_dt(x["end"])-local_dt(x["start"])).total_seconds()/3600)
        setup_person += take*x["crew"]
        setup_left -= take
    effort = bool((order.get("baselinePlan") or {}).get("effort")) or order.get("planningType") == "LABOR_HOURS"
    target = float(order.get("hours") or order.get("requiredHours") or 1)
    if not effort:
        target /= float((order.get("baselinePlan") or {}).get("crew") or 1)
    remaining = max(0.01, target-max(0, person-setup_person if effort else wall-setup))
    locked = production_segments(state, order, stamp, remaining, effort, setup_left, crew)
    order["lockedSegments"] = locked
    order["lockedStart"] = locked[0]["start"]
    order["remainingHours"] = sum((local_dt(x["end"])-local_dt(x["start"])).total_seconds()/3600 for x in locked)


def production_apply(state, user, oid, action, body, stamp=None):
    """One authoritative runtime for every FA source and planning type."""
    stamp = stamp or now_iso()
    order, hist = production_access(state, user, oid)
    if not order and action == "label":
        order = hist
    if not order:
        raise ProductionError("MP-PROD-044", "FA ist bereits abgeschlossen.", 409)
    status = order.get("status", "planned")
    if status in {"running", "paused"} and "productionPhases" not in order:
        start = order.get("actualStartedAt") or order.get("runningSince")
        if not start:
            raise ProductionError("MP-PROD-043", "Legacy-Produktionsstart fehlt.")
        crew = int((order.get("baselinePlan") or {}).get("crew") or 1)
        phases, cursor = [], start
        for pause in order.get("pauseIntervals") or []:
            phases.append({"start": cursor, "end": pause["start"], "crew": crew, "employees": [], "actor": "Legacy"})
            cursor = pause.get("end")
            if not cursor:
                break
        if cursor:
            phases.append({"start": cursor, "end": "", "crew": crew, "employees": [], "actor": "Legacy"})
        order["productionPhases"] = phases
        order.setdefault("partialCompletions", [])
    allowed = {"release": {"planned"}, "start": {"released"}, "pause": {"running"}, "resume": {"paused"}, "partial": {"running", "paused"}, "finish": {"running", "paused"}, "abort": {"running", "paused"}, "label": {"planned", "released", "running", "paused", "done", "cancelled"}}
    if action not in allowed or status not in allowed[action]:
        raise ProductionError("MP-PROD-045", "Ungültiger Produktionsstatuswechsel.", 409)
    if action in {"release", "start"}:
        completed = {str(h.get("originalOrderId")) for h in state.get("history") or [] if h.get("recordType", "done") == "done"}
        completed |= {str(x.get("id")) for x in state.get("workSteps") or [] if x.get("status") == "done"}
        if any(str(i) not in completed for i in order.get("predecessorIds") or []):
            raise ProductionError("MP-PROD-046", "Vorgänger offen.")
        if order.get("planningType") == "LABOR_HOURS" and float(order.get("requiredHours") or 0) <= 0:
            raise ProductionError("MP-PROD-046", "Konfektionsstunden fehlen.")
    if action == "release":
        if order.get("planningType") == "MACHINE":
            raise ProductionError("MP-PROD-045", "Maschinen-FA über den Plan freigeben.")
        order["status"] = "released"
    elif action in {"start", "resume"}:
        mid = str(order.get("machineId") or "")
        if action == "start":
            planned_mid = str((order.get("baselinePlan") or {}).get("machineId") or mid)
            if planned_mid != mid:
                if not order.get("allowAlternative") or planned_mid != str(order.get("altMachineId") or ""):
                    raise ProductionError("MP-PROD-047", "Freigegebene Einsatzmaschine ist ungültig.")
                mid = planned_mid
                order.update(machineId=mid, altMachineId="", allowAlternative=False)
            occupied = [x for x in state.get("workSteps") or [] if x is not order and str(x.get("machineId") or "") == mid and x.get("status") in {"running", "paused"}]
            if mid and len(occupied) >= machine_lanes(state, mid):
                raise ProductionError("MP-PROD-047", "Alle Parallelplätze sind belegt.")
            if mid:
                used = {int(x.get("laneIndex", 1)) for x in occupied}
                lane = body.get("laneIndex", order.get("laneIndex") or next((i for i in range(1, machine_lanes(state, mid)+1) if i not in used), 1))
                if isinstance(lane, bool) or not isinstance(lane, int) or lane in used or not 1 <= lane <= machine_lanes(state, mid):
                    raise ProductionError("MP-PROD-047", "Parallelplatz ist belegt oder ungültig.")
                order["laneIndex"] = lane
            order["actualStartedAt"] = stamp
            order["goodQty"], order["scrapQty"] = 0, 0
            order["productionPhases"], order["partialCompletions"] = [], []
            order["pauseIntervals"] = []
        if mid:
            moment = local_dt(stamp)
            if not any(a <= moment < z for a, z, _ in work_intervals(state, mid, moment)):
                raise ProductionError("MP-PROD-042", "Start/Fortsetzen nur während der Arbeitszeit möglich.")
            probe = {"start": moment, "end": moment+timedelta(microseconds=1), "machineId": mid}
            if hits_machine_block(state, probe):
                raise ProductionError("MP-PROD-047", "Maschine ist aktuell gesperrt.")
            if action == "start" and (order.get("baselinePlan") or {}).get("start") and moment < local_dt(order["baselinePlan"]["start"])-timedelta(minutes=1):
                raise ProductionError("MP-PROD-042", "Geplanter Start liegt in der Zukunft.")
            people, crew = production_staff(state, order, stamp)
        else:
            crew = body.get("crew", 1)
            if isinstance(crew, bool) or not isinstance(crew, int) or not 1 <= crew <= 99:
                raise ProductionError("MP-PERS-034", "Besetzung muss zwischen 1 und 99 liegen.")
            people = []
        order["productionPhases"].append({"start": stamp, "end": "", "crew": crew, "employees": people, "actor": user["username"], "capacity": production_capacity(state, order)})
        if action == "resume":
            order["pauseIntervals"][-1]["end"] = stamp
        production_replan(state, order, stamp, crew)
        order["runningSince"], order["pausedAt"], order["status"] = stamp, "", "running"
        order["runtimeVersion"] = 1
    elif action == "pause":
        order["productionPhases"][-1]["end"] = stamp
        order["pauseIntervals"].append({"start": stamp, "end": ""})
        order["pausedAt"], order["status"] = stamp, "paused"
        production_replan(state, order, stamp, order["productionPhases"][-1]["crew"])
    elif action in {"partial", "finish", "abort"}:
        target = int(order.get("targetQty") or order.get("quantity") or 0)
        previous_good, previous_scrap = int(order.get("goodQty") or 0), int(order.get("scrapQty") or 0)
        good = body.get("goodQty", max(0, target-previous_good-previous_scrap) if action == "finish" else 0)
        scrap = body.get("scrapQty", 0)
        if not _nonnegative_int(good) or not _nonnegative_int(scrap) or (action == "partial" and good+scrap <= 0) or (target and previous_good+previous_scrap+good+scrap > target):
            raise ProductionError("MP-PROD-048", "Meldemenge ist ungültig oder größer als die Restmenge.")
        order["goodQty"], order["scrapQty"] = previous_good+good, previous_scrap+scrap
        order["remainingQty"] = max(0, target-order["goodQty"]-order["scrapQty"])
        partial = {"id": body["requestId"], "goodQty": good, "scrapQty": scrap, "at": stamp, "actor": user["username"]}
        order.setdefault("partialCompletions", []).append(partial)
        if action in {"finish", "abort"}:
            if action == "abort" and (not isinstance(body.get("reason"), str) or not body["reason"].strip() or len(body["reason"]) > 500):
                raise ProductionError("MP-PROD-045", "Abbruchgrund fehlt oder ist zu lang.")
            if status == "running":
                order["productionPhases"][-1]["end"] = stamp
            else:
                order["pauseIntervals"][-1]["end"] = stamp
            segments, wall, person = production_actual(state, order, stamp)
            finished = {**order, "id": "h_"+secrets.token_hex(10), "originalOrderId": oid, "recordType": "done", "status": "done", "actualFinishedAt": stamp, "finishedAt": stamp, "actualSegments": segments, "actualWorkHours": wall, "actualMachineHours": wall, "actualPersonHours": person, "actualProductionHours": max(0, wall-float((order.get("baselinePlan") or {}).get("setupMinutes", 0))/60), "actor": user["username"]}
            if action == "abort":
                finished.update(recordType="cancelled", status="cancelled", abortReason=body["reason"].strip())
            state["history"].insert(0, finished)
            state["workSteps"].remove(order)
            if order.get("sourceType") in DEMAND_SOURCES:
                # Auch ein Abbruch bucht bereits gefertigte Gutteile: sie liegen physisch vor.
                demand_book_completion(state, order, finished)
    elif action == "label":
        template = next((t for t in state.get("palletTemplates") or [] if t.get("id") == body.get("templateId")), {})
        if body.get("templateId") and not template:
            raise ProductionError("MP-PROD-048", "Etikettenvorlage nicht gefunden.")
        qty = body.get("quantity")
        if not _nonnegative_int(qty) or qty <= 0:
            raise ProductionError("MP-PROD-048", "Palettenmenge muss größer 0 sein.")
        shelf = template.get("shelfLifeDays", 0)
        if not _nonnegative_int(shelf) or shelf > 36500:
            raise ProductionError("MP-PROD-048", "Haltbarkeit ist ungültig.")
        sequence = 1+max((int(x.get("sequence", 0)) for x in state.get("palletLabels") or []), default=0)
        # Hex encodes the stable ID without losing distinctions such as '_' versus '-'.
        barcode = f"FA-{oid.encode().hex().upper()}-P{sequence:06d}"
        state["palletLabels"].append({"id": "pal_"+secrets.token_hex(8), "sequence": sequence, "orderId": oid, "departmentId": order["departmentId"], "fa": order.get("fa"), "articleNo": order.get("articleNo", ""), "description": order.get("description", ""), "quantity": qty, "fromAddress": str(template.get("fromAddress") or ""), "toAddress": str(template.get("toAddress") or ""), "bestBefore": (local_dt(stamp)+timedelta(days=shelf)).strftime("%Y-%m-%d") if shelf else "", "barcode": barcode, "templateId": template.get("id", ""), "createdAt": stamp})
    event = {"id": "evt_"+secrets.token_hex(10), "orderId": oid, "fa": order.get("fa"), "departmentId": order["departmentId"], "projectId": order.get("projectId", ""), "sourceType": order.get("sourceType", "PROJECT"), "sourceId": order.get("sourceId", ""), "action": action, "at": stamp, "actor": user["username"], "role": user["role"], "goodQty": order.get("goodQty", 0), "scrapQty": order.get("scrapQty", 0)}
    state["productionEvents"].append(event)
    return event


# ---------------------------------------------------------------------------------------------
# V12.22.0 Block E: Rahmenaufträge, Abrufe, Bestand, Reservierung und Bedarf → FA.
# Server-autoritativ wie die Produktions-Runtime: Änderungen nur über /api/demand/<aktion>, idempotent je
# Benutzer + Request-ID. Ab FA läuft alles im gemeinsamen Produktionskern (Bereichsvorrat, Planung, Runtime).
#   verfügbar = physisch - reserviert
#   Abrufbedarf = offen - reserviert - erwartete Menge offener Abruf-FA   (offen = Abrufmenge - geliefert)
#   Bestandsbedarf = Sollbestand - verfügbar - erwartete Menge offener Bestands-FA
# ---------------------------------------------------------------------------------------------
DEMAND_WRITE_ROLES = {"admin", "production_planning"}
DEMAND_SOURCES = {"FRAME_ORDER", "STOCK_REQUIREMENT"}
DEMAND_ACTIONS = {"frame-order-save", "call-off-create", "call-off-update", "call-off-cancel", "reserve", "fa-create", "deliver", "stock-save"}
DEMAND_LOCKED_FIELDS = ("sourceType", "sourceId", "frameOrderId", "callOffId", "stockRequirementId", "articleId", "articleNo", "targetQty")


def _demand_text(body, key, limit, required=False):
    value = body.get(key, "")
    if not isinstance(value, str) or len(value.strip()) > limit or (required and not value.strip()):
        raise ProductionError("MP-DEM-001", f"Feld '{key}' fehlt oder ist zu lang (max. {limit} Zeichen).")
    return value.strip()


def _demand_qty(body, key, positive=True):
    value = body.get(key)
    if not _nonnegative_int(value) or (positive and value <= 0) or value > 10**9:
        raise ProductionError("MP-DEM-002", f"Menge '{key}' muss eine ganze Zahl {'größer 0' if positive else 'ab 0'} sein.")
    return value


def _demand_date(body, key, required=False):
    value = body.get(key, "")
    if (value or required) and not _valid_date_key(value):
        raise ProductionError("MP-DEM-003", f"Datum '{key}' im Format JJJJ-MM-TT angeben.")
    return value or ""


def _find(items, rid, code, label):
    rec = next((x for x in items if str(x.get("id")) == str(rid)), None)
    if rec is None:
        raise ProductionError(code, f"{label} nicht gefunden.", 404)
    return rec


def _inventory_for(state, article, department, create=False):
    inv = next((x for x in state["inventory"] if str(x.get("articleId")) == article and str(x.get("departmentId")) == department), None)
    if inv is None and create:
        inv = {"id": "inv_"+secrets.token_hex(8), "articleId": article, "departmentId": department, "physicalQty": 0, "reservedQty": 0}
        state["inventory"].append(inv)
    return inv


def _expected_output(state, key, value):
    """Noch erwartete Gutmenge offener FA einer Bedarfsquelle (Teilmeldungen werden erst bei Fertigmeldung gebucht)."""
    return sum(max(0, int(x.get("targetQty") or 0) - int(x.get("scrapQty") or 0)) for x in state.get("workSteps") or [] if str(x.get(key) or "") == str(value))


def call_off_demand(state, c):
    open_qty = int(c.get("qty") or 0) - int(c.get("deliveredQty") or 0)
    return max(0, open_qty - int(c.get("reservedQty") or 0) - _expected_output(state, "callOffId", c["id"]))


def stock_demand(state, inv):
    target = int(inv.get("targetQty") or 0)
    available = int(inv.get("physicalQty") or 0) - int(inv.get("reservedQty") or 0)
    return max(0, target - available - _expected_output(state, "stockRequirementId", inv["id"]))


def _reserve_call_off(state, c):
    """Freien Bestand deterministisch für genau diesen Abruf reservieren; liefert die reservierte Menge."""
    inv = _inventory_for(state, str(c["articleId"]), str(c["departmentId"]))
    if inv is None:
        return 0
    need = int(c["qty"]) - int(c.get("deliveredQty") or 0) - int(c.get("reservedQty") or 0)
    take = max(0, min(need, int(inv.get("physicalQty") or 0) - int(inv.get("reservedQty") or 0)))
    if take:
        c["reservedQty"] = int(c.get("reservedQty") or 0) + take
        inv["reservedQty"] = int(inv.get("reservedQty") or 0) + take
    return take


def _demand_log(rec, user, stamp, action, **detail):
    rec.setdefault("log", []).append({"at": stamp, "actor": user["username"], "action": action, **detail})


def _demand_fa(state, stamp, source, source_id, department, article, description, qty, due, fa, links):
    steps = state["workSteps"]
    pos = max([_finite_float(x.get("pos")) or 0 for x in steps] + [0]) + 10
    step = {"id": "ws_"+secrets.token_hex(8), "sequence": pos, "planningType": "MACHINE", "pos": pos, "departmentId": department,
            "projectId": "", "predecessorIds": [], "fa": fa, "faNumber": fa, "order": fa, "ab": "", "wt": "",
            "machineId": "", "altMachineId": "", "allowAlternative": False, "articleNo": article, "articleId": article,
            "description": description, "targetQty": qty, "dueDate": due, "baselinePlan": None, "hours": 0,
            "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none",
            "requiredStart": "", "requiredFinish": "", "createdAt": stamp, "lockedStart": "", "lockedSegments": [],
            "actualStartedAt": "", "runningSince": "", "pausedAt": "", "pauseIntervals": [], "remainingHours": None,
            "lastStatusCheckAt": "", "handoffUnassigned": True, "sourceType": source, "sourceId": source_id, **links}
    steps.append(step)
    return step


def demand_apply(state, user, action, body, stamp=None):
    """Eine autoritative Bedarfslogik für Rahmenauftrag, Abruf, Bestand und FA-Erzeugung."""
    stamp = stamp or now_iso()
    if user["role"] not in DEMAND_WRITE_ROLES:
        raise ProductionError("MP-DEM-040", "Rahmenaufträge, Abrufe und Bestand pflegt die Arbeitsvorbereitung.", 403)
    for key in ("frameOrders", "callOffs", "inventory", "workSteps"):
        state.setdefault(key, [])
    production_departments = {str(d.get("id")) for d in state.get("departments") or [] if d.get("kind", "production") == "production" and d.get("active", True) is not False}
    result = {"action": action, "at": stamp, "actor": user["username"]}
    if action == "frame-order-save":
        department = _demand_text(body, "departmentId", 80, True)
        if department not in production_departments:
            raise ProductionError("MP-DEM-004", "Rahmenauftrag braucht einen aktiven Produktionsbereich.")
        fields = {"number": _demand_text(body, "number", 60, True), "customer": _demand_text(body, "customer", 120, True),
                  "articleId": _demand_text(body, "articleId", 80, True), "description": _demand_text(body, "description", 300),
                  "departmentId": department, "totalQty": _demand_qty(body, "totalQty"), "validTo": _demand_date(body, "validTo")}
        status = body.get("status", "open")
        if status not in {"open", "closed"}:
            raise ProductionError("MP-DEM-005", "Status muss 'open' oder 'closed' sein.")
        if any(str(f.get("number")) == fields["number"] and f.get("id") != body.get("id") for f in state["frameOrders"]):
            raise ProductionError("MP-DEM-006", f"Rahmenauftrag {fields['number']} existiert bereits.", 409)
        if body.get("id"):
            fo = _find(state["frameOrders"], body["id"], "MP-DEM-010", "Rahmenauftrag")
            calls = [c for c in state["callOffs"] if c.get("frameOrderId") == fo["id"]]
            if calls and (fields["articleId"] != fo["articleId"] or fields["departmentId"] != fo["departmentId"]):
                raise ProductionError("MP-DEM-007", "Artikel und Bereich sind nach dem ersten Abruf fest.", 409)
            called = sum(int(c.get("qty") or 0) for c in calls)
            if fields["totalQty"] < called:
                raise ProductionError("MP-DEM-008", f"Gesamtmenge kleiner als bereits abgerufen ({called}).", 409)
            changes = {k: v for k, v in fields.items() if fo.get(k) != v}
            fo.update(fields, status=status, updatedAt=stamp)
            _demand_log(fo, user, stamp, "geändert", changes=changes, status=status)
        else:
            fo = {"id": "fo_"+secrets.token_hex(8), **fields, "status": status, "createdAt": stamp, "createdBy": user["username"]}
            _demand_log(fo, user, stamp, "angelegt")
            state["frameOrders"].append(fo)
        result.update(frameOrderId=fo["id"], departmentId=fo["departmentId"])
    elif action == "call-off-create":
        fo = _find(state["frameOrders"], body.get("frameOrderId"), "MP-DEM-010", "Rahmenauftrag")
        if fo.get("status") != "open":
            raise ProductionError("MP-DEM-011", "Rahmenauftrag ist abgeschlossen.", 409)
        qty, due = _demand_qty(body, "qty"), _demand_date(body, "dueDate", True)
        remaining = int(fo["totalQty"]) - sum(int(c.get("qty") or 0) for c in state["callOffs"] if c.get("frameOrderId") == fo["id"])
        if qty > remaining:
            raise ProductionError("MP-DEM-012", f"Abrufmenge {qty} größer als Rahmenrest {remaining}.", 409)
        seq = 1 + sum(1 for c in state["callOffs"] if c.get("frameOrderId") == fo["id"])
        c = {"id": "co_"+secrets.token_hex(8), "frameOrderId": fo["id"], "number": _demand_text(body, "number", 60) or f"{fo['number']}-{seq:03d}",
             "articleId": fo["articleId"], "departmentId": fo["departmentId"], "qty": qty, "dueDate": due,
             "reservedQty": 0, "deliveredQty": 0, "status": "open", "createdAt": stamp, "createdBy": user["username"]}
        _demand_log(c, user, stamp, "angelegt", qty=qty)
        state["callOffs"].append(c)
        result.update(callOffId=c["id"], departmentId=c["departmentId"])
    elif action in {"call-off-update", "call-off-cancel"}:
        c = _find(state["callOffs"], body.get("callOffId"), "MP-DEM-020", "Abruf")
        if c.get("status") != "open":
            raise ProductionError("MP-DEM-021", "Abruf ist nicht mehr offen.", 409)
        delivered = int(c.get("deliveredQty") or 0)
        if action == "call-off-cancel":
            new_qty, due = delivered, c["dueDate"]
        else:
            new_qty, due = _demand_qty(body, "qty"), _demand_date(body, "dueDate") or c["dueDate"]
            if new_qty < delivered:
                raise ProductionError("MP-DEM-022", f"Abrufmenge kleiner als bereits geliefert ({delivered}).", 409)
            fo = _find(state["frameOrders"], c["frameOrderId"], "MP-DEM-010", "Rahmenauftrag")
            remaining = int(fo["totalQty"]) - sum(int(x.get("qty") or 0) for x in state["callOffs"] if x.get("frameOrderId") == fo["id"] and x is not c)
            if new_qty > remaining:
                raise ProductionError("MP-DEM-012", f"Abrufmenge {new_qty} größer als Rahmenrest {remaining}.", 409)
        excess = int(c.get("reservedQty") or 0) - (new_qty - delivered)
        if excess > 0:
            inv = _inventory_for(state, str(c["articleId"]), str(c["departmentId"]))
            c["reservedQty"] -= excess
            inv["reservedQty"] = int(inv.get("reservedQty") or 0) - excess
        # Noch nicht begonnene FA auf den neuen Bedarf kürzen; laufende FA produzieren in den freien Bestand.
        need = max(0, new_qty - delivered - int(c.get("reservedQty") or 0))
        planned = [x for x in state["workSteps"] if str(x.get("callOffId")) == c["id"]]
        surplus = sum(max(0, int(x.get("targetQty") or 0) - int(x.get("scrapQty") or 0)) for x in planned) - need
        for step in sorted(planned, key=lambda x: str(x.get("createdAt") or ""), reverse=True):
            if surplus <= 0:
                break
            if step.get("status") != "planned":
                continue
            cut = min(surplus, int(step.get("targetQty") or 0))
            surplus -= cut
            if cut == int(step.get("targetQty") or 0):
                if any(step["id"] in (x.get("predecessorIds") or []) for x in state["workSteps"]):
                    raise ProductionError("MP-DEM-023", f"FA {step.get('fa')} ist Vorgänger eines anderen FA und kann nicht entfallen.", 409)
                state["workSteps"].remove(step)
                result.setdefault("removedFa", []).append(step.get("fa"))
            else:
                step["targetQty"] = int(step["targetQty"]) - cut
                result.setdefault("reducedFa", []).append(step.get("fa"))
        old_qty = c["qty"]
        c.update(qty=new_qty, dueDate=due)
        if action == "call-off-cancel":
            c["status"] = "cancelled"
        elif new_qty == delivered:
            c["status"] = "delivered"
        _demand_log(c, user, stamp, "storniert" if action == "call-off-cancel" else "geändert", qty=new_qty, before=old_qty)
        result.update(callOffId=c["id"], departmentId=c["departmentId"])
    elif action == "reserve":
        c = _find(state["callOffs"], body.get("callOffId"), "MP-DEM-020", "Abruf")
        if c.get("status") != "open":
            raise ProductionError("MP-DEM-021", "Abruf ist nicht mehr offen.", 409)
        taken = _reserve_call_off(state, c)
        _demand_log(c, user, stamp, "reserviert", qty=taken)
        result.update(callOffId=c["id"], departmentId=c["departmentId"], reservedQty=taken)
    elif action == "fa-create":
        fa = _demand_text(body, "fa", 60, True)
        if body.get("callOffId"):
            c = _find(state["callOffs"], body["callOffId"], "MP-DEM-020", "Abruf")
            if c.get("status") != "open":
                raise ProductionError("MP-DEM-021", "Abruf ist nicht mehr offen.", 409)
            taken = _reserve_call_off(state, c)
            qty = call_off_demand(state, c)
            if qty <= 0:
                raise ProductionError("MP-DEM-030", "Kein offener Produktionsbedarf (Bestand reserviert bzw. FA bereits angelegt).", 409)
            fo = _find(state["frameOrders"], c["frameOrderId"], "MP-DEM-010", "Rahmenauftrag")
            step = _demand_fa(state, stamp, "FRAME_ORDER", c["id"], c["departmentId"], c["articleId"],
                              fo.get("description") or f"Abruf {c['number']} · {fo['customer']}", qty,
                              _demand_date(body, "dueDate") or c["dueDate"], fa, {"frameOrderId": fo["id"], "callOffId": c["id"]})
            _demand_log(c, user, stamp, "FA angelegt", fa=fa, qty=qty, reserved=taken)
            result.update(callOffId=c["id"], reservedQty=taken)
        elif body.get("inventoryId"):
            inv = _find(state["inventory"], body["inventoryId"], "MP-DEM-050", "Bestand")
            qty = stock_demand(state, inv)
            if qty <= 0:
                raise ProductionError("MP-DEM-030", "Kein Bestandsbedarf (Sollbestand erreicht bzw. FA bereits angelegt).", 409)
            if str(inv.get("departmentId")) not in production_departments:
                raise ProductionError("MP-DEM-004", "Bestand gehört zu keinem aktiven Produktionsbereich.")
            step = _demand_fa(state, stamp, "STOCK_REQUIREMENT", inv["id"], str(inv["departmentId"]), str(inv["articleId"]),
                              str(inv.get("description") or f"Bestand {inv['articleId']}"), qty,
                              _demand_date(body, "dueDate"), fa, {"stockRequirementId": inv["id"]})
        else:
            raise ProductionError("MP-DEM-031", "Abruf oder Bestand angeben.")
        result.update(orderId=step["id"], fa=fa, qty=step["targetQty"], departmentId=step["departmentId"])
    elif action == "deliver":
        c = _find(state["callOffs"], body.get("callOffId"), "MP-DEM-020", "Abruf")
        if c.get("status") != "open":
            raise ProductionError("MP-DEM-021", "Abruf ist nicht mehr offen.", 409)
        reserved = int(c.get("reservedQty") or 0)
        qty = body.get("qty", reserved)
        if not _nonnegative_int(qty) or qty <= 0 or qty > reserved:
            raise ProductionError("MP-DEM-032", f"Liefermenge muss zwischen 1 und der reservierten Menge ({reserved}) liegen.", 409)
        inv = _inventory_for(state, str(c["articleId"]), str(c["departmentId"]))
        inv["physicalQty"] = int(inv["physicalQty"]) - qty
        inv["reservedQty"] = int(inv["reservedQty"]) - qty
        c["reservedQty"] = reserved - qty
        c["deliveredQty"] = int(c.get("deliveredQty") or 0) + qty
        if c["deliveredQty"] >= int(c["qty"]):
            c["status"] = "delivered"
        _demand_log(c, user, stamp, "geliefert", qty=qty)
        result.update(callOffId=c["id"], departmentId=c["departmentId"], qty=qty)
    elif action == "stock-save":
        if body.get("inventoryId"):
            inv = _find(state["inventory"], body["inventoryId"], "MP-DEM-050", "Bestand")
        else:
            department = _demand_text(body, "departmentId", 80, True)
            if department not in production_departments:
                raise ProductionError("MP-DEM-004", "Bestand braucht einen aktiven Produktionsbereich.")
            inv = _inventory_for(state, _demand_text(body, "articleId", 80, True), department, create=True)
        before = {k: inv.get(k) for k in ("physicalQty", "targetQty", "description")}
        if "physicalQty" in body:
            physical = _demand_qty(body, "physicalQty", positive=False)
            if physical < int(inv.get("reservedQty") or 0):
                raise ProductionError("MP-DEM-051", f"Physischer Bestand kleiner als reserviert ({inv.get('reservedQty')}).", 409)
            inv["physicalQty"] = physical
        if "targetQty" in body:
            inv["targetQty"] = _demand_qty(body, "targetQty", positive=False)
        if "description" in body:
            inv["description"] = _demand_text(body, "description", 300)
        _demand_log(inv, user, stamp, "Bestand gepflegt", before=before)
        result.update(inventoryId=inv["id"], departmentId=inv["departmentId"])
    else:
        raise ProductionError("MP-DEM-000", "Unbekannte Bedarfsaktion.", 404)
    return result


def demand_book_completion(state, order, finished):
    """Fertig-/Abbruchmeldung eines Bedarfs-FA: Gutmenge in den Bestand, für den Abruf reservieren."""
    article = str(order.get("articleId") or order.get("articleNo") or "")
    if not article:
        raise ProductionError("MP-PROD-049", "Bestands-FA benötigt eine Artikelreferenz.")
    inv = _inventory_for(state, article, str(order.get("departmentId")), create=True)
    inv["physicalQty"] = int(inv.get("physicalQty") or 0) + int(order.get("goodQty") or 0)
    c = next((x for x in state.get("callOffs") or [] if str(x.get("id")) == str(order.get("callOffId") or "")), None)
    if c is not None and c.get("status") == "open":
        finished["reservedForCallOff"] = _reserve_call_off(state, c)


SCOPE_COLLECTIONS = {"productionEvents", "palletLabels", "inventory", "frameOrders", "callOffs", "machines", "workSteps", "history", "formats", "baseFormats", "machineBlocks", "yearRules", "weekRules", "employees", "personnelAssignments", "personnelAbsences", "weeklyEmployeeDeployments", "departmentStaffNeeds", "projects", "audit", "planVersions"}


def record_key(name, x):
    if x.get("id") is not None:
        return str(x["id"])
    if name in {"personnelAssignments", "personnelAbsences"}:
        return str(x.get("employeeId")) + "|" + str(x.get("date"))
    if name == "weeklyEmployeeDeployments":
        return str(x.get("employeeId")) + "|" + str(x.get("weekStart"))
    if name == "departmentStaffNeeds":
        return str(x.get("departmentId")) + "|" + str(x.get("weekStart"))
    return canonical({k: x.get(k) for k in ("machineId", "year", "week")})


def append_change_audit(old, new, user, revision, stamp):
    """Authoritative field diffs, also persisted in server_audit by the caller."""
    changes = []
    machine_depts = {str(m.get("id")): m.get("departmentId", "") for m in new.get("machines") or []}
    employee_depts = {str(e.get("id")): e.get("departmentId", "") for e in new.get("employees") or []}
    for collection in ("workSteps", "projects", "employees", "personnelAssignments", "personnelAbsences", "weeklyEmployeeDeployments", "machines", "palletTemplates"):
        before = {record_key(collection, x): x for x in old.get(collection) or []}
        after = {record_key(collection, x): x for x in new.get(collection) or []}
        for rid in sorted(set(before) | set(after)):
            a, b = before.get(rid) or {}, after.get(rid) or {}
            rec = b or a
            for field in sorted(set(a) | set(b)):
                if field == "id" or canonical(a.get(field)) == canonical(b.get(field)):
                    continue
                previous, current = a.get(field), b.get(field)
                if collection == "projects" and field in {"processes", "log"}:
                    left = _record_map(previous)
                    right = _record_map(current)
                    ids = {i for i in set(left) | set(right) if canonical(left.get(i)) != canonical(right.get(i))}
                    previous = [left[i] for i in sorted(ids) if i in left]
                    current = [right[i] for i in sorted(ids) if i in right]
                changes.append({"id": "a_"+secrets.token_hex(10), "ts": stamp, "actor": user["username"], "role": user["role"], "departmentId": rec.get("departmentId") or machine_depts.get(str(rec.get("machineId"))) or employee_depts.get(str(rec.get("employeeId"))) or user.get("department_id") or "", "collection": collection, "recordId": rid, "fa": rec.get("fa", ""), "projectId": rec.get("projectId") or (rid if collection == "projects" else ""), "field": field, "old": previous, "new": current, "action": "Feld geändert", "detail": f"{collection}/{rid}: {field}", "revision": revision})
    new["audit"] = [*reversed(changes), *(new.get("audit") or [])][:AUDIT_CAP]
    return changes


def read_scope(state, user):
    role = str(user.get("role") or "")
    own = str(user.get("department_id") or user.get("departmentId") or "")
    if role not in SCOPED_ROLES or (role == "viewer" and not own):
        return None
    mids = {str(m["id"]) for m in state.get("machines") or [] if str(m.get("departmentId")) == own}
    eids = {str(e["id"]) for e in state.get("employees") or [] if str(e.get("departmentId")) == own}
    eids |= {str(x.get("employeeId")) for x in state.get("weeklyEmployeeDeployments") or [] if str(x.get("departmentId")) == own}
    pids = {str(x.get("projectId")) for x in [*(state.get("workSteps") or []), *(state.get("history") or [])] if str(x.get("departmentId")) == own}
    def visible(name, x):
        if not own or not isinstance(x, dict):
            return False
        if name == "planVersions":
            return False
        if name == "projects":
            return str(x.get("id")) in pids or any(str(pr.get("areaId")) == own for pr in x.get("processes") or [])
        if name == "employees":
            return str(x.get("id")) in eids
        if name == "audit":
            return str(x.get("departmentId")) == own or str(x.get("actor") or x.get("user")) == str(user.get("username"))
        if name in {"personnelAssignments", "personnelAbsences", "weeklyEmployeeDeployments"}:
            return str(x.get("employeeId")) in eids
        return str(x.get("departmentId")) == own or (not x.get("departmentId") and str(x.get("machineId")) in mids)
    return visible


def redact_state(state: dict, user: dict) -> dict:
    """Kopie des Datenstands für diesen Benutzer (Abwesenheitsgrund ggf. ausgeblendet)."""
    visible = _absence_visible(state, user)
    out = dict(state)
    scoped = read_scope(state, user)
    if scoped:
        for key in SCOPE_COLLECTIONS:
            out[key] = [dict(x) for x in state.get(key) or [] if scoped(key, x)]
        own = str(user.get("department_id") or user.get("departmentId") or "")
        out["projects"] = [{**p, "processes": [pr for pr in p.get("processes") or [] if str(pr.get("areaId")) == own], "log": [x for x in p.get("log") or [] if str(x.get("actor")) == str(user.get("username"))]} for p in out["projects"]]
        # Dependency status alone permits cross-department handoff without foreign order data.
        needed = {str(i) for x in out["workSteps"] for i in x.get("predecessorIds") or []}
        out["dependencyStatus"] = {str(x.get("id")): str(x.get("status") or "planned") for x in state.get("workSteps") or [] if str(x.get("id")) in needed}
        out["dependencyStatus"].update({str(x.get("originalOrderId")): "done" for x in state.get("history") or [] if str(x.get("originalOrderId")) in needed and x.get("recordType", "done") == "done"})
    out["personnelAbsences"] = [a if not isinstance(a, dict) or visible(a) else _masked_absence(a)
                                for a in (out.get("personnelAbsences") or [])]
    if user.get("role") == "production":
        # Production follows its own FA plan; commercial and cross-area plans
        # are not needed in this read-only project view.
        out["projects"] = [{k: v for k, v in p.items() if k not in {"customerPlan", "offer", "development"}}
                           for p in out.get("projects") or []]
    out["audit"] = [_masked_audit(a) if isinstance(a, dict) and user.get("role") not in ABSENCE_FULL_ROLES else a for a in (out.get("audit") or [])]
    restrict_state_for_rights(out, user)
    return out


def unredact_incoming(old: dict, incoming: dict, user: dict) -> None:
    """Unverändert zurückgeschickte, ausgeblendete Datensätze durch den echten Stand ersetzen.

    Geänderte Datensätze bleiben wie gesendet; darüber entscheiden die Rechteprüfungen.
    """
    incoming.pop("dependencyStatus", None)
    for function, keys in FUNCTION_HIDDEN.items():
        if user_level(user, function) == "none":
            for key in keys:
                incoming[key] = json.loads(json.dumps(old.get(key) or []))
    scoped = read_scope(old, user)
    if scoped:
        projected = redact_state(old, user)
        for key in SCOPE_COLLECTIONS:
            sent = incoming.get(key)
            if not isinstance(sent, list):
                continue
            keys = {record_key(key, x) for x in sent if isinstance(x, dict)}
            sent += [x for x in old.get(key) or [] if not scoped(key, x) and record_key(key, x) not in keys]
        old_projects = _record_map(old.get("projects"))
        for p in incoming.get("projects") or []:
            before = old_projects.get(str(p.get("id")))
            shown = next((x for x in projected.get("projects") or [] if x.get("id") == p.get("id")), None)
            if before and shown:
                own = str(user.get("department_id") or "")
                shown_ids = {str(x.get("id")) for x in shown.get("log") or []}
                sent_log = p.get("log", [])
                sent_ids = {str(x.get("id")) for x in sent_log}
                p["log"] = [*[x for x in before.get("log") or [] if str(x.get("id")) not in shown_ids and str(x.get("id")) not in sent_ids], *sent_log]
                p["processes"] = [*p.get("processes", []), *[pr for pr in before.get("processes") or [] if str(pr.get("areaId")) != own and str(pr.get("id")) not in {str(x.get("id")) for x in p.get("processes", [])}]]
    visible = _absence_visible(old, user)
    old_abs = {(str(a.get("employeeId")), str(a.get("date"))): a for a in (old.get("personnelAbsences") or []) if isinstance(a, dict)}
    absences = incoming.get("personnelAbsences")
    if isinstance(absences, list):
        restored = []
        for a in absences:
            if isinstance(a, dict):
                before = old_abs.get((str(a.get("employeeId")), str(a.get("date"))))
                if before is not None and not visible(before) and canonical(_masked_absence(before)) == canonical(a):
                    a = before
            restored.append(a)
        incoming["personnelAbsences"] = restored
    old_audit = {str(a.get("id")): a for a in (old.get("audit") or []) if isinstance(a, dict) and a.get("id")}
    audit = incoming.get("audit")
    if isinstance(audit, list):
        restored = []
        for a in audit:
            if isinstance(a, dict):
                before = old_audit.get(str(a.get("id")))
                if before is not None and canonical(_masked_audit(before)) == canonical(a):
                    a = before
            restored.append(a)
        incoming["audit"] = restored


def prune_sessions(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))


def _login_keys(ip: str, username: str) -> list[tuple[str, int]]:
    u = str(username or "").strip().lower()[:80]
    return [
        ("ip:" + ip, LOGIN_MAX_PER_IP),
        ("user:" + u, LOGIN_MAX_PER_USER),
        ("ipuser:" + ip + "|" + u, LOGIN_MAX_PER_IP_USER),
    ]


def _prune_login_fails(now: float) -> None:
    """Abgelaufene Versuche und leere Schlüssel entfernen (hält LOGIN_FAILS klein)."""
    for key in list(LOGIN_FAILS):
        xs = [t for t in LOGIN_FAILS[key] if now - t < LOGIN_WINDOW]
        if xs:
            LOGIN_FAILS[key] = xs
        else:
            del LOGIN_FAILS[key]


def login_attempt_reserve(ip: str, username: str) -> float | None:
    """Prüft die Sperre und zählt den Versuch atomar vorab als Fehlversuch.

    Rückgabe: Zeitstempel der Reservierung, None = gesperrt. Parallele Anfragen
    während der Passwortprüfung (~0,2 s) zählen dadurch alle mit.
    """
    now = time.time()
    keys = _login_keys(ip, username)
    with LOGIN_LOCK:
        _prune_login_fails(now)
        if any(len(LOGIN_FAILS.get(key, [])) >= limit for key, limit in keys):
            return None
        for key, _ in keys:
            LOGIN_FAILS.setdefault(key, []).append(now)
        return now


def login_attempt_succeeded(ip: str, username: str, stamp: float) -> None:
    """Erfolg: nur die Zähler dieses Benutzers löschen; frühere Fehlversuche der IP bleiben."""
    ip_key, user_key, ip_user_key = (k for k, _ in _login_keys(ip, username))
    with LOGIN_LOCK:
        LOGIN_FAILS.pop(user_key, None)
        LOGIN_FAILS.pop(ip_user_key, None)
        xs = LOGIN_FAILS.get(ip_key)
        if xs and stamp in xs:
            xs.remove(stamp)


def client_ip_allowed(ip: str) -> bool:
    if ALLOWED_NETWORK is None:
        return False
    try:
        return ipaddress.ip_address(ip) in ALLOWED_NETWORK
    except ValueError:
        return False


def _local_host_names() -> set[str]:
    names = set()
    for fn in (socket.gethostname, socket.getfqdn):
        try:
            n = str(fn() or "").strip().lower().rstrip(".")
        except OSError:
            n = ""
        if n:
            names.add(n)
            names.add(n.split(".", 1)[0])
    return names


LOCAL_HOST_NAMES = _local_host_names()


def host_name_allowed(host: str, bind_ip: str) -> bool:
    """Schutz gegen DNS-Rebinding: nur Server-IP, eigener PC-Name (auch mit DNS-Suffix) oder MP_ALLOWED_HOSTS."""
    h = str(host or "").strip().lower().rstrip(".")
    if not h:
        return False
    if h.startswith("["):
        h = h[1:].split("]", 1)[0]
    if h == str(bind_ip).lower() or h in EXTRA_ALLOWED_HOSTS or h in LOCAL_HOST_NAMES:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        ip = None
    if ip is not None:
        try:
            bind = ipaddress.ip_address(bind_ip)
        except ValueError:
            return False
        # Server an 0.0.0.0 (nur Testbetrieb): jede IP-Adresse ist ein direkter Aufruf, kein Rebinding.
        return ip == bind or bind.is_unspecified or (ip.is_loopback and bind.is_loopback)
    if h == "localhost":
        try:
            return ipaddress.ip_address(bind_ip).is_loopback
        except ValueError:
            return False
    # PC-Name mit beliebigem DNS-Suffix (pc01.firma.local), sofern der erste Teil der eigene Name ist.
    return h.split(".", 1)[0] in LOCAL_HOST_NAMES and "." in h


def split_host_port(value: str) -> tuple[str, int | None]:
    v = str(value or "").strip()
    if v.startswith("["):
        host, _, rest = v[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif v.count(":") == 1:
        host, _, port = v.partition(":")
    else:
        host, port = v, ""
    try:
        return host, (int(port) if port else None)
    except ValueError:
        return host, -1


class MPHTTPServer(ThreadingHTTPServer):
    # 30 gleichzeitige Browser + kurze Bursts bei Login/Reload/Long-Poll-Reconnect.
    request_queue_size = 128
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, ssl_context=None, **kwargs):
        self._conn_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.ssl_context = ssl_context
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        # V12.10.2: begrenzte Threadzahl – Verbindungen über dem Limit werden sofort geschlossen.
        if not self._conn_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._conn_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            if self.ssl_context is not None:
                # V12.21.0: TLS-Handshake im Verbindungs-Thread mit Zeitlimit, damit ein langsamer
                # oder fehlerhafter Client die Annahme weiterer Verbindungen nicht blockiert.
                try:
                    request.settimeout(15)
                    request = self.ssl_context.wrap_socket(request, server_side=True)
                except (OSError, ValueError):
                    self.shutdown_request(request)
                    return
            super().process_request_thread(request, client_address)
        finally:
            self._conn_slots.release()


# ---------------------------------------------------------------------------------------------
# V12.16.0: Branchenvorlagen (vorlage_<id>.json im Paket). Eine Vorlage wird nur auf Wunsch (Admin) angewendet und
# ERGÄNZT nur: vorhandene Bereiche, Maschinen, Schichten und Abläufe bleiben unverändert, nichts wird gelöscht.
# Die Vorlage enthält keine Firmen-/Personendaten.
# ---------------------------------------------------------------------------------------------
TEMPLATE_FILE = re.compile(r"^vorlage_([a-z0-9_]{1,30})\.json$")
TEMPLATE_MACHINE_KEYS = ("kind", "crew", "lanes", "setupMinutes", "defaultShiftMode", "staffRequired")


def load_templates() -> dict:
    out = {}
    for p in sorted(BASE.glob("vorlage_*.json")):
        m = TEMPLATE_FILE.match(p.name)
        if not m:
            continue
        try:
            t = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(t, dict) and t.get("id") == m.group(1) and isinstance(t.get("name"), str):
            out[t["id"]] = t
    return out


def _tpl_list(t: dict, key: str) -> list:
    v = t.get(key)
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def template_overview() -> list[dict]:
    out = []
    for t in load_templates().values():
        out.append({
            "id": t["id"], "name": t["name"], "description": str(t.get("description") or ""),
            "departments": [{"id": d.get("id"), "name": d.get("name"), "planningType": d.get("planningType")} for d in _tpl_list(t, "departments")],
            "machines": len(_tpl_list(t, "machines")),
            "processTemplates": [x.get("name") for x in _tpl_list(t, "processTemplates")],
            "modules": {k: bool(v) for k, v in (t.get("modules") or {}).items() if k in CONFIG_MODULES} if isinstance(t.get("modules"), dict) else {},
        })
    return out


def template_merge(state: dict, tpl: dict) -> tuple[dict, dict]:
    """Liefert (neuer Datenstand, Zusammenfassung). Der übergebene Stand wird nicht verändert."""
    new = json.loads(json.dumps(state))
    summary = {"departments": [], "machines": [], "shiftTemplates": [], "processTemplates": [], "skipped": 0}
    deps = new.setdefault("departments", [])
    dep_ids = {str(d.get("id")) for d in deps if isinstance(d, dict)}
    dep_names = {str(d.get("name") or "").strip().casefold() for d in deps if isinstance(d, dict)}
    added_deps = set()
    for d in _tpl_list(tpl, "departments"):
        did, name = str(d.get("id") or ""), str(d.get("name") or "").strip()
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", did) or did in dep_ids or did in PROJECT_FIXED_AREA_IDS
                or name.casefold() in dep_names or d.get("planningType") not in {"MACHINE", "LABOR_HOURS", "PROCESS", "CYCLE"}):
            summary["skipped"] += 1
            continue
        rec = {"id": did, "name": name[:60], "planningType": d["planningType"], "active": True}
        for flag in ("formats", "sharedOperators"):
            if d.get(flag) is True:
                rec[flag] = True
        deps.append(rec)
        dep_ids.add(did)
        dep_names.add(name.casefold())
        added_deps.add(did)
        summary["departments"].append(name)
    machines = new.setdefault("machines", [])
    mids = {str(m.get("id")) for m in machines if isinstance(m, dict)}
    monday = datetime.now().date() - timedelta(days=datetime.now().weekday())
    for m in _tpl_list(tpl, "machines"):
        mid = str(m.get("id") or "")
        if str(m.get("departmentId") or "") not in added_deps:
            continue     # Maschinen nur für Bereiche, die diese Anwendung neu anlegt
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", mid) or mid in mids:
            summary["skipped"] += 1
            continue
        rec = {"id": mid, "name": str(m.get("name") or mid)[:60], "departmentId": str(m["departmentId"]), "setupMinutes": 0,
               "start": f"{monday.isoformat()}T06:30", "committedUntil": "", "defaultShiftMode": "1", "staffRequired": 1, "effortScaling": False, "crewMax": 0, "laneStaff": {}}
        rec.update({k: m[k] for k in TEMPLATE_MACHINE_KEYS if k in m})
        machines.append(rec)
        mids.add(mid)
        summary["machines"].append(rec["name"])
    if isinstance(tpl.get("shiftTemplates"), dict):
        st = new.setdefault("shiftTemplates", {})
        for k, v in tpl["shiftTemplates"].items():
            if k not in st and isinstance(v, dict):
                st[k] = json.loads(json.dumps(v))
                summary["shiftTemplates"].append(k)
    areas = {a["id"] for a in (current_config().get("projectAreas") or CONFIG_PROJECT_AREAS)} | dep_ids
    pts = new.setdefault("processTemplates", [])
    pids = {str(x.get("id")) for x in pts if isinstance(x, dict)}
    pnames = {(str(x.get("kind")), str(x.get("name") or "").strip().casefold()) for x in pts if isinstance(x, dict)}
    for t in _tpl_list(tpl, "processTemplates"):
        key = (str(t.get("kind")), str(t.get("name") or "").strip().casefold())
        if str(t.get("id")) in pids or key in pnames or any(str(st.get("areaId")) not in areas for st in t.get("steps") or [] if isinstance(st, dict)):
            summary["skipped"] += 1
            continue
        pts.append(json.loads(json.dumps(t)))
        pids.add(str(t.get("id")))
        pnames.add(key)
        summary["processTemplates"].append(str(t.get("name")))
    return new, summary


def template_apply(user: dict, body: dict) -> tuple[int, dict]:
    tid = str(body.get("id") or "")
    tpl = load_templates().get(tid)
    if not tpl:
        return 404, mp_error("MP-TPL-040", "Vorlage nicht gefunden.")
    dry = body.get("dryRun") is True
    want_modules = body.get("modules") is True
    with DB_LOCK, db_session() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT revision,json FROM state WHERE id=1").fetchone()
        old = json.loads(row["json"])
        new, summary = template_merge(old, tpl)
        changed = canonical({k: v for k, v in new.items() if k != "meta"}) != canonical({k: v for k, v in old.items() if k != "meta"})
        cfg_now = current_config()
        tmods = tpl.get("modules") if isinstance(tpl.get("modules"), dict) else {}
        mod_changes = {k: bool(v) for k, v in tmods.items() if k in CONFIG_MODULES and (cfg_now.get("modules") or {}).get(k, CONFIG_MODULE_DEFAULTS[k]) != bool(v)} if want_modules else {}
        summary["modules"] = mod_changes
        # V12.17.0: Projektbereiche der Vorlage werden nur ergänzt (nie entfernt oder umbenannt).
        have_pa = cfg_now.get("projectAreas") or CONFIG_PROJECT_AREAS
        have_ids = {a["id"] for a in have_pa}
        add_pa = [{"id": a["id"], "name": a["name"]} for a in _tpl_list(tpl, "projectAreas")
                  if isinstance(a.get("id"), str) and re.fullmatch(r"[a-z0-9_-]{1,30}", a["id"]) and a["id"] not in have_ids
                  and isinstance(a.get("name"), str) and 0 < len(a["name"]) <= 60 and a["id"] not in {d["id"] for d in new.get("departments", []) if isinstance(d, dict)}]
        summary["projectAreas"] = [a["name"] for a in add_pa]
        if dry or (not changed and not mod_changes and not add_pa):
            con.execute("ROLLBACK")
            return 200, {"ok": True, "dryRun": dry, "unchanged": not changed and not mod_changes and not add_pa, "summary": summary, "revision": row["revision"]}
        revision = row["revision"]
        if changed:
            ok, code, reason = validate_state(old, new)
            if not ok:
                con.execute("ROLLBACK")
                return 400, mp_error(code, reason)
            revision = row["revision"] + 1
            new.setdefault("meta", {})
            new["meta"]["serverRevision"] = revision
            new["meta"]["actor"] = user["username"]
            new["meta"]["storage"] = "server"
            ts = now_iso()
            con.execute("UPDATE state SET revision=?,json=?,updated_at=?,updated_by=? WHERE id=1",
                        (revision, json.dumps(new, ensure_ascii=False, separators=(",", ":")), ts, user["username"]))
            con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,?)",
                        (ts, user["username"], "Vorlage angewendet", tid, revision))
        con.execute("COMMIT")
    if mod_changes or add_pa:
        body_cfg = {"revision": config_revision()}
        if mod_changes:
            body_cfg["modules"] = mod_changes
        if add_pa:
            body_cfg["projectAreas"] = [dict(a) for a in have_pa] + add_pa
        status, payload, _detail = config_apply(body_cfg)
        if status != 200:
            return status, payload
    return 200, {"ok": True, "summary": summary, "revision": revision}


class Handler(BaseHTTPRequestHandler):
    server_version = f"ProduktionsplanungV{APP_VERSION}/1.0"
    # V12.10.2: Socket-Timeout gegen langsame bzw. hängende Verbindungen (Slowloris).
    # Long-Poll (/api/revision) wartet serverseitig max. 15 s und liest dabei nicht vom Socket.
    timeout = 30

    def handle(self):
        # Defense in depth: clients outside the configured LAN subnet are
        # dropped before HTTP parsing, even if a firewall/router rule is wrong.
        if not client_ip_allowed(self.client_address[0]):
            try:
                self.request.close()
            except Exception:
                pass
            return
        try:
            return super().handle()
        except ConnectionError:
            # V12.17.2: ConnectionAbortedError (WinError 10053), ConnectionResetError (10054), BrokenPipeError.
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

    def read_json(self, limit: int = MAX_BODY) -> dict:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.close_connection = True
            raise ValueError("Ungültige Content-Length")
        if n <= 0 or n > limit:
            # Body bleibt ungelesen -> Verbindung nach der Antwort schließen.
            self.close_connection = True
            raise ValueError("Ungültige oder zu große Anfrage")
        raw = self.rfile.read(n)
        def reject_constant(value):
            raise ValueError(f"Nicht standardkonforme JSON-Zahl: {value}")
        obj = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
        if not isinstance(obj, dict):
            raise ValueError("JSON-Objekt erwartet")
        return obj

    def server_action(self, user, body, path, label, apply, replay_guard=None, validate=False):
        """Autoritative Serveraktion (Produktion, Bedarf): eine Transaktion, idempotent je Benutzer + Request-ID."""
        request_id = body.get("requestId")
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", request_id):
            return self.json_response(400, mp_error("MP-PROD-050", "Stabile Request-ID erforderlich."))
        fingerprint = canonical({"path": path, "body": {k: v for k, v in body.items() if k != "revision"}})
        try:
            with DB_LOCK, db_session() as con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT json,revision FROM state WHERE id=1").fetchone()
                state = json.loads(row["json"])
                previous = con.execute("SELECT fingerprint,result FROM production_requests WHERE username=? AND request_id=?", (user["username"], request_id)).fetchone()
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise ProductionError("MP-PROD-050", "Request-ID wurde bereits anders verwendet.", 409)
                    result, revision = json.loads(previous["result"]), row["revision"]
                    if replay_guard:
                        replay_guard(result)
                else:
                    if body.get("revision") is not None and body["revision"] != row["revision"]:
                        raise ProductionError("MP-SYNC-001", "Revision veraltet.", 409)
                    old = json.loads(row["json"])
                    if validate:
                        # Wie beim Speichern: beide Seiten normalisieren, sonst gälten ergänzte Kanonfelder als Änderung.
                        normalize_fa_state(old)
                        normalize_fa_state(state)
                    result = apply(state)
                    if validate:
                        ok, code, reason = validate_state(old, state)
                        if not ok:
                            raise ProductionError(code, reason, 400)
                    revision = row["revision"]+1
                    state.setdefault("meta", {})["serverRevision"] = revision
                    changes = append_change_audit(old, state, user, revision, result["at"])
                    con.execute("UPDATE state SET json=?,revision=?,updated_at=?,updated_by=? WHERE id=1", (json.dumps(state, ensure_ascii=False), revision, now_iso(), user["username"]))
                    con.execute("INSERT INTO production_requests VALUES(?,?,?,?)", (user["username"], request_id, fingerprint, json.dumps(result, ensure_ascii=False)))
                    con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,?)", (now_iso(), user["username"], label, json.dumps({"event": result, "changes": changes}, ensure_ascii=False), revision))
                con.execute("COMMIT")
            with REVISION_CONDITION:
                REVISION_CONDITION.notify_all()
            return self.json_response(200, {"ok": True, "revision": revision, "data": redact_state(state, user), "event": result, "replayed": bool(previous)})
        except ProductionError as e:
            return self.json_response(e.status, mp_error(e.code, e.message))

    def save_role_profile(self, rid: str):
        user = self.require_user(["admin"])
        if not user or not self.require_current_client():
            return
        try:
            body = self.read_json()
            profile = validate_role_profile(body, rid)
        except ValueError as e:
            return self.json_response(400, mp_error("MP-ROLE-006", str(e)))
        with DB_LOCK, db_session() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT json FROM role_profiles WHERE id=?", (rid,)).fetchone()
            before = json.loads(row["json"]) if row else None
            assigned = con.execute("SELECT count(*) FROM users WHERE profile_id=?", (rid,)).fetchone()[0]
            if before and assigned and before.get("baseRole") != profile["baseRole"]:
                return self.json_response(409, mp_error("MP-ROLE-007", "Die Systemrolle einer zugewiesenen Rolle bleibt fest."))
            if any(p["name"].casefold() == profile["name"].casefold() and pid != rid for pid, p in role_profiles(con).items()):
                return self.json_response(409, mp_error("MP-ROLE-008", "Rollenname existiert bereits."))
            stamp = now_iso()
            profile.update(createdAt=(before or {}).get("createdAt", stamp), updatedAt=stamp, updatedBy=user["username"])
            con.execute("INSERT INTO role_profiles(id,json,updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET json=excluded.json,updated_at=excluded.updated_at", (rid, json.dumps(profile, ensure_ascii=False), stamp))
            if before and canonical(before) != canonical({**profile, "createdAt": before.get("createdAt"), "updatedAt": before.get("updatedAt"), "updatedBy": before.get("updatedBy")}):
                # Geänderte Rechte gelten sofort: betroffene Sitzungen neu anmelden lassen.
                con.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE profile_id=?)", (rid,))
            # V12.26.0 (#55): Audit mit alten und neuen Rechten (nur geänderte Einträge), damit Rechteänderungen nachvollziehbar sind.
            changes = {}
            for key in ("name", "baseRole", "active", "description"):
                if (before or {}).get(key) != profile.get(key):
                    changes[key] = {"alt": (before or {}).get(key), "neu": profile.get(key)}
            for group in ("rights", "actions"):
                old_g, new_g = (before or {}).get(group) or {}, profile.get(group) or {}
                for key in sorted(set(old_g) | set(new_g)):
                    if old_g.get(key) != new_g.get(key):
                        changes[f"{group}.{key}"] = {"alt": old_g.get(key), "neu": new_g.get(key)}
            detail = {"rolle": rid, "neu": before is None, "aenderungen": changes, "profil": profile}
            con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,NULL)", (stamp, user["username"], "Rolle angelegt" if before is None else "Rolle geändert", json.dumps(detail, ensure_ascii=False)))
        return self.json_response(200, {"ok": True, "profile": profile, "users": assigned})

    def require_rights(self, user, function=None, level="read", action=None) -> bool:
        """Rechte des Rollenprofils (#63) zusätzlich zur Systemrolle prüfen."""
        if function and ROLE_LEVELS.index(user_level(user, function)) < ROLE_LEVELS.index(level):
            self.json_response(403, mp_error("MP-ROLE-001", f"Rolle hat für „{dict(ROLE_FUNCTIONS)[function]}“ kein {'Bearbeitungs' if level == 'edit' else 'Lese'}recht."))
            return False
        if action and not user_action(user, action):
            self.json_response(403, mp_error("MP-ROLE-001", f"Aktion „{dict(ROLE_ACTIONS)[action]}“ ist für diese Rolle gesperrt."))
            return False
        return True

    def cookie_secure(self) -> str:
        return "; Secure" if getattr(self.server, "ssl_context", None) is not None else ""

    def request_origin_ok(self, write: bool) -> bool:
        """Host-Header (DNS-Rebinding) und bei Schreibzugriffen Origin prüfen."""
        bind_ip, bind_port = self.server.server_address[:2]
        host, port = split_host_port(self.headers.get("Host", ""))
        if not host_name_allowed(host, bind_ip) or port not in (None, bind_port):
            self.close_connection = True
            self.json_response(421, mp_error("MP-REQ-421", "Falsche Adresse. Bitte die Server-Adresse vom Admin nutzen."))
            return False
        if write:
            origin = str(self.headers.get("Origin", "") or "").strip()
            if origin and origin != "null":
                o = urlparse(origin)
                if o.scheme != ("https" if self.server.ssl_context is not None else "http") or not host_name_allowed(o.hostname or "", bind_ip) or (o.port or 80) != bind_port:
                    self.close_connection = True
                    self.json_response(403, mp_error("MP-REQ-403", "Anfrage von fremder Seite abgelehnt."))
                    return False
        return True

    def _guarded(self, fn, write: bool):
        if not self.request_origin_ok(write):
            return
        try:
            return fn()
        except (ConnectionError, socket.timeout):
            # V12.17.2: Client-Abbruch (Tab zu/neu geladen, Long-Poll laeuft noch) ist kein Serverfehler:
            # keine 500-Antwort auf die tote Verbindung, kein MP-SRV-500, nur eine Debug-Zeile.
            self.close_connection = True
            if MP_DEBUG_ABORTS:
                sys.stdout.write("DEBUG client abort %s %s\n" % (self.command, urlparse(self.path).path))
            return
        except Exception as e:
            # V12.10.2: unerwartete Fehler -> 500 mit Fehlercode statt Verbindungsabbruch.
            msg = " ".join(str(e).split())  # eine Logzeile, auch bei mehrzeiligen Windows-Meldungen
            sys.stderr.write(f"MP-SRV-500 {self.command} {urlparse(self.path).path}: {type(e).__name__}: {msg}\n")
            self.close_connection = True
            try:
                self.json_response(500, mp_error("MP-SRV-500", "Serverfehler. Bitte erneut versuchen."))
            except Exception:
                pass

    def do_GET(self):
        return self._guarded(self._do_GET, False)

    def do_POST(self):
        return self._guarded(self._do_POST, True)

    def do_PUT(self):
        return self._guarded(self._do_PUT, True)

    def do_PATCH(self):
        return self._guarded(self._do_PATCH, True)

    def session_user(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        token = c.get("mp_session")
        if not token:
            return None
        token_hash = hashlib.sha256(token.value.encode()).hexdigest()
        with DB_LOCK, db_session() as con:
            prune_sessions(con)
            row = con.execute(
                "SELECT u.id,u.username,u.role,u.department_id,u.active,u.profile_id,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?",
                (token_hash,),
            ).fetchone()
            if not row or not row["active"] or row["expires_at"] < int(time.time()):
                return None
            profile = None
            if row["profile_id"]:
                found = con.execute("SELECT json FROM role_profiles WHERE id=?", (row["profile_id"],)).fetchone()
                profile = json.loads(found["json"]) if found else None
                if not profile or not profile.get("active", True) or profile.get("baseRole") != row["role"]:
                    return None
            return attach_rights(dict(row), profile)

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
        if (BASE / "updates" / "installing").exists():
            self.json_response(503, mp_error("MP-UPD-003", "Update läuft. Änderungen sind bis zum Healthcheck gesperrt."))
            return False
        client_version = str(self.headers.get("X-MP-Client-Version", "")).strip()
        if client_version != APP_VERSION:
            self.json_response(426, mp_error("MP-SYNC-002", "Client-Version veraltet. Seite vollständig neu laden.", serverVersion=APP_VERSION))
            return False
        return True

    def _do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/updates":
            user = self.require_user(["admin"])
            if not user:
                return
            return self.json_response(200, update_manager().poll())
        if path == "/api/health":
            return self.json_response(200, {"ok": True, "version": APP_VERSION})
        if path == "/api/session":
            user = self.require_user()
            if not user:
                return
            return self.json_response(200, {"user": user_public(user)})
        if path == "/api/state":
            user = self.require_user()
            if not user:
                return
            with DB_LOCK, db_session() as con:
                row = con.execute("SELECT revision,json,updated_at,updated_by FROM state WHERE id=1").fetchone()
            return self.json_response(200, {"revision": row["revision"], "data": redact_state(json.loads(row["json"]), user), "updatedAt": row["updated_at"], "updatedBy": row["updated_by"]})
        if path.startswith("/api/chat/"):
            user = self.require_user()
            if not user:
                return
            if not self.require_rights(user, "chat"):
                return
            if not module_on("chat"):
                return self.json_response(403, module_error("chat"))
            with DB_LOCK, db_session() as con:
                status, payload = chat_get(con, user, path, parse_qs(parsed.query))
            return self.json_response(status, payload)
        if path.startswith("/api/notifications"):
            user = self.require_user()
            if not user:
                return
            if not self.require_rights(user, "notifications"):
                return
            if not module_on("notifications"):
                return self.json_response(403, module_error("notifications"))
            with DB_LOCK, db_session() as con:
                status, payload = notif_get(con, user, path, parse_qs(parsed.query))
            return self.json_response(status, payload)
        if path == "/api/config":
            user = self.require_user()
            if not user:
                return
            payload = public_config()
            if user["role"] == "admin" and not payload["setupDone"]:
                # V12.17.0: Passwortschritt des Assistenten nur, solange der Admin sein Startpasswort nie geändert hat.
                with DB_LOCK, db_session() as con:
                    ur = con.execute("SELECT created_at,updated_at FROM users WHERE id=?", (user["id"],)).fetchone()
                payload["adminPwUnchanged"] = bool(ur) and ur["created_at"] == ur["updated_at"]
            return self.json_response(200, payload)
        if path == "/api/config/logo":
            user = self.require_user()
            if not user:
                return
            return self.serve_logo()
        if path == "/api/templates":
            user = self.require_user(["admin"])
            if not user:
                return
            return self.json_response(200, {"templates": template_overview()})
        if path == "/api/history-archive":
            # Ältere Ist-Historie (nur lesen), neueste zuerst. Filter: from/to (YYYY-MM-DD, Fertigmeldung), limit/offset.
            user = self.require_user()
            if not user:
                return
            if not module_on("history"):
                return self.json_response(403, module_error("history"))
            if not self.require_rights(user, "history"):
                return
            qs = parse_qs(parsed.query)
            date_from, date_to = qs.get("from", [""])[0], qs.get("to", [""])[0]
            if (date_from and not _valid_date_key(date_from)) or (date_to and not _valid_date_key(date_to)):
                return self.json_response(400, mp_error("MP-HIST-010", "Zeitraum muss im Format JJJJ-MM-TT angegeben werden."))
            try:
                limit = max(1, min(2000, int(qs.get("limit", ["500"])[0])))
                offset = max(0, int(qs.get("offset", ["0"])[0]))
            except ValueError:
                return self.json_response(400, mp_error("MP-HIST-010", "limit/offset müssen Zahlen sein."))
            where, args = [], []
            if date_from:
                where.append("finished_at >= ?"); args.append(date_from)
            if date_to:
                # finished_at ist ein ISO-Zeitstempel; "bis" schließt den ganzen Tag ein.
                where.append("finished_at < ?"); args.append((datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"))
            own = str(user.get("department_id") or "")
            if user["role"] in SCOPED_ROLES and (own or user["role"] != "viewer"):
                if not own:
                    where.append("1=0")
                else:
                    where.append("COALESCE(NULLIF(json_extract(history_archive.json, '$.departmentId'), ''), (SELECT json_extract(value, '$.departmentId') FROM json_each((SELECT json FROM state WHERE id=1), '$.machines') WHERE json_extract(value, '$.id')=json_extract(history_archive.json, '$.machineId') LIMIT 1)) = ?")
                    args.append(own)
            clause = (" WHERE " + " AND ".join(where)) if where else ""
            with DB_LOCK, db_session() as con:
                total = con.execute(f"SELECT COUNT(*) FROM history_archive{clause}", args).fetchone()[0]
                rows = con.execute(f"SELECT json FROM history_archive{clause} ORDER BY finished_at DESC, id LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
            history = [json.loads(r["json"]) for r in rows]
            with DB_LOCK, db_session() as con:
                machines = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()[0]).get("machines", [])
            normalize_fa_state({"history": history, "machines": machines})
            return self.json_response(200, {"total": total, "offset": offset, "history": history})
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
            # V12.13.0: Benachrichtigungen im selben Long-Poll. Der Client nennt den zuletzt gesehenen Stand
            # (nsig); ändert er sich (neu/gelesen), endet die Wartezeit sofort.
            nsig = qs.get("nsig", [""])[0]
            if not re.fullmatch(r"\d{1,18}:\d{1,18}", nsig):
                nsig = ""   # V12.14.1: ungültige Signatur ignorieren, sonst käme der Poll nie zur Ruhe
            # V12.15.0: Firmenprofil-Revision (crev) im selben Long-Poll; Änderung beendet die Wartezeit.
            try:
                crev = int(qs.get("crev", [""])[0])
            except ValueError:
                crev = None
            deadline = time.monotonic() + wait_ms / 1000.0
            with REVISION_CONDITION:
                while True:
                    with DB_LOCK, db_session() as con:
                        row = con.execute("SELECT revision,updated_at,updated_by FROM state WHERE id=1").fetchone()
                        notif = notif_sig(con, user["username"])
                    if int(row["revision"]) != since or wait_ms <= 0 or (nsig and nsig != notif["sig"]) or (crev is not None and crev != config_revision()):
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    REVISION_CONDITION.wait(remaining)
            return self.json_response(200, {"revision": row["revision"], "updatedAt": row["updated_at"], "updatedBy": row["updated_by"], "version": APP_VERSION, "notif": notif, "cfg": config_revision()})
        if path == "/api/roles":
            user = self.require_user(["admin"])
            if not user:
                return
            with DB_LOCK, db_session() as con:
                profiles = role_profiles(con)
                usage = {r["profile_id"]: r["n"] for r in con.execute("SELECT profile_id,count(*) n FROM users WHERE profile_id<>'' GROUP BY profile_id")}
            return self.json_response(200, {"functions": [list(x) for x in ROLE_FUNCTIONS], "actions": [list(x) for x in ROLE_ACTIONS],
                                            "profiles": [{**p, "users": usage.get(pid, 0)} for pid, p in profiles.items()]})
        if path == "/api/users":
            user = self.require_user(USER_MANAGER_ROLES)
            if not user:
                return
            if not self.require_rights(user, action="userAdmin"):
                return
            with DB_LOCK, db_session() as con:
                if user["role"] == "admin":
                    rows = con.execute("SELECT id,username,role,department_id,active,created_at,updated_at,profile_id FROM users ORDER BY username COLLATE NOCASE").fetchall()
                else:
                    rows = con.execute("SELECT id,username,role,department_id,active,created_at,updated_at,profile_id FROM users WHERE department_id=? ORDER BY username COLLATE NOCASE", (str(user.get("department_id") or ""),)).fetchall()
                    manageable = MANAGEABLE_ROLES.get(user["role"], set())
                    rows = [r for r in rows if r["role"] in manageable]
            return self.json_response(200, {"users": [dict(r) for r in rows]})
        if path in {"/", "/index.html"}:
            return self.serve_file(INDEX_PATH, "text/html; charset=utf-8")
        # Self-contained page: never expose files from BASE/data/backups/scripts.
        self.send_error(404)
        return

    def _config_patch(self):
        user = self.require_user(["admin"])
        if not user:
            return
        if not self.require_current_client():
            return
        try:
            body = self.read_json(64 * 1024)
        except Exception as e:
            return self.json_response(400, mp_error("MP-DATA-013", str(e)))
        return self.config_write(user, body)

    def serve_logo(self):
        lf = (current_config().get("company") or {}).get("logoFile") or ""
        p = CONFIG_DIR / lf if lf else None
        if not p or not p.is_file():
            return self.json_response(404, mp_error("MP-CFG-404", "Kein Logo hinterlegt."))
        raw = p.read_bytes()
        etag = '"' + hashlib.sha256(raw).hexdigest()[:32] + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "private, no-cache")
        self.send_header("ETag", etag)
        # SVG darf nie aktiv werden, auch nicht beim direkten Aufruf: keine Skripte, keine Ressourcen, Sandbox.
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; sandbox")
        self.send_header("Content-Type", LOGO_MIME.get(lf.rsplit(".", 1)[-1].lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def config_write(self, user, body: dict, logo=None, remove_logo: bool = False):
        status, payload, detail = config_apply(body, logo, remove_logo)
        if status == 200 and detail:
            with DB_LOCK, db_session() as con:
                con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,NULL)",
                            (now_iso(), user["username"], "Firmenprofil geändert", detail))
            with REVISION_CONDITION:
                REVISION_CONDITION.notify_all()
        return self.json_response(status, payload)

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

    def _do_POST(self):
        path = urlparse(self.path).path
        if path in AUTH_POST_PATHS:
            # Login-CSRF: fremde Seiten können den eigenen Header nicht ohne CORS-Freigabe setzen.
            if not str(self.headers.get("X-MP-Client-Version", "")).strip():
                self.close_connection = True
                return self.json_response(403, mp_error("MP-REQ-403", "Anfrage ohne Client-Kennung abgelehnt."))
            limit = MAX_AUTH_BODY
        else:
            # Body erst nach erfolgreicher Anmeldung lesen (kein 8-MB-Upload ohne Sitzung).
            if not self.session_user():
                self.close_connection = True
                return self.json_response(401, mp_error("MP-AUTH-001", "Nicht angemeldet."))
            limit = MAX_BODY
        try:
            body = self.read_json(limit)
        except Exception as e:
            return self.json_response(400, mp_error("MP-DATA-013", str(e)))
        if path == "/api/updates/install":
            user = self.require_user(["admin"])
            if not user or not self.require_current_client():
                return
            if body:
                return self.json_response(400, mp_error("MP-UPD-001", "Updatequelle und Paket werden ausschließlich vom Server gewählt."))
            try:
                job, started = update_manager().start(user["username"])
                return self.json_response(202 if started else 200, {"ok": True, "job": {k: job.get(k) for k in ("jobId", "version", "stage", "error")}, "alreadyRunning": not started})
            except ValueError as e:
                return self.json_response(409, mp_error("MP-UPD-001", str(e)))
        if path == "/api/config/logo":
            user = self.require_user(["admin"])
            if not user:
                return
            if not self.require_current_client():
                return
            try:
                raw = base64.b64decode(str(body.get("data", "")), validate=True)
                ext, data = logo_check(raw, str(body.get("contentType", "")))
            except ValueError as e:
                return self.json_response(400, mp_error("MP-CFG-005", f"Logo abgelehnt: {e}"))
            return self.config_write(user, {"revision": body.get("revision")}, logo=(ext, data))
        if path == "/api/templates/apply":
            user = self.require_user(["admin"])
            if not user:
                return
            if not self.require_current_client():
                return
            status, payload = template_apply(user, body if isinstance(body, dict) else {})
            if status == 200 and not payload.get("dryRun") and not payload.get("unchanged"):
                with REVISION_CONDITION:
                    REVISION_CONDITION.notify_all()
            return self.json_response(status, payload)
        if path.startswith("/api/chat/"):
            user = self.require_user()
            if not user:
                return
            if not self.require_rights(user, "chat", "edit"):
                return
            if not self.require_current_client():
                return
            if not module_on("chat"):
                return self.json_response(403, module_error("chat"))
            with DB_LOCK, db_session() as con:
                status, payload = chat_post(con, user, path, body if isinstance(body, dict) else {})
            if status in (200, 201):
                with REVISION_CONDITION:
                    REVISION_CONDITION.notify_all()   # V12.13.0: Glocke sofort aktualisieren
            return self.json_response(status, payload)
        if path.startswith("/api/notifications/"):
            user = self.require_user()
            if not user:
                return
            if not self.require_current_client():
                return
            if not module_on("notifications"):
                return self.json_response(403, module_error("notifications"))
            state = None
            with DB_LOCK, db_session() as con:
                sig_before = notif_sig(con, user["username"])["sig"]
                if path.endswith("/derived"):
                    state = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
                status, payload = notif_post(con, user, path, body if isinstance(body, dict) else {}, state)
                changed = status == 200 and notif_sig(con, user["username"])["sig"] != sig_before
            # V12.14.1: nur wecken, wenn sich der Stand wirklich geändert hat (kein Thundering Herd bei No-op-POSTs).
            if changed:
                with REVISION_CONDITION:
                    REVISION_CONDITION.notify_all()
            return self.json_response(status, payload)
        production_path = re.fullmatch(r"/api/production/([A-Za-z0-9_-]{1,80})/(release|start|pause|resume|partial|finish|abort|label)", path)
        if production_path:
            user = self.require_user(["admin", *DEPARTMENT_ROLES, "production"])
            if not user or not self.require_rights(user, action="production") or not self.require_current_client():
                return
            if production_path.group(2) == "label" and not module_on("palletLabels"):
                return self.json_response(403, module_error("palletLabels"))
            oid, action = production_path.groups()

            def replay_guard(result):
                if user["role"] != "admin" and str(user.get("department_id") or "") != str(result.get("departmentId") or ""):
                    raise ProductionError("MP-PROD-041", "Keine Produktionsrechte für diesen Bereich.", 403)

            def apply(state):
                production_access(state, user, oid)
                return production_apply(state, user, oid, action, body)
            return self.server_action(user, body, path, "Produktion: "+action, apply, replay_guard)
        demand_path = re.fullmatch(r"/api/demand/([a-z-]{1,40})", path)
        if demand_path:
            user = self.require_user(sorted(DEMAND_WRITE_ROLES))
            if not user or not self.require_rights(user, "frameOrders", "edit") or not self.require_current_client():
                return
            if not module_on("frameOrders"):
                return self.json_response(403, module_error("frameOrders"))
            action = demand_path.group(1)
            if action not in DEMAND_ACTIONS:
                return self.json_response(404, mp_error("MP-DEM-000", "Unbekannte Bedarfsaktion."))
            return self.server_action(user, body, path, "Bedarf: "+action, lambda state: demand_apply(state, user, action, body), validate=True)
        if path == "/api/login":
            ip = self.client_address[0]
            username = str(body.get("username", "")).strip()
            password = str(body.get("password", ""))
            stamp = login_attempt_reserve(ip, username)
            if stamp is None:
                return self.json_response(429, mp_error("MP-AUTH-003", "Zu viele Fehlversuche. Bitte später erneut versuchen."))
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
                return self.json_response(401, mp_error("MP-AUTH-004", "Benutzer oder Passwort falsch."))
            profile = None
            if row["profile_id"]:
                with DB_LOCK, db_session() as con:
                    found = con.execute("SELECT json FROM role_profiles WHERE id=?", (row["profile_id"],)).fetchone()
                profile = json.loads(found["json"]) if found else None
                if not profile or not profile.get("active", True) or profile.get("baseRole") != row["role"]:
                    return self.json_response(403, mp_error("MP-ROLE-002", "Die zugewiesene Rolle ist deaktiviert. Bitte den Admin ansprechen."))
            login_attempt_succeeded(ip, username, stamp)
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            expires = int(time.time()) + SESSION_TTL
            with DB_LOCK, db_session() as con:
                con.execute("INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)", (token_hash, row["id"], expires, now_iso()))
            cookie = f"mp_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}" + self.cookie_secure()
            return self.json_response(200, {"user": user_public(attach_rights(dict(row), profile))}, cookie)
        if path == "/api/logout":
            c = SimpleCookie(self.headers.get("Cookie", ""))
            token = c.get("mp_session")
            if token:
                th = hashlib.sha256(token.value.encode()).hexdigest()
                with DB_LOCK, db_session() as con:
                    con.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
            return self.json_response(200, {"ok": True}, "mp_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0" + self.cookie_secure())
        if path == "/api/users":
            user = self.require_user(USER_MANAGER_ROLES)
            if not user:
                return
            if not self.require_rights(user, action="userAdmin"):
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
            if role not in SCOPED_ROLES:
                department_id = ""
            if department_id:
                with DB_LOCK, db_session() as con:
                    state = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()[0])
                if department_id not in {str(d.get("id")) for d in state.get("departments") or []}:
                    return self.json_response(400, mp_error("MP-AUTH-010", "Bereich ist ungültig."))
            if len(username) < 2 or len(password) < 8 or role not in ROLES or (role in DEPARTMENT_ROLES | {"production"} and not department_id):
                return self.json_response(400, mp_error("MP-AUTH-010", "Benutzername, Passwort, Rolle oder Bereich ungültig."))
            if len(username) > 40 or any(ch in username for ch in "⟦⟧|<>\"'`") or any(ord(ch) < 32 for ch in username):
                return self.json_response(400, mp_error("MP-AUTH-010", "Benutzername: höchstens 40 Zeichen, keine Sonderzeichen ⟦ ⟧ | < > \" ' `."))
            salt, digest = hash_password(password)
            ts = now_iso()
            try:
                with DB_LOCK, db_session() as con:
                    if con.execute("SELECT 1 FROM retired_usernames WHERE username=?", (username,)).fetchone():
                        return self.json_response(409, mp_error("MP-AUTH-013", "Dieser Benutzername bleibt für historische Nachweise reserviert. Bitte einen neuen Namen wählen."))
                    try:
                        profile_id = resolve_profile_id(con, user, role, body.get("profileId", ""))
                    except PermissionError as e:
                        return self.json_response(403, mp_error("MP-ROLE-003", str(e)))
                    except ValueError as e:
                        return self.json_response(400, mp_error("MP-ROLE-003", str(e)))
                    cur = con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at,profile_id) VALUES(?,?,?,?,?,1,?,?,?)", (username, salt, digest, role, department_id, ts, ts, profile_id))
                    con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,NULL)", (ts, user["username"], "Benutzer angelegt", username))
                return self.json_response(201, {"id": cur.lastrowid, "username": username, "role": role, "departmentId": department_id, "active": True, "profileId": profile_id})
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
        ip = self.client_address[0]
        stamp = login_attempt_reserve(ip, user["username"])
        if stamp is None:
            return self.json_response(429, mp_error("MP-AUTH-003", "Zu viele Fehlversuche. Bitte später erneut versuchen."))
        with DB_LOCK, db_session() as con:
            row = con.execute("SELECT salt,password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not row or not verify_password(current, row["salt"], row["password_hash"]):
            return self.json_response(403, mp_error("MP-AUTH-023", "Aktuelles Passwort ist falsch."))
        login_attempt_succeeded(ip, user["username"], stamp)
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

    def do_DELETE(self):
        return self._guarded(self._do_DELETE, True)

    def _do_DELETE(self):
        user = self.require_user(["admin"])
        if not user or not self.require_current_client():
            return
        path = urlparse(self.path).path
        role_path = re.fullmatch(r"/api/roles/([a-z0-9_-]{2,40})", path)
        if role_path:
            with DB_LOCK, db_session() as con:
                con.execute("BEGIN IMMEDIATE")
                if not con.execute("SELECT 1 FROM role_profiles WHERE id=?", (role_path.group(1),)).fetchone():
                    return self.json_response(404, mp_error("MP-ROLE-004", "Rolle nicht gefunden."))
                if con.execute("SELECT 1 FROM users WHERE profile_id=?", (role_path.group(1),)).fetchone():
                    return self.json_response(409, mp_error("MP-ROLE-005", "Rolle ist Benutzern zugewiesen. Deaktivieren oder Benutzer zuerst umstellen."))
                con.execute("DELETE FROM role_profiles WHERE id=?", (role_path.group(1),))
                con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,NULL)", (now_iso(), user["username"], "Rolle gelöscht", role_path.group(1)))
            return self.json_response(200, {"ok": True})
        match = re.fullmatch(r"/api/users/(\d+)", path)
        if not match:
            return self.json_response(404, mp_error("MP-REQ-404", "Nicht gefunden."))
        uid = int(match[1])
        if uid == user["id"]:
            return self.json_response(400, mp_error("MP-AUTH-016", "Das eigene Konto darf nicht gelöscht werden."))
        with DB_LOCK, db_session() as con:
            con.execute("BEGIN IMMEDIATE")
            target = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not target:
                return self.json_response(404, mp_error("MP-AUTH-014", "Benutzer nicht gefunden."))
            if target["role"] == "admin" and target["active"] and con.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
                return self.json_response(400, mp_error("MP-AUTH-016", "Mindestens ein aktiver Admin muss erhalten bleiben."))
            state = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()[0])
            if any(str(e.get("userId") or "") == str(uid) for e in state.get("employees") or [] if e.get("active", True)):
                return self.json_response(409, mp_error("MP-AUTH-024", "Aktive Mitarbeiterzuordnung zuerst lösen."))
            if any(str(pr.get("owner") or "").casefold() == target["username"].casefold() and pr.get("status", "open") not in {"done", "cancelled"} for p in state.get("projects") or [] for pr in p.get("processes") or []):
                return self.json_response(409, mp_error("MP-AUTH-024", "Offene Zuständigkeit zuerst übertragen."))
            # Keep message authors/mention evidence; remove current memberships/read cursors only.
            for channel in con.execute("SELECT id,members FROM chat_channels WHERE kind='group'").fetchall():
                members = json.loads(channel["members"] or "[]")
                cleaned = [x for x in members if str(x).casefold() != target["username"].casefold()]
                if cleaned != members:
                    con.execute("UPDATE chat_channels SET members=? WHERE id=?", (json.dumps(cleaned), channel["id"]))
            con.execute("DELETE FROM chat_reads WHERE username=? COLLATE NOCASE", (target["username"],))
            # Actor snapshots use immutable usernames, so historical references remain readable.
            con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            con.execute("INSERT OR IGNORE INTO retired_usernames VALUES(?,?)", (target["username"], now_iso()))
            con.execute("DELETE FROM users WHERE id=?", (uid,))
            con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,NULL)", (now_iso(), user["username"], "Benutzer gelöscht", json.dumps({"id": uid, "username": target["username"], "role": target["role"]})))
            con.execute("COMMIT")
        return self.json_response(200, {"ok": True})
    do_OPTIONS = _method_not_allowed

    def _do_PATCH(self):
        path = urlparse(self.path).path
        if path == "/api/config":
            return self._config_patch()
        if not path.startswith("/api/users/"):
            return self.json_response(404, mp_error("MP-REQ-404", "Nicht gefunden."))
        user = self.require_user(USER_MANAGER_ROLES)
        if not user:
            return
        if not self.require_rights(user, action="userAdmin") or not self.require_current_client():
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
            raw_active = body.get("active", bool(target["active"]))
            if not isinstance(raw_active, bool):
                return self.json_response(400, mp_error("MP-AUTH-015", "Feld 'active' muss true oder false sein."))
            active = 1 if raw_active else 0
            if uid == user["id"]:
                return self.json_response(400, mp_error("MP-AUTH-016", "Das eigene Konto wird hier nicht geändert. Eigenes Passwort über „Passwort ändern“ setzen."))
            if user["role"] != "admin":
                own = str(user.get("department_id") or "")
                manageable = MANAGEABLE_ROLES.get(user["role"], set())
                if str(target["department_id"] or "") != own or department_id != own or target["role"] not in manageable or role not in manageable:
                    return self.json_response(403, mp_error("MP-AUTH-021", "Benutzerverwaltung ist auf untergeordnete Rollen im eigenen Bereich begrenzt."))
            if role not in ROLES or (role in DEPARTMENT_ROLES | {"production"} and not department_id):
                return self.json_response(400, mp_error("MP-AUTH-015", "Ungültige Rolle oder Bereich fehlt."))
            if role not in SCOPED_ROLES:
                department_id = ""
            state = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()[0])
            if department_id and department_id not in {str(d.get("id")) for d in state.get("departments") or []}:
                return self.json_response(400, mp_error("MP-AUTH-015", "Bereich ist ungültig."))
            try:
                profile_id = resolve_profile_id(con, user, role, body.get("profileId"), str(target["profile_id"] or ""))
            except PermissionError as e:
                return self.json_response(403, mp_error("MP-ROLE-003", str(e)))
            except ValueError as e:
                return self.json_response(400, mp_error("MP-ROLE-003", str(e)))
            fields = ["role=?", "department_id=?", "active=?", "updated_at=?", "profile_id=?"]
            vals = [role, department_id, active, now_iso(), profile_id]
            if "password" in body:
                pw = str(body["password"])
                if len(pw) < 8:
                    return self.json_response(400, mp_error("MP-AUTH-017", "Passwort muss mindestens 8 Zeichen haben."))
                salt, digest = hash_password(pw)
                fields += ["salt=?", "password_hash=?"]
                vals += [salt, digest]
            vals.append(uid)
            con.execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", vals)
            if not active or "password" in body or role != target["role"] or department_id != target["department_id"] or profile_id != str(target["profile_id"] or ""):
                con.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,NULL)", (now_iso(), user["username"], "Benutzer geändert", target["username"]))
        return self.json_response(200, {"ok": True})

    def _do_PUT(self):
        path = urlparse(self.path).path
        if path == "/api/notifications/prefs":
            user = self.require_user()
            if not user:
                return
            if not self.require_rights(user, "notifications"):
                return
            if not self.require_current_client():
                return
            if not module_on("notifications"):
                return self.json_response(403, module_error("notifications"))
            try:
                body = self.read_json(MAX_AUTH_BODY)
            except Exception as e:
                return self.json_response(400, mp_error("MP-DATA-013", str(e)))
            with DB_LOCK, db_session() as con:
                status, payload = notif_put(con, user, path, body)
            return self.json_response(status, payload)
        if path == "/api/config":
            return self._config_patch()
        role_path = re.fullmatch(r"/api/roles/([a-z0-9_-]{2,40})", path)
        if role_path:
            return self.save_role_profile(role_path.group(1))
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
            normalize_fa_state(old)
            normalize_fa_state(incoming)
            unredact_incoming(old, incoming, user)
            strip_archived_history(con, old, incoming)
            rights_error = rights_change_error(old, incoming, user)
            if rights_error:
                con.execute("ROLLBACK")
                return self.json_response(403, mp_error("MP-ROLE-010", rights_error))
            if user["role"] != "admin" and canonical(old.get("palletTemplates")) != canonical(incoming.get("palletTemplates")):
                con.execute("ROLLBACK")
                return self.json_response(403, mp_error("MP-AUTH-002", "Etikettenvorlagen dürfen nur Admins verwalten."))
            for key in ("productionEvents", "palletLabels", "inventory", "frameOrders", "callOffs"):
                # Reihenfolge egal: Bereichsrollen erhalten ausgeblendete Datensätze am Listenende zurück.
                if sorted(map(canonical, old.get(key) or [])) != sorted(map(canonical, incoming.get(key) or [])):
                    return self.json_response(403, mp_error("MP-PROD-041", "Produktionsbuchungen erfolgen über die Produktionsaktionen."))
                incoming[key] = old.get(key) or []
            before_steps, after_steps = _record_map(old.get("workSteps")), _record_map(incoming.get("workSteps"))
            # Block E: Bedarfs-FA entstehen, ändern Menge/Quelle und entfallen nur über /api/demand.
            for oid in set(before_steps) | set(after_steps):
                before, after = before_steps.get(oid), after_steps.get(oid)
                if not any(x and x.get("sourceType") in DEMAND_SOURCES for x in (before, after)):
                    continue
                if before is None or (after is None and before.get("status") in {"planned", "released"}) or (after and any(canonical(before.get(f)) != canonical(after.get(f)) for f in DEMAND_LOCKED_FIELDS)):
                    return self.json_response(403, mp_error("MP-DEM-041", "Bedarfs-FA (Abruf/Bestand) werden über Rahmenaufträge & Bestand angelegt, geändert und storniert."))
            runtime_fields = {"actualStartedAt", "runningSince", "pausedAt", "pauseIntervals", "productionPhases", "partialCompletions", "goodQty", "scrapQty", "remainingHours", "lockedSegments", "lockedStart", "runtimeVersion"}
            for oid, before in before_steps.items():
                after = after_steps.get(oid)
                if before.get("status") in {"running", "paused"} and after is None:
                    return self.json_response(403, mp_error("MP-PROD-041", "Fertigmeldung erfolgt über die Produktionsaktion."))
                if after and (any(canonical(before.get(f)) != canonical(after.get(f)) for f in runtime_fields) or ((after.get("status") in {"running", "paused"} or before.get("status") in {"running", "paused"}) and before.get("status") != after.get("status"))):
                    return self.json_response(403, mp_error("MP-PROD-041", "Produktionsdaten werden serverseitig erfasst."))
            for oid, after in after_steps.items():
                if oid not in before_steps and (after.get("status") in {"running", "paused", "done"} or after.get("productionPhases")):
                    return self.json_response(403, mp_error("MP-PROD-041", "Neue FA beginnen im Planungsstatus."))
            old_hist_ids = {str(h.get("id")) for h in old.get("history") or []}
            if any(str(h.get("id")) not in old_hist_ids and h.get("recordType", "done") == "done" for h in incoming.get("history") or []):
                return self.json_response(403, mp_error("MP-PROD-041", "Ist-Historie entsteht durch die Fertigmeldung."))
            # Legacy-Schattenkopie (V12.4.3 und älter) nie wieder in den Live-State übernehmen.
            incoming.pop("orders", None)
            mod, reason = module_state_guard(old, incoming)
            if mod:
                con.execute("ROLLBACK")
                return self.json_response(403, mp_error("MP-MOD-001", reason, module=mod))
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
                return self.json_response(200, {"ok": True, "revision": row["revision"], "data": redact_state(old, user), "unchanged": True})
            new_revision = row["revision"] + 1
            incoming.setdefault("meta", {})
            incoming["meta"]["serverRevision"] = new_revision
            incoming["meta"]["actor"] = user["username"]
            incoming["meta"]["storage"] = "server"
            ts = now_iso()
            changes = append_change_audit(old, incoming, user, new_revision, ts)
            archived_ids = archive_excess_history(con, incoming)
            raw = json.dumps(incoming, ensure_ascii=False, separators=(",", ":"))
            con.execute("UPDATE state SET revision=?,json=?,updated_at=?,updated_by=? WHERE id=1", (new_revision, raw, ts, user["username"]))
            con.execute("INSERT INTO server_audit(ts,username,action,detail,revision) VALUES(?,?,?,?,?)", (ts, user["username"], action, json.dumps({"detail": detail, "role": user["role"], "departmentId": user.get("department_id") or "", "changes": changes}, ensure_ascii=False), new_revision))
            con.execute("COMMIT")
        try:
            with DB_LOCK, db_session() as con:
                if module_on("notifications"):
                    notify_from_diff(con, old, incoming, user["username"])
        except Exception as e:   # Benachrichtigungen dürfen das Speichern nie verhindern
            print(f"NOTIF: Ereignisse konnten nicht erzeugt werden: {e}", flush=True)
        with REVISION_CONDITION:
            REVISION_CONDITION.notify_all()
        return self.json_response(200, {"ok": True, "revision": new_revision, "data": redact_state(incoming, user), "archivedHistoryIds": archived_ids})


# ---------------------------------------------------------------------------------------------
# V12.21.0: HTTPS im LAN. Ohne Fremdpakete: eigene Firmen-CA und Serverzertifikat (RSA 2048, SHA-256)
# werden einmalig mit "server.py --tls-einrichten --host <LAN-IP>" in config/tls/ erzeugt.
# Liegen server.crt und server.key dort, läuft der Server nur noch per HTTPS (Cookie mit Secure).
# Die CA (firmen-ca.crt) wird auf den Arbeitsplätzen als vertrauenswürdige Stammzertifizierungsstelle
# importiert; ihr Schlüssel bleibt auf dem Server und erlaubt spätere Erneuerung ohne neuen Import.
# ---------------------------------------------------------------------------------------------
TLS_DIR = CONFIG_DIR / "tls"
TLS_CERT, TLS_KEY = TLS_DIR / "server.crt", TLS_DIR / "server.key"
TLS_CA_CERT, TLS_CA_KEY = TLS_DIR / "firmen-ca.crt", TLS_DIR / "firmen-ca.key"
TLS_SERVER_DAYS, TLS_CA_DAYS = 825, 3650


def _is_probable_prime(n: int) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(40):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _rsa_prime(bits: int, e: int) -> int:
    while True:
        c = secrets.randbits(bits) | (3 << (bits - 2)) | 1
        if c % e != 1 and _is_probable_prime(c):
            return c


def rsa_generate(bits: int = 2048) -> dict:
    e = 65537
    while True:
        p, q = _rsa_prime(bits // 2, e), _rsa_prime(bits // 2, e)
        n = p * q
        if p != q and n.bit_length() == bits:
            break
    d = pow(e, -1, (p - 1) * (q - 1))
    return {"n": n, "e": e, "d": d, "p": p, "q": q}


def _der(tag: int, body: bytes) -> bytes:
    n = len(body)
    if n < 0x80:
        size = bytes([n])
    else:
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        size = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + size + body


def _der_int(v: int) -> bytes:
    raw = v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big")
    return _der(0x02, raw)


def _der_seq(*items: bytes) -> bytes:
    return _der(0x30, b"".join(items))


def _der_oid(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    out = bytes([parts[0] * 40 + parts[1]])
    for v in parts[2:]:
        chunk = [v & 0x7F]
        v >>= 7
        while v:
            chunk.append(0x80 | (v & 0x7F))
            v >>= 7
        out += bytes(reversed(chunk))
    return _der(0x06, out)


def _der_time(t: datetime) -> bytes:
    t = t.astimezone(timezone.utc)
    if t.year < 2050:
        return _der(0x17, t.strftime("%y%m%d%H%M%SZ").encode())
    return _der(0x18, t.strftime("%Y%m%d%H%M%SZ").encode())


def _der_name(common_name: str, org: str) -> bytes:
    rdn = lambda oid, v: _der(0x31, _der_seq(_der_oid(oid), _der(0x0C, v.encode("utf-8"))))
    return _der_seq(rdn("2.5.4.10", org), rdn("2.5.4.3", common_name))


def _der_ext(oid: str, value: bytes, critical: bool = False) -> bytes:
    return _der_seq(_der_oid(oid), *([_der(0x01, b"\xff")] if critical else []), _der(0x04, value))


def _rsa_public_info(key: dict) -> bytes:
    pub = _der_seq(_der_int(key["n"]), _der_int(key["e"]))
    return _der_seq(_der_seq(_der_oid("1.2.840.113549.1.1.1"), _der(0x05, b"")), _der(0x03, b"\x00" + pub))


def _key_id(key: dict) -> bytes:
    return hashlib.sha1(_der_seq(_der_int(key["n"]), _der_int(key["e"]))).digest()


def _rsa_sign_sha256(key: dict, data: bytes) -> bytes:
    digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(data).digest()
    k = (key["n"].bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - len(digest_info) - 3) + b"\x00" + digest_info
    return pow(int.from_bytes(em, "big"), key["d"], key["n"]).to_bytes(k, "big")


def x509_certificate(subject_key: dict, issuer_key: dict, subject_cn: str, issuer_cn: str, org: str,
                     days: int, ca: bool, dns_names: list[str] = (), ips: list[str] = ()) -> bytes:
    """Ein X.509-v3-Zertifikat (DER), signiert mit issuer_key (sha256WithRSAEncryption)."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sig_alg = _der_seq(_der_oid("1.2.840.113549.1.1.11"), _der(0x05, b""))
    exts = [_der_ext("2.5.29.19", _der_seq(_der(0x01, b"\xff")) if ca else _der_seq(), True),
            _der_ext("2.5.29.15", _der(0x03, b"\x01\x06") if ca else _der(0x03, b"\x05\xa0"), True),
            _der_ext("2.5.29.14", _der(0x04, _key_id(subject_key))),
            _der_ext("2.5.29.35", _der_seq(_der(0x80, _key_id(issuer_key))))]
    if not ca:
        exts.append(_der_ext("2.5.29.37", _der_seq(_der_oid("1.3.6.1.5.5.7.3.1"))))
        names = [_der(0x82, n.encode("ascii")) for n in dns_names] + [_der(0x87, ipaddress.ip_address(i).packed) for i in ips]
        exts.append(_der_ext("2.5.29.17", _der_seq(*names)))
    tbs = _der_seq(
        _der(0xA0, _der_int(2)), _der_int(secrets.randbits(120) | (1 << 120)), sig_alg,
        _der_name(issuer_cn, org), _der_seq(_der_time(now - timedelta(hours=1)), _der_time(now + timedelta(days=days))),
        _der_name(subject_cn, org), _rsa_public_info(subject_key), _der(0xA3, _der_seq(*exts)))
    return _der_seq(tbs, sig_alg, _der(0x03, b"\x00" + _rsa_sign_sha256(issuer_key, tbs)))


def _pem(label: str, der: bytes) -> str:
    b64 = base64.b64encode(der).decode()
    return f"-----BEGIN {label}-----\n" + "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64)) + f"\n-----END {label}-----\n"


def _rsa_private_pem(key: dict) -> str:
    n, e, d, p, q = key["n"], key["e"], key["d"], key["p"], key["q"]
    der = _der_seq(*(_der_int(v) for v in (0, n, e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p))))
    return _pem("RSA PRIVATE KEY", der)


def _rsa_private_load(path: Path) -> dict:
    """Liest den mit _rsa_private_pem geschriebenen PKCS#1-Schlüssel (nur die eigene CA)."""
    der = base64.b64decode("".join(l for l in path.read_text(encoding="ascii").splitlines() if not l.startswith("-----")))
    def read(pos):
        tag, size = der[pos], der[pos + 1]
        pos += 2
        if size & 0x80:
            cnt = size & 0x7F
            size, pos = int.from_bytes(der[pos:pos + cnt], "big"), pos + cnt
        return tag, der[pos:pos + size], pos + size
    tag, body, _ = read(0)
    if tag != 0x30:
        raise ValueError("CA-Schlüssel ist ungültig.")
    der, pos, vals = body, 0, []
    while pos < len(der):
        tag, value, pos = read(pos)
        vals.append(int.from_bytes(value, "big"))
    _ver, n, e, d, p, q = vals[:6]
    return {"n": n, "e": e, "d": d, "p": p, "q": q}


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def tls_setup(host_ip: str, extra_names: list[str] = ()) -> dict:
    """Firmen-CA (falls fehlend) und neues Serverzertifikat für LAN-IP und PC-Namen anlegen."""
    ip = str(ipaddress.ip_address(host_ip))
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    org = "Produktionsplanung"
    try:
        org = str(load_config()[0]["company"].get("name") or org)[:60] or org
    except Exception:
        pass
    if TLS_CA_CERT.exists() and TLS_CA_KEY.exists():
        ca_key, created_ca = _rsa_private_load(TLS_CA_KEY), False
    else:
        ca_key, created_ca = rsa_generate(), True
        _write_private(TLS_CA_KEY, _rsa_private_pem(ca_key))
        TLS_CA_CERT.write_text(_pem("CERTIFICATE", x509_certificate(ca_key, ca_key, "Produktionsplanung Firmen-CA", "Produktionsplanung Firmen-CA", org, TLS_CA_DAYS, True)), encoding="ascii")
    names = sorted({n for n in [*LOCAL_HOST_NAMES, *(x.strip().lower() for x in extra_names)] if n and re.fullmatch(r"[a-z0-9.-]{1,253}", n) and not re.fullmatch(r"[0-9.]+", n)})
    key = rsa_generate()
    cn = next((n for n in names if n != "localhost"), ip)
    cert = x509_certificate(key, ca_key, cn, "Produktionsplanung Firmen-CA", org, TLS_SERVER_DAYS, False, names, [ip])
    _write_private(TLS_KEY, _rsa_private_pem(key))
    TLS_CERT.write_text(_pem("CERTIFICATE", cert), encoding="ascii")
    return {"ca": str(TLS_CA_CERT), "createdCa": created_ca, "names": names, "ip": ip, "days": TLS_SERVER_DAYS}


def tls_renew_reason(host_ip: str) -> str:
    """Grund für eine automatische Erneuerung des eigenen Serverzertifikats, sonst ''.

    Nur wenn die Firmen-CA (mit Schlüssel) vorliegt; ein selbst hinterlegtes Fremdzertifikat bleibt unangetastet.
    """
    if not (TLS_CERT.exists() and TLS_KEY.exists() and TLS_CA_CERT.exists() and TLS_CA_KEY.exists()):
        return ""
    import ssl
    try:
        info = ssl._ssl._test_decode_cert(str(TLS_CERT))
        ips = {v for k, v in info.get("subjectAltName", ()) if k == "IP Address"}
        expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(info["notAfter"]), timezone.utc)
    except Exception:
        return "Serverzertifikat nicht lesbar"
    if str(ipaddress.ip_address(host_ip)) not in ips:
        return f"LAN-IP {host_ip} fehlt im Zertifikat"
    if expires < datetime.now(timezone.utc) + timedelta(days=30):
        return f"Zertifikat läuft am {expires:%d.%m.%Y} ab"
    return ""


def tls_context():
    """SSL-Kontext, wenn config/tls/server.crt und server.key vorhanden sind; sonst None (HTTP)."""
    if not (TLS_CERT.exists() and TLS_KEY.exists()):
        return None
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(TLS_CERT), str(TLS_KEY))
    return ctx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--init-admin", metavar="USERNAME")
    ap.add_argument("--allowed-subnet", help="Pflicht im Serverbetrieb, z. B. 192.168.178.0/24")
    ap.add_argument("--firma-einrichten", metavar="VORLAGE", help="config/firma.json aus Vorlagedatei (oder 'neutral') anlegen und beenden")
    ap.add_argument("--tls-einrichten", action="store_true", help="Firmen-CA und Serverzertifikat für --host in config/tls/ anlegen und beenden")
    ap.add_argument("--tls-name", action="append", default=[], help="zusätzlicher DNS-Name im Serverzertifikat (mehrfach möglich)")
    args = ap.parse_args()
    global ALLOWED_NETWORK
    if args.tls_einrichten:
        try:
            info = tls_setup(args.host, args.tls_name)
        except ValueError as e:
            print(f"FEHLER: HTTPS-Einrichtung: {e}", file=sys.stderr)
            raise SystemExit(2)
        print(f"HTTPS eingerichtet: {TLS_CERT} (gültig {info['days']} Tage) für {', '.join([info['ip'], *info['names']])}")
        print(f"Firmen-CA {'neu angelegt' if info['createdCa'] else 'weiterverwendet'}: {info['ca']}")
        return
    if args.firma_einrichten:
        raise SystemExit(setup_firma_from_template(args.firma_einrichten))
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
    if not args.init_admin:
        try:
            check_firma_config_start()      # vor init_db: bei Stopp bleibt die Datenbank unberuehrt
        except ConfigError as e:
            print(f"FEHLER: {e.message}", file=sys.stderr)
            raise SystemExit(3)
    init_db()
    if not args.init_admin:
        try:
            _cfg, cfg_warn = load_config()
        except ConfigError as e:
            print(f"FEHLER: {e.message}", file=sys.stderr)
            raise SystemExit(3)
        for w in cfg_warn:
            print(f"WARNUNG: {w}", flush=True)
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
    if os.name == 'nt':
        def update_checks():
            while True:
                update_manager().check()
                threading.Event().wait(3600)
        threading.Thread(target=update_checks, daemon=True).start()
    reason = tls_renew_reason(args.host)
    if reason:
        info = tls_setup(args.host)
        print(f"HTTPS: Serverzertifikat erneuert ({reason}); gültig {info['days']} Tage, Firmen-CA unverändert.", flush=True)
    try:
        ssl_context = tls_context()
    except (OSError, ValueError) as e:
        print(f"FEHLER: MP-TLS-001 HTTPS-Zertifikat in {TLS_DIR} ist ungültig: {e}", file=sys.stderr)
        raise SystemExit(3)
    httpd = MPHTTPServer((args.host, args.port), Handler, ssl_context=ssl_context)
    print(f"Maschinenplanung V{APP_VERSION} LAN-only läuft auf {'https' if ssl_context else 'http'}://{args.host}:{args.port}")
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

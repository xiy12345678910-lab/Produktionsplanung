#!/usr/bin/env python3
"""Firmenkonfiguration config/firma.json (+ Logo), Config-Schema, Prüfung, Migration, Branding und Module.

#73 Phase 1: aus server.py herausgelöst (flaches Modul neben server.py, Teil von $MP_AppFiles). Nur Standardbibliothek.
server.py importiert alle Namen von hier; die veränderlichen Modulwerte (CONFIG_DIR, CONFIG_PATH, LEGACY_SEED_PATH,
CURRENT_CFG) leben NUR hier. server.X = ... wird von server.py auf dieses Modul umgeleitet (Tests bleiben gültig).
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent


# Vom Server (server.py) beim Import gesetzt. Liefern dessen JEWEILS aktuelle Werte, auch wenn Tests
# server.DB_PATH ersetzen. Ohne Server: Standardpfad der Datenbank bzw. keine Rollen.
def db_path_provider() -> Path:
    return BASE / "data" / "maschinenplanung.sqlite3"


def role_ids_provider():
    return frozenset()


def _db_path() -> Path:
    return db_path_provider()


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
            k not in role_ids_provider() or not isinstance(v, str) or len(v) > 40 for k, v in te["roleLabels"].items()):
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
    if not _db_path().exists():
        return None
    try:
        con = sqlite3.connect(f"file:{_db_path().as_posix()}?mode=ro", uri=True)
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
    if not _db_path().exists():
        return True
    try:
        con = sqlite3.connect(f"file:{_db_path().as_posix()}?mode=ro", uri=True)
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

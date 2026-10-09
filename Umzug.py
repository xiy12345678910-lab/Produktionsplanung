#!/usr/bin/env python3
r"""Umzug der Installation auf ein neues Geraet: ein Paket (ZIP) mit Datenbank, config\ und Einstellungen.

Wird von Umzug_Exportieren.ps1 und Umzug_Importieren.ps1 aufgerufen (als Administrator, Server-Stopp/Start,
Ordnerrechte und Tasks erledigen die .ps1-Skripte). Die Datenbank wird wie bei jedem Backup ueber die
SQLite Backup API gesichert (Backup_Datenbank.py, WAL-sicher, integrity_check).

Aufruf:
  Umzug.py --export --base <live-ordner> --ziel <ordner> [--ergebnis <json>]
  Umzug.py --verify --datei <zip> [--sha256 <hash>] [--ergebnis <json>]
  Umzug.py --import --datei <zip> --base <ziel-ordner> [--ueberschreiben] [--sha256 <hash>] [--ergebnis <json>]

Paketinhalt (Umzug_<Firma>_<Datum>.zip):
  umzug.json                     Manifest: Version, Firma, tenantId, Revision, SHA256 und Groesse jeder Datei
  data/maschinenplanung.sqlite3  geprueftes Backup (inkl. Lizenz-Kulanz in app_meta)
  config/...                     firma.json, Logo, lizenz.key, tls\ (Firmen-CA + Serverzertifikat)
  extra/BACKUP_ZIEL.txt          falls vorhanden
LAN_CONFIG.json gehoert zum Geraet und wird nicht mitgenommen; nur "UpdateRepo" steht im Manifest.

Exitcode: 0 ok, 1 Fehler (Meldung mit Code MP-UMZ-0xx). Meldungen nur ASCII (Windows PowerShell 5.1).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import io
import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = 1
KIND = "maschinenplanung-umzug"
MANIFEST = "umzug.json"
DB_MEMBER = "data/maschinenplanung.sqlite3"
EXTRA_FILES = ("BACKUP_ZIEL.txt",)
MEMBER_RE = re.compile(r"\A(data/maschinenplanung\.sqlite3|config/[A-Za-z0-9 _.()+-]+(/[A-Za-z0-9 _.()+-]+)*|extra/BACKUP_ZIEL\.txt)\Z")


class UmzugError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _backup_module():
    """Backup_Datenbank.py aus demselben Ordner laden (mit -I steht der Skriptordner nicht in sys.path)."""
    sys.dont_write_bytecode = True   # kein __pycache__ im Live-Ordner
    spec = importlib.util.spec_from_file_location("mp_backup_umzug", HERE / "Backup_Datenbank.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _quiet(fn, *args):
    """Backup_Datenbank-Funktionen ohne deren (nicht-ASCII) Konsolenausgabe aufrufen."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args)


def _bind(bd, base: Path):
    bd.base = base
    bd.db = base / "data" / "maschinenplanung.sqlite3"
    bd.out = base / "backups"
    bd.cfg_dir = base / "config"
    return bd


def app_version(base: Path) -> str:
    try:
        text = (base / "server.py").read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else ""


def version_key(value: str) -> tuple:
    parts = re.findall(r"\d+", str(value or ""))[:3]
    if not parts:
        raise UmzugError("MP-UMZ-002", f"Ungueltige Versionsangabe: {value!r}")
    return tuple(int(p) for p in parts) + (0,) * (3 - len(parts))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def ascii_text(value: str) -> str:
    table = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
    return "".join(table.get(ch, ch) for ch in str(value or ""))


def file_label(name: str) -> str:
    """Firmenname -> Dateinamensteil (nur A-Z, 0-9, _ und -)."""
    label = re.sub(r"[^A-Za-z0-9-]+", "_", ascii_text(name)).strip("_-")[:40]
    return label or "Firma"


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def config_members(cfg: Path) -> list[tuple[str, Path]]:
    """Alle Dateien in config\\ (auch tls\\ mit privaten Schluesseln, lizenz.key); keine .tmp, keine Links."""
    if not cfg.is_dir():
        return []
    out = []
    for p in sorted(cfg.rglob("*")):
        if p.is_symlink() or not p.is_file() or p.name.endswith(".tmp"):
            continue
        name = "config/" + p.relative_to(cfg).as_posix()
        if not MEMBER_RE.match(name):
            raise UmzugError("MP-UMZ-001", f"Dateiname in config nicht uebertragbar: {name}")
        out.append((name, p))
    return out


def export(base: Path, ziel: Path) -> dict:
    base = base.resolve()
    db = base / "data" / "maschinenplanung.sqlite3"
    version = app_version(base)
    if not db.exists() or not version:
        raise UmzugError("MP-UMZ-001", f"Keine Installation mit Datenbank in {base}.")
    bd = _bind(_backup_module(), base)
    # 1. Frisches, geprueftes Backup (bleibt auch als normale Sicherung in backups\ liegen).
    backup = _quiet(bd.backup, "_umzug")
    revision = bd.verify(backup)
    cfg = base / "config"
    firma = read_json(cfg / "firma.json")
    company = str((firma.get("company") or {}).get("name") or "")
    lan = read_json(base / "LAN_CONFIG.json")
    repo = str(lan.get("UpdateRepo") or "")
    members: list[tuple[str, Path]] = [(DB_MEMBER, backup)] + config_members(cfg)
    for name in EXTRA_FILES:
        if (base / name).is_file():
            members.append((f"extra/{name}", base / name))
    files = {name: {"sha256": sha256_file(path), "groesse": path.stat().st_size} for name, path in members}
    manifest = {
        "schema": SCHEMA,
        "art": KIND,
        "appVersion": version,
        "erstellt": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "quelle": {"computer": socket.gethostname(), "ordner": str(base)},
        "firma": company,
        "tenantId": str(firma.get("tenantId") or ""),
        "dbRevision": revision,
        "einstellungen": {"updateRepo": repo if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) else ""},
        "dateien": files,
    }
    ziel.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    target = ziel / f"Umzug_{file_label(company)}_{stamp}.zip"
    tmp = target.with_name(target.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, path in members:
                z.write(path, name)
        verify(tmp)   # das fertige Paket wird vor dem Umbenennen komplett nachgeprueft
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return {
        "datei": str(target), "sha256": sha256_file(target), "appVersion": version, "firma": company,
        "tenantId": manifest["tenantId"], "dbRevision": revision, "backup": str(backup),
        "dateien": len(files), "lizenz": any(n.startswith("config/lizenz.key") for n in files),
        "tls": "config/tls/server.crt" in files and "config/tls/server.key" in files,
        "ca": "config/tls/firmen-ca.crt" in files, "updateRepo": manifest["einstellungen"]["updateRepo"],
    }


def verify(path: Path, expected_sha256: str = "") -> dict:
    """Prueft das Paket vollstaendig (ohne etwas zu schreiben). Rueckgabe: Manifest."""
    path = Path(path)
    if expected_sha256:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256.strip().lower()):
            raise UmzugError("MP-UMZ-003", "SHA256 muss 64 Hex-Zeichen haben.")
        if sha256_file(path) != expected_sha256.strip().lower():
            raise UmzugError("MP-UMZ-003", "SHA256 der Umzugsdatei stimmt nicht (Datei beschaedigt oder ausgetauscht).")
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as e:
        raise UmzugError("MP-UMZ-002", f"Keine gueltige Umzugsdatei: {e}") from e
    with z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        if len(set(names)) != len(names) or MANIFEST not in names:
            raise UmzugError("MP-UMZ-002", "umzug.json fehlt oder Dateien sind doppelt.")
        try:
            manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
        except ValueError as e:
            raise UmzugError("MP-UMZ-002", f"umzug.json ist ungueltig: {e}") from e
        if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA or manifest.get("art") != KIND:
            raise UmzugError("MP-UMZ-002", "umzug.json: unbekanntes Format.")
        version_key(manifest.get("appVersion"))
        files = manifest.get("dateien")
        if not isinstance(files, dict) or DB_MEMBER not in files:
            raise UmzugError("MP-UMZ-002", "umzug.json: Dateiliste fehlt oder ohne Datenbank.")
        if set(names) - {MANIFEST} != set(files):
            raise UmzugError("MP-UMZ-003", "Paketinhalt passt nicht zur Dateiliste in umzug.json.")
        for info in infos:
            if info.filename == MANIFEST:
                continue
            name = info.filename
            if not MEMBER_RE.match(name) or any(part in ("", ".", "..") for part in name.split("/")):
                raise UmzugError("MP-UMZ-003", f"Unzulaessiger Dateiname im Paket: {name}")
            if info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise UmzugError("MP-UMZ-003", f"Ordner/Links im Paket sind unzulaessig: {name}")
            entry = files[name] if isinstance(files[name], dict) else {}
            h = hashlib.sha256()
            size = 0
            with z.open(info) as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
                    size += len(chunk)
            if h.hexdigest() != entry.get("sha256") or size != entry.get("groesse"):
                raise UmzugError("MP-UMZ-003", f"Pruefsumme stimmt nicht: {name}")
    return manifest


def _summary(manifest: dict) -> dict:
    files = manifest.get("dateien") or {}
    return {
        "appVersion": manifest.get("appVersion"), "firma": manifest.get("firma", ""), "tenantId": manifest.get("tenantId", ""),
        "dbRevision": manifest.get("dbRevision"), "erstellt": manifest.get("erstellt", ""),
        "quelle": (manifest.get("quelle") or {}).get("computer", ""),
        "lizenz": any(n.startswith("config/lizenz.key") for n in files),
        "tls": "config/tls/server.crt" in files and "config/tls/server.key" in files,
        "ca": "config/tls/firmen-ca.crt" in files,
        "updateRepo": str((manifest.get("einstellungen") or {}).get("updateRepo") or ""),
        "backupZiel": "extra/BACKUP_ZIEL.txt" in files,
    }


def import_package(path: Path, base: Path, overwrite: bool = False, expected_sha256: str = "") -> dict:
    """Spielt das Paket in <base> ein. Der Server muss gestoppt sein (Umzug_Importieren.ps1)."""
    base = base.resolve()
    manifest = verify(path, expected_sha256)
    target_version = app_version(base)
    if not target_version:
        raise UmzugError("MP-UMZ-001", f"Im Zielordner {base} ist kein Programm installiert (server.py fehlt).")
    if version_key(target_version) < version_key(manifest["appVersion"]):
        raise UmzugError("MP-UMZ-004", f"Zielgeraet hat V{target_version}, das Paket stammt von V{manifest['appVersion']}. "
                                       f"Zuerst V{manifest['appVersion']} oder neuer installieren bzw. aus diesem Paket importieren.")
    db = base / "data" / "maschinenplanung.sqlite3"
    existing = any(Path(str(db) + ext).exists() for ext in ("", "-wal"))
    if existing and not overwrite:
        raise UmzugError("MP-UMZ-005", f"Auf dem Zielgeraet gibt es bereits eine Datenbank ({db}). "
                                       "Import nur mit -Ueberschreiben (der aktuelle Stand wird vorher gesichert).")
    bd = _bind(_backup_module(), base)
    (base / "backups").mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    work = Path(tempfile.mkdtemp(prefix=f"umzug_{stamp}_", dir=base / "backups"))
    staged = work / "paket"
    previous = work / "vorher"
    result = _summary(manifest)
    result.update(zielVersion=target_version, vorherDb="", vorherConfig="")
    cfg = base / "config"
    # Ein leerer, vom Importskript vorab gesperrter config\\tls zaehlt nicht als vorhandene Konfiguration.
    cfg_existed = cfg.is_dir() and any(p.is_file() for p in cfg.rglob("*"))
    replaced = False
    try:
        # 1. Paket entpacken (nur gepruefte Namen) und Datenbank pruefen, bevor irgendetwas ersetzt wird.
        with zipfile.ZipFile(path) as z:
            for name in manifest["dateien"]:
                dest = staged.joinpath(*name.split("/"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as src, open(dest, "xb") as out:
                    shutil.copyfileobj(src, out)
        for name, entry in manifest["dateien"].items():
            if sha256_file(staged.joinpath(*name.split("/"))) != entry["sha256"]:
                raise UmzugError("MP-UMZ-003", f"Pruefsumme nach dem Entpacken falsch: {name}")
        try:
            revision = bd.verify(staged / "data" / "maschinenplanung.sqlite3")
        except Exception as e:
            raise UmzugError("MP-UMZ-003", f"Datenbank im Paket ist ungueltig: {e}") from e
        # 2. Vorhandenen Stand sichern (nur mit -Ueberschreiben moeglich).
        if existing:
            try:
                pre = _quiet(bd.backup, "_vor_umzug")
            except Exception as e:
                raise UmzugError("MP-UMZ-006", f"Sicherung des aktuellen Stands fehlgeschlagen, nichts ueberschrieben: {e}") from e
            result["vorherDb"] = str(pre)
        if cfg_existed:
            previous.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copytree(cfg, previous / "config")
            except Exception as e:
                raise UmzugError("MP-UMZ-006", f"Sicherung von config fehlgeschlagen, nichts ueberschrieben: {e}") from e
            result["vorherConfig"] = str(previous / "config")
        for name in EXTRA_FILES:
            if (base / name).is_file():
                previous.mkdir(parents=True, exist_ok=True)
                shutil.copy2(base / name, previous / name)
        # 3. Datenbank einspielen: alte -wal/-shm nie neben der neuen Datei liegen lassen (wie Restore).
        db.parent.mkdir(parents=True, exist_ok=True)
        replaced = True
        for ext in ("-wal", "-shm"):
            Path(str(db) + ext).unlink(missing_ok=True)
        bd.sqlite_copy(staged / "data" / "maschinenplanung.sqlite3", db)
        # 4. config\ vollstaendig ersetzen (Firmenprofil, Logo, Lizenz, tls\ mit Firmen-CA).
        replace_tree(staged / "config", cfg)
        # 5. Zusatzdateien
        for name in EXTRA_FILES:
            src = staged / "extra" / name
            if src.is_file():
                shutil.copy2(src, base / name)
        ziel = ""
        if (base / "BACKUP_ZIEL.txt").is_file():
            lines = [x.strip() for x in (base / "BACKUP_ZIEL.txt").read_text(encoding="utf-8-sig").splitlines()
                     if x.strip() and not x.strip().startswith("#")]
            ziel = lines[0] if lines else ""
        result.update(dbRevision=revision, backupZielPfad=ziel, backupZielErreichbar=bool(ziel) and Path(ziel).is_dir())
    except Exception:
        if replaced:
            _undo(bd, base, result.get("vorherDb", ""), previous, cfg_existed)
        raise
    finally:
        shutil.rmtree(staged, ignore_errors=True)
        if not previous.exists():
            shutil.rmtree(work, ignore_errors=True)
    result["vorherOrdner"] = str(work) if previous.exists() else ""
    return result


def replace_tree(src: Path | None, dst: Path) -> None:
    """Inhalt von dst durch src ersetzen. Vorhandene Ordner (v. a. config\\tls mit gesperrten Rechten, vom
    Importskript vorab angelegt) bleiben bestehen, damit private Schluessel nie mit geerbten Leserechten entstehen."""
    if dst.is_dir():
        for p in sorted(dst.rglob("*"), key=lambda x: len(x.parts), reverse=True):
            if p.is_symlink() or p.is_file():
                p.unlink()
            elif p.is_dir() and p != dst / "tls" and not any(p.iterdir()):
                p.rmdir()
    if src is not None and src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def _undo(bd, base: Path, pre_db: str, previous: Path, cfg_existed: bool) -> None:
    """Fehler mitten im Einspielen: vorherigen Stand wiederherstellen (bzw. Ziel wieder leeren)."""
    db = base / "data" / "maschinenplanung.sqlite3"
    cfg = base / "config"
    for ext in ("-wal", "-shm"):
        Path(str(db) + ext).unlink(missing_ok=True)
    if pre_db:
        bd.sqlite_copy(Path(pre_db), db)
    else:
        db.unlink(missing_ok=True)
    replace_tree(previous / "config" if cfg_existed else None, cfg)
    for name in EXTRA_FILES:
        if (previous / name).is_file():
            shutil.copy2(previous / name, base / name)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Umzug der Maschinenplanung auf ein neues Geraet")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--export", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--import", dest="do_import", action="store_true")
    p.add_argument("--base")
    p.add_argument("--ziel")
    p.add_argument("--datei")
    p.add_argument("--sha256", default="")
    p.add_argument("--ueberschreiben", action="store_true")
    p.add_argument("--ergebnis")
    a = p.parse_args(argv)
    try:
        if a.export:
            if not a.base or not a.ziel:
                raise UmzugError("MP-UMZ-001", "--export braucht --base und --ziel.")
            result = export(Path(a.base), Path(a.ziel))
            print(f"Umzugspaket: {result['datei']}")
        elif a.verify:
            if not a.datei:
                raise UmzugError("MP-UMZ-002", "--verify braucht --datei.")
            result = _summary(verify(Path(a.datei), a.sha256))
            print(f"Umzugspaket geprueft: V{result['appVersion']}, Revision {result['dbRevision']}")
        else:
            if not a.datei or not a.base:
                raise UmzugError("MP-UMZ-001", "--import braucht --datei und --base.")
            result = import_package(Path(a.datei), Path(a.base), a.ueberschreiben, a.sha256)
            print(f"Umzugspaket eingespielt: Revision {result['dbRevision']}")
    except UmzugError as e:
        print(f"FEHLER {e}", file=sys.stderr)
        return 1
    except Exception as e:  # unerwartet: nie still
        print(f"FEHLER MP-UMZ-009: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if a.ergebnis:
        Path(a.ergebnis).write_text(json.dumps(result, ensure_ascii=True, indent=2), encoding="ascii")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

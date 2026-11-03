#!/usr/bin/env python3
r"""Online-Backup und Wiederherstellung der Maschinenplanung-Datenbank.

- Nutzt die SQLite Backup API: konsistent auch bei laufendem Server und WAL-Betrieb
  (nie die .sqlite3-Datei allein kopieren – der jüngste Stand kann im -wal liegen).
- V12.10.2: Jede Sicherung entsteht zuerst als .tmp, wird mit PRAGMA integrity_check geprüft
  und erst dann umbenannt. Eine fehlerhafte Sicherung trägt nie den endgültigen Namen.
- Optional: zusätzliche Kopie auf ein zweites Ziel (z. B. Netzlaufwerk), eingetragen
  als erste Zeile in BACKUP_ZIEL.txt neben diesem Skript.
- Behält die neuesten KEEP Sicherungen je Ziel.
- V12.14.0: Zu jeder Sicherung entsteht firma_<zeitstempel>.zip mit config\ (ohne .bak), gleiche Rotation,
  gleiche Zweitkopie. Fehlt config\, entfällt die ZIP-Datei.

Aufruf:
  Backup_Datenbank.py                     Sicherung anlegen
  Backup_Datenbank.py --base <ordner>     Sicherung mit diesem Skript fuer einen alten Installationsordner
  Backup_Datenbank.py --restore <datei>   Sicherung zurückspielen (Server vorher stoppen!
                                          -> Restore_Datenbank.ps1 erledigt Stopp/Start)
  Backup_Datenbank.py --restore-config <zip>  config\ aus firma_*.zip zurückspielen (aktuelle Datei vorher als .bak)
Exitcode: 0 ok, 1 Fehler, 2 lokale Sicherung ok, Zweitkopie fehlgeschlagen,
          3 Datenbank gesichert, Config-Sicherung (firma_*.zip) fehlgeschlagen.

Aufbewahrung: KEEP regulaere Sicherungen; Sicherungen mit Zusatz (_vor_restore, _umzug, _vor_umzug) werden getrennt
je Art (KEEP_SPECIAL) aufbewahrt und verdraengen nie regulaere Sicherungen (und umgekehrt).

Zweitkopie (BACKUP_ZIEL.txt): Das Ziel ist unverschluesselt und oft ein Netzlaufwerk. Daher enthaelt die Config-ZIP
dort keine Geheimnisse (tls\*.key = private Schluessel, update\token.txt). Die lokale Sicherung behaelt sie
(backups\ ist per ACL auf Administratoren/SYSTEM beschraenkt). Nach einer Wiederherstellung nur aus dem Zweitziel
muessen die Schluessel neu erzeugt (HTTPS_Einrichten.ps1) bzw. der Token neu eingetragen werden.
"""
from __future__ import annotations

import datetime
import os
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

KEEP = 60  # bei zwei Sicherungen pro Tag ca. 30 Tage
KEEP_SPECIAL = 10   # je Art: _vor_restore, _umzug, _vor_umzug
SPECIAL_SUFFIXES = ("_vor_restore", "_vor_umzug", "_umzug")
REQUIRED_TABLES = ("state",)
MAX_USER_VERSION = 0   # hoechste unterstuetzte PRAGMA user_version (der Server setzt sie derzeit nicht = 0); bei Schema-Aenderung anheben
last_config_error: str | None = None

base = Path(__file__).resolve().parent
db = base / "data" / "maschinenplanung.sqlite3"
out = base / "backups"
cfg_dir = Path(os.environ.get("MP_CONFIG_DIR") or (base / "config"))


def _kind(path: Path) -> str:
    """'' = regulaere Sicherung, sonst das Zusatz-Suffix (_vor_restore, _umzug, _vor_umzug)."""
    stem = path.name.rsplit(".", 1)[0]
    for suffix in SPECIAL_SUFFIXES:
        if stem.endswith(suffix):
            return suffix
    return ""


def prune(folder: Path, protect: Path | None = None) -> None:
    """Alte Sicherungen entfernen. Regulaere und Zusatz-Sicherungen werden getrennt gezaehlt; `protect` bleibt immer."""
    for pattern in ("maschinenplanung_*.sqlite3", "firma_*.zip"):
        groups: dict[str, list[Path]] = {}
        for p in sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
            groups.setdefault(_kind(p), []).append(p)
        for kind, files in groups.items():
            for p in files[(KEEP_SPECIAL if kind else KEEP):]:
                if protect is not None and p.resolve() == protect.resolve():
                    continue
                p.unlink(missing_ok=True)
    # Reste abgebrochener Sicherungen (älter als 1 Stunde) entfernen.
    limit = datetime.datetime.now().timestamp() - 3600
    for p in [*folder.glob("*.tmp"), *folder.glob("*.tmp-*")]:
        try:
            if p.stat().st_mtime < limit:
                p.unlink()
        except OSError:
            pass


def verify(path: Path) -> int:
    """integrity_check, Pflichttabellen, Schemaversion + Revision lesen. Rückgabe: Revision; Fehler -> RuntimeError."""
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        user_version = int(con.execute("PRAGMA user_version").fetchone()[0])
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        row = con.execute("SELECT revision FROM state WHERE id=1").fetchone() if not missing else None
    except sqlite3.DatabaseError as e:
        raise RuntimeError(f"keine gültige Datenbank: {e}") from e
    finally:
        con.close()
    if result != "ok":
        raise RuntimeError(f"integrity_check fehlgeschlagen: {result}")
    if missing:
        raise RuntimeError(f"Pflichttabelle fehlt: {', '.join(missing)}")
    if user_version > MAX_USER_VERSION:
        raise RuntimeError(f"Datenbank stammt von einer neueren Programmversion (Schema {user_version} > {MAX_USER_VERSION})")
    if not row:
        raise RuntimeError("Datenstand fehlt (Tabelle state leer)")
    return int(row[0])


def sqlite_copy(src_path: Path, dst: Path, clear_dst_sidecars: bool = False) -> int:
    """src -> dst über .tmp, prüfen, dann atomar umbenennen. Rückgabe: Revision.
    clear_dst_sidecars: -wal/-shm des Ziels erst unmittelbar vor dem Umbenennen entfernen (Live-DB beim Restore)."""
    tmp = dst.with_name(dst.name + ".tmp")
    _remove_with_sidecars(tmp)
    src = sqlite3.connect(src_path, timeout=30)
    target = sqlite3.connect(tmp)
    try:
        with target:
            src.backup(target)
        # Sicherung als eigenständige Datei (kein WAL-Modus von der Live-DB übernehmen).
        target.execute("PRAGMA journal_mode=DELETE")
    finally:
        target.close()
        src.close()
    try:
        revision = verify(tmp)
    except Exception:
        _remove_with_sidecars(tmp)
        raise
    for ext in ("-wal", "-shm", "-journal"):
        Path(str(tmp) + ext).unlink(missing_ok=True)
    if clear_dst_sidecars:
        for ext in ("-wal", "-shm"):
            Path(str(dst) + ext).unlink(missing_ok=True)
    os.replace(tmp, dst)
    return revision


def _remove_with_sidecars(path: Path) -> None:
    for ext in ("", "-wal", "-shm", "-journal"):
        Path(str(path) + ext).unlink(missing_ok=True)


def config_files() -> list[Path]:
    if not cfg_dir.is_dir():
        return []
    return sorted(p for p in cfg_dir.rglob("*") if p.is_file() and ".bak-" not in p.name and ".tmp-" not in p.name and not p.name.endswith(".tmp"))


def config_zip(dst: Path) -> Path | None:
    """config\\ -> firma_<zeitstempel>.zip (über .tmp, geprüft, dann umbenannt)."""
    files = config_files()
    if not files:
        return None
    tmp = dst.with_name(dst.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(cfg_dir).as_posix())
    with zipfile.ZipFile(tmp) as z:
        if z.testzip() is not None:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("Config-ZIP fehlerhaft")
    os.replace(tmp, dst)
    return dst


def backup(suffix: str = "", do_prune: bool = True) -> Path:
    global last_config_error
    last_config_error = None
    if not db.exists():
        raise FileNotFoundError("Datenbank fehlt.")
    out.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dst = out / f"maschinenplanung_{ts}{suffix}.sqlite3"
    revision = sqlite_copy(db, dst)
    print(f"geprüft: integrity ok, Revision {revision}")
    try:
        z = config_zip(out / f"firma_{ts}{suffix}.zip")
        if z:
            print(f"Config gesichert: {z}")
    except Exception as e:  # DB-Sicherung bleibt gültig, Exitcode 3
        last_config_error = str(e)
        print(f"WARNUNG: Config-Sicherung fehlgeschlagen: {e}", file=sys.stderr)
    if do_prune:
        prune(out, protect=dst)
    return dst


# Geheimnisse, die nicht auf das (unverschluesselte) Zweitziel gehoeren.
def is_secret(member: str) -> bool:
    parts = member.replace("\\", "/").lower().split("/")
    return (len(parts) >= 2 and parts[0] == "tls" and parts[-1].endswith(".key")) or parts == ["update", "token.txt"]


def filtered_config_zip(zsrc: Path, dst: Path) -> list[str]:
    """Config-ZIP ohne Geheimnisse atomar (.tmp + Umbenennen) nach dst schreiben und pruefen. Rückgabe: ausgelassene Namen."""
    tmp = dst.with_name(dst.name + ".tmp")
    skipped: list[str] = []
    try:
        with zipfile.ZipFile(zsrc) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                if is_secret(info.filename):
                    skipped.append(info.filename)
                    continue
                zout.writestr(info.filename, zin.read(info.filename))
        with zipfile.ZipFile(tmp) as z:
            if z.testzip() is not None:
                raise RuntimeError("Config-ZIP (Zweitkopie) fehlerhaft")
            if any(is_secret(n) for n in z.namelist()):
                raise RuntimeError("Config-ZIP (Zweitkopie) enthält Geheimnisse")
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)
    return skipped


def second_copy(dst: Path) -> int:
    extra_cfg = base / "BACKUP_ZIEL.txt"
    if not extra_cfg.exists():
        return 0
    lines = [x.strip() for x in extra_cfg.read_text(encoding="utf-8-sig").splitlines() if x.strip() and not x.strip().startswith("#")]
    if not lines:
        return 0
    extra = Path(lines[0])
    try:
        extra.mkdir(parents=True, exist_ok=True)
        tmp = extra / (dst.name + ".tmp")
        shutil.copy2(dst, tmp)
        try:
            verify(tmp)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, extra / dst.name)
        zsrc = dst.with_name(dst.name.replace("maschinenplanung_", "firma_", 1).rsplit(".", 1)[0] + ".zip")
        if zsrc.exists():
            skipped = filtered_config_zip(zsrc, extra / zsrc.name)
            if skipped:
                print(f"Zweitkopie ohne Geheimnisse: {', '.join(skipped)}")
        prune(extra)
        print(f"Zweitkopie: {extra / dst.name}")
    except Exception as e:  # lokale Sicherung bleibt gültig
        print(f"WARNUNG: Zweitkopie nach {extra} fehlgeschlagen: {e}", file=sys.stderr)
        return 2
    return 0


def restore(src: Path) -> int:
    """Sicherung zurückspielen. Vorher wird der aktuelle Stand als *_vor_restore gesichert."""
    src = src.resolve()
    revision = verify(src)
    print(f"Sicherung geprüft: {src.name}, Revision {revision}")
    # Quelle zuerst in eine eigene Temp-Datei kopieren: nichts, was danach passiert (Rotation, Vor-Sicherung),
    # kann die Quelle noch löschen. Die Kopie wird erneut geprüft.
    db.parent.mkdir(parents=True, exist_ok=True)
    snapshot = db.parent / f"restore-quelle-{os.getpid()}.tmp"
    _remove_with_sidecars(snapshot)
    try:
        shutil.copy2(src, snapshot)
        if verify(snapshot) != revision:
            raise RuntimeError("Sicherung hat sich während des Kopierens geändert")
        if db.exists():
            pre = backup("_vor_restore", do_prune=False)
            print(f"Aktueller Stand gesichert: {pre}")
        # -wal/-shm der Live-DB werden erst unmittelbar vor dem Austausch entfernt (bei Fehler bleibt die Live-DB intakt).
        sqlite_copy(snapshot, db, clear_dst_sidecars=True)
    finally:
        _remove_with_sidecars(snapshot)
    try:
        prune(out)
    except OSError:
        pass
    print(f"Wiederhergestellt: Revision {revision}")
    return 0


def restore_config(src: Path) -> int:
    """config\\ aus ZIP zurückspielen. Die aktuelle firma.json wird vorher als firma.json.bak-<zeit> gesichert."""
    src = src.resolve()
    with zipfile.ZipFile(src) as z:
        if z.testzip() is not None:
            raise RuntimeError("Config-ZIP fehlerhaft")
        names = [n for n in z.namelist() if not n.endswith("/")]
        root = cfg_dir.resolve()
        for n in names:
            if not (root / n).resolve().is_relative_to(root):
                raise RuntimeError(f"Ungültiger Pfad im ZIP: {n}")
        if "firma.json" not in names:
            raise RuntimeError("firma.json fehlt im ZIP")
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cur = cfg_dir / "firma.json"
        if cur.exists():
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(cur, cfg_dir / f"firma.json.bak-{ts}")
        for n in names:
            target = cfg_dir / n
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(z.read(n))
            os.replace(tmp, target)
    print(f"Config wiederhergestellt: {src.name}")
    return 0


def main(argv: list[str]) -> int:
    global base, db, out, cfg_dir
    try:
        if len(argv) == 2 and argv[0] == "--base":
            base = Path(argv[1]).resolve()
            db, out, cfg_dir = base / "data" / "maschinenplanung.sqlite3", base / "backups", base / "config"
            argv = []
        if len(argv) == 2 and argv[0] == "--restore":
            return restore(Path(argv[1]))
        if len(argv) == 2 and argv[0] == "--restore-config":
            return restore_config(Path(argv[1]))
        if argv:
            print(__doc__, file=sys.stderr)
            return 1
        dst = backup()
        print(dst)
        code = second_copy(dst)
        if last_config_error:
            print("FEHLER: Datenbank gesichert, aber Config-Sicherung fehlgeschlagen (Exitcode 3).", file=sys.stderr)
            return 3
        return code
    except Exception as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

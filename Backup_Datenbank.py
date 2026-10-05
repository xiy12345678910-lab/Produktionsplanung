#!/usr/bin/env python3
"""Online-Backup und Wiederherstellung der Maschinenplanung-Datenbank.

- Nutzt die SQLite Backup API: konsistent auch bei laufendem Server und WAL-Betrieb
  (nie die .sqlite3-Datei allein kopieren – der jüngste Stand kann im -wal liegen).
- V12.10.2: Jede Sicherung entsteht zuerst als .tmp, wird mit PRAGMA integrity_check geprüft
  und erst dann umbenannt. Eine fehlerhafte Sicherung trägt nie den endgültigen Namen.
- Optional: zusätzliche Kopie auf ein zweites Ziel (z. B. Netzlaufwerk), eingetragen
  als erste Zeile in BACKUP_ZIEL.txt neben diesem Skript.
- Behält die neuesten KEEP Sicherungen je Ziel.

Aufruf:
  Backup_Datenbank.py                     Sicherung anlegen
  Backup_Datenbank.py --restore <datei>   Sicherung zurückspielen (Server vorher stoppen!
                                          -> Restore_Datenbank.ps1 erledigt Stopp/Start)
Exitcode: 0 ok, 1 Fehler, 2 lokale Sicherung ok, Zweitkopie fehlgeschlagen.
"""
from __future__ import annotations

import datetime
import os
import shutil
import sqlite3
import sys
from pathlib import Path

KEEP = 60  # bei zwei Sicherungen pro Tag ca. 30 Tage

base = Path(__file__).resolve().parent
db = base / "data" / "maschinenplanung.sqlite3"
out = base / "backups"


def prune(folder: Path) -> None:
    files = sorted(folder.glob("maschinenplanung_*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[KEEP:]:
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
    """integrity_check + Revision lesen. Rückgabe: Revision; Fehler -> RuntimeError."""
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
        row = con.execute("SELECT revision FROM state WHERE id=1").fetchone()
    except sqlite3.DatabaseError as e:
        raise RuntimeError(f"keine gültige Datenbank: {e}") from e
    finally:
        con.close()
    if result != "ok":
        raise RuntimeError(f"integrity_check fehlgeschlagen: {result}")
    if not row:
        raise RuntimeError("Datenstand fehlt (Tabelle state leer)")
    return int(row[0])


def sqlite_copy(src_path: Path, dst: Path) -> int:
    """src -> dst über .tmp, prüfen, dann atomar umbenennen. Rückgabe: Revision."""
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
    os.replace(tmp, dst)
    return revision


def _remove_with_sidecars(path: Path) -> None:
    for ext in ("", "-wal", "-shm", "-journal"):
        Path(str(path) + ext).unlink(missing_ok=True)


def backup(suffix: str = "") -> Path:
    if not db.exists():
        raise FileNotFoundError("Datenbank fehlt.")
    out.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dst = out / f"maschinenplanung_{ts}{suffix}.sqlite3"
    revision = sqlite_copy(db, dst)
    print(f"geprüft: integrity ok, Revision {revision}")
    prune(out)
    return dst


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
        verify(tmp)
        os.replace(tmp, extra / dst.name)
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
    if db.exists():
        pre = backup("_vor_restore")
        print(f"Aktueller Stand gesichert: {pre}")
    db.parent.mkdir(parents=True, exist_ok=True)
    # Nie eine alte -wal/-shm neben der zurückgespielten Datenbank liegen lassen.
    for ext in ("-wal", "-shm"):
        Path(str(db) + ext).unlink(missing_ok=True)
    sqlite_copy(src, db)
    print(f"Wiederhergestellt: Revision {revision}")
    return 0


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "--restore":
            return restore(Path(argv[1]))
        if argv:
            print(__doc__, file=sys.stderr)
            return 1
        dst = backup()
        print(dst)
        return second_copy(dst)
    except Exception as e:
        print(f"FEHLER: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

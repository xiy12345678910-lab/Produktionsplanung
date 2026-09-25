#!/usr/bin/env python3
"""Online-Backup der Maschinenplanung-Datenbank.

- Nutzt die SQLite Backup API: konsistent auch bei laufendem Server und WAL-Betrieb
  (nie die .sqlite3-Datei allein kopieren – der jüngste Stand kann im -wal liegen).
- Prüft jede Sicherung mit PRAGMA integrity_check.
- Optional: zusätzliche Kopie auf ein zweites Ziel (z. B. Netzlaufwerk), eingetragen
  als erste Zeile in BACKUP_ZIEL.txt neben diesem Skript.
- Behält die neuesten KEEP Sicherungen je Ziel.
Rückgabe: Pfad der Sicherung; Exitcode != 0 bei Fehler.
"""
from __future__ import annotations

import datetime
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


def verify(path: Path) -> None:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = con.execute("PRAGMA integrity_check").fetchone()[0]
        revision = con.execute("SELECT revision FROM state WHERE id=1").fetchone()[0]
    finally:
        con.close()
    if result != "ok":
        raise RuntimeError(f"integrity_check fehlgeschlagen: {result}")
    print(f"geprüft: integrity ok, Revision {revision}")


def main() -> int:
    if not db.exists():
        print("Datenbank fehlt.", file=sys.stderr)
        return 1
    out.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dst = out / f"maschinenplanung_{ts}.sqlite3"
    src = sqlite3.connect(db, timeout=30)
    target = sqlite3.connect(dst)
    try:
        with target:
            src.backup(target)
    finally:
        target.close()
        src.close()
    verify(dst)
    prune(out)
    print(dst)

    extra_cfg = base / "BACKUP_ZIEL.txt"
    if extra_cfg.exists():
        lines = [x.strip() for x in extra_cfg.read_text(encoding="utf-8-sig").splitlines() if x.strip() and not x.strip().startswith("#")]
        if lines:
            extra = Path(lines[0])
            try:
                extra.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, extra / dst.name)
                verify(extra / dst.name)
                prune(extra)
                print(f"Zweitkopie: {extra / dst.name}")
            except Exception as e:  # lokale Sicherung bleibt gültig
                print(f"WARNUNG: Zweitkopie nach {extra} fehlgeschlagen: {e}", file=sys.stderr)
                return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

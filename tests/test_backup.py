#!/usr/bin/env python3
"""Backup/Restore (ab V12.10.2): .tmp-Sicherung, Prüfung, Wiederherstellung inkl. WAL.

Aufruf:  python tests/test_backup.py
Arbeitet mit einer Kopie von Backup_Datenbank.py in einem Temp-Ordner (nie mit Live-Daten).
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    RESULTS.append((bool(cond), label))
    print(("PASS " if cond else "FAIL ") + label)


tmp = Path(tempfile.mkdtemp(prefix="mp-backup-"))
shutil.copy2(SRC / "Backup_Datenbank.py", tmp / "Backup_Datenbank.py")
(tmp / "data").mkdir()
DB = tmp / "data" / "maschinenplanung.sqlite3"


def set_revision(rev: int, wal: bool = False) -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    if wal:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY, revision INTEGER, json TEXT)")
    con.execute("INSERT OR REPLACE INTO state(id,revision,json) VALUES(1,?,'{}')", (rev,))
    con.commit()
    return con


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(tmp / "Backup_Datenbank.py"), *args], capture_output=True, text=True, timeout=120)


def revision(path: Path) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT revision FROM state WHERE id=1").fetchone()[0]
    finally:
        con.close()


# 1) Online-Backup bei offenem WAL (letzter Stand nur im -wal)
live = set_revision(5, wal=True)
check(Path(str(DB) + "-wal").exists(), "Testaufbau: Live-DB im WAL-Modus mit offener -wal")
r = run()
backups = sorted((tmp / "backups").glob("maschinenplanung_*.sqlite3"))
check(r.returncode == 0 and len(backups) == 1, f"Backup erfolgreich (Exit {r.returncode})")
check(revision(backups[0]) == 5, "Backup enthält den Stand aus der -wal (Revision 5)")
check(not [p for p in (tmp / "backups").iterdir() if ".tmp" in p.name or p.name.endswith(("-wal", "-shm"))], "Keine .tmp/-wal/-shm-Reste nach erfolgreichem Backup")

# 2) Fehlgeschlagene Prüfung -> keine Datei mit endgültigem Namen
spec = importlib.util.spec_from_file_location("bk", tmp / "Backup_Datenbank.py")
bk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bk)
orig_verify = bk.verify
bk.verify = lambda p: (_ for _ in ()).throw(RuntimeError("simuliert"))
time.sleep(1.1)
try:
    bk.backup()
    failed_ok = False
except RuntimeError:
    failed_ok = True
bk.verify = orig_verify
names = sorted(p.name for p in (tmp / "backups").iterdir())
check(failed_ok and len([n for n in names if n.endswith(".sqlite3")]) == 1 and not any(".tmp" in n for n in names),
      f"Fehlerhafte Sicherung trägt nie den endgültigen Namen ({names})")

# 3) Kaputte Datei wird nicht zurückgespielt
bad = tmp / "kaputt.sqlite3"
bad.write_bytes(b"keine datenbank" * 100)
r = run("--restore", str(bad))
check(r.returncode == 1 and revision(DB) == 5, "Restore einer kaputten Datei abgelehnt, Live-DB unverändert")

# 4) Restore: aktueller Stand wird vorher gesichert, -wal/-shm entfernt
live.execute("UPDATE state SET revision=9 WHERE id=1")
live.commit()
live.close()
con = sqlite3.connect(DB)  # -wal erneut offen halten wie bei laufendem Server
con.execute("PRAGMA wal_autocheckpoint=0")
time.sleep(1.1)
r = run("--restore", str(backups[0]))
con.close()
check(r.returncode == 0, f"Restore erfolgreich (Exit {r.returncode}) {r.stderr.strip()}")
check(revision(DB) == 5, "Live-DB hat nach Restore Revision 5")
pre = list((tmp / "backups").glob("*_vor_restore.sqlite3"))
check(len(pre) == 1 and revision(pre[0]) == 9, "Stand vor dem Restore (Revision 9) gesichert")
ic = sqlite3.connect(DB).execute("PRAGMA integrity_check").fetchone()[0]
check(ic == "ok", "integrity_check nach Restore ok")

# 5) Aufbewahrung: alte .tmp-Reste werden aufgeräumt
stale = tmp / "backups" / "maschinenplanung_alt.sqlite3.tmp"
stale.write_bytes(b"x")
old = time.time() - 7200
os.utime(stale, (old, old))
time.sleep(1.1)
run()
check(not stale.exists(), "Abgebrochene .tmp-Sicherung (> 1 h) wird entfernt")

# Bootstrap upgrades use the new backup script before replacing old application files.
(tmp / "config").mkdir()
(tmp / "config" / "firma.json").write_text('{"company":{"name":"Bestandsfirma"}}', encoding="utf-8")
r = subprocess.run([sys.executable, "-I", str(SRC / "Backup_Datenbank.py"), "--base", str(tmp)], capture_output=True, text=True,
                   env={**os.environ, "MP_CONFIG_DIR": str(tmp / "irrelevant")}, timeout=120)
profile_backups = list((tmp / "backups").glob("firma_*.zip"))
check(r.returncode == 0 and len(profile_backups) == 1, "Neues Backupskript sichert alten Installationsordner samt Firmenprofil")
with zipfile.ZipFile(profile_backups[0]) as z:
    check(z.testzip() is None and b"Bestandsfirma" in z.read("firma.json"), "Bootstrap-Firmenprofilbackup enthält die tatsächliche Live-Konfiguration")

shutil.rmtree(tmp, ignore_errors=True)
failed = [x for x in RESULTS if not x[0]]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

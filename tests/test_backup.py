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

# ---- Audit: Restore der aeltesten von 60 Sicherungen, getrennte Aufbewahrung, verify, Geheimnisse ----
rt = Path(tempfile.mkdtemp(prefix="mp-backup-rot-"))
shutil.copy2(SRC / "Backup_Datenbank.py", rt / "Backup_Datenbank.py")
(rt / "data").mkdir()
(rt / "config" / "tls").mkdir(parents=True)
(rt / "config" / "firma.json").write_text("{}", encoding="utf-8")
(rt / "config" / "tls" / "server.key").write_text("PRIVATE", encoding="utf-8")
(rt / "config" / "tls" / "server.crt").write_text("CERT", encoding="utf-8")
(rt / "config" / "update").mkdir()
(rt / "config" / "update" / "token.txt").write_text("TOKEN", encoding="utf-8")
(rt / "config" / "lizenz.key").write_text("LIZENZ", encoding="utf-8")
rdb = rt / "data" / "maschinenplanung.sqlite3"
c = sqlite3.connect(rdb)
c.execute("CREATE TABLE state(id INTEGER PRIMARY KEY, revision INTEGER, json TEXT)")
c.execute("INSERT INTO state VALUES(1,100,'{}')")
c.commit(); c.close()
spec2 = importlib.util.spec_from_file_location("bk2", rt / "Backup_Datenbank.py")
bk2 = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(bk2)
bdir = rt / "backups"
bdir.mkdir()
base_t = time.time() - 100000
for i in range(60):   # 60 regulaere Sicherungen, i=0 ist die aelteste (Revision 1)
    f = bdir / f"maschinenplanung_2026-01-01_{i:06d}.sqlite3"
    cc = sqlite3.connect(f)
    cc.execute("CREATE TABLE state(id INTEGER PRIMARY KEY, revision INTEGER, json TEXT)")
    cc.execute("INSERT INTO state VALUES(1,?,'{}')", (i + 1,))
    cc.commit(); cc.close()
    os.utime(f, (base_t + i * 10, base_t + i * 10))
oldest = bdir / "maschinenplanung_2026-01-01_000000.sqlite3"
r = subprocess.run([sys.executable, "-I", str(rt / "Backup_Datenbank.py"), "--restore", str(oldest)], capture_output=True, text=True, timeout=120)
check(r.returncode == 0 and revision(rdb) == 1, f"Restore der aeltesten von 60 Sicherungen funktioniert (Exit {r.returncode}) {r.stderr.strip()}")
check(len(list(bdir.glob("*_vor_restore.sqlite3"))) == 1, "Vor-Restore-Sicherung angelegt")
check(len([p for p in bdir.glob("maschinenplanung_*.sqlite3") if not p.name.endswith("_vor_restore.sqlite3")]) == 60, "Vor-Restore-Sicherung verdraengt keine regulaere Sicherung")
check(not list(rdb.parent.glob("restore-quelle-*")), "keine Temp-Kopie der Quelle zurueckgeblieben")

# Zusatz-Sicherungen getrennt: 12 x _umzug -> 10 bleiben, regulaere unberuehrt
for i in range(12):
    f = bdir / f"maschinenplanung_2026-02-01_{i:06d}_umzug.sqlite3"
    shutil.copy2(oldest, f)
    os.utime(f, (base_t + 5000 + i, base_t + 5000 + i))
bk2.prune(bdir)
check(len(list(bdir.glob("*_umzug.sqlite3"))) == bk2.KEEP_SPECIAL, "Zusatz-Sicherungen (_umzug) getrennt auf KEEP_SPECIAL begrenzt")
check(len([p for p in bdir.glob("maschinenplanung_*.sqlite3") if bk2._kind(p) == ""]) == 60, "regulaere Sicherungen bleiben bei 60")

# -wal/-shm der Live-DB bleiben bis zum Austausch erhalten, wenn die Quelle ungueltig ist
hold = sqlite3.connect(rdb)
hold.execute("PRAGMA journal_mode=WAL")
hold.execute("PRAGMA wal_autocheckpoint=0")
hold.execute("UPDATE state SET revision=77 WHERE id=1")
hold.commit()
badsrc = rt / "leer.sqlite3"
badsrc.write_bytes(b"x" * 200)
r = subprocess.run([sys.executable, "-I", str(rt / "Backup_Datenbank.py"), "--restore", str(badsrc)], capture_output=True, text=True, timeout=120)
check(r.returncode == 1 and Path(str(rdb) + "-wal").exists(), "Fehlgeschlagener Restore laesst -wal der Live-DB unangetastet")
hold.close()

# verify(): Pflichttabelle und neuere Schemaversion
nostate = rt / "ohne_state.sqlite3"
c = sqlite3.connect(nostate); c.execute("CREATE TABLE andere(x)"); c.commit(); c.close()
try:
    bk2.verify(nostate); v1 = False
except RuntimeError as e:
    v1 = "Pflichttabelle" in str(e)
check(v1, "verify() lehnt Datenbank ohne Pflichttabelle state ab")
newer = rt / "neuer.sqlite3"
shutil.copy2(oldest, newer)
c = sqlite3.connect(newer); c.execute("PRAGMA user_version=99"); c.commit(); c.close()
try:
    bk2.verify(newer); v2 = False
except RuntimeError as e:
    v2 = "neueren" in str(e)
check(v2, "verify() lehnt neuere Schemaversion (user_version) ab")

# Zweitziel: keine Geheimnisse, atomar, lokale Sicherung behaelt sie
zweit = rt / "zweit"
(rt / "BACKUP_ZIEL.txt").write_text(str(zweit), encoding="utf-8")
time.sleep(1.1)
r = subprocess.run([sys.executable, "-I", str(rt / "Backup_Datenbank.py")], capture_output=True, text=True, timeout=120)
check(r.returncode == 0, f"Backup mit Zweitziel erfolgreich (Exit {r.returncode}) {r.stderr.strip()}")
zz = sorted(zweit.glob("firma_*.zip"))
with zipfile.ZipFile(zz[-1]) as z:
    zn = z.namelist()
check(zz and not any(n.endswith(".key") and n.startswith("tls/") for n in zn) and "update/token.txt" not in zn, f"Zweitziel-ZIP ohne tls/*.key und Token ({zn})")
check("tls/server.crt" in zn and "firma.json" in zn and "lizenz.key" in zn, "Zweitziel-ZIP enthaelt Zertifikat, Firmenprofil und Lizenz")
with zipfile.ZipFile(sorted(bdir.glob("firma_*.zip"), key=lambda p: p.stat().st_mtime)[-1]) as z:
    ln = z.namelist()
check("tls/server.key" in ln and "update/token.txt" in ln, "lokale Sicherung behaelt Schluessel und Token")
check(not list(zweit.glob("*.tmp")), "Zweitziel ohne .tmp-Reste")

# Config-Sicherungsfehler -> Exitcode 3
orig_zip = bk2.config_zip
bk2.base, bk2.db, bk2.out, bk2.cfg_dir = rt, rdb, bdir, rt / "config"
bk2.config_zip = lambda dst: (_ for _ in ()).throw(RuntimeError("simuliert"))
time.sleep(1.1)
code = bk2.main([])
bk2.config_zip = orig_zip
check(code == 3, f"Config-Sicherungsfehler -> Exitcode 3 (war {code})")
shutil.rmtree(rt, ignore_errors=True)

shutil.rmtree(tmp, ignore_errors=True)
failed = [x for x in RESULTS if not x[0]]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

#!/usr/bin/env python3
"""#50: Backup_Datenbank.py Zweitziel (BACKUP_ZIEL.txt): Kopie, Rotation (KEEP), Exitcode 2 bei Fehler.

Aufruf:  python tests/test_backup_target.py
Arbeitet mit einer Kopie von Backup_Datenbank.py in Temp-Ordnern (nie mit Live-Daten).
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
KEEP = 60


class SecondTarget(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mp-bktarget-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.inst = self.tmp / "inst"
        (self.inst / "data").mkdir(parents=True)
        (self.inst / "config").mkdir()
        (self.inst / "config" / "firma.json").write_text('{"x":1}', encoding="utf-8")
        shutil.copy2(SRC / "Backup_Datenbank.py", self.inst / "Backup_Datenbank.py")
        con = sqlite3.connect(self.inst / "data" / "maschinenplanung.sqlite3")
        con.execute("CREATE TABLE state(id INTEGER PRIMARY KEY, revision INTEGER, json TEXT)")
        con.execute("INSERT INTO state VALUES(1,7,'{}')")
        con.commit(); con.close()
        self.second = self.tmp / "zweit"

    def target(self, text: str):
        (self.inst / "BACKUP_ZIEL.txt").write_text(text, encoding="utf-8")

    def run_backup(self):
        env = {"PATH": "/usr/bin:/bin"}
        return subprocess.run([sys.executable, "-I", str(self.inst / "Backup_Datenbank.py")], capture_output=True, text=True, timeout=120, env=env)

    def primary(self):
        return sorted((self.inst / "backups").glob("maschinenplanung_*.sqlite3"))

    def test_copy_to_second_target(self):
        self.target(f"# Kommentar\n\n{self.second}\n")
        r = self.run_backup()
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.primary()
        self.assertEqual(len(p), 1)
        copies = sorted(self.second.glob("maschinenplanung_*.sqlite3"))
        self.assertEqual([c.name for c in copies], [p[0].name])
        self.assertEqual(copies[0].read_bytes(), p[0].read_bytes())
        con = sqlite3.connect(copies[0])
        self.assertEqual(con.execute("SELECT revision FROM state").fetchone()[0], 7)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        con.close()
        self.assertEqual(len(list(self.second.glob("firma_*.zip"))), 1, "Config-ZIP wird mitkopiert")
        self.assertFalse([x for x in self.second.iterdir() if ".tmp" in x.name], "keine .tmp-Reste")
        self.assertIn("Zweitkopie", r.stdout)

    def test_no_or_empty_target_file_is_exit_0_and_no_copy(self):
        self.assertEqual(self.run_backup().returncode, 0)
        self.target("# nur Kommentar\n   \n")
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertFalse(self.second.exists())

    def test_retention_applied_on_second_target(self):
        self.target(str(self.second))
        self.second.mkdir()
        old = time.time() - 30 * 86400
        for i in range(KEEP + 5):
            for name in (f"maschinenplanung_2026-01-01_{i:06d}.sqlite3", f"firma_2026-01-01_{i:06d}.zip"):
                f = self.second / name
                f.write_bytes(b"alt")
                import os
                os.utime(f, (old + i, old + i))
        stale = self.second / "maschinenplanung_x.sqlite3.tmp"
        stale.write_bytes(b"rest")
        import os
        os.utime(stale, (time.time() - 7200, time.time() - 7200))
        r = self.run_backup()
        self.assertEqual(r.returncode, 0, r.stderr)
        dbs = sorted(self.second.glob("maschinenplanung_*.sqlite3"), key=lambda p: p.stat().st_mtime)
        zips = list(self.second.glob("firma_*.zip"))
        self.assertEqual(len(dbs), KEEP, "Zweitziel behaelt genau KEEP Datenbanksicherungen")
        self.assertEqual(len(zips), KEEP, "und KEEP Config-ZIPs")
        newest = self.primary()[0].name
        self.assertIn(newest, [d.name for d in dbs], "neueste Sicherung bleibt")
        self.assertNotIn("maschinenplanung_2026-01-01_000000.sqlite3", [d.name for d in dbs], "aelteste faellt weg")
        self.assertFalse(stale.exists(), "alter .tmp-Rest wird entfernt")

    def test_unwritable_second_target_exit_2_primary_kept(self):
        blocker = self.tmp / "datei"
        blocker.write_text("ich bin eine Datei", encoding="utf-8")
        self.target(str(blocker / "unterordner"))   # mkdir unter einer Datei scheitert (auch als root)
        r = self.run_backup()
        self.assertEqual(r.returncode, 2, (r.stdout, r.stderr))
        self.assertIn("WARNUNG: Zweitkopie", r.stderr)
        self.assertIn(str(blocker / "unterordner"), r.stderr)
        p = self.primary()
        self.assertEqual(len(p), 1, "lokale Sicherung bleibt erhalten")
        con = sqlite3.connect(p[0])
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        con.close()
        self.assertTrue(blocker.is_file() and blocker.read_text(encoding="utf-8") == "ich bin eine Datei")

    def test_second_target_missing_drive_style_path_exit_2(self):
        self.target("/proc/nicht-vorhanden/mp-backup")
        r = self.run_backup()
        self.assertEqual(r.returncode, 2, (r.stdout, r.stderr))
        self.assertIn("WARNUNG", r.stderr)
        self.assertEqual(len(self.primary()), 1)

    def test_corrupt_copy_is_not_published_at_final_name(self):
        # Zweitkopie scheitert in verify(): kein endgueltiger Name im Zweitziel, Exit 2.
        import importlib.util
        spec = importlib.util.spec_from_file_location("bk_t", self.inst / "Backup_Datenbank.py")
        bk = importlib.util.module_from_spec(spec); spec.loader.exec_module(bk)
        dst = bk.backup()
        self.target(str(self.second))
        real = bk.verify
        bk.verify = lambda p: (_ for _ in ()).throw(RuntimeError("simuliert"))
        try:
            self.assertEqual(bk.second_copy(dst), 2)
        finally:
            bk.verify = real
        self.assertFalse(list(self.second.glob("maschinenplanung_*.sqlite3")))
        self.assertEqual(len(self.primary()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

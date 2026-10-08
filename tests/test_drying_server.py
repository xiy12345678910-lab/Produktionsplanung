#!/usr/bin/env python3
"""V12.27.0: Server prueft die Reinigungszeit nach FA je Maschine (MP-MACH-018)."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class CleanupMinutesValidation(unittest.TestCase):
    def test_cleanup_minutes(self):
        saved_dir, saved_db = server.DATA_DIR, server.DB_PATH
        try:
            with tempfile.TemporaryDirectory(prefix="mp-cleanup-") as tmp:
                server.DATA_DIR = Path(tmp)
                server.DB_PATH = Path(tmp) / "t.sqlite3"
                server.init_db(seed="werbetechnik")
                with server.db_session() as con:
                    old = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
        finally:
            server.DATA_DIR, server.DB_PATH = saved_dir, saved_db
        for good in (0, 120, 1440):
            new = copy.deepcopy(old)
            new["machines"][0]["cleanupMinutes"] = good
            ok, code, reason = server.validate_state(old, new)
            self.assertTrue(ok, f"{good}: {code} {reason}")
        for bad in (2000, -1, 1.5, True, "x"):
            new = copy.deepcopy(old)
            new["machines"][0]["cleanupMinutes"] = bad
            ok, code, _ = server.validate_state(old, new)
            self.assertEqual((ok, code), (False, "MP-MACH-018"), bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)

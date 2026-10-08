#!/usr/bin/env python3
"""V12.27.0: Bereichs-Eigenschaft avHours (Planung AV ja/nein) statt Namenserkennung."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_av_handoff import step


def deps(*rows):
    return [dict({"planningType": "MACHINE", "active": True}, **r) for r in rows]


class AvHoursFlag(unittest.TestCase):
    def test_migration(self):
        saved = server.DATA_DIR, server.DB_PATH
        try:
            with tempfile.TemporaryDirectory(prefix="mp-avflag-") as tmp:
                server.DATA_DIR = Path(tmp); server.DB_PATH = Path(tmp) / "state.sqlite3"
                server.init_db(seed="neutral")
                with server.db_session() as con:
                    st = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()[0])
                    st["departments"] = deps({"id": "konfx", "name": "Konfektion X"}, {"id": "nae", "name": "Näherei"},
                                             {"id": "k9", "name": "KONFEKTION 9", "avHours": False}, {"id": "k8", "name": "Konf 8", "avHours": True})
                    con.execute("UPDATE state SET json=? WHERE id=1", (json.dumps(st),))
                    for _ in range(2):  # idempotent
                        server.migrate_state_v12270(con)
                        got = {d["id"]: d for d in json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()[0])["departments"]}
                        self.assertIs(got["konfx"]["avHours"], True)
                        self.assertNotIn("avHours", got["nae"])
                        self.assertIs(got["k9"]["avHours"], False)
                        self.assertIs(got["k8"]["avHours"], True)
        finally:
            server.DATA_DIR, server.DB_PATH = saved

    def test_rule_uses_flag_not_name(self):
        for name, flag, hours, expected in (("Näherei", True, 2, True), ("Näherei", False, 2, False), ("Konfektion 9", False, 2, False), ("Konfektion 9", True, 2, True)):
            old = {"departments": deps({"id": "d1", "name": name, "avHours": flag}), "machines": [], "workSteps": []}
            new = copy.deepcopy(old); new["workSteps"].append(step(departmentId="d1", hours=hours))
            self.assertEqual(server.production_planning_change_allowed(old, new)[0], expected, (name, flag))

    def test_validator_requires_bool(self):
        self.assertTrue(server.av_hours_department(deps({"id": "a", "avHours": True}), "a"))
        self.assertFalse(server.av_hours_department(deps({"id": "a", "avHours": "true"}), "a"))


if __name__ == "__main__":
    unittest.main()

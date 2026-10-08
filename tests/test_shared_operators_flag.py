#!/usr/bin/env python3
"""#51: Bedienerkapazität der Freigabe hängt am Bereichs-Flag sharedOperators, nicht an der ID 'cnc'."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_gates as gates
import server

SEG = [{"start": "2026-10-05T08:00", "end": "2026-10-05T12:00", "shift": "early"}]


def order(oid, mid, status):
    return {"id": oid, "planningType": "MACHINE", "status": status, "machineId": mid,
            "baselinePlan": {"machineId": mid, "segments": copy.deepcopy(SEG)}}


def state(dept_id, **flags):
    return {"departments": [{"id": dept_id, "name": "X", "planningType": "MACHINE", "active": True, **flags}],
            "machines": [{"id": "a", "departmentId": dept_id, "defaultShiftMode": "2"}, {"id": "b", "departmentId": dept_id, "defaultShiftMode": "2"}],
            "operatorCapacity": {"early": 1}, "shiftTemplates": {"early": {"start": "06:00", "end": "14:00"}}, "personnelGate": False, "history": [],
            "workSteps": [order("o1", "a", "released"), order("o2", "b", "planned")]}


def verdict(st):
    new = copy.deepcopy(st)
    new["workSteps"][1]["status"] = "released"
    return gates.validate_release_feasibility(st, new)


class SharedOperators(unittest.TestCase):
    def test_non_cnc_with_flag_is_checked(self):
        ok, code, _ = verdict(state("fraesen", sharedOperators=True))
        self.assertEqual((ok, code), (False, "MP-PLAN-059"))

    def test_cnc_flag_false_or_missing_key_not_checked(self):
        self.assertTrue(verdict(state("cnc", sharedOperators=False))[0])
        self.assertTrue(verdict(state("cnc"))[0])  # Schlüssel fehlt: Migration setzt ihn; die Prüfung selbst liest nur das Flag
        self.assertFalse(verdict(state("cnc", sharedOperators=True))[0])

    def test_fallback_department_is_first_active_production(self):
        st = {"departments": [{"id": "x", "kind": "sales"}, {"id": "y", "active": False}, {"id": "z"}], "machines": [{"id": "m"}]}
        self.assertEqual(gates.dept_of(st, "m"), "z")
        self.assertEqual(server.default_dept_id(st), "z")
        self.assertEqual(gates.dept_of({"machines": [{"id": "m"}]}, "m"), "cnc")

    def test_migration_sets_flag_on_legacy_cnc_idempotent(self):
        saved = server.DATA_DIR, server.DB_PATH
        try:
            with tempfile.TemporaryDirectory(prefix="mp-shared-") as tmp:
                server.DATA_DIR = Path(tmp); server.DB_PATH = Path(tmp) / "state.sqlite3"
                server.init_db(seed="neutral")
                with server.db_session() as con:
                    def load():
                        return {d["id"]: d for d in json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()[0])["departments"]}
                    st = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()[0])
                    st["departments"] = [{"id": "cnc", "name": "CNC", "planningType": "MACHINE", "active": True},
                                         {"id": "other", "name": "O", "planningType": "MACHINE", "active": True},
                                         {"id": "kept", "name": "K", "planningType": "MACHINE", "active": True, "sharedOperators": False}]
                    con.execute("UPDATE state SET json=?", (json.dumps(st),))
                    for _ in range(2):
                        server.migrate_state_v1216(con)
                        got = load()
                        self.assertIs(got["cnc"]["sharedOperators"], True)
                        self.assertNotIn("sharedOperators", got["other"])
                        self.assertIs(got["kept"]["sharedOperators"], False)
                    st["departments"][0]["sharedOperators"] = False
                    con.execute("UPDATE state SET json=?", (json.dumps(st),))
                    server.migrate_state_v1216(con)
                    self.assertIs(load()["cnc"]["sharedOperators"], False)
        finally:
            server.DATA_DIR, server.DB_PATH = saved


if __name__ == "__main__":
    unittest.main()

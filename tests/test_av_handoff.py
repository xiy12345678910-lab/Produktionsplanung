#!/usr/bin/env python3
"""Server authorization for AV handoff ownership and departmental metadata."""
import copy
import sys
import unittest
import tempfile
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

DEPARTMENTS = [
    {"id": "cnc", "name": "CNC", "planningType": "MACHINE"},
    {"id": "konf1", "name": "Konfektion 1", "planningType": "MACHINE"},
]
BASE = {"departments": copy.deepcopy(DEPARTMENTS), "machines": [], "workSteps": []}

def step(**overrides):
    value = {"id": "step_1", "departmentId": "cnc", "planningType": "MACHINE", "machineId": "", "altMachineId": "", "allowAlternative": False, "status": "planned", "hours": 0, "direction": "forward", "anchorMode": "none", "requiredStart": "", "requiredFinish": "", "baselinePlan": None, "handoffUnassigned": True}
    value.update(overrides)
    return value

class AVHandoffAuthorization(unittest.TestCase):
    def test_validator_rejects_invalid_handoff_flag_and_release_without_assignment(self):
        saved_dir, saved_db = server.DATA_DIR, server.DB_PATH
        try:
            with tempfile.TemporaryDirectory(prefix="mp-av-validator-") as tmp:
                server.DATA_DIR=Path(tmp); server.DB_PATH=Path(tmp)/"state.sqlite3"
                server.init_db(seed="werbetechnik")
                with server.db_session() as con:
                    base=json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()[0])
                order=step(fa="Validator",order="Validator",sequence=10,pos=10,predecessorIds=[],targetQty=1)
                new=copy.deepcopy(base);new["workSteps"].append(order)
                self.assertTrue(server.validate_state(base,new)[0])
                for patch in ({"handoffUnassigned":"false"},{"status":"released"},{"machineId":new["machines"][0]["id"]}):
                    bad=copy.deepcopy(new);bad["workSteps"][-1].update(patch)
                    ok,code,reason=server.validate_state(base,bad)
                    self.assertFalse(ok,patch)
                    self.assertEqual(code,"MP-PLAN-065",reason)
        finally:
            server.DATA_DIR,server.DB_PATH=saved_dir,saved_db

    def test_unassigned_av_order_must_have_no_resource_or_operational_runtime(self):
        old = copy.deepcopy(BASE)
        new = copy.deepcopy(old)
        new["workSteps"].append(step())
        self.assertTrue(server.production_planning_change_allowed(old, new)[0])
        for patch in ({"machineId": "m1"}, {"anchorMode": "hard", "requiredFinish": "2026-10-21T16:00"}, {"baselinePlan": {"machineId": "m1"}}, {"planningWeek": "2026-10-19"}):
            bad = copy.deepcopy(old)
            bad["workSteps"].append(step(**patch))
            ok, reason = server.production_planning_change_allowed(old, bad)
            self.assertFalse(ok, patch)
            self.assertIn("Bereichsplanung", reason)

    def test_av_hours_are_only_allowed_for_confection(self):
        old = copy.deepcopy(BASE)
        for dep, hours, expected in (("konf1", 2.5, True), ("cnc", 2.5, False), ("cnc", 0, True)):
            new = copy.deepcopy(old)
            new["workSteps"].append(step(departmentId=dep, hours=hours))
            self.assertEqual(server.production_planning_change_allowed(old, new)[0], expected, dep)

    def test_department_cannot_change_av_fa_project_quantity_or_predecessors(self):
        old = {"departments": copy.deepcopy(DEPARTMENTS), "machines": [{"id": "m1", "departmentId": "cnc"}], "workSteps": [step(machineId="m1", handoffUnassigned=False, fa="4711", projectId="p1", targetQty=8, sequence=10, predecessorIds=[])]}
        for field, value in (("fa", "changed"), ("projectId", "p2"), ("targetQty", 9), ("sequence", 20), ("predecessorIds", ["other"]), ("avNote", "vom Bereich")):
            new = copy.deepcopy(old)
            new["workSteps"][0][field] = value
            self.assertFalse(server.department_change_allowed(old, new, "cnc")[0], field)

    def test_av_cannot_clear_department_takeover_marker(self):
        old = copy.deepcopy(BASE)
        old["workSteps"] = [step()]
        new = copy.deepcopy(old)
        new["workSteps"][0]["handoffUnassigned"] = False
        self.assertFalse(server.production_planning_change_allowed(old, new)[0])

    def test_department_cannot_remove_av_provenance_or_delete_and_recreate(self):
        old={"departments":copy.deepcopy(DEPARTMENTS),"machines":[{"id":"m1","departmentId":"cnc"}],"workSteps":[step(machineId="m1",handoffUnassigned=False,hours=2)]}
        for replacement in ([],[step(id="replacement",machineId="m1",handoffUnassigned=False,hours=4)]):
            new=copy.deepcopy(old);new["workSteps"]=replacement
            self.assertFalse(server.department_change_allowed(old,new,"cnc")[0])
        new=copy.deepcopy(old);new["workSteps"][0].pop("handoffUnassigned")
        self.assertFalse(server.department_change_allowed(old,new,"cnc")[0])
        self.assertFalse(server.production_planning_change_allowed(old,new)[0])

    def test_department_takeover_requires_same_department_resource_and_runtime(self):
        old = {"departments": copy.deepcopy(DEPARTMENTS), "machines": [{"id": "m1", "departmentId": "cnc"}, {"id": "m2", "departmentId": "konf1"}], "workSteps": [step()]}
        taken = copy.deepcopy(old)
        taken["workSteps"][0].update(machineId="m1", hours=4, handoffUnassigned=False)
        self.assertTrue(server.department_change_allowed(old, taken, "cnc")[0])
        for patch in ({"machineId": "m2", "hours": 4, "handoffUnassigned": False}, {"machineId": "m1", "hours": 0, "handoffUnassigned": False}, {"machineId": "m1", "hours": 4, "handoffUnassigned": True}):
            bad = copy.deepcopy(old)
            bad["workSteps"][0].update(patch)
            self.assertFalse(server.department_change_allowed(old, bad, "cnc")[0], patch)

    def test_same_department_twice_and_av_note(self):
        """V12.23.0: Ein FA darf denselben Bereich mehrfach enthalten; die AV-Notiz ist Text bis 500 Zeichen."""
        with tempfile.TemporaryDirectory() as tmp:
            server.DATA_DIR = Path(tmp); server.DB_PATH = server.DATA_DIR / "t.sqlite3"
            server.init_db(seed="werbetechnik")
            with server.db_session() as con:
                old = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
        dep = old["departments"][0]["id"]
        new = copy.deepcopy(old); new["workSteps"] = []
        base = dict(departmentId=dep, projectId="", fa="FA-MULTI", order="FA-MULTI", status="planned", hours=0, targetQty=10, pos=9001, description="")
        new["workSteps"] = [step(id="ws_a", sequence=10, **base), step(id="ws_b", sequence=20, avNote="zweite Maschine", **{**base, "pos": 9002})]
        ok, code, reason = server.validate_state({**old, "workSteps": []}, new)
        self.assertTrue(ok, f"{code} {reason}")
        for bad in ("x" * 501, 5):
            new["workSteps"][1]["avNote"] = bad
            ok, code, _ = server.validate_state({**old, "workSteps": []}, new)
            self.assertEqual((ok, code), (False, "MP-STEP-014"), bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Server-side invariants for admin users, module gates and retained pallet data."""
from __future__ import annotations

import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS " if ok else "FAIL ") + label)


cfg = server.config_defaults()
check(cfg["modules"]["palletLabels"] is False, "new configuration disables pallet labels by default")
check(all(cfg["modules"][k] is True for k in server.CONFIG_MODULES if k != "palletLabels"), "existing modules remain enabled by default")
legacy = {**cfg, "modules": {k: True for k in server.CONFIG_MODULES if k != "palletLabels"}}
check(server.modules_effective(legacy)["palletLabels"] is False, "legacy config without palletLabels gets disabled default")
check(server.modules_effective({**legacy, "modules": {**legacy["modules"], "palletLabels": True}})["palletLabels"] is True,
      "admin can opt pallet labels back on")

old = {"palletTemplates": [{"id": "tpl1", "name": "Legacy"}], "palletLabels": [{"id": "lab1", "sequence": 1}]}
new = {"palletTemplates": [{"id": "tpl1", "name": "Changed"}], "palletLabels": old["palletLabels"]}
server.CURRENT_CFG = legacy
mod, _ = server.module_state_guard(old, new)
check(mod == "palletLabels", "disabled pallet module blocks template edits")
mod, _ = server.module_state_guard(old, {**old})
check(not mod, "disabled pallet module accepts retained template and label data unchanged")

# Deactivating an existing area is a soft state change. Linked resources and orders survive both directions.
tmp = Path(tempfile.mkdtemp(prefix="mp-admin-v12191-"))
server.DATA_DIR = tmp
server.DB_PATH = tmp / "maschinenplanung.sqlite3"
server.init_db(seed="werbetechnik")
with server.db_session() as con:
    old = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
    new = json.loads(json.dumps(old))
    machine = new["machines"][0]
    dep = next(d for d in new["departments"] if d["id"] == machine.get("departmentId"))
    new["workSteps"].append({"id": "ws_pending", "sequence": 10, "planningType": "MACHINE", "pos": 10,
                             "departmentId": dep["id"], "projectId": "", "predecessorIds": [], "fa": "FA-PENDING",
                             "ab": "", "wt": "", "machineId": machine["id"], "altMachineId": "",
                             "allowAlternative": False, "order": "FA-PENDING", "articleNo": "", "description": "",
                             "targetQty": 0, "dueDate": "", "baselinePlan": None, "hours": 1, "goodQty": 0,
                             "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none",
                             "requiredStart": "", "requiredFinish": "", "createdAt": server.now_iso(),
                             "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "",
                             "pausedAt": "", "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": ""})
    dep["active"] = False
    linked_ids = [m["id"] for m in new["machines"] if m.get("departmentId") == dep["id"]]
    valid, code, reason = server.validate_state(old, new)
    check(valid, f"linked area deactivates safely ({code} {reason})")
    check([m["id"] for m in new["machines"] if m.get("departmentId") == dep["id"]] == linked_ids,
          "deactivation retains linked machine records")
    restored = json.loads(json.dumps(new))
    restored["departments"][0]["active"] = True
    valid, code, reason = server.validate_state(new, restored)
    check(valid and [m["id"] for m in restored["machines"] if m.get("departmentId") == dep["id"]] == linked_ids,
          "area can be reactivated with its original linked records")

# A department with only historical records must retain its identity, including
# requests that try to remove the linked rows along with the department.
archived=json.loads(json.dumps(old))
archived["departments"].append({"id":"archived_area","name":"Archivbereich","planningType":"MACHINE"})
archived["history"].append({"id":"h_archived_area","departmentId":"archived_area","recordType":"done"})
for clear_history in (False,True):
    removed=json.loads(json.dumps(archived));removed["departments"]=[d for d in removed["departments"] if d["id"]!="archived_area"]
    if clear_history: removed["history"]=[]
    valid,code,reason=server.validate_state(archived,removed)
    check(not valid and code=="MP-DEPT-006", "historical area cannot be deleted or cleared in the same request")
for active in (False,True):
    retained=json.loads(json.dumps(archived));next(d for d in retained["departments"] if d["id"]=="archived_area")["active"]=active
    valid,code,reason=server.validate_state(archived,retained)
    check(valid and retained["history"]==archived["history"], "historical area deactivates/reactivates without changing records")

failed = [label for ok, label in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} bestanden")
sys.exit(bool(failed))

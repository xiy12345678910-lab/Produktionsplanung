#!/usr/bin/env python3
"""Erzeugt einen künstlichen, realitätsnahen Datenstand für tests/test_regression.py.

Aufruf:  python tests/make_test_db.py <leerer-ordner>
         python tests/test_regression.py <derselbe-ordner>
Ersetzt nicht den Test gegen eine Kopie der echten Datenbank vor einem Update, macht den
Regressionstest aber ohne Live-Daten lauffähig (Abnahme, CI).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
import server  # noqa: E402

target = Path(sys.argv[1]).resolve()
target.mkdir(parents=True, exist_ok=True)
if (target / "maschinenplanung.sqlite3").exists():
    sys.exit(f"{target} enthält schon eine Datenbank – bitte leeren Ordner angeben.")
server.DATA_DIR = target
server.DB_PATH = target / "maschinenplanung.sqlite3"
server.init_db(seed="werbetechnik")

with server.DB_LOCK, server.db_session() as con:
    old = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
    new = json.loads(json.dumps(old))
    new["employees"] = [
        {"id": "e_cnc1", "name": "Max Fräser", "departmentId": "cnc", "active": True},
        {"id": "e_cnc2", "name": "Eva Dreher", "departmentId": "cnc", "active": True},
        {"id": "e_k1", "name": "Ole Näher", "departmentId": "konf1", "active": True},
    ]
    new["projects"] = [
        {"id": "p_seed1", "number": "P-2026-001", "phase": "accepted", "name": "Gehäuse", "customer": "Kunde A", "ab": "AB-1001", "dueDate": "2026-12-18", "log": [], "processes": []},
        {"id": "p_seed2", "number": "P-2026-002", "phase": "inquiry", "name": "Anfrage Deckel", "customer": "Kunde B", "ab": "", "dueDate": "", "log": [], "processes": []},
    ]
    m1 = next(m for m in new["machines"] if m["departmentId"] == "cnc")
    new["workSteps"].append({"id": "ws_seed1", "sequence": 10, "planningType": "MACHINE", "pos": 10, "departmentId": "cnc", "projectId": "", "predecessorIds": [],
                             "fs": "FS 1001", "ab": "AB-1001", "wt": "", "machineId": m1["id"], "altMachineId": "", "allowAlternative": False, "order": "FS 1001",
                             "articleNo": "", "description": "Seed", "targetQty": 5, "dueDate": "2026-11-20", "baselinePlan": None, "hours": 4, "goodQty": 0,
                             "scrapQty": 0, "status": "planned", "direction": "forward", "anchorMode": "none", "requiredStart": "", "requiredFinish": "",
                             "createdAt": server.now_iso(), "lockedStart": "", "lockedSegments": [], "actualStartedAt": "", "runningSince": "", "pausedAt": "",
                             "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": ""})
    ok, code, reason = server.validate_state(old, new)
    if not ok:
        sys.exit(f"Seed ungültig: {code} {reason}")
    con.execute("UPDATE state SET json=?, revision=revision+1 WHERE id=1", (json.dumps(new, ensure_ascii=False),))
print(f"Testdatenbank angelegt: {server.DB_PATH}")

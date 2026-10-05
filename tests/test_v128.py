#!/usr/bin/env python3
"""Server-Regeln V12.8.x ohne Browser (Formate, Parallelplätze, Bereichsarten, PM-Vorplan).

Aufruf:  python tests/test_v128.py
Arbeitet nur mit einem frischen Datenstand im Temp-Ordner.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
import release_gates  # noqa: E402
import server  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    RESULTS.append((bool(cond), label))
    print(("PASS " if cond else "FAIL ") + label)


tmp = Path(tempfile.mkdtemp(prefix="mp-v128-"))
server.DATA_DIR = tmp
server.DB_PATH = tmp / "maschinenplanung.sqlite3"
server.init_db()
with server.db_session() as con:
    BASE = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])


def state():
    return copy.deepcopy(BASE)


def step(sid, fs, mid, dep, **kw):
    x = {"id": sid, "sequence": 10, "planningType": "MACHINE", "pos": 10, "departmentId": dep, "projectId": "", "predecessorIds": [], "fs": fs, "ab": "", "wt": "", "machineId": mid, "altMachineId": "", "allowAlternative": False, "order": fs, "hours": 2, "status": "planned", "direction": "forward", "anchorMode": "none"}
    x.update(kw)
    return x


# --------------------------------------------------------------------------- V12.8.0 Formate
check(BASE.get("formats") == [] and BASE.get("baseFormats") == [], "Migration V12.8.0: Formatlisten im Serverstand angelegt")
s = state()
s["baseFormats"] = [{"id": "fg1", "departmentId": "thermoforming", "name": "Groß", "L": 1500, "B": 1000}]
s["formats"] = [{"id": "fm1", "number": "F-1", "departmentId": "thermoforming", "status": "active", "baseId": "fg1", "machineId": "", "L": 1500, "B": 1000, "H": 0, "rand": 100, "tools": [], "log": []}]
check(server.validate_state(BASE, s)[0], "Formate: gültiges Format akzeptiert")
bad = copy.deepcopy(s); bad["formats"][0]["status"] = "stored"
check(server.validate_state(BASE, bad)[1] == "MP-FMT-008", "Formate: eingelagert ohne Lagerort → MP-FMT-008")
bad = copy.deepcopy(s); bad["formats"].append({**s["formats"][0], "id": "fm2"})
check(server.validate_state(BASE, bad)[1] == "MP-FMT-003", "Formate: doppelte Formatnummer → MP-FMT-003")
ok, _ = server.department_change_allowed(BASE, s, "cnc")
check(not ok, "Formate: CNC-Leitung darf Tiefzieh-Format nicht anlegen")
ok, _ = server.department_change_allowed(BASE, s, "thermoforming")
check(ok, "Formate: Tiefzieh-Leitung darf eigenes Format anlegen")
ok, _ = server.production_planning_change_allowed(BASE, {**s, "baseFormats": BASE["baseFormats"]})
check(ok, "Formate: AV darf Formate anlegen")
ok, _ = server.production_planning_change_allowed(BASE, s)
check(not ok, "Formate: AV darf keine Grundformate anlegen")

# --------------------------------------------------------------------------- V12.8.1 Parallelplätze
s = state()
m = s["machines"][0]
m["lanes"] = 2
check(server.validate_state(BASE, s)[0], "Parallel: lanes=2 akzeptiert")
m["lanes"] = 21
check(server.validate_state(BASE, s)[1] == "MP-MACH-015", "Parallel: lanes=21 → MP-MACH-015")
m["lanes"] = 2
check(release_gates.machine_lanes(s, m["id"]) == 2 and release_gates.machine_lanes(BASE, m["id"]) == 1, "Parallel: machine_lanes liest Feld, Standard 1")
from datetime import datetime  # noqa: E402
seg = {"start": datetime(2026, 10, 5, 8), "end": datetime(2026, 10, 5, 12)}
others = [{"start": datetime(2026, 10, 5, 7), "end": datetime(2026, 10, 5, 9)}, {"start": datetime(2026, 10, 5, 8, 30), "end": datetime(2026, 10, 5, 10)}, {"start": datetime(2026, 10, 5, 11), "end": datetime(2026, 10, 5, 13)}]
check(release_gates.max_parallel(seg, others) == 2, "Parallel: max_parallel zählt gleichzeitige Belegung (2)")

# --------------------------------------------------------------------------- V12.8.2 Bereichsarten
s = state()
s["departments"].append({"id": "dep_dev", "name": "Musterbau", "planningType": "MACHINE", "kind": "development"})
check(server.validate_state(BASE, s)[0], "Bereiche: Entwicklungsbereich ohne Maschine akzeptiert")
bad = copy.deepcopy(s); bad["machines"].append({**bad["machines"][0], "id": "mx", "departmentId": "dep_dev"})
check(server.validate_state(BASE, bad)[1] == "MP-DEPT-004", "Bereiche: Maschine im Entwicklungsbereich → MP-DEPT-004")
bad = copy.deepcopy(s); bad["departments"][-1]["kind"] = "foo"
check(server.validate_state(BASE, bad)[1] == "MP-DEPT-004", "Bereiche: unbekannte Art → MP-DEPT-004")
bad = copy.deepcopy(s); bad["departments"].append({"id": "sales", "name": "X", "planningType": "MACHINE"})
check(server.validate_state(BASE, bad)[1] == "MP-DEPT-005", "Bereiche: reservierte ID 'sales' → MP-DEPT-005")
ok, why = server.gf_change_allowed(BASE, s)
check(ok, f"Bereiche: GF legt Entwicklungsbereich an {why}")
p = state()
p["departments"].append({"id": "dep_lack", "name": "Lackierung", "planningType": "MACHINE"})
p["machines"].append({"id": "m_lack", "name": "Lackierlinie", "departmentId": "dep_lack", "kind": "line", "crew": 1, "setupMinutes": 15, "start": "2026-10-05T06:30", "committedUntil": "", "defaultShiftMode": "2", "staffRequired": 0})
check(server.validate_state(BASE, p)[0] and server.gf_change_allowed(BASE, p)[0], "Bereiche: GF legt Produktionsbereich mit erster Linie an")
p2 = copy.deepcopy(p); p2["machines"].append({**p["machines"][-1], "id": "m_lack2"})
check(not server.gf_change_allowed(p, p2)[0], "Bereiche: GF legt keine zweite Maschine an")
p3 = copy.deepcopy(p); p3["machines"][0]["setupMinutes"] = 9
check(not server.gf_change_allowed(p, p3)[0], "Bereiche: GF ändert keine bestehende Maschine")
p4 = copy.deepcopy(p); p4["departments"] = [d for d in p4["departments"] if d["id"] != "dep_lack"]; p4["machines"] = [x for x in p4["machines"] if x["id"] != "m_lack"]
check(not server.gf_change_allowed(p, p4)[0], "Bereiche: GF löscht keine Bereiche")
p5 = copy.deepcopy(p); p5["departments"][-1]["active"] = False; p5["departments"][-1]["name"] = "Lack alt"
check(server.gf_change_allowed(p, p5)[0], "Bereiche: GF benennt um und deaktiviert")
p6 = copy.deepcopy(p); p6["workSteps"].append(step("ws1", "FS 1", "m_lack", "dep_lack"))
p7 = copy.deepcopy(p6); p7["departments"][-1]["active"] = False
check(server.validate_state(p6, p7)[1] == "MP-DEPT-006", "Bereiche: Deaktivieren mit offenen Aufträgen → MP-DEPT-006")
check(server.production_department_ids(s) == {d["id"] for d in BASE["departments"]}, "Bereiche: Entwicklungsbereich ist kein Fertigungsbereich (AV)")
proj_old = copy.deepcopy(s)
proj_old["projects"] = [{"id": "p1", "number": "P-1", "phase": "accepted", "name": "Test", "customer": "K", "processes": [], "log": []}]
proj_new = copy.deepcopy(proj_old)
proj_new["projects"][0]["processes"] = [{"id": "pr1", "areaId": "dep_dev", "title": "Muster", "status": "open"}]
ok, _ = server.project_changes_allowed(proj_old, proj_new, "production_planning", "av")
check(not ok, "Bereiche: AV plant keine Prozesse im Entwicklungsbereich")
proj_new["projects"][0]["processes"][0]["areaId"] = "cnc"
ok, why = server.project_changes_allowed(proj_old, proj_new, "production_planning", "av")
check(ok, f"Bereiche: AV plant Prozess in Produktion {why}")

failed = [x for x in RESULTS if not x[0]]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

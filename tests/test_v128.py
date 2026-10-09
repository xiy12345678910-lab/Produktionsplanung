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
server.init_db(seed="werbetechnik")
with server.db_session() as con:
    BASE = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])


def state():
    return copy.deepcopy(BASE)


def step(sid, fa, mid, dep, **kw):
    x = {"id": sid, "sequence": 10, "planningType": "MACHINE", "pos": 10, "departmentId": dep, "projectId": "", "predecessorIds": [], "fa": fa, "ab": "", "wt": "", "machineId": mid, "altMachineId": "", "allowAlternative": False, "order": fa, "hours": 2, "status": "planned", "direction": "forward", "anchorMode": "none"}
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
check(not ok, "Formate: AV darf keine Formate anlegen (ab V12.10.2, Formate nur Tiefziehen/Admin)")
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
bad = copy.deepcopy(s); bad["departments"][-1]["name"] = " " + bad["departments"][0]["name"].upper()
check(server.validate_state(BASE, bad)[1] == "MP-DEPT-005", "Bereiche: Umbenennen auf vorhandenen Namen (Groß-/Kleinschreibung egal) → MP-DEPT-005")
dup_old = copy.deepcopy(bad); dup_new = copy.deepcopy(bad); dup_new["departments"][-1]["kind"] = "sales"
check(server.validate_state(dup_old, dup_new)[0], "Bereiche: bereits vorhandenes Namensduplikat blockiert das Speichern nicht")
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
p6 = copy.deepcopy(p); p6["workSteps"].append(step("ws1", "FA 1", "m_lack", "dep_lack"))
p7 = copy.deepcopy(p6); p7["departments"][-1]["active"] = False
check(server.validate_state(p6, p7)[0] and p7["workSteps"] == p6["workSteps"] and p7["machines"] == p6["machines"], "Bereiche: Deaktivieren erhält offene Aufträge und Maschinen")
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

# --------------------------------------------------------------------------- V12.8.3 PM-Vorplan
pa = state()
pa["projects"] = [{"id": "p1", "number": "P-1", "phase": "accepted", "name": "Test", "customer": "K", "ab": "AB-1", "dueDate": "2026-11-30", "log": [],
                   "processes": [{"id": "pr1", "areaId": "cnc", "title": "Fräsen", "status": "open", "startDate": "2026-10-12", "dueDate": "2026-10-16"}]}]
check(server.validate_state(BASE, pa)[0], "PM-Vorplan: Ausgangsprojekt gültig " + str(server.validate_state(BASE, pa)[1:]))
av = copy.deepcopy(pa); pr = av["projects"][0]["processes"][0]
pr["pmPlan"] = {"startDate": "2026-10-12", "dueDate": "2026-10-16", "at": "2026-10-05T08:00:00Z", "by": "av"}; pr["dueDate"] = "2026-10-21"
check(server.validate_state(pa, av)[0], "PM-Vorplan: Schnappschuss strukturell gültig")
check(server.project_changes_allowed(pa, av, "production_planning", "av")[0], "PM-Vorplan: AV sichert PM-Termin beim ersten Überschreiben")
fake = copy.deepcopy(av); fake["projects"][0]["processes"][0]["pmPlan"]["dueDate"] = "2026-10-30"
check(not server.project_changes_allowed(pa, fake, "production_planning", "av")[0], "PM-Vorplan: AV darf keinen veränderten PM-Termin sichern")
again = copy.deepcopy(av); again["projects"][0]["processes"][0]["pmPlan"]["dueDate"] = "2026-10-21"
check(not server.project_changes_allowed(av, again, "production_planning", "av")[0], "PM-Vorplan: Schnappschuss nur einmal")
pm = copy.deepcopy(av); pm["projects"][0]["processes"][0]["dueDate"] = "2026-10-23"
check(not server.project_changes_allowed(av, pm, "project_management", "pm")[0], "PM-Vorplan: PM-Termine nach AV-Übernahme gesperrt")
pm2 = copy.deepcopy(pa); pm2["projects"][0]["processes"][0]["dueDate"] = "2026-10-19"
check(server.project_changes_allowed(pa, pm2, "project_management", "pm")[0], "PM-Vorplan: PM terminiert, solange AV nicht übernommen hat")
pm3 = copy.deepcopy(av); pm3["projects"][0]["processes"][0]["pmPlan"] = None
check(not server.project_changes_allowed(av, pm3, "project_management", "pm")[0], "PM-Vorplan: PM löscht Schnappschuss nicht")
bad = copy.deepcopy(av); bad["projects"][0]["processes"][0]["pmPlan"] = {"dueDate": "x"}
check(server.validate_state(pa, bad)[1] == "MP-PM-017", "PM-Vorplan: ungültiges Datum → MP-PM-017")

failed = [x for x in RESULTS if not x[0]]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

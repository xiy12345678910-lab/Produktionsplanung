#!/usr/bin/env python3
"""Server-Regeln V12.18.0 ohne Browser: Dauer nach Besetzung (effortScaling, crewMax, laneStaff), Migration, Freigabeplan.

Aufruf:  python tests/test_effort.py
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
import server  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    RESULTS.append((bool(cond), label))
    print(("PASS " if cond else "FAIL ") + label)


tmp = Path(tempfile.mkdtemp(prefix="mp-effort-"))
server.DATA_DIR = tmp
server.DB_PATH = tmp / "maschinenplanung.sqlite3"
server.init_db(seed="werbetechnik")


def load():
    with server.db_session() as con:
        return json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])


BASE = load()
M = BASE["machines"][0]
MID = M["id"]


def state():
    return copy.deepcopy(BASE)


# --------------------------------------------------------------------------- Migration: additiv und idempotent
check(all(m.get("effortScaling") is False and m.get("crewMax") == 0 and m.get("laneStaff") == {} for m in BASE["machines"]),
      "Migration: jede Maschine hat effortScaling=false, crewMax=0, laneStaff={}")
with server.db_session() as con:
    st = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
    st["machines"][0].pop("effortScaling")
    st["machines"][0].pop("crewMax")
    st["machines"][0].pop("laneStaff")
    st["machines"][1]["effortScaling"] = True      # vorhandene Werte bleiben
    st["machines"][1]["crewMax"] = 5
    st["machines"][1]["laneStaff"] = {"2": 3}
    con.execute("UPDATE state SET json=? WHERE id=1", (json.dumps(st),))
    server.migrate_state_v1218(con)
a = load()
check(a["machines"][0]["effortScaling"] is False and a["machines"][0]["crewMax"] == 0 and a["machines"][0]["laneStaff"] == {}, "Migration: fehlende Felder ergaenzt")
check(a["machines"][1]["effortScaling"] is True and a["machines"][1]["crewMax"] == 5 and a["machines"][1]["laneStaff"] == {"2": 3}, "Migration: vorhandene Werte bleiben")
with server.db_session() as con:
    server.migrate_state_v1218(con)
b = load()
check(a == b, "Migration ist idempotent (zweiter Lauf aendert nichts)")
with server.db_session() as con:
    con.execute("UPDATE state SET json=? WHERE id=1", (json.dumps(BASE),))

# --------------------------------------------------------------------------- Validierung
def machine_case(**kw):
    s = state()
    s["machines"][0].update(kw)
    return server.validate_state(BASE, s)


check(machine_case(effortScaling=True, crewMax=4, laneStaff={"1": 1, "2": 2})[0], "gueltig: effortScaling, crewMax, laneStaff")
check(machine_case()[0], "Bestand ohne Aenderung bleibt gueltig")
check(machine_case(effortScaling="ja")[1] == "MP-MACH-017", "effortScaling muss true/false sein -> MP-MACH-017")
check(machine_case(crewMax=-1)[1] == "MP-MACH-018", "crewMax < 0 -> MP-MACH-018")
check(machine_case(crewMax=100)[1] == "MP-MACH-018", "crewMax > 99 -> MP-MACH-018")
check(machine_case(crewMax=2.5)[1] == "MP-MACH-018", "crewMax nicht ganzzahlig -> MP-MACH-018")
check(machine_case(crewMax=True)[1] == "MP-MACH-018", "crewMax true -> MP-MACH-018")
check(machine_case(laneStaff={"0": 1})[1] == "MP-MACH-019", "laneStaff Platz 0 -> MP-MACH-019")
check(machine_case(laneStaff={"21": 1})[1] == "MP-MACH-019", "laneStaff Platz 21 -> MP-MACH-019")
check(machine_case(laneStaff={"1": 10})[1] == "MP-MACH-019", "laneStaff Bedarf 10 -> MP-MACH-019")
check(machine_case(laneStaff=[1, 2])[1] == "MP-MACH-019", "laneStaff als Liste -> MP-MACH-019")
check(machine_case(laneStaff={"x": 1})[1] == "MP-MACH-019", "laneStaff Schluessel kein Platz -> MP-MACH-019")

# --------------------------------------------------------------------------- Freigabeplan mit Besetzung je Segment
dep = M["departmentId"]
day = "2026-09-07"


def released(hours, segs, effort=True, crew=1):
    s = state()
    s["machines"][0]["effortScaling"] = True
    o = {"id": "ws_eff", "sequence": 10, "planningType": "MACHINE", "pos": 10, "departmentId": dep, "projectId": "", "predecessorIds": [], "fa": "FA 1",
         "ab": "", "wt": "", "machineId": MID, "altMachineId": "", "allowAlternative": False, "order": "FA 1", "hours": hours, "status": "planned",
         "direction": "forward", "anchorMode": "none"}
    old = copy.deepcopy(s)
    old["workSteps"] = [dict(o)]
    o["status"] = "released"
    o["baselinePlan"] = {"capturedAt": "2026-09-07T05:00:00Z", "machineId": MID, "setupMinutes": float(M.get("setupMinutes") or 0), "crew": crew,
                         "start": segs[0]["start"], "end": segs[-1]["end"], "segments": segs, "planType": "auto", "anchor": ""}
    if effort:
        o["baselinePlan"]["effort"] = True
    s["workSteps"] = [o]
    return server.validate_state(old, s)


seg = lambda a, z, crew=None, **kw: {"start": f"{day}T{a}", "end": f"{day}T{z}", "shift": "single", **({"crew": crew} if crew else {}), **kw}  # noqa: E731
good = [seg("07:00:00", "08:30:00", 2), seg("10:00:00", "11:00:00", 4)]      # 3 Ph + 4 Ph
check(released(7, good)[0], "Freigabeplan Dauer nach Besetzung: 1,5 h x 2 + 1 h x 4 = 7 Ph wird akzeptiert")
r = released(8, good)
check(not r[0] and r[1] == "MP-PLAN-047", "Freigabeplan: Personenstunden passen nicht (8 statt 7) -> MP-PLAN-047")
r = released(7, [seg("07:00:00", "08:30:00", 2), seg("10:00:00", "11:00:00")])
check(not r[0] and r[1] == "MP-PLAN-047", "Freigabeplan: Segment ohne Besetzung -> MP-PLAN-047")
r = released(7, [seg("07:00:00", "08:30:00", 0.5), seg("10:00:00", "11:00:00", 4)])
check(not r[0] and r[1] == "MP-PLAN-047", "Freigabeplan: ungueltige Besetzung 0,5 -> MP-PLAN-047")
r = released(10, good, effort=False)
check(not r[0] and r[1] == "MP-PLAN-047", "Ohne effort-Kennzeichen gilt die bisherige Regel (Stunden / crew), Plan wird abgelehnt")
r = released(2.5, good, effort=False)
check(r[0], "Bisheriger Freigabeplan (Stunden / crew = Dauer) bleibt unveraendert gueltig")

failed = [r for r in RESULTS if not r[0]]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

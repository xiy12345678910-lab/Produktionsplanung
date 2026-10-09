#!/usr/bin/env python3
"""#47 Block F: Charge je Maschine – Server prüft die Maschinenfelder (MP-MACH-020/021), die eingefrorene
Charge im Freigabeplan (MP-PLAN-047/072) und rechnet beim Umplanen laufender FA mit derselben Formel.

Aufruf:  python tests/test_batch_server.py
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
from release_gates import batch_plan  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    RESULTS.append((bool(cond), label))
    print(("PASS " if cond else "FAIL ") + label)


tmp = Path(tempfile.mkdtemp(prefix="mp-batch-"))
server.DATA_DIR = tmp
server.DB_PATH = tmp / "maschinenplanung.sqlite3"
server.init_db(seed="werbetechnik")
with server.db_session() as con:
    BASE = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
M = BASE["machines"][0]
MID = M["id"]
GOOD = {"size": 100, "minutes": 60, "parallel": 1, "cleanMinutes": 30, "setup": "order", "partial": "full"}

# --------------------------------------------------------------------------- Bestand / Migration
check(all("batch" not in m for m in BASE["machines"]), "Bestand: keine Maschine hat eine Charge (Standard aus)")
check(server.validate_state(BASE, copy.deepcopy(BASE))[0], "Bestand ohne Charge bleibt gültig")


def machine_case(batch, **kw):
    s = copy.deepcopy(BASE)
    s["machines"][0]["batch"] = batch
    s["machines"][0].update(kw)
    return server.validate_state(BASE, s)


check(machine_case(None)[0], "batch=null = aus, gültig")
check(machine_case(GOOD)[0], "gültige Charge wird akzeptiert")
check(machine_case({"size": 2.5, "minutes": 1})[0], "Minimal-Charge (nur Größe + Dauer) wird akzeptiert")
for bad, label in [
    ({"size": 0, "minutes": 60}, "Chargengröße 0"),
    ({"size": -5, "minutes": 60}, "Chargengröße negativ"),
    ({"size": "100", "minutes": 60}, "Chargengröße als Text"),
    ({"size": 100}, "Dauer fehlt"),
    ({"size": 100, "minutes": 0}, "Dauer 0"),
    ({"size": 100, "minutes": 1.5}, "Dauer keine ganze Minute"),
    ({"size": 100, "minutes": 14401}, "Dauer > 240 h"),
    ({**GOOD, "parallel": 0}, "Chargen gleichzeitig 0"),
    ({**GOOD, "parallel": 2.5}, "Chargen gleichzeitig 2,5"),
    ({**GOOD, "parallel": 100}, "Chargen gleichzeitig 100"),
    ({**GOOD, "cleanMinutes": -1}, "Reinigung negativ"),
    ({**GOOD, "cleanMinutes": 1441}, "Reinigung > 24 h"),
    ({**GOOD, "setup": "x"}, "Rüsten unbekannt"),
    ({**GOOD, "partial": "x"}, "Teilcharge unbekannt"),
    ({**GOOD, "extra": 1}, "unbekanntes Feld"),
    ([1, 2], "Liste statt Objekt"),
    (True, "true statt Objekt"),
]:
    r = machine_case(bad)
    check(not r[0] and r[1] == "MP-MACH-020", f"{label} -> MP-MACH-020 ({r[1]})")
r = machine_case(GOOD, effortScaling=True)
check(not r[0] and r[1] == "MP-MACH-021", f"Charge + Dauer nach Besetzung -> MP-MACH-021 ({r[1]})")

# --------------------------------------------------------------------------- Freigabeplan mit eingefrorener Charge
dep = M["departmentId"]
day = "2026-09-07"
setup = float(M.get("setupMinutes") or 0)
seg = lambda a, z: {"start": f"{day}T{a}", "end": f"{day}T{z}", "shift": "single"}  # noqa: E731
# 200 Stk, 100 je Charge, 1:00, Reinigung 0:30 -> 2 × 1:00 + 1 × 0:30 = 2,5 h
SEGS = [seg("07:00:00", "08:30:00"), seg("10:00:00", "11:00:00")]
check(setup == 0, "Testmaschine ohne Umrüstzeit (Segmente = Chargen-Stunden)")
check(batch_plan(GOOD, 200, setup)["hours"] == 2.5, "Formel: 2 Chargen × 1:00 + Reinigung 1 × 0:30 = 2,5 h")


def released(machine_batch, frozen, qty=200, hours=2.5, segs=SEGS):
    s = copy.deepcopy(BASE)
    if machine_batch is not None:
        s["machines"][0]["batch"] = machine_batch
    o = {"id": "ws_b", "sequence": 10, "planningType": "MACHINE", "pos": 10, "departmentId": dep, "projectId": "", "predecessorIds": [], "fa": "FA B",
         "ab": "", "wt": "", "machineId": MID, "altMachineId": "", "allowAlternative": False, "order": "FA B", "hours": hours, "targetQty": qty,
         "status": "planned", "direction": "forward", "anchorMode": "none"}
    old = copy.deepcopy(s)
    old["workSteps"] = [dict(o)]
    o["status"] = "released"
    o["baselinePlan"] = {"capturedAt": "2026-09-07T05:00:00Z", "machineId": MID, "setupMinutes": setup, "crew": 1,
                         "start": segs[0]["start"], "end": segs[-1]["end"], "segments": segs, "planType": "auto", "anchor": ""}
    if frozen is not None:
        o["baselinePlan"]["batch"] = frozen
    s["workSteps"] = [o]
    return server.validate_state(old, s)


FROZEN = {**GOOD, "qty": 200, "hours": 2.5}
r = released(GOOD, FROZEN)
check(r[0], f"Freigabe mit eingefrorener Charge (2,5 h) wird akzeptiert ({r[1:]})")
r = released(GOOD, FROZEN, hours=7)
check(r[0], "Sollstunden des FA zählen bei Charge nicht (Charge hat Vorrang)")
r = released(GOOD, None)
check(not r[0] and r[1] in {"MP-PLAN-047", "MP-PLAN-072"}, f"Charge aktiv, aber nicht eingefroren -> abgelehnt ({r[1]})")
r = released(GOOD, None, hours=2.5)
check(not r[0] and r[1] == "MP-PLAN-072", f"Charge aktiv, Plan ohne Charge (Dauer passt zufällig) -> MP-PLAN-072 ({r[1]})")
r = released({**GOOD, "cleanMinutes": 15}, FROZEN)
check(not r[0] and r[1] == "MP-PLAN-072", f"Eingefrorene Charge weicht von der Maschine ab -> MP-PLAN-072 ({r[1]})")
r = released(GOOD, {**FROZEN, "qty": 150})
check(not r[0] and r[1] in {"MP-PLAN-047", "MP-PLAN-072"}, f"Eingefrorene Menge weicht ab -> abgelehnt ({r[1]})")
r = released(GOOD, {**FROZEN, "hours": 3})
check(not r[0] and r[1] == "MP-PLAN-047", f"Eingefrorene Chargen-Stunden falsch -> MP-PLAN-047 ({r[1]})")
r = released(GOOD, FROZEN, segs=[seg("07:00:00", "08:30:00")])
check(not r[0] and r[1] == "MP-PLAN-047", f"Plandauer passt nicht zur Charge -> MP-PLAN-047 ({r[1]})")
r = released(None, FROZEN)
check(not r[0] and r[1] == "MP-PLAN-072", f"Maschine ohne Charge, Plan mit Charge -> MP-PLAN-072 ({r[1]})")
r = released(GOOD, None, qty=0, hours=2.5)
check(r[0], f"Charge aktiv, aber FA ohne Menge -> bisherige Regel (Sollstunden) ({r[1:]})")
r = released(None, None, hours=2.5)
check(r[0], "Ohne Charge: bisherige Regel unverändert")

# --------------------------------------------------------------------------- production_replan nutzt die eingefrorene Chargen-Dauer
o = {"id": "ws_r", "planningType": "MACHINE", "machineId": MID, "hours": 99, "targetQty": 200, "status": "running",
     "baselinePlan": {"machineId": MID, "setupMinutes": 0, "crew": 3, "batch": FROZEN}}
captured = {}
orig_actual, orig_segments = server.production_actual, server.production_segments
try:
    server.production_actual = lambda state, order, stamp: ([], 0.0, 0.0)

    def fake_segments(state, order, stamp, remaining, effort, setup_left, crew):
        captured.update(remaining=remaining, effort=effort)
        return [{"start": "2026-09-07T07:00:00", "end": "2026-09-07T09:30:00"}]
    server.production_segments = fake_segments
    server.production_replan(BASE, o, "2026-09-07T07:00:00", 1)
finally:
    server.production_actual, server.production_segments = orig_actual, orig_segments
check(captured.get("remaining") == 2.5 and captured.get("effort") is False,
      f"production_replan: Rest = Chargen-Stunden 2,5 h, nicht Sollstunden/Besetzung ({captured})")

failed = [r for r in RESULTS if not r[0]]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

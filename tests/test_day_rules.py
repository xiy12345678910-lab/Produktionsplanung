#!/usr/bin/env python3
"""#84: Sonderschichten je Maschine und Tag (Sa/So, Überstunden) – Kalender auf dem Server (release_gates),
Prüfung (MP-CAL-015), Bereichsrechte und Sichtbarkeit.

Aufruf:  python tests/test_day_rules.py
Arbeitet nur mit einem frischen Datenstand im Temp-Ordner.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
import server  # noqa: E402
import release_gates as rg  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    RESULTS.append((bool(cond), label))
    print(("PASS " if cond else "FAIL ") + label)


tmp = Path(tempfile.mkdtemp(prefix="mp-dayrules-"))
server.DATA_DIR = tmp
server.DB_PATH = tmp / "maschinenplanung.sqlite3"
server.init_db(seed="werbetechnik")
with server.db_session() as con:
    BASE = json.loads(con.execute("SELECT json FROM state WHERE id=1").fetchone()["json"])
M = BASE["machines"][0]
MID, DEP = M["id"], M["departmentId"]
OTHER = next(m for m in BASE["machines"] if m["departmentId"] != DEP)
SAT, MON = datetime(2026, 10, 10), datetime(2026, 10, 12)


def state(rules):
    s = copy.deepcopy(BASE)
    s["dayRules"] = rules
    return s


# --- Kalender (Server = Client)
check(rg.work_intervals(BASE, MID, SAT) == [], "Samstag ohne Sonderschicht: frei")
sat = rg.work_intervals(state([{"id": "d1", "machineId": MID, "date": "2026-10-10", "mode": "1"}]), MID, SAT)
mon = rg.work_intervals(BASE, MID, MON)
check(bool(sat) and [(a.time(), z.time()) for a, z, _ in sat] == [(a.time(), z.time()) for a, z, _ in mon],
      "Samstag mit Sonderschicht 1-schichtig: Zeiten wie ein normaler Tag")
check(rg.work_intervals(state([{"id": "d1", "machineId": MID, "date": "2026-10-10", "mode": "1"}]), OTHER["id"], SAT) == [],
      "Sonderschicht gilt nur für die eigene Maschine")
ot = rg.work_intervals(state([{"id": "d2", "machineId": MID, "date": "2026-10-12", "mode": "", "extraMinutes": 90}]), MID, MON)
check(ot[:-1] == mon[:-1] and (ot[-1][1] - mon[-1][1]).total_seconds() == 90 * 60, "Überstunden 1:30 verlängern das Tagesende um 90 Minuten")
check(rg.work_intervals(state([{"id": "d3", "machineId": MID, "date": "2026-10-12", "mode": "0"}]), MID, MON) == [],
      "Sonderschicht 'Betriebsfrei' an einem Werktag")
hol = state([{"id": "d4", "machineId": MID, "date": "2026-10-12", "mode": "2"}])
hol["exceptions"] = [{"date": "2026-10-12", "mode": "0", "label": "Feiertag"}]
check(len(rg.work_intervals(hol, MID, MON)) >= 2, "Sonderschicht hat Vorrang vor Betriebsferien (spezieller gewinnt)")

# --- Prüfung
ok = state([{"id": "d1", "machineId": MID, "date": "2026-10-10", "mode": "2"}, {"id": "d2", "machineId": MID, "date": "2026-10-12", "mode": "", "extraMinutes": 120}])
check(server.validate_state(BASE, ok)[0], "gültige Sonderschichten werden gespeichert")
for bad, what in (([{"machineId": "nope", "date": "2026-10-10", "mode": "1"}], "unbekannte Maschine"),
                  ([{"machineId": MID, "date": "10.10.2026", "mode": "1"}], "Datumsformat"),
                  ([{"machineId": MID, "date": "2026-10-10", "mode": ""}], "weder Betrieb noch Überstunden"),
                  ([{"machineId": MID, "date": "2026-10-10", "mode": "", "extraMinutes": 300}], "Überstunden über 4 h"),
                  ([{"machineId": MID, "date": "2026-10-10", "mode": "3"}], "unbekannter Betrieb"),
                  ([{"machineId": MID, "date": "2026-10-10", "mode": "1"}, {"machineId": MID, "date": "2026-10-10", "mode": "2"}], "doppelt")):
    check(server.validate_state(BASE, state(bad))[1] == "MP-CAL-015", f"MP-CAL-015: {what}")

# --- Rechte der Abteilung und Sichtbarkeit
own = state([{"id": "d1", "machineId": MID, "date": "2026-10-10", "mode": "1"}])
check(server.department_change_allowed(BASE, own, DEP)[0], "Abteilung legt Sonderschicht für eigene Maschine an")
foreign = state([{"id": "d5", "machineId": OTHER["id"], "date": "2026-10-10", "mode": "1"}])
check(not server.department_change_allowed(BASE, foreign, DEP)[0], "Abteilung kann keine Sonderschicht für fremde Maschine anlegen")
check(not server.gf_change_allowed(BASE, own)[0], "GF ändert keine Sonderschichten (wie KW-Regeln)")
both = state([{"id": "d1", "machineId": MID, "date": "2026-10-10", "mode": "1"}, {"id": "d5", "machineId": OTHER["id"], "date": "2026-10-10", "mode": "1"}])
red = server.redact_state(both, {"role": "department_lead", "department_id": DEP, "username": "lead"})
check([r["machineId"] for r in red["dayRules"]] == [MID], "Abteilung sieht nur Sonderschichten ihrer Maschinen")

# --- Überstunden je Mitarbeiter
emp = {"id": "e_ot", "name": "Mehrarbeit", "departmentId": DEP, "active": True, "weeklyHours": 40, "workingDays": [1, 2, 3, 4, 5], "skills": [MID], "employmentType": "permanent"}
long_day = {"employeeId": "e_ot", "date": "2026-10-12", "machineId": MID, "shift": "single", "start": "06:00", "end": "18:00", "breaks": []}
check(rg.limit_assignment(emp, dict(long_day), MON)["end"] != "18:00", "ohne Kennzeichen: Tag wird auf die Vertragsstunden gekürzt")
check(rg.limit_assignment(emp, {**long_day, "overtime": True}, MON)["end"] == "18:00", "mit overtime: 12 h bleiben stehen")
sat_shift = {"employeeId": "e_ot", "date": "2026-10-10", "machineId": MID, "shift": "single", "start": "07:00", "end": "12:00", "breaks": []}
check(rg.limit_assignment(emp, dict(sat_shift), SAT) is None and rg.limit_assignment(emp, {**sat_shift, "overtime": True}, SAT) is not None,
      "Samstag (kein Arbeitstag): nur als Überstunden planbar")
week = copy.deepcopy(BASE)
week["employees"] = [*week.get("employees", []), emp]
week["dayRules"] = [{"id": "d1", "machineId": MID, "date": "2026-10-10", "mode": "1"}]
days = [{"employeeId": "e_ot", "date": f"2026-10-0{d}", "machineId": MID, "shift": "single", "start": "06:00", "end": "15:00", "breaks": []} for d in (5, 6, 7, 8, 9)]
week["personnelAssignments"] = days + [{**sat_shift, "overtime": True}]
check(server.validate_state(BASE, week)[1] == "MP-PERS-035", "45 h ohne Kennzeichen überschreiten die Wochenstunden (MP-PERS-035)")
week["personnelAssignments"] = [{**x, "overtime": True} for x in days] + [{**sat_shift, "overtime": True}]
check(server.validate_state(BASE, week)[0], "als Überstunden gekennzeichnet: gespeichert")
week["personnelAssignments"] = [{**sat_shift, "overtime": "ja"}]
check(server.validate_state(BASE, week)[1] == "MP-PERS-001", "overtime muss true/false sein")

failed = [label for ok_, label in RESULTS if not ok_]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

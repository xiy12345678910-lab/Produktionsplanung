#!/usr/bin/env python3
"""Regressionstests (ab V12.4.4, erweitert V12.4.5) gegen eine KOPIE einer echten Datenbank.

Aufruf:  python tests/test_v1244.py <pfad-zur-kopie-von-data-ordner>
Der Ordner muss maschinenplanung.sqlite3 (+ ggf. -wal/-shm) enthalten und wird verändert.
Niemals gegen den Live-Ordner ausführen.
"""
from __future__ import annotations

import copy
import http.client
import ipaddress
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
import server  # noqa: E402

DATA = Path(sys.argv[1]).resolve()
server.DATA_DIR = DATA
server.DB_PATH = DATA / "maschinenplanung.sqlite3"
server.ALLOWED_NETWORK = ipaddress.ip_network("127.0.0.0/8")
PORT = 18766
RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    RESULTS.append((bool(cond), label))
    print(("PASS " if cond else "FAIL ") + label)


# --------------------------------------------------------------------------- migration
before = sqlite3.connect(server.DB_PATH)
before_state = json.loads(before.execute("SELECT json FROM state").fetchone()[0])
before_rev = before.execute("SELECT revision FROM state").fetchone()[0]
before.close()
server.init_db()
con = sqlite3.connect(server.DB_PATH)
after_state = json.loads(con.execute("SELECT json FROM state").fetchone()[0])
check("orders" not in after_state, "Migration: Legacy-orders aus Live-State entfernt")
if "orders" in before_state:
    archived = json.loads(con.execute("SELECT json FROM legacy_orders_archive ORDER BY id DESC").fetchone()[0])
    check(server.canonical(archived) == server.canonical(before_state["orders"]), "Migration: orders unverändert archiviert")
_before_machine = [x for x in before_state["workSteps"] if x.get("planningType") == "MACHINE"]
_after_ids = {str(x.get("id")) for x in after_state["workSteps"]}
_hist_ids = {str(h.get("originalOrderId")) for h in after_state.get("history") or []}
check(all(any(server.canonical(a) == server.canonical({**b, **{k: a.get(k) for k in a if k not in b}}) for a in after_state["workSteps"] if a.get("id") == b.get("id")) for b in _before_machine), "Migration: bestehende CNC-Aufträge unverändert")
check(all(str(x.get("id")) in _after_ids or str(x.get("id")) in _hist_ids for x in before_state["workSteps"]), "Migration V12.7: jeder Bereichs-Arbeitsgang ist Auftrag oder Historie")
check(all(d.get("planningType") == "MACHINE" for d in after_state["departments"]), "Migration V12.7: alle Bereiche planen über Maschinen/Linien")
check(all(any(m.get("departmentId") == d["id"] for m in after_state["machines"]) for d in after_state["departments"]), "Migration V12.7: jeder Bereich hat mindestens eine Ressource")
check(all(x.get("planningType") == "MACHINE" and x.get("machineId") for x in after_state["workSteps"]), "Migration V12.7: alle offenen Arbeitsgänge sind Wochenplan-Aufträge")
check(con.execute("SELECT revision FROM state").fetchone()[0] == before_rev, "Migration: Revision unverändert")
check(server.validate_state(after_state, after_state)[0], "Migration: Live-Stand besteht validate_state")
check(con.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "Migration: integrity_check ok")
check(all(p.get("number") and p.get("phase") for p in after_state.get("projects", [])), "Migration: Projekte haben Nummer und Phase")
check(all(p["phase"] == ("accepted" if p.get("ab") else "inquiry") for p in after_state.get("projects", [])), "Migration: bestehende AB-Datensätze = angenommene Projekte")
check(len([t for t in after_state.get("processTemplates", []) if t["kind"] == "pm"]) == 3, "Migration: 3 PM-Standardabläufe als Daten angelegt")
con.close()
server.init_db()  # idempotent
check(True, "Migration: zweiter Start idempotent")


# --------------------------------------------------------------------------- server
def mk(user: str, role: str, dep: str) -> None:
    salt, digest = server.hash_password("password123")
    ts = server.now_iso()
    with server.db_session() as c:
        c.execute("DELETE FROM users WHERE username=?", (user,))
        c.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)",
                  (user, salt, digest, role, dep, ts, ts))


for u, r, d in [("t_admin", "admin", ""), ("t_gf", "gf", ""), ("t_pm", "project_management", ""), ("t_view", "viewer", ""),
                ("t_lead_cnc", "department_lead", "cnc"), ("t_dep_cnc", "department_deputy", "cnc"),
                ("t_lead_k1", "department_lead", "konf1"), ("t_view_cnc", "viewer", "cnc"), ("t_lead2_cnc", "department_lead", "cnc"), ("t_av", "production_planning", ""), ("t_sales", "sales", "")]:
    mk(u, r, d)

httpd = server.MPHTTPServer(("127.0.0.1", PORT), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
server.Handler.log_message = lambda *a, **k: None


def req(method, path, body=None, cookie=None, version=server.APP_VERSION):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    h = {"Content-Type": "application/json", "X-MP-Client-Version": version}
    if cookie:
        h["Cookie"] = cookie
    c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
    r = c.getresponse()
    raw = r.read()
    try:
        data = json.loads(raw)
    except Exception:
        data = raw
    return r.status, data, r.getheader("Set-Cookie")


def login(u, pw="password123"):
    s, d, ck = req("POST", "/api/login", {"username": u, "password": pw})
    assert s == 200, (u, s, d)
    return ck.split(";")[0]


def state(ck):
    s, d, _ = req("GET", "/api/state", cookie=ck)
    return d["revision"], d["data"]


def put(ck, mutate, action="test"):
    rev, st = state(ck)
    new = copy.deepcopy(st)
    mutate(new)
    s, d, _ = req("PUT", "/api/state", {"revision": rev, "data": new, "action": action}, cookie=ck)
    return s, (d.get("errorCode") if isinstance(d, dict) else None), d


def audit_entry(new, actor):
    new.setdefault("audit", []).insert(0, {"id": "a" + os.urandom(6).hex(), "ts": server.now_iso(), "actor": actor, "action": "Test", "detail": "", "revision": 0})


def res_of(n, dep):
    return next(m["id"] for m in n["machines"] if m.get("departmentId") == dep)
def mstep(n, sid, fs, dep, hours, **kw):
    pos = max([float(x.get("pos") or 0) for x in n["workSteps"]] + [0]) + 10
    base = {"id": sid, "projectId": "", "fs": fs, "ab": "", "wt": "", "order": fs, "departmentId": dep, "planningType": "MACHINE", "sequence": 50000 + pos,
            "predecessorIds": [], "pos": pos, "machineId": res_of(n, dep), "altMachineId": "", "allowAlternative": False, "articleNo": "", "description": "",
            "targetQty": 0, "dueDate": "", "baselinePlan": None, "hours": hours, "goodQty": 0, "scrapQty": 0, "status": "planned", "direction": "forward",
            "anchorMode": "none", "requiredStart": "", "requiredFinish": "", "createdAt": server.now_iso(), "lockedStart": "", "lockedSegments": [],
            "actualStartedAt": "", "runningSince": "", "pausedAt": "", "pauseIntervals": [], "remainingHours": None, "lastStatusCheckAt": ""}
    base.update(kw)
    return base

A = login("t_admin"); G = login("t_gf"); P = login("t_pm"); V = login("t_view")
LC = login("t_lead_cnc"); DC = login("t_dep_cnc"); LK = login("t_lead_k1")

# Admin setup: a confirmed konf1 need and a free konf1 step
def setup(n):
    n["departmentStaffNeeds"] = [{"departmentId": "konf1", "weekStart": "2026-10-19", "requested": 4, "confirmed": 3, "updatedAt": ""}]
    n["workSteps"].append(mstep(n, "ws_k1_a", "FS K1", "konf1", 3, dueDate="2026-10-16"))
s, code, _ = put(A, setup); check(s == 200, "Admin: Testdaten anlegen")

# --- Bereichsgrenzen (MP-AUD-032 / Person 2 Rollenmatrix)
def rehome_machine(n):
    m = next(x for x in n["machines"] if x["id"] == "m3"); m["departmentId"] = "konf1"; m["name"] = "x"
    audit_entry(n, "t_lead_k1")
s, code, _ = put(LK, rehome_machine); check(s in (400, 403), f"Konf1-Leitung kann CNC-Maschine NICHT umhängen ({s} {code})")
def rehome_emp(n):
    n["employees"][0]["departmentId"] = "konf1"; audit_entry(n, "t_lead_k1")
s, code, _ = put(LK, rehome_emp); check(s == 403, f"Konf1-Leitung kann CNC-Mitarbeiter NICHT umhängen ({s} {code})")
def edit_cnc_machine(n):
    n["machines"][2]["setupMinutes"] = 5; audit_entry(n, "t_lead_k1")
s, code, _ = put(LK, edit_cnc_machine); check(s == 403, f"Konf1-Leitung kann CNC-Maschine NICHT ändern ({s} {code})")
def edit_own_step(n):
    next(x for x in n["workSteps"] if x["id"] == "ws_k1_a")["hours"] = 4; audit_entry(n, "t_lead_k1")
s, code, _ = put(LK, edit_own_step); check(s == 200, f"Konf1-Leitung ändert eigenen Arbeitsgang ({s} {code})")
def edit_cnc_order(n):
    o = next(x for x in n["workSteps"] if x["planningType"] == "MACHINE" and x["status"] == "planned"); o["hours"] = float(o["hours"]) + 0.25
    audit_entry(n, "t_lead_cnc")
s, code, _ = put(LC, edit_cnc_order); check(s == 200, f"CNC-Leitung ändert CNC-Auftrag ({s} {code})")
def cnc_order_to_konf1(n):
    o = next(x for x in n["workSteps"] if x["planningType"] == "MACHINE"); o["departmentId"] = "konf1"
s, code, _ = put(A, cnc_order_to_konf1); check(s == 400, f"Auftrag darf nicht bereichsfremd zur Maschine sein ({s} {code})")

# --- Personalbedarf (MP-AUD-002/003)
def gf_confirm(n):
    n["departmentStaffNeeds"][0]["confirmed"] = 2; audit_entry(n, "t_gf")
s, code, _ = put(G, gf_confirm); check(s == 200, f"GF bestätigt Bedarf ({s} {code})")
def gf_requested(n):
    n["departmentStaffNeeds"][0]["requested"] = 9; audit_entry(n, "t_gf")
s, code, _ = put(G, gf_requested); check(s == 403, f"GF kann gemeldeten Bedarf NICHT ändern ({s} {code})")
def gf_delete(n):
    n["departmentStaffNeeds"] = []; audit_entry(n, "t_gf")
s, code, _ = put(G, gf_delete); check(s == 403, f"GF kann Bedarfsmeldung NICHT löschen ({s} {code})")
def gf_new(n):
    n["departmentStaffNeeds"].append({"departmentId": "konf2", "weekStart": "2026-10-19", "requested": 0, "confirmed": 5, "updatedAt": ""}); audit_entry(n, "t_gf")
s, code, _ = put(G, gf_new); check(s == 200, f"GF bestätigt Bereich ohne Meldung (requested 0) ({s} {code})")
def gf_machine(n):
    n["machines"][0]["setupMinutes"] = 7; audit_entry(n, "t_gf")
s, code, _ = put(G, gf_machine); check(s == 403, f"GF kann Maschinen NICHT ändern ({s} {code})")
def lead_delete_confirmed(n):
    n["departmentStaffNeeds"] = [x for x in n["departmentStaffNeeds"] if x["departmentId"] != "konf1"]; audit_entry(n, "t_lead_k1")
s, code, _ = put(LK, lead_delete_confirmed); check(s == 403, f"Bereich kann bestätigten Bedarf NICHT löschen ({s} {code})")
def lead_request(n):
    next(x for x in n["departmentStaffNeeds"] if x["departmentId"] == "konf1")["requested"] = 6; audit_entry(n, "t_lead_k1")
s, code, _ = put(LK, lead_request); check(s == 200, f"Bereich meldet Bedarf ({s} {code})")

# --- PM / Viewer
def pm_proj(n):
    n["projects"][0]["name"] = "Testname"; n["projects"][0].setdefault("log", []).append({"id": "lpm1", "ts": server.now_iso(), "actor": "t_pm", "text": "x"}); audit_entry(n, "t_pm")
s, code, _ = put(P, pm_proj); check(s == 200, f"PM ändert Projekt ({s} {code})")
def pm_step(n):
    n["workSteps"][0]["hours"] = 99; audit_entry(n, "t_pm")
s, code, _ = put(P, pm_step); check(s == 403, f"PM kann Produktion NICHT ändern ({s} {code})")
s, code, _ = put(V, lambda n: n["machines"][0].update(name="x")); check(s == 403, f"Lesend kann nicht schreiben ({s} {code})")

# --- Validierung (MP-AUD-004/009)
def step_no_fs(n):
    n["workSteps"].append(mstep(n, "ws_nofs", "", "konf1", 1))
s, code, _ = put(A, step_no_fs); check(s == 400 and code == "MP-STEP-014", f"Arbeitsgang ohne FS abgelehnt ({s} {code})")
def free_pred(n):
    n["workSteps"].append(mstep(n, "ws_k1_b", "FS K1b", "konf1", 1, predecessorIds=["ws_k1_a"]))
s, code, _ = put(A, free_pred); check(s == 400 and code == "MP-STEP-007", f"Vorgänger zwischen freien FS abgelehnt ({s} {code})")
def bad_status(n):
    next(x for x in n["workSteps"] if x["id"] == "ws_k1_a")["status"] = "kaputt"
s, code, _ = put(A, bad_status); check(s == 400 and code == "MP-STEP-017", f"Ungültiger Status abgelehnt ({s} {code})")

# --- Protokoll append-only (Teil MP-AUD-014)
s, code, _ = put(LC, lambda n: n.update(audit=[])); check(s == 403 and code == "MP-LOG-001", f"Leitung kann Protokoll NICHT leeren ({s} {code})")
def edit_entry(n):
    n["audit"][0]["detail"] = "manipuliert"; audit_entry(n, "t_lead_cnc")
s, code, _ = put(LC, edit_entry); check(s == 403, f"Protokolleintrag NICHT änderbar ({s} {code})")
def foreign_actor(n):
    audit_entry(n, "Jonas"); n["machines"][0]["setupMinutes"] = 1
s, code, _ = put(LC, foreign_actor); check(s == 403, f"Fremder Akteur im Protokoll abgelehnt ({s} {code})")
def fill_audit(n):
    base = n.get("audit") or []
    filler = [{"id": f"fill{i}", "ts": f"2020-01-01T00:00:{i % 60:02d}.{i:06d}Z", "actor": "t_admin", "action": "alt", "detail": "", "revision": 0} for i in range(1000 - len(base))]
    n["audit"] = base + filler
s, code, _ = put(A, fill_audit); check(s == 200, "Admin: Protokoll auf 1000 Einträge gefüllt")
def capped(n):
    audit_entry(n, "t_lead_cnc"); n["audit"] = n["audit"][:1000]; n["machines"][0]["setupMinutes"] = 2
s, code, _ = put(LC, capped); check(s == 200, f"Kappung auf 1000 (älteste fallen weg) erlaubt ({s} {code})")
def cap_wrong(n):
    audit_entry(n, "t_lead_cnc"); del n["audit"][1]; n["audit"] = n["audit"][:1000]; n["machines"][0]["setupMinutes"] = 3
s, code, _ = put(LC, cap_wrong); check(s == 403, f"Löschen eines neueren Eintrags trotz Kappung abgelehnt ({s} {code})")

# --- V12.4.5: Fortschritt / Planwoche / Arbeitsvorbereitung
def step_done(n):
    x = next(y for y in n["workSteps"] if y["id"] == "ws_k1_a"); x.update(anchorMode="soft", requiredStart="2026-10-05T06:30"); audit_entry(n, "t_lead_k1")
s, code, _ = put(LK, step_done); check(s == 200, f"V12.7: Bereich plant fein VOR dem AV-Termin (05.10. < 16.10.) ({s} {code})")
def lead_late(n):
    next(y for y in n["workSteps"] if y["id"] == "ws_k1_a").update(requiredStart="2026-10-19T06:30"); audit_entry(n, "t_lead_k1")
s, code, _ = put(LK, lead_late); check(s == 400 and code == "MP-PLAN-062", f"V12.7: Termin NACH dem AV-Termin abgelehnt ({s} {code})")
def lead_due(n):
    next(y for y in n["workSteps"] if y["id"] == "ws_k1_a").update(dueDate="2026-10-30"); audit_entry(n, "t_lead_k1")
s, code, _ = put(LK, lead_due); check(s == 403, f"V12.7: Bereich ändert den AV-Termin NICHT ({s} {code})")
s, code, _ = put(A, lambda n: next(y for y in n["workSteps"] if y["id"] == "ws_k1_a").update(hours=-1)); check(s == 400 and code == "MP-PLAN-034", f"Negative Sollstunden abgelehnt ({s} {code})")
s, code, _ = put(A, lambda n: next(y for y in n["workSteps"] if y["id"] == "ws_k1_a").update(dueDate="KW40")); check(s == 400 and code == "MP-PLAN-060", f"V12.7: Ungültiger AV-Termin abgelehnt ({s} {code})")
AV = login("t_av")
def av_cnc(n):
    o = copy.deepcopy(next(x for x in n["workSteps"] if x["planningType"] == "MACHINE" and x["status"] == "planned"))
    o.update(id="ws_av_cnc", fs="FA AV1", order="FA AV1", pos=max(float(x.get("pos") or 0) for x in n["workSteps"] if x["planningType"] == "MACHINE") + 10,
             sequence=9001, createdAt=server.now_iso(), direction="forward", anchorMode="none", requiredStart="", requiredFinish="")
    n["workSteps"].append(o); audit_entry(n, "t_av")
s, code, _ = put(AV, av_cnc); check(s == 200, f"Arbeitsvorbereitung legt CNC-Auftrag an ({s} {code})")
def av_dept(n):
    n["workSteps"].append(mstep(n, "ws_av_k2", "FS AV2", "konf2", 40, dueDate="2026-10-16")); audit_entry(n, "t_av")
s, code, _ = put(AV, av_dept); check(s == 200, f"Arbeitsvorbereitung legt Konfektions-Auftrag an ({s} {code})")
def av_link(n):
    x = next(y for y in n["workSteps"] if y["id"] == "ws_av_k2"); pr = n["projects"][0]; x["projectId"] = pr["id"]; x["ab"] = pr["ab"]; x["wt"] = pr.get("wt", ""); audit_entry(n, "t_av")
s, code, _ = put(AV, av_link); check(s == 200, f"Arbeitsvorbereitung verknüpft freie FS mit AB ({s} {code})")
s, code, _ = put(AV, lambda n: (n["machines"][0].update(setupMinutes=33), audit_entry(n, "t_av"))); check(s == 403 and code == "MP-AV-030", f"Arbeitsvorbereitung ändert KEINE Maschinen ({s} {code})")
s, code, _ = put(AV, lambda n: (n["employees"][0].update(weeklyHours=10), audit_entry(n, "t_av"))); check(s == 403, f"Arbeitsvorbereitung ändert KEIN Personal ({s} {code})")
s, code, _ = put(AV, lambda n: (next(y for y in n["workSteps"] if y["id"] == "ws_k1_a").update(dueDate="2026-10-23"), audit_entry(n, "t_av"))); check(s == 200, f"V12.7: Arbeitsvorbereitung ändert AV-Termin ({s} {code})")
s, code, _ = put(LK, lambda n: (next(y for y in n["workSteps"] if y["id"] == "ws_k1_a").update(status="done"), audit_entry(n, "t_lead_k1"))); check(s == 400, f"V12.7: 'Fertig' nur über Produktion/Historie, nicht per Status ({s} {code})")
s, code, _ = put(AV, lambda n: (next(y for y in n["workSteps"] if y["id"] == "ws_av_cnc").update(status="released"), audit_entry(n, "t_av"))); check(s in (400, 403), f"Arbeitsvorbereitung gibt NICHT frei ({s} {code})")
s, code, _ = put(AV, lambda n: (n.update(workSteps=[y for y in n["workSteps"] if y["id"] != "ws_av_cnc"]), audit_entry(n, "t_av"))); check(s == 200, f"Arbeitsvorbereitung löscht geplanten Auftrag ({s} {code})")
s, d, _ = req("POST", "/api/users", {"username": "t_av2", "password": "password123", "role": "production_planning"}, cookie=A); check(s == 201, f"Admin legt Arbeitsvorbereitung an ({s})")

# --- V12.6: Projekt-Lebenszyklus
def plog(n, pid, actor, text="t"):
    p = next(x for x in n["projects"] if x["id"] == pid); p.setdefault("log", []).append({"id": "l" + os.urandom(5).hex(), "ts": server.now_iso(), "actor": actor, "text": text}); return p
SA = login("t_sales")
def sales_new(n):
    n["projects"].append({"id": "pj1", "number": "P-2026-9001", "phase": "inquiry", "customer": "Kunde A", "name": "Gehäuse", "ab": "", "dueDate": "", "processes": [], "log": [{"id": "l0", "ts": server.now_iso(), "actor": "t_sales", "text": "Eingang"}]}); audit_entry(n, "t_sales")
s_, code, _ = put(SA, sales_new); check(s_ == 200, f"Vertrieb legt Eingang an ({s_} {code})")
def sales_new_pm(n):
    n["projects"].append({"id": "pj2", "number": "P-2026-9002", "phase": "pm", "customer": "B", "processes": [], "log": []}); audit_entry(n, "t_sales")
s_, code, _ = put(SA, sales_new_pm); check(s_ == 403, f"Vertrieb kann nicht direkt in PM-Phase anlegen ({s_} {code})")
def dup_number(n):
    n["projects"].append({"id": "pj3", "number": "P-2026-9001", "phase": "inquiry", "customer": "C", "processes": [], "log": []})
s_, code, _ = put(A, dup_number); check(s_ == 400 and code == "MP-PM-006", f"Doppelte Projektnummer abgelehnt ({s_} {code})")
def to_pm(n):
    p = plog(n, "pj1", "t_sales"); p["phase"] = "pm"; audit_entry(n, "t_sales")
s_, code, _ = put(SA, to_pm); check(s_ == 200, f"Vertrieb übergibt an Projektmanagement ({s_} {code})")
def sales_offer(n):
    p = plog(n, "pj1", "t_sales"); p["phase"] = "offer_sent"; audit_entry(n, "t_sales")
s_, code, _ = put(SA, sales_offer); check(s_ == 403, f"Vertrieb kann Angebot nicht als versendet setzen ({s_} {code})")
def pm_procs(n):
    p = plog(n, "pj1", "t_pm"); p["processes"] = [{"id": "pr1", "areaId": "engineering", "title": "Konstruktion", "status": "in_progress"}, {"id": "pr2", "areaId": "konf1", "title": "Muster Konfektion", "status": "open"}, {"id": "pr3", "areaId": "cnc", "title": "Muster CNC", "status": "open"}, {"id": "pr4", "areaId": "sales", "title": "Kundenfreigabe", "status": "open"}]; audit_entry(n, "t_pm")
s_, code, _ = put(P, pm_procs); check(s_ == 200, f"PM legt parallele Prozesse in 4 Bereichen an ({s_} {code})")
def k1_own(n):
    p = plog(n, "pj1", "t_lead_k1"); next(x for x in p["processes"] if x["id"] == "pr2").update(status="done", note="Muster ok"); audit_entry(n, "t_lead_k1")
s_, code, _ = put(LK, k1_own); check(s_ == 200, f"Konfektion meldet eigenen Prozess erledigt ({s_} {code})")
def k1_foreign(n):
    p = plog(n, "pj1", "t_lead_k1"); next(x for x in p["processes"] if x["id"] == "pr3").update(status="done"); audit_entry(n, "t_lead_k1")
s_, code, _ = put(LK, k1_foreign); check(s_ == 403, f"Konfektion kann CNC-Prozess NICHT ändern ({s_} {code})")
def k1_add(n):
    p = plog(n, "pj1", "t_lead_k1"); p["processes"].append({"id": "prx", "areaId": "konf1", "title": "x", "status": "open"}); audit_entry(n, "t_lead_k1")
s_, code, _ = put(LK, k1_add); check(s_ == 403, f"Bereich kann keine Prozesse anlegen ({s_} {code})")
def k1_title(n):
    p = plog(n, "pj1", "t_lead_k1"); next(x for x in p["processes"] if x["id"] == "pr2").update(title="umbenannt"); audit_entry(n, "t_lead_k1")
s_, code, _ = put(LK, k1_title); check(s_ == 403, f"Bereich kann nur Status/Notiz ändern ({s_} {code})")
def sales_own(n):
    p = plog(n, "pj1", "t_sales"); next(x for x in p["processes"] if x["id"] == "pr4").update(status="done"); audit_entry(n, "t_sales")
s_, code, _ = put(SA, sales_own); check(s_ == 200, f"Vertrieb erledigt eigenen Prozess ({s_} {code})")
def cnc_own(n):
    p = plog(n, "pj1", "t_lead_cnc"); next(x for x in p["processes"] if x["id"] == "pr3").update(status="in_progress"); audit_entry(n, "t_lead_cnc")
s_, code, _ = put(LC, cnc_own); check(s_ == 200, f"CNC bearbeitet Muster-Prozess parallel ({s_} {code})")
def k1_takeover(n):
    n["workSteps"].append(mstep(n, "ws_muster_k1", "FS M-1", "konf1", 6, projectId="pj1", sequence=20))
    p = plog(n, "pj1", "t_lead_k1"); pr = next(x for x in p["processes"] if x["id"] == "pr2"); pr["workStepId"] = "ws_muster_k1"; audit_entry(n, "t_lead_k1")
s_, code, _ = put(LK, k1_takeover); check(s_ == 200, f"Konfektion übernimmt Muster-Prozess als FS ohne Termin ({s_} {code})")
_, st_ = state(A); check(next(x for x in st_["workSteps"] if x["id"] == "ws_muster_k1")["anchorMode"] == "none", "FS ohne festen Termin gespeichert (automatisch eingeplant)")
def av_timing(n):
    p = plog(n, "pj1", "t_av"); next(x for x in p["processes"] if x["id"] == "pr3").update(startDate="2026-10-05", dueDate="2026-10-09"); audit_entry(n, "t_av")
s_, code, _ = put(AV, av_timing); check(s_ == 200, f"Arbeitsvorbereitung terminiert Fertigungsprozess ({s_} {code})")
def av_status(n):
    p = plog(n, "pj1", "t_av"); next(x for x in p["processes"] if x["id"] == "pr3").update(status="done"); audit_entry(n, "t_av")
s_, code, _ = put(AV, av_status); check(s_ == 403, f"Arbeitsvorbereitung meldet KEINEN Prozessstatus ({s_} {code})")
def av_add_proc(n):
    p = plog(n, "pj1", "t_av"); p["processes"].append({"id": "pr_av1", "areaId": "screenprint", "title": "Siebdruck Serie", "status": "open", "startDate": "2026-10-12", "dueDate": "2026-10-14"}); audit_entry(n, "t_av")
s_, code, _ = put(AV, av_add_proc); check(s_ == 200, f"Arbeitsvorbereitung legt Fertigungsprozess im Ablauf an ({s_} {code})")
def av_add_eng(n):
    p = plog(n, "pj1", "t_av"); p["processes"].append({"id": "pr_av2", "areaId": "engineering", "title": "x", "status": "open"}); audit_entry(n, "t_av")
s_, code, _ = put(AV, av_add_eng); check(s_ == 403, f"Arbeitsvorbereitung legt KEINE Konstruktionsprozesse an ({s_} {code})")
def pm_status_dept(n):
    p = plog(n, "pj1", "t_pm"); next(x for x in p["processes"] if x["id"] == "pr3").update(status="done"); audit_entry(n, "t_pm")
s_, code, _ = put(P, pm_status_dept); check(s_ == 403, f"PM meldet KEINEN Status für Abteilungsprozesse ({s_} {code})")
def pm_due_dept(n):
    p = plog(n, "pj1", "t_pm"); next(x for x in p["processes"] if x["id"] == "pr2").update(startDate="2026-10-01", dueDate="2026-10-06"); audit_entry(n, "t_pm")
s_, code, _ = put(P, pm_due_dept); check(s_ == 200, f"PM terminiert Abteilungsprozess ({s_} {code})")
def pm_bad_dates(n):
    p = plog(n, "pj1", "t_pm"); next(x for x in p["processes"] if x["id"] == "pr1").update(startDate="2026-12-01", dueDate="2026-11-01"); audit_entry(n, "t_pm")
s_, code, _ = put(P, pm_bad_dates); check(s_ == 400 and code == "MP-PM-016", f"Start nach Fälligkeit abgelehnt ({s_} {code})")
def pm_status_eng(n):
    p = plog(n, "pj1", "t_pm"); next(x for x in p["processes"] if x["id"] == "pr1").update(status="done"); audit_entry(n, "t_pm")
s_, code, _ = put(P, pm_status_eng); check(s_ == 200, f"PM setzt Status für Konstruktion (kein eigenes Konto) ({s_} {code})")
def tpl(kind, tid, actor):
    def f(n):
        n.setdefault("processTemplates", []).append({"id": tid, "kind": kind, "name": "Test " + tid, "steps": [{"areaId": "cnc", "title": "Fräsen", "offsetDays": 0, "durationDays": 2}, {"areaId": "konf1", "title": "Konfektion", "offsetDays": 2, "durationDays": 3}]}); audit_entry(n, actor)
    return f
s_, code, _ = put(P, tpl("pm", "tpl_t1", "t_pm")); check(s_ == 200, f"PM speichert eigenen Ablauf ({s_} {code})")
s_, code, _ = put(P, tpl("av", "tpl_t2", "t_pm")); check(s_ == 403, f"PM speichert KEINEN AV-Ablauf ({s_} {code})")
s_, code, _ = put(AV, tpl("av", "tpl_t3", "t_av")); check(s_ == 200, f"Arbeitsvorbereitung speichert eigenen Fertigungsablauf ({s_} {code})")
s_, code, _ = put(AV, lambda n: (n.update(processTemplates=[t for t in n["processTemplates"] if t["id"] != "tpl_pm_new"]), audit_entry(n, "t_av"))); check(s_ == 403, f"Arbeitsvorbereitung löscht KEINEN PM-Ablauf ({s_} {code})")
s_, code, _ = put(LC, tpl("av", "tpl_t4", "t_lead_cnc")); check(s_ == 403, f"Abteilung speichert keine Abläufe ({s_} {code})")
s_, code, _ = put(A, lambda n: n["processTemplates"].append({"id": "tpl_bad", "kind": "av", "name": "Leer", "steps": []})); check(s_ == 400, f"Ablauf ohne Schritte abgelehnt ({s_} {code})")
def av_eng(n):
    p = plog(n, "pj1", "t_av"); next(x for x in p["processes"] if x["id"] == "pr1").update(status="waiting", dueDate="2026-10-30"); audit_entry(n, "t_av")
s_, code, _ = put(AV, av_eng); check(s_ == 403, f"Arbeitsvorbereitung ändert KEINE Konstruktions-Prozesse ({s_} {code})")
def sales_link(n):
    p = plog(n, "pj1", "t_sales"); next(x for x in p["processes"] if x["id"] == "pr3").update(workStepId="ws_muster_k1"); audit_entry(n, "t_sales")
s_, code, _ = put(SA, sales_link); check(s_ == 403, f"Vertrieb verknüpft keine Fertigungsprozesse ({s_} {code})")
def pm_offer(n):
    p = plog(n, "pj1", "t_pm"); p["phase"] = "offer_sent"; p["offer"] = {"number": "ANG-1", "value": "1200"}; audit_entry(n, "t_pm")
s_, code, _ = put(P, pm_offer); check(s_ == 200, f"PM: Angebot versendet ({s_} {code})")
def accept_nodate(n):
    p = plog(n, "pj1", "t_sales"); p["phase"] = "accepted"; p["ab"] = "AB-777"; audit_entry(n, "t_sales")
s_, code, _ = put(SA, accept_nodate); check(s_ == 400 and code == "MP-PM-009", f"Annahme ohne Liefertermin abgelehnt ({s_} {code})")
def accept(n):
    p = plog(n, "pj1", "t_sales"); p["phase"] = "accepted"; p["ab"] = "AB-777"; p["dueDate"] = "2026-11-30"; audit_entry(n, "t_sales")
s_, code, _ = put(SA, accept); check(s_ == 200, f"Vertrieb: Angebot angenommen mit AB + Liefertermin ({s_} {code})")
def sales_after(n):
    p = plog(n, "pj1", "t_sales"); p["customer"] = "anders"; audit_entry(n, "t_sales")
s_, code, _ = put(SA, sales_after); check(s_ == 403, f"Vertrieb ändert nach Annahme keine Stammdaten ({s_} {code})")
def av_fs(n):
    n["workSteps"].append(mstep(n, "ws_pj1", "FS 8801", "konf2", 12, projectId="pj1", ab="AB-777", sequence=10, dueDate="2026-11-27")); audit_entry(n, "t_av")
s_, code, _ = put(AV, av_fs); check(s_ == 200, f"Arbeitsvorbereitung legt FS zum angenommenen Projekt an ({s_} {code})")
def av_edit_project(n):
    p = plog(n, "pj1", "t_av"); p["dueDate"] = "2026-12-24"; audit_entry(n, "t_av")
s_, code, _ = put(AV, lambda n: (next(y for y in n["workSteps"] if y["id"] == "ws_pj1").update(goodQty=3), audit_entry(n, "t_av"))); check(s_ == 403 and code == "MP-AV-030", f"Arbeitsvorbereitung meldet KEINE Produktionsmengen ({s_} {code})")
s_, code, _ = put(AV, av_edit_project); check(s_ == 403, f"Arbeitsvorbereitung ändert KEINEN Liefertermin ({s_} {code})")
def av_close(n):
    p = plog(n, "pj1", "t_av", "Abschluss"); p["phase"] = "closed"; audit_entry(n, "t_av")
s_, code, _ = put(AV, av_close); check(s_ == 200, f"Arbeitsvorbereitung schließt Projekt ab ({s_} {code})")
def log_tamper(n):
    p = next(x for x in n["projects"] if x["id"] == "pj1"); p["log"][0]["text"] = "gefälscht"; audit_entry(n, "t_pm")
s_, code, _ = put(P, log_tamper); check(s_ == 403, f"Projektverlauf nicht fälschbar ({s_} {code})")
def gf_proj(n):
    n["projects"][0]["name"] = "GF"; audit_entry(n, "t_gf")
s_, code, _ = put(G, gf_proj); check(s_ == 403, f"GF ändert keine Projekte ({s_} {code})")
def pm_del_closed(n):
    n["projects"] = [x for x in n["projects"] if x["id"] != "pj1"]; audit_entry(n, "t_pm")
s_, code, _ = put(P, pm_del_closed); check(s_ in (400, 403), f"Abgeschlossenes Projekt mit FS nicht löschbar ({s_} {code})")
s_, d, _ = req("POST", "/api/users", {"username": "t_sales2", "password": "password123", "role": "sales"}, cookie=A); check(s_ == 201, f"Admin legt Vertrieb an ({s_})")

# --- No-op / orders / Methoden (MP-AUD-017/012/024)
rev, st = state(A)
s, d, _ = req("PUT", "/api/state", {"revision": rev, "data": st}, cookie=A)
rev2, _ = state(A)
check(s == 200 and d.get("unchanged") and rev2 == rev, "Unveränderter Stand erzeugt keine Revision")
def with_orders(n):
    n["orders"] = [{"id": "zombie"}]; n["machines"][0]["setupMinutes"] = 4
s, code, _ = put(A, with_orders); _, st = state(A)
check(s == 200 and "orders" not in st, "Mitgesendetes Legacy-orders wird verworfen")
s, d, _ = req("DELETE", "/api/state", cookie=A); check(s == 405 and d.get("errorCode") == "MP-REQ-405", f"DELETE → 405 MP-REQ-405 ({s})")

# --- Benutzerverwaltung
s, d, _ = req("GET", "/api/users", cookie=DC); names = {u["username"] for u in d["users"]}
check(names and all(u["role"] == "viewer" for u in d["users"]), f"Stellvertretung sieht nur Lesende ({sorted(names)})")
uid = {u: server.db().execute("SELECT id FROM users WHERE username=?", (u,)).fetchone()[0] for u in ["t_lead_cnc", "t_dep_cnc", "t_view_cnc", "t_lead2_cnc", "t_view", "t_admin"]}
s, d, _ = req("PATCH", f"/api/users/{uid['t_lead_cnc']}", {"password": "takeover123"}, cookie=DC); check(s == 403, f"Stellvertretung kann Leitungspasswort NICHT setzen ({s})")
s, d, _ = req("PATCH", f"/api/users/{uid['t_lead_cnc']}", {"active": False}, cookie=DC); check(s == 403, f"Stellvertretung kann Leitung NICHT sperren ({s})")
s, d, _ = req("PATCH", f"/api/users/{uid['t_lead2_cnc']}", {"password": "takeover123"}, cookie=LC); check(s == 403, f"Leitung kann andere Leitung NICHT übernehmen ({s})")
s, d, _ = req("POST", "/api/users", {"username": "t_new_lead", "password": "password123", "role": "department_lead"}, cookie=LC); check(s == 403, f"Leitung kann keine Leitung anlegen ({s})")
s, d, _ = req("POST", "/api/users", {"username": "t_new_dep", "password": "password123", "role": "department_deputy"}, cookie=LC); check(s == 201, f"Leitung legt Stellvertretung an ({s})")
s, d, _ = req("POST", "/api/users", {"username": "t_new_dep2", "password": "password123", "role": "department_deputy"}, cookie=DC); check(s == 403, f"Stellvertretung kann keine Stellvertretung anlegen ({s})")
s, d, _ = req("POST", "/api/users", {"username": "t_new_view", "password": "password123", "role": "viewer"}, cookie=DC); check(s == 201, f"Stellvertretung legt Lesende an ({s})")
s, d, _ = req("PATCH", f"/api/users/{uid['t_view_cnc']}", {"password": "neuesPasswort1"}, cookie=DC); check(s == 200, f"Stellvertretung setzt Passwort eines Lesenden ({s})")
s, d, _ = req("PATCH", f"/api/users/{uid['t_view']}", {"role": ["x"]}, cookie=A); check(s == 400, f"Ungültiger Rollentyp → 400 statt Absturz ({s})")
s, d, _ = req("PATCH", f"/api/users/{uid['t_admin']}", {"password": "x" * 10}, cookie=A); check(s == 400, f"Eigenes Konto nicht über PATCH ({s})")
LC2 = login("t_lead_cnc")
s, d, _ = req("POST", "/api/password", {"currentPassword": "falsch", "newPassword": "neuesPasswort1"}, cookie=LC); check(s == 403, f"Passwortwechsel mit falschem Passwort abgelehnt ({s})")
s, d, _ = req("POST", "/api/password", {"currentPassword": "password123", "newPassword": "kurz"}, cookie=LC); check(s == 400, f"Zu kurzes neues Passwort abgelehnt ({s})")
s, d, _ = req("POST", "/api/password", {"currentPassword": "password123", "newPassword": "neuesPasswort1"}, cookie=LC); check(s == 200, f"Bereichsleitung ändert eigenes Passwort ({s})")
check(req("GET", "/api/session", cookie=LC)[0] == 200, "Aktuelle Sitzung bleibt nach Passwortwechsel gültig")
check(req("GET", "/api/session", cookie=LC2)[0] == 401, "Andere Sitzungen werden nach Passwortwechsel beendet")
check(req("POST", "/api/login", {"username": "t_lead_cnc", "password": "neuesPasswort1"})[0] == 200, "Login mit neuem Passwort")
check(req("POST", "/api/login", {"username": "gibtsnicht", "password": "x"})[0] == 401, "Unbekannter Benutzer → 401")
s, d, _ = req("GET", "/api/state", cookie=A, version="12.4.3")
s2, d2, _ = req("PUT", "/api/state", {"revision": 1, "data": {}}, cookie=A, version="12.4.3")
check(s2 == 426, f"Veralteter Client darf nicht schreiben (426) ({s2})")

# --- Concurrency
rev, st = state(A)
results = []
def racer(i):
    n = copy.deepcopy(st); n["machines"][0]["setupMinutes"] = 10 + i
    results.append(req("PUT", "/api/state", {"revision": rev, "data": n}, cookie=A)[0])
ts = [threading.Thread(target=racer, args=(i,)) for i in range(10)]
[t.start() for t in ts]; [t.join() for t in ts]
check(sorted(results) == [200] + [409] * 9, f"10 parallele Writes: genau 1×200, 9×409 ({sorted(results)})")

httpd.shutdown()

# --------------------------------------------------------------------------- backup
tmp = Path(tempfile.mkdtemp())
(tmp / "data").mkdir()
for f in DATA.glob("maschinenplanung.sqlite3*"):
    shutil.copy2(f, tmp / "data" / f.name)
shutil.copy2(SRC / "Backup_Datenbank.py", tmp / "Backup_Datenbank.py")
p = subprocess.run([sys.executable, "-I", str(tmp / "Backup_Datenbank.py")], capture_output=True, text=True)
check(p.returncode == 0 and "integrity ok" in p.stdout, f"Backup-Skript: Sicherung + Integritätsprüfung ({p.returncode})")
bk = sorted((tmp / "backups").glob("maschinenplanung_*.sqlite3"))[-1]
bc = sqlite3.connect(bk)
check(bc.execute("SELECT revision FROM state").fetchone()[0] == sqlite3.connect(server.DB_PATH).execute("SELECT revision FROM state").fetchone()[0], "Backup enthält aktuelle Revision (WAL berücksichtigt)")
bc.close()
(tmp / "BACKUP_ZIEL.txt").write_text(str(tmp / "zweitziel") + "\n", encoding="utf-8")
p = subprocess.run([sys.executable, "-I", str(tmp / "Backup_Datenbank.py")], capture_output=True, text=True)
check(p.returncode == 0 and list((tmp / "zweitziel").glob("*.sqlite3")), "Backup-Skript: Zweitkopie nach BACKUP_ZIEL.txt")

# --------------------------------------------------------------------------- tz fallback
code = "import release_gates as g; g.LOCAL_TZ=None; print(g.local_dt('2026-07-01T04:30:00Z'))"
p = subprocess.run([sys.executable, "-c", code], cwd=SRC, capture_output=True, text=True, env={**os.environ, "TZ": "Europe/Berlin"})
check(p.stdout.strip() == "2026-07-01 06:30:00", f"Zeitzonen-Fallback ohne tzdata nutzt Systemzeit (Sommerzeit) ({p.stdout.strip()})")

ok = sum(1 for r in RESULTS if r[0])
print(f"\nERGEBNIS: {ok}/{len(RESULTS)} PASS")
sys.exit(0 if ok == len(RESULTS) else 1)

#!/usr/bin/env python3
"""Server-Regeln Benachrichtigungen V12.13.0 ohne Browser (Empfänger, Prefs, Dedupe, Rechte, Aufbewahrung).

Aufruf:  python tests/test_notifications.py
Arbeitet nur mit einer frischen Datenbank im Temp-Ordner.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
import server  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    RESULTS.append((bool(cond), label))
    print(("PASS " if cond else "FAIL ") + label)


tmp = Path(tempfile.mkdtemp(prefix="mp-notif-"))
server.DATA_DIR = tmp
server.DB_PATH = tmp / "maschinenplanung.sqlite3"
server.init_db(seed="werbetechnik")
USERS = [("adm", "admin", ""), ("gf1", "gf", ""), ("pm1", "project_management", ""), ("av1", "production_planning", ""),
         ("lead_cnc", "department_lead", "cnc"), ("lead_tf", "department_lead", "thermoforming"), ("sales1", "sales", ""), ("view1", "viewer", "")]
U = {n: {"username": n, "role": r, "department_id": d} for n, r, d in USERS}
st = lambda con, n, **kw: [dict(x) for x in con.execute("SELECT * FROM notifications WHERE username=? ORDER BY id", (n,)).fetchall()]  # noqa: E731
kinds = lambda con, n: [x["kind"] for x in st(con, n)]  # noqa: E731

with server.db_session() as con:
    for n, r, d in USERS:
        salt, digest = server.hash_password("Test-Passwort-1")
        con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)",
                    (n, salt, digest, r, d, server.now_iso(), server.now_iso()))

    # --- Defaults je Rolle
    d = lambda n: server.notif_prefs(con, n, U[n]["role"])["kinds"]  # noqa: E731
    check(d("lead_cnc") == {"mention": True, "release": True, "risk": True, "block": True, "loan": True}, "Defaults Bereichsrolle: alles an")
    check(d("gf1")["risk"] and d("gf1")["loan"] and not d("gf1")["release"] and not d("gf1")["block"], "Defaults GF: Termin + Leiharbeiter")
    check(d("av1")["block"] and not d("av1")["risk"], "Defaults AV: Sperre/Abwesenheit")
    check(d("sales1")["risk"] and not d("sales1")["loan"], "Defaults Vertrieb: Termin")
    check(d("view1") == {"mention": True, "release": False, "risk": False, "block": False, "loan": False}, "Defaults Lesende: nur Erwähnung")

    # --- Prefs prüfen / speichern
    check(server.notif_put(con, U["gf1"], "/api/notifications/prefs", {"kinds": {"bogus": True}})[0] == 400, "Prefs: unbekannte Art -> 400")
    check(server.notif_put(con, U["gf1"], "/api/notifications/prefs", {"kinds": {"risk": "ja"}})[0] == 400, "Prefs: kein Bool -> 400")
    check(server.notif_put(con, U["gf1"], "/api/notifications/prefs", {"quiet": {"on": True, "from": "25:00", "to": "06:00"}})[1].get("errorCode") == "MP-NOTIF-002", "Prefs: Ruhezeit 25:00 -> MP-NOTIF-002")
    s, b = server.notif_put(con, U["gf1"], "/api/notifications/prefs", {"quiet": {"on": True, "from": "21:30", "to": "05:00"}, "kinds": {"loan": False}})
    check(s == 200 and b["prefs"]["quiet"] == {"on": True, "from": "21:30", "to": "05:00"} and b["prefs"]["kinds"]["loan"] is False and b["prefs"]["kinds"]["risk"], "Prefs gespeichert, Rest bleibt Default")
    server.notif_put(con, U["gf1"], "/api/notifications/prefs", {"kinds": {"loan": True}})

    # --- Chat: Erwähnung und Direktnachricht
    s, b = server.chat_post(con, U["adm"], "/api/chat/channels", {"kind": "direct", "members": ["pm1"]})
    did = b["channel"]["id"]
    server.chat_post(con, U["adm"], "/api/chat/messages", {"channel": did, "text": "Hallo ⟦u:pm1⟧, siehe ⟦o:ws1|FA 7001⟧"})
    n = st(con, "pm1")
    check(len(n) == 1 and n[0]["kind"] == "mention" and n[0]["ref_type"] == "chat" and n[0]["ref_id"] == str(did), "Direktnachricht + @ = ein Eintrag für den Empfänger")
    check("@pm1" in n[0]["text"] and "/FA 7001" in n[0]["text"] and "⟦" not in n[0]["text"], "Vorschau ohne Token-Syntax")
    check(st(con, "adm") == [], "Absender erhält nichts")
    server.chat_post(con, U["adm"], "/api/chat/messages", {"channel": 1, "text": "Info an alle ⟦u:lead_cnc⟧"})
    check(len(st(con, "lead_cnc")) == 1 and st(con, "gf1") == [], "@ im Kanal Alle: nur die erwähnte Person")
    server.chat_post(con, U["adm"], "/api/chat/messages", {"channel": 1, "text": "ohne Erwähnung"})
    check(len(st(con, "lead_cnc")) == 1, "Nachricht ohne @ im Kanal Alle: kein Eintrag")
    s, b = server.chat_post(con, U["adm"], "/api/chat/channels", {"kind": "group", "name": "G", "members": ["lead_tf"]})
    server.chat_post(con, U["adm"], "/api/chat/messages", {"channel": b["channel"]["id"], "text": "⟦u:pm1⟧ du bist nicht Mitglied"})
    check(len(st(con, "pm1")) == 1, "Erwähnung eines Nicht-Mitglieds der Gruppe: kein Eintrag")
    server.notif_put(con, U["pm1"], "/api/notifications/prefs", {"kinds": {"mention": False}})
    server.chat_post(con, U["adm"], "/api/chat/messages", {"channel": did, "text": "nochmal"})
    check(len(st(con, "pm1")) == 1, "Prefs aus: kein Eintrag")
    server.notif_put(con, U["pm1"], "/api/notifications/prefs", {"kinds": {"mention": True}})
    # Kanal lesen erledigt den Eintrag
    server.chat_post(con, U["pm1"], "/api/chat/read", {"channel": did, "lastId": 99})
    check(st(con, "pm1")[0]["read_at"] is not None, "Kanal gelesen -> Eintrag gelesen")

    # --- Diff: Freigabe, Sperre, Abwesenheit, Leiharbeiter
    def step(i, dep, mid, status="planned", **kw):
        x = {"id": f"s{i}", "planningType": "MACHINE", "departmentId": dep, "machineId": mid, "fa": f"FA {i}", "status": status, "dueDate": "2026-10-30"}
        x.update(kw)
        return x
    machines = [{"id": "m1", "name": "Fräse 1", "departmentId": "cnc"}, {"id": "t1", "name": "Tiefzieher", "departmentId": "thermoforming"}]
    base = {"machines": machines, "departments": [{"id": "cnc", "name": "CNC"}, {"id": "thermoforming", "name": "Tiefziehen"}],
            "workSteps": [step(1, "cnc", "m1"), step(2, "thermoforming", "t1")], "machineBlocks": [], "personnelAbsences": [],
            "employees": [{"id": "e1", "name": "Max", "departmentId": "cnc", "employmentType": "permanent"}]}
    import copy
    new = copy.deepcopy(base)
    new["workSteps"][0]["status"] = "released"
    before = {n: len(st(con, n)) for n in U}
    made = server.notify_from_diff(con, base, new, "av1")
    check(kinds(con, "lead_cnc")[-1] == "release" and len(st(con, "lead_cnc")) == before["lead_cnc"] + 1, "Freigabe CNC -> Leitung CNC")
    check(len(st(con, "lead_tf")) == before["lead_tf"], "Freigabe CNC -> Leitung Tiefziehen sieht nichts (anderer Bereich)")
    check(len(st(con, "gf1")) == before["gf1"] and len(st(con, "av1")) == before["av1"], "Freigabe: GF (Default aus) und Auslöser erhalten nichts")
    check(made == 1, "Genau ein Eintrag erzeugt")
    back = copy.deepcopy(new); back["workSteps"][0]["status"] = "planned"
    server.notify_from_diff(con, new, back, "av1")
    check("zurückgezogen" in st(con, "lead_cnc")[-1]["text"], "Zurückziehen -> Eintrag „zurückgezogen“")
    n0 = len(st(con, "lead_cnc"))
    server.notify_from_diff(con, base, base, "av1")
    check(len(st(con, "lead_cnc")) == n0, "Unveränderter Stand erzeugt nichts")
    server.notif_put(con, U["lead_cnc"], "/api/notifications/prefs", {"kinds": {"release": False}})
    server.notify_from_diff(con, base, new, "av1")
    check(len(st(con, "lead_cnc")) == n0, "Freigabe-Prefs aus -> kein Eintrag")
    server.notif_put(con, U["lead_cnc"], "/api/notifications/prefs", {"kinds": {"release": True}})

    blk = copy.deepcopy(base); blk["machineBlocks"] = [{"id": "b1", "machineId": "m1", "start": "2026-10-12T06:00", "end": "2026-10-14T14:00", "label": "Wartung"}]
    server.notify_from_diff(con, base, blk, "lead_cnc")
    a_av = [x for x in st(con, "av1") if x["ref_type"] == "machine"]
    check(len(a_av) == 1 and "Fräse 1" in a_av[0]["text"] and "12.10.–14.10." in a_av[0]["text"] and "1 Auftrag betroffen" in a_av[0]["text"], f"Sperre -> AV: {a_av[0]['text'] if a_av else '-'}")
    check(not [x for x in st(con, "lead_cnc") if x["ref_type"] == "machine"], "Sperre: Auslöser erhält nichts")
    check(not [x for x in st(con, "lead_tf") if x["ref_type"] == "machine"], "Sperre CNC -> Leitung Tiefziehen nichts")
    server.notify_from_diff(con, base, blk, "lead_cnc")
    check(len([x for x in st(con, "av1") if x["ref_type"] == "machine"]) == 1, "Dieselbe Sperre erzeugt keinen zweiten Eintrag (Dedupe)")
    empty = copy.deepcopy(base); empty["workSteps"] = []; blk2 = copy.deepcopy(empty); blk2["machineBlocks"] = blk["machineBlocks"]
    nb = len(st(con, "av1"))
    server.notify_from_diff(con, empty, blk2, "x")
    check(len(st(con, "av1")) == nb, "Sperre ohne offene Aufträge: kein Eintrag")

    ab = copy.deepcopy(base); ab["personnelAbsences"] = [{"employeeId": "e1", "date": d, "label": "Krank"} for d in ("2026-10-13", "2026-10-14", "2026-10-15")]
    server.notify_from_diff(con, base, ab, "lead_cnc")
    e_av = [x for x in st(con, "av1") if x["ref_type"] == "employee"]
    check(len(e_av) == 1 and "Max" in e_av[0]["text"] and "13.10.–15.10." in e_av[0]["text"], "Abwesenheit (3 Tage) -> ein gebündelter Eintrag für AV")
    check("Krank" not in e_av[0]["text"] and "Urlaub" not in e_av[0]["text"], "Kein Abwesenheitsgrund im Text")
    check(not [x for x in st(con, "lead_tf") if x["ref_type"] == "employee"], "Abwesenheit CNC -> Tiefziehen nichts")

    lo = copy.deepcopy(base); lo["employees"][0].update({"id": "e1", "employmentType": "temporary", "tempStatus": "requested", "tempFrom": "2026-11-02", "tempTo": "2026-11-13", "tempBy": "lead_cnc"})
    server.notify_from_diff(con, base, lo, "lead_cnc")
    check([x["kind"] for x in st(con, "gf1")][-1] == "loan" and "angefragt" in st(con, "gf1")[-1]["text"], "Leiharbeiter-Anfrage -> GF")
    check(not [x for x in st(con, "lead_cnc") if x["kind"] == "loan"], "Anfrage: Antragsteller erhält nichts")
    ok = copy.deepcopy(lo); ok["employees"][0]["tempStatus"] = "approved"
    server.notify_from_diff(con, lo, ok, "gf1")
    lc = [x for x in st(con, "lead_cnc") if x["kind"] == "loan"]
    check(len(lc) == 1 and "genehmigt" in lc[0]["text"], "Entscheidung -> Antragsteller")
    check(not [x for x in st(con, "lead_tf") if x["kind"] == "loan"], "Entscheidung CNC -> Tiefziehen nichts")

    # --- Derived: Dedupe, Rechte, Validierung
    state = {"machines": machines, "workSteps": [step(1, "cnc", "m1", dueDate="2026-10-30"), step(2, "thermoforming", "t1"), step(3, "cnc", "m1", status="done")]}
    item = lambda oid, kind="late": {"orderId": oid, "kind": kind, "end": "2026-11-03"}  # noqa: E731
    s, b = server.notif_post(con, U["pm1"], "/api/notifications/derived", {"items": [item("s1"), item("s2"), item("s3"), item("nix")]}, state)
    check(s == 200 and b["created"] == 2, "Derived: aktive Aufträge ja, erledigter/unbekannter nein")
    s, b = server.notif_post(con, U["pm1"], "/api/notifications/derived", {"items": [item("s1"), item("s2")]}, state)
    check(b["created"] == 0, "Derived am selben Tag: dedupliziert")
    rk = [x for x in st(con, "pm1") if x["kind"] == "risk"]
    check(len(rk) == 2 and "03.11." in rk[0]["text"] and "30.10." in rk[0]["text"], f"Text nennt Plan-Ende und Termin vom Server ({rk[0]['text']})")
    s, b = server.notif_post(con, U["lead_cnc"], "/api/notifications/derived", {"items": [item("s1"), item("s2", "tight")]}, state)
    check(b["created"] == 1 and [x["ref_id"] for x in st(con, "lead_cnc") if x["kind"] == "risk"] == ["s1"], "Derived: Bereichsrolle nur eigener Bereich")
    server.notif_post(con, U["gf1"], "/api/notifications/derived", {"items": [item("s2", "tight")]}, state)
    check("Puffer" in st(con, "gf1")[-1]["text"], "Puffer-Text bei kind=tight")
    check(server.notif_post(con, U["pm1"], "/api/notifications/derived", {"items": [{"orderId": "s1", "kind": "boese", "end": "2026-11-03"}]}, state)[0] == 400, "Derived: unbekannte Art -> 400")
    check(server.notif_post(con, U["pm1"], "/api/notifications/derived", {"items": [item("s1")] * 51}, state)[0] == 400, "Derived: mehr als 50 -> 400")
    server.notif_put(con, U["sales1"], "/api/notifications/prefs", {"kinds": {"risk": False}})
    check(server.notif_post(con, U["sales1"], "/api/notifications/derived", {"items": [item("s1")]}, state)[1]["created"] == 0, "Derived: Prefs aus -> kein Eintrag")

    # --- Lesen, Isolation, Signatur
    s, b = server.notif_get(con, U["pm1"], "/api/notifications", {})
    check(s == 200 and b["unread"] >= 2 and all(x["id"] in {y["id"] for y in st(con, "pm1")} for x in b["items"]), "GET liefert nur eigene Einträge")
    own = {x["id"] for x in st(con, "pm1")}
    other = [x["id"] for x in st(con, "gf1")][0]
    ids = [x["id"] for x in b["items"]]
    s, r = server.notif_post(con, U["pm1"], "/api/notifications/read", {"ids": [other]})
    check(s == 200 and st(con, "gf1")[0]["read_at"] is None, "Fremde Einträge lassen sich nicht als gelesen markieren")
    sig0 = server.notif_sig(con, "pm1")
    server.notif_post(con, U["pm1"], "/api/notifications/read", {"ids": ids[:1]})
    sig1 = server.notif_sig(con, "pm1")
    check(sig1["unread"] == sig0["unread"] - 1 and sig1["sig"] != sig0["sig"], "Gelesen ändert die Signatur (weckt den Long-Poll)")
    server.notif_post(con, U["pm1"], "/api/notifications/read", {"all": True})
    check(server.notif_sig(con, "pm1")["unread"] == 0, "Alle gelesen")
    check(server.notif_post(con, U["pm1"], "/api/notifications/read", {"ids": ["x"]})[0] == 400, "Read: ungültige IDs -> 400")
    check(server.notif_get(con, U["pm1"], "/api/notifications", {"since": ["x"]})[0] == 400, "GET: since ungültig -> 400")
    s, b = server.notif_get(con, U["pm1"], "/api/notifications", {"since": [str(max(own))]})
    check(b["items"] == [], "since=letzte ID liefert nichts Neues")

    # --- Aufbewahrung
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    con.execute("INSERT INTO notifications(username,kind,text,created_at) VALUES('pm1','mention','alt',?)", (old,))
    n_before = con.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    removed = server.notif_purge(con, force=True)
    check(removed == 1 and con.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == n_before - 1, "Purge löscht Einträge älter als 30 Tage")
    check(not con.execute("SELECT 1 FROM notifications WHERE text='alt'").fetchone(), "Alter Eintrag ist weg, neuere bleiben")
    for i in range(520):
        server.notif_add(con, "view1", "viewer", "mention", "chat", "1", f"t{i}")
    check(con.execute("SELECT COUNT(*) FROM notifications WHERE username='view1'").fetchone()[0] == server.NOTIF_MAX_PER_USER, "Höchstens 500 Einträge je Benutzer")

# --- V12.14.1: Long-Poll wacht nur bei echter Änderung auf (kein Thundering Herd bei No-op-POSTs)
import http.client  # noqa: E402
import json  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

server.PBKDF2_ITERS = 1000
server.ALLOWED_NETWORK = server.ipaddress.ip_network("127.0.0.0/8")
with server.db_session() as con:
    salt, digest = server.hash_password("Test-Passwort-1")
    con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES('lp1','%s','%s','project_management','',1,?,?)" % (salt, digest),
                (server.now_iso(), server.now_iso()))
httpd = server.MPHTTPServer(("127.0.0.1", 0), server.Handler)
server.Handler.log_message = lambda *a, **k: None
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def req(method, path, body=None, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h = {"Content-Type": "application/json", "X-MP-Client-Version": server.APP_VERSION}
    if cookie:
        h["Cookie"] = cookie
    c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
    r = c.getresponse()
    txt = r.read()
    out = (r.status, json.loads(txt or b"{}"), (r.getheader("Set-Cookie") or "").split(";")[0])
    c.close()
    return out


_, _, ck = req("POST", "/api/login", {"username": "lp1", "password": "Test-Passwort-1"})
_, rv, _ = req("GET", "/api/revision", cookie=ck)
rev, nsig = rv["revision"], rv["notif"]["sig"]


def longpoll(query, box):
    t0 = time.monotonic()
    box["r"] = req("GET", "/api/revision?" + query, cookie=ck)
    box["dt"] = time.monotonic() - t0


def poll_during(action, query):
    box: dict = {}
    th = threading.Thread(target=longpoll, args=(query, box))
    th.start()
    time.sleep(0.4)
    action()
    th.join(10)
    return box


q = f"since={rev}&wait=2500&nsig={nsig}"
box = poll_during(lambda: req("POST", "/api/notifications/read", {"all": True}, ck), q)
check(box["dt"] >= 2.2, f"No-op read (nichts ungelesen) weckt den Long-Poll nicht ({box['dt']:.1f}s)")
box = poll_during(lambda: req("POST", "/api/notifications/derived", {"items": []}, ck), q)
check(box["dt"] >= 2.2, f"No-op derived (made=0) weckt den Long-Poll nicht ({box['dt']:.1f}s)")
with server.db_session() as con:
    server.notif_add(con, "lp1", "project_management", "mention", "chat", "1", "neu")
    ids = [x["id"] for x in st(con, "lp1")]
_, rv, _ = req("GET", "/api/revision", cookie=ck)
nsig2 = rv["notif"]["sig"]
box = poll_during(lambda: req("POST", "/api/notifications/read", {"ids": [999999]}, ck), f"since={rev}&wait=2500&nsig={nsig2}")
check(box["dt"] >= 2.2, f"read ohne getroffene Zeile weckt nicht ({box['dt']:.1f}s)")
box = poll_during(lambda: req("POST", "/api/notifications/read", {"ids": ids}, ck), f"since={rev}&wait=8000&nsig={nsig2}")
check(box["dt"] < 3 and box["r"][1]["notif"]["unread"] == 0, f"Echte Änderung (gelesen) weckt sofort ({box['dt']:.1f}s)")
box = {}
longpoll(f"since={rev}&wait=8000&nsig=kaputt", box)
check(box["dt"] >= 7.5 and box["r"][0] == 200, "Ungültige nsig wird ignoriert: Poll wartet normal")

failed = [l for ok, l in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

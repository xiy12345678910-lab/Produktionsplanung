#!/usr/bin/env python3
"""Server-Regeln Nachrichten V12.9.x ohne Browser (Aufbewahrung, Behalten, Erwähnungen, Rechte).

Aufruf:  python tests/test_v129.py
Arbeitet nur mit einem frischen Datenstand im Temp-Ordner.
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


tmp = Path(tempfile.mkdtemp(prefix="mp-v129-"))
server.DATA_DIR = tmp
server.DB_PATH = tmp / "maschinenplanung.sqlite3"
server.init_db(seed="werbetechnik")
ago = lambda d: (datetime.now(timezone.utc) - timedelta(days=d)).isoformat()  # noqa: E731
U = {u: {"username": u, "role": r} for u, r in (("Jörg", "viewer"), ("Groß", "viewer"), ("anna", "admin"), ("fremd", "viewer"))}

with server.db_session() as con:
    for name in U:
        salt, digest = server.hash_password("Test-Passwort-1")
        con.execute("INSERT INTO users(username,salt,password_hash,role,department_id,active,created_at,updated_at) VALUES(?,?,?,?,'',1,?,?)",
                    (name, salt, digest, U[name]["role"], server.now_iso(), server.now_iso()))
    st, b = server.chat_post(con, U["anna"], "/api/chat/channels", {"kind": "group", "name": "Team", "members": ["Jörg", "Groß"]})
    check(st == 201, "Gruppe angelegt")
    gid = b["channel"]["id"]
    ids = {}
    for key, days, keep in (("alt", 31, 0), ("alt_behalten", 45, 1), ("grenze", 29, 0)):
        ids[key] = con.execute("INSERT INTO chat_messages(channel_id,author,text,ts,keep,kept_by) VALUES(?,?,?,?,?,?)",
                               (gid, "anna", key, ago(days), keep, "anna" if keep else "")).lastrowid

    # Aufbewahrung
    check(server.chat_purge(con) == 0, "Purge läuft höchstens stündlich (init_db hat gerade gelöscht)")
    check(server.chat_purge(con, force=True) == 1, "Purge löscht genau die nicht behaltene Nachricht > 30 Tage")
    left = {r["text"] for r in con.execute("SELECT text FROM chat_messages WHERE channel_id=?", (gid,))}
    check(left == {"alt_behalten", "grenze"}, f"Behaltene und 29 Tage alte Nachricht bleiben ({sorted(left)})")

    # Behalten
    st, b = server.chat_post(con, U["Jörg"], "/api/chat/keep", {"channel": gid, "message": ids["grenze"], "keep": True})
    check(st == 200 and b["message"]["keep"] and b["message"]["keptBy"] == "Jörg", "Mitglied setzt Behalten")
    st, b = server.chat_post(con, U["fremd"], "/api/chat/keep", {"channel": gid, "message": ids["grenze"], "keep": False})
    check(st == 404 and b["errorCode"] == "MP-CHAT-002", "Nicht-Mitglied darf Behalten nicht ändern")
    st, b = server.chat_post(con, U["Jörg"], "/api/chat/keep", {"channel": 1, "message": ids["grenze"], "keep": False})
    check(st == 404 and b["errorCode"] == "MP-CHAT-007", "Nachricht aus anderem Kanal → MP-CHAT-007")
    st, b = server.chat_post(con, U["anna"], "/api/chat/keep", {"channel": gid, "message": ids["grenze"], "keep": False})
    check(st == 200 and not b["message"]["keep"] and b["message"]["keptBy"] == "", "Behalten wieder lösen")
    st, b = server.chat_get(con, U["anna"], "/api/chat/messages", {"channel": [str(gid)], "kept": ["1"], "after": [str(2 ** 31)]})
    check(st == 200 and [m["text"] for m in b["kept"]] == ["alt_behalten"] and b["messages"] == [] and b["retentionDays"] == 30, "Liste der behaltenen Nachrichten")

    # Erwähnungen mit Umlaut/ß
    server.chat_post(con, U["anna"], "/api/chat/messages", {"channel": gid, "text": "Hallo ⟦u:Jörg⟧ und ⟦u:Groß⟧"})
    chans = {u: next(c for c in server.chat_get(con, U[u], "/api/chat/channels", {})[1]["channels"] if c["id"] == gid) for u in ("Jörg", "Groß")}
    check(chans["Jörg"]["mentions"] == 1, "Erwähnung bei Benutzername mit Umlaut gezählt")
    check(chans["Groß"]["mentions"] == 1, "Erwähnung bei Benutzername mit ß gezählt")
    check(chans["Jörg"]["unread"] == 3, f"Ungelesen zählt fremde Nachrichten ({chans['Jörg']['unread']})")

failed = [x for x in RESULTS if not x[0]]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

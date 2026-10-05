#!/usr/bin/env python3
"""Version nur an einer Stelle führen: alle Angaben müssen APP_VERSION aus server.py entsprechen.

Aufruf:  python tests/test_version.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    RESULTS.append((bool(cond), label))
    print(("PASS " if cond else "FAIL ") + label)


def read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8-sig")


v = re.search(r'^APP_VERSION = "([^"]+)"', read("server.py"), re.M).group(1)
html = read("index.html")
check(re.search(r"const CLIENT_VERSION='([^']+)'", html).group(1) == v, f"index.html CLIENT_VERSION = {v}")
key = lambda x: tuple(map(int, x.split(".")))  # noqa: E731
check(re.search(r"<title>[^<]*V([\d.]+)</title>", html).group(1) == v, f"index.html <title> nennt V{v}")
check(re.search(r"<small>V([\d.]+) ·", html).group(1) == v, f"index.html Kopfzeile nennt V{v}")
newer = sorted(x for x in set(re.findall(r"V(\d+\.\d+\.\d+)", html)) if key(x) > key(v))
check(not newer, f"index.html nennt keine neuere Version als V{v} {newer}")
for name, pattern in (
    ("README.md", r"V(\d+\.\d+\.\d+)"),
    ("README_Windows.txt", r"^MASCHINENPLANUNG V(\S+)"),
    ("FEHLERCODES.txt", r"^MASCHINENPLANUNG V(\S+)"),
    ("BENUTZER_KURZANLEITUNG.txt", r"^MASCHINENPLANUNG V(\S+)"),
    ("RELEASE_NOTES.txt", r"^MASCHINENPLANUNG V(\S+)"),
):
    m = re.search(pattern, read(name), re.M)
    check(m and m.group(1) == v, f"{name}: V{m.group(1) if m else '?'} = V{v}")

failed = [x for x in RESULTS if not x[0]]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

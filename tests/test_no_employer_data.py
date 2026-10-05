#!/usr/bin/env python3
"""V12.15.0: Das ausgelieferte Paket ($MP_AppFiles aus MP_Common.ps1) enthält keine Daten des Arbeitgebers.

Die Suchbegriffe stehen nur als (Länge, SHA-256) im Test, damit sie nicht selbst im Repo auftauchen.
Gesucht wird mit einem Fenster der jeweiligen Länge über jede Datei (Textdateien, UTF-8).
Ausnahme: der Repo-Default des Updaters (Update_von_GitHub.ps1) – er muss für den Bestandskunden weiter funktionieren.

Aufruf:  python tests/test_no_employer_data.py
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
RESULTS: list[bool] = []


def check(cond, label):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + label)


# (Länge, sha256, ohne Groß-/Kleinschreibung, Bezeichnung)
FORBIDDEN = [
    (12, "7a5bd9bc9b32f16f940aeb403318862964ccf5c12978b2f64cf515f848cfb946", False, "Firmenname (Großschreibung)"),
    (14, "4babae7300a14272eb6139cb2b260e2172c2ae813383c4efaa451ad28b428601", True, "Firmenzusatz"),
    (7, "65d5bcaec77669dbfab37ae5cf35b489d5dc0d2016c77e9c2816178a234aba6f", True, "Rechnername"),
    (7, "e8c3285c5fad88d13026ca9d6f57b0b61a9ca42d3634a5659c7e2e4cfe11c071", True, "Personenname 1"),
    (6, "ffa7e158c8727f00251c8ac562714b6885c0c73962f7997b2c1faaec86d1ac43", True, "Personenname 2"),
    (7, "b96a3c3852c2ddc69f017c9f701611bae4370e4b05b27db9daa7354cc8a6bc9c", True, "Benutzername"),
    (14, "4b98ce320d4ee6bcab1e1793becb3f51a3514fab7bd7808df463a78a24cfefe1", True, "GitHub-Konto"),
    (96, "0ffa479c1e5b8fcbe9959f821ae79ebc2f47d4e07b20f84e3454e6abec5d9ea7", False, "Logo (Base64-Ausschnitt)"),
]
ALLOWED = {"GitHub-Konto": {"Update_von_GitHub.ps1"}}   # Updater-Default für den Bestandskunden


def find(text: str):
    """Liefert {Bezeichnung: Anzahl} für alle gefundenen Begriffe."""
    low = text.lower()
    found: dict[str, int] = {}
    for n, digest, fold, label in FORBIDDEN:
        src = low if fold else text
        for i in range(len(src) - n + 1):
            if hashlib.sha256(src[i:i + n].encode("utf-8")).hexdigest() == digest:
                found[label] = found.get(label, 0) + 1
    return found


def ps_list(text: str, name: str) -> list[str]:
    m = re.search(r"\$" + name + r"\s*=\s*@\((.*?)\n\)", text, re.S)
    return re.findall(r"'([^']+)'", m.group(1)) if m else []


common = (SRC / "MP_Common.ps1").read_text(encoding="utf-8-sig")
APP = ps_list(common, "MP_AppFiles")
check(len(APP) > 20 and "index.html" in APP and "server.py" in APP, f"{len(APP)} Paketdateien aus MP_Common.ps1 gelesen")
check(all((SRC / f).is_file() for f in APP), "Alle Paketdateien existieren")
check(not any(f.replace("\\", "/").startswith(("tools/", "tests/", "docs/", "config")) for f in APP),
      "tools/ (Legacy-Seed), tests/, docs/ und config/ gehören nicht zum Paket")

# Selbsttest der Suche (sonst wäre ein grüner Lauf wertlos)
probe = "xx " + "Th" + "omsen" + " yy " + "wtpc" + "207$" + " zz"
check(set(find(probe)) == {"Personenname 1", "Rechnername"}, "Suche findet Begriffe (Selbsttest, auch ohne Groß-/Kleinschreibung)")
b64 = __import__("json").loads((SRC / "tools" / "legacy_employer_seed.json").read_text(encoding="utf-8"))["logoDataUrl"].split(",")[1]
check(find("junk " + b64 + " junk") == {"Logo (Base64-Ausschnitt)": 1}, "Suche findet das alte Standardlogo in Base64 (Selbsttest)")

total = 0
for name in APP:
    text = (SRC / name).read_text(encoding="utf-8-sig", errors="replace")
    hits = find(text)
    bad = {k: v for k, v in hits.items() if name not in ALLOWED.get(k, ())}
    total += len(bad)
    check(not bad, f"{name}: keine Arbeitgeberdaten" + (f" (gefunden: {bad})" if bad else ""))
for label, files in ALLOWED.items():
    for f in files:
        check(find((SRC / f).read_text(encoding="utf-8-sig")).get(label, 0) == 1, f"{f}: {label} genau einmal (Updater-Default)")

html = (SRC / "index.html").read_text(encoding="utf-8")
check(re.search(r"const CI_DEFAULT=\{company:'',address:'',footer:'',color:'#1f5eff',font:'Arial',logo:''\}", html) is not None, "CI_DEFAULT ist neutral (kein Name, kein Logo)")
srv = (SRC / "server.py").read_text(encoding="utf-8")
check("LEGACY_SEED =" not in srv and "LEGACY_SEED[" not in srv, "server.py enthält keine eingebauten Firmenwerte")

print(f"\n{sum(RESULTS)}/{len(RESULTS)} bestanden")
sys.exit(0 if all(RESULTS) else 1)

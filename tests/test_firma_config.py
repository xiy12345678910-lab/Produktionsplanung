#!/usr/bin/env python3
"""V12.14.0: config/firma.json (Migration, Validierung, Sicherungen, Start-Prüfung, Backup-ZIP).

Aufruf:  python tests/test_firma_config.py
Arbeitet nur in Temp-Ordnern (MP_CONFIG_DIR), nie mit Live-Daten.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
os.environ["MP_CONFIG_DIR"] = tempfile.mkdtemp(prefix="mp-cfg-env-")
import server  # noqa: E402

RESULTS: list[bool] = []


def check(cond, label):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + label)


def fresh():
    """Neuer Temp-Ordner mit config\\ und data\; server-Globals darauf umgebogen."""
    d = Path(tempfile.mkdtemp(prefix="mp-cfg-"))
    server.CONFIG_DIR = d / "config"
    server.CONFIG_PATH = server.CONFIG_DIR / "firma.json"
    server.DATA_DIR = d / "data"
    server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
    server.DATA_DIR.mkdir()
    return d


def make_db(revision, ci=None):
    con = sqlite3.connect(server.DB_PATH)
    con.execute("CREATE TABLE state(id INTEGER PRIMARY KEY, revision INTEGER, json TEXT)")
    con.execute("INSERT INTO state VALUES(1,?,?)", (revision, json.dumps({"ci": ci} if ci is not None else {})))
    con.commit()
    con.close()


def code_of(fn):
    try:
        fn()
    except server.ConfigError as e:
        return e.code, e.message
    return None, ""


# 1) Neue Installation ohne Bestand: neutrale Datei
d = fresh()
cfg, warn = server.load_config()
check(server.CONFIG_PATH.exists() and not warn, "Neue Installation legt neutrale firma.json an")
check(cfg["company"]["name"] == "" and cfg["tenantId"] == "firma" and cfg["terms"]["projectNumber"] == "Projekt", "Neutral: kein Firmenname, keine Firmenbegriffe")
check(server.validate_config(cfg) == [], "Neutrale Datei besteht die Validierung")

# 2) Bestand (Revision > 1): Migration aus bisherigen Werten + data.ci
d = fresh()
png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 50).decode()
make_db(7, {"company": "Testfirma GmbH", "address": "Weg 1", "footer": "Fuss", "color": "#112233", "font": "Verdana",
            "logo": "data:image/png;base64," + png})
cfg, _ = server.load_config()
check(cfg["company"]["name"] == "Testfirma GmbH" and cfg["company"]["color"] == "#112233" and cfg["company"]["font"] == "Verdana", "Bestand: Name/Farbe/Schrift aus data.ci")
check(cfg["company"]["address"] == "Weg 1" and cfg["company"]["footer"] == "Fuss", "Bestand: Adresse/Fuß aus data.ci")
check(cfg["terms"]["projectNumber"] == "WT" and cfg["tenantId"] == "werbetechnik" and cfg["template"] == "werbetechnik", "Bestand: WT/tenantId/Vorlage aus bisherigen Werten")
check(len(cfg["projectAreas"]) == 7 and all(cfg["modules"].values()) and cfg["setupDone"] is True, "Bestand: 7 Projekt-Bereiche, alle Module, setupDone")
check(cfg["company"]["logoFile"] == "logo.png" and (server.CONFIG_DIR / "logo.png").read_bytes().startswith(b"\x89PNG"), "Bestand: Logo dekodiert nach config/logo.png")
first = server.CONFIG_PATH.read_bytes()
cfg2, _ = server.load_config()
check(server.CONFIG_PATH.read_bytes() == first and not list(server.CONFIG_DIR.glob("*.bak-*")), "Zweiter Start: Datei byte-gleich, keine .bak")

# 3) Bestand ohne data.ci: Einbauwerte des Bestandskunden
d = fresh()
make_db(3)
cfg, _ = server.load_config()
check(cfg["company"]["name"] == server.LEGACY_SEED["company"]["name"] and cfg["company"]["color"] == "#E2382A", "Bestand ohne data.ci: bisherige Einbauwerte")
check(cfg["company"]["logoFile"] in ("logo.jpg", "logo.png") and (server.CONFIG_DIR / cfg["company"]["logoFile"]).stat().st_size <= server.CONFIG_LOGO_MAX, "Bestand ohne data.ci: bisheriges Logo übernommen (<= 420 KB)")

# 4) Revision 1 (nie gespeichert) gilt nicht als Bestand
d = fresh()
make_db(1)
cfg, _ = server.load_config()
check(cfg["company"]["name"] == "" and cfg["tenantId"] == "firma", "Revision 1: neutral, kein Bestand")

# 5) Fehlende Felder ergänzen, unbekannte und vorhandene Werte bleiben, .bak entsteht
d = fresh()
server.CONFIG_DIR.mkdir()
server.CONFIG_PATH.write_text(json.dumps({"schemaVersion": 1, "tenantId": "abc", "company": {"name": "X", "extra": 5},
                                          "_kommentar": "bleibt", "meinFeld": {"a": 1}}), encoding="utf-8")
cfg, _ = server.load_config()
check(cfg["company"]["name"] == "X" and cfg["tenantId"] == "abc", "Vorhandene Werte unverändert")
check(cfg["_kommentar"] == "bleibt" and cfg["meinFeld"] == {"a": 1} and cfg["company"]["extra"] == 5, "Unbekannte Felder bleiben")
check("modules" in cfg and "locale" in cfg and cfg["company"]["font"] == "Arial", "Fehlende Felder mit Defaults ergänzt")
check(len(list(server.CONFIG_DIR.glob("firma.json.bak-*"))) == 1, "Vor der Änderung entstand genau eine .bak")
again, _ = server.load_config()
check(again == cfg and len(list(server.CONFIG_DIR.glob("firma.json.bak-*"))) == 1, "Erneutes Laden: idempotent, keine weitere .bak")

# 6) .bak-Rotation: nur die letzten 20
d = fresh()
server.load_config()
for i in range(30):
    c = json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))
    c["company"]["name"] = f"Name {i}"
    server.save_config(c)
baks = sorted(server.CONFIG_DIR.glob("firma.json.bak-*"))
check(len(baks) == server.CONFIG_KEEP_BAK, f"Rotation behält {server.CONFIG_KEEP_BAK} Sicherungen (gefunden {len(baks)})")
check(not list(server.CONFIG_DIR.glob("*.tmp")), "Atomares Schreiben hinterlässt keine .tmp")

# 7) schemaVersion zu neu: nur lesen, nichts zurückschreiben, Warnung MP-CFG-003
d = fresh()
server.CONFIG_DIR.mkdir()
raw = json.dumps({"schemaVersion": 99, "tenantId": "abc", "zukunft": True})
server.CONFIG_PATH.write_text(raw, encoding="utf-8")
cfg, warn = server.load_config()
check(server.CONFIG_PATH.read_text(encoding="utf-8") == raw and not list(server.CONFIG_DIR.glob("*.bak-*")), "schemaVersion zu neu: Datei unverändert")
check(warn and "MP-CFG-003" in warn[0], "schemaVersion zu neu: Warnung MP-CFG-003")
check(code_of(lambda: server.save_config(cfg))[0] == "MP-CFG-003", "schemaVersion zu neu: save_config verweigert")

# 8) Ungültige Dateien
d = fresh()
server.CONFIG_DIR.mkdir()
good = {"schemaVersion": 1, "tenantId": "abc", "company": {"name": "Gut"}}
server.CONFIG_PATH.write_text(json.dumps(good), encoding="utf-8")
server.load_config()  # erzeugt Ergänzung + .bak? (nur wenn geändert)
server.CONFIG_PATH.write_text(json.dumps(dict(good, company={"name": "Gut", "color": "rot"})), encoding="utf-8")
c, m = code_of(server.load_config)
check(c == "MP-CFG-002" and "company.color" in m and str(server.CONFIG_PATH) in m, "Ungültige Farbe: MP-CFG-002 mit Datei und Feldname")
check("letzte gültige Sicherung" in m and ".bak-" in m, "Meldung nennt letzte gültige .bak")
server.CONFIG_PATH.write_text("{kaputt", encoding="utf-8")
c, m = code_of(server.load_config)
check(c == "MP-CFG-001", "Kein JSON: MP-CFG-001")
check(server.CONFIG_PATH.read_text(encoding="utf-8") == "{kaputt", "Ungültige Datei wird nie überschrieben")
server.CONFIG_PATH.write_text("[1]", encoding="utf-8")
check(code_of(server.load_config)[0] == "MP-CFG-002", "Kein Objekt: MP-CFG-002")

for label, patch in {
    "tenantId ungültig": {"tenantId": "Ab!"},
    "Logo zu groß": {"company": {"logoFile": "big.png"}},
    "Logo fehlt": {"company": {"logoFile": "nix.png"}},
    "Logo-Pfad": {"company": {"logoFile": "../x.png"}},
    "Vorlage unbekannt": {"template": "gibtsnicht"},
    "Modul kein Bool": {"modules": {"chat": "ja"}},
    "Bereiche leer": {"projectAreas": []},
    "Sprache": {"locale": {"language": "fr"}},
}.items():
    (server.CONFIG_DIR / "big.png").write_bytes(b"0" * (server.CONFIG_LOGO_MAX + 1))
    server.CONFIG_PATH.write_text(json.dumps(dict(good, **patch)), encoding="utf-8")
    check(code_of(server.load_config)[0] == "MP-CFG-002", f"Validierung: {label}")

# 9) Start-Prüfung vor dem Port-Bind (Prozess): ungültig = Exit 3, gültig = weiter bis Admin-Prüfung (Exit 2)
runner = (
    "import sys,server;from pathlib import Path\n"
    "d=Path(sys.argv[1]);server.DATA_DIR=d/'data';server.DB_PATH=server.DATA_DIR/'m.sqlite3'\n"
    "sys.argv=['server.py','--host','10.0.0.5','--port','1','--allowed-subnet','10.0.0.0/24'];server.main()\n"
)


def start(dirp):
    env = dict(os.environ, MP_CONFIG_DIR=str(dirp / "config"))
    return subprocess.run([sys.executable, "-c", runner, str(dirp)], cwd=SRC, env=env, capture_output=True, text=True, timeout=60)


d = Path(tempfile.mkdtemp(prefix="mp-cfg-start-"))
(d / "config").mkdir()
(d / "config" / "firma.json").write_text('{"schemaVersion":1,"company":{"color":"zzz"}}', encoding="utf-8")
r = start(d)
check(r.returncode == 3 and "MP-CFG-002" in r.stderr and "company.color" in r.stderr, f"Start mit ungültiger Config verweigert (Exit {r.returncode}, MP-CFG-002)")
check((d / "config" / "firma.json").read_text(encoding="utf-8") == '{"schemaVersion":1,"company":{"color":"zzz"}}', "Ungültige Config nach Startversuch unverändert")
(d / "config" / "firma.json").write_text('{"schemaVersion":1,"company":{"color":"#abcdef"}}', encoding="utf-8")
r = start(d)
check(r.returncode == 2 and "MP-CFG" not in r.stderr, f"Gültige Config: Start läuft bis zur Admin-Prüfung (Exit {r.returncode})")

# 10) Backup: ZIP ohne .bak, Rotation, Restore mit .bak der aktuellen Datei
b = Path(tempfile.mkdtemp(prefix="mp-cfg-bk-"))
shutil.copy2(SRC / "Backup_Datenbank.py", b / "Backup_Datenbank.py")
(b / "data").mkdir()
con = sqlite3.connect(b / "data" / "maschinenplanung.sqlite3")
con.execute("CREATE TABLE state(id INTEGER PRIMARY KEY, revision INTEGER, json TEXT)")
con.execute("INSERT INTO state VALUES(1,5,'{}')")
con.commit()
con.close()
(b / "config").mkdir()
(b / "config" / "firma.json").write_text('{"schemaVersion":1,"tenantId":"abc"}', encoding="utf-8")
(b / "config" / "logo.png").write_bytes(b"PNG")
(b / "config" / "firma.json.bak-20260101-000000").write_text("{}", encoding="utf-8")
env = {k: v for k, v in os.environ.items() if k != "MP_CONFIG_DIR"}


def bk(*a):
    return subprocess.run([sys.executable, "-I", str(b / "Backup_Datenbank.py"), *a], capture_output=True, text=True, timeout=60, env=env)


r = bk()
zips = list((b / "backups").glob("firma_*.zip"))
check(r.returncode == 0 and len(zips) == 1, "Backup legt firma_<zeit>.zip an")
names = sorted(zipfile.ZipFile(zips[0]).namelist())
check(names == ["firma.json", "logo.png"], f"ZIP enthält config ohne .bak ({names})")
(b / "BACKUP_ZIEL.txt").write_text(str(b / "zweit"), encoding="utf-8")
import time; time.sleep(1.1)
r = bk()
check(r.returncode == 0 and len(list((b / "zweit").glob("firma_*.zip"))) == 1, "Zweitkopie enthält das Config-ZIP")
(b / "config" / "firma.json").write_text('{"schemaVersion":1,"tenantId":"neu"}', encoding="utf-8")
(b / "config" / "logo.png").unlink()
r = bk("--restore-config", str(sorted((b / "backups").glob("firma_*.zip"))[0]))
check(r.returncode == 0 and json.loads((b / "config" / "firma.json").read_text())["tenantId"] == "abc" and (b / "config" / "logo.png").exists(), "Restore stellt firma.json und Logo wieder her")
check(any(json.loads(p.read_text()).get("tenantId") == "neu" for p in (b / "config").glob("firma.json.bak-2*")), "Restore sichert die aktuelle Datei vorher als .bak")
bad = b / "evil.zip"
with zipfile.ZipFile(bad, "w") as z:
    z.writestr("firma.json", "{}")
    z.writestr("../evil.txt", "x")
r = bk("--restore-config", str(bad))
check(r.returncode == 1 and not (b / "evil.txt").exists(), "ZIP mit Pfad außerhalb von config wird abgelehnt")
import importlib.util
spec = importlib.util.spec_from_file_location("bk", b / "Backup_Datenbank.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.KEEP = 2
for i in range(4):
    (b / "backups" / f"firma_2020-01-0{i+1}_000000.zip").write_bytes(b"x")
    os.utime(b / "backups" / f"firma_2020-01-0{i+1}_000000.zip", (1000 + i, 1000 + i))
m.prune(b / "backups")
check(len(list((b / "backups").glob("firma_*.zip"))) == 2, "ZIP-Rotation behält KEEP Stück")

print(f"\n{sum(RESULTS)}/{len(RESULTS)} bestanden")
sys.exit(0 if all(RESULTS) else 1)

#!/usr/bin/env python3
"""V12.14.0: Update überschreibt Paket -> config bleibt (Dateisystem-Simulation von UPDATE_LIVE.ps1).

Aufruf:  python tests/test_config_update.py
Die Dateiliste $MP_AppFiles / $MP_ObsoleteFiles wird aus MP_Common.ps1 gelesen.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC))
os.environ["MP_CONFIG_DIR"] = tempfile.mkdtemp(prefix="mp-cfgu-env-")
import server  # noqa: E402

RESULTS: list[bool] = []


def check(cond, label):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + label)


def ps_list(text: str, name: str) -> list[str]:
    m = re.search(r"\$" + name + r"\s*=\s*@\((.*?)\n\)", text, re.S)
    return re.findall(r"'([^']+)'", m.group(1)) if m else []


common = (SRC / "MP_Common.ps1").read_text(encoding="utf-8-sig")
update = (SRC / "UPDATE_LIVE.ps1").read_text(encoding="utf-8-sig")
restore = (SRC / "Restore_Datenbank.ps1").read_text(encoding="utf-8-sig")
uninst = (SRC / "Deinstallieren.ps1").read_text(encoding="utf-8-sig")
setup = (SRC / "Setup_Windows.ps1").read_text(encoding="utf-8-sig")
APP = ps_list(common, "MP_AppFiles")
OBS = ps_list(common, "MP_ObsoleteFiles")
check(len(APP) > 20 and "server.py" in APP, f"{len(APP)} Programmdateien aus MP_Common.ps1 gelesen")
check(not any(re.match(r"(?i)^config([\\/]|$)", n) for n in APP + OBS), "config steht weder in $MP_AppFiles noch in $MP_ObsoleteFiles")
check(re.search(r"\$MP_ConfigDir\s*=\s*'config'", common) is not None, "$MP_ConfigDir = 'config' definiert")
# #73 Phase 1: server.py importiert flache Module; sie werden immer mitgeliefert, sind aber keine Pflichtdateien im
# Manifest (ältere Pakete bleiben installier- und rücksetzbar).
import app_updates  # noqa: E402
for mod in ("mp_config.py", "mp_tls.py", "mp_rights.py"):
    check(mod in APP and (SRC / mod).is_file() and mod not in app_updates.validate_manifest.__code__.co_consts,
          f"{mod} flach in $MP_AppFiles, nicht Pflichtdatei im Manifest")

# Statische Zusagen der Skripte
check(update.count("$MP_ConfigDir") >= 3, "UPDATE_LIVE.ps1: config bei Sicherung, Umzug und Rollback berücksichtigt")
check(re.search(r"Invoke-MPPreflight[^\n]*\$OldBase", update) is not None, "UPDATE_LIVE.ps1: Vorabtest bekommt den Live-Ordner (echte Config)")
pre = common[common.index("function Invoke-MPPreflight"):]
check("$MP_ConfigDir" in pre.split("Start-Process")[0], "Invoke-MPPreflight kopiert config in den Temp-Ordner")
check("MitConfig" in restore and "--restore-config" in restore, "Restore_Datenbank.ps1 -MitConfig")
check("config" in uninst and "Remove-Item" not in uninst.split("Unregister")[0].replace("Remove-NetFirewallRule", ""), "Deinstallieren behält config (nennt es, löscht keine Ordner)")
check(not re.search(r"Remove-Item[^\n]*config", update + setup + uninst, re.I), "Kein Skript löscht config")
# V12.27.0: Kopie über Copy-MPAppFile (Unterordner-fähig); der Helfer kopiert genau eine Datei.
helper = common[common.index("function Copy-MPAppFile"):]
helper = helper[:helper.index("\n}\n")]
check("foreach ($name in $MP_AppFiles) { Copy-MPAppFile $NewSource $TargetBase $name }" in update and helper.count("Copy-Item") == 1
      and "-Recurse" not in helper, "Updatekopie nimmt nur Namen aus $MP_AppFiles")


def sha_tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def touch_pkg(root: Path, version: str):
    root.mkdir(parents=True, exist_ok=True)
    for n in APP:
        (root / n).write_text(f"{n} {version}\n", encoding="utf-8")


def put_config(live: Path):
    c = live / "config"
    c.mkdir()
    (c / "firma.json").write_text(json.dumps({
        "schemaVersion": 1, "tenantId": "werbetechnik", "company": {"name": "Meine Firma", "color": "#E2382A", "logoFile": "logo.jpg"},
        "locale": {"holidayRegion": "SH"}, "terms": {"projectNumber": "WT"}, "eigenes": {"x": 1}}, indent=2), encoding="utf-8")
    (c / "logo.jpg").write_bytes(b"\xff\xd8JPEGDATA")
    (c / "lizenz.key").write_text("KEY-123", encoding="utf-8")
    (c / "firma.json.bak-20260101-000000").write_text("{}", encoding="utf-8")


def make_live(version="12.13.0") -> Path:
    live = Path(tempfile.mkdtemp(prefix="mp-live-"))
    touch_pkg(live, version)
    (live / "data").mkdir()
    (live / "data" / "maschinenplanung.sqlite3").write_bytes(b"DB-" + version.encode())
    (live / "LAN_CONFIG.json").write_text('{"lan_ip":"192.168.1.5","port":8765}', encoding="utf-8")
    (live / "BACKUP_ZIEL.txt").write_text(r"\\nas\backup", encoding="utf-8")
    (live / "backups").mkdir()
    (live / "backups" / "maschinenplanung_x.sqlite3").write_bytes(b"B")
    put_config(live)
    return live


def sim_update(new: Path, live: Path, target: Path | None = None):
    """Spiegelt Schritt 5/6 aus UPDATE_LIVE.ps1. Rückgabe: Rollback-Ordner."""
    target = target or live
    rb = target / "update_backups" / "pre_Vneu"
    rb.mkdir(parents=True)
    for p in live.iterdir():                       # Get-ChildItem -File | Copy-Item
        if p.is_file():
            shutil.copy2(p, rb)
    if (live / "config").exists():                 # config rekursiv in den Rollback-Stand
        shutil.copytree(live / "config", rb / "config")
    shutil.copy2(live / "data" / "maschinenplanung.sqlite3", rb / "maschinenplanung_vor_update.sqlite3")
    if target != live:                             # Umzug
        (target / "data").mkdir(exist_ok=True)
        (target / "backups").mkdir(exist_ok=True)
        for f in ("LAN_CONFIG.json", "BACKUP_ZIEL.txt"):
            shutil.copy2(live / f, target / f)
        shutil.copytree(live / "config", target / "config", dirs_exist_ok=True)
        shutil.copy2(live / "data" / "maschinenplanung.sqlite3", target / "data" / "maschinenplanung.sqlite3")
    for n in APP:                                  # nur $MP_AppFiles
        shutil.copy2(new / n, target / n)
    for n in OBS:
        (target / n).unlink(missing_ok=True)
    shutil.rmtree(target / "__pycache__", ignore_errors=True)
    return rb


def sim_rollback(rb: Path, target: Path):
    for p in rb.iterdir():
        if p.is_file() and p.name != "maschinenplanung_vor_update.sqlite3":
            shutil.copy2(p, target)
    if (rb / "config").exists():
        shutil.copytree(rb / "config", target / "config", dirs_exist_ok=True)


# 1) Update am gleichen Ort
live = make_live()
new = Path(tempfile.mkdtemp(prefix="mp-new-"))
touch_pkg(new, "12.14.0")
(new / "config").mkdir()                              # Paketordner mit (Beispiel-)config darf nichts ändern
(new / "config" / "firma.json").write_text('{"tenantId":"paket"}', encoding="utf-8")
before = sha_tree(live / "config")
side_before = {n: (live / n).read_bytes() for n in ("LAN_CONFIG.json", "BACKUP_ZIEL.txt")}
rb = sim_update(new, live)
check(sha_tree(live / "config") == before, "Update: config\\ byte-genau gleich (SHA256, inkl. Logo, Lizenz, .bak)")
check((live / "server.py").read_text(encoding="utf-8").endswith("12.14.0\n"), "Update: Programmdateien sind neu")
check(all((live / n).read_bytes() == v for n, v in side_before.items()), "Update: LAN_CONFIG.json (Hosts/Port) und BACKUP_ZIEL.txt unverändert")
check(sha_tree(rb / "config") == before, "Rollback-Sicherung enthält config\\ rekursiv")

# 2) Neue Version startet mit der echten Config: ergänzt nur Fehlendes
server.CONFIG_DIR = live / "config"
server.CONFIG_PATH = server.CONFIG_DIR / "firma.json"
server.DATA_DIR = live / "data"
server.DB_PATH = server.DATA_DIR / "maschinenplanung.sqlite3"
orig = json.loads(server.CONFIG_PATH.read_text(encoding="utf-8"))
cfg, warn = server.load_config()
check(cfg["company"]["name"] == "Meine Firma" and cfg["company"]["color"] == "#E2382A" and cfg["terms"]["projectNumber"] == "WT"
      and cfg["locale"]["holidayRegion"] == "SH" and cfg["tenantId"] == "werbetechnik" and cfg["eigenes"] == {"x": 1}, "Neue Version: Name, Farbe, Begriffe, Feiertagsregion, eigene Felder unverändert")
check("modules" in cfg and "projectAreas" in cfg and not warn, "Neue Version ergänzt nur fehlende Felder")
check((live / "config" / "logo.jpg").read_bytes() == b"\xff\xd8JPEGDATA" and (live / "config" / "lizenz.key").read_text() == "KEY-123", "Logo und Lizenz unverändert")
check(any(json.loads(p.read_text()) == orig for p in live.glob("config/firma.json.bak-2*")), "Ergänzung hat den Vorzustand als .bak gesichert")

# 3) Rollback nach fehlgeschlagenem Update: config auf Stand vor dem Update
(live / "config" / "firma.json").write_text('{"schemaVersion":1,"tenantId":"kaputt"}', encoding="utf-8")
sim_rollback(rb, live)
check(sha_tree(live / "config").get("firma.json") == before["firma.json"], "Rollback stellt firma.json wieder her")
check((live / "server.py").read_text(encoding="utf-8").endswith("12.13.0\n"), "Rollback stellt den alten Programmstand wieder her")

# 4) Umzug in anderen Zielordner
live2 = make_live()
tgt = Path(tempfile.mkdtemp(prefix="mp-target-"))
before2 = sha_tree(live2 / "config")
sim_update(new, live2, tgt)
check(sha_tree(tgt / "config") == before2, "Umzug: config\\ im Zielordner byte-genau gleich")
check(sha_tree(live2 / "config") == before2, "Umzug: alter Ordner bleibt unverändert")
check((tgt / "LAN_CONFIG.json").exists() and (tgt / "BACKUP_ZIEL.txt").exists(), "Umzug: Hosts/Port und Backup-Ziel mitgenommen")

# 5) Vorabtest: Kopie von config für die neue Version, Live unberührt
live3 = make_live()
tmpdir = Path(tempfile.mkdtemp(prefix="mp-pre-"))
shutil.copytree(live3 / "config", tmpdir / "config")
b3 = sha_tree(live3 / "config")
server.CONFIG_DIR = tmpdir / "config"
server.CONFIG_PATH = server.CONFIG_DIR / "firma.json"
server.DB_PATH = tmpdir / "nix.sqlite3"
server.load_config()
check(sha_tree(live3 / "config") == b3, "Vorabtest arbeitet auf der Kopie; Live-config unverändert")
(tmpdir / "config" / "firma.json").write_text('{"company":{"color":"x"}}', encoding="utf-8")
try:
    server.load_config()
    refused = False
except server.ConfigError as e:
    refused = e.code == "MP-CFG-002"
check(refused, "Vorabtest: ungültige Config bricht ab (MP-CFG-002), bevor Live berührt wird")

print(f"\n{sum(RESULTS)}/{len(RESULTS)} bestanden")
sys.exit(0 if all(RESULTS) else 1)

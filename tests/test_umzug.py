#!/usr/bin/env python3
"""Umzug auf ein neues Geraet (Umzug.py, von Umzug_Exportieren.ps1 / Umzug_Importieren.ps1 aufgerufen).

Prueft: Export (frisches WAL-sicheres Backup, Manifest mit SHA256, Lizenz/tls/Logo/BACKUP_ZIEL dabei, LAN_CONFIG
nicht), Rundreise Export -> Import (Daten, app_meta-Kulanz, config byte-gleich), Manipulation wird abgewiesen
(Inhalt, Zusatzdatei, Pfad, Manifest, SHA256 der ganzen Datei), Versionspruefung (Ziel >= Export), kein
Ueberschreiben ohne Schalter, mit Schalter vorherige Sicherung, Ruecknahme bei Fehler mitten im Einspielen,
sowie die statischen Zusagen (Paketliste, ASCII-Skripte, Fehlercodes, CI).

Aufruf:  python tests/test_umzug.py
Arbeitet nur in Temp-Ordnern (nie mit Live-Daten).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, label: str) -> None:
    RESULTS.append((bool(cond), label))
    print(("PASS " if cond else "FAIL ") + label)


TMP = Path(tempfile.mkdtemp(prefix="mp-umzug-"))


def make_install(folder: Path, version: str) -> Path:
    """Programmordner wie installiert: nur die fuer den Umzug noetigen Dateien + server.py mit APP_VERSION."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("Umzug.py", "Backup_Datenbank.py"):
        shutil.copy2(SRC / name, folder / name)
    (folder / "server.py").write_text(f'#!/usr/bin/env python3\nAPP_VERSION = "{version}"\n', encoding="utf-8")
    return folder


def run(folder: Path, *args: str) -> tuple[int, str, dict]:
    res = TMP / "ergebnis.json"
    res.unlink(missing_ok=True)
    p = subprocess.run([sys.executable, "-I", str(folder / "Umzug.py"), *args, "--ergebnis", str(res)],
                       capture_output=True, text=True, timeout=120)
    data = json.loads(res.read_text(encoding="ascii")) if res.exists() else {}
    return p.returncode, p.stdout + p.stderr, data


def revision(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT revision FROM state WHERE id=1").fetchone()[0]
    finally:
        con.close()


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


# ---------------- Geraet 1: laufende Installation (WAL, ungecheckpointete Aenderung) ----------------
dev1 = make_install(TMP / "geraet1", "12.27.0")
(dev1 / "data").mkdir()
DB1 = dev1 / "data" / "maschinenplanung.sqlite3"
live = sqlite3.connect(DB1)
live.execute("PRAGMA journal_mode=WAL")
live.execute("PRAGMA wal_autocheckpoint=0")
live.execute("CREATE TABLE state(id INTEGER PRIMARY KEY, revision INTEGER, json TEXT)")
live.execute("CREATE TABLE app_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
live.execute("INSERT INTO state VALUES(1, 6, '{\"workSteps\": []}')")
live.execute("INSERT INTO app_meta VALUES('license_grace_start', '1760000000')")
live.commit()
live.execute("UPDATE state SET revision=7, json='{\"workSteps\": [{\"id\": \"ws1\"}]}' WHERE id=1")
live.commit()   # Stand 7 liegt nur im -wal (Verbindung bleibt offen wie beim laufenden Server)
check(Path(str(DB1) + "-wal").stat().st_size > 0, "Vorbereitung: juengster Stand liegt im -wal")
cfg1 = dev1 / "config"
(cfg1 / "tls").mkdir(parents=True)
(cfg1 / "firma.json").write_text(json.dumps({"schemaVersion": 1, "tenantId": "mueller", "company": {"name": "Müller & Söhne GmbH"}}), encoding="utf-8")
(cfg1 / "firma.json.bak-20261001-101010").write_text("{}", encoding="utf-8")
(cfg1 / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
(cfg1 / "lizenz.key").write_text('{"payload": {"tenantId": "mueller"}, "signature": "x"}', encoding="utf-8")
for n in ("firmen-ca.crt", "firmen-ca.key", "server.crt", "server.key"):
    (cfg1 / "tls" / n).write_bytes(("-----" + n + "-----\n").encode())
(cfg1 / "halb.tmp").write_text("abgebrochen", encoding="utf-8")
(dev1 / "BACKUP_ZIEL.txt").write_text(str(TMP / "nas") + "\r\n", encoding="utf-8")
(dev1 / "LAN_CONFIG.json").write_text(json.dumps({"lan_ip": "192.168.1.10", "port": 8765, "UpdateRepo": "konto/repo"}), encoding="utf-8-sig")
(TMP / "nas").mkdir()

# ---------------- Export ----------------
out = TMP / "stick"
code, log, exp = run(dev1, "--export", "--base", str(dev1), "--ziel", str(out))
check(code == 0, f"Export laeuft durch (Exitcode {code})" + ("" if code == 0 else f": {log}"))
zips = list(out.glob("Umzug_*.zip"))
check(len(zips) == 1 and re.fullmatch(r"Umzug_Mueller_Soehne_GmbH_\d{4}-\d{2}-\d{2}_\d{6}\.zip", zips[0].name) is not None,
      f"genau EINE Datei Umzug_<Firma>_<Datum>.zip ({[z.name for z in zips]})")
PKG = zips[0]
check(not list(out.glob("*.tmp")), "keine .tmp-Reste im Zielordner")
check(exp.get("sha256") == hashlib.sha256(PKG.read_bytes()).hexdigest(), "Ergebnis nennt SHA256 der Datei")
check(exp.get("appVersion") == "12.27.0" and exp.get("dbRevision") == 7 and exp.get("tenantId") == "mueller", "Ergebnis: Version, Revision 7 (aus dem -wal), tenantId")
check(exp.get("lizenz") and exp.get("tls") and exp.get("ca") and exp.get("updateRepo") == "konto/repo", "Ergebnis: Lizenz, tls, Firmen-CA, UpdateRepo")
check(any(p.name.endswith("_umzug.sqlite3") for p in (dev1 / "backups").glob("maschinenplanung_*.sqlite3")),
      "Export legt zusaetzlich eine normale gepruefte Sicherung in backups\\ an")
with zipfile.ZipFile(PKG) as z:
    names = set(z.namelist())
    manifest = json.loads(z.read("umzug.json"))
check({"umzug.json", "data/maschinenplanung.sqlite3", "config/firma.json", "config/logo.png", "config/lizenz.key",
       "config/tls/firmen-ca.crt", "config/tls/firmen-ca.key", "config/tls/server.crt", "config/tls/server.key",
       "extra/BACKUP_ZIEL.txt"} <= names, "Paket enthaelt DB, firma.json, Logo, lizenz.key, tls\\ inkl. CA, BACKUP_ZIEL.txt")
check(not any("LAN_CONFIG" in n or n.endswith(".tmp") for n in names), "LAN_CONFIG.json (geraetebezogen) und .tmp nicht im Paket")
check(set(manifest["dateien"]) == names - {"umzug.json"} and all(re.fullmatch(r"[0-9a-f]{64}", e["sha256"]) for e in manifest["dateien"].values()),
      "Manifest listet jede Datei mit SHA256")
check(manifest["schema"] == 1 and manifest["art"] == "maschinenplanung-umzug" and manifest["firma"] == "Müller & Söhne GmbH", "Manifest: Format, Firma")
live.close()

code, log, ver = run(dev1, "--verify", "--datei", str(PKG), "--sha256", exp["sha256"])
check(code == 0 and ver.get("appVersion") == "12.27.0", "Pruefung mit korrektem SHA256 besteht")
code, log, _ = run(dev1, "--verify", "--datei", str(PKG), "--sha256", "0" * 64)
check(code == 1 and "MP-UMZ-003" in log, "falscher SHA256 der ganzen Datei: MP-UMZ-003")


# ---------------- Manipulation ----------------
def rebuild(name: str, change) -> Path:
    """Kopie des Pakets mit veraendertem Inhalt; change(members: dict) aendert {name: bytes}."""
    with zipfile.ZipFile(PKG) as z:
        members = {n: z.read(n) for n in z.namelist()}
    change(members)
    dst = TMP / name
    with zipfile.ZipFile(dst, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)
    return dst


cases = {
    "inhalt.zip": lambda m: m.__setitem__("config/lizenz.key", b'{"payload": {"tenantId": "andere"}}'),
    "zusatz.zip": lambda m: m.__setitem__("config/boese.py", b"print(1)"),
    "ohne_manifest.zip": lambda m: m.pop("umzug.json"),
    "db_fehlt.zip": lambda m: m.pop("data/maschinenplanung.sqlite3"),
}


def traversal(m):
    mf = json.loads(m["umzug.json"])
    mf["dateien"]["config/../../server.py"] = {"sha256": hashlib.sha256(b"x").hexdigest(), "groesse": 1}
    m["umzug.json"] = json.dumps(mf).encode()
    m["config/../../server.py"] = b"x"


def wrong_manifest_hash(m):
    mf = json.loads(m["umzug.json"])
    mf["dateien"]["config/logo.png"]["sha256"] = "0" * 64
    m["umzug.json"] = json.dumps(mf).encode()


cases["pfad.zip"] = traversal
cases["manifest_hash.zip"] = wrong_manifest_hash
for name, change in cases.items():
    bad = rebuild(name, change)
    code, log, _ = run(dev1, "--verify", "--datei", str(bad))
    check(code == 1 and re.search(r"MP-UMZ-00[23]", log) is not None, f"manipuliertes Paket ({name}) wird abgewiesen")
(TMP / "kaputt.zip").write_bytes(b"PK\x03\x04 kein zip")
code, log, _ = run(dev1, "--verify", "--datei", str(TMP / "kaputt.zip"))
check(code == 1 and "MP-UMZ-002" in log, "keine ZIP-Datei: MP-UMZ-002")

# ---------------- Import: Versionspruefung ----------------
old = make_install(TMP / "geraet_alt", "12.26.9")
code, log, _ = run(old, "--import", "--datei", str(PKG), "--base", str(old))
check(code == 1 and "MP-UMZ-004" in log and "12.27.0" in log, "Ziel mit aelterer Version: MP-UMZ-004")
check(not (old / "data").exists() and not (old / "config").exists(), "abgewiesener Import schreibt nichts")
bad = rebuild("inhalt2.zip", cases["inhalt.zip"])
dev2 = make_install(TMP / "geraet2", "12.28.0")   # neuere Version darf importieren
code, log, _ = run(dev2, "--import", "--datei", str(bad), "--base", str(dev2))
check(code == 1 and "MP-UMZ-003" in log and not (dev2 / "data" / "maschinenplanung.sqlite3").exists(), "manipuliertes Paket wird beim Import abgewiesen, nichts geschrieben")

# ---------------- Import: Rundreise auf frisches Geraet ----------------
(dev2 / "config" / "tls").mkdir(parents=True)   # wie Umzug_Importieren.ps1: tls\\ vorab mit gesperrten Rechten
code, log, imp = run(dev2, "--import", "--datei", str(PKG), "--base", str(dev2), "--sha256", exp["sha256"])
check(code == 0, f"Import auf neueres, leeres Geraet (Exitcode {code})" + ("" if code == 0 else f": {log}"))
DB2 = dev2 / "data" / "maschinenplanung.sqlite3"
check(revision(DB2) == 7, "Datenbank: Revision 7 (Stand aus dem -wal) angekommen")
con = sqlite3.connect(DB2)
check(con.execute("SELECT value FROM app_meta WHERE key='license_grace_start'").fetchone() == ("1760000000",), "Lizenz-Kulanz (app_meta) zieht mit der Datenbank um")
check(con.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "integrity_check ok")
con.close()
check(not Path(str(DB2) + "-wal").exists() or Path(str(DB2) + "-wal").stat().st_size == 0, "kein fremdes -wal neben der eingespielten Datenbank")
want = {k: v for k, v in tree(cfg1).items() if not k.endswith(".tmp")}
check(tree(dev2 / "config") == want, "config\\ byte-gleich (firma.json, Logo, lizenz.key, tls\\ mit Firmen-CA)")
check(all((dev2 / "config" / "tls" / n).is_file() for n in ("server.crt", "server.key", "firmen-ca.crt", "firmen-ca.key")),
      "Firmen-CA mit Schluessel vorhanden: Server erneuert das Zertifikat fuer die neue IP selbst (mp_tls.tls_renew_reason, test_tls.py)")
check((dev2 / "BACKUP_ZIEL.txt").read_bytes() == (dev1 / "BACKUP_ZIEL.txt").read_bytes() and imp.get("backupZielErreichbar") is True, "BACKUP_ZIEL.txt uebernommen, Ziel erreichbar gemeldet")
check(imp.get("updateRepo") == "konto/repo" and imp.get("lizenz") and imp.get("tls") and imp.get("zielVersion") == "12.28.0", "Ergebnis: UpdateRepo, Lizenz, tls, Zielversion")
check(not (dev2 / "LAN_CONFIG.json").exists(), "LAN_CONFIG.json wird nicht kopiert (schreibt der Serverstart des neuen Geraets)")
check(not list((dev2 / "backups").glob("umzug_*")), "frisches Geraet: kein Rest des Entpackordners")

# ---------------- Kein Ueberschreiben ohne Schalter ----------------
con = sqlite3.connect(DB2)
con.execute("UPDATE state SET revision=9 WHERE id=1")
con.commit()
con.close()
(dev2 / "config" / "firma.json").write_text('{"tenantId": "mueller", "neu": true}', encoding="utf-8")
cfg_before = tree(dev2 / "config")
code, log, _ = run(dev2, "--import", "--datei", str(PKG), "--base", str(dev2))
check(code == 1 and "MP-UMZ-005" in log and "-Ueberschreiben" in log, "vorhandene Daten ohne -Ueberschreiben: MP-UMZ-005")
check(revision(DB2) == 9 and tree(dev2 / "config") == cfg_before, "Daten und config bleiben unveraendert")

# ---------------- Mit Schalter: vorher sichern ----------------
code, log, imp2 = run(dev2, "--import", "--datei", str(PKG), "--base", str(dev2), "--ueberschreiben")
check(code == 0 and revision(DB2) == 7, "mit -Ueberschreiben: Paketstand eingespielt")
pre = Path(imp2.get("vorherDb", ""))
check(pre.is_file() and pre.name.endswith("_vor_umzug.sqlite3") and revision(pre) == 9, "vorheriger Stand als *_vor_umzug.sqlite3 gesichert (Revision 9)")
prev_cfg = Path(imp2.get("vorherConfig", ""))
check(prev_cfg.is_dir() and tree(prev_cfg) == cfg_before and prev_cfg.is_relative_to(dev2 / "backups"), "vorherige config\\ in backups\\ gesichert")
check(tree(dev2 / "config") == want, "config\\ jetzt wie im Paket")

# ---------------- Fehler mitten im Einspielen: Ruecknahme ----------------
spec = importlib.util.spec_from_file_location("umzug_inproc", dev2 / "Umzug.py")
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)
con = sqlite3.connect(DB2)
con.execute("UPDATE state SET revision=11 WHERE id=1")
con.commit()
con.close()
cfg_before = tree(dev2 / "config")
real_copytree = U.shutil.copytree


FAILED = []


def failing_copytree(src, dst, *a, **k):
    if Path(dst) == dev2 / "config" and "paket" in Path(src).parts and not FAILED:
        FAILED.append(src)
        raise OSError("Datentraeger voll (Test)")
    return real_copytree(src, dst, *a, **k)


U.shutil.copytree = failing_copytree
try:
    U.import_package(PKG, dev2, overwrite=True)
    check(False, "Fehler beim Einspielen von config wird gemeldet")
except OSError:
    check(True, "Fehler beim Einspielen von config wird gemeldet")
finally:
    U.shutil.copytree = real_copytree
check(revision(DB2) == 11 and tree(dev2 / "config") == cfg_before, "Ruecknahme: Datenbank (Revision 11) und config wie vorher")

# ---------------- Statische Zusagen ----------------
common = (SRC / "MP_Common.ps1").read_text(encoding="utf-8-sig")
app = re.findall(r"'([^']+)'", re.search(r"\$MP_AppFiles\s*=\s*@\((.*?)\n\)", common, re.S).group(1))
check({"Umzug.py", "Umzug_Exportieren.ps1", "Umzug_Importieren.ps1"} <= set(app), "Umzug.py und beide Skripte stehen in $MP_AppFiles (werden ausgeliefert)")
for name in ("Umzug_Exportieren.ps1", "Umzug_Importieren.ps1", "Umzug.py"):
    raw = (SRC / name).read_bytes().removeprefix(b"\xef\xbb\xbf")
    check(all(b < 128 for b in raw) if name.endswith(".ps1") else True, f"{name}: nur ASCII (Windows PowerShell 5.1)")
umzug_py = (SRC / "Umzug.py").read_text(encoding="utf-8")
msgs = re.findall(r'UmzugError\("MP-UMZ-\d{3}",\s*f?"([^"]*)"', umzug_py)
check(msgs and all(all(ord(c) < 128 for c in m) for m in msgs), "Umzug.py: Fehlermeldungen nur ASCII")
exp_ps = (SRC / "Umzug_Exportieren.ps1").read_text(encoding="utf-8-sig")
imp_ps = (SRC / "Umzug_Importieren.ps1").read_text(encoding="utf-8-sig")
for label, text, needles in (
    ("Umzug_Exportieren.ps1", exp_ps, ("Assert-MPAdmin", "Get-MPPython", "Umzug.py", "--export", "updates\\installing", "-Entsperren")),
    ("Umzug_Importieren.ps1", imp_ps, ("Assert-MPAdmin", "Get-MPPython", "Umzug.py", "--import", "--verify", "Stop-MPServer",
                                       "Protect-MPInstall", "Wait-MPHealth", "Register-MPServerTask", "Register-MPBackupTask",
                                       "Assert-MPPrivatePaths", "-Ueberschreiben", "Publish-MPCa")),
):
    missing = [n for n in needles if n not in text]
    check(not missing, f"{label} nutzt {', '.join(needles)}" + (f" (fehlt: {missing})" if missing else ""))
codes = (SRC / "FEHLERCODES.txt").read_text(encoding="utf-8-sig")
used = set(re.findall(r"MP-UMZ-\d{3}", exp_ps + imp_ps + umzug_py))
check(used and all(re.search(rf"^{c}\s", codes, re.M) for c in used), f"alle MP-UMZ-Codes in FEHLERCODES.txt ({sorted(used)})")
ci = (SRC / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
check("Umzug.py" in ci and "tests/test_umzug.py" in ci, "CI kompiliert Umzug.py und startet tests/test_umzug.py")

shutil.rmtree(TMP, ignore_errors=True)
failed = [label for ok, label in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} bestanden")
sys.exit(1 if failed else 0)

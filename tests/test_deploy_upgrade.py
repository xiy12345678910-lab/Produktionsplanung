#!/usr/bin/env python3
"""Datenerhalt bei Updates (ab V12.15.1): Ein Update ändert nur Code, nie Daten.

Für jeden alten Paketstand (Baseline) wird die Produktivlogik von UPDATE_LIVE.ps1 im Dateisystem nachgestellt:

  1. Alten Stand aus git holen, nur dessen $MP_AppFiles installieren, mit dem ALTEN server.py starten und über die API
     realistische Daten anlegen (Benutzer, Aufträge, Arbeitsschritte, Personal, Abwesenheiten, Formate, Chat,
     Benachrichtigungen, Firmen-CI/Logo). Dazu LAN_CONFIG.json, BACKUP_ZIEL.txt, logs und echte Backups (alter Backup-Lauf).
  2. Server stoppen, Fingerabdruck (alle Tabellen) und Dateibaum festhalten.
  3. Update: nur $MP_AppFiles des AKTUELLEN Stands (aus MP_Common.ps1 geparst) darüberkopieren, $MP_ObsoleteFiles entfernen.
  4. Neuer Server startet. Prüfung: alle Datensätze feldweise gleich (erlaubt sind nur NEUE Felder), Logins klappen,
     config\\firma.json entstand aus dem Bestand (Name, Farbe, Logo, Akzent wie vorher), LAN_CONFIG.json/BACKUP_ZIEL.txt/
     backups\\ byte-gleich, außerhalb von data\\ und config\\ wurde nichts geschrieben.
  5. Zweites Update (aktueller Stand auf sich selbst): Datenbank und config\\ danach identisch.
  6. Rollback auf den Paketstand davor: Daten und Config bleiben, der alte Server startet und sieht alle Daten.
  7. Bestand ohne firma.json und ohne Firmen-CI darf nie still neutral starten (MP-CFG-006); Firma_Einrichten behebt es.

Baselines: feste Stände (V12.10.1, V12.13.0, V12.14.0) plus automatisch der letzte Stand mit anderer APP_VERSION vor
dem aktuellen. Künftige Versionen werden dadurch ohne Änderung am Test mitgetestet.

Aufruf:  python tests/test_deploy_upgrade.py [--baseline <commit>] [--keep]
Umgebung: MP_UPGRADE_STRICT=1 (CI) macht fehlende git-Stände oder fehlende LAN-Adresse zu Fehlern statt Überspringen.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
sys.path.insert(0, str(HERE))
import deploy_lib as L  # noqa: E402

STRICT = os.environ.get("MP_UPGRADE_STRICT") == "1"
FIXED_BASELINES = [("V12.10.1", "5343676"), ("V12.13.0", "dac0860"), ("V12.14.0", "4624619")]
RESULTS: list[bool] = []
ADMIN, ADMIN_PW = "admin", "Admin-Passwort-ß-1"


def check(cond, label, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""), flush=True)
    return bool(cond)


def git(*args, binary=False, check_rc=True):
    r = subprocess.run(["git", "-C", str(SRC), *args], capture_output=True)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode(errors='replace')[:200]}")
    return r.stdout if binary else r.stdout.decode("utf-8", errors="replace")


def commit_exists(c: str) -> bool:
    return subprocess.run(["git", "-C", str(SRC), "cat-file", "-e", f"{c}^{{commit}}"], capture_output=True).returncode == 0


def app_version(text: str) -> str:
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else ""


CURRENT_VERSION = app_version((SRC / "server.py").read_text(encoding="utf-8"))


def previous_version_commit() -> tuple[str, str] | None:
    """Neuester Commit mit einer anderen APP_VERSION als der aktuelle Stand (Vorgänger-Release)."""
    try:
        for h in git("log", "--format=%H", "-n", "120", "--", "server.py").split():
            v = app_version(git("show", f"{h}:server.py", check_rc=False))
            if v and v != CURRENT_VERSION:
                return f"V{v}", h[:7]
    except RuntimeError:
        pass
    return None


def extract_commit(commit: str, dest: Path) -> None:
    data = git("archive", "--format=tar", commit, binary=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        tf.extractall(dest)


def clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if not k.startswith("MP_") and not k.startswith("PYTHON")}


class Server:
    def __init__(self, live: Path, ip: str, port: int, logp: Path):
        self.live, self.ip, self.port, self.proc, self.logp = live, ip, port, None, logp
        self.url = f"http://{ip}:{port}"
        self.logf = None

    def start(self, expect_version: str, seconds: int = 40):
        """Startet wie Run_Server_LAN.ps1 (python -X utf8 -u -I server.py ...). Rückgabe (ok, exitcode, log)."""
        self.logf = open(self.logp, "ab")
        self.proc = subprocess.Popen([sys.executable, "-X", "utf8", "-u", "-I", str(self.live / "server.py"), "--host", self.ip, "--port", str(self.port),
                                      "--allowed-subnet", f"{self.ip}/32"], cwd=self.live, stdout=self.logf, stderr=subprocess.STDOUT, env=clean_env())
        api = L.Api(self.url)
        ok = False
        for _ in range(seconds * 4):
            if self.proc.poll() is not None:
                break
            try:
                if api.health() == expect_version:
                    ok = True
                    break
            except OSError:
                pass
            time.sleep(0.25)
        code = self.proc.poll()
        return ok, code, self.log()

    def log(self) -> str:
        try:
            self.logf.flush()
            return self.logp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.logf:
            self.logf.close()
            self.logf = None


def run_py(live: Path, args: list[str], env_extra: dict | None = None, timeout=60):
    env = clean_env()
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-X", "utf8", "-I", str(live / args[0]), *args[1:]], cwd=live, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=timeout)


def copy_files(src: Path, dst: Path, names: list[str]):
    for n in names:
        shutil.copy2(src / n, dst / n)


def simulate_update(live: Path, pkg_src: Path, app: list[str], obsolete: list[str]):
    """Wie UPDATE_LIVE.ps1 Schritt 6: nur $MP_AppFiles kopieren, $MP_ObsoleteFiles und __pycache__ entfernen."""
    for n in app:
        shutil.copy2(pkg_src / n, live / n)
    for n in obsolete:
        (live / n).unlink(missing_ok=True)
    shutil.rmtree(live / "__pycache__", ignore_errors=True)


def firma_files(live: Path) -> dict[str, str]:
    d = live / "config"
    return L.tree_hashes(d) if d.exists() else {}


def config_additive(before: Path, live: Path) -> list[str]:
    """Erstes Update: Dateien in config\\ bleiben (firma.json nur um neue Felder ergänzt; uiAccent folgt data.ui.accent)."""
    out = []
    for p in sorted(before.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(before)
        q = live / "config" / rel
        if not q.exists():
            out.append(f"config/{rel.as_posix()} fehlt")
        elif rel.name == "firma.json":
            old, new = json.loads(p.read_text(encoding="utf-8-sig")), json.loads(q.read_text(encoding="utf-8-sig"))
            # Bewusst erlaubt: LEERE/Standard-Felder der Firma werden einmalig aus data.ci bzw. data.ui.accent gefüllt.
            fill = {f"/company/{k}" for k in ("name", "logoFile", "color", "font", "address", "footer", "uiAccent")}
            old.get("company", {}).pop("uiAccent", None)
            out += [f"firma.json{x}" for x in L.additive_diff(old, new, fill=fill)]
        elif p.read_bytes() != q.read_bytes():
            out.append(f"config/{rel.as_posix()} geändert")
    return out


def run_case(label: str, commit: str, scenario: str, keep: bool, ip: str):
    print(f"\n=== {label} ({commit}) -> V{CURRENT_VERSION}, Szenario {scenario} ===", flush=True)
    tag = f"{label}/{scenario}"
    root = Path(tempfile.mkdtemp(prefix="mp-upg-"))
    srv = None
    ok_all = True
    try:
        # ---------- 1. Alten Stand installieren ----------
        oldsrc = root / "oldsrc"
        extract_commit(commit, oldsrc)
        old_app, _ = L.package_lists((oldsrc / "MP_Common.ps1").read_text(encoding="utf-8-sig"))
        old_ver = app_version((oldsrc / "server.py").read_text(encoding="utf-8"))
        live = root / "live"
        live.mkdir()
        copy_files(oldsrc, live, old_app)
        r = run_py(live, ["server.py", "--init-admin", ADMIN], {"MP_ADMIN_PASSWORD": ADMIN_PW})
        ok_all &= check(r.returncode == 0, f"[{tag}] alter Stand V{old_ver}: Admin eingerichtet", r.stderr[-300:])
        port = L.free_port(ip)
        (live / "LAN_CONFIG.json").write_text(json.dumps({"lan_ip": ip, "prefix_length": 24, "subnet": f"{ip}/32", "port": port, "interface": "Ethernet",
                                                          "interface_description": "Test", "profile": "Private", "updated_at": "2026-10-01T08:00:00+02:00",
                                                          "UpdateRepo": "konto/repo"}, indent=2), encoding="utf-8")
        (live / "LAN_ADRESSEN.txt").write_text(f"LAN-IP:  http://{ip}:{port}\nSubnetz: {ip}/32\n", encoding="utf-8")
        second = root / "backup_zweitziel"
        second.mkdir()
        (live / "BACKUP_ZIEL.txt").write_text(str(second) + "\n# Zweitziel (Test)\n", encoding="utf-8")
        (live / "logs").mkdir()
        (live / "logs" / "server_2026-10-01.log").write_text("2026-10-01 08:00:00 === Start ===\n", encoding="utf-8")
        srv = Server(live, ip, port, root / "server_test.log")
        ok, code, log = srv.start(old_ver)
        ok_all &= check(ok, f"[{tag}] alter Server V{old_ver} startet", log[-400:])
        if not ok:
            return False
        # ---------- Daten über die API anlegen ----------
        try:
            rec = L.seed(srv.url, ADMIN, ADMIN_PW, scenario)
        except Exception as e:  # noqa: BLE001
            ok_all &= check(False, f"[{tag}] Testdaten über die API angelegt", f"{e.__class__.__name__}: {e}")
            return False
        check(True, f"[{tag}] Testdaten über die API angelegt ({len(rec['users'])} Benutzer, Funktionen {sorted(rec['features'])})")
        srv.stop()
        # alter Backup-Lauf (echte Backups + Zweitziel) und eigene Zusatzdateien in backups\
        r = run_py(live, ["Backup_Datenbank.py"])
        ok_all &= check(r.returncode in (0, 2), f"[{tag}] altes Backup-Skript legt Backups an", (r.stdout + r.stderr)[-300:])
        (live / "backups" / "von_hand_kopie.txt").write_text("manuelle Kopie\n", encoding="utf-8")
        ok_all &= check(any((live / "backups").glob("maschinenplanung_*.sqlite3")), f"[{tag}] backups\\ enthält Datenbanksicherungen")
        # ---------- 2. Fingerabdruck ----------
        dbA = L.dump_db(live / "data" / "maschinenplanung.sqlite3")
        keep_top = ("data",)
        treeA = L.tree_hashes(live, skip_top=keep_top)
        cfgA_dir = root / "cfgA"
        if (live / "config").exists():
            shutil.copytree(live / "config", cfgA_dir)
        had_cfg = (live / "config" / "firma.json").exists()
        stateA = next(iter(dbA["state"].values()))["json"]
        ok_all &= check(len(dbA["users"]) == len(rec["users"]) + 1, f"[{tag}] Fingerabdruck: {len(dbA['users'])} Benutzer, "
                        f"{len(stateA.get('workSteps', []))} Arbeitsschritte, {sum(len(v) for t, v in dbA.items() if t.startswith('chat_'))} Chat-Zeilen, "
                        f"{len(dbA.get('notifications', {}))} Benachrichtigungen")
        old_pkg = root / "old_pkg"
        old_pkg.mkdir()
        for p in live.iterdir():
            if p.is_file():
                shutil.copy2(p, old_pkg / p.name)

        # ---------- 3. Update ----------
        app, obsolete = L.package_lists((SRC / "MP_Common.ps1").read_text(encoding="utf-8-sig"))
        missing = [n for n in app if not (SRC / n).exists()]
        ok_all &= check(not missing, f"[{tag}] Updatepaket vollständig ({len(app)} Dateien)", ", ".join(missing))
        # Ein Altstand von V11 o. ä. soll Obsolete-Dateien enthalten dürfen
        (live / obsolete[0]).write_text("alt\n", encoding="utf-8") if obsolete else None
        treeA_pre_update = L.tree_hashes(live, skip_top=("data", "config"))
        simulate_update(live, SRC, app, obsolete)
        treeU = L.tree_hashes(live, skip_top=("data", "config"))
        changed = {k for k in set(treeU) | set(treeA_pre_update) if treeU.get(k) != treeA_pre_update.get(k)}
        outside = sorted(k for k in changed if k not in app and k not in obsolete)
        ok_all &= check(not outside, f"[{tag}] Update fasst nur $MP_AppFiles/$MP_ObsoleteFiles an", ", ".join(outside[:5]))

        # Bestand ohne Firmenprofil und ohne Firmen-CI: harter Stopp statt stiller neutraler Start
        expect_stop = (not had_cfg) and scenario == "noci"
        ok, code, log = srv.start(f"{CURRENT_VERSION}", 15 if expect_stop else 40)
        if expect_stop:
            ok_all &= check(not ok and code == 3 and "MP-CFG-006" in log, f"[{tag}] Bestand ohne firma.json/CI: Start gestoppt mit MP-CFG-006 (Exitcode {code})", log[-300:])
            srv.stop()
            dbS = L.dump_db(live / "data" / "maschinenplanung.sqlite3")
            ok_all &= check(not L.compare_dumps(dbA, dbS, exact=True), f"[{tag}] nach dem Stopp ist die Datenbank unverändert (Migration lief nicht an)")
            ok_all &= check(not (live / "config" / "firma.json").exists(), f"[{tag}] keine stille neutrale firma.json geschrieben")
            seed_file = SRC / "tools" / "legacy_employer_seed.json"
            template = json.loads(seed_file.read_text(encoding="utf-8-sig"))
            # Hilfsmittel wie Firma_Einrichten.ps1: MP_CONFIG_DIR = Live\config, server.py aus dem neuen Paket
            r = run_py(live, ["server.py", "--firma-einrichten", str(seed_file)], {"MP_CONFIG_DIR": str(live / "config")})
            ok_all &= check(r.returncode == 0 and (live / "config" / "firma.json").exists(), f"[{tag}] Firma_Einrichten legt firma.json aus der Vorlage an", r.stdout + r.stderr)
            r = run_py(live, ["server.py", "--firma-einrichten", str(seed_file)], {"MP_CONFIG_DIR": str(live / "config")})
            ok_all &= check(r.returncode == 4, f"[{tag}] Firma_Einrichten überschreibt eine vorhandene firma.json nie")
            ok, code, log = srv.start(f"{CURRENT_VERSION}")
        else:
            template = None
        ok_all &= check(ok, f"[{tag}] neuer Server V{CURRENT_VERSION} startet auf dem Bestand", log[-600:])
        if not ok:
            return False

        # ---------- 4. Prüfungen nach dem Update ----------
        problems = []
        for u in [{"username": ADMIN, "password": ADMIN_PW, "active": True}] + rec["users"]:
            a = L.Api(srv.url)
            code = a.login(u["username"], u["password"])
            want = 200 if u["active"] else 401
            if code != want:
                problems.append(f"{u['username']}: {code} (erwartet {want})")
        a = L.Api(srv.url)
        a.login("lead_cnc", rec["users"][1]["password"]) if rec["users"][1]["username"] == "lead_cnc" else None
        ok_all &= check(not problems, f"[{tag}] Benutzer und Passwörter funktionieren weiter (Login, deaktivierter bleibt gesperrt)", "; ".join(problems))
        wrong = L.Api(srv.url)
        ok_all &= check(wrong.login("viewer1", "falsch-falsch-1") == 401 and L.Api(srv.url).login("viewer1", "Pässwort-2-Ü#2026") == 401,
                        f"[{tag}] falsches und altes Passwort (vor der Änderung) werden abgewiesen")
        problems = L.check_company(srv.url, rec, template=template)
        ok_all &= check(not problems, f"[{tag}] Firmenprofil: Name, Farbe, Logo, Akzent wie vorher", "; ".join(problems))
        # V12.16.0: nach dem Update sind alle Module an, die Bereichs-Eigenschaften sind gesetzt, die Anzeige bleibt (Daten gleich).
        adm = L.Api(srv.url)
        adm.login(ADMIN, ADMIN_PW)
        sc, pc = adm.call("GET", "/api/config")
        mods = (pc or {}).get("modules") if sc == 200 and isinstance(pc, dict) else {}
        ok_all &= check(mods and all(mods.values()) and len(mods) >= 7, f"[{tag}] nach dem Update sind alle Module an", str(mods))
        sc, st = adm.call("GET", "/api/state")
        deps = {d["id"]: d for d in (st.get("data", {}).get("departments") or [])} if sc == 200 else {}
        want = {"thermoforming": "formats", "cnc": "sharedOperators"}
        miss = [f"{i}.{p}" for i, p in want.items() if i in deps and deps[i].get(p) is not True]
        ok_all &= check(sc == 200 and not miss, f"[{tag}] Bereichs-Eigenschaften für den Bestand gesetzt (formats, sharedOperators)", ", ".join(miss))
        cfg_path = live / "config" / "firma.json"
        ok_all &= check(cfg_path.exists(), f"[{tag}] config\\firma.json vorhanden")
        if scenario == "ci" or template:
            lf = json.loads(cfg_path.read_text(encoding="utf-8"))["company"]["logoFile"]
            ok_all &= check(lf and (live / "config" / lf).is_file(), f"[{tag}] Logo-Datei in config\\ vorhanden")
        if scenario == "ci":
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            ok_all &= check(cfg["company"]["name"] == L.CI_VALUE["company"] and cfg["company"]["color"].lower() == L.CI_VALUE["color"].lower(),
                            f"[{tag}] firma.json entstand aus dem Bestand (data.ci)", json.dumps(cfg["company"])[:200])
        if had_cfg:
            problems = config_additive(cfgA_dir, live)
            ok_all &= check(not problems, f"[{tag}] vorhandene config\\ nur ergänzt, nichts geändert", "; ".join(problems))
        srv.stop()
        dbB = L.dump_db(live / "data" / "maschinenplanung.sqlite3")
        problems = L.compare_dumps(dbA, dbB)
        ok_all &= check(not problems, f"[{tag}] alle Datensätze vollständig und gleich (nur neue Felder; {sum(len(v) for v in dbA.values())} Zeilen, {len(dbA)} Tabellen)",
                        "; ".join(problems[:6]))
        treeB = L.tree_hashes(live, skip_top=("data", "config"))
        diff = sorted(k for k in set(treeB) | set(treeU) if treeB.get(k) != treeU.get(k))
        ok_all &= check(not diff, f"[{tag}] der Serverstart schreibt außerhalb von data\\ und config\\ nichts", ", ".join(diff[:5]))
        same = [k for k in treeA if k not in app and k not in obsolete and not k.startswith("config/")]
        bad = [k for k in same if treeB.get(k) != treeA[k]]
        ok_all &= check(not bad and "LAN_CONFIG.json" in same and "BACKUP_ZIEL.txt" in same and any(k.startswith("backups/") for k in same),
                        f"[{tag}] LAN_CONFIG.json, BACKUP_ZIEL.txt, backups\\ ({sum(1 for k in same if k.startswith('backups/'))} Dateien) und logs byte-gleich", ", ".join(bad[:5]))
        cfgB = firma_files(live)

        # ---------- 5. Zweites Update ----------
        simulate_update(live, SRC, app, obsolete)
        ok, code, log = srv.start(CURRENT_VERSION)
        ok_all &= check(ok, f"[{tag}] zweites Update: Server startet", log[-400:])
        srv.stop()
        dbC = L.dump_db(live / "data" / "maschinenplanung.sqlite3")
        problems = L.compare_dumps(dbB, dbC, exact=True)
        ok_all &= check(not problems, f"[{tag}] zweites Update: Datenbankinhalt identisch", "; ".join(problems[:6]))
        cfgC = firma_files(live)
        ok_all &= check(cfgB == cfgC, f"[{tag}] zweites Update: config\\ byte-gleich ({len(cfgC)} Dateien)", f"{set(cfgB) ^ set(cfgC)}")
        treeC = L.tree_hashes(live, skip_top=("data",))
        ok_all &= check(all(treeC.get(k) == treeB.get(k) for k in treeB if k not in app), f"[{tag}] zweites Update: Dateien außerhalb des Pakets byte-gleich")

        # ---------- 6. Rollback auf den Paketstand davor ----------
        for p in old_pkg.iterdir():
            shutil.copy2(p, live / p.name)
        for n in app:
            if not (old_pkg / n).exists():
                (live / n).unlink(missing_ok=True)
        shutil.rmtree(live / "__pycache__", ignore_errors=True)
        ok, code, log = srv.start(old_ver)
        ok_all &= check(ok, f"[{tag}] Rollback auf V{old_ver}: alter Server startet mit den migrierten Daten", log[-600:])
        if ok:
            bad = []
            for u in [{"username": ADMIN, "password": ADMIN_PW, "active": True}] + rec["users"]:
                if L.Api(srv.url).login(u["username"], u["password"]) != (200 if u["active"] else 401):
                    bad.append(u["username"])
            ok_all &= check(not bad, f"[{tag}] Rollback: Logins funktionieren", ", ".join(bad))
            s, d = (lambda x: (x.login(ADMIN, ADMIN_PW), x.call("GET", "/api/state")))(L.Api(srv.url))[1]
            ok_all &= check(s == 200 and len(d["data"]["workSteps"]) == len(stateA["workSteps"]), f"[{tag}] Rollback: Planungsdaten lesbar")
        srv.stop()
        dbD = L.dump_db(live / "data" / "maschinenplanung.sqlite3")
        problems = L.compare_dumps(dbA, dbD)
        ok_all &= check(not problems, f"[{tag}] Rollback: Daten weiterhin vollständig (Bestand von vor dem Update)", "; ".join(problems[:6]))
        problems = L.compare_dumps(dbC, dbD)
        ok_all &= check(not problems, f"[{tag}] Rollback: auch die vom neuen Stand ergänzten Felder bleiben", "; ".join(problems[:6]))
        ok_all &= check(firma_files(live) == cfgC, f"[{tag}] Rollback: config\\ unverändert")
        treeR = L.tree_hashes(live, skip_top=("data", "config"))
        ok_all &= check(all(treeR.get(k) == treeA[k] for k in same), f"[{tag}] Rollback: LAN_CONFIG.json, BACKUP_ZIEL.txt, backups\\ byte-gleich")
        return ok_all
    finally:
        if srv:
            srv.stop()
        if keep:
            print(f"(Arbeitsordner behalten: {root})")
        else:
            shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="append", help="zusätzlicher/alleiniger Commit (mehrfach möglich)")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    ip = L.find_lan_ip()
    if not ip:
        check(not STRICT, "keine LAN-Adresse (nicht Loopback) gefunden – Test übersprungen" if not STRICT else "keine LAN-Adresse gefunden")
        return 0 if not STRICT else 1
    bases = [(f"Commit {c}", c) for c in args.baseline] if args.baseline else list(FIXED_BASELINES)
    if not args.baseline:
        prev = previous_version_commit()
        if prev and prev[1] not in [c[:7] for _, c in bases]:
            bases.append(prev)
    ran = 0
    for label, commit in bases:
        if not commit_exists(commit):
            check(not STRICT, f"Baseline {label} ({commit}) nicht im Repository (flache Kopie?) – übersprungen; CI nutzt fetch-depth: 0")
            continue
        for scenario in ("ci", "noci"):
            ran += 1
            run_case(label, commit, scenario, args.keep, ip)
    check(ran > 0 or not STRICT, f"mindestens eine Baseline getestet ({ran} Läufe)")
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} bestanden")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

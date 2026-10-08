#!/usr/bin/env python3
"""V12.27.0 (#73 Phase 1 Vorbereitung): Updater und Skripte können Programmdateien in EINER Unterordnerebene (core/x.py).

Geprüft wird die ganze Kette ohne verschobenen Code:
  - tools/build_release.py baut ein Paket mit core/x.py, app_updates prüft und entpackt es in den Unterordner,
  - Sicherung/Update/Rollback (Nachbildung von UPDATE_LIVE.ps1 aus tests/test_deploy_upgrade.py) nehmen Unterordner mit
    und löschen nichts Fremdes,
  - böswillige Namen (.., absolute Pfade, Laufwerk, Backslash, zwei Ebenen, Gerätenamen, Groß/klein-Doppel) werden abgewiesen,
  - die PowerShell-Skripte nutzen dieselbe Regel (Get-MPAppPath/Copy-MPAppFile) statt flacher Kopien,
  - das ausgelieferte Paket bleibt flach (noch keine Unterordner-Datei in $MP_AppFiles).

Aufruf:  python tests/test_update_subfolders.py
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC / "tools"))
import app_updates as U  # noqa: E402
import build_release  # noqa: E402
import test_deploy_upgrade as D  # noqa: E402
import deploy_lib as L  # noqa: E402

RESULTS: list[bool] = []
V = "12.99.0"
REQUIRED = {"server.py": f'APP_VERSION = "{V}"\n', "index.html": f"const CLIENT_VERSION='{V}';", "release_gates.py": "", "MP_Common.ps1": "",
            "UPDATE_LIVE.ps1": "", "app_updates.py": ""}


def check(cond, label, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""), flush=True)


def rejects(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def make_package(files: dict[str, str], manifest_files: dict[str, str] | None = None, raw_names: dict[str, str] | None = None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in files.items():
            z.writestr(k, v)
        for k, v in (raw_names or {}).items():
            z.writestr(zipfile.ZipInfo(k), v)
    blob = buf.getvalue()
    digests = manifest_files if manifest_files is not None else {k: hashlib.sha256(v.encode()).hexdigest() for k, v in {**files, **(raw_names or {})}.items()}
    manifest = {"schema": 1, "version": V, "minVersion": "12.19.0", "commit": "a" * 40, "sha256": hashlib.sha256(blob).hexdigest(), "files": digests}
    return blob, manifest


def tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


tmp = Path(tempfile.mkdtemp(prefix="mp-subdir-"))
try:
    # ---------- 1. Gültiges Paket mit core/x.py ----------
    files = {**REQUIRED, "core/x.py": "VALUE = 1\n", "core/__init__.py": ""}
    blob, manifest = make_package(files)
    U.validate_manifest(manifest, V, "12.27.0")
    check(True, "Manifest mit core/x.py und core/__init__.py ist gültig")
    pkg = U.unpack_package(blob, manifest, tmp / "staging" / "job1" / "package")
    check((pkg / "core" / "x.py").read_text(encoding="utf-8") == "VALUE = 1\n" and (pkg / "server.py").is_file(),
          "unpack_package legt core\\ an und schreibt core/x.py hinein")
    check(sorted(tree(pkg)) == sorted(files) and not (tmp / "staging" / "job1" / "package.zip").exists(),
          "entpackt genau die Manifest-Dateien, temporäres ZIP entfernt")

    # ---------- 2. Böswillige oder unzulässige Namen im Manifest ----------
    bad_names = ["../x.py", "core/../../x", "C:/x", "C:x.py", "core\\x.py", "a/b/c.py", "/x.py", "core/", "/core/x.py", "core//x.py", "",
                 ".git/x.py", "core/.hidden", ".env", "core/x.", "x.", "CON", "core/nul.py", "aux.txt", "LPT1/x.py", "co re/x.py", "core/x.py\n"]
    leaked = []
    for name in bad_names:
        m = json.loads(json.dumps(manifest))
        m["files"][name] = "0" * 64
        if not rejects(lambda: U.validate_manifest(m, V, "12.27.0")):
            leaked.append(repr(name))
    check(not leaked, f"validate_manifest weist {len(bad_names)} böswillige Namen ab (.., absolut, Laufwerk, Backslash, zwei Ebenen, Punkt, Gerätename)",
          ", ".join(leaked))
    for name in bad_names:
        if name and not rejects(lambda: U.member_name(name)):
            leaked.append(repr(name))
    check(not leaked, "member_name (auch von build_release genutzt) weist dieselben Namen ab", ", ".join(leaked))
    m = json.loads(json.dumps(manifest))
    m["files"]["Core/x.py"] = "0" * 64
    check(rejects(lambda: U.validate_manifest(m, V, "12.27.0")), "Groß/klein-Doppel (core/x.py + Core/x.py) wird abgewiesen (Windows)")
    m = json.loads(json.dumps(manifest))
    m["files"]["core"] = "0" * 64
    check(rejects(lambda: U.validate_manifest(m, V, "12.27.0")), "Datei und Ordner mit gleichem Namen (core + core/x.py) werden abgewiesen")

    # ---------- 3. unpack_package bleibt hart, auch wenn ein Manifest an der Prüfung vorbeikäme ----------
    def unpack(b, mf, name):
        return lambda: U.unpack_package(b, mf, tmp / "staging" / name / "package")
    outside = tmp / "staging" / "j2" / "evil.py"
    b2, m2 = make_package({**REQUIRED, "../evil.py": "x"})
    check(rejects(unpack(b2, m2, "j2")) and not outside.exists(), "ZIP-Eintrag ../evil.py: abgewiesen, nichts außerhalb geschrieben")
    b3, m3 = make_package(REQUIRED, raw_names={"core\\x.py": "x"})
    check(rejects(unpack(b3, m3, "j3")) and not list((tmp / "staging" / "j3").rglob("*x.py")), "ZIP-Eintrag mit Backslash: abgewiesen")
    b4, m4 = make_package({**REQUIRED, "a/b/c.py": "x"})
    check(rejects(unpack(b4, m4, "j4")) and not (tmp / "staging" / "j4" / "package" / "a").exists(), "ZIP-Eintrag mit zwei Ebenen: abgewiesen")
    b5, m5 = make_package({**REQUIRED, "core/x.py": "x"}, raw_names={"core/": ""})
    check(rejects(unpack(b5, m5, "j5")), "Verzeichniseintrag core/ im ZIP: abgewiesen")
    b6, m6 = make_package({**REQUIRED, "core/x.py": "x"})
    m6["files"]["core/x.py"] = "0" * 64
    check(rejects(unpack(b6, m6, "j6")), "falsche Prüfsumme einer Unterordner-Datei: abgewiesen")
    b7, m7 = make_package({**REQUIRED, "core/x.py": "x"})
    m7["sha256"] = "0" * 64
    check(rejects(unpack(b7, m7, "j7")) and not (tmp / "staging" / "j7").exists(), "falsche Paket-SHA256: abgewiesen, bevor etwas entpackt wird")
    b8, m8 = make_package({**REQUIRED, "C:/x.py": "x"})
    check(rejects(unpack(b8, m8, "j8")), "ZIP-Eintrag C:/x.py: abgewiesen")

    # ---------- 4. build_release -> validate -> unpack mit Unterordner ----------
    root = tmp / "repo"
    (root / "core").mkdir(parents=True)
    for k, v in {**REQUIRED, "core/x.py": "VALUE = 2\n"}.items():
        (root / k).write_text(v, encoding="utf-8")
    names = ", ".join(f"'{k}'" for k in [*REQUIRED, "core/x.py"])
    (root / "MP_Common.ps1").write_text(f"$MP_AppFiles = @(\n    {names}\n)\n", encoding="utf-8")
    built = build_release.build(root, tmp / "out", "b" * 40)
    with zipfile.ZipFile(tmp / "out" / f"produktionsplanung-v{V}.zip") as z:
        zipnames = z.namelist()
    check("core/x.py" in built["files"] and "core/x.py" in zipnames, "build_release: core/x.py steht mit '/' in Manifest und ZIP")
    U.validate_manifest(built, V, "12.27.0")
    pkg2 = U.unpack_package((tmp / "out" / f"produktionsplanung-v{V}.zip").read_bytes(), built, tmp / "staging" / "built" / "package")
    check((pkg2 / "core" / "x.py").read_text(encoding="utf-8") == "VALUE = 2\n", "gebautes Paket wird geprüft und in core\\ entpackt")
    (root / "MP_Common.ps1").write_text(f"$MP_AppFiles = @(\n    {names}, '../evil.py'\n)\n", encoding="utf-8")
    check(rejects(lambda: build_release.build(root, tmp / "out2", "b" * 40)), "build_release verweigert unzulässige Namen in $MP_AppFiles")

    # ---------- 5. Sicherung, Update, Rollback (Nachbildung UPDATE_LIVE.ps1) ----------
    def install(dirname: str, app: dict[str, str]) -> Path:
        d = tmp / dirname
        for k, v in app.items():
            p = d.joinpath(*k.split("/"))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(v, encoding="utf-8")
        return d

    # a) alter Stand flach, neuer Stand bringt core/x.py; Rollback entfernt core\ wieder
    old_app = {"server.py": "old", "index.html": "old"}
    new_app = {"server.py": "new", "index.html": "new", "core/x.py": "new-x"}
    live = install("live_a", old_app)
    for k, v in {"LAN_CONFIG.json": "{}", "backups/b.sqlite3": "B", "config/firma.json": "{}", "logs/s.log": "L"}.items():
        p = live.joinpath(*k.split("/")); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(v, encoding="utf-8")
    new = install("new_a", new_app)
    before = tree(live)
    rb = tmp / "rb_a"
    D.backup_code(live, rb, list(old_app), list(new_app))
    D.simulate_update(live, new, list(new_app), [])
    check((live / "core" / "x.py").read_text(encoding="utf-8") == "new-x", "Update: core/x.py landet im Live-Ordner (Unterordner angelegt)")
    D.rollback_code(rb, live, list(new_app))
    check(tree(live) == before and not (live / "core").exists(), "Rollback flach <- core: alter Stand exakt, leerer core\\ entfernt, Fremddateien unberührt")

    # b) alter Stand hat core/x.py, neuer Stand verschiebt nach core2/y.py; fremde Datei in core\ bleibt
    old_app = {"server.py": "old", "core/x.py": "old-x"}
    new_app = {"server.py": "new", "core2/y.py": "new-y"}
    live = install("live_b", old_app)
    (live / "core" / "eigene_notiz.txt").write_text("bleibt", encoding="utf-8")
    (live / "LAN_CONFIG.json").write_text("{}", encoding="utf-8")
    new = install("new_b", new_app)
    before = tree(live)
    rb = tmp / "rb_b"
    D.backup_code(live, rb, list(old_app), list(new_app))
    check(tree(rb).get("core/x.py") and "core/eigene_notiz.txt" not in tree(rb), "Sicherung enthält alte Unterordner-Programmdateien (alte Dateiliste), nur Programmdateien")
    D.simulate_update(live, new, list(new_app), [])
    (live / "core" / "x.py").write_text("vom Update verändert", encoding="utf-8")
    D.rollback_code(rb, live, list(new_app))
    check(tree(live) == before and not (live / "core2").exists(), "Rollback core2 -> core: core/x.py zurück, core2\\ entfernt, eigene Datei in core\\ bleibt")

    # c) neuer Ordner mit fremder Datei wird beim Rollback NICHT gelöscht
    live = install("live_c", {"server.py": "old"})
    new = install("new_c", {"server.py": "new", "core/x.py": "x"})
    rb = tmp / "rb_c"
    D.backup_code(live, rb, ["server.py"], ["server.py", "core/x.py"])
    D.simulate_update(live, new, ["server.py", "core/x.py"], [])
    (live / "core" / "fremd.txt").write_text("f", encoding="utf-8")
    D.rollback_code(rb, live, ["server.py", "core/x.py"])
    check(not (live / "core" / "x.py").exists() and (live / "core" / "fremd.txt").is_file(), "Rollback löscht keine fremden Dateien (nicht leerer Ordner bleibt)")
    check(rejects(lambda: D.copy_app_file(new, live, "../x.py")) and not (tmp / "x.py").exists(), "Kopierhelfer der Testnachbildung weist ../x.py ab")

    # ---------- 6. PowerShell: gleiche Regel, keine flachen Kopien mehr ----------
    common = (SRC / "MP_Common.ps1").read_text(encoding="utf-8-sig")
    ps_rx = re.search(r"\$Name -cnotmatch '([^']+)'", common)
    check(ps_rx is not None and ps_rx.group(1) == "^" + U.MEMBER.pattern + "$", "Test-MPAppName nutzt dasselbe Muster wie app_updates.MEMBER",
          ps_rx.group(1) if ps_rx else "fehlt")
    ps_dev = re.search(r"\$part -match '\^\(([^)]+)\)", common)
    check(ps_dev is not None and ps_dev.group(1) in U.WINDOWS_RESERVED.pattern and "StartsWith('.')" in common and "EndsWith('.')" in common,
          "Test-MPAppName weist wie Python Punkt-Teile und Gerätenamen ab")
    scripts = {n: (SRC / n).read_text(encoding="utf-8-sig") for n in ("MP_Common.ps1", "Setup_Windows.ps1", "UPDATE_LIVE.ps1", "tests/ci_windows_deploy.ps1")}
    flat = [n for n, t in scripts.items() for line in t.splitlines() if re.search(r"\$MP_AppFiles\)", line) and "Copy-Item" in line]
    check(not flat, "keine flache Copy-Item-Schleife über $MP_AppFiles mehr (MP_Common, Setup, UPDATE_LIVE, CI-Deploy)", ", ".join(flat))
    uses = {n: t.count("Copy-MPAppFile") for n, t in scripts.items()}
    check(uses["Setup_Windows.ps1"] >= 1 and uses["UPDATE_LIVE.ps1"] >= 3 and uses["tests/ci_windows_deploy.ps1"] == 2 and "foreach ($name in $MP_AppFiles) { Copy-MPAppFile $NewSource $dir $name }" in common,
          "Setup, Update, Vorabtest, Rollback und CI-Deploy kopieren über Copy-MPAppFile", str(uses))
    upd = scripts["UPDATE_LIVE.ps1"]
    step5 = upd[upd.index("5/9 Bisherigen Programmstand sichern"):upd.index("6/9 Dateien")]
    check("Get-MPAppFileList $OldBase" in step5 and "Contains('/')" in step5, "Sicherung (Schritt 5) nimmt Unterordner-Dateien der alten und neuen Liste mit")
    rollback = upd[upd.index("Rollback wird ausgefuehrt"):]
    check("-Directory | Where-Object { $_.Name -ne $MP_ConfigDir }" in rollback and "Get-MPAppPath $RollbackCode $name" in rollback,
          "Rollback spielt Unterordner (außer config) zurück und entfernt nur Paketdateien ohne Sicherung")
    check("New-Item -ItemType Directory -Path $parent -Force" in common, "Copy-MPAppFile legt den Zielordner an")

    # ---------- 7. Ausgeliefertes Paket bleibt flach ----------
    app, obsolete = L.package_lists(common)
    check(app and all("/" not in n and "\\" not in n for n in app), f"$MP_AppFiles bleibt flach ({len(app)} Dateien, noch nichts verschoben)")
    bad = [n for n in app + obsolete if rejects(lambda n=n: U.member_name(n))]
    check(not bad, "alle heutigen Paket- und Obsolet-Namen erfüllen die neue Regel", ", ".join(bad))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(RESULTS)}/{len(RESULTS)} bestanden")
sys.exit(0 if all(RESULTS) else 1)

#!/usr/bin/env python3
"""Release-Werkzeug: Release-Text = nur der oberste Abschnitt der RELEASE_NOTES, Asset-Prüfung erkennt Abweichungen.

Aufruf:  python tests/test_release_tools.py
"""
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import build_release  # noqa: E402

results = []


def check(ok, label):
    results.append(bool(ok))
    print(('PASS ' if ok else 'FAIL ') + label)


notes = build_release.top_notes((ROOT / 'RELEASE_NOTES.txt').read_text(encoding='utf-8'))
check(notes.startswith('MASCHINENPLANUNG V') and notes.count('MASCHINENPLANUNG V') == 1, 'Release-Text enthält genau einen Versionsabschnitt')
check(len(notes.encode()) < 20000, f'Release-Text ist kurz ({len(notes.encode())} Bytes)')
check(build_release.top_notes('MASCHINENPLANUNG V2\n- a\n\nMASCHINENPLANUNG V1\n- b\n') == 'MASCHINENPLANUNG V2\n- a\n', 'Schnitt vor dem nächsten Abschnitt')

commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp) / 'out'
    build_release.build(ROOT, out, commit)
    check(build_release.verify(out, commit)['commit'] == commit, 'Frisch gebautes Paket besteht die Prüfung')

    def rejected(label, mutate):
        build_release.build(ROOT, out, commit)
        mutate()
        try:
            build_release.verify(out, commit)
            check(False, label)
        except ValueError:
            check(True, label)

    manifest = out / 'update-manifest.json'
    rejected('Fremde Datei im Asset-Ordner wird abgewiesen', lambda: (out / 'RELEASE_NOTES.txt').write_text('x'))
    (out / 'RELEASE_NOTES.txt').unlink(missing_ok=True)
    try:
        build_release.verify(out, '0' * 40)
        check(False, 'Falscher Commit wird abgewiesen')
    except ValueError:
        check(True, 'Falscher Commit wird abgewiesen')

    def tamper_hash():
        m = json.loads(manifest.read_text(encoding='utf-8'))
        m['files']['index.html'] = '0' * 64
        manifest.write_text(json.dumps(m), encoding='utf-8')
    rejected('Geänderter Datei-Hash im Manifest wird abgewiesen', tamper_hash)

    def extra_member():
        m = json.loads(manifest.read_text(encoding='utf-8'))
        archive = out / ('produktionsplanung-v' + m['version'] + '.zip')
        with zipfile.ZipFile(archive, 'a') as z:
            z.writestr('extra.py', 'x')
    rejected('Zusätzliche Datei im Paket wird abgewiesen', extra_member)

print(f'\n{sum(results)}/{len(results)} bestanden')
sys.exit(0 if all(results) else 1)

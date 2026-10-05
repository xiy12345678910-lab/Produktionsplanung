# Arbeitsanweisung für Feature-Agents (ab V12.12.0)

Arbeitsverzeichnis `/home/user/Produktionsplanung`, Branch `claude/new-session-95ro2l` (bereits ausgecheckt).
Nur auf diesen Branch committen und pushen (`git push origin claude/new-session-95ro2l`), **keinen PR** anlegen.
Vor dem Start: `git pull --ff-only origin claude/new-session-95ro2l`.

## Zuerst lesen
1. `docs/FEATURE_PROMPTS.md`: den gemeinsamen Vorspann und **deinen** Prompt; der Vorspann gilt vollständig.
2. `docs/UEBERGABE.md`, `docs/AUDIT.md`, die obersten Abschnitte von `RELEASE_NOTES.txt` und `FEHLERCODES.txt`.

## UX-Regel (Vorgabe des Nutzers, wichtiger als Vollständigkeit)
Die Bedienung muss ohne Anleitung klar sein:
- wenig Text, eindeutige Symbole mit Tooltip und aria-label, sinnvolle Defaults;
- die Aktion steht dort, wo der Nutzer gerade arbeitet;
- keine Erklärblöcke; Meldungen höchstens ein Satz.

## Projektbesonderheiten (schon vorhanden, bitte nutzen)
- **Client-JS läuft in einer IIFE.** Tests kommen nicht direkt an interne Funktionen. Muster dafür ist
  `installHook()` in `tests/e2e_v12102.mjs`: Playwright schleust vor `})();` den Ausdruck
  `window.__t=src=>eval(src)` ein und entfernt die CSP nur in der Testantwort. Danach liefert
  `ev(page, () => internalFn())` Werte aus dem IIFE-Scope. Im Produktivcode keine Test-Hintertüren.
- **Testdaten / Server im Test:** Vorlage ist der Seed-Block in `tests/e2e_roles.mjs`
  (`server.validate_state` vor dem Schreiben). Jede neue E2E-Datei bekommt einen **eigenen PORT**.
  Belegt sind 18777, 18789, 18791, 18795, 18797; außerdem die Ports, die die übrigen `tests/e2e_*.mjs` setzen.
- **Darstellung (V12.11.0):**
  - Alle Farben sind CSS-Variablen je Rolle: `--t<hex>` Text, `--b<hex>` Fläche, `--l<hex>` Linie.
  - Hell steht in `:root`; Dunkel und Halle stehen im Block `@media screen{ :root[data-theme="dark"]{…} :root[data-theme="hall"]{…} }`
    am Ende des `<style>`.
  - Neue Farben bevorzugt über vorhandene Variablen (`--text --muted --line --panel --panel2 --bg --brand --ok --warn --danger`,
    als Text `var(--ok-t)` usw.). Eine neue Hex-Farbe braucht ein Token mit Werten für Dunkel und Halle.
  - Schriftgrößen immer `font-size:max(var(--fsMin),calc(Npx*var(--fs)))`.
  - `node tests/e2e_theme.mjs` muss grün bleiben (Kontrast Dunkel ≥ 4,5, Halle ≥ 7, Halle ≥ 14 px).
- **Undo/Redo (V12.12.0):** `UNDO_COLLS` im Client. Neue, vom Nutzer geplante Datensatz-Sammlungen mit `id`
  dort aufnehmen, wenn Rückgängig sinnvoll ist.
- **Server:** nur Python-Standardbibliothek. Neue Felder bekommen eine idempotente Migration (Muster `migrate_state_v1280`),
  eine Prüfung in `validate_state` und Rechte in der passenden `*_change_allowed`-Funktion.
  Neue Endpunkte sitzen in `_do_GET/_do_POST/...`: Die Hülle prüft Host/Origin und fängt Exceptions ab (500).
  Für POST außer Login/Logout/Passwort muss eine Sitzung bestehen.

## Datenerhalt bei Updates (Regel, ab V12.15.1)
- Updates ändern nur Code. Nichts außerhalb von `$MP_AppFiles` (`MP_Common.ps1`) schreiben; `config\`, `data\`, `backups\`, `LAN_CONFIG.json`, `BACKUP_ZIEL.txt` gehören dem Betrieb.
- Datenänderungen nur als additive, idempotente Migration. Nie Felder löschen oder umdeuten; leere Felder füllen ist erlaubt, Vorhandenes bleibt.
- Ein Bestand (Revision > 1) darf nie still mit anderen Werten starten (Beispiel: `MP-CFG-006`, Firmenprofil fehlt).
- `python3 tests/test_deploy_upgrade.py` muss grün sein, sonst kein Commit. Neue Datensammlungen/Dateien werden dort automatisch mitgeprüft (feldweiser Vergleich); neue Paketdateien nur über `$MP_AppFiles`.
- CI-Job `windows-deploy` (`tests/ci_windows_deploy.ps1`) prüft das echte Deploy; nach dem Push den Lauf ansehen.

## Version und Doku (Pflicht)
- `APP_VERSION` in `server.py`, `CLIENT_VERSION` und alle `V12.x.y` in `index.html` (sed über `V<alt>`),
  `README.md` Zeile 3, erste Zeile von `README_Windows.txt`, `FEHLERCODES.txt` und `BENUTZER_KURZANLEITUNG.txt`.
  Prüfung: `python3 tests/test_version.py`.
- Neuer Abschnitt oben in `RELEASE_NOTES.txt`, Zeile in der Tabelle von `docs/UEBERGABE.md`, Status in der To-do-Tabelle
  von `docs/FEATURE_PROMPTS.md`, neue Codes in `FEHLERCODES.txt`, für Nutzer eine Zeile in `BENUTZER_KURZANLEITUNG.txt`.
- Dateien mit CRLF bzw. BOM (einige `.ps1`) im vorhandenen Format lassen.

## Tests (alle grün, Zahlen berichten)
```
python3 -m py_compile server.py
python3 tests/test_version.py
python3 tests/test_v128.py && python3 tests/test_v129.py && python3 tests/test_v12102.py && python3 tests/test_backup.py
python3 tests/test_notifications.py && python3 tests/test_firma_config.py && python3 tests/test_config_update.py && python3 tests/test_config_api.py && python3 tests/test_no_employer_data.py
python3 tests/test_deploy_upgrade.py
d=$(mktemp -d) && python3 tests/make_test_db.py $d >/dev/null && python3 tests/test_regression.py $d | tail -1
for f in tests/ui_smoke.mjs tests/e2e_*.mjs; do echo -n "$f: "; node $f 2>&1 | tail -1; done
pwsh -NoProfile -File tests/ps_syntax.ps1 | tail -1      # nur falls .ps1 geändert
```
JS-Syntax: `python3 -c "import re,subprocess;s=open('index.html').read();[ (open(f'/tmp/b{i}.js','w').write(b), print(subprocess.run(['node','--check',f'/tmp/b{i}.js'],capture_output=True,text=True).stderr)) for i,b in enumerate(re.findall(r'<script[^>]*>(.*?)</script>',s,re.S))]"`
Neue Tests: `tests/e2e_<feature>.mjs` (läuft automatisch in CI über `tests/e2e_*.mjs`) und bei Serverlogik
`tests/test_<feature>.py`. Den neuen Python-Test zusätzlich in `.github/workflows/ci.yml` (Job python) eintragen.

## Commit
Commit-Nachricht mit Versionspräfix, z. B. `V12.13.0: Benachrichtigungen`. Sie endet mit genau diesen Zeilen:
```
Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WjF2Zo3XdceSfrSTfpUKXx
```
Danach pushen. Kein Modellname in Code, Commits oder Doku.

## Bericht an den Auftraggeber (am Ende, kurz)
Was umgesetzt ist, was bewusst nicht (mit Grund), Testzahlen je Suite, Commit-Hash.

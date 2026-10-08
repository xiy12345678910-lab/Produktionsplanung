# Release-Regeln

Gilt für `.github/workflows/release.yml` (Stand 09.10.2026).

## 1. Wie ein Release entsteht

Ein Release entsteht nur über den Workflow „Release package“. Von Hand wird kein Release und kein Tag angelegt.

- GitHub → Actions → „Release package“ → „Run workflow“, Branch **main**. Der Workflow hat keine Eingabefelder.
- Ergebnis: **Entwurf** `v<Version>` mit ZIP und `update-manifest.json`.
- Ein Tag-Push startet seit 09.10.2026 keinen Release mehr (Entscheidung Jonas).

## 2. Vorbedingungen (vom Workflow geprüft)

1. Lauf auf `main`, sonst Abbruch („Releases must be built on main“).
2. Der Commit ist in `origin/main` enthalten.
3. Für genau diesen Commit gibt es einen erfolgreichen CI-Lauf „CI“ (Push auf main), sonst Abbruch.
4. `python tests/test_version.py` ist grün: alle Versionsangaben stimmen mit `APP_VERSION` in `server.py` überein.
5. `tools/build_release.py` baut ZIP (Programmdateien laut `MP_Common.ps1`) und Manifest (Version, Commit, SHA256)
   und prüft beides danach erneut: genau zwei Dateien, Hashes und Commit stimmen, ZIP-Inhalt = Manifest.
6. Gibt es `v<Version>` schon (auch als Entwurf), bricht der Lauf ab. Vorher die Version erhöhen.

Release-Text ist nur der oberste Abschnitt aus `RELEASE_NOTES.txt` (die aktuelle Version), nicht die ganze Datei.

## 3. Entwurf und Veröffentlichung

- Der Lauf erzeugt nur einen **Entwurf**. Veröffentlicht wird von Jonas per Klick auf „Publish release“.
  Erst dabei legt GitHub den Tag `vX.Y.Z` an.
- Entwürfe sind für den Updater unsichtbar, weil er nur veröffentlichte Releases lädt.
- Keine Tags und keine Releases von Hand anlegen. Am 08.10. entstand so der Tag „v12,24,0“ (mit Kommas) und ein Release ohne Dateien.
- Fehler: Entwurf löschen und Lauf neu starten, statt den Entwurf von Hand zu ändern.

## 4. Installation auf Windows

Normalweg: Der Admin klickt in der Oberfläche unter **System → Softwareupdate** auf „Update installieren“.
Der Server lädt das veröffentlichte Release, prüft Manifest und Hashes und installiert mit `UPDATE_LIVE.ps1`
(Vorabtest, Sicherung, Rollback bei Fehler).

Ersatzweg nach `README_Windows.txt`, Abschnitt 2b, in einer normalen PowerShell (kein Administrator):

    powershell -ExecutionPolicy Bypass -File C:\ProgramData\Maschinenplanung\Update_von_GitHub.ps1 -Tag vX.Y.Z

- Immer mit `-Tag` die freigegebene Version angeben. Der Commit des Release muss in main liegen.
- Installation nicht per Remote Desktop.

Wichtig für Umbauten am Updater selbst: Geprüft wird ein Paket vom **installierten** `app_updates.py`.
Neue Paketregeln (z. B. Dateien in Unterordnern) wirken also erst ab dem Update, das nach dem Release mit der neuen Regel installiert wird.

## 5. Empfohlene GitHub-Einstellungen

**Empfehlung – noch nicht eingestellt.** Jonas stellt das selbst ein.

- **Tag-Schutzregel** (Settings → Rules → Rulesets, Target „Tag“) für `v*`: Tags nur durch Admins und den Release-Workflow.
  Vorsicht: Beim Veröffentlichen des Entwurfs legt GitHub den Tag an. Die Regel darf das nicht blockieren. Vor der Einstellung an einem Probe-Entwurf prüfen.
- **Branch-Schutz für main** (Settings → Rules → Rulesets, Target „Branch“): Pull Request Pflicht, Statusprüfung „CI“ muss grün sein, kein Force-Push, keine Umgehung.

## 6. Checkliste vor „Publish release“

- [ ] Titel „Produktionsplanung vX.Y.Z“, Tag `vX.Y.Z` (ohne Kommas oder Leerzeichen).
- [ ] Version = `APP_VERSION` in `server.py` = oberster Eintrag in `RELEASE_NOTES.txt`.
- [ ] Assets vorhanden: `produktionsplanung-vX.Y.Z.zip` und `update-manifest.json`.
- [ ] Der Lauf war auf main und grün; der Commit des Entwurfs ist der aktuelle main-Kopf.
- [ ] Der Release-Text ist der oberste Abschnitt von `RELEASE_NOTES.txt` und beschreibt diese Version.
- [ ] SHA256 aus dem Manifest notiert (für `Update_von_GitHub.ps1 -Sha256`, falls nötig).

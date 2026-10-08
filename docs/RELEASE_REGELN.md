# Release-Regeln

Gilt für `.github/workflows/release.yml` (Stand 08.10.2026, Version V12.27.0).

## 1. Wie ein Release entsteht

Ein Release entsteht nur über den Workflow „Release package“. Von Hand wird kein Release und kein Tag angelegt.

- **Start A (Entwurf):** GitHub → Actions → „Release package“ → „Run workflow“, Branch **main**.
  Der Workflow hat keine Eingabefelder (keine `inputs`). Ergebnis: Entwurf `v<Version>` mit ZIP und `update-manifest.json`.
- **Start B (Tag):** Push eines Tags `v*`. Ergebnis: sofort veröffentlicht, ohne Entwurf (siehe Abschnitt 3).

## 2. Vorbedingungen (vom Workflow geprüft)

1. Bei manuellem Start: Lauf auf `main`, sonst Abbruch („Releases must be built on main“).
2. Der Commit ist in `origin/main` enthalten.
3. Für genau diesen Commit gibt es einen erfolgreichen CI-Lauf „CI“ (Push auf main), sonst Abbruch.
4. `python tests/test_version.py` ist grün: alle Versionsangaben stimmen mit `APP_VERSION` in `server.py` überein.
5. `tools/build_release.py` baut ZIP (Programmdateien laut `MP_Common.ps1`) und Manifest (Version, Commit, SHA256).
6. Bei Tag-Start: Tagname muss exakt `v<APP_VERSION>` sein, sonst Abbruch.

Bei manuellem Start wird der Tagname nicht gegen die Version geprüft.

## 3. Entwurf und Veröffentlichung

- Ein manueller Lauf erzeugt nur einen **Entwurf**. Veröffentlicht wird von Jonas per Klick auf „Publish release“.
- Ein Tag-Push veröffentlicht dagegen direkt. Deshalb keine Tags pushen, ohne vorher die Checkliste (Abschnitt 6) geprüft zu haben.
- Entwürfe sind für den Updater unsichtbar, weil er nur veröffentlichte Releases lädt.
- Keine Tags und keine Releases von Hand anlegen. Am 08.10. entstand so der Tag „v12,24,0“ (mit Kommas) und ein Release ohne Dateien.
- Fehler: Lauf neu starten statt den Entwurf von Hand zu ändern.

## 4. Installation auf Windows

Nur nach `README_Windows.txt`, Abschnitt 2b. Befehl in einer normalen PowerShell (kein Administrator):

    powershell -ExecutionPolicy Bypass -File C:\ProgramData\Maschinenplanung\Update_von_GitHub.ps1 -Tag vX.Y.Z

- Immer mit `-Tag` die freigegebene Version angeben. Der Commit des Release muss in main liegen.
- Ohne `-Tag` nimmt der Updater das neueste veröffentlichte Release.
- Installation nicht per Remote Desktop, sondern am Server selbst wie in 2b beschrieben.

## 5. Empfohlene GitHub-Einstellungen

**Empfehlung – noch nicht eingestellt.** Jonas stellt das selbst ein.

- **Tag-Schutzregel** (Settings → Rules → Rulesets, Target „Tag“) für `v*`: Tags nur durch Admins und den Release-Workflow.
  Vorsicht: Der Workflow legt den Tag beim Veröffentlichen selbst an. Die Regel darf ihn nicht blockieren. Vor der Einstellung an einem Probetag prüfen.
- **Branch-Schutz für main** (Settings → Rules → Rulesets, Target „Branch“): Pull Request Pflicht, Statusprüfung „CI“ muss grün sein, kein Force-Push, keine Umgehung.

## 6. Checkliste vor „Publish release“

- [ ] Titel „Produktionsplanung vX.Y.Z“, Tag `vX.Y.Z` (ohne Kommas oder Leerzeichen).
- [ ] Version = `APP_VERSION` in `server.py` = oberster Eintrag in `RELEASE_NOTES.txt`.
- [ ] Assets vorhanden: `produktionsplanung-vX.Y.Z.zip` und `update-manifest.json`.
- [ ] Der Lauf war auf main und grün; der Commit des Entwurfs ist der aktuelle main-Kopf.
- [ ] Die Release-Notizen sind der komplette Inhalt von `RELEASE_NOTES.txt`; oben prüfen, ob der neueste Stand steht.
- [ ] SHA256 aus dem Manifest notiert (für `Update_von_GitHub.ps1 -Sha256`, falls nötig).

# Umzug GitHub → Gitea (Vorbereitung)

Stand: 12.10.2026, V12.28.4. Ziel: Neustart auf einem eigenen Gitea-Server. GitHub wird nach dem Umzug gelöscht.

> **Wichtigste Regel:** GitHub erst löschen, wenn **jede** Kundeninstallation eine Version hat, die Updates von
> Gitea lädt. Bis einschließlich V12.28.4 ist GitHub fest eingebaut. Ohne Brückenversion fällt nach dem Löschen
> der Button „Update installieren“ überall aus, ebenso `Update_von_GitHub.ps1`.
> Updates sind dann nur noch per Hand möglich (Abschnitt 5).

## 1. Was heute an GitHub hängt

| Stelle | Kopplung | Folge nach dem Löschen |
|---|---|---|
| `app_updates.py` (läuft beim Kunden, prüft stündlich) | `https://api.github.com/repos/…`: Release, Commit, Vergleich mit `main`, Assets; Weiterleitungen nur zu `*.github.com`/`githubusercontent.com` | Prüfung schlägt fehl, Button meldet einen Fehler. Die Daten sind nicht betroffen. |
| `Update_von_GitHub.ps1` (Ersatzweg 2b) | `api.github.com` und `codeload.github.com/<repo>/zip/<sha>`, Standard-Repo fest im Skript | Ersatzweg fällt aus |
| `.github/workflows/ci.yml` | GitHub Actions: Ubuntu- **und Windows**-Runner (Deploy-/Update-Tests mit Windows PowerShell 5.1) | Gitea Actions braucht eigene Runner, für die Windows-Jobs einen Windows-`act_runner` |
| `.github/workflows/release.yml` | `actions/github-script` (prüft den CI-Lauf), `gh release create` | Läuft auf Gitea nicht und muss neu geschrieben werden (Gitea-API `POST /api/v1/repos/{o}/{r}/releases` plus Asset-Upload) |
| `LAN_CONFIG.json` → `UpdateRepo` | nur `konto/repo`, Host immer GitHub | braucht zusätzlich die Gitea-Adresse |
| Doku | `README_Windows.txt` 2b, `docs/RELEASE_REGELN.md`, `docs/ROLLBACK.md`, `docs/ADMIN_UPDATES.md`, Issue-Links in `docs/V12_19_1.md` u. a. | Text anpassen; Issue-Links werden ungültig |
| Tests | `tests/update_button_lib.py` bildet die GitHub-API nach; `test_update_button.py` prüft „Standard = api.github.com“ | an die Gitea-API anpassen |

## 2. Unterschiede der Gitea-API (vor dem Umbau am echten Server prüfen)

- Release: `GET /api/v1/repos/{o}/{r}/releases/latest` ist vorhanden. Assets haben `id`, `name` und `browser_download_url`.
  Den **Inhalt** lädt man über `browser_download_url` (`/{o}/{r}/releases/download/{tag}/{name}`), nicht über die Asset-ID.
- Commit eines Tags: `GET /api/v1/repos/{o}/{r}/git/refs/tags/{tag}` bzw. `/git/commits/{sha}`, nicht `/commits/{tag}` wie bei GitHub.
- Prüfung „Release-Commit liegt in main“: Gitea hat kein `status: behind` wie GitHub.
  Ersatz: Ist der Release-Commit in `GET …/commits?sha=main` enthalten? Die bessere Alternative steht in Abschnitt 6.
- Tokens: `Authorization: token <PAT>`. Ein privates Repo braucht beim Kunden einen Lesetoken, ein öffentliches keinen.

## 3. Brückenversion (V12.29.0, muss noch **auf GitHub** veröffentlicht werden)

> **Stand 15.10.2026 (Gitea-Repo, Branch `claude/bruecke-gitea-updates`):** Punkte 1–3 umgesetzt, abweichend vom Entwurf:
> Quelle ist weiter das eine Feld `UpdateRepo` (bzw. `update.source`), jetzt auch als `https://host/konto/repo`.
> Es wird von `Run_Server_LAN.ps1` und dem Umzug schon heute erhalten. Lesetoken: `config\update\token.txt`.
> `Update_von_GitHub.ps1` behält seinen Namen (wird überall referenziert) und lädt bei Gitea das Quellarchiv des Commits.
> Geprüft gegen das echte Gitea (28.1.0): Release finden, Tag-Commit, „liegt in main“, Download und SHA256.
> **Offen:** Standardquelle im Paket auf Gitea umstellen (braucht die für Kunden erreichbare Gitea-URL), Punkte 4–6.

1. `app_updates.py`: Quelle als Host plus `konto/repo` in `LAN_CONFIG.json` (z. B. `"UpdateHost": "https://git.firma.de"`).
   Für diesen Host gilt die Gitea-API aus Abschnitt 2. Weiterleitungen sind nur zu genau diesem Host erlaubt.
   Ohne Eintrag gilt der neue Standard (Gitea-Adresse fest im Paket, wie heute der GitHub-Standard).
2. `Update_von_GitHub.ps1` → `Update_von_Gitea.ps1` (alter Name bleibt als Weiterleitung für alte Anleitungen).
   Pakete kommen dann aus den Release-Assets statt aus `codeload`.
3. Test-Nachbildung (`update_button_lib.py`) auf die Gitea-API umstellen; die Windows-CI-Tests gegen die Nachbildung bleiben.
4. V12.29.0 auf GitHub veröffentlichen **und** auf Gitea.
5. Auf **jeder** Installation V12.29.0 installieren und dort den Button „Updates suchen“ prüfen (Quelle ist dann Gitea).
6. Erst danach GitHub löschen.

## 4. Clean Start auf Gitea – Checkliste

- [ ] Gitea-Adresse, Version (Actions ab 1.19, besser ≥ 1.21), Konto/Repo festlegen. Ist der Server vom Kunden-LAN aus erreichbar (Firewall, HTTPS-Zertifikat)?
- [ ] Repo über **„Neue Migration → GitHub“** mit GitHub-Token übernehmen: Code, Tags, Releases, Issues, PRs, Labels und Meilensteine.
      Alternativ nur Code: `git clone --mirror` + `git push --mirror`. Die Historie wird gebraucht (`test_deploy_upgrade.py` holt alte Stände).
- [ ] Gitea Actions einschalten; Runner registrieren: Linux (Python 3.12, Node 22, Chromium) und Windows (PowerShell 5.1, Python 3.12).
- [ ] Workflows nach `.gitea/workflows/` kopieren und anpassen; `release.yml` mit der Gitea-API neu schreiben (Entwurf, Assets, CI-Prüfung).
- [ ] Branch-Schutz für `main` (PR-Pflicht, Status „CI“), Tag-Schutz `v*` (siehe `docs/RELEASE_REGELN.md` Abschnitt 5).
- [ ] Brückenversion nach Abschnitt 3; danach überall installiert und geprüft.
- [ ] Doku anpassen (Abschnitt 1, letzte Zeile); Releases V12.27.0 bis V12.29.0 mit Assets auf Gitea vorhanden (für Rückstufung).
- [ ] Lizenz: Der private Signaturschlüssel liegt nicht im Repo und ist nicht betroffen (`docs/LIZENZ.md`).
- [ ] Erst zum Schluss: GitHub-Repo archivieren (lesend) und nach einer Wartezeit löschen.

## 5. Notweg ohne Brückenversion (nach dem Löschen)

Ein Paket lässt sich immer per Hand installieren:
ZIP des Releases auf den Server kopieren, entpacken, dann als Administrator

    powershell -ExecutionPolicy Bypass -File <entpackt>\\UPDATE_LIVE.ps1 -Paket <entpackt>

Mit Vorabtest, Sicherung und Rollback wie beim Button; Daten bleiben erhalten.

## 6. Empfehlung bei der Gelegenheit

Signierte Updates statt „Commit liegt in main“: Das Manifest wird mit dem Schlüssel des Herstellers signiert, wie die Lizenz in `mp_license.py`.
Der öffentliche Schlüssel steht im Paket. Damit hängt die Echtheit eines Updates an keinem Git-Anbieter mehr, und spätere Umzüge sind einfach.
Das Feld `signature` ist im Manifest schon reserviert (`app_updates.validate_manifest`).

## Offene Angaben (vom Betreiber)

Gitea-URL · Konto/Repo · öffentlich oder privat · Gitea-Version · vom Kunden-LAN erreichbar? · Termin für das Löschen von GitHub.

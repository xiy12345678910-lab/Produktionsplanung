# Umzug GitHub → Gitea: Übersicht

Stand: 12.10.2026. Alles zum Umzug steht in diesem Ordner. GitHub wird nach dem Umzug gelöscht.

## Was liegt wo

| Datei | Inhalt |
|---|---|
| `README.md` (diese Datei) | Übersicht, Reihenfolge und Status |
| `INVENTAR.md` | Bestand auf GitHub (Branches, Tags, Releases, Issues, Workflows, Doku) und was davon wohin geht |
| `BEFEHLE.md` | Befehle zum Kopieren: Code, Tags, Archiv-Tags, Releases mit Paketen, Kontrolle |
| `PLAN_BRUECKENVERSION.md` | Warum vor dem Löschen eine Brückenversion (V12.29.0) nötig ist, Gitea-API-Unterschiede, Notweg |
| `CHECKLISTE.md` | Abhakliste vom Anlegen des Gitea-Repos bis zum Löschen von GitHub |
| `RUNNER.md` | Was die Gitea-Runner (Linux, Windows) brauchen, Release-Workflow auf Gitea |

## Reihenfolge (kurz)

1. Kunde auf aktuellen Stand bringen (V12.28.3, danach V12.28.4).
2. Gitea-Repo leer anlegen, Code und Tags übertragen (`BEFEHLE.md` Abschnitt 1–2).
3. Releases mit Paketen auf Gitea anlegen (`BEFEHLE.md` Abschnitt 3).
4. CI auf Gitea einrichten: Runner Linux + Windows (`RUNNER.md`). Die Workflows bleiben in `.github/workflows/` und laufen auf beiden Plattformen.
5. Brückenversion V12.29.0 bauen. Sie lädt Updates von Gitea. Auf GitHub **und** Gitea veröffentlichen (`PLAN_BRUECKENVERSION.md`).
6. V12.29.0 auf **jedem** Kundenserver installieren und prüfen.
7. GitHub archivieren, nach einer Wartezeit löschen.

**Nie GitHub löschen, bevor Schritt 6 überall erledigt ist.** Sonst fällt „Update installieren“ beim Kunden aus.
Updates sind dann nur noch per Hand mit `UPDATE_LIVE.ps1 -Paket` möglich.

## Status

| Punkt | Stand |
|---|---|
| Issues nach Gitea | erledigt (Betreiber). **Ausnahme: #91** wurde danach auf GitHub angelegt und muss noch übertragen werden. |
| Branches bewertet | erledigt (`INVENTAR.md` Abschnitt 1) |
| Tags/Releases bewertet | erledigt (`INVENTAR.md` Abschnitt 2) |
| Code + Tags nach Gitea | erledigt (Abgleich 15.10.2026: Branches, Tags, Issues, PRs, Kommentare, Releases mit Paketen identisch) |
| Releases + Pakete nach Gitea | erledigt (7 Releases, Pakete byte-gleich) |
| CI auf Gitea | Workflows bereit (CI wird ausgelöst, `release.yml` kann Gitea). Offen: Runner (`RUNNER.md`) |
| Brückenversion V12.29.0 | Code fertig (Gitea-Quelle per `UpdateRepo`, siehe `PLAN_BRUECKENVERSION.md` Abschnitt 3). Offen: für Kunden erreichbare Gitea-URL als Standard, Lesetoken je Kunde |
| Kunde auf V12.29.0 | offen |
| GitHub gelöscht | offen (zuletzt) |

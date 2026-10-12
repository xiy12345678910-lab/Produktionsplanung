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

## Reihenfolge (kurz)

1. Kunde auf aktuellen Stand bringen (V12.28.3, danach V12.28.4).
2. Gitea-Repo leer anlegen, Code und Tags übertragen (`BEFEHLE.md` Abschnitt 1–2).
3. Releases mit Paketen auf Gitea anlegen (`BEFEHLE.md` Abschnitt 3).
4. CI auf Gitea einrichten (Runner Linux + Windows, Workflows nach `.gitea/workflows/`).
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
| Code + Tags nach Gitea | offen |
| Releases + Pakete nach Gitea | offen |
| CI auf Gitea | offen |
| Brückenversion V12.29.0 | offen. Braucht: Gitea-URL, Konto/Repo, öffentlich/privat, Gitea-Version, Erreichbarkeit aus dem Kunden-LAN |
| Kunde auf V12.29.0 | offen |
| GitHub gelöscht | offen (zuletzt) |

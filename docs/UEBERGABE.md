# Übergabe – Produktionsplanung (Stand 05.10.2026)

Branch: `claude/new-session-mpx5ch` · Live-fähiger Stand: **V12.10.1** (getestet, Abnahme siehe unten)

## 1. Fertig und getestet (per `UPDATE_LIVE.ps1` einspielbar)

| Version | Thema | Test |
|---|---|---|
| 12.8.0 | Formatplanung Tiefziehen (Ansicht „Formate“): Grundformate, Takte je Maschine, Werkzeuge, Layout, Einplanen als Format-Auftrag, Einlagern/Suche WKZ/FS | `tests/e2e_formats.mjs` 35/35 |
| 12.8.1 | Parallelbelegung: Spalte „Parallel“ je Maschine/Linie, Scheduler + Server (MP-PROD-010, MP-PLAN-058) spurfähig | `tests/e2e_parallel.mjs` 15/15 |
| 12.8.2 | GF legt Bereiche an (Produktion/Vertrieb/Entwicklung), erste Maschine/Linie mit Schichtmodell/Umrüstzeit | `tests/e2e_departments.mjs` 18/18 |
| 12.8.3 | PM-Vorplan beim ersten AV-Überschreiben gesichert, PM-Termine danach gesperrt, Vergleich PM · AV · Ist in AT | `tests/e2e_pmplan.mjs` 11/11 |
| 12.9.0 | Messenger: Kanal „Alle“, Gruppen, Direkt, `/`-Verweise (Auftrag/Projekt/Format) mit Direktlink, `@`-Erwähnungen, klein ↔ groß, Mobil-Vollbild; eigene Tabellen `chat_*`, API `/api/chat/*` | `tests/e2e_chat.mjs` 24/24 |
| 12.9.1 | Nachrichten nach 30 Tagen gelöscht, 📌 behält; Kundenplan ohne erfundenen Liefertermin, PM-Aufgaben im Kundenplan; Abnahme-Korrekturen und UI; `Update_von_GitHub.ps1` | `tests/test_v129.py` 12/12, `e2e_chat.mjs` 31/31, `e2e_customerplan.mjs` 9/9 |
| 12.10.0 | Formate als Fenster (Wochenplan/Auftragsliste/Neuer Auftrag), nur Tiefziehen + Admin; Takte/Maße und Grundformate (mehrere Maschinen) unter System; zwei Bereichsarten; Projektfenster breit | `e2e_formats.mjs` 43/43, `e2e_departments.mjs` 26/26 |
| alle | Rollen-Rundgang (8 Rollen × alle Ansichten × Desktop/Handy) | `tests/e2e_roles.mjs` 374/374 |
| alle | Regression Rechte/Migration (künstliche DB über `tests/make_test_db.py`) | `tests/test_regression.py` 151/151 |
| alle | Server-Regeln ohne Browser | `tests/test_v128.py` 35/35 |
| alle | UI-Smoke (Ansichten, Dialoge, Mobil) | `tests/ui_smoke.mjs` 77/77 |

Behobene Fehler unterwegs: JS-Fehler in `renderFormats`; `migrate()` ergänzte Takt-Felder an allen Maschinen (Abteilungsleitungen konnten nicht speichern); fehlende `formats/baseFormats` im Serverstand blockierten GF/PM/Vertrieb (Migration `migrate_state_v1280`).

Testhinweis: E2E-Tests setzen die Browser-Zeitzone auf Europe/Berlin (wie `release_gates.LOCAL_TZ`).

## 2. Bekannte Grenzen

- Format mit Positionen aus mehreren Projekten: Format-Auftrag ohne Projektverknüpfung.
- Parallelplätze: Personalbedarf/Linienbesetzung gelten je Ressource, nicht je Platz.
- Projektfenster zeichnet sich erst nach Verlassen eines Eingabefelds neu (bewusst, Fokus bleibt).

## 3. Ideen für danach

- Messenger: Dateianhänge/Fotos, Nachrichten bearbeiten/löschen, Browser-Benachrichtigung, Gruppe verlassen.
- Parallelplätze: Personalbedarf je Platz.

## 4. Betrieb

- Update als normaler Windows-Benutzer (z. B. boensch): `Update_von_GitHub.ps1` (siehe README_Windows.txt 2b) – lädt das Paket, startet `UPDATE_LIVE.ps1` per UAC als Administrator.
- Alternativ (Admin-PowerShell): Paket-ZIP des Branches laden, entpacken, `.\UPDATE_LIVE.ps1`.
- Gelöschte Chat-Nachrichten bleiben bis zu 30 Tage in den Backups (60 Sicherungen).
- Danach alle Browser mit Strg+F5 neu laden.

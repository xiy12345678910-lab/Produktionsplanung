# Übergabe – Produktionsplanung (Stand 05.10.2026)

Branch: `claude/new-session-95ro2l` (Basis `claude/new-session-mpx5ch`) · Live-fähiger Stand: **V12.18.0** (getestet, Abnahme siehe unten)

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
| 12.10.2 | Hotfix aus `docs/AUDIT.md` (V-02, V-03, V-04, V-12): Login-Sperre je Benutzer/IP, Host/Origin-Prüfung, Timeouts, 500-Hülle; beforeunload, Cache ohne Quota-Abbruch, Offline erst nach 3 Fehlern, Konfliktkopie; AV ohne Formatrechte; CSV Ortszeit/Formelschutz, Arbeitstage mit Feiertagen | `tests/test_v12102.py` 27/27, `tests/e2e_v12102.mjs` 21/21 |
| 12.10.2 | Betrieb (V-01, V-05): Update nur aus Releases auf main mit festem Commit/SHA256 und geschütztem Staging; Backup über .tmp + Prüfung, `Restore_Datenbank.ps1`, Backup-Warnungen, `update_backups` rotiert | `tests/test_backup.py` 11/11, `tests/ps_syntax.ps1` 15/15 |
| 12.10.2 | V-11 CI (`.github/workflows/ci.yml`), `package.json` (Playwright 1.56.1), `requirements.txt` (tzdata mit Hash), Versionsprüfung; V-07 Python-ACL-Prüfung | `tests/test_version.py` 9/9 |
| 12.11.0 | Darstellung Hell/Dunkel/Halle (CSS-Variablen je Rolle, Kontrast ≥ 4,5 bzw. ≥ 7, Halle ≥ 14 px, Status-Symbole, `?theme=&view=&dept=`) | `tests/e2e_theme.mjs` 20/20 |
| 12.12.0 | Undo/Redo (↶ ↷, Strg+Z/Y): Diff der geänderten Datensätze je save(), Konfliktprüfung gegen Fremdänderungen, max. 50, Rücknahme abgelehnter Änderungen | `tests/e2e_undo.mjs` 12/12 |
| 12.13.0 | Benachrichtigungen: Glocke 🔔 mit Zähler, Liste mit Direktlink, ⚙ Arten/Ruhezeit, Browser-Meldung nur bei https; Tabellen `notifications`, Abruf im Long-Poll | `tests/test_notifications.py` 54/54, `tests/e2e_notifications.mjs` 31/31 |
| 12.14.0 | Firmen-Config `config\firma.json` außerhalb des Pakets (Migration, Validierung MP-CFG-001..003, Update/Rollback/Umzug/Vorabtest/Backup behalten sie); API/UI folgen | `tests/test_firma_config.py` 45/45, `tests/test_config_update.py` 25/25 |
| 12.14.1 | Benachrichtigungen: Long-Poll wird nur bei echter Änderung geweckt (kein Thundering Herd); Client übernimmt Server-Signatur, Backoff bei fehlgeschlagenem Abruf | `tests/test_notifications.py` 59/59, `tests/e2e_notifications.mjs` 32/32 |
| 12.15.0 | Firmenprofil-API `/api/config` (GET alle Rollen, PUT/PATCH + Logo nur Admin, Revision, .bak, SVG-Entschärfung), Client liest Name/Farbe/Logo/Begriffe daraus, Firmenprofil-Panel; Arbeitgeberdaten aus Code/Paket entfernt (`tools/legacy_employer_seed.json` nur im Repo); Zeitzone aus Config | `tests/test_config_api.py` 103/103, `tests/test_no_employer_data.py` 31/31, `tests/e2e_company.mjs` 37/37 |
| 12.15.1 | Datenerhalt bei Updates: Dauertest alte Stände → aktuell (additiver Feldvergleich, Logins, Firmenprofil, 2. Update, Rollback), Windows-Deploy in CI; Bestand ohne Firmenprofil stoppt mit MP-CFG-006 (`Firma_Einrichten.ps1`); Firmen-CI aus data.ci für V12.14.x-Bestände | `tests/test_deploy_upgrade.py` 241/241, `tests/ci_windows_deploy.ps1` (CI) |
| 12.16.0 | Paket B: Branchenvorlagen (`vorlage_*.json`, Anwenden nur ergänzend, Vorschau), Module ein/aus (Client ausgeblendet, Server sperrt mit MP-MOD-001, Daten bleiben), Bereichs-Eigenschaften `formats`/`sharedOperators` statt Bereichs-IDs, Begriffe/Rollenbezeichnungen aus der Config | `tests/test_templates.py` 67/67, `tests/e2e_templates.mjs` 42/42, `test_deploy_upgrade.py` 257/257 |
| 12.17.1 | Fix: Personal-Gate gilt auch für Linien (Besetzung = max(staffRequired, crew)); unbesetzte Aufträge werden markiert (👤⚠, MP-PERS-003) statt eingeplant; Fehler bestand schon in 12.10.1 | `tests/e2e_gate_lines.mjs` 12/12 |
| 12.17.0 | Paket C1: Einrichtungsassistent beim ersten Admin-Login (5 Schritte, überspringbar, später erneut aufrufbar), neutraler Seed für Neuinstallationen, `setupDone` in `firma.json` (Bestand nie), Projektbereiche aus der Config | `tests/test_setup.py` N1, `tests/e2e_setup.mjs` N2, `test_deploy_upgrade.py` N3 |
| 12.17.2 | Hotfix Windows: ACL-Prüfung wertet nur echte Schreib-Bits (`Test-MPRightsWritable`, kein Fehlalarm bei Benutzer RX); CHECK_LAN ohne Fehlerrauschen; Firma_Einrichten zeigt Pfad; Server_Status: Task-Ergebnis 267009 = „läuft“. Client-Abbruch (10053/10054/EPIPE) kein MP-SRV-500; Run_Server_LAN loggt stderr 1:1. Tests `tests/ps_acl.ps1`, `tests/test_client_abort.py` |
| 12.18.0 | Dauer nach Besetzung (P18): Schalter je Ressource (Default aus), Sollstunden = Personenstunden, Dauer = Ph ÷ Besetzung je Schicht (Gate EIN: zugeordnete Mitarbeiter, AUS: crew), `crewMax`; Personal je Parallelplatz (P16, Teil: `laneStaff`, Bedarf = Summe belegter Plätze); Freigabeplan mit Besetzung je Segment; Ist-Personenstunden in der Historie | `tests/e2e_effort.mjs` 36/36, `tests/test_effort.py` 22/22 |
| alle | Rollen-Rundgang (8 Rollen × alle Ansichten × Desktop/Handy) | `tests/e2e_roles.mjs` 374/374 |
| alle | Regression Rechte/Migration (künstliche DB über `tests/make_test_db.py`) | `tests/test_regression.py` 151/151 |
| alle | Server-Regeln ohne Browser | `tests/test_v128.py` 35/35 |
| alle | UI-Smoke (Ansichten, Dialoge, Mobil) | `tests/ui_smoke.mjs` 77/77 |

Behobene Fehler unterwegs: JS-Fehler in `renderFormats`; `migrate()` ergänzte Takt-Felder an allen Maschinen (Abteilungsleitungen konnten nicht speichern); fehlende `formats/baseFormats` im Serverstand blockierten GF/PM/Vertrieb (Migration `migrate_state_v1280`).

Testhinweis: E2E-Tests setzen die Browser-Zeitzone auf Europe/Berlin (wie `release_gates.LOCAL_TZ`).

## 2. Bekannte Grenzen

- Format mit Positionen aus mehreren Projekten: Format-Auftrag ohne Projektverknüpfung.
- Parallelplätze: Personalbedarf je Platz nur als Summe (`laneStaff`, V12.18.0); Zuordnung auf einen Platz (laneIndex), Auswertung/GF-Cockpit/Board je Platz und Dauer nach Besetzung bei mehreren Plätzen sind offen (Prompt 16/18).
- Projektfenster zeichnet sich erst nach Verlassen eines Eingabefelds neu (bewusst, Fokus bleibt).

## 3. Ideen für danach

- Messenger: Dateianhänge/Fotos, Nachrichten bearbeiten/löschen, Gruppe verlassen.
- Parallelplätze: Personalbedarf je Platz.

## 4. Betrieb

- Update als normaler Windows-Benutzer (z. B. boensch): `Update_von_GitHub.ps1` (siehe README_Windows.txt 2b) – lädt das Paket, startet `UPDATE_LIVE.ps1` per UAC als Administrator.
- Alternativ (Admin-PowerShell): Paket-ZIP des Branches laden, entpacken, `.\UPDATE_LIVE.ps1`.
- Gelöschte Chat-Nachrichten bleiben bis zu 30 Tage in den Backups (60 Sicherungen).
- Danach alle Browser mit Strg+F5 neu laden.

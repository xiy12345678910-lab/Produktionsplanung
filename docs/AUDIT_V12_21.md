# Ist-Übersicht V12.21.0 (Deep Audit #64, Phase 1–2)

Stand: 08.10.2026, Branch `claude/loving-knuth-bphlrr` = `main` (V12.20.0, `b30005c`) + Lazy-Loading-/FA-Dialog-Stand aus #69.
Vorgänger-Audit: `docs/AUDIT.md` (V12.10.1). Diese Übersicht ist **vor** größeren Fixes erstellt (Vorgabe #64, Phase 2).

**Prüftiefe:** `server.py` (Rechte, Redaktion, Produktions-Runtime, State-PUT-Schutz) gelesen und gezielt per HTTP geprüft; alle Server- und Browsertests ausgeführt. `index.html` (640 KB) nur im geänderten Lazy-/Dialog-Teil gelesen, sonst über die E2E-Tests abgedeckt. Windows-Skripte nicht ausgeführt (Linux-Umgebung; CI-Job `windows-deploy` deckt sie ab).

## Testergebnis (lokal, 08.10.2026)

| Test | Ergebnis |
|---|---|
| Python: 20 Testdateien (v128, v129, notifications, blocks, updates, av_handoff, project_live, personnel_times, admin_v12191, backup, …) | alle grün |
| Regression künstliche DB | 151/151 |
| STRICT-Upgrade (alte Stände → aktuell) | 313/313 |
| UI-Smoke | 79/79 |
| Browser-E2E (25 Dateien, u. a. Rollen 408/408, Lazy 14/14, AV 51/51, Blocks 35/35, Effort 36/36) | alle grün nach den Testkorrekturen unten |

## Übersicht

| Bereich / Block | Ist-Stand | Fehler / Lücke | Risiko | Prio | Maßnahme |
|---|---|---|---|---|---|
| A Core Data (FA-Kern, FS→FA, Source-Modell) | PASS – `normalize_fa_state`, Source-Typen PROJECT/FRAME_ORDER/STOCK_REQUIREMENT, Migration im STRICT-Upgrade geprüft | – | gering | – | – |
| B Planning Engine (Personal, Teilzeit, Parallel, Qualifikation) | PASS – Tests personnel_times, gate_lines, parallel, effort grün | – | gering | – | – |
| C Projektfluss & Rechte | PASS – Bereichsredaktion `read_scope`/`redact_state`, AV nur Konfektionsstunden, Rollen-E2E 408/408 | Projekte der eigenen Bereiche zeigen Kundendaten auch für Bereichsrollen (nur Produktion maskiert) – fachlich vermutlich gewollt | gering | P3 | bestätigen lassen |
| D Production Runtime | PASS – idempotent je Benutzer+Request-ID, Statusautomat, Mengenprüfung, Historie unveränderlich, Runtime-Felder per State-PUT gesperrt (geprüft) | – | gering | – | – |
| #69 Lazy Loading / FA-Dialog (V12.21.0) | PASS lokal – alle Tests grün | 3 Tests prüften inaktive Ansichten bzw. warteten nicht korrekt; korrigiert | gering | P1 | CI abwarten, dann PR/Release nach Freigabe |
| E Demand & Frame Orders (#46) | **FAIL / kaum vorhanden** – nur Source-Felder (`frameOrderId`, `callOffId`, `stockRequirementId`) und Bestandszugang bei Fertigmeldung | kein Rahmenauftrags-, Abruf-, Reservierungs- oder Bedarfsmodell, keine UI, keine idempotente FA-Erzeugung aus Bedarf | mittel (Feature fehlt, kein Datenrisiko) | P2 | eigener Block, siehe Reihenfolge |
| F Machine Framework (#47) | PARTIAL – Planungsarten MACHINE/LABOR_HOURS/PROCESS/CYCLE validiert, Umrüstzeit vorhanden | keine Reinigung, keine Charge, keine Takt-/Zykluszeit-Berechnung, keine Umrüstmatrix | mittel | P2 | nach E |
| G Tiefziehen V13 (#48) | PARTIAL – Formate, Grundformate, Nutzen/Auto-Fit-Ansätze in der UI | Werkzeug-/Rotations-/manuelle Positionierung laut Issue nicht vollständig | mittel | P2 | nach F |
| H Reporting (#49) | PARTIAL – GF-KPIs, Report, Ist-Personen-/Maschinenstunden | keine Liefertermin-Szenarien; Nachkalkulation bewusst offen | gering | P3 | nach G |
| I Deployment (#50) | PARTIAL – Backup/Restore/Rollback/Update mit Tests, Windows-CI | **kein HTTPS** (Altbefund B-H1); Dienstkonto statt SYSTEM offen (O-H2); Umzug nur dokumentiert | **hoch** (Passwörter/Cookies im Klartext im LAN) | **P1** | HTTPS mit internem Zertifikat |
| J Testkunde (#51) | PARTIAL – Einrichtungsassistent, Vorlagen, Demo, E2E setup/templates/company grün | Import-Pfad nicht geprüft | gering | P3 | – |
| K Productization (#52) | PARTIAL – Lizenzdatei, Release-Gates, Paket-Hash | keine signierten Updates, kein Installer-Paket | mittel | P3 | nach I |
| V9 Rollen & Rechte (#63, #55) | **FAIL** – Rollen fest im Code (`ROLES`), Rechte je Rolle fest verdrahtet | eigene Rollen, Rechte je Funktion (Kein/Lesen/Bearbeiten), Aktionsrechte fehlen | mittel (große Änderung am Rechtekern) | P2 | eigener Block mit Server-Rechtemodell zuerst |

## A. Wirklich fertig (Code + Test)
Blocks A–D, AV-Dialog/Projekt-Live (V12.20.0), Lazy Loading + gemeinsamer FA-Dialog (#69, lokal), Personalzeiten, Benutzerfilter, Palettenzettel optional, Scheduler-Selbsttest, Backup/Restore-Logik, STRICT-Upgrade.

## B. Nur teilweise fertig
Blocks F, G, H, I, J, K (siehe Tabelle). Block E nur Datenfelder.

## C. Echte Bugs
| Ort | Ursache | Auswirkung | Status |
|---|---|---|---|
| `tests/e2e_lazy_loading.mjs` | Server ohne `MP_CONFIG_DIR`; Modulschalter schrieb in `config/` des Repos | lokaler Testlauf verändert Arbeitskopie, Folgetests liefen mit abgeschaltetem Projektmodul | behoben |
| `tests/e2e_lazy_loading.mjs` | Mitarbeiter mit Stammmaschine → Personalrevision ohne sichtbare Änderung | Prüfung konnte nicht fehlschlagen bzw. schlug immer fehl | behoben |
| 19 weitere `tests/*.mjs` | gleiche fehlende Config-Isolation (`ui_smoke`/`e2e_admin_v12191` schrieben `config/firma.json`) | wie oben | behoben (eigener Config-Ordner im Temp-Verzeichnis) |
| `e2e_effort`, `e2e_blocks`, `e2e_av_handoff` | prüften nicht aktive Ansichten bzw. `waitForFunction` mit async-Prädikat | nach Lazy Loading rot | behoben |
| `server.py` HTTP | kein TLS, Cookie ohne `Secure` | Mitlesen von Passwort/Sitzung im LAN | **offen, P1** |

Kein Datenverlust-, Scope-Leak- oder Mengenfehler gefunden: Direkter State-PUT kann Bestand, Ereignisse, Historie und Runtime-Felder nicht fälschen (403/400, per HTTP geprüft).

## D. Offene Features
- Release-relevant: HTTPS (I), V12.21.0-Abnahme in CI.
- Produktreife: Block E, F, G, Rollen-/Rechteverwaltung (#63), signierte Updates (K).
- Future: Lager (#54), eigene Rollen granular (#55, deckt sich mit #63), Nachkalkulation (#11/H).

## E. Veraltete / doppelte Issues
- **#57** fachlich erledigt (V12.20.0 released, Windows-Installation laut #69 bestätigt) → schließen.
- **#55** überschneidet sich mit #63 (eigene Rollen/Rechte) → zusammenführen oder auf #63 verweisen.
- **#53** (HTML 1 als Soll-Referenz) durch V9-Referenz in #63 abgelöst → prüfen/schließen.
- Offener PR **#7** (`claude/new-session-mpx5ch`, 05.10.) ist veraltet → schließen.

## F. Technisch sinnvolle Reihenfolge
1. #69 abschließen (CI grün, dann PR/Merge/Release v12.21.0 nach Freigabe).
2. **HTTPS** (Sicherheit, P1) – unabhängig vom Datenmodell, kleiner Umfang.
3. Block E (#46) – baut auf A–D auf.
4. #63 Rollen & Rechte – zuerst serverseitiges Rechtemodell, danach V9-UI.
5. F → G → H, dann I/K-Rest, J.

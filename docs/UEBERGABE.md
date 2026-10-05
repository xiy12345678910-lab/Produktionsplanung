# Übergabe – Produktionsplanung (Stand 05.10.2026)

Branch: `claude/sharp-noether-hc1ycl` · Live-fähiger Stand: **V12.7.6** (getestet)

## 1. Fertig und getestet (im Branch, per `UPDATE_LIVE.ps1` einspielbar)

| Thema | Dateien | Test |
|---|---|---|
| Start-Popup (Tag/Uhrzeit) über ▶ in der Auftragsliste; geplante Aufträge werden dabei erst freigegeben, dann gestartet | `index.html` (`openProdStart`, `confirmProdStart`, `startOrderAt`, `flushNow`) | E2E 12/12 |
| Fertig-Popup mit realer Zeit: Kalenderzeit − Pausen/Feierabend/freie Tage − Produktionspausen − Umrüsten; Werte in Historie (`actualWorkHours`, `actualSetupMinutes`, `actualProductionHours`) | `index.html` (`finishTimes`, `updateFinishCalc`) | E2E |
| Wochenplan: „In Produktion seit …“ / „Abgeschlossen · x h real“ | `index.html` (`chipHtml`, `renderBoard`) | E2E |
| Fix MP-PERS-033: Server zählt Personal wie die Oberfläche (Stammmaschine, KW-Einsatz, Leiharbeiter) | `release_gates.py` (`personnel_cover` + Helfer) | Unit-Test |
| Version 12.7.6, Release Notes, Fehlercodes MP-PROD-033..035 | `server.py`, `index.html`, `RELEASE_NOTES.txt`, `FEHLERCODES.txt` | UI-Smoke 77/77 |

Wichtige Server-Regel: Statuswechsel nur **Geplant → Freigegeben → In Produktion**, jeweils ein eigener Speichervorgang. Daher speichert `confirmProdStart` die Freigabe zuerst (`flushNow`) und startet danach.

## 2. Mockup (nur zur Abstimmung, ohne Server)

`docs/mockups/mockup_parallel_formatplanung.html` – Parallelbelegung im Wochenplan, Formatplanung Tiefziehen:
Grundformate (Abteilungsleiter), Werkzeuge mit WKZ-Nr./L×B×H/Nutzen, empfohlenes Layout (Rand 100 mm, Abstand ½ Werkzeughöhe), Verschieben/Drehen, Einlagern mit Lagerort, Suche nach WKZ-/FS-Nummer („X hat das Format hier abgelegt“).

Abgestimmt mit dem Anwender:
- Takte gehören **an die Maschine** (Liste je Maschine, Auswahl am Format) – im Mockup noch „Takt je Format und Maschine“, in der App-Version (WIP) bereits richtig umgesetzt.
- Werkzeugmaße sind je Format frei änderbar.
- Gesucht wird über **WKZ-Nummer oder FS-Nummer**.

## 3. In Arbeit: V12.8.0 Formatplanung in der App (NICHT getestet)

Gesichert als Patch: `docs/wip/V12.8.0_formate_WIP.patch` (gegen V12.7.6, lässt sich sauber anwenden):

```bash
git apply docs/wip/V12.8.0_formate_WIP.patch
```

Inhalt:
- **Server** (`server.py`): `validate_formats` (Grundformate `baseFormats`, Formate `formats`), `_validate_machine_format_fields` (Maschine: `takte[{id,name,sec}]`, `maxL/maxB/maxH`), Rechte: Abteilungsrollen nur eigener Bereich (`formats`, `baseFormats`), AV darf `formats`, GF/PM/Vertrieb nur lesen. Neue Codes MP-FMT-001..010, MP-MACH-014.
- **Oberfläche** (`index.html`): neue Ansicht „Formate“ (Nav `navFormats`, View `formats`), Modul `renderFormats` u. a.:
  Formatliste (In Arbeit / Eingelagert, Suche WKZ/FS), Grundformate, Maschinen & Takte, Editor, SVG-Draufsicht mit Verschieben, offene Tiefzieh-Aufträge „Auf Format“, Einplanen (`fmtPlan`: ersetzt die übernommenen geplanten FS durch einen Format-Auftrag `formatId`, Laufzeit = Takte × Takt), Einlagern/Auslagern mit Lagerort, WKZ-Maße werden aus früheren Formaten übernommen.
  `migrate()` übernimmt die neuen Maschinenfelder und `formats`/`baseFormats`; `askInput` kann eine Vorschlagsliste (`list`).

Offen vor Freigabe:
1. Browser-E2E gegen echten Server (Muster: `tests/ui_smoke.mjs`): Tiefziehmaschine anlegen (System → „+ Maschine“ bei Tiefziehen), Takt + Grundformate anlegen, Tiefzieh-Auftrag anlegen, Format bauen, einplanen (Server-Stand prüfen), einlagern, per WKZ wiederfinden, als Abteilungsleiter/AV/Viewer prüfen.
2. `tests/ui_smoke.mjs` (77 Prüfungen) erneut laufen lassen.
3. FEHLERCODES/RELEASE_NOTES ergänzen, Version 12.8.0 setzen (`APP_VERSION`, `CLIENT_VERSION`, Titel).
4. Bekannte Grenze: Ein Format mit Positionen aus mehreren Projekten bekommt keine Projektverknüpfung am Format-Auftrag (nur wenn alle Positionen aus einem Projekt stammen).

## 4. Noch nicht begonnen

- **V12.8.1 Parallelbelegung**: Konfektion und Tiefziehen fahren mehrere Aufträge gleichzeitig. Idee: Parallelplätze (`lanes`) je Maschine/Linie; Scheduler (`calcSchedule`, `tryScheduleOnMachine`, `machineBlocks`) je Spur; Server-Prüfungen MP-PROD-010 (nur ein laufender Auftrag je Maschine) und MP-PLAN-058 (`release_gates.py`) spurfähig machen; `machineHasLockedOrder` im Client.
- **V12.8.2 GF legt Bereiche an**: `departments[].kind` = production | sales | development. Vertrieb/Entwicklung = nur Projektaufgaben (keine Maschinen/Fertigmeldungen), Produktion = Maschinen/Linie, Schichtmodell, Umrüstzeit. Anpassen: `gf_change_allowed`, `projectIsDeptArea`, AV-`own_areas`, PM-Statusbereiche, Bereichslisten (Scope-Auswahl, Auftragsdialog, Maschineneinstellungen, GF-Raster).
- **V12.8.3 PM-Vorplan vs. AV vs. Ist**: beim ersten Überschreiben durch die AV `pmPlan {startDate,dueDate}` am Prozess sichern (Server: `_av_process_change` erlaubt nur diesen Schnappschuss, `_pm_process_change` sperrt danach Termine), Vergleichstabelle im Projekt mit Ist/Prognose aus `productionChain` und Abweichung in Arbeitstagen (`workDayDiff`).

## 5. Betrieb

- Update (Admin-PowerShell, Daten bleiben erhalten): Paket-ZIP des Branches laden, entpacken, `.\UPDATE_LIVE.ps1` – Backup, Vorabtest mit Datenkopie, automatisches Zurückspielen bei Fehlern.
- Danach alle Browser mit Strg+F5 neu laden.

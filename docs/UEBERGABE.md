# Übergabe – Produktionsplanung (Stand 05.10.2026)

Branch: `claude/new-session-mpx5ch` · Live-fähiger Stand: **V12.8.3** (getestet)

## 1. Fertig und getestet (per `UPDATE_LIVE.ps1` einspielbar)

| Version | Thema | Test |
|---|---|---|
| 12.8.0 | Formatplanung Tiefziehen (Ansicht „Formate“): Grundformate, Takte je Maschine, Werkzeuge, Layout, Einplanen als Format-Auftrag, Einlagern/Suche WKZ/FS | `tests/e2e_formats.mjs` 35/35 |
| 12.8.1 | Parallelbelegung: Spalte „Parallel“ je Maschine/Linie, Scheduler + Server (MP-PROD-010, MP-PLAN-058) spurfähig | `tests/e2e_parallel.mjs` 15/15 |
| 12.8.2 | GF legt Bereiche an (Produktion/Vertrieb/Entwicklung), erste Maschine/Linie mit Schichtmodell/Umrüstzeit | `tests/e2e_departments.mjs` 18/18 |
| 12.8.3 | PM-Vorplan beim ersten AV-Überschreiben gesichert, PM-Termine danach gesperrt, Vergleich PM · AV · Ist in AT | `tests/e2e_pmplan.mjs` 11/11 |
| alle | Server-Regeln ohne Browser | `tests/test_v128.py` 35/35 |
| alle | UI-Smoke (Ansichten, Dialoge, Mobil) | `tests/ui_smoke.mjs` 77/77 |

Behobene Fehler unterwegs: JS-Fehler in `renderFormats`; `migrate()` ergänzte Takt-Felder an allen Maschinen (Abteilungsleitungen konnten nicht speichern); fehlende `formats/baseFormats` im Serverstand blockierten GF/PM/Vertrieb (Migration `migrate_state_v1280`).

Testhinweis: E2E-Tests setzen die Browser-Zeitzone auf Europe/Berlin (wie `release_gates.LOCAL_TZ`).

## 2. Bekannte Grenzen

- Format mit Positionen aus mehreren Projekten: Format-Auftrag ohne Projektverknüpfung.
- Parallelplätze: Personalbedarf/Linienbesetzung gelten je Ressource, nicht je Platz.
- Projektfenster zeichnet sich erst nach Verlassen eines Eingabefelds neu (bewusst, Fokus bleibt).

## 3. Als Nächstes

- **V12.9.0 Messenger**: Teams-ähnlich, Gruppen und Direktnachrichten, `/`-Erwähnungen von Aufträgen/Projekten/Formaten mit Direktlink, kleines Fenster ↔ groß.

## 4. Betrieb

- Update (Admin-PowerShell, Daten bleiben erhalten): Paket-ZIP des Branches laden, entpacken, `.\UPDATE_LIVE.ps1`.
- Danach alle Browser mit Strg+F5 neu laden.

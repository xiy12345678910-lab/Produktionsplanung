# Produktionsplanung

Maschinenplanung V12.25.0 – Wochen-/Ressourcenplanung je Bereich (Maschinen & Linien), Projekte,
Formate (Tiefziehen), Personal und Nachrichten. Zentraler Windows-LAN-Server, Browser-Oberfläche.

- `server.py` – Python-Server (nur Standardbibliothek, SQLite), `index.html` – Oberfläche, `release_gates.py` – Freigabeprüfung, `app_updates.py` – allgemeine Releaseupdates
- Windows: `README_Windows.txt` (Installation, Update, Update direkt von GitHub), `BENUTZER_KURZANLEITUNG.txt`
- A–D: `docs/BLOCKS_A_D.md` · Admin-Updates (I 7a): `docs/ADMIN_UPDATES.md`
- Änderungen: `RELEASE_NOTES.txt` · Fehlercodes: `FEHLERCODES.txt` · Übergabe: `docs/UEBERGABE.md`
- Trocknung durch Abteilungsleitung (hh:mm), Phase-0-Referenztests V12.25.0: `docs/V12_25_0.md`
- Trocknungs-/Wartezeit nach Arbeitsgaengen V12.24.0: `docs/V12_24_0.md`
- Rollen & Rechte, FA-Einplanungsfenster V12.23.0: `docs/V12_23_0.md`
- Rahmenaufträge, Abrufe & Bestand (Block E) V12.22.0: `docs/V12_22_0.md`
- Lazy Loading, FA-Dialog und HTTPS V12.21.0: `docs/V12_21_0.md`
- AV-Dialog und Projekt-Liveverfolgung V12.20.0: `docs/V12_20_0.md`
- Hotfixes V12.19.1 (#57–#62): `docs/V12_19_1.md`

## Tests

```
python tests/test_blocks.py                   # A–D: Migration, Planung, Rechte, atomare Produktion
python tests/test_updates.py                  # I 7a: Folgeversionen, Paketprüfung, Status, Wiederherstellung
python tests/test_roles_rights.py             # Eigene Rollen: Rechte je Funktion/Aktion serverseitig
node tests/e2e_roles_rights.mjs              # System > Rollen & Rechte bis zur Wirkung beim Benutzer
python tests/test_demand.py                   # Block E: Rahmenauftrag, Abruf, Bestand, Bedarf → FA
node tests/e2e_demand.mjs                    # Bedarf über die Oberfläche bis zur Bereichsplanung
python tests/test_project_live.py             # Produktion: Projekt-Lesescope und Schreibschutz
node tests/e2e_project_live.mjs              # Geoeffnetes Projekt aktualisiert Plan und Istfortschritt
python tests/test_av_handoff.py               # AV-Übergabe, Vorgaben und Bereichsrechte
python tests/test_personnel_times.py          # Nettozeiten, Teilzeit und individuelle Pausen
python tests/test_admin_v12191.py             # Optionale Palettenzettel, Datenerhalt und Admin-Rechte
node tests/e2e_av_handoff.mjs                 # AV → Bereichsvorrat → Planungsvorschau
node tests/e2e_personnel_performance.mjs      # Personalzeiten und aktuelle Planberechnung
node tests/e2e_admin_v12191.mjs               # Benutzerfilter, Bereiche und Palettenzettel
node tests/e2e_scheduler_selftest.mjs         # Isolierter Selbsttest ohne Live-Änderungen
node tests/e2e_blocks.mjs                    # AV → Bereich → Produktion, Exporte, Admin-Update-UI
python tests/test_v128.py                     # Server-Regeln Formate/Parallel/Bereiche/PM-Vorplan
python tests/test_v129.py                     # Nachrichten: Aufbewahrung, Behalten, Erwähnungen
python tests/make_test_db.py <leerer-ordner>  # künstliche DB, danach:
python tests/test_regression.py <ordner>      # Rechte/Migration (auch gegen Kopie der Live-DB)
node tests/ui_smoke.mjs                       # Oberfläche allgemein
node tests/e2e_roles.mjs                      # jede Rolle × jede Ansicht × Desktop/Handy
node tests/e2e_formats.mjs | e2e_parallel.mjs | e2e_departments.mjs | e2e_pmplan.mjs | e2e_chat.mjs | e2e_customerplan.mjs
```
Die E2E-Tests brauchen Playwright mit Chromium und starten den Server mit Testdaten in einem Temp-Ordner.

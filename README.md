# Produktionsplanung

Maschinenplanung V12.17.0 – Wochen-/Ressourcenplanung je Bereich (Maschinen & Linien), Projekte,
Formate (Tiefziehen), Personal und Nachrichten. Zentraler Windows-LAN-Server, Browser-Oberfläche.

- `server.py` – Python-Server (nur Standardbibliothek, SQLite), `index.html` – Oberfläche, `release_gates.py` – Freigabeprüfung
- Windows: `README_Windows.txt` (Installation, Update, Update direkt von GitHub), `BENUTZER_KURZANLEITUNG.txt`
- Änderungen: `RELEASE_NOTES.txt` · Fehlercodes: `FEHLERCODES.txt` · Übergabe: `docs/UEBERGABE.md`

## Tests

```
python tests/test_v128.py                     # Server-Regeln Formate/Parallel/Bereiche/PM-Vorplan
python tests/test_v129.py                     # Nachrichten: Aufbewahrung, Behalten, Erwähnungen
python tests/make_test_db.py <leerer-ordner>  # künstliche DB, danach:
python tests/test_regression.py <ordner>      # Rechte/Migration (auch gegen Kopie der Live-DB)
node tests/ui_smoke.mjs                       # Oberfläche allgemein
node tests/e2e_roles.mjs                      # jede Rolle × jede Ansicht × Desktop/Handy
node tests/e2e_formats.mjs | e2e_parallel.mjs | e2e_departments.mjs | e2e_pmplan.mjs | e2e_chat.mjs | e2e_customerplan.mjs
```
Die E2E-Tests brauchen Playwright mit Chromium und starten den Server mit Testdaten in einem Temp-Ordner.

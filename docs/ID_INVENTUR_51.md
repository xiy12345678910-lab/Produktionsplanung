# Inventur fest verdrahteter Bereichs- und Maschinen-IDs (Issue #51, Aufgabe 4)

Stand: Commit-Basis `3c53067` (Branch `claude/loving-knuth-bphlrr`). Nur Analyse, kein Code geändert.

## Ergebnis

- **11 Stellen** mit fester Bereichs-ID in der Logik (server.py, index.html, release_gates.py), dazu **1 bewusst feste Liste** (Projekt-Systembereiche).
- **Maschinen-IDs** (`m1`–`m3`): keine Logik-Abhängigkeit. Vorkommen nur in Seed und Tests.
- **Siebdruck / Formbau**: keine Logik-Abhängigkeit. Vorkommen nur in Labels, Hinweistexten und kommentierten Stellen.

## Tabelle

| Datei:Zeile | ID | Was hängt daran (Verhalten) | Vorschlag: Bereichs-Flag/Eigenschaft | Risiko |
|---|---|---|---|---|
| server.py:1327 (`migrate_state_v1216`) | `thermoforming`, `cnc` | Einmalige Migration: setzt `formats=true` bzw. `sharedOperators=true`, wenn die Eigenschaft fehlt. | Keine Änderung. Migration bleibt als Upgrade-Pfad. | niedrig |
| server.py:1347 (`migrate_state_v12270`) | Teilstring „konf“ in ID/Name | Einmalige Migration: setzt `avHours=true` bei Namenstreffer. Namens-Heuristik. | `avHours` (bereits Flag). Heuristik nicht erweitern. | niedrig |
| server.py:1406 (`migrate_state_v1243`) | `cnc` (Fallback für `departmentId`) | Legacy-Arbeitsgänge ohne Bereich bekommen `cnc`. | `default_dept_id(state)` statt Literal; neue Eigenschaft `defaultArea`. | niedrig |
| server.py:1539–1540 (`DEFAULT_PM_TEMPLATES`, `tpl_pm_dev`) | `cnc`, `konf1` als `areaId` | Standard-Musterablauf. Beim Anlegen (Z. 1559) werden Schritte mit unbekannter Bereichs-ID **still verworfen**. Umbenennen oder Löschen von cnc/konf1 entfernt Schritte ohne Hinweis. | Neue Eigenschaft `sampleArea` (Bereich für Musterfertigung). | mittel |
| server.py:1614 (`migrate_state_v1270`) | `cnc` (Fallback für Maschine ohne Bereich) | Migration Siebdruck/Tiefziehen: Maschinen ohne Bereich werden `cnc` zugeordnet. | `default_dept_id(state)`. | niedrig |
| server.py:2100 (`default_dept_id`) | `cnc` (Fallback, wenn keine Bereiche existieren) | Zielbereich für Benachrichtigungen, Sichtbarkeit und Diff-Logik (Z. 2109, 2251, 2709, 2862, 3676, 4018). | Neue Eigenschaft `defaultArea` auf dem ersten aktiven Produktionsbereich. Literal entfernen. | mittel |
| server.py:2103 (`_step_dept`) | `cnc` (Default-Parameter `dd`) | Bereich eines Arbeitsgangs für Benachrichtigung und Sichtbarkeit (Z. 2136, 2261). Greift nur, wenn der Aufrufer kein `dd` übergibt. | Parameter ohne Literal; alle Aufrufer übergeben `default_dept_id`. | niedrig |
| index.html:609 (`defaultDepartmentId`) | `'cnc'` (Fallback) | Bereich für Maschinen ohne `departmentId` (Z. 723, 731, 1583, 1678): Wochenplan, Formate, Auftragsdialog. | Wie server.py:2100 (`defaultArea`). Die Logik (erster Produktionsbereich) existiert schon, nur das Literal fehlt. | mittel |
| release_gates.py:80 (`dept_of`) | `cnc` (Fallback) | Bereich einer Maschine bei der Freigabe-Prüfung. Maschine ohne Bereich wird als CNC behandelt. | `defaultArea` bzw. Maschinenbereich ohne Literal. | mittel |
| release_gates.py:306 (`home_machine_for`) | `cnc` (Fallback) | Stammmaschine einer Person gilt nur, wenn ihr Einsatzbereich gleich `cnc` ist. | Vergleich über den Bereich der Maschine statt Literal. | mittel |
| release_gates.py:452–454 (Bedienerkapazität) | `cnc` (Vergleich) | Serverseitige Bedienerprüfung gilt nur für Bereich `cnc`. Der Client prüft dagegen `sharedOperators` (index.html:610, 699–700, 723). **Abweichung:** Ein Bereich mit `sharedOperators=true` wird serverseitig nicht geprüft; „cnc“ ohne Flag schon. Serverseitig kommt `sharedOperators` sonst nicht vor (nur Seed, Validierung, Migration). | `sharedOperators` (bereits Flag). Server und Client angleichen. | **hoch** |
| server.py:1556, 2807, 3774 (`PROJECT_FIXED_AREA_IDS`, `PM_STATUS_AREAS`) | sales, pm, engineering, calculation, purchasing, quality, av | Feste Projekt-Systembereiche, keine Produktionsbereiche. Bewusst fest. | Kein Flag nötig. Nicht anfassen. | niedrig |

Nicht gezählt, nur Hinweis: `deptKind` (index.html:1076) und `DEPARTMENT_KINDS` (server.py:2806) nutzen Art-Werte `sales`/`development`, keine Bereichs-IDs.

### Bereits auf Eigenschaften umgestellt (kein Handlungsbedarf)

- **avHours**: server.py 942, 1014, 3987, 3994, 4116; index.html 897, 1078, 1170, 1583.
- **noQuantity**: server.py 4429, 4521.
- **formats**: index.html 1678, 1722. Serverseitig nur Validierung und Migration.
- **sharedOperators**: index.html 610, 699–700, 723, 856. Serverseitig siehe Zeile release_gates.py:452–454.
- **dryingHours** und **planningType** hängen nicht an IDs; `planningType` prüft den Typ (MACHINE, LABOR_HOURS, PROCESS, CYCLE).

### Ignoriert (Seed, Demo, Tests, Nicht-Logik)

Seed/Demo: server.py:59 (`CONFIG_TEMPLATES`), 1079–1091 (`LEGACY_MACHINES`, `LEGACY_DEPARTMENTS`), 1208 (`seed == "werbetechnik"`); index.html:460–476 (Demo-Maschinen und -Bereiche), 891 (Scheduler-Tests). Tests in `tests/`. `tools/` und `*.ps1`: keine Logik (nur Vorlagendateinamen in MP_Common.ps1:35).

## Reihenfolge für Aufgabe 5

**Schritt 1 – ERLEDIGT (Aufgabe 5): Fallbacks in server.py 1406, 1614, 2103 und release_gates.py 80, 306 nutzen den ersten aktiven Produktionsbereich; index.html:609 hatte die Logik schon. Die Migration `defaultArea` entfiel (nicht nötig, Verhalten gleich). – sicher, verhaltensgleich, zuerst**
- Literal `"cnc"` als Fallback ersetzen durch `default_dept_id(state)` bzw. `defaultDepartmentId()`: server.py 1406, 1614, 2100, 2103; index.html 609.
- Verhalten identisch, solange `cnc` der erste aktive Produktionsbereich ist (Standard-Seed).
- Migration (idempotent): Eigenschaft `defaultArea=true` auf dem Bereich mit ID `cnc` setzen, sofern vorhanden und kein Bereich das Flag trägt.

**Schritt 2 – ERLEDIGT (Aufgabe 5): release_gates 80/306/452–454 umgestellt, `sharedOperators` serverseitig wie im Client; Test `tests/test_shared_operators_flag.py`. Keine neue Migration, v1216 deckt den Bestand ab. – mit Test, Verhalten ändert sich bei Abweichung**
- release_gates.py 80 und 306 nach Schritt 1 umstellen.
- release_gates.py 452–454 auf `sharedOperators` umstellen. Migration: v1216 hat `cnc.sharedOperators=true` bereits gesetzt, daher für Bestand identisch. Zusätzlich Test mit zwei Bereichen (Golden-Tests unter `tests/golden` prüfen). Risiko hoch.

**Schritt 3 – OFFEN (`sampleArea` nicht umgesetzt) – neue Eigenschaft nötig**
- server.py 1539–1540: Eigenschaft `sampleArea`. Migration setzt sie auf `cnc` bzw. `konf1` aus dem Bestand.

**Nicht anfassen**
- Migrationen server.py 1327 und 1347 (historische Upgrade-Pfade; Upgrade-Test 313/313 muss grün bleiben).
- Projekt-Systembereiche (server.py 1556, 2807, 3774).

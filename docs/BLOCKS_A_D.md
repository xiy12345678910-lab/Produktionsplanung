# Abnahme A–D (V12.19.0)

## A: FA und Datenbestand

`fa`/`faNumber` sind die aktuelle Fertigungsauftragsnummer. Die additive Migration übernimmt alte `fs`-Werte unverändert und lässt alte Felder, IDs und unbekannte Erweiterungen bestehen. Verweise in Projekten, Werkzeugen/Formaten, Historie und gespeicherten Planständen bleiben erhalten. Alte Chat-Verweise `/FS…` werden weiterhin gefunden. Jeder FA trägt `sourceType` (`PROJECT`, `FRAME_ORDER`, `STOCK_REQUIREMENT`) und eine stabile `sourceId`; alle drei Quellen benutzen dieselbe Planung und Produktionsruntime.

Nachweis: `test_blocks.py` prüft Migration, Referenzen, wiederholte Migration und alle Quellen; `e2e_chat.mjs` prüft alte `/FS`-Suche; der strikte Upgrade-Test vergleicht Altbestände feldweise und einschließlich zweitem Update/Rollback.

## B: Personal und Planung

Teilzeit begrenzt die Nettozuordnung mit Wochenstunden, individuellen Arbeitstagen und Tagesstunden. Abwesenheiten entziehen Kapazität. Eine Wochenversetzung verändert den Einsatzbereich, erteilt aber keine Maschinenqualifikation; nach der Woche gilt wieder der Stammbereich. Zuordnungen und Laufpläne führen `laneIndex`. Ein Mitarbeiter besetzt nur seinen zugeordneten Parallelplatz.

Linien dürfen mit weniger Mitarbeitern als ihrer maximalen Besetzung planen, sofern die Mindestbesetzung erfüllt ist. Mit P18 bleibt die Sollarbeit in Personenstunden konstant, z. B. 40 Ph bei 2 Mitarbeitern = 20 h, bei 4 = 10 h. Vorwärts-/Rückwärtsplanung, Vorschau, Freigabe und laufende Planung verwenden denselben Scheduler. Die Runtime friert Personal-/Kalenderdaten je Start-/Fortsetzungsphase für nachvollziehbare Iststunden ein; spätere Personaländerungen schreiben vergangene Zeiten nicht um.

Nachweis: `test_blocks.py`, `test_effort.py`, `e2e_effort.mjs`, `e2e_parallel.mjs`, `e2e_gate_lines.mjs` einschließlich Teilzeit, qualifizierten Versetzungen, 2/4 Mitarbeitern, Pausen und unterschiedlichen Parallelplätzen.

## C: AV, Bereiche und Rechte

AV legt Projekte und mehrere FA an, pflegt Menge, AB, Liefertermin und Stunden. Die Bereichsleitung übernimmt Ressourcen-/Termin-/Prioritätsplanung. Konfektions-/Personenstunden bleiben AV-Vorgabe; fehlende Stunden und offene Vorgänger werden sichtbar und verhindern einen Produktionsstart. Das Projekt zeigt FA-Status, Fortschritt, Zeiten und Terminrisiken aus den verknüpften Arbeitsgängen.

Bereichsgebundene Rollen erhalten vom Server nur ihre erlaubten Datensätze; unberechtigte Änderungen fremder Bereiche werden auch bei manipulierten HTTP-Anfragen abgelehnt. Bereichsrollen ohne gültigen Bereich bekommen keine Produktionsdaten. CSV und PDF folgen derselben Sicht und enthalten FA-/AB-/Projektbezüge. Globale Rollen benötigen kein Bereichsfeld. Nur Admin löscht andere Benutzer nach Bestätigung; aktive Zuordnungen und offene Zuständigkeiten müssen zuvor gelöst werden. Sessions werden ungültig, historische Urheber bleiben bestehen.

Der Server ergänzt strukturierte Auditänderungen mit Zeit, Benutzer, Rolle, Bereich, FA/Projekt, Feld und Alt-/Neuwert. Die normale UI speichert ihren Verlauf zusätzlich; serverseitige Änderungen und Produktionsaktionen sind unabhängig davon protokolliert.

Nachweis: `test_blocks.py` für HTTP-Rechte und Audit; `e2e_blocks.mjs` für AV-Projekt → mehrere FA → Bereichsplanung, CSV/PDF und Benutzerverwaltung; bestehende Rollen-, Projekt- und Exporttests.

## D: Gemeinsame Produktion

Start, Pause, Fortsetzen, Teilfertig, Fertig und Abbruch laufen atomar auf dem Server. Wiederholte Requests mit derselben ID liefern die bereits verbuchte Aktion, auch nach Neuladen oder Archivierung des FA. Gleiche ID mit geändertem Inhalt wird abgelehnt. Teilmengen kumulieren Gutmenge/Ausschuss und verringern die Restmenge. Die Fertigmeldung übernimmt alle vorherigen Teilmengen in die Historie. Bei Rahmen-/Bestandsquellen erhöht ausschließlich die Gutmenge den physischen Bestand; Projekt-/Quellenereignisse bleiben für die spätere Bedarfslogik erhalten.

Die Historie enthält aktive Zeit ohne Pausen, Maschinen-/Personenstunden und die eingefrorene Besetzung. Serverzeit, aktuelle Arbeitszeit, Vorgänger und Parallelplatz werden beim Start geprüft. Palettenetiketten stehen vor, während und nach der Produktion bereit: Vorlage, Adressen, Menge, fortlaufende Nummer, MHD aus Haltbarkeit und druckbarer Code-39-Barcode. Ein Etikett bucht keine Produktionsmenge.

Nachweis: `test_blocks.py` für gleichzeitige/repetierte HTTP-Aktionen, Mengen, Ausschuss, Pausen, Quellen-/Bestandsbuchung und Zeit; `e2e_blocks.mjs` für den realen Produktionsablauf und Etiketten.

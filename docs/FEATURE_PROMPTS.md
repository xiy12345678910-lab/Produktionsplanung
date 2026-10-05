# Feature-Prompts – Produktionsplanung (ab V12.10.2)

## To-do-Status

| Reihenfolge | Prompt | Feature | Status |
|---|---|---|---|
| 1 | 5 | Hallenmodus (Hell/Dunkel/Halle) | erledigt V12.11.0 (Kiosk-Schalter optional, nicht umgesetzt) |
| 2 | 3 | Undo/Redo | erledigt V12.12.0 |
| 3 | 4 | Browser-Benachrichtigungen | erledigt V12.13.0 (Glocke; Browser-Meldung nur bei https, Erklärtext bewusst weggelassen) |
| 4 | A | Firmen-Config `config\firma.json` außerhalb des Pakets (docs/PRODUKT_MULTI_FIRMA.md) | erledigt V12.14.0 (Datei, Migration, Validierung, Update/Rollback/Umzug/Vorabtest/Backup), Korrektur Long-Poll V12.14.1, Rest V12.15.0 (API, Firmenprofil-UI, Arbeitgeberdaten aus dem Paket; Export/Import-ZIP bewusst offen), Datenerhalt-Test + Windows-Deploy in CI V12.15.1 |
| 5 | B | Branchenvorlagen, Module | erledigt V12.16.0 (Vorlagen als Paketdateien vorlage_*.json, Anwenden nur ergaenzend, Module ein/aus mit Server-Sperre MP-MOD-001, Bereichs-Eigenschaften, Begriffe/Rollenbezeichnungen; offen: Projektbereiche aus Config, Modul export) |
| 6 | C1 | Einrichtungsassistent | offen |
| 7 | 17 | Qualifikationsmatrix | offen |
| 8 | 16 | Personalbedarf je Parallelplatz | offen |
| 9 | 14 | Automatische Nachkalkulation | offen |
| 10 | 7 | KPI-Dashboard GF | offen |
| 11 | 12 | Liefertermin-Vorschlag Vertrieb | offen |
| 12 | – | **Stopp: Windows-Installation beim Arbeitgeber** (Erstinstallation/Update live, danach weiter) | offen |
| 13 | E | Installer-Branding, signierte Updates | offen |
| 14 | G | Vorbereitung gehosteter Betrieb | offen |
| 15 | 11 | Was-wäre-wenn-Szenarien | offen |
| 16 | C2 | Demo-Daten, Feiertage je Bundesland | niedrige Prio |
| 17 | D | Lizenz (erst nach Klärung der Code-Rechte) | niedrige Prio |
| 18 | F | Mehrsprachigkeit | niedrige Prio |

Voraussetzung erledigt: Hotfix V12.10.2 (docs/AUDIT.md V-02, V-03, V-04, V-12), u. a. `workDaysBetween` mit Feiertagen (für Prompt 12).

Jeder Prompt ist eigenständig: Er wird in eine neue Claude-Code-Session kopiert, und zwar **zusammen mit dem gemeinsamen Vorspann**.
Reihenfolge: 5 → 3 → 4 → A → B → C1 → 17 → 16 → 14 → 7 → 12 → **Windows-Installation** → E → G → 11 → C2 → D → F.

---

## Gemeinsamer Vorspann (vor jeden Prompt setzen)

```
Repo: xiy12345678910-lab/Produktionsplanung, Branch: claude/new-session-95ro2l (Stand V12.10.2).
Architektur: server.py (Python-Stdlib-HTTP-Server + SQLite, Datenstand als JSON-Blob,
Validierung in validate_state, Rechte je Rolle in *_change_allowed), index.html (Single-File-SPA,
Vanilla JS in einer IIFE, Scheduler calcSchedule, Ansichten render*, Sync über PUT /api/state
mit Revision), Tests in tests/ (Python-Tests ohne Browser + Playwright-E2E *.mjs).
Lies zuerst docs/UEBERGABE.md, docs/AUDIT.md, RELEASE_NOTES.txt und FEHLERCODES.txt.

Regeln:
- UX zuerst: Bedienung muss ohne Anleitung klar sein. Wenig Text, eindeutige Symbole mit Tooltip,
  sinnvolle Defaults, Aktion dort, wo der Nutzer gerade arbeitet; keine Erklärblöcke, Meldungen max. 1 Satz.
- Nur Python-Standardbibliothek, keine npm-Abhängigkeiten im Produktivcode, alles bleibt in index.html/server.py.
- Neue Felder im Datenstand: Default in der Client-Normalisierung UND in einer idempotenten
  Server-Migration (Muster migrate_state_v1280) UND in validate_state prüfen.
- Rechte serverseitig durchsetzen (die passende *_change_allowed-Funktion), nicht nur im Client.
- Ausgaben immer per escapeHtml. Zeiten in Europe/Berlin. Texte deutsch, Fehlercodes MP-<BEREICH>-<NNN>
  neu in FEHLERCODES.txt.
- APP_VERSION erhöhen (Minor je Feature), RELEASE_NOTES.txt, README.md, docs/UEBERGABE.md nachziehen.
- Tests: eigener E2E-Test tests/e2e_<feature>.mjs und Server-Tests für Validierung und Rechte. Danach
  test_v128.py, test_v129.py, test_regression.py, ui_smoke.mjs und e2e_roles.mjs grün laufen lassen
  und Ergebnisse mit Zahlen berichten.
- Commit mit Versionspräfix ("V12.x.y: …"), Push auf den Branch, kein PR ohne Auftrag.
```

---

## Prompt 3 – Undo/Redo

```
Feature: Undo/Redo (Strg+Z / Strg+Y bzw. Strg+Umschalt+Z) für Planungsaktionen.

Umfang:
1. Befehls-Stack im Client, je Benutzer und Sitzung, max. 50 Schritte, nur im Speicher (nicht in localStorage).
2. Rückgängig machbar: Board-Drag&Drop (Maschine/Tag/Start), Priorität ↑/↓ und Drag in der Auftragsliste,
   Statuswechsel (geplant/freigegeben/pausiert), Fixtermin setzen/lösen, Auftrag anlegen/löschen,
   Personalzuordnung, Abwesenheit anlegen/löschen, Format-Layout verschieben.
   NICHT rückgängig machbar: Produktion starten/fertig melden, Benutzer/Rechte, Import, Planstand wiederherstellen.
3. Umsetzung: Vor jeder Aktion den betroffenen Teilbaum (Datensätze per id) als Snapshot sichern,
   nicht den ganzen Datenstand. Beim Undo diese Datensätze zurückschreiben und den normalen save()-Weg gehen,
   damit Server-Rechte und Revision gelten.
4. Konflikt: Hat inzwischen ein anderer Benutzer denselben Datensatz geändert (Vergleich mit dem Stand nach
   der eigenen Aktion), Undo abbrechen und Toast „Inzwischen von <Benutzer> geändert – Rückgängig nicht möglich“.
   Den Stack-Eintrag verwerfen.
5. UI: Buttons ↶ ↷ in der Kopfzeile mit Tooltip „Rückgängig: <Aktion>“; deaktiviert bei leerem Stack.
   Tastenkürzel nicht in Eingabefeldern abfangen (dort bleibt das Browser-Undo).
6. Der Stack wird bei Logout, Rollenwechsel und Server-Reload (426/Versionswechsel) geleert.

Abnahme: E2E tests/e2e_undo.mjs – Verschieben → Undo → Position wie vorher; Redo; Undo nach Fremdänderung
wird abgelehnt; Kürzel im Input-Feld lösen kein App-Undo aus; Rollen ohne Schreibrecht sehen keine Buttons.
```

---

## Prompt 4 – Benachrichtigungen im Browser

```
Feature: Browser-Benachrichtigungen (Notification API) und Benachrichtigungszentrale.

Ereignisse (je Benutzer abonnierbar, Defaults in Klammern):
- @Erwähnung oder Direktnachricht im Messenger (an)
- Auftrag freigegeben / zurückgezogen in meinem Bereich (an für Bereichsrollen)
- Liefertermin gefährdet: Plan-Ende nach dueDate oder Puffer < 1 Arbeitstag (an für PM, Vertrieb, GF, Bereich)
- Maschinensperre oder Abwesenheit mit Auswirkung auf geplante Aufträge (an für Bereich, AV)
- Leiharbeiter-Genehmigung angefragt / entschieden (an für GF bzw. Antragsteller)

Umsetzung:
1. Serverseitig eine Tabelle notifications(id, username, kind, ref_type, ref_id, text, created_at, read_at),
   mit Aufbewahrung 30 Tage (wie Chat). Die Ereignisse werden beim PUT /api/state aus dem Diff alt→neu
   erzeugt (Freigabe, Sperre, Abwesenheit). Gefährdete Liefertermine meldet der Client nach calcSchedule
   per POST /api/notifications/derived (der Server dedupliziert je Auftrag und Tag).
   Erwähnungen und DMs kommen aus den Chat-Endpunkten.
2. API: GET /api/notifications?since=…, POST /api/notifications/read, GET/PUT /api/notifications/prefs.
   Rechte: Jeder sieht nur die eigenen Benachrichtigungen. Kein Inhalt aus Bereichen, die die Rolle nicht sehen darf.
3. Client: Glocke 🔔 mit Zähler in der Kopfzeile, Liste mit Direktlink (gleiche Mechanik wie /-Verweise im Chat).
   Nach Opt-in Notification.requestPermission() und echte Browser-Notification, wenn document.hidden.
   Abruf im bestehenden Long-Poll mitliefern, kein weiteres setInterval.
4. Einstellungen je Benutzer: Ereignisarten an/aus, Ruhezeit (z. B. 20–6 Uhr).
5. Hinweis: Notification API braucht einen sicheren Kontext. Über HTTP im LAN funktioniert nur die Glocke.
   Das im UI erklären und ohne Fehler auf die Glocke allein zurückfallen.

Abnahme: tests/e2e_notifications.mjs (Erwähnung → Zähler +1 → Klick öffnet Kanal → gelesen;
Freigabe erzeugt Eintrag nur für Berechtigte; Prefs aus → kein Eintrag) + Server-Test für Rechte/Dedupe/Purge.
```

---

## Prompt 5 – Hallenmodus (Dark/Kontrast, große Schrift)

```
Feature: Darstellungsmodi „Hell“ (heute), „Dunkel“ und „Halle“ (hoher Kontrast, große Schrift,
für Hallenmonitor/Tablet).

Umsetzung:
1. Alle Farben im CSS auf CSS-Variablen in :root umstellen (heute viele Hex-Werte direkt, z. B. #7b8799).
   Drei Themes über [data-theme="light|dark|hall"] auf <html>. Die Akzentfarbe aus data.ui.accent bleibt erhalten.
2. „Halle“: Grundschrift 18 px, keine Schrift unter 14 px, Kontrast ≥ 7:1 (WCAG AAA) für Text,
   Board-Zellen höher, Statusfarben zusätzlich mit Symbol (nicht nur Farbe).
3. „Dunkel“: Kontrast ≥ 4,5:1 überall, auch Badges, Konflikt-Tray, Chat, Formatlayout, Druck bleibt hell.
4. Auswahl pro Gerät (localStorage, try/catch), nicht im Server-Datenstand. Default folgt
   prefers-color-scheme. URL-Parameter ?theme=hall&view=overview&dept=<id> für feste Hallenbildschirme.
5. Optional: „Kiosk“-Schalter im Hallenmodus mit Auto-Refresh, ausgeblendeter Navigation und nur lesend.
6. Alle 10/11-px-Schriften (ca. 75 Stellen) auf Variablen umstellen.

Abnahme: tests/e2e_theme.mjs – alle Ansichten je Theme ohne JS-Fehler. Ein automatischer Kontrast-Check
(getComputedStyle, Kontrastformel) über alle sichtbaren Textknoten liefert 0 Verstöße < 4,5:1 (dunkel)
bzw. < 7:1 (Halle). Screenshots in den Scratchpad legen.
```

---

## Prompt 7 – KPI-Dashboard für die GF

```
Feature: KPI-Dashboard im GF-Cockpit (Ansicht „gf“, renderGF) auf Basis vorhandener Daten
(Historie/Fertigmeldungen, Ist-Laufzeiten, Plan aus calcSchedule, Kapazität je Bereich/KW).

KPIs (Filter: Bereich, Zeitraum 4/12/26 Wochen, Kunde):
1. Liefertreue % = fertig bis dueDate / fertig mit dueDate (Tagesgenauigkeit Europe/Berlin).
2. Plan-/Ist-Abweichung Stunden je Auftrag und Summe, Abweichung in %.
3. Rüstanteil % = Rüstzeit / (Rüst- + Laufzeit) je Maschine.
4. Auslastung % je Bereich und KW = geplante Stunden / verfügbare Stunden (Schichtmodell, Sperren, Feiertage).
5. Ausschussquote % (Gut/Ausschuss aus der Fertigmeldung).
6. Durchlaufzeit Projekt: Annahme → letzter Fertig-Termin, Median und P90.
7. Offene gefährdete Aufträge (Anzahl, Liste mit Link).

Darstellung: KPI-Kacheln mit Vorperiode und Trend (Sparkline 12 Wochen), darunter eine Tabelle je Bereich.
Charts als Inline-SVG ohne externe Bibliothek, Farben aus den Theme-Variablen.
Drilldown: Klick auf eine Kachel öffnet die zugrunde liegende Auftragsliste.

Export: GET /api/export/kpi.csv und /api/export/orders.csv (Ortszeit, Semikolon, UTF-8-BOM, Formelschutz:
Zellen mit =+-@ am Anfang bekommen ein '). Nur Rollen GF/Admin. Für Power BI ist das eine Fakten-Tabelle je Auftrag
(Plan-/Ist-Stunden, dueDate, finishedAt, Maschine, Bereich, Kunde).

Berechnung in einer reinen Funktion computeKpis(data, schedule, range), mit calcSchedule je Revision memoized.

Abnahme: Server-Test für Rechte am Export; tests/e2e_kpi.mjs mit Testdaten und festen Erwartungswerten
(z. B. 3 von 4 pünktlich = 75,0 %) inkl. Fertigmeldung um 00:30 Uhr (Zeitzonenfall).
```

---

## Prompt 11 – Was-wäre-wenn-Szenarien

```
Feature: Szenarien – eine Kopie des Planstands, in der man frei verschieben und simulieren kann,
ohne den Live-Plan zu ändern.

Bestand: data.planVersions existiert (Planstände sichern/wiederherstellen, Audit-Ansicht). Laut Audit
wird es im Client nicht mehr erzeugt, ist aber serverseitig beschreibbar. Darauf aufbauen oder bewusst
ersetzen und das begründen.

Umfang:
1. „Szenario anlegen“ (GF, AV, Admin, Bereichsleitung für den eigenen Bereich). Es kopiert workSteps, machines,
   Sperren, Abwesenheiten und Personalzuordnungen in eine eigene Server-Tabelle
   scenarios(id, name, owner, base_revision, data_json, created_at). Szenarien laufen NICHT über PUT /api/state.
2. Im Szenario-Modus (deutliches Band „SZENARIO: <Name> – keine Live-Daten“): Board, Auftragsliste,
   Personal, Sperren bearbeitbar; Speichern geht nur ins Szenario.
3. Vorlagen-Aktionen: „Eilauftrag einfügen“ (Dauer, Maschine, Termin), „Maschine fällt aus von–bis“,
   „Mitarbeiter fehlen“ (n Personen, Bereich, Zeitraum), „Zusatzschicht“.
4. Vergleich Live ↔ Szenario: Liste der Aufträge mit Plan-Ende-Delta, neu gefährdete und entspannte
   Liefertermine, Auslastungsdelta je Bereich/KW. calcSchedule wird unverändert auf die Szenario-Daten angewendet.
5. „Übernehmen“ (nur GF/Admin bzw. Bereich für eigenen Bereich): Die geänderten Datensätze werden als ein
   PUT gegen die aktuelle Live-Revision gemergt. Wurde ein Datensatz live seit base_revision geändert, gibt es
   eine Konfliktliste zum Abhaken statt einer Übernahme.
6. Szenarien verfallen nach 30 Tagen, wenn sie nicht angeheftet sind.

Abnahme: tests/e2e_scenarios.mjs – Szenario anlegen, Maschine ausfallen lassen, Delta zeigt verschobene
Aufträge, Live-Plan unverändert (Revision gleich), Übernahme mit und ohne Konflikt; Server-Test Rechte.
```

---

## Prompt 12 – Liefertermin-Vorschlag für den Vertrieb

```
Feature: Frühestmöglicher realistischer Liefertermin bereits in der Anfrage-/Angebotsphase eines Projekts.

Bestand: Projekte mit Phasen, Ablauf-Vorlagen (processTemplates) mit Vorwärts-/Rückwärtsplanung,
„An Liefertermin ausrichten“, calcSchedule, Kapazität je Bereich und KW.

Umsetzung:
1. Im Projektfenster (Phase Anfrage/Angebot) den Button „Termin vorschlagen“. Eingabe: Ablauf-Vorlage,
   Mengen bzw. Stunden je Prozess, gewünschter Termin (optional).
2. Berechnung: Die Prozesse der Vorlage werden als temporäre Aufträge vorwärts ab heute (bzw. ab
   frühestem Materialtermin) in eine KOPIE des aktuellen Plans eingeplant: calcSchedule auf einem Klon,
   bestehende Aufträge behalten Priorität, der neue Auftrag hängt hinten an.
   Ergebnis: frühestes Ende je Prozess und Gesamttermin, plus Puffer-Empfehlung
   (konfigurierbar, Default 2 Arbeitstage).
3. Zweite Variante „mit Priorität“: Wie wäre der Termin, wenn der Auftrag vor die anderen gesetzt wird?
   Welche Aufträge würden sich verschieben, und wären deren Liefertermine danach gefährdet?
4. Ausgabe: Termin, Engpass-Bereich und Engpass-KW, Ampel gegenüber dem Wunschtermin. „Übernehmen“ schreibt
   nur project.dueDate bzw. proposedDate. Es entstehen keine Aufträge im Live-Plan.
5. Feiertage und Betriebsferien, Sperren und Schichtmodell berücksichtigen (workDaysBetween korrigieren,
   siehe docs/AUDIT.md F-M2).
6. Rechte: Vertrieb, PM, GF, Admin. Vertrieb sieht nur Termin und Engpass, keine fremden Auftragsdetails.

Abnahme: tests/e2e_due_proposal.mjs mit deterministischen Testdaten (fester „heute“-Zeitpunkt):
Termin = erwarteter Tag, Feiertag verschiebt um 1 AT, Prioritätsvariante listet betroffene Aufträge,
Live-Revision unverändert.
```

---

## Prompt 14 – Automatische Nachkalkulation anhand Stunden

```
Feature: Automatische Nachkalkulation in Stunden (Plan vs. Ist) je Auftrag und Projekt, fortlaufend
aktualisiert, nicht nur als Auswertung nach Fertigmeldung.

Bestand: Ansicht „Auswertung & Nachkalkulation (Ist)“ (evalPanel) aus der Historie. Linien in
Personenstunden (Laufzeit × Besetzung), Maschinen in Maschinenstunden, Ist-Laufzeit ohne Pausen,
Gut/Ausschuss, Mitarbeiterstunden, Druck der Nachkalkulation. Darauf aufbauen und nichts duplizieren.

Umfang:
1. Je Auftrag automatisch berechnet, ohne Klick:
   - Plan: Rüst- und Laufzeit aus dem Auftrag/Takt, Maschinenstunden, Personenstunden (Besetzung bzw. staffRequired).
   - Ist: aus Produktion Start/Pause/Fortsetzen/Fertig (laufende Aufträge mit Ist bis jetzt),
     Maschinen- und Personenstunden, Stück/h, Ausschuss.
   - Abweichung h und %, Ampel (Schwellen in den Einstellungen, Default ±10 % gelb, ±25 % rot).
2. Rollup je Projekt (alle Prozesse/Aufträge) und je Bereich/KW.
3. Optionale Bewertung in € (später zuschaltbar): Stundensatz je Maschine/Linie und je Personaltyp
   (eigen/Leiharbeiter). Felder im Datenstand vorsehen, Default leer = nur Stunden anzeigen.
   Nur GF/Admin sehen €.
4. Anzeige: Spalte „Ist/Plan h“ in der Auftragsliste, Block im Projektfenster neben dem PM·AV·Ist-Vergleich,
   die bestehende Auswertungsansicht nutzt dieselbe Funktion.
5. Eine reine Funktion calcPostCalc(order, history, data) als einzige Quelle für Auswertung, Druck,
   Projektfenster und den KPI-Export (Prompt 7).
6. Automatischer Abschluss: Bei Fertigmeldung wird der Ist-Snapshot im Historien-Eintrag fixiert
   (spätere Änderungen an Stammdaten verändern abgeschlossene Nachkalkulationen nicht).
   Serverseitig validieren, damit der Snapshot nicht nachträglich überschreibbar ist (außer Admin).

Abnahme: Python-Test für calcPostCalc-äquivalente Servervalidierung des Snapshots; tests/e2e_postcalc.mjs:
Auftrag mit Pause → Ist ohne Pause korrekt; Linie mit 3 Personen → Personenstunden ×3; Projekt-Rollup = Summe;
nach Stammdatenänderung bleibt der fixierte Snapshot gleich.
```

---

## Prompt 16 – Personalbedarf je Parallelplatz

```
Feature: Personalbedarf und Linienbesetzung je Parallelplatz statt je Ressource.

Bestand (V12.8.1): Parallelplätze je Maschine/Linie (machineLanes, Spalte „Parallel“, Anzeige „∥ n parallel“),
Scheduler und Server spurfähig (MP-PROD-010, MP-PLAN-058), staffRequired und crew je Maschine bzw. Linie,
Personalzuordnungen, Stammmaschine (homeMachineId). Bekannte Grenze laut docs/UEBERGABE.md §2:
„Personalbedarf/Linienbesetzung gelten je Ressource, nicht je Platz.“

Umfang:
1. Datenmodell: Je Platz (lane 1..n) optional staffRequired/crew überschreiben, sonst Wert der Ressource.
   Migration: Bestehende Werte bleiben auf Ressourcenebene gültig.
2. Bedarf je Schicht = Summe über die Plätze, die in dieser Schicht belegt sind (nicht n × Wert, wenn Plätze leer sind).
3. Personalzuordnung optional auf einen Platz (laneIndex), Validierung: nicht mehr Personen als Bedarf ohne Warnung,
   gleiche Person nicht gleichzeitig auf zwei Plätzen.
4. Linien-Personenstunden in Nachkalkulation und Auswertung je Platz (Schnittstelle zu Prompt 14).
5. GF-Cockpit Kapazität/Bedarf und Abwesenheits-Auswirkung (absenceImpact) rechnen je Platz.
6. Board: Unterbesetzung je Platz markieren (heute je Ressource).

Abnahme: Server-Tests (Validierung, Migration idempotent); tests/e2e_parallel.mjs erweitern:
2 Plätze, nur 1 belegt → Bedarf = Bedarf Platz 1; Platz 2 mit abweichender Besetzung; Doppelzuordnung
abgelehnt; GF-Bedarf stimmt.
```

---

## Prompt 17 – Qualifikationsmatrix (Ausbau des Bestands)

```
Feature: Qualifikationsmatrix ausbauen.

Bestand: employees[].skills = Liste von Maschinen-IDs (wer darf welche Maschine), homeMachineId muss in skills
stehen (Client-Normalisierung + server.py-Validierung), Freitextfeld „Funktion / Qualifikation“ im
Personalformular, Bedarfsberechnung nutzt skills bei Stammbesetzung. Bestehendes beibehalten und migrieren,
keine zweite Struktur daneben.

Umfang:
1. Matrix-Ansicht unter Personal: Zeilen = Mitarbeiter, Spalten = Maschinen/Linien des Bereichs,
   Zellen mit Stufen 0 = nein, 1 = eingewiesen (nur mit Begleitung), 2 = selbstständig, 3 = Trainer.
   Bearbeiten per Klick oder Tastatur, Sammelaktion für eine Spalte.
2. Datenmodell: skills bleibt als Liste (Stufe ≥ 2) für Rückwärtskompatibilität; neu skillLevels {machineId: 0..3}
   und optional validUntil (z. B. Staplerschein, Unterweisung) je Eintrag. Migration: vorhandene skills → Stufe 2.
3. Planung: Bei Personalzuordnung und Schichtbesetzung warnen, wenn keine Person mit Stufe ≥ 2 an der
   Maschine ist. Stufe 1 nur zusammen mit Stufe ≥ 2 zählen. Abgelaufene Qualifikation = Stufe 0
   plus Hinweis 30 Tage vorher (an Notification-Feature aus Prompt 4 anbinden, falls vorhanden).
4. Engpass-Auswertung: Maschinen mit < 2 qualifizierten Personen (Single Point of Failure) im GF-Cockpit
   und in der Matrix markieren.
5. Rechte: Bereichsleitung für den eigenen Bereich, Admin alles, andere nur lesen. Serverseitig prüfen.
6. Export der Matrix als CSV (Ortszeit, Formelschutz) und Druck A4 quer.

Abnahme: Server-Test (Migration skills → skillLevels idempotent, homeMachineId-Regel bleibt, Rechte);
tests/e2e_skills.mjs: Stufe setzen, Warnung bei Zuordnung ohne Qualifikation, Ablauf → Warnung,
Single-Point-of-Failure-Markierung.
```

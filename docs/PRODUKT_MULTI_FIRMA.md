# Produktkonzept: Produktionsplanung als Werkzeug für viele Firmen

Stand: 05.10.2026 · Basis: V12.12.0 (Arbeitsverzeichnis enthält bereits Teile von 12.13.0) · nur Konzept, kein Code geändert.
Fundstellen sind Zeilennummern im aktuellen Arbeitsstand; sie wandern, solange andere Agents `server.py`/`index.html` ändern. Vor dem Bau mit `grep` neu bestätigen.

## 0. Kurzfassung

- **Empfehlung:** (c) gestaffelt. Jetzt **eine Installation je Firma** (Konfiguration statt Code in `config\firma.json` außerhalb des Programmpakets, Firmenprofil, Branchenvorlagen, Module, Lizenz). Später „gehostet je Firma“ (eigene Datenbank je Firma hinter einem Router), **nicht** zeilenbasierte Mandantenfähigkeit.
- **Warum nicht gleich SaaS:** Der Datenstand ist ein einziger JSON-Blob (`state id=1`), jede Änderung schickt den ganzen Stand, es gibt nur HTTP im LAN und keine Betriebsumgebung für Fremddaten. SaaS hieße Vertragspartner nach Art. 28 DSGVO, 24/7-Betrieb, TLS, Mandantentrennung, Haftung. Das ist ein anderes Geschäft als Software verkaufen.
- **Update-sicher:** Alles Firmenspezifische liegt in `config\firma.json` (plus Logo, Lizenz) neben `data\`, nicht in `$MP_AppFiles`; Updates, Rollback, Vorabtest und Backup behandeln die Datei ausdrücklich (3.1, Paket A).
- **Wichtigste Schutzregel:** Der heutige Arbeitgeber-Stand wird zum Profil „Werbetechnik“ und verhält sich nach der Migration identisch. Jedes Paket hat dafür dieselbe Abnahme (Abschnitt 6.0).
- **Vorab klären:** Rechte am Code (Abschnitt 4.4), bevor irgendetwas verkauft oder beworben wird.

---

## 1. Bestandsaufnahme Firmenbindung

Belegt per `grep` am Arbeitsstand. Art: **K** = Konstante im Code, **D** = Default-/Seed-Daten, **T** = Text/Label, **L** = Logik hängt an der Bedeutung, **B** = Betrieb/Skripte.

### 1.1 Firmenidentität und Branding

| # | Datei · Fundstelle | Was | Art | Folge für Produkt |
|---|---|---|---|---|
| 1 | `index.html:1202` `CI_DEFAULT` | `company:'WERBETECHNIK *ART OF DISPLAY* GMBH'`, `color:'#E2382A'`, `font:'Arial'` | K/D | Wird Standard für jede neue Installation. Muss im Profil stehen, Default neutral. |
| 2 | `index.html:1202` | `logo:'data:image/jpeg;base64,/9j/…'`, ca. 17,5 KB Zeile, einziges eingebettetes Bild | K | Logo des Arbeitgebers steckt im Quelltext. Auslieferung an Dritte wäre eine Rechteverletzung. |
| 3 | `index.html:1123` (Kommentar), `1124`, `309` | „CD-Guideline WERBETECHNIK ART OF DISPLAY“, Panel „Firmen-CI für Kundenpläne … Rot #E2382A“ | T | Hinweistexte nennen den Arbeitgeber. |
| 4 | `index.html:1202, 1183` | Schreibweise `*…*` = fett nur für die Firmenzeile, `company.toUpperCase()` im Fuß | L | Branding-Syntax ist generisch brauchbar, der Default nicht. |
| 5 | `index.html:872–882` `printEvaluation`, `1158–1196` `buildCustomerPlanXlsx` | CI (Name, Logo, Farbe, Schrift) fließt in Nachkalkulations-Druck und Kunden-Excel | L | Gut: läuft schon über `ciSettings()` (`1125`). Nur Quelle der Defaults ändern. |
| 6 | `server.py:1692–1694` | Serverseitige Validierung des CI-Logos (PNG/JPG, 420 KB) | K | bleibt, ist generisch. |
| 7 | `index.html:11` `--accent:#1f5eff`, `452` `ui.accent`, `server.py:~270` `"accent": "#1f5eff"` | App-Akzentfarbe (blau), getrennt von der CI-Farbe (rot) | D | Zwei Farbwelten: UI-Akzent und Dokument-CI. Im Profil zusammenführen (UI-Akzent optional ableiten). |
| 8 | `index.html:6, 203, 293, 358, 360, 812, 833–835, 561` | Produktname „Produktionsplanung“ in Titel, Kopf, Druck, Dateinamen (`Produktionsplanung_V12_Backup_…`) | T | Name muss pro Kunde einstellbar sein (White-Label) und die Dateinamen folgen. |
| 9 | `index.html:203, 360` | Logo-Kachel „PP“ im Kopf und Login | T | Ersatz durch Firmenlogo/Kürzel. |
| 10 | Skripte, Doku, DB: `Maschinenplanung` (`MP_Common.ps1:4–13`, `server.py:39` `maschinenplanung.sqlite3`, `README_Windows.txt`, `RELEASE_NOTES.txt`) | Zweiter Produktname „Maschinenplanung“ neben „Produktionsplanung“ | K/B | Namensbruch. Vor einem Verkauf einen Produktnamen festlegen; die internen Namen (Task, Ordner, DB) nicht umbenennen, solange Live-Installationen laufen (siehe 3.7). |
| 11 | `index.html:372` `KEY='produktionsplanung_webapp_v12'`, `server.py:3346 u. a.` Cookie `mp_session` | Interne Schlüssel | K | unkritisch, aber bei mehreren Firmen am gleichen Rechner (Test) kollisionsanfällig. Nicht ändern. |

### 1.2 Feste Bereiche, Branchenlogik

| # | Datei · Fundstelle | Was | Art |
|---|---|---|---|
| 12 | `index.html:443–450`, `server.py:244–251` | Default-Bereiche: `cnc` „CNC“, `konf1/2/3` „Konfektion 1 – Thomsen / 2 – Keller / 3“, `screenprint` „Siebdruck“, `thermoforming` „Tiefziehen“ mit `planningType` MACHINE / LABOR_HOURS / PROCESS / CYCLE | D |
| 13 | `index.html:445–446`, `server.py:246–247` | **Personennamen** „Thomsen“, „Keller“ in Bereichsnamen (zusätzlich zu `index.html:433–435`, `server.py:234–236` Demo-Maschinen `m1–m3` auf `cnc`) | D |
| 14 | `index.html` 32 Zeilen, `server.py` 21 Zeilen | Fallback `departmentId \|\| 'cnc'` (z. B. `index.html:578, 582, 634, 956, 961, 1002, 1115, 1232–1234`; `server.py:344, 552, 1297, 1305, 1472, 1616, 1731, 1845, 2155, 2492, 2519, 2526, 2573`) | L |
| 15 | `index.html:634` `operatorConflict`/`648`, `operatorCapacity` (20 Treffer) | **Bedienerkapazität gilt nur für CNC**: `deptOfMachine(mid)!=='cnc'` → kein Konflikt. Gemeinsame Bediener-Schicht ist eine CNC-Eigenheit des Arbeitgebers. | L |
| 16 | `index.html:1232` `fmtDeptIds()`: `d.id==='thermoforming'\|\|d.formats===true`; `1274–1282, 807, 776, 765` | **Formate/Tiefziehen** erkennt den Bereich an der ID `thermoforming` (Alternative `formats===true` existiert schon). | L |
| 17 | `server.py:1613–1660, 2763, 2794, 2856` | Format-Validierung und Rechte „nur Tiefzieh-Leitung/-Stellvertretung und Admin“ | L |
| 18 | `index.html:923` `PROJECT_FIXED_AREAS` (sales, pm, engineering, calculation, purchasing, quality, av), `926 projectAreas()`, `server.py:1375` `PROJECT_FIXED_AREA_IDS`, `1463` | Feste Projekt-Bereiche (Vertrieb … Arbeitsvorbereitung). Die Liste steht **doppelt** (Client/Server). | K |
| 19 | `server.py:60–63` `ROLES`, `WRITE_ROLES`, `USER_MANAGER_ROLES`; `1500, 2578, 2653–2698, 2757–2779` | Feste acht Rollen inklusive „Arbeitsvorbereitung“, „Vertrieb“, „Projektmanagement“. Rechte-Regeln hängen daran. | K/L |
| 20 | `server.py:477–491` `DEFAULT_PM_TEMPLATES`: Vorlagen „Wiederholteil“, „Neuteil“, „Entwicklung / Muster“ mit Schritten `cnc` „Muster CNC“, `konf1` „Muster Konfektion“ | Ablauf-Vorlagen sind fest an Branche und Bereichs-IDs gebunden | D |
| 21 | `index.html:336, 654, 661, 846, 847, 928, 1034, 1049` | Feldname/Label **„WT / Projektnummer“, „WT 12345“** (`wt` in `workSteps`, `projects`, `history`; Server-Validierung nutzt `"wt"` an 17 Stellen). „WT“ ist die Nummer des Arbeitgebers (Werbetechnik). Dazu `ab` (Auftragsbestätigung). | T/K |
| 22 | `server.py:~520` Projektnummer `P-<Jahr>-<NNNN>`; Schichtvorlagen `index.html:436–442`, `server.py:237–243`: „1-Schicht Mo–Do 06:30–16:00“, „Freitag bis 11:45“, 2-Schicht 06:00–14:30 / 14:30–22:30 | Zahlenschema und Schichtmodell des Arbeitgebers als Seed | D |

### 1.3 Sprache, Zeit, Region

| # | Fundstelle | Was | Art |
|---|---|---|---|
| 23 | `index.html` 347 Zeilen, `server.py` 380 Zeilen mit Umlauten; `<html lang="de">` im Druck (`index.html:875`) | Alle Oberflächentexte, Fehlermeldungen und Server-Meldungen fest deutsch, **ohne i18n-Schicht**. Serverseitig werden deutsche Sätze als Fehlermeldung ausgeliefert (`return False, "MP-…", "…"`). | T |
| 24 | `release_gates.py:12–17` | Zeitzone: `MP_TIMEZONE`, Default `Europe/Berlin` (schon konfigurierbar per Umgebung, nicht per Profil). Dazu `requirements.txt` `tzdata` nur für Windows. | K |
| 25 | `index.html:315, 764` | **Feiertage** werden **nicht** berechnet, sondern als manuelle „Ausnahmen“ (`exceptions`) gepflegt. Gut für Produktneutralität, aber kein Bundesland-Vorschlag (Audit F-M2 ist erledigt, `workDaysBetween` nutzt die Ausnahmen). | D |
| 26 | `index.html` (CSV/Excel) und Kunden-Excel | Dezimal/Datum deutsch, CSV mit Semikolon und BOM | T |

### 1.4 Betrieb, Skripte, Updater

| # | Fundstelle | Was | Art |
|---|---|---|---|
| 27 | `README_Windows.txt:66` | Rechnername **`WTPC207$`** (Computerkonto des Arbeitgeber-Servers) in der Anleitung | B |
| 28 | `Update_von_GitHub.ps1:21` | `$Repo = 'xiy12345678910-lab/Produktionsplanung'` (privates GitHub-Konto des Entwicklers), ebenso `README_Windows.txt` 2b (Beispiel-Branch `claude/new-session-95ro2l`) | B |
| 29 | `MP_Common.ps1:4–19` | `ProgramData\Maschinenplanung`, Port `8765`, Task-/Firewallnamen `Maschinenplanung …`, `server.py:30–31` Host/Port | B |
| 30 | `README_Windows.txt`, `BENUTZER_KURZANLEITUNG.txt`, `FEHLERCODES.txt`, `RELEASE_NOTES.txt` | Dokumentation als Textdateien, deutsch, mit Hinweisen auf Nutzer „boensch“ (`RELEASE_NOTES.txt:158`) | T |
| 31 | `server.py:3825` `MP_ADMIN_PASSWORD`, `Setup_Windows.ps1`, `INSTALLIEREN_ALS_ADMIN.ps1` | Erst-Admin über Skript/Umgebung. Es gibt **keinen** Browser-Assistenten. | B |
| 32 | `tests/*` (z. B. `make_test_db.py:41`, `e2e_roles.mjs:57`) | Testdaten nutzen Arbeitgeber-IDs `cnc`, `konf1`, `thermoforming` | T |

### 1.5 Was schon neutral ist (nicht anfassen)

- Bereiche sind Daten: `data.departments` mit `planningType` und `active`, GF kann sie anlegen (V12.8.2). Maschinen/Linien, Schichtmodelle, Ausnahmen, Ablauf-Vorlagen (`processTemplates`) liegen im Datenstand, nicht im Code.
- `d.formats===true` als Schalter für Formatbereiche existiert bereits (`index.html:1232`).
- Rollen und Rechte sind serverseitig durchgesetzt; die CI läuft schon durch `ciSettings()`.
- Server nutzt nur die Standardbibliothek; Zeitzone per `MP_TIMEZONE`.

**Befund in einem Satz:** Die Bindung sitzt weniger in der Architektur als in **Seed-Daten, Fallbacks `'cnc'`, ID-Vergleichen (`thermoforming`, `cnc`) und Texten**. Das ist gut behebbar, ohne den Datenstand umzubauen.

---

## 2. Modellentscheidung

### 2.1 Bewertung

Skala: ++ gut, + brauchbar, – schlecht, – – blockierend.

| Kriterium | (a) Eine Installation je Firma (LAN/On-Prem) | (b) Mandantenfähig (SaaS, ein Server) | (c) gestaffelt: erst (a), dann „gehostet je Firma“ |
|---|---|---|---|
| Aufwand bis erster Verkauf | **M** (Profil, Vorlagen, Lizenz, Installer): ++ | **XL** (Auth-Härtung, TLS, Trennung, Betrieb, Abrechnung): – – | M, danach L bei Bedarf: ++ |
| Risiko für heutigen Arbeitgeber | gering, wenn Migration = Profil „Werbetechnik“: + | hoch: Umbau des Datenmodells berührt Live-Daten, Daten liegen plötzlich extern: – – | gering (a-Pfad zuerst): + |
| Vertriebsfähigkeit | KMU-Fertiger mit Hallennetz: LAN-only/Datenhoheit ist Argument: ++ | Einfache Einführung, aber viele Mittelständler fordern Datenhoheit: + | beides anbietbar: ++ |
| Datenschutz | Kunde bleibt Verantwortlicher; Anbieter berührt keine Daten außer im Support (dann AVV für Fernwartung): ++ | Anbieter ist Auftragsverarbeiter (Art. 28 DSGVO): AVV, TOMs, Unterauftragnehmer, Löschkonzept, ggf. Verzeichnis der Verarbeitung: – | wie (a); AVV erst beim Hosting: + |
| Betrieb/Support | Skaliert schlecht mit Kundenzahl (jede Installation eigen), aber Fehler bleiben lokal: – bis + | Ein Ort für Updates, aber 24/7-Pflicht, Monitoring, Backup-Verantwortung, Haftung bei Ausfall: – | Support-Prozess lernt erst auf (a): + |
| Technische Reife heute | Passt: Windows-Task, SQLite, Backup, Updater vorhanden: ++ | Passt nicht: ein Blob `state id=1`, Gesamtstand pro PUT, globale DB-Sperre (Audit F-H3, B-M), HTTP ohne TLS (B-H1), Login-Sperre je IP: – – | wie (a): ++ |
| Laufende Einnahmen | Wartungsvertrag/Lizenz pro Jahr: + | Abo, besser planbar: ++ | (a) mit Wartung, (b) als Aufpreis: + |

### 2.2 Kritische Prüfung der Erwartung „(a) jetzt, (b) später vorbereitet“

Die Richtung stimmt, drei Punkte sind zu schärfen:

1. **„(b) später“ sollte nicht „Mandanten-ID in jeder Zeile“ heißen.** Der Datenstand ist ein Blob je Installation. Der billigste und sicherste Weg zu mehreren Firmen auf einem Server ist **eine SQLite-Datei je Firma** (`data/<tenant>/maschinenplanung.sqlite3`), ausgewählt am Host-Namen oder Pfad. Das trennt Daten physisch, braucht keine Umstellung aller Abfragen und lässt Backup/Restore je Firma zu. Zeilenbasierte `tenant_id`-Spalten wären nur bei Millionen Zeilen oder tenantübergreifenden Auswertungen sinnvoll, beides trifft hier nicht zu. Vorbereitung = Datenpfad abstrahieren, `tenantId` im Firmenprofil und in der Lizenz, keine Schemaänderung der Fachdaten.
2. **(a) skaliert im Support, nicht im Code.** Wer 20 Installationen hat, braucht einen einheitlichen Versionsstand, Fernwartungsweg und Update-Kanal. Darum gehören Lizenz, Update-Kanal je Kunde und Diagnosepaket in die erste Ausbaustufe, nicht in „später“.
3. **Der größte Risikotreiber ist nicht das Modell, sondern der Rechtekreis.** Solange die Rechte am Code ungeklärt sind (Abschnitt 4.4), ist jede Investition in (a) wie (b) mit einem Haftungs- und Verfügbarkeitsrisiko behaftet. Das ist Schritt 0, nicht Fußnote.

Außerdem: (b) ist kein Muss, wenn die Zielgruppe LAN-Datenhoheit schätzt. Es ist eine Option für Kunden ohne eigene IT. Dafür reicht Variante „gehostet je Firma“ (eigener Prozess und eigene DB je Kunde), die Betrieb und AVV erst dann auslöst, wenn ein Kunde es kauft.

### 2.3 Empfehlung

**(c), konkret:**

| Stufe | Inhalt | Wann |
|---|---|---|
| 1 | (a) Eine Installation je Firma. Konfiguration statt Code. Firmenprofil, Bereichsvorlagen, Module, Einrichtungsassistent, Lizenz, Installer-Branding, i18n-Grundlage. Alles in einem Code-Stand, keine Kundenforks. | Pakete A–F |
| 2 | Vorbereitung für „gehostet je Firma“: Datenpfad pro Firma kapseln, `tenantId` in Profil und Lizenz, Host-Header → Firma. Kein produktiver Mehrfirmenbetrieb. | Paket G |
| 3 | Gehosteter Betrieb nur auf Nachfrage und erst nach TLS, Kontohärtung, AVV, Betriebskonzept. | frühestens nach 10 Installationen |

---

## 3. Zielarchitektur (Stufe 1 und 2)

### 3.1 Firmenkonfiguration außerhalb des Programmpakets (`config\firma.json`)

**Pflichtanforderung des Nutzers:** Alles Firmenspezifische liegt in Dateien außerhalb des Programmpakets, damit ein Update den Ist-Stand des Arbeitgebers nie überschreibt. Das ersetzt den früheren Plan „`data.company` im Datenstand“.

**Ist-Prüfung, was die Skripte heute anfassen** (`UPDATE_LIVE.ps1`, `MP_Common.ps1`, `Setup_Windows.ps1`, `Backup_Datenbank.py`, `Restore_Datenbank.ps1`):

| Vorgang | Verhalten heute | Folge für `config\` |
|---|---|---|
| Kopieren beim Update/Setup | Nur Namen aus `$MP_AppFiles` (`MP_Common.ps1:23–30`; `UPDATE_LIVE.ps1:104–107`, `Setup_Windows.ps1:28–31`) | `config\` steht **nicht** in `$MP_AppFiles` und wird nie überschrieben. Im Paket liegt nur `config\firma.beispiel.json`. |
| Entfernen | `$MP_ObsoleteFiles` (feste Liste) und `__pycache__` (`UPDATE_LIVE.ps1:109–110`) | `config\` nie in diese Liste aufnehmen. |
| Rollback-Sicherung | `Get-ChildItem -File … Copy-Item` (`UPDATE_LIVE.ps1:89`) kopiert nur **Dateien der obersten Ebene**; Ordner (`data\`, `backups\`) werden separat über das DB-Backup behandelt | **Lücke:** `config\` würde nicht gesichert. Muss ergänzt werden: `config\` rekursiv nach `update_backups\pre_V…\config\`. |
| Rollback | Stellt Dateien der obersten Ebene und die DB zurück (`:157–166`) | Muss `config\` aus dem Rollback-Ordner zurückspielen (Zeile ergänzen). |
| Umzug auf anderen Zielordner (`$migrating`) | Kopiert `backups\*`, `LAN_CONFIG.json`, `LAN_ADRESSEN.txt`, `BACKUP_ZIEL.txt` (`:96–99`) | `config\` muss in diese Liste (rekursiv). |
| Vorabtest `Invoke-MPPreflight` | Baut Temp-Ordner aus `$MP_AppFiles` + DB-Kopie (`MP_Common.ps1:272–278`) | Muss zusätzlich `config\` kopieren, damit die **neue** Version mit der **echten** Config startet. |
| Deinstallation | Behält `data\` und `backups\` (`Deinstallieren.ps1:13`) | `config\` ebenfalls behalten. |
| Tägliches Backup | `Backup_Datenbank.py` sichert nur `maschinenplanung_*.sqlite3` (`prune`, `backup`, `second_copy`) | Config mitsichern (6., unten). |

**Dateien** (neben `data\`, im Live-Ordner `C:\ProgramData\Maschinenplanung\`):

```
config\firma.json          Installations- und Firmenidentität (JSON, UTF-8)
config\logo.png|jpg        Firmenlogo (statt Base64 in der JSON; ≤ 420 KB wie server.py MP-CI-002)
config\lizenz.key          Lizenzschlüssel (Paket D)
config\firma.json.bak-<yyyymmdd-hhmmss>   automatische Sicherungen (letzte 20)
config\firma.beispiel.json ← einzige Datei im Programmpaket (config_beispiel\), wird nie aktiv
```

Zugriffsrechte wie `data\`: nur SYSTEM/Administratoren schreiben (`UPDATE_LIVE.ps1` sperrt den Live-Ordner bereits).

**Grenze Konfigurationsdatei ↔ Datenbank (Faustregel: Installations- und Firmenidentität in die Datei, Planungsdaten in die DB):**

| In `firma.json` (Datei) | Bleibt im Datenstand (DB) |
|---|---|
| Firmenname, Logo, Farben (CI, UI-Akzent), Schrift, Adresse, Fußzeile | Maschinen, Linien, Schichtmodelle, Ausnahmen/Feiertage, Sperren |
| Sprache, Zeitzone, Bundesland (Feiertagsvorschlag), Datums-/Zahlenformat | Bereiche mit Planungsart (`departments`), Bereichs-Eigenschaften (`formats`, `sharedOperators`) |
| Branchenvorlage (Name der Vorlage, Seed-Stand), aktive Module, Rollenbezeichnungen | Projekte, Aufträge, Historie, Personal, Chat |
| Begriffe/Benennungen (`terms`: Projektnummer „WT“, Auftragsnummer, Bereichsarten) | Ablauf-Vorlagen (`processTemplates`), Benutzer und Rechte (eigene Tabellen) |
| Lizenzschlüssel (oder Verweis auf `lizenz.key`), `tenantId`, Update-Kanal und -Quelle | Alles, was Benutzer im Betrieb ändern und was Rückgängig/Revision braucht |
| Projekt-Bereiche (`projectAreas`, die sieben Namen) | |

Begründung: Was ein Update nie anfassen darf und was Admin ohne Datenbank sichern, tauschen und ins Kundenpaket kopieren will, gehört in die Datei. Was Revision, Rechte pro Datensatz und Undo braucht, bleibt in der DB. Bereiche liegen bewusst in der DB, weil Maschinen, Aufträge und Historie per ID daran hängen.

**Schema (gekürzt):**

```json
{
  "schemaVersion": 1,
  "tenantId": "werbetechnik",
  "company": {"name": "WERBETECHNIK *ART OF DISPLAY* GMBH", "productName": "Produktionsplanung",
              "logoFile": "logo.png", "color": "#E2382A", "uiAccent": "#1f5eff", "font": "Arial",
              "address": "", "footer": ""},
  "locale": {"language": "de", "timezone": "Europe/Berlin", "holidayRegion": ""},
  "terms": {"projectNumber": "WT", "orderNumber": "FA", "roleLabels": {}},
  "template": "werbetechnik",
  "modules": {"projects": true, "formats": true, "personnel": true, "chat": true, "notifications": true, "postcalc": true, "kpi": true},
  "projectAreas": [{"id": "sales", "name": "Vertrieb"}],
  "license": {"file": "lizenz.key"},
  "update": {"channel": "stable", "source": ""}
}
```

(Das frühere Feldschema aus `data.ci` – Name, Logo, Farbe, Schrift, Adresse, Fuß – wandert hierher; `data.ci` bleibt als lesbarer Alias, bis Paket A den Client umgestellt hat. Jede Änderung, die der Admin im UI macht, schreibt in die Datei, nicht mehr in den Datenstand.)

**Schema-Version und Migration der Datei**

- `schemaVersion` steigt nur bei Strukturänderungen. Eine neue Programmversion **ergänzt fehlende Felder mit Defaults** (idempotent, Deep-Merge „nur fehlend“), **löscht nie** etwas und **behält unbekannte Felder** (wichtig bei Downgrade und Forks).
- Vor jeder Änderung durch das Programm: Kopie `firma.json.bak-<yyyymmdd-hhmmss>` (nur wenn sich Inhalt ändert; die letzten 20 bleiben). Schreiben atomar (`.tmp` + `os.replace`).
- Ist `schemaVersion` **größer** als die Programmversion kennt (Downgrade): Server startet, liest nur bekannte Felder, schreibt nichts zurück, Hinweisband für Admin.
- Mehrere Programmversionen schreiben nie gleichzeitig (ein Server-Prozess; Schreibzugriff über Sperre).

**Erstmigration Arbeitgeber**

Beim ersten Start der neuen Version **ohne** `config\firma.json` und mit vorhandener Datenbank (Bestand erkannt an `state`-Zeile mit Revision > 1) erzeugt `server.py` die Datei aus den heute fest eingebauten Werten und aus `data.ci`: Name, Logo (nach `config\logo.jpg` dekodiert), `#E2382A`, Arial, `terms.projectNumber="WT"`, `template="werbetechnik"`, alle Module an, `projectAreas` = die sieben heutigen, `tenantId="werbetechnik"`, Bundesland leer (Feiertage bleiben manuell, wie heute). Die Einbau-Konstanten stehen genau einmal in der Migration (siehe Paket A zur Frage der Auslieferung). Danach verhält sich alles wie heute. Neue leere Datenbank ohne Datei: Setup-Assistent (3.4) erzeugt sie.

**Prüfung beim Start und im Update**

- `server.py` validiert `firma.json` vor dem Port-Bind (Typen, Hex, Logo-Datei vorhanden und ≤ 420 KB, `tenantId`-Format, bekannte Vorlage, Modul-Schlüssel). Ungültig: **Start verweigert** mit klarer Meldung in Konsole und Log, Exit-Code ≠ 0, neuer Code `MP-CFG-001` (Datei nicht lesbar/kein JSON), `MP-CFG-002` (Feld ungültig, nennt Feldnamen), `MP-CFG-003` (`schemaVersion` zu neu, nur Warnung), in `FEHLERCODES.txt`. Die letzte gültige `.bak` wird in der Meldung genannt.
- `Invoke-MPPreflight` startet die neue Version mit Kopie von **DB und `config\`**; scheitert die Config-Prüfung, bricht das Update ab, bevor das Live-System berührt wird (bestehender Mechanismus, `UPDATE_LIVE.ps1:57–72`).
- Rollback stellt `config\` mit her (siehe Tabelle oben).

**Sicherung und Wiederherstellung**

- `Backup_Datenbank.py` legt zu jedem DB-Backup ein `firma_<zeitstempel>.zip` (Inhalt `config\` ohne `.bak`-Dateien) an, rotiert wie die DB-Backups (60) und kopiert es mit `second_copy` auch an `BACKUP_ZIEL.txt`.
- `Restore_Datenbank.ps1` nimmt optional `-MitConfig` (Standard: fragt, nimmt die zum DB-Stand passende ZIP, sichert die aktuelle Config vorher als `.bak`).
- Admin-UI System → Firmenprofil: **Export** (ZIP mit `firma.json` und Logo) und **Import** (validiert wie beim Start, legt vorher `.bak` an, ersetzt erst nach bestandener Prüfung). Endpunkte `GET /api/config/export`, `POST /api/config/import`, nur Admin, Import über `MP-CFG-…`.

**Auslieferung an Dritte:** Das Kundenpaket enthält `config\firma.beispiel.json` mit neutralen Werten. Arbeitgeberwerte stehen nur im Live-Ordner des Arbeitgebers (und in der Migration, 4.4 beachten).

### 3.1a Felder des Firmenprofils (Referenz)

Alle Felder des früheren `data.company`-Plans bleiben inhaltlich gleich, liegen aber in `firma.json`: `tenantId` (`[a-z0-9-]{3,32}`), `company.*` (CI), `locale.*` (Sprache, Zeitzone mit Vorrang `MP_TIMEZONE` > Datei > `Europe/Berlin`, Bundesland), `terms.*`, `template`, `modules`, `projectAreas`. Feiertagsvorschlag: Button „Feiertage des Landes eintragen“ erzeugt `exceptions` im Datenstand (bestehender Mechanismus), berechnet in der Standardbibliothek (Ostern + feste/bewegliche Tage je Land), überschreibt nie.

### 3.2 Branchen-/Bereichsvorlagen statt fester Bereiche

Vorlage = JSON-Paket (`templates/industry/<id>.json`, im Release enthalten, im Assistenten gewählt, danach **nur Seed**, nicht mehr laufend wirksam).

| Vorlage | Bereiche (`planningType`) | Besonderheit |
|---|---|---|
| `werbetechnik` | CNC (MACHINE), Konfektion 1–3 (LABOR_HOURS), Siebdruck (PROCESS), Tiefziehen (CYCLE, `formats:true`) | Exakt der heutige Seed **ohne** Personennamen (Namen kommen bei der Migration aus der DB). Ablauf-Vorlagen aus `DEFAULT_PM_TEMPLATES`. |
| `metall_cnc` | Zuschnitt (MACHINE), CNC-Fräsen (MACHINE), Drehen (MACHINE), Schweißen (LABOR_HOURS), Oberfläche (PROCESS), Montage (LABOR_HOURS) | Bedienerkapazität gemeinsam für alle MACHINE-Bereiche optional |
| `leer` | keine, Assistent legt einen Bereich an | Mindestprofil |
| `demo` | wie `metall_cnc` plus Beispielaufträge | nur zum Vorführen (4.2) |

Umbau der festen Bindungen (Fundstellen 12–20):

- Bereichs-**Eigenschaften** statt ID-Vergleiche: `formats:true` (ersetzt `id==='thermoforming'`), `sharedOperators:true` (ersetzt `!=='cnc'` in `operatorConflict`), `fallback` = erster aktiver Produktionsbereich statt `'cnc'`. Die Funktionen `fmtDeptIds`, `operatorConflict`, `deptOfMachine` und die Serverpendants lesen die Eigenschaft. Migration setzt `thermoforming.formats=true` und `cnc.sharedOperators=true`, damit der Arbeitgeber identisch bleibt.
- `PROJECT_FIXED_AREAS` und `PROJECT_FIXED_AREA_IDS` werden **eine** Quelle: `projectAreas` in `config\firma.json` (Liste `{id,name,kind}`), Default = die heutigen sieben. Serverseitig aus dem Datenstand gelesen statt Konstante, Fallback auf die sieben IDs, damit alte Stände validieren.
- Rollen: Bezeichnungen konfigurierbar (z. B. „Arbeitsvorbereitung“ → „Planung“), die **acht Rollen-IDs und ihre Rechte bleiben fest**. Neue Rollen sind kein Ziel von Stufe 1 (Rechtekern ist sicherheitskritisch und gut getestet: 374 + 151 Prüfungen).
- Feldbezeichnungen: `projectNumberLabel` ersetzt das fest codierte „WT“ in den Texten; der Datenschlüssel `wt` bleibt unverändert (keine Datenmigration, kein Risiko).

### 3.3 Module ein/aus

Gespeichert in `modules` von `config\firma.json`, serverseitig als Ein-/Aus-Flags geprüft (Endpunkte und Schreibrechte). Aus = Ansicht und Navigation verschwinden, Daten bleiben erhalten (wieder einschaltbar).

| Modul | Heute | Abhängigkeiten | Anmerkung |
|---|---|---|---|
| `planning` (Board, Aufträge, Scheduler) | Kern | – | immer an |
| `projects` (Projekte, Ablauf-Vorlagen, Kundenplan) | an | `planning` | Teil des Verkaufs-Pakets „Projekt“ |
| `formats` (Formate/Tiefziehen) | an für Bereich mit `formats:true` | Bereich mit Eigenschaft | bei Vorlage `werbetechnik` an |
| `personnel` (Personal, Abwesenheit, Besetzung) | an | `planning` | Voraussetzung für Qualifikationsmatrix |
| `chat` (Messenger) | an | – | Tabellen `chat_*` |
| `notifications` (12.13.0) | an | – | |
| `postcalc` (Auswertung & Nachkalkulation) | an | `planning` | später: Prompt 14 |
| `kpi` (GF-Cockpit-Kennzahlen) | an | `postcalc` | Prompt 7 |
| `export` (Excel/CSV/Druck) | an | – | |

### 3.4 Erst-Einrichtungsassistent im Browser

Läuft, wenn `setupDone` in `firma.json` nicht `true` ist **und** die DB neu ist (Migration setzt `setupDone=true` für Bestandssysteme, sonst würde der Arbeitgeber den Assistenten sehen).

Schritte (jeder überspringbar, sinnvolle Defaults, kein Erklärtext):

1. Admin-Konto (löst `MP_ADMIN_PASSWORD`/Skriptabfrage ab, Skript bleibt als Fallback)
2. Firma: Name, Logo, Farbe, Sprache, Zeitzone, Bundesland
3. Vorlage wählen (Karten mit Vorschau der Bereiche)
4. Module an/aus
5. Erste Maschine/Linie, Schichtmodell (Vorbelegung aus Vorlage)
6. Lizenzschlüssel eingeben (oder „Testversion“)
7. Fertig: Option „Demo-Daten laden“

Serverseitig: `GET /api/setup/status` (ohne Sitzung, nur `{setupDone}`), `POST /api/setup` (nur solange `setupDone=false`, danach 403). Der Assistent darf nicht aus dem Netz erreichbar bleiben, wenn schon ein Admin existiert.

### 3.5 Migration des Arbeitgeber-Bestands

Zwei Teile, beide idempotent und einmalig:

1. **Datei:** Erstmigration von `config\firma.json` (3.1, „Erstmigration Arbeitgeber“).
2. **Datenstand:** nur Bereichs-Eigenschaften (`thermoforming.formats=true`, `cnc.sharedOperators=true`, Paket B). `departments`, `machines`, `workSteps`, `history`, Rollen und Ausnahmen werden nicht verändert.

Rückweg: `data.ci` bleibt befüllt, `firma.json.bak-…` und `update_backups\pre_V…\config\` liegen vor. Beweis in jedem Paket: Identitätsnachweis (6.0).

### 3.6 Lizenzierung

Ziel: **offline prüfbarer, signierter Schlüssel** mit Firma, Laufzeit, Nutzerzahl, Modulen, nur Standardbibliothek.

**Einordnung der Verfahren**

| Verfahren | Stdlib? | Eignung |
|---|---|---|
| HMAC-SHA256 mit Geheimnis | ja (`hmac`) | **Ungeeignet**: Das Geheimnis liegt im ausgelieferten Quelltext. Wer es liest, erzeugt beliebige Schlüssel. |
| Ed25519 | nein (`ssl`/`hashlib` bieten es nicht) | Gewünscht, aber nur mit Eigenimplementierung (RFC-8032-Referenzcode, ca. 100 Zeilen, einmalige Verifikation ist schnell genug). Kryptografisch korrekt, aber fehleranfällig bei Eigenbau. |
| **RSA-3072, PKCS#1 v1.5 mit SHA-256, nur Verifikation** | ja: `pow(sig, e, n)` ist Python-Builtin, `hashlib.sha256` | **Empfehlung.** Verifikation sind ca. 25 Zeilen (Signatur → Zahl → `pow` → Padding-Struktur und Hash vergleichen). Der Server hält nur den **öffentlichen** Schlüssel (n, e). Signieren erfolgt beim Anbieter mit `openssl dgst -sha256 -sign` oder einem kleinen Tool (nutzt dort `cryptography`/OpenSSL, nicht im Kundenprodukt). |

Begründung: Asymmetrisch (Kunde kann nicht fälschen), keine Abhängigkeit, kleiner prüfbarer Code, ausgereiftes Standardverfahren, Schlüsselrotation über `kid` im Schlüssel möglich. Pflicht: gängige Prüfungen (Padding exakt vergleichen, nicht nur Hash-Suffix; Länge der Signatur = Modulusgröße; `e=65537`), Testvektoren aus `openssl`.

**Format:** `base64url(payload_json) + "." + base64url(signature)`, Payload:

```json
{"v":1,"kid":"2026-1","tenantId":"musterfirma","company":"Muster GmbH",
 "issued":"2026-11-01","validUntil":"2027-10-31","maxUsers":25,
 "modules":["projects","personnel","chat"],"edition":"standard"}
```

**Verhalten**

- Prüfung beim Start und täglich; Ergebnis im Status (`/api/license`, nur Admin sieht Details).
- Abgelaufen oder ungültig: **Lesemodus mit Hinweis, kein Datenzugriffsverlust**, Export und Backup bleiben möglich. Karenzzeit 30 Tage nach Ablauf. Nie Daten sperren oder löschen.
- `maxUsers`: zählt aktive Benutzer; Überschreitung blockiert nur das **Anlegen** weiterer Benutzer.
- Module: Lizenz schaltet Module frei; `modules` in `firma.json` kann sie nur abschalten.
- Zeitmanipulation: Systemuhr zurückdrehen lässt sich offline nicht verhindern. Gegenmaßnahme: letzten bekannten Zeitpunkt (max aus Revisionszeitstempeln) speichern; ist die Uhr davor, gilt Lizenz als abgelaufen. Reicht als Hürde, ist kein Kopierschutz.
- **Ehrliche Grenze:** Der Kunde hat den Python-Quelltext und kann die Prüfung entfernen. Lizenz ist daher vertragliche Basis mit technischer Erinnerung, kein DRM. Das ist bei On-Prem üblich und vertretbar.
- Arbeitgeber-Installation: Profil `werbetechnik` bekommt einen eigenen, unbefristeten Schlüssel („Hauslizenz“), oder die Prüfung wird für `tenantId='werbetechnik'` ohne Schlüssel „unlimited“ gesetzt. **Wichtig:** Eine fehlende Lizenz darf den Arbeitgeber nie ausschließen. Entscheidung dazu fällt mit der Rechteklärung (4.4).

### 3.7 Update-Kanal je Kunde, Installer-Branding

- Kanäle: `stable` (Standard), `beta`. Je Kunde in `LAN_CONFIG.json`/Lizenz (`channel`). Der Updater (`Update_von_GitHub.ps1`) liest Quelle aus Konfiguration statt aus dem festen Parameter `$Repo` (Fundstelle 28): `UpdateSource` = URL eines Manifests `{version, url, sha256, signature}` (mit demselben Signaturverfahren wie die Lizenz), Standardquelle wird die Anbieter-Domain, nicht das private GitHub-Konto. Die bestehenden Schutzmechanismen (fester Hash, Staging, Downgrade-Schutz, Vorab-Backup) bleiben.
- Installer: `INSTALLIEREN_ALS_ADMIN.ps1` bekommt Parameter `-Firma`, `-Produktname`, `-Port`, `-InstallBase`. Task-/Firewallnamen aus `$MP_ProductSlug` ableiten. **Für bestehende Installationen bleiben die Namen `Maschinenplanung …` unverändert**, sonst entstehen doppelte Tasks und Firewallregeln; `MP_LegacyTaskNames` fängt nur bekannte Altnamen.
- `README_Windows.txt`: Rechnername `WTPC207$` durch Platzhalter ersetzen, Beispiel-Repo/Branch entfernen.
- Doku-Auslieferung: Arbeitgeber-Hinweise (`boensch`, Rechnername) raus aus den ausgelieferten Texten; interne Doku (`docs/`, `RELEASE_NOTES.txt` historisch) nicht Teil des Kundenpakets, stattdessen kundenneutrale `CHANGELOG`.

### 3.8 Mehrsprachigkeit (de zuerst, en vorbereitet)

- Client: `t('key', {param})` mit Wörterbuch `I18N.de`/`I18N.en` im Single-File; Fallback immer `de`. Schlüssel nach Bereich (`board.title`, `err.MP-PLAN-058`). Mechanisches Herauslösen von ca. 347 Zeilen ist der Aufwand; **Zielzustand für Stufe 1: de vollständig über `t()`, en für Navigation, Login, Fehlermeldungen und Hauptansichten**, Rest fällt auf de zurück.
- Server: Fehler liefern `{code, msg_de, params}`; der Client übersetzt über `err.<code>`. Die bestehenden Codes `MP-<BEREICH>-<NNN>` sind die Schlüssel. Neue Codes zweisprachig anlegen. `FEHLERCODES.txt` bleibt Index.
- Zahlen/Datum: `Intl` mit `locale.language`; Zeit weiter Ortszeit der Firmenzeitzone. CSV: Semikolon/BOM und Dezimalkomma bleiben Standard in `de`, `en` bekommt Komma/Punkt-Umschalter.
- Druck: `<html lang>` aus Profil.
- Test: `tests/test_i18n.py` prüft, dass jeder `t('…')`-Schlüssel in `de` existiert (kein hartcodierter deutscher Satz in neuen Funktionen) und `en`-Lücken nur als Warnung.

### 3.9 Mandanten-ID als Vorbereitung für (b)

- `tenantId` (3.1) steht in `firma.json`, in der Lizenz und in `server_audit`-Einträgen.
- `server.py`: `DATA_DIR`/`DB_PATH` (Zeilen 38–39) laufen über `tenant_paths(tenant_id)`; Standard bleibt `data/maschinenplanung.sqlite3` für `werbetechnik` und Einzelinstallationen (**Pfad ändert sich nicht**).
- Später: Host-Header/Subdomain → `tenantId` → eigene DB. Kein Schema-Umbau der Fachdaten.
- Nicht tun: `tenant_id`-Spalten in allen Tabellen, gemeinsamer Blob für mehrere Firmen.

---

## 4. Produkt und Vertrieb (kurz)

### 4.1 Zielgruppe und Positionierung

- **Zielgruppe:** produzierende KMU mit 20–250 Beschäftigten, mehreren Fertigungsbereichen, Excel-/Whiteboard-Planung, keine Lust auf ERP-Großprojekt: Werbetechnik/Display, Metall/CNC, Kunststoff, Schilder-/Messebau, Lohnfertiger.
- **Aussage:** „Wochenplan, Projekte und Hallenmonitor in einem, läuft im eigenen Netz, Daten bleiben bei Ihnen.“
- **Differenzierung:** LAN-only/Datenhoheit, Einrichtung in unter einem Tag, Hallenmodus (V12.11.0), Undo, Rollenmodell (Geschäftsführung bis Viewer), Kundenplan als Excel.
- **Gegenseite ehrlich:** Keine ERP-/Schnittstellenanbindung heute, nur HTTP im LAN (Audit B-H1), keine Mobile-App, kein Dienstkonto statt SYSTEM (O-H2). Diese Punkte entscheiden bei IT-affinen Kunden.

### 4.2 Pakete und Preisideen (Hypothesen, am Markt zu prüfen)

| Paket | Inhalt | Richtwert Lizenz einmalig / Wartung p. a. |
|---|---|---|
| Basis | Planung, Board, Aufträge, Personal, bis 10 Nutzer | 1.900 € / 15 % |
| Standard | + Projekte, Kundenplan, Chat, Benachrichtigungen, bis 25 Nutzer | 3.900 € / 15 % |
| Professional | + Formate, Nachkalkulation, KPI, Szenarien, Qualifikationsmatrix, bis 60 Nutzer | 6.900 € / 15 % |
| Einrichtung | Vor-Ort/Fernwartung, Vorlage anpassen, Schulung (Tagessatz) | nach Aufwand |
| Zusatznutzer | je 5 Nutzer | 400 € / 15 % |

Alternative Abo-Form (jährlich je Paket) erst bei „gehostet je Firma“. Preise sind Annahmen, keine Marktanalyse.

### 4.3 Was für Verkaufsfähigkeit fehlt

| Thema | Stand | Nötig |
|---|---|---|
| Handbuch | `BENUTZER_KURZANLEITUNG.txt`, `README_Windows.txt` (intern, Arbeitgeber-Details) | Admin- und Benutzerhandbuch (PDF/HTML), nach Rollen, mit Screenshots aus Demo-Daten |
| Demo-Daten | nur `tests/make_test_db.py` | Vorlage `demo` + Schalter „Demo laden/entfernen“ (Paket C) |
| Installer | PowerShell-Skripte für Admins | Ein Installationspaket mit Prüfungen (Python, Port, Firewall), Branding-Parameter, Deinstallation (`Deinstallieren.ps1` vorhanden), optional MSI/Inno-Setup später |
| Support-/Update-Prozess | Updater existiert, Quelle ist privates GitHub-Konto | Signierte Release-Manifeste, Diagnose-Paket (Logs ohne Fachdaten), Ticket-/SLA-Regeln, Versions-/Kompatibilitätsmatrix |
| Sicherheitsniveau | Audit-Rest: TLS (B-H1), Dienstkonto (O-H2) | TLS mit interner CA als Option, Dienstkonto, Pen-Test-Nachweis; Sicherheitsdatenblatt |
| Haftung/AGB/Lizenzbedingungen | nicht vorhanden | Software-Lizenzvertrag, Haftungsbegrenzung, Wartungs-/SLA-Vertrag, Gewährleistung, Nutzungsrechte, Datensicherung als Kundenpflicht |
| AVV | nicht nötig bei reinem On-Prem, **nötig** bei Fernwartung mit Datenzugriff oder Hosting | Muster-AVV (Art. 28 DSGVO), TOMs, Subunternehmerliste, Verzeichnis |
| Datenschutz-Dokumente | – | Beschreibung Datenarten (Personal, Abwesenheit, Leistungsdaten → Betriebsrat/Mitbestimmung beim Kunden beachten, Löschfristen: Chat 30 Tage vorhanden) |
| Code-Eigentum | ungeklärt | siehe 4.4 |
| Marke/Name | „Produktionsplanung“ vs „Maschinenplanung“ | Namensrecherche, Domain, Markenschutz |
| Open-Source-Lizenzen | nur Stdlib, `tzdata` (Apache-2.0), Playwright nur Tests | Lizenzliste mitliefern |

### 4.4 Rechte am Code (Hinweis, keine Rechtsberatung)

Die App ist vermutlich im Rahmen des Arbeitsverhältnisses entstanden; vor jedem Verkauf müssen die Rechte am Code mit dem Arbeitgeber geklärt werden (§ 69b UrhG: Rechte an im Arbeitsverhältnis geschaffener Software liegen grundsätzlich beim Arbeitgeber), ebenso Fragen zu Arbeitnehmererfindungen und zur Mitnahme von Firmendaten wie Logo und Bereichsnamen.

Konsequenzen für die Reihenfolge: Paket A (Firmendaten raus aus dem Code) ist unabhängig von der Antwort richtig und reduziert das Risiko. Alles ab Paket D (Lizenz, Vertrieb) wird erst gebaut, wenn die Klärung vorliegt (z. B. Lizenzvereinbarung, Freigabe, Ausgliederung).

---

## 5. Einordnung der offenen Features

Alle sechs sind **fachlich produktneutral** (Planung, Personal, Kennzahlen). Arbeitgeberbezug steckt in Detailannahmen, die konfigurierbar werden müssen.

| Prompt | Feature | Neutral? | Anpassung am Prompt | Einordnung |
|---|---|---|---|---|
| **P17** | Qualifikationsmatrix | ja | (1) Stufenbezeichnungen und Anzahl (0–3) aus Profil statt festem Text; (2) Zertifikat-/Nachweis-Typen konfigurierbar (Beispiel „Staplerschein, Unterweisung“ nur Vorlage); (3) Spalten = Ressourcen des Bereichs, keine feste Bereichsliste; (4) Hinweis an Benachrichtigungen nur bei aktivem Modul `notifications`; (5) Modul `personnel` als Voraussetzung. | Nach Paket B (Module/Eigenschaften), vor Paket D. Position 1 der Feature-Gruppe. |
| **P16** | Personalbedarf je Parallelplatz | ja, aber stark an Scheduler und Server-Rechte | (1) „Plätze“ nicht an Siebdruck/Tiefziehen koppeln, sondern am Ressourcen-Feld `machineLanes`; (2) Rechenregel pro Ressource einstellbar (Bedarf je Platz ja/nein) mit Default „wie heute“, damit der Arbeitgeber unverändert bleibt; (3) Migration idempotent, Standard `perLane:false`. | Nach P17 (gemeinsamer Personal-Block). Position 2. |
| **P14** | Automatische Nachkalkulation | ja | (1) Währung und Dezimalformat aus Profil, € nur Beispiel; (2) Stundensätze als optionales Feld (leer = nur Stunden), Rolle GF/Admin; (3) Ampel-Schwellen konfigurierbar (Default ±10 %/±25 %); (4) Modul `postcalc`; (5) Snapshot-Regel unverändert. | Nach P16 (nutzt Personenstunden je Platz). Position 3. |
| **P7** | KPI-Dashboard GF | ja | (1) Kennzahlen einzeln abschaltbar (nicht jeder Fertiger meldet Ausschuss); (2) Zeitzone und Wochenstart aus Profil; (3) Export-Spaltennamen aus Sprache, Spaltenreihenfolge fest (Power BI); (4) Modul `kpi` hängt an `postcalc`; (5) Nichts mit Bereichs-IDs hartcodieren. | Nach P14. Position 4. |
| **P12** | Liefertermin-Vorschlag Vertrieb | neutral, **aber abhängig von Ablauf-Vorlagen**, die heute Branchenlogik tragen (`DEFAULT_PM_TEMPLATES` mit `cnc`/`konf1`) | (1) Eingabe nur über Vorlagen der aktiven Branchenvorlage; (2) Puffer Default 2 AT im Profil einstellbar; (3) Feiertage aus `holidayRegion` + `exceptions`; (4) Rechte unverändert (Vertrieb sieht nur Termin/Engpass); (5) Test mit Vorlagen von zwei Branchen, nicht nur Werbetechnik. | Nach Paket B (Vorlagen entkoppelt) und P14. Position 5. |
| **P11** | Was-wäre-wenn-Szenarien | ja | (1) Neue Tabelle `scenarios` im Datenpfad der Firma (nicht global), `tenantId` im Datensatz; (2) „Zusatzschicht“, „Maschinenausfall“ nutzen vorhandene Schichtmodelle, keine festen Zeiten; (3) 30-Tage-Verfall konfigurierbar; (4) Rechte nach Rolle, nicht nach Bereichs-ID. | Zuletzt (größtes Risiko, berührt Persistenz). Position 6, nach Paket G. |

Hinweis zur Zählung: 12.13.0 (Benachrichtigungen, Prompt 4) ist vorangestellt. Die Features erhalten ihre Versionsnummern erst beim Bau (nächste freie Minor-Version nach den Paketen, die zu dem Zeitpunkt fertig sind); die Reihenfolge ist verbindlich, die Nummern nicht.

Wenn der Arbeitgeber eines dieser Features dringend braucht: Reihenfolge zugunsten des Arbeitgebers ändern, aber **vor** dem Bau Paket A abschließen, damit neue Features nicht neue feste Bindungen erzeugen.

---

## 6. Umsetzungspakete

Reihenfolge und Abhängigkeiten:

| Paket | Version | Thema | Aufwand | Hängt ab von |
|---|---|---|---|---|
| A | 12.14.0 | Fundament: Firmenkonfiguration außerhalb des Pakets (`config\firma.json`), Firmendaten aus dem Code | M | 12.13.0 |
| B | 12.15.0 | Branchenvorlagen, Bereichs-Eigenschaften, Module | L | A |
| C | 12.16.0 | Einrichtungsassistent, Demo-Daten, Leerprofil | M | A, B |
| D | 12.17.0 | Lizenzierung (RSA-Signatur, Lesemodus) | M | A, **Rechteklärung 4.4** |
| E | 12.18.0 | Installer-Branding, Update-Kanal je Kunde, Doku-Trennung | M | A, D |
| F | 12.19.0 | Mehrsprachigkeit (de vollständig, en vorbereitet) | L | A (parallel zu B–E möglich, aber Zeilen-Konflikte mit B beachten) |
| G | 12.20.0 | Mandanten-Vorbereitung (Datenpfad, Host → Firma) | S | A, D |

Features P17, P16, P14, P7, P12, P11 laufen zwischen C/D und danach (Abschnitt 5), ihre Versionen werden fortlaufend vergeben. Die Pakete C bis G können dadurch auf spätere Minor-Versionen rutschen; maßgeblich ist die Reihenfolge.

### 6.0 Gemeinsamer Vorspann und Abnahmeregel (vor jedes Paket setzen)

```
Repo: xiy12345678910-lab/Produktionsplanung, Branch: claude/new-session-95ro2l.
Lies zuerst docs/PRODUKT_MULTI_FIRMA.md (dieses Konzept), docs/UEBERGABE.md, docs/AUDIT.md, RELEASE_NOTES.txt, FEHLERCODES.txt.
Produktziel: Die App wird für viele Firmen verkaufbar. Der heutige Arbeitgeber-Stand ist das Profil „Werbetechnik“
und MUSS sich nach Migration identisch verhalten. Konfiguration statt Code, nur Python-Standardbibliothek,
alles bleibt in index.html/server.py, keine Kundenforks.

Pflicht-Abnahme in JEDEM Paket (Identitätsnachweis):
1. Vorher: tests/make_test_db.py erzeugt eine DB mit Arbeitgeber-Stand. Vor dem Umbau Snapshot ziehen:
   Datenstand (state-JSON) und Screenshots der Hauptansichten (Übersicht/Board, Auftragsliste, Projekte, Personal,
   Formate, GF-Cockpit, System/Einstellungen, Druckansicht Nachkalkulation) je Theme hell, Desktop 1440x900 und Handy 390x844
   als tests/snapshots/<paketnummer>/before/*.png (Playwright, Browser-Zeitzone Europe/Berlin, fixes „heute“).
2. Nachher: gleiche DB durch die neue Migration, Screenshots nach .../after/. Pixel- oder DOM-Textvergleich
   (tests/e2e_identity.mjs, fehlerfrei bei 0 Differenzen außer dokumentierten Ausnahmen; Ausnahmen in der Datei begründen).
3. State-Diff: außer den neuen Schlüsseln (company, Bereichs-Eigenschaften …) bleibt der Datenstand byte-gleich
   (tests/test_identity.py, idempotent: Migration zweimal = gleiches Ergebnis).
4. Alle bestehenden Tests unverändert grün: py_compile, test_version, test_v128, test_v129, test_v12102, test_backup,
   test_regression (151), ui_smoke (77), alle e2e_*.mjs (u. a. e2e_roles 374, e2e_theme, e2e_undo), ps_syntax falls .ps1 geändert.
Erst dann Version, Doku, Commit, Push (nur Branch, kein PR). Bericht mit Testzahlen und Commit-Hash.
```

### Paket A · V12.14.0 · Aufwand M · Fundament: Firmenkonfiguration außerhalb des Pakets

```
Paket A (V12.14.0): Firmenkonfiguration in config\firma.json (außerhalb des Programmpakets) und Entfernen fest
eingebauter Firmendaten aus dem Code.

Ziel: Der Quelltext enthält keine Daten des Arbeitgebers mehr (Name, Logo, Rechnername, Repo, Personennamen).
Alles Firmenspezifische liegt in config\firma.json + config\logo.* im Live-Ordner neben data\. Ein Update überschreibt
diese Dateien nie. Der Arbeitgeber-Bestand wird beim ersten Start automatisch zur Datei „Werbetechnik“ migriert
und verhält sich exakt wie heute. Lies docs/PRODUKT_MULTI_FIRMA.md Abschnitt 3.1 vollständig (Grenze Datei/DB, Schema).

Umfang:
1. Config-Modul in server.py (Standardbibliothek): load_config(), validate_config(), migrate_config(), save_config()
   (atomar .tmp + os.replace, vorher firma.json.bak-<yyyymmdd-hhmmss>, die letzten 20 behalten, Schreibsperre).
   Schema laut 3.1 (schemaVersion, tenantId, company, locale, terms, template, modules, projectAreas, license, update).
   Migration ergänzt nur fehlende Felder mit Defaults, löscht nie, behält unbekannte Felder; schemaVersion größer als
   bekannt = nur lesen, nichts zurückschreiben, Hinweis an Admin. Pfad: <Programmordner>\config\ (Umgebung MP_CONFIG_DIR
   überschreibt, für Tests).
2. Start-Prüfung vor dem Port-Bind: ungültige Datei = Start verweigert, klare Meldung (Datei, Feld, letzte gültige .bak),
   Exit ≠ 0. Neue Codes MP-CFG-001 (nicht lesbar/kein JSON), MP-CFG-002 (Feld ungültig), MP-CFG-003 (schemaVersion zu neu,
   nur Warnung) in FEHLERCODES.txt.
3. Erstmigration Arbeitgeber: Existiert keine firma.json und die DB ist ein Bestand (state-Revision > 1), wird sie aus den
   heute eingebauten Werten und data.ci erzeugt: Name „WERBETECHNIK *ART OF DISPLAY* GMBH“, Logo → config\logo.jpg
   (aus CI_DEFAULT, index.html:1202), Farbe #E2382A, Font Arial, terms.projectNumber="WT", template="werbetechnik",
   tenantId="werbetechnik", alle Module an, projectAreas = die sieben heutigen, setupDone=true. Diese Einbauwerte
   stehen danach NUR noch in der Migration, in einer eigenen Datei tools/legacy_employer_seed.json, die nicht ins
   Kundenpaket kommt. Neue DB ohne Datei: neutrale Datei aus config_beispiel\firma.beispiel.json.
4. API: GET /api/config (angemeldete Rollen: Branding-Teil, ohne Lizenz/Update-Quelle), PUT /api/config (nur Admin,
   validiert wie beim Start, schreibt via save_config), GET /api/config/export (ZIP firma.json + Logo), POST /api/config/import
   (Admin, validiert, legt .bak an, ersetzt erst nach bestandener Prüfung). Das Logo wird über GET /api/config/logo
   ausgeliefert (kein Base64 im Datenstand). data.ci bleibt als Alias lesbar; der Client schreibt nur noch in die Datei.
5. Client: CI_DEFAULT neutral (kein Logo, Name leer). ciSettings() liest /api/config. System → Firmenprofil (Admin)
   ersetzt das Panel „Firmen-CI für Kundenpläne“ (Name, Logo, Farbe, Schrift, Adresse, Fuß; Export/Import-Knöpfe).
   Kopf, Login, Titel, Druck und Dateinamen (index.html:6, 203, 293, 358, 360, 561, 833–835) nehmen productName/Logo
   aus der Config. Texte „CD-Guideline WERBETECHNIK …“ (309, 1123) entfallen. Label „WT“ (index.html:336, 654, 661, 846,
   847, 928, 1034, 1049) aus terms.projectNumber; Datenschlüssel wt bleibt.
6. Personennamen aus Seed: server.py:244–251 und index.html:443–450 Default-Bereiche ohne „Thomsen“/„Keller“. Bestandsdaten
   behalten ihre Namen (departments wird nicht angefasst). tests/make_test_db.py und e2e-Tests bleiben lauffähig.
7. Zeitzone: release_gates.LOCAL_TZ liest MP_TIMEZONE, dann config locale.timezone, dann Europe/Berlin.
8. Skripte (nur diese Änderungen, kein anderes Verhalten):
   - MP_Common.ps1: $MP_AppFiles bleibt OHNE config\. Neue Variable $MP_ConfigDir='config'. config_beispiel\firma.beispiel.json
     wird als Paketdatei kopiert (nie als config\firma.json).
   - UPDATE_LIVE.ps1: (a) Rollback-Sicherung nimmt config\ rekursiv mit (Zeile 89 kopiert heute nur Dateien der obersten
     Ebene), (b) Rollback (Zeilen 157–166) stellt config\ wieder her, (c) im Umzugszweig ($migrating, Zeile 96–99) config\ rekursiv
     kopieren, (d) Invoke-MPPreflight (MP_Common.ps1:272–278) kopiert config\ in den Temp-Ordner, damit die neue Version mit
     der echten Config startet. config\ steht nie in $MP_ObsoleteFiles. Setup_Windows.ps1 überschreibt config\ nie.
   - Deinstallieren.ps1 behält config\ wie data\ und backups\.
   - Backup_Datenbank.py: legt zu jedem DB-Backup firma_<zeitstempel>.zip (config\ ohne .bak) an, rotiert wie die DB-Backups,
     kopiert mit second_copy; Restore_Datenbank.ps1 bekommt -MitConfig (sichert die aktuelle Config vorher als .bak).
   - README_Windows.txt:66 Rechnername WTPC207$ → „<Rechnername>$“, Beispiel-Repo/-Branch als Platzhalter;
     Update_von_GitHub.ps1:21 Repo-Default aus LAN_CONFIG.json (UpdateRepo), Fallback unverändert (keine Verhaltensänderung).
9. Rechte: company/Config schreiben nur Admin (serverseitig); alle angemeldeten Rollen lesen den Branding-Teil.

Migration/Kompatibilität: firma.json-Erstmigration zweimal ausführen = identisch. Downgrade-Schutz bleibt. Bestehende
Datenbank wird nicht verändert. Alte Clients werden über den Versionscheck abgewiesen.

Abnahme (zusätzlich zu 6.0):
- tests/test_config.py (reines Python, ohne Windows): Erstmigration aus Bestands-DB erzeugt Datei mit erwarteten Werten;
  zweite Migration byte-gleich; fehlende Felder werden ergänzt, unbekannte Felder und Kommentarfelder bleiben, bestehende
  Werte unverändert; .bak wird vor Änderung angelegt und rotiert; schemaVersion zu neu = nichts zurückgeschrieben;
  ungültige Datei = Start verweigert mit MP-CFG-002; Validierung (zu großes Logo, ungültige tenantId, Hex); Rechte je Rolle.
- Update-Simulation (tests/test_config_update.py): Ordner „alte Version mit config\“ → Dateien aus $MP_AppFiles
  (Liste per Regex aus MP_Common.ps1 gelesen) in ein Zielverzeichnis kopieren, wie UPDATE_LIVE.ps1 es tut; danach ist
  config\ byte-genau gleich (SHA256); „neue Version“ ergänzt nur neue Felder; Rollback-Simulation stellt config\ zurück;
  Test prüft außerdem, dass „config“ weder in $MP_AppFiles noch in $MP_ObsoleteFiles steht.
- tests/test_backup.py erweitern: Config-ZIP entsteht, wird rotiert, Restore -MitConfig stellt sie wieder her.
- tests/ps_syntax.ps1 grün (Skripte geändert).
- tests/e2e_company.mjs: Profil ändern → Kundenplan-Excel und Nachkalkulations-Druck zeigen neuen Namen/Logo/Farbe;
  neutrale Config zeigt kein Arbeitgeber-Logo; terms.projectNumber „Projekt“ ändert alle 8 Fundstellen; Export/Import-Rundlauf.
- tests/test_no_employer_data.py: Quelltext und Kundenpaket enthalten WERBETECHNIK, ART OF DISPLAY, /9j/4AAQ, WTPC207,
  xiy12345678910 (außer Updater-Default aus 8.), Thomsen, Keller nicht.
- Identitätsnachweis 6.0: Screenshots Arbeitgeber-DB + migrierte Config vor/nach: 0 Differenzen.
```

### Paket B · V12.15.0 · Aufwand L · Branchenvorlagen, Bereichs-Eigenschaften, Module

```
Paket B (V12.15.0): Bereichs-Eigenschaften statt fester IDs, Branchenvorlagen, Module ein/aus.
Voraussetzung: Paket A fertig.

Ziel: Keine Logik hängt mehr an den Bereichs-IDs cnc, thermoforming, konf1; die Projektbereiche sind Daten;
Funktionsblöcke lassen sich abschalten.

Umfang:
1. Bereichs-Eigenschaften in departments[]: formats (Boolean, existiert), sharedOperators (Boolean, neu).
   Client und Server ersetzen die ID-Vergleiche (Fundstellen: index.html:634 operatorConflict, 648, 1232 fmtDeptIds;
   server.py Format-/Rechtelogik 1613–1660, 2763, 2794, 2856) durch die Eigenschaften.
   Der Fallback `departmentId || 'cnc'` (index.html 32 Stellen, server.py 21 Stellen) wird zu
   defaultDepartmentId() = erster aktiver Produktionsbereich. Bei der Arbeitgeber-DB ist das weiterhin 'cnc'
   (Reihenfolge in departments bleibt) – per Test belegen.
2. Migration: thermoforming.formats=true, cnc.sharedOperators=true setzen, wenn die Bereiche existieren.
   Für jede andere Datenbank: keine Eigenschaften, Funktionen schalten sich ab (kein Fehler, keine leeren Menüs).
3. Projektbereiche (PROJECT_FIXED_AREAS client, PROJECT_FIXED_AREA_IDS server.py:1375) → projectAreas in config\firma.json
   [{id,name,kind}] mit Default der heutigen sieben. Server liest sie aus dem Datenstand (Fallback = sieben IDs).
   Bestehende Kunden-/Projektdaten mit areaId bleiben gültig. Rollen-IDs und Rechte bleiben unverändert;
   nur die Anzeigenamen der Rollen sind im Profil (roleLabels) änderbar.
4. Branchenvorlagen als Dateien templates/industry/{werbetechnik,metall_cnc,leer,demo}.json (Schema: departments,
   machines optional, shiftTemplates, processTemplates inkl. DEFAULT_PM_TEMPLATES-Ersatz, projectAreas, modules).
   Server: GET /api/templates, POST /api/templates/apply nur wenn der Datenstand keine Aufträge/Projekte enthält
   (sonst 409, Admin). DEFAULT_PM_TEMPLATES (server.py:477–491) wird Bestandteil der Vorlage werbetechnik; für
   Bestandsdaten ändert sich nichts (processTemplates stehen schon im Datenstand).
5. Module (Abschnitt 3.3): modules in config\firma.json; Navigation, Ansichten und Endpunkte prüfen das Flag (Client:
   viewAllowed/Navigation; Server: Schreibrecht und Endpunkt-Guard, Fehlercode MP-MOD-001). Aus = ausgeblendet, Daten bleiben.
   Schalter in System → Module (Admin), eine Zeile je Modul mit Hinweis auf Abhängigkeiten per Tooltip.
6. Deutsche Texte mit Branchenbezug (Tiefziehen, Siebdruck, Konfektion) nur noch in Vorlagen und Seed, nicht in Logik.

Migration/Kompatibilität: Arbeitgeber-DB nach Migration alle Module an, Eigenschaften gesetzt, Aussehen unverändert.

Rechte: Module und Vorlagen nur Admin; Formate weiter nur Bereichsleitung/Stellvertretung des formats-Bereichs und Admin.

Abnahme (zusätzlich zu 6.0):
- tests/test_templates.py: Vorlagen validieren gegen validate_state; Apply nur auf leerem Stand; Eigenschaften-Migration idempotent.
- tests/e2e_templates.mjs: Vorlage metall_cnc → Board/Aufträge/Personal laufen ohne JS-Fehler, kein „Tiefziehen“,
  keine Formate-Ansicht; Modul chat aus → keine Navigation, Endpunkt 403/404; wieder an → alte Nachrichten da.
- Bestehende e2e_formats (43), e2e_departments (26), e2e_parallel (15) unverändert grün (Beweis, dass die
  Eigenschaften die ID-Logik exakt ersetzen).
- grep: kein `==='thermoforming'` und kein `!=='cnc'`/`'cnc')` mehr in Logikpfaden (nur in Seed/Tests/Vorlage).
- Identität 6.0: Arbeitgeber-DB, 0 Differenzen.
```

### Paket C · V12.16.0 · Aufwand M · Einrichtungsassistent und Demo-Daten

```
Paket C (V12.16.0): Erst-Einrichtungsassistent im Browser, Demo-Daten, Leerprofil.
Voraussetzung: Pakete A und B.

Ziel: Eine neue Installation ist in unter 10 Minuten im Browser einsatzbereit, ohne Skriptwissen und ohne Dateiedit.

Umfang:
1. GET /api/setup/status (ohne Sitzung, nur {setupDone, hasAdmin}); POST /api/setup (ohne Sitzung, nur solange
   setupDone=false und kein Admin existiert; danach 403 MP-SETUP-001). Rate-Limit wie Login. Host/Origin-Prüfung bleibt.
2. Assistent (Vollbild, 7 Schritte aus Abschnitt 3.4, jeder Schritt überspringbar, Defaults vorbelegt, ein Satz je Schritt):
   Admin → Firma (Name, Logo, Farbe, Sprache [de], Zeitzone, Bundesland) → Vorlage → Module → erste Maschine und
   Schicht → Lizenz [Platzhalter bis Paket D: „Testversion“] → Fertig.
3. Bundesland-Feiertage: Button „Feiertage eintragen“ (Server-Funktion, Standardbibliothek, DE-Bundesländer inkl.
   Ostern/Pfingsten/Fronleichnam/Buß- und Bettag nach Land; Jahr + Folgejahr) erzeugt exceptions (bestehender Mechanismus, nie überschreiben).
4. Demo-Daten: Vorlage demo mit ca. 30 Aufträgen, 6 Projekten, 12 Mitarbeitern, Abwesenheiten, Historie. Datensätze mit
   Marker meta.demo=true; „Demo-Daten entfernen“ löscht nur markierte Datensätze. Nie auf einem Stand mit echten Daten anbieten.
5. Setup-Skripte: INSTALLIEREN_ALS_ADMIN.ps1 legt keinen Admin mehr zwingend an (Parameter optional bleibt für
   unbeaufsichtigte Installation), öffnet am Ende die Setup-Seite im Browser.
6. Bestandssysteme (setupDone=true aus Paket A): Assistent erscheint nie; „Einrichtung erneut öffnen“ nur Admin, nur
   Anzeige der Profilseiten, keine Datenlöschung.

Migration/Kompatibilität: Keine Datenänderung für Bestand. Neue DB: Server startet mit leerem Stand + setupDone=false.

Rechte: Öffentlich nur status/setup im Erst-Zustand; danach Admin.

Abnahme (zusätzlich zu 6.0):
- tests/test_setup.py: Setup nur einmal; zweiter Aufruf 403; Passwortregeln wie Benutzerverwaltung; Rate-Limit.
- tests/e2e_setup.mjs: leere DB → Assistent durchlaufen mit Vorlage metall_cnc, Feiertage NW → Ausnahmen vorhanden,
  danach Login, Board benutzbar, Demo laden/entfernen lässt echte Datensätze unberührt.
- Arbeitgeber-DB: Assistent erscheint nicht (E2E + Screenshot-Identität 6.0).
```

### Paket D · V12.17.0 · Aufwand M · Lizenzierung (erst nach Rechteklärung)

```
Paket D (V12.17.0): Offline prüfbarer, signierter Lizenzschlüssel.
Voraussetzung: Paket A, Rechteklärung mit dem Arbeitgeber (docs/PRODUKT_MULTI_FIRMA.md 4.4) liegt vor.

Ziel: Lizenz mit Firma, Laufzeit, Nutzerzahl, Modulen; Prüfung offline; nur Standardbibliothek; der Arbeitgeber wird nie ausgesperrt.

Umfang:
1. Verfahren: RSA-3072, PKCS#1 v1.5 mit SHA-256, nur Verifikation in server.py (pow(sig, e, n), hashlib). Öffentlicher
   Schlüssel (n, e, kid) als Konstante in server.py (mehrere kid für Rotation). Format base64url(payload).base64url(signatur),
   Payload laut 3.6 (v, kid, tenantId, company, issued, validUntil, maxUsers, modules, edition, channel).
   Strenge Prüfung: Signaturlänge = Modulusgröße, EMSA-PKCS1-v1_5-Struktur exakt vergleichen, e=65537, Payload-Schema.
2. Signierwerkzeug beim Anbieter (nicht im Kundenpaket): tools/license_sign.py (nutzt `openssl dgst -sha256 -sign`),
   Doku für Schlüsselverwaltung. Private Schlüssel nie im Repo.
3. Server: Tabelle license (key_text, installed_at); GET /api/license (Admin: Details; andere: nur Status),
   POST /api/license (Admin, prüft Schlüssel, tenantId muss zu firma.json tenantId passen, sonst MP-LIC-003).
4. Wirkung:
   - gültig: Module der Lizenz freigegeben, maxUsers beim Benutzer-Anlegen geprüft (MP-LIC-005).
   - abgelaufen: 30 Tage Karenz, danach Lesemodus mit Hinweisband; Export und Backup bleiben; keine Datenlöschung.
   - fehlend: Testmodus 30 Tage ab Installation (Zeitpunkt in DB), danach Lesemodus. tenantId 'werbetechnik'
     (Migrationsprofil): unbefristete Hauslizenz ohne Schlüssel, bis eine Vereinbarung mit dem Arbeitgeber etwas anderes sagt.
   - Uhrrücksetzung: maximaler bekannter Zeitstempel (Revisionen) gilt als Mindestzeit.
5. UI: System → Lizenz (Admin): Firma, Laufzeit, Nutzer x/y, Module als Chips, Eingabefeld für Schlüssel, Statusband
   bei <30 Tagen Restlaufzeit (ein Satz). Assistent-Schritt 6 nutzt dasselbe Feld.
6. Keine Telemetrie, kein Netzzugriff für die Prüfung.

Migration/Kompatibilität: Bestandssysteme unverändert nutzbar (Hauslizenz). Neue Tabelle idempotent.

Rechte: Lizenz setzen nur Admin; Lesemodus technisch als zusätzlicher Schreib-Guard vor den *_change_allowed-Funktionen.

Abnahme (zusätzlich zu 6.0):
- tests/test_license.py mit openssl-erzeugten Testvektoren: gültig; abgelaufen (Karenz, Lesemodus); manipulierte Payload;
  falscher kid; falsche tenantId; Signatur um 1 Bit geändert; zu viele Benutzer; Uhr zurückgedreht; Hauslizenz ohne Schlüssel.
- tests/e2e_license.mjs: Lesemodus blockiert Speichern, Export geht; Schlüssel eingeben schaltet Module frei.
- Arbeitgeber-DB: kein Banner, volle Funktion, 0 Differenzen in der Identität (6.0).
```

### Paket E · V12.18.0 · Aufwand M · Installer-Branding, Update-Kanal je Kunde

```
Paket E (V12.18.0): Installer parametrisierbar, Update-Quelle und -Kanal je Kunde, Doku-Trennung.
Voraussetzung: Pakete A und D.

Ziel: Neuinstallation und Updates ohne private GitHub-Konten und ohne Arbeitgeber-Hinweise; Bestandsinstallation läuft unverändert weiter.

Umfang:
1. MP_Common.ps1: $MP_ProductSlug und Namen (Task, Firewall, Ordner) aus LAN_CONFIG.json (Feld productSlug) ableiten.
   Default für Neuinstallation 'Produktionsplanung', für vorhandene Installation wird der vorgefundene Name 'Maschinenplanung'
   beibehalten (Erkennung über vorhandenen Task/Ordner). Keine Doppel-Tasks, keine verwaisten Firewallregeln; Test mit ps_syntax und
   Trockenlauf-Schalter.
2. INSTALLIEREN_ALS_ADMIN.ps1: Parameter -Firma, -Produktname, -Port, -InstallBase, -Kanal. Schreibt LAN_CONFIG.json.
3. Update-Quelle: Manifest {version, url, sha256, signature(kid), channel, minVersion} von einer konfigurierbaren
   URL (UpdateSource in LAN_CONFIG.json); Signatur wie Lizenz (RSA). Update_von_GitHub.ps1 wird zu Update_Produkt.ps1
   (Alias für den alten Namen bleibt, damit vorhandene Aufgaben und Anleitungen laufen). Bestehende Schutzmechanismen
   bleiben: fester Hash, Staging mit ACL, Vorab-Backup/Test, Downgrade-Schutz, Rollback.
   Kanäle stable/beta; Kanal aus Lizenz oder Konfiguration. Für den Arbeitgeber bleibt GitHub Releases von main als Quelle
   eingetragen (UpdateRepo aus Paket A), bis er umgestellt wird.
4. Diagnosepaket: Server_Status.ps1 -Diagnose erstellt ZIP aus Logs, Version, Lizenzstatus, Modulliste, Schema-Version,
   OHNE state-JSON, Benutzernamen oder Fachdaten (Test: Stichproben-Grep im ZIP).
5. Kundenpaket: build/make_customer_package.ps1/.py erzeugt ZIP ohne docs/-Internes, ohne tests/snapshots, ohne
   tools/legacy_employer_seed.json, mit neutraler README_Windows.txt, Handbuch-Platzhalter und OSS-Lizenzliste. Prüfung per
   grep, dass keine Arbeitgeber-Strings enthalten sind.
6. Doku neutralisieren: README_Windows.txt, BENUTZER_KURZANLEITUNG.txt (Hinweise auf boensch/Rechnername raus,
   Platzhalter), FEHLERCODES.txt bleibt.

Migration/Kompatibilität: Bestandsinstallation: gleiche Task-/Ordner-/Firewallnamen, gleiche Update-Befehle funktionieren weiter.

Rechte: Skripte laufen wie bisher als Administrator; Quelle und Signatur nur aus Konfiguration im geschützten Live-Ordner.

Abnahme (zusätzlich zu 6.0):
- tests/ps_syntax.ps1 grün; tests/test_update_manifest.py (Signatur gültig/ungültig, Hash-Abweichung, Downgrade, Kanalwechsel).
- tests/test_package.py: Kundenpaket enthält keine verbotenen Strings (WERBETECHNIK, WTPC207, xiy12345678910, boensch, Thomsen, Keller).
- Trockenlauf auf Windows-Testrechner (manuell, Protokoll im Bericht): Neuinstallation und Update einer „Maschinenplanung“-Altinstallation; Task-/Regel-Liste vorher/nachher identisch.
- Identität 6.0.
```

### Paket F · V12.19.0 · Aufwand L · Mehrsprachigkeit

```
Paket F (V12.19.0): i18n-Schicht, de vollständig, en vorbereitet.
Voraussetzung: Paket A. Konflikte mit B beachten (gleiche Zeilen); Paket in kleinen Commits.

Ziel: Alle sichtbaren Texte laufen über t(); die Umschaltung de/en wirkt auf Navigation, Login, Fehlermeldungen,
Hauptansichten und Druck; fehlende en-Texte fallen auf de zurück.

Umfang:
1. Client: const I18N={de:{…},en:{…}}; t(key,params); locale.language (config\firma.json) bestimmt die Sprache, pro Benutzer optional
   überschreibbar (localStorage, try/catch). Mechanisches Herauslösen der ca. 347 Zeilen mit deutschem Text,
   in Gruppen nach Ansicht, je Gruppe ein Commit. Platzhalter statt String-Verkettung; Pluralformen explizit.
2. Server: Fehlerantworten liefern code und params; der Client übersetzt über err.<MP-…>. Bestehende deutsche Server-Texte
   bleiben als msg-Fallback. Neue Codes zweisprachig. Server-Audit-/Log-Texte bleiben deutsch.
3. Formate: Intl.DateTimeFormat/NumberFormat nach Sprache und Firmenzeitzone; CSV: de Semikolon/Komma, en Komma/Punkt
   (Schalter), BOM und Formelschutz bleiben. Excel-/Druckkopf (lang-Attribut, Spaltentitel) aus Sprache.
4. en vollständig für: Navigation, Login, Assistent, Board, Auftragsliste, Fehlermeldungen, Druckköpfe. Rest en = de.
5. UX-Regel: keine längeren Texte als de; Layout-Test mit en (lange Wörter) bei 390 px.

Migration/Kompatibilität: language='de' für Bestand; Texte byte-gleich zur Vorversion (Textvergleich der Screenshots).

Rechte: Sprache pro Firma nur Admin, pro Benutzer lokal.

Abnahme (zusätzlich zu 6.0):
- tests/test_i18n.py: jeder t('…')-Schlüssel existiert in de; kein deutscher Satz-Literal in neu geschriebenen render*-Funktionen;
  en-Lücken als Warnliste; Platzhalter in de/en identisch.
- tests/e2e_i18n.mjs: Sprache auf en → Hauptansichten ohne JS-Fehler, Fehlermeldung MP-PLAN-058 englisch, Druck lang="en",
  Umschalten zurück stellt den Stand her. e2e_theme (Kontrast) weiter grün mit en.
- Identität 6.0: de-Screenshots und DOM-Texte 0 Differenzen.
```

### Paket G · V12.20.0 · Aufwand S · Mandanten-Vorbereitung

```
Paket G (V12.20.0): Datenpfad je Firma kapseln, Host → Firma. Kein produktiver Mehrfirmenbetrieb.
Voraussetzung: Pakete A und D.

Ziel: Der Code kennt tenantId als Auswahl des Datenpfads, ohne dass sich für Einzelinstallationen irgendetwas ändert.

Umfang:
1. server.py: tenant_paths(tenant_id) liefert DATA_DIR, DB_PATH, BACKUP_DIR. Standard und alle Bestandsinstallationen:
   unveränderte Pfade (data/maschinenplanung.sqlite3). Zusätzliche Firmen nur mit Umgebungsvariable MP_TENANTS=1:
   data/<tenantId>/maschinenplanung.sqlite3.
2. Alle globalen Zugriffe auf DB_PATH/DATA_DIR laufen über eine Funktion get_db(tenant) (heute Zeile 38–39, 80). Sitzungen,
   Login-Sperre, Chat, Benachrichtigungen, server_audit sind je Firma (liegen in deren DB). Cookie-Name enthält die tenantId, wenn MP_TENANTS=1.
3. Auswahl der Firma über Host-Header/Subdomain (Tabelle in LAN_CONFIG.json). Unbekannter Host → 404 ohne Hinweis auf andere Firmen.
4. Backup/Restore-Skripte kennen -Tenant; Standard ohne Parameter = heutiges Verhalten.
5. Lizenz je Firma (tenantId muss passen). Update bleibt global für den Prozess.
6. Dokumentiert, nicht gebaut: TLS, Kontohärtung, AVV, Betriebskonzept als Voraussetzungen für Hosting.

Migration/Kompatibilität: Einzelinstallation byte-gleich; kein Schema-Change an Fachdaten.

Rechte: unverändert; Tests prüfen, dass Sitzung der Firma A bei Firma B 401 liefert.

Abnahme (zusätzlich zu 6.0):
- tests/test_tenants.py: zwei Firmen im Testserver, getrennte DBs, Cookie von A gilt nicht für B, Login-Sperre getrennt,
  Chat/Benachrichtigungen getrennt, unbekannter Host 404, ohne MP_TENANTS exakt altes Verhalten.
- Alle bestehenden Tests unverändert grün; Identität 6.0.
```

---

## 7. Risiken und offene Entscheidungen

| Thema | Risiko | Gegenmaßnahme |
|---|---|---|
| Rechte am Code | Verkauf ohne Zustimmung des Arbeitgebers; Streit über Logo/Daten | 4.4 vor Paket D klären; Paket A trennt Firmendaten unabhängig davon |
| Regressionen beim Arbeitgeber | Umbau der `'cnc'`-Fallbacks und ID-Vergleiche (52 Stellen) | Identitätsnachweis 6.0 in jedem Paket, Pakete klein, Update mit Vorab-Backup und Rollback (vorhanden) |
| Parallele Agents | Pakete B und F berühren dieselben Zeilen in `index.html` | B vor F oder F in kleinen Commits nach B, immer `git pull --ff-only` |
| Single-File-Größe | `index.html` 561 KB; i18n und Vorlagen vergrößern sie | Vorlagen als JSON-Dateien beim Server, nicht im HTML; `en` erst nach Bedarf |
| Sicherheitsniveau | LAN-HTTP, SYSTEM-Konto (Audit B-H1, O-H2) schlägt bei Kunden-IT durch | Vor dem ersten Kunden TLS-Option und Dienstkonto nachziehen (eigenes Paket, nicht Teil der Zählung oben) |
| Pfad der Altnamen | Umbenennen von „Maschinenplanung“ bricht Tasks | Namen nur für Neuinstallationen ändern (Paket E) |
| Support-Last | Jede Installation ist eine Instanz | Diagnosepaket, einheitlicher Versionsstand, Manifest-Updates |
| Preise | Hypothesen ohne Marktdaten | 3–5 Kundengespräche vor Preisfestlegung |

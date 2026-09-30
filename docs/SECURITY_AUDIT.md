# Audit Server & Rechte – V12.7.5 (30.09.2026)

Geprüft: `server.py` (API, Anmeldung, Rechte je Rolle, Validierung, Historie-Archiv, Abwesenheits-Ausblendung),
`release_gates.py` (Freigabeprüfung) und in `index.html` alle Stellen, an denen gespeicherte Daten als HTML
eingefügt werden. Jeder Befund unten wurde gegen einen laufenden Testserver nachgestellt, nicht nur im Code gelesen.

Tests: `tests/test_regression.py` 162/162 (neu: 11 Prüfungen zu diesem Audit; auf dem alten Stand schlagen 6 davon
fehl, danach bricht der Lauf mit einem Verbindungsabbruch ab), `tests/ui_smoke.mjs` 77/77.

## Behoben

| # | Schwere | Befund | Nachweis | Änderung |
|---|---------|--------|----------|----------|
| 1 | **Hoch** | Gespeicherter Skript-Einschleusung (XSS): `revision` im Änderungsprotokoll und in gespeicherten Planständen wurde ungeprüft als HTML eingefügt. Jede schreibende Rolle (auch Vertrieb) kann `audit`-Einträge anlegen, Bereiche und AV zusätzlich `planVersions`. Skript läuft dann in der Sitzung des Admins, kann also z. B. Admin-Konten anlegen. Die CSP erlaubt `'unsafe-inline'` und hält das nicht auf. | CNC-Leitung speichert `<img src=x onerror=…>` als Revision; beim Admin wird der Handler in Chromium ausgeführt. | `index.html`: `escapeHtml` für beide Revisionen und die Abwesenheits-CSS-Klasse. `server.py`: `planVersions` ändert nur noch der Admin (die Oberfläche legt keine Planstände mehr an). |
| 2 | Mittel | Abwesenheitsgrund (z. B. „Krank“) war in gespeicherten Planständen (`planVersions[].payload.personnelAbsences`) für alle Rollen sichtbar, obwohl er im Live-Stand ausgeblendet wird. | Admin legt einen Planstand mit Krankmeldung an; die Konf1-Leitung liest „Krank“. | `redact_state`/`unredact_incoming` blenden den Grund auch dort aus und stellen ihn beim Speichern wieder her. |
| 3 | Mittel | CNC-Leitung konnte beliebige Ist-Historie anlegen (erfundene Auftrags-ID → Bereich fiel auf den Standard „cnc“ zurück). Weil die Historie nur ergänzt werden darf, kann danach niemand den Eintrag entfernen, auch der Admin nicht. Fließt in Auswertung und Nachkalkulation. | PUT als CNC-Leitung mit `originalOrderId: "gibt-es-nicht"` → 200. | Neue Historie aus Bereichen muss zu einem eigenen Auftrag gehören, der im selben Speichervorgang entfernt wird (so arbeitet die Oberfläche beim Fertigmelden/Abbrechen). |
| 4 | Mittel | Freigabeprüfung (Personal-Gate) zählte per KW in einen Bereich eingesetzte Mitarbeiter (z. B. Leiharbeiter) nicht mit, die Oberfläche (`canStaff`) aber schon. Folge: Der Server lehnte Freigaben mit „personell unterdeckt“ ab, obwohl die Planung sie als besetzt zeigte. | `personnel_cover` mit eingesetztem Mitarbeiter: vorher `(False, 0, 1)`, jetzt `(True, 1, 1)`. | `release_gates.py`: gleiche Regel wie `canStaff()`. |
| 5 | Niedrig | Beim **Anlegen** eines Projekts galten keine Feldgrenzen: Der Vertrieb konnte Angebot, Kundenplan und Prozesse mit Status „Erledigt“ setzen, was er danach nicht mehr ändern darf. | PUT als Vertrieb mit `offer` + Prozess `done` → 200. | Beim Anlegen gelten dieselben Felder wie beim Ändern (plus `id`, `number`, `createdAt`); neue Abteilungsprozesse starten „Offen“. |
| 6 | Niedrig | Falsche Datentypen (z. B. `shiftTemplates: [1]`, `tempStatus: [..]`, `shift: [..]`, zyklenfreie, aber sehr tiefe Vorgängerketten) lösten in der Prüfung eine Ausnahme aus; der Browser bekam nur einen Verbindungsabbruch statt einer Meldung. Beim Fuzzing gefunden: 6 Stellen. | Fuzzing aller Felder mit falschen Typen. | Prüfungen in `check_incoming_state` gebündelt; Typfehler → 400 `MP-DATA-014`. |

## Offen (nicht geändert, Empfehlung)

| Schwere | Befund | Empfehlung |
|---------|--------|------------|
| Mittel | CSP enthält `script-src 'unsafe-inline'`. Jede künftige vergessene Maskierung wird dadurch sofort zu ausführbarem Skript. | Inline-Skript in eine Datei auslagern und `'unsafe-inline'` für Skripte entfernen (größerer Umbau von `index.html`, inline `onclick` gibt es dort kaum). |
| Mittel | Betrieb nur über HTTP im LAN: Passwort und Sitzungscookie gehen unverschlüsselt über das Netz, Cookie ohne `Secure`. | Falls das Netz nicht vollständig vertrauenswürdig ist: TLS-Proxy (z. B. Caddy) vor den Server, dann `Secure` setzen. |
| Niedrig | `meta` und `ui` im Datenstand werden nicht geprüft; jede schreibende Rolle kann dort bis zur Gesamtgrenze (8 MB) Daten ablegen. Die Oberfläche prüft `ui.accent`/`cellH` beim Laden. | Erlaubte Schlüssel und Längen serverseitig festlegen. |
| Niedrig | Client-Änderungsprotokoll: Jede schreibende Rolle kann 1000 eigene Einträge anlegen und so ältere verdrängen. Das serverseitige `server_audit` bleibt vollständig. | Für Nachweise `server_audit` verwenden; ggf. Einträge je Speichervorgang begrenzen. |
| Niedrig | „Passwort ändern“ zählt falsche aktuelle Passwörter, sperrt aber nie (die Anmeldung sperrt nach 8 Fehlversuchen/10 min). Voraussetzung ist eine bestehende Sitzung. | `failed_login_blocked` auch in `change_own_password` prüfen. |
| Niedrig | AV-Termin (`dueDate`) wird für Bereiche nur bei bestehenden Arbeitsgängen gesperrt; ein neu angelegter Arbeitsgang darf ihn setzen. | Klären, ob Bereiche neue Aufträge mit Termin anlegen sollen. |
| Niedrig | Konten mit Altrollen (`planner`/`production`) lassen sich nicht sperren, ohne gleichzeitig eine neue Rolle zu wählen (PATCH → 400). | Beim Sperren die Rollenprüfung überspringen, wenn die Rolle unverändert bleibt. |
| Niedrig | Alle DB-Zugriffe laufen unter einer globalen Sperre; beim Anlegen/Ändern von Benutzern wird das Passwort (~0,2 s) innerhalb der Sperre gehasht und blockiert solange alle anderen Anfragen. | Hash vor dem `with DB_LOCK` berechnen (wie bei der Anmeldung). |

## Geprüft, ohne Befund

- Anmeldung: PBKDF2 (310 000 Runden), gleiche Rechenzeit für unbekannte Benutzer, Sperre nach 8 Fehlversuchen,
  Token nur als SHA-256 gespeichert, Cookie `HttpOnly; SameSite=Strict`.
- CSRF: Alle schreibenden Anfragen außer Anmelden/Abmelden verlangen den Kopf `X-MP-Client-Version`; fremde
  Seiten können ihn nicht ohne Preflight setzen.
- SQL: durchgehend Platzhalter; die einzigen f-Strings setzen feste Spaltenlisten zusammen.
- Nebenläufigkeit: Revision + `BEGIN IMMEDIATE` (10 parallele Schreibzugriffe → genau 1×200, 9×409),
  Long-Poll ohne verlorene Benachrichtigung.
- Benutzerverwaltung: Leitungen/Stellvertretungen nur für untergeordnete Rollen im eigenen Bereich, eigenes
  Konto nicht über die Verwaltung änderbar.
- Rechte je Rolle (GF, PM, Vertrieb, AV, Bereiche) für alle Datenbereiche; Bereichswechsel von Datensätzen
  prüft alten und neuen Bereich.
- Auslieferung: nur `index.html`, keine Dateien aus `data/` oder Skripten erreichbar; Verbindungen außerhalb
  des Subnetzes werden vor dem HTTP-Parsing verworfen.

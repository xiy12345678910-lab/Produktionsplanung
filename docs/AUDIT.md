# Audit & Verbesserungsliste – Produktionsplanung V12.10.1

Stand: 05.10.2026 · Branch `claude/new-session-mpx5ch` · Analyse von V12.10.1.

> **Umsetzung V12.10.2** (Branch `claude/new-session-95ro2l`): V-01, V-02, V-03, V-04, V-05, V-11, V-12 und die README-Version sind erledigt; von V-07 die ACL-Prüfung des Python-Ordners und tzdata mit Hash (O-H3).
> Offen bei V-07: Dienstkonto statt SYSTEM (O-H2) – braucht einen Test auf dem Windows-Host.
> Offen bei V-01: Branch-Schutz für `main` und Tag-Schutz muss ein Repo-Admin auf GitHub einschalten (siehe README_Windows.txt 2b).
> Abweichung zu N8: `or` statt `and` hätte Altbestände blockiert (fertige Aufträge wandern in die Historie, gelöschte bleiben verknüpft).
> Umgesetzt ist daher: Eine **neue** Verknüpfung muss auf einen vorhandenen oder fertigen Auftrag zeigen.
> Bei B-M5 ist nur das Schreibrecht der AV entfernt; `formats` wird im GET noch nicht je Rolle ausgeblendet.
Umfang: `server.py` (3264 Z.), `index.html` (532 KB), PowerShell-/Backup-Tooling, Tests, Doku.

## 0. Zusammenfassung

| Bereich | Kritisch | Hoch | Mittel | Niedrig |
|---|---|---|---|---|
| Backend (`server.py`) | 0 | 2 | 6 | 12 |
| Frontend (`index.html`) | 0 | 5 | 7 | 6 |
| Betrieb / Update / Backup | 0 | 4 | 7 | 6 |
| **Summe** | **0** | **11** | **20** | **24** |

Insgesamt solide: Das SQL ist parametrisiert, die Sitzungs-Tokens sind gehasht, die Revision wird optimistisch unter `BEGIN IMMEDIATE` geprüft, Ausgaben sind durchgängig per `escapeHtml` maskiert (rund 340 Stellen, keine XSS-Lücke gefunden), Backups laufen über die SQLite-Backup-API mit `integrity_check`, und vor jedem Update steht ein Vorabtest auf einer DB-Kopie.

Die Hauptrisiken:
1. **Herkunft des Codes beim Update.** Der Server zieht den Kopf eines Feature-Branches ohne Hash- oder Signaturprüfung und führt ihn als SYSTEM aus.
2. **Datenverlust im Mehrbenutzerbetrieb.** Konflikte werden pro Datensatz statt pro Feld erkannt, bei einem Konflikt wird der ganze lokale Batch verworfen, und es gibt keinen `beforeunload`-Schutz.
3. **Klartext-HTTP.** Passwörter und Sitzungs-Cookies gehen unverschlüsselt durchs LAN.
4. **Skalierung.** Jede Änderung überträgt den ganzen Datenstand, eine globale DB-Sperre gilt während der gesamten Validierung, und der Client rendert alle Ansichten neu.

### Tests (am 05.10.2026 unter Linux ausgeführt)

| Test | Ergebnis |
|---|---|
| `python3 -m py_compile server.py` | OK |
| `tests/test_v128.py` | 35/35 |
| `tests/test_v129.py` | 12/12 |
| `make_test_db.py` + `test_regression.py` | 151/151 |
| `node tests/ui_smoke.mjs` | 77/77 |
| `e2e_*.mjs` (6 Dateien) | nicht ausgeführt |

---

## 1. Befunde – Hoch

| ID | Ort | Befund | Szenario | Fix |
|---|---|---|---|---|
| **B-H1** | `server.py:2998,3252`, `Run_Server_LAN.ps1:76` | Nur HTTP. Das Cookie `mp_session` hat kein `Secure`. | Ein Gerät im Subnetz (WLAN, ARP-Spoofing) liest Admin-Passwort oder Cookie mit und übernimmt die Sitzung. | TLS per `ssl.SSLContext` mit Zertifikat einer internen CA, danach `Secure` und HSTS. |
| **B-H2** | `server.py:2975-2992` | Die Brute-Force-Sperre lässt sich umgehen: (a) Ein erfolgreicher Login setzt mit `clear_failed_login(ip)` den Zähler der ganzen IP zurück. (b) Zwischen Prüfung und Erfassung liegt ein Race von ca. 0,2 s PBKDF2. (c) Es gibt keine Sperre pro Benutzer. | Ein Viewer-Konto rät 7-mal das Admin-Passwort, meldet sich selbst an und wiederholt das. So sind unbegrenzt viele Versuche möglich. | Fehlversuche je (IP, Benutzer) und je Benutzer zählen, den Versuch atomar unter `LOGIN_LOCK` reservieren, bei Erfolg nur den Schlüssel dieses Benutzers löschen. |
| **F-H1** | `index.html` `flushRemoteSave` (409-Zweig) | Scheitert der Merge, verwirft der Code mit `remoteDirty=false; overlayLocalUI(...)` den ganzen lokalen Batch. Konflikte werden pro Datensatz erkannt, nicht pro Feld; `normalizePos()` schreibt viele `pos`-Werte neu. | Die AV ändert den Termin von Auftrag X, gleichzeitig ändert der Planer die Menge von X. Die Eingabe der AV ist weg (MP-SYNC-004). | Merge auf Feldebene für `workSteps`, `projects` und `processes`; nur den betroffenen Datensatz zurücksetzen; verworfenen Stand als Recovery-Kopie anbieten. |
| **F-H2** | `index.html` `save()`, `overlayLocalUI` | Wirft localStorage `QuotaExceededError` (ca. 5 MB, darin Logo bis 420 KB, Audit und Historie), erreicht die Änderung den Server nie. `overlayLocalUI` hat kein `try`, deshalb erscheint fälschlich „SERVER OFFLINE“. | Bei großem Datenstand kann niemand mehr speichern. | Den lokalen Cache in ein eigenes `try` legen und nur UI-Zustand spiegeln, nicht Audit, Historie und Logo. |
| **F-H3** | `index.html` `serverPayload()` | Jede Änderung schickt nach 120 ms den kompletten Stand per PUT. Jede Fremd-Revision löst einen GET des kompletten Stands aus. | Mehrere hundert KB bis MB pro Klick, Hänger auf Werkstatt-PCs. | Mindestens Logo, Audit und Historie aus dem PUT herausnehmen; mittelfristig Patch-Endpunkte (siehe V-04). |
| **F-H4** | `index.html` (es gibt keinen `beforeunload`-Handler, geprüft) | Beim Schließen des Tabs gehen Änderungen in der Entprellung, ein laufender PUT oder Änderungen im Backoff verloren. | Der Nutzer schließt den Tab direkt nach einer Änderung, die Änderung ist weg. | `beforeunload`-Warnung bei `remoteDirty‖remoteSaving`, dazu `fetch(...,{keepalive:true})`. |
| **F-H5** | `index.html` `renderAllViews`, `calcSchedule` | Bei jeder Änderung werden alle 15 Ansichten gerendert. `calcSchedule()` läuft ohne Cache 7-mal oder öfter pro `renderAll`. | Spürbare Latenz bei jeder Eingabe und jeder Fremd-Revision. | `calcSchedule` per Revision memoizen; nur die aktive Ansicht rendern, mit Dirty-Flags je Ansicht. |
| **O-H1** | `Update_von_GitHub.ps1:10,23,29-39` | Lädt per `codeload…/zip/refs/heads/<Branch>` den Kopf von `claude/new-session-mpx5ch`, ohne Hash, Signatur oder festen Commit. Danach folgen `Unblock-File` und `-ExecutionPolicy Bypass`. | Jeder Push auf den Branch, ob kompromittiert, ein WIP-Commit oder eine KI-Session, läuft beim nächsten Update als SYSTEM. | Nur aus Release-Tags von `main` deployen, SHA256 bzw. Commit fest vorgeben und vor dem Entpacken prüfen, Branch-Schutz einrichten. |
| **O-H2** | `MP_Common.ps1:174,183`, `UPDATE_LIVE.ps1:170` | Server und Backup-Task laufen als SYSTEM mit `RunLevel Highest`. | Jede RCE im eigenen HTTP-Server bedeutet volle Kontrolle über die Maschine. | Eigenes Dienstkonto oder virtuelles Konto mit Schreibrecht nur auf `data\`, `backups\` und `logs\`. Firewall-Regeln einmalig im Setup anlegen. |
| **O-H3** | `MP_Common.ps1:71-76` | Die Python-Prüfung testet nur den Pfad (`C:\Users\*`), nicht die ACL. Unter `C:\Python311` darf jeder Benutzer `python.exe` ersetzen. Dazu kommt `pip install tzdata` ohne Pin und Hash als Admin. | Ein Domänenbenutzer verändert Python, der Code läuft danach als SYSTEM. | Die ACL mit `Test-MPFolderAclSafe` prüfen, tzdata als Wheel mitliefern und mit `--require-hashes` installieren. |
| **O-H4** | = B-H1 | Kein TLS (betrifft auch den Betrieb). | – | siehe B-H1 |

## 2. Befunde – Mittel

| ID | Ort | Befund | Fix |
|---|---|---|---|
| B-M1 | `server.py:2807-2814,2959` | Kein Socket-Timeout, Threads unbegrenzt, der Body (bis 8 MB) wird vor der Authentifizierung gelesen, was einen Slowloris-Angriff ermöglicht. | `Handler.timeout=30`, Body-Limit für Login/Logout von wenigen KB, Threads begrenzen. |
| B-M2 | `server.py:2763-2770` | `Host` und `Origin` werden nicht geprüft, DNS-Rebinding umgeht den Subnetzfilter. | Den `Host`-Header gegen die gebundene IP bzw. konfigurierte Namen prüfen, bei Schreibzugriffen zusätzlich `Origin`. |
| B-M3 | `server.py:43,3139-3208` | `DB_LOCK` gilt global während JSON-Verarbeitung, Validierung und Release-Gates. Gemessen: 0,36 s bei 2,8 MB. Lineare Suchen O(n·m) in 1493, 1721 und 1821. | Lookup-Dicts verwenden, außerhalb der Sperre validieren und unter der Sperre nur Revision prüfen und schreiben (Compare-and-Swap). |
| B-M4 | `server.py:3118-3211` | Der ganze Stand ist ein JSON-Blob. `server_audit` wächst unbegrenzt und wird nie gelesen. | Aufbewahrungsregel für `server_audit`, ETag und gzip für `GET /api/state`, später Teiltabellen. |
| **B-M5** | `server.py:2404` | Die AV darf `formats` ändern (steht in `allowed_root`), obwohl laut RELEASE_NOTES 12.10.0 AV und GF keine Formate mehr sehen sollen. Formate gehen außerdem an alle Rollen im `GET /api/state`. | Klären, was gelten soll; dann `formats` entfernen oder auf `status`/`workStepId` begrenzen und je Rolle schwärzen. |
| B-M6 | `server.py:724-750` | Die Chat-Kanalliste macht N+1-Abfragen unter `DB_LOCK`, jeder Client alle 5 s. Bei 30 Clients und 20 Kanälen sind das rund 600 Abfragen in 5 s. | Eine aggregierte Abfrage; Tabelle `chat_members` statt JSON-Spalte. |
| F-M1 | `index.html` CSV-Export | Start und Ende werden per `toISOString()` in UTC ausgegeben (1–2 h falsch). CSV-Formelinjektion ist möglich. Der Dateiname heißt noch `…_CNC_Auftraege.csv`. | Ortszeit verwenden, Zellen mit `[=+\-@]` am Anfang mit `'` präfixen, Dateinamen korrigieren. |
| F-M2 | `index.html` `workDaysBetween`, `workDayDiff` | Nur `getDay()%6` zählt, Feiertage und Betriebsferien werden ignoriert. | `exceptionForDate` einbeziehen (betrifft den PM/AV-Vergleich und den Kundenterminplan). |
| F-M3 | `index.html` `setInterval(pollServer,250)` | Kein Backoff bei Fehlern (4 Anfragen/s pro Client). Polling läuft auch bei verborgenem Tab. | Exponentielles Backoff, Pause bei `document.hidden`. |
| F-M4 | `index.html` `save()` offline | Ein einziger fehlgeschlagener Poll schaltet auf offline, die gerade gemachte Änderung wird verworfen. | Erst nach 2–3 Fehlern auf offline schalten, Änderungen lokal in eine Warteschlange stellen. |
| F-M5 | `index.html` `chatLoadMsgs` | Race beim Kanalwechsel: Nachrichten landen im falschen Kanal. `chat.msgs` wächst unbegrenzt, die Duplikatprüfung ist O(n²). | Nach `await` prüfen, ob `chat.cur===c.id` noch gilt; In-flight-Guard; Duplikate per `Set` prüfen. |
| F-M6 | `roleCan` und `viewAllowed` vs. `server.py` | Die Rollenmatrix ist doppelt von Hand gepflegt, Abweichungen führen zu 403-Fehlern. | Die Matrix über `/api/session` vom Server ausliefern. |
| F-M7 | Board, Formatlayout | Drag&Drop funktioniert nur per Maus (nicht per Touch oder Tastatur). Kontrast `#7b8799` bei 10–11 px liegt unter WCAG AA. | Pointer Events und Tastaturbedienung ergänzen, Farben anpassen. |
| O-M1 | `UPDATE_LIVE.ps1:37-42` | Der Downgrade-Schutz greift nur bei laufendem Server. | Schemaversion in der DB speichern und prüfen. |
| O-M2 | `Update_von_GitHub.ps1:12,52` | TOCTOU: Das Paket liegt im beschreibbaren Download-Ordner, bis UAC bestätigt ist. | Im erhöhten Teil nach `%ProgramData%\…\staging` kopieren und dort per Hash prüfen. |
| O-M3 | `Backup_Datenbank.py:51-60` | Ein fehlgeschlagenes Backup bleibt unter dem endgültigen Namen liegen, die Statusskripte werten es als PASS. | Erst in eine `.tmp`-Datei schreiben, nach `verify` umbenennen; zusätzlich `LastTaskResult` prüfen. |
| O-M4 | `Backup_Datenbank.py:64-77` | Die Zweitkopie wird als SYSTEM geschrieben (in einer Arbeitsgruppe scheitert sie meist still), ist unverschlüsselt und ohne Schutz gegen Ransomware. | Rechte für `DOMAIN\PC$` dokumentieren, eine Offline- oder versionierte Kopie anlegen, bei Fehlern warnen. |
| O-M5 | `UPDATE_LIVE.ps1:87-90` | `update_backups\` wird nie aufgeräumt. Das widerspricht der 30-Tage-Zusage für gelöschte Chats. | Nur die letzten 5 Update-Backups behalten, Doku korrigieren. |
| O-M6 | `README_Windows.txt:73-77` | Eine Wiederherstellung ist nur beschrieben. Es gibt kein Restore-Skript und keinen Restore-Test; `Stop_Server.ps1` fasst beim Beenden nicht nach. | `Restore_Datenbank.ps1` (Stop-MPServer, Vorab-Backup, `integrity_check`, WAL/SHM entfernen, Health-Check) plus Test. |
| O-M7 | `.github/workflows` fehlt | Keine CI; PowerShell-Skripte sind ungetestet. | GitHub Actions mit Python-Tests, Playwright und PSScriptAnalyzer; Tags nur bei grünem Lauf. |

## 3. Befunde – Niedrig (Kurzliste)

**Backend**
- N1: `ui`, `meta` und `planVersions` werden nicht validiert. Ist `meta` ein String, löst Zeile 3200 einen TypeError aus und die Verbindung bricht ohne Antwort ab.
- N2: Kein globales `try/except` in `do_*`. Beispiel: `POST /api/chat/members {"add":5}` endet mit einem Verbindungsabbruch statt einer 500-Antwort.
- N3: `change_own_password` prüft die Sperre nicht, mit einer Sitzung lässt sich das aktuelle Passwort unbegrenzt raten.
- N4: PBKDF2 mit 310.000 Iterationen statt der von OWASP empfohlenen 600.000, kein Rehash beim Login; Sitzungs-TTL 12 h absolut, kein Idle-Timeout.
- N5: Die CSP enthält `script-src 'unsafe-inline'`; `send_error(404)` sendet keine Security-Header; die Version ist ohne Anmeldung sichtbar.
- N6: Login und Logout verlangen keinen Custom Header, dadurch ist Login-CSRF möglich.
- N7: `server_audit.action`/`detail` und Zeitstempel kommen vom Client; neue Projekte werden nicht feldweise geprüft.
- **N8 (geprüft): `server.py:1349` `if wsid and wsid not in step_ids and len(wsid) > 80`. Verwaiste Verweise mit höchstens 80 Zeichen werden akzeptiert, gemeint ist vermutlich `or`.**
- N9: Der Archivfilter vergleicht UTC-`finishedAt` mit lokalen Datumsangaben, Fertigmeldungen zwischen 00:00 und 02:00 Uhr landen am Vortag.
- N10: Migrationen beim Start ändern den Stand, ohne die Revision zu erhöhen.
- N11: Jedes Mitglied kann jede Nachricht behalten (📌), auch in „Alle“, und so die Löschfrist aushebeln; Admin ohne Mitgliedschaft bekommt 404; kein Ratenlimit für Chat-Nachrichten.
- N12: `DELETE FROM sessions` läuft bei jeder Anfrage; `LOGIN_FAILS` wird nie geleert; der String `"false"` für `active` gilt als wahr; die Bereichs-ID neuer Benutzer wird nicht geprüft.

**Frontend**
- CSS-Injection über `ci.font` im Druckfenster (nur Admin), Abhilfe: Whitelist für Schriftnamen.
- Abwesenheiten und Krankmeldungen bleiben im localStorage gemeinsam genutzter PCs, bis sich jemand abmeldet (nicht beim Ablauf der Sitzung).
- Die Entprellung überschreibt `pendingRemoteAction`, im Server-Audit steht nur die letzte Aktion.
- `renderAll` überschreibt ungespeicherte Eingaben in nicht fokussierten Feldern.
- Begriffe uneinheitlich („Report“ vs. „Auswertung“, „Mitarbeiter“ vs. „Personal“).
- `runSchedulerTests` wird produktiv mit ausgeliefert; das Logo ist als base64 eingebettet, der Firmenname fest im Code.

**Betrieb**
- Rollback ist nicht atomar (Dateien werden einzeln kopiert); der Task wird beim Rollback mit festen Werten neu angelegt.
- Die Kopie der Live-DB für den Vorabtest in `%TEMP%` bleibt bei einem Abbruch liegen (inkl. Passwort-Hashes).
- `logs\` und `LAN_CONFIG.json` sind für alle lokalen Benutzer lesbar.
- `'$($pkg.FullName)'` bricht bei einem Apostroph im Profilpfad.
- Versionsangabe uneinheitlich: `README.md` nennt 12.9.1, alle anderen Stellen 12.10.1. Keine Git-Tags, kein `package.json` (Playwright-Version), kein `requirements.txt`.

---

## 4. Verbesserungs- und Feature-Liste (priorisiert)

Aufwand: S < 1 Tag · M 1–3 Tage · L 1–2 Wochen.

### P0 – Sicherheit und Datenverlust (sofort)

| # | Maßnahme | Befunde | Aufwand |
|---|---|---|---|
| V-01 | **Update-Kette absichern:** Deploy nur aus signierten bzw. getaggten Releases von `main`, SHA256 im erhöhten Staging-Ordner prüfen, Branch-Schutz | O-H1, O-M2 | M |
| V-02 | **Login härten:** Sperre je Benutzer und IP, atomare Reservierung, auch beim Passwortwechsel, Socket-Timeout, kleines Body-Limit, Host/Origin-Prüfung | B-H2, B-M1, B-M2, N3, N6 | S |
| V-03 | **Datenverlust im Client stoppen:** `beforeunload`, lokaler Cache in eigenem `try`, offline erst nach 3 Fehlern, Recovery-Kopie bei Konflikt | F-H1 (Teil), F-H2, F-H4, F-M4 | S |
| V-04 | **Rechtefehler Formate korrigieren** (AV) und `N8` (`and` → `or`), Validierung von `ui`/`meta`/`planVersions`, globale 500-Hülle | B-M5, N1, N2, N8 | S |
| V-05 | **Backup robust machen:** zuerst `.tmp`, `LastTaskResult` prüfen, `update_backups` rotieren, Restore-Skript plus Test | O-M3, O-M5, O-M6 | M |

### P1 – Betriebssicherheit und Performance (nächster Release)

| # | Maßnahme | Befunde | Aufwand |
|---|---|---|---|
| V-06 | **TLS** mit Zertifikat einer internen CA (oder Caddy/IIS als Reverse Proxy), Cookie `Secure`, HSTS | B-H1 | M |
| V-07 | **Least Privilege:** Dienstkonto statt SYSTEM, ACL-Prüfung des Python-Ordners, tzdata mit Hash | O-H2, O-H3 | M |
| V-08 | **Merge auf Feldebene** für `workSteps`, `projects` und `processes` inklusive `pos`-Umsortierung | F-H1 | M |
| V-09 | **Render-Performance:** `calcSchedule` memoizen, nur die aktive Ansicht rendern | F-H5 | M |
| V-10 | **Server-Durchsatz:** Validierung außerhalb von `DB_LOCK`, Lookup-Dicts, Chat-Kanalliste mit einer aggregierten Abfrage, Polling mit Backoff und Pause bei `hidden` | B-M3, B-M6, F-M3 | M |
| V-11 | **CI:** GitHub Actions (Python-Tests, Playwright-E2E, PSScriptAnalyzer), `package.json`/`requirements.txt`, Git-Tags, Version nur an einer Stelle | O-M7, Hygiene | S |
| V-12 | **Korrekturen:** CSV in Ortszeit mit Formelschutz, Arbeitstage mit Feiertagen, Archivfilter in Europe/Berlin, Chat-Race | F-M1, F-M2, N9, F-M5 | S |

### P2 – Architektur (mittelfristig)

| # | Maßnahme | Nutzen | Aufwand |
|---|---|---|---|
| V-13 | **Delta-Sync statt ganzem Stand:** `PATCH /api/workSteps/:id` usw., Server sendet Änderungsereignisse (SSE statt 250-ms-Polling) | Weniger 409-Konflikte, Netzlast −90 %, Grundlage für Live-Kollaboration | L |
| V-14 | **Normalisiertes Schema:** Aufträge, Projekte, Personal und Formate in eigenen SQLite-Tabellen statt JSON-Blob | Indizes, Abfragen, Audit je Datensatz | L |
| V-15 | **Modularisierung:** `server.py` aufteilen in `db/`, `validation/`, `permissions/`, `chat/`, `http/` mit Routing-Tabelle; `index.html` in ES-Module (`state`, `sync`, `scheduler`, `views/*`) | Bessere Reviews und Tests, weniger Regressionen | L |
| V-16 | **Rechtematrix aus einer Quelle** (Server liefert sie, Client wertet sie aus) | Keine 403-Abweichungen | M |
| V-17 | **Server-Audit serverseitig erzeugen** (Diff statt Client-Text, Serverzeit), mit Aufbewahrungsregel | Revisionssicheres Protokoll | M |

### P3 – Neue Funktionen (fachlicher Mehrwert)

| # | Feature | Beschreibung | Aufwand |
|---|---|---|---|
| F-01 | **Undo/Redo** | Lokaler Befehls-Stack (Strg+Z) für Verschieben, Prio und Statusänderungen | M |
| F-02 | **Benachrichtigungen** | Browser-Push bzw. Notification API für @Erwähnungen, gefährdete Liefertermine und Freigaben; optional E-Mail-Digest per SMTP | M |
| F-03 | **Feiertagskalender automatisch** | Gesetzliche Feiertage je Bundesland berechnen (Osterformel), ICS-Import für Betriebsferien | S |
| F-04 | **Kapazitäts-Szenarien** | „Was wäre wenn“: Planstand kopieren, Eilauftrag oder Maschinenausfall simulieren, Delta zu Liefertermin und Auslastung anzeigen | L |
| F-05 | **Touch- und Tastatur-Planung** | Pointer Events für das Board (Tablet in der Halle), Tastenkürzel zum Verschieben | M |
| F-06 | **Shopfloor-Terminal** | Vereinfachte Vollbildansicht je Maschine: aktueller und nächster Auftrag, Start/Stop/Fertig, große Schaltflächen, QR-Code des Auftrags | M |
| F-07 | **KPI-Dashboard** | OEE-Näherung (Plan vs. Ist), Liefertreue in %, Rüstanteil, Auslastung je KW; Export nach Excel/Power BI (CSV/OData-artiger JSON-Endpunkt `/api/export/*`) | M |
| F-08 | **ERP-Schnittstelle** | Import von Aufträgen/AB aus CSV/XLSX mit Feldmapping; Rückmeldung der Ist-Zeiten | L |
| F-09 | **Messenger-Ausbau** | Dateianhänge und Fotos (z. B. Schadensbild), Nachrichten bearbeiten und löschen, Gruppe verlassen (bereits in UEBERGABE §3) | M |
| F-10 | **Personalbedarf je Parallelplatz** | Bekannte Grenze aus UEBERGABE §2 | M |
| F-11 | **Material/Platten-Bedarf** | Aus Formaten und Aufträgen Plattenbedarf je KW ableiten, Bestellvorschlag | M |
| F-12 | **Dark Mode / Hallenmodus** | Hoher Kontrast, große Schrift (behebt zugleich F-M7) | S |
| F-13 | **Druck/PDF Wochenplan** | Druckansicht A3 quer je Bereich und KW | S |
| F-14 | **Sitzungsverwaltung** | Admin sieht aktive Sitzungen und kann sie beenden; Idle-Timeout; Passwortregeln; optional TOTP für Admin | M |

---

## 5. Empfohlene Reihenfolge

1. **Release 12.10.2 (Hotfix, ca. 2 Tage):** V-02, V-03, V-04, V-12, README-Version
2. **Release 12.11 (ca. 1 Woche):** V-01, V-05, V-07, V-11
3. **Release 12.12 (ca. 2 Wochen):** V-06, V-08, V-09, V-10, F-03, F-12, F-13
4. **Release 13.0 (Architektur):** V-13 bis V-17, danach F-01, F-04, F-06, F-07

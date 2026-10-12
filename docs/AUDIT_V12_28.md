# Deep Audit V12.28.3

Stand: 12.10.2026 · Branch `claude/happy-thompson-fuifkm` · Basis `main` = V12.28.3 (`327f4b5`).
Vorgänger: `docs/AUDIT.md` (V12.10.1), `docs/AUDIT_V12_21.md` (V12.21.0).

**Prüftiefe:** `server.py` vollständig für HTTP-Schicht, Anmeldung, Sitzungen, Benutzer-/Rollenverwaltung,
Bereichsredaktion (`read_scope`/`redact_state`/`unredact_incoming`), Schreibrechte je Rolle, Historie,
Produktion, Chat, Benachrichtigungen, Lizenz und Vorlagen. Außerdem `mp_rights.py`, `mp_license.py`, `mp_tls.py`,
`mp_config.py` (Schreibpfade), `app_updates.py`, `Umzug.py` und `Backup_Datenbank.py` (Entpacken).
Geprüft wurde auch über HTTP gegen einen Testserver, dazu ein Typ-Fuzz der Validierungen
(jedes Feld × 16 falsche Werte × Rollen). In `index.html` wurden nur die Escape-Stellen geprüft.
PowerShell-Skripte wurden nur stichprobenartig gelesen (Injection/ACL); auf Windows deckt sie die CI ab.

## Testergebnis (lokal, Linux)

| Test | vor Fix | nach Fix |
|---|---|---|
| Python, 44 Dateien aus `ci.yml` | grün | grün (inkl. 2 neue Tests) |
| Regression künstliche DB | 151/151 | 151/151 |
| STRICT-Upgrade | 313/313 | grün |
| UI-Smoke + Browser-E2E (36 Dateien) | – | alle grün |

## Befunde

| ID | Schwere | Ort | Befund | Status |
|---|---|---|---|---|
| **A-1** | **Hoch** | `mp_rights.resolve_profile_id` | Eine Bereichsleitung konnte ein vom Admin zugewiesenes Rollenprofil eines Stellvertreters oder Lesenden entfernen. Das ging direkt per `PATCH /api/users/<id> {"profileId": ""}` oder indirekt über einen Rollenwechsel (Stellvertretung → Lesend → Stellvertretung), der das Profil verwirft. Danach hatte der Benutzer wieder die vollen Rechte seiner Systemrolle, z. B. Fertigmelden, Benutzer verwalten oder Personal sehen. Per HTTP nachgewiesen. | **behoben**: Nicht-Admins können ein Profil weder ändern noch entfernen, ein Rollenwechsel mit Profilverlust wird mit MP-ROLE-003 abgelehnt. Test `AuditV12283.test_lead_cannot_remove_admin_profile`. |
| **A-2** | **Mittel** | `department_change_allowed` (Historie) | Für die Bereichszuordnung neuer Historieneinträge zählten nur Aufträge der Art MACHINE. Ein Eintrag ohne passenden Auftrag galt daher als Standardbereich (erster Produktionsbereich). (a) Die Leitung dieses Bereichs konnte dauerhafte Einträge (append-only) für **fremde** Bereiche anlegen, mit beliebigen Mengen und Stunden; diese fließen in Report und Historie ein. (b) Bereiche mit Stunden-/Prozessplanung konnten einen freigegebenen Auftrag nicht stornieren (falsches 403). | **behoben**: Neue Historie entsteht nur zu einem Auftrag des eigenen Bereichs, der im selben Speichern entfällt (jede Planungsart); der Bereich am Eintrag muss passen. Test `AuditV12283.test_history_only_from_own_removed_order`. |
| A-3 | Niedrig | `Handler.request_origin_ok` | Beim Origin-Vergleich galt ohne Portangabe immer Port 80. Bei HTTPS an Port 443 wären deshalb alle Schreibzugriffe mit MP-REQ-403 abgelehnt worden. | **behoben** (Standardport je Schema). |
| A-4 | Mittel (Design) | `mp_tls.x509_certificate` | Die Firmen-CA ist eine uneingeschränkte Stamm-CA ohne NameConstraints und ohne pathLen. Ihr privater Schlüssel liegt auf dem Server (`config\tls\firmen-ca.key`, wird für die automatische Erneuerung gebraucht). Wer den Server übernimmt, kann für **jede** Domain Zertifikate ausstellen, denen alle Arbeitsplätze mit importierter CA vertrauen. | offen. Empfehlung: neue CAs mit NameConstraints (Server-IP/Subnetz, PC-Name, DNS-Suffix) und `pathLenConstraint=0`. Bestehende CAs bleiben uneingeschränkt; das ist nur mit neuer CA und erneutem Import auf den Arbeitsplätzen lösbar. Vorher auf den Ziel-Browsern testen. |
| A-5 | Niedrig | `ROLE_ACTIONS["updates"]` | Das Aktionsrecht „Updates installieren“ lässt sich im Rollenprofil einstellen, wirkt aber nirgends: Installieren darf nur der Admin, und Profile können nicht auf Admin beruhen. | offen. Recht entfernen oder als „nur Admin“ kennzeichnen. |
| A-6 | Niedrig | `chat_post` `/api/chat/members` | `add`/`remove` werden nicht auf Listen geprüft. Ist `add` eine Zahl, entsteht ein TypeError, der als MP-SRV-500 abgefangen wird. Ist `remove` ein Text, wird er zeichenweise ausgewertet. | offen (nur Robustheit). |
| A-7 | Niedrig | `validate_state` und Rollenprüfungen | Falsche Typen in Wurzel-Sammlungen führen an einigen Stellen zu MP-SRV-500 statt 400. Der Fuzz zeigt: Alles wird zurückgerollt, und kein Nicht-Admin kann einen Stand speichern, der das Lesen (`redact_state`) für andere bricht. | offen (nur Robustheit). |
| A-8 | Info | Firmen-Schriftart im Druckfenster | `ci.font` wird HTML-, aber nicht CSS-maskiert. CSS-Injection ist nur durch den Admin möglich (Altbefund). | unverändert. |

## Geprüft ohne Befund

- **Anmeldung:** Sperre je IP, Benutzer und IP+Benutzer, atomar reserviert; Zeitverhalten bei unbekannten Benutzern wie bei bekannten. Gesperrte Konten werden erst nach dem richtigen Passwort genannt. Die Sitzung ist gehasht, `HttpOnly`, `SameSite=Strict` und bei HTTPS `Secure`.
- **CSRF/Rebinding:** Alle Schreibpfade verlangen `X-MP-Client-Version`; dazu kommen Host-/Origin-Prüfung und ein Body-Limit vor der Anmeldung.
- **Bereichsredaktion:** Fremde Datensätze mit gleicher ID werden über den alten Stand erkannt und abgelehnt. Ausgeblendete Funktionen werden vor der Rechteprüfung wiederhergestellt.
- **Produktion/Bedarf:** idempotent je Benutzer und Request-ID, Bereich wird auch beim Replay geprüft. Status `done`/`released` lässt sich nicht per State-PUT setzen.
- **Updates:** nur Admin. Quelle ist nicht über die Oberfläche änderbar (`update` fehlt in `CONFIG_WRITE_SECTIONS`). Release muss auf `main` liegen, SHA256 gilt je Paket und je Datei, Zip-Slip und Links werden geprüft.
- **Lizenz:** RSA-PKCS#1-v1.5-Prüfung durch vollständigen Vergleich der Kodierung (keine Fälschung über lockeres Parsen).
- **Umzug/Backup:** Dateinamen und Hashes im Paket werden vor dem Entpacken geprüft, `resolve()` bleibt im Zielordner.
- **Frontend:** `escapeHtml` an den geprüften Stellen (Fehlerprotokoll, Historie, Chat-Vorschau, Titel).

## Nächste Schritte

1. Korrekturen A-1 bis A-3: V12.28.4 (Version, Release Notes und PR erledigt; Release nach Merge über den Workflow).
2. A-4 entscheiden: Ist eine neue, eingeschränkte Firmen-CA mit erneutem Import auf den Arbeitsplätzen vertretbar?
3. A-5 bis A-7 bei Gelegenheit bereinigen.

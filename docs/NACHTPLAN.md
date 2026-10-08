# Nachtplan 08./09.10.2026 – Issues #73, #55, #51, #52

Auftrag (Jonas, 08.10. abends): über Nacht selbständig an #73, #55, #51, #52 arbeiten. Dabei immer nur **ein Sub-Agent gleichzeitig** (Haiku, Sonnet oder Opus je nach Aufgabe) und das Sitzungslimit nicht überschreiten. **Keine Rückfragen** über Nacht – offene Fragen sammeln (unten) und morgen im Büro stellen.

## Regeln
- **Ein Sub-Agent gleichzeitig.** Der Orchestrator (Hauptsitzung) prüft das Ergebnis, pusht und startet erst danach den nächsten.
- **Modellwahl:**
  - Haiku: Doku, Listen, reine Leseanalysen
  - Sonnet: Umsetzung mit Tests
  - Opus: riskante Umbauten (Phase 1) und Reviews
- **Takt:** Weckruf etwa alle 40 Minuten (`send_later`).
  - Läuft noch ein Sub-Agent, wird nur neu geweckt.
  - Bei Rate-Limit: Reset-Zeit notieren, danach erst wieder wecken.
- **Kein Deploy, kein Release, keine Tags.** PRs werden gemergt, wenn die CI grün ist (main bleibt grün). Release erst mit Jonas.
- **Sicherheitsnetz:**
  - `tests/golden/*` (Datenstand, Rechte-Matrix, Planung) darf sich nur durch gewollte, im Commit begründete Änderungen bewegen.
  - Upgrade-Test 313/313 bleibt grün.
- **Keine Entscheidungen mit Außenwirkung:** Lizenz/Recht, HTTPS-Standard, Entfernen des Einrichtungsschritts „Branche“ (Deep Dive am 09.10.). Das kommt unter „Fragen“.

## Warteschlange (Status: offen / läuft / erledigt + Commit)
| # | Issue | Aufgabe | Modell | Status |
|---|---|---|---|---|
| 1 | #73 Phase 0 | Rechte-Matrix für Schreibzugriffe (Rolle × Datenbereich) als Referenztest | Sonnet | erledigt (Commit „Phase 0: Schreib-Matrix (Rolle × Datenbereich) als Referenztest (#73)“) |
| 2 | #73 Phase 0 | Release-Regeln dokumentieren (nur über den Workflow; GitHub-Regeln für Tags) | Haiku | erledigt (Commit „Doku: Release-Regeln (#73)“) |
| 3 | #55 | Warnung bei riskanten Rollen-Kombinationen im Rolleneditor | Sonnet | erledigt (Commit „#55: Warnung bei riskanten Rollen-Kombinationen“) |
| 4 | #51 | Inventur der fest verdrahteten IDs (cnc, thermoforming, konf …) – nur Analyse | Haiku | erledigt (Commit „#51: Inventur fest verdrahteter IDs“) |
| 5 | #51 | Fest verdrahtete IDs in der Logik durch Bereichs-Flags ersetzen (nach #4) | Sonnet | erledigt (Commit „#51: Bereichs-Flags statt fester IDs (Bedienerkapazität, Fallback-Bereich)“) |
| 6 | #73 Phase 1 | Backend-Trennung Schritt 1: Konfiguration → `mp_config.py` (flach, Entscheidung 10) | Opus | erledigt (flach als `mp_config.py`; Commit „#73 Phase 1: Konfiguration nach mp_config.py“) |
| 6a | #73 Phase 1 | Vorbereitung: Updater, PowerShell-Kopie, Preflight, Rollback und Tests können Dateien in Unterordnern (`core/x.py`), noch ohne verschobenen Code | Opus | erledigt (Commit „Phase 1 Vorbereitung: Updater und Skripte unterstützen Unterordner (#73)“) |
| 7 | #73 Phase 1 | Schritt 2: TLS → `mp_tls.py` (flach, Entscheidung 10) | Opus | erledigt (flach als `mp_tls.py`; Commit „#73 Phase 1: TLS nach mp_tls.py“) |
| 8 | #73 Phase 1 | Review der Schritte 6–7 (rein lesend) | Opus | erledigt (Review: keine Blocker; Nacharbeit Commit „Phase 1 Vorbereitung: Review-Nacharbeit Updater (#73)“) |
| 9 | #52 K6 | System-Status/Diagnosepaket für den Admin (ohne Geheimnisse) | Sonnet | erledigt (Commit „#52 K6: Systemstatus und Diagnosepaket für den Admin“) |

## Entscheidungen Jonas (09.10.)
| # | Frage | Antwort | Umsetzung |
|---|---|---|---|
| 1 | Vertrieb darf Projekte bearbeiten? | Nein | bleibt so (Schreib-Matrix: Vertrieb schreibt nichts) |
| 2 | GF darf Bereiche ändern? | Ja | bleibt so |
| 3 | Unveränderte Rollen-Kopie zeigt drei Warnungen | Nur ein Hinweis, Speichern geht | MP-ROLE-014 (PR #82) |
| 4 | README/Updater beschreiben Release von Hand | Claude entscheidet | umgeschrieben auf den Workflow-Weg (PR #82) |
| 5 | Tag-Push veröffentlicht sofort | Claude entscheidet | Tag-Weg abgeschaltet, nur noch Entwurf (PR #82) |
| 6 | Release-Text = ganze RELEASE_NOTES | Claude entscheidet | nur oberster Abschnitt, Assets geprüft (PR #82) |
| 7 | Einrichtungsschritt „Branche“ entfernen? | Ja | Assistent mit 4 Schritten, Vorlagen bleiben im Firmenprofil (PR #82) |
| 8 | HTTPS Standard? Lizenz? | Ja; Lizenzen zum Verkauf an andere Firmen | HTTPS bei Neuinstallation (PR #82); Lizenzschlüssel: 30 Tage Kulanz, dann nur lesen; unbefristet möglich; Bestand bekommt Schlüssel |
| 9 | Wann wird installiert? | Admin klickt in der Oberfläche auf Update | Geprüft wird mit dem installierten Updater → Unterordner erst ein Release später |
| 10 | `core/` oder flach? | Claude entscheidet | flach (`mp_config.py`, `mp_tls.py`), geht ohne Zwischen-Release |

## Nachtschicht 2 (08./09.10.) – Warteschlange
Gleiche Regeln wie oben (ein Sub-Agent gleichzeitig, Weckruf alle 60 min, bei Limit Reset abwarten, kein Release/Tag/Deploy).
| # | Aufgabe | Modell | Status |
|---|---|---|---|
| N1 | #52 Lizenzschlüssel (30 Tage Kulanz, dann nur Lesen; ohne öffentlichen Schlüssel inaktiv) | Opus | erledigt (cad15a3) |
| N2 | Phase 1 flach: Konfiguration → `mp_config.py`, TLS → `mp_tls.py` | Opus | erledigt (cc5481c, a60891a) |
| N3 | PR #82 mergen, wenn grün | – | offen |
| N4 | Offene Issues (#73 Roadmap, #52, #50, #47, #49, #54) lesen: Aufgaben ohne Produktentscheidung auswählen, hier eintragen | Haiku | erledigt → N5a–N5c |
| N5a | #52 K1 Negativtests Sicherheit (Logout/abgelaufene Session, Übergröße, Upload, Pfade) + #50 Backup-Zweitziel/Exitcode 2 – nur Tests, Befunde melden | Sonnet | läuft |
| N5b | #73 Phase 1: Rollen & Rechte → `mp_rights.py` (flach, nur verschieben; Golden-Tests Rechte/Scope/Schreib-Matrix) | Opus | offen |
| N5c | #47 Charge: reine Rechenfunktion ceil(Menge/Chargengröße) × Dauer mit Unit-Tests, ohne Verdrahtung | Sonnet | offen, nur wenn Zeit |

Fragen aus N4 für Jonas: Gate 0 (Rechte am Code) vor Verkauf? · Rollback: nur Code oder auch Daten (#50)? · Lizenz bei Serverumzug (#52 K3)? · Code-Signing-Zertifikat kaufen? · Charge parallel/sequenziell, Reinigung zwischen Chargen (#47)? · Lager (#54) in Release 1.0? · Scheduler-Performance-Ziel für 1500 FA?

## Fragen für morgen (Jonas) – beantwortet, siehe oben
- Rechte: Darf der Vertrieb Projekte bearbeiten? (heute nein)
- Rechte: GF darf Bereiche ändern – so gewollt?
- #55: Eine unveränderte Kopie der Rolle Abteilungsleitung zeigt sofort alle drei Warnungen (MP-ROLE-011/012/013). Zu laut, oder genau richtig als Hinweis?
- Release: `README_Windows.txt` Abschnitt „RELEASE ANLEGEN“ und die Fehlermeldung in `Update_von_GitHub.ps1` beschreiben noch das Anlegen von Tag/Release von Hand. Auf den Workflow-Weg umschreiben? (siehe docs/RELEASE_REGELN.md)
- Release: Ein Tag-Push `v*` veröffentlicht sofort ohne Entwurf. Soll der Tag-Weg abgeschaltet werden (nur noch manueller Lauf → Entwurf)?
- Release: Der Schritt „checked assets“ prüft nichts, und die komplette RELEASE_NOTES.txt (83 KB) wird Release-Text. Kürzen auf den obersten Abschnitt?
- Einrichtungsschritt „Branche“ entfernen? (Deep Dive 09.10., #51/#53)
- HTTPS als Standard? Lizenz? (#52)
- Wann wird V12.27 auf Windows installiert?
- Phase 1: Backend-Module in den Ordner `core/` (braucht ein Zwischen-Release mit Ordner-Unterstützung im Updater, dann folgt die Verschiebung ein Release später) oder flach als `mp_config.py`/`mp_tls.py` neben server.py (geht sofort)? Über Nacht wird nur die Ordner-Unterstützung vorbereitet (6a); verschoben wird noch nichts.

## Beobachtungen (kein Handlungsbedarf über Nacht)
- Firmenprofil: Schnell nacheinander geänderte Felder speichern einzeln. Eine ältere Serverantwort kann die gerade gewählte Akzentfarbe kurz mit dem alten Stand überschreiben, bis die eigene Antwort ankommt. Der Endzustand stimmt. Kleiner Client-Schönheitsfehler, im Test jetzt berücksichtigt.
- CI-e2e: Heute schlugen nacheinander verschiedene Browser-Tests einmalig fehl (Bedarf, Ruhezeit, Firmenprofil), alle lokal grün. Bei Wiederholung den jeweiligen Test gezielt robuster machen.

## Protokoll
- 08.10. abends: Plan angelegt. PR #80 (V12.27 Teil 2) offen.
- 08./09.10.: Warteschlange abgearbeitet, PR #81 gemergt. Antworten von Jonas umgesetzt in PR #82.

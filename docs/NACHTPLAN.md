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
| 4 | #51 | Inventur der fest verdrahteten IDs (cnc, thermoforming, konf …) – nur Analyse | Haiku | offen |
| 5 | #51 | Fest verdrahtete IDs in der Logik durch Bereichs-Flags ersetzen (nach #4) | Sonnet | offen |
| 6 | #73 Phase 1 | Backend-Trennung Schritt 1: Konfiguration → `core/config.py`, keine Funktionsänderung, Paketliste anpassen | Opus | offen |
| 7 | #73 Phase 1 | Schritt 2: TLS → `core/tls.py` | Sonnet | offen |
| 8 | #73 Phase 1 | Review der Schritte 6–7 (rein lesend) | Opus | offen |
| 9 | #52 K6 | System-Status/Diagnosepaket für den Admin (ohne Geheimnisse) | Sonnet | offen |

## Fragen für morgen (Jonas)
- Rechte: Darf der Vertrieb Projekte bearbeiten? (heute nein)
- Rechte: GF darf Bereiche ändern – so gewollt?
- #55: Eine unveränderte Kopie der Rolle Abteilungsleitung zeigt sofort alle drei Warnungen (MP-ROLE-011/012/013). Zu laut, oder genau richtig als Hinweis?
- Release: `README_Windows.txt` Abschnitt „RELEASE ANLEGEN“ und die Fehlermeldung in `Update_von_GitHub.ps1` beschreiben noch das Anlegen von Tag/Release von Hand. Auf den Workflow-Weg umschreiben? (siehe docs/RELEASE_REGELN.md)
- Release: Ein Tag-Push `v*` veröffentlicht sofort ohne Entwurf. Soll der Tag-Weg abgeschaltet werden (nur noch manueller Lauf → Entwurf)?
- Release: Der Schritt „checked assets“ prüft nichts, und die komplette RELEASE_NOTES.txt (83 KB) wird Release-Text. Kürzen auf den obersten Abschnitt?
- Einrichtungsschritt „Branche“ entfernen? (Deep Dive 09.10., #51/#53)
- HTTPS als Standard? Lizenz? (#52)
- Wann wird V12.27 auf Windows installiert?

## Protokoll
- 08.10. abends: Plan angelegt. PR #80 (V12.27 Teil 2) offen.

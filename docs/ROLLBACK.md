# Rollback und Rückkehr zu einer älteren Version

Stand V12.27.0. Entscheidung Jonas (09.10.2026): **Ein Rollback betrifft nur den Programmcode.** Neuere
Produktionsdaten werden nie verworfen.

## 1. Was beim Update automatisch zurückgerollt wird

Gilt für `UPDATE_LIVE.ps1`, für das Softwareupdate in der Oberfläche (es ruft `UPDATE_LIVE.ps1` auf) und für
`Update_von_GitHub.ps1`.

| Fehler tritt auf … | Programmcode | Datenbank |
| --- | --- | --- |
| bei Online-Backup oder Vorabtest (Schritte 1–2) | unverändert, Live wurde nicht berührt | unverändert |
| nach dem Stopp, aber bevor die neue Version startet (Schritte 3–7) | alter Code zurück | unverändert. Die neue Version hat sie nie geöffnet |
| nachdem die neue Version gestartet ist (Migration, Health- oder Sicherheitscheck) | alter Code zurück | Stand aus dem finalen Backup (Schritt 4, nach dem Stopp). Die neue Version kann schon migriert haben, und der alte Code liest das evtl. nicht |
| nach bestandenem Health- und Sicherheitscheck (Aufräumen, Statusdatei) | kein Rollback, das Update gilt | unverändert |
| beim Wechsel in einen neuen Installationsordner | alter Ordner und alter Task bleiben | alter Ordner unverändert |

**Warum dabei keine Daten verloren gehen:** Ab Schritt 3 (vor dem Stopp) liegt die Wartungssperre
`updates\installing`. Bis Health- und Sicherheitscheck bestanden sind, lehnt der Server jede Änderung mit
**503 / MP-UPD-003** ab. Das gilt für Speichern, Produktions- und Bedarfsaktionen, Benutzer, Rollen, Firmenprofil,
Logo, Lizenz, Chat, Benachrichtigungen und das eigene Passwort. Lesen und Anmelden bleiben möglich; eine Anmeldung
schreibt nur eine Sitzung. Änderungen zwischen Online-Backup und Stopp sind im finalen Backup enthalten.
Im Rollback wird die Sperre erst entfernt, wenn die alte Version den Health-Check bestanden hat. Den Nachweis
führt `tests/test_update_lock.py`: Der Test liest alle schreibenden Routen aus `server.py`, prüft für jede die
Sperre und vergleicht den Datenbankinhalt. Außerdem prüft er die Reihenfolge in `UPDATE_LIVE.ps1`.

Die ersetzte, evtl. migrierte Datenbank bleibt zur Kontrolle in `update_backups\fehlversuch_V<neu>_<Zeit>\`.

**Was nie automatisch passiert:**

- Eine ältere Sicherung aus `backups\` oder `update_backups\` wird nie eingespielt. Einzige Ausnahme ist der
  oben genannte Fall innerhalb desselben Updatelaufs. Er gilt nur, solange die Sperre aktiv ist.
- Es werden keine Daten gelöscht.
- Ein Update wird nach dem Entsperren nicht mehr zurückgenommen.

## 2. Manuell zurück zu einer älteren Version (nur Code)

PowerShell (kein Administrator nötig, das UAC-Fenster folgt):

    powershell -ExecutionPolicy Bypass -File C:\ProgramData\Maschinenplanung\Update_von_GitHub.ps1 -Tag v12.26.0 -Rueckstufen

Ohne Internet, mit einem entpackten älteren Paket (PowerShell als Administrator):

    C:\ProgramData\Maschinenplanung\UPDATE_LIVE.ps1 -Paket D:\Paket_V12.26.0 -Rueckstufen

So läuft die Rückstufung ab:

- Der **installierte** (neuere) Updater übernimmt Ablauf und Sicherungen. Aus dem alten Paket kommen nur die
  Programmdateien. Dafür muss V12.27.0 oder neuer installiert sein.
- Die Schritte sind dieselben wie beim Update: Online-Backup und Vorabtest der alten Version mit einer
  **Kopie der aktuellen Daten**. Danach folgen Sperre, finales Backup, Code-Tausch, Health-Check und bei einem
  Fehler der Rollback auf die neuere Version (Abschnitt 1).
- Die Datenbank bleibt der aktuelle Stand. Es wird **keine** alte Sicherung eingespielt.
- Ohne `-Rueckstufen` bricht der Updater ab: „Live ist V…, Paket ist älter“. Das gilt auch, wenn der Server gerade
  gestoppt ist, weil die installierte Version zählt.
- Das Softwareupdate in der Oberfläche bietet nur neuere Versionen an und stuft nie zurück.
- Dateien, die nur die neuere Version kennt (z. B. `Umzug.py`), bleiben im Ordner liegen. Sie stören nicht.

## 3. Datengarantie, wenn eine ältere Version mit neueren Daten läuft

- **Migrationen sind additiv** (Projektregel). Neue Tabellen, Spalten und Felder werden ergänzt, nichts wird
  entfernt oder umbenannt. Die ältere Version ignoriert, was sie nicht kennt. `tests/test_deploy_upgrade.py`
  (Schritt 6) startet bei jedem CI-Lauf die älteren Stände auf den migrierten Daten. Er prüft, dass Logins,
  Daten und die neu ergänzten Felder erhalten bleiben.
- **Startet die ältere Version mit den aktuellen Daten nicht**, scheitert schon der Vorabtest. Die Meldung lautet
  „Vorabtest fehlgeschlagen: V… startet mit einer Kopie der Live-Daten nicht. Live-System wurde NICHT verändert.“
  Daten und laufende Version bleiben, es wird nichts zurückgespielt. Dann bei der neueren Version bleiben und den
  Entwickler fragen.
- `config\firma.json` mit einer neueren `schemaVersion`: Die ältere Version startet, das Firmenprofil ist dann
  schreibgeschützt (MP-CFG-003).
- Grenze: Felder, die nur die neuere Version kennt, bleiben in der Datenbank. Die ältere Oberfläche übernimmt
  unbekannte Felder beim Speichern in den meisten Datensätzen mit. Eine Garantie für jedes Feld gibt es nicht.
  Die Rückstufung ist deshalb eine Notlösung; bald wieder aktualisieren. Vorher entstehen immer Sicherungen
  (`backups\maschinenplanung_*.sqlite3`, `update_backups\pre_V*`).
- Wer wirklich einen alten Datenstand braucht, spielt ihn nur bewusst mit `Restore_Datenbank.ps1` ein. Der aktuelle
  Stand wird dabei vorher als `*_vor_restore` gesichert.

Fehlercodes: MP-UPD-003 (Wartungssperre), MP-UPD-005 (Rollback-Health-Check fehlgeschlagen, Sperre bleibt),
MP-UPD-007 (`-Paket` ohne `-Rueckstufen`), siehe `FEHLERCODES.txt`.

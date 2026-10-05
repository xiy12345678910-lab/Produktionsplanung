# Update beim Arbeitgeber: V12.10.1 auf V12.17.0

Server-PC, Windows PowerShell **als Administrator** (Rechtsklick, "Als Administrator ausführen"), sofern nicht anders vermerkt.
Daten (`data\`, `backups\`, `LAN_CONFIG.json`) bleiben erhalten. Das Skript sichert vorher selbst und spielt bei einem Fehler automatisch alles zurück.

Voraussetzung: Der PR ist nach `main` gemergt und das Release `v12.17.0` liegt auf `main`. Sonst bricht der Updater mit "Tag nicht gefunden" bzw. "ausserhalb von main" ab (Live bleibt unverändert).

## 1. Vorher

**1.1 Installationsordner und Variablen** (Standard `C:\ProgramData\Maschinenplanung`; das Update ermittelt ihn selbst aus dem Task):

```powershell
$t = Get-ScheduledTask -TaskName 'Maschinenplanung Server'
$b = $t.Actions[0].WorkingDirectory; $b
$c = Get-Content "$b\LAN_CONFIG.json" -Raw | ConvertFrom-Json
```

Weicht `$b` von `C:\ProgramData\Maschinenplanung` ab, kopiert das Update dorthin um; der alte Ordner bleibt unverändert. Bei Firma_Einrichten dann `-Ziel $b` (siehe 3).

**1.2 Version prüfen.** Der Server lauscht nur auf der LAN-IP, nicht auf `localhost`:

```powershell
Invoke-RestMethod "http://$($c.lan_ip):$($c.port)/api/health"
```

Erwartet: `version 12.10.1`. Alternativ im Browser `http://<LAN-IP>:8765/api/health` (IP steht in `$c.lan_ip`).

**1.3 Manuelles Backup** (Skript aus V12.10.1; Ergebnis liegt in `$b\backups`):

```powershell
& "$b\Backup_Datenbank.ps1"
Get-ChildItem "$b\backups" -Filter 'maschinenplanung_*.sqlite3' | Sort-Object LastWriteTime -Descending | Select-Object -First 3 Name, Length, LastWriteTime
```

Exitcode 2 heißt: lokales Backup OK, Zweitkopie (`BACKUP_ZIEL.txt`) fehlgeschlagen. Zusätzlich die neueste `.sqlite3` auf einen USB-Stick kopieren.

**1.4 Steht ein Firmenname in `data.ci`?** Dann kommt `MP-CFG-006` nicht. Nur lesend:

```powershell
@'
import sqlite3, json, sys
con = sqlite3.connect("file:" + sys.argv[1].replace("\\", "/") + "?mode=ro", uri=True)
rev, js = con.execute("select revision, json from state where id=1").fetchone()
ci = json.loads(js).get("ci") or {}
name = (ci.get("company") or "").strip()
logo = str(ci.get("logo") or "").startswith("data:image")
print("Revision", rev, "| Firmenname:", repr(name), "| Logo:", "ja" if logo else "nein")
print("=> MP-CFG-006 kommt, Schritt 3 noetig" if rev > 1 and not name and not logo else "=> kein MP-CFG-006, Schritt 3 entfaellt")
'@ | Set-Content "$env:TEMP\ci_check.py" -Encoding UTF8
py -3 "$env:TEMP\ci_check.py" "$b\data\maschinenplanung.sqlite3"
```

(Ohne `py`: `python` statt `py -3`.) Name **oder** Logo genügt, dann ist kein Schritt 3 nötig.

## 2. Paket holen und installieren

Der alte `Update_von_GitHub.ps1` auf dem PC taugt nicht: Er kennt kein `-Tag`, nimmt standardmäßig den Branch `claude/new-session-mpx5ch` und prüft weder Release noch Commit. **Nicht verwenden.** Stattdessen den neuen Updater (aus dem Release `v12.17.0`) holen. Er prüft, dass der Tag auf `main` liegt, installiert genau diesen Commit und startet `UPDATE_LIVE.ps1` in einem Administrator-Fenster (UAC bestätigen).

Normale (nicht-Admin) PowerShell reicht; als Admin geht es auch:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$u = "$env:USERPROFILE\Downloads\Update_von_GitHub.ps1"
Invoke-WebRequest -UseBasicParsing -OutFile $u -Uri 'https://raw.githubusercontent.com/xiy12345678910-lab/Produktionsplanung/v12.17.0/Update_von_GitHub.ps1'
Unblock-File $u
powershell -ExecutionPolicy Bypass -File $u -Tag v12.17.0
```

Im Administrator-Fenster laufen 9 Schritte: Backup, Vorabtest mit Datenkopie auf Testport, Stopp, Backup, Programmstand sichern, Dateien, Tasks, Health, HTTP-Sicherheitscheck. Am Ende `UPDATE ERFOLGREICH - V12.17.0`.

**Alternative ohne Updater (z. B. Proxy blockiert die API):**
1. Im Browser `https://github.com/xiy12345678910-lab/Produktionsplanung/archive/refs/tags/v12.17.0.zip` laden.
2. Rechtsklick auf die ZIP, Eigenschaften, "Zulassen" bzw. nach dem Entpacken: `Get-ChildItem <Ordner> -Recurse -File | Unblock-File`.
3. In den entpackten Ordner (dort liegt `UPDATE_LIVE.ps1`) wechseln und als Administrator:

```powershell
Set-Location '<entpackter Ordner>'
Set-ExecutionPolicy -Scope Process Bypass
.\UPDATE_LIVE.ps1
```

## 3. Nur falls `MP-CFG-006` kam (oder Schritt 1.4 es angekündigt hat)

Meldung: "MP-CFG-006: Bestand ohne Firmenprofil ... Live-System wurde NICHT veraendert." Dann ist nichts kaputt. Vorlage holen, `config\firma.json` anlegen, Update fortsetzen. Alles in der Administrator-PowerShell:

```powershell
$v = "$env:TEMP\legacy_employer_seed.json"
Invoke-WebRequest -UseBasicParsing -OutFile $v -Uri 'https://raw.githubusercontent.com/xiy12345678910-lab/Produktionsplanung/v12.17.0/tools/legacy_employer_seed.json'
# neuer Paketordner (vom Updater entpackt, enthält UPDATE_LIVE.ps1):
$p = Get-ChildItem "$env:ProgramData\Maschinenplanung_Update" -Directory -Recurse -Filter 'Produktionsplanung-*' |
     Where-Object { Test-Path "$($_.FullName)\UPDATE_LIVE.ps1" } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Set-Location $p.FullName
.\Firma_Einrichten.ps1 -Vorlage $v          # bei abweichendem Ordner (1.1): zusaetzlich -Ziel $b
.\UPDATE_LIVE.ps1
```

Ergebnis von `Firma_Einrichten`: `Fertig: ...\config - jetzt UPDATE_LIVE.ps1 starten.` Existiert die `firma.json` schon, meldet es "nichts geaendert" (das ist in Ordnung). Bewusst ohne Firmenname: `-Neutral` statt `-Vorlage`.

## 4. Kontrolle

```powershell
Invoke-RestMethod "http://$($c.lan_ip):$($c.port)/api/health"      # version 12.17.0
& "$b\Server_Status.ps1"                                            # beide Tasks, Server V12.17.0, Backup nicht älter als 26 h
& "$b\CHECK_LAN_SICHERHEIT.ps1"                                     # alles PASS
```

Dann im Browser (jeder PC, jeder Browser): **Strg+F5**, Login mit jeder Rolle (Admin, GF, Projektmanagement, Arbeitsvorbereitung, Vertrieb, Abteilungsleiter, Lesend), Daten vollständig, Firmenname und Logo oben, Farbe stimmt.

## 5. Rückweg

**Automatisch:** Bricht `UPDATE_LIVE.ps1` ab (nach dem Stopp), stellt es selbst Code, Config, Datenbank und Task wieder her und startet den alten Stand (Meldung `Alter Stand wieder gestartet`). Vor dem Stopp (Vorabtest, MP-CFG-006) wurde nichts verändert. Auslösen kann man das nicht per Befehl.

**Von Hand, wenn das Update durchlief, aber etwas nicht stimmt.** Gesichert liegt der alte Stand in `$b\update_backups\pre_V12.17.0_<Zeitstempel>` (Dateien, `config\`, `maschinenplanung_vor_update.sqlite3`). Achtung: Eingaben seit dem Update gehen dabei verloren.

```powershell
. "$b\MP_Common.ps1"
Stop-MPServer $b
$r = Get-ChildItem "$b\update_backups" -Directory -Filter 'pre_V12.17.0_*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-ChildItem $r.FullName -File | Where-Object Name -ne 'maschinenplanung_vor_update.sqlite3' | Copy-Item -Destination $b -Force
Remove-Item "$b\data\maschinenplanung.sqlite3-wal", "$b\data\maschinenplanung.sqlite3-shm" -Force -ErrorAction SilentlyContinue
Copy-Item "$($r.FullName)\maschinenplanung_vor_update.sqlite3" "$b\data\maschinenplanung.sqlite3" -Force
Start-ScheduledTask -TaskName 'Maschinenplanung Server'
```

Danach `/api/health` zeigt 12.10.1. Neu hinzugekommene Dateien von V12.17.0 (z. B. `Restore_Datenbank.ps1`, `vorlage_*.json`) bleiben harmlos liegen. Danach nicht wieder `UPDATE_LIVE.ps1` einer älteren Version starten (Downgrade-Schutz).

**Nur Daten zurückholen (V12.17.0 bleibt installiert):**

```powershell
& "$b\Restore_Datenbank.ps1"            # Auswahl der letzten 10 Sicherungen, Enter = neueste
& "$b\Restore_Datenbank.ps1" -MitConfig # zusätzlich config\ aus dem passenden firma_*.zip
```

## 6. Testliste bis Mittwoch (12 Punkte)

1. Login mit jeder Rolle; Firmenname, Logo und Farbe stimmen, Daten (Projekte, Maschinen, Personal) vollständig.
2. Planung: Projekt verschieben, Konflikt-Hinweis, Speichern, in 2. Browser sichtbar.
3. Formate: Format anlegen/ändern, Zuordnung im Projekt.
4. Personal: Mitarbeiter, Schichten, Abwesenheit eintragen.
5. Chat: Nachricht senden, kommt beim anderen Benutzer an.
6. Glocke: Meldung erscheint bei Änderung durch einen anderen Benutzer, Zähler geht nach dem Lesen weg.
7. Undo/Redo: Änderung rückgängig machen und wiederholen.
8. Hallenmodus: Hell/Dunkel/Halle umschalten, am Hallen-Bildschirm lesbar.
9. Firmenprofil: Name/Farbe ändern, speichern, Strg+F5, bleibt erhalten (danach zurückstellen).
10. Module ein/aus: ein Modul im Firmenprofil abschalten (Schreiben dort gesperrt, `MP-MOD-001`, Daten bleiben) und wieder einschalten.
11. Backup-Task: `Server_Status.ps1` zeigt "Maschinenplanung Backup" ohne Fehler; nach 12:15 oder 22:15 neue Datei in `backups\`.
12. Neustart des PCs: Server kommt von selbst hoch (`/api/health`), danach Login.

# Aktuelles Hotfix-Update V12.19.1

Die Anleitung und Abnahme für V12.19.1 stehen in [V12_19_1.md](V12_19_1.md). Für die bestehende Installation wird das neue Paket lokal entpackt und `UPDATE_LIVE.ps1` als Administrator gestartet. Der Installer übernimmt Backup, Vorabtest, Update und gegebenenfalls Rollback.

Die folgende Anleitung dokumentiert die frühere Umstellung von V12.10.1 auf V12.17.0, einschließlich der damals nötigen einmaligen Firmeneinrichtung.

# Historisches Update beim Arbeitgeber: V12.10.1 auf V12.17.0

Ausgangslage (am Server-PC geprüft): Ordner `C:\ProgramData\Maschinenplanung`, Live V12.10.1, Python `C:\Program Files\Python313\python.exe`, `data.ci` leer (kein Firmenname, kein Logo, keine Farbe), kein `config\firma.json`, Backup-Task läuft, manuelles Backup vom 05.10. erledigt.
Daraus folgt: Das Firmenprofil muss **vor** dem Update angelegt werden (sonst bricht der Vorabtest mit `MP-CFG-006` ab; Live bliebe dabei unverändert).

Voraussetzung: PR nach `main` gemergt und Release `v12.17.0` auf `main` erstellt. Sonst meldet der Updater "Tag nicht gefunden" bzw. "ausserhalb von main".

Alles in **einer** Windows PowerShell **als Administrator** (Rechtsklick, "Als Administrator ausführen"). Der Server lauscht nur auf der LAN-IP, nicht auf `localhost`.

## 1. Vorbereitung

Skripte sind per ExecutionPolicy gesperrt, auch als Admin. Deshalb in diesem Fenster zuerst (gilt nur für dieses Fenster):

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
$b = 'C:\ProgramData\Maschinenplanung'
$c = Get-Content "$b\LAN_CONFIG.json" -Raw | ConvertFrom-Json
Invoke-RestMethod "http://$($c.lan_ip):$($c.port)/api/health"      # version 12.10.1
& "$b\Backup_Datenbank.ps1"                                         # frisches Backup direkt vor dem Update
```

Meldet `Set-ExecutionPolicy` einen Fehler wegen einer Gruppenrichtlinie (`Get-ExecutionPolicy -List`, Zeile MachinePolicy/UserPolicy gesetzt), muss die IT die Policy für diesen PC lockern. Das Update selbst (Task, Admin-Fenster des Updaters) startet seine Prozesse bereits mit `-ExecutionPolicy Bypass`.

## 2. Neues Paket holen (noch nichts installieren)

Der alte `Update_von_GitHub.ps1` auf dem PC taugt nicht: kein `-Tag`, Standard-Branch `claude/new-session-mpx5ch`, keine Release-Prüfung. **Nicht verwenden.** Neuen Updater aus dem Release laden und das Paket nur herunterladen:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$repo = 'xiy12345678910-lab/Produktionsplanung'
$u = "$env:TEMP\Update_von_GitHub.ps1"
Invoke-WebRequest -UseBasicParsing -OutFile $u -Uri "https://raw.githubusercontent.com/$repo/v12.17.0/Update_von_GitHub.ps1"
Unblock-File $u
$stage = "$env:ProgramData\Maschinenplanung_Update\manuell"
& $u -Tag v12.17.0 -NurHerunterladen -Ziel $stage
$p = (Get-ChildItem $stage -Directory -Recurse -Filter 'Produktionsplanung-*' | Where-Object { Test-Path "$($_.FullName)\UPDATE_LIVE.ps1" } | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
$p
```

Erwartet: "Release v12.17.0", ein 40-stelliger Commit, `SHA256`, "Paket bereit". `$p` zeigt den neuen Paketordner.

## 3. Firmenprofil anlegen (vor dem Update)

Die Vorlage mit Name, Logo, Farbe, Projektkürzel `WT` kommt aus dem Repo (liegt bewusst nicht im Paket):

```powershell
$v = "$env:TEMP\legacy_employer_seed.json"
Invoke-WebRequest -UseBasicParsing -OutFile $v -Uri "https://raw.githubusercontent.com/$repo/v12.17.0/tools/legacy_employer_seed.json"
Set-Location $p
.\Firma_Einrichten.ps1 -Vorlage $v -Ziel $b
Get-ChildItem "$b\config"
```

Erwartet: `Fertig: C:\ProgramData\Maschinenplanung\config - jetzt UPDATE_LIVE.ps1 starten.` und die Dateien `firma.json`, `logo.jpg`. Das ist ungefährlich für den laufenden Server: V12.10.1 liest `config\` nicht. Das Ergebnis entspricht dem Stand von V12.10.1 (Name "WERBETECHNIK *ART OF DISPLAY* GMBH", Farbe #E2382A, Schrift Arial, Logo, Projektkürzel WT, die sieben festen Projektbereiche Vertrieb bis Arbeitsvorbereitung). Die Rechte des Ordners setzt `UPDATE_LIVE.ps1` im Schritt 7 neu (SYSTEM und Administratoren Vollzugriff, Benutzer lesen); der SYSTEM-Task kann `config\` also lesen und schreiben.

## 4. Update

```powershell
Set-Location $p
.\UPDATE_LIVE.ps1
```

Neun Schritte: Backup, Vorabtest mit Datenkopie (Testport), Stopp, Backup, Programmstand sichern (`update_backups\pre_V12.17.0_<Zeit>`), Dateien, Ordner sperren und Tasks, Health, HTTP-Sicherheitscheck. Ende: `UPDATE ERFOLGREICH - V12.17.0`. Bei einem Fehler nach dem Stopp stellt das Skript Code, Config, Datenbank und Task selbst wieder her (`Alter Stand wieder gestartet`). Vor dem Stopp wurde nichts verändert.

Alternative ohne Updater (z. B. Proxy blockiert die GitHub-API): ZIP `https://github.com/xiy12345678910-lab/Produktionsplanung/archive/refs/tags/v12.17.0.zip` im Browser laden, entpacken, `Get-ChildItem <Ordner> -Recurse -File | Unblock-File`, dann in Schritt 3 und 4 `$p` auf den entpackten Ordner setzen (der `UPDATE_LIVE.ps1` enthält).

## 5. Kontrolle

```powershell
Invoke-RestMethod "http://$($c.lan_ip):$($c.port)/api/health"      # version 12.17.0
& "$b\Server_Status.ps1"          # beide Tasks, Server V12.17.0, Backup nicht älter als 26 h
& "$b\CHECK_LAN_SICHERHEIT.ps1"   # alles PASS
```

Dann an jedem PC in jedem Browser **Strg+F5**, Login mit jeder Rolle (Admin, GF, Projektmanagement, Arbeitsvorbereitung, Vertrieb, Abteilungsleiter, Lesend), Daten vollständig, Firmenname, Logo und Farbe oben stimmen.

## 6. Rückweg

**Automatisch:** siehe Schritt 4 (nur bei Abbruch nach dem Stopp; nicht per Befehl auslösbar).

**Von Hand, wenn das Update durchlief, aber etwas nicht stimmt.** Der alte Stand liegt in `$b\update_backups\pre_V12.17.0_<Zeitstempel>` (Dateien, `config\`, `maschinenplanung_vor_update.sqlite3`). Eingaben seit dem Update gehen dabei verloren.

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
. "$b\MP_Common.ps1"
Stop-MPServer $b
$r = Get-ChildItem "$b\update_backups" -Directory -Filter 'pre_V12.17.0_*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-ChildItem $r.FullName -File | Where-Object Name -ne 'maschinenplanung_vor_update.sqlite3' | Copy-Item -Destination $b -Force
Remove-Item "$b\data\maschinenplanung.sqlite3-wal", "$b\data\maschinenplanung.sqlite3-shm" -Force -ErrorAction SilentlyContinue
Copy-Item "$($r.FullName)\maschinenplanung_vor_update.sqlite3" "$b\data\maschinenplanung.sqlite3" -Force
Start-ScheduledTask -TaskName 'Maschinenplanung Server'
```

Danach zeigt `/api/health` 12.10.1. Übrig gebliebene Dateien von V12.17.0 (z. B. `Restore_Datenbank.ps1`, `config\`) stören nicht. Danach nicht den `UPDATE_LIVE.ps1` einer älteren Version starten (Downgrade-Schutz).

**Nur Daten zurückholen (V12.17.0 bleibt):**

```powershell
& "$b\Restore_Datenbank.ps1"             # Auswahl der letzten 10 Sicherungen, Enter = neueste
& "$b\Restore_Datenbank.ps1" -MitConfig  # zusätzlich config\ aus dem passenden firma_*.zip
```

## 7. Testliste bis Mittwoch (12 Punkte)

1. Login mit jeder Rolle; Firmenname, Logo und Farbe stimmen, Projekte, Maschinen, Personal vollständig.
2. Planung: Projekt verschieben, Konflikt-Hinweis, Speichern, im 2. Browser sichtbar.
3. Formate: Format anlegen/ändern, Zuordnung im Projekt.
4. Personal: Mitarbeiter, Schichten, Abwesenheit eintragen.
5. Chat: Nachricht senden, kommt beim anderen Benutzer an.
6. Glocke: Meldung erscheint bei Änderung durch einen anderen Benutzer, Zähler geht nach dem Lesen weg.
7. Undo/Redo: Änderung rückgängig machen und wiederholen.
8. Hallenmodus: Hell/Dunkel/Halle umschalten, am Hallen-Bildschirm lesbar.
9. Firmenprofil: Name/Farbe ändern, speichern, Strg+F5, bleibt erhalten (danach zurückstellen).
10. Module: ein Modul im Firmenprofil abschalten (Schreiben dort gesperrt, `MP-MOD-001`, Daten bleiben) und wieder einschalten.
11. Backup-Task: `Server_Status.ps1` zeigt "Maschinenplanung Backup" ohne Fehler; nach 12:15 oder 22:15 neue Datei in `backups\`.
12. PC-Neustart: Server kommt von selbst hoch (`/api/health`), danach Login.

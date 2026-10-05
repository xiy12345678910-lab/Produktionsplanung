MASCHINENPLANUNG V12.14.0 - WINDOWS-SERVER (LAN ONLY)
=====================================================

1. VORAUSSETZUNGEN
   - Windows-PC als Host, eingeschaltet, Netzwerkprofil "Privat" oder "Domaene".
   - Python 3 FUER ALLE BENUTZER installiert (C:\Program Files\Python3xx).
     Der Server laeuft als SYSTEM; Python aus einem Benutzerprofil (AppData) wird
     vom Sicherheitscheck als FAIL gemeldet.
       winget install -e --id Python.Python.3.13 --scope machine
   - Empfohlen: feste IP bzw. DHCP-Reservierung fuer den Host.

2. BESTEHENDE INSTALLATION AKTUALISIEREN (Normalfall)
   1) Paket lokal entpacken (nicht OneDrive/Netzlaufwerk).
   2) PowerShell ALS ADMINISTRATOR oeffnen, in den entpackten Ordner wechseln:
        Set-ExecutionPolicy -Scope Process Bypass
        .\UPDATE_LIVE.ps1
   3) Der Updater:
        - erstellt ein Online-Backup, stoppt den Server, erstellt ein finales Backup,
        - sichert den bisherigen Programmstand nach update_backups\pre_V<version>_<zeit>\
          (inkl. Datenbank vor dem Update),
        - kopiert die neuen Dateien und entfernt veraltete (z. B. alte V11-Updater),
        - sperrt den Live-Ordner (nur SYSTEM/Administratoren duerfen schreiben),
        - richtet den Servertask und den Backup-Task ein, startet und prueft Version + HTTP.
      Bei einem Fehler werden alter Programmstand UND Datenbank automatisch zurueckgespielt.
      Ein aelteres Paket laesst sich nicht ueber ein neueres installieren (Downgrade-Schutz).
   4) Danach:  .\CHECK_LAN_SICHERHEIT.ps1  (aus C:\ProgramData\Maschinenplanung)
   5) Alle Browser einmal mit Strg+F5 neu laden.
   6) V12.7: Beim ersten Start werden Konfektion, Siebdruck und Tiefziehen auf Maschinen/Linien
      umgestellt (je Konfektion eine Linie, Siebdruck/Tiefziehen je eine Maschine, falls keine da).
      Offene Arbeitsgaenge landen im Wochenplan, erledigte in der Historie. Danach unter
      System -> Maschinen & Linien Namen, weitere Maschinen und die Besetzung der Linien pruefen.
   7) Ab V12.4.5/V12.6 wird beim ersten Start die Benutzertabelle um neue Rollen (Arbeitsvorbereitung,
      Vertrieb) erweitert. Dabei werden alle Sitzungen beendet: jeder meldet sich einmal neu an.

2b. UPDATE DIREKT VON GITHUB (Windows-Benutzer, z. B. boensch) - ab V12.10.2 nur aus Releases
   Normale PowerShell (kein Administrator):
        powershell -ExecutionPolicy Bypass -File C:\ProgramData\Maschinenplanung\Update_von_GitHub.ps1
   - Nimmt das neueste GitHub-Release; dessen Commit muss in "main" liegen. Bestimmte Version: -Tag v12.10.2
   - Installiert wird genau ein fester Commit (Hash wird angezeigt). Mit -Sha256 <hash> wird das ZIP
     zusaetzlich geprueft (Hash steht in den Release-Notizen bzw. wird beim Download angezeigt).
   - Download, Pruefung und Entpacken laufen erst im Administrator-Fenster (UAC) in
     C:\ProgramData\Maschinenplanung_Update\ - nur SYSTEM/Administratoren duerfen dort schreiben.
   - Branch-Stand nur zum Testen: -Branch <name> -UnsicherBranch
   - Nur herunterladen (ohne Installation): -NurHerunterladen
   ERSTES Update auf V12.10.2 (alter Updater kennt noch keine Releases):
        $B = 'claude/new-session-95ro2l'
        irm "https://raw.githubusercontent.com/xiy12345678910-lab/Produktionsplanung/$B/Update_von_GitHub.ps1" -OutFile "$env:USERPROFILE\Downloads\Update_von_GitHub.ps1"
        powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\Downloads\Update_von_GitHub.ps1" -Branch $B -UnsicherBranch
   RELEASE ANLEGEN (GitHub, einmal je Version): Stand nach main mergen -> Releases -> "Draft a new
   release" -> Tag vX.Y.Z auf main -> Veroeffentlichen.
   BRANCH-SCHUTZ (GitHub -> Settings -> Branches -> main): "Require a pull request before merging",
   "Do not allow bypassing", kein Force-Push; Tags v* unter Settings -> Rules schuetzen.

2a. FIRMENDATEN (config\firma.json)
   - Liegt in C:\ProgramData\Maschinenplanung\config\ (neben data\): firma.json, logo.png|jpg, lizenz.key.
   - Updates, Rollback, Umzug und Vorabtest behalten sie; UPDATE_LIVE.ps1 kopiert nur Programmdateien.
   - Backup_Datenbank.py legt zu jeder Sicherung firma_<zeit>.zip an.
     Restore: .\Restore_Datenbank.ps1 -MitConfig
   - Ungueltige Datei: Server startet nicht, Meldung MP-CFG-001/002 (siehe FEHLERCODES.txt).

3. ERSTINSTALLATION (neuer PC)
   PowerShell als Administrator:
        Set-ExecutionPolicy -Scope Process Bypass
        .\INSTALLIEREN_ALS_ADMIN.ps1
   Admin-Benutzername und Passwort festlegen. Installationsordner: C:\ProgramData\Maschinenplanung

4. GEPLANTE AUFGABEN
   - "Maschinenplanung Server": beim Windows-Start, Konto SYSTEM, automatischer Neustart bei Fehlern.
   - "Maschinenplanung Backup": taeglich 12:15 und 22:15 Online-Backup mit Integritaetspruefung,
     60 Sicherungen (ca. 30 Tage) in backups\.
   - Zweitkopie (dringend empfohlen): Datei BACKUP_ZIEL.txt im Live-Ordner anlegen und als erste
     Zeile das Ziel eintragen, z. B.  \\NAS\Sicherung\Maschinenplanung
     Der Computer (SYSTEM-Konto, im Netz als WTPC207$) braucht dort Schreibrechte.

5. VERWALTUNG (PowerShell im Live-Ordner C:\ProgramData\Maschinenplanung)
   .\Server_Status.ps1          Status, Version, letztes Backup (rot bei Fehler/>26 h), Firewall
   .\Start_Server.ps1           Server starten
   .\Stop_Server.ps1            Server stoppen (Autostart bleibt)
   .\Neustart_Server.ps1        Neustart mit Health-Check
   .\Backup_Datenbank.ps1       sofortiges Backup
   .\Restore_Datenbank.ps1      Sicherung zurueckspielen (siehe 6.)
   .\CHECK_LAN_SICHERHEIT.ps1   Sicherheits- und Betriebscheck
   .\Deinstallieren.ps1         Tasks + Firewall entfernen (Daten bleiben)

   WICHTIG: Die Datenbank laeuft im WAL-Modus. Nie nur data\maschinenplanung.sqlite3 kopieren -
   der juengste Stand kann in der -wal-Datei liegen. Immer Backup_Datenbank.ps1 verwenden.

6. WIEDERHERSTELLEN EINES BACKUPS (als Administrator)
   .\Restore_Datenbank.ps1                 Auswahl aus den letzten 10 Sicherungen (Enter = neueste)
   .\Restore_Datenbank.ps1 -Datei <pfad>   bestimmte Sicherung (z. B. von der Zweitkopie)
   Prueft die Sicherung, stoppt den Server, sichert den aktuellen Stand (*_vor_restore), spielt
   zurueck (inkl. -wal/-shm), startet und prueft den Server. Startet er nicht, wird der Stand vor
   dem Restore automatisch zurueckgespielt.
   update_backups\ behaelt die letzten 5 Update-Staende.

7. ROLLEN
   Admin                   alles, inkl. Benutzerverwaltung und Backup-Import
   GF                      Gesamtuebersicht; bestaetigt Personalbedarf, KW-Einsaetze, Betriebsferien
   Abteilungsleiter        plant den eigenen Bereich; verwaltet Stellvertretungen und Lesende
   Stellv. Abteilungsleiter plant den eigenen Bereich; verwaltet Lesende
   Arbeitsvorbereitung     plant Termine und verwaltet die Fertigung: FS anlegen/verknuepfen,
                           Planwochen, Fertigungsprozesse terminieren, eigene Fertigungsablaeufe.
                           Keine Freigabe, keine Fortschrittsmeldung, kein Personal/Einstellungen
   (V12.10.1) GF sieht Wochenplan und Auftragsliste nur lesend.
   (V12.7) GF-Ansicht: nur GF + Admin. Projekt-Ansicht: GF, PM, AV, Vertrieb, Admin.
   Vertrieb                legt Eingaenge an, pflegt Kundendaten bis zur Annahme, meldet
                           Angebot angenommen (AB + Liefertermin) oder verloren
   Projektmanagement       plant Termine und verwaltet Projekte bis zur Annahme: Prozesse je
                           Abteilung anlegen/terminieren, Angebot, eigene Prozessablaeufe.
                           Status von Abteilungsprozessen meldet die Abteilung.
   Lesend                  nur Ansicht
   Leitungs- und Arbeitsvorbereitungskonten legt nur der Admin an. Jeder Benutzer aendert sein Passwort selbst (Schluessel-Symbol oben).

8. SICHERHEIT
   - Server bindet nur an die LAN-IP; Firewall erlaubt nur das eigene Subnetz; Public-Profil blockiert.
   - Der Server prueft die Client-IP zusaetzlich selbst.
   - Passwoerter: PBKDF2-SHA256 (310.000 Iterationen) mit Salt; Sitzungen HttpOnly/SameSite=Strict.
   - Transport ist HTTP (unverschluesselt). Nur im vertrauenswuerdigen LAN betreiben,
     keine Portweiterleitung/UPnP. HTTPS ist fuer eine spaetere Version vorgesehen.
   - Live-Ordner ist gesperrt: Aenderungen an Programmdateien nur mit Administratorrechten.

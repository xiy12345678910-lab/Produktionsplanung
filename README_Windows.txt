MASCHINENPLANUNG V12.7.5 - WINDOWS-SERVER (LAN ONLY)
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
   .\Server_Status.ps1          Status, Version, letztes Backup, Firewall
   .\Start_Server.ps1           Server starten
   .\Stop_Server.ps1            Server stoppen (Autostart bleibt)
   .\Neustart_Server.ps1        Neustart mit Health-Check
   .\Backup_Datenbank.ps1       sofortiges Backup
   .\CHECK_LAN_SICHERHEIT.ps1   Sicherheits- und Betriebscheck
   .\Deinstallieren.ps1         Tasks + Firewall entfernen (Daten bleiben)

   WICHTIG: Die Datenbank laeuft im WAL-Modus. Nie nur data\maschinenplanung.sqlite3 kopieren -
   der juengste Stand kann in der -wal-Datei liegen. Immer Backup_Datenbank.ps1 verwenden.

6. WIEDERHERSTELLEN EINES BACKUPS
   1) .\Stop_Server.ps1
   2) data\maschinenplanung.sqlite3-wal und -shm loeschen (falls vorhanden)
   3) gewuenschte Sicherung aus backups\ nach data\maschinenplanung.sqlite3 kopieren
   4) .\Start_Server.ps1

7. ROLLEN
   Admin                   alles, inkl. Benutzerverwaltung und Backup-Import
   GF                      Gesamtuebersicht; bestaetigt Personalbedarf, KW-Einsaetze, Betriebsferien
   Abteilungsleiter        plant den eigenen Bereich; verwaltet Stellvertretungen und Lesende
   Stellv. Abteilungsleiter plant den eigenen Bereich; verwaltet Lesende
   Arbeitsvorbereitung     plant Termine und verwaltet die Fertigung: FS anlegen/verknuepfen,
                           Planwochen, Fertigungsprozesse terminieren, eigene Fertigungsablaeufe.
                           Keine Freigabe, keine Fortschrittsmeldung, kein Personal/Einstellungen
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

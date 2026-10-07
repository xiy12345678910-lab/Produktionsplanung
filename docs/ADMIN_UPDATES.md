# Allgemeine Softwareupdates im Adminbereich (I 7a)

Ab V12.19.0 prüft der Server beim Start und anschließend stündlich die zentrale Releasequelle. Ein Admin sieht unter System nur bei einem neueren freigegebenen Release einen Hinweis mit installierter und verfügbarer Version, Änderungen und **Update installieren**. Andere Rollen haben weder den Hinweis noch Zugriff auf die Update-API. Es gibt keinen manuellen Suchknopf.

Der Mechanismus gilt für beliebige künftige Softwareversionen. Windows Server 2019+ (Build 17763+) ist die unterstützte Installationsplattform. Windows PowerShell 5.1, die bestehende Installation mit SYSTEM-Servertask und deren geschützte Python-Installation werden weiterverwendet.

## Releasequelle und Freigabe

Die Quelle ist `config/firma.json` → `update.source` in der Form `konto/repository` oder `https://github.com/konto/repository`. Ohne diese Einstellung gilt `LAN_CONFIG.json` → `UpdateRepo`, anschließend das Repository `xiy12345678910-lab/Produktionsplanung`. Diese Einstellungen liegen serverseitig. Die Browser-API nimmt keine Quelle, Paketpfade oder Befehle entgegen. Für private Repositories kann die IT `MP_UPDATE_GITHUB_TOKEN` im Umfeld des SYSTEM-Servertasks setzen; es wird nicht an den Browser oder andere Downloadhosts weitergegeben.

Freigegeben bedeutet: veröffentlichtes GitHub-Release, weder Entwurf noch Vorabversion, Tag `vX.Y.Z`, Commit auf `main`, passende Paketversion und kompatibles Manifest. Der Release-Workflow darf nur auf `main` laufen und verlangt einen erfolgreichen CI-Lauf genau dieses Commits. Er erstellt zunächst einen **Entwurf**; ein Maintainer prüft Änderungen und veröffentlicht ihn. Erst danach bietet die Anwendung das Release an. Tag/Assets/Manifest müssen aus dem freigegebenen Commit stammen.

`tools/build_release.py --output <ordner>` baut die Dateien aus `$MP_AppFiles`, `produktionsplanung-vX.Y.Z.zip` und `update-manifest.json`. Das Manifest enthält Schema 1, Version, Mindestversion, Commit, SHA256 des gesamten Pakets und SHA256 jeder Datei. Die Anwendung prüft diese Angaben sowie die Client-/Serverversion. Sie weist zusätzliche oder doppelte Dateien, Verzeichniswechsel, Links und Pakete über 64 MiB zurück. Die Vertrauensgrundlage ist das freigegebene Repository über HTTPS; eine Signatur mit unbekanntem Prüfverfahren wird abgelehnt.

V12.18.0 hat noch keinen Admin-Updater. Der einmalige Einstieg auf V12.19.0 erfolgt wie bisher über das geprüfte Paket und `UPDATE_LIVE.ps1`. Danach können Folgeversionen mit diesem Mechanismus installiert werden. Die derzeitige Paket-Mindestversion ist V12.19.0; zukünftige Releases können eine höhere Mindestversion deklarieren.

## Installation und Wiederherstellung

1. Der Admin startet einen Auftrag. Ein dauerhaftes Dateilock und ein Windows-Mutex verhindern parallele Installationen, auch durch Doppelklick oder einen manuellen Aufruf.
2. Ein vom Browser und Servertask unabhängiger Worker prüft das Release erneut, lädt das Paket und prüft Integrität, Version, Kompatibilität und freien Speicher.
3. Der bestehende Installer erstellt ein Onlinebackup und startet die neue Anwendung mit einer Datenkopie zum verpflichtenden Vorabtest.
4. Eine Wartungssperre blockiert Schreibzugriffe, der Servertask stoppt, und ein finales Datenbankbackup mit Firmenprofilprüfung sowie eine Kopie des bisherigen Codes und der Konfiguration entstehen.
5. Die Anwendung wird aktualisiert. Beim Start laufen die additiven Datenmigrationen; Task-Neustart, Versions-/Healthcheck und die Prüfung privater HTTP-Pfade folgen.
6. Erst nach erfolgreicher Prüfung wird die Wartungssperre entfernt. Die laufende Softwareversion ist der aktive Stand, der Auftrag wird abgeschlossen und der Updatehinweis verschwindet. Ein Browser mit alter Clientversion lädt die neue Oberfläche.
7. Bei einem Fehler vor dem Live-Eingriff bleibt die Anwendung bestehen. Nach einem Live-Eingriff werden Code, Datenbank und Konfiguration aus dem gesicherten Stand zurückgespielt und die alte Version erneut per Healthcheck geprüft. Scheitert auch diese Prüfung, bleibt die Wartungssperre bestehen und die Meldung fordert eine Wiederherstellung durch die IT.

Die Oberfläche zeigt Prüfung, Download, Vorabtest, Backup, Installation, Migration, Neustart, Healthcheck sowie gegebenenfalls Rollback und einen verständlichen Fehler. Die Migration startet im Servertask; ihre Freigabe erfolgt mit dem Healthcheck. Eine Unterbrechung der Browserverbindung beendet den Worker nicht. Der serverseitige Status erscheint nach Neuladen wieder.

Status und Lock: `updates/status.json`, `updates/update.lock`; Wartungssperre: `updates/installing`; Workerprotokoll: `updates/install-<jobId>.log`; gesicherter Code und Datenbank: `update_backups/pre_V…`; reguläre Daten-/Firmenprofilbackups: `backups/`. Diese Verzeichnisse sind privat. Auftrag, Phasen und Ergebnis werden zusätzlich in `server_audit` protokolliert.

Bei einem hart beendeten Worker oder Stromausfall kann ein Lock zurückbleiben. Die IT prüft zuerst laufende Worker und Tasks, Installationsprotokoll, aktiven Code, Datenbank und Healthcheck. Erst nach bestätigter Wiederherstellung darf sie ein verwaistes Lock bzw. die Wartungssperre entfernen. Ein automatisches Löschen allein aufgrund des Alters würde eine laufende Installation gefährden.

## Nachweise und offene Hostprüfung

`tests/test_updates.py` prüft zwei Folgeversionen, wiederholten Start, dauerhaften Status, Releasefreigabe, Kompatibilität und manipulierte Pakete. `tests/e2e_blocks.mjs` prüft Admin-Anzeige, Normalzustand, Fortschritt, Browser-Neuladen und das Verschwinden des Hinweises. `tests/ci_windows_deploy.ps1` installiert unter Windows echte Tasks, führt zwei Folgeupdates aus und erzwingt einen Migrationsfehler mit anschließendem Daten-/Konfigurationsvergleich und Rollback-Healthcheck.

Die Windows-CI läuft auf `windows-latest`; sie ist kein Nachweis einer Installation auf einem tatsächlichen Windows Server 2019. Vor dem produktiven Einsatz auf diesem Host sind dieselben Deployprüfungen mit dem dortigen Python, den Task-/ACL-Einstellungen und der Netzwerkverbindung zur Releasequelle auszuführen. Ein solcher Host steht in dieser Arbeitsumgebung nicht zur Verfügung; der Nachweis wird daher nicht als bestanden angegeben.

Dieser Änderungsumfang umfasst ausschließlich I 7a. Weitere Anforderungen aus Block I werden dadurch nicht als abgeschlossen erklärt.

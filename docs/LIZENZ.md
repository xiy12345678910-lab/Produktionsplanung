# Lizenzschlüssel (#52) – Anleitung für den Lizenzgeber

Stand V12.27.0. Zielgruppe: Jonas (Entwickler/Lizenzgeber). Kunden lesen README_Windows.txt Abschnitt 2d.

## Prinzip

- Jede Installation braucht eine Lizenzdatei `config\lizenz.key`. Sie enthält Lizenznehmer, Mandanten-ID
  (`tenantId` aus `config\firma.json` des Kunden), Ausstellungs- und optional Ablaufdatum – und eine Signatur.
- Signiert wird mit deinem **privaten Schlüssel** (nur bei dir). Das Programm enthält nur den **öffentlichen
  Schlüssel** (`PUBLIC_KEY` in `mp_license.py`) und kann damit prüfen, aber keine Lizenzen erzeugen.
- Lizenzen sind unbefristet (`expires: null`) oder befristet. Keine Benutzer- oder Modulgrenzen.
- Solange `PUBLIC_KEY` leer ist (so wird V12.27.0 ausgeliefert), ist die Prüfung **nicht aktiv**: keine
  Einschränkung, keine Kulanzzählung. Erst der PR mit deinem öffentlichen Schlüssel schaltet sie ein.

## 1. Schlüsselpaar einmalig erzeugen (auf deinem eigenen PC)

Nie im Repository-Ordner, nie in einer Cloud (OneDrive, Dropbox, GitHub, …), nie per Mail.
Das Werkzeug verweigert Ordner innerhalb eines Git-Repositorys.

```
python tools\lizenz_werkzeug.py schluessel-erzeugen --ordner D:\Lizenz-Tresor
```

- Dauert einige Sekunden bis etwa eine Minute (RSA-3072 in reinem Python).
- Ergebnis: `D:\Lizenz-Tresor\lizenz_privat.pem` (privat!) und eine Zeile `PUBLIC_KEY = {"n": "…", "e": 65537}`.
- Lege eine zweite Kopie des privaten Schlüssels offline ab (z. B. USB-Stick im Tresor). Geht er verloren,
  kannst du keine neuen Lizenzen mehr ausstellen und musst einen neuen Schlüssel ausliefern (alle Kunden
  brauchen dann neue Lizenzdateien). Wird er bekannt, kann jeder Lizenzen erzeugen.
- `.gitignore` schließt `*.pem`, `*.key` und `lizenz_privat*` aus – trotzdem den Schlüssel gar nicht erst ins
  Repository legen.

## 2. Öffentlichen Schlüssel ins Programm bringen (PR)

1. In `mp_license.py` die Zeile `PUBLIC_KEY = {"n": "", "e": 65537}` durch die ausgegebene Zeile ersetzen.
2. Als PR einreichen (nur diese Zeile + Release Notes). `tests/test_license.py` prüft, dass der eingetragene
   Schlüssel mindestens 3072 Bit hat und kein privater Schlüssel in der Datei steht.
3. **Vor** dem Release mit diesem PR: Lizenzdateien für alle bestehenden Kunden erzeugen (Abschnitt 3) und
   verteilen. Ab dem Update beginnen sonst 30 Tage Kulanz.

## 3. Lizenzdatei für einen Kunden erstellen

Mandanten-ID des Kunden: `tenantId` in `C:\ProgramData\Maschinenplanung\config\firma.json` – oder im
Programm unter System → Lizenz („Mandant dieser Installation“) bzw. im Diagnosepaket (`config.tenantId`).

```
REM unbefristet (z. B. die bestehende Installation)
python tools\lizenz_werkzeug.py erstellen --privat D:\Lizenz-Tresor\lizenz_privat.pem ^
       --firma "Muster GmbH" --mandant muster-gmbh --datei muster-gmbh.key

REM befristet bis einschließlich 31.12.2027
python tools\lizenz_werkzeug.py erstellen --privat D:\Lizenz-Tresor\lizenz_privat.pem ^
       --firma "Beispiel AG" --mandant beispiel-ag --bis 2027-12-31 --datei beispiel-ag.key

REM prüfen (mit dem im Programm eingetragenen Schlüssel oder --oeffentlich <pem>)
python tools\lizenz_werkzeug.py pruefen --datei muster-gmbh.key --mandant muster-gmbh
```

**Bestehende Installation:** unbefristete Lizenz mit der `tenantId` aus ihrer `firma.json` (meist `firma`,
falls nie geändert) erstellen und installieren – idealerweise vor dem Update, das den öffentlichen Schlüssel
mitbringt. Bis dahin ist die Prüfung inaktiv; danach gilt bis zur Installation die Kulanz.

Die Lizenzdatei ist nicht geheim (sie enthält nur geprüfte Angaben und die Signatur), aber an einen Mandanten gebunden.

## 4. Lizenz beim Kunden installieren

- Oberfläche: Admin → System → Lizenz → „Lizenzdatei hochladen (.key)“. Der Server prüft Signatur,
  Mandant und Ablauf **vor** dem Speichern; eine bisherige Datei bleibt als `lizenz.key.alt`.
- Oder Datei als `config\lizenz.key` ablegen und `Neustart_Server.ps1` (Status wird ohnehin laufend neu gelesen).
- „Lizenz entfernen“ legt die Datei als `lizenz.key.entfernt` beiseite (wird nicht gelöscht).

## 5. Was Kunden sehen

| Lage | Admin | Andere Rollen |
| --- | --- | --- |
| Prüfung nicht aktiv | System → Lizenz: „nicht aktiv“ | nichts |
| Lizenz gültig | Status, Lizenznehmer, Ablauf („unbefristet“) | nichts |
| Befristet, läuft in ≤ 30 Tagen ab | gelber Hinweis mit Ablaufdatum | nichts |
| Fehlt / ungültig / falsche Firma / abgelaufen, Kulanz läuft | gelber Hinweis „noch N Tage Kulanz“ | nichts, alles normal |
| Kulanz (30 Tage) vorbei | roter Banner „Nur Lesen“, Upload möglich | roter Banner „Nur Lesen“ |

**Nur Lesen** heißt: alles bleibt sichtbar, nichts änderbar. Der Server lehnt jede Änderung mit MP-LIC-001 ab
(Speichern, Produktions-/Bedarfsaktionen, Benutzer, Rollen, Firmenprofil, Chat, Benachrichtigungen …).
Weiter möglich: Lesen, An-/Abmelden, eigenes Passwort, Diagnosepaket, Backup (Backup_Datenbank.py),
Lizenz hochladen/entfernen und Software-Update. Es werden nie Daten gelöscht; mit einer gültigen Lizenz ist
sofort wieder alles bearbeitbar.

## 6. Kulanz – Details

- Beginnt im ersten Moment, in dem die aktive Prüfung keine gültige Lizenz findet (auch beim Serverstart),
  und steht in der Datenbank (Tabelle `app_meta`, nicht vom Client schreibbar).
- Eine gültige Lizenz beendet die Kulanz. Läuft eine befristete Lizenz später ab, beginnen erneut 30 Tage.
- Uhr zurückstellen hilft nicht: maßgeblich ist max(jetzt, zuletzt gesehene Zeit).
- Achtung: Stand die Serveruhr versehentlich weit in der Zukunft, bleibt diese Zeit als „zuletzt gesehen“
  gespeichert. Dann hilft eine gültige (unbefristete oder passend befristete) Lizenz.

## 7. Grenzen

Das Programm wird als Python-Quelltext ausgeliefert. Wer die Dateien gezielt verändert, kann die Prüfung
umgehen; der Schlüssel schützt gegen versehentliche oder beiläufige Nutzung ohne Lizenz, nicht gegen
gezielte Manipulation. Rechtlich regelt das der Lizenzvertrag (siehe `LICENSE`).

# Anleitungen erzeugen

Die PDF-Anleitungen in `docs/anleitungen/` entstehen automatisch aus der echten Oberfläche:

1. `demo_server.py` startet den Server mit leerer Datenbank in einem Temp-Ordner (nie Live-Daten).
2. `seed_demo.py` legt erfundene Beispieldaten an (Bereiche, Personal, Projekte, Aufträge, Benutzer).
3. `capture.mjs` meldet sich mit jeder Rolle an, spielt deren Abläufe wirklich durch
   (Freigeben, Produktion starten/pausieren/fertig, Auftrag und Projekt anlegen …) und macht Bildschirmfotos.
   Fehlermeldungen dabei werden ausgegeben – ein Ablauf, der hier scheitert, ist ein Fehler im Programm.
4. `build.py` setzt Text und Bilder zu HTML zusammen (Texte stehen dort und sind dort zu pflegen).
5. `render.mjs` erzeugt daraus die PDFs.

Alles in einem Schritt (Linux/macOS, Git-Bash):

    bash tools/anleitungen/erstellen.sh

Nach Änderungen an der Oberfläche neu erzeugen, damit Bilder und Texte stimmen.
Die Browser-Zeitzone ist Europe/Berlin wie im Betrieb (Schichtzeiten hängen davon ab).

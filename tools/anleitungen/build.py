"""Erzeugt die Anleitungen (HTML) aus Text + Bildschirmfotos. PDF daraus: render.mjs.

Aufruf: python build.py <bildordner> <ausgabeordner>
Die Bilder erzeugt capture.mjs (Dateinamen wie cnc_01_wochenplan.jpg).
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

VERSION = "12.7.5"
STAND = "30.09.2026"

IMG = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path("img").resolve()
OUT = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else Path("out").resolve()


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# ----------------------------------------------------------------------------- Bausteine
# Ein Abschnitt ist eine Liste von Blöcken: ("p", text) | ("steps", [..]) | ("list", [..])
# | ("img", datei, bildunterschrift) | ("tip", text) | ("warn", text) | ("table", kopf, zeilen)
# Im Text: **fett**; Knöpfe als [[Knopf]].

def inline(t: str) -> str:
    out = esc(t)
    import re
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"\[\[(.+?)\]\]", r'<span class="key">\1</span>', out)
    return out


def block(b) -> str:
    kind = b[0]
    if kind == "h3":
        return f"<h3>{inline(b[1])}</h3>"
    if kind == "p":
        return f"<p>{inline(b[1])}</p>"
    if kind == "steps":
        return "<ol class=\"steps\">" + "".join(f"<li>{inline(x)}</li>" for x in b[1]) + "</ol>"
    if kind == "list":
        return "<ul>" + "".join(f"<li>{inline(x)}</li>" for x in b[1]) + "</ul>"
    if kind == "tip":
        return f"<div class=\"box tip\"><b>Tipp</b>{inline(b[1])}</div>"
    if kind == "warn":
        return f"<div class=\"box warn\"><b>Wichtig</b>{inline(b[1])}</div>"
    if kind == "table":
        head = "".join(f"<th>{inline(h)}</th>" for h in b[1])
        rows = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in b[2])
        return f"<table class=\"t\"><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
    if kind == "img":
        f = IMG / f"{b[1]}.jpg"
        if not f.exists():
            return ""
        cls = " small" if len(b) > 3 and b[3] == "small" else ""
        return f"<figure class=\"fig{cls}\"><img src=\"{f.as_uri()}\"><figcaption>{inline(b[2])}</figcaption></figure>"
    raise ValueError(kind)


CSS = """
@page{size:A4;margin:16mm 15mm 18mm}
*{box-sizing:border-box}
body{font-family:'Segoe UI',Arial,'DejaVu Sans',sans-serif;color:#1d2939;font-size:10.5pt;line-height:1.45;margin:0}
.cover{height:257mm;display:flex;flex-direction:column;justify-content:space-between;page-break-after:always}
.cover .band{background:#10213d;color:#fff;border-radius:14px;padding:26mm 14mm 16mm}
.cover .kicker{letter-spacing:.14em;text-transform:uppercase;font-size:9pt;opacity:.8}
.cover h1{font-size:30pt;margin:6px 0 4px;line-height:1.1}
.cover .sub{font-size:13pt;opacity:.9}
.cover .meta{font-size:9.5pt;color:#475467;border-top:1px solid #d0d5dd;padding-top:8px}
.cover .who{margin-top:14mm;font-size:11pt}
.cover .who b{display:block;font-size:9pt;text-transform:uppercase;letter-spacing:.08em;color:#667085;margin-bottom:4px}
.toc{page-break-after:always}
.toc h2{margin-top:0}
.toc ol{padding-left:28px}.toc li{margin:4px 0}
h2{font-size:17pt;color:#10213d;border-bottom:3px solid #1f5eff;padding-bottom:4px;margin:0 0 10px;page-break-after:avoid}
h3{font-size:12.5pt;color:#10213d;margin:14px 0 6px;page-break-after:avoid}
section.chap{page-break-before:always}
p{margin:5px 0 8px}
ol.steps{padding-left:0;list-style:none;counter-reset:s;margin:6px 0 10px}
ol.steps li{counter-increment:s;position:relative;padding:3px 0 3px 30px;margin:2px 0}
ol.steps li:before{content:counter(s);position:absolute;left:0;top:2px;width:21px;height:21px;border-radius:50%;background:#1f5eff;color:#fff;font-weight:700;font-size:9pt;display:flex;align-items:center;justify-content:center}
ul{margin:4px 0 10px;padding-left:18px}ul li{margin:2px 0}
.key{display:inline-block;border:1px solid #98a2b3;border-bottom-width:2px;border-radius:5px;padding:0 5px;font-size:9.5pt;background:#f9fafb;white-space:nowrap}
.box{border-radius:8px;padding:8px 10px 8px 12px;margin:8px 0 10px;page-break-inside:avoid;border-left:4px solid}
.box b{display:block;font-size:8.5pt;text-transform:uppercase;letter-spacing:.06em;margin-bottom:2px}
.tip{background:#eef4ff;border-color:#1f5eff}.tip b{color:#1f5eff}
.warn{background:#fef3f2;border-color:#d92d20}.warn b{color:#b42318}
figure.fig{margin:8px 0 12px;page-break-inside:avoid;text-align:center}
figure.fig img{max-width:100%;max-height:118mm;border:1px solid #d0d5dd;border-radius:6px}
figure.fig.small img{max-height:80mm}
figcaption{font-size:8.5pt;color:#667085;margin-top:3px}
table.t{border-collapse:collapse;width:100%;margin:6px 0 12px;font-size:9.5pt;page-break-inside:auto}
table.t th{background:#10213d;color:#fff;text-align:left;padding:5px 7px;font-weight:600}
table.t td{border-bottom:1px solid #e4e7ec;padding:5px 7px;vertical-align:top}
table.t tr{page-break-inside:avoid}
"""


def document(title: str, subtitle: str, audience: str, chapters: list[tuple[str, list]]) -> str:
    toc = "".join(f"<li>{esc(t)}</li>" for t, _ in chapters)
    body = "".join(
        f"<section class=\"chap\"><h2>{i}. {esc(t)}</h2>" + "".join(block(b) for b in blocks) + "</section>"
        for i, (t, blocks) in enumerate(chapters, 1)
    )
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><title>{esc(title)}</title><style>{CSS}</style></head><body>
<div class="cover"><div><div class="band"><div class="kicker">Produktionsplanung · Anleitung</div><h1>{esc(title)}</h1><div class="sub">{esc(subtitle)}</div></div>
<div class="who"><b>Für wen</b>{inline(audience)}</div></div>
<div class="meta">Programmversion V{VERSION} · Stand {STAND} · Die Bildschirmfotos zeigen erfundene Beispieldaten.</div></div>
<div class="toc"><h2>Inhalt</h2><ol>{toc}</ol></div>
{body}</body></html>"""


# ----------------------------------------------------------------------------- gemeinsame Kapitel
GRUNDLAGEN = ("Grundlagen: Anmelden, Speichern, Meldungen", [
    ("h", ""),
    ("p", "Die Produktionsplanung läuft im Browser (Edge, Chrome oder Firefox). Auf dem PC muss nichts installiert werden."),
    ("steps", [
        "Browser öffnen und die Adresse eingeben, die der Admin nennt, z. B. **http://192.168.10.89:8765**.",
        "Benutzername und Passwort eingeben, [[Anmelden]].",
        "Oben rechts stehen Ihr Name und Ihre Rolle. Links sehen Sie nur die Bereiche, die Ihre Rolle nutzen darf.",
    ]),
    ("list", [
        "**Passwort ändern:** oben rechts [[🔑]], aktuelles und zweimal das neue Passwort (mind. 8 Zeichen). Andere angemeldete Geräte dieses Kontos werden dabei abgemeldet.",
        "**Abmelden:** oben rechts [[↪]]. An gemeinsam genutzten PCs immer abmelden – dabei werden die im Browser zwischengespeicherten Daten gelöscht.",
        "**Speichern:** Jede Änderung wird sofort auf dem Server gespeichert. Unten links steht dann „Server gespeichert“.",
        "**Mehrere Benutzer gleichzeitig:** Änderungen anderer erscheinen nach wenigen Sekunden automatisch. Haben zwei Personen gleichzeitig gespeichert, gleicht das Programm ab und meldet sich, falls das nicht möglich ist.",
    ]),
    ("warn", "Fehlermeldungen bestehen aus Klartext und einem Code, z. B. **MP-PLAN-031**. Bei Rückfragen immer Code, Auftrag (FS/AB) und Uhrzeit nennen. Meldung „Client-Version veraltet“ (MP-SYNC-002): die Seite mit [[Strg]]+[[F5]] neu laden."),
    ("tip", "Nach 8 falschen Passwörtern ist die Anmeldung von diesem PC für 10 Minuten gesperrt."),
])
GRUNDLAGEN = (GRUNDLAGEN[0], [b for b in GRUNDLAGEN[1] if b[0] != "h"])

BEGRIFFE = ("Begriffe", [
    ("table", ["Begriff", "Bedeutung"], [
        ["FS", "Fertigungsauftrag – ein Auftrag im Wochenplan einer Maschine oder Linie."],
        ["AB", "Auftragsbestätigung des Kunden. Ab der Übergabe an die Produktion Pflicht am Projekt."],
        ["WT", "Projekt- bzw. Bestellnummer des Kunden."],
        ["AV-Termin", "„Fertig bis“-Termin, den die Arbeitsvorbereitung vorgibt. Früher fertig ist immer erlaubt, später nicht."],
        ["Sollstunden", "Geplante Arbeitszeit. Bei Linien Personenstunden: Durchlaufzeit = Sollstunden ÷ Besetzung."],
        ["Freigabe", "Friert den Plan eines Auftrags ein (Maschine, Zeiten, Umrüstzeit, Besetzung). Erst danach kann die Produktion starten."],
        ["Historie", "Fertig gemeldete oder abgebrochene Aufträge mit Ist-Zeiten und Mengen. Wird nie verändert."],
        ["Personal-Gate", "Wenn EIN: Freigabe und Start nur mit ausreichend eingeplantem Personal. Wenn AUS: nur Anzeige."],
    ]),
])

HILFE = ("Häufige Meldungen und Hilfe", [
    ("table", ["Meldung / Code", "Was tun?"], [
        ["MP-PLAN-062 · Termin liegt nach dem AV-Termin", "Früher planen oder die Arbeitsvorbereitung bitten, den AV-Termin zu verschieben."],
        ["MP-PLAN-056/057/058 · Freigabe außerhalb Schicht, Sperrzeit oder Kollision", "Im Wochenplan einen freien Zeitraum in der Schicht wählen ([[⏱]] Termin) und erneut freigeben."],
        ["MP-PLAN-032 · Freigegebener Auftrag ist eingefroren", "Zuerst die Freigabe zurücknehmen ([[↩]]), dann ändern."],
        ["MP-PROD-002 · Nur während der Arbeitszeit", "Start/Fortsetzen geht nur innerhalb der Schicht der Maschine."],
        ["MP-PERS-025 · Keine Freigabe für die Maschine", "Unter Personal → Mitarbeiter die Maschinen-Freigabe setzen."],
        ["MP-SYNC-001 · Revision veraltet", "Jemand hat gleichzeitig gespeichert. Das Programm lädt neu; Änderung ggf. wiederholen."],
        ["MP-AUTH-018 · Ansicht nicht freigegeben", "Diese Ansicht gehört nicht zu Ihrer Rolle."],
        ["Verbindung weg / „nicht gespeichert“", "Netzwerk prüfen, Seite neu laden. Bleibt es, den Admin informieren."],
    ]),
    ("p", "Die vollständige Liste aller Codes steht in **FEHLERCODES.txt** im Programmordner (beim Admin)."),
])


# ----------------------------------------------------------------------------- Produktionsbereiche
DEPTS = {
    "cnc": {
        "title": "CNC", "file": "Anleitung_CNC", "res": "Maschinen",
        "special": [
            ("h3", "Besonderheiten CNC"),
            ("list", [
                "Jede CNC-Maschine ist eine Zeile im Wochenplan. Standardbetrieb 0-, 1- oder 2-schichtig je Maschine.",
                "**CNC-Bediener je Schicht** (Einstellung des Admins) begrenzt, wie viele CNC-Maschinen gleichzeitig laufen dürfen. Wird die Grenze überschritten, lehnt die Freigabe mit MP-PLAN-059 ab.",
                "Die Umrüstzeit der Maschine wird bei der Freigabe eingefroren und in die Dauer eingerechnet.",
            ]),
        ],
    },
    "konf1": {
        "title": "Konfektion", "file": "Anleitung_Konfektion", "res": "Linien",
        "special": [
            ("h3", "Besonderheiten Konfektion 1, 2 und 3"),
            ("list", [
                "Jede Konfektion plant auf einer **Linie**. Die Sollstunden sind **Personenstunden**.",
                "Durchlaufzeit = Sollstunden ÷ Besetzung. Beispiel: 40 h bei 4 Personen = 10 h im Wochenplan.",
                "Die Besetzung pflegen Sie unter System → Maschinen & Linien (Spalte „Besetzung“). Sie wird bei der Freigabe eingefroren.",
                "Mehr Personal für eine Woche? Leiharbeiter anfragen (Kapitel Personal) oder die GF setzt Mitarbeiter anderer Bereiche per KW-Einsatz ein.",
            ]),
        ],
    },
    "screenprint": {
        "title": "Siebdruck", "file": "Anleitung_Siebdruck", "res": "Maschinen",
        "special": [
            ("h3", "Besonderheiten Siebdruck"),
            ("list", [
                "Siebdruck plant auf Maschinen wie CNC. Weitere Maschinen legen Sie unter System → Maschinen & Linien an.",
                "Trocknungs- oder Wartezeiten planen Sie als Maschinenstillstand (Sperrzeit) oder rechnen sie in die Sollstunden ein.",
            ]),
        ],
    },
    "thermoforming": {
        "title": "Tiefziehen", "file": "Anleitung_Tiefziehen", "res": "Maschinen",
        "special": [
            ("h3", "Besonderheiten Tiefziehen"),
            ("list", [
                "Tiefziehen plant auf Maschinen. Im Auftragsdialog gibt es die Takt-Rechnung: **Menge × Takt (Sekunden) ÷ Teile je Takt + Rüsten** → Knopf [[→ Sollstunden]] trägt das Ergebnis ein.",
                "Die Umrüstzeit der Maschine wird bei der Freigabe eingefroren.",
            ]),
        ],
    },
}


def dept_guide(dep: str, d: dict) -> tuple[str, str]:
    k = dep
    chapters = [
        GRUNDLAGEN,
        (f"Ihr Bereich im Überblick", [
            ("p", f"Als **Abteilungsleitung** oder **Stellvertretung {d['title']}** planen Sie den eigenen Bereich fein, geben Aufträge frei, melden die Produktion (Start, Pause, Fertig) und planen Ihr Personal. Die Aufträge (FS) mit AV-Termin legt die Arbeitsvorbereitung an."),
            ("table", ["Aufgabe", "Wo?", "Wer?"], [
                ["Aufträge fein planen, Reihenfolge festlegen", "Planung → Wochenplan / Aufträge", "Leitung, Stellvertretung"],
                ["Freigeben / Freigabe zurücknehmen", "Aufträge ([[▶]] / [[↩]])", "Leitung, Stellvertretung"],
                ["Start, Pause, Fertig, Abbruch", "oben [[Produktion]]", "Leitung, Stellvertretung"],
                ["Personal einteilen, Urlaub/Krankheit", "Personal", "Leitung, Stellvertretung"],
                ["Leiharbeiter anfragen", "Personal → Mitarbeiter", "Leitung, Stellvertretung → GF genehmigt"],
                ["Maschinen/Linien, Schichtkalender, Stillstand", "System", "Leitung, Stellvertretung"],
                ["Benutzer für den Bereich", "System → Benutzer", "Leitung: Stellvertretung + Lesende · Stellvertretung: Lesende"],
                ["AV-Termin, neue FS anlegen", "–", "Arbeitsvorbereitung"],
                ["Feiertage / Betriebsferien", "–", "GF, Admin"],
            ]),
            ("tip", "Ein typischer Tag: Wochenplan prüfen (rote Karten zuerst) → Personal der Woche kontrollieren → nächste Aufträge freigeben → in der Produktion starten und am Ende fertig melden."),
        ] + d["special"]),
        ("Wochenplan", [
            ("img", f"{k}_01_wochenplan", f"Wochenplan {d['title']} – Zeilen = {d['res']}, Spalten = Tage der KW"),
            ("list", [
                "Mit [[‹]] [[›]] oder dem Datumsfeld die Woche wechseln, [[Heute]] springt zur aktuellen Woche. [[🖨 PDF]] druckt den Wochen-Auftragsplan.",
                "Jede Karte ist ein Auftrag mit FS, AB/WT, Zeitraum, Sollstunden und AV-Termin („AV bis …“). Läuft ein Auftrag über mehrere Tage, ist nur die Karte am ersten Tag verschiebbar; Folgetage sind gestrichelt (↳).",
                "Farben: blau = geplant, dunkelblau = freigegeben, gelb = läuft, violett = pausiert, grün = fertig. **Rot umrandet = AV-Termin wird überschritten**; oben erscheint dann eine Liste zum Anklicken.",
                "Kacheln oben: aktive Aufträge, offene Sollstunden, in Produktion, Termin gefährdet, Auslastung der KW.",
            ]),
            ("h3", "Termin ändern (Feinplanung)"),
            ("steps", [
                "Karte auf einen anderen Tag bzw. eine andere Maschine ziehen – oder auf der Karte [[Termin]] klicken.",
                "Planart wählen: **Automatik** (frühestmöglich), **Startwunsch / Start fix** oder **Fertig bis Wunsch / fix** (rückwärts).",
                "[[Vorschau]] zeigt ALT und NEU und die Auswirkungen auf andere Aufträge. Erst [[Änderung übernehmen]] speichert.",
            ]),
            ("warn", "Früher als der AV-Termin planen ist immer erlaubt, später nicht (MP-PLAN-062). Den AV-Termin selbst ändert nur die Arbeitsvorbereitung."),
            ("h3", "Projekt-Aufgaben für Ihren Bereich"),
            ("img", f"{k}_02_projektaufgaben", "Unter dem Wochenplan: Aufgaben aus Projekten für Ihren Bereich", "small"),
            ("list", [
                "Hier stehen Aufgaben, die das Projektmanagement oder die AV Ihrem Bereich zugewiesen hat, mit Zeitraum.",
                "Status selbst melden: **Offen → In Arbeit → Wartet → Erledigt** (oder Entfällt).",
                "[[Als Auftrag einplanen]] legt dazu eine FS im Wochenplan an; die Aufgabe bleibt mit der FS verknüpft.",
            ]),
        ]),
        ("Auftragsliste und Priorität", [
            ("img", f"{k}_03_auftraege", "Planung → Aufträge: dieselben Aufträge als Liste"),
            ("list", [
                "Die Reihenfolge ist die **Priorität**: Zeile am Griff [[⋮⋮]] ziehen oder mit [[↑]] / [[↓]] verschieben.",
                "Direkt in der Zeile änderbar: Maschine, Alternative Maschine, Sollstunden, Menge. Suche und Filter oben.",
                "Spalte Aktion: [[⏱]] Termin planen · [[▶]] Freigeben · [[↩]] Freigabe zurücknehmen · [[×]] Löschen (nur geplante Aufträge).",
                "Mit [[+ Neuer Auftrag]] können Sie selbst eine FS anlegen (z. B. Muster). In der Regel legt die AV die Aufträge an.",
            ]),
        ]),
        ("Freigeben", [
            ("p", "Die Freigabe friert den Plan des Auftrags ein. Danach kann die Produktion starten."),
            ("steps", [
                "Planung → Aufträge, beim gewünschten Auftrag [[▶]] (Freigeben).",
                "Der Server prüft: liegt der Plan in der Schicht, außerhalb von Pausen, Sperrzeiten und anderen festen Aufträgen, und (bei Personal-Gate EIN) ist genug Personal eingeteilt?",
                "Status wird „Freigegeben“, die Zeile ist gesperrt (🔒).",
            ]),
            ("img", f"{k}_05_freigegeben", "Freigegebener Auftrag: Zeile gesperrt, Aktion [[↩]] nimmt die Freigabe zurück"),
            ("warn", "Ändern geht nach der Freigabe nicht mehr. Erst [[↩]] Freigabe zurücknehmen, dann umplanen und neu freigeben."),
        ]),
        ("Produktion melden", [
            ("p", "Oben links auf [[Produktion]] umschalten. Je Maschine bzw. Linie sehen Sie den aktuellen Auftrag und die Warteschlange der freigegebenen Aufträge."),
            ("img", f"{k}_06_produktion_bereit", "Produktionsansicht vor dem Start"),
            ("h3", "Starten"),
            ("steps", [
                "Beim nächsten Auftrag [[Produktion starten]]. Das geht nur während der Arbeitszeit der Maschine und nicht deutlich vor dem geplanten Start.",
                "Die Karte wird gelb („In Produktion“).",
            ]),
            ("img", f"{k}_08_laeuft", "Laufender Auftrag mit Gut, Ausschuss und Rest-Stunden"),
            ("h3", "Während der Produktion"),
            ("list", [
                "**Gut / Ausschuss** laufend eintragen (optional, am Ende Pflicht).",
                "**Rest h (Korrektur):** Dauert es länger oder kürzer, die Reststunden anpassen – der Plan der folgenden Aufträge rechnet neu.",
                "[[Pause]] unterbricht (z. B. Störung), [[Fortsetzen]] läuft weiter. Die Pause wird in der Historie festgehalten.",
            ]),
            ("img", f"{k}_09_pausiert", "Pausierter Auftrag", "small"),
            ("h3", "Fertig melden"),
            ("steps", [
                "[[Fertig]] klicken.",
                "Ist-Start und Ist-Fertig prüfen, **Gutmenge** und **Ausschuss** eintragen.",
                "[[Fertig archivieren]] – der Auftrag wandert mit Ist-Zeiten in die Historie und fließt in Report und Nachkalkulation ein.",
            ]),
            ("img", f"{k}_10_fertig_dialog", "Dialog Fertigmeldung", "small"),
            ("h3", "Abbrechen"),
            ("p", "[[Abbrechen]] beendet einen laufenden Auftrag ohne Fertigmeldung. Ein **Grund ist Pflicht**; der Abbruch steht mit Grund in der Historie."),
            ("warn", "Fertig gemeldete und abgebrochene Aufträge lassen sich nicht mehr ändern (die Historie ist unveränderlich). Soll derselbe Auftrag erneut laufen: in der Historie [[Als neuen Auftrag]]."),
        ]),
        ("Personal", [
            ("p", "Unter **Personal** planen Sie, wer wann an welcher Maschine bzw. Linie arbeitet."),
            ("h3", "Wochenplan Personal"),
            ("img", f"{k}_12_personal_woche", "Personal → Wochenplan: je Mitarbeiter und Tag Maschine und Schicht"),
            ("list", [
                "Je Mitarbeiter und Tag im Auswahlfeld Maschine/Linie + Schicht wählen, oder **Frei** / eine Abwesenheit.",
                "Mit [[✎]] unter der Zuordnung Arbeitszeit und Pausen anpassen.",
                "Die Kachel **Unterbesetzte Schichten** und die rote Leiste unten zeigen, wo die Mindestbesetzung (System → Spalte „Personal/Schicht“) nicht erreicht ist.",
                "Mitarbeiter mit Stammmaschine sind automatisch eingeplant.",
            ]),
            ("h3", "Mitarbeiter anlegen und pflegen"),
            ("img", f"{k}_13_mitarbeiter", "Personal → Mitarbeiter"),
            ("steps", [
                "[[+ Mitarbeiter anlegen]] öffnen: Name, Funktion, Typ (Festangestellt/Leiharbeiter), Stammabteilung, Wochenstunden.",
                "Stammmaschine/-linie und Stammschicht (Automatisch, Früh, Spät) wählen.",
                "Bei **Freigaben** alle Maschinen/Linien anhaken, die die Person bedienen darf – nur dort kann sie eingeteilt werden.",
            ]),
            ("h3", "Leiharbeiter anfragen"),
            ("steps", [
                "Beim Anlegen Typ **Leiharbeiter** wählen und den **Zeitraum** (von–bis) eintragen.",
                "Der Knopf heißt dann [[Leiharbeiter anfragen]]. Die Anfrage geht an die GF.",
                "Erst nach Genehmigung der GF und nur im Zeitraum zählt die Person in Planung und Kapazität. Ein geänderter Zeitraum muss neu angefragt werden.",
            ]),
            ("h3", "Urlaub, Krankheit, Sonstiges"),
            ("img", f"{k}_14_abwesenheit", "Personal → Abwesenheit"),
            ("steps", [
                "Mitarbeiter, Art (Urlaub / Krank / Sonstiges) und Zeitraum wählen, [[Eintragen]]. Wochenenden werden übersprungen.",
                "Die Liste zeigt aktuelle Abwesenheiten; [[×]] löscht einen Eintrag.",
                "„Auswirkung auf die Produktion“ zeigt, welche Aufträge dadurch später fertig werden.",
            ]),
            ("tip", "Den Grund (Urlaub/Krank) sehen nur Sie, Ihre Stellvertretung, die GF und der Admin. Alle anderen sehen nur „Abwesend“."),
        ]),
        ("System: Maschinen, Kalender, Benutzer", [
            ("img", f"{k}_15_system", "System → Maschinen & Schichtkalender"),
            ("h3", "Maschinen & Linien"),
            ("list", [
                "Name, Typ (Maschine/Linie), Standardbetrieb (0-, 1-, 2-schichtig), Umrüsten (Minuten), Besetzung (nur Linien) und Personal je Schicht (Mindestbesetzung).",
                "[[+ Maschine]] / [[+ Linie]] legt eine neue Ressource an, [[×]] entfernt sie – nur ohne offene Aufträge.",
            ]),
            ("h3", "Schichtkalender · Abweichungen"),
            ("p", "Abweichender Betrieb für eine Maschine für **eine KW** oder ein **ganzes Jahr** (z. B. 2-schichtig in KW 42): Maschine, „Gilt für“, Jahr, KW, Betrieb → [[Regel speichern]]."),
            ("h3", "Maschinenstillstand / Wartung"),
            ("p", "Maschine, Von, Bis und Grund eintragen → [[+ Sperrzeit]]. In diesem Zeitraum wird nichts eingeplant."),
            ("h3", "Benutzer"),
            ("p", "Im Reiter **Benutzer** legt die Leitung Konten für Stellvertretung und Lesende des eigenen Bereichs an, die Stellvertretung nur Lesende. Passwörter mindestens 8 Zeichen. Leitungskonten legt der Admin an."),
            ("img", f"{k}_16_benutzer", "System → Benutzer", "small"),
        ]),
        BEGRIFFE,
        HILFE,
    ]
    title = f"{d['title']}"
    sub = "Abteilungsleitung und Stellvertretung" + (" · Konfektion 1, 2 und 3" if dep == "konf1" else "")
    return d["file"], document(f"Anleitung {title}", sub, f"Abteilungsleitung und Stellvertretung {d['title']}. Lesende Konten des Bereichs sehen dieselben Ansichten ohne Bearbeitung.", chapters)


# ----------------------------------------------------------------------------- Arbeitsvorbereitung
def av_guide():
    chapters = [
        GRUNDLAGEN,
        ("Ihre Rolle", [
            ("p", "Die **Arbeitsvorbereitung (AV)** legt die Fertigungsaufträge (FS) in allen Bereichen an, gibt mit dem **AV-Termin** vor, bis wann sie fertig sein müssen, verknüpft sie mit Projekten (AB) und plant die Fertigungsabläufe der Projekte."),
            ("list", [
                "**Darf:** FS anlegen, ändern und löschen, solange sie geplant sind; AV-Termin setzen; Projekte nach der Übergabe planen und „Produktion fertig“ melden; eigene AV-Abläufe speichern.",
                "**Darf nicht:** freigeben, Produktion melden, Personal, Maschinen oder Einstellungen ändern – das machen die Bereiche bzw. der Admin.",
            ]),
            ("img", "av_01_start", "Startansicht der AV: Wochenplan aller Bereiche"),
            ("tip", "Oben unter **Bereich** auf einen Bereich einschränken; „Alle Bereiche“ zeigt alles."),
        ]),
        ("Neuen Auftrag (FS) anlegen", [
            ("steps", [
                "[[+ Auftrag]] (oben) bzw. unter Aufträge [[+ Neuer Auftrag]].",
                "**Bereich** und **Maschine / Linie** wählen.",
                "**FS** (Pflicht) eintragen, bei Bedarf **Projekt / AB** wählen – AB und WT werden dann übernommen.",
                "Artikel/Beschreibung, Menge und **Sollstunden** (Pflicht). Bei Taktfertigung: Takt, Teile je Takt und Rüsten eintragen und [[→ Sollstunden]].",
                "**AV-Termin · fertig bis** setzen. Optional Alternative Maschine, Planart und Starttermin.",
                "[[Anlegen]] – der Auftrag wird automatisch frühestmöglich eingeplant.",
            ]),
            ("img", "av_02_neuer_auftrag", "Dialog Neuer Auftrag", "small"),
            ("warn", "Wird der Auftrag erst nach dem AV-Termin fertig, meldet das Programm das sofort (MP-PLAN-063) und die Karte wird rot. Dann mit dem Bereich sprechen, Priorität ändern oder den Termin anpassen."),
            ("img", "av_03_nach_anlegen", "Nach dem Anlegen: Auftrag im Wochenplan"),
        ]),
        ("Aufträge pflegen", [
            ("list", [
                "Planung → Aufträge: Maschine, Sollstunden, Menge und **AV-Termin** direkt in der Zeile ändern.",
                "Nur **geplante** Aufträge können geändert oder gelöscht werden. Freigegebene, laufende und fertige gehören dem Bereich.",
                "Einen AV-Termin, der vor einem schon gesetzten Start- oder Fertigtermin liegt, lehnt das Programm ab (MP-PLAN-062).",
                "Priorität (Reihenfolge) legt der Bereich fest; die AV kann sie ebenfalls ziehen.",
            ]),
        ]),
        ("Projekte nach der Übergabe", [
            ("img", "av_04_projekte", "Projekte-Tafel: Spalte „Arbeitsvorbereitung“ = an die Produktion übergeben"),
            ("steps", [
                "Projekt in der Spalte **Arbeitsvorbereitung** öffnen.",
                "Im Abschnitt **Produktion**: gespeicherten **AV-Ablauf** wählen, Startdatum (vorwärts) oder Enddatum (rückwärts) und [[Anwenden]] – oder Aufgaben einzeln mit Bereich, Start und Fällig anlegen.",
                "Für Fertigungsaufgaben die FS anlegen: [[Als Auftrag einplanen]] oder [[+ Auftrag]] mit Projekt/AB.",
                "Der Fortschritt „In Produktion / Produziert“ kommt automatisch aus den FS.",
                "Sind alle FS fertig: [[Produktion fertig]] schließt das Projekt ab.",
            ]),
            ("img", "av_05_projekt", "Projekt-Fenster mit Kundenplan-Vergleich und Produktion", "small"),
            ("tip", "Der **Kundenplan** (vom PM an den Kunden gegeben) wird mit Ihrer Planung verglichen: „⚠ x AT“ zeigt Verzug gegenüber dem Kunden, „✓ Puffer“ ist in Ordnung."),
            ("p", "Eigene Fertigungsabläufe: im Projekt einen Namen eintragen und [[Als Ablauf speichern]]. Bereiche, Aufgaben und Terminabstände werden gemerkt."),
        ]),
        ("Historie und Report", [
            ("list", [
                "**Historie**: alle fertig gemeldeten und abgebrochenen Aufträge mit Ist-Zeiten. Ältere Einträge unter „Ältere Historie (Archiv)“ nachladen.",
                "**Report**: Monatsreport mit Anzahl, Sollstunden, Gutmenge und Ausschuss; [[PDF/Druck]].",
            ]),
        ]),
        BEGRIFFE, HILFE,
    ]
    return "Anleitung_Arbeitsvorbereitung", document("Anleitung Arbeitsvorbereitung", "Aufträge anlegen, AV-Termine, Fertigungsabläufe", "Mitarbeitende der Arbeitsvorbereitung (AV).", chapters)


# ----------------------------------------------------------------------------- Projektmanagement
def pm_guide():
    chapters = [
        GRUNDLAGEN,
        ("Ihre Rolle und der Projektablauf", [
            ("p", "Das **Projektmanagement (PM)** legt Projekte an, plant die Aufgaben aller Abteilungen bis zur Angebotsannahme, gibt dem Kunden einen Terminplan (Kundenplan) und übergibt das Projekt mit AB und Liefertermin an die Produktion."),
            ("table", ["Schritt", "Wer", "Spalte der Projekte-Tafel"], [
                ["Projekt anlegen, Aufgaben und Termine planen, Angebot", "PM", "Projektmanagement"],
                ["[[An Produktion übergeben]] (AB + Liefertermin Pflicht)", "PM", "→ Arbeitsvorbereitung"],
                ["FS anlegen und fertigen", "AV, Bereiche", "In Produktion (automatisch)"],
                ["Alle FS fertig", "automatisch", "Produziert"],
                ["[[Produktion fertig]]", "PM oder AV", "abgeschlossen (Haken „Abgeschlossene/Verlorene“)"],
            ]),
            ("img", "pm_01_tafel", "Projekte-Tafel"),
        ]),
        ("Neues Projekt anlegen", [
            ("steps", [
                "[[+ Neues Projekt]].",
                "**Kunde** (Pflicht), Bezeichnung/Teil, Ansprechpartner, Wunschtermin des Kunden.",
                "**Workflow** wählen: ein gespeicherter PM-Ablauf wird ab heute angewendet (Aufgaben mit Terminen).",
                "Anfrage/Notiz, [[Anlegen]]. Die Projektnummer (P-JJJJ-nnnn) vergibt das Programm.",
            ]),
            ("img", "pm_02_neues_projekt", "Dialog Neues Projekt", "small"),
        ]),
        ("Aufgaben und Termine planen", [
            ("img", "pm_03_projekt_offen", "Projekt-Fenster: Entwicklung & Vertrieb mit Aufgaben, Ablauf, Übergabe", "small"),
            ("list", [
                "Jede Aufgabe gehört zu einer Abteilung und hat **Start** und **Fällig**. Aufgaben mit gleichem Zeitraum laufen parallel („2 parallel“).",
                "Neue Aufgabe: Abteilung wählen, Titel, Start, Fällig → [[+ Aufgabe]].",
                "**Ablauf anwenden:** gespeicherten Ablauf wählen, „ab“ (vorwärts ab Datum) oder „bis“ (rückwärts, letzter Schritt endet am Datum), [[Anwenden]].",
                "[[⇥ An Liefertermin ausrichten]] verschiebt offene Aufgaben gemeinsam so, dass die späteste am Liefertermin endet.",
                "**Status** melden die Abteilungen selbst. Nur für Konstruktion, Kalkulation, Einkauf, QS und PM setzt das PM den Status.",
                "„nur intern“ an einer Aufgabe = erscheint nicht im Kundenplan.",
                "Eigene Abläufe: Namen eintragen und [[Als Ablauf speichern]]. Liste und Löschen unten auf der Projektseite unter „Gespeicherte Prozessabläufe“.",
            ]),
        ]),
        ("Stammdaten, Angebot und Kundenplan", [
            ("img", "pm_05_projekt_bestand", "Bestehendes Projekt mit Kundenplan und Stammdaten", "small"),
            ("list", [
                "**Stammdaten & Angebot:** Kunde, Bezeichnung, Ansprechpartner, WT, Ablauf, Liefertermin, AB-Nummer, Angebotsnummer, Angebotswert, gültig bis, Notiz → [[Stammdaten speichern]].",
                "**Kundenplan:** [[📊 Excel exportieren]] erzeugt den Terminplan im Firmen-CI (Excel). Jeder Export erhöht die Version und speichert den Stand – die AV sieht später Abweichungen dagegen.",
                "**Verlauf:** Jeder Schritt steht mit Name und Uhrzeit im Projekt.",
            ]),
        ]),
        ("An die Produktion übergeben", [
            ("steps", [
                "Angebot angenommen: **AB-Nummer** und **Liefertermin** in den Stammdaten eintragen und speichern.",
                "Optional Notiz zum Schritt, dann [[An Produktion übergeben]].",
                "Das Projekt erscheint bei der Arbeitsvorbereitung. Stammdaten sind ab jetzt gesperrt.",
            ]),
            ("warn", "Löschen geht nur vor der Übergabe ([[Projekt löschen]] in den Stammdaten)."),
        ]),
        ("Historie und Report", [
            ("img", "pm_06_historie", "Historie (nur lesen)"),
            ("p", "Report: Monatsreport der fertigen Aufträge, [[PDF/Druck]]."),
        ]),
        BEGRIFFE, HILFE,
    ]
    return "Anleitung_Projektmanagement", document("Anleitung Projektmanagement", "Projekte anlegen, planen, Kundenplan, Übergabe", "Mitarbeitende im Projektmanagement (PM).", chapters)


# ----------------------------------------------------------------------------- Vertrieb
def sales_guide():
    chapters = [
        GRUNDLAGEN,
        ("Ihre Rolle", [
            ("p", "Der **Vertrieb** sieht alle Projekte und ihren Stand, pflegt Kundendaten bis zur Annahme und meldet den Status der Vertriebs-Aufgaben. Projekte legt das Projektmanagement an."),
            ("img", "sales_01_tafel", "Projekte-Tafel"),
        ]),
        ("Projekt ansehen und pflegen", [
            ("img", "sales_02_projekt", "Projekt-Fenster", "small"),
            ("list", [
                "Oben: Phase, Termin, Fortschritt „Entwicklung & Vertrieb“ und „Produktion“.",
                "Aufgaben des Bereichs **Vertrieb**: Status selbst setzen (Offen, In Arbeit, Wartet, Erledigt).",
                "Bis zur Annahme: Kunde, Bezeichnung, Ansprechpartner, WT, Notiz sowie AB-Nummer und Liefertermin in den Stammdaten ändern und [[Stammdaten speichern]].",
                "Nach der Übergabe an die Produktion ändert der Vertrieb die Stammdaten nicht mehr.",
            ]),
        ]),
        ("Report", [
            ("img", "sales_03_report", "Monatsreport"),
            ("p", "Monat wählen, [[PDF/Druck]] für den Ausdruck."),
        ]),
        BEGRIFFE, HILFE,
    ]
    return "Anleitung_Vertrieb", document("Anleitung Vertrieb", "Projekte verfolgen und Kundendaten pflegen", "Mitarbeitende im Vertrieb.", chapters)


# ----------------------------------------------------------------------------- Rollenübersicht (GF + Admin)
ROLLEN = ("Rollen und Rechte", [
    ("table", ["Rolle", "Sieht", "Darf ändern"], [
        ["Admin", "alles", "alles inkl. Benutzer, Einstellungen, Backup-Import"],
        ["GF", "GF-Übersicht, Projekte, Historie, Report, System (Abteilungen, Feiertage)", "Leiharbeiter genehmigen/ablehnen, KW-Einsatz, Feiertage/Betriebsferien, Abteilungen anlegen/umbenennen/deaktivieren"],
        ["Abteilungsleitung", "eigener Bereich: Planung, Aufträge, Produktion, Personal, System; Projekte, Historie, Report", "Feinplanung, Freigabe, Produktion, Personal, Maschinen, Schichtkalender, Stillstand; Stellvertretung + Lesende anlegen"],
        ["Stellv. Abteilungsleitung", "wie Leitung", "wie Leitung; nur Lesende anlegen"],
        ["Arbeitsvorbereitung", "Planung und Aufträge aller Bereiche, Projekte, Historie, Report", "FS anlegen/ändern/löschen (geplant), AV-Termin, Fertigungsabläufe, „Produktion fertig“"],
        ["Projektmanagement", "Projekte, Historie, Report", "Projekte, Aufgaben, Termine, Kundenplan, Übergabe, PM-Abläufe"],
        ["Vertrieb", "Projekte, Report", "Kundendaten bis zur Annahme, Status der Vertriebs-Aufgaben"],
        ["Lesend", "Planung, Aufträge, Projekte, Personal, Historie, Report", "nichts"],
    ]),
    ("p", "Den **Abwesenheitsgrund** (Urlaub/Krank/Sonstiges) sehen nur Admin, GF und die Leitung/Stellvertretung des Bereichs, in dem der Mitarbeiter eingesetzt ist – der Server blendet ihn für alle anderen aus."),
    ("p", "Anleitungen je Rolle: CNC, Konfektion, Siebdruck, Tiefziehen, Arbeitsvorbereitung, Projektmanagement, Vertrieb."),
])

ABLAUF = ("Der Gesamtablauf", [
    ("steps", [
        "**Projektmanagement** legt das Projekt an, plant Aufgaben aller Abteilungen, gibt dem Kunden den Kundenplan (Excel) und übergibt nach Annahme mit AB + Liefertermin.",
        "**Arbeitsvorbereitung** legt je Bereich die FS mit AV-Termin an und plant die Fertigungsaufgaben.",
        "**Bereiche** (CNC, Konfektion, Siebdruck, Tiefziehen) planen fein, teilen Personal ein, geben frei, starten und melden fertig.",
        "Fertig gemeldete Aufträge landen mit Ist-Zeiten in der **Historie** → Monatsreport und Nachkalkulation.",
        "**GF** überwacht Termine, Personal und Kapazität, genehmigt Leiharbeiter und setzt Mitarbeiter per KW in anderen Bereichen ein.",
    ]),
])


def gf_chapters():
    return [
        ("GF-Übersicht", [
            ("img", "gf_01_uebersicht", "GF-Übersicht (Start der GF)"),
            ("list", [
                "Kacheln: Bereiche, Festangestellte, Leiharbeiter in der KW, offene Leih-Anfragen, genehmigte Leiharbeiter, gefährdete Termine.",
                "[[Monatsreport]] und [[Historie]] oben rechts.",
            ]),
        ]),
        ("Auswertung & Nachkalkulation", [
            ("img", "gf_02_nachkalkulation", "Auswertung & Nachkalkulation (Ist)"),
            ("steps", [
                "Zeitraum (Von/Bis), Projekt oder Suchbegriff (Artikel, Bezeichnung, FS) wählen.",
                "Tabelle je Bereich: Aufträge, Soll h, Ist h, Abweichung, Gut, Ausschuss, Minuten je Stück, Stück je Stunde, Mitarbeiter.",
                "[[📄 PDF-Bericht]] erzeugt die Nachkalkulation im Firmen-CI mit allen Aufträgen und Mitarbeiterstunden.",
            ]),
            ("tip", "Ist = gemeldete Laufzeit ohne Pausen und Schichtlücken, bei Linien × Besetzung. Soll = Sollstunden + Umrüstzeit. Abweichungen über 10 % sind hervorgehoben."),
        ]),
        ("Abwesenheiten und Leiharbeiter", [
            ("img", "gf_03_abwesenheit", "Urlaub & Krankheit aller Bereiche mit Auswirkung auf die Produktion", "small"),
            ("img", "gf_04_leiharbeiter", "Leiharbeiter-Anfragen", "small"),
            ("steps", [
                "Anfragen der Bereiche stehen mit Zeitraum und Arbeitstagen unter **Leiharbeiter-Anfragen**.",
                "Optional Notiz, dann [[Genehmigen]] oder [[Ablehnen]]. Genehmigte zählen nur im Zeitraum.",
            ]),
        ]),
        ("Mitarbeitereinsatz je KW", [
            ("img", "gf_05_einsatz", "Mitarbeitereinsatz dieser KW"),
            ("p", "Jeder Mitarbeiter existiert genau einmal. Hier wählen Sie für die **gewählte KW** den Einsatzbereich (z. B. CNC-Mitarbeiter hilft in Konfektion). Die Stammabteilung bleibt. Der aufnehmende Bereich plant die Person in dieser KW ein; sie darf dort jede Linie/Maschine bedienen."),
            ("img", "gf_06_auftraege", "Auftragsübersicht je Bereich", "small"),
        ]),
        ("Abteilungen anlegen und pflegen", [
            ("img", "gf_09_abteilungen", "System → Maschinen & Schichtkalender → Abteilungen", "small"),
            ("steps", [
                "System öffnen, Reiter **Maschinen & Schichtkalender**, Bereich **Abteilungen**.",
                "**Neue Abteilung:** Namen eintragen (z. B. „Lackierung“) → [[+ Abteilung]]. Der Name muss eindeutig sein.",
                "**Umbenennen:** Namen in der Liste ändern – wird sofort gespeichert und überall angezeigt.",
                "**Deaktivieren:** Haken „aktiv“ entfernen. Geht nur ohne offene Aufträge und ohne aktive Mitarbeiter mit dieser Stammabteilung; die Zeile zeigt die Zahlen dazu. Wieder aktivieren jederzeit.",
            ]),
            ("p", "Danach richtet der **Admin** im Reiter „Benutzer“ das Konto der Abteilungsleitung ein (Rolle Abteilungsleiter, Bereich = neue Abteilung). Maschinen und Linien legt der Admin oder die neue Leitung unter „Maschinen & Linien“ an ([[+ Maschine]] / [[+ Linie]]). Die Abteilung steht dann auch in Projekten als Bereich zur Verfügung."),
            ("warn", "Abteilungen werden nicht gelöscht, nur deaktiviert – Benutzer, Historie und Projekte verweisen weiter darauf."),
        ]),
        ("Projekte, Report, Feiertage", [
            ("img", "gf_07_projekte", "Projekte-Tafel (GF liest)"),
            ("img", "gf_08_report", "Monatsreport"),
            ("p", "**Feiertage & Betriebsferien** pflegen GF und Admin unter System → Arbeitszeiten & Feiertage: Datum, Bezeichnung, Betrieb (0/1/2-schichtig) → [[+ Tag]]. Gilt für alle Maschinen und Linien."),
        ]),
    ]


def gf_guide():
    chapters = [GRUNDLAGEN, ABLAUF, ROLLEN] + gf_chapters() + [
        ("Was die Bereiche tun (Kurzüberblick)", [
            ("img", "cnc_01_wochenplan", "Wochenplan eines Bereichs (CNC)"),
            ("list", [
                "Wochenplan: Aufträge je Maschine/Linie; rot = AV-Termin wird überschritten.",
                "Freigabe friert den Plan ein; Produktion: Start, Pause, Fertig mit Gut/Ausschuss, Abbruch mit Grund.",
                "Personal: Einteilung je Tag, Abwesenheiten, Leiharbeiter-Anfragen.",
            ]),
            ("img", "cnc_08_laeuft", "Produktion: laufender Auftrag", "small"),
        ]),
        BEGRIFFE, HILFE,
    ]
    return "Gesamtueberblick_GF", document("Gesamtüberblick Geschäftsführung", "Alles auf einen Blick: Abläufe, Rollen, Kennzahlen, Entscheidungen", "Geschäftsführung.", chapters)


def admin_guide():
    chapters = [GRUNDLAGEN, ABLAUF, ROLLEN,
        ("System-Einstellungen", [
            ("img", "admin_02_system", "System (Admin)"),
            ("p", "Die Einstellungen sind in Reiter gegliedert. Der Admin darf alles, Bereichsleitungen nur ihren Bereich."),
            ("img", "admin_15_abteilungen", "Abteilungen: anlegen, umbenennen, deaktivieren (Admin und GF)", "small"),
            ("p", "**Neue Abteilung einrichten:** 1. unter **Abteilungen** anlegen, 2. im Reiter **Benutzer** die Leitung mit diesem Bereich anlegen, 3. unter **Maschinen & Linien** die Ressourcen anlegen (oder die Leitung macht das selbst). Ausführlich im Kapitel „Abteilungen anlegen und pflegen“."),
            ("img", "admin_03_maschinen", "Maschinen & Linien aller Bereiche", "small"),
            ("list", [
                "**Maschinen & Linien:** je Bereich anlegen, umbenennen, Typ, Standardbetrieb, Umrüstzeit, Besetzung, Personal je Schicht.",
                "**Schichtzeiten & Pausen:** Beginn/Ende und bis zu zwei Pausen für 1-Schicht Mo–Do, 1-Schicht Freitag, Früh- und Spätschicht.",
                "**Schichtkalender · Abweichungen:** Betrieb je Maschine für eine KW oder ein Jahr.",
                "**CNC-Bediener je Schicht:** Obergrenze gleichzeitig laufender CNC-Maschinen.",
                "**Feiertage & Betriebsferien**, **Maschinenstillstand / Wartung**.",
                "**Scheduler testen:** prüft Schichten, Freitag, Vorwärts/Rückwärts und Konflikte.",
            ]),
            ("img", "admin_05_schichten", "Schichtzeiten & Pausen", "small"),
            ("img", "admin_07_feiertage", "Feiertage & Betriebsferien", "small"),
        ]),
        ("Firmen-CI für Kundenpläne und Berichte", [
            ("img", "admin_04_ci", "Firmen-CI", "small"),
            ("p", "Firmenname (mit *…* fett), Adresse, Fußzeile, Schrift, Farbe und Logo (PNG/JPG bis 300 KB). Wird im Excel-Kundenplan und im PDF der Nachkalkulation verwendet."),
        ]),
        ("Benutzer & Rechte", [
            ("img", "admin_10_benutzer", "Benutzer & Rechte"),
            ("steps", [
                "Benutzername, Passwort (mind. 8 Zeichen), Rolle und bei Bereichsrollen den Bereich wählen → [[Benutzer anlegen]].",
                "In der Liste: Rolle und Bereich ändern, [[Sperren]] / [[Aktivieren]], [[Passwort]] neu setzen. Gesperrte oder neu gesetzte Passwörter beenden die Sitzungen des Kontos.",
                "Das eigene Konto ändern Sie nur über [[🔑]] (Passwort).",
            ]),
            ("tip", "Leitungs-, AV-, PM-, Vertriebs- und GF-Konten legt nur der Admin an. Konten mit alten Rollen aus V11 (planner/production) haben keine Rechte, bis Sie eine neue Rolle zuweisen; sperren geht jederzeit."),
        ]),
        ("Daten: Änderungsprotokoll, Historie, Archiv, Backup", [
            ("img", "admin_12_aenderungen", "Änderungsprotokoll"),
            ("list", [
                "**Änderungen:** wer hat wann was gespeichert (Protokoll im Programm; zusätzlich serverseitig in der Datenbank).",
                "**Historie:** alle fertigen und abgebrochenen Aufträge; ab 1.500 Einträgen verschiebt der Server die ältesten ins Archiv – unter „Ältere Historie (Archiv)“ nachladen.",
                "**Darstellung & Daten:** [[JSON speichern]] (vollständige Sicherung als Datei), [[JSON öffnen]] (Backup-Import, nur Admin), [[CSV]] (Aufträge für Excel), [[Planung zurücksetzen]] (nur ohne laufende Produktion).",
            ]),
            ("img", "admin_09_daten", "Darstellung & Daten", "small"),
            ("warn", "Die tägliche Datenbank-Sicherung macht der Server selbst (12:15 und 22:15, 60 Stände). Eine Zweitkopie auf NAS über BACKUP_ZIEL.txt ist dringend empfohlen."),
        ]),
        ("Server-Betrieb (Windows)", [
            ("img", "admin_11_server", "Server-Betrieb", "small"),
            ("table", ["Aufgabe", "PowerShell im Live-Ordner C:\\ProgramData\\Maschinenplanung"], [
                ["Status, Version, letztes Backup", ".\\Server_Status.ps1"],
                ["Starten / Stoppen / Neustart", ".\\Start_Server.ps1 · .\\Stop_Server.ps1 · .\\Neustart_Server.ps1"],
                ["Sofort sichern", ".\\Backup_Datenbank.ps1"],
                ["Sicherheits- und Betriebscheck", ".\\CHECK_LAN_SICHERHEIT.ps1"],
                ["Update einspielen (als Administrator, im entpackten Paket)", ".\\UPDATE_LIVE.ps1 – mit automatischem Backup und Rückfall bei Fehler"],
            ]),
            ("steps", [
                "**Backup wiederherstellen:** Stop_Server.ps1 → im Ordner data die Dateien -wal und -shm löschen → gewünschte Sicherung aus backups nach data\\maschinenplanung.sqlite3 kopieren → Start_Server.ps1.",
                "Nach jedem Update alle Browser einmal mit [[Strg]]+[[F5]] neu laden.",
            ]),
            ("warn", "Nie nur die Datei maschinenplanung.sqlite3 kopieren – der jüngste Stand kann in der -wal-Datei liegen. Immer Backup_Datenbank.ps1 verwenden. Der Server ist nur für das eigene LAN gedacht (HTTP, keine Portweiterleitung)."),
        ]),
    ] + gf_chapters() + [BEGRIFFE, HILFE]
    return "Gesamtueberblick_Admin", document("Gesamtüberblick Admin", "Einrichtung, Benutzer, Daten, Server und alle Abläufe", "Administratorinnen und Administratoren.", chapters)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    docs = [dept_guide(k, d) for k, d in DEPTS.items()] + [av_guide(), pm_guide(), sales_guide(), gf_guide(), admin_guide()]
    for name, doc in docs:
        (OUT / f"{name}.html").write_text(doc, encoding="utf-8")
        print(name)


if __name__ == "__main__":
    main()

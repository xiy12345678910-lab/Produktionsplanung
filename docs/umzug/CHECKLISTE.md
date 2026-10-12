# Checkliste Umzug GitHub → Gitea

Von oben nach unten abhaken. Details stehen jeweils in der genannten Datei.

## A. Vorher (GitHub läuft noch)
- [ ] Kunde auf V12.28.3 (läuft, 12.10.2026), danach V12.28.4 (Entwurf auf GitHub veröffentlichen, dann Update)
- [ ] Issue #91 nach Gitea übertragen
- [ ] Angaben festlegen: Gitea-URL, Konto/Repo, öffentlich/privat, Gitea-Version (Actions ab 1.19, besser ≥ 1.21)
- [ ] Ist Gitea vom Kunden-LAN aus erreichbar? (HTTPS-Zertifikat gültig, Firewall)

## B. Gitea einrichten
- [ ] Leeres Repo anlegen (ohne README und Lizenz)
- [ ] Code und Release-Tags übertragen (`BEFEHLE.md` 1)
- [ ] Archiv-Tags (`BEFEHLE.md` 2)
- [ ] Releases mit Paketen, SHA256 abgeglichen (`BEFEHLE.md` 3–4)
- [ ] Actions einschalten, Runner registrieren: Linux (Python 3.12, Node 22, Chromium) und Windows (PowerShell 5.1, Python 3.12)
- [ ] `.gitea/workflows/ci.yml` (Kopie von `.github/workflows/ci.yml`, Runner-Labels anpassen) – CI auf `main` grün
- [ ] `.gitea/workflows/release.yml` mit der Gitea-API neu (Entwurf + Pakete, Prüfung „CI grün“)
- [ ] Branch-Schutz `main` (PR-Pflicht, Status „CI“), Tag-Schutz `v*`

## C. Brückenversion (`PLAN_BRUECKENVERSION.md`)
- [ ] V12.29.0: Updater lädt von Gitea (`app_updates.py`, `Update_von_Gitea.ps1`, Tests)
- [ ] V12.29.0 auf **GitHub** veröffentlichen (damit alte Server sie noch finden) **und** auf Gitea
- [ ] Kunde: V12.29.0 installiert, „Update installieren“ findet danach Releases auf Gitea (z. B. Test-Release prüfen)

## D. Abschluss
- [ ] Doku anpassen (`INVENTAR.md` 6)
- [ ] GitHub-Repo archivieren (nur lesend), Wartezeit (z. B. 4 Wochen)
- [ ] GitHub-Repo löschen

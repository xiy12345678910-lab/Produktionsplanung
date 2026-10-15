# Gitea Actions: was die Runner brauchen

Stand: 15.10.2026. Gitea liest `.github/workflows/`, solange es kein `.gitea/workflows/` gibt. Die Workflows bleiben deshalb, wo sie sind, und laufen auf GitHub und Gitea.
Getestet: Ein Push und ein PR auf Gitea lösen `CI` aus (Test-PR #94). Ohne Runner bleiben alle Jobs auf „queued“.

## Runner und Labels

| Label (`runs-on`) | Jobs | Braucht |
|---|---|---|
| `ubuntu-latest` | `python`, `e2e`, `Release package` | Linux mit Docker (act_runner-Standard). Die Jobs holen Python 3.12 und Node 22 selbst (`actions/setup-python`, `actions/setup-node`). `npx playwright install --with-deps chromium` braucht im Container root/apt. |
| `windows-latest` | `powershell`, `windows-deploy` (Matrix 4×), `windows-update-button` (2×) | Windows-`act_runner` **im Host-Modus** (kein Docker). Windows PowerShell 5.1 **und** PowerShell 7 (`pwsh`), Git, Admin-Rechte (die Tests legen Aufgaben, Firewallregeln und Ordner-ACLs an wie beim Kunden). `actions/setup-python` installiert Python 3.12. `Install-Module PSScriptAnalyzer` braucht Zugriff auf die PowerShell Gallery. |

- Die Actions (`actions/checkout@v4`, `setup-python`, `setup-node`) kommen von github.com. Das bleibt auch nach dem Löschen unseres GitHub-Repos so, nur der Runner braucht Internet.
- `windows-deploy` holt alte Stände per `git` (volle Historie, `fetch-depth: 0`).
- Der Windows-Runner verändert das System (geplante Aufgaben, Firewall, `C:\ProgramData\Maschinenplanung`). Deshalb eine eigene VM nehmen, nicht den Kundenserver und nicht den Gitea-Server.

## Release-Workflow auf Gitea

- `Release package` (manuell auf `main`) prüft auf Gitea die Commit-Status des Release-Commits: Jeder Status `CI / … (push)` muss `success` sein. Danach legt der Workflow einen **Entwurf** mit Paket und Manifest über die Gitea-API an. Veröffentlichen und damit den Tag anlegen tut weiter nur jonas.
- Der Workflow-Token (`github.token`) braucht Schreibrecht auf Releases. Bei Gitea ist das Standard für Workflows im eigenen Repo.

## Branch-Schutz (Admin)

Sobald die Runner laufen: In `main` die Statusprüfungen `CI / python (pull_request)`, `CI / e2e (pull_request)` und die Windows-Jobs als Pflicht eintragen.

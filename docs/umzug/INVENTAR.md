# Inventar GitHub → Gitea

Erhoben am 12.10.2026 aus `xiy12345678910-lab/Produktionsplanung`. `main` steht auf `93fc8bd` (V12.28.4).
Die Spalte „Ziel“ sagt, was auf Gitea passiert.

## 1. Branches

### Nicht in main (geprüft: Commits, Inhalt, PRs)

| Branch | Kopf | Letzter Commit | Inhalt | Ziel |
|---|---|---|---|---|
| `claude/sharp-noether-hc1ycl` | 872fe33 | 05.10. | V12.7.6, Mockup Formatplanung, WIP-Patch V12.8.0; Funktionen längst in main | **Archiv-Tag** `archive/sharp-noether-hc1ycl` |
| `claude/dazzling-feynman-6zu513` | 1d34167 | 06.10. | WIP #34 auf V12.18: Umzug-Skripte, Teilzeit usw. (in main besser gelöst). Offen gebliebene Idee: Windows-Build-Prüfung (Warnung unter Build 17763) | **Archiv-Tag** `archive/dazzling-feynman-6zu513` |
| `claude/bold-heisenberg-bx6qyt` | 2ca1c52 | 10.10. | Früher Lösungsversuch #84 (MP-PLAN-073), ersetzt durch ec0bbab „Arbeitsplatz anlegen“ | **weglassen** |
| `claude/new-session-mpx5ch` | 17bed0d | 05.10. | Nur `docs/FEATURE_PROMPTS.md` (Ideenliste); PR #7 geschlossen. Alte Server ≤ V12.10.1 nutzten den Branchnamen, brauchen aber ohnehin GitHub | **weglassen** (optional Archiv-Tag) |

### Vollständig in main enthalten → weglassen

`claude/friendly-cray-r6h3e8`, `claude/happy-thompson-fuifkm`, `claude/loving-knuth-bphlrr`, `claude/quirky-fermi-r1ei1g`,
`claude/new-session-95ro2l`, `codex/blocks-a-d`, `codex/lazy-loading`, `codex/lazy-loading-block-e`, `codex/v12.19.1-hotfixes`,
`xiy12345678910-lab-patch-1`. Bei allen ist `git rev-list --count origin/main..<branch>` = 0.

**Auf Gitea also nur der Branch `main`** (mit voller Historie, kein flacher Clone).

## 2. Tags und Releases

| Tag | Commit | Release auf GitHub | Pakete | Ziel |
|---|---|---|---|---|
| v12.17.1 | c74580c | – | – | Tag |
| v12.18.0 | 4b2a4c2 | – | – | Tag |
| v12.19.0 | 2a97556 | – | – | Tag |
| v12.20.0 | b30005c | ja | ZIP + Manifest | Tag + Release + Pakete |
| **V12.20.0** (großes V) | b30005c | – | – | **weglassen** (doppelt, falsche Schreibweise) |
| v12.22.0 | 332f02e | ja | ZIP + Manifest | Tag + Release + Pakete |
| v12.24.0 | 9810cdd | – | – | Tag |
| v12.27.0 | 4d0a669 | ja | ZIP + Manifest | Tag + Release + Pakete (**Pflicht**, Rückstufung ab hier) |
| v12.28.0 | c932aca | ja | ZIP + Manifest | Tag + Release + Pakete (**Pflicht**) |
| v12.28.1 | a2dc0c2 | ja | ZIP + Manifest | Tag + Release + Pakete (**Pflicht**) |
| v12.28.3 | 327f4b5 | ja | ZIP + Manifest | Tag + Release + Pakete (**Pflicht**) |
| v12.28.4 | 93fc8bd | **Entwurf** (noch kein Tag) | ZIP + Manifest | erst auf GitHub veröffentlichen, dann wie oben übertragen |

Pakete je Release: `produktionsplanung-vX.Y.Z.zip` und `update-manifest.json`. Das Manifest enthält den Commit-SHA.
Werden `main` und die Tags unverändert übertragen, bleiben die SHAs und damit die Manifeste gültig.

Die CI braucht alte Stände: `dac0860`, `5343676` (Windows-Deploy-Matrix) und `4624619` (`test_deploy_upgrade.py`).
Alle drei liegen in `main` und bleiben erhalten.

## 3. Issues

Bereits nach Gitea übertragen (Betreiber). **Nachtragen:** #91 „Softwareupdate: alter Fehlerstatus bleibt stehen …“ (12.10.2026).
Issue-Links auf `github.com/.../issues/NN` in `docs/V12_19_1.md` u. a. werden ungültig. Nummern bleiben bei einer Gitea-Migration meist gleich.

## 4. CI und Release (GitHub Actions)

| Datei | Läuft auf | Auf Gitea |
|---|---|---|
| `.github/workflows/ci.yml` | ubuntu-latest (Python, E2E/Chromium), windows-latest (PowerShell-Prüfung, Deploy-Matrix, Update-Button mit PowerShell 5.1) | Gitea Actions mit eigenen Runnern: Linux **und** Windows (`act_runner`). Gitea liest `.gitea/workflows/` vor `.github/workflows/` |
| `.github/workflows/release.yml` | `actions/github-script`, `gh release create` | **neu schreiben** mit der Gitea-API (Entwurf + Asset-Upload, Prüfung „CI grün für diesen Commit“) |

Secrets: Die Workflows nutzen nur das eingebaute `github.token`. Es sind keine eigenen Secrets zu übertragen.

## 5. Im Programm fest verdrahtet (betrifft Kundenserver)

| Stelle | GitHub-Bezug | Wird geändert in |
|---|---|---|
| `app_updates.py` | `api.github.com`, Weiterleitungen nur `*.github.com` | V12.29.0 (Brücke) |
| `Update_von_GitHub.ps1` | `api.github.com`, `codeload.github.com`, Standard-Repo | V12.29.0 → `Update_von_Gitea.ps1` |
| `LAN_CONFIG.json` beim Kunden | `UpdateRepo` = `konto/repo` (Host immer GitHub) | V12.29.0: zusätzlich `UpdateHost` |
| Tests | `tests/update_button_lib.py` (GitHub-API-Nachbildung), `test_update_button.py` | V12.29.0 |

## 6. Doku mit GitHub-Bezug (nach dem Umzug anpassen)

`README_Windows.txt` (2b Update, Release anlegen, Branch-Schutz), `docs/RELEASE_REGELN.md`, `docs/ROLLBACK.md`,
`docs/ADMIN_UPDATES.md`, `docs/UEBERGABE.md`, `docs/LIZENZ.md`, `README.md`, `FEHLERCODES.txt` (Rückstufungsbefehl).

## 7. Nicht im Repo (nichts zu übertragen, nur merken)

- Privater Lizenz-Signaturschlüssel: beim Lizenzgeber, nie im Repo (`docs/LIZENZ.md`).
- Kundendaten, `config\`, `LAN_CONFIG.json`: nur auf dem Kundenserver.
- `tools/legacy_employer_seed.json` liegt im Repo und kommt mit `main` mit.

# Befehle zum Kopieren (Linux/WSL/Git-Bash)

Benötigt: `git`, `curl`, `jq` und für die Pakete `gh` (GitHub CLI, angemeldet). Das Gitea-Repo vorher **leer** anlegen, ohne README und ohne Lizenz.
Mit diesen Befehlen kommen auf Gitea genau: `main`, 10 Release-Tags, 2 Archiv-Tags und 6 Releases mit Paketen. Sonst nichts.

## 0. Variablen

```bash
GH_REPO="xiy12345678910-lab/Produktionsplanung"
GITEA_URL="https://GITEA_HOST"             # ohne / am Ende
GITEA_OWNER="OWNER"; GITEA_REPO="Produktionsplanung"
GITEA_TOKEN="TOKEN"                        # Gitea → Einstellungen → Anwendungen, Recht write:repository
GITEA_GIT="$GITEA_URL/$GITEA_OWNER/$GITEA_REPO.git"
API="$GITEA_URL/api/v1/repos/$GITEA_OWNER/$GITEA_REPO"
```

## 1. Code: nur main und Release-Tags

```bash
git clone --bare "https://github.com/$GH_REPO.git" pp.git
cd pp.git
git remote add gitea "$GITEA_GIT"
git push gitea refs/heads/main:refs/heads/main
git push gitea refs/tags/v12.17.1 refs/tags/v12.18.0 refs/tags/v12.19.0 \
               refs/tags/v12.20.0 refs/tags/v12.22.0 refs/tags/v12.24.0 \
               refs/tags/v12.27.0 refs/tags/v12.28.0 refs/tags/v12.28.1 refs/tags/v12.28.3
# Nach dem Veröffentlichen von v12.28.4 auf GitHub:  git fetch origin --tags && git push gitea refs/tags/v12.28.4
# NICHT verwenden: --mirror, --all, --tags (würde V12.20.0 und alle Alt-Branches mitnehmen).
```

## 2. Archiv-Tags für zwei alte Branches

```bash
git push gitea 872fe33:refs/tags/archive/sharp-noether-hc1ycl
git push gitea 1d34167:refs/tags/archive/dazzling-feynman-6zu513
# optional: git push gitea 17bed0d:refs/tags/archive/new-session-mpx5ch
git ls-remote gitea          # Kontrolle: refs/heads/main + 10 Tags v12.* + 2 Tags archive/*
cd ..
```

## 3. Releases mit Paketen

```bash
for T in v12.20.0 v12.22.0 v12.27.0 v12.28.0 v12.28.1 v12.28.3; do      # v12.28.4 ans Ende, sobald veröffentlicht
  mkdir -p "rel/$T"
  gh release download "$T" -R "$GH_REPO" -D "rel/$T" -p "produktionsplanung-$T.zip" -p "update-manifest.json"
  gh release view "$T" -R "$GH_REPO" --json body -q .body > "rel/$T/body.md"
  echo "$T: $(sha256sum rel/$T/produktionsplanung-$T.zip | cut -c1-64)  Manifest: $(jq -r .sha256 rel/$T/update-manifest.json)"   # müssen gleich sein

  ID=$(jq -n --arg t "$T" --arg n "Produktionsplanung $T" --rawfile b "rel/$T/body.md" \
        '{tag_name:$t, name:$n, body:$b, draft:false, prerelease:false}' |
       curl -sS -X POST "$API/releases" -H "Authorization: token $GITEA_TOKEN" \
            -H "Content-Type: application/json" --data-binary @- | jq -r .id)
  echo "$T -> Release-ID $ID"
  for F in "produktionsplanung-$T.zip" "update-manifest.json"; do
    curl -sS -X POST "$API/releases/$ID/assets?name=$F" -H "Authorization: token $GITEA_TOKEN" \
         -F "attachment=@rel/$T/$F" | jq -r '.name + "  " + (.size|tostring)'
  done
done
```

## 4. Kontrolle

```bash
curl -sS -H "Authorization: token $GITEA_TOKEN" "$API/releases?limit=50" |
  jq -r '.[] | .tag_name + "  draft=" + (.draft|tostring) + "  " + ([.assets[].name]|join(","))'
curl -sS -H "Authorization: token $GITEA_TOKEN" "$API/releases/latest" | jq -r .tag_name   # erwartet: neuestes Release
git ls-remote "$GITEA_GIT" | wc -l                                                          # 1 Branch + 12 Tags = 13
```

Erwartung: 6 Releases, je 2 Pakete, keine Entwürfe. `latest` ist das neueste.
Falls Gitea `latest` anders sortiert, das jüngste Release zuletzt anlegen (die Schleife tut das).

## 5. GitHub am Ende (erst nach `CHECKLISTE.md` Punkt „Kunde auf V12.29.0“)

GitHub → Settings → General → „Archive this repository“. Nach einer Wartezeit „Delete this repository“.

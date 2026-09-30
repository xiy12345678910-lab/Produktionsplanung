#!/usr/bin/env bash
# Erzeugt alle Anleitungen neu: Demo-Server mit Beispieldaten -> Bildschirmfotos -> HTML -> PDF.
# Aufruf aus dem Repository:  bash tools/anleitungen/erstellen.sh
# Voraussetzung: Python 3, Node mit Playwright/Chromium. Nutzt nie den Live-Datenordner.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
WORK="$(mktemp -d)"
PORT="${MP_DEMO_PORT:-18999}"
export MP_DEMO_PORT="$PORT" TZ="${TZ:-Europe/Berlin}"
mkdir -p "$WORK/data" "$WORK/img" "$WORK/html"
python3 "$HERE/demo_server.py" "$WORK/data" "$PORT" > "$WORK/server.log" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null || true; rm -rf "$WORK"' EXIT
for _ in $(seq 1 50); do grep -q ready "$WORK/server.log" 2>/dev/null && break; sleep 0.2; done
python3 "$HERE/seed_demo.py"
node "$HERE/capture.mjs" "$WORK/img"
python3 "$HERE/build.py" "$WORK/img" "$WORK/html"
node "$HERE/render.mjs" "$WORK/html" "$ROOT/docs/anleitungen"
echo "Fertig: $ROOT/docs/anleitungen"

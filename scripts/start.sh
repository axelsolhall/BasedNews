#!/usr/bin/env bash
# start.sh — run the BasedNews site locally on :4200 (Angular dev server). Stop with scripts/stop.sh.
# The site reads static JSON from frontend/src/assets; there is no API server (ADR-010).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="$ROOT/.logs"; mkdir -p "$LOGS"
say() { printf '\033[1;34m[basednews]\033[0m %s\n' "$*"; }
busy() { lsof -ti ":$1" >/dev/null 2>&1; }

if busy 4200; then say "port 4200 busy — leaving it"; else
  (cd "$ROOT/frontend" && nohup npx ng serve --port 4200 > "$LOGS/frontend.log" 2>&1 & echo $! > "$LOGS/frontend.pid")
  say "site starting on :4200 (log .logs/frontend.log)"
fi
for i in $(seq 1 90); do busy 4200 && break; sleep 1; done
busy 4200 && say "Website  http://localhost:4200" || say "site not up yet — see .logs/frontend.log"

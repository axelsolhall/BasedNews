#!/usr/bin/env bash
# stop.sh — stop what start.sh started (pid file first, then whatever still holds :4200).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
say() { printf '\033[1;34m[basednews]\033[0m %s\n' "$*"; }
for f in "$ROOT"/.logs/*.pid; do [[ -e "$f" ]] || continue; kill "$(cat "$f")" 2>/dev/null; rm -f "$f"; done
pids="$(lsof -ti :4200 2>/dev/null)"; [[ -n "$pids" ]] && kill $pids 2>/dev/null
for i in $(seq 1 15); do lsof -ti :4200 >/dev/null 2>&1 || break; sleep 1; done
lsof -ti :4200 >/dev/null 2>&1 && kill -9 $(lsof -ti :4200) 2>/dev/null
say "site is down"

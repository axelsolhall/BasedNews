#!/usr/bin/env bash
# history_judge_stop.sh — stop the history judging and discard the day it was on: the loops first (so nothing restarts),
# then the judge and grouping processes, then replay trim — the database ends at the last finished day, exactly as the
# saved positions do, and history_judge_start.sh resumes from the day after it.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$ROOT/data/history/run"
cd "$ROOT" || exit 1
say() { printf '\033[1;34m[judging]\033[0m %s\n' "$*"; }

[[ -f "$RUN/pids" ]] && while read -r p; do kill "$p" 2>/dev/null; done < "$RUN/pids"
pkill -f history_judge_loop.sh 2>/dev/null
pkill -f "zsh judge_only|zsh group3.sh|zsh judge_chain" 2>/dev/null          # the hand-started loops of 2026-10-23/25
pkill -f "pipeline.history.replay .*(--judge local|--count-only)" 2>/dev/null
for i in $(seq 1 30); do
  pgrep -f "pipeline.history.replay .*(--judge local|--count-only)" >/dev/null || break; sleep 1
done
pkill -9 -f "pipeline.history.replay .*(--judge local|--count-only)" 2>/dev/null
rm -f "$RUN/pids"
PYTHONPATH=. uv run python -m pipeline.history.replay trim
say "stopped at $(sqlite3 data/history/history.sqlite "SELECT max(day) FROM history_progress") (the last finished day)"

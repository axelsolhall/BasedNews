#!/usr/bin/env bash
# history_judge_start.sh — judge the history (A61) from the day after the last finished one. First anything of an
# unfinished day is discarded (replay trim: the database matches the saved positions exactly), then it waits for the
# local judge server (G4-1/3 on the PC), then starts three grouping processes and the judge, each restarted 5 s after
# it stops. Logs and pids in data/history/run/. Stop with scripts/history_judge_stop.sh.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$ROOT/data/history/run"; mkdir -p "$RUN"
cd "$ROOT" || exit 1
say() { printf '\033[1;34m[judging]\033[0m %s\n' "$*"; }

if pgrep -f "pipeline.history.replay .*(--judge local|--count-only)" >/dev/null || pgrep -f history_judge_loop.sh >/dev/null; then
  say "already running — scripts/history_judge_stop.sh first"; exit 1
fi
PYTHONPATH=. uv run python -m pipeline.history.replay trim || { say "trim failed: not starting"; exit 1; }
until PYTHONPATH=. uv run python -c "from pipeline.judge.client import local_client
print([m.id for m in local_client(timeout=10).models.list().data])" 2>/dev/null | grep -q g4-one-third; do
  say "waiting for the judge server on the PC"; sleep 30
done
SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)          # only story runs grouped from now on count
: > "$RUN/pids"
for off in 0 1 2; do
  nohup "$ROOT/scripts/history_judge_loop.sh" group "$SINCE" "$off" > "$RUN/group_$off.log" 2>&1 &
  echo $! >> "$RUN/pids"
done
nohup "$ROOT/scripts/history_judge_loop.sh" judge "$SINCE" > "$RUN/judge.log" 2>&1 &
echo $! >> "$RUN/pids"
say "judging from $(sqlite3 data/history/history.sqlite "SELECT coalesce(date(max(day), '+1 day'), '2015-11-01') FROM history_progress") — log: data/history/run/judge.log"

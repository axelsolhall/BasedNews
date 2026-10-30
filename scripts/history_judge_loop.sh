#!/usr/bin/env bash
# history_judge_loop.sh judge SINCE | group SINCE OFFSET — one process of the history judging (history_judge_start.sh
# starts them), restarted 5 s after it stops, always from the day after the last finished one.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
MODE=$1; SINCE=$2; OFFSET=${3:-0}
END=${END:-2026-10-22}
R=(--prefetched --vectors data/history/vec --embed-model Qwen3-Embedding-0.6B --threshold 0.54)
next() { sqlite3 -cmd ".timeout 60000" data/history/history.sqlite \
           "SELECT coalesce(date(max(day), '+1 day'), '2015-11-01') FROM history_progress"; }
for k in $(seq 1 100); do
  n=$(next); [[ -n "$n" ]] || { sleep 5; continue; }
  if [[ $MODE == judge ]]; then
    PYTHONPATH=. uv run python -m pipeline.history.replay "${R[@]}" --judge local --judge-fraction 0.05 \
      --grouped "$SINCE" --start "$n" --end "$END" && break
  else
    PYTHONPATH=. uv run python -m pipeline.history.replay "${R[@]}" --count-only --recount --ahead 4 --stride 3 \
      --offset "$OFFSET" --start "$n" --end "$END" && break
  fi
  echo "$MODE $OFFSET stopped (try $k): again in 5 s"; sleep 5
done
echo "$MODE $OFFSET DONE $(date +%H:%M)"

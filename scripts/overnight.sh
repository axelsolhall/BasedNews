#!/usr/bin/env bash
# Unattended data collection (2026-10-10): live ingest every 2 h + 10-year Wayback backfill, for HOURS hours.
# Keeps the Mac awake with caffeinate (lid must stay open / on power). Stop: kill $(cat logs/overnight.pid)
set -u
cd "$(dirname "$0")/.."
HOURS="${1:-10}"
END=$(( $(date +%s) + HOURS * 3600 ))
echo $$ > logs/overnight.pid
exec > >(tee -a logs/overnight.log) 2>&1
echo "=== overnight start $(date) for ${HOURS}h ==="

caffeinate -i -w $$ &

# backfill: coarse-to-fine, resumable, outage-tolerant; restart if it exits early
(
  while [ "$(date +%s)" -lt "$END" ]; do
    uv run python -m pipeline.backfill.run --years 10 --per-snapshot 4 >> logs/backfill.log 2>&1
    sleep 60
  done
) &
BF=$!

# live: health check + ingest every 2 h
while [ "$(date +%s)" -lt "$END" ]; do
  echo "--- live ingest $(date)"
  uv run python -m pipeline.sources.run   >> logs/live.log 2>&1
  uv run python -m pipeline.ingest.run    >> logs/live.log 2>&1
  echo "    done $(date): $(tail -1 logs/live.log)"
  sleep 7200
done

kill $BF 2>/dev/null; pkill -f "pipeline.backfill.run" 2>/dev/null
echo "=== overnight end $(date) ==="
uv run python -m pipeline.ingest.eval   > logs/morning_ingest_eval.txt 2>&1
uv run python -m pipeline.backfill.eval > logs/morning_backfill_eval.txt 2>&1
rm -f logs/overnight.pid

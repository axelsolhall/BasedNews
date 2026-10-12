"""3.2 call log (A51): everything a paid judge call returns or depends on, kept so later analyses never re-buy it.

    uv run python -m pipeline.judge.calllog            # check: coverage, integrity, what the log holds

Per call (table `judge_call_log`, one row per attempt-group, also for calls that failed):
- the exact request (model, messages incl. the rendered articles, schema, reasoning effort), zlib-compressed + sha256 —
  article texts and headline histories change after re-cleaning, so the stored request is the only exact record;
- the provider's full response (returned model snapshot, response id, system fingerprint, finish reason, token
  breakdown incl. cached and reasoning tokens);
- timing (start, end, latency), attempts and retry errors (API keys scrubbed);
- context: measure run id, batch, the planner's score at pick time, estimated cost, the two outlets' positions and sds
  per axis when the pair was picked, story run, budget left, spreads (τ, σ_a);
- code fingerprint (hash of the judge's source files) and SDK version.
`judge_calls` / `judgments` stay the lean tables the model fits from; this table is the archive. After ≈ 30 days the
retention job (A52) moves the request blob to the Parquet lake (`data/lake/judge_call_log/`) and sets `request_z` to NULL
here; the hash stays, so the lake copy can still be checked against it.
"""
from __future__ import annotations

import hashlib
import json
import re
import zlib
from pathlib import Path

from pipeline import store

JUDGE_SOURCES = ("prompt.py", "render.py", "blind.py", "client.py")
_KEY = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


def redact(text: str) -> str:
    """Never let an API key reach a log (provider errors and tracebacks can echo part of it)."""
    return _KEY.sub("sk-***", str(text))


def scrub(text: str) -> str:
    return redact(text)[:1000]


def to_jsonable(obj):
    """Provider response → plain JSON: pydantic models via model_dump, otherwise a recursive walk (fakes in tests)."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return {k: to_jsonable(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return obj if isinstance(obj, (str, int, float, bool)) or obj is None else str(obj)


def pack_request(kwargs: dict) -> tuple[str, bytes]:
    data = json.dumps(kwargs, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest(), zlib.compress(data, 6)


def unpack_request(blob: bytes) -> dict:
    return json.loads(zlib.decompress(blob))


def code_fingerprint() -> str:
    h = hashlib.sha256()
    for name in JUDGE_SOURCES:
        h.update((Path(__file__).parent / name).read_bytes())
    return h.hexdigest()[:12]


def save(conn, call_id: str, log: dict, context: dict | None = None, status: str = "ok") -> None:
    """Write one log row. `log` comes from OpenAIJudge (empty for the simulated judge → nothing stored)."""
    if not log:
        return
    sha, blob = pack_request(log["request"])
    resp = log.get("response") or {}
    usage = resp.get("usage") or {}
    choice = (resp.get("choices") or [{}])[0]
    conn.execute("INSERT OR REPLACE INTO judge_call_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        call_id, status, (context or {}).get("run_id"), log.get("started_at"), log.get("finished_at"),
        log.get("latency_ms"), log.get("attempts"), json.dumps(log.get("errors") or []), sha, blob,
        json.dumps(resp, ensure_ascii=False) if resp else None, resp.get("model"), resp.get("id"),
        resp.get("system_fingerprint"), choice.get("finish_reason"),
        ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")),
        ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")),
        json.dumps(context or {}, ensure_ascii=False, default=float), log.get("code")))


def load(conn, call_id: str) -> dict:
    r = conn.execute("SELECT * FROM judge_call_log WHERE call_id = ?", (call_id,)).fetchone()
    if r is None:
        return {}
    out = dict(r)
    blob = out.pop("request_z")
    out["request"] = unpack_request(blob) if blob is not None else None      # None: archived to the lake (A52)
    out["request_archived"] = blob is None
    out["response"] = json.loads(out.pop("response_json") or "null")
    out["context"] = json.loads(out["context"] or "{}")
    out["errors"] = json.loads(out["errors"] or "[]")
    return out


def coverage(conn, since: str, purpose: str = "measure") -> dict:
    """Paid calls since `since` and how many have a log row; failed calls logged in the same window."""
    paid = conn.execute("SELECT COUNT(*) FROM judge_calls WHERE purpose = ? AND model != 'simulated' AND at >= ?",
                        (purpose, since)).fetchone()[0]
    logged = conn.execute("""SELECT COUNT(*) FROM judge_calls c JOIN judge_call_log l USING(call_id)
                             WHERE c.purpose = ? AND c.at >= ?""", (purpose, since)).fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM judge_call_log WHERE status != 'ok' AND started_at >= ?",
                          (since,)).fetchone()[0]
    return {"paid_calls": paid, "logged": logged, "failed": failed}


def main():
    conn = store.connect()
    first = conn.execute("SELECT MIN(started_at) FROM judge_call_log").fetchone()[0]
    print("# Judge call log (A51)\n")
    if not first:
        print("No logged calls yet: the log starts with the next paid judge call (older calls have only the lean"
              " tables: judge_calls, judgments, api_usage).")
        return
    paid = conn.execute("SELECT COUNT(*) FROM judge_calls WHERE model != 'simulated' AND at >= ?", (first,)).fetchone()[0]
    logged = conn.execute("""SELECT COUNT(*) FROM judge_calls c JOIN judge_call_log l USING(call_id)
                             WHERE c.at >= ?""", (first,)).fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM judge_call_log WHERE status != 'ok'").fetchone()[0]
    print(f"- since {first}: {logged} of {paid} paid calls have a full log ({'✅' if logged == paid else '❌ gap'});"
          f" {failed} failed calls logged")
    bad = archived = 0
    for r in conn.execute("SELECT request_sha256, request_z FROM judge_call_log"):
        if r["request_z"] is None:
            archived += 1
            continue
        bad += hashlib.sha256(zlib.decompress(r["request_z"])).hexdigest() != r["request_sha256"]
    size = conn.execute("SELECT SUM(COALESCE(LENGTH(request_z), 0) + COALESCE(LENGTH(response_json), 0)"
                        " + LENGTH(context)) FROM judge_call_log").fetchone()[0] or 0
    n = conn.execute("SELECT COUNT(*) FROM judge_call_log").fetchone()[0]
    print(f"- integrity: {n - archived - bad} of {n - archived} stored requests match their hash {'✅' if not bad else '❌'}"
          f"{f' ({archived} moved to the lake, A52)' if archived else ''}"
          f" · storage {size / 1e6:.1f} MB ({size / max(n, 1) / 1e3:.1f} kB per call)")
    lat = [r[0] for r in conn.execute("SELECT latency_ms FROM judge_call_log WHERE latency_ms IS NOT NULL ORDER BY 1")]
    if lat:
        print(f"- latency: median {lat[len(lat) // 2] / 1e3:.1f} s, p95 {lat[int(len(lat) * 0.95)] / 1e3:.1f} s;"
              f" retries {conn.execute('SELECT SUM(attempts - 1) FROM judge_call_log').fetchone()[0] or 0}")
    for col in ("response_model", "system_fingerprint", "finish_reason"):
        vals = conn.execute(f"SELECT {col}, COUNT(*) FROM judge_call_log GROUP BY 1 ORDER BY 2 DESC LIMIT 5").fetchall()
        print(f"- {col}: " + ", ".join(f"{v[0]} ×{v[1]}" for v in vals))
    t = conn.execute("SELECT SUM(cached_tokens), SUM(reasoning_tokens) FROM judge_call_log").fetchone()
    print(f"- cached input tokens {t[0] or 0:,} · reasoning tokens {t[1] or 0:,}")
    ctx_keys = set()
    for r in conn.execute("SELECT context FROM judge_call_log ORDER BY started_at DESC LIMIT 50"):
        ctx_keys |= set(json.loads(r[0] or "{}"))
    print(f"- context fields: {', '.join(sorted(ctx_keys)) or '(none)'}")


if __name__ == "__main__":
    main()

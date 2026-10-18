"""Check the local judge on the gaming PC (A65): reachable, key enforced, answers, and how many pairs an hour it does.

    uv run python -m pipeline.history.llm_check [--n 256] [--parallel 16] [--candidate C2] [--db data/history/history.sqlite]

Reads LOCAL_LLM_BASE_URL and LOCAL_LLM_API_KEY from .env (never printed). The load test sends `--n` calls exactly as
the replay does — LocalJudge with HISTORY_JUDGE's settings (or an E9 candidate's), prompt v4, its schema —
`--parallel` at a time, on two texts shaped like the history's (headline + description; from `--db` when given, else
filler of their average length), and reports calls a second, tokens in and out per call, latency, and the pairs an
hour that implies. It measures speed only; whether the model judges well is E9.

The first version (2026-10-17, ≈ 10,000 an hour) sent gpt-5.4-mini's call shape: 1,000 tokens in, unique from the
first token (so nothing cached), and 174 forced out — but most of those 174 were gpt-5.4-mini's hidden reasoning. The
history's calls are ≈ 600 tokens in, of which the ≈ 400-token instructions are the same every time (vLLM caches
them), and with thinking off the v4 answer is ≈ 15 tokens: {"econ": "A", "social": "na"}.
"""
from __future__ import annotations

import argparse
import os
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from pipeline.settings import _dotenv

MODEL = "judge"                      # the served name (infra/pc/llm.env): the model behind it can change
TITLE, SUMMARY = 68, 174             # characters: the history's average headline and description (March 2019)

FILLER = ("The government announced a new budget on Tuesday, with higher spending on defence and lower taxes for "
          "small firms, while the opposition said cuts to public services would hit families. ")


def settings() -> tuple[str, str]:
    env = {**_dotenv(), **os.environ}
    url, key = env.get("LOCAL_LLM_BASE_URL"), env.get("LOCAL_LLM_API_KEY")
    if not url or not key:
        raise SystemExit("LOCAL_LLM_BASE_URL / LOCAL_LLM_API_KEY missing from .env (docs/LOCAL_LLM.md)")
    return url.rstrip("/"), key


def refuses_without_key(url: str) -> bool:
    try:
        urllib.request.urlopen(f"{url}/models", timeout=5)
    except urllib.error.HTTPError as e:
        return e.code == 401
    return False


def texts(n: int, db: str | None = None, seed: int = 0) -> list[tuple[str, str]]:
    """n pairs of article texts as the replay renders a summary: headline, blank line, description. From the history
    database when given (random articles; they stay on the maintainer's machines), else distinct filler of the
    history's average length — distinct so that only the shared instructions can be cached."""
    if db:
        import sqlite3
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = conn.execute("SELECT title, summary FROM articles WHERE summary != '' ORDER BY random() LIMIT ?",
                            (2 * n,)).fetchall()
        if len(rows) >= 2 * n:
            arts = [f"{t}\n\n{s}" for t, s in rows]
            return list(zip(arts[0::2], arts[1::2]))
    rng = __import__("random").Random(seed)
    body = FILLER * 3

    def one(i: int, side: str) -> str:
        k = rng.randrange(len(FILLER))
        return f"{side}{i}: {body[k:k + TITLE]}\n\n{body[k + TITLE:k + TITLE + SUMMARY]}"
    return [(one(i, "A"), one(i, "B")) for i in range(n)]


def one(judge, pair: tuple[str, str], direction: str) -> tuple[float, int, int]:
    t = time.monotonic()
    v = judge.compare(pair[0], pair[1], direction)
    return time.monotonic() - t, v.input_tokens, v.output_tokens


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=256)
    ap.add_argument("--parallel", type=int, default=16)
    ap.add_argument("--candidate", help="an E9 candidate's settings (C1, C2, C3) instead of HISTORY_JUDGE; the server "
                                        "must be running its model")
    ap.add_argument("--db", help="history database to draw real headline + description pairs from")
    args = ap.parse_args()
    from openai import OpenAI

    from pipeline.history.replay import HISTORY_JUDGE
    from pipeline.judge.client import LocalJudge
    url, key = settings()
    client = OpenAI(base_url=url, api_key=key, timeout=600)
    print(f"server            {url}")
    print(f"key enforced      {'yes' if refuses_without_key(url) else 'NO — anyone on the network can use it'}")
    models = [m.id for m in client.models.list().data]
    print(f"models            {models}")
    if args.candidate:
        from pipeline.experiments.e9 import CANDIDATES
        spec = {k: v for k, v in CANDIDATES[args.candidate].items() if k != "model"}
    else:
        spec = HISTORY_JUDGE
    judge = LocalJudge(client, **spec)
    pairs = texts(args.n + 1, args.db)
    lat, pin, pout = one(judge, pairs[0], "positive")              # also puts the instructions in the server's cache
    print(f"one call          {lat:.1f} s, {pin} tokens in, {pout} out")
    t0 = time.monotonic()
    with ThreadPoolExecutor(args.parallel) as pool:
        res = list(pool.map(lambda k: one(judge, pairs[k], "positive" if k % 2 else "negative"),
                            range(1, args.n + 1)))
    wall = time.monotonic() - t0
    lats = sorted(r[0] for r in res)
    print(f"load              {args.n} calls, {args.parallel} at a time, in {wall:.1f} s "
          f"({args.candidate or 'HISTORY_JUDGE'}: thinking {'on' if judge.thinking else 'off'})")
    print(f"per call          {statistics.mean(r[1] for r in res):.0f} tokens in, "
          f"{statistics.mean(r[2] for r in res):.0f} out")
    print(f"throughput        {args.n / wall:.2f} calls/s, {sum(r[2] for r in res) / wall:.0f} output tokens/s")
    print(f"latency           median {statistics.median(lats):.1f} s, p95 {lats[int(0.95 * (len(lats) - 1))]:.1f} s")
    print(f"≈ pairs an hour   {args.n / wall * 3600:,.0f} (replay-shaped calls) → LOCAL_PAIRS_PER_HOUR in "
          f"pipeline/history/replay.py")


if __name__ == "__main__":
    main()

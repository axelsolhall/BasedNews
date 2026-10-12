"""3.2 judge backends behind one interface (A45, A46):

    judge.compare(text_a, text_b, direction) -> Verdict(answers, input_tokens, output_tokens, cost_usd, raw)

OpenAIJudge   the real judge (structured output, retries, cost from the API's own token counts)
SimulatedJudge verdicts drawn from simulated true positions — the same maths as E3 — so the full 3.1 → 3.2 → 3.3 loop
               runs end to end, in tests and dry runs, without a key or any spend
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import numpy as np

from pipeline.ingest.feeds import utc_now_iso
from pipeline.judge import calllog
from pipeline.judge import prompt as P
from pipeline.settings import Settings, load_settings


def _code_version() -> str:
    try:
        from importlib.metadata import version
        sdk = f"openai-{version('openai')}"
    except Exception:
        sdk = "openai-?"
    return f"{calllog.code_fingerprint()} {sdk}"


@dataclass
class Verdict:
    answers: dict                    # {axis: {"verdict": A|B|same|na, "reason": str}}
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    raw: str = ""
    log: dict = field(default_factory=dict)   # A51: request, full response, timing, retries (pipeline/judge/calllog.py)


class JudgeCallFailed(RuntimeError):
    """All attempts failed. Carries the log so the failure is recorded too (A51)."""
    def __init__(self, message: str, log: dict):
        super().__init__(message)
        self.log = log


class OpenAIJudge:
    def __init__(self, settings: Settings | None = None, client=None, model: str | None = None):
        self.settings = settings or load_settings()
        if not self.settings.has_openai and client is None:
            raise RuntimeError("OPENAI_API_KEY is not set (.env) — needed for the OpenAI judge")
        from openai import OpenAI
        self.client = client or OpenAI(api_key=self.settings.openai_api_key)
        self.model = model or self.settings.judge_model
        self.reasoning = self.settings.judge_reasoning_effort or None

    def _request(self, msgs, version) -> dict:
        kw = dict(model=self.model, messages=msgs,
                  response_format={"type": "json_schema",
                                   "json_schema": {"name": "framing_verdicts", "strict": True, "schema": P.schema(version)}})
        if self.reasoning:
            kw["reasoning_effort"] = self.reasoning
        return kw

    def compare(self, text_a: str, text_b: str, direction: str = "positive", version: str = P.PROMPT_VERSION) -> Verdict:
        msgs = P.messages(text_a, text_b, direction, version)
        log = {"started_at": utc_now_iso(), "errors": [], "code": _code_version()}
        t0 = time.monotonic()
        for attempt in range(5):
            kw = self._request(msgs, version)
            log["request"], log["attempts"] = kw, attempt + 1      # the request actually sent last
            try:
                r = self.client.chat.completions.create(**kw)
                break
            except Exception as e:
                msg = str(e).lower()
                log["errors"].append({"attempt": attempt + 1, "at": utc_now_iso(), "type": type(e).__name__,
                                      "message": calllog.scrub(e)})
                if self.reasoning and "reasoning" in msg and ("unsupported" in msg or "not supported" in msg
                                                               or "unrecognized" in msg or "invalid" in msg):
                    self.reasoning = None            # model does not take reasoning_effort: drop it, retry at once
                    continue
                if attempt == 4 or "invalid_api_key" in msg or "incorrect api key" in msg:
                    log["finished_at"], log["latency_ms"] = utc_now_iso(), int((time.monotonic() - t0) * 1000)
                    raise JudgeCallFailed(calllog.scrub(e), log) from e
                time.sleep(2 ** attempt)             # rate limit / transient
        log["finished_at"], log["latency_ms"] = utc_now_iso(), int((time.monotonic() - t0) * 1000)
        log["response"] = calllog.to_jsonable(r)
        raw = r.choices[0].message.content or ""
        u = getattr(r, "usage", None)
        tin, tout = (u.prompt_tokens, u.completion_tokens) if u else (0, 0)
        pin, pout = self.settings.price(self.model)
        return Verdict(P.parse(raw), tin, tout, (tin * pin + tout * pout) / 1e6, self.model, raw, log)


@dataclass
class SimulatedJudge:
    """Bradley–Terry verdicts from simulated truth: P(A further +) = σ(a·(θ_A − θ_B) + β); abstains with 1 − relevance."""
    outlets: list[str]
    seed: int = 0
    beta: float = 0.3
    relevance: dict = field(default_factory=lambda: {"econ": 0.4, "social": 0.8})
    model: str = "simulated"

    def __post_init__(self):
        rng = np.random.default_rng(self.seed)
        self.truth = {ax: dict(zip(self.outlets, rng.standard_normal(len(self.outlets)))) for ax in P.AXES}
        self.rng = np.random.default_rng(self.seed + 1)

    def compare_outlets(self, outlet_a: str, outlet_b: str, a: float = 1.0, direction: str = "positive") -> Verdict:
        ans = {}
        for ax in P.AXES:
            if self.rng.random() >= self.relevance[ax]:
                ans[ax] = {"verdict": "na", "reason": "simulated"}
                continue
            d = self.truth[ax][outlet_a] - self.truth[ax][outlet_b] + self.rng.normal(0, 0.3 * np.sqrt(2))
            a_further = self.rng.random() < 1 / (1 + np.exp(-(a * d + self.beta)))
            v = "A" if a_further else "B"
            if direction == "negative":
                v = "B" if v == "A" else "A"
            ans[ax] = {"verdict": v, "reason": "simulated"}
        return Verdict(ans, 0, 0, 0.0, self.model, json.dumps(ans))

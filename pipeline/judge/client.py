"""3.2 judge backends behind one interface (A45, A46):

    judge.compare(text_a, text_b, direction) -> Verdict(answers, input_tokens, output_tokens, cost_usd, raw)

OpenAIJudge   the real judge (structured output, retries, cost from the API's own token counts)
LocalJudge    the same over an OpenAI-compatible local server (the gaming PC, A65): no spend, no OpenAI parameters
SimulatedJudge verdicts drawn from simulated true positions — the same maths as E3 — so the full 3.1 → 3.2 → 3.3 loop
               runs end to end, in tests and dry runs, without a key or any spend
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import numpy as np

from pipeline.ingest.feeds import utc_now_iso
from pipeline.judge import calllog
from pipeline.judge import prompt as P
from pipeline.settings import Settings, _dotenv, load_settings


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



def local_client(timeout: float = 600):
    """The local server from .env (LOCAL_LLM_BASE_URL, LOCAL_LLM_API_KEY; docs/LOCAL_LLM.md)."""
    from openai import OpenAI
    env = {**_dotenv(), **os.environ}
    if not env.get("LOCAL_LLM_BASE_URL") or not env.get("LOCAL_LLM_API_KEY"):
        raise SystemExit("LOCAL_LLM_BASE_URL / LOCAL_LLM_API_KEY missing from .env (docs/LOCAL_LLM.md)")
    return OpenAI(base_url=env["LOCAL_LLM_BASE_URL"], api_key=env["LOCAL_LLM_API_KEY"], timeout=timeout, max_retries=0)


def finish_reason(verdict) -> str | None:
    try:
        return verdict.log["response"]["choices"][0]["finish_reason"]
    except (KeyError, IndexError, TypeError):
        return None


class LocalJudge(OpenAIJudge):
    """OpenAIJudge against a local OpenAI-compatible server serving the model as "judge" (A65): its own sampling and
    the Qwen-style thinking switch; no reasoning_effort (an OpenAI parameter) and no max_tokens, so the server answers
    within whatever context the prompt leaves (E9 harness 2). A reply cut off at the token limit is JudgeCallFailed,
    never a verdict."""

    def __init__(self, client=None, thinking: bool = False, sampling: dict | None = None, settings: Settings | None = None,
                 served: str = "judge"):
        super().__init__(settings or load_settings(), client=client or local_client(), model=served)
        self.reasoning, self.thinking = None, thinking
        self.sampling = dict(sampling if sampling is not None else {"temperature": 0.0})

    def _request(self, msgs, version) -> dict:
        kw = super()._request(msgs, version)
        kw.update(self.sampling)
        kw["extra_body"] = {"chat_template_kwargs": {"enable_thinking": self.thinking}}
        return kw

    def compare(self, text_a: str, text_b: str, direction: str = "positive", version: str = P.PROMPT_VERSION) -> Verdict:
        v = super().compare(text_a, text_b, direction, version)
        if finish_reason(v) == "length":
            raise JudgeCallFailed("answer cut off at the token limit", {**v.log, "raw": v.raw})
        return v


@dataclass
class SimulatedJudge:
    """Bradley–Terry verdicts from simulated truth: P(A further +) = σ(a·(θ_A − θ_B + ε) + β), outlets θ ~ N(0, τ²),
    ε ~ N(0, 2σ_a²) the two articles' own lean; abstains with 1 − relevance. Per axis: tau, sigma_a, relevance (beta
    a number or per axis). LIKE_LIVE below holds live's estimates, for dry runs that should need what the real judge
    would."""
    outlets: list[str]
    seed: int = 0
    beta: float | dict = 0.3
    relevance: dict = field(default_factory=lambda: {"econ": 0.4, "social": 0.8})
    model: str = "simulated"
    tau: dict = field(default_factory=lambda: {"econ": 1.0, "social": 1.0})
    sigma_a: dict = field(default_factory=lambda: {"econ": 0.3, "social": 0.3})
    angles: dict = field(default_factory=dict)       # story → {axis: P(answer)}: story-dependent answers (A70 tests)

    def __post_init__(self):
        rng = np.random.default_rng(self.seed)
        self.truth = {ax: dict(zip(self.outlets, self.tau[ax] * rng.standard_normal(len(self.outlets)))) for ax in P.AXES}
        self.rng = np.random.default_rng(self.seed + 1)

    def compare_outlets(self, outlet_a: str, outlet_b: str, a: float = 1.0, direction: str = "positive",
                        story: str | None = None) -> Verdict:
        ans = {}
        for ax in P.AXES:
            if self.rng.random() >= self.angles.get(story, self.relevance)[ax]:
                ans[ax] = {"verdict": "na", "reason": "simulated"}
                continue
            d = self.truth[ax][outlet_a] - self.truth[ax][outlet_b] + self.rng.normal(0, self.sigma_a[ax] * np.sqrt(2))
            beta = self.beta[ax] if isinstance(self.beta, dict) else self.beta
            a_further = self.rng.random() < 1 / (1 + np.exp(-(a * d + beta)))
            v = "A" if a_further else "B"
            if direction == "negative":
                v = "B" if v == "A" else "A"
            ans[ax] = {"verdict": v, "reason": "simulated"}
        return Verdict(ans, 0, 0, 0.0, self.model, json.dumps(ans))


# live's v4 estimates (2026-10-18, data/prod_snapshot.sqlite: 693 gpt-5.4-mini calls, the evidence-averaged fit and the
# share of calls answered per axis): the economic angle is rare (≈ 1 call in 7) and articles vary a lot socially
LIKE_LIVE = {"tau": {"econ": 0.84, "social": 0.61}, "sigma_a": {"econ": 0.46, "social": 1.41},
             "relevance": {"econ": 0.14, "social": 0.50}, "beta": {"econ": -0.75, "social": -0.52}}

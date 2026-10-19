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


OPTIONS = ("A", "B", "na")


def choose(p: dict[str, float], cut: float) -> str:
    """A71: "na" iff P(na) ≥ cut, else the likelier of A and B."""
    return "na" if p["na"] >= cut else ("A" if p["A"] >= p["B"] else "B")


def decide(probs: dict, cut: dict[str, float]) -> dict[str, str]:
    """Econ first; social read after the econ answer chosen (ThresholdJudge.probabilities)."""
    econ = choose(probs["econ"], cut["econ"])
    return {"econ": econ, "social": choose(probs["social"][econ], cut["social"])}


class ThresholdJudge(LocalJudge):
    """The local judge read from its probabilities instead of its greedy answer (A71). At each verdict of the trained
    answer {"econ":…,"social":…} it takes the model's next-token probabilities of "A", "B" and "na" (renormalised over
    the three) and says "na" only when P(na) ≥ na_cut[axis], else the likelier of A and B. Econ is decided first; social
    is read after the econ answer chosen. One-token completions over the server's prefix cache: the prompt is
    prefilled once. Social is also read after the other two econ answers, so a different econ cut can be evaluated
    from the log without asking again."""

    def __init__(self, na_cut: dict[str, float], client=None, settings: Settings | None = None, served: str = "judge",
                 http=None):
        super().__init__(client=client, thinking=False, settings=settings, served=served)
        self.na_cut = dict(na_cut)
        self.http = http
        self._prefix: dict | None = None

    def _post(self, path: str, body: dict) -> dict:
        import httpx
        env = {**_dotenv(), **os.environ}
        root = env["LOCAL_LLM_BASE_URL"].rstrip("/").removesuffix("/v1")
        r = (self.http or httpx).post(f"{root}{path}", json=body, timeout=600,
                                      headers={"Authorization": f"Bearer {env['LOCAL_LLM_API_KEY']}"})
        r.raise_for_status()
        return r.json()

    def _tokens(self, text: str) -> list[int]:
        return self._post("/tokenize", {"model": self.model, "prompt": text, "add_special_tokens": False})["tokens"]

    def prefixes(self) -> dict:
        """Token ids up to each verdict: econ's, and social's after each econ answer (the shared part of the three
        answers' tokens, as infra/pc/train_lora.py slots them)."""
        if self._prefix is None:
            def upto(texts):
                seqs = [self._tokens(t) for t in texts]
                p = 0
                while len({s[p] for s in seqs}) == 1:
                    p += 1
                return seqs[0][:p]
            js = lambda e, s: json.dumps({"econ": e, "social": s}, separators=(",", ":"))   # noqa: E731
            self._prefix = {"econ": upto([js(o, "na") for o in OPTIONS]),
                            "social": {e: upto([js(e, o) for o in OPTIONS]) for e in OPTIONS}}
        return self._prefix

    def _next(self, ids: list[int]) -> dict[str, float]:
        r = self.client.completions.create(model=self.model, prompt=ids, max_tokens=1, logprobs=20, temperature=0.0)
        top = r.choices[0].logprobs.top_logprobs[0]
        p = {o: float(np.exp(top[o])) if o in top else 0.0 for o in OPTIONS}
        s = sum(p.values())
        return {o: v / s for o, v in p.items()} if s > 0 else {o: 1 / 3 for o in OPTIONS}

    def probabilities(self, msgs: list[dict]) -> tuple[dict, int]:
        """{"econ": {A, B, na}, "social": {econ answer: {A, B, na}}} and the prompt's token count."""
        prompt = self._post("/tokenize", {"model": self.model, "messages": msgs, "add_generation_prompt": True,
                                          "chat_template_kwargs": {"enable_thinking": False}})["tokens"]
        pre = self.prefixes()
        return ({"econ": self._next(prompt + pre["econ"]),
                 "social": {e: self._next(prompt + pre["social"][e]) for e in OPTIONS}}, len(prompt))

    def compare(self, text_a: str, text_b: str, direction: str = "positive", version: str = P.PROMPT_VERSION) -> Verdict:
        msgs = P.messages(text_a, text_b, direction, version)
        log = {"started_at": utc_now_iso(), "errors": [], "code": _code_version(), "attempts": 1,
               "request": {"model": self.model, "messages": msgs, "na_cut": self.na_cut, "mode": "threshold"}}
        t0 = time.monotonic()
        try:
            probs, n_in = self.probabilities(msgs)
        except Exception as e:
            log["errors"].append({"attempt": 1, "at": utc_now_iso(), "type": type(e).__name__, "message": calllog.scrub(e)})
            raise JudgeCallFailed(calllog.scrub(e), log) from e
        raw = json.dumps(decide(probs, self.na_cut), separators=(",", ":"))
        log["finished_at"], log["latency_ms"] = utc_now_iso(), int((time.monotonic() - t0) * 1000)
        log["response"] = {"model": self.model, "probabilities": probs, "choices": [{"finish_reason": "threshold"}],
                           "usage": {"prompt_tokens": n_in, "completion_tokens": 2}}
        return Verdict(P.parse(raw), n_in, 2, 0.0, self.model, raw, log)


SWAP = {"A": "B", "B": "A", "na": "na"}


def swapped(probs: dict) -> dict:
    """Probabilities read with the articles in the other order, in the first order's letters: A↔B on both axes, and
    social's condition (the econ answer before it) swapped too."""
    flip = lambda p: {SWAP[o]: v for o, v in p.items()}                       # noqa: E731
    return {"econ": flip(probs["econ"]), "social": {SWAP[e]: flip(p) for e, p in probs["social"].items()}}


def averaged(first: dict, second: dict) -> dict:
    """E13 (A72): the mean of the two orders' probabilities, the second already in the first order's letters."""
    mean = lambda a, b: {o: (a[o] + b[o]) / 2 for o in OPTIONS}               # noqa: E731
    return {"econ": mean(first["econ"], second["econ"]),
            "social": {e: mean(first["social"][e], second["social"][e]) for e in OPTIONS}}


def likeliest(probs: dict) -> dict[str, str]:
    """The greedy answer from probabilities: the likeliest option per axis, econ first."""
    top = lambda p: max(OPTIONS, key=lambda o: p[o])                         # noqa: E731
    econ = top(probs["econ"])
    return {"econ": econ, "social": top(probs["social"][econ])}


class SwapJudge(ThresholdJudge):
    """E13 (A72): every pair read in both orders (A/B as given and swapped), the probabilities averaged, the likeliest
    option per axis — order artefacts cancel, a real lean survives the swap (MT-Bench's swap check, as an average).
    Twice the prefill of one call; both orders' probabilities go to the call log."""

    def __init__(self, client=None, settings: Settings | None = None, served: str = "judge", http=None):
        super().__init__({"econ": 1.0, "social": 1.0}, client=client, settings=settings, served=served, http=http)

    def compare(self, text_a: str, text_b: str, direction: str = "positive", version: str = P.PROMPT_VERSION) -> Verdict:
        msgs, back = P.messages(text_a, text_b, direction, version), P.messages(text_b, text_a, direction, version)
        log = {"started_at": utc_now_iso(), "errors": [], "code": _code_version(), "attempts": 1,
               "request": {"model": self.model, "messages": msgs, "mode": "swap-average"}}
        t0 = time.monotonic()
        try:
            first, n_in = self.probabilities(msgs)
            second, _ = self.probabilities(back)
        except Exception as e:
            log["errors"].append({"attempt": 1, "at": utc_now_iso(), "type": type(e).__name__, "message": calllog.scrub(e)})
            raise JudgeCallFailed(calllog.scrub(e), log) from e
        second = swapped(second)
        raw = json.dumps(likeliest(averaged(first, second)), separators=(",", ":"))
        log["finished_at"], log["latency_ms"] = utc_now_iso(), int((time.monotonic() - t0) * 1000)
        log["response"] = {"model": self.model, "probabilities": first, "probabilities_swapped": second,
                           "choices": [{"finish_reason": "swap-average"}],
                           "usage": {"prompt_tokens": 2 * n_in, "completion_tokens": 4}}
        return Verdict(P.parse(raw), 2 * n_in, 4, 0.0, self.model, raw, log)


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

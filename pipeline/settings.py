"""Runtime settings from the environment and the untracked .env file. Never print or log the key.

    OPENAI_API_KEY              required for the OpenAI judge (3.2) and OpenAI embeddings (2.2)
    BASEDNEWS_JUDGE_MODEL       default gpt-5.4-mini (A11, A45)
    BASEDNEWS_EMBED_MODEL       default: the local MiniLM until the OpenAI profile is calibrated (A45, A47)
    BASEDNEWS_MONTHLY_AI_USD    hard cap across judge + embeddings, from the api_usage log (A48)
Prices are USD per 1 M tokens, checked 2026-10-11 — verify on https://openai.com/api/pricing before relying on them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MINILM = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
PRICES = {                                         # (input, output) USD per 1 M tokens
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.4": (2.50, 15.00),
    "openai:text-embedding-3-small": (0.02, 0.0),
    "openai:text-embedding-3-large": (0.13, 0.0),
}


def _dotenv(path: Path = Path(".env")) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


@dataclass(frozen=True)
class Settings:
    openai_api_key: str = field(default="", repr=False)
    judge_model: str = "gpt-5.4-mini"
    judge_reasoning_effort: str = "low"           # dropped automatically if the model rejects it
    embed_model: str = MINILM
    monthly_ai_usd: float = 12.0                  # ~$25/month total minus hosting (A16)

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)

    def price(self, model: str) -> tuple[float, float]:
        return PRICES.get(model, (0.0, 0.0))


def load_settings() -> Settings:
    env = {**_dotenv(), **os.environ}
    return Settings(
        openai_api_key=env.get("OPENAI_API_KEY", ""),
        judge_model=env.get("BASEDNEWS_JUDGE_MODEL", Settings.judge_model),
        judge_reasoning_effort=env.get("BASEDNEWS_JUDGE_REASONING", Settings.judge_reasoning_effort),
        embed_model=env.get("BASEDNEWS_EMBED_MODEL", Settings.embed_model),
        monthly_ai_usd=float(env.get("BASEDNEWS_MONTHLY_AI_USD", Settings.monthly_ai_usd)),
    )

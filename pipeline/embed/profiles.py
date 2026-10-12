"""Similarity thresholds per embedding model (A47). Every cut-off in 2.1 and 2.3 is a cosine similarity, and a cosine
from one model means something else in another — so thresholds live here, per model, not in the modules.

The MiniLM profile holds the values calibrated by hand on 2026-10-11. Other profiles are written by
`python -m pipeline.embed.calibrate --model …` into data/embedding_profiles.json (tracked), and a model without a
profile refuses to run 2.1/2.3 rather than silently using MiniLM's numbers.
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.settings import MINILM, load_settings

PROFILE_FILE = Path("data/embedding_profiles.json")
KEYS = ("story", "wall_max", "same_story", "same_summary", "same_title", "owns_text", "owns_margin")
MINILM_PROFILE = {
    "story": 0.60,          # 2.3 clustering threshold (P 0.92 / R 0.76 on 100 labelled pairs)
    "wall_max": 0.15,       # 2.1 headline↔opening below this + a wall phrase = wall (A38)
    "same_story": 0.50,     # 2.1 boilerplate guard: mean headline similarity of the articles sharing a paragraph (A40)
    "same_summary": 0.60,   # 2.1 renamed-copy link (A34)
    "same_title": 0.75,     # 2.1 renamed-copy link without summaries (A34)
    "owns_text": 0.50,      # 2.1 container: the entry that keeps the page text (A34)
    "owns_margin": 0.15,    # … and its lead over the runner-up (a difference, not a level: kept as-is across models)
}


def _all() -> dict[str, dict]:
    stored = json.loads(PROFILE_FILE.read_text()) if PROFILE_FILE.exists() else {}
    return {MINILM: {**MINILM_PROFILE, "source": "hand-calibrated 2026-10-11"}, **stored}


def profile(model: str | None = None) -> dict:
    model = model or load_settings().embed_model
    p = _all().get(model)
    if p is None:
        raise RuntimeError(f"no similarity profile for {model}: run `uv run python -m pipeline.embed.calibrate "
                           f"--model {model}` first (A47)")
    return p


def save_profile(model: str, values: dict) -> None:
    stored = json.loads(PROFILE_FILE.read_text()) if PROFILE_FILE.exists() else {}
    stored[model] = values
    PROFILE_FILE.write_text(json.dumps(stored, indent=1))

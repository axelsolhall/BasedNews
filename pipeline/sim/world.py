"""Synthetic media world: countries, outlets with true positions, events, and a noisy biased judge.

Truth is on the model's logit scale, so recovered means are directly comparable to truth.
Article-level noise (article_sd > 0) deliberately violates A03 a little, as real articles will.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pipeline.plan.planner import Candidate


@dataclass
class WorldConfig:
    n_countries: int = 8
    outlets_per_country: int = 5
    country_sd: float = 0.6          # spread of country means
    within_sd: float = 0.7           # spread of outlets inside a country
    judge_bias: float = 0.3          # true first-position bias beta (A04)
    article_sd: float = 0.3          # article deviation from its outlet (A03 violation)
    p_international: float = 0.3     # share of events covered across countries (A07)
    coverage: float = 0.6            # chance an eligible outlet covers an event
    seed: int = 0


@dataclass
class World:
    cfg: WorldConfig
    outlets: list[str]
    country_of: dict[str, str]
    theta: dict[str, float]
    countries: dict[str, list[str]] = field(default_factory=dict)
    rng: np.random.Generator = None
    _event_counter: int = 0

    @classmethod
    def make(cls, cfg: WorldConfig | None = None) -> "World":
        cfg = cfg or WorldConfig()
        rng = np.random.default_rng(cfg.seed)
        outlets, country_of, theta, countries = [], {}, {}, {}
        for c in range(cfg.n_countries):
            cname = f"C{c}"
            cmean = rng.normal(0, cfg.country_sd)
            countries[cname] = []
            for k in range(cfg.outlets_per_country):
                o = f"{cname}_o{k}"
                outlets.append(o)
                country_of[o] = cname
                theta[o] = cmean + rng.normal(0, cfg.within_sd)
                countries[cname].append(o)
        return cls(cfg, outlets, country_of, theta, countries, rng)

    # ---- events -> candidate pairs (A02: only same-event pairs are comparable) ---
    def new_event(self) -> list[Candidate]:
        self._event_counter += 1
        eid = f"e{self._event_counter}"
        if self.rng.random() < self.cfg.p_international:
            k = self.rng.integers(2, self.cfg.n_countries + 1)
            eligible = [o for c in self.rng.choice(list(self.countries), size=k, replace=False) for o in self.countries[c]]
        else:
            eligible = list(self.countries[self.rng.choice(list(self.countries))])
        covering = [o for o in eligible if self.rng.random() < self.cfg.coverage]
        return [
            Candidate(eid, a, b, f"{eid}:{a}", f"{eid}:{b}")
            for i, a in enumerate(covering) for b in covering[i + 1:]
        ]

    # ---- the judge ------------------------------------------------------------------
    def judge(self, first: str, second: str) -> bool:
        a = self.theta[first] + self.rng.normal(0, self.cfg.article_sd)
        b = self.theta[second] + self.rng.normal(0, self.cfg.article_sd)
        p = 1.0 / (1.0 + np.exp(-(a - b + self.cfg.judge_bias)))
        return bool(self.rng.random() < p)

    def country_weights(self) -> dict[str, dict[str, float]]:
        return {c: {o: 1.0 for o in os} for c, os in self.countries.items()}   # A08: equal weights (MVP)

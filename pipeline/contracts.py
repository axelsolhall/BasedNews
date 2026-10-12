"""Data contracts between pipeline modules (PLAN.md §2).

Change these deliberately: every module downstream depends on them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# A01: two axes. Adding an axis = add it here + a prompt in M7.
Axis = Literal["econ", "social"]
AXES: tuple[Axis, ...] = ("econ", "social")


# ---- M1 sources --------------------------------------------------------------
@dataclass(frozen=True)
class Outlet:
    id: str
    name: str
    country: str
    language: str
    homepage: str
    feeds: tuple[str, ...]
    type: str = "private"          # public | private
    weight: float = 1.0            # A08: audience weight for country aggregation (equal in MVP)
    tier: str = "reference"        # A29: "reference" (DNR 2026 top 5, defines origin + country position) | "extra"
    dnr_reach_pct: float | None = None


@dataclass(frozen=True)
class FeedHealth:
    """M1 output: is this outlet usable, and may we process its article text? (A10)"""
    outlet_id: str
    checked_at: str
    feeds_ok: int
    feeds_total: int
    items_seen: int
    newest_item_age_h: float | None
    robots_allows_articles: bool | None     # None = could not determine
    tdm_reserved: bool | None               # True = rightsholder opted out of text & data mining
    full_text_allowed: bool                 # derived policy: robots allows AND not TDM-reserved (A10)
    errors: tuple[str, ...] = ()


# ---- M2 ingest -----------------------------------------------------------------
@dataclass(frozen=True)
class Article:
    id: str                        # sha256(url|title)
    outlet_id: str
    country: str
    url: str
    title: str
    published: str | None          # ISO-8601 UTC when parseable, else raw string
    summary: str | None
    full_text: str | None          # internal only — never served (A10)
    feed_url: str
    ingested_at: str
    text_source: str = "none"      # jsonld | trafilatura | summary | none


@dataclass(frozen=True)
class HeadlineVersion:
    """One headline an article has carried (A34). An article has 1..n versions; the latest is what readers see now."""
    article_id: str                # the canonical article
    title: str
    first_seen: str                # ISO-8601 UTC: publication time of the copy, or when M2 saw the edit in the feed
    url: str
    how: str = "original"          # original | feed_edit (same URL, new title) | merged_copy (new URL, same text; M3)


# ---- M6 planner -> M7 judge -------------------------------------------------
@dataclass(frozen=True)
class ComparisonRequest:
    """Ask the judge: is the article from `first` further to the positive end of `axis` than `second`'s?"""
    event_id: str
    axis: Axis
    first_outlet: str          # shown first to the judge (order is randomised by the planner, A04)
    second_outlet: str
    first_article_id: str = ""
    second_article_id: str = ""
    expected_info_gain: float = 0.0


# ---- M7 judge -> M8 rate ----------------------------------------------------
@dataclass(frozen=True)
class Judgment:
    """Outcome of one comparison. `first_wins`=True means `first_outlet` was judged further toward
    the positive end of the axis (econ: right, social: conservative). None = abstained (A06)."""
    request: ComparisonRequest
    first_wins: bool | None
    reason: str = ""
    model: str = ""
    cost_usd: float = 0.0
    event_time: str = ""           # A22: when the event happened (UTC ISO) — needed for trends over time
    judged_at: str = ""            # when the LLM judged it (UTC ISO) — lets us re-fit any past window


# ---- M8 rate -> M9 aggregate / M6 planner -----------------------------------
@dataclass(frozen=True)
class PositionEstimate:
    outlet: str
    axis: Axis
    mean: float
    sd: float
    n_comparisons: int = 0


# ---- M9 aggregate -> API / frontend: one dot + oval on the 2-D plane ------------
@dataclass(frozen=True)
class PlanePoint:
    """x = econ position (− left, + right); y = social position (− liberal, + conservative).
    Oval radii = sd_x * k, sd_y * k with k = OVAL_SD_MULTIPLIER (A25). Axis-aligned is exact: axes are fitted separately (A01)."""
    id: str
    label: str
    kind: str                      # "country" | "outlet"
    x: float
    y: float
    sd_x: float
    sd_y: float
    parent: str = ""               # country id for outlets
    n_outlets: int = 0


@dataclass
class AxisPosterior:
    """Full Laplace posterior for one axis: needed by the planner (covariances matter for information gain)."""
    axis: Axis
    outlets: list[str]
    mean: "object"             # np.ndarray, shape (n+1,): outlet positions + position-bias beta (last)
    cov: "object"              # np.ndarray, shape (n+1, n+1)
    n_obs: int = 0
    meta: dict = field(default_factory=dict)

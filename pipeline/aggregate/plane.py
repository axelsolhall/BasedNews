"""M9 — positions on the 2-D plane.

Outlet point: (mean_econ, mean_social) with ovals (sd_econ, sd_social).
Country point: audience-weighted mean of its outlets per axis (A08); its sd uses the full posterior covariance
(group_sd), i.e. the uncertainty of the country *average* — not the spread of its outlets (A25).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from pipeline.contracts import AxisPosterior, PlanePoint

OVAL_SD_MULTIPLIER = 1.0   # A25: radii = 1 sd (≈39 % 2-D coverage). 2.45 → ≈95 %.


def _contrast_stats(post: AxisPosterior, weights: dict[str, float]) -> tuple[float, float]:
    """Mean and sd of (weighted group mean − mean of ALL outlets). A26: the plane's origin is the average outlet,
    because Bradley–Terry only identifies differences. Using the contrast vector keeps the sd correct."""
    n = len(post.outlets)
    v = np.zeros(n + 1)
    v[:n] = -1.0 / n
    total = sum(weights.values())
    for o, w in weights.items():
        v[post.outlets.index(o)] += w / total
    return float(v @ post.mean), float(np.sqrt(v @ post.cov @ v))


def outlet_points(econ: AxisPosterior, social: AxisPosterior, labels: dict[str, str],
                  country_of: dict[str, str]) -> list[PlanePoint]:
    assert econ.outlets == social.outlets, "both axes must be fitted over the same outlet list"
    pts = []
    for o in econ.outlets:
        x, sx = _contrast_stats(econ, {o: 1.0})
        y, sy = _contrast_stats(social, {o: 1.0})
        pts.append(PlanePoint(o, labels.get(o, o), "outlet", x, y, sx, sy, parent=country_of.get(o, "")))
    return pts


def country_points(econ: AxisPosterior, social: AxisPosterior,
                   groups: dict[str, dict[str, float]]) -> list[PlanePoint]:
    pts = []
    for c, weights in groups.items():
        x, sx = _contrast_stats(econ, weights)
        y, sy = _contrast_stats(social, weights)
        pts.append(PlanePoint(c, c, "country", x, y, sx, sy, n_outlets=len(weights)))
    return pts


def export_json(path: Path | str, world: list[PlanePoint], outlets: list[PlanePoint], meta: dict | None = None) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({
        "meta": {"oval_sd_multiplier": OVAL_SD_MULTIPLIER, **(meta or {})},
        "countries": [asdict(p) for p in world],
        "outlets": [asdict(p) for p in outlets],
    }, indent=2))

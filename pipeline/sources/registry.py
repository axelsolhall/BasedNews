"""Load and validate data/outlets.json (v1 and v2 formats)."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.contracts import Outlet

DEFAULT_REGISTRY = Path("data/outlets.json")


def load_outlets(path: Path | str = DEFAULT_REGISTRY) -> list[Outlet]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    outlets: list[Outlet] = []
    for country, entry in data["countries"].items():
        if isinstance(entry, list):                          # v1: country -> [outlets]
            items, language = entry, "und"
        else:                                                # v2: country -> {iso, language, outlets}
            items, language = entry["outlets"], entry.get("language", "und")
        for o in items:
            outlets.append(Outlet(
                id=o["id"], name=o["name"], country=country, language=language,
                homepage=o.get("homepage", ""), feeds=tuple(o.get("feeds") or ()),
                type=o.get("type", "private"), weight=float(o.get("weight", 1.0)),
                tier=o.get("tier", "reference"), dnr_reach_pct=o.get("dnr_2026_online_reach_pct"),
            ))
    validate(outlets)
    return outlets


def validate(outlets: list[Outlet]) -> None:
    ids = [o.id for o in outlets]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate outlet ids: {sorted(dupes)}")
    for o in outlets:
        if o.tier not in ("reference", "extra"):
            raise ValueError(f"{o.id}: tier must be reference|extra (A29)")
        if o.weight <= 0:
            raise ValueError(f"{o.id}: weight must be > 0 (A08)")


def reference_groups(outlets: list[Outlet]) -> dict[str, dict[str, float]]:
    """Country -> {reference outlet: weight}. Only the reference tier defines country positions and the origin (A29, A26)."""
    groups: dict[str, dict[str, float]] = {}
    for o in outlets:
        if o.tier == "reference":
            groups.setdefault(o.country, {})[o.id] = o.weight
    return groups

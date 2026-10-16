"""5.1 The job steps: one per module, run by `python -m pipeline.job` (ADR-010).

Each step calls the module's own `main()` with fixed arguments, so a step does exactly what the module's CLI does.
Jobs are the sequences the scheduler runs; Step Functions runs the same steps one container each.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pipeline.job import gates


@dataclass(frozen=True)
class Step:
    name: str
    module: str                                   # a module with main(), as `python -m <module>`
    args: tuple[str, ...] = ()
    gate: Callable | None = None                  # gates.<name>(conn, ctx) -> Gate
    paid: bool = False                            # spends AI money: refuses to run without an explicit budget (A48)
    publishes: bool = False                       # writes files for the site (uploaded only if A54 allows)
    needs_ml: bool = False                        # embeds: needs the local model only when MiniLM is configured (A56)


RECENT_DAYS = "14"              # normalize and embed read only this window: stories (7 days) and the judge read inside it

STEPS: dict[str, Step] = {s.name: s for s in (
    Step("sources", "pipeline.sources.run", gate=gates.sources),
    Step("ingest", "pipeline.ingest.run", gate=gates.ingest),
    Step("normalize", "pipeline.normalize.run", ("--days", RECENT_DAYS), gate=gates.normalize, needs_ml=True),
    Step("embed", "pipeline.embed.run", ("--days", RECENT_DAYS), gate=gates.embed, needs_ml=True),
    Step("stories", "pipeline.events.run", ("--days", "7"), gate=gates.stories),
    Step("measure", "pipeline.measure.run", gate=gates.measure, paid=True),
    Step("export", "pipeline.export.run", gate=gates.export, publishes=True),
    Step("retention", "pipeline.retention.run", gate=gates.retention),
    Step("backfill", "pipeline.backfill.run"),
)}

JOBS: dict[str, tuple[str, ...]] = {
    "refresh": ("ingest",),                                    # hourly: feeds keep only their latest items
    "process": ("normalize", "embed", "stories"),              # daily, before the judge: only the judge reads stories (A63)
    "feeds": ("sources",),                                     # daily: feed health once a day is enough
    "judge": ("measure", "export"),                            # daily, new pairs as they appear (A58); paid: carries a budget
    "maintenance": ("retention",),                             # daily, after the judge job
}

"""What the M7 judge reads for one article: headline history + body (A36).

Headline edits are framing evidence ("Protesters clash with police" → "Police attack peaceful protesters"), and two
outlets may be compared at different stages of the same story, so the judge sees the latest headline plus the earlier
versions with the time each first appeared. Capped so a heavily edited article cannot blow up the token cost; E3's
cost model charges for exactly this text.
"""
from __future__ import annotations

from pipeline.contracts import HeadlineVersion
from pipeline.judge.blind import mask_outlet

HEADLINE_POLICY = "history"     # A36: "history" | "latest" | "first"
MAX_VERSIONS = 6                # first + the latest five


def _when(ts: str | None) -> str:
    return f"{ts[:10]} {ts[11:16]} UTC" if ts and len(ts) >= 16 and ts[4] == "-" else "time unknown"


def render_article(versions: list[HeadlineVersion], body: str, policy: str = HEADLINE_POLICY,
                   max_versions: int = MAX_VERSIONS, outlet_id: str | None = None) -> str:
    """versions oldest first, as store.headline_history returns them. With outlet_id, the outlet's references to
    itself are masked (A42) — always pass it when building real judge input."""
    if outlet_id:
        body = mask_outlet(body, outlet_id)[0] or ""
        versions = [HeadlineVersion(v.article_id, mask_outlet(v.title, outlet_id)[0], v.first_seen, v.url, v.how)
                    for v in versions]
    if not versions:
        return body.strip()
    if policy == "latest" or len(versions) == 1:
        head = f"Headline: {versions[-1].title}"
    elif policy == "first":
        head = f"Headline: {versions[0].title}"
    elif policy == "history":
        earlier = versions[:-1]
        omitted = 0
        if len(versions) > max_versions:
            omitted = len(versions) - max_versions
            earlier = [versions[0]] + versions[-max_versions:-1]
        lines = [f'  "{v.title}" (from {_when(v.first_seen)})' for v in earlier]
        if omitted:
            lines.insert(1, f"  … {omitted} more versions …")
        head = (f"Headline: {versions[-1].title} (since {_when(versions[-1].first_seen)})\n"
                f"Earlier headlines, oldest first:\n" + "\n".join(lines))
    else:
        raise ValueError(f"unknown headline policy {policy!r}")
    return f"{head}\n\n{body.strip()}".strip()

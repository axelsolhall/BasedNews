"""RSS/Atom parsing and date normalisation (moved from scripts/ingest_feeds.py)."""
from __future__ import annotations

import re

import datetime as dt
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

ATOM = {"atom": "http://www.w3.org/2005/Atom"}
DC_DATE = "{http://purl.org/dc/elements/1.1/}date"


def _text(el) -> str | None:
    if el is None or el.text is None:
        return None
    return el.text.strip() or None


def parse_feed(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if root.tag.lower().endswith("rss") or channel is not None:
        return [
            {"title": _text(i.find("title")), "url": _text(i.find("link")) or _text(i.find("guid")),
             "published": _text(i.find("pubDate")) or _text(i.find(DC_DATE)), "summary": _text(i.find("description"))}
            for i in (channel.findall("item") if channel is not None else [])
        ]
    if root.tag.lower().endswith("feed"):
        out = []
        for e in root.findall("atom:entry", ATOM):
            link = e.find("atom:link", ATOM)
            out.append({"title": _text(e.find("atom:title", ATOM)),
                        "url": link.attrib.get("href") if link is not None else None,
                        "published": _text(e.find("atom:published", ATOM)) or _text(e.find("atom:updated", ATOM)),
                        "summary": _text(e.find("atom:summary", ATOM))})
        return out
    return []


# RFC-822 parsers know only US zone names; European feeds (Bild) write "CEST", which was silently read as UTC.
TZ_ABBREVIATIONS = {"CEST": "+0200", "CET": "+0100", "MESZ": "+0200", "MEZ": "+0100", "BST": "+0100",
                    "WEST": "+0100", "WET": "+0000", "EEST": "+0300", "EET": "+0200", "IST": "+0100"}
_TZ_ABBR = re.compile(r"\s(" + "|".join(TZ_ABBREVIATIONS) + r")\s*$")


def to_utc_iso(value: str | None) -> str | None:
    """RFC-822 (RSS) or ISO-8601 (Atom) -> 'YYYY-MM-DDTHH:MM:SSZ'. Returns None if unparseable."""
    if not value:
        return None
    value = _TZ_ABBR.sub(lambda m: " " + TZ_ABBREVIATIONS[m.group(1)], value.strip())
    for parse in (parsedate_to_datetime, lambda v: dt.datetime.fromisoformat(v.replace("Z", "+00:00"))):
        try:
            d = parse(value)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.UTC)
            return d.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError, IndexError):
            continue
    return None


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

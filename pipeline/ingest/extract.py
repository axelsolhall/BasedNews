"""Article text extraction (moved from scripts/ingest_feeds.py). Internal use only (A10)."""
from __future__ import annotations

import html
import json
import re

import trafilatura

from pipeline import net


_BLOCK = re.compile(r"<(script|style|iframe|noscript)\b.*?</\1\s*>|<!--.*?-->|<!\[endif\]-->", re.I | re.S)
_BREAK = re.compile(r"<\s*(br|/p|/div|/li|/h[1-6])\s*/?\s*>", re.I)
_TAG = re.compile(r"</?[a-zA-Z][^<>]*>")          # a real tag starts with a letter: "3 < 5 > 2" survives
_ENTITY = re.compile(r"&(#\d+;?|#x[0-9a-fA-F]+;?|[a-zA-Z]+;)")    # numeric refs may lack the ';' (taz icons)
_PRIVATE_USE = re.compile("[\ue000-\uf8ff]")                       # icon-font glyphs, not text
_CMS_TOKEN = re.compile(r"\[\[ge:[^\]]*\]\]")                        # Libero/Kolumbus embed markers


def strip_html(value: str) -> str:
    """Feed summaries and JSON-LD bodies often carry HTML (<p>, <img>, iframes) and entities (&nbsp;, &#8220;)."""
    v = _BREAK.sub("\n", _BLOCK.sub(" ", value))
    v = _TAG.sub(" ", v)
    for _ in range(2):                              # some feeds double-encode: &amp;nbsp;
        if not _ENTITY.search(v):
            break
        v = html.unescape(v)
    return _CMS_TOKEN.sub("", _PRIVATE_USE.sub("", v)).replace("\xa0", " ")


# Paragraphs that open with one of these are site notices, not article text (found in the 2026-10-11 verification).
# Exact openers only: generic words like "consent" also appear in real news (e.g. Sweden's consent law).
BOILERPLATE_OPENERS = (
    "um externe inhalte anzuzeigen",                 # FAZ embed-consent notice
    "vi använder cookies",                           # DN
    "wir verwenden cookies", "we use cookies",
    "en poursuivant votre navigation",               # BFM TV
    "se hai scelto di non accettare i cookie",       # ANSA
    "nettavisen vil gjerne vite hva du mener",       # Nettavisen comment-section blurb
    "ti invitiamo a leggere le condizioni generali",  # ANSA cookie-wall follow-up
    "se è una notizia,", "raccogliamo, pubblichiamo e distribuiamo",   # ANSA footer
)
# A text containing one of these is a paywall or dead-link page: what we got is a teaser plus links to other
# articles, not the article. Such a text is dropped (the item keeps its summary, like any paywalled outlet, A21).
PAYWALL_MARKERS = (
    "zugang zu allen faz+",                          # FAZ+: free first paragraph, offer, then other articles' teasers
    "sie können den artikel leider nicht mehr aufrufen",   # expired Spiegel gift link
)


def is_paywalled(text: str | None) -> bool:
    low = (text or "").lower()
    return any(m in low for m in PAYWALL_MARKERS)


def drop_boilerplate(text: str) -> str:
    return "\n".join(p for p in text.split("\n") if not p.strip().lower().startswith(BOILERPLATE_OPENERS))


def clean_text(value: str | None) -> str | None:
    if not value:
        return None
    text = strip_html(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", drop_boilerplate(text)).strip()
    return text or None


def looks_like_markup(text: str | None) -> bool:
    if not text:
        return True
    s = text.lstrip()
    return (s.startswith("{") and '"@context"' in text) or ("<" in text and ">" in text) or ("css-" in text and "{" in text)


def jsonld_body(html: str) -> str | None:
    texts = []
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and isinstance(node.get("articleBody"), str):
                texts.append(node["articleBody"])
    return clean_text("\n\n".join(texts)) if texts else None


def fetch_text(url: str) -> tuple[str | None, str]:
    """Returns (text, source) with source in {jsonld, trafilatura, none}."""
    try:
        body, _ = net.fetch(url, timeout=20)
    except Exception:
        return None, "none"
    return text_from_html(body.decode("utf-8", errors="ignore"))


def text_from_html(html: str) -> tuple[str | None, str]:
    if is_paywalled(html):
        return None, "none"
    t = jsonld_body(html)
    if t and not looks_like_markup(t):
        return t, "jsonld"
    t = clean_text(trafilatura.extract(html, output_format="txt", include_comments=False,
                                       include_tables=False, favor_precision=True))
    if t and not looks_like_markup(t):
        return t, "trafilatura"
    return None, "none"

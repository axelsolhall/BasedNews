"""M3 rules for items that are not one article (A38). Pure functions, unit-tested; run.py applies them.

rundown  a multi-topic broadcast or newsletter page ("LCI Live du samedi 10 octobre 2026", "10/10: CBS Weekend News")
         — never one story, so 2.3 must not match it to one. Single-topic live blogs ("Liveblog Ukrainekrieg") are fine.
page     a site page, not news: cookie/privacy policy, user agreement, contest terms, section hubs, job boards
headline_only  neither text nor summary (VG video clips, blocked outlets without summaries): nothing to judge
wall     stored text that is a cookie/consent/paywall/comment notice, not the article: the headline does not match the
         opening AND the opening contains a wall phrase. Both conditions, so a real article that mentions cookies stays.
"""
from __future__ import annotations

import re

_WEEKDAY_DATE = re.compile(
    r"\b(du|de|vom|del|fra|från)\s+(lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|montag|dienstag|mittwoch|"
    r"donnerstag|freitag|samstag|sonntag|lunes|martes|miércoles|jueves|viernes|sábado|domingo|lunedì|martedì|"
    r"mercoledì|giovedì|venerdì|sabato|domenica)\s+\d{1,2}\.?\s+\w+\s+\d{4}\s*$", re.I)
_SHOW_DATE = re.compile(r"^\d{1,2}/\d{1,2}:\s.*\b(news|newscast|report|briefing)\b", re.I)
_RUNDOWN_SUMMARY = ("au sommaire", "in dieser sendung", "i dagens sending", "i kvällens sändning", "in this episode")

_PAGE_TITLE = re.compile(
    r"^(om\s+)?cookies?\b|\bcookies?\s+(på|on|policy)\b|personuppgiftspolicy|personvernerklæring|^personvern\s*$|"
    r"datenschutzerklärung|datenschutzhinweise|^datenschutz\s*$|privacy\s+(policy|notice)|brukeravtale|användarvillkor|nutzungsbedingungen|"
    r"allgemeine\s+geschäftsbedingungen|terms\s+(of\s+use|of\s+service|and\s+conditions)|^impressum\b|"
    r"nutzungsbasierte\s+online.?werbung", re.I)

_HUB_TITLE = re.compile(r"aktuelle news|news aus der region|le ultime notizie|stellenangebote|stellenmarkt|husregler|"
                        r"zu ihrer startseite|nyheter från dagen", re.I)
_HUB_URL = re.compile(r"startseite|/home-\d+|publishingblock-", re.I)

WALL_MAX_SIM = 0.15        # headline ↔ opening similarity below this is a candidate (bottom ~1 %, verified 2026-10-11)
WALL_PHRASES = (           # each seen in a real wall during verification, or its direct equivalent in another language
    "cookie", "consentless", "zustimmung", "einwilligung", "samtycke", "samtykke", "consentement", "consenso",
    "consentimiento", "en poursuivant votre navigation", "nicht mehr aufrufen", "kommentarfelt", "kommentarsfält",
    "enable javascript", "javascript is disabled", "aktivieren sie javascript",
)
WALL_LEAD_CHARS = 400


def is_rundown(title: str | None, summary: str | None) -> bool:
    t, s = (title or "").strip(), (summary or "").strip().lower()
    return bool(_WEEKDAY_DATE.search(t) or _SHOW_DATE.search(t) or s.startswith(_RUNDOWN_SUMMARY))


def has_wall_phrase(text: str | None) -> bool:
    lead = (text or "")[:WALL_LEAD_CHARS].lower()
    return any(p in lead for p in WALL_PHRASES)


def is_wall(text: str | None, headline_sim: float, max_sim: float = WALL_MAX_SIM) -> bool:
    return headline_sim < max_sim and has_wall_phrase(text)


def is_page(title: str | None) -> bool:
    return bool(_PAGE_TITLE.search((title or "").strip()))


def is_hub(title: str | None, url: str | None) -> bool:
    return bool(_HUB_TITLE.search(title or "") or _HUB_URL.search(url or ""))


def kind_of(title: str | None, summary: str | None, url: str | None = None, full_text: str | None = None) -> str | None:
    """None = a normal article (the default for every row)."""
    if is_page(title) or is_hub(title, url):
        return "page"
    if is_rundown(title, summary):
        return "rundown"
    if not (full_text or "").strip() and not (summary or "").strip():
        return "headline_only"
    return None


# ---- title site suffixes (A42) -------------------------------------------------------------------------------------
_SEP = re.compile(r"\s+(?:\||-|–|—|:)\s+")


def strip_site_suffix(title: str | None, site_names: list[str]) -> str | None:
    """'Trump … | SVT Nyheter' → 'Trump …'; '… | Kultur | Aftonbladet' → '…'. The final segment must BE the site
    (its name or domain, optionally + a short tail like '.no' or ' Nyheter'); one-word section labels right before it
    ('Kultur', 'Sport') go too. Anything else — 'Bayern verliert - Neuer patzt' — is headline text and stays."""
    if not title:
        return title
    t = title.strip()
    seps = list(_SEP.finditer(t))
    names = [n.lower() for n in site_names if n]

    def is_site(seg: str) -> bool:
        s_ = seg.strip().lower()
        return any(s_ == n or (s_.startswith(n) and len(s_) <= len(n) + 12) for n in names)

    cut = None
    for k in range(len(seps) - 1, -1, -1):
        seg = t[seps[k].end():(seps[k + 1].start() if k + 1 < len(seps) else len(t))]
        if cut is None:
            if not is_site(seg):
                return title
            cut = seps[k].start()
        elif is_site(seg) or (len(seg.split()) == 1 and seg[:1].isupper()):
            cut = seps[k].start()
        else:
            break
    kept = t[:cut].rstrip() if cut else t
    return kept if len(kept.split()) >= 2 else title


# ---- genre (A43) ---------------------------------------------------------------------------------------------------
_OPINION_URL = re.compile(r"/(debatt|debatten|kommentar|kommentarer|kommentare|ledare|leder|ledarsidan|opinion|opinions|"
                          r"opinioni|meinung|editorial|editoriales|editoriali|commentisfree|tribune|tribunes|idees|columna|"
                          r"columnas|columnists|commenti|blogs?|kronikk|kronikker|kolumne|kolumnit|mielipide|"
                          r"paakirjoitukset|lettere-e-idee)/", re.I)
_OPINION_TITLE = re.compile(r"^(kommentar|kommentaar|ledare|leder|debatt|analys|analyse|meinung|opinion|editorial|"
                            r"kolumne|kolumni|krönika|kronikk|tribune|opinión|editoriale|commento)\s*[:|–-]", re.I)


def genre_of(title: str | None, url: str | None) -> str:
    """'opinion' (columns, editorials, debate, analysis pieces) or 'news'."""
    return "opinion" if _OPINION_URL.search(url or "") or _OPINION_TITLE.search(title or "") else "news"

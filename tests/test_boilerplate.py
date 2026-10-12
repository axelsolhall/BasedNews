"""A40: boilerplate = repeated across an outlet's articles, at the start/end, across different stories."""
from pipeline.normalize.boilerplate import find_boilerplate, strip_boilerplate

FOOTER = ["Vet du mer? Har du ett nyhetstips? Skicka sms 71000", "Aftonbladet garanterar alla tipsare källskydd enligt lag"]
QUOTE = "– Jeg var ved gate 403. Jeg så 401 blive ramt, alle løb"


def doc(i, body, outlet="o"):
    return (f"a{i}", outlet, "\n".join(body))


def different(ids):        # articles about different stories
    return 0.1


def same(ids):             # follow-ups of one story
    return 0.8


def test_repeated_footer_block_at_the_end_is_boilerplate():
    docs = [doc(i, [f"Unique story {i} first paragraph here", f"Unique story {i} second paragraph", *FOOTER]) for i in range(4)]
    found = find_boilerplate(docs, different)
    assert {p for _, p in found} == {f.lower() for f in FOOTER}


def test_repeated_quote_in_the_body_is_kept():
    docs = [doc(i, [f"Opening of follow-up {i} on the attack", QUOTE, f"Closing paragraph number {i} here"]) for i in range(3)]
    assert find_boilerplate(docs, different) == {}


def test_same_story_opening_reused_in_follow_ups_is_kept():
    docs = [doc(i, ["Christa Pike, 50, survived two lethal injections", f"New development number {i} today"]) for i in range(4)]
    assert find_boilerplate(docs, same) == {}
    assert find_boilerplate(docs, different) != {}


def test_very_frequent_inline_embed_is_boilerplate_wherever_it_sits():
    embed = "Wir benötigen Ihre Einwilligung, um den Instagram-Inhalt anzuzeigen."
    docs = [doc(i, [f"Story {i} opening paragraph text", embed, f"Story {i} closing paragraph text"]) for i in range(10)]
    assert ("o", embed.lower()) in find_boilerplate(docs, different)
    assert find_boilerplate(docs[:9], different) == {}          # 9 body repeats: below the bar, kept


def test_two_repeats_never_count_and_outlets_are_separate():
    docs = [doc(i, ["Story", *FOOTER], outlet=f"o{i}") for i in range(4)]    # same footer, different outlets
    assert find_boilerplate(docs, different) == {}


def test_strip_keeps_the_story():
    text = "Story starts here\n" + "\n".join(FOOTER)
    assert strip_boilerplate(text, {f.lower() for f in FOOTER}) == "Story starts here"
    assert strip_boilerplate(None, set()) is None

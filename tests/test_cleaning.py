"""Text cleaning (1.2) and non-article rules (2.1, A38) — cases taken from the 2026-10-11 verification."""
from pipeline.ingest.extract import clean_text, is_paywalled
from pipeline.normalize.kinds import is_page, is_rundown, is_wall, kind_of
from pipeline.ingest.feeds import to_utc_iso
from pipeline.net import iri_to_uri


def test_clean_text_strips_html_and_entities_but_keeps_comparisons():
    assert clean_text("<p>&#8220;Senza precedenti&#8221;. La n</p>") == "“Senza precedenti”. La n"
    assert clean_text("<img src='x.jpg'/><p>Den peruanska staden</p><p>I veckan&nbsp;valdes</p>") == \
        "Den peruanska staden\nI veckan valdes"
    assert clean_text('<iframe frameborder="0" src="x"></iframe>Text') == "Text"
    assert clean_text("lo apoyan.\n<!--[if IE 9]>\n<![endif]-->\nPedro &#xE80F") == "lo apoyan.\n\nPedro"
    assert clean_text("3 < 5 and 7 > 2, Tom &amp; Jerry") == "3 < 5 and 7 > 2, Tom & Jerry"


def test_clean_text_drops_known_notice_paragraphs_only():
    faz = "Um externe Inhalte anzuzeigen, ist Ihre widerrufliche Zustimmung nötig. Weitere Informationen .\nIn Schwerin beginnen Verhandlungen."
    assert clean_text(faz) == "In Schwerin beginnen Verhandlungen."
    law = "Samtyckeslagen ska utvärderas.\nRegeringen vill se över samtycke i lagen."      # real news about consent
    assert clean_text(law) == law


def test_rundowns_are_multi_topic_shows_not_single_topic_live_blogs():
    assert is_rundown("LCI Live du samedi 10 octobre 2026", "Au sommaire : Primaire de la gauche hackée…")
    assert is_rundown("Le JT de 20 heures de TF1 du samedi 10 octobre 2026", "Retrouvez en replay…")
    assert is_rundown("10/10: CBS Weekend News", "Isaias kills at least 4 in Florida…")
    assert not is_rundown("Liveblog Ukrainekrieg: Trump: „Es ist Zeit…“", "US-Präsident: …")
    assert not is_rundown("Quinté du dimanche 11 octobre à Auteuil : « Il a encore évolué »", None)


def test_site_pages_but_not_news_about_privacy():
    for t in ("Cookies på db.no - Dagbladet", "Om cookies", "Terms of Use : NPR", "Datenschutzerklärung",
              "Allgemeine Geschäftsbedingungen für Gewinnspiele der DIGITAL"):
        assert is_page(t), t
    for t in ("Datenschutz: Meta muss zahlen", "EU fines Google over cookie banners", "Personvern: Datatilsynet bøtelegger"):
        assert not is_page(t), t


def test_wall_needs_both_a_weak_headline_match_and_a_wall_phrase():
    ansa = "Se hai scelto di non accettare i cookie di profilazione e tracciamento, puoi aderire all'abbonamento"
    assert is_wall(ansa, 0.07)
    assert not is_wall(ansa, 0.45)                                        # headline matches: an article
    assert not is_wall("While today's freezers are packed with flavors such as cookies and cream", 0.68)


def test_paywall_page_is_not_article_text():
    faz = "Am Eingang kurz vor dem Strand …\nZugang zu allen FAZ+ Beiträgen\n(Originalpreis: 13,80 €)\nEs ist ein außergewöhnlicher Fall"
    assert is_paywalled(faz)
    assert not is_paywalled("Ein ganz normaler Artikel über Heiligendamm.")


def test_european_zone_abbreviations_are_not_read_as_utc():
    assert to_utc_iso("Sun, 11 Oct 2026 13:51:24 CEST") == "2026-10-11T11:51:24Z"      # Bild
    assert to_utc_iso("Sun, 11 Oct 2026 13:51:24 +0000") == "2026-10-11T13:51:24Z"


def test_non_ascii_urls_are_encoded_ascii_untouched():
    assert iri_to_uri("https://example.it/züri?x=ä") == "https://example.it/z%C3%BCri?x=%C3%A4"
    assert iri_to_uri("https://example.com/a?b=c") == "https://example.com/a?b=c"


def test_kinds_hubs_and_headline_only():
    assert kind_of("FC Augsburg: Aktuelle News, Interviews, Transfergerüchte und mehr", "x",
                   "https://www.bild.de/sport/fussball/fc-augsburg/startseite-fc-augsburg-1.bild.html", "text") == "page"
    assert kind_of("Se høydepunktene fra Inter - Parma", None, "https://www.vg.no/sport/i/1oq02l/x", None) == "headline_only"
    assert kind_of("Trump: Ukraine needs a new president", "Summary.", "https://x/y", None) is None


def test_same_item_needs_same_headline_and_same_text():
    from pipeline.events.wire import same_item
    body = ("Minst tolv personer dödades och 309 skadades i en attack mot flygplatsen i Riyadh på lördagen "
            "enligt saudiska myndigheter som pekar ut huthirebellerna i Jemen som ansvariga för attacken")
    h = "Minst tolv döda i flygplatsattack"
    assert same_item(h, body + " (TT)", h, body) is not None                         # agency tag is not editorial
    assert same_item(h, h + "\n" + body, h, body) is not None                       # headline repeated in the text
    assert same_item("Endrer navn på snus\u00adboksene", body, "Endrer navn på snusboksene", body) is not None
    assert same_item("Trump tordner mod Zelenskyj", body, "Trump: Ukraine har brug for en ny præsident", body) is None
    longer = body + " " + " ".join(f"Egen rapport stycke {k} om läget i staden." for k in range(4))
    assert same_item(h, body, h, longer) is None                                     # the outlet added its own text
    assert same_item(h, "kort", h, "kort") is None                                   # too short to tell

"""A34-A36: headline history. M3 decision rule, M4 vector averaging, M7 rendering."""
import numpy as np
import pytest

from pipeline.contracts import HeadlineVersion
from pipeline.embed.run import input_hash, mean_vectors, version_texts
from pipeline.judge.render import render_article
from pipeline.normalize.rules import Member, decide


def members(n, summaries=True):
    return [Member(f"a{k}", f"2026-10-0{k + 1}T10:00:00Z", summaries) for k in range(n)]


def test_article_renamed_any_number_of_times_is_one_article():
    n = 7                                                   # no headline-count cap any more
    S = np.ones((n, n))                                     # the feed summary never changed
    T = np.full((n, n), 0.4)                                # headlines reworded a lot
    d = decide(members(n), S, T, np.full(n, 0.3))           # even with a weak headline-to-text match (teaser text)
    assert d.kind == "renamed"
    assert set(d.canonical.values()) == {"a0"}              # earliest copy is canonical
    assert d.keeps_text == {"a0"}


def test_live_blog_top_story_keeps_text_others_lose_it():
    n = 5
    S = np.eye(n) + 0.1 * (1 - np.eye(n))                   # every entry has its own summary
    T = np.eye(n) + 0.2 * (1 - np.eye(n))
    text = np.array([0.15, 0.72, 0.30, 0.20, 0.10])         # entry a1 is the story at the top of the page
    d = decide(members(n), S, T, text)
    assert d.kind == "container" and d.keeps_text == {"a1"}
    assert all(d.canonical[m] == m for m in d.canonical)    # nothing merged


def test_ambiguous_container_gives_text_to_nobody():
    n = 4                                                   # four different car crashes vs one crash text
    S = np.eye(n) + 0.1 * (1 - np.eye(n))
    T = np.eye(n) + 0.6 * (1 - np.eye(n))
    d = decide(members(n), S, T, np.array([0.57, 0.51, 0.47, 0.47]))
    assert d.kind == "container" and d.keeps_text == set()


def test_without_summaries_only_near_identical_headlines_link():
    n = 3
    S = np.zeros((n, n))
    T = np.eye(n) + 0.3 * (1 - np.eye(n))                   # DR "seneste": unrelated items, no summaries
    d = decide(members(n, summaries=False), S, T, np.array([0.2, 0.3, 0.1]))
    assert d.kind == "container" and d.keeps_text == set()
    T2 = np.ones((n, n)) * 0.9                              # same item, headline polished twice
    assert decide(members(n, summaries=False), S, T2, np.array([0.2, 0.3, 0.1])).kind == "renamed"


def test_mean_vector_is_normalised_and_single_version_unchanged():
    rng = np.random.default_rng(0)
    V = rng.normal(size=(5, 8)).astype(np.float32)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    out = mean_vectors(V, [1, 4])
    assert np.allclose(out[0], V[0], atol=1e-6)              # one headline: same as before A35
    assert np.allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-6)
    assert np.allclose(out[1], V[1:].mean(0) / np.linalg.norm(V[1:].mean(0)), atol=1e-6)


def test_input_hash_changes_with_a_new_headline_version():
    assert input_hash(["A"], "body") != input_hash(["A", "B"], "body")
    assert version_texts(["A", "B"], "body") == ["A\nbody", "B\nbody"]


def hv(title, ts):
    return HeadlineVersion("x", title, ts, "https://example.org/x", "feed_edit")


def test_render_shows_latest_headline_and_history_with_times():
    out = render_article([hv("Protesters clash with police", "2026-10-10T14:02:00Z"),
                          hv("Police attack peaceful protesters", "2026-10-10T16:30:00Z")], "Body text.")
    assert out.startswith("Headline: Police attack peaceful protesters (since 2026-10-10 16:30 UTC)")
    assert '"Protesters clash with police" (from 2026-10-10 14:02 UTC)' in out
    assert out.endswith("Body text.")
    assert render_article([hv("Only one", "2026-10-10T14:02:00Z")], "B") == "Headline: Only one\n\nB"


def test_render_caps_history_but_keeps_first_and_latest():
    versions = [hv(f"v{k}", f"2026-10-10T{10 + k:02d}:00:00Z") for k in range(10)]
    out = render_article(versions, "B", max_versions=6)
    assert "Headline: v9" in out and '"v0"' in out and "4 more versions" in out
    assert '"v1"' not in out and '"v8"' in out
    with pytest.raises(ValueError):
        render_article(versions, "B", policy="nonsense")

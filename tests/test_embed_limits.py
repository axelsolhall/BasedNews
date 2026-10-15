"""The embed step failed every hour on 2026-10-15: one article from a new Greek-script outlet was over the API's
8,192-token input limit (the old cap counted characters, and Greek costs ~5x the tokens of English), and one rejected
input failed the whole batch and step. Now inputs are cut at the limit with the model's own tokenizer, and a rejected
input only loses itself."""
import datetime as dt
import types

import numpy as np

from pipeline import store
from pipeline.contracts import Article
from pipeline.embed import backend
from pipeline.settings import load_settings


def test_greek_text_is_cut_at_the_token_limit_not_a_character_count():
    import tiktoken
    enc = tiktoken.get_encoding(backend.TOKENIZER)
    greek = "Η κυβέρνηση ανακοίνωσε νέους φόρους σήμερα. " * 600            # ~27 k chars, far over the limit
    assert len(enc.encode(greek)) > backend.MAX_INPUT_TOKENS
    (cut,) = backend.fit_to_limit([greek])
    assert len(enc.encode(cut)) <= backend.MAX_INPUT_TOKENS
    assert backend.fit_to_limit(["A short English headline."]) == ["A short English headline."]   # untouched


class Rejected(Exception):
    status_code = 400


class FakeClient:
    """Rejects any batch containing a text marked REJECT, like the API does for an over-long input."""
    def __init__(self, dims=8):
        self.dims, self.calls = dims, 0
        self.embeddings = types.SimpleNamespace(create=self.create)

    def create(self, model, input, **kw):
        self.calls += 1
        if any("REJECT" in t for t in input):
            raise Rejected("Invalid input: maximum input length is 8192 tokens.")
        data = [types.SimpleNamespace(index=i, embedding=list(np.random.default_rng(len(t)).normal(size=self.dims)))
                for i, t in enumerate(input)]
        return types.SimpleNamespace(data=data, usage=None)


def encoder(client):
    return backend.OpenAIEncoder("openai:text-embedding-3-small@8", load_settings(), client=client)


def test_one_rejected_text_loses_only_itself():
    texts = [f"article {i}" for i in range(9)] + ["REJECT me"] + [f"article {i}" for i in range(9, 15)]
    V = encoder(FakeClient()).encode(texts)
    ok = np.isfinite(V).all(axis=1)
    assert ok.sum() == 15 and not ok[9]                                  # 15 embedded, the bad one is NaN


def test_a_rejected_text_is_not_retried_five_times(monkeypatch):
    slept = []
    monkeypatch.setattr(backend.time, "sleep", slept.append)
    client = FakeClient()
    encoder(client).encode(["REJECT"])
    assert client.calls == 1 and not slept                               # a 400 cannot succeed on retry


def test_the_embed_step_skips_the_rejected_article_and_keeps_going(tmp_path, monkeypatch):
    from pipeline.embed import run
    conn = store.connect(tmp_path / "t.sqlite")
    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i, title in enumerate(["Good one", "REJECT this one", "Good two"]):
        store.insert_article(conn, Article(id=f"a{i}", outlet_id="o", country="X", url=f"u{i}", title=title,
                                           published=now, summary="summary", full_text=None, feed_url="f",
                                           ingested_at=now))
    conn.commit()
    monkeypatch.setattr(run, "get_encoder", lambda model, conn=None: encoder(FakeClient()))
    n, _, _ = run.embed_window(conn, "openai:text-embedding-3-small@8", 14)
    assert n == 2
    saved = {r["article_id"] for r in conn.execute("SELECT article_id FROM embeddings")}
    assert saved == {"a0", "a2"}

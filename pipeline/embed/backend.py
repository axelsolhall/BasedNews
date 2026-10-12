"""2.2 embedding backends behind one interface (A45): the local MiniLM baseline or an OpenAI embedding model.

    enc = get_encoder()                      # model from settings (BASEDNEWS_EMBED_MODEL)
    V = enc.encode(texts)                    # float32, L2-normalised rows
Both follow sentence-transformers' encode() signature, so 2.1 and 2.2 do not care which one runs. Vectors are stored
under the model name, so switching models re-embeds automatically and never mixes vector spaces.
"""
from __future__ import annotations

import time

import numpy as np

from pipeline.settings import MINILM, Settings, load_settings

OPENAI_PREFIX = "openai:"
MAX_EMBED_CHARS = 24_000      # ~6 k tokens, under the 8,191-token input limit; vectors only — the judge reads all (A41)
OPENAI_BATCH = 256


class LocalEncoder:
    def __init__(self, model: str = MINILM):
        from sentence_transformers import SentenceTransformer       # heavy import, only when used
        self.name = model
        self._m = SentenceTransformer(model)

    def encode(self, texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False):
        return self._m.encode(list(texts), normalize_embeddings=normalize_embeddings, batch_size=batch_size,
                              show_progress_bar=show_progress_bar).astype(np.float32)


class OpenAIEncoder:
    def __init__(self, model: str, settings: Settings, client=None, conn=None):
        if not settings.has_openai and client is None:
            raise RuntimeError("OPENAI_API_KEY is not set (.env) — needed for " + model)
        from openai import OpenAI
        self.name = model
        self.api_model = model.removeprefix(OPENAI_PREFIX)
        self.client = client or OpenAI(api_key=settings.openai_api_key)
        self.settings, self.conn = settings, conn

    def encode(self, texts, normalize_embeddings=True, batch_size=OPENAI_BATCH, show_progress_bar=False):
        texts = [(t or " ")[:MAX_EMBED_CHARS] for t in texts]
        out = []
        for k in range(0, len(texts), batch_size):
            batch = texts[k:k + batch_size]
            for attempt in range(5):
                try:
                    r = self.client.embeddings.create(model=self.api_model, input=batch)
                    break
                except Exception:                   # rate limit / transient: back off, then give up loudly
                    if attempt == 4:
                        raise
                    time.sleep(2 ** attempt)
            out += [d.embedding for d in sorted(r.data, key=lambda d: d.index)]
            if self.conn is not None and getattr(r, "usage", None):
                tokens = r.usage.total_tokens
                self.conn.execute("SELECT 1")             # keep the connection warm in long runs
                from pipeline import store
                store.log_usage(self.conn, "openai", self.name, "embedding", tokens, 0,
                                tokens * self.settings.price(self.name)[0] / 1e6)
        V = np.asarray(out, dtype=np.float32)
        if normalize_embeddings and len(V):
            V /= np.linalg.norm(V, axis=1, keepdims=True).clip(min=1e-12)
        return V


def get_encoder(model: str | None = None, settings: Settings | None = None, conn=None):
    settings = settings or load_settings()
    model = model or settings.embed_model
    if model.startswith(OPENAI_PREFIX):
        return OpenAIEncoder(model, settings, conn=conn)
    return LocalEncoder(model)

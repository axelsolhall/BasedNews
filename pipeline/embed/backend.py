"""2.2 embedding backends behind one interface (A45): the local MiniLM baseline or an OpenAI embedding model.

    enc = get_encoder()                      # model from settings (BASEDNEWS_EMBED_MODEL)
    V = enc.encode(texts)                    # float32, L2-normalised rows
Both follow sentence-transformers' encode() signature, so 2.1 and 2.2 do not care which one runs. Vectors are stored
under the model name, so switching models re-embeds automatically and never mixes vector spaces.

OpenAI model names may carry a size: `openai:text-embedding-3-small@512` asks the API for 512 dimensions (A56).
With `cache=True` (2.1 normalize) every vector is kept by text hash in `text_vectors`, so an hourly run pays only for
texts it has not seen; retention drops entries unused for 30 days (A52). Each paid batch is logged in `api_usage` and
refused once the month's AI cap is reached (A48).
"""
from __future__ import annotations

import hashlib
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


def parse_openai(model: str) -> tuple[str, int | None]:
    """`openai:text-embedding-3-small@512` → ("text-embedding-3-small", 512)."""
    name, _, dims = model.removeprefix(OPENAI_PREFIX).partition("@")
    return name, int(dims) if dims else None


class OpenAIEncoder:
    def __init__(self, model: str, settings: Settings, client=None, conn=None, cache: bool = False):
        if not settings.has_openai and client is None:
            raise RuntimeError("OPENAI_API_KEY is not set (.env) — needed for " + model)
        if cache and conn is None:
            raise ValueError("cache=True needs a database connection")
        self.name = model
        self.api_model, self.dims = parse_openai(model)
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=settings.openai_api_key)
        self.client = client
        self.settings, self.conn, self.cache = settings, conn, cache

    def _price(self) -> float:
        return self.settings.price(f"{OPENAI_PREFIX}{self.api_model}")[0]

    def _call(self, batch: list[str]) -> list[list[float]]:
        if self.conn is not None:                           # A48: embeddings count toward the same monthly cap
            from pipeline import store
            spent = store.spent_this_month(self.conn)
            if spent >= self.settings.monthly_ai_usd:
                raise RuntimeError(f"monthly AI cap reached (${spent:.2f} of ${self.settings.monthly_ai_usd:.2f}, A48)")
        kw = {"dimensions": self.dims} if self.dims else {}
        for attempt in range(5):
            try:
                r = self.client.embeddings.create(model=self.api_model, input=batch, **kw)
                break
            except Exception:                               # rate limit / transient: back off, then give up loudly
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        if self.conn is not None and getattr(r, "usage", None):
            from pipeline import store
            tokens = r.usage.total_tokens
            store.log_usage(self.conn, "openai", self.name, "embedding", tokens, 0, tokens * self._price() / 1e6)
        return [d.embedding for d in sorted(r.data, key=lambda d: d.index)]

    def encode(self, texts, normalize_embeddings=True, batch_size=OPENAI_BATCH, show_progress_bar=False):
        texts = [(t or " ")[:MAX_EMBED_CHARS] for t in texts]
        keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts] if self.cache else []
        known: dict[str, np.ndarray] = {}
        if self.cache:
            from pipeline.ingest.feeds import utc_now_iso
            uniq = list(dict.fromkeys(keys))
            for k in range(0, len(uniq), 900):              # SQLite's parameter limit
                chunk = uniq[k:k + 900]
                for r in self.conn.execute(f"SELECT text_sha1, vector FROM text_vectors WHERE model = ? AND text_sha1 IN "
                                           f"({','.join('?' * len(chunk))})", (self.name, *chunk)):
                    known[r[0]] = np.frombuffer(r[1], dtype=np.float32)
            now = utc_now_iso()
            self.conn.executemany("UPDATE text_vectors SET used_at = ? WHERE model = ? AND text_sha1 = ? AND used_at < ?",
                                  [(now, self.name, h, now[:10]) for h in known])      # at most one touch a day
        todo = list(dict.fromkeys(t for t, h in zip(texts, keys or [None] * len(texts)) if h not in known)) \
            if self.cache else texts
        fresh = []
        for k in range(0, len(todo), batch_size):
            fresh += self._call(todo[k:k + batch_size])
        if self.cache:
            from pipeline.ingest.feeds import utc_now_iso
            new = {hashlib.sha1(t.encode()).hexdigest(): np.asarray(v, dtype=np.float32) for t, v in zip(todo, fresh)}
            self.conn.executemany("INSERT OR REPLACE INTO text_vectors VALUES (?,?,?,?)",
                                  [(self.name, h, v.tobytes(), utc_now_iso()) for h, v in new.items()])
            known.update(new)
            out = [known[h] for h in keys]
        else:
            out = fresh
        V = np.asarray(out, dtype=np.float32).reshape(len(texts), -1) if len(texts) else np.zeros((0, 0), np.float32)
        if normalize_embeddings and len(V):
            V /= np.linalg.norm(V, axis=1, keepdims=True).clip(min=1e-12)
        return V


def get_encoder(model: str | None = None, settings: Settings | None = None, conn=None, cache: bool = False):
    """cache=True: keep vectors by text hash (OpenAI only; the local model is free to re-run)."""
    settings = settings or load_settings()
    model = model or settings.embed_model
    if model.startswith(OPENAI_PREFIX):
        return OpenAIEncoder(model, settings, conn=conn, cache=cache)
    return LocalEncoder(model)

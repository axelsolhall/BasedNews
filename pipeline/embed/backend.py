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
MAX_INPUT_TOKENS = 8192       # the API's own limit per input (its 400 error, 2026-10-15); counted with the model's
                              # tokenizer, because characters are not tokens: 7 tokens for an English sentence is 37 in
                              # Greek. Vectors only — the judge reads the full text (A41).
TOKENIZER = "cl100k_base"     # text-embedding-3-*; baked into the job image (Dockerfile), never downloaded at run time
OPENAI_BATCH = 256


def fit_to_limit(texts: list[str]) -> list[str]:
    """Cut every text at MAX_INPUT_TOKENS tokens of the model's tokenizer; shorter texts are returned unchanged."""
    import tiktoken
    enc = tiktoken.get_encoding(TOKENIZER)
    out = []
    for t in texts:
        ids = enc.encode(t or " ", disallowed_special=())
        out.append(t or " " if len(ids) <= MAX_INPUT_TOKENS else enc.decode(ids[:MAX_INPUT_TOKENS]))
    return out


def _is_rejected(e: Exception) -> bool:
    """The API refused the input itself (HTTP 400): retrying the same input can never succeed."""
    return getattr(e, "status_code", None) == 400


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
            except Exception as e:                          # rate limit / transient: back off, then give up loudly
                if attempt == 4 or _is_rejected(e):
                    raise
                time.sleep(2 ** attempt)
        if self.conn is not None and getattr(r, "usage", None):
            from pipeline import store
            tokens = r.usage.total_tokens
            store.log_usage(self.conn, "openai", self.name, "embedding", tokens, 0, tokens * self._price() / 1e6)
        return [d.embedding for d in sorted(r.data, key=lambda d: d.index)]

    def _call_isolating(self, batch: list[str]) -> list[list[float] | None]:
        """Like _call, but an input the API rejects only loses itself: the batch is split until the rejected text is
        alone, which comes back as None (logged); everything else is embedded. One bad article never fails a step."""
        try:
            return self._call(batch)
        except Exception as e:
            if not _is_rejected(e):
                raise
            if len(batch) == 1:
                import json
                print(json.dumps({"event": "embedding_rejected", "chars": len(batch[0]), "error": str(e)[:300]}), flush=True)
                return [None]
            mid = len(batch) // 2
            return self._call_isolating(batch[:mid]) + self._call_isolating(batch[mid:])

    def encode(self, texts, normalize_embeddings=True, batch_size=OPENAI_BATCH, show_progress_bar=False):
        """Rows the API rejected come back as NaN: callers drop them (pipeline/embed/run.py)."""
        texts = fit_to_limit(list(texts))
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
            fresh += self._call_isolating(todo[k:k + batch_size])
        dim = next((len(v) for v in fresh if v is not None), self.dims or 1)
        fresh = [np.full(dim, np.nan, dtype=np.float32) if v is None else v for v in fresh]
        if self.cache:
            from pipeline.ingest.feeds import utc_now_iso
            new = {hashlib.sha1(t.encode()).hexdigest(): np.asarray(v, dtype=np.float32) for t, v in zip(todo, fresh)
                   if np.isfinite(v).all()}                    # a rejected text is never cached
            known.update({hashlib.sha1(t.encode()).hexdigest(): np.asarray(v, dtype=np.float32)
                          for t, v in zip(todo, fresh) if not np.isfinite(v).all()})
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

# One image for every job step (ADR-010): `python -m pipeline.job <step>`. The same image runs locally (compose) and on
# ECS Fargate. No secrets inside: the OpenAI key comes from SSM (production) or .env (compose) at run time.
#   docker build -t basednews-job .                            # embeddings via the OpenAI API (A45, A56)
#   docker build --build-arg WITH_ML=1 -t basednews-job:ml .   # + the local MiniLM baseline (≈ +1.5 GB), rarely needed
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1 HF_HOME=/opt/hf TIKTOKEN_CACHE_DIR=/opt/tiktoken
WORKDIR /app

# dependencies first (cached until pyproject/uv.lock change), exactly as locked
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# the embedding tokenizer's table, fetched once at build time: a job never downloads it (pipeline/embed/backend.py)
RUN .venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" && chmod -R a+rX /opt/tiktoken

# optional local embedding model: CPU-only torch (the default Linux wheel pulls ~2.5 GB of CUDA), versions as in uv.lock
ARG WITH_ML=0
RUN if [ "$WITH_ML" = "1" ]; then \
      uv pip install --python .venv/bin/python --index-url https://download.pytorch.org/whl/cpu "torch==2.14.1" && \
      uv pip install --python .venv/bin/python "sentence-transformers==6.1.0" && \
      .venv/bin/python -c "from sentence_transformers import SentenceTransformer as S; S('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2')" && \
      chmod -R a+rX /opt/hf; \
    fi

COPY pipeline ./pipeline
# the [n] glob: embedding_profiles.json exists only after an OpenAI calibration (A47); MiniLM's profile is in code
COPY data/outlets.json data/outlet_aliases.json data/embedding_profiles.jso[n] ./data/

# the commit the image was built from: every job record carries it (pipeline/version.py)
ARG GIT_SHA=unknown
ENV BASEDNEWS_COMMIT=$GIT_SHA PATH="/app/.venv/bin:$PATH"
RUN useradd --system --uid 10001 app && mkdir -p /app/logs && chown -R app /app/logs /app/data
USER app
ENTRYPOINT ["python", "-m", "pipeline.job"]
CMD ["list"]

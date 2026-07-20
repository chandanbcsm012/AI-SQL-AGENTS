# Containerizes the Streamlit app (docs/IMPLEMENTATION_PLAN.md Phase 5).
# Everything this app talks to (Postgres, Valkey, Qdrant, Ollama, the OTel
# stack) lives in infra/docker-compose.yml -- run this alongside that, on
# the same Docker network, pointing DATABASE_URL/REDIS_URL/QDRANT_URL/
# OLLAMA_BASE_URL at the compose service names instead of localhost.

FROM python:3.11-slim

# uv: the same package manager used for local dev (pyproject.toml/uv.lock).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependencies first so they're cached across rebuilds that only touch code.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .
RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH"

# Presidio's NER masking needs this spaCy model (middleware/pii.py). `uv
# pip install` (not `spacy download`, which shells out to a pip binary
# that isn't present in a uv-managed venv) installs the model wheel directly.
RUN uv pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s CMD \
    python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

# db/app.db (the demo business data) is generated, not baked into the
# image -- run `uv run python db/init_db.py` once against a mounted
# volume, or mount an existing app.db, before starting.
CMD ["uv", "run", "streamlit", "run", "streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]

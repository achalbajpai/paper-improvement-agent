

FROM pandoc/latex:3.10-ubuntu



ENTRYPOINT []

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/srv

WORKDIR /srv






COPY pyproject.toml uv.lock ./
RUN mkdir -p app && touch app/__init__.py \
 && uv venv "$VIRTUAL_ENV" \
 && uv export --frozen --all-extras --no-emit-project --no-hashes -o /tmp/requirements.txt \
 && uv pip install --no-cache -r /tmp/requirements.txt \
 && uv pip install --no-cache --no-deps -e .

COPY app ./app
COPY tests ./tests

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

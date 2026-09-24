# Production image: one container serving the API, the built frontend, the
# generated demo warehouse, the recorded runs, and the MCP endpoint.
#
# The demo warehouse is generated at build time rather than shipped, so the
# image carries the generator's output for the pinned seed and the dataset
# fingerprint is reproducible from source.

# ---------------------------------------------------------------- frontend
FROM node:22-slim AS web
WORKDIR /build

COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY web/tsconfig.json web/tsconfig.app.json web/tsconfig.node.json web/vite.config.ts web/index.html ./
COPY web/src ./src
RUN npm run build


# ------------------------------------------------------------ python build
FROM python:3.12-slim AS build
WORKDIR /build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY pyproject.toml README.md constraints.txt ./
COPY src ./src

RUN python -m pip install --upgrade pip build \
 && python -m build --wheel --outdir /wheels


# ------------------------------------------------------------------ runtime
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    AAE_PROVIDER_MODE=fake \
    AAE_LIVE_ANALYTICS_ENABLED=false \
    AAE_DATA_DIR=/app/var/warehouse \
    AAE_UPLOAD_DIR=/app/var/uploads \
    AAE_RECORDINGS_DIR=/app/examples/recordings \
    PORT=8000

WORKDIR /app

# curl is the healthcheck; nothing else is added to the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 app

COPY --from=build /wheels/*.whl /tmp/
COPY constraints.txt /tmp/constraints.txt
# Installed against the pinned closure, so a rebuild resolves to the same
# tree the tests and the recorded runs were produced against.
RUN python -m pip install -c /tmp/constraints.txt /tmp/*.whl \
      "uvicorn[standard]" "fastapi" "python-multipart" \
 && rm -rf /tmp/*.whl /tmp/constraints.txt

COPY --from=web /build/dist /app/web/dist
COPY examples/recordings /app/examples/recordings

# Generate the demo warehouse into the image, then hand ownership to the
# unprivileged user. The application never writes outside /app/var.
RUN mkdir -p /app/var/warehouse /app/var/uploads \
 && python -m agentic_analytics.cli generate-data --out /app/var/warehouse/commerce \
 && python -m agentic_analytics.cli validate-recordings --directory /app/examples/recordings \
 && chown -R app:app /app/var

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

CMD ["sh", "-c", "exec uvicorn agentic_analytics.api.app:app --host 0.0.0.0 --port ${PORT} --workers 1"]

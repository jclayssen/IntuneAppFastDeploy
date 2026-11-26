# syntax=docker/dockerfile:1

# Versions here are moved forward by Renovate pull requests; the image is also
# rebuilt every week so the base system gets its security patches.
FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.13-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim
LABEL org.opencontainers.image.title="IntuneAppFastDeploy" \
      org.opencontainers.image.description="Publish and update MSI/EXE applications in Microsoft Intune from a download link" \
      org.opencontainers.image.licenses="MIT"
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    CONFIG_DIR=/config \
    WORK_DIR=/tmp/iafd
# Any unprivileged user works: compose.yaml runs the container as the owner of
# the config folder so the files written there stay yours.
USER 10001:10001
EXPOSE 8080
VOLUME ["/config"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"]
CMD ["python", "-m", "iafd.web"]

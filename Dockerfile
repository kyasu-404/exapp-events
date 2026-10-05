# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build
RUN npm install --global pnpm@11.19.0
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY tsconfig.json vite.config.ts ./
COPY scripts/copy-style.mjs ./scripts/copy-style.mjs
COPY ex_app/src ./ex_app/src
RUN pnpm typecheck && pnpm build

FROM python:3.12-slim-bookworm AS python-builder
WORKDIR /build
COPY pyproject.toml ./
COPY ex_app/lib ./ex_app/lib
COPY ex_app/__init__.py ./ex_app/__init__.py
RUN pip install --no-cache-dir --prefix=/install .

# FRP version and upstream checksums from the official nextcloud/app-skeleton-python.
ARG TARGETARCH
ARG FRP_VERSION=0.61.1
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*; \
    case "$TARGETARCH" in \
      amd64) sum=bff260b68ca7b1461182a46c4f34e9709ba32764eed30a15dd94ac97f50a2c40 ;; \
      arm64) sum=af6366f2b43920ebfe6235dba6060770399ed1fb18601e5818552bd46a7621f8 ;; \
      *) echo "Supported architectures: amd64, arm64"; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_linux_${TARGETARCH}.tar.gz" -o /tmp/frp.tar.gz && \
    echo "$sum  /tmp/frp.tar.gz" | sha256sum -c - && \
    tar -xzf /tmp/frp.tar.gz -C /tmp && \
    cp "/tmp/frp_${FRP_VERSION}_linux_${TARGETARCH}/frpc" /install/bin/frpc

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends gosu ca-certificates && \
    rm -rf /var/lib/apt/lists/* && useradd --uid 10001 --create-home events
COPY --from=python-builder /install /usr/local
WORKDIR /app
COPY ex_app/lib ./ex_app/lib
COPY ex_app/__init__.py ./ex_app/__init__.py
COPY ex_app/img ./ex_app/img
COPY --from=frontend /build/ex_app/js/events-main.js ./ex_app/js/events-main.js
COPY --from=frontend /build/ex_app/css/events-main.css ./ex_app/css/events-main.css
COPY appinfo ./appinfo
COPY --chmod=755 scripts/entrypoint.sh scripts/start.sh scripts/healthcheck.py scripts/configure_frp.py /scripts/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 APP_HOST=0.0.0.0 APP_PORT=23000 APP_PERSISTENT_STORAGE=/data
VOLUME /data
ENTRYPOINT ["/scripts/entrypoint.sh"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=4 CMD ["python", "/scripts/healthcheck.py"]

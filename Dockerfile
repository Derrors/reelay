# 国内网络可加：--build-arg NPM_REGISTRY=https://registry.npmmirror.com/ \
#              --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
FROM node:24-bookworm-slim AS frontend
ARG NPM_REGISTRY=https://registry.npmjs.org/
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --registry="$NPM_REGISTRY"
COPY web/ ./
RUN npm run typecheck && npm run build

FROM python:3.12-slim-bookworm
ARG PIP_INDEX_URL=https://pypi.org/simple
LABEL org.opencontainers.image.source="https://github.com/Derrors/reelay" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.description="Self-hosted Telegram → 115 auto transfer with AI parsing"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY reelay/ ./reelay/
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" ".[all]" \
    && useradd --create-home --uid 10001 reelay \
    && mkdir -p /app/data/plugins /app/config /app/web/dist \
    && chown -R reelay:reelay /app/data /app/config
COPY --from=frontend /build/web/dist/ ./web/dist/
USER reelay
CMD ["python", "-m", "reelay.main"]

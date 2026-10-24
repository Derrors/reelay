# 国内网络可加：--build-arg NPM_REGISTRY=https://registry.npmmirror.com/ \
#              --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# 前端产物与架构无关：在构建机的原生平台上编译，多架构构建时不必在 QEMU 里跑 npm
FROM --platform=$BUILDPLATFORM node:24-bookworm-slim AS frontend
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
COPY pyproject.toml constraints.txt README.md LICENSE ./
COPY reelay/ ./reelay/
# 按 constraints.txt 安装已验证的确切版本
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -c constraints.txt ".[all]" \
    && useradd --create-home --uid 10001 reelay \
    && mkdir -p /app/data/plugins /app/web/dist \
    && chown -R reelay:reelay /app/data
COPY --from=frontend /build/web/dist/ ./web/dist/
USER reelay
CMD ["python", "-m", "reelay.main"]

FROM node:24-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run typecheck && npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY reelay/ ./reelay/
RUN pip install --no-cache-dir ".[all]" \
    && useradd --create-home --uid 10001 reelay \
    && mkdir -p /app/data /app/config /app/plugins /app/web/dist \
    && chown -R reelay:reelay /app/data /app/config
COPY --from=frontend /build/web/dist/ ./web/dist/
USER reelay
CMD ["python", "-m", "reelay.main"]

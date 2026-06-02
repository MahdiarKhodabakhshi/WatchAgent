FROM node:20-slim AS frontend

WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml ruff.toml README.md ./
COPY app/ ./app/
RUN pip install --no-cache-dir --user .

FROM python:3.11-slim

RUN groupadd -r app && useradd -r -g app app
WORKDIR /srv
COPY --from=builder /root/.local /home/app/.local
COPY app/ ./app/
COPY --from=frontend /fe/dist ./app/static
RUN mkdir -p /srv/data && chown -R app:app /srv /home/app

USER app
ENV PATH=/home/app/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000

# Stdlib-only readiness probe (keeps curl out of the image) so `docker compose up --wait`
# blocks until the app actually serves /health with a 200.
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=12 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

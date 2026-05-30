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

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

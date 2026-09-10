FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md requirements.lock ./
COPY litlens ./litlens
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps '.[dev]' && useradd --create-home litlens && mkdir /app/data && chown litlens /app/data
COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests
COPY docs/openapi.json ./docs/openapi.json
ENV RUFF_CACHE_DIR=/tmp/litlens-ruff
USER litlens
EXPOSE 8000

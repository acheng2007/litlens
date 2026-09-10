FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY litlens ./litlens
RUN pip install --no-cache-dir '.[dev]' && useradd --create-home litlens && mkdir /app/data && chown litlens /app/data
COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests
USER litlens
EXPOSE 8000

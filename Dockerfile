FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY litlens ./litlens
RUN pip install --no-cache-dir -e .[pdf]
CMD ["uvicorn", "litlens.main:app", "--host", "0.0.0.0", "--port", "8000"]


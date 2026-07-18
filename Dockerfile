FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .

EXPOSE 8000
CMD ["/bin/sh", "-c", "uv run --no-sync alembic upgrade head && uv run --no-sync uvicorn main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'"]

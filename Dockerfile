FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.0 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .

# The application tree stays root-owned, so the account serving requests reads
# its code without being able to rewrite it. Only ./data needs to be writable.
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
CMD ["/bin/sh", "-c", "uv run --no-sync alembic upgrade head && uv run --no-sync uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]

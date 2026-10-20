FROM python:3.14-slim

# uv instala las dependencias exactas de uv.lock
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Primero solo las dependencias: esta capa se reutiliza mientras no cambien pyproject.toml ni uv.lock
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY main.py tools.py ./
COPY static ./static

# Sin privilegios de root
RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8000

# La autenticación con Azure usa la identidad administrada del Container App (DefaultAzureCredential)
CMD ["fastapi", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]

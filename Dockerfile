FROM ghcr.io/astral-sh/uv:0.12.10 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy FORUM_DATA_DIR=/data
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY forum ./forum
RUN uv sync --frozen --no-dev && useradd --create-home forum && mkdir /data && chown forum /data
USER forum
EXPOSE 8000
CMD ["/app/.venv/bin/forum", "serve", "--host", "0.0.0.0"]

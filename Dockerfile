FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

RUN mkdir -p /data && chmod 777 /data

VOLUME ["/data"]

ENV MCP_EGRUL_DB=/data/mcp_egrul_data.sqlite \
    MCP_EGRUL_DUMPS_DIR=/data/dumps \
    MCP_EGRUL_LOG_LEVEL=INFO

ENTRYPOINT ["mcp-egrul"]

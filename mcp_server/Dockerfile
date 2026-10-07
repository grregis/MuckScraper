# Read-only MCP server (docs/MCP.md). Same Python and app dependencies as the
# app image, minus the Playwright browsers and system libraries it never uses.
FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./requirements.txt
COPY mcp_server/requirements.txt ./mcp-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r mcp-requirements.txt

COPY aggregator ./aggregator
COPY news_fetcher ./news_fetcher
COPY mcp_server ./mcp_server

# code_live_check reads git metadata from the bind-mounted checkout.
RUN git config --global --add safe.directory /app

EXPOSE 8765
CMD ["python", "-m", "mcp_server"]

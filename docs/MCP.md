# MCP server (read-only health checks)

MuckScraper ships an optional [Model Context Protocol](https://modelcontextprotocol.io)
server, so an AI assistant (Claude, a local agent running on Ollama, or any
MCP client) can check on your install and help troubleshoot it: is a run in
progress, did the last one work, is Ollama asleep or broken, what is in this
edition, does a story look wrong.

It is **read-only**. It cannot fetch, restart, rescrape or change settings.

## What it checks

| Tool | What it answers |
|---|---|
| `run_status` | Is a pipeline run in progress? Was a scheduled run missed? Other background tasks, and the next runs. **Check this before anything that touches Ollama or containers.** |
| `last_run` | The last run's type, duration, articles in and stored, each step's status, Ollama's state. |
| `run_history` | Duration and volume of the last N runs, with slow or thin runs flagged. |
| `scheduler_log` | Filtered log lines: `errors`, `warnings`, `guards`, `dedupe`, `edition`, `ollama`, `search`, `runs`, or `all`. |
| `ollama_health` | Ollama answering, asleep after a run (normal), service down, hung, models partly on CPU, or another client using it. Never wakes the box or runs inference. |
| `outage_check` | Articles left without embeddings or tagged only "Other", and a jump in single-article stories: the signs a run happened without a working LLM. |
| `edition` | An edition's lineup: rank, headline, article and outlet counts, left/center/right mix, single-outlet stories, missing summaries. |
| `quality_report` | The fetch-quality evidence pack (headlines, summaries, grouping, bias, editions). |
| `get_story`, `get_article`, `search` | Look up stories and articles. |
| `scrape_health` | Blocked and failed scrapes by domain. |
| `bias_coverage` | Rated, unrated and abandoned outlets. |
| `containers` | Container states, start times, restart counts, disk space, database size. |
| `search_health` | Meilisearch up, and in step with the database. |
| `code_live_check` | Whether the running app and scheduler have the newest code loaded (the scheduler only loads code at startup). |
| `runbook` | The documented procedure for a topic, from this repo's docs. |

Most checks return a `verdict` (`ok`, `warning`, `problem`) and a `summary` in
plain language, so a small local model can relay them without interpreting raw
numbers. Times are UTC.

## How it stays read-only

- The database connection starts every transaction read-only
  (`default_transaction_read_only=on`, set in code). A write fails at Postgres.
- The container mounts the checkout read-only.
- It never gets the Docker socket. Container status and logs come from
  `docker-restart-proxy`, whose log route is GET-only and bounded.
- No tool sends anything to an LLM. `ollama_health` only reads `/api/version`
  and `/api/ps`, so it never loads a model or wakes a sleeping box (it cannot
  send wake-on-LAN).
- Secret values from the environment, tokens and database passwords are
  masked in every response.

## Setup

1. Generate a token and add it to `.env`:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
   ```
   MCP_TOKEN=<the token>
   ```
2. Start it (it is behind a Compose profile, so a plain `docker compose up`
   leaves it off):
   ```bash
   docker compose --profile mcp up -d --build mcp docker-restart-proxy
   ```
   Rebuilding `docker-restart-proxy` adds the log route the server uses; the
   admin app's restart buttons keep working.
3. Point your client at `http://127.0.0.1:8765/mcp` with the header
   `Authorization: Bearer <token>` (Streamable HTTP transport).

### Settings (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `MCP_TOKEN` | (none) | Required for HTTP. 16+ characters. The server refuses to start without it. |
| `MCP_BIND` | `127.0.0.1` | Host address the port is published on. Use `0.0.0.0` (or a LAN address) to reach it from another machine. |
| `MCP_PORT` | `8765` | Host port. |
| `MCP_ALLOWED_HOSTS` | (none) | Comma-separated host names. When set, requests with any other `Host` header are refused (useful behind a reverse proxy). |
| `MCP_EXPECTED_OLLAMA_CONTEXT` | (none) | Your Ollama server's configured context size. `ollama_health` then reports a model loaded at a different size, which means another client is using Ollama. |
| `MCP_OLLAMA_PROBE_LOG` | (none) | Path inside the container to a ping/port probe log, if you keep one; mount it with a `docker-compose.override.yml`. Its tail is included in `ollama_health`. |

### Reaching it from another machine

Set `MCP_BIND=0.0.0.0` (or the host's LAN address) and recreate the service.
The token is then the only lock, so keep the port off the internet: put it on
an internal reverse-proxy frontend with HTTPS, or firewall it to the machines
that need it. Docker-published ports bypass a host's `INPUT` firewall chain,
so restrict them in `DOCKER-USER` or bind to a specific address instead.

Behind a reverse proxy, set `MCP_ALLOWED_HOSTS` to the public host name and
forward `/mcp` to port 8765. Requests are short (stateless JSON responses), so
no special WebSocket or tunnel timeouts are needed.

### stdio

For a client that launches the server itself:
```bash
docker compose --profile mcp run --rm -T mcp python -m mcp_server --stdio
```
No token is needed over stdio: whoever can run that command already controls
the host.

## Client examples

**Claude Code:**
```bash
claude mcp add --transport http muckscraper http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer <token>"
```

**Generic MCP client config:**
```json
{
  "mcpServers": {
    "muckscraper": {
      "url": "http://127.0.0.1:8765/mcp",
      "headers": {"Authorization": "Bearer <token>"}
    }
  }
}
```

## Using it with a local model on the same Ollama

If the assistant itself runs on the Ollama server the pipeline uses, keep it
out of the pipeline's way:

- **Don't use it during runs.** Ollama serves one request per model at a time,
  so a long request from the assistant makes the pipeline's calls queue and
  time out; the run then skips its LLM work and stores articles without
  embeddings (repairable, but avoidable). Have it call `run_status` first.
- **Don't let it set a context size** (`num_ctx`). Ollama reloads the model
  whenever two clients ask for different sizes. Set the size once on the
  Ollama server (`OLLAMA_CONTEXT_LENGTH`) and leave clients on the default.
- **Prefer the `runbook` tool to guessing.** It returns the documented
  procedure for a symptom.

## Development

- Code: `mcp_server/` (`server.py` registers the tools, `tools.py` gathers
  data, `verdicts.py` holds the decision logic as pure functions).
- A new tool must be read-only. The database connection enforces that for
  SQL; anything else (HTTP calls, files) is on the author.
- Tests: `python -m pytest tests/test_mcp_verdicts.py` covers the decision
  logic without a database.

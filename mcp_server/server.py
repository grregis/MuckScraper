"""MuckScraper MCP server: read-only health and troubleshooting tools.

See docs/MCP.md. Every tool is read-only: the database connection refuses
writes, no tool sends anything to an LLM or wakes the Ollama box, and there
are no tools that fetch, restart, or change configuration.
"""
import hmac
import os

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mcp_server import tools
from mcp_server.context import run_read_only, scrub

INSTRUCTIONS = """\
Read-only diagnostics for a MuckScraper news-aggregator install.
Start with run_status (is a pipeline run in progress?) before anything that
touches Ollama or containers. For "something is wrong", try in order:
run_status, last_run, ollama_health, outage_check, then scheduler_log.
Use runbook(topic) to get the documented procedure before suggesting a fix.
Each check returns a verdict (ok / warning / problem) and a plain summary.
Times are UTC unless a field says local. This server cannot change anything.
"""


def _allowed_hosts():
    hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if not hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                     allowed_origins=[f"https://{h}" for h in hosts] + [f"http://{h}" for h in hosts])


mcp = FastMCP(
    "muckscraper",
    instructions=INSTRUCTIONS,
    stateless_http=True,
    json_response=True,
    transport_security=_allowed_hosts(),
)


async def _call(fn, *args, **kwargs):
    result = await anyio.to_thread.run_sync(lambda: run_read_only(fn, *args, **kwargs))
    return scrub(result)


# --- Runs -------------------------------------------------------------------

@mcp.tool()
async def run_status() -> dict:
    """Is a pipeline run in progress right now? Also: whether a scheduled run was
    missed, other background tasks, and the next scheduled runs. Check this
    before restarting anything or sending requests to Ollama."""
    return await _call(tools.run_status)


@mcp.tool()
async def last_run() -> dict:
    """Summary of the most recent pipeline run: type (full or fetch-only),
    duration, articles in and stored, each step's status, and Ollama's state."""
    return await _call(tools.last_run)


@mcp.tool()
async def run_history(runs: int = 10) -> dict:
    """Duration and volume of the last N runs (max 40), with slow or thin runs
    flagged against the median."""
    return await _call(tools.run_history, runs)


@mcp.tool()
async def scheduler_log(kind: str = "errors", minutes: int = 180, max_lines: int = 100,
                        container: str = "scheduler") -> dict:
    """Filtered log lines from the scheduler (or app) container. kind is one of:
    errors, warnings, guards (rejected headlines/summaries), dedupe (edition
    duplicate skips), edition, ollama, search, runs, all."""
    return await _call(tools.scheduler_log, kind, minutes, max_lines, container)


# --- Ollama -----------------------------------------------------------------

@mcp.tool()
async def ollama_health() -> dict:
    """Ollama's state without waking the box or running inference: answering,
    asleep after a run (normal), service down, hung, or models partly on CPU."""
    return await _call(tools.ollama_health)


# --- Content ----------------------------------------------------------------

@mcp.tool()
async def edition(edition_id: int | None = None) -> dict:
    """An edition's lineup (latest published if no id): rank, headline, article
    and outlet counts, left/center/right mix, and flags such as single-outlet
    stories or missing summaries."""
    return await _call(tools.edition, edition_id)


@mcp.tool()
async def outage_check(hours: int = 24) -> dict:
    """Signs that a window was processed without a working LLM: articles with
    no embedding, articles tagged only 'Other', and a jump in single-article
    stories. Says whether the scoped repair recipe is needed."""
    return await _call(tools.outage_check, hours)


@mcp.tool()
async def quality_report(since: str | None = None) -> dict:
    """The fetch-quality evidence pack (headlines, summaries, grouping, bias,
    editions) since an ISO time (UTC), or since the last run's start. Lists are
    trimmed to 10 items. Can take tens of seconds."""
    return await _call(tools.quality_report, since)


@mcp.tool()
async def get_story(story_id: int) -> dict:
    """One story: headline, summary, analysis, topics, its articles (source,
    bias side, scrape status) and the editions it appeared in."""
    return await _call(tools.get_story, story_id)


@mcp.tool()
async def get_article(article_id: int) -> dict:
    """One article: source, outlet bias, topics, scrape outcome, grouping
    method, a short content preview and its summary."""
    return await _call(tools.get_article, article_id)


@mcp.tool()
async def search(query: str, kind: str = "stories", days: int = 7, limit: int = 20) -> dict:
    """Search stories or articles (kind='articles') from the last N days
    (days=0 for all time). Uses Meilisearch, or a SQL match if it is down."""
    return await _call(tools.search, query, kind, days, limit)


@mcp.tool()
async def scrape_health(hours: int = 24) -> dict:
    """Scrape outcomes in a window, with blocked and failed articles broken
    down by domain, and the scrape blocklist size."""
    return await _call(tools.scrape_health, hours)


@mcp.tool()
async def bias_coverage() -> dict:
    """How many outlets have a bias rating (AllSides or model), how many are
    unrated or abandoned after repeated failures, and recent unrated outlets."""
    return await _call(tools.bias_coverage)


# --- Infrastructure ---------------------------------------------------------

@mcp.tool()
async def containers() -> dict:
    """Container states, start times and restart counts, free disk space, and
    the database size."""
    return await _call(tools.containers)


@mcp.tool()
async def search_health() -> dict:
    """Whether Meilisearch is up, its document counts against the database,
    and the result of the last run's search index update."""
    return await _call(tools.search_health)


@mcp.tool()
async def code_live_check() -> dict:
    """Whether the running app and scheduler have the newest code loaded, or
    started before the latest change on disk (and so need a restart)."""
    return await _call(tools.code_live_check)


@mcp.tool()
async def runbook(topic: str | None = None) -> dict:
    """The documented procedure for a topic (e.g. 'ollama unreachable',
    'restart scheduler', 'outage repair'), from the project's docs. With no
    topic, lists the sections available."""
    return await _call(tools.runbook, topic)


# --- HTTP app with bearer-token auth -----------------------------------------

class BearerAuth:
    """ASGI middleware: every HTTP request needs 'Authorization: Bearer <token>'."""

    def __init__(self, app, token):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            header = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(header, self.expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)


def http_app():
    token = os.environ.get("MCP_TOKEN", "").strip()
    if len(token) < 16:
        raise SystemExit("MCP_TOKEN must be set (16+ characters) to serve over HTTP. See docs/MCP.md.")
    return BearerAuth(mcp.streamable_http_app(), token)

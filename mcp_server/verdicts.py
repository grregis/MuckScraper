"""Pure decision logic for the MCP tools: no database, no network.

Everything here takes plain values and returns plain values, so tests/ can
cover it with synthetic inputs. The tools in tools.py gather the data and hand
it to these functions; the verdict strings are written for a reader that may
be a small local model, so they say what the state means and what to do next.
"""
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Mirrors news_fetcher/scheduler.py TIMEZONE (hard-coded there too). Not
# imported: importing the scheduler module creates a Flask app at import time.
SCHEDULE_TIMEZONE = "America/New_York"

# Mirrors ORPHANED_TASK_STALE_AFTER in aggregator/blueprints/admin/__init__.py:
# a "running" flag older than this is a dead process, not a live run.
RUN_STALE_AFTER = timedelta(hours=3)

OK, WARNING, PROBLEM = "ok", "warning", "problem"


def parse_iso(value):
    """Naive-UTC datetime from an ISO string (tz-aware strings converted)."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# ---------------------------------------------------------------------------
# Run status and schedule
# ---------------------------------------------------------------------------

def run_in_progress(status_payload, now):
    """(running, stale) for a fetch_run_status_v1 payload."""
    if not status_payload or status_payload.get("status") != "running":
        return False, False
    started = parse_iso(status_payload.get("started_at"))
    if started is None:
        return True, False
    if now - started > RUN_STALE_AFTER:
        return False, True
    return True, False


def upcoming_runs(schedule_rows, now, count=4):
    """Next scheduled runs as naive UTC datetimes.

    schedule_rows: iterable of (hour, run_full_pipeline) for active rows, hours
    in SCHEDULE_TIMEZONE.
    """
    tz = ZoneInfo(SCHEDULE_TIMEZONE)
    local_now = now.replace(tzinfo=timezone.utc).astimezone(tz)
    rows = sorted(set(schedule_rows))
    out = []
    for day in range(0, 3):
        date = (local_now + timedelta(days=day)).date()
        for hour, full in rows:
            when = datetime(date.year, date.month, date.day, hour, tzinfo=tz)
            if when > local_now:
                out.append({
                    "at_utc": when.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes"),
                    "at_local": when.strftime("%Y-%m-%d %H:%M %Z"),
                    "type": "full" if full else "fetch-only",
                })
    out.sort(key=lambda r: r["at_utc"])
    return out[:count]


def latest_slot_before(schedule_rows, now):
    """Most recent scheduled slot at or before now (naive UTC), or None."""
    tz = ZoneInfo(SCHEDULE_TIMEZONE)
    local_now = now.replace(tzinfo=timezone.utc).astimezone(tz)
    best = None
    for day in (0, -1):
        date = (local_now + timedelta(days=day)).date()
        for hour, _full in schedule_rows:
            when = datetime(date.year, date.month, date.day, hour, tzinfo=tz)
            if when <= local_now and (best is None or when > best):
                best = when
    return best.astimezone(timezone.utc).replace(tzinfo=None) if best else None


def run_status_verdict(running, stale, status_payload, last_fetch, latest_slot, other_operations, now):
    if running:
        kind = "full" if (status_payload or {}).get("run_full_pipeline") else "fetch-only"
        started = parse_iso(status_payload.get("started_at"))
        mins = int((now - started).total_seconds() // 60) if started else None
        return WARNING, (
            f"A {kind} run is in progress" + (f" ({mins} min so far)" if mins is not None else "")
            + ". Do not restart scheduler or app, and do not send your own requests to Ollama"
            " until it finishes: they force model swaps and slow the run."
        )
    notes = []
    level = OK
    if stale:
        level = WARNING
        notes.append(
            "fetch_run_status_v1 says 'running' but started over 3 hours ago: the process "
            "probably died (restart or crash) and the flag was never cleared. Nothing is "
            "actually running; correct the flag by hand (TROUBLESHOOTING: 'Run status out of sync')."
        )
    if latest_slot and (last_fetch is None or last_fetch < latest_slot):
        level = WARNING
        notes.append(
            f"The scheduled run at {latest_slot.isoformat(timespec='minutes')} UTC has not happened "
            "(last completed fetch is older). The scheduler may be stopped, or it will catch the "
            "slot up when it next starts."
        )
    if other_operations:
        level = WARNING if level == OK else level
        notes.append("Other background operations are running: " + ", ".join(other_operations) + ".")
    if not notes:
        notes.append("Idle. Safe to restart containers; nothing is running.")
    return level, " ".join(notes)


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------

def history_flags(entries):
    """Flag runs whose duration or stored count is far from the median."""
    durations = sorted(e["duration_min"] for e in entries if e.get("duration_min") is not None)
    stored = sorted(e["stored"] for e in entries if e.get("stored") is not None)
    med_d = durations[len(durations) // 2] if durations else None
    med_s = stored[len(stored) // 2] if stored else None
    flags = []
    for e in entries:
        if med_d and e.get("duration_min") is not None and e["duration_min"] > 1.75 * med_d:
            flags.append(f"{e['finished_at']}: slow run ({e['duration_min']} min vs median {med_d})")
        if med_s and e.get("stored") is not None and e["stored"] < 0.4 * med_s:
            flags.append(f"{e['finished_at']}: few articles stored ({e['stored']} vs median {med_s})")
        if e.get("status") not in (None, "ok"):
            flags.append(f"{e['finished_at']}: status {e['status']}")
    return {"median_duration_min": med_d, "median_stored": med_s, "flags": flags}


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def ollama_verdict(probe, last_run_ollama, suspend_fired_after_last_run, expected_context=None):
    """probe: {"result": "ok"|"refused"|"timeout"|"hung"|"error"|"not_configured",
    "models": [{"name", "size", "size_vram", "context_length"}]}.

    expected_context: the server's configured context (OLLAMA_CONTEXT_LENGTH on
    the Ollama host). MuckScraper never sends num_ctx, so a model loaded at any
    other size was loaded by another client.
    """
    result = probe.get("result")
    if result == "not_configured":
        return OK, "OLLAMA_HOST is not set; this install does not use Ollama."
    if result == "ok":
        models = probe.get("models") or []
        partial = [m["name"] for m in models if m.get("size") and (m.get("size_vram") or 0) < m["size"]]
        if partial:
            return WARNING, (
                "Ollama answers, but these models are partly on CPU (size_vram < size), so every "
                "call is slow: " + ", ".join(partial) + ". Usually the GPU is short of memory."
            )
        foreign = [f"{m['name']} ({m['context_length']})" for m in models
                   if expected_context and m.get("context_length") and m["context_length"] != expected_context]
        if foreign:
            return WARNING, (
                f"Another client is using Ollama: loaded at a context size other than the configured "
                f"{expected_context}: " + ", ".join(foreign) + ". Ollama serves one request per model at a "
                "time, so a long request from that client makes the pipeline's calls queue and time out, "
                "and a different context size forces model reloads. Keep other clients off Ollama during "
                "runs (run_status) and don't let them set num_ctx."
            )
        wedged = (last_run_ollama or {}).get("inference_wedged")
        text = "Ollama answers and every loaded model is fully on the GPU." if models else (
            "Ollama answers; no model is loaded right now (normal between runs, models load on demand)."
        )
        if wedged:
            return WARNING, text + (
                " But the last run marked inference as wedged (HTTP up, real calls failing). "
                "If that persists, the box needs a reboot, not a service restart."
            )
        return OK, text
    if result == "timeout":
        if suspend_fired_after_last_run:
            return OK, (
                "No answer, and the end-of-run suspend webhook fired after the last run: the box "
                "is asleep, which is normal between runs. Do not wake it just to check."
            )
        return PROBLEM, (
            "No answer, and no suspend was recorded after the last run. The box may be off the "
            "network or wedged. Check the probe log and the ARP entry (MAC vs OLLAMA_MAC: another "
            "device may hold the address) before anything else."
        )
    if result == "refused":
        return PROBLEM, (
            "The host is up but nothing listens on Ollama's port: the Ollama service is not "
            "running (on a systemd --user install, check that lingering is enabled)."
        )
    if result == "hung":
        return PROBLEM, (
            "The port accepts connections but Ollama does not answer even /api/version: the "
            "service is hung. Seen when the desktop was in use during a run; a reboot fixes it."
        )
    return PROBLEM, f"Ollama check failed: {probe.get('error') or result}."


# ---------------------------------------------------------------------------
# Outage window
# ---------------------------------------------------------------------------

def outage_verdict(total, without_embedding, other_only, singleton_share, baseline_singleton_share):
    if total == 0:
        return WARNING, "No articles were stored in this window. Check whether runs happened (run_history)."
    notes, level = [], OK
    if without_embedding:
        level = PROBLEM
        notes.append(
            f"{without_embedding} of {total} articles have no embedding, so they could not be grouped. "
            "This is the signature of an Ollama outage during a run. Repair with the scoped recipe "
            "(embed, reclassify, regroup), never the global helpers."
        )
    if total >= 20 and other_only / total > 0.25:
        level = PROBLEM if level == PROBLEM else WARNING
        notes.append(f"{other_only} of {total} articles are tagged only 'Other' (classification likely failed).")
    if baseline_singleton_share and singleton_share > baseline_singleton_share + 0.15:
        level = PROBLEM if level == PROBLEM else WARNING
        notes.append(
            f"Singleton share {singleton_share:.0%} vs {baseline_singleton_share:.0%} over 7 days: grouping looks degraded."
        )
    if not notes:
        notes.append("No sign of an outage in this window.")
    return level, " ".join(notes)


# ---------------------------------------------------------------------------
# Code freshness
# ---------------------------------------------------------------------------

def code_live_verdict(container_started, newest_change):
    """Each container: does its process predate the newest code change?"""
    out = {}
    for name, started in container_started.items():
        if started is None or newest_change is None:
            out[name] = "unknown"
        elif started < newest_change:
            out[name] = (
                f"STALE: started {started.isoformat(timespec='minutes')} UTC, before the newest code "
                f"change at {newest_change.isoformat(timespec='minutes')} UTC. Restart it (only when "
                "run_status says idle) to load the change."
            )
        else:
            out[name] = "current: started after the newest code change"
    return out


# ---------------------------------------------------------------------------
# Runbook search
# ---------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,4})\s+(.*)")
_BOLD_BULLET = re.compile(r"^- \*\*(.+?)\*\*")
_WORD = re.compile(r"[a-z0-9_]+")
_STOP = {"the", "a", "an", "is", "to", "of", "and", "or", "in", "on", "for", "how", "what",
         "why", "do", "does", "i", "it", "my", "with", "when", "not"}


def split_sections(text, source):
    """Split markdown into sections at headings and at top-level bold bullets
    (CLAUDE.md keeps most guides as '- **Title:** ...' paragraphs)."""
    sections, title, lines = [], None, []

    def flush():
        if title and lines:
            sections.append({"source": source, "title": title, "text": "\n".join(lines).strip()})

    for line in text.splitlines():
        m = _HEADING.match(line) or _BOLD_BULLET.match(line)
        if m:
            flush()
            title = (m.group(2) if m.re is _HEADING else m.group(1)).strip(" :*")
            lines = [line]
        elif title:
            lines.append(line)
    flush()
    return sections


def _terms(text):
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


def search_sections(sections, query, limit=3):
    terms = _terms(query)
    if not terms:
        return []
    scored = []
    for s in sections:
        title_terms = _terms(s["title"])
        body = s["text"].lower()
        score = 3 * len(terms & title_terms) + sum(1 for t in terms if t in body)
        if score:
            scored.append((score, s))
    scored.sort(key=lambda p: -p[0])
    return [s for _, s in scored[:limit]]


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_SECRET_ENV = re.compile(r"(KEY|SECRET|PASSWORD|TOKEN|PASS)$")
_INLINE = [
    re.compile(r"(?i)((?:api[_-]?key|apikey|token|secret|password)=)([^&\s\"']+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{8,})"),
    re.compile(r"(postgres(?:ql)?://[^:/\s]+:)([^@\s]+)(@)"),
]


def secret_values(environ=None):
    environ = os.environ if environ is None else environ
    return sorted(
        {v for k, v in environ.items() if _SECRET_ENV.search(k) and v and len(v) >= 6},
        key=len, reverse=True,
    )


def redact(text, secrets):
    for value in secrets:
        text = text.replace(value, "***")
    for pattern in _INLINE:
        text = pattern.sub(lambda m: m.group(1) + "***" + (m.group(3) if m.re.groups >= 3 else ""), text)
    return text

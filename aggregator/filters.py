import re

from aggregator.display_time import local_datetime, short_datetime
from aggregator.html_safety import clean_html, plain_text_br

# Section labels article summaries are written with (Smart Brevity, and the
# older "What's happening" format). Only these are stripped, so a line that
# starts with a quoted speaker ("Trump: ...") keeps its name.
SUMMARY_LABELS = (
    "The big picture", "Why it matters", "Quick analysis", "What's next",
    "What's happening", "Between the lines", "The bottom line", "Bottom line",
)
_SUMMARY_LABEL_RE = re.compile(
    r"^\**\s*(?:%s)\s*:\s*\**\s*" % "|".join(re.escape(label) for label in SUMMARY_LABELS),
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^(?:[•\-*]|\d+[.)])\s+")


def summary_paragraph(summary):
    """An article summary as one plain paragraph: section labels and bullet
    markers dropped, the parts joined with spaces (Regis, 2026-10-18)."""
    if not summary:
        return ""
    parts = []
    for line in summary.splitlines():
        text = _BULLET_RE.sub("", _SUMMARY_LABEL_RE.sub("", line.strip())).strip()
        if not text:
            continue
        if text[-1] not in ".!?\"'”’)":
            text += "."
        parts.append(text)
    return " ".join(parts)


def register_filters(app):
    # Untrusted text (scraped HTML, LLM output) must go through one of these,
    # never `| safe`. See aggregator/html_safety.py.
    app.add_template_filter(clean_html, "clean_html")
    app.add_template_filter(plain_text_br, "plain_text_br")

    # Stored timestamps are naive UTC; show them in DISPLAY_TIMEZONE.
    app.add_template_filter(local_datetime, "local_datetime")
    app.add_template_filter(short_datetime, "short_datetime")

    # Article summaries read as one paragraph, without section labels.
    app.add_template_filter(summary_paragraph, "summary_paragraph")

    @app.template_filter("get_whats_happening")
    def get_whats_happening(summary):
        if not summary:
            return None
        try:
            start_marker = "What's happening:"
            end_marker = "What's next:"
            if start_marker in summary:
                parts = summary.split(start_marker)
                content = parts[1]
                if end_marker in content:
                    content = content.split(end_marker)[0]
                return content.strip()
        except Exception:
            pass
        return None

    @app.template_filter("get_the_story")
    def get_the_story(report):
        if not report:
            return None
        try:
            markers = [
                "The story:",
                "What happened:",
                "The discovery or development:",
                "The discovery:",
                "The development:"
            ]
            start_marker = None
            for m in markers:
                if m in report:
                    start_marker = m
                    break
            if not start_marker:
                return None
            next_sections = [
                "How the left is covering it:", "Why it matters:",
                "What's the game or company:", "Key performances:",
                "Market impact:", "What experts are saying:",
                "Key details:", "Different perspectives:",
                "What the research shows:", "What the coverage is saying:",
                "The bigger picture:", "Market impact:"
            ]
            content = report.split(start_marker)[1]
            end_pos = len(content)
            for next_m in next_sections:
                pos = content.find(next_m)
                if pos != -1 and pos < end_pos:
                    end_pos = pos
            return content[:end_pos].strip()
        except Exception:
            pass
        return None

    @app.template_filter("get_big_picture")
    def get_big_picture(summary):
        if not summary:
            return None
        try:
            start_marker = "The big picture:"
            end_marker = "Why it matters:"
            if start_marker in summary:
                parts = summary.split(start_marker)
                content = parts[1]
                if end_marker in content:
                    content = content.split(end_marker)[0]
                return content.strip()
        except Exception:
            pass
        return summary.strip()

"""The story page's multi-source analysis, split into its labelled sections.

The deep_report prompts (seed_defaults.py, deep_report.*) ask for a fixed set
of "Label: text" paragraphs. The story page lays these out as cards or as
titled subsections (Philip, 2026-10-23; the reader picks one under Appearance
on their profile), so the text is parsed once here instead of in Jinja.

Each section gets a `kind`, which decides where it goes and how it looks:
"lead" (the first section), "left" / "center" / "right" (the politics
prompt's coverage buckets), "next" (What's next) or "other", plus an icon
name from icons.html. Text that doesn't follow the format still renders:
a line with no label continues the section before it, or starts an
unlabelled one.
"""
import re

# The same cutoff the template used: a colon further in than this is part of
# a sentence, not a label.
MAX_LABEL = 35

# Longest L/C/R text, in characters, that still fits the three-column cards.
# Longer than this and the cards stack full width, so a long bucket doesn't
# leave a narrow column towering over the other two.
CARD_COLUMN_MAX = 650
# The same for the pair of callouts under them.
CALLOUT_PAIR_MAX = 900

_BUCKETS = (
    ("left", re.compile(r"\bthe left\b|\bleft[- ]leaning\b")),
    ("center", re.compile(r"\bthe cent(er|re)\b")),
    ("right", re.compile(r"\bthe right\b|\bright[- ]leaning\b")),
)
_EMPTY_BUCKET = re.compile(r"^no (left|right|center|centre)[- \w]* sources were found", re.I)

# Label words → icon, first match wins. Covers the labels of every
# deep_report prompt; anything else gets file-text.
_ICONS = (
    ("contested", "scale"),
    ("perspectives", "scale"),
    ("being covered", "scale"),
    ("missing", "eye-off"),
    ("unknown", "eye-off"),
    ("next", "circle-arrow-right"),
    ("why it matters", "zap"),
    ("matters", "zap"),
    ("experts", "message-square"),
    ("analysts", "message-square"),
    ("numbers", "table"),
    ("research shows", "list"),
    ("key details", "list"),
    ("market", "gauge"),
    ("risks", "circle-alert"),
    ("picture", "layers"),
    ("impact", "users"),
    ("involved", "users"),
    ("performances", "zap"),
)


def _icon_for(label):
    low = label.lower()
    for needle, name in _ICONS:
        if needle in low:
            return name
    return "file-text"


def _kind_for(label):
    low = label.lower()
    if low.startswith("what's next") or low.startswith("what’s next"):
        return "next"
    for bucket, pattern in _BUCKETS:
        if pattern.search(low):
            return bucket
    return "other"


def _split(text):
    """[(label or None, [paragraph, ...]), ...] from the raw report."""
    sections = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        head, sep, rest = line.partition(":")
        if sep and 0 < len(head.strip()) < MAX_LABEL and rest.strip():
            sections.append((head.strip(), [rest.strip()]))
        elif sep and 0 < len(head.strip()) < MAX_LABEL and not rest.strip():
            # A label on a line of its own; its text follows.
            sections.append((head.strip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            sections.append((None, [line]))
    return [(label, paras) for label, paras in sections if paras]


def parse(text):
    """The report's sections, in order, as dicts:
    label, paragraphs, kind, icon, empty (an L/C/R bucket with no sources)."""
    out = []
    for i, (label, paras) in enumerate(_split(text)):
        kind = _kind_for(label) if label else "other"
        if i == 0 and kind == "other":
            kind = "lead"
        out.append({
            "label": label,
            "paragraphs": paras,
            "kind": kind,
            "icon": "newspaper" if kind == "lead" else _icon_for(label or ""),
            "empty": kind in ("left", "center", "right") and bool(_EMPTY_BUCKET.match(paras[0])),
        })
    return out


def _length(section):
    return sum(len(p) for p in section["paragraphs"])


def layout(text):
    """What the story page needs to lay the report out:

    sections  every section, in order (the Sections layout uses these)
    lead      the first section, if it is a plain one
    buckets   the L/C/R sections that have coverage, left to right
    empty     the L/C/R sections whose bucket had no sources
    others    every other section but What's next, in order
    next      the What's next section, if any
    stacked   True when a bucket is too long for the three-column cards
    pair_stacked  True when the callouts are too long to sit side by side
    """
    sections = parse(text)
    lead = sections[0] if sections and sections[0]["kind"] == "lead" else None
    order = {"left": 0, "center": 1, "right": 2}
    lcr = sorted((s for s in sections if s["kind"] in order), key=lambda s: order[s["kind"]])
    buckets = [s for s in lcr if not s["empty"]]
    others = [s for s in sections if s["kind"] == "other"]
    nxt = next((s for s in sections if s["kind"] == "next"), None)
    return {
        "sections": sections,
        "lead": lead,
        "buckets": buckets,
        "empty": [s for s in lcr if s["empty"]],
        "others": others,
        "next": nxt,
        "stacked": any(_length(s) > CARD_COLUMN_MAX for s in buckets),
        "pair_stacked": any(_length(s) > CALLOUT_PAIR_MAX for s in others),
    }

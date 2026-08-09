import re
from urllib.parse import urlparse


ROUNDUP_TITLE_PATTERNS = (
    re.compile(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?:"),
    re.compile(r"\b(?:morning|afternoon|evening|night)\s+(?:rundown|roundup|briefing)\b", re.IGNORECASE),
    re.compile(r"\b(?:daily|news)\s+(?:rundown|roundup|briefing)\b", re.IGNORECASE),
    re.compile(r"\btop stories\b", re.IGNORECASE),
    re.compile(r"\bwhat to know\b", re.IGNORECASE),
)

ROUNDUP_URL_HINTS = (
    "morning-rundown",
    "evening-rundown",
    "nightly-rundown",
    "roundup",
    "briefing",
    "top-stories",
)

LOW_VALUE_URL_HINTS = (
    "/video/",
    "/videos/",
    "/watch/",
    "/live/",
    "/live-updates/",
    "/liveblog/",
    "/podcast/",
    "/podcasts/",
    "/audio/",
    "/listen/",
    "/photos/",
    "/photo/",
    "/gallery/",
    "/galleries/",
    "/sounds/",
    "/iplayer/",
    "/newsletters/",
    "/newsletter/",
    "/briefings/",
    "/opinion/letters/",
)

# Sports-betting tipster content: odds, picks, parlays, prop bets. It is not
# news, but it arrives through ordinary sports feeds and has reached published
# editions (2026-08-08 evening, rank 8).
#
# Deliberately multi-word phrases only. Single words are unusable here --
# BLOCKED_TITLE_KEYWORDS does bare substring matching, so "picks" would kill
# "Trump picks Supreme Court nominee" and "odds" would kill "odds of a
# recession". These patterns were checked against every matching article in the
# database (203 across 20 outlets) with no false positives.
#
# URL paths are no help for this: dedicated tipster sites file under generic
# /mlb/ and /nba/ paths, and mainstream outlets scatter betting content across
# /sports/, /story/ and section paths rather than a consistent /betting/ prefix.
BETTING_TITLE_PATTERNS = (
    re.compile(r"\bbest bets\b", re.IGNORECASE),
    re.compile(r"\bpicks and predictions\b", re.IGNORECASE),
    re.compile(r"\bpredictions?,?\s+odds\b", re.IGNORECASE),
    re.compile(r"\bodds,?\s+picks\b", re.IGNORECASE),
    re.compile(r"\bparlay\b", re.IGNORECASE),
    re.compile(r"\bmoneyline\b", re.IGNORECASE),
    # NB: "against the spread" is deliberately NOT here. It is a real betting
    # term, but it matched 0 of 70,555 articles (so adds no recall) while
    # "against the spread of a virus / of misinformation" is ordinary news
    # phrasing -- pure downside.
    re.compile(r"\bbetting (?:odds|preview|splits|guide|lines)\b", re.IGNORECASE),
    re.compile(r"\bexpert picks\b", re.IGNORECASE),
    re.compile(r"\bdfs lineup\b", re.IGNORECASE),
    re.compile(r"\bprop bets?\b", re.IGNORECASE),
)

LOW_VALUE_TITLE_PATTERNS = (
    re.compile(r"^\s*(?:watch|video|listen)\s*:", re.IGNORECASE),
    re.compile(r"\bletters?\s+to\s+the\s+editor\b", re.IGNORECASE),
    re.compile(r"\blive updates?\b", re.IGNORECASE),
    re.compile(r"\bphoto(?:s| gallery)?\b", re.IGNORECASE),
    re.compile(r"\bgallery\b", re.IGNORECASE),
    re.compile(r"\bnewsletter\b", re.IGNORECASE),
)


def is_roundup_article(title=None, url=None):
    normalized_title = (title or "").strip()
    if any(pattern.search(normalized_title) for pattern in ROUNDUP_TITLE_PATTERNS):
        return True

    parsed_path = urlparse(url or "").path.lower()
    return any(hint in parsed_path for hint in ROUNDUP_URL_HINTS)


# Minimum characters of scraped body text for an article to count as its
# outlet's independent corroboration of a story.
#
# Below this an article is almost always a blocked paywall (stored with 0
# chars) or an RSS snippet -- it carries the outlet's name but none of its
# reporting, so counting it inflates "N outlets reported this". In one sampled
# edition, 8 of 18 stories advertised multiple outlets while having only a
# single source above the floor; one showed three outlets for a story only one
# outlet had actually written up (17,114 chars vs 279 vs 138).
#
# This gates *corroboration counting only*. Articles below the floor are still
# stored, still displayed, and still link out to their source.
INDEPENDENT_CONTENT_FLOOR = 300


def is_independent_source(article):
    """Whether an article carries enough scraped content to count as its
    outlet's independent corroboration of a story."""
    return len(article.content or "") >= INDEPENDENT_CONTENT_FLOOR


def bias_bucket_for_score(score):
    if score is None:
        return "unrated"
    if score <= 1.5:
        return "left"
    if score <= 2.5:
        return "lean_left"
    if score <= 3.5:
        return "center"
    if score <= 4.5:
        return "lean_right"
    return "right"


def is_betting_article(title=None):
    """Sports-betting tipster content (odds/picks/parlays), which is not news."""
    normalized_title = (title or "").strip()
    return any(pattern.search(normalized_title) for pattern in BETTING_TITLE_PATTERNS)


def low_value_article_reason(title=None, url=None):
    if is_roundup_article(title, url):
        return "roundup"

    if is_betting_article(title):
        return "betting"

    normalized_title = (title or "").strip()
    if any(pattern.search(normalized_title) for pattern in LOW_VALUE_TITLE_PATTERNS):
        return "low_value_title"

    parsed = urlparse(url or "")
    parsed_path = parsed.path.lower()

    if any(hint in parsed_path for hint in LOW_VALUE_URL_HINTS):
        return "low_value_url"

    if parsed_path.endswith((".m3u8", ".mp4", ".m4v", ".mov", ".webm")):
        return "video_asset"

    return None

"""Deterministic quality detectors for reviewing generated pipeline output.

Pure functions only: no database, no network, no Flask app context. Everything
here takes plain strings and returns findings, so it can be unit-tested with
synthetic input (see tests/test_quality_checks.py) and reused from anywhere.

The judgment calls -- "is this headline accurate", "should these two articles be
one story" -- deliberately do NOT live here. This module only finds the things
that are mechanically checkable, so that a human review spends its attention on
the things that aren't.

Companion: news_fetcher/quality_report.py does the DB/Langfuse gathering and
calls into these.
"""
import re
import unicodedata


# ---------------------------------------------------------------------------
# Section-label contract
# ---------------------------------------------------------------------------
#
# The deep_report.* prompts mandate an exact set of output labels ("Use EXACTLY
# the labels shown above including the colon"), and aggregator/filters.py parses
# those labels back out to render the site. A report that is well written but
# mislabelled renders EMPTY -- get_the_story() returns None when it recognizes
# none of its start markers -- so label compliance is a reader-visible defect
# and not a cosmetic one.
#
# Labels are derived from the prompt body at runtime rather than hardcoded here.
# Prompt bodies are DB-backed and go live within 60s of an edit with no deploy
# (see CLAUDE.md, "Updating LLM Prompts"), so a hardcoded table here would drift
# silently the first time someone edited a prompt in the admin UI.

# Everything between the "EXACT format" instruction and the trailing rules block
# is the mandated structure. Both markers are present in all five deep_report.*
# prompts as seeded in aggregator/seed_defaults.py.
_FORMAT_START_RE = re.compile(r"using this EXACT format\s*:", re.IGNORECASE)
_FORMAT_END_RE = re.compile(r"^Rules\s*:", re.IGNORECASE | re.MULTILINE)

# A mandated label starts a line and is followed by a bracketed instruction --
# "The story: [2-3 sentences explaining what happened factually]". Requiring the
# bracket is what separates a real label from prose that happens to contain a
# colon.
_LABEL_RE = re.compile(r"^([A-Z][^:\n\[\]]{2,60}?)\s*:\s*\[", re.MULTILINE)


def required_labels_for_prompt(prompt_text):
    """Output labels a prompt mandates, in the order it lists them.

    Returns [] for a prompt that mandates no labelled structure (story_summary
    explicitly forbids labels), which callers must treat as "no contract to
    check" rather than "every label is missing".
    """
    if not prompt_text:
        return []

    start = _FORMAT_START_RE.search(prompt_text)
    body = prompt_text[start.end():] if start else prompt_text

    end = _FORMAT_END_RE.search(body)
    if end:
        body = body[:end.start()]

    labels = []
    for match in _LABEL_RE.finditer(body):
        label = f"{match.group(1).strip()}:"
        if label not in labels:
            labels.append(label)
    return labels


def missing_report_labels(report_text, prompt_text):
    """Mandated labels absent from generated text. [] means fully compliant."""
    required = required_labels_for_prompt(prompt_text)
    if report_text is None:
        return []
    return [label for label in required if label not in report_text]


# Mirrors aggregator/filters.py:get_the_story()'s marker list exactly. Kept as a
# literal copy rather than an import because filters.py registers Jinja filters
# against a live app and cannot be imported from a plain script -- but the two
# lists MUST stay in step, so tests/test_quality_checks.py asserts that.
THE_STORY_START_MARKERS = (
    "The story:",
    "What happened:",
    "The discovery or development:",
    "The discovery:",
    "The development:",
)


def renders_empty_the_story(report_text):
    """True when the site's get_the_story() filter would return None.

    That leaves the story's headline analysis slot blank on the published page,
    which is the concrete reader-visible cost of a label violation.
    """
    if not report_text:
        return True
    return not any(marker in report_text for marker in THE_STORY_START_MARKERS)


# ---------------------------------------------------------------------------
# Text health
# ---------------------------------------------------------------------------

_TERMINAL_PUNCTUATION = tuple('.!?"”’\')]»…')

# A generated paragraph that stops on one of these was cut off mid-thought. Used
# for headlines, which legitimately carry no terminal punctuation and so need a
# different signal entirely.
_DANGLING_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "of", "at", "to",
    "with", "from", "that", "than", "into", "about", "against", "between",
    "while", "because", "be", "been", "has", "have", "had", "would",
    "could", "should", "might", "its", "their", "his", "her", "highly", "very",
})
_PRONOUN_DANGLERS = frozenset({"will", "may"})
_SUBJECT_PRONOUNS = frozenset({"he", "she", "it", "they", "we", "who", "that", "you", "i"})
# Deliberately NOT here, because a real headline can end on them: "in" / "on" /
# "by" / "over" (phrasal verbs: "war drags on", "Rules to Live By"), "is" / "are"
# ("what 'Woke 1.0' is"), "will" / "may" (nouns: "subverting voters' will",
# the month), "more" / "less". Measured 2026-09-29: with them, 232 all-time hits
# of which nearly every one was a legitimate headline.


def looks_truncated(text):
    """Reason a prose passage (summary / report) looks cut off, else None.

    Prose only. Headlines carry no terminal punctuation by convention -- use
    headline_looks_truncated() for those.
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None

    if not stripped.endswith(_TERMINAL_PUNCTUATION):
        last = re.findall(r"[A-Za-z']+", stripped)
        if last and last[-1].lower() in _DANGLING_WORDS:
            return f"ends on dangling word '{last[-1]}' with no terminal punctuation"
        return "no terminal punctuation"
    return None


def headline_looks_truncated(headline):
    """Reason a headline looks cut off, else None.

    A headline has no terminal punctuation, so the only reliable signal is that
    it ends on a function word -- "Trump says he will" rather than "Trump says
    he will veto the bill".
    """
    if not headline:
        return None
    # Digits count as words: "dies at 35" ends on a number, not on "at".
    words = re.findall(r"[\w'-]+", headline.strip())
    if not words:
        return None
    if words[-1].lower() in _DANGLING_WORDS:
        return f"ends on dangling word '{words[-1]}'"
    # "he will" is cut off; "voters' will" and "what it is" are not.
    if (len(words) > 1 and words[-1].lower() in _PRONOUN_DANGLERS
            and words[-2].lower() in _SUBJECT_PRONOUNS):
        return f"ends on dangling '{words[-2]} {words[-1]}'"
    return None


# Words whose trailing period is an abbreviation, not a sentence end. Without
# this "U.S." and "Gov." split a four-sentence summary into nine (2026-10-06).
_ABBREVIATIONS = {
    "u.s", "u.k", "u.n", "e.u", "d.c", "gov", "sen", "rep", "rev", "dr", "mr",
    "mrs", "ms", "st", "jr", "sr", "gen", "lt", "col", "sgt", "capt", "adm",
    "no", "vs", "inc", "corp", "co", "ltd", "a.m", "p.m", "jan", "feb", "aug",
    "sept", "sep", "oct", "nov", "dec", "mt", "ft", "approx", "dept", "est",
}
_SENTENCE_BREAK = re.compile(r"[.!?]['\"\u201d\u2019)]*\s+")


def sentence_count(text):
    """Rough sentence count, for prompts that mandate a range ("3 to 5").
    A period after a known abbreviation or a single initial ("John F.
    Kennedy") does not end a sentence."""
    if not text or not text.strip():
        return 0
    text = text.strip()
    count = 1
    for match in _SENTENCE_BREAK.finditer(text):
        if match.end() >= len(text):
            break
        # A period inside closing quotes ('said "no."') always ends a sentence.
        if text[match.start()] == "." and not match.group()[1:].strip():
            before = text[:match.start()].split()
            word = before[-1].lstrip("(\"'\u201c").lower() if before else ""
            if word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                continue
        count += 1
    return count


# ---------------------------------------------------------------------------
# Acronym casing
# ---------------------------------------------------------------------------
#
# The model sometimes title-cases an acronym -- "Nato Chief Warns", "Gop Senator"
# -- which reads as a typo on the published page. Found 19 in the story table on
# 2026-09-29.
#
# Deliberately curated and deliberately incomplete. Every entry must be a token
# that is NEVER a lowercase English word, because the check can only look at
# casing, not meaning:
#   - "Ice" is excluded: ICE the agency vs. ice the substance are both real.
#   - "Us" is excluded: the pronoun at a sentence start is far commoner.
#   - "Ai" is excluded: Ai Weiwei. "Un" is included but skipped after "Jong".
#   - "Eu" is included: not an English word on its own.
# Add to this only after checking the candidate against real headlines.
ACRONYMS = frozenset({
    "NATO", "FBI", "CIA", "NSA", "DOJ", "DHS", "FEMA", "NASA", "CDC", "FDA",
    "IRS", "EPA", "GOP", "NFL", "NBA", "WNBA", "MLB", "NHL", "NCAA",
    "UEFA", "FIFA", "EU", "UN", "UK", "USA", "NHS", "IMF", "OPEC",
    "CEO", "CFO", "COO", "CTO", "SUV", "GDP", "GPS", "NYPD", "LAPD",
    "TSA", "ICC", "BBC", "CNN", "NPR", "PBS", "ATF", "DEA", "USDA",
})

_WORD_RE = re.compile(r"\b[A-Za-z][a-z]+\b")


def _acronym_casing_spans(text):
    """(start, end) of each Title-case known acronym, with the same rules as
    acronym_cased_tokens()."""
    spans = []
    previous = ""
    for match in _WORD_RE.finditer(text or ""):
        token = match.group(0)
        is_title = token[0].isupper() and token[1:].islower()
        is_name = token.upper() == "UN" and previous.lower() == "jong"
        if is_title and token.upper() in ACRONYMS and not is_name:
            spans.append(match.span())
        previous = token
    return spans


def fix_acronym_casing(text):
    """Upper-case known acronyms the model wrote in Title case ("Gop" -> "GOP").

    Only touches tokens acronym_cased_tokens() would flag, so it inherits the
    curated list's guarantee that none of them is an ordinary English word.
    """
    if not text:
        return text
    for start, end in reversed(_acronym_casing_spans(text)):
        text = text[:start] + text[start:end].upper() + text[end:]
    return text


def acronym_cased_tokens(text):
    """Tokens that are a known acronym written in Title case ("Nato", "Gop")."""
    if not text:
        return []
    found = []
    previous = ""
    for token in _WORD_RE.findall(text):
        # Title case only: "un-American" and "Kim Jong-un" must not match.
        is_title = token[0].isupper() and token[1:].islower()
        # "Kim Jong Un" is a person, not the United Nations.
        is_name = token.upper() == "UN" and previous.lower() == "jong"
        if is_title and token.upper() in ACRONYMS and not is_name and token not in found:
            found.append(token)
        previous = token
    return found


# ---------------------------------------------------------------------------
# Numeric claims
# ---------------------------------------------------------------------------
#
# Catches the magnitude-invention class: on 2026-08-07 a source's "1.7M ladders"
# was published as "Over 17 million" -- a value that appears in no source.
#
# It does NOT catch the 2026-08-10 "$4 in interest" failure, where the source
# said "4 savings account options". The value 4 was present; the meaning was
# wrong. Semantic misreading is out of reach for a deterministic check, and this
# function should not be described as covering it.

_SCALE_WORDS = {
    "hundred": 1e2, "thousand": 1e3, "k": 1e3,
    "million": 1e6, "m": 1e6, "mn": 1e6,
    "billion": 1e9, "b": 1e9, "bn": 1e9,
    "trillion": 1e12, "t": 1e12,
}

# Word scales may follow a space ("1.7 million"); single-letter suffixes must be
# attached ("1.7M"), or "3 T-shirts" and "5 m tall" would read as 3 trillion.
_NUMBER_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)"
    r"(?:\s+(hundred|thousand|million|billion|trillion|bn|mn)|([kmbt]))?\b",
    re.IGNORECASE,
)


def extract_numeric_values(text):
    """Numeric values mentioned in text, scale suffixes/words resolved.

    "1.7M", "1.7 million" and "1,700,000" all yield 1700000.0.
    """
    if not text:
        return set()
    values = set()
    for raw, word_scale, letter_scale in _NUMBER_RE.findall(text):
        scale = word_scale or letter_scale
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if scale:
            value *= _SCALE_WORDS[scale.lower()]
        values.add(value)
    return values


def _two_sig_figs(value):
    """Round to two significant figures, so a headline's "about 4,900" still
    matches a source's 4,873 instead of being reported as invented."""
    if value == 0:
        return 0.0
    from math import floor, log10
    magnitude = floor(log10(abs(value)))
    factor = 10 ** (magnitude - 1)
    return round(value / factor) * factor


def unsupported_numbers(text, source_texts, skip_years=True):
    """Values in `text` that appear in none of `source_texts`.

    Matching allows the generated value to be a two-significant-figure rounding
    of a source value, since headlines round legitimately. Years are skipped by
    default -- they are rarely the invented figure and frequently appear only in
    a dateline the sources don't repeat.
    """
    claimed = extract_numeric_values(text)
    if not claimed:
        return []

    supported = set()
    for source in source_texts or []:
        supported |= extract_numeric_values(source)
    supported_rounded = {_two_sig_figs(v) for v in supported}

    unsupported = []
    for value in sorted(claimed):
        if skip_years and value == int(value) and 1900 <= value <= 2100:
            continue
        if value in supported or _two_sig_figs(value) in supported_rounded:
            continue
        unsupported.append(value)
    return unsupported


# ---------------------------------------------------------------------------
# Entity claims
# ---------------------------------------------------------------------------

# Capitalized words that start sentences or are otherwise not proper nouns. Kept
# small on purpose: the check reports candidates for a human to eyeball, so a
# false positive costs a glance, and over-filtering costs a missed invention.
_ENTITY_STOPWORDS = frozenset({
    "The", "A", "An", "This", "That", "These", "Those", "It", "He", "She",
    "They", "We", "I", "But", "And", "Or", "If", "When", "While", "After",
    "Before", "However", "Meanwhile", "Now", "Then", "There", "Here", "What",
    "Why", "How", "Who", "Which", "In", "On", "At", "To", "For", "With",
    "From", "By", "As", "Of", "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday", "January", "February", "March", "April",
    "May", "June", "July", "August", "September", "October", "November",
    "December",
})

_ENTITY_RE = re.compile(r"\b([A-Z][a-z]{2,})\b")


def _fold(text):
    """Casefold and strip accents, so "Zelenskyy" vs "Zelensky" aside, an
    accented source spelling still matches an unaccented generated one."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def unsupported_entities(text, source_texts):
    """Capitalized words in `text` present in none of `source_texts`.

    A candidate list for review, not a verdict: a summary may legitimately
    paraphrase a country as "Britain" where every source wrote "UK". Treat a hit
    as something to look at, which is why the stopword list stays minimal.
    """
    if not text:
        return []
    haystack = " ".join(_fold(s) for s in (source_texts or []))
    if not haystack.strip():
        return []

    found = []
    for token in _ENTITY_RE.findall(text):
        if token in _ENTITY_STOPWORDS or token in found:
            continue
        if _fold(token) not in haystack:
            found.append(token)
    return found

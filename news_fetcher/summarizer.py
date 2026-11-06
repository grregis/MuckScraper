# muckscraperHeadlinesGoogleNEW/news_fetcher/summarizer.py
# news_fetcher/summarizer.py

import os
import re
import logging
from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context

from news_fetcher import llm_client
from news_fetcher.llm_client import check_llm_status as check_ollama_status
from news_fetcher.prompt_registry import render_prompt
from news_fetcher.quality_checks import fix_current_officeholder_titles

logger = logging.getLogger(__name__)


def _fix_titles(text, source_texts, what):
    """fix_current_officeholder_titles(), logged when it changes something.

    Every writing prompt forbids titles the sources don't give, and the model
    still writes "former President Trump" (training years) now and then.
    """
    fixed = fix_current_officeholder_titles(text, source_texts)
    if fixed != text:
        logger.info("  [Summarizer] Fixed 'former' officeholder title in %s", what)
        langfuse_context.update_current_observation(metadata={"fixed_former_title": True})
    return fixed

langfuse = Langfuse(
    public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
    host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
)

# Every generate_text() call in this module deliberately stays on the default
# quality tier: summaries and deep reports are the text readers actually see,
# so they keep the main model even when OLLAMA_FAST_MODEL is set for the
# pipeline's mechanical calls (grouping, classification, headlines).
MODEL = os.environ.get("OLLAMA_MODEL", "")

# Checks the provider that actually serves summaries. Under split routing the
# global provider can be a cloud model while Ollama runs the fast tier, in
# which case an unset OLLAMA_MODEL is irrelevant here -- and, more importantly,
# the reverse: warning off LLM_PROVIDER would go quiet exactly when summaries
# are the thing misconfigured.
if llm_client.provider_for_tier(llm_client.TIER_QUALITY) == "ollama" and not MODEL:
    logging.warning("OLLAMA_MODEL environment variable is not set. All summarization will fail.")


def strip_html(text):
    """Strip HTML tags and clean up whitespace for LLM input."""
    if not text:
        return ""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&nbsp;', ' ').replace('&quot;', '"').replace('&#39;', "'")
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


STORY_FILTER_STOPWORDS = {
    "about", "after", "again", "against", "amid", "among", "and", "are",
    "around", "before", "being", "but", "can", "could", "did", "does",
    "during", "for", "from", "has", "have", "her", "his", "how", "into",
    "its", "may", "more", "new", "news", "not", "over", "says", "she",
    "that", "the", "their", "this", "through", "with", "what", "when",
    "where", "who", "why", "will", "you", "your",
}


def _story_filter_tokens(text):
    tokens = re.findall(r"[a-z0-9][a-z0-9'-]{2,}", (text or "").lower())
    return {
        token.strip("-'")
        for token in tokens
        if token.strip("-'") and token.strip("-'") not in STORY_FILTER_STOPWORDS
    }


def _article_filter_text(article):
    return article.title or ""


def _jaccard(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _select_story_prompt_articles(story, limit=10):
    """
    Return articles to use for story-level LLM prompts.

    This is intentionally conservative: it only removes clear outliers from
    multi-source clusters and does not alter persisted story membership.

    story.articles has no defined order, so it's sorted most-recent-first
    before truncating to `limit` — otherwise a story with more than `limit`
    articles can silently drop its newest developments from the prompt if
    they don't happen to land in the collection's native DB order.
    """
    from datetime import datetime as _dt
    from aggregator.article_copies import original_articles
    # A copy of another article in the story adds no reporting, and the
    # model would otherwise report it as a second outlet's coverage.
    sorted_articles = sorted(original_articles(story.articles), key=lambda a: getattr(a, "date", None) or _dt.min, reverse=True)
    articles = sorted_articles[:limit]
    if len(articles) < 3:
        return articles, []

    token_sets = [_story_filter_tokens(_article_filter_text(article)) for article in articles]
    story_tokens = _story_filter_tokens(" ".join([story.headline or "", story.title or ""]))

    # Pick the article that best represents the cluster based on title/content
    # overlap with the story label and neighboring articles.
    anchor_index = 0
    best_score = -1.0
    for idx, tokens in enumerate(token_sets):
        peer_scores = [
            _jaccard(tokens, other)
            for other_idx, other in enumerate(token_sets)
            if other_idx != idx
        ]
        score = (sum(peer_scores) / len(peer_scores)) if peer_scores else 0.0
        if story_tokens:
            score += _jaccard(tokens, story_tokens)
        if score > best_score:
            anchor_index = idx
            best_score = score

    anchor_tokens = token_sets[anchor_index]
    selected = []
    excluded = []
    for article, tokens in zip(articles, token_sets):
        anchor_similarity = _jaccard(tokens, anchor_tokens)
        story_similarity = _jaccard(tokens, story_tokens)
        shared_anchor_terms = len(tokens & anchor_tokens)
        shared_story_terms = len(tokens & story_tokens)
        include = (
            article is articles[anchor_index] or
            anchor_similarity >= 0.08 or
            story_similarity >= 0.08 or
            shared_anchor_terms >= 3 or
            shared_story_terms >= 2
        )
        if include:
            selected.append(article)
        else:
            excluded.append(article)

    # Avoid starving the prompt on small or unusually diverse stories.
    if len(selected) < max(2, len(articles) // 2):
        return articles, []

    if excluded:
        logger.info(
            "  [StoryFilter] Excluding %s likely outlier article(s) from story %s prompt: %s",
            len(excluded),
            getattr(story, "id", "unknown"),
            "; ".join((article.title or "")[:80] for article in excluded),
        )
    return selected, excluded


def get_topics_list(obj):
    """Get the topic names for a Story or Article as a list of strings."""
    try:
        return [t.name for t in obj.topics]
    except Exception:
        return []


def _analysis_text(obj):
    parts = [
        getattr(obj, "headline", None) or "",
        getattr(obj, "title", None) or "",
    ]
    for article in list(getattr(obj, "articles", []) or [])[:8]:
        parts.append(article.title or "")
    return " ".join(parts).lower()


def _contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


POLITICAL_ANALYSIS_KEYWORDS = {
    "administration", "agency", "bill", "campaign", "congress", "court",
    "democrat", "diplomat", "election", "executive order", "federal",
    "governor", "government", "house ", "justice department", "law",
    "lawsuit", "minister", "parliament", "policy", "president", "prime minister",
    "republican", "ruling", "sanction", "senate", "tariff", "trump", "white house",
}

PUBLIC_SAFETY_ANALYSIS_KEYWORDS = {
    "accident", "arrested", "attack", "blaze", "crash", "dead", "death",
    "disaster", "earthquake", "evacuation", "explosion", "fire", "flood",
    "hostage", "injured", "killed", "missing", "police", "rescue", "search",
    "shooting", "storm", "victim",
}

BUSINESS_ANALYSIS_KEYWORDS = {
    "bank", "bankruptcy", "bond", "ceo", "company", "earnings", "economy",
    "fed", "federal reserve", "finance", "inflation", "investor", "layoff",
    "market", "merger", "mortgage", "price", "profit", "rate", "revenue",
    "stock", "trade", "wall street",
}


def detect_analysis_type(obj):
    """
    Determine which type of specialized persona to use based on topics.
    Returns one of: 'politics', 'science', 'sports', 'business', 'default'
    """
    topics = get_topics_list(obj)
    topics_lower = [t.lower() for t in topics]
    text = _analysis_text(obj)

    # A story whose only topic is US Politics is high-confidence enough on its
    # own: don't let rhetorical verbs ("attacks", "slams") in its headlines
    # fall through to PUBLIC_SAFETY_ANALYSIS_KEYWORDS, and don't require a
    # POLITICAL_ANALYSIS_KEYWORDS match that policy-speech headlines often lack.
    if topics_lower == ['us politics']:
        return 'politics'

    # A story tagged both US Politics and Sports (e.g. a stadium workers'
    # strike vote) is fundamentally political activity that happens to
    # involve a sports venue/team -- treat it as political, not sports,
    # rather than letting the sports check below win by default.
    if 'us politics' in topics_lower and 'sports' in topics_lower:
        return 'politics'

    if _contains_any(text, PUBLIC_SAFETY_ANALYSIS_KEYWORDS):
        return 'default'
    if any(t == 'us politics' for t in topics_lower) and _contains_any(text, POLITICAL_ANALYSIS_KEYWORDS):
        return 'politics'
    if any(t == 'sci/tech' for t in topics_lower):
        return 'science'
    if any(t == 'sports' for t in topics_lower):
        return 'sports'
    if (
        any(t == 'buss/fin' for t in topics_lower)
        and _contains_any(text, BUSINESS_ANALYSIS_KEYWORDS)
    ):
        return 'business'
    return 'default'


def get_persona(analysis_type):
    """Return the specialized journalist persona for a given analysis type."""
    mapping = {
        'politics': 'political analyst',
        'science': 'science and technology journalist',
        'sports': 'sports journalist',
        'business': 'financial journalist',
        'default': 'professional news analyst'
    }
    return mapping.get(analysis_type, mapping['default'])


# Below this the article is a feed blurb or a blocked scrape, and a
# four-section analysis of one sentence is padding. Same floor
# story_view.has_good_original uses for "we actually read it".
ARTICLE_DEEP_ANALYSIS_MIN_CHARS = 500


def article_has_analysable_content(article):
    return len(strip_html(article.content or "").strip()) >= ARTICLE_DEEP_ANALYSIS_MIN_CHARS


# Shortest legitimate multi-article story summary in the 60 days to
# 2026-09-30 was 319 chars (p1 421); the one below this was an 81-char
# mid-sentence cut (story 74001) that went straight onto the site.
STORY_SUMMARY_MIN_CHARS = 200


def _story_summary_rejection(summary):
    """Reason a generated story summary must not be stored, else None.

    Returning None from summarize_story() leaves Story.summary empty, so the
    story is retried on the next full run instead of publishing a cut-off
    paragraph. Deliberately narrow: only length and a missing sentence end.
    """
    from news_fetcher.quality_checks import looks_truncated

    if not summary or not summary.strip():
        return None
    text = summary.strip()
    if len(text) < STORY_SUMMARY_MIN_CHARS:
        return f"too short ({len(text)} chars)"
    return looks_truncated(text)


# Total article text in one story_summary prompt. Was 9,000 (1,500 each)
# while the context was Ollama's ~4k default (2026-10-02 scan: 6 of 8
# prompts >= 15k came back empty or cut off, 0 of 215 under 13k). The model
# now runs with a 16k-token context (~55k chars for prompt plus answer), so
# 20,000 / 3,000 each (Regis, 2026-10-26).
STORY_SUMMARY_CONTENT_BUDGET = 20000
STORY_SUMMARY_EXCERPT_MAX = 3000
# Deep reports take up to 15 articles. Was a flat 300 chars each until
# 2026-10-08, then 13,500 / 1,500 each until 2026-10-26.
DEEP_REPORT_CONTENT_BUDGET = 24000
DEEP_REPORT_EXCERPT_MAX = 3000
# An article longer than its excerpt also brings its own analysis (or its
# summary when it has none), both written from up to 24,000 chars of it, so
# the report sees how long pieces end, not just how they open (Regis,
# 2026-10-26). Shared out like the excerpts; with both budgets full the
# prompt is ~43k chars (~11k tokens), inside the 16k context with room for
# the answer.
DEEP_REPORT_DIGEST_BUDGET = 16000
DEEP_REPORT_DIGEST_MAX = 2500

# Big stories (Regis, 2026-11-03): 59 edition stories in 14 days had more than
# 15 articles and 18 more than 30 (one 138), so the deep report never saw most
# of their coverage, sometimes a whole side. Above DEEP_REPORT_MAX_FULL
# articles the newest BIG_STORY_FULL_ARTICLES keep full excerpts (on smaller
# budgets) and every other article comes in brief: outlet, title and the start
# of its stored article summary, filed under its side. The briefs share a
# budget and fall back to the title alone when a story is huge, so the prompt
# stays about the size of today's largest (~43k chars, ~11k tokens of 16k).
# No extra LLM calls: the article summaries are already written by then.
DEEP_REPORT_MAX_FULL = 15
BIG_STORY_FULL_ARTICLES = 10
DEEP_REPORT_BIG_CONTENT_BUDGET = 18000
DEEP_REPORT_BIG_DIGEST_BUDGET = 10000
DEEP_REPORT_BRIEF_BUDGET = 14000
STORY_SUMMARY_MAX_FULL = 10
STORY_SUMMARY_BRIEF_BUDGET = 8000
BRIEF_MAX = 300
BRIEF_MIN = 60
BRIEF_NOTE = ("Articles marked \"In brief\" are earlier coverage of the same story, "
              "given as a short summary only; the ones with excerpts are the newest.")
_SUMMARY_LABELS = re.compile(r"\b(The big picture|Why it matters|Quick analysis|What's happening|What's next):\s*", re.I)


def article_brief(article):
    """An article's stored summary as one plain line, without its section labels."""
    text = _SUMMARY_LABELS.sub("", strip_html(article.summary or ""))
    return " ".join(text.split())


def brief_limits(articles, budget, per_article_max=BRIEF_MAX, minimum=BRIEF_MIN):
    """{article id: chars of brief text}. Shared out like the excerpts; when a
    share would be under `minimum` the article gets its title only (0)."""
    lengths = [len(article_brief(a)) for a in articles]
    title_cost = sum(len(a.title or "") + 30 for a in articles)
    limits = _story_summary_excerpt_limits(lengths, budget=max(budget - title_cost, 0),
                                           per_article_max=per_article_max)
    return {a.id: (limit if limit and limit >= min(minimum, length) else 0)
            for a, limit, length in zip(articles, limits, lengths)}


def brief_text(article, limit):
    text = article_brief(article)
    if not limit or not text:
        return ""
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."


def split_full_and_brief(story, max_full, full_count):
    """(full, brief, excluded): stories up to `max_full` usable articles are
    unchanged (all full); bigger ones keep the newest `full_count` full and
    every other article (copies aside) brief.

    The outlier filter only picks the full set. Run over a whole big story it
    dropped on-topic coverage (24 of 77 on the Alaska ballot story: Peltola's
    denials, Trump's calls to remove her), since one anchor article can't
    stand for 80; a stray brief costs a line, a lost side costs the report."""
    originals = original_articles_for(story)
    if len(originals) <= max_full:
        full, excluded = _select_story_prompt_articles(story, limit=max_full)
        return full, [], excluded
    full, excluded = _select_story_prompt_articles(story, limit=full_count)
    from datetime import datetime as _dt
    taken = {a.id for a in full} | {a.id for a in excluded}
    brief = sorted((a for a in originals if a.id not in taken),
                   key=lambda a: getattr(a, "date", None) or _dt.min, reverse=True)
    return full, brief, excluded


def original_articles_for(story):
    from aggregator.article_copies import original_articles
    return original_articles(story.articles)


def article_digest(article):
    """The most in-depth text already written about an article: its deep
    analysis, else its summary, else ''."""
    return (getattr(article, "deep_analysis", None) or article.summary or "").strip()


def deep_report_digest_limits(articles, excerpt_limits, budget=DEEP_REPORT_DIGEST_BUDGET,
                              per_article_max=DEEP_REPORT_DIGEST_MAX):
    """{article id: chars of digest to include}, only for articles whose text
    was cut by the excerpt and that have a digest."""
    cut = [a for a in articles
           if article_digest(a)
           and len(strip_html(a.content or "").strip()) > excerpt_limits.get(a.id, 0)]
    lengths = [len(article_digest(a)) for a in cut]
    return dict(zip((a.id for a in cut),
                    _story_summary_excerpt_limits(lengths, budget=budget, per_article_max=per_article_max)))


def _story_summary_excerpt_limits(content_lengths, budget=STORY_SUMMARY_CONTENT_BUDGET,
                                  per_article_max=STORY_SUMMARY_EXCERPT_MAX):
    """Per-article excerpt length so all excerpts together fit `budget`.

    Each article gets at most `per_article_max`. Short articles only use what
    they have, and the unused share goes to the longer ones, so a story with
    a few stubs is not cut any harder than it has to be. Stories whose
    articles already fit (six or fewer full articles) are unchanged.
    """
    limits = [0] * len(content_lengths)
    remaining = budget
    order = sorted(range(len(content_lengths)), key=lambda i: content_lengths[i])
    for position, i in enumerate(order):
        share = remaining // (len(order) - position)
        limits[i] = min(content_lengths[i], per_article_max, share)
        remaining -= limits[i]
    return limits


@observe()
def summarize_story(story):
    """
    Given a Story object with related articles, ask Ollama to generate
    a detailed summary of the story using a specialized journalist persona.
    Returns summary string or None if Ollama is unavailable.
    """
    if not story.articles:
        return None

    if not check_ollama_status(llm_client.TIER_QUALITY):
        logger.warning(
            "  [Summarizer] Skipping story summary for '%s': Ollama unavailable.",
            story.title[:80],
        )
        return None

    analysis_type = detect_analysis_type(story)
    persona = get_persona(analysis_type)

    prompt_articles, brief_articles, excluded_articles = split_full_and_brief(
        story, STORY_SUMMARY_MAX_FULL, STORY_SUMMARY_MAX_FULL)
    readable_articles = [
        article for article in prompt_articles
        if len(strip_html(article.content or "").strip()) >= 200
    ]
    if not readable_articles:
        logger.info(
            "  Skipping story summary for '%s': no readable article content.",
            story.title[:80],
        )
        langfuse_context.update_current_observation(
            metadata={
                "model": MODEL,
                "analysis_type": analysis_type,
                "persona": persona,
                "prompt_articles": len(prompt_articles),
                "excluded_prompt_articles": len(excluded_articles),
                "skipped_reason": "no_readable_article_content",
            }
        )
        return None

    contents = [
        strip_html(article.content).strip() if article.content else ""
        for article in prompt_articles
    ]
    limits = _story_summary_excerpt_limits([len(c) for c in contents])
    article_texts = []
    for i, (article, content, limit) in enumerate(zip(prompt_articles, contents, limits), 1):
        text = f"{i}. Title: {article.title}"
        if content:
            text += f"\n   Content: {content[:limit].strip()}"
        article_texts.append(text)

    combined = "\n\n".join(article_texts)
    if brief_articles:
        limits_brief = brief_limits(brief_articles, STORY_SUMMARY_BRIEF_BUDGET)
        lines = [BRIEF_NOTE]
        for article in brief_articles:
            outlet_name = article.outlet.name if article.outlet else (article.source or "Unknown source")
            line = f"- {outlet_name}: {article.title}"
            text = brief_text(article, limits_brief.get(article.id))
            if text:
                line += f"\n  In brief: {text}"
            lines.append(line)
        combined += "\n\n" + "\n".join(lines)

    prompt = render_prompt("story_summary", persona=persona, combined=combined)
    if prompt is None:
        return None

    langfuse_context.update_current_trace(metadata={"story_id": story.id}, tags=[f"story:{story.id}"])
    langfuse_context.update_current_observation(
        input=prompt,
        metadata={
            "model": MODEL,
            "analysis_type": analysis_type,
            "persona": persona,
            "prompt_articles": len(prompt_articles),
            "brief_articles": len(brief_articles),
            "excluded_prompt_articles": len(excluded_articles),
        }
    )
    summary = llm_client.generate_text(prompt, timeout=120)
    langfuse_context.update_current_observation(output=summary)

    rejected = _story_summary_rejection(summary)
    if rejected:
        logger.warning(
            "  [Summarizer] Rejected story summary for '%s' (%s): %r",
            story.title[:60], rejected, (summary or "")[-80:],
        )
        langfuse_context.update_current_observation(metadata={"rejected_reason": rejected})
        return None

    if summary:
        summary = _fix_titles(summary, [combined], f"story {story.id} summary")
        logger.info(f"  Generated {analysis_type} summary for story: {story.title[:60]}...")
        return summary
    return None


@observe()
def generate_deep_report(story):
    """
    Generate an in-depth analytical report for a multi-source story.
    Uses topic-aware prompts based on the story's classification.
    Returns report string or None if Ollama is unavailable.
    """
    if not story.articles:
        return None

    if not check_ollama_status(llm_client.TIER_QUALITY):
        logger.warning(
            "  [Summarizer] Skipping deep report for '%s': Ollama unavailable.",
            story.title[:80],
        )
        return None

    analysis_type = detect_analysis_type(story)

    # Group articles by bias category
    left_articles = []
    center_articles = []
    right_articles = []
    unrated_articles = []

    prompt_articles, brief_articles, excluded_articles = split_full_and_brief(
        story, DEEP_REPORT_MAX_FULL, BIG_STORY_FULL_ARTICLES)
    big_story = bool(brief_articles)
    readable_articles = [
        article for article in prompt_articles
        if len(strip_html(article.content or "").strip()) >= 200
    ]
    if not readable_articles:
        logger.info(
            "  Skipping deep report for '%s': no readable article content.",
            story.title[:80],
        )
        langfuse_context.update_current_observation(
            metadata={
                "model": MODEL,
                "analysis_type": analysis_type,
                "prompt_articles": len(prompt_articles),
                "excluded_prompt_articles": len(excluded_articles),
                "skipped_reason": "no_readable_article_content",
            }
        )
        return None

    contents = [strip_html(a.content or "").strip() for a in prompt_articles]
    excerpt_limits = dict(zip(
        (a.id for a in prompt_articles),
        _story_summary_excerpt_limits([len(c) for c in contents],
                                      budget=DEEP_REPORT_BIG_CONTENT_BUDGET if big_story else DEEP_REPORT_CONTENT_BUDGET,
                                      per_article_max=DEEP_REPORT_EXCERPT_MAX),
    ))

    def excerpt(a):
        return strip_html(a.content or "")[:excerpt_limits.get(a.id, 300)].strip()

    digest_limits = deep_report_digest_limits(
        prompt_articles, excerpt_limits,
        budget=DEEP_REPORT_BIG_DIGEST_BUDGET if big_story else DEEP_REPORT_DIGEST_BUDGET)
    brief_ids = {a.id for a in brief_articles}
    brief_char_limits = brief_limits(brief_articles, DEEP_REPORT_BRIEF_BUDGET) if big_story else {}

    def digest(a):
        limit = digest_limits.get(a.id)
        if not limit:
            return ""
        text = " ".join(article_digest(a).split())
        return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."

    for article in prompt_articles + brief_articles:
        score = article.bias_score
        if score is None and article.outlet:
            score = article.outlet.bias_score
        if score is None:
            unrated_articles.append(article)
        elif score <= 2.5:
            left_articles.append(article)
        elif score <= 3.5:
            center_articles.append(article)
        else:
            right_articles.append(article)

    def article_lines(a):
        outlet_name = a.outlet.name if a.outlet else (a.source or "Unknown source")
        lines = [f"- {outlet_name}: {a.title}"]
        if a.id in brief_ids:
            text = brief_text(a, brief_char_limits.get(a.id))
            if text:
                lines.append(f"  In brief: {text}")
            return lines
        snippet = excerpt(a)
        if snippet:
            lines.append(f"  Excerpt: {snippet}")
        whole = digest(a)
        if whole:
            lines.append(f"  Analysis of the whole article: {whole}")
        return lines

    def format_articles(articles, label, include_empty=False):
        if not articles:
            return f"\n{label} Sources:\n- None found in the current source set." if include_empty else ""
        lines = [f"\n{label} Sources:"]
        for a in articles:
            lines.extend(article_lines(a))
        return "\n".join(lines)

    def format_all_articles(articles):
        """Format all articles without bias grouping for non-political analysis."""
        lines = []
        for a in articles:
            lines.extend(article_lines(a))
        return "\n".join(lines)

    # Build prompt based on analysis type
    if analysis_type == 'politics':
        left_section = format_articles(left_articles, "LEFT-LEANING", include_empty=True)
        center_section = format_articles(center_articles, "CENTER", include_empty=True)
        right_section = format_articles(right_articles, "RIGHT-LEANING", include_empty=True)
        unrated_section = format_articles(unrated_articles, "UNRATED", include_empty=True)
        combined = left_section + center_section + right_section + unrated_section

        if not combined.strip():
            return None

        source_availability = "\n".join([
            f"- Left-leaning sources found: {len(left_articles)}",
            f"- Center sources found: {len(center_articles)}",
            f"- Right-leaning sources found: {len(right_articles)}",
            f"- Unrated sources found: {len(unrated_articles)}",
        ])

        combined = (BRIEF_NOTE + "\n" + combined) if big_story else combined
        prompt = render_prompt("deep_report.politics", source_availability=source_availability, combined=combined)

    elif analysis_type == 'science':
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        combined = (BRIEF_NOTE + "\n" + combined) if big_story else combined
        prompt = render_prompt("deep_report.science", combined=combined)

    elif analysis_type == 'sports':
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        combined = (BRIEF_NOTE + "\n" + combined) if big_story else combined
        prompt = render_prompt("deep_report.sports", combined=combined)

    elif analysis_type == 'business':
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        combined = (BRIEF_NOTE + "\n" + combined) if big_story else combined
        prompt = render_prompt("deep_report.business", combined=combined)

    else:
        # Default — generic deep analysis
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        combined = (BRIEF_NOTE + "\n" + combined) if big_story else combined
        prompt = render_prompt("deep_report.default", combined=combined)

    if prompt is None:
        return None

    langfuse_context.update_current_trace(metadata={"story_id": story.id}, tags=[f"story:{story.id}"])
    langfuse_context.update_current_observation(
        input=prompt,
        metadata={
            "model": MODEL,
            "analysis_type": analysis_type,
            "prompt_articles": len(prompt_articles),
            "brief_articles": len(brief_articles),
            "excluded_prompt_articles": len(excluded_articles),
            "articles_with_digest": len(digest_limits),
        }
    )

    report = llm_client.generate_text(prompt, timeout=180)
    langfuse_context.update_current_observation(output=report)
    if report:
        report = _fix_titles(report, [combined], f"story {story.id} deep report")
        logger.info(f"  Generated {analysis_type} deep report for: {story.title[:60]}...")
        return report
    return None


# How much of an article the article summary and analysis read. 24,000
# characters (~6-7k tokens) covers all but ~1 in 150 articles and fits the
# 16k context. Was 3,000 / 3,500, which on 2026-10-26 A/B cut the later half
# of long pieces: analyses said "no polling data" for an article that had it.
# About +2 s per long article. ARTICLE_CONTENT_LIMIT in .env lowers it for a
# model with a small context (Ollama's old 4k default needs ~3000).
ARTICLE_SUMMARY_CONTENT_LIMIT = int(os.environ.get("ARTICLE_CONTENT_LIMIT") or 24000)
ARTICLE_ANALYSIS_CONTENT_LIMIT = ARTICLE_SUMMARY_CONTENT_LIMIT


@observe()
def summarize_article(article):
    """
    Generate a concise Smart Brevity briefing for a single article using a
    specialized journalist persona.
    Used for the per-article summary button in the article reader.
    Returns summary string or None if Ollama is unavailable.
    """
    if not article or not article.content:
        return None

    if not check_ollama_status(llm_client.TIER_QUALITY):
        logger.warning(
            "  [Summarizer] Skipping article summary for '%s': Ollama unavailable.",
            article.title[:80],
        )
        return None

    analysis_type = detect_analysis_type(article)
    persona = get_persona(analysis_type)

    clean_content = strip_html(article.content)[:ARTICLE_SUMMARY_CONTENT_LIMIT].strip()
    if not clean_content:
        return None

    prompt = render_prompt(
        "article_summary", persona=persona, article_title=article.title, clean_content=clean_content
    )
    if prompt is None:
        return None

    langfuse_context.update_current_trace(metadata={"article_id": article.id}, tags=[f"article:{article.id}"])
    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"model": MODEL, "analysis_type": analysis_type, "persona": persona}
    )

    summary = llm_client.generate_text(prompt, timeout=120)
    langfuse_context.update_current_observation(output=summary)
    if summary:
        summary = _fix_titles(summary, [article.title, clean_content], f"article {article.id} summary")
        logger.info(f"  Generated {analysis_type} summary for article: {article.title[:60]}...")
        return summary
    return None


@observe()
def generate_article_deep_analysis(article):
    """
    Generate a deeper article-level analysis, with a prompt per analysis type.
    Returns None when the article has too little real text to analyse.
    """
    if not article or not article_has_analysable_content(article):
        return None

    if not check_ollama_status(llm_client.TIER_QUALITY):
        logger.warning(
            "  [Summarizer] Skipping article deep analysis for '%s': Ollama unavailable.",
            article.title[:80],
        )
        return None

    analysis_type = detect_analysis_type(article)
    clean_content = strip_html(article.content)[:ARTICLE_ANALYSIS_CONTENT_LIMIT].strip()
    if not clean_content:
        return None

    prompt = render_prompt(
        f"article_deep_analysis.{analysis_type}", article_title=article.title, clean_content=clean_content
    )
    if prompt is None:
        return None

    langfuse_context.update_current_trace(metadata={"article_id": article.id}, tags=[f"article:{article.id}"])
    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"model": MODEL, "analysis_type": analysis_type, "scope": "article_deep_analysis"}
    )

    analysis = llm_client.generate_text(prompt, timeout=150)
    langfuse_context.update_current_observation(output=analysis)
    if analysis:
        analysis = _fix_titles(analysis, [article.title, clean_content], f"article {article.id} analysis")
        logger.info(f"  Generated {analysis_type} article analysis: {article.title[:60]}...")
        return analysis
    return None

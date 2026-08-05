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

logger = logging.getLogger(__name__)

langfuse = Langfuse(
    public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
    host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
)

MODEL = os.environ.get("OLLAMA_MODEL", "")

if llm_client.LLM_PROVIDER == "ollama" and not MODEL:
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
    sorted_articles = sorted(story.articles, key=lambda a: getattr(a, "date", None) or _dt.min, reverse=True)
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


def article_needs_deep_analysis(article):
    """Only generate article-level deep analysis for domains where it adds value."""
    return detect_analysis_type(article) in {"politics", "science", "business"}


@observe()
def summarize_story(story):
    """
    Given a Story object with related articles, ask Ollama to generate
    a detailed summary of the story using a specialized journalist persona.
    Returns summary string or None if Ollama is unavailable.
    """
    if not story.articles:
        return None

    if not check_ollama_status():
        logger.warning(
            "  [Summarizer] Skipping story summary for '%s': Ollama unavailable.",
            story.title[:80],
        )
        return None

    analysis_type = detect_analysis_type(story)
    persona = get_persona(analysis_type)

    prompt_articles, excluded_articles = _select_story_prompt_articles(story, limit=10)
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

    article_texts = []
    for i, article in enumerate(prompt_articles, 1):
        text = f"{i}. Title: {article.title}"
        if article.content:
            # Strip HTML before sending to Ollama
            clean_content = strip_html(article.content)
            # Use more content now that we have full scraped articles
            snippet = clean_content[:1500].strip()
            text += f"\n   Content: {snippet}"
        article_texts.append(text)

    combined = "\n\n".join(article_texts)

    prompt = render_prompt("story_summary", persona=persona, combined=combined)
    if prompt is None:
        return None

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={
            "model": MODEL,
            "analysis_type": analysis_type,
            "persona": persona,
            "prompt_articles": len(prompt_articles),
            "excluded_prompt_articles": len(excluded_articles),
        }
    )
    summary = llm_client.generate_text(prompt, timeout=120)
    langfuse_context.update_current_observation(output=summary)

    if summary:
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

    if not check_ollama_status():
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

    prompt_articles, excluded_articles = _select_story_prompt_articles(story, limit=15)
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

    for article in prompt_articles:
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

    def format_articles(articles, label, include_empty=False):
        if not articles:
            return f"\n{label} Sources:\n- None found in the current source set." if include_empty else ""
        lines = [f"\n{label} Sources:"]
        for a in articles:
            outlet_name = a.outlet.name if a.outlet else (a.source or "Unknown source")
            lines.append(f"- {outlet_name}: {a.title}")
            if a.content:
                snippet = strip_html(a.content)[:300].strip()
                if snippet:
                    lines.append(f"  Excerpt: {snippet}")
        return "\n".join(lines)

    def format_all_articles(articles):
        """Format all articles without bias grouping for non-political analysis."""
        lines = []
        for a in articles:
            outlet_name = a.outlet.name if a.outlet else (a.source or "Unknown source")
            lines.append(f"- {outlet_name}: {a.title}")
            if a.content:
                snippet = strip_html(a.content)[:300].strip()
                if snippet:
                    lines.append(f"  Excerpt: {snippet}")
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

        prompt = render_prompt("deep_report.politics", source_availability=source_availability, combined=combined)

    elif analysis_type == 'science':
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        prompt = render_prompt("deep_report.science", combined=combined)

    elif analysis_type == 'sports':
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        prompt = render_prompt("deep_report.sports", combined=combined)

    elif analysis_type == 'business':
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        prompt = render_prompt("deep_report.business", combined=combined)

    else:
        # Default — generic deep analysis
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        prompt = render_prompt("deep_report.default", combined=combined)

    if prompt is None:
        return None

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={
            "model": MODEL,
            "analysis_type": analysis_type,
            "prompt_articles": len(prompt_articles),
            "excluded_prompt_articles": len(excluded_articles),
        }
    )

    report = llm_client.generate_text(prompt, timeout=180)
    langfuse_context.update_current_observation(output=report)
    if report:
        logger.info(f"  Generated {analysis_type} deep report for: {story.title[:60]}...")
        return report
    return None


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

    if not check_ollama_status():
        logger.warning(
            "  [Summarizer] Skipping article summary for '%s': Ollama unavailable.",
            article.title[:80],
        )
        return None

    analysis_type = detect_analysis_type(article)
    persona = get_persona(analysis_type)

    clean_content = strip_html(article.content)[:3000].strip()
    if not clean_content:
        return None

    prompt = render_prompt(
        "article_summary", persona=persona, article_title=article.title, clean_content=clean_content
    )
    if prompt is None:
        return None

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"model": MODEL, "analysis_type": analysis_type, "persona": persona}
    )

    summary = llm_client.generate_text(prompt, timeout=120)
    langfuse_context.update_current_observation(output=summary)
    if summary:
        logger.info(f"  Generated {analysis_type} summary for article: {article.title[:60]}...")
        return summary
    return None


@observe()
def generate_article_deep_analysis(article):
    """
    Generate a deeper article-level analysis for topics that benefit from it.
    Returns analysis string or None if this topic should only receive a summary.
    """
    if not article or not article.content or not article_needs_deep_analysis(article):
        return None

    if not check_ollama_status():
        logger.warning(
            "  [Summarizer] Skipping article deep analysis for '%s': Ollama unavailable.",
            article.title[:80],
        )
        return None

    analysis_type = detect_analysis_type(article)
    clean_content = strip_html(article.content)[:3500].strip()
    if not clean_content:
        return None

    if analysis_type == "politics":
        prompt = render_prompt(
            "article_deep_analysis.politics", article_title=article.title, clean_content=clean_content
        )
    elif analysis_type == "science":
        prompt = render_prompt(
            "article_deep_analysis.science", article_title=article.title, clean_content=clean_content
        )
    elif analysis_type == "business":
        prompt = render_prompt(
            "article_deep_analysis.business", article_title=article.title, clean_content=clean_content
        )
    else:
        return None

    if prompt is None:
        return None

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"model": MODEL, "analysis_type": analysis_type, "scope": "article_deep_analysis"}
    )

    analysis = llm_client.generate_text(prompt, timeout=150)
    langfuse_context.update_current_observation(output=analysis)
    if analysis:
        logger.info(f"  Generated {analysis_type} article analysis: {article.title[:60]}...")
        return analysis
    return None

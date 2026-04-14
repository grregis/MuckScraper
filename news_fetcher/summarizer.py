# muckscraperHeadlinesGoogleNEW/news_fetcher/summarizer.py
# news_fetcher/summarizer.py

import requests
import os
import re
import logging
from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context

logger = logging.getLogger(__name__)

langfuse = Langfuse(
    public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
    host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
)

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "")
MODEL = os.environ.get("OLLAMA_MODEL", "")


def check_ollama_status():
    """Returns True if Ollama is reachable, False otherwise."""
    try:
        response = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        return response.status_code == 200
    except Exception:
        return False


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


def get_topics_list(obj):
    """Get the topic names for a Story or Article as a list of strings."""
    try:
        return [t.name for t in obj.topics]
    except Exception:
        return []


def detect_analysis_type(obj):
    """
    Determine which type of specialized persona to use based on topics.
    Returns one of: 'politics', 'science', 'gaming', 'sports', 'business', 'default'
    """
    topics = get_topics_list(obj)
    topics_lower = [t.lower() for t in topics]
    
    if any('us politics' in t for t in topics_lower):
        return 'politics'
    if any('science' in t or 'technology' in t for t in topics_lower):
        return 'science'
    if any('gaming' in t for t in topics_lower):
        return 'gaming'
    if any('sports' in t for t in topics_lower):
        return 'sports'
    if any('business' in t or 'finance' in t for t in topics_lower):
        return 'business'
    return 'default'


def get_persona(analysis_type):
    """Return the specialized journalist persona for a given analysis type."""
    mapping = {
        'politics': 'political analyst',
        'science': 'science and technology journalist',
        'gaming': 'gaming journalist',
        'sports': 'sports journalist',
        'business': 'financial journalist',
        'default': 'professional news analyst'
    }
    return mapping.get(analysis_type, mapping['default'])


@observe()
def summarize_story(story):
    """
    Given a Story object with related articles, ask Ollama to generate
    a detailed summary of the story using a specialized journalist persona.
    Returns summary string or None if Ollama is unavailable.
    """
    if not story.articles:
        return None

    analysis_type = detect_analysis_type(story)
    persona = get_persona(analysis_type)

    article_texts = []
    for i, article in enumerate(story.articles[:10], 1):
        text = f"{i}. Title: {article.title}"
        if article.content:
            # Strip HTML before sending to Ollama
            clean_content = strip_html(article.content)
            # Use more content now that we have full scraped articles
            snippet = clean_content[:1500].strip()
            text += f"\n   Content: {snippet}"
        article_texts.append(text)

    combined = "\n\n".join(article_texts)

    prompt = f"""You are a {persona} writing in the Smart Brevity style.

Below are multiple news articles covering the same story. Write a structured summary using EXACTLY this format:

The big picture: [One punchy, direct sentence summarizing what happened. No fluff.]

Why it matters: [1-2 sentences explaining the significance.]

What's happening:
- [Key fact or development]
- [Key fact or development]
- [Key fact or development]
- [Add more bullets if needed, max 6]

What's next: [One sentence on what to watch for or what comes next.]

Rules:
- Use EXACTLY the labels shown above including the colon
- Bullets must start with • 
- Keep every section tight and direct
- No markdown, no extra formatting, no commentary
- Do not add any text before or after the structure above

Articles:
{combined}

Detailed Summary:"""

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"model": MODEL, "analysis_type": analysis_type, "persona": persona}
    )
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
            },
            timeout=120,
        )
        response.raise_for_status()

        result = response.json()
        summary = result.get("response", "").strip()

        langfuse_context.update_current_observation(
            output=summary
        )

        if summary:
            logger.info(f"  Generated {analysis_type} summary for story: {story.title[:60]}...")
            return summary
        return None

    except Exception as e:
        logger.info(f"  Error generating summary for '{story.title}': {e}")
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
        return None

    analysis_type = detect_analysis_type(story)

    # Group articles by bias category
    left_articles = []
    center_articles = []
    right_articles = []
    unrated_articles = []

    for article in story.articles[:15]:
        score = article.outlet.bias_score if article.outlet else None
        if score is None:
            unrated_articles.append(article)
        elif score <= 2:
            left_articles.append(article)
        elif score == 3:
            center_articles.append(article)
        else:
            right_articles.append(article)

    def format_articles(articles, label):
        if not articles:
            return ""
        lines = [f"\n{label} Sources:"]
        for a in articles:
            lines.append(f"- {a.outlet.name}: {a.title}")
            if a.content:
                snippet = strip_html(a.content)[:300].strip()
                if snippet:
                    lines.append(f"  Excerpt: {snippet}")
        return "\n".join(lines)

    def format_all_articles(articles):
        """Format all articles without bias grouping for non-political analysis."""
        lines = []
        for a in articles:
            lines.append(f"- {a.outlet.name}: {a.title}")
            if a.content:
                snippet = strip_html(a.content)[:300].strip()
                if snippet:
                    lines.append(f"  Excerpt: {snippet}")
        return "\n".join(lines)

    # Build prompt based on analysis type
    if analysis_type == 'politics':
        left_section = format_articles(left_articles, "LEFT-LEANING")
        center_section = format_articles(center_articles, "CENTER")
        right_section = format_articles(right_articles, "RIGHT-LEANING")
        unrated_section = format_articles(unrated_articles, "UNRATED")
        combined = left_section + center_section + right_section + unrated_section

        if not combined.strip():
            return None

        prompt = f"""You are an experienced media analyst writing a detailed report on how different news outlets are covering the same political story.

Below are articles from outlets with different political leanings. Analyze how each side is framing the story.

{combined}

Write a detailed analytical report using this EXACT format:

The story: [2-3 sentences explaining what happened factually]

How the left is covering it: [How left-leaning outlets are framing this story, what they emphasize, what language they use. If no left sources, say "No left-leaning sources covered this story."]

How the center is covering it: [How center outlets are framing this story. If no center sources, say "No center sources covered this story."]

How the right is covering it: [How right-leaning outlets are framing this story, what they emphasize, what language they use. If no right sources, say "No right-leaning sources covered this story."]

What's contested: [Where the different sides disagree most sharply, what facts or framings are in dispute]

What's missing: [What angles or perspectives seem absent from the coverage, what questions aren't being asked]

What's next: [One sentence on what to watch for]

Rules:
- Use EXACTLY the labels shown above including the colon
- Be specific about framing differences, not just topic differences
- Stay neutral and analytical in your own voice
- No markdown, no extra formatting
- Do not add any text before or after the structure above"""

    elif analysis_type == 'science':
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        prompt = f"""You are a science journalist writing a detailed report on a scientific or technology development.

Below are articles covering the same story:

{combined}

Write a detailed analytical report using this EXACT format:

The discovery or development: [2-3 sentences explaining what happened or was discovered factually]

Why it matters: [The scientific or technological significance — what does this change or enable?]

What the research shows: [Key findings, data points, or technical details from the coverage]

Real world impact: [How this affects people, industries, or society in practical terms]

What experts are saying: [Notable quotes or expert opinions from the coverage. If none available, say "Expert commentary not available in current coverage."]

What's still unknown: [Open questions, limitations of the research, or what needs further study]

What's next: [One sentence on upcoming developments or what to watch for]

Rules:
- Use EXACTLY the labels shown above including the colon
- Focus on accuracy and significance over drama
- Stay neutral and factual
- No markdown, no extra formatting
- Do not add any text before or after the structure above"""

    elif analysis_type == 'gaming':
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        prompt = f"""You are a gaming journalist writing a detailed report on a gaming story.

Below are articles covering the same story:

{combined}

Write a detailed analytical report using this EXACT format:

The story: [2-3 sentences explaining what happened factually]

What's the game or company: [Brief context about the game, developer, or company involved]

What the coverage is saying: [How gaming outlets and mainstream press are covering this — areas of agreement and disagreement]

Community reaction: [How players and the gaming community are responding based on the coverage. If not mentioned, say "Community reaction not covered in current sources."]

Industry impact: [What this means for the broader gaming industry or market]

What's next: [One sentence on what to watch for — upcoming releases, announcements, or developments]

Rules:
- Use EXACTLY the labels shown above including the colon
- Be specific and detailed about the gaming context
- No markdown, no extra formatting
- Do not add any text before or after the structure above"""

    elif analysis_type == 'sports':
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        prompt = f"""You are a sports journalist writing a factual recap and analysis of a sports story.

Below are articles covering the same story:

{combined}

Write a detailed report using this EXACT format:

What happened: [2-3 sentences with the key facts — scores, results, or news]

Key performances: [Standout players, teams, or moments from the coverage. If not a game recap, describe the key people involved.]

The bigger picture: [What this means for standings, playoffs, championships, contracts, or the sport more broadly]

By the numbers: [Key stats, records, or figures mentioned in the coverage. If none available, say "Detailed statistics not available in current coverage."]

What's next: [One sentence on upcoming games, decisions, or developments to watch]

Rules:
- Use EXACTLY the labels shown above including the colon
- Focus on facts and context over opinion
- No markdown, no extra formatting
- Do not add any text before or after the structure above"""

    elif analysis_type == 'business':
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        prompt = f"""You are a financial journalist writing a detailed report on a business or markets story.

Below are articles covering the same story:

{combined}

Write a detailed analytical report using this EXACT format:

The story: [2-3 sentences explaining what happened factually]

Market impact: [How markets, stocks, or prices have reacted based on the coverage]

What companies or sectors are affected: [Key players, industries, or markets involved and how they are impacted]

What analysts are saying: [Expert or analyst opinions from the coverage. If none available, say "Analyst commentary not available in current coverage."]

The broader economic picture: [How this fits into wider economic trends, policy, or conditions]

Risks and opportunities: [Key risks or opportunities this creates for investors, businesses, or consumers]

What's next: [One sentence on key dates, decisions, or developments to watch]

Rules:
- Use EXACTLY the labels shown above including the colon
- Focus on market and economic significance
- Stay neutral and factual
- No markdown, no extra formatting
- Do not add any text before or after the structure above"""

    else:
        # Default — generic deep analysis
        all_articles = left_articles + center_articles + right_articles + unrated_articles
        combined = format_all_articles(all_articles)

        if not combined.strip():
            return None

        prompt = f"""You are an experienced journalist writing a detailed report on a news story.

Below are articles covering the same story:

{combined}

Write a detailed analytical report using this EXACT format:

The story: [2-3 sentences explaining what happened factually]

Why it matters: [The significance of this story — who it affects and how]

Key details: [The most important facts, figures, or developments from the coverage]

Different perspectives: [How different outlets or sources are framing this story. If coverage is uniform, say what angle is being emphasized.]

What's missing: [What angles or questions seem absent from the coverage]

What's next: [One sentence on what to watch for]

Rules:
- Use EXACTLY the labels shown above including the colon
- Stay neutral and analytical
- No markdown, no extra formatting
- Do not add any text before or after the structure above"""

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"model": MODEL, "analysis_type": analysis_type}
    )

    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
            },
            timeout=180,
        )
        response.raise_for_status()
        result = response.json()
        report = result.get("response", "").strip()
        langfuse_context.update_current_observation(output=report)
        if report:
            logger.info(f"  Generated {analysis_type} deep report for: {story.title[:60]}...")
            return report
        return None
    except Exception as e:
        logger.error(f"  Error generating deep report for '{story.title}': {e}")
        return None


@observe()
def summarize_article(article):
    """
    Generate a Smart Brevity summary for a single article using a 
    specialized journalist persona.
    Used for the per-article summary button in the article reader.
    Returns summary string or None if Ollama is unavailable.
    """
    if not article or not article.content:
        return None

    if not check_ollama_status():
        return None

    analysis_type = detect_analysis_type(article)
    persona = get_persona(analysis_type)

    clean_content = strip_html(article.content)[:3000].strip()
    if not clean_content:
        return None

    prompt = f"""You are a {persona} writing in the Smart Brevity style.

Below is a news article. Write a structured summary using EXACTLY this format:

The big picture: [One punchy, direct sentence summarizing what happened. No fluff.]

Why it matters: [1-2 sentences explaining the significance.]

What's happening:
- [Key fact or development]
- [Key fact or development]
- [Key fact or development]
- [Add more bullets if needed, max 6]

What's next: [One sentence on what to watch for or what comes next.]

Rules:
- Use EXACTLY the labels shown above including the colon
- Bullets must start with •
- Keep every section tight and direct
- No markdown, no extra formatting, no commentary
- Do not add any text before or after the structure above

Article title: {article.title}

Article content:
{clean_content}

Summary:"""

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"model": MODEL, "analysis_type": analysis_type, "persona": persona}
    )

    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
            },
            timeout=120,
        )
        response.raise_for_status()
        result = response.json()
        summary = result.get("response", "").strip()
        langfuse_context.update_current_observation(output=summary)
        if summary:
            logger.info(f"  Generated {analysis_type} summary for article: {article.title[:60]}...")
            return summary
        return None
    except Exception as e:
        logger.error(f"  Error generating summary for article '{article.title}': {e}")
        return None

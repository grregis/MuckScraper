# news_fetcher/summarizer.py

import requests
import os

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "")
MODEL = os.environ.get("OLLAMA_MODEL", "mannix/llama3.1-8b-lexi:latest")


def check_ollama_status():
    """Returns True if Ollama is reachable, False otherwise."""
    try:
        response = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        return response.status_code == 200
    except Exception:
        return False


def summarize_story(story):
    """
    Given a Story object with related articles, ask Ollama to generate
    a detailed summary of the story based on titles and content snippets.
    Returns summary string or None if Ollama is unavailable.
    """
    if not story.articles:
        return None

    # Build input text from titles + content snippets
    article_texts = []
    for i, article in enumerate(story.articles[:10], 1):  # cap at 10 articles
        text = f"{i}. Title: {article.title}"
        if article.content:
            # NewsAPI content snippets are often truncated, use what we have
            snippet = article.content[:500].strip()
            text += f"\n   Snippet: {snippet}"
        article_texts.append(text)

    combined = "\n\n".join(article_texts)

    prompt = f"""You are a professional news analyst. Below are multiple news articles covering the same story.

Your task is to write a comprehensive, detailed summary of this story. Include:
- What happened and the key facts
- Who is involved and their roles
- Why this is significant
- Any different perspectives or angles covered across the sources
- Any important context or background

Be as detailed and thorough as possible. Write in clear, neutral journalistic prose.

Articles:
{combined}

Detailed Summary:"""

    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
            },
            timeout=120,  # summaries take longer than bias scoring
        )
        response.raise_for_status()

        result = response.json()
        summary = result.get("response", "").strip()

        if summary:
            print(f"  Generated summary for story: {story.title[:60]}...")
            return summary
        return None

    except Exception as e:
        print(f"  Error generating summary for '{story.title}': {e}")
        return None
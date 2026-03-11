# news_fetcher/outlet_bias_llm.py

import requests
import json
import os

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "")
MODEL = "mannix/llama3.1-8b-lexi:latest"

BIAS_LABELS = {
    1: "Left",
    2: "Lean Left",
    3: "Center",
    4: "Lean Right",
    5: "Right"
}


def _ask_ollama(prompt):
    """Send a prompt to Ollama and return the raw response string or None."""
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model": MODEL,
                "prompt": prompt,
                "stream": False,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except Exception as e:
        print(f"  Error calling Ollama: {e}")
        return None


def _parse_bias_score(raw, label):
    """Parse a 1-5 integer from Ollama's response."""
    if raw is None:
        return None
    if raw.lower() == "unknown":
        print(f"  Ollama could not determine bias for: {label}")
        return None
    try:
        score = int(raw)
        if 1 <= score <= 5:
            return score
        return None
    except ValueError:
        print(f"  Could not parse Ollama response for '{label}': {raw}")
        return None


def get_outlet_bias_from_llm(outlet_name):
    """
    Ask Ollama to rate the political bias of a news outlet by name.
    Returns an integer 1-5 or None if it can't determine.
    """
    prompt = f"""You are a media bias analyst. Rate the political bias of the news outlet "{outlet_name}" on this scale:
1 = Left
2 = Lean Left
3 = Center
4 = Lean Right
5 = Right

Rules:
- Respond with a single integer between 1 and 5 only
- No explanation, no punctuation, just the number
- If you have never heard of the outlet or genuinely cannot determine its bias, respond with the single word: unknown

Outlet: {outlet_name}
Rating:"""

    raw = _ask_ollama(prompt)
    print(f"  Ollama rated outlet '{outlet_name}': {raw}")
    return _parse_bias_score(raw, outlet_name)


def get_article_bias_from_llm(title, content=None):
    """
    Ask Ollama to rate the political bias of a specific article
    based on its title and content snippet.
    Returns an integer 1-5 or None if it can't determine.
    """
    article_text = f"Title: {title}"
    if content:
        snippet = content[:600].strip()
        article_text += f"\n\nContent snippet: {snippet}"

    prompt = f"""You are a media bias analyst. Read the following news article and rate its political bias on this scale:
1 = Left
2 = Lean Left
3 = Center
4 = Lean Right
5 = Right

Consider the language used, framing, and perspective presented in the article itself.

Rules:
- Respond with a single integer between 1 and 5 only
- No explanation, no punctuation, just the number
- If you genuinely cannot determine the bias from the content, respond with the single word: unknown

Article:
{article_text}

Rating:"""

    raw = _ask_ollama(prompt)
    print(f"  Ollama rated article '{title[:60]}...': {raw}")
    return _parse_bias_score(raw, title)
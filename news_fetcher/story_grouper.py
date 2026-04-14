# muckscraperHeadlinesGoogleNEW/news_fetcher/story_grouper.py
# news_fetcher/story_grouper.py

import requests
import os
import re
import numpy as np
import logging
from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context

logger = logging.getLogger(__name__)

langfuse = Langfuse(
    public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
    host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
)

OLLAMA_HOST     = os.environ.get("OLLAMA_HOST", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")

SIMILARITY_THRESHOLD = 0.92
LOWER_THRESHOLD = 0.80


@observe()
def get_embedding(text):
    if not OLLAMA_HOST:
        return None
    langfuse_context.update_current_observation(
        input=text,
        metadata={"model": EMBEDDING_MODEL}
    )
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=15,
        )
        response.raise_for_status()
        embedding = response.json().get("embedding")
        if embedding:
            langfuse_context.update_current_observation(output=str(embedding))
            return embedding
        return None
    except Exception as e:
        logger.info(f"  [Embeddings] Error generating embedding: {e}")
        return None


def cosine_similarity(vec1, vec2):
    a = np.array(vec1)
    b = np.array(vec2)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def strip_to_snippet(html_content, max_chars=300):
    """Strip HTML tags and return a plain text snippet for LLM context."""
    if not html_content:
        return ""
    text = re.sub(r'<[^>]+>', ' ', html_content)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_chars]


def find_matching_story(article_title, article_embedding, recent_stories, article_content=None):
    if article_embedding is None:
        return None

    best_global_score = 0.0
    best_story = None

    from sqlalchemy.orm.exc import ObjectDeletedError

    for story in recent_stories:
        try:
            articles = story.articles
            if not articles:
                continue
            best_story_score = 0.0
            for article in articles:
                try:
                    if article.embedding is not None:
                        score = cosine_similarity(article_embedding, article.embedding)
                        if score > best_story_score:
                            best_story_score = score
                except ObjectDeletedError:
                    continue
            if best_story_score > best_global_score:
                best_global_score = best_story_score
                best_story = story
        except ObjectDeletedError:
            continue

    if best_global_score >= SIMILARITY_THRESHOLD and best_story:
        logger.info(f"  [Grouper] Matched to '{best_story.title}' (similarity: {best_global_score:.3f})")
        return best_story

    if best_global_score >= LOWER_THRESHOLD and best_story and OLLAMA_HOST:
        logger.info(f"  [Grouper] Ambiguous match (score: {best_global_score:.3f}), asking Ollama...")
        logger.info(f"  [Grouper] article_content present: {bool(article_content)}, length: {len(article_content) if article_content else 0}")

        story_snippet = ""
        if best_story.articles:
            story_snippet = strip_to_snippet(best_story.articles[0].content)

        ollama_decision = ask_ollama_for_match(
            article_title, [best_story],
            article_content=article_content,
            story_snippets=[story_snippet]
        )
        if ollama_decision:
            logger.info(f"  [Grouper] Ollama confirmed match to '{best_story.title}'")
            return ollama_decision
        else:
            logger.info(f"  [Grouper] Ollama rejected match, creating new story")
            return None

    logger.info(f"  [Grouper] No match found (best score: {best_global_score:.3f}), creating new story")
    return None


def find_or_create_story(article_title, db, Story, recent_stories, article_embedding=None, article_content=None):
    matched_story = find_matching_story(article_title, article_embedding, recent_stories, article_content=article_content)

    if matched_story:
        return matched_story

    new_title = clean_story_title(article_title)
    story = Story(title=new_title, summary=None)
    db.session.add(story)
    db.session.flush()
    logger.info(f"  [Grouper] Created new story: '{new_title}'")
    return story


def clean_story_title(article_title):
    for sep in [" - ", " | ", " — "]:
        if sep in article_title:
            parts = article_title.rsplit(sep, 1)
            if len(parts[1].split()) <= 4:
                article_title = parts[0]
                break
    words = article_title.split()
    if len(words) > 30:
        return " ".join(words[:30]) + "..."
    return article_title


def get_candidate_stories(article_title, recent_stories, max_candidates=5):
    """Keyword pre-filter — kept for regroup_ungrouped_stories compatibility."""
    article_words = set(w.lower() for w in article_title.split() if len(w) > 3)
    scored = []
    for story in recent_stories:
        story_words = set(w.lower() for w in story.title.split() if len(w) > 3)
        overlap = len(article_words & story_words)
        if overlap > 0:
            scored.append((overlap, story))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [story for _, story in scored[:max_candidates]]


@observe()
def ask_ollama_for_match(article_title, candidate_stories, article_content=None, story_snippets=None):
    """Kept for regroup_ungrouped_stories compatibility."""
    if not candidate_stories:
        return None

    story_lines = []
    for i, story in enumerate(candidate_stories):
        line = f"{i+1}. {story.title}"
        if story_snippets and i < len(story_snippets) and story_snippets[i]:
            line += f"\n   Context: {story_snippets[i]}"
        story_lines.append(line)
    story_list = "\n".join(story_lines)

    article_block = f'Article title: "{article_title}"'
    if article_content:
        snippet = strip_to_snippet(article_content)
        if snippet:
            article_block += f"\nArticle context: {snippet}"

    prompt = f"""You are a news editor grouping articles into stories.

{article_block}

Existing stories:
{story_list}

Does this article cover the same specific event or ongoing situation as any of the stories listed above?

Rules:
- Only match if they are clearly about the same specific event or situation
- Do not match just because they share a broad topic or the same company/person/country
- Do not match if the stories contradict each other (e.g. "price drop" vs "price increase")
- Do not match opinion, analysis, or impact articles to a news event unless they are explicitly about that same event
- Use the context snippets to distinguish between similar-sounding but different events
- If it matches, respond with only the number of the matching story (e.g. "2")
- If it does not match any story, respond with only "0"
- Respond with a single number and nothing else

Examples of correct NON-matches (should return 0):
- "Meta announces layoffs" vs "Epic Games lays off 900 workers" → 0 (different companies, different events)
- "Measles outbreak in Michigan" vs "Measles outbreak in Washington state" → 0 (same disease, different locations)
- "UFC fighter suspended for PED use" vs "MLB player suspended for PED use" → 0 (different sports, different athletes)
- "iPhone security alert" vs "Chrome zero-day vulnerability" → 0 (different platforms, different vulnerabilities)
- "NPR funding ruling" vs "Pentagon press policy ruling" → 0 (different court cases)
- "Grocery chain closing 17 stores" vs "Restaurant chain closing locations" → 0 (different companies)
- "Gold prices fall amid Iran war" vs "Trump says no ceasefire with Iran" → 0 (different topics: finance vs diplomacy)
- "How the Iran war affects trade recovery" vs "Iranian official killed in strike" → 0 (analysis piece vs news event)"""

    model = os.environ.get("OLLAMA_MODEL", "")
    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"model": model}
    )
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json().get("response", "").strip()
        langfuse_context.update_current_observation(output=result)

        for token in result.split():
            if token.isdigit():
                match_index = int(token)
                if 1 <= match_index <= len(candidate_stories):
                    matched = candidate_stories[match_index - 1]
                    logger.info(f"  [Grouper] Matched to story: '{matched.title}'")
                    return matched
                elif match_index == 0:
                    return None

        return None

    except Exception as e:
        logger.info(f"  [Grouper] Ollama error: {e}")
        return None

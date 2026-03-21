# news_fetcher/story_grouper.py

import requests
import os
import numpy as np
import logging

logger = logging.getLogger(__name__)

OLLAMA_HOST     = os.environ.get("OLLAMA_HOST", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")

# Similarity threshold — articles with cosine similarity above this
# are considered the same story. Tune this if grouping is too aggressive
# or too conservative.
SIMILARITY_THRESHOLD = 0.90


def get_embedding(text):
    """
    Generate a vector embedding for the given text using Ollama.
    Returns a list of floats or None if unavailable.
    """
    if not OLLAMA_HOST:
        return None
    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/embeddings",
            json={
                "model":  EMBEDDING_MODEL,
                "prompt": text,
            },
            timeout=15,
        )
        response.raise_for_status()
        embedding = response.json().get("embedding")
        if embedding:
            return embedding
        return None
    except Exception as e:
        logger.info(f"  [Embeddings] Error generating embedding: {e}")
        return None


def cosine_similarity(vec1, vec2):
    """Calculate cosine similarity between two vectors."""
    a = np.array(vec1)
    b = np.array(vec2)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def find_matching_story(article_title, article_embedding, recent_stories):
    """
    Find the best matching story using vector similarity.
    Compares against ALL articles in each story (Single Linkage).
    Returns matched Story or None.
    """
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

            # Find the best matching article within this story
            best_story_score = 0.0
            for article in articles:
                try:
                    if article.embedding is not None:
                        score = cosine_similarity(article_embedding, article.embedding)
                        if score > best_story_score:
                            best_story_score = score
                except ObjectDeletedError:
                    continue

            # Check if this story is the best match so far
            if best_story_score > best_global_score:
                best_global_score = best_story_score
                best_story = story
        except ObjectDeletedError:
            continue

    if best_global_score >= SIMILARITY_THRESHOLD and best_story:
        logger.info(f"  [Grouper] Matched to '{best_story.title}' (similarity: {best_global_score:.3f})")
        return best_story

    logger.info(f"  [Grouper] No match found (best score: {best_global_score:.3f}), creating new story")
    return None


def find_or_create_story(article_title, db, Story, recent_stories, article_embedding=None):
    """
    Main entry point. Try to match article to an existing story via
    vector similarity, or create a new one if no match found.
    Returns a Story object.
    """
    matched_story = find_matching_story(article_title, article_embedding, recent_stories)

    if matched_story:
        return matched_story

    new_title = clean_story_title(article_title)
    story = Story(title=new_title, summary=None)
    db.session.add(story)
    db.session.flush()
    logger.info(f"  [Grouper] Created new story: '{new_title}'")
    return story


def clean_story_title(article_title):
    """
    Generate a clean story title from an article title.
    Only strips source attribution at the end, keeps the rest intact.
    """
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


# Keep these available for the regroup function in fetch_and_store_articles.py
def get_candidate_stories(article_title, recent_stories, max_candidates=5):
    """Keyword pre-filter — kept for regroup_ungrouped_stories compatibility."""
    article_words = set(
        w.lower() for w in article_title.split()
        if len(w) > 3
    )
    scored = []
    for story in recent_stories:
        story_words = set(
            w.lower() for w in story.title.split()
            if len(w) > 3
        )
        overlap = len(article_words & story_words)
        if overlap > 0:
            scored.append((overlap, story))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [story for _, story in scored[:max_candidates]]


def ask_ollama_for_match(article_title, candidate_stories):
    """Kept for regroup_ungrouped_stories compatibility."""
    if not candidate_stories:
        return None

    story_list = "\n".join(
        f"{i+1}. {story.title}"
        for i, story in enumerate(candidate_stories)
    )

    prompt = f"""You are a news editor grouping articles into stories.

Article title: "{article_title}"

Existing stories:
{story_list}

Does this article cover the same event or ongoing situation as any of the stories listed above?

Rules:
- Only match if they are clearly about the same specific event or situation
- Do not match just because they share a broad topic (e.g. both about "politics")
- If it matches, respond with only the number of the matching story (e.g. "2")
- If it does not match any story, respond with only "0"
- Respond with a single number and nothing else"""

    try:
        response = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json={
                "model":  os.environ.get("OLLAMA_MODEL", ""),
                "prompt": prompt,
                "stream": False,
            },
            timeout=30,
        )
        response.raise_for_status()
        result = response.json().get("response", "").strip()

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
# muckscraperHeadlinesGoogleNEW/news_fetcher/story_grouper.py
# news_fetcher/story_grouper.py

import os
import re
import unicodedata
import numpy as np
import logging
from dataclasses import dataclass, field
from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context

from news_fetcher import llm_client

logger = logging.getLogger(__name__)

langfuse = Langfuse(
    public_key=os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
    secret_key=os.environ.get("LANGFUSE_SECRET_KEY", ""),
    host=os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
)

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")

SIMILARITY_THRESHOLD = 0.92
LOWER_THRESHOLD = 0.68

TITLE_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at",
    "after", "amid", "over", "with", "from", "into", "by", "new",
    "latest", "against", "says", "say", "just", "still", "could", "would",
    "us", "u", "s",
}

TITLE_TOKEN_REPLACEMENTS = {
    "xi": "xi_jinping",
    "jinping": "xi_jinping",
    "chinese": "china",
    "pm": "prime_minister",
    "prime": "prime_minister",
    "minister": "prime_minister",
    "orbán": "orban",
    "péter": "peter",
    "cpi": "inflation",
    "hospitalized": "injured",
    "hospitalizes": "injures",
    "injures": "injured",
    "wounded": "injured",
    "dies": "dead",
    "died": "dead",
    "deaths": "dead",
    "evacuated": "evacuate",
    "evacuating": "evacuate",
    "evacuation": "evacuate",
    "disembark": "evacuate",
    "disembarking": "evacuate",
    "years": "year",
    "ejected": "eject",
    "ejection": "eject",
    "elbowing": "elbow",
    "swinging": "swing",
    "surges": "rise",
    "surged": "rise",
    "spikes": "rise",
    "spiked": "rise",
    "soars": "rise",
    "soared": "rise",
    "jumps": "rise",
    "jumped": "rise",
    "accelerated": "rise",
    "shooting": "gunfire",
    "shots": "gunfire",
    "gunshots": "gunfire",
    "gunshot": "gunfire",
    "fired": "gunfire",
    "flees": "escape",
    "fled": "escape",
    "flee": "escape",
    "sentenced": "sentence",
    "sentencing": "sentence",
    "poisoning": "poison",
    "poisoned": "poison",
    "testify": "hearing",
    "testifies": "hearing",
    "testified": "hearing",
    "testimony": "hearing",
    "hearings": "hearing",
    "lawmakers": "congress",
    "lawmaker": "congress",
    "house": "congress",
    "congressional": "congress",
    "says": "say",
    "calls": "call",
    "sees": "say",
}


@observe()
def get_embedding(text):
    langfuse_context.update_current_observation(
        input=text,
        metadata={"model": EMBEDDING_MODEL, "provider": llm_client.EMBEDDING_PROVIDER}
    )
    embedding = llm_client.get_embedding(text)
    if embedding:
        langfuse_context.update_current_observation(output=str(embedding))
    return embedding


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


def strip_video_prefix(title):
    """
    Remove common video/media prefixes that distort embeddings.
    Returns the cleaned title string.
    """
    import re
    prefixes = [
        r'^WATCH\s*:\s*',
        r'^\[WATCH\]\s*',
        r'^WATCH\s*-\s*',
        r'^VIDEO\s*:\s*',
        r'^\[VIDEO\]\s*',
        r'^VIDEO\s*-\s*',
        r'^LISTEN\s*:\s*',
        r'^\[LISTEN\]\s*',
        r'^BREAKING\s*:\s*',
        r'^LIVE\s*:\s*',
        r'^LIVE UPDATES?\s*:\s*',
        r'^UPDATE\s*:\s*',
        r'^PHOTOS?\s*:\s*',
        r'^GALLERY\s*:\s*',
    ]
    for pattern in prefixes:
        title = re.sub(pattern, '', title, flags=re.IGNORECASE).strip()
    return title


def normalize_title_tokens(title):
    """Normalize headline wording for conservative near-duplicate checks."""
    cleaned = strip_video_prefix(title or "")
    cleaned = cleaned.replace("’", "'")
    cleaned = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"\b([a-z0-9]+)'s\b", r"\1", cleaned)
    replacements = (
        ("u.s.", "us"),
        ("u.s", "us"),
        ("cease-fire", "ceasefire"),
        ("cease fire", "ceasefire"),
        ("strikes down", "blocks"),
        ("ruled against", "blocks"),
        ("rules against", "blocks"),
        ("invalidates", "blocks"),
        ("rejects", "blocks"),
        ("blocked", "blocks"),
        ("tariffs", "tariff"),
        ("trump's", "trump"),
    )
    for old, new in replacements:
        cleaned = cleaned.replace(old, new)

    cleaned = re.sub(r"[^a-z0-9\s]", " ", cleaned)
    tokens = []
    for token in cleaned.split():
        token = TITLE_TOKEN_REPLACEMENTS.get(token, token)
        if token in TITLE_STOPWORDS or len(token) < 3:
            continue
        if token == "universal" or token == "global":
            token = "broad"
        tokens.append(token)
    return set(tokens)


def shared_title_tokens(title_a, title_b):
    return normalize_title_tokens(title_a) & normalize_title_tokens(title_b)


def titles_are_near_duplicates(article_title, story_title):
    article_tokens = normalize_title_tokens(article_title)
    story_tokens = normalize_title_tokens(story_title)
    if not article_tokens or not story_tokens:
        return False

    shared = article_tokens & story_tokens
    if len(shared) < 4:
        return False

    overlap = len(shared) / min(len(article_tokens), len(story_tokens))
    if overlap >= 0.8:
        return True

    # Ongoing event titles often differ by update angle ("evacuate" vs
    # "disembark", "injured" vs "hospitalized") while still sharing the
    # event-defining terms.
    if len(shared) >= 5 and overlap >= 0.5:
        return True

    return len(shared) >= 4 and overlap >= 0.55


@dataclass
class MatchDecision:
    story: object = None
    method: str = "none"
    confidence: float = 0.0
    candidate_story_ids: list[int] = field(default_factory=list)
    needs_review: bool = False


def _candidate_story_ids(stories, max_candidates=3):
    ids = []
    for story in stories[:max_candidates]:
        story_id = getattr(story, "id", None)
        if story_id is not None and story_id not in ids:
            ids.append(story_id)
    return ids


def find_matching_story_with_metadata(article_title, article_embedding, recent_stories, article_content=None, db=None):
    article_title = strip_video_prefix(article_title)
    if article_embedding is None:
        return MatchDecision()

    best_global_score = 0.0
    best_story = None
    best_title_match = None
    overlap_candidates = []

    from sqlalchemy.orm.exc import ObjectDeletedError

    # Title scan (Python): pure string ops, fast regardless of pool size.
    # Finds near-duplicate titles and keyword overlap candidates.
    for story in recent_stories:
        try:
            candidate_titles = [story.title]
            if story.headline:
                candidate_titles.append(story.headline)

            max_shared = 0
            for candidate_title in candidate_titles:
                shared = shared_title_tokens(article_title, candidate_title)
                max_shared = max(max_shared, len(shared))
                if titles_are_near_duplicates(article_title, candidate_title):
                    best_title_match = story
                    break
            if best_title_match:
                break
            if max_shared >= 3:
                overlap_candidates.append((max_shared, story))
        except ObjectDeletedError:
            continue

    if best_title_match:
        logger.info(f"  [Grouper] Matched to '{best_title_match.title}' via title overlap")
        return MatchDecision(
            story=best_title_match,
            method="title_overlap",
            confidence=1.0,
            candidate_story_ids=_candidate_story_ids([best_title_match]),
        )

    if overlap_candidates and llm_client.is_configured():
        overlap_candidates.sort(key=lambda item: item[0], reverse=True)
        unique_candidates = []
        seen_story_ids = set()
        for _, story in overlap_candidates:
            if story.id in seen_story_ids:
                continue
            seen_story_ids.add(story.id)
            unique_candidates.append(story)
            if len(unique_candidates) == 3:
                break

        story_snippets = []
        for story in unique_candidates:
            snippet = ""
            if story.articles:
                snippet = strip_to_snippet(story.articles[0].content)
            story_snippets.append(snippet)

        ollama_decision = ask_ollama_for_match(
            article_title,
            unique_candidates,
            article_content=article_content,
            story_snippets=story_snippets,
        )
        if ollama_decision:
            logger.info(f"  [Grouper] Matched to '{ollama_decision.title}' via title-overlap review")
            max_shared = max(shared_title_tokens(article_title, candidate.title) and len(shared_title_tokens(article_title, candidate.title)) or 0 for candidate in unique_candidates)
            return MatchDecision(
                story=ollama_decision,
                method="title_overlap_review",
                confidence=min(0.9, 0.6 + (0.05 * max_shared)),
                candidate_story_ids=_candidate_story_ids(unique_candidates),
                needs_review=True,
            )

    # Embedding search: pgvector query when db is available (large pools),
    # Python cosine scan as fallback for small candidate sets or if pgvector fails.
    embedding_match_done = False

    if db is not None and recent_stories:
        story_map = {s.id: s for s in recent_stories if s.id is not None}
        valid_ids = list(story_map)
        if valid_ids:
            emb_str = '[' + ','.join(f'{float(x):.8f}' for x in article_embedding) + ']'
            ids_pg = '{' + ','.join(str(i) for i in valid_ids) + '}'
            try:
                rows = db.session.execute(
                    db.text("""
                        SELECT a.story_id,
                               1 - MIN(a.embedding <=> (:emb)::vector) AS best_sim
                        FROM articles a
                        WHERE a.story_id = ANY((:ids)::int[])
                          AND a.embedding IS NOT NULL
                        GROUP BY a.story_id
                        ORDER BY best_sim DESC
                        LIMIT 5
                    """),
                    {"emb": emb_str, "ids": ids_pg},
                ).fetchall()
                for story_id, sim in rows:
                    sim = float(sim)
                    if sim > best_global_score:
                        best_global_score = sim
                        best_story = story_map.get(story_id)
                embedding_match_done = True
            except Exception as e:
                logger.warning(f"  [Grouper] pgvector search failed, falling back to Python: {e}")

    if not embedding_match_done:
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
        return MatchDecision(
            story=best_story,
            method="embedding_strong",
            confidence=best_global_score,
            candidate_story_ids=_candidate_story_ids([best_story]),
        )

    if best_global_score >= LOWER_THRESHOLD and best_story and llm_client.is_configured():
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
            return MatchDecision(
                story=ollama_decision,
                method="embedding_review",
                confidence=best_global_score,
                candidate_story_ids=_candidate_story_ids([best_story]),
                needs_review=True,
            )
        else:
            logger.info(f"  [Grouper] Ollama rejected match, creating new story")
            return MatchDecision(
                story=None,
                method="embedding_review_rejected",
                confidence=best_global_score,
                candidate_story_ids=_candidate_story_ids([best_story]),
                needs_review=True,
            )

    logger.info(f"  [Grouper] No match found (best score: {best_global_score:.3f}), creating new story")
    return MatchDecision(
        story=None,
        method="none",
        confidence=best_global_score,
        candidate_story_ids=_candidate_story_ids([best_story] if best_story else []),
    )


def find_matching_story(article_title, article_embedding, recent_stories, article_content=None, db=None):
    return find_matching_story_with_metadata(
        article_title,
        article_embedding,
        recent_stories,
        article_content=article_content,
        db=db,
    ).story


def find_or_create_story(article_title, db, Story, recent_stories, article_embedding=None, article_content=None):
    article_title = strip_video_prefix(article_title)
    match = find_matching_story_with_metadata(
        article_title,
        article_embedding,
        recent_stories,
        article_content=article_content,
        db=db,
    )
    matched_story = match.story

    if matched_story:
        return matched_story, match

    new_title = clean_story_title(article_title)
    story = Story(title=new_title, summary=None)
    db.session.add(story)
    db.session.flush()
    logger.info(f"  [Grouper] Created new story: '{new_title}'")
    return story, match


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


def build_match_prompt(article_title, story_list, article_content=None):
    article_block = f'Article title: "{article_title}"'
    if article_content:
        snippet = strip_to_snippet(article_content)
        if snippet:
            article_block += f"\nArticle context: {snippet}"

    return f"""You are a news editor grouping articles into stories.

{article_block}

Existing stories:
{story_list}

Does this article cover the same specific event or ongoing situation as any of the stories listed above?

Rules:
- Match if they are clearly about the same specific event or continuing storyline, even when the new article is an update, explainer, reaction, evacuation step, casualty update, or human-interest angle on that same event
- Do not match just because they share a broad topic or the same company/person/country
- Do not match if the stories contradict each other (e.g. "price drop" vs "price increase")
- Do not match broad opinion or analysis to a news event unless it is explicitly anchored to that same concrete event or ongoing situation
- Use the context snippets to distinguish between similar-sounding but different events
- If it matches, respond with only the number of the matching story (e.g. "2")
- If it does not match any story, respond with only "0"
- Respond with a single number and nothing else

Examples of correct NON-matches (should return 0):
- "Meta announces layoffs" vs "Epic Games lays off 900 workers" -> 0 (different companies, different events)
- "Measles outbreak in Michigan" vs "Measles outbreak in Washington state" -> 0 (same disease, different locations)
- "UFC fighter suspended for PED use" vs "MLB player suspended for PED use" -> 0 (different sports, different athletes)
- "iPhone security alert" vs "Chrome zero-day vulnerability" -> 0 (different platforms, different vulnerabilities)
- "NPR funding ruling" vs "Pentagon press policy ruling" -> 0 (different court cases)
- "Grocery chain closing 17 stores" vs "Restaurant chain closing locations" -> 0 (different companies)
- "Gold prices fall amid Iran war" vs "Trump says no ceasefire with Iran" -> 0 (different topics: finance vs diplomacy)
- "How the Iran war affects trade recovery" vs "Iranian official killed in strike" -> 0 (analysis piece vs news event)"""


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
    prompt = build_match_prompt(article_title, story_list, article_content=article_content)

    langfuse_context.update_current_observation(
        input=prompt,
        metadata={"provider": llm_client.LLM_PROVIDER}
    )
    result = llm_client.generate_text(prompt, timeout=30)
    if result is None:
        return None
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

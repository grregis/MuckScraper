import logging
import os
import time

import requests
from bs4 import BeautifulSoup
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from aggregator.models import db, Article, Story

logger = logging.getLogger(__name__)

STORY_INDEX = "stories"
ARTICLE_INDEX = "articles"
TASK_TIMEOUT_SECONDS = 60
TASK_POLL_SECONDS = 0.5
DOCUMENT_BATCH_SIZE = 200
# Rows loaded from Postgres per chunk. A full reindex used to load every
# article (with full content) at once; on 2026-09-30 that got the gunicorn
# worker running it killed 70s in, leaving the index two months stale.
LOAD_CHUNK_SIZE = 500
ID_PAGE_SIZE = 10000
INDEX_SETTINGS = {
    STORY_INDEX: {
        "searchableAttributes": [
            "title",
            "headline",
            "summary",
            "deep_report",
            "topic_names",
            "article_titles",
            "outlet_names",
            "article_sources",
        ],
        "filterableAttributes": [
            "topic_names",
            "scrape_statuses",
            "article_count",
        ],
        "sortableAttributes": [
            "latest_article_date",
            "created_at",
        ],
    },
    ARTICLE_INDEX: {
        "searchableAttributes": [
            "title",
            "source",
            "outlet_name",
            "summary",
            "content_text",
            "story_title",
            "story_headline",
            "topic_names",
        ],
        "filterableAttributes": [
            "story_id",
            "topic_names",
            "scrape_status",
        ],
        "sortableAttributes": [
            "date",
            "fetched_at",
        ],
    },
}


class SearchUnavailableError(RuntimeError):
    pass


def meili_enabled():
    return bool(os.environ.get("MEILI_URL", "").strip())


def _meili_url(path):
    base_url = os.environ.get("MEILI_URL", "").strip().rstrip("/")
    if not base_url:
        raise SearchUnavailableError("MEILI_URL is not configured")
    return f"{base_url}{path}"


def _meili_headers():
    headers = {"Content-Type": "application/json"}
    master_key = os.environ.get("MEILI_MASTER_KEY", "").strip()
    if master_key:
        headers["Authorization"] = f"Bearer {master_key}"
    return headers


def _request(method, path, **kwargs):
    timeout = kwargs.pop("timeout", 10)
    try:
        response = requests.request(
            method,
            _meili_url(path),
            headers=_meili_headers(),
            timeout=timeout,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise SearchUnavailableError(str(exc)) from exc

    if response.status_code >= 400:
        raise SearchUnavailableError(
            f"Meilisearch request failed: {response.status_code} {response.text[:200]}"
        )
    if response.content:
        return response.json()
    return None


def _normalize_text(value, limit=None):
    if not value:
        return ""
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    text = " ".join(text.split())
    if limit and len(text) > limit:
        return text[:limit]
    return text


def _serialize_story(story):
    latest_article_date = max((article.date for article in story.articles if article.date), default=None)
    return {
        "id": story.id,
        "title": story.title or "",
        "headline": story.headline or "",
        "summary": _normalize_text(story.summary, limit=4000),
        "deep_report": _normalize_text(story.deep_report, limit=6000),
        "topic_names": [topic.name for topic in story.topics],
        "article_titles": [article.title or "" for article in story.articles],
        "article_sources": [article.source or "" for article in story.articles if article.source],
        "outlet_names": [article.outlet.name for article in story.articles if article.outlet and article.outlet.name],
        "scrape_statuses": [article.scrape_status or "pending" for article in story.articles],
        "article_count": len(story.articles),
        "latest_article_date": latest_article_date.isoformat() if latest_article_date else None,
        "created_at": story.created_at.isoformat() if story.created_at else None,
    }


def _serialize_article(article):
    story = article.story
    return {
        "id": article.id,
        "story_id": article.story_id,
        "title": article.title or "",
        "source": article.source or "",
        "outlet_name": article.outlet.name if article.outlet and article.outlet.name else "",
        "summary": _normalize_text(article.summary, limit=3000),
        "content_text": _normalize_text(article.content, limit=3000),
        "story_title": story.title if story and story.title else "",
        "story_headline": story.headline if story and story.headline else "",
        "topic_names": [topic.name for topic in article.topics],
        "scrape_status": article.scrape_status or "pending",
        "date": article.date.isoformat() if article.date else None,
        "fetched_at": article.fetched_at.isoformat() if article.fetched_at else None,
        "bias_score": article.bias_score,
    }


def _wait_for_task(task_uid, timeout_seconds=TASK_TIMEOUT_SECONDS):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        payload = _request("GET", f"/tasks/{task_uid}", timeout=10)
        status = payload.get("status")
        if status == "succeeded":
            return payload
        if status == "failed":
            raise SearchUnavailableError(f"Meilisearch task failed: {payload}")
        time.sleep(TASK_POLL_SECONDS)
    raise SearchUnavailableError(f"Timed out waiting for Meilisearch task {task_uid}")


def _ensure_index_exists(uid):
    try:
        _request("GET", f"/indexes/{uid}")
        return
    except SearchUnavailableError as exc:
        if "index_not_found" not in str(exc):
            raise
    task = _request("POST", "/indexes", json={"uid": uid, "primaryKey": "id"})
    _wait_for_task(task["taskUid"])


def ensure_indexes():
    if not meili_enabled():
        raise SearchUnavailableError("MEILI_URL is not configured")

    for uid, settings in INDEX_SETTINGS.items():
        _ensure_index_exists(uid)
        task = _request("PATCH", f"/indexes/{uid}/settings", json=settings)
        _wait_for_task(task["taskUid"])


def _story_query():
    return Story.query.options(
        joinedload(Story.topics),
        joinedload(Story.articles).joinedload(Article.outlet),
        joinedload(Story.articles).joinedload(Article.topics),
    )


def _article_query():
    return Article.query.options(
        joinedload(Article.outlet),
        joinedload(Article.topics),
        joinedload(Article.story).joinedload(Story.topics),
    )


def _put_documents(index_name, documents, batch_size=DOCUMENT_BATCH_SIZE):
    for start in range(0, len(documents), batch_size):
        task = _request(
            "PUT",
            f"/indexes/{index_name}/documents",
            json=documents[start:start + batch_size],
            timeout=120,
        )
        _wait_for_task(task["taskUid"])


def _upsert_ids(index_name, model, query_fn, serialize, ids):
    """Load, serialize and upsert rows in chunks so memory stays bounded.
    Upserting (not delete-then-add) keeps search answering throughout."""
    ids = sorted(set(ids))
    written = 0
    for start in range(0, len(ids), LOAD_CHUNK_SIZE):
        chunk = ids[start:start + LOAD_CHUNK_SIZE]
        rows = query_fn().filter(model.id.in_(chunk)).all()
        documents = [serialize(row) for row in rows]
        if documents:
            _put_documents(index_name, documents)
        written += len(documents)
    return written


def _indexed_ids(index_name):
    ids = set()
    offset = 0
    while True:
        payload = _request(
            "GET",
            f"/indexes/{index_name}/documents",
            params={"fields": "id", "limit": ID_PAGE_SIZE, "offset": offset},
            timeout=60,
        )
        results = payload.get("results") or []
        ids.update(doc["id"] for doc in results)
        if len(results) < ID_PAGE_SIZE:
            return ids
        offset += ID_PAGE_SIZE


def _delete_missing(index_name, model):
    """Remove documents whose row no longer exists (e.g. a story merged away
    during grouping). Returns how many were deleted."""
    existing = {row[0] for row in db.session.query(model.id).all()}
    stale = sorted(_indexed_ids(index_name) - existing)
    for start in range(0, len(stale), ID_PAGE_SIZE):
        task = _request(
            "POST",
            f"/indexes/{index_name}/documents/delete-batch",
            json=stale[start:start + ID_PAGE_SIZE],
            timeout=120,
        )
        _wait_for_task(task["taskUid"])
    return len(stale)


def reindex_all():
    """Rebuild both indexes from Postgres. Upserts in bounded chunks and then
    deletes stale documents, so search keeps working while it runs."""
    ensure_indexes()

    story_ids = [row[0] for row in db.session.query(Story.id).all()]
    article_ids = [row[0] for row in db.session.query(Article.id).all()]
    stories = _upsert_ids(STORY_INDEX, Story, _story_query, _serialize_story, story_ids)
    articles = _upsert_ids(ARTICLE_INDEX, Article, _article_query, _serialize_article, article_ids)

    return {
        "story_documents": stories,
        "article_documents": articles,
        "stories_deleted": _delete_missing(STORY_INDEX, Story),
        "articles_deleted": _delete_missing(ARTICLE_INDEX, Article),
    }


def index_changed_since(since):
    """Bring the indexes up to date with what changed since `since` (a naive
    UTC datetime, normally the start of a pipeline run).

    Covers new articles, the stories they joined, stories whose headline or
    summary was (re)generated, the articles of those stories (their documents
    carry the story headline), and stories/articles that no longer exist.
    """
    _ensure_index_exists(STORY_INDEX)
    _ensure_index_exists(ARTICLE_INDEX)

    new_article_rows = (
        db.session.query(Article.id, Article.story_id)
        .filter(Article.fetched_at >= since)
        .all()
    )
    story_ids = {story_id for _, story_id in new_article_rows if story_id}
    story_ids.update(
        row[0] for row in db.session.query(Story.id).filter(
            or_(
                Story.created_at >= since,
                Story.summary_generated_at >= since,
                Story.headline_generated_at >= since,
            )
        ).all()
    )
    article_ids = {article_id for article_id, _ in new_article_rows}
    for start in range(0, len(story_ids), ID_PAGE_SIZE):
        chunk = sorted(story_ids)[start:start + ID_PAGE_SIZE]
        article_ids.update(
            row[0] for row in db.session.query(Article.id).filter(Article.story_id.in_(chunk)).all()
        )

    return {
        "story_documents": _upsert_ids(STORY_INDEX, Story, _story_query, _serialize_story, story_ids),
        "article_documents": _upsert_ids(ARTICLE_INDEX, Article, _article_query, _serialize_article, article_ids),
        "stories_deleted": _delete_missing(STORY_INDEX, Story),
        "articles_deleted": _delete_missing(ARTICLE_INDEX, Article),
    }


def search_story_ids(query, limit=250):
    _ensure_index_exists(STORY_INDEX)
    _ensure_index_exists(ARTICLE_INDEX)

    story_payload = _request(
        "POST",
        f"/indexes/{STORY_INDEX}/search",
        json={"q": query, "limit": limit},
    )
    article_payload = _request(
        "POST",
        f"/indexes/{ARTICLE_INDEX}/search",
        json={"q": query, "limit": limit},
    )

    ordered_story_ids = []
    seen_story_ids = set()

    for hit in story_payload.get("hits", []):
        story_id = hit.get("id")
        if story_id and story_id not in seen_story_ids:
            seen_story_ids.add(story_id)
            ordered_story_ids.append(story_id)

    for hit in article_payload.get("hits", []):
        story_id = hit.get("story_id")
        if story_id and story_id not in seen_story_ids:
            seen_story_ids.add(story_id)
            ordered_story_ids.append(story_id)

    return ordered_story_ids


def healthcheck():
    if not meili_enabled():
        return False
    try:
        payload = _request("GET", "/health")
        return payload.get("status") == "available"
    except SearchUnavailableError:
        return False


def main():
    from aggregator import create_app

    app = create_app()
    with app.app_context():
        counts = reindex_all()
        logger.info(
            "Reindexed Meilisearch stories=%s articles=%s",
            counts["story_documents"],
            counts["article_documents"],
        )
        print(
            f"Reindexed Meilisearch stories={counts['story_documents']} "
            f"articles={counts['article_documents']}"
        )


if __name__ == "__main__":
    main()

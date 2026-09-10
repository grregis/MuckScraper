# news_fetcher/fetch_and_store_articles/providers.py
"""
The NewsAPI / GNews fetch entry points that feed store_articles(), and
the raw-payload retention that runs alongside them.
"""

import logging
from datetime import datetime, timedelta
import json
import os
import requests
from newsapi import NewsApiClient
from aggregator import db
from .ingestion import store_articles

logger = logging.getLogger(__name__)


def fetch_newsapi(topic_name, mode="top", query=None, country="us", category=None):
    """Fetch articles from NewsAPI and store them."""
    api_key = os.environ.get("NEWS_API_KEY", "")
    if not api_key:
        logger.warning("NEWS_API_KEY not set, skipping NewsAPI fetch.")
        return {
            "provider": "newsapi",
            "topic_name": topic_name,
            "status": "skipped",
            "reason": "missing_api_key",
            "input_articles": 0,
            "stored": 0,
        }

    newsapi = NewsApiClient(api_key=api_key)

    try:
        if mode == "query" and query:
            logger.info(f"[NewsAPI] Fetching query: {query}")
            results = newsapi.get_everything(
                q=query,
                language="en",
                sort_by="publishedAt",
                page_size=100,
            )
        else:
            label = f"country={country}" if country else ""
            label += f" category={category}" if category else ""
            logger.info(f"[NewsAPI] Fetching top headlines ({label.strip()})")
            kwargs = {"page_size": 100}
            if country:
                kwargs["country"] = country
            if category:
                kwargs["category"] = category
            results = newsapi.get_top_headlines(**kwargs)

        raw_articles = results.get("articles", [])
        logger.info(f"[NewsAPI] Fetched {len(raw_articles)} articles")

        normalized = []
        for a in raw_articles:
            published_at_str = a.get("publishedAt")
            try:
                published_at = datetime.fromisoformat(
                    published_at_str.replace("Z", "+00:00")
                ).replace(tzinfo=None) if published_at_str else datetime.utcnow()
            except Exception:
                published_at = datetime.utcnow()

            normalized.append({
                "title":        a.get("title"),
                "content":      a.get("content") or "",
                "url":          a.get("url"),
                "source_name":  (a.get("source") or {}).get("name", "Unknown"),
                "published_at": published_at,
                "image_url":    a.get("urlToImage"),
            })

        metrics = store_articles(normalized, topic_name, provider="newsapi")

        # Store raw payload
        from aggregator.models import RawArticlePayload
        raw = RawArticlePayload(
            source="newsapi",
            topic_name=topic_name,
            payload=json.dumps(results),
        )
        db.session.add(raw)
        db.session.commit()
        metrics["status"] = "ok"
        return metrics

    except Exception as e:
        db.session.rollback()
        logger.error(f"[NewsAPI] Error fetching {topic_name}: {e}")
        return {
            "provider": "newsapi",
            "topic_name": topic_name,
            "status": "error",
            "reason": str(e),
            "input_articles": 0,
            "stored": 0,
        }


def fetch_gnews(topic_name, query=None, category=None):
    """Fetch articles from GNews API and store them."""
    api_key = os.environ.get("GNEWS_API_KEY", "")
    if not api_key:
        logger.warning("GNEWS_API_KEY not set, skipping GNews fetch.")
        return {
            "provider": "gnews",
            "topic_name": topic_name,
            "status": "skipped",
            "reason": "missing_api_key",
            "input_articles": 0,
            "stored": 0,
        }

    try:
        if query:
            logger.info(f"[GNews] Fetching query: {query}")
            url = "https://gnews.io/api/v4/search"
            params = {
                "q":      query,
                "lang":   "en",
                "max":    20,
                "apikey": api_key,
            }
        elif category:
            logger.info(f"[GNews] Fetching category: {category}")
            url = "https://gnews.io/api/v4/top-headlines"
            params = {
                "category": category,
                "lang":     "en",
                "country":  "us",
                "max":      20,
                "apikey":   api_key,
            }
        else:
            logger.info(f"[GNews] Fetching top headlines")
            url = "https://gnews.io/api/v4/top-headlines"
            params = {
                "lang":    "en",
                "country": "us",
                "max":     20,
                "apikey":  api_key,
            }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        raw_articles = data.get("articles", [])
        logger.info(f"[GNews] Fetched {len(raw_articles)} articles")

        normalized = []
        for a in raw_articles:
            published_at_str = a.get("publishedAt")
            try:
                published_at = datetime.fromisoformat(
                    published_at_str.replace("Z", "+00:00")
                ).replace(tzinfo=None) if published_at_str else datetime.utcnow()
            except Exception:
                published_at = datetime.utcnow()

            source = a.get("source") or {}
            normalized.append({
                "title":        a.get("title"),
                "content":      a.get("content") or a.get("description") or "",
                "url":          a.get("url"),
                "source_name":  source.get("name", "Unknown"),
                "published_at": published_at,
                "image_url":    a.get("image"),
            })

        metrics = store_articles(normalized, topic_name, provider="gnews")

        # Store raw payload
        from aggregator.models import RawArticlePayload
        raw = RawArticlePayload(
            source="gnews",
            topic_name=topic_name,
            payload=json.dumps(data),
        )
        db.session.add(raw)
        db.session.commit()
        metrics["status"] = "ok"
        return metrics

    except Exception as e:
        db.session.rollback()
        logger.error(f"[GNews] Error fetching {topic_name}: {e}")
        return {
            "provider": "gnews",
            "topic_name": topic_name,
            "status": "error",
            "reason": str(e),
            "input_articles": 0,
            "stored": 0,
        }
    


def cleanup_old_payloads():
    """Delete raw API payloads older than 30 days."""
    from aggregator.models import RawArticlePayload
    cutoff = datetime.utcnow() - timedelta(days=30)
    old = RawArticlePayload.query.filter(RawArticlePayload.fetched_at < cutoff).all()
    if old:
        logger.info(f"Deleting {len(old)} raw payloads older than 30 days...")
        for payload in old:
            db.session.delete(payload)
        db.session.commit()
        logger.info("Cleanup complete.")
    else:
        logger.info("No old payloads to clean up.")


def fetch_and_store_articles(topic_name, mode="top", query=None,
                              country="us", category=None,
                              gnews_query=None, gnews_category=None):
    """
    Main entry point. Fetches from both NewsAPI and GNews for a given topic.
    """
    newsapi_metrics = fetch_newsapi(topic_name, mode=mode, query=query,
                                    country=country, category=category)
    gnews_metrics = fetch_gnews(topic_name, query=gnews_query, category=gnews_category)
    cleanup_old_payloads()
    return {
        "topic_name": topic_name,
        "providers": {
            "newsapi": newsapi_metrics,
            "gnews": gnews_metrics,
        },
    }

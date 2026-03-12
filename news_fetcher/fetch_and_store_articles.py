# news_fetcher/fetch_and_store_articles.py

from aggregator import create_app, db
from aggregator.models import Article, Outlet, Story, Topic
from newsapi import NewsApiClient
from news_fetcher.outlet_bias_llm import get_outlet_bias_from_llm
from news_fetcher.summarizer import summarize_story, check_ollama_status
from datetime import datetime
import requests
import os

app = create_app()

BLOCKED_SOURCES = [
    "github.com",
    "github.blog",
    "dev.to",
    "stackoverflow.com",
    "reddit.com",
    "npmjs.com",
    "pypi.org",
]

BLOCKED_TITLE_KEYWORDS = [
    "starred",
    "forked",
    "pull request",
    "merged",
    "repository",
    "npm package",
    "pypi",
    "added to pypi",
    "released on pypi",
    "week in review",
    "patch tuesday",
    "added to npm",
    "new release:",
    "changelog:",
]


def guess_story_title(title):
    if ":" in title:
        return title.split(":")[0]
    if "-" in title:
        return title.split("-")[0]
    return " ".join(title.split()[:6])


def retry_unrated_outlets():
    """Find outlets with no bias score and retry Ollama."""
    unrated = Outlet.query.filter_by(bias_score=None).all()

    if not unrated:
        print("No unrated outlets to retry.")
        return

    print(f"Found {len(unrated)} unrated outlets, retrying Ollama...")

    for outlet in unrated:
        print(f"  Retrying bias score for: {outlet.name}")
        bias_score = get_outlet_bias_from_llm(outlet.name)

        if bias_score is not None:
            print(f"  Got score {bias_score} for {outlet.name}, updating...")
            outlet.bias_score = bias_score
            for article in outlet.articles:
                article.bias_score = bias_score
        else:
            print(f"  Still couldn't rate {outlet.name}, will try again next fetch.")

    db.session.commit()
    print("Finished retrying unrated outlets.")


def get_or_create_topic(topic_name):
    """Get existing topic or create a new one."""
    topic = Topic.query.filter_by(name=topic_name).first()
    if not topic:
        topic = Topic(name=topic_name)
        db.session.add(topic)
        db.session.flush()
    return topic


def store_articles(articles_data, topic_name):
    """
    Store a list of normalized article dicts into the database,
    tagging them with the given topic.
    articles_data: list of dicts with keys:
        title, content, url, source_name, published_at
    """
    topic = get_or_create_topic(topic_name)
    stored = 0

    for article in articles_data:
        title        = article.get("title")
        content      = article.get("content") or ""
        url          = article.get("url")
        source_name  = article.get("source_name", "Unknown")
        published_at = article.get("published_at", datetime.utcnow())

        if not title or not url:
            continue

        if any(blocked in url.lower() for blocked in BLOCKED_SOURCES):
            print(f"Skipping blocked source: {url}")
            continue

        if any(kw in title.lower() for kw in BLOCKED_TITLE_KEYWORDS):
            print(f"Skipping blocked title: {title}")
            continue

        existing = Article.query.filter_by(url=url).first()
        if existing:
            if topic not in existing.topics:
                existing.topics.append(topic)
            print(f"Skipping duplicate: {title}")
            continue

        print(f"Processing: {title}")

        outlet = Outlet.query.filter_by(name=source_name).first()
        if not outlet:
            print(f"  New outlet found: {source_name}, asking Ollama for bias score...")
            bias_score = get_outlet_bias_from_llm(source_name)
            outlet = Outlet(
                name=source_name,
                url=url,
                description="N/A",
                bias_score=bias_score
            )
            db.session.add(outlet)
            db.session.flush()

        story_title = guess_story_title(title)
        story = Story.query.filter_by(title=story_title).first()
        if not story:
            story = Story(title=story_title, summary=None)
            db.session.add(story)
            db.session.flush()

        if topic not in story.topics:
            story.topics.append(topic)

        new_article = Article(
            title=title,
            content=content,
            source=source_name,
            outlet_id=outlet.id,
            story_id=story.id,
            url=url,
            date=published_at,
            bias_score=outlet.bias_score
        )
        new_article.topics.append(topic)
        db.session.add(new_article)
        stored += 1

    db.session.commit()
    print(f"Stored {stored} new articles for topic: {topic_name}")


def fetch_newsapi(topic_name, mode="top", query=None, country="us", category=None):
    """Fetch articles from NewsAPI and store them."""
    api_key = os.environ.get("NEWS_API_KEY", "")
    if not api_key:
        print("NEWS_API_KEY not set, skipping NewsAPI fetch.")
        return

    newsapi = NewsApiClient(api_key=api_key)

    try:
        if mode == "query" and query:
            print(f"[NewsAPI] Fetching query: {query}")
            results = newsapi.get_everything(
                q=query,
                language="en",
                sort_by="publishedAt",
                page_size=100,
            )
        else:
            label = f"country={country}" if country else ""
            label += f" category={category}" if category else ""
            print(f"[NewsAPI] Fetching top headlines ({label.strip()})")
            kwargs = {"page_size": 100}
            if country:
                kwargs["country"] = country
            if category:
                kwargs["category"] = category
            results = newsapi.get_top_headlines(**kwargs)

        raw_articles = results.get("articles", [])
        print(f"[NewsAPI] Fetched {len(raw_articles)} articles")

        normalized = []
        for a in raw_articles:
            published_at_str = a.get("publishedAt")
            try:
                published_at = datetime.fromisoformat(
                    published_at_str.replace("Z", "+00:00")
                ) if published_at_str else datetime.utcnow()
            except Exception:
                published_at = datetime.utcnow()

            normalized.append({
                "title":        a.get("title"),
                "content":      a.get("content") or "",
                "url":          a.get("url"),
                "source_name":  (a.get("source") or {}).get("name", "Unknown"),
                "published_at": published_at,
            })

        store_articles(normalized, topic_name)

    except Exception as e:
        print(f"[NewsAPI] Error fetching {topic_name}: {e}")


def fetch_gnews(topic_name, query=None, category=None):
    """Fetch articles from GNews API and store them."""
    api_key = os.environ.get("GNEWS_API_KEY", "")
    if not api_key:
        print("GNEWS_API_KEY not set, skipping GNews fetch.")
        return

    try:
        if query:
            print(f"[GNews] Fetching query: {query}")
            url = "https://gnews.io/api/v4/search"
            params = {
                "q":      query,
                "lang":   "en",
                "max":    10,
                "apikey": api_key,
            }
        elif category:
            print(f"[GNews] Fetching category: {category}")
            url = "https://gnews.io/api/v4/top-headlines"
            params = {
                "category": category,
                "lang":     "en",
                "country":  "us",
                "max":      10,
                "apikey":   api_key,
            }
        else:
            print(f"[GNews] Fetching top headlines")
            url = "https://gnews.io/api/v4/top-headlines"
            params = {
                "lang":    "en",
                "country": "us",
                "max":     10,
                "apikey":  api_key,
            }

        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        raw_articles = data.get("articles", [])
        print(f"[GNews] Fetched {len(raw_articles)} articles")

        normalized = []
        for a in raw_articles:
            published_at_str = a.get("publishedAt")
            try:
                published_at = datetime.fromisoformat(
                    published_at_str.replace("Z", "+00:00")
                ) if published_at_str else datetime.utcnow()
            except Exception:
                published_at = datetime.utcnow()

            source = a.get("source") or {}
            normalized.append({
                "title":        a.get("title"),
                "content":      a.get("content") or a.get("description") or "",
                "url":          a.get("url"),
                "source_name":  source.get("name", "Unknown"),
                "published_at": published_at,
            })

        store_articles(normalized, topic_name)

    except Exception as e:
        print(f"[GNews] Error fetching {topic_name}: {e}")


def fetch_and_store_articles(topic_name, mode="top", query=None,
                              country="us", category=None,
                              gnews_query=None, gnews_category=None):
    """
    Main entry point. Fetches from both NewsAPI and GNews for a given topic.
    """
    retry_unrated_outlets()
    fetch_newsapi(topic_name, mode=mode, query=query,
                  country=country, category=category)
    fetch_gnews(topic_name, query=gnews_query, category=gnews_category)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        fetch_and_store_articles("US Headlines")
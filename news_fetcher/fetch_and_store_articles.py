# news_fetcher/fetch_and_store_articles.py

from aggregator import create_app, db
from aggregator.models import Article, Outlet, Story, Topic
from newsapi import NewsApiClient
from news_fetcher.outlet_bias_llm import get_outlet_bias_from_llm
from news_fetcher.summarizer import summarize_story, check_ollama_status
from datetime import datetime
import os

app = create_app()


def guess_story_title(title):
    if ":" in title:
        return title.split(":")[0]
    if "-" in title:
        return title.split("-")[0]
    return " ".join(title.split()[:6])

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
    "v1.", "v2.", "v3.",   # version release titles
]


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
            Article.query.filter_by(outlet_id=outlet.id).update(
                {"bias_score": bias_score}
            )
        else:
            print(f"  Still couldn't rate {outlet.name}, will try again next fetch.")

    db.session.commit()
    print("Finished retrying unrated outlets.")


def summarize_new_stories():
    """Find stories without summaries and generate them via Ollama."""
    if not check_ollama_status():
        print("Ollama is not reachable, skipping summarization.")
        return

    # Find stories where summary is null or still equals the title (placeholder)
    unsummarized = Story.query.filter(
        (Story.summary == None) |
        (Story.summary == Story.title)
    ).all()

    if not unsummarized:
        print("All stories already have summaries.")
        return

    print(f"Found {len(unsummarized)} stories needing summaries...")

    for story in unsummarized:
        summary = summarize_story(story)
        if summary:
            story.summary = summary

    db.session.commit()
    print("Finished generating summaries.")


def fetch_and_store_articles(mode="top", query=None, country="us", category=None):
    """
    mode="top"   : fetch top headlines, optionally filtered by country and/or category
    mode="query" : fetch everything matching a search query
    """
    newsapi = NewsApiClient(api_key=os.environ["NEWS_API_KEY"])

    retry_unrated_outlets()

    if mode == "query" and query:
        print(f"Fetching articles for query: {query}")
        results = newsapi.get_everything(
            q=query,
            language="en",
            sort_by="publishedAt",
            page_size=100,
        )
    else:
        label = f"country={country}" if country else ""
        label += f" category={category}" if category else ""
        print(f"Fetching top headlines ({label.strip()})")

        kwargs = {"page_size": 100}
        if country:
            kwargs["country"] = country
        if category:
            kwargs["category"] = category

        results = newsapi.get_top_headlines(**kwargs)

    articles = results.get("articles", [])
    print(f"Fetched {len(articles)} articles")

    for article in articles:

        title = article.get("title")
        content = article.get("content") or ""
        url = article.get("url")

        if not title or not url:
            continue

        source = article.get("source") or {}
        source_name = source.get("name", "Unknown")

        published_at_str = article.get("publishedAt")
        if published_at_str:
            published_at = datetime.fromisoformat(
                published_at_str.replace("Z", "+00:00")
            )
        else:
            published_at = datetime.utcnow()

        existing = Article.query.filter_by(url=url).first()
        if existing:
            print("Skipping duplicate:", title)
            continue

        print("Processing:", title)

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
        db.session.add(new_article)

    db.session.commit()
    print("Finished inserting articles.")

    # Un-comment to generate all summaries for any new stories
    # summarize_new_stories()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        fetch_and_store_articles()
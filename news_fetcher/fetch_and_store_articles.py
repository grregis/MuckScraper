# news_fetcher/fetch_and_store_articles.py

from aggregator import create_app, db
from aggregator.models import Article, Outlet, Story, Topic
from newsapi import NewsApiClient
from datetime import datetime
import os

app = create_app()


def guess_story_title(title):
    if ":" in title:
        return title.split(":")[0]
    if "-" in title:
        return title.split("-")[0]
    return " ".join(title.split()[:6])


def fetch_and_store_articles(query=None):

    newsapi = NewsApiClient(api_key=os.environ["NEWS_API_KEY"])

    if query:
        print(f"Fetching articles for query: {query}")
        results = newsapi.get_everything(
            q=query,
            language="en",
            sort_by="publishedAt",
            page_size=100,
        )
    else:
        print("Fetching US top headlines")
        results = newsapi.get_top_headlines(
            country="us",
            page_size=100,
        )

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
            outlet = Outlet(name=source_name, url=url, description="N/A")
            db.session.add(outlet)
            db.session.flush()

        story_title = guess_story_title(title)
        story = Story.query.filter_by(title=story_title).first()
        if not story:
            story = Story(title=story_title, summary=story_title)
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
        )
        db.session.add(new_article)

    db.session.commit()
    print("Finished inserting articles")


if __name__ == "__main__":
    with app.app_context():
        fetch_and_store_articles()  # No query = top US headlines
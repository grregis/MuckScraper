# news_fetcher/scheduler.py

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from aggregator import create_app, db
from news_fetcher.fetch_and_store_articles import fetch_and_store_articles
import logging
import sys

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Each entry fetches from both NewsAPI and GNews
SCHEDULED_FETCHES = [
    {
        "label":          "US Headlines",
        "mode":           "top",
        "country":        "us",
        "category":       None,
        "query":          None,
        "gnews_query":    None,
        "gnews_category": "general",
    },
    {
        "label":          "World Headlines",
        "mode":           "top",
        "country":        None,
        "category":       None,
        "query":          None,
        "gnews_query":    None,
        "gnews_category": "world",
    },
    {
        "label":          "US Politics",
        "mode":           "query",
        "country":        None,
        "category":       None,
        "query":          "US politics congress white house",
        "gnews_query":    "US politics",
        "gnews_category": None,
    },
    {
        "label":          "Technology",
        "mode":           "top",
        "country":        "us",
        "category":       "technology",
        "query":          None,
        "gnews_query":    None,
        "gnews_category": "technology",
    },
    {
        "label":          "Gaming",
        "mode":           "top",
        "country":        "us",
        "category":       "entertainment",
        "query":          None,
        "gnews_query":    "gaming video games",
        "gnews_category": None,
    },
]

app = create_app()


def run_all_fetches():
    logging.info("=== Starting scheduled fetch run ===")
    with app.app_context():
        for fetch in SCHEDULED_FETCHES:
            logging.info(f"--- Fetching: {fetch['label']} ---")
            try:
                fetch_and_store_articles(
                    topic_name=fetch["label"],
                    mode=fetch["mode"],
                    query=fetch.get("query"),
                    country=fetch.get("country"),
                    category=fetch.get("category"),
                    gnews_query=fetch.get("gnews_query"),
                    gnews_category=fetch.get("gnews_category"),
                )
            except Exception as e:
                logging.error(f"Error fetching {fetch['label']}: {e}")

    logging.info("=== Scheduled fetch run complete ===")


if __name__ == "__main__":
    logging.info("Scheduler starting up...")

    with app.app_context():
        db.create_all()

    # Run once immediately on startup
    run_all_fetches()

    # Schedule every 3 hours
    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_all_fetches,
        trigger=IntervalTrigger(hours=3),
        id="fetch_job",
        name="3-hourly news fetch",
        replace_existing=True
    )

    logging.info("Scheduler running. Next fetch in 3 hours.")
    scheduler.start()
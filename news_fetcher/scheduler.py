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

# Define all the fetches to run every hour
SCHEDULED_FETCHES = [
    {"label": "US Top Headlines",       "mode": "top",      "country": "us",  "category": None},
    {"label": "World Top Headlines",    "mode": "top",      "country": None,  "category": None},
    {"label": "US Politics",            "mode": "query",    "query": "US politics congress white house"},
    {"label": "Technology Headlines",   "mode": "category", "category": "technology"},
    {"label": "Gaming Headlines",       "mode": "query",    "query": "gaming video games"},
    {"label": "Linux Headlines",        "mode": "query",    "query": "linux open source"},
]

app = create_app()


def run_all_fetches():
    logging.info("=== Starting scheduled fetch run ===")
    with app.app_context():
        for fetch in SCHEDULED_FETCHES:
            logging.info(f"--- Fetching: {fetch['label']} ---")
            try:
                if fetch["mode"] == "top":
                    fetch_and_store_articles(
                        mode="top",
                        country=fetch.get("country"),
                        category=fetch.get("category")
                    )
                elif fetch["mode"] == "category":
                    fetch_and_store_articles(
                        mode="top",
                        category=fetch.get("category")
                    )
                elif fetch["mode"] == "query":
                    fetch_and_store_articles(
                        mode="query",
                        query=fetch.get("query")
                    )
            except Exception as e:
                logging.error(f"Error fetching {fetch['label']}: {e}")

    logging.info("=== Scheduled fetch run complete ===")


if __name__ == "__main__":
    logging.info("Scheduler starting up...")

    # Run once immediately on startup
    run_all_fetches()

    # Then schedule to run every hour
    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_all_fetches,
        trigger=IntervalTrigger(hours=1),
        id="hourly_fetch",
        name="Hourly news fetch",
        replace_existing=True
    )

    logging.info("Scheduler running. Next fetch in 1 hour.")
    scheduler.start()
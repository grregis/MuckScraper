# muckscraperHeadlinesGoogleNEW/news_fetcher/scheduler.py
# news_fetcher/scheduler.py

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from aggregator import create_app, db
from aggregator.models import AppSetting
from news_fetcher.fetch_and_store_articles import fetch_and_store_articles, ollama_catchup
from datetime import datetime, timedelta
import logging
import sys

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# New schedule times in Eastern Time: 12am, 7am, 12pm, 6pm
SCHEDULE_HOURS = "0,7,12,18"
TIMEZONE = "America/New_York"

SCHEDULED_FETCHES = [
    # === NATIONAL / POLITICS ===
    {
        "label":          "US Politics",
        "mode":           "query",
        "country":        None,
        "category":       None,
        "query":          "US politics congress white house senate supreme court",
        "gnews_query":    "US politics congress white house",
        "gnews_category": None,
    },
    # === BUSINESS / ECONOMY ===
    {
        "label":          "Business & Economy",
        "mode":           "top",
        "country":        "us",
        "category":       "business",
        "query":          None,
        "gnews_query":    None,
        "gnews_category": "business",
    },
    # === SCIENCE / HEALTH ===
    {
        "label":          "Science & Health",
        "mode":           "query",
        "country":        None,
        "category":       None,
        "query":          "science health medicine research climate environment",
        "gnews_query":    "science health medicine",
        "gnews_category": "health",
    },
    # === TECHNOLOGY — kept but scoped to real news, not dev releases ===
    {
        "label":          "Technology",
        "mode":           "top",
        "country":        "us",
        "category":       "technology",
        "query":          None,
        "gnews_query":    None,
        "gnews_category": "technology",
    },
    # === NATIONAL SECURITY / FOREIGN POLICY ===
    {
        "label":          "National Security & Foreign Policy",
        "mode":           "query",
        "country":        None,
        "category":       None,
        "query":          "military NATO foreign policy diplomacy war conflict sanctions",
        "gnews_query":    "military NATO diplomacy conflict",
        "gnews_category": None,
    },
]
app = create_app()


def get_last_fetch_time():
    """Get the last fetch timestamp from the database."""
    setting = AppSetting.query.filter_by(key="last_fetch").first()
    if setting and setting.value:
        try:
            return datetime.fromisoformat(setting.value)
        except Exception:
            return None
    return None


def set_last_fetch_time():
    """Store the current time as the last fetch timestamp."""
    setting = AppSetting.query.filter_by(key="last_fetch").first()
    if setting:
        setting.value = datetime.utcnow().isoformat()
    else:
        setting = AppSetting(key="last_fetch", value=datetime.utcnow().isoformat())
        db.session.add(setting)
    db.session.commit()


def should_fetch_now():
    """
    Returns True if it's been more than 1 hour since the last fetch,
    or if no fetch has ever been recorded. This allows the scheduler 
    to fetch on startup if it was offline during a scheduled window.
    """
    last_fetch = get_last_fetch_time()
    if last_fetch is None:
        logging.info("No previous fetch found, fetching now.")
        return True

    elapsed = datetime.utcnow() - last_fetch
    threshold = timedelta(hours=1)

    if elapsed >= threshold:
        logging.info(f"Last fetch was {elapsed} ago, fetching now.")
        return True
    else:
        logging.info(
            f"Last fetch was {int(elapsed.total_seconds() / 60)} minutes ago. "
            f"Skipping startup fetch."
        )
        return False


# Track Ollama state between runs
ollama_was_online = False


def run_all_fetches():
    global ollama_was_online

    logging.info("=== Starting scheduled fetch run ===")
    with app.app_context():
        # Check if Ollama just came back online
        from news_fetcher.summarizer import check_ollama_status
        ollama_is_online = check_ollama_status()

        if ollama_is_online and not ollama_was_online:
            logging.info("Ollama just came back online — running catchup...")
            try:
                ollama_catchup()
            except Exception as e:
                logging.error(f"Ollama catchup error: {e}")

        ollama_was_online = ollama_is_online

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

        set_last_fetch_time()

    logging.info("=== Scheduled fetch run complete ===")



if __name__ == "__main__":
    logging.info("Scheduler starting up...")

    with app.app_context():
        db.create_all()
        # Only fetch on startup if enough time has passed
        if should_fetch_now():
            run_all_fetches()
        else:
            logging.info("Skipping startup fetch.")

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_all_fetches,
        trigger=CronTrigger(hour=SCHEDULE_HOURS, minute=0, timezone=TIMEZONE),
        id="fetch_job",
        name="Scheduled news fetch (America/New_York)",
        replace_existing=True
    )

    logging.info(f"Scheduler running. Fetching at {SCHEDULE_HOURS} in {TIMEZONE}.")
    scheduler.start()
# news_fetcher/fetch_and_store_articles/__main__.py
# `python -m news_fetcher.fetch_and_store_articles` -- the old module's
# __main__ block, unchanged.
from aggregator import db
from . import app, fetch_and_store_articles

with app.app_context():
    db.create_all()
    fetch_and_store_articles("US Politics")

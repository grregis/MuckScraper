# news_fetcher/backfill_images.py

from aggregator import create_app, db
from aggregator.models import Article, RawArticlePayload
from news_fetcher.fetch_and_store_articles import normalize_url
from datetime import datetime, timedelta
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = create_app()

def backfill_images_last_7_days():
    """
    Iterate through raw payloads from the last 7 days, 
    extract image URLs, and update existing Article records.
    """
    with app.app_context():
        cutoff = datetime.utcnow() - timedelta(days=7)
        payloads = RawArticlePayload.query.filter(RawArticlePayload.fetched_at >= cutoff).all()
        
        logger.info(f"Processing {len(payloads)} raw payloads from the last 7 days...")
        
        updated_count = 0
        
        for p in payloads:
            try:
                # Load the raw JSON data
                data = json.loads(p.payload)
                articles_list = data.get("articles", [])
                
                for a in articles_list:
                    raw_url = a.get("url")
                    if not raw_url:
                        continue
                        
                    norm_url = normalize_url(raw_url)
                    
                    # Find article by normalized URL
                    article_rec = Article.query.filter_by(url=norm_url).first()
                    
                    if article_rec and not article_rec.image_url:
                        # Extract image based on the specific API source format
                        img_url = None
                        if p.source == "newsapi":
                            img_url = a.get("urlToImage")
                        elif p.source == "gnews":
                            img_url = a.get("image")
                            
                        if img_url:
                            article_rec.image_url = img_url
                            updated_count += 1
                            
                # Commit progress periodically
                db.session.commit()
                
            except Exception as e:
                logger.error(f"Error processing payload {p.id}: {e}")
                db.session.rollback()
                continue
                
        logger.info(f"Successfully backfilled image URLs for {updated_count} articles from the last 7 days.")

if __name__ == "__main__":
    backfill_images_last_7_days()

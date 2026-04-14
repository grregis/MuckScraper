# muckscraperHeadlinesGoogleNEW/news_fetcher/cleanup_duplicates.py
from aggregator import create_app, db
from aggregator.models import Article, Story
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def normalize_url(url):
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    except Exception:
        return url

def cleanup_duplicates():
    app = create_app()
    with app.app_context():
        logger.info("Starting duplicate cleanup...")
        
        # Fetch all articles ordered by ID (so we process older ones first)
        articles = Article.query.order_by(Article.id).all()
        logger.info(f"Scanning {len(articles)} articles...")
        
        seen_urls = {}
        seen_title_outlet = {}
        
        duplicates = []
        
        for article in articles:
            # 1. Normalize URL
            norm_url = normalize_url(article.url)
            
            # Check by URL
            if norm_url in seen_urls:
                existing = seen_urls[norm_url]
                duplicates.append((article, existing, "URL"))
                continue
            
            # Check by Title + Outlet
            key = (article.title, article.outlet_id)
            if key in seen_title_outlet:
                existing = seen_title_outlet[key]
                duplicates.append((article, existing, "Title+Outlet"))
                continue
                
            # Not a duplicate, mark as seen
            seen_urls[norm_url] = article
            seen_title_outlet[key] = article
            
        if not duplicates:
            logger.info("No duplicates found.")
            return

        logger.info(f"Found {len(duplicates)} duplicates. removing...")
        
        deleted_count = 0
        for duplicate, original, reason in duplicates:
            # Keep the one with more content
            d_len = len(duplicate.content or "")
            o_len = len(original.content or "")
            
            if d_len > o_len + 500: # Significant difference
                # Swap! Keep duplicate, remove original
                logger.info(f"  Swapping: Keeping newer article {duplicate.id} ({d_len} chars) over {original.id} ({o_len} chars)")
                
                # Update our tracking maps to point to the better article
                norm_url = normalize_url(duplicate.url)
                seen_urls[norm_url] = duplicate
                key = (duplicate.title, duplicate.outlet_id)
                seen_title_outlet[key] = duplicate
                
                db.session.delete(original)
            else:
                # Default: remove the duplicate
                logger.info(f"  Deleting duplicate {duplicate.id} ({reason}): '{duplicate.title}'")
                db.session.delete(duplicate)
            
            deleted_count += 1
            
        db.session.commit()
        logger.info(f"Cleanup complete. Deleted {deleted_count} duplicates.")

if __name__ == "__main__":
    cleanup_duplicates()

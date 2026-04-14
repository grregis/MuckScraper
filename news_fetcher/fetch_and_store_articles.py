# muckscraperHeadlinesGoogleNEW/news_fetcher/fetch_and_store_articles.py
# news_fetcher/fetch_and_store_articles.py

from aggregator import create_app, db
from aggregator.models import Article, Outlet, Story, Topic
from newsapi import NewsApiClient
from news_fetcher.outlet_bias_llm import get_outlet_bias_from_llm
from news_fetcher.summarizer import summarize_story, check_ollama_status, generate_deep_report, summarize_article
from news_fetcher.scraper import scrape_article
from datetime import datetime
import requests
import os
import json
from news_fetcher.story_grouper import find_or_create_story, get_embedding
from datetime import datetime, timedelta
from news_fetcher.topic_classifier import classify_article
from news_fetcher.headline_generator import generate_story_headline, generate_missing_headlines
import logging

logger = logging.getLogger(__name__)

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
    "box office",
    "box score",
    "game recap",
    "highlights:",
    "traded to",
    "signs with",
    "scores in",
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
        logger.info("No unrated outlets to retry.")
        return

    logger.info(f"Found {len(unrated)} unrated outlets, retrying Ollama...")

    for outlet in unrated:
        logger.info(f"  Retrying bias score for: {outlet.name}")
        bias_score = get_outlet_bias_from_llm(outlet.name)

        if bias_score is not None:
            logger.info(f"  Got score {bias_score} for {outlet.name}, updating...")
            outlet.bias_score = bias_score
            for article in outlet.articles:
                article.bias_score = bias_score
        else:
            logger.warning(f"  Still couldn't rate {outlet.name}, will try again next fetch.")

    db.session.commit()
    logger.info("Finished retrying unrated outlets.")


def get_or_create_topic(topic_name):
    """Get existing topic or create a new one, handling race conditions."""
    topic = Topic.query.filter_by(name=topic_name).first()
    if not topic:
        try:
            topic = Topic(name=topic_name)
            db.session.add(topic)
            db.session.flush()
        except Exception:
            # Another process created it at the same time, roll back and fetch it
            db.session.rollback()
            topic = Topic.query.filter_by(name=topic_name).first()
    return topic


def normalize_url(url):
    """Strip query parameters from URL to detect duplicates."""
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(url)
        # Keep only scheme, netloc, and path
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    except Exception:
        return url


def detect_duplicate_outlet_content(content, outlet_id, exclude_article_id=None):
    """
    Check if scraped content is near-identical to other articles from the same outlet.
    This catches login/error pages that return the same HTML for every blocked request.
    Returns (is_duplicate: bool, reason: str or None).
    """
    if not content or not outlet_id:
        return False, None

    from news_fetcher.scraper import sanitize_html
    import re

    def strip_to_text(html, max_chars=2000):
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]

    clean_new = strip_to_text(content)
    if len(clean_new) < 100:
        return False, None

    from difflib import SequenceMatcher

    recent = Article.query.filter(
        Article.outlet_id == outlet_id,
        Article.content != None,
        Article.content != "",
    )
    if exclude_article_id:
        recent = recent.filter(Article.id != exclude_article_id)
    recent = recent.order_by(Article.id.desc()).limit(10).all()

    match_count = 0
    for article in recent:
        if not article.content:
            continue
        clean_existing = strip_to_text(article.content)
        if len(clean_existing) < 100:
            continue
        ratio = SequenceMatcher(None, clean_new, clean_existing).ratio()
        if ratio > 0.85:
            match_count += 1

    if match_count >= 2:
        reason = f"Bad scrape: content near-identical to {match_count} other articles from same outlet (login/error page)"
        return True, reason

    return False, None


def store_articles(articles_data, topic_name):
    """
    Store a list of normalized article dicts into the database,
    tagging them with the given topic.
    articles_data: list of dicts with keys:
        title, content, url, source_name, published_at, image_url
    """
    stored = 0

    # Pre-fetch recent stories once for the whole batch
    cutoff = datetime.utcnow() - timedelta(days=7)
    recent_stories = Story.query.filter(Story.created_at >= cutoff).all()
    logger.info(f"  [Grouper] Loaded {len(recent_stories)} recent stories for matching")

    for article in articles_data:
        title        = article.get("title")
        content      = article.get("content") or ""
        raw_url      = article.get("url")
        source_name  = article.get("source_name", "Unknown")
        published_at = article.get("published_at", datetime.utcnow())
        image_url    = article.get("image_url")

        if not title or not raw_url:
            continue
            
        url = normalize_url(raw_url)

        if any(blocked in url.lower() for blocked in BLOCKED_SOURCES):
            logger.debug(f"Skipping blocked source: {url}")
            continue

        if any(kw in title.lower() for kw in BLOCKED_TITLE_KEYWORDS):
            logger.debug(f"Skipping blocked title: {title}")
            continue

        # Check for URL duplicate (normalized)
        existing = Article.query.filter_by(url=url).first()
        if existing:
            logger.debug(f"Skipping duplicate URL: {title}")
            continue

        # Check for Title + Source duplicate (catch same article, different URL)
        # First get/create outlet to have the ID
        outlet = Outlet.query.filter_by(name=source_name).first()
        if outlet:
            existing_title = Article.query.filter_by(title=title, outlet_id=outlet.id).first()
            if existing_title:
                logger.debug(f"Skipping duplicate Title+Outlet: {title}")
                continue
        
        logger.info(f"Processing: {title}")

        if not outlet:
            logger.info(f"  New outlet found: {source_name}, asking Ollama for bias score...")
            bias_score = get_outlet_bias_from_llm(source_name)
            outlet = Outlet(
                name=source_name,
                url=url,
                description="N/A",
                bias_score=bias_score
            )
            db.session.add(outlet)
            db.session.flush()

        # Generate embedding for this article
        # Use title + first 500 chars of content for better semantic matching
        from news_fetcher.summarizer import strip_html
        text_for_embedding = f"{title} {strip_html(content)[:400]}"
        article_embedding = get_embedding(text_for_embedding)
        
        story = find_or_create_story(title, db, Story, recent_stories,
                                     article_embedding=article_embedding,
                                     article_content=content)

        # Add new story to recent_stories so subsequent articles
        # in this same batch can match against it
        if story not in recent_stories:
            recent_stories.append(story)

        # Classify article into topics via Ollama
        from aggregator.models import Topic as TopicModel
        classified_topic_names = classify_article(title, content)
        for classified_name in classified_topic_names:
            classified_topic = TopicModel.query.filter_by(name=classified_name).first()
            if not classified_topic:
                classified_topic = TopicModel(name=classified_name)
                db.session.add(classified_topic)
                db.session.flush()
            if classified_topic not in story.topics:
                story.topics.append(classified_topic)

        scraped_content = scrape_article(url)
        if scraped_content:
            # Check if this looks like a duplicate login/error page across the outlet
            is_dup, dup_reason = detect_duplicate_outlet_content(scraped_content, outlet.id)
            if is_dup:
                logger.warning(f"  [Scraper] {dup_reason} — clearing content and blocking domain for {url[:60]}")
                from news_fetcher.scraper import add_to_blocklist
                add_to_blocklist(url, dup_reason)
                scraped_content = None

        if scraped_content:
            final_content = scraped_content
        else:
            from news_fetcher.scraper import sanitize_html
            final_content = sanitize_html(f"<div>{content}</div>") if content else ""

        # Ensure embedding is a list, not a string
        if isinstance(article_embedding, str):
            import json
            article_embedding = json.loads(article_embedding)

        new_article = Article(
            title=title,
            content=final_content,
            source=source_name,
            outlet_id=outlet.id,
            story_id=story.id,
            url=url,
            date=published_at,
            fetched_at=datetime.utcnow(),
            bias_score=outlet.bias_score,
            image_url=image_url,
            embedding=article_embedding
        )

        db.session.add(new_article)
        # IMPORTANT: Append to story.articles so it's visible to find_matching_story
        # for subsequent articles in this SAME loop iteration.
        story.articles.append(new_article)

        # Tag article with same topics as story
        for t in story.topics:
            if t not in new_article.topics:
                new_article.topics.append(t)

        # Generate headline if this is a multi-article story (2+ articles)
        if len(story.articles) >= 2:
            db.session.flush() # Ensure article is associated for headline generator
            headline = generate_story_headline(story)
            if headline:
                story.headline = headline
        else:
            # For single-article stories, ensure story headline is cleared
            # so the UI falls back to story.title (original article title)
            story.headline = None
                
        stored += 1

    db.session.commit()
    logger.info(f"Stored {stored} new articles for topic: {topic_name}")


def fetch_newsapi(topic_name, mode="top", query=None, country="us", category=None):
    """Fetch articles from NewsAPI and store them."""
    api_key = os.environ.get("NEWS_API_KEY", "")
    if not api_key:
        logger.warning("NEWS_API_KEY not set, skipping NewsAPI fetch.")
        return

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
                ) if published_at_str else datetime.utcnow()
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

        store_articles(normalized, topic_name)

        # Store raw payload
        from aggregator.models import RawArticlePayload
        raw = RawArticlePayload(
            source="newsapi",
            topic_name=topic_name,
            payload=json.dumps(results),
        )
        db.session.add(raw)
        db.session.commit()

    except Exception as e:
        logger.error(f"[NewsAPI] Error fetching {topic_name}: {e}")


def fetch_gnews(topic_name, query=None, category=None):
    """Fetch articles from GNews API and store them."""
    api_key = os.environ.get("GNEWS_API_KEY", "")
    if not api_key:
        logger.warning("GNEWS_API_KEY not set, skipping GNews fetch.")
        return

    try:
        if query:
            logger.info(f"[GNews] Fetching query: {query}")
            url = "https://gnews.io/api/v4/search"
            params = {
                "q":      query,
                "lang":   "en",
                "max":    10,
                "apikey": api_key,
            }
        elif category:
            logger.info(f"[GNews] Fetching category: {category}")
            url = "https://gnews.io/api/v4/top-headlines"
            params = {
                "category": category,
                "lang":     "en",
                "country":  "us",
                "max":      10,
                "apikey":   api_key,
            }
        else:
            logger.info(f"[GNews] Fetching top headlines")
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
        logger.info(f"[GNews] Fetched {len(raw_articles)} articles")

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
                "image_url":    a.get("image"),
            })

        store_articles(normalized, topic_name)

        # Store raw payload
        from aggregator.models import RawArticlePayload
        raw = RawArticlePayload(
            source="gnews",
            topic_name=topic_name,
            payload=json.dumps(data),
        )
        db.session.add(raw)
        db.session.commit()

    except Exception as e:
        logger.error(f"[GNews] Error fetching {topic_name}: {e}")
    

def regroup_ungrouped_stories():
    """
    Find single-article stories from the last 7 days and attempt
    to re-group them using the vector similarity matcher.
    """
    from news_fetcher.story_grouper import find_matching_story

    cutoff = datetime.utcnow() - timedelta(days=7)

    # Find stories that only have one article
    all_recent = Story.query.filter(Story.created_at >= cutoff).all()
    ungrouped_stories = [s for s in all_recent if len(s.articles) == 1]

    if not ungrouped_stories:
        logger.info("No single-article stories to re-group.")
        return

    logger.info(f"Checking {len(ungrouped_stories)} single-article stories for potential matches...")

    # Potential targets for merging (stories with > 1 article)
    multi_article_stories = [s for s in all_recent if len(s.articles) > 1]

    merged = 0
    for story in ungrouped_stories:
        if not story.articles:
            continue

        article = story.articles[0]
        if not article.embedding:
            continue

        # Try to match to an existing multi-article story
        matched = find_matching_story(article.title, article.embedding, multi_article_stories, article_content=article.content)

        if matched and matched.id != story.id:
            logger.info(f"  [Re-group] Merging '{story.title}' into '{matched.title}'")

            # Move article to matched story
            article.story_id = matched.id
            db.session.flush()

            # Merge topic tags
            for topic in story.topics:
                if topic not in matched.topics:
                    matched.topics.append(topic)

            # Generate/Update headline for the matched story now that it has a new article
            from news_fetcher.headline_generator import generate_story_headline
            headline = generate_story_headline(matched)
            if headline:
                matched.headline = headline

            # Delete the now-empty story
            db.session.delete(story)
            merged += 1

    db.session.commit()
    logger.info(f"Re-grouping complete. Merged {merged} stories.")


def retry_unsummarized_stories(batch_size=10):
    """Find stories without summaries and generate them — capped at batch_size."""
    if not check_ollama_status():
        logger.info("Ollama offline, skipping auto-summarization.")
        return

    unsummarized = Story.query.filter(
        (Story.summary == None) |
        (Story.summary == Story.title)
    ).limit(batch_size).all()

    if not unsummarized:
        logger.info("All stories have summaries.")
        return

    logger.info(f"Summarizing up to {batch_size} stories...")
    for story in unsummarized:
        if not story.articles:
            continue
        summary = summarize_story(story)
        if summary:
            story.summary = summary
            logger.info(f"  Summarized: {story.title[:60]}")

    db.session.commit()
    logger.info("Finished summarization batch.")


def generate_missing_deep_reports(batch_size=5):
    """Find multi-article stories picked for headlines that don't have deep reports."""
    if not check_ollama_status():
        logger.info("Ollama offline, skipping deep report generation.")
        return

    from sqlalchemy import func
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=2)

    # Only target stories with a headline_score > 0 (meaning they were picked by the ranker)
    undissected = Story.query.join(Article).group_by(Story.id).having(
        func.count(Article.id) >= 2
    ).filter(
        Story.headline_score > 0,
        Story.created_at >= cutoff,
        (Story.deep_report == None) | (Story.deep_report == "")
    ).order_by(Story.headline_score.desc()).limit(batch_size).all()

    if not undissected:
        logger.info("No headline stories need deep reports.")
        return

    logger.info(f"Generating deep reports for {len(undissected)} headline stories...")
    from news_fetcher.summarizer import generate_deep_report
    for story in undissected:
        report = generate_deep_report(story)
        if report:
            story.deep_report = report
            logger.info(f"  Generated deep report for: {story.title[:60]}")
    
    db.session.commit()
    logger.info("Finished deep report batch.")


def generate_missing_embeddings(batch_size=50):
    """Generate embeddings for articles that don't have one yet."""
    from news_fetcher.story_grouper import get_embedding

    missing = Article.query.filter(Article.embedding == None).limit(batch_size).all()

    if not missing:
        logger.info("All articles have embeddings.")
        return

    logger.info(f"Generating embeddings for {len(missing)} articles...")
    count = 0
    for article in missing:
        # Align with store_articles and force_regroup_all: use title + content
        text = f"{article.title} {(article.content or '')[:500]}"
        embedding = get_embedding(text)
        if embedding:
            article.embedding = embedding
            count += 1

    db.session.commit()
    logger.info(f"Generated {count} embeddings.")


def audit_existing_scrapes(batch_size=200):
    """
    Scan all stored article content for bad scrapes — login walls, captchas,
    bot detection pages, and outlet-level duplicate content.
    Clears bad content and adds offending domains to the blocklist.
    """
    from news_fetcher.scraper import detect_bad_scrape, get_domain, add_to_blocklist
    import re

    articles = Article.query.filter(
        Article.content != None,
        Article.content != ""
    ).order_by(Article.outlet_id, Article.id).all()

    logger.info(f"[Audit] Scanning {len(articles)} articles for bad scrapes...")

    cleared = 0
    auto_blocked = set()

    for i, article in enumerate(articles):
        if not article.content:
            continue

        domain = get_domain(article.url)

        # If domain was already flagged this run, just clear the content
        if domain and domain in auto_blocked:
            article.content = None
            cleared += 1
            continue

        # Strong/weak indicator check
        is_bad, reason = detect_bad_scrape(article.content)
        if is_bad:
            logger.info(f"  [Audit] Bad scrape detected: {article.title[:60]} — {reason}")
            article.content = None
            cleared += 1
            if domain:
                add_to_blocklist(article.url, reason)
                auto_blocked.add(domain)
            continue

        # Duplicate content check
        is_dup, dup_reason = detect_duplicate_outlet_content(
            article.content, article.outlet_id, exclude_article_id=article.id
        )
        if is_dup:
            logger.info(f"  [Audit] Duplicate scrape detected: {article.title[:60]} — {dup_reason}")
            article.content = None
            cleared += 1
            if domain:
                add_to_blocklist(article.url, dup_reason)
                auto_blocked.add(domain)
            continue

        # Commit in batches
        if (i + 1) % batch_size == 0:
            db.session.commit()
            logger.info(f"  [Audit] Progress: {i + 1}/{len(articles)}, cleared {cleared} so far")

    db.session.commit()
    logger.info(f"[Audit] Complete. Cleared {cleared} articles, auto-blocked {len(auto_blocked)} domains.")


def force_resummarize_all(batch_size=20):
    """
    Force re-generate summaries and deep reports for all stories and articles
    using the updated specialized journalist personas.
    """
    if not check_ollama_status():
        logger.info("Ollama offline, skipping force re-summarization.")
        return

    logger.info("=== Force re-summarization starting ===")
    
    # 1. Update Story Summaries
    stories = Story.query.all()
    logger.info(f"Re-summarizing {len(stories)} stories...")
    for i, story in enumerate(stories):
        if not story.articles:
            continue
        summary = summarize_story(story)
        if summary:
            story.summary = summary
        
        if (i + 1) % batch_size == 0:
            db.session.commit()
            logger.info(f"  Progress (Stories): {i+1}/{len(stories)}")
    
    db.session.commit()

    # 2. Update Deep Reports
    from sqlalchemy import func
    multi_article_stories = Story.query.join(Article).group_by(Story.id).having(
        func.count(Article.id) >= 2
    ).all()
    logger.info(f"Re-analyzing {len(multi_article_stories)} multi-article stories (Deep Reports)...")
    for i, story in enumerate(multi_article_stories):
        report = generate_deep_report(story)
        if report:
            story.deep_report = report
        
        if (i + 1) % 5 == 0: # Deep reports are slower
            db.session.commit()
            logger.info(f"  Progress (Deep Reports): {i+1}/{len(multi_article_stories)}")
            
    db.session.commit()

    # 3. Update Article Summaries
    articles = Article.query.filter(Article.content != None).all()
    logger.info(f"Re-summarizing {len(articles)} articles...")
    for i, article in enumerate(articles):
        summary = summarize_article(article)
        if summary:
            article.summary = summary
        
        if (i + 1) % batch_size == 0:
            db.session.commit()
            logger.info(f"  Progress (Articles): {i+1}/{len(articles)}")

    db.session.commit()
    logger.info("=== Force re-summarization complete ===")


def force_regroup_all():
    """
    Force re-group ALL articles using vector similarity embeddings.
    Regenerates ALL embeddings first (to include content), then re-assigns every article
    to the best matching story.
    """
    from news_fetcher.story_grouper import get_embedding, find_matching_story

    if not check_ollama_status():
        logger.info("Ollama offline, skipping force re-group.")
        return

    logger.info("=== Force re-group starting ===")
    logger.info("  [Force Regroup] Step 1: Regenerating embeddings...")

    # Step 1: Regenerate embeddings for ALL articles to ensure content is included
    all_articles = Article.query.all()
    logger.info(f"Regenerating embeddings for {len(all_articles)} articles (this may take a while)...")
    
    for i, article in enumerate(all_articles):
        # Use title + first 500 chars of content
        text = f"{article.title} {(article.content or '')[:500]}"
        embedding = get_embedding(text)
        if embedding:
            article.embedding = embedding
        
        if (i + 1) % 50 == 0:
            db.session.commit()
            logger.info(f"  [Force Regroup] Embeddings progress: {i + 1}/{len(all_articles)}")

    db.session.commit()
    logger.info("Embeddings regenerated.")
    logger.info("  [Force Regroup] Step 2: Starting re-grouping loop...")

    # Step 2: Get all articles with embeddings (should be all of them now)
    # Re-query to be safe
    all_articles = Article.query.filter(Article.embedding != None).all()
    logger.info(f"Re-grouping {len(all_articles)} articles...")

    # Step 3: Delete all existing stories and re-create from scratch
    # First detach all articles from stories and clear topics
    for article in all_articles:
        article.story_id = None
        article.topics = [] # Clear in-memory topics to avoid IntegrityError on flush/commit
    db.session.flush()

    # Clear junction tables first to avoid foreign key violations
    db.session.execute(db.text("DELETE FROM story_topics"))
    db.session.execute(db.text("DELETE FROM article_topics"))
    db.session.flush()

    # Delete all stories
    Story.query.delete()
    db.session.flush()
    
    # CRITICAL: Expire all objects after bulk deletes so the identity map 
    # doesn't contain references to the deleted Story objects.
    db.session.expire_all()

    # Step 4: Re-group articles one by one and re-attach topics
    from news_fetcher.story_grouper import clean_story_title
    from news_fetcher.topic_classifier import classify_article
    from aggregator.models import Topic as TopicModel

    new_stories = []
    try:
        for i, article in enumerate(all_articles):
            matched = find_matching_story(
                article.title, article.embedding, new_stories, article_content=article.content
            )

            if matched:
                story = matched
            else:
                new_title = clean_story_title(article.title)
                story = Story(title=new_title, summary=None)
                db.session.add(story)
                db.session.flush()
                new_stories.append(story)
            
            # Re-attach article to story
            article.story = story
            # Maintain in-memory list so find_matching_story can see it
            if article not in story.articles:
                story.articles.append(article)

            # Re-attach topic tags
            topic_names = classify_article(article.title, article.content or "")
            for topic_name in topic_names:
                topic = TopicModel.query.filter_by(name=topic_name).first()
                if not topic:
                    topic = TopicModel(name=topic_name)
                    db.session.add(topic)
                    db.session.flush()
                
                # Since we cleared article.topics = [] above, this is safe
                if topic not in article.topics:
                    article.topics.append(topic)
                if topic not in story.topics:
                    story.topics.append(topic)

            # Commit in batches of 50
            if (i + 1) % 50 == 0:
                db.session.commit()
                logger.info(f"  [Force Regroup] Grouping progress: {i + 1}/{len(all_articles)}")

    except Exception as e:
        logger.error(f"  [Force Regroup] CRITICAL ERROR: {e}")
        import traceback
        logger.error(traceback.format_exc())
        db.session.rollback()
        raise

    db.session.commit()

    # Step 5: Generate headlines for all multi-article stories
    logger.info("Generating AI headlines for regrouped stories...")
    logger.info("  [Force Regroup] Step 3: Generating AI headlines...")
    generate_missing_headlines()

    logger.info(f"=== Force re-group complete. Created {len(new_stories)} stories. ===")


def reclassify_all_articles(batch_size=50):
    """
    Reclassify all existing articles into the new topic system using Ollama.
    Clears existing topic tags and reassigns based on content.
    """
    from news_fetcher.topic_classifier import classify_article
    from aggregator.models import Topic as TopicModel

    if not check_ollama_status():
        logger.info("Ollama offline, skipping reclassification.")
        return

    # Clear all existing topic assignments
    db.session.execute(db.text("DELETE FROM article_topics"))
    db.session.execute(db.text("DELETE FROM story_topics"))
    db.session.flush()
    db.session.expire_all() # Ensure stale collections are cleared
    logger.info("Cleared existing topic assignments.")

    all_articles = Article.query.all()
    total = len(all_articles)
    logger.info(f"Reclassifying {total} articles...")

    for i, article in enumerate(all_articles):
        # Clear in-memory topics for this article to be safe
        article.topics = []
        
        topic_names = classify_article(article.title, article.content or "")

        for topic_name in topic_names:
            topic = TopicModel.query.filter_by(name=topic_name).first()
            if not topic:
                topic = TopicModel(name=topic_name)
                db.session.add(topic)
                db.session.flush()
            
            if topic not in article.topics:
                article.topics.append(topic)
            
            if article.story:
                if topic not in article.story.topics:
                    article.story.topics.append(topic)

        # Commit in batches
        if (i + 1) % batch_size == 0:
            db.session.commit()
            logger.info(f"  Progress: {i + 1}/{total}")

    db.session.commit()
    logger.info(f"Reclassification complete. Processed {total} articles.")


def ollama_catchup():
    """
    Run all Ollama-dependent tasks that may have been skipped
    while Ollama was offline.
    """
    logger.info("=== Ollama catchup starting ===")
    audit_existing_scrapes()
    generate_missing_embeddings(batch_size=50)
    generate_missing_headlines()
    regroup_ungrouped_stories()
    retry_unrated_outlets()
    retry_unsummarized_stories(batch_size=10)
    logger.info("=== Ollama catchup complete ===")


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
    retry_unrated_outlets()
    fetch_newsapi(topic_name, mode=mode, query=query,
                  country=country, category=category)
    fetch_gnews(topic_name, query=gnews_query, category=gnews_category)
    retry_unsummarized_stories()
    cleanup_old_payloads()


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        fetch_and_store_articles("US Politics")

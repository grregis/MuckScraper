# news_fetcher/fetch_and_store_articles/maintenance.py
"""
Whole-database repair actions behind the Admin Tools bulk buttons:
embedding backfill, scrape audit, force re-summarize / re-group /
reclassify, and the ollama_catchup bundle. Read the docstrings before
running any of these -- several are global, not scoped.
"""

import logging
from aggregator import db
from aggregator.models import Article, Story
from news_fetcher import llm_client as _llm
from news_fetcher.headline_generator import generate_missing_headlines
from news_fetcher.summarizer import summarize_story, check_ollama_status, generate_deep_report, summarize_article
from .grouping_review import regroup_ungrouped_stories
from .ingestion import detect_duplicate_outlet_content
from .outlets import retry_unrated_outlets

logger = logging.getLogger(__name__)


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
        # Align with store_articles: use title + snippet
        from news_fetcher.story_grouper import strip_video_prefix
        clean_title = strip_video_prefix(article.title)
        embed_text = clean_title
        if article.content:
            from news_fetcher.summarizer import strip_html
            snippet = strip_html(article.content)[:200].strip()
            embed_text = f"{clean_title}. {snippet}"
        embedding = get_embedding(embed_text)
        if embedding is not None:
            article.embedding = embedding
            count += 1

    db.session.commit()
    logger.info(f"Generated {count} embeddings.")


def audit_existing_scrapes(batch_size=200):
    """
    Scan non-audited article content for bad scrapes — login walls, captchas,
    bot detection pages, and outlet-level duplicate content.
    Clears bad content and adds offending domains to the blocklist.
    """
    from news_fetcher.scraper import detect_bad_scrape, get_domain, add_to_blocklist
    import re

    articles = Article.query.filter(
        Article.scrape_audited == False,
        Article.content != None,
        Article.content != ""
    ).order_by(Article.outlet_id, Article.id).all()

    if not articles:
        logger.info("[Audit] No new articles to audit.")
        return

    logger.info(f"[Audit] Scanning {len(articles)} new articles for bad scrapes...")

    cleared = 0
    auto_blocked = set()

    for i, article in enumerate(articles):
        # Mark as audited immediately
        article.scrape_audited = True

        if not article.content:
            continue

        domain = get_domain(article.url)

        # If domain was already flagged this run, just clear the content
        if domain and domain in auto_blocked:
            article.content = None
            article.scrape_status = "blocked"
            article.scrape_failure_reason = "domain_auto_blocked_same_audit_run"
            cleared += 1
            continue

        # Strong/weak indicator check
        is_bad, reason = detect_bad_scrape(article.content)
        if is_bad:
            logger.info(f"  [Audit] Bad scrape detected: {article.title[:60]} — {reason}")
            article.content = None
            article.scrape_status = "blocked"
            article.scrape_failure_reason = reason
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
            article.scrape_status = "blocked"
            article.scrape_failure_reason = dup_reason
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
    if not check_ollama_status(_llm.TIER_QUALITY):
        logger.info("Quality-tier LLM offline, skipping force re-summarization.")
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


def reclassify_all_articles(batch_size=50):
    """
    Reclassify all existing articles into the new topic system using Ollama.
    Clears existing topic tags and reassigns based on content.
    """
    from news_fetcher.topic_classifier import classify_article
    from aggregator.models import Topic as TopicModel

    if not check_ollama_status(_llm.TIER_FAST):
        logger.info("Fast-tier LLM offline, skipping reclassification.")
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
    # Headlines run *after* regrouping, not before: regroup_ungrouped_stories()
    # merges stories, and a headline written before a merge describes the wrong
    # article set. It used to regenerate them inline; now the batch pass here
    # covers both the merged stories and anything that was already missing.
    regroup_ungrouped_stories()
    generate_missing_headlines()
    retry_unrated_outlets()
    logger.info("=== Ollama catchup complete ===")

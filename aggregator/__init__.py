# aggregator/__init__.py
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
import os
import requests

db = SQLAlchemy()

TOPICS = [
    {"label": "US Headlines"},
    {"label": "US Politics"},
    {"label": "International Headlines"},
    {"label": "Science/Technology"},
    {"label": "Gaming"},
    {"label": "Sports"},
    {"label": "Business/Finance"},
    {"label": "Other"},
]


def create_app():
    app = Flask(__name__)

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

    db.init_app(app)

    from .models import Article, Story, Topic

    @app.route("/")
    def index():
        return redirect(url_for("list_articles"))

    @app.route("/multi-stories")
    def multi_article_stories():
        # Dedicated view for multi-article stories, paginated at 50
        return list_articles(per_page=50, force_multi=True)

    @app.route("/articles")
    def list_articles(per_page=25, force_multi=False):
        active_label = request.args.get("topic", None)
        page = request.args.get("page", 1, type=int)
        show_single = request.args.get("show_single", "false") == "true"
        
        # If force_multi is True (from /multi-stories), override show_single
        if force_multi:
            show_single = False

        # Always join with Article and group by Story.id to allow sorting by max(article.date)
        from sqlalchemy import func
        query = Story.query.join(Article).group_by(Story.id)

        if not show_single:
            query = query.having(func.count(Article.id) > 1)

        if active_label:
            topic = Topic.query.filter_by(name=active_label).first()
            if topic:
                query = query.filter(Story.topics.contains(topic))
            else:
                # If topic doesn't exist, we'll get no results
                query = query.filter(False)

        # Order by the NEWEST article publish date in the group
        pagination = query.order_by(func.max(Article.date).desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        stories = pagination.items if pagination else []
        total_pages = pagination.pages if pagination else 0

        # Filter out aggregator duplicates if original content exists
        from datetime import datetime
        AGGREGATORS = ["Yahoo News", "Google News", "MSN", "AOL", "Startups"]
        for story in stories:
            originals = []
            aggregators = []
            has_good_original = False
            
            seen_articles = set()

            # Sort articles by date descending initially
            sorted_articles = sorted(story.articles, key=lambda x: x.date or datetime.min, reverse=True)

            for article in sorted_articles:
                # 1. Deduplicate by (title, outlet_id) to handle exact duplicates with different URLs
                # normalizing title slightly to catch very close matches?
                # keeping it simple for now: exact title match
                key = (article.title, article.outlet_id)
                if key in seen_articles:
                    continue
                seen_articles.add(key)

                # 2. Check outlet name safely
                outlet_name = article.outlet.name if article.outlet else ""
                is_aggregator = any(agg in outlet_name for agg in AGGREGATORS)
                
                if is_aggregator:
                    aggregators.append(article)
                else:
                    originals.append(article)
                    # Check if original has substantial content
                    if article.content and len(article.content) > 500:
                        has_good_original = True
            
            if has_good_original:
                # If we have a scraped original, hide the aggregator versions
                story.display_articles = originals
            else:
                # Otherwise show everything (fallback to aggregator)
                # Combine originals + aggregators, preserving sort order?
                # Actually, we iterated sorted_articles, so just append them in order
                # Re-sorting might be safer or just reconstructing the list
                story.display_articles = originals + aggregators
                # Resort to be safe since we split them
                story.display_articles.sort(key=lambda x: x.date or datetime.min, reverse=True)

        return render_template(
            "articles.html",
            stories=stories,
            topics=TOPICS,
            active_label=active_label,
            page=page,
            total_pages=total_pages,
            show_single=show_single,
            is_multi_view=force_multi
        )

    @app.route("/fetch", methods=["POST"])
    def fetch_articles():
        mode          = request.form.get("mode", "top").strip()
        query         = request.form.get("query", "").strip() or None
        country       = request.form.get("country", "").strip() or None
        category      = request.form.get("category", "").strip() or None
        label         = request.form.get("label", "").strip() or None
        gnews_query   = request.form.get("gnews_query", "").strip() or None
        gnews_category = request.form.get("gnews_category", "").strip() or None

        try:
            from news_fetcher.fetch_and_store_articles import fetch_and_store_articles
            fetch_and_store_articles(
                topic_name=label or "Custom",
                mode=mode,
                query=query,
                country=country,
                category=category,
                gnews_query=gnews_query,
                gnews_category=gnews_category,
            )
        except Exception as e:
            print(f"Fetch error: {e}")

        if label:
            return redirect(url_for("list_articles", topic=label))
        return redirect(url_for("list_articles"))

    @app.route("/summarize/<int:story_id>", methods=["POST"])
    def summarize_story_route(story_id):
        story = Story.query.get_or_404(story_id)
        label = request.form.get("label", "")

        try:
            from news_fetcher.summarizer import summarize_story, check_ollama_status
            print(f"[Summarize] Story {story_id}: '{story.title}'")
            print(f"[Summarize] Articles: {len(story.articles)}")
            ollama_ok = check_ollama_status()
            print(f"[Summarize] Ollama reachable: {ollama_ok}")
            if not ollama_ok:
                print("Ollama is not reachable.")
            else:
                print("[Summarize] Calling summarize_story...")
                summary = summarize_story(story)
                print(f"[Summarize] Got summary: {bool(summary)}")
                if summary:
                    story.summary = summary
                    db.session.commit()
                    print("[Summarize] Saved!")
        except Exception as e:
            print(f"Summarization error: {e}")

        if label:
            return redirect(url_for("list_articles", topic=label))
        return redirect(url_for("list_articles"))

    @app.route("/rerank-outlet/<int:outlet_id>", methods=["POST"])
    def rerank_outlet(outlet_id):
        from .models import Outlet
        outlet = Outlet.query.get_or_404(outlet_id)
        label  = request.form.get("label", "")

        try:
            from news_fetcher.outlet_bias_llm import get_outlet_bias_from_llm
            bias_score = get_outlet_bias_from_llm(outlet.name)
            if bias_score is not None:
                outlet.bias_score = bias_score
                for article in outlet.articles:
                    article.bias_score = bias_score
                db.session.commit()
        except Exception as e:
            print(f"Re-rank error: {e}")

        if label:
            return redirect(url_for("list_articles", topic=label))
        return redirect(url_for("list_articles"))

    @app.route("/rate-article/<int:article_id>", methods=["POST"])
    def rate_article(article_id):
        article = Article.query.get_or_404(article_id)
        label   = request.form.get("label", "")

        try:
            from news_fetcher.outlet_bias_llm import get_article_bias_from_llm
            bias_score = get_article_bias_from_llm(article.title, article.content)
            if bias_score is not None:
                article.bias_score = bias_score
                db.session.commit()
        except Exception as e:
            print(f"Article rating error: {e}")

        if label:
            return redirect(url_for("list_articles", topic=label))
        return redirect(url_for("list_articles"))

    @app.route("/ollama-status")
    def ollama_status():
        ollama_host = os.environ.get("OLLAMA_HOST", "")
        try:
            response = requests.get(f"{ollama_host}/api/tags", timeout=5)
            online = response.status_code == 200
        except Exception:
            online = False
        return jsonify({"online": online})

    @app.route("/article/<int:article_id>")
    def view_article(article_id):
        article = Article.query.get_or_404(article_id)
        return render_template("article.html", article=article)

    @app.route("/ollama-catchup", methods=["POST"])
    def ollama_catchup_route():
        label = request.form.get("label", "")
        try:
            from news_fetcher.fetch_and_store_articles import ollama_catchup
            ollama_catchup()
        except Exception as e:
            print(f"Catchup error: {e}")
        return redirect(f"/articles?topic={label}" if label else "/articles")

    @app.route("/scrape-article/<int:article_id>", methods=["POST"])
    def scrape_article_route(article_id):
        article = Article.query.get_or_404(article_id)
        label = request.form.get("label", "")
        try:
            from news_fetcher.scraper import scrape_article
            content = scrape_article(article.url)
            if content:
                article.content = content
                db.session.commit()
                print(f"[Scrape] Successfully scraped article {article_id}")
            else:
                print(f"[Scrape] Could not scrape article {article_id}")
        except Exception as e:
            print(f"[Scrape] Error: {e}")
        if label:
            return redirect(url_for("list_articles", topic=label))
        return redirect(url_for("list_articles"))

    @app.route("/scrape-all-missing", methods=["POST"])
    def scrape_all_missing():
        label = request.form.get("label", "")
        try:
            from news_fetcher.scraper import scrape_article
            # Find articles with no content or very short content (API snippets)
            missing = Article.query.filter(
                (Article.content == None) |
                (Article.content == "") |
                (db.func.length(Article.content) < 500)
            ).limit(20).all()

            if missing:
                print(f"[Scrape] Attempting to scrape {len(missing)} articles...")
                scraped = 0
                for article in missing:
                    content = scrape_article(article.url)
                    if content:
                        article.content = content
                        scraped += 1
                        print(f"[Scrape] Scraped: {article.title[:60]}")
                db.session.commit()
                print(f"[Scrape] Scraped {scraped} of {len(missing)} articles.")
            else:
                print("[Scrape] No articles missing full text.")
        except Exception as e:
            print(f"[Scrape] Error: {e}")
        if label:
            return redirect(url_for("list_articles", topic=label))
        return redirect(url_for("list_articles"))

    @app.route("/rescrape-article/<int:article_id>", methods=["POST"])
    def rescrape_article_route(article_id):
        article = Article.query.get_or_404(article_id)
        label = request.form.get("label", "")
        try:
            from news_fetcher.scraper import scrape_article
            content = scrape_article(article.url)
            if content:
                article.content = content
                db.session.commit()
                print(f"[Scrape] Successfully scraped article {article_id}")
            else:
                print(f"[Scrape] Could not scrape article {article_id}")
        except Exception as e:
            print(f"[Scrape] Error: {e}")
        if label:
            return redirect(url_for("list_articles", topic=label))
        return redirect(url_for("list_articles"))

    @app.route("/force-regroup", methods=["POST"])
    def force_regroup():
        label = request.form.get("label", "")
        try:
            print("[Force Regroup] Starting...", flush=True)
            from news_fetcher.fetch_and_store_articles import force_regroup_all
            print("[Force Regroup] Imported successfully", flush=True)
            force_regroup_all()
            print("[Force Regroup] Done!", flush=True)
        except Exception as e:
            import traceback
            print(f"Force regroup error: {e}", flush=True)
            traceback.print_exc()
        return redirect(f"/articles?topic={label}" if label else "/articles")

    @app.route("/wake-ollama", methods=["POST"])
    def wake_ollama():
        label = request.form.get("label", "")
        try:
            import wakeonlan
            mac = os.environ.get("OLLAMA_MAC", "")
            if mac:
                wakeonlan.send_magic_packet(mac)
                print(f"[WoL] Magic packet sent to {mac}")
            else:
                print("[WoL] No MAC address configured")
        except Exception as e:
            print(f"[WoL] Error: {e}")
        return redirect(f"/articles?topic={label}" if label else "/articles")
    
    @app.route("/reclassify-articles", methods=["POST"])
    def reclassify_articles():
        label = request.form.get("label", "")
        try:
            print("[Reclassify] Starting...", flush=True)
            from news_fetcher.fetch_and_store_articles import reclassify_all_articles
            reclassify_all_articles()
            print("[Reclassify] Done!", flush=True)
        except Exception as e:
            import traceback
            print(f"Reclassify error: {e}", flush=True)
            traceback.print_exc()
        return redirect(f"/articles?topic={label}" if label else "/articles")
        
    return app


def create_db(app):
    with app.app_context():
        db.create_all()

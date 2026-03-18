# MuckScraper — All the Muck That's Fit to Scrape

### A Self-Hosted News Aggregator with LLM Analysis

> **TL;DR:** MuckScraper pulls news from multiple sources, groups articles about the same story together, scores every outlet for political bias, and gives you a Smart Brevity AI summary — all running on your own hardware with no subscriptions, no tracking, and no algorithm deciding what you see.

---

## Why This Is Different

Most news aggregators just show you a firehose of headlines. MuckScraper does three things no other self-hosted tool does:

**Cross-outlet story clustering** — Articles from CNN, Fox News, Reuters, and AP covering the same event are automatically grouped into a single story using LLM-assisted matching. See how different outlets cover the same story side by side.

**Political bias scoring** — Every outlet is scored on a 1–5 left-to-right spectrum by your local LLM the first time it appears. Individual articles can be rated separately. No hardcoded bias lists — your LLM makes the call based on its own knowledge.

**Smart Brevity summaries** — On-demand AI summaries follow the Axios Smart Brevity format: The big picture, Why it matters, What's happening, and What's next. Tight, structured, and actually readable.

---

## What It Does

MuckScraper pulls news from NewsAPI and GNews across configurable topic categories on a 3-hour schedule. Articles are scraped for full text, grouped into stories by a local LLM, scored for outlet-level political bias, and displayed in a clean web interface. Users can trigger on-demand fetches, generate AI summaries for any story, view full scraped article text, and rate individual articles for bias.

---

## Tech Stack

- **Backend:** Python, Flask, SQLAlchemy
- **Database:** PostgreSQL
- **News Data:** NewsAPI, GNews (adaptable to any provider)
- **LLM:** Any Ollama-compatible model, or adaptable to OpenAI/Anthropic APIs
- **Containerization:** Docker, Docker Compose
- **Scraping:** BeautifulSoup, Playwright, readability-lxml, archive.ph fallback

---

## ⚠️ Security Warning

MuckScraper has **no built-in authentication**. Any user who can reach the app on your network can trigger fetches, generate summaries, and read all stored articles.

**Do not expose MuckScraper directly to the internet.**

Recommended safe deployment options:
- Run on a local network only (default)
- Put it behind a VPN (e.g. WireGuard, Tailscale)
- Use a reverse proxy with authentication (e.g. Nginx + Authelia, Caddy + basic auth)

Authentication is planned for a future release.

---

## Current Features

### News Fetching
- 3-hour scheduled fetching across configurable topic categories (defaults: US Headlines, World Headlines, US Politics, Technology, Gaming)
- Smart restart detection — skips fetch on startup if last fetch was less than 3 hours ago
- On-demand fetch via the web interface — either by topic or custom search query
- Dual API sources: NewsAPI and GNews fetched simultaneously for each topic
- Duplicate article detection to avoid re-storing the same articles
- Source and title blocklist to filter out unwanted domains and content patterns

### Full Article Scraping
- BeautifulSoup with Mozilla readability algorithm for smart content extraction
- Playwright fallback for JavaScript-heavy sites
- Googlebot user agent fallback for soft-paywalled sites
- archive.ph fallback for paywalled content
- Per-article **[scrape]** button for articles missing full text
- Global **↻ Scrape Missing** button to bulk re-scrape up to 20 articles at a time
- Full article reader page showing scraped HTML content

### LLM Story Grouping
- Articles are clustered into stories using a two-step process:
  - Keyword pre-filter selects top candidate stories
  - Ollama decides if the article matches an existing story or needs a new one
- Looks back 7 days when matching articles to existing stories
- Works across all topics simultaneously
- Automatic re-grouping when Ollama comes back online after being offline

### Political Bias Scoring
- Outlet-level bias scoring via LLM on a 1–5 scale:
  - 1 = Left
  - 2 = Lean Left
  - 3 = Center
  - 4 = Lean Right
  - 5 = Right
- Scores assigned automatically when a new outlet is first seen
- Retry mechanism re-attempts scoring for outlets that couldn't be rated when LLM was offline
- Manual re-rank button to re-score any outlet on demand
- Per-article bias rating button to score individual articles separately from their outlet

### LLM Summarization
- On-demand AI summary generation using Smart Brevity format:
  - **The big picture** — one punchy sentence
  - **Why it matters** — 1-2 sentences on significance
  - **What's happening** — bullet points of key facts
  - **What's next** — forward-looking statement
- Summaries use full scraped article text, not just API snippets
- Re-summarize button to regenerate at any time
- Auto-summarization of unsummarized stories when Ollama comes back online

### Ollama Catchup
- Automatic catchup when scheduler detects Ollama came back online:
  - Re-groups single-article stories using LLM
  - Retries unrated outlets
  - Auto-summarizes unsummarized stories (capped at 10 per run)
- Manual **↻ Ollama Catchup** button in the sidebar

### Web Interface
- Sticky sidebar with topic links and per-topic refresh buttons
- Dark/light mode toggle — preference saved in localStorage
- Color-coded bias tags on every article (blue = left, red = right, grey = center)
- Separate "Outlet" and "Article" bias labels when an article has been individually rated
- Smart Brevity AI summary with labeled sections and bullet points
- Ollama online/offline status indicator
- Custom topic search bar

### Infrastructure
- Raw API payload storage with 30-day auto-cleanup
- Database indexes on key columns for query performance
- `restart.sh` — soft rebuild that preserves the database

---

## Project Structure

```
muckscraper/
├── aggregator/
│   ├── __init__.py          # Flask app factory, all routes
│   ├── app.py               # App entry point
│   ├── models.py            # Database models
│   └── templates/
│       ├── articles.html    # Main UI template
│       └── article.html     # Full article reader
├── news_fetcher/
│   ├── fetch_and_store_articles.py   # Core ingestion logic
│   ├── scheduler.py                  # 3-hour fetch scheduler
│   ├── scraper.py                    # Article scraping (BS4, Playwright, archive.ph)
│   ├── story_grouper.py              # LLM story clustering
│   ├── summarizer.py                 # LLM summarization
│   └── outlet_bias_llm.py           # LLM bias scoring
├── docker-compose.yml
├── Dockerfile
├── news_fetcher/Dockerfile
├── requirements.txt
├── restart.sh
└── README.md
```

---

## Requirements

- Docker and Docker Compose
- NewsAPI key — [newsapi.org](https://newsapi.org) (free tier: 100 requests/day)
- GNews key — [gnews.io](https://gnews.io) (free tier: 100 requests/day)
- Ollama running on your network with a compatible model

---

## Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/grregis/muckscraper.git
   cd muckscraper
   ```

2. Copy the environment template and fill in your values:
   ```bash
   cp .env.sample .env
   ```

3. Generate a secret key:
   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

4. Start the containers:
   ```bash
   docker compose up --build
   ```

5. Open the app at `http://localhost:5000`

---

## Customization

### Topics / Categories
Edit the `TOPICS` list in `aggregator/__init__.py` to add, remove, or change the topics that appear in the sidebar.

### News API Provider
The fetching logic lives in `news_fetcher/fetch_and_store_articles.py`. Swap out the API client for any provider that returns article titles, URLs, content snippets, and source names.

### LLM Provider
LLM calls are isolated in:
- `news_fetcher/outlet_bias_llm.py` — bias scoring
- `news_fetcher/summarizer.py` — summarization
- `news_fetcher/story_grouper.py` — story clustering

All use simple HTTP POST to an Ollama-compatible endpoint. Replace with OpenAI, Anthropic, Groq, etc. by swapping the API call.

### Blocked Sources
Add domains or title keywords to `BLOCKED_SOURCES` and `BLOCKED_TITLE_KEYWORDS` in `news_fetcher/fetch_and_store_articles.py`.

### Scraping Strategies
Configure which domains use Playwright, Googlebot UA, or get skipped entirely via the lists at the top of `news_fetcher/scraper.py`.

---

## Roadmap

### Planned Features

#### Headlines Feature
- Dedicated `/headlines` page ranking top stories by cross-outlet coverage
- LLM story clustering using vector similarity (pgvector)
- Visual bias spectrum per story showing which outlets from left to right covered it

#### Paywall Bypass
- RSS feed extraction for outlets that publish full text in RSS
- Per-outlet cookie injection for subscribed sites

#### Admin Interface
- Manual bias score overrides for outlets
- Outlet management page (view, edit, delete)
- Article management (bulk delete, manage blocked sources via UI)

#### Better Filtering & Sorting
- Filter articles by bias score
- Sort stories by coverage frequency
- Date range filtering
- Pagination

#### Infrastructure
- User authentication (Flask-Login)
- Replace `print()` with Python `logging` throughout
- pgvector embeddings for semantic story clustering
- Replace Docker `sleep` with proper `wait-for-db`

---

## Known Limitations

- Development server only — not production hardened
- No authentication — see Security Warning above
- NYT, Washington Post, and hard-paywalled sites are difficult to fully scrape
- Story clustering quality depends on Ollama model and availability
- No pagination beyond 50 stories per topic view

---

## License

MIT License — see `LICENSE` for details.

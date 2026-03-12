# MuckScraper — All the Muck That's Fit to Scrape

### A Self-Hosted News Aggregator with LLM Analysis

MuckScraper is a self-hosted news aggregation and analysis tool that collects articles from multiple sources, scores them for political bias, and uses a local or remote LLM to generate detailed summaries. It is designed to give you a clear, unfiltered view of how different news outlets cover the same stories.

---

## What It Does

MuckScraper pulls news from a news API of your choice across multiple topic categories on an hourly schedule. Articles are grouped into stories, scored for outlet-level political bias using an LLM, and displayed in a clean web interface. Users can trigger on-demand fetches, generate AI summaries for any story, and rate individual articles for bias — all configurable to work with whatever news source and LLM you prefer.

---

## Tech Stack

- **Backend:** Python, Flask, SQLAlchemy
- **Database:** PostgreSQL
- **News Data:** Any compatible news API (e.g. NewsAPI, GNews, Mediastack)
- **LLM:** Any Ollama-compatible model, or adaptable to OpenAI/Anthropic APIs
- **Containerization:** Docker, Docker Compose
- **Scraping:** BeautifulSoup, Requests

---

## Current Features

### News Fetching
- Hourly scheduled fetching across configurable topic categories (defaults: US Headlines, World Headlines, US Politics, Technology, Gaming)
- On-demand fetch via the web interface — either by topic or custom search query
- Duplicate article detection to avoid re-storing the same articles
- Source blocklist to filter out unwanted domains and title patterns (e.g. package release announcements, repository updates)

### Political Bias Scoring
- Outlet-level bias scoring via LLM on a 1–5 scale:
  - 1 = Left
  - 2 = Lean Left
  - 3 = Center
  - 4 = Lean Right
  - 5 = Right
- Scores are assigned automatically when a new outlet is first seen
- Retry mechanism re-attempts scoring for outlets that couldn't be rated (e.g. when the LLM was offline)
- Manual re-rank button to re-score any outlet on demand
- Per-article bias rating button to score individual articles separately from their outlet

### LLM Summarization
- On-demand AI summary generation for any story
- Summaries are based on article titles and content snippets
- Re-summarize button to regenerate summaries at any time
- LLM online/offline status indicator in the sidebar

### Web Interface
- Sidebar navigation with topic links and per-topic refresh buttons
- Color-coded bias tags on every article (blue = left, red = right, grey = center)
- Separate "Outlet" and "Article" bias labels when an article has been individually rated
- AI Summary displayed prominently on each story card
- Custom topic search bar

---

## Project Structure

```
muckscraper/
├── aggregator/
│   ├── __init__.py          # Flask app factory, routes
│   ├── app.py               # App entry point
│   ├── models.py            # Database models
│   └── templates/
│       └── articles.html    # Main UI template
├── news_fetcher/
│   ├── fetch_and_store_articles.py   # Article fetching logic
│   ├── scheduler.py                  # Hourly fetch scheduler
│   ├── summarizer.py                 # LLM summarization
│   └── outlet_bias_llm.py           # LLM bias scoring
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## Customization

MuckScraper is designed to be flexible. Here are the main things you can tailor:

### Topics / Categories
Edit the `TOPICS` list in `aggregator/__init__.py` to add, remove, or change the topics that appear in the sidebar and are fetched on the hourly schedule.

### News API Provider
The fetching logic lives in `news_fetcher/fetch_and_store_articles.py`. Swap out the news API client for any provider that returns article titles, URLs, content snippets, and source names.

### LLM Provider
The LLM calls are isolated in two files:
- `news_fetcher/outlet_bias_llm.py` — bias scoring
- `news_fetcher/summarizer.py` — summarization

Both files use a simple HTTP POST to an Ollama-compatible endpoint. To use a different provider, replace the API call in those files with your preferred client (OpenAI, Anthropic, Groq, etc.).

### Blocked Sources
Add domains or title keywords to the `BLOCKED_SOURCES` and `BLOCKED_TITLE_KEYWORDS` lists in `news_fetcher/fetch_and_store_articles.py` to filter out unwanted content.

---

## Roadmap

### In Progress / Near Term
- [ ] Fix source blocklist to catch articles from news aggregator sites that slip past URL filtering
- [ ] `.env` file integration to avoid hardcoded credentials in `docker-compose.yml`

### Planned Features

#### Better Article Filtering & Sorting
- Filter articles by bias score (e.g. show only left-leaning or right-leaning sources)
- Sort stories by coverage frequency (how many outlets covered the same story)
- Date range filtering

#### Full Article Scraping
- Use Scrapy or Playwright to fetch full article text beyond API snippets
- Store full text in the database for richer LLM summaries and analysis

#### Improved Story Clustering
- Replace the current headline-splitting approach with smarter topic similarity grouping
- Use LLM embeddings or keyword clustering to group articles about the same event more accurately

#### LLM Analysis Features
- Cross-outlet analysis: identify how different outlets frame the same story
- Trend detection: surface topics gaining coverage momentum
- Periodic outlet re-scoring to keep bias ratings fresh

#### Admin Interface
- Manual bias score overrides for outlets you disagree with
- Outlet management page (view, edit, delete outlets)
- Article management (bulk delete old articles, manage blocked sources via UI)

#### Coverage Frequency & Outlet Rankings
- Display how many outlets covered each story
- Rank outlets by coverage volume per topic
- Visual bias spectrum bar showing the spread of coverage across the political spectrum

#### UI Improvements
- Responsive mobile layout
- Dark/light mode toggle
- Pagination for large article sets

---

## License

MIT License — see `LICENSE` for details.

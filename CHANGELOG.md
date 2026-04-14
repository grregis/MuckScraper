## [0.3.0] - 2026-04-14

### Added

- **Newspaper-style headlines front page** (`/headlines`) — Chomsky masthead, 3-column broadsheet layout, grayscale newsprint images, weather widget, dark mode, pagination. Accessible without authentication.
- **Story detail page** (`/story/<id>`) — multi-source deep report display, source article list with bias tags, auto-generates summary and deep report on first visit if Ollama is online
- **Deep reports** — topic-aware in-depth analytical reports with six distinct analytical frames:
  - Politics: left/center/right framing comparison
  - Science/Technology: findings, impact, expert commentary
  - Gaming: coverage analysis, community reaction, industry impact
  - Sports: recap, key performances, standings context
  - Business/Finance: market impact, analyst commentary, economic context
  - Default: generic multi-source analysis
- **Headline ranking pipeline** — two-stage scoring system:
  - `headline_ranker.py`: polls 16 RSS feeds across the political spectrum, uses LLM to match external headlines to MuckScraper stories, calculates weighted base score (67% outlet coverage, 33% article count) with time decay
  - `editorial_ranker.py`: LLM re-ranks top 50 candidates by real-world importance, blends with base score (60/40 split)
- **`EditorialHistory` model** — logs every ranking run per story for auditability
- **Image capture and display** — `image_url` added to Article model, populated from NewsAPI (`urlToImage`) and GNews (`image`). Images shown in headlines front page and articles feed
- **`backfill_images.py`** — utility to backfill image URLs from stored raw API payloads
- **`process_headline_stories()`** — post-fetch pipeline step ensuring top 20 headline stories have summaries, deep reports, and per-article summaries
- **Langfuse observability** — all LLM calls instrumented with tracing across summarizer, story grouper, headline generator, outlet bias, topic classifier
- **Authentication** — Flask-Login based auth with login page, admin user creation script (`create_admin.py`), and protected admin routes
- **Blueprint architecture** — routes split into three blueprints:
  - `personal.py` — public-facing read-only routes: `/headlines`, `/story/<id>`, `/article/<id>`
  - `admin.py` — all write and trigger routes requiring authentication
  - `public.py` — shared route base
- **`filters.py`** — Jinja2 template filters extracted from app factory and registered via `register_filters(app)`
- **`constants.py`** — shared `TOPICS` and `AGGREGATORS` constants extracted from app factory
- **Scrape blocklist** — automatic detection and blocking of bad scrapes:
  - Strong indicators: login walls, captchas, bot detection, subscriber gates
  - Weak indicators: short content with sign-in/subscribe text
  - Duplicate detection: content near-identical to 2+ other articles from same outlet flagged as login/error page
  - Pre-populated permanent blocklist of hard-paywalled domains (NYT, WSJ, FT, Bloomberg, etc.)
  - `audit_existing_scrapes()` — retroactive scan of all stored content
  - `/scrape-blocklist` admin page — view blocked domains, unblock auto-blocked entries, trigger audit
- **`get_headline_stories()`** — shared query function used by both the headlines route and `process_headline_stories()`, eliminating duplicated logic
- **New Alembic migrations**: `headline_score` on stories, `image_url` on articles, `editorial_history` table, `scrape_blocklist` table
- **Gunicorn** — replaces Flask development server in production

### Changed

- Scheduler fetch times changed to 4 daily runs at 12am, 7am, 12pm, 6pm Eastern
- Scheduler categories restructured: Top News, World News, US Politics, Business & Economy, Science & Health, Technology, National Security & Foreign Policy
- `regroup_ungrouped_stories()` replaced O(n²) Python cosine loop with pgvector `<=>` nearest-neighbour SQL query
- Force Re-group button now requires confirmation before executing
- `create_app()` reduced to wiring only — db init, filter registration, blueprint registration
- OLLAMA_MAC environment variable now validated against MAC address format before use

### Fixed

- Duplicate `summarize_article` definition in `summarizer.py` — dead first definition removed
- `cleanup_duplicates.py` no longer imports from `fetch_and_store_articles`, avoiding double app instantiation
- Alembic migration branch — `add_editorial_history.py` had incorrect `down_revision` creating a two-headed migration chain
- Duplicate CSS block in `article.html`

---

## [0.2.2] - 2026-03-21

### Added
- **Collapsible sidebar** — toggle to icon-only mode, state saved in localStorage
- **Grouped Stories view** — dedicated `/multi-stories` page showing only stories with 2+ articles, paginated at 50
- **All Stories view** — explicit link in sidebar to view unfiltered stories
- **Sticky header** with hamburger menu (☰) — maintenance buttons moved from sidebar into a cleaner dropdown
- **Aggregator deduplication** — Yahoo News, Google News, MSN, AOL articles hidden per story when original source content exists
- **Local timezone conversion** — article dates displayed in user's local timezone via JavaScript
- **Published and fetched timestamps** — both the original publish date and MuckScraper fetch date shown per article
- **`fetched_at` column** added to Article model
- **Single linkage story matching** — new articles now compared against every article in a story for best similarity match, not just the first
- **Story ordering** by most recent article date instead of story creation date
- **`cleanup_duplicates.py`** — maintenance script for deduplicating articles (work in progress)

### Changed
- Maintenance buttons moved from sidebar footer to hamburger menu in header
- Sidebar now shows "All Stories" and "Grouped Stories" navigation links
- Story display uses `display_articles` filtered list to hide aggregator duplicates

### Fixed
- Story ordering now reflects latest news rather than when the story was first created

---

## [0.2.1] - 2026-03-20

### Added
- AI-generated wire service style headlines for multi-article stories
- Single-article story filter toggle — hide/show stories with only one article
- `headline_generator.py` — new module for story headline generation
- Headlines generated automatically when second article added to a story
- Headlines generated during Ollama catchup for existing multi-article stories

### Changed
- Replaced all `print()` statements with proper Python `logging` module across all news_fetcher files
- Story display now shows AI headline when available, falls back to auto-generated title

---

## [0.2.0] - 2026-03-19

### Added
- **pgvector story clustering** — replaced Ollama prompt-based grouping with vector embeddings using `nomic-embed-text`
- **LLM topic classifier** — articles classified into topics by Ollama based on content
- **Pagination** — 25 stories per page with prev/next navigation
- **Force Re-group button** — rebuilds all story groupings from scratch using vector similarity
- **Reclassify Topics button** — reclassifies all existing articles into the new topic system
- **Wake Ollama button** — sends Wake on LAN magic packet to Ollama machine
- **Per-article [scrape] button** — appears on articles missing full text
- **Global ↻ Scrape Missing button** — bulk re-scrapes up to 20 articles missing full text
- `python-readability` for smarter article content extraction
- Googlebot user agent fallback for soft-paywalled sites
- archive.ph fallback when all other scraping strategies fail
- DB indexes on articles and stories tables for faster queries
- Raw API payload storage with 30-day auto-cleanup
- `restart.sh` script for soft rebuilds that preserve the database
- Screenshots added to README

### Changed
- Topics redesigned — now 7 categories classified by LLM content analysis rather than API fetch category
- Scheduler fetch configurations updated to better target relevant content
- TOPICS list in `__init__.py` simplified — fetch config moved entirely to scheduler

### Fixed
- Ollama catchup button breaking article links and summarization
- Re-grouping creating new stories instead of only matching existing ones
- Auto-summarization capped to 10 stories per batch to prevent timeouts
- HTML tags being sent to Ollama in summaries
- Content snippet size increased from 500 to 1500 chars per article
- Force regroup foreign key violation on story_topics table
- numpy array boolean evaluation error in story grouper

---

## [0.1.3] - 2026-03-17

### Added
- `python-readability` for smarter article content extraction
- Googlebot user agent fallback for soft-paywalled sites
- archive.ph fallback when all other scraping strategies fail
- Per-article `[scrape]` button for articles missing full text
- Global ↻ Scrape Missing sidebar button to bulk re-scrape up to 20 articles at a time
- DB indexes on key columns
- Raw API payload storage with 30-day auto-cleanup
- `restart.sh` script for soft rebuilds that preserve the database

### Fixed
- Ollama catchup button breaking article links and summarization
- Re-grouping creating new stories instead of only matching existing ones
- Auto-summarization capped to 10 stories per batch to prevent timeouts
- HTML tags being sent to Ollama in summaries
- Content snippet size increased from 500 to 1500 chars per article

---

## [0.1.2] - 2026-03-13

### Added
- Full article scraping with BeautifulSoup and Playwright fallback
- Sanitized HTML storage for scraped articles
- Article reader page at `/article/<id>`
- LLM story grouping using keyword pre-filter and Ollama match decision
- Smart Brevity summary format with labeled sections and bullet points
- Dark/light mode toggle with localStorage persistence
- Sticky sidebar with purple accent and drop shadow
- Ollama Catchup button
- Automatic Ollama catchup when scheduler detects Ollama came back online
- Smart restart timer
- `AppSetting` model for persisting state across container restarts
- Many-to-many topic tagging for articles and stories
- GNews as a second news source alongside NewsAPI
- `destroy.sh` and `restart.sh` maintenance scripts
- `.env` support for all credentials and configuration

### Fixed
- Race condition causing duplicate topic creation
- Scheduler running stale cached code after restarts
- `POSTGRES_USER` typo in `docker-compose.yml`
- Removed standalone `news_fetcher` container — scheduler handles all fetching

---

## [0.1.0] - 2026-03-10

### Added
- Initial release
- Flask + PostgreSQL + Docker Compose setup
- NewsAPI integration with scheduled fetching every 3 hours
- Outlet-level political bias scoring via Ollama (1=Left to 5=Right)
- On-demand AI story summarization via Ollama
- Source blocklist for filtering unwanted domains and title patterns
- Ollama online/offline status indicator
- Per-article and per-outlet bias rating buttons
- MIT License
- README documentation
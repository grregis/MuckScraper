# Changelog
 
All notable changes to MuckScraper are documented here.
 
---

## [Unreleased]

### Added
- **Optional OpenID Connect sign-in** (#28, thanks @rinseaid). Sign in through
  your own identity provider (Keycloak, Authentik, Pocket ID and others) with
  the existing accounts and Reader / Scraper / Admin roles. Off by default
  (`OIDC_ENABLED`); password login stays unless you turn it off. Existing
  accounts are linked explicitly with `flask link-oidc-identity`, never by a
  matching email. Needs HTTPS for both the provider and the app. Setup in
  `docs/OIDC.md`. The migration only adds an `oidc_identities` table.

### Changed
- **Article summaries and analyses read up to 24,000 characters of the
  article** (was the first 3,000 and 3,500). Long pieces are summarised from
  the whole text instead of their opening, and the analysis no longer says a
  piece lacks evidence that sits further down. Costs about two seconds per
  long article. Needs a model context of at least ~8k tokens: raise
  `OLLAMA_CONTEXT_LENGTH` on the Ollama server, or set
  `ARTICLE_CONTENT_LIMIT=3000` in `.env` to keep the old length.
- **Story summaries and deep analyses read more of each article.** The story
  summary takes up to 20,000 characters across its articles (was 9,000) and
  3,000 per article (was 1,500); the deep analysis up to 24,000 (was 13,500)
  and 3,000 per article, plus, for each article longer than its excerpt, that
  article's own analysis (or summary), up to 16,000 more. Article summaries
  and analyses are now written before the story's, so they are there to use.
  The biggest prompts are under 10k tokens; with Ollama's old 4k default
  context, raise `OLLAMA_CONTEXT_LENGTH` (16k is enough).

## [1.0.2] - 2026-10-26

### Added
- **Article bylines** (`articles.author`). Taken from the feed or NewsAPI
  entry, or from the publisher's page when the scraper reads it, and cleaned
  ("By Jane Doe, CNN" becomes "Jane Doe"; "Staff" and outlet names are left
  blank). Not shown on pages yet. Upgrading adds one empty column
  (`./install.sh` applies the migration); articles stored before it have no
  byline.

### Fixed
- **The scraper's page-reading fallbacks run again.** Since 0.5 the first
  page it downloaded was thrown away, so three fallbacks never ran: finding
  the AMP version of a page from its link, reading the article from the
  page's structured metadata, and (new here) the byline from the page.
  Sites without a usable feed author or a clean readable page get more of
  each now.

## [1.0.1] - 2026-10-22

### Added
- **`APP_BIND` and `APP_PORT`** in `.env` choose where the admin app is
  published (default `127.0.0.1:5000`, unchanged). Set `APP_BIND=0.0.0.0` to
  reach it from other machines on your network, the same way `MCP_BIND`
  works for the MCP server; no override file needed. Suggested on Reddit by
  u/geo38.

### Changed
- **Meilisearch telemetry is off** (`MEILI_NO_ANALYTICS`). It sent anonymous
  usage data to Meilisearch by default. Spotted by u/geo38. Takes effect when
  the container is recreated (`./install.sh`, or
  `docker compose up -d meilisearch`).
- **CNN Underscored pages are refused at ingestion** (`/cnn-underscored/`):
  they are shopping reviews with affiliate links, not news. They also answer
  403, and one of those was enough to put all of cnn.com on a six-hour
  scrape hold.

### Removed
- **The scraper no longer poses as Googlebot or opens paywalled sites in a
  headless browser.** Seven sites were fetched with Google's crawler user
  agent, and seven paywalled ones (NYT, WSJ, Washington Post, FT, Bloomberg,
  The Athletic, Wired) got a headless Chromium attempt. MuckScraper now reads
  only what an ordinary browser can, and a paywalled article stays
  headline-only. In practice little is lost: over 30 days the crawler user
  agent fetched 81 articles and the headless browser none.

## [1.0.0] - 2026-10-20

Status: stable. MuckScraper is out of beta. From here on, upgrades within
1.x never break your stored config or customizations (topics, RSS feeds,
prompts, scheduled fetches, ingestion blocks); anything that would is saved
for 2.0 and called out there. Since 0.8 this release adds **opinion
coverage** (an Op/Ed section with columns linked to the stories they argue
about, each with its stance, and a roundup of what each side says),
**copied-article detection** so one wire report under three mastheads counts
once, **deep analysis** for every edition article, **past editions**, and
**Google News search feeds** for the big outlets whose own feeds died. It
also makes a fresh install simpler (both secret keys generated on the first
run) and safer (a placeholder `SECRET_KEY` is refused), and moves to current
urllib3 and requests.

### Added
- **`OLLAMA_THINK`** (optional, blank by default). Set it to `false` to turn
  off a thinking model's hidden reasoning (gemma4, qwen3, deepseek-r1), which
  is most of the time those models spend per call. Blank keeps Ollama's
  default, so existing installs are unchanged.
- **Read-only MCP server** (`mcp_server/`, docs in `docs/MCP.md`), off by
  default behind the `mcp` Compose profile. Gives an AI assistant health and
  troubleshooting checks: run status and history, last run, filtered scheduler
  logs, Ollama state (without waking the box), outage signs, edition lineups,
  quality report, story/article lookup and search, scrape and bias coverage,
  containers, search index, whether running code is current, and a runbook
  lookup over the docs. The database connection refuses writes, the checkout
  is mounted read-only, and HTTP access needs a bearer token (`MCP_TOKEN`).
- `docker-restart-proxy` lists each container's start time and restart count,
  and has a bounded, read-only log route (`GET /containers/<name>/logs`).
- **Op/Ed.** Opinion pieces are still kept out of the news, but they are now
  saved to their own table instead of being dropped, and listed on a new Op/Ed
  page (sidebar, below the topics) with a time window, a Left / Center / Right
  filter and search. A new `op_ed` RSS bucket stores every entry of an
  opinion feed straight to Op/Ed; ten default feeds, balanced across the
  spectrum. On full runs, each new column is linked to the news story it
  argues about (embedding match plus a yes/no check by the fast model), and a
  story with two or more linked columns gets a short opinion roundup saying
  what each side argues. Letters to the editor are never linked.
- **Copied articles are marked.** An article that shares at least half its
  text with another outlet's article in the same story (a syndicated wire
  story, or one outlet running another's piece) is labelled "Same report as
  ..." on the story page, and is not counted as a separate source in story
  selection, headlines, summaries or reports.
- **Deep analysis for every edition article** with at least 500 characters of
  text, shown in a Deep Analysis box on the article page. Sports reports first
  decide whether the story is about a game, and stop assuming one.
- **Quality check for invented titles:** the quality report flags summaries
  and reports that call someone "former President ..." (or another former
  office) when no source does.
- **Past editions.** A Past editions page lists every edition, grouped by
  month, and opens each one as it was published, read live from the
  database, so opinion columns added to its stories since are there too.
- **A page for each Op/Ed piece:** what it argues, the story it's linked
  to, its text (signed-in readers only) and a link to the publisher. The
  Op/Ed list and the story page's columns link there.
- **Opinion page per edition:** the stories that got new opinion columns in
  that edition, each column with a one-line stance. Every linked column now
  gets a stance, not only the ones a roundup described.
- **Outlets without a working RSS feed** (CNN, AP, Reuters, WSJ) come in
  through Google News search feeds. Each link is resolved to the publisher's
  own URL before anything is stored; an entry that can't be resolved is
  dropped and retried next run.
- **Feed fallback URL:** any RSS feed can have a second URL, fetched instead
  when the feed answers an HTTP error or can't be parsed (set it on the RSS
  Feeds page).
- **Signed-out article pages** say in plain words how much of the article
  could be read ("Fully read", "Publisher blocked full-text access", ...), and
  why there is no summary when the text couldn't be read.

### Changed
- **Ingestion refuses more junk:** fantasy-sports advice, "odds, predictions"
  betting picks, Seeking Alpha contributor pieces, editorial cartoons (as
  news and as Op/Ed), show and interview transcripts, and "News of the day"
  digests. News about cartoons or transcripts is kept. HTML tags embedded in
  feed titles are stripped.
- **Editions prefer stories covered by two or more outlets**, not two
  articles: one outlet's pair (a transcript plus its write-up) no longer
  jumps ahead of single-source stories.
- **"former President" is fixed in code.** Summaries, reports, headlines and
  opinion roundups no longer call the current President or Vice President
  "former" unless a source does. Prompt rules alone never stopped the model
  doing it.
- **Back to Feed returns to where you were.** Going back from a story or
  article lands on the same card in the list, or the same row in Search,
  instead of the top of the page.
- On article pages, scrape details sit above the summary for signed-in readers.
- The dark top bar spans the full page width on All Stories, Grouped Stories
  and Search.
- **Layout and navigation pass:** every page has its back link in the same
  place ("Back to Feed" returns to the list you came from, config pages go
  "Back to Admin Tools"); the accent colour is the sidebar's indigo (blue is
  left for Left); every page has the Light/Dark button and follows the saved
  theme; the Headlines and Fetch top bars are sticky; and no page is wider
  than a phone screen, upright or sideways.
- The browser tab icon is the raccoon mascot.
- The quality checker accepts either label set when a prompt offers two
  formats.
- `seed_defaults()` seeds prompts as the migrations leave them, so a fresh
  install starts with the current prompt text.
- Article summaries are shown as one paragraph, without the section labels.
- Phones: the top bar keeps the title, theme button and account control on
  one line.
- **Default feeds:** Google News searches replace the dead CNN, AP, Reuters,
  WSJ and The Economist feeds; more right-leaning news feeds; and ten Op/Ed
  feeds balanced left and right (the `op_ed` bucket had none by default).
  Existing installs get the new rows added and nothing else changed: the dead
  feeds stay as they are, so disable them on the RSS Feeds page.
- **Install:** `.env.sample` leaves the optional news API keys blank (no more
  "still unedited" prompt for them) and suggests a starting model;
  `install.sh` creates `.env` readable by its owner only; the optional
  Langfuse, n8n and Wake-on-LAN settings no longer print Compose warnings
  when unset; a first start no longer prints a database traceback while
  Postgres is starting. The first run of `install.sh` now generates
  `SECRET_KEY` and `MEILI_MASTER_KEY` when it creates `.env`, so you only
  fill in the Ollama settings and the admin password. On an upgrade it
  rebuilds the scheduler image too (before, the scheduler kept running the
  old code until it was rebuilt by hand).
- **The restart proxy finds its own Compose project**, so the Admin Tools
  restart buttons work in a clone with any folder name (it assumed
  `muckscraper`). `COMPOSE_PROJECT_NAME` in its environment overrides it.
- **Outlet names:** Politico, Reason, Newsmax and The Daily Wire now match
  their AllSides entries.

### Fixed
- **A wedged Ollama no longer looks asleep.** When the port accepts a
  connection but nothing answers, the health probe now treats the box as
  broken and the run fails fast, instead of waiting out hundreds of timeouts.
- **Edition dedupe:** two stories sharing only country and leader names
  ("Russia", "Trump", "Ukraine") are no longer treated as the same event; one
  shared word has to be something else. A six-outlet Ukraine story had been
  dropped from two editions.
- **Headlines ending in a quotation** keep their closing quote mark.

### Removed
- `restart.sh` (use `./install.sh`, the Admin Tools restart buttons, or
  `docker compose up -d <service>`), `create_admin.py` (`install.sh` creates
  the admin; add more on the Users page) and `catcode.sh` (an old code-dump
  helper).

### Security
- **The app refuses to start with the `.env.sample` placeholder as its
  `SECRET_KEY`.** It used to accept it, and since that value is public, anyone
  could forge a login session on an install that kept it (only possible by
  editing `.env` by hand; `install.sh` always replaced it). `.env.sample` now
  shows the command to generate a key.
- **urllib3 1.26.20 -> 2.8.0** (decompression-bomb and redirect advisories,
  relevant because the pipeline fetches pages from sites it doesn't control)
  and **requests 2.31.0 -> 2.34.2**. The old `urllib3<2.0.0` pin is gone.

### Database
- Seven migrations: `f2a6c9e4b7d1` (deep-analysis prompts), `a7d3e9f1c2b4`
  (Op/Ed table), `b3f7c1d9e5a2` (Op/Ed links and roundups), `c5d1e8a3f7b9`
  (copied-article columns), `d8e2a4f6b1c3` (opinion stance prompt),
  `e4b7c2a9d6f1` (feed fallback URL), `f7a1d3c5e8b2` (new default feeds,
  added only where missing). All are additive.

### Upgrade Notes

- Wait for a run to finish, then pull and run the installer:

  ```bash
  git pull
  ./install.sh
  ```

  It rebuilds the images (the scheduler and the restart proxy included),
  applies the seven migrations and recreates the containers. By hand:
  `docker compose up -d --build`, then
  `docker compose exec app flask db upgrade`.
- **If `.env` still has the sample `SECRET_KEY`**, the app now refuses to
  start. Generate a key with
  `python3 -c "import secrets; print(secrets.token_hex(32))"`, put it in
  `.env`, and run `docker compose up -d app scheduler`. Everyone has to sign
  in again afterwards.
- **New default feeds are added; your own feeds are left alone.** So the
  old defaults the new ones replace stay enabled: on the RSS Feeds page,
  disable the CNN, AP, Reuters and WSJ feeds (dead), The Economist's "world
  this week" feed (weekly round-ups only) and National Review in `general`
  and `right_enrichment` (its columns were landing in the news; it now comes
  in through Op/Ed). Their replacements are already in.
- **New optional settings:** `OLLAMA_THINK` (blank keeps Ollama's default;
  `false` turns off a thinking model's hidden reasoning) and `MCP_TOKEN` for
  the optional MCP server
  (`docker compose --profile mcp up -d --build mcp docker-restart-proxy`).

## [0.8.0] - 2026-10-05

Status: beta. This release covers three areas. The first is **a reading and
search interface you can actually work in**: a new Search page, time windows
on every list, local-time timestamps, and a full accessibility and layout pass
over the admin app. The second is **accounts**: Reader, Scraper and Admin
roles, user management, a profile page, and an optional public read-only
mode. The third is **making the pipeline harder to fool**: it
checks that the LLM can really run inference before a run uses it, refuses
broken generated text instead of publishing it, and stops a single bad scrape
from blocking a domain for months. It also fixes a fresh install, which used
to start and then do nothing.

### Added

- **Three account roles.**
  - **Reader:** reads everything, including full article text and Search, and
    changes nothing.
  - **Scraper:** also fetches articles and runs summaries, analysis, bias
    ratings and scrapes.
  - **Admin:** also uses Admin Tools, changes all configuration, and manages
    users.

  Pages hide the buttons a role can't use, and routes refuse the actions
  (403).
- **User management** (`/admin/users`, admins only). Add accounts, change
  their email, role and active status, set a new password, or delete them. You
  can't change your own role, disable yourself or delete yourself, and the app
  refuses anything that would leave no active admin. A disabled account can't
  sign in and loses any session it already had.
- **A profile page** (`/auth/profile`). It shows your username, role, sign-up
  date and last sign-in, and lets you change your email or password (both ask
  for your current password).
- **An account menu, top right.** Signed out, it's a Sign in button. Signed
  in, a round person button opens Profile, Users and Admin Tools (admins only)
  and Sign out. It replaces the Admin Tools header button, and Sign out is now
  a POST.
- **`PUBLIC_READ_ACCESS`** (`.env`, default `false`). When on, signed-out
  visitors can read Headlines, Grouped Stories, All Stories, the topic filters,
  story pages and article summaries. They never get scraped article text,
  scrape details, Search, the Fetch page or any action button. An article page
  shows them the summary and a link to the publisher.
- **A Search page** (`/admin/search`, in the sidebar). Stories and Articles
  tabs share the query, time window and sort. Results are dense, sortable
  tables, 50 rows a page: Left / Center / Right counts and sources per story,
  and an L / LL / C / LR / R bias chip per article with the exact score on
  hover. Custom date ranges are supported. With no query, results are newest
  first; with a query, best match first.
- **Time windows on the story lists.** All Stories and Grouped Stories default
  to the last 7 days, with 24 hours, 30 days and All time options. A story is
  shown if any of its articles falls in the window, and it is shown with all
  of its articles.
- **`DISPLAY_TIMEZONE`** (`.env`, IANA name, default `UTC`). The time zone all
  aggregator dates are shown in. Timestamps now read "3 hours ago", with the
  exact local time on hover.
- **Better pagination.** First and Last buttons, and a page-number box for
  jumping straight to a page.
- **Default config on a fresh install.** Topics, RSS feeds, prompts,
  scheduled fetches, the pipeline schedule, ingestion blocks and the scrape
  blocklist are now seeded by `bootstrap_admin.py` (`aggregator/seed_defaults.py`).
  Before this, a fresh install started, logged in, and then did nothing: no
  scheduled runs, nothing fetched, and no prompts to generate with. Seeding
  only inserts what is missing, so it never overwrites anything you've edited.
- **Search stays current by itself.** Each run, fetch-only or full, ends by
  updating Meilisearch with what that run changed (about a minute, and search
  stays up throughout). Before this, the index only changed when rebuilt by
  hand. A full rebuild now updates in chunks instead of wiping the index first.
- **Fetch quality tooling.** `news_fetcher/quality_checks.py` (pure detectors
  for truncated text, leaked prompt labels, acronym casing and figures that
  appear in no source) and `news_fetcher/quality_report.py` (a read-only
  report covering headlines, summaries, grouping, bias and editions). Each
  run's history entry now records headline, grouping-review and edition
  counters, and Langfuse traces carry the story, article or outlet they were
  generated for.
- **Opinion and shopping filters at ingestion.** Opinion and editorial pieces
  (`/opinion/`, `/editorials/`, `/commentisfree/`, "Opinion:" titles) and
  shopping or affiliate roundups (`/shopping/`, `/deals/`) are no longer
  stored. Betting promos and promo-code titles are caught as well.

### Changed

- **Signed-out access is now a choice, and off by default.** Before this
  release, Headlines and the story, article and status pages were open to
  anyone, and `/article/<id>` served the full scraped text of any article
  (article IDs are sequential). Now every page needs an account unless you
  turn on `PUBLIC_READ_ACCESS`, and even then scraped text stays private.
  Signed-in users and public visitors land on Headlines.
- **All Stories now includes single-article stories by default.** A "Hide
  single-article stories" switch filters them out. The sidebar order is now
  Headlines, Grouped Stories, All Stories.
- **The scrape blocklist needs repeated failures.** A domain used to be
  blocked indefinitely after one bad scrape. Now it takes 3 failed articles
  within 48 hours, and the block expires 48 hours after the last failure.
  Domains marked permanent (hard paywalls) are unaffected. The blocklist page
  shows hits, expiry, and a Pending state for domains below the threshold.
- **The pipeline checks for real inference, not just a responding server.** At
  each checkpoint the scheduler makes one real embedding call. If Ollama's
  HTTP server answers but the GPU can't run the model, the run treats Ollama
  as down and degrades the way it does in an ordinary outage, instead of
  spending hours timing out.
- **A run on the fallback Ollama host re-checks the primary on schedule.**
  Before this, a long job that never went idle never noticed the primary
  coming back.
- **Story summary prompts have a size limit.** Article text in a summary
  prompt shares a 9,000-character budget (1,500 per article at most). Very
  large stories had been overflowing Ollama's default context, and their
  summaries came back empty or cut off.
- **The edition duplicate check is stricter.** Two stories that share title
  words now also need closely matching article embeddings before one is
  dropped as a duplicate of the other. Some common words are ignored as well.
  Replayed over 30 editions, wrongly flagged pairs fell from 45 to 6.
- **The entity veto in grouping** no longer splits one event into two stories
  just because two headlines name different countries; it stands down when
  the headlines share a real subject word.
- **Outlet names.** Bare domains (`nypost.com`, `cnn.com`) map to the outlet's
  real name, and AllSides name variants (PBS News, The Washington Times,
  Breitbart News Network) now match their ratings.
- **Admin UI refresh.**
  - Text meets WCAG AA contrast in both themes.
  - Colours live in one shared stylesheet (`aggregator/static/css/theme.css`).
  - Emoji are replaced with inline SVG icons.
  - Long button labels use sentence case.
  - "Hide single-article stories" is a real switch.
  - Search table headers stay visible while scrolling.
  - Story cards have one information line, a compact Left / Center / Right box
    and side-by-side buttons.
  - No text is smaller than 12px.
  - Each page type has one centred content width.
- **Code layout.** `aggregator/blueprints/admin.py` and
  `news_fetcher/fetch_and_store_articles.py` are now packages. This is pure
  code motion; no URLs or behaviour changed.

### Removed

- **"Rebuild Story Grouping"** and `force_regroup_all()`. It regenerated
  every embedding, deleted every story, and then crashed on any install that
  had ever published an edition, before it finished. For targeted repairs,
  use Ollama catch-up and Reclassify Articles in Admin Tools instead.

### Fixed

- **`bootstrap_admin.py` on an existing install.** It used to mark pending
  migrations as already applied, so `flask db upgrade` could never run them.
  It now stamps only an empty database, upgrades a migrated one, and stops
  with instructions for anything in between.
- **Merging duplicate outlets orphaned the moved articles.** Their outlet was
  set to NULL. Fixed, and the surviving outlet's bias is now copied onto them.
- **Headlines that were really error messages.** Output such as "(Error: Input
  transcripts are missing...)" is now rejected and retried next run, instead
  of being stored as the story's headline.
- **Acronyms in headlines** ("Gop", "Nato", "Team Usa") are corrected to their
  usual capitalisation before a headline is stored.
- **Cut-off summaries.** A story summary under 200 characters, or one that
  doesn't end a sentence, is rejected and retried next run instead of being
  published.
- **One article counted as several blocklist hits**, because each URL variant
  of the same article was counted separately. A single Fox News article was
  enough to block the domain.
- **Grouping merges orphaned articles.** `regroup_ungrouped_stories()` left
  every article it merged with no story at all, and the grouping review left
  the stories it emptied behind. Both now also move a vacated story's edition
  slot to the story it merged into, instead of dropping it.
- **Long outlet names** in bias tags now truncate instead of overflowing.
- **The article page's theme button** was unstyled. **The search table** no
  longer runs off the edge of 1100–1279px screens.

### Security

- **Scraped and generated HTML is cleaned at display time**, not only when
  it's stored. Two template filters, `clean_html` and `plain_text_br`
  (`aggregator/html_safety.py`), replace `| safe`. Links and images are limited
  to `http`, `https` and `mailto` at both stages.
- See also the accounts and public-access changes under Added and Changed.

### Upgrade Notes

- Pull the update, rebuild, run migrations, and restart the scheduler:

  ```bash
  docker compose up -d --build
  docker compose exec app flask db upgrade
  docker compose restart scheduler
  ```

  `scheduler` loads pipeline code once at startup, so it needs the restart
  for the inference check, blocklist, summary and dedupe changes to take
  effect.
- **Two migrations.**
  - **`c8e2f4a6b1d3` (user roles):** adds role, active flag and sign-in
    timestamps to `users`. **Every existing account becomes Admin**, because
    before this release any account could do everything, so nobody loses
    access. Lower roles afterwards at `/admin/users` if you want to. New
    accounts default to Reader.
  - **`a4b7f0d92c31` (scrape blocklist hit counts and expiry):** it only adds
    columns, and then adjusts existing rows:
    - **Automatic blocks:** domains you blocked automatically stay blocked for
      one more 48-hour window, then follow the new 3-hit rule, so you won't get
      a flood of paywall content from every domain at once.
    - **Permanent paywall domains:** 13 hard-paywall domains from the original
      seed list are set back to permanent if their rows exist (nytimes.com,
      wsj.com, ft.com, washingtonpost.com, theathletic.com, bloomberg.com,
      thetimes.co.uk, economist.com, newyorker.com, foreignpolicy.com, hbr.org,
      seekingalpha.com, barrons.com). On the maintainer's database an
      unblock/re-add cycle had silently changed them to non-permanent. If you
      made one of them non-permanent on purpose, check it after upgrading. Rows
      you've deleted are not re-added.
- **New optional settings:**
  - `DISPLAY_TIMEZONE` (defaults to `UTC`): the time zone dates are shown in.
  - `PUBLIC_READ_ACCESS` (defaults to `false`): set it to `true` to keep
    Headlines and story pages readable without an account, as they were
    before this release (now without scraped text).

  After changing either one in `.env`, run `docker compose up -d app`; a
  restart doesn't re-read `.env`.
- **If visitors used to read Headlines or story pages without logging in**,
  they will get the login page until you set `PUBLIC_READ_ACCESS=true`.
- **Search after upgrading:** documents now carry time fields that the window
  filter uses. Until the index is rebuilt, older documents drop out of
  windowed searches. Rebuild it once, in its own process; on a large database
  this takes a while (about 80 minutes for 115k articles):

  ```bash
  docker exec -w /app -e PYTHONPATH=/app muckscraper-app-1 python3 -m aggregator.search
  ```

---

## [0.7.0] - 2026-09-08

Status: beta. The theme of this release is **splitting cost from quality in
the LLM pipeline, and closing the loop on both GitHub issues that shaped
0.6.0**: LLM calls can now be routed per-tier to different providers, so the
~1,140 mechanical calls in a run can run locally while the ~65 summaries and
deep reports go somewhere better. Alongside that, several rounds of
grouping-accuracy fixes and content-quality filters landed, closing out
issues #1 and #8 for good.

### Added

- **Per-tier LLM provider routing (GitHub issue #8)** — `LLM_PROVIDER` is now
  the quality-tier provider and the optional `LLM_FAST_PROVIDER` overrides
  the fast tier, defaulting to `LLM_PROVIDER` when unset so single-provider
  installs are unchanged. `llm_client.provider_for_tier(tier)` is the one
  place that should ever be asked which backend serves a call.
- **A generic OpenAI-compatible provider** — `openrouter` speaks
  `chat/completions` against any base URL (`OPENROUTER_HOST`), so DeepSeek,
  OpenAI, Together, or a local vLLM endpoint all work with no new code.
  Built on the same client Groq already used.
- **Tier-aware LLM health checks and status** — `check_llm_status(tier=)` and
  `is_configured(tier=)` gate a pipeline stage on the backend that stage
  actually uses, instead of one boolean for the whole pipeline. With split
  routing, one tier being down is now the normal steady state rather than a
  fault, so the pipeline degrades per-stage instead of stopping outright.
- **What gets fetched is now fully DB-backed (GitHub issue #1, final slice)**
  — `ScheduledFetch` (`/admin/scheduled-fetches`) replaces the hardcoded
  NewsAPI/GNews query list, read fresh at the top of every run with no
  restart needed; `IngestionBlock` (`/admin/ingestion-blocks`) replaces
  `BLOCKED_SOURCES`/`BLOCKED_TITLE_KEYWORDS` with CRUD and a `kind` column
  (`source` / `title_keyword`).
- **An entity-veto guardrail on LLM grouping decisions** — `story_grouper.py`
  now rejects an LLM-approved match when the article and the destination
  story name different, non-overlapping countries, catching cases where the
  model approved a match on a shared boilerplate phrase ("death toll rises
  to X") with no real topical connection.
- **A confirmation dialog on "Rebuild Story Grouping"** in Admin Tools — this
  bulk action regenerates every article's embedding and reassigns it from
  scratch, and had no guard at all despite sitting next to two other
  destructive actions that do.
- **Content-quality filters at ingestion**: sports-betting tipster sites and
  betting-section/TV-listings stubs, syndicated advice columns (anchored to
  the column name so news coverage *about* a columnist isn't caught), and
  league/club PR sites, press-release wires, and `news.google.com`.
- **An independence floor on corroboration claims** — outlets whose stored
  content is a blocked paywall (0 chars) or a thin RSS stub no longer count
  toward a story's "N outlets reported this," which had been inflating
  every such claim (18 of 20 stories in one audited edition advertised
  multiple outlets when only 10 actually had more than one source above
  the floor).

### Changed

- **Headline generation now runs in one batch pass on the quality tier**,
  after grouping settles, instead of inline per-article interleaved with
  fast-tier grouping/classification calls. On a box where the model doesn't
  fit in VRAM, alternating tiers per call was costing ~40 minutes/run in
  pure model-swap time; batching next to the summary phase (which loads the
  same model anyway) costs about 8 minutes instead.
- The stale `GROQ_MODEL` default (a model Groq has since retired) was
  removed rather than replaced — an unset model now fails once at the
  config gate instead of 404ing on every request.
- `normalize_title_tokens()` is now memoized (`@lru_cache`), and
  `store_articles()` no longer eager-loads every recent story's articles
  up front — only the rare Python-fallback grouping path needs them, and
  pgvector does the real match in SQL without touching that relationship.

### Fixed

- **The grouping review could match an article against itself**, so the
  safety net built to catch and correct misgrouped articles could never
  actually move one — it always "confirmed" the article's existing
  placement at a fabricated similarity of exactly 1.0.
- **Three compounding bugs in `title_overlap_review`'s incumbent handling**:
  an article's current story could fail to make the LLM's candidate slate
  at all; even when present, the incumbent lost to superficial title
  overlap and list-position bias; and a story's stale headline (from before
  it lost an article) could hijack matching outright before the LLM step
  ever ran. All three fixed — candidate-slate pinning, an explicit
  `[CURRENT STORY]` label with a burden-of-proof prompt rule, and clearing
  a story's headline on any article departure rather than only when it
  drops to a single article.
- `headline_generated_at` is now backfilled for existing headlines, so they
  read as current instead of perpetually stale under the new staleness
  check.
- Two small dead-code spots from an earlier code audit: an unreachable
  Playwright JS fallback gated behind a literal `if False`, and a redundant
  duplicate-detection branch that could never change the outcome.

### Upgrade Notes

- Pull the update and run migrations:

  ```bash
  docker compose up -d --build
  docker compose exec app flask db upgrade
  ```

- **Restart the `scheduler` container after upgrading.** It imports pipeline
  code once at process startup and never reloads it — a `git pull` alone
  does not put any of this release's fixes into effect.
- `LLM_FAST_PROVIDER` is optional and defaults to `LLM_PROVIDER` when blank,
  so an install that only ever set `LLM_PROVIDER` sees no behavior change.
- `ScheduledFetch` and `IngestionBlock` are seeded from the values that used
  to be hardcoded, so an existing install keeps fetching and blocking
  exactly what it did before, on both migrations.

---

## [0.6.0] - 2026-08-08

Status: beta. The theme of this release is **configuration moving out of the
source and into the database**: topics, RSS feeds, LLM prompts, and the pipeline
run schedule are all editable from the admin UI now, so running your own instance
no longer means editing Python to change what gets fetched or how it gets
summarized. This release also adds two more LLM providers, makes the app safe to
run with multiple workers, and closes several security issues.

### Added

- **Admin-configurable pipeline (GitHub issue #1)** — the largest change in this release:
  - **Topics** are now a `Topic` model with icon/sort-order/active columns, managed at `/admin/topics`, replacing the hardcoded `constants.py:TOPICS` list
  - **RSS feeds** are now an `RssFeed` model keyed by `(url, bucket)`, managed at `/admin/rss-feeds`, replacing the three hardcoded feed lists in `rss_fetcher.py`
  - **LLM prompts** are now a `PromptTemplate` model with an immutable `default_text` and an editable `current_text`, managed at `/admin/prompts` with per-prompt reset-to-default and save-time placeholder validation
  - **Pipeline run schedule** is now a `PipelineSchedule` model (hour + fetch-only/full-pipeline + active), managed at `/admin/pipeline-schedule`, with a banner warning when the scheduler needs a restart to pick up changes
- **`install.sh`** — one-command setup: bootstraps `.env`, generates `SECRET_KEY`, warns on placeholder values, brings up postgres/meilisearch/app, runs `bootstrap_admin.py`, starts the scheduler
- **Additional LLM providers** — `LLM_PROVIDER` now accepts `groq` and `gemini` alongside `ollama`, with provider-specific model/embedding settings and Groq rate-limit retry
- **Fast model tier** — `OLLAMA_FAST_MODEL` (and the Gemini/Groq equivalents) routes the ~1,100 high-volume mechanical calls per run (grouping confirmation, topic classification, headline generation, outlet bias) to a smaller model, while summaries and deep reports stay on the main model. Defaults to the main model, so leaving it unset preserves the previous single-model behavior
- **Fallback Ollama host** with automatic recovery back to the primary (`OLLAMA_FALLBACK_HOST`, `OLLAMA_PRIMARY_RECHECK_SECONDS`)
- **Headlines section** (`/headlines`) — a plain-list view of the current published edition
- **Persistent sidebar navigation** across all non-settings pages, via shared partials
- **Container restart from Admin Tools**, backed by a separate small `docker_restart_proxy` service that holds the Docker socket so the public-facing app never has to. Refuses restarts that would interrupt a running operation, with an explicit override
- **Background execution with status polling for bulk admin actions** (GitHub issue #3), so long-running maintenance no longer hits the worker timeout
- **Optional automatic article-level deep analysis** during story fill, off by default (community PR #19)
- **Expanded topic taxonomy** — seven categories, splitting out US News and International News

### Changed

- Gunicorn now runs multiple workers (`GUNICORN_WORKERS`, default 2). Task claiming was made process-safe with an atomic Postgres upsert, replacing in-process locks that only coordinated threads within a single worker
- Story grouping uses a pgvector query instead of a Python cosine scan
- Editions collapsed to morning/evening only, with the morning window widened
- Headline ranking consolidated from up to five passes per pipeline run down to one
- Consolidated duplicated bias-bucket, aggregator-filter, and RSS-enrichment logic into shared helpers
- Orphaned `running` task statuses are now reconciled by staleness rather than by "found at boot", which is correct under multiple workers (community PR #20)
- Editions are published even without the private headline-ranking plugin, falling back to recency-ordered stories (community PR #16)

### Fixed

- **Every stored article generated a headline**, including single-article stories — an ORM relationship was double-counting a story's articles, so the `>= 2` guards were always true. Roughly 295 LLM calls per run, most of them wasted
- Outlet names taken from RSS channel titles could produce a fake outlet — Washington Post's world feed is titled "World" — so outlet names now resolve from the article URL domain where the feed title is unreliable
- Summary and deep-report generation failed silently when the LLM was unreachable; all four call sites now log a warning
- Labor disputes and political action at sports venues were classified as Sports rather than US Politics
- `story.articles` could silently drop the newest articles when truncated for prompts, because the relationship has no defined order
- Datetime comparison crash parsing NewsAPI/GNews articles (naive vs. aware)
- Missing `topics` columns after upgrading a database already stamped past the migration (community PR #10)
- Single-article view was lost after submitting a bias rating (community PR #12)
- Invisible button text on the article detail page (GitHub issue #5)
- Broken "Back to Feed" links across four admin templates

### Security

- **CSRF protection** (`CSRFProtect`) enabled app-wide, with tokens on all admin/auth POST forms and AJAX calls
- **A real `SECRET_KEY` is now required at startup** instead of silently falling back to a development default — enforced on both the admin app and the read-only public app
- **Open redirect** fixed in `redirect_to_articles()`, which was missing the `netloc` check the login flow already had
- **Postgres and the admin app are no longer bound to all interfaces**, so a default install does not expose them to the public internet
- **`.gitignore` now covers `.env` backups and variants** (`.env.*`, with `.env.sample` negated). Previously only plain `.env` was ignored, so a stray `.env.bak` holding live credentials could be committed by `git add -A`
- Case-sensitive matching in `BLOCKED_TITLE_KEYWORDS` meant capitalized entries could never match a lowercased title, silently disabling much of the filter
- Detailed Ollama host/role status moved off the public route

### Upgrade Notes

- For an existing install, pull the update and run migrations:

  ```bash
  docker compose up -d --build
  docker compose exec app flask db upgrade
  ```

- **Restart the `scheduler` container after upgrading.** Pipeline schedule rows become APScheduler jobs at process startup and are not re-read live.
- All new settings are optional and default to previous behavior. `OLLAMA_FAST_MODEL`, `GEMINI_FAST_MODEL`, and `GROQ_FAST_MODEL` fall back to the main model when unset; `GUNICORN_WORKERS` defaults to 2.
- Topics and prompts upgrade cleanly. The topics migration matches your existing rows by name and only fills in the new icon/sort-order columns, so custom topics are preserved. Prompts are seeded with their shipped defaults.
- **If you customized the RSS feed lists in `news_fetcher/rss_fetcher.py`, back them up before upgrading.** The migration seeds the `rss_feeds` table from this project's default lists, and `rss_fetcher.py` now reads feeds only from the database — so your edits to that file will stop taking effect and will not be carried over. Re-add them at `/admin/rss-feeds` after upgrading (they can be re-added at any time; nothing is destroyed in the file itself).
- More generally, editing `constants.py` or `rss_fetcher.py` to change topics or feeds no longer has any effect. Use `/admin/topics` and `/admin/rss-feeds` instead.

---

## [0.5.0] - 2026-06-03

Status: beta candidate. MuckScraper is moving from early alpha into beta: the
core fetch, grouping, admin, and edition workflows are now usable end-to-end on a
fresh install, while source balance, scrape quality, and headline-selection
tuning remain active areas of work.

### Added

- **Fresh-install bootstrap**:
  - `bootstrap_admin.py` initializes the database, stamps Alembic current for a fresh schema, and creates or updates the admin user from `.env`
  - `.env.sample` now includes `ADMIN_USERNAME`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD`
  - README install flow now starts core services, runs bootstrap, then starts the scheduler
- **Admin and operations tooling**:
  - new Admin Tools page centralizes maintenance actions
  - Meilisearch-backed admin search support with rebuild/status endpoints
  - expanded troubleshooting guide for scheduler, database, scrape, and run-metric checks
- **Scrape telemetry expansion**:
  - persisted scrape status, method, failure reason, and HTTP status now flow through ingestion, retries, and admin tooling
  - scrape outcome history is stored over time for operational review
  - low-value article detection flags roundups, live updates, video pages, and weak/duplicative scrape output
- **Edition and headline hardening**:
  - same-event headline candidates are filtered before publish
  - edition backfill continues down the ranked list to preserve edition size after duplicate removals
  - editions are capped at 20 stories even after mixed-coverage reservation and balance fills
  - previous-edition repeats are suppressed unless the story has new articles
  - stories with leftish and rightish coverage are favored during edition selection
- **Source-balance and RSS enrichment**:
  - added targeted right/center-right and international sources including Reason, National Post, The Telegraph, Toronto Sun, Washington Examiner, Newsmax, and Daily Wire
  - added bias lookup/normalization support for new outlets
  - added left and right targeted enrichment passes around headline ranking
- **Summary quality guardrails**:
  - story summaries and deep reports now skip generation when no article has enough readable scraped content
  - prompt filtering reduces misgrouped/outlier articles before story summaries and deep reports
- **Archived edition images**:
  - edition-story image metadata is stored on `EditionStory`
  - archived local copies can be generated from story article images for stable published output
  - low-resolution headline images are filtered out instead of shown as blurry thumbnails
- **Grouping review fields** on articles to support higher-scrutiny grouping workflows
- **Updated README screenshots** for story reader, bias tags, and article reader

### Changed

- Docker Compose no longer includes personal Langfuse or n8n environment hooks by default
- README now treats n8n, Matrix notifications, and similar workflow hooks as optional personal integrations
- Flask app now runs through `boot.sh` and Gunicorn in the container
- Scheduler startup catch-up logic waits for the next scheduled run unless a scheduled slot was actually missed
- Static headline export now defaults to the latest edition instead of rebuilding every historical edition on every run
- `process_current_edition()` skips unchanged stable stories earlier instead of repeatedly reconsidering them for summary work
- Story grouping now eager-loads recent story articles for the hot matching path and logs the configured lookback window
- Scraper retry behavior keeps degraded domains cooled down even when a URL-level fallback succeeds
- High-cost scrape variant fan-out is skipped after strong terminal failures like `401`, `403`, `404`, and `410`
- Edition dedupe matching was tightened to reduce false positives driven by generic political/process words
- README and project map were cleaned up so public repo docs no longer depend on deployment-specific/private headline-site files

### Fixed

- Fresh installs failing when `create_admin.py` ran before the `users` table existed
- Compose warnings for removed personal Langfuse/n8n variables in default installs
- Real same-event duplicate clusters making it into the same published edition
- Editions occasionally exceeding the 20-story target during balance fill
- Repeated stories carrying forward unchanged from the immediately previous edition
- Summaries and deep reports hallucinating from title-only article sets with no readable content
- Excessive retry churn on chronically blocked scrape domains
- Misleading public docs and repo-map references to deployment-specific paths
- Confusing Alembic migration filename mismatch for the archived-edition-image revision

### Upgrade Notes

- For a fresh install:

  ```bash
  cp .env.sample .env
  # Edit .env with API keys, model host, database settings, and admin login
  docker compose up -d --build postgres meilisearch app
  docker compose exec app python bootstrap_admin.py
  docker compose up -d scheduler
  ```

- For an existing install, pull the update and run migrations:

  ```bash
  docker compose up -d --build
  docker compose exec app flask db upgrade
  ```

- `create_admin.py` still exists, but `bootstrap_admin.py` is now the recommended first-run setup path.
- Langfuse and n8n are no longer part of the default Compose environment. Add them with your own override/env wiring if you use those personal workflows.

---
 
## [0.4.0] - 2026-05-04
 
### Added
 
- **Edition deduplication hardening**:
  - `seen_story_ids` set added to `publish_edition()` candidate loop — prevents a story appearing twice if it exists multiple times in the candidate pool
  - Final `top_20` slice now runs through a dedup pass before slicing
  - `UniqueConstraint('edition_id', 'story_id')` added to `EditionStory` model as a database-level safety net
- **Repeat story window expanded** — edition eligibility check now looks back across all editions published in the last 24 hours, not just the immediately previous edition. Prevents stories from recycling by skipping a single cycle
- **Story age filter** — `publish_edition()` candidate query now filters to stories created within the last 3 days. Previously, high-scoring old stories could remain in the candidate pool indefinitely
- **Carried-over story age cap** — fallback carried stories capped at 48 hours old. Previously, arbitrarily old stories could pad an edition if fewer than 20 fresh stories were available
- **Unscraped single-article story exclusion** — stories with exactly one article and no scraped content are excluded from edition eligibility
- **Video prefix stripping in story grouper** — `strip_video_prefix()` strips "WATCH:", "VIDEO:", "LIVE:", "LISTEN:", "BREAKING:", "PHOTOS:", "GALLERY:" and `[WATCH]` variants before embedding and before LLM title comparison. Prevents media-type prefixes from distorting semantic similarity scores
- **Outlet name normalization** — `normalize_source_name()` expanded with partial-match patterns for all major outlets, eliminating feed-title variants like "NPR Topics: News", "Al Jazeera – Breaking News, World News and Video from Al Jazeera", "NYT > World News", "World news | The Guardian"
- **`merge_duplicate_outlets()` admin function** — normalizes all outlet names and merges duplicates. Accessible from admin hamburger menu as "Merge Duplicate Outlets". Returns renamed/deleted/reassigned counts
### Changed
 
- `LOWER_THRESHOLD` in `story_grouper.py` lowered from `0.80` to `0.68` — gives Ollama more opportunity to confirm semantically equivalent but differently worded headlines
- Embeddings now generated from `title + content[:200]` snippet rather than title alone — anchors similarity to event substance, not surface wording
- `merge_duplicate_outlets()` now uses raw SQL `UPDATE articles SET outlet_id = :canonical WHERE outlet_id = :dup` with a post-update verification count before executing `DELETE` — eliminates article orphaning on merge
### Fixed
 
- Duplicate story IDs appearing in the same edition (same story_id at multiple ranks)
- `merge_duplicate_outlets()` orphaning articles — articles were being left with `outlet_id = NULL` when duplicate outlet records were deleted before article reassignment completed
---
 
## [0.3.0] - 2026-04-14
 
### Added
 
- **Image capture and display** — `image_url` added to Article model, populated from NewsAPI (`urlToImage`) and GNews (`image`). Images shown in articles feed
- **`backfill_images.py`** — utility to backfill image URLs from stored raw API payloads
- **Langfuse observability** — all LLM calls instrumented with tracing across summarizer, story grouper, headline generator, outlet bias, topic classifier
- **Authentication** — Flask-Login based auth with login page, admin user creation script (`create_admin.py`), and protected admin routes
- **Blueprint architecture** — routes split into blueprints:
  - `admin.py` — all write and trigger routes requiring authentication
  - `auth.py` — login/logout
- **`filters.py`** — Jinja2 template filters extracted from app factory and registered via `register_filters(app)`
- **`constants.py`** — shared `TOPICS` and `AGGREGATORS` constants extracted from app factory
- **Scrape blocklist** — automatic detection and blocking of bad scrapes:
  - Strong indicators: login walls, captchas, bot detection, subscriber gates
  - Weak indicators: short content with sign-in/subscribe text
  - Duplicate detection: content near-identical to 2+ other articles from same outlet flagged as login/error page
  - Pre-populated permanent blocklist of hard-paywalled domains (NYT, WSJ, FT, Bloomberg, etc.)
  - `audit_existing_scrapes()` — retroactive scan of all stored content
  - `/scrape-blocklist` admin page — view blocked domains, unblock auto-blocked entries, trigger audit
- **New Alembic migrations**: `image_url` on articles and `scrape_blocklist` table
- **Gunicorn** — replaces Flask development server in production
### Changed
 
- Scheduler fetch times changed to 4 daily runs at 12am, 7am, 12pm, 6pm Eastern
- Scheduler categories restructured: Top News, World News, US Politics, Business & Economy, Science & Health, Technology, National Security & Foreign Policy
- `regroup_ungrouped_stories()` replaced O(n²) Python cosine loop with pgvector `<=>` nearest-neighbour SQL query
- Force Re-group button now requires confirmation before executing
- `create_app()` reduced to wiring only
### Fixed
 
- Duplicate `summarize_article` definition in `summarizer.py`
- `cleanup_duplicates.py` double app instantiation
- Alembic migration branch conflict
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
- Topics redesigned — now 7 categories classified by LLM content analysis
- Scheduler fetch configurations updated
- TOPICS list simplified
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
- Global ↻ Scrape Missing sidebar button
- DB indexes on key columns
- Raw API payload storage with 30-day auto-cleanup
- `restart.sh` script for soft rebuilds that preserve the database
### Fixed
- Ollama catchup button breaking article links and summarization
- Re-grouping creating new stories instead of only matching existing ones
- Auto-summarization capped to 10 stories per batch
- HTML tags being sent to Ollama in summaries
---
 
## [0.1.2] - 2026-03-13
 
### Added
- Full article scraping with BeautifulSoup and Playwright fallback
- Sanitized HTML storage for scraped articles
- Article reader page at `/article/<id>`
- LLM story grouping using keyword pre-filter and Ollama match decision
- Smart Brevity summary format
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
- Removed standalone `news_fetcher` container
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

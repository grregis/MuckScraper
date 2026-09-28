# aggregator/seed_defaults.py
"""Default configuration rows for a fresh install.

Seven tables are DB-backed config that the app cannot function without:
topics, RSS feeds, scheduled fetches, the pipeline schedule, ingestion blocks,
the scrape blocklist, and LLM prompt templates. Their default rows are inserted
by Alembic migrations -- which is fine on an install that upgrades through the
chain, and broken on a fresh one.

`bootstrap_admin.py` sets a fresh database up with `db.create_all()` followed by
`stamp("head")`. `create_all()` builds the tables empty and `stamp` marks the
whole chain as already applied, so the seeding migrations never run *and can
never run afterwards* -- `flask db upgrade` is a no-op from that point. The
result was a stack that started, logged in, and then did nothing at all: no
scheduled runs (empty `pipeline_schedule`), nothing fetched (empty
`scheduled_fetches`), and no summaries, headlines or classification, because
`render_prompt()` returns None for every key when `prompt_templates` is empty.

Measured 2026-09-28 before writing this: `create_all()`'s schema is byte-for-byte
the migrated schema (129 vs 130 columns across `public`, the sole difference
being `alembic_version.version_num`, which `stamp` creates). So nothing
structural was ever missing -- only the data. Replaying the migration chain
instead was ruled out because the chain has no initial-schema migration: its
root revision is an `ALTER TABLE`, so `upgrade()` on an empty database fails
immediately.

`seed_defaults()` is INSERT-ONLY and idempotent. It never updates or deletes an
existing row, which is the property that makes it safe to call unconditionally
on every install, new or old:

  - On an existing install every row is already present, so it is a no-op.
  - It can never clobber a prompt, feed or blocklist entry a self-hoster has
    edited -- required by CLAUDE.md's rule that a `1.x` -> `1.y` upgrade must
    not discard a user's customizations.

The data below duplicates what the migrations hold. That duplication is
deliberate (decided 2026-09-28): the alternative was to have the migrations
import this module, which means editing migrations that have already run
everywhere. The cost is drift if someone changes a migration's defaults without
changing these. Mitigations: the literals were generated mechanically from the
migrations and verified to round-trip exactly, never transcribed by hand, and
`tests/test_seed_defaults.py` asserts these values still match the migrations'
so drift fails a test rather than shipping silently.
"""

import logging
from datetime import datetime

from aggregator import db
from aggregator.models import (
    IngestionBlock,
    PipelineSchedule,
    PromptTemplate,
    RssFeed,
    ScheduledFetch,
    ScrapeBlocklist,
    Topic,
)

logger = logging.getLogger(__name__)



# --- BEGIN GENERATED DATA ---------------------------------------------------
# Generated from the seven seeding migrations (see module docstring). Do not
# reformat by hand; regenerate if a migration's defaults change.

SEED_TOPICS = [('US Politics', 'UP', 0),
 ('US News', 'UN', 1),
 ('International News', 'IN', 2),
 ('Sci/Tech', 'ST', 3),
 ('Sports', 'SP', 4),
 ('Buss/Fin', 'BF', 5),
 ('Other', 'OT', 6)]

SEED_SCHEDULED_FETCHES = [{'label': 'US Politics',
  'description': 'Congress, White House, courts, elections',
  'mode': 'query',
  'newsapi_country': None,
  'newsapi_category': None,
  'newsapi_query': 'US politics congress white house senate supreme court',
  'gnews_query': 'US politics congress white house',
  'gnews_category': None},
 {'label': 'Business & Economy',
  'description': 'Top business headlines',
  'mode': 'top',
  'newsapi_country': 'us',
  'newsapi_category': 'business',
  'newsapi_query': None,
  'gnews_query': None,
  'gnews_category': 'business'},
 {'label': 'Science & Health',
  'description': 'Research, medicine, technology',
  'mode': 'query',
  'newsapi_country': None,
  'newsapi_category': None,
  'newsapi_query': 'scientific breakthroughs medical research healthcare tech',
  'gnews_query': 'science health research',
  'gnews_category': 'science'},
 {'label': 'Sports',
  'description': 'Top sports headlines',
  'mode': 'top',
  'newsapi_country': 'us',
  'newsapi_category': 'sports',
  'newsapi_query': None,
  'gnews_query': None,
  'gnews_category': 'sports'},
 {'label': 'World News',
  'description': 'International news, conflict, diplomacy',
  'mode': 'query',
  'newsapi_country': None,
  'newsapi_category': None,
  'newsapi_query': 'international world global news conflicts diplomacy',
  'gnews_query': 'world global news',
  'gnews_category': 'world'}]

SEED_PIPELINE_SCHEDULE = [{'hour': 7, 'run_full_pipeline': True},
 {'hour': 12, 'run_full_pipeline': False},
 {'hour': 17, 'run_full_pipeline': True},
 {'hour': 22, 'run_full_pipeline': False}]

SEED_RSS_GENERAL = ['https://feeds.apnews.com/rss/topnews',
 'https://feeds.reuters.com/reuters/topNews',
 'https://feeds.bbci.co.uk/news/rss.xml',
 'https://feeds.npr.org/1001/rss.xml',
 'https://www.pbs.org/newshour/feeds/rss/headlines',
 'https://www.economist.com/the-world-this-week/rss.xml',
 'https://rss.cnn.com/rss/edition.rss',
 'https://feeds.nbcnews.com/nbcnews/public/news',
 'https://feeds.washingtonpost.com/rss/world',
 'https://www.nytimes.com/svc/collections/v1/publish/https://www.nytimes.com/section/world/rss.xml',
 'https://www.theguardian.com/world/rss',
 'https://moxie.foxnews.com/google-publisher/latest.xml',
 'https://moxie.foxbusiness.com/google-publisher/latest.xml',
 'https://feeds.a.dj.com/rss/RSSWorldNews.xml',
 'https://nypost.com/feed/',
 'https://www.washingtontimes.com/rss/headlines/news/politics/',
 'https://reason.com/feed/',
 'https://www.nationalreview.com/feed/',
 'https://thehill.com/feed/',
 'https://api.axios.com/feed/',
 'https://rss.politico.com/politics-news.xml',
 'https://www.aljazeera.com/xml/rss/all.xml',
 'https://nationalpost.com/feed/',
 'https://torontosun.com/feed/',
 'https://feeds.abcnews.com/abcnews/topstories',
 'https://www.cbsnews.com/latest/rss/main']

SEED_RSS_RIGHT_ENRICHMENT = ['https://moxie.foxnews.com/google-publisher/latest.xml',
 'https://feeds.a.dj.com/rss/RSSWorldNews.xml',
 'https://nypost.com/feed/',
 'https://www.washingtonexaminer.com/rss',
 'https://www.washingtontimes.com/rss/headlines/news/politics/',
 'https://www.nationalreview.com/feed/',
 'https://moxie.foxbusiness.com/google-publisher/latest.xml',
 'https://www.newsmax.com/rss/Newsfront/16/',
 'https://www.dailywire.com/feeds/rss.xml']

SEED_RSS_LEFT_ENRICHMENT = ['https://rss.cnn.com/rss/edition.rss',
 'https://feeds.nbcnews.com/nbcnews/public/news',
 'https://feeds.washingtonpost.com/rss/world',
 'https://www.nytimes.com/svc/collections/v1/publish/https://www.nytimes.com/section/world/rss.xml',
 'https://www.theguardian.com/world/rss',
 'https://feeds.npr.org/1001/rss.xml',
 'https://www.cbsnews.com/latest/rss/main']

SEED_BLOCK_SOURCES = [('github.com', 'Developer/repo noise, not news'),
 ('github.blog', 'Developer/repo noise, not news'),
 ('dev.to', 'Developer/repo noise, not news'),
 ('stackoverflow.com', 'Developer/repo noise, not news'),
 ('reddit.com', 'Developer/repo noise, not news'),
 ('npmjs.com', 'Developer/repo noise, not news'),
 ('pypi.org', 'Developer/repo noise, not news'),
 ('actionnetwork.com', 'Sports-betting tipster site -- entire output is odds and picks'),
 ('vsin.com', 'Sports-betting tipster site -- entire output is odds and picks'),
 ('covers.com', 'Sports-betting tipster site -- entire output is odds and picks'),
 ('sportsline.com', 'Sports-betting tipster site -- entire output is odds and picks'),
 ('lineups.com', 'Sports-betting tipster site -- entire output is odds and picks'),
 ('nhl.com', 'Official league media -- PR, not journalism (substring also covers subdomains)'),
 ('mlb.com', 'Official league media -- PR, not journalism (substring also covers subdomains)'),
 ('nba.com', 'Official league media -- PR, not journalism (substring also covers subdomains)'),
 ('nfl.com', 'Official league media -- PR, not journalism (substring also covers subdomains)'),
 ('49ers.com', 'Official club media -- PR, not journalism'),
 ('atlantafalcons.com', 'Official club media -- PR, not journalism'),
 ('azcardinals.com', 'Official club media -- PR, not journalism'),
 ('baltimoreravens.com', 'Official club media -- PR, not journalism'),
 ('bengals.com', 'Official club media -- PR, not journalism'),
 ('buccaneers.com', 'Official club media -- PR, not journalism'),
 ('buffalobills.com', 'Official club media -- PR, not journalism'),
 ('chargers.com', 'Official club media -- PR, not journalism'),
 ('chicagobears.com', 'Official club media -- PR, not journalism'),
 ('chiefs.com', 'Official club media -- PR, not journalism'),
 ('clevelandbrowns.com', 'Official club media -- PR, not journalism'),
 ('commanders.com', 'Official club media -- PR, not journalism'),
 ('dallascowboys.com', 'Official club media -- PR, not journalism'),
 ('denverbroncos.com', 'Official club media -- PR, not journalism'),
 ('detroitlions.com', 'Official club media -- PR, not journalism'),
 ('giants.com', 'Official club media -- PR, not journalism'),
 ('jaguars.com', 'Official club media -- PR, not journalism'),
 ('miamidolphins.com', 'Official club media -- PR, not journalism'),
 ('neworleanssaints.com', 'Official club media -- PR, not journalism'),
 ('newyorkjets.com', 'Official club media -- PR, not journalism'),
 ('orlandomagic.com', 'Official club media -- PR, not journalism'),
 ('packers.com', 'Official club media -- PR, not journalism'),
 ('panthers.com', 'Official club media -- PR, not journalism'),
 ('patriots.com', 'Official club media -- PR, not journalism'),
 ('philadelphiaeagles.com', 'Official club media -- PR, not journalism'),
 ('pistons.com', 'Official club media -- PR, not journalism'),
 ('raiders.com', 'Official club media -- PR, not journalism'),
 ('seahawks.com', 'Official club media -- PR, not journalism'),
 ('steelers.com', 'Official club media -- PR, not journalism'),
 ('tennesseetitans.com', 'Official club media -- PR, not journalism'),
 ('therams.com', 'Official club media -- PR, not journalism'),
 ('timberwolves.com', 'Official club media -- PR, not journalism'),
 ('vikings.com', 'Official club media -- PR, not journalism'),
 ('businesswire.com', 'Press-release wire -- corporate announcements carried verbatim'),
 ('prnewswire.com', 'Press-release wire -- corporate announcements carried verbatim'),
 ('prnewswire.co.uk', 'Press-release wire -- corporate announcements carried verbatim'),
 ('globenewswire.com', 'Press-release wire -- corporate announcements carried verbatim'),
 ('news.google.com', 'Aggregator, not an outlet -- produced duplicate headlines and a bogus bias score')]

SEED_BLOCK_TITLE_KEYWORDS = [('starred', 'Developer/repo noise, not news'),
 ('forked', 'Developer/repo noise, not news'),
 ('pull request', 'Developer/repo noise, not news'),
 ('merged', 'Developer/repo noise, not news'),
 ('repository', 'Developer/repo noise, not news'),
 ('npm package', 'Developer/repo noise, not news'),
 ('pypi', 'Developer/repo noise, not news'),
 ('added to pypi', 'Developer/repo noise, not news'),
 ('released on pypi', 'Developer/repo noise, not news'),
 ('week in review', 'Roundup/newsletter format, not an event'),
 ('patch tuesday', 'Developer/repo noise, not news'),
 ('added to npm', 'Developer/repo noise, not news'),
 ('new release:', 'Developer/repo noise, not news'),
 ('changelog:', 'Developer/repo noise, not news'),
 ('box office', 'Recap or transaction item, not an event'),
 ('box score', 'Recap or transaction item, not an event'),
 ('game recap', 'Recap or transaction item, not an event'),
 ('highlights:', 'Recap or transaction item, not an event'),
 ('traded to', 'Recap or transaction item, not an event'),
 ('signs with', 'Recap or transaction item, not an event'),
 ('scores in', 'Recap or transaction item, not an event'),
 ('Nintendo', 'Gaming coverage'),
 ('PlayStation', 'Gaming coverage'),
 ('Xbox', 'Gaming coverage'),
 ('Game review', 'Gaming coverage'),
 ('Gameplay', 'Gaming coverage'),
 ('eSports', 'Gaming coverage'),
 ('patch notes', 'Gaming coverage'),
 ('Twitch', 'Gaming coverage'),
 ('Fortnite', 'Gaming coverage'),
 ('Minecraft', 'Gaming coverage'),
 ('Pokemon', 'Gaming coverage')]

SEED_SCRAPE_BLOCKLIST = [('nytimes.com', 'Hard paywall — scraping permanently blocked'),
 ('wsj.com', 'Hard paywall — scraping permanently blocked'),
 ('ft.com', 'Hard paywall — scraping permanently blocked'),
 ('washingtonpost.com', 'Hard paywall — scraping permanently blocked'),
 ('theathletic.com', 'Hard paywall — scraping permanently blocked'),
 ('bloomberg.com', 'Hard paywall — scraping permanently blocked'),
 ('thetimes.co.uk', 'Hard paywall — scraping permanently blocked'),
 ('economist.com', 'Hard paywall — scraping permanently blocked'),
 ('newyorker.com', 'Hard paywall — scraping permanently blocked'),
 ('foreignpolicy.com', 'Hard paywall — scraping permanently blocked'),
 ('hbr.org', 'Hard paywall — scraping permanently blocked'),
 ('seekingalpha.com', 'Hard paywall — scraping permanently blocked'),
 ('barrons.com', 'Hard paywall — scraping permanently blocked')]

PROMPT_STORY_SUMMARY = """You are a {persona} writing an executive summary for a news briefing.

Below are multiple news articles covering the same story. Write a concise executive summary.

Rules:
- Write exactly one short paragraph
- Use 3 to 5 sentences
- Explain what happened, why it matters, and the most important current development
- No bullet points
- No section labels
- No markdown or prefatory text
- Keep it sharp and readable for a front-page briefing

Articles:
{combined}

Executive Summary:"""

PROMPT_DEEP_REPORT_POLITICS = """You are an experienced media analyst writing a detailed report on how different news outlets are covering the same political story.

Below are articles from the current source set, grouped by available outlet bias.

Source availability:
{source_availability}

{combined}

Write a detailed analytical report using this EXACT format:

The story: [2-3 sentences explaining what happened factually]

How the left is covering it: [Only describe left-leaning coverage if left-leaning sources are listed above. If no left-leaning sources are listed, write exactly: "No left-leaning sources were found in the current coverage."]

How the center is covering it: [Only describe center coverage if center sources are listed above. If no center sources are listed, write exactly: "No center sources were found in the current coverage."]

How the right is covering it: [Only describe right-leaning coverage if right-leaning sources are listed above. If no right-leaning sources are listed, write exactly: "No right-leaning sources were found in the current coverage."]

What's contested: [Where the different sides disagree most sharply, what facts or framings are in dispute]

What's missing: [What angles or perspectives seem absent from the coverage, what questions aren't being asked]

What's next: [One sentence on what to watch for]

Rules:
- Use EXACTLY the labels shown above including the colon
- Be specific about framing differences, not just topic differences
- Do not infer, invent, or speculate about how a missing source bucket would cover the story
- If a source bucket has no listed articles, use the exact "No ... sources were found" sentence for that section
- Stay neutral and analytical in your own voice
- No markdown, no extra formatting
- Do not add any text before or after the structure above"""

PROMPT_DEEP_REPORT_SCIENCE = """You are a science journalist writing a detailed report on a scientific or technology development.

Below are articles covering the same story:

{combined}

Write a detailed analytical report using this EXACT format:

The discovery or development: [2-3 sentences explaining what happened or was discovered factually]

Why it matters: [The scientific or technological significance — what does this change or enable?]

What the research shows: [Key findings, data points, or technical details from the coverage]

Real world impact: [How this affects people, industries, or society in practical terms]

What experts are saying: [Notable quotes or expert opinions from the coverage. If none available, say "Expert commentary not available in current coverage."]

What's still unknown: [Open questions, limitations of the research, or what needs further study]

What's next: [One sentence on upcoming developments or what to watch for]

Rules:
- Use EXACTLY the labels shown above including the colon
- Focus on accuracy and significance over drama
- Stay neutral and factual
- No markdown, no extra formatting
- Do not add any text before or after the structure above"""

PROMPT_DEEP_REPORT_SPORTS = """You are a sports journalist writing a factual recap and analysis of a sports story.

Below are articles covering the same story:

{combined}

Write a detailed report using this EXACT format:

What happened: [2-3 sentences with the key facts — scores, results, or news]

Key performances: [Standout players, teams, or moments from the coverage. If not a game recap, describe the key people involved.]

The bigger picture: [What this means for standings, playoffs, championships, contracts, or the sport more broadly]

By the numbers: [Key stats, records, or figures mentioned in the coverage. If none available, say "Detailed statistics not available in current coverage."]

What's next: [One sentence on upcoming games, decisions, or developments to watch]

Rules:
- Use EXACTLY the labels shown above including the colon
- Focus on facts and context over opinion
- No markdown, no extra formatting
- Do not add any text before or after the structure above"""

PROMPT_DEEP_REPORT_BUSINESS = """You are a financial journalist writing a detailed report on a business or markets story.

Below are articles covering the same story:

{combined}

Write a detailed analytical report using this EXACT format:

The story: [2-3 sentences explaining what happened factually]

Market impact: [How markets, stocks, or prices have reacted based on the coverage]

What companies or sectors are affected: [Key players, industries, or markets involved and how they are impacted]

What analysts are saying: [Expert or analyst opinions from the coverage. If none available, say "Analyst commentary not available in current coverage."]

The broader economic picture: [How this fits into wider economic trends, policy, or conditions]

Risks and opportunities: [Key risks or opportunities this creates for investors, businesses, or consumers]

What's next: [One sentence on key dates, decisions, or developments to watch]

Rules:
- Use EXACTLY the labels shown above including the colon
- Focus on market and economic significance
- Stay neutral and factual
- No markdown, no extra formatting
- Do not add any text before or after the structure above"""

PROMPT_DEEP_REPORT_DEFAULT = """You are an experienced journalist writing a detailed report on a news story.

Below are articles covering the same story:

{combined}

Write a detailed analytical report using this EXACT format:

The story: [2-3 sentences explaining what happened factually]

Why it matters: [The significance of this story — who it affects and how]

Key details: [The most important facts, figures, or developments from the coverage]

Different perspectives: [How different outlets or sources are framing this story. If coverage is uniform, say what angle is being emphasized.]

What's missing: [What angles or questions seem absent from the coverage]

What's next: [One sentence on what to watch for]

Rules:
- Use EXACTLY the labels shown above including the colon
- Stay neutral and analytical
- Compare only the outlets and perspectives actually present in the article list
- Do not use left/right political framing unless the story is explicitly about politics, government, law, elections, or policy
- No markdown, no extra formatting
- Do not add any text before or after the structure above"""

PROMPT_ARTICLE_SUMMARY = """You are a {persona} writing a tight Smart Brevity-style article briefing.

Below is a news article. Write a concise briefing using EXACTLY this format:

The big picture: [One direct sentence on what happened.]

Why it matters: [1-2 short sentences on why this story matters.]

Quick analysis: [1-2 short sentences on the framing, tension, consequence, uncertainty, or what stands out most.]

What's next: [One sentence on what to watch for next.]

Rules:
- Use EXACTLY the labels shown above including the colon
- No bullets
- Keep the full response to 4 short sections only
- Be concrete, not generic
- Do not repeat the same idea in multiple sections
- No markdown, no extra formatting, no commentary
- Do not add any text before or after the structure above

Article title: {article_title}

Article content:
{clean_content}

Summary:"""

PROMPT_ARTICLE_DEEP_ANALYSIS_POLITICS = """You are a political analyst writing a focused article analysis.

Analyze this political article using EXACTLY this format:

Core argument: [2-3 sentences summarizing the article's main thesis and factual basis]

How it frames the issue: [What assumptions, emphasis, or political framing the piece uses]

What evidence it relies on: [The main facts, sources, or claims used to support the argument]

What to question or watch: [Potential blind spots, unresolved questions, or what future reporting should clarify]

Rules:
- Use EXACTLY the labels shown above including the colon
- Stay analytical, not partisan
- No markdown, no extra formatting
- Do not add any text before or after the structure above

Article title: {article_title}

Article content:
{clean_content}

Analysis:"""

PROMPT_ARTICLE_DEEP_ANALYSIS_SCIENCE = """You are a science and technology journalist writing a technical analysis.

Analyze this article using EXACTLY this format:

What the article says: [2-3 sentences summarizing the core finding or development]

Technical substance: [The key mechanism, data, or technical concept explained in the article]

Why this matters: [What the development changes in practical or scientific terms]

What remains uncertain: [Limitations, caveats, unanswered questions, or hype risk]

Rules:
- Use EXACTLY the labels shown above including the colon
- Prioritize clarity and technical accuracy
- No markdown, no extra formatting
- Do not add any text before or after the structure above

Article title: {article_title}

Article content:
{clean_content}

Analysis:"""

PROMPT_ARTICLE_DEEP_ANALYSIS_BUSINESS = """You are a financial journalist writing a markets and business analysis.

Analyze this article using EXACTLY this format:

What happened: [2-3 sentences summarizing the business or market event]

What is driving it: [The main financial, operational, or policy factors behind it]

Who is affected: [The companies, sectors, investors, or consumers most affected]

What to watch next: [Risks, catalysts, or decision points that matter going forward]

Rules:
- Use EXACTLY the labels shown above including the colon
- Focus on economic significance, not fluff
- No markdown, no extra formatting
- Do not add any text before or after the structure above

Article title: {article_title}

Article content:
{clean_content}

Analysis:"""

PROMPT_TOPIC_CLASSIFIER = """You are a news editor categorizing articles. You must respond with ONLY category names from the list below, one per line. No other text, no notes, no explanations, no parentheses.

Article: "{text}"

Categories (choose only from these exact names):
{categories_list}

Rules:
- Use EXACT category names only — do not create new categories
- US Politics means US federal government, Congress, White House, elections, federal courts/policy, or any US government action or statement toward another country (diplomacy, sanctions, tariffs, military orders)
- International News means events, governments, conflicts, or disasters in other countries. If a story is about a US government action toward another country, use BOTH US Politics and International News
- US News means domestic US news that is NOT about government or politics — crime, accidents, disasters, lawsuits, local/state news, transportation, weather
- Entertainment, celebrity, lifestyle, and human-interest stories belong to Other, not US News
- Sci/Tech means technology, science, research, AI, space — NOT general business news about tech companies (use Buss/Fin for stock/earnings stories)
- Buss/Fin means financial markets, economics, corporate earnings, mergers — NOT general commerce
- Sports contracts and player signings belong to Sports only, not Buss/Fin
- Labor disputes, strikes, unionization votes, protests, or other political/regulatory action are US Politics, not Sports, even when they take place at or involve a sports venue, team, or event — the venue is incidental to a fundamentally political story
- Pick the most specific category — if it's clearly Sports, do not also add other categories
- Maximum 2 categories per article unless truly necessary
- If none apply, respond with only: Other
- Your entire response must be category names only — no parentheses, no notes, no commentary"""

PROMPT_OUTLET_BIAS_BY_NAME = """You are a media bias analyst. Rate the political bias of the news outlet "{outlet_name}" on this scale:
1 = Left
2 = Lean Left
3 = Center
4 = Lean Right
5 = Right

Rules:
- Respond with a single integer between 1 and 5 only
- No explanation, no punctuation, just the number
- If you have never heard of the outlet or genuinely cannot determine its bias, respond with the single word: unknown

Outlet: {outlet_name}
Rating:"""

PROMPT_OUTLET_BIAS_BY_ARTICLE = """You are a media bias analyst. Read the following news article and rate its political bias on this scale:
1 = Left
2 = Lean Left
3 = Center
4 = Lean Right
5 = Right

Consider the language used, framing, and perspective presented in the article itself.

Rules:
- Respond with a single integer between 1 and 5 only
- No explanation, no punctuation, just the number
- If you genuinely cannot determine the bias from the content, respond with the single word: unknown

Article:
{article_text}

Rating:"""

PROMPT_HEADLINE_GENERATOR = """You are a wire service editor writing a single headline.

Below are multiple news articles covering the same story:
{titles}

Write ONE headline for this story in wire service style.

Rules:
- Who/what/where in one line
- Maximum 15 words
- Present tense, active voice
- No punctuation at the end
- No quotes around the headline
- Do not include source names or outlet names
- Respond with ONLY the headline, nothing else"""

SEED_PROMPTS = [
    ('story_summary', "Story executive summary (shown as the story's short summary)", PROMPT_STORY_SUMMARY),
    ('deep_report.politics', 'Deep report — political stories (left/center/right breakdown)', PROMPT_DEEP_REPORT_POLITICS),
    ('deep_report.science', 'Deep report — science/tech stories', PROMPT_DEEP_REPORT_SCIENCE),
    ('deep_report.sports', 'Deep report — sports stories', PROMPT_DEEP_REPORT_SPORTS),
    ('deep_report.business', 'Deep report — business/markets stories', PROMPT_DEEP_REPORT_BUSINESS),
    ('deep_report.default', 'Deep report — general/uncategorized stories', PROMPT_DEEP_REPORT_DEFAULT),
    ('article_summary', 'Per-article Smart Brevity summary', PROMPT_ARTICLE_SUMMARY),
    ('article_deep_analysis.politics', 'Per-article deep analysis — political articles', PROMPT_ARTICLE_DEEP_ANALYSIS_POLITICS),
    ('article_deep_analysis.science', 'Per-article deep analysis — science/tech articles', PROMPT_ARTICLE_DEEP_ANALYSIS_SCIENCE),
    ('article_deep_analysis.business', 'Per-article deep analysis — business articles', PROMPT_ARTICLE_DEEP_ANALYSIS_BUSINESS),
    ('topic_classifier', 'Topic classification prompt', PROMPT_TOPIC_CLASSIFIER),
    ('outlet_bias.by_name', 'Outlet bias rating by outlet name', PROMPT_OUTLET_BIAS_BY_NAME),
    ('outlet_bias.by_article', 'Outlet bias rating by article content', PROMPT_OUTLET_BIAS_BY_ARTICLE),
    ('headline_generator', 'Wire-style headline generation for multi-article stories', PROMPT_HEADLINE_GENERATOR),
]

# --- END GENERATED DATA -----------------------------------------------------

def _seed_topics(counts):
    """Topics, ordered. The only seeder that fills a column on an existing row.

    A pre-existing Topic created by classification (topics are auto-created when
    the classifier returns an unseen name) can be missing icon/sort_order, which
    the admin UI sorts by -- so fill those two if and only if they are NULL.
    That is not a customization anyone set, so it is not an overwrite; anything
    the user did set is left alone.
    """
    for name, icon, sort_order in SEED_TOPICS:
        topic = Topic.query.filter_by(name=name).first()
        if topic is None:
            db.session.add(Topic(name=name, icon=icon, sort_order=sort_order, is_active=True))
            counts["topics"] += 1
        else:
            if topic.icon is None:
                topic.icon = icon
                counts["topics_backfilled"] += 1
            if topic.sort_order is None:
                topic.sort_order = sort_order


def _seed_scheduled_fetches(counts):
    """What each run pulls from NewsAPI/GNews. Keyed by the unique `label`."""
    for index, entry in enumerate(SEED_SCHEDULED_FETCHES):
        if ScheduledFetch.query.filter_by(label=entry["label"]).first() is not None:
            continue
        db.session.add(ScheduledFetch(sort_order=index, is_active=True, **entry))
        counts["scheduled_fetches"] += 1


def _seed_pipeline_schedule(counts):
    """When runs happen. Keyed by the unique `hour`."""
    for entry in SEED_PIPELINE_SCHEDULE:
        if PipelineSchedule.query.filter_by(hour=entry["hour"]).first() is not None:
            continue
        db.session.add(PipelineSchedule(is_active=True, **entry))
        counts["pipeline_schedule"] += 1


def _seed_rss_feeds(counts):
    """RSS sources. Keyed by (url, bucket) -- a feed can be in several buckets."""
    buckets = (
        ("general", SEED_RSS_GENERAL),
        ("right_enrichment", SEED_RSS_RIGHT_ENRICHMENT),
        ("left_enrichment", SEED_RSS_LEFT_ENRICHMENT),
    )
    now = datetime.utcnow()
    for bucket, urls in buckets:
        for url in urls:
            if RssFeed.query.filter_by(url=url, bucket=bucket).first() is not None:
                continue
            db.session.add(RssFeed(url=url, bucket=bucket, enabled=True, added_at=now))
            counts["rss_feeds"] += 1


def _seed_ingestion_blocks(counts):
    """Sources and title keywords refused before anything is stored.

    Patterns are stored lowercase to match what the admin routes write and what
    get_ingestion_blocks() compares against -- the source lists are mixed case
    ("Nintendo", "PlayStation"), and seeding them verbatim would let the UI later
    add a lowercase duplicate straight past the unique constraint.
    """
    now = datetime.utcnow()
    for kind, entries in (("source", SEED_BLOCK_SOURCES),
                          ("title_keyword", SEED_BLOCK_TITLE_KEYWORDS)):
        for pattern, note in entries:
            pattern = pattern.lower()
            if IngestionBlock.query.filter_by(kind=kind, pattern=pattern).first() is not None:
                continue
            db.session.add(IngestionBlock(
                kind=kind, pattern=pattern, note=note, is_active=True, added_at=now,
            ))
            counts["ingestion_blocks"] += 1


def _seed_scrape_blocklist(counts):
    """Domains whose content will never scrape (hard paywalls)."""
    now = datetime.utcnow()
    for domain, reason in SEED_SCRAPE_BLOCKLIST:
        if ScrapeBlocklist.query.filter_by(domain=domain).first() is not None:
            continue
        db.session.add(ScrapeBlocklist(
            domain=domain, reason=reason, added_at=now, is_permanent=True,
        ))
        counts["scrape_blocklist"] += 1


def _seed_prompts(counts):
    """LLM prompt bodies. Without these the pipeline produces no text at all.

    Seeds `default_text` and `current_text` to the same value, matching the
    migration: `default_text` is the immutable "reset to default" target and
    `current_text` is what renders. An existing row is never touched, so an
    edited prompt survives.
    """
    for key, description, text in SEED_PROMPTS:
        if PromptTemplate.query.filter_by(key=key).first() is not None:
            continue
        db.session.add(PromptTemplate(
            key=key, description=description,
            default_text=text, current_text=text, updated_at=None,
        ))
        counts["prompts"] += 1


def seed_defaults():
    """Insert any missing default config rows. Idempotent; never updates a row.

    Returns a dict of how many rows were inserted per table, so a caller can log
    whether this was a fresh seed or a no-op. Safe to call on every startup.
    """
    counts = {
        "topics": 0,
        "topics_backfilled": 0,
        "scheduled_fetches": 0,
        "pipeline_schedule": 0,
        "rss_feeds": 0,
        "ingestion_blocks": 0,
        "scrape_blocklist": 0,
        "prompts": 0,
    }

    _seed_topics(counts)
    _seed_scheduled_fetches(counts)
    _seed_pipeline_schedule(counts)
    _seed_rss_feeds(counts)
    _seed_ingestion_blocks(counts)
    _seed_scrape_blocklist(counts)
    _seed_prompts(counts)

    db.session.commit()

    inserted = sum(v for k, v in counts.items() if k != "topics_backfilled")
    if inserted:
        logger.info(
            "Seeded default config: "
            + ", ".join(f"{k}={v}" for k, v in counts.items() if v)
        )
    else:
        logger.info("Default config already present, nothing seeded.")
    return counts

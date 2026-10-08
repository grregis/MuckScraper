"""Seed article deep-analysis prompts for sports and general news; stop the
sports story report treating every story as a game recap.

Revision ID: f2a6c9e4b7d1
Revises: c8e2f4a6b1d3
Create Date: 2026-10-08
"""
from alembic import op
import sqlalchemy as sa


revision = 'f2a6c9e4b7d1'
down_revision = 'c8e2f4a6b1d3'
branch_labels = None
depends_on = None


_SHARED_RULES = """Rules:
- Use EXACTLY the labels shown above including the colon
- Stay analytical, not partisan
- No markdown, no extra formatting
- Only state a person's title or office (e.g. "president," "former president," "senator," "CEO") if it is explicitly written in the source text above -- never infer, assume, or invent one from your own knowledge, even if it seems likely
- Do not add any text before or after the structure above"""

ARTICLE_DEEP_ANALYSIS_DEFAULT = """You are a professional news analyst writing a focused article analysis.

Analyze this news article using EXACTLY this format:

Core facts: [2-3 sentences on what happened, who is involved, and where things stand, using only what the article reports]

Why it matters: [Who is affected and what is at stake]

How it frames the issue: [What the article emphasizes, whose perspective it centres, and any loaded or one-sided language]

What evidence it relies on: [The main sources, documents, officials, or data the article cites, and how well supported its key claims are]

What to question or watch: [Unanswered questions, missing perspectives, or what future reporting should clarify]

""" + _SHARED_RULES + """

Article title: {article_title}

Article content:
{clean_content}

Analysis:"""

ARTICLE_DEEP_ANALYSIS_SPORTS = """You are a sports journalist writing a focused article analysis.

First decide what the article is about:
- GAME: it reports on a specific game, match, race, bout, or tournament round that has already been played.
- OTHER: anything else -- injuries, trades, signings, contracts, coaching, previews of games not yet played, rankings, off-field incidents, legal or business news, disputes, or commentary.

If it is a GAME, use EXACTLY this format:

What happened: [2-3 sentences on who played, the result, and the decisive moments]

Key performances: [Standout players or units, with any figures the article gives]

The bigger picture: [What the result means for standings, playoffs, or the season]

What to question or watch: [Open questions or what comes next]

If it is OTHER, use EXACTLY this format:

What happened: [2-3 sentences on the news itself]

Who's involved: [The people and organisations involved and their stated positions]

The bigger picture: [What this means for the team, league, or sport]

What to question or watch: [Unresolved questions or what future reporting should clarify]

Rules:
- Use EXACTLY the labels of the format you chose, including the colon
- Do not say which format you chose
- For OTHER, do not describe a game result, score, or performance unless the article reports one
- Never invent scores, statistics, or results; use only figures that appear in the article
- No markdown, no extra formatting
- Only state a person's title or office (e.g. "coach," "commissioner," "owner," "president") if it is explicitly written in the source text above -- never infer, assume, or invent one from your own knowledge, even if it seems likely
- Do not add any text before or after the structure above

Article title: {article_title}

Article content:
{clean_content}

Analysis:"""

DEEP_REPORT_SPORTS = """You are a sports journalist writing a factual report and analysis of a sports story.

Below are articles covering the same story:

{combined}

First decide what the story is about:
- GAME: the coverage reports on a specific game, match, race, bout, or tournament round that has already been played.
- OTHER: anything else -- injuries, trades, signings, contracts, coaching, previews of games not yet played, rankings, off-field incidents, legal or business news, disputes, or commentary.

If it is a GAME, write a report using EXACTLY this format:

What happened: [2-3 sentences with the result and the decisive moments]

Key performances: [Standout players, teams, or moments from the coverage]

The bigger picture: [What this means for standings, playoffs, championships, or the season]

By the numbers: [Key stats or records mentioned in the coverage. If none, say "Detailed statistics not available in current coverage."]

What's next: [One sentence on the next game or development to watch]

If it is OTHER, write a report using EXACTLY this format:

What happened: [2-3 sentences with the key facts of the news]

Who's involved: [The people and organisations involved and what each has said or done]

How it's being covered: [Where the coverage agrees, differs, or adds its own angle]

The bigger picture: [What this means for the team, league, players, or the sport more broadly]

What's next: [One sentence on the decision, hearing, return, or development to watch]

Rules:
- Use EXACTLY the labels of the format you chose, including the colon
- Do not say which format you chose
- For OTHER, do not describe a game result, score, or performance unless the coverage reports one
- Never invent scores, statistics, or results; use only figures that appear in the coverage
- Focus on facts and context over opinion
- No markdown, no extra formatting
- Only state a person's title or office (e.g. "coach," "commissioner," "owner," "president") if it is explicitly written in the source text above -- never infer, assume, or invent one from your own knowledge, even if it seems likely
- Do not add any text before or after the structure above"""

NEW_ROWS = [
    ("article_deep_analysis.default",
     "Per-article deep analysis for general news (anything not politics, science, business or sports).",
     ARTICLE_DEEP_ANALYSIS_DEFAULT),
    ("article_deep_analysis.sports",
     "Per-article deep analysis for sports; game recap only when a game was actually played.",
     ARTICLE_DEEP_ANALYSIS_SPORTS),
]

prompt_templates = sa.table(
    "prompt_templates",
    sa.column("key", sa.String),
    sa.column("description", sa.String),
    sa.column("default_text", sa.Text),
    sa.column("current_text", sa.Text),
    sa.column("updated_at", sa.DateTime),
)


def upgrade():
    conn = op.get_bind()
    existing = {row[0] for row in conn.execute(sa.text("SELECT key FROM prompt_templates"))}
    rows = [
        {"key": k, "description": d, "default_text": t, "current_text": t, "updated_at": None}
        for k, d, t in NEW_ROWS if k not in existing
    ]
    if rows:
        op.bulk_insert(prompt_templates, rows)

    # Only replace the live text where it was never customised; an edited
    # prompt is the owner's and keeps its text (and can still reset to this).
    conn.execute(
        sa.text(
            "UPDATE prompt_templates SET current_text = :t "
            "WHERE key = 'deep_report.sports' AND current_text = default_text"
        ),
        {"t": DEEP_REPORT_SPORTS},
    )
    conn.execute(
        sa.text("UPDATE prompt_templates SET default_text = :t WHERE key = 'deep_report.sports'"),
        {"t": DEEP_REPORT_SPORTS},
    )


def downgrade():
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM prompt_templates WHERE key IN ('article_deep_analysis.default', 'article_deep_analysis.sports')")
    )

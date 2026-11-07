"""Big stories: newest articles in full, every other original in brief (2026-11-03)."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from news_fetcher import summarizer, llm_client


def _article(i, side_score, summary="The big picture: Officials said things happened. Why it matters: It matters.",
             content=None, copy_of=None, title=None):
    outlet = SimpleNamespace(name=f"Outlet {i}", bias_score=side_score)
    return SimpleNamespace(
        id=i, title=title or f"Election result update number {i} for the vote count",
        content=content if content is not None else ("Election vote count result coalition parties seats " * 40),
        summary=summary, deep_analysis=None, bias_score=side_score, outlet=outlet, source=f"Outlet {i}",
        date=datetime(2026, 11, 1) + timedelta(hours=i), copy_of_id=copy_of)


def _story(n, copies=0):
    arts = [_article(i, [1.5, 3.0, 4.5][i % 3]) for i in range(1, n + 1)]
    arts += [_article(1000 + j, 3.0, copy_of=1) for j in range(copies)]
    return SimpleNamespace(id=1, title="Election result vote count", headline="Election result vote count",
                           articles=arts, topics=[], site_articles=arts)


def _prompt(fn, story, analysis_type="politics"):
    captured = {}
    with mock.patch.object(llm_client, "generate_text", side_effect=lambda p, **k: captured.setdefault("p", p) and None), \
         mock.patch.object(summarizer, "check_ollama_status", return_value=True), \
         mock.patch.object(summarizer, "detect_analysis_type", return_value=analysis_type), \
         mock.patch.object(summarizer, "render_prompt", side_effect=lambda key, **kw: "\n".join(f"{k}:\n{v}" for k, v in kw.items())), \
         mock.patch.object(summarizer, "original_articles_for", side_effect=lambda s: [a for a in s.articles if not a.copy_of_id]), \
         mock.patch("aggregator.article_copies.original_articles", side_effect=lambda arts: [a for a in arts if not a.copy_of_id]):
        fn(story)
    return captured.get("p", "")


def test_small_story_has_no_briefs():
    p = _prompt(summarizer.generate_deep_report, _story(12))
    assert "In brief" not in p and summarizer.BRIEF_NOTE not in p
    assert p.count("Excerpt:") == 12


def test_big_story_every_original_appears_and_copies_do_not():
    story = _story(60, copies=3)
    p = _prompt(summarizer.generate_deep_report, story)
    for a in story.articles:
        if a.copy_of_id:
            continue
        assert f": {a.title}" in p, a.id
    assert p.count("Excerpt:") <= summarizer.BIG_STORY_FULL_ARTICLES
    assert "In brief:" in p and summarizer.BRIEF_NOTE in p
    assert "Outlet 1000" not in p


def test_newest_get_full_text():
    story = _story(30)
    p = _prompt(summarizer.generate_deep_report, story)
    newest = max(story.articles, key=lambda a: a.date)
    oldest = min(story.articles, key=lambda a: a.date)
    assert "Excerpt:" in p.split(f": {newest.title}", 1)[1][:40]
    assert "In brief:" in p.split(f": {oldest.title}", 1)[1][:40]


def test_source_counts_cover_every_article():
    p = _prompt(summarizer.generate_deep_report, _story(45))
    counts = {k: int(v) for k, v in __import__("re").findall(r"- (Left-leaning|Center|Right-leaning) sources found: (\d+)", p)}
    assert sum(counts.values()) == 45


def test_brief_drops_summary_labels_and_huge_story_falls_back_to_titles():
    a = _article(1, 3.0)
    assert "The big picture" not in summarizer.article_brief(a)
    arts = [_article(i, 3.0) for i in range(1, 400)]
    limits = summarizer.brief_limits(arts, summarizer.DEEP_REPORT_BRIEF_BUDGET)
    assert set(limits.values()) == {0}


def test_prompt_stays_inside_context_for_a_huge_story():
    p = _prompt(summarizer.generate_deep_report, _story(140))
    assert len(p) < 60000


def test_story_summary_gets_briefs_on_big_story():
    p = _prompt(summarizer.summarize_story, _story(40))
    assert "In brief:" in p and p.count("Content:") <= summarizer.STORY_SUMMARY_MAX_FULL

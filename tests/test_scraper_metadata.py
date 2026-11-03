import json
from news_fetcher.scraper import extract_structured_metadata


def test_yoast_graph_ignores_organization_and_person_descriptions():
    """Inside @graph, Organization and Person descriptions must not outcompete the article."""
    org_desc = (
        "Global News Network is an international news broadcasting corporation "
        "dedicated to delivering comprehensive 24-hour news coverage, investigative "
        "journalism, and high quality media reporting across digital and broadcast platforms worldwide."
    )
    person_bio = (
        "Jane Doe is a veteran investigative journalist and senior political correspondent "
        "who has reported from over thirty countries, authored multiple bestselling books, "
        "and received prestigious awards for investigative reporting and documentary filmmaking."
    )
    article_text = (
        "City council leaders voted unanimously late Tuesday evening to approve a new municipal transit "
        "infrastructure expansion project aimed at reducing traffic congestion across the downtown corridor."
    )

    payload = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "@id": "https://example.com/#organization",
                "name": "Global News Network",
                "description": org_desc,
            },
            {
                "@type": "Person",
                "@id": "https://example.com/#author",
                "name": "Jane Doe",
                "description": person_bio,
            },
            {
                "@type": "NewsArticle",
                "@id": "https://example.com/#article",
                "headline": "Council Approves Transit Plan",
                "articleBody": article_text,
            },
        ],
    }

    html = f'<html><head><script type="application/ld+json">{json.dumps(payload)}</script></head><body></body></html>'
    result = extract_structured_metadata(html, "https://example.com/transit-plan")

    assert result is not None
    assert "Council Approves Transit Plan" in result
    assert article_text in result
    assert org_desc not in result
    assert person_bio not in result


def test_article_body_as_list():
    """articleBody provided as a list of strings should be preserved as separate paragraphs."""
    para1 = (
        "The state environmental agency released its annual water quality report on Monday morning, "
        "finding notable improvements across major river basins in the northern region."
    )
    para2 = (
        "Officials credited updated wastewater treatment standards and community conservation initiatives "
        "for the positive environmental recovery over the past five years."
    )

    payload = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": "Water Quality Shows Improvement",
        "articleBody": [para1, para2],
    }

    html = f'<html><head><script type="application/ld+json">{json.dumps(payload)}</script></head><body></body></html>'
    result = extract_structured_metadata(html, "https://example.com/water-report")

    assert result is not None
    assert f"<p>{para1}</p>" in result
    assert f"<p>{para2}</p>" in result


def test_multi_paragraph_article_body():
    """Multi-paragraph articleBody with internal newlines and spaces should split into <p> tags with collapsed whitespace."""
    raw_body = (
        "Federal regulators announced updated guidelines on Tuesday regarding aviation safety protocols.\n"
        "   The new measures will require   additional training for flight crew members.\n\n"
        "Industry representatives expressed general support for the timetable,\n"
        "noting that airline compliance testing is already underway across several major hubs."
    )

    payload = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": "Aviation Safety Guidelines",
        "articleBody": raw_body,
    }

    html = f'<html><head><script type="application/ld+json">{json.dumps(payload)}</script></head><body></body></html>'
    result = extract_structured_metadata(html, "https://example.com/aviation-safety")

    assert result is not None
    expected_p1 = (
        "Federal regulators announced updated guidelines on Tuesday regarding aviation safety protocols. "
        "The new measures will require additional training for flight crew members."
    )
    expected_p2 = (
        "Industry representatives expressed general support for the timetable, "
        "noting that airline compliance testing is already underway across several major hubs."
    )
    assert f"<p>{expected_p1}</p>" in result
    assert f"<p>{expected_p2}</p>" in result


def test_top_level_non_article_type():
    """Top-level JSON-LD items that are not typed as an article should still have their text extracted."""
    top_level_text = (
        "Quarterback Marcus Vance led the fourth-quarter comeback drive with two touchdown passes "
        "in the final three minutes to secure a thrilling victory in Sunday's championship semifinal game."
    )

    payload = {
        "@context": "https://schema.org",
        "@type": "SportsEvent",  # Not an article type
        "headline": "Dramatic Fourth-Quarter Comeback Seals Victory",
        "articleBody": top_level_text,
    }

    html = f'<html><head><script type="application/ld+json">{json.dumps(payload)}</script></head><body></body></html>'
    result = extract_structured_metadata(html, "https://example.com/sports-event")

    assert result is not None
    assert "Dramatic Fourth-Quarter Comeback Seals Victory" in result
    assert top_level_text in result

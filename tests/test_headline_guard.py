import pytest

from news_fetcher.headline_generator import _looks_like_llm_failure as bad


@pytest.mark.parametrize("text", [
    "(Error: Input transcripts are missing. Please provide the content to generate a headline.)",
    "[No headline could be generated]",
    "Error: no articles provided",
    "Sorry, I can't write a headline for this",
    "I cannot generate a headline without content",
    "As an AI, I need the article text",
    "Please provide the article titles",
    "Fed holds rates\n\nNote: based on the titles above",
    # Echoes of the prompt's own figure example (story 97307, 2026-11-15)
    "The Mayflower extracts 1.7M stays",
    "Budget keeps its comma at 23,000",
    "",
    None,
])
def test_rejects_llm_failure_text(text):
    assert bad(text)


@pytest.mark.parametrize("text", [
    "Missing Carnival Panorama passenger jumps overboard off Mexico coast",
    "Error at Boeing plant delays 737 deliveries",
    "Unable to reach deal, Senate talks stall",
    "Sorry state of NFL officiating draws fan anger",
    "(Updated) Fed raises rates by a quarter point",
    "Pittman's Absence Provides Silver Lining For Steelers",
    "Face the Nation transcripts, Sept. 20, 2026",
    "Jobless claims stay at 1.7M for third week",
    "Hotel stays rise to 1.7 million as travel rebounds",
])
def test_keeps_real_headlines(text):
    assert not bad(text)


# Quote stripping (2026-10-05): a plain strip('"\'') removed the closing quote
# of headlines ending on a quotation (19 stored headlines, e.g. 76440).
from news_fetcher.headline_generator import _strip_wrapping_quotes as unquote


@pytest.mark.parametrize("raw, expected", [
    ('"Fed holds rates steady"', "Fed holds rates steady"),
    ("'Fed holds rates steady'", "Fed holds rates steady"),
    ("“Fed holds rates steady”", "Fed holds rates steady"),
    ('  "Fed holds rates steady"  ', "Fed holds rates steady"),
    ("\"'Fed holds rates steady'\"", "Fed holds rates steady"),
    ('"Trump\'s tariffs hit farmers"', "Trump's tariffs hit farmers"),
    ("'Trump's tariffs hit farmers'", "Trump's tariffs hit farmers"),
    ('"Fed holds rates steady', "Fed holds rates steady"),
    ('Fed holds rates steady"', "Fed holds rates steady"),
])
def test_strips_quotes_wrapping_the_whole_headline(raw, expected):
    assert unquote(raw) == expected


@pytest.mark.parametrize("text", [
    # Real stored headlines that lost their closing quote to the old strip.
    'Cornell president calls alleged gang rape "deeply disturbing"',
    'Trump posts map renaming New Mexico "New America"',
    "Bird says WNBA players urged to 'shut up'",
    "Ben Jones dies at 84 after acting in 'The Dukes of Hazzard'",
    # Quotes at both ends that belong to two different quotations.
    '"Shut up," Bird says, calling critics "liars"',
    "'Shut up,' Bird says, calling critics 'liars'",
    "“Shut up,” Bird says, calling critics “liars”",
    # A leading quotation must keep its opening quote.
    "'Shut up,' Bird tells WNBA critics",
    '"No Kings" protests draw millions',
    "Players' union rejects offer",
    "Fed holds rates steady",
])
def test_keeps_quotes_that_are_part_of_the_headline(text):
    assert unquote(text) == text


# Dated title list (2026-11-16): the prompt can use {dated_titles}, newest
# first, each title prefixed with its publication date.
from datetime import datetime
from types import SimpleNamespace

from news_fetcher.headline_generator import headline_title_lists


def test_title_lists_newest_first_with_dates():
    articles = [
        SimpleNamespace(title="Trump appoints new chief of staff", date=datetime(2026, 11, 9, 17, 4)),
        SimpleNamespace(title="Inside Miller's first week", date=datetime(2026, 11, 14, 17, 5)),
        SimpleNamespace(title="Undated wire copy", date=None),
    ]
    titles, dated = headline_title_lists(articles)
    assert titles.splitlines() == [
        "- Inside Miller's first week",
        "- Trump appoints new chief of staff",
        "- Undated wire copy",
    ]
    assert dated.splitlines() == [
        "- (Nov 14) Inside Miller's first week",
        "- (Nov 9) Trump appoints new chief of staff",
        "- Undated wire copy",
    ]


def test_title_lists_keep_the_ten_newest():
    articles = [SimpleNamespace(title=f"t{i}", date=datetime(2026, 11, 1 + i)) for i in range(12)]
    titles, dated = headline_title_lists(articles)
    assert len(titles.splitlines()) == 10 and len(dated.splitlines()) == 10
    assert titles.splitlines()[0] == "- t11"

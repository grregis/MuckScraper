import pytest

from news_fetcher.summarizer import _story_summary_rejection as rejection

GOOD = (
    "Justice Samuel Alito has recused himself from a major climate change case "
    "currently before the Supreme Court, Suncor Energy v. Boulder County. The "
    "dispute will determine whether state-law claims can compel fossil fuel "
    "companies to pay billions in damages. Alito's recusal introduces uncertainty."
)


@pytest.mark.parametrize("text", [
    # Story 74001, published on edition 466 (2026-09-29).
    "The Republic of Ireland secured a 3-0 victory over Israel in a highly politically",
    "Short but complete sentence.",
    GOOD[:-60],
])
def test_rejects_cut_off_summary(text):
    assert rejection(text)


@pytest.mark.parametrize("text", [
    GOOD,
    GOOD[:-1] + ', officials said."',
    GOOD[:-1] + " (per the court's clerk).",
])
def test_accepts_complete_summary(text):
    assert rejection(text) is None


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_is_left_to_the_existing_none_path(text):
    assert rejection(text) is None

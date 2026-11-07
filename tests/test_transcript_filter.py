"""Show/interview transcripts are dropped at ingestion; news ABOUT transcripts
is kept (2026-10-06). Every case is a real stored title."""
import pytest

from aggregator.article_signals import low_value_article_reason as reason

URL = "https://example.com/news/story"


@pytest.mark.parametrize("title", [
    "60 Minutes Transcript: Sen. Thom Tillis",
    "60 Minutes Transcript: Artificial intelligence and the future of work",
    'Transcript: Sen. Mark Kelly on "Face the Nation with Margaret Brennan," Oct. 4, 2026',
    'Full transcript of "Face the Nation with Margaret Brennan," Oct. 4, 2026',
    'Full transcript: Cindy McCain on "Face the Nation with Margaret Brennan," May 31, 2026',
    'Read full episode transcripts of "Face the Nation with Margaret Brennan" from 2026',
    "Read the full transcript of 60 Minutes' interview with President Trump",
    'Read the transcript of Trump\'s interview with "The Axios Show"',
    "Read the transcript: President Donald Trump interviewed by NBC News' 'Meet the Press' moderator Kristen Welker",
    "A Transcript From The Times Interview with Prime Minister Mark Carney of Canada",
    "Transcript: The Mistakes That The Democrats Keep Making",
    "Video + Transcript: Everything Mike Norvell had to say about Florida State's first preseason scrimmage",
    "Alphabet Inc. (GOOGL) Q1 2026 Earnings Call Transcript - Seeking Alpha",
    "The Tim Ferriss Show Transcripts: Tim's Founder Kitchen",
])
def test_transcripts_are_low_value(title):
    assert reason(title, URL) is not None


@pytest.mark.parametrize("title", [
    "Sen. Tillis worries about the \"outsized influence\" of the executive branch",
    "Transcript shows campus police's interview with Jane Doe in Cornell rape case",
    'Man accused in Cornell case allegedly asked student not to "say anything," transcript says',
    "DA in Cornell case says he never saw interview transcript",
    "Oversight panel releases transcript of Bondi interview about Epstein files",
    "Read: House Oversight's transcript of Bondi interview about Epstein files",
    "5 takeaways from Pam Bondi's House Oversight Committee transcript in Epstein probe",
    "House Republicans drop Jack Smith transcripts in explosive Arctic Frost records fight",
    "Judge rejects Biden's attempt to halt release of special counsel inquiry transcripts",
    "The Transcripts of OpenAI Models Plotting Together to Commit an Actual Crime Is Pretty Chilling",
    "University of Michigan plans to shield 2027 freshman letter grades from transcripts",
])
def test_news_about_transcripts_is_kept(title):
    assert reason(title, URL) is None

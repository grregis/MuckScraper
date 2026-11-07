"""news_fetcher/copy_detector.py and aggregator/article_copies.py (2026-10-11):
an article that is mostly another article's text (a republished piece, or
the same wire copy) is marked as a copy of the original."""
import unittest
from datetime import datetime
from types import SimpleNamespace

from aggregator.article_copies import copy_label, is_copy_in_story, original_articles
from news_fetcher.copy_detector import containment, find_copies, plain_text, shingles

BASE = (
    "Comedian Bill Maher warned the Democratic Party has not yet renounced their crazy on Thursday "
    "and that it could spell trouble for the party in 2028, but maintained that Republicans were "
    "likely to lose control of Congress in November. During an interview on the podcast, the host "
    "asked Maher if Republicans taking a beating in the midterm elections would mean the Democratic "
    "Party successfully made the case to the American people. Absolutely not, Maher said. They have "
    "not renounced their crazy at all. In fact they are going full steam ahead, which is why he is so "
    "worried about the next presidential election and what the far left will make of a loss. "
)
OTHER = (
    "The Food and Drug Administration said its inspectors detected a parasite at the farm blamed for "
    "the largest outbreak of its kind, though officials said it remained unclear how the parasite got "
    "into the lettuce. Inspectors found it in a wastewater tank and a drainage ditch used for irrigation, "
    "and the company said it had stopped shipping from the site while the investigation continues. "
    "State health departments have reported more than four hundred cases across eleven states so far. "
)


def item(id_, outlet, text, day, name=None):
    return {"id": id_, "outlet": outlet, "name": name or outlet, "text": text, "date": datetime(2026, 10, day)}


class FindCopiesTests(unittest.TestCase):
    def test_republished_copy_points_to_credited_original(self):
        fox = item(1, "Fox News", BASE + "Fox News Digital reached out for comment.", 10)
        # The Post's copy is earlier here on purpose: the credit decides.
        post = item(2, "New York Post", BASE.replace("Thursday", "Thursday night") + "Fox News Digital reached out.", 9)
        copies = find_copies([fox, post])
        self.assertEqual(list(copies), [2])
        self.assertEqual(copies[2][0], 1)
        self.assertGreater(copies[2][1], 0.8)

    def test_wire_copy_without_credit_keeps_the_earliest(self):
        npr = item(1, "NPR", BASE, 10)
        cbs = item(2, "CBS News", BASE + " More details were not immediately available.", 9)
        self.assertEqual({k: v[0] for k, v in find_copies([npr, cbs]).items()}, {1: 2})

    def test_three_copies_share_one_original(self):
        a = item(1, "AP", BASE, 8)
        b = item(2, "NPR", BASE + " NPR contributed.", 9)
        c = item(3, "CBS News", BASE + " CBS contributed.", 10)
        self.assertEqual({k: v[0] for k, v in find_copies([b, c, a]).items()}, {2: 1, 3: 1})

    def test_different_reporting_is_not_a_copy(self):
        self.assertEqual(find_copies([item(1, "Fox News", BASE, 9), item(2, "NPR", OTHER * 2, 9)]), {})

    def test_same_outlet_is_never_paired(self):
        self.assertEqual(find_copies([item(1, "Fox News", BASE, 9), item(2, "Fox News", BASE, 10)]), {})

    def test_short_texts_are_ignored(self):
        self.assertEqual(find_copies([item(1, "Fox News", BASE[:300], 9), item(2, "NPR", BASE[:300], 10)]), {})

    def test_shared_quote_below_threshold(self):
        # Half of each text is its own: under 50% shared stays separate.
        a = item(1, "Fox News", BASE[:350] + OTHER, 9)
        b = item(2, "NPR", BASE[:350] + OTHER[::-1], 10)
        self.assertEqual(find_copies([a, b]), {})


class TextTests(unittest.TestCase):
    def test_plain_text_and_containment(self):
        self.assertEqual(plain_text("<p>A&amp;B</p>\n<p>c</p>"), "A&B c")
        self.assertEqual(containment(shingles(BASE), shingles(BASE)), 1.0)
        self.assertEqual(containment(set(), shingles(BASE)), 0.0)
        # Curly and straight apostrophes shingle the same.
        self.assertEqual(shingles("they haven’t " + BASE), shingles("they haven't " + BASE))


class ArticleCopiesTests(unittest.TestCase):
    def setUp(self):
        self.fox = SimpleNamespace(id=1, story_id=7, copy_of_id=None, copy_of=None,
                                   outlet=SimpleNamespace(name="Fox News"), source="Fox News")
        self.post = SimpleNamespace(id=2, story_id=7, copy_of_id=1, copy_of=self.fox,
                                    outlet=SimpleNamespace(name="New York Post"), source="New York Post")

    def test_original_articles(self):
        self.assertEqual(original_articles([self.fox, self.post]), [self.fox])
        self.assertTrue(is_copy_in_story(self.post))
        self.assertEqual(copy_label(self.post), "Same report as Fox News")
        self.assertIsNone(copy_label(self.fox))

    def test_original_moved_away_makes_the_copy_count_again(self):
        self.fox.story_id = 8
        self.assertEqual(original_articles([self.post]), [self.post])
        self.assertIsNone(copy_label(self.post))


if __name__ == "__main__":
    unittest.main()

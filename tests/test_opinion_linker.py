"""Pure-function tests for news_fetcher/opinion_linker.py (2026-10-10)."""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from news_fetcher.opinion_linker import (
    choose_columns,
    format_columns,
    is_letters_page,
    parse_link_answer,
    parse_roundup,
    roundup_rejection,
)


def piece(pid, bias, full=False, hours_ago=0, source="Outlet", title="Title", content="<p>Teaser.</p>"):
    when = datetime(2026, 10, 10, 12) - timedelta(hours=hours_ago)
    return SimpleNamespace(id=pid, bias_score=bias, full_text="x" * 2000 if full else None,
                           date=when, fetched_at=when, source=source, title=title, content=content)


class LettersTests(unittest.TestCase):
    def test_letters_by_title(self):
        self.assertTrue(is_letters_page("Letters to the Editor: Trump suggested ...", "https://x.com/a", ""))
        self.assertTrue(is_letters_page("Mamdani's disgraceful Oct. 7 remarks: Letters to the Editor — Oct. 9", "", ""))

    def test_letters_by_url(self):
        self.assertTrue(is_letters_page("A Reckoning at Cornell", "https://www.nytimes.com/2026/10/06/opinion/letters/cornell.html", ""))
        self.assertTrue(is_letters_page("x", "https://nypost.com/2026/10/09/opinion/cornell-7-rape-allegations-letters/", ""))

    def test_letters_by_teaser(self):
        self.assertTrue(is_letters_page("The Trump Outrages Keep Piling Up", "", "Readers react to President Trump's remarks"))
        self.assertTrue(is_letters_page("x", "", "<p>NY Post readers discuss the former Cornell student"))
        self.assertTrue(is_letters_page("Prednisone is safe", "", "Readers on climate change and health."))

    def test_columns_are_not_letters(self):
        self.assertFalse(is_letters_page("Mamdani's Moral Obscenity", "https://www.nytimes.com/2026/10/08/opinion/mamdani.html",
                                         "A mayor for one million Jewish New Yorkers fails his duty."))
        # A column that mentions readers in passing, and a letter carrier.
        self.assertFalse(is_letters_page("Why readers deserve better", "https://x.com/opinion/readers", "Newspapers keep failing."))
        self.assertFalse(is_letters_page("The letter carrier who saved a town", "https://x.com/opinion/letter-carrier", ""))


class LinkAnswerTests(unittest.TestCase):
    def test_answers(self):
        self.assertTrue(parse_link_answer("YES"))
        self.assertTrue(parse_link_answer(" yes."))
        self.assertFalse(parse_link_answer("NO"))
        self.assertFalse(parse_link_answer(""))
        self.assertFalse(parse_link_answer(None))


class ChooseColumnsTests(unittest.TestCase):
    def test_takes_turns_across_sides(self):
        pieces = [piece(i, 5.0, hours_ago=i) for i in range(1, 6)] + [piece(10, 1.5)]
        chosen = choose_columns(pieces, limit=3)
        self.assertIn(10, [p.id for p in chosen])
        self.assertEqual(len(chosen), 3)

    def test_full_text_first_then_newest(self):
        pieces = [piece(1, 5.0, hours_ago=1), piece(2, 5.0, full=True, hours_ago=5), piece(3, 5.0, hours_ago=0)]
        self.assertEqual([p.id for p in choose_columns(pieces, limit=2)], [2, 3])

    def test_fewer_than_limit(self):
        self.assertEqual(len(choose_columns([piece(1, 1.0), piece(2, None)])), 2)


class FormatColumnsTests(unittest.TestCase):
    def test_headline_only_marked(self):
        text = format_columns([piece(1, 1.0, title="A"), piece(2, 5.0, full=True, title="B")])
        self.assertIn('[1] Outlet: "A" (HEADLINE ONLY)\nTeaser: Teaser.', text)
        self.assertIn('[2] Outlet: "B"\nxxx', text)


class ParseRoundupTests(unittest.TestCase):
    OUTPUT = """1: ON | Trump is a dangerous leader
2: OFF | About UK bomber bases
**3: ON** | Critics are making things up
ROUNDUP:
A Guardian column argues Trump is dangerous.
A Washington Post column says critics are making things up."""

    def test_parse(self):
        verdicts, paragraph = parse_roundup(self.OUTPUT, 3)
        self.assertEqual(verdicts[1], (True, "Trump is a dangerous leader"))
        self.assertEqual(verdicts[2], (False, "About UK bomber bases"))
        self.assertEqual(verdicts[3][0], True)
        self.assertEqual(paragraph, "A Guardian column argues Trump is dangerous. "
                                    "A Washington Post column says critics are making things up.")

    def test_paragraph_on_roundup_line_and_unknown_numbers(self):
        verdicts, paragraph = parse_roundup("1: ON | x\n7: OFF | y\nROUNDUP: One. Two.", 2)
        self.assertEqual(set(verdicts), {1})
        self.assertEqual(paragraph, "One. Two.")

    def test_no_marker_means_no_paragraph(self):
        self.assertEqual(parse_roundup("1: ON | x\nSome text.", 1)[1], "")
        self.assertEqual(parse_roundup(None, 1), ({}, ""))


class RoundupRejectionTests(unittest.TestCase):
    GOOD = ("A Fox News column argues the speech was a needed defense of the West, while a National Review "
            "column praises its aim but faults its history. A Washington Examiner column disputes that the "
            "traits Rubio named are uniquely Western.")

    def test_accepts_a_real_roundup(self):
        self.assertIsNone(roundup_rejection(self.GOOD))

    def test_rejects(self):
        self.assertEqual(roundup_rejection(""), "empty")
        self.assertTrue(roundup_rejection("Too short."))
        self.assertTrue(roundup_rejection(self.GOOD.rstrip(".") + " and the"))
        self.assertTrue(roundup_rejection("I cannot write a roundup because " + "the columns are missing " * 8 + "."))




class ParseStanceTests(unittest.TestCase):
    """opinion_stance answers (describe_linked_columns, 2026-10-17)."""

    def test_plain_phrase(self):
        from news_fetcher.opinion_linker import parse_stance
        self.assertEqual(parse_stance("Argues the raid was political"), "Argues the raid was political")
        self.assertEqual(parse_stance("\n**Says the deal funds Putin**\nMore text"), "Says the deal funds Putin")

    def test_drops_labels_but_not_the_word_on(self):
        from news_fetcher.opinion_linker import parse_stance
        self.assertEqual(parse_stance("ON | Backs the tariffs"), "Backs the tariffs")
        self.assertEqual(parse_stance("Position: Backs the tariffs"), "Backs the tariffs")
        self.assertEqual(parse_stance("On immigration, argues for limits"), "On immigration, argues for limits")

    def test_not_an_answer(self):
        from news_fetcher.opinion_linker import parse_stance
        for output in (None, "", "  \n ", "ON |", "I cannot determine the column's position"):
            self.assertIsNone(parse_stance(output), output)

    def test_strips_quotes_and_caps_length(self):
        from news_fetcher.opinion_linker import parse_stance, STANCE_MAX_CHARS
        self.assertEqual(parse_stance('"Backs the tariffs"'), "Backs the tariffs")
        self.assertEqual(len(parse_stance("x" * 500)), STANCE_MAX_CHARS)


if __name__ == "__main__":
    unittest.main()

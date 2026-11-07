"""The Op/Ed piece page's paragraph splitting (aggregator/opinion.py)."""
import unittest

from aggregator.opinion import reading_paragraphs


class ReadingParagraphsTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(reading_paragraphs(None), [])
        self.assertEqual(reading_paragraphs("   "), [])

    def test_groups_sentences(self):
        text = "One. Two! Three? Four. Five."
        self.assertEqual(reading_paragraphs(text, 2), ["One. Two!", "Three? Four.", "Five."])

    def test_abbreviations_and_figures_do_not_split(self):
        text = "Mr. Smith told Sen. Paul it was 3.5 percent in the U.S. Economy. Then he left."
        self.assertEqual(reading_paragraphs(text, 1),
                         ["Mr. Smith told Sen. Paul it was 3.5 percent in the U.S. Economy.", "Then he left."])

    def test_quoted_sentence_end(self):
        text = '"We are done." Zelensky said so. Later.'
        self.assertEqual(reading_paragraphs(text, 1), ['"We are done."', "Zelensky said so.", "Later."])

    def test_keeps_existing_line_breaks(self):
        self.assertEqual(reading_paragraphs("First para.\n\nSecond para.\nThird."),
                         ["First para.", "Second para.", "Third."])


if __name__ == "__main__":
    unittest.main()

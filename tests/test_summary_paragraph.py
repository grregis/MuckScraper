"""Article summaries shown as one paragraph (2026-10-18): the section
labels and bullet markers go, the text stays."""
import unittest

from aggregator.filters import summary_paragraph


class SummaryParagraphTests(unittest.TestCase):
    def test_smart_brevity_labels_are_dropped_and_joined(self):
        summary = (
            "The big picture: An alligator killed a man fishing on Lake Apopka.\n\n"
            "Why it matters: It is the state's second fatal attack this year.\n\n"
            "Quick analysis: Other alligators kept responders away.\n\n"
            "What's next: Authorities will keep monitoring the lake."
        )
        self.assertEqual(
            summary_paragraph(summary),
            "An alligator killed a man fishing on Lake Apopka. It is the state's second "
            "fatal attack this year. Other alligators kept responders away. Authorities "
            "will keep monitoring the lake.",
        )

    def test_older_format_with_bullets(self):
        summary = (
            "What's happening:\n"
            "• Defense Secretary Pete Hegseth ordered a review\n"
            "• The Pentagon will report in 30 days.\n"
        )
        self.assertEqual(
            summary_paragraph(summary),
            "Defense Secretary Pete Hegseth ordered a review. The Pentagon will report in 30 days.",
        )

    def test_bold_and_lowercase_labels(self):
        self.assertEqual(summary_paragraph("**Why it matters:** Prices rose."), "Prices rose.")
        self.assertEqual(summary_paragraph("the big picture: Rates held."), "Rates held.")

    def test_a_speaker_at_the_start_of_a_line_is_kept(self):
        self.assertEqual(
            summary_paragraph("Trump: \"We will win.\""),
            "Trump: \"We will win.\"",
        )

    def test_plain_paragraph_is_unchanged(self):
        text = "The Senate passed the bill 52-48 on Tuesday."
        self.assertEqual(summary_paragraph(text), text)

    def test_empty(self):
        self.assertEqual(summary_paragraph(None), "")
        self.assertEqual(summary_paragraph("  \n "), "")


if __name__ == "__main__":
    unittest.main()

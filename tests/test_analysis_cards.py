"""The story page's analysis layouts (Philip, 2026-10-23): the report is
split into its labelled sections (aggregator/story_analysis.py) and shown as
Cards (default) or Sections, picked under Appearance on the profile page.

The parser tests need nothing; the page tests run against the scratch
Postgres database named by API_TEST_DATABASE_URL, like tests/test_reads.py,
and are skipped without it.
"""
import os
import re
import unittest
from datetime import datetime
from unittest import mock

from aggregator import story_analysis

POLITICS = """The story: Riots spread to a dozen cities after a police shooting.

How the left is covering it: The Guardian leads with the bystander video.

How the center is covering it: AP and Reuters focus on the arrests.

How the right is covering it: No right-leaning sources were found in the current coverage.

What's contested: Whether the officer's account holds up.

What's missing: Residents' own voices.

What's next: Whether a state of emergency is declared."""

SCIENCE = """The discovery or development: A new battery chemistry.
It lasts twice as long.

Why it matters: Cheaper storage.

What experts are saying: Cautious optimism.

What's next: Field trials next year."""


class ParseTests(unittest.TestCase):
    def test_politics_sections(self):
        a = story_analysis.layout(POLITICS)
        self.assertEqual([s["kind"] for s in a["sections"]],
                         ["lead", "left", "center", "right", "other", "other", "next"])
        self.assertEqual(a["lead"]["label"], "The story")
        self.assertEqual([s["kind"] for s in a["buckets"]], ["left", "center"])
        self.assertEqual([s["kind"] for s in a["empty"]], ["right"])
        self.assertEqual([s["icon"] for s in a["others"]], ["scale", "eye-off"])
        self.assertEqual(a["next"]["icon"], "circle-arrow-right")

    def test_other_story_types_have_no_buckets(self):
        a = story_analysis.layout(SCIENCE)
        self.assertEqual(a["lead"]["paragraphs"], ["A new battery chemistry.", "It lasts twice as long."])
        self.assertEqual(a["buckets"], [])
        self.assertEqual([s["icon"] for s in a["others"]], ["zap", "message-square"])

    def test_unlabelled_text_still_renders(self):
        a = story_analysis.layout("Just one plain paragraph, no labels at all.")
        self.assertEqual(len(a["sections"]), 1)
        self.assertIsNone(a["sections"][0]["label"])
        self.assertEqual(story_analysis.layout("  \n ")["sections"], [])


def test_analysis_text_is_escaped():
    from pathlib import Path
    from types import SimpleNamespace
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1] / 'aggregator/templates'), autoescape=select_autoescape())
    template = env.from_string("{% from 'story_analysis.html' import story_analysis %}{{ story_analysis(a, story) }}")
    html = template.render(a=story_analysis.layout('What is next: <script>alert(1)</script>'), story=SimpleNamespace(left_bias_count=1, center_bias_count=1, right_bias_count=1))
    assert '<script>' not in html
    assert '&lt;script&gt;' in html

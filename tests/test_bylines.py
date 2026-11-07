import unittest

from news_fetcher.bylines import author_from_html, clean_byline, feed_entry_author


class CleanBylineTests(unittest.TestCase):
    def test_plain_and_by_prefix(self):
        self.assertEqual(clean_byline("Jane Doe"), "Jane Doe")
        self.assertEqual(clean_byline("By Jane Doe"), "Jane Doe")
        self.assertEqual(clean_byline("  by   Jane   Doe "), "Jane Doe")

    def test_two_authors_kept(self):
        self.assertEqual(clean_byline("By Jane Doe and John Roe"), "Jane Doe and John Roe")

    def test_rss_email_form(self):
        self.assertEqual(clean_byline("jdoe@example.com (Jane Doe)"), "Jane Doe")
        self.assertIsNone(clean_byline("newsroom@example.com"))

    def test_trailing_outlet(self):
        self.assertEqual(clean_byline("Jane Doe, CNN"), "Jane Doe")
        self.assertEqual(clean_byline("Jane Doe | Reuters"), "Jane Doe")
        self.assertEqual(clean_byline("Jane Doe - The Associated Press"), "Jane Doe")
        self.assertEqual(clean_byline("Jane Doe, Fox News", source_name="Fox News"), "Jane Doe")

    def test_place_agencies_and_role(self):
        self.assertEqual(clean_byline("Richard Luscombe in Miami"), "Richard Luscombe")
        self.assertEqual(clean_byline("Carlos Mureithi in Nairobi and agencies"), "Carlos Mureithi")
        self.assertEqual(clean_byline("Jane Doe in New York and John Roe in London"),
                         "Jane Doe and John Roe")
        self.assertEqual(clean_byline("Tom Phillips in Rio de Janeiro and Tiago Rogero"),
                         "Tom Phillips and Tiago Rogero")
        self.assertEqual(clean_byline("Sam Inman"), "Sam Inman")
        self.assertEqual(clean_byline("John Mac Ghlionn, opinion contributor"), "John Mac Ghlionn")
        self.assertEqual(clean_byline("Jane Doe, Senior White House Correspondent"), "Jane Doe")

    def test_hyphenated_name_survives(self):
        self.assertEqual(clean_byline("Mary Smith-Jones"), "Mary Smith-Jones")

    def test_not_a_person(self):
        for raw in ("Staff", "Reuters", "Associated Press", "https://example.com/people/jane",
                    "", None, "   ", "Fox News", "Al Jazeera Staff", "mliss1578"):
            self.assertIsNone(clean_byline(raw, source_name="Fox News"), raw)

    def test_another_outlets_name(self):
        self.assertIsNone(clean_byline("Washington Post", "Toronto Sun", {"washington post", "toronto sun"}))
        self.assertEqual(clean_byline("Jane Doe", "Toronto Sun", {"washington post"}), "Jane Doe")

    def test_html_and_length(self):
        self.assertEqual(clean_byline("<a href='/x'>Jane Doe</a>"), "Jane Doe")
        self.assertIsNone(clean_byline("A" * 300))


class FeedEntryTests(unittest.TestCase):
    def test_author_then_authors(self):
        self.assertEqual(feed_entry_author({"author": "Jane Doe"}), "Jane Doe")
        self.assertEqual(feed_entry_author({"authors": [{"name": "A B"}, {"name": "C D"}]}), "A B and C D")
        self.assertIsNone(feed_entry_author({}))


class AuthorFromHtmlTests(unittest.TestCase):
    def test_jsonld_list_and_graph(self):
        html = """<script type="application/ld+json">
        {"@graph": [{"@type": "WebPage"}, {"@type": "NewsArticle",
          "author": [{"@type": "Person", "name": "Jane Doe"}, {"name": "John Roe"}, {"name": "Ann Lee"}]}]}
        </script>"""
        self.assertEqual(author_from_html(html), "Jane Doe, John Roe and Ann Lee")

    def test_jsonld_organisation_only_falls_to_meta(self):
        html = """<script type="application/ld+json">{"author": {"@type": "Organization", "name": "Reuters"}}</script>
        <meta name="author" content="By Jane Doe">"""
        self.assertEqual(author_from_html(html), "Jane Doe")

    def test_meta_url_ignored(self):
        html = '<meta property="article:author" content="https://www.facebook.com/janedoe">'
        self.assertIsNone(author_from_html(html))

    def test_bad_json_and_empty(self):
        self.assertIsNone(author_from_html('<script type="application/ld+json">{oops</script>'))
        self.assertIsNone(author_from_html(None))


if __name__ == "__main__":
    unittest.main()

"""Editorial cartoons are refused everywhere (2026-10-09): not news, and not
kept as Op/Ed either. News about cartoons must still pass."""
import unittest

from aggregator.article_signals import is_cartoon, low_value_article_reason


class CartoonTests(unittest.TestCase):
    DROP = [
        ("Steve Nease cartoon, Sept. 10, 2026",
         "https://torontosun.com/opinion/cartoons/steve-nease-cartoon-sept-10-2026"),
        ("Cartoon by Matt Davies",
         "https://www.washingtonpost.com/opinions/2026/10/07/matt-davies-cartoon-oil-prices/"),
        ("The Russian plague",
         "https://www.washingtonpost.com/opinions/2026/10/08/michael-ramirez-cartoon-russia-plague-putin/"),
        ("Cartoon: America's gargantuan debt",
         "https://www.economist.com/the-world-this-week/2026/10/08/cartoon-americas-gargantuan-debt"),
        ("The weekly cartoon",
         "https://www.economist.com/the-world-this-week/2026/04/16/the-weekly-cartoon"),
        ("October", "https://www.politico.com/cartoons/2026/10/02/matt-wuerker-cartoons-october-2026-01104877"),
        ("Besties until the end - Daily Kos",
         "http://www.dailykos.com/stories/2026/8/30/800087726/cartoon/besties-until-the-end/"),
        ("Sports cartoons of the day", "https://www.foxnews.com/outkick-sports/sports-cartoons-day"),
    ]
    KEEP = [
        ("Legendary Toronto Sun political cartoonist Andy Donato passes away",
         "https://torontosun.com/news/local-news/toronto-sun-cartoonist-andy-donato-passes-away"),
        ("Hastie accepts apology from Joyce over 'traitor' cartoon as Hanson doubles down",
         "https://www.theguardian.com/australia-news/2026/sep/11/andrew-hastie-accepts-joyce-apology-traitor-cartoon"),
        ("Trump compares Dem Senate candidate to freckle-faced cartoon character",
         "https://www.foxnews.com/politics/trump-compares-dem-senate-candidate-freckle-faced-cartoon-character"),
        ("Dem senators accuse Belgian diamond group of 'cartoonish bribe' to Trump",
         "https://www.washingtontimes.com/news/2026/aug/12/dem-senators-accuse-belgian-diamond-group-cartoonish-bribe/"),
        ("Popcorn and cartoons: A Gaza cinema gives girls a brief escape from war",
         "https://www.aljazeera.com/features/2026/8/25/popcorn-and-cartoons-a-gaza-cinema"),
        # A "first-last-cartoon" slug outside an opinion path is not enough.
        ("Looney Tunes cartoon restored", "https://example.com/news/looney-tunes-cartoon-restored"),
    ]

    def test_drops_cartoons(self):
        for title, url in self.DROP:
            with self.subTest(title=title):
                self.assertTrue(is_cartoon(title, url))
                self.assertEqual(low_value_article_reason(title, url), "cartoon")

    def test_keeps_news_about_cartoons(self):
        for title, url in self.KEEP:
            with self.subTest(title=title):
                self.assertFalse(is_cartoon(title, url))

    def test_ordinary_column_is_still_opinion(self):
        self.assertEqual(low_value_article_reason(
            "Biden's legacy is being rewritten",
            "https://www.washingtonpost.com/opinions/2026/10/08/biden-legacy/"), "opinion")


if __name__ == "__main__":
    unittest.main()

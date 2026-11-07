"""Fantasy advice, "odds, predictions" picks columns and Seeking Alpha
contributor pieces are refused at ingestion (2026-10-08). Each rule has a
real near-miss that must still pass."""
import unittest

from aggregator.article_signals import is_betting_article, is_opinion_article, low_value_article_reason


class FantasyTests(unittest.TestCase):
    DROP = [
        "Fantasy Football Waiver Wire Advice: Players to Add, Stash & Drop (Week 5) - FantasyPros",
        "Sneaky Fantasy Football Pickups To Target For Week 5 - FantasyLife",
        "2026 Trade Value Charts — Justin Boone's fantasy football wide receiver breakdown for Week 5",
        "2026-27 Rotoworld Fantasy Basketball Draft Kit: Rankings, player profiles, expert analysis",
        "Week 4 Fantasy Waiver Wire: Add Ollie Gordon II, Braelon Allen - Sports Illustrated",
        "Fantasy football Start 'Em & Sit 'Em WR: Davante Adams resumes must-start status",
        "Top Week 1 Start 'Em, Sit 'Em Matchup Plays and Sleepers for Opening Games",
        "Week 2 Start ’Em, Sit ’Em Fantasy Football Stars to Play and Bench",
        "FAAB Waiver Wire Advice: Week 3 Fantasy Pickups (2026) - RotoBaller",
    ]
    KEEP = [
        "Colts and Taylor agree on two-year, $44M extension - NBC Sports",
        "2026 Baltimore Ravens waiver wire and practice squad tracker - Baltimore Beatdown",
        "Mike Vrabel gives intel into Patriots' waiver wire pickups - Pats Pulpit",
        "'It started as a joke, a fantasy' - F1 heads to football-obsessed Madrid",
        "Raiders star Brock Bowers undergoes meniscus trim surgery as fantasy managers fume",
        "Final Fantasy XIV patch 7.5 detailed",
    ]

    def test_drops_fantasy_advice(self):
        for title in self.DROP:
            with self.subTest(title=title):
                self.assertTrue(is_betting_article(title))
                self.assertEqual(low_value_article_reason(title, "https://example.com/x"), "betting")

    def test_keeps_news(self):
        for title in self.KEEP:
            with self.subTest(title=title):
                self.assertFalse(is_betting_article(title))


class OddsPredictionsTests(unittest.TestCase):
    def test_drops_odds_predictions(self):
        for title in [
            "College football odds, predictions: Alabama vs. Georgia, Oregon vs. UCLA lead Week 6 slate",
            "Argentina vs. England odds, prediction, time: 2026 World Cup semifinal picks",
            "Thunder at Lakers best bet, odds and prediction",
        ]:
            with self.subTest(title=title):
                self.assertTrue(is_betting_article(title))

    def test_keeps_odds_in_news(self):
        for title in [
            "Odds of a recession rise, economists say",
            "Pollsters' predictions miss as odds shift late",
        ]:
            with self.subTest(title=title):
                self.assertFalse(is_betting_article(title))


class CnnUnderscoredTests(unittest.TestCase):
    # Shopping reviews, refused since 2026-10-21.
    def test_underscored_review_is_refused(self):
        self.assertEqual(low_value_article_reason(
            "Is the iPhone Duo worth $1,999? I used it for a week",
            "https://www.cnn.com/cnn-underscored/reviews/iphone-duo"), "low_value_url")

    def test_edition_host_is_refused(self):
        self.assertEqual(low_value_article_reason(
            "The best noise-canceling headphones",
            "https://edition.cnn.com/cnn-underscored/electronics/best-headphones"), "low_value_url")

    def test_cnn_news_is_kept(self):
        self.assertIsNone(low_value_article_reason(
            "Chemical tank explodes in Toledo, Ohio",
            "https://www.cnn.com/2026/10/21/us/toledo-chemical-tank-explosion-ohio"))

    def test_cnn_tech_review_in_news_path_is_kept(self):
        # A review written as tech news, under the dated path, is not Underscored.
        self.assertIsNone(low_value_article_reason(
            "Apple's iPhone Duo review",
            "https://www.cnn.com/2026/10/21/tech/apple-iphone-duo-review"))


class SeekingAlphaTests(unittest.TestCase):
    # Refused outright since 2026-10-09: not news, and not kept as Op/Ed.
    def test_contributor_article_is_refused_not_opinion(self):
        url = "https://seekingalpha.com/article/4800000-why-i-am-buying-reits"
        title = "Why I Am Buying REITs Hand Over Fist"
        self.assertEqual(low_value_article_reason(title, url), "low_value_url")
        self.assertFalse(is_opinion_article(title, url))

    def test_www_host_is_refused(self):
        self.assertEqual(low_value_article_reason(
            "Micron: The Market Is In Disbelief",
            "https://www.seekingalpha.com/article/4800001-micron"), "low_value_url")

    def test_news_reason_is_none(self):
        self.assertIsNone(low_value_article_reason(
            "Constellation Energy's sweeping nuclear deal sparks rally",
            "https://seekingalpha.com/news/4500000-constellation-energy-deal"))

    def test_news_is_kept(self):
        self.assertFalse(is_opinion_article(
            "Constellation Energy's sweeping nuclear deal sparks rally",
            "https://seekingalpha.com/news/4500000-constellation-energy-deal"))

    def test_other_sites_article_path_kept(self):
        self.assertFalse(is_opinion_article(
            "Senate passes funding bill", "https://www.example.com/article/123-senate"))


class ColumnistPrefixTests(unittest.TestCase):
    # Breitbart's John Nolte columns, filed under news URLs (2026-10-30).
    def test_nolte_column_is_opinion(self):
        url = "https://www.breitbart.com/politics/2026/10/29/nolte-ny-times-admits-obama-doj-found-evidence/"
        title = "Nolte: NY Times Admits Obama DOJ Found Evidence of Ilhan Omar\u2019s Suspected \u2018Sham Marriage\u2019"
        self.assertTrue(is_opinion_article(title, url))
        self.assertEqual(low_value_article_reason(title, url), "opinion")

    def test_quote_attribution_stays_news(self):
        self.assertFalse(is_opinion_article(
            "Vance: Democrats Are Lying About the Shutdown",
            "https://www.breitbart.com/politics/2026/10/29/vance-democrats-lying/"))
        self.assertFalse(is_opinion_article(
            "Exclusive: GOP Leaders Plan Vote on Border Bill",
            "https://www.breitbart.com/politics/2026/10/29/exclusive-gop-border-bill/"))

    def test_nolte_on_another_outlet_stays_news(self):
        self.assertFalse(is_opinion_article(
            "Nolte: I Never Wanted to Leave Malibu",
            "https://variety.com/2026/film/news/nick-nolte-interview-123/"))

    def test_nolte_mid_title_stays_news(self):
        self.assertFalse(is_opinion_article(
            "Breitbart's Nolte: Media Ignored the Story",
            "https://www.example.com/news/nolte-media"))


if __name__ == "__main__":
    unittest.main()


class ExaminerOpEdTests(unittest.TestCase):
    def test_examiner_op_eds_path_is_opinion(self):
        self.assertTrue(is_opinion_article(
            title="Mao's 'magic weapon': Hegseth just exposed 80 rebranded Chinese cells on campus",
            url="https://www.washingtonexaminer.com/op-eds/4784917/confucius-institutes-national-security-threat/"))

    def test_examiner_news_is_kept(self):
        self.assertFalse(is_opinion_article(
            title="Democrats optimistic they can overcome split-ticket voters",
            url="https://www.washingtonexaminer.com/news/campaigns/congressional/4781935/democrats-optimistic-split-ticket/"))

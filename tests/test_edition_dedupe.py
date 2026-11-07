import unittest
from types import SimpleNamespace

try:
    from news_fetcher.fetch_and_store_articles import stories_look_duplicate_for_edition
except ModuleNotFoundError as exc:
    stories_look_duplicate_for_edition = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


def make_story(title, article_titles, outlet_names):
    articles = []
    for article_title, outlet_name in zip(article_titles, outlet_names):
        outlet = SimpleNamespace(name=outlet_name) if outlet_name else None
        articles.append(SimpleNamespace(title=article_title, outlet=outlet))
    return SimpleNamespace(title=title, headline=None, articles=articles)


class EditionDedupeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"Project dependencies are not installed: {IMPORT_ERROR}")

    def test_detects_philippine_arrest_standoff_split(self):
        story_a = make_story(
            "Philippine senator flees ICC arrest over role in drug war",
            [
                "Gunfire breaks out in Philippine Senate as police try to arrest senator",
                "Gunshots fired in Philippines senate in standoff with senator Ronald dela Rosa",
                "Shooting reported inside Philippines senate building where Ronald dela Rosa has been hiding",
            ],
            ["NPR", "The Guardian", "CBS News"],
        )
        story_b = make_story(
            "Gunshots ring out at Philippine Senate where lawmaker is holed up",
            [
                "Shots fired in Philippines Senate, where a wanted senator is holed up",
                "Gunfire breaks out in Philippine Senate where authorities tried to arrest a senator",
                "Chaos Erupts Inside Philippine Senate After Apparent Gunshots",
            ],
            ["CBS News", "PBS News", "New York Times"],
        )
        self.assertTrue(stories_look_duplicate_for_edition(story_a, story_b))

    def test_detects_utah_sentencing_split(self):
        story_a = make_story(
            "Sons of Kouri Richins say they'd feel unsafe if she wasn't in prison",
            [
                "Utah grief author sentenced to life in prison in husband’s poisoning death",
                "Children’s book author sentenced for husband’s poisoning death as judge calls her too dangerous",
            ],
            ["NBC News", "Fox News"],
        )
        story_b = make_story(
            "Mom sentenced to life without parole for fatally poisoning husband with fentanyl",
            [
                "Kouri Richins sentenced in husband's fentanyl-laced cocktail murder",
                "Utah mom sentenced to life without parole for husband's murder",
            ],
            ["CBS News", "NBC News"],
        )
        self.assertTrue(stories_look_duplicate_for_edition(story_a, story_b))

    def test_detects_inflation_report_split(self):
        story_a = make_story(
            "April inflation rate surges to 3.8% amid uncertainty in the Middle East",
            [
                "April inflation rate surges to 3.8% amid uncertainty in the Middle East",
                "Inflation spikes to 3.8 percent in April, higher than expected",
            ],
            ["NBC News", "The Hill"],
        )
        story_b = make_story(
            "CPI surged in April as inflation soars to highest level in almost 3 years",
            [
                "CPI surged in April as inflation soars to highest level in almost 3 years",
                "Inflation jumps to its highest level since 2023. Here are 3 things costing a lot more",
                "Inflation accelerated in April to highest rate since May 2023",
            ],
            ["CBS News", "NPR", "CBS News"],
        )
        self.assertTrue(stories_look_duplicate_for_edition(story_a, story_b))

    def test_detects_hegseth_hearing_split(self):
        story_a = make_story(
            "Watch live: Hegseth, Caine testify before House on Iran, defense budget",
            [
                "Lawmakers grill Hegseth over huge Pentagon budget request",
                "WATCH LIVE: Hegseth, Caine testify on Capitol Hill as Trump says Iran ceasefire is weak",
            ],
            ["CBS News", "PBS News"],
        )
        story_b = make_story(
            "Pete Hegseth faces Congress over Pentagon's unprecedented $1.5 trillion budget as Democrats vow to block it",
            [
                "Key House Republicans press Hegseth, express skepticism on Pentagon budget",
                "Watch: Pete Hegseth faces lawmakers' questions as he vies for $1.5 trillion defense budget",
            ],
            ["The Hill", "CBS News"],
        )
        self.assertTrue(stories_look_duplicate_for_edition(story_a, story_b))

    def test_detects_trump_china_visit_cluster(self):
        story_a = make_story(
            "Trump-Xi summit: China’s help in Iran may require US concessions",
            [
                "Trump seeks China's help on Iran during Beijing summit",
                "Trump-Xi summit live: US, China leaders holding talks on trade, tech, Iran",
            ],
            ["Reuters", "Reuters"],
        )
        story_b = make_story(
            "Scepticism and tight security as Beijing braces for Trump visit",
            [
                "Trump meets Xi as Beijing tightens security",
                "Trump arrives in Beijing for talks with Chinese President Xi Jinping",
            ],
            ["BBC News", "BBC News"],
        )
        self.assertTrue(stories_look_duplicate_for_edition(story_a, story_b))

    def test_does_not_collapse_broad_shared_topic(self):
        story_a = make_story(
            "Trump goes to China as Iran war smolders",
            ["Trump goes to China as Iran war smolders"],
            ["Reuters"],
        )
        story_b = make_story(
            "Overdose deaths fall for 3rd straight year, CDC data shows",
            ["Overdose deaths fall for 3rd straight year, CDC data shows"],
            ["CBS News"],
        )
        self.assertFalse(stories_look_duplicate_for_edition(story_a, story_b))

    def test_does_not_collapse_unrelated_senate_stories(self):
        story_a = make_story(
            "Time running out for Senate crypto deal",
            [
                "Senate Banking Committee debates crypto bill",
                "Senate crypto talks go down to the wire before key test",
                "Senate panel advances key crypto bill with bipartisan support",
            ],
            ["Politico", "Politico", "CNBC"],
        )
        story_b = make_story(
            "Senate advances Warsh, Trump’s pick to chair Federal Reserve",
            [
                "Senate confirms Warsh as Federal Reserve chair",
                "Kevin Warsh confirmed to US Federal Reserve board in close Senate vote",
            ],
            ["Reuters", "CBS News"],
        )
        self.assertFalse(stories_look_duplicate_for_edition(story_a, story_b))

    def test_does_not_collapse_unrelated_release_stories(self):
        story_a = make_story(
            "Colorado's Democratic governor will let Trump ally Tina Peters out of prison early",
            [
                "Colorado governor frees Trump ally Tina Peters from prison",
                "Election denier Tina Peters set for release after Colorado governor commutes sentence",
            ],
            ["AP", "CBS News"],
        )
        story_b = make_story(
            "ICE releases wife of U.S. Army soldier after month-long detention",
            [
                "Wife of US Army soldier released from ICE detention",
                "After a woman was released from ICE detention, her Army soldier husband speaks out",
            ],
            ["NBC News", "CBS News"],
        )
        self.assertFalse(stories_look_duplicate_for_edition(story_a, story_b))

    # Edition 475 (2026-09-30): both pairs were skipped as "same event" on
    # function/stock words (was/what/who; announce/announces/trump/watch).
    def test_does_not_collapse_on_function_words(self):
        story_a = make_story(
            "What did the fans say to A'ja Wilson? Reaction of Fever-Aces fan incident",
            [
                "WNBA commentator Sydney Colson physically restrained after going after Fever fan following A'ja Wilson mess",
                "What did the fans say to A'ja Wilson? Reaction of Fever-Aces fan incident",
                "Aces' A'ja Wilson gets tech for confrontation with Fever fan, who was determined to have broken no rules",
                "WNBA refs miss opportunity to eject A'ja Wilson over meltdown, entering stands",
                "Enes Kanter Freedom speaks out on A'ja Wilson playoff incident with Fever fan, after Natasha Cloud encounter",
            ],
            ["Fox News", "IndyStar", "Fox News", "Fox News", "Fox News"],
        )
        story_b = make_story(
            "DA reopens probe into 7 Cornell frat bros who allegedly drugged, gang raped student",
            [
                "Cornell fraternity rape investigation will be reopened, DA says",
                "DA reopens student\u2019s allegation she was raped by 7 men at Cornell in 2024",
                "Prosecutors are reexamining a Cornell fraternity rape case. Here's what we know",
                "DA reopens case after woman says she was sexually assaulted for hours by seven Cornell frat bros",
                "Prosecutor reopens probe into Cornell gang rape allegations after accuser files lawsuit",
            ],
            ["CBS News", "The Hill", "NPR", "Toronto Sun", "PBS News"],
        )
        self.assertFalse(stories_look_duplicate_for_edition(story_a, story_b))

    def test_does_not_collapse_separate_trump_announcements(self):
        story_a = make_story(
            "Watch live: Trump, Lutnick to announce major US energy investments",
            [
                "Trump puts Sullivan at center stage during Alaska pipeline reveal",
                "Trump set to announce $200bn in energy investments from South Korea",
                "Trump announces $200B in South Korean investments in the U.S.",
                "Trump unveils South Korean investment in Alaska gas pipeline",
                "South Korea to invest up to $200B in Alaska LNG pipeline, US power plants, Trump announces",
            ],
            ["The Hill", "Al Jazeera", "The Washington Times", "CBS News", "The Hill"],
        )
        story_b = make_story(
            "WATCH LIVE: Trump announces new steel plant project in Iowa",
            [
                "Watch live: Trump to announce $15B Iowa steel project",
                "Trump to announce new $15B steel plant: White House official",
                "Trump to announce massive steel plant in Iowa",
                "Trump announces $15bn steel mill project in Iowa before US midterms",
                "WATCH LIVE: Trump announces new steel plant project in Iowa",
            ],
            ["The Hill", "ABC News", "The Hill", "Al Jazeera", "PBS News"],
        )
        self.assertFalse(stories_look_duplicate_for_edition(story_a, story_b))

    def test_strips_own_outlet_suffix(self):
        story_a = make_story(
            "Fed measure of inflation dips to 3.4 percent in August - CNBC",
            ["Fed measure of inflation dips to 3.4 percent in August - CNBC"],
            ["CNBC"],
        )
        story_b = make_story(
            "Treasury to auto-enroll 60 million children in Trump Accounts - CNBC",
            ["Treasury to auto-enroll 60 million children in Trump Accounts - CNBC"],
            ["CNBC"],
        )
        self.assertFalse(stories_look_duplicate_for_edition(story_a, story_b))

    def test_does_not_collapse_on_him_life_behind(self):
        story_a = make_story(
            "Evan Gershkovich reflects on wrongful Russian detention and recovery",
            ["Evan Gershkovich reflects on arrest in Russia and reclaiming his life after release"],
            ["NPR"],
        )
        story_b = make_story(
            "Russian dissident Garry Kasparov says US warned him of assassination threat",
            [
                "Kremlin critic, chess legend Garry Kasparov says US warned him of assassination threat",
                "Russian dissident Garry Kasparov says US warned him of danger to his life",
            ],
            ["Fox News", "CBS News"],
        )
        self.assertFalse(stories_look_duplicate_for_edition(story_a, story_b))


    def test_does_not_collapse_on_names_alone(self):
        # Editions 683 and 684 (2026-10-15): two events in the same war shared
        # only russia/trump/ukraine, with embeddings at 0.807.
        story_a = make_story(
            "Ukraine resumes attacks on Russian oil refineries as relations with Trump sour",
            [
                "Ukraine resumes attacks on Russian oil refineries as relations with Trump sour",
                "Zelensky Says Ukraine Will Resume Russian Refinery Strikes, Defying Trump",
            ],
            ["PBS News", "New York Times"],
        )
        story_b = make_story(
            "Starlink restrictions hamper Ukraine front line operations as Russia escalates",
            [
                "Sudden Starlink Restrictions Hamper Ukraine Just as Russia Escalates",
                "Trump, Senate GOP see Starlink become latest flash point over Ukraine",
            ],
            ["New York Times", "The Hill"],
        )
        self.assertFalse(stories_look_duplicate_for_edition(story_a, story_b))

    def test_names_still_count_alongside_another_word(self):
        story_a = make_story(
            "Netanyahu delivers fiery UN speech attacking Israel's enemies and allies",
            ["Netanyahu delivers fiery UN speech attacking Israel's enemies and allies"],
            ["CBS News"],
        )
        story_b = make_story(
            "Fiery Netanyahu vows Iran's demise in heavily protested UN speech",
            ["Fiery Netanyahu vows Iran's demise in heavily protested UN speech"],
            ["New York Post"],
        )
        self.assertTrue(stories_look_duplicate_for_edition(story_a, story_b))


if __name__ == "__main__":
    unittest.main()


class EditionDedupeEmbeddingTests(unittest.TestCase):
    """A word match also needs the stories' article embeddings to agree."""

    @classmethod
    def setUpClass(cls):
        if IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"Project dependencies are not installed: {IMPORT_ERROR}")

    @staticmethod
    def _story(title, article_titles, vector, content_len=800):
        articles = [
            SimpleNamespace(title=t, outlet=SimpleNamespace(name="AP"), embedding=list(vector), content="x" * content_len)
            for t in article_titles
        ]
        return SimpleNamespace(title=title, headline=None, articles=articles)

    def _pair(self, va, vb, content_len=800):
        # Shares "arrests", "charged", "fbi" -- three real content words.
        a = self._story("FBI arrests man charged with stalking podcaster",
                        ["FBI arrests man charged with stalking podcaster"], va, content_len)
        b = self._story("FBI arrests federal worker charged with aiding Houthis",
                        ["FBI arrests federal worker charged with aiding Houthis"], vb, content_len)
        return a, b

    def test_word_match_vetoed_when_embeddings_differ(self):
        a, b = self._pair([1.0, 0.0, 0.0], [0.6, 0.8, 0.0])  # cosine 0.6
        self.assertFalse(stories_look_duplicate_for_edition(a, b))

    def test_word_match_kept_when_embeddings_agree(self):
        a, b = self._pair([1.0, 0.0, 0.0], [0.95, 0.31, 0.0])  # cosine ~0.95
        self.assertTrue(stories_look_duplicate_for_edition(a, b))

    def test_thin_articles_give_no_veto(self):
        # Below the 500-char content floor the embeddings are not trusted.
        a, b = self._pair([1.0, 0.0, 0.0], [0.6, 0.8, 0.0], content_len=100)
        self.assertTrue(stories_look_duplicate_for_edition(a, b))

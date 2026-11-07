"""Pure-function tests for news_fetcher/quality_checks.py -- no DB, no network.

Cases are real production output (2026-09-29) classified by hand, plus a
false-positive pin for every detector.
"""
import re
import unittest
from pathlib import Path

from news_fetcher import quality_checks as qc

REPO = Path(__file__).resolve().parents[1]


def _prompt(labels):
    body = "\n".join(f"{label} [instruction]" for label in labels)
    return f"You are an analyst.\nUse this EXACT format:\n\nx\n{body}\n\nRules:\n- be brief"


def _prompt_exact(labels):
    body = "\n".join(f"{label} [instruction]" for label in labels)
    return f"Write a report using this EXACT format:\n{body}\n\nRules:\n- be brief"


POLITICS = ["The story:", "How the left is covering it:", "How the center is covering it:",
            "How the right is covering it:", "What's contested:", "What's missing:", "What's next:"]
SPORTS = ["What happened:", "Key performances:", "The bigger picture:", "By the numbers:", "What's next:"]
SCIENCE = ["The discovery or development:", "Why it matters:", "What the research shows:",
           "Real world impact:", "What experts are saying:", "What's still unknown:", "What's next:"]


class RequiredLabelsTests(unittest.TestCase):
    def test_politics(self):
        self.assertEqual(qc.required_labels_for_prompt(_prompt_exact(POLITICS)), POLITICS)

    def test_sports_uses_its_own_labels_not_politics(self):
        got = qc.required_labels_for_prompt(_prompt_exact(SPORTS))
        self.assertEqual(got, SPORTS)
        self.assertNotIn("How the left is covering it:", got)

    def test_science(self):
        self.assertEqual(qc.required_labels_for_prompt(_prompt_exact(SCIENCE)), SCIENCE)

    def test_prose_before_format_block_is_ignored(self):
        text = "Note: this is not a label [really]\nusing this EXACT format:\nThe story: [x]\n\nRules:\nWhat's next: [nope]"
        self.assertEqual(qc.required_labels_for_prompt(text), ["The story:"])

    def test_no_format_block_means_no_requirements(self):
        self.assertEqual(qc.required_labels_for_prompt("Write one paragraph."), [])
        self.assertEqual(qc.required_labels_for_prompt(""), [])


class MissingLabelsTests(unittest.TestCase):
    def test_compliant_report(self):
        report = "\n".join(f"{l} text" for l in SPORTS)
        self.assertEqual(qc.missing_report_labels(report, _prompt_exact(SPORTS)), [])

    def test_sports_report_is_not_penalised_for_politics_labels(self):
        report = "What happened: x\nKey performances: y\nThe bigger picture: z\nBy the numbers: n\nWhat's next: w"
        self.assertEqual(qc.missing_report_labels(report, _prompt_exact(SPORTS)), [])

    def test_missing_labels_are_listed(self):
        report = "The story: x\nWhat's next: y"
        missing = qc.missing_report_labels(report, _prompt_exact(POLITICS))
        self.assertIn("How the left is covering it:", missing)
        self.assertNotIn("The story:", missing)

    def test_no_report_is_not_a_label_violation(self):
        self.assertEqual(qc.missing_report_labels(None, _prompt_exact(POLITICS)), [])


SPORTS_OTHER = ["What happened:", "Who's involved:", "How it's being covered:", "The bigger picture:", "What's next:"]


def _two_format_prompt():
    game = "\n".join(f"{label} [instruction]" for label in SPORTS)
    other = "\n".join(f"{label} [instruction]" for label in SPORTS_OTHER)
    return ("First decide what the story is about:\n- GAME: a game that was played.\n- OTHER: anything else.\n\n"
            f"If it is a GAME, write a report using EXACTLY this format:\n\n{game}\n\n"
            f"If it is OTHER, write a report using EXACTLY this format:\n\n{other}\n\n"
            "Rules:\n- Use EXACTLY the labels of the format you chose")


class AlternativeFormatTests(unittest.TestCase):
    """deep_report.sports since 2026-10-08: game recap or other-news format.
    Before this, the checker took the union of both and flagged every report."""

    def test_exactly_this_format_phrasing_is_recognised(self):
        text = "Analyze using EXACTLY this format:\n\nCore argument: [x]\n\nRules:\n- No markdown: [nope]"
        self.assertEqual(qc.required_labels_for_prompt(text), ["Core argument:"])

    def test_two_formats_give_two_sets(self):
        self.assertEqual(qc.required_label_sets_for_prompt(_two_format_prompt()), [SPORTS, SPORTS_OTHER])

    def test_game_report_passes(self):
        report = "\n".join(f"{l} text" for l in SPORTS)
        self.assertEqual(qc.missing_report_labels(report, _two_format_prompt()), [])

    def test_other_news_report_passes(self):
        report = "\n".join(f"{l} text" for l in SPORTS_OTHER)
        self.assertEqual(qc.missing_report_labels(report, _two_format_prompt()), [])

    def test_incomplete_report_is_judged_against_closest_format(self):
        report = "What happened: x\nWho's involved: y\nThe bigger picture: z\nWhat's next: w"
        self.assertEqual(qc.missing_report_labels(report, _two_format_prompt()), ["How it's being covered:"])

    def test_live_sports_prompt_text(self):
        migration = (REPO / "migrations/versions/f2a6c9e4b7d1_article_deep_analysis_all_types.py").read_text()
        ns = {}
        exec(compile(migration.split("NEW_ROWS = [")[0].replace("from alembic import op", ""), "m", "exec"), ns)
        sets = qc.required_label_sets_for_prompt(ns["DEEP_REPORT_SPORTS"])
        self.assertEqual(len(sets), 2)
        self.assertIn("Key performances:", sets[0])
        self.assertIn("Who's involved:", sets[1])


class TheStoryFilterTests(unittest.TestCase):
    def test_markers_match_filters_py(self):
        src = (REPO / "aggregator" / "filters.py").read_text()
        block = src[src.index("def get_the_story"):]
        block = block[:block.index("markers")+800]
        quoted = re.findall(r'"([^"]+:)"', block)
        for marker in qc.THE_STORY_START_MARKERS:
            self.assertIn(marker, quoted)

    def test_renders_empty_without_marker(self):
        self.assertTrue(qc.renders_empty_the_story("How the left is covering it: x"))
        self.assertTrue(qc.renders_empty_the_story(""))
        self.assertTrue(qc.renders_empty_the_story(None))

    def test_renders_with_marker(self):
        for m in qc.THE_STORY_START_MARKERS:
            self.assertFalse(qc.renders_empty_the_story(f"{m} something"))


class TruncationTests(unittest.TestCase):
    def test_real_81_char_truncated_summary_shape(self):
        self.assertIsNotNone(qc.looks_truncated("The council voted on the proposal after weeks of debate over the"))

    def test_complete_prose(self):
        for ok in ["It ended.", 'He said "no."', "Really?", "Done!", "It ended (mostly)."]:
            self.assertIsNone(qc.looks_truncated(ok), ok)

    def test_empty_is_not_flagged(self):
        self.assertIsNone(qc.looks_truncated(""))
        self.assertIsNone(qc.looks_truncated(None))

    def test_headline_dangling(self):
        self.assertIsNotNone(qc.headline_looks_truncated("Trump says he will"))
        self.assertIsNotNone(qc.headline_looks_truncated("Senate votes on the"))

    def test_headline_false_positive_pins(self):
        for ok in ["Error at Boeing plant delays deliveries",
                   "Unable to reach deal, Senate talks stall",
                   "Fed holds rates steady",
                   "Former NFL player Josh Mauro dies at 35",
                   "Fantasy football rankings released for 2026"]:
            self.assertIsNone(qc.headline_looks_truncated(ok), ok)

    def test_sentence_count(self):
        self.assertEqual(qc.sentence_count("One. Two! Three?"), 3)
        self.assertEqual(qc.sentence_count(""), 0)

    def test_sentence_count_skips_abbreviations(self):
        # Real summary of story 77888 (2026-10-06), flagged as 9 sentences.
        text = ("U.S. Coast Guard personnel exchanged gunfire with individuals across the border. "
                "This encounter prompted the deployment of hundreds of U.S. troops to the border. "
                "Mexico has requested all evidence of the shooting. "
                "Now, the U.S. Embassy in Mexico is coordinating a joint operation.")
        self.assertEqual(qc.sentence_count(text), 4)
        self.assertEqual(qc.sentence_count("Trump blocked Maryland Gov. Wes Moore from speaking. Moore objected."), 2)
        self.assertEqual(qc.sentence_count("John F. Kennedy spoke. Sen. Smith replied."), 2)
        self.assertEqual(qc.sentence_count('He said "no." Then he left.'), 2)
        self.assertEqual(qc.sentence_count("It ended at 3 p.m. Officials left."), 1)  # accepted undercount


class AcronymCasingTests(unittest.TestCase):
    REAL = {
        "Gop campaign boss says midterms feature only knife fights": ["Gop"],
        "Fifa accuses Uefa of smear campaign against Infantino": ["Fifa", "Uefa"],
        "Patriots Fan Fights Multiple Seahawks Fans At Nfl Opener": ["Nfl"],
        "Nasa launches new space telescope": ["Nasa"],
        "Spy chiefs warn Moscow plans decisive action against Nato": ["Nato"],
        "Un envoy criticizes Europe while Trump demands payment over Hormuz conflict": ["Un"],
    }

    def test_real_cases(self):
        for headline, expected in self.REAL.items():
            self.assertEqual(qc.acronym_cased_tokens(headline), expected, headline)

    def test_correctly_cased_is_clean(self):
        for ok in ["NATO jet shoots down drone", "Fans fume at NFL for being un-American",
                   "FIFA cancels World Cup stake sale plan"]:
            self.assertEqual(qc.acronym_cased_tokens(ok), [], ok)

    def test_kim_jong_un_is_a_person(self):
        for ok in ["Trump seeks talks with Kim Jong Un", "Kim Jong Un’s sister denies US talks",
                   "Kim Jong-un strengthens ties with China"]:
            self.assertEqual(qc.acronym_cased_tokens(ok), [], ok)

    def test_ordinary_words_never_flagged(self):
        for ok in ["Who will lead the party", "Ice storm hits the north",
                   "Us and them: a divided country", "Sec of state resigns", "Ai Weiwei opens show"]:
            self.assertEqual(qc.acronym_cased_tokens(ok), [], ok)

    def test_no_acronym_is_a_lowercase_english_word(self):
        for banned in ("WHO", "US", "ICE", "SEC", "AI"):
            self.assertNotIn(banned, qc.ACRONYMS)


class NumericTests(unittest.TestCase):
    def test_scale_forms_agree(self):
        for text in ["1.7M ladders", "1.7 million ladders", "1,700,000 ladders"]:
            self.assertIn(1700000.0, qc.extract_numeric_values(text), text)

    def test_single_letter_suffix_must_be_attached(self):
        vals = qc.extract_numeric_values("3 T-shirts and a 5 m pole")
        self.assertEqual(vals, {3.0, 5.0})

    def test_real_1_7m_ladders_becomes_17_million(self):
        bad = qc.unsupported_numbers("Over 17 million ladders recalled",
                                     ["Home Depot recalls 1.7M ladders"])
        self.assertEqual(bad, [17000000.0])

    def test_supported_number_is_clean(self):
        self.assertEqual(qc.unsupported_numbers("1.7 million ladders recalled",
                                                ["Home Depot recalls 1.7M ladders"]), [])

    def test_rounded_number_is_clean(self):
        self.assertEqual(qc.unsupported_numbers("About 4,900 arrested", ["4,873 people were arrested"]), [])

    def test_years_are_skipped(self):
        self.assertEqual(qc.unsupported_numbers("Trump's 2020 claims", ["He disputes the vote"]), [])

    def test_known_blind_spot_semantic_misread(self):
        # "4 savings account options" -> "$4 in interest": value present, meaning wrong.
        self.assertEqual(qc.unsupported_numbers("$4 in interest", ["4 savings account options"]), [])


class EntityTests(unittest.TestCase):
    def test_invented_entity_is_a_candidate(self):
        got = qc.unsupported_entities("Zelenskyy meets Macron in Paris",
                                      ["Zelenskyy met Macron on Tuesday"])
        self.assertIn("Paris", got)
        self.assertNotIn("Macron", got)

    def test_accents_fold(self):
        self.assertEqual(qc.unsupported_entities("Café Royal opens", ["cafe royal opens"]), [])




class FixAcronymCasingTests(unittest.TestCase):
    def test_fixes_real_defects(self):
        cases = {
            "Gop senator blocks move to stop Trump from tearing down presidential buildings":
                "GOP senator blocks move to stop Trump from tearing down presidential buildings",
            "Team Usa Captain Snedeker credits Trump for massive Presidents Cup comeback":
                "Team USA Captain Snedeker credits Trump for massive Presidents Cup comeback",
            "Fifa accuses Uefa of smear campaign against Infantino":
                "FIFA accuses UEFA of smear campaign against Infantino",
            "Uk-based Nato chief warns Eu": "UK-based NATO chief warns EU",
        }
        for bad, good in cases.items():
            self.assertEqual(qc.fix_acronym_casing(bad), good)

    def test_leaves_ordinary_words_and_names_alone(self):
        for ok in [
            "Kim Jong Un meets Putin",
            "Ice storm hits Texas",
            "Trump tells us to wait",
            "un-American activities probe",
            "GOP and NATO already correct",
            "Ai Weiwei opens exhibition",
        ]:
            self.assertEqual(qc.fix_acronym_casing(ok), ok)

    def test_empty(self):
        self.assertEqual(qc.fix_acronym_casing(""), "")
        self.assertIsNone(qc.fix_acronym_casing(None))


class UnsupportedFormerTitlesTests(unittest.TestCase):
    """unsupported_former_titles(): cases from the 2026-10-07 fix, where the
    model wrote 'former President Trump' (its training-era title) in 288 texts
    whose sources never said it."""

    f = staticmethod(qc.unsupported_former_titles)

    def test_flags_title_no_source_gives(self):
        # Story 78593's deep report vs its real source titles.
        sources = ["Trump announces $6.6B initiative to build submarine parts at Baltimore shipyard",
                   "Watch live: Trump to unveil major investment", "President Trump said on Monday"]
        self.assertEqual(self.f("The story: Former President Trump announced a major defense investment.", sources),
                         ["Former President Trump"])
        self.assertEqual(self.f("The story: Former US President Donald Trump is scheduled to visit Ireland.",
                                ["Trump to visit Ireland"]), ["Former US President Donald Trump"])

    def test_supported_in_either_order(self):
        self.assertEqual(self.f("Former President Donald Trump spoke.", ["Donald Trump, the former president, spoke"]), [])
        self.assertEqual(self.f("former President Barack Obama endorsed her.", ["Former President Barack Obama endorsed"]), [])
        self.assertEqual(self.f("former Vice President Mike Pence said", ["Pence, the former vice president"]), [])

    def test_office_abbreviations_match_spelled_out(self):
        self.assertEqual(self.f("former Gov. Larry Hogan said", ["Larry Hogan, a former governor, said"]), [])
        self.assertEqual(self.f("former Sen. Joe Manchin", ["Manchin spoke"]), ["former Sen. Joe Manchin"])

    def test_possessive_and_sentence_end(self):
        # Both were false flags in the first version, on real summaries.
        self.assertEqual(self.f("praised former President Obama's record", ["former President Barack Obama"]), [])
        self.assertEqual(self.f("succeeding former Chair Jerome Powell. However, markets fell",
                                ["former Chair Jerome Powell"]), [])

    def test_ignores_unnamed_and_lowercase(self):
        self.assertEqual(self.f("the former president of the club resigned", ["x"]), [])
        self.assertEqual(self.f("", ["x"]), [])
        self.assertEqual(self.f(None, ["x"]), [])


class FixCurrentOfficeholderTitlesTests(unittest.TestCase):
    """fix_current_officeholder_titles(): 'former' on a current officeholder."""

    def f(self, text, sources=("Trump spoke on Friday",)):
        return qc.fix_current_officeholder_titles(text, list(sources))

    def test_fixes_real_defects(self):
        cases = {
            # 83852 deep report, 2026-10-18
            "Israeli Prime Minister Benjamin Netanyahu and former U.S. President Donald Trump are discussing":
                "Israeli Prime Minister Benjamin Netanyahu and U.S. President Donald Trump are discussing",
            # article summaries 10-11 to 10-18
            "Former president Donald Trump said the deal was done.": "President Donald Trump said the deal was done.",
            "Former President Trump announced tariffs.": "President Trump announced tariffs.",
            "It is the former President Trump's plan.": "It is President Trump's plan.",
            "The former president Donald J. Trump spoke.": "President Donald J. Trump spoke.",
            "former Vice President JD Vance said": "Vice President JD Vance said",
        }
        for bad, good in cases.items():
            self.assertEqual(self.f(bad), good)

    def test_kept_when_a_source_says_it(self):
        text = "In 2022, former President Trump said he would run."
        self.assertEqual(self.f(text, ["In 2022, then-former President Donald Trump said"]), text)
        self.assertEqual(self.f(text, ["Trump, the former president, said in 2022"]), text)

    def test_leaves_other_people_and_offices(self):
        for text in (
            "former President Biden said",
            "former President Barack Obama said",
            "former Vice President Trump",          # not his office now
            "former Trump aide Steve Bannon said",
            "the former president said",            # unnamed: can't tell who
            "former Senator Marco Rubio said",
        ):
            self.assertEqual(self.f(text), text)

    def test_empty(self):
        self.assertEqual(self.f(""), "")
        self.assertIsNone(self.f(None))

    def test_detector_agrees_after_fix(self):
        bad = "Netanyahu and former U.S. President Donald Trump met."
        self.assertTrue(qc.unsupported_former_titles(bad, ["x"]))
        self.assertEqual(qc.unsupported_former_titles(self.f(bad, ["x"]), ["x"]), [])


if __name__ == "__main__":
    unittest.main()

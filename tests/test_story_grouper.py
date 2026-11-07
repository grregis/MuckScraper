import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from aggregator.article_signals import is_roundup_article
    from news_fetcher.story_grouper import (
        ask_ollama_for_match,
        build_match_prompt,
        entity_veto,
        find_matching_story_with_metadata,
        normalize_title_tokens,
        titles_are_near_duplicates,
    )
except ModuleNotFoundError as exc:
    is_roundup_article = None
    ask_ollama_for_match = None
    build_match_prompt = None
    entity_veto = None
    find_matching_story_with_metadata = None
    normalize_title_tokens = None
    titles_are_near_duplicates = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


def _fake_story(story_id, title):
    return SimpleNamespace(id=story_id, title=title, headline=None, articles=[])


class StoryGrouperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if IMPORT_ERROR is not None:
            raise unittest.SkipTest(f"Project dependencies are not installed: {IMPORT_ERROR}")

    def test_normalize_title_tokens_handles_accents_and_pm_variants(self):
        tokens = normalize_title_tokens("Péter Magyar sworn in as Hungary's PM ending Orbán era")
        self.assertIn("peter", tokens)
        self.assertIn("orban", tokens)
        self.assertIn("prime_minister", tokens)

    def test_titles_are_near_duplicates_for_hungary_story_variants(self):
        self.assertTrue(
            titles_are_near_duplicates(
                "Péter Magyar sworn in as Hungary's prime minister ending Orbán era",
                "Peter Magyar sworn in as Hungary's PM, ending Orban's 16 years in power",
            )
        )

    def test_titles_are_near_duplicates_for_hantavirus_update_variants(self):
        self.assertTrue(
            titles_are_near_duplicates(
                "Passengers on Hantavirus Cruise Ship to Disembark Sunday",
                "Spain readies for hantavirus cruise ship to dock, evacuate passengers on Canary Islands",
            )
        )

    def test_titles_are_near_duplicates_for_boating_injury_variants(self):
        self.assertTrue(
            titles_are_near_duplicates(
                "15 hospitalized after possible boat explosion near Miami Beach",
                "Nearly a dozen injured after possible boat explosion at popular Florida tourist destination",
            )
        )

    def test_titles_are_near_duplicates_for_wembanyama_ejection_variants(self):
        self.assertTrue(
            titles_are_near_duplicates(
                "Spurs’ Victor Wembanyama ejected for elbowing Timberwolves player in shocking Game 4 scene",
                "Spurs lose Victor Wembanyama to first career ejection after violent elbow in Western Conference semis",
            )
        )

    def test_normalize_title_tokens_handles_possessives_and_ceasefire_variants(self):
        tokens = normalize_title_tokens("Trump sees Iran cease-fire on life support after Tehran’s response")
        self.assertIn("ceasefire", tokens)
        self.assertIn("tehran", tokens)
        self.assertNotIn("tehrans", tokens)

    def test_titles_are_not_near_duplicates_for_broad_shared_topic(self):
        self.assertFalse(
            titles_are_near_duplicates(
                "Gold prices fall amid Iran war",
                "Trump says no ceasefire with Iran",
            )
        )

    def test_build_match_prompt_allows_same_event_updates_and_explainers(self):
        prompt = build_match_prompt(
            "What people need to know about hantavirus",
            "1. Three dead in suspected hantavirus outbreak aboard cruise ship",
            article_content="A CBS explainer tied to the active cruise ship outbreak.",
        )
        self.assertIn("update, explainer, reaction, evacuation step, casualty update, or human-interest angle", prompt)
        self.assertIn("same concrete event or ongoing situation", prompt)
        self.assertNotIn("CURRENT STORY", prompt)

    def test_build_match_prompt_adds_burden_of_proof_rule_when_incumbent_present(self):
        prompt = build_match_prompt(
            "Some article",
            "1. Some story",
            incumbent_present=True,
        )
        self.assertIn("[CURRENT STORY]", prompt)
        self.assertIn("do not move it based on title wording alone", prompt)

    def test_roundup_detector_flags_morning_rundown_titles(self):
        self.assertTrue(
            is_roundup_article(
                "Trump wraps visit with Xi Jinping and CIA director meets with Cuban officials: Morning Rundown",
                "https://www.nbcnews.com/news/us-news/trump-xi-jinping-cia-director-cuba-morning-rundown-rcna345264",
            )
        )

    def test_roundup_detector_does_not_flag_regular_single_story_title(self):
        self.assertFalse(
            is_roundup_article(
                "CIA Director John Ratcliffe meets with Cuban officials in Havana",
                "https://www.nbcnews.com/world/cuba/cia-director-ratcliffe-meets-cuban-officials-havana-rcna345214",
            )
        )

    def test_roundup_detector_does_not_misclassify_bbc_sounds_media_page(self):
        self.assertFalse(
            is_roundup_article(
                "Does Trump need China to end the war in Iran?",
                "https://www.bbc.co.uk/sounds/play/p0nl1r5w",
            )
        )

    def test_title_overlap_review_pins_incumbent_into_candidate_slate(self):
        # Three "stranger" stories each share exactly 3 tokens with the article
        # title -- enough to clear title_overlap_review's >=3 bar and fill its
        # 3-candidate cap on raw token count alone. The incumbent (the article's
        # actual current story) shares only "echo" -- below that bar, so before
        # the incumbent-pinning fix it would never even reach the ranking step
        # and the LLM would pick blind from three unrelated stories.
        article_title = "alpha bravo charlie delta echo"
        stranger1 = _fake_story(1, "alpha bravo charlie foxtrot golf")
        stranger2 = _fake_story(2, "alpha bravo delta hotel india")
        stranger3 = _fake_story(3, "alpha charlie delta juliet kilo")
        incumbent = _fake_story(4, "echo foxtrot golf hotel india")

        captured = {}

        def fake_ask_ollama_for_match(title, candidate_stories, **kwargs):
            captured["candidate_stories"] = candidate_stories
            return incumbent

        with patch("news_fetcher.story_grouper.llm_client.is_configured", return_value=True), \
             patch("news_fetcher.story_grouper.ask_ollama_for_match", side_effect=fake_ask_ollama_for_match):
            decision = find_matching_story_with_metadata(
                article_title,
                [0.0],
                [stranger1, stranger2, stranger3, incumbent],
                incumbent_story=incumbent,
            )

        candidate_ids = [story.id for story in captured["candidate_stories"]]
        self.assertLessEqual(len(candidate_ids), 3)
        self.assertIn(incumbent.id, candidate_ids)
        self.assertEqual(decision.story, incumbent)
        self.assertEqual(decision.method, "title_overlap_review")

    def test_ask_ollama_for_match_tags_incumbent_and_adds_burden_of_proof_rule(self):
        # Reproduces a real bad reassignment: the correct story lost purely on
        # position/title-overlap ranking until the incumbent was tagged. See
        # ask_ollama_for_match's docstring for the live before/after numbers.
        wrong = _fake_story(1, "More than 1,000 birds found dead in three mass mortality events")
        correct = _fake_story(2, "Violence flares in Haiti as gang attack kills 30 people")

        captured = {}

        def fake_generate_text(prompt, **kwargs):
            captured["prompt"] = prompt
            return "2"

        with patch("news_fetcher.story_grouper.llm_client.generate_text", side_effect=fake_generate_text):
            ask_ollama_for_match(
                "More than 50 kidnapped as violent gang attack in Haiti leaves 47 dead",
                [wrong, correct],
                incumbent_id=correct.id,
            )

        prompt = captured["prompt"]
        self.assertIn("2. Violence flares in Haiti as gang attack kills 30 people [CURRENT STORY", prompt)
        self.assertNotIn("1. More than 1,000 birds found dead in three mass mortality events [CURRENT STORY", prompt)
        self.assertIn("do not move it based on title wording alone", prompt)

    def test_title_overlap_review_unaffected_when_no_incumbent(self):
        # Ingestion path: no current placement to defend, so passing no
        # incumbent_story must leave the original ranking-by-token-count
        # behavior untouched.
        article_title = "alpha bravo charlie delta echo"
        stranger1 = _fake_story(1, "alpha bravo charlie foxtrot golf")
        stranger2 = _fake_story(2, "alpha bravo delta hotel india")
        stranger3 = _fake_story(3, "alpha charlie delta juliet kilo")

        captured = {}

        def fake_ask_ollama_for_match(title, candidate_stories, **kwargs):
            captured["candidate_stories"] = candidate_stories
            return stranger1

        with patch("news_fetcher.story_grouper.llm_client.is_configured", return_value=True), \
             patch("news_fetcher.story_grouper.ask_ollama_for_match", side_effect=fake_ask_ollama_for_match):
            find_matching_story_with_metadata(
                article_title,
                [0.0],
                [stranger1, stranger2, stranger3],
            )

        candidate_ids = {story.id for story in captured["candidate_stories"]}
        self.assertEqual(candidate_ids, {1, 2, 3})

    def test_entity_veto_blocks_nepal_flood_article_from_ukraine_strike_story(self):
        # Reproduces a real 09-04 misgrouping: title_overlap_review moved a
        # Nepal flood death-toll article into a Russia/Ukraine arms-depot-strike
        # story on nothing but the shared phrase "death toll rises to X".
        article_title = "Nepal says death toll rises to 1,252 as rescue operations continue"
        ukraine_story = _fake_story(1, "Death toll rises to 38 from Russian strike on Ukrainian arms depot")
        self.assertTrue(entity_veto(article_title, ukraine_story))

    def test_entity_veto_allows_match_when_countries_overlap(self):
        article_title = "Nepal says death toll rises to 1,252 as rescue operations continue"
        nepal_story = _fake_story(1, "Nepal flood tunnel rescue offers last hope as death toll tops 1,000")
        self.assertFalse(entity_veto(article_title, nepal_story))

    def test_entity_veto_allows_match_when_neither_side_names_a_country(self):
        article_title = "Local officials urge calm after malaria outbreak in rural county"
        other_story = _fake_story(1, "State health department confirms new malaria cases")
        self.assertFalse(entity_veto(article_title, other_story))

    def test_entity_veto_recognizes_demonym_forms(self):
        # Article uses the demonym ("Russian"), story uses the canonical
        # country name ("Russia") -- must still be recognized as the same
        # country via TITLE_TOKEN_REPLACEMENTS, not vetoed as a mismatch.
        article_title = "Russian strike hits Ukrainian arms depot, killing dozens"
        story = _fake_story(1, "Death toll rises to 38 from Russia's deadliest attack on Ukraine")
        self.assertFalse(entity_veto(article_title, story))

    def test_ask_ollama_for_match_applies_entity_veto_to_llm_pick(self):
        # End-to-end: even when the LLM picks the country-mismatched story,
        # ask_ollama_for_match must reject it rather than trust the model.
        ukraine_story = _fake_story(1, "Death toll rises to 38 from Russian strike on Ukrainian arms depot")

        with patch("news_fetcher.story_grouper.llm_client.generate_text", return_value="1"):
            result = ask_ollama_for_match(
                "Nepal says death toll rises to 1,252 as rescue operations continue",
                [ukraine_story],
            )

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

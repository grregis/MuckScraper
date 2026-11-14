"""Running-story cap in edition selection (2026-11-14): at most
EDITION_RUNNING_STORY_CAP kept stories may share a headline proper noun.
Pure functions, no database."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from news_fetcher.fetch_and_store_articles import edition_publish as ep


def story(story_id, headline):
    return SimpleNamespace(id=story_id, headline=headline, title=headline, articles=[])


class HeadlineProperTokensTests(unittest.TestCase):
    def test_takes_capitalised_words_after_the_first(self):
        self.assertEqual(
            ep.headline_proper_tokens("Jensen Huang resigns as Nvidia CEO amid scandal and stock slide"),
            {"huang", "nvidia"},
        )

    def test_skips_parties_institutions_states_and_leaders(self):
        self.assertEqual(
            ep.headline_proper_tokens("Democrats probe Trump administration over Nvidia chip sales to China"),
            {"nvidia"},
        )
        self.assertEqual(
            ep.headline_proper_tokens("Alaska judge denies Peltola campaign request for Senate ballot data"),
            {"peltola"},
        )
        self.assertEqual(
            ep.headline_proper_tokens("Justice Department sends criminal target letters in Alaska Senate probe"),
            set(),
        )

    def test_no_headline_means_no_tokens(self):
        self.assertEqual(ep.headline_proper_tokens(None), set())


class StoriesShareRunningStoryTests(unittest.TestCase):
    def _with_centroids(self, centroids):
        return patch.object(ep, "_story_embedding_centroid", side_effect=lambda s: centroids.get(s.id))

    def test_shared_proper_noun_with_similar_embeddings_links(self):
        a = story(1, "Republicans call on Howard Lutnick and Jensen Huang to testify on Nvidia")
        b = story(2, "Bessent urges Commerce Dept. to enforce export controls on Nvidia chips")
        with self._with_centroids({1: np.array([1.0, 0.0]), 2: np.array([0.66, 0.75])}):
            self.assertEqual(ep.stories_share_running_story(a, b), {"nvidia"})

    def test_dissimilar_embeddings_do_not_link(self):
        a = story(1, "Nvidia shares fall after earnings")
        b = story(2, "Gamers queue for new Nvidia graphics card")
        with self._with_centroids({1: np.array([1.0, 0.0]), 2: np.array([0.3, 0.95])}):
            self.assertEqual(ep.stories_share_running_story(a, b), set())

    def test_missing_embeddings_do_not_link(self):
        a = story(1, "Bessent blames Lutnick over Nvidia exports")
        b = story(2, "Lutnick agrees to testify on Nvidia")
        with self._with_centroids({}):
            self.assertEqual(ep.stories_share_running_story(a, b), set())

    def test_common_words_do_not_link(self):
        # Replay false drops with shared title words (2026-11-14): these share
        # only ordinary or institutional words, so they must stay unlinked.
        pairs = [
            ("DOJ asks Washington to set aside 40,000 ballots",
             "Attorney General Todd Blanche defends DOJ lawsuit over Alaska election"),
            ("Democrat Gina Hinojosa defeats Republican Greg Abbott in Texas governor race",
             "Democrat Aaron Ford unseats Republican incumbent Joe Lombardo in Nevada"),
            ("Prosecutors charge suspect in racist-motive murder in Paris suburbs",
             "Suspect arrested after fire ravages Hitler's Eagle's Nest mountain retreat"),
        ]
        for i, (h1, h2) in enumerate(pairs):
            a, b = story(10 * i + 1, h1), story(10 * i + 2, h2)
            with self._with_centroids({a.id: np.array([1.0, 0.0]), b.id: np.array([1.0, 0.0])}):
                self.assertEqual(ep.stories_share_running_story(a, b), set(), (h1, h2))


class RunningStoryMembersTests(unittest.TestCase):
    def test_edition_793_nvidia_lineup_is_capped_at_two(self):
        # Edition 793 in rank order; the first two Nvidia stories are kept and
        # every further one joins their running story.
        lineup = [
            story(97358, "Republicans call on Howard Lutnick and Jensen Huang to testify on Nvidia"),
            story(97415, "Bessent urges Commerce Dept. to enforce export controls on Nvidia chips"),
            story(96716, "Democrats probe Trump administration over Nvidia chip sales to China"),
            story(96224, "Jensen Huang resigns as Nvidia CEO amid scandal and stock slide"),
            story(96489, "Bessent says Commerce Dept 'has the pen' on Megaspeed blacklist"),
            story(97519, "Supreme Court to rule on Trump's southern border asylum ban"),
        ]

        def link(a, b):
            return ep.headline_proper_tokens(a.headline) & ep.headline_proper_tokens(b.headline)

        kept = []
        for candidate in lineup:
            members, _ = ep.running_story_members(candidate, kept, link=link)
            if len(members) >= ep.EDITION_RUNNING_STORY_CAP:
                continue
            kept.append(candidate)
        self.assertEqual([s.id for s in kept], [97358, 97415, 97519])

    def test_members_include_stories_linked_through_each_other(self):
        a, b, c = story(1, "x"), story(2, "y"), story(3, "z")
        links = {(3, 1)}

        def link(x, y):
            return {"w"} if (x.id, y.id) in links or (y.id, x.id) in links or {x.id, y.id} == {1, 2} else set()

        members, words = ep.running_story_members(c, [a, b], link=link)
        self.assertEqual({m.id for m in members}, {1, 2})
        self.assertEqual(words, {"w"})


if __name__ == "__main__":
    unittest.main()

# news_fetcher/fetch_and_store_articles/story_topics.py
"""
A story's topics are the topics a real share of its articles have.

Until 2026-11-17 topics only ever accumulated: a story gained every topic any
article joining it was classified into, and each new article was then tagged
with the story's whole list. Big stories snowballed into every topic. The FAA
ground stop (97515) reached all 7, so it was listed under Sports and got the
politics report format ("US Politics" + "Sports" counts as politics in
detect_analysis_type()), and every article joining it, United Airlines' own
outage included, was tagged with all 7 topics as well.

Now an article keeps its own classification (plus its feed's topic), and the
story's topics are recomputed from its articles whenever articles join or
leave it: a topic counts when at least STORY_TOPIC_MIN_SHARE of the story's
classified articles carry it, the most common topic(s) always count, and
"Other" is dropped once anything else qualifies. Stories of up to four
articles therefore keep every topic their articles have.
"""

import math
from collections import Counter

STORY_TOPIC_MIN_SHARE = 0.25
OTHER_TOPIC_NAME = "Other"


def story_topic_choice(article_topic_lists):
    """Pick a story's topics from its articles' topic lists (pure function).

    `article_topic_lists` holds one iterable of topics per article (any
    hashable with a `.name`, or plain strings). Returns the chosen topics,
    most common first. Articles with no topics don't count toward the share.
    """
    lists = [list(dict.fromkeys(topics)) for topics in article_topic_lists if topics]
    if not lists:
        return []
    counts = Counter(topic for topics in lists for topic in topics)
    needed = max(1, math.ceil(STORY_TOPIC_MIN_SHARE * len(lists)))
    top = max(counts.values())
    chosen = [topic for topic, n in counts.most_common() if n >= needed or n == top]

    def name(topic):
        return getattr(topic, "name", topic)

    if len(chosen) > 1:
        without_other = [topic for topic in chosen if name(topic) != OTHER_TOPIC_NAME]
        if without_other:
            chosen = without_other
    return chosen


def refresh_story_topics(story):
    """Set `story.topics` from its articles' own topics. Keeps the current
    topics when no article has any (e.g. older rows), so it never empties a
    story's topic list."""
    if story is None:
        return
    chosen = story_topic_choice([article.topics for article in story.articles])
    if not chosen:
        return
    if set(chosen) != set(story.topics):
        story.topics = chosen

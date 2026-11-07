from aggregator.article_signals import low_value_article_reason as reason


def test_fox_outkick_betting_promo_is_filtered():
    title = "Fanatics Sportsbook Promo Code FOXNEWS350: Bet $20, Get $350 on Iowa vs Ohio State"
    url = "https://www.foxnews.com/outkick-betting/fanatics-sportsbook-promo-code-foxnews350-bet-20-get-350"
    assert reason(title, url) is not None


def test_outkick_betting_section_filtered_even_without_promo_wording():
    url = "https://www.foxnews.com/outkick-betting/pennsylvania-sports-betting-best-sportsbooks"
    assert reason("Pennsylvania Sports Betting - Best Sportsbooks and Apps", url) is not None


def test_promo_code_title_filtered_on_any_url():
    assert reason("bet365 bonus code: Bet $10, get $200 in bonus bets for Rockets vs. Lakers",
                  "https://example.com/sports/story") == "betting"


def test_ordinary_outkick_and_sports_news_kept():
    assert reason("Tony Romo leaves CBS Sports following July DUI arrest",
                  "https://www.outkick.com/sports/tony-romo-cbs") is None
    assert reason("New York sues Kalshi for alleged illegal gambling operation",
                  "https://www.espn.com/espn/story/_/id/123/new-york-sues-kalshi") is None

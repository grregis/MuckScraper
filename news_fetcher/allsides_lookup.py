# news_fetcher/allsides_lookup.py
# AllSides Media Bias Ratings mapped to MuckScraper's 1-5 scale.
# 1=Left, 2=Lean Left, 3=Center, 4=Lean Right, 5=Right
# Source: allsides.com/media-bias/media-bias-ratings
# Last updated: April 2026

ALLSIDES_BIAS = {
    # Wire Services
    "Associated Press": 2,
    "AP": 2,
    "Reuters": 3,
    "United Press International": 3,
    "UPI": 3,

    # Public Broadcasting
    "NPR": 2,
    "PBS": 3,
    "PBS NewsHour": 3,
    "PBS News": 3,
    "BBC": 2,
    "BBC News": 2,

    # Center-Left Networks
    "ABC News": 2,
    "CBS News": 2,
    "NBC News": 2,
    "MSNBC": 1,
    "CNN": 2,

    # Print / Digital Center-Left
    "The Washington Post": 1,
    "Washington Post": 1,
    "The New York Times": 1,
    "New York Times": 1,
    "The Guardian": 1,
    "Guardian": 1,
    "The Atlantic": 1,
    "Atlantic": 1,
    "Politico": 2,
    "Business Insider": 2,
    "Fast Company": 2,
    "Vox": 1,
    "HuffPost": 1,
    "The Independent": 2,
    "Time": 2,
    "Newsweek": 3,

    # Center
    "Axios": 3,
    "The Hill": 3,
    "Hill": 3,
    "RealClearPolitics": 3,
    "Real Clear Politics": 3,
    "USA Today": 3,
    "Bloomberg": 3,
    "Forbes": 3,
    "Al Jazeera": 2,
    "Al Jazeera English": 2,
    "Christian Science Monitor": 3,
    "The Christian Science Monitor": 3,
    "The Economist": 3,

    # Center-Right
    "Wall Street Journal": 4,
    "The Wall Street Journal": 4,
    "National Post": 4,
    "The Telegraph": 4,
    "Telegraph": 4,
    "New York Post": 5,
    "NY Post": 5,
    "Daily Mail": 4,
    "The Daily Mail": 4,
    "Washington Examiner": 4,
    "Washington Times": 4,
    "The Washington Times": 4,
    "National Review": 4,
    "Fox Business": 4,
    "Reason": 4,

    # Right
    "Fox News": 5,
    "Breitbart": 5,
    "Breitbart News": 5,
    "Breitbart News Network": 5,
    "The Daily Wire": 5,
    "Daily Wire": 5,
    "Newsmax": 5,
    "Toronto Sun": 5,
    "The Federalist": 5,
    "Daily Caller": 5,
    "The Daily Caller": 5,
    "New York Sun": 4,
}


def get_allsides_score(outlet_name):
    """
    Look up an outlet's AllSides bias score by name.
    Returns float score (1-5) or None if not found.
    Tries exact match first, then case-insensitive match.
    """
    if not outlet_name:
        return None

    # Exact match
    if outlet_name in ALLSIDES_BIAS:
        return float(ALLSIDES_BIAS[outlet_name])

    # Case-insensitive match
    outlet_lower = outlet_name.lower().strip()
    for key, score in ALLSIDES_BIAS.items():
        if key.lower() == outlet_lower:
            return float(score)

    return None

"""
Pulls headlines from Moneycontrol, Economic Times, and Zerodha Pulse via
public RSS feeds, and filters them down to articles that actually mention a
given company. Also pulls BBC, Al Jazeera, CNN, and Times of India for
general world/geopolitical coverage -- these four are used by
macro/macro_strategist.py only, not for per-stock news, since they don't
help match headlines to a specific company the way the financial sources
do; they're for exactly the kind of story a keyword match would never
catch (a Middle East conflict, a central bank move, a natural disaster).

Important honest caveat: the Moneycontrol and Economic Times feed URLs were
written from general knowledge of how these sites publish RSS, not verified
live against the sites themselves (both are blocked from the web-browsing
tools available while building this project). The Zerodha Pulse feed, by
contrast, WAS verified live and working while building this
(http://pulse.zerodha.com/feed.php) -- it's a market news aggregator that
itself pulls from Economic Times, NDTV Profit, Finshots, and other major
Indian financial sources, so it's a strong, confirmed source on its own.
BBC, Al Jazeera, CNN, and Times of India were ALL verified live and working
when added -- BBC's and Al Jazeera's live feeds both carried real Iran-
related coverage at verification time, confirming they add the kind of
geopolitical signal the financial-only sources above were missing.

News sites occasionally restructure their RSS feeds. Each fetch is wrapped
so that if a feed URL has moved or is temporarily unavailable, that one
source is skipped with a warning rather than crashing the whole pipeline --
check the printed warnings the first time you run this, and update the URLs
below if a source consistently fails.

Groww was considered but not added: its blog (groww.in/blog) is educational
content ("What is a Stop-Limit Order", "What is BTST Trading"), not timely
news about specific companies, and it doesn't publish a working RSS feed at
the standard address -- it wouldn't add real signal here.

RSS feeds are the sites' own publisher-sanctioned way of syndicating
headlines (the same mechanism news readers and aggregators use) -- this is
not scraping.
"""

import csv
import os
import re

import feedparser
import requests

REQUEST_TIMEOUT = 10  # seconds

MONEYCONTROL_FEEDS = {
    "markets": "https://www.moneycontrol.com/rss/marketreports.xml",
    "business": "https://www.moneycontrol.com/rss/business.xml",
    "results": "https://www.moneycontrol.com/rss/results.xml",
    "latest": "https://www.moneycontrol.com/rss/latestnews.xml",
}

ECONOMIC_TIMES_FEEDS = {
    "markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "stocks": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
}

# Verified live and working while building this project -- a market news
# aggregator, so a single feed covers many underlying sources at once.
ZERODHA_PULSE_FEED = "http://pulse.zerodha.com/feed.php"

# General world/geopolitical sources -- used by Macro Strategist only (see
# module docstring). All verified live and working when added.
BBC_WORLD_FEED = "http://feeds.bbci.co.uk/news/world/rss.xml"
ALJAZEERA_FEED = "https://www.aljazeera.com/xml/rss/all.xml"
CNN_WORLD_FEED = "http://rss.cnn.com/rss/cnn_world.rss"
TIMES_OF_INDIA_WORLD_FEED = "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms"

# Manual overrides: ticker symbol -> name variants likely to appear in
# headlines. Only needed where the auto-derived keywords (from the Nifty 500
# constituents CSV's Company Name column -- see _build_auto_keywords) would
# miss common shorthand, like "RIL" for Reliance. Every other symbol gets
# its keywords derived automatically, so nothing needs to be added here just
# because a new stock entered the tradable universe.
SYMBOL_KEYWORDS = {
    "RELIANCE.NS": ["Reliance", "Reliance Industries", "RIL"],
    "TCS.NS": ["TCS", "Tata Consultancy"],
    "HDFCBANK.NS": ["HDFC Bank", "HDFC"],
    "INFY.NS": ["Infosys"],
    "ICICIBANK.NS": ["ICICI Bank", "ICICI"],
}

NIFTY500_CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "nifty500_constituents.csv")

# Corporate suffixes that carry no matching signal -- stripped from the end
# of company names when deriving keywords ("Patanjali Foods Ltd." should
# match on "Patanjali Foods", not require headlines to say "Ltd.").
_CORPORATE_SUFFIXES = re.compile(r"\s+(ltd\.?|limited)\s*$", re.IGNORECASE)

# First words too generic to use alone as a headline keyword -- "Indian Oil
# Corporation" must not match every headline containing "Indian". The full
# company name variant still applies to these; only the single-word
# shorthand is suppressed.
_GENERIC_FIRST_WORDS = {
    "india", "indian", "national", "central", "state", "the", "new",
    "united", "great", "general", "oil", "power", "steel", "life", "bank",
}

_auto_keywords_cache = None


def _derive_keywords(company_name: str) -> list:
    """
    Turns a CSV company name into headline-matching keyword variants:
    the full name minus corporate suffixes ("Patanjali Foods Ltd." ->
    "Patanjali Foods"), plus the first word alone when it's distinctive
    enough to stand on its own ("Patanjali" -- headlines rarely use the
    full legal name). Group names like Tata/Adani are deliberately kept as
    single-word variants: group-level news (e.g. a governance scandal)
    genuinely affects each member stock, and the News Agent sees which
    company it's assessing so it can discount unrelated group headlines.
    """
    cleaned = _CORPORATE_SUFFIXES.sub("", company_name.strip())
    if not cleaned:
        return []

    variants = [cleaned]
    first_word = cleaned.split()[0]
    if (len(first_word) >= 4 and first_word.lower() not in _GENERIC_FIRST_WORDS
            and first_word.lower() != cleaned.lower()):
        variants.append(first_word)
    return variants


def _build_auto_keywords() -> dict:
    """
    Builds {"SYMBOL.NS": [keyword variants]} for every stock in the Nifty
    500 constituents CSV (the same file run_daily.py scans from), so news
    filtering works for the entire tradable universe without maintaining a
    manual mapping per stock. Returns {} with a warning if the CSV is
    missing -- callers fall back to the manual SYMBOL_KEYWORDS only.
    """
    global _auto_keywords_cache
    if _auto_keywords_cache is not None:
        return _auto_keywords_cache

    if not os.path.exists(NIFTY500_CSV_PATH):
        print(f"WARNING: Nifty 500 constituents CSV not found at {NIFTY500_CSV_PATH} -- "
              f"news keyword auto-derivation disabled, only manual SYMBOL_KEYWORDS apply.")
        _auto_keywords_cache = {}
        return _auto_keywords_cache

    mapping = {}
    with open(NIFTY500_CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            symbol = (row.get("Symbol") or "").strip()
            company_name = (row.get("Company Name") or "").strip()
            if not symbol or not company_name:
                continue
            keywords = _derive_keywords(company_name)
            if keywords:
                mapping[f"{symbol}.NS"] = keywords

    _auto_keywords_cache = mapping
    return mapping


def keywords_for_symbol(symbol: str) -> list:
    """
    Keyword variants for a symbol: the manual SYMBOL_KEYWORDS override wins
    if present (curated shorthand like "RIL"), otherwise auto-derived from
    the company's name in the Nifty 500 CSV. Returns [] only if the symbol
    is in neither -- which now means "not in the scanned universe at all",
    not "nobody added it to a hardcoded map yet".
    """
    manual = SYMBOL_KEYWORDS.get(symbol)
    if manual:
        return manual
    return _build_auto_keywords().get(symbol, [])


def _fetch_feed(url: str, source_label: str) -> list:
    """Fetches and parses one RSS feed. Returns [] and prints a warning on any failure."""
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        if parsed.bozo and not parsed.entries:
            print(f"WARNING: could not parse RSS feed [{source_label}] {url}: {parsed.bozo_exception}")
            return []
        return [
            {"title": entry.get("title", ""), "summary": entry.get("summary", ""), "publisher": source_label}
            for entry in parsed.entries
        ]
    except Exception as e:
        print(f"WARNING: could not fetch RSS feed [{source_label}] {url}: {e}")
        return []


def fetch_moneycontrol_articles() -> list:
    """Fetches all configured Moneycontrol feeds and combines them."""
    articles = []
    for section, url in MONEYCONTROL_FEEDS.items():
        articles.extend(_fetch_feed(url, f"Moneycontrol/{section}"))
    return articles


def fetch_economic_times_articles() -> list:
    """Fetches all configured Economic Times feeds and combines them."""
    articles = []
    for section, url in ECONOMIC_TIMES_FEEDS.items():
        articles.extend(_fetch_feed(url, f"EconomicTimes/{section}"))
    return articles


def fetch_zerodha_pulse_articles() -> list:
    """Fetches Zerodha Pulse's aggregated market news feed."""
    return _fetch_feed(ZERODHA_PULSE_FEED, "ZerodhaPulse")


def fetch_bbc_articles() -> list:
    """Fetches BBC's World News feed -- general world/geopolitical coverage."""
    return _fetch_feed(BBC_WORLD_FEED, "BBC")


def fetch_aljazeera_articles() -> list:
    """
    Fetches Al Jazeera's general feed -- a mix of world news, sport, and
    features, but with particularly strong Middle East coverage, which is
    exactly the gap this was added to close (a regional conflict story is
    more likely to show up here first than in Indian financial RSS feeds).
    """
    return _fetch_feed(ALJAZEERA_FEED, "AlJazeera")


def fetch_cnn_articles() -> list:
    """Fetches CNN's World News feed."""
    return _fetch_feed(CNN_WORLD_FEED, "CNN")


def fetch_times_of_india_articles() -> list:
    """Fetches Times of India's World section feed."""
    return _fetch_feed(TIMES_OF_INDIA_WORLD_FEED, "TimesOfIndia")


def _matches_keywords(article: dict, keywords: list) -> bool:
    haystack = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    return any(kw.lower() in haystack for kw in keywords)


def fetch_rss_news_for_symbol(symbol: str, max_items: int = 8) -> list:
    """
    Fetches Moneycontrol + Economic Times + Zerodha Pulse articles and
    filters down to the ones that actually mention the given symbol's
    company (by name, since RSS feeds are topic-based, not per-ticker).
    Returns a list of {'title', 'publisher'} dicts, same shape as
    news_agent.fetch_recent_news, so all sources can be combined in one list.
    """
    keywords = keywords_for_symbol(symbol)
    if not keywords:
        print(f"WARNING: no keywords for {symbol} -- not in SYMBOL_KEYWORDS and not in the "
              f"Nifty 500 constituents CSV. Can't filter RSS articles for this symbol.")
        return []

    all_articles = (
        fetch_moneycontrol_articles()
        + fetch_economic_times_articles()
        + fetch_zerodha_pulse_articles()
    )
    matched = [a for a in all_articles if _matches_keywords(a, keywords)]

    return [{"title": a["title"], "publisher": a["publisher"]} for a in matched[:max_items]]

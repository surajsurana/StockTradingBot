"""
Pulls headlines from Moneycontrol, Economic Times, and Zerodha Pulse via
public RSS feeds, and filters them down to articles that actually mention a
given company.

Important honest caveat: the Moneycontrol and Economic Times feed URLs were
written from general knowledge of how these sites publish RSS, not verified
live against the sites themselves (both are blocked from the web-browsing
tools available while building this project). The Zerodha Pulse feed, by
contrast, WAS verified live and working while building this
(http://pulse.zerodha.com/feed.php) -- it's a market news aggregator that
itself pulls from Economic Times, NDTV Profit, Finshots, and other major
Indian financial sources, so it's a strong, confirmed source on its own.

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

# Maps a ticker symbol to the name variants likely to appear in headlines.
# Extend this as more symbols are added to the tradable universe.
SYMBOL_KEYWORDS = {
    "RELIANCE.NS": ["Reliance", "Reliance Industries", "RIL"],
    "TCS.NS": ["TCS", "Tata Consultancy"],
    "HDFCBANK.NS": ["HDFC Bank", "HDFC"],
    "INFY.NS": ["Infosys"],
    "ICICIBANK.NS": ["ICICI Bank", "ICICI"],
}


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
    keywords = SYMBOL_KEYWORDS.get(symbol)
    if not keywords:
        print(f"WARNING: no keyword mapping for {symbol} in SYMBOL_KEYWORDS -- "
              f"can't filter RSS articles for this symbol. Add it to news/rss_sources.py.")
        return []

    all_articles = (
        fetch_moneycontrol_articles()
        + fetch_economic_times_articles()
        + fetch_zerodha_pulse_articles()
    )
    matched = [a for a in all_articles if _matches_keywords(a, keywords)]

    return [{"title": a["title"], "publisher": a["publisher"]} for a in matched[:max_items]]

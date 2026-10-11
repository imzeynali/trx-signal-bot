import calendar
import re
import threading
import time
from urllib.parse import urlparse

import feedparser
import numpy as np
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
]
KEYWORDS = {
    "TRX": ["tron", "trx", "justin sun"], "BTC": ["bitcoin", "btc"], "ETH": ["ethereum", "ether"],
    "BNB": ["bnb", "binance coin"], "SOL": ["solana"], "XRP": ["xrp", "ripple"],
    "ADA": ["cardano"], "DOGE": ["dogecoin", "doge"], "LTC": ["litecoin"], "DOT": ["polkadot"],
    "AVAX": ["avalanche"], "LINK": ["chainlink"], "TON": ["toncoin"], "SHIB": ["shiba inu", "shib"],
}
MARKET_WORDS = ["bitcoin", "fed", "sec", "etf", "regulation", "stablecoin", "market"]
WINDOW_HOURS = 48

_vader = SentimentIntensityAnalyzer()
_lock = threading.Lock()
_entries_cache = {"t": 0.0, "v": []}


def _strip(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


def _load_entries() -> list[dict]:
    with _lock:
        age = time.time() - _entries_cache["t"]
        if _entries_cache["v"] and age < 600 or (not _entries_cache["v"] and age < 60):
            return _entries_cache["v"]
        out = []
        for url in FEEDS:
            try:
                r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                feed = feedparser.parse(r.content)
            except Exception:
                continue
            for e in feed.entries[:60]:
                p = getattr(e, "published_parsed", None)
                ts = calendar.timegm(p) if p else time.time()
                text = f"{e.get('title', '')}. {_strip(e.get('summary', ''))}"
                out.append({
                    "title": e.get("title", "").strip(), "link": e.get("link", ""),
                    "ts": ts, "text": text.lower(),
                    "sent": _vader.polarity_scores(text)["compound"],
                    "source": urlparse(e.get("link", "")).netloc.replace("www.", ""),
                })
        _entries_cache.update(t=time.time(), v=out)
        return out


def _pattern(words: list[str]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b")


def fetch_news(symbol: str) -> dict:
    base = symbol.split("/")[0]
    keys = list(dict.fromkeys(KEYWORDS.get(base, []) + [base.lower()]))
    pa, pm = _pattern(keys), _pattern(MARKET_WORDS)
    cutoff = time.time() - WINDOW_HOURS * 3600
    asset, market = [], []
    for e in sorted(_load_entries(), key=lambda x: -x["ts"]):
        if e["ts"] < cutoff:
            continue
        item = {k: e[k] for k in ("title", "link", "ts", "sent", "source")}
        if pa.search(e["text"]):
            asset.append(item)
        elif pm.search(e["text"]):
            market.append(item)
    avg = lambda xs: float(np.mean([x["sent"] for x in xs])) if xs else 0.0
    return {"asset": asset[:15], "market": market[:10],
            "asset_count": len(asset), "market_count": len(market),
            "asset_sent": avg(asset), "market_sent": avg(market), "window_hours": WINDOW_HOURS}


def news_adjustment(news: dict) -> int:
    adj = 0
    if news["asset"]:
        adj += 1 if news["asset_sent"] > 0.25 else -1 if news["asset_sent"] < -0.25 else 0
    if news["market"]:
        adj += 1 if news["market_sent"] > 0.25 else -1 if news["market_sent"] < -0.25 else 0
    return adj

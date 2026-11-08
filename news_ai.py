"""The bot's own news AI, built from scratch (no paid API, no outside AI model).

How it learns, in plain English:
  1. It reads every market headline (and short summary) from Alpaca's free news feed.
  2. For each trading day it gathers the news published between the previous day's close and
     just before the opening bell: what a trader would have read before the market opened.
  3. It turns that text into word counts ("tariffs" x3, "rate cut" x1, ...).
  4. It learns a weight for each word or two-word phrase: positive if it tended to come before days
     the Nasdaq-100 went up, negative if before down days. This is logistic regression, trained
     with plain gradient descent written below in numpy.
  5. Each morning it reads the overnight news and gives a probability that today will be an up day,
     plus the words that pushed it most, so you can see WHY.

Honest limits: news is already partly priced in by the time it's published, and big funds read it
faster. research/news_test.py and news_test2.py test it on years it never saw (2019-2026, 83k articles):
it was right 52.8% of days, while always guessing "up" was right 54.2%. So the bot does NOT trade on it.
The dashboard only shows the headlines.
"""
import json
import math
import re
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ET = ZoneInfo("America/New_York")
HERE = Path(__file__).resolve().parent
MODEL = HERE / "data" / "news_model.json"

STOP = set("""a an the and or of to in on for at by with from as is are was were be been it its this that
these those has have had will would can could may might than then into over about after before up down
not no but if so what which who how why when new says said say s inc corp co ltd etc vs per via amid
top just more most here there their they he she we you i his her our your also all any one two three""".split())
BOILER = re.compile(r"imbalance update|benzinga pro|unusual options|options activity|whale|earnings scheduled|"
                    r"price target|analyst ratings|premarket movers|mid-day gainers|mid-day losers")


def tokens(text):
    """Lower-case words and two-word phrases, minus filler words."""
    words = [w for w in re.findall(r"[a-z][a-z'\-]+", text.lower()) if w not in STOP and len(w) > 2]
    return words + [a + "_" + b for a, b in zip(words, words[1:])]


def article_text(a):
    if BOILER.search(a["h"].lower()):
        return ""  # routine listings carry no news
    return a["h"] + " " + a.get("s", "")


# ---------- grouping news into "what you could read before the open" ----------
def premarket_window(day):
    """News from 4:00 PM ET on the previous weekday to 9:25 AM ET on `day` (weekends included)."""
    prev = day - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return (datetime.combine(prev, time(16, 0), ET), datetime.combine(day, time(9, 25), ET))


def daily_docs(articles, trading_days):
    """{day: Counter of tokens} using only news published before 9:25 AM ET that day."""
    arts = sorted(((datetime.fromisoformat(a["t"]).astimezone(ET), a) for a in articles), key=lambda x: x[0])
    stamps = [t for t, _ in arts]
    import bisect
    docs = {}
    for d in trading_days:
        a, b = premarket_window(d)
        i, j = bisect.bisect_left(stamps, a), bisect.bisect_left(stamps, b)
        c = Counter()
        for _, art in arts[i:j]:
            c.update(set(tokens(article_text(art))))  # each article counts a word once
        docs[d] = (c, j - i)
    return docs


# ---------- the model ----------
class NewsBrain:
    def __init__(self, vocab=None, w=None, b=0.0):
        self.vocab = vocab or {}
        self.w = np.array(w if w is not None else [], dtype=float)
        self.b = b

    def _matrix(self, docs):
        X = np.zeros((len(docs), len(self.vocab)))
        for r, (c, n) in enumerate(docs):
            for t, k in c.items():
                j = self.vocab.get(t)
                if j is not None:
                    X[r, j] = math.log1p(k) / math.log1p(max(n, 1))  # share of the day's articles, damped
        return X

    def fit(self, docs, up, min_days=30, max_vocab=3000, l2=3.0, steps=600, lr=0.5):
        """docs: list of (Counter, n_articles); up: list of 1 (up day) / 0 (down day)."""
        df = Counter()
        for c, _ in docs:
            df.update(c.keys())
        common = [t for t, k in df.most_common() if k >= min_days][:max_vocab]
        self.vocab = {t: j for j, t in enumerate(common)}
        X, y = self._matrix(docs), np.array(up, dtype=float)
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-6
        Z = (X - self.mu) / self.sd
        w, b = np.zeros(Z.shape[1]), 0.0
        for _ in range(steps):  # gradient descent on the logistic loss + L2 penalty
            p = 1 / (1 + np.exp(-(Z @ w + b)))
            g = p - y
            w -= lr * (Z.T @ g / len(y) + l2 * w / len(y))
            b -= lr * g.mean()
        self.w, self.b = w, b
        return self

    def prob_up(self, docs):
        Z = (self._matrix(docs) - self.mu) / self.sd
        return 1 / (1 + np.exp(-(Z @ self.w + self.b)))

    def reasons(self, doc, k=4):
        """The words that pushed today's forecast up and down the most."""
        z = (self._matrix([doc])[0] - self.mu) / self.sd
        contrib = z * self.w
        words = {j: t for t, j in self.vocab.items()}
        order = np.argsort(contrib)
        up = [words[j].replace("_", " ") for j in order[::-1][:k] if contrib[j] > 0]
        down = [words[j].replace("_", " ") for j in order[:k] if contrib[j] < 0]
        return up, down

    def save(self, path=MODEL, **info):
        path.write_text(json.dumps({"vocab": self.vocab, "w": self.w.tolist(), "b": self.b,
                                    "mu": self.mu.tolist(), "sd": self.sd.tolist(), **info}))

    @classmethod
    def load(cls, path=MODEL):
        d = json.loads(path.read_text())
        m = cls(d["vocab"], d["w"], d["b"])
        m.mu, m.sd = np.array(d["mu"]), np.array(d["sd"])
        m.info = {k: v for k, v in d.items() if k not in ("vocab", "w", "b", "mu", "sd")}
        return m


# ---------- live use ----------
def fetch_premarket(env, day=None):
    """Today's pre-open market news from Alpaca's free feed."""
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest
    day = day or datetime.now(ET).date()
    a, b = premarket_window(day)
    b = min(b, datetime.now(ET))
    nc = NewsClient(env["ALPACA_API_KEY"], env["ALPACA_SECRET_KEY"])
    r = nc.get_news(NewsRequest(symbols="SPY,QQQ,DIA,IWM", start=a, end=b))  # the library pages by itself
    return [{"t": n.created_at.isoformat(), "h": n.headline, "s": n.summary or "", "sym": n.symbols}
            for n in r.data["news"]]


def latest_headlines(env, hours=24, n=15):
    """The newest market headlines (S&P 500, Nasdaq, Dow, small companies) for the dashboard."""
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest
    nc = NewsClient(env["ALPACA_API_KEY"], env["ALPACA_SECRET_KEY"])
    now = datetime.now(ET)
    r = nc.get_news(NewsRequest(symbols="SPY,QQQ,DIA,IWM", start=now - timedelta(hours=hours), end=now, limit=60))
    arts = [n for n in r.data["news"] if article_text({"h": n.headline})]
    arts.sort(key=lambda a: a.created_at, reverse=True)
    return [{"time": a.created_at, "headline": a.headline, "summary": a.summary or "", "url": a.url,
             "source": a.source} for a in arts[:n]]


def morning_read(env, day=None):
    """Today's news forecast: {prob_up, lean (+1/-1/0), up_words, down_words, headlines, n}."""
    day = day or datetime.now(ET).date()
    m = NewsBrain.load()
    arts = fetch_premarket(env, day)
    doc = daily_docs(arts, [day])[day]
    p = float(m.prob_up([doc])[0])
    band = m.info.get("band", 0.05)
    lean = 1 if p >= 0.5 + band else -1 if p <= 0.5 - band else 0
    up, down = m.reasons(doc)
    heads = [a["h"] for a in sorted(arts, key=lambda a: a["t"], reverse=True) if article_text(a)][:6]
    return {"day": str(day), "prob_up": p, "lean": lean, "up_words": up, "down_words": down,
            "headlines": heads, "n": doc[1]}

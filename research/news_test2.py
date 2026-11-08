"""Second chance for the news AI: stronger regularization, and two other targets:
 (a) tomorrow's S&P 500 direction from all of today's news,
 (b) whether tomorrow is a BIG move day (top 30% of swings): volatility is easier to predict than direction."""
import json
import sys
from collections import Counter
from datetime import datetime, time, timedelta
from pathlib import Path
import bisect

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from news_ai import ET, NewsBrain, article_text, tokens
import yfinance as yf

spy = yf.download("SPY", start="2015-06-01", auto_adjust=True, progress=False)["Close"].squeeze()
r = spy.pct_change()
nxt = r.shift(-1)
arts = sorted(((datetime.fromisoformat(a["t"]).astimezone(ET), a) for a in map(json.loads, open("data/news.jsonl"))),
              key=lambda x: x[0])
stamps = [t for t, _ in arts]
days = [d.date() for d in spy.index if d.year >= 2016][:-1]
docs = {}
for i, d in enumerate(days):
    a = datetime.combine(days[i - 1] if i else d - timedelta(days=1), time(15, 55), ET)
    b = datetime.combine(d, time(15, 55), ET)  # everything up to just before today's close
    lo, hi = bisect.bisect_left(stamps, a), bisect.bisect_left(stamps, b)
    c = Counter()
    for _, art in arts[lo:hi]:
        c.update(set(tokens(article_text(art))))
    docs[d] = (c, hi - lo)
idx = pd.to_datetime(days)
y_dir = (nxt.loc[idx] > 0).astype(int).values
absr = nxt.abs().loc[idx]
big_cut = absr.rolling(250, min_periods=100).quantile(0.7).shift(1)  # threshold from the past only
y_big = (absr > big_cut).astype(int).values
past_vol = r.abs().rolling(20).mean().loc[idx].values

for target, y in (("tomorrow up?", y_dir), ("tomorrow a big-swing day?", y_big)):
    for l2 in (3.0, 50.0, 300.0):
        P, Yt, V = [], [], []
        hits_by_year = []
        for Y in range(2019, 2027):
            tr = [i for i, d in enumerate(days) if d.year < Y and docs[d][1] > 0 and not np.isnan(big_cut.iloc[i])]
            te = [i for i, d in enumerate(days) if d.year == Y and not np.isnan(big_cut.iloc[i])]
            m = NewsBrain().fit([docs[days[i]] for i in tr], [y[i] for i in tr], l2=l2)
            p = m.prob_up([docs[days[i]] for i in te])
            P += list(p); Yt += [y[i] for i in te]; V += [past_vol[i] for i in te]
            base = max(np.mean([y[i] for i in tr]), 1 - np.mean([y[i] for i in tr]))
            hits_by_year.append(np.mean((p > 0.5) == np.array([y[i] for i in te])) > base)
        P, Yt, V = map(np.array, (P, Yt, V))
        from_past = np.corrcoef(V, Yt)[0, 1]
        both = np.corrcoef(np.c_[P, V].T)[0, 1]
        print(f"{target:26} l2={l2:<5g} right {np.mean((P > 0.5) == Yt):.1%} (always-guess {max(Yt.mean(), 1 - Yt.mean()):.1%}), "
              f"corr {np.corrcoef(P, Yt)[0, 1]:+.3f}, beat always-guess in {sum(hits_by_year)}/8 years"
              + (f" | past-swings alone corr {from_past:+.3f}" if 'big' in target else ""), flush=True)

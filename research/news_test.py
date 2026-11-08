"""Walk-forward test of the news AI: for each year 2019-2026, train only on earlier years, then
forecast that year's days blind. Shows whether the news forecast actually helps the day trading."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import intraday as I
from news_ai import NewsBrain, daily_docs

df = I.load()
arts = [json.loads(l) for l in open(I.DATA / "news.jsonl")]
g = df.groupby("day")
oc = (g.open.nth(0).values, None)
day_open, day_exit = g.open.first(), g.apply(lambda x: x[(x.index.hour == 15) & (x.index.minute == 50)].open.iloc[0]
                                             if ((x.index.hour == 15) & (x.index.minute == 50)).any() else x.close.iloc[-1])
ret = (day_exit / day_open - 1)
days = list(ret.index)
docs = daily_docs(arts, days)
print(f"{len(arts)} articles, {len(days)} trading days, median {np.median([docs[d][1] for d in days]):.0f} articles before each open")

probs = {}
for Y in range(2019, 2027):
    tr = [d for d in days if d.year < Y and docs[d][1] > 0]
    te = [d for d in days if d.year == Y]
    if not te:
        continue
    m = NewsBrain().fit([docs[d] for d in tr], [int(ret[d] > 0) for d in tr])
    p = m.prob_up([docs[d] for d in te])
    for d, x in zip(te, p):
        probs[d] = x if docs[d][1] > 0 else 0.5
    hit = np.mean([(probs[d] > 0.5) == (ret[d] > 0) for d in te])
    print(f"{Y}: trained on {len(tr)} days, guessed today's direction right {hit:.1%} of {len(te)} days", flush=True)

P = pd.Series(probs)
R = ret[P.index]
print(f"\nAll blind years: right {np.mean((P > 0.5) == (R > 0)):.1%} | base rate up days {np.mean(R > 0):.1%} | "
      f"correlation {np.corrcoef(P, R)[0, 1]:+.3f}")
for band in (0.03, 0.06, 0.10):
    sel = (P - 0.5).abs() >= band
    print(f"  when confident by {band:.0%}+: {sel.mean():.0%} of days, right {np.mean((P[sel] > 0.5) == (R[sel] > 0)):.1%}")

test = df[pd.to_datetime(df.day) >= "2019-01-01"]
print("\nBlind years 2019-2026 only:")
print(I.HEADER)
I.summary("Just hold QQQ", test.groupby("day").close.last().pct_change().fillna(0), 1.0)
I.summary("News only: buy or bet against at the open", R.where(P > 0.5, -R) - 0.0004)
I.summary("ORB buy only (now)", I.orb(test, 15))
I.summary("ORB buy + bet against", I.orb(test, 15, short=True))
for band in (0.0, 0.05):
    lean = {d: ("L" if p >= 0.5 + band else "S" if p <= 0.5 - band else "LS") for d, p in probs.items()}
    I.summary(f"ORB, side picked by news (band {band:.0%})", I.orb(test, 15, short=True, direction=lean))
    I.summary(f"ORB buy only, skip news-down days ({band:.0%})", I.orb(test, 15, direction={d: ("" if v == "S" else "L") for d, v in lean.items()}))

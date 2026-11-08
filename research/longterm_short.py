"""Does betting AGAINST the market when it's weak help the long-term strategy?
Daily S&P 500 data, 1994-today. Leveraged and inverse funds are simulated from the S&P 500's daily
moves (2x fund = 2x the daily move minus fees, -1x fund = minus the daily move minus fees).
Signals use yesterday's close, so there is no peeking."""
import numpy as np
import pandas as pd
import yfinance as yf

spy = yf.download("SPY", start="1993-01-01", auto_adjust=True, progress=False)["Close"].squeeze()
r = spy.pct_change().fillna(0)
avg200, avg50 = spy.rolling(200).mean(), spy.rolling(50).mean()
vol = r.rolling(20).std() * np.sqrt(252)
fee = 0.009 / 252  # yearly fund fee
healthy = spy > avg200
L = (0.25 / vol * 4).round() / 4
L = L.clip(0.25, 2.0).where(healthy, 0.0)


def daily(exposure):
    e = exposure.shift(1).fillna(0)  # decided at yesterday's close
    drag = fee * e.abs().clip(lower=0) * (e.abs() > 1)  # leveraged/inverse funds cost more
    turn = e.diff().abs().fillna(0) * 0.0005  # trading cost
    return e * r - drag - turn


variants = {
    "Now: cash when weak": L,
    "-1x (bet against) when weak": L.where(healthy, -1.0),
    "-1x only when below BOTH averages": L.where(healthy, np.where(spy < avg50, -1.0, 0.0)),
    "Just hold S&P 500": pd.Series(1.0, index=spy.index),
}
start = spy.index[260]
print(f"{'':38}{'per year':>9}{'worst':>8}{'1994-2009':>11}{'2010-2026':>11}")
for name, ex in variants.items():
    d = daily(pd.Series(ex, index=spy.index))[start:]
    eq = (1 + d).cumprod()
    yrs = len(d) / 252
    dd = (eq / eq.cummax() - 1).min()
    h = [(1 + d[a:b]).prod() ** (252 / len(d[a:b])) - 1 for a, b in (("1994", "2009"), ("2010", "2026"))]
    print(f"{name:38}{eq.iloc[-1] ** (1 / yrs) - 1:>9.1%}{dd:>8.0%}{h[0]:>11.1%}{h[1]:>11.1%}")

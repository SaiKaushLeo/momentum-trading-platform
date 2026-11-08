"""Everything that talks to a broker lives here, and nowhere else.

The rest of the bot (rules, explanations, approvals, Claude) only uses the seven
methods of Broker below. To add a new broker (Interactive Brokers, Schwab, ...):
  1. write a class that inherits Broker and fills in the seven methods
  2. add it to BROKERS at the bottom
  3. put BROKER=<its name> in .env, plus whatever keys it needs
Nothing else in the bot has to change.
"""


class Broker:
    name = "base"

    def market_is_open(self):
        """True if the stock market is open right now."""
        raise NotImplementedError

    def account(self):
        """(total account value, cash) in dollars."""
        raise NotImplementedError

    def positions(self):
        """{symbol: {"qty": shares owned, "entry": average price paid}}"""
        raise NotImplementedError

    def buy(self, symbol, qty):
        """Market order to buy qty shares, good for today only."""
        raise NotImplementedError

    def sell_all(self, symbol):
        """Sell every share of symbol at market."""
        raise NotImplementedError

    def sell(self, symbol, qty):
        """Market order to sell qty shares, good for today only."""
        raise NotImplementedError

    def latest_price(self, symbol):
        """Most recent trade price right now."""
        raise NotImplementedError

    # --- used by day trading ---
    def buy_and_wait(self, symbol, qty):
        """Market buy, wait for the fill. Returns the average fill price."""
        raise NotImplementedError

    def close_and_wait(self, symbol):
        """Sell the whole position at market, wait for the fill. Returns the average fill price."""
        raise NotImplementedError

    def sell_and_wait(self, symbol, qty):
        """Market sell of qty shares, wait for the fill (so the cash is there for the buys after it)."""
        raise NotImplementedError

    def stop_sell(self, symbol, qty, stop_price):
        """Stop order held by the broker (works even if this Mac is asleep). Returns its id."""
        raise NotImplementedError

    def order_status(self, order_id):
        """(status, average fill price or None)"""
        raise NotImplementedError

    def cancel(self, order_id):
        raise NotImplementedError


class AlpacaBroker(Broker):
    name = "Alpaca (paper)"

    def __init__(self, env):
        from alpaca.trading.client import TradingClient
        key, secret = env.get("ALPACA_API_KEY"), env.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise SystemExit("Missing keys: put your Alpaca PAPER keys in .env.")
        # paper=True is hard-coded on purpose: this bot can never touch real money.
        self.client = TradingClient(key, secret, paper=True)
        self.key, self.secret = key, secret

    def market_is_open(self):
        return self.client.get_clock().is_open

    def account(self):
        acct = self.client.get_account()
        return float(acct.equity), float(acct.cash)

    def positions(self):
        return {p.symbol: {"qty": int(float(p.qty)), "entry": float(p.avg_entry_price),
                           "price": float(p.current_price), "change_today": float(p.change_today or 0)}
                for p in self.client.get_all_positions()}

    def buy(self, symbol, qty):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        self.client.submit_order(MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY,
                                                    time_in_force=TimeInForce.DAY))

    def sell_all(self, symbol):
        self.client.close_position(symbol)

    def sell(self, symbol, qty):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        self.client.submit_order(MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.SELL,
                                                    time_in_force=TimeInForce.DAY))


    def latest_price(self, symbol):
        from alpaca.data.enums import DataFeed
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest
        data = StockHistoricalDataClient(self.key, self.secret)
        req = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        return float(data.get_stock_latest_trade(req)[symbol].price)


    def _wait(self, order_id, seconds=30):
        import time
        for _ in range(seconds * 2):
            o = self.client.get_order_by_id(order_id)
            if str(o.status.value) in ("filled", "canceled", "rejected", "expired"):
                return o
            time.sleep(0.5)
        return self.client.get_order_by_id(order_id)

    def buy_and_wait(self, symbol, qty):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        o = self.client.submit_order(MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY,
                                                        time_in_force=TimeInForce.DAY))
        o = self._wait(o.id)
        if o.filled_avg_price is None:
            raise RuntimeError(f"buy order not filled ({o.status.value})")
        return float(o.filled_avg_price)

    def close_and_wait(self, symbol):
        o = self._wait(self.client.close_position(symbol).id)
        if o.filled_avg_price is None:
            raise RuntimeError(f"sell order not filled ({o.status.value})")
        return float(o.filled_avg_price)

    def sell_and_wait(self, symbol, qty):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        o = self.client.submit_order(MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.SELL,
                                                        time_in_force=TimeInForce.DAY))
        o = self._wait(o.id)
        if o.filled_avg_price is None:
            raise RuntimeError(f"sell order not filled ({o.status.value})")
        return float(o.filled_avg_price)

    def stop_sell(self, symbol, qty, stop_price):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import StopOrderRequest
        o = self.client.submit_order(StopOrderRequest(symbol=symbol, qty=qty, side=OrderSide.SELL,
                                                      time_in_force=TimeInForce.GTC,  # stays on overnight if the 3:50 sale is missed
                                                      stop_price=round(stop_price, 2)))
        return str(o.id)

    def order_status(self, order_id):
        o = self.client.get_order_by_id(order_id)
        return str(o.status.value), (float(o.filled_avg_price) if o.filled_avg_price else None)

    def cancel(self, order_id):
        self.client.cancel_order_by_id(order_id)
        self._wait(order_id, 10)


class DryRunBroker(Broker):
    """Pretend account for --dry-run: $100k cash, owns nothing, never sends orders."""
    name = "dry run (nothing is sent)"

    def market_is_open(self):
        return True

    def account(self):
        return 100_000.0, 100_000.0

    def positions(self):
        return {}

    def buy(self, symbol, qty):
        pass

    def sell_all(self, symbol):
        pass

    def sell(self, symbol, qty):
        pass


BROKERS = {"alpaca": AlpacaBroker}


def get_broker(env, dry=False):
    if dry:
        return DryRunBroker()
    choice = env.get("BROKER", "alpaca").strip().lower()
    if choice not in BROKERS:
        raise SystemExit(f"Unknown BROKER={choice!r} in .env. Options: {', '.join(BROKERS)}")
    return BROKERS[choice](env)

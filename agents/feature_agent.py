"""Feature agent — same indicator engine as technical. No dummy SMAs.

Domain logic above; Agent wrapper at the bottom.
"""

from agents.base import Agent
from agents.indicator_engine import snapshot_only


def get_market_features(symbol="SPY", indicators=None):
    snap = snapshot_only(symbol, indicators)
    if snap.get("error"):
        return snap
    return {
        "symbol": snap.get("symbol", symbol),
        "price": snap.get("price"),
        "sma20": snap.get("sma20"),
        "sma50": snap.get("sma50"),
        "ema20": snap.get("ema20"),
        "trend": snap.get("trend", "NEUTRAL"),
        "rsi": snap.get("rsi"),
        "macd": snap.get("macd"),
        "atr": snap.get("atr"),
        "volume": snap.get("volume"),
    }


class FeatureAgent(Agent):
    node = "features"

    def run(self, ctx):
        return get_market_features(ctx["symbol"], ctx.get("indicators"))

    def message(self, out):
        return self._err(out) or f"{out.get('trend')} @ {out.get('price')}"

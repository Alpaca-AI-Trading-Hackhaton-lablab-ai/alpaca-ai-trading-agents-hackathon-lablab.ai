"""Market-state node — merge sentiment, options, features, technical.

Domain logic above; Agent wrapper at the bottom.
"""

from agents.base import Agent


def build_market_state(
    sentiment,
    options,
    features,
    technical
):

    return {
        "symbol": features.get("symbol", options.get("symbol", "SPY")),

        # Sentiment
        "sentiment": sentiment.get("sentiment", "NEUTRAL"),
        "confidence": sentiment.get("confidence", 0),
        "trade_bias": sentiment.get("trade_bias", "WAIT"),

        # Feature / technical engine
        "trend": features.get("trend", "NEUTRAL"),
        "price": features.get("price") or technical.get("price", 100.0),
        "rsi": technical.get("rsi", features.get("rsi")),
        "sma20": technical.get("sma20", features.get("sma20")),
        "sma50": technical.get("sma50", features.get("sma50")),
        "ema20": technical.get("ema20", features.get("ema20")),
        "macd": technical.get("macd", features.get("macd")),
        "macd_signal": technical.get("macd_signal"),
        "macd_hist": technical.get("macd_hist"),
        "atr": technical.get("atr", features.get("atr")),
        "volume": technical.get("volume", features.get("volume")),
        "technical_signal": technical.get("signal", "HOLD"),

        # Options Agent
        "option_strategy": options.get("strategy", "NO_TRADE"),
    }


class MarketStateAgent(Agent):
    node = "market_state"

    def run(self, ctx):
        return build_market_state(
            ctx["sentiment"], ctx["options"], ctx["features"], ctx["technical"]
        )

    def message(self, out):
        return self._err(out) or (
            f"{out.get('sentiment')} · {out.get('technical_signal')} · {out.get('trend')}"
        )

"""Options node — rule labels from sentiment. No LLM.

Domain logic above; Agent wrapper at the bottom.
"""

from agents.base import Agent


def options_strategy(sentiment, confidence, symbol="SPY"):
    symbol = (symbol or "SPY").upper()

    if sentiment == "BULLISH" and confidence >= 75:
        return {
            "symbol": symbol,
            "strategy": "LONG_CALL",
            "action": "BUY_CALL"
        }

    elif sentiment == "BEARISH" and confidence >= 75:
        return {
            "symbol": symbol,
            "strategy": "LONG_PUT",
            "action": "BUY_PUT"
        }

    else:
        return {
            "symbol": symbol,
            "strategy": "NO_TRADE",
            "action": "WAIT"
        }


class OptionsAgent(Agent):
    node = "options"

    def run(self, ctx):
        sentiment = ctx["sentiment"]
        return options_strategy(
            sentiment.get("sentiment", "NEUTRAL"),
            sentiment.get("confidence", 0),
            ctx["symbol"],
        )

    def message(self, out):
        return self._err(out) or f"{out.get('strategy')} / {out.get('action')}"

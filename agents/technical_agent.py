"""Technical analysis — deterministic indicator snapshot. Never an LLM.

Domain logic above; Agent wrapper at the bottom.
"""

from agents.base import Agent
from agents.indicator_engine import snapshot_only


def technical_analysis(symbol="SPY", indicators=None):
    return snapshot_only(symbol, indicators)


class TechnicalAgent(Agent):
    node = "technical"

    def run(self, ctx):
        return technical_analysis(ctx["symbol"], ctx.get("indicators"))

    def message(self, out):
        rsi3 = out.get("rsi3")
        rsi = out.get("rsi") if rsi3 is None else rsi3
        extra = f" (RSI {rsi})" if rsi is not None else ""
        return self._err(out) or f"{out.get('signal')}{extra}"

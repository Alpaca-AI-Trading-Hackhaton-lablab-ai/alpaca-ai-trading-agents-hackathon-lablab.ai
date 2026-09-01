"""Technical analysis — deterministic indicator snapshot. Never an LLM."""

from agents.indicator_engine import snapshot_only


def technical_analysis(symbol="SPY", indicators=None):
    return snapshot_only(symbol, indicators)

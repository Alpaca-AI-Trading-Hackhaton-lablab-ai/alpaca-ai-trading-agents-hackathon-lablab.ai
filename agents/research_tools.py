"""Read-only tool registry for the ReAct proposal loops.

Mirrors Gemini-Claw's whitelisted `ToolRegistry`: only these read-only
functions are reachable from a loop. No order / execute / gate tool is ever
registered here — the loops can gather evidence, never trade.
"""

from agents.feature_agent import get_market_features
from agents.institutional_flow import detect_smart_money
from agents.orderblock_engine import detect_orderblocks
from agents.technical_agent import technical_analysis
from services.alpaca_service import get_account_info
from services.concept_lookup import lookup_concept
from services.logs import recent_for_agents
from services.news_service import get_market_news


def recent_history(symbol="SPY", limit=10):
    """Read-only compact invocation history for this symbol. No secrets, no OHLCV."""
    return recent_for_agents(symbol, limit=limit)


# name -> read-only callable
REGISTRY = {
    "get_market_news": get_market_news,
    "get_market_features": get_market_features,
    "technical_analysis": technical_analysis,
    "get_account_info": get_account_info,
    "recent_history": recent_history,
    "lookup_concept": lookup_concept,
    "detect_orderblocks": detect_orderblocks,
    "detect_smart_money": detect_smart_money,
}


def subset(*names):
    """Return a {name: callable} dict for the given allowed tool names."""
    return {n: REGISTRY[n] for n in names if n in REGISTRY}


def run_tool(name, **params):
    """Validate against the allowlist and run. Off-list -> {"error": ...}."""
    fn = REGISTRY.get(name)
    if fn is None:
        return {"error": f"tool not allowed: {name}"}
    try:
        return fn(**params)
    except Exception as e:  # noqa: BLE001 - a bad tool call must not crash the caller
        return {"error": str(e)}

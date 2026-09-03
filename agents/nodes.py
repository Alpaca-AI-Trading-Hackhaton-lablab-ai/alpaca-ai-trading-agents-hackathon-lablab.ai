"""Pipeline registry: ordered Agent instances for one run.

Each Agent class lives at the bottom of its domain module. This file only
assembles them. `deep` swaps ReAct variants for sentiment and decision; the
gate stays a deterministic Agent. Execution is not in this list — nothing
reaches the broker except via `dispatch()` after `evaluate_gate()`. The SSE
graph is preview only.
"""

from agents.account_agent import AccountAgent
from agents.decision_agent import DecisionAgent, DecisionReactAgent
from agents.execution_gate import GateAgent
from agents.feature_agent import FeatureAgent
from agents.institutional_flow import InstitutionalAgent
from agents.market_state_agent import MarketStateAgent
from agents.news_agent import NewsAgent
from agents.options_agent import OptionsAgent
from agents.orderblock_engine import OrderblockAgent
from agents.risk_manager import RiskAgent
from agents.sentiment_agent import SentimentAgent, SentimentReactAgent
from agents.technical_agent import TechnicalAgent

# Trace keys, in topological order. Each node = one agent.
PIPELINE_KEYS = (
    "news",
    "sentiment",
    "options",
    "features",
    "technical",
    "orderblock",
    "institutional",
    "market_state",
    "account",
    "risk",
    "decision",
    "gate",
)


def build_pipeline(deep=False, deep_sentiment=None, deep_decision=None):
    """The ordered list of Agent instances for one run. `deep` swaps in the
    ReAct variants for sentiment and decision; everything else is deterministic.
    Per-node deep flags override the global `deep` when provided."""
    use_sent = deep if deep_sentiment is None else deep_sentiment
    use_dec = deep if deep_decision is None else deep_decision
    sentiment = SentimentReactAgent() if use_sent else SentimentAgent()
    decision = DecisionReactAgent() if use_dec else DecisionAgent()
    return [
        NewsAgent(),
        sentiment,
        OptionsAgent(),
        FeatureAgent(),
        TechnicalAgent(),
        OrderblockAgent(),
        InstitutionalAgent(),
        MarketStateAgent(),
        AccountAgent(),
        RiskAgent(),
        decision,
        GateAgent(),
    ]

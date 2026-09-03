"""Market-state node — merge sentiment, options, features, technical, OB, flow.

Domain logic above; Agent wrapper at the bottom. Compact fields only.
"""

from agents.base import Agent


def _pick(primary, secondary, key, default=None):
    if primary.get(key) is not None:
        return primary[key]
    if secondary.get(key) is not None:
        return secondary[key]
    return default


def build_market_state(
    sentiment,
    options,
    features,
    technical,
    orderblock=None,
    institutional=None,
):
    sentiment = sentiment or {}
    options = options or {}
    features = features or {}
    technical = technical or {}
    orderblock = orderblock or {}
    institutional = institutional or {}

    bullish_ob = orderblock.get("bullish_ob")
    bearish_ob = orderblock.get("bearish_ob")

    return {
        "symbol": features.get("symbol", options.get("symbol", "SPY")),
        "sentiment": sentiment.get("sentiment", "NEUTRAL"),
        "confidence": sentiment.get("confidence", 0),
        "trade_bias": sentiment.get("trade_bias", "WAIT"),
        "trend": features.get("trend", technical.get("trend", "NEUTRAL")),
        "ema_trend": _pick(technical, features, "ema_trend", "NEUTRAL"),
        "price": features.get("price") or technical.get("price") or orderblock.get("current_price") or 100.0,
        "rsi": _pick(technical, features, "rsi"),
        "rsi3": _pick(technical, features, "rsi3"),
        "rsi_signal": _pick(technical, features, "rsi_signal", "NEUTRAL"),
        "sma20": _pick(technical, features, "sma20"),
        "sma50": _pick(technical, features, "sma50"),
        "ema3": _pick(technical, features, "ema3"),
        "ema10": _pick(technical, features, "ema10"),
        "ema20": _pick(technical, features, "ema20"),
        "ema50": _pick(technical, features, "ema50"),
        "ema100": _pick(technical, features, "ema100"),
        "macd": _pick(technical, features, "macd"),
        "macd_signal": technical.get("macd_signal"),
        "macd_hist": technical.get("macd_hist"),
        "atr": _pick(technical, features, "atr"),
        "volume": _pick(technical, features, "volume"),
        "technical_signal": technical.get("signal", "HOLD"),
        "option_strategy": options.get("strategy", "NO_TRADE"),
        "bullish_ob": bullish_ob,
        "bearish_ob": bearish_ob,
        "near_bullish": bool(orderblock.get("near_bullish")),
        "near_bearish": bool(orderblock.get("near_bearish")),
        "smart_money_buying": bool(institutional.get("smart_money_buying")),
        "smart_money_selling": bool(institutional.get("smart_money_selling")),
        "institutional_signal": institutional.get("institutional_signal", "NEUTRAL"),
        "ad_line_trend": institutional.get("ad_line_trend", "NEUTRAL"),
        "volume_ratio": institutional.get("volume_ratio"),
    }


class MarketStateAgent(Agent):
    node = "market_state"

    def run(self, ctx):
        return build_market_state(
            ctx.get("sentiment") or {},
            ctx.get("options") or {},
            ctx.get("features") or {},
            ctx.get("technical") or {},
            ctx.get("orderblock") or {},
            ctx.get("institutional") or {},
        )

    def message(self, out):
        return self._err(out) or (
            f"{out.get('sentiment')} · {out.get('technical_signal')} · {out.get('trend')}"
        )

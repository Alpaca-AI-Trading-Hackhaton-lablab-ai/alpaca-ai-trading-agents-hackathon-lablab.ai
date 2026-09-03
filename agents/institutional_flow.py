"""Institutional / smart-money flow from bar volume + accumulation/distribution.

Deterministic. Bars only — no tick tape, no LLM. Compact snapshot.
Domain logic above; Agent wrapper at the bottom.
"""

from agents.base import Agent
from agents.indicator_engine import TF_HOUR, _finite, bars_frame
from services import cache


def _empty(symbol, error=None):
    out = {
        "symbol": (symbol or "SPY").upper(),
        "smart_money_buying": False,
        "smart_money_selling": False,
        "volume_ratio": None,
        "ad_line_trend": "NEUTRAL",
        "institutional_signal": "NEUTRAL",
    }
    if error:
        out["error"] = error
    return out


def _detect_uncached(symbol):
    df = bars_frame(symbol, timeframe=TF_HOUR, limit=200)
    if df is None or len(df) < 22:
        return _empty(symbol, "No bars data")

    frame = df.copy()
    hl = frame["high"] - frame["low"]
    # Fail-closed on doji / zero range: multiplier 0, no invented flow.
    mfm = (
        ((frame["close"] - frame["low"]) - (frame["high"] - frame["close"])) / hl
    ).where(hl != 0, 0.0)
    mf_vol = mfm * frame["volume"]
    frame["ad_line"] = mf_vol.cumsum()
    frame["avg_volume"] = frame["volume"].rolling(20).mean()
    frame["volume_ratio"] = frame["volume"] / frame["avg_volume"]

    last = frame.iloc[-1]
    prev = frame.iloc[-2]
    volume_ratio = _finite(last["volume_ratio"])
    last_ad = last["ad_line"]
    prev_ad = prev["ad_line"]
    last_close = last["close"]
    prev_close = prev["close"]

    smart_buy = bool(
        volume_ratio is not None
        and volume_ratio > 1.5
        and last_close > prev_close
        and last_ad > prev_ad
    )
    smart_sell = bool(
        volume_ratio is not None
        and volume_ratio > 1.5
        and last_close < prev_close
        and last_ad < prev_ad
    )

    ad_mean = frame["ad_line"].mean()
    if last_ad > ad_mean:
        ad_trend = "ACCUMULATING"
    elif last_ad < ad_mean:
        ad_trend = "DISTRIBUTING"
    else:
        ad_trend = "NEUTRAL"

    if smart_buy:
        signal = "BUY"
    elif smart_sell:
        signal = "SELL"
    else:
        signal = "NEUTRAL"

    return {
        "symbol": (symbol or "SPY").upper(),
        "smart_money_buying": smart_buy,
        "smart_money_selling": smart_sell,
        "volume_ratio": volume_ratio,
        "ad_line_trend": ad_trend,
        "institutional_signal": signal,
    }


def detect_smart_money(symbol="SPY"):
    symbol = (symbol or "SPY").upper()
    return cache.cached(
        "institutional",
        symbol,
        TF_HOUR,
        cache.TTL_INDICATORS,
        lambda: _detect_uncached(symbol),
    )


class InstitutionalAgent(Agent):
    node = "institutional"

    def run(self, ctx):
        return detect_smart_money(ctx["symbol"])

    def message(self, out):
        if self._err(out):
            return self._err(out)
        ratio = out.get("volume_ratio")
        extra = f" · vol {ratio:.1f}x" if isinstance(ratio, (int, float)) else ""
        return f"{out.get('institutional_signal', 'NEUTRAL')}{extra}"

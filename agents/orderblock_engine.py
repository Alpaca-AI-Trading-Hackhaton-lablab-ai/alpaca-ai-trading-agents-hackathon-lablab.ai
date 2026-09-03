"""Order-block detector — last opposing candle before a strong daily move.

Deterministic. Compact snapshot only (no candle array to the LLM).
The zone is the *prior* opposing bar (expert prose), not the impulse bar.
Domain logic above; Agent wrapper at the bottom.
"""

from agents.base import Agent
from agents.indicator_engine import TF_DAY, _finite, bars_frame
from services import cache

_NEAR_PCT = 0.01


def _empty(symbol, error=None):
    out = {
        "symbol": (symbol or "SPY").upper(),
        "current_price": None,
        "bullish_ob": None,
        "bearish_ob": None,
        "near_bullish": False,
        "near_bearish": False,
        "ob_count": {"bullish": 0, "bearish": 0},
    }
    if error:
        out["error"] = error
    return out


def _distance_pct(price, level):
    if price is None or level is None or level == 0:
        return None
    return abs(price - level) / abs(level)


def _zone(frame, impulse_idx, kind):
    """Zone = prior opposing candle. Bullish → HIGH of the bear bar; bearish → LOW of the bull bar."""
    if impulse_idx is None or impulse_idx < 1:
        return None
    prior = frame.iloc[impulse_idx - 1]
    low = _finite(prior["low"])
    high = _finite(prior["high"])
    if low is None or high is None:
        return None
    if kind == "bullish":
        return {"price": high, "low": low, "high": high, "level": "HIGH", "distance_pct": None}
    return {"price": low, "low": low, "high": high, "level": "LOW", "distance_pct": None}


def _last_index(mask):
    idxs = mask[mask].index.tolist()
    return int(idxs[-1]) if idxs else None


def detect_from_frame(frame, symbol="SPY"):
    """Pure detector over a prepared OHLC frame. Used by cache + tests."""
    symbol = (symbol or "SPY").upper()
    if frame is None or len(frame) < 22:
        return _empty(symbol, "No bars data")

    work = frame.reset_index(drop=True).copy()
    work["range"] = work["high"] - work["low"]
    work["is_bullish"] = work["close"] > work["open"]
    work["is_bearish"] = work["close"] < work["open"]
    avg_range = work["range"].rolling(20).mean()
    work["strong_move"] = work["range"] > (avg_range * 1.5)

    prev_bear = work["is_bearish"].shift(1) == True
    prev_bull = work["is_bullish"].shift(1) == True
    prev_high = work["high"].shift(1)
    prev_low = work["low"].shift(1)

    work["ob_bullish"] = (
        prev_bear
        & work["is_bullish"]
        & (work["strong_move"] == True)
        & (work["close"] > prev_high)
    )
    work["ob_bearish"] = (
        prev_bull
        & work["is_bearish"]
        & (work["strong_move"] == True)
        & (work["close"] < prev_low)
    )

    current = _finite(work["close"].iloc[-1])
    bullish_ob = _zone(work, _last_index(work["ob_bullish"]), "bullish")
    bearish_ob = _zone(work, _last_index(work["ob_bearish"]), "bearish")
    if bullish_ob:
        bullish_ob["distance_pct"] = _finite(_distance_pct(current, bullish_ob["price"]))
    if bearish_ob:
        bearish_ob["distance_pct"] = _finite(_distance_pct(current, bearish_ob["price"]))

    near_bull = bool(
        bullish_ob
        and bullish_ob["distance_pct"] is not None
        and bullish_ob["distance_pct"] < _NEAR_PCT
    )
    near_bear = bool(
        bearish_ob
        and bearish_ob["distance_pct"] is not None
        and bearish_ob["distance_pct"] < _NEAR_PCT
    )

    return {
        "symbol": symbol,
        "current_price": current,
        "bullish_ob": bullish_ob,
        "bearish_ob": bearish_ob,
        "near_bullish": near_bull,
        "near_bearish": near_bear,
        "ob_count": {
            "bullish": int(work["ob_bullish"].sum()),
            "bearish": int(work["ob_bearish"].sum()),
        },
    }


def _detect_uncached(symbol):
    return detect_from_frame(bars_frame(symbol, timeframe=TF_DAY, limit=200), symbol)


def detect_orderblocks(symbol="SPY"):
    symbol = (symbol or "SPY").upper()
    return cache.cached(
        "orderblock",
        symbol,
        TF_DAY,
        cache.TTL_INDICATORS,
        lambda: _detect_uncached(symbol),
    )


class OrderblockAgent(Agent):
    node = "orderblock"

    def run(self, ctx):
        return detect_orderblocks(ctx["symbol"])

    def message(self, out):
        if self._err(out):
            return self._err(out)
        if out.get("near_bullish"):
            zone = out.get("bullish_ob") or {}
            price = zone.get("price")
            extra = f" @ {price}" if price is not None else ""
            return f"near bullish{extra}"
        if out.get("near_bearish"):
            zone = out.get("bearish_ob") or {}
            price = zone.get("price")
            extra = f" @ {price}" if price is not None else ""
            return f"near bearish{extra}"
        counts = out.get("ob_count") or {}
        return f"{counts.get('bullish', 0)} bull / {counts.get('bearish', 0)} bear"

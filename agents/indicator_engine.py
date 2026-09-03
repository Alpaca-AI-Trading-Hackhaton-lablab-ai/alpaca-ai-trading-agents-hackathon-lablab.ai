"""Deterministic indicator engine.

Computes OHLC candles plus SMA/EMA/RSI/MACD/ATR/volume from Alpaca (or demo)
bars. Shared by GET /bars (human chart) and the feature/technical agents.
The LLM never sees the candle array — only a compact last-bar snapshot.
"""

import math

import pandas as pd

from services import cache
from services.alpaca_service import get_spy_bars
from services.schemas import bars_adapter

INDICATOR_IDS = (
    "sma20",
    "sma50",
    "ema20",
    "ema3",
    "ema10",
    "ema50",
    "ema100",
    "rsi",
    "rsi3",
    "macd",
    "volume",
    "atr",
)

_DEFAULT = list(INDICATOR_IDS)


def parse_indicators(raw):
    """Comma-separated query string -> allowlisted ids. Empty -> all defaults."""
    if not raw:
        return list(_DEFAULT)
    wanted = [part.strip().lower() for part in str(raw).split(",") if part.strip()]
    picked = [name for name in wanted if name in INDICATOR_IDS]
    return picked or list(_DEFAULT)


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, 4)


def _unix(ts):
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return int(stamp.timestamp())


def _prepare(df):
    if df is None or (isinstance(df, dict) and df.get("error")):
        return None
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    frame = df.copy()
    if "timestamp" not in frame.columns:
        if "time" in frame.columns:
            frame = frame.rename(columns={"time": "timestamp"})
        else:
            frame = frame.reset_index()
            if "timestamp" not in frame.columns:
                for col in frame.columns:
                    if "time" in str(col).lower() or "date" in str(col).lower():
                        frame = frame.rename(columns={col: "timestamp"})
                        break
            if "timestamp" not in frame.columns and len(frame.columns):
                first = frame.columns[0]
                if pd.api.types.is_datetime64_any_dtype(frame[first]):
                    frame = frame.rename(columns={first: "timestamp"})
    for col in ("open", "high", "low", "close", "volume"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["close"]).reset_index(drop=True)
    return frame if not frame.empty else None


def _rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _atr(frame, period=14):
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    prev = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev).abs(), (low - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


TF_HOUR = "1Hour"
TF_DAY = "1Day"


def bars_frame(symbol="SPY", timeframe=TF_DAY, limit=200):
    """Prepared OHLC DataFrame or None. Shared by indicator / OB / flow engines."""
    return _prepare(get_spy_bars(symbol, timeframe=timeframe, limit=limit))


def _points(times, series):
    out = []
    for ts, value in zip(times, series, strict=False):
        number = _finite(value)
        if number is None:
            continue
        out.append({"time": ts, "value": number})
    return out


def _ema_trend(ema3, ema10, ema50, ema100):
    if None in (ema3, ema10, ema50, ema100):
        return None
    if ema3 > ema10 > ema50 > ema100:
        return "BULLISH"
    if ema3 < ema10 < ema50 < ema100:
        return "BEARISH"
    return "NEUTRAL"


def _rsi_band(rsi3):
    if rsi3 is None:
        return "NEUTRAL"
    if rsi3 < 30:
        return "OVERSOLD"
    if rsi3 > 70:
        return "OVERBOUGHT"
    return "NEUTRAL"


def compute_pack(symbol="SPY", indicators=None):
    """Return candles, overlay series, oscillators, and a last-bar snapshot."""
    enabled = parse_indicators(
        ",".join(indicators) if isinstance(indicators, (list, tuple)) else indicators
    )
    suffix = f"{','.join(enabled)}|{TF_HOUR}"
    symbol = (symbol or "SPY").upper()
    return cache.cached(
        "technical",
        symbol,
        suffix,
        cache.TTL_INDICATORS,
        lambda: _compute_pack_uncached(symbol, enabled),
        adapter=bars_adapter,
    )


def _compute_pack_uncached(symbol, enabled):
    enabled_set = set(enabled)
    df = bars_frame(symbol, timeframe=TF_HOUR, limit=200)
    if df is None:
        return {"error": "No bars data", "symbol": (symbol or "SPY").upper()}

    close = df["close"]
    times = [_unix(ts) for ts in df["timestamp"]]
    candles = []
    for i, row in df.iterrows():
        o, h, low, c = (
            _finite(row.get("open")),
            _finite(row.get("high")),
            _finite(row.get("low")),
            _finite(row.get("close")),
        )
        if None in (o, h, low, c):
            continue
        candles.append(
            {
                "time": times[i],
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": _finite(row.get("volume")) or 0,
            }
        )

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    ema3 = close.ewm(span=3, adjust=False).mean()
    ema10 = close.ewm(span=10, adjust=False).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema100 = close.ewm(span=100, adjust=False).mean()
    rsi = _rsi(close)
    rsi3 = _rsi(close, period=3)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal
    atr = _atr(df) if "high" in df.columns else pd.Series(dtype=float)
    vol_ma = df["volume"].rolling(20).mean() if "volume" in df.columns else None

    overlays = {}
    oscillators = {}
    if "sma20" in enabled_set:
        overlays["sma20"] = _points(times, sma20)
    if "sma50" in enabled_set:
        overlays["sma50"] = _points(times, sma50)
    if "ema3" in enabled_set:
        overlays["ema3"] = _points(times, ema3)
    if "ema10" in enabled_set:
        overlays["ema10"] = _points(times, ema10)
    if "ema20" in enabled_set:
        overlays["ema20"] = _points(times, ema20)
    if "ema50" in enabled_set:
        overlays["ema50"] = _points(times, ema50)
    if "ema100" in enabled_set:
        overlays["ema100"] = _points(times, ema100)
    if "macd" in enabled_set:
        overlays["macd_hist"] = _points(times, macd_hist)
    if "rsi" in enabled_set:
        oscillators["rsi"] = _points(times, rsi)
    if "rsi3" in enabled_set:
        oscillators["rsi3"] = _points(times, rsi3)

    last = candles[-1] if candles else {}
    sma20_last = _finite(sma20.iloc[-1])
    sma50_last = _finite(sma50.iloc[-1])
    rsi_last = _finite(rsi.iloc[-1])
    ema3_last = _finite(ema3.iloc[-1])
    ema10_last = _finite(ema10.iloc[-1])
    ema50_last = _finite(ema50.iloc[-1])
    ema100_last = _finite(ema100.iloc[-1])
    rsi3_last = _finite(rsi3.iloc[-1])
    ema_trend = _ema_trend(ema3_last, ema10_last, ema50_last, ema100_last)
    rsi_signal = _rsi_band(rsi3_last)

    if ema_trend is not None:
        trend = ema_trend
        if rsi_signal == "OVERSOLD" and ema_trend != "BEARISH":
            signal = "BUY"
        elif rsi_signal == "OVERBOUGHT" and ema_trend != "BULLISH":
            signal = "SELL"
        else:
            signal = "HOLD"
    else:
        trend = "NEUTRAL"
        if sma20_last is not None and sma50_last is not None:
            trend = "BULLISH" if sma20_last > sma50_last else "BEARISH"
        if rsi_last is not None and sma20_last is not None and sma50_last is not None:
            if rsi_last > 60 and sma20_last > sma50_last:
                signal = "BUY"
            elif rsi_last < 40 and sma20_last < sma50_last:
                signal = "SELL"
            else:
                signal = "HOLD"
        else:
            signal = "HOLD"

    snapshot = {
        "symbol": (symbol or "SPY").upper(),
        "price": last.get("close"),
        "signal": signal,
        "trend": trend,
        "ema_trend": ema_trend or "NEUTRAL",
        "rsi_signal": rsi_signal,
    }
    if "sma20" in enabled_set:
        snapshot["sma20"] = sma20_last
    if "sma50" in enabled_set:
        snapshot["sma50"] = sma50_last
    if "ema3" in enabled_set:
        snapshot["ema3"] = ema3_last
    if "ema10" in enabled_set:
        snapshot["ema10"] = ema10_last
    if "ema20" in enabled_set:
        snapshot["ema20"] = _finite(ema20.iloc[-1])
    if "ema50" in enabled_set:
        snapshot["ema50"] = ema50_last
    if "ema100" in enabled_set:
        snapshot["ema100"] = ema100_last
    if "rsi" in enabled_set:
        snapshot["rsi"] = rsi_last
    if "rsi3" in enabled_set:
        snapshot["rsi3"] = rsi3_last
    if "macd" in enabled_set:
        snapshot["macd"] = _finite(macd.iloc[-1])
        snapshot["macd_signal"] = _finite(macd_signal.iloc[-1])
        snapshot["macd_hist"] = _finite(macd_hist.iloc[-1])
    if "atr" in enabled_set:
        snapshot["atr"] = _finite(atr.iloc[-1]) if len(atr) else None
    if "volume" in enabled_set:
        snapshot["volume"] = last.get("volume")
        snapshot["volume_sma20"] = _finite(vol_ma.iloc[-1]) if vol_ma is not None else None

    volume_overlay = []
    if "volume" in enabled_set:
        for bar in candles:
            color = "#0ecb81" if bar["close"] >= bar["open"] else "#f6465d"
            volume_overlay.append(
                {"time": bar["time"], "value": bar["volume"], "color": color}
            )

    return {
        "symbol": snapshot["symbol"],
        "indicators": enabled,
        "candles": candles,
        "overlays": overlays,
        "oscillators": oscillators,
        "volume": volume_overlay,
        "snapshot": snapshot,
    }


def snapshot_only(symbol="SPY", indicators=None):
    pack = compute_pack(symbol, indicators)
    if pack.get("error"):
        return pack
    return pack["snapshot"]


def filter_snapshot(snapshot, wanted):
    """Keep identity / market-state keys plus the requested indicator fields."""
    if not wanted or not isinstance(snapshot, dict):
        return snapshot
    keep = {
        "symbol",
        "price",
        "signal",
        "trend",
        "error",
        "sentiment",
        "confidence",
        "trade_bias",
        "technical_signal",
        "option_strategy",
        "ema_trend",
        "rsi_signal",
        "near_bullish",
        "near_bearish",
        "institutional_signal",
        "smart_money_buying",
        "smart_money_selling",
        *wanted,
        "macd_signal",
        "macd_hist",
        "volume_sma20",
    }
    if "macd" not in wanted:
        keep.discard("macd_signal")
        keep.discard("macd_hist")
    if "volume" not in wanted:
        keep.discard("volume_sma20")
    return {k: v for k, v in snapshot.items() if k in keep}

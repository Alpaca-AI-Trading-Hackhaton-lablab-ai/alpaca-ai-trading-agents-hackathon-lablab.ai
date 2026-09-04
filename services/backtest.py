"""Offline walk-forward using the live score/risk/gate/qty path. Never submits."""

from __future__ import annotations

from agents.decision_agent import make_decision, score_setup
from agents.execution_gate import evaluate_gate
from agents.risk_manager import calculate_risk
from services.bracket_plan import seed_plan


def _num(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bars(bars):
    out = []
    for i, raw in enumerate(bars or []):
        if isinstance(raw, dict):
            close = _num(raw.get("close") if raw.get("close") is not None else raw.get("price"))
            if close is None:
                continue
            high = _num(raw.get("high"), close)
            low = _num(raw.get("low"), close)
            open_ = _num(raw.get("open"), close)
            out.append(
                {
                    "open": open_,
                    "high": high if high >= close else close,
                    "low": low if low <= close else close,
                    "close": close,
                    "volume": _num(raw.get("volume"), 0.0) or 0.0,
                    "timestamp": raw.get("timestamp") or raw.get("time") or i,
                }
            )
            continue
        close = _num(raw)
        if close is None:
            continue
        out.append(
            {
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 0.0,
                "timestamp": i,
            }
        )
    return out


def compact_state(bars, symbol="SPY"):
    """Features/technical compact enough for score_setup. No network."""
    rows = _as_bars(bars)
    symbol = str(symbol or "SPY").upper()
    if not rows:
        return {
            "symbol": symbol,
            "price": None,
            "atr": None,
            "technical_signal": "HOLD",
            "rsi_signal": "NEUTRAL",
            "sentiment": "NEUTRAL",
            "error": "no bars",
        }
    closes = [r["close"] for r in rows]
    price = closes[-1]
    window = closes[-20:] if len(closes) >= 2 else closes
    sma = sum(window) / len(window)
    ranges = [abs(r["high"] - r["low"]) for r in rows[-14:]]
    atr = (sum(ranges) / len(ranges)) if ranges else price * 0.01
    if atr <= 0:
        atr = price * 0.01
    if price > sma:
        technical = "BUY"
    elif price < sma:
        technical = "SELL"
    else:
        technical = "HOLD"
    if sma > 0 and price < sma * 0.98:
        rsi_signal = "OVERSOLD"
    elif sma > 0 and price > sma * 1.02:
        rsi_signal = "OVERBOUGHT"
    else:
        rsi_signal = "NEUTRAL"
    return {
        "symbol": symbol,
        "price": price,
        "atr": atr,
        "technical_signal": technical,
        "rsi_signal": rsi_signal,
        "sentiment": "NEUTRAL",
        "near_bullish": False,
        "near_bearish": False,
        "smart_money_buying": False,
        "smart_money_selling": False,
    }


def _positions(pos):
    if not pos:
        return []
    return [
        {
            "symbol": pos["symbol"],
            "side": "long" if pos["side"] == "buy" else "short",
            "qty": pos["qty"],
        }
    ]


def _close_hit(pos, bar):
    side = pos["side"]
    sl = pos.get("sl")
    tp = pos.get("tp")
    high = bar["high"]
    low = bar["low"]
    if side == "buy":
        if sl is not None and low <= sl:
            return sl, "sl"
        if tp is not None and high >= tp:
            return tp, "tp"
    else:
        if sl is not None and high >= sl:
            return sl, "sl"
        if tp is not None and low <= tp:
            return tp, "tp"
    return None, None


def run_backtest(bars=None, symbol="SPY", equity=100000.0):
    """Walk bars through score_setup + risk + gate + seed_plan. Never submit_*."""
    symbol = str(symbol or "SPY").upper()
    if bars is None:
        from services.alpaca_service import get_spy_bars

        raw = get_spy_bars(symbol)
        if hasattr(raw, "to_dict"):
            bars = raw.to_dict("records")
        else:
            bars = raw
    series = _as_bars(bars)
    cash = float(equity or 100000.0)
    start = cash
    position = None
    trades = []
    verdicts = []
    curve = []

    for i, bar in enumerate(series):
        if position:
            exit_px, why = _close_hit(position, bar)
            if exit_px is not None:
                qty = position["qty"]
                if position["side"] == "buy":
                    pnl = (exit_px - position["entry"]) * qty
                else:
                    pnl = (position["entry"] - exit_px) * qty
                cash += position["notional"] + pnl
                trades.append(
                    {
                        "symbol": symbol,
                        "side": position["side"],
                        "qty": qty,
                        "entry": position["entry"],
                        "exit": exit_px,
                        "pnl": round(pnl, 2),
                        "reason": why,
                        "bar": i,
                    }
                )
                position = None

        ms = compact_state(series[: i + 1], symbol)
        scores = score_setup(ms)
        pos_rows = _positions(position)
        risk = calculate_risk(
            cash,
            50,
            atr=ms.get("atr"),
            price=ms.get("price"),
            scores=scores,
            positions=pos_rows,
            symbol=symbol,
            intended_action="BUY" if scores.get("buy", 0) >= scores.get("sell", 0) else "SELL",
        )
        decision = make_decision(ms, risk)
        seeded = seed_plan(decision, ms.get("price"), ms.get("atr"))
        qty = ((seeded or {}).get("size") or {}).get("qty") or 0
        account = {"equity": cash, "buying_power": cash, "mode": "paper"}
        clock = {"is_open": True}
        gate = evaluate_gate(
            decision,
            account,
            pos_rows,
            [],
            clock,
            plan=seeded if seeded and int(qty) >= 1 else None,
        )
        verdicts.append(
            {
                "bar": i,
                "price": ms.get("price"),
                "action": decision.get("action"),
                "verdict": gate.get("verdict"),
                "qty": int(qty) if seeded else 0,
                "scores": scores,
            }
        )
        if (
            position is None
            and gate.get("verdict") == "ALLOW"
            and seeded
            and int(qty) >= 1
        ):
            notional = min(float(seeded["size"]["notional"]), cash)
            share_qty = int(qty)
            cost = share_qty * bar["close"]
            if cost > 0 and cost <= cash:
                cash -= cost
                sl = ((seeded.get("sl") or {}).get("price"))
                tps = seeded.get("tps") or []
                tp = (tps[0] or {}).get("price") if tps else None
                position = {
                    "symbol": symbol,
                    "side": seeded["side"],
                    "qty": share_qty,
                    "entry": bar["close"],
                    "notional": cost,
                    "sl": sl,
                    "tp": tp,
                }

        mark = cash
        if position:
            if position["side"] == "buy":
                mark += position["qty"] * bar["close"]
            else:
                mark += position["notional"] + (position["entry"] - bar["close"]) * position["qty"]
        curve.append(round(mark, 2))

    if position:
        last = series[-1]["close"]
        qty = position["qty"]
        if position["side"] == "buy":
            pnl = (last - position["entry"]) * qty
        else:
            pnl = (position["entry"] - last) * qty
        cash += position["notional"] + pnl
        trades.append(
            {
                "symbol": symbol,
                "side": position["side"],
                "qty": qty,
                "entry": position["entry"],
                "exit": last,
                "pnl": round(pnl, 2),
                "reason": "eod",
                "bar": len(series) - 1,
            }
        )
        position = None

    final = cash if not curve else curve[-1]
    return {
        "symbol": symbol,
        "equity": round(final, 2),
        "start_equity": start,
        "trades": trades,
        "verdicts": verdicts,
        "equity_curve": curve,
    }

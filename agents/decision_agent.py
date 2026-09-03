"""Decision node — scored setup (EMA/RSI3/OB/flow) plus optional ReAct loop.

Domain logic above; Agent wrappers at the bottom. The LLM (deep mode) picks
the action for the focus symbol (HOLD/CANCEL overlay); universe place intents
stay deterministic via intents_from_book. Position size always comes from the
deterministic risk node. Compact OMS snapshot (working + positions) is in ctx
on both the SSE pipeline and the scheduler tick.
"""

from agents.base import Agent, ReactAgent
from agents.feature_agent import get_market_features
from agents.indicator_engine import filter_snapshot
from agents.institutional_flow import detect_smart_money
from agents.orderblock_engine import detect_orderblocks
from agents.react_core import extract_json
from agents.technical_agent import technical_analysis
from agents import research_tools
from services import config, logs
from services.news_service import get_market_news

SCORE_THRESHOLD = 2.5


def score_setup(market_state):
    """Deterministic buy/sell scores. Missing fields add 0 (fail-closed)."""
    ms = market_state or {}
    buy = 0.0
    sell = 0.0

    tech = ms.get("technical_signal") or ms.get("signal")
    if tech == "BUY":
        buy += 2
    elif tech == "SELL":
        sell += 2

    rsi_sig = ms.get("rsi_signal")
    if rsi_sig == "OVERSOLD":
        buy += 1.5
    elif rsi_sig == "OVERBOUGHT":
        sell += 1.5

    if ms.get("near_bullish"):
        buy += 3
    if ms.get("near_bearish"):
        sell += 3

    if ms.get("smart_money_buying"):
        buy += 2
    if ms.get("smart_money_selling"):
        sell += 2

    sentiment = ms.get("sentiment")
    if sentiment in ("POSITIVE", "BULLISH"):
        buy += 1
    elif sentiment in ("NEGATIVE", "BEARISH"):
        sell += 1

    return {"buy": buy, "sell": sell}


def _orderblock_label(ms):
    if ms.get("near_bullish"):
        return "BULLISH"
    if ms.get("near_bearish"):
        return "BEARISH"
    if (ms.get("bullish_ob") or {}).get("price"):
        return "BULLISH"
    if (ms.get("bearish_ob") or {}).get("price"):
        return "BEARISH"
    return "NONE"


def _signals(ms):
    return {
        "technical": ms.get("technical_signal"),
        "rsi": ms.get("rsi_signal"),
        "orderblock": _orderblock_label(ms),
        "institutional": ms.get("institutional_signal"),
        "sentiment": ms.get("sentiment"),
    }


def compact_working_book(open_orders):
    """Proposal-side OMS snapshot. Short ids; never candles."""
    rows = []
    for order in open_orders or []:
        oid = str(order.get("order_id") or order.get("id") or "")
        rows.append(
            {
                "symbol": (order.get("symbol") or "").upper(),
                "side": str(order.get("side") or "").lower().split(".")[-1],
                "order_id": oid[:8],
                "notional": order.get("notional"),
            }
        )
    return rows


def compact_positions(positions):
    rows = []
    for pos in positions or []:
        rows.append(
            {
                "symbol": (pos.get("symbol") or "").upper(),
                "side": str(pos.get("side") or "").lower().split(".")[-1],
                "qty": pos.get("qty"),
                "market_value": pos.get("market_value"),
            }
        )
    return rows


def oms_snapshot(open_orders=None, positions=None):
    return {
        "working_book": compact_working_book(open_orders),
        "positions": compact_positions(positions),
    }


def _working_action(order):
    side = str((order or {}).get("side") or "").lower().split(".")[-1]
    if side == "buy":
        return "BUY"
    if side == "sell":
        return "SELL"
    return None


def apply_focus_action(intents, action, symbol, open_orders):
    """Deep: LLM HOLD/opposite cancels focus working. Universe places stay scored."""
    symbol = (symbol or "").upper()
    action = str(action or "HOLD").upper()
    if action not in ("BUY", "SELL", "HOLD"):
        action = "HOLD"
    others = [
        i for i in (intents or []) if (i.get("symbol") or "").upper() != symbol
    ]
    existing = next(
        (
            o
            for o in (open_orders or [])
            if (o.get("symbol") or "").upper() == symbol
        ),
        None,
    )
    placed = _working_action(existing) if existing else None
    opposite = action in ("BUY", "SELL") and placed and action != placed
    if action == "HOLD" or opposite:
        if existing:
            oid = existing.get("order_id") or existing.get("id")
            if oid:
                others.append(
                    {
                        "symbol": symbol,
                        "action": "CANCEL",
                        "order_id": str(oid),
                        "notional": 0,
                    }
                )
        return others
    focus = [
        i for i in (intents or []) if (i.get("symbol") or "").upper() == symbol
    ]
    return others + focus


def intents_from_book(book, open_orders, max_active=None):
    """Place/cancel intents. HOLD does not take a slot. Max 3 working, 1 per symbol."""
    max_active = int(max_active if max_active is not None else config.MAX_WORKING_ORDERS)
    working = list(open_orders or [])
    by_sym: dict[str, list] = {}
    for order in working:
        sym = (order.get("symbol") or "").upper()
        if sym:
            by_sym.setdefault(sym, []).append(order)

    ranked = []
    for row in book or []:
        ms = row.get("market_state") or row
        risk = row.get("risk") or {}
        decided = make_decision(ms, risk)
        decided["symbol"] = (decided.get("symbol") or row.get("symbol") or "").upper()
        ranked.append(decided)

    intents = []
    cancel_ids = set()
    for decided in ranked:
        existing = by_sym.get(decided["symbol"]) or []
        if not existing:
            continue
        side = str(existing[0].get("side") or "").lower()
        placed = "BUY" if side == "buy" else "SELL" if side == "sell" else None
        opposite = (
            decided["action"] in ("BUY", "SELL")
            and placed
            and decided["action"] != placed
        )
        if decided["action"] == "HOLD" or opposite:
            oid = existing[0].get("order_id") or existing[0].get("id")
            if oid:
                cancel_ids.add(str(oid))
                intents.append(
                    {
                        "symbol": decided["symbol"],
                        "action": "CANCEL",
                        "order_id": str(oid),
                        "notional": 0,
                    }
                )

    remaining = [
        o
        for o in working
        if str(o.get("order_id") or o.get("id") or "") not in cancel_ids
    ]
    slots = max(0, max_active - len(remaining))
    occupied = {(o.get("symbol") or "").upper() for o in remaining}
    scored = sorted(
        [d for d in ranked if d.get("action") in ("BUY", "SELL")],
        key=lambda d: abs(
            float((d.get("scores") or {}).get("buy") or 0)
            - float((d.get("scores") or {}).get("sell") or 0)
        ),
        reverse=True,
    )
    for decided in scored:
        if slots <= 0:
            break
        sym = decided["symbol"]
        if not sym or sym in occupied:
            continue
        intents.append(
            {
                "symbol": sym,
                "action": decided["action"],
                "notional": decided.get("position_size"),
                "position_size": decided.get("position_size"),
            }
        )
        occupied.add(sym)
        slots -= 1
    return intents


def make_decision(market_state, risk):
    market_state = market_state or {}
    risk = risk or {}
    scores = score_setup(market_state)
    buy, sell = scores["buy"], scores["sell"]

    if market_state.get("error"):
        action = "HOLD"
    elif buy > sell and buy >= SCORE_THRESHOLD:
        action = "BUY"
    elif sell > buy and sell >= SCORE_THRESHOLD:
        action = "SELL"
    else:
        action = "HOLD"

    return {
        "symbol": market_state.get("symbol"),
        "action": action,
        "position_size": risk.get("position_size"),
        "technical_signal": market_state.get("technical_signal"),
        "sentiment": market_state.get("sentiment"),
        "risk_level": risk.get("risk_level"),
        "scores": scores,
        "signals": _signals(market_state),
    }


def book_prompt(book, open_orders=None, positions=None, focus=None):
    """Compact book for the decision LLM. Scores, ATR, positions, working
    orders — never OHLCV."""
    working = {}
    for order in open_orders or []:
        sym = (order.get("symbol") or "").upper()
        if not sym:
            continue
        side = str(order.get("side") or "").lower().split(".")[-1]
        oid = str(order.get("order_id") or order.get("id") or "")[:8]
        bit = f"{side}:{oid}" if oid else side or "open"
        working.setdefault(sym, []).append(bit)
    pos = {}
    for row in positions or []:
        sym = (row.get("symbol") or "").upper()
        if not sym:
            continue
        side = str(row.get("side") or "").lower().split(".")[-1]
        qty = row.get("qty")
        pos[sym] = f"{side}:{qty}" if qty is not None else (side or "open")

    def _line(sym, ms, risk):
        wo = ",".join(working.get(sym) or []) or "none"
        po = pos.get(sym) or "none"
        return (
            f"{sym} bias={ms.get('trade_bias')} tech={ms.get('technical_signal')} "
            f"rsi={ms.get('rsi_signal')} sent={ms.get('sentiment')} "
            f"atr={ms.get('atr')} size={risk.get('position_size')} "
            f"working={wo} pos={po}"
        )

    lines = []
    seen = set()
    for row in book or []:
        ms = row.get("market_state") or {}
        risk = row.get("risk") or {}
        sym = (row.get("symbol") or ms.get("symbol") or "").upper()
        if not sym:
            continue
        seen.add(sym)
        lines.append(_line(sym, ms, risk))
    extra = set(working) | set(pos)
    focus_sym = (focus or "").upper()
    if focus_sym:
        extra.add(focus_sym)
    for sym in sorted(extra):
        if not sym or sym in seen:
            continue
        lines.append(_line(sym, {}, {}))
    if not lines:
        return ""
    return "Compact book (no OHLCV):\n" + "\n".join(lines)


class DecisionAgent(Agent):
    node = "decision"

    def run(self, ctx):
        out = make_decision(ctx.get("market_state") or {}, ctx.get("risk") or {})
        if not out.get("symbol"):
            out["symbol"] = (ctx.get("symbol") or "").upper() or None
        book = ctx.get("book") or [
            {
                "symbol": out.get("symbol"),
                "market_state": ctx.get("market_state") or {},
                "risk": ctx.get("risk") or {},
            }
        ]
        out["intents"] = intents_from_book(
            book,
            ctx.get("open_orders") or [],
            config.MAX_WORKING_ORDERS,
        )
        return out

    def message(self, out):
        if self._err(out):
            return self._err(out)
        model = out.get("model") or ""
        short = model.split("/")[-1] if model else ""
        scores = out.get("scores") or {}
        head = (
            f"{out.get('action')} ({out.get('sentiment')}×{out.get('technical_signal')}"
            f" · {scores.get('buy', 0)}/{scores.get('sell', 0)})"
        )
        return f"{head} · {short}" if short else head


class DecisionReactAgent(ReactAgent, DecisionAgent):
    """Reasoning loop over market_state + risk + compact OMS.

    The LLM picks BUY/SELL/HOLD for the focus symbol. That action may force
    HOLD/CANCEL on focus working orders; place intents for the universe stay
    with intents_from_book. Never sizes. Never calls place_*.
    """

    max_turns = config.DECISION_MAX_TURNS

    def system_prompt(self):
        return (
            "You are a trading decision agent. Decide BUY, SELL, or HOLD for a "
            "symbol given its market state and risk. You may gather more "
            "evidence by calling a read-only tool. To call a tool, output ONLY "
            'JSON: {"tool": "technical_analysis", "params": {"symbol": "AAPL"}}. '
            "Available tools: get_market_features, technical_analysis, "
            "get_market_news, recent_history, lookup_concept, detect_orderblocks, "
            "detect_smart_money. Use lookup_concept when you do not know a term "
            "or indicator (Instant Answer, not news). Reflect on whether the "
            "action is well-supported before finalizing. Do NOT size positions. "
            "When decided, output ONLY JSON: "
            '{"action": "BUY|SELL|HOLD", "rationale": "...", "confidence": 0-100}. '
            "Output JSON only."
        )

    def goal(self, ctx):
        ms = filter_snapshot(
            ctx.get("market_state") or {},
            ctx.get("decision_indicators"),
        )
        risk = ctx.get("risk") or {}
        scores = score_setup(ms)
        bits = [
            f"Symbol {ms.get('symbol')}.",
            f"sentiment={ms.get('sentiment')}",
            f"technical_signal={ms.get('technical_signal')}",
            f"trend={ms.get('trend')}",
            f"ema_trend={ms.get('ema_trend')}",
            f"rsi_signal={ms.get('rsi_signal')}",
            f"near_bullish={ms.get('near_bullish')}",
            f"near_bearish={ms.get('near_bearish')}",
            f"institutional_signal={ms.get('institutional_signal')}",
            f"scores buy={scores['buy']} sell={scores['sell']}",
        ]
        for key in (
            "rsi",
            "rsi3",
            "sma20",
            "sma50",
            "ema3",
            "ema10",
            "ema20",
            "ema50",
            "ema100",
            "macd",
            "atr",
            "volume",
            "confidence",
        ):
            if key in ms and ms[key] is not None:
                bits.append(f"{key}={ms[key]}")
        goal = (
            " ".join(bits)
            + f". Risk: level={risk.get('risk_level')}, position_size=${risk.get('position_size')}. "
            "Decide the action."
        )
        history = logs.history_text(ctx.get("symbol"), agent_id="decision")
        book = book_prompt(
            ctx.get("book"),
            open_orders=ctx.get("open_orders"),
            positions=ctx.get("positions"),
            focus=ctx.get("symbol"),
        )
        extra = "\n\n".join(part for part in (book, history) if part)
        return f"{goal}\n\n{extra}" if extra else goal

    def tools(self, ctx=None):
        inds = (ctx or {}).get("indicators")

        def _features(symbol, **_k):
            return get_market_features(symbol, indicators=inds)

        def _technical(symbol, **_k):
            return technical_analysis(symbol, indicators=inds)

        def _ob(symbol, **_k):
            return detect_orderblocks(symbol)

        def _flow(symbol, **_k):
            return detect_smart_money(symbol)

        return {
            "get_market_features": _features,
            "technical_analysis": _technical,
            "get_market_news": get_market_news,
            "recent_history": lambda symbol="SPY", limit=10, **_k: research_tools.recent_history(
                symbol, limit=limit, agent_id="decision"
            ),
            "lookup_concept": research_tools.lookup_concept,
            "detect_orderblocks": _ob,
            "detect_smart_money": _flow,
        }

    def finalize(self, text, ctx):
        parsed = extract_json(text)
        if not isinstance(parsed, dict) or "action" not in parsed:
            raise ValueError("no final action")
        action = str(parsed.get("action", "")).upper()
        if action not in ("BUY", "SELL", "HOLD"):
            action = "HOLD"
        ms = ctx.get("market_state") or {}
        risk = ctx.get("risk") or {}
        out = {
            "symbol": ms.get("symbol"),
            "action": action,
            "position_size": risk.get("position_size"),
            "technical_signal": ms.get("technical_signal"),
            "sentiment": ms.get("sentiment"),
            "risk_level": risk.get("risk_level"),
            "scores": score_setup(ms),
            "signals": _signals(ms),
            "rationale": parsed.get("rationale", ""),
            "confidence": parsed.get("confidence"),
            "model": (ctx.get("models") or {}).get("decision"),
        }
        if not out.get("symbol"):
            out["symbol"] = (ctx.get("symbol") or "").upper() or None
        book = ctx.get("book") or [
            {
                "symbol": out.get("symbol"),
                "market_state": ms,
                "risk": risk,
            }
        ]
        scored = intents_from_book(
            book,
            ctx.get("open_orders") or [],
            config.MAX_WORKING_ORDERS,
        )
        out["intents"] = apply_focus_action(
            scored,
            action,
            out.get("symbol"),
            ctx.get("open_orders") or [],
        )
        return out

    def fallback(self, reason, ctx):
        ms = ctx.get("market_state") or {}
        risk = ctx.get("risk") or {}
        out = {
            "symbol": ms.get("symbol") or (ctx.get("symbol") or "").upper() or None,
            "action": "HOLD",
            "position_size": risk.get("position_size"),
            "technical_signal": ms.get("technical_signal"),
            "sentiment": ms.get("sentiment"),
            "risk_level": risk.get("risk_level"),
            "scores": score_setup(ms),
            "signals": _signals(ms),
            "rationale": f"fail-closed: {reason}",
            "confidence": 0,
            "model": (ctx.get("models") or {}).get("decision"),
        }
        book = ctx.get("book") or [
            {
                "symbol": out.get("symbol"),
                "market_state": ms,
                "risk": risk,
            }
        ]
        scored = intents_from_book(
            book,
            ctx.get("open_orders") or [],
            config.MAX_WORKING_ORDERS,
        )
        out["intents"] = apply_focus_action(
            scored,
            "HOLD",
            out.get("symbol"),
            ctx.get("open_orders") or [],
        )
        return out

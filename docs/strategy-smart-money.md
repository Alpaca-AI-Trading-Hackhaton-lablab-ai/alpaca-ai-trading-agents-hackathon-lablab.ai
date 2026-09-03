# Smart-money strategy (EMA stack, RSI3, order blocks, institutional flow)

How the expert notes in `consideraciones-estrategia-agents.md` are coupled into
the pipeline. Deterministic Python only. Compact snapshots — never candles,
option chains, or ticks to an LLM. Paper-only.

## What changed vs the original snippet

The note used `get_bars` / `get_trades` and mixed `close` vs `c`. This repo uses
`services.alpaca_service.get_spy_bars(symbol, timeframe, limit)` (IEX) and
`agents.indicator_engine.bars_frame`. Existing SMA20/50, EMA20, RSI(14), MACD,
ATR, and volume stay for the chart; the strategy **extends** them.

The snippet priced the OB on the *impulse* bar. The expert **prose** defines
the block as the last opposing candle before that impulse — that is what we
store (`price` = HIGH of the prior bear for a bullish OB, LOW of the prior
bull for a bearish OB, plus `level`).

| Piece | Timeframe | Module | LLM? |
|---|---|---|---|
| EMA 3/10/50/100, RSI(3), `ema_trend`, `rsi_signal` | **1Hour** | `indicator_engine.py` | No |
| Volume ratio + accumulation/distribution | **1Hour** | `institutional_flow.py` | No |
| Order blocks (prior opposing daily candle, 1.5× range) | **1Day** | `orderblock_engine.py` | No |
| Scored BUY/SELL/HOLD (threshold 2.5) | — | `decision_agent.py` | Deep may pick action; never size |
| ATR% × score conviction sizing, cap 10% equity | — | `risk_manager.py` | No |

No trade tape (`get_trades` is not used). Cache keys include the timeframe.

## Pipeline

```
news → sentiment → options; features; technical; orderblock; institutional
  → market_state → account → risk → decision → gate
```

`orderblock` and `institutional` emit compact dicts (`near_bullish`,
`bullish_ob.price` / `level`, `institutional_signal`, …). `build_market_state`
merges them. `score_setup` feeds **both** risk (size) and decision (action).
Risk still runs **before** decision. Deep ReAct `finalize` still copies
`position_size` from risk.

The dashboard chart draws dashed price lines at the last bullish / bearish
OB (compact levels from market_state — not a candle dump).

## Scoring

| Signal | Buy | Sell |
|---|---|---|
| technical `signal` BUY/SELL | +2 | +2 |
| RSI3 OVERSOLD / OVERBOUGHT | +1.5 | +1.5 |
| price within 1% of bullish / bearish OB | +3 | +3 |
| smart-money buying / selling | +2 | +2 |
| sentiment BULLISH/POSITIVE or BEARISH/NEGATIVE | +1 | +1 |

Action if the winning side is ≥ 2.5 and strictly ahead; else HOLD. A degraded
`market_state` (`error`) is HOLD regardless of score.

## Risk

1. Base % from sentiment confidence (1 / 2 / 3%) — unchanged tiers.
2. `confidence_factor` = clamp(\|buy−sell\| / 5, 0.5, 1.5).
3. `volatility_factor` = clamp(0.01 / (atr/price), 0.5, 1.5). Missing ATR → 1.0.
4. Cap at 10% of equity (`MAX_SYMBOL_EXPOSURE_PCT`).

## ReAct tools (read-only)

`detect_orderblocks` and `detect_smart_money` are on the decision-loop
allowlist. They return the same compact snapshots. News stays Tavily;
`lookup_concept` stays Instant Answer, not search.

## Out of scope

Backtrader / vectorbt, live trading, raw tick tape.

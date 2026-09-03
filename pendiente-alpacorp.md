# Pendiente Alpacore — TradeLix backend

> **Before deploying**, read this file and verify each item is still accurate.
> Update it when APIs change or items are completed.
> _Last research: 2026-09-03 via Alpaca docs + community._

---

## P1 — Tick propone bracket (TP/SL), no market notional desnudo

**Estado actual del tick:** `book.run_tick` → `apply_intents` → `dispatch` sin `plan` →
`execute_trade` con market notional. El SL/TP se ignora en el tick.

**Objetivo:** el tick llama a `dispatch(..., plan=plan)` con un plan seedeado desde la
decisión + precio actual + ATR. Usa `bracket_plan.seed_plan(decision, last_price, atr)`.

**API Alpaca (confirmado 2026-09):**
- Bracket nativo: `order_class="bracket"`, `take_profit={"limit_price": …}`,
  `stop_loss={"stop_price": …}`. Funciona con `notional` (fractional).
- **Trailing stop como leg de bracket: NO soportado aún.** Alpaca tiene planeado
  añadirlo pero sin ETA. El workaround actual es el motor emulado que ya existe en
  `services/conditional.py` + `emulated_rows`.
- Los órdenes hijo (legs) quedan en estado `HELD` hasta que el padre se llena.
  Para verlos hay que usar `QueryOrderStatus.ALL`, no `OPEN`.
- Los legs no aceptan `client_order_id` personalizado (estado `HELD` rechaza PATCH).
  Workaround: guardar el mapa `parent_id → [leg_ids]` desde `order.legs` en la
  respuesta de submit.

**Archivos a tocar:** `services/book.py` (`run_tick` / `apply_intents`),
`services/alpaca_service.py` (pasar `last_price` / `atr` al tick desde market_state),
`agents/execution_agent.py` (dispatch ya soporta `plan`).

---

## P1 — Risk/account ven la posición abierta (no sizear como si no estuvieras long)

**Estado actual:** `RiskAgent` llama `calculate_risk(equity, confidence, atr=…)` sin
conocer la posición existente en el símbolo. Puede sizear una nueva entrada completa
aunque ya tengas una posición abierta.

**Objetivo:** antes de calcular notional, consultar `positions` (ya pasan por el ctx)
para el símbolo y reducir o bloquear si `existing_qty > 0` (long) / `< 0` (short).

**API Alpaca (confirmado 2026-09):**
- `trading_client.get_all_positions()` devuelve lista de `Position` con campos
  `symbol`, `qty`, `side`, `market_value`, `unrealized_pl`, `avg_entry_price`.
- `get_open_position(symbol_or_asset_id)` → lanza `APIError` si no hay posición.
- Los `positions` ya llegan en `ctx` en el pipeline y en el tick. Solo hay que
  leerlos en `RiskAgent` / `calculate_risk`.

**Archivos a tocar:** `agents/risk_manager.py` (`calculate_risk` y `RiskAgent.run`),
opcionalmente `agents/execution_gate.py` (añadir check de exposición neta).

---

## P1 — Dueño de lifecycle: fills, BE/trailing, client_order_id

**Estado actual:** nadie escucha el WebSocket de trade_updates. Los fills de órdenes
enviadas (market, bracket) solo se detectan por polling en `execute_trade` (6 intentos,
0.5s). Los BE/trailing emulados se guardan como condicionales en la DB pero nadie
confirma el fill del padre antes de activarlos.

**Objetivo:** un `FillAgent` o tarea background que:
1. Suscribe a `trade_updates` (WebSocket paper).
2. En evento `fill` / `partial_fill` del padre: activa los condicionales emulados
   enlazados por `client_order_id` o por `parent_id → legs`.
3. Actualiza el estado de la orden en la DB de audit/invocation_logs.

**API Alpaca (confirmado 2026-09):**
- WebSocket paper: `wss://paper-api.alpaca.markets/stream` (cuenta paper).
- Stream `trade_updates`: eventos `new`, `fill`, `partial_fill`, `canceled`,
  `expired`, `replaced`, `rejected`, etc. El evento `fill` incluye `position_qty`
  (posición resultante), `price`, `qty`, `execution_id`.
- `client_order_id` en el orden principal llega en todos los eventos del stream.
  Para los legs de un bracket el `client_order_id` es autogenerado; la única forma
  de linkearlos es guardar el mapa desde `order.legs` al momento de submit (la API
  rechaza PATCH de `client_order_id` cuando el leg está en `HELD`).
- alpaca-py: `TradingStream` en `alpaca.trading.stream` — clase async. En FastAPI
  usar `asyncio.create_task` en el lifespan o un thread dedicado.

**Archivos a tocar (nuevos o modificados):**
- `services/fill_listener.py` (nuevo) — TradingStream, maneja events.
- `backend.py` lifespan — arranca/para el listener.
- `services/conditional.py` — `_fire` puede ser llamado desde el listener.
- `services/alpaca_service.py` — helper `submit_with_legs` que retorna
  `{order_id, leg_ids: [tp_id, sl_id]}`.

---

## Fuera de este corte (P2+)

- Un solo endpoint HTTP (fusionar `/execute` + `/bracket/execute`).
- MCP: `mcp_client.py` aún muerto (qty, no notional).
- Critic / feedback loop entre fills y decision scoring.
- Backtesting offline con el mismo gate/risk pipeline.

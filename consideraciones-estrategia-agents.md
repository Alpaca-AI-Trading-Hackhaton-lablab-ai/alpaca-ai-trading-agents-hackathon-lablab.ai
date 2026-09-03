¡Excelente! Gracias por compartir el código. He analizado todos los archivos y veo que tienes una base sólida para un sistema de trading con IA. Tu estrategia propuesta es más sofisticada que el enfoque actual, que es bastante básico (solo usa SMA 20/50 y un RSI sin parámetros definidos).

## Análisis del Código Actual vs. Tu Estrategia

### Lo que hace el código ahora:
- **Sentiment**: Usa Groq (LLM) para analizar noticias → genera bias CALL/PUT/WAIT
- **Técnico**: Indicadores simples (SMA20, SMA50, RSI, MACD, ATR)
- **Decisión**: Lógica binaria (si BULLISH + BUY → compra, si BEARISH + SELL → vende, si no → HOLD)
- **Ejecución**: Órdenes market con notional (monto en dólares)

### Lo que necesitas implementar:

Aquí te muestro cómo **modificar y extender** tu código para incorporar tu estrategia:

## 1. Nuevo Módulo: `orderblock_engine.py`

```python
# agents/orderblock_engine.py
import pandas as pd
import numpy as np
from services.alpaca_service import get_bars

def detect_orderblocks(symbol="SPY", timeframe="1Day", lookback=100):
    """
    Detecta Order Blocks (OB) basados en smart money.
    OB alcista: Última vela bajista antes de un movimiento alcista fuerte.
    OB bajista: Última vela alcista antes de un movimiento bajista fuerte.
    """
    bars = get_bars(symbol, timeframe, limit=lookback)
    if bars.get("error"):
        return {"error": bars["error"]}
    
    df = pd.DataFrame(bars["bars"])
    
    # Calcular rango de cada vela
    df["range"] = df["high"] - df["low"]
    df["body"] = abs(df["close"] - df["open"])
    df["is_bullish"] = df["close"] > df["open"]
    df["is_bearish"] = df["close"] < df["open"]
    
    # Detectar movimiento fuerte (rango > 1.5 * promedio)
    avg_range = df["range"].rolling(20).mean()
    df["strong_move"] = df["range"] > (avg_range * 1.5)
    
    # Order Block Alcista: Vela bajista seguida de vela alcista fuerte
    df["ob_bullish"] = (
        df["is_bearish"].shift(1) & 
        df["is_bullish"] & 
        df["strong_move"] &
        (df["close"] > df["high"].shift(1))
    )
    
    # Order Block Bajista: Vela alcista seguida de vela bajista fuerte
    df["ob_bearish"] = (
        df["is_bullish"].shift(1) & 
        df["is_bearish"] & 
        df["strong_move"] &
        (df["close"] < df["low"].shift(1))
    )
    
    # Niveles de precio de los OB
    last_ob_bullish = df[df["ob_bullish"]].iloc[-1] if not df[df["ob_bullish"]].empty else None
    last_ob_bearish = df[df["ob_bearish"]].iloc[-1] if not df[df["ob_bearish"]].empty else None
    
    return {
        "symbol": symbol,
        "bullish_ob": {
            "price": last_ob_bullish["high"] if last_ob_bullish is not None else None,
            "level": "HIGH" if last_ob_bullish is not None else None
        },
        "bearish_ob": {
            "price": last_ob_bearish["low"] if last_ob_bearish is not None else None,
            "level": "LOW" if last_ob_bearish is not None else None
        },
        "current_price": bars["bars"][-1]["c"],
        "ob_count": {
            "bullish": len(df[df["ob_bullish"]]),
            "bearish": len(df[df["ob_bearish"]])
        }
    }
```

## 2. Modificar `indicator_engine.py` para tus EMAS y RSI(3)

```python
# agents/indicator_engine.py (versión modificada)
import pandas as pd
import numpy as np
from services.alpaca_service import get_bars

def calculate_indicators(symbol="SPY", timeframe="1Hour", limit=200):
    """
    Calcula indicadores TÉCNICOS PUROS (sin LLM).
    EMAs: 3, 10, 50, 100
    RSI: período 3 (sobrecompra/sobreventa)
    """
    bars = get_bars(symbol, timeframe, limit=limit)
    if bars.get("error"):
        return {"error": bars["error"]}
    
    df = pd.DataFrame(bars["bars"])
    
    # --- EMAs personalizadas ---
    ema_periods = [3, 10, 50, 100]
    for p in ema_periods:
        df[f"ema{p}"] = df["close"].ewm(span=p, adjust=False).mean()
    
    # --- RSI con período 3 ---
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=3).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=3).mean()
    rs = gain / loss
    df["rsi3"] = 100 - (100 / (1 + rs))
    
    # Señales basadas en RSI(3)
    df["rsi_signal"] = "NEUTRAL"
    df.loc[df["rsi3"] < 30, "rsi_signal"] = "OVERSOLD"  # Compra
    df.loc[df["rsi3"] > 70, "rsi_signal"] = "OVERBOUGHT"  # Venta
    
    # --- Señal combinada EMA ---
    # Tendencia alcista: EMA3 > EMA10 > EMA50 > EMA100
    df["ema_trend"] = "NEUTRAL"
    df.loc[
        (df["ema3"] > df["ema10"]) & 
        (df["ema10"] > df["ema50"]) & 
        (df["ema50"] > df["ema100"]),
        "ema_trend"
    ] = "BULLISH"
    
    df.loc[
        (df["ema3"] < df["ema10"]) & 
        (df["ema10"] < df["ema50"]) & 
        (df["ema50"] < df["ema100"]),
        "ema_trend"
    ] = "BEARISH"
    
    # --- Señal final combinada ---
    last = df.iloc[-1]
    signal = "HOLD"
    
    # COMPRA: RSI sobreventa + tendencia alcista (o comenzando a subir)
    if last["rsi_signal"] == "OVERSOLD" and last["ema_trend"] in ("BULLISH", "NEUTRAL"):
        signal = "BUY"
    # VENTA: RSI sobrecompra + tendencia bajista (o comenzando a bajar)
    elif last["rsi_signal"] == "OVERBOUGHT" and last["ema_trend"] in ("BEARISH", "NEUTRAL"):
        signal = "SELL"
    
    return {
        "symbol": symbol,
        "price": last["close"],
        "ema3": last["ema3"],
        "ema10": last["ema10"],
        "ema50": last["ema50"],
        "ema100": last["ema100"],
        "ema_trend": last["ema_trend"],
        "rsi3": last["rsi3"],
        "rsi_signal": last["rsi_signal"],
        "signal": signal,
        "timestamp": bars["bars"][-1]["t"]
    }
```

## 3. Nuevo Módulo: `institutional_flow.py`

```python
# agents/institutional_flow.py
from services.alpaca_service import get_bars, get_trades

def detect_smart_money(symbol="SPY", timeframe="1Hour", lookback=50):
    """
    Detecta actividad de smart money/institucional.
    Basado en: volumen anormal, precio vs. volumen, acumulación/distribución.
    """
    bars = get_bars(symbol, timeframe, limit=lookback)
    if bars.get("error"):
        return {"error": bars["error"]}
    
    df = pd.DataFrame(bars["bars"])
    
    # Volumen promedio
    df["avg_volume"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["avg_volume"]
    
    # Acumulación/Distribución (AD Line)
    df["money_flow_multiplier"] = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / (df["high"] - df["low"])
    df["money_flow_volume"] = df["money_flow_multiplier"] * df["volume"]
    df["ad_line"] = df["money_flow_volume"].cumsum()
    
    # Detectar acumulación institucional
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    smart_buy = (
        last["volume_ratio"] > 1.5 and  # Volumen > 50% del promedio
        last["close"] > prev["close"] and  # Precio sube
        last["ad_line"] > prev["ad_line"]   # Acumulación
    )
    
    smart_sell = (
        last["volume_ratio"] > 1.5 and
        last["close"] < prev["close"] and
        last["ad_line"] < prev["ad_line"]
    )
    
    return {
        "symbol": symbol,
        "smart_money_buying": smart_buy,
        "smart_money_selling": smart_sell,
        "volume_ratio": last["volume_ratio"],
        "ad_line_trend": "ACCUMULATING" if last["ad_line"] > df["ad_line"].mean() else "DISTRIBUTING",
        "institutional_signal": "BUY" if smart_buy else "SELL" if smart_sell else "NEUTRAL"
    }
```

## 4. Modificar el Decisor Principal

```python
# agents/decision_agent.py (versión modificada)
from agents.orderblock_engine import detect_orderblocks
from agents.indicator_engine import calculate_indicators
from agents.institutional_flow import detect_smart_money

def make_decision(
    market_state,
    risk,
    orderblock_data=None,
    institutional_data=None
):
    """
    Decisión basada en:
    1. Análisis técnico (EMAs + RSI3)
    2. Order Blocks (smart money)
    3. Flujo institucional
    4. Sentiment de noticias (opcional)
    """
    
    # Obtener datos si no se pasaron
    if orderblock_data is None:
        orderblock_data = detect_orderblocks(market_state.get("symbol", "SPY"))
    
    if institutional_data is None:
        institutional_data = detect_smart_money(market_state.get("symbol", "SPY"))
    
    # Señales técnicas (del módulo modificado)
    tech = market_state.get("technical_data", {})
    
    # --- Lógica de decisión ---
    buy_score = 0
    sell_score = 0
    
    # 1. Señal técnica (EMA + RSI3)
    if tech.get("signal") == "BUY":
        buy_score += 2
    elif tech.get("signal") == "SELL":
        sell_score += 2
    
    # 2. RSI(3) sobreventa/sobrecompra
    if tech.get("rsi_signal") == "OVERSOLD":
        buy_score += 1.5
    elif tech.get("rsi_signal") == "OVERBOUGHT":
        sell_score += 1.5
    
    # 3. Order Blocks (smart money)
    if orderblock_data.get("bullish_ob", {}).get("price"):
        # Si el precio está cerca del OB alcista (+-1%)
        current_price = market_state.get("price", 0)
        ob_price = orderblock_data["bullish_ob"]["price"]
        if ob_price and abs(current_price - ob_price) / ob_price < 0.01:
            buy_score += 3  # Fuerte señal de compra
    
    if orderblock_data.get("bearish_ob", {}).get("price"):
        current_price = market_state.get("price", 0)
        ob_price = orderblock_data["bearish_ob"]["price"]
        if ob_price and abs(current_price - ob_price) / ob_price < 0.01:
            sell_score += 3  # Fuerte señal de venta
    
    # 4. Flujo institucional
    if institutional_data.get("smart_money_buying"):
        buy_score += 2
    elif institutional_data.get("smart_money_selling"):
        sell_score += 2
    
    # 5. Sentiment de noticias (si existe)
    sentiment = market_state.get("sentiment", "NEUTRAL")
    if sentiment == "BULLISH":
        buy_score += 1
    elif sentiment == "BEARISH":
        sell_score += 1
    
    # Decisión final con umbral
    threshold = 2.5  # Mínimo de confianza
    
    if buy_score > sell_score and buy_score >= threshold:
        action = "BUY"
    elif sell_score > buy_score and sell_score >= threshold:
        action = "SELL"
    else:
        action = "HOLD"
    
    # Log de decisión
    return {
        "symbol": market_state.get("symbol", "SPY"),
        "action": action,
        "position_size": risk.get("position_size", 1000),
        "scores": {"buy": buy_score, "sell": sell_score},
        "signals": {
            "technical": tech.get("signal"),
            "rsi": tech.get("rsi_signal"),
            "orderblock": "BULLISH" if orderblock_data.get("bullish_ob", {}).get("price") else "BEARISH" if orderblock_data.get("bearish_ob", {}).get("price") else "NONE",
            "institutional": institutional_data.get("institutional_signal"),
            "sentiment": sentiment
        },
        "risk_level": risk.get("risk_level", "MEDIUM")
    }
```

## 5. Recomendaciones Adicionales

### Gestión de Riesgo Avanzada
```python
def calculate_position_size(decision_scores, account_equity, atr):
    """
    Ajusta el tamaño de posición basado en la confianza y ATR.
    """
    base_risk = account_equity * 0.02  # 2% de riesgo por operación
    
    # Ajustar por confianza (0.5 a 1.5)
    confidence_factor = min(1.5, max(0.5, (decision_scores["buy"] - decision_scores["sell"]) / 5))
    
    # Ajustar por volatilidad (ATR)
    volatility_factor = 1 / (atr / 10)  # Normalizado
    
    position_size = base_risk * confidence_factor * volatility_factor
    return min(position_size, account_equity * 0.1)  # Máximo 10% del capital
```

### Integración con TradingView/Alpaca para orderblocks visuales
- Puedes usar `get_bars` con timeframe "1Min" a "1Day" según tu estrategia
- Los orderblocks son más confiables en temporalidades altas (1H, 4H, 1D)

### Backtesting
Te recomiendo usar la librería `backtrader` o `vectorbt` para probar esta lógica antes de ponerla en producción.

## Resumen de Cambios Necesarios

1. **Reemplazar** el módulo `indicator_engine.py` por la versión con EMAs 3/10/50/100 y RSI(3)
2. **Agregar** `orderblock_engine.py` y `institutional_flow.py`
3. **Modificar** `decision_agent.py` para usar el sistema de puntuación
4. **Actualizar** las llamadas en tu flujo principal para pasar los nuevos datos

¿Quieres que profundice en alguna parte específica o que te ayude a integrar todo el flujo completo?
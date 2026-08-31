from services.alpaca_service import get_spy_bars
import pandas as pd


def technical_analysis():

    df = get_spy_bars()

    # Agar service error return kare
    if isinstance(df, dict):
        return df

    if df.empty:
        return {"error": "No bars data"}

    close = pd.to_numeric(df["close"])

    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    ema20 = close.ewm(span=20).mean().iloc[-1]

    delta = close.diff()

    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    rsi_value = round(float(rsi.iloc[-1]), 2)
    price = round(float(close.iloc[-1]), 2)

    if rsi_value > 60 and sma20 > sma50:
        signal = "BUY"
    elif rsi_value < 40 and sma20 < sma50:
        signal = "SELL"
    else:
        signal = "HOLD"

    return {
        "symbol": "SPY",
        "price": price,
        "rsi": rsi_value,
        "sma20": round(float(sma20), 2),
        "sma50": round(float(sma50), 2),
        "ema20": round(float(ema20), 2),
        "signal": signal
    }
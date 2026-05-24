#!/usr/bin/env python3
"""
Monitor AUDUSD BUY señal regalo.
Detecta cambio de tendencia y avisa al grupo de Telegram.
Triggers:
  1. RSI 14 (5min) > 68 y bajando (overbought + reversal)
  2. Pullback >= 15 pips desde el máximo alcanzado
  3. EMA9 cruza por debajo de EMA21 (5min)
"""

import time, requests, os, json
from datetime import datetime, timezone, timedelta
import yfinance as yf
import pandas as pd
import numpy as np
from dotenv import dotenv_values

cfg = dotenv_values(os.path.join(os.path.dirname(__file__), ".env"))
TOKEN    = cfg["TELEGRAM_TOKEN"]
GROUP_ID = cfg.get("GROUP_ID", "@BUYSELL_365_24_7")
BASE     = f"https://api.telegram.org/bot{TOKEN}"

ENTRY      = 0.71669   # precio señal regalo
SL         = 0.70940
TP         = 0.73440
SYMBOL     = "AUDUSD=X"
PAIR_NAME  = "AUD/USD"
STATE_FILE = "/tmp/monitor_audusd_state.json"
CHECK_INTERVAL = 300  # 5 minutos

DUBAI_TZ = timezone(timedelta(hours=4))

# ─── Estado persistente ────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"high": ENTRY, "alerted": False, "last_rsi": None, "last_alert_type": None}

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f)

# ─── Indicadores ──────────────────────────────────────────────────────────────
def get_data():
    """Descarga ~100 velas de 5min y calcula RSI14, EMA9, EMA21."""
    t = yf.Ticker(SYMBOL)
    df = t.history(period="2d", interval="5m")
    if df.empty or len(df) < 25:
        return None
    close = df["Close"]
    # RSI 14
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
    rs    = gain / loss
    rsi   = 100 - 100 / (1 + rs)
    # EMAs
    ema9  = close.ewm(span=9,  adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    return {
        "price":   float(close.iloc[-1]),
        "rsi":     float(rsi.iloc[-1]),
        "rsi_prev":float(rsi.iloc[-2]),
        "ema9":    float(ema9.iloc[-1]),
        "ema9_prev":float(ema9.iloc[-2]),
        "ema21":   float(ema21.iloc[-1]),
        "ema21_prev":float(ema21.iloc[-2]),
    }

# ─── Telegram ────────────────────────────────────────────────────────────────
def send_alert(msg):
    requests.post(f"{BASE}/sendMessage",
                  json={"chat_id": GROUP_ID, "text": msg, "parse_mode": "HTML"},
                  timeout=10)

# ─── Loop principal ───────────────────────────────────────────────────────────
def main():
    print(f"[monitor] Iniciado. Señal: {PAIR_NAME} BUY @ {ENTRY}  TP {TP}  SL {SL}")
    state = load_state()

    while True:
        try:
            data = get_data()
            if not data:
                print("[monitor] Sin datos, reintentando en 60s")
                time.sleep(60)
                continue

            price    = data["price"]
            rsi      = data["rsi"]
            rsi_prev = data["rsi_prev"]
            ema9     = data["ema9"]
            ema9_p   = data["ema9_prev"]
            ema21    = data["ema21"]
            ema21_p  = data["ema21_prev"]
            pips     = round((price - ENTRY) * 10000, 1)
            now_str  = datetime.now(DUBAI_TZ).strftime("%H:%M")

            # Actualizar máximo
            if price > state["high"]:
                state["high"] = price
                state["alerted"] = False  # reset si sube a nuevo máximo

            pullback_pips = round((state["high"] - price) * 10000, 1)

            print(f"[{now_str}] {PAIR_NAME} {price:.5f}  RSI {rsi:.1f}  "
                  f"EMA9 {ema9:.5f}  EMA21 {ema21:.5f}  "
                  f"PnL {pips:+.1f}p  Pullback {pullback_pips:.1f}p")

            if state["alerted"]:
                save_state(state)
                time.sleep(CHECK_INTERVAL)
                continue

            trigger = None

            # Trigger 1: RSI overbought bajando
            if rsi_prev >= 68 and rsi < rsi_prev and rsi > 60:
                trigger = ("rsi_reversal",
                    f"📉 <b>RSI overbought bajando</b> ({rsi_prev:.0f} → {rsi:.0f})")

            # Trigger 2: Pullback >= 15 pips desde máximo
            if not trigger and pullback_pips >= 15:
                trigger = ("pullback",
                    f"📉 <b>Pullback de {pullback_pips:.0f} pips</b> desde el máximo")

            # Trigger 3: EMA9 cruza por debajo de EMA21
            if not trigger:
                cross_down = ema9_p >= ema21_p and ema9 < ema21
                if cross_down:
                    trigger = ("ema_cross",
                        f"📉 <b>EMA9 cruzó por debajo de EMA21</b> (cambio de tendencia)")

            if trigger:
                t_type, t_desc = trigger
                msg = (
                    f"⚠️ <b>SEÑAL REGALO — {PAIR_NAME} BUY</b>\n\n"
                    f"{t_desc}\n\n"
                    f"💰 Precio actual: <b>{price:.5f}</b>\n"
                    f"📈 PnL: <b>{pips:+.1f} pips</b>\n\n"
                    f"✅ <b>Considera cerrar con ganancias ahora.</b>\n"
                    f"Entrada: {ENTRY}  |  Máximo alcanzado: {state['high']:.5f}\n\n"
                    f"💎 Señal gratis de nuestro Canal VIP\n"
                    f"👉 Escribe /vip para acceder a TODAS las señales"
                )
                send_alert(msg)
                print(f"[{now_str}] ✅ Alerta enviada: {t_type}")
                state["alerted"]    = True
                state["last_alert_type"] = t_type

            save_state(state)

        except Exception as e:
            print(f"[monitor] Error: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()

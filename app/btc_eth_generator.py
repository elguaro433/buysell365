"""
btc_eth_generator.py — Generador propio de señales BTC + ETH
==============================================================

Sistema autónomo que ESCANEA el mercado crypto cada 15 min y genera
señales propias con análisis técnico + Claude Vision (sistema híbrido).

A diferencia del signal_copier (que copia de canales aliados), este
módulo GENERA las señales internamente — son 100% propietarias.

Características:
- 24/7 (incluyendo fines de semana)
- BTC + ETH (operables en XM)
- Múltiples timeframes (M15, H1, H4)
- Múltiples tipos de setup (pullback, breakout, reversion, squeeze)
- Direcciones LONG y SHORT
- Validación con Claude Sonnet 4.6 + Vision
- Score híbrido 0-95% antes de publicar
- Min 5 señales/día por par como objetivo

Toggle: GENERATOR_ENABLED=true en .env
Ejecución MT5: GENERATOR_MT5_EXECUTE=false por defecto (publica solo info)
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("btc_eth_gen")

# ============================================================
# CONFIG
# ============================================================
ENABLED = os.getenv("GENERATOR_ENABLED", "false").lower() in ("true", "1", "yes")
MT5_EXECUTE = os.getenv("GENERATOR_MT5_EXECUTE", "false").lower() in ("true", "1", "yes")
SCAN_INTERVAL_S = int(os.getenv("GENERATOR_SCAN_INTERVAL_S", "1800"))  # 30 min (FIX 2026-05-16: subido de 900s para bajar coste Vision)
MIN_SIGNALS_PER_DAY_PER_PAIR = int(os.getenv("GENERATOR_MIN_DAILY", "5"))
MAX_SIGNALS_PER_DAY_PER_PAIR = int(os.getenv("GENERATOR_MAX_DAILY", "10"))
MIN_PROBABILITY_TO_PUBLISH = int(os.getenv("GENERATOR_MIN_PROB", "55"))  # más bajo que el copier para más volumen
MIN_RR_RATIO = float(os.getenv("GENERATOR_MIN_RR", "1.3"))

PAIRS = [
    {"symbol": "BTCUSD", "display": "BTC/USD", "decimals": 2, "min_atr_pips": 50},
    {"symbol": "ETHUSD", "display": "ETH/USD", "decimals": 2, "min_atr_pips": 5},
]

TIMEFRAMES = ["M15", "H1", "H4"]

# Estado runtime
_signals_today: dict = {}  # {symbol: count_today}
_signals_today_date: str = ""
_recent_signals: dict = {}  # {symbol_direction: timestamp} — anti-duplicado
_DEDUP_WINDOW = 1800  # 30 min entre señales mismo par+direccion

# Output paths
_BASE = Path(__file__).parent
_LOG_FILE = _BASE / "generated_signals.json"
_QUEUE_FILE = _BASE / "generator_signals_queue.json"  # Cola para que signal_copier ingiera
_STATE_FILE = _BASE / "generator_state.json"  # Persiste estado entre reinicios

_queue_lock = threading.Lock()


# ============================================================
# UTILIDADES
# ============================================================
def _today_andorra() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")


def _hhmm() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%H:%M")


def _save_state() -> None:
    """Persiste contadores diarios y ventana dedup a disco para sobrevivir reinicios."""
    try:
        data = {
            "signals_today": _signals_today,
            "signals_today_date": _signals_today_date,
            "recent_signals": _recent_signals,
        }
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            import json as _json
            _json.dump(data, f)
    except Exception as _e:
        log.debug(f"[GEN] save_state error: {_e}")


def _load_state() -> None:
    """Carga estado persistido. Descarta entradas de dedup expiradas (>30 min)."""
    global _signals_today, _signals_today_date, _recent_signals
    try:
        if not _STATE_FILE.exists():
            return
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            import json as _json
            data = _json.load(f)
        today = _today_andorra()
        saved_date = data.get("signals_today_date", "")
        if saved_date == today:
            _signals_today = data.get("signals_today", {})
            _signals_today_date = saved_date
        else:
            # Nuevo dia → resetear contadores
            _signals_today = {p["symbol"]: 0 for p in PAIRS}
            _signals_today_date = today
        # Cargar dedup filtrando entradas expiradas
        now = time.time()
        _recent_signals = {
            k: v for k, v in data.get("recent_signals", {}).items()
            if (now - v) < _DEDUP_WINDOW
        }
        log.info(f"[GEN] Estado cargado: today={_signals_today} dedup_activos={len(_recent_signals)}")
    except Exception as _e:
        log.warning(f"[GEN] load_state error (no crítico): {_e}")


def _reset_daily_counter_if_new_day():
    global _signals_today, _signals_today_date
    today = _today_andorra()
    if today != _signals_today_date:
        _signals_today = {p["symbol"]: 0 for p in PAIRS}
        _signals_today_date = today
        log.info(f"[GEN] Reset diario: {today}")
        _save_state()


def _is_dedup(symbol: str, direction: str) -> bool:
    """Devuelve True si esta senal ya fue emitida dentro de _DEDUP_WINDOW.
    FIX 2026-05-11 (tarde-3): solo LEE el dedup, NO marca. El caller debe
    invocar _mark_dedup(symbol, direction) tras un publish EXITOSO. Antes
    se marcaba aqui y si calculate_levels/score_signal/publish_signal fallaba,
    el dedup quedaba bloqueado 30min sin senal emitida — saltabamos oportunidades."""
    key = f"{symbol}_{direction}"
    now = time.time()
    last = _recent_signals.get(key, 0)
    if last and (now - last) < _DEDUP_WINDOW:
        return True
    return False


def _mark_dedup(symbol: str, direction: str) -> None:
    """Marca el (symbol, direction) como recien publicado. Llamar SOLO tras publish exitoso."""
    key = f"{symbol}_{direction}"
    _recent_signals[key] = time.time()
    _save_state()


# ============================================================
# INDICADORES TECNICOS
# ============================================================
def compute_indicators(rates) -> dict:
    """Calcula EMA20, EMA50, RSI(14), ATR(14), Bollinger, soportes/resistencias."""
    import numpy as np
    closes = np.array([float(r['close']) for r in rates])
    highs = np.array([float(r['high']) for r in rates])
    lows = np.array([float(r['low']) for r in rates])

    # EMA
    def _ema(arr, n):
        if len(arr) < n:
            return float(arr[-1])
        alpha = 2 / (n + 1)
        ema = arr[0]
        for v in arr[1:]:
            ema = alpha * v + (1 - alpha) * ema
        return float(ema)

    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)

    # RSI
    if len(closes) >= 15:
        diffs = np.diff(closes[-30:])
        gains = np.where(diffs > 0, diffs, 0)
        losses = np.where(diffs < 0, -diffs, 0)
        avg_g = np.mean(gains[-14:]) if len(gains) >= 14 else 0
        avg_l = np.mean(losses[-14:]) if len(losses) >= 14 else 0.0001
        rs = avg_g / max(avg_l, 0.0001)
        rsi = 100 - 100 / (1 + rs)
    else:
        rsi = 50.0

    # ATR(14)
    trs = []
    for i in range(1, min(len(rates), 15)):
        tr = max(
            highs[-i] - lows[-i],
            abs(highs[-i] - closes[-i - 1]),
            abs(lows[-i] - closes[-i - 1]),
        )
        trs.append(tr)
    atr = float(np.mean(trs)) if trs else 0.0

    # Bollinger Bands (20, 2σ)
    if len(closes) >= 20:
        sma20 = float(np.mean(closes[-20:]))
        std20 = float(np.std(closes[-20:]))
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20
        bb_width = (bb_upper - bb_lower) / sma20
    else:
        sma20 = float(closes[-1])
        bb_upper = sma20
        bb_lower = sma20
        bb_width = 0

    # Soportes/resistencias (swing highs/lows ultimas 50 velas)
    if len(rates) >= 50:
        recent_highs = highs[-50:]
        recent_lows = lows[-50:]
        resistance = float(np.max(recent_highs))
        support = float(np.min(recent_lows))
    else:
        resistance = float(np.max(highs))
        support = float(np.min(lows))

    return {
        "current": float(closes[-1]),
        "prev": float(closes[-2]) if len(closes) >= 2 else float(closes[-1]),
        "ema20": ema20,
        "ema50": ema50,
        "rsi": float(rsi),
        "atr": atr,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_width": bb_width,
        "resistance": resistance,
        "support": support,
        "high_recent": float(np.max(highs[-20:])) if len(highs) >= 20 else float(np.max(highs)),
        "low_recent": float(np.min(lows[-20:])) if len(lows) >= 20 else float(np.min(lows)),
    }


# ============================================================
# DETECTORES DE SETUP
# ============================================================
def detect_pullback_to_ema(ind: dict, rates) -> Optional[dict]:
    """Pullback al EMA20 en tendencia."""
    cur = ind["current"]
    e20 = ind["ema20"]
    e50 = ind["ema50"]
    # Tendencia alcista + price toca EMA20 + RSI 40-55
    if e20 > e50 and cur > e50 and cur < e20 * 1.005 and 38 <= ind["rsi"] <= 55:
        return {"type": "pullback_ema_long", "direction": "BUY"}
    # Tendencia bajista + price toca EMA20 + RSI 45-60
    if e20 < e50 and cur < e50 and cur > e20 * 0.995 and 45 <= ind["rsi"] <= 62:
        return {"type": "pullback_ema_short", "direction": "SELL"}
    return None


def detect_bounce_support_resistance(ind: dict) -> Optional[dict]:
    """Rebote en soporte/resistencia."""
    cur = ind["current"]
    sup = ind["support"]
    res = ind["resistance"]
    # Cerca de soporte (1% margen) + RSI <40 → LONG
    if cur < sup * 1.01 and ind["rsi"] < 42:
        return {"type": "bounce_support", "direction": "BUY"}
    # Cerca de resistencia (1% margen) + RSI >60 → SHORT
    if cur > res * 0.99 and ind["rsi"] > 58:
        return {"type": "bounce_resistance", "direction": "SELL"}
    return None


def detect_breakout(ind: dict, rates) -> Optional[dict]:
    """Breakout de banda superior/inferior con momentum."""
    cur = ind["current"]
    prev = ind["prev"]
    # Breakout alcista: cierra sobre BB upper con vela alcista
    if prev < ind["bb_upper"] < cur and cur > prev * 1.001:
        return {"type": "breakout_long", "direction": "BUY"}
    # Breakout bajista: cierra bajo BB lower con vela bajista
    if prev > ind["bb_lower"] > cur and cur < prev * 0.999:
        return {"type": "breakout_short", "direction": "SELL"}
    return None


def detect_rsi_reversal(ind: dict) -> Optional[dict]:
    """Reversion en RSI extremo."""
    rsi = ind["rsi"]
    cur = ind["current"]
    # RSI sobreventa + precio empezando a recuperar
    if rsi < 30 and cur > ind["prev"]:
        return {"type": "rsi_oversold", "direction": "BUY"}
    # RSI sobrecompra + precio empezando a bajar
    if rsi > 70 and cur < ind["prev"]:
        return {"type": "rsi_overbought", "direction": "SELL"}
    return None


def detect_squeeze_release(ind: dict, rates) -> Optional[dict]:
    """Bollinger squeeze release — bandas se abren tras compresión."""
    if ind["bb_width"] > 0.02:  # bandas anchas (no squeeze)
        return None
    cur = ind["current"]
    prev = ind["prev"]
    # Squeeze + breakout direccional
    if cur > prev * 1.003 and ind["rsi"] > 50:
        return {"type": "squeeze_long", "direction": "BUY"}
    if cur < prev * 0.997 and ind["rsi"] < 50:
        return {"type": "squeeze_short", "direction": "SELL"}
    return None


def find_all_setups(rates, ind) -> list:
    """Aplica todos los detectores y devuelve lista de setups encontrados."""
    setups = []
    for detector in [
        detect_pullback_to_ema,
        detect_bounce_support_resistance,
        detect_breakout,
        detect_rsi_reversal,
        detect_squeeze_release,
    ]:
        # Cada detector tiene firma distinta — try-except simple
        try:
            if detector in (detect_pullback_to_ema, detect_breakout, detect_squeeze_release):
                s = detector(ind, rates)
            else:
                s = detector(ind)
            if s:
                setups.append(s)
        except Exception as e:
            log.debug(f"detector error: {e}")
    return setups


# ============================================================
# CALCULO DE NIVELES (Entry/SL/TPs)
# ============================================================
def calculate_levels(setup: dict, ind: dict, pair_cfg: dict) -> Optional[dict]:
    """Calcula Entry, SL y 3 TPs con R:R 1.5x, 2.5x, 4x."""
    direction = setup["direction"]
    cur = ind["current"]
    atr = ind["atr"]

    if atr <= 0:
        return None

    # SL distance basada en ATR (ajustado al tipo de setup)
    sl_atr_mult = 1.5
    if "rsi" in setup["type"]:
        sl_atr_mult = 2.0  # más espacio para reversiones
    if "breakout" in setup["type"]:
        sl_atr_mult = 1.2  # más ajustado para breakouts

    sl_dist = atr * sl_atr_mult

    # FIX 2026-05-11 (noche): tp1 escalado a 2.0x SL para que R:R real por trade sea
    # 2:1, no 1.5:1. Combinado con MIN_RR=1.6 garantiza CALIDAD por señal: cada trade
    # potencialmente gana 2x lo que arriesga. Antes la formula daba R:R fijo 1.5 y
    # el filtro 1.6 rechazaba TODAS las señales (matematicamente imposible).
    # Resultado esperado: menos cantidad, mucho mejor R:R por señal.
    if direction == "BUY":
        entry = cur
        sl = cur - sl_dist
        tp1 = cur + sl_dist * 2.0   # R:R 2.0 (era 1.5)
        tp2 = cur + sl_dist * 3.0   # R:R 3.0 (era 2.5)
        tp3 = cur + sl_dist * 4.5   # R:R 4.5 (era 4.0)
    else:  # SELL
        entry = cur
        sl = cur + sl_dist
        tp1 = cur - sl_dist * 2.0
        tp2 = cur - sl_dist * 3.0
        tp3 = cur - sl_dist * 4.5

    # R:R check
    risk = abs(entry - sl)
    reward = abs(tp1 - entry)
    rr = reward / risk if risk > 0 else 0
    if rr < MIN_RR_RATIO:
        return None

    # Redondear al num de decimales del par
    d = pair_cfg["decimals"]
    return {
        "entry": round(entry, d),
        "sl": round(sl, d),
        "tp1": round(tp1, d),
        "tp2": round(tp2, d),
        "tp3": round(tp3, d),
        "rr": round(rr, 2),
        "atr": round(atr, d),
    }


# ============================================================
# SCORING HIBRIDO (usa signal_probability.py existente)
# ============================================================
def score_signal(symbol: str, direction: str, levels: dict) -> dict:
    """Llama a signal_probability para obtener score híbrido (técnico + Vision)."""
    try:
        from signal_probability import compute_signal_probability
        result = compute_signal_probability(
            pair=symbol, direction=direction,
            entry=levels["entry"], sl=levels["sl"], tp=levels["tp1"],
            mt5_symbol=symbol,
        )
        return result
    except Exception as e:
        log.warning(f"[GEN] score_signal error: {e}")
        return {"score": 50, "available": True, "source": "fallback"}


# ============================================================
# MENSAJE PARA TELEGRAM
# ============================================================
def format_signal_message(pair_cfg: dict, setup: dict, levels: dict, prob_score: int) -> str:
    """Mensaje formateado IDENTICO al resto de señales VIP — sin distintivo IA propia.
    El usuario quiere coherencia total con el resto del canal."""
    direction = setup["direction"]
    dir_emoji = "🟢" if direction == "BUY" else "🔴"
    # Display: BITCOIN/ETHEREUM en lugar de BTCUSD/ETHUSD para coherencia VIP
    display = pair_cfg["display"]
    decimals = pair_cfg["decimals"]
    # Formato sin separador de miles (regla canal VIP)
    fmt = f"{{:.{decimals}f}}".format

    lines = [
        f"{dir_emoji} *{direction} — {display}*",
        f"",
        f"📍 Entry: {fmt(levels['entry'])}",
        f"🎯 TP1: {fmt(levels['tp1'])}",
        f"🎯 TP2: {fmt(levels['tp2'])}",
        f"🎯 TP3: {fmt(levels['tp3'])}",
        f"🛡️ SL: {fmt(levels['sl'])}",
        f"",
        f"━━━━━━━━━━━━━━━━━━",
        f"📊 Probabilidad: {prob_score}%",
        f"",
        f"— _Eli · BuySell365 Pro_ 🤖",
    ]
    return "\n".join(lines)


# ============================================================
# PUBLICACION AL CANAL VIP
# ============================================================
def publish_signal(pair_cfg: dict, setup: dict, levels: dict, prob_score: int) -> bool:
    """Publica señal al canal VIP. Devuelve True si éxito."""
    try:
        token = os.getenv("TELEGRAM_TOKEN", "").strip()
        channel = os.getenv("CHANNEL_ID", "").strip()
        if not token or not channel:
            log.warning("[GEN] TELEGRAM_TOKEN o CHANNEL_ID vacíos")
            return False
        msg = format_signal_message(pair_cfg, setup, levels, prob_score)
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": channel, "text": msg, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=12,
        )
        if r.status_code == 200:
            msg_id = r.json().get("result", {}).get("message_id")
            log.info(f"[GEN] ✅ Publicado al VIP: {pair_cfg['symbol']} {setup['direction']} (msg_id={msg_id})")
            try:
                from whatsapp_notifier import notify_raw as _wsp_raw
                # FIX 2026-05-11: pasar probability=prob_score para que destinatarios
                # con min_probability puedan filtrar tambien las señales del generator.
                _wsp_raw(msg, pair=pair_cfg["display"], event="new_signal",
                         probability=prob_score)
            except Exception as _e_wsp_new:
                log.debug(f"[GEN] WhatsApp notify error: {_e_wsp_new}")
            return True
        log.warning(f"[GEN] Telegram error {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        log.warning(f"[GEN] publish error: {e}")
        return False


def execute_in_mt5(pair_cfg: dict, setup: dict, levels: dict):
    """Ejecuta la operación en MT5. Devuelve dict {ticket, mt5_entry} o None.
    Solo si GENERATOR_MT5_EXECUTE=true."""
    if not MT5_EXECUTE:
        log.info(f"[GEN] MT5 execution OFF, skip {pair_cfg['symbol']}")
        return None
    try:
        import price_feed as mt5
        if not mt5.initialize():
            return None
        symbol = pair_cfg["symbol"]
        info = mt5.symbol_info(symbol)
        if not info:
            return None
        # FIX 2026-05-11 (noche): unificar calculo de lot con el copier (1% riesgo del equity).
        # Antes: lot fijo GENERATOR_LOT_SIZE=0.01 que daba exposicion desbalanceada
        # comparada con las senales aliadas (que usan 1% riesgo del balance).
        # Ahora: mismo metodo — lot = (equity * risk%) / (sl_distance/tick_size * tick_value)
        import math
        vol_min = float(info.volume_min or 0.01)
        vol_step = float(info.volume_step or 0.01)
        vol_max = float(info.volume_max or 1000.0)

        # Lot dinamico por riesgo (mismo formula que signal_copier.py:8096-8126)
        lot = vol_min  # fallback inicial
        try:
            _risk_pct = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
            _acc_info = mt5.account_info()
            _equity = float(getattr(_acc_info, "equity", 0) or 0) if _acc_info else 0.0
            _sl_dist = abs(float(levels.get("entry", 0)) - float(levels.get("sl", 0)))
            _tick_val = float(getattr(info, "trade_tick_value", 0) or 0)
            _tick_size = float(getattr(info, "trade_tick_size", 0) or info.point or 0)

            if _equity > 0 and _sl_dist > 0 and _tick_val > 0 and _tick_size > 0:
                _risk_amount = _equity * (_risk_pct / 100.0)
                _loss_per_lot = (_sl_dist / _tick_size) * _tick_val
                if _loss_per_lot > 0:
                    _calc = _risk_amount / _loss_per_lot
                    _calc = round(_calc / vol_step) * vol_step
                    lot = max(vol_min, min(vol_max, round(_calc, 2)))
            log.info(f"[GEN] 📐 Lot {symbol}: equity=€{_equity:.0f} risk={_risk_pct}% sl_dist={_sl_dist:.5f} → {lot} lotes")
        except Exception as _e_lot:
            log.warning(f"[GEN] Lot calc fallo ({_e_lot}) — usando vol_min={vol_min}")
            lot = vol_min

        # GUARD lot-cap absoluto (paridad con signal_copier.py:8543 P1.5 del 12-may).
        # Caso 13-may: ETHUSD calc=7.33 lotes para cuenta de €9.9k — exposicion bestial.
        # El generator usaba su propio path sin este cap.
        _max_lot_abs = float(os.getenv("MAX_LOT_ABSOLUTE", "0.30"))
        if lot > _max_lot_abs:
            log.warning(
                f"[GEN] 🛡️ GUARD lot-cap: {symbol} calc={lot:.2f} > max {_max_lot_abs:.2f} — cap aplicado"
            )
            lot = _max_lot_abs

        # Final guard: respeta vol_step y vol_max del broker
        if vol_step > 0:
            lot = math.ceil(lot / vol_step) * vol_step
            lot = round(lot, 8)
        if vol_max > 0:
            lot = min(lot, vol_max)
        if lot < vol_min:
            lot = vol_min
        order_type = mt5.ORDER_TYPE_BUY if setup["direction"] == "BUY" else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(symbol)
        price = tick.ask if setup["direction"] == "BUY" else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": levels["sl"],
            "tp": levels["tp1"],  # TP1 inicial (luego mover a tp2/tp3 manual)
            "deviation": 20,
            "magic": 365001,
            "comment": "BS365_IA_Generator",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            mt5_entry = float(result.price) if result.price else float(price)
            log.info(f"[GEN] ✅ MT5 ejecutado: {symbol} {setup['direction']} lot={lot} ticket={result.order} entry={mt5_entry}")

            # FIX 2026-05-17: post-fill check — drift entry + SL discrepancy.
            # Caso 17-may BTC SELL ticket 770355780: VIP publicado SL=78339.31,
            # MT5 stored SL=78037.45 (broker ajusto). Cliente vio R:R 1:2, real fue
            # R:R 1:54 → cerro en 1h41min con -€3.
            # Caso BTC BUY ticket 770409481: VIP entry 78446.35, MT5 entry 78492.45
            # (drift 46 USD = 17% del SL distance) sin aviso.
            # Ahora: tras fill OK, leer la posicion REAL en MT5 y comparar.
            try:
                _theoric_entry = float(levels.get("entry", 0) or 0)
                _theoric_sl    = float(levels.get("sl", 0) or 0)
                _theoric_tp    = float(levels.get("tp1", 0) or 0)
                # Esperar 1s para que MT5 escriba la posicion
                import time as _t_pf
                _t_pf.sleep(1)
                _pos_pf = mt5.positions_get(ticket=int(result.order)) or []
                if _pos_pf:
                    _p_pf = _pos_pf[0]
                    _real_entry = float(_p_pf.price_open)
                    _real_sl    = float(_p_pf.sl or 0)
                    _real_tp    = float(_p_pf.tp or 0)

                    _sl_dist = abs(_theoric_entry - _theoric_sl) if _theoric_sl > 0 else 0
                    _drift_e = abs(_real_entry - _theoric_entry) if _theoric_entry > 0 else 0
                    _drift_s = abs(_real_sl - _theoric_sl) if (_real_sl > 0 and _theoric_sl > 0) else 0

                    _entry_drift_pct = (_drift_e / _sl_dist) if _sl_dist > 0 else 0
                    _sl_diff_pct     = (_drift_s / _sl_dist) if _sl_dist > 0 else 0

                    _entry_thr = float(os.getenv("ENTRY_DRIFT_NOTIFY_PCT", "0.10"))
                    _sl_thr    = float(os.getenv("SL_DISCREPANCY_NOTIFY_PCT", "0.20"))

                    _need_notify = False
                    _notify_lines = []
                    if _sl_dist > 0 and _entry_drift_pct > _entry_thr:
                        log.warning(
                            f"[GEN] ⚠️ ENTRY DRIFT {symbol}: teorica={_theoric_entry} fill={_real_entry} "
                            f"drift={_drift_e:.2f} ({_entry_drift_pct*100:.0f}% del SL)"
                        )
                        _notify_lines.append(f"Entry filled at *{_real_entry}* (planned {_theoric_entry})")
                        _need_notify = True
                    if _sl_dist > 0 and _sl_diff_pct > _sl_thr:
                        log.error(
                            f"[GEN] 🚨 SL DISCREPANCY {symbol}: VIP={_theoric_sl} MT5={_real_sl} "
                            f"diff={_drift_s:.2f} ({_sl_diff_pct*100:.0f}% del SL). Broker ajusto el SL."
                        )
                        _notify_lines.append(f"⚠️ SL adjusted by broker to *{_real_sl}* (planned {_theoric_sl})")
                        _need_notify = True

                    if _need_notify:
                        try:
                            _token_pf = os.getenv("TELEGRAM_TOKEN", "").strip()
                            _chan_pf  = os.getenv("CHANNEL_ID", "").strip()
                            if _token_pf and _chan_pf:
                                _dir_emoji = "🟢" if setup["direction"] == "BUY" else "🔴"
                                _pair_disp = pair_cfg.get("display", symbol)
                                _msg_pf = (
                                    f"📌 *Trade adjusted* — {_dir_emoji} {_pair_disp}\n"
                                    + "\n".join(_notify_lines)
                                    + "\n\n— _Eli · BuySell365 Pro_ 🤖"
                                )
                                requests.post(
                                    f"https://api.telegram.org/bot{_token_pf}/sendMessage",
                                    json={"chat_id": _chan_pf, "text": _msg_pf,
                                          "parse_mode": "Markdown",
                                          "disable_web_page_preview": True},
                                    timeout=10,
                                )
                                log.info(f"[GEN] 📌 Aviso ajuste enviado al VIP ({_pair_disp})")
                        except Exception as _e_pf_notif:
                            log.debug(f"[GEN] Post-fill notify error: {_e_pf_notif}")
            except Exception as _e_pf:
                log.debug(f"[GEN] Post-fill check error: {_e_pf}")

            return {"ticket": int(result.order), "mt5_entry": mt5_entry, "lot": lot}
        log.warning(f"[GEN] MT5 falló: {result.comment if result else 'no result'}")
        return None
    except Exception as e:
        log.warning(f"[GEN] MT5 execute error: {e}")
        return None


def save_signal_log(signal_data: dict):
    """Guarda señal a JSON para tracking/backtest."""
    try:
        signals = []
        if _LOG_FILE.exists():
            try:
                with open(_LOG_FILE, "r", encoding="utf-8") as f:
                    signals = json.load(f)
            except Exception:
                signals = []
        signals.append(signal_data)
        # Mantener últimas 1000 señales
        signals = signals[-1000:]
        with open(_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(signals, f, indent=2, default=str)
    except Exception as e:
        log.warning(f"[GEN] log save error: {e}")


def queue_for_tracking(signal_data: dict):
    """Encola la señal para que signal_copier la registre en su tracker.
    El monitor del copier se encarga de TP/SL/celebraciones automáticamente.
    """
    try:
        with _queue_lock:
            queue = []
            if _QUEUE_FILE.exists():
                try:
                    with open(_QUEUE_FILE, "r", encoding="utf-8") as f:
                        queue = json.load(f)
                except Exception:
                    queue = []
            queue.append(signal_data)
            # Cap a 200 entradas (señales viejas se purgan tras ingesta)
            queue = queue[-200:]
            tmp = str(_QUEUE_FILE) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(queue, f, ensure_ascii=False, indent=2, default=str)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    pass
            os.replace(tmp, str(_QUEUE_FILE))
    except Exception as e:
        log.warning(f"[GEN] queue write error: {e}")


# ============================================================
# LOOP PRINCIPAL
# ============================================================
def scan_pair(pair_cfg: dict) -> int:
    """Escanea un par en múltiples timeframes. Devuelve cantidad de señales emitidas."""
    try:
        import price_feed as mt5
        if not mt5.initialize():
            return 0

        emitted = 0
        symbol = pair_cfg["symbol"]
        _reset_daily_counter_if_new_day()

        # Check daily limit
        today_count = _signals_today.get(symbol, 0)
        if today_count >= MAX_SIGNALS_PER_DAY_PER_PAIR:
            log.debug(f"[GEN] {symbol} límite diario alcanzado ({today_count})")
            return 0

        # Escanear timeframes
        tf_map = {"M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}
        for tf_name in TIMEFRAMES:
            tf = tf_map[tf_name]
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, 100)
            if rates is None or len(rates) < 50:
                continue
            ind = compute_indicators(rates)

            # Filtro ATR mínimo
            min_atr = pair_cfg.get("min_atr_pips", 1)
            if ind["atr"] < min_atr:
                continue

            # Detectar setups
            setups = find_all_setups(rates, ind)
            for setup in setups:
                # Anti-duplicado por par+dirección
                if _is_dedup(symbol, setup["direction"]):
                    continue

                # Calcular niveles
                levels = calculate_levels(setup, ind, pair_cfg)
                if not levels:
                    continue

                # Score híbrido (Vision + técnico)
                prob = score_signal(symbol, setup["direction"], levels)
                score = prob.get("score", 50)
                if score is None:
                    score = 50

                # FIX 2026-05-15: log componentes del score cuando descartamos.
                # Caso 15-may 18:00-18:50 logs: 25+ evaluaciones BTC/ETH con scores
                # 27-55% (todos < 70). Sin desglose era imposible saber si el problema
                # era el mercado o un input siempre a 0 aplastando el agregado.
                if score < MIN_PROBABILITY_TO_PUBLISH:
                    _tech = prob.get("tech_score")
                    _vision = prob.get("vision_score")
                    _src = prob.get("source", "?")
                    log.info(
                        f"[GEN] {symbol} {setup['direction']} score {score}% "
                        f"< {MIN_PROBABILITY_TO_PUBLISH}% — descartado "
                        f"(tech={_tech} vision={_vision} src={_src} "
                        f"entry={levels.get('entry')} sl={levels.get('sl')} tp={levels.get('tp')})"
                    )
                    continue

                # GUARD spread + drift (13-may): broker XM en madrugada metio ~5 USD spread
                # en ETHUSD y publicamos entry teorica 2302.89 mientras ask real 2307.23.
                # Operacion nacio con 4.24 USD en rojo. Aqui:
                #  - Si spread > X% del SL → skip (no merece la pena entrar).
                #  - Si ask/bid real difiere > Y% del SL de la entry teorica → re-anclar
                #    para que el VIP vea el precio al que REALMENTE se ejecutara.
                try:
                    import price_feed as _mt5_g
                    if _mt5_g.initialize():
                        _tick_g = _mt5_g.symbol_info_tick(symbol)
                        if _tick_g:
                            _spread_g = abs(float(_tick_g.ask) - float(_tick_g.bid))
                            _sl_dist_g = abs(float(levels["entry"]) - float(levels["sl"]))
                            _max_spread_pct = float(os.getenv("GEN_MAX_SPREAD_PCT_OF_SL", "0.25"))
                            if _sl_dist_g > 0 and _spread_g > _sl_dist_g * _max_spread_pct:
                                log.warning(
                                    f"[GEN] 🛡️ SKIP spread alto: {symbol} spread={_spread_g:.2f} "
                                    f"> {_max_spread_pct*100:.0f}% del SL ({_sl_dist_g:.2f}) — no publica"
                                )
                                continue
                            # Re-anclar entry al precio real si hay drift
                            _real_px = float(_tick_g.ask) if setup["direction"] == "BUY" else float(_tick_g.bid)
                            _drift_g = abs(_real_px - float(levels["entry"]))
                            _max_drift_pct = float(os.getenv("GEN_MAX_ENTRY_DRIFT_PCT_OF_SL", "0.20"))
                            if _sl_dist_g > 0 and _drift_g > _sl_dist_g * _max_drift_pct:
                                log.warning(
                                    f"[GEN] ⚠️ Drift entry: teorica={levels['entry']:.2f} "
                                    f"real={_real_px:.2f} (drift {_drift_g:.2f}) — re-anclando al precio real"
                                )
                                levels["entry"] = _real_px
                except Exception as _e_guard:
                    log.warning(f"[GEN] spread/drift guard fallo ({_e_guard}) — continuando sin guard")

                # FIX 2026-05-16: anti-stack robusto — antes de publicar, verificar
                # que NO haya ya una posicion abierta del mismo par+direccion en
                # MT5 ni en copier_open_signals.json. El dedup temporal de 30min
                # NO basta: si la posicion #1 sigue abierta tras 30min, el dedup
                # expira y publicamos una #2 sobre la misma direccion. Caso real
                # 16-may: 3 ETHUSD SELL simultaneas (tickets 770286180/770290549/
                # 770294386) acumulando riesgo 3x.
                try:
                    import price_feed as _mt5_as
                    if _mt5_as.initialize():
                        _open_pos = _mt5_as.positions_get(symbol=symbol) or []
                        _same_dir_open = False
                        for _p_as in _open_pos:
                            _p_dir = "BUY" if _p_as.type == _mt5_as.ORDER_TYPE_BUY else "SELL"
                            if _p_dir == setup["direction"]:
                                _same_dir_open = True
                                log.warning(
                                    f"[GEN] 🛡️ ANTI-STACK: {symbol} {setup['direction']} "
                                    f"ya tiene posicion abierta ticket={_p_as.ticket} "
                                    f"entry={_p_as.price_open} — NO publicar nueva"
                                )
                                break
                        if _same_dir_open:
                            continue
                except Exception as _e_as:
                    log.warning(f"[GEN] anti-stack check fallo ({_e_as}) — continuando sin guard")
                # Verificar tambien copier_open_signals.json por si el monitor del
                # copier tiene la senal pero MT5 ya cerro (caso edge).
                try:
                    _cop_open_path = str(_BASE / "copier_open_signals.json")
                    if os.path.exists(_cop_open_path):
                        with open(_cop_open_path, "r", encoding="utf-8") as _f_as:
                            _cop_open_as = json.load(_f_as) or {}
                        for _sid_as, _sd_as in _cop_open_as.items():
                            _s_as = _sd_as.get("signal", {}) if isinstance(_sd_as, dict) else {}
                            if (str(_s_as.get("pair", "")).upper() == symbol.upper()
                                    and str(_s_as.get("direction", "")).upper() == setup["direction"]):
                                log.warning(
                                    f"[GEN] 🛡️ ANTI-STACK (tracker): {symbol} "
                                    f"{setup['direction']} ya esta en copier_open_signals "
                                    f"(sid={_sid_as}) — NO publicar nueva"
                                )
                                _same_dir_open = True
                                break
                        if _same_dir_open:
                            continue
                except Exception as _e_as2:
                    log.debug(f"[GEN] anti-stack tracker check error: {_e_as2}")

                # Publicar al canal VIP
                ok = publish_signal(pair_cfg, setup, levels, score)
                if ok:
                    # FIX 2026-05-11 (tarde-3): marcar dedup SOLO tras publish exitoso
                    _mark_dedup(symbol, setup["direction"])
                    _signals_today[symbol] = _signals_today.get(symbol, 0) + 1
                    _save_state()  # persistir contador para sobrevivir reinicio
                    emitted += 1

                    # Ejecutar en MT5 si está habilitado
                    mt5_result = None
                    if MT5_EXECUTE:
                        mt5_result = execute_in_mt5(pair_cfg, setup, levels)

                    mt5_ticket = mt5_result["ticket"] if mt5_result else None
                    mt5_entry = mt5_result["mt5_entry"] if mt5_result else None
                    mt5_lot = mt5_result["lot"] if mt5_result else None

                    # Encolar para que signal_copier la trackee igual que las del copier
                    # (TP/SL monitor + celebraciones foto VIP + video grupo + todo automático)
                    queue_for_tracking({
                        "ts": time.time(),
                        "ts_iso": datetime.utcnow().isoformat(),
                        "ingested": False,
                        "source": "BS365_IA_Generator",
                        "symbol": symbol,
                        "mt5_symbol": symbol,
                        "pair": symbol,
                        "pair_display": pair_cfg["display"],
                        "direction": setup["direction"],
                        "entry": levels["entry"],
                        "mt5_entry": mt5_entry,  # precio REAL de ejecucion MT5
                        "sl": levels["sl"],
                        "tp": levels["tp1"],
                        "tp1": levels["tp1"],
                        "tp2": levels["tp2"],
                        "tp3": levels["tp3"],
                        "score": score,
                        "tech_score": prob.get("tech_score"),
                        "vision_score": prob.get("vision_score"),
                        "rr": levels["rr"],
                        "setup_type": setup["type"],
                        "timeframe": tf_name,
                        "mt5_ticket": mt5_ticket,
                        "mt5_lot": mt5_lot,
                        "mt5_executed": mt5_ticket is not None,
                    })

                    # Log para análisis
                    save_signal_log({
                        "ts": datetime.utcnow().isoformat(),
                        "symbol": symbol,
                        "direction": setup["direction"],
                        "setup": setup["type"],
                        "timeframe": tf_name,
                        "entry": levels["entry"],
                        "sl": levels["sl"],
                        "tp1": levels["tp1"],
                        "tp2": levels["tp2"],
                        "tp3": levels["tp3"],
                        "rr": levels["rr"],
                        "score": score,
                        "tech_score": prob.get("tech_score"),
                        "vision_score": prob.get("vision_score"),
                        "mt5_ticket": mt5_ticket,
                    })

                    # Solo 1 señal por timeframe por scan (evita spam)
                    break

            # Si ya emitimos varias en este par, parar
            if today_count + emitted >= MAX_SIGNALS_PER_DAY_PER_PAIR:
                break
        return emitted
    except Exception as e:
        log.warning(f"[GEN] scan_pair {pair_cfg['symbol']} error: {e}")
        return 0


def run_loop():
    """Loop principal — ejecuta cada SCAN_INTERVAL_S."""
    log.info(f"[GEN] 🚀 BTC+ETH Generator iniciado (intervalo {SCAN_INTERVAL_S}s)")
    log.info(f"[GEN]   ENABLED: {ENABLED}, MT5_EXECUTE: {MT5_EXECUTE}")
    log.info(f"[GEN]   MIN_PROB: {MIN_PROBABILITY_TO_PUBLISH}%, MIN_RR: {MIN_RR_RATIO}")
    log.info(f"[GEN]   Pairs: {[p['symbol'] for p in PAIRS]}")
    _load_state()  # FIX 2026-05-09: cargar estado persistido para no regenerar tras reinicio

    while True:
        try:
            if not ENABLED:
                time.sleep(60)
                continue

            for pair_cfg in PAIRS:
                count = scan_pair(pair_cfg)
                if count > 0:
                    log.info(f"[GEN] {pair_cfg['symbol']}: +{count} señal(es) este scan, total hoy: {_signals_today.get(pair_cfg['symbol'], 0)}")

            time.sleep(SCAN_INTERVAL_S)
        except Exception as e:
            log.warning(f"[GEN] loop error: {e}")
            time.sleep(60)


def start_in_thread():
    """Arranca el generator en un thread daemon."""
    if not ENABLED:
        log.info("[GEN] Generator deshabilitado (GENERATOR_ENABLED=false)")
        return None
    t = threading.Thread(target=run_loop, daemon=True, name="btc_eth_generator")
    t.start()
    log.info("[GEN] ✅ Thread iniciado")
    return t


if __name__ == "__main__":
    # Modo CLI: scan once y reporta
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    print("=== BTC+ETH Generator — Scan único ===\n")
    if not ENABLED:
        print("⚠️  GENERATOR_ENABLED=false — set GENERATOR_ENABLED=true para activar")
    for pair_cfg in PAIRS:
        print(f"\n--- Escaneando {pair_cfg['symbol']} ---")
        count = scan_pair(pair_cfg)
        print(f"  Señales emitidas: {count}")
        print(f"  Total hoy: {_signals_today.get(pair_cfg['symbol'], 0)}")

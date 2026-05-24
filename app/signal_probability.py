"""
signal_probability.py — Sistema híbrido de scoring de señales
============================================================

Análisis pre-publicación de cada señal del canal VIP. Calcula un % de
probabilidad de éxito basado en:
  Capa A — Análisis técnico (indicadores MT5): ~30% del peso
  Capa B — Claude Sonnet 4.6 + Vision (gráfica real): ~70% del peso

NUNCA descarta ni filtra señales. Solo añade información transparente.
Si falla todo el análisis → la señal se publica sin la línea de score.

Uso:
    from signal_probability import compute_signal_probability
    result = compute_signal_probability(
        pair="GOLD", direction="BUY", entry=4720.5,
        sl=4710.0, tp=4735.0, mt5_symbol="GOLD",
    )
    # result = {"score": 78, "available": True, "source": "hybrid",
    #           "tech_score": 72, "vision_score": 81, "error": None}
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from typing import Optional

log = logging.getLogger("signal_probability")

# ============================================================
# CONFIG
# ============================================================
ENABLED = os.getenv("SIGNAL_PROBABILITY_ENABLED", "true").lower() in ("true", "1", "yes")
VISION_TIMEOUT_S = float(os.getenv("VISION_TIMEOUT_S", "20"))
TECH_WEIGHT = float(os.getenv("TECH_WEIGHT", "0.3"))   # 30% indicadores
VISION_WEIGHT = float(os.getenv("VISION_WEIGHT", "0.7"))  # 70% Vision

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("VISION_MODEL", "claude-sonnet-4-6")

# Auto-disable tras 3 fallos consecutivos durante 1h
_failure_count = 0
_disabled_until = 0.0
_failure_lock = threading.Lock()
_last_admin_alert = 0.0  # rate-limit del DM al admin


# ============================================================
# AUTO-DISABLE / RECOVERY
# ============================================================
def _is_disabled() -> bool:
    return time.time() < _disabled_until


def _record_failure(reason: str) -> None:
    global _failure_count, _disabled_until, _last_admin_alert
    with _failure_lock:
        _failure_count += 1
        if _failure_count >= 3:
            _disabled_until = time.time() + 3600  # pausa 1h
            log.warning(f"signal_probability auto-disabled 1h tras 3 fallos consecutivos. Último: {reason}")
            # DM al admin
            now = time.time()
            if now - _last_admin_alert > 1800:  # max 1 alerta cada 30 min
                _last_admin_alert = now
                _notify_admin_failure(reason, paused=True)
            _failure_count = 0  # reset


def _record_success() -> None:
    global _failure_count
    with _failure_lock:
        _failure_count = 0


def _notify_admin_failure(reason: str, paused: bool = False) -> None:
    """DM al admin cuando hay fallos. Best-effort, nunca bloquea.
    FIX 2026-05-08: usar nombres reales de env vars (USER_ID_1 + TELEGRAM_TOKEN).
    Antes usaba ADMIN_USER_ID/BOT_TOKEN que no existen → DM nunca llegaba.
    """
    try:
        admin_id = (os.getenv("USER_ID_1", "").strip()
                    or os.getenv("ADMIN_ID", "").strip()
                    or "8696207137")  # fallback al ID conocido del creador
        bot_token = (os.getenv("TELEGRAM_TOKEN", "").strip()
                     or os.getenv("BOT_TOKEN", "").strip())
        if not admin_id or not bot_token:
            return
        import requests as _rq
        if paused:
            msg = f"⚠️ *Vision análisis pausado 1h*\n3 fallos consecutivos.\nÚltimo: `{reason[:120]}`\nReanudará en 60 min."
        else:
            msg = f"⚠️ Vision análisis falló: `{reason[:120]}`\nSeñal publicada sin score."
        _rq.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": admin_id, "text": msg, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception:
        pass


# ============================================================
# CAPA A — ANÁLISIS TÉCNICO (indicadores MT5)
# ============================================================
def _compute_indicators(rates) -> dict:
    """Calcula EMA20, EMA50, RSI(14), ATR(14) sobre array MT5 OHLC."""
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

    # RSI(14)
    def _rsi(arr, n=14):
        if len(arr) < n + 1:
            return 50.0
        diffs = np.diff(arr)
        gains = np.where(diffs > 0, diffs, 0)
        losses = np.where(diffs < 0, -diffs, 0)
        avg_gain = np.mean(gains[-n:])
        avg_loss = np.mean(losses[-n:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100 - 100 / (1 + rs))

    rsi = _rsi(closes)

    # ATR(14)
    if len(rates) >= 14:
        trs = []
        for i in range(1, min(len(rates), 15)):
            tr = max(
                highs[-i] - lows[-i],
                abs(highs[-i] - closes[-i - 1]),
                abs(lows[-i] - closes[-i - 1]),
            )
            trs.append(tr)
        atr = float(np.mean(trs)) if trs else 0.0
    else:
        atr = 0.0

    return {
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi,
        "atr": atr,
        "current": float(closes[-1]),
    }


def _technical_score(pair: str, direction: str, entry: float, sl: float, tp: float,
                     mt5_symbol: str) -> tuple[int, list[str]]:
    """Capa A: 0-100 score basado en indicadores. Devuelve (score, reasons)."""
    try:
        import price_feed as mt5
    except ImportError:
        return 50, ["mt5 no disponible"]

    if not mt5.initialize():
        return 50, ["mt5 no inicializado"]

    sym = mt5_symbol or pair
    rates_m15 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 100)
    rates_h1 = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_H1, 0, 100)

    if rates_m15 is None or len(rates_m15) < 50:
        return 50, ["sin OHLC"]

    ind_m15 = _compute_indicators(rates_m15)
    ind_h1 = _compute_indicators(rates_h1) if rates_h1 is not None and len(rates_h1) >= 50 else None

    score = 50  # base neutra
    reasons = []
    is_buy = direction.upper() == "BUY"

    # 1. Tendencia M15 (peso: ±15)
    if is_buy:
        if ind_m15["current"] > ind_m15["ema20"] > ind_m15["ema50"]:
            score += 15
            reasons.append("Tendencia M15 alcista")
        elif ind_m15["current"] < ind_m15["ema50"]:
            score -= 15
            reasons.append("Contra-tendencia M15")
    else:
        if ind_m15["current"] < ind_m15["ema20"] < ind_m15["ema50"]:
            score += 15
            reasons.append("Tendencia M15 bajista")
        elif ind_m15["current"] > ind_m15["ema50"]:
            score -= 15
            reasons.append("Contra-tendencia M15")

    # 2. Tendencia H1 (peso: ±10)
    if ind_h1:
        if is_buy and ind_h1["current"] > ind_h1["ema50"]:
            score += 10
        elif not is_buy and ind_h1["current"] < ind_h1["ema50"]:
            score += 10
        else:
            score -= 5

    # 3. R:R ratio (peso: ±10)
    if entry > 0 and sl > 0 and tp > 0:
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk > 0:
            rr = reward / risk
            if rr >= 1.5:
                score += 10
                reasons.append(f"R:R 1:{rr:.1f} favorable")
            elif rr < 0.8:
                score -= 10
                reasons.append(f"R:R 1:{rr:.1f} subóptimo")

    # 4. SL vs ATR (peso: ±5)
    if ind_m15["atr"] > 0 and entry > 0 and sl > 0:
        sl_dist = abs(entry - sl)
        if sl_dist < ind_m15["atr"] * 0.5:
            score -= 5
            reasons.append("SL muy ajustado vs ATR")
        elif sl_dist > ind_m15["atr"] * 5:
            score -= 5
            reasons.append("SL muy ancho vs ATR")

    # 5. RSI extremo en contra (peso: ±5)
    if is_buy and ind_m15["rsi"] > 75:
        score -= 5
        reasons.append(f"RSI {ind_m15['rsi']:.0f} sobrecompra")
    elif not is_buy and ind_m15["rsi"] < 25:
        score -= 5
        reasons.append(f"RSI {ind_m15['rsi']:.0f} sobreventa")

    score = max(5, min(95, score))
    return score, reasons


# ============================================================
# CAPA B — CLAUDE SONNET 4.6 + VISION
# ============================================================
def _render_chart_b64(pair: str, direction: str, entry: float, sl: float, tp: float,
                     mt5_symbol: str) -> Optional[str]:
    """Genera screenshot del gráfico M15 con niveles dibujados. Devuelve base64."""
    try:
        import price_feed as mt5
        import pandas as pd
        import mplfinance as mpf
        import matplotlib.pyplot as plt
        import base64

        if not mt5.initialize():
            return None
        sym = mt5_symbol or pair
        # FIX 2026-05-18 P1.11: 80 velas M15 = solo ~20h. Si TP/SL caen fuera
        # del rango visible, Vision puntua sin verlos. Subimos a 200 velas
        # (~50h) y si aun asi entry/tp/sl quedan fuera del high/low del chart,
        # ampliamos el eje Y para asegurar que TODOS los niveles caben.
        rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, 200)
        if rates is None or len(rates) < 30:
            return None

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df = df.set_index('time')
        df = df[['open', 'high', 'low', 'close', 'tick_volume']]
        df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']

        # Niveles a dibujar
        hlines = []
        colors = []
        if entry > 0:
            hlines.append(entry); colors.append('blue')
        if tp > 0:
            hlines.append(tp); colors.append('green')
        if sl > 0:
            hlines.append(sl); colors.append('red')

        # FIX 2026-05-18 P1.11: forzar que entry/tp/sl caigan dentro del eje Y
        # del chart aunque esten lejos del precio actual. Sin esto, mplfinance
        # auto-escala al rango de las velas y Vision no ve la linea horizontal.
        try:
            _y_min = float(df[['Low']].min().iloc[0])
            _y_max = float(df[['High']].max().iloc[0])
            for _lvl in hlines:
                if _lvl > 0:
                    _y_min = min(_y_min, _lvl)
                    _y_max = max(_y_max, _lvl)
            # 2% de margen para que las lineas no toquen el borde
            _pad = (_y_max - _y_min) * 0.02 if (_y_max - _y_min) > 0 else 0
            _ylim = (_y_min - _pad, _y_max + _pad)
        except Exception:
            _ylim = None

        buf = io.BytesIO()
        _plot_kwargs = dict(
            type='candle', style='charles',
            hlines=dict(hlines=hlines, colors=colors, linewidths=1.2, linestyle='--'),
            title=f"{pair} {direction} · M15",
            volume=False, figsize=(8, 5),
            savefig=dict(fname=buf, dpi=80, bbox_inches='tight'),
        )
        if _ylim:
            _plot_kwargs['ylim'] = _ylim
        mpf.plot(df, **_plot_kwargs)
        plt.close('all')
        buf.seek(0)
        return base64.b64encode(buf.read()).decode('ascii')
    except Exception as e:
        log.debug(f"render_chart error: {e}")
        return None


def _vision_score(pair: str, direction: str, entry: float, sl: float, tp: float,
                  mt5_symbol: str) -> tuple[Optional[int], Optional[str]]:
    """Capa B: llama a Claude Vision con el gráfico. Devuelve (score, error)."""
    if not ANTHROPIC_API_KEY:
        return None, "no api key"

    img_b64 = _render_chart_b64(pair, direction, entry, sl, tp, mt5_symbol)
    if not img_b64:
        return None, "render chart failed"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=VISION_TIMEOUT_S)

        prompt = (
            f"Analyze this trading chart for signal viability.\n\n"
            f"SIGNAL: {direction.upper()} {pair}\n"
            f"Entry: {entry}\n"
            f"Stop Loss: {sl}\n"
            f"Take Profit 1: {tp}\n\n"
            f"Looking at the chart (M15), evaluate the probability that TP1 will hit BEFORE SL hits, "
            f"based on:\n"
            f"- Current trend alignment with signal direction\n"
            f"- Price structure (support/resistance, swing highs/lows)\n"
            f"- Recent momentum (last 5-10 candles)\n"
            f"- Distance to entry vs current price action\n\n"
            f"Respond ONLY in this exact format (no other text):\n"
            f"SCORE: <0-100>\n"
            f"REASON: <one short sentence>"
        )

        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png", "data": img_b64
                    }},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text = resp.content[0].text if resp.content else ""

        # Parse "SCORE: 78"
        import re
        m = re.search(r'SCORE:\s*(\d{1,3})', text, re.IGNORECASE)
        if not m:
            return None, f"no score in response: {text[:80]}"
        score = int(m.group(1))
        score = max(5, min(95, score))
        return score, None
    except Exception as e:
        return None, f"vision call: {str(e)[:100]}"


# ============================================================
# API PÚBLICA
# ============================================================
def compute_signal_probability(pair: str, direction: str, entry: float,
                               sl: float, tp: float, mt5_symbol: str = "") -> dict:
    """Pipeline completo: técnico + Vision híbrido.

    Returns:
        {
          "score": int 5-95 | None,
          "available": bool,
          "source": "hybrid" | "tech_only" | "disabled" | "error",
          "tech_score": int | None,
          "vision_score": int | None,
          "reasons": list[str],
          "error": str | None,
        }
    """
    result = {
        "score": None, "available": False, "source": "error",
        "tech_score": None, "vision_score": None,
        "reasons": [], "error": None,
    }

    if not ENABLED:
        result["source"] = "disabled"
        result["error"] = "deshabilitado por env"
        return result

    # FIX 2026-05-18 P1.9: si Vision esta auto-pausada, NO retornar vacio —
    # caer a tech_only para que la senal SIEMPRE se publique con su % (regla
    # del usuario 18-may: todas las senales al VIP con probabilidad visible,
    # sin importar score). Antes durante esta hora salian sin la linea de
    # probabilidad, rompiendo la promesa al cliente.
    _vision_paused = _is_disabled()

    if not all([pair, direction, entry > 0]):
        result["error"] = "params inválidos"
        return result

    # === Capa A (técnica) ===
    try:
        tech_score, reasons = _technical_score(pair, direction, entry, sl, tp, mt5_symbol)
        result["tech_score"] = tech_score
        result["reasons"] = reasons
    except Exception as e:
        log.debug(f"tech_score error: {e}")
        tech_score = None

    # === Capa B (Vision) ===
    # FIX 2026-05-14: skip Vision si tech_score muy bajo. Anthropic Vision cuesta
    # ~$0.008/llamada. Hoy el generator hizo 336 llamadas y solo 2 pasaron el 70%
    # (99.4% wasted). Matematica:
    #   final = tech*0.3 + vision*0.7
    #   Para final>=70 con tech=30: vision necesita >=87 (extremadamente raro)
    #   Para final>=70 con tech=40: vision necesita >=83 (muy raro)
    #   Para final>=70 con tech=50: vision necesita >=79 (raro)
    #   Para final>=70 con tech=60: vision necesita >=74 (raro)
    # FIX 2026-05-16: subimos el umbral a 60 por defecto. En auditoria del CSV
    # post-fix (14-15 may), el ahorro real fue solo ~25% en lugar del 60% esperado.
    # Subir el threshold a 60 mata las llamadas Vision en BTC/ETH SELL con tech
    # alto-medio (50-60) donde Vision casi nunca saca >=74. Ahorro adicional
    # estimado: ~$0.80/dia = $24/mes. Configurable via SKIP_VISION_IF_TECH_BELOW.
    vision_score = None
    vision_err = None
    _skip_threshold = float(os.getenv("SKIP_VISION_IF_TECH_BELOW", "60"))
    # FIX 2026-05-18 P1.9: si Vision esta auto-pausada, saltar la llamada
    # pero conservar el tech_score para que la senal salga con score visible
    # (tech_only). No spamea fallos durante la hora de pausa.
    if _vision_paused:
        vision_err = "vision auto-paused (tech_only fallback)"
        log.debug(f"vision SKIP {pair} {direction}: auto-paused, fallback tech_only")
    elif tech_score is not None and tech_score < _skip_threshold:
        vision_err = f"skipped (tech_score={tech_score} < {_skip_threshold:.0f})"
        # FIX 2026-05-14: log visible para confirmar que el skip funciona y
        # cuanto se ahorra en Vision API. Antes era debug y no aparecia en logs.
        log.info(f"💰 Vision SKIP {pair} {direction}: tech={tech_score} < {_skip_threshold:.0f} (ahorro ~$0.008 por llamada)")
    else:
        try:
            vision_score, vision_err = _vision_score(pair, direction, entry, sl, tp, mt5_symbol)
            result["vision_score"] = vision_score
        except Exception as e:
            vision_err = f"exception: {str(e)[:100]}"
            log.debug(f"vision_score exception: {e}")

    # === Combinar ===
    # FIX 2026-05-16: distinguir SKIP intencional (tech<40, ahorro coste) de FALLO real.
    # Antes _record_failure() se llamaba SIEMPRE que vision_err estaba seteado, incluso
    # cuando era "skipped (tech_score=35 < 40)" — un skip planeado por diseño. Resultado:
    # tras 3 skips consecutivos (situacion normal en mercado bajista con BUY setups),
    # auto-disable 1h y todo el generator publicaba con src=disabled score=50.
    # Caso 16-may 03:00: BTC/ETH BUY bajada de tendencia clara, 3 SKIPs seguidos,
    # auto-disable y luego 4 senales con src=disabled descartadas por score=50 < 70.
    _is_intentional_skip = isinstance(vision_err, str) and (
        vision_err.startswith("skipped") or vision_err.startswith("vision auto-paused")
    )

    if vision_score is not None and tech_score is not None:
        final = round(tech_score * TECH_WEIGHT + vision_score * VISION_WEIGHT)
        result["score"] = max(5, min(95, final))
        result["source"] = "hybrid"
        result["available"] = True
        _record_success()
    elif tech_score is not None:
        # Vision falló o se salto a proposito → usar técnico solo
        result["score"] = tech_score
        result["source"] = "tech_only"
        result["available"] = True
        result["error"] = vision_err
        if vision_err and not _is_intentional_skip:
            _record_failure(vision_err)
        elif _is_intentional_skip:
            # Un skip intencional cuenta como exito parcial — resetea el contador
            # para que skips repetidos NO disparen el auto-disable.
            _record_success()
    else:
        # Ambos fallaron → no score
        result["error"] = vision_err or "tech+vision failed"
        if vision_err and not _is_intentional_skip:
            _record_failure(vision_err)

    return result


def format_score_line(result: dict) -> str:
    """Devuelve la línea de probabilidad lista para concatenar al mensaje VIP.
    Vacía si no hay score disponible."""
    if not result.get("available") or result.get("score") is None:
        return ""
    return f"\n━━━━━━━━━━━━━━━━━━\n📊 Probabilidad: {result['score']}%"

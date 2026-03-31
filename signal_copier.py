"""
BuySell365 Signal Copier — Userbot que escucha canales VIP de Telegram
Lee señales de SureShotFX, Learn2Trade, etc. y las ejecuta en MT5 + reenvía al canal BuySell365.
Usa Telethon (cuenta personal de Telegram).
"""
import os, re, asyncio, logging, time, json, threading, io
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# === CONFIG ===
API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
PHONE = os.getenv("TG_PHONE", "")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

SESSION_FILE = str(Path(__file__).parent / "signal_copier_session")

# Fix sqlite locked: set WAL mode and timeout
import sqlite3
_session_db = SESSION_FILE + ".session"
if os.path.exists(_session_db):
    try:
        _conn = sqlite3.connect(_session_db, timeout=5)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.close()
    except Exception:
        # If locked, delete and let Telethon recreate
        try:
            os.remove(_session_db)
            _journal = _session_db + "-journal"
            if os.path.exists(_journal):
                os.remove(_journal)
        except Exception:
            pass

# === LOGGING ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s [COPIER] %(message)s",
                    handlers=[logging.FileHandler(Path(__file__).parent / "logs" / "copier.log", encoding="utf-8"),
                              logging.StreamHandler()])
log = logging.getLogger("copier")

# === SYMBOL MAP — todos los pares de canales aliados (sin duplicados) ===
SYMBOL_MAP = {
    # Oro
    "XAUUSD": "GOLD", "GOLD": "GOLD", "ORO": "GOLD",
    # Índices
    "NAS100": "US100Cash", "NASDAQ": "US100Cash", "US100": "US100Cash",
    "NASDAQ100": "US100Cash", "NQ": "US100Cash",
    "US30": "US30Cash", "DOW": "US30Cash", "DJ30": "US30Cash",
    "SPX500": "US500Cash", "SP500": "US500Cash", "US500": "US500Cash",
    "GER40": "GER40Cash", "DAX": "GER40Cash", "DE40": "GER40Cash",
    # Petróleo
    "BRENT": "BRENT", "UKOIL": "BRENT", "OIL": "BRENT",
    # Pares USD principales
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "AUDUSD": "AUDUSD",
    "NZDUSD": "NZDUSD", "USDCAD": "USDCAD", "USDCHF": "USDCHF",
    "USDJPY": "USDJPY",
    # Pares GBP
    "GBPJPY": "GBPJPY", "GBPAUD": "GBPAUD", "GBPNZD": "GBPNZD",
    "GBPCAD": "GBPCAD", "GBPCHF": "GBPCHF",
    # Pares EUR
    "EURJPY": "EURJPY", "EURAUD": "EURAUD", "EURGBP": "EURGBP",
    "EURCHF": "EURCHF", "EURCAD": "EURCAD", "EURNZD": "EURNZD",
    # Pares AUD
    "AUDJPY": "AUDJPY", "AUDCAD": "AUDCAD", "AUDNZD": "AUDNZD",
    "AUDCHF": "AUDCHF",
    # Pares JPY cruzados
    "NZDJPY": "NZDJPY", "CADJPY": "CADJPY", "CHFJPY": "CHFJPY",
    # Otros
    "NZDCAD": "NZDCAD", "NZDCHF": "NZDCHF",
    "CADCHF": "CADCHF",
}

MAGIC_COPIER = 20260325
TWELVE_KEY = os.getenv("TWELVE_DATA_KEY", "")

# === TP TRACKER ===
# _open_signals: { sig_id → {"signal": signal_dict, "sent_at": float} }
_open_signals: dict = {}
_signals_lock = threading.Lock()
_resolved_signals: set = set()  # sig_ids ya resueltos — no volver a cargar del JSON

# Archivo de señales manuales — el admin registra señales vía /rastrear en bot.py
MANUAL_SIGNALS_FILE = Path(__file__).parent / "manual_signals.json"

# === EDIT TRACKER ===
# Mensajes reenviados SIN precio de entrada (entry=0) → esperar edición del canal original
# { telegram_msg_id → signal_dict } para actualizarlos cuando el canal edite con el precio real
_pending_entry: dict = {}   # { msg_id: signal } — señales publicadas sin precio de entrada
_pending_entry_lock = threading.Lock()
_published_msg_ids: set = set()  # msg_ids ya publicados como señal completa (evita duplicar en edit)

# === BUFFER 30s para señales sin entry ===
# { msg_id → {"signal": dict, "executed": bool, "detail": str, "task": asyncio.Task|None} }
_buffered_signals: dict = {}
_buffered_lock = threading.Lock()
BUFFER_WAIT_SECONDS = 30


def _normalize_twelve_symbol(pair: str) -> str:
    """Convierte símbolo interno → formato Twelve Data (XAU/USD, EUR/USD, etc.)."""
    _twelve_map = {
        "GOLD": "XAU/USD", "XAUUSD": "XAU/USD",
        "BRENT": "BRENT", "US100Cash": "NDX", "US30Cash": "DJI", "US500Cash": "SPX",
    }
    if pair in _twelve_map:
        return _twelve_map[pair]
    # Forex pairs: EURUSD → EUR/USD (insertar "/" en posición 3)
    if len(pair) == 6 and pair.isalpha():
        return f"{pair[:3]}/{pair[3:]}"
    return pair


def _get_current_price(pair: str) -> float | None:
    """Fetch current price via Twelve Data API. Returns float or None."""
    if not TWELVE_KEY:
        return None
    symbol = _normalize_twelve_symbol(pair)
    try:
        import requests
        resp = requests.get(
            "https://api.twelvedata.com/price",
            params={"symbol": symbol, "apikey": TWELVE_KEY},
            timeout=8,
        )
        data = resp.json()
        val = float(data.get("price", 0) or 0)
        return val if val > 0 else None
    except Exception:
        return None


def _fetch_chart_image(pair: str, direction: str, entry: float, tp: float) -> bytes | None:
    """Generate professional TP chart using Twelve Data + matplotlib."""
    if not TWELVE_KEY:
        return None
    symbol = _normalize_twelve_symbol(pair)
    # Display pair bonito
    if pair in ("GOLD", "XAUUSD"):
        pair_d = "XAU/USD"
    elif len(pair) == 6 and pair.isalpha():
        pair_d = f"{pair[:3]}/{pair[3:]}"
    else:
        pair_d = pair
    try:
        import requests
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
        import numpy as np

        resp = requests.get(
            "https://api.twelvedata.com/time_series",
            params={"symbol": symbol, "interval": "15min", "outputsize": 50, "apikey": TWELVE_KEY},
            timeout=15,
        )
        data = resp.json()
        if "values" not in data:
            return None
        values = data["values"][::-1]  # oldest → newest
        opens  = [float(v["open"])  for v in values]
        closes = [float(v["close"]) for v in values]
        highs  = [float(v["high"])  for v in values]
        lows   = [float(v["low"])   for v in values]
        n = len(closes)

        # ── Colores estilo TradingView dark ──
        BG = "#131722"
        GRID = "#1e222d"
        TEXT = "#787b86"
        CANDLE_GREEN = "#26a69a"
        CANDLE_RED   = "#ef5350"
        GOLD = "#ffd700"
        ENTRY_COLOR = "#2962ff"
        WICK_GREEN = "#26a69a"
        WICK_RED   = "#ef5350"

        fig, ax = plt.subplots(figsize=(10, 5), facecolor=BG)
        ax.set_facecolor(BG)

        # ── Velas japonesas ──
        candle_width = 0.6
        wick_width = 1.2
        for i in range(n):
            o, c, h, l = opens[i], closes[i], highs[i], lows[i]
            is_bull = c >= o
            color = CANDLE_GREEN if is_bull else CANDLE_RED
            wick_color = WICK_GREEN if is_bull else WICK_RED

            # Mecha (high-low)
            ax.plot([i, i], [l, h], color=wick_color, linewidth=wick_width, solid_capstyle="round", zorder=3)

            # Cuerpo de la vela
            body_bottom = min(o, c)
            body_height = abs(c - o) or (h - l) * 0.01  # mínimo visible para doji
            rect = plt.Rectangle((i - candle_width / 2, body_bottom), candle_width, body_height,
                                 facecolor=color, edgecolor=color, linewidth=0.5, zorder=4)
            ax.add_patch(rect)

        # ── TP line — prominente ──
        ax.axhline(y=tp, color=GOLD, linestyle="-", linewidth=2, alpha=0.9, zorder=5)
        ax.text(n + 0.5, tp, f" TP {tp:.2f}", color=GOLD, fontsize=11, fontweight="bold",
                va="center", ha="left", zorder=6,
                bbox=dict(boxstyle="round,pad=0.2", facecolor=GOLD, edgecolor="none", alpha=0.15))

        # ── Entry line ──
        if entry > 0:
            ax.axhline(y=entry, color=ENTRY_COLOR, linestyle="--", linewidth=1.5, alpha=0.8, zorder=5)
            ax.text(n + 0.5, entry, f" Entry {entry:.2f}", color=ENTRY_COLOR, fontsize=10,
                    va="center", ha="left", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.2", facecolor=ENTRY_COLOR, edgecolor="none", alpha=0.15))

        # ── Zona de profit (fill entre entry y TP) ──
        if entry > 0 and tp > 0:
            y_min = min(entry, tp)
            y_max = max(entry, tp)
            ax.axhspan(y_min, y_max, alpha=0.06, color=GOLD, zorder=1)

        # Calcular pips ganados
        pips_won = abs(tp - entry) if entry > 0 else 0
        if pair in ("GOLD", "XAUUSD"):
            pips_label = f"+{pips_won:.0f} pips" if pips_won >= 1 else f"+{pips_won:.1f} pips"
        elif entry >= 100:
            pips_label = f"+{pips_won:.1f} pts"
        else:
            pips_label = f"+{pips_won * 10000:.0f} pips" if pips_won > 0 else ""

        # Título con pips
        dir_es = "COMPRA" if direction == "BUY" else "VENTA"
        title = f"✅ TP ALCANZADO — {dir_es} {pair_d}"
        if pips_label:
            title += f"  |  {pips_label}"
        ax.set_title(title, color=GOLD, fontsize=14, fontweight="bold", pad=15,
                     fontfamily="sans-serif")

        # Grid estilo TradingView
        ax.grid(True, alpha=0.08, color=TEXT, linestyle="-", linewidth=0.5)
        ax.tick_params(colors=TEXT, labelsize=9)
        ax.set_xlim(-1, n + 7)  # Espacio para labels
        for spine in ax.spines.values():
            spine.set_visible(False)

        # Watermark
        fig.text(0.98, 0.02, "BuySell365 Pro", fontsize=8, color="#2a2e39",
                 ha="right", va="bottom", fontstyle="italic")

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=150, facecolor=BG, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        log.warning(f"Chart generation error: {e}")
        return None


def _send_tp_celebration(signal: dict, reply_to_msg_id: int = None) -> None:
    """Send TP celebration to channel with chart image and rockets."""
    import requests

    direction = signal["direction"]
    pair = signal["pair"]
    entry = signal["entry"]
    tp = signal["tp"]

    # Display pair bonito
    if pair in ("GOLD", "XAUUSD"):
        pair_d = "XAU/USD"
    elif len(pair) == 6 and pair.isalpha():
        pair_d = f"{pair[:3]}/{pair[3:]}"
    else:
        pair_d = pair

    def fmt(v):
        if v <= 0: return "Mercado"
        return f"{v:.2f}" if v >= 100 else f"{v:.5f}".rstrip("0").rstrip(".")

    dir_es = "COMPRA" if direction == "BUY" else "VENTA"
    dir_emoji = "🟢" if direction == "BUY" else "🔴"

    # Calcular pips ganados
    pips_won = abs(tp - entry) if entry > 0 and tp > 0 else 0
    if pair in ("GOLD", "XAUUSD"):
        pips_str = f"+{pips_won:.0f} pips" if pips_won >= 1 else ""
    elif entry >= 100:
        pips_str = f"+{pips_won:.1f} pts" if pips_won > 0 else ""
    else:
        pips_str = f"+{pips_won * 10000:.0f} pips" if pips_won > 0 else ""

    pips_line = f"\n💰 Ganancia: *{pips_str}*" if pips_str else ""

    msg = (
        f"🎯🎯🎯 *TP ALCANZADO* 🎯🎯🎯\n"
        f"━━━━━━━━━━━━━━\n"
        f"{dir_emoji} *{dir_es} {pair_d}*\n\n"
        f"📍 Entrada: {fmt(entry)}\n"
        f"✅ TP: {fmt(tp)}"
        f"{pips_line}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🚀 _Otra victoria para el canal VIP_"
    )

    chart_bytes = _fetch_chart_image(pair, direction, entry, tp)

    try:
        if chart_bytes:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            payload = {"chat_id": CHANNEL_ID, "caption": msg, "parse_mode": "Markdown"}
            if reply_to_msg_id:
                payload["reply_to_message_id"] = reply_to_msg_id
            resp = requests.post(url, data=payload,
                files={"photo": ("chart.png", chart_bytes, "image/png")}, timeout=20)
        else:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": CHANNEL_ID, "text": msg, "parse_mode": "Markdown"}
            if reply_to_msg_id:
                payload["reply_to_message_id"] = reply_to_msg_id
            resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info(f"🎉 TP CELEBRATION enviada: {dir_es} {pair}")
        else:
            log.warning(f"Celebration send error: {resp.status_code} {resp.text[:80]}")
    except Exception as e:
        log.warning(f"Celebration send error: {e}")


def _send_sl_notification(signal: dict, reply_to_msg_id: int = None) -> None:
    """Notify channel that SL was hit."""
    import requests

    direction = signal["direction"]
    pair = signal["pair"]
    entry = signal["entry"]
    sl = signal["sl"]

    # Display pair bonito
    if pair in ("GOLD", "XAUUSD"):
        pair_d = "XAU/USD"
    elif len(pair) == 6 and pair.isalpha():
        pair_d = f"{pair[:3]}/{pair[3:]}"
    else:
        pair_d = pair

    dir_es = "COMPRA" if direction == "BUY" else "VENTA"
    dir_emoji = "🟢" if direction == "BUY" else "🔴"

    def fmt(v):
        return f"{v:.2f}" if v >= 100 else f"{v:.5f}".rstrip("0").rstrip(".")

    msg = (
        f"🛑 *STOP LOSS* — {pair_d}\n"
        f"━━━━━━━━━━━━━━\n"
        f"{dir_emoji} *{dir_es}*\n"
        f"📍 Entrada: {fmt(entry)} → SL: {fmt(sl)}\n"
        f"━━━━━━━━━━━━━━"
    )
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHANNEL_ID, "text": msg, "parse_mode": "Markdown"}
        if reply_to_msg_id:
            payload["reply_to_message_id"] = reply_to_msg_id
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        log.warning(f"SL notification error: {e}")


async def _monitor_tp_loop() -> None:
    """Async background loop — checks every 30s if any tracked signal hit TP or SL."""
    log.info("🎯 Monitor TP/SL loop iniciado (intervalo: 30s)")
    while True:
        await asyncio.sleep(30)  # 30s para no perder TP/SL en mercados volátiles

        # ── Cargar señales manuales del admin (manual_signals.json) ──
        try:
            if MANUAL_SIGNALS_FILE.exists():
                with open(MANUAL_SIGNALS_FILE, 'r', encoding='utf-8') as _f:
                    _manual = json.load(_f)
                for _ms in _manual:
                    _sid = _ms.get("sig_id", "")
                    if not _sid or _sid in _resolved_signals:
                        continue
                    with _signals_lock:
                        if _sid not in _open_signals:
                            _open_signals[_sid] = {"signal": _ms, "sent_at": _ms.get("sent_at", time.time())}
                            log.info(f"📌 Señal manual cargada: {_ms.get('direction')} {_ms.get('pair')} TP:{_ms.get('tp')} SL:{_ms.get('sl')}")
        except Exception as _e_manual:
            log.warning(f"Error leyendo manual_signals.json: {_e_manual}")

        # ── Limpieza de _pending_entry antiguos (>10 min) para evitar memory leak ──
        _now = time.time()
        with _pending_entry_lock:
            _stale_ids = [mid for mid, sig in _pending_entry.items()
                          if _now - sig.get("_buffered_at", _now) > 600]
            for mid in _stale_ids:
                _pending_entry.pop(mid, None)
            if _stale_ids:
                log.info(f"🧹 Limpieza: {len(_stale_ids)} pending_entry antiguos eliminados")

        with _signals_lock:
            signals_copy = dict(_open_signals)

        to_resolve = []
        for sig_id, sdata in signals_copy.items():
            signal = sdata["signal"]
            direction = signal["direction"]
            tp = signal["tp"]
            sl = signal["sl"]
            pair = signal["pair"]
            age_hours = (time.time() - sdata["sent_at"]) / 3600

            # Auto-expire after 48h to avoid zombie tracking
            if age_hours > 48:
                to_resolve.append((sig_id, sdata, "expired"))
                continue

            price = _get_current_price(pair)
            if price is None:
                continue

            # BUG FIX: si TP=0 (abierto/desconocido), NO verificar TP hit — price>=0 siempre True
            tp_hit = False
            if tp > 0:
                tp_hit = (direction == "BUY" and price >= tp) or (direction == "SELL" and price <= tp)
            sl_hit = (direction == "BUY" and price <= sl) or (direction == "SELL" and price >= sl)

            if tp_hit:
                to_resolve.append((sig_id, sdata, "tp"))
            elif sl_hit:
                to_resolve.append((sig_id, sdata, "sl"))

        for sig_id, sdata_resolved, result in to_resolve:
            signal   = sdata_resolved["signal"] if isinstance(sdata_resolved, dict) and "signal" in sdata_resolved else sdata_resolved
            _reply_id = signals_copy.get(sig_id, {}).get("telegram_msg_id") if isinstance(signals_copy.get(sig_id), dict) else None
            with _signals_lock:
                _open_signals.pop(sig_id, None)
            _resolved_signals.add(sig_id)  # No volver a cargar del JSON
            if result == "tp":
                log.info(f"🎯 TP ALCANZADO: {signal['direction']} {signal['pair']}")
                _send_tp_celebration(signal, reply_to_msg_id=_reply_id)
            elif result == "sl":
                log.info(f"🛡️ SL alcanzado: {signal['direction']} {signal['pair']}")
                _send_sl_notification(signal, reply_to_msg_id=_reply_id)


# === PARSER ===
def parse_signal(text, chat_title=""):
    """Parse trading signal from text. Returns dict or None.
    Soporta formatos: SureShotFX, Learn2Trade VIP.
    """
    if not text or len(text) < 10:
        return None

    # Normalizar superíndices Unicode → dígitos normales
    # GOLD FOREX MARKET usa TP¹ TP² TP³ TP⁴ TP⁵ (U+00B9, U+00B2, U+00B3, U+2074-2079)
    _superscript_map = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
    text = text.translate(_superscript_map)

    upper = text.upper().replace("\n", " ").replace("  ", " ").strip()
    # Limpiar hashtags (#XAUUSD → XAUUSD) — formato GOLD FOREX MARKET
    upper = upper.replace("#", "")
    # Versión sin slash para búsqueda de pares (AUDJPY, GBPUSD...)
    upper_noslash = upper.replace("/", "")

    # ── FILTROS DE RUIDO — ignorar completamente ──
    _ignore_keywords = [
        "SSF COPIER", "SSF TRADE COPIER", "AUTOMATIZACION", "CUPON", "SURESHOTFX.COM",
        "INVALID PARAMETERS", "INVALID ORDER", "MARKET IS TOO VOLATILE",
        "GOLD ANALYSIS", "LET'S WAIT", "HOLA MIEMBROS", "HELLO VIP",
        "SL UPDATED", "HIT OUR RISK",
        "ALGOBOT", "REVOLUTIONARY TRADING", "DAILY PICKS", "MATCHBETS",
        "BTC DOMINANCE", "ALTCOIN", "ETHEREUM", "BITCOIN",
        "WEEKLY OUTLOOK", "MARKET OUTLOOK", "RESEARCH DESK",
        "STAY SHARP", "SIGNALS WILL FOLLOW", "HEY TRADERS",
        "FAILED TO TRIGGER", "GETTING DELETED", "BECOME INVALID IF NOT TRIGGERED",
        "FED SPEAKERS", "GEOPOLITICAL", "ECONOMIC DATA",
        # GOLD FOREX MARKET — celebraciones / actualizaciones de TP
        "SMASHED", "BLAZING PROFIT", "BOOM BOOM", "POWER TRADE DONE",
        "PATIENCE PAYS", "TRADE SMART", "STAY DISCIPLINED",
        "MARKET OPENING ALERT", "VOLATILITY EXPECTED",
    ]
    if any(w in upper for w in _ignore_keywords):
        return None

    # ── MENSAJES DE ACTUALIZACIÓN ──
    _update_keywords = [
        "CLOSE HALF", "CLOSE PARTIAL", "FULL CLOSE", "MOVE SL", "PIPS PROFIT",
        "STOP LOSS HIT", "TP HIT", "HIT OUR RISK", "PIPS IN PROFIT",
        "CIERRA LA MITAD", "CIERRE PARCIAL", "MOVER SL", "CERRAR COMPLETAMENTE",
        "RUNNING WITH", "CURRENTLY RUNNING",
    ]
    if any(w in upper for w in _update_keywords):
        return _parse_update(text, upper_noslash)

    # ── DETECTAR FUENTE ──
    source = "Externa"
    chat_lower = chat_title.lower()
    text_lower = text.lower()
    if "sureshot" in chat_lower or "ssf" in text_lower:
        source = "SureShotFX"
    elif "learn" in chat_lower or "l2t" in text_lower:
        source = "Learn2Trade"
    elif "fxpremiere" in chat_lower or "fxpremiere" in text_lower or "goldsignals" in chat_lower:
        source = "FXPremiere"
    elif "gold forex" in chat_lower:
        source = "GoldForexMarket"

    # ── DETECTAR DIRECCIÓN ──
    # Learn2Trade usa ▲▲▲=BUY y ▼▼▼=SELL
    direction = None
    order_type = "Market"   # Market | Limit | Stop
    is_limit = False

    # Flechas Learn2Trade
    arrow_up   = text.count("▲") + text.count("🔺")
    arrow_down = text.count("▼") + text.count("🔻")
    if arrow_up > arrow_down:
        direction = "BUY"
    elif arrow_down > arrow_up:
        direction = "SELL"

    # "Order type: Buy Limit / Sell Stop / etc."
    ot_match = re.search(r'ORDER\s*TYPE\s*[:\s]+(BUY|SELL)\s*(LIMIT|STOP|MARKET)?', upper)
    if ot_match:
        direction = ot_match.group(1)
        ot_sub = (ot_match.group(2) or "MARKET").strip()
        if ot_sub == "LIMIT":
            order_type = "Limit"
            is_limit = True
        elif ot_sub == "STOP":
            order_type = "Stop"
        else:
            order_type = "Market"

    # Fallback: BUY/SELL en el texto (incluyendo formatos FXPremiere en español)
    if not direction:
        _buy_words  = ["BUY", "COMPRA", "LONG", "COMPRE", "COMPRA DE", "COMPRA INSTANTANEA",
                       "VENTA DE ORO AHORA" ]  # "Venta de Oro Ahora" puede ser SELL
        _sell_words = ["SELL", "VENTA", "SHORT", "VENTA INSTANTANEA", "VENTA DE ORO",
                       "LIMITE DE VENTA", "VENTA LIMITE"]  # AnabelSignals: "Límite de venta de oro"
        # Verificar VENTA antes que COMPRA para evitar falsos positivos
        if any(w in upper_noslash for w in _sell_words):
            direction = "SELL"
        elif any(w in upper_noslash for w in _buy_words):
            direction = "BUY"
    if not direction:
        return None

    # Si es LIMIT también desde otras menciones
    if "LIMIT" in upper:
        is_limit = True
        if order_type == "Market":
            order_type = "Limit"

    # ── DETECTAR PAR ──
    pair_found = None
    for alias, mt5_sym in SYMBOL_MAP.items():
        if re.search(rf'\b{alias}\b', upper_noslash):
            pair_found = (alias, mt5_sym)
            break
    if not pair_found:
        return None

    alias, mt5_symbol = pair_found

    # ── EXTRAER PRECIOS ──
    # Eliminar símbolos de moneda para parsear números
    upper_clean = re.sub(r'[$€£]', '', upper)

    # ── EXTRAER SL ──
    # Formatos: "SL: 4499.60" | "SL 4499" | "❗️ SL 45370" | "Stop Loss → 1.3801" | "SL4415" (sin espacio)
    # FIX: \d{1,6} en vez de \d{3,6} — forex como EURUSD/GBPAUD tienen precio 1.XXXXX (1 solo dígito entero)
    sl_match = re.search(r'(?:SL|STOP\s*LOSS)\s*[:\s→]*(\d{1,6}\.?\d+)', upper_clean)

    # ── EXTRAER TP1 (solo el primero) ──
    # Formatos: "TP1: 4513" | "TP: 4513" | "Tp 4540" | "🥇 TP 45530" | "Toma de Ganancias 1 : 4513"
    # | "Take profit 4480" | "Take profit : 4480" (FxPremiere format)
    # Ignora líneas con "TP: abierto" / "TP: ABIERTO" (sin número fijo)
    _upper_clean_no_abierto = re.sub(r'TP\s*[:\s]*ABIERTO', '', upper_clean)
    tp_match = re.search(
        r'(?:TOMA\s*DE\s*GANANCIAS\s*1\s*[:\s]+|TP\s*1\s*[:\s]+|TP\s*[:\s]+|TP\s+|TAKE\s*PROFIT\s*[:\s]*)(\d+\.?\d*)',
        _upper_clean_no_abierto
    )
    # Fallback: "Tp 4540" o "TP 1.9150" — \d{1,6} cubre forex (1.XXXX) y gold (4XXX)
    if not tp_match:
        tp_match = re.search(r'\bTP\s+(\d{1,6}\.?\d+)', _upper_clean_no_abierto)
    # Fallback AnabelSignals: "TP4430" o "TP1.9150" (sin espacio entre TP y número)
    if not tp_match:
        tp_match = re.search(r'\bTP(\d{1,6}\.?\d+)', _upper_clean_no_abierto)

    # ── EXTRAER ENTRADA ──
    # Formatos: "Entrada: 4509/4504" | "Entrada 4545" | "Venta de Oro Ahora: 4416 - 4419"
    # | "ENTRY 1.3741" | "Buy 1.8412"
    entry_match = re.search(
        r'(?:ENTRY\s*(?:PRICE)?|ENTRADA)\s*[:\s]+(\d+\.?\d*)',
        upper_clean
    )
    # FXPremiere: "Venta de Oro Ahora: 4416 - 4419" → tomar primer número después del ":"
    if not entry_match:
        entry_match = re.search(r'(?:AHORA|NOW)\s*[:\s]+(\d+\.?\d*)', upper_clean)
    # AnabelSignals/SureShot: "XAUUSD BUY 4458.22" → número INMEDIATAMENTE tras activo+dirección
    # IMPORTANTE: usar \s{0,5} en vez de [^\d]* para NO capturar el SL cuando no hay precio de entrada
    # Ejemplo malo: "XAUUSD BUY \n\nSL: 4450.32" → el [^\d]* antiguo capturaba 4450.32 como entrada
    if not entry_match:
        entry_match = re.search(r'(?:ORO|XAUUSD|GOLD)\s+(?:COMPRA|VENTA|BUY|SELL)\s{0,5}(\d{3,6}\.?\d*)', upper_clean)
    # AnabelSignals: "Límite de venta de oro 4442" | "Venta de oro 4467" → número tras dirección+activo
    if not entry_match:
        entry_match = re.search(r'(?:VENTA|COMPRA|LIMITE)\s+(?:DE\s+)?(?:ORO|XAUUSD|GOLD)\s{0,5}(\d{3,6}\.?\d*)', upper_clean)
    # Formato inline: "GBP/CAD H1 Buy 1.8412" → número después de BUY/SELL seguido de otro token
    if not entry_match:
        entry_match = re.search(r'(?:BUY|SELL|COMPRA|VENTA)\s+[\w/]+\s+(?:H\d+\s+)?(\d+\.?\d*)', upper_clean)
    # SureShotFX inline: "GBPAUD SELL  1.93236" — precio con decimal JUSTO tras BUY/SELL (sin keyword)
    # Requiere decimal para evitar falsos positivos con números enteros del texto
    if not entry_match:
        entry_match = re.search(r'(?:BUY|SELL|COMPRA|VENTA)\s{1,10}(\d+\.\d+)', upper_clean)
    # FxPremiere: "Gold buy now!!@4487 - 4482" — precio precedido de @
    # También cubre formatos como "@4509/4504", "ENTRY @1.3741"
    if not entry_match:
        entry_match = re.search(r'@\s*(\d{1,6}\.?\d*)', upper_clean)

    # SL es obligatorio — si no hay SL ignorar la señal (demasiado arriesgado)
    # TP es opcional — Learn2Trade y otros canales a veces publican "Take profit: OPEN"
    # o sin TP en el primer mensaje (lo añaden vía edición)
    if not sl_match:
        return None

    try:
        sl    = float(sl_match.group(1))
        tp    = float(tp_match.group(1)) if tp_match else 0.0
        entry = float(entry_match.group(1)) if entry_match else 0.0
    except (ValueError, IndexError):
        return None

    # Protección: si entry == sl o entry == tp EXACTAMENTE, es un falso positivo del parser → ignorar entrada
    # NOTA: umbral muy pequeño (0.0001) para no rechazar SL legítimos de pares forex con 5 decimales.
    # Ejemplo legítimo: EURAUD SELL 1.67645 SL:1.68130 → diff=0.00485 (48.5 pips, válido)
    # Falso positivo real: entry captura el mismo número que SL (diff ≈ 0)
    if entry > 0 and sl > 0 and abs(entry - sl) < 0.0001:
        log.warning(f"⚠️ Parser: entry={entry} == sl={sl} — descartando entrada (falso positivo exacto)")
        entry = 0.0
    if entry > 0 and tp > 0 and abs(entry - tp) < 0.0001:
        log.warning(f"⚠️ Parser: entry={entry} == tp={tp} — descartando entrada (falso positivo exacto)")
        entry = 0.0

    # TP puede ser 0 (abierto) — solo SL es obligatorio
    if sl <= 0:
        return None

    # ── Validación lógica: TP y SL deben estar en la dirección correcta ──
    if entry > 0 and tp > 0:
        if direction == "BUY" and tp < entry and abs(tp - entry) > 0.001:
            log.warning(f"⚠️ Parser: BUY pero TP({tp}) < entry({entry}) — señal invertida, descartando")
            return None
        if direction == "SELL" and tp > entry and abs(tp - entry) > 0.001:
            log.warning(f"⚠️ Parser: SELL pero TP({tp}) > entry({entry}) — señal invertida, descartando")
            return None
    if entry > 0 and sl > 0:
        if direction == "BUY" and sl > entry and abs(sl - entry) > 0.001:
            log.warning(f"⚠️ Parser: BUY pero SL({sl}) > entry({entry}) — señal invertida, descartando")
            return None
        if direction == "SELL" and sl < entry and abs(sl - entry) > 0.001:
            log.warning(f"⚠️ Parser: SELL pero SL({sl}) < entry({entry}) — señal invertida, descartando")
            return None

    # ── RRR ──
    rrr = ""
    rrr_match = re.search(r'RR+R?\s*[:\s]+(\d+:\d+)', upper)
    if rrr_match:
        rrr = rrr_match.group(1)

    # ── TIPO DE SWING/SCALP ──
    style = ""
    if "SWING" in upper:
        style = "Swing"
    elif "SCALP" in upper:
        style = "Scalp"
    elif "INTRADAY" in upper:
        style = "Intraday"

    return {
        "type":       "new_signal",
        "pair":       alias,
        "mt5_symbol": mt5_symbol,
        "direction":  direction,
        "order_type": order_type,
        "is_limit":   is_limit,
        "entry":      entry,
        "sl":         sl,
        "tp":         tp,
        "rrr":        rrr,
        "style":      style,
        "source":     source,
        "raw":        text[:300],
        "timestamp":  time.time(),
    }


def _parse_update(text, upper):
    """Parse signal updates (close half, move SL, etc.) — English + Spanish."""
    action = None

    # Close half (EN + ES)
    if any(w in upper for w in ["CLOSE HALF", "CIERRA LA MITAD", "CIERRE DE LA MITAD", "CIERRE MEDIO"]):
        action = "close_half"
    # Close partial (EN + ES)
    elif any(w in upper for w in ["CLOSE PARTIAL", "CIERRE PARCIAL"]):
        action = "close_partial"
    # Full close (EN + ES)
    elif any(w in upper for w in ["FULL CLOSE", "CERRAR COMPLETAMENTE"]):
        action = "full_close"
    # Move SL to entry (EN + ES)
    elif any(w in upper for w in ["MOVE SL TO ENTRY", "MOVER SL A LA ENTRADA", "MOVER EL SL A LA ENTRADA", "MOVIMOS EL SL A LA ENTRADA"]):
        action = "move_sl_to_entry"
    # SL/TP hit
    elif "STOP LOSS HIT" in upper:
        action = "sl_hit"
    elif "TP HIT" in upper or "TAKE PROFIT HIT" in upper:
        action = "tp_hit"
    # "EN CURSO CON GANANCIA" = info only, suggest full close
    elif "EN CURSO CON" in upper or "RUNNING" in upper:
        action = "info_running"
    else:
        return None

    # info_running = just notify, don't execute
    if action == "info_running":
        return None

    # "FULL TP HIT" = TP alcanzado
    if "FULL TP HIT" in upper:
        action = "tp_hit"

    # Find pair in update
    pair_found = None
    for alias, mt5_sym in SYMBOL_MAP.items():
        if re.search(rf'\b{alias}\b', upper):
            pair_found = (alias, mt5_sym)
            break

    if not pair_found:
        return None

    return {
        "type": "update",
        "action": action,
        "pair": pair_found[0],
        "mt5_symbol": pair_found[1],
        "raw": text[:200],
    }


# === MT5 EXECUTION ===
def execute_in_mt5(signal):
    """Execute signal in MT5. Returns (success, detail)."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return False, "MetaTrader5 not installed"

    if not mt5.initialize():
        return False, "MT5 not initialized"

    sym = signal["mt5_symbol"]
    info = mt5.symbol_info(sym)
    if not info:
        return False, f"Symbol {sym} not found"
    if not info.visible:
        mt5.symbol_select(sym, True)

    tick = mt5.symbol_info_tick(sym)
    if not tick:
        return False, f"No tick for {sym}"

    price = tick.ask if signal["direction"] == "BUY" else tick.bid
    sl = signal["sl"]
    tp = signal["tp"]
    # If entry was 0 (not in message), use current market price
    if signal["entry"] == 0 or signal["entry"] == 0.0:
        signal["entry"] = price

    # R:R check
    risk = abs(price - sl)
    reward = abs(tp - price)
    if risk <= 0:
        return False, "Invalid SL"
    rr = reward / risk
    if rr < 0.8:
        return False, f"R:R {rr:.2f} too low"

    # Lot calculation (1% risk)
    account = mt5.account_info()
    capital = account.balance if account else 1000
    risk_money = capital * 0.01

    tick_size = info.trade_tick_size
    tick_value = info.trade_tick_value
    if tick_size > 0 and tick_value > 0:
        sl_ticks = abs(price - sl) / tick_size
        lot = risk_money / (sl_ticks * tick_value) if sl_ticks > 0 else 0.01
    else:
        lot = 0.01

    lot = max(info.volume_min, min(info.volume_max, round(lot, 2)))

    order_type = mt5.ORDER_TYPE_BUY if signal["direction"] == "BUY" else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": sym,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": round(sl, info.digits),
        "tp": round(tp, info.digits),
        "deviation": 30,
        "magic": MAGIC_COPIER,
        "comment": "BuySell365 Pro",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return True, f"Executed {signal['direction']} {sym} @ {price} Lot={lot}"
    else:
        err = result.comment if result else "No response"
        return False, f"MT5 error: {err}"


def handle_update_mt5(update):
    """Handle signal updates (close half, move SL to entry, etc.)."""
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return False, "MT5 not initialized"

        sym = update["mt5_symbol"]
        positions = mt5.positions_get(symbol=sym)
        if not positions:
            return False, f"No open position for {sym}"

        # Find our copier position
        pos = None
        for p in positions:
            if p.magic == MAGIC_COPIER:
                pos = p
                break
        if not pos:
            return False, f"No copier position for {sym}"

        action = update["action"]

        if action == "close_half":
            # Close half the position
            close_vol = round(pos.volume / 2, 2)
            if close_vol < mt5.symbol_info(sym).volume_min:
                close_vol = pos.volume  # Close all if can't split

            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(sym)
            close_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": sym,
                "volume": close_vol,
                "type": close_type,
                "price": close_price,
                "position": pos.ticket,
                "deviation": 30,
                "magic": MAGIC_COPIER,
                "comment": "BuySell365 Pro",
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            return (result and result.retcode == mt5.TRADE_RETCODE_DONE), f"Close half {sym}"

        elif action == "move_sl_to_entry":
            # Move SL to entry (breakeven)
            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "symbol": sym,
                "position": pos.ticket,
                "sl": pos.price_open,
                "tp": pos.tp,
            }
            result = mt5.order_send(request)
            return (result and result.retcode == mt5.TRADE_RETCODE_DONE), f"SL moved to entry {sym}"

        elif action in ("full_close", "sl_hit", "tp_hit"):
            # Close full position
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(sym)
            close_price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": sym,
                "volume": pos.volume,
                "type": close_type,
                "price": close_price,
                "position": pos.ticket,
                "deviation": 30,
                "magic": MAGIC_COPIER,
                "comment": "BuySell365 Pro",
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            result = mt5.order_send(request)
            return (result and result.retcode == mt5.TRADE_RETCODE_DONE), f"Closed {sym}"

        return False, f"Unknown action: {action}"
    except Exception as e:
        return False, str(e)


# === IA FILTER + COMMENTARY ===
_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")

def _ia_evaluar_senal(signal):
    """IA evalúa si la señal es buena antes de ejecutar. Retorna (aprobar, comentario)."""
    if not _GEMINI_KEY:
        return True, ""
    try:
        import google.generativeai as genai
        genai.configure(api_key=_GEMINI_KEY)
        model = genai.GenerativeModel("gemini-2.0-flash")

        _dir = signal.get("direction", signal.get("action", "?"))
        _pair = signal["pair"]
        _entry = signal.get("entry", 0)
        _sl = signal.get("sl", 0)
        _tp = signal.get("tp", 0)

        # R:R calculation
        if _entry > 0 and _sl > 0 and _tp > 0:
            risk = abs(_entry - _sl)
            reward = abs(_tp - _entry)
            rr = f"{reward/risk:.1f}:1" if risk > 0 else "N/A"
        else:
            rr = "N/A"

        prompt = f"""Eres un analista de trading profesional. Evalúa esta señal en 1 línea (max 80 caracteres).

Señal: {_dir} {_pair} @ {_entry}
SL: {_sl} | TP: {_tp} | R:R: {rr}

Responde SOLO con una línea corta de análisis. Ejemplo:
- "Tendencia alcista fuerte, buen momento"
- "RSI sobrecomprado, riesgo alto"
- "Zona de soporte, buena entrada"

NO digas si aprobar o rechazar. Solo el análisis."""

        response = model.generate_content(prompt)
        comentario = response.text.strip()[:100] if response.text else ""
        return True, comentario
    except Exception as e:
        log.warning(f"IA eval error: {e}")
        return True, ""


# === TELEGRAM BOT SEND ===
async def _publish_buffered(msg_id: int) -> None:
    """Publica una señal buffered después de 30s si no llegó edición con entry."""
    try:
        await asyncio.sleep(BUFFER_WAIT_SECONDS)
    except asyncio.CancelledError:
        return  # Edit llegó antes de 30s — cancelado correctamente
    with _buffered_lock:
        buf = _buffered_signals.pop(msg_id, None)
    if buf is None:
        return  # Ya fue publicada por edit_handler (doble seguridad)
    signal = buf["signal"]
    log.info(f"⏳ Buffer expirado (msg_id={msg_id}) — publicando con 'Precio de Mercado'")
    tg_msg_id = send_to_channel(signal, buf["executed"], buf["detail"])
    _published_msg_ids.add(msg_id)
    # Registrar como pending_entry con el telegram_msg_id para poder EDITAR si llega el precio real
    if signal.get("entry", 0) == 0:
        sig_copy = signal.copy()
        sig_copy["_tg_msg_id"] = tg_msg_id  # ID del mensaje en nuestro canal (para editarlo)
        sig_copy["_buffered_at"] = time.time()
        with _pending_entry_lock:
            _pending_entry[msg_id] = sig_copy


def send_to_channel(signal, executed, detail):
    """Envía señales al canal BuySell365 en formato español profesional."""
    import requests

    if signal["type"] == "update":
        # Notificar actualizaciones importantes al canal VIP
        _action = signal.get("action", "")
        _pair = signal.get("pair", "")
        # Display pair con / para forex (XAUUSD → XAU/USD)
        if _pair in ("GOLD", "XAUUSD"):
            _pair_d = "XAU/USD"
        elif len(_pair) == 6 and _pair.isalpha():
            _pair_d = f"{_pair[:3]}/{_pair[3:]}"
        else:
            _pair_d = _pair
        _action_labels = {
            "close_half":       f"⚡ *CERRAR MITAD* — {_pair_d}",
            "close_partial":    f"⚡ *CIERRE PARCIAL* — {_pair_d}",
            "full_close":       f"🔒 *CERRAR COMPLETAMENTE* — {_pair_d}",
            "move_sl_to_entry": f"🛡️ *MOVER SL A ENTRADA* — {_pair_d}",
            "sl_hit":           f"🛑 *SL ALCANZADO* — {_pair_d}",
            "tp_hit":           f"✅ *TP ALCANZADO* — {_pair_d}",
        }
        _msg = _action_labels.get(_action)
        if _msg:
            # Buscar la señal original del mismo par para hacer reply (referencia visual)
            _reply_id = None
            with _signals_lock:
                for _sid, _sdata in _open_signals.items():
                    _s = _sdata.get("signal", {})
                    if _s.get("pair") == _pair or _s.get("mt5_symbol") == _pair:
                        _reply_id = _sdata.get("telegram_msg_id")
                        break
            try:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                _payload = {"chat_id": CHANNEL_ID, "text": _msg, "parse_mode": "Markdown"}
                if _reply_id:
                    _payload["reply_to_message_id"] = _reply_id
                _resp_upd = requests.post(url, json=_payload, timeout=10)
                log.info(f"📢 Update notificado al canal: {_action} {_pair_d} (reply_to={_reply_id})")
                if _resp_upd.status_code == 200:
                    return _resp_upd.json().get("result", {}).get("message_id")
            except Exception as _e:
                log.warning(f"Error enviando update al canal: {_e}")
        return None

    direction  = signal["direction"]
    pair       = signal["pair"]
    entry      = signal["entry"]
    sl         = signal["sl"]
    tp         = signal["tp"]
    source     = signal.get("source", "Externa")
    order_type = signal.get("order_type", "Market")
    is_limit   = signal.get("is_limit", False)
    rrr        = signal.get("rrr", "")
    style      = signal.get("style", "")

    dir_es    = "COMPRA" if direction == "BUY" else "VENTA"
    dir_emoji = "🟢" if direction == "BUY" else "🔴"
    src_emoji = {
        "SureShotFX":      "📡",
        "Learn2Trade":     "📊",
        "FXPremiere":      "🔔",
        "GoldForexMarket": "🥇",
    }.get(source, "🔔")

    # Nombre para mostrar del par — mapeo completo
    _display_special = {
        "GOLD": "XAU/USD", "US100Cash": "NASDAQ", "US500Cash": "S&P 500",
        "US30Cash": "DOW 30", "GER40Cash": "DAX 40", "BRENT": "BRENT",
    }
    if pair in _display_special:
        pair_display = _display_special[pair]
    elif len(pair) == 6 and pair.isalpha():
        pair_display = f"{pair[:3]}/{pair[3:]}"  # EURUSD → EUR/USD
    else:
        pair_display = pair

    # Tipo de orden en español
    tipo_es = {"Market": "A Mercado", "Limit": "Orden Límite", "Stop": "Orden Stop"}.get(order_type, order_type)

    # Formato de precios
    def fmt(v):
        if v <= 0: return "—"
        return f"{v:.5f}".rstrip('0').rstrip('.') if v < 100 else f"{v:.2f}"

    entry_display = fmt(entry) if entry > 0 else "Precio de Mercado"

    tp_display = fmt(tp) if tp > 0 else "Abierto"

    lines = [
        f"{dir_emoji} *{dir_es} {pair_display}*",
        f"",
        f"📍 Entrada: {entry_display}",
        f"🎯 TP: {tp_display}",
        f"🛡️ SL: {fmt(sl)}",
    ]

    # Añadir comentario IA si está disponible (evaluado antes de llamar a esta función)
    ia_comment = signal.get("ia_comment", "")
    if ia_comment:
        lines.append(f"")
        lines.append(f"🤖 _{ia_comment}_")

    msg = "\n".join(lines)

    # Botones de afiliado XM — aparecen debajo de cada señal nueva
    _xm_buttons = {
        "inline_keyboard": [
            [
                {"text": "🎁 Abrir Cuenta XM — Bono 100%", "url": "https://clicks.pipaffiliates.com/c?c=1198043&l=es&p=1"},
                {"text": "🤖 Copy Trading (ya tengo cuenta)", "url": "https://social.tp-redirect.com/s/WRE0V7jm"},
            ]
        ]
    }

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    _payload = {
        "chat_id": CHANNEL_ID,
        "text": msg,
        "parse_mode": "Markdown",
        "reply_markup": _xm_buttons,
    }

    # Retry hasta 3 veces con backoff
    for _intento in range(3):
        try:
            resp = requests.post(url, json=_payload, timeout=10)
            if resp.status_code == 200:
                log.info(f"📡 ENVIADO AL CANAL: {dir_es} {pair_display} ({source})")
                _canal_msg_id = resp.json().get("result", {}).get("message_id")
                # Registrar señal para seguimiento TP/SL
                if signal["sl"] > 0:
                    sig_id = f"{pair}_{int(time.time())}"
                    with _signals_lock:
                        _open_signals[sig_id] = {
                            "signal": signal,
                            "sent_at": time.time(),
                            "telegram_msg_id": _canal_msg_id,
                        }
                    log.info(f"🎯 Señal registrada para seguimiento: {sig_id} (msg_id={_canal_msg_id})")
                return _canal_msg_id
            elif resp.status_code == 429:
                _retry_after = resp.json().get("parameters", {}).get("retry_after", 3)
                log.warning(f"📡 Rate-limited, esperando {_retry_after}s...")
                time.sleep(_retry_after)
            elif resp.status_code == 400 and "parse" in resp.text.lower():
                log.warning(f"📡 Markdown inválido, reintentando sin formato...")
                _payload.pop("parse_mode", None)
            else:
                log.warning(f"📡 Error canal (intento {_intento+1}/3): {resp.status_code} {resp.text[:100]}")
                if _intento < 2:
                    time.sleep(1)
        except Exception as e:
            log.warning(f"📡 Error enviando (intento {_intento+1}/3): {e}")
            if _intento < 2:
                time.sleep(1)
    log.error(f"📡 FALLO TOTAL: no se pudo enviar señal {dir_es} {pair_display} tras 3 intentos")
    return None


# === MAIN USERBOT ===
async def main():
    from telethon import TelegramClient, events

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)

    # Canales VIP monitoreados — IDs verificados con Telethon
    ALLOWED_CHANNEL_IDS = {
        -1001422000261,   # Sureshot FX VIP
        -1001661400724,   # SureShot GOLD (VIP)
        -1001700795303,   # Sureshot INDICES (VIP)
        -1001389726384,   # Learn 2 Trade VIP (verificado 2026-03-28)
    }

    # Canales públicos por username (se resuelven al arrancar)
    # TODAS las señales pasan — sin filtro por activo
    PUBLIC_CHANNELS_USERNAMES = [
        "forexsignalstrialgroup_00",  # FXPremiere Free Trial — Forex + Gold + Crypto
        "Anabelsignals08",            # AnabelSignals — XAUUSD/Gold
        "Jerry77446",                 # GOLD FOREX MARKET — XAUUSD/Gold señales VIP
    ]
    # Sin filtros — todas las señales de todos los activos se reenvían al canal VIP
    CHANNEL_ASSET_FILTER = {}  # Vacío = sin restricción por activo

    # Resolver usernames → se hace DESPUÉS de client.start() (ver más abajo)
    _username_to_id = {}

    # Auto-discover: buscar canales por keywords conocidos
    L2T_KEYWORDS = ["learn 2 trade", "learn2trade", "l2t"]
    # Keywords para auto-descubrir canales de señales
    AUTO_DISCOVER_KEYWORDS = [
        "sureshot", "learn2trade", "learn 2 trade",
        "fxpremiere", "fx premiere", "goldsignals", "gold signals",
        "anabelsignals", "anabel signals", "forex signals", "forexsignals",
        "nasdaq vip", "vip signals", "signal vip",
        "gold forex market", "gold forex",  # GOLD FOREX MARKET (@Jerry77446)
    ]
    SIGNAL_KEYWORDS = ["sureshot", "learn", "fxpremiere", "anabel", "gold forex"]

    @client.on(events.NewMessage(chats=list(ALLOWED_CHANNEL_IDS)))
    async def handler(event):
        """Process every new message from allowed signal channels."""
        try:
            text = event.raw_text
            if not text or len(text) < 10:
                return

            # Get chat info
            chat = await event.get_chat()
            chat_title = getattr(chat, 'title', 'Unknown')
            chat_id = event.chat_id

            log.info(f"📡 Mensaje recibido de [{chat_title}]: {text[:80]}")

            # Double check channel ID (positive or negative)
            if chat_id not in ALLOWED_CHANNEL_IDS and -chat_id not in ALLOWED_CHANNEL_IDS:
                return

            # Detectar username del canal para filtros
            _chan_uname = _username_to_id.get(chat_id, _username_to_id.get(-chat_id, ""))

            # Parse the signal
            signal = parse_signal(text, chat_title=chat_title)

            # Aplicar filtro de activo por canal (ej: forexsignalstrialgroup_00 solo XAUUSD)
            if signal and _chan_uname in CHANNEL_ASSET_FILTER:
                _allowed_assets = CHANNEL_ASSET_FILTER[_chan_uname]
                if signal.get("pair") not in _allowed_assets and signal.get("mt5_symbol") not in _allowed_assets:
                    log.info(f"⏭️ Señal ignorada ({signal['pair']}) — canal {_chan_uname} solo acepta {_allowed_assets}")
                    return
            if not signal:
                return

            log.info(f"📡 SEÑAL DETECTADA en [{chat.title}]: {signal.get('direction', signal.get('action', '?'))} {signal['pair']}")

            # ── Deduplicación: evitar misma señal del MISMO canal 2 veces en <5 min ──
            # Usa pair+direction+source para no bloquear señales legítimas de canales diferentes
            if signal["type"] == "new_signal":
                _source = signal.get("source", chat_title)
                _dedup_key = f"{signal['pair']}_{signal['direction']}_{_source}"
                with _signals_lock:
                    for _sid, _sdata in _open_signals.items():
                        _s = _sdata.get("signal", {})
                        _existing_key = f"{_s.get('pair','')}_{_s.get('direction','')}_{_s.get('source','')}"
                        if _existing_key == _dedup_key and (time.time() - _sdata.get("sent_at", 0)) < 300:
                            log.info(f"⏭️ Señal duplicada ignorada: {_dedup_key} (ya existe en últimos 5 min)")
                            return

            # ══════════════════════════════════════════════════════════════
            # 🔒 KILL-SWITCH GLOBAL — MT5 EXECUTION COMPLETAMENTE DESACTIVADO
            # Para reactivar cuando el usuario autorice:
            #   1. Cambiar MT5_EXECUTION_ENABLED = True aquí abajo
            #   2. Y asegurarse de que AUTO_TRADING=True en el .env del bot
            # ══════════════════════════════════════════════════════════════
            MT5_EXECUTION_ENABLED = False  # ← NUNCA cambiar sin autorización explícita del usuario

            if signal["type"] == "new_signal":
                executed, detail = False, "Ejecución MT5 desactivada (kill-switch activo)"
                if MT5_EXECUTION_ENABLED:
                    aprobar, ia_comment = _ia_evaluar_senal(signal)
                    signal["ia_comment"] = ia_comment
                    if ia_comment:
                        log.info(f"🤖 IA: {ia_comment}")
                    executed, detail = execute_in_mt5(signal)
                    log.info(f"📡 MT5: {'✅' if executed else '❌'} {detail}")

                # Registrar msg_id para manejar ediciones futuras
                msg_id = event.message.id

                if signal.get("entry", 0) == 0 and msg_id:
                    # ── BUFFER 30s: NO publicar aún, esperar edición con precio real ──
                    _buf_task = asyncio.create_task(_publish_buffered(msg_id))
                    with _buffered_lock:
                        _buffered_signals[msg_id] = {
                            "signal": signal.copy(),
                            "executed": executed,
                            "detail": detail,
                            "task": _buf_task,
                        }
                    log.info(f"⏳ Señal sin entrada en buffer (msg_id={msg_id}) — esperando 30s por edición con precio")
                else:
                    # Señal con entry → publicar inmediatamente
                    send_to_channel(signal, executed, detail)
                    if msg_id:
                        _published_msg_ids.add(msg_id)

            elif signal["type"] == "update":
                # ── Updates de posiciones (cerrar, mover SL) ──
                # Solo ejecutar si el kill-switch está activo
                if MT5_EXECUTION_ENABLED:
                    executed, detail = handle_update_mt5(signal)
                    log.info(f"📡 UPDATE MT5: {'✅' if executed else '❌'} {detail}")
                else:
                    executed, detail = False, "Update MT5 omitido (kill-switch activo)"
                    log.info(f"📡 UPDATE ignorado (kill-switch): {signal.get('action','?')} {signal['pair']}")
                send_to_channel(signal, executed, detail)

        except Exception as e:
            log.error(f"Error processing message: {e}")

    # ══════════════════════════════════════════════════════════════
    # Handler de mensajes EDITADOS — captura cuando el canal aliado
    # edita el mensaje para agregar el precio de entrada real
    # Ejemplo: SureShot envía "XAUUSD BUY" sin precio, luego edita
    # para agregar "XAUUSD BUY 4458.22". Este handler lo captura.
    # ══════════════════════════════════════════════════════════════
    @client.on(events.MessageEdited(chats=list(ALLOWED_CHANNEL_IDS)))
    async def edit_handler(event):
        """Captura ediciones de señales aliadas.
        Caso A: msg fue enviado sin precio de entrada → publicar "Entrada confirmada"
        Caso B: msg original no parseó (sin SL/TP) y la edición añadió todo → publicar como nueva señal
        """
        try:
            msg_id = event.message.id
            text = event.raw_text
            if not text or len(text) < 10:
                return

            chat = await event.get_chat()
            chat_title = getattr(chat, 'title', 'Unknown')

            # Log SIEMPRE para trazabilidad — facilita debug cuando parse falla silenciosamente
            log.info(f"✏️ EDIT recibido de [{chat_title}] msg_id={msg_id}: {text[:70].replace(chr(10), ' ')}")

            import requests

            def fmt(v):
                if v <= 0: return "—"
                return f"{v:.5f}".rstrip('0').rstrip('.') if v < 100 else f"{v:.2f}"

            # ── CASO A-BUFFER: señal aún en buffer de 30s (no publicada) ──────
            with _buffered_lock:
                _is_buffered = msg_id in _buffered_signals

            if _is_buffered:
                signal = parse_signal(text, chat_title=chat_title)
                if not signal or signal.get("entry", 0) <= 0:
                    return  # La edición no añadió precio de entrada — ignorar

                # Extraer del buffer, CANCELAR el timer, y publicar 1 solo mensaje completo
                with _buffered_lock:
                    buf = _buffered_signals.pop(msg_id, None)
                if buf:
                    # Cancelar el task de 30s para que no publique duplicado
                    _task = buf.get("task")
                    if _task and not _task.done():
                        _task.cancel()
                    # Usar la señal actualizada con entry real
                    log.info(f"✏️ Edición recibida dentro de 30s (msg_id={msg_id}) — publicando señal completa con entry={signal.get('entry')}")
                    send_to_channel(signal, buf["executed"], buf["detail"])
                    _published_msg_ids.add(msg_id)
                return

            # ── CASO A: señal ya publicada con "Precio de Mercado" → EDITAR mensaje existente ──
            with _pending_entry_lock:
                _pending_sig = _pending_entry.get(msg_id)

            if _pending_sig:
                signal = parse_signal(text, chat_title=chat_title)
                if not signal or signal.get("entry", 0) <= 0:
                    return  # La edición no añadió precio de entrada — ignorar

                pair = signal.get("pair", "")
                direction = signal.get("direction", "")
                entry = signal.get("entry", 0)
                dir_emoji = "🟢" if direction == "BUY" else "🔴"
                dir_es = "COMPRA" if direction == "BUY" else "VENTA"

                # Nombre display del par
                _display_special = {
                    "GOLD": "XAU/USD", "US100Cash": "NASDAQ", "US500Cash": "S&P 500",
                    "US30Cash": "DOW 30", "GER40Cash": "DAX 40", "BRENT": "BRENT",
                }
                if pair in _display_special:
                    pair_display = _display_special[pair]
                elif len(pair) == 6 and pair.isalpha():
                    pair_display = f"{pair[:3]}/{pair[3:]}"
                else:
                    pair_display = pair

                # Reconstruir el mensaje completo (mismo formato que send_to_channel)
                tp_display = fmt(signal.get('tp', 0)) if signal.get('tp', 0) > 0 else "Abierto"
                msg = (
                    f"{dir_emoji} *{dir_es} {pair_display}*\n\n"
                    f"📍 Entrada: {fmt(entry)}\n"
                    f"🎯 TP: {tp_display}\n"
                    f"🛡️ SL: {fmt(signal.get('sl', 0))}"
                )

                # EDITAR el mensaje existente en nuestro canal (no enviar uno nuevo)
                _tg_msg_id = _pending_sig.get("_tg_msg_id")
                if _tg_msg_id:
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
                    resp = requests.post(url, json={
                        "chat_id": CHANNEL_ID,
                        "message_id": _tg_msg_id,
                        "text": msg,
                        "parse_mode": "Markdown",
                    }, timeout=10)
                    if resp.status_code == 200:
                        log.info(f"✏️ Mensaje EDITADO con entrada real: {dir_es} {pair_display} @ {entry} (tg_msg={_tg_msg_id})")
                    else:
                        log.warning(f"✏️ Error editando mensaje (tg_msg={_tg_msg_id}): {resp.status_code} — enviando nuevo")
                        # Fallback: enviar mensaje nuevo si editar falla
                        url2 = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                        requests.post(url2, json={"chat_id": CHANNEL_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
                else:
                    # Sin _tg_msg_id (no debería pasar, pero fallback seguro)
                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    requests.post(url, json={"chat_id": CHANNEL_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
                    log.info(f"✏️ Entrada confirmada (nuevo msg): {dir_es} {pair_display} @ {entry}")

                _published_msg_ids.add(msg_id)
                # Actualizar entry en _open_signals para que el monitor TP/SL use el precio real
                with _signals_lock:
                    for _sid, _sdata in _open_signals.items():
                        _sig = _sdata.get("signal", {})
                        if _sig.get("pair") == pair and _sig.get("direction") == direction and _sig.get("entry", 0) == 0:
                            _sig["entry"] = entry
                            _sig["sl"] = signal.get("sl", _sig.get("sl", 0))
                            _sig["tp"] = signal.get("tp", _sig.get("tp", 0))
                            log.info(f"✏️ _open_signals actualizado: {_sid} entry={entry}")
                            break
                with _pending_entry_lock:
                    _pending_entry.pop(msg_id, None)
                return

            # ── CASO B: mensaje NO estaba en _pending_entry ni en _published ──
            # El mensaje original no se pudo parsear (ej: SureShotFX envió sin SL/TP)
            # y la edición añadió la información completa → publicar como señal nueva
            if msg_id in _published_msg_ids:
                return  # Ya publicado, ignorar ediciones cosméticas

            signal = parse_signal(text, chat_title=chat_title)
            if not signal or signal.get("type") != "new_signal":
                return  # No es señal nueva completa — ignorar

            log.info(f"✏️ Señal capturada vía edición (msg_id={msg_id}): {signal.get('direction')} {signal.get('pair')}")
            MT5_EXECUTION_ENABLED = False
            executed, detail = False, "Ejecución MT5 desactivada (kill-switch activo)"
            send_to_channel(signal, executed, detail)
            _published_msg_ids.add(msg_id)

        except Exception as e:
            log.error(f"Error en edit_handler: {e}")

    log.info("📡 Signal Copier iniciando...")
    log.info(f"📡 API ID: {API_ID}")
    log.info(f"📡 Phone: {PHONE}")

    await client.start(phone=PHONE)

    me = await client.get_me()
    log.info(f"📡 Conectado como: {me.first_name} (@{me.username})")

    # Resolver usernames de canales públicos → IDs numéricos
    for _uname in PUBLIC_CHANNELS_USERNAMES:
        try:
            _entity = await client.get_entity(_uname)
            _cid = _entity.id
            _cid_neg = int(f"-100{_cid}") if _cid > 0 else _cid
            ALLOWED_CHANNEL_IDS.add(_cid_neg)
            _username_to_id[_cid_neg] = _uname
            log.info(f"✅ Canal público registrado: @{_uname} → {_cid_neg}")
        except Exception as _e:
            log.warning(f"⚠️ No se pudo resolver @{_uname}: {_e}")

    # Auto-discover TODOS los canales de señales conocidos
    async for dialog in client.iter_dialogs():
        title_lower = (dialog.title or "").lower()
        # Auto-agregar canales que coincidan con keywords de señales
        _es_canal = hasattr(dialog.entity, 'broadcast') or hasattr(dialog.entity, 'megagroup')
        _match = any(kw in title_lower for kw in AUTO_DISCOVER_KEYWORDS)
        # BUG FIX: Telethon iter_dialogs devuelve IDs positivos para canales,
        # pero event.chat_id es negativo (-100XXXXXXXXXX). Normalizar al formato negativo.
        _raw_id = dialog.id
        _norm_id = int(f"-100{_raw_id}") if _raw_id > 0 else _raw_id
        _already = _norm_id in ALLOWED_CHANNEL_IDS or _raw_id in ALLOWED_CHANNEL_IDS
        if _match and _es_canal and not _already:
            ALLOWED_CHANNEL_IDS.add(_norm_id)
            log.info(f"📡 AUTO-AGREGADO: {dialog.title} (ID: {_norm_id})")
        if _norm_id in ALLOWED_CHANNEL_IDS or _raw_id in ALLOWED_CHANNEL_IDS:
            log.info(f"📡 Monitoreando: {dialog.title} (ID: {_norm_id})")

    # Update event handlers with ALL channels (incluyendo auto-descubiertos)
    # FIX: re-registrar AMBOS handlers — antes solo se actualizaba NewMessage, no MessageEdited
    client.remove_event_handler(handler)
    client.add_event_handler(handler, events.NewMessage(chats=list(ALLOWED_CHANNEL_IDS)))
    client.remove_event_handler(edit_handler)
    client.add_event_handler(edit_handler, events.MessageEdited(chats=list(ALLOWED_CHANNEL_IDS)))

    # Iniciar monitor TP/SL en background
    asyncio.ensure_future(_monitor_tp_loop())

    log.info("📡 Signal Copier ACTIVO — escuchando todos los canales VIP...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    import sys, time as _time_lock
    _lock_file = Path(__file__).parent / ".copier.lock"
    _my_pid = os.getpid()

    # ── Verificación robusta: buscar TODOS los procesos signal_copier.py corriendo ──
    try:
        import psutil as _psutil_lock
        _otros = [
            p.pid for p in _psutil_lock.process_iter(['pid', 'cmdline'])
            if p.pid != _my_pid
            and 'signal_copier' in ' '.join(p.info.get('cmdline') or [])
        ]
        if _otros:
            log.warning(f"📡 Otra instancia del copier corriendo (PIDs={_otros}). Saliendo.")
            sys.exit(0)
    except ImportError:
        # Fallback: usar lock file
        if _lock_file.exists():
            try:
                old_pid = int(_lock_file.read_text().strip())
                import psutil as _ps2
                if _ps2.pid_exists(old_pid) and old_pid != _my_pid:
                    log.warning(f"📡 Otra instancia (PID={old_pid}). Saliendo.")
                    sys.exit(0)
            except Exception:
                pass

    _lock_file.write_text(str(_my_pid))
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("📡 Signal Copier detenido por usuario")
    except Exception as e:
        log.error(f"📡 Signal Copier error fatal: {e}")
    finally:
        try:
            _lock_file.unlink(missing_ok=True)
        except Exception:
            pass

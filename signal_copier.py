"""
BuySell365 Signal Copier — Userbot que escucha canales VIP de Telegram
Lee señales de SureShotFX, Learn2Trade, etc. y las ejecuta en MT5 + reenvía al canal BuySell365.
Usa Telethon (cuenta personal de Telegram).
"""
import os, re, asyncio, logging, time, json
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

# === SYMBOL MAP ===
SYMBOL_MAP = {
    "XAUUSD": "GOLD", "GOLD": "GOLD", "ORO": "GOLD",
    "NAS100": "US100Cash", "NASDAQ": "US100Cash", "US100": "US100Cash", "NASDAQ100": "US100Cash", "NQ": "US100Cash",
    "US30": "US30Cash", "DOW": "US30Cash", "DJ30": "US30Cash",
    "SPX500": "US500Cash", "SP500": "US500Cash", "US500": "US500Cash",
    "AUDJPY": "AUDJPY", "NZDJPY": "NZDJPY", "CADJPY": "CADJPY",
    "EURJPY": "EURJPY", "CHFJPY": "CHFJPY",
    "GBPUSD": "GBPUSD", "GBPJPY": "GBPJPY", "GBPAUD": "GBPAUD", "GBPNZD": "GBPNZD",
    "AUDUSD": "AUDUSD", "NZDUSD": "NZDUSD", "USDCAD": "USDCAD", "USDCHF": "USDCHF",
    "BRENT": "BRENT", "UKOIL": "BRENT", "OIL": "BRENT",
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "AUDUSD": "AUDUSD",
    "NZDUSD": "NZDUSD", "USDCAD": "USDCAD", "USDCHF": "USDCHF",
    "USDJPY": "USDJPY", "EURJPY": "EURJPY", "GBPJPY": "GBPJPY",
    "AUDJPY": "AUDJPY", "AUDCAD": "AUDCAD", "EURCHF": "EURCHF",
    "EURGBP": "EURGBP", "EURAUD": "EURAUD", "GBPAUD": "GBPAUD",
    "NZDJPY": "NZDJPY", "CADJPY": "CADJPY", "CHFJPY": "CHFJPY",
    "AUDNZD": "AUDNZD", "GBPNZD": "GBPNZD",
    "GER40": "GER40Cash", "DAX": "GER40Cash", "DE40": "GER40Cash",
}

MAGIC_COPIER = 20260325

# === PARSER ===
def parse_signal(text, chat_title=""):
    """Parse trading signal from text. Returns dict or None.
    Soporta formatos: SureShotFX, Learn2Trade VIP.
    """
    if not text or len(text) < 10:
        return None

    upper = text.upper().replace("\n", " ").replace("  ", " ").strip()
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
    elif "fxpremiere" in chat_lower or "fxpremiere" in text_lower or "goldSignals" in chat_title:
        source = "FXPremiere"

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
        _sell_words = ["SELL", "VENTA", "SHORT", "VENTA INSTANTANEA", "VENTA DE ORO"]
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
    # Formatos: "SL: 4499.60" | "SL 4499" | "❗️ SL 45370" | "Stop Loss → 1.3801"
    sl_match = re.search(r'(?:SL|STOP\s*LOSS)\s*[:\s→]+(\d+\.?\d*)', upper_clean)

    # ── EXTRAER TP1 (solo el primero) ──
    # Formatos: "TP1: 4513" | "TP: 4513" | "Tp 4540" | "🥇 TP 45530" | "Toma de Ganancias 1 : 4513"
    tp_match = re.search(
        r'(?:TOMA\s*DE\s*GANANCIAS\s*1\s*[:\s]+|TP\s*1\s*[:\s]+|TP\s*[:\s]+|TP\s+)(\d+\.?\d*)',
        upper_clean
    )
    # Fallback: "Tp 4540" (capital T lowercase p)
    if not tp_match:
        tp_match = re.search(r'\bTP\s+(\d{3,6}\.?\d*)', upper_clean)

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
    # Formato inline: "GBP/CAD H1 Buy 1.8412" → número después de BUY/SELL
    if not entry_match:
        entry_match = re.search(r'(?:BUY|SELL|COMPRA|VENTA)\s+[\w/]+\s+(?:H\d+\s+)?(\d+\.?\d*)', upper_clean)

    # SL es obligatorio — si no hay SL ignorar la señal (demasiado arriesgado)
    if not sl_match or not tp_match:
        return None

    try:
        sl    = float(sl_match.group(1))
        tp    = float(tp_match.group(1))
        entry = float(entry_match.group(1)) if entry_match else 0.0
    except (ValueError, IndexError):
        return None

    if sl <= 0 or tp <= 0:
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
def send_to_channel(signal, executed, detail):
    """Envía señales al canal BuySell365 en formato español profesional."""
    import requests

    if signal["type"] == "update":
        return  # Solo señales de apertura

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
    src_emoji = "📡" if source == "SureShotFX" else "🔔"

    # Nombre para mostrar del par
    pair_display = pair.replace("USDJPY","USD/JPY").replace("AUDJPY","AUD/JPY") \
                       .replace("GBPJPY","GBP/JPY").replace("EURUSD","EUR/USD") \
                       .replace("GBPUSD","GBP/USD").replace("NZDUSD","NZD/USD") \
                       .replace("USDCAD","USD/CAD").replace("USDCHF","USD/CHF") \
                       .replace("EURJPY","EUR/JPY").replace("CADJPY","CAD/JPY") \
                       .replace("GBPAUD","GBP/AUD").replace("GBPNZD","GBP/NZD") \
                       .replace("AUDUSD","AUD/USD").replace("US100Cash","NASDAQ") \
                       .replace("US500Cash","S&P 500").replace("US30Cash","DOW 30")

    # Tipo de orden en español
    tipo_es = {"Market": "A Mercado", "Limit": "Orden Límite", "Stop": "Orden Stop"}.get(order_type, order_type)

    # Formato de precios
    def fmt(v):
        if v <= 0: return "—"
        return f"{v:.5f}".rstrip('0').rstrip('.') if v < 100 else f"{v:.2f}"

    entry_display = fmt(entry) if entry > 0 else "Precio de Mercado"

    lines = [
        f"{dir_emoji} *{dir_es} — {pair_display}*",
        f"",
        f"📍 Entrada: `{entry_display}`",
        f"🎯 TP: `{fmt(tp)}`",
        f"🛡️ SL: `{fmt(sl)}`",
        f"",
        f"_BuySell365.pro_",
    ]

    msg = "\n".join(lines)

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": CHANNEL_ID,
            "text": msg,
            "parse_mode": "Markdown"
        }, timeout=10)
        if resp.status_code == 200:
            log.info(f"📡 ENVIADO AL CANAL: {dir_es} {pair_display} ({source})")
        else:
            log.warning(f"📡 Error canal: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        log.warning(f"Error sending to channel: {e}")


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
    PUBLIC_CHANNELS_USERNAMES = [
        "forexsignalstrialgroup_00",  # FXPremiere Free Trial — Gold + Forex
    ]
    # Filtro por activo: si el canal está en esta lista, solo se aceptan esas señales
    CHANNEL_ASSET_FILTER = {
        "forexsignalstrialgroup_00": ["XAUUSD", "GOLD"],  # Solo oro
    }

    # Resolver usernames → IDs numéricos y agregarlos al set
    _username_to_id = {}
    for _uname in PUBLIC_CHANNELS_USERNAMES:
        try:
            _entity = await client.get_entity(_uname)
            _cid = _entity.id
            # Telethon usa IDs negativos para canales: -100XXXXXXXXXX
            _cid_neg = int(f"-100{_cid}") if _cid > 0 else _cid
            ALLOWED_CHANNEL_IDS.add(_cid_neg)
            _username_to_id[_cid_neg] = _uname
            log.info(f"✅ Canal público registrado: @{_uname} → {_cid_neg}")
        except Exception as _e:
            log.warning(f"⚠️ No se pudo resolver @{_uname}: {_e}")

    # Auto-discover: buscar canales Learn2Trade VIP y agregarlos
    L2T_KEYWORDS = ["learn 2 trade", "learn2trade", "l2t"]
    SIGNAL_KEYWORDS = ["sureshot", "learn"]  # Para el log de monitoreo

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

            if signal["type"] == "new_signal":
                # IA filter + commentary
                aprobar, ia_comment = _ia_evaluar_senal(signal)
                signal["ia_comment"] = ia_comment
                if ia_comment:
                    log.info(f"🤖 IA: {ia_comment}")

                # Execute in MT5
                executed, detail = execute_in_mt5(signal)
                log.info(f"📡 MT5: {'✅' if executed else '❌'} {detail}")

                # Send to BuySell365 channel
                send_to_channel(signal, executed, detail)

            elif signal["type"] == "update":
                # Handle updates (close half, move SL, etc.)
                executed, detail = handle_update_mt5(signal)
                log.info(f"📡 UPDATE: {'✅' if executed else '❌'} {detail}")
                send_to_channel(signal, executed, detail)

        except Exception as e:
            log.error(f"Error processing message: {e}")

    log.info("📡 Signal Copier iniciando...")
    log.info(f"📡 API ID: {API_ID}")
    log.info(f"📡 Phone: {PHONE}")

    await client.start(phone=PHONE)

    me = await client.get_me()
    log.info(f"📡 Conectado como: {me.first_name} (@{me.username})")

    # Auto-discover Learn2Trade VIP channels + list monitored channels
    async for dialog in client.iter_dialogs():
        title_lower = (dialog.title or "").lower()
        # Auto-add Learn2Trade VIP channels
        if any(kw in title_lower for kw in L2T_KEYWORDS) and "vip" in title_lower:
            if dialog.id not in ALLOWED_CHANNEL_IDS:
                ALLOWED_CHANNEL_IDS.add(dialog.id)
                log.info(f"📡 AUTO-AGREGADO: {dialog.title} (ID: {dialog.id})")
        if dialog.id in ALLOWED_CHANNEL_IDS:
            log.info(f"📡 Monitoreando: {dialog.title} (ID: {dialog.id})")

    # Update event handler with new channels
    client.remove_event_handler(handler)
    client.add_event_handler(handler, events.NewMessage(chats=list(ALLOWED_CHANNEL_IDS)))

    log.info("📡 Signal Copier ACTIVO — escuchando todos los canales VIP...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    # Lock file para evitar múltiples instancias
    _lock_file = Path(__file__).parent / ".copier.lock"
    try:
        if _lock_file.exists():
            # Check if the PID in the lock file is still running
            try:
                old_pid = int(_lock_file.read_text().strip())
                import psutil
                if psutil.pid_exists(old_pid):
                    log.warning(f"📡 Otra instancia del copier corriendo (PID={old_pid}). Saliendo.")
                    sys.exit(0)
            except (ImportError, ValueError):
                pass  # psutil not installed or invalid PID — continue
        _lock_file.write_text(str(os.getpid()))
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

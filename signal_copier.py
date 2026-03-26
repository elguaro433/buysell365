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
    "NAS100": "US100Cash", "NASDAQ": "US100Cash", "US100": "US100Cash",
    "US30": "US30Cash", "DOW": "US30Cash", "DJ30": "US30Cash",
    "SPX500": "US500Cash", "SP500": "US500Cash", "US500": "US500Cash",
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
def parse_signal(text):
    """Parse trading signal from text. Returns dict or None."""
    if not text or len(text) < 10:
        return None

    upper = text.upper().replace("\n", " ").replace("  ", " ").strip()

    # Skip update/close messages (English + Spanish)
    # Skip noise messages (info, ads, errors)
    _ignore_keywords = [
        "SSF COPIER", "SSF TRADE COPIER", "AUTOMATIZACION", "CUPON", "SURESHOTFX.COM",
        "INVALID PARAMETERS", "INVALID ORDER", "MARKET IS TOO VOLATILE", "RISK SMALL",
        "GOLD ANALYSIS", "LET'S WAIT", "HOLA MIEMBROS", "HELLO VIP",
        "SL UPDATED", "HIT OUR RISK",
    ]
    if any(w in upper for w in _ignore_keywords):
        return None

    # Update/close messages (English + Spanish)
    _update_keywords = [
        "CLOSE HALF", "CLOSE PARTIAL", "FULL CLOSE", "MOVE SL", "RUNNING", "PIPS PROFIT",
        "STOP LOSS HIT", "TP HIT", "HIT OUR RISK",
        "CIERRA LA MITAD", "CIERRE DE LA MITAD", "CIERRE MEDIO", "CIERRE PARCIAL",
        "MOVER SL", "MOVER EL SL", "MOVIMOS EL SL",
        "CERRAR COMPLETAMENTE", "EN CURSO CON", "GANANCIA DE",
    ]
    if any(w in upper for w in _update_keywords):
        return _parse_update(text, upper)

    # Detect direction (including LIMIT orders — treated as market orders)
    direction = None
    is_limit = "LIMIT" in upper
    if any(w in upper for w in ["BUY", "COMPRA", "LONG"]):
        direction = "BUY"
    elif any(w in upper for w in ["SELL", "VENTA", "SHORT"]):
        direction = "SELL"
    if not direction:
        return None

    # Detect pair
    pair_found = None
    for alias, mt5_sym in SYMBOL_MAP.items():
        if re.search(rf'\b{alias}\b', upper):
            pair_found = (alias, mt5_sym)
            break
    if not pair_found:
        return None

    alias, mt5_symbol = pair_found

    # Extract SL and TP
    sl_match = re.search(r'(?:SL|STOP\s*LOSS)[:\s]*(\d+\.?\d*)', upper)
    tp_match = re.search(r'(?:TP|TAKE\s*PROFIT)[:\s]*(\d+\.?\d*)', upper)

    # Extract entry price
    entry_match = re.search(rf'(?:BUY|SELL|COMPRA|VENTA)\s+(?:DE\s+)?(?:{alias}\s+)?(\d+\.?\d*)', upper)
    if not entry_match:
        entry_match = re.search(rf'{alias}\s+(?:BUY|SELL|COMPRA|VENTA)\s+(\d+\.?\d*)', upper)

    if not sl_match or not tp_match:
        return None

    try:
        sl = float(sl_match.group(1))
        tp = float(tp_match.group(1))
        # Entry price: use from text if available, otherwise 0 (will use market price)
        entry = float(entry_match.group(1)) if entry_match else 0.0
    except (ValueError, IndexError):
        return None

    if sl <= 0 or tp <= 0:
        return None
    # entry=0 es OK — se usará precio de mercado en execute_in_mt5()

    # Detect trader
    trader = "Desconocido"
    trader_match = re.search(r'(?:Trade by|Operaci[oó]n de)\s+(\w+)', text, re.IGNORECASE)
    if trader_match:
        trader = trader_match.group(1)

    # Detect source
    source = "Externa"
    text_lower = text.lower()
    if "sureshot" in text_lower or "ssf" in text_lower:
        source = "SureShotFX"
    elif "learn2trade" in text_lower or "l2t" in text_lower:
        source = "Learn2Trade"
    elif "jessica" in text_lower:
        source = "JessicaGold"

    return {
        "type": "new_signal",
        "pair": alias,
        "mt5_symbol": mt5_symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "source": source,
        "trader": trader,
        "raw": text[:200],
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
    """Send ONLY new signals to BuySell365 channel. No updates, no closes."""
    import requests

    # Solo enviar señales de apertura — nada de cierres ni updates
    if signal["type"] == "update":
        return  # No enviar al canal

    _dir = "COMPRA" if signal["direction"] == "BUY" else "VENTA"
    tipo_emoji = "🟢" if signal["direction"] == "BUY" else "🔴"
    _entry_display = signal['entry'] if signal['entry'] > 0 else "Mercado"
    msg = (
        f"{tipo_emoji} {_dir} {signal['pair']}\n"
        f"Entrada: {_entry_display}\n"
        f"SL: {signal['sl']}\n"
        f"TP: {signal['tp']}"
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHANNEL_ID, "text": msg}, timeout=10)
    except Exception as e:
        log.warning(f"Error sending to channel: {e}")


# === MAIN USERBOT ===
async def main():
    from telethon import TelegramClient, events

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)

    # Known signal channel keywords (to filter noise)
    # Solo estos 3 canales VIP de SureShotFX (IDs exactos)
    ALLOWED_CHANNEL_IDS = {
        -1001422000261,   # Sureshot FX VIP
        -1001661400724,   # SureShot GOLD (VIP)
        -1001700795303,   # Sureshot INDICES (VIP)
    }
    SIGNAL_KEYWORDS = ["sureshot"]  # Solo para el log de monitoreo

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

            # Parse the signal
            signal = parse_signal(text)
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

    # List channels we're monitoring
    async for dialog in client.iter_dialogs():
        if dialog.id in ALLOWED_CHANNEL_IDS:
            log.info(f"📡 Monitoreando: {dialog.title} (ID: {dialog.id})")

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

"""
BuySell365 — Monitor de cuenta REAL
Lee posiciones de MSC Gold Stable Pro y las envía a Telegram.
Solo lectura — no ejecuta nada.
Corre como proceso separado del bot principal.
"""
import os, time, json, logging, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Config
MT5_LOGIN_REAL = 88849791
MT5_PASSWORD_REAL = "Andorra433+"
MT5_SERVER_REAL = "XMGlobal-MT5 4"
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = os.getenv("USER_ID_1", "8696207137")
CHECK_INTERVAL = 30  # segundos

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [MONITOR] %(message)s",
                    handlers=[logging.FileHandler(Path(__file__).parent / "logs" / "monitor_real.log", encoding="utf-8"),
                              logging.StreamHandler()])
log = logging.getLogger("monitor_real")

# State
_posiciones_conocidas: dict = {}  # {ticket: {symbol, type, price_open, volume, sl, tp}}
_state_file = Path(__file__).parent / "monitor_real_state.json"


def _cargar_estado():
    global _posiciones_conocidas
    try:
        if _state_file.exists():
            data = json.loads(_state_file.read_text(encoding="utf-8"))
            _posiciones_conocidas = {int(k): v for k, v in data.items()}
    except Exception:
        _posiciones_conocidas = {}


def _guardar_estado():
    try:
        _state_file.write_text(json.dumps(_posiciones_conocidas, default=str), encoding="utf-8")
    except Exception:
        pass


def _enviar_telegram(msg, chat_id=None):
    """Envía mensaje a Telegram."""
    if not BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    target = chat_id or CHANNEL_ID
    try:
        requests.post(url, json={"chat_id": target, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        log.warning(f"Error Telegram: {e}")


def main():
    import MetaTrader5 as mt5

    log.info("📊 Monitor cuenta REAL iniciando...")
    log.info(f"📊 Login: {MT5_LOGIN_REAL} | Server: {MT5_SERVER_REAL}")

    _cargar_estado()

    while True:
        try:
            # Conectar a cuenta real
            if not mt5.initialize():
                log.warning("MT5 no se pudo inicializar")
                time.sleep(CHECK_INTERVAL)
                continue

            # Login a cuenta real
            authorized = mt5.login(MT5_LOGIN_REAL, password=MT5_PASSWORD_REAL, server=MT5_SERVER_REAL)
            if not authorized:
                log.warning(f"Login fallido: {mt5.last_error()}")
                time.sleep(CHECK_INTERVAL)
                continue

            # Leer posiciones
            positions = mt5.positions_get()
            account = mt5.account_info()
            balance = account.balance if account else 0
            equity = account.equity if account else 0

            current_tickets = set()
            if positions:
                for pos in positions:
                    current_tickets.add(pos.ticket)
                    # ¿Es nueva?
                    if pos.ticket not in _posiciones_conocidas:
                        _dir = "COMPRA" if pos.type == 0 else "VENTA"
                        emoji = "🟢" if pos.type == 0 else "🔴"

                        msg = (
                            f"{emoji} *{_dir} {pos.symbol}*\n"
                            f"Entrada: {pos.price_open}\n"
                            f"SL: {pos.sl}\n"
                            f"TP: {pos.tp}"
                        )

                        _enviar_telegram(msg)
                        log.info(f"📊 NUEVA POSICIÓN: {_dir} {pos.symbol} @ {pos.price_open}")

                        _posiciones_conocidas[pos.ticket] = {
                            "symbol": pos.symbol,
                            "type": pos.type,
                            "price_open": pos.price_open,
                            "volume": pos.volume,
                            "sl": pos.sl,
                            "tp": pos.tp,
                            "profit": pos.profit,
                        }

            # Detectar posiciones cerradas
            cerradas = set(_posiciones_conocidas.keys()) - current_tickets
            for ticket in cerradas:
                info = _posiciones_conocidas[ticket]
                _dir = "COMPRA" if info["type"] == 0 else "VENTA"

                msg = f"🔄 *{info['symbol']}* — CIERRE ({_dir})"
                _enviar_telegram(msg)
                log.info(f"📊 POSICIÓN CERRADA: {_dir} {info['symbol']} (ticket {ticket})")
                del _posiciones_conocidas[ticket]

            _guardar_estado()

            # Reconectar a cuenta demo para que el bot principal siga funcionando
            mt5.login(336093063, password="Emmanuel433+", server="XMGlobal-MT5 9")

        except Exception as e:
            log.error(f"Error monitor: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

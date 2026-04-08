"""
BuySell365 Signal Copier — Userbot que escucha canales VIP de Telegram
Lee señales de SureShotFX, Learn2Trade, etc. y las ejecuta en MT5 + reenvía al canal BuySell365.
Usa Telethon (cuenta personal de Telegram).
"""
import os, re, asyncio, logging, time, json, threading, io
from logging.handlers import RotatingFileHandler
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# === CONFIG ===
API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
PHONE = os.getenv("TG_PHONE", "")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
GROUP_ID = os.getenv("GROUP_ID", "").strip()  # FIX 2026-04-07: Para celebrar TPs en el grupo público
ADMIN_ID = os.getenv("USER_ID_1", "").strip()  # Admin para reportes privados diarios

SESSION_FILE = str(Path(__file__).parent / "signal_copier_session")

# Fix sqlite locked: set WAL mode and timeout
# FIX 2026-04-06: NUNCA borrar el archivo de sesión si está locked.
# Antes: si SQLite estaba locked, borraba la sesión → perdía la autenticación → FloodWait loop.
# Ahora: si está locked, simplemente espera y reintenta. Telethon maneja locks internamente.
import sqlite3
_session_db = SESSION_FILE + ".session"
if os.path.exists(_session_db):
    try:
        _conn = sqlite3.connect(_session_db, timeout=10)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.close()
    except Exception as _e_db:
        # Si está locked, NO borrar — solo advertir y dejar que Telethon lo maneje
        print(f"⚠️ Sesión SQLite ocupada ({_e_db}) — Telethon lo manejará internamente.")

# === LOGGING ===
# FIX 2026-04-06b: En Windows, FileHandler puede bloquearse entre procesos.
# Usar modo 'a' con delay=True para minimizar conflictos de lock.
_log_dir = Path(__file__).parent / "logs"
_log_dir.mkdir(exist_ok=True)
_log_handlers = [logging.StreamHandler()]
try:
    _fh = RotatingFileHandler(
        _log_dir / "copier.log", encoding="utf-8",
        maxBytes=5 * 1024 * 1024,  # 5 MB max por archivo
        backupCount=3              # Mantener 3 backups (copier.log.1, .2, .3)
    )
    _log_handlers.append(_fh)
except Exception:
    # Si el archivo está bloqueado por otro proceso, logear solo a stderr
    pass
logging.basicConfig(level=logging.INFO, format="%(asctime)s [COPIER] %(message)s",
                    handlers=_log_handlers)
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
    # Petróleo (Sureshot INDICES envía USOIL)
    "USOIL": "USOILCash", "WTI": "USOILCash", "CRUDEOIL": "USOILCash",
    # Crypto (FxPremiere envía BTC/USD)
    "BTCUSD": "BTCUSDm",
}

MAGIC_COPIER = 20260325
TWELVE_KEY = os.getenv("TWELVE_DATA_KEY", "")

# Mapa de nombres para display — FIX 2026-04-06: mantener nombres originales del mercado
# Antes: US30→"DOW 30", XAUUSD→"XAU/USD" — el usuario quiere los nombres estándar de trading
_DISPLAY_MAP = {
    # MT5 symbols → nombre estándar
    "GOLD": "XAUUSD", "US100Cash": "NAS100", "US500Cash": "US500",
    "US30Cash": "US30", "GER40Cash": "GER40", "BRENT": "BRENT",
    # Aliases → nombre estándar
    "XAUUSD": "XAUUSD", "ORO": "XAUUSD",
    "NAS100": "NAS100", "NASDAQ": "NAS100", "NASDAQ100": "NAS100", "NQ": "NAS100", "US100": "NAS100",
    "US30": "US30", "DOW": "US30", "DJ30": "US30",
    "SPX500": "US500", "SP500": "US500", "US500": "US500",
    "GER40": "GER40", "DAX": "GER40", "DE40": "GER40",
    "UKOIL": "BRENT", "OIL": "BRENT",
    # Petróleo WTI
    "USOIL": "USOIL", "WTI": "USOIL", "USOILCash": "USOIL",
    # Crypto
    "BTCUSD": "BTC/USD", "BTCUSDm": "BTC/USD",
}

def _get_display_pair(pair: str) -> str:
    """Devuelve nombre bonito del par para mensajes de Telegram."""
    if pair in _DISPLAY_MAP:
        return _DISPLAY_MAP[pair]
    elif len(pair) == 6 and pair.isalpha():
        return f"{pair[:3]}/{pair[3:]}"
    return pair


def fmt_price(v, zero_label="—"):
    """Formato de precio: 2 decimales para valores >= 100 (GOLD, índices),
    hasta 5 decimales para forex. zero_label se muestra si v <= 0."""
    if v <= 0:
        return zero_label
    return f"{v:.2f}" if v >= 100 else f"{v:.5f}".rstrip("0").rstrip(".")


# === TP TRACKER ===
# _open_signals: { sig_id → {"signal": signal_dict, "sent_at": float, "telegram_msg_id": int} }
_open_signals: dict = {}
_signals_lock = threading.Lock()
_resolved_signals: set = set()  # sig_ids ya resueltos — no volver a cargar del JSON
# FIX 2026-04-07: Cache anti-duplicados — persiste incluso después de TP/SL resolution
# { "PAIR_DIRECTION_ENTRY": timestamp_sent }
_recently_sent: dict = {}
# FIX 2026-04-08: Anti-duplicado para TP/SL notifications
# { "PAIR_DIRECTION_tp/sl": timestamp } — evita doble SL HIT / TP HIT
_recently_notified: dict = {}

# === DAILY RESULTS TRACKER (para reportes de mediodía y tarde) ===
# Lista de dicts: {"pair": str, "direction": str, "entry": float, "tp": float, "pips_str": str, "result": "tp"|"sl", "time": float}
_daily_results: list = []
_daily_results_lock = threading.Lock()

# Archivo de señales manuales — el admin registra señales vía /rastrear en bot.py
MANUAL_SIGNALS_FILE = Path(__file__).parent / "manual_signals.json"
# Archivo de señales abiertas — sobrevive reinicios del copier
OPEN_SIGNALS_FILE = Path(__file__).parent / "copier_open_signals.json"


def _save_open_signals():
    """Guarda señales abiertas a disco para sobrevivir reinicios."""
    try:
        with _signals_lock:
            data = {}
            for sid, sdata in _open_signals.items():
                data[sid] = {
                    "signal": sdata["signal"],
                    "sent_at": sdata["sent_at"],
                    "telegram_msg_id": sdata.get("telegram_msg_id"),
                }
        tmp = str(OPEN_SIGNALS_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        import os
        os.replace(tmp, OPEN_SIGNALS_FILE)
    except Exception as e:
        log.warning(f"Error guardando open_signals: {e}")


def _load_open_signals():
    """Carga señales abiertas desde disco al arrancar."""
    try:
        if OPEN_SIGNALS_FILE.exists():
            with open(OPEN_SIGNALS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded = 0
            with _signals_lock:
                for sid, sdata in data.items():
                    if sid not in _open_signals and sid not in _resolved_signals:
                        age = (time.time() - sdata.get("sent_at", 0)) / 3600
                        sig = sdata.get("signal", {})
                        entry = sig.get("entry", 0) or 0
                        # Descartar: >4h de antigüedad O sin precio de entrada válido
                        if age > 4:
                            log.info(f"🗑️ Señal expirada al cargar ({age:.1f}h): {sid[:30]}")
                            continue
                        if entry <= 0:
                            log.info(f"🗑️ Señal sin entrada al cargar (entry=0): {sid[:30]}")
                            continue
                        _open_signals[sid] = sdata
                        loaded += 1
            if loaded:
                log.info(f"📂 {loaded} señales válidas cargadas desde disco (sobrevivieron reinicio)")
    except Exception as e:
        log.warning(f"Error cargando open_signals: {e}")

# === EDIT TRACKER ===
# Mensajes reenviados SIN precio de entrada (entry=0) → esperar edición del canal original
# { telegram_msg_id → signal_dict } para actualizarlos cuando el canal edite con el precio real
_pending_entry: dict = {}   # { msg_id: signal } — señales publicadas sin precio de entrada
_pending_entry_lock = threading.Lock()
_published_msg_ids: set = set()  # msg_ids ya publicados como señal completa (evita duplicar en edit)

# (Buffer 30s eliminado — señales se publican de inmediato con precio actual)


def _normalize_twelve_symbol(pair: str) -> str:
    """Convierte símbolo interno → formato Twelve Data (XAU/USD, NDX, etc.)."""
    _twelve_map = {
        # ORO
        "GOLD": "XAU/USD", "XAUUSD": "XAU/USD", "GC": "XAU/USD", "XAUUSD=X": "XAU/USD",
        # Índices — tickers yfinance sin sufijo → símbolo Twelve Data
        "US100Cash": "NDX",  "NQ": "NDX",  "NAS100": "NDX",  "NASDAQ": "NDX",
        "US500Cash": "SPX",  "ES": "SPX",  "SP500": "SPX",
        "US30Cash":  "DJI",  "YM": "DJI",  "DOW30": "DJI",
        "GER40Cash": "GER40", "GER40": "GER40", "DAX": "GER40", "DE40": "GER40",
        # Commodities
        "BRENT": "BRENT",
    }
    if pair in _twelve_map:
        return _twelve_map[pair]
    # Forex pairs: EURUSD → EUR/USD (insertar "/" en posición 3)
    if len(pair) == 6 and pair.isalpha():
        return f"{pair[:3]}/{pair[3:]}"
    return pair


def _get_current_price(pair: str) -> float | None:
    """Fetch current price via yfinance (gratis, sin límite).
    Twelve Data se reserva SOLO para gráficos de velas.
    """
    _yf_map = {
        "GOLD": "GC=F", "XAUUSD": "GC=F",  # FIX 2026-04-07: XAUUSD=X delisted — usar GC=F (COMEX futures)
        "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
        "GBPJPY": "GBPJPY=X", "AUDCAD": "AUDCAD=X", "USDCAD": "USDCAD=X",
        "EURCHF": "EURCHF=X", "GBPAUD": "GBPAUD=X", "EURJPY": "EURJPY=X",
        "NZDUSD": "NZDUSD=X", "AUDUSD": "AUDUSD=X",
        "US100Cash": "NQ=F", "US500Cash": "ES=F", "US30Cash": "YM=F",
        "NAS100": "NQ=F", "US100": "NQ=F", "NASDAQ": "NQ=F",
        "US30": "YM=F", "DOW30": "YM=F", "DJ30": "YM=F", "DOW": "YM=F",
        "US500": "ES=F", "SP500": "ES=F",
        "GER40Cash": "GER40=X", "GER40": "GER40=X", "DAX": "GER40=X",
        "BRENT": "BZ=F",
    }
    yf_ticker = _yf_map.get(pair)
    if not yf_ticker:
        if len(pair) == 6 and pair.isalpha():
            yf_ticker = f"{pair}=X"
        else:
            yf_ticker = pair

    # Spot-forex =X no tienen datos fiables en yfinance → ir directo a Twelve Data
    # FIX 2026-04-07: GC=F y futuros SÍ funcionan en yfinance — solo skip =X (spot forex)
    _use_yf = not yf_ticker.endswith("=X")
    if _use_yf:
        try:
            import yfinance as yf
            import warnings, io, sys
            # Suprimir stderr/stdout de yfinance (evitar spam "possibly delisted")
            _old_stderr = sys.stderr
            sys.stderr = io.StringIO()
            try:
                tk = yf.Ticker(yf_ticker)
                val = getattr(tk.fast_info, 'last_price', None)
                if val and val > 0:
                    return float(val)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    data = tk.history(period="1d", interval="5m")
                if data is not None and not data.empty:
                    val = float(data["Close"].iloc[-1])
                    return val if val > 0 else None
            finally:
                sys.stderr = _old_stderr
        except Exception:
            pass

    # Fallback final: Twelve Data (gasta 1 crédito)
    if TWELVE_KEY:
        try:
            import requests
            symbol = _normalize_twelve_symbol(pair)
            resp = requests.get(
                "https://api.twelvedata.com/price",
                params={"symbol": symbol, "apikey": TWELVE_KEY},
                timeout=8,
            )
            data = resp.json()
            val = float(data.get("price", 0) or 0)
            return val if val > 0 else None
        except Exception:
            pass
    return None


def _fetch_chart_image(pair: str, direction: str, entry: float, tp: float, *, title_override: str = "") -> bytes | None:
    """Generate professional chart using Twelve Data (primary) or yfinance (fallback) + matplotlib.
    title_override: si se pasa, usa ese título en vez de 'TP HIT'."""
    pair_d = _get_display_pair(pair)
    try:
        import requests
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
        import numpy as np

        opens, closes, highs, lows = None, None, None, None

        # ── Fuente 1: Twelve Data (si hay key y créditos) ──
        if TWELVE_KEY:
            try:
                symbol = _normalize_twelve_symbol(pair)
                resp = requests.get(
                    "https://api.twelvedata.com/time_series",
                    params={"symbol": symbol, "interval": "15min", "outputsize": 50, "apikey": TWELVE_KEY},
                    timeout=15,
                )
                data = resp.json()
                if "values" in data:
                    values = data["values"][::-1]
                    opens  = [float(v["open"])  for v in values]
                    closes = [float(v["close"]) for v in values]
                    highs  = [float(v["high"])  for v in values]
                    lows   = [float(v["low"])   for v in values]
                    log.info(f"📊 Chart data from Twelve Data ({len(values)} candles)")
                else:
                    log.warning(f"📊 Twelve Data sin datos: {data.get('message','')[:80]}")
            except Exception as _e_td:
                log.warning(f"📊 Twelve Data error: {_e_td}")

        # ── Fuente 2: yfinance fallback (gratis, sin límite) ──
        if opens is None:
            try:
                import yfinance as yf
                import warnings, sys as _sys_yf
                _yf_chart_map = {
                    "GOLD": "GC=F", "XAUUSD": "GC=F", "GC": "GC=F",
                    "US30": "YM=F", "DOW30": "YM=F", "DJ30": "YM=F", "YM": "YM=F",
                    "NAS100": "NQ=F", "US100": "NQ=F", "NASDAQ": "NQ=F", "NQ": "NQ=F",
                    "US500": "ES=F", "SP500": "ES=F", "ES": "ES=F",
                }
                _yf_ticker = _yf_chart_map.get(pair)
                if not _yf_ticker:
                    if len(pair) == 6 and pair.isalpha():
                        _yf_ticker = f"{pair}=X"
                    else:
                        _yf_ticker = pair
                _old_stderr = _sys_yf.stderr
                _sys_yf.stderr = io.StringIO()
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        _df = yf.download(_yf_ticker, period="1d", interval="5m", progress=False)
                finally:
                    _sys_yf.stderr = _old_stderr
                if _df is not None and len(_df) >= 10:
                    # Handle both single and multi-level columns
                    _cols = _df.columns
                    if hasattr(_cols, 'nlevels') and _cols.nlevels > 1:
                        _df.columns = _df.columns.get_level_values(0)
                    # Limitar a últimas 50 velas para igualar look de Twelve Data
                    _df = _df.tail(50)
                    opens  = _df["Open"].tolist()
                    closes = _df["Close"].tolist()
                    highs  = _df["High"].tolist()
                    lows   = _df["Low"].tolist()
                    log.info(f"📊 Chart data from yfinance ({len(opens)} candles)")
                else:
                    log.warning(f"📊 yfinance sin datos suficientes para {_yf_ticker}")
            except Exception as _e_yf:
                log.warning(f"📊 yfinance chart error: {_e_yf}")

        if opens is None or len(opens) < 5:
            return None
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
        if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
            pips_label = f"+{pips_won:.0f} pts" if pips_won >= 1 else f"+{pips_won:.1f} pts"
        elif "JPY" in pair.upper():
            # JPY pairs: 1 pip = 0.01 → multiply by 100
            pips_label = f"+{pips_won * 100:.0f} pips" if pips_won > 0 else ""
        elif entry >= 100:
            # Indices (NAS100, US30, etc.): raw points
            pips_label = f"+{pips_won:.1f} pts" if pips_won > 0 else ""
        else:
            pips_label = f"+{pips_won * 10000:.0f} pips" if pips_won > 0 else ""

        # Título con pips
        dir_label = direction.upper()  # BUY/SELL sin traducir
        if title_override:
            title = title_override
        else:
            title = f"✅ TP HIT — {dir_label} {pair_d}"
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
    # FIX 2026-04-08: Usar TP final (el más alto/bajo) para calcular profit real
    tp = signal.get("_tp_final", signal["tp"]) or signal["tp"]
    pair_d = _get_display_pair(pair)

    fmt = lambda v: fmt_price(v, zero_label="Market")

    dir_label = direction.upper()  # BUY/SELL sin traducir
    dir_emoji = "🟢" if direction == "BUY" else "🔴"

    # Calcular pips ganados con el TP final
    pips_won = abs(tp - entry) if entry > 0 and tp > 0 else 0
    if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
        pips_str = f"+{pips_won:.0f} pts" if pips_won >= 1 else ""
    elif "JPY" in pair.upper():
        # JPY pairs: 1 pip = 0.01 → multiply by 100
        pips_str = f"+{pips_won * 100:.0f} pips" if pips_won > 0 else ""
    elif entry >= 100:
        # Indices (NAS100, US30, etc.): raw points
        pips_str = f"+{pips_won:.1f} pts" if pips_won > 0 else ""
    else:
        pips_str = f"+{pips_won * 10000:.0f} pips" if pips_won > 0 else ""

    pips_line = f"\n💰 Profit: *{pips_str}*" if pips_str else ""

    # Build TP lines — show all TPs, mark the hit one with ✅
    tp2 = signal.get("tp2", 0) or 0
    tp3 = signal.get("tp3", 0) or 0
    tp4 = signal.get("tp4", 0) or 0
    tp5 = signal.get("tp5", 0) or 0
    has_multi_tp = any(t > 0 for t in [tp2, tp3, tp4, tp5])

    # FIX 2026-04-08: Marcar TODOS los TPs como ✅ (precio alcanzó el TP final)
    _tp1_val = signal.get("tp", 0) or 0
    tp_lines = ""
    if has_multi_tp:
        if _tp1_val > 0:
            tp_lines += f"\n✅ TP1: {fmt(_tp1_val)}"
        if tp2 > 0:
            tp_lines += f"\n✅ TP2: {fmt(tp2)}"
        if tp3 > 0:
            tp_lines += f"\n✅ TP3: {fmt(tp3)}"
        if tp4 > 0:
            tp_lines += f"\n✅ TP4: {fmt(tp4)}"
        if tp5 > 0:
            tp_lines += f"\n✅ TP5: {fmt(tp5)}"
    else:
        tp_lines = f"\n✅ TP: {fmt(tp)}"

    msg = (
        f"🎯🎯🎯 *TP HIT* 🎯🎯🎯\n"
        f"━━━━━━━━━━━━━━\n"
        f"{dir_emoji} *{dir_label} — {pair_d}*\n\n"
        f"📍 Entry: {fmt(entry)}"
        f"{tp_lines}"
        f"{pips_line}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🚀 _BuySell365 Pro — señal exitosa_"
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
            log.info(f"🎉 TP CELEBRATION enviada: {dir_label} {pair}")
        else:
            log.warning(f"Celebration send error: {resp.status_code} {resp.text[:80]}")
    except Exception as e:
        log.warning(f"Celebration send error: {e}")

    # FIX 2026-04-07: También celebrar en el grupo público (marketing)
    if GROUP_ID and str(GROUP_ID) != str(CHANNEL_ID):
        import random
        _promos = [
            "\n\n💎 *¿Quieres recibir estas señales?*\nÚnete al canal VIP y opera con nosotros.\n👉 Escribe */vip* para más info",
            "\n\n🤖 *Activa el Copy Trading*\nCopia estas operaciones automáticamente en tu cuenta.\n👉 Escribe */vip* para activarlo",
            "\n\n🔥 *Otra victoria más del equipo*\nNo te quedes fuera, únete al VIP.\n👉 Escribe */vip* y empieza hoy",
            "\n\n📈 *Resultados reales, sin trucos*\nSeñales en vivo con entrada, TP y SL exactos.\n👉 Escribe */vip* para unirte",
        ]
        _msg_grupo = (
            f"🎯🎯🎯 *TP HIT* 🎯🎯🎯\n"
            f"━━━━━━━━━━━━━━\n"
            f"{dir_emoji} *{dir_label} — {pair_d}*\n\n"
            f"📍 Entry: {fmt(entry)}"
            f"{tp_lines}"
            f"{pips_line}\n"
            f"━━━━━━━━━━━━━━\n"
            f"🚀 _BuySell365 Pro — señal exitosa_"
            f"{random.choice(_promos)}"
        )
        try:
            if chart_bytes:
                _url_g = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                _pay_g = {"chat_id": GROUP_ID, "caption": _msg_grupo, "parse_mode": "Markdown"}
                requests.post(_url_g, data=_pay_g,
                    files={"photo": ("chart.png", chart_bytes, "image/png")}, timeout=20)
            else:
                _url_g = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                _pay_g = {"chat_id": GROUP_ID, "text": _msg_grupo, "parse_mode": "Markdown"}
                requests.post(_url_g, json=_pay_g, timeout=10)
            log.info(f"📢 TP celebration enviada al GRUPO: {dir_label} {pair}")
        except Exception as _eg:
            log.warning(f"Error enviando TP al grupo: {_eg}")


def _send_sl_notification(signal: dict, reply_to_msg_id: int = None) -> None:
    """Notify channel that SL was hit — same professional model as TP HIT."""
    import requests

    direction = signal["direction"]
    pair = signal["pair"]
    entry = signal["entry"]
    sl = signal["sl"]
    pair_d = _get_display_pair(pair)

    dir_label = direction.upper()
    dir_emoji = "🟢" if direction == "BUY" else "🔴"

    fmt = fmt_price

    # Calcular pips perdidos
    pips_lost = abs(sl - entry) if entry > 0 and sl > 0 else 0
    if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
        loss_str = f"-{pips_lost:.0f} pts" if pips_lost >= 1 else ""
    elif "JPY" in pair.upper():
        loss_str = f"-{pips_lost * 100:.0f} pips" if pips_lost > 0 else ""
    elif entry >= 100:
        loss_str = f"-{pips_lost:.1f} pts" if pips_lost > 0 else ""
    else:
        loss_str = f"-{pips_lost * 10000:.0f} pips" if pips_lost > 0 else ""

    loss_line = f"\n💔 Loss: *{loss_str}*" if loss_str else ""

    msg = (
        f"🛑🛑🛑 *SL HIT* 🛑🛑🛑\n"
        f"━━━━━━━━━━━━━━\n"
        f"{dir_emoji} *{dir_label} — {pair_d}*\n\n"
        f"📍 Entry: {fmt(entry)}\n"
        f"🛡️ SL: {fmt(sl)}"
        f"{loss_line}\n"
        f"━━━━━━━━━━━━━━\n"
        f"📊 _BuySell365 Pro — gestión de riesgo_"
    )

    # FIX 2026-04-07: Sin gráfica para SL — solo texto
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHANNEL_ID, "text": msg, "parse_mode": "Markdown"}
        if reply_to_msg_id:
            payload["reply_to_message_id"] = reply_to_msg_id
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info(f"🛑 SL notification enviada: {dir_label} {pair}")
        else:
            log.warning(f"SL notification error: {resp.status_code} {resp.text[:80]}")
    except Exception as e:
        log.warning(f"SL notification error: {e}")


def _record_daily_result(signal: dict, result: str) -> None:
    """Registra un TP o SL en el tracker diario para los reportes del grupo."""
    pair = signal.get("pair", "?")
    direction = signal.get("direction", "?")
    entry = signal.get("entry", 0) or 0
    tp = signal.get("_tp_final", signal.get("tp", 0)) or 0
    sl = signal.get("sl", 0) or 0

    if result == "tp":
        pips_raw = abs(tp - entry) if entry > 0 and tp > 0 else 0
    else:
        pips_raw = abs(sl - entry) if entry > 0 and sl > 0 else 0

    # Formatear pips según activo
    if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
        pips_str = f"{pips_raw:.0f} pts"
        pips_numeric = pips_raw
    elif "JPY" in pair.upper():
        pips_str = f"{pips_raw * 100:.0f} pips"
        pips_numeric = pips_raw * 100
    elif entry >= 100:
        pips_str = f"{pips_raw:.1f} pts"
        pips_numeric = pips_raw
    else:
        pips_str = f"{pips_raw * 10000:.0f} pips"
        pips_numeric = pips_raw * 10000

    record = {
        "pair": pair,
        "pair_display": _get_display_pair(pair),
        "direction": direction,
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "pips_str": pips_str,
        "pips_numeric": pips_numeric,
        "result": result,
        "time": time.time(),
    }
    with _daily_results_lock:
        _daily_results.append(record)
    log.info(f"📊 Daily tracker: +1 {result.upper()} {pair} ({pips_str}) — total hoy: {len(_daily_results)}")


def _build_promo_report(hora_label: str) -> str | None:
    """Construye el mensaje de reporte promocional para el grupo público.
    Retorna None si no hay TPs que reportar."""
    from datetime import datetime
    import pytz
    tz = pytz.timezone("Europe/Andorra")
    hoy = datetime.now(tz).strftime("%d/%m/%Y")

    with _daily_results_lock:
        # Solo resultados de hoy (por si acaso hay remanentes)
        today_start = datetime.now(tz).replace(hour=0, minute=0, second=0).timestamp()
        results = [r for r in _daily_results if r["time"] >= today_start]

    tps = [r for r in results if r["result"] == "tp"]
    sls = [r for r in results if r["result"] == "sl"]

    if not tps:
        return None  # No hay TPs — no publicar

    # Agrupar pips por tipo (GOLD=pts, indices=pts, forex=pips)
    gold_pts = sum(r["pips_numeric"] for r in tps if r["pair"] in ("GOLD", "XAUUSD", "XAUUSD=X"))
    index_pts = sum(r["pips_numeric"] for r in tps if r["pair"] not in ("GOLD", "XAUUSD", "XAUUSD=X") and r["entry"] >= 100)
    forex_pips = sum(r["pips_numeric"] for r in tps if r["pair"] not in ("GOLD", "XAUUSD", "XAUUSD=X") and r["entry"] < 100)

    # Detalle de cada TP
    tp_lines = ""
    for r in tps:
        dir_emoji = "🟢" if r["direction"] == "BUY" else "🔴"
        tp_lines += f"  {dir_emoji} {r['pair_display']} — *+{r['pips_str']}*\n"

    # Resumen de puntos/pips
    resumen_parts = []
    if gold_pts > 0:
        resumen_parts.append(f"🥇 GOLD: *+{gold_pts:.0f} pts*")
    if index_pts > 0:
        resumen_parts.append(f"📈 Indices: *+{index_pts:.1f} pts*")
    if forex_pips > 0:
        resumen_parts.append(f"💱 Forex: *+{forex_pips:.0f} pips*")
    resumen = "\n".join(resumen_parts)

    wr = len(tps) / (len(tps) + len(sls)) * 100 if (tps or sls) else 0

    msg = (
        f"📊📊📊 *REPORTE {hora_label}* 📊📊📊\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {hoy}\n\n"
        f"🎯 *{len(tps)} TP{'s' if len(tps) != 1 else ''} ganado{'s' if len(tps) != 1 else ''}*"
    )
    if sls:
        msg += f"  |  🛑 {len(sls)} SL{'s' if len(sls) != 1 else ''}"
    msg += f"  |  ✅ *{wr:.0f}% Win Rate*\n\n"

    msg += f"*Señales exitosas:*\n{tp_lines}\n"
    msg += f"{resumen}\n"
    msg += f"━━━━━━━━━━━━━━━━━━━\n\n"
    msg += (
        f"💎 *¿Quieres recibir estas señales en tiempo real?*\n"
        f"Únete al canal VIP y copia estas operaciones.\n\n"
        f"👉 Escribe */vip* para más info\n"
        f"🤖 O activa el *Copy Trading* automático\n\n"
        f"_BuySell365 Pro — Resultados reales, verificados en MT5_"
    )
    return msg


async def _loop_promo_reportes() -> None:
    """Loop que envía reportes promocionales al grupo público a las 12:00 y 17:00 hora Andorra."""
    import requests
    from datetime import datetime
    import pytz
    tz = pytz.timezone("Europe/Andorra")

    _sent_today: dict = {}  # {"12": "2026-04-08", "17": "2026-04-08"}

    _promo_buttons = json.dumps({
        "inline_keyboard": [
            [
                {"text": "🤖 Empezar Copy Trading", "url": "https://social.tp-redirect.com/s/WRE0V7jm"},
            ],
            [
                {"text": "🎁 Abrir Cuenta XM — Bono 100%", "url": "https://clicks.pipaffiliates.com/c?c=1198043&l=es&p=1"},
            ]
        ]
    })

    while True:
        try:
            now = datetime.now(tz)
            hoy_str = now.strftime("%Y-%m-%d")
            hora = now.hour
            minuto = now.minute

            # Reset tracker a medianoche
            if hora == 0 and minuto < 2:
                with _daily_results_lock:
                    if _daily_results:
                        log.info(f"🔄 Reset daily tracker: {len(_daily_results)} resultados del día anterior")
                        _daily_results.clear()
                _sent_today.clear()

            # 12:00 — Reporte de mañana
            if hora == 12 and minuto < 5 and _sent_today.get("12") != hoy_str:
                _sent_today["12"] = hoy_str
                msg = _build_promo_report("DE MAÑANA")
                if msg and GROUP_ID:
                    try:
                        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                        payload = {"chat_id": GROUP_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": _promo_buttons}
                        resp = requests.post(url, json=payload, timeout=10)
                        if resp.status_code == 200:
                            log.info("📢 Reporte mediodía enviado al grupo")
                        else:
                            log.warning(f"Reporte mediodía error: {resp.status_code}")
                    except Exception as e:
                        log.warning(f"Reporte mediodía error: {e}")
                elif not msg:
                    log.info("📢 Reporte mediodía: sin TPs aún, no se envía")

            # 17:00 — Reporte de tarde
            if hora == 17 and minuto < 5 and _sent_today.get("17") != hoy_str:
                _sent_today["17"] = hoy_str
                msg = _build_promo_report("DE TARDE")
                if msg and GROUP_ID:
                    try:
                        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                        payload = {"chat_id": GROUP_ID, "text": msg, "parse_mode": "Markdown", "reply_markup": _promo_buttons}
                        resp = requests.post(url, json=payload, timeout=10)
                        if resp.status_code == 200:
                            log.info("📢 Reporte tarde enviado al grupo")
                        else:
                            log.warning(f"Reporte tarde error: {resp.status_code}")
                    except Exception as e:
                        log.warning(f"Reporte tarde error: {e}")
                elif not msg:
                    log.info("📢 Reporte tarde: sin TPs aún, no se envía")

            # 22:00 — Reporte privado al admin (resumen del día)
            if hora == 22 and minuto < 5 and _sent_today.get("22") != hoy_str:
                _sent_today["22"] = hoy_str
                if ADMIN_ID:
                    with _daily_results_lock:
                        today_start = datetime.now(tz).replace(hour=0, minute=0, second=0).timestamp()
                        results = [r for r in _daily_results if r["time"] >= today_start]
                    tps = [r for r in results if r["result"] == "tp"]
                    sls = [r for r in results if r["result"] == "sl"]
                    with _signals_lock:
                        abiertas = len(_open_signals)
                    _admin_msg = (
                        f"📋 *Copier — Resumen del día*\n"
                        f"📅 {hoy_str}\n\n"
                        f"🎯 TPs: *{len(tps)}*\n"
                        f"🛑 SLs: *{len(sls)}*\n"
                        f"📡 Señales abiertas: *{abiertas}*\n"
                    )
                    if tps:
                        _total_lines = ""
                        for r in tps:
                            _total_lines += f"  ✅ {r['pair_display']} +{r['pips_str']}\n"
                        _admin_msg += f"\n*Detalle TPs:*\n{_total_lines}"
                    if sls:
                        _sl_lines = ""
                        for r in sls:
                            _sl_lines += f"  ❌ {r['pair_display']} -{r['pips_str']}\n"
                        _admin_msg += f"\n*Detalle SLs:*\n{_sl_lines}"
                    try:
                        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                        payload = {"chat_id": ADMIN_ID, "text": _admin_msg, "parse_mode": "Markdown"}
                        resp = requests.post(url, json=payload, timeout=10)
                        if resp.status_code == 200:
                            log.info("📋 Reporte admin enviado")
                        else:
                            log.warning(f"Reporte admin error: {resp.status_code}")
                    except Exception as e:
                        log.warning(f"Reporte admin error: {e}")

        except Exception as e:
            log.warning(f"Error en loop promo reportes: {e}")

        await asyncio.sleep(60)  # Revisar cada minuto


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

        # ── Limpieza de sets que crecen sin límite ──
        if len(_published_msg_ids) > 2000:
            # Conservar solo los últimos 500 (los más recientes son los más altos)
            _sorted = sorted(_published_msg_ids)
            _published_msg_ids.clear()
            _published_msg_ids.update(_sorted[-500:])
            log.info(f"🧹 _published_msg_ids limpiado: 2000+ → 500")
        if len(_resolved_signals) > 2000:
            # No hay forma de saber cuáles son recientes, limpiar dejando vacío
            # Las señales en disco (copier_open_signals.json) ya están resueltas
            _resolved_signals.clear()
            log.info(f"🧹 _resolved_signals limpiado (>2000 entradas)")

        with _signals_lock:
            signals_copy = dict(_open_signals)

        to_resolve = []
        for sig_id, sdata in signals_copy.items():
            signal = sdata["signal"]
            direction = signal["direction"]
            sl = signal["sl"]
            pair = signal["pair"]
            age_hours = (time.time() - sdata["sent_at"]) / 3600

            # FIX 2026-04-08: Rastrear el TP MÁS ALTO de la señal, no solo TP1
            # Así celebramos el profit real, no +2 pts de un TP1 scalper
            _tp1 = signal.get("tp", 0) or 0
            _tp2 = signal.get("tp2", 0) or 0
            _tp3 = signal.get("tp3", 0) or 0
            _tp4 = signal.get("tp4", 0) or 0
            _tp5 = signal.get("tp5", 0) or 0
            _all_tps = [t for t in [_tp5, _tp4, _tp3, _tp2, _tp1] if t > 0]
            if direction == "BUY":
                tp = max(_all_tps) if _all_tps else 0  # TP más alto para BUY
            else:
                tp = min(_all_tps) if _all_tps else 0  # TP más bajo para SELL

            # Actualizar el signal con el TP final para que la celebración muestre el profit correcto
            signal["_tp_final"] = tp

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
            _save_open_signals()  # Actualizar disco

            if result in ("tp", "sl"):
                # FIX 2026-04-08: Anti-duplicado TP/SL — no enviar 2 veces
                _notif_key = f"{signal.get('pair','')}_{signal.get('direction','')}_{result}"
                _prev_notif = _recently_notified.get(_notif_key, 0)
                if _prev_notif and (time.time() - _prev_notif) < 300:  # 5 min cooldown
                    log.info(f"🔕 {result.upper()} duplicado ignorado: {_notif_key} (notificado hace {time.time()-_prev_notif:.0f}s)")
                    continue
                _recently_notified[_notif_key] = time.time()
                # Cleanup entradas viejas (>30 min) para no acumular memoria
                _now_notif = time.time()
                _recently_notified.update({k: v for k, v in _recently_notified.items() if _now_notif - v < 1800})

                # REGLA: No anunciar si no hay precio de entrada válido
                _entry = signal.get('entry', 0) or 0
                if _entry <= 0:
                    log.info(f"🔕 TP/SL silenciado ({result.upper()} {signal.get('pair','?')}): entrada desconocida")
                    continue

                if result == "tp":
                    log.info(f"🎯 Llamando _send_tp_celebration para {signal.get('pair','?')} entry={_entry}")
                    _send_tp_celebration(signal, reply_to_msg_id=_reply_id)
                else:
                    log.info(f"🛑 Llamando _send_sl_notification para {signal.get('pair','?')} entry={_entry}")
                    _send_sl_notification(signal, reply_to_msg_id=_reply_id)

                # Registrar resultado para reportes diarios del grupo
                _record_daily_result(signal, result)


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

    # ── EXTRAER TP1, TP2, TP3 ──
    # Formatos: "TP1: 4513" | "TP: 4513" | "Tp 4540" | "🥇 TP 45530" | "Toma de Ganancias 1 : 4513"
    # | "Take profit 4480" | "Take profit : 4480" (FxPremiere format)
    # TP2: "TP2: 4520" | "TP 2: 4520" | "TAKE PROFIT 2: 4520" | "Toma de Ganancias 2: 4520"
    # TP3: "TP3: 4545" | "TP 3: 4545" | "TAKE PROFIT 3: 4545" | "Toma de Ganancias 3: 4545"
    # Ignora líneas con "TP: abierto" / "TP: ABIERTO" / "TP: OPEN" (sin número fijo)
    # FIX 2026-04-07: También filtrar "OPEN" en inglés
    _upper_clean_no_abierto = re.sub(r'TP\s*[:\s]*(?:ABIERTO|OPEN)\b', '', upper_clean)
    tp_match = re.search(
        r'(?:TOMA\s*DE\s*GANANCIAS\s*1\s*[:\s]+|TAKE\s*PROFIT\s*1\s*[:\s]+|TP\s*1\s*[:\s]+|TP\s*[:\s]+|TP\s+|TAKE\s*PROFIT\s*[:\s]+)(\d+\.?\d*)',
        _upper_clean_no_abierto
    )
    # Fallback: "Tp 4540" o "TP 1.9150" — \d{1,6} cubre forex (1.XXXX) y gold (4XXX)
    if not tp_match:
        tp_match = re.search(r'\bTP\s+(\d{1,6}\.?\d+)', _upper_clean_no_abierto)
    # Fallback AnabelSignals: "TP4430" o "TP1.9150" (sin espacio entre TP y número)
    if not tp_match:
        tp_match = re.search(r'\bTP(\d{1,6}\.?\d+)', _upper_clean_no_abierto)
    # ── TP2-TP5: extraer por número explícito ──
    def _extract_tp_n(n, txt):
        """Extract TPn from text using multiple patterns."""
        # Patrón 1: "TP 2: 4626" | "TP2: 4626" | "TAKE PROFIT 2: 4626"
        m = re.search(
            rf'(?:TOMA\s*DE\s*GANANCIAS\s*{n}\s*[:\s]+|TAKE\s*PROFIT\s*{n}\s*[:\s]+|TP\s*{n}\s*[:\s]+|TP{n}\s*[:\s]*)(\d+\.?\d*)',
            txt
        )
        if m: return m
        # Patrón 2: "TP 2 4626" (espacio sin :)
        m = re.search(rf'\bTP\s*{n}\s+(\d{{1,6}}\.?\d+)', txt)
        if m: return m
        # Patrón 3: "TP2 4626" pegado
        m = re.search(rf'\bTP{n}(\d{{1,6}}\.?\d+)', txt)
        return m

    tp2_match = _extract_tp_n(2, _upper_clean_no_abierto)
    tp3_match = _extract_tp_n(3, _upper_clean_no_abierto)
    tp4_match = _extract_tp_n(4, _upper_clean_no_abierto)
    tp5_match = _extract_tp_n(5, _upper_clean_no_abierto)

    # ── Fallback: múltiples líneas "TP 4690 / TP 4700 / TP 4710" (FxPremiere, AnabelSignals) ──
    # También cubre "TP: 4604 / TP: 4606 / TP: 4608" (AnabelSignals con : repetido)
    # Captura TODOS los "TP[:]? <número>" y asigna en orden: [0]=TP1, [1]=TP2, [2]=TP3, [3]=TP4, [4]=TP5
    _all_tp_nums = re.findall(r'(?:TP|TAKE\s*PROFIT)\s*[:\s]*(\d{1,6}\.?\d+)', _upper_clean_no_abierto)
    # Eliminar duplicados manteniendo orden (TP1 ya capturado arriba puede repetirse)
    _seen_tp = set()
    _unique_tp = []
    for _t in _all_tp_nums:
        if _t not in _seen_tp:
            _seen_tp.add(_t)
            _unique_tp.append(_t)
    # Asignar fallback solo si no se capturó por número explícito
    if len(_unique_tp) >= 2 and not tp2_match:
        tp2_match = re.match(r'(\d+\.?\d*)', _unique_tp[1])
    if len(_unique_tp) >= 3 and not tp3_match:
        tp3_match = re.match(r'(\d+\.?\d*)', _unique_tp[2])
    if len(_unique_tp) >= 4 and not tp4_match:
        tp4_match = re.match(r'(\d+\.?\d*)', _unique_tp[3])
    if len(_unique_tp) >= 5 and not tp5_match:
        tp5_match = re.match(r'(\d+\.?\d*)', _unique_tp[4])

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
        tp2   = float(tp2_match.group(1)) if tp2_match else 0.0
        tp3   = float(tp3_match.group(1)) if tp3_match else 0.0
        tp4   = float(tp4_match.group(1)) if tp4_match else 0.0
        tp5   = float(tp5_match.group(1)) if tp5_match else 0.0
        entry = float(entry_match.group(1)) if entry_match else 0.0
    except (ValueError, IndexError):
        return None

    # Protección: TP de dígito único (ej "1") es artefacto del parser — "TP1: 4608" captura "1" si regex falla
    # Ningún par real tiene TP < 5 como número entero. Forex mínimo: 1.0500 (tiene decimales); Gold: 4000+
    _tp_vars = {"tp": tp, "tp2": tp2, "tp3": tp3, "tp4": tp4, "tp5": tp5}
    for _tp_attr, _tp_val in _tp_vars.items():
        if 0 < _tp_val < 5 and _tp_val == float(int(_tp_val)):
            log.warning(f"⚠️ Parser: {_tp_attr}={_tp_val} es artefacto numérico (< 5, entero) — descartado")
            if _tp_attr == "tp":   tp  = 0.0
            if _tp_attr == "tp2":  tp2 = 0.0
            if _tp_attr == "tp3":  tp3 = 0.0
            if _tp_attr == "tp4":  tp4 = 0.0
            if _tp_attr == "tp5":  tp5 = 0.0

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

    # FIX 2026-04-07: Validar TP2-TP5 dirección y rango razonable
    # Si un TP está en la dirección contraria o es absurdamente lejano, descartarlo (no la señal entera)
    if entry > 0:
        for _tpn, _tpv in [("tp2", tp2), ("tp3", tp3), ("tp4", tp4), ("tp5", tp5)]:
            if _tpv <= 0:
                continue
            _wrong_dir = False
            if direction == "BUY" and _tpv < entry and abs(_tpv - entry) > 0.001:
                _wrong_dir = True
            elif direction == "SELL" and _tpv > entry and abs(_tpv - entry) > 0.001:
                _wrong_dir = True
            # Rango: TP no debería estar a más de 20% del entry (descarta "200.00" para XAUUSD a 4650)
            _pct_diff = abs(_tpv - entry) / entry if entry > 0 else 0
            _out_of_range = _pct_diff > 0.20
            if _wrong_dir or _out_of_range:
                _reason = "wrong direction" if _wrong_dir else f"out of range ({_pct_diff:.0%})"
                log.warning(f"⚠️ Parser: {_tpn}={_tpv} inválido ({_reason}) para {direction} entry={entry} — descartado")
                if _tpn == "tp2": tp2 = 0.0
                if _tpn == "tp3": tp3 = 0.0
                if _tpn == "tp4": tp4 = 0.0
                if _tpn == "tp5": tp5 = 0.0

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
        "tp":         tp,    # TP1
        "tp2":        tp2,   # TP2 — 0 si el canal no lo envía (el bot lo proyecta)
        "tp3":        tp3,   # TP3 — 0 si el canal no lo envía (el bot lo proyecta)
        "tp4":        tp4,   # TP4 — GOLD FOREX MARKET / AnabelSignals
        "tp5":        tp5,   # TP5 — GOLD FOREX MARKET
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
    entry = signal["entry"]
    # If entry was 0 (not in message), use current market price (sin mutar el dict original)
    if entry == 0 or entry == 0.0:
        entry = price

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

        prompt = f"""You are a professional trading analyst. Evaluate this signal in 1 line (max 80 characters). Reply in English.

Signal: {_dir} {_pair} @ {_entry}
SL: {_sl} | TP: {_tp} | R:R: {rr}

Reply ONLY with a short analysis line. Example:
- "Strong uptrend, good entry"
- "RSI overbought, high risk"
- "Support zone, solid setup"

Do NOT say approve or reject. Only the analysis."""

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
        # Notificar actualizaciones importantes al canal VIP
        _action = signal.get("action", "")
        _pair = signal.get("pair", "")
        _pair_d = _get_display_pair(_pair)
        # FIX 2026-04-06: Labels en inglés — canal profesional
        _action_labels = {
            "close_half":       f"⚡ *CLOSE HALF* — {_pair_d}",
            "close_partial":    f"⚡ *PARTIAL CLOSE* — {_pair_d}",
            "full_close":       f"🔒 *FULL CLOSE* — {_pair_d}",
            "move_sl_to_entry": f"🛡️ *MOVE SL TO ENTRY* — {_pair_d}",
            "sl_hit":           f"🛑 *SL HIT* — {_pair_d}",
            "tp_hit":           f"✅ *TP HIT* — {_pair_d}",
        }
        _msg = _action_labels.get(_action)
        if _msg:
            # FIX 2026-03-31: Solo publicar actualización si tenemos señal abierta para ese par
            # Evita reenviar "CERRAR MITAD — GBP/AUD" cuando BuySell365 nunca abrió esa operación
            _reply_id = None
            _tenemos_senal = False
            with _signals_lock:
                for _sid, _sdata in _open_signals.items():
                    _s = _sdata.get("signal", {})
                    if _s.get("pair") == _pair or _s.get("mt5_symbol") == _pair:
                        _reply_id = _sdata.get("telegram_msg_id")
                        _tenemos_senal = True
                        break
            if not _tenemos_senal:
                log.info(f"🔕 Update '{_action}' {_pair_d} ignorado — BuySell365 no tiene señal abierta para ese par")
                return None
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

    # FIX 2026-04-06: Mantener idioma original BUY/SELL — no traducir a COMPRA/VENTA
    dir_label = direction.upper()  # "BUY" o "SELL"
    dir_emoji = "🟢" if direction == "BUY" else "🔴"
    src_emoji = {
        "SureShotFX":      "📡",
        "Learn2Trade":     "📊",
        "FXPremiere":      "🔔",
        "GoldForexMarket": "🥇",
    }.get(source, "🔔")

    pair_display = _get_display_pair(pair)

    # Tipo de orden en inglés
    tipo_label = {"Market": "Market", "Limit": "Limit Order", "Stop": "Stop Order"}.get(order_type, order_type)

    # Formato de precios
    fmt = fmt_price

    entry_display = fmt(entry) if entry > 0 else "Market Price"

    tp2 = signal.get("tp2", 0) or 0
    tp3 = signal.get("tp3", 0) or 0
    tp4 = signal.get("tp4", 0) or 0
    tp5 = signal.get("tp5", 0) or 0
    has_multi_tp = any(t > 0 for t in [tp2, tp3, tp4, tp5])
    tp_label = "TP1" if has_multi_tp else "TP"

    lines = [
        f"{dir_emoji} *{dir_label} — {pair_display}*",
        f"",
        f"📍 Entry: {entry_display}",
    ]
    # FIX 2026-04-08: No mostrar "TP: Open" — si no hay TP válido, omitir línea
    if tp > 0:
        lines.append(f"🎯 {tp_label}: {fmt(tp)}")
    if tp2 > 0:
        lines.append(f"🎯 TP2: {fmt(tp2)}")
    if tp3 > 0:
        lines.append(f"🎯 TP3: {fmt(tp3)}")
    if tp4 > 0:
        lines.append(f"🎯 TP4: {fmt(tp4)}")
    if tp5 > 0:
        lines.append(f"🎯 TP5: {fmt(tp5)}")
    lines.append(f"🛡️ SL: {fmt(sl)}")

    # FIX 2026-04-07: Comentario IA removido por solicitud del usuario
    # ia_comment = signal.get("ia_comment", "")
    # if ia_comment:
    #     lines.append(f"")
    #     lines.append(f"🤖 _{ia_comment}_")

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

    # FIX 2026-04-08: Gráfica SOLO en TP/SL HIT, NO en señales nuevas
    # Las señales nuevas solo llevan texto + botones XM

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
                log.info(f"📡 ENVIADO AL CANAL: {dir_label} {pair_display} ({source})")
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
                    _save_open_signals()  # Persistir a disco

                    # ── OPCIÓN A: Registrar señal en operaciones_activas del bot ──
                    # El bot monitorea precio y anuncia TP/SL igual que sus propias señales
                    try:
                        import json as _json_bot
                        from datetime import datetime as _dt_bot
                        from pathlib import Path as _Path_bot
                        _estado_path = _Path_bot(__file__).parent / "estado.json"
                        _yf_map_bot = {
                            "GOLD": "GC=F", "XAUUSD": "GC=F",  # FIX 2026-04-07: XAUUSD=X delisted
                            "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X",
                            "USDJPY": "USDJPY=X", "GBPJPY": "GBPJPY=X",
                            "AUDCAD": "AUDCAD=X", "USDCAD": "USDCAD=X",
                            "EURCHF": "EURCHF=X", "GBPAUD": "GBPAUD=X",
                            "EURJPY": "EURJPY=X", "NZDUSD": "NZDUSD=X",
                            "AUDUSD": "AUDUSD=X", "GBPNZD": "GBPNZD=X",
                            "AUDNZD": "AUDNZD=X", "GBPCAD": "GBPCAD=X",
                            "EURCAD": "EURCAD=X", "USDCHF": "USDCHF=X",
                            "NZDJPY": "NZDJPY=X", "CADJPY": "CADJPY=X",
                            "GBPCHF": "GBPCHF=X", "EURGBP": "EURGBP=X",
                            "NAS100": "NQ=F", "US100Cash": "NQ=F",
                            "US500Cash": "ES=F", "US30Cash": "YM=F",
                            "USOIL": "CL=F", "USOILCash": "CL=F",
                            "BTCUSD": "BTC-USD", "BTCUSDm": "BTC-USD",
                        }
                        _nombre_map_bot = {
                            "GOLD": "GOLD", "XAUUSD": "GOLD",
                            "NAS100": "NASDAQ", "US100Cash": "NASDAQ",
                            "US500Cash": "S&P500", "US30Cash": "DOW30",
                            "USOIL": "USOIL", "USOILCash": "USOIL",
                            "BTCUSD": "BTC/USD", "BTCUSDm": "BTC/USD",
                            "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD",
                            "USDJPY": "USD/JPY", "GBPJPY": "GBP/JPY",
                            "AUDNZD": "AUD/NZD", "NZDJPY": "NZD/JPY",
                            "EURCAD": "EUR/CAD", "GBPCAD": "GBP/CAD",
                            "USDCHF": "USD/CHF", "CADJPY": "CAD/JPY",
                        }
                        _yf_tk = _yf_map_bot.get(pair)
                        if not _yf_tk:
                            _yf_tk = f"{pair}=X" if (len(pair) == 6 and pair.isalpha()) else pair
                        _nombre_bot = _nombre_map_bot.get(pair, pair_display)
                        _tipo_bot = "COMPRA" if direction == "BUY" else "VENTA"
                        _tp1_bot = signal.get("tp", 0) or signal.get("tp1", 0)
                        _tp2_bot = signal.get("tp2", 0) or 0
                        _tp3_bot = signal.get("tp3", 0) or 0
                        # Proyectar TP2/TP3 si no vienen en la señal
                        if _tp1_bot > 0 and entry > 0:
                            _dist = abs(_tp1_bot - entry)
                            if _tp2_bot <= 0:
                                _tp2_bot = round(_tp1_bot + _dist, 5) if _tipo_bot == "COMPRA" else round(_tp1_bot - _dist, 5)
                            if _tp3_bot <= 0:
                                _tp3_bot = round(_tp1_bot + 2 * _dist, 5) if _tipo_bot == "COMPRA" else round(_tp1_bot - 2 * _dist, 5)
                        _op_id_bot = f"{_yf_tk}_{sig_id}"
                        with open(_estado_path, "r", encoding="utf-8") as _fbot:
                            _est_bot = _json_bot.load(_fbot)
                        # No agregar si ya hay op abierta para este par (anti-duplicado)
                        _base_bot = _yf_tk.replace("=X", "").replace("=F", "").upper()
                        _ya_hay = any(
                            v.get("ticker", "").replace("=X", "").replace("=F", "").upper() == _base_bot
                            for v in _est_bot.get("operaciones_activas", {}).values()
                        )
                        if not _ya_hay and _tp1_bot > 0:
                            _est_bot.setdefault("operaciones_activas", {})[_op_id_bot] = {
                                "ticker": _yf_tk, "nombre": _nombre_bot, "tipo": _tipo_bot,
                                "entrada": entry, "tp1": _tp1_bot, "tp2": _tp2_bot, "tp3": _tp3_bot,
                                "sl": sl, "score": 3, "timestamp": time.time(),
                                "hora": _dt_bot.now().strftime("%H:%M"),
                                "tp1_hit": False, "tp2_hit": False,
                                "aviso_sl_enviado": False, "trailing_activo": False,
                                "confianza_multi_ia": 0, "confianza": 0, "confianza_score_100": 0,
                                "estrategia": "signal_copier", "mt5_ejecutado": False,
                                "ticket_mt5": None, "skip_mt5_razon": "Señal copiada de canal externo",
                                "premium": False, "nivel_senal": "COPIADA", "riesgo_usado": 0,
                                "telegram_msg_id": _canal_msg_id or 0,
                                "fuente": source, "_reservado": False,
                            }
                            # Escritura atómica: tmp + replace para evitar corrupción si bot.py escribe al mismo tiempo
                            _tmp_estado = str(_estado_path) + ".tmp"
                            with open(_tmp_estado, "w", encoding="utf-8") as _fbot:
                                _json_bot.dump(_est_bot, _fbot, ensure_ascii=False, indent=2)
                            os.replace(_tmp_estado, _estado_path)
                            log.info(f"🔗 Bot registrará TP/SL: {_nombre_bot} {_tipo_bot} entrada={entry} TP={_tp1_bot} SL={sl}")
                        else:
                            log.info(f"🔗 No registrado en bot: ya hay op abierta para {_base_bot}")
                    except Exception as _e_bot:
                        log.warning(f"⚠️ No se pudo registrar en operaciones_activas: {_e_bot}")

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
    log.error(f"📡 FALLO TOTAL: no se pudo enviar señal {dir_label} {pair_display} tras 3 intentos")
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
    # FIX 2026-04-08: Solo GOLD y NASDAQ — bloquear US30, forex, crypto, etc.
    # Lista global de pares permitidos (aplica a TODOS los canales)
    ALLOWED_PAIRS = {"GOLD", "XAUUSD", "XAUUSD=X", "ORO",
                     "NAS100", "NASDAQ", "NASDAQ100", "US100", "US100Cash", "NQ"}

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

            # FIX 2026-04-08: Filtro GLOBAL — solo GOLD y NASDAQ
            if signal and signal["type"] == "new_signal":
                _sig_pair = (signal.get("pair") or "").upper()
                _sig_mt5 = (signal.get("mt5_symbol") or "").upper()
                if _sig_pair not in ALLOWED_PAIRS and _sig_mt5 not in ALLOWED_PAIRS:
                    log.info(f"⏭️ Señal ignorada ({_sig_pair}) — solo permitidos: GOLD, NASDAQ")
                    return
            # Updates (close_half, sl_hit, tp_hit) se filtran más abajo por _open_signals
            if not signal:
                return

            log.info(f"📡 SEÑAL DETECTADA en [{chat.title}]: {signal.get('direction', signal.get('action', '?'))} {signal['pair']}")

            # ── Deduplicación: evitar misma señal (mismo par+dirección+precio) de CUALQUIER canal en <60 min ──
            # FIX 2026-04-07: Doble check — _open_signals Y _recently_sent (sobrevive TP/SL resolution)
            if signal["type"] == "new_signal":
                _entry_round = round(signal.get("entry", 0), 2)
                _dedup_key = f"{signal['pair']}_{signal['direction']}_{_entry_round}"
                # Check 1: _recently_sent cache (persiste incluso después de TP/SL)
                _prev_sent_time = _recently_sent.get(_dedup_key, 0)
                if _prev_sent_time and (time.time() - _prev_sent_time) < 3600:
                    log.info(f"⏭️ Señal duplicada ignorada (cache): {_dedup_key} (enviada hace {(time.time() - _prev_sent_time):.0f}s)")
                    return
                # Check 2: _open_signals (legacy)
                with _signals_lock:
                    for _sid, _sdata in _open_signals.items():
                        _s = _sdata.get("signal", {})
                        _e_round = round(_s.get("entry", 0), 2)
                        _existing_key = f"{_s.get('pair','')}_{_s.get('direction','')}_{_e_round}"
                        if _existing_key == _dedup_key and (time.time() - _sdata.get("sent_at", 0)) < 3600:
                            log.info(f"⏭️ Señal duplicada ignorada: {_dedup_key} (ya existe en últimos 60 min)")
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

                if signal.get("entry", 0) == 0:
                    # Sin precio de entrada → buscar precio actual (yfinance/TwelveData → MT5)
                    _live = _get_current_price(signal.get("pair", ""))
                    if not _live or _live <= 0:
                        # Fallback: precio MT5 directo
                        try:
                            import MetaTrader5 as _mt5
                            _mt5_sym = signal.get("mt5_symbol") or signal.get("pair", "")
                            if _mt5.initialize():
                                _tick = _mt5.symbol_info_tick(_mt5_sym)
                                if _tick:
                                    _live = (_tick.ask + _tick.bid) / 2
                        except Exception:
                            pass
                    if _live and _live > 0:
                        signal["entry"] = round(_live, 5 if _live < 100 else 2)
                        log.info(f"📍 Sin entry en señal — usando precio actual: {signal['entry']}")
                    else:
                        log.info(f"📍 Sin entry y sin precio disponible — publicando con 'Market Price'")

                # ── Validar distancia mínima de SL antes de publicar ──
                _entry_val = signal.get("entry", 0)
                _sl_val = signal.get("sl", 0)
                if _entry_val > 0 and _sl_val > 0:
                    _sl_dist = abs(_entry_val - _sl_val)
                    _pair_upper = signal.get("pair", "").upper()
                    # Mínimos: GOLD/índices = 15 pts, forex = 10 pips (0.0010)
                    if _pair_upper in ("GOLD", "XAUUSD", "XAUUSD=X", "GC=F"):
                        _min_sl = 15.0
                    elif _entry_val >= 100:  # Índices (NAS100, US30, etc.)
                        _min_sl = 15.0
                    else:  # Forex
                        _min_sl = 0.0010
                    if _sl_dist < _min_sl:
                        log.warning(f"⚠️ SL demasiado cerca: {_pair_upper} entry={_entry_val} sl={_sl_val} dist={_sl_dist:.5f} (min={_min_sl}) — señal descartada")
                        return

                # Publicar inmediatamente (con o sin precio)
                send_to_channel(signal, executed, detail)
                # FIX 2026-04-07: Registrar en cache anti-duplicados
                _entry_r = round(signal.get("entry", 0), 2)
                _dk = f"{signal['pair']}_{signal['direction']}_{_entry_r}"
                _recently_sent[_dk] = time.time()
                # Limpiar entradas viejas (>2h) para no acumular memoria
                _now = time.time()
                _recently_sent.update({k: v for k, v in _recently_sent.items() if _now - v < 7200})
                if msg_id:
                    _published_msg_ids.add(msg_id)

            elif signal["type"] == "update":
                # ── Updates de posiciones — SOLO log interno, NO publicar al canal ──
                # Usuario: solo quiere señales nuevas en el canal. Bot scanner maneja TP/SL.
                log.info(f"📡 UPDATE silenciado (solo señales): {signal.get('action','?')} {signal['pair']}")
                # MT5 execution (si se reactiva en el futuro)
                if MT5_EXECUTION_ENABLED:
                    executed, detail = handle_update_mt5(signal)
                    log.info(f"📡 UPDATE MT5: {'✅' if executed else '❌'} {detail}")
                # send_to_channel desactivado — no enviar CERRAR/TP/SL/MOVER al canal VIP

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

            fmt = fmt_price

            # Ya no hay buffer — las señales se publican de inmediato con precio actual
            # Solo queda Case B: señal nueva capturada vía edición

            # ── CASO B: mensaje NO estaba en _pending_entry ni en _published ──
            # El mensaje original no se pudo parsear (ej: SureShotFX envió sin SL/TP)
            # y la edición añadió la información completa → publicar como señal nueva
            if msg_id in _published_msg_ids:
                return  # Ya publicado, ignorar ediciones cosméticas

            signal = parse_signal(text, chat_title=chat_title)
            if not signal or signal.get("type") != "new_signal":
                return  # No es señal nueva completa — ignorar

            # ── Deduplicación: no publicar si ya existe señal abierta del mismo par+dirección ──
            _entry_round = round(signal.get("entry", 0), 2)
            _dedup_key = f"{signal['pair']}_{signal['direction']}_{_entry_round}"
            with _signals_lock:
                for _sid, _sdata in _open_signals.items():
                    _s = _sdata.get("signal", {})
                    _e_round = round(_s.get("entry", 0), 2)
                    _existing_key = f"{_s.get('pair','')}_{_s.get('direction','')}_{_e_round}"
                    if _existing_key == _dedup_key and (time.time() - _sdata.get("sent_at", 0)) < 3600:
                        log.info(f"✏️ Edit ignorado — señal ya existe: {_dedup_key}")
                        _published_msg_ids.add(msg_id)
                        return

            log.info(f"✏️ Señal capturada vía edición (msg_id={msg_id}): {signal.get('direction')} {signal.get('pair')}")
            MT5_EXECUTION_ENABLED = False
            executed, detail = False, "Ejecución MT5 desactivada (kill-switch activo)"
            send_to_channel(signal, executed, detail)
            # FIX 2026-04-07: Registrar en cache anti-duplicados
            _entry_r = round(signal.get("entry", 0), 2)
            _dk = f"{signal['pair']}_{signal['direction']}_{_entry_r}"
            _recently_sent[_dk] = time.time()
            _published_msg_ids.add(msg_id)

        except Exception as e:
            log.error(f"Error en edit_handler: {e}")

    log.info("📡 Signal Copier iniciando...")
    log.info(f"📡 API ID: {API_ID}")
    log.info(f"📡 Phone: {PHONE}")

    # Cargar señales abiertas de la sesión anterior (sobreviven reinicios)
    _load_open_signals()

    # Intentar conectar con sesión existente PRIMERO (sin enviar código SMS)
    # FIX 2026-04-06: Si la sesión no se reconoce, limpiar WAL/SHM y reintentar
    # Antes: client.start(phone=PHONE) → FloodWait → crash loop cada 2 min
    # Ahora: 1) Intentar conexión normal, 2) Si falla, limpiar journal y reintentar,
    #        3) Solo si todo falla, parar limpiamente
    await client.connect()
    if await client.is_user_authorized():
        log.info("📡 Sesión existente válida — conectado sin código SMS")
    else:
        log.warning("📡 Sesión no reconocida — limpiando WAL/SHM y reintentando...")
        await client.disconnect()
        # Los archivos WAL/SHM son journal del SQLite — pueden estar corruptos
        # El archivo .session principal tiene el auth_key real
        _sess_base = str(Path(__file__).parent / "signal_copier_session.session")
        for _ext in ["-shm", "-wal", "-journal"]:
            _jf = _sess_base + _ext
            try:
                if os.path.exists(_jf):
                    os.remove(_jf)
                    log.info(f"📡 Borrado journal corrupto: {os.path.basename(_jf)}")
            except Exception:
                pass
        # Reconectar con sesión limpia
        await client.connect()
        if await client.is_user_authorized():
            log.info("📡 ✅ Sesión recuperada después de limpiar journal — conectado")
        else:
            log.error("📡 ❌ Sesión de Telegram realmente inválida. Ejecuta AUTH_QR.bat para re-autenticar.")
            _flag = Path(__file__).parent / ".copier_needs_auth"
            _flag.write_text("Sesión Telegram inválida. Ejecutar AUTH_QR.bat para re-autenticar.")
            import asyncio as _aio_wait
            await _aio_wait.sleep(3600)
            return

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
    # Iniciar reportes promocionales (12:00 y 17:00 hora Andorra)
    asyncio.ensure_future(_loop_promo_reportes())

    log.info("📡 Signal Copier ACTIVO — escuchando todos los canales VIP...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    import sys, time as _time_lock
    _lock_file = Path(__file__).parent / ".copier.lock"
    _my_pid = os.getpid()

    # ── Verificación de instancia única usando lock file + confirmación psutil ──
    if _lock_file.exists():
        try:
            _old_pid = int(_lock_file.read_text().strip())
            if _old_pid != _my_pid:
                try:
                    import psutil as _psutil_lock
                    _old_proc = _psutil_lock.Process(_old_pid)
                    _old_cmd = ' '.join(_old_proc.cmdline())
                    _old_status = _old_proc.status()
                    if ('signal_copier' in _old_cmd
                            and _old_status not in ('zombie', 'dead', 'stopped')):
                        log.warning(f"📡 Otra instancia del copier corriendo (PID={_old_pid}). Saliendo.")
                        sys.exit(0)
                except Exception:
                    pass  # Proceso muerto o inaccesible — ignorar lock obsoleto
        except Exception:
            pass
    _lock_file.write_text(str(_my_pid))
    # FIX 2026-04-06c: Loop de reconexión — si run_until_disconnected() retorna (desconexión),
    # reintentar en vez de salir con code=0 (que causa restart loop cada 2 min).
    _max_retries = 10
    _retry_count = 0
    _retry_wait = 30  # segundos entre reintentos
    while _retry_count < _max_retries:
        try:
            asyncio.run(main())
            # main() retornó normalmente (client desconectado) — reintentar
            _retry_count += 1
            if _retry_count < _max_retries:
                log.warning(f"📡 Copier desconectado — reconectando en {_retry_wait}s (intento {_retry_count}/{_max_retries})...")
                _time_lock.sleep(_retry_wait)
                _retry_wait = min(_retry_wait * 2, 300)  # backoff hasta 5 min
            else:
                log.error(f"📡 Copier: {_max_retries} reconexiones fallidas — saliendo.")
            continue
        except KeyboardInterrupt:
            log.info("📡 Signal Copier detenido por usuario")
            break
        except Exception as e:
            _err_str = str(e)
            log.error(f"📡 Signal Copier error: {e}")
            # Respetar FloodWaitError de Telegram — esperar en vez de reintentar en loop
            import re as _re_flood
            _flood_match = _re_flood.search(r'wait of (\d+) seconds', _err_str, _re_flood.IGNORECASE)
            if _flood_match:
                _wait_secs = int(_flood_match.group(1))
                _wait_mins = _wait_secs // 60
                _wait_hrs = _wait_mins // 60
                log.warning(f"⏳ Telegram FloodWait: esperando {_wait_hrs}h {_wait_mins % 60}m antes de reintentar...")
                _time_lock.sleep(min(_wait_secs + 30, 86400))  # Esperar + 30s margen, máx 24h
                _retry_count = 0  # Reset retries después de FloodWait
                _retry_wait = 30
                continue
            else:
                _retry_count += 1
                if _retry_count < _max_retries:
                    log.warning(f"⏳ Reintentando en {_retry_wait}s (intento {_retry_count}/{_max_retries})...")
                    _time_lock.sleep(_retry_wait)
                    _retry_wait = min(_retry_wait * 2, 300)
                else:
                    log.error(f"📡 Copier: {_max_retries} errores consecutivos — saliendo.")
                    break
    try:
        _lock_file.unlink(missing_ok=True)
    except Exception:
        pass

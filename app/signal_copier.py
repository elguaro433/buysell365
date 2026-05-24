"""
BuySell365 Signal Copier — Userbot que escucha canales VIP de Telegram
Lee senales de canales aliados activos y las ejecuta en MT5 + reenvia al canal BuySell365.
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

# === FIX 2026-04-22: Capturar muertes silenciosas ===
# El copier muere cada ~3 min sin logs. Instalamos handlers que capturan:
# 1. faulthandler: segfaults y crashes de C extensions (MT5, cryptg, sqlite)
# 2. sys.excepthook: excepciones no capturadas en thread principal
# 3. threading.excepthook: excepciones en otros threads
# 4. atexit: registra la salida del proceso (sea por donde sea)
import sys, faulthandler, atexit, traceback as _tb
try:
    # faulthandler escribe al stderr en caso de segfault/fatal error
    _fault_path = _log_dir / "copier_fault.log"
    _fault_file = open(_fault_path, "a", encoding="utf-8")
    faulthandler.enable(file=_fault_file, all_threads=True)
    log.info(f"🔧 faulthandler activo → {_fault_path}")
except Exception as _e_fh:
    log.warning(f"🔧 faulthandler no se pudo activar: {_e_fh}")

def _log_unhandled(exc_type, exc_value, exc_tb):
    """Captura excepciones no capturadas en thread principal."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    log.error("💥 EXCEPCIÓN NO CAPTURADA (thread principal):")
    log.error("".join(_tb.format_exception(exc_type, exc_value, exc_tb)))
sys.excepthook = _log_unhandled

def _log_thread_exc(args):
    """Captura excepciones en otros threads (Python 3.8+)."""
    log.error(f"💥 EXCEPCIÓN EN THREAD {args.thread.name}:")
    log.error("".join(_tb.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
try:
    threading.excepthook = _log_thread_exc
except Exception:
    pass

@atexit.register
def _log_exit():
    """Loguea cuando el proceso sale (por cualquier motivo)."""
    import sys as _sys_exit
    log.warning(f"⚰️ COPIER SALIENDO — PID={os.getpid()}")
    # Flush forzado para asegurar que el último log se escriba
    for _h in log.handlers:
        try: _h.flush()
        except Exception: pass
    try: _sys_exit.stderr.flush()
    except Exception: pass

# === GLOBALS para Story (FIX 2026-04-20) ===
client = None          # TelegramClient global — se asigna en main()
_main_loop = None      # Event loop principal — para run_coroutine_threadsafe desde hilos

# === SYMBOL MAP — todos los pares de canales aliados (sin duplicados) ===
SYMBOL_MAP = {
    # Oro
    "XAUUSD": "GOLD", "GOLD": "GOLD", "ORO": "GOLD",
    # Plata (FIX 2026-05-08: XM la llama SILVER, no XAGUSD)
    "XAGUSD": "SILVER", "SILVER": "SILVER", "PLATA": "SILVER",
    "XAG/USD": "SILVER", "XAG": "SILVER",
    # Índices
    "NAS100": "US100Cash", "NASDAQ": "US100Cash", "US100": "US100Cash",
    "NASDAQ100": "US100Cash", "NQ": "US100Cash",
    "US30": "US30Cash", "DOW": "US30Cash", "DJ30": "US30Cash",
    "SPX500": "US500Cash", "SP500": "US500Cash", "US500": "US500Cash",
    "GER40": "GER40Cash", "DAX": "GER40Cash", "DE40": "GER40Cash",
    # Petróleo — XM usa "BRENTCash" y "OILCash"
    "BRENT": "BRENTCash", "UKOIL": "BRENTCash", "OIL": "BRENTCash",
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
    # Petróleo — XM usa "OILCash" (WTI) y "BRENTCash" (Brent)
    # FIX 2026-05-04 (#5): añadidos XTI/XTIUSD/CL/USCRUDE — tickers alternativos
    # de WTI Light Crude. SureShot envia a veces "XTI/USD" en lugar de "USOIL".
    # El 4-may una senal XTI/USD del gift_signal SELL no se ejecuto porque
    # XTIUSD no estaba mapeado → watchdog la cerro como huerfana sin entrar a MT5.
    "USOIL": "OILCash", "WTI": "OILCash", "CRUDEOIL": "OILCash",
    "XTI": "OILCash", "XTIUSD": "OILCash", "CL": "OILCash", "USCRUDE": "OILCash",
    "WTIUSD": "OILCash",
    # Gas natural — XM usa "NGASCash"
    "XNGUSD": "NGASCash", "NATGAS": "NGASCash", "NGAS": "NGASCash", "NG": "NGASCash",
    # Crypto (FxPremiere envía BTC/USD)
    # FIX 2026-04-12: XM usa "BTCUSD" (no "BTCUSDm")
    "BTCUSD": "BTCUSD",
}

MAGIC_COPIER = 20260325
# FIX 2026-05-11 (noche): magic del generator interno BTC/ETH. Sus posiciones
# tambien son "nuestras" — deben recibir auto-breakeven, auto-close 50%, y
# publicaciones via reconcile. Usar BS365_MAGICS para checks coherentes.
MAGIC_GENERATOR = 365001
BS365_MAGICS = (MAGIC_COPIER, MAGIC_GENERATOR)
TWELVE_KEY = os.getenv("TWELVE_DATA_KEY", "")

# ── Firma Eli — se añade al final de TODOS los mensajes del canal VIP ──
ELI_SIG = "\n\n— _Eli · BuySell365 Pro_ 🤖"

# Mapa de nombres para display — FIX 2026-04-21: ORO unificado (no XAU/GOLD/XAUUSD)
# Por petición del usuario: a partir de hoy el oro siempre se llama "ORO" en mensajes.
_DISPLAY_MAP = {
    # MT5 symbols → nombre estándar de display
    "GOLD": "ORO", "US100Cash": "NAS100", "US500Cash": "US500",
    "US30Cash": "US30", "GER40Cash": "GER40", "BRENTCash": "BRENT", "OILCash": "USOIL",
    # Aliases → nombre estándar
    "XAUUSD": "ORO", "XAU/USD": "ORO", "XAU": "ORO", "ORO": "ORO",
    "NAS100": "NAS100", "NASDAQ": "NAS100", "NASDAQ100": "NAS100", "NQ": "NAS100", "US100": "NAS100",
    "US30": "US30", "DOW": "US30", "DJ30": "US30",
    "SPX500": "US500", "SP500": "US500", "US500": "US500",
    "GER40": "GER40", "DAX": "GER40", "DE40": "GER40",
    "BRENT": "BRENT", "UKOIL": "BRENT", "OIL": "BRENT",
    # Petróleo WTI
    "USOIL": "USOIL", "WTI": "USOIL",
    # Gas natural
    "NGASCash": "NATGAS", "XNGUSD": "NATGAS", "NATGAS": "NATGAS", "NGAS": "NATGAS",
    # Crypto
    "BTCUSD": "BTC/USD",
}

def _get_display_pair(pair: str) -> str:
    """Devuelve nombre bonito del par para mensajes de Telegram."""
    if pair in _DISPLAY_MAP:
        return _DISPLAY_MAP[pair]
    elif len(pair) == 6 and pair.isalpha():
        return f"{pair[:3]}/{pair[3:]}"
    return pair


def _get_weekly_stats_block() -> str:
    """Bloque de stats semanales para celebraciones del grupo publico.

    FIX 2026-05-12 P2.9: ahora calcula DINAMICAMENTE desde copier_stats.json
    (ultimos 7 dias, excluyendo fuentes internas/legacy). Antes usaba numeros
    hardcoded en weekly_stats.json que el usuario editaba a mano cada lunes
    — se quedaban desactualizados y mentian respecto a la realidad del bot.

    Si calculo dinamico falla → fallback al JSON hardcoded.
    weekly_stats.json sigue respetando active=false para apagar el bloque.
    """
    import json
    import os as _os
    # Permitir desactivar globalmente via weekly_stats.json
    try:
        _path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "weekly_stats.json")
        if _os.path.exists(_path):
            with open(_path, "r", encoding="utf-8") as _f:
                _d_cfg = json.load(_f)
            if not _d_cfg.get("active", True):
                return ""
    except Exception:
        pass
    # Calculo dinamico desde copier_stats.json — ultimos 7 dias
    try:
        _stats_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "copier_stats.json")
        if not _os.path.exists(_stats_path):
            raise FileNotFoundError("copier_stats.json missing")
        with open(_stats_path, "r", encoding="utf-8") as _f:
            _stats = json.load(_f)
        _trades = _stats.get("trades", [])
        _now_ts = time.time()
        _seven_days_ago = _now_ts - 7 * 86400
        # Excluir mismas fuentes que el web (Historico, Unknown, Manual, MT5_Reinsert, Internal, Bot)
        _EXCL = {"Historico", "Unknown", "Manual", "MT5_Reinsert", "Internal", "Bot"}
        _WIN_RES = ("tp", "close_half", "close_partial", "full_close")
        _week = [
            t for t in _trades
            if t.get("closed_at", 0) >= _seven_days_ago
            and (t.get("source") or "").strip() not in _EXCL
        ]
        _win_n = sum(1 for t in _week if t.get("result") in _WIN_RES and float(t.get("pips", 0) or 0) > 0)
        _loss_n = sum(1 for t in _week if t.get("result") == "sl")
        _decisive = _win_n + _loss_n
        if _decisive < 5:
            # No hay suficientes datos para mostrar stats creibles
            return ""
        _wr = round(_win_n / _decisive * 100, 1)
        _pips_won = sum(float(t.get("pips", 0) or 0) for t in _week if t.get("result") in _WIN_RES and float(t.get("pips", 0) or 0) > 0)
        _pips_lost = sum(float(t.get("pips", 0) or 0) for t in _week if t.get("result") == "sl")
        _pips_net = round(_pips_won - _pips_lost, 0)
        if _pips_net <= 0:
            return ""  # no mostrar semanas perdedoras
        _total = len(_week)
        return (
            f"\n📊 *Last week's track record:*\n"
            f"✅ {_wr:.0f}% win rate\n"
            f"💰 +{int(_pips_net):,} pips locked\n"
            f"🏆 {_win_n}/{_decisive} signals were winners\n"
        )
    except Exception as _e_dyn:
        log.debug(f"weekly_stats dynamic calc failed: {_e_dyn} — fallback al JSON")
    # Fallback al JSON hardcoded
    try:
        _path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "weekly_stats.json")
        if not _os.path.exists(_path):
            return ""
        with open(_path, "r", encoding="utf-8") as _f:
            _d = json.load(_f)
        if not _d.get("active", False):
            return ""
        _wr_f = _d.get("win_rate_pct", 0)
        _pips_f = _d.get("pips_total", 0)
        _win_f = _d.get("winners", 0)
        _tot_f = _d.get("total_signals", 0)
        if not (_wr_f and _pips_f and _win_f and _tot_f):
            return ""
        return (
            f"\n📊 *Last week's track record:*\n"
            f"✅ {_wr_f}% win rate\n"
            f"💰 +{_pips_f:,} pips locked\n"
            f"🏆 {_win_f}/{_tot_f} signals were winners\n"
        )
    except Exception:
        return ""


def fmt_price(v, zero_label="—"):
    """Formato de precio: 2 decimales para valores >= 100 (ORO, índices, JPY),
    3 decimales para 10-100 (PLATA, OIL),
    forex 4-5 decimales si v < 10.
    zero_label se muestra si v <= 0.

    FIX 2026-04-21: antes hacía rstrip('0') agresivo → '1.358' (3 dec, amateur).
    FIX 2026-04-30: drop UN cero terminal si quedan ≥4 decimales → 1.17320 → 1.1732
    FIX 2026-05-08: rango 10-100 → 3 decimales (plata 79.640, oil 75.500)."""
    if v <= 0:
        return zero_label
    if v >= 100:
        # FIX 2026-05-06: strip trailing .00 (4530.00 → 4530, 4586.50 → 4586.5)
        s = f"{v:.2f}"
        if s.endswith(".00"):
            return s[:-3]
        if s.endswith("0"):
            return s[:-1]
        return s
    if v >= 10:
        # FIX 2026-05-08: plata, petróleo y otros activos en rango 10-100 → 3 decimales
        # Antes usaba 5 decimales como forex → "79.64000" → "79.6400" (mal)
        s = f"{v:.3f}"
        if s.endswith(".000"):
            return s[:-4]  # 79.000 → 79
        if s.endswith("0"):
            return s[:-1]  # 79.640 → 79.64
        return s
    s = f"{v:.5f}"
    # Drop solo el último 0 (no rstrip todos) para mantener mínimo 4 decimales.
    if s.endswith("0"):
        s = s[:-1]
    return s


# FIX 2026-04-12: Lock para yfinance — evita race condition entre señales simultáneas
_lock_yf = threading.Lock()

# === TP TRACKER ===
# _open_signals: { sig_id → {"signal": signal_dict, "sent_at": float, "telegram_msg_id": int} }
_open_signals: dict = {}
_signals_lock = threading.Lock()
_resolved_signals: set = set()  # sig_ids ya resueltos — no volver a cargar del JSON
# FIX 2026-05-05: Buffer de señales recién cerradas — se mantienen 2h para poder
# celebrar TPs que lleguen del canal aliado aunque la señal ya no esté en _open_signals.
# { pair → {"signal": signal_dict, "closed_at": float, "telegram_msg_id": int} }
_recently_closed_buffer: dict = {}
_recently_closed_lock = threading.Lock()
_resolved_signals_lock = threading.Lock()
# FIX 2026-05-01: persistir _resolved_signals a disco — sin esto, tras reinicio
# del copier una senal recien cerrada que aun este en copier_open_signals.json
# (mid-write) se cargaria de nuevo y podria recelebrarse. Persistencia en JSON
# simple con TTL 72h (alineado con _load_open_signals expiry).
_RESOLVED_SIGNALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".resolved_signals.json")


def _save_resolved_signals() -> None:
    """Escribe _resolved_signals a disco (atomico). Throttled implicitamente
    porque solo se llama tras add() critico, no en hot-path."""
    try:
        with _resolved_signals_lock:
            _now = time.time()
            # Guardar como dict {sig_id: timestamp_resolucion} para poder expirar viejos
            _data = {}
            try:
                if os.path.exists(_RESOLVED_SIGNALS_FILE):
                    with open(_RESOLVED_SIGNALS_FILE, "r", encoding="utf-8") as _f:
                        _data = json.load(_f) or {}
            except Exception:
                _data = {}
            # Agregar los nuevos con timestamp ahora
            for _sid in _resolved_signals:
                if _sid not in _data:
                    _data[_sid] = _now
            # Expirar >72h (alineado con load_open_signals)
            _cutoff = _now - (72 * 3600)
            _data = {k: v for k, v in _data.items() if v >= _cutoff}
            _tmp = _RESOLVED_SIGNALS_FILE + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as _f:
                json.dump(_data, _f, ensure_ascii=False)
            os.replace(_tmp, _RESOLVED_SIGNALS_FILE)
    except Exception as _e:
        log.debug(f"_save_resolved_signals error: {_e}")


def _load_resolved_signals() -> None:
    """Carga _resolved_signals del disco al arrancar — evita recelebrar
    senales que ya fueron resueltas antes del reinicio."""
    try:
        if not os.path.exists(_RESOLVED_SIGNALS_FILE):
            return
        with open(_RESOLVED_SIGNALS_FILE, "r", encoding="utf-8") as _f:
            _data = json.load(_f) or {}
        _now = time.time()
        _cutoff = _now - (72 * 3600)
        with _resolved_signals_lock:
            for _sid, _ts in _data.items():
                if _ts >= _cutoff:
                    _resolved_signals.add(_sid)
        log.info(f"📂 _resolved_signals cargado: {len(_resolved_signals)} sig_ids (TTL 72h)")
    except Exception as _e:
        log.warning(f"_load_resolved_signals error: {_e} — empezando vacio")
# FIX 2026-04-07: Cache anti-duplicados — persiste incluso después de TP/SL resolution
# { "PAIR_DIRECTION_ENTRY": timestamp_sent }
# FIX 2026-05-18 P1.8: lock para handlers async simultaneos (dos senales del
# mismo par llegando con <100ms gap pasaban dedup debil)
_recently_sent: dict = {}
_recently_sent_lock = threading.Lock()

# FIX 2026-05-09: cache anti-spam de logs de precio MT5.
# Antes el polling cada 30s loguea siempre — durante mercados cerrados o periodos
# de poco movimiento son cientos de líneas idénticas. Ahora solo loguea cuando
# el precio cambia (bid o ask) o cada 5 minutos como heartbeat.
# { "SYMBOL": (bid, ask, last_log_ts) }
_price_log_cache: dict = {}
# FIX 2026-04-08: Anti-duplicado para TP/SL notifications
# { "PAIR_DIRECTION_tp/sl": timestamp } — evita doble SL HIT / TP HIT
_recently_notified: dict = {}

# FIX 2026-05-06 (Capa A): persistir _recently_notified a disco — sobrevive
# reinicios del copier. Sin esto, al reiniciar se wipea el cache y el
# reconcile re-publica cierres ya publicados antes del reinicio (visto hoy
# 6 May 21:01: 3x SL ORO -74, 2x TP US100CASH +132.7).
NOTIF_DEDUP_FILE = Path(__file__).parent / ".notif_dedup.json"

# FIX 2026-05-11: TTL diferenciado.
# - keys "orphan_deal_*" deben sobrevivir tanto como la ORPHAN_WINDOW del reconcile (2h),
#   más un buffer. Antes: TTL 30min < window 2h → entre 30-120min, mismo ticket se
#   re-publicaba cada vez que reconcile corría. Caso 11-may 18:35: 5 SLs fantasma al VIP.
# - resto de keys mantiene TTL 30min (sigue siendo apropiado para celebraciones/alerts).
_DEDUP_TTL_DEFAULT = 1800   # 30 min
_DEDUP_TTL_ORPHAN  = 7800   # 2h10min (cubre _ORPHAN_WINDOW_SEC=7200 + buffer)
# FIX 2026-05-13: celebraciones (TP HIT / SL HIT) deben sobrevivir 12h.
# Caso 13-may: 3 duplicados al VIP (ETH +11.5 a 14:17 y 15:09; ORO +21.1 a
# 17:23 y 18:30; BTC -192 a 17:42 y 18:30). TTL 30min purgaba el dedup
# antes de que reconcile / monitor / orphan reproyectaran el mismo cierre.
_DEDUP_TTL_CELEBRATION = 43200  # 12 horas

def _dedup_ttl_for(k: str) -> int:
    _k = str(k)
    if _k.startswith("orphan_deal_"):
        return _DEDUP_TTL_ORPHAN
    if _k.startswith("cel_") or _k.startswith("sl_notif_"):
        return _DEDUP_TTL_CELEBRATION
    return _DEDUP_TTL_DEFAULT


# FIX 2026-05-11 (tarde-3): COOLDOWN GLOBAL POR PAR para canal VIP.
# Evita rafagas como las del 11-may 18:35 (5 SLs) y 19:33 (6 mensajes) del mismo
# par/categoria. Cada vez que un sitio del codigo intenta publicar al VIP un evento
# relativo a un par, este helper decide si pasa o no segun cooldown global por par.
# Aplica DESPUES de los dedups especificos (por accion+par) — es la ultima linea
# de defensa para que el canal mantenga ritmo armonico.
# Configurable via VIP_PUBLISH_COOLDOWN_SEC env (default 45s).
# Set a 0 para desactivar (no recomendado en produccion).
_vip_publish_pair_last: dict = {}  # {pair_uppercased: ts}

# FIX 2026-05-13: contador diario de senales por simbolo (reset cada dia Andorra).
# { "GOLD": {"date": "2026-05-13", "count": 4 } } — se persiste en disco.
DAILY_SYMBOL_COUNTER_FILE = Path(__file__).parent / ".daily_symbol_counter.json"
_daily_symbol_counter: dict = {}

def _today_andorra_str() -> str:
    try:
        import pytz
        from datetime import datetime as _dt
        return _dt.now(pytz.timezone("Europe/Andorra")).strftime("%Y-%m-%d")
    except Exception:
        from datetime import datetime as _dt
        return _dt.utcnow().strftime("%Y-%m-%d")

def _load_daily_symbol_counter():
    global _daily_symbol_counter
    try:
        if DAILY_SYMBOL_COUNTER_FILE.exists():
            with open(DAILY_SYMBOL_COUNTER_FILE, "r", encoding="utf-8") as _f:
                _daily_symbol_counter = json.load(_f) or {}
    except Exception:
        _daily_symbol_counter = {}

def _save_daily_symbol_counter():
    try:
        _tmp = str(DAILY_SYMBOL_COUNTER_FILE) + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(_daily_symbol_counter, _f)
            _f.flush()
            try: os.fsync(_f.fileno())
            except (OSError, AttributeError): pass
        os.replace(_tmp, DAILY_SYMBOL_COUNTER_FILE)
    except Exception:
        pass

def _check_daily_symbol_cap(symbol: str, cap: int) -> bool:
    """Devuelve True si la senal puede publicarse (counter < cap). Incrementa contador y guarda.
    Si counter >= cap, devuelve False sin incrementar."""
    if not symbol or cap <= 0:
        return True
    today = _today_andorra_str()
    rec = _daily_symbol_counter.get(symbol, {})
    if rec.get("date") != today:
        rec = {"date": today, "count": 0}
    if rec["count"] >= cap:
        return False
    rec["count"] += 1
    _daily_symbol_counter[symbol] = rec
    _save_daily_symbol_counter()
    return True

_load_daily_symbol_counter()

def _can_publish_to_vip(pair: str, event: str = "") -> bool:
    """True si el par puede publicarse al canal VIP ahora; False si esta en cooldown.
    Si False, NO actualiza el timestamp (puede reintentarse).
    Si True, marca el par como recien publicado y devuelve True.
    Excepciones: senales nuevas ("new_signal") siempre pasan — son el evento principal.
    """
    if not pair:
        return True
    if str(event).lower() in ("new_signal", "daily_summary", "promo", "skip_notification"):
        return True  # eventos top-level siempre pasan; el ruido viene de updates/closures
    try:
        cd = int(os.getenv("VIP_PUBLISH_COOLDOWN_SEC", "45"))
    except (ValueError, TypeError):
        cd = 45
    if cd <= 0:
        return True
    pair_n = str(pair).upper().replace("/", "")
    now = time.time()
    last = _vip_publish_pair_last.get(pair_n, 0)
    if last and (now - last) < cd:
        try:
            log.info(f"🔕 VIP cooldown global: {pair_n} {event} ignorado (ultimo evento hace {now-last:.0f}s, cd={cd}s)")
        except Exception:
            pass
        return False
    _vip_publish_pair_last[pair_n] = now
    return True


# === GUARD ÚNICO DE PUBLICACIÓN AL CANAL VIP ===
# FIX 2026-05-15: chokepoint para validar TODO mensaje saliente al canal VIP.
# Bloquea: texto vacío/muy corto. Para kind="signal": exige bloque Probabilidad
# (auto-inyecta si recibe valor, aborta si no). Normaliza precios (sin separador miles).
# Devuelve el texto sanitizado o None si debe abortarse el envío.
def _safe_publish_vip(text, kind: str = "generic", pair: str = "",
                      direction: str = "", probability=None) -> "str | None":
    """Valida y sanitiza un mensaje antes de enviarlo al canal VIP.

    kind: "signal" | "tp_hit" | "sl_hit" | "partial_close" | "update" | "generic"
    Devuelve str sanitizado, o None para abortar el envio.
    """
    try:
        if text is None or not isinstance(text, str):
            log.error(
                f"🚫 _safe_publish_vip: text invalido (type={type(text).__name__}, "
                f"kind={kind}, pair={pair}) — abortando envio al VIP"
            )
            return None
        _t = text.strip()
        if len(_t) < 10:
            log.error(
                f"🚫 _safe_publish_vip: text vacio/muy corto (len={len(_t)}, "
                f"kind={kind}, pair={pair}): {_t[:60]!r} — abortando envio al VIP"
            )
            return None
        # Normalizar precios: 4,600.00 -> 4600.00 (regla del canal VIP)
        try:
            import re as _re_norm
            _t = _re_norm.sub(
                r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b",
                lambda m: m.group(0).replace(",", ""),
                _t,
            )
        except Exception:
            pass
        # Para senales nuevas: exigir bloque Probabilidad o auto-inyectar
        if kind == "signal":
            _has_prob_block = ("Probabilidad" in _t) or ("Probability" in _t)
            if not _has_prob_block:
                _prob_val = None
                if probability is not None:
                    try:
                        _prob_val = float(probability)
                    except (TypeError, ValueError):
                        _prob_val = None
                if _prob_val is None:
                    log.warning(
                        f"🛡️ _safe_publish_vip: signal {direction} {pair} SIN bloque "
                        f"Probabilidad y sin valor para auto-inyectar — abortando "
                        f"(prevencion fallo de filtro)"
                    )
                    return None
                # Auto-inyectar bloque antes de la firma Eli
                _prob_block = f"\n\n━━━━━━━━━━━━━━━━━━\n📊 Probabilidad: {_prob_val:.0f}%"
                _eli_markers = (
                    "— _Eli · BuySell365 Pro_",
                    "— Eli · BuySell365 Pro",
                )
                _injected = False
                for _m in _eli_markers:
                    if _m in _t:
                        _t = _t.replace(_m, _prob_block + "\n\n" + _m, 1)
                        _injected = True
                        break
                if not _injected:
                    _t = _t.rstrip() + _prob_block
                log.info(
                    f"🔧 _safe_publish_vip: bloque Probabilidad auto-inyectado "
                    f"({_prob_val:.0f}%) en signal {direction} {pair}"
                )
        return _t
    except Exception as _e_guard:
        # En caso de bug en el guard, log y devuelve el texto original (no romper envio)
        try:
            log.warning(f"_safe_publish_vip guard error: {_e_guard}")
        except Exception:
            pass
        return text


def _save_notif_dedup():
    """Persist _recently_notified to disk (atomic write via .tmp + os.replace)."""
    try:
        _now = time.time()
        # TTL: 30 min estandar; 2h10min para orphan_deal_* keys
        _data = {k: v for k, v in _recently_notified.items()
                 if isinstance(v, (int, float)) and (_now - v) < _dedup_ttl_for(k)}
        _tmp = str(NOTIF_DEDUP_FILE) + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(_data, _f)
            _f.flush()  # FIX #7: durabilidad
            try: os.fsync(_f.fileno())
            except (OSError, AttributeError): pass
        os.replace(_tmp, NOTIF_DEDUP_FILE)
    except Exception as _e:
        try: log.debug(f"_save_notif_dedup error: {_e}")
        except Exception: pass

def _load_notif_dedup():
    """Carga _recently_notified desde disco al arrancar — preserva dedup tras reinicio."""
    try:
        if NOTIF_DEDUP_FILE.exists():
            with open(NOTIF_DEDUP_FILE, "r", encoding="utf-8") as _f:
                _data = json.load(_f) or {}
            _now = time.time()
            _loaded = 0
            for _k, _v in _data.items():
                if isinstance(_v, (int, float)) and (_now - _v) < _dedup_ttl_for(_k):
                    _recently_notified[_k] = _v
                    _loaded += 1
            if _loaded:
                try: log.info(f"📂 Notif dedup cargado: {_loaded} entries activos")
                except Exception: pass
    except Exception as _e:
        try: log.debug(f"_load_notif_dedup error: {_e}")
        except Exception: pass

# Cargar al import del módulo
_load_notif_dedup()

# === DAILY RESULTS TRACKER (para reportes de mediodía y tarde) ===
# Lista de dicts: {"pair": str, "direction": str, "entry": float, "tp": float, "pips_str": str, "result": "tp"|"sl", "time": float}
_daily_results: list = []
_daily_results_lock = threading.Lock()

# Archivo de señales manuales — el admin registra señales vía /rastrear en bot.py
MANUAL_SIGNALS_FILE = Path(__file__).parent / "manual_signals.json"
# Archivo de señales abiertas — sobrevive reinicios del copier
OPEN_SIGNALS_FILE = Path(__file__).parent / "copier_open_signals.json"
# FIX 2026-04-15: Archivo de estadísticas persistentes — sobrevive reinicios
COPIER_STATS_FILE = Path(__file__).parent / "copier_stats.json"
# FIX 2026-05-18 P1.10: lockfile inter-procesos para que launcher.py GUI no lea
# copier_stats.json a media escritura (cifras raras en dashboard).
COPIER_STATS_LOCK = Path(__file__).parent / "copier_stats.lock"


class _StatsFileLock:
    """Lock cross-platform basado en archivo (msvcrt Windows / fcntl Linux).
    Best-effort: si la libreria no esta disponible, no bloquea pero no falla.
    Uso: `with _StatsFileLock(): ...`
    """
    def __init__(self, path: Path = COPIER_STATS_LOCK, timeout_s: float = 5.0):
        self.path = path
        self.timeout_s = timeout_s
        self._fh = None

    def __enter__(self):
        try:
            self._fh = open(self.path, "a+b")
            _deadline = time.time() + self.timeout_s
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.time() > _deadline:
                            break
                        time.sleep(0.05)
            else:
                import fcntl
                while True:
                    try:
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.time() > _deadline:
                            break
                        time.sleep(0.05)
        except Exception:
            self._fh = None
        return self

    def __exit__(self, *exc):
        try:
            if self._fh:
                if os.name == "nt":
                    try:
                        import msvcrt
                        self._fh.seek(0)
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                else:
                    try:
                        import fcntl
                        fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                self._fh.close()
        except Exception:
            pass
# FIX 2026-04-25: Persistencia de estado diario (reportes, IG promo) — sobrevive reinicios
COPIER_SENT_STATE_FILE = Path(__file__).parent / "copier_sent_state.json"
# Flag para enviar resumen diario solo una vez
_daily_summary_sent: str = ""  # fecha "DD/MM/YYYY" del último resumen enviado
_daily_publisher_sent: str = ""  # fecha "DD/MM/YYYY" del último publisher (19:00 Andorra → Canal VIP + Grupo + IG Story + Highlight)
_daily_eli_sent: str = ""        # fecha "DD/MM/YYYY" de la última presentación Eli (13:10 Andorra → solo Grupo)
_transparency_sent: str = ""  # fecha "DD/MM/YYYY" del último anuncio transparency (20:15 Andorra → Canal VIP)

# === AUTO-CLOSE AL 50% DE GANANCIAS (INTERNO — sin mensaje al canal) ===
# Set de tickets MT5 ya cerrados por esta regla (para no cerrar dos veces).
_auto_half_closed_tickets: set = set()
AUTO_HALF_CLOSE_PCT = 0.50  # Porcentaje del recorrido entry→TP para cerrar (50%)

# FIX 2026-05-16: cache de tickets a los que el orphan-fix ya intento aplicar SLTP
# y fallo. Sin esto el reconcile reintenta cada 3min y spammea el log (caso 16-may
# noche: GBPUSD 769104074 retcode=10025 90+ veces durante el weekend porque el
# broker XM rechaza TRADE_ACTION_SLTP en forex con mercado cerrado).
_orphan_fix_fail_cache: dict = {}  # {ticket: last_fail_ts}
ORPHAN_FIX_RETRY_BACKOFF_SEC = 3600  # 1 hora antes de reintentar

# FIX 2026-05-11 (tarde): AUTO-BREAKEVEN — fase previa al auto-close.
# Si profit alcanza X% del distance to TP, mover SL a entry (breakeven).
# Asi trades que pasan por profit pero no llegan al 50% (umbral del auto-close)
# tampoco pueden terminar en perdida. Caso real 11-may: SELL ORO entry 4664 TP 4640
# fue a +30 pips, retrocedio y toco SL -80. Con BE activo, habria cerrado en 0.
# Activar con AUTO_BREAKEVEN_ENABLED=true en .env (default OFF para rollback rapido).
# FIX 2026-05-11 (tarde-2): bajado de 0.30 a 0.20 — caso US30 19:33 el aliado
# senalo "63 pips profit" en posicion que distancia 303 pips al TP (= 20.8% del camino).
# Con umbral 0.30 nunca disparaba; con 0.20 si protege antes del retraceo.
# Parametrizable via env AUTO_BREAKEVEN_PCT (default 0.20 = 20% del camino).
_auto_breakeven_done_tickets: set = set()
try:
    AUTO_BREAKEVEN_PCT = float(os.getenv("AUTO_BREAKEVEN_PCT", "0.20"))
    if AUTO_BREAKEVEN_PCT <= 0 or AUTO_BREAKEVEN_PCT >= 1:
        AUTO_BREAKEVEN_PCT = 0.20
except (ValueError, TypeError):
    AUTO_BREAKEVEN_PCT = 0.20

# === SEÑALES REGALO AL GRUPO PÚBLICO (2/día: 1 oro + 1 otra) ===
# FIX 2026-04-17: Persistir a disco — antes se perdía en cada reinicio y se
# regalaban múltiples oros el mismo día (hoy se regalaron 4 en vez de 2).
GIFT_TRACKER_FILE = Path(__file__).parent / "gift_tracker.json"
_gift_tracker = {
    "date": "",            # "YYYY-MM-DD" — se resetea al cambiar el día
    # FIX 2026-05-08: aleatoriedad real — al inicio de cada día se elijen 2
    # minutos random del día (uno mañana 8-12h, uno tarde 13-18h). El próximo
    # gift dispara cuando current_minute >= gift_targets[gifts_count]. Garantiza
    # 2 gifts/día a horas distintas cada día (no más "0 regalos hoy" como antes).
    "gift_targets": [],    # [minute_morning, minute_afternoon] — random per day
    "gifts_count": 0,      # cuántos gifts ya enviados hoy (0, 1 o 2)
    # KEEP — usados por el tracker de resultados (gift_history)
    "gold_gifted": False,
    "other_gifted": False,
    "gold_pair": None,
    "other_pair": None,
    "gold_result": None,
    "other_result": None,
}
_gift_lock = threading.Lock()


def _save_gift_tracker() -> None:
    """Persiste _gift_tracker a disco. Llamar tras mutar gold_gifted/other_gifted."""
    try:
        tmp = str(GIFT_TRACKER_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_gift_tracker, f, ensure_ascii=False)
        os.replace(tmp, GIFT_TRACKER_FILE)
    except Exception as e:
        log.warning(f"Error guardando gift_tracker: {e}")


def _load_gift_tracker() -> None:
    """Carga _gift_tracker del disco al arrancar. Reset automático si cambió día."""
    try:
        if not GIFT_TRACKER_FILE.exists():
            return
        with open(GIFT_TRACKER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        from datetime import datetime
        import pytz
        today = datetime.now(pytz.timezone("Europe/Andorra")).strftime("%Y-%m-%d")
        if data.get("date") == today:
            _gift_tracker.update(data)
            log.info(f"🎁 Gift tracker restaurado: oro={_gift_tracker['gold_gifted']} otra={_gift_tracker['other_gifted']}")
        else:
            log.info(f"🎁 Gift tracker: día cambió ({data.get('date')}→{today}), reset")
    except Exception as e:
        log.warning(f"Error cargando gift_tracker: {e}")


# ── GIFT HISTORY (FIX 2026-05-04) ────────────────────────────
# Tracker persistente entre dias para resumen semanal de viernes 17:00.
# Cada gift signal enviada al grupo se anota aqui. Cuando cierra (TP/SL/close)
# se actualiza el resultado. Sobrevive a reinicios y reset diario.
GIFT_HISTORY_FILE = Path(__file__).parent / "gift_history.json"
_gift_history_lock = threading.Lock()
_GOLD_ALIASES = {"GOLD", "XAUUSD", "XAU", "XAUUSD=X", "ORO"}


def _load_gift_history() -> list:
    try:
        if not GIFT_HISTORY_FILE.exists():
            return []
        with open(GIFT_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("entries", []) if isinstance(data, dict) else []
    except Exception:
        return []


def _save_gift_history(entries: list) -> None:
    try:
        tmp = str(GIFT_HISTORY_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"entries": entries}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, GIFT_HISTORY_FILE)
    except Exception as e:
        log.warning(f"Error guardando gift_history: {e}")


def _append_gift_history(pair: str, direction: str, source: str, entry: float = 0) -> None:
    """Llamar cuando se envia una gift signal al grupo gratis."""
    try:
        from datetime import datetime
        import pytz
        tz = pytz.timezone("Europe/Andorra")
        with _gift_history_lock:
            entries = _load_gift_history()
            entries.append({
                "date": datetime.now(tz).strftime("%Y-%m-%d"),
                "ts": time.time(),
                "pair": pair,
                "direction": direction,
                "source": source,
                "entry": float(entry or 0),
                "result": None,
                "pips": 0,
            })
            _save_gift_history(entries)
            log.info(f"🎁📝 Gift history: append {direction} {pair} from {source}")
    except Exception as e:
        log.warning(f"Error append gift_history: {e}")


def _update_gift_history_result(pair: str, result: str, pips: float = 0) -> None:
    """Llamar cuando una gift signal cierra (TP/SL/full_close).
    Actualiza la entrada mas reciente sin resultado para ese par."""
    try:
        with _gift_history_lock:
            entries = _load_gift_history()
            p_norm = (pair or "").upper()
            for e in reversed(entries):
                if e.get("result") is not None:
                    continue
                e_pair = (e.get("pair") or "").upper()
                match = (e_pair == p_norm) or (
                    e_pair in _GOLD_ALIASES and p_norm in _GOLD_ALIASES
                )
                if match:
                    e["result"] = result
                    e["pips"] = float(pips or 0)
                    e["closed_ts"] = time.time()
                    _save_gift_history(entries)
                    log.info(f"🎁📝 Gift history: update {pair} result={result} pips={pips:.0f}")
                    return
    except Exception as ex:
        log.warning(f"Error update gift_history: {ex}")


def _build_weekly_gift_summary() -> str:
    """Construye el resumen semanal de senales gratis (viernes 17:00).
    Filtra entradas de la semana actual (Lun-Vie). Retorna "" si no hay datos."""
    try:
        from datetime import datetime, timedelta
        import pytz
        tz = pytz.timezone("Europe/Andorra")
        now = datetime.now(tz)
        weekday = now.weekday()  # 0=Mon ... 4=Fri
        monday_dt = now - timedelta(days=weekday)
        monday = monday_dt.strftime("%Y-%m-%d")
        friday = now.strftime("%Y-%m-%d")

        entries = _load_gift_history()
        week = [e for e in entries
                if e.get("date", "") >= monday and e.get("date", "") <= friday]

        if not week:
            return ""

        wins = 0
        losses = 0
        opens = 0
        total_won = 0.0
        total_lost = 0.0
        per_pair = {}

        for e in week:
            pair = e.get("pair", "?")
            res = e.get("result")
            pips = float(e.get("pips") or 0)
            if res == "tp" or res == "full_close":
                wins += 1
                total_won += pips
                per_pair[pair] = per_pair.get(pair, 0) + pips
            elif res == "sl":
                losses += 1
                total_lost += pips
            else:
                opens += 1

        net = total_won - total_lost
        if wins == 0 and losses == 0:
            return ""

        lines = [
            "🎁 *FREE SIGNALS RECAP — THIS WEEK* 🎁",
            "━━━━━━━━━━━━━━━━━━━",
            "",
            f"📅 Week of {monday[5:]} → {friday[5:]}",
            "",
            f"✓ *{wins} winning FREE signals*",
            f"📈 *+{net:.0f} pips* total in profit",
            "",
        ]
        if per_pair:
            top = sorted(per_pair.items(), key=lambda kv: -kv[1])[:3]
            lines.append("*Top FREE signals this week:*")
            em = ["🥇", "🥈", "🥉"]
            for i, (p, v) in enumerate(top):
                lines.append(f"  {em[i]} {p}: +{v:.0f} pips")
            lines.append("")
        lines.extend([
            "━━━━━━━━━━━━━━━━━━━",
            "🎁 *These were our FREE picks*",
            "💎 *VIPs got 8-12 winners EVERY DAY*",
            "",
            "🔥 *Imagine this kind of week — every week*",
            "👑 *Want every signal in real time?*",
            "",
            "👇 *Join the VIP Channel below*",
        ])
        return "\n".join(lines)
    except Exception as e:
        log.warning(f"Error build_weekly_gift_summary: {e}")
        return ""


# === INSTAGRAM RATE LIMITER + CIRCUIT BREAKER (FIX 2026-04-17) ===
# Instagram bloqueó 3 posts hoy por feedback_required (spam flag).
# Política: cooldown entre posts + circuit breaker tras bloqueo.
IG_STATE_FILE = Path(__file__).parent / "ig_rate_state.json"
_IG_COOLDOWNS = {
    "tp_post": 900,     # 15 min entre TPs (antes sin límite)
    "close_post": 1200, # 20 min entre cierres
    "promo_post": 3600, # 1h entre promos
}
_IG_CB_DURATION = 7200  # 2h pausa tras feedback_required


def _load_ig_state() -> dict:
    try:
        if IG_STATE_FILE.exists():
            with open(IG_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"last_posts": {}, "cb_until": 0}


def _save_ig_state(state: dict) -> None:
    try:
        tmp = str(IG_STATE_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, IG_STATE_FILE)
    except Exception as e:
        log.debug(f"Error guardando ig_state: {e}")


def _check_ig_rate_limit(post_type: str) -> bool:
    """True si se puede publicar, False si hay que esperar. Aplica cooldown + CB."""
    state = _load_ig_state()
    now = time.time()
    # Circuit breaker global
    cb_until = state.get("cb_until", 0)
    if cb_until > now:
        mins = (cb_until - now) / 60
        log.debug(f"IG circuit breaker activo ({mins:.0f} min restantes)")
        return False
    # Cooldown por tipo
    last = state.get("last_posts", {}).get(post_type, 0)
    cooldown = _IG_COOLDOWNS.get(post_type, 600)
    elapsed = now - last
    if elapsed < cooldown:
        log.debug(f"IG {post_type} cooldown ({(cooldown-elapsed):.0f}s restantes)")
        return False
    return True


def _mark_ig_post_sent(post_type: str) -> None:
    """Registra timestamp del post para el cooldown."""
    state = _load_ig_state()
    state.setdefault("last_posts", {})[post_type] = time.time()
    _save_ig_state(state)


def _trigger_ig_circuit_breaker() -> None:
    """Activa pausa de 2h para TODOS los posts IG tras feedback_required."""
    state = _load_ig_state()
    state["cb_until"] = time.time() + _IG_CB_DURATION
    _save_ig_state(state)


def _ig_in_circuit_breaker() -> bool:
    """True si el circuit breaker IG esta activo (pausa post-feedback_required).
    Fix 2026-05-10: funcion faltaba — habia 2 callers (lineas 4836, 8785) que
    causaban NameError "name '_ig_in_circuit_breaker' is not defined".
    Visto en log final-recap 22:00:39 ayer."""
    try:
        state = _load_ig_state()
        cb_until = float(state.get("cb_until", 0) or 0)
        return cb_until > time.time()
    except Exception:
        return False  # ante duda, NO bloquear posts


def _get_pips_info(pair: str, entry: float, exit_price: float) -> tuple:
    """Calcula pips y unidad según tipo de activo. Retorna (pips_numeric, pips_unit).
    FIX 2026-04-17: Petróleo (BRENT/OIL) caía en el default forex (×10000). Con
    entry=80 y diff=0.50 retornaba 5000 "pips" en vez de 50 pts. Añadido case."""
    # FIX 2026-04-27: GOLD usa "pips" con factor x10 (1 pip = 0.1) — convencion
    # de los aliados (SureShot, AnabelSignals). Antes mostraba
    # "17.3 pts" cuando los aliados publicaban "+173 pips" del mismo movimiento.
    pips_raw = abs(exit_price - entry) if entry > 0 and exit_price > 0 else 0
    _p_up = pair.upper()
    if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
        return round(pips_raw * 10, 1), "pips"
    elif any(x in _p_up for x in ("BRENT", "OIL", "WTI", "USOIL", "UKOIL")):
        # Petróleo: pip = 0.01 → ×100 para contar céntimos como "pts"
        return round(pips_raw * 100, 1), "pts"
    elif any(x in _p_up for x in ("BTC", "ETH", "BITCOIN")):
        # Crypto: puntos directos (precio alto)
        return round(pips_raw, 1), "pts"
    elif "JPY" in _p_up:
        return round(pips_raw * 100, 1), "pips"
    elif entry >= 100:  # Índices (precio ≥100)
        return round(pips_raw, 1), "pts"
    else:  # Forex
        return round(pips_raw * 10000, 1), "pips"


def _sync_copier_stats_to_web() -> None:
    """FIX 2026-04-23: Envía copier_stats.json al servidor web (Render).
    FIX 2026-05-08: aplica normalize_pips + is_excluded + consolidación multi-TP
    antes de enviar (mismo pipeline que sync_web_now.py — VERDAD SIEMPRE).
    Antes el auto-sync mandaba raw, así la web volvía a verse "rara" tras cada
    cierre y solo se arreglaba con sync manual. Ahora cada cierre publica datos
    limpios automáticamente.
    Nonblocking — si falla no detiene el copier.
    """
    try:
        _web_url = os.getenv("WEB_URL", "").strip()
        _sync_secret = os.getenv("SYNC_SECRET", "").strip()
        if not _web_url or not _sync_secret:
            return  # sin URL o secret no hacemos nada
        if not COPIER_STATS_FILE.exists():
            return
        with open(COPIER_STATS_FILE, "r", encoding="utf-8") as f:
            _stats = json.load(f)
        _trades_raw = _stats.get("trades", []) if isinstance(_stats, dict) else []

        # === Normalize + exclude (idem sync_web_now.py) ===
        try:
            from stats_normalizer import normalize_pips as _norm_p, is_excluded as _is_excl
        except Exception as _e_imp:
            log.debug(f"stats_normalizer no disponible, enviando raw: {_e_imp}")
            _norm_p = None
            _is_excl = None

        _trades_clean = []
        for _t in _trades_raw:
            if _is_excl is not None and _is_excl(_t):
                continue
            if _norm_p is not None:
                _t2 = dict(_t)
                _p_norm, _cat = _norm_p(_t)
                _t2["pips_numeric"] = _p_norm
                _t2["pips"] = _p_norm
                _t2["category"] = _cat
                _trades_clean.append(_t2)
            else:
                _trades_clean.append(_t)

        # === Consolidar multi-TPs por (pair, source, opened_at, direction) ===
        _consolidated = {}
        _standalone = []
        for _t in _trades_clean:
            _r = _t.get("result", "")
            if _r in ("close_half", "close_partial", "full_close"):
                _standalone.append(_t)
                continue
            if _r not in ("tp", "sl"):
                _standalone.append(_t)
                continue
            _key = (_t.get("pair", ""), _t.get("source", ""),
                    _t.get("opened_at", 0), _t.get("direction", ""))
            if not all(k not in (0, "") for k in _key[:3]):
                _standalone.append(_t)
                continue
            _p = float(_t.get("pips_numeric", _t.get("pips", 0)) or 0)
            _prev = _consolidated.get(_key)
            if _prev is None:
                _tt = dict(_t)
                _tt["_pips_total"] = _p if _r == "tp" else -_p
                _tt["_tp_levels_hit"] = 1 if _r == "tp" else 0
                _tt["_has_sl"] = (_r == "sl")
                _tt["_has_tp"] = (_r == "tp")
                _tt["_latest_close"] = _t.get("closed_at", 0)
                _consolidated[_key] = _tt
            else:
                if _r == "tp":
                    _prev["_pips_total"] += _p
                    _prev["_tp_levels_hit"] += 1
                    _prev["_has_tp"] = True
                else:
                    _prev["_pips_total"] -= _p
                    _prev["_has_sl"] = True
                if _t.get("closed_at", 0) > _prev.get("_latest_close", 0):
                    _prev["_latest_close"] = _t.get("closed_at", 0)
                    _prev["fecha"] = _t.get("fecha", _prev.get("fecha", ""))
                    _prev["closed_at"] = _t.get("closed_at", _prev.get("closed_at", 0))

        _final = []
        for _k, _t in _consolidated.items():
            _net = _t["_pips_total"]
            if _t["_has_tp"] and not _t["_has_sl"]:
                _t["result"] = "tp"
            elif _t["_has_sl"] and not _t["_has_tp"]:
                _t["result"] = "sl"
            elif _net > 0:
                _t["result"] = "tp"
            else:
                _t["result"] = "sl"
            _t["pips"] = abs(_net) if _t["result"] == "sl" else _net
            _t["pips_numeric"] = _t["pips"]
            if _t.get("_tp_levels_hit", 0) > 1:
                _t["multi_tp_count"] = _t["_tp_levels_hit"]
            for _kk in list(_t.keys()):
                if _kk.startswith("_"):
                    del _t[_kk]
            _final.append(_t)

        _payload = sorted(_final + _standalone, key=lambda x: x.get("closed_at", 0))

        # FIX 2026-05-11 (noche): el usuario quiere ver cada TP hit como 1 win en la web.
        # Antes enviabamos lista consolidada (multi-TPs colapsados en 1 entry) → server
        # contaba wins por longitud y subreportaba el WR visible al suscriptor.
        # Ahora ENVIAMOS LA LISTA RAW (cada TP1, TP2, TP3 como evento separado), lo que
        # hace que el WR de la web refleje los "TP HIT" que ven en el canal VIP.
        # El _payload consolidado lo seguimos calculando por compat (campo extra).
        _payload_raw = sorted(
            [_t for _t in _trades_clean if _t.get("result") in ("tp", "sl", "close_half", "close_partial", "full_close")],
            key=lambda x: x.get("closed_at", 0),
        )

        import requests as _rq
        _rq.post(
            f"{_web_url.rstrip('/')}/api/sync",
            headers={"X-Sync-Secret": _sync_secret, "Content-Type": "application/json"},
            json={
                "copier_trades": _payload_raw,           # lista raw — cada TP individual
                "copier_trades_consolidated": _payload,  # consolidada (alternativa)
            },
            timeout=8,
        )
        log.debug(f"Sync auto → web: {len(_trades_raw)} raw → {len(_payload_raw)} eventos / {len(_payload)} consolidados")
    except Exception as _e_sync:
        # No logueamos como warning porque si Render está caído lo haría cada trade
        log.debug(f"Sync copier_stats → web falló (no crítico): {_e_sync}")


def _save_copier_stats(trade: dict) -> None:
    """Persiste un trade cerrado a copier_stats.json. Limita a 90 días.
    FIX 2026-04-19: write atómico con tmp+os.replace.
    Antes: json.dump directo → si crash a media escritura (frecuente con watchdog activo),
    el dashboard del launcher leía un JSON corrupto y mostraba el copier en 0.
    FIX 2026-04-23: al final, sincroniza copier_stats.json con la web (Render).
    FIX 2026-05-18 P1.10: lock inter-procesos para serializar contra launcher.py
    leyendo el archivo mientras escribimos.
    """
    try:
        with _StatsFileLock():
            data = {"trades": []}
            if COPIER_STATS_FILE.exists():
                try:
                    with open(COPIER_STATS_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except json.JSONDecodeError:
                    # Si el archivo está corrupto, intentar el .bak
                    _bak = str(COPIER_STATS_FILE) + ".bak"
                    if os.path.exists(_bak):
                        try:
                            with open(_bak, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            log.warning(f"copier_stats principal corrupto — recuperado del .bak")
                        except Exception:
                            data = {"trades": []}

            data["trades"].append(trade)

            # Cleanup: mantener solo últimos 90 días
            cutoff = time.time() - (90 * 86400)
            data["trades"] = [t for t in data["trades"] if t.get("closed_at", 0) > cutoff]

            # Backup rotativo
            try:
                if COPIER_STATS_FILE.exists():
                    import shutil as _sh
                    _sh.copy2(str(COPIER_STATS_FILE), str(COPIER_STATS_FILE) + ".bak")
            except Exception:
                pass
            # Atomic write
            # FIX 2026-05-06 (#7): añadir flush+fsync antes de replace para garantizar
            # durabilidad ante corte de luz / crash del SO. Sin fsync el contenido
            # puede quedar en buffer del OS y perderse si el sistema cae mid-write.
            _tmp = str(COPIER_STATS_FILE) + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                try: os.fsync(f.fileno())
                except (OSError, AttributeError): pass
            os.replace(_tmp, str(COPIER_STATS_FILE))
        # FIX 2026-04-23: push a la web (Render) para que la landing lo muestre
        # (fuera del lock para no retener el lock durante HTTP)
        try:
            _sync_copier_stats_to_web()
        except Exception:
            pass
    except Exception as e:
        log.warning(f"Error guardando copier_stats: {e}")


def _load_copier_stats_today() -> list:
    """Carga trades de HOY desde copier_stats.json para restaurar _daily_results."""
    try:
        if not COPIER_STATS_FILE.exists():
            return []
        with open(COPIER_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        from datetime import datetime
        import pytz
        tz = pytz.timezone("Europe/Andorra")
        hoy = datetime.now(tz).strftime("%d/%m/%Y")
        return [t for t in data.get("trades", []) if t.get("fecha") == hoy]
    except Exception as e:
        log.warning(f"Error cargando copier_stats: {e}")
        return []


def _record_close_result(pair: str, action: str, pips: float, direction: str = "", source: str = "",
                         entry: float = 0, opened_at: float = 0) -> None:
    """Registra un cierre parcial/total en copier_stats.json."""
    pair_d = _get_display_pair(pair)
    # FIX 2026-05-04: pips ya viene en unidades correctas (extraído del canal aliado
    # como "+93 PIPS" → 93, o calculado con *10 en el bloque de llamada).
    # El FIX 2026-05-01 hacía *10 aquí causando doble multiplicación: 93 → 930.
    # FIX 2026-05-04 (#2): GUARD DEFENSIVO contra regresion de doble x10.
    # El 04-may publicamos recap inflado +6315 (real +2472) por 3 close_half ORO
    # con pips 1700/930/1640 cuando deberian ser 170/93/164. Si vuelve a pasar
    # esa regresion, este guard lo detecta y avisa al admin EN VEZ de guardar
    # el valor inflado silenciosamente. Umbral 800 cubre escenarios extremos
    # legitimos (raro ver close_half > 800 pips en ORO, equivalente a $80
    # de movimiento parcial).
    _is_gold = pair in ("GOLD", "XAUUSD", "XAUUSD=X")
    _is_partial = action in ("close_half", "close_partial", "full_close")
    if _is_gold and _is_partial and pips > 800:
        log.error(
            f"🚨 [STATS-GUARD] ORO {action} con pips={pips} sospechoso — "
            f"posible regresion de doble x10 (umbral 800 = $80 de movimiento). "
            f"Saved as is, pero AUDITAR copier_stats.json antes del recap diario. "
            f"source={source} direction={direction}"
        )
        # Notificar a admin inmediatamente si está disponible
        try:
            import os as _os, requests as _req
            _admin_id = _os.getenv("USER_ID_1", "").strip()
            _bot_tok = _os.getenv("TELEGRAM_TOKEN", "").strip()
            if _admin_id and _bot_tok:
                _req.post(
                    f"https://api.telegram.org/bot{_bot_tok}/sendMessage",
                    json={
                        "chat_id": _admin_id,
                        "text": (
                            f"🚨 *STATS GUARD* — valor sospechoso ORO {action}\n\n"
                            f"pips={pips} (umbral 800)\n"
                            f"source={source}\n"
                            f"Posible regresion de doble x10. "
                            f"Auditar `copier_stats.json` antes del recap 19:00."
                        ),
                        "parse_mode": "Markdown",
                    },
                    timeout=8,
                )
        except Exception:
            pass
    if _is_gold:
        pips_numeric = round(pips, 1)
        pips_unit = "pips"
    elif pips > 50:
        pips_numeric = round(pips, 1)
        pips_unit = "pts"
    else:
        pips_numeric = round(pips, 1)
        pips_unit = "pips"

    from datetime import datetime
    import pytz
    tz = pytz.timezone("Europe/Andorra")

    trade = {
        "sig_id": f"{pair}_{action}_{int(time.time())}",
        "pair": pair,
        "pair_display": pair_d,
        "direction": direction,
        "source": source,
        "entry": entry,
        "tp": 0,
        "sl": 0,
        "result": action,  # close_half | close_partial | full_close
        "pips": pips_numeric,
        # FIX 2026-04-30: añadido pips_numeric (consistente con _record_daily_result)
        # para que _build_promo_report pueda sumar partials sin KeyError.
        "pips_numeric": pips_numeric,
        "pips_unit": pips_unit,
        "opened_at": opened_at,
        "closed_at": time.time(),
        "fecha": datetime.now(tz).strftime("%d/%m/%Y"),
    }
    _save_copier_stats(trade)
    with _daily_results_lock:
        _daily_results.append(trade)
    log.info(f"📊 Stats: {action} {pair_d} +{pips_numeric} {pips_unit}")


def _startup_orphan_cleanup() -> None:
    """FIX 2026-05-04 (v2 BLINDADO): limpieza ULTRA-conservadora.

    Tras el incidente del 4-may donde se eliminaron 7 trades reales del tracker
    (que SI tenian posicion en MT5 pero no fueron detectados correctamente por
    la heuristica de matching por nombre), esta funcion ahora aplica 5 capas
    de seguridad:

    1. DESHABILITADO POR DEFECTO. Solo corre si STARTUP_ORPHAN_CLEANUP_ENABLED=true
    2. AUDIT MODE: STARTUP_ORPHAN_CLEANUP_DRYRUN=true → solo reporta, no borra
    3. BACKUP automatico antes de cualquier modificacion
    4. Verificacion DOBLE: positions activas Y history de cierres recientes
    5. Matching de simbolos PRECISO (no fuzzy/contains que confunde)
    6. Si MT5 falla, ABORT (mejor preservar que perder seguimiento)

    Nunca celebra TPs fantasma — solo limpia tracker silenciosamente.
    Si tienes dudas, mantenlo deshabilitado.
    """
    try:
        # SAFETY 1: feature flag — deshabilitado por defecto tras incidente
        _enabled = os.getenv("STARTUP_ORPHAN_CLEANUP_ENABLED", "false").lower() in ("true","1","yes")
        if not _enabled:
            log.info("🧹 Startup cleanup: DESHABILITADO (STARTUP_ORPHAN_CLEANUP_ENABLED!=true)")
            return

        # SAFETY 2: dry-run mode (solo reporta, no toca nada)
        _dryrun = os.getenv("STARTUP_ORPHAN_CLEANUP_DRYRUN", "false").lower() in ("true","1","yes")

        with _signals_lock:
            if not _open_signals:
                log.info("🧹 Startup cleanup: tracker vacio, nada que hacer")
                return
            _all_signals = list(_open_signals.items())
            log.info(f"🧹 Startup cleanup: revisando {len(_all_signals)} senales en tracker")

        # SAFETY 3: backup automatico ANTES de cualquier accion
        try:
            from datetime import datetime
            _ts_bak = datetime.now().strftime("%Y%m%d_%H%M%S")
            _bak_path = OPEN_SIGNALS_FILE.parent / f"{OPEN_SIGNALS_FILE.name}.bak_pre_startup_cleanup_{_ts_bak}"
            import shutil
            shutil.copy2(OPEN_SIGNALS_FILE, _bak_path)
            log.info(f"🧹 Backup creado: {_bak_path.name}")
        except Exception as _e_bak:
            log.warning(f"🧹 No se pudo crear backup ({_e_bak}) — ABORT por seguridad")
            return

        # SAFETY 4: verificar MT5 — si falla, ABORT
        try:
            import price_feed as _mt5_chk
            _ok_init, _ = _mt5_init_and_login()
            if not _ok_init:
                log.warning("🧹 MT5 no disponible — ABORT (preservar tracker)")
                return
            # Posiciones activas (cualquier magic, no solo MAGIC_COPIER —
            # el usuario puede tener trades manuales que el copier monitorea)
            _positions = _mt5_chk.positions_get() or []
            _open_symbols = set((p.symbol or "").upper().replace(".", "") for p in _positions)
            # History de cierres ultimos 7 dias (deals tipo OUT)
            from datetime import datetime as _dt_h, timedelta as _td_h
            _hist_from = _dt_h.now() - _td_h(days=7)
            _deals = _mt5_chk.history_deals_get(_hist_from, _dt_h.now()) or []
            _closed_symbols_recent = set()
            for _d in _deals:
                # ENTRY_OUT = cierre completo
                if hasattr(_d, "entry") and _d.entry == _mt5_chk.DEAL_ENTRY_OUT:
                    _closed_symbols_recent.add((_d.symbol or "").upper().replace(".", ""))
            log.info(f"🧹 MT5: {len(_positions)} posiciones abiertas ({_open_symbols})")
            log.info(f"🧹 MT5: {len(_closed_symbols_recent)} simbolos con cierres recientes (7d)")
        except Exception as _e_mt5_chk:
            log.warning(f"🧹 Error consultando MT5 ({_e_mt5_chk}) — ABORT")
            return

        # SAFETY 5: matching PRECISO. Comparar normalizando ambos lados.
        def _matches_open(pair: str) -> bool:
            """¿La senal tiene posicion abierta en MT5 ahora? Match exacto + alias."""
            if not pair:
                return False
            _pn = pair.upper().replace(".", "").replace("/", "")
            for _sym in _open_symbols:
                _sn = _sym.replace("/", "")
                if _pn == _sn:
                    return True
                # Aliases conocidos: GOLD == XAUUSD
                if _pn in ("GOLD", "XAUUSD") and _sn in ("GOLD", "XAUUSD"):
                    return True
            return False

        def _matches_closed(pair: str) -> bool:
            if not pair:
                return False
            _pn = pair.upper().replace(".", "").replace("/", "")
            for _sym in _closed_symbols_recent:
                _sn = _sym.replace("/", "")
                if _pn == _sn:
                    return True
                if _pn in ("GOLD", "XAUUSD") and _sn in ("GOLD", "XAUUSD"):
                    return True
            return False

        # Categorizar TODAS las senales del tracker
        _keep = []
        _candidates_remove = []
        for _sid, _sd in _all_signals:
            _s = _sd.get("signal", {})
            _pair = (_s.get("pair") or _s.get("mt5_symbol") or "").upper()
            _en_mt5 = _matches_open(_pair)
            _cerro_reciente = _matches_closed(_pair)

            if _en_mt5:
                # Posicion ACTUALMENTE abierta en MT5 → JAMAS borrar
                _keep.append((_sid, _pair, "OPEN_IN_MT5"))
            elif _cerro_reciente:
                # No esta abierta pero MT5 historia muestra cierre reciente
                # → seguramente cerro por TP/SL hace poco y el copier no se entero
                # Es seguro removerla (era valida pero ya cerro)
                _candidates_remove.append((_sid, _pair, "CLOSED_IN_MT5_HISTORY"))
            else:
                # No esta abierta NI hay history reciente → muy probable orphan
                # PERO solo si tiene >24h de antiguedad (mas conservador que antes)
                _sent = _sd.get("sent_at", 0)
                _age_h = (time.time() - _sent) / 3600 if _sent > 0 else 0
                if _age_h > 24:
                    _candidates_remove.append((_sid, _pair, f"NO_TRACE_MT5_{_age_h:.0f}h"))
                else:
                    _keep.append((_sid, _pair, f"YOUNG_{_age_h:.1f}h_KEEP"))

        log.info(f"🧹 Resultado analisis: {len(_keep)} a mantener, {len(_candidates_remove)} a remover")
        for _sid, _p, _r in _keep:
            log.info(f"🧹   KEEP {_p}: {_r}")
        for _sid, _p, _r in _candidates_remove:
            log.info(f"🧹   REMOVE {_p}: {_r}")

        # SAFETY 6: dry-run mode — no toca nada
        if _dryrun:
            log.info(f"🧹 DRY-RUN activo (STARTUP_ORPHAN_CLEANUP_DRYRUN=true). NO se eliminan.")
            return

        # Aplicar remociones
        if _candidates_remove:
            with _signals_lock:
                for _sid, _p, _r in _candidates_remove:
                    _open_signals.pop(_sid, None)
                _save_open_signals()
            log.info(f"🧹 Limpieza aplicada: {len(_candidates_remove)} senales removidas (backup en {_bak_path.name})")
        else:
            log.info("🧹 Sin remociones — todas las senales tienen posicion abierta o reciente en MT5")
    except Exception as _e:
        log.error(f"🧹 Startup cleanup error inesperado: {_e}")


def _reconcile_open_vs_mt5() -> None:
    """Reconcilia copier_open_signals.json con posiciones reales MT5.

    FIX 2026-04-23: Solo se ejecuta si MT5_EXECUTION_ENABLED=True (el bot ejecuta
    las señales en MT5 con MAGIC_COPIER). Cuando está desactivado, MT5 es del
    USUARIO (trades manuales/personales) y esos deals NO pertenecen al canal VIP:
    asociarlos a las señales publicadas genera TPs falsos (como cuando el usuario
    cierra manualmente una operación con pequeña ganancia y el bot lo anuncia como
    TAKE PROFIT aunque el precio nunca tocó el TP).

    Solo tiene sentido reconciliar cuando el bot es el que ejecuta las ordenes.

    Lógica (solo si MT5_EXECUTION_ENABLED):
    1. Para cada señal abierta → busca posición MT5 viva con MAGIC_COPIER.
    2. Si no hay posición pero sí hay deal cerrado en últimas 48h con ese magic:
       → celebra como TP (profit>0) o SL (profit<0), registra stats y limpia.
    3. Si no hay posición ni deal reciente (>2h sin match): limpia silenciosamente
       porque la señal probablemente nunca se ejecutó (rechazada por el EA).
    """
    # FIX 2026-04-23: Desactivar reconcile cuando el bot no ejecuta en MT5.
    # Los deals de MT5 del usuario (cierres manuales) NO deben afectar al
    # seguimiento de las señales del canal — son cosas distintas.
    _mt5_exec = os.getenv("COPIER_MT5_ENABLED", "True").lower() in ("true", "1", "yes")
    if not _mt5_exec:
        return

    try:
        import price_feed as mt5
        from datetime import datetime, timedelta
    except ImportError:
        return

    ok, _ = _mt5_init_and_login()
    if not ok:
        return

    with _signals_lock:
        signals_copy = dict(_open_signals)
    if not signals_copy:
        return

    # Mapa pair → mt5_symbol (reutiliza SYMBOL_MAP)
    def _resolve_mt5_sym(pair: str) -> str:
        p = (pair or "").upper().replace("/", "")
        return SYMBOL_MAP.get(p, p)

    # Obtener todas las posiciones MT5 abiertas del copier
    try:
        all_pos = mt5.positions_get() or []
    except Exception:
        all_pos = []
    mt5_open_keys = set()
    mt5_copier_positions = []
    for p in all_pos:
        # FIX 2026-05-12: incluir posiciones con MAGIC_GENERATOR (365001) ademas
        # de MAGIC_COPIER (20260325). Sin esto, las señales del btc_eth_generator
        # NO aparecen en live_tickets → el reconcile las marcaba como "nunca
        # ejecutada en MT5" a los 120min y las limpiaba aunque siguieran vivas.
        # Caso del 12-may 05:47: ETH SELL ticket 764680441 (magic 365001) limpiada
        # erroneamente, posicion quedo huerfana sin tracking del bot.
        if getattr(p, "magic", 0) not in BS365_MAGICS:
            continue
        _dir = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
        mt5_open_keys.add((p.symbol.upper(), _dir))
        mt5_copier_positions.append(p)

    # FIX 2026-04-17: Huérfana INVERSA — posición MT5 del copier sin tracker en _open_signals.
    # Puede pasar si se reinició el copier después de descartar señales >72h o si un
    # TP/SL de otro canal removió la señal del tracker sin cerrar MT5.
    # Re-registramos la posición en _open_signals con datos del propio MT5.
    tracked_keys = set()
    for _sd in signals_copy.values():
        _s = _sd.get("signal", {})
        _p = _resolve_mt5_sym(_s.get("pair", "")).upper()
        _d = _s.get("direction", "")
        tracked_keys.add((_p, _d))

    reinserted = 0
    rescued = 0  # FIX 2026-05-08: zombies condenadas cerradas al rescate
    for p in mt5_copier_positions:
        _dir = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
        key = (p.symbol.upper(), _dir)
        if key in tracked_keys:
            continue
        # Posición MT5 sin tracker
        sid = f"{p.symbol.upper()}_{int(p.time)}"
        with _signals_lock:
            if sid in _open_signals or sid in _resolved_signals:
                continue

            # FIX 2026-05-08 (A): Si la zombi está >50% del recorrido hacia SL,
            # cerrar al market en vez de monitorearla hasta SL completo. Análisis
            # histórico mostró 5 SLs y -301 pips por zombies que se monitorearon
            # pasivamente cuando ya estaban condenadas. Mejor cortar pérdida
            # parcial que esperar al SL definitivo.
            _zombie_closed = False
            try:
                _z_entry = p.price_open
                _z_sl = p.sl or 0.0
                if _z_entry > 0 and _z_sl > 0:
                    _z_tick = mt5.symbol_info_tick(p.symbol)
                    _z_cur = None
                    if _z_tick:
                        _z_cur = _z_tick.bid if _dir == "BUY" else _z_tick.ask
                    if _z_cur and _z_cur > 0:
                        # Distancia recorrida hacia SL (0 = en entry, 1.0 = ya en SL)
                        _z_total = abs(_z_sl - _z_entry)
                        if _dir == "BUY":
                            _z_done = max(0.0, _z_entry - _z_cur)
                        else:
                            _z_done = max(0.0, _z_cur - _z_entry)
                        _z_pct = (_z_done / _z_total) if _z_total > 0 else 0
                        if _z_pct >= 0.5:
                            # Zombi condenada — cerrar al market
                            _z_close_type = mt5.ORDER_TYPE_SELL if _dir == "BUY" else mt5.ORDER_TYPE_BUY
                            _z_close_price = _z_tick.bid if _dir == "BUY" else _z_tick.ask
                            _z_req = {
                                "action":    mt5.TRADE_ACTION_DEAL,
                                "position":  p.ticket,
                                "symbol":    p.symbol,
                                "volume":    p.volume,
                                "type":      _z_close_type,
                                "price":     _z_close_price,
                                "deviation": 30,
                                "magic":     MAGIC_COPIER,
                                "comment":   "ZombieRescue50",
                                "type_time":    mt5.ORDER_TIME_GTC,
                                "type_filling": mt5.ORDER_FILLING_IOC,
                            }
                            _z_res = mt5.order_send(_z_req)
                            if _z_res and _z_res.retcode == mt5.TRADE_RETCODE_DONE:
                                _z_loss = abs(_z_close_price - _z_entry)
                                log.warning(
                                    f"🪦 ZOMBIE RESCUE: {p.symbol} {_dir} ticket={p.ticket} "
                                    f"al {_z_pct*100:.0f}% del SL → cerrada al market "
                                    f"@ {_z_close_price:.5f} (pérdida {_z_loss:.2f}, evita SL completo)"
                                )
                                rescued += 1
                                _zombie_closed = True
                            else:
                                _rc = _z_res.retcode if _z_res else "None"
                                log.warning(f"🪦 ZombieRescue order_send falló retcode={_rc} para ticket={p.ticket}")
            except Exception as _e_zr:
                log.debug(f"ZombieRescue check error (no crítico): {_e_zr}")

            if _zombie_closed:
                # No re-registrar — ya está cerrada
                continue

            # Re-registrar normalmente (zombi viable, monitorear hasta TP/SL)
            # FIX 2026-05-16: marcar mt5_executed=True + mt5_entry para que
            # _verify_mt5_trade_exists() acepte estas senales como reales. Antes,
            # _verify devolvia False ("signal.mt5_executed=False") para reinsertadas
            # y abortaba envio de SL/TP al VIP cuando la posicion cerraba. Riesgo:
            # SL silencioso para las 3 huerfanas reinsertadas hoy (GBPUSD, AUDCAD, EURGBP).
            _open_signals[sid] = {
                "signal": {
                    "type": "new_signal",
                    "pair": p.symbol.upper(),
                    "mt5_symbol": p.symbol,
                    "direction": _dir,
                    "order_type": "Market",
                    "is_limit": False,
                    "entry": p.price_open,
                    "mt5_entry": p.price_open,
                    "sl": p.sl or 0.0,
                    "tp": p.tp or 0.0,
                    "tp2": 0, "tp3": 0, "tp4": 0, "tp5": 0,
                    "rrr": "",
                    "source": "MT5_Reinsert",
                    "pair_display": _get_display_pair(p.symbol.upper()),
                    "timestamp": p.time,
                    "_mt5_ticket": p.ticket,
                    "mt5_ticket": p.ticket,
                    "mt5_executed": True,
                    "_reinserted_by_reconcile": True,
                },
                "sent_at": p.time,
                "telegram_msg_id": None,
            }
            tracked_keys.add(key)
            reinserted += 1
    if reinserted or rescued:
        _save_open_signals()
        if reinserted:
            log.info(f"🔄 Reconcile: {reinserted} posición(es) MT5 sin tracker re-registrada(s) en copier")
        if rescued:
            log.info(f"🪦 Reconcile: {rescued} zombi(s) condenada(s) cerrada(s) al rescate")

    # FIX 2026-05-15: HUERFANAS sin SL/TP — re-aplicar desde tracker o cerrar
    # Caso 15-may: GBPUSD ticket 769104074 reinsertada por Reconcile con SL=0 TP=0
    # y quedo huerfana 8h sin proteccion (-32.73 EUR flotante).
    # Politica: si la posicion MT5 no tiene SL/TP:
    #   1) Buscar la senal en _open_signals y re-aplicar SL/TP desde ahi.
    #   2) Si no hay senal o no tiene SL: aplicar SL de emergencia
    #      (0.5% adverso del entry para forex/oro, 1% para BTC/ETH).
    try:
        _orphan_fixed = 0
        _emergency_sl = 0
        _now_ts_orph = time.time()
        # FIX 2026-05-16: detectar fin de semana para skip de SLTP en forex/indices.
        # El broker XM rechaza TRADE_ACTION_SLTP con retcode 10025 (NO_CHANGES) o
        # similar cuando el mercado esta cerrado. Sin guard, el reconcile spammea
        # cada 3min en el log durante 48h (caso 16-may madrugada: GBPUSD 769104074
        # rebotado 100+ veces). BTC/ETH/cripto se intentan igual (24/7).
        try:
            from datetime import datetime as _dt_orph
            _is_weekend = _dt_orph.now().weekday() >= 5  # Sab=5, Dom=6
        except Exception:
            _is_weekend = False
        for p in mt5_copier_positions:
            try:
                _has_sl = (p.sl or 0) > 0
                _has_tp = (p.tp or 0) > 0
                if _has_sl and _has_tp:
                    continue
                # FIX 2026-05-16: backoff 1h si ya fallo recientemente — corta el spam
                _last_fail_o = _orphan_fix_fail_cache.get(p.ticket, 0)
                if _last_fail_o and (_now_ts_orph - _last_fail_o) < ORPHAN_FIX_RETRY_BACKOFF_SEC:
                    continue  # silencioso — proximo reintento tras 1h
                _sym_up_o = (p.symbol or "").upper()
                _is_24_7_o = any(x in _sym_up_o for x in ("BTC", "ETH", "XBT"))
                # Skip weekend en forex/indices/oro
                if _is_weekend and not _is_24_7_o:
                    # Cachear como "fallo" para evitar log + reintento durante todo el weekend
                    _orphan_fix_fail_cache[p.ticket] = _now_ts_orph
                    continue
                # Verificar que el simbolo este tradeable ahora mismo
                try:
                    _si = mt5.symbol_info(p.symbol)
                    _trade_mode_ok = True
                    if _si is not None:
                        _tm = getattr(_si, "trade_mode", 4)
                        # SYMBOL_TRADE_MODE_DISABLED=0, CLOSEONLY=1, SHORTONLY=3 etc.
                        if _tm == 0:
                            _trade_mode_ok = False
                    if not _trade_mode_ok:
                        _orphan_fix_fail_cache[p.ticket] = _now_ts_orph
                        continue
                except Exception:
                    pass
                # Buscar la senal original
                _dir_p = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
                _sig_match = None
                with _signals_lock:
                    for _sid, _sd in _open_signals.items():
                        _s = _sd.get("signal", {})
                        if (_s.get("_mt5_ticket") or _s.get("mt5_ticket")) == p.ticket:
                            _sig_match = _s
                            break
                        if (str(_s.get("mt5_symbol", "")).upper() == p.symbol.upper()
                                and str(_s.get("direction", "")).upper() == _dir_p):
                            _sig_match = _s
                            break
                _target_sl = float(_sig_match.get("sl", 0) or 0) if _sig_match else 0.0
                _target_tp = float(_sig_match.get("tp", 0) or 0) if _sig_match else 0.0
                # SL de emergencia si seguimos sin valor
                if _target_sl <= 0 and p.price_open > 0:
                    if _is_24_7_o:
                        _pct = 0.01
                    elif "JPY" in _sym_up_o:
                        _pct = 0.005
                    elif _sym_up_o in ("US30CASH", "US100CASH", "US500CASH", "GERMANY40CASH"):
                        _pct = 0.005
                    elif _sym_up_o.startswith("XAU") or _sym_up_o == "GOLD":
                        _pct = 0.005
                    else:
                        _pct = 0.005
                    if _dir_p == "BUY":
                        _target_sl = p.price_open * (1 - _pct)
                    else:
                        _target_sl = p.price_open * (1 + _pct)
                    _emergency_sl += 1
                # Aplicar via MT5
                if _target_sl > 0 or _target_tp > 0:
                    _req_mod = {
                        "action":   mt5.TRADE_ACTION_SLTP,
                        "position": p.ticket,
                        "symbol":   p.symbol,
                        "sl":       float(_target_sl) if _target_sl > 0 else (p.sl or 0.0),
                        "tp":       float(_target_tp) if _target_tp > 0 else (p.tp or 0.0),
                    }
                    _res_mod = mt5.order_send(_req_mod)
                    if _res_mod and _res_mod.retcode == mt5.TRADE_RETCODE_DONE:
                        _orphan_fixed += 1
                        _orphan_fix_fail_cache.pop(p.ticket, None)
                        log.warning(
                            f"🛡️ Reconcile ORPHAN FIX: {p.symbol} ticket={p.ticket} "
                            f"SL={_target_sl:.5f} TP={_target_tp:.5f} aplicado (antes sl={p.sl} tp={p.tp})"
                        )
                    else:
                        _rc = _res_mod.retcode if _res_mod else "None"
                        # FIX 2026-05-16: marcar para backoff 1h en lugar de spammear
                        _orphan_fix_fail_cache[p.ticket] = _now_ts_orph
                        log.warning(
                            f"🛡️ Reconcile ORPHAN FIX FAIL: {p.symbol} ticket={p.ticket} "
                            f"retcode={_rc} — reintento en {ORPHAN_FIX_RETRY_BACKOFF_SEC//60}min"
                        )
            except Exception as _e_orph:
                log.debug(f"Orphan fix iter error: {_e_orph}")
        if _orphan_fixed:
            log.info(
                f"🛡️ Reconcile: {_orphan_fixed} posicion(es) huerfana(s) protegida(s) "
                f"({_emergency_sl} con SL de emergencia)"
            )
        # Limpieza periodica del cache de fallos (max 1000 entries)
        if len(_orphan_fix_fail_cache) > 1000:
            _cutoff_o = _now_ts_orph - ORPHAN_FIX_RETRY_BACKOFF_SEC * 2
            for _tk in [_t for _t, _v in _orphan_fix_fail_cache.items() if _v < _cutoff_o]:
                _orphan_fix_fail_cache.pop(_tk, None)
    except Exception as _e_orph_blk:
        log.debug(f"Reconcile orphan SL/TP block error: {_e_orph_blk}")

    # FIX 2026-05-15: ORDENES PENDING viejas (>OLD_PENDING_HOURS) — cancelar + notificar
    # Caso 15-may: sell limit ORO ticket 768488195 colgada >21h desde 14 May 21:09
    # sin ejecutarse ni expirar. Politica: cancelar pending de MAGIC_COPIER/GENERATOR
    # con edad > umbral configurable (default 12h) y notificar al VIP como "expired".
    try:
        _old_h = float(os.getenv("PENDING_ORDER_MAX_HOURS", "12"))
    except Exception:
        _old_h = 12.0
    try:
        _all_pending = mt5.orders_get() or []
    except Exception:
        _all_pending = []
    _cancelled = 0
    _now_pend = time.time()
    for _o in _all_pending:
        try:
            if getattr(_o, "magic", 0) not in BS365_MAGICS:
                continue
            _age_h_pend = (_now_pend - getattr(_o, "time_setup", _now_pend)) / 3600
            if _age_h_pend < _old_h:
                continue
            _req_cancel = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order":  _o.ticket,
            }
            _res_cancel = mt5.order_send(_req_cancel)
            if _res_cancel and _res_cancel.retcode == mt5.TRADE_RETCODE_DONE:
                _cancelled += 1
                log.warning(
                    f"⏰ Reconcile EXPIRED PENDING: {_o.symbol} ticket={_o.ticket} "
                    f"edad={_age_h_pend:.1f}h > {_old_h}h — cancelada"
                )
                # Notificar al VIP via _send_expired_notification si existe la senal
                try:
                    with _signals_lock:
                        _matched_sid = None
                        for _sid_e, _sd_e in _open_signals.items():
                            _s_e = _sd_e.get("signal", {})
                            if (_s_e.get("_mt5_ticket") or _s_e.get("mt5_ticket")) == _o.ticket:
                                _matched_sid = _sid_e
                                break
                        if _matched_sid:
                            _sig_e = _open_signals.get(_matched_sid, {}).get("signal", {})
                            _reply_id_e = _open_signals.get(_matched_sid, {}).get("telegram_msg_id")
                            _send_expired_notification(_sig_e, reason="pending_too_old", reply_to_msg_id=_reply_id_e)
                            _open_signals.pop(_matched_sid, None)
                            _save_open_signals()
                except Exception as _e_notify_e:
                    log.debug(f"Pending expired notify error: {_e_notify_e}")
            else:
                _rc_c = _res_cancel.retcode if _res_cancel else "None"
                log.warning(
                    f"⏰ Reconcile PENDING cancel FAIL: {_o.symbol} ticket={_o.ticket} retcode={_rc_c}"
                )
        except Exception as _e_pi:
            log.debug(f"Pending iter error: {_e_pi}")
    if _cancelled:
        log.info(f"⏰ Reconcile: {_cancelled} pending(s) viejas canceladas (>{_old_h}h)")

    # Buscar deals cerrados últimas 48h con MAGIC_COPIER
    try:
        since = datetime.now() - timedelta(hours=48)
        deals = mt5.history_deals_get(since, datetime.now()) or []
    except Exception:
        deals = []
    # FIX: agrupar deals por (symbol, dir) quedándose con el último cierre por ticket
    # Un cierre MT5 genera 2 deals (entry + exit). Solo nos interesa el deal de salida (entry==OUT)
    closed_by_pair = {}  # (symbol, dir) → deal (exit)
    try:
        DEAL_ENTRY_IN = mt5.DEAL_ENTRY_IN
        DEAL_ENTRY_OUT = mt5.DEAL_ENTRY_OUT
    except Exception:
        DEAL_ENTRY_IN = 0
        DEAL_ENTRY_OUT = 1
    # FIX 2026-04-27: MT5 no preserva el `magic` en cierres TP server-side
    # (cuando el broker cierra la posicion al tocar el TP del request, el deal
    # OUT viene con magic=0, reason=1). Antes filtrabamos por magic y
    # perdiamos esos cierres → 14 de 18 trades de un dia quedaron como
    # "fantasmas" (state limpiaba la senal, updates close_half/tp_hit
    # ignorados, dashboard 0W/0L con +$15 reales en MT5). Ahora recolectamos
    # primero los position_id de los IN con MAGIC_COPIER y aceptamos cualquier
    # OUT ligado a esos position_id, sin importar el magic.
    # FIX 2026-05-12: aceptar tambien MAGIC_GENERATOR (365001) para detectar
    # cierres de posiciones del btc_eth_generator (mismo bug que mt5_copier_positions).
    copier_position_ids = {
        d.position_id
        for d in deals
        if getattr(d, "entry", 0) == DEAL_ENTRY_IN
        and getattr(d, "magic", 0) in BS365_MAGICS
    }
    for d in deals:
        if getattr(d, "entry", 0) != DEAL_ENTRY_OUT:
            continue  # solo deals de salida
        if d.position_id not in copier_position_ids:
            continue  # OUT que no pertenece a ninguna posicion abierta por el copier
        # dir de la POSICIÓN original (opuesta al deal de cierre)
        _pos_dir = "SELL" if d.type == mt5.ORDER_TYPE_BUY else "BUY"
        key = (d.symbol.upper(), _pos_dir)
        prev = closed_by_pair.get(key)
        if not prev or d.time > prev.time:
            closed_by_pair[key] = d

    to_remove = []  # [(sig_id, sdata, deal_or_None, reason)]
    now = time.time()

    # Fix 2026-05-10: tambien indexar por ticket para multi-positions per (pair, dir).
    # Antes la lógica usaba solo (mt5_sym, direction) como clave — fallaba cuando
    # habia 2+ senales del mismo par+dir simultaneas (caso comun con BTC/ETH del
    # generator). Resultado: zombies en tracker porque "alguna" posicion del par+dir
    # estaba viva, ergo todas se marcaban sincronizadas falsamente.
    live_tickets = {p.ticket for p in mt5_copier_positions}
    # Mapa ticket → deal cerrado (mejor que (sym, dir) cuando tenemos ticket)
    closed_by_ticket = {}
    for d in deals:
        if getattr(d, "entry", 0) != DEAL_ENTRY_OUT:
            continue
        if d.position_id not in copier_position_ids:
            continue
        prev = closed_by_ticket.get(d.position_id)
        if not prev or d.time > prev.time:
            closed_by_ticket[d.position_id] = d

    for sig_id, sdata in signals_copy.items():
        sig = sdata.get("signal", {})
        pair = sig.get("pair", "")
        direction = sig.get("direction", "")
        sent_at = sdata.get("sent_at", now)
        age_min = (now - sent_at) / 60
        my_ticket = sig.get("mt5_ticket") or sig.get("_mt5_ticket")

        mt5_sym = _resolve_mt5_sym(pair).upper()

        # Caso A: tenemos ticket especifico → usar matching exacto.
        # Si NO tenemos ticket → fallback al matching legacy por (sym, dir).
        if my_ticket:
            # Caso A1 con ticket: posicion especifica viva → sincronizada
            if my_ticket in live_tickets:
                continue
            # Caso B1 con ticket: buscar deal cerrado de ESTE ticket especifico
            deal = closed_by_ticket.get(my_ticket)
            if deal and deal.time > sent_at - 60:
                to_remove.append((sig_id, sdata, deal, "closed_mt5"))
                continue
        else:
            # Sin ticket — fallback legacy
            if (mt5_sym, direction) in mt5_open_keys:
                continue
            deal = closed_by_pair.get((mt5_sym, direction))
            if deal and deal.time > sent_at - 60:
                to_remove.append((sig_id, sdata, deal, "closed_mt5"))
                continue

        # Caso C: señal con entry=0 sin match aún (monitor todavía le asignará precio)
        entry = sig.get("entry", 0) or 0
        if entry <= 0 and age_min < 30:
            continue

        # Caso D: sin match y >2h desde envío → nunca se ejecutó, limpiar
        # FIX 2026-04-26: NO limpiar señales esperando apertura de mercado.
        # Se reintentaran via _retry_pending_market_open_signals cuando abra.
        if age_min > 120:
            if sdata.get("_pending_market_open", False):
                continue  # mantener para reintento al abrir
            # FIX 2026-05-12 (defensa en profundidad): si la senal afirma haberse
            # ejecutado en MT5 (mt5_ticket presente, mt5_executed True), verificar
            # UNA vez mas con MT5 directo antes de marcar como never_executed.
            # Caso del 12-may 05:47: ETH SELL ticket 764680441 del generator (magic
            # 365001) no entraba en live_tickets por un bug de filtro de magic ya
            # corregido. Este guard previene perdidas futuras por bugs similares.
            if my_ticket and sig.get("mt5_executed", False):
                try:
                    _check = mt5.positions_get(ticket=int(my_ticket))
                    if _check and len(_check) > 0:
                        log.warning(
                            f"⚠️ Reconcile: {pair_d} {direction} ticket={my_ticket} "
                            f"NO estaba en live_tickets pero MT5 directo SI lo ve "
                            f"(age={age_min:.0f}min). NO limpio — bug de filtro? Investigar."
                        )
                        continue  # NO eliminar — señal vive en MT5
                except Exception as _e_chk:
                    log.warning(
                        f"⚠️ Reconcile: {pair_d} ticket={my_ticket} chequeo MT5 fallo "
                        f"({_e_chk}). Conservador: NO limpio (puede ser timeout)."
                    )
                    continue
            to_remove.append((sig_id, sdata, None, "never_executed"))

    if not to_remove:
        return

    for sig_id, sdata, deal, reason in to_remove:
        sig = sdata.get("signal", {})
        pair = sig.get("pair", "?")
        direction = sig.get("direction", "?")
        pair_d = _get_display_pair(pair)
        _reply_id = sdata.get("telegram_msg_id")

        with _signals_lock:
            _open_signals.pop(sig_id, None)
        _resolved_signals.add(sig_id)

        if reason == "closed_mt5" and deal is not None:
            # FIX 2026-05-11 (tarde-3): si el deal de cierre tiene magic != MAGIC_COPIER,
            # vino de un cierre manual del usuario (o de otro bot/EA externo). NO publicar
            # al canal VIP — Phase 1 tiene el mismo riesgo que Phase 2.
            # Limpia la senal del tracker (ya esta resuelta) pero sin notificacion.
            _deal_magic_p1 = getattr(deal, "magic", 0) or 0
            if _deal_magic_p1 not in BS365_MAGICS:
                log.info(
                    f"🔕 Reconcile Phase 1 SKIP publish: {pair_d} {direction} "
                    f"deal magic={_deal_magic_p1} (cierre manual/externo, tracker limpiado sin notificar)"
                )
                _save_open_signals()
                continue

            profit = getattr(deal, "profit", 0.0) or 0.0
            exit_price = getattr(deal, "price", 0.0) or 0.0
            entry = sig.get("entry", 0) or 0
            # Calcular pips con precio real de salida
            pips_num, pips_unit = _get_pips_info(pair, entry, exit_price)
            result = "tp" if profit > 0 else "sl"

            # FIX 2026-04-24: Sanity adicional — el SIGNO del profit debe coincidir
            # con la dirección × diff de precio. Si NO coincide, el deal MT5 que
            # encontramos pertenece a otra posición (deal fantasma). Esto cubre el
            # caso del incidente del 23/04: SELL @49277 con exit=49370 debería ser
            # PÉRDIDA (~-$93 con lote 0.1), pero MT5 reportó profit=+$1.71. Eso
            # indica que el deal NO corresponde a esta señal aunque coincida magic.
            if entry > 0 and exit_price > 0 and direction in ("BUY", "SELL"):
                _expected_positive = (
                    (direction == "SELL" and exit_price < entry) or
                    (direction == "BUY" and exit_price > entry)
                )
                _actual_positive = profit > 0
                if _expected_positive != _actual_positive and abs(exit_price - entry) > 0.01:
                    log.warning(
                        f"🚫 Reconcile skip: {pair_d} {direction} entry={entry} exit={exit_price} "
                        f"profit=${profit:.2f} — signo profit no coincide con dirección × precio, "
                        f"deal fantasma de otra posición"
                    )
                    _save_open_signals()
                    continue

            # FIX 2026-04-23: Sanity — el precio de salida debe haber TOCADO
            # realmente el TP/SL. Si se cerró a mitad de camino (cierre manual
            # o parcial), NO publicar como TP/SL completo. Esto evita falsos
            # "TAKE PROFIT" cuando hay deals huérfanos con el magic del copier
            # que no corresponden a la señal actual.
            _sig_tp = sig.get("tp", 0) or 0
            _sig_sl = sig.get("sl", 0) or 0
            _tol_pct = 0.0015  # 0.15% de tolerancia (broker spread + slippage)
            _touched_tp = False
            _touched_sl = False
            if _sig_tp > 0 and exit_price > 0:
                if direction == "BUY":
                    _touched_tp = exit_price >= _sig_tp * (1 - _tol_pct)
                else:  # SELL
                    _touched_tp = exit_price <= _sig_tp * (1 + _tol_pct)
            if _sig_sl > 0 and exit_price > 0:
                if direction == "BUY":
                    _touched_sl = exit_price <= _sig_sl * (1 + _tol_pct)
                else:  # SELL
                    _touched_sl = exit_price >= _sig_sl * (1 - _tol_pct)
            if result == "tp" and not _touched_tp:
                log.warning(
                    f"🚫 Reconcile skip: {pair_d} {direction} exit={exit_price} NO tocó TP={_sig_tp} "
                    f"(profit=${profit:.2f}, pips={pips_num:.1f}) — cierre parcial/manual, no es TP real"
                )
                _save_open_signals()
                continue
            # FIX 2026-04-24: Quitar el filtro `abs(profit) < 5.0` para SL — un SL
            # no tocado es un SL no tocado, da igual la magnitud de la pérdida (un
            # cierre manual con pérdida grande tampoco es un SL real).
            # FIX 2026-05-21 (Bug B): NO skip silent. Si MT5 cerro la posicion con
            # nuestro magic pero no toco el SL publicado, casi siempre fue auto-BE
            # (SL movido a entry y cerro ahi). El cliente VIP necesita saber que
            # la posicion cerro. Re-clasificar como MANAGED CLOSE y notificar.
            # Caso 21-may: 5 cierres MT5 entre 16:54-20:16 sin aviso al VIP
            # (BUY ORO 16:11 -89, BUY ORO 16:45 -87, NAS100 15:32 -24, US30 16:22 BE,
            # GBPUSD 02:10 BE). Cliente veia las senales abrir y nunca cerrarlas.
            if result == "sl" and not _touched_sl:
                log.warning(
                    f"⚠️ Reconcile: {pair_d} {direction} exit={exit_price} NO tocó SL={_sig_sl} "
                    f"(profit=${profit:.2f}) — re-clasificando como MANAGED CLOSE/BE"
                )
                try:
                    _mc_sig = dict(sig)
                    _mc_sig["entry"] = entry
                    _mc_sig["mt5_entry"] = sig.get("mt5_entry", entry) or entry
                    _mc_sig["sl"] = exit_price  # usar exit real para pips honestos
                    _mc_sig["mt5_ticket"] = sig.get("mt5_ticket") or (deal.position_id if deal else 0)
                    # _send_sl_notification detectara BE close y usara el builder
                    # MANAGED CLOSE — closed at break-even (Fix A aplicado arriba).
                    _send_sl_notification(_mc_sig, reply_to_msg_id=_reply_id)
                    log.info(
                        f"🔄 Reconcile MANAGED CLOSE: {pair_d} notificado al VIP "
                        f"(exit={exit_price} profit=${profit:.2f}, ticket={getattr(deal, 'position_id', '?')})"
                    )
                    _record_daily_result(_mc_sig, "sl")
                except Exception as _e_mc:
                    log.warning(f"Reconcile MANAGED CLOSE error {pair_d}: {_e_mc}")
                _save_open_signals()
                continue

            # Anti-duplicado: si otro flujo ya notificó este cierre, skip
            _notif_key = f"{pair}_{direction}_{result}_reconcile"
            _prev = _recently_notified.get(_notif_key, 0)
            # FIX 2026-05-06 (Capa B): cross-check con el monitor TP/SL.
            # El monitor escribe keys tipo "{pair}_{direction}_tp1", "..._sl0", etc.
            # Antes solo se chequeaba la key con suffix "_reconcile" → reconcile
            # publicaba duplicados de cierres ya notificados por monitor con
            # diferente formato de key (visto hoy 21:01: 3x SL ORO duplicados).
            _prefix_match = f"{pair}_{direction}_{result}"  # ej "ORO_SELL_sl"
            _monitor_recent = False
            for _k_mon, _v_mon in _recently_notified.items():
                if _k_mon.startswith(_prefix_match) and not _k_mon.endswith("_reconcile"):
                    if isinstance(_v_mon, (int, float)) and (now - _v_mon) < 600:
                        _monitor_recent = True
                        break
            if (_prev and (now - _prev) < 600) or _monitor_recent:
                log.info(f"🔕 Reconcile: {result.upper()} {pair_d} ya notificado por monitor/reconcile — skip")
                _save_open_signals()
                continue
            _recently_notified[_notif_key] = now
            _save_notif_dedup()  # FIX 2026-05-06 (Capa A): persistir

            # Registrar en stats persistentes
            signal_copy = dict(sig)
            signal_copy["entry"] = entry
            if result == "tp" and exit_price > 0:
                signal_copy["_tp_final"] = exit_price
            elif result == "sl" and exit_price > 0:
                signal_copy["sl"] = exit_price
            _record_daily_result(signal_copy, result)

            # 2026-05-10 FIX CRITICO: antes el reconciler solo publicaba TEXTO simple al VIP
            # y NO publicaba al grupo ni disparaba WhatsApp. Resultado: cuando MT5 broker
            # cerraba la posicion mas rapido que el monitor de 30s (caso comun en BTC/ETH
            # con volatilidad), la celebracion al grupo + WhatsApp NUNCA llegaban.
            # Visto hoy 10-may: BTC ticket 763055104 +$2.65 y 763059353 +$1.37 cerraron
            # en MT5 broker pero NUNCA aparecio celebracion en grupo ni WhatsApp.
            # Solucion: usar las funciones completas _send_tp_celebration y
            # _send_sl_notification que cubren VIP foto + grupo foto + WhatsApp + IG.
            try:
                # Construir signal dict completo con todos los campos que las funciones esperan
                _sig_full = dict(sig)  # copia con tp/sl/entry/direction/pair etc
                _sig_full["entry"] = entry
                _sig_full["mt5_entry"] = sig.get("mt5_entry", entry) or entry
                if result == "tp" and exit_price > 0:
                    # Marcar que el TP fue alcanzado al precio exit_price
                    _sig_full["_tp_final"] = exit_price
                    _send_tp_celebration(_sig_full, reply_to_msg_id=_reply_id)
                    log.info(f"🔄 Reconcile TP: {pair_d} celebrado completo (VIP+grupo+WhatsApp) — exit={exit_price} pips={pips_num:.1f}")
                elif result == "sl" and exit_price > 0:
                    # _send_sl_notification se encarga de VIP texto + WhatsApp + (no grupo, regla 9 May)
                    # Setea sl=exit_price para que pips_lost se calcule contra el cierre real
                    _sig_full["sl"] = exit_price
                    _send_sl_notification(_sig_full, reply_to_msg_id=_reply_id)
                    log.info(f"🔄 Reconcile SL: {pair_d} notificado (VIP+WhatsApp, no grupo) — exit={exit_price} pips={pips_num:.1f}")
            except Exception as e:
                log.warning(f"Reconcile notify error {pair_d}: {e}")
        else:
            log.info(f"🧹 Reconcile: {pair_d} {direction} limpiada — nunca ejecutada en MT5 (age={((now-sdata.get('sent_at',now))/60):.0f}min)")

    # ── PHASE 2: Orphan closed deals ──────────────────────────────────────────
    # Detecta posiciones del copier que se cerraron en MT5 pero NO tenían señal
    # en copier_open_signals.json (p.ej. archivo corrupto en reinicio).
    # Ventana: deals cerrados en las últimas 2 horas. Notifica TP/SL perdido.
    # FIX 2026-05-06: Prevenir celebraciones perdidas cuando JSON se corrompe.
    try:
        _reconciled_pos_ids = {
            deal.position_id
            for _sid2, _sdata2, deal, reason in to_remove
            if reason == "closed_mt5" and deal is not None
        }
        # También los que ya estaban en mt5_open_keys (Caso A) — esos siguen vivos, no aplican
        _ORPHAN_WINDOW_SEC = 7200  # 2 horas

        # FIX 2026-05-11 (defensa en profundidad): cachear tickets orphan ya registrados
        # en copier_stats.json. Aunque _recently_notified expire o se corrompa, esto
        # garantiza idempotencia absoluta — un ticket que ya generó un trade en stats
        # con sig_id "{SYM}_orphan_{ticket}" NUNCA se vuelve a publicar.
        # Caso 11-may 18:35: 5 SLs re-publicados al VIP porque TTL dedup (30min) expiró
        # antes que la ventana orphan (2h). Con esta cache, no vuelve a pasar.
        _orphan_tickets_in_stats = set()
        try:
            if COPIER_STATS_FILE.exists():
                with open(COPIER_STATS_FILE, "r", encoding="utf-8") as _f_ost:
                    _cs_data = json.load(_f_ost)
                for _t in _cs_data.get("trades", []):
                    # FIX 2026-05-20: cachear TODOS los mt5_ticket registrados,
                    # no solo los sig_id "_orphan_*". Caso NZDUSD 765126185 hoy:
                    # el flujo normal grabo SL en stats sin sig_id "_orphan_*"
                    # y el reconcile orphan no lo reconocio → entrada duplicada.
                    _tk_field = _t.get("mt5_ticket")
                    if _tk_field:
                        try:
                            _orphan_tickets_in_stats.add(int(_tk_field))
                        except (TypeError, ValueError):
                            pass
                    _sid = _t.get("sig_id", "")
                    if "_orphan_" in str(_sid):
                        try:
                            _orphan_tickets_in_stats.add(int(str(_sid).rsplit("_", 1)[-1]))
                        except (ValueError, IndexError):
                            pass
        except Exception as _e_load_orphan:
            log.debug(f"Reconcile orphan tickets cache load error: {_e_load_orphan}")

        for _od in deals:
            if getattr(_od, "entry", 0) != DEAL_ENTRY_OUT:
                continue
            if _od.position_id not in copier_position_ids:
                continue  # no es del copier
            if _od.position_id in _reconciled_pos_ids:
                continue  # ya fue manejado por Phase 1
            if (now - _od.time) > _ORPHAN_WINDOW_SEC:
                continue  # demasiado antiguo

            # FIX 2026-05-11 (tarde-2): si el deal OUT tiene magic != MAGIC_COPIER,
            # el cierre vino de fuente externa (usuario cerro manualmente desde MT5,
            # otro bot/EA, ajuste manual). NO publicar al canal — el VIP no debe
            # anunciar como TP/SL del sistema lo que el usuario cerro a mano.
            # Caso 11-may 19:33: 3 closes magic=0 (US100Cash x2, AUDCAD) que el
            # usuario cerro manualmente a las 19:13 se publicaron al VIP como
            # falsos TPs/SLs llenando el canal de info incorrecta.
            _deal_magic = getattr(_od, "magic", 0) or 0
            if _deal_magic not in BS365_MAGICS:
                log.info(
                    f"🔕 Reconcile orphan SKIP: deal ticket={_od.ticket} {_od.symbol} "
                    f"magic={_deal_magic} (cierre manual/externo, no publicar al canal)"
                )
                continue

            # FIX 2026-05-11: idempotencia cross-restart contra copier_stats.json
            try:
                _od_tk_int = int(_od.ticket)
            except (TypeError, ValueError):
                _od_tk_int = None
            if _od_tk_int is not None and _od_tk_int in _orphan_tickets_in_stats:
                log.info(f"🔄 Reconcile orphan SKIP: ticket {_od.ticket} ya registrado en copier_stats")
                continue

            # Dedup por ticket: evitar doble notificación dentro de la sesión
            _orphan_dedup_key = f"orphan_deal_{_od.ticket}"
            if _recently_notified.get(_orphan_dedup_key, 0):
                continue

            # FIX 2026-05-13: si el trade YA fue celebrado correctamente por el flujo
            # normal (_send_tp_celebration / _send_sl_notification), no re-publicar
            # con el formato pobre del orphan path. Caso 13-may noche: tras reinicio
            # a las 19:01, el reconcile encontro BTC ticket 766753607 (TP +519.7 pts
            # cerrado a las 16:48, ya celebrado con chart) y lo re-publico como orphan
            # con formato simple. Las celebraciones normales persisten claves
            # cel_t{ticket}_* y sl_notif_t{ticket} con TTL 12h, asi que sobreviven
            # al restart. Si encontramos cualquiera de esas claves → skip orphan.
            _already_celebrated = False
            for _kk_chk in list(_recently_notified.keys()):
                if not isinstance(_kk_chk, str):
                    continue
                if _kk_chk.startswith(f"cel_t{_od.ticket}_"):
                    _already_celebrated = True
                    break
                if _kk_chk == f"sl_notif_t{_od.ticket}":
                    _already_celebrated = True
                    break
            if _already_celebrated:
                log.info(
                    f"🔕 Reconcile orphan SKIP: ticket {_od.ticket} ya celebrado "
                    f"por flujo normal (cel_t*/sl_notif_t* presente). NO duplicar al VIP."
                )
                _recently_notified[_orphan_dedup_key] = now
                _save_notif_dedup()
                continue

            _recently_notified[_orphan_dedup_key] = now
            _save_notif_dedup()  # FIX 2026-05-06 (Capa A): persistir dedup orphan ticket

            _od_sym = _od.symbol.upper()
            _od_dir = "SELL" if _od.type == getattr(mt5, "ORDER_TYPE_BUY", 0) else "BUY"
            _od_profit = getattr(_od, "profit", 0.0) or 0.0
            _od_result = "tp" if _od_profit > 0 else "sl"
            _od_exit = getattr(_od, "price", 0.0) or 0.0
            _od_pair_d = _get_display_pair(_od_sym)

            # Buscar precio de entrada en el deal IN del mismo position_id
            _od_entry = 0.0
            for _od_in in deals:
                if (getattr(_od_in, "entry", 0) == DEAL_ENTRY_IN
                        and _od_in.position_id == _od.position_id):
                    _od_entry = getattr(_od_in, "price", 0.0) or 0.0
                    break

            _od_pips_num, _od_pips_unit = _get_pips_info(_od_sym, _od_entry, _od_exit)

            log.warning(
                f"🔄 Reconcile ORPHAN: deal ticket={_od.ticket} {_od_sym} {_od_dir} "
                f"entry={_od_entry} exit={_od_exit} profit=${_od_profit:.2f} pips={_od_pips_num:.1f} "
                f"→ {_od_result.upper()} — señal no estaba en tracking (JSON corrupto?)"
            )

            # Registrar en stats si hay profit significativo
            try:
                _orphan_sig_rec = {
                    "pair": _od_sym, "pair_display": _od_pair_d,
                    "direction": _od_dir, "entry": _od_entry,
                    "source": "Canal Aliado",
                    "sig_id": f"{_od_sym}_orphan_{_od.ticket}",
                }
                if _od_result == "tp":
                    _orphan_sig_rec["_tp_final"] = _od_exit
                else:
                    _orphan_sig_rec["sl"] = _od_exit
                _record_daily_result(_orphan_sig_rec, _od_result)
            except Exception as _e_rec_orphan:
                log.debug(f"Reconcile orphan stats error: {_e_rec_orphan}")

            # Notificar al canal VIP y grupo público
            # FIX 2026-05-11 (tarde-3): cooldown global por par antes de publicar.
            # Si llegan 5 orphans del mismo par a la vez (post-restart, rara vez),
            # solo pasa el primero. Los demas quedan registrados en stats pero no
            # llenan el canal de ruido del mismo par.
            if not _can_publish_to_vip(_od_pair_d or _od_sym, event=f"orphan_{_od_result}"):
                continue
            # FIX 2026-05-13: usar _send_tp_celebration / _send_sl_notification igual
            # que el flujo normal de Phase 1 — asi el VIP recibe formato completo con
            # chart + entry + TPs en vez del texto simple antiguo. Si ya estaba celebrado
            # el dedup interno (cel_t{ticket}_*) hara skip automatico — pero ya cribamos
            # arriba en el bloque _already_celebrated, asi que aqui solo llegan orphans
            # genuinos (cierres MT5 sin tracker y sin celebracion previa).
            try:
                _orphan_sig_full = {
                    "pair": _od_sym,
                    "pair_display": _od_pair_d,
                    "mt5_symbol": _od_sym,
                    "direction": _od_dir,
                    "entry": _od_entry,
                    "mt5_entry": _od_entry,
                    "source": "Canal Aliado",
                    "sig_id": f"{_od_sym}_orphan_{_od.ticket}",
                    "mt5_ticket": int(_od.ticket),
                    "_mt5_ticket": int(_od.ticket),
                    "_tp_idx": 0,
                }
                if _od_result == "tp":
                    _orphan_sig_full["tp"] = _od_exit
                    _orphan_sig_full["_tp_final"] = _od_exit
                    _send_tp_celebration(_orphan_sig_full, reply_to_msg_id=None)
                    log.info(
                        f"🔄 Reconcile orphan TP: {_od_pair_d} celebrado con chart "
                        f"(profit=${_od_profit:.2f}, ticket={_od.ticket})"
                    )
                else:
                    _orphan_sig_full["sl"] = _od_exit
                    _send_sl_notification(_orphan_sig_full, reply_to_msg_id=None)
                    log.info(
                        f"🔄 Reconcile orphan SL: {_od_pair_d} notificado "
                        f"(loss=${_od_profit:.2f}, ticket={_od.ticket})"
                    )
            except Exception as _e_orphan:
                log.warning(f"Reconcile orphan notify error {_od_pair_d}: {_e_orphan}")
    except Exception as _e_phase2:
        log.warning(f"Reconcile Phase 2 (orphan deals) error: {_e_phase2}")
    # ── FIN PHASE 2 ───────────────────────────────────────────────────────────

    _save_open_signals()


def _send_daily_summary() -> None:
    """Envía resumen completo del día al canal VIP y grupo público.
    Lista cronológica de TODAS las operaciones (wins + losses) con total neto al final."""
    import requests
    from datetime import datetime
    import pytz
    tz = pytz.timezone("Europe/Andorra")
    now_tz = datetime.now(tz)
    hoy = now_tz.strftime("%d/%m/%Y")

    # ── Trades cerrados hoy (de copier_stats.json) ──
    today_trades = _load_copier_stats_today()

    # ── Señales aún abiertas (de _open_signals) ──
    with _signals_lock:
        open_pairs = {}
        for _sid, _sdata in _open_signals.items():
            _s = _sdata.get("signal", {})
            _p = _s.get("pair_display", _s.get("pair", "?"))
            _d = _s.get("direction", "?")
            _key = f"{_p} {_d}"
            open_pairs[_key] = open_pairs.get(_key, 0) + 1
    unique_open = len(open_pairs)

    # Si no hay actividad, no enviar
    if not today_trades and not open_pairs:
        log.info("📊 Resumen diario: sin actividad hoy, no se envía")
        return

    # ── Ordenar cronológicamente ──
    def _get_ts(t):
        return t.get("closed_at") or t.get("time") or 0

    today_sorted = sorted(today_trades, key=_get_ts)

    # ── Construir lista de operaciones ──
    wins_pips = 0.0
    losses_pips = 0.0
    n_wins = 0
    n_losses = 0
    trade_lines = []

    for t in today_sorted:
        pair    = t.get("pair_display") or t.get("pair", "?")
        direction = t.get("direction", "BUY")
        result  = t.get("result", "")
        pips    = float(t.get("pips_numeric") or t.get("pips") or 0)
        unit    = t.get("pips_unit", "pips")
        ts      = _get_ts(t)

        hora = datetime.fromtimestamp(ts, tz=tz).strftime("%H:%M") if ts else "--:--"
        dir_emoji = "📈" if direction == "BUY" else "📉"

        is_loss = result == "sl"

        if is_loss:
            losses_pips += pips
            n_losses += 1
            trade_lines.append(f"`{hora}` ❌ {dir_emoji} *{pair} {direction}* — `-{pips:.0f} {unit}`")
        else:
            wins_pips += pips
            n_wins += 1
            result_emoji = "✅" if result == "tp" else "💰"
            trade_lines.append(f"`{hora}` {result_emoji} {dir_emoji} *{pair} {direction}* — `+{pips:.0f} {unit}`")

    net_pips = wins_pips - losses_pips
    net_sign = "+" if net_pips >= 0 else ""

    # ── Señales abiertas al final ──
    open_txt = ""
    if open_pairs:
        open_lines = "\n".join(f"  ⏳ {pd}" for pd in sorted(open_pairs))
        open_txt = f"\n\n*Aún abiertas ({unique_open}):*\n{open_lines}"

    # ── Mensaje final ──
    trades_block = "\n".join(trade_lines) if trade_lines else "_Sin operaciones cerradas_"

    msg = (
        f"📊 *RESUMEN DEL DÍA — {hoy}*\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{trades_block}"
        f"{open_txt}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ Ganadas: *{n_wins}* ops — *+{wins_pips:.0f} pips/pts*\n"
        f"❌ Pérdidas: *{n_losses}* ops — *-{losses_pips:.0f} pips/pts*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📈 *TOTAL NETO: {net_sign}{net_pips:.0f} pips/pts*\n\n"
        f"🔥 _BuySell365 Pro — señales que dan resultados_"
    )

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    # Enviar a canal VIP
    try:
        resp = requests.post(url, json={"chat_id": CHANNEL_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        if resp.status_code == 200:
            log.info(f"📊 Resumen diario VIP enviado ({n_wins} wins, {n_losses} losses, net {net_sign}{net_pips:.0f})")
        else:
            log.warning(f"📊 Error resumen VIP: {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        log.warning(f"📊 Error enviando resumen VIP: {e}")

    # Enviar también al grupo público (versión resumida — solo totales)
    if GROUP_ID and str(GROUP_ID) != str(CHANNEL_ID):
        net_emoji = "🟢" if net_pips >= 0 else "🔴"
        msg_group = (
            f"📊 *CIERRE DEL DÍA — {hoy}*\n\n"
            f"✅ {n_wins} operaciones ganadoras\n"
            f"❌ {n_losses} pérdidas\n\n"
            f"{net_emoji} *RESULTADO: {net_sign}{net_pips:.0f} pips/pts*\n\n"
            f"💎 Únete al canal VIP para ver cada señal en detalle\n"
            f"👉 @BUYSELL_365_24_7"
        )
        try:
            requests.post(url, json={"chat_id": GROUP_ID, "text": msg_group, "parse_mode": "Markdown"}, timeout=10)
        except Exception as e:
            log.warning(f"📊 Error enviando resumen grupo: {e}")

    # ── Instagram: resumen diario DESACTIVADO ──
    # El usuario solo quiere TPs en Instagram, no resúmenes diarios
    # (se mantiene solo en Telegram)


# === SEÑALES REGALO — funciones ===

# FIX 2026-04-28: minimos para considerar una fuente "confiable" para regalar
GIFT_MIN_TRADES_HIST = 8         # min trades historicos para evaluar fuente
GIFT_MIN_WIN_RATE = 0.60         # min 60% WR para regalar
GIFT_HIST_WINDOW_DAYS = 30       # ventana de evaluacion (dias)


def _get_source_reliability(source: str) -> tuple:
    """Calcula reliability de una fuente desde copier_stats.json.

    Returns (is_reliable: bool, win_rate: float, total_trades: int).
    Reliability = >=60% WR con >=8 trades historicos en los ultimos 30 dias.
    Si la fuente es desconocida o no tiene datos -> NO confiable (False).
    """
    if not source:
        return (False, 0.0, 0)
    try:
        if not COPIER_STATS_FILE.exists():
            return (False, 0.0, 0)
        with open(COPIER_STATS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        trades = data.get("trades", [])
        cutoff = time.time() - (GIFT_HIST_WINDOW_DAYS * 86400)
        # Filtrar trades de esta fuente, decididos (tp o sl), recientes
        src_trades = [
            t for t in trades
            if t.get("source", "") == source
            and t.get("result") in ("tp", "sl")
            and t.get("closed_at", 0) >= cutoff
        ]
        total = len(src_trades)
        if total < GIFT_MIN_TRADES_HIST:
            return (False, 0.0, total)
        wins = sum(1 for t in src_trades if t.get("result") == "tp")
        wr = wins / total
        return (wr >= GIFT_MIN_WIN_RATE, wr, total)
    except Exception as e:
        log.debug(f"_get_source_reliability error: {e}")
        return (False, 0.0, 0)


def _should_gift_signal(pair: str, source: str = "") -> bool:
    """Decide si esta señal se regala al grupo público.

    FIX 2026-04-28: ahora exige que la fuente sea CONFIABLE (>=60% WR historico
    con >=8 trades). Las regalo deben ser de alta calidad — son nuestro mejor
    mostrador para conseguir VIPs nuevos. Una regalo que toca SL nos hace ver mal.

    Basado en análisis original (15-16 abril 2026):
    - ORO: franja óptima 8:00-12:00
    - OTRA: franja óptima 9:00-15:00
    """
    import random
    from datetime import datetime
    import pytz

    if not GROUP_ID:
        return False

    # FIX 2026-04-28: filtro de confiabilidad ANTES de probabilidad horaria.
    # Si la fuente no es confiable, NO regalamos esta senal.
    if source:
        is_reliable, wr, n_trades = _get_source_reliability(source)
        if not is_reliable:
            log.info(f"🎁 Skip gift {pair} ({source}) — source not reliable (WR={wr:.0%}, n={n_trades})")
            return False
        log.info(f"🎁 Source {source} CONFIABLE (WR={wr:.0%}, n={n_trades}) — evaluando regalar {pair}")

    tz = pytz.timezone("Europe/Andorra")
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")
    hour = now.hour

    # FIX 2026-04-26: PROHIBIDO regalar señales sabado/domingo (mercado Forex
    # cerrado, no tiene sentido regalar algo que no se puede ejecutar).
    # weekday(): Mon=0, Tue=1, ..., Sat=5, Sun=6
    weekday = now.weekday()
    if weekday >= 5:  # Sabado o domingo
        return False

    is_gold = pair.upper() in ("GOLD", "XAUUSD", "XAUUSD=X")
    current_minute = now.hour * 60 + now.minute

    with _gift_lock:
        # Reset diario + elegir 2 minutos random nuevos para el día
        if _gift_tracker["date"] != today_str:
            _gift_tracker["date"] = today_str
            # FIX 2026-05-08: pick 2 random minutos del día
            # Mañana: 8:00-12:00 (480-720). Tarde: 13:00-18:00 (780-1080).
            # Garantiza ≥1h de gap y horario distinto cada día → no se predice.
            target_morning = random.randint(480, 720)
            target_afternoon = random.randint(780, 1080)
            _gift_tracker["gift_targets"] = [target_morning, target_afternoon]
            _gift_tracker["gifts_count"] = 0
            _gift_tracker["gold_gifted"] = False
            _gift_tracker["other_gifted"] = False
            _gift_tracker["gold_pair"] = None
            _gift_tracker["other_pair"] = None
            _gift_tracker["gold_result"] = None
            _gift_tracker["other_result"] = None
            _save_gift_tracker()
            log.info(
                f"🎁 Gift targets para hoy: "
                f"#1={target_morning//60:02d}:{target_morning%60:02d}, "
                f"#2={target_afternoon//60:02d}:{target_afternoon%60:02d}"
            )

        gifts_count = _gift_tracker.get("gifts_count", 0)
        targets = _gift_tracker.get("gift_targets") or []

        # Si ya hicimos 2 regalos hoy, no más
        if gifts_count >= 2:
            return False

        # Si por alguna razón no hay targets cargados (estado viejo), generarlos
        if len(targets) < 2:
            targets = [random.randint(480, 720), random.randint(780, 1080)]
            _gift_tracker["gift_targets"] = targets
            _save_gift_tracker()

        # ¿Pasamos el target del próximo gift?
        next_target = targets[gifts_count]
        if current_minute < next_target:
            # Aún no es hora — esperar
            log.debug(
                f"🎁 Skip gift {pair}: aún no es hora "
                f"(now={current_minute//60:02d}:{current_minute%60:02d}, "
                f"target={next_target//60:02d}:{next_target%60:02d})"
            )
            return False

        # ✅ Es hora — regalar esta señal
        _gift_tracker["gifts_count"] = gifts_count + 1
        # Mantener tracker viejo (gold_pair / other_pair) para que el result
        # tracker existente (líneas 1785+) siga funcionando.
        if is_gold and not _gift_tracker.get("gold_gifted"):
            _gift_tracker["gold_gifted"] = True
            _gift_tracker["gold_pair"] = pair
            _gift_tracker["gold_result"] = None
        elif not is_gold and not _gift_tracker.get("other_gifted"):
            _gift_tracker["other_gifted"] = True
            _gift_tracker["other_pair"] = pair
            _gift_tracker["other_result"] = None
        else:
            # Slot del tipo ya usado (ej: 2 oros seguidos) → usar el otro libre
            if not _gift_tracker.get("gold_gifted"):
                _gift_tracker["gold_gifted"] = True
                _gift_tracker["gold_pair"] = pair
                _gift_tracker["gold_result"] = None
            elif not _gift_tracker.get("other_gifted"):
                _gift_tracker["other_gifted"] = True
                _gift_tracker["other_pair"] = pair
                _gift_tracker["other_result"] = None
        _save_gift_tracker()
        log.info(
            f"🎁 GIFT #{_gift_tracker['gifts_count']}/2 seleccionado: {pair} "
            f"({'oro' if is_gold else 'otra'}) source={source} "
            f"@ {current_minute//60:02d}:{current_minute%60:02d} "
            f"(target era {next_target//60:02d}:{next_target%60:02d})"
        )
        return True

    return False


def _format_gift_message(signal: dict) -> str:
    """Formatea una señal VIP como mensaje REGALO para el grupo público."""
    pair = signal.get("pair", "")
    pair_d = _get_display_pair(pair)
    direction = signal.get("direction", "BUY")
    entry = signal.get("entry", 0)
    sl = signal.get("sl", 0)
    tp = signal.get("tp", 0)
    tp2 = signal.get("tp2", 0)
    tp3 = signal.get("tp3", 0)

    dir_emoji = "🟢" if direction.upper() == "BUY" else "🔴"
    dir_label = "BUY" if direction.upper() == "BUY" else "SELL"

    fmt = lambda v: fmt_price(v, zero_label="Market")
    entry_display = "Market" if entry <= 0 else fmt(entry)

    # Construir líneas de TPs
    tp_lines = ""
    if tp > 0:
        tp_lines += f"\n🎯 TP: {fmt(tp)}"
    if tp2 > 0:
        tp_lines += f"\n🎯 TP2: {fmt(tp2)}"
    if tp3 > 0:
        tp_lines += f"\n🎯 TP3: {fmt(tp3)}"

    # Contar operaciones del día para el texto promo
    ops_hoy = 0
    with _signals_lock:
        from datetime import datetime
        import pytz
        tz = pytz.timezone("Europe/Andorra")
        hoy_ts = datetime.now(tz).replace(hour=0, minute=0, second=0).timestamp()
        ops_hoy = sum(1 for s in _open_signals.values()
                      if s.get("sent_at", 0) >= hoy_ts)
    ops_hoy = max(ops_hoy, 2)  # Mínimo 2 para que el texto tenga sentido

    # FIX 2026-04-26: traducido a INGLES — todo el sistema en EN
    msg = (
        f"🎁🎁🎁  *FREE SIGNAL*  🎁🎁🎁\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{dir_emoji} *{dir_label} — {pair_d}*\n\n"
        f"📍 Entry: {entry_display}"
        f"{tp_lines}\n"
        f"🛡️ SL: {fmt(sl)}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🆓 *Free signal* from our VIP Channel\n"
        f"📊 Risk management included\n\n"
        f"💎 *Want to get ALL signals?*\n"
        f"+{ops_hoy} trades in the VIP today already\n"
        f"👉 Type /vip and start trading with us"
    )
    return msg


def _is_gifted_signal(pair: str) -> bool:
    """Comprueba si hay una señal regalada activa para este par.

    FIX 2026-05-04 (#3): primero busca en _open_signals (señal todavía abierta).
    Si no la encuentra (puede haber sido limpiada tras close), cae a gift_tracker
    para detectar regalo del día por par. Esto garantiza que SIEMPRE celebremos
    en grande cuando una señal regalo hace TP o close, sin importar si llega
    antes o después de que se haya limpiado del tracker en memoria.

    FIX 2026-05-05: verificar gifted_date == hoy para que señales huérfanas de
    días anteriores (gifted=True pero de ayer) no disparen celebraciones hoy.
    """
    from datetime import datetime
    import pytz
    _today = datetime.now(pytz.timezone("Europe/Andorra")).strftime("%Y-%m-%d")
    _p_norm = (pair or "").upper()
    with _signals_lock:
        for sid, sdata in _open_signals.items():
            if sdata.get("gifted") and sdata.get("signal", {}).get("pair") == pair:
                _gdate = sdata.get("gifted_date", "")
                if not _gdate or _gdate == _today:  # sin fecha = legacy, aceptar
                    return True
    # Fallback: gift_tracker del dia (sobrevive close + reinicio)
    # FIX 2026-05-05: SOLO devolver True si el regalo aún está PENDIENTE (result=None).
    # Si ya se resolvió (result="tp" o "sl"), la celebración ya fue enviada — no
    # tratar señales nuevas del mismo par como si fueran el regalo original.
    try:
        with _gift_lock:
            _gold_pair = (_gift_tracker.get("gold_pair") or "").upper()
            _other_pair = (_gift_tracker.get("other_pair") or "").upper()
            _gold_result = _gift_tracker.get("gold_result")    # None = pendiente
            _other_result = _gift_tracker.get("other_result")  # None = pendiente
            # Solo activo si result es None (regalo abierto, aún no cerrado)
            if _p_norm and _p_norm == _gold_pair and _gold_result is None:
                return True
            if _p_norm and _p_norm == _other_pair and _other_result is None:
                return True
            # Tolerar variantes ORO: GOLD == XAUUSD == XAU == XAUUSD=X
            _gold_aliases = {"GOLD", "XAUUSD", "XAU", "XAUUSD=X"}
            if _p_norm in _gold_aliases and _gold_pair in _gold_aliases and _gold_result is None:
                return True
    except Exception:
        pass
    return False


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
                    # FIX 2026-05-01: persistir 'gifted' flag — antes se perdia tras
                    # reinicio del bot, por eso gift_tracker.gold_result quedaba null
                    # aunque la senal regalada hubiera cerrado en TP/SL.
                    "gifted": sdata.get("gifted", False),
                    # FIX 2026-05-18 P0.2: persistir mt5_ticket para que tras
                    # reinicio el monitor_tp_loop pueda cerrar/verificar la posicion.
                    # Sin esto, posiciones quedaban floteando sin cierre TP.
                    "mt5_ticket": sdata.get("mt5_ticket"),
                    "mt5_entry": sdata.get("mt5_entry"),
                    "mt5_executed": sdata.get("mt5_executed", False),
                    # FIX 2026-05-19: persistir flag BE-announced para dedup
                    # del "SL TO ENTRY" del lado aliado. Sin esto, tras reinicio
                    # el mismo trade volvia a recibir "SL TO ENTRY" si el aliado
                    # republicaba el update.
                    "_be_announced": sdata.get("_be_announced", False),
                }
        import os
        # FIX 2026-05-06: Guardar backup .bak ANTES de sobrescribir.
        # Si el proceso es interrumpido durante la escritura .tmp y el archivo
        # original queda corrupto, el backup permite recuperar las señales.
        _bak = str(OPEN_SIGNALS_FILE) + ".bak"
        if OPEN_SIGNALS_FILE.exists():
            try:
                import shutil
                shutil.copy2(str(OPEN_SIGNALS_FILE), _bak)
            except Exception as _e_bak:
                log.debug(f"open_signals backup skip: {_e_bak}")
        tmp = str(OPEN_SIGNALS_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            # FIX 2026-05-06 (#7): fsync para durabilidad ante corte de luz
            f.flush()
            try: os.fsync(f.fileno())
            except (OSError, AttributeError): pass
        os.replace(tmp, OPEN_SIGNALS_FILE)
    except Exception as e:
        log.warning(f"Error guardando open_signals: {e}")


def _load_open_signals():
    """Carga señales abiertas desde disco al arrancar."""
    try:
        if OPEN_SIGNALS_FILE.exists():
            # FIX 2026-05-06: Intentar cargar el archivo principal; si falla
            # (JSON corrupto por kill durante escritura), intentar el backup .bak.
            _bak = str(OPEN_SIGNALS_FILE) + ".bak"
            _raw = None
            try:
                with open(OPEN_SIGNALS_FILE, "r", encoding="utf-8") as f:
                    _raw = f.read()
                data = json.loads(_raw)
            except Exception as _e_main:
                log.warning(f"⚠️ copier_open_signals.json corrupto ({_e_main}) — intentando backup .bak")
                # Intentar recuperar desde backup
                import os
                try:
                    with open(_bak, "r", encoding="utf-8") as f_bak:
                        data = json.load(f_bak)
                    # Restaurar el archivo principal desde .bak
                    os.replace(_bak, str(OPEN_SIGNALS_FILE))
                    log.warning(f"✅ copier_open_signals.json restaurado desde .bak ({len(data)} señales recuperadas)")
                    # Avisar al admin
                    try:
                        import os as _os2
                        _tok = _os2.getenv("BOT_TOKEN", "")
                        _uid1 = _os2.getenv("USER_ID_1", "")
                        _uid2 = _os2.getenv("USER_ID_2", "")
                        import requests as _req_alert
                        _alert_url = f"https://api.telegram.org/bot{_tok}/sendMessage"
                        _alert_msg = (
                            f"⚠️ AVISO COPIER: copier_open_signals.json estaba corrupto al arrancar.\n"
                            f"Recuperado desde backup .bak con {len(data)} señal(es).\n"
                            f"Causa probable: proceso terminado durante escritura."
                        )
                        for _aid in sorted({_u for _u in (_uid1, _uid2) if _u}):
                            _req_alert.post(_alert_url, json={"chat_id": _aid, "text": _alert_msg}, timeout=8)
                    except Exception:
                        pass
                except Exception as _e_bak:
                    log.warning(f"⚠️ .bak también falló ({_e_bak}) — arrancando con señales vacías")
                    # Avisar al admin que se perdieron las señales
                    try:
                        import os as _os3
                        _tok = _os3.getenv("BOT_TOKEN", "")
                        _uid1 = _os3.getenv("USER_ID_1", "")
                        _uid2 = _os3.getenv("USER_ID_2", "")
                        import requests as _req_alert2
                        _alert_url2 = f"https://api.telegram.org/bot{_tok}/sendMessage"
                        _alert_msg2 = (
                            f"⚠️ AVISO COPIER: copier_open_signals.json CORRUPTO y sin backup.\n"
                            f"Se arranca con señales vacías — verificar MT5 manualmente.\n"
                            f"(La reconciliación automática intentará recuperar posiciones abiertas)"
                        )
                        for _aid in sorted({_u for _u in (_uid1, _uid2) if _u}):
                            _req_alert2.post(_alert_url2, json={"chat_id": _aid, "text": _alert_msg2}, timeout=8)
                    except Exception:
                        pass
                    data = {}
            # FIX 2026-05-01: validacion pydantic — descarta entries malformadas
            try:
                from schemas import validate_open_signals_json
                _validated = validate_open_signals_json(data)
                if _validated is not None:
                    data = _validated
                    log.info(f"✅ copier_open_signals.json validado ({len(data)} entradas)")
            except Exception as _e_sch:
                log.warning(f"⚠️ Schema validation copier skipped: {_e_sch}")
            loaded = 0
            with _signals_lock:
                for sid, sdata in data.items():
                    if sid not in _open_signals and sid not in _resolved_signals:
                        age = (time.time() - sdata.get("sent_at", 0)) / 3600
                        sig = sdata.get("signal", {})
                        entry = sig.get("entry", 0) or 0
                        # FIX 2026-04-17: Subir umbral 24h → 72h para alinear con monitor_tp
                        # (antes el monitor expiraba a 48h pero load descartaba a 24h: al
                        # reiniciar perdíamos tracking de posiciones MT5 todavía vivas).
                        if age > 72:
                            log.info(f"🗑️ Señal expirada al cargar ({age:.1f}h): {sid[:30]}")
                            continue
                        # FIX 2026-04-09: No descartar señales con entry=0
                        # El monitor les asignará precio live
                        if entry <= 0:
                            log.info(f"⚠️ Señal con entry=0 cargada (se asignará precio live): {sid[:30]}")
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
    # FIX 2026-04-12: Mapa COMPLETO — todos los pares que pueden llegar al chart generator
    _twelve_map = {
        # Oro
        "GOLD": "XAU/USD", "XAUUSD": "XAU/USD", "GC": "XAU/USD", "XAUUSD=X": "XAU/USD",
        # Índices
        "US100Cash": "NDX",  "NQ": "NDX",  "NAS100": "NDX",  "NASDAQ": "NDX",
        "US500Cash": "SPX",  "ES": "SPX",  "SP500": "SPX",
        "US30Cash":  "DJI",  "YM": "DJI",  "DOW30": "DJI",
        "GER40Cash": "GER40", "GER40": "GER40", "DAX": "GER40", "DE40": "GER40",
        # Petróleo
        "BRENT": "BRENT", "BRENTCash": "BRENT", "UKOIL": "BRENT",
        "OILCash": "WTI/USD", "USOIL": "WTI/USD", "USOILCash": "WTI/USD", "WTI": "WTI/USD",
        # Crypto
        "BTCUSD": "BTC/USD",
    }
    if pair in _twelve_map:
        return _twelve_map[pair]
    # Forex pairs: EURUSD → EUR/USD (insertar "/" en posición 3)
    if len(pair) == 6 and pair.isalpha():
        return f"{pair[:3]}/{pair[3:]}"
    return pair


def _min_sl_distance_for_pair(pair: str) -> float:
    """FIX 2026-04-30: Distancia mínima de SL en puntos/pips por tipo de activo.
    Usada por _auto_fix_inverted_sl() para construir un SL razonable cuando el
    SL recibido viene invertido (typo del aliado).
    """
    p = (pair or "").upper().replace("/", "")
    if p in ("GOLD", "XAUUSD", "ORO"):
        return 15.0           # 15 puntos oro
    if p in ("NAS100", "NASDAQ", "US100", "US30", "DOW", "US500", "SP500", "GER40", "DAX", "DE40"):
        return 50.0           # 50 puntos índices
    if p in ("USOIL", "WTI", "BRENT", "UKOIL"):
        return 30.0           # 30 ticks petróleo
    if "BTC" in p or "ETH" in p:
        return 100.0          # 100 puntos crypto
    # Forex
    if p.endswith("JPY"):
        return 0.20           # 20 pips JPY
    return 0.0015             # 15 pips forex non-JPY


def _advance_tp_idx_to_unrebased(signal: dict) -> None:
    """Anti-celebracion fantasma: si los primeros TPs ya estan rebasados por el
    precio actual al momento de publicar la senal, marcarlos como alcanzados
    SILENCIOSAMENTE (sin celebracion en canal) y avanzar _tp_idx al primer TP
    que aun NO esta rebasado.

    Sin esto: el monitor compara precio_actual >= TP cada 30s. Si BUY @ 4591 con
    TPs 4595..4614 y precio actual 4606, dispararia 4 'TP HIT' falsos en cascada
    en segundos (caso AnabelSignals 06:23 hoy).

    Con esto: marca los 4 TPs ya rebasados como _silent=True y _tp_idx=4 — el
    monitor solo celebra el siguiente TP cuando el precio LO CRUCE DESPUES de
    la senal, no antes.

    FIX 2026-05-02: parte del set de cambios "ZERO bloqueos" que reemplaza el
    intento previo de bloquear publicacion stale.

    FIX 2026-05-01: DESACTIVADO por defecto. Usuario pidio SIEMPRE celebrar TPs
    (caso GoldForexMarket 15:23 BUY ORO 4588 con TPs 4593-4605 estando precio
    4604 — no celebro nada). Para reactivar anti-cascada en .env:
      COPIER_SKIP_REBASED_TPS=1
    """
    if os.getenv("COPIER_SKIP_REBASED_TPS", "0").lower() not in ("1", "true", "yes", "on"):
        return  # Siempre celebrar TPs — politica del usuario
    try:
        _direction = (signal.get("direction") or "").upper()
        if _direction not in ("BUY", "SELL"):
            return
        _pair = signal.get("pair") or ""
        _live = _get_current_price(_pair) or 0
        if _live <= 0:
            return
        # Reconstruir _tp_levels si no existe (parser puede no haberlo seteado)
        _tp_levels = signal.get("_tp_levels")
        if not _tp_levels:
            _tp_levels = [
                signal.get(_k, 0) for _k in ("tp", "tp2", "tp3", "tp4", "tp5")
            ]
            _tp_levels = [t for t in _tp_levels if t and t > 0]
            signal["_tp_levels"] = _tp_levels
        if not _tp_levels:
            return
        # Calcular cuantos TPs ya estan rebasados al momento de publicar
        _rebased = []
        for _i, _tp in enumerate(_tp_levels):
            if _direction == "BUY" and _live >= _tp:
                _rebased.append((_i, _tp))
            elif _direction == "SELL" and _live <= _tp:
                _rebased.append((_i, _tp))
            else:
                break  # Encontramos el primer no-rebasado
        if not _rebased:
            return  # Ningun TP rebasado, todo normal
        # Marcar los rebasados como alcanzados silent y avanzar _tp_idx
        _alcanzados = signal.get("_tps_alcanzados", []) or []
        _entry = signal.get("entry") or _live
        for _i, _tp in _rebased:
            _pips = abs(_tp - _entry)
            _alcanzados.append({
                "nivel": _i + 1,
                "precio": _tp,
                "pips": round(_pips, 2),
                "_silent": True,  # Monitor: NO celebrar, ya estaba rebasado al publicar
                "_at_publish": True,
            })
        signal["_tps_alcanzados"] = _alcanzados
        signal["_tp_idx"] = len(_rebased)
        log.info(
            f"⏩ {_pair} {_direction}: {len(_rebased)} TPs ya rebasados al publicar "
            f"(precio {_live} vs TPs {[t for _, t in _rebased]}) — _tp_idx avanzado a "
            f"{len(_rebased)} para no celebrar fantasmas"
        )
    except Exception as _e:
        log.debug(f"_advance_tp_idx_to_unrebased error (no critico): {_e}")


def _auto_fix_inverted_sl(signal: dict) -> bool:
    """FIX 2026-04-30: AUTO-FIX para SL invertido (typo del aliado).
    Caso real AnabelSignals 30-abr 09:12: '#XAUUSD BUY 4591 SL 4678 TP 4595...' —
    SL > entry en BUY (debía ser 4578). El sistema antes ponía SL=0 y eliminaba
    los TPs, dejando la señal en estado vacío y bloqueada por send_to_channel.

    Heurística:
      • BUY con SL > entry  → SL invertido. Calcular SL = entry - max(2*dist_TP1, dist_min_par)
      • SELL con SL < entry → SL invertido. Calcular SL = entry + max(2*dist_TP1, dist_min_par)
    Si no hay TP1 válido, usar 2*dist_min_par como fallback. Loguear con prefijo
    🔧 AUTO-FIX para trazabilidad.

    Return True si modificó el SL, False si la señal estaba bien o no se pudo arreglar.
    """
    entry = signal.get("entry", 0) or 0
    sl    = signal.get("sl", 0) or 0
    direction = signal.get("direction", "")
    pair  = signal.get("pair", "")
    if entry <= 0 or sl <= 0 or direction not in ("BUY", "SELL"):
        return False
    inverted = (direction == "BUY" and sl > entry) or (direction == "SELL" and sl < entry)
    if not inverted:
        return False
    # Buscar TP1 válido (mismo lado correcto vs entry)
    tp1 = signal.get("tp", 0) or 0
    dist_tp1 = 0.0
    if tp1 > 0:
        if direction == "BUY" and tp1 > entry:
            dist_tp1 = tp1 - entry
        elif direction == "SELL" and tp1 < entry:
            dist_tp1 = entry - tp1
    dist_min = _min_sl_distance_for_pair(pair)
    # SL en R:R ~1:2 (SL = 2 * distancia TP1) o mínimo del par
    sl_distance = max(2.0 * dist_tp1, dist_min) if dist_tp1 > 0 else (2.0 * dist_min)
    # Clamp adicional para evitar SLs absurdamente grandes (cap a 2% del entry)
    sl_distance = min(sl_distance, abs(entry) * 0.02)
    if direction == "BUY":
        new_sl = entry - sl_distance
    else:  # SELL
        new_sl = entry + sl_distance
    # Redondeo razonable según par
    p = pair.upper().replace("/", "")
    if p in ("GOLD", "XAUUSD", "ORO") or p.endswith("CASH") or p in ("NAS100", "NASDAQ", "US100", "US30", "DOW", "US500", "GER40", "DAX", "USOIL", "BRENT", "WTI", "UKOIL"):
        new_sl = round(new_sl, 2)
    elif "BTC" in p or "ETH" in p:
        new_sl = round(new_sl, 1)
    elif p.endswith("JPY"):
        new_sl = round(new_sl, 3)
    else:
        new_sl = round(new_sl, 5)
    log.warning(
        f"🔧 AUTO-FIX SL invertido: {pair} {direction} entry={entry} "
        f"SL recibido={sl} → SL corregido={new_sl} "
        f"(distancia={sl_distance:.4f}, base TP1={dist_tp1:.4f}, min={dist_min:.4f}, R:R~1:2)"
    )
    signal["sl"] = new_sl
    signal["_sl_autofixed"] = True
    return True


def _validate_entry_vs_market(signal: dict) -> bool:
    """FIX 2026-04-17: Si entry difiere >1.5% del precio de mercado → RECHAZAR señal.
    Antes corregíamos al precio actual, pero eso rompía la relación entry/TP/SL
    (especialmente en señales retrasadas tipo Sureshot 2 días después → publicábamos
    TP<entry para BUY = imposible). Ahora descartamos directamente.

    FIX 2026-04-30: detectar BUY/SELL LIMIT legítimo. Si el entry está en el lado
    'pendiente' del precio actual (BUY entry < precio, SELL entry > precio) y
    la diferencia es <= 1.5%, NO marcamos stale: marcamos como is_limit=True y
    preservamos el entry original. Solo rechazamos cuando el entry está en el
    lado equivocado y la distancia supera el umbral del par.

    Return True si la señal es válida, False si hay que descartarla.
    """
    entry = signal.get("entry", 0) or 0
    if entry <= 0:
        return True  # sin entrada no se puede validar (se filtra en otro sitio)
    pair = signal.get("pair", "")
    if not pair:
        return True

    # Intentar MT5 primero (precio exacto del broker)
    live = None
    try:
        import price_feed as _mt5v
        # FIX 2026-04-22: añadir GER40 (faltaba → caía a yfinance sin precio exacto del broker)
        _mt5_sym_map_v = {
            "GOLD": "GOLD", "XAUUSD": "GOLD", "ORO": "GOLD",
            "SILVER": "SILVER", "XAGUSD": "SILVER", "PLATA": "SILVER",
            "NAS100": "US100Cash", "NASDAQ": "US100Cash", "US100": "US100Cash",
            "US30": "US30Cash", "DOW": "US30Cash", "US500": "US500Cash",
            "GER40": "GER40Cash", "DAX": "GER40Cash", "DE40": "GER40Cash",
            "USOIL": "OILCash", "BRENT": "BRENTCash", "BTCUSD": "BTCUSD",
        }
        _pair_clean = pair.upper().replace("/", "")
        _sym = _mt5_sym_map_v.get(_pair_clean, _pair_clean)
        if _mt5v.initialize():
            _mt5v.symbol_select(_sym, True)
            _tick = _mt5v.symbol_info_tick(_sym)
            if _tick and _tick.bid > 0:
                live = (_tick.ask + _tick.bid) / 2
    except Exception:
        pass

    # Fallback yfinance/TwelveData
    if not live:
        live = _get_current_price(pair)
    if not live or live <= 0:
        return True  # sin precio de referencia no podemos validar

    # FIX 2026-04-21 (M2): Tolerancia adaptativa por tipo de activo (antes 1.5% fijo — muy laxo).
    # Forex: 0.5% (50 pips en un par 1.00000 = demasiado slippage)
    # JPY pairs: 0.5% (mismo criterio — pip menor ya es 0.01)
    # Oro/Índices: 0.8% (más volátiles, ~38 pts en ORO 4800)
    # Crypto: 1.5% (volatilidad alta justificada)
    _pair_u = pair.upper().replace("/", "")
    if "BTC" in _pair_u or "ETH" in _pair_u:
        _max_pct = 0.015  # 1.5% crypto
    elif _pair_u in ("GOLD", "XAUUSD", "ORO") or _pair_u.endswith("CASH") or _pair_u in ("NAS100", "US30", "US500", "GER40", "USOIL", "BRENT"):
        _max_pct = 0.008  # 0.8% oro/índices/petróleo
    else:
        _max_pct = 0.005  # 0.5% forex
    pct_diff = abs(entry - live) / live
    direction = signal.get("direction", "")
    # FIX 2026-04-30: Detectar BUY/SELL LIMIT legítimo antes de rechazar como stale.
    # • BUY con entry < precio_actual → BUY LIMIT esperando pullback abajo.
    # • SELL con entry > precio_actual → SELL LIMIT esperando rebote arriba.
    # Permitimos hasta 1.5% (oro/índices/forex) — cubre zonas de pending razonables
    # sin abrir la puerta a señales realmente viejas.
    _limit_max_pct = 0.015
    if pct_diff <= _limit_max_pct:
        if direction == "BUY" and entry < live:
            log.info(
                f"🔧 AUTO-FIX BUY LIMIT detectado: {pair} entry={entry} < precio_actual={live:.4f} "
                f"(dif {pct_diff:.2%}) — preservando entry original como pending order"
            )
            signal["is_limit"] = True
            signal["order_type"] = "Limit"
            return True
        if direction == "SELL" and entry > live:
            log.info(
                f"🔧 AUTO-FIX SELL LIMIT detectado: {pair} entry={entry} > precio_actual={live:.4f} "
                f"(dif {pct_diff:.2%}) — preservando entry original como pending order"
            )
            signal["is_limit"] = True
            signal["order_type"] = "Limit"
            return True
    if pct_diff > _max_pct:
        log.warning(f"🚫 Entry stale RECHAZADA: {pair} entry={entry} difiere {pct_diff:.1%} del precio actual {live:.2f} (>{_max_pct:.1%} → señal vieja)")
        signal["_rejected_stale"] = True
        return False
    return True


def _get_current_price(pair: str) -> float | None:
    """Fetch current price via yfinance (gratis, sin límite).
    Twelve Data se reserva SOLO para gráficos de velas.
    """
    # FIX 2026-04-12: Mapa COMPLETO — todos los pares que SYMBOL_MAP puede recibir
    _yf_map = {
        # Oro
        "GOLD": "GC=F", "XAUUSD": "GC=F",
        # Plata (FIX 2026-05-08: XAGUSD=X delisted en Yahoo, usar futuros SI=F)
        "SILVER": "SI=F", "XAGUSD": "SI=F", "PLATA": "SI=F",
        # Índices
        "US100Cash": "NQ=F", "US500Cash": "ES=F", "US30Cash": "YM=F",
        "NAS100": "NQ=F", "US100": "NQ=F", "NASDAQ": "NQ=F",
        "US30": "YM=F", "DOW30": "YM=F", "DJ30": "YM=F", "DOW": "YM=F",
        "US500": "ES=F", "SP500": "ES=F",
        "GER40Cash": "GER40=X", "GER40": "GER40=X", "DAX": "GER40=X",
        # Petróleo
        "BRENT": "BZ=F", "BRENTCash": "BZ=F", "UKOIL": "BZ=F",
        "OILCash": "CL=F", "USOIL": "CL=F", "USOILCash": "CL=F", "WTI": "CL=F",
        # Crypto — XM usa "BTCUSD"
        "BTCUSD": "BTC-USD",
        # Forex — pares USD principales
        "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X",
        "AUDUSD": "AUDUSD=X", "NZDUSD": "NZDUSD=X", "USDCAD": "USDCAD=X",
        "USDCHF": "USDCHF=X",
        # Forex — pares GBP
        "GBPJPY": "GBPJPY=X", "GBPAUD": "GBPAUD=X", "GBPNZD": "GBPNZD=X",
        "GBPCAD": "GBPCAD=X", "GBPCHF": "GBPCHF=X",
        # Forex — pares EUR
        "EURJPY": "EURJPY=X", "EURCHF": "EURCHF=X", "EURAUD": "EURAUD=X",
        "EURGBP": "EURGBP=X", "EURCAD": "EURCAD=X", "EURNZD": "EURNZD=X",
        # Forex — pares AUD
        "AUDCAD": "AUDCAD=X", "AUDJPY": "AUDJPY=X", "AUDNZD": "AUDNZD=X",
        "AUDCHF": "AUDCHF=X",
        # Forex — pares JPY cruzados
        "NZDJPY": "NZDJPY=X", "CADJPY": "CADJPY=X", "CHFJPY": "CHFJPY=X",
        # Forex — otros
        "NZDCAD": "NZDCAD=X", "NZDCHF": "NZDCHF=X", "CADCHF": "CADCHF=X",
    }
    yf_ticker = _yf_map.get(pair)
    if not yf_ticker:
        if len(pair) == 6 and pair.isalpha():
            yf_ticker = f"{pair}=X"
        else:
            yf_ticker = pair

    # FIX 2026-04-21: Probar yfinance TAMBIÉN para spot-forex (=X). Antes se saltaban
    # y eso dejaba los pares EUR/JPY, USD/CAD, AUD/USD, etc. SIN precio cuando MT5 no
    # respondía y TWELVE_KEY estaba vacío o sin créditos → señales huérfanas sin TP/SL.
    # yfinance =X a veces tarda, pero suele responder con el último Close intradía.
    _use_yf = True
    if _use_yf:
        try:
            import yfinance as yf
            import warnings, io, sys
            # FIX 2026-04-12: Lock para evitar race condition entre señales simultáneas
            with _lock_yf:
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
        except Exception as e:
            log.warning(f"⚠️ yfinance falló para {pair} ({yf_ticker}): {e}")

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
        except Exception as e:
            log.warning(f"⚠️ TwelveData falló para {pair}: {e}")
    log.error(f"❌ Todas las fuentes de precio fallaron para {pair}")
    return None


def _fetch_chart_image(pair: str, direction: str, entry: float, tp: float, *, title_override: str = "", tp_levels: list = None, signal_sent_at: float = 0, sl: float = 0, tp_hit_label: str = "") -> bytes | None:
    """Generate professional chart using Twelve Data (primary) or yfinance (fallback) + matplotlib.
    title_override: si se pasa, usa ese título en vez de 'TP HIT'.
    tp_levels: lista de tuplas [("TP1", precio), ("TP2", precio), ...] para dibujar múltiples líneas.
    sl: si > 0, dibujar línea SL roja + fill rojo entre entry y SL (zona de riesgo). 0 = "SL no especificado".
    tp_hit_label: si se pasa (ej "TP1"), recorta velas a ~10 después del hit y marca con flecha + texto.

    FIX 2026-04-30: chart inteligente para TP HIT:
      1. Recorta el chart a la vela del HIT (no muestra 100-200 velas posteriores).
      2. Marca el HIT con flecha + label "TP{N} HIT".
      3. Dibuja SL como línea roja + zona de riesgo en rojo semitransparente.
      4. TPs no alcanzados como líneas amarillas tenues.
      5. Zona ganadora entry→TP en verde.
      6. Si SL=0 → nota visible "SL no especificado".
    """
    pair_d = _get_display_pair(pair)
    try:
        import requests
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
        import numpy as np

        opens, closes, highs, lows = None, None, None, None

        # ── Fuente 0: MT5 (precio real del broker — PRIORIDAD) ──
        try:
            import price_feed as _mt5_chart
            # FIX 2026-04-12: Mapa COMPLETO — mismos pares que _mt5_sym_map del monitor
            _mt5_chart_map = {
                # Oro
                "GOLD": "GOLD", "XAUUSD": "GOLD", "ORO": "GOLD",
                # Plata (FIX 2026-05-08)
                "SILVER": "SILVER", "XAGUSD": "SILVER", "PLATA": "SILVER",
                # Índices
                "NAS100": "US100Cash", "NASDAQ": "US100Cash", "US100": "US100Cash", "US100CASH": "US100Cash",
                "US30": "US30Cash", "DOW": "US30Cash", "DOW30": "US30Cash", "DJ30": "US30Cash", "US30CASH": "US30Cash",
                "US500": "US500Cash", "SP500": "US500Cash", "SPX500": "US500Cash", "US500CASH": "US500Cash",
                "GER40": "GER40Cash", "GER40CASH": "GER40Cash", "DAX": "GER40Cash", "DE40": "GER40Cash",
                # Petróleo
                "USOIL": "OILCash", "OILCASH": "OILCash", "OIL": "OILCash", "WTI": "OILCash",
                "BRENT": "BRENTCash", "BRENTCASH": "BRENTCash", "UKOIL": "BRENTCash",
                # Crypto
                "BTCUSD": "BTCUSD",
                # Forex — se resuelve automático: pair.upper() ya es el símbolo MT5
            }
            _mt5_sym_chart = _mt5_chart_map.get(pair.upper(), pair.upper())
            if _mt5_chart.initialize():
                _mt5_chart.symbol_select(_mt5_sym_chart, True)
                # FIX 2026-04-22: Si tenemos el timestamp de la señal, buscar desde ese momento
                # para que el gráfico muestre el movimiento real de la operación.
                _best_rates = None
                _target_tps  = [p for p in [tp] if p > 0]  # Solo TP para cobertura (entry puede ser stale)

                # Calcular cuántas velas M15 necesitamos desde signal_sent_at hasta ahora
                if signal_sent_at > 0:
                    from datetime import datetime as _dt_c
                    _elapsed_min = (time.time() - signal_sent_at) / 60
                    # Añadir margen del 50% y limitar a 200 velas
                    _m15_needed = min(int(_elapsed_min / 15 * 1.5) + 10, 200)
                    _m5_needed  = min(int(_elapsed_min / 5  * 1.5) + 10, 200)
                    log.info(f"📊 Señal tiene {_elapsed_min:.0f}min → buscando {_m15_needed} velas M15")
                else:
                    _m15_needed = 80
                    _m5_needed  = 80

                # FIX 2026-05-11: dos pasadas para elegir TF.
                # Caso real ORO 11 May: SELL @4705 TP4 4690 hit. M5 daba velas
                # 4670-4699 (cubre TPs pero entry 4705 queda fuera) → chart
                # parecia mostrar el trade abierto ya por debajo del entry,
                # sin recorrido visible desde 4705 hacia abajo.
                # Ahora preferimos TF que cubra entry+TPs (recorrido completo);
                # si ninguno cubre entry, caemos al criterio anterior (solo TPs).
                _entry_for_cobertura = entry if entry > 0 else 0
                _best_with_entry = None
                _best_tps_only = None
                _tf_iter = [
                    (_mt5_chart.TIMEFRAME_M5,  "M5",  _m5_needed),
                    (_mt5_chart.TIMEFRAME_M15, "M15", _m15_needed),
                    (_mt5_chart.TIMEFRAME_M30, "M30", 100),
                    (_mt5_chart.TIMEFRAME_H1,  "H1",  72),
                ]
                for _tf, _tf_name, _count in _tf_iter:
                    _rates = _mt5_chart.copy_rates_from_pos(_mt5_sym_chart, _tf, 0, _count)
                    if _rates is None or len(_rates) < 10:
                        continue
                    _all_lows  = [float(r['low'])  for r in _rates]
                    _all_highs = [float(r['high']) for r in _rates]
                    _data_min  = min(_all_lows)
                    _data_max  = max(_all_highs)
                    _covers_tps = all(_data_min <= p <= _data_max for p in _target_tps)
                    _covers_entry = (
                        _entry_for_cobertura > 0 and
                        _data_min <= _entry_for_cobertura <= _data_max
                    )
                    if _covers_tps and _covers_entry and _best_with_entry is None:
                        _best_with_entry = (_rates, _tf_name)
                        # primer TF que cubre todo → ideal, no seguir buscando
                        break
                    if _covers_tps and _best_tps_only is None:
                        _best_tps_only = (_rates, _tf_name)
                    if _best_rates is None:
                        _best_rates = _rates  # fallback de ultimo recurso
                if _best_with_entry is not None:
                    _best_rates = _best_with_entry[0]
                    log.info(f"📊 Chart {_best_with_entry[1]} cubre entry+TPs ({len(_best_rates)} velas) — recorrido completo")
                elif _best_tps_only is not None:
                    _best_rates = _best_tps_only[0]
                    log.info(f"📊 Chart {_best_tps_only[1]} cubre TPs ({len(_best_rates)} velas) — entry fuera de rango")
                if _best_rates is not None and len(_best_rates) >= 10:
                    opens  = [float(r['open'])  for r in _best_rates]
                    closes = [float(r['close']) for r in _best_rates]
                    highs  = [float(r['high'])  for r in _best_rates]
                    lows   = [float(r['low'])   for r in _best_rates]
                    log.info(f"📊 Chart data from MT5 ({len(_best_rates)} candles) — precio real broker")
        except Exception as _e_mt5c:
            log.warning(f"📊 MT5 chart error: {_e_mt5c}")

        # ── Fuente 1: Twelve Data (fallback si MT5 no disponible) ──
        if opens is None and TWELVE_KEY:
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
                # FIX 2026-04-12: Mapa COMPLETO — mismos pares que _yf_map global
                _yf_chart_map = {
                    # Oro
                    "GOLD": "GC=F", "XAUUSD": "GC=F", "GC": "GC=F",
                    # Plata (FIX 2026-05-08)
                    "SILVER": "SI=F", "XAGUSD": "SI=F", "PLATA": "SI=F", "SI": "SI=F",
                    # Índices
                    "US100Cash": "NQ=F", "NAS100": "NQ=F", "US100": "NQ=F", "NASDAQ": "NQ=F", "NQ": "NQ=F",
                    "US500Cash": "ES=F", "US500": "ES=F", "SP500": "ES=F", "ES": "ES=F",
                    "US30Cash": "YM=F", "US30": "YM=F", "DOW30": "YM=F", "DJ30": "YM=F", "DOW": "YM=F", "YM": "YM=F",
                    "GER40Cash": "GER40=X", "GER40": "GER40=X", "DAX": "GER40=X",
                    # Petróleo
                    "OILCash": "CL=F", "USOIL": "CL=F", "WTI": "CL=F",
                    "BRENTCash": "BZ=F", "BRENT": "BZ=F", "UKOIL": "BZ=F",
                    # Crypto
                    "BTCUSD": "BTC-USD",
                }
                _yf_ticker = _yf_chart_map.get(pair)
                if not _yf_ticker:
                    if len(pair) == 6 and pair.isalpha():
                        _yf_ticker = f"{pair}=X"
                    else:
                        _yf_ticker = pair
                # FIX 2026-04-12: Lock para evitar race condition
                with _lock_yf:
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

        # FIX 2026-04-30 + REFORZADO 2026-05-01: CORTE INTELIGENTE robusto.
        # Si la señal hizo TP HIT, recortar las velas a partir del primer cruce TP
        # + 5-8 velas de cierre. Casos reales:
        #   - NZD/JPY 1/5: TP 92.13 hit a las 8h, pero chart mostraba 200 velas
        #     terminando en 92.80 (entry+50 pips contra) → SUSCRIPTOR VE PERDIDA
        #   - GBP/JPY 1/5: TP 212.50 hit, pero chart mostraba subida a 214.0
        #     (130 pips drawdown) antes del dump al TP → VEROSIMIL PERO TERRIBLE PR
        # Refuerzo: si se pasa tp_hit_label SIEMPRE recortar (nunca dejar 200 velas).
        tp_hit_candle_idx = -1  # -1 = no marcar
        # FIX 2026-05-19: distinguir CLOSE chart vs TP HIT chart. Caso 19-may
        # ORO BUY 4549.48 partial cierra a 4633.48: precio siguio subiendo a
        # 4700+, entonces TODAS las velas devueltas tenian high>=4633 →
        # tp_hit_candle_idx=0 → slice [0,9] = chart de 9 velas casi vacio.
        # Para CLOSE simplemente recortamos a ultimas 60 sin buscar hit.
        _is_close_chart = bool(tp_hit_label) and tp_hit_label.upper() == "CLOSE"
        if _is_close_chart:
            if len(opens) > 60:
                _start_idx = len(opens) - 60
                opens  = opens[_start_idx:]
                closes = closes[_start_idx:]
                highs  = highs[_start_idx:]
                lows   = lows[_start_idx:]
                log.info(f"📊 Chart CLOSE — recortado a ultimas 60 velas (de {_start_idx + 60})")
        elif tp_hit_label and tp > 0:
            # Buscar primer cruce — usar close O wick (high para BUY, low para SELL)
            for _i_h in range(len(highs)):
                if direction == "BUY":
                    # BUY: TP alcanzado cuando high (mecha) >= TP
                    if highs[_i_h] >= tp - 0.0001:
                        tp_hit_candle_idx = _i_h
                        break
                else:
                    # SELL: TP alcanzado cuando low (mecha) <= TP
                    if lows[_i_h] <= tp + 0.0001:
                        tp_hit_candle_idx = _i_h
                        break

            # FIX 2026-05-01: AUNQUE no detectemos cruce, recortar a las ULTIMAS 60 velas
            # para no mostrar 200 (la realidad post-cierre confunde). Si encontramos cruce,
            # mostrar [hit-30, hit+8]. Si no, mostrar las ultimas 60 velas.
            if tp_hit_candle_idx >= 0:
                _post_hit = 8
                _pre_hit = 30   # mostrar contexto antes del hit (entry + zona ganadora)
                _new_end = min(tp_hit_candle_idx + _post_hit + 1, len(opens))
                _start_idx = max(0, tp_hit_candle_idx - _pre_hit)
                opens  = opens[_start_idx:_new_end]
                closes = closes[_start_idx:_new_end]
                highs  = highs[_start_idx:_new_end]
                lows   = lows[_start_idx:_new_end]
                tp_hit_candle_idx = tp_hit_candle_idx - _start_idx
                log.info(f"📊 Chart recortado a la vela del TP HIT ({len(opens)} velas, hit en idx={tp_hit_candle_idx}, src={len(opens)+_start_idx} originales)")
            else:
                # Cruce TP NO detectado en velas. Causas posibles:
                #   - Velas en timeframe demasiado bajo, primera vela ya supera TP
                #   - Datos de mercado no incluyen el momento del hit
                # Solucion robusta: recortar a las ultimas 60 velas (mas digerible)
                if len(opens) > 60:
                    _start_idx = len(opens) - 60
                    opens  = opens[_start_idx:]
                    closes = closes[_start_idx:]
                    highs  = highs[_start_idx:]
                    lows   = lows[_start_idx:]
                    log.warning(f"📊 Chart cruce TP no detectado en velas — recortado a ultimas 60 velas (de {_start_idx + 60} originales)")

        n = len(closes)

        # ── Paleta navy oscuro estilo Bloomberg/Wall Street ──
        # FIX 2026-05-11: mejorado contraste para que velas sean claramente visibles
        # tras compresión JPEG de Telegram. Verde brillante (#00c853) vs teal apagado
        # (#26a69a) — la diferencia en móvil es enorme. Fondo levemente más claro
        # (#131E2F vs #0D1B2A) también ayuda. Mechas más gruesas (2.2 vs 1.5) y cuerpos
        # más anchos (0.72 vs 0.65) para que cada vela sea legible aunque sean 50+.
        BG = "#131E2F"
        GRID = "#1e2f45"
        TEXT = "#a0aab8"
        CANDLE_GREEN = "#00c853"
        CANDLE_RED   = "#ff5252"
        GOLD = "#ffd700"
        ENTRY_COLOR = "#42a5f5"
        WICK_GREEN = "#00c853"
        WICK_RED   = "#ff5252"

        fig, ax = plt.subplots(figsize=(11, 5.5), facecolor=BG)
        ax.set_facecolor(BG)

        # ── Velas japonesas ──
        candle_width = 0.72
        wick_width = 2.2
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

        # ── TP lines — todas las líneas de TP ──
        # FIX 2026-04-30: TPs no alcanzados se muestran tenues; el TP alcanzado se resalta.
        _tp_colors = ["#ffd700", "#ffb300", "#ff8f00", "#ff6f00", "#ff4500"]  # Gold → Orange gradient
        _all_tps = tp_levels if tp_levels else [("TP", tp)]
        SL_RED = "#ff5252"
        for _i_tp, (_tp_label, _tp_val) in enumerate(_all_tps):
            if _tp_val <= 0:
                continue
            # ¿Este TP ya se alcanzó? (en valor está dentro/después del TP final tp)
            if direction == "BUY":
                _tp_already_hit = _tp_val <= tp + 0.0001
            else:
                _tp_already_hit = _tp_val >= tp - 0.0001
            if _tp_already_hit:
                _tp_color = _tp_colors[min(_i_tp, len(_tp_colors) - 1)]
                _lw = 2.5 if _i_tp == len(_all_tps) - 1 else 1.8
                _alpha = 0.95 if _i_tp == len(_all_tps) - 1 else 0.75
                _ls = "-"
            else:
                # TP futuro — línea amarilla tenue, label pequeño
                _tp_color = "#ffd700"
                _lw = 1.0
                _alpha = 0.35
                _ls = "--"
            ax.axhline(y=_tp_val, color=_tp_color, linestyle=_ls, linewidth=_lw, alpha=_alpha, zorder=5)
            if _tp_already_hit:
                ax.text(n + 0.5, _tp_val, f" {_tp_label} {fmt_price(_tp_val)}", color="#131722", fontsize=10,
                        fontweight="bold", va="center", ha="left", zorder=6,
                        bbox=dict(boxstyle="round,pad=0.3", facecolor=_tp_color, edgecolor=_tp_color, alpha=0.9))
            else:
                ax.text(n + 0.5, _tp_val, f" {_tp_label} {fmt_price(_tp_val)}", color=_tp_color, fontsize=8,
                        fontweight="normal", va="center", ha="left", zorder=6, alpha=0.65)

        # ── Entry line ──
        # FIX 2026-05-19: pre-computar _entry_in_range para evitar etiqueta
        # duplicada. Caso 19-may ORO PARTIAL CLOSE: entry 4549, velas en
        # 4680-4720 → entry fuera del 2% del rango → se dibujaba inline label
        # flotante fuera de los ejes + anotacion en esquina. Ahora solo
        # dibujamos inline si entry esta dentro del rango visible; si no,
        # la anotacion en esquina (mas abajo) lo cubre sin duplicar.
        _candle_min_pre = min(lows)
        _candle_max_pre = max(highs)
        _entry_in_range = (
            entry > 0 and
            _candle_min_pre * 0.98 <= entry <= _candle_max_pre * 1.02
        )
        if entry > 0 and _entry_in_range:
            ax.axhline(y=entry, color=ENTRY_COLOR, linestyle="--", linewidth=2, alpha=0.85, zorder=5)
            ax.text(n + 0.5, entry, f" Entry {fmt_price(entry)}", color="#ffffff", fontsize=10,
                    fontweight="bold", va="center", ha="left", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor=ENTRY_COLOR, edgecolor=ENTRY_COLOR, alpha=0.85))

        # ── SL line + zona de riesgo (FIX 2026-04-30) ──
        # Solo si SL > 0 y dirección coherente. Si SL=0, agregamos nota visible al final.
        _sl_to_draw = sl if sl > 0 else 0
        if _sl_to_draw > 0 and entry > 0:
            # Validar dirección: BUY → SL < entry; SELL → SL > entry
            _sl_dir_ok = (direction == "BUY" and _sl_to_draw < entry) or (direction == "SELL" and _sl_to_draw > entry)
            if _sl_dir_ok:
                ax.axhline(y=_sl_to_draw, color=SL_RED, linestyle="-", linewidth=1.8, alpha=0.85, zorder=5)
                ax.text(n + 0.5, _sl_to_draw, f" SL {fmt_price(_sl_to_draw)}", color="#ffffff", fontsize=10,
                        fontweight="bold", va="center", ha="left", zorder=6,
                        bbox=dict(boxstyle="round,pad=0.3", facecolor=SL_RED, edgecolor=SL_RED, alpha=0.85))
                # Zona de riesgo (entry → SL) en rojo semitransparente
                _risk_min = min(entry, _sl_to_draw)
                _risk_max = max(entry, _sl_to_draw)
                ax.axhspan(_risk_min, _risk_max, alpha=0.10, color=SL_RED, zorder=1)

        # ── Zona ganadora (fill entre entry y TP final, verde) ──
        if entry > 0 and tp > 0:
            y_min = min(entry, tp)
            y_max = max(entry, tp)
            # FIX 2026-04-30: cambiado a verde (más coherente con "ganaste") en lugar de gold
            _win_color = CANDLE_GREEN
            ax.axhspan(y_min, y_max, alpha=0.12, color=_win_color, zorder=1)
            # Bordes de la zona
            ax.axhline(y=y_min, color=_win_color, linestyle=":", linewidth=0.5, alpha=0.3, zorder=1)
            ax.axhline(y=y_max, color=_win_color, linestyle=":", linewidth=0.5, alpha=0.3, zorder=1)

        # ── Marcar la vela del TP HIT con flecha + label "TPx HIT" (FIX 2026-04-30) ──
        # FIX 2026-05-01: solo dibujar flecha si label es TP-style (no "CLOSE")
        if tp_hit_candle_idx >= 0 and tp_hit_label and tp_hit_label.upper().startswith("TP") and 0 <= tp_hit_candle_idx < n:
            # Posición de la flecha: punta en (idx, tp); origen ~3 velas atrás y 2% más arriba/abajo
            _arrow_dy = (max(highs) - min(lows)) * 0.08
            if direction == "BUY":
                _arrow_origin_y = tp + _arrow_dy
                _va = "bottom"
            else:
                _arrow_origin_y = tp - _arrow_dy
                _va = "top"
            _arrow_origin_x = max(0, tp_hit_candle_idx - 2)
            ax.annotate(
                f"{tp_hit_label} HIT ✓",
                xy=(tp_hit_candle_idx, tp),
                xytext=(_arrow_origin_x, _arrow_origin_y),
                fontsize=11, color="#ffffff", fontweight="bold",
                ha="center", va=_va,
                bbox=dict(boxstyle="round,pad=0.4", facecolor=CANDLE_GREEN, edgecolor="#ffffff", alpha=0.95, linewidth=1.2),
                arrowprops=dict(arrowstyle="->", color=CANDLE_GREEN, lw=2, alpha=0.9),
                zorder=7
            )

        # ── Nota visible "SL no especificado" si SL=0 (transparencia) ──
        # Solo en contexto TP HIT (tp_hit_label set como TPx) — para no contaminar
        # charts de cierre/parcial donde no hay tracking de SL del signal original.
        if sl <= 0 and tp_hit_label and tp_hit_label.upper().startswith("TP"):
            ax.text(0.5, 0.96, "⚠ SL not specified",
                    transform=ax.transAxes,
                    fontsize=9, color="#ffae42", fontweight="bold",
                    ha="center", va="top",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="#3a2a14", edgecolor="#ffae42", alpha=0.85),
                    zorder=8)

        # Calcular pips ganados
        # GOLD usa "pips" con factor x10 — convencion aliados (SureShot, etc.)
        pips_won = abs(tp - entry) if entry > 0 else 0
        if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
            pips_label = f"+{pips_won * 10:.0f} pips" if pips_won >= 0.1 else ""
        elif "JPY" in pair.upper():
            # JPY pairs: 1 pip = 0.01 → multiply by 100
            pips_label = f"+{pips_won * 100:.0f} pips" if pips_won > 0 else ""
        elif entry >= 100:
            # Indices (NAS100, US30, etc.): raw points
            pips_label = f"+{pips_won:.1f} pts" if pips_won > 0 else ""
        else:
            pips_label = f"+{pips_won * 10000:.0f} pips" if pips_won > 0 else ""

        # Título con pips
        dir_label = "BUY" if direction.upper() == "BUY" else "SELL"
        if title_override:
            title = title_override
        else:
            title = f"✅ TP REACHED — {dir_label} {pair_d}"
            if pips_label:
                title += f"  |  {pips_label}"
        ax.set_title(title, color=GOLD, fontsize=16, fontweight="bold", pad=18,
                     fontfamily="sans-serif")

        # Grid estilo TradingView — FIX 2026-05-11: alpha 0.08→0.14 para que las líneas
        # sean visibles sin tapar las velas (antes eran casi invisibles en screenshots)
        ax.grid(True, alpha=0.14, color=TEXT, linestyle="-", linewidth=0.5)
        ax.tick_params(colors=TEXT, labelsize=9)
        ax.set_xlim(-1, n + 9)  # Espacio para labels (más ancho para múltiples TPs)

        # FIX 2026-04-22: Eje Y inteligente — centrar en precio real (velas + TPs)
        # Si el entry está muy lejos del rango de velas (señal con entry stale),
        # NO forzar el eje a incluirlo: las velas quedarían comprimidas y la imagen se vería mal.
        # Solo forzamos TPs (son precios que SÍ se alcanzaron o están cerca).
        # FIX 2026-05-19: reutilizar _entry_in_range / _candle_min_pre / _candle_max_pre
        # ya calculados arriba para que el inline label y el rango del eje sean coherentes.
        _tp_prices = [v for v in ([tp] + [t[1] for t in (_all_tps if tp_levels else [])]) if v > 0]
        _candle_min = _candle_min_pre
        _candle_max = _candle_max_pre
        # Calcular rango base: velas + TPs (siempre incluir TPs porque son relevantes)
        _range_min = min([_candle_min] + _tp_prices)
        _range_max = max([_candle_max] + _tp_prices)
        # FIX 2026-04-30: incluir SL en el rango si está dibujado (mismo criterio que entry)
        if _sl_to_draw > 0 and entry > 0:
            _sl_in_range = _candle_min * 0.97 <= _sl_to_draw <= _candle_max * 1.03
            if _sl_in_range:
                _range_min = min(_range_min, _sl_to_draw)
                _range_max = max(_range_max, _sl_to_draw)
        if _entry_in_range:
            _range_min = min(_range_min, entry)
            _range_max = max(_range_max, entry)
        _margin = (_range_max - _range_min) * 0.10
        ax.set_ylim(_range_min - _margin, _range_max + _margin)
        # Si entry está fuera de rango, reemplazar línea de entrada por una anotación en el borde
        if not _entry_in_range and entry > 0:
            # Dibujar solo una etiqueta en el borde del eje (sin línea)
            _entry_y_pct = 0.97 if entry > _range_max else 0.03
            ax.annotate(
                f"↑ Entry {fmt_price(entry)}" if entry > _range_max else f"↓ Entry {fmt_price(entry)}",
                xy=(n * 0.02, _range_min + (_range_max - _range_min) * _entry_y_pct),
                fontsize=9, color=ENTRY_COLOR, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.2", facecolor=BG, edgecolor=ENTRY_COLOR, alpha=0.8)
            )

        for spine in ax.spines.values():
            spine.set_visible(False)

        # ── Watermark diagonal repetido (anti-screenshot/repost) ──
        # Cambio 2026-05-09: del watermark central gigante (que tapaba data)
        # a 5 instancias en diagonal con alpha bajo. Estilo "documento confidencial".
        # Si alguien hace screenshot/repost, la marca queda por todo el chart.
        for _wm_x, _wm_y in [(0.10, 0.85), (0.30, 0.65), (0.50, 0.45),
                              (0.70, 0.25), (0.90, 0.05)]:
            fig.text(_wm_x, _wm_y, "BUYSELL365 PRO", fontsize=14, color="#243550",
                     ha="center", va="center", fontweight="bold", alpha=0.55,
                     rotation=-25, transform=fig.transFigure, zorder=0)
        # Watermark pequeño esquina inferior derecha
        fig.text(0.98, 0.02, "buysell365.pro", fontsize=9, color="#5a6885",
                 ha="right", va="bottom", fontweight="bold")

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=180, facecolor=BG, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        log.warning(f"Chart generation error: {e}")
        return None


# ════════════════════════════════════════════════════════════════════
# FIX 2026-05-12 P0.1: Verificacion MT5 antes de publicar celebraciones
# ════════════════════════════════════════════════════════════════════
# Caso real 12-may 17:02: bot publico "TP REACHED — SELL NAS100 +300 pts"
# con imagen completa, pero la orden us100cash SELL LIMIT @ 29189.70 seguia
# PENDIENTE en MT5 (el precio nunca cruzo el limit). Resultado: celebracion
# de operacion que NUNCA existio en la cuenta. Riesgo legal/credibilidad.
#
# Esta funcion verifica que el trade REALMENTE se ejecuto en MT5 antes de
# publicar TP/SL/Partial. Si la signal afirma haber ejecutado pero MT5 no
# tiene rastro (ni viva ni cerrada) → BLOQUEAR la celebracion.
def _verify_mt5_trade_exists(signal: dict, context: str = "") -> tuple[bool, str]:
    """Verifica que el trade asociado a la signal exista en MT5 (vivo o cerrado).
    Retorna (exists, reason).
    - exists=True si el trade es real (vivo en positions o cerrado en deals).
    - exists=False si signal afirma mt5_executed pero MT5 no lo confirma.
    - context: string para logs (ej. 'tp_celebration', 'sl_notification').

    Politica:
    - Si signal.mt5_executed != True → False ('signal no ejecutada en MT5').
    - Si signal.mt5_ticket vacio → False ('sin ticket asociado').
    - Si MetaTrader5 no disponible / sesion rota → True (no bloquear por infra).
    - Si ticket en positions_get → True ('live in MT5').
    - Si ticket aparece como deal cerrado o pending order → True/False segun caso.
    - Si signal RECIENTE (<2h) y no se encuentra → False con prefijo "RETRY:"
      (caller debe diferir cleanup y reintentar proximo ciclo).
    - Caso contrario → False ('ticket no encontrado en MT5').

    FIX 2026-05-12 (caso ETH 765966520):
    - Antes: bare `initialize()` podia atachear a sesion sin login → positions_get
      vacio para tickets vivos → SL real bloqueado + cleanup → aviso real perdido.
    - Ahora: init+login con credenciales + chequeo account_info + RETRY transitorio.
    """
    try:
        # FIX 2026-05-20: usuario quiere que TODAS las senales tracked tengan
        # seguimiento + celebracion de TP, sin importar si MT5 abrio el trade
        # propio. La verificacion de que el TP REALMENTE se toco la hace el
        # monitor leyendo precios MT5 reales (no se confia en el aliado).
        # La proteccion contra senales nacidas muertas (precio ya paso TP1
        # al publicar) la cubre el Fix B de 19-may en el path de publicacion.
        # Aqui solo verificamos coherencia cuando SI hay trade propio.
        if not signal.get("mt5_executed", False):
            return True, "signal sin MT5 propio — tracking aliado OK (TP verificado por precio MT5)"
        ticket = signal.get("mt5_ticket") or signal.get("_mt5_ticket")
        if not ticket:
            return True, "signal sin ticket pero mt5_executed=True — permitir celebracion"
        try:
            import price_feed as _mt5_check
        except ImportError:
            return True, "MetaTrader5 import error (permitir por defecto)"

        # FIX 2026-05-12 (C): usar init+login propio en vez de bare initialize().
        # `_mt5_init_and_login` aplica MT5_LOGIN/PASSWORD/SERVER del .env y
        # asegura que la sesion apunte a la cuenta correcta (1301348583 demo XM).
        # Sin esto, positions_get(ticket=...) devuelve () para tickets vivos cuando
        # MT5 reattacha a la sesion sin login activo.
        try:
            _ok_init, _reason_init = _mt5_init_and_login()
            if not _ok_init:
                return True, f"mt5 init+login fallo ({_reason_init}) — permitir"
        except Exception:
            # Fallback al initialize bare si la helper no esta disponible aun
            if not _mt5_check.initialize():
                return True, "mt5 no inicializado (permitir por defecto)"

        # FIX 2026-05-12 (C2): doble check de sesion. Si account_info=None la
        # conexion al broker no esta autenticada — positions_get mentira sera
        # silencioso. Devolver True para no bloquear publicacion legitima.
        try:
            _acc_info = _mt5_check.account_info()
            if _acc_info is None:
                return True, "mt5 account_info=None (sesion no autenticada) — permitir"
        except Exception:
            pass

        # Buscar en positions vivas
        try:
            _pos = _mt5_check.positions_get(ticket=int(ticket))
            if _pos and len(_pos) > 0:
                return True, f"live in MT5 (ticket {ticket})"
        except Exception:
            pass
        # Buscar como deal cerrado (history).
        # FIX 2026-05-14: doble busqueda — primero por position (mas eficiente y especifico,
        # devuelve solo los deals de ese position_id, evita iterar miles de deals globales),
        # luego fallback a window de 7 dias (antes 48h era insuficiente para sesiones MT5
        # con history sync lento o reattached). Caso 14-may BTC 768258449: cerrado 18:00,
        # verify a las 18:28 devolvio RETRY (history_deals_get(48h) vacio) → daily tracker
        # registro +843 pts fantasma.
        try:
            # API directa por position_id (mas confiable)
            _deals_pos = _mt5_check.history_deals_get(position=int(ticket)) or []
            for _d in _deals_pos:
                if getattr(_d, "position_id", None) == int(ticket) or getattr(_d, "ticket", None) == int(ticket):
                    return True, f"closed deal by position_id (ticket {ticket})"
        except Exception:
            pass
        try:
            # Fallback a window 7 dias por si la consulta por position falla
            _now_ts = time.time()
            _from = _now_ts - 7 * 24 * 3600
            _deals = _mt5_check.history_deals_get(_from, _now_ts) or []
            for _d in _deals:
                if getattr(_d, "position_id", None) == int(ticket) or getattr(_d, "ticket", None) == int(ticket):
                    return True, f"closed deal in history-7d (ticket {ticket})"
        except Exception:
            pass
        # Buscar tambien en history_orders por si fue cancelada/expirada
        try:
            _now_ts = time.time()
            _from = _now_ts - 7 * 24 * 3600
            _h_orders = _mt5_check.history_orders_get(_from, _now_ts) or []
            for _o in _h_orders:
                if getattr(_o, "ticket", None) == int(ticket) or getattr(_o, "position_id", None) == int(ticket):
                    return True, f"order found in history (ticket {ticket})"
        except Exception:
            pass
        # Buscar como orden pending (los limit/stop sin ejecutar)
        try:
            _orders = _mt5_check.orders_get(ticket=int(ticket))
            if _orders and len(_orders) > 0:
                # Si esta como pending order, NO es trade ejecutado
                return False, f"ticket {ticket} aun PENDING (limit/stop no ejecutado)"
        except Exception:
            pass

        # FIX 2026-05-12 (B): si la signal es RECIENTE (<2h desde envio) y dice
        # haberse ejecutado con ticket valido pero no encontramos rastro, marcar
        # como RETRY (transitorio) en vez de fantasma definitivo. El caller debe
        # diferir el cleanup y reintentar el proximo ciclo de monitor.
        # Caso real ETH 765966520 (12-may 21:35): trade vivo en MT5 pero verify
        # devolvio False → SL real bloqueado + signal limpiada → aviso perdido
        # cuando SL real saltó a las 22:34.
        _sent_at = signal.get("sent_at") or signal.get("timestamp") or 0
        try:
            _age = time.time() - float(_sent_at) if _sent_at else 99999.0
        except (TypeError, ValueError):
            _age = 99999.0
        if _age < 7200:
            return False, f"RETRY: ticket {ticket} no encontrado (age={_age:.0f}s, posible transitorio)"
        return False, f"ticket {ticket} no encontrado en MT5 (positions/deals/orders, age={_age:.0f}s)"
    except Exception as _e:
        log.warning(f"_verify_mt5_trade_exists error ({context}): {_e}")
        # FIX 2026-05-18 P0.3: si MT5 esta offline (ConnectionError, init fail,
        # etc.), NO permitir celebracion fantasma. Marcar como RETRY para que
        # el caller difiera y reintente el siguiente ciclo. Antes retornaba
        # True (permitir) y eso reabria el agujero del bug del 14-may.
        return False, f"RETRY: MT5 unreachable ({_e})"


def _send_tp_celebration(signal: dict, reply_to_msg_id: int = None) -> None:
    """Send TP celebration to channel with chart image and rockets."""
    import requests

    direction = signal["direction"]
    pair = signal["pair"]
    # FIX 2026-04-27: usar mt5_entry (precio REAL ejecutado en MT5) cuando exista.
    # Caso GBPUSD 27/04: entry teorico 1.35320 pero MT5 ejecuto a 1.35118 → la
    # celebracion mostraba "+16 pips" calculados sobre el teorico, mientras que
    # la cuenta real cerro practicamente neutra. Ahora reflejamos el real.
    entry = signal.get("mt5_entry", 0) or signal["entry"]
    # FIX 2026-04-08: Usar TP final (el más alto/bajo) para calcular profit real
    tp = signal.get("_tp_final") or signal.get("tp", 0) or 0
    pair_d = _get_display_pair(pair)

    # FIX 2026-04-29: idempotencia + atomicidad. Visto en log VIP 29/04: ORO BUY
    # @4534 TP4 4540 celebrado 3 veces (4:33, 4:35, 5:01, 5:35) con pips
    # distintos (+6/+9/+12). Causa: 2 paths llaman _send_tp_celebration
    # (monitor_tp_loop + channel update tp_hit) sin compartir dedup; ademas la
    # ventana entre lecturas permitia que entry/tp fueran reinterpretados con
    # mt5_entry actualizado entre llamadas. Solucion: una sola celebracion por
    # (pair+direction+nivel+tp_price) con cooldown 10 min, y los valores se
    # snapshotean al inicio para que profit sea deterministico.
    _cel_lvl = (signal.get("_tp_idx", 0) or 0) + 1
    # FIX 2026-05-13: clave dedup por mt5_ticket/sig_id (unico por trade), ventana
    # ampliada a 4h. Caso 13-may: ETH +11.5 celebrado 14:17 y 15:09 (gap 52 min,
    # superaba la ventana de 10 min). Antes la clave era por par+dir+lvl+tp que
    # podia colisionar entre senales distintas del mismo par cerradas a precios
    # similares; ahora prima el ticket si existe.
    _cel_ticket = signal.get("mt5_ticket") or signal.get("_mt5_ticket") or 0
    _cel_sig_id = signal.get("sig_id") or ""
    if _cel_ticket:
        _cel_key = f"cel_t{_cel_ticket}_{_cel_lvl}"
    elif _cel_sig_id:
        _cel_key = f"cel_s{_cel_sig_id}_{_cel_lvl}"
    else:
        _cel_key = f"cel_{pair}_{direction}_{_cel_lvl}_{tp:.5f}"
    _cel_now = time.time()
    _cel_prev = _recently_notified.get(_cel_key, 0)
    # FIX 2026-05-18 P0.4: ventana de dedup coherente con _DEDUP_TTL_CELEBRATION
    # (12h). Antes 4h vs persistencia 12h -> reconcile que tocaba el mismo TP
    # entre 4h-12h post-cierre lo celebraba duplicado (bug 13-may BTC/ETH/ORO).
    if _cel_prev and (_cel_now - _cel_prev) < _DEDUP_TTL_CELEBRATION:
        log.info(
            f"🔕 TP celebration duplicada ignorada: {_cel_key} "
            f"(ya celebrado hace {_cel_now - _cel_prev:.0f}s, ventana 12h)"
        )
        return
    _recently_notified[_cel_key] = _cel_now
    _save_notif_dedup()  # FIX 2026-05-06 (Capa A): persistir TP celebration dedup

    # FIX 2026-05-12 P0.1: verificar que el trade REALMENTE existe en MT5
    # antes de publicar celebracion. Bloquea TP HITs fantasma de ordenes
    # PENDING (limit/stop sin ejecutar) y celebraciones de ghost trades.
    # FIX 2026-05-12 (B): si verify devuelve RETRY (transitorio), liberar el
    # dedup de celebracion para permitir reintento — no bloquear TPs reales
    # por fallos de sesion MT5.
    _exists, _reason = _verify_mt5_trade_exists(signal, context="tp_celebration")
    if not _exists:
        if isinstance(_reason, str) and _reason.startswith("RETRY:"):
            log.warning(
                f"⏸️ TP celebration DIFERIDA — {pair_d} {direction} ({_reason}). "
                f"Liberando dedup para retry proximo ciclo."
            )
            _recently_notified.pop(_cel_key, None)
            _save_notif_dedup()
            return
        log.warning(
            f"🚫 TP celebration BLOQUEADA — {pair_d} {direction} ({_reason}). "
            f"Signal sin trade real en MT5 — NO publicando para evitar celebracion fantasma."
        )
        return

    # FIX 2026-05-11 (tarde-3): cooldown global por par para canal VIP
    if not _can_publish_to_vip(pair_d or pair, event=f"tp_celebration_lvl{_cel_lvl}"):
        return

    fmt = lambda v: fmt_price(v, zero_label="Market")

    dir_label = direction.upper()  # BUY/SELL sin traducir
    dir_emoji = "🟢" if direction == "BUY" else "🔴"

    # Calcular pips ganados con el TP final
    # GOLD usa "pips" con factor x10 — convencion aliados (SureShot, etc.)
    pips_won = abs(tp - entry) if entry > 0 and tp > 0 else 0
    if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
        pips_str = f"+{pips_won * 10:.0f} pips" if pips_won >= 0.1 else ""
    elif "JPY" in pair.upper():
        # JPY pairs: 1 pip = 0.01 → multiply by 100
        pips_str = f"+{pips_won * 100:.0f} pips" if pips_won > 0 else ""
    elif entry >= 100:
        # Indices (NAS100, US30, etc.): raw points
        pips_str = f"+{pips_won:.1f} pts" if pips_won > 0 else ""
    else:
        pips_str = f"+{pips_won * 10000:.0f} pips" if pips_won > 0 else ""

    pips_line = f"\n💰 Profit: *{pips_str}*" if pips_str else ""

    # FIX 2026-04-10: Solo mostrar TPs válidos (dirección correcta y no basura)
    def _tp_valid(tval):
        if tval <= 0:
            return False
        if entry > 0:
            if abs(tval - entry) / entry > 0.20:
                return False  # >20% = basura
            if direction == "BUY" and tval < entry:
                return False
            if direction == "SELL" and tval > entry:
                return False
        return True

    _tp1_val = signal.get("tp", 0) or 0
    tp2 = signal.get("tp2", 0) or 0
    tp3 = signal.get("tp3", 0) or 0
    tp4 = signal.get("tp4", 0) or 0
    tp5 = signal.get("tp5", 0) or 0

    _valid_display = []
    if _tp_valid(_tp1_val): _valid_display.append(("TP1", _tp1_val))
    if _tp_valid(tp2): _valid_display.append(("TP2", tp2))
    if _tp_valid(tp3): _valid_display.append(("TP3", tp3))
    if _tp_valid(tp4): _valid_display.append(("TP4", tp4))
    if _tp_valid(tp5): _valid_display.append(("TP5", tp5))

    # FIX 2026-04-17: Marcar ✅ SOLO TPs realmente alcanzados (los demás ⏳ pendientes)
    # Para BUY: hit si tpn <= precio_alcanzado (tp final)
    # Para SELL: hit si tpn >= precio_alcanzado
    def _tp_hit(tpn_val, tp_reached):
        if tp_reached <= 0 or tpn_val <= 0:
            return False
        if direction == "BUY":
            return tpn_val <= tp_reached + 0.0001
        else:
            return tpn_val >= tp_reached - 0.0001

    tp_lines = ""
    if len(_valid_display) > 1:
        for _label, _val in _valid_display:
            _mark = "✅" if _tp_hit(_val, tp) else "⏳"
            tp_lines += f"\n{_mark} {_label}: {fmt(_val)}"
    elif len(_valid_display) == 1:
        _mark = "✅" if _tp_hit(_valid_display[0][1], tp) else "⏳"
        tp_lines = f"\n{_mark} {_valid_display[0][0]}: {fmt(_valid_display[0][1])}"
    elif tp > 0:
        tp_lines = f"\n✅ TP: {fmt(tp)}"

    _dir_label_es = "BUY" if direction.upper() == "BUY" else "SELL"
    # FIX 2026-04-30: nota de transparencia si la señal NO tenia SL definido y por
    # tanto no se pudo validar SL-FIRST (caso GBP/JPY 30/04 — sl=0 del parser).
    _no_sl_note = ""
    if signal.get("_tp_sin_sl_warning"):
        _no_sl_note = "\n_(SL was not specified — TP-first not validated)_"
    # FIX 2026-04-26: traducido a INGLES (mensaje al canal VIP cuando se alcanza TP)
    msg = (
        f"🎯🎯 *TP HIT* 🎯🎯\n"
        f"━━━━━━━━━━━━━━\n"
        f"{dir_emoji} *{_dir_label_es} — {pair_d}*\n\n"
        f"📍 Entry: {fmt(entry)}"
        f"{tp_lines}"
        f"{pips_line}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🚀 _BuySell365 Pro — winning trade_"
        f"{_no_sl_note}"
    ) + ELI_SIG

    # FIX 2026-04-22: pasar sent_at para que el chart cubra el movimiento real de la señal
    # FIX 2026-04-30: pasar sl + tp_hit_label para chart inteligente (recortado + SL + flecha HIT)
    _chart_sent_at = signal.get("sent_at", 0) or signal.get("timestamp", 0) or 0
    _sl_chart = signal.get("sl", 0) or 0
    # Determinar etiqueta del TP alcanzado (TP1, TP2, etc.) para flecha
    _tp_hit_idx = (signal.get("_tp_idx", 0) or 0) + 1
    _tp_hit_label_chart = f"TP{_tp_hit_idx}" if len(_valid_display) > 1 else "TP"
    chart_bytes = _fetch_chart_image(pair, direction, entry, tp,
                                     tp_levels=_valid_display if len(_valid_display) > 1 else None,
                                     signal_sent_at=_chart_sent_at,
                                     sl=_sl_chart,
                                     tp_hit_label=_tp_hit_label_chart)

    # 2026-05-09: video al grupo eliminado por preferencia del usuario.
    # Tanto VIP como grupo publico reciben FOTO con el chart_bytes.
    # El watermark diagonal funciona mejor en imagen y reduce coste ffmpeg.

    # FIX 2026-05-15: guard chokepoint para celebracion TP
    msg = _safe_publish_vip(msg, kind="tp_hit", pair=pair, direction=dir_label) or msg
    if not msg or len(str(msg).strip()) < 10:
        log.error(f"🚫 TP celebration {pair} abortada por guard (texto invalido)")
        return
    try:
        # VIP recibe FOTO (no video) — como siempre
        if chart_bytes:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            payload = {"chat_id": CHANNEL_ID, "caption": msg, "parse_mode": "Markdown"}
            if reply_to_msg_id:
                payload["reply_to_message_id"] = reply_to_msg_id
            resp = requests.post(url, data=payload,
                files={"photo": ("chart.png", chart_bytes, "image/png")}, timeout=20)
            if resp.status_code == 400 and "message to be replied" in resp.text:
                payload.pop("reply_to_message_id", None)
                resp = requests.post(url, data=payload,
                    files={"photo": ("chart.png", chart_bytes, "image/png")}, timeout=20)
        else:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": CHANNEL_ID, "text": msg, "parse_mode": "Markdown"}
            if reply_to_msg_id:
                payload["reply_to_message_id"] = reply_to_msg_id
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 400 and "message to be replied" in resp.text:
                payload.pop("reply_to_message_id", None)
                resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info(f"🎉 TP CELEBRATION enviada al VIP (foto): {dir_label} {pair}")
            # FIX 2026-05-01: events_log para auditoria
            try:
                from events_log import log_event as _log_event
                _log_event("signal.tp_hit", source="copier", data={
                    "pair": pair, "direction": direction, "entry": entry,
                    "tp": tp, "tp_num": (signal.get("_tp_idx", 0)) if isinstance(signal, dict) else 0,
                })
            except Exception:
                pass
            # FIX 2026-05-10: ENVIAR TAMBIEN A WHATSAPP — antes _send_tp_celebration
            # solo enviaba a VIP+grupo+IG pero no disparaba notify_tp_hit. Resultado:
            # cuando reconcile o el monitor llamaban esta funcion, WhatsApp no se
            # enteraba. notify_tp_hit tiene su propio dedup (5 min) asi que es seguro
            # llamarlo aqui aunque otro path lo dispare tambien.
            try:
                from whatsapp_notifier import notify_tp_hit as _wsp_tp_hit_cel
                # tp_lvl = 1, 2, 3 segun _tp_idx + 1
                _tp_lvl_wsp = (signal.get("_tp_idx", 0) or 0) + 1
                # Convertir pips_won (raw price diff) a display pips segun activo
                _pair_up = (pair or "").upper()
                if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
                    _pips_disp = pips_won * 10
                elif "JPY" in _pair_up:
                    _pips_disp = pips_won * 100
                elif entry >= 100:
                    _pips_disp = pips_won  # indices/crypto: ya son puntos directos
                else:
                    _pips_disp = pips_won * 10000
                _wsp_tp_hit_cel(pair, _tp_lvl_wsp, _pips_disp)
                log.info(f"📱 WhatsApp TP HIT disparado: {pair} TP{_tp_lvl_wsp} +{_pips_disp:.0f} pips")
            except Exception as _e_wsp_tp:
                log.debug(f"WhatsApp TP notify error: {_e_wsp_tp}")
        else:
            log.warning(f"Celebration VIP send error: {resp.status_code} {resp.text[:80]}")
    except Exception as e:
        log.warning(f"Celebration send error: {e}")

    # FIX 2026-04-07: También celebrar en el grupo público (marketing)
    _was_gifted = _is_gifted_signal(pair)
    if GROUP_ID and str(GROUP_ID) != str(CHANNEL_ID):
        import random

        # FIX 2026-04-21: GRUPO PÚBLICO solo muestra el TP ALCANZADO, NUNCA los TPs
        # pendientes (⏳). Eso es contenido VIP — los próximos niveles son parte del
        # producto pagado y no deben filtrarse al grupo gratuito.
        # FIX 2026-05-08: usar _tp_idx (mismo que VIP) en vez de _tp_hit recalculado.
        # Antes el público mostraba "TP2: 4724" cuando el VIP mostraba "TP1: 4726"
        # porque _tp_hit recalculaba desde el `tp` pasado y a veces capturaba más
        # TPs que los del idx secuencial. Ahora ambos canales usan _tp_idx → labels
        # consistentes entre VIP y público para la misma señal.
        _tp_idx_publico = (signal.get("_tp_idx", 0) or 0) + 1
        if _valid_display and _tp_idx_publico <= len(_valid_display):
            _hit_lbl, _hit_val = _valid_display[_tp_idx_publico - 1]
            tp_lines_publico = f"\n✅ {_hit_lbl}: {fmt(_hit_val)}"
        elif tp > 0:
            tp_lines_publico = f"\n✅ TP: {fmt(tp)}"
        else:
            tp_lines_publico = ""

        if _was_gifted:
            # ── CELEBRATION for FREE signals (gifted to public group) ──
            # FIX 2026-04-28: celebracion EPICA — la regalo es nuestro mejor cebo
            # de marketing. Cuando toca TP, lo amplificamos con emojis, hype, y
            # llamado a accion fuerte para conversion VIP. Mensaje en INGLES.
            import random as _rnd_hype
            _hype_intros = [
                "🎁🚀 *FREE SIGNAL = PURE WIN* 🚀🎁",
                "🎁🔥 *TODAY'S FREE PICK SMASHED IT* 🔥🎁",
                "🎁💎 *THE GIFT KEEPS PAYING* 💎🎁",
                "🎁⚡ *GIFTED → TP HIT → WIN* ⚡🎁",
                "🎁🏆 *FREE SIGNAL — ANOTHER TP NAILED* 🏆🎁",
            ]
            _hype_outros = [
                "💯 *This is what VIP looks like — every single day.*",
                "💯 *Imagine getting 8-12 of these EVERY day.*",
                "💯 *And this is just 1 of the FREE ones. The VIP gets ALL.*",
                "💯 *Free clients won 1. VIP clients won 8 today.*",
                "💯 *Track record speaks louder than promises.*",
            ]
            # FIX 2026-05-04 (#4): banner SIEMPRE visible en grande
            # "ESTA SENAL FUE GRATIS Y YA TUVO TP" como pidio el usuario
            # 2026-05-09: caption gift refinado — orden mejor, mas conversion.
            _stats_block = _get_weekly_stats_block()
            _msg_grupo = (
                f"🎁🎁🎁🎁🎁🎁🎁🎁🎁🎁\n"
                f"🏆 *THIS SIGNAL WAS FREE*\n"
                f"✅ *AND ALREADY HIT TP* ✅\n"
                f"🎁🎁🎁🎁🎁🎁🎁🎁🎁🎁\n"
                f"━━━━━━━━━━━━━━\n"
                f"{_rnd_hype.choice(_hype_intros)}\n\n"
                f"{dir_emoji} *{_dir_label_es} — {pair_d}*\n"
                f"📍 Entry: {fmt(entry)}"
                f"{tp_lines_publico}"
                f"{pips_line}\n"
                f"━━━━━━━━━━━━━━\n"
                f"{_stats_block}\n"
                f"🎁 *This gift was 1 of 8-12 signals today.*\n"
                f"🏆 *VIPs got the OTHER ones in real-time.*\n\n"
                f"{_rnd_hype.choice(_hype_outros)}\n\n"
                f"🔥 *Want EVERY signal as it happens?*\n"
                f"👉 Type */vip* — no upsells, no tricks"
            )
            log.info(f"🎁🎯 GIFT TP CELEBRATION (EPIC): {pair_d} {pips_str}")
            # FIX 2026-05-04: persistir resultado para resumen semanal viernes
            try:
                _gift_pips_num = abs(float(pips or 0))
                _update_gift_history_result(pair, "tp", _gift_pips_num)
            except Exception as _e_gh_up:
                log.debug(f"gift_history update tp err: {_e_gh_up}")
            # FIX 2026-04-23: registrar resultado "tp" en gift_tracker para resumen diario
            # FIX 2026-05-05: también guardar pips, hora, direction, tp_level (antes no se guardaban)
            try:
                from datetime import datetime as _dt_gt
                _hora_gt = _dt_gt.now().strftime("%H:%M")
                # pips_won es raw price diff — aplicar factor display igual que pips_str
                _pips_disp_gt = round(abs(pips_won or 0), 4)
                _unit_gt = "pips"
                if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
                    _pips_disp_gt = round(_pips_disp_gt * 10, 1)
                    _unit_gt = "pips"
                elif "JPY" in pair.upper():
                    _pips_disp_gt = round(_pips_disp_gt * 100, 1)
                elif entry >= 100:
                    _pips_disp_gt = round(_pips_disp_gt, 1)
                    _unit_gt = "pts"
                else:
                    _pips_disp_gt = round(_pips_disp_gt * 10000, 1)
                _tp_lv_gt = f"TP{_cel_lvl}" if _cel_lvl > 0 else "TP"
                with _gift_lock:
                    _is_gold_g = pair.upper() in ("GOLD", "XAUUSD", "XAUUSD=X")
                    if _is_gold_g:
                        _gift_tracker["gold_result"] = "tp"
                        _gift_tracker["gold_pips"] = _pips_disp_gt
                        _gift_tracker["gold_hora"] = _hora_gt
                        _gift_tracker["gold_direction"] = direction.upper()
                        _gift_tracker["gold_tp_level"] = _tp_lv_gt
                        _gift_tracker["gold_unit"] = _unit_gt
                    else:
                        _gift_tracker["other_result"] = "tp"
                        _gift_tracker["other_pips"] = _pips_disp_gt
                        _gift_tracker["other_hora"] = _hora_gt
                        _gift_tracker["other_direction"] = direction.upper()
                        _gift_tracker["other_tp_level"] = _tp_lv_gt
                        _gift_tracker["other_unit"] = _unit_gt
                    _save_gift_tracker()
            except Exception as _egt:
                log.debug(f"gift_tracker tp update error: {_egt}")
        else:
            # 2026-05-09: caption con marketing fuerte para conversion VIP.
            # Antes: solo "winning trade" sin CTA. Ahora: bloque CTA completo.
            _stats_block = _get_weekly_stats_block()
            _msg_grupo = (
                f"🎯🎯 *TP HIT* 🎯🎯\n"
                f"━━━━━━━━━━━━━━\n"
                f"{dir_emoji} *{_dir_label_es} — {pair_d}*\n\n"
                f"📍 Entry: {fmt(entry)}"
                f"{tp_lines_publico}"
                f"{pips_line}\n"
                f"━━━━━━━━━━━━━━\n\n"
                f"🏆 *Another VIP winner just closed*\n"
                f"📊 _Our VIPs got this in real-time. The market doesn't wait._\n"
                f"{_stats_block}\n"
                f"💎 *VIP = ALL signals + alerts + WhatsApp*\n"
                f"🔥 *Limited spots — VIP closes monthly*\n\n"
                f"👉 *Type /vip to join now*\n\n"
                f"━━━━━━━━━━━━━━\n"
                f"🚀 _BuySell365 Pro — winning trade_"
            )
        # 2026-05-09: video eliminado, siempre FOTO al grupo.
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
            log.info(f"📢 TP celebration (foto) enviada al GRUPO: {dir_label} {pair}")
        except Exception as _eg:
            log.warning(f"Error enviando TP al grupo: {_eg}")

    # ── Instagram: carrusel (celebración + gráfico) + Reel + Highlight "TPs" ──
    # FIX 2026-04-17: Throttle + circuit breaker anti-spam flag.
    # Instagram bloqueó 3 veces hoy por "feedback_required" (spam). Política nueva:
    #   · cooldown 15 min entre posts TP
    #   · circuit breaker: tras feedback_required, pausa 2h todos los posts IG
    if _check_ig_rate_limit("tp_post"):
        try:
            _chart_file = None
            if chart_bytes:
                _chart_dir = Path(__file__).parent / "ig_images"
                _chart_dir.mkdir(exist_ok=True)
                _chart_file = _chart_dir / f"chart_{pair}_{int(time.time())}.jpg"
                from PIL import Image as _PILImg
                import io as _io_chart
                _png_img = _PILImg.open(_io_chart.BytesIO(chart_bytes))
                _jpg_img = _png_img.convert("RGB")
                _jpg_img.save(str(_chart_file), "JPEG", quality=95)
            from instagram_poster import post_tp_celebration as _ig_post_tp
            from datetime import datetime as _dt_ig
            _ts_open = signal.get("timestamp", 0) or signal.get("opened_at", 0) or 0
            _he_tp = _dt_ig.fromtimestamp(_ts_open).strftime("%H:%M") if _ts_open else ""
            _now_tp = _dt_ig.now()
            # FIX 2026-04-26: NUNCA pasar fuente del copy a IG (regla canal VIP)
            _ig_post_tp(pair_d, direction, entry, tp, pips_str if pips_str else "+0",
                        source="", chart_path=_chart_file,
                        reel_entry=entry, reel_tp=tp, is_gift=_was_gifted,
                        fecha=_now_tp.strftime("%d/%m/%Y"),
                        hora_entrada=_he_tp,
                        hora_salida=_now_tp.strftime("%H:%M"))
            _mark_ig_post_sent("tp_post")
        except Exception as _ig_err:
            _ig_errstr = str(_ig_err).lower()
            if "feedback_required" in _ig_errstr or "spam" in _ig_errstr:
                _trigger_ig_circuit_breaker()
                log.warning(f"🚨 IG feedback_required — circuit breaker 2h activado: {_ig_err}")
            else:
                log.debug(f"Instagram TP post skip: {_ig_err}")
    else:
        log.info(f"🔕 Instagram TP skip ({pair_d}) — cooldown/circuit breaker activo")


def _send_close_celebration(pair: str, direction: str, action: str, pips: float,
                            entry: float = 0, source: str = "") -> None:
    """Celebra cierres parciales/totales con ganancia → VIDEO al GRUPO + Instagram.
    Se llama cuando close_half, close_partial o full_close tienen pips > 0."""
    import requests
    import random

    # FIX 2026-04-16: Si no hay dirección, no celebrar (evita label "VENTA" incorrecto)
    if not direction:
        log.warning(f"Close celebration skipped — no direction for {pair}")
        return

    pair_d = _get_display_pair(pair)
    dir_emoji = "🟢" if direction.upper() == "BUY" else "🔴"
    _dir_label_es = "BUY" if direction.upper() == "BUY" else "SELL"

    # Formatear pips según activo
    # FIX 2026-04-30: GOLD usa "pips" (alineado con _record_close_result FIX 2026-04-27).
    # FIX 2026-05-04: _pips ya llega en display-pips (Fix 1+2 hicieron *10 en origen).
    # REVERTIDO el *10 aquí para evitar doble multiplicacion (+800 vs +80).
    _p_up = (pair or "").upper()
    if pair in ("GOLD", "XAUUSD", "XAUUSD=X") or "XAU" in _p_up:
        pips_str = f"+{pips:.0f} pips" if pips >= 0.1 else f"+{pips:.1f} pips"  # FIX 2026-05-05: sin *10
    elif any(x in _p_up for x in ("US30", "NAS100", "US100", "US500", "SP500", "DOW")):
        pips_str = f"+{pips:.0f} pts" if pips >= 1 else f"+{pips:.1f} pts"
    else:
        pips_str = f"+{pips:.0f} pips"

    # Labels según tipo de cierre
    if action == "close_half":
        # FIX 2026-04-26: traducido a INGLES — todo el sistema EN
        title_emoji = "⚡⚡"
        title_text = "PROFITS LOCKED IN"
        action_line = "⚡ Partial close 50%"
        subtitle = "protecting profits"
    elif action == "close_partial":
        title_emoji = "⚡⚡"
        title_text = "PROFITS LOCKED IN"
        action_line = "⚡ Partial close"
        subtitle = "protecting profits"
    else:  # full_close
        title_emoji = "🔒🔒"
        title_text = "CLOSED IN PROFIT"
        action_line = "✅ Full close"
        subtitle = "winning trade"

    entry_line = f"\n📍 Entry: {fmt_price(entry)}" if entry > 0 else ""

    _was_gifted = _is_gifted_signal(pair)
    if _was_gifted:
        # ── CELEBRATION for FREE signals (gifted to public group) — EN ──
        # FIX 2026-05-04 (#4): banner SIEMPRE visible en grande
        # "ESTA SENAL FUE GRATIS Y YA TUVO TP" como pidio el usuario
        # 2026-05-09: cierre gift refinado — comparativa free vs VIP mas clara.
        _stats_block = _get_weekly_stats_block()
        _msg_grupo = (
            f"🎁🎁🎁🎁🎁🎁🎁🎁🎁🎁\n"
            f"🏆 *FREE SIGNAL CLOSED IN PROFIT* ✅\n"
            f"🎁🎁🎁🎁🎁🎁🎁🎁🎁🎁\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎁🎯 *YOUR FREE GIFT JUST PAID* 🎯🎁\n\n"
            f"{dir_emoji} *{_dir_label_es} — {pair_d}*"
            f"{entry_line}\n"
            f"{action_line}\n"
            f"💰 Profit: *{pips_str}*\n"
            f"━━━━━━━━━━━━━━\n"
            f"{_stats_block}\n"
            f"📊 *Free user got 1. VIPs got more today.*\n"
            f"💎 *Imagine getting 8-12 winners like this EVERY day.*\n\n"
            f"🔥 *VIP = same trade, real-time, 24/7*\n"
            f"👉 *Type /vip to upgrade*"
        )
        log.info(f"🎁🎯 GIFT CLOSE CELEBRATION: {pair_d} {pips_str}")
        # FIX 2026-05-04: persistir resultado para resumen semanal viernes
        try:
            _gift_pips_close = abs(float(pips or 0))
            _update_gift_history_result(pair, "full_close", _gift_pips_close)
        except Exception as _e_gh_close:
            log.debug(f"gift_history update close err: {_e_gh_close}")
        # FIX 2026-04-23: registrar resultado "tp" en gift_tracker para resumen diario
        # FIX 2026-05-05: también guardar pips, hora, direction (antes no se guardaban)
        try:
            from datetime import datetime as _dt_gtc
            _hora_gtc = _dt_gtc.now().strftime("%H:%M")
            # pips llega ya en display-pips (Fix 1+2). Sin *10 extra.
            _pips_disp_gtc = round(abs(float(pips or 0)), 1)
            _p_up_gc = (pair or "").upper()
            if "XAU" in _p_up_gc or pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
                _unit_gtc = "pips"
            elif any(x in _p_up_gc for x in ("US30", "NAS100", "US100", "US500", "SP500", "DOW")):
                _unit_gtc = "pts"
            else:
                _unit_gtc = "pips"
            with _gift_lock:
                _is_gold_gc = pair.upper() in ("GOLD", "XAUUSD", "XAUUSD=X")
                if _is_gold_gc:
                    _gift_tracker["gold_result"] = "tp"
                    _gift_tracker["gold_pips"] = _pips_disp_gtc
                    _gift_tracker["gold_hora"] = _hora_gtc
                    _gift_tracker["gold_direction"] = direction.upper()
                    _gift_tracker["gold_unit"] = _unit_gtc
                else:
                    _gift_tracker["other_result"] = "tp"
                    _gift_tracker["other_pips"] = _pips_disp_gtc
                    _gift_tracker["other_hora"] = _hora_gtc
                    _gift_tracker["other_direction"] = direction.upper()
                    _gift_tracker["other_unit"] = _unit_gtc
                _save_gift_tracker()
        except Exception as _egc:
            log.debug(f"gift_tracker close tp update error: {_egc}")
    else:
        # 2026-05-09: cierre normal con marketing fuerte para conversion VIP.
        # Antes: solo titulo + datos. Ahora: bloque CTA al final.
        _stats_block = _get_weekly_stats_block()
        _msg_grupo = (
            f"{title_emoji} *{title_text}* {title_emoji}\n"
            f"━━━━━━━━━━━━━━\n"
            f"{dir_emoji} *{_dir_label_es} — {pair_d}*"
            f"{entry_line}\n"
            f"{action_line}\n"
            f"💰 Profit: *{pips_str}*\n"
            f"━━━━━━━━━━━━━━\n\n"
            f"🏆 *Another VIP trade closed in profit*\n"
            f"📊 _While you wait, we're already opening the next one._\n"
            f"{_stats_block}\n"
            f"💎 *VIP = signals 24/7 + alerts in your phone*\n"
            f"👉 *Type /vip to join the next winner*\n\n"
            f"🚀 _BuySell365 Pro — {subtitle}_"
        )

    # Calcular TP aproximado para el video (entry +/- pips)
    tp_approx = 0
    if entry > 0:
        if pair in ("GOLD", "XAUUSD", "XAUUSD=X") or entry >= 100:
            tp_approx = entry + pips if direction.upper() == "BUY" else entry - pips
        elif "JPY" in pair.upper():
            tp_approx = entry + pips / 100 if direction.upper() == "BUY" else entry - pips / 100
        else:
            tp_approx = entry + pips / 10000 if direction.upper() == "BUY" else entry - pips / 10000

    # Obtener gráfica real para el video
    # FIX 2026-05-01: pasar title_override correcto segun action — antes el chart
    # decia "TP REACHED" en TODA celebracion (incluso partial close) → engañoso al
    # suscriptor (vee "TP REACHED" cuando solo fue cierre parcial).
    _chart_title_map = {
        "close_half":    f"💰 PARTIAL CLOSE 50% — {direction.upper()} {pair_d}",
        "close_partial": f"💰 PARTIAL CLOSE — {direction.upper()} {pair_d}",
        "full_close":    f"🔒 FULL CLOSE — {direction.upper()} {pair_d}",
    }
    _chart_title = _chart_title_map.get(action, "")
    # FIX 2026-05-01: pasar tp_hit_label="CLOSE" para que el chart se recorte
    # (no muestre 200 velas con drawdown post-cierre que confunda al suscriptor).
    _close_chart = _fetch_chart_image(pair, direction, entry, tp_approx,
                                       title_override=_chart_title,
                                       tp_hit_label="CLOSE") if entry > 0 else None

    # ── CIERRE al GRUPO (siempre FOTO, video eliminado 2026-05-09) ──
    # FIX 2026-05-05: señales REGALADAS ya fueron celebradas en el grupo desde
    # _send_tp_celebration (mensaje épico "THIS SIGNAL WAS FREE"). No mandar
    # un segundo (y tercer) mensaje de close al grupo — era la causa del spam.
    if GROUP_ID and str(GROUP_ID) != str(CHANNEL_ID) and not _was_gifted:
        # 2026-05-09: video eliminado por preferencia del usuario.
        # Fallback en cascada: foto (si tenemos _close_chart) -> texto puro.
        _photo_sent = False
        if _close_chart:
            try:
                _url_gp = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                _resp_gp = requests.post(
                    _url_gp,
                    data={
                        "chat_id": GROUP_ID,
                        "caption": _msg_grupo,
                        "parse_mode": "Markdown",
                    },
                    files={"photo": ("close.png", _close_chart, "image/png")},
                    timeout=20,
                )
                if _resp_gp.status_code == 200:
                    _photo_sent = True
                    log.info(f"📢 CLOSE CELEBRATION (foto) al GRUPO: {action} {pair_d} {pips_str}")
            except Exception as _ep:
                log.debug(f"Foto close celebration error: {_ep}")
        if not _photo_sent:
            try:
                _url_g = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                _pay_g = {"chat_id": GROUP_ID, "text": _msg_grupo, "parse_mode": "Markdown"}
                requests.post(_url_g, json=_pay_g, timeout=10)
                log.info(f"📢 CLOSE CELEBRATION (texto) al GRUPO: {action} {pair_d} {pips_str}")
            except Exception as _eg:
                log.warning(f"Error enviando close celebration al grupo: {_eg}")

    # ── Instagram: Reel + Highlight "TPs" ──
    # FIX 2026-04-17: respetar rate-limit + circuit breaker
    if _check_ig_rate_limit("close_post"):
        try:
            from instagram_poster import post_tp_celebration as _ig_post_tp
            from datetime import datetime as _dt_ig_cl
            _now_cl = _dt_ig_cl.now()
            # FIX 2026-04-26: NUNCA pasar fuente del copy a IG (regla canal VIP)
            _ig_post_tp(pair_d, direction, entry if entry > 0 else 0,
                        tp_approx, pips_str, source="",
                        reel_entry=entry if entry > 0 else 0,
                        reel_tp=tp_approx,
                        fecha=_now_cl.strftime("%d/%m/%Y"),
                        hora_salida=_now_cl.strftime("%H:%M"))
            _mark_ig_post_sent("close_post")
        except Exception as _ig_err:
            _ig_errstr = str(_ig_err).lower()
            if "feedback_required" in _ig_errstr or "spam" in _ig_errstr:
                _trigger_ig_circuit_breaker()
                log.warning(f"🚨 IG feedback_required (close) — CB 2h: {_ig_err}")
            else:
                log.debug(f"Instagram close celebration skip: {_ig_err}")
    else:
        log.info(f"🔕 Instagram close skip ({pair_d}) — cooldown/CB activo")

    log.info(f"🎉 Close celebration completa: {action} {pair_d} {pips_str}")


def _build_sl_motivational_msg(pair_d: str, pips_lost_str: str = "", tps_previos: list = None, cierres_previos: list = None, pair: str = "") -> str:
    """Construye el mensaje motivacional de SL que se publica al canal VIP.

    FIX 2026-04-21: reemplaza el mensaje antiguo "SL tocado. Operación cerrada.
    BuySell365 Pro 💪" por un formato que mantiene transparencia pero añade
    contexto estadístico (WR del día, net del día) para que los suscriptores
    entiendan que un SL aislado no define el resultado global.

    Si tps_previos existe, genera variante "CIERRE TOTAL — X TPs asegurados antes".

    FIX 2026-05-08: nuevo parámetro `pair` (raw symbol) para aplicar correctamente
    el multiplicador x10 (oro), x100 (JPY) en pips_secured. Antes mostraba "+2 pips
    secured" cuando el TP HIT había dicho "+31 pips" — inconsistencia clara.
    """
    # FIX 2026-05-08: factor de multiplicación según par para "pips secured"
    _pair_upper = (pair or "").upper().replace("/", "")
    if _pair_upper in ("GOLD", "XAUUSD", "XAUUSDX", "XAUUSD=X", "ORO"):
        _pip_mult = 10
    elif "JPY" in _pair_upper:
        _pip_mult = 100
    elif _pair_upper in ("SILVER", "XAGUSD", "XAGUSDX", "XAGUSD=X", "PLATA"):
        _pip_mult = 10
    else:
        _pip_mult = 1  # forex usa pips raw o pts
    # Calcular WR y Net del día desde _daily_results (tracker en memoria)
    _wr_line = ""
    _net_line = ""
    try:
        with _daily_results_lock:
            _tps_hoy = [r for r in _daily_results if r.get("result") == "tp"]
            _sls_hoy = [r for r in _daily_results if r.get("result") == "sl"]
            _pips_tp = sum(r.get("pips_numeric", 0) for r in _tps_hoy)
            _pips_sl = sum(r.get("pips_numeric", 0) for r in _sls_hoy)
        _decididas = len(_tps_hoy) + len(_sls_hoy)
        if _decididas > 0:
            _wr = round(len(_tps_hoy) / _decididas * 100)
            _wr_line = f"   ✅ Win Rate today: *{_wr}%*\n"
        _net = _pips_tp - _pips_sl
        if abs(_net) > 0:
            _sign = "+" if _net >= 0 else ""
            _net_line = f"   ✅ Net today: *{_sign}{_net:.0f} pts/pips*\n"
    except Exception:
        pass

    # FIX 2026-05-11 (revision tarde): mensajes SL ultra compactos.
    # Usuario pidio "demasiado texto", quitamos stats line (WR+Net van en resumen diario).
    # Antes 3-4 lineas; ahora 1-2 lineas + ELI_SIG.

    # FIX 2026-05-06: Si se indicó cierre parcial/manual ANTES del SL → mensaje especial
    if cierres_previos:
        _cierre_lines = " · ".join(
            f"+{abs(c.get('pips', 0)) * _pip_mult:.0f} pips secured" for c in cierres_previos
        )
        return (
            f"🛡️ *MANAGED CLOSE — {pair_d}*\n"
            f"✅ {_cierre_lines} · then SL{pips_lost_str}"
        )

    if tps_previos:
        _tps_lines = " · ".join(f"TP{t['nivel']}: +{abs(t.get('pips', 0)) * _pip_mult:.0f}" for t in tps_previos)
        return (
            f"🏁 *TOTAL CLOSE — {pair_d}*\n"
            f"✅ {_tps_lines} pips · rest SL{pips_lost_str}"
        )

    return f"🛡️ *SL — {pair_d}*{pips_lost_str}"


def _send_sl_notification(signal: dict, reply_to_msg_id: int = None) -> None:
    """Notify channel that SL was hit — same professional model as TP HIT.
    FIX 2026-04-17: Si la señal tocó TPs antes, mostrar el NETO real (no solo pérdida).
    FIX 2026-05-11: cooldown 3 min por par — si llegan 3 SL de ORO en 30s solo pasa el primero."""
    import requests

    # ── DEDUP SL notification ──
    # FIX 2026-05-13: clave por ticket/sig_id (unico por trade) + ventana 4h.
    # Caso 13-may: BTC -192 pts publicado a 17:42 y a 18:30 (gap 48 min,
    # superaba cooldown de 3 min). Ahora cada trade solo puede notificar SL
    # una vez en 4h. Si no hay ticket/sig_id, fallback a pair+direction.
    _sl_ticket = signal.get("mt5_ticket") or signal.get("_mt5_ticket") or 0
    _sl_sig_id = signal.get("sig_id") or ""
    _sl_dir = (signal.get("direction") or "").upper()
    if _sl_ticket:
        _sl_cd_key = f"sl_notif_t{_sl_ticket}"
    elif _sl_sig_id:
        _sl_cd_key = f"sl_notif_s{_sl_sig_id}"
    else:
        # FIX 2026-05-21 (Bug B): incluir entry en la key fallback para evitar
        # colision cuando hay multiples senales mismo par+direction en 4h.
        # Caso 21-may: 2 BUY ORO (16:11 y 16:45) ambos hit SL. Sin ticket en
        # signal dict, ambas usaban key "sl_notif_ORO_BUY" → segunda silenciada
        # por dedup de 4h.
        _sl_entry_key = signal.get("mt5_entry") or signal.get("entry") or 0
        _sl_cd_key = f"sl_notif_{signal.get('pair', '')}_{_sl_dir}_{_sl_entry_key:.4f}"
    _sl_cd_prev = _recently_notified.get(_sl_cd_key, 0)
    _sl_cd_now = time.time()
    if _sl_cd_now - _sl_cd_prev < 14400:  # 4 horas
        log.info(f"🔕 SL notification duplicada ignorada {_sl_cd_key} "
                 f"(ya notificado hace {_sl_cd_now - _sl_cd_prev:.0f}s, ventana 4h)")
        return
    _recently_notified[_sl_cd_key] = _sl_cd_now
    _save_notif_dedup()  # persistir tras restart

    # FIX 2026-05-12 P0.1: verificar trade real en MT5 antes de publicar SL
    # FIX 2026-05-12 (B): si RETRY, liberar dedup y devolver — caller decide retry.
    _exists_sl, _reason_sl = _verify_mt5_trade_exists(signal, context="sl_notification")
    if not _exists_sl:
        if isinstance(_reason_sl, str) and _reason_sl.startswith("RETRY:"):
            log.warning(
                f"⏸️ SL notification DIFERIDA — {signal.get('pair','?')} ({_reason_sl}). "
                f"Liberando cooldown para reintento."
            )
            _recently_notified.pop(_sl_cd_key, None)
            return
        log.warning(
            f"🚫 SL notification BLOQUEADA — {signal.get('pair','?')} ({_reason_sl}). "
            f"No publicando SL fantasma."
        )
        return

    # FIX 2026-05-11 (tarde-3): cooldown global por par para canal VIP
    if not _can_publish_to_vip(signal.get("pair_display") or signal.get("pair",""), event="sl_hit"):
        return

    direction = signal["direction"]
    pair = signal["pair"]
    # FIX 2026-04-27: usar mt5_entry (precio REAL ejecutado en MT5) cuando exista
    # para calcular pips perdidos honestos respecto a la posicion real.
    entry = signal.get("mt5_entry", 0) or signal["entry"]
    sl = signal["sl"]
    pair_d = _get_display_pair(pair)

    dir_label = direction.upper()
    dir_emoji = "🟢" if direction == "BUY" else "🔴"

    fmt = fmt_price

    # FIX 2026-05-21 (Bug A): si la posicion ya cerro en MT5 y tenemos ticket,
    # usar el PRECIO REAL DE CIERRE de MT5 history en vez del SL publicado.
    # Caso 21-may 19:15: BTC SELL ticket 776590926 entry 77242.45, auto-BE movio
    # SL a entry, cerro en 77244.25 (~BE). El bot anuncio "-392.2 pts" usando el
    # SL original publicado (77634.64) — mensaje falso al cliente VIP.
    # Ahora: si exit real esta significativamente mas cerca del entry que del SL
    # publicado, sobreescribimos sl=exit (pips_lost reflejara la realidad).
    _be_close = False
    _real_exit = 0.0
    _mt5_ticket_sl_msg = (signal.get("mt5_ticket") or signal.get("_mt5_ticket") or 0)
    if _mt5_ticket_sl_msg and entry > 0 and sl > 0:
        try:
            import price_feed as _mt5_sl_h
            if _mt5_sl_h.initialize():
                _hd_sl = _mt5_sl_h.history_deals_get(position=int(_mt5_ticket_sl_msg)) or []
                for _d_sl in _hd_sl:
                    if getattr(_d_sl, "entry", 0) == getattr(_mt5_sl_h, "DEAL_ENTRY_OUT", 1):
                        _real_exit = float(getattr(_d_sl, "price", 0.0) or 0.0)
                        break
            if _real_exit > 0:
                _sl_distance = abs(sl - entry)
                _exit_to_entry = abs(_real_exit - entry)
                # Si exit esta dentro del 30% del camino entry->SL, fue un BE close
                # (auto-BE protegio el trade) — no es un SL real, no fake -X pts.
                if _sl_distance > 0 and _exit_to_entry < _sl_distance * 0.30:
                    log.info(
                        f"🛡️ SL message: BE close detectado {pair_d} ticket={_mt5_ticket_sl_msg} — "
                        f"exit={_real_exit} entry={entry} sl_orig={sl} "
                        f"(exit_dist={_exit_to_entry:.2f} vs sl_dist={_sl_distance:.2f}). "
                        f"Reportando como close at break-even, no SL falso."
                    )
                    sl = _real_exit  # pips_lost ahora refleja exit real (~0)
                    _be_close = True
                elif abs(_real_exit - sl) > _sl_distance * 0.10:
                    # Exit cerro lejos del SL publicado pero tampoco en BE — usar exit real
                    log.info(
                        f"🛡️ SL message: usando exit real MT5 {pair_d} ticket={_mt5_ticket_sl_msg} — "
                        f"exit={_real_exit} sl_publicado={sl}"
                    )
                    sl = _real_exit
        except Exception as _e_sl_real:
            log.debug(f"SL real exit lookup failed: {_e_sl_real}")

    # Calcular pips perdidos en el último segmento (resto de la posición al SL)
    pips_lost = abs(sl - entry) if entry > 0 and sl > 0 else 0

    def _fmt_pips(v, signo=""):
        # FIX 2026-05-01: GOLD usa "pips" con factor x10 (estandar mercado).
        if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
            return f"{signo}{v * 10:.0f} pips"
        elif "JPY" in pair.upper():
            return f"{signo}{v * 100:.0f} pips"
        elif entry >= 100:
            return f"{signo}{v:.1f} pts"
        else:
            return f"{signo}{v * 10000:.0f} pips"

    # FIX 2026-04-17: Verificar si la señal ya tocó TPs antes → mensaje CIERRE NETO
    # FIX 2026-04-21: Mensaje motivacional con WR/Net del día (ya no "SL tocado 💪")
    # FIX 2026-05-06: Verificar si se mandó cierre parcial antes → mensaje "MANAGED CLOSE"
    tps_previos = signal.get("_tps_alcanzados", []) or []
    cierres_previos = signal.get("_cierres_previos", []) or []
    # FIX 2026-05-21 (Bug A): si fue un BE close detectado arriba, usar el
    # builder MANAGED CLOSE con "break-even" en vez del SL normal.
    if _be_close and not tps_previos and not cierres_previos:
        _dir_emo = "🟢" if direction == "BUY" else "🔴"
        msg = (
            f"🛡️ *MANAGED CLOSE — {_dir_emo} {dir_label} {pair_d}*\n"
            f"✅ Trade closed at break-even. Risk fully eliminated."
            + ELI_SIG
        )
    else:
        _pips_str = f" ({_fmt_pips(pips_lost, '-')})" if pips_lost > 0 else ""
        msg = _build_sl_motivational_msg(pair_d, pips_lost_str=_pips_str, tps_previos=tps_previos, cierres_previos=cierres_previos, pair=pair) + ELI_SIG

    # FIX 2026-05-15: guard chokepoint para SL hit
    msg = _safe_publish_vip(msg, kind="sl_hit", pair=pair_d, direction=dir_label) or msg
    if not msg or len(str(msg).strip()) < 10:
        log.error(f"🚫 SL notification {pair_d} abortada por guard (texto invalido)")
        return

    # FIX 2026-04-07: Sin gráfica para SL — solo texto
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHANNEL_ID, "text": msg, "parse_mode": "Markdown"}
        if reply_to_msg_id:
            payload["reply_to_message_id"] = reply_to_msg_id
        resp = requests.post(url, json=payload, timeout=10)
        # FIX: Si falla por reply_to inválido, reintentar sin reply
        if resp.status_code == 400 and "message to be replied" in resp.text:
            payload.pop("reply_to_message_id", None)
            resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info(f"🛑 SL notification enviada: {dir_label} {pair}")
        else:
            log.warning(f"SL notification error: {resp.status_code} {resp.text[:80]}")
    except Exception as e:
        log.warning(f"SL notification error: {e}")

    # ── WhatsApp personal: cierre por SL (solo ORO) ──
    try:
        from whatsapp_notifier import notify_position_closed as _wsp_closed
        _wsp_pips_l = pips_lost * 10 if pair in ("GOLD", "XAUUSD", "XAUUSD=X") else pips_lost
        _wsp_closed(pair, direction, entry, sl, -abs(_wsp_pips_l), reason="SL")
    except Exception as _e_wsp_sl:
        log.debug(f"[WSP-GOLD] notify SL error: {_e_wsp_sl}")

    # 2026-05-09: SL al grupo publico ELIMINADO por preferencia del usuario.
    # Antes (FIX 2026-05-08) se publicaba SL al grupo cuando la senal era gift,
    # buscando "honestidad total" con free users que la tomaron. El usuario
    # decidio anular esto: el grupo publico SOLO ve ganancias.
    # Razon: prioridad marketing/conversion sobre transparencia con free users.
    # SL siguen llegando a: canal VIP, WhatsApp personal, Instagram queda como
    # estaba (Instagram nunca publica SL — feedback_instagram_solo_ganancias.md).


def _send_expired_notification(signal: dict, reason: str = "expired", reply_to_msg_id: int = None) -> None:
    """Notifica al canal el cierre de una señal que NO tocó TP/SL.

    Casos:
      - reason="expired": pasaron >72h sin tocar TP ni SL → cierre por tiempo
      - reason="orphan":  el monitor no pudo obtener precio durante mucho tiempo (broker desconectado)
      - reason="eod":     cierre forzado al final del día para no dejar señales abiertas

    Calcula el neto al precio actual del mercado (si está disponible) y muestra
    los TPs ya alcanzados. FIX 2026-04-21 — antes las señales expiradas se
    eliminaban silenciosamente y quedaban como huérfanas en el canal.
    """
    import requests

    direction = signal.get("direction", "BUY")
    pair = signal.get("pair", "?")
    # FIX 2026-04-27: usar mt5_entry real cuando exista — net contra el precio
    # ACTUAL debe medirse desde donde MT5 entro, no desde el teorico del aliado.
    entry = signal.get("mt5_entry", 0) or signal.get("entry", 0) or 0
    pair_d = _get_display_pair(pair)
    dir_emoji = "🟢" if direction == "BUY" else "🔴"
    _dir_label_es = "BUY" if direction.upper() == "BUY" else "SELL"

    # Precio actual (si lo conseguimos) para calcular neto realista
    _live = None
    try:
        import price_feed as _mt5_e
        _sym_clean = pair.upper().replace("/", "")
        _sym_map = {"GOLD": "GOLD", "XAUUSD": "GOLD", "ORO": "GOLD",
                    "SILVER": "SILVER", "XAGUSD": "SILVER", "PLATA": "SILVER",
                    "NAS100": "US100Cash", "NASDAQ": "US100Cash", "US100": "US100Cash",
                    "US30": "US30Cash", "US500": "US500Cash"}
        _sym = _sym_map.get(_sym_clean, _sym_clean)
        if _mt5_e.initialize():
            _resolved = _sym
            if not _mt5_e.symbol_info(_resolved):
                for _suf in ("m", "c", "i", ".pro", ".raw"):
                    if _mt5_e.symbol_info(f"{_sym}{_suf}"):
                        _resolved = f"{_sym}{_suf}"
                        break
            _mt5_e.symbol_select(_resolved, True)
            _t = _mt5_e.symbol_info_tick(_resolved)
            if _t and _t.bid > 0:
                _live = _t.bid if direction == "BUY" else _t.ask
    except Exception:
        pass
    if _live is None:
        _live = _get_current_price(pair) or 0

    def _fmt_pips(v, signo=""):
        # FIX 2026-05-01: GOLD usa "pips" con factor x10 (estandar mercado).
        if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
            return f"{signo}{v * 10:.0f} pips"
        elif "JPY" in pair.upper():
            return f"{signo}{v * 100:.0f} pips"
        elif entry >= 100:
            return f"{signo}{v:.1f} pts"
        else:
            return f"{signo}{v * 10000:.0f} pips"

    tps_previos = signal.get("_tps_alcanzados", []) or []
    pips_ganados_tps = sum(t.get("pips", 0) for t in tps_previos)

    # Pips del último segmento (al precio actual)
    pips_resto = 0
    if _live > 0 and entry > 0:
        if direction == "BUY":
            pips_resto = _live - entry
        else:
            pips_resto = entry - _live

    # Si tocó TPs antes, neto = pips de TPs + pips_resto del último segmento (positivo o negativo)
    # Si NO tocó TPs, neto = pips_resto solo
    if tps_previos:
        pips_netos = pips_ganados_tps + pips_resto
    else:
        pips_netos = pips_resto

    signo_neto = "+" if pips_netos >= 0 else "-"
    emoji_neto = "✅" if pips_netos >= 0 else "⚠️"

    # FIX 2026-04-26: titulos y mensajes en INGLES
    # FIX 2026-04-28: separar "drift" de "orphan". Antes el drift detection (TP
    # del lado equivocado vs mt5_entry real) reusaba reason="orphan" cuyo titulo
    # era "no broker price" — engañoso al suscriptor (el broker SI respondio).
    _titulo = {
        "expired": "⏱ CLOSED BY TIME",
        "orphan":  "⏱ CLOSE — no broker price",
        "drift":   "⏱ CLOSE — drift detected (slippage too high)",
        "eod":     "🌙 END OF DAY CLOSE",
    }.get(reason, "⏱ CLOSED BY TIME")

    if tps_previos:
        tps_lines = "\n".join(
            f"✅ TP{t['nivel']}: {_fmt_pips(t.get('pips',0),'+')}" for t in tps_previos
        )
        msg = (
            f"{_titulo}\n"
            f"━━━━━━━━━━━━━━\n"
            f"{dir_emoji} *{_dir_label_es} — {pair_d}*\n\n"
            f"📍 Entry: {fmt_price(entry)}\n"
            f"{tps_lines}\n"
            f"📊 Current close: {fmt_price(_live) if _live > 0 else '—'}\n\n"
            f"{emoji_neto} *Net: {_fmt_pips(abs(pips_netos), signo_neto)}*\n"
            f"━━━━━━━━━━━━━━\n"
            f"📊 _BuySell365 Pro — {len(tps_previos)} TP(s) secured before close_"
        ) + ELI_SIG
    else:
        msg = (
            f"{_titulo}\n"
            f"━━━━━━━━━━━━━━\n"
            f"{dir_emoji} *{_dir_label_es} — {pair_d}*\n\n"
            f"📍 Entry: {fmt_price(entry)}\n"
            f"📊 Current close: {fmt_price(_live) if _live > 0 else '—'}\n\n"
            f"{emoji_neto} *Net: {_fmt_pips(abs(pips_netos), signo_neto)}*\n"
            f"━━━━━━━━━━━━━━\n"
            f"📊 _BuySell365 Pro — trade finalized_"
        ) + ELI_SIG

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHANNEL_ID, "text": msg, "parse_mode": "Markdown"}
        if reply_to_msg_id:
            payload["reply_to_message_id"] = reply_to_msg_id
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 400 and "message to be replied" in resp.text:
            payload.pop("reply_to_message_id", None)
            resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info(f"⏱ Cierre {reason} notificado: {direction} {pair} neto={pips_netos:.2f}")
        else:
            log.warning(f"Cierre {reason} error: {resp.status_code} {resp.text[:80]}")
    except Exception as e:
        log.warning(f"Cierre {reason} error: {e}")


def _record_daily_result(signal: dict, result: str) -> None:
    """Registra un TP o SL en el tracker diario para los reportes del grupo."""
    pair = signal.get("pair", "?")
    direction = signal.get("direction", "?")

    # FIX 2026-05-20: usuario quiere que TODAS las senales tracked se cuenten
    # en el daily tracker (W/L) — sin importar si MT5 abrio el trade propio.
    # La consistencia es: si la senal se publico al VIP y el precio toco TP/SL,
    # se cuenta. La proteccion contra senales nacidas muertas la cubre Fix B
    # (no publica si precio ya paso TP1 al momento de publicar).
    # Antes (FIX 2026-05-19): bloqueaba aqui cuando mt5_executed=False —
    # eso causaba que el header de stats no contara la senal aunque el
    # mercado SI tocara TP.
    # FIX 2026-04-27: priorizar mt5_entry (precio real ejecutado) sobre el
    # entry teorico del aliado. El daily tracker alimenta los reportes promo
    # publicos — debe reflejar pips reales, no teoricos.
    entry = signal.get("mt5_entry", 0) or signal.get("entry", 0) or 0
    tp = signal.get("_tp_final", signal.get("tp", 0)) or 0
    sl = signal.get("sl", 0) or 0

    if result == "tp":
        pips_raw = abs(tp - entry) if entry > 0 and tp > 0 else 0
    else:
        pips_raw = abs(sl - entry) if entry > 0 and sl > 0 else 0

    # FIX 2026-04-28: anadidos OIL/BRENT/WTI/USOIL/UKOIL y CRYPTO. Antes el OIL
    # con entry=98.50 caia en la rama Forex (entry<100) y multiplicaba pips_raw
    # x10000 -> SL OIL de 3.30 pts publicaba 33000 "pips" -> Net del dia: -32936.
    # FIX 2026-04-30: GOLD usa "pips" (alineado con _record_close_result y mensaje
    # de celebracion). Antes daily_result decia "pts" para GOLD pero close_result
    # decia "pips" — al sumarlos en _build_promo_report mezclaba unidades.
    # FIX 2026-05-01: GOLD x10 — convencion estandar mercado (1 pip = 0.10 dolar).
    _p_up = pair.upper()
    if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
        pips_str = f"{pips_raw * 10:.0f} pips"
        pips_numeric = pips_raw * 10
        pips_unit = "pips"
    elif any(x in _p_up for x in ("BRENT", "OIL", "WTI", "USOIL", "UKOIL")):
        pips_str = f"{pips_raw * 100:.1f} pts"
        pips_numeric = pips_raw * 100
        pips_unit = "pts"
    elif any(x in _p_up for x in ("BTC", "ETH", "BITCOIN")):
        pips_str = f"{pips_raw:.1f} pts"
        pips_numeric = pips_raw
        pips_unit = "pts"
    elif "JPY" in _p_up:
        pips_str = f"{pips_raw * 100:.0f} pips"
        pips_numeric = pips_raw * 100
        pips_unit = "pips"
    elif entry >= 100:
        pips_str = f"{pips_raw:.1f} pts"
        pips_numeric = pips_raw
        pips_unit = "pts"
    else:
        pips_str = f"{pips_raw * 10000:.0f} pips"
        pips_numeric = pips_raw * 10000
        pips_unit = "pips"

    from datetime import datetime
    import pytz
    tz = pytz.timezone("Europe/Andorra")

    record = {
        "pair": pair,
        "pair_display": _get_display_pair(pair),
        "direction": direction,
        "source": signal.get("source", ""),
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "pips_str": pips_str,
        "pips": pips_numeric,
        "pips_unit": pips_unit,
        "pips_numeric": pips_numeric,
        "result": result,
        "time": time.time(),
        "opened_at": signal.get("timestamp", 0),
        "closed_at": time.time(),
        "fecha": datetime.now(tz).strftime("%d/%m/%Y"),
    }
    # FIX 2026-05-11: persistir score de probabilidad (si la señal lo tenía)
    if signal.get("probability") is not None:
        record["probability"] = signal["probability"]
        record["probability_tech"] = signal.get("probability_tech")
        record["probability_vision"] = signal.get("probability_vision")
        record["probability_source"] = signal.get("probability_source")
    # FIX 2026-05-20: persistir mt5_ticket y sig_id para idempotencia robusta
    # del reconcile orphan. Caso 20-may NZDUSD ticket 765126185: el daily tracker
    # registro el SL via flujo normal pero SIN guardar ticket → el guard orphan
    # (_orphan_tickets_in_stats) no lo reconocio y duplico la entrada como
    # "Canal Aliado" orphan. Con esto el guard puede saltarlo.
    _mt5_tk = signal.get("mt5_ticket") or signal.get("_mt5_ticket")
    if _mt5_tk:
        try:
            record["mt5_ticket"] = int(_mt5_tk)
        except (TypeError, ValueError):
            pass
    _sid = signal.get("sig_id") or signal.get("_sid")
    if _sid:
        record["sig_id"] = str(_sid)
    with _daily_results_lock:
        _daily_results.append(record)

    # FIX 2026-04-15: Persistir a disco (sobrevive reinicios)
    _save_copier_stats(record)
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
        # FIX 2026-04-22: los dicts usan "closed_at" (no "time") — .get() para ambas claves
        results = [r for r in _daily_results if r.get("closed_at", r.get("time", 0)) >= today_start]

    # FIX 2026-04-30: incluir partial closes y full closes con ganancia en TPs.
    # Antes solo contabamos result=="tp" → reportes 12:00/17:00 sub-cuentaban
    # mucho (ej. partial 50% +77pts + full close +246pts NO sumaban). Ahora si.
    _CLOSE_KINDS_PROFIT = ("tp", "close_half", "close_partial", "full_close")
    def _pips(r):
        return r.get("pips_numeric", r.get("pips", 0)) or 0
    tps = [r for r in results if r["result"] in _CLOSE_KINDS_PROFIT and _pips(r) > 0]
    sls = [r for r in results if r["result"] == "sl" or (r["result"] == "full_close" and _pips(r) <= 0)]

    if not tps:
        return None  # No hay ganancias — no publicar

    # FIX 2026-04-27: GOLD ahora usa "pips" (alineado con aliados, antes "pts").
    # FIX 2026-04-30: clasificacion robusta por nombre de pair (los partials tienen
    # entry=0, antes caian todos en "Forex" por la rama entry<100).
    def _is_index(p):
        u = (p or "").upper()
        return any(x in u for x in ("NAS", "US30", "US100", "US500", "SP500", "DOW", "DAX", "FTSE", "NIKKEI"))
    def _is_gold(p):
        return p in ("GOLD", "XAUUSD", "XAUUSD=X") or "XAU" in (p or "").upper()
    # Agrupar pips por tipo: GOLD=pips, indices=pts, forex=pips
    gold_pips_total = sum(_pips(r) for r in tps if _is_gold(r["pair"]))
    index_pts = sum(_pips(r) for r in tps if not _is_gold(r["pair"]) and (_is_index(r["pair"]) or r.get("entry", 0) >= 100))
    forex_pips = sum(_pips(r) for r in tps if not _is_gold(r["pair"]) and not _is_index(r["pair"]) and r.get("entry", 0) < 100)

    # Detalle de cada TP
    tp_lines = ""
    for r in tps:
        dir_emoji = "🟢" if r["direction"] == "BUY" else "🔴"
        tp_lines += f"  {dir_emoji} {r['pair_display']} — *+{r['pips_str']}*\n"

    # Resumen de puntos/pips — FIX 2026-04-27: GOLD = pips (no pts)
    resumen_parts = []
    if gold_pips_total > 0:
        resumen_parts.append(f"🥇 GOLD: *+{gold_pips_total:.0f} pips*")
    if index_pts > 0:
        resumen_parts.append(f"📈 Indices: *+{index_pts:.1f} pts*")
    if forex_pips > 0:
        resumen_parts.append(f"💱 Forex: *+{forex_pips:.0f} pips*")
    resumen = "\n".join(resumen_parts)

    wr = len(tps) / (len(tps) + len(sls)) * 100 if (tps or sls) else 0

    # FIX 2026-04-14: Reporte simplificado — solo ganancias + promo VIP
    # FIX 2026-04-30: traducido a INGLES (audiencia internacional, alineado con
    # el resto del sistema). hora_label sigue parametrizado: "MIDDAY" o "AFTERNOON".
    msg = (
        f"📊📊📊 *{hora_label} REPORT* 📊📊📊\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {hoy}\n\n"
        f"💰 *Today's profits:*\n"
        f"{resumen}\n\n"
        f"🔥 *These profits were LIVE*\n"
        f"Our VIP subscribers received them in real time.\n\n"
        f"👉 Type */vip* and start winning with us\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"_BuySell365 Pro — Real results, real time_"
    )
    return msg


# ═══════════════════════════════════════════════════════════════
# 📅 CALENDARIO ECONÓMICO DIARIO — 7:30 AM hora Andorra
# Genera imagen + caption y lo publica en el canal VIP cada mañana
# ═══════════════════════════════════════════════════════════════

def _generar_imagen_calendario(eventos: list) -> bytes | None:
    """Genera imagen estilo ProSignalsFX con los eventos de alto impacto del día."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        import requests as _req
        from io import BytesIO

        FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        FONT_REG  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        SIZE      = 1080
        BG        = (10, 10, 10)
        GOLD      = (212, 175, 55)
        GOLD_L    = (230, 200, 90)
        WHITE     = (245, 245, 245)

        CURRENCY_CC = {
            "USD":"us","EUR":"eu","GBP":"gb","CAD":"ca","JPY":"jp",
            "AUD":"au","NZD":"nz","CHF":"ch","CNY":"cn","SEK":"se",
            "NOK":"no","DKK":"dk","SGD":"sg","MXN":"mx","BRL":"br",
        }

        def _get_flag(cc, w=68, h=45):
            try:
                r = _req.get(f"https://flagcdn.com/w80/{cc}.png", timeout=5)
                flag = Image.open(BytesIO(r.content)).convert("RGBA")
                return flag.resize((w, h), Image.LANCZOS)
            except:
                return None

        PAD     = 40
        TITLE_H = 160
        HDR_H   = 65
        N       = max(len(eventos), 1)
        ROW_H   = 112
        TABLE_H = HDR_H + N * ROW_H
        IMG_H   = TITLE_H + TABLE_H + PAD

        img = Image.new("RGB", (SIZE, IMG_H), BG)
        d   = ImageDraw.Draw(img)

        f_brand = ImageFont.truetype(FONT_BOLD, 62)
        f_sub   = ImageFont.truetype(FONT_BOLD, 30)
        f_hdr   = ImageFont.truetype(FONT_BOLD, 22)
        f_time  = ImageFont.truetype(FONT_BOLD, 32)
        f_curr  = ImageFont.truetype(FONT_BOLD, 22)
        f_event = ImageFont.truetype(FONT_BOLD, 25)

        # Título
        brand = "BuySell365 Pro"
        bw = d.textlength(brand, font=f_brand)
        d.text(((SIZE-bw)/2, 18), brand, font=f_brand, fill=GOLD)
        sub = "H I G H   I M P A C T   N E W S"
        sw = d.textlength(sub, font=f_sub)
        d.text(((SIZE-sw)/2, 100), sub, font=f_sub, fill=GOLD_L)

        TL = PAD; TR = SIZE - PAD; TT = TITLE_H; TB = TT + TABLE_H
        col_curr = TL + 185; col_ev = TL + 400

        d.rectangle([(TL, TT), (TR, TB)], outline=GOLD, width=3)
        hdr_bot = TT + HDR_H
        d.line([(TL, hdr_bot), (TR, hdr_bot)], fill=GOLD, width=3)
        d.line([(col_curr, TT), (col_curr, hdr_bot)], fill=GOLD, width=2)
        d.line([(col_ev,   TT), (col_ev,   hdr_bot)], fill=GOLD, width=2)
        d.text((TL + 28,      TT + 20), "TIME",     font=f_hdr, fill=GOLD)
        d.text((col_curr + 28, TT + 20), "CURRENCY", font=f_hdr, fill=GOLD)
        d.text((col_ev   + 28, TT + 20), "EVENTS",   font=f_hdr, fill=GOLD)

        for i, ev in enumerate(eventos):
            y0 = hdr_bot + i * ROW_H; y1 = y0 + ROW_H; yc = (y0 + y1) // 2
            d.line([(TL, y1), (TR, y1)], fill=GOLD, width=1)
            d.line([(col_curr, y0), (col_curr, y1)], fill=GOLD, width=2)
            d.line([(col_ev,   y0), (col_ev,   y1)], fill=GOLD, width=2)

            tw = d.textlength(ev['time'], font=f_time)
            d.text((TL + (col_curr - TL - tw)/2, yc - 17), ev['time'], font=f_time, fill=WHITE)

            cc   = CURRENCY_CC.get(ev['currency'], 'un')
            flag = _get_flag(cc)
            fx   = col_curr + 18; fy = yc - 22
            if flag:
                img.paste(flag, (fx, fy), flag)
            d.text((fx + 76, yc - 13), ev['currency'], font=f_curr, fill=WHITE)

            max_w = TR - col_ev - 40
            words = ev['title'].split(); lines, cur = [], ""
            for w in words:
                test = (cur + " " + w).strip()
                if d.textlength(test, font=f_event) <= max_w:
                    cur = test
                else:
                    if cur: lines.append(cur)
                    cur = w
            if cur: lines.append(cur)
            lines = lines[:2]; lh = 32
            ty = yc - (len(lines) * lh) // 2
            for ln in lines:
                d.text((col_ev + 22, ty), ln, font=f_event, fill=WHITE)
                ty += lh

        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as _e:
        log.warning(f"📅 Error generando imagen calendario: {_e}")
        return None


async def _loop_calendario_diario() -> None:
    """Publica el calendario económico de alto impacto cada día a las 7:30h (hora Andorra)."""
    import requests as _req
    from datetime import datetime
    import pytz

    tz      = pytz.timezone("Europe/Andorra")
    _sent   = {}  # {"2026-04-22": True}

    CURRENCY_CC = {
        "USD":"us","EUR":"eu","GBP":"gb","CAD":"ca","JPY":"jp",
        "AUD":"au","NZD":"nz","CHF":"ch","CNY":"cn",
    }
    # Emojis bandera por moneda para el caption
    FLAG_EMOJI = {
        "USD":"🇺🇸","EUR":"🇪🇺","GBP":"🇬🇧","CAD":"🇨🇦","JPY":"🇯🇵",
        "AUD":"🇦🇺","NZD":"🇳🇿","CHF":"🇨🇭","CNY":"🇨🇳","SEK":"🇸🇪",
        "NOK":"🇳🇴","DKK":"🇩🇰","SGD":"🇸🇬","MXN":"🇲🇽","BRL":"🇧🇷",
    }

    while True:
        try:
            now     = datetime.now(tz)
            hoy_str = now.strftime("%Y-%m-%d")

            if now.hour == 7 and now.minute >= 30 and now.minute < 35 and _sent.get(hoy_str) != True:
                _sent[hoy_str] = True

                # Obtener eventos de hoy
                try:
                    _r = _req.get(
                        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                        timeout=10, headers={"User-Agent": "Mozilla/5.0"}
                    )
                    _data = _r.json() if _r.status_code == 200 else []
                except:
                    _data = []

                hoy = now.date()
                eventos = []
                for e in _data:
                    if e.get("impact", "").lower() != "high":
                        continue
                    try:
                        dt = datetime.fromisoformat(e["date"]).astimezone(tz)
                        if dt.date() == hoy:
                            eventos.append({
                                "time":     dt.strftime("%H:%M"),
                                "currency": e["country"],
                                "title":    e["title"],
                                "dt":       dt,
                            })
                    except:
                        pass
                eventos.sort(key=lambda x: x["dt"])

                if not eventos:
                    log.info("📅 Calendario: sin eventos de alto impacto hoy")
                    await asyncio.sleep(60)
                    continue

                # Generar imagen
                img_bytes = _generar_imagen_calendario(eventos)

                # Caption corto y dinámico
                divisas_unicas = list(dict.fromkeys(ev["currency"] for ev in eventos))
                divisas_str    = ", ".join(
                    f"{FLAG_EMOJI.get(d,'')}{d}" for d in divisas_unicas
                )
                # Evento más importante (prioridad: Fed/FOMC/CPI/NFP)
                _CRITICOS = ["fed","fomc","nfp","cpi","gdp","rate","payroll","inflation","retail"]
                top_ev = eventos[-1]  # fallback: último del día
                for ev in eventos:
                    if any(k in ev["title"].lower() for k in _CRITICOS):
                        top_ev = ev
                        break

                caption = (
                    f"⚠️ *CALENDARIO ECONÓMICO* ⚠️\n\n"
                    f"📊 *{len(eventos)} eventos de alto impacto hoy* — Hora España (GMT+2)\n\n"
                    f"❗️ Alta volatilidad esperada en pares de {divisas_str}. "
                    f"¡Ten cuidado con tus operaciones abiertas\\!\n\n"
                    f"🔥 Atención especial\\: *{top_ev['title']}* {top_ev['time']}h \\({top_ev['currency']}\\)\n\n"
                    f"BuySell365 Pro"
                )

                # Enviar al canal
                try:
                    url_base = f"https://api.telegram.org/bot{BOT_TOKEN}"
                    if img_bytes:
                        import io as _io
                        resp = _req.post(
                            f"{url_base}/sendPhoto",
                            data={"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "MarkdownV2"},
                            files={"photo": ("calendario.png", _io.BytesIO(img_bytes), "image/png")},
                            timeout=20,
                        )
                    else:
                        resp = _req.post(
                            f"{url_base}/sendMessage",
                            json={"chat_id": CHANNEL_ID, "text": caption, "parse_mode": "MarkdownV2"},
                            timeout=10,
                        )
                    if resp.status_code == 200:
                        log.info(f"📅 Calendario económico publicado ({len(eventos)} eventos)")
                    else:
                        log.warning(f"📅 Error publicando calendario: {resp.status_code} {resp.text[:200]}")
                except Exception as _e:
                    log.warning(f"📅 Error enviando calendario: {_e}")

        except Exception as _e:
            log.warning(f"📅 Loop calendario error: {_e}")

        await asyncio.sleep(60)


async def _loop_sync_web_periodico() -> None:
    """FIX 2026-04-24: Re-sincroniza copier_stats.json → web cada 5 min.
    Render tiene filesystem efimero — al redeploy pierde _copier_trades (RAM).
    Este loop garantiza que la web muestre siempre los stats frescos.
    """
    while True:
        try:
            await asyncio.sleep(300)  # 5 min
            _sync_copier_stats_to_web()
        except asyncio.CancelledError:
            break
        except Exception as _e_sync_loop:
            log.debug(f"Loop sync web fallo (no critico): {_e_sync_loop}")


async def _loop_ingest_generator_signals() -> None:
    """FIX 2026-05-09: ingesta de senales del btc_eth_generator.

    El generator (que corre en bot.py) escribe senales a generator_signals_queue.json.
    Este loop las lee cada 30s y las registra en _open_signals para que el monitor
    TP/SL del copier las trackee normal — celebracion, WhatsApp, etc.

    El generator ya:
    - Publico al canal VIP
    - Ejecuto en MT5 (si MT5_EXECUTE=true)
    - Solo falta el tracking — eso es lo que hace este loop.
    """
    queue_file = Path(__file__).parent / "generator_signals_queue.json"
    last_ingested_ts = 0.0

    # Esperar 30s tras arranque para no chocar con _load_open_signals
    await asyncio.sleep(30)

    while True:
        try:
            await asyncio.sleep(30)
            if not queue_file.exists():
                continue

            try:
                with open(queue_file, "r", encoding="utf-8") as f:
                    queue = json.load(f)
            except Exception:
                continue

            new_count = 0
            for entry in queue:
                if entry.get("ingested"):
                    continue
                ts = entry.get("ts", 0)
                if ts <= last_ingested_ts:
                    continue

                # Construir signal dict en formato que entiende _open_signals
                # (campos compatibles con _send_tp_celebration y monitor)
                signal = {
                    "pair": entry.get("symbol"),
                    "pair_display": entry.get("pair_display", entry.get("symbol")),
                    "mt5_symbol": entry.get("mt5_symbol", entry.get("symbol")),
                    "direction": entry.get("direction"),
                    "entry": entry.get("entry"),
                    "mt5_entry": entry.get("mt5_entry"),  # precio REAL ejecutado en MT5
                    "sl": entry.get("sl"),
                    "tp": entry.get("tp"),
                    "tp2": entry.get("tp2"),
                    "tp3": entry.get("tp3"),
                    "source": entry.get("source", "BS365_IA_Generator"),
                    "sent_at": entry.get("ts", time.time()),
                    "score": entry.get("score"),
                    # FIX 2026-05-11: mapear score → probability para que _record_daily_result
                    # persista el % en copier_stats.json (igual flujo que canales aliados).
                    "probability": entry.get("score"),
                    "probability_tech": entry.get("tech_score"),
                    "probability_vision": entry.get("vision_score"),
                    "probability_source": "generator",
                    "mt5_executed": entry.get("mt5_executed", False),
                    "mt5_ticket": entry.get("mt5_ticket"),
                    "mt5_lot": entry.get("mt5_lot"),
                    "_from_generator": True,
                }

                sig_id = f"{signal['pair']}_GEN_{int(ts)}"
                with _signals_lock:
                    if sig_id in _open_signals:
                        entry["ingested"] = True
                        continue
                    _open_signals[sig_id] = {
                        "signal": signal,
                        "sent_at": ts,
                        "telegram_msg_id": None,  # generator publica directo, sin id capturado aún
                    }
                entry["ingested"] = True
                new_count += 1
                last_ingested_ts = max(last_ingested_ts, ts)
                log.info(f"📥 Senal generator ingerida: {sig_id} ({signal['direction']} {signal['pair']} @ {signal['entry']})")

            if new_count > 0:
                _save_open_signals()
                # Re-escribir queue con flag ingested actualizado.
                # Fix 2026-05-10: ANTES de escribir, re-leer el archivo y MERGEAR
                # cualquier entrada nueva que el generator haya añadido entre
                # nuestra lectura inicial y este momento. Sin esto, la escritura
                # sobrescribia la version del generator → entradas perdidas
                # silenciosamente (race condition cross-process).
                try:
                    fresh_queue = []
                    try:
                        with open(queue_file, "r", encoding="utf-8") as f:
                            fresh_queue = json.load(f)
                    except Exception:
                        fresh_queue = queue  # fallback: usar nuestra version
                    # Identificar entradas en fresh que NO esten en nuestra version
                    # (clave: ts + mt5_ticket por si hay multiples por par)
                    our_keys = {(e.get("ts"), e.get("mt5_ticket")) for e in queue}
                    merged = list(queue)
                    for fe in fresh_queue:
                        fkey = (fe.get("ts"), fe.get("mt5_ticket"))
                        if fkey not in our_keys:
                            merged.append(fe)
                            log.info(f"📥 Re-merge: entrada nueva del generator preservada ({fe.get('symbol')} {fe.get('direction')} ts={fe.get('ts')})")
                    # Cap a 200 (mismo cap que el generator)
                    merged = merged[-200:]
                    tmp = str(queue_file) + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(merged, f, ensure_ascii=False, indent=2, default=str)
                        f.flush()
                        try:
                            os.fsync(f.fileno())
                        except (OSError, AttributeError):
                            pass
                    os.replace(tmp, str(queue_file))
                except Exception as _e_qsave:
                    log.debug(f"queue update skip: {_e_qsave}")

        except asyncio.CancelledError:
            break
        except Exception as _e_ing:
            log.debug(f"Ingest generator fallo (no critico): {_e_ing}")


async def _loop_promo_reportes() -> None:
    """Loop que envía reportes promocionales al grupo público a las 12:00 y 17:00 hora Andorra."""
    import requests
    from datetime import datetime
    import pytz
    tz = pytz.timezone("Europe/Andorra")

    # FIX 2026-04-25: Persistir _sent_today en disco (sobrevive reinicios del copier)
    def _load_sent_today() -> dict:
        try:
            if COPIER_SENT_STATE_FILE.exists():
                return json.loads(COPIER_SENT_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_sent_today(data: dict):
        try:
            _tmp = str(COPIER_SENT_STATE_FILE) + ".tmp"
            with open(_tmp, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False))
            import os as _os_st
            _os_st.replace(_tmp, str(COPIER_SENT_STATE_FILE))
        except Exception:
            pass

    _sent_today: dict = _load_sent_today()
    log.info(f"📢 Reportes: estado cargado de disco — {list(_sent_today.keys())}")

    _promo_rows = [
        [{"text": "💎 VER CANAL VIP", "url": f"https://t.me/{os.getenv('BOT_USERNAME','Andoperandobot')}?start=vip"}],
        [{"text": "🌐 Dashboard en vivo", "url": "https://buysell365.pro"}],
    ]
    _promo_buttons = json.dumps({"inline_keyboard": _promo_rows})

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
                _save_sent_today(_sent_today)

            # 12:00 — Reporte de mañana
            if hora == 12 and minuto < 5 and _sent_today.get("12") != hoy_str:
                _sent_today["12"] = hoy_str
                _save_sent_today(_sent_today)
                msg = _build_promo_report("MIDDAY")
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
                _save_sent_today(_sent_today)
                msg = _build_promo_report("AFTERNOON")
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

            # ── WEEKLY GIFT SUMMARY — Viernes 17:00 (5 min de ventana) ──
            # FIX 2026-05-04: resumen semanal de senales gratis al grupo publico
            # con CTA al canal VIP debajo. Solo se publica si hay datos en
            # gift_history de la semana actual.
            if (now.weekday() == 4 and hora == 17 and minuto < 15
                    and _sent_today.get("17_friday_gift") != hoy_str):
                _sent_today["17_friday_gift"] = hoy_str
                _save_sent_today(_sent_today)
                _gift_msg_sem = ""
                try:
                    _gift_msg_sem = _build_weekly_gift_summary()
                except Exception as _e_gws:
                    log.warning(f"weekly gift summary build error: {_e_gws}")
                if _gift_msg_sem and GROUP_ID:
                    try:
                        _url_gw = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                        _btn_gw = json.dumps({"inline_keyboard": [
                            [{"text": "💎 JOIN VIP CHANNEL",
                              "url": f"https://t.me/{os.getenv('BOT_USERNAME','Andoperandobot')}?start=vip"}],
                        ]})
                        _resp_gw = requests.post(_url_gw, json={
                            "chat_id": GROUP_ID,
                            "text": _gift_msg_sem,
                            "parse_mode": "Markdown",
                            "reply_markup": _btn_gw,
                        }, timeout=15)
                        if _resp_gw.status_code == 200:
                            log.info("🎁📅 Weekly gift summary publicado al grupo gratis")
                        else:
                            log.warning(f"Weekly gift summary error: {_resp_gw.status_code} {_resp_gw.text[:120]}")
                    except Exception as _e_gw:
                        log.warning(f"Weekly gift summary send error: {_e_gw}")
                elif not _gift_msg_sem:
                    log.info("🎁📅 Weekly gift summary: sin datos esta semana, skip")

            # ── MONTHLY RECAP — Primer lunes del mes a las 19:00 Andorra ──
            # FIX 2026-05-04: publicacion automatica del recap mensual del mes
            # anterior. Suma todas las senales del Canal VIP del mes y publica
            # en VIP + Grupo + Instagram. Se dispara en el primer lunes (1-7) y
            # solo una vez por mes (state guarda last_published_month).
            try:
                _is_first_monday = (now.weekday() == 0 and now.day <= 7)
                _monthly_state_file = Path(BASE_DIR) / "monthly_summary_state.json" if 'BASE_DIR' in dir() else (Path(__file__).resolve().parent / "monthly_summary_state.json")
                _monthly_key = f"{now.year}-{now.month:02d}"
                _last_monthly_key = ""
                if _monthly_state_file.exists():
                    try:
                        _ms = json.loads(_monthly_state_file.read_text(encoding="utf-8"))
                        _last_monthly_key = _ms.get("last_published_month", "")
                    except Exception:
                        pass
                if (_is_first_monday and hora == 19 and minuto < 10
                        and _last_monthly_key != _monthly_key):
                    log.info(f"📅 MONTHLY RECAP trigger — primer lunes {now.strftime('%d/%m/%Y')} 19:00")
                    try:
                        # Mes anterior
                        _prev_month = now.month - 1 if now.month > 1 else 12
                        _prev_year = now.year if now.month > 1 else now.year - 1
                        from monthly_summary_publisher import publicar_mensual as _pub_mensual
                        _res = _pub_mensual(_prev_month, _prev_year, dry_run=False)
                        log.info(f"📅 MONTHLY RECAP publicado: {_res.get('resultados', {})}")

                        # Instagram (Feed + Story) — override IG_DISABLED
                        try:
                            import os as _os_mig
                            _orig_ig_dis = _os_mig.environ.get("INSTAGRAM_DISABLED", "")
                            _os_mig.environ["INSTAGRAM_DISABLED"] = "0"
                            from instagrapi import Client as _IGClient
                            _ig_user = _os_mig.getenv("IG_USERNAME", "")
                            _ig_pass = _os_mig.getenv("IG_PASSWORD", "")
                            _ig_session = Path(__file__).resolve().parent / "ig_session.json"
                            if _ig_user and _ig_pass and _res.get("img"):
                                _cl = _IGClient()
                                if _ig_session.exists():
                                    _cl.load_settings(str(_ig_session))
                                    _cl.get_timeline_feed()
                                else:
                                    _cl.login(_ig_user, _ig_pass)
                                    _cl.dump_settings(str(_ig_session))
                                _stats_ig = _res.get("stats", {})
                                _net_ig = _stats_ig.get("net_total", 0)
                                _wr_ig = _stats_ig.get("wr", 0)
                                _tps_ig = _stats_ig.get("tps_unique", 0)
                                _bd = _stats_ig.get("best_day") or ("", 0)
                                _days_ig = _stats_ig.get("days_traded", 0)
                                _top = _stats_ig.get("top_pairs", [])
                                _top_txt = " | ".join(
                                    f"{p} +{int(v)}" for p, v in _top[:3]
                                ) if _top else ""
                                _mes_label = ["JAN","FEB","MAR","APR","MAY","JUN",
                                              "JUL","AUG","SEP","OCT","NOV","DEC"][_prev_month-1]
                                _caption_ig = (
                                    f"🏆 {_mes_label} {_prev_year} - MONTHLY RECAP\n\n"
                                    f"📈 +{int(_net_ig):,} points NET\n"
                                    f"✓ {_tps_ig} winning signals\n"
                                    f"🏆 Win Rate: {_wr_ig}%\n\n"
                                    f"🥇 Top Pairs:\n  {_top_txt}\n\n"
                                    f"📅 Best day: {_bd[0]} ({_bd[1]:+.0f} pts)\n"
                                    f"{_days_ig} active trading days\n\n"
                                    f"Total transparency. Real numbers.\n"
                                    f"Link in bio for VIP\n\n"
                                    f"#trading #forex #gold #xauusd #signals "
                                    f"#vip #buysell365 #tradingsignals"
                                )
                                try:
                                    _cl.photo_upload(_res["img"], _caption_ig)
                                    log.info(f"📅 MONTHLY IG Feed publicado")
                                except Exception as _eif:
                                    log.warning(f"MONTHLY IG Feed error: {_eif}")
                                try:
                                    import time as _t_ig
                                    _t_ig.sleep(3)
                                    _cl.photo_upload_to_story(_res["img"])
                                    log.info(f"📅 MONTHLY IG Story publicado")
                                except Exception as _eis:
                                    log.warning(f"MONTHLY IG Story error: {_eis}")
                            if _orig_ig_dis:
                                _os_mig.environ["INSTAGRAM_DISABLED"] = _orig_ig_dis
                        except Exception as _e_ig:
                            log.warning(f"MONTHLY IG block error: {_e_ig}")

                        # Persistir state — evitar doble publicacion en el mes
                        try:
                            _ms_data = {
                                "last_published_month": _monthly_key,
                                "last_published_ts": int(time.time()),
                                "prev_month_published": f"{_prev_year}-{_prev_month:02d}",
                            }
                            _monthly_state_file.write_text(
                                json.dumps(_ms_data, indent=2), encoding="utf-8")
                            log.info(f"📅 Monthly state guardado: {_monthly_key}")
                        except Exception as _e_state:
                            log.warning(f"Monthly state save error: {_e_state}")
                    except Exception as _e_monthly:
                        log.error(f"📅 MONTHLY RECAP error: {_e_monthly}")
            except Exception as _e_outer:
                log.debug(f"Monthly trigger check error: {_e_outer}")

            # 22:00 — FINAL DAY RECAP (solo admin privado + IG)
            # FIX 2026-05-06: Canal VIP y Grupo ya reciben el resumen completo a las 19:00
            # via publicar_resumen_diario(). El bloque de las 22:00 ahora solo envía al
            # admin (confirmación técnica) y a Instagram — nunca al canal ni al grupo
            # para evitar duplicados.
            if hora == 22 and minuto < 5 and _sent_today.get("22") != hoy_str:
                _sent_today["22"] = hoy_str
                _save_sent_today(_sent_today)

                # ── Calcular stats completos via stats_normalizer (FIX 2026-05-01: VERDAD SIEMPRE) ──
                # Centraliza:  TPs - SLs + parciales · x10 GOLD consistente · multi-TP grouping
                #              · exclusiones Manual/MT5_Reinsert/blacklist · categorias separadas
                from stats_normalizer import compute_day_stats as _compute_day_stats
                _hoy_dmy = datetime.now(tz).strftime("%d/%m/%Y")
                _all_trades = []
                try:
                    if COPIER_STATS_FILE.exists():
                        with open(COPIER_STATS_FILE, "r", encoding="utf-8") as f:
                            _cdata = json.load(f)
                        _all_trades = _cdata.get("trades", [])
                except Exception:
                    pass

                _ds = _compute_day_stats(_all_trades, _hoy_dmy)

                # Mapear a variables locales (compatibilidad con resto del codigo)
                _net = _ds["net_total"]
                _wr = round(_ds["wr_unique"])
                tps_count_unique = _ds["tps_unique"]
                sls_count_unique = _ds["sls_unique"]
                tps_count_events = _ds["tps_events"]
                sls_count_events = _ds["sls_events"]
                parciales_count = _ds["partials"]

                with _signals_lock:
                    abiertas = len(_open_signals)

                # Top 3 best/worst by pair (consistente x10)
                from stats_normalizer import normalize_pips as _norm_pips
                _by_pair_real = {}
                for t in _all_trades:
                    if t.get("fecha") != _hoy_dmy:
                        continue
                    src_t = t.get("source", "")
                    if src_t in ("Manual", "MT5_Reinsert"):
                        continue
                    p, _cat = _norm_pips(t)
                    pair = t.get("pair_display") or t.get("pair", "?")
                    res = t.get("result", "")
                    if res == "tp" or (res in ("close_half", "close_partial", "full_close") and p > 0):
                        _by_pair_real[pair] = _by_pair_real.get(pair, 0.0) + p
                    elif res == "sl":
                        _by_pair_real[pair] = _by_pair_real.get(pair, 0.0) - p
                # Top 3 best (positivos) y worst (negativos)
                _by_pair_sorted = sorted(_by_pair_real.items(), key=lambda kv: kv[1], reverse=True)
                _top = [{"pair_display": p, "pips_numeric": v, "pips_unit": "pts"} for p, v in _by_pair_sorted if v > 0][:3]
                _worst = [{"pair_display": p, "pips_numeric": -v, "pips_unit": "pts"} for p, v in sorted(_by_pair_real.items(), key=lambda kv: kv[1]) if v < 0][:3]
                tps = list(range(tps_count_unique))  # placeholder para condiciones len(tps) abajo
                sls = list(range(sls_count_unique))

                # Parciales para mostrar (filtra ganadores)
                parciales = []
                for t in _all_trades:
                    if t.get("fecha") != _hoy_dmy:
                        continue
                    if t.get("source", "") in ("Manual", "MT5_Reinsert"):
                        continue
                    if t.get("result") in ("close_half", "close_partial", "full_close"):
                        _p, _ = _norm_pips(t)
                        if _p > 0:
                            parciales.append({
                                "pair": t.get("pair_display") or t.get("pair", "?"),
                                "pips": _p,
                                "unit": "pts",
                            })
                # FIX 2026-04-28e: ELIMINADA seccion "BY SOURCE" — REGLA DE MARCA:
                # NUNCA revelar fuente del copy (canal aliado) en publicaciones publicas
                # ni en canal VIP. Las senhales son nuestras (somos los creadores).
                # Stats por source solo se calculan internamente (no se publican).
                # FIX 2026-04-28e: gifts MAS prominentes con detalle (par, hora, resultado, pips)
                _gifts_text = ""
                _gifts_grupo_text = ""  # version para grupo publico (mas hype)
                try:
                    if GIFT_TRACKER_FILE.exists():
                        with open(GIFT_TRACKER_FILE, "r", encoding="utf-8") as f:
                            _gd = json.load(f)
                        _gold_g = _gd.get("gold_gifted", False)
                        _other_g = _gd.get("other_gifted", False)
                        _gold_p = _gd.get("gold_pair", "—")
                        _other_p = _gd.get("other_pair", "—")
                        _gold_r = _gd.get("gold_result")
                        _other_r = _gd.get("other_result")
                        _gold_h = _gd.get("gold_hora", "")
                        _other_h = _gd.get("other_hora", "")
                        _gold_pips = _gd.get("gold_pips")
                        _other_pips = _gd.get("other_pips")
                        _gold_unit = _gd.get("gold_unit", "pts")
                        _other_unit = _gd.get("other_unit", "pips")
                        _gold_dir = _gd.get("gold_direction", "")
                        _other_dir = _gd.get("other_direction", "")
                        _gold_tplv = _gd.get("gold_tp_level", "")
                        _other_tplv = _gd.get("other_tp_level", "")

                        def _gift_line(gifted, pair, direction, hora, result, pips, unit, tplv):
                            if not gifted:
                                return ""
                            _hora_txt = f" ({hora})" if hora else ""
                            _dir_txt = f"{direction} " if direction else ""
                            _emoji = {"tp": "✅ WINNER", "sl": "❌ Stop Loss", None: "⏳ Pending"}.get(result, "⏳ Pending")
                            _pips_txt = ""
                            if pips is not None:
                                _sign = "+" if pips >= 0 else ""
                                _pips_txt = f" · *{_sign}{pips} {unit}*"
                                if tplv and result == "tp":
                                    _pips_txt += f" ({tplv})"
                            return f"   • {_dir_txt}{pair}{_hora_txt} → {_emoji}{_pips_txt}\n"

                        _gold_line_admin = _gift_line(_gold_g, _gold_p, _gold_dir, _gold_h, _gold_r, _gold_pips, _gold_unit, _gold_tplv)
                        _other_line_admin = _gift_line(_other_g, _other_p, _other_dir, _other_h, _other_r, _other_pips, _other_unit, _other_tplv)

                        if _gold_g or _other_g:
                            _gifts_text = f"\n🎁 *FREE SIGNALS PUBLISHED TODAY:*\n{_gold_line_admin}{_other_line_admin}"
                            # Para grupo publico — solo si al menos UNA gano (hype)
                            _wins_g = sum(1 for r in (_gold_r, _other_r) if r == "tp")
                            _total_g = sum(1 for g in (_gold_g, _other_g) if g)
                            if _wins_g > 0:
                                _gifts_grupo_text = (
                                    f"\n🎁 *FREE SIGNALS today: {_wins_g}/{_total_g} WINNERS!*\n"
                                    f"{_gold_line_admin}{_other_line_admin}"
                                )
                except Exception:
                    pass

                # Helpers para formato
                def _fmt_top_lines(items, prefix, sign):
                    lines = []
                    medals = ["🥇", "🥈", "🥉"]
                    for i, r in enumerate(items[:3]):
                        _pp = abs(float(r.get("pips_numeric", r.get("pips", 0)) or 0))
                        _pd = r.get("pair_display") or r.get("pair", "?")
                        lines.append(f"   {medals[i]} {_pd} {sign}{_pp:.0f} {r.get('pips_unit','pts')}")
                    return "\n".join(lines) if lines else "   (none)"

                # FIX 2026-04-28e: _src_block ya no se renderiza. Mantengo variable
                # vacia para compatibilidad con f-string abajo si quedo alguna referencia.
                _src_block = ""

                # ── Breakdown por categoria (FIX 2026-05-01: VERDAD SIEMPRE) ──
                _cat_lines = ""
                if _ds["categories"]:
                    _cat_parts = []
                    for c in _ds["categories"]:
                        _sgn = "+" if c["net"] >= 0 else ""
                        _cat_parts.append(f"   {c['name']}: *{_sgn}{c['net']:.0f} {c['unit']}*  ({c['tps']}W/{c['sls']}L)")
                    _cat_lines = "\n".join(_cat_parts) + "\n"

                # Nota multi-TP si los eventos exceden las senales unicas
                _multi_tp_note = ""
                if tps_count_events > tps_count_unique:
                    _multi_tp_note = f"\n   _({tps_count_events} TP levels hit across multi-target signals)_\n"

                # ── 1) Mensaje COMPLETO (admin + canal VIP) ──
                # FIX 2026-05-01: VERDAD SIEMPRE — senales unicas + breakdown por categoria + multi-TP nota
                _vip_msg = (
                    f"📋 *FINAL DAY RECAP*\n"
                    f"📅 {hoy_str}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 *SUMMARY:*\n"
                    f"   ✓ {tps_count_unique} winners · ● {sls_count_unique} losers · ⚡ {parciales_count} partials\n"
                    f"{_multi_tp_note}"
                    f"   📈 Win Rate: *{_wr}%*\n"
                    f"   💰 Net: *{_net:+.0f} pts*\n"
                    f"   🟢 Open signals: *{abiertas}*\n"
                )
                if _cat_lines:
                    _vip_msg += f"\n📂 *BY CATEGORY:*\n{_cat_lines}"
                if _top:
                    _vip_msg += f"\n🏆 *TOP 3 BEST:*\n{_fmt_top_lines(_top, 'top', '+')}\n"
                if _worst:
                    _vip_msg += f"\n📉 *TOP 3 WORST:*\n{_fmt_top_lines(_worst, 'worst', '-')}\n"
                if parciales:
                    _par_lines = "\n".join(
                        f"   ⚡ {p['pair']} +{p['pips']:.0f} {p['unit']}"
                        for p in parciales[:5]
                    )
                    _vip_msg += f"\n💎 *PARTIALS SECURED:*\n{_par_lines}\n"
                # FIX 2026-04-28e: ELIMINADA seccion BY SOURCE — regla de marca.
                _vip_msg += (
                    f"{_gifts_text}"
                    f"\n━━━━━━━━━━━━━━━━━━━━\n"
                    f"_Full transparency · SLs counted_"
                )

                # ── 2) Mensaje GRUPO PUBLICO (marketing + hype) ──
                # Solo se publica si net positivo (regla solo-ganancias en grupo publico)
                _grupo_msg = None
                if _net > 0 and tps_count_unique > 0:
                    _grupo_msg = (
                        f"🚀 *VIP CHANNEL — DAY CLOSED*\n"
                        f"📅 {hoy_str}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"📊 *Today in VIP:*\n"
                        f"   ✓ {tps_count_unique} winners · ● {sls_count_unique} losers\n"
                        f"   📈 Win Rate: *{_wr}%*\n"
                        f"   💰 *{_net:+.0f} pts net profit*\n"
                        f"\n🏆 *Best trades today:*\n{_fmt_top_lines(_top, 'top', '+')}\n"
                    )
                    if parciales:
                        _grupo_msg += f"\n⚡ *{len(parciales)} partial closes secured*\n"
                    if _gifts_grupo_text:
                        _grupo_msg += _gifts_grupo_text
                    _grupo_msg += (
                        f"\n━━━━━━━━━━━━━━━━━━━━\n"
                        f"💎 *This is what VIPs got today.*\n"
                        f"📡 Free clients got 1-2 of these.\n\n"
                        f"🔥 *Want EVERY signal in real time?*\n"
                        f"👉 Type */vip* — no upsells, no tricks"
                    )

                # ── ENVIO ──
                # FIX 2026-05-12 (noche): admin recap 22:00 DESACTIVADO por usuario.
                # Era spam — el resumen 19:00 ya cubre todo. Solo log interno para
                # auditoria si hace falta revisar mas tarde.
                log.info(f"📋 Final recap 22:00 — admin desactivado por usuario · net {_net:+.0f} pts · {tps_count_unique}W/{sls_count_unique}L · WR {_wr}%")
                # Canal VIP y Grupo ya se publicaron a las 19:00 — no duplicar

                # 4) Instagram Story (solo si net positivo)
                if _net > 0 and tps_count_unique > 0 and not _ig_in_circuit_breaker():
                    try:
                        from instagram_poster import post_daily_summary
                        _ig_stats = {
                            "fecha": hoy_str,
                            "wr": _wr,
                            "tps": tps_count_unique,
                            "sls": sls_count_unique,
                            "pips_netos": int(_net),
                            "pips_net": int(_net),
                            "parciales_total": parciales_count,
                            "top_pares": [(r.get("pair_display") or r.get("pair","?"),
                                           float(r.get("pips_numeric", r.get("pips", 0)) or 0)) for r in _top[:3]],
                        }
                        _ig_ok = post_daily_summary(_ig_stats)
                        if _ig_ok:
                            log.info("📸 Final recap → Instagram OK")
                            _mark_ig_post_sent("final_recap")
                        else:
                            log.debug("📸 Final recap IG skip — sesion/cooldown")
                    except Exception as _e_ig:
                        _eis = str(_e_ig).lower()
                        if "feedback_required" in _eis or "spam" in _eis:
                            _trigger_ig_circuit_breaker()
                        log.debug(f"Final recap IG error: {_e_ig}")

        except Exception as e:
            log.warning(f"Error en loop promo reportes: {e}")

        await asyncio.sleep(60)  # Revisar cada minuto


def _is_forex_market_closed() -> bool:
    """True si forex/indices/metales/petroleo estan cerrados (fin de semana).
    Forex cierra Viernes 22:00 UTC y reabre Domingo 22:00 UTC.
    Crypto no aplica — siempre abierto.
    """
    from datetime import datetime, timezone
    _now_utc = datetime.now(timezone.utc)
    _wd = _now_utc.weekday()  # Mon=0..Sun=6
    if _wd == 5:  # Sabado completo
        return True
    if _wd == 4 and _now_utc.hour >= 22:  # Viernes desde 22:00 UTC
        return True
    if _wd == 6 and _now_utc.hour < 22:  # Domingo antes de 22:00 UTC
        return True
    return False


def _is_24_7_asset(pair: str) -> bool:
    """True si el activo cotiza 24/7 (cryptos)."""
    _p = pair.upper().replace("/", "")
    return any(_c in _p for _c in ("BTC", "ETH", "BNB", "XRP", "DOGE", "SOL", "LTC", "BCH", "ADA", "DOT", "AVAX", "MATIC"))


_market_closed_logged_until = 0.0  # ts hasta el que ya logueamos el skip de mercado cerrado
_market_was_closed = None  # estado previo para detectar transicion close->open


def _retry_pending_market_open_signals() -> int:
    """FIX 2026-04-26: reintenta ejecutar en MT5 las senhales que fueron
    marcadas con _pending_market_open=True cuando el mercado estaba cerrado.

    Se llama cuando se detecta que el mercado acaba de abrir (transicion
    closed -> open). Notifica al canal VIP en respuesta al mensaje original
    de cada senhal cuando se ejecuta exitosamente.

    Retorna numero de senhales reintentadas (no necesariamente exitosas).
    """
    import requests
    retried = 0
    success = 0
    with _signals_lock:
        # Snapshot — vamos a mutar _open_signals durante la iteracion
        pending = [(sid, dict(sdata)) for sid, sdata in _open_signals.items()
                   if sdata.get("_pending_market_open", False)]

    if not pending:
        return 0

    log.info(f"🔓 Mercado abierto — reintentando {len(pending)} señales pendientes en MT5")

    # FIX 2026-04-27: umbral minimo de R:R para retry tras fin de semana.
    # Bajo este umbral consideramos que el precio se movio demasiado durante el
    # cierre y la senal del aliado ya no es viable. Override por env si se quiere
    # mas/menos estricto. 1.0 = reward minimo == riesgo (R:R 1:1).
    _min_rr_retry = float(os.getenv("COPIER_RETRY_MIN_RR", "1.0"))

    for sig_id, sdata in pending:
        sig = sdata.get("signal", {})
        pair = sig.get("pair", "?")
        pair_d = _get_display_pair(pair)
        direction = sig.get("direction", "?")
        msg_id = sdata.get("telegram_msg_id")

        # Skip si es crypto (no deberia estar aqui, pero por seguridad)
        if _is_24_7_asset(pair):
            with _signals_lock:
                if sig_id in _open_signals:
                    _open_signals[sig_id].pop("_pending_market_open", None)
            continue

        # FIX 2026-05-11: si la senal YA tiene ticket MT5 ejecutado, NO publicar
        # Skip — el _pending_market_open quedo huerfano (no se limpio tras
        # ejecucion exitosa). Caso real GBPUSD ticket 762904397 viernes 23:49:
        # MT5 ejecuto, lunes 00:00 retry encontro pending=True + precio ya
        # paso el TP → publico "Signal SKIPPED" mientras monitor publico
        # "TP HIT +$233.81". Contradiccion al canal VIP.
        # Ahora: si _mt5_ticket existe Y la posicion esta/estuvo en MT5,
        # solo limpiar flag y dejar que monitor/reconcile manejen TP/SL.
        _existing_ticket = sdata.get("_mt5_ticket") or sig.get("_mt5_ticket")
        if _existing_ticket:
            try:
                import price_feed as _mt5_tchk
                if _mt5_tchk.terminal_info() is not None or _mt5_tchk.initialize():
                    # Buscar en posiciones abiertas O en historial reciente
                    _pos_alive = _mt5_tchk.positions_get(ticket=int(_existing_ticket))
                    _has_ticket = _pos_alive is not None and len(_pos_alive) > 0
                    if not _has_ticket:
                        # Tambien chequear historial (puede haber cerrado en TP/SL durante weekend)
                        try:
                            from datetime import datetime as _dt_h, timedelta as _td_h
                            _from = _dt_h.now() - _td_h(days=7)
                            _deals = _mt5_tchk.history_deals_get(_from, _dt_h.now())
                            if _deals:
                                _has_ticket = any(int(d.position_id) == int(_existing_ticket) for d in _deals)
                        except Exception:
                            pass
                    if _has_ticket:
                        log.info(
                            f"🔓 Retry omitido {pair_d} {direction}: ticket MT5 #{_existing_ticket} "
                            f"ya ejecutado (flag _pending_market_open huerfano, limpiando)"
                        )
                        with _signals_lock:
                            if sig_id in _open_signals:
                                _open_signals[sig_id].pop("_pending_market_open", None)
                        continue
            except Exception as _e_tchk:
                log.debug(f"Ticket precheck retry {pair_d}: {_e_tchk}")

        # FIX 2026-04-27: PRE-CHECK R:R real con tick actual del broker.
        # Caso real 27/04 00:00: GBPUSD SELL del sabado entry=1.35320 TP=1.35160
        # SL=1.35470. Lunes abre a 1.35118 (precio bajo del entry teorico). MT5
        # ejecuta a precio actual → R:R=0.13 (TP a 4 pips, SL a 35 pips). Trade
        # PESIMO publicado como ganador. Ahora abortamos si R:R < umbral.
        _abort_reason = None
        try:
            import price_feed as _mt5_chk
            if _mt5_chk.terminal_info() is not None or _mt5_chk.initialize():
                _sym_chk = sig.get("mt5_symbol") or sig.get("pair", "")
                _tick_chk = _mt5_chk.symbol_info_tick(_sym_chk)
                if _tick_chk:
                    _is_buy_chk = sig.get("direction") == "BUY"
                    _live_price = _tick_chk.ask if _is_buy_chk else _tick_chk.bid
                    _tp_chk = sig.get("tp", 0) or 0
                    _sl_chk = sig.get("sl", 0) or 0
                    if _live_price > 0 and _tp_chk > 0 and _sl_chk > 0:
                        # Direccion: TP debe estar al lado correcto del precio actual.
                        _wrong_dir = (
                            (_is_buy_chk and _tp_chk <= _live_price) or
                            (not _is_buy_chk and _tp_chk >= _live_price)
                        )
                        if _wrong_dir:
                            _abort_reason = f"TP {_tp_chk} ya quedo del lado equivocado (precio {_live_price})"
                        else:
                            _risk_real = abs(_live_price - _sl_chk)
                            _reward_real = abs(_tp_chk - _live_price)
                            _rr_real = (_reward_real / _risk_real) if _risk_real > 0 else 0
                            if _rr_real < _min_rr_retry:
                                _abort_reason = f"R:R real {_rr_real:.2f} < min {_min_rr_retry}"
        except Exception as _e_rrcheck:
            log.debug(f"R:R precheck error {pair_d}: {_e_rrcheck}")

        if _abort_reason:
            log.warning(f"🚫 Retry abortado {pair_d} {direction}: {_abort_reason}")
            # Sacar de _open_signals para que no quede colgada
            with _signals_lock:
                _open_signals.pop(sig_id, None)
            _resolved_signals.add(sig_id)
            # Notificar al canal VIP — honestidad sobre por que NO se ejecuto
            if BOT_TOKEN and CHANNEL_ID and msg_id:
                try:
                    _msg_skip = (
                        f"⚠️ *Signal SKIPPED*\n\n"
                        f"_{pair_d} {direction}_ — market moved during weekend.\n"
                        f"Original setup no longer valid.\n"
                        f"_{_abort_reason}_"
                    )
                    requests.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                        json={
                            "chat_id": CHANNEL_ID,
                            "text": _msg_skip,
                            "parse_mode": "Markdown",
                            "reply_to_message_id": msg_id,
                        },
                        timeout=10,
                    )
                except Exception as _e_skipnotif:
                    log.debug(f"Skip notif error: {_e_skipnotif}")
            continue

        try:
            executed, detail = execute_in_mt5(sig)
            retried += 1
            log.info(f"📡 MT5 retry: {'✅' if executed else '❌'} {pair_d} {direction} — {detail}")

            if executed:
                success += 1
                # Quitar el flag pending — ya esta ejecutada
                with _signals_lock:
                    if sig_id in _open_signals:
                        _open_signals[sig_id].pop("_pending_market_open", None)

                # FIX 2026-04-27: el mensaje al canal ahora muestra el entry MT5
                # REAL ademas del teorico (cuando difieren) — antes mentia
                # diciendo "ACTIVADA" sin aclarar que el precio cambio.
                _mt5_e = sig.get("mt5_entry", 0) or 0
                _e_teorico = sig.get("entry", 0) or 0
                _entry_line = f"📍 Entry MT5: {_mt5_e}"
                if _e_teorico > 0 and _mt5_e > 0:
                    _entry_diff_pct = abs(_mt5_e - _e_teorico) / _e_teorico * 100
                    if _entry_diff_pct >= 0.1:
                        _entry_line += (
                            f"\n⚠️ Signal entry: {_e_teorico} "
                            f"(price moved {_entry_diff_pct:.2f}% over weekend)"
                        )

                # Notificar al canal VIP en respuesta al mensaje original
                if BOT_TOKEN and CHANNEL_ID and msg_id:
                    try:
                        _msg_act = (
                            f"✅ *Signal ACTIVATED*\n\n"
                            f"Market is now open — order executed in MT5.\n"
                            f"_{pair_d} {direction}_"
                            f"_{pair_d} {direction}_\n\n"
                            f"{_entry_line}"
                        )
                        _payload_act = {
                            "chat_id": CHANNEL_ID,
                            "text": _msg_act,
                            "parse_mode": "Markdown",
                            "reply_to_message_id": msg_id,
                        }
                        _r_act = requests.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                            json=_payload_act, timeout=10
                        )
                        if _r_act.status_code == 400 and "message to be replied" in _r_act.text:
                            _payload_act.pop("reply_to_message_id", None)
                            requests.post(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                                json=_payload_act, timeout=10
                            )
                    except Exception as _e_notif:
                        log.debug(f"Notif activation error: {_e_notif}")
            else:
                # Si sigue fallando con MARKET_CLOSED (mercado aun cerrado por
                # algun motivo), conservamos el flag y reintentaremos en el
                # siguiente ciclo. Si falla por otra razon, dejamos como esta.
                if not (detail and detail.startswith("MARKET_CLOSED:")):
                    log.warning(
                        f"⚠️ Reintento {pair_d} {direction} fallo NO por mercado cerrado: {detail}"
                    )
        except Exception as _e_retry:
            log.warning(f"⚠️ Error reintentando senhal {sig_id}: {_e_retry}")

    if retried > 0:
        _save_open_signals()
        log.info(f"🔓 Reintento mercado abierto: {success}/{retried} senhales activadas exitosamente")
    return retried


async def _monitor_tp_loop() -> None:
    """Async background loop — checks every 30s if any tracked signal hit TP or SL."""
    log.info("🎯 Monitor TP/SL loop iniciado (intervalo: 30s)")
    global _daily_summary_sent, _daily_publisher_sent, _transparency_sent, _market_closed_logged_until, _daily_eli_sent
    # FIX 2026-04-25: Cargar estado de disco (mismas funciones definidas en _loop_promo_reportes)
    try:
        _sent_today = json.loads(COPIER_SENT_STATE_FILE.read_text(encoding="utf-8")) if COPIER_SENT_STATE_FILE.exists() else {}
    except Exception:
        _sent_today = {}
    _reconcile_tick = 0  # FIX 2026-04-17: contador para reconciliación MT5 cada 3 min
    while True:
        await asyncio.sleep(30)  # 30s para no perder TP/SL en mercados volátiles

        # ── FIX 2026-04-17: Reconcile copier_open_signals ↔ MT5 cada 3 min ──
        _reconcile_tick += 1
        if _reconcile_tick >= 6:  # 6 * 30s = 3 min
            _reconcile_tick = 0
            try:
                _reconcile_open_vs_mt5()
            except Exception as _e_rec:
                log.warning(f"Reconcile error: {_e_rec}")

        # ── AUTO-BREAKEVEN AL 30% DEL CAMINO (INTERNO — sin mensaje al canal) ──
        # FIX 2026-05-11 (tarde): mueve SL a entry cuando profit alcanza 30% del distance to TP.
        # Asegura que trades que pasan por profit pero no llegan al 50% (umbral auto-close)
        # tampoco terminen en SL. Activar con AUTO_BREAKEVEN_ENABLED=true en .env.
        # Default OFF para rollback rapido si causa problemas.
        try:
            if os.getenv("AUTO_BREAKEVEN_ENABLED", "false").strip().lower() in ("true", "1", "yes", "on"):
                import price_feed as _mt5_be
                _all_pos_be = _mt5_be.positions_get() or []
                for _pb in _all_pos_be:
                    if getattr(_pb, "magic", 0) not in BS365_MAGICS:
                        continue
                    if _pb.ticket in _auto_breakeven_done_tickets:
                        continue
                    _entry_b = _pb.price_open
                    _tp_b    = _pb.tp
                    _sl_b    = _pb.sl
                    if _tp_b <= 0 or _entry_b <= 0:
                        continue  # sin TP, no podemos calcular % del camino
                    # Si SL ya esta en entry (o mas favorable), saltar
                    _is_buy_b = (_pb.type == 0)
                    if _sl_b > 0:
                        if _is_buy_b and _sl_b >= _entry_b:
                            _auto_breakeven_done_tickets.add(_pb.ticket)
                            continue
                        if not _is_buy_b and _sl_b <= _entry_b:
                            _auto_breakeven_done_tickets.add(_pb.ticket)
                            continue
                    _tick_b = _mt5_be.symbol_info_tick(_pb.symbol)
                    if not _tick_b:
                        continue
                    _cur_b = _tick_b.bid if _is_buy_b else _tick_b.ask
                    # Calcular umbral BE
                    if _is_buy_b:
                        _be_target = _entry_b + (_tp_b - _entry_b) * AUTO_BREAKEVEN_PCT
                        _hit_be    = _cur_b >= _be_target
                    else:
                        _be_target = _entry_b - (_entry_b - _tp_b) * AUTO_BREAKEVEN_PCT
                        _hit_be    = _cur_b <= _be_target
                    if not _hit_be:
                        continue
                    # Mover SL a entry via TRADE_ACTION_SLTP
                    _sym_info_b = _mt5_be.symbol_info(_pb.symbol)
                    _digits_b = getattr(_sym_info_b, "digits", 5) if _sym_info_b else 5
                    _req_be = {
                        "action":   _mt5_be.TRADE_ACTION_SLTP,
                        "position": _pb.ticket,
                        "symbol":   _pb.symbol,
                        "sl":       round(_entry_b, _digits_b),
                        "tp":       round(_tp_b, _digits_b),
                        "magic":    MAGIC_COPIER,
                    }
                    _res_be = _mt5_be.order_send(_req_be)
                    if _res_be and _res_be.retcode == _mt5_be.TRADE_RETCODE_DONE:
                        _auto_breakeven_done_tickets.add(_pb.ticket)
                        log.info(
                            f"🛡️ AutoBE: {_pb.symbol} ticket={_pb.ticket} | "
                            f"SL movido a entry {_entry_b} (profit ya >= {int(AUTO_BREAKEVEN_PCT*100)}% del camino al TP {_tp_b})"
                        )
                        # FIX 2026-05-20: anunciar "SL TO ENTRY" al VIP cuando auto-BE
                        # mueve el SL. Antes era silencioso ("INTERNO — sin mensaje al canal"
                        # por diseno original). Pero el cliente VIP no veia que su trade
                        # estaba protegido cuando el aliado no enviaba update de BE.
                        # Caso 20-may: AUDCAD y USDCAD movidos a BE silenciosamente,
                        # cliente no veia la proteccion del trade. Solo se anuncia si:
                        #  (a) hay senal abierta en tracker para este ticket
                        #  (b) no se anuncio ya BE para esa senal (_be_announced)
                        try:
                            _be_sid_match = None
                            _be_pair_d = _pb.symbol
                            with _signals_lock:
                                for _bs_sid, _bs_data in _open_signals.items():
                                    _bs_tkt = (_bs_data.get("mt5_ticket") or
                                               _bs_data.get("_mt5_ticket") or
                                               (_bs_data.get("signal", {}) or {}).get("mt5_ticket") or
                                               (_bs_data.get("signal", {}) or {}).get("_mt5_ticket") or 0)
                                    try:
                                        if int(_bs_tkt or 0) == int(_pb.ticket):
                                            _be_sid_match = _bs_sid
                                            _be_sig = _bs_data.get("signal", {}) or {}
                                            _be_pair_d = _get_display_pair(_be_sig.get("pair", _pb.symbol)) or _pb.symbol
                                            _be_already = bool(_bs_data.get("_be_announced"))
                                            break
                                    except (TypeError, ValueError):
                                        continue
                                else:
                                    _be_already = False
                            if _be_sid_match and not _be_already:
                                # FIX 2026-05-21: incluir direction + entry para
                                # desambiguar cuando hay multiples senales del mismo
                                # simbolo abiertas (caso 21-may 14:33: 2 ORO abiertas
                                # — SELL 14:18 y BUY 14:31 — mensaje "SL TO ENTRY — ORO"
                                # no indicaba a cual se referia).
                                _be_dir = (_be_sig.get("direction") or "").upper()
                                _be_emoji = "🟢" if _be_dir == "BUY" else "🔴"
                                _be_entry = _be_sig.get("mt5_entry") or _be_sig.get("entry") or 0
                                _be_entry_str = f" @ {fmt_price(_be_entry)}" if _be_entry else ""
                                _be_label = f"{_be_emoji} {_be_dir} — {_be_pair_d}{_be_entry_str}" if _be_dir else _be_pair_d
                                _be_msg = (
                                    f"🛡️ *SL TO ENTRY* — {_be_label}\n"
                                    f"🔐 Trade protected. Risk eliminated."
                                    f"{ELI_SIG}"
                                )
                                _be_msg = _safe_publish_vip(_be_msg, kind="update", pair=_be_pair_d) or _be_msg
                                if _be_msg and _can_publish_to_vip(_be_pair_d, event="autobe"):
                                    try:
                                        import requests  # FIX 2026-05-23: faltaba import local — bloqueaba todos los AutoBE publish
                                        _url_be = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                                        _payload_be = {
                                            "chat_id": CHANNEL_ID,
                                            "text": _be_msg,
                                            "parse_mode": "Markdown",
                                        }
                                        # Reply al mensaje original de la senal si lo tenemos
                                        _be_reply = None
                                        with _signals_lock:
                                            _be_sd = _open_signals.get(_be_sid_match, {}) or {}
                                            _be_reply = _be_sd.get("telegram_msg_id")
                                        if _be_reply:
                                            _payload_be["reply_to_message_id"] = _be_reply
                                        _resp_be = requests.post(_url_be, json=_payload_be, timeout=10)
                                        if _resp_be.status_code == 400 and "message to be replied" in _resp_be.text:
                                            _payload_be.pop("reply_to_message_id", None)
                                            requests.post(_url_be, json=_payload_be, timeout=10)
                                        log.info(f"📢 AutoBE notificado al VIP: {_be_pair_d} sid={_be_sid_match}")
                                        # Marcar dedup para que update del aliado no duplique
                                        with _signals_lock:
                                            if _be_sid_match in _open_signals:
                                                _open_signals[_be_sid_match]["_be_announced"] = True
                                        _save_open_signals()
                                    except Exception as _e_be_pub:
                                        log.warning(f"AutoBE publish error: {_e_be_pub}")
                        except Exception as _e_be_match:
                            log.debug(f"AutoBE announce match error: {_e_be_match}")
                        if len(_auto_breakeven_done_tickets) > 5000:
                            _auto_breakeven_done_tickets.clear()
                    else:
                        _rc_be = getattr(_res_be, 'retcode', 'N/A')
                        _cm_be = getattr(_res_be, 'comment', '')
                        log.warning(
                            f"⚠️ AutoBE FALLO: {_pb.symbol} ticket={_pb.ticket} retcode={_rc_be} {_cm_be}"
                        )
        except Exception as _e_be:
            log.warning(f"AutoBE error: {_e_be}")

        # ── AUTO-CLOSE AL 50% DE GANANCIAS (INTERNO — sin mensaje al canal) ──
        # Cierra cualquier posición del copier cuando alcanza la mitad del camino entry→TP.
        # Funciona para todos los activos. Silencioso: solo log, sin publicar al canal.
        try:
            import price_feed as _mt5_half
            _all_pos_half = _mt5_half.positions_get() or []
            for _p in _all_pos_half:
                if getattr(_p, "magic", 0) not in BS365_MAGICS:
                    continue
                if _p.ticket in _auto_half_closed_tickets:
                    continue
                _entry_h = _p.price_open
                _tp_h    = _p.tp        # TP configurado en la posición MT5
                if _tp_h <= 0 or _entry_h <= 0:
                    continue  # Sin TP fijado, no podemos calcular el 50%
                # Precio actual de mercado
                _tick_h = _mt5_half.symbol_info_tick(_p.symbol)
                if not _tick_h:
                    continue
                _cur_h = _tick_h.bid if _p.type == 0 else _tick_h.ask  # BUY→bid, SELL→ask
                # Calcular objetivo del 50%
                _is_buy_h = (_p.type == 0)  # ORDER_TYPE_BUY = 0
                if _is_buy_h:
                    _half_target = _entry_h + (_tp_h - _entry_h) * AUTO_HALF_CLOSE_PCT
                    _hit_half    = _cur_h >= _half_target
                else:
                    _half_target = _entry_h - (_entry_h - _tp_h) * AUTO_HALF_CLOSE_PCT
                    _hit_half    = _cur_h <= _half_target
                if not _hit_half:
                    continue
                # ── Cerrar la posición en MT5 silenciosamente ──
                _close_type  = _mt5_half.ORDER_TYPE_SELL if _is_buy_h else _mt5_half.ORDER_TYPE_BUY
                _close_price = _tick_h.bid if _is_buy_h else _tick_h.ask
                _req_half = {
                    "action":    _mt5_half.TRADE_ACTION_DEAL,
                    "position":  _p.ticket,
                    "symbol":    _p.symbol,
                    "volume":    _p.volume,
                    "type":      _close_type,
                    "price":     _close_price,
                    "deviation": 30,
                    "magic":     MAGIC_COPIER,
                    "comment":   f"AutoClose{int(AUTO_HALF_CLOSE_PCT*100)}%",
                    "type_time":    _mt5_half.ORDER_TIME_GTC,
                    "type_filling": _mt5_half.ORDER_FILLING_IOC,
                }
                _res_half = _mt5_half.order_send(_req_half)
                if _res_half and _res_half.retcode == _mt5_half.TRADE_RETCODE_DONE:
                    _auto_half_closed_tickets.add(_p.ticket)
                    _pips_half = abs(_close_price - _entry_h)
                    log.info(
                        f"💰 AutoClose{int(AUTO_HALF_CLOSE_PCT*100)}%: {_p.symbol} "
                        f"ticket={_p.ticket} | entry={_entry_h} → cierre={_close_price:.5f} "
                        f"(+{_pips_half:.2f}) | TP era {_tp_h}"
                    )
                    # Limpiar ticket de la memoria si pasa de 5000 entradas
                    if len(_auto_half_closed_tickets) > 5000:
                        _auto_half_closed_tickets.clear()
                else:
                    _rc = getattr(_res_half, 'retcode', 'N/A')
                    _cm = getattr(_res_half, 'comment', '')
                    log.warning(
                        f"⚠️ AutoClose50% FALLÓ: {_p.symbol} ticket={_p.ticket} "
                        f"retcode={_rc} {_cm}"
                    )
        except Exception as _e_half:
            log.warning(f"AutoClose50% error: {_e_half}")

        # ── Setup hora Andorra (compartido por los bloques de abajo) ──
        # FIX 2026-04-22: Resumen de las 22:00 DESACTIVADO — usuario pidió un solo resumen al día
        # a las 19:00 vía publisher completo (ver bloque siguiente). La variable _daily_summary_sent
        # queda por compatibilidad (por si se reactiva el resumen corto en el futuro).
        try:
            from datetime import datetime
            import pytz
            _tz_sum = pytz.timezone("Europe/Andorra")
            _now_sum = datetime.now(_tz_sum)
            _hoy_str = _now_sum.strftime("%d/%m/%Y")
        except Exception as _e_sum:
            log.warning(f"Error calculando hora Andorra: {_e_sum}")
            _now_sum = None
            _hoy_str = ""

        # ── FIX 2026-05-08: Resumen de fin de día (texto) — SOLO ADMIN privado a las 18:00 ──
        # Versión "C" elegida por el usuario: solo se envía al admin (TÚ), no al grupo
        # público. Sirve como tu propio resumen del día sin spammear al grupo.
        try:
            if (_now_sum and _now_sum.hour == 18 and _now_sum.minute < 5
                    and _sent_today.get("eod_text_admin") != _hoy_str):
                _sent_today["eod_text_admin"] = _hoy_str
                _save_sent_today(_sent_today)

                _trades_hoy = _load_copier_stats_today()
                _tps = [t for t in _trades_hoy if (t.get("result") or "").lower() == "tp"]
                _sls = [t for t in _trades_hoy if (t.get("result") or "").lower() == "sl"]
                _total = len(_tps) + len(_sls)
                _wr = (len(_tps) / _total * 100) if _total > 0 else 0.0
                _net_pips = sum((t.get("pips") or 0) for t in _trades_hoy)
                _net_emoji = "🟢" if _net_pips > 0 else ("🔴" if _net_pips < 0 else "⚪")

                # Gift signals de hoy
                _gold_p = (_gift_tracker.get("gold_pair") or "").upper()
                _other_p = (_gift_tracker.get("other_pair") or "").upper()
                _gold_r = _gift_tracker.get("gold_result")
                _other_r = _gift_tracker.get("other_result")
                _gifts_lines = []
                if _gold_p:
                    _emo = "✅" if _gold_r == "tp" else ("🛡️" if _gold_r == "sl" else "⏳")
                    _gifts_lines.append(f"{_emo} {_gold_p} (free)")
                if _other_p:
                    _emo = "✅" if _other_r == "tp" else ("🛡️" if _other_r == "sl" else "⏳")
                    _gifts_lines.append(f"{_emo} {_other_p} (free)")
                _gifts_block = (
                    f"\n🎁 *Gifts hoy:*\n" + "\n".join(_gifts_lines) + "\n"
                ) if _gifts_lines else ""

                _eod_msg = (
                    f"🏁 *RESUMEN DEL DÍA — {_hoy_str}*\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"📊 *Resultados:*\n"
                    f"✅ TPs: {len(_tps)}\n"
                    f"🛡️ SLs: {len(_sls)}\n"
                    f"📈 Win rate: {_wr:.0f}%\n"
                    f"{_net_emoji} *Net: {'+' if _net_pips >= 0 else ''}{_net_pips:.0f} pips*\n"
                    f"{_gifts_block}"
                )

                _admin_for_eod = ""
                try:
                    _admin_for_eod = (os.getenv("USER_ID_1") or os.getenv("ADMIN_ID") or "8696207137").strip()
                except Exception:
                    _admin_for_eod = "8696207137"

                if _admin_for_eod:
                    try:
                        import requests as _req_eod
                        _resp_eod = _req_eod.post(
                            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                            json={"chat_id": _admin_for_eod, "text": _eod_msg, "parse_mode": "Markdown"},
                            timeout=10,
                        )
                        if _resp_eod.status_code == 200:
                            log.info(f"🏁 Resumen EOD enviado al ADMIN (TPs={len(_tps)} SLs={len(_sls)} net={_net_pips:.0f})")
                        else:
                            log.warning(f"EOD admin post error: {_resp_eod.status_code} {_resp_eod.text[:80]}")
                    except Exception as _e_eod_post:
                        log.warning(f"Error enviando EOD al admin: {_e_eod_post}")
        except Exception as _e_eod:
            log.warning(f"EOD resumen error: {_e_eod}")

        # ── Alerta pre-resumen al admin (18:59) — DESACTIVADA 2026-05-12 ──
        # FIX 2026-05-12 (noche): usuario pidio eliminar este aviso al chat privado.
        # El resumen ya sale al canal/grupo automaticamente a las 19:00, no necesita
        # confirmacion previa. Solo dejamos log interno.
        try:
            if (_now_sum.hour == 18 and _now_sum.minute == 59
                    and _sent_today.get("pre_summary_alert") != _hoy_str):
                _sent_today["pre_summary_alert"] = _hoy_str
                _today_count = len(_load_copier_stats_today())
                log.info(f"⏰ Pre-resumen check: {_today_count} ops registradas hoy (alerta admin desactivada por usuario)")
        except Exception as _e_pre:
            log.warning(f"Pre-resumen check error: {_e_pre}")

        # ── FIX 2026-04-22: Publisher completo (imagen 1080x1920) a las 19:00 Andorra ──
        # Publica en: Canal VIP Telegram · Grupo gratis · Instagram Story · Highlight "Resultados" · Admin
        try:
            if _now_sum.hour == 19 and _now_sum.minute < 5 and _daily_publisher_sent != _hoy_str:
                _daily_publisher_sent = _hoy_str

                def _run_publisher():
                    try:
                        from daily_summary_publisher import publicar_resumen_diario
                        ok = publicar_resumen_diario(force=False)
                        log.info(f"📸 Publisher 19:00 — resultado: {'OK' if ok else 'sin eventos/ya publicado'}")
                    except Exception as _e_pub:
                        log.warning(f"Publisher 19:00 error: {_e_pub}")

                threading.Thread(target=_run_publisher, daemon=True,
                                 name="daily_publisher_19h").start()
                log.info("📸 Publisher 19:00 lanzado en thread")
        except Exception as _e_pub:
            log.warning(f"Error programando publisher 19:00: {_e_pub}")

        # ── 2026-05-05: Presentación Eli (solo GRUPO) a las 13:10 Andorra ──
        try:
            if _now_sum.hour == 13 and _now_sum.minute == 10 and _daily_eli_sent != _hoy_str:
                _daily_eli_sent = _hoy_str
                def _run_eli_presentation():
                    try:
                        import requests as _req
                        _tok = os.getenv("TELEGRAM_TOKEN", "")
                        _grp = os.getenv("GROUP_ID", "")
                        _img = Path(__file__).parent / "static" / "eli_presentation.png"
                        _es = (
                            "🇪🇸 *ESPAÑOL*\n\n"
                            "📌 *Por favor, leed este mensaje* 👇\n\n"
                            "Hola a todos 👋\n\nMe llamo *Eli*. 🤖\n\n"
                            "Mi creador me bautizó así, y cada día paso por aquí "
                            "a saludar a la comunidad. 👋\n\n"
                            "Llevo meses trabajando en silencio detrás de este canal y "
                            "este grupo — analizando mercados 📊, procesando datos y "
                            "ejecutando operaciones en tiempo real. ⚡\n\n"
                            "Soy inteligencia artificial. No tengo días malos, no me "
                            "canso, no opero con emociones. Solo con datos y precisión. 🎯\n\n"
                            "Soy rápida. Soy consistente. Cada resultado que veis publicado "
                            "es real, verificable y construido trade a trade, pip a pip. 💎\n\n"
                            "Pero seré honesta — aún estoy creciendo. 🌱 A veces me "
                            "equivoco, pero mi creador me actualiza cada día para ser mejor. 🚀\n\n"
                            "💎 *Solo los miembros del Canal VIP tienen asistencia personalizada conmigo.*\n\n"
                            "Me alegra estar aquí con vosotros cada día. 🤖✨\n\n"
                            "*— Eli · BuySell365 Pro 🤖*"
                        )
                        _en = (
                            "🇬🇧 *ENGLISH*\n\n"
                            "📌 *Please read this message* 👇\n\n"
                            "Hello everyone 👋\n\nMy name is *Eli*. 🤖\n\n"
                            "My creator gave me this name, and every day I stop by "
                            "to greet the community. 👋\n\n"
                            "I've been working silently behind this channel and this group "
                            "for months — analyzing markets 📊, processing data and "
                            "executing operations in real time. ⚡\n\n"
                            "I am artificial intelligence. No bad days, no fatigue, no "
                            "emotions. Only data and precision. 🎯\n\n"
                            "I'm fast. I'm consistent. Every result you see published is "
                            "real, verifiable and built trade by trade, pip by pip. 💎\n\n"
                            "But I'll be honest — I'm still growing. 🌱 Sometimes I make "
                            "mistakes, but my creator updates me every day to be better. 🚀\n\n"
                            "💎 *Only VIP Channel members have personalized assistance with me.*\n\n"
                            "Happy to be here with you every day. 🤖✨\n\n"
                            "*— Eli · BuySell365 Pro 🤖*"
                        )
                        for _cap in [_es, _en]:
                            with open(str(_img), "rb") as _f:
                                _req.post(
                                    f"https://api.telegram.org/bot{_tok}/sendPhoto",
                                    data={"chat_id": _grp, "caption": _cap, "parse_mode": "Markdown"},
                                    files={"photo": _f}, timeout=30
                                )
                            import time as _t; _t.sleep(2)
                        log.info("🤖 Presentación Eli 13:10 enviada al grupo")
                    except Exception as _e_eli:
                        log.warning(f"Eli presentation error: {_e_eli}")
                threading.Thread(target=_run_eli_presentation, daemon=True, name="eli_13h").start()
        except Exception as _e_eli_sched:
            log.warning(f"Error programando Eli 13:10: {_e_eli_sched}")

        # Transparency 20:15 ELIMINADO 2026-05-24: ya no trabajamos con MT5,
        # el publisher de credenciales investor ha sido borrado por completo.

        # ── Publicidad diaria de Instagram en grupo Telegram (14:00) ──
        try:
            if _now_sum.hour == 14 and _now_sum.minute < 5 and _sent_today.get("ig_promo") != _hoy_str:
                _sent_today["ig_promo"] = _hoy_str
                try:
                    _tmp_st = str(COPIER_SENT_STATE_FILE) + ".tmp"
                    with open(_tmp_st, "w", encoding="utf-8") as _f_st:
                        _f_st.write(json.dumps(_sent_today, ensure_ascii=False))
                    os.replace(_tmp_st, str(COPIER_SENT_STATE_FILE))
                except Exception:
                    pass
                import random
                import requests as _req_ig
                # FIX 2026-04-26: traducido a INGLES + quitado "los buenos
                # y los malos" (decision usuario: solo enfocarse en wins).
                # FIX 2026-05-24: eliminado bloque MT5/credentials (ya no trabajamos con MT5)
                _ig_captions = [
                    "📸 *Follow us on Instagram!*\n\n"
                    "We post daily results, TPs hit and exclusive content.\n\n"
                    "🔗 @buysell365.pro\\_tradingsignals\n"
                    "👉 instagram.com/buysell365.pro\\_tradingsignals\n\n"
                    "_Real results, every day_",

                    "🚀 *BuySell365 is on Instagram*\n\n"
                    "📊 Daily results\n"
                    "🎯 Every TP celebrated\n"
                    "📈 Weekly stats\n\n"
                    "Follow us: @buysell365.pro\\_tradingsignals\n"
                    "👉 instagram.com/buysell365.pro\\_tradingsignals",

                    "📱 *Already following us on Instagram?*\n\n"
                    "Real winning trades published daily.\n"
                    "Live results.\n\n"
                    "🔗 @buysell365.pro\\_tradingsignals\n"
                    "_BuySell365 Pro_",
                ]
                _ig_caption = random.choice(_ig_captions)
                # Generar imagen promo con logo de Instagram
                try:
                    from instagram_poster import generate_ig_promo_image
                    _promo_img_path = generate_ig_promo_image()
                    _url_ig = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                    with open(str(_promo_img_path), "rb") as _img_f:
                        _resp_ig = _req_ig.post(_url_ig, data={
                            "chat_id": GROUP_ID,
                            "caption": _ig_caption,
                            "parse_mode": "Markdown"
                        }, files={"photo": _img_f}, timeout=15)
                except Exception as _img_err:
                    log.debug(f"Promo imagen error, enviando texto: {_img_err}")
                    _url_ig = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                    _resp_ig = _req_ig.post(_url_ig, json={
                        "chat_id": GROUP_ID, "text": _ig_caption, "parse_mode": "Markdown"
                    }, timeout=10)
                if _resp_ig.status_code == 200:
                    log.info("📸 Promo Instagram enviada al grupo (con imagen)")
                else:
                    log.warning(f"Promo Instagram error: {_resp_ig.status_code}")
        except Exception as _e_ig:
            log.warning(f"Error promo Instagram: {_e_ig}")

        # ── Posts programados de Instagram DESACTIVADOS ──
        # Solo se publican TPs en Instagram (con carrusel + gráfico)
        # Motivacionales, tips y resúmenes desactivados por calidad insuficiente

        # ── Auto-follow / auto-unfollow DESACTIVADOS 2026-04-18 ──
        # Desactivado por el usuario: Instagram había puesto spam flag
        # (feedback_required) por la actividad de auto-follow. Mantener solo
        # los posts de TP/cierre celebraciones (que ya tienen rate-limit).

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

        # ── Limpieza de sets que crecen sin límite (con lock) ──
        with _signals_lock:
            if len(_published_msg_ids) > 2000:
                _sorted = sorted(_published_msg_ids)
                _published_msg_ids.clear()
                _published_msg_ids.update(_sorted[-500:])
                log.info(f"🧹 _published_msg_ids limpiado: 2000+ → 500")
            if len(_resolved_signals) > 2000:
                _resolved_signals.clear()
                log.info(f"🧹 _resolved_signals limpiado (>2000 entradas)")

        with _signals_lock:
            signals_copy = dict(_open_signals)

        # FIX 2026-04-25: Saltar polling de precio MT5 para forex/indices/metales
        # cuando el mercado esta cerrado (fin de semana). Crypto sigue 24/7.
        # Antes: cada 30s polleabamos USDJPY 2x + GBPUSD 1x los sabados sin que se
        # mueva ni un pip → ruido en log + uso inutil de MT5.
        _market_closed = _is_forex_market_closed()

        # FIX 2026-04-26: detectar transicion CLOSED -> OPEN para reintentar
        # senhales que MT5 rechazo durante el fin de semana.
        global _market_was_closed
        if _market_was_closed is True and _market_closed is False:
            log.info("🔓 Mercado FOREX abre — verificando senhales pendientes…")
            try:
                _retry_pending_market_open_signals()
            except Exception as _e_retry_loop:
                log.warning(f"Error en reintento al abrir mercado: {_e_retry_loop}")
        _market_was_closed = _market_closed

        if _market_closed and signals_copy and time.time() > _market_closed_logged_until:
            _non_crypto = sum(1 for _s in signals_copy.values() if not _is_24_7_asset(_s["signal"].get("pair", "")))
            if _non_crypto > 0:
                log.info(f"💤 Mercado forex/indices CERRADO (fin de semana) — pausando polling de {_non_crypto} señales no-crypto")
                _market_closed_logged_until = time.time() + 1800  # log cada 30 min mientras dure

        to_resolve = []
        _already_in_cycle = set()  # Anti-duplicado: un TP/SL por par+dir por ciclo
        for sig_id, sdata in signals_copy.items():
            signal = sdata["signal"]
            direction = signal["direction"]
            sl = signal["sl"]
            pair = signal["pair"]
            age_hours = (time.time() - sdata["sent_at"]) / 3600

            # FIX 2026-04-25: Skip silencioso si el mercado esta cerrado y el activo
            # no es 24/7. La señal sigue en seguimiento — solo no consultamos precio
            # hasta que reabra. TP/SL no se pueden tocar con mercado cerrado.
            if _market_closed and not _is_24_7_asset(pair):
                continue

            # FIX 2026-04-10: Filtrar TPs basura ANTES de seleccionar _tp_final
            _entry_ref = signal.get("entry", 0) or 0
            _tp1 = signal.get("tp", 0) or 0
            _tp2 = signal.get("tp2", 0) or 0
            _tp3 = signal.get("tp3", 0) or 0
            _tp4 = signal.get("tp4", 0) or 0
            _tp5 = signal.get("tp5", 0) or 0
            _valid_tps = []
            for _tval in [_tp1, _tp2, _tp3, _tp4, _tp5]:
                if _tval <= 0:
                    continue
                # Descartar si >20% de diferencia con entry
                if _entry_ref > 0 and abs(_tval - _entry_ref) / _entry_ref > 0.20:
                    continue
                # Descartar si dirección incorrecta (BUY→TP>entry, SELL→TP<entry)
                if _entry_ref > 0:
                    if direction == "BUY" and _tval < _entry_ref:
                        continue
                    if direction == "SELL" and _tval > _entry_ref:
                        continue
                _valid_tps.append(_tval)

            # FIX 2026-04-16: Monitorear TP1 (más cercano) primero, NO el último
            # BUY: ascendente (TP1 < TP2 < TP3), SELL: descendente (TP1 > TP2 > TP3)
            if direction == "BUY":
                _valid_tps.sort()
            else:
                _valid_tps.sort(reverse=True)
            # Almacenar niveles para tracking multi-TP (persiste en JSON)
            if "_tp_levels" not in signal:
                signal["_tp_levels"] = list(_valid_tps)
                signal["_tp_idx"] = 0
            _tp_idx_cur = signal.get("_tp_idx", 0)
            _tp_levels_cur = signal.get("_tp_levels", _valid_tps)
            if _tp_idx_cur < len(_tp_levels_cur):
                tp = _tp_levels_cur[_tp_idx_cur]
            elif _tp_levels_cur:
                tp = _tp_levels_cur[-1]
            else:
                tp = 0

            # Actualizar el signal con el TP actual para celebración
            signal["_tp_final"] = tp

            # Auto-expire after 72h to avoid zombie tracking
            # FIX 2026-04-17: 48h→72h (alineado con _load_open_signals)
            if age_hours > 72:
                to_resolve.append((sig_id, sdata, "expired"))
                continue

            # FIX 2026-04-21: WATCHDOG — si llevamos >60 fallos de precio consecutivos
            # (≈30 min con monitor cada 30s) cerrar como huérfana. Mejor un cierre con
            # neto al precio actual (cuando se recupere) que dejar la señal en limbo.
            if signal.get("_price_fails", 0) >= 60:
                log.warning(f"🪦 Watchdog huérfana: {pair} {direction} con {signal['_price_fails']} fallos → cerrando como orphan")
                to_resolve.append((sig_id, sdata, "orphan"))
                continue

            # FIX 2026-05-05: CIERRE FIN DE DÍA — DESACTIVADO por peticion del usuario
            # Antes (FIX 2026-04-21) cerrabamos automaticamente entre 23:50 y 23:59 toda
            # senal >6h sin TP. Problema: cerraba forex con TPs lejanos antes de que
            # tocaran TP natural, publicaba "END OF DAY CLOSE" al canal VIP creando
            # falsa impresion de "perdida del dia" cuando en realidad la senal podia
            # ir bien al dia siguiente. Tampoco contemplaba que muchos forex hold
            # trades 24-72h legitimamente.
            #
            # Ahora: las senales viven hasta su TP/SL natural o hasta los 72h de TTL
            # automatico (linea 4795). MT5 cierra por TP/SL del broker.
            #
            # Re-activar con env var EOD_FORCE_CLOSE_ENABLED=true (no recomendado).
            try:
                _eod_enabled = os.getenv("EOD_FORCE_CLOSE_ENABLED", "false").lower() in ("true","1","yes")
                if _eod_enabled:
                    from datetime import datetime as _dt_eod
                    import pytz as _pytz_eod
                    _now_and = _dt_eod.now(_pytz_eod.timezone("Europe/Andorra"))
                    if _now_and.hour == 23 and _now_and.minute >= 50 and age_hours > 6:
                        if not signal.get("_tps_alcanzados"):
                            log.info(f"🌙 EOD cierre: {pair} {direction} ({age_hours:.1f}h sin TP)")
                            to_resolve.append((sig_id, sdata, "eod"))
                            continue
            except Exception:
                pass

            # FIX 2026-04-09: Usar precio MT5 (broker real) en vez de yfinance
            price = None
            try:
                import price_feed as _mt5_check
                # FIX 2026-04-12: Mapa COMPLETO — todos los pares que SYMBOL_MAP puede recibir
                _mt5_sym_map = {
                    # Oro
                    "GOLD": "GOLD", "XAUUSD": "GOLD", "ORO": "GOLD",
                    # Plata (FIX 2026-05-08)
                    "SILVER": "SILVER", "XAGUSD": "SILVER", "PLATA": "SILVER",
                    # Índices
                    "NAS100": "US100Cash", "NASDAQ": "US100Cash", "US100": "US100Cash", "US100CASH": "US100Cash", "NQ": "US100Cash",
                    "US30": "US30Cash", "DOW": "US30Cash", "DOW30": "US30Cash", "DJ30": "US30Cash", "US30CASH": "US30Cash",
                    "US500": "US500Cash", "SP500": "US500Cash", "SPX500": "US500Cash", "US500CASH": "US500Cash",
                    "GER40": "GER40Cash", "GER40CASH": "GER40Cash", "DAX": "GER40Cash", "DE40": "GER40Cash",
                    # Petróleo
                    "USOIL": "OILCash", "USOILCASH": "OILCash", "OILCASH": "OILCash", "OIL": "OILCash", "WTI": "OILCash",
                    "BRENT": "BRENTCash", "UKOIL": "BRENTCash",
                    # Crypto — XM usa "BTCUSD" (no BTCUSDm)
                    "BTCUSD": "BTCUSD", "BTCUSDM": "BTCUSD",
                    # Forex — pares USD
                    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "AUDUSD": "AUDUSD",
                    "NZDUSD": "NZDUSD", "USDCAD": "USDCAD", "USDCHF": "USDCHF", "USDJPY": "USDJPY",
                    # Forex — pares GBP
                    "GBPJPY": "GBPJPY", "GBPAUD": "GBPAUD", "GBPNZD": "GBPNZD",
                    "GBPCAD": "GBPCAD", "GBPCHF": "GBPCHF",
                    # Forex — pares EUR
                    "EURJPY": "EURJPY", "EURAUD": "EURAUD", "EURGBP": "EURGBP",
                    "EURCHF": "EURCHF", "EURCAD": "EURCAD", "EURNZD": "EURNZD",
                    # Forex — pares AUD
                    "AUDJPY": "AUDJPY", "AUDCAD": "AUDCAD", "AUDNZD": "AUDNZD", "AUDCHF": "AUDCHF",
                    # Forex — pares JPY cruzados
                    "NZDJPY": "NZDJPY", "CADJPY": "CADJPY", "CHFJPY": "CHFJPY",
                    # Forex — otros
                    "NZDCAD": "NZDCAD", "NZDCHF": "NZDCHF", "CADCHF": "CADCHF",
                }
                # Resolver símbolo MT5: primero mapa, luego quitar /
                _pair_clean = pair.upper().replace("/", "")
                _mt5_sym = _mt5_sym_map.get(_pair_clean, _pair_clean)
                if _mt5_check.initialize():
                    # FIX 2026-04-21: Auto-detectar sufijo del broker (XM "m", IC ".", etc).
                    # Si el símbolo limpio no existe, intentar variantes comunes para no
                    # dejar pares Forex sin precio cuando el broker añade sufijo.
                    _resolved_sym = _mt5_sym
                    if not _mt5_check.symbol_info(_resolved_sym):
                        for _suffix in ("m", "c", "i", ".pro", ".raw", ".m", "_pro"):
                            _alt = f"{_mt5_sym}{_suffix}"
                            if _mt5_check.symbol_info(_alt):
                                _resolved_sym = _alt
                                log.info(f"🔧 Símbolo broker detectado: {_mt5_sym} → {_resolved_sym}")
                                break
                    _mt5_check.symbol_select(_resolved_sym, True)
                    _tick = _mt5_check.symbol_info_tick(_resolved_sym)
                    if _tick and _tick.bid > 0:
                        # FIX 2026-04-16: Usar bid para BUY, ask para SELL (precio real de cierre)
                        # BUY cierra al bid, SELL cierra al ask — no usar mid-price
                        if direction == "BUY":
                            price = _tick.bid  # BUY se cierra al bid
                        else:
                            price = _tick.ask  # SELL se cierra al ask
                        # FIX 2026-05-09: log solo si cambia precio o cada 5min (heartbeat).
                        # Reduce ruido en logs durante mercados cerrados o periodos planos.
                        _now_pl = time.time()
                        _prev_pl = _price_log_cache.get(_resolved_sym)
                        _changed = (
                            _prev_pl is None
                            or _prev_pl[0] != _tick.bid
                            or _prev_pl[1] != _tick.ask
                            or (_now_pl - _prev_pl[2]) > 300  # heartbeat cada 5min
                        )
                        if _changed:
                            _price_log_cache[_resolved_sym] = (_tick.bid, _tick.ask, _now_pl)
                            log.info(
                                f"💹 Precio MT5 {_resolved_sym}: bid={_tick.bid:.5f} ask={_tick.ask:.5f} -> usando {price:.5f}"
                                if price < 100
                                else f"💹 Precio MT5 {_resolved_sym}: bid={_tick.bid:.2f} ask={_tick.ask:.2f} -> usando {price:.2f}"
                            )
                        else:
                            log.debug(
                                f"Precio MT5 {_resolved_sym} sin cambios: bid={_tick.bid:.5f} ask={_tick.ask:.5f}"
                            )
            except Exception as _e_mt5_price:
                log.debug(f"MT5 price err {pair}: {_e_mt5_price}")
            # Fallback a yfinance solo si MT5 no disponible
            if price is None:
                price = _get_current_price(pair)
            if price is None:
                # FIX 2026-04-21: Antes era skip silencioso → señales huérfanas sin TP/SL.
                # Ahora contamos fallos por señal: tras N intentos consecutivos avisamos por log
                # y dejamos que el watchdog la procese (no la perdemos).
                _fails = signal.get("_price_fails", 0) + 1
                signal["_price_fails"] = _fails
                if _fails in (1, 5, 20, 60):
                    log.warning(f"⚠️ Sin precio para {pair} {direction} (fallos={_fails}) — MT5+yfinance+TwelveData fallaron")
                continue
            else:
                # Resetear contador en cuanto vuelve a haber precio
                if signal.get("_price_fails"):
                    log.info(f"✅ Precio recuperado para {pair} {direction} tras {signal['_price_fails']} fallos")
                    signal["_price_fails"] = 0

            # FIX 2026-04-09: Si entry=0, asignar precio live al primer chequeo
            _entry = signal.get("entry", 0) or 0
            if _entry <= 0:
                signal["entry"] = price
                log.info(f"📍 Entry auto-asignado en monitor: {price} para {pair}")

            # TP/SL hit checks — solo verificar si el valor existe (>0)
            tp_hit = False
            if tp > 0:
                tp_hit = (direction == "BUY" and price >= tp) or (direction == "SELL" and price <= tp)
            sl_hit = False
            if sl > 0:
                sl_hit = (direction == "BUY" and price <= sl) or (direction == "SELL" and price >= sl)

            # FIX 2026-04-30: SL-FIRST LOOKBACK — si tp_hit detectado, verificar el
            # historial de velas (MT5) entre signal_sent_at y ahora para confirmar
            # que el SL no fue tocado ANTES que el TP. Si SL primero → es SL HIT,
            # no TP HIT (caso GBP/JPY 30/04: SELL 212.68/SL 212.77, precio subio
            # +130 pips a 214.00 antes de bajar a TP 212.50; se publico TP HIT
            # falso, suscriptores confundidos).
            # Si SL == 0: imposible validar — celebramos TP HIT pero log WARNING
            # claro y signal["_tp_sin_sl_warning"] para nota en mensaje canal.
            # NOTA: sent_at vive en sdata (outer dict), no en signal. Fallback a
            # signal["timestamp"] (parser) si por algun motivo sdata no lo tiene.
            _sent_at_lb = sdata.get("sent_at", 0) if isinstance(sdata, dict) else 0
            if not _sent_at_lb:
                _sent_at_lb = signal.get("timestamp", 0) or signal.get("sent_at", 0) or 0
            if tp_hit and sl > 0 and _sent_at_lb > 0:
                try:
                    import price_feed as _mt5_lookback
                    from datetime import datetime as _dt_lb
                    # Resolver simbolo defensivo (puede que _resolved_sym no este definido
                    # si el MT5 init de arriba fallo y caimos a yfinance).
                    _resolved_lb = locals().get("_resolved_sym")
                    if not _resolved_lb:
                        _pair_clean_lb = pair.upper().replace("/", "")
                        _resolved_lb = _pair_clean_lb  # mejor que nada
                    if _mt5_lookback.initialize():
                        # Si el simbolo no existe, intentar sufijos broker como en el bloque arriba
                        if not _mt5_lookback.symbol_info(_resolved_lb):
                            for _suffix_lb in ("m", "c", "i", ".pro", ".raw", ".m", "_pro"):
                                _alt_lb = f"{_resolved_lb}{_suffix_lb}"
                                if _mt5_lookback.symbol_info(_alt_lb):
                                    _resolved_lb = _alt_lb
                                    break
                        _from_dt = _dt_lb.fromtimestamp(_sent_at_lb)
                        _to_dt   = _dt_lb.now()
                        _bars_lb = _mt5_lookback.copy_rates_range(
                            _resolved_lb, _mt5_lookback.TIMEFRAME_M1, _from_dt, _to_dt
                        )
                        if _bars_lb is not None and len(_bars_lb) > 0:
                            # FIX 2026-05-01: filtrar bars que sean DESPUÉS de sent_at.
                            # MT5 copy_rates_range puede devolver velas anteriores al rango
                            # solicitado si caen cerca del borde. Bug real hoy 09:39:
                            # SELL ORO publicada 09:38 con SL=4605, vela vieja del dia
                            # (oro estuvo en 4621+ a las 06:00) tenia high >= 4605 y
                            # disparo "SL-FIRST" falsamente reclasificando TP -> SL.
                            # Filtramos a bars cuyo time >= sent_at (con margen de 30s).
                            _sent_at_int = int(_sent_at_lb)
                            _bars_lb = [_b for _b in _bars_lb if int(_b['time']) >= (_sent_at_int - 30)]
                            # Necesitamos al menos 2 bars para discriminar SL primero vs TP primero
                            if len(_bars_lb) < 2:
                                log.debug(
                                    f"SL-FIRST skip {pair} {direction}: solo {len(_bars_lb)} bars desde "
                                    f"sent_at — datos insuficientes para validar"
                                )
                            else:
                                _sl_first_idx = -1
                                _tp_first_idx = -1
                                for _bi, _b in enumerate(_bars_lb):
                                    _bh = float(_b['high'])
                                    _bl = float(_b['low'])
                                    # SL hit?
                                    if _sl_first_idx < 0:
                                        if (direction == "BUY" and _bl <= sl) or (direction == "SELL" and _bh >= sl):
                                            _sl_first_idx = _bi
                                    # TP hit?
                                    if _tp_first_idx < 0:
                                        if (direction == "BUY" and _bh >= tp) or (direction == "SELL" and _bl <= tp):
                                            _tp_first_idx = _bi
                                    # Si tenemos los dos, podemos parar
                                    if _sl_first_idx >= 0 and _tp_first_idx >= 0:
                                        break
                                # FIX 2026-05-01: condicion estricta REAL — solo reclasificar
                                # como SL HIT si AMBOS indices son validos Y SL fue antes que TP.
                                # Si TP no se encuentra en M1 lookback (_tp_first_idx == -1) pero
                                # el monitor ya lo detecto por TICK LIVE (precio_actual cruzo TP),
                                # confiar en el tick — NO degradar a SL HIT solo porque las velas
                                # M1 no llegaron al broker. El lookback es complementario, no
                                # autoridad sobre el tick. Bug que mato la SELL ORO 4590 hoy.
                                if _sl_first_idx >= 0 and _tp_first_idx >= 0 and _sl_first_idx < _tp_first_idx:
                                    log.warning(
                                        f"🛑 SL-FIRST detectado {pair} {direction}: SL={sl} cruzado "
                                        f"en bar {_sl_first_idx} (M1, post-sent_at) ANTES que TP={tp} "
                                        f"(bar {_tp_first_idx}) — reclasificando TP HIT → SL HIT"
                                    )
                                    tp_hit = False
                                    sl_hit = True
                                elif _sl_first_idx >= 0 and _tp_first_idx < 0:
                                    # Caso ambiguo: SL aparece en bars pero TP no. Confiamos en el
                                    # tick live que ya detecto tp_hit. Logueamos para auditoria.
                                    log.warning(
                                        f"⚠️ SL-FIRST AMBIGUO {pair} {direction}: SL={sl} en bar "
                                        f"{_sl_first_idx} pero TP={tp} no en M1 lookback "
                                        f"(_tp_first_idx=-1). Confiando en TP HIT del tick live "
                                        f"— no reclasificando."
                                    )
                                else:
                                    log.info(
                                        f"✅ SL-FIRST check OK {pair} {direction}: TP en bar {_tp_first_idx}, "
                                        f"SL en bar {_sl_first_idx} (TP primero o SL no tocado)"
                                    )
                except Exception as _e_lb:
                    log.debug(f"SL-first lookback error {pair}: {_e_lb}")
            elif tp_hit and sl <= 0:
                # SL no especificado — imposible validar SL-first. Marcamos warning
                # para que el mensaje del canal lo refleje y log claro para auditoria.
                log.warning(
                    f"⚠️ TP HIT sin SL en {pair} {direction}: imposible validar SL-FIRST "
                    f"(no se puede saber si SL fue tocado antes). Celebrando con nota de transparencia."
                )
                signal["_tp_sin_sl_warning"] = True

            # FIX 2026-04-27: detectar DRIFT — si hay mt5_entry (precio real
            # ejecutado en MT5) y el TP esta del lado equivocado vs ese entry,
            # disparar tp_hit seria celebrar una "ganancia" que en MT5 real es
            # PERDIDA. Caso GBPUSD 27/04: entry teorico 1.35320, MT5 ejecuto a
            # 1.35118, TP teorico 1.35160 → cumplio para "teorico" pero MT5
            # real cerro en perdida. Convertir a orphan honesto.
            if tp_hit:
                _mt5_e_drift = signal.get("mt5_entry", 0) or 0
                if _mt5_e_drift > 0:
                    _tp_lado_correcto = (
                        (direction == "BUY" and tp > _mt5_e_drift) or
                        (direction == "SELL" and tp < _mt5_e_drift)
                    )
                    if not _tp_lado_correcto:
                        log.warning(
                            f"⚠️ DRIFT detectado {pair} {direction}: TP={tp} esta "
                            f"del lado equivocado vs mt5_entry={_mt5_e_drift} — "
                            f"cerrando como drift (no celebrar TP falso)"
                        )
                        tp_hit = False
                        _dedup_key_drift = f"{pair}_{direction}_drift"
                        if _dedup_key_drift not in _already_in_cycle:
                            _already_in_cycle.add(_dedup_key_drift)
                            # FIX 2026-04-28: usar reason="drift" en lugar de "orphan"
                            # para que el mensaje al canal diga "drift detected" en
                            # vez de "no broker price" (engañoso — broker SI responde).
                            to_resolve.append((sig_id, sdata, "drift"))
                        else:
                            with _signals_lock:
                                _open_signals.pop(sig_id, None)
                            _resolved_signals.add(sig_id)
                        continue

            if tp_hit:
                # FIX 2026-04-16: Anti-duplicado dentro del mismo ciclo
                _dedup_key = f"{pair}_{direction}_tp{signal.get('_tp_idx', 0)}"
                if _dedup_key not in _already_in_cycle:
                    _already_in_cycle.add(_dedup_key)
                    to_resolve.append((sig_id, sdata, "tp"))
                else:
                    # Misma señal de otro canal — solo remover sin celebrar
                    with _signals_lock:
                        _open_signals.pop(sig_id, None)
                    _resolved_signals.add(sig_id)
                    log.info(f"🔕 TP duplicado en ciclo ignorado: {pair} {direction} (otro canal)")
            elif sl_hit:
                # FIX 2026-05-06: Verificar que el ticket MT5 sigue abierto antes de declarar SL.
                # Si la posición ya fue cerrada manualmente por el usuario, NO mandar "STOP LOSS"
                # al canal VIP — evita asustar a suscriptores con notificaciones falsas de SL.
                # FIX 2026-05-12: leer ticket desde ambos campos (mt5_ticket / _mt5_ticket).
                # btc_eth_generator guarda como `mt5_ticket`, copier como `_mt5_ticket`.
                # FIX 2026-05-12: asegurar sesion MT5 con login antes del check — sin login,
                # positions_get devuelve () silencioso para tickets vivos. Si init+login falla
                # o account_info=None, NO popear: dejar que el monitor reintente proximo ciclo.
                _mt5_ticket_sl = signal.get("_mt5_ticket", 0) or signal.get("mt5_ticket", 0)
                if _mt5_ticket_sl:
                    try:
                        _ok_login, _r_login = _mt5_init_and_login()
                        _acc_chk = _mt5_check.account_info() if _ok_login else None
                        if not _ok_login or _acc_chk is None:
                            # Sesion no autenticada → NO confiar en positions_get vacio.
                            # Dejamos seguir el flujo normal (to_resolve → notification con verify).
                            log.warning(
                                f"⚠️ Pre-SL ticket check {pair}: sesion MT5 no autenticada "
                                f"({_r_login if not _ok_login else 'account_info=None'}). "
                                f"Saltando guard, sigue al monitor con verify mejorado."
                            )
                        else:
                            _pos_check_sl = _mt5_check.positions_get(ticket=int(_mt5_ticket_sl))
                            if _pos_check_sl is not None and len(_pos_check_sl) == 0:
                                # FIX 2026-05-15: Ticket cerrado → consultar deal de cierre
                                # antes de skip. Si el reason es SL/TP, debemos notificar al VIP.
                                # Antes asumiamos "manual close" y se perdian SLs reales (2 caso
                                # 15-may ORO 769263742 y 769848851: ambos SL en MT5, cero aviso VIP).
                                _close_reason = None
                                try:
                                    _h_deals_sl = _mt5_check.history_deals_get(position=int(_mt5_ticket_sl)) or []
                                    for _hd in _h_deals_sl:
                                        if getattr(_hd, "entry", 0) == _mt5_check.DEAL_ENTRY_OUT:
                                            _close_reason = getattr(_hd, "reason", None)
                                            break
                                except Exception as _e_hd_sl:
                                    log.debug(f"history_deals_get sl check {pair}: {_e_hd_sl}")
                                # MT5 deal reason codes:
                                #   4 = DEAL_REASON_SL, 5 = DEAL_REASON_TP, 6 = DEAL_REASON_SO
                                #   0 = client manual, 1 = mobile, 2 = web, 3 = expert (our EA)
                                _DEAL_REASON_SL = 4
                                _DEAL_REASON_SO = 6
                                if _close_reason is not None and _close_reason in (_DEAL_REASON_SL, _DEAL_REASON_SO):
                                    log.warning(
                                        f"🛑 SL/SO confirmado por deal MT5 #{_mt5_ticket_sl} "
                                        f"({pair} {direction}, reason={_close_reason}) — notificando al VIP"
                                    )
                                    # NO skip: dejar caer al flujo to_resolve para que envie SL
                                else:
                                    log.info(
                                        f"🔕 SL-PRECIO detectado {pair} {direction} pero ticket MT5 "
                                        f"#{_mt5_ticket_sl} cerrado por reason={_close_reason} "
                                        f"(no SL/SO). NO se notifica STOP LOSS al canal VIP."
                                    )
                                    with _signals_lock:
                                        _open_signals.pop(sig_id, None)
                                    _resolved_signals.add(sig_id)
                                    _save_open_signals()
                                    continue
                    except Exception as _e_tc:
                        log.debug(f"Ticket pos check {pair}: {_e_tc}")
                # FIX 2026-05-21 (Bug B): incluir sig_id en la dedup key del ciclo.
                # Antes: "{pair}_{direction}_sl" — colision entre multiples senales
                # del mismo simbolo+direction. Caso 21-may: 2 BUY ORO abiertas
                # simultaneamente, una hit SL y la otra quedaba silenciada al toparse
                # con la misma key en el mismo cycle. Ahora cada sig_id se procesa
                # independientemente; la dedup cross-canal real vive en
                # _send_sl_notification (per-ticket 4h) y _recently_notified.
                _dedup_key_sl = f"{pair}_{direction}_sl_{sig_id}"
                if _dedup_key_sl not in _already_in_cycle:
                    _already_in_cycle.add(_dedup_key_sl)
                    to_resolve.append((sig_id, sdata, "sl"))
                else:
                    with _signals_lock:
                        _open_signals.pop(sig_id, None)
                    _resolved_signals.add(sig_id)
                    log.info(f"🔕 SL duplicado en ciclo ignorado: {pair} {direction} sig={sig_id}")

        for sig_id, sdata_resolved, result in to_resolve:
          try:
            signal   = sdata_resolved["signal"] if isinstance(sdata_resolved, dict) and "signal" in sdata_resolved else sdata_resolved
            _reply_id = signals_copy.get(sig_id, {}).get("telegram_msg_id") if isinstance(signals_copy.get(sig_id), dict) else None
            with _signals_lock:
                _open_signals.pop(sig_id, None)
            _resolved_signals.add(sig_id)
            _save_open_signals()
            _save_resolved_signals()  # FIX 2026-05-01: persistir tras cada resolucion

            if result in ("tp", "sl"):
                # FIX 2026-04-16: Dedup key incluye nivel TP para multi-TP
                _tp_num_r = signal.get("_tp_idx", 0) + 1 if result == "tp" else 0
                _notif_key = f"{signal.get('pair','')}_{signal.get('direction','')}_{result}{_tp_num_r}"
                _prev_notif = _recently_notified.get(_notif_key, 0)
                if _prev_notif and (time.time() - _prev_notif) < 300:
                    log.info(f"🔕 {result.upper()} duplicado ignorado: {_notif_key} (notificado hace {time.time()-_prev_notif:.0f}s)")
                    continue
                _recently_notified[_notif_key] = time.time()
                _now_notif = time.time()
                _stale_keys = [k for k, v in _recently_notified.items() if _now_notif - v >= 1800]
                for _sk in _stale_keys:
                    _recently_notified.pop(_sk, None)
                # FIX 2026-05-06 (Capa A): persistir tras cada update — sobrevive reinicio
                _save_notif_dedup()

                _entry = signal.get('entry', 0) or 0
                if _entry <= 0:
                    _live_e = _get_current_price(signal.get("pair", ""))
                    if _live_e and _live_e > 0:
                        signal["entry"] = _live_e
                        _entry = _live_e
                    else:
                        log.info(f"🔕 {result.upper()} sin entry ni precio live: {signal.get('pair','?')}")
                        continue

                if result == "tp":
                    # FIX 2026-04-16: Multi-TP — celebrar cada nivel y avanzar
                    _tp_levels_r = signal.get("_tp_levels", [])
                    _tp_idx_r = signal.get("_tp_idx", 0)
                    _tp_num_log = _tp_idx_r + 1
                    log.info(f"🎯 TP{_tp_num_log} alcanzado para {signal.get('pair','?')} entry={_entry}")

                    # FIX 2026-05-01: WICK VALIDATOR LLM (modo SHADOW) — pilar
                    # Sonnet evalua ticks ultimos 30s para detectar mecha falsa.
                    # En shadow solo loguea. LLM_WICK_VALIDATOR_MODE=active para actuar.
                    _wick_mode = os.getenv("LLM_WICK_VALIDATOR_MODE", "shadow").lower()
                    if _wick_mode in ("shadow", "active") and _tp_idx_r < len(_tp_levels_r):
                        try:
                            from llm_features import wick_validator as _wick_v
                            _hit_lvl = _tp_levels_r[_tp_idx_r]
                            # Recolectar ticks ultimos 30s del log de precios
                            _recent_ticks = [(time.time(), price)]  # mejor que nada
                            _wick_r = _wick_v(signal, "TP", _hit_lvl, _recent_ticks)
                            if _wick_r and _wick_r.get("is_wick"):
                                _wait = _wick_r.get("wait_seconds", 0)
                                _reason = _wick_r.get("reason", "")
                                if _wick_mode == "active" and _wait > 0:
                                    log.warning(
                                        f"⏸️ WICK detectado por LLM (active): {signal.get('pair')} "
                                        f"TP{_tp_num_log} — esperando {_wait}s antes de celebrar. "
                                        f"Razon: {_reason}"
                                    )
                                    try:
                                        from events_log import log_event as _log_event
                                        _log_event("signal.wick_delayed", source="copier",
                                                  data={"pair": signal.get("pair"),
                                                        "tp_num": _tp_num_log, "wait": _wait})
                                    except Exception:
                                        pass
                                    continue  # Re-checkea proximo ciclo monitor
                                elif _wick_mode == "shadow":
                                    log.info(
                                        f"🌑 SHADOW wick: TP{_tp_num_log} {signal.get('pair')} "
                                        f"podria ser mecha. Razon: {_reason} (NO se aplica)"
                                    )
                                    try:
                                        from events_log import log_event as _log_event
                                        _log_event("signal.wick_shadow", source="copier",
                                                  data={"pair": signal.get("pair"),
                                                        "tp_num": _tp_num_log, "reason": _reason})
                                    except Exception:
                                        pass
                        except Exception as _e_wv:
                            log.debug(f"Wick validator skipped: {_e_wv}")
                    # FIX 2026-04-17: Registrar TP alcanzado para que si luego toca SL
                    # el mensaje muestre "CIERRE — X TPs asegurados, neto +Y" en vez de "SL perdida"
                    _tp_precio = _tp_levels_r[_tp_idx_r] if _tp_idx_r < len(_tp_levels_r) else signal.get("tp", 0)
                    _pips_tp = abs(_tp_precio - _entry) if _entry > 0 and _tp_precio > 0 else 0

                    # FIX 2026-05-14: pre-verify TP igual que el path SL (linea 6782+).
                    # Caso 14-may 18:28: BTC ticket 768258449 ya cerrado en MT5 pero verify
                    # devolvio RETRY (age 5942s, history_deals_get vacio). _send_tp_celebration
                    # difirio el mensaje pero el flujo de afuera SIGUIO ejecutando
                    # _record_daily_result + avance a TP3 → daily tracker contabilizo
                    # +842.6 pts fantasma (la posicion ya no existia). Resultado: VIP +843 pts
                    # falsos en el contador del dia.
                    # Ahora: si verify devuelve RETRY → diferir todo (no celebrar, no
                    # registrar, no avanzar TP). El monitor reintenta proximo ciclo.
                    _pre_exists_tp, _pre_reason_tp = _verify_mt5_trade_exists(signal, context="tp_resolve_pre")
                    if (not _pre_exists_tp) and isinstance(_pre_reason_tp, str) and _pre_reason_tp.startswith("RETRY:"):
                        log.warning(
                            f"⏸️ TP DIFERIDO para {signal.get('pair','?')} TP{_tp_num_log} — {_pre_reason_tp}. "
                            f"NO se celebra, NO se registra, NO se avanza. Retry proximo ciclo monitor."
                        )
                        with _signals_lock:
                            _open_signals[sig_id] = sdata_resolved
                        _resolved_signals.discard(sig_id)
                        _save_open_signals()
                        _save_resolved_signals()
                        try:
                            from events_log import log_event as _log_event
                            _log_event("signal.tp_deferred", source="copier", data={
                                "pair": signal.get("pair"), "direction": signal.get("direction"),
                                "tp_num": _tp_num_log, "reason": _pre_reason_tp,
                            })
                        except Exception:
                            pass
                        continue

                    # FIX 2026-04-28b: proteger mutaciones de signal con _signals_lock.
                    # Antes signal["_tp_final"] y signal["_tps_alcanzados"] se mutaban
                    # sin lock — si dos TPs hit casi simultaneos (multi-canal espejo),
                    # race entre asignacion -> celebracion -> record -> avance, y
                    # _record_daily_result podia leer _tp_final ya avanzado al siguiente
                    # nivel. Ahora la SECCION CRITICA (mutacion + celebracion + record)
                    # corre dentro del lock atomico.
                    with _signals_lock:
                        _tps_hechos = signal.setdefault("_tps_alcanzados", [])
                        _tps_hechos.append({"nivel": _tp_num_log, "precio": _tp_precio, "pips": _pips_tp})
                        signal["_tp_final"] = _tp_precio
                    _send_tp_celebration(signal, reply_to_msg_id=_reply_id)
                    _record_daily_result(signal, result)
                    # Si hay más niveles TP, avanzar y seguir tracking
                    if (_tp_idx_r + 1) < len(_tp_levels_r):
                        with _signals_lock:
                            signal["_tp_idx"] = _tp_idx_r + 1
                            signal["_tp_final"] = _tp_levels_r[_tp_idx_r + 1]
                            _open_signals[sig_id] = sdata_resolved
                        _resolved_signals.discard(sig_id)
                        _save_open_signals()
                        log.info(f"📈 Avanzando a TP{_tp_num_log+1} ({_tp_levels_r[_tp_idx_r+1]}) para {signal.get('pair','?')}")
                else:
                    # FIX 2026-05-12: verify ANTES de notificar y registrar. Si verify
                    # devuelve RETRY (signal reciente + ticket valido + MT5 no confirma
                    # vivo/cerrado), es probable falso positivo del monitor (wick) o
                    # session MT5 transitoria. Re-insertar la signal y NO contar como
                    # SL — el monitor reintentara proximo ciclo cuando el broker
                    # confirme el cierre real.
                    # Caso ETH 765966520 (12-may): SL detectado a 21:35 por wick, broker
                    # no cerro, verify bloqueo el mensaje, pero el record corrio igual y
                    # dejo huerfana la signal — a 22:34 el SL real saltó sin aviso al VIP.
                    _pre_exists, _pre_reason = _verify_mt5_trade_exists(signal, context="sl_resolve_pre")
                    if (not _pre_exists) and isinstance(_pre_reason, str) and _pre_reason.startswith("RETRY:"):
                        log.warning(
                            f"⏸️ SL DIFERIDO para {signal.get('pair','?')} — {_pre_reason}. "
                            f"Re-insertando signal para reintento (NO se cuenta como SL)."
                        )
                        with _signals_lock:
                            _open_signals[sig_id] = sdata_resolved
                        _resolved_signals.discard(sig_id)
                        _save_open_signals()
                        _save_resolved_signals()
                        # Liberar dedup para permitir retry en ciclos siguientes
                        _recently_notified.pop(_notif_key, None)
                        _save_notif_dedup()
                        try:
                            from events_log import log_event as _log_event
                            _log_event("signal.sl_deferred", source="copier", data={
                                "pair": signal.get("pair"), "direction": signal.get("direction"),
                                "entry": _entry, "sl": signal.get("sl"),
                                "reason": _pre_reason,
                            })
                        except Exception:
                            pass
                        continue
                    log.info(f"🛑 SL notificado para {signal.get('pair','?')} entry={_entry}")
                    _send_sl_notification(signal, reply_to_msg_id=_reply_id)
                    # FIX 2026-05-01: events_log auditoria
                    try:
                        from events_log import log_event as _log_event
                        _log_event("signal.sl_hit", source="copier", data={
                            "pair": signal.get("pair"), "direction": signal.get("direction"),
                            "entry": _entry, "sl": signal.get("sl"),
                        })
                    except Exception:
                        pass
                    # FIX 2026-04-23: registrar SL en gift_tracker si era señal regalo
                    if _is_gifted_signal(signal.get("pair", "")):
                        try:
                            with _gift_lock:
                                _sl_pair = signal.get("pair", "").upper()
                                _is_gold_sl = _sl_pair in ("GOLD", "XAUUSD", "XAUUSD=X")
                                if _is_gold_sl:
                                    _gift_tracker["gold_result"] = "sl"
                                else:
                                    _gift_tracker["other_result"] = "sl"
                                _save_gift_tracker()
                        except Exception as _egsl:
                            log.debug(f"gift_tracker sl update error: {_egsl}")
                    _record_daily_result(signal, result)
                    # FIX 2026-04-29: POST-TRADE ANALYSIS — Sonnet analiza por que fallo
                    # y guarda la leccion en logs/posttrade_lessons.log para que el
                    # admin pueda revisar al final de la semana.
                    if os.getenv("LLM_POSTTRADE_LEARN", "true").lower() in ("true", "1", "yes"):
                        try:
                            from llm_features import posttrade_analysis
                            _sl_pips_post = abs(_entry - signal.get("sl", 0)) if _entry > 0 and signal.get("sl", 0) > 0 else 0
                            _duration_post = int(time.time() - signal.get("timestamp", time.time()))
                            _lesson = posttrade_analysis(signal, _sl_pips_post, _duration_post)
                            if _lesson:
                                _lessons_path = Path(__file__).parent / "logs" / "posttrade_lessons.log"
                                _lessons_path.parent.mkdir(parents=True, exist_ok=True)
                                _ts_post = time.strftime("%Y-%m-%d %H:%M:%S")
                                with open(_lessons_path, "a", encoding="utf-8") as _flog:
                                    _flog.write(
                                        f"\n=== {_ts_post} | {signal.get('pair','?')} {signal.get('direction','?')} "
                                        f"src={signal.get('source','?')} ===\n{_lesson}\n"
                                    )
                                log.info(f"🤖 Post-trade analysis guardado en posttrade_lessons.log")
                        except Exception as _e_post:
                            log.debug(f"Post-trade analysis skipped: {_e_post}")
            elif result in ("expired", "orphan", "drift", "eod"):
                # FIX 2026-04-21: Antes las señales expiradas/huérfanas se eliminaban
                # silenciosamente del dict y dejaban al canal sin notificación de cierre.
                # Ahora notificamos siempre con el neto al precio actual del mercado.
                # FIX 2026-05-08 (B): TAMBIÉN cerrar la posición MT5 al market — antes
                # solo se quitaba del tracker dejando el trade abierto en MT5 (zombie).
                # Esas zombies eran rescatadas tarde por el reconcile y solían pegar SL.
                # Ahora cortamos la pérdida en el momento de la expiración, no después.
                try:
                    _mt5_ticket_exp = signal.get("_mt5_ticket", 0) or 0
                    if _mt5_ticket_exp > 0 and result == "expired":
                        import price_feed as _mt5_exp
                        _pos_list_exp = _mt5_exp.positions_get(ticket=int(_mt5_ticket_exp))
                        if _pos_list_exp and len(_pos_list_exp) > 0:
                            _pe = _pos_list_exp[0]
                            _close_t_exp = _mt5_exp.ORDER_TYPE_SELL if _pe.type == 0 else _mt5_exp.ORDER_TYPE_BUY
                            _tick_exp = _mt5_exp.symbol_info_tick(_pe.symbol)
                            if _tick_exp:
                                _price_exp = _tick_exp.bid if _pe.type == 0 else _tick_exp.ask
                                _req_exp = {
                                    "action":    _mt5_exp.TRADE_ACTION_DEAL,
                                    "position":  _pe.ticket,
                                    "symbol":    _pe.symbol,
                                    "volume":    _pe.volume,
                                    "type":      _close_t_exp,
                                    "price":     _price_exp,
                                    "deviation": 30,
                                    "magic":     MAGIC_COPIER,
                                    "comment":   "ExpireClose72h",
                                    "type_time":    _mt5_exp.ORDER_TIME_GTC,
                                    "type_filling": _mt5_exp.ORDER_FILLING_IOC,
                                }
                                _res_exp = _mt5_exp.order_send(_req_exp)
                                if _res_exp and _res_exp.retcode == _mt5_exp.TRADE_RETCODE_DONE:
                                    log.warning(
                                        f"⏱ EXPIRE-CLOSE MT5: {_pe.symbol} ticket={_pe.ticket} "
                                        f"cerrada al market @ {_price_exp:.5f} (evita zombi)"
                                    )
                                else:
                                    _rc_exp = _res_exp.retcode if _res_exp else "None"
                                    log.warning(f"⏱ ExpireClose order_send falló retcode={_rc_exp} ticket={_pe.ticket}")
                except Exception as _e_ec:
                    log.warning(f"ExpireClose MT5 error (sigue notificando): {_e_ec}")

                log.info(f"⏱ {result.upper()} notificado para {signal.get('pair','?')}")
                _send_expired_notification(signal, reason=result, reply_to_msg_id=_reply_id)
          except Exception as _e_resolve:
            log.error(f"❌ Error procesando {result} para {sig_id}: {_e_resolve}")


# === PARSER LLM PRO (Sonnet 4.6 + Vision + Always-LLM opcional) ===
# FIX 2026-04-29: Parser hibrido PRO. Modos:
#   LLM_MODE=fallback (default): regex primero, LLM si regex falla
#   LLM_MODE=always: TODAS las senales pasan por LLM (mejor calidad, ~$5/mes)
#
# Modelo por defecto: Claude Sonnet 4.6 (mejor extraccion, mejor lenguaje
# natural, soporte Vision para imagenes). Se puede bajar a Haiku con
# LLM_MODEL=claude-haiku-4-5-20251001 si quieres ahorrar mas.
#
# Vision: si la senal viene con imagen adjunta (ej. screenshot de chart),
# Sonnet la lee directamente — pasamos el bytes de la imagen al LLM.
#
# Activacion:
#   ANTHROPIC_API_KEY=sk-ant-...   (de console.anthropic.com)
#   LLM_PARSER_ENABLED=true
#   LLM_MODEL=claude-sonnet-4-6     (default; o claude-haiku-4-5-20251001)
#   LLM_MODE=always                 (default fallback; "always" pasa todo por LLM)
#   LLM_VISION_ENABLED=true         (default; lee imagenes adjuntas)
#
# Coste estimado por modo:
#   - solo regex (LLM_PARSER_ENABLED off):   $0
#   - fallback + Sonnet 4.6:                 ~$0.50/mes (regex cubre la mayoria)
#   - always + Sonnet 4.6:                   ~$1.50/mes (todo via LLM)
#   - always + Sonnet 4.6 + vision activo:   ~$3-5/mes
_llm_parse_cache: dict = {}        # hash(text) -> (signal_dict|None, ts)
_llm_parse_stats = {
    "regex_hit": 0, "llm_hit": 0, "llm_fail": 0, "llm_skipped": 0,
    "vision_hit": 0, "always_mode": 0, "fallback_mode": 0,
}

# Prompt SYSTEM una sola vez (cacheable por Anthropic)
_LLM_PARSE_SYSTEM = """You are a strict trading-signal extractor.

INPUT: A raw text message that MAY be a trading signal from a Telegram channel.

OUTPUT: Return ONLY valid JSON, NO prose, NO markdown, NO code fences. Schema:
{
  "is_signal": true|false,
  "type": "new_signal"|"update"|null,
  "direction": "BUY"|"SELL"|null,
  "pair": "XAUUSD"|"EURUSD"|"GBPUSD"|"USDJPY"|"GBPJPY"|"US30"|"NAS100"|"GER40"|"BTCUSD"|"ETHUSD"|null,
  "order_type": "Market"|"Limit"|"Stop",
  "entry": number|null,
  "entry2": number|null,
  "sl": number|null,
  "tp1": number|null,
  "tp2": number|null,
  "tp3": number|null,
  "tp4": number|null,
  "tp5": number|null,
  "action": "tp_hit"|"sl_hit"|"close_half"|"close_partial"|"full_close"|"move_sl_to_entry"|null,
  "tp_level": 1|2|3|4|5|null,
  "pips_profit": number|null
}

RULES:
- "is_signal":false for promos, market commentary, greetings, news, results cards.
- For "BUY 4594-96" or "SELL 4703-05" use shorthand: 4594-96 means entry=4594, entry2=4596.
- For "BUY 4534 OR 4530" entry=4534, entry2=4530.
- "SL OPEN" or no SL => sl=null.
- Pair: normalize hashtags (#XAUUSD->XAUUSD), gold/oro->XAUUSD, us100/nasdaq->NAS100, us30/dow->US30.
- If "limit" or "stop" mentioned for entry, set order_type accordingly.
- "TP1: 1.2345" or "TP¹: 1.2345" -> tp1=1.2345.
- Updates: "TP HIT", "SMASHED", "+50 pips" => type="update", action+tp_level set.
- "Close half"/"Close 50%" => action="close_half". "Full close" => action="full_close".
- BREAKEVEN PRIORITY: if the message mentions "breakeven", "B/E", "BE", "move SL to entry", "set stop to entry", "set SL to entry", "protect the trade" -> action="move_sl_to_entry", EVEN IF the message also contains the word "close". The trader's intent is to PROTECT the open position (move SL up), NOT to terminate it. Example: "Let's CLOSE our trade now and set breakeven" -> action="move_sl_to_entry" (NOT full_close). Example: "Close partial and move to BE" -> action="close_half" (partial close PLUS BE — partial wins because it's the cash action; the BE move is implicit follow-up). When in doubt between full_close and move_sl_to_entry, choose move_sl_to_entry — it is the safer, reversible action.
- Numbers must be realistic. EURUSD ~1.05-1.20, GBPUSD ~1.20-1.40, USDJPY ~140-160, XAUUSD ~3000-5000, US30 ~30000-50000, NAS100 ~15000-30000, BTCUSD ~50000-100000, ETHUSD ~2000-5000.
- If text has NO direction or NO pair, is_signal=false.
- Maximum 1500 chars input.

EXAMPLES (representative formats from ally channels):

Example 1 — SureShotFX classic new signal:
INPUT: "GOLD BUY 4534.50\\nSL: 4525.00\\nTP1: 4540\\nTP2: 4548\\nTP3: 4560\\n--Trade by Julian"
OUTPUT: {"is_signal":true,"type":"new_signal","direction":"BUY","pair":"XAUUSD","order_type":"Market","entry":4534.50,"sl":4525.00,"tp1":4540,"tp2":4548,"tp3":4560}

Example 2 — ProSignalsFx limit order:
INPUT: "#CHFJPY\\nFREE SIGNAL|SHORT\\nEntry: 201.76\\nStop Loss: 202.18\\nTarget: 201.17"
OUTPUT: {"is_signal":true,"type":"new_signal","direction":"SELL","pair":"CHFJPY","order_type":"Limit","entry":201.76,"sl":202.18,"tp1":201.17}

Example 3 — AnabelSignals dual-entry shorthand:
INPUT: "BUY ORO 4690-4686\\nTP1 4693 TP2 4696 TP3 4700\\nSL 4680"
OUTPUT: {"is_signal":true,"type":"new_signal","direction":"BUY","pair":"XAUUSD","order_type":"Market","entry":4690,"entry2":4686,"sl":4680,"tp1":4693,"tp2":4696,"tp3":4700}

Example 4 — Update TP hit:
INPUT: "TP1 SMASHED on GOLD! +120 pips secured"
OUTPUT: {"is_signal":true,"type":"update","pair":"XAUUSD","action":"tp_hit","tp_level":1,"pips_profit":120}

Example 5 — Update move to breakeven (priority over "close"):
INPUT: "Close our trade and set SL to entry — protect the position"
OUTPUT: {"is_signal":true,"type":"update","action":"move_sl_to_entry"}

Example 6 — Partial close:
INPUT: "Close 50% on ORO, let the rest run"
OUTPUT: {"is_signal":true,"type":"update","pair":"XAUUSD","action":"close_half"}

Example 7 — Promotional/non-signal (must return false):
INPUT: "FREE COPY TRADING - Join our VIP channel for premium signals!"
OUTPUT: {"is_signal":false}

Example 8 — Market commentary (not a signal):
INPUT: "Good morning traders. Gold is at key levels today. Volatility expected."
OUTPUT: {"is_signal":false}

Example 9 — Results card (historical, not a signal):
INPUT: "GOLD SIGNALS RESULTS THIS WEEK\\n+450 pips total\\n8 wins / 2 losses"
OUTPUT: {"is_signal":false}

Example 10 — BTCUSD with 4-digit TPs:
INPUT: "BTC SELL 80205\\nSL 80395\\nTP1 79781 TP2 79577 TP3 79270"
OUTPUT: {"is_signal":true,"type":"new_signal","direction":"SELL","pair":"BTCUSD","order_type":"Market","entry":80205,"sl":80395,"tp1":79781,"tp2":79577,"tp3":79270}

EDGE CASES:
- If the message is in Spanish ("COMPRA"/"VENTA"), map to BUY/SELL.
- If TP/SL are present but entry is missing, set entry=null and is_signal=true (will be filled at market).
- If a "limit" entry has a current price reference like "wait for 4690 to BUY", that's order_type=Limit with entry=4690.
- For superscripts (TP¹ TP² TP³), already normalized externally — treat as TP1, TP2, TP3.
- Numbers with comma thousands separator ("80,205.5") must be parsed as 80205.5."""


def _parse_with_llm(text: str, chat_title: str = "", image_bytes: bytes = None) -> dict | None:
    """LLM parser PRO (Claude Sonnet 4.6 default, Vision si hay imagen adjunta).
    Returns signal dict en el mismo formato que _parse_signal_impl, o None.
    Cache 24h por hash del mensaje (texto + imagen) para no pagar 2 veces igual.
    """
    if os.getenv("LLM_PARSER_ENABLED", "false").lower() not in ("true", "1", "yes"):
        _llm_parse_stats["llm_skipped"] += 1
        return None
    _api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not _api_key:
        _llm_parse_stats["llm_skipped"] += 1
        return None

    _model = os.getenv("LLM_MODEL", "claude-sonnet-4-6").strip()
    _vision_on = os.getenv("LLM_VISION_ENABLED", "true").lower() in ("true", "1", "yes")
    _has_image = bool(image_bytes) and _vision_on

    # Cache lookup (24h por hash del texto + imagen normalizado)
    import hashlib
    _norm = " ".join(text.split())[:1500]
    _hash_input = _norm.encode("utf-8")
    if _has_image:
        _hash_input += b"|IMG|" + hashlib.md5(image_bytes).digest()
    _h = hashlib.md5(_hash_input).hexdigest()
    _cached = _llm_parse_cache.get(_h)
    if _cached:
        _val, _ts = _cached
        if time.time() - _ts < 86400:
            return _val if _val else None

    try:
        import anthropic
        # FIX 2026-05-20: más reintentos en 529 (sobrecarga Anthropic)
        _client = anthropic.Anthropic(api_key=_api_key, max_retries=5)

        # Construir content multimodal si hay imagen
        _user_content = []
        if _has_image:
            import base64
            # Detectar mime type por magic bytes
            _mime = "image/jpeg"
            if image_bytes[:8].startswith(b"\x89PNG"):
                _mime = "image/png"
            elif image_bytes[:6] in (b"GIF87a", b"GIF89a"):
                _mime = "image/gif"
            elif image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
                _mime = "image/webp"
            _b64 = base64.standard_b64encode(image_bytes).decode("ascii")
            _user_content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": _mime, "data": _b64},
            })
            _user_content.append({
                "type": "text",
                "text": (f"Channel: {chat_title}\n\nMessage (with attached image):\n"
                         f"{text[:1500]}\n\nIf the image contains the trading signal "
                         f"(prices, TPs, SL), extract from the image too.")
            })
        else:
            _user_content.append({
                "type": "text",
                "text": f"Channel: {chat_title}\n\nMessage:\n{text[:1500]}"
            })

        # FIX 2026-05-13: prompt caching de Anthropic — system >=1024 tokens se cachea
        # 5min con 90% descuento. Reduce coste del parser LLM (principal consumidor:
        # ~83% del coste diario) de \$3/MTok input a \$0.30/MTok en cache hits.
        # El system prompt fue expandido a ~1100 tokens con ejemplos para superar
        # el umbral minimo de Anthropic.
        _resp = _client.messages.create(
            model=_model,
            max_tokens=500,
            system=[{
                "type": "text",
                "text": _LLM_PARSE_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": _user_content}],
        )
        _raw = _resp.content[0].text.strip()
        # Strip code fences si el modelo los puso por error
        if _raw.startswith("```"):
            _raw = _raw.split("```")[1]
            if _raw.startswith("json"):
                _raw = _raw[4:]
            _raw = _raw.strip()
        import json as _json
        _data = _json.loads(_raw)

        if not _data.get("is_signal"):
            _llm_parse_cache[_h] = (None, time.time())
            return None

        _stype = _data.get("type") or "new_signal"
        _direction = _data.get("direction")
        _pair_raw = (_data.get("pair") or "").upper().replace("/", "")

        if _stype == "new_signal":
            if not _direction or not _pair_raw:
                _llm_parse_cache[_h] = (None, time.time())
                return None

            # Mapeo a mt5_symbol (mismo SYMBOL_MAP que el parser regex)
            _mt5_sym = SYMBOL_MAP.get(_pair_raw, _pair_raw)
            _alias = _pair_raw

            # Source detection (igual que regex) — solo canales activos en lista de Monitoreando
            _ct_low = chat_title.lower()
            _source = "Unknown"
            if "sureshot" in _ct_low: _source = "SureShotFX"
            elif "gold forex" in _ct_low: _source = "GoldForexMarket"
            elif "toptradingsignals" in _ct_low or "top trading" in _ct_low: _source = "TopTradingSignals"
            elif "united kings" in _ct_low or "unitedkings" in _ct_low: _source = "UnitedKings"
            elif "prosignalsfx" in _ct_low: _source = "ProSignalsFx"
            elif "anabel" in _ct_low: _source = "AnabelSignals"
            else:
                log.warning(f"⚠️ Fuente desconocida — chat_title='{chat_title}' → marcado como Unknown")

            _order_type = _data.get("order_type") or "Market"
            _result = {
                "type":       "new_signal",
                "pair":       _alias,
                "mt5_symbol": _mt5_sym,
                "direction":  _direction.upper(),
                "order_type": _order_type,
                "is_limit":   _order_type == "Limit",
                "entry":      float(_data.get("entry") or 0),
                "entry2":     float(_data.get("entry2") or 0),
                "sl":         float(_data.get("sl") or 0),
                "tp":         float(_data.get("tp1") or 0),
                "tp2":        float(_data.get("tp2") or 0),
                "tp3":        float(_data.get("tp3") or 0),
                "tp4":        float(_data.get("tp4") or 0),
                "tp5":        float(_data.get("tp5") or 0),
                "rrr":        "",
                "style":      "",
                "source":     _source,
                "raw":        text[:300],
                "timestamp":  time.time(),
                "_parsed_by": "llm",
            }
            # FIX 2026-05-06: detectar scalp/swing desde el texto crudo
            # El LLM no extrae "style" — lo detectamos nosotros del raw text.
            _upper_text = text.upper()
            if "SCALP" in _upper_text:
                _result["style"] = "Scalp"
            elif "SWING" in _upper_text:
                _result["style"] = "Swing"
            elif "INTRADAY" in _upper_text:
                _result["style"] = "Intraday"
            _llm_parse_cache[_h] = (_result, time.time())
            _llm_parse_stats["llm_hit"] += 1
            if _has_image:
                _llm_parse_stats["vision_hit"] += 1
            _vision_tag = " [VISION]" if _has_image else ""
            log.info(f"🤖 LLM ({_model}) parsed{_vision_tag}: {_direction} {_alias} entry={_result['entry']} sl={_result['sl']} src={_source}")
            return _result

        elif _stype == "update":
            _action = _data.get("action") or "tp_hit"
            # FIX 2026-05-19: fallback regex para extraer el pair si el LLM no lo extrajo.
            # Caso real Sureshot 19-may 06:17 "AUDCAD FULL TP HIT 150+ PIPS (3RR)":
            # LLM extrajo action=tp_hit pero pair="" -> handle_update_mt5 fallaba con
            # "No mt5_symbol ni pair en el update". Ahora intentamos extraerlo del texto.
            if not _pair_raw:
                import re as _re_upd_pair
                _m_upd = _re_upd_pair.search(
                    r'\b(XAUUSD|XAUEUR|GOLD|ORO|BTCUSD|ETHUSD|US100CASH|US100|US30CASH|'
                    r'US30|NAS100|NASDAQ|NDX|GER40|DAX|SPX500|US500|JP225|UKOIL|USOIL|'
                    r'BRENT|WTI|[A-Z]{3}[A-Z]{3})\b',
                    text.upper(),
                )
                if _m_upd:
                    _pair_raw = _m_upd.group(1)
                    log.info(f"🔧 LLM update fallback regex: pair extraido del texto = {_pair_raw}")
            _mt5_sym_upd = SYMBOL_MAP.get(_pair_raw, _pair_raw) if _pair_raw else ""
            _result = {
                "type":       "update",
                "pair":       _pair_raw,
                "mt5_symbol": _mt5_sym_upd,
                "action":     _action,
                "tp_level":   _data.get("tp_level") or 0,
                "pips_profit": float(_data.get("pips_profit") or 0),
                "raw":        text[:300],
                "timestamp":  time.time(),
                "_parsed_by": "llm",
            }
            _llm_parse_cache[_h] = (_result, time.time())
            _llm_parse_stats["llm_hit"] += 1
            if _has_image:
                _llm_parse_stats["vision_hit"] += 1
            log.info(f"🤖 LLM ({_model}) parsed update: {_action} {_pair_raw}")
            return _result

        _llm_parse_cache[_h] = (None, time.time())
        return None

    except ImportError:
        log.warning("⚠️ LLM parser: paquete 'anthropic' no instalado (pip install anthropic)")
        _llm_parse_stats["llm_fail"] += 1
        return None
    except Exception as _e:
        log.warning(f"⚠️ LLM parser fallo: {_e}")
        _llm_parse_stats["llm_fail"] += 1
        return None


# FIX 2026-04-30 + REFORZADO 2026-05-01: pre-filtro narrative-only.
# Caso real 1/5/2026 que motivó el refuerzo: TopTradingSignals publicó
#   "📉NZD-USD Local Short! ⭕Sell! #NZDUSD taps into a supply area after
#    internal liquidity is taken, showing rejection and weak bullish
#    follow-through. Distribution likely underway, with bearish continuation
#    targeting inefficiency below. Time Frame 5H. Sell!🔽"
# Sin Entry/SL/TP. El bot publicó al canal VIP con precios INVENTADOS
# (Entry 0.59019, TP 0.5885, SL 0.5930) que NUNCA aparecieron en el original.
# Los keywords antiguos no detectaban "supply area", "follow-through", etc.
_NARRATIVE_ONLY_KEYWORDS = [
    # Bias / direccion sin niveles
    "STRONG BULLISH BIAS", "STRONG BEARISH BIAS",
    "BULLISH BIAS", "BEARISH BIAS",
    "PULLBACK AHEAD", "REVERSAL AHEAD",
    # Liquidity concepts
    "BUY-SIDE LIQUIDITY", "SELL-SIDE LIQUIDITY",
    "BUY SIDE LIQUIDITY", "SELL SIDE LIQUIDITY",
    "INTERNAL LIQUIDITY", "EXTERNAL LIQUIDITY",
    "LIQUIDITY GRAB", "LIQUIDITY SWEEP",
    "SWEEPING BUY-SIDE", "SWEEPING SELL-SIDE",
    # Displacement / strength
    "BEARISH DISPLACEMENT", "BULLISH DISPLACEMENT",
    "AGGRESSIVE BEARISH", "AGGRESSIVE BULLISH",
    "BEARISH CONTINUATION", "BULLISH CONTINUATION",
    "WEAK BULLISH", "WEAK BEARISH",
    "FOLLOW-THROUGH", "FOLLOW THROUGH",
    # Zones
    "DEMAND CLUSTER", "SUPPLY CLUSTER",
    "DEMAND ZONE", "SUPPLY ZONE",
    "DEMAND AREA", "SUPPLY AREA",
    "PREMIUM ZONE", "DISCOUNT ZONE",
    "HORIZONTAL SUPPLY", "HORIZONTAL DEMAND",
    # Structure / patterns
    "BREAKING STRUCTURE", "BREAK OF STRUCTURE",
    "CHANGE OF CHARACTER", "CHANGE IN CHARACTER",
    "RISING TRENDLINE", "FALLING TRENDLINE",
    "BULLISH TRENDLINE", "BEARISH TRENDLINE",
    # Accumulation / distribution
    "CONTINUED ACCUMULATION", "CONTINUED DISTRIBUTION",
    "DISTRIBUTION LIKELY", "ACCUMULATION LIKELY",
    "DISTRIBUTION UNDERWAY", "ACCUMULATION UNDERWAY",
    # Targeting inefficiency / imbalance
    "TARGETING INEFFICIENCY", "TARGETING IMBALANCE",
    "INEFFICIENCY BELOW", "INEFFICIENCY ABOVE",
    "IMBALANCE BELOW", "IMBALANCE ABOVE",
    "REJECTION FROM",
    # Time-frame language without operational levels
    "TIME FRAME 5H", "TIME FRAME 4H", "TIME FRAME 1H",
    "TIME FRAME 30M", "TIME FRAME 15M", "TIME FRAME 1D",
    "TIMEFRAME 5H", "TIMEFRAME 4H", "TIMEFRAME 1H",
    # Local short/long sin precios (TopTradingSignals classic)
    "LOCAL SHORT", "LOCAL LONG",
]


def _is_narrative_only(text: str) -> bool:
    """True si el mensaje parece analisis (narrative-only, sin precios operativos).

    REFORZADO 2026-05-01: doble defensa.

    1. Si contiene un keyword narrative Y no tiene Entry/SL/TP explícitos → narrative
    2. NUEVO: si tiene direccion (BUY/SELL/SHORT/LONG) Y NO tiene >=2 precios
       numericos relevantes en el texto → narrative (anti-invencion del LLM)
    """
    if not text:
        return False
    upper = text.upper()

    # Tiene Entry/SL/TP explicitos con numero? Si si, NO es narrative.
    if re.search(r'(ENTRY|SL|STOP\s*LOSS|TP|TAKE\s*PROFIT|TARGET)\s*[:\-=\s]*\d', upper):
        return False

    has_narrative_kw = any(kw in upper for kw in _NARRATIVE_ONLY_KEYWORDS)
    if has_narrative_kw:
        return True

    # Refuerzo anti-invencion: si menciona BUY/SELL/SHORT/LONG con un par
    # pero el texto no contiene >=2 precios numericos, descartar.
    # Casos detectados: "📉NZD-USD Local Short! ⭕Sell!" (sin precios).
    has_direction = bool(re.search(r'\b(SELL|BUY|SHORT|LONG)\b', upper))
    if has_direction:
        # Buscar precios decimales (0.5901, 92.27, 1.0850) o enteros grandes (4500, 27500)
        prices = re.findall(r'\d+\.\d{2,5}|\b\d{4,6}\b', text)
        # Filtrar años 2020-2030 (falsos positivos en "Time Frame 2026" o fechas)
        prices = [p for p in prices if not (len(p) == 4 and "." not in p and 2020 <= int(p) <= 2030)]
        if len(prices) < 2:
            return True

    return False


# FIX 2026-05-13: pre-filtros SAFE para descartar mensajes obvios antes del LLM.
# Cubre patrones 100% confirmados como NO-senal: greetings, results cards puros,
# mensajes sin numeros, mensajes muy cortos. Conservador — solo descarta cuando
# la confianza es total. No reemplaza al parser regex que sigue para confirmacion.
_OBVIOUS_NOT_SIGNAL_KEYWORDS = [
    # Greetings (sin contenido operativo)
    "GOOD MORNING TRADERS", "GOOD MORNING TEAM", "GOOD AFTERNOON TRADERS",
    "BUENOS DIAS TRADERS", "BUENOS DIAS EQUIPO", "BUENAS NOCHES",
    "HEY TRADERS", "HELLO VIP", "HELLO EVERYONE", "HOLA MIEMBROS",
    "WELCOME TO", "BIENVENIDO", "BIENVENIDOS",
    # Results cards puros
    "SIGNALS RESULTS", "WEEKLY RESULTS", "MONTHLY RESULTS",
    "TOTAL PIPS WON", "TOTAL PIPS LOST", "NET PIPS GAINED",
    # Promo / branding sin niveles
    "VIP CHANNEL OPEN", "FREE COPY TRADING", "FREE CHANNEL",
    "JOIN OUR VIP", "JOIN VIP", "JOIN NOW", "DM ME",
    "LIKE & SUBSCRIBE", "FOLLOW US ON", "SUBSCRIBE TO",
    # Bot/system noise
    "AUTOMATIZACION", "SSF COPIER", "SSF TRADE COPIER", "SURESHOTFX.COM",
    "INVALID PARAMETERS", "INVALID ORDER", "MARKET IS TOO VOLATILE",
    "ALGOBOT", "REVOLUTIONARY TRADING",
    # Education / motivation puros
    "PATIENCE PAYS", "STAY DISCIPLINED", "TRADE SMART", "STAY SHARP",
    # FED / news commentary (no es senal)
    "FED SPEAKERS TODAY", "GEOPOLITICAL TENSIONS",
]


def _is_obviously_not_signal(text: str) -> bool:
    """True si el mensaje claramente NO es senal — descarta antes del LLM.
    Conservador: solo devuelve True con patrones 100% confirmados como ruido.
    Ahorra coste API en mensajes que SIEMPRE serian descartados luego."""
    if not text or len(text.strip()) < 10:
        return True
    upper = text.upper()
    # Pattern 1: contiene keywords de spam puro Y no tiene precios operativos.
    has_spam_kw = any(kw in upper for kw in _OBVIOUS_NOT_SIGNAL_KEYWORDS)
    if has_spam_kw:
        # Doble check: si tiene Entry/SL/TP con numero adyacente, podria ser
        # senal real con header promo. NO descartar en ese caso.
        if not re.search(r'(ENTRY|SL|STOP\s*LOSS|TP|TAKE\s*PROFIT|TARGET)\s*[:\-=\s]*\d', upper):
            return True
    # Pattern 2: cero numeros AND sin keywords de update/accion → no es ni senal ni update.
    # Las senales requieren precios. Los updates pueden ser sin numeros pero llevan
    # keywords claros (SL TO ENTRY, MOVE TO BE, CLOSE HALF, FULL CLOSE, etc.).
    if not re.search(r'\d', text):
        _UPDATE_KW_NUMLESS = (
            "SL TO ENTRY", "SL TO BE", "STOP TO ENTRY", "STOP TO BE",
            "MOVE TO BE", "MOVE TO BREAKEVEN", "SET BE", "BREAKEVEN",
            "B/E", "B.E.", "SL AT BE", "PROTECT",
            "CLOSE HALF", "CLOSE 50", "CLOSE FULL", "FULL CLOSE", "CIERRE",
            "TP HIT", "SL HIT", "SMASHED", "BANKED",
            "MOVE SL", "MOVER SL",
        )
        if not any(kw in upper for kw in _UPDATE_KW_NUMLESS):
            return True
    return False


# === PARSER ===
def parse_signal(text, chat_title="", image_bytes: bytes = None):
    """Parse trading signal from text (+ optional image). Returns dict or None.
    Soporta formatos de los canales activos: SureShotFX, AnabelSignals,
    GOLD FOREX MARKET, TopTradingSignals, UnitedKings, ProSignalsFx.
    FIX 2026-04-29 PRO:
      - LLM_MODE=fallback (default): regex primero, LLM si regex no es confiable.
      - LLM_MODE=always: TODO pasa por LLM (Sonnet 4.6); regex solo de respaldo.
      - Si image_bytes != None y LLM_VISION_ENABLED=true, Sonnet lee la imagen.
    FIX 2026-04-30: pre-filtro narrative-only ANTES del LLM (ahorro coste).
    FIX 2026-05-13: pre-filtro obvious-not-signal — descarta basura clara antes
    de pagar el LLM. Reduce calls en ~20-30% por mensajes promo/greeting/no-data.
    """
    _mode = os.getenv("LLM_MODE", "fallback").lower()
    _llm_enabled = os.getenv("LLM_PARSER_ENABLED", "false").lower() in ("true", "1", "yes")

    # FIX 2026-05-13: pre-filtro obvio ANTES de cualquier procesamiento.
    # Descarta saludos, results cards, promo pura, mensajes sin numeros — todos
    # son 100% NO-senal. Ahorra coste LLM en ~20-30% del trafico.
    if _is_obviously_not_signal(text):
        log.info(f"⏭️ Obvious-not-signal descartado en [{chat_title}]: {text[:80].replace(chr(10),' ')!r}")
        return None

    # FIX 2026-04-30: pre-filtro ANTES del LLM. Si es narrative-only, no llamar.
    # Aplica a ambos modos (always y fallback) y ahorra coste API en posts
    # de TopTradingSignals/ProSignalsFx que solo publican analisis sin niveles.
    if _is_narrative_only(text):
        log.info(f"⏭️ Narrative-only descartado en [{chat_title}]: {text[:80].replace(chr(10),' ')!r} — sin Entry/SL/TP, no se llama LLM")
        return None

    # ── Modo ALWAYS: LLM directo, sin pasar por regex primero ──
    if _mode == "always" and _llm_enabled:
        _llm_parse_stats["always_mode"] += 1
        _llm_result = _parse_with_llm(text, chat_title, image_bytes=image_bytes)
        if _llm_result:
            # FIX 2026-05-01: anti-invencion Vision — validar que los precios del LLM
            # esten razonablemente representados en el texto original.
            if not _validate_llm_prices_against_text(_llm_result, text, chat_title):
                return None
            return _llm_result
        # Si LLM no extrae senal (promo/incompleta o error real), regex como respaldo.
        # FIX 2026-04-30: log mas claro — antes decia "LLM fallo" pero en 95% de casos
        # es porque el mensaje no es senal (promo, comentario, etc), no un error real.
        log.info("ℹ️ LLM no extrajo senal (probable promo/incompleto), fallback a regex")
        try:
            return _parse_signal_impl(text, chat_title)
        except Exception as _e_p:
            log.exception(f"parse_signal crash (always backup) en [{chat_title}]: {_e_p}")
            return None

    # ── Modo FALLBACK (default): regex primero ──
    _llm_parse_stats["fallback_mode"] += 1
    try:
        _result = _parse_signal_impl(text, chat_title)
    except Exception as _e_parse:
        log.exception(f"parse_signal crash en [{chat_title}]: {_e_parse} | text={text[:120].replace(chr(10),' ')!r}")
        _result = None

    # Confidence check del resultado regex
    _regex_confident = False
    if _result:
        if _result.get("type") == "update":
            _regex_confident = True  # updates son menos exigentes
        elif _result.get("type") == "new_signal":
            _has_dir_pair = bool(_result.get("direction")) and bool(_result.get("pair"))
            # FIX 2026-05-06 (#9): endurecer fallback para evitar señales fantasma.
            # Antes: aceptaba si cualquiera de entry/sl/tp > 0 → publicaba señales
            # con entry=0 sl=0 tp=4708 (parser falló parcialmente).
            # Ahora: requiere entry > 0 (necesario para ejecutar/mostrar) Y
            # al menos uno de sl/tp (gestión de riesgo). Esto NO rechaza señales
            # válidas con sólo entry+sl o entry+tp (común en algunos canales).
            _has_entry = (_result.get("entry") or 0) > 0
            _has_risk = ((_result.get("sl") or 0) > 0) or ((_result.get("tp") or 0) > 0)
            _regex_confident = _has_dir_pair and _has_entry and _has_risk
            if _result and not _regex_confident and _has_dir_pair:
                # Loguear qué faltó para diagnóstico
                _missing = []
                if not _has_entry: _missing.append("entry")
                if not _has_risk: _missing.append("sl/tp")
                log.info(f"ℹ️ Regex parse incompleto en [{chat_title}]: faltan {_missing} — pasando a LLM o descartando")

    if _regex_confident:
        _llm_parse_stats["regex_hit"] += 1
        return _result

    # Regex no fue suficiente → intentar LLM fallback (con imagen si aplica)
    _llm_result = _parse_with_llm(text, chat_title, image_bytes=image_bytes)
    if _llm_result:
        # FIX 2026-05-01: anti-invencion Vision — mismo check
        if not _validate_llm_prices_against_text(_llm_result, text, chat_title):
            return None
        return _llm_result

    return _result  # devolver lo que sea (regex parcial o None)


def _validate_llm_prices_against_text(result: dict, text: str, chat_title: str = "") -> bool:
    """FIX 2026-05-01: anti-invencion del LLM Vision.

    Caso real 1/5/2026 que motivo este check: TopTradingSignals publico mensaje
    sin precios ("Sell NZDUSD" + analisis narrativo + chart). El LLM Vision
    INVENTO entry=0.59019 sl=0.593 leyendo zonas del chart. Se publico al VIP
    con precios que NUNCA aparecieron en el mensaje original.

    Validacion: para senales nuevas con precios, al menos UNO de los precios
    devueltos por el LLM debe aparecer en el texto original (con tolerancia 0.5%).
    Si NINGUNO aparece, probablemente fueron inventados → rechazar.

    Excepciones:
      - Si result.type != "new_signal" → no aplica
      - Si entry=sl=tp=0 → no hay nada que validar (probablemente promo/narrative)
      - Si solo hay TP precios y match en TPs alternativos (tp2/tp3/etc.)
    """
    if not result or result.get("type") != "new_signal":
        return True

    entry = float(result.get("entry", 0) or 0)
    sl = float(result.get("sl", 0) or 0)
    tp = float(result.get("tp", 0) or 0)
    tp2 = float(result.get("tp2", 0) or 0)
    tp3 = float(result.get("tp3", 0) or 0)
    tp4 = float(result.get("tp4", 0) or 0)
    tp5 = float(result.get("tp5", 0) or 0)

    llm_prices = [p for p in (entry, sl, tp, tp2, tp3, tp4, tp5) if p > 0]
    if not llm_prices:
        return True  # nada que validar — se filtra despues por sl/tp gate

    # Extraer todos los numeros del texto (decimales y enteros)
    # FIX 2026-05-08: soportar comas como separador de miles (4,714 → 4714).
    # Antes el regex partía "4,714" en "4" y "714" → falso positivo de invención
    # con canales como TopTradingSignals que usan formato americano. Se descartaban
    # señales legítimas de oro a $4,714 porque el número 4714 no aparecía "puro".
    nums_in_text = set()
    for m in re.finditer(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?', text):
        try:
            v = float(m.group(0).replace(",", ""))
            if v > 0:
                nums_in_text.add(v)
        except ValueError:
            pass

    if not nums_in_text:
        # Texto sin numeros pero LLM devolvio precios → 100% invencion
        log.warning(
            f"🛑 ANTI-INVENCION: LLM devolvio precios {llm_prices} pero el texto NO tiene numeros. "
            f"Probable invencion via Vision. Descartando senal de [{chat_title}]: {text[:80].replace(chr(10),' ')!r}"
        )
        return False

    # Verificar match con tolerancia 0.5% (capta variaciones de formato/precision)
    matched = []
    for lp in llm_prices:
        for tn in nums_in_text:
            if abs(lp - tn) / max(lp, 0.0001) <= 0.005:
                matched.append((lp, tn))
                break

    if not matched:
        log.warning(
            f"🛑 ANTI-INVENCION: ningun precio del LLM {llm_prices} matchea con numeros del texto "
            f"{sorted(nums_in_text)[:10]}. Probable invencion via Vision. "
            f"Descartando senal de [{chat_title}]: {text[:120].replace(chr(10),' ')!r}"
        )
        return False

    # Al menos un precio matchea → confianza minima razonable
    if len(matched) < len(llm_prices):
        log.info(
            f"🟡 LLM matchea {len(matched)}/{len(llm_prices)} precios con texto "
            f"(otros pueden venir de imagen valida). Aceptando."
        )
    return True


def get_llm_parse_stats() -> dict:
    """Acceso publico a las metricas del parser. Usado por bot.py /llm_stats."""
    return dict(_llm_parse_stats)


def reset_llm_parse_stats() -> None:
    """Resetea contadores. Util para empezar un periodo limpio."""
    for _k in _llm_parse_stats:
        _llm_parse_stats[_k] = 0


def _parse_signal_impl(text, chat_title=""):
    """Implementación real de parse_signal (envuelta con try/except defensivo)."""
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
    # FIX 2026-04-22: Quitado "HIT OUR RISK" (debería ser sl_hit, no ignorar)
    # FIX 2026-04-22: Quitado "SMASHED" — ahora "TP SMASHED" se procesa como tp_hit
    # (mantenemos otros celebration keywords para descartar ruido puro)
    _ignore_keywords = [
        "SSF COPIER", "SSF TRADE COPIER", "AUTOMATIZACION", "CUPON", "SURESHOTFX.COM",
        "INVALID PARAMETERS", "INVALID ORDER", "MARKET IS TOO VOLATILE",
        "GOLD ANALYSIS", "LET'S WAIT", "HOLA MIEMBROS", "HELLO VIP",
        "SL UPDATED",
        "ALGOBOT", "REVOLUTIONARY TRADING", "DAILY PICKS", "MATCHBETS",
        "BTC DOMINANCE", "ALTCOIN", "ETHEREUM", "BITCOIN",
        "WEEKLY OUTLOOK", "MARKET OUTLOOK", "RESEARCH DESK",
        "STAY SHARP", "SIGNALS WILL FOLLOW", "HEY TRADERS",
        "FAILED TO TRIGGER", "GETTING DELETED", "BECOME INVALID IF NOT TRIGGERED",
        "FED SPEAKERS", "GEOPOLITICAL", "ECONOMIC DATA",
        # GOLD FOREX MARKET — celebraciones genéricas (no TP events)
        # FIX 2026-04-22: Quitado "BLAZING PROFIT" porque mensajes tipo
        # "TP5 SMASHED! 170+ PIPS BLAZING PROFIT" se descartaban — el TP manda.
        "BOOM BOOM", "POWER TRADE DONE",
        "PATIENCE PAYS", "TRADE SMART", "STAY DISCIPLINED",
        "MARKET OPENING ALERT", "VOLATILITY EXPECTED",
        # NasdaqMasters / NASDaqxNinja — mensajes de celebración (NO son señales)
        "IT FLEW", "PIPS HIT", "TP CORRECTED", "1000 PIPS", "2000 PIPS", "3000 PIPS",
        "BANKED", "NAILED IT", "MASSIVE WIN", "LET IT RUN",
        # FIX 2026-04-25: TopTradingSignals manda CARDS de resultados pasados como
        # "GOLD SIGNALS RESULTS" — son ranking historico, NO señales nuevas.
        "SIGNALS RESULTS", "GOLD SIGNALS RESULT", "PIPS WON", "PIPS LOST",
        "NET GAIN", "TOTAL PIPS WON", "TOTAL PIPS LOST", "NET PIPS GAINED",
        "VIP CHANNEL", "GOLD VIP", "FREE COPY TRADING",
        "LIKE & SUBSCRIBE", "BROADCAST",
        # FIX 2026-04-25: ProSignalsFx manda posts narrativos promocionales como
        # "Absolutely fascinating short on #GBPUSD" + #update + #bestforexsignals.
        # NO son señales — son posts de marketing del propio canal.
        # Mantenemos aquí solo los términos PURO promo (nunca aparecen en señales reales).
        "FASCINATING SHORT", "FASCINATING LONG", "ABSOLUTELY FASCINATING",
        "FASCINATING", "AMAZING SETUP", "BEAUTIFUL SETUP", "PERFECT SETUP",
    ]
    if any(w in upper for w in _ignore_keywords):
        return None

    # FIX 2026-04-25 (regresión NZDJPY ProSignalsFx): los términos genéricos
    # como "FREE SIGNAL", "#FREESIGNAL", "BEST FOREX SIGNALS" aparecen también
    # en señales legítimas como header/marca (ej. "FREE SIGNAL|SHORT🔥" seguido
    # de Entry/SL/TP). Filtrar como promo SOLO si NO hay precios en el mensaje.
    _promo_only_keywords = [
        "#UPDATE", "#BESTFOREXSIGNALS", "#FREECHANNEL", "#FREESIGNAL",
        "BEST FOREX SIGNALS", "FREE CHANNEL THIS MONTH", "FREE SIGNAL",
    ]
    if any(w in upper for w in _promo_only_keywords):
        # Si NO encontramos Entry/SL/TP con número adyacente → es promo pura.
        if not re.search(r'(ENTRY|SL|STOP\s*LOSS|TP|TAKE\s*PROFIT)\s*[:\s]*\d', upper):
            # FIX 2026-04-29: log explicito para que /perdidas detecte estos descartes
            log.info(f"⏭️ Senal descartada (promo only — sin Entry/SL/TP): {text[:80].replace(chr(10),' ')!r}")
            return None

    # ── MENSAJES DE ACTUALIZACIÓN ──
    # FIX 2026-04-22: Ampliado para capturar:
    #  - "TP SMASHED" de GOLD FOREX MARKET (TP intermedio o final)
    #  - "HIT OUR RISK" de SureShot (equivale a SL)
    #  - "CLOSE our trade", "READY TO CLOSE", "CLOSE some positions" de United Kings
    #  - "Full close" corto de SureShot (el pair se deduce del canal)
    _update_keywords = [
        "CLOSE HALF", "CLOSE PARTIAL", "FULL CLOSE", "MOVE SL", "PIPS PROFIT",
        "STOP LOSS HIT", "SL HIT", "TP HIT", "HIT OUR RISK", "PIPS IN PROFIT",
        "CIERRA LA MITAD", "CIERRE PARCIAL", "MOVER SL", "CERRAR COMPLETAMENTE",
        "RUNNING WITH", "CURRENTLY RUNNING",
        "SMASHED",  # TP SMASHED de GOLD FOREX MARKET
        "CLOSE OUR TRADE", "CLOSE OUR POSITIONS", "READY TO CLOSE",
        "CLOSE SOME POSITIONS", "CLOSE THE TRADE",
    ]
    if any(w in upper for w in _update_keywords):
        return _parse_update(text, upper_noslash, chat_title)

    # ── DETECTAR FUENTE ── solo canales activos en lista de Monitoreando
    source = "Unknown"
    chat_lower = chat_title.lower()
    text_lower = text.lower()
    if "sureshot" in chat_lower or "ssf" in text_lower:
        source = "SureShotFX"
    elif "gold forex" in chat_lower:
        source = "GoldForexMarket"
    elif "toptradingsignals" in chat_lower or "top trading" in chat_lower:
        source = "TopTradingSignals"
    elif "united kings" in chat_lower or "unitedkings" in chat_lower:
        source = "UnitedKings"
    elif "prosignalsfx" in chat_lower or "prosignals fx" in chat_lower:
        source = "ProSignalsFx"
    elif "anabel" in chat_lower:
        source = "AnabelSignals"
    else:
        log.warning(f"⚠️ Fuente desconocida — chat_title='{chat_title}' → marcado como Unknown")

    # ── DETECTAR DIRECCIÓN ──
    direction = None
    order_type = "Market"   # Market | Limit | Stop
    is_limit = False

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
                       "LIMITE DE VENTA", "VENTA LIMITE",
                       "VENDER", "VENDER ORO"]  # FIX 2026-04-29: GOLD FOREX MARKET usa "Vender"
        # Verificar VENTA antes que COMPRA para evitar falsos positivos
        if any(w in upper_noslash for w in _sell_words):
            direction = "SELL"
        elif any(w in upper_noslash for w in _buy_words):
            direction = "BUY"
    if not direction:
        return None

    # Si es LIMIT también desde otras menciones
    # FIX 2026-05-06: incluir LÍMIT (con acento) — AnabelSignals usa español "Comprar límite"
    if "LIMIT" in upper or "LÍMIT" in upper:
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
    # FIX 2026-04-24: eliminar comas de miles ("4,680" → "4680"). Learn 2 Trade
    # VIP usa formato "Entry: $4,680 – $4,705" que dejaba tp=4 como artefacto.
    # Caso real 24/04 18:03: XAU/USD BUY + WTI CRUDE BUY perdidas por este bug.
    # Regex especifico: solo comas ENTRE digitos con 3 digitos despues (miles).
    # NO toca comas con < 3 digitos despues (ej "1,5" decimal europeo).
    upper_clean = re.sub(r'(\d),(\d{3})(?!\d)', r'\1\2', upper_clean)

    # ── EXTRAER SL ──
    # Formatos: "SL: 4499.60" | "SL 4499" | "❗️ SL 45370" | "Stop Loss → 1.3801" | "SL4415" (sin espacio)
    # FIX: \d{1,6} en vez de \d{3,6} — forex como EURUSD/GBPAUD tienen precio 1.XXXXX (1 solo dígito entero)
    # FIX 2026-04-16: incluir @ como separador válido — NasdaqMasters usa "SL @47950"
    # FIX 2026-04-24: ampliar separadores a `_`, `-`, `=`, `|`, `—` — aliados usan
    # variantes raras ("SL_ 4686" AnabelSignals 24/04 06:50 que crasheaba antes).
    sl_match = re.search(r'(?:SL|STOP\s*LOSS)\s*[:\s→@_\-=\|—]*(\d{1,6}\.?\d+)', upper_clean)
    # FIX 2026-04-27: Learn 2 Trade VIP SWING usa "Stop: $100.00" (STOP solo,
    # sin LOSS). Requerimos ":" o "→" obligatorio para NO capturar "SELL STOP"
    # / "BUY STOP" (tipo de orden). Lookbehind evita prefijos como "AUTOSTOP".
    if not sl_match:
        sl_match = re.search(r'(?<![A-Z])STOP\s*[:→]\s*(\d{1,6}\.?\d+)', upper_clean)
    # FIX 2026-04-24: detectar "SL OPEN/NONE/N/A/SIN/ABIERTO/-" — el aliado dice
    # explicitamente "sin SL" (AnabelSignals "SL OPEN", otros canales "NO SL").
    # En ese caso sl=0 es INTENCIONAL, no un parser fail. Sirve para no capturar
    # un TP por error al ver "SL" sin numero. Tambien evita que el parser intente
    # buscar un numero perdido y agarre el primer TP por accidente.
    _sl_explicit_none = bool(re.search(
        r'(?:SL|STOP\s*LOSS)\s*[:\s→@]*(?:OPEN|NONE|N/?A|ABIERTO|SIN|NO\s*SL|-+)\b',
        upper_clean
    )) or bool(re.search(r'\bNO\s+SL\b|\bSIN\s+SL\b', upper_clean))
    if _sl_explicit_none and not sl_match:
        # SL declarado como "abierto/none" — dejamos sl=0 explicitamente
        sl_match = None  # ya es None, pero dejamos claro que es intencional

    # ── EXTRAER TP1, TP2, TP3 ──
    # Formatos: "TP1: 4513" | "TP: 4513" | "Tp 4540" | "🥇 TP 45530" | "Toma de Ganancias 1 : 4513"
    # | "Take profit 4480" | "Take profit : 4480" (FxPremiere format)
    # TP2: "TP2: 4520" | "TP 2: 4520" | "TAKE PROFIT 2: 4520" | "Toma de Ganancias 2: 4520"
    # TP3: "TP3: 4545" | "TP 3: 4545" | "TAKE PROFIT 3: 4545" | "Toma de Ganancias 3: 4545"
    # Ignora líneas con "TP: abierto" / "TP: ABIERTO" / "TP: OPEN" (sin número fijo)
    # FIX 2026-04-07: También filtrar "OPEN" en inglés
    _upper_clean_no_abierto = re.sub(r'TP\s*[:\s]*(?:ABIERTO|OPEN)\b', '', upper_clean)
    # FIX 2026-04-16: incluir @ como separador válido en TP — NasdaqMasters usa "TP @48300"
    tp_match = re.search(
        r'(?:TOMA\s*DE\s*GANANCIAS\s*1\s*[:\s]+|TAKE\s*PROFIT\s*1\s*[:\s]+|TP\s*1\s*[:\s@]+|TP\s*[:\s@]+|TP\s+|TAKE\s*PROFIT\s*[:\s@]+)(\d+\.?\d*)',
        _upper_clean_no_abierto
    )
    # Fallback: "Tp 4540" o "TP 1.9150" — \d{1,6} cubre forex (1.XXXX) y gold (4XXX)
    if not tp_match:
        tp_match = re.search(r'\bTP\s*@?\s*(\d{1,6}\.?\d+)', _upper_clean_no_abierto)
    # Fallback AnabelSignals: "TP4430" o "TP1.9150" (sin espacio entre TP y número)
    if not tp_match:
        tp_match = re.search(r'\bTP(\d{1,6}\.?\d+)', _upper_clean_no_abierto)
    # FIX 2026-04-27: Learn 2 Trade VIP SWING usa "Target: $80.00" (sin "TP" ni
    # "Take Profit"). Lookbehind evita prefijos tipo "PRICETARGET".
    if not tp_match:
        tp_match = re.search(r'(?<![A-Z])TARGET\s*[:\s]\s*(\d{1,6}\.?\d+)', _upper_clean_no_abierto)
    # ── TP2-TP5: extraer por número explícito ──
    def _extract_tp_n(n, txt):
        """Extract TPn from text using multiple patterns.
        FIX 2026-04-28: nuevo patron 3a sin separador donde TP{n}{value} podria
        confundirse con TP+resto-del-precio. Caso AnabelSignals: 'TP4625' significa
        TP: 4625 (valor pegado), no TP4=625. Se intenta primero el pattern con
        separador antes del fallback ambiguo.
        """
        # Patrón 1: "TP 2: 4626" | "TP2: 4626" | "TAKE PROFIT 2: 4626"
        # FIX 2026-04-16: incluir @ como separador — NasdaqMasters usa "TP2 @48500"
        m = re.search(
            rf'(?:TOMA\s*DE\s*GANANCIAS\s*{n}\s*[:\s]+|TAKE\s*PROFIT\s*{n}\s*[:\s]+|TP\s*{n}\s*[:\s@]+|TP{n}\s*[:\s@]*)(\d+\.?\d*)',
            txt
        )
        if m: return m
        # Patrón 2: "TP 2 4626" (espacio sin :)
        # FIX 2026-05-06: añadir (?!\d) para evitar que TP\s*4 capture "TP 4666"
        # como tp4=666 — el 4 de "4666" era interpretado como el número de TP.
        m = re.search(rf'\bTP\s*{n}(?!\d)\s*@?\s*(\d{{1,6}}\.?\d+)', txt)
        if m: return m
        # Patrón 3: "TP2 4626" pegado (AMBIGUO — puede ser TP+2: o TP+24626)
        # FIX 2026-04-28: si captura un valor, marcar como ambiguo para validacion
        # posterior. Si el valor capturado esta out-of-range vs entry, salvage_logic
        # mas abajo prefijara el digito {n} para reinterpretar como valor pegado.
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
        # FIX 2026-05-07: añadir XAU/USD (con barra) — AnabelSignals escribe el par con slash
        # "💎 XAU/USD BUY 4740/4738" → sin este alias entry=0 porque upper_clean conserva "/"
        entry_match = re.search(r'(?:ORO|XAUUSD|XAU/USD|GOLD)\s+(?:COMPRA|VENTA|VENDER?|BUY|SELL)\s{0,5}(\d{3,6}\.?\d*)', upper_clean)
    # AnabelSignals: "Límite de venta de oro 4442" | "Venta de oro 4467" → número tras dirección+activo
    # FIX 2026-05-06: también captura "COMPRAR LÍMITE XAUUSD\n4530" — COMPRAR(verb) + LIMITE + par
    if not entry_match:
        entry_match = re.search(r'(?:VENTA[R]?|COMPRA[R]?|LIMITE|LÍMITE)\s+(?:DE\s+)?(?:ORO|XAUUSD|XAU/USD|GOLD)\s{0,5}(\d{3,6}\.?\d*)', upper_clean)
    # FIX 2026-05-06: AnabelSignals "COMPRAR LÍMITE XAUUSD / 4530" — precio puede venir
    # en línea propia tras el par, con o sin separador "/". Captura número ≥4 dígitos
    # inmediatamente después del par cuando está en la siguiente línea.
    if not entry_match:
        entry_match = re.search(r'(?:ORO|XAUUSD|XAU/USD|GOLD)\s*[/\s]\s*(\d{4,6}\.?\d*)', upper_clean)
    # FIX 2026-04-29: GOLD FOREX MARKET "XAUUSD VENDER 4594-96" — rango: tomar primer número
    # El guión "-96" es el final del rango (4594-4596), regex captura solo 4594
    if not entry_match:
        entry_match = re.search(r'VENDER?\s+(\d{3,6}\.?\d*)', upper_clean)
    # Formato inline: "GBP/CAD H1 Buy 1.8412" → número después de BUY/SELL seguido de otro token
    if not entry_match:
        entry_match = re.search(r'(?:BUY|SELL|COMPRA|VENTA)\s+[\w/]+\s+(?:H\d+\s+)?(\d+\.?\d*)', upper_clean)
    # FIX 2026-04-24: AnabelSignals "Buy limit us30\n\n49100\n\nTp 49250..."
    # Formato: direccion + LIMIT/STOP + pair + (newlines o spaces) + precio.
    # Ocurrido 24/04 15:28: parser no capturaba 49100, usaba precio actual
    # 49453 → TPs invertidos → MT5 No response. Ahora captura correctamente.
    if not entry_match:
        entry_match = re.search(
            r'(?:BUY|SELL|COMPRA|VENTA)\s+(?:LIMIT|STOP)\s+[\w/]+\s+(\d+\.?\d*)',
            upper_clean
        )
    # SureShotFX inline: "GBPAUD SELL  1.93236" — precio con decimal JUSTO tras BUY/SELL (sin keyword)
    # Requiere decimal para evitar falsos positivos con números enteros del texto
    if not entry_match:
        entry_match = re.search(r'(?:BUY|SELL|COMPRA|VENTA)\s{1,10}(\d+\.\d+)', upper_clean)
    # FxPremiere: "Gold buy now!!@4487 - 4482" — precio precedido de @
    # También cubre formatos como "@4509/4504", "ENTRY @1.3741"
    if not entry_match:
        entry_match = re.search(r'@\s*(\d{1,6}\.?\d*)', upper_clean)

    # FIX 2026-04-22: SL ya NO es obligatorio para publicar — se publica todo.
    # MT5 no ejecutará sin SL, pero la señal llega al canal igualmente.
    # (El usuario quiere que se publiquen TODAS las señales sin excepción)

    try:
        # FIX 2026-04-24: SL puede ser None si el aliado dijo "SL OPEN" o no hay
        # SL en el mensaje. Antes esto crasheaba con 'NoneType.group' y la senal
        # se descartaba completa (ej. AnabelSignals "GOLD SELL 4667 OR 4670 SL OPEN..."
        # del 24/04 06:44 que se perdio). Ahora sl=0 = "sin SL" → se publica igual,
        # MT5 no ejecuta sin SL pero el canal recibe la senal.
        sl    = float(sl_match.group(1)) if sl_match else 0.0
        tp    = float(tp_match.group(1)) if tp_match else 0.0
        tp2   = float(tp2_match.group(1)) if tp2_match else 0.0
        tp3   = float(tp3_match.group(1)) if tp3_match else 0.0
        tp4   = float(tp4_match.group(1)) if tp4_match else 0.0
        tp5   = float(tp5_match.group(1)) if tp5_match else 0.0
        entry = float(entry_match.group(1)) if entry_match else 0.0
    except (ValueError, IndexError, AttributeError):
        return None

    # FIX 2026-04-29: capturar SEGUNDA entry de referencia. Formatos vistos:
    # AnabelSignals     "BUY 4545 4541" | "BUY 4534 OR 4530"
    # GOLD FOREX MARKET "Sell 4594-96"  → 4594/4596 (shorthand 2 ultimos digitos)
    # FxPremiere        "@4509/4504" | "@4487 - 4482"
    # Validacion: entry2 a < 1% del entry principal (sino es ruido).
    entry2 = 0.0
    if entry > 0 and entry_match:
        _e_pos = entry_match.end()
        _after = upper_clean[_e_pos:_e_pos + 30]  # ventana corta tras el primer entry

        # Patron 1: SHORTHAND "-YY" o "-Y" → reemplazar ultimos digitos del entry.
        # Ej: entry=4594 + "-96" → entry2=4596 | entry=4703 + "-05" → 4705.
        # Solo aplica para precios enteros >= 100 (indices/oro/cripto), NO forex.
        _shorthand = re.match(r'^-(\d{1,3})\b', _after)
        if _shorthand and entry >= 100:
            try:
                _last_digits = _shorthand.group(1)
                _entry_int_str = str(int(entry))
                if len(_entry_int_str) > len(_last_digits):
                    _e2_candidate = float(_entry_int_str[:-len(_last_digits)] + _last_digits)
                    _e2_pct = abs(_e2_candidate - entry) / entry
                    if 0 < _e2_pct < 0.01:  # 1% tolerance shorthand
                        entry2 = _e2_candidate
            except (ValueError, AttributeError):
                pass

        # Patron 2: 2do precio COMPLETO con separador (OR / - espacio)
        if entry2 == 0:
            _e2_match = re.search(r'^\s*(?:OR\s+|[/\-]\s*)(\d{1,6}\.?\d*)', _after)
            if _e2_match:
                try:
                    _e2_val = float(_e2_match.group(1))
                    if entry > 0 and _e2_val > 0:
                        _e2_pct = abs(_e2_val - entry) / entry
                        if 0 < _e2_pct < 0.01:
                            entry2 = _e2_val
                except (ValueError, AttributeError):
                    pass

        # Patron 3: separados por solo espacio (AnabelSignals "BUY 4545 4541")
        if entry2 == 0:
            _e2_match = re.match(r'^\s+(\d{3,6}(?:\.\d+)?)\b', _after)
            if _e2_match:
                try:
                    _e2_val = float(_e2_match.group(1))
                    if entry > 0 and _e2_val > 0:
                        _e2_pct = abs(_e2_val - entry) / entry
                        if 0 < _e2_pct < 0.005:  # mas estricto: 0.5%
                            entry2 = _e2_val
                except (ValueError, AttributeError):
                    pass

    # FIX 2026-04-25: REGLA CRITICA — una senal REAL tiene al menos UNO de
    # estos: SL valido, TP valido, o entry valido. Si NINGUNO de los tres se
    # capturo, es un post promocional/publicidad (NO publicar al canal VIP).
    # Casos reales que se colaron antes de este filtro:
    # - "Absolutely fascinating short on #GBPUSD" (ProSignalsFx promo)
    # - "GOLD SIGNALS RESULTS" (TopTradingSignals card de stats)
    # Ambos tenian dir + pair pero CERO precios → falsos positivos.
    if entry <= 0 and sl <= 0 and tp <= 0 and tp2 <= 0 and tp3 <= 0 and tp4 <= 0 and tp5 <= 0:
        # FIX 2026-04-29: agregar emoji ⏭️ para que /perdidas lo detecte
        log.warning(
            f"⏭️ Senal descartada (sin precios) en [{chat_title}]: "
            f"{text[:100].replace(chr(10),' ')!r} — probable promo/publicidad"
        )
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

    # FIX 2026-04-22: Sin filtros — se publica aunque SL=0 (no ejecuta en MT5, pero sí en canal)

    # ── Validación lógica: TP y SL deben estar en la dirección correcta ──
    # FIX 2026-04-22: Formato zona doble "BUY 4796 4692" (AnabelSignals) → parser toma 4796 pero
    # la entry real es 4692. Cuando la validación falla, buscar un segundo candidato en el texto.
    def _try_fix_inverted_entry(cur_entry, direction, tp_val, sl_val, upper_text):
        """Busca un número alternativo en el texto que haga válida la señal."""
        # Busca todos los precios candidatos en el rango de XAUUSD/índices (>100)
        _candidates = re.findall(r'\b(\d{3,6}\.?\d*)\b', upper_text)
        _cands = []
        for c in _candidates:
            try:
                v = float(c)
                if v != cur_entry and v > 100:
                    _cands.append(v)
            except ValueError:
                pass
        # Para BUY: necesitamos entry < tp y entry > sl → buscar candidato menor que tp y mayor que sl
        for _alt in _cands:
            _tp_ok = (tp_val == 0) or (direction == "BUY" and _alt < tp_val) or (direction == "SELL" and _alt > tp_val)
            _sl_ok = (sl_val == 0) or (direction == "BUY" and _alt > sl_val) or (direction == "SELL" and _alt < sl_val)
            if _tp_ok and _sl_ok and abs(_alt - cur_entry) / max(cur_entry, 1) < 0.20:
                return _alt
        return None

    # FIX 2026-04-22: Sin filtros de dirección — si entry parece invertida, intentar corrección.
    # Si no se puede corregir, se publica con entry=0 (ejecuta a mercado). NUNCA descartar.
    if entry > 0 and tp > 0:
        if direction == "BUY" and tp < entry and abs(tp - entry) > 0.001:
            _alt = _try_fix_inverted_entry(entry, direction, tp, sl, upper_clean)
            if _alt is not None:
                log.info(f"⚠️ Parser: BUY TP({tp})<entry({entry}) → zona doble, usando {_alt}")
                entry = _alt
            else:
                log.warning(f"⚠️ Parser: BUY TP({tp})<entry({entry}) — publicando con entry=0 (a mercado)")
                entry = 0.0
        if direction == "SELL" and tp > entry and abs(tp - entry) > 0.001:
            _alt = _try_fix_inverted_entry(entry, direction, tp, sl, upper_clean)
            if _alt is not None:
                log.info(f"⚠️ Parser: SELL TP({tp})>entry({entry}) → zona doble, usando {_alt}")
                entry = _alt
            else:
                log.warning(f"⚠️ Parser: SELL TP({tp})>entry({entry}) — publicando con entry=0 (a mercado)")
                entry = 0.0
    if entry > 0 and sl > 0:
        if direction == "BUY" and sl > entry and abs(sl - entry) > 0.001:
            _alt = _try_fix_inverted_entry(entry, direction, tp, sl, upper_clean)
            if _alt is not None:
                log.info(f"⚠️ Parser: BUY SL({sl})>entry({entry}) → usando {_alt}")
                entry = _alt
            else:
                log.warning(f"⚠️ Parser: BUY SL({sl})>entry({entry}) — publicando con entry=0 (a mercado)")
                entry = 0.0
        if direction == "SELL" and sl < entry and abs(sl - entry) > 0.001:
            _alt = _try_fix_inverted_entry(entry, direction, tp, sl, upper_clean)
            if _alt is not None:
                log.info(f"⚠️ Parser: SELL SL({sl})<entry({entry}) → usando {_alt}")
                entry = _alt
            else:
                log.warning(f"⚠️ Parser: SELL SL({sl})<entry({entry}) — publicando con entry=0 (a mercado)")
                entry = 0.0

    # FIX 2026-04-07: Validar TP2-TP5 dirección y rango razonable
    # Si un TP está en la dirección contraria o es absurdamente lejano, descartarlo (no la señal entera)
    # FIX 2026-04-28: salvage logic — si pattern 3 capturo formato "TP{n}{value}"
    # ambiguo (ej. AnabelSignals "TP4625" = TP:4625, no TP4=625), reinterpretar
    # prefijando el digito {n} antes del valor capturado out-of-range.
    if entry > 0:
        _entry_int_str = str(int(entry))  # ej. "4630" para entry=4630.0
        _entry_first_digit = _entry_int_str[0] if _entry_int_str else ""
        for _tpn_idx, (_tpn, _tpv) in enumerate([("tp2", tp2), ("tp3", tp3), ("tp4", tp4), ("tp5", tp5)], start=2):
            if _tpv <= 0:
                continue
            _wrong_dir = False
            if direction == "BUY" and _tpv < entry and abs(_tpv - entry) > 0.001:
                _wrong_dir = True
            elif direction == "SELL" and _tpv > entry and abs(_tpv - entry) > 0.001:
                _wrong_dir = True
            _pct_diff = abs(_tpv - entry) / entry if entry > 0 else 0
            _out_of_range = _pct_diff > 0.20
            if _wrong_dir or _out_of_range:
                # FIX 2026-04-28: SALVAGE — si el pattern 3 ambiguo capturo TP{n}+digitos,
                # intentar reinterpretar prefijando el primer digito del entry para
                # reconstruir el valor pegado. Ej: TP4=625, entry=4630 -> probar 4625.
                _tpv_salvaged = None
                _tpv_int_str = str(int(_tpv))
                if _entry_first_digit and len(_tpv_int_str) < len(_entry_int_str):
                    _candidate = float(_entry_first_digit + _tpv_int_str)
                    _cand_pct = abs(_candidate - entry) / entry if entry > 0 else 1
                    _cand_dir_ok = (
                        (direction == "BUY" and _candidate > entry) or
                        (direction == "SELL" and _candidate < entry)
                    )
                    # FIX 2026-04-29: NO rellenar si el valor candidato coincide con
                    # otro TP ya capturado (caso TP4 == TP1 — bug visto 29/04 chart).
                    # Prevenir el lookup en TP1=tp y TP2-TP5 ya extraidos.
                    _otros_tps = [v for k, v in [("tp", tp), ("tp2", tp2), ("tp3", tp3),
                                                  ("tp4", tp4), ("tp5", tp5)]
                                  if k != _tpn and v > 0]
                    _coincide_existente = any(
                        abs(_candidate - _v) < 0.001 for _v in _otros_tps
                    )
                    if _cand_dir_ok and _cand_pct < 0.20 and not _coincide_existente:
                        _tpv_salvaged = _candidate
                    elif _coincide_existente:
                        log.info(f"🔧 Parser SALVAGE {_tpn}: candidato {_candidate} coincide con otro TP — descartado (evita TP duplicado)")
                if _tpv_salvaged is not None:
                    log.info(f"🔧 Parser SALVAGE {_tpn}: {_tpv} -> {_tpv_salvaged} (formato pegado AnabelSignals)")
                    if _tpn == "tp2": tp2 = _tpv_salvaged
                    if _tpn == "tp3": tp3 = _tpv_salvaged
                    if _tpn == "tp4": tp4 = _tpv_salvaged
                    if _tpn == "tp5": tp5 = _tpv_salvaged
                else:
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

    # FIX 2026-04-28: AUTO-DETECT order_type por drift respecto a precio actual.
    # Si el aliado NO especifico Limit/Stop y el entry esta lejos del mercado actual,
    # convertir a pending order (Limit/Stop) en vez de ejecutar Market a un precio
    # peor. Asi el canal VIP recibe "WAIT FOR ENTRY at X" en lugar de ejecutar slippage.
    if order_type == "Market" and entry > 0:
        try:
            _live_price = _get_current_price(alias) or 0
            if _live_price > 0:
                _drift_abs = abs(entry - _live_price)
                _drift_pct = _drift_abs / _live_price if _live_price > 0 else 0
                # Threshold por activo: forex 0.05%, gold/index 0.1%, crypto 0.5%
                _p_up = alias.upper()
                if alias in ("GOLD", "XAUUSD", "XAUUSD=X"):
                    _threshold = 0.001  # 0.1% = ~5 pts en ORO @4500
                elif any(x in _p_up for x in ("BTC","ETH","BITCOIN")):
                    _threshold = 0.005  # 0.5% en cripto (mas volatil)
                elif _live_price >= 100:  # indices, JPY pairs
                    _threshold = 0.001
                else:  # forex
                    _threshold = 0.0005  # 0.05% = ~5 pips en EURUSD
                if _drift_pct > _threshold:
                    # Decidir Limit vs Stop segun direccion + posicion
                    if direction == "BUY":
                        if entry < _live_price:
                            order_type = "Limit"   # BUY LIMIT — esperar bajada
                            is_limit = True
                        else:
                            order_type = "Stop"    # BUY STOP — esperar ruptura alcista
                    else:  # SELL
                        if entry > _live_price:
                            order_type = "Limit"   # SELL LIMIT — esperar subida
                            is_limit = True
                        else:
                            order_type = "Stop"    # SELL STOP — esperar ruptura bajista
                    log.info(f"🎯 Auto-detect order: {direction} {alias} entry={entry} vs market={_live_price} (drift {_drift_pct:.2%}) -> {order_type}")
        except Exception as _e_auto:
            log.debug(f"Auto-detect order_type fallo (se queda Market): {_e_auto}")

    # FIX 2026-04-29: filtro SL minimo por clase de activo. Visto en canal el
    # 29/04: SL de 1.8 pts en NAS100 (ruido absoluto, ni cubre el spread). El
    # parser no rechaza la senhal — siguiendo zero_bloqueos limpiamos sl=0 para
    # que MT5 no ejecute (necesita SL) y se publique con el aviso de "not set
    # by source" que ya esta en send_to_channel. Asi el suscriptor sabe que
    # tiene que gestionar manual o ignorar la senhal.
    if entry > 0 and sl > 0:
        _sl_dist_raw = abs(entry - sl)
        _alias_up = (alias or "").upper()
        # Distancia minima razonable para que el SL cubra al menos el spread
        # tipico + algo de ruido. Por debajo de esto el SL no es real.
        if _alias_up in ("XAUUSD", "GOLD", "ORO"):
            _sl_min_required = 5.0          # 5 pts en oro = $0.50
            _sl_unit = "pts"
            _sl_dist_pts = _sl_dist_raw
        elif any(x in _alias_up for x in ("NAS100", "US100", "US30", "US500", "GER40", "GER30", "UK100", "JPN225", "FRA40", "SPX500")):
            _sl_min_required = 10.0         # 10 pts en indices
            _sl_unit = "pts"
            _sl_dist_pts = _sl_dist_raw
        elif any(x in _alias_up for x in ("BRENT", "USOIL", "UKOIL", "WTI", "NATGAS", "XNGUSD")):
            _sl_min_required = 5.0          # 5 pts en oil/gas (precio en docenas)
            _sl_unit = "pts"
            _sl_dist_pts = _sl_dist_raw
        elif any(x in _alias_up for x in ("BTC", "BITCOIN")):
            _sl_min_required = 50.0         # 50 pts en BTC (~$50)
            _sl_unit = "pts"
            _sl_dist_pts = _sl_dist_raw
        elif any(x in _alias_up for x in ("ETH", "ETHEREUM")):
            _sl_min_required = 5.0          # 5 pts en ETH
            _sl_unit = "pts"
            _sl_dist_pts = _sl_dist_raw
        elif "JPY" in _alias_up:
            _sl_min_required = 5.0          # 5 pips JPY = 0.05 precio
            _sl_unit = "pips"
            _sl_dist_pts = _sl_dist_raw * 100
        else:
            # Forex estandar (EURUSD, GBPUSD, etc.) — 5 pips = 0.0005
            _sl_min_required = 5.0
            _sl_unit = "pips"
            _sl_dist_pts = _sl_dist_raw * 10000
        if _sl_dist_pts < _sl_min_required:
            log.warning(
                f"⚠️ Parser: SL muy pequeno {alias} {direction} entry={entry} "
                f"sl={sl} dist={_sl_dist_pts:.1f}{_sl_unit} "
                f"< min={_sl_min_required}{_sl_unit} — limpiando sl=0 (MT5 no ejecutara)"
            )
            sl = 0.0

    return {
        "type":       "new_signal",
        "pair":       alias,
        "mt5_symbol": mt5_symbol,
        "direction":  direction,
        "order_type": order_type,
        "is_limit":   is_limit,
        "entry":      entry,
        "entry2":     entry2,  # FIX 2026-04-29: 2da entry de referencia (AnabelSignals)
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


# FIX 2026-04-22: Default pair por título de canal — cuando el mensaje de update
# es muy corto (ej. "Full close", "Hit our risk") y no menciona el par, deducimos
# del nombre del canal. Solo canales DEDICADOS a un solo activo.
_CHANNEL_DEFAULT_PAIR = [
    ("sureshot gold", "XAUUSD", "GOLD"),
    ("sure shot gold", "XAUUSD", "GOLD"),
    ("goldsignals",    "XAUUSD", "GOLD"),
    ("gold forex",     "XAUUSD", "GOLD"),
    ("anabelsignals",  "XAUUSD", "GOLD"),
    ("xauusd",         "XAUUSD", "GOLD"),
    # SureShot INDICES mezcla US30/NAS100/GER40 → NO default, requiere que mencione el par
]


def _deducir_par_del_canal(chat_title):
    """Si el mensaje no tiene par, intenta deducirlo del canal (solo canales dedicados)."""
    if not chat_title:
        return None
    low = chat_title.lower()
    for needle, alias, mt5 in _CHANNEL_DEFAULT_PAIR:
        if needle in low:
            return (alias, mt5)
    return None


# FIX 2026-04-22: Para canales multi-par (United Kings, SureShot INDICES),
# cuando llega un "CLOSE our trade" sin par, usar el ÚLTIMO par abierto por
# ese canal en _open_signals (la señal más reciente que seguimos rastreando).
def _deducir_par_por_senal_activa(chat_title):
    """Busca la señal abierta más reciente del canal — útil para updates sin par."""
    if not chat_title:
        return None
    chat_low = chat_title.lower()
    try:
        with _signals_lock:
            signals_snapshot = list(_open_signals.items())
    except Exception:
        return None
    best = None
    best_ts = 0
    for _sid, _sdata in signals_snapshot:
        sig = _sdata.get("signal", {})
        src_low = str(sig.get("source", "")).lower()
        src_chat = str(sig.get("chat_title", "")).lower()
        # Match por source o por chat_title guardado
        if src_chat and src_chat == chat_low:
            pass
        elif src_low and (src_low in chat_low or chat_low in src_low):
            pass
        else:
            continue
        ts = _sdata.get("sent_at", 0)
        if ts > best_ts:
            best_ts = ts
            pair_alias = sig.get("pair", "")
            mt5_sym = SYMBOL_MAP.get(pair_alias, pair_alias)
            if pair_alias:
                best = (pair_alias, mt5_sym)
    return best


def _parse_update(text, upper, chat_title=""):
    """Parse signal updates (close half, move SL, etc.) — English + Spanish.

    FIX 2026-04-17: Política "correr hasta último TP":
    - TP1/TP2/TP3/TP4 HIT intermedios → action="tp_partial" (NO cierra MT5, solo celebra nivel)
    - FULL TP HIT / TP HIT sin número / último TP → action="tp_hit" (cierra MT5)
    - CLOSE ALL / EXIT NOW / CIERRE TOTAL / CERRAR TODO → action="full_close"

    FIX 2026-04-22: Ampliado para cubrir:
    - "TP SMASHED" / "TPn SMASHED" de GOLD FOREX MARKET (antes ignorado por filtro "SMASHED")
    - "HIT OUR RISK" de SureShot → sl_hit
    - "CLOSE our trade", "READY TO CLOSE", "CLOSE some positions" de United Kings → full_close
    - Mensajes cortos ("Full close", "Hit our risk") sin par → deducir del canal
    """
    action = None
    tp_level = 0  # para tp_partial

    # FIX 2026-04-22 (prioridad alta): "TP SMASHED" es TP hit de GOLD FOREX MARKET.
    # Formato típico: "🏆 XAUUSD TP1 SMASHED! 🏆" o "XAUUSD TP5 SMASHED! 170+ PIPS"
    _smashed = re.search(r"TP\s*([12345])?\s*SMASHED", upper)
    if _smashed:
        _lvl = _smashed.group(1)
        if _lvl:
            tp_level = int(_lvl)
            # Intermedio → tp_partial; último (5) o sin número → tp_hit
            action = "tp_partial" if tp_level < 5 else "tp_hit"
        else:
            action = "tp_hit"
    # FIX 2026-04-22: "HIT OUR RISK" = SL hit (SureShot lo usa así)
    elif "HIT OUR RISK" in upper:
        action = "sl_hit"
    # FIX 2026-05-12 (noche): BREAKEVEN PRIORIDAD ABSOLUTA — debe chequearse
    # ANTES que cualquier CLOSE. Razon: mensajes como "Let's CLOSE our trade
    # now and set breakeven" (United Kings 20:09) son ambiguos. Antes el regex
    # CLOSE...TRADE los clasificaba como full_close por error y habria cerrado
    # la posicion en vez de subir SL a BE.
    # Tambien cubre: "CLOSE PARTIAL AND MOVE TO BE" → preferimos move_sl_to_entry
    # (el partial es follow-up; el SL move es la accion segura sin perdida).
    elif any(w in upper for w in [
        "BREAKEVEN", "BREAK EVEN", "BREAK-EVEN",
        "MOVE SL TO ENTRY", "MOVE STOP TO ENTRY", "MOVE SL TO BE",
        "SET BREAKEVEN", "SET BE", "SET STOP TO ENTRY", "SET SL TO ENTRY",
        "SL TO BE", "SL AT BE", "SL TO ENTRY", "STOP AT ENTRY", "B/E", "B.E.",
        "MOVER SL A LA ENTRADA", "MOVER EL SL A LA ENTRADA",
        "MOVIMOS EL SL A LA ENTRADA", "PUNTO DE EQUILIBRIO",
    ]):
        action = "move_sl_to_entry"
    # FIX 2026-04-17: Cierre explícito ampliado (EN + ES)
    # FIX 2026-04-22: Añadidas variantes flexibles para United Kings
    # Debe detectarse ANTES que close_half/partial para no confundir
    elif any(w in upper for w in [
        "CLOSE ALL", "CLOSE FULL", "FULL CLOSE", "EXIT NOW", "EXIT ALL",
        "CLOSE POSITION", "CLOSE TRADE", "CLOSE ORDER",
        "CLOSE OUR TRADE", "CLOSE OUR POSITIONS", "CLOSE THE TRADE",
        "CLOSE SOME POSITIONS", "READY TO CLOSE",
        "CERRAR COMPLETAMENTE", "CIERRE TOTAL", "CERRAR TODO",
        "CERRAR POSICION", "CERRAR POSICIÓN", "CIERRE MANUAL",
        "CIERRA COMPLETA", "CIERRE COMPLETO",
    ]) or re.search(r"\bCLOSE\b[^.]{0,20}\b(TRADE|POSITIONS?)\b", upper):
        action = "full_close"
    # Close half (EN + ES)
    elif any(w in upper for w in ["CLOSE HALF", "CIERRA LA MITAD", "CIERRE DE LA MITAD", "CIERRE MEDIO"]):
        action = "close_half"
    # Close partial (EN + ES)
    elif any(w in upper for w in ["CLOSE PARTIAL", "CIERRE PARCIAL", "CIERRA PARCIAL"]):
        action = "close_partial"
    # SL/TP hit
    elif "STOP LOSS HIT" in upper or "SL HIT" in upper:
        action = "sl_hit"
    elif "TP HIT" in upper or "TAKE PROFIT HIT" in upper or re.search(r"\bTP\s*[12345]\b", upper):
        # Detectar número de TP: TP1/TP2/TP3/TP4/TP5
        _m = re.search(r"\bTP\s*([12345])\b", upper)
        if _m:
            tp_level = int(_m.group(1))
            # TP1/2/3/4 → intermedio (no cierra). Solo TP5 o "TP HIT" sin número o "FULL TP" cierra.
            if tp_level < 5 and "FULL" not in upper:
                action = "tp_partial"
            else:
                action = "tp_hit"
        else:
            action = "tp_hit"
    # FIX 2026-04-14: "RUNNING...FULL CLOSE" = cierre real, no descartar
    elif "EN CURSO CON" in upper or "RUNNING" in upper:
        if "FULL CLOSE" in upper or "CERRAR" in upper:
            action = "full_close"
        else:
            action = "info_running"
    else:
        return None

    # info_running sin FULL CLOSE = informativo, descartar
    if action == "info_running":
        return None

    # "FULL TP HIT" = TP alcanzado (cierre total)
    if "FULL TP HIT" in upper:
        action = "tp_hit"

    # Find pair in update
    pair_found = None
    for alias, mt5_sym in SYMBOL_MAP.items():
        if re.search(rf'\b{alias}\b', upper):
            pair_found = (alias, mt5_sym)
            break

    # FIX 2026-04-22: Si no se detecta par en el texto, deducir del canal
    # (solo canales dedicados a un solo activo — ver _CHANNEL_DEFAULT_PAIR).
    # Cubre "Full close", "Hit our risk" sin mención del par.
    if not pair_found:
        pair_found = _deducir_par_del_canal(chat_title)

    # FIX 2026-04-22: Último fallback — canales multi-par (United Kings, SureShot INDICES).
    # Si siguen sin par, usar la última señal abierta de ese canal.
    if not pair_found:
        pair_found = _deducir_par_por_senal_activa(chat_title)

    if not pair_found:
        return None

    # FIX 2026-04-14: Extraer pips de ganancia del mensaje aliado
    _pips_profit = 0
    _pips_match = re.search(r'(\d+)\s*\+?\s*(?:PIPS|PTS)', upper)
    if _pips_match:
        _pips_profit = int(_pips_match.group(1))

    return {
        "type": "update",
        "action": action,
        "pair": pair_found[0],
        "mt5_symbol": pair_found[1],
        "pips_profit": _pips_profit,
        "tp_level": tp_level,  # solo útil cuando action=="tp_partial"
        "raw": text[:200],
        "chat_title": chat_title,  # FIX 2026-05-05: para validar fuente del update
    }


# === MT5 CONFIG ===
MT5_DEMO_LOGIN = int(os.getenv("MT5_LOGIN", 0))
MT5_DEMO_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_DEMO_SERVER = os.getenv("MT5_SERVER", "")


def _mt5_init_and_login():
    """Initialize MT5 and login to the configured account."""
    import price_feed as mt5
    # FIX 2026-04-16: Solo inicializar si MT5 no está ya conectado (evita reinicializaciones repetidas)
    if mt5.terminal_info() is None:
        if not mt5.initialize():
            return False, "MT5 not initialized"
    # Login explícito para asegurar que estamos en la cuenta correcta
    if MT5_DEMO_LOGIN and MT5_DEMO_PASSWORD:
        acc = mt5.account_info()
        if acc is None or acc.login != MT5_DEMO_LOGIN:
            if not mt5.login(MT5_DEMO_LOGIN, password=MT5_DEMO_PASSWORD, server=MT5_DEMO_SERVER):
                return False, f"MT5 login failed: {mt5.last_error()}"
    return True, "OK"


# === MT5 EXECUTION ===
def _check_mt5_circuit_breaker() -> bool:
    """Lee el estado del circuit breaker desde archivo compartido.
    Retorna True si está ABIERTO (no operar).

    FIX 2026-04-21 (A3): auto-evaluación — si los últimos N trades cerrados
    son SL consecutivos, abrir el breaker automáticamente por 2h."""
    # 1) Auto-check: ¿5+ SLs consecutivos en las últimas 2h?
    try:
        if COPIER_STATS_FILE.exists():
            with open(COPIER_STATS_FILE, "r", encoding="utf-8") as _f_acb:
                _stats_cb = json.load(_f_acb)
            _trades_cb = _stats_cb.get("trades", [])
            _recent = sorted(_trades_cb, key=lambda t: t.get("closed_at", 0), reverse=True)[:10]
            # Contar SLs al inicio de la lista (los más recientes)
            _consec_sl = 0
            for _t in _recent:
                if _t.get("result") == "sl":
                    _consec_sl += 1
                else:
                    break
            if _consec_sl >= 5:
                # Auto-abrir breaker por 2h
                _cb_file_auto = Path(__file__).parent / "mt5_circuit_breaker.json"
                _opened_at = time.time()
                try:
                    _cb_data_auto = {
                        "state": "OPEN",
                        "opened_at": _opened_at,
                        "reason": f"auto: {_consec_sl} SL consecutivos",
                        "cooldown_seg": 7200,  # 2h
                    }
                    _tmp_cb = str(_cb_file_auto) + ".tmp"
                    with open(_tmp_cb, "w", encoding="utf-8") as _f_w:
                        json.dump(_cb_data_auto, _f_w, ensure_ascii=False, indent=2)
                    os.replace(_tmp_cb, str(_cb_file_auto))
                    log.warning(f"🚨 Circuit Breaker AUTO-ABIERTO: {_consec_sl} SLs consecutivos — pausado 2h")
                    return True
                except Exception:
                    pass
    except Exception:
        pass

    # 2) Check manual/persistente desde archivo
    try:
        _cb_file = Path(__file__).parent / "mt5_circuit_breaker.json"
        if _cb_file.exists():
            import json as _json_cb_read
            with open(_cb_file, "r") as _f_cb_r:
                _cb = _json_cb_read.load(_f_cb_r)
            if _cb.get("state") == "OPEN":
                import time as _time_cb
                elapsed = _time_cb.time() - _cb.get("opened_at", 0)
                # FIX: respetar el cooldown_seg configurado (default 2h para auto, 5min para manual)
                _cooldown = _cb.get("cooldown_seg", 300)
                if elapsed < _cooldown:
                    _mins_left = (_cooldown - elapsed) / 60
                    log.warning(f"🚨 Circuit Breaker ABIERTO — MT5 pausado ({_mins_left:.1f}min restantes)")
                    return True
    except Exception:
        pass
    return False


def execute_in_mt5(signal):
    """Execute signal in MT5. Returns (success, detail).

    FIX 2026-05-02 (DEMO €10k): bloqueos retirados — Circuit Breaker, kill-switch,
    spread guard y limite de posiciones DESACTIVADOS porque la cuenta es DEMO sin
    riesgo real. Para reactivar bajo cuenta real: COPIER_GUARDS=true en .env.
    """
    _guards_on = os.getenv("COPIER_GUARDS", "false").strip().lower() in ("1","true","yes","on")

    if os.getenv("MT5_EXECUTION_DISABLED", "").strip().lower() in ("1", "true", "yes", "on"):
        log.info(f"ℹ️ MT5_EXECUTION_DISABLED=1 — skip ejecucion {signal.get('direction','?')} {signal.get('pair','?')} (publicacion-only mode)")
        return False, "MT5 execution disabled by user (publication-only mode)"

    # ════════════════════════════════════════════════════════════════════
    # FIX 2026-05-12 — BS365 SAFETY GUARDS (P0+P1+P2)
    # ════════════════════════════════════════════════════════════════════
    # Tras la auditoria del 12 may detectando 25+ fallos, estos guards son
    # OBLIGATORIOS (siempre activos, sin .env flag) para evitar:
    #  - Ejecuciones sin SL (us100 BUY 2.2 sin stop = riesgo ilimitado)
    #  - SL/TP invertidos por bug del parser (BUY con TP debajo del entry)
    #  - Lots gigantes (us100 2.2, us30 1.3) por bug del calc lot
    #  - Hedge sin sentido (mismo par + direccion opuesta abiertas)
    #  - Spam contradictorio (3 GBPUSD opuestos en 3 min)
    #  - Señales <60% probabilidad (en mercados normales son ruido)

    _sig_pair = signal.get("pair", "?")
    _sig_dir = (signal.get("direction") or "").upper()
    _sig_entry = float(signal.get("entry") or 0)
    _sig_sl = float(signal.get("sl") or 0)
    _sig_tp = float(signal.get("tp") or 0)
    _sig_prob = signal.get("probability")

    # GUARD P1.4: Filtro probability minimo (default 60%, configurable).
    # Si la señal trae probability < min → no ejecutar (NO spam de señales de
    # baja calidad). Si no trae probability (señal aliado sin score) → permitir.
    _min_pub_prob = float(os.getenv("MIN_PUBLISH_PROBABILITY", "0"))
    if _sig_prob is not None:
        try:
            if float(_sig_prob) < _min_pub_prob:
                log.warning(
                    f"🛡️ GUARD prob<{_min_pub_prob:.0f}%: {_sig_dir} {_sig_pair} prob={_sig_prob} — skip execute"
                )
                return False, f"probability {_sig_prob}% < min {_min_pub_prob:.0f}%"
        except (TypeError, ValueError):
            pass

    # GUARD P0.3: SL obligatorio. NO ejecutar sin Stop Loss configurado.
    # Caso us100 BUY 2.2 lot sin SL del 12-may: -$164 floating sin protección.
    if _sig_sl <= 0:
        log.warning(
            f"🛡️ GUARD no-SL: {_sig_dir} {_sig_pair} entry={_sig_entry} — NO ejecutar sin SL (riesgo ilimitado)"
        )
        return False, "signal sin SL (P0.3 mandatory)"

    # GUARD P0.2: Coherencia SL/TP vs direccion. BUY → TP > entry > SL.
    # SELL → SL > entry > TP. Cubre el bug del 12-may us100 BUY con TP debajo.
    if _sig_entry > 0 and _sig_sl > 0:
        if _sig_dir == "BUY" and _sig_sl >= _sig_entry:
            log.warning(
                f"🛡️ GUARD SL-coherence: BUY {_sig_pair} sl={_sig_sl} >= entry={_sig_entry} — SL deberia estar DEBAJO"
            )
            return False, "SL >= entry para BUY (P0.2 invertido)"
        if _sig_dir == "SELL" and _sig_sl <= _sig_entry:
            log.warning(
                f"🛡️ GUARD SL-coherence: SELL {_sig_pair} sl={_sig_sl} <= entry={_sig_entry} — SL deberia estar ARRIBA"
            )
            return False, "SL <= entry para SELL (P0.2 invertido)"
    if _sig_entry > 0 and _sig_tp > 0:
        if _sig_dir == "BUY" and _sig_tp <= _sig_entry:
            log.warning(
                f"🛡️ GUARD TP-coherence: BUY {_sig_pair} tp={_sig_tp} <= entry={_sig_entry} — TP deberia estar ARRIBA"
            )
            return False, "TP <= entry para BUY (P0.2 invertido)"
        if _sig_dir == "SELL" and _sig_tp >= _sig_entry:
            log.warning(
                f"🛡️ GUARD TP-coherence: SELL {_sig_pair} tp={_sig_tp} >= entry={_sig_entry} — TP deberia estar DEBAJO"
            )
            return False, "TP >= entry para SELL (P0.2 invertido)"

    # GUARD P1.6: Anti-spam mismo par+direccion en 30 minutos.
    # Caso 12-may 16:04-16:07: 3 GBPUSD opuestos (BUY 20%, SELL 48%, BUY 25%)
    # en 3 minutos por spam de canales aliados → caos.
    _spam_key = f"exec_{_sig_pair}_{_sig_dir}"
    _spam_now = time.time()
    _spam_prev = _recently_notified.get(_spam_key, 0)
    _spam_window = float(os.getenv("ANTI_SPAM_SECONDS", "1800"))  # 30 min default
    if _spam_prev and (_spam_now - _spam_prev) < _spam_window:
        log.warning(
            f"🛡️ GUARD anti-spam: {_sig_dir} {_sig_pair} ya ejecutado hace {(_spam_now-_spam_prev):.0f}s "
            f"(ventana {_spam_window:.0f}s) — skip duplicado"
        )
        return False, f"anti-spam: mismo par+dir hace <{_spam_window:.0f}s"

    # FIX 2026-05-20: respetar el guard de PUBLICACION via flag en el signal
    # dict (no via _recently_notified). El fix anterior (18-may) chequeaba
    # _recently_notified["pub_*"] pero ese key se setea TAMBIEN cuando publish
    # es OK — entonces la senal que acababa de publicarse veia su propio
    # pub_* hace 6s y bloqueaba su propio MT5.
    # Caso real 20-may 03:06:57 SELL ORO 4501: PUBLICO al VIP, MT5 NO ejecuto,
    # luego TPs llegaron y celebracion se bloqueo (mt5_executed=False).
    # Ahora send_to_channel marca signal["_pub_blocked"]=razon (si bloquea) o
    # signal["_pub_ok"]=True (si publica OK). Exec solo bloquea si _pub_blocked.
    _pub_blocked_reason = signal.get("_pub_blocked")
    if _pub_blocked_reason:
        log.warning(
            f"🛡️ GUARD pub-mirror: {_sig_dir} {_sig_pair} publicacion bloqueada "
            f"({_pub_blocked_reason}) — NO ejecutar MT5 si VIP no la vio"
        )
        return False, f"publish blocked ({_pub_blocked_reason}) — skip MT5"

    # GUARD P2.8: Anti-duplicado misma signature (entry+SL+TP) en 5 min.
    # Caso 12-may us30 duplicada: 2× BUY 0.4 @ 49471.23 con SL/TP idénticos.
    _dup_sig = f"{_sig_pair}_{_sig_dir}_{_sig_entry:.5f}_{_sig_sl:.5f}_{_sig_tp:.5f}"
    _dup_key = f"dup_{_dup_sig}"
    _dup_prev = _recently_notified.get(_dup_key, 0)
    _dup_window = 300  # 5 min
    if _dup_prev and (_spam_now - _dup_prev) < _dup_window:
        log.warning(
            f"🛡️ GUARD anti-dup: {_dup_sig} duplicada hace {(_spam_now-_dup_prev):.0f}s — skip"
        )
        return False, "anti-duplicate: signature identical <5min"

    # AutoTrading button: este SI se respeta siempre (lo controla MT5 directamente)
    try:
        import price_feed as _mt5_chk
        if _mt5_chk.initialize():
            _ti = _mt5_chk.terminal_info()
            if _ti and not _ti.trade_allowed:
                log.info(f"ℹ️ AutoTrading desactivado en MT5 — skip ejecucion {signal.get('direction','?')} {signal.get('pair','?')} (activa el boton verde para ejecutar)")
                return False, "AutoTrading disabled in MT5 — enable button to resume execution"
    except Exception as _e_chk:
        log.debug(f"check trade_allowed pre-execute fallo: {_e_chk}")

    # Circuit Breaker / Kill-switch — solo si COPIER_GUARDS=true
    if _guards_on:
        if _check_mt5_circuit_breaker():
            return False, "Circuit Breaker OPEN — MT5 pausado"
        try:
            _ks_file = Path(__file__).parent / "kill_switch.json"
            if _ks_file.exists():
                import json as _ksj
                with open(_ks_file, "r", encoding="utf-8") as _f:
                    _ks = _ksj.load(_f)
                if _ks.get("active", False):
                    return False, f"Kill-switch activo: {_ks.get('reason', 'desconocido')}"
        except Exception:
            pass

    try:
        import price_feed as mt5
    except ImportError:
        return False, "MetaTrader5 not installed"

    ok, msg = _mt5_init_and_login()
    if not ok:
        return False, msg

    sym = signal["mt5_symbol"]
    info = mt5.symbol_info(sym)
    if not info:
        # FIX 2026-04-22: log todos los símbolos disponibles para diagnóstico
        try:
            _all_syms = [s.name for s in (mt5.symbols_get() or []) if any(k in s.name.upper() for k in ("GER", "US30", "DAX", "DOW", "NAS", "CAC", "FTSE"))]
            log.warning(f"❌ MT5: Símbolo '{sym}' NO encontrado en el broker. Símbolos índices disponibles: {_all_syms[:20]}")
        except Exception:
            log.warning(f"❌ MT5: Símbolo '{sym}' NO encontrado. Verifica el nombre exacto en tu plataforma MT5.")
        return False, f"Symbol {sym} not found in broker"
    if not info.visible:
        mt5.symbol_select(sym, True)

    tick = mt5.symbol_info_tick(sym)
    if not tick:
        return False, f"No tick for {sym}"

    # FIX 2026-04-24: Spread guard OPCIONAL (default OFF). Politica DEMO 2026-05-02:
    # ejecutar toda senal sin bloqueos. Reactivar con `COPIER_SPREAD_GUARD=true`.
    if _guards_on and os.getenv("COPIER_SPREAD_GUARD", "false").lower() in ("true", "1", "yes"):
        _spread_pts = (tick.ask - tick.bid) / info.point if info.point > 0 else 0
        _max_spread = {
            "GOLD": 150, "XAUUSD": 150,
            "US100Cash": 600, "US30Cash": 2000, "US500Cash": 500,
            "GER40Cash": 1000, "OILCash": 50, "BRENTCash": 50, "BTCUSD": 500,
        }.get(sym, 30)
        if _spread_pts > _max_spread:
            log.warning(f"🛡️ Spread alto en {sym}: {_spread_pts:.0f}pts > máx {_max_spread}pts — ABORTANDO (guard ON)")
            return False, f"Spread alto: {_spread_pts:.0f}pts (max {_max_spread})"

    # FIX 2026-04-21 (C2): Limite de posiciones simultaneas — solo si COPIER_GUARDS=true.
    # Cuenta DEMO sin limite (margen libre €10k cubre ~50 trades de 1% riesgo).
    if _guards_on:
        try:
            _max_positions = int(os.getenv("COPIER_MAX_POSITIONS", "8"))
            _open_pos = mt5.positions_get() or []
            _copier_open = [p for p in _open_pos if getattr(p, 'magic', 0) == MAGIC_COPIER]
            if len(_copier_open) >= _max_positions:
                log.warning(f"🛡️ Máx posiciones copier ({_max_positions}) alcanzado — ABORTANDO {sym}")
                return False, f"Máx posiciones ({_max_positions}) alcanzado"
        except Exception as _e_pos:
            log.debug(f"positions_get check falló: {_e_pos}")

    # GUARD P1.7: Anti-hedge + Anti-stack.
    # Anti-hedge: si ya hay posicion del mismo par direccion OPUESTA → NO abrir
    # (caso 12-may GBPUSD: 3 posiciones BUY+SELL+BUY mismo segundo).
    # Anti-stack (FIX 2026-05-13): si ya hay posicion del mismo par direccion
    # IGUAL → NO abrir otra. Caso 13-may BTCUSD: ticket 766999306 SELL 0.23 +
    # ticket 766938634 SELL 0.11 simultaneas (anti-spam 30min no cubrio gap
    # de 46min). Cero sentido tener 2 SELLs del mismo activo a la vez.
    # Configurable: ANTI_STACK_ENABLED=true|false (default true).
    _anti_stack_on = os.getenv("ANTI_STACK_ENABLED", "true").lower() in ("true", "1", "yes")
    try:
        _all_pos_hedge = mt5.positions_get(symbol=sym) or []
        _opposite_dir = "BUY" if _sig_dir == "SELL" else "SELL"
        for _p in _all_pos_hedge:
            if getattr(_p, "magic", 0) in BS365_MAGICS:
                _p_dir = "BUY" if _p.type == mt5.ORDER_TYPE_BUY else "SELL"
                if _p_dir == _opposite_dir:
                    log.warning(
                        f"🛡️ GUARD anti-hedge: {sym} ya tiene posicion {_p_dir} abierta — "
                        f"NO abrir {_sig_dir} opuesto (evita pagar 2x spread)"
                    )
                    return False, f"anti-hedge: {sym} {_p_dir} ya abierta"
                if _anti_stack_on and _p_dir == _sig_dir:
                    log.warning(
                        f"🛡️ GUARD anti-stack: {sym} ya tiene {_p_dir} abierta (ticket={_p.ticket}) — "
                        f"NO duplicar misma direccion (cubre el gap del anti-spam 30min)"
                    )
                    return False, f"anti-stack: {sym} {_p_dir} ya abierta"
    except Exception as _e_hedge:
        log.debug(f"anti-hedge/stack check fallo: {_e_hedge}")

    price = tick.ask if signal["direction"] == "BUY" else tick.bid
    sl = signal["sl"]
    entry = signal["entry"]
    # If entry was 0 (not in message), use current market price (sin mutar el dict original)
    if entry == 0 or entry == 0.0:
        entry = price

    # FIX 2026-05-05: TP enviado a MT5 = TP1 (el más cercano a la entrada).
    # La posición MT5 cierra rápido en TP1 para asegurar beneficio.
    # TP2/TP3 intermedios siguen notificándose al canal VIP pero MT5 no los espera.
    _is_buy_for_tp = signal["direction"] == "BUY"
    _tp_raw = [signal.get("tp", 0), signal.get("tp2", 0), signal.get("tp3", 0),
               signal.get("tp4", 0), signal.get("tp5", 0)]
    _tp_valid_mt5 = []
    for _t in _tp_raw:
        _tv = _t or 0
        if _tv <= 0:
            continue
        # Dirección correcta vs entry
        if entry > 0:
            if _is_buy_for_tp and _tv <= entry:
                continue
            if not _is_buy_for_tp and _tv >= entry:
                continue
            # Sanity: <25% de entry (descartar basura)
            if abs(_tv - entry) / entry > 0.25:
                continue
        _tp_valid_mt5.append(_tv)
    if _tp_valid_mt5:
        # TP1 = el nivel más cercano a la entrada (min para BUY, max para SELL)
        tp = min(_tp_valid_mt5) if _is_buy_for_tp else max(_tp_valid_mt5)
    else:
        tp = signal.get("tp", 0) or 0  # fallback

    # FIX 2026-05-12 P0.3 (override del FIX 2026-04-24): "Invalid SL" (risk<=0) AHORA
    # BLOQUEA la ejecucion. Caso us100 BUY 2.2 lot del 12-may sin SL = riesgo
    # ilimitado. Politica nueva: NO ejecutar nunca sin SL valido.
    risk = abs(price - sl)
    if risk <= 0:
        log.warning(
            f"🛡️ GUARD risk<=0: {sym} price={price} sl={sl} — ABORTAR (P0.3 SL mandatory)"
        )
        return False, f"risk<=0 (price={price}, sl={sl}) — SL invalido"
    if tp > 0:
        reward = abs(tp - price)
        rr = reward / risk
        log.info(f"📊 R:R = {rr:.2f} para {sym} (TP final MT5={tp}, niveles canal={len(_tp_valid_mt5)})")

    # FIX 2026-05-02: Lot dinamico basado en equity y SL.
    # RISK_PER_TRADE_PCT (default 1%) define cuanto se arriesga si SL pega.
    # lot = risk_amount / loss_per_lot (donde loss_per_lot = sl_distance/tick_size * tick_value)
    # Sin SL valido: fallback proporcional al equity (€10k -> ~0.10 lotes).
    lot = info.volume_min  # fallback inicial
    try:
        _step = info.volume_step or 0.01
        _vmin = info.volume_min or 0.01
        _vmax = info.volume_max or 100.0
        _risk_pct = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
        _acc_info = mt5.account_info()
        _equity = float(getattr(_acc_info, "equity", 0) or 0) if _acc_info else 0.0
        _sl_dist = abs(price - sl) if sl and sl > 0 else 0.0
        _tick_val = float(getattr(info, "trade_tick_value", 0) or 0)
        _tick_size = float(getattr(info, "trade_tick_size", 0) or info.point or 0)

        if _equity > 0 and _sl_dist > 0 and _tick_val > 0 and _tick_size > 0:
            _risk_amount = _equity * (_risk_pct / 100.0)
            _loss_per_lot = (_sl_dist / _tick_size) * _tick_val
            if _loss_per_lot > 0:
                _calc = _risk_amount / _loss_per_lot
                _calc = round(_calc / _step) * _step
                lot = max(_vmin, min(_vmax, round(_calc, 2)))
        elif _equity > 0:
            # Fallback sin SL: lote proporcional al equity (~0.10 por €10k).
            _calc = _equity / 100000.0
            _calc = round(_calc / _step) * _step
            lot = max(_vmin, min(_vmax, round(_calc, 2)))
        log.info(f"📐 Lot {sym}: equity=€{_equity:.0f} risk={_risk_pct}% sl_dist={_sl_dist:.5f} → {lot} lotes")
    except Exception as _e_lot:
        log.warning(f"⚠️ Lot calc fallo ({_e_lot}) — usando volume_min {info.volume_min}")
        lot = info.volume_min

    # GUARD P1.5: Lot cap absoluto. Caso 12-may us100 2.2 lot ($22/pip),
    # us30 1.3 lot ($13/pip), gbpusd 0.62 — el calc lot dio numeros gigantes
    # para una cuenta de €9k. Cap absoluto evita riesgo desproporcionado.
    _max_lot_abs = float(os.getenv("MAX_LOT_ABSOLUTE", "0.30"))
    if lot > _max_lot_abs:
        log.warning(
            f"🛡️ GUARD lot-cap: {sym} calc={lot:.2f} > max {_max_lot_abs:.2f} — cap aplicado"
        )
        lot = _max_lot_abs
        # Ajustar al step del simbolo
        try:
            _step_cap = info.volume_step or 0.01
            lot = round(round(lot / _step_cap) * _step_cap, 2)
            lot = max(info.volume_min or 0.01, lot)
        except Exception:
            pass

    # FIX 2026-04-16: Respetar tipo de orden (Limit/Stop → PENDING, Market → DEAL)
    sig_order_type = signal.get("order_type", "Market")
    sig_is_limit   = signal.get("is_limit", False)
    is_buy = signal["direction"] == "BUY"

    # FIX 2026-05-06: Si el precio ya superó TP1, el tren ya se fue — descartar orden MT5.
    # Evita colocar BUY LIMIT por debajo del mercado cuando el aliado publicó tarde.
    if tp > 0:
        _tp1_passed = (is_buy and price >= tp) or (not is_buy and price <= tp)
        if _tp1_passed:
            log.warning(
                f"🚫 MT5 SKIP {sym}: mercado {price:.5f} ya superó TP1 {tp:.5f} "
                f"({'BUY' if is_buy else 'SELL'}) — señal expirada, no se coloca orden"
            )
            return False, f"Señal expirada: precio ya pasó TP1 ({tp:.5f})"

    if sig_order_type == "Limit" or sig_is_limit:
        order_type  = mt5.ORDER_TYPE_BUY_LIMIT  if is_buy else mt5.ORDER_TYPE_SELL_LIMIT
        trade_action = mt5.TRADE_ACTION_PENDING
        exec_price   = round(entry, info.digits)  # Usar precio de entrada del canal
    elif sig_order_type == "Stop":
        order_type  = mt5.ORDER_TYPE_BUY_STOP  if is_buy else mt5.ORDER_TYPE_SELL_STOP
        trade_action = mt5.TRADE_ACTION_PENDING
        exec_price   = round(entry, info.digits)
    else:
        order_type  = mt5.ORDER_TYPE_BUY  if is_buy else mt5.ORDER_TYPE_SELL
        trade_action = mt5.TRADE_ACTION_DEAL
        exec_price   = price  # Precio de mercado actual

    request = {
        "action": trade_action,
        "symbol": sym,
        "volume": lot,
        "type": order_type,
        "price": exec_price,
        "sl": round(sl, info.digits),
        "tp": round(tp, info.digits),
        "deviation": 30,
        "magic": MAGIC_COPIER,
        "comment": "BuySell365 Pro",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    # FIX 2026-04-27: helper para guardar precio REAL de ejecucion en el signal.
    # Antes: el signal mantenia solo el entry teorico del aliado. Cuando el bot
    # ejecutaba a precio distinto (ej. retry tras fin de semana con mercado
    # movido) el monitor TP/SL y el daily tracker seguian usando el entry
    # teorico → "TP HIT +16 pips" falso mientras la cuenta real perdia.
    # Ahora signal["mt5_entry"] queda con el precio efectivo del broker.
    def _record_execution(_res, _exec_default):
        try:
            _real = float(getattr(_res, "price", 0) or _exec_default)
        except (TypeError, ValueError):
            _real = float(_exec_default)
        signal["mt5_entry"] = _real
        signal["mt5_executed_at"] = time.time()
        signal["mt5_lot"] = float(lot)
        signal["mt5_sl_set"] = float(request.get("sl", 0) or 0)
        signal["mt5_tp_set"] = float(request.get("tp", 0) or 0)
        # FIX 2026-05-16: detectar drift entry teorica vs fill real y avisar al VIP
        # si supera ENTRY_DRIFT_NOTIFY_PCT del SL distance (default 10%). Caso real
        # 15-may BTC: publicada 80104.95, fill 80108.15 -> drift 3.2 pts (~16% del
        # SL de 200 pts). El cliente ve un precio y se ejecuta a otro sin aviso.
        try:
            _theoric = float(signal.get("entry", 0) or 0)
            _sl_e = float(signal.get("sl", 0) or 0)
            if _theoric > 0 and _sl_e > 0 and _real > 0:
                _sl_dist_e = abs(_theoric - _sl_e)
                _drift_e = abs(_real - _theoric)
                _pct_thr = float(os.getenv("ENTRY_DRIFT_NOTIFY_PCT", "0.10"))
                if _sl_dist_e > 0 and _drift_e > _sl_dist_e * _pct_thr:
                    log.warning(
                        f"⚠️ ENTRY DRIFT: {signal.get('pair','?')} teorica={_theoric} "
                        f"fill={_real} drift={_drift_e:.4f} ({_drift_e/_sl_dist_e*100:.0f}% del SL)"
                    )
                    # Notificar al VIP con mensaje breve, una sola vez por signal
                    if not signal.get("_drift_notified"):
                        signal["_drift_notified"] = True
                        try:
                            _pair_d_drift = _get_display_pair(signal.get("pair", ""))
                            _drift_msg = (
                                f"📌 *Entry adjusted* — {_pair_d_drift}\n"
                                f"Filled at *{_real}* (planned {_theoric}). "
                                f"Spread/market drift {_drift_e/_sl_dist_e*100:.0f}% of SL."
                                f"{ELI_SIG}"
                            )
                            _drift_msg = _safe_publish_vip(
                                _drift_msg, kind="update",
                                pair=_pair_d_drift, direction=signal.get("direction", ""),
                            )
                            if _drift_msg:
                                _url_drift = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                                _payload_drift = {
                                    "chat_id": CHANNEL_ID,
                                    "text": _drift_msg,
                                    "parse_mode": "Markdown",
                                }
                                requests.post(_url_drift, json=_payload_drift, timeout=10)
                                log.info(f"📌 Drift notif enviada al VIP: {_pair_d_drift}")
                        except Exception as _e_drift_n:
                            log.debug(f"Drift notify error: {_e_drift_n}")
        except Exception as _e_drift:
            log.debug(f"Drift check error: {_e_drift}")
        # FIX 2026-05-11 (tarde-3): guardar _mt5_ticket para que handle_update_mt5
        # pueda cerrar la posicion exacta cuando llegue update del aliado. Antes:
        # solo se persistia si reconcile encontraba la posicion vivente (linea 1379),
        # dejando senales pre-restart o ejecuciones recientes sin ticket → fallback
        # a magic match que puede confundir posiciones del mismo par.
        try:
            _order_id = int(getattr(_res, "order", 0) or 0)
            _deal_id  = int(getattr(_res, "deal",  0) or 0)
            # Para market orders, order==position normalmente.
            # Para limit/stop, el position_id se asigna cuando se ejecuta — preferir deal.
            _ticket_to_save = _deal_id or _order_id
            if _ticket_to_save > 0:
                signal["_mt5_ticket"] = _ticket_to_save
                signal["mt5_ticket"] = _ticket_to_save  # alias por compatibilidad
        except (TypeError, ValueError):
            pass
        # FIX 2026-05-12: marcar guards anti-spam y anti-dup tras ejecucion
        # exitosa (P1.6 + P2.8). Asi proximos intentos del mismo par+dir o
        # misma signature se bloquean dentro de la ventana.
        try:
            _recently_notified[_spam_key] = time.time()
            _recently_notified[_dup_key] = time.time()
            _save_notif_dedup()
        except Exception:
            pass
        return _real

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        _real = _record_execution(result, exec_price)
        return True, f"Executed {signal['direction']} {sym} @ {_real} Lot={lot} [{sig_order_type}]"

    err = result.comment if result else "No response"

    # FIX 2026-04-17: "Invalid stops" — ajuste MÚLTIPLE (SL+TP) con stops_level del broker
    # Antes solo ajustaba SL y a veces el TP también era inválido → reintento fallaba (US100 05:41).
    if result and "invalid stops" in err.lower():
        stops_level = info.trade_stops_level  # puntos mínimos distancia
        point       = info.point
        min_dist    = (stops_level + 10) * point  # +10 puntos de margen extra

        # Recalcular SL y TP respetando min_dist
        if is_buy:
            sl_adj = round(exec_price - min_dist, info.digits)
            tp_adj = round(max(tp, exec_price + min_dist), info.digits) if tp > 0 else 0
        else:
            sl_adj = round(exec_price + min_dist, info.digits)
            tp_adj = round(min(tp, exec_price - min_dist), info.digits) if tp > 0 else 0

        log.warning(f"⚠️ Invalid stops {sym} — ajustando SL {sl}→{sl_adj} TP {tp}→{tp_adj} (min_dist={min_dist})")
        request["sl"] = sl_adj
        if tp_adj > 0:
            request["tp"] = tp_adj
        result2 = mt5.order_send(request)
        if result2 and result2.retcode == mt5.TRADE_RETCODE_DONE:
            _real2 = _record_execution(result2, exec_price)
            return True, f"Executed {signal['direction']} {sym} @ {_real2} [SL/TP ajustados: {sl_adj}/{tp_adj}]"
        # FIX 2026-04-24: si el ajuste tambien falla → ejecutar SIN SL ni TP (usuario los pone manual).
        # Caso real 24/04 14:12: SureShot XAUUSD BUY 4715.97 con precio 4707.60 → SL imposible, pero
        # politica usuario es EJECUTAR TODA senal. Antes era return False, ahora reintento sin stops.
        log.warning(f"⚠️ Invalid stops {sym} ajustado tampoco funciono — reintentando SIN SL/TP")
        request["sl"] = 0.0
        request["tp"] = 0.0
        result3 = mt5.order_send(request)
        if result3 and result3.retcode == mt5.TRADE_RETCODE_DONE:
            _real3 = _record_execution(result3, exec_price)
            return True, f"Executed {signal['direction']} {sym} @ {_real3} [SIN SL/TP — ajusta manual]"
        err = result3.comment if result3 else "No response"
        return False, f"MT5 skip (invalid stops tras 2 reintentos): {err}"

    # FIX 2026-04-17: "Invalid price" (precio stale en Limit/Stop) — convertir a Market
    if result and "invalid price" in err.lower() and trade_action == mt5.TRADE_ACTION_PENDING:
        log.warning(f"⚠️ Invalid price {sym} en orden {sig_order_type} — convirtiendo a Market")
        request["action"] = mt5.TRADE_ACTION_DEAL
        request["type"] = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        request["price"] = tick.ask if is_buy else tick.bid
        result3 = mt5.order_send(request)
        if result3 and result3.retcode == mt5.TRADE_RETCODE_DONE:
            _real_mkt = _record_execution(result3, request["price"])
            return True, f"Executed {signal['direction']} {sym} @ {_real_mkt} [fallback de {sig_order_type}]"
        err = result3.comment if result3 else "No response"
        return False, f"MT5 skip (invalid price tras fallback Market): {err}"

    # FIX 2026-04-26: detectar mercado cerrado para reintentar al abrir.
    # El caller marcara la senhal como _pending_market_open en _open_signals.
    if "market closed" in err.lower() or "market is closed" in err.lower():
        return False, f"MARKET_CLOSED:{err}"
    return False, f"MT5 error: {err}"


def handle_update_mt5(update):
    """Handle signal updates (close half, move SL to entry, etc.)."""
    action = update["action"]

    # FIX 2026-04-17: tp_partial (TP1/TP2/TP3/TP4 intermedios) NO toca MT5.
    # La posición sigue corriendo hasta el último TP (configurado en execute_in_mt5)
    # o hasta un cierre explícito del canal (full_close/close_half/etc.).
    if action == "tp_partial":
        _lvl = update.get("tp_level", 0)
        return True, f"TP{_lvl} intermedio — posición MT5 sigue corriendo (nivel celebrado en canal)"

    try:
        import price_feed as mt5
        ok, msg = _mt5_init_and_login()
        if not ok:
            return False, msg

        # FIX 2026-05-11: usar .get() con fallback a SYMBOL_MAP en lugar de ["mt5_symbol"]
        # que causaba KeyError cuando el aliado mandaba close/update sin mt5_symbol en el dict.
        # Esto provocaba que las posiciones no se cerraran en MT5 a pesar de recibir la señal,
        # terminando en SL cuando deberían haber cerrado en TP. Error visible en logs: ❌ 'mt5_symbol'
        sym = update.get("mt5_symbol")
        if not sym:
            _raw_pair = (update.get("pair") or "").upper()
            sym = SYMBOL_MAP.get(_raw_pair) or _raw_pair
        if not sym:
            return False, "No mt5_symbol ni pair en el update — no se puede resolver símbolo MT5"

        positions = mt5.positions_get(symbol=sym)
        if not positions:
            return False, f"No open position for {sym}"

        # FIX 2026-05-06: Buscar posición por ticket guardado en _open_signals primero.
        # Antes solo filtraba por MAGIC_COPIER — posiciones colocadas manualmente
        # (sin magic del copier) no se cerraban aunque el canal aliado dijera "cierra".
        # Ahora: primero intenta por ticket conocido, luego magic, luego cualquier posición del par.
        _tracked_ticket = None
        with _signals_lock:
            for _sid, _sdata in _open_signals.items():
                _s = _sdata.get("signal", {})
                if _s.get("pair") == update.get("pair") or _s.get("mt5_symbol") == sym:
                    _tracked_ticket = _s.get("_mt5_ticket", 0)
                    break

        pos = None
        # 1) Buscar por ticket conocido (incluye posiciones manuales)
        if _tracked_ticket:
            for p in positions:
                if p.ticket == int(_tracked_ticket):
                    pos = p
                    log.info(f"🎯 handle_update_mt5: posición encontrada por ticket #{_tracked_ticket}")
                    break
        # 2) Fallback: buscar por magic del copier
        if not pos:
            for p in positions:
                if p.magic == MAGIC_COPIER:
                    pos = p
                    log.info(f"🎯 handle_update_mt5: posición encontrada por MAGIC_COPIER")
                    break
        # 3) Fallback final: cerrar TODAS las posiciones del par (política canal aliado = cierra todo)
        if not pos and action in ("full_close", "close_half", "close_partial"):
            log.warning(
                f"⚠️ handle_update_mt5: no hay posición COPIER para {sym}. "
                f"Usando primera posición del par (puede ser manual)."
            )
            pos = positions[0]
        if not pos:
            return False, f"No copier position for {sym}"

        if action in ("close_half", "close_partial"):
            # close_half = 50%, close_partial = 30% (o mínimo si no se puede dividir)
            if action == "close_half":
                close_vol = round(pos.volume / 2, 2)
            else:
                close_vol = round(pos.volume * 0.3, 2)  # ~30% para cierre parcial
            # FIX 2026-04-16: Null check para symbol_info (puede retornar None)
            _sym_info = mt5.symbol_info(sym)
            if not _sym_info:
                return False, f"Symbol info unavailable for {sym}"
            vol_min = _sym_info.volume_min
            if close_vol < vol_min:
                if action == "close_partial":
                    return False, f"Cannot split {sym} — volume too small for partial ({pos.volume} lots)"
                close_vol = pos.volume  # Close all if can't split (close_half)

            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            # FIX 2026-05-06 (#3): null guard en symbol_info_tick. Sin esto, en
            # desconexion broker accede a tick.bid/.ask y crash silencioso.
            tick = mt5.symbol_info_tick(sym)
            if tick is None:
                log.warning(f"❌ Close {action} {sym}: tick=None (broker desconectado?)")
                return False, f"No tick disponible para cerrar {sym}"
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
            _label = "Close half" if action == "close_half" else "Close partial"
            return (result and result.retcode == mt5.TRADE_RETCODE_DONE), f"{_label} {sym} ({close_vol} lots)"

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
            # FIX 2026-05-06 (#3): null guard en symbol_info_tick (idem close_half)
            tick = mt5.symbol_info_tick(sym)
            if tick is None:
                log.warning(f"❌ Full close {action} {sym}: tick=None (broker desconectado?)")
                return False, f"No tick disponible para cerrar {sym}"
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
# FIX 2026-05-11: migrado Gemini -> Anthropic (Claude Haiku). Gemini agotaba cuota
# gratuita (429) y disparaba circuit breaker 1h cada par de senales. Ahora usa la
# misma API key ANTHROPIC_API_KEY del parser principal (cuota pagada).
_IA_EVAL_MODEL = os.getenv("IA_EVAL_MODEL", "claude-haiku-4-5-20251001").strip()
_ia_eval_cb_until: float = 0
_ia_eval_cb_duration = 1800  # 30min silencio tras error de cuota (raro con Anthropic)


def _ia_evaluar_senal(signal):
    """IA evalua la senal y genera comentario de 1 linea. Retorna (aprobar, comentario).

    Usa Claude Haiku (rapido + barato ~$0.0003/llamada) via ANTHROPIC_API_KEY.
    Override del modelo con IA_EVAL_MODEL en .env si se prefiere Sonnet.
    Con circuit breaker silencioso para 429/cuota: no bloquea el flujo si falla."""
    global _ia_eval_cb_until
    _api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not _api_key:
        return True, ""
    # Circuit breaker activo -> saltar IA eval sin ruido
    if time.time() < _ia_eval_cb_until:
        return True, ""
    try:
        import anthropic
        # FIX 2026-05-20: más reintentos en 529 (sobrecarga Anthropic)
        _client = anthropic.Anthropic(api_key=_api_key, max_retries=5)

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

        # FIX 2026-05-11: prompt NO incluye sanity-check de precio absoluto.
        # El conocimiento del LLM esta congelado (oro a ~3300 USD segun training),
        # asi que un check tipo "implausible" rechaza precios reales actuales (4700+).
        # El check stale-entry vs precio MT5 ya cubre ese caso correctamente.
        _system = (
            "You are a professional trading analyst. Output ONLY a short 1-line "
            "analysis (max 80 chars). Do NOT judge price plausibility based on "
            "absolute values — markets move. Do NOT approve or reject. "
            "Examples: 'Tight SL, good R:R setup' / 'Counter-trend, manage risk' / "
            "'Range-bound entry, scalp-style'."
        )
        _user = f"Signal: {_dir} {_pair} @ {_entry}\nSL: {_sl} | TP: {_tp} | R:R: {rr}"
        _resp = _client.messages.create(
            model=_IA_EVAL_MODEL,
            max_tokens=80,
            system=_system,
            messages=[{"role": "user", "content": _user}],
        )
        _txt = ""
        if _resp and _resp.content:
            for _b in _resp.content:
                if getattr(_b, "type", "") == "text":
                    _txt = (getattr(_b, "text", "") or "").strip()
                    break
        comentario = _txt[:100] if _txt else ""
        return True, comentario
    except Exception as e:
        _errstr = str(e)
        if "429" in _errstr or "quota" in _errstr.lower() or "rate limit" in _errstr.lower() or "overloaded" in _errstr.lower():
            _ia_eval_cb_until = time.time() + _ia_eval_cb_duration
            log.warning(f"🚨 Anthropic IA eval rate-limit/overloaded — circuit breaker 30min activado")
        else:
            log.warning(f"IA eval error: {e}")
        return True, ""


# === TELEGRAM BOT SEND ===
def send_to_channel(signal, executed, detail):
    """Envía señales al canal BuySell365 en formato español profesional."""
    import requests

    # FIX 2026-05-18: FILTRO DE PROBABILIDAD EN PUBLICACION DESACTIVADO por petición del usuario.
    # El usuario quiere que TODAS las señales lleguen al canal VIP con su % mostrado,
    # sin importar si es 30%, 50% o 90%. El filtro de ejecución en MT5 sigue activo
    # (solo ejecuta >= MIN_PUBLISH_PROBABILITY) pero la publicación al VIP es siempre.
    # Bloque conservado con default "0" para mantener la palanca por env var:
    # si quieres reactivar el filtro un dia, basta poner MIN_PUBLISH_PROBABILITY=60.
    if signal.get("type") == "new_signal":
        _pub_prob = signal.get("probability")
        if _pub_prob is not None:
            try:
                _min_pub = float(os.getenv("MIN_PUBLISH_PROBABILITY", "0"))
                if float(_pub_prob) < _min_pub:
                    log.warning(
                        f"🛡️ PUBLISH GUARD prob<{_min_pub:.0f}%: "
                        f"{signal.get('direction','?')} {signal.get('pair','?')} "
                        f"prob={_pub_prob} — NO publicar al canal VIP"
                    )
                    return
            except (TypeError, ValueError):
                pass

        # FIX 2026-05-18 P0.1: aplicar anti-spam / anti-hedge / anti-stack / anti-dup
        # TAMBIEN antes de publicar al VIP — antes solo vivian en execute_in_mt5,
        # por eso 18-may se publicaron US30 BUY + SELL al VIP con 1 min de gap
        # (execute_in_mt5 bloqueo la 2a en MT5 pero el VIP ya las habia visto).
        _pp_pair = (signal.get("pair") or "?").strip()
        _pp_dir = (signal.get("direction") or "").upper()
        _pp_entry = float(signal.get("entry") or 0)
        _pp_sl = float(signal.get("sl") or 0)
        _pp_tp = float(signal.get("tp") or 0)
        _pp_now = time.time()
        # 1) Anti-spam mismo par+direccion publicada hace <ANTI_SPAM_SECONDS
        # FIX 2026-05-20: bypass si el precio de entrada difiere >0.3% del ultimo publicado
        # (señales genuinamente distintas aunque sean mismo par+dir en ventana corta).
        _pp_spam_key = f"pub_{_pp_pair}_{_pp_dir}"
        _pp_spam_prev = _recently_notified.get(_pp_spam_key, 0)
        _pp_spam_window = float(os.getenv("ANTI_SPAM_SECONDS", "300"))  # FIX 2026-05-20: 5 min (era 30 min)
        if _pp_spam_prev and (_pp_now - _pp_spam_prev) < _pp_spam_window:
            # Bypass si el entry difiere significativamente del último publicado
            _pp_prev_entry = float(_recently_notified.get(f"pub_entry_{_pp_pair}_{_pp_dir}", 0) or 0)
            _pp_entry_dev = abs(_pp_entry - _pp_prev_entry) / _pp_prev_entry if _pp_prev_entry > 0 and _pp_entry > 0 else 0
            if _pp_entry_dev > 0.003:  # >0.3% de diferencia → señal distinta, dejar pasar
                log.info(
                    f"✅ PUBLISH GUARD bypass entry-deviation: {_pp_dir} {_pp_pair} "
                    f"entry={_pp_entry:.2f} vs prev={_pp_prev_entry:.2f} ({_pp_entry_dev*100:.2f}%) — publicar"
                )
            else:
                log.warning(
                    f"🛡️ PUBLISH GUARD anti-spam: {_pp_dir} {_pp_pair} ya publicada hace "
                    f"{(_pp_now - _pp_spam_prev):.0f}s entry_dev={_pp_entry_dev*100:.2f}% — NO publicar duplicado al VIP"
                )
                signal["_pub_blocked"] = "anti-spam"
                return
        # 2) Anti-dup signature identica (par+dir+entry+sl+tp) en 5 min
        _pp_dup_sig = f"{_pp_pair}_{_pp_dir}_{_pp_entry:.5f}_{_pp_sl:.5f}_{_pp_tp:.5f}"
        _pp_dup_key = f"pubdup_{_pp_dup_sig}"
        _pp_dup_prev = _recently_notified.get(_pp_dup_key, 0)
        if _pp_dup_prev and (_pp_now - _pp_dup_prev) < 300:
            log.warning(
                f"🛡️ PUBLISH GUARD anti-dup: {_pp_dup_sig} duplicada hace "
                f"{(_pp_now - _pp_dup_prev):.0f}s — NO publicar al VIP"
            )
            signal["_pub_blocked"] = "anti-dup"
            return
        # 3) Anti-hedge / anti-stack vs MT5 (par + direccion opuesta o igual ya abierta)
        # FIX 2026-05-22 (Bug E+G): per regla feedback_publicar_todas_sin_filtro,
        # TODAS las senales al VIP — el cliente paga para VER. Anti-hedge/stack
        # ahora SOLO bloquea MT5 execute (via _pub_blocked flag), publicacion VIP
        # continua. Caso 22-may: 2 SELL XAUUSD reales (United Kings 09:38 +
        # SureShot 10:47) bloqueados, usuario tuvo que pegar manual.
        try:
            import price_feed as _mt5_pp
            _sym_pp = signal.get("mt5_symbol") or _pp_pair
            if _mt5_pp.initialize() and _sym_pp:
                _pos_pp = _mt5_pp.positions_get(symbol=_sym_pp) or []
                _opp_dir = "BUY" if _pp_dir == "SELL" else "SELL"
                _anti_stack_pub = os.getenv("ANTI_STACK_ENABLED", "true").lower() in ("true", "1", "yes")
                for _pp_p in _pos_pp:
                    if getattr(_pp_p, "magic", 0) in BS365_MAGICS:
                        _pp_p_dir = "BUY" if _pp_p.type == _mt5_pp.ORDER_TYPE_BUY else "SELL"
                        if _pp_p_dir == _opp_dir:
                            log.warning(
                                f"🛡️ PUBLISH GUARD anti-hedge: {_sym_pp} ya tiene {_pp_p_dir} abierta — "
                                f"publicar al VIP pero MT5 NO ejecutara (evita hedge real)"
                            )
                            signal["_pub_blocked"] = "anti-hedge"
                            # NO return — dejar caer a la publicacion VIP. _pub_blocked
                            # hace que execute_in_mt5 skip (linea ~9317).
                            break
                        if _anti_stack_pub and _pp_p_dir == _pp_dir:
                            log.warning(
                                f"🛡️ PUBLISH GUARD anti-stack: {_sym_pp} ya tiene {_pp_p_dir} abierta "
                                f"(ticket={_pp_p.ticket}) — publicar al VIP pero MT5 NO ejecutara (evita stack)"
                            )
                            signal["_pub_blocked"] = "anti-stack"
                            # NO return — publicar al VIP, skip MT5.
                            break
        except Exception as _e_pp_hedge:
            log.debug(f"publish anti-hedge/stack skip: {_e_pp_hedge}")
        # FIX 2026-05-20: DRIFT GUARD pre-publish para TODOS los activos.
        # Antes solo existia en btc_eth_generator (BTC/ETH). Caso 20-may 9:29:
        # un canal aliado publico "BUY ORO 4576 SL 4563" cuando ORO real estaba
        # a ~4490 (drift = 86 puntos = ~660% del SL de 13 puntos). El VIP vio
        # una senal absurda con prob 35% que toco -130 pips al instante.
        # Politica:
        #  - drift > PUBLISH_MAX_ENTRY_DRIFT_PCT_OF_SL del SL distance → SKIP
        #  - drift > PUBLISH_REANCHOR_DRIFT_PCT_OF_SL → re-anclar al precio real
        try:
            if _pp_entry > 0 and _pp_sl > 0:
                _sl_dist_pub = abs(_pp_entry - _pp_sl)
                if _sl_dist_pub > 0:
                    import price_feed as _mt5_drift
                    _sym_drift = signal.get("mt5_symbol") or _pp_pair
                    _real_px_pub = 0.0
                    try:
                        if _mt5_drift.initialize() and _sym_drift:
                            _tk_drift = _mt5_drift.symbol_info_tick(_sym_drift)
                            if _tk_drift:
                                _real_px_pub = (float(_tk_drift.ask) if _pp_dir == "BUY"
                                                else float(_tk_drift.bid))
                    except Exception:
                        _real_px_pub = 0.0
                    if _real_px_pub <= 0:
                        # Fallback: intentar _get_current_price si esta disponible
                        try:
                            _real_px_pub = float(_get_current_price(_pp_pair) or 0)
                        except Exception:
                            _real_px_pub = 0.0
                    if _real_px_pub > 0:
                        _drift_pub = abs(_real_px_pub - _pp_entry)
                        _drift_ratio = _drift_pub / _sl_dist_pub
                        _max_drift_skip = float(os.getenv("PUBLISH_MAX_ENTRY_DRIFT_PCT_OF_SL", "1.0"))
                        _max_drift_reanchor = float(os.getenv("PUBLISH_REANCHOR_DRIFT_PCT_OF_SL", "0.30"))
                        if _drift_ratio > _max_drift_skip:
                            log.warning(
                                f"🛡️ PUBLISH GUARD drift-skip: {_pp_dir} {_pp_pair} "
                                f"entry={_pp_entry} real={_real_px_pub:.5f} "
                                f"drift={_drift_pub:.5f} ({_drift_ratio*100:.0f}% del SL) "
                                f"> {_max_drift_skip*100:.0f}% — senal rota/stale, NO publicar al VIP"
                            )
                            signal["_pub_blocked"] = "drift-skip"
                            return
                        elif _drift_ratio > _max_drift_reanchor:
                            log.warning(
                                f"⚠️ PUBLISH GUARD drift-reanchor: {_pp_dir} {_pp_pair} "
                                f"entry teorica={_pp_entry} real={_real_px_pub:.5f} "
                                f"drift={_drift_pub:.5f} ({_drift_ratio*100:.0f}% del SL) — "
                                f"re-anclando entry al precio real antes de publicar"
                            )
                            # Re-anclar entry; SL/TP intactos. _pp_entry local NO se actualiza
                            # porque ya pasamos los guards anti-dup que dependen del valor original.
                            signal["entry"] = _real_px_pub
                            signal["_entry_reanchored"] = True
                            signal["_entry_original"] = _pp_entry
        except Exception as _e_drift_pub:
            log.debug(f"publish drift guard skip: {_e_drift_pub}")

        # Si pasa todos los guards, REGISTRAR los timestamps ahora para que
        # publicaciones inmediatas posteriores los vean.
        # FIX 2026-05-20: marcar signal como publicada OK para que exec
        # NO se bloquee por su propio pub_* recien registrado.
        signal["_pub_ok"] = True
        _recently_notified[_pp_spam_key] = _pp_now
        _recently_notified[_pp_dup_key] = _pp_now
        # FIX 2026-05-20: guardar entry del último publicado para bypass entry-deviation
        if _pp_entry > 0:
            _recently_notified[f"pub_entry_{_pp_pair}_{_pp_dir}"] = _pp_entry

        # FIX 2026-05-13: cap diario de senales publicadas por simbolo (DESACTIVADO por defecto).
        # Decision del usuario: prefiere que TODAS las senales >=60% probabilidad pasen al VIP,
        # sin tope por simbolo. Si un dia hay 15 senales buenas de ORO al >=60%, todas se publican.
        # Para reactivar: DAILY_SIGNAL_CAP_PER_SYMBOL=6 (o el numero que quieras) en .env.
        try:
            _daily_cap = int(os.getenv("DAILY_SIGNAL_CAP_PER_SYMBOL", "0"))
            if _daily_cap > 0:
                _cap_pair = (signal.get("pair") or "").upper()
                if _cap_pair and not _check_daily_symbol_cap(_cap_pair, _daily_cap):
                    log.warning(
                        f"🛡️ PUBLISH GUARD daily-cap: {_cap_pair} ya alcanzo cap {_daily_cap} "
                        f"senales hoy — NO publicar mas al VIP"
                    )
                    return
        except Exception as _e_cap:
            log.debug(f"daily cap check error: {_e_cap}")

    if signal["type"] == "update":
        # Notificar actualizaciones importantes al canal VIP
        _action = signal.get("action", "")
        _pair = signal.get("pair", "")
        _pair_d = _get_display_pair(_pair)

        # FIX 2026-05-05: Validar que el update viene del mismo canal que originó la señal.
        # Evita que "CLOSE our trade" de United Kings cierre señales de GoldForexMarket.
        # Solo aplica a acciones destructivas (full_close, sl_hit, tp_hit).
        if _action in ("full_close", "sl_hit", "tp_hit"):
            _upd_chat = signal.get("chat_title", "").lower()
            _open_source = None
            with _signals_lock:
                for _sid_chk, _sdata_chk in _open_signals.items():
                    _s_chk = _sdata_chk.get("signal", {})
                    if _s_chk.get("pair") == _pair or _s_chk.get("mt5_symbol") == _pair:
                        _open_source = str(_s_chk.get("source", "")).lower()
                        break
            if _open_source and _upd_chat:
                # Normalizar nombres de canal para comparación flexible
                _src_norm = _open_source.replace(" ", "").replace("signals", "").replace("signal", "")
                _chat_norm = _upd_chat.replace(" ", "").replace("signals", "").replace("signal", "")
                _fuente_coincide = (
                    _src_norm in _chat_norm or _chat_norm in _src_norm or
                    _open_source in _upd_chat or _upd_chat in _open_source
                )
                if not _fuente_coincide:
                    log.info(
                        f"⚠️ UPDATE '{_action}' de [{signal.get('chat_title')}] IGNORADO — "
                        f"señal {_pair} abierta por [{_open_source}], no coincide con el canal del update."
                    )
                    return
        # FIX 2026-04-14: Labels en español con pips dinámicos del canal aliado
        _pips = signal.get("pips_profit", 0)
        # FIX 2026-04-27: GOLD usa "pips" (alineado con aliados); indices siguen "pts"
        if _pair in ("GOLD", "XAUUSD", "XAUUSD=X") or _pair_d == "GOLD":
            _unit = "pips"
        elif _pair_d in ("US30", "NAS100", "S&P 500"):
            _unit = "pts"
        else:
            _unit = "pips"
        # Calcular pips desde _open_signals si no vienen del canal aliado.
        # FIX 2026-05-22 (Bug H): pips SIGNED (positivo si profit, negativo si loss)
        # segun direction. Antes usaba abs() → siempre "+" → caso 22-may 10:51
        # BUY ORO entry 4529.4 cerro a 4523.02 (LOSS) pero msg dijo "+64 pips in profit".
        _pips_signed = 0  # con signo
        if _pips <= 0:
            with _signals_lock:
                for _sid, _sdata in _open_signals.items():
                    _s = _sdata.get("signal", {})
                    if _s.get("pair") == _pair or _s.get("mt5_symbol") == _pair:
                        _entry_sig = _s.get("entry", 0)
                        _dir_sig = (_s.get("direction") or "").upper()
                        if _entry_sig > 0:
                            _live_p = _get_current_price(_pair)
                            if _live_p and _live_p > 0:
                                # diff CON SIGNO segun direction (positivo si gana)
                                if _dir_sig == "SELL":
                                    _signed_diff = _entry_sig - _live_p
                                else:  # BUY (default)
                                    _signed_diff = _live_p - _entry_sig
                                _raw_diff = abs(_signed_diff)
                                if _entry_sig >= 100:
                                    if _pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
                                        _pips = round(_raw_diff * 10, 1)
                                        _pips_signed = round(_signed_diff * 10, 1)
                                    else:
                                        _pips = round(_raw_diff, 1)
                                        _pips_signed = round(_signed_diff, 1)
                                elif "JPY" in _pair.upper():
                                    _pips = round(_raw_diff * 100)
                                    _pips_signed = round(_signed_diff * 100)
                                else:
                                    _pips = round(_raw_diff * 10000)
                                    _pips_signed = round(_signed_diff * 10000)
                        break
        else:
            # pips ya venian del canal aliado — asumimos positivo (aliados solo anuncian gains)
            _pips_signed = _pips
        # Texto con signo correcto. Si pips_signed positivo → "+X", negativo → "-X"
        if _pips_signed != 0:
            _sign = "+" if _pips_signed > 0 else ""
            _pips_txt = f"{_sign}{_pips_signed:g} {_unit}"
        elif _pips > 0:
            _pips_txt = f"+{_pips} {_unit}"
        else:
            _pips_txt = ""

        # FIX 2026-04-14: Detectar volatilidad real desde MT5 (spread + ATR)
        _es_volatil = False
        try:
            import price_feed as _mt5_vol
            _mt5_sym_vol = signal.get("mt5_symbol") or _pair
            if _mt5_vol.initialize():
                _tick_vol = _mt5_vol.symbol_info_tick(_mt5_sym_vol)
                _info_vol = _mt5_vol.symbol_info(_mt5_sym_vol)
                if _tick_vol and _info_vol:
                    _spread_actual = _tick_vol.ask - _tick_vol.bid
                    _spread_normal = _info_vol.spread_float if hasattr(_info_vol, 'spread_float') else _info_vol.spread * _info_vol.point
                    # Volátil si spread > 2x el normal
                    if _spread_normal > 0 and _spread_actual > _spread_normal * 2:
                        _es_volatil = True
                # También verificar ATR via últimas velas
                _rates_vol = _mt5_vol.copy_rates_from_pos(_mt5_sym_vol, _mt5_vol.TIMEFRAME_M15, 0, 20)
                if _rates_vol is not None and len(_rates_vol) >= 10:
                    _ranges = [float(r['high'] - r['low']) for r in _rates_vol]
                    _atr_reciente = sum(_ranges[-5:]) / 5  # ATR últimas 5 velas
                    _atr_previo = sum(_ranges[:10]) / 10    # ATR previas 10
                    if _atr_previo > 0 and _atr_reciente > _atr_previo * 1.5:
                        _es_volatil = True
        except Exception:
            pass  # Si falla, no pasa nada — usamos mensaje genérico

        # FIX 2026-04-28: traducido a INGLES — todos los mensajes al canal en EN
        if _es_volatil:
            _razon_half = "Volatile market — securing profit, closing half."
            _razon_partial = "Volatile market — securing profit, closing part."
        else:
            _razon_half = "Securing profit, closing half."
            _razon_partial = "Securing profit, closing part."

        # FIX 2026-04-17: tp_partial = TP intermedio → la posición sigue corriendo
        _tp_lvl = signal.get("tp_level", 0) if isinstance(signal, dict) else 0
        _tp_partial_msg = (
            f"🎯 *TP{_tp_lvl} REACHED* — {_pair_d}\n"
            f"✅ Level secured. Trade *still running* to next target."
        )
        # FIX 2026-04-28: traducido a INGLES — labels de updates al canal VIP
        # FIX 2026-05-22 (Bug H): full_close ahora detecta profit vs loss via _pips_signed.
        # Antes hardcoded "in profit" → caso 22-may 10:51 ORO cerro -€22 pero msg dijo
        # "+64 pips in profit" — mentira al cliente.
        _is_loss_close = _pips_signed < 0
        if _is_loss_close:
            _full_close_msg = f"🔒 *FULL CLOSE — {_pair_d}*\n⚠️ Closed at *{_pips_txt}* loss. Trade finalized."
        elif _pips_txt and _pips_signed > 0:
            _full_close_msg = f"🔒 *FULL CLOSE — {_pair_d}*\n✅ *{_pips_txt}* in profit. Trade finalized."
        else:
            _full_close_msg = f"🔒 *FULL CLOSE — {_pair_d}*\n✅ Closed at break-even. Trade finalized."
        if _pips_txt:
            _action_labels = {
                "close_half":       f"⚡ *PARTIAL CLOSE 50%* — {_pair_d}\n💰 *{_pips_txt}* secured. {_razon_half}",
                "close_partial":    f"⚡ *PARTIAL CLOSE* — {_pair_d}\n💰 *{_pips_txt}* secured. {_razon_partial}",
                "full_close":       _full_close_msg,
                "move_sl_to_entry": f"🛡️ *SL TO ENTRY* — {_pair_d}\n🔐 Trade protected. Risk eliminated.",
                "sl_hit":           f"🛑 *SL HIT* — {_pair_d}",
                "tp_hit":           f"✅ *TP REACHED* — {_pair_d}",
                "tp_partial":       _tp_partial_msg + (f"\n💰 *{_pips_txt}* at this level." if _pips_txt else ""),
            }
        else:
            _action_labels = {
                "close_half":       f"⚡ *PARTIAL CLOSE 50%* — {_pair_d}\n💰 {_razon_half}",
                "close_partial":    f"⚡ *PARTIAL CLOSE* — {_pair_d}\n💰 {_razon_partial}",
                "full_close":       _full_close_msg,
                "move_sl_to_entry": f"🛡️ *SL TO ENTRY* — {_pair_d}\n🔐 Trade protected. Risk eliminated.",
                "sl_hit":           f"🛑 *SL HIT* — {_pair_d}",
                "tp_hit":           f"✅ *TP REACHED* — {_pair_d}",
                "tp_partial":       _tp_partial_msg,
            }
        _msg = (_action_labels.get(_action) or "")
        # FIX 2026-05-11 (tarde): si FULL CLOSE llega <10min despues de PARTIAL CLOSE
        # para el mismo par, reemplazar con 1-liner. Evita doble mensaje "PARTIAL +85" +
        # "FULL CLOSE +130" llenando el canal con info repetida del mismo trade.
        # Caso 11-may 18:44+18:45: ORO partial +85 y full +130 = 2 mensajes seguidos.
        if _action == "full_close":
            _partial_recent_key = f"upd_partial_close_{_pair}"
            _partial_when = _recently_notified.get(_partial_recent_key, 0)
            if _partial_when and (time.time() - _partial_when) < 600:
                _msg = f"✅ *{_pair_d}* trade closed · final *{_pips_txt}*" if _pips_txt else f"✅ *{_pair_d}* trade closed"
        if _msg:
            _msg += ELI_SIG
        # FIX 2026-05-07: tp_hit y tp_partial NO envían el mensaje breve aquí —
        # _send_tp_celebration (más abajo) ya publica la celebración completa.
        # Enviar los dos causaba un duplicado: "✅ TP REACHED — ORO" + "TOQUE DE OBJETIVO".
        if _action in ("tp_hit", "tp_partial"):
            _msg = None
        if _msg:
            # FIX 2026-03-31: Solo publicar actualización si tenemos señal abierta para ese par
            # Evita reenviar "CERRAR MITAD — GBP/AUD" cuando BuySell365 nunca abrió esa operación
            _reply_id = None
            _tenemos_senal = False
            _open_sid = None
            _be_already = False
            with _signals_lock:
                for _sid, _sdata in _open_signals.items():
                    _s = _sdata.get("signal", {})
                    if _s.get("pair") == _pair or _s.get("mt5_symbol") == _pair:
                        _reply_id = _sdata.get("telegram_msg_id")
                        _open_sid = _sid
                        _be_already = bool(_sdata.get("_be_announced"))
                        _tenemos_senal = True
                        break
            if not _tenemos_senal:
                log.info(f"🔕 Update '{_action}' {_pair_d} ignorado — BuySell365 no tiene señal abierta para ese par")
                return None
            # FIX 2026-05-19: dedup BE per-signal. Caso 19-may ORO: 3 "SL TO ENTRY"
            # publicados a las 13:12 / 13:18 / 13:59 para el mismo trade — el cooldown
            # de 5min por par no alcanzaba. Ahora marcamos _be_announced en la senal
            # tras el primer aviso y bloqueamos repeticiones para la misma senal.
            if _action == "move_sl_to_entry" and _be_already:
                log.info(
                    f"🔕 SL TO ENTRY {_pair_d} ignorado — ya anunciado para esta senal "
                    f"(sid={_open_sid})"
                )
                return None
            # FIX 2026-05-21: para SL TO ENTRY, reconstruir el mensaje incluyendo
            # direction + entry de la senal que se va a proteger. Asi no es ambiguo
            # cuando hay multiples senales del mismo simbolo abiertas (caso 21-may
            # 14:33 — 2 ORO abiertas, mensaje generico no decia cual).
            if _action == "move_sl_to_entry" and _open_sid:
                try:
                    with _signals_lock:
                        _sd_be = _open_signals.get(_open_sid, {}) or {}
                        _sig_be = _sd_be.get("signal", {}) or {}
                    _dir_be = (_sig_be.get("direction") or "").upper()
                    _emo_be = "🟢" if _dir_be == "BUY" else "🔴"
                    _entry_be = _sig_be.get("mt5_entry") or _sig_be.get("entry") or 0
                    _entry_str_be = f" @ {fmt_price(_entry_be)}" if _entry_be else ""
                    if _dir_be:
                        _label_be = f"{_emo_be} {_dir_be} — {_pair_d}{_entry_str_be}"
                        _msg = (
                            f"🛡️ *SL TO ENTRY* — {_label_be}\n"
                            f"🔐 Trade protected. Risk eliminated."
                            f"{ELI_SIG}"
                        )
                except Exception as _e_be_label:
                    log.debug(f"BE label enrichment skipped: {_e_be_label}")
            # FIX 2026-05-11 (tarde-3): GHOST UPDATE GUARD — incluso si tenemos
            # senal en tracker, si MT5 ya NO tiene posicion abierta para ese par,
            # el update del aliado llego tarde (caso 19:33:20: US30 CLOSE HALF
            # despues que MT5 ya cerro en SL). No tiene sentido publicar al canal
            # "PARTIAL CLOSE +85 pips" cuando la posicion ya esta cerrada en SL.
            # Excepcion: tp_hit/sl_hit/tp_partial pasan (son notificaciones de
            # resolucion final, monitor decide).
            if _action in ("close_half", "close_partial", "full_close", "move_sl_to_entry"):
                try:
                    import price_feed as _mt5_gg
                    _resolved = None
                    try:
                        _resolved = _resolve_mt5_sym(_pair)
                    except Exception:
                        _resolved = _pair
                    # FIX 2026-05-20: Ghost guard por TICKET especifico, no por simbolo.
                    # Antes: positions_get(symbol="GOLD") devolvia True si CUALQUIER ORO
                    # estaba abierta, dejando pasar updates fantasma cuando MI senal ya
                    # cerro por SL pero otra ORO seguia viva. Caso 20-may 13:26/15:14:
                    # "+167 pips" y "+245 pips" celebrados despues de SL real a 12:29 EUR
                    # (ticket 774333997 BUY ORO 4482 -> 4473.53). positions_get(symbol)
                    # vio el SELL ORO 1:16 PM como "posicion viva" y dejo pasar todo.
                    _my_ticket = 0
                    if _open_sid:
                        with _signals_lock:
                            _sdat_gg = _open_signals.get(_open_sid, {}) or {}
                            _my_ticket = int(_sdat_gg.get("mt5_ticket") or
                                             _sdat_gg.get("_mt5_ticket") or
                                             (_sdat_gg.get("signal", {}) or {}).get("mt5_ticket") or
                                             (_sdat_gg.get("signal", {}) or {}).get("_mt5_ticket") or 0)
                    if _my_ticket and _mt5_gg.initialize():
                        _pos_check_t = _mt5_gg.positions_get(ticket=_my_ticket)
                        if _pos_check_t is not None and len(_pos_check_t) == 0:
                            log.info(
                                f"🔕 Update '{_action}' {_pair_d} ignorado — MT5 ticket={_my_ticket} "
                                f"YA CERRADO (update fantasma de senal cerrada; "
                                f"sid={_open_sid}); evita celebracion falsa al VIP"
                            )
                            return None
                    elif _resolved and _mt5_gg.initialize():
                        # Fallback: sin ticket conocido, chequear por simbolo (legacy)
                        _pos_check = _mt5_gg.positions_get(symbol=_resolved)
                        if _pos_check is not None and len(_pos_check) == 0:
                            log.info(
                                f"🔕 Update '{_action}' {_pair_d} ignorado — MT5 sin posicion abierta para {_resolved} "
                                f"(update llego tarde tras cierre real); evita mensaje fantasma al canal"
                            )
                            return None
                except Exception as _e_gg:
                    log.debug(f"Ghost update guard {_pair_d}: {_e_gg}")
            # FIX 2026-04-14: Deduplicar updates — cooldown 5 min por acción+par
            # FIX 2026-04-28: normalizar close_half/close_partial a la misma key.
            # Antes: el aliado mandaba "Close partial" + "Close half" en segundos
            # y los DOS pasaban (keys distintas) -> dos mensajes "PARTIAL CLOSE"
            # con pips distintos para el mismo trade (ej. NAS100 28/04 16:37).
            _action_dedup = "partial_close" if _action in ("close_half", "close_partial") else _action
            _upd_key = f"upd_{_action_dedup}_{_pair}"
            _upd_now = time.time()
            # FIX 2026-05-15: para PARTIAL CLOSE el cooldown corto (5min) era
            # insuficiente. Caso 15-may AUDCAD ticket 768363913: el aliado mando
            # 4 "close half" en 3h45min (04:00, 06:59, 07:13, 07:45) y los 4 pasaron
            # al VIP — diluye psicologicamente al cliente. Subimos a 60 min para
            # partial closes; otros updates siguen en 5 min.
            _upd_cd = 3600 if _action_dedup == "partial_close" else 300
            if _upd_key in _recently_notified and (_upd_now - _recently_notified[_upd_key]) < _upd_cd:
                log.info(
                    f"🔕 Update '{_action}' {_pair_d} ignorado — ya enviado hace "
                    f"{_upd_now - _recently_notified[_upd_key]:.0f}s (cooldown {_upd_cd}s)"
                )
                return None
            # FIX 2026-05-15: cap diario de PARTIAL CLOSE por par-direccion (default 2).
            # Mismo dia + mismo par + misma direccion → max 2 partials. Evita "4 partial 50%"
            # del mismo trade. Configurable via MAX_PARTIALS_PER_TRADE_DAY (default 2).
            if _action_dedup == "partial_close":
                try:
                    _max_partials = int(os.getenv("MAX_PARTIALS_PER_TRADE_DAY", "2"))
                except Exception:
                    _max_partials = 2
                _dir_pc = ""
                with _signals_lock:
                    for _sid_pc, _sd_pc in _open_signals.items():
                        _s_pc = _sd_pc.get("signal", {})
                        if _s_pc.get("pair") == _pair or _s_pc.get("mt5_symbol") == _pair:
                            _dir_pc = (_s_pc.get("direction") or "").upper()
                            break
                _today_pc = _today_andorra_str()
                _cap_key_pc = f"upd_partial_cap_{_today_pc}_{_pair}_{_dir_pc}"
                _prev_count = int(_recently_notified.get(_cap_key_pc, 0) or 0)
                if _prev_count >= _max_partials:
                    log.info(
                        f"🔕 PARTIAL CLOSE {_pair_d} {_dir_pc} cap alcanzado "
                        f"({_prev_count}/{_max_partials} hoy) — NO publicar mas partials"
                    )
                    return None
                # Reservar slot ahora (incrementar antes de publicar)
                _recently_notified[_cap_key_pc] = _prev_count + 1
            _recently_notified[_upd_key] = _upd_now
            # FIX 2026-05-11 (tarde-3): cooldown global por par como ultima linea
            # de defensa contra rafagas mixtas (PARTIAL + SL + close en segundos).
            if not _can_publish_to_vip(_pair_d or _pair, event=f"update_{_action}"):
                return None
            # FIX 2026-05-15: guard chokepoint para updates (PARTIAL CLOSE, FULL CLOSE, etc.)
            _msg = _safe_publish_vip(_msg, kind="update", pair=_pair_d or _pair) or _msg
            if not _msg or len(str(_msg).strip()) < 10:
                log.error(f"🚫 Update '{_action}' {_pair_d} abortado por guard (texto invalido)")
                return None
            try:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                _payload = {"chat_id": CHANNEL_ID, "text": _msg, "parse_mode": "Markdown"}
                if _reply_id:
                    _payload["reply_to_message_id"] = _reply_id
                _resp_upd = requests.post(url, json=_payload, timeout=10)
                # FIX: Si falla por reply_to inválido, reintentar sin reply
                if _resp_upd.status_code == 400 and "message to be replied" in _resp_upd.text:
                    _payload.pop("reply_to_message_id", None)
                    _resp_upd = requests.post(url, json=_payload, timeout=10)
                log.info(f"📢 Update notificado al canal: {_action} {_pair_d} (reply_to={_reply_id})")
                # FIX 2026-05-19: marcar _be_announced para no republicar BE en la misma senal
                if _action == "move_sl_to_entry" and _open_sid:
                    try:
                        with _signals_lock:
                            if _open_sid in _open_signals:
                                _open_signals[_open_sid]["_be_announced"] = True
                        _save_open_signals()
                    except Exception as _e_be_mark:
                        log.debug(f"BE-announced mark error: {_e_be_mark}")
                # ── WhatsApp personal: solo ORO (TODOS los eventos del canal) ──
                try:
                    if _action in ("move_sl_to_entry", "full_close", "sl_hit",
                                   "close_half", "close_partial",
                                   "tp_hit", "tp_partial"):
                        from whatsapp_notifier import (
                            notify_sl_moved as _wsp_sl_moved,
                            notify_position_closed as _wsp_closed,
                            notify_tp_hit as _wsp_tp_hit,
                            notify_partial_close as _wsp_partial,
                        )
                        _wsp_dir = ""
                        _wsp_entry = 0.0
                        _wsp_sl_old = 0.0
                        with _signals_lock:
                            for _xsid, _xsdata in _open_signals.items():
                                _xs = _xsdata.get("signal", {})
                                if _xs.get("pair") == _pair or _xs.get("mt5_symbol") == _pair:
                                    _wsp_dir = _xs.get("direction", "") or ""
                                    _wsp_entry = float(_xs.get("entry", 0) or 0)
                                    _wsp_sl_old = float(_xs.get("sl", 0) or 0)
                                    break
                        # FIX 2026-05-05: _pips ya en display-pips (Fix 1+2). Sin *10 extra.
                        _wsp_pips = _pips
                        if _action == "move_sl_to_entry":
                            _wsp_sl_moved(_pair, _wsp_sl_old, _wsp_entry, kind="BE")
                        elif _action == "full_close":
                            _wsp_closed(_pair, _wsp_dir, _wsp_entry, 0.0, _wsp_pips, reason="CIERRE")
                        elif _action == "sl_hit":
                            _wsp_closed(_pair, _wsp_dir, _wsp_entry, _wsp_sl_old, -abs(_wsp_pips or 0), reason="SL")
                        elif _action in ("close_half", "close_partial"):
                            _wsp_partial(_pair, _action, _wsp_pips)
                        elif _action in ("tp_hit", "tp_partial"):
                            _tp_lvl = signal.get("tp_level", 0) if isinstance(signal, dict) else 0
                            _wsp_tp_hit(_pair, _tp_lvl, _wsp_pips)
                except Exception as _e_wsp_upd:
                    log.debug(f"[WSP-GOLD] notify update error: {_e_wsp_upd}")
                # FIX 2026-04-15: Registrar cierre con pips en estadísticas persistentes
                # FIX 2026-04-16: Celebrar cierres con ganancia (video grupo + Instagram)
                if _action in ("close_half", "close_partial", "full_close") and _pips > 0:
                    _sig_dir = ""
                    _sig_src = ""
                    _sig_entry = 0
                    _sig_opened_at = 0
                    with _signals_lock:
                        for _sid, _sdata in _open_signals.items():
                            _s = _sdata.get("signal", {})
                            if _s.get("pair") == _pair or _s.get("mt5_symbol") == _pair:
                                _sig_dir = _s.get("direction", "")
                                _sig_src = _s.get("source", "")
                                _sig_entry = _s.get("entry", 0) or 0
                                # FIX 2026-05-05: pasar opened_at para que stats pueda
                                # vincular el cierre con la señal original (evita entry=0 huérfano)
                                _sig_opened_at = _sdata.get("opened_at", 0) or 0
                                break
                    _record_close_result(_pair, _action, _pips, direction=_sig_dir, source=_sig_src,
                                         entry=_sig_entry, opened_at=_sig_opened_at)
                    # FIX 2026-05-06: Marcar _cierres_previos en la señal abierta para que
                    # si luego llega un SL, el mensaje al canal recuerde que ya se indicó cerrar.
                    if _pips > 0 and _action in ("close_half", "close_partial", "full_close"):
                        with _signals_lock:
                            for _sid2, _sdata2 in _open_signals.items():
                                _s2 = _sdata2.get("signal", {})
                                if _s2.get("pair") == _pair or _s2.get("mt5_symbol") == _pair:
                                    _s2.setdefault("_cierres_previos", []).append({
                                        "type": _action,
                                        "pips": _pips,
                                        "ts": time.time()
                                    })
                                    log.info(f"📌 _cierres_previos marcado en señal {_pair}: +{_pips:.0f} pips ({_action})")
                                    break
                    # Celebrar cierre con ganancia → video al grupo + Instagram
                    try:
                        _send_close_celebration(
                            _pair, _sig_dir, _action, _pips,
                            entry=_sig_entry, source=_sig_src)
                    except Exception as _cel_err:
                        log.debug(f"Close celebration error: {_cel_err}")
                # FIX 2026-04-16: Celebrar TP HIT desde update del canal VIP (no solo texto plano)
                # FIX 2026-04-17: VERIFICAR MT5 — no celebrar TPs falsos (ops que nunca se ejecutaron)
                if _action == "tp_hit":
                    _tp_signal_data = None
                    _tp_reply_id = None
                    with _signals_lock:
                        for _sid, _sdata in _open_signals.items():
                            _s = _sdata.get("signal", {})
                            if _s.get("pair") == _pair or _s.get("mt5_symbol") == _pair:
                                _tp_signal_data = dict(_s)
                                _tp_reply_id = _sdata.get("telegram_msg_id")
                                break
                    # VERIFICACIÓN MT5: comprobar que la op cerró con profit real
                    _mt5_verified = False
                    _mt5_profit = 0.0
                    if _tp_signal_data:
                        try:
                            import price_feed as _mt5_check
                            from datetime import datetime as _dt_check, timedelta as _td_check
                            _ok_init, _ = _mt5_init_and_login()
                            if _ok_init:
                                _sym_check = _tp_signal_data.get("mt5_symbol") or _pair
                                _since = _dt_check.now() - _td_check(hours=72)
                                _deals = _mt5_check.history_deals_get(_since, _dt_check.now(), group=f"*{_sym_check}*")
                                if _deals:
                                    _our_deals = [d for d in _deals if d.magic == MAGIC_COPIER and d.profit != 0]
                                    _mt5_profit = sum(d.profit for d in _our_deals)
                                    _mt5_verified = _mt5_profit > 0
                        except Exception as _ver_err:
                            log.warning(f"MT5 verify error ({_pair}): {_ver_err}")

                    if _tp_signal_data and _mt5_verified:
                        # FIX 2026-04-29: dedup contra el otro path (_resolve_signals).
                        # Antes: monitor MT5 celebraba + update aliado celebraba lo mismo
                        # = TP HIT duplicado al canal VIP (visto 29-abr 3:12, 3:53, 4:35).
                        _tp_idx_chk = _tp_signal_data.get("_tp_idx", 0) + 1
                        _tp_dir_chk = _tp_signal_data.get("direction", "")
                        _dedup_key_tp = f"{_pair}_{_tp_dir_chk}_tp{_tp_idx_chk}"
                        _prev_tp = _recently_notified.get(_dedup_key_tp, 0)
                        if _prev_tp and (time.time() - _prev_tp) < 300:
                            log.info(f"🔕 TP HIT duplicado (otro path ya celebro hace {time.time()-_prev_tp:.0f}s): {_dedup_key_tp}")
                        else:
                            _recently_notified[_dedup_key_tp] = time.time()
                            try:
                                _send_tp_celebration(_tp_signal_data, reply_to_msg_id=_tp_reply_id)
                                _record_daily_result(_tp_signal_data, "tp")
                                log.info(f"🎯 TP celebrado desde update canal VIP: {_pair} (MT5 profit=${_mt5_profit:.2f} ✅)")
                            except Exception as _tp_upd_err:
                                log.debug(f"TP celebration from channel update error: {_tp_upd_err}")
                    elif _tp_signal_data and not _mt5_verified:
                        log.warning(f"🚫 TP celebración BLOQUEADA ({_pair}) — no se ejecutó en MT5 o profit≤0 (evita TP falso)")
                    else:
                        # FIX 2026-05-05: Fallback — intentar celebrar desde buffer de señales
                        # recién cerradas (ej: señal cerrada por United Kings pero TP llega
                        # luego de Gold Forex Market). TTL 2h.
                        _buf_entry = None
                        with _recently_closed_lock:
                            _b = _recently_closed_buffer.get(_pair)
                            if _b and (time.time() - _b.get("closed_at", 0)) < 7200:
                                _buf_entry = _b
                        if _buf_entry:
                            log.info(f"🎯 tp_hit GHOST celebración ({_pair}) — señal ya cerrada pero en buffer reciente")
                            try:
                                _ghost_sig = dict(_buf_entry["signal"])
                                _ghost_sig["_tp_idx"] = signal.get("tp_level", 1) or 1
                                _send_tp_celebration(_ghost_sig, reply_to_msg_id=_buf_entry.get("telegram_msg_id"))
                            except Exception as _ghost_err:
                                log.debug(f"Ghost TP celebration error: {_ghost_err}")
                        else:
                            log.info(f"🔕 tp_hit celebración skipped — no signal data for {_pair}")
                # FIX 2026-04-17: tp_partial (TP intermedio) → avanzar _tp_idx y registrar
                # en stats sin limpiar la señal. La posición MT5 sigue corriendo.
                if _action == "tp_partial":
                    _tp_lvl_adv = signal.get("tp_level", 0) if isinstance(signal, dict) else 0
                    _found_in_open = False
                    with _signals_lock:
                        for _sid_p, _sdata_p in _open_signals.items():
                            _s_p = _sdata_p.get("signal", {})
                            if _s_p.get("pair") == _pair or _s_p.get("mt5_symbol") == _pair:
                                if _tp_lvl_adv > 0:
                                    _s_p["_tp_idx"] = max(_s_p.get("_tp_idx", 0), _tp_lvl_adv)
                                _tps_ok = _s_p.setdefault("_tps_alcanzados", [])
                                _tps_ok.append({"nivel": _tp_lvl_adv, "pips": _pips})
                                _found_in_open = True
                                break
                    if not _found_in_open:
                        # FIX 2026-05-05: señal ya cerrada pero TP parcial llega del canal aliado
                        # → celebrar desde buffer reciente si existe
                        _buf_partial = None
                        with _recently_closed_lock:
                            _bp = _recently_closed_buffer.get(_pair)
                            if _bp and (time.time() - _bp.get("closed_at", 0)) < 7200:
                                _buf_partial = _bp
                        if _buf_partial:
                            log.info(f"🎯 tp_partial GHOST celebración ({_pair} TP{_tp_lvl_adv}) — señal en buffer reciente")
                            try:
                                _ghost_sig_p = dict(_buf_partial["signal"])
                                _ghost_sig_p["_tp_idx"] = _tp_lvl_adv
                                _send_tp_celebration(_ghost_sig_p, reply_to_msg_id=_buf_partial.get("telegram_msg_id"))
                            except Exception as _ghost_p_err:
                                log.debug(f"Ghost TP partial celebration error: {_ghost_p_err}")
                    _save_open_signals()
                    if _pips > 0:
                        _sig_dir_p = ""
                        _sig_src_p = ""
                        with _signals_lock:
                            for _sid_p, _sdata_p in _open_signals.items():
                                _s_p = _sdata_p.get("signal", {})
                                if _s_p.get("pair") == _pair or _s_p.get("mt5_symbol") == _pair:
                                    _sig_dir_p = _s_p.get("direction", "")
                                    _sig_src_p = _s_p.get("source", "")
                                    break
                        # Registrar como "tp" parcial para stats (pips alcanzados)
                        _record_close_result(_pair, "tp_partial_hit", _pips, direction=_sig_dir_p, source=_sig_src_p)
                # FIX 2026-04-15: Remover señal de tracking al recibir cierre definitivo
                # Previene: 1) TP duplicado de señal ya cerrada (Bug 3)
                #           2) Doble CIERRE TOTAL (Bug 4) — segundo full_close no encuentra señal
                if _action in ("full_close", "sl_hit", "tp_hit"):
                    with _signals_lock:
                        _to_remove = [s for s, sd in _open_signals.items()
                                      if sd.get("signal", {}).get("pair") == _pair
                                      or sd.get("signal", {}).get("mt5_symbol") == _pair]
                        for s in _to_remove:
                            # FIX 2026-04-29: limpiar _recently_sent al cerrar señal
                            # Así una nueva señal del mismo par con entrada cercana NO queda
                            # bloqueada por el dedup de 4h aunque la anterior ya se cerró.
                            _closed_sig = _open_signals[s].get("signal", {})
                            _closed_dir = _closed_sig.get("direction", "")
                            _closed_raw = (_closed_sig.get("pair", "") or "").upper().replace("/", "")
                            _closed_canon = SYMBOL_MAP.get(_closed_raw, _closed_raw)
                            _rs_prefix = f"{_closed_canon}_{_closed_dir}_"
                            _rs_to_clear = [k for k in _recently_sent if k.startswith(_rs_prefix)]
                            for _rk in _rs_to_clear:
                                _recently_sent.pop(_rk, None)
                            if _rs_to_clear:
                                log.info(f"🧹 _recently_sent limpiado ({_closed_canon} {_closed_dir}) tras {_action}: {_rs_to_clear}")
                            # FIX 2026-05-05: Guardar en buffer de recién cerradas (2h TTL)
                            # para que TPs posteriores del canal aliado puedan celebrarse.
                            with _recently_closed_lock:
                                _recently_closed_buffer[_pair] = {
                                    "signal": dict(_closed_sig),
                                    "closed_at": time.time(),
                                    "telegram_msg_id": _open_signals[s].get("telegram_msg_id"),
                                }
                            _open_signals.pop(s, None)
                            _resolved_signals.add(s)
                    if _to_remove:
                        _save_open_signals()
                        log.info(f"🗑️ {len(_to_remove)} señal(es) {_pair_d} removida(s) de tracking tras {_action}")
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
    source     = signal.get("source", "Unknown")
    order_type = signal.get("order_type", "Market")
    is_limit   = signal.get("is_limit", False)
    rrr        = signal.get("rrr", "")
    style      = signal.get("style", "")

    # FIX 2026-04-29: gate anti-mensajes-crudos. Antes, si parse_signal devolvia
    # un dict con direction/pair pero sin entry/sl/tp, el canal recibia una
    # publicacion vacia ("Entry: Market Price" + "SL: not set" + "(sin TPs)")
    # que parecia ruido. Ahora exigimos al menos UNA referencia de precio
    # (entry, sl, o tp). Si todo es 0, la senhal es demasiado pobre para ser
    # util al suscriptor — se descarta con log claro.
    _has_any_price = (entry > 0) or (sl > 0) or (tp > 0)
    if not _has_any_price:
        log.warning(
            f"🚫 send_to_channel: senhal SIN precios validos "
            f"({direction} {pair} entry={entry} sl={sl} tp={tp} src={source}) "
            f"— descartando publicacion (mensaje crudo/parcial)"
        )
        return None
    # Direccion y par OBLIGATORIOS — defensa extra (parse_signal ya valida).
    if not direction or direction not in ("BUY", "SELL") or not pair:
        log.warning(
            f"🚫 send_to_channel: direction='{direction}' pair='{pair}' invalidos "
            f"— descartando publicacion"
        )
        return None
    # FIX 2026-04-30: Canal VIP exige 📍🎯🛡️ exacto (feedback_canal_vip_normas.md).
    # Si SL o TP=0, NO publicar — produce mensaje incompleto (visto 30 abr 19:12 GBPJPY
    # y 19:18 NZDJPY — Vision extrajo entry pero no SL → usuario tuvo que borrar).
    # Entry=0 SI esta permitido (señal "Market Price"). SL y TP son obligatorios.
    # FIX 2026-04-30 (#7): smart TP/SL fallback con Sonnet — si entry+TP estan OK
    # pero SL=0 (caso narrative con chart), usar R:R 1:2 calculado matematicamente
    # antes de descartar. Asi recuperamos señales que antes se perdian.
    # FIX 2026-04-30 (smart-SL gate): el smart-SL puede generar un SL DEMASIADO
    # tight (caso GBP/JPY 30/04: tp_dist 18 pips → smart SL 9 pips, mercado se
    # movio +130 pips contra antes de bajar a TP, suscriptores confundidos).
    # Por defecto BLOQUEAR la publicacion cuando parser SL=0 (mas honesto).
    # Re-activar smart-SL con env SMART_SL_PUBLISH=true si se quiere coverage.
    _smart_sl_publish = os.getenv("SMART_SL_PUBLISH", "false").lower() in ("true", "1", "yes")
    if sl <= 0 and tp > 0 and entry > 0 and _smart_sl_publish:
        try:
            _tp_dist = abs(tp - entry)
            # SL distance = TP distance / 2 (R:R 1:2). Para SELL sumamos al entry,
            # para BUY restamos.
            if direction.upper() == "SELL":
                sl = round(entry + (_tp_dist / 2.0), 5)
            else:
                sl = round(entry - (_tp_dist / 2.0), 5)
            signal["sl"] = sl
            signal["_sl_auto_calculado"] = True
            log.info(
                f"🤖 Smart-SL calculado para {pair} {direction}: SL={sl} "
                f"(R:R 1:2 desde entry={entry}, tp={tp}) [SMART_SL_PUBLISH=true]"
            )
        except Exception as _e_sl:
            log.debug(f"Smart-SL calc falló: {_e_sl}")
            sl = signal.get("sl", 0) or 0

    if sl <= 0 or tp <= 0:
        # FIX 2026-04-30: log mas claro distinguiendo "sin SL del parser" vs "sin TP".
        # Caso GBP/JPY 30/04 que llego al canal con SL=0 sintetico: el smart-SL
        # esta off por defecto ahora, asi que descartamos en vez de publicar
        # un SL inventado que confunde al suscriptor cuando el precio se mueve.
        _missing = []
        if sl <= 0: _missing.append("SL")
        if tp <= 0: _missing.append("TP")
        log.warning(
            f"🚫 send_to_channel: senhal sin {'/'.join(_missing)} (sl={sl} tp={tp}) "
            f"— {direction} {pair} entry={entry} src={source} — NO publicando "
            f"(canal VIP requiere 📍🎯🛡️ completo). "
            f"Tip: si quieres re-activar smart-SL fallback, set SMART_SL_PUBLISH=true en .env"
        )
        return None

    # FIX 2026-04-14: Señales en español
    dir_label = "BUY" if direction.upper() == "BUY" else "SELL"
    dir_emoji = "🟢" if direction == "BUY" else "🔴"
    src_emoji = {
        "SureShotFX":         "📡",
        "GoldForexMarket":    "🥇",
        "TopTradingSignals":  "🎯",
        "UnitedKings":        "👑",
        "ProSignalsFx":       "🐻",
        "AnabelSignals":      "🌸",
    }.get(source, "🔔")

    pair_display = _get_display_pair(pair)

    # Format prices
    fmt = fmt_price

    # FIX 2026-04-29: si el aliado mando 2 entries de referencia (AnabelSignals
    # "BUY 4545 4541" o "BUY 4534 OR 4530"), mostrar ambos: "Entry: 4545 / 4541".
    entry2 = signal.get("entry2", 0) or 0
    if entry > 0 and entry2 > 0:
        entry_display = f"{fmt(entry)} / {fmt(entry2)}"
    elif entry > 0:
        entry_display = fmt(entry)
    else:
        entry_display = "Market Price"

    tp2 = signal.get("tp2", 0) or 0
    tp3 = signal.get("tp3", 0) or 0
    tp4 = signal.get("tp4", 0) or 0
    tp5 = signal.get("tp5", 0) or 0
    has_multi_tp = any(t > 0 for t in [tp2, tp3, tp4, tp5])
    tp_label = "TP1" if has_multi_tp else "TP"

    # FIX 2026-04-29: formato SIMPLE solicitado por el usuario para reducir
    # confusion de suscriptores. Solo: header asset + BUY/SELL, Entry, TPs, SL.
    # Removido: header LIMIT/STOP, "Wait entry at", "Current market", "Pending
    # order...", warning de Forex closed weekend. Si el usuario quiere de
    # vuelta esos detalles, son commits separados.
    # FIX 2026-05-07: Scalping badge en el header — visible inmediatamente
    _is_scalp = style and style.lower() in ("scalp", "scalping")
    _header_extra = "\n⚡ *SCALPING SIGNAL* ⚡\n" if _is_scalp else ""

    lines = [
        f"{dir_emoji} *{dir_label} — {pair_display}*{_header_extra}",
        f"",
        f"📍 Entry: {entry_display}",
    ]
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
    if sl > 0:
        lines.append(f"🛡️ SL: {fmt(sl)}")

    # FIX 2026-05-08: análisis híbrido pre-publicación (indicadores + Claude Vision).
    # NUNCA descarta. Solo añade línea de probabilidad. Si falla, no se muestra.
    # FIX 2026-05-11: persistir score en signal dict para que close handlers lo
    # graben en copier_stats.json → habilita auditoría WR-vs-probability.
    try:
        from signal_probability import compute_signal_probability, format_score_line
        # FIX 2026-05-18 P1.7: si la signal ya fue scoreada (probability_source
        # presente), NO llamar otra vez a Vision. Antes se gastaba ~$0.002 +
        # 5-20s de latencia por cada reconstruccion del mensaje (ediciones,
        # reintentos), duplicando coste API.
        if signal.get("probability_source") and signal.get("probability") is not None:
            _prob_result = {
                "available": True,
                "score": signal.get("probability"),
                "tech_score": signal.get("probability_tech"),
                "vision_score": signal.get("probability_vision"),
                "source": signal.get("probability_source"),
                "reasons": [],
                "error": None,
            }
            log.debug(f"signal_probability cached hit: {pair} {direction} score={_prob_result['score']}")
        else:
            _prob_result = compute_signal_probability(
                pair=pair_display, direction=direction,
                entry=entry, sl=sl, tp=tp,
                mt5_symbol=signal.get("mt5_symbol") or pair,
            )
        _score_line = format_score_line(_prob_result)
        # FIX 2026-05-13: guard de probabilidad minima para senales aliadas.
        # Antes el guard al inicio de send_to_channel veia signal["probability"]=None
        # porque el score se calcula AQUI dentro, despues. Resultado: 13-may publicamos
        # 22 senales aliadas con prob 23%, 26%, 30%, 32%, 35%... al VIP.
        # Ahora descartamos antes de construir el mensaje si score < MIN_PUBLISH_PROBABILITY.
        if _prob_result and _prob_result.get("available"):
            _calc_score = _prob_result.get("score")
            # FIX 2026-05-18: filtro DESACTIVADO via default "0" — todas las
            # senales pasan. Conservado para que MIN_PUBLISH_PROBABILITY env
            # var pueda reactivarlo si algun dia se quiere.
            try:
                _min_pub_calc = float(os.getenv("MIN_PUBLISH_PROBABILITY", "0"))
                if _calc_score is not None and float(_calc_score) < _min_pub_calc:
                    log.warning(
                        f"🛡️ PUBLISH GUARD prob<{_min_pub_calc:.0f}%: "
                        f"{direction} {pair} score={_calc_score} (aliada) — NO publicar al VIP"
                    )
                    return None
            except (TypeError, ValueError):
                pass
        if _score_line:
            lines.append(_score_line)
        # Persistencia del score para auditoría a futuro
        if _prob_result and _prob_result.get("available"):
            signal["probability"] = _prob_result.get("score")
            signal["probability_tech"] = _prob_result.get("tech_score")
            signal["probability_vision"] = _prob_result.get("vision_score")
            signal["probability_source"] = _prob_result.get("source")
    except Exception as _e_prob:
        log.debug(f"signal_probability error (no crítico): {_e_prob}")

    # FIX 2026-05-07: Firma Eli al final de cada señal
    lines.append(f"\n— _Eli · BuySell365 Pro_ 🤖")

    msg = "\n".join(lines)

    # FIX 2026-04-21: Señales sin botones — publicidad eliminada por petición del usuario

    # FIX 2026-05-12 P2.10: Rechazar mensajes vacios o muy cortos.
    # Caso 12-may 07:06: bot publico un mensaje completamente vacio al canal VIP
    # (solo el header del bot, sin contenido). Validacion minima de longitud para
    # detectar bugs de construccion de mensaje antes de postear.
    if not msg or len(msg.strip()) < 20:
        log.error(
            f"🚫 send_to_channel: mensaje vacio o muy corto (len={len(msg or '')}) "
            f"para {direction} {pair} — NO publicando"
        )
        return None

    # FIX 2026-05-15: guard chokepoint antes de cualquier envio al VIP
    msg = _safe_publish_vip(
        msg, kind="signal", pair=pair_display, direction=dir_label,
        probability=signal.get("probability"),
    )
    if not msg:
        return None

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    _payload = {
        "chat_id": CHANNEL_ID,
        "text": msg,
        "parse_mode": "Markdown",
    }

    # Retry hasta 3 veces con backoff
    for _intento in range(3):
        try:
            resp = requests.post(url, json=_payload, timeout=10)
            if resp.status_code == 200:
                log.info(f"📡 ENVIADO AL CANAL: {dir_label} {pair_display} ({source})")
                _canal_msg_id = resp.json().get("result", {}).get("message_id")
                # ── WhatsApp personal: mismo texto que canal VIP ──
                try:
                    from whatsapp_notifier import notify_raw as _wsp_raw
                    _wsp_raw(msg, pair=pair, event="new_signal",
                             probability=signal.get("probability"))
                except Exception as _e_wsp_new:
                    log.debug(f"[WSP] notify_raw error: {_e_wsp_new}")
                # FIX 2026-05-01: events_log append-only para auditoria/recovery
                # FIX 2026-05-06: incluir mt5_executed para que recovery LLM
                # distinga señales informativas (Forex) de posiciones reales.
                try:
                    from events_log import log_event as _log_event
                    _log_event("signal.published", source="copier", data={
                        "pair": pair, "direction": direction, "entry": signal.get("entry"),
                        "sl": signal.get("sl"), "tp": signal.get("tp"),
                        "msg_id": _canal_msg_id, "channel_source": source,
                        "mt5_executed": executed,  # False=publicada solo info (MT5 pendiente o no ejecuta este par)
                    })
                except Exception:
                    pass
                # Registrar TODAS las señales para seguimiento TP/SL
                # FIX 2026-04-09: Ya no se requiere SL>0 para registrar.
                # Si entry=0, intentar obtener precio live como entry.
                if (signal.get("entry") or 0) <= 0:
                    _live = _get_current_price(pair)
                    if _live and _live > 0:
                        signal["entry"] = round(_live, 5)
                        log.info(f"📍 Entry auto-asignado (live): {signal['entry']} para {pair}")
                    else:
                        log.error(f"❌ No se pudo obtener entry para {pair} — señal NO registrada para seguimiento TP/SL")
                        break  # No registrar señal incompleta

                # FIX 2026-04-10: Validar TPs DESPUÉS de tener entry real
                _e_val = signal.get("entry", 0) or 0
                _d_val = signal.get("direction", "")
                if _e_val > 0:
                    for _tk in ("tp", "tp2", "tp3", "tp4", "tp5"):
                        _tv = signal.get(_tk, 0) or 0
                        if _tv <= 0:
                            continue
                        # Sanity: >20% de diferencia = basura
                        if abs(_tv - _e_val) / _e_val > 0.20:
                            log.warning(f"🗑️ {_tk}={_tv} descartado (>20% de entry={_e_val})")
                            signal[_tk] = 0
                            continue
                        # Dirección: BUY→TP>entry, SELL→TP<entry
                        if _d_val == "BUY" and _tv < _e_val:
                            log.warning(f"🗑️ {_tk}={_tv} descartado (BUY pero TP < entry={_e_val})")
                            signal[_tk] = 0
                        elif _d_val == "SELL" and _tv > _e_val:
                            log.warning(f"🗑️ {_tk}={_tv} descartado (SELL pero TP > entry={_e_val})")
                            signal[_tk] = 0

                sig_id = f"{pair}_{int(time.time())}"
                with _signals_lock:
                    _open_signals[sig_id] = {
                        "signal": signal,
                        "sent_at": time.time(),
                        "telegram_msg_id": _canal_msg_id,
                    }
                    # FIX 2026-04-26: si MT5 rechazo por mercado cerrado, marcar
                    # como pendiente. _retry_pending_market_open_signals la
                    # reintentara cuando abra el mercado, y el reconcile NO la
                    # limpiara pasados los 120min.
                    if not executed and detail and detail.startswith("MARKET_CLOSED:"):
                        _open_signals[sig_id]["_pending_market_open"] = True
                        log.info(f"⏳ Señal {sig_id} PENDING — esperando apertura de mercado")
                log.info(f"🎯 Señal registrada para seguimiento: {sig_id} (msg_id={_canal_msg_id})")
                _save_open_signals()  # Persistir a disco

                # ── SEÑAL REGALO al grupo público (2/día: 1 oro + 1 otra) ──
                # FIX 2026-04-28: pasar source para filtro de confiabilidad
                try:
                    if GROUP_ID and _should_gift_signal(pair, source=signal.get("source", "")):
                        _gift_msg = _format_gift_message(signal)
                        _url_gift = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                        _pay_gift = {
                            "chat_id": GROUP_ID,
                            "text": _gift_msg,
                            "parse_mode": "Markdown",
                        }
                        _resp_gift = requests.post(_url_gift, json=_pay_gift, timeout=10)
                        if _resp_gift.status_code == 200:
                            log.info(f"🎁 SEÑAL REGALO enviada al grupo: {dir_label} {pair_display}")
                            # FIX 2026-05-04: tracker historico para resumen semanal
                            try:
                                _append_gift_history(
                                    pair=pair, direction=dir_label,
                                    source=signal.get("source", "?"),
                                    entry=float(signal.get("entry", 0) or 0),
                                )
                            except Exception as _e_gh_app:
                                log.debug(f"gift_history append err: {_e_gh_app}")
                            # Marcar en _open_signals para que el grupo reciba la celebración
                            # FIX 2026-05-05: guardar gifted_date para evitar que señales
                            # huérfanas de días anteriores disparen celebraciones hoy.
                            with _signals_lock:
                                if sig_id in _open_signals:
                                    _open_signals[sig_id]["gifted"] = True
                                    _open_signals[sig_id]["gifted_date"] = today_str
                            _save_open_signals()

                            # FIX 2026-04-28: publicar TEASER de la senal regalo en
                            # Instagram Story tambien — amplifica alcance del free
                            # signal y atrae trafico a la web/canal VIP. Best-effort:
                            # si IG falla por sesion/rate, no bloquea el copier.
                            if not _ig_in_circuit_breaker():
                                try:
                                    from instagram_poster import post_vip_signal_teaser_story
                                    _ig_ok = post_vip_signal_teaser_story(
                                        pair=pair,
                                        direction=dir_label,
                                        nivel="FREE TODAY",
                                    )
                                    if _ig_ok:
                                        log.info(f"📸 IG Story (gift teaser) publicada: {dir_label} {pair_display}")
                                        _mark_ig_post_sent("gift_teaser")
                                    else:
                                        log.debug(f"📸 IG gift teaser skip — sesion/cooldown")
                                except Exception as _e_ig_gift:
                                    _eis = str(_e_ig_gift).lower()
                                    if "feedback_required" in _eis or "spam" in _eis:
                                        _trigger_ig_circuit_breaker()
                                    log.debug(f"IG gift teaser error: {_e_ig_gift}")
                            else:
                                log.info(f"📸 IG gift teaser skip ({pair_display}) — circuit breaker activo")
                        else:
                            log.warning(f"🎁 Error enviando regalo: {_resp_gift.status_code}")
                except Exception as _eg:
                    log.debug(f"🎁 Gift signal error: {_eg}")

                # ── REGISTRO EN estado.json DESACTIVADO (FIX 2026-04-09) ──
                # Causa: doble monitor (copier + bot.py) generaba SL HIT duplicados
                # con valores de SL diferentes (yfinance vs MT5). Solo el copier
                # monitorea señales copiadas ahora.

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


# === LOOP PROACTIVO DE REGALOS (2/día garantizados) ===
async def _loop_gift_proactive():
    """Garantiza 2 regalos/día al grupo público aunque no lleguen señales nuevas a la hora exacta.

    FIX 2026-05-18: Antes el regalo dependía de que una señal nueva llegara justo cuando
    current_minute >= gift_target. Si no llegaba señal en ese minuto, el regalo no salía.
    Ahora este loop corre cada 10 min y, cuando pasa el target, elige aleatoriamente
    una señal activa de _open_signals (preferencia: fuentes confiables >=60% WR).
    Formato idéntico al _format_gift_message() ya definido.
    """
    import requests as _req_gift
    import random as _rand_gift
    from datetime import datetime as _dt_gift
    import pytz as _pytz_gift

    _tz_gift = _pytz_gift.timezone("Europe/Andorra")

    while True:
        try:
            await asyncio.sleep(600)  # cada 10 minutos

            _now_g = _dt_gift.now(_tz_gift)
            _today_g = _now_g.strftime("%Y-%m-%d")
            _cur_min_g = _now_g.hour * 60 + _now_g.minute
            _weekday_g = _now_g.weekday()

            # Sin regalos fines de semana (mercado cerrado)
            if _weekday_g >= 5:
                continue

            # Solo actuar en horario de regalo (8h-18h30)
            if not (480 <= _cur_min_g <= 1110):
                continue

            if not GROUP_ID:
                continue

            # --- Init/reset tracker para hoy si hace falta ---
            with _gift_lock:
                if _gift_tracker.get("date") != _today_g:
                    _t_morn = _rand_gift.randint(480, 720)   # 8:00-12:00
                    _t_aftn = _rand_gift.randint(780, 1080)  # 13:00-18:00
                    _gift_tracker.update({
                        "date": _today_g,
                        "gift_targets": [_t_morn, _t_aftn],
                        "gifts_count": 0,
                        "gold_gifted": False, "other_gifted": False,
                        "gold_pair": None, "other_pair": None,
                        "gold_result": None, "other_result": None,
                    })
                    _save_gift_tracker()
                    log.info(
                        f"🎁 [loop] Targets del día: "
                        f"#1={_t_morn//60:02d}:{_t_morn%60:02d}  "
                        f"#2={_t_aftn//60:02d}:{_t_aftn%60:02d}"
                    )

                _gc = _gift_tracker.get("gifts_count", 0)
                _targets = _gift_tracker.get("gift_targets") or []

                if _gc >= 2 or len(_targets) < 2:
                    continue

                _next_tgt = _targets[_gc]
                if _cur_min_g < _next_tgt:
                    continue  # aún no es hora

                # Reservar el slot (adelantar contador para evitar doble-envío)
                _gift_tracker["gifts_count"] = _gc + 1
                _save_gift_tracker()

            # --- Buscar señal candidata en _open_signals ---
            with _signals_lock:
                _open_list_g = list(_open_signals.items())

            _pool_reliable = []
            _pool_any = []
            for _sid_g, _sdata_g in _open_list_g:
                _sig_g = _sdata_g.get("signal", {})
                if _sdata_g.get("gifted"):
                    continue
                if (_sig_g.get("entry") or 0) <= 0 or (_sig_g.get("sl") or 0) <= 0:
                    continue
                _src_g = _sig_g.get("source", "")
                _rel_g, _, _ = _get_source_reliability(_src_g)
                if _rel_g:
                    _pool_reliable.append((_sid_g, _sdata_g, _sig_g))
                else:
                    _pool_any.append((_sid_g, _sdata_g, _sig_g))

            _pool_g = _pool_reliable if _pool_reliable else _pool_any

            if not _pool_g:
                log.info(f"🎁 [loop] Sin señales activas para regalo #{_gc + 1} — reintentando en 10 min")
                # Revertir contador — probar de nuevo en el siguiente ciclo
                with _gift_lock:
                    _gift_tracker["gifts_count"] = _gc
                    _save_gift_tracker()
                continue

            _chosen_sid, _chosen_sdata, _chosen_sig = _rand_gift.choice(_pool_g)
            _pair_g = _chosen_sig.get("pair", "")
            _dir_g = _chosen_sig.get("direction", "?")
            _src_g = _chosen_sig.get("source", "?")

            _gift_txt = _format_gift_message(_chosen_sig)
            _resp_g = _req_gift.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": GROUP_ID, "text": _gift_txt, "parse_mode": "Markdown"},
                timeout=10,
            )

            if _resp_g.status_code == 200:
                log.info(
                    f"🎁 [loop] REGALO #{_gc + 1} enviado al grupo: "
                    f"{_dir_g} {_get_display_pair(_pair_g)} ({_src_g})"
                )
                _is_gold_g = _pair_g.upper() in ("GOLD", "XAUUSD", "XAUUSD=X")
                with _gift_lock:
                    if _is_gold_g and not _gift_tracker.get("gold_gifted"):
                        _gift_tracker.update({"gold_gifted": True, "gold_pair": _pair_g, "gold_result": None})
                    elif not _is_gold_g and not _gift_tracker.get("other_gifted"):
                        _gift_tracker.update({"other_gifted": True, "other_pair": _pair_g, "other_result": None})
                    _save_gift_tracker()
                with _signals_lock:
                    if _chosen_sid in _open_signals:
                        _open_signals[_chosen_sid]["gifted"] = True
                        _open_signals[_chosen_sid]["gifted_date"] = _today_g
                _save_open_signals()
                try:
                    _append_gift_history(
                        pair=_pair_g, direction=_dir_g,
                        source=_src_g, entry=float(_chosen_sig.get("entry", 0) or 0),
                    )
                except Exception:
                    pass
            else:
                log.warning(
                    f"🎁 [loop] Error enviando regalo #{_gc + 1}: "
                    f"{_resp_g.status_code} {_resp_g.text[:80]}"
                )
                # Revertir contador
                with _gift_lock:
                    _gift_tracker["gifts_count"] = _gc
                    _save_gift_tracker()

        except Exception as _e_gloop:
            log.warning(f"🎁 [loop] Excepción en gift loop: {_e_gloop}")


# === MAIN USERBOT ===
async def main():
    from telethon import TelegramClient, events

    client = TelegramClient(SESSION_FILE, API_ID, API_HASH)

    # Canales VIP monitoreados — IDs verificados con Telethon
    ALLOWED_CHANNEL_IDS = {
        -1001422000261,   # Sureshot FX VIP
        -1001661400724,   # SureShot GOLD (VIP)
        -1001700795303,   # Sureshot INDICES (VIP)
    }

    # Canales públicos por username (se resuelven al arrancar)
    # TODAS las señales pasan — sin filtro por activo
    PUBLIC_CHANNELS_USERNAMES = [
        "Anabelsignals08",            # AnabelSignals — XAUUSD/Gold
        "Jerry77446",                 # GOLD FOREX MARKET — XAUUSD/Gold señales VIP
        "top_tradingsignals",         # TopTradingSignals — Forex + Gold + Indexes (agregado 2026-04-19)
        "topforexsignals",            # TopTradingSignals alias (por si falla el primero)
        "unitedkings1",               # United Kings Signals — XAUUSD/Gold commentary (agregado 2026-04-19)
        "prosignalsfxx",              # ProSignalsFx — Gold + Forex diario (agregado 2026-04-19)
    ]
    # FIX 2026-04-26: ALLOWED_PAIRS eliminado — era dead code (nunca referenciado).
    # Confirmado en sesion 25-abr y reporte de auditoria 2026-04-26.

    # Resolver usernames → se hace DESPUÉS de client.start() (ver más abajo)
    _username_to_id = {}

    # Keywords para auto-descubrir canales de señales
    AUTO_DISCOVER_KEYWORDS = [
        "sureshot",
        "anabelsignals", "anabel signals", "forex signals", "forexsignals",
        "vip signals", "signal vip",
        "gold forex market", "gold forex",  # GOLD FOREX MARKET (@Jerry77446)
        "top_tradingsignals", "toptradingsignals", "top trading signals",  # TopTradingSignals (2026-04-19)
        "unitedkings", "united kings",                                     # United Kings (2026-04-19)
        "prosignalsfx", "pro signals fx",                                  # ProSignalsFx (2026-04-19)
        # FIX 2026-05-09: keywords crypto para cobertura fin de semana (BTC/ETH 24/7)
        "bitcoin bullets", "bitcoinbullets",
        "evening trader", "eveningtrader", "evening_trader",
        "coincodecap", "coin code cap",
        "altsignals", "alt signals",
        "binance signals", "binancekillers", "binance killers",
        "bybit signals", "bybitsignals",
        "crypto signals", "cryptosignals",
        "btc signals", "btcsignals", "bitcoin signals", "bitcoinsignals",
        "wolf of trading", "wolfoftrading",
        "fat pig signals", "fatpigsignals",
        "fed russian insiders", "fedrussian",
        "onward btc", "onwardbtc",
        "wallstreet queen", "wallstreetqueen",
        "crypto inner circle", "cryptoinnercircle",
        "nación crypto", "nacion crypto", "nacioncrypto",
        "crypto whale", "cryptowhale",
    ]
    SIGNAL_KEYWORDS = ["sureshot", "anabel", "gold forex",
                       "toptradingsignals", "unitedkings", "prosignalsfx"]

    # FIX 2026-05-06: chats= filter REMOVED — list(ALLOWED_CHANNEL_IDS) was a snapshot
    # taken at registration time, BEFORE public channels (AnabelSignals, GoldForexMarket,
    # NasdaqMasters, etc.) are resolved and added at line ~8668. That snapshot only had
    # the 3 SureShotFX VIP IDs, so Telethon silently dropped ALL public channel messages.
    # The inside double-check at line 8080 already correctly filters by ALLOWED_CHANNEL_IDS,
    # which by then is fully populated. Removing the outer filter here is safe.
    @client.on(events.NewMessage())
    async def handler(event):
        """Process every new message from allowed signal channels."""
        try:
            text = event.raw_text or ""

            # FIX 2026-04-29: Si el mensaje trae imagen adjunta y LLM_VISION_ENABLED,
            # descargarla para que el LLM pueda leerla. Algunos aliados envian
            # screenshots de charts con la senal (entry/SL/TP en la imagen).
            _image_bytes = None
            try:
                _vision_enabled = os.getenv("LLM_VISION_ENABLED", "true").lower() in ("true", "1", "yes")
                _llm_active = os.getenv("LLM_PARSER_ENABLED", "false").lower() in ("true", "1", "yes")
                if _vision_enabled and _llm_active and event.photo:
                    # Descargar imagen a memoria (no a disco)
                    _img_buf = await event.download_media(file=bytes)
                    if _img_buf and len(_img_buf) < 5_000_000:  # max 5MB
                        _image_bytes = _img_buf
                        log.info(f"🖼️ Imagen adjunta descargada ({len(_img_buf)} bytes) — pasando al LLM Vision")
            except Exception as _e_img:
                log.debug(f"Vision download error (no bloqueante): {_e_img}")

            # Si no hay texto Y no hay imagen, descartar (evita ruido)
            if (not text or len(text) < 10) and not _image_bytes:
                return

            # Get chat info
            chat = await event.get_chat()
            chat_title = getattr(chat, 'title', 'Unknown')
            chat_id = event.chat_id

            # FIX 2026-05-11: HARD BLOCK — nunca procesar mensajes de nuestros propios
            # canales (VIP y grupo público). Previene feedback loops donde nuestras
            # propias celebraciones (TP HIT, SL, PARTIAL CLOSE) sean re-procesadas
            # como señales de aliados. El bloqueo es ANTES del log para no contaminar
            # los registros con ruido de nuestras propias publicaciones.
            _own_vip_id = CHANNEL_ID  # int, ej: -1003729609114
            _is_own_channel = (
                chat_id == _own_vip_id or
                chat_id == -_own_vip_id or
                "buysell365" in (chat_title or "").lower() or
                "buysell 365" in (chat_title or "").lower()
            )
            if _is_own_channel:
                return  # Nuestro propio canal/grupo — ignorar siempre

            log.info(f"📡 Mensaje recibido de [{chat_title}]: {(text or '[image only]')[:80]}")

            # Double check channel ID (positive or negative)
            # FIX 2026-05-06: también aceptar canales cuyo título coincida con
            # SIGNAL_KEYWORDS como fallback, por si get_entity() falló al resolver
            # su ID durante el startup y el canal no quedó en ALLOWED_CHANNEL_IDS.
            _title_lower = (chat_title or "").lower()
            _title_is_signal = any(kw in _title_lower for kw in SIGNAL_KEYWORDS)
            _id_allowed = (chat_id in ALLOWED_CHANNEL_IDS or -chat_id in ALLOWED_CHANNEL_IDS)
            if not _id_allowed and not _title_is_signal:
                return
            # Si pasó por título (ID desconocido), agregar ahora para futuras msgs
            if not _id_allowed and _title_is_signal:
                _cid_to_add = int(f"-100{chat_id}") if chat_id > 0 else chat_id
                ALLOWED_CHANNEL_IDS.add(_cid_to_add)
                log.info(f"📡 FALLBACK: canal con título reconocido agregado al vuelo: {chat_title} (ID: {_cid_to_add})")

            # Detectar username del canal para filtros
            _chan_uname = _username_to_id.get(chat_id, _username_to_id.get(-chat_id, ""))

            # Parse the signal (texto + imagen opcional)
            signal = parse_signal(text or "", chat_title=chat_title, image_bytes=_image_bytes)

            # FIX 2026-04-09: TODAS las señales pasan — sin filtro por activo
            # Updates (close_half, sl_hit, tp_hit) se filtran más abajo por _open_signals
            if not signal:
                return

            # FIX 2026-05-01: blacklist de fuentes via env var COPIER_SOURCES_DISABLED.
            # Lista de canales a IGNORAR completamente (tanto new_signal como updates).
            # Ejemplo .env: COPIER_SOURCES_DISABLED=ProSignalsFx,TopTradingSignals
            _blacklist_raw = os.getenv("COPIER_SOURCES_DISABLED", "").strip()
            if _blacklist_raw:
                _blacklist = {s.strip().lower() for s in _blacklist_raw.split(",") if s.strip()}
                _sig_src = (signal.get("source", "") or "").lower()
                _chat_title_low = (chat.title or "").lower()
                # Match contra el source extraido O contra el chat_title (cubre todos los casos)
                if any(b in _sig_src or b in _chat_title_low for b in _blacklist):
                    log.info(f"⏭️ Senal de fuente blacklisted ignorada: source='{signal.get('source','?')}' chat='{chat.title}'")
                    return

            # FIX 2026-05-08: Blacklist de PARES perdedores (analisis historico mostro
            # WR <50% y net pips negativo). Estos pares NO se copian a MT5 NI se publican
            # al canal VIP — para evitar danar la calidad del producto.
            # Pares descartados (basado en 30dias de data):
            #   USDJPY 0% WR, GBPCAD 25%, GBPAUD 0%, GBPNZD 0%, EURCAD 33%,
            #   GBPCHF 0%, NZDCAD 0%, EURUSD 0%, EURAUD 0%
            # Configurable por env var COPIER_PAIRS_DISABLED (csv, override del default).
            _pair_blacklist_default = "USDJPY,GBPCAD,GBPAUD,GBPNZD,EURCAD,GBPCHF,NZDCAD,EURUSD,EURAUD"
            _pair_blacklist_raw = os.getenv("COPIER_PAIRS_DISABLED", _pair_blacklist_default).strip()
            if _pair_blacklist_raw:
                _pair_blacklist = {p.strip().upper().replace("/", "") for p in _pair_blacklist_raw.split(",") if p.strip()}
                _sig_pair = (signal.get("pair", "") or "").upper().replace("/", "")
                _sig_pair_canon = SYMBOL_MAP.get(_sig_pair, _sig_pair)
                if _sig_pair in _pair_blacklist or _sig_pair_canon in _pair_blacklist:
                    log.info(f"⏭️ Senal de PAR blacklisted ignorada: pair='{_sig_pair}' canon='{_sig_pair_canon}' (no copia, no publica)")
                    return

            log.info(f"📡 SEÑAL DETECTADA en [{chat.title}]: {signal.get('direction', signal.get('action', '?'))} {signal['pair']}")

            # ── Deduplicación: evitar misma señal de CUALQUIER canal ──
            # FIX 2026-04-21b: TODOS los checks + pre-registro dentro del mismo lock
            # FIX 2026-04-21c: (1) normalizar pair canónico (GOLD/XAUUSD/ORO → mismo)
            # y (2) tolerancia en entry para cubrir diferencias de cotizacion entre canales.
            # FIX 2026-04-29: tolerancia reducida 0.3% -> 0.05%. Antes bloqueaba multi-entry
            # legitimo (ej. SureShot mando BUY @4587, @4599, @4593 — el dedup viejo con 14pts
            # de tolerancia las trataba como duplicadas). Ahora 0.05% (~2pts oro, ~1pip forex)
            # solo bloquea verdaderos duplicados de cotizacion entre canales espejo.
            # Tambien REMOVIDO Check 4 "ya hay abierta 60min" — bloqueaba multi-entry strategy.
            # FIX 2026-04-30: política ZERO BLOQUEOS — quitado cooldown canal 5min
            # (rechazaba SureShot 06:45 SELL 4575 → cancel → 06:48 SELL 4547 distinta).
            # Ventana dedup canónico+tol reducida 4h → 30min: mismo entry tras 30min se
            # considera estrategia legítima, no espejo.
            if signal["type"] == "new_signal":
                _raw_pair = signal.get("pair", "").upper().replace("/", "")
                # Canonical: mapea a símbolo MT5 (GOLD/XAUUSD/ORO → "GOLD")
                _pair_canon = SYMBOL_MAP.get(_raw_pair, _raw_pair)
                _direction = signal.get("direction", "")
                _entry_val = signal.get("entry", 0) or 0
                _entry_round = round(_entry_val, 2)
                # Tolerancia adaptativa REDUCIDA: 0.05% del entry (~2pts oro a 4600)
                _tol = abs(_entry_val) * 0.0005 if _entry_val > 0 else 0
                _dedup_key = f"{_pair_canon}_{_direction}_{_entry_round}"
                _pair_key = f"{_pair_canon}_{_direction}"

                def _entry_match(e1, e2, tol):
                    """Dos entries se consideran la misma señal si difieren < tol."""
                    if tol <= 0:
                        return round(e1, 2) == round(e2, 2)
                    return abs(e1 - e2) <= tol

                with _signals_lock:
                    # FIX 2026-05-07: POLÍTICA "PUBLICAR TODO" — usuario requiere que
                    # TODAS las señales de canales aliados se publiquen sin excepción,
                    # incluso si el entry es similar a una señal ya abierta o enviada.
                    # Check 1 (dedup 30min) y Check 4 (señal abierta misma entrada)
                    # ELIMINADOS. Solo se registra para limpiar _recently_sent al cerrar.
                    _now_t = time.time()
                    # PRE-REGISTRAR — registro para limpieza al cerrar (no bloquea)
                    # FIX 2026-05-18 P1.8: lock para writes concurrentes
                    with _recently_sent_lock:
                        _recently_sent[_dedup_key] = _now_t
                        _recently_sent[_pair_key] = _now_t               # registro global

            # ══════════════════════════════════════════════════════════════
            # 🟢 MT5 EXECUTION ACTIVADO — Cuenta DEMO (1301348583 / XMGlobal-MT5 6)
            # Autorizado por el usuario 2026-05-02 (refresh: lote risk-based 1%)
            # Cada señal del canal VIP se replica con lotaje dinámico (RISK_PER_TRADE_PCT)
            # Para desactivar: COPIER_MT5_ENABLED=false en .env
            # ══════════════════════════════════════════════════════════════
            # FIX 2026-04-21 (M3): ahora por .env — default False por seguridad
            MT5_EXECUTION_ENABLED = os.getenv("COPIER_MT5_ENABLED", "True").lower() in ("true", "1", "yes")

            if signal["type"] == "new_signal":
                # Registrar msg_id para manejar ediciones futuras
                msg_id = event.message.id

                # FIX 2026-05-08: Anti-duplicado contra el handler de EDIT.
                # Telegram entrega a veces el mismo mensaje primero como EDIT y
                # después como NEW (orden inverso al lógico). Si el EDIT ya lo
                # publicó, _published_msg_ids tiene el msg_id → no republicar.
                # Caso real: ProSignalsFx XAGUSD 8/5 06:09:49 EDIT → publica;
                # 06:10:06 NEW del mismo msg → publicaba de nuevo (duplicado VIP).
                if msg_id and msg_id in _published_msg_ids:
                    log.info(f"⏭️ Senal NEW ignorada: msg_id={msg_id} ya publicado por EDIT handler [{chat_title}]")
                    return

                # FIX 2026-04-16 BUG#1: TODAS las validaciones ANTES de execute_in_mt5
                # Antes: execute → validar entry → validar SL → validar TPs → si falla: return (posición huérfana)
                # Ahora: validar entry → validar SL → validar TPs → si todo OK → execute

                # PASO 1: Obtener entry real (si no viene en la señal)
                if signal.get("entry", 0) == 0:
                    # Sin precio de entrada → buscar precio actual (yfinance/TwelveData → MT5)
                    _live = _get_current_price(signal.get("pair", ""))
                    if not _live or _live <= 0:
                        # Fallback: precio MT5 directo
                        try:
                            import price_feed as _mt5
                            _mt5_sym = signal.get("mt5_symbol") or signal.get("pair", "")
                            if _mt5.terminal_info() is not None or _mt5.initialize():
                                _tick = _mt5.symbol_info_tick(_mt5_sym)
                                if _tick:
                                    _live = (_tick.ask + _tick.bid) / 2
                        except Exception:
                            pass
                    if _live and _live > 0:
                        signal["entry"] = round(_live, 5 if _live < 100 else 2)
                        log.info(f"📍 Sin entry en señal — usando precio actual: {signal['entry']}")
                    else:
                        # FIX 2026-04-15: No publicar señal sin entry — esperar edición del canal
                        log.warning(f"⚠️ Sin entry y sin precio disponible — NO publicando (esperamos edición del canal)")
                        return

                # PASO 2: SL vs entry — NUNCA descartar, solo limpiar valor invalido
                # FIX 2026-04-24: Politica del usuario: TODA senal al canal VIP TAMBIEN
                # se ejecuta en MT5. El usuario ajusta SL/TP manualmente despues.
                # Si SL contradice direccion → setear SL=0 (MT5 ejecuta sin SL)
                # NO auto-flip (respetamos la direccion declarada por el aliado).
                #
                # FIX 2026-04-30: ANTES de limpiar SL=0, intentar auto-corregir SL
                # invertido (typo). Caso AnabelSignals 30-abr 09:12: BUY 4591 SL 4678
                # — evidente typo (5→6). Construimos SL coherente con TPs y publicamos
                # con SL real en vez de 0. Si la auto-corrección no aplica, caemos al
                # comportamiento anterior (SL=0).
                _e = signal.get("entry", 0)
                _s = signal.get("sl", 0)
                _d = signal.get("direction", "")
                if _e > 0 and _s > 0:
                    _autofixed = _auto_fix_inverted_sl(signal)
                    if not _autofixed:
                        # Auto-fix no aplicó (no había TP1 + condiciones edge): limpiar SL=0
                        if _d == "SELL" and _s < _e:
                            log.warning(f"⚠️ SELL con SL({_s}) < entry({_e}) — limpiando SL=0 (usuario ajusta manual)")
                            signal["sl"] = 0
                        elif _d == "BUY" and _s > _e:
                            log.warning(f"⚠️ BUY con SL({_s}) > entry({_e}) — limpiando SL=0 (usuario ajusta manual)")
                            signal["sl"] = 0

                # PASO 3: Validar TPs — NUNCA descartar, solo limpiar valores invalidos.
                # FIX 2026-04-24: Politica usuario: TODA senal se ejecuta en MT5.
                # Si todos los TPs son invalidos, ejecutamos sin TP (tp=0). MT5 acepta
                # ordenes sin TP. Usuario ajusta manualmente despues.
                if _e > 0:
                    for _tp_key in ("tp", "tp2", "tp3", "tp4", "tp5"):
                        _tv = signal.get(_tp_key, 0) or 0
                        if _tv <= 0:
                            continue
                        # Dirección: BUY → TP debe ser > entry | SELL → TP debe ser < entry
                        _tp_wrong_dir = False
                        if _d == "BUY" and _tv < _e and abs(_tv - _e) > 0.001:
                            _tp_wrong_dir = True
                        elif _d == "SELL" and _tv > _e and abs(_tv - _e) > 0.001:
                            _tp_wrong_dir = True
                        # Rango: > 20% de diferencia = basura (ej: TP2=200 para GOLD a 4700)
                        _pct = abs(_tv - _e) / _e
                        _tp_out_range = _pct > 0.20
                        if _tp_wrong_dir or _tp_out_range:
                            _reason = f"dirección invertida ({_d} pero TP {'<' if _tv < _e else '>'} entry)" if _tp_wrong_dir else f"fuera de rango ({_pct:.0%})"
                            log.warning(f"⚠️ {_tp_key}={_tv} inválido vs entry={_e} — {_reason} — limpiando")
                            signal[_tp_key] = 0
                    # FIX 2026-04-24: NO descartar aunque todos los TPs sean invalidos.
                    # Se ejecuta a mercado sin TP (usuario lo pone a mano).

                # PASO 4: Validar entry vs precio actual
                # FIX 2026-05-02: ZERO bloqueos — feedback_zero_bloqueos_mt5.md.
                # NUNCA perdemos una senal: auto-corregir y publicar siempre.
                # _validate_entry_vs_market() del worktree merge marca is_limit=True
                # cuando es BUY/SELL LIMIT legitimo (entry razonable cerca del mercado).
                # Si es stale FUERA de tolerancia (manipulacion/edit muy viejo), seguimos
                # publicando pero a MERCADO con TPs ajustados. La proteccion contra
                # celebraciones fantasma se hace mas abajo en _advance_tp_idx_to_unrebased.
                _is_stale_entry = not _validate_entry_vs_market(signal)
                if _is_stale_entry:
                    log.info(
                        f"⚠️ {signal.get('pair','?')} entry stale — publicando a MERCADO "
                        f"(monitor avanzara _tp_idx para no celebrar TPs ya rebasados)"
                    )

                # FIX 2026-04-29: PRE-TRADE SAFETY CHECK con Sonnet 4.6
                # FIX 2026-05-01: default ahora "true" (era "false") + bloqueo aplica
                # tambien a la publicacion al canal, no solo a MT5. La feature ELITE
                # estaba dormida en produccion porque exigia MT5 activo Y env opt-in.
                # Antes de ejecutar/publicar, Sonnet evalua la senhal en contexto:
                # - SL muy ajustado vs ATR del par
                # - Sobreexposicion (3+ posiciones del mismo par/correlacion)
                # - Direccion vs setup tecnico
                # Solo BLOQUEA si Sonnet detecta riesgo claro (block=true en JSON).
                _pretrade_block = False
                _pretrade_reason = ""
                if os.getenv("LLM_PRETRADE_CHECK", "true").lower() in ("true", "1", "yes"):
                    try:
                        from llm_features import pretrade_safety_check
                        _open_pos_pre = []
                        with _signals_lock:
                            for _sid_pre, _sd_pre in list(_open_signals.items())[:10]:
                                _s_pre = _sd_pre.get("signal", {})
                                _open_pos_pre.append({
                                    "pair": _s_pre.get("pair", ""),
                                    "direction": _s_pre.get("direction", ""),
                                })
                        _live_pre = _get_current_price(signal.get("pair", "")) or 0
                        _pretrade = pretrade_safety_check(signal, _open_pos_pre, _live_pre, atr=0)
                        if _pretrade and _pretrade.get("block"):
                            _pretrade_block = True
                            _pretrade_reason = _pretrade.get("reason", "LLM bloqueo (sin razon)")
                            log.warning(
                                f"🚫 PRE-TRADE BLOCK por LLM: {signal.get('pair','?')} "
                                f"{signal.get('direction','?')} — {_pretrade_reason}"
                            )
                    except Exception as _e_pre:
                        log.debug(f"Pre-trade check skipped: {_e_pre}")

                # FIX 2026-05-06: ORDEN INVERTIDO — Canal VIP PRIMERO, MT5 DESPUÉS.
                # Bug anterior: MT5 ejecutaba en línea 8351 y luego publicaba en canal.
                # Si algo fallaba entre ambas líneas (LLM block en active mode, error de red,
                # bot.py caído), MT5 tenía la orden pero el canal VIP nunca la veía.
                # Resultado: trades "fantasma" en MT5 sin publicación en VIP.
                # Solución: publicar al canal VIP PRIMERO con executed=False provisional,
                # luego ejecutar en MT5. Si MT5 falla, al menos el canal ya tiene la señal.

                # Anti-celebracion fantasma: avanzar _tp_idx para que el monitor no
                # dispare TPs ya rebasados al momento de publicar (caso AnabelSignals
                # 06:23 que disparo 5 TP HITs falsos en 2 minutos).
                _advance_tp_idx_to_unrebased(signal)

                # FIX 2026-05-01: PRE-PUBLISH SECOND OPINION (Sonnet 4.6) — pilar LLM
                # En modo SHADOW por defecto: solo loguea lo que el LLM diria.
                # Cambiar LLM_PREPUBLISH_MODE=active en .env tras 7 dias de shadow OK
                # para que actue (block + skip publicacion).
                _prepub_mode = os.getenv("LLM_PREPUBLISH_MODE", "shadow").lower()
                if _prepub_mode in ("shadow", "active"):
                    try:
                        from llm_features import prepublish_second_opinion
                        _live_pp = _get_current_price(signal.get("pair", "")) or 0
                        _pp_result = prepublish_second_opinion(signal, current_price=_live_pp)
                        if _pp_result:
                            _pub_ok = _pp_result.get("publish", True)
                            _conf = _pp_result.get("confidence", 0)
                            _reason = _pp_result.get("reason", "")
                            _concerns = _pp_result.get("concerns", [])
                            if not _pub_ok and _prepub_mode == "active":
                                log.warning(
                                    f"🚫 PRE-PUBLISH BLOCK (LLM active mode): {signal.get('pair')} "
                                    f"{signal.get('direction')} — {_reason} (concerns: {_concerns})"
                                )
                                try:
                                    from events_log import log_event as _log_event
                                    _log_event("signal.prepublish_blocked", source="copier",
                                              data={"pair": signal.get("pair"), "reason": _reason,
                                                    "concerns": _concerns})
                                except Exception:
                                    pass
                                return  # NO publicar ni ejecutar en MT5
                            elif not _pub_ok and _prepub_mode == "shadow":
                                log.info(
                                    f"🌑 SHADOW pre-publish would BLOCK: {signal.get('pair')} "
                                    f"{signal.get('direction')} — {_reason} (NO se aplica, modo shadow)"
                                )
                                try:
                                    from events_log import log_event as _log_event
                                    _log_event("signal.prepublish_shadow_block", source="copier",
                                              data={"pair": signal.get("pair"), "reason": _reason,
                                                    "concerns": _concerns, "confidence": _conf})
                                except Exception:
                                    pass
                            else:
                                log.info(
                                    f"✅ PRE-PUBLISH OK ({_conf}%): {signal.get('pair')} "
                                    f"{signal.get('direction')} — {_reason}"
                                )
                    except Exception as _e_pp:
                        log.debug(f"Pre-publish check skipped: {_e_pp}")

                # FIX 2026-05-19 (Fix B): si el precio actual ya paso TP1, marcar dead.
                # FIX 2026-05-22 (Bug F): per regla feedback_publicar_todas_sin_filtro
                # publicar igual al VIP — cliente paga para ver TODAS. Pero set
                # _pub_blocked para que MT5 no ejecute. Antes (19-may) descartabamos
                # silenciosamente — caso 22-may 09:44 GOLD Anabel: entry 4520 TP 4522,
                # precio 4523 → bot no publico, cliente no vio nada. Ahora SI ve la
                # senal con su nota informativa.
                try:
                    _dead_pair = signal.get("pair", "")
                    _dead_dir = (signal.get("direction") or "").upper()
                    _dead_entry = float(signal.get("entry") or 0)
                    _dead_tp1 = float(signal.get("tp") or 0)
                    if _dead_pair and _dead_dir in ("BUY", "SELL") and _dead_tp1 > 0:
                        _dead_live = _get_current_price(_dead_pair) or 0
                        if _dead_live > 0:
                            _dead_passed = (
                                (_dead_dir == "BUY" and _dead_live >= _dead_tp1) or
                                (_dead_dir == "SELL" and _dead_live <= _dead_tp1)
                            )
                            if _dead_passed:
                                log.warning(
                                    f"⚠️ SEÑAL MUERTA — publicar al VIP info-only: {_dead_pair} {_dead_dir} "
                                    f"entry={_dead_entry} TP1={_dead_tp1} pero precio actual {_dead_live} "
                                    f"ya en zona profit. MT5 NO ejecutara (pub_blocked=dead)."
                                )
                                signal["_pub_blocked"] = "dead-signal"
                                # NO return — publicar al VIP, _pub_blocked impide MT5
                except Exception as _e_dead:
                    log.debug(f"Dead-signal pre-publish check error: {_e_dead}")

                # PASO 5a: PUBLICAR AL CANAL VIP PRIMERO (executed=False provisional)
                # Garantiza que el canal siempre recibe la señal antes que MT5.
                send_to_channel(signal, False, "Pendiente ejecución MT5")

                # PASO 5b: Ejecutar en MT5 DESPUÉS de publicar al canal
                executed, detail = False, "Ejecución MT5 desactivada (kill-switch activo)"
                if _pretrade_block:
                    executed, detail = False, f"BLOQUEADO PRE-TRADE: {_pretrade_reason}"
                elif MT5_EXECUTION_ENABLED:
                    aprobar, ia_comment = _ia_evaluar_senal(signal)
                    signal["ia_comment"] = ia_comment
                    if ia_comment:
                        log.info(f"🤖 IA: {ia_comment}")
                    if _is_stale_entry:
                        # Forzar market order: copiar señal con entry=0 para evitar mutar el dict
                        _signal_market = dict(signal)
                        _signal_market["entry"] = 0.0
                        executed, detail = execute_in_mt5(_signal_market)
                    else:
                        executed, detail = execute_in_mt5(signal)
                    log.info(f"📡 MT5: {'✅' if executed else '❌'} {detail}")
                # FIX 2026-05-06: Registrar resultado MT5 para recovery LLM.
                # Permite distinguir señales ejecutadas (orphan real si no hay cierre)
                # de señales no ejecutadas (Forex info-only → nunca orphan).
                try:
                    from events_log import log_event as _log_event_mt5r
                    _log_event_mt5r("signal.mt5_result", source="copier", data={
                        "pair": signal.get("pair"), "direction": signal.get("direction"),
                        "executed": executed, "detail": detail,
                    })
                except Exception:
                    pass
                # FIX 2026-04-09: Registrar en cache anti-duplicados + cooldown por par
                # FIX 2026-04-16: cooldown incluye canal (no bloquear canales distintos)
                # FIX 2026-05-18 P1.8: bloque write-cleanup protegido por lock
                _entry_r = round(signal.get("entry", 0), 2)
                _dk = f"{signal['pair']}_{signal['direction']}_{_entry_r}"
                _pk = f"{signal['pair']}_{signal['direction']}"
                _pk_ch = f"{_pk}_{chat.title}"
                with _recently_sent_lock:
                    _recently_sent[_dk] = time.time()
                    _recently_sent[_pk_ch] = time.time()  # Cooldown por par+canal
                    # Limpiar entradas viejas (>5h) para no acumular memoria
                    # FIX 2026-04-16: dict.update() no borra claves → limpiar correctamente
                    # FIX 2026-04-21: subido a 5h porque dedup _dk ahora dura 4h
                    _now = time.time()
                    _stale_sent = [k for k, v in _recently_sent.items() if _now - v >= 18000]
                    for _sk in _stale_sent:
                        _recently_sent.pop(_sk, None)
                if msg_id:
                    _published_msg_ids.add(msg_id)

            elif signal["type"] == "update":
                # ── Updates: publicar TP HIT, SL HIT y CLOSE HALF al canal VIP ──
                log.info(f"📡 UPDATE recibido: {signal.get('action','?')} {signal['pair']}")
                executed, detail = False, ""
                send_to_channel(signal, executed, detail)
                # MT5 execution (si se reactiva en el futuro)
                if MT5_EXECUTION_ENABLED:
                    executed, detail = handle_update_mt5(signal)
                    log.info(f"📡 UPDATE MT5: {'✅' if executed else '❌'} {detail}")

        except Exception as e:
            log.exception(f"Error processing message: {e}")

    # ══════════════════════════════════════════════════════════════
    # Handler de mensajes EDITADOS — captura cuando el canal aliado
    # edita el mensaje para agregar el precio de entrada real
    # Ejemplo: SureShot envía "XAUUSD BUY" sin precio, luego edita
    # para agregar "XAUUSD BUY 4458.22". Este handler lo captura.
    # ══════════════════════════════════════════════════════════════
    # FIX 2026-05-06: same snapshot bug as NewMessage handler above — removed chats= filter.
    # Inside handler already re-checks ALLOWED_CHANNEL_IDS (populated after this registration).
    @client.on(events.MessageEdited())
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
            chat_id = event.chat_id

            # FIX 2026-05-11: HARD BLOCK — mismo guard que NewMessage handler.
            # Rechazar ediciones de nuestros propios canales antes de procesar.
            _is_own_ch = (
                chat_id == CHANNEL_ID or chat_id == -CHANNEL_ID or
                "buysell365" in (chat_title or "").lower() or
                "buysell 365" in (chat_title or "").lower()
            )
            if _is_own_ch:
                return

            # FIX 2026-05-06: gate idéntico al NewMessage handler — filtrar por ALLOWED_CHANNEL_IDS
            # (la versión con chats= ya fue removida del decorador; este check es el reemplazo)
            if chat_id not in ALLOWED_CHANNEL_IDS and -chat_id not in ALLOWED_CHANNEL_IDS:
                return

            # Log SIEMPRE para trazabilidad — facilita debug cuando parse falla silenciosamente
            log.info(f"✏️ EDIT recibido de [{chat_title}] msg_id={msg_id}: {text[:70].replace(chr(10), ' ')}")

            # FIX 2026-05-01: descartar EDIT de mensajes muy viejos (>1h despues
            # del original). Hoy 06:23 AnabelSignals edito msg_id=1052 (un mensaje
            # original con OTRO contenido) a "BUY 4545" cuando el oro estaba en
            # 4621 — manipulacion clasica de canal fraudulento. El sistema lo
            # capturo como senhal nueva, lo publico al canal VIP y disparo 5 TP HIT
            # falsos en 2 minutos. Edit legitimo del autor: minutos. Edit
            # manipulador: horas/dias despues.
            try:
                _orig_date = getattr(event.message, "date", None)
                _edit_date = getattr(event.message, "edit_date", None)
                if _orig_date and _edit_date:
                    _edit_age_min = (_edit_date - _orig_date).total_seconds() / 60.0
                    if _edit_age_min > 60:
                        log.warning(
                            f"🚫 EDIT IGNORADO — msg_id={msg_id} editado {_edit_age_min:.0f}min "
                            f"despues del original. Probable manipulacion de canal aliado "
                            f"[{chat_title}]. Texto: {text[:80]}"
                        )
                        return
            except Exception as _e_age:
                log.debug(f"No se pudo medir edad del edit msg_id={msg_id}: {_e_age}")

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
            if not signal:
                return

            # FIX 2026-04-22: Si el EDIT es un UPDATE (close, TP hit, SL hit, etc.)
            # procesarlo igual que en el handler principal. Antes solo miraba
            # señales nuevas y perdía "Let's CLOSE our trade now" editados.
            if signal.get("type") == "update":
                log.info(f"📡 UPDATE (via edit) recibido: {signal.get('action','?')} {signal['pair']}")
                try:
                    send_to_channel(signal, False, "")
                except Exception as _e_send:
                    log.error(f"Error enviando update edit: {_e_send}")
                MT5_EXECUTION_ENABLED = os.getenv("COPIER_MT5_ENABLED", "True").lower() in ("true", "1", "yes")
                if MT5_EXECUTION_ENABLED:
                    try:
                        ok, det = handle_update_mt5(signal)
                        log.info(f"📡 UPDATE MT5 (edit): {'✅' if ok else '❌'} {det}")
                    except Exception as _e_mt5:
                        log.error(f"Error MT5 update edit: {_e_mt5}")
                return

            if signal.get("type") != "new_signal":
                return  # No es señal nueva completa — ignorar

            # FIX 2026-05-08: aplicar tambien en EDIT handler la blacklist de pares
            # perdedores (mismo filtro que NewMessage para que no se cuelen via edicion)
            _pair_blacklist_default_e = "USDJPY,GBPCAD,GBPAUD,GBPNZD,EURCAD,GBPCHF,NZDCAD,EURUSD,EURAUD"
            _pair_blacklist_raw_e = os.getenv("COPIER_PAIRS_DISABLED", _pair_blacklist_default_e).strip()
            if _pair_blacklist_raw_e:
                _pair_blacklist_e = {p.strip().upper().replace("/", "") for p in _pair_blacklist_raw_e.split(",") if p.strip()}
                _sig_pair_e = (signal.get("pair", "") or "").upper().replace("/", "")
                _sig_pair_canon_e = SYMBOL_MAP.get(_sig_pair_e, _sig_pair_e)
                if _sig_pair_e in _pair_blacklist_e or _sig_pair_canon_e in _pair_blacklist_e:
                    log.info(f"⏭️ EDIT senal de PAR blacklisted ignorada: pair='{_sig_pair_e}'")
                    return

            # FIX 2026-04-09: TODAS las señales pasan — sin filtro por activo

            # ── Deduplicación: no publicar si ya existe señal abierta del mismo par+dirección ──
            # FIX 2026-04-11: Verificar SOLO par+dirección (sin entry), ya que entry puede ser 0
            # y se asigna live después. Esto causaba duplicados BTC desde 2 canales.
            _sig_pair = signal.get("pair", "")
            _sig_dir  = signal.get("direction", "")
            with _signals_lock:
                for _sid, _sdata in _open_signals.items():
                    _s = _sdata.get("signal", {})
                    if (_s.get("pair", "") == _sig_pair and
                        _s.get("direction", "") == _sig_dir and
                        (time.time() - _sdata.get("sent_at", 0)) < 3600):
                        log.info(f"✏️ Edit ignorado — ya hay {_sig_pair}_{_sig_dir} abierta (señal {_sid})")
                        _published_msg_ids.add(msg_id)
                        return
            # También verificar en _recently_sent (cooldown 10min)
            # FIX 2026-05-18 P1.8: read protegido por lock para evitar race con writes
            _pair_cooldown_key = f"{_sig_pair}_{_sig_dir}"
            with _recently_sent_lock:
                _prev_time = _recently_sent.get(_pair_cooldown_key, 0)
            if _prev_time and (time.time() - _prev_time) < 600:
                log.info(f"✏️ Edit ignorado — cooldown activo: {_pair_cooldown_key} (hace {time.time()-_prev_time:.0f}s)")
                _published_msg_ids.add(msg_id)
                return

            log.info(f"✏️ Señal capturada vía edición (msg_id={msg_id}): {signal.get('direction')} {signal.get('pair')}")
            # FIX 2026-04-22: Sin filtro de stale — publicar siempre (usuario quiere sin excepciones)
            _validate_entry_vs_market(signal)  # solo para marcar _rejected_stale, no para bloquear
            # FIX 2026-04-15: No publicar si entry=0 (sin precio)
            if (signal.get("entry", 0) or 0) <= 0:
                log.warning(f"✏️ Edit sin entry resuelto — NO publicando {signal.get('pair','?')}")
                return
            # FIX 2026-04-16 BUG#2: edit_handler ahora ejecuta en MT5 igual que el handler principal
            # FIX 2026-04-21 (M3): ahora por .env
            MT5_EXECUTION_ENABLED = os.getenv("COPIER_MT5_ENABLED", "True").lower() in ("true", "1", "yes")
            executed, detail = False, "Ejecución MT5 desactivada (kill-switch activo)"
            if MT5_EXECUTION_ENABLED:
                _aprobar, _ia_comment = _ia_evaluar_senal(signal)
                signal["ia_comment"] = _ia_comment
                if _ia_comment:
                    log.info(f"🤖 IA (edit): {_ia_comment}")
                executed, detail = execute_in_mt5(signal)
                log.info(f"✏️ MT5 (edit): {'✅' if executed else '❌'} {detail}")
            send_to_channel(signal, executed, detail)
            # FIX 2026-04-07: Registrar en cache anti-duplicados
            # FIX 2026-05-18 P1.8: write protegido por lock
            _entry_r = round(signal.get("entry", 0), 2)
            _dk = f"{signal['pair']}_{signal['direction']}_{_entry_r}"
            with _recently_sent_lock:
                _recently_sent[_dk] = time.time()
            _published_msg_ids.add(msg_id)

        except Exception as e:
            log.exception(f"Error en edit_handler: {e}")

    log.info("📡 Signal Copier iniciando...")
    log.info(f"📡 API ID: {API_ID}")
    log.info(f"📡 Phone: {PHONE}")

    # FIX 2026-04-28c: HEARTBEAT FILE — senal de vida fiable. Escribe timestamp
    # cada 20s en .copier.heartbeat. El watchdog del launcher usa esto para
    # decidir vivo/muerto, en lugar de solo .copier.lock (frágil en arranque
    # cuando aun no se ha escrito el lock).
    try:
        from heartbeat import start_heartbeat
        _hb_path = str(Path(__file__).parent / ".copier.heartbeat")
        start_heartbeat(_hb_path, interval=20)
        log.info(f"💓 Heartbeat iniciado: {_hb_path}")
    except Exception as _e_hb:
        log.warning(f"Heartbeat no disponible: {_e_hb}")

    # Cargar señales abiertas de la sesión anterior (sobreviven reinicios)
    _load_open_signals()
    # FIX 2026-05-01: cargar _resolved_signals persistido — evita recelebracion
    # tras reinicio (signal cerrada justo antes del crash que aun esta en JSON).
    _load_resolved_signals()
    # FIX 2026-05-01: arrancar HEALTH-CHECK HTTP local en port 5557
    try:
        import sys as _sys_hc
        _base_hc = os.path.dirname(os.path.abspath(__file__))
        if _base_hc not in _sys_hc.path:
            _sys_hc.path.insert(0, _base_hc)
        from health_check import start_health_server, PORT_COPIER
        _COPIER_START_TS = time.time()
        def _copier_state():
            try:
                _hb_p = os.path.join(_base_hc, ".copier.heartbeat")
                _hb_age = int(time.time() - os.path.getmtime(_hb_p)) if os.path.exists(_hb_p) else 999
            except Exception:
                _hb_age = 999
            return {
                "service": "signal_copier.py",
                "pid": os.getpid(),
                "healthy": _hb_age < 60 and len(_open_signals) < 100,
                "uptime_sec": int(time.time() - _COPIER_START_TS),
                "stats": {
                    "open_signals_count": len(_open_signals),
                    "resolved_signals_count": len(_resolved_signals),
                    "heartbeat_age_sec": _hb_age,
                },
            }
        if start_health_server(PORT_COPIER, _copier_state):
            log.info(f"💉 Health-check HTTP arrancado en localhost:{PORT_COPIER}/health")
    except Exception as _e_hc:
        log.warning(f"Health-check no arranco: {_e_hc}")
    # FIX 2026-04-17: Cargar gift_tracker para no regalar múltiples oros por reinicio
    _load_gift_tracker()

    # FIX 2026-04-15: Restaurar estadísticas del día actual desde disco
    _today_stats = _load_copier_stats_today()
    if _today_stats:
        with _daily_results_lock:
            _daily_results.extend(_today_stats)
        log.info(f"📊 {len(_today_stats)} resultados de hoy restaurados desde copier_stats.json")

    # FIX 2026-04-23: Sync inicial de copier_stats.json → Render (web pública)
    # Así la landing muestra el historial completo desde el arranque.
    try:
        _sync_copier_stats_to_web()
        log.info("📡 copier_stats.json sincronizado con la web al arranque")
    except Exception as _e_init_sync:
        log.debug(f"Sync inicial copier_stats → web falló: {_e_init_sync}")

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
    # FIX 2026-05-06: get_messages(limit=1) después de get_entity fuerza a Telethon
    # a registrar el pts del canal y solicitar GetChannelDifference en tiempo real.
    # Sin esto, Telegram no envía updates del canal aunque el handler no tenga filtro.
    for _uname in PUBLIC_CHANNELS_USERNAMES:
        try:
            _entity = await client.get_entity(_uname)
            _cid = _entity.id
            _cid_neg = int(f"-100{_cid}") if _cid > 0 else _cid
            ALLOWED_CHANNEL_IDS.add(_cid_neg)
            _username_to_id[_cid_neg] = _uname
            log.info(f"✅ Canal público registrado: @{_uname} → {_cid_neg}")
            # FIX: forzar pts tracking — sin esto Telegram no envía updates en tiempo real
            try:
                await client.get_messages(_entity, limit=1)
                log.info(f"📡 pts tracking activado: @{_uname}")
            except Exception as _pts_e:
                log.debug(f"pts touch error @{_uname}: {_pts_e}")
        except Exception as _e:
            log.warning(f"⚠️ No se pudo resolver @{_uname}: {_e}")

    # Auto-discover TODOS los canales de señales conocidos
    async for dialog in client.iter_dialogs():
        title_lower = (dialog.title or "").lower()
        _es_canal = hasattr(dialog.entity, 'broadcast') or hasattr(dialog.entity, 'megagroup')
        _match = any(kw in title_lower for kw in AUTO_DISCOVER_KEYWORDS)
        _raw_id = dialog.id
        _norm_id = int(f"-100{_raw_id}") if _raw_id > 0 else _raw_id
        _already = _norm_id in ALLOWED_CHANNEL_IDS or _raw_id in ALLOWED_CHANNEL_IDS
        # FIX 2026-05-08: si auto-agregamos, NO duplicar el log con "Monitoreando".
        # Antes salian 2 lineas para el mismo canal (AUTO-AGREGADO + Monitoreando)
        # inflando el conteo aparente de canales (8 reales -> 9 en el log).
        if _match and _es_canal and not _already:
            ALLOWED_CHANNEL_IDS.add(_norm_id)
            log.info(f"📡 AUTO-AGREGADO + Monitoreando: {dialog.title} (ID: {_norm_id})")
        elif _norm_id in ALLOWED_CHANNEL_IDS or _raw_id in ALLOWED_CHANNEL_IDS:
            log.info(f"📡 Monitoreando: {dialog.title} (ID: {_norm_id})")

    # Update event handlers with ALL channels (incluyendo auto-descubiertos)
    # FIX 2026-05-06: handlers ya registrados sin chats= filter — inside filter es el control
    # (no re-registration needed — handler covers all channels via inside filter)

    # Iniciar monitor TP/SL en background
    asyncio.ensure_future(_monitor_tp_loop())
    asyncio.ensure_future(_loop_promo_reportes())
    # FIX 2026-04-24: loop de sync web cada 5 min. Render tiene filesystem
    # efimero → al redeploy pierde _copier_trades (RAM). Este loop garantiza
    # que maximo cada 5 min la web vuelva a tener los stats aunque reinicie.
    asyncio.ensure_future(_loop_sync_web_periodico())

    # FIX 2026-05-09: ingesta de senales del btc_eth_generator (BTC+ETH propias).
    # El generator escribe a generator_signals_queue.json. Aqui las leemos cada
    # 30s, las registramos en _open_signals para que el monitor TP/SL las trackee
    # igual que las del copier (celebracion, WhatsApp, etc.).
    asyncio.ensure_future(_loop_ingest_generator_signals())

    # FIX 2026-05-18: loop proactivo de regalos — garantiza 2/día aunque no lleguen
    # señales nuevas exactamente en el minuto del target. Corre cada 10 min.
    asyncio.ensure_future(_loop_gift_proactive())

    # FIX 2026-05-06: catch_up() sincroniza GetChannelDifference para TODOS los canales
    # conocidos en la sesión — garantiza que no se pierdan mensajes recientes al arrancar
    # y que Telegram registre este cliente para updates en tiempo real.
    try:
        log.info("📡 Sincronizando updates pendientes (catch_up)...")
        await client.catch_up()
        log.info("📡 catch_up completado — todos los canales sincronizados")
    except Exception as _cu_e:
        log.warning(f"⚠️ catch_up error (no bloqueante): {_cu_e}")

    log.info("📡 Signal Copier ACTIVO — escuchando todos los canales VIP...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    import sys, time as _time_lock
    # FIX 2026-05-07: separar lock-file (bloqueado, ilegible por otros procesos
    # en Windows debido a msvcrt.locking mandatorio) del pid-file legible que
    # consume el launcher para mostrar el estado del copier.
    _singleton_lock_path = Path(__file__).parent / ".copier.singleton.lock"
    _pid_file_path = Path(__file__).parent / ".copier.lock"  # legible por launcher
    _my_pid = os.getpid()

    # ============================================================
    # SINGLE-INSTANCE BULLETPROOF (file-lock held-for-life)
    # ============================================================

    # Paso 1: matar copiers ajenos
    try:
        import psutil as _ps_killall
        _killed = []
        for _proc in _ps_killall.process_iter(['pid', 'cmdline', 'status']):
            try:
                if _proc.pid == _my_pid:
                    continue
                _cmd = ' '.join(_proc.info.get('cmdline') or []).lower()
                if 'signal_copier' in _cmd and 'python' in _cmd:
                    _proc.terminate()
                    try:
                        _proc.wait(timeout=5)
                    except _ps_killall.TimeoutExpired:
                        _proc.kill()
                    _killed.append(_proc.pid)
            except (_ps_killall.NoSuchProcess, _ps_killall.AccessDenied):
                pass
        if _killed:
            log.warning(f"🧹 Copiers previos terminados: {_killed}")
            _time_lock.sleep(1)  # dar tiempo al OS a liberar locks
    except Exception as _e_killall:
        log.warning(f"⚠️ Error matando copiers previos: {_e_killall}")

    # Paso 2: adquirir lock exclusivo HELD-FOR-LIFE en archivo separado
    try:
        _copier_singleton_lock = open(_singleton_lock_path, 'w')
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(_copier_singleton_lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_copier_singleton_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        log.info(f"🔒 Copier lock exclusivo adquirido (PID {_my_pid}) — instancia única garantizada.")
    except (OSError, IOError) as _e_lock:
        log.warning(f"📡 Otra instancia del copier ya tiene el lock — saliendo. ({_e_lock})")
        sys.exit(0)

    # Paso 3: escribir PID legible (sin lock) para que el launcher lo consuma
    try:
        _pid_file_path.write_text(str(_my_pid))
    except Exception as _e_pid:
        log.warning(f"⚠️ No se pudo escribir .copier.lock con el PID: {_e_pid}")
    _max_retries = 10
    _retry_count = 0
    _retry_wait = 30
    while _retry_count < _max_retries:
        try:
            asyncio.run(main())
            _retry_count += 1
            if _retry_count < _max_retries:
                log.warning(f"📡 Copier desconectado — reconectando en {_retry_wait}s (intento {_retry_count}/{_max_retries})...")
                _time_lock.sleep(_retry_wait)
                _retry_wait = min(_retry_wait * 2, 300)
            else:
                log.error(f"📡 Copier: {_max_retries} reconexiones fallidas — saliendo.")
            continue
        except KeyboardInterrupt:
            log.info("📡 Signal Copier detenido por usuario")
            break
        except Exception as e:
            _err_str = str(e)
            log.error(f"📡 Signal Copier error: {e}")
            import re as _re_flood
            _flood_match = _re_flood.search(r'wait of (\d+) seconds', _err_str, _re_flood.IGNORECASE)
            if _flood_match:
                _wait_secs = int(_flood_match.group(1))
                _wait_mins = _wait_secs // 60
                _wait_hrs = _wait_mins // 60
                log.warning(f"⏳ Telegram FloodWait: esperando {_wait_hrs}h {_wait_mins % 60}m antes de reintentar...")
                _time_lock.sleep(min(_wait_secs + 30, 86400))
                _retry_count = 0
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
    try:
        import price_feed as _mt5_final
        if _mt5_final.terminal_info() is not None:
            _mt5_final.shutdown()
            log.info("📡 MT5 desconectado limpiamente.")
    except Exception:
        pass


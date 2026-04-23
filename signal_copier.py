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
    "USOIL": "OILCash", "WTI": "OILCash", "CRUDEOIL": "OILCash",
    # Crypto (FxPremiere envía BTC/USD)
    # FIX 2026-04-12: XM usa "BTCUSD" (no "BTCUSDm")
    "BTCUSD": "BTCUSD",
}

MAGIC_COPIER = 20260325
TWELVE_KEY = os.getenv("TWELVE_DATA_KEY", "")

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


def fmt_price(v, zero_label="—"):
    """Formato de precio: 2 decimales para valores >= 100 (GOLD, índices),
    hasta 5 decimales para forex. zero_label se muestra si v <= 0."""
    if v <= 0:
        return zero_label
    return f"{v:.2f}" if v >= 100 else f"{v:.5f}".rstrip("0").rstrip(".")


# FIX 2026-04-12: Lock para yfinance — evita race condition entre señales simultáneas
_lock_yf = threading.Lock()

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
# FIX 2026-04-15: Archivo de estadísticas persistentes — sobrevive reinicios
COPIER_STATS_FILE = Path(__file__).parent / "copier_stats.json"
# Flag para enviar resumen diario solo una vez
_daily_summary_sent: str = ""  # fecha "DD/MM/YYYY" del último resumen enviado

# === SEÑALES REGALO AL GRUPO PÚBLICO (2/día: 1 oro + 1 otra) ===
# FIX 2026-04-17: Persistir a disco — antes se perdía en cada reinicio y se
# regalaban múltiples oros el mismo día (hoy se regalaron 4 en vez de 2).
GIFT_TRACKER_FILE = Path(__file__).parent / "gift_tracker.json"
_gift_tracker = {
    "date": "",            # "YYYY-MM-DD" — se resetea al cambiar el día
    "gold_gifted": False,  # ¿Ya se regaló la señal de oro hoy?
    "other_gifted": False, # ¿Ya se regaló la otra señal hoy?
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


def _get_pips_info(pair: str, entry: float, exit_price: float) -> tuple:
    """Calcula pips y unidad según tipo de activo. Retorna (pips_numeric, pips_unit).
    FIX 2026-04-17: Petróleo (BRENT/OIL) caía en el default forex (×10000). Con
    entry=80 y diff=0.50 retornaba 5000 "pips" en vez de 50 pts. Añadido case."""
    pips_raw = abs(exit_price - entry) if entry > 0 and exit_price > 0 else 0
    _p_up = pair.upper()
    if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
        return round(pips_raw, 1), "pts"
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


def _save_copier_stats(trade: dict) -> None:
    """Persiste un trade cerrado a copier_stats.json. Limita a 90 días."""
    try:
        data = {"trades": []}
        if COPIER_STATS_FILE.exists():
            with open(COPIER_STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

        data["trades"].append(trade)

        # Cleanup: mantener solo últimos 90 días
        cutoff = time.time() - (90 * 86400)
        data["trades"] = [t for t in data["trades"] if t.get("closed_at", 0) > cutoff]

        with open(COPIER_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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


def _record_close_result(pair: str, action: str, pips: float, direction: str = "", source: str = "") -> None:
    """Registra un cierre parcial/total en copier_stats.json."""
    pair_d = _get_display_pair(pair)
    pips_numeric, pips_unit = _get_pips_info(pair, 100 if pips > 50 else 1, (100 + pips) if pips > 50 else (1 + pips / 10000))
    # Usar pips directos para Gold/índices, convertir para forex
    if pair in ("GOLD", "XAUUSD", "XAUUSD=X") or pips > 50:
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
        "entry": 0,
        "tp": 0,
        "sl": 0,
        "result": action,  # close_half | close_partial | full_close
        "pips": pips_numeric,
        "pips_unit": pips_unit,
        "opened_at": 0,
        "closed_at": time.time(),
        "fecha": datetime.now(tz).strftime("%d/%m/%Y"),
    }
    _save_copier_stats(trade)
    with _daily_results_lock:
        _daily_results.append(trade)
    log.info(f"📊 Stats: {action} {pair_d} +{pips_numeric} {pips_unit}")


def _reconcile_open_vs_mt5() -> None:
    """FIX 2026-04-17: Reconcilia copier_open_signals.json con posiciones reales MT5.

    Problema detectado: si MT5 cierra una posición (TP/SL automático) y el canal VIP
    no publica un mensaje explícito de cierre, la señal queda huérfana en
    _open_signals para siempre (hasta auto-expire a 48h).

    Esta función cada 3 min:
    1. Para cada señal abierta → busca posición MT5 viva con MAGIC_COPIER.
    2. Si no hay posición pero sí hay deal cerrado en últimas 48h con ese magic:
       → celebra como TP (profit>0) o SL (profit<0), registra stats y limpia.
    3. Si no hay posición ni deal reciente (>2h sin match): limpia silenciosamente
       porque la señal probablemente nunca se ejecutó (rechazada por el EA).
    """
    try:
        import MetaTrader5 as mt5
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
        if getattr(p, "magic", 0) != MAGIC_COPIER:
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
    for p in mt5_copier_positions:
        _dir = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
        key = (p.symbol.upper(), _dir)
        if key in tracked_keys:
            continue
        # Posición MT5 sin tracker — re-registrar
        sid = f"{p.symbol.upper()}_{int(p.time)}"
        with _signals_lock:
            if sid in _open_signals or sid in _resolved_signals:
                continue
            _open_signals[sid] = {
                "signal": {
                    "type": "new_signal",
                    "pair": p.symbol.upper(),
                    "mt5_symbol": p.symbol,
                    "direction": _dir,
                    "order_type": "Market",
                    "is_limit": False,
                    "entry": p.price_open,
                    "sl": p.sl or 0.0,
                    "tp": p.tp or 0.0,
                    "tp2": 0, "tp3": 0, "tp4": 0, "tp5": 0,
                    "rrr": "",
                    "source": "MT5_Reinsert",
                    "pair_display": _get_display_pair(p.symbol.upper()),
                    "timestamp": p.time,
                    "_mt5_ticket": p.ticket,
                    "_reinserted_by_reconcile": True,
                },
                "sent_at": p.time,
                "telegram_msg_id": None,
            }
            tracked_keys.add(key)
            reinserted += 1
    if reinserted:
        _save_open_signals()
        log.info(f"🔄 Reconcile: {reinserted} posición(es) MT5 sin tracker re-registrada(s) en copier")

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
        DEAL_ENTRY_OUT = mt5.DEAL_ENTRY_OUT
    except Exception:
        DEAL_ENTRY_OUT = 1
    for d in deals:
        if getattr(d, "magic", 0) != MAGIC_COPIER:
            continue
        if getattr(d, "entry", 0) != DEAL_ENTRY_OUT:
            continue  # solo deals de salida
        # dir de la POSICIÓN original (opuesta al deal de cierre)
        _pos_dir = "SELL" if d.type == mt5.ORDER_TYPE_BUY else "BUY"
        key = (d.symbol.upper(), _pos_dir)
        prev = closed_by_pair.get(key)
        if not prev or d.time > prev.time:
            closed_by_pair[key] = d

    to_remove = []  # [(sig_id, sdata, deal_or_None, reason)]
    now = time.time()

    for sig_id, sdata in signals_copy.items():
        sig = sdata.get("signal", {})
        pair = sig.get("pair", "")
        direction = sig.get("direction", "")
        sent_at = sdata.get("sent_at", now)
        age_min = (now - sent_at) / 60

        mt5_sym = _resolve_mt5_sym(pair).upper()
        # Caso A: hay posición MT5 viva → sincronizada, continuar
        if (mt5_sym, direction) in mt5_open_keys:
            continue

        # Caso B: no hay posición → buscar deal cerrado reciente
        deal = closed_by_pair.get((mt5_sym, direction))
        if deal and deal.time > sent_at - 60:  # deal posterior al envío de señal
            to_remove.append((sig_id, sdata, deal, "closed_mt5"))
            continue

        # Caso C: señal con entry=0 sin match aún (monitor todavía le asignará precio)
        entry = sig.get("entry", 0) or 0
        if entry <= 0 and age_min < 30:
            continue

        # Caso D: sin match y >2h desde envío → nunca se ejecutó, limpiar
        if age_min > 120:
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
            profit = getattr(deal, "profit", 0.0) or 0.0
            exit_price = getattr(deal, "price", 0.0) or 0.0
            entry = sig.get("entry", 0) or 0
            # Calcular pips con precio real de salida
            pips_num, pips_unit = _get_pips_info(pair, entry, exit_price)
            result = "tp" if profit > 0 else "sl"

            # Anti-duplicado: si otro flujo ya notificó este cierre, skip
            _notif_key = f"{pair}_{direction}_{result}_reconcile"
            _prev = _recently_notified.get(_notif_key, 0)
            if _prev and (now - _prev) < 600:
                log.info(f"🔕 Reconcile: {result.upper()} {pair_d} ya notificado — skip")
                _save_open_signals()
                continue
            _recently_notified[_notif_key] = now

            # Registrar en stats persistentes
            signal_copy = dict(sig)
            signal_copy["entry"] = entry
            if result == "tp" and exit_price > 0:
                signal_copy["_tp_final"] = exit_price
            elif result == "sl" and exit_price > 0:
                signal_copy["sl"] = exit_price
            _record_daily_result(signal_copy, result)

            # Notificar al canal (sin celebración agresiva — solo info de cierre reconciliado)
            try:
                import requests
                emoji = "🎯" if result == "tp" else "🛑"
                label = "TAKE PROFIT" if result == "tp" else "STOP LOSS"
                sign = "+" if result == "tp" else "-"
                msg = (
                    f"{emoji} *{label} — {pair_d}*\n\n"
                    f"Cierre detectado en MT5\n"
                    f"Resultado: *{sign}{pips_num:.1f} {pips_unit}*\n"
                    f"P&L: *${profit:.2f}*"
                )
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                payload = {"chat_id": CHANNEL_ID, "text": msg, "parse_mode": "Markdown"}
                if _reply_id:
                    payload["reply_to_message_id"] = _reply_id
                r = requests.post(url, json=payload, timeout=10)
                if r.status_code == 400 and "message to be replied" in r.text:
                    payload.pop("reply_to_message_id", None)
                    requests.post(url, json=payload, timeout=10)
                log.info(f"🔄 Reconcile: {result.upper()} {pair_d} publicado (profit=${profit:.2f}, pips={pips_num:.1f})")
            except Exception as e:
                log.warning(f"Reconcile notify error {pair_d}: {e}")
        else:
            log.info(f"🧹 Reconcile: {pair_d} {direction} limpiada — nunca ejecutada en MT5 (age={((now-sdata.get('sent_at',now))/60):.0f}min)")

    _save_open_signals()


def _send_daily_summary() -> None:
    """Envía resumen completo del día al canal VIP.
    Incluye trades cerrados (TP/SL/cierres) Y señales aún abiertas."""
    import requests
    from datetime import datetime
    import pytz
    tz = pytz.timezone("Europe/Andorra")
    hoy = datetime.now(tz).strftime("%d/%m/%Y")

    # ── Trades cerrados hoy (de copier_stats.json) ──
    today_trades = _load_copier_stats_today()

    tps = [t for t in today_trades if t.get("result") == "tp"]
    sls = [t for t in today_trades if t.get("result") == "sl"]
    closes_profit = [t for t in today_trades if t.get("result") in ("close_half", "close_partial", "full_close") and t.get("pips", 0) > 0]
    closes_loss = [t for t in today_trades if t.get("result") in ("close_half", "close_partial", "full_close") and t.get("pips", 0) <= 0]

    # ── Señales aún abiertas (de _open_signals) ──
    with _signals_lock:
        open_count = len(_open_signals)
        open_pairs = {}
        for _sid, _sdata in _open_signals.items():
            _s = _sdata.get("signal", {})
            _p = _s.get("pair_display", _s.get("pair", "?"))
            _d = _s.get("direction", "?")
            _key = f"{_p} {_d}"
            open_pairs[_key] = open_pairs.get(_key, 0) + 1

    # ── Total real de señales del día (cerradas + abiertas) ──
    # Deduplicar señales abiertas que vienen de multiples fuentes
    unique_open = len(open_pairs)
    total_all = len(today_trades) + unique_open

    # Win rate (solo sobre TP vs SL — señales con resultado definitivo)
    decided = len(tps) + len(sls)
    wr = len(tps) / decided * 100 if decided > 0 else 0

    # Pips netos
    pips_tp = sum(t.get("pips", 0) for t in tps)
    pips_sl = sum(t.get("pips", 0) for t in sls)
    pips_closes = sum(t.get("pips", 0) for t in closes_profit)
    pips_closes_loss = sum(t.get("pips", 0) for t in closes_loss)
    pips_netos = pips_tp - pips_sl + pips_closes - pips_closes_loss

    # Mejor y peor señal
    all_with_pips = [t for t in today_trades if t.get("pips", 0) > 0 and t.get("result") in ("tp", "close_half", "close_partial", "full_close")]
    mejor = max(all_with_pips, key=lambda x: x.get("pips", 0), default=None)
    peor = max(sls, key=lambda x: x.get("pips", 0), default=None)

    mejor_txt = ""
    if mejor:
        mejor_txt = f"\n🏆 Mejor: {mejor['pair_display']} *+{mejor['pips']:.0f} {mejor.get('pips_unit', 'pts')}*"
    peor_txt = ""
    if peor:
        peor_txt = f"\n💥 Peor: {peor['pair_display']} *-{peor['pips']:.0f} {peor.get('pips_unit', 'pts')}*"

    # Por activo (cerrados)
    asset_stats = {}
    for t in today_trades:
        if t.get("result") not in ("tp", "sl"):
            continue
        p = t.get("pair_display", t.get("pair", "?"))
        if p not in asset_stats:
            asset_stats[p] = {"w": 0, "l": 0}
        if t["result"] == "tp":
            asset_stats[p]["w"] += 1
        else:
            asset_stats[p]["l"] += 1

    asset_lines = ""
    for asset, stats in sorted(asset_stats.items(), key=lambda x: x[1]["w"] + x[1]["l"], reverse=True):
        asset_lines += f"\n  {asset}: {stats['w']}W / {stats['l']}L"

    # Señales abiertas por par
    open_lines = ""
    if open_pairs:
        for pair_dir, count in sorted(open_pairs.items()):
            src_note = f" ({count} fuentes)" if count > 1 else ""
            open_lines += f"\n  ⏳ {pair_dir}{src_note}"

    pips_sign = "+" if pips_netos >= 0 else ""

    # Si no hay trades cerrados ni señales abiertas, no enviar
    if not today_trades and not open_pairs:
        log.info("📊 Resumen diario: sin actividad hoy, no se envía")
        return

    # FIX 2026-04-17: Resumen más compacto — sin Win Rate, sin listas largas
    msg = f"📊 *RESUMEN — {hoy}*\n"

    if pips_tp > 0 or pips_sl > 0:
        msg += f"\n💰 *{pips_sign}{pips_netos:.0f} pips netos*"
        msg += mejor_txt
        msg += "\n"

    # Contadores en una línea compacta
    _parts = []
    if len(tps):  _parts.append(f"✅ {len(tps)} TP")
    if len(sls):  _parts.append(f"🛑 {len(sls)} SL")
    if closes_profit or closes_loss:
        _n_parciales = len(closes_profit) + len(closes_loss)
        _pips_parciales = pips_closes - pips_closes_loss
        _sign_p = "+" if _pips_parciales >= 0 else ""
        _parts.append(f"⚡ {_n_parciales} parciales ({_sign_p}{_pips_parciales:.0f} pips)")
    if unique_open > 0:
        _parts.append(f"⏳ {unique_open} abiertas")
    if _parts:
        msg += "\n" + " · ".join(_parts) + "\n"

    msg += (
        f"\n🔔 {total_all} señales hoy ({len(today_trades)} cerradas)\n"
        f"_BuySell365 Pro_"
    )

    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHANNEL_ID, "text": msg, "parse_mode": "Markdown"}
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            log.info(f"📊 Resumen diario enviado ({len(tps)} TPs, {len(sls)} SLs, {unique_open} abiertas)")
        else:
            log.warning(f"📊 Error enviando resumen: {resp.status_code}")
    except Exception as e:
        log.warning(f"📊 Error enviando resumen: {e}")

    # ── Instagram: resumen diario DESACTIVADO ──
    # El usuario solo quiere TPs en Instagram, no resúmenes diarios
    # (se mantiene solo en Telegram)


# === SEÑALES REGALO — funciones ===

def _should_gift_signal(pair: str) -> bool:
    """Decide si esta señal se regala al grupo público.
    Basado en análisis de 2 días de datos VIP (15-16 abril 2026):
    - ORO: franja óptima 8:00-12:00 (14 señales/2 días, mejores wins)
    - OTRA: franja óptima 9:00-15:00 (11 señales/2 días, NAS100 +300 etc.)
    Fuera de franja: probabilidad baja pero escalada para garantizar 2/día.
    """
    import random
    from datetime import datetime
    import pytz

    if not GROUP_ID:
        return False

    tz = pytz.timezone("Europe/Andorra")
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")
    hour = now.hour

    is_gold = pair.upper() in ("GOLD", "XAUUSD", "XAUUSD=X")

    with _gift_lock:
        # Reset diario
        if _gift_tracker["date"] != today_str:
            _gift_tracker["date"] = today_str
            _gift_tracker["gold_gifted"] = False
            _gift_tracker["other_gifted"] = False
            _save_gift_tracker()

        # Si ya se regalaron las 2 del día, no regalar más
        if _gift_tracker["gold_gifted"] and _gift_tracker["other_gifted"]:
            return False

        # Comprobar si este tipo ya se regaló
        if is_gold and _gift_tracker["gold_gifted"]:
            return False
        if not is_gold and _gift_tracker["other_gifted"]:
            return False

        # Probabilidad basada en franjas óptimas analizadas
        if is_gold:
            # ORO: franja óptima 8:00-12:00 (pico de señales con wins)
            if 8 <= hour < 12:
                prob = 0.50   # Franja óptima — 1 de cada 2
            elif 12 <= hour < 16:
                prob = 0.70   # Franja secundaria — más urgencia
            elif hour >= 16:
                prob = 0.95   # Tarde — regalar la próxima que llegue
            else:
                prob = 0.15   # Madrugada/temprano — esperar mejor hora
        else:
            # OTRA: franja óptima 9:00-15:00 (NAS100, USD/CAD, etc.)
            if 9 <= hour < 15:
                prob = 0.50   # Franja óptima
            elif 15 <= hour < 18:
                prob = 0.75   # Franja secundaria
            elif hour >= 18:
                prob = 0.95   # Tarde — regalar ya
            else:
                prob = 0.10   # Madrugada — esperar

        if random.random() < prob:
            # Marcar como regalada
            if is_gold:
                _gift_tracker["gold_gifted"] = True
            else:
                _gift_tracker["other_gifted"] = True
            _save_gift_tracker()  # FIX 2026-04-17: persistir tras mutar
            log.info(f"🎁 Señal REGALO seleccionada: {pair} ({'oro' if is_gold else 'otra'}) hora={hour} prob={prob:.0%}")
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

    msg = (
        f"🎁🎁🎁  *SEÑAL GRATIS*  🎁🎁🎁\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{dir_emoji} *{dir_label} — {pair_d}*\n\n"
        f"📍 Entrada: {entry_display}"
        f"{tp_lines}\n"
        f"🛡️ SL: {fmt(sl)}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🆓 Señal *REGALO* de nuestro Canal VIP\n"
        f"📊 Gestión de riesgo incluida\n\n"
        f"💎 *¿Quieres recibir TODAS las señales?*\n"
        f"Hoy ya van +{ops_hoy} operaciones en el VIP\n"
        f"👉 Escribe /vip y empieza a operar con nosotros"
    )
    return msg


def _is_gifted_signal(pair: str) -> bool:
    """Comprueba si hay una señal regalada activa para este par."""
    with _signals_lock:
        for sid, sdata in _open_signals.items():
            if sdata.get("gifted") and sdata.get("signal", {}).get("pair") == pair:
                return True
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


def _validate_entry_vs_market(signal: dict) -> bool:
    """FIX 2026-04-17: Si entry difiere >1.5% del precio de mercado → RECHAZAR señal.
    Antes corregíamos al precio actual, pero eso rompía la relación entry/TP/SL
    (especialmente en señales retrasadas tipo Sureshot 2 días después → publicábamos
    TP<entry para BUY = imposible). Ahora descartamos directamente.

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
        import MetaTrader5 as _mt5v
        _mt5_sym_map_v = {
            "GOLD": "GOLD", "XAUUSD": "GOLD", "ORO": "GOLD",
            "NAS100": "US100Cash", "NASDAQ": "US100Cash", "US100": "US100Cash",
            "US30": "US30Cash", "DOW": "US30Cash", "US500": "US500Cash",
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

    pct_diff = abs(entry - live) / live
    if pct_diff > 0.015:  # >1.5% de diferencia → señal stale, descartar
        log.warning(f"🚫 Entry stale RECHAZADA: {pair} entry={entry} difiere {pct_diff:.1%} del precio actual {live:.2f} (>1.5% → señal vieja, no publicable)")
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


def _fetch_chart_image(pair: str, direction: str, entry: float, tp: float, *, title_override: str = "", tp_levels: list = None) -> bytes | None:
    """Generate professional chart using Twelve Data (primary) or yfinance (fallback) + matplotlib.
    title_override: si se pasa, usa ese título en vez de 'TP HIT'.
    tp_levels: lista de tuplas [("TP1", precio), ("TP2", precio), ...] para dibujar múltiples líneas."""
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
            import MetaTrader5 as _mt5_chart
            # FIX 2026-04-12: Mapa COMPLETO — mismos pares que _mt5_sym_map del monitor
            _mt5_chart_map = {
                # Oro
                "GOLD": "GOLD", "XAUUSD": "GOLD", "ORO": "GOLD",
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
                # Intentar varios timeframes/tamaños para que el gráfico incluya
                # tanto la entrada como el TP (el precio pudo haber rebotado)
                _best_rates = None
                _target_prices = [p for p in [entry, tp] if p > 0]
                for _tf, _tf_name, _count in [
                    (_mt5_chart.TIMEFRAME_M5, "M5", 80),
                    (_mt5_chart.TIMEFRAME_M15, "M15", 80),
                    (_mt5_chart.TIMEFRAME_M30, "M30", 60),
                    (_mt5_chart.TIMEFRAME_H1, "H1", 50),
                ]:
                    _rates = _mt5_chart.copy_rates_from_pos(_mt5_sym_chart, _tf, 0, _count)
                    if _rates is None or len(_rates) < 10:
                        continue
                    # Verificar que las velas cubren el rango entry-TP
                    _all_lows = [float(r['low']) for r in _rates]
                    _all_highs = [float(r['high']) for r in _rates]
                    _data_min = min(_all_lows)
                    _data_max = max(_all_highs)
                    _covers_all = all(_data_min <= p <= _data_max for p in _target_prices)
                    if _covers_all:
                        _best_rates = _rates
                        log.info(f"📊 Chart {_tf_name} cubre entry+TP ({len(_rates)} velas)")
                        break
                    if _best_rates is None:
                        _best_rates = _rates  # Guardar como fallback
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

        fig, ax = plt.subplots(figsize=(11, 5.5), facecolor=BG)
        ax.set_facecolor(BG)

        # ── Velas japonesas ──
        candle_width = 0.65
        wick_width = 1.5
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
        _tp_colors = ["#ffd700", "#ffb300", "#ff8f00", "#ff6f00", "#ff4500"]  # Gold → Orange gradient
        _all_tps = tp_levels if tp_levels else [("TP", tp)]
        for _i_tp, (_tp_label, _tp_val) in enumerate(_all_tps):
            if _tp_val <= 0:
                continue
            _tp_color = _tp_colors[min(_i_tp, len(_tp_colors) - 1)]
            _lw = 2.5 if _i_tp == len(_all_tps) - 1 else 1.8  # Última TP más gruesa
            _alpha = 0.95 if _i_tp == len(_all_tps) - 1 else 0.75
            ax.axhline(y=_tp_val, color=_tp_color, linestyle="-", linewidth=_lw, alpha=_alpha, zorder=5)
            ax.text(n + 0.5, _tp_val, f" {_tp_label} {fmt_price(_tp_val)}", color="#131722", fontsize=10,
                    fontweight="bold", va="center", ha="left", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor=_tp_color, edgecolor=_tp_color, alpha=0.9))

        # ── Entry line ──
        if entry > 0:
            ax.axhline(y=entry, color=ENTRY_COLOR, linestyle="--", linewidth=2, alpha=0.85, zorder=5)
            ax.text(n + 0.5, entry, f" Entrada {fmt_price(entry)}", color="#ffffff", fontsize=10,
                    fontweight="bold", va="center", ha="left", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.3", facecolor=ENTRY_COLOR, edgecolor=ENTRY_COLOR, alpha=0.85))

        # ── Zona de profit (fill entre entry y TP final) ──
        if entry > 0 and tp > 0:
            y_min = min(entry, tp)
            y_max = max(entry, tp)
            ax.axhspan(y_min, y_max, alpha=0.10, color=GOLD, zorder=1)
            # Bordes de la zona
            ax.axhline(y=y_min, color=GOLD, linestyle=":", linewidth=0.5, alpha=0.3, zorder=1)
            ax.axhline(y=y_max, color=GOLD, linestyle=":", linewidth=0.5, alpha=0.3, zorder=1)

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
        dir_label = "BUY" if direction.upper() == "BUY" else "SELL"
        if title_override:
            title = title_override
        else:
            title = f"✅ TP ALCANZADO — {dir_label} {pair_d}"
            if pips_label:
                title += f"  |  {pips_label}"
        ax.set_title(title, color=GOLD, fontsize=16, fontweight="bold", pad=18,
                     fontfamily="sans-serif")

        # Grid estilo TradingView
        ax.grid(True, alpha=0.08, color=TEXT, linestyle="-", linewidth=0.5)
        ax.tick_params(colors=TEXT, labelsize=9)
        ax.set_xlim(-1, n + 9)  # Espacio para labels (más ancho para múltiples TPs)

        # FIX 2026-04-15: Forzar eje Y para incluir entry y todos los TPs
        # Si las velas no cubren el rango, el gráfico se veía cortado/incoherente
        _y_prices = [v for v in [entry, tp] if v > 0]
        for _tp_item in (_all_tps if tp_levels else []):
            if _tp_item[1] > 0:
                _y_prices.append(_tp_item[1])
        if _y_prices and lows and highs:
            _chart_min = min(min(lows), min(_y_prices))
            _chart_max = max(max(highs), max(_y_prices))
            _margin = (_chart_max - _chart_min) * 0.08
            ax.set_ylim(_chart_min - _margin, _chart_max + _margin)

        for spine in ax.spines.values():
            spine.set_visible(False)

        # ── Watermark grande de fondo ──
        fig.text(0.50, 0.58, "BUYSELL365 PRO", fontsize=48, color="#2a3045",
                 ha="center", va="center", fontweight="bold", alpha=0.85,
                 fontstyle="normal", fontfamily="sans-serif",
                 transform=fig.transFigure, zorder=0)
        # Watermark pequeño esquina
        fig.text(0.98, 0.02, "buysell365.pro", fontsize=9, color="#3a3f52",
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


def _send_tp_celebration(signal: dict, reply_to_msg_id: int = None) -> None:
    """Send TP celebration to channel with chart image and rockets."""
    import requests

    direction = signal["direction"]
    pair = signal["pair"]
    entry = signal["entry"]
    # FIX 2026-04-08: Usar TP final (el más alto/bajo) para calcular profit real
    tp = signal.get("_tp_final") or signal.get("tp", 0) or 0
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

    pips_line = f"\n💰 Ganancia: *{pips_str}*" if pips_str else ""

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
    msg = (
        f"🎯🎯 *TP ALCANZADO* 🎯🎯\n"
        f"━━━━━━━━━━━━━━\n"
        f"{dir_emoji} *{_dir_label_es} — {pair_d}*\n\n"
        f"📍 Entrada: {fmt(entry)}"
        f"{tp_lines}"
        f"{pips_line}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🚀 _BuySell365 Pro — señal exitosa_"
    )

    chart_bytes = _fetch_chart_image(pair, direction, entry, tp, tp_levels=_valid_display if len(_valid_display) > 1 else None)

    # FIX 2026-04-16: Video compacto 720x720 solo para GRUPO, VIP recibe foto como siempre
    _tg_video_path = None   # Video compacto para grupo público
    try:
        from instagram_poster import _generate_tp_telegram_video
        _tg_video_path = _generate_tp_telegram_video(
            pair, direction, pips_str if pips_str else "+0",
            entry_price=entry, tp_price=tp,
            chart_image=chart_bytes)
    except Exception as _reel_err:
        log.debug(f"Video gen error: {_reel_err}")

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
        else:
            log.warning(f"Celebration VIP send error: {resp.status_code} {resp.text[:80]}")
    except Exception as e:
        log.warning(f"Celebration send error: {e}")

    # FIX 2026-04-07: También celebrar en el grupo público (marketing)
    _was_gifted = _is_gifted_signal(pair)
    if GROUP_ID and str(GROUP_ID) != str(CHANNEL_ID):
        import random

        if _was_gifted:
            # ── CELEBRACIÓN ESPECIAL para señales REGALO ──
            _msg_grupo = (
                f"🎁🎯 *SEÑAL GRATIS GANADORA* 🎯🎁\n"
                f"━━━━━━━━━━━━━━\n"
                f"{dir_emoji} *{_dir_label_es} — {pair_d}*\n\n"
                f"📍 Entrada: {fmt(entry)}"
                f"{tp_lines}"
                f"{pips_line}\n"
                f"━━━━━━━━━━━━━━\n"
                f"🎁 *¡La señal REGALO tocó TP!*\n"
                f"🏆 Esto pasa todos los días en el VIP\n\n"
                f"🔥 *¿Quieres TODAS las señales?*\n"
                f"👉 Escribe */vip* para unirte"
            )
            log.info(f"🎁🎯 GIFT TP CELEBRATION: {pair_d} {pips_str}")
        else:
            _promos = [
                "\n\n💎 *¿Quieres recibir estas señales?*\nÚnete al canal VIP y opera con nosotros.\n👉 Escribe */vip* para más info",
                "\n\n📡 *Suscríbete al canal VIP*\nRecibe estas señales en tiempo real con entry, TP y SL exactos.\n👉 Escribe */vip* para activarlo",
                "\n\n🔥 *Otra victoria más del equipo*\nNo te quedes fuera, únete al VIP.\n👉 Escribe */vip* y empieza hoy",
                "\n\n📈 *Resultados reales, sin trucos*\nSeñales en vivo con entrada, TP y SL exactos.\n👉 Escribe */vip* para unirte",
            ]
            _msg_grupo = (
                f"🎯🎯 *TP ALCANZADO* 🎯🎯\n"
                f"━━━━━━━━━━━━━━\n"
                f"{dir_emoji} *{_dir_label_es} — {pair_d}*\n\n"
                f"📍 Entrada: {fmt(entry)}"
                f"{tp_lines}"
                f"{pips_line}\n"
                f"━━━━━━━━━━━━━━\n"
                f"🚀 _BuySell365 Pro — señal exitosa_"
                f"{random.choice(_promos)}"
            )
        # FIX 2026-04-16: Video COMPACTO 720x720 para el grupo (no el reel vertical)
        _reel_sent = False
        try:
            _grupo_video = _tg_video_path
            if _grupo_video and Path(_grupo_video).exists():
                _url_gv = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
                with open(str(_grupo_video), "rb") as _vf:
                    _resp_gv = requests.post(_url_gv, data={
                        "chat_id": GROUP_ID, "caption": _msg_grupo,
                        "parse_mode": "Markdown", "supports_streaming": "true"
                    }, files={"video": _vf}, timeout=30)
                if _resp_gv.status_code == 200:
                    _reel_sent = True
                    log.info(f"📢 TP VIDEO compacto enviado al GRUPO: {dir_label} {pair}")
        except Exception as _ev:
            log.debug(f"Video grupo error: {_ev}")

        # Fallback: si el video falla, enviar imagen como antes
        if not _reel_sent:
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
                log.info(f"📢 TP celebration (imagen) enviada al GRUPO: {dir_label} {pair}")
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
            _ig_post_tp(pair_d, direction, entry, tp, pips_str if pips_str else "+0",
                        source=signal.get("source", ""), chart_path=_chart_file,
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
    if pair in ("GOLD", "XAUUSD", "XAUUSD=X") or entry >= 100:
        pips_str = f"+{pips:.0f} pts" if pips >= 1 else f"+{pips:.1f} pts"
    elif "JPY" in pair.upper():
        pips_str = f"+{pips:.0f} pips"
    else:
        pips_str = f"+{pips:.0f} pips"

    # Labels según tipo de cierre
    if action == "close_half":
        title_emoji = "⚡⚡"
        title_text = "GANANCIAS ASEGURADAS"
        action_line = "⚡ Cierre parcial 50%"
        subtitle = "protegiendo ganancias"
    elif action == "close_partial":
        title_emoji = "⚡⚡"
        title_text = "GANANCIAS ASEGURADAS"
        action_line = "⚡ Cierre parcial"
        subtitle = "protegiendo ganancias"
    else:  # full_close
        title_emoji = "🔒🔒"
        title_text = "CIERRE CON GANANCIA"
        action_line = "✅ Cierre total"
        subtitle = "operación exitosa"

    entry_line = f"\n📍 Entrada: {fmt_price(entry)}" if entry > 0 else ""

    _was_gifted = _is_gifted_signal(pair)
    if _was_gifted:
        # ── CELEBRACIÓN ESPECIAL para señal REGALO ──
        _msg_grupo = (
            f"🎁🎯 *SEÑAL GRATIS GANADORA* 🎯🎁\n"
            f"━━━━━━━━━━━━━━\n"
            f"{dir_emoji} *{_dir_label_es} — {pair_d}*"
            f"{entry_line}\n"
            f"{action_line}\n"
            f"💰 Ganancia: *{pips_str}*\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎁 *¡La señal REGALO fue ganadora!*\n"
            f"🏆 Esto pasa todos los días en el VIP\n\n"
            f"🔥 *¿Quieres TODAS las señales?*\n"
            f"👉 Escribe */vip* para unirte"
        )
        log.info(f"🎁🎯 GIFT CLOSE CELEBRATION: {pair_d} {pips_str}")
    else:
        _promos = [
            "\n\n💎 *¿Quieres recibir estas señales?*\nÚnete al canal VIP y opera con nosotros.\n👉 Escribe */vip* para más info",
            "\n\n📡 *Suscríbete al canal VIP*\nRecibe estas señales en tiempo real con entry, TP y SL exactos.\n👉 Escribe */vip* para activarlo",
            "\n\n🔥 *Otra victoria más del equipo*\nNo te quedes fuera, únete al VIP.\n👉 Escribe */vip* y empieza hoy",
            "\n\n📈 *Resultados reales, sin trucos*\nSeñales en vivo con entrada, TP y SL exactos.\n👉 Escribe */vip* para unirte",
        ]
        _msg_grupo = (
            f"{title_emoji} *{title_text}* {title_emoji}\n"
            f"━━━━━━━━━━━━━━\n"
            f"{dir_emoji} *{_dir_label_es} — {pair_d}*"
            f"{entry_line}\n"
            f"{action_line}\n"
            f"💰 Ganancia: *{pips_str}*\n"
            f"━━━━━━━━━━━━━━\n"
            f"🚀 _BuySell365 Pro — {subtitle}_"
            f"{random.choice(_promos)}"
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
    _close_chart = _fetch_chart_image(pair, direction, entry, tp_approx) if entry > 0 else None

    # ── VIDEO COMPACTO al GRUPO (720x720 con velas animadas MT5) ──
    if GROUP_ID and str(GROUP_ID) != str(CHANNEL_ID):
        _reel_sent = False
        # Header dinámico según tipo de cierre
        _header_map = {
            "close_half":    "CIERRE PARCIAL",
            "close_partial": "CIERRE PARCIAL",
            "full_close":    "CIERRE TOTAL",
        }
        _hdr = _header_map.get(action, "GANANCIAS ASEGURADAS")
        try:
            from instagram_poster import _generate_tp_telegram_video
            _tg_path = _generate_tp_telegram_video(
                pair_d, direction, pips_str,
                entry_price=entry, tp_price=tp_approx,
                chart_image=_close_chart,
                header_text=_hdr)
            if _tg_path and Path(_tg_path).exists():
                _url_gv = f"https://api.telegram.org/bot{BOT_TOKEN}/sendVideo"
                with open(str(_tg_path), "rb") as _vf:
                    _resp_gv = requests.post(_url_gv, data={
                        "chat_id": GROUP_ID, "caption": _msg_grupo,
                        "parse_mode": "Markdown", "supports_streaming": "true"
                    }, files={"video": _vf}, timeout=30)
                if _resp_gv.status_code == 200:
                    _reel_sent = True
                    log.info(f"📢 CLOSE CELEBRATION VIDEO compacto al GRUPO: {action} {pair_d} {pips_str}")
        except Exception as _ev:
            log.debug(f"Video close celebration error: {_ev}")

        # Fallback: texto si video falla
        if not _reel_sent:
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
            _ig_post_tp(pair_d, direction, entry if entry > 0 else 0,
                        tp_approx, pips_str, source=source,
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


def _send_sl_notification(signal: dict, reply_to_msg_id: int = None) -> None:
    """Notify channel that SL was hit — same professional model as TP HIT.
    FIX 2026-04-17: Si la señal tocó TPs antes, mostrar el NETO real (no solo pérdida)."""
    import requests

    direction = signal["direction"]
    pair = signal["pair"]
    entry = signal["entry"]
    sl = signal["sl"]
    pair_d = _get_display_pair(pair)

    dir_label = direction.upper()
    dir_emoji = "🟢" if direction == "BUY" else "🔴"

    fmt = fmt_price

    # Calcular pips perdidos en el último segmento (resto de la posición al SL)
    pips_lost = abs(sl - entry) if entry > 0 and sl > 0 else 0

    def _fmt_pips(v, signo=""):
        if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
            return f"{signo}{v:.0f} pts"
        elif "JPY" in pair.upper():
            return f"{signo}{v * 100:.0f} pips"
        elif entry >= 100:
            return f"{signo}{v:.1f} pts"
        else:
            return f"{signo}{v * 10000:.0f} pips"

    # FIX 2026-04-17: Verificar si la señal ya tocó TPs antes → mensaje CIERRE NETO
    tps_previos = signal.get("_tps_alcanzados", []) or []
    _dir_label_es = "BUY" if direction.upper() == "BUY" else "SELL"

    if tps_previos:
        # Calcular neto: suma de pips TPs - pips perdidos en último segmento
        pips_ganados_tps = sum(t.get("pips", 0) for t in tps_previos)
        # Asumimos gestión profesional: cada TP cierra 1/(N+1) de la posición, resto al SL
        # Simplificación: neto = ganancias acumuladas de TPs - pérdida parcial restante
        n_tps = len(tps_previos)
        # Si tocaron todos los TPs declarados, el resto es pequeño; si no, el SL afecta proporcional
        pips_netos = pips_ganados_tps - pips_lost
        signo_neto = "+" if pips_netos >= 0 else ""
        emoji_neto = "✅" if pips_netos >= 0 else "⚠️"

        tps_lines = "\n".join(
            f"✅ TP{t['nivel']}: {_fmt_pips(t.get('pips',0),'+')}" for t in tps_previos
        )
        msg = (
            f"🏁 *CIERRE TOTAL* 🏁\n"
            f"━━━━━━━━━━━━━━\n"
            f"{dir_emoji} *{_dir_label_es} — {pair_d}*\n\n"
            f"📍 Entrada: {fmt(entry)}\n"
            f"{tps_lines}\n"
            f"🛡️ SL final: {fmt(sl)}  ({_fmt_pips(pips_lost,'-')} última parte)\n\n"
            f"{emoji_neto} *Neto: {_fmt_pips(abs(pips_netos), signo_neto)}*\n"
            f"━━━━━━━━━━━━━━\n"
            f"📊 _BuySell365 Pro — {n_tps} TP(s) asegurado(s) antes del cierre_"
        )
    else:
        # Sin TPs previos: mensaje SL normal
        msg = (
            f"🛑🛑 *SL TOCADO* 🛑🛑\n"
            f"━━━━━━━━━━━━━━\n"
            f"{dir_emoji} *{_dir_label_es} — {pair_d}*\n\n"
            f"📍 Entrada: {fmt(entry)}\n"
            f"🛡️ SL: {fmt(sl)}  ({_fmt_pips(pips_lost,'-')})\n"
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
    entry = signal.get("entry", 0) or 0
    pair_d = _get_display_pair(pair)
    dir_emoji = "🟢" if direction == "BUY" else "🔴"
    _dir_label_es = "BUY" if direction.upper() == "BUY" else "SELL"

    # Precio actual (si lo conseguimos) para calcular neto realista
    _live = None
    try:
        import MetaTrader5 as _mt5_e
        _sym_clean = pair.upper().replace("/", "")
        _sym_map = {"GOLD": "GOLD", "XAUUSD": "GOLD", "ORO": "GOLD",
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
        if pair in ("GOLD", "XAUUSD", "XAUUSD=X"):
            return f"{signo}{v:.0f} pts"
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

    _titulo = {
        "expired": "⏱ CIERRE POR TIEMPO",
        "orphan":  "⏱ CIERRE — sin precio del broker",
        "eod":     "🌙 CIERRE FIN DEL DÍA",
    }.get(reason, "⏱ CIERRE POR TIEMPO")

    if tps_previos:
        tps_lines = "\n".join(
            f"✅ TP{t['nivel']}: {_fmt_pips(t.get('pips',0),'+')}" for t in tps_previos
        )
        msg = (
            f"{_titulo}\n"
            f"━━━━━━━━━━━━━━\n"
            f"{dir_emoji} *{_dir_label_es} — {pair_d}*\n\n"
            f"📍 Entrada: {fmt_price(entry)}\n"
            f"{tps_lines}\n"
            f"📊 Cierre actual: {fmt_price(_live) if _live > 0 else '—'}\n\n"
            f"{emoji_neto} *Neto: {_fmt_pips(abs(pips_netos), signo_neto)}*\n"
            f"━━━━━━━━━━━━━━\n"
            f"📊 _BuySell365 Pro — {len(tps_previos)} TP(s) asegurado(s) antes del cierre_"
        )
    else:
        msg = (
            f"{_titulo}\n"
            f"━━━━━━━━━━━━━━\n"
            f"{dir_emoji} *{_dir_label_es} — {pair_d}*\n\n"
            f"📍 Entrada: {fmt_price(entry)}\n"
            f"📊 Cierre actual: {fmt_price(_live) if _live > 0 else '—'}\n\n"
            f"{emoji_neto} *Neto: {_fmt_pips(abs(pips_netos), signo_neto)}*\n"
            f"━━━━━━━━━━━━━━\n"
            f"📊 _BuySell365 Pro — operación finalizada_"
        )

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
        "pips_unit": "pts" if pair in ("GOLD", "XAUUSD", "XAUUSD=X") or entry >= 100 else "pips",
        "pips_numeric": pips_numeric,
        "result": result,
        "time": time.time(),
        "opened_at": signal.get("timestamp", 0),
        "closed_at": time.time(),
        "fecha": datetime.now(tz).strftime("%d/%m/%Y"),
    }
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

    # FIX 2026-04-14: Reporte simplificado — solo ganancias + promo VIP
    msg = (
        f"📊📊📊 *REPORTE {hora_label}* 📊📊📊\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {hoy}\n\n"
        f"💰 *Ganancias del día:*\n"
        f"{resumen}\n\n"
        f"🔥 *Estas ganancias fueron en VIVO*\n"
        f"Nuestros suscriptores VIP las recibieron en tiempo real.\n\n"
        f"👉 Escribe */vip* y empieza a ganar con nosotros\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
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
    global _daily_summary_sent
    _sent_today = {}  # FIX 2026-04-16: Variable local para promo/follow diarios (antes NameError)
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

        # ── FIX 2026-04-15: Resumen diario automático a las 22:00 hora Andorra ──
        try:
            from datetime import datetime
            import pytz
            _tz_sum = pytz.timezone("Europe/Andorra")
            _now_sum = datetime.now(_tz_sum)
            _hoy_str = _now_sum.strftime("%d/%m/%Y")
            if _now_sum.hour == 22 and _now_sum.minute < 5 and _daily_summary_sent != _hoy_str:
                _daily_summary_sent = _hoy_str
                _send_daily_summary()
        except Exception as _e_sum:
            log.warning(f"Error en resumen diario: {_e_sum}")

        # ── Publicidad diaria de Instagram en grupo Telegram (14:00) ──
        try:
            if _now_sum.hour == 14 and _now_sum.minute < 5 and _sent_today.get("ig_promo") != _hoy_str:
                _sent_today["ig_promo"] = _hoy_str
                import random
                import requests as _req_ig
                _ig_captions = [
                    "📸 *¡Síguenos en Instagram!*\n\n"
                    "Publicamos resultados diarios, TPs alcanzados y contenido exclusivo.\n\n"
                    "🔗 @buysell365.pro\\_tradingsignals\n"
                    "👉 instagram.com/buysell365.pro\\_tradingsignals\n\n"
                    "_Transparencia total — resultados reales cada día_",

                    "🚀 *BuySell365 ya está en Instagram*\n\n"
                    "📊 Resultados diarios verificados\n"
                    "🎯 Celebraciones de cada TP\n"
                    "📈 Estadísticas semanales\n\n"
                    "Síguenos: @buysell365.pro\\_tradingsignals\n"
                    "👉 instagram.com/buysell365.pro\\_tradingsignals",

                    "📱 *¿Ya nos sigues en Instagram?*\n\n"
                    "Cada día publicamos nuestros resultados reales — los buenos y los malos.\n"
                    "Sin filtros, sin trucos.\n\n"
                    "🔗 @buysell365.pro\\_tradingsignals\n"
                    "_BuySell365 Pro — Transparencia total_",
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

        to_resolve = []
        _already_in_cycle = set()  # Anti-duplicado: un TP/SL por par+dir por ciclo
        for sig_id, sdata in signals_copy.items():
            signal = sdata["signal"]
            direction = signal["direction"]
            sl = signal["sl"]
            pair = signal["pair"]
            age_hours = (time.time() - sdata["sent_at"]) / 3600

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

            # FIX 2026-04-21: CIERRE FIN DE DÍA — entre 23:50 y 23:59 hora Andorra
            # cerramos toda señal con >6h de antigüedad que no haya tocado TP final.
            # Evita arrastrar huérfanas al día siguiente y "contamina" el resumen del día.
            try:
                from datetime import datetime as _dt_eod
                import pytz as _pytz_eod
                _now_and = _dt_eod.now(_pytz_eod.timezone("Europe/Andorra"))
                if _now_and.hour == 23 and _now_and.minute >= 50 and age_hours > 6:
                    # Si ya tocó TPs no urge cerrar — solo si no logró ningún TP final
                    if not signal.get("_tps_alcanzados"):
                        log.info(f"🌙 EOD cierre: {pair} {direction} ({age_hours:.1f}h sin TP)")
                        to_resolve.append((sig_id, sdata, "eod"))
                        continue
            except Exception:
                pass

            # FIX 2026-04-09: Usar precio MT5 (broker real) en vez de yfinance
            price = None
            try:
                import MetaTrader5 as _mt5_check
                # FIX 2026-04-12: Mapa COMPLETO — todos los pares que SYMBOL_MAP puede recibir
                _mt5_sym_map = {
                    # Oro
                    "GOLD": "GOLD", "XAUUSD": "GOLD", "ORO": "GOLD",
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
                        log.info(f"💹 Precio MT5 {_resolved_sym}: bid={_tick.bid:.5f} ask={_tick.ask:.5f} -> usando {price:.5f}" if price < 100 else f"💹 Precio MT5 {_resolved_sym}: bid={_tick.bid:.2f} ask={_tick.ask:.2f} -> usando {price:.2f}")
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
                _dedup_key_sl = f"{pair}_{direction}_sl"
                if _dedup_key_sl not in _already_in_cycle:
                    _already_in_cycle.add(_dedup_key_sl)
                    to_resolve.append((sig_id, sdata, "sl"))
                else:
                    with _signals_lock:
                        _open_signals.pop(sig_id, None)
                    _resolved_signals.add(sig_id)
                    log.info(f"🔕 SL duplicado en ciclo ignorado: {pair} {direction} (otro canal)")

        for sig_id, sdata_resolved, result in to_resolve:
          try:
            signal   = sdata_resolved["signal"] if isinstance(sdata_resolved, dict) and "signal" in sdata_resolved else sdata_resolved
            _reply_id = signals_copy.get(sig_id, {}).get("telegram_msg_id") if isinstance(signals_copy.get(sig_id), dict) else None
            with _signals_lock:
                _open_signals.pop(sig_id, None)
            _resolved_signals.add(sig_id)
            _save_open_signals()

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
                    # FIX 2026-04-17: Registrar TP alcanzado para que si luego toca SL
                    # el mensaje muestre "CIERRE — X TPs asegurados, neto +Y" en vez de "SL perdida"
                    _tp_precio = _tp_levels_r[_tp_idx_r] if _tp_idx_r < len(_tp_levels_r) else signal.get("tp", 0)
                    _pips_tp = abs(_tp_precio - _entry) if _entry > 0 and _tp_precio > 0 else 0
                    _tps_hechos = signal.setdefault("_tps_alcanzados", [])
                    _tps_hechos.append({"nivel": _tp_num_log, "precio": _tp_precio, "pips": _pips_tp})
                    _send_tp_celebration(signal, reply_to_msg_id=_reply_id)
                    # Si hay más niveles TP, avanzar y seguir tracking
                    if (_tp_idx_r + 1) < len(_tp_levels_r):
                        signal["_tp_idx"] = _tp_idx_r + 1
                        signal["_tp_final"] = _tp_levels_r[_tp_idx_r + 1]
                        with _signals_lock:
                            _open_signals[sig_id] = sdata_resolved
                        _resolved_signals.discard(sig_id)
                        _save_open_signals()
                        log.info(f"📈 Avanzando a TP{_tp_num_log+1} ({_tp_levels_r[_tp_idx_r+1]}) para {signal.get('pair','?')}")
                else:
                    log.info(f"🛑 SL notificado para {signal.get('pair','?')} entry={_entry}")
                    _send_sl_notification(signal, reply_to_msg_id=_reply_id)

                _record_daily_result(signal, result)
            elif result in ("expired", "orphan", "eod"):
                # FIX 2026-04-21: Antes las señales expiradas/huérfanas se eliminaban
                # silenciosamente del dict y dejaban al canal sin notificación de cierre.
                # Ahora notificamos siempre con el neto al precio actual del mercado.
                log.info(f"⏱ {result.upper()} notificado para {signal.get('pair','?')}")
                _send_expired_notification(signal, reason=result, reply_to_msg_id=_reply_id)
          except Exception as _e_resolve:
            log.error(f"❌ Error procesando {result} para {sig_id}: {_e_resolve}")


# === PARSER ===
def parse_signal(text, chat_title=""):
    """Parse trading signal from text. Returns dict or None.
    Soporta formatos: SureShotFX, Learn2Trade VIP.
    """
    try:
        return _parse_signal_impl(text, chat_title)
    except Exception as _e_parse:
        log.exception(f"parse_signal crash en [{chat_title}]: {_e_parse} | text={text[:120].replace(chr(10),' ')!r}")
        return None


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
        # NasdaqMasters / NASDaqxNinja — mensajes de celebración (NO son señales)
        "IT FLEW", "PIPS HIT", "TP CORRECTED", "1000 PIPS", "2000 PIPS", "3000 PIPS",
        "BANKED", "NAILED IT", "MASSIVE WIN", "LET IT RUN",
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
    elif "nasdaqmaster" in chat_lower or "nasdaqninja" in chat_lower or "nas100" in chat_lower or "nasdaqmaster" in text_lower or "nasdaqxninja" in text_lower:
        source = "NasdaqMasters"
    elif "toptradingsignals" in chat_lower or "top trading" in chat_lower:
        source = "TopTradingSignals"
    elif "united kings" in chat_lower or "unitedkings" in chat_lower:
        source = "UnitedKings"
    elif "prosignalsfx" in chat_lower or "prosignals fx" in chat_lower:
        source = "ProSignalsFx"

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
    # FIX 2026-04-16: incluir @ como separador válido — NasdaqMasters usa "SL @47950"
    sl_match = re.search(r'(?:SL|STOP\s*LOSS)\s*[:\s→@]*(\d{1,6}\.?\d+)', upper_clean)

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
    # ── TP2-TP5: extraer por número explícito ──
    def _extract_tp_n(n, txt):
        """Extract TPn from text using multiple patterns."""
        # Patrón 1: "TP 2: 4626" | "TP2: 4626" | "TAKE PROFIT 2: 4626"
        # FIX 2026-04-16: incluir @ como separador — NasdaqMasters usa "TP2 @48500"
        m = re.search(
            rf'(?:TOMA\s*DE\s*GANANCIAS\s*{n}\s*[:\s]+|TAKE\s*PROFIT\s*{n}\s*[:\s]+|TP\s*{n}\s*[:\s@]+|TP{n}\s*[:\s@]*)(\d+\.?\d*)',
            txt
        )
        if m: return m
        # Patrón 2: "TP 2 4626" (espacio sin :)
        m = re.search(rf'\bTP\s*{n}\s*@?\s*(\d{{1,6}}\.?\d+)', txt)
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
    """Parse signal updates (close half, move SL, etc.) — English + Spanish.

    FIX 2026-04-17: Política "correr hasta último TP":
    - TP1/TP2/TP3/TP4 HIT intermedios → action="tp_partial" (NO cierra MT5, solo celebra nivel)
    - FULL TP HIT / TP HIT sin número / último TP → action="tp_hit" (cierra MT5)
    - CLOSE ALL / EXIT NOW / CIERRE TOTAL / CERRAR TODO → action="full_close"
    """
    action = None
    tp_level = 0  # para tp_partial

    # FIX 2026-04-17: Cierre explícito ampliado (EN + ES)
    # Debe detectarse ANTES que close_half/partial para no confundir
    if any(w in upper for w in [
        "CLOSE ALL", "CLOSE FULL", "FULL CLOSE", "EXIT NOW", "EXIT ALL",
        "CLOSE POSITION", "CLOSE TRADE", "CLOSE ORDER",
        "CERRAR COMPLETAMENTE", "CIERRE TOTAL", "CERRAR TODO",
        "CERRAR POSICION", "CERRAR POSICIÓN", "CIERRE MANUAL",
        "CIERRA COMPLETA", "CIERRE COMPLETO",
    ]):
        action = "full_close"
    # Close half (EN + ES)
    elif any(w in upper for w in ["CLOSE HALF", "CIERRA LA MITAD", "CIERRE DE LA MITAD", "CIERRE MEDIO"]):
        action = "close_half"
    # Close partial (EN + ES)
    elif any(w in upper for w in ["CLOSE PARTIAL", "CIERRE PARCIAL"]):
        action = "close_partial"
    # Move SL to entry (EN + ES)
    elif any(w in upper for w in ["MOVE SL TO ENTRY", "MOVER SL A LA ENTRADA", "MOVER EL SL A LA ENTRADA", "MOVIMOS EL SL A LA ENTRADA"]):
        action = "move_sl_to_entry"
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
    }


# === MT5 CONFIG ===
MT5_DEMO_LOGIN = int(os.getenv("MT5_LOGIN", 0))
MT5_DEMO_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_DEMO_SERVER = os.getenv("MT5_SERVER", "")


def _mt5_init_and_login():
    """Initialize MT5 and login to the configured account."""
    import MetaTrader5 as mt5
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
    Retorna True si está ABIERTO (no operar)."""
    try:
        _cb_file = Path(__file__).parent / "mt5_circuit_breaker.json"
        if _cb_file.exists():
            import json as _json_cb_read
            with open(_cb_file, "r") as _f_cb_r:
                _cb = _json_cb_read.load(_f_cb_r)
            if _cb.get("state") == "OPEN":
                import time as _time_cb
                elapsed = _time_cb.time() - _cb.get("opened_at", 0)
                if elapsed < 300:  # Cooldown 5 min
                    log.warning(f"🚨 Circuit Breaker ABIERTO — MT5 pausado ({300 - elapsed:.0f}s restantes)")
                    return True
    except Exception:
        pass
    return False


def execute_in_mt5(signal):
    """Execute signal in MT5. Returns (success, detail)."""
    # Circuit Breaker: si MT5 está pausado, no intentar
    if _check_mt5_circuit_breaker():
        return False, "Circuit Breaker OPEN — MT5 pausado"

    try:
        import MetaTrader5 as mt5
    except ImportError:
        return False, "MetaTrader5 not installed"

    ok, msg = _mt5_init_and_login()
    if not ok:
        return False, msg

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
    entry = signal["entry"]
    # If entry was 0 (not in message), use current market price (sin mutar el dict original)
    if entry == 0 or entry == 0.0:
        entry = price

    # FIX 2026-04-17: TP enviado a MT5 = ÚLTIMO nivel definido, no TP1.
    # Política del usuario: la posición debe correr hasta el último TP. TP1/TP2 intermedios
    # son solo notificaciones al canal VIP; MT5 cierra solo en TP final, SL, o cierre explícito.
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
        tp = max(_tp_valid_mt5) if _is_buy_for_tp else min(_tp_valid_mt5)
    else:
        tp = signal.get("tp", 0) or 0  # fallback

    # R:R check — solo log informativo, NO rechazar (todas las señales se ejecutan)
    risk = abs(price - sl)
    if risk <= 0:
        return False, "Invalid SL"
    if tp > 0:
        reward = abs(tp - price)
        rr = reward / risk
        log.info(f"📊 R:R = {rr:.2f} para {sym} (TP final MT5={tp}, niveles canal={len(_tp_valid_mt5)})")

    # Lot = SIEMPRE el mínimo (cuenta demo, sin riesgo)
    lot = info.volume_min  # Normalmente 0.01

    # FIX 2026-04-16: Respetar tipo de orden (Limit/Stop → PENDING, Market → DEAL)
    sig_order_type = signal.get("order_type", "Market")
    sig_is_limit   = signal.get("is_limit", False)
    is_buy = signal["direction"] == "BUY"

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

    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        return True, f"Executed {signal['direction']} {sym} @ {exec_price} Lot={lot} [{sig_order_type}]"

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
            return True, f"Executed {signal['direction']} {sym} @ {exec_price} [SL/TP ajustados: {sl_adj}/{tp_adj}]"
        err = result2.comment if result2 else "No response"
        return False, f"MT5 skip (invalid stops tras ajuste): {err}"

    # FIX 2026-04-17: "Invalid price" (precio stale en Limit/Stop) — convertir a Market
    if result and "invalid price" in err.lower() and trade_action == mt5.TRADE_ACTION_PENDING:
        log.warning(f"⚠️ Invalid price {sym} en orden {sig_order_type} — convirtiendo a Market")
        request["action"] = mt5.TRADE_ACTION_DEAL
        request["type"] = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
        request["price"] = tick.ask if is_buy else tick.bid
        result3 = mt5.order_send(request)
        if result3 and result3.retcode == mt5.TRADE_RETCODE_DONE:
            return True, f"Executed {signal['direction']} {sym} @ market [fallback de {sig_order_type}]"
        err = result3.comment if result3 else "No response"
        return False, f"MT5 skip (invalid price tras fallback Market): {err}"

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
        import MetaTrader5 as mt5
        ok, msg = _mt5_init_and_login()
        if not ok:
            return False, msg

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
# FIX 2026-04-17: Circuit breaker Gemini — tras cuota agotada (429), pausar 1h para
# no seguir llamando y generar 9+ errores inútiles por día (como hoy).
_gemini_cb_until: float = 0
_gemini_cb_duration = 3600  # 1h silencio tras 429


def _ia_evaluar_senal(signal):
    """IA evalúa si la señal es buena antes de ejecutar. Retorna (aprobar, comentario).
    Con circuit breaker silencioso para 429: si Gemini está agotado, no bloquea flujo."""
    global _gemini_cb_until
    if not _GEMINI_KEY:
        return True, ""
    # Circuit breaker activo → saltar Gemini sin ruido
    if time.time() < _gemini_cb_until:
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
        _errstr = str(e)
        if "429" in _errstr or "quota" in _errstr.lower() or "rate limit" in _errstr.lower():
            _gemini_cb_until = time.time() + _gemini_cb_duration
            log.warning(f"🚨 Gemini 429 cuota agotada — circuit breaker 1h activado (se saltará IA eval)")
        else:
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
        # FIX 2026-04-14: Labels en español con pips dinámicos del canal aliado
        _pips = signal.get("pips_profit", 0)
        _unit = "pts" if _pair in ("GOLD", "XAUUSD", "XAUUSD=X") or _pair_d in ("GOLD", "US30", "NAS100", "S&P 500") else "pips"
        # Calcular pips desde _open_signals si no vienen del canal aliado
        if _pips <= 0:
            with _signals_lock:
                for _sid, _sdata in _open_signals.items():
                    _s = _sdata.get("signal", {})
                    if _s.get("pair") == _pair or _s.get("mt5_symbol") == _pair:
                        _entry_sig = _s.get("entry", 0)
                        if _entry_sig > 0:
                            _live_p = _get_current_price(_pair)
                            if _live_p and _live_p > 0:
                                _raw_diff = abs(_live_p - _entry_sig)
                                if _entry_sig >= 100:
                                    _pips = round(_raw_diff, 1)
                                elif "JPY" in _pair.upper():
                                    _pips = round(_raw_diff * 100)
                                else:
                                    _pips = round(_raw_diff * 10000)
                        break
        _pips_txt = f"+{_pips} {_unit}" if _pips > 0 else ""

        # FIX 2026-04-14: Detectar volatilidad real desde MT5 (spread + ATR)
        _es_volatil = False
        try:
            import MetaTrader5 as _mt5_vol
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

        # Razón del cierre según volatilidad real
        if _es_volatil:
            _razon_half = "Mercado volátil, protegemos ganancia cerrando la mitad."
            _razon_partial = "Mercado volátil, protegemos ganancia cerrando parte."
        else:
            _razon_half = "Protegemos ganancia cerrando la mitad."
            _razon_partial = "Protegemos ganancia cerrando parte."

        # FIX 2026-04-17: tp_partial = TP intermedio → la posición sigue corriendo
        _tp_lvl = signal.get("tp_level", 0) if isinstance(signal, dict) else 0
        _tp_partial_msg = (
            f"🎯 *TP{_tp_lvl} ALCANZADO* — {_pair_d}\n"
            f"✅ Nivel asegurado. La operación *sigue corriendo* hasta el próximo objetivo."
        )
        if _pips_txt:
            _action_labels = {
                "close_half":       f"⚡ *CIERRE PARCIAL 50%* — {_pair_d}\n💰 *{_pips_txt}* asegurados. {_razon_half}",
                "close_partial":    f"⚡ *CIERRE PARCIAL* — {_pair_d}\n💰 *{_pips_txt}* asegurados. {_razon_partial}",
                "full_close":       f"🔒 *CIERRE TOTAL* — {_pair_d}\n✅ *{_pips_txt}* de ganancia. Operación finalizada.",
                "move_sl_to_entry": f"🛡️ *SL A ENTRADA* — {_pair_d}\n🔐 Protegemos la operación. Ya no hay riesgo de pérdida.",
                "sl_hit":           f"🛑 *SL TOCADO* — {_pair_d}",
                "tp_hit":           f"✅ *TP ALCANZADO* — {_pair_d}",
                "tp_partial":       _tp_partial_msg + (f"\n💰 *{_pips_txt}* en este nivel." if _pips_txt else ""),
            }
        else:
            _action_labels = {
                "close_half":       f"⚡ *CIERRE PARCIAL 50%* — {_pair_d}\n💰 {_razon_half}",
                "close_partial":    f"⚡ *CIERRE PARCIAL* — {_pair_d}\n💰 {_razon_partial}",
                "full_close":       f"🔒 *CIERRE TOTAL* — {_pair_d}\n✅ Cerramos toda la posición. Operación finalizada.",
                "move_sl_to_entry": f"🛡️ *SL A ENTRADA* — {_pair_d}\n🔐 Protegemos la operación. Ya no hay riesgo de pérdida.",
                "sl_hit":           f"🛑 *SL TOCADO* — {_pair_d}",
                "tp_hit":           f"✅ *TP ALCANZADO* — {_pair_d}",
                "tp_partial":       _tp_partial_msg,
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
            # FIX 2026-04-14: Deduplicar updates — cooldown 5 min por acción+par
            _upd_key = f"upd_{_action}_{_pair}"
            _upd_now = time.time()
            if _upd_key in _recently_notified and (_upd_now - _recently_notified[_upd_key]) < 300:
                log.info(f"🔕 Update '{_action}' {_pair_d} ignorado — ya enviado hace {_upd_now - _recently_notified[_upd_key]:.0f}s (cooldown 5min)")
                return None
            _recently_notified[_upd_key] = _upd_now
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
                # FIX 2026-04-15: Registrar cierre con pips en estadísticas persistentes
                # FIX 2026-04-16: Celebrar cierres con ganancia (video grupo + Instagram)
                if _action in ("close_half", "close_partial", "full_close") and _pips > 0:
                    _sig_dir = ""
                    _sig_src = ""
                    _sig_entry = 0
                    with _signals_lock:
                        for _sid, _sdata in _open_signals.items():
                            _s = _sdata.get("signal", {})
                            if _s.get("pair") == _pair or _s.get("mt5_symbol") == _pair:
                                _sig_dir = _s.get("direction", "")
                                _sig_src = _s.get("source", "")
                                _sig_entry = _s.get("entry", 0) or 0
                                break
                    _record_close_result(_pair, _action, _pips, direction=_sig_dir, source=_sig_src)
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
                            import MetaTrader5 as _mt5_check
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
                        try:
                            _send_tp_celebration(_tp_signal_data, reply_to_msg_id=_tp_reply_id)
                            _record_daily_result(_tp_signal_data, "tp")
                            log.info(f"🎯 TP celebrado desde update canal VIP: {_pair} (MT5 profit=${_mt5_profit:.2f} ✅)")
                        except Exception as _tp_upd_err:
                            log.debug(f"TP celebration from channel update error: {_tp_upd_err}")
                    elif _tp_signal_data and not _mt5_verified:
                        log.warning(f"🚫 TP celebración BLOQUEADA ({_pair}) — no se ejecutó en MT5 o profit≤0 (evita TP falso)")
                    else:
                        log.info(f"🔕 tp_hit celebración skipped — no signal data for {_pair}")
                # FIX 2026-04-17: tp_partial (TP intermedio) → avanzar _tp_idx y registrar
                # en stats sin limpiar la señal. La posición MT5 sigue corriendo.
                if _action == "tp_partial":
                    _tp_lvl_adv = signal.get("tp_level", 0) if isinstance(signal, dict) else 0
                    with _signals_lock:
                        for _sid_p, _sdata_p in _open_signals.items():
                            _s_p = _sdata_p.get("signal", {})
                            if _s_p.get("pair") == _pair or _s_p.get("mt5_symbol") == _pair:
                                if _tp_lvl_adv > 0:
                                    _s_p["_tp_idx"] = max(_s_p.get("_tp_idx", 0), _tp_lvl_adv)
                                _tps_ok = _s_p.setdefault("_tps_alcanzados", [])
                                _tps_ok.append({"nivel": _tp_lvl_adv, "pips": _pips})
                                break
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
    source     = signal.get("source", "Externa")
    order_type = signal.get("order_type", "Market")
    is_limit   = signal.get("is_limit", False)
    rrr        = signal.get("rrr", "")
    style      = signal.get("style", "")

    # FIX 2026-04-14: Señales en español
    dir_label = "BUY" if direction.upper() == "BUY" else "SELL"
    dir_emoji = "🟢" if direction == "BUY" else "🔴"
    src_emoji = {
        "SureShotFX":         "📡",
        "Learn2Trade":        "📊",
        "FXPremiere":         "🔔",
        "GoldForexMarket":    "🥇",
        "NasdaqMasters":      "📈",
        "TopTradingSignals":  "🎯",
        "UnitedKings":        "👑",
        "ProSignalsFx":       "🐻",
    }.get(source, "🔔")

    pair_display = _get_display_pair(pair)

    # Tipo de orden en español
    tipo_label = {"Market": "Mercado", "Limit": "Orden Límite", "Stop": "Orden Stop"}.get(order_type, order_type)

    # Formato de precios
    fmt = fmt_price

    entry_display = fmt(entry) if entry > 0 else "Precio de Mercado"

    tp2 = signal.get("tp2", 0) or 0
    tp3 = signal.get("tp3", 0) or 0
    tp4 = signal.get("tp4", 0) or 0
    tp5 = signal.get("tp5", 0) or 0
    has_multi_tp = any(t > 0 for t in [tp2, tp3, tp4, tp5])
    tp_label = "TP1" if has_multi_tp else "TP"

    lines = [
        f"{dir_emoji} *{dir_label} — {pair_display}*",
        f"",
        f"📍 Entrada: {entry_display}",
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

    # Botones de afiliado XM + ANÁLISIS por petición — debajo de cada señal nueva
    # FIX 2026-04-21: Botones de análisis abren DM con el bot vía deep link
    # (los users del canal pulsan el botón → se abre el bot en privado y reciben el análisis)
    _BOT_USERNAME = os.getenv("BOT_USERNAME", "Andoperandobot")
    # Sugerir activos relevantes según el par publicado, más opciones generales
    _pair_upper = (pair_display or pair or "").upper().replace("/", "")
    _btn_pair_norm = "ORO" if _pair_upper in ("ORO", "GOLD", "XAUUSD") else _pair_upper
    # FIX 2026-04-21: Botón Copy Trading oculto (servicio pausado)
    _btn_rows = [
        # Fila 1: análisis del activo señalado + ORO siempre disponible
        [
            {"text": f"🔍 Análisis {_btn_pair_norm}", "url": f"https://t.me/{_BOT_USERNAME}?start=analisis_{_btn_pair_norm.lower()}"},
            {"text": "🥇 Análisis ORO", "url": f"https://t.me/{_BOT_USERNAME}?start=analisis_oro"},
        ],
        # Fila 2: análisis populares
        [
            {"text": "📈 NASDAQ", "url": f"https://t.me/{_BOT_USERNAME}?start=analisis_nasdaq"},
            {"text": "📊 S&P 500", "url": f"https://t.me/{_BOT_USERNAME}?start=analisis_sp500"},
            {"text": "🛢 Petróleo", "url": f"https://t.me/{_BOT_USERNAME}?start=analisis_usoil"},
        ],
        # Fila 3: pedir cualquier otro activo
        [
            {"text": "💬 Pedir análisis de otro activo", "url": f"https://t.me/{_BOT_USERNAME}?start=analisis"},
        ],
    ]
    _xm_buttons = {"inline_keyboard": _btn_rows}

    # FIX 2026-04-08: Gráfica SOLO en TP/SL HIT, NO en señales nuevas
    # Las señales nuevas solo llevan texto + botones de análisis

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
                log.info(f"🎯 Señal registrada para seguimiento: {sig_id} (msg_id={_canal_msg_id})")
                _save_open_signals()  # Persistir a disco

                # ── SEÑAL REGALO al grupo público (2/día: 1 oro + 1 otra) ──
                try:
                    if GROUP_ID and _should_gift_signal(pair):
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
                            # Marcar en _open_signals para que el grupo reciba la celebración
                            with _signals_lock:
                                if sig_id in _open_signals:
                                    _open_signals[sig_id]["gifted"] = True
                            _save_open_signals()
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
        "nas100group",                # NasdaqMasters / NASDaqxNinja's TRADES — US30 + NASDAQ (agregado 2026-04-16)
        "top_tradingsignals",         # TopTradingSignals — Forex + Gold + Indexes (agregado 2026-04-19)
        "topforexsignals",            # TopTradingSignals alias (por si falla el primero)
        "unitedkings1",               # United Kings Signals — XAUUSD/Gold commentary (agregado 2026-04-19)
        "prosignalsfxx",              # ProSignalsFx — Gold + Forex diario (agregado 2026-04-19)
    ]
    # Lista global de pares permitidos (aplica a TODOS los canales)
    # FIX 2026-04-16: Añadido US30/DOW para soporte canal NasdaqMasters
    ALLOWED_PAIRS = {"GOLD", "XAUUSD", "XAUUSD=X", "ORO",
                     "NAS100", "NASDAQ", "NASDAQ100", "US100", "US100Cash", "NQ",
                     "US30", "DOW", "DJ30", "US30Cash"}

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
        "nasdaqmasters", "nasdaq masters", "nasdaqninja", "nasdaq ninja",  # NasdaqMasters (nas100group)
        "nas100", "nas 100",
        "top_tradingsignals", "toptradingsignals", "top trading signals",  # TopTradingSignals (2026-04-19)
        "unitedkings", "united kings",                                     # United Kings (2026-04-19)
        "prosignalsfx", "pro signals fx",                                  # ProSignalsFx (2026-04-19)
    ]
    SIGNAL_KEYWORDS = ["sureshot", "learn", "fxpremiere", "anabel", "gold forex",
                       "nasdaqmasters", "nasdaqninja",
                       "toptradingsignals", "unitedkings", "prosignalsfx"]

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

            # FIX 2026-04-09: TODAS las señales pasan — sin filtro por activo
            # Updates (close_half, sl_hit, tp_hit) se filtran más abajo por _open_signals
            if not signal:
                return

            log.info(f"📡 SEÑAL DETECTADA en [{chat.title}]: {signal.get('direction', signal.get('action', '?'))} {signal['pair']}")

            # ── Deduplicación: evitar misma señal de CUALQUIER canal ──
            # FIX 2026-04-09: Doble check — por par+dirección+precio Y por par solo (cooldown 10min)
            # FIX 2026-04-21: TTLs ampliados para frenar duplicados exactos como
            # USD/JPY 19:36 + 21:18 con misma entrada 158.75 (1h42min de separación)
            if signal["type"] == "new_signal":
                _entry_round = round(signal.get("entry", 0), 2)
                _dedup_key = f"{signal['pair']}_{signal['direction']}_{_entry_round}"
                _pair_key = f"{signal['pair']}_{signal['direction']}"
                # Check 1: _recently_sent — mismo par+dirección+precio exacto (4h)
                _prev_sent_time = _recently_sent.get(_dedup_key, 0)
                if _prev_sent_time and (time.time() - _prev_sent_time) < 14400:
                    log.info(f"⏭️ Señal duplicada ignorada (cache): {_dedup_key} (enviada hace {(time.time() - _prev_sent_time):.0f}s)")
                    return
                # Check 2: cooldown por PAR+DIRECCIÓN+CANAL — máx 1 señal cada 30 min del mismo par POR CANAL
                # FIX 2026-04-16: incluir canal en la clave para no bloquear señales de distintos canales
                _pair_key_with_ch = f"{_pair_key}_{chat.title}"
                _prev_pair_time = _recently_sent.get(_pair_key_with_ch, 0)
                if _prev_pair_time and (time.time() - _prev_pair_time) < 1800:
                    log.info(f"⏭️ Cooldown activo: {_pair_key_with_ch} (última hace {(time.time() - _prev_pair_time):.0f}s < 1800s)")
                    return
                # Check 3: _open_signals — ya hay señal abierta del mismo par+dirección
                # FIX 2026-04-21: Si existe una abierta con MISMA entry (round 2 dec), bloquear
                # SIN límite de tiempo (es literalmente la misma señal). Si entry distinto, mantener
                # cooldown 1h como antes para no bloquear giros legítimos del mercado.
                with _signals_lock:
                    for _sid, _sdata in _open_signals.items():
                        _s = _sdata.get("signal", {})
                        _existing_pair_key = f"{_s.get('pair','')}_{_s.get('direction','')}"
                        if _existing_pair_key != _pair_key:
                            continue
                        _existing_entry = round(_s.get("entry", 0) or 0, 2)
                        if _existing_entry == _entry_round and _entry_round > 0:
                            log.info(f"⏭️ Señal duplicada exacta ignorada: {_pair_key} entry={_entry_round} ya abierta")
                            return
                        if (time.time() - _sdata.get("sent_at", 0)) < 3600:
                            log.info(f"⏭️ Señal ignorada: ya hay {_pair_key} abierta (últimos 60 min)")
                            return

            # ══════════════════════════════════════════════════════════════
            # 🟢 MT5 EXECUTION ACTIVADO — Cuenta DEMO (101595184)
            # Autorizado por el usuario 2026-04-15
            # Cada señal del canal VIP se replica con lotaje mínimo (0.01)
            # Para desactivar: cambiar a False
            # ══════════════════════════════════════════════════════════════
            MT5_EXECUTION_ENABLED = True  # ← Activado para cuenta demo

            if signal["type"] == "new_signal":
                # Registrar msg_id para manejar ediciones futuras
                msg_id = event.message.id

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
                            import MetaTrader5 as _mt5
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

                # PASO 2: Validar SL vs entry (antes de ejecutar)
                _e = signal.get("entry", 0)
                _s = signal.get("sl", 0)
                _d = signal.get("direction", "")
                if _e > 0 and _s > 0:
                    # SELL: SL debe estar ARRIBA del entry | BUY: SL debe estar ABAJO
                    if _d == "SELL" and _s < _e:
                        log.warning(f"⚠️ SELL con SL({_s}) < entry({_e}) — señal inválida, descartando")
                        return
                    if _d == "BUY" and _s > _e:
                        log.warning(f"⚠️ BUY con SL({_s}) > entry({_e}) — señal inválida, descartando")
                        return

                # PASO 3: Validar TPs (antes de ejecutar)
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
                    # Si TP principal fue limpiado, la señal no tiene sentido — descartar
                    if (signal.get("tp", 0) or 0) <= 0 and all((signal.get(f"tp{i}", 0) or 0) <= 0 for i in range(2, 6)):
                        log.warning(f"⚠️ Todos los TPs inválidos para {_d} {signal.get('pair','')} entry={_e} — descartando señal")
                        return

                # PASO 4: Validar entry vs precio actual (previene entries stale)
                if not _validate_entry_vs_market(signal):
                    log.warning(f"🚫 Señal {signal.get('pair','?')} descartada — entry stale >1.5% del mercado actual")
                    return

                # PASO 5: Ejecutar en MT5 (solo si TODAS las validaciones pasaron)
                executed, detail = False, "Ejecución MT5 desactivada (kill-switch activo)"
                if MT5_EXECUTION_ENABLED:
                    aprobar, ia_comment = _ia_evaluar_senal(signal)
                    signal["ia_comment"] = ia_comment
                    if ia_comment:
                        log.info(f"🤖 IA: {ia_comment}")
                    executed, detail = execute_in_mt5(signal)
                    log.info(f"📡 MT5: {'✅' if executed else '❌'} {detail}")

                # Publicar al canal VIP
                send_to_channel(signal, executed, detail)
                # FIX 2026-04-09: Registrar en cache anti-duplicados + cooldown por par
                # FIX 2026-04-16: cooldown incluye canal (no bloquear canales distintos)
                _entry_r = round(signal.get("entry", 0), 2)
                _dk = f"{signal['pair']}_{signal['direction']}_{_entry_r}"
                _pk = f"{signal['pair']}_{signal['direction']}"
                _pk_ch = f"{_pk}_{chat.title}"
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
            _pair_cooldown_key = f"{_sig_pair}_{_sig_dir}"
            _prev_time = _recently_sent.get(_pair_cooldown_key, 0)
            if _prev_time and (time.time() - _prev_time) < 600:
                log.info(f"✏️ Edit ignorado — cooldown activo: {_pair_cooldown_key} (hace {time.time()-_prev_time:.0f}s)")
                _published_msg_ids.add(msg_id)
                return

            log.info(f"✏️ Señal capturada vía edición (msg_id={msg_id}): {signal.get('direction')} {signal.get('pair')}")
            # FIX 2026-04-17: Validar entry vs precio actual — DESCARTAR si stale (>1.5%)
            if not _validate_entry_vs_market(signal):
                log.warning(f"🚫 Edit {signal.get('pair','?')} descartado — entry stale (señal vieja, no publicable)")
                _published_msg_ids.add(msg_id)
                return
            # FIX 2026-04-15: No publicar si entry=0 (sin precio)
            if (signal.get("entry", 0) or 0) <= 0:
                log.warning(f"✏️ Edit sin entry resuelto — NO publicando {signal.get('pair','?')}")
                return
            # FIX 2026-04-16 BUG#2: edit_handler ahora ejecuta en MT5 igual que el handler principal
            MT5_EXECUTION_ENABLED = True  # ← Activado (era False → señales vía edición nunca llegaban a MT5)
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
            _entry_r = round(signal.get("entry", 0), 2)
            _dk = f"{signal['pair']}_{signal['direction']}_{_entry_r}"
            _recently_sent[_dk] = time.time()
            _published_msg_ids.add(msg_id)

        except Exception as e:
            log.exception(f"Error en edit_handler: {e}")

    log.info("📡 Signal Copier iniciando...")
    log.info(f"📡 API ID: {API_ID}")
    log.info(f"📡 Phone: {PHONE}")

    # Cargar señales abiertas de la sesión anterior (sobreviven reinicios)
    _load_open_signals()
    # FIX 2026-04-17: Cargar gift_tracker para no regalar múltiples oros por reinicio
    _load_gift_tracker()

    # FIX 2026-04-15: Restaurar estadísticas del día actual desde disco
    _today_stats = _load_copier_stats_today()
    if _today_stats:
        with _daily_results_lock:
            _daily_results.extend(_today_stats)
        log.info(f"📊 {len(_today_stats)} resultados de hoy restaurados desde copier_stats.json")

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
    # FIX 2026-04-16 BUG#7: Cerrar conexión MT5 limpiamente al salir
    try:
        import MetaTrader5 as _mt5_final
        if _mt5_final.terminal_info() is not None:
            _mt5_final.shutdown()
            log.info("📡 MT5 desconectado limpiamente.")
    except Exception:
        pass

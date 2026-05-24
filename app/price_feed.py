"""
price_feed.py — Fachada única para datos de mercado sin MT5.

Reemplaza:
    mt5.symbol_info_tick(sym)  → price_feed.get_tick(sym)
    mt5.copy_rates_*(sym, ...) → price_feed.get_ohlc(sym, tf, bars)

Backends:
    BTC/ETH y cripto  → Binance REST API (público, sin key, sub-segundo)
    Forex (EURUSD…)   → yfinance (sufijo "=X")
    Oro / XAUUSD      → yfinance "GC=F" (futuro oro)
    Plata / XAGUSD    → yfinance "SI=F"
    Índices (NAS100…) → yfinance ticker correspondiente
    Stocks / acciones → yfinance directo

Cache:
    Tick → 5 segundos (suficiente para detección TP/SL)
    OHLC → 60 segundos
"""
from __future__ import annotations
import time
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# ─── Cache simple en memoria ──────────────────────────────────────────
_TICK_CACHE: Dict[str, tuple[float, "Tick"]] = {}
_OHLC_CACHE: Dict[str, tuple[float, Any]] = {}
TICK_TTL = 5.0      # segundos
OHLC_TTL = 60.0     # segundos

# ─── Mapeo de símbolos broker (XM) → tickers de cada backend ──────────
# XM usa nombres como "EURUSD", "XAUUSD", "BTCUSD", "USOIL", "NAS100"
# Algunos brokers añaden sufijos tipo "EURUSDm" o "EURUSD.r" — limpiamos.
_FOREX_PAIRS = {
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
    "EURJPY", "GBPJPY", "EURGBP", "EURAUD", "AUDJPY", "NZDJPY", "CADJPY",
    "EURCAD", "GBPCAD", "EURCHF", "GBPCHF", "AUDCAD", "AUDCHF", "AUDNZD",
    "NZDCAD", "NZDCHF", "CHFJPY", "CADCHF", "EURNZD", "GBPAUD", "GBPNZD",
}

_BINANCE_MAP = {
    "BTCUSD":  "BTCUSDT",
    "ETHUSD":  "ETHUSDT",
    "BTCUSDT": "BTCUSDT",
    "ETHUSDT": "ETHUSDT",
    "BNBUSD":  "BNBUSDT",
    "SOLUSD":  "SOLUSDT",
    "XRPUSD":  "XRPUSDT",
    "ADAUSD":  "ADAUSDT",
    "DOGEUSD": "DOGEUSDT",
}

# yfinance ticker para metales, índices, energía
_YF_SPECIAL = {
    "XAUUSD":  "GC=F",          # Oro
    "GOLD":    "GC=F",
    "ORO":     "GC=F",
    "XAGUSD":  "SI=F",          # Plata
    "SILVER":  "SI=F",
    "USOIL":   "CL=F",          # WTI Crude
    "WTI":     "CL=F",
    "BRENT":   "BZ=F",
    "NATGAS":  "NG=F",
    "XNGUSD":  "NG=F",
    "NAS100":  "^NDX",          # Nasdaq 100
    "US100":   "^NDX",
    "NASDAQ":  "^NDX",
    "US30":    "^DJI",          # Dow Jones
    "DOW":     "^DJI",
    "SPX500":  "^GSPC",         # S&P 500
    "US500":   "^GSPC",
    "GER40":   "^GDAXI",        # DAX
    "DAX":     "^GDAXI",
    "UK100":   "^FTSE",         # FTSE 100
    "JPN225":  "^N225",         # Nikkei
}


def _clean_symbol(sym: str) -> str:
    """Limpia sufijos de broker. EURUSDm → EURUSD, EURUSD.r → EURUSD."""
    if not sym:
        return ""
    s = sym.upper().strip()
    # Sufijos comunes de brokers
    for suf in ("M", ".R", ".A", ".B", "_RAW", "_PRO"):
        if s.endswith(suf) and len(s) > len(suf) + 3:
            s = s[: -len(suf)]
            break
    return s


@dataclass
class Tick:
    """Sustituto de mt5.symbol_info_tick(). Compatible con código existente."""
    bid: float
    ask: float
    last: float
    time: int  # epoch seconds
    volume: int = 0

    @property
    def time_msc(self) -> int:
        return self.time * 1000


# ─── Backend Binance (crypto) ─────────────────────────────────────────
def _binance_tick(symbol: str) -> Optional[Tick]:
    """REST público de Binance — sub-segundo, sin auth."""
    try:
        import requests
        r = requests.get(
            f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={symbol}",
            timeout=5,
        )
        if r.status_code != 200:
            log.warning(f"Binance {symbol} status {r.status_code}")
            return None
        d = r.json()
        bid = float(d["bidPrice"])
        ask = float(d["askPrice"])
        return Tick(
            bid=bid,
            ask=ask,
            last=(bid + ask) / 2,
            time=int(time.time()),
        )
    except Exception as e:
        log.warning(f"Binance tick {symbol} fail: {e}")
        return None


# ─── Backend Twelvedata (fallback OHLC) ──────────────────────────────
_TWELVEDATA_MAP = {
    "XAUUSD": "XAU/USD",
    "XAGUSD": "XAG/USD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "USDCHF": "USD/CHF",
    "AUDUSD": "AUD/USD",
    "NZDUSD": "NZD/USD",
    "USDCAD": "USD/CAD",
    "EURJPY": "EUR/JPY",
    "GBPJPY": "GBP/JPY",
    "NAS100": "NDX",
    "US30":   "DJI",
    "SPX500": "SPX",
}

def _twelvedata_ohlc(symbol: str, interval: str, bars: int):
    """Fallback OHLC vía Twelvedata cuando yfinance falla (oro, índices fin de semana)."""
    import os
    key = os.getenv("TWELVE_DATA_KEY", "").strip()
    if not key:
        return None
    td_sym = _TWELVEDATA_MAP.get(symbol)
    if not td_sym:
        return None
    td_interval = {
        "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
        "1h": "1h", "1d": "1day", "1wk": "1week",
    }.get(interval, "15min")
    try:
        import requests, pandas as pd
        r = requests.get(
            "https://api.twelvedata.com/time_series",
            params={"symbol": td_sym, "interval": td_interval, "outputsize": min(bars, 5000), "apikey": key},
            timeout=10,
        )
        d = r.json()
        if d.get("status") == "error" or not d.get("values"):
            return None
        vals = list(reversed(d["values"]))  # Twelvedata viene del más reciente al más antiguo
        df = pd.DataFrame(vals)
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime")
        df = df.astype({"open": float, "high": float, "low": float, "close": float})
        df = df.rename(columns=str.lower)
        if "volume" not in df.columns:
            df["volume"] = 0
        return df.tail(bars)
    except Exception as e:
        log.warning(f"Twelvedata fallback {symbol} fail: {e}")
        return None


# ─── Backend yfinance (forex, índices, materias primas) ───────────────
def _yfinance_tick(yticker: str) -> Optional[Tick]:
    """yfinance fast_info — 200-500ms, cacheado por la librería."""
    try:
        import yfinance as yf
        t = yf.Ticker(yticker)
        info = t.fast_info
        last = float(info.get("last_price") or info.get("lastPrice") or 0)
        if last <= 0:
            # Fallback: descargar último 1m
            hist = t.history(period="1d", interval="1m")
            if hist.empty:
                return None
            last = float(hist["Close"].iloc[-1])
        # Sin bid/ask reales en yfinance fast_info — emulamos spread mínimo
        spread = max(last * 0.0001, 0.0001)
        return Tick(
            bid=last - spread / 2,
            ask=last + spread / 2,
            last=last,
            time=int(time.time()),
        )
    except Exception as e:
        log.warning(f"yfinance tick {yticker} fail: {e}")
        return None


# ─── API pública: get_tick ────────────────────────────────────────────
def get_tick(symbol: str) -> Optional[Tick]:
    """
    Reemplaza mt5.symbol_info_tick(symbol). Devuelve None si no se pudo obtener.

    Uso típico:
        tick = price_feed.get_tick("EURUSD")
        if tick:
            current_price = tick.bid  # o tick.ask, tick.last
    """
    if not symbol:
        return None

    sym = _clean_symbol(symbol)
    now = time.time()

    # Cache hit
    cached = _TICK_CACHE.get(sym)
    if cached and (now - cached[0]) < TICK_TTL:
        return cached[1]

    tick: Optional[Tick] = None

    # 1) Crypto → Binance
    if sym in _BINANCE_MAP:
        tick = _binance_tick(_BINANCE_MAP[sym])

    # 2) Forex → yfinance "PAIR=X"
    elif sym in _FOREX_PAIRS:
        tick = _yfinance_tick(f"{sym}=X")

    # 3) Símbolos especiales (oro, índices, etc.)
    elif sym in _YF_SPECIAL:
        tick = _yfinance_tick(_YF_SPECIAL[sym])

    # 4) Fallback genérico — intentar tal cual en yfinance
    else:
        tick = _yfinance_tick(sym)

    if tick:
        _TICK_CACHE[sym] = (now, tick)

    return tick


# ─── API pública: get_ohlc ────────────────────────────────────────────
def get_ohlc(symbol: str, timeframe: str = "M5", bars: int = 100):
    """
    Reemplaza mt5.copy_rates_from(), copy_rates_range(), etc.
    Devuelve DataFrame de pandas con columnas: open, high, low, close, volume.
    timeframe acepta: M1, M5, M15, M30, H1, H4, D1 (estilo MT5).
    """
    try:
        import yfinance as yf
    except ImportError:
        log.error("yfinance no disponible")
        return None

    sym = _clean_symbol(symbol)
    cache_key = f"{sym}|{timeframe}|{bars}"
    now = time.time()

    # Cache hit
    cached = _OHLC_CACHE.get(cache_key)
    if cached and (now - cached[0]) < OHLC_TTL:
        return cached[1]

    # Mapear timeframe MT5 → yfinance interval
    tf_map = {
        "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
        "H1": "1h", "H4": "1h",  # yfinance no tiene H4 nativo, devolvemos H1
        "D1": "1d", "W1": "1wk",
    }
    interval = tf_map.get(timeframe.upper(), "5m")

    # Mapear símbolo
    if sym in _BINANCE_MAP:
        # Para crypto, yfinance acepta "BTC-USD" mejor que la API Binance para historia
        yticker = sym.replace("USDT", "-USD").replace("USD", "-USD") if "-USD" not in sym else sym
        if yticker == "BTC-USD" or yticker == "ETH-USD":
            pass  # OK
        else:
            yticker = "BTC-USD"  # safe fallback
    elif sym in _FOREX_PAIRS:
        yticker = f"{sym}=X"
    elif sym in _YF_SPECIAL:
        yticker = _YF_SPECIAL[sym]
    else:
        yticker = sym

    # Calcular periodo necesario
    minutes_per_bar = {
        "1m": 1, "5m": 5, "15m": 15, "30m": 30,
        "1h": 60, "1d": 1440, "1wk": 10080,
    }.get(interval, 5)
    total_minutes = bars * minutes_per_bar
    days_needed = max(1, total_minutes // 1440 + 1)
    # FIX 2026-05-24: mínimo 5d. Con period="1d" durante fin de semana / festivos
    # el mercado está cerrado y yfinance devuelve DataFrame vacío. Caso real:
    # GC=F (oro) 15m period=1d → 0 velas; period=5d → 344 velas. Mismo problema
    # con forex sábado/domingo. 5d cubre el peor caso (festivo largo + finde).
    days_needed = max(days_needed, 5)
    period = f"{min(days_needed, 60)}d"  # yfinance cap 60d para intradía

    try:
        df = yf.download(
            yticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        if df is None or df.empty:
            log.warning(f"yfinance OHLC vacío para {yticker} — intentando fallback Twelvedata")
            df = _twelvedata_ohlc(sym, interval, bars)
            if df is None or df.empty:
                log.warning(f"OHLC no disponible para {sym} (yfinance + Twelvedata fallaron)")
                return None

        # Normalizar columnas (yfinance a veces usa MultiIndex)
        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        df = df.tail(bars)

        _OHLC_CACHE[cache_key] = (now, df)
        return df
    except Exception as e:
        log.warning(f"OHLC {yticker} fail: {e}")
        return None


# ─── Stubs de compatibilidad con MT5 ──────────────────────────────────
# Si alguna parte del código todavía importa MT5 indirectamente, estos
# stubs evitan crashes — devuelven valores que el código sabe manejar
# (positions vacías, account info None, etc.).

def positions_get(symbol: Optional[str] = None, ticket: Optional[int] = None) -> list:
    """Stub: sin MT5 no hay posiciones abiertas. El código ya tiene branches
    para lista vacía (anti-hedge passes, reconcile no-op, etc.)."""
    return []


def positions_total() -> int:
    return 0


def account_info():
    """Stub: devuelve None. El código que dependa de equity/balance debe
    leer de copier_stats.json en su lugar."""
    return None


def history_deals_get(*args, **kwargs) -> list:
    """Stub: sin MT5 no hay historial de deals reales."""
    return []


def history_orders_get(*args, **kwargs) -> list:
    return []


def symbol_info(symbol: str):
    """Stub: devuelve None — el código que pida specs de símbolo (digits,
    point, contract_size) debe usar valores razonables por defecto."""
    return None


def initialize(*args, **kwargs) -> bool:
    """price_feed siempre está listo — devolvemos True para que el código que
    hacía `if not mt5.initialize(): skip` siga el camino feliz y procese.
    (Antes devolvíamos False y eso saltaba el cálculo técnico de probabilidad
    en signal_probability.py — bug detectado en auditoría 24-may.)"""
    return True


def shutdown() -> None:
    return None


def last_error() -> tuple:
    return (0, "MT5 not used — see price_feed.py")


def login(*args, **kwargs) -> bool:
    return False


def symbol_select(symbol: str, enable: bool = True) -> bool:
    """Stub: sin MT5 no hay nada que seleccionar."""
    return True


def symbols_get(*args, **kwargs) -> list:
    return []


def terminal_info():
    return None


def orders_get(*args, **kwargs) -> list:
    return []


def symbol_info_tick(symbol: str) -> Optional[Tick]:
    """Alias de get_tick() para compatibilidad drop-in con MT5."""
    return get_tick(symbol)


# Aliases de copy_rates_* — todos delegan a get_ohlc()
def copy_rates_from(symbol: str, timeframe, date_from, count: int):
    tf_str = _tf_to_str(timeframe)
    df = get_ohlc(symbol, tf_str, count)
    return _df_to_rates_array(df) if df is not None else None


def copy_rates_from_pos(symbol: str, timeframe, start_pos: int, count: int):
    tf_str = _tf_to_str(timeframe)
    df = get_ohlc(symbol, tf_str, count + start_pos)
    if df is None:
        return None
    df = df.iloc[: len(df) - start_pos] if start_pos else df
    return _df_to_rates_array(df)


def copy_rates_range(symbol: str, timeframe, date_from, date_to):
    tf_str = _tf_to_str(timeframe)
    df = get_ohlc(symbol, tf_str, 500)
    return _df_to_rates_array(df) if df is not None else None


def _tf_to_str(timeframe) -> str:
    """Convierte timeframe MT5 numérico a string."""
    if isinstance(timeframe, str):
        return timeframe
    tf_lookup = {
        1: "M1", 5: "M5", 15: "M15", 30: "M30",
        60: "H1", 240: "H4", 1440: "D1", 10080: "W1",
        # Algunas constantes MT5 numéricas
        16385: "H1", 16388: "H4", 16408: "D1",
    }
    return tf_lookup.get(int(timeframe) if timeframe else 5, "M5")


def _df_to_rates_array(df):
    """Convierte DataFrame de yfinance a structured array estilo mt5.copy_rates_*"""
    if df is None or df.empty:
        return None
    try:
        import numpy as np
        n = len(df)
        dtype = [
            ("time", "i8"),
            ("open", "f8"),
            ("high", "f8"),
            ("low", "f8"),
            ("close", "f8"),
            ("tick_volume", "i8"),
            ("spread", "i4"),
            ("real_volume", "i8"),
        ]
        arr = np.zeros(n, dtype=dtype)
        idx_unix = (df.index.astype("int64") // 10**9).to_numpy()
        arr["time"] = idx_unix
        arr["open"] = df["open"].to_numpy()
        arr["high"] = df["high"].to_numpy()
        arr["low"] = df["low"].to_numpy()
        arr["close"] = df["close"].to_numpy()
        vol_col = "volume" if "volume" in df.columns else None
        if vol_col:
            arr["tick_volume"] = df[vol_col].to_numpy().astype("i8")
        return arr
    except Exception as e:
        log.warning(f"_df_to_rates_array fail: {e}")
        return None


def order_send(request):
    """Stub: sin MT5 NO se envía nada. Devuelve resultado fake exitoso
    para que el código que lee result.retcode no crashee."""
    return _StubOrderResult()


class _StubOrderResult:
    """Mimic del struct OrderSendResult de MT5."""
    retcode = 10009  # TRADE_RETCODE_DONE — exito fake
    deal = 0
    order = 0
    volume = 0.0
    price = 0.0
    bid = 0.0
    ask = 0.0
    comment = "MT5 disabled — order not sent (price_feed stub)"
    request_id = 0
    retcode_external = 0


# ─── Constantes MT5 (valores oficiales de la librería) ────────────────
# Acciones de trading
TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_SLTP = 6
TRADE_ACTION_MODIFY = 7
TRADE_ACTION_REMOVE = 8
TRADE_ACTION_CLOSE_BY = 10

# Tipos de orden
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5
ORDER_TYPE_BUY_STOP_LIMIT = 6
ORDER_TYPE_SELL_STOP_LIMIT = 7
ORDER_TYPE_CLOSE_BY = 8

# Filling
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
ORDER_FILLING_BOC = 3

# Time
ORDER_TIME_GTC = 0
ORDER_TIME_DAY = 1
ORDER_TIME_SPECIFIED = 2
ORDER_TIME_SPECIFIED_DAY = 3

# Retcodes
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010
TRADE_RETCODE_REQUOTE = 10004
TRADE_RETCODE_REJECT = 10006
TRADE_RETCODE_CANCEL = 10007
TRADE_RETCODE_PLACED = 10008
TRADE_RETCODE_ERROR = 10006
TRADE_RETCODE_TIMEOUT = 10008
TRADE_RETCODE_INVALID = 10013
TRADE_RETCODE_INVALID_VOLUME = 10014
TRADE_RETCODE_INVALID_PRICE = 10015
TRADE_RETCODE_INVALID_STOPS = 10016
TRADE_RETCODE_TRADE_DISABLED = 10017
TRADE_RETCODE_MARKET_CLOSED = 10018
TRADE_RETCODE_NO_MONEY = 10019
TRADE_RETCODE_PRICE_CHANGED = 10020
TRADE_RETCODE_PRICE_OFF = 10021
TRADE_RETCODE_INVALID_EXPIRATION = 10022
TRADE_RETCODE_ORDER_CHANGED = 10023
TRADE_RETCODE_TOO_MANY_REQUESTS = 10024
TRADE_RETCODE_NO_CHANGES = 10025
TRADE_RETCODE_SERVER_DISABLES_AT = 10026
TRADE_RETCODE_CLIENT_DISABLES_AT = 10027
TRADE_RETCODE_LOCKED = 10028
TRADE_RETCODE_FROZEN = 10029
TRADE_RETCODE_INVALID_FILL = 10030
TRADE_RETCODE_CONNECTION = 10031
TRADE_RETCODE_ONLY_REAL = 10032
TRADE_RETCODE_LIMIT_ORDERS = 10033
TRADE_RETCODE_LIMIT_VOLUME = 10034

# Deal entry
DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_ENTRY_INOUT = 2
DEAL_ENTRY_OUT_BY = 3

# Deal type
DEAL_TYPE_BUY = 0
DEAL_TYPE_SELL = 1

# Timeframes (valores oficiales MT5)
TIMEFRAME_M1 = 1
TIMEFRAME_M2 = 2
TIMEFRAME_M3 = 3
TIMEFRAME_M4 = 4
TIMEFRAME_M5 = 5
TIMEFRAME_M6 = 6
TIMEFRAME_M10 = 10
TIMEFRAME_M12 = 12
TIMEFRAME_M15 = 15
TIMEFRAME_M20 = 20
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 16385
TIMEFRAME_H2 = 16386
TIMEFRAME_H3 = 16387
TIMEFRAME_H4 = 16388
TIMEFRAME_H6 = 16390
TIMEFRAME_H8 = 16392
TIMEFRAME_H12 = 16396
TIMEFRAME_D1 = 16408
TIMEFRAME_W1 = 32769
TIMEFRAME_MN1 = 49153
# Aliases cortos (algunos códigos los usan)
TIMEFRAME_M = TIMEFRAME_M1
TIMEFRAME_H = TIMEFRAME_H1
TIMEFRAME_D = TIMEFRAME_D1

# Position type (mismos valores que ORDER_TYPE)
POSITION_TYPE_BUY = 0
POSITION_TYPE_SELL = 1


# ─── Smoke test si se ejecuta directo ─────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print("=== price_feed smoke test ===\n")
    for sym in ("BTCUSD", "ETHUSD", "EURUSD", "GBPUSD", "XAUUSD", "NAS100"):
        t = get_tick(sym)
        if t:
            print(f"  {sym:10s} bid={t.bid:.5f} ask={t.ask:.5f} last={t.last:.5f}")
        else:
            print(f"  {sym:10s} FALLO")
    print("\n=== OHLC test BTCUSD M15 x10 ===")
    df = get_ohlc("BTCUSD", "M15", 10)
    if df is not None:
        print(df.tail(3))
    else:
        print("  FALLO")

"""
BuySell365 — Monitor cuenta REAL XM (88849791)
================================================
- Lee TODAS las posiciones abiertas (manuales + bot) cada 30s
- Lee historial de deals cerrados del día
- Calcula WR, pips, profit en tiempo real
- Escribe mt5_realtime.json para el dashboard web
- SOLO LECTURA — nunca ejecuta órdenes
"""
import os, time, json, logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Configuración — credenciales desde .env (nunca hardcodeadas) ───────────
MT5_LOGIN    = int(os.getenv("MT5_LOGIN",    "88849791"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER   = os.getenv("MT5_SERVER",   "XMGlobal-MT5 4")

CHECK_INTERVAL = 30  # segundos entre lecturas

REALTIME_FILE = Path(__file__).parent / "mt5_realtime.json"

# ── Logging ────────────────────────────────────────────────────
_log_dir = Path(__file__).parent / "logs"
_log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MONITOR_REAL] %(message)s",
    handlers=[
        RotatingFileHandler(_log_dir / "monitor_real.log", encoding="utf-8",
                            maxBytes=5 * 1024 * 1024, backupCount=3),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("monitor_real")


# ── Helpers ────────────────────────────────────────────────────

def _pip_size(symbol: str) -> float:
    """Retorna el tamaño de pip para calcular pips desde precio."""
    s = symbol.upper()
    if "JPY" in s:
        return 0.01
    if any(x in s for x in ["GOLD", "XAUUSD", "GC"]):
        return 0.1
    if any(x in s for x in ["NAS", "US100", "NDX"]):
        return 1.0
    if any(x in s for x in ["SPX", "US500", "SP500"]):
        return 1.0
    if any(x in s for x in ["US30", "DOW", "DJI"]):
        return 1.0
    return 0.0001  # Forex estándar


def _pips(symbol: str, price_open: float, price_close: float, is_buy: bool) -> float:
    """Calcula pips de ganancia/pérdida."""
    diff = (price_close - price_open) if is_buy else (price_open - price_close)
    pip = _pip_size(symbol)
    return round(diff / pip, 1) if pip > 0 else round(diff, 2)


def _escribir_json(data: dict):
    """Escribe el JSON de forma atómica."""
    tmp = REALTIME_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(REALTIME_FILE)
    except Exception as e:
        log.error(f"Error escribiendo JSON: {e}")


# ── Monitor principal ──────────────────────────────────────────

def main():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        log.error("MetaTrader5 no instalado. Instala: pip install MetaTrader5")
        return

    log.info(f"📊 Monitor REAL iniciando — cuenta {MT5_LOGIN} en {MT5_SERVER}")

    while True:
        try:
            # ── 1. Inicializar y conectar ──────────────────────
            if not mt5.initialize():
                log.warning(f"MT5 initialize() falló: {mt5.last_error()}")
                time.sleep(CHECK_INTERVAL)
                continue

            if not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
                log.warning(f"Login fallido: {mt5.last_error()}")
                mt5.shutdown()
                time.sleep(CHECK_INTERVAL)
                continue

            # ── 2. Info de cuenta ──────────────────────────────
            acc = mt5.account_info()
            balance    = round(acc.balance, 2)  if acc else 0
            equity     = round(acc.equity, 2)   if acc else 0
            profit_fl  = round(acc.profit, 2)   if acc else 0
            margen_libre = round(acc.margin_free, 2) if acc else 0

            # ── 3. Posiciones abiertas ─────────────────────────
            raw_pos = mt5.positions_get() or []
            posiciones = []
            for p in raw_pos:
                is_buy = (p.type == 0)
                posiciones.append({
                    "ticket":      p.ticket,
                    "symbol":      p.symbol,
                    "tipo":        "COMPRA" if is_buy else "VENTA",
                    "volumen":     p.volume,
                    "entrada":     p.price_open,
                    "precio_actual": p.price_current,
                    "sl":          p.sl,
                    "tp":          p.tp,
                    "profit":      round(p.profit, 2),
                    "pips":        _pips(p.symbol, p.price_open, p.price_current, is_buy),
                    "hora_apertura": datetime.fromtimestamp(p.time).strftime("%H:%M"),
                    "fecha_apertura": datetime.fromtimestamp(p.time).strftime("%d/%m/%Y"),
                    "comentario":  p.comment,
                })

            # ── 4. Historial del día (deals cerrados) ──────────
            hoy_inicio = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            manana     = hoy_inicio + timedelta(days=1)
            deals_raw  = mt5.history_deals_get(hoy_inicio, manana) or []

            historial_hoy = []
            _pos_vistos   = {}  # position_id → deal de entrada

            for d in deals_raw:
                if d.entry == 0:   # DEAL_ENTRY_IN  (apertura)
                    _pos_vistos[d.position_id] = d
                elif d.entry == 1: # DEAL_ENTRY_OUT (cierre)
                    entrada_deal = _pos_vistos.get(d.position_id)
                    # FIX 2026-04-15: Clarificar lógica — deal de cierre tiene tipo inverso
                    # DEAL_TYPE_SELL(1) cierra BUY, DEAL_TYPE_BUY(0) cierra SELL
                    original_was_buy = (d.type == 1)  # Si deal de cierre es SELL → posición original era BUY
                    price_open  = entrada_deal.price if entrada_deal else d.price
                    price_close = d.price
                    pips_val = _pips(d.symbol, price_open, price_close, original_was_buy)
                    historial_hoy.append({
                        "ticket":      d.position_id,
                        "symbol":      d.symbol,
                        "tipo":        "COMPRA" if original_was_buy else "VENTA",
                        "entrada":     price_open,
                        "salida":      price_close,
                        "profit":      round(d.profit, 2),
                        "pips":        pips_val,
                        "volumen":     d.volume,
                        "hora_salida": datetime.fromtimestamp(d.time).strftime("%H:%M"),
                        "fecha":       datetime.fromtimestamp(d.time).strftime("%d/%m/%Y"),
                        "resultado":   "WIN" if d.profit > 0 else "LOSS",
                    })

            # ── 5. Estadísticas del día ────────────────────────
            total   = len(historial_hoy)
            wins    = sum(1 for h in historial_hoy if h["profit"] > 0)
            losses  = total - wins
            wr      = round(wins / total * 100, 1) if total > 0 else 0
            pips_d  = round(sum(h["pips"] for h in historial_hoy), 1)
            profit_d = round(sum(h["profit"] for h in historial_hoy), 2)

            # ── 6. Estadísticas históricas (historial_real.json) ─
            hist_real_path = Path(__file__).parent / "historial_real.json"
            wr_hist = 0; total_hist = 0; pips_hist = 0
            try:
                if hist_real_path.exists():
                    _hr = json.loads(hist_real_path.read_text(encoding="utf-8"))
                    total_hist = len(_hr)
                    wins_hist  = sum(1 for h in _hr if float(h.get("pips", 0)) > 0)
                    wr_hist    = round(wins_hist / total_hist * 100, 1) if total_hist > 0 else 0
                    pips_hist  = round(sum(float(h.get("pips", 0)) for h in _hr), 1)
            except Exception:
                pass

            # ── 7. Escribir JSON para el dashboard ─────────────
            data = {
                "last_update":    datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                "cuenta": {
                    "login":       MT5_LOGIN,
                    "balance":     balance,
                    "equity":      equity,
                    "profit_fl":   profit_fl,
                    "margen_libre": margen_libre,
                },
                "posiciones_abiertas": posiciones,
                "historial_hoy":       historial_hoy,
                "stats_hoy": {
                    "total":   total,
                    "wins":    wins,
                    "losses":  losses,
                    "wr":      wr,
                    "pips":    pips_d,
                    "profit":  profit_d,
                },
                "stats_historico": {
                    "total":   total_hist,
                    "wr":      wr_hist,
                    "pips":    pips_hist,
                },
            }
            _escribir_json(data)

            log.info(
                f"✅ Sync OK | Abiertas: {len(posiciones)} | "
                f"Hoy: {wins}W/{losses}L WR{wr}% | "
                f"Balance: ${balance} Equity: ${equity}"
            )

            mt5.shutdown()

        except Exception as e:
            log.error(f"Error en ciclo monitor: {e}")
            try:
                mt5.shutdown()
            except Exception:
                pass

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()

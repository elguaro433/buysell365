"""
sync_mt5_to_render.py
---------------------
Lee el historial real de MT5 (deals cerrados) y lo sube a Render via /api/sync.
Uso: python sync_mt5_to_render.py [dias]  (por defecto 3 dias)
"""
import os, sys, json, time, requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

WEB_URL    = os.getenv("WEB_URL", "https://buysell365.pro").rstrip("/")
SYNC_SECRET = os.getenv("SYNC_SECRET", "buysell365_sync_2026")
DIAS       = int(sys.argv[1]) if len(sys.argv) > 1 else 3

# ── Mapa ticker → nombre y pip_size ────────────────────────────────────────
ASSETS_MAP = {
    "XAUUSD":   {"nombre": "ORO",       "pip": 0.01},
    "EURUSD":   {"nombre": "EUR/USD",   "pip": 0.0001},
    "USDJPY":   {"nombre": "USD/JPY",   "pip": 0.01},
    "GBPJPY":   {"nombre": "GBP/JPY",   "pip": 0.01},
    "AUDUSD":   {"nombre": "AUD/USD",   "pip": 0.0001},
    "AUDCAD":   {"nombre": "AUD/CAD",   "pip": 0.0001},
    "EURCHF":   {"nombre": "EUR/CHF",   "pip": 0.0001},
    "USDCAD":   {"nombre": "USD/CAD",   "pip": 0.0001},
    "GBPUSD":   {"nombre": "GBP/USD",   "pip": 0.0001},
    "US100Cash":{"nombre": "NASDAQ",    "pip": 1.0},
    "US500Cash":{"nombre": "S&P 500",   "pip": 1.0},
    "DE40Cash": {"nombre": "DAX",       "pip": 1.0},
    "UK100Cash":{"nombre": "FTSE 100",  "pip": 1.0},
    "NDAQ":     {"nombre": "NASDAQ",    "pip": 1.0},
    "US100":    {"nombre": "NASDAQ",    "pip": 1.0},
    "US500":    {"nombre": "S&P 500",   "pip": 1.0},
}

def get_pip_size(symbol):
    for k, v in ASSETS_MAP.items():
        if k.upper() in symbol.upper() or symbol.upper() in k.upper():
            return v["pip"]
    # Default: 4-decimal forex
    return 0.0001 if "JPY" not in symbol.upper() else 0.01

def get_nombre(symbol):
    for k, v in ASSETS_MAP.items():
        if k.upper() in symbol.upper() or symbol.upper() in k.upper():
            return v["nombre"]
    return symbol

def mt5_deals_to_historial(dias=3):
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("❌ MetaTrader5 no instalado. Instala con: pip install MetaTrader5")
        return []

    mt5_path = os.getenv("MT5_PATH", "")
    login    = int(os.getenv("MT5_LOGIN", 0))
    password = os.getenv("MT5_PASSWORD", "")
    server   = os.getenv("MT5_SERVER", "")

    if not mt5.initialize(mt5_path):
        print(f"❌ MT5 initialize failed: {mt5.last_error()}")
        return []

    if not mt5.login(login, password=password, server=server):
        print(f"❌ MT5 login failed: {mt5.last_error()}")
        mt5.shutdown()
        return []

    print(f"✅ MT5 conectado: cuenta {login}")

    date_from = datetime.now() - timedelta(days=dias)
    date_to   = datetime.now() + timedelta(days=1)

    deals = mt5.history_deals_get(date_from, date_to)
    mt5.shutdown()

    if deals is None or len(deals) == 0:
        print(f"ℹ️  Sin deals en los últimos {dias} días")
        return []

    print(f"📊 {len(deals)} deals encontrados en MT5")

    # Agrupar deals por position_id para emparentar open+close
    positions = {}
    for d in deals:
        pid = d.position_id
        if pid not in positions:
            positions[pid] = []
        positions[pid].append(d)

    historial = []
    for pid, deal_list in positions.items():
        # type: 0=DEAL_TYPE_BUY, 1=DEAL_TYPE_SELL
        # entry: 0=DEAL_ENTRY_IN, 1=DEAL_ENTRY_OUT, 2=DEAL_ENTRY_INOUT
        opens  = [d for d in deal_list if d.entry == 0]
        closes = [d for d in deal_list if d.entry == 1]

        if not opens or not closes:
            continue  # skip incomplete

        open_deal  = opens[0]
        close_deal = closes[-1]

        symbol   = open_deal.symbol
        nombre   = get_nombre(symbol)
        pip_size = get_pip_size(symbol)
        tipo     = "COMPRA" if open_deal.type == 0 else "VENTA"

        # Calculate pips from price difference
        price_open  = open_deal.price
        price_close = close_deal.price
        if tipo == "COMPRA":
            pips = (price_close - price_open) / pip_size
        else:
            pips = (price_open - price_close) / pip_size

        pips = round(pips, 1)

        # Date/time from close deal
        close_dt = datetime.fromtimestamp(close_deal.time)
        fecha = close_dt.strftime("%d/%m/%Y")
        hora  = close_dt.strftime("%H:%M")

        entry = {
            "nombre":  nombre,
            "ticker":  symbol,
            "tipo":    tipo,
            "pips":    pips,
            "fecha":   fecha,
            "hora":    hora,
            "precio_entrada": round(price_open, 5),
            "precio_salida":  round(price_close, 5),
            "profit_usd":     round(close_deal.profit, 2),
            "volumen":        round(open_deal.volume, 2),
            "fuente":         "mt5_real",
        }
        historial.append(entry)
        print(f"  {fecha} {hora} | {tipo:6} {nombre:10} | {pips:+.1f} pips | ${close_deal.profit:.2f}")

    # Sort by fecha+hora
    historial.sort(key=lambda x: (x["fecha"].split("/")[::-1], x["hora"]))
    return historial


def push_to_render(historial):
    payload = {
        "historial_operaciones": historial,
        "bot_active": True,
        "assets_count": 20,
    }
    try:
        r = requests.post(
            f"{WEB_URL}/api/sync",
            json=payload,
            headers={"X-Sync-Secret": SYNC_SECRET},
            timeout=20,
        )
        if r.status_code == 200:
            print(f"✅ Render actualizado con {len(historial)} operaciones")
        else:
            print(f"❌ Error Render: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"❌ Error de conexión: {e}")


if __name__ == "__main__":
    print(f"🔄 Extrayendo historial MT5 — últimos {DIAS} días...")
    hist = mt5_deals_to_historial(DIAS)
    if hist:
        print(f"\n📤 Subiendo {len(hist)} operaciones a Render...")
        push_to_render(hist)
    else:
        print("⚠️  Sin operaciones para subir")

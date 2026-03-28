# -*- coding: utf-8 -*-
"""
run_mt5_sync.py  —  Lee historial real MT5 y sube a Render.
Uso: python run_mt5_sync.py [dias]
"""
import os, sys, requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

WEB_URL     = os.getenv('WEB_URL', 'https://buysell365.pro').rstrip('/')
SYNC_SECRET = os.getenv('SYNC_SECRET', 'buysell365_sync_2026')
DIAS        = int(sys.argv[1]) if len(sys.argv) > 1 else 3

# Pip sizes segun convencion del bot (calcular_pips en bot.py)
# GOLD/XAU: 1 pip = $1 movimiento de precio  → divisor 1.0
# JPY pairs: 1 pip = 0.01                    → divisor 0.01
# Forex:     1 pip = 0.0001                  → divisor 0.0001
# Indices:   1 pip = 1 punto                 → divisor 1.0
_PIP_DIVISOR = {
    'XAUUSD': 1.0, 'GOLD': 1.0,
    'US100':  1.0, 'US100Cash': 1.0, 'NDAQ': 1.0,
    'US500':  1.0, 'US500Cash': 1.0,
    'DE40Cash': 1.0, 'UK100Cash': 1.0,
    'USDJPY': 0.01, 'GBPJPY': 0.01, 'EURJPY': 0.01,
    'AUDJPY': 0.01, 'CADJPY': 0.01, 'CHFJPY': 0.01,
}
_NOMBRE = {
    'XAUUSD': 'ORO', 'GOLD': 'ORO',
    'EURUSD': 'EUR/USD', 'USDJPY': 'USD/JPY', 'GBPJPY': 'GBP/JPY',
    'AUDUSD': 'AUD/USD', 'AUDCAD': 'AUD/CAD', 'EURCHF': 'EUR/CHF',
    'USDCAD': 'USD/CAD', 'GBPUSD': 'GBP/USD', 'GBPAUD': 'GBP/AUD',
    'US100Cash': 'NASDAQ', 'US500Cash': 'S&P 500', 'DE40Cash': 'DAX',
    'UK100Cash': 'FTSE 100',
}

def get_pip(sym):
    for k, v in _PIP_DIVISOR.items():
        if k.upper() in sym.upper():
            return v
    return 0.01 if 'JPY' in sym.upper() else 0.0001

def get_nombre(sym):
    for k, v in _NOMBRE.items():
        if k.upper() in sym.upper():
            return v
    return sym

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: pip install MetaTrader5")
    sys.exit(1)

print(f"Conectando a MT5...")
if not mt5.initialize(os.getenv('MT5_PATH', '')):
    print("MT5 initialize failed:", mt5.last_error()); sys.exit(1)

if not mt5.login(int(os.getenv('MT5_LOGIN', 0)),
                 password=os.getenv('MT5_PASSWORD', ''),
                 server=os.getenv('MT5_SERVER', '')):
    print("MT5 login failed:", mt5.last_error()); mt5.shutdown(); sys.exit(1)

acc = mt5.account_info()
print(f"Cuenta {acc.login} | Balance: ${acc.balance:.2f} | Broker: {acc.server}")

date_from = datetime.now() - timedelta(days=DIAS)
deals = mt5.history_deals_get(date_from, datetime.now() + timedelta(hours=2))
mt5.shutdown()

if not deals:
    print("Sin deals en los ultimos", DIAS, "dias"); sys.exit(0)

print(f"\nDeals brutos: {len(deals)}")

# Debug: mostrar primeros 3 deals para verificar precios
for d in list(deals)[:3]:
    print(f"  DEBUG deal: sym={d.symbol} type={d.type} entry={d.entry} price={d.price} vol={d.volume} profit={d.profit} pos_id={d.position_id}")

# Agrupar por position_id
positions = {}
for d in deals:
    positions.setdefault(d.position_id, []).append(d)

hist = []
for pid, dl in positions.items():
    opens  = [d for d in dl if d.entry == 0]  # DEAL_ENTRY_IN
    closes = [d for d in dl if d.entry == 1]  # DEAL_ENTRY_OUT
    if not opens or not closes:
        continue
    od = opens[0]
    cd = closes[-1]

    sym  = od.symbol
    pip  = get_pip(sym)
    tipo = 'COMPRA' if od.type == 0 else 'VENTA'

    # Pips segun convencion del bot
    diff = cd.price - od.price
    if tipo == 'VENTA':
        diff = -diff
    pips = round(diff / pip, 1)

    dt = datetime.fromtimestamp(cd.time)
    entry = {
        'nombre':         get_nombre(sym),
        'ticker':         sym,
        'tipo':           tipo,
        'pips':           pips,
        'fecha':          dt.strftime('%d/%m/%Y'),
        'hora':           dt.strftime('%H:%M'),
        'precio_entrada': round(od.price, 5),
        'precio_salida':  round(cd.price, 5),
        'profit_usd':     round(cd.profit, 2),
        'volumen':        round(od.volume, 2),
        'fuente':         'mt5_real',
    }
    hist.append(entry)
    result = "WIN" if pips > 0 else "LOSS"
    print(f"  {dt.strftime('%d/%m %H:%M')} | {tipo:6} | {get_nombre(sym):10} | {pips:+.1f} pips | ${cd.profit:+.2f} | {result}")

print(f"\nTotal operaciones cerradas: {len(hist)}")
wins   = sum(1 for h in hist if h['pips'] > 0)
losses = sum(1 for h in hist if h['pips'] <= 0)
total_pips = sum(h['pips'] for h in hist)
print(f"W/L: {wins}W / {losses}L | Pips netos: {total_pips:+.1f}")

if hist:
    hist.sort(key=lambda x: (x['fecha'].split('/')[::-1], x['hora']))
    print(f"\nSubiendo a Render...")
    r = requests.post(
        f'{WEB_URL}/api/sync',
        json={'historial_operaciones': hist, 'bot_active': True, 'assets_count': 20},
        headers={'X-Sync-Secret': SYNC_SECRET},
        timeout=20
    )
    print(f"Render: {r.status_code} — {r.text[:150]}")

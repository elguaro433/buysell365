"""
Importa trades de hoy (30/03/2026) desde hoy.html (MT5 HTML export, UTF-16)
y los AGREGA a historial_real.json (sin borrar trades anteriores).
Columnas confirmadas:
  col[0]=open_dt, col[1]=ticket, col[2]=symbol, col[3]=type,
  col[4]=empty, col[5]=volume, col[6]=entry, col[7]=empty,
  col[8]=empty, col[9]=close_dt, col[10]=close_price,
  col[11]=commission, col[12]=swap, col[13]=profit
"""
import json, re
from datetime import datetime
from bs4 import BeautifulSoup

SYMBOL_MAP = {
    'GOLD':      ('GOLD',    'XAUUSD=X'),
    'XAUUSD':    ('GOLD',    'XAUUSD=X'),
    'EURUSD':    ('EUR/USD', 'EURUSD=X'),
    'GBPUSD':    ('GBP/USD', 'GBPUSD=X'),
    'USDJPY':    ('USD/JPY', 'USDJPY=X'),
    'GBPJPY':    ('GBP/JPY', 'GBPJPY=X'),
    'US100Cash': ('NASDAQ',  'NQ=F'),
    'US500Cash': ('SP500',   'ES=F'),
    'US30Cash':  ('DOW30',   'YM=F'),
    'EURAUD':    ('EUR/AUD', 'EURAUD=X'),
    'EURGBP':    ('EUR/GBP', 'EURGBP=X'),
    'AUDUSD':    ('AUD/USD', 'AUDUSD=X'),
    'USDCAD':    ('USD/CAD', 'USDCAD=X'),
    'USDCHF':    ('USD/CHF', 'USDCHF=X'),
    'NZDUSD':    ('NZD/USD', 'NZDUSD=X'),
    'EURJPY':    ('EUR/JPY', 'EURJPY=X'),
    'CADJPY':    ('CAD/JPY', 'CADJPY=X'),
    'AUDNZD':    ('AUD/NZD', 'AUDNZD=X'),
}

TARGET_DATE = '2026.03.30'

with open(r'C:\Users\hpint\Desktop\hoy.html', encoding='utf-16') as f:
    soup = BeautifulSoup(f.read(), 'html.parser')

rows = soup.find_all('tr', bgcolor=lambda b: b in ('#FFFFFF', '#F7F7F7'))
print(f"Total rows encontrados: {len(rows)}")

trades_hoy = []
skipped = 0

for row in rows:
    cells = row.find_all('td')
    vals = [c.get_text(strip=True) for c in cells]

    if len(vals) < 14:
        skipped += 1
        continue

    open_dt_raw = vals[0]
    if TARGET_DATE not in open_dt_raw:
        skipped += 1
        continue

    tipo_raw = vals[3].lower()
    if tipo_raw not in ('buy', 'sell'):
        skipped += 1
        continue

    symbol = vals[2]
    if not symbol or symbol == 'nan':
        skipped += 1
        continue

    def _f(s):
        """Convierte string a float, quitando separadores de miles (espacios/comas)."""
        return float(s.replace(' ', '').replace(',', '')) if s else 0.0

    try:
        ticket     = int(vals[1]) if vals[1] else 0
        volume     = _f(vals[5])
        entry      = _f(vals[6])
        close_str  = vals[9]
        close_price= _f(vals[10])
        profit     = _f(vals[13])
    except (ValueError, TypeError) as e:
        print(f"  WARN Error parsing row {vals[:4]}: {e}")
        skipped += 1
        continue

    # Validar que el precio de cierre sea real (>0) — las filas de balance tienen close_price=0
    if close_price <= 0:
        skipped += 1
        continue

    # Validar que la fecha de cierre sea válida — las filas de balance no tienen close_dt
    try:
        close_dt = datetime.strptime(close_str, '%Y.%m.%d %H:%M:%S')
    except (ValueError, TypeError):
        skipped += 1
        continue

    try:
        open_dt = datetime.strptime(open_dt_raw, '%Y.%m.%d %H:%M:%S')
    except ValueError:
        skipped += 1
        continue

    duracion = (close_dt - open_dt).total_seconds() / 60
    nombre, ticker = SYMBOL_MAP.get(symbol, (symbol, symbol + '=X'))
    tipo = 'COMPRA' if tipo_raw == 'buy' else 'VENTA'

    # Calcular pips / puntos
    diff = (close_price - entry) if tipo_raw == 'buy' else (entry - close_price)
    sym_up = symbol.upper()
    if 'GOLD' in sym_up or 'XAU' in sym_up:
        pips = round(diff, 2)
    elif 'JPY' in sym_up:
        pips = round(diff * 100, 2)
    elif sym_up in ('US100CASH', 'US500CASH', 'US30CASH'):
        pips = round(diff, 2)
    else:
        pips = round(diff * 10000, 2)

    resultado = 'WIN' if profit > 0 else ('LOSS' if profit < 0 else 'BE')
    tag       = 'TP'  if profit > 0 else ('SL'   if profit < 0 else 'BE')

    trade = {
        'nombre': nombre, 'tipo': tipo, 'ticker': ticker,
        'entrada': round(entry, 5), 'salida': round(close_price, 5),
        'pips': pips, 'resultado': resultado,
        'hora': open_dt.strftime('%H:%M'), 'hora_salida': close_dt.strftime('%H:%M'),
        'fecha': open_dt.strftime('%d/%m/%Y'), 'tag': tag,
        'tp1_hit': profit > 0, 'tp2_hit': False,
        'duracion_min': round(duracion, 1), 'score': 0, 'confianza': 0,
        'estrategia': 'BuySell365_AI', 'fuente': 'mt5_real', 'mt5_ejecutado': True,
        'ticket_mt5': ticket, 'profit_mt5': round(profit, 2), 'volumen_mt5': volume,
    }
    trades_hoy.append(trade)
    print(f"  OK {symbol} {tipo_raw.upper()} | entrada={entry} salida={close_price} | profit={profit:+.2f} | {pips:+.1f} pips | {tag}")

print(f"\nTrades de hoy parseados: {len(trades_hoy)} | Skipped: {skipped}")

if not trades_hoy:
    print("❌ No se encontraron trades — revisa el archivo HTML")
    exit(1)

# --- Cargar historial existente ---
PATHS = [
    r'C:\Users\hpint\Desktop\BuySell365_Bot\historial_real.json',
    r'C:\Users\hpint\Desktop\BuySell365_Bot\web\historial_real.json',
]

with open(PATHS[0], encoding='utf-8') as f:
    historial = json.load(f)

# Eliminar duplicados por ticket_mt5 (si ya se importó antes)
tickets_existentes = {t.get('ticket_mt5') for t in historial}
nuevos = [t for t in trades_hoy if t['ticket_mt5'] not in tickets_existentes]
print(f"Nuevos (sin duplicar): {len(nuevos)} | Ya existían: {len(trades_hoy) - len(nuevos)}")

historial = nuevos + historial  # hoy primero

# Ordenar: fecha DESC, hora DESC
def sort_key(t):
    try:
        return datetime.strptime(f"{t['fecha']} {t['hora']}", '%d/%m/%Y %H:%M')
    except Exception:
        return datetime.min

historial.sort(key=sort_key, reverse=True)

for path in PATHS:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(historial, f, ensure_ascii=False, indent=2)
    print(f"Guardado: {path}")

# --- Resumen ---
from collections import defaultdict
by_date = defaultdict(list)
for t in trades_hoy:
    by_date[t['fecha']].append(t)

print("\n=== RESUMEN HOY ===")
for d in sorted(by_date.keys()):
    ts = by_date[d]
    w = sum(1 for t in ts if t['resultado'] == 'WIN')
    l = sum(1 for t in ts if t['resultado'] == 'LOSS')
    b = sum(1 for t in ts if t['resultado'] == 'BE')
    pips_total   = sum(t['pips'] for t in ts)
    profit_total = sum(t['profit_mt5'] for t in ts)
    print(f"  {d}: {len(ts)} ops | {w}W/{l}L/{b}BE | {pips_total:+.1f} pts | USD {profit_total:+.2f}")

tw = sum(1 for t in trades_hoy if t['resultado'] == 'WIN')
tl = sum(1 for t in trades_hoy if t['resultado'] == 'LOSS')
total_pips   = sum(t['pips'] for t in trades_hoy)
total_profit = sum(t['profit_mt5'] for t in trades_hoy)
print(f"\nTOTAL HOY: {tw}W/{tl}L | Pips: {total_pips:+.1f} | USD: {total_profit:+.2f}")
print(f"HISTORIAL TOTAL: {len(historial)} trades")

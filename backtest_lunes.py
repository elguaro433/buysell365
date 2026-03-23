#!/usr/bin/env python3
"""
BuySell365 Pro -- Backtest Realista 30 Dias
Simula EXACTAMENTE como operará el bot a partir del lunes.
Aplica: horarios, días, dirección bloqueada, SL/TP por par, cooldown.
"""
import sys, os, warnings
warnings.filterwarnings("ignore")
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

try:
    import pandas_ta as ta
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pandas_ta", "-q"])
    import pandas_ta as ta

try:
    import yfinance as yf
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "yfinance", "-q"])
    import yfinance as yf

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONFIGURACIÓN POR PAR — Exacta del PAR_PROFILES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PARES = {
    # ── SCALPER + PREMIUM ──
    "EURUSD": {
        "yf": "EURUSD=X", "display": "EUR/USD", "pip_mult": 10000, "pip_name": "pips",
        "scalper": True, "premium": True,
        "sc_estrategia": "bb_rsi", "sc_rsi_buy": 40, "sc_rsi_sell": 60,
        "sc_adx_min": 10, "sc_adx_max": 50, "sc_sl_atr": 1.2, "sc_tp_atr": 2.0,
        "sc_block_buy": False, "sc_block_sell": False,
        "sc_horario": (7, 17), "sc_dias": [0,1,2,3,4],
        "pr_sl_mult": 0.8, "pr_tp1_mult": 2.5, "pr_adx_min": 15,
        "pr_rsi_os": 32, "pr_rsi_ob": 68, "pr_rsi_gate_buy": 55, "pr_rsi_gate_sell": 45,
        "pr_horario": (7, 17), "pr_dias": [1,2],
    },
    "US100Cash": {
        "yf": "NQ=F", "display": "NASDAQ", "pip_mult": 1, "pip_name": "pts",
        "scalper": True, "premium": True,
        "sc_estrategia": "bb_rsi", "sc_rsi_buy": 40, "sc_rsi_sell": 60,
        "sc_adx_min": 10, "sc_adx_max": 50, "sc_sl_atr": 1.2, "sc_tp_atr": 2.0,
        "sc_block_buy": False, "sc_block_sell": True,
        "sc_horario": (13, 20), "sc_dias": [0,1,2,3,4],
        "pr_sl_mult": 0.7, "pr_tp1_mult": 2.2, "pr_adx_min": 20,
        "pr_rsi_os": 35, "pr_rsi_ob": 65, "pr_rsi_gate_buy": None, "pr_rsi_gate_sell": None,
        "pr_horario": (13, 20), "pr_dias": [0,1,2,3,4],
    },
    # ── SOLO SCALPER ──
    "GOLD": {
        "yf": "GC=F", "display": "ORO", "pip_mult": 1, "pip_name": "pts",
        "scalper": True, "premium": False,
        "sc_estrategia": "bb_rsi", "sc_rsi_buy": 40, "sc_rsi_sell": 60,
        "sc_adx_min": 10, "sc_adx_max": 50, "sc_sl_atr": 1.2, "sc_tp_atr": 2.0,
        "sc_block_buy": True, "sc_block_sell": False,
        "sc_horario": (9, 19), "sc_dias": [0,1,2,3,4],
    },
    "AUDCAD": {
        "yf": "AUDCAD=X", "display": "AUD/CAD", "pip_mult": 10000, "pip_name": "pips",
        "scalper": True, "premium": False,
        "sc_estrategia": "fibonacci", "sc_rsi_buy": 45, "sc_rsi_sell": 55,
        "sc_adx_min": 10, "sc_adx_max": 50, "sc_sl_atr": 1.0, "sc_tp_atr": 1.8,
        "sc_block_buy": True, "sc_block_sell": False,
        "sc_horario": (0, 14), "sc_dias": [0,1,2,3,4],
    },
    "EURCHF": {
        "yf": "EURCHF=X", "display": "EUR/CHF", "pip_mult": 10000, "pip_name": "pips",
        "scalper": True, "premium": False,
        "sc_estrategia": "fibonacci", "sc_rsi_buy": 45, "sc_rsi_sell": 55,
        "sc_adx_min": 10, "sc_adx_max": 50, "sc_sl_atr": 1.0, "sc_tp_atr": 1.8,
        "sc_block_buy": True, "sc_block_sell": False,
        "sc_horario": (7, 16), "sc_dias": [0,1,2,3,4],
    },
    "USDCAD": {
        "yf": "USDCAD=X", "display": "USD/CAD", "pip_mult": 10000, "pip_name": "pips",
        "scalper": True, "premium": False,
        "sc_estrategia": "fibonacci", "sc_rsi_buy": 45, "sc_rsi_sell": 55,
        "sc_adx_min": 10, "sc_adx_max": 50, "sc_sl_atr": 1.0, "sc_tp_atr": 1.8,
        "sc_block_buy": False, "sc_block_sell": False,
        "sc_horario": (13, 20), "sc_dias": [0,1,2,3,4],
    },
    # ── SOLO PREMIUM ──
    "US500Cash": {
        "yf": "ES=F", "display": "S&P 500", "pip_mult": 1, "pip_name": "pts",
        "scalper": False, "premium": True,
        "pr_sl_mult": 0.7, "pr_tp1_mult": 2.2, "pr_adx_min": 16,
        "pr_rsi_os": 35, "pr_rsi_ob": 65, "pr_rsi_gate_buy": None, "pr_rsi_gate_sell": None,
        "pr_horario": (13, 20), "pr_dias": [0,1,2,3,4],
    },
    "USDJPY": {
        "yf": "USDJPY=X", "display": "USD/JPY", "pip_mult": 100, "pip_name": "pips",
        "scalper": False, "premium": True,
        "pr_sl_mult": 1.0, "pr_tp1_mult": 1.4, "pr_adx_min": 16,
        "pr_rsi_os": 33, "pr_rsi_ob": 67, "pr_rsi_gate_buy": 55, "pr_rsi_gate_sell": 45,
        "pr_horario": (7, 16), "pr_dias": [1,2,3],
    },
}

COOLDOWN_BARS = 6  # 30 min / 5 min = 6 barras
PERIODO = 30  # días

def descargar_datos(ticker, dias):
    """Descarga datos M5 de yfinance"""
    try:
        df = yf.download(ticker, period=f"{dias}d", interval="5m", progress=False, auto_adjust=True)
        if df is None or len(df) < 100:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
        return df
    except:
        return None

def calcular_indicadores(df):
    """Calcula BB, RSI, ADX, ATR, EMA20, EMA50, Fibonacci"""
    df = df.copy()
    df['rsi'] = ta.rsi(df['Close'], length=7)
    bb = ta.bbands(df['Close'], length=20, std=2.0)
    if bb is not None:
        cols = bb.columns
        df['bb_upper'] = bb[cols[0]] if 'BBU' in cols[0] else bb.iloc[:, 2]
        df['bb_middle'] = bb[cols[1]] if 'BBM' in cols[1] else bb.iloc[:, 1]
        df['bb_lower'] = bb[cols[2]] if 'BBL' in cols[2] else bb.iloc[:, 0]
        # Fix column order
        for c in bb.columns:
            if 'BBU' in c: df['bb_upper'] = bb[c]
            elif 'BBM' in c: df['bb_middle'] = bb[c]
            elif 'BBL' in c: df['bb_lower'] = bb[c]
    adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=10)
    if adx_df is not None:
        for c in adx_df.columns:
            if 'ADX' in c and 'DM' not in c:
                df['adx'] = adx_df[c]
                break
    df['atr'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    df['ema20'] = ta.ema(df['Close'], length=20)
    df['ema50'] = ta.ema(df['Close'], length=50)

    # Fibonacci 23.6/76.4
    df['fib_high'] = df['High'].rolling(50).max()
    df['fib_low'] = df['Low'].rolling(50).min()
    rng = df['fib_high'] - df['fib_low']
    rng = rng.replace(0, np.nan)
    df['fib_pos'] = (df['Close'] - df['fib_low']) / rng

    # Tendencia
    df['tendencia'] = np.where(df['ema20'] > df['ema50'], 'ALCISTA', 'BAJISTA')

    df = df.dropna()
    return df

def backtest_scalper(df, cfg):
    """Backtest scalper con filtros exactos del bot"""
    trades = []
    cooldown_end = {}
    last_signal_bar = -999

    for i in range(60, len(df)):
        row = df.iloc[i]
        idx = df.index[i]
        hora = idx.hour if hasattr(idx, 'hour') else 0
        dia = idx.dayofweek if hasattr(idx, 'dayofweek') else 0

        # Filtro horario
        h_ini, h_fin = cfg['sc_horario']
        if hora < h_ini or hora >= h_fin:
            continue

        # Filtro día
        if dia not in cfg['sc_dias']:
            continue

        # Cooldown
        if i - last_signal_bar < COOLDOWN_BARS:
            continue

        rsi = row.get('rsi', 50)
        adx = row.get('adx', 0)
        atr = row.get('atr', 0)
        close = row['Close']
        tendencia = row.get('tendencia', 'NEUTRAL')
        fib_pos = row.get('fib_pos', 0.5)

        if pd.isna(rsi) or pd.isna(adx) or pd.isna(atr) or atr == 0:
            continue

        # ADX filter
        if adx < cfg['sc_adx_min'] or adx > cfg['sc_adx_max']:
            continue

        signal = None
        estrategia = cfg['sc_estrategia']

        if estrategia == 'bb_rsi':
            bb_lower = row.get('bb_lower', close)
            bb_upper = row.get('bb_upper', close)
            if pd.isna(bb_lower) or pd.isna(bb_upper):
                continue

            # BUY: RSI < threshold AND close near/below BB lower
            if rsi < cfg['sc_rsi_buy'] and close <= bb_lower * 1.001:
                if not cfg['sc_block_buy']:
                    # Filtro tendencia: no comprar en bajista
                    if tendencia != 'BAJISTA':
                        signal = 'BUY'
                    elif rsi < 30:  # RSI extremo = override
                        signal = 'BUY'
            # SELL: RSI > threshold AND close near/above BB upper
            elif rsi > cfg['sc_rsi_sell'] and close >= bb_upper * 0.999:
                if not cfg['sc_block_sell']:
                    if tendencia != 'ALCISTA':
                        signal = 'SELL'
                    elif rsi > 70:
                        signal = 'SELL'

        elif estrategia == 'fibonacci':
            if fib_pos < 0.35 and rsi < cfg['sc_rsi_buy']:
                if not cfg['sc_block_buy']:
                    signal = 'BUY'
            elif fib_pos > 0.65 and rsi > cfg['sc_rsi_sell']:
                if not cfg['sc_block_sell']:
                    signal = 'SELL'

        if signal is None:
            continue

        # Cooldown por dirección
        cd_key = f"{signal}"
        if cd_key in cooldown_end and i < cooldown_end[cd_key]:
            continue

        # Calcular SL/TP
        sl_dist = atr * cfg['sc_sl_atr']
        tp_dist = atr * cfg['sc_tp_atr']

        if signal == 'BUY':
            sl = close - sl_dist
            tp = close + tp_dist
        else:
            sl = close + sl_dist
            tp = close - tp_dist

        # Simular resultado (barras futuras, max 60 = 5 horas)
        resultado = None
        pips = 0
        barra_cierre = min(i + 60, len(df) - 1)

        for j in range(i + 1, barra_cierre):
            future = df.iloc[j]
            if signal == 'BUY':
                if future['Low'] <= sl:
                    resultado = 'SL'
                    pips = -(sl_dist * cfg['pip_mult'])
                    break
                elif future['High'] >= tp:
                    resultado = 'TP'
                    pips = tp_dist * cfg['pip_mult']
                    break
            else:
                if future['High'] >= sl:
                    resultado = 'SL'
                    pips = -(sl_dist * cfg['pip_mult'])
                    break
                elif future['Low'] <= tp:
                    resultado = 'TP'
                    pips = tp_dist * cfg['pip_mult']
                    break

        if resultado is None:
            # Timeout — cerrar al precio actual
            exit_price = df.iloc[barra_cierre]['Close']
            if signal == 'BUY':
                pips = (exit_price - close) * cfg['pip_mult']
            else:
                pips = (close - exit_price) * cfg['pip_mult']
            resultado = 'TIMEOUT'

        trades.append({
            'fecha': idx, 'hora': hora, 'dia': dia,
            'signal': signal, 'resultado': resultado,
            'pips': round(pips, 1), 'estrategia': estrategia
        })

        last_signal_bar = i
        if resultado == 'SL':
            cooldown_end[cd_key] = i + COOLDOWN_BARS

    return trades

def backtest_premium(df_m15, cfg):
    """Backtest premium en M15"""
    trades = []
    cooldown_end = -999

    for i in range(60, len(df_m15)):
        row = df_m15.iloc[i]
        idx = df_m15.index[i]
        hora = idx.hour if hasattr(idx, 'hour') else 0
        dia = idx.dayofweek if hasattr(idx, 'dayofweek') else 0

        # Filtro horario
        h_ini, h_fin = cfg['pr_horario']
        if hora < h_ini or hora >= h_fin:
            continue

        # Filtro día
        if dia not in cfg['pr_dias']:
            continue

        # Cooldown premium (4 barras M15 = 1 hora)
        if i - cooldown_end < 4:
            continue

        rsi = row.get('rsi', 50)
        adx = row.get('adx', 0)
        atr = row.get('atr', 0)
        close = row['Close']
        bb_upper = row.get('bb_upper', close)
        bb_lower = row.get('bb_lower', close)
        tendencia = row.get('tendencia', 'NEUTRAL')

        if pd.isna(rsi) or pd.isna(adx) or pd.isna(atr) or atr == 0:
            continue

        if adx < cfg.get('pr_adx_min', 15):
            continue

        signal = None

        # RSI gate check
        rsi_gate_buy = cfg.get('pr_rsi_gate_buy')
        rsi_gate_sell = cfg.get('pr_rsi_gate_sell')

        # Breakout strategy
        if not pd.isna(bb_upper) and close > bb_upper and rsi > cfg.get('pr_rsi_ob', 65):
            if rsi_gate_sell is None or rsi > rsi_gate_sell:
                signal = 'BUY'  # Momentum breakout arriba
        elif not pd.isna(bb_lower) and close < bb_lower and rsi < cfg.get('pr_rsi_os', 35):
            if rsi_gate_buy is None or rsi < rsi_gate_buy:
                signal = 'SELL'  # Momentum breakout abajo

        # Reversal strategy
        if signal is None:
            if rsi < cfg.get('pr_rsi_os', 35) and tendencia == 'BAJISTA':
                if rsi_gate_buy is None or rsi < rsi_gate_buy:
                    signal = 'BUY'  # Reversal oversold
            elif rsi > cfg.get('pr_rsi_ob', 65) and tendencia == 'ALCISTA':
                if rsi_gate_sell is None or rsi > rsi_gate_sell:
                    signal = 'SELL'  # Reversal overbought

        if signal is None:
            continue

        # SL/TP
        sl_dist = atr * cfg['pr_sl_mult']
        tp_dist = atr * cfg['pr_tp1_mult']

        if signal == 'BUY':
            sl = close - sl_dist
            tp = close + tp_dist
        else:
            sl = close + sl_dist
            tp = close - tp_dist

        # Simular (max 96 barras M15 = 24 horas)
        resultado = None
        pips = 0
        barra_cierre = min(i + 96, len(df_m15) - 1)

        for j in range(i + 1, barra_cierre):
            future = df_m15.iloc[j]
            if signal == 'BUY':
                if future['Low'] <= sl:
                    resultado = 'SL'
                    pips = -(sl_dist * cfg['pip_mult'])
                    break
                elif future['High'] >= tp:
                    resultado = 'TP'
                    pips = tp_dist * cfg['pip_mult']
                    break
            else:
                if future['High'] >= sl:
                    resultado = 'SL'
                    pips = -(sl_dist * cfg['pip_mult'])
                    break
                elif future['Low'] <= tp:
                    resultado = 'TP'
                    pips = tp_dist * cfg['pip_mult']
                    break

        if resultado is None:
            exit_price = df_m15.iloc[barra_cierre]['Close']
            if signal == 'BUY':
                pips = (exit_price - close) * cfg['pip_mult']
            else:
                pips = (close - exit_price) * cfg['pip_mult']
            resultado = 'TIMEOUT'

        trades.append({
            'fecha': idx, 'hora': hora, 'dia': dia,
            'signal': signal, 'resultado': resultado,
            'pips': round(pips, 1), 'estrategia': 'premium'
        })

        cooldown_end = i

    return trades

def analizar_trades(trades, pip_name):
    """Analiza resultados de trades"""
    if not trades:
        return {'total': 0, 'ganancia': 0, 'wr': 0, 'wins': 0, 'losses': 0, 'timeouts': 0}

    total = len(trades)
    wins = sum(1 for t in trades if t['resultado'] == 'TP')
    losses = sum(1 for t in trades if t['resultado'] == 'SL')
    timeouts = sum(1 for t in trades if t['resultado'] == 'TIMEOUT')
    ganancia = sum(t['pips'] for t in trades)
    wr = (wins / max(1, wins + losses)) * 100

    # Mejor y peor trade
    mejor = max(trades, key=lambda t: t['pips'])
    peor = min(trades, key=lambda t: t['pips'])

    # Mejores horas
    horas = {}
    for t in trades:
        h = t['hora']
        if h not in horas:
            horas[h] = {'total': 0, 'pips': 0}
        horas[h]['total'] += 1
        horas[h]['pips'] += t['pips']

    mejor_hora = max(horas.items(), key=lambda x: x[1]['pips']) if horas else (0, {'pips': 0, 'total': 0})

    # Mejores días
    dias_nombre = ['Lun', 'Mar', 'Mie', 'Jue', 'Vie']
    dias = {}
    for t in trades:
        d = t['dia']
        if d not in dias:
            dias[d] = {'total': 0, 'pips': 0}
        dias[d]['total'] += 1
        dias[d]['pips'] += t['pips']

    mejor_dia = max(dias.items(), key=lambda x: x[1]['pips']) if dias else (0, {'pips': 0})

    # BUY vs SELL
    buys = [t for t in trades if t['signal'] == 'BUY']
    sells = [t for t in trades if t['signal'] == 'SELL']

    return {
        'total': total, 'ganancia': round(ganancia, 1), 'wr': round(wr, 1),
        'wins': wins, 'losses': losses, 'timeouts': timeouts,
        'mejor': mejor, 'peor': peor,
        'mejor_hora': mejor_hora, 'mejor_dia': mejor_dia,
        'horas': horas, 'dias': dias,
        'buys': len(buys), 'buy_pips': round(sum(t['pips'] for t in buys), 1),
        'sells': len(sells), 'sell_pips': round(sum(t['pips'] for t in sells), 1),
        'dias_nombre': dias_nombre
    }

def imprimir_resultado(nombre, display, cfg, sc_stats, pr_stats, pip_name):
    """Imprime resultado detallado de un par"""
    dias_nombre = ['Lun', 'Mar', 'Mie', 'Jue', 'Vie', 'Sab', 'Dom']

    print(f"\n{'='*70}")
    print(f"  {display} ({nombre})")
    print(f"{'='*70}")

    total_pips = 0

    # ── SCALPER ──
    if cfg.get('scalper', False):
        s = sc_stats
        total_pips += s['ganancia']
        block = []
        if cfg.get('sc_block_buy'): block.append("BUY bloqueado")
        if cfg.get('sc_block_sell'): block.append("SELL bloqueado")
        block_str = " | ".join(block) if block else "BUY & SELL"

        print(f"\n  SCALPER M5 — {cfg['sc_estrategia'].upper()}")
        print(f"  Direccion: {block_str}")
        print(f"  Horario: {cfg['sc_horario'][0]:02d}:00-{cfg['sc_horario'][1]:02d}:00 UTC")
        print(f"  SL: {cfg['sc_sl_atr']}x ATR | TP: {cfg['sc_tp_atr']}x ATR")
        print(f"  {'-'*50}")
        print(f"  Senales: {s['total']} | WR: {s['wr']}% | Ganancia: {s['ganancia']:+.1f} {pip_name}")
        print(f"  TP: {s['wins']} | SL: {s['losses']} | Timeout: {s['timeouts']}")
        if s['buys'] > 0:
            print(f"  BUY:  {s['buys']} ops → {s['buy_pips']:+.1f} {pip_name}")
        if s['sells'] > 0:
            print(f"  SELL: {s['sells']} ops → {s['sell_pips']:+.1f} {pip_name}")

        if s['total'] > 0:
            # Mejor hora
            mh = s['mejor_hora']
            print(f"  Mejor hora: {mh[0]:02d}:00 ({mh[1]['total']} ops, {mh[1]['pips']:+.1f} {pip_name})")

            # Mejor día
            md = s['mejor_dia']
            print(f"  Mejor dia:  {dias_nombre[md[0]]} ({md[1].get('total',0)} ops, {md[1]['pips']:+.1f} {pip_name})")

            # Tabla por día
            print(f"\n  Rendimiento por dia:")
            for d in sorted(s['dias'].keys()):
                info = s['dias'][d]
                emoji = "+" if info['pips'] > 0 else ""
                print(f"    {dias_nombre[d]}: {info['total']:3d} ops → {emoji}{info['pips']:.1f} {pip_name}")
    else:
        print(f"\n  SCALPER: Desactivado")

    # ── PREMIUM ──
    if cfg.get('premium', False):
        p = pr_stats
        total_pips += p['ganancia']

        print(f"\n  PREMIUM M15")
        print(f"  Horario: {cfg['pr_horario'][0]:02d}:00-{cfg['pr_horario'][1]:02d}:00 UTC")
        dias_activos = ", ".join([dias_nombre[d] for d in cfg['pr_dias']])
        print(f"  Dias: {dias_activos}")
        print(f"  SL: {cfg['pr_sl_mult']}x | TP1: {cfg['pr_tp1_mult']}x")
        print(f"  {'-'*50}")
        print(f"  Senales: {p['total']} | WR: {p['wr']}% | Ganancia: {p['ganancia']:+.1f} {pip_name}")
        print(f"  TP: {p['wins']} | SL: {p['losses']} | Timeout: {p['timeouts']}")
        if p['buys'] > 0:
            print(f"  BUY:  {p['buys']} ops → {p['buy_pips']:+.1f} {pip_name}")
        if p['sells'] > 0:
            print(f"  SELL: {p['sells']} ops → {p['sell_pips']:+.1f} {pip_name}")

        if p['total'] > 0:
            mh = p['mejor_hora']
            print(f"  Mejor hora: {mh[0]:02d}:00 ({mh[1]['total']} ops, {mh[1]['pips']:+.1f} {pip_name})")
    else:
        print(f"\n  PREMIUM: Desactivado")

    # ── TOTAL ──
    veredicto = "RENTABLE" if total_pips > 0 else "PERDEDOR"
    emoji = "[OK]" if total_pips > 0 else "[XX]"
    print(f"\n  TOTAL: {total_pips:+.1f} {pip_name} -> {emoji} {veredicto}")

    return total_pips


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    print("=" * 70)
    print("  BuySell365 Pro -- BACKTEST REALISTA 30 DIAS")
    print("  Simulacion exacta de como operara el bot a partir del lunes")
    print("  Filtros: horarios, dias, direccion, cooldown, tendencia")
    print("=" * 70)

    ranking = []

    for nombre, cfg in PARES.items():
        display = cfg['display']
        pip_name = cfg['pip_name']
        ticker = cfg['yf']

        print(f"\n{'-'*70}")
        print(f"  Descargando {display} ({ticker})...")
        print(f"{'-'*70}")

        # ── Scalper M5 ──
        sc_trades = []
        if cfg.get('scalper', False):
            df_m5 = descargar_datos(ticker, PERIODO)
            if df_m5 is not None and len(df_m5) > 100:
                print(f"    M5: {len(df_m5)} velas descargadas")
                df_m5 = calcular_indicadores(df_m5)
                sc_trades = backtest_scalper(df_m5, cfg)
            else:
                print(f"    M5: Error descargando datos")

        # ── Premium M15 ──
        pr_trades = []
        if cfg.get('premium', False):
            df_m15 = descargar_datos(ticker, PERIODO)
            if df_m15 is not None and len(df_m15) > 100:
                # Resample a M15
                df_m15_rs = df_m15.resample('15min').agg({
                    'Open': 'first', 'High': 'max', 'Low': 'min',
                    'Close': 'last', 'Volume': 'sum'
                }).dropna()
                if len(df_m15_rs) > 60:
                    print(f"    M15: {len(df_m15_rs)} velas (resampled)")
                    df_m15_rs = calcular_indicadores(df_m15_rs)
                    pr_trades = backtest_premium(df_m15_rs, cfg)
            else:
                print(f"    M15: Error descargando datos")

        sc_stats = analizar_trades(sc_trades, pip_name)
        pr_stats = analizar_trades(pr_trades, pip_name)

        total = imprimir_resultado(nombre, display, cfg, sc_stats, pr_stats, pip_name)
        ranking.append((display, total, pip_name, cfg.get('scalper', False), cfg.get('premium', False)))

    # ── RANKING FINAL ──
    ranking.sort(key=lambda x: x[1], reverse=True)

    print(f"\n{'='*70}")
    print(f"  RANKING FINAL — 30 DIAS")
    print(f"{'='*70}")

    total_global = 0
    rentables = 0
    for i, (display, total, pip_name, has_sc, has_pr) in enumerate(ranking, 1):
        modo = []
        if has_sc: modo.append("SC")
        if has_pr: modo.append("PR")
        modo_str = "+".join(modo)
        veredicto = "RENTABLE" if total > 0 else "PERDEDOR"
        emoji = "+" if total > 0 else ""
        print(f"  {i}. {display:15s} {emoji}{total:>8.1f} {pip_name:4s}  [{modo_str:5s}]  [{veredicto}]")
        total_global += total
        if total > 0:
            rentables += 1

    print(f"\n  TOTAL GLOBAL: {total_global:+.1f}")
    print(f"  Pares rentables: {rentables}/{len(ranking)}")
    print(f"{'='*70}")

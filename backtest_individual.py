#!/usr/bin/env python3
"""
BuySell365 Pro — BACKTEST INDIVIDUAL POR PAR v1.0

Testea CADA par por separado con sus propios parametros de PAR_PROFILES.
Muestra scalper Y premium para cada par individualmente.

Uso:
  python backtest_individual.py              # Todos los pares
  python backtest_individual.py GOLD         # Solo GOLD
  python backtest_individual.py EURUSD       # Solo EURUSD
  python backtest_individual.py AUDCAD       # Solo AUDCAD
"""

import os
import sys
import io
import warnings
from datetime import datetime
from collections import defaultdict

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np
import pandas_ta as ta
import yfinance as yf

warnings.filterwarnings("ignore")

# ── Cargar PAR_PROFILES desde bot.py ──
def load_par_profiles():
    bot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")
    with open(bot_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Extract PAR_PROFILES with balanced braces
    for li, ln in enumerate(lines):
        if "PAR_PROFILES = {" in ln:
            brace_count = 0
            pp_lines = []
            for j in range(li, min(li + 500, len(lines))):
                pp_lines.append(lines[j])
                brace_count += lines[j].count('{') - lines[j].count('}')
                if brace_count == 0 and j > li:
                    break
            pp_code = ''.join(pp_lines)
            ns = {}
            exec(pp_code, ns)
            return ns['PAR_PROFILES']
    raise RuntimeError("PAR_PROFILES not found in bot.py")


PAR_PROFILES = load_par_profiles()

# Tickers de yfinance para pares que no tienen .yf (scalper-only)
YF_FALLBACK = {
    "AUDCAD": "AUDCAD=X",
    "EURCHF": "EURCHF=X",
    "USDCAD": "USDCAD=X",
    "GBPUSD": "GBPUSD=X",
}


def get_col(df, prefijo):
    cols = [c for c in df.columns if str(c).startswith(prefijo)]
    if cols:
        return df[cols[0]]
    cols = [c for c in df.columns if prefijo in str(c)]
    if cols:
        return df[cols[0]]
    raise KeyError(f"Columna '{prefijo}' no encontrada")


def download_data(ticker_yf, timeframe="5m", period="60d"):
    df = yf.download(ticker_yf, period=period, interval=timeframe, progress=False, auto_adjust=True)
    if hasattr(df.columns, 'levels') and df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    return df


def backtest_scalper_par(nombre, profile):
    """Backtest scalper para un par individual."""
    sc = profile.get("scalper", {})
    if not sc.get("enabled"):
        return None

    identity = profile["identity"]
    ticker_yf = identity.get("yf") or YF_FALLBACK.get(nombre)
    if not ticker_yf:
        return None

    # Parametros del perfil
    rsi_period = sc.get("rsi_period", 7)
    bb_period = sc.get("bb_period", 20)
    bb_std = sc.get("bb_std", 2.0)
    adx_period = sc.get("adx_period", 10)
    adx_min = sc.get("adx_min", 10)
    adx_max = sc.get("adx_max", 50)
    rsi_buy = sc.get("rsi_buy", 40)
    rsi_sell = sc.get("rsi_sell", 60)
    sl_atr = sc.get("sl_atr", 1.2)
    tp_atr = sc.get("tp_atr", 2.0)
    vol_min = sc.get("vol_min", 0.3)
    estrategia = sc.get("strategies", ["bb_rsi"])[0]
    fib_buy = sc.get("fib_buy", 0.35)
    fib_sell = sc.get("fib_sell", 0.65)

    beh = profile.get("behavior", {})
    block_buy = beh.get("block_buy", False)
    block_sell = beh.get("block_sell", False)

    tf = profile.get("time_filter", {})
    best_hours = tf.get("best_hours_utc", [(0, 24)])
    best_days = tf.get("best_days", [0, 1, 2, 3, 4])

    pip_size = identity.get("pip_size", 0.0001)
    if identity["category"] in ("commodity", "indice"):
        pip_mult = 1
        pip_name = "pts"
    else:
        pip_mult = int(1 / pip_size) if pip_size < 1 else 1
        pip_name = "pips"

    # Descargar datos M5
    print(f"    Descargando {ticker_yf} M5 60d...", end=" ")
    try:
        df = download_data(ticker_yf, "5m", "60d")
    except Exception as e:
        print(f"ERROR: {e}")
        return None

    if len(df) < 300:
        print(f"Solo {len(df)} velas (min 300)")
        return None
    print(f"{len(df):,} velas")

    # Calcular indicadores
    rsi = ta.rsi(df["Close"], length=rsi_period)
    bb = ta.bbands(df["Close"], length=bb_period, std=bb_std)
    ema20 = ta.ema(df["Close"], length=20)
    ema50 = ta.ema(df["Close"], length=50)
    adx_df = ta.adx(df["High"], df["Low"], df["Close"], length=adx_period)
    atr = ta.atr(df["High"], df["Low"], df["Close"], length=14)
    vol = df.get("Volume", pd.Series(0, index=df.index))
    vol_sma = ta.sma(vol, length=20)

    if rsi is None or bb is None:
        print("    Error calculando indicadores")
        return None

    bbl = get_col(bb, "BBL_")
    bbu = get_col(bb, "BBU_")
    adx_col = get_col(adx_df, "ADX_")

    timeout_candles = 9  # 45 min
    cooldown = 6  # 30 min

    results = {"total": 0, "wins": 0, "losses": 0, "timeout": 0, "pips": 0.0,
               "buy": 0, "sell": 0, "buy_wins": 0, "sell_wins": 0,
               "buy_pips": 0.0, "sell_pips": 0.0, "trades": []}

    last_signal_idx = -999

    for i in range(200, len(df) - timeout_candles - 1):
        ts = df.index[i]
        if hasattr(ts, 'hour'):
            # Filtro horario
            h = ts.hour
            in_hours = any(h_start <= h < h_end for h_start, h_end in best_hours)
            if not in_hours:
                continue
            if ts.weekday() >= 5:
                continue
            if best_days and ts.weekday() not in best_days:
                continue

        if (i - last_signal_idx) < cooldown:
            continue

        curr_close = float(df['Close'].iloc[i])
        curr_low = float(df['Low'].iloc[i])
        curr_high = float(df['High'].iloc[i])
        curr_rsi = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50
        curr_bbl = float(bbl.iloc[i]) if not pd.isna(bbl.iloc[i]) else curr_close
        curr_bbu = float(bbu.iloc[i]) if not pd.isna(bbu.iloc[i]) else curr_close
        bbm = (curr_bbl + curr_bbu) / 2
        curr_adx = float(adx_col.iloc[i]) if not pd.isna(adx_col.iloc[i]) else 20
        curr_atr = float(atr.iloc[i]) if not pd.isna(atr.iloc[i]) else 0

        if curr_atr <= 0 or any(np.isnan(v) for v in [curr_rsi, curr_bbl, curr_bbu, curr_adx]):
            continue

        # Filtro ADX
        if curr_adx < adx_min or curr_adx > adx_max:
            continue

        # Filtro volumen
        _vs = float(vol_sma.iloc[i]) if vol_sma is not None and not pd.isna(vol_sma.iloc[i]) else 0
        _vr = float(vol.iloc[i]) / _vs if _vs > 0 else 1.0
        if _vr > 0 and _vr < vol_min:
            continue

        tipo = None
        variante = ""

        if estrategia == "fibonacci":
            fib_lookback = min(240, i)
            _fh = df['High'].iloc[max(0, i - fib_lookback):i + 1].max()
            _fl = df['Low'].iloc[max(0, i - fib_lookback):i + 1].min()
            _fr = _fh - _fl
            _fib_pos = (curr_close - _fl) / _fr if _fr > 0 else 0.5

            if _fib_pos < fib_buy and curr_rsi < rsi_buy:
                tipo = "BUY"; variante = "FIB"
            elif _fib_pos > fib_sell and curr_rsi > rsi_sell:
                tipo = "SELL"; variante = "FIB"
        else:
            # BB+RSI variants
            if curr_low <= curr_bbl and curr_rsi < rsi_buy:
                tipo = "BUY"; variante = "A"
            elif curr_high >= curr_bbu and curr_rsi > rsi_sell:
                tipo = "SELL"; variante = "A"
            elif curr_rsi < 30 and curr_close < (curr_bbl + (bbm - curr_bbl) * 0.3):
                tipo = "BUY"; variante = "C"
            elif curr_rsi > 70 and curr_close > (curr_bbu - (curr_bbu - bbm) * 0.3):
                tipo = "SELL"; variante = "C"

        # Block filters
        if tipo == "BUY" and block_buy:
            tipo = None
        if tipo == "SELL" and block_sell:
            tipo = None

        if tipo is None:
            continue

        # SL/TP
        sl_dist = curr_atr * sl_atr
        tp_dist = curr_atr * tp_atr

        if tipo == "BUY":
            sl_price = curr_close - sl_dist
            tp_price = curr_close + tp_dist
        else:
            sl_price = curr_close + sl_dist
            tp_price = curr_close - tp_dist

        # Simular resultado
        resultado = "TIMEOUT"
        precio_cierre = curr_close
        for j in range(1, min(timeout_candles + 1, len(df) - i)):
            high_f = float(df['High'].iloc[i + j])
            low_f = float(df['Low'].iloc[i + j])
            if tipo == "BUY":
                if low_f <= sl_price:
                    resultado = "SL"; precio_cierre = sl_price; break
                if high_f >= tp_price:
                    resultado = "TP"; precio_cierre = tp_price; break
            else:
                if high_f >= sl_price:
                    resultado = "SL"; precio_cierre = sl_price; break
                if low_f <= tp_price:
                    resultado = "TP"; precio_cierre = tp_price; break

        if resultado == "TIMEOUT":
            t_idx = min(i + timeout_candles, len(df) - 1)
            precio_cierre = float(df['Close'].iloc[t_idx])

        pips = ((precio_cierre - curr_close) if tipo == "BUY" else (curr_close - precio_cierre)) * pip_mult

        results["total"] += 1
        results["pips"] += pips
        if tipo == "BUY":
            results["buy"] += 1; results["buy_pips"] += pips
            if resultado == "TP": results["buy_wins"] += 1
        else:
            results["sell"] += 1; results["sell_pips"] += pips
            if resultado == "TP": results["sell_wins"] += 1
        if resultado == "TP": results["wins"] += 1
        elif resultado == "SL": results["losses"] += 1
        else: results["timeout"] += 1

        results["trades"].append({
            "fecha": ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, 'strftime') else str(ts),
            "tipo": tipo, "variante": variante, "rsi": round(curr_rsi, 1),
            "adx": round(curr_adx, 1), "resultado": resultado,
            "pips": round(pips, 1),
        })
        last_signal_idx = i

    results["pip_name"] = pip_name
    return results


def backtest_premium_par(nombre, profile):
    """Backtest premium para un par individual."""
    prem = profile.get("premium", {})
    if not prem.get("enabled"):
        return None

    identity = profile["identity"]
    ticker_yf = identity.get("yf")
    if not ticker_yf:
        return None

    # Cargar funciones del bot
    bot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.py")
    with open(bot_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    def extract_function(source_lines, func_sig):
        func_lines = []
        found = False
        base_indent = 0
        for ln in source_lines:
            if func_sig in ln and not found:
                found = True
                base_indent = len(ln) - len(ln.lstrip())
                func_lines.append(ln)
                continue
            if found:
                stripped = ln.strip()
                if stripped == "" or stripped.startswith("#"):
                    func_lines.append(ln)
                    continue
                current_indent = len(ln) - len(ln.lstrip())
                if current_indent <= base_indent and stripped:
                    break
                func_lines.append(ln)
        return '\n'.join(func_lines) if func_lines else ""

    needed_funcs = [
        "def get_categoria(", "def get_col(", "def detectar_patrones_velas(",
        "def calcular_pivot_points_df(", "def _calcular_features_ml(",
        "def predecir_direccion_ml(", "def _calcular_rango_asiatico(",
        "def calcular_indicadores_profesionales(", "def evaluar_senal_profesional(",
        "def calcular_niveles_3tp(",
    ]

    stubs = [
        "def log_sistema(msg, level='info'): pass",
        "def log_op(msg): pass", "def log_senal(msg): pass",
        "def confirmar_tendencia_1h(ticker, tipo): return True",
        "def hay_noticia_alto_impacto(*a, **kw): return False",
        "def en_horario_mercado(ticker): return True",
        "def en_horario_mt5(ticker): return True",
        "def filtro_fear_greed(ticker, tipo): return True",
        "def hay_correlacion_peligrosa(ticker, tipo): return False",
        "def filtro_multi_timeframe(ticker, tipo): return True",
        "def _kill_switch_activo(): return False",
        "def get_min_score_efectivo(): return 3",
        "def get_min_score_calibrado(ticker): return 3",
        "MODO_RIESGO = 'normal'", "MIN_SCORE = 3",
    ]

    # PAR_PROFILES
    par_code = ""
    for li, ln in enumerate(lines):
        if "PAR_PROFILES = {" in ln:
            bc = 0
            pp = []
            for j in range(li, min(li + 500, len(lines))):
                pp.append(lines[j])
                bc += lines[j].count('{') - lines[j].count('}')
                if bc == 0 and j > li:
                    break
            par_code = ''.join(pp)
            break

    lookup_code = ""
    for ln in lines:
        if "_PROFILE_BY_YF" in ln and "=" in ln and ln.strip().startswith("_"):
            lookup_code += ln + "\n"
        if "_PROFILE_BY_MT5" in ln and "=" in ln and ln.strip().startswith("_"):
            lookup_code += ln + "\n"

    gpf = extract_function(lines, "def get_par_profile(")
    extracted = [extract_function(lines, fs) for fs in needed_funcs]
    extracted = [e for e in extracted if e]

    full_code = '\n'.join(stubs) + '\n\n'
    if par_code: full_code += par_code + '\n\n'
    if lookup_code: full_code += lookup_code + '\n'
    if gpf: full_code += gpf + '\n\n'
    full_code += '\n\n'.join(extracted)

    import pytz as _pytz
    ns = {"pd": pd, "np": np, "ta": ta, "threading": __import__("threading"),
          "pytz": _pytz,
          "logger": type('L', (), {"info": lambda s, m: None, "warning": lambda s, m: None, "debug": lambda s, m: None})(),
          "MT5_AVAILABLE": False, "mt5": None}
    try:
        exec(compile(full_code, '<backtest_premium>', 'exec'), ns, ns)
    except Exception as e:
        print(f"    Error cargando funciones premium: {e}")
        return None

    calcular_ind = ns.get("calcular_indicadores_profesionales")
    evaluar = ns.get("evaluar_senal_profesional")
    calc_niveles = ns.get("calcular_niveles_3tp")
    if not all([calcular_ind, evaluar, calc_niveles]):
        print("    No se pudieron extraer funciones premium")
        return None

    # Descargar datos 15m
    print(f"    Descargando {ticker_yf} M15 60d...", end=" ")
    try:
        df = download_data(ticker_yf, "15m", "60d")
    except Exception as e:
        print(f"ERROR: {e}")
        return None

    if len(df) < 250:
        print(f"Solo {len(df)} velas")
        return None
    print(f"{len(df):,} velas")

    pip_size = identity.get("pip_size", 0.0001)
    if identity["category"] in ("commodity", "indice"):
        pip_mult = 1
        pip_name = "pts"
    else:
        pip_mult = int(1 / pip_size) if pip_size < 1 else 1
        pip_name = "pips"

    results = {"total": 0, "wins": 0, "losses": 0, "timeout": 0, "pips": 0.0,
               "buy": 0, "sell": 0, "buy_wins": 0, "sell_wins": 0,
               "buy_pips": 0.0, "sell_pips": 0.0,
               "breakout": 0, "reversal": 0, "trades": []}

    for i in range(200, len(df) - 96, 4):
        ts = df.index[i]
        if hasattr(ts, 'hour'):
            if ts.hour < 7 or ts.hour >= 21:
                continue
            if ts.weekday() >= 5:
                continue

        df_slice = df.iloc[:i + 1].copy()
        precio = float(df_slice['Close'].iloc[-1])

        try:
            ind = calcular_ind(df_slice, precio, ticker_yf)
        except:
            continue
        if not ind:
            continue

        try:
            tipo, score, razones = evaluar(ind, ticker_yf)
        except:
            continue

        if tipo is None or score < 4:
            continue

        estrategia = "breakout"
        for r in razones:
            if "Reversi" in r or "Divergencia" in r:
                estrategia = "reversal"; break
            elif "Breakout" in r:
                estrategia = "breakout"; break

        try:
            niveles = calc_niveles(precio, tipo, ind.get('atr_1h', ind.get('atr', 0)), ticker_yf, estrategia=estrategia)
        except:
            continue

        sl = niveles['sl']
        tp1 = niveles['tp1']

        # Simular 96 velas (24h)
        resultado = "TIMEOUT"
        precio_cierre = precio
        for j in range(1, min(97, len(df) - i)):
            h_f = float(df['High'].iloc[i + j])
            l_f = float(df['Low'].iloc[i + j])
            if tipo == "COMPRA":
                if l_f <= precio - sl:
                    resultado = "SL"; precio_cierre = precio - sl; break
                if h_f >= precio + tp1:
                    resultado = "TP1"; precio_cierre = precio + tp1; break
            else:
                if h_f >= precio + sl:
                    resultado = "SL"; precio_cierre = precio + sl; break
                if l_f <= precio - tp1:
                    resultado = "TP1"; precio_cierre = precio - tp1; break

        if resultado == "TIMEOUT":
            t_idx = min(i + 96, len(df) - 1)
            precio_cierre = float(df['Close'].iloc[t_idx])

        if tipo == "COMPRA":
            pips = (precio_cierre - precio) * pip_mult
        else:
            pips = (precio - precio_cierre) * pip_mult

        results["total"] += 1
        results["pips"] += pips
        if tipo == "COMPRA":
            results["buy"] += 1; results["buy_pips"] += pips
            if "TP" in resultado: results["buy_wins"] += 1
        else:
            results["sell"] += 1; results["sell_pips"] += pips
            if "TP" in resultado: results["sell_wins"] += 1
        if "TP" in resultado: results["wins"] += 1
        elif resultado == "SL": results["losses"] += 1
        else: results["timeout"] += 1
        if estrategia == "breakout": results["breakout"] += 1
        else: results["reversal"] += 1

        results["trades"].append({
            "fecha": ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, 'strftime') else str(ts),
            "tipo": tipo, "score": score, "estrategia": estrategia,
            "resultado": resultado, "pips": round(pips, 1),
        })

    results["pip_name"] = pip_name
    return results


def print_par_results(nombre, profile, scalper_res, premium_res):
    """Imprime resultados de un par."""
    display = profile["identity"]["display"]

    print()
    print(f"{'=' * 60}")
    print(f"  {display} ({nombre})")
    print(f"  Categoria: {profile['identity']['category']}")
    print(f"{'=' * 60}")

    # Scalper
    if scalper_res and scalper_res["total"] > 0:
        s = scalper_res
        wr = s["wins"] / s["total"] * 100
        u = s["pip_name"]
        print(f"\n  SCALPER (M5)")
        print(f"  Estrategia: {profile['scalper']['strategies']}")
        print(f"  RSI: {profile['scalper'].get('rsi_buy')}/{profile['scalper'].get('rsi_sell')} | ADX: {profile['scalper'].get('adx_min')}-{profile['scalper'].get('adx_max')} | SL: {profile['scalper'].get('sl_atr')}x | TP: {profile['scalper'].get('tp_atr')}x")
        print(f"  Block BUY: {profile['behavior'].get('block_buy')} | Block SELL: {profile['behavior'].get('block_sell')}")
        print(f"  {'─' * 50}")
        print(f"  Senales: {s['total']} | WR: {wr:.1f}% | Ganancia: {s['pips']:+.1f} {u}")
        print(f"  Wins: {s['wins']} | Losses: {s['losses']} | Timeout: {s['timeout']}")
        if s["buy"] > 0:
            bwr = s["buy_wins"] / s["buy"] * 100
            print(f"  BUY:  {s['buy']:3d} senales, {bwr:5.1f}% WR, {s['buy_pips']:+.1f} {u}")
        if s["sell"] > 0:
            swr = s["sell_wins"] / s["sell"] * 100
            print(f"  SELL: {s['sell']:3d} senales, {swr:5.1f}% WR, {s['sell_pips']:+.1f} {u}")

        # Top 3 mejores
        if s["trades"]:
            best = sorted(s["trades"], key=lambda x: x["pips"], reverse=True)[:3]
            print(f"  Top 3: ", end="")
            for t in best:
                print(f"{t['fecha'][-5:]} {t['tipo']} {t['pips']:+.1f} | ", end="")
            print()
    elif profile.get("scalper", {}).get("enabled"):
        print(f"\n  SCALPER: Sin datos o 0 senales")
    else:
        print(f"\n  SCALPER: Desactivado para este par")

    # Premium
    if premium_res and premium_res["total"] > 0:
        s = premium_res
        wr = s["wins"] / s["total"] * 100
        u = s["pip_name"]
        print(f"\n  PREMIUM (M15)")
        print(f"  Estrategias: {profile['premium']['strategies']}")
        print(f"  RSI OS/OB: {profile['premium'].get('rsi_os')}/{profile['premium'].get('rsi_ob')} | ADX min: {profile['premium'].get('adx_min')} | Score min: {profile['premium'].get('min_score')}")
        print(f"  RSI gate BUY: {profile['premium'].get('rsi_gate_buy')} | SELL: {profile['premium'].get('rsi_gate_sell')}")
        print(f"  SL: {profile['sl_tp'].get('sl_mult')}x | TP1: {profile['sl_tp'].get('tp1_mult')}x | TP2: {profile['sl_tp'].get('tp2_mult')}x")
        print(f"  {'─' * 50}")
        print(f"  Senales: {s['total']} | WR: {wr:.1f}% | Ganancia: {s['pips']:+.1f} {u}")
        print(f"  Wins: {s['wins']} | Losses: {s['losses']} | Timeout: {s['timeout']}")
        print(f"  Breakout: {s['breakout']} | Reversal: {s['reversal']}")
        if s["buy"] > 0:
            bwr = s["buy_wins"] / s["buy"] * 100
            print(f"  COMPRA: {s['buy']:3d} senales, {bwr:5.1f}% WR, {s['buy_pips']:+.1f} {u}")
        if s["sell"] > 0:
            swr = s["sell_wins"] / s["sell"] * 100
            print(f"  VENTA: {s['sell']:3d} senales, {swr:5.1f}% WR, {s['sell_pips']:+.1f} {u}")
    elif profile.get("premium", {}).get("enabled"):
        print(f"\n  PREMIUM: 0 senales generadas")
    else:
        print(f"\n  PREMIUM: Desactivado para este par")

    # Veredicto
    total_pips = 0
    if scalper_res and scalper_res["total"] > 0:
        total_pips += scalper_res["pips"]
    if premium_res and premium_res["total"] > 0:
        total_pips += premium_res["pips"]

    print(f"\n  TOTAL COMBINADO: {total_pips:+.1f}")
    if total_pips > 50:
        print(f"  VEREDICTO: RENTABLE")
    elif total_pips > 0:
        print(f"  VEREDICTO: MARGINAL")
    else:
        print(f"  VEREDICTO: PERDEDOR")


def main():
    # Filtro por par (argumento de linea de comandos)
    filtro = sys.argv[1].upper() if len(sys.argv) > 1 else None

    print("=" * 60)
    print("  BuySell365 Pro — BACKTEST INDIVIDUAL POR PAR v1.0")
    print("  Cada par con sus propios parametros de PAR_PROFILES")
    print(f"  {len(PAR_PROFILES)} pares configurados")
    print("=" * 60)

    pares_a_testear = {}
    for nombre, profile in PAR_PROFILES.items():
        if filtro and filtro not in nombre.upper():
            continue
        pares_a_testear[nombre] = profile

    if not pares_a_testear:
        print(f"\nNo se encontro el par '{filtro}'. Disponibles: {list(PAR_PROFILES.keys())}")
        return

    print(f"\nPares a testear: {list(pares_a_testear.keys())}")

    resumen = []
    for nombre, profile in pares_a_testear.items():
        display = profile["identity"]["display"]
        print(f"\n{'─' * 60}")
        print(f"  Testeando {display}...")
        print(f"{'─' * 60}")

        scalper_res = backtest_scalper_par(nombre, profile)
        premium_res = backtest_premium_par(nombre, profile)
        print_par_results(nombre, profile, scalper_res, premium_res)

        total = 0
        if scalper_res and scalper_res["total"] > 0:
            total += scalper_res["pips"]
        if premium_res and premium_res["total"] > 0:
            total += premium_res["pips"]
        resumen.append((display, total))

    # Resumen final
    print(f"\n{'=' * 60}")
    print(f"  RANKING FINAL")
    print(f"{'=' * 60}")
    resumen.sort(key=lambda x: x[1], reverse=True)
    for i, (display, pips) in enumerate(resumen, 1):
        tag = "RENTABLE" if pips > 50 else ("MARGINAL" if pips > 0 else "PERDEDOR")
        print(f"  {i}. {display:12s} {pips:+10.1f}  [{tag}]")

    total_global = sum(p for _, p in resumen)
    print(f"\n  TOTAL GLOBAL: {total_global:+.1f}")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado.")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()

#!/usr/bin/env python3
"""
BuySell365 Pro — SCALPER BACKTESTER v1.0

Estrategia: Bollinger Bands(20,2) + RSI(7) Mean Reversion en M5
Activos: XAUUSD, EUR/USD, GBP/USD, NASDAQ
Periodo: 60 dias (limite yfinance para 5m)

Uso: python backtest_scalper.py
"""

import os
import sys
import io
import warnings
from datetime import datetime
from collections import defaultdict

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np
import pandas_ta as ta
import yfinance as yf

warnings.filterwarnings("ignore")

# -- Configuracion --
PERIODO_DATOS = "60d"
INTERVALO = "5m"

# Activos del scalper con PAR_PROFILES params
ACTIVOS = {
    "GOLD":    {"ticker": "GC=F",     "hora_ini": 7,  "hora_fin": 17, "pip_mult": 1,     "pip_name": "pts",
                "rsi_buy": 40, "rsi_sell": 60, "adx_min": 10, "adx_max": 50, "sl_atr": 1.2, "tp_atr": 2.0,
                "block_buy": True, "block_sell": False, "estrategia": "bb_rsi",
                "bb_period": 20, "bb_std": 2.0, "rsi_period": 7, "adx_period": 10},
    "EUR/USD": {"ticker": "EURUSD=X", "hora_ini": 7,  "hora_fin": 17, "pip_mult": 10000, "pip_name": "pips",
                "rsi_buy": 40, "rsi_sell": 60, "adx_min": 10, "adx_max": 50, "sl_atr": 1.2, "tp_atr": 2.0,
                "block_buy": False, "block_sell": False, "estrategia": "bb_rsi",
                "bb_period": 20, "bb_std": 2.0, "rsi_period": 7, "adx_period": 10},
    "NASDAQ":  {"ticker": "NQ=F",     "hora_ini": 13, "hora_fin": 20, "pip_mult": 1,     "pip_name": "pts",
                "rsi_buy": 40, "rsi_sell": 60, "adx_min": 10, "adx_max": 50, "sl_atr": 1.2, "tp_atr": 2.0,
                "block_buy": False, "block_sell": True, "estrategia": "bb_rsi",
                "bb_period": 20, "bb_std": 2.0, "rsi_period": 7, "adx_period": 10},
    "AUD/CAD": {"ticker": "AUDCAD=X", "hora_ini": 0,  "hora_fin": 14, "pip_mult": 10000, "pip_name": "pips",
                "rsi_buy": 45, "rsi_sell": 55, "adx_min": 10, "adx_max": 50, "sl_atr": 1.0, "tp_atr": 1.8,
                "block_buy": False, "block_sell": False, "estrategia": "fibonacci",
                "fib_buy": 0.35, "fib_sell": 0.65,
                "bb_period": 20, "bb_std": 2.0, "rsi_period": 7, "adx_period": 10},
    "EUR/CHF": {"ticker": "EURCHF=X", "hora_ini": 7,  "hora_fin": 16, "pip_mult": 10000, "pip_name": "pips",
                "rsi_buy": 45, "rsi_sell": 55, "adx_min": 10, "adx_max": 50, "sl_atr": 1.0, "tp_atr": 1.8,
                "block_buy": False, "block_sell": False, "estrategia": "fibonacci",
                "fib_buy": 0.35, "fib_sell": 0.65,
                "bb_period": 20, "bb_std": 2.0, "rsi_period": 7, "adx_period": 10},
    "USD/CAD": {"ticker": "USDCAD=X", "hora_ini": 13, "hora_fin": 20, "pip_mult": 10000, "pip_name": "pips",
                "rsi_buy": 45, "rsi_sell": 55, "adx_min": 10, "adx_max": 50, "sl_atr": 1.0, "tp_atr": 1.8,
                "block_buy": False, "block_sell": False, "estrategia": "fibonacci",
                "fib_buy": 0.35, "fib_sell": 0.65,
                "bb_period": 20, "bb_std": 2.0, "rsi_period": 7, "adx_period": 10},
}

# Defaults (used as fallback)
BB_PERIOD = 20
BB_STD = 2.0
RSI_PERIOD = 7
EMA_PERIOD = 50
ADX_PERIOD = 10
ADX_MIN = 10
ADX_MAX = 50
ATR_PERIOD = 14
SL_ATR_MULT = 1.2
TP_ATR_MULT = 2.0
TIMEOUT_CANDLES = 9   # 45 min / 5 min = 9 velas (matching bot timeout)
COOLDOWN_CANDLES = 6  # 30 min cooldown (matching bot)

RSI_BUY_ZONE = 40
RSI_SELL_ZONE = 60


def get_col(df, prefijo):
    """Busca columna por prefijo de forma segura."""
    cols = [c for c in df.columns if str(c).startswith(prefijo)]
    if cols:
        return df[cols[0]]
    cols = [c for c in df.columns if prefijo in str(c)]
    if cols:
        return df[cols[0]]
    raise KeyError(f"No se encontro columna '{prefijo}'. Columnas: {list(df.columns)}")


def calcular_indicadores(df):
    """Calcula BB, RSI, EMA, ADX, ATR sobre el DataFrame."""
    # Bollinger Bands — uso directo de funciones pandas_ta (no .ta accessor)
    bb = ta.bbands(df["Close"], length=BB_PERIOD, std=BB_STD)
    if bb is not None:
        df = pd.concat([df, bb], axis=1)

    # RSI
    rsi = ta.rsi(df["Close"], length=RSI_PERIOD)
    if rsi is not None:
        df[f"RSI_{RSI_PERIOD}"] = rsi

    # EMA 50
    ema = ta.ema(df["Close"], length=EMA_PERIOD)
    if ema is not None:
        df[f"EMA_{EMA_PERIOD}"] = ema

    # ADX
    adx = ta.adx(df["High"], df["Low"], df["Close"], length=ADX_PERIOD)
    if adx is not None:
        df = pd.concat([df, adx], axis=1)

    # ATR
    atr = ta.atr(df["High"], df["Low"], df["Close"], length=ATR_PERIOD)
    if atr is not None:
        df[f"ATRr_{ATR_PERIOD}"] = atr

    return df


def run_backtest():
    """Ejecuta el backtest scalper completo."""

    print("=" * 60)
    print("  BuySell365 Pro -- SCALPER BACKTESTER v1.0")
    print("  Estrategia: BB(20,2) + RSI(7) Mean Reversion M5")
    print("=" * 60)
    print()

    # -- Descargar datos --
    print(f"Descargando datos historicos ({PERIODO_DATOS}, {INTERVALO})...")
    datos = {}
    for nombre, cfg in ACTIVOS.items():
        ticker = cfg["ticker"]
        try:
            df = yf.download(ticker, period=PERIODO_DATOS, interval=INTERVALO,
                             progress=False, auto_adjust=True)
            if hasattr(df.columns, 'levels') and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
            df = df.dropna()
            if len(df) >= 250:
                datos[nombre] = df
                f_ini = df.index[0].strftime("%Y-%m-%d")
                f_fin = df.index[-1].strftime("%Y-%m-%d")
                print(f"  OK {nombre:10s} -- {len(df):,} velas ({f_ini} -> {f_fin})")
            else:
                print(f"  !! {nombre:10s} -- Solo {len(df)} velas (min 250)")
        except Exception as e:
            print(f"  XX {nombre:10s} -- Error: {e}")

    if not datos:
        print("\nNo se pudieron descargar datos.")
        return

    # -- Simulacion --
    print(f"\nSimulando senales scalper...")
    print(f"  Ventana: 200 velas | Paso: 1 vela M5 | Timeout: {TIMEOUT_CANDLES} velas (60 min)")
    print()

    todas_senales = []
    stats_activo = defaultdict(lambda: {"total": 0, "wins": 0, "losses": 0, "timeout": 0,
                                         "pips": 0.0, "buy": 0, "sell": 0,
                                         "buy_wins": 0, "sell_wins": 0,
                                         "buy_pips": 0.0, "sell_pips": 0.0})

    for nombre, df_full in datos.items():
        cfg = ACTIVOS[nombre]
        hora_ini = cfg["hora_ini"]
        hora_fin = cfg["hora_fin"]
        pip_mult = cfg["pip_mult"]
        n_senales = 0
        last_signal_idx = -999  # Para cooldown

        # Filtrar solo horario de trading
        # Primero calculamos indicadores sobre todo el dataset
        df_calc = df_full.copy()
        df_calc = calcular_indicadores(df_calc)

        # Verificar que tenemos todas las columnas necesarias
        try:
            _ = get_col(df_calc, "BBL_")
            _ = get_col(df_calc, "BBU_")
            _ = get_col(df_calc, "RSI_")
            _ = get_col(df_calc, "EMA_")
            _ = get_col(df_calc, "ADX_")
            _ = get_col(df_calc, "ATR")
        except KeyError as e:
            print(f"  {nombre}: Faltan indicadores - {e}")
            continue

        bbl = get_col(df_calc, "BBL_")
        bbu = get_col(df_calc, "BBU_")
        rsi = get_col(df_calc, "RSI_")
        ema = get_col(df_calc, "EMA_")
        adx = get_col(df_calc, "ADX_")
        atr = get_col(df_calc, "ATR")

        # Walk forward desde vela 200
        for i in range(200, len(df_calc) - TIMEOUT_CANDLES - 1):
            ts = df_calc.index[i]

            # Filtrar horario
            if hasattr(ts, 'hour'):
                if ts.hour < hora_ini or ts.hour >= hora_fin:
                    continue
                if ts.weekday() >= 5:
                    continue

            # Cooldown: no repetir senal del mismo activo en menos de N velas
            if n_senales > 0 and (i - last_signal_idx) < COOLDOWN_CANDLES:
                continue

            # Valores actuales
            curr_close = float(df_calc['Close'].iloc[i])
            curr_open = float(df_calc['Open'].iloc[i])
            curr_low = float(df_calc['Low'].iloc[i])
            curr_high = float(df_calc['High'].iloc[i])
            curr_rsi = float(rsi.iloc[i])
            prev_rsi = float(rsi.iloc[i - 1])
            curr_bbl = float(bbl.iloc[i])
            curr_bbu = float(bbu.iloc[i])
            bbm = (curr_bbl + curr_bbu) / 2  # BB media
            curr_ema = float(ema.iloc[i])
            curr_adx = float(adx.iloc[i])
            curr_atr = float(atr.iloc[i])

            # Saltar si hay NaN
            if any(np.isnan(v) for v in [curr_rsi, prev_rsi, curr_bbl, curr_bbu,
                                          curr_ema, curr_adx, curr_atr]):
                continue

            if curr_atr <= 0:
                continue

            # Per-pair thresholds from config
            _rsi_buy = cfg.get("rsi_buy", RSI_BUY_ZONE)
            _rsi_sell = cfg.get("rsi_sell", RSI_SELL_ZONE)
            _adx_min = cfg.get("adx_min", ADX_MIN)
            _adx_max = cfg.get("adx_max", ADX_MAX)
            _estrategia = cfg.get("estrategia", "bb_rsi")

            # -- Condiciones de entrada --
            tipo = None
            variante = ""

            if _estrategia == "fibonacci":
                # Fibonacci Mean Reversion
                fib_lookback = min(240, i)
                _fh = df_calc['High'].iloc[max(0, i-fib_lookback):i+1].max()
                _fl = df_calc['Low'].iloc[max(0, i-fib_lookback):i+1].min()
                _fr = _fh - _fl
                _fib_pos = (curr_close - _fl) / _fr if _fr > 0 else 0.5
                _fib_buy_t = cfg.get("fib_buy", 0.35)
                _fib_sell_t = cfg.get("fib_sell", 0.65)

                if _fib_pos < _fib_buy_t and curr_rsi < _rsi_buy and _adx_min <= curr_adx <= _adx_max:
                    tipo = "BUY"
                    variante = "FIB"
                elif _fib_pos > _fib_sell_t and curr_rsi > _rsi_sell and _adx_min <= curr_adx <= _adx_max:
                    tipo = "SELL"
                    variante = "FIB"
            else:
                # BB+RSI Mean Reversion (3 variants)
                # Variante A: BB touch + RSI extreme
                if (curr_low <= curr_bbl and curr_rsi < _rsi_buy and _adx_min <= curr_adx <= _adx_max):
                    tipo = "BUY"; variante = "A"
                elif (curr_high >= curr_bbu and curr_rsi > _rsi_sell and _adx_min <= curr_adx <= _adx_max):
                    tipo = "SELL"; variante = "A"
                # Variante B: BB rejection (long wick)
                elif (curr_low < curr_bbl and curr_close > curr_bbl
                      and (curr_close - curr_low) > (curr_high - curr_close) * 1.5
                      and curr_rsi < (_rsi_buy + 8) and _adx_min <= curr_adx <= _adx_max):
                    tipo = "BUY"; variante = "B"
                elif (curr_high > curr_bbu and curr_close < curr_bbu
                      and (curr_high - curr_close) > (curr_close - curr_low) * 1.5
                      and curr_rsi > (_rsi_sell - 8) and _adx_min <= curr_adx <= _adx_max):
                    tipo = "SELL"; variante = "B"
                # Variante C: RSI extremo
                elif (curr_rsi < 30 and curr_close < (curr_bbl + (bbm - curr_bbl) * 0.3)
                      and _adx_min <= curr_adx <= _adx_max):
                    tipo = "BUY"; variante = "C"
                elif (curr_rsi > 70 and curr_close > (curr_bbu - (curr_bbu - bbm) * 0.3)
                      and _adx_min <= curr_adx <= _adx_max):
                    tipo = "SELL"; variante = "C"

            # Block filters from PAR_PROFILES
            if tipo == "BUY" and cfg.get("block_buy"):
                tipo = None
            if tipo == "SELL" and cfg.get("block_sell"):
                tipo = None

            if tipo is None:
                continue

            # -- Calcular SL y TP (per-pair from profile) --
            sl_dist = curr_atr * cfg.get("sl_atr", SL_ATR_MULT)
            tp_dist = curr_atr * cfg.get("tp_atr", TP_ATR_MULT)

            if tipo == "BUY":
                sl_price = curr_close - sl_dist
                tp_price = curr_close + tp_dist
            else:
                sl_price = curr_close + sl_dist
                tp_price = curr_close - tp_dist

            # -- Simular resultado: recorrer velas futuras --
            resultado = "TIMEOUT"
            precio_cierre = curr_close
            velas_cierre = 0

            max_velas = min(TIMEOUT_CANDLES, len(df_calc) - i - 1)

            for j in range(1, max_velas + 1):
                idx_f = i + j
                high_f = float(df_calc['High'].iloc[idx_f])
                low_f = float(df_calc['Low'].iloc[idx_f])

                if tipo == "BUY":
                    # SL primero (peor caso)
                    if low_f <= sl_price:
                        resultado = "SL"
                        precio_cierre = sl_price
                        velas_cierre = j
                        break
                    # TP
                    if high_f >= tp_price:
                        resultado = "TP"
                        precio_cierre = tp_price
                        velas_cierre = j
                        break
                else:  # SELL
                    if high_f >= sl_price:
                        resultado = "SL"
                        precio_cierre = sl_price
                        velas_cierre = j
                        break
                    if low_f <= tp_price:
                        resultado = "TP"
                        precio_cierre = tp_price
                        velas_cierre = j
                        break

            # Si timeout, cerrar al close de la ultima vela
            if resultado == "TIMEOUT":
                timeout_idx = min(i + TIMEOUT_CANDLES, len(df_calc) - 1)
                precio_cierre = float(df_calc['Close'].iloc[timeout_idx])
                velas_cierre = TIMEOUT_CANDLES

            # Calcular pips
            if tipo == "BUY":
                pips = (precio_cierre - curr_close) * pip_mult
            else:
                pips = (curr_close - precio_cierre) * pip_mult

            duracion_min = velas_cierre * 5

            senal = {
                "fecha": ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, 'strftime') else str(ts),
                "activo": nombre,
                "tipo": tipo,
                "entrada": round(curr_close, 5),
                "sl": round(sl_price, 5),
                "tp": round(tp_price, 5),
                "atr": round(curr_atr, 5),
                "rsi": round(curr_rsi, 1),
                "rsi_prev": round(prev_rsi, 1),
                "adx": round(curr_adx, 1),
                "variante": variante,
                "resultado": resultado,
                "pips": round(pips, 1),
                "duracion_min": duracion_min,
            }
            todas_senales.append(senal)

            # Estadisticas
            s = stats_activo[nombre]
            s["total"] += 1
            s["pips"] += pips
            if tipo == "BUY":
                s["buy"] += 1
                s["buy_pips"] += pips
            else:
                s["sell"] += 1
                s["sell_pips"] += pips

            if resultado == "TP":
                s["wins"] += 1
                if tipo == "BUY":
                    s["buy_wins"] += 1
                else:
                    s["sell_wins"] += 1
            elif resultado == "SL":
                s["losses"] += 1
            else:
                s["timeout"] += 1

            n_senales += 1
            last_signal_idx = i  # Cooldown

        print(f"  {nombre:10s} -- {n_senales} senales encontradas")

    # -- RESULTADOS --
    print()
    print("=" * 60)
    print("  RESULTADOS SCALPER BACKTEST")
    print("  BB(20,2) + RSI(7) Mean Reversion | M5")
    print("=" * 60)
    print()

    total = len(todas_senales)
    if total == 0:
        print("No se generaron senales en el periodo.")
        return

    wins = sum(1 for s in todas_senales if s["resultado"] == "TP")
    losses = sum(1 for s in todas_senales if s["resultado"] == "SL")
    timeouts = sum(1 for s in todas_senales if s["resultado"] == "TIMEOUT")
    total_pips = sum(s["pips"] for s in todas_senales)
    wr = wins / total * 100

    print(f"  Total senales:  {total}")
    print(f"  Wins:           {wins} ({wr:.1f}%)")
    print(f"  Losses:         {losses} ({losses/total*100:.1f}%)")
    print(f"  Timeout:        {timeouts} ({timeouts/total*100:.1f}%)")
    print(f"  Ganancia total: {total_pips:+.1f} (pts+pips combinados)")
    print(f"  Promedio:       {total_pips/total:+.1f} por senal")
    print()

    # Por activo
    print(f"  {'Activo':10s} {'Senal':>6s} {'Win%':>6s} {'Wins':>5s} {'Loss':>5s} {'T/O':>5s} {'Ganancia':>10s} {'Por Op':>8s} {'Unidad':>6s}")
    print(f"  {'-'*10} {'-'*6} {'-'*6} {'-'*5} {'-'*5} {'-'*5} {'-'*10} {'-'*8} {'-'*6}")
    for nombre in ACTIVOS.keys():
        s = stats_activo[nombre]
        unit = ACTIVOS[nombre]["pip_name"]
        if s["total"] == 0:
            print(f"  {nombre:10s} {'0':>6s} {'--':>6s} {'--':>5s} {'--':>5s} {'--':>5s} {'--':>10s} {'--':>8s} {unit:>6s}")
            continue
        w = s["wins"] / s["total"] * 100
        pp = s["pips"] / s["total"]
        print(f"  {nombre:10s} {s['total']:6d} {w:5.1f}% {s['wins']:5d} {s['losses']:5d} {s['timeout']:5d} {s['pips']:+10.1f} {pp:+8.1f} {unit:>6s}")

    # Por direccion y activo
    print()
    print("  Por direccion (desglosado por activo):")
    print()
    for nombre in ACTIVOS.keys():
        s = stats_activo[nombre]
        unit = ACTIVOS[nombre]["pip_name"]
        if s["total"] == 0:
            continue
        bwr = (s["buy_wins"] / s["buy"] * 100) if s["buy"] > 0 else 0
        swr = (s["sell_wins"] / s["sell"] * 100) if s["sell"] > 0 else 0
        buy_pp = s["buy_pips"] / s["buy"] if s["buy"] > 0 else 0
        sell_pp = s["sell_pips"] / s["sell"] if s["sell"] > 0 else 0
        print(f"  {nombre:10s}  BUY: {s['buy']:3d} senales, {bwr:5.1f}% WR, {s['buy_pips']:+9.1f} {unit} ({buy_pp:+.1f}/{unit[0]})")
        print(f"  {'':10s} SELL: {s['sell']:3d} senales, {swr:5.1f}% WR, {s['sell_pips']:+9.1f} {unit} ({sell_pp:+.1f}/{unit[0]})")
        print()

    # -- Guardar CSV --
    csv_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_scalper_results.csv")
    df_results = pd.DataFrame(todas_senales)
    df_results.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"\n  CSV guardado: {csv_file}")

    # -- Top 5 mejores y peores senales --
    print()
    print("  Top 5 mejores senales:")
    sorted_best = sorted(todas_senales, key=lambda x: x["pips"], reverse=True)[:5]
    for s in sorted_best:
        unit = ACTIVOS[s['activo']]["pip_name"]
        print(f"    {s['fecha']} {s['activo']:10s} {s['tipo']:4s} {s['pips']:+.1f} {unit} (RSI:{s['rsi_prev']:.0f}->{s['rsi']:.0f}, ADX:{s['adx']:.0f})")

    print()
    print("  Top 5 peores senales:")
    sorted_worst = sorted(todas_senales, key=lambda x: x["pips"])[:5]
    for s in sorted_worst:
        unit = ACTIVOS[s['activo']]["pip_name"]
        print(f"    {s['fecha']} {s['activo']:10s} {s['tipo']:4s} {s['pips']:+.1f} {unit} (RSI:{s['rsi_prev']:.0f}->{s['rsi']:.0f}, ADX:{s['adx']:.0f})")

    print()
    print(f"  Backtest completado. {total} senales analizadas.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        run_backtest()
    except KeyboardInterrupt:
        print("\nBacktest cancelado por el usuario.")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()

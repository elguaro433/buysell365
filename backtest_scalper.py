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

ACTIVOS = {
    "GOLD":    {"ticker": "GC=F",     "hora_ini": 7,  "hora_fin": 17, "pip_mult": 1,     "pip_name": "pts"},
    "EUR/USD": {"ticker": "EURUSD=X", "hora_ini": 7,  "hora_fin": 16, "pip_mult": 10000, "pip_name": "pips"},
    "GBP/USD": {"ticker": "GBPUSD=X", "hora_ini": 7,  "hora_fin": 16, "pip_mult": 10000, "pip_name": "pips"},
    "NASDAQ":  {"ticker": "NQ=F",     "hora_ini": 13, "hora_fin": 20, "pip_mult": 1,     "pip_name": "pts"},
}

# Parametros de la estrategia
BB_PERIOD = 20
BB_STD = 2.0
RSI_PERIOD = 7
EMA_PERIOD = 50
ADX_PERIOD = 10
ADX_MIN = 12
ADX_MAX = 35
ATR_PERIOD = 14
SL_ATR_MULT = 1.5
TP_ATR_MULT = 2.0
TIMEOUT_CANDLES = 12  # 60 min / 5 min = 12 velas

# RSI thresholds (relajados para mas senales)
RSI_BUY_PREV = 30   # RSI <= 30 en vela anterior
RSI_BUY_CURR = 32   # RSI > 32 en vela actual
RSI_SELL_PREV = 70   # RSI >= 70 en vela anterior
RSI_SELL_CURR = 68   # RSI < 68 en vela actual


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

            # Valores actuales y anteriores
            curr_close = float(df_calc['Close'].iloc[i])
            curr_low = float(df_calc['Low'].iloc[i])
            curr_high = float(df_calc['High'].iloc[i])
            curr_rsi = float(rsi.iloc[i])
            prev_rsi = float(rsi.iloc[i - 1])
            curr_bbl = float(bbl.iloc[i])
            curr_bbu = float(bbu.iloc[i])
            curr_ema = float(ema.iloc[i])
            curr_adx = float(adx.iloc[i])
            curr_atr = float(atr.iloc[i])

            # Saltar si hay NaN
            if any(np.isnan(v) for v in [curr_rsi, prev_rsi, curr_bbl, curr_bbu,
                                          curr_ema, curr_adx, curr_atr]):
                continue

            if curr_atr <= 0:
                continue

            # -- Condiciones de entrada --
            tipo = None

            # Margen EMA: permitir precio cercano al EMA (0.3%)
            ema_margin = curr_ema * 0.003

            # BUY: Low toca/perfora BB inferior + RSI rebota + precio >= EMA50-margen + ADX
            if (curr_low <= curr_bbl and
                    prev_rsi <= RSI_BUY_PREV and
                    curr_rsi > RSI_BUY_CURR and
                    curr_close >= (curr_ema - ema_margin) and
                    ADX_MIN <= curr_adx <= ADX_MAX):
                tipo = "BUY"

            # SELL: High toca/perfora BB superior + RSI rebota + precio <= EMA50+margen + ADX
            elif (curr_high >= curr_bbu and
                  prev_rsi >= RSI_SELL_PREV and
                  curr_rsi < RSI_SELL_CURR and
                  curr_close <= (curr_ema + ema_margin) and
                  ADX_MIN <= curr_adx <= ADX_MAX):
                tipo = "SELL"

            if tipo is None:
                continue

            # -- Calcular SL y TP --
            sl_dist = curr_atr * SL_ATR_MULT
            tp_dist = curr_atr * TP_ATR_MULT

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
    print(f"  Pips totales:   {total_pips:+.1f}")
    print(f"  Pips promedio:  {total_pips/total:+.1f} por senal")
    print()

    # Por activo
    print(f"  {'Activo':10s} {'Senal':>6s} {'Win%':>6s} {'Wins':>5s} {'Loss':>5s} {'T/O':>5s} {'Pips':>10s} {'Pips/Op':>8s}")
    print(f"  {'-'*10} {'-'*6} {'-'*6} {'-'*5} {'-'*5} {'-'*5} {'-'*10} {'-'*8}")
    for nombre in ACTIVOS.keys():
        s = stats_activo[nombre]
        if s["total"] == 0:
            print(f"  {nombre:10s} {'0':>6s} {'--':>6s} {'--':>5s} {'--':>5s} {'--':>5s} {'--':>10s} {'--':>8s}")
            continue
        w = s["wins"] / s["total"] * 100
        pp = s["pips"] / s["total"]
        print(f"  {nombre:10s} {s['total']:6d} {w:5.1f}% {s['wins']:5d} {s['losses']:5d} {s['timeout']:5d} {s['pips']:+10.1f} {pp:+8.1f}")

    # Por direccion
    print()
    print("  Por direccion:")
    total_buy = sum(s["buy"] for s in stats_activo.values())
    total_sell = sum(s["sell"] for s in stats_activo.values())
    total_buy_wins = sum(s["buy_wins"] for s in stats_activo.values())
    total_sell_wins = sum(s["sell_wins"] for s in stats_activo.values())
    total_buy_pips = sum(s["buy_pips"] for s in stats_activo.values())
    total_sell_pips = sum(s["sell_pips"] for s in stats_activo.values())

    if total_buy > 0:
        buy_wr = total_buy_wins / total_buy * 100
        print(f"  BUY  -- {total_buy:3d} senales, {buy_wr:5.1f}% WR, {total_buy_pips:+.1f} pips ({total_buy_pips/total_buy:+.1f}/op)")
    else:
        print(f"  BUY  -- 0 senales")

    if total_sell > 0:
        sell_wr = total_sell_wins / total_sell * 100
        print(f"  SELL -- {total_sell:3d} senales, {sell_wr:5.1f}% WR, {total_sell_pips:+.1f} pips ({total_sell_pips/total_sell:+.1f}/op)")
    else:
        print(f"  SELL -- 0 senales")

    # Por activo y direccion detallado
    print()
    print("  Detalle por activo y direccion:")
    print(f"  {'Activo':10s} {'BUY':>5s} {'BuyWR':>6s} {'BuyPips':>9s} {'SELL':>5s} {'SellWR':>7s} {'SellPips':>9s}")
    print(f"  {'-'*10} {'-'*5} {'-'*6} {'-'*9} {'-'*5} {'-'*7} {'-'*9}")
    for nombre in ACTIVOS.keys():
        s = stats_activo[nombre]
        if s["total"] == 0:
            continue
        bwr = (s["buy_wins"] / s["buy"] * 100) if s["buy"] > 0 else 0
        swr = (s["sell_wins"] / s["sell"] * 100) if s["sell"] > 0 else 0
        print(f"  {nombre:10s} {s['buy']:5d} {bwr:5.1f}% {s['buy_pips']:+9.1f} {s['sell']:5d} {swr:6.1f}% {s['sell_pips']:+9.1f}")

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
        print(f"    {s['fecha']} {s['activo']:10s} {s['tipo']:4s} {s['pips']:+.1f} pips (RSI:{s['rsi_prev']:.0f}->{s['rsi']:.0f}, ADX:{s['adx']:.0f})")

    print()
    print("  Top 5 peores senales:")
    sorted_worst = sorted(todas_senales, key=lambda x: x["pips"])[:5]
    for s in sorted_worst:
        print(f"    {s['fecha']} {s['activo']:10s} {s['tipo']:4s} {s['pips']:+.1f} pips (RSI:{s['rsi_prev']:.0f}->{s['rsi']:.0f}, ADX:{s['adx']:.0f})")

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

#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║  BuySell365 Pro — BACKTESTER v1.0                          ║
║  Simula señales históricas con las mismas estrategias      ║
║  que usa el bot en tiempo real.                             ║
║                                                             ║
║  Uso: python backtest.py                                    ║
║  Resultado: backtest_results.html + backtest_results.csv    ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import sys
import io
import json
import time
import threading
import warnings
from datetime import datetime, timedelta
from collections import defaultdict

# Fix Windows console encoding for emojis
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import pandas as pd
import numpy as np
import yfinance as yf

warnings.filterwarnings("ignore")

# ── Configuración del backtest ──────────────────────────────
PERIODO_DATOS = "60d"       # Máximo para 15m en yfinance (límite duro de Yahoo Finance)
INTERVALO = "15m"
ACTIVOS = {
    "ORO":      "GC=F",
    "EUR/USD":  "EURUSD=X",
    "USD/JPY":  "USDJPY=X",
    "GBP/JPY":  "GBPJPY=X",
    "NASDAQ":   "NQ=F",
    "S&P 500":  "ES=F",
}

# Horario de trading (08:00-18:00 Andorra = UTC+1 invierno, UTC+2 verano)
HORA_INICIO = 7   # UTC (conservador para cubrir ambos)
HORA_FIN = 17     # UTC

# ── Importar funciones del bot ──────────────────────────────
# Necesitamos inicializar algunos globals antes de importar
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)

# Globals que el bot necesita
os.environ.setdefault("BACKTEST_MODE", "1")

print("=" * 60)
print("  BuySell365 Pro — BACKTESTER v1.0")
print("=" * 60)
print()


def run_backtest():
    """Ejecuta el backtest completo."""

    # ── Extraer funciones del bot sin importar todo el módulo ─
    print("📦 Cargando funciones del bot (modo backtest)...")

    try:
        bot_path = os.path.join(BOT_DIR, "bot.py")
        with open(bot_path, "r", encoding="utf-8") as f:
            bot_source = f.read()

        # Crear un namespace aislado con las dependencias necesarias
        import pandas_ta as ta
        import pytz
        import copy

        ns = {
            "pd": pd, "np": np, "ta": ta, "yf": yf,
            "os": os, "sys": sys, "json": json, "time": time,
            "threading": threading, "datetime": datetime, "timedelta": timedelta,
            "copy": copy, "re": __import__("re"), "math": __import__("math"),
            "pytz": pytz, "warnings": warnings, "random": __import__("random"),
            # Globals que las funciones necesitan
            "MODO_RIESGO": "normal",
            "_cache_mtf_1h": {},
            "_cache_ml_modelos": {},
            "_lock_ops": threading.RLock(),
            "_lock_mt5": threading.RLock(),
            "historial_operaciones": [],
            "operaciones_activas": {},
            "logger": __import__("logging").getLogger("backtest"),
            "MT5_AVAILABLE": False,
            "mt5": None,
        }

        # Extraer las funciones que necesitamos (y sus helpers)
        # Buscar las funciones por línea en el source
        import textwrap

        # Ejecutar solo la parte de definiciones (funciones puras, no threads ni main)
        # Extraemos líneas que definen funciones, constantes, y clases necesarias
        lines = bot_source.split('\n')

        # Recopilar secciones seguras: imports y funciones puras (no arranque de threads)
        safe_sections = []
        i = 0
        skip_until_unindent = False
        dangerous_patterns = [
            "def main(", "def iniciar_bot(", "def loop_escaneo(",
            "def loop_monitor(", "def loop_polling(", "def loop_health(",
            "def loop_vip(", "def _watchdog(", "def _start_web_sync(",
            "Thread(", ".start()", "mt5.initialize", "mt5.login",
            "requests.post(", "requests.get(", "enviar_telegram(",
            "if __name__", "iniciar_bot()",
        ]

        # Approach: extract specific function blocks we need
        needed_funcs = [
            "def get_categoria(", "def get_col(", "def detectar_patrones_velas(",
            "def calcular_pivot_points_df(", "def _calcular_features_ml(",
            "def predecir_direccion_ml(",
            "def _calcular_rango_asiatico(",
            "def calcular_indicadores_profesionales(", "def evaluar_senal_profesional(",
            "def calcular_niveles_3tp(",
        ]

        # Also need constants
        needed_constants = [
            "MODO_RIESGO", "MIN_SCORE", "MAX_TRADES", "RIESGO_",
            "MIN_SL", "FERIADOS",
        ]

        def extract_function(source_lines, func_sig):
            """Extract a complete function from source lines."""
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
                    if current_indent <= base_indent and stripped and not stripped.startswith("#"):
                        break
                    func_lines.append(ln)
            return '\n'.join(func_lines) if func_lines else ""

        # Extract all needed functions
        extracted = []
        for func_sig in needed_funcs:
            code = extract_function(lines, func_sig)
            if code:
                extracted.append(code)

        # Add stubs for functions referenced but not extracted
        stubs = [
            "def log_sistema(msg, level='info'): pass",
            "def log_op(msg): pass",
            "def log_senal(msg): pass",
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
            "MODO_RIESGO = 'normal'",
            "MIN_SCORE = 3",
        ]

        full_code = '\n'.join(stubs) + '\n\n' + '\n\n'.join(extracted)

        # Execute in isolated namespace
        exec(full_code, ns)

        calcular_indicadores = ns.get("calcular_indicadores_profesionales")
        evaluar_senal = ns.get("evaluar_senal_profesional")
        calcular_niveles = ns.get("calcular_niveles_3tp")

        if calcular_indicadores and evaluar_senal and calcular_niveles:
            print("   ✅ Funciones extraídas correctamente (modo aislado)")
        else:
            raise RuntimeError("No se pudieron extraer todas las funciones")

    except Exception as e:
        print(f"   ❌ Error cargando funciones: {e}")
        import traceback
        traceback.print_exc()
        calcular_indicadores = None
        calcular_niveles = None
        evaluar_senal = None

    if not calcular_indicadores:
        print("\n❌ No se pudieron cargar las funciones del bot.")
        return

    # ── Descargar datos ─────────────────────────────────────
    print(f"\n📊 Descargando datos históricos ({PERIODO_DATOS}, {INTERVALO})...")
    print(f"   (Yahoo Finance limita 15m a 60 días máximo)")
    datos = {}
    for nombre, ticker in ACTIVOS.items():
        try:
            df = yf.download(ticker, period=PERIODO_DATOS, interval=INTERVALO,
                           progress=False, auto_adjust=True)
            if hasattr(df.columns, 'levels') and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
            df = df.dropna()
            if len(df) >= 250:
                datos[nombre] = {"ticker": ticker, "df": df}
                fecha_inicio = df.index[0].strftime("%Y-%m-%d")
                fecha_fin = df.index[-1].strftime("%Y-%m-%d")
                print(f"   ✅ {nombre:10s} — {len(df):,} velas ({fecha_inicio} → {fecha_fin})")
            else:
                print(f"   ⚠️ {nombre:10s} — Solo {len(df)} velas (mín 250)")
        except Exception as e:
            print(f"   ❌ {nombre:10s} — Error: {e}")

    if not datos:
        print("\n❌ No se pudieron descargar datos.")
        return

    # ── Ejecutar simulación ─────────────────────────────────
    print(f"\n🔄 Simulando señales en datos históricos...")
    print(f"   Ventana mínima: 200 velas | Paso: 12 velas (~3 horas)")
    print()

    todas_senales = []
    stats_por_activo = defaultdict(lambda: {"total": 0, "tp1": 0, "tp2": 0, "tp3": 0, "sl": 0,
                                              "timeout": 0, "pips_total": 0.0})

    for nombre, info in datos.items():
        ticker = info["ticker"]
        df_full = info["df"]
        n_senales = 0

        # Simular vela por vela desde la 200 en adelante
        # Paso de 4 velas de 15m = 1 hora (más oportunidades)
        paso = 4  # Cada 4 velas de 15m = 1 hora
        total_pasos = (len(df_full) - 200) // paso

        for i in range(200, len(df_full) - 20, paso):  # -20 para tener datos de resultado
            # Verificar horario
            ts = df_full.index[i]
            if hasattr(ts, 'hour'):
                if ts.hour < HORA_INICIO or ts.hour >= HORA_FIN:
                    continue
                if ts.weekday() >= 5:  # Fin de semana
                    continue

            # Tomar ventana de datos hasta este punto
            df_slice = df_full.iloc[:i+1].copy()
            precio = float(df_slice['Close'].iloc[-1])

            try:
                ind = calcular_indicadores(df_slice, precio, ticker)
            except Exception:
                continue

            if not ind:
                continue

            try:
                tipo, score, razones = evaluar_senal(ind, ticker)
            except Exception:
                continue

            if tipo is None or score < 4:
                continue

            # Detectar estrategia de las razones
            estrategia = "desconocida"
            for r in razones:
                if "Pullback" in r:
                    estrategia = "pullback"
                    break
                elif "Asian Range" in r:
                    estrategia = "asian_breakout"
                    break
                elif "Breakout" in r:
                    estrategia = "breakout"
                    break
                elif "Reversi" in r or "Divergencia" in r:
                    estrategia = "reversion"
                    break

            # ¡Señal detectada! Calcular niveles con estrategia
            try:
                niveles = calcular_niveles(precio, tipo, ind.get('atr_1h', ind.get('atr', 0)), ticker, estrategia=estrategia)
            except Exception:
                continue

            sl = niveles['sl']
            tp1 = niveles['tp1']
            tp2 = niveles['tp2']
            tp3 = niveles['tp3']

            # ── Simular resultado: recorrer velas futuras ───
            resultado = "TIMEOUT"
            precio_cierre = precio
            velas_hasta_cierre = 0
            max_favorable = 0
            max_adverso = 0

            # Máximo 96 velas futuras de 15m (24 horas)
            max_velas = min(96, len(df_full) - i - 1)

            for j in range(1, max_velas + 1):
                idx_futuro = i + j
                if idx_futuro >= len(df_full):
                    break

                high_j = float(df_full['High'].iloc[idx_futuro])
                low_j = float(df_full['Low'].iloc[idx_futuro])
                close_j = float(df_full['Close'].iloc[idx_futuro])

                if tipo == "COMPRA":
                    favorable = high_j - precio
                    adverso = precio - low_j
                    max_favorable = max(max_favorable, favorable)
                    max_adverso = max(max_adverso, adverso)

                    # Verificar SL primero (peor caso)
                    if low_j <= sl:
                        resultado = "SL"
                        precio_cierre = sl
                        velas_hasta_cierre = j
                        break
                    # TP1 primero (conservador: nearest target first)
                    if high_j >= tp1:
                        resultado = "TP1"
                        precio_cierre = tp1
                        velas_hasta_cierre = j
                        break
                    # TP2
                    if high_j >= tp2:
                        resultado = "TP2"
                        precio_cierre = tp2
                        velas_hasta_cierre = j
                        break
                    # TP3
                    if high_j >= tp3:
                        resultado = "TP3"
                        precio_cierre = tp3
                        velas_hasta_cierre = j
                        break
                else:  # VENTA
                    favorable = precio - low_j
                    adverso = high_j - precio
                    max_favorable = max(max_favorable, favorable)
                    max_adverso = max(max_adverso, adverso)

                    # SL primero
                    if high_j >= sl:
                        resultado = "SL"
                        precio_cierre = sl
                        velas_hasta_cierre = j
                        break
                    # TP1 primero (conservador)
                    if low_j <= tp1:
                        resultado = "TP1"
                        precio_cierre = tp1
                        velas_hasta_cierre = j
                        break
                    # TP2
                    if low_j <= tp2:
                        resultado = "TP2"
                        precio_cierre = tp2
                        velas_hasta_cierre = j
                        break
                    # TP3
                    if low_j <= tp3:
                        resultado = "TP3"
                        precio_cierre = tp3
                        velas_hasta_cierre = j
                        break

            if velas_hasta_cierre == 0:
                velas_hasta_cierre = max_velas
                # Cerrar al precio actual si timeout
                if i + max_velas < len(df_full):
                    precio_cierre = float(df_full['Close'].iloc[i + max_velas])

            # Calcular pips/puntos
            if "JPY" in ticker:
                pip_mult = 100  # 1 pip = 0.01
                unit_name = "pips"
            elif "=X" in ticker:
                pip_mult = 10000  # 1 pip = 0.0001
                unit_name = "pips"
            elif ticker == "GC=F":
                pip_mult = 1  # $1 = 1 punto
                unit_name = "pts"
            else:
                pip_mult = 1  # Puntos para índices
                unit_name = "pts"

            if tipo == "COMPRA":
                pips = (precio_cierre - precio) * pip_mult
            else:
                pips = (precio - precio_cierre) * pip_mult

            duracion_min = velas_hasta_cierre * 15  # Velas de 15m

            # Nombre bonito de estrategia para el reporte
            estrategia_display = {"pullback": "Pullback", "breakout": "Breakout", "reversion": "Reversión", "asian_breakout": "Asian Breakout"}.get(estrategia, "Desconocida")

            # Determinar nivel: Premium vs Standard
            nivel = "PREMIUM" if (score >= 5 or estrategia in ("breakout", "asian_breakout")) else "STANDARD"

            senal = {
                "fecha": ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, 'strftime') else str(ts),
                "activo": nombre,
                "ticker": ticker,
                "tipo": tipo,
                "score": score,
                "estrategia": estrategia_display,
                "nivel": nivel,
                "entrada": round(precio, 5),
                "sl": round(sl, 5),
                "tp1": round(tp1, 5),
                "tp2": round(tp2, 5),
                "tp3": round(tp3, 5),
                "resultado": resultado,
                "pips": round(pips, 1),
                "unit": unit_name,
                "duracion_min": duracion_min,
                "max_favorable": round(max_favorable * pip_mult, 1),
                "max_adverso": round(max_adverso * pip_mult, 1),
            }
            todas_senales.append(senal)

            # Estadísticas
            s = stats_por_activo[nombre]
            s["total"] += 1
            s["pips_total"] += pips
            if resultado == "TP1": s["tp1"] += 1
            elif resultado == "TP2": s["tp2"] += 1
            elif resultado == "TP3": s["tp3"] += 1
            elif resultado == "SL": s["sl"] += 1
            else: s["timeout"] += 1

            n_senales += 1

        print(f"   {nombre:10s} — {n_senales} señales encontradas")

    # ── Resultados ──────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"  RESULTADOS DEL BACKTEST")
    print(f"{'=' * 60}\n")

    total_senales = len(todas_senales)
    if total_senales == 0:
        print("❌ No se generaron señales en el período.")
        return

    total_wins = sum(1 for s in todas_senales if s["resultado"] in ("TP1", "TP2", "TP3"))
    total_losses = sum(1 for s in todas_senales if s["resultado"] == "SL")
    total_timeout = sum(1 for s in todas_senales if s["resultado"] == "TIMEOUT")
    total_pips = sum(s["pips"] for s in todas_senales)
    win_rate = (total_wins / total_senales * 100) if total_senales > 0 else 0

    print(f"  📊 Total señales: {total_senales}")
    print(f"  ✅ Wins (TP):     {total_wins} ({win_rate:.1f}%)")
    print(f"  ❌ Losses (SL):   {total_losses} ({total_losses/total_senales*100:.1f}%)")
    print(f"  ⏰ Timeout:       {total_timeout}")
    print(f"  💰 Ganancia total: {total_pips:+.1f} (pts+pips combinados)")
    print(f"  📈 Promedio:       {total_pips/total_senales:+.1f} por señal")
    print()

    # Mapa de unidades por activo
    _unit_map = {}
    for nombre, ticker in ACTIVOS.items():
        if "JPY" in ticker or ("=X" in ticker and "JPY" not in ticker):
            _unit_map[nombre] = "pips"
        else:
            _unit_map[nombre] = "pts"

    # Por activo
    print(f"  {'Activo':12s} {'Total':>6s} {'Win%':>6s} {'TP1':>5s} {'TP2':>5s} {'TP3':>5s} {'SL':>5s} {'Ganancia':>10s} {'Unidad':>6s}")
    print(f"  {'-'*12} {'-'*6} {'-'*6} {'-'*5} {'-'*5} {'-'*5} {'-'*5} {'-'*10} {'-'*6}")
    for nombre in ACTIVOS.keys():
        s = stats_por_activo[nombre]
        if s["total"] == 0:
            continue
        wins = s["tp1"] + s["tp2"] + s["tp3"]
        wr = wins / s["total"] * 100
        unit = _unit_map.get(nombre, "pts")
        print(f"  {nombre:12s} {s['total']:6d} {wr:5.1f}% {s['tp1']:5d} {s['tp2']:5d} {s['tp3']:5d} {s['sl']:5d} {s['pips_total']:+10.1f} {unit:>6s}")

    # Por estrategia
    print()
    stats_estrat = defaultdict(lambda: {"total": 0, "wins": 0, "pips": 0.0})
    for s in todas_senales:
        e = stats_estrat[s["estrategia"]]
        e["total"] += 1
        if s["resultado"] in ("TP1", "TP2", "TP3"):
            e["wins"] += 1
        e["pips"] += s["pips"]

    print(f"  {'Estrategia':15s} {'Total':>6s} {'Win%':>6s} {'Ganancia':>10s} {'Por Op':>8s}")
    print(f"  {'-'*15} {'-'*6} {'-'*6} {'-'*10} {'-'*8}")
    for est, e in sorted(stats_estrat.items()):
        wr = e["wins"] / e["total"] * 100 if e["total"] > 0 else 0
        pp = e["pips"] / e["total"] if e["total"] > 0 else 0
        print(f"  {est:15s} {e['total']:6d} {wr:5.1f}% {e['pips']:+10.1f} {pp:+8.1f}")

    # Por nivel (Premium vs Standard)
    print()
    stats_nivel = defaultdict(lambda: {"total": 0, "wins": 0, "pips": 0.0})
    for s in todas_senales:
        n = stats_nivel[s["nivel"]]
        n["total"] += 1
        if s["resultado"] in ("TP1", "TP2", "TP3"):
            n["wins"] += 1
        n["pips"] += s["pips"]

    print(f"  {'Nivel':15s} {'Total':>6s} {'Win%':>6s} {'Ganancia':>10s} {'Por Op':>8s}")
    print(f"  {'-'*15} {'-'*6} {'-'*6} {'-'*10} {'-'*8}")
    for niv in ["PREMIUM", "STANDARD"]:
        n = stats_nivel.get(niv, {"total": 0, "wins": 0, "pips": 0.0})
        if n["total"] == 0:
            continue
        wr = n["wins"] / n["total"] * 100
        pp = n["pips"] / n["total"]
        emoji = "⭐" if niv == "PREMIUM" else "📊"
        print(f"  {emoji} {niv:13s} {n['total']:6d} {wr:5.1f}% {n['pips']:+10.1f} {pp:+8.1f}")

    # ── Guardar CSV ─────────────────────────────────────────
    csv_file = os.path.join(BOT_DIR, "backtest_results.csv")
    df_results = pd.DataFrame(todas_senales)
    df_results.to_csv(csv_file, index=False, encoding="utf-8-sig")
    print(f"\n  📁 CSV guardado: {csv_file}")

    # ── Generar HTML con tabla visual ───────────────────────
    html_file = os.path.join(BOT_DIR, "backtest_results.html")
    _generate_html_report(todas_senales, stats_por_activo, stats_estrat,
                          total_senales, total_wins, total_losses, total_pips, win_rate,
                          html_file)
    print(f"  📁 HTML guardado: {html_file}")
    print(f"\n✅ Backtest completado.")


def _generate_html_report(senales, stats_activo, stats_estrat,
                          total, wins, losses, pips, win_rate, filepath):
    """Genera un reporte HTML visual."""

    # Tabla de señales
    rows_html = ""
    for s in senales:
        res = s["resultado"]
        if res in ("TP1", "TP2", "TP3"):
            color = "#00c853"
            icon = "✅"
        elif res == "SL":
            color = "#ff5252"
            icon = "❌"
        else:
            color = "#ffa726"
            icon = "⏰"

        tipo_color = "#00c853" if s["tipo"] == "COMPRA" else "#ff5252"
        pips_color = "#00c853" if s["pips"] >= 0 else "#ff5252"
        unit = s.get("unit", "pts")

        rows_html += f"""
        <tr>
            <td>{s['fecha']}</td>
            <td>{s['activo']}</td>
            <td style="color:{tipo_color};font-weight:bold">{s['tipo']}</td>
            <td>{s['estrategia']}</td>
            <td>{s['score']}</td>
            <td>{s['entrada']}</td>
            <td>{s['sl']}</td>
            <td>{s['tp1']}</td>
            <td style="color:{color};font-weight:bold">{icon} {res}</td>
            <td style="color:{pips_color};font-weight:bold">{s['pips']:+.1f} {unit}</td>
            <td>{s['duracion_min']}m</td>
            <td>{s['max_favorable']:+.1f} {unit}</td>
            <td>{s['max_adverso']:+.1f} {unit}</td>
        </tr>"""

    # Tabla por activo
    activo_rows = ""
    for nombre, s in stats_activo.items():
        if s["total"] == 0:
            continue
        w = s["tp1"] + s["tp2"] + s["tp3"]
        wr = w / s["total"] * 100
        color = "#00c853" if wr >= 50 else "#ff5252"
        activo_rows += f"""
        <tr>
            <td>{nombre}</td>
            <td>{s['total']}</td>
            <td style="color:{color};font-weight:bold">{wr:.1f}%</td>
            <td>{s['tp1']}</td><td>{s['tp2']}</td><td>{s['tp3']}</td><td>{s['sl']}</td>
            <td style="color:{'#00c853' if s['pips_total']>=0 else '#ff5252'}">{s['pips_total']:+.1f}</td>
        </tr>"""

    pips_color = "#00c853" if pips >= 0 else "#ff5252"

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>BuySell365 Pro — Backtest Results</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; background: #0d1117; color: #e6edf3; margin: 20px; }}
  h1 {{ color: #00d4aa; border-bottom: 2px solid #00d4aa; padding-bottom: 10px; }}
  h2 {{ color: #58a6ff; margin-top: 30px; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }}
  .stat-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 15px; text-align: center; }}
  .stat-card .value {{ font-size: 28px; font-weight: bold; }}
  .stat-card .label {{ color: #8b949e; font-size: 13px; margin-top: 5px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 15px 0; font-size: 13px; }}
  th {{ background: #161b22; color: #00d4aa; padding: 10px 8px; text-align: left; border-bottom: 2px solid #30363d; }}
  td {{ padding: 8px; border-bottom: 1px solid #21262d; }}
  tr:hover {{ background: #161b22; }}
  .green {{ color: #00c853; }} .red {{ color: #ff5252; }}
</style>
</head>
<body>
<h1>📊 BuySell365 Pro — Resultados Backtest</h1>
<p>Período: {PERIODO_DATOS} | Intervalo: {INTERVALO} | Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

<div class="stats-grid">
  <div class="stat-card"><div class="value">{total}</div><div class="label">Total Señales</div></div>
  <div class="stat-card"><div class="value green">{wins}</div><div class="label">Wins (TP)</div></div>
  <div class="stat-card"><div class="value red">{losses}</div><div class="label">Losses (SL)</div></div>
  <div class="stat-card"><div class="value" style="color:{'#00c853' if win_rate>=50 else '#ff5252'}">{win_rate:.1f}%</div><div class="label">Win Rate</div></div>
  <div class="stat-card"><div class="value" style="color:{pips_color}">{pips:+.1f}</div><div class="label">Ganancia Total (pts+pips)</div></div>
  <div class="stat-card"><div class="value" style="color:{pips_color}">{pips/total:+.1f}</div><div class="label">Promedio/Señal</div></div>
</div>

<h2>📈 Resultados por Activo</h2>
<table>
<tr><th>Activo</th><th>Total</th><th>Win Rate</th><th>TP1</th><th>TP2</th><th>TP3</th><th>SL</th><th>Ganancia</th></tr>
{activo_rows}
</table>

<h2>📋 Todas las Señales ({total})</h2>
<table>
<tr><th>Fecha</th><th>Activo</th><th>Tipo</th><th>Estrategia</th><th>Score</th><th>Entrada</th><th>SL</th><th>TP1</th><th>Resultado</th><th>Ganancia</th><th>Duración</th><th>Max Favor</th><th>Max Contra</th></tr>
{rows_html}
</table>

</body></html>"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    try:
        run_backtest()
    except KeyboardInterrupt:
        print("\n\n⏹️ Backtest cancelado por el usuario.")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

    input("\nPresiona Enter para salir...")

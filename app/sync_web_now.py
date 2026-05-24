"""
sync_web_now.py — FIX 2026-05-01: VERDAD SIEMPRE
==================================================

Fuerza un sync INMEDIATO a buysell365.pro con los stats REALES calculados
con stats_normalizer (x10 GOLD consistente, multi-TP grouping, exclusiones).

Uso:
    python -X utf8 sync_web_now.py

No requiere que el bot este corriendo. Lee directamente de:
  - copier_stats.json
  - copier_open_signals.json
  - historial_real.json
  - mt5_realtime.json

Y los empuja al endpoint /api/sync con SYNC_SECRET.
"""
import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

WEB_URL = os.getenv("WEB_URL", "https://buysell365.pro").rstrip("/")
SYNC_SECRET = os.getenv("SYNC_SECRET", "").strip()

if not SYNC_SECRET:
    print("ERROR: SYNC_SECRET no configurado en .env")
    sys.exit(1)

# Importar stats_normalizer
sys.path.insert(0, str(BASE))
from stats_normalizer import (
    compute_day_stats,
    normalize_pips,
    is_excluded,
    classify_pair,
)


def _read_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"WARN: no se pudo leer {path.name}: {e}")
        return default if default is not None else {}


def build_state():
    """Construye el state completo con normalizacion VERDAD SIEMPRE."""

    # 1) COPIER cerrados
    copier_data = _read_json(BASE / "copier_stats.json", {"trades": []})
    copier_closed_raw = copier_data.get("trades", [])

    # Normalizar (x10 GOLD, exclusiones)
    copier_closed_clean = []
    for t in copier_closed_raw:
        if is_excluded(t):
            continue
        t2 = dict(t)
        p_norm, cat = normalize_pips(t)
        t2["pips_numeric"] = p_norm
        t2["pips"] = p_norm
        t2["category"] = cat
        copier_closed_clean.append(t2)

    print(f"[copier] {len(copier_closed_raw)} raw -> {len(copier_closed_clean)} clean (excluidos: {len(copier_closed_raw)-len(copier_closed_clean)})")

    # FIX 2026-05-01: CONSOLIDAR multi-TPs como 1 senal por (pair, source, opened_at).
    # La web cuenta wins/losses por len() — si enviamos 5 TPs de la misma senal cuenta
    # como 5 wins. Para VERDAD SIEMPRE: agrupar y mostrar el RESULTADO FINAL de cada senal.
    # Mantenemos los partials/close como entries individuales (son eventos distintos).
    consolidated = {}      # key -> trade entry consolidado
    standalone = []        # parciales y closes que se mantienen como entries propias
    for t in copier_closed_clean:
        result = t.get("result", "")
        # parciales/closes pasan tal cual (son eventos de cierre parcial, distintos a TP/SL final)
        if result in ("close_half", "close_partial", "full_close"):
            standalone.append(t)
            continue
        # Solo TP/SL: consolidar por senal unica
        if result not in ("tp", "sl"):
            standalone.append(t)
            continue
        key = (t.get("pair", ""), t.get("source", ""), t.get("opened_at", 0), t.get("direction", ""))
        if not all(k not in (0, "") for k in key[:3]):
            # senal sin opened_at fiable → entry propia
            standalone.append(t)
            continue
        prev = consolidated.get(key)
        p = float(t.get("pips_numeric", t.get("pips", 0)) or 0)
        if prev is None:
            # primer entry para esta senal
            tt = dict(t)
            tt["_pips_total"] = p if result == "tp" else -p
            tt["_tp_levels_hit"] = 1 if result == "tp" else 0
            tt["_has_sl"] = (result == "sl")
            tt["_has_tp"] = (result == "tp")
            tt["_latest_close"] = t.get("closed_at", 0)
            consolidated[key] = tt
        else:
            # ya existe: acumular
            if result == "tp":
                prev["_pips_total"] += p
                prev["_tp_levels_hit"] += 1
                prev["_has_tp"] = True
            elif result == "sl":
                prev["_pips_total"] -= p
                prev["_has_sl"] = True
            # si este evento es mas reciente, actualizar fecha/pair_display/etc.
            if t.get("closed_at", 0) > prev.get("_latest_close", 0):
                prev["_latest_close"] = t.get("closed_at", 0)
                prev["fecha"] = t.get("fecha", prev.get("fecha", ""))
                prev["closed_at"] = t.get("closed_at", prev.get("closed_at", 0))

    # Reconstruir lista consolidada con resultado final
    copier_consolidated = []
    for key, t in consolidated.items():
        net_pips = t["_pips_total"]
        # Resultado final: TP si net > 0, SL si net < 0 o solo hubo SL
        if t["_has_tp"] and not t["_has_sl"]:
            final_result = "tp"
        elif t["_has_sl"] and not t["_has_tp"]:
            final_result = "sl"
        elif net_pips > 0:
            final_result = "tp"  # TPs ganaron mas que SL parcial
        else:
            final_result = "sl"
        t["result"] = final_result
        t["pips"] = abs(net_pips) if final_result == "sl" else net_pips
        t["pips_numeric"] = t["pips"]
        if t.get("_tp_levels_hit", 0) > 1:
            t["multi_tp_count"] = t["_tp_levels_hit"]
        # limpiar campos internos
        for k in list(t.keys()):
            if k.startswith("_"):
                del t[k]
        copier_consolidated.append(t)

    # Mergear con standalones (parciales/closes) y reordenar por closed_at
    copier_final = sorted(copier_consolidated + standalone, key=lambda x: x.get("closed_at", 0))
    print(f"[consolidate] {len(copier_closed_clean)} eventos -> {len(copier_final)} entries (multi-TPs colapsados)")
    print(f"  - {len(consolidated)} senales unicas TP/SL")
    print(f"  - {len(standalone)} parciales/closes")

    # 2) COPIER abiertas
    copier_open_raw = _read_json(BASE / "copier_open_signals.json", {})
    copier_open = []
    for sid, sd in copier_open_raw.items():
        s = sd.get("signal", {}) if isinstance(sd, dict) else {}
        if not s:
            continue
        raw_pair = (s.get("pair", "") or "").upper().replace("/", "")
        display_pair = {
            "XAUUSD": "ORO", "GOLD": "ORO", "XAU": "ORO",
            "US100CASH": "NAS100", "US500CASH": "US500", "US30CASH": "US30",
            "GER40CASH": "GER40", "BRENTCASH": "BRENT", "OILCASH": "USOIL",
        }.get(raw_pair, raw_pair)
        copier_open.append({
            "id": sid,
            "pair": display_pair,
            "direction": s.get("direction", ""),
            "entry": s.get("entry", 0),
            "sl": s.get("sl", 0),
            "tp": s.get("tp", 0),
            "tp2": s.get("tp2", 0),
            "tp3": s.get("tp3", 0),
            "source": s.get("source", "VIP"),
            "sent_at": sd.get("sent_at", 0),
        })
    print(f"[copier_open] {len(copier_open)} abiertas")

    # 3) Historial real MT5
    hist_real = _read_json(BASE / "historial_real.json", [])
    print(f"[historial_real] {len(hist_real)} trades MT5 reales")

    # 4) MT5 live
    mt5_live = _read_json(BASE / "mt5_realtime.json", {"posiciones_abiertas": [], "historial_hoy": [], "stats_hoy": {}})

    # 5) Calcular timestamps
    tz_and = timezone(timedelta(hours=2))
    now_web = datetime.now(tz_and)
    hoy_start = now_web.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    weekday = now_web.weekday()
    if weekday <= 4:
        week_start_dt = now_web - timedelta(days=weekday)
    else:
        week_start_dt = now_web - timedelta(days=weekday)
    week_start_ts = week_start_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    # 6) Stats HOY via normalizer
    hoy_dmy = now_web.strftime("%d/%m/%Y")
    ds_hoy = compute_day_stats(copier_closed_raw, hoy_dmy)

    hoy = [t for t in copier_final if t.get("closed_at", 0) >= hoy_start]
    tps_hoy = [t for t in hoy if t.get("result") == "tp"]
    sls_hoy = [t for t in hoy if t.get("result") == "sl"]

    # 7) Stats SEMANA — sumar cada dia con normalizer
    semana = [t for t in copier_final if t.get("closed_at", 0) >= week_start_ts]
    tps_sem = [t for t in semana if t.get("result") == "tp"]
    sls_sem = [t for t in semana if t.get("result") == "sl"]
    wr_sem = round(len(tps_sem) / max(1, len(tps_sem) + len(sls_sem)) * 100, 0)

    net_sem = 0.0
    wstart = week_start_dt.date()
    for di in range(7):
        d = wstart + timedelta(days=di)
        dmy = d.strftime("%d/%m/%Y")
        dstats = compute_day_stats(copier_closed_raw, dmy)
        net_sem += dstats["net_total"]

    # 8) Stats LIFETIME — sobre lista RAW (cada TP/SL como evento individual).
    # FIX 2026-05-11 (noche): el usuario pidio que el WR refleje lo que el suscriptor
    # ve en el canal: cada "TP HIT" es 1 win visible. Antes consolidabamos multi-TPs
    # como 1 win (mas "honesto matematico" pero subreporta lo que perciben los usuarios).
    # Ahora: cada TP1+TP2+TP3 cuenta como 3 wins. Cada SL como 1 loss.
    # Mantiene los partials/close_half con profit como wins extras.
    lifetime_net = 0.0
    lifetime_won = 0.0
    lifetime_lost = 0.0
    lifetime_won_count = 0
    lifetime_lost_count = 0
    for t in copier_closed_clean:  # CAMBIO: usar lista raw, no consolidada
        p = float(t.get("pips_numeric", t.get("pips", 0)) or 0)
        r = t.get("result", "")
        if r == "tp":
            lifetime_won += p
            lifetime_net += p
            lifetime_won_count += 1
        elif r == "sl":
            lifetime_lost += p
            lifetime_net -= p
            lifetime_lost_count += 1
        elif r in ("close_half", "close_partial", "full_close") and p > 0:
            lifetime_won += p
            lifetime_net += p
            lifetime_won_count += 1  # parciales en profit tambien suman a wins
    lifetime_total = lifetime_won_count + lifetime_lost_count
    lifetime_wr = round(100 * lifetime_won_count / max(1, lifetime_total), 1)

    # Current streak
    current_streak = 0
    for t in reversed(copier_final[-200:]):
        r = t.get("result", "")
        if r == "tp":
            current_streak += 1
        elif r == "sl":
            break

    print(f"[lifetime] total={lifetime_total} won={lifetime_won_count} lost={lifetime_lost_count} WR={lifetime_wr}%")
    print(f"[lifetime] net={lifetime_net:+.1f} pts (won {lifetime_won:+.1f} - lost {lifetime_lost:+.1f})")
    print(f"[streak] {current_streak} TPs consecutivos")
    print(f"[hoy] net_total={ds_hoy['net_total']:+.1f} pts ({ds_hoy['tps_unique']}W/{ds_hoy['sls_unique']}L)")
    print(f"[semana] net={net_sem:+.1f} pts (week_start={week_start_dt.strftime('%d/%m/%Y')})")

    # 9) Construir state
    state = {
        "copier_open_signals": copier_open,
        "copier_closed_trades": copier_final[-200:],
        "copier_trades": copier_final[-200:],
        "copier_stats_today": {
            "tps": ds_hoy["tps_unique"],
            "tps_events": ds_hoy["tps_events"],
            "sls": ds_hoy["sls_unique"],
            "sls_events": ds_hoy["sls_events"],
            "total_closed": len(hoy),
            "win_rate": round(ds_hoy["wr_unique"], 0),
            "net_pips": round(ds_hoy["net_total"], 1),
            "categories": ds_hoy["categories"],
        },
        "copier_stats_week": {
            "tps": len(tps_sem),
            "sls": len(sls_sem),
            "total_closed": len(semana),
            "win_rate": wr_sem,
            "pips_netos": round(net_sem, 1),
            "net_total": round(net_sem, 1),
            "week_start": week_start_dt.strftime("%d/%m/%Y"),
        },
        "stats_lifetime": {
            "total_signals": lifetime_total,
            "won": lifetime_won_count,
            "lost": lifetime_lost_count,
            "win_rate": lifetime_wr,
            "net_pips": round(lifetime_net, 1),
            "won_pips": round(lifetime_won, 1),
            "lost_pips": round(lifetime_lost, 1),
            "current_streak": current_streak,
        },
        "mt5_live": mt5_live,
        "historial_real": hist_real,
        "operaciones_activas": {},  # vacio en sync manual
        "active_ops_detail": [],
        "estadisticas_diarias": {
            "ganadas": ds_hoy["tps_unique"],
            "perdidas": ds_hoy["sls_unique"],
            "senales_hoy": len(hoy) + len(copier_open),
        },
        "winning_trades": [t for t in copier_closed_clean[-100:] if t.get("result") == "tp"],
        "bot_active": True,
        "auto_trading": os.getenv("AUTO_TRADING", "False").lower() in ("true", "1", "yes"),
        "assets_count": 6,
        "data_source": "manual_sync_v2_truth",
        "account_type": "real",
        "account_login": os.getenv("MT5_LOGIN", ""),
    }
    return state


def main():
    print(f"Forzando sync a {WEB_URL}/api/sync ...")
    state = build_state()

    print(f"\nEnviando {len(json.dumps(state, default=str))} bytes ...")
    try:
        resp = requests.post(
            f"{WEB_URL}/api/sync",
            json=state,
            headers={"X-Sync-Secret": SYNC_SECRET, "Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code == 200:
            print(f"OK — sync completado. Respuesta: {resp.text[:200]}")
        else:
            print(f"ERROR {resp.status_code}: {resp.text[:500]}")
            sys.exit(2)
    except Exception as e:
        print(f"EXCEPTION: {e}")
        sys.exit(3)


if __name__ == "__main__":
    main()

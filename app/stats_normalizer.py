"""
BuySell365 — Stats Normalizer (FIX 2026-05-01)
================================================

Modulo compartido para garantizar TRANSPARENCIA TOTAL en todos los recaps:
  - daily_summary_publisher.py (recap 19:00 imagen)
  - weekly_summary_publisher.py (recap viernes 19:00)
  - signal_copier.py final recap 22:00

REGLAS DE ORO (no negociables — usuario pidio "VERDAD SIEMPRE SIEMPRE"):

1. NET REAL = TPs + parciales - SLs  (NUNCA solo-ganancias)
2. GOLD/XAUUSD/ORO siempre x10 standard market convention (1 pip = $0.10)
3. Multi-TPs de la misma senal cuentan como 1 senal ganadora en WR
4. Excluir sources internas (Manual, MT5_Reinsert) — son inserciones manuales
5. Excluir sources en blacklist (COPIER_SOURCES_DISABLED env var)
6. Categorias separadas: ORO / FOREX / INDICES / OIL / CRIPTO

Uso:
    from stats_normalizer import compute_day_stats, compute_week_stats

    day = compute_day_stats(trades, fecha_dmy="01/05/2026")
    # day = {
    #   "categories": {"ORO": {"net": 270, "tps": 9, "sls": 5}, ...},
    #   "net_total": 100,
    #   "tps_unique": 6,           <-- senales unicas, no eventos
    #   "sls_unique": 8,
    #   "tps_events": 11,           <-- eventos individuales (multi-TPs)
    #   "sls_events": 8,
    #   "partials": 1,
    #   "wr_unique": 42.9,          <-- por senal unica (mas honesto)
    #   "wr_events": 57.9,          <-- por evento (puede inflar)
    #   "best": {"pair": "ORO", "pts": 270, "category": "ORO"},
    #   "worst": {"pair": "INDICES", "pts": -270, "category": "INDICES"},
    # }
"""
import os
from collections import defaultdict
from typing import List, Dict, Optional


# ── Sources excluidas del reporte publico ──
# 'Historico' = trades legacy de canales ya retirados (relabelados 2026-05-11)
# 'Unknown'   = fallback cuando el chat_title no matchea ningun canal activo
_SOURCES_INTERNAS = {"Manual", "MT5_Reinsert", "Internal", "Bot", "Historico", "Unknown"}


def _get_blacklist() -> set:
    """Lee COPIER_SOURCES_DISABLED del entorno (lista coma-separada)."""
    raw = os.getenv("COPIER_SOURCES_DISABLED", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


# ── Clasificacion de pares ──
GOLD_PAIRS = {"XAUUSD", "GOLD", "ORO", "XAU", "XAU/USD"}
INDEX_PAIRS = {
    "NAS100", "NDX100", "NDX", "USTEC", "NQ100",
    "US30", "DJ30", "DOW30", "WS30", "DJI",
    "SPX500", "SPX", "US500", "SP500",
    "GER40", "DE40", "DAX40", "DAX",
    "UK100", "FTSE", "FTSE100",
    "JPN225", "NIKKEI", "JP225", "N225",
    "FR40", "CAC40", "CAC",
    "AUS200", "ASX200",
}
OIL_PAIRS = {"USOIL", "UKOIL", "BRENT", "WTI", "NATGAS", "XBRUSD", "XTIUSD", "XNGUSD", "OIL"}


def classify_pair(pair: str) -> str:
    """Devuelve la categoria de un par: ORO / FOREX / INDICES / OIL / CRIPTO / OTHER."""
    if not pair:
        return "OTHER"
    p = pair.upper().replace("/", "").replace("-", "").replace(" ", "")
    if p in GOLD_PAIRS:
        return "ORO"
    if p in INDEX_PAIRS:
        return "INDICES"
    if p in OIL_PAIRS:
        return "OIL"
    if "BTC" in p or "ETH" in p or "USDT" in p or "USDC" in p or "DOGE" in p or "SOL" in p or "XRP" in p:
        return "CRIPTO"
    # Forex: 6 letras alfa (EUR/USD, GBP/JPY, etc.)
    if len(p) == 6 and p.isalpha():
        return "FOREX"
    return "OTHER"


def category_unit(cat: str) -> str:
    """Unidad de medida correcta por categoria."""
    if cat == "FOREX":
        return "pips"
    if cat == "CRIPTO":
        return "USD"
    return "pts"  # ORO, INDICES, OIL → puntos


def normalize_pips(trade: Dict) -> tuple:
    """Normaliza pips de un trade aplicando x10 GOLD consistente.

    Retorna (pips_normalizado, categoria).

    Para GOLD: detecta si los pips almacenados estan en "raw" (sin x10) o ya x10,
    comparando con el delta entry-tp/sl. Si son raw, los multiplica por 10.
    """
    pair = trade.get("pair_display") or trade.get("pair") or ""
    cat = classify_pair(pair)
    raw_pips = float(trade.get("pips_numeric", trade.get("pips", 0)) or 0)

    if cat == "ORO":
        entry = float(trade.get("entry", 0) or 0)
        result = trade.get("result")
        if result == "tp":
            ref = float(trade.get("tp", 0) or 0)
        elif result == "sl":
            ref = float(trade.get("sl", 0) or 0)
        else:
            # parciales y otros: no podemos verificar, devolver tal cual
            return raw_pips, cat

        if entry > 0 and ref > 0:
            raw_diff = abs(entry - ref)
            # Si raw_pips coincide con raw_diff (no x10) y es razonable (<30) → multiplicar
            if abs(raw_pips - raw_diff) < 0.5 and raw_diff < 30:
                return raw_diff * 10, cat
            # Si ya esta x10 (raw_pips ≈ raw_diff*10) o es muy grande → mantener
        return raw_pips, cat

    return raw_pips, cat


def is_excluded(trade: Dict, blacklist: Optional[set] = None) -> bool:
    """True si el trade debe excluirse del reporte (interna o blacklisted)."""
    src = trade.get("source", "")
    if src in _SOURCES_INTERNAS:
        return True
    if blacklist is None:
        blacklist = _get_blacklist()
    if src in blacklist:
        return True
    return False


def _signal_key(trade: Dict) -> tuple:
    """Clave unica de senal para agrupar multi-TPs.
    Misma (pair, source, opened_at) = misma senal, multiples TPs son fragmentos.
    """
    return (
        trade.get("pair", ""),
        trade.get("source", ""),
        trade.get("opened_at", 0),
        trade.get("direction", ""),
    )


def compute_day_stats(trades: List[Dict], fecha_dmy: str) -> Dict:
    """Calcula stats reales de un dia.

    Args:
        trades: lista completa de trades del archivo copier_stats.json["trades"]
        fecha_dmy: fecha en formato "DD/MM/YYYY"

    Returns:
        dict con net por categoria, totales, WR, best/worst, etc.
    """
    blacklist = _get_blacklist()
    day_trades = [
        t for t in trades
        if t.get("fecha") == fecha_dmy and not is_excluded(t, blacklist)
    ]

    tps = [t for t in day_trades if t.get("result") == "tp"]
    sls = [t for t in day_trades if t.get("result") == "sl"]
    partials = [
        t for t in day_trades
        if t.get("result") in ("close_half", "close_partial", "full_close")
        and float(t.get("pips_numeric", t.get("pips", 0)) or 0) > 0
    ]

    cat_net = defaultdict(float)
    cat_tps = defaultdict(int)
    cat_sls = defaultdict(int)
    cat_partials = defaultdict(int)

    sum_tps = 0.0
    sum_sls = 0.0  # almacenado como positivo (para mostrar magnitud), restado al final
    sum_par = 0.0

    # Para multi-TP grouping (WR por senal unica)
    won_signals = set()
    lost_signals = set()

    for t in tps:
        p, cat = normalize_pips(t)
        sum_tps += p
        cat_net[cat] += p
        cat_tps[cat] += 1
        key = _signal_key(t)
        if all(k != 0 and k != "" for k in key[:3]):  # evita keys vacias
            won_signals.add(key)

    for t in sls:
        p, cat = normalize_pips(t)
        sum_sls += p  # positivo
        cat_net[cat] -= p  # restado en categoria
        cat_sls[cat] += 1
        key = _signal_key(t)
        if all(k != 0 and k != "" for k in key[:3]):
            lost_signals.add(key)

    for t in partials:
        p, cat = normalize_pips(t)
        sum_par += p
        cat_net[cat] += p
        cat_partials[cat] += 1

    # FIX 2026-05-18 P1.13: ya NO cancelamos lost vs won del mismo (pair,src,opened,dir).
    # Antes: si una senal hacia TP1 +50 y luego SL -100 en lo restante, el SL desaparecia
    # del WR porque set-diff lo eliminaba contra el TP. Resultado: WR publicado mayor
    # que el real. Ahora ambos cuentan en su categoria — la magnitud neta ya se
    # refleja en net_total (sum_tps + sum_par - sum_sls), pero el WR honesto necesita
    # contar las dos caras del trade.
    final_lost = lost_signals

    net_total = sum_tps + sum_par - sum_sls

    # Best / worst por par (no por categoria)
    by_pair = defaultdict(float)
    for t in day_trades:
        p, _ = normalize_pips(t)
        result = t.get("result")
        pair = t.get("pair_display") or t.get("pair", "?")
        if result == "tp" or (result in ("close_half", "close_partial", "full_close") and p > 0):
            by_pair[pair] += p
        elif result == "sl":
            by_pair[pair] -= p

    best_pair, best_pts = (None, 0)
    worst_pair, worst_pts = (None, 0)
    if by_pair:
        best_pair, best_pts = max(by_pair.items(), key=lambda kv: kv[1])
        worst_pair, worst_pts = min(by_pair.items(), key=lambda kv: kv[1])

    tps_events = len(tps)
    sls_events = len(sls)
    # Orphan trades have opened_at=0 (close not linked to original signal entry).
    # They still represent real events — count them separately so they don't vanish.
    orphan_tps = sum(1 for t in tps if not all(k != 0 and k != "" for k in _signal_key(t)[:3]))
    orphan_sls = sum(1 for t in sls if not all(k != 0 and k != "" for k in _signal_key(t)[:3]))
    tps_unique = len(won_signals) + orphan_tps
    sls_unique = len(final_lost) + orphan_sls

    wr_events = (100 * tps_events / (tps_events + sls_events)) if (tps_events + sls_events) > 0 else 0.0
    wr_unique = (100 * tps_unique / (tps_unique + sls_unique)) if (tps_unique + sls_unique) > 0 else 0.0

    # Categorias para mostrar (orden + unidad)
    categories = []
    for cat in ("ORO", "FOREX", "INDICES", "OIL", "CRIPTO", "OTHER"):
        if cat in cat_net or cat_tps.get(cat, 0) > 0 or cat_sls.get(cat, 0) > 0:
            categories.append({
                "name": cat,
                "net": cat_net.get(cat, 0),
                "unit": category_unit(cat),
                "tps": cat_tps.get(cat, 0),
                "sls": cat_sls.get(cat, 0),
                "partials": cat_partials.get(cat, 0),
            })

    return {
        "fecha": fecha_dmy,
        "categories": categories,
        "net_total": net_total,
        "sum_tps": sum_tps,
        "sum_sls": sum_sls,
        "sum_par": sum_par,
        "tps_events": tps_events,
        "sls_events": sls_events,
        "tps_unique": tps_unique,
        "sls_unique": sls_unique,
        "partials": len(partials),
        "wr_events": wr_events,
        "wr_unique": wr_unique,
        "best": {"pair": best_pair, "pts": best_pts} if best_pair else None,
        "worst": {"pair": worst_pair, "pts": worst_pts} if worst_pair and worst_pts < 0 else None,
        "trades_count": len(day_trades),
    }


def compute_week_stats(trades: List[Dict], fechas_dmy: List[str], day_names: List[str]) -> Dict:
    """Calcula stats reales semanales (sumando dias).

    Args:
        trades: lista completa de trades
        fechas_dmy: lista de 7 fechas "DD/MM/YYYY" (sabado a viernes)
        day_names: lista de 7 nombres ["SATURDAY", "SUNDAY", ..., "FRIDAY"]
    """
    days = []
    total_net = 0.0
    total_tps_events = 0
    total_sls_events = 0
    total_tps_unique = 0
    total_sls_unique = 0
    total_partials = 0
    best_day = None

    for fecha, name in zip(fechas_dmy, day_names):
        ds = compute_day_stats(trades, fecha)
        ds["name"] = name
        days.append(ds)
        total_net += ds["net_total"]
        total_tps_events += ds["tps_events"]
        total_sls_events += ds["sls_events"]
        total_tps_unique += ds["tps_unique"]
        total_sls_unique += ds["sls_unique"]
        total_partials += ds["partials"]
        if best_day is None or ds["net_total"] > best_day["net_total"]:
            best_day = ds

    wr_events = (100 * total_tps_events / (total_tps_events + total_sls_events)) if (total_tps_events + total_sls_events) > 0 else 0.0
    wr_unique = (100 * total_tps_unique / (total_tps_unique + total_sls_unique)) if (total_tps_unique + total_sls_unique) > 0 else 0.0

    return {
        "days": days,
        "total_net": total_net,
        "total_tps_events": total_tps_events,
        "total_sls_events": total_sls_events,
        "total_tps_unique": total_tps_unique,
        "total_sls_unique": total_sls_unique,
        "total_partials": total_partials,
        "wr_events": wr_events,
        "wr_unique": wr_unique,
        "best_day": best_day,
    }


# ── Test rapido ──
if __name__ == "__main__":
    import json
    from pathlib import Path

    stats_file = Path(__file__).parent / "copier_stats.json"
    if not stats_file.exists():
        # fallback a la raiz del proyecto
        stats_file = Path(r"C:\Users\hpint\Desktop\BuySell365_Bot\copier_stats.json")

    with open(stats_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    trades = data.get("trades", [])

    print("=" * 70)
    print("TEST stats_normalizer — FRIDAY 01/05/2026")
    print("=" * 70)
    ds = compute_day_stats(trades, "01/05/2026")
    print(f"\nNet total: {ds['net_total']:+.1f} pts")
    print(f"TPs: {ds['tps_events']} eventos · {ds['tps_unique']} senales unicas")
    print(f"SLs: {ds['sls_events']} eventos · {ds['sls_unique']} senales unicas")
    print(f"Partials: {ds['partials']}")
    print(f"WR (eventos): {ds['wr_events']:.1f}%")
    print(f"WR (senales unicas): {ds['wr_unique']:.1f}%")
    print("\nPor categoria:")
    for c in ds["categories"]:
        print(f"  {c['name']:8} {c['net']:+8.1f} {c['unit']:5} ({c['tps']}TP/{c['sls']}SL/{c['partials']}P)")
    if ds.get("best"):
        print(f"\nBest pair:  {ds['best']['pair']} {ds['best']['pts']:+.1f}")
    if ds.get("worst"):
        print(f"Worst pair: {ds['worst']['pair']} {ds['worst']['pts']:+.1f}")

    print("\n" + "=" * 70)
    print("TEST WEEK 25-29 APR + 30 APR + 01 MAY")
    print("=" * 70)
    fechas = ["25/04/2026", "26/04/2026", "27/04/2026", "28/04/2026", "29/04/2026", "30/04/2026", "01/05/2026"]
    names = ["SATURDAY", "SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]
    ws = compute_week_stats(trades, fechas, names)
    for d in ws["days"]:
        print(f"  {d['name']:11} {d['fecha']} {d['net_total']:+8.1f} pts  ({d['tps_unique']}W/{d['sls_unique']}L)")
    print(f"\n  TOTAL: {ws['total_net']:+,.1f} pts")
    print(f"  WR senal unica:  {ws['wr_unique']:.1f}% ({ws['total_tps_unique']}W/{ws['total_sls_unique']}L)")
    print(f"  WR eventos:      {ws['wr_events']:.1f}% ({ws['total_tps_events']}TP/{ws['total_sls_events']}SL)")
    if ws.get("best_day"):
        print(f"  Best day: {ws['best_day']['name']} {ws['best_day']['net_total']:+.1f}")

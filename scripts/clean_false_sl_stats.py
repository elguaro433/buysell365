#!/usr/bin/env python3
"""
clean_false_sl_stats.py — Limpia de copier_stats.json los "SL HIT" falsos
generados por el desfase futuro-vs-spot (GC=F / YM=F / NQ=F) entre el
29-jul-2026 y el 17-sep-2026 (ver FIX 2026-09-17 en price_feed.py).

Criterio ESTRICTO (solo se borra si se cumplen TODOS):
  - pair en ORO / US30 / NAS100 / US100 / US500
  - result == "sl"
  - closed_at - opened_at < 120 s   (SL en menos de 2 min de publicarse)
  - fecha >= 29/07/2026

Uso (con el bot PARADO):
  python3 clean_false_sl_stats.py /opt/buysell365/app/copier_stats.json [--apply]
Sin --apply solo muestra lo que borraria. Con --apply hace backup .bak_false_sl_<ts>.
"""
import json, sys, time, datetime, shutil

GOLD_IDX = {"XAUUSD", "GOLD", "ORO", "US30", "US30CASH", "NAS100", "US100", "US100CASH", "US500", "US500CASH"}
SINCE = datetime.date(2026, 7, 29)
MAX_DUR = 120

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    path = sys.argv[1]; apply = "--apply" in sys.argv
    data = json.load(open(path, encoding="utf-8"))
    trades = data.get("trades", [])
    keep, drop = [], []
    for t in trades:
        try:
            pair = (t.get("pair") or "").upper()
            res = (t.get("result") or "").lower()
            o = float(t.get("opened_at") or 0); c = float(t.get("closed_at") or 0)
            f = datetime.datetime.strptime(t.get("fecha", "01/01/2000"), "%d/%m/%Y").date()
            if pair in GOLD_IDX and res == "sl" and o > 0 and c > 0 and (c - o) < MAX_DUR and f >= SINCE:
                drop.append(t); continue
        except Exception:
            pass
        keep.append(t)
    print(f"trades totales: {len(trades)}  |  SL falsos detectados: {len(drop)}  |  quedan: {len(keep)}")
    by_day = {}
    for t in drop:
        by_day[t.get("fecha")] = by_day.get(t.get("fecha"), 0) + 1
    for d in sorted(by_day, key=lambda x: datetime.datetime.strptime(x, "%d/%m/%Y")):
        print(f"  {d}: {by_day[d]}")
    if not apply:
        print("(dry-run — añade --apply para escribir)"); return
    bak = f"{path}.bak_false_sl_{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(path, bak); print("backup:", bak)
    data["trades"] = keep
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    import os; os.replace(tmp, path)
    print("escrito", path)

if __name__ == "__main__":
    main()

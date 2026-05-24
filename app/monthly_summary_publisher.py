"""monthly_summary_publisher.py — Reporte mensual del primer lunes de cada mes.

Genera y publica un recap mensual del mes anterior:
- Imagen estilo dashboard (1080x1920) con stats globales
- Caption a Canal VIP + Grupo gratis
- Centraliza datos via stats_normalizer (consistente con daily, anti-bug x10)

Uso CLI:
    python monthly_summary_publisher.py              # mes anterior automatico
    python monthly_summary_publisher.py --mes 04 --year 2026  # mes especifico
    python monthly_summary_publisher.py --dry-run    # generar imagen sin publicar
"""
from __future__ import annotations
import os
import sys
import json
import time
import logging
import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz
from dotenv import load_dotenv
from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")
sys.path.insert(0, str(BASE))

from stats_normalizer import normalize_pips  # noqa: E402

TZ = pytz.timezone("Europe/Andorra")
TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
GROUP_ID = os.getenv("GROUP_ID", "")
VIP_DEEPLINK = f"https://t.me/{os.getenv('BOT_USERNAME','Andoperandobot')}?start=vip"

STATS_PATH = BASE / "copier_stats.json"
LOGO_PATH = BASE / "logo.png"
IG_IMAGES_DIR = BASE / "ig_images"
IG_IMAGES_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [MONTHLY] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

# Colores
W, H = 1080, 1920
WHITE = (245, 248, 252)
GRAY = (140, 160, 180)
GREEN = (76, 217, 136)
RED_SOFT = (235, 95, 95)
GOLD = (255, 195, 40)
CYAN = (90, 220, 230)
DARK_BG = (4, 28, 46)
CARD_BG = (10, 40, 54)


# ── 1) Cargar trades del mes ─────────────────────────────────

def _trades_del_mes(month: int, year: int) -> list:
    """Carga trades del mes/anio, excluye Manual y MT5_Reinsert."""
    if not STATS_PATH.exists():
        return []
    try:
        data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"No se pudo leer copier_stats.json: {e}")
        return []
    suffix = f"/{month:02d}/{year}"
    out = []
    for t in data.get("trades", []):
        if not t.get("fecha", "").endswith(suffix):
            continue
        if t.get("source", "") in ("Manual", "MT5_Reinsert"):
            continue
        out.append(t)
    return out


# ── 2) Calcular stats mensuales ──────────────────────────────

def calcular_stats_mes(month: int, year: int) -> dict:
    trades = _trades_del_mes(month, year)
    if not trades:
        return {"empty": True, "month": month, "year": year}

    cat_net = defaultdict(float)
    cat_tps = defaultdict(int)
    cat_sls = defaultdict(int)
    cat_partials = defaultdict(int)
    by_pair = defaultdict(float)
    by_day = defaultdict(float)
    days_traded = set()
    sources = defaultdict(int)
    won_signals = set()
    lost_signals = set()
    tps_events = 0
    sls_events = 0
    partial_events = 0
    sl_pips_total = 0.0
    tp_pips_total = 0.0

    # Normalizar nombre pair (ORO == XAUUSD == GOLD)
    def _norm_pair_display(t):
        p = (t.get("pair_display") or t.get("pair", "?")).upper()
        if p in ("XAUUSD", "GOLD", "XAU/USD", "XAU"):
            return "ORO"
        return p

    for t in trades:
        pips, cat = normalize_pips(t)
        pair = _norm_pair_display(t)
        fecha = t.get("fecha", "")
        days_traded.add(fecha)
        sources[t.get("source", "?")] += 1
        result = t.get("result", "")
        key = (t.get("pair", ""), t.get("source", ""),
               t.get("opened_at", 0), t.get("direction", ""))

        if result == "tp":
            cat_net[cat] += pips
            cat_tps[cat] += 1
            by_pair[pair] += pips
            by_day[fecha] += pips
            tps_events += 1
            tp_pips_total += pips
            if all(k != 0 and k != "" for k in key[:3]):
                won_signals.add(key)
        elif result == "sl":
            cat_net[cat] -= pips
            cat_sls[cat] += 1
            by_pair[pair] -= pips
            by_day[fecha] -= pips
            sls_events += 1
            sl_pips_total += pips
            if all(k != 0 and k != "" for k in key[:3]):
                lost_signals.add(key)
        elif result in ("close_half", "close_partial", "full_close") and pips > 0:
            cat_net[cat] += pips
            cat_partials[cat] += 1
            by_pair[pair] += pips
            by_day[fecha] += pips
            partial_events += 1

    final_lost = lost_signals - won_signals
    tps_unique = len(won_signals)
    sls_unique = len(final_lost)
    wr = round(100 * tps_unique / (tps_unique + sls_unique)) if (tps_unique + sls_unique) else 0

    net_total = sum(cat_net.values())
    top_pairs = sorted(by_pair.items(), key=lambda kv: -kv[1])[:5]
    best_day = max(by_day.items(), key=lambda kv: kv[1]) if by_day else None
    worst_day = min(by_day.items(), key=lambda kv: kv[1]) if by_day else None

    categories = []
    for cat in ("ORO", "FOREX", "INDICES", "OIL", "CRIPTO", "OTHER"):
        if cat_net[cat] != 0 or cat_tps[cat] or cat_sls[cat] or cat_partials[cat]:
            unit = "pips" if cat in ("ORO", "FOREX") else "pts"
            categories.append({
                "name": cat, "net": cat_net[cat], "unit": unit,
                "tps": cat_tps[cat], "sls": cat_sls[cat],
                "partials": cat_partials[cat],
            })

    return {
        "empty": False,
        "month": month,
        "year": year,
        "trades_count": len(trades),
        "tps_events": tps_events,
        "sls_events": sls_events,
        "tps_unique": tps_unique,
        "sls_unique": sls_unique,
        "partial_events": partial_events,
        "wr": wr,
        "net_total": net_total,
        "tp_pips_total": tp_pips_total,
        "sl_pips_total": sl_pips_total,
        "categories": categories,
        "top_pairs": top_pairs,
        "best_day": best_day,
        "worst_day": worst_day,
        "days_traded": len(days_traded),
    }


# ── 3) Generar imagen ────────────────────────────────────────

def _font(size: int, bold: bool = False):
    """Carga fuente DejaVuSans o cae a default."""
    try:
        from PIL import ImageFont
        if bold:
            paths = [
                "C:\\Windows\\Fonts\\arialbd.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
        else:
            paths = [
                "C:\\Windows\\Fonts\\arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ]
        for p in paths:
            if Path(p).exists():
                return ImageFont.truetype(p, size)
        return ImageFont.load_default()
    except Exception:
        from PIL import ImageFont
        return ImageFont.load_default()


def _bg() -> Image.Image:
    img = Image.new("RGB", (W, H), DARK_BG)
    d = ImageDraw.Draw(img)
    # Gradient sutil top
    for y in range(0, 400):
        c = int(28 + 12 * (1 - y / 400))
        d.line([(0, y), (W, y)], fill=(4, 28, c))
    return img


def _rounded(d: ImageDraw.ImageDraw, box, r: int = 22, fill=None,
             outline=None, width: int = 1):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def _nombre_mes(month: int) -> str:
    nombres = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    return nombres[month - 1]


def generar_imagen_mes(stats: dict) -> Path:
    img = _bg()
    d = ImageDraw.Draw(img)

    # Borde decorativo sutil
    _rounded(d, [20, 20, W - 20, H - 20], r=40, outline=(20, 70, 95), width=2)

    # ── HEADER ─────────────────────────────────────────────
    try:
        if LOGO_PATH.exists():
            lg = Image.open(LOGO_PATH).convert("RGBA").resize((130, 130), Image.LANCZOS)
            img.paste(lg, (80, 90), lg)
    except Exception:
        pass
    d.text((230, 100), "BuySell365", fill=WHITE, font=_font(56, True))
    d.text((230, 165), "Pro  -  VIP Channel", fill=CYAN, font=_font(34, True))
    d.text((230, 215), "MONTHLY RECAP", fill=GOLD, font=_font(22, True))

    # Mes badge — dorado
    label = f"{_nombre_mes(stats['month'])} {stats['year']}"
    badge_w = 30 * len(label) + 60
    badge_x0 = (W - badge_w) // 2
    _rounded(d, [badge_x0, 285, badge_x0 + badge_w, 360], r=38,
             fill=(28, 70, 95), outline=GOLD, width=3)
    d.text((W // 2, 322), label, fill=GOLD, font=_font(30, True), anchor="mm")

    # Linea decorativa bajo header
    d.line([(120, 390), (W - 120, 390)], fill=(30, 90, 110), width=2)

    # ── HERO BOX ──────────────────────────────────────────
    net = stats["net_total"]
    sig = "+" if net >= 0 else ""
    hero_color = GREEN if net >= 0 else RED_SOFT
    label_top = "MONTHLY NET PROFIT" if net >= 0 else "MONTHLY RESULT"

    x0, y0, x1, y1 = 60, 420, W - 60, 770
    # Sombra
    _rounded(d, [x0 + 4, y0 + 6, x1 + 4, y1 + 6], r=30, fill=(2, 18, 28))
    # Caja principal
    _rounded(d, [x0, y0, x1, y1], r=30, fill=(8, 40, 56),
             outline=hero_color, width=3)
    # Banda superior dorada
    _rounded(d, [x0, y0, x1, y0 + 65], r=30, fill=(14, 65, 85))
    d.text((W // 2, y0 + 32), label_top, fill=GOLD,
           font=_font(26, True), anchor="mm")
    # Numero gigante
    d.text((W // 2, y0 + 165), f"{sig}{int(round(net))}",
           fill=hero_color, font=_font(170, True), anchor="mm")
    # Subtitulo
    d.text((W // 2, y0 + 270), "POINTS NET", fill=WHITE,
           font=_font(38, True), anchor="mm")
    # Breakdown por categoria
    parts = []
    for c in stats["categories"]:
        sgn = "+" if c["net"] >= 0 else ""
        parts.append(f"{c['name']} {sgn}{c['net']:.0f}")
    breakdown = "   |   ".join(parts)
    if breakdown:
        d.text((W // 2, y0 + 320), breakdown, fill=GRAY,
               font=_font(20), anchor="mm")

    # ── STATS GRID 2x2 ────────────────────────────────────
    # FIX 2026-05-04: NO mostrar count de losing signals (asusta clientes
    # potenciales). Solo mostrar metricas positivas. SL aparece como total
    # de pips al final del recap como referencia honesta sin numero de fallos.
    cards = [
        (GREEN, str(stats["tps_unique"]), "WINNING SIGNALS", "✓"),
        (GOLD, str(stats["tps_events"]), "TP HITS", "🎯"),
        (CYAN, str(stats["partial_events"]), "PARTIAL CLOSES", "⚡"),
        (GREEN, f"{stats['wr']}%", "WIN RATE", "🏆"),
    ]
    cw = (W - 60 - 60 - 30) // 2
    ch = 180
    bx = 60
    by = 810
    gap = 30
    for i, (col, big, lbl, ic) in enumerate(cards):
        x = bx + (i % 2) * (cw + gap)
        y = by + (i // 2) * (ch + gap)
        # Sombra
        _rounded(d, [x + 3, y + 4, x + cw + 3, y + ch + 4], r=22, fill=(2, 18, 28))
        # Card
        _rounded(d, [x, y, x + cw, y + ch], r=22, fill=CARD_BG,
                 outline=col, width=2)
        # Numero grande
        d.text((x + cw // 2, y + ch // 2 + 5), big,
               fill=col, font=_font(64, True), anchor="mm")
        # Label
        d.text((x + cw // 2, y + ch - 28), lbl,
               fill=GRAY, font=_font(17, True), anchor="mm")

    # ── TOP 5 PAIRS ──────────────────────────────────────
    y_base = 1230
    d.text((W // 2, y_base), "TOP 5 PAIRS OF THE MONTH",
           fill=GOLD, font=_font(26, True), anchor="ma")
    # Linea decorativa
    d.line([(W // 2 - 200, y_base + 38), (W // 2 + 200, y_base + 38)],
           fill=GOLD, width=2)
    y = y_base + 60
    medal_colors = [
        ((255, 195, 40), GOLD),         # oro
        ((200, 210, 220), (220, 230, 240)),  # plata
        ((205, 130, 80), (220, 160, 105)),    # bronce
        ((110, 140, 170), (130, 160, 190)),   # azul
        ((110, 140, 170), (130, 160, 190)),   # azul
    ]
    for idx in range(5):
        if idx < len(stats["top_pairs"]):
            pair, val = stats["top_pairs"][idx]
            txt_val = f"{'+' if val >= 0 else ''}{val:.0f} pts"
            val_color = GREEN if val >= 0 else RED_SOFT
        else:
            pair, txt_val = "—", ""
            val_color = GRAY
        mc, _ = medal_colors[idx]
        # Sombra row
        _rounded(d, [83, y + 3, W - 77, y + 73], r=16, fill=(2, 18, 28))
        _rounded(d, [80, y, W - 80, y + 70], r=16,
                 fill=(8, 42, 58), outline=(30, 90, 110), width=2)
        # Medalla
        d.ellipse([98, y + 12, 144, y + 58], fill=mc, outline=WHITE, width=2)
        d.text((121, y + 35), str(idx + 1), fill=(4, 28, 46),
               font=_font(26, True), anchor="mm")
        # Par
        d.text((170, y + 35), pair, fill=WHITE, font=_font(28, True), anchor="lm")
        # Valor
        d.text((W - 110, y + 35), txt_val, fill=val_color,
               font=_font(28, True), anchor="rm")
        y += 78

    # ── FOOTER MINI (todo pequeno y discreto) ─────────────
    # FIX 2026-05-04: SL pips se muestra como texto pequeno aqui, no como
    # caja roja grande. El usuario lo quiere discreto al final.
    sl_pips = stats.get("sl_pips_total", 0)
    fy = y + 35
    if stats.get("best_day"):
        d.text((W // 2, fy),
               f"BEST DAY: {stats['best_day'][0]}  ({stats['best_day'][1]:+.0f} pts)",
               fill=GREEN, font=_font(20, True), anchor="ma")
        fy += 30
    if stats.get("days_traded"):
        d.text((W // 2, fy), f"{stats['days_traded']} active trading days",
               fill=GRAY, font=_font(18), anchor="ma")
        fy += 28
    # SL pips — pequeno y gris, al final
    d.text((W // 2, fy), f"Total SL: -{sl_pips:.0f} pips",
           fill=(160, 130, 130), font=_font(16), anchor="ma")

    # Save
    out_name = f"recap_mensual_{stats['month']:02d}_{stats['year']}.jpg"
    out_path = IG_IMAGES_DIR / out_name
    img.save(out_path, "JPEG", quality=94)
    log.info(f"[OK] Imagen mensual generada -> {out_name}")
    return out_path


# ── 4) Captions ──────────────────────────────────────────────

def _caption_vip(s: dict) -> str:
    """FIX 2026-05-04: NO mostrar count de losing signals. Solo SL pips total."""
    sig = "+" if s["net_total"] >= 0 else ""
    cat_lines = ""
    if s["categories"]:
        parts = [f"  {c['name']}: {'+' if c['net']>=0 else ''}{c['net']:.0f} {c['unit']}"
                 for c in s["categories"]]
        cat_lines = "\n".join(parts) + "\n\n"

    medallas = []
    em = ["🥇", "🥈", "🥉", "🏅", "🏅"]
    for i, (p, v) in enumerate(s["top_pairs"][:5]):
        medallas.append(f"{em[i]} {p}: {'+' if v>=0 else ''}{v:.0f}")
    medallas_txt = "\n".join(medallas)

    sl_pips = s.get("sl_pips_total", 0)
    mes_label = f"{_nombre_mes(s['month'])} {s['year']}"
    return (
        f"📊 *MONTHLY RECAP — {mes_label}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"✓ *{s['tps_unique']} winning signals*\n"
        f"🎯 {s['tps_events']} TP hits\n"
        f"⚡ {s['partial_events']} partial closes in profit\n\n"
        f"🏆 *Win Rate: {s['wr']}%*\n"
        f"📈 *{sig}{s['net_total']:.0f} points* net for the month\n\n"
        f"{cat_lines}"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"*TOP 5 PAIRS*\n"
        f"{medallas_txt}\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"📅 Best day: *{s['best_day'][0]}* ({s['best_day'][1]:+.0f} pts)\n"
        f"📊 {s['days_traded']} active trading days\n"
        f"🛡️ Total SL: -{sl_pips:.0f} pips\n\n"
        f"Thanks for an incredible month 💎\n"
        f"_Full transparency · Professional management_"
    )


def _caption_grupo(s: dict) -> str:
    """FIX 2026-05-04: NO mostrar count de losing signals. Solo SL pips total."""
    sig = "+" if s["net_total"] >= 0 else ""
    medallas = []
    em = ["🥇", "🥈", "🥉"]
    for i, (p, v) in enumerate(s["top_pairs"][:3]):
        medallas.append(f"{em[i]} {p}: {'+' if v>=0 else ''}{v:.0f} pts")
    medallas_txt = "\n".join(medallas)

    sl_pips = s.get("sl_pips_total", 0)
    mes_label = f"{_nombre_mes(s['month'])} {s['year']}"
    return (
        f"🚀 *{mes_label} CLOSED — RESULTS RECAP* 📊\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"✓ *{s['tps_unique']} winning signals*\n"
        f"🏆 *Win Rate: {s['wr']}%*\n"
        f"📈 *{sig}{s['net_total']:.0f} points* net\n"
        f"📅 *{s['days_traded']} active days*\n"
        f"🛡️ *Total SL: -{sl_pips:.0f} pips*\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{medallas_txt}\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔥 *Imagine getting these results EVERY day*\n"
        f"💎 *VIP members got every single signal in real time*\n\n"
        f"👑 *Want to start next month strong?*\n"
        f"👇 Join the VIP Channel"
    )


# ── 5) Publicacion ────────────────────────────────────────────

def _tg_send_photo(chat_id, img_path: Path, caption: str,
                   reply_markup: Optional[dict] = None) -> Optional[int]:
    import requests
    if not TOKEN:
        log.warning("Sin TELEGRAM_TOKEN")
        return None
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    with open(img_path, "rb") as f:
        files = {"photo": (img_path.name, f, "image/jpeg")}
        data = {"chat_id": str(chat_id), "caption": caption,
                "parse_mode": "Markdown"}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        try:
            r = requests.post(url, data=data, files=files, timeout=60)
            if r.status_code == 200:
                return r.json().get("result", {}).get("message_id")
            log.warning(f"send_photo {chat_id}: {r.status_code} {r.text[:100]}")
        except Exception as e:
            log.warning(f"send_photo {chat_id}: {e}")
    return None


def publicar_mensual(month: int, year: int, dry_run: bool = False) -> dict:
    stats = calcular_stats_mes(month, year)
    if stats.get("empty"):
        return {"empty": True}

    img = generar_imagen_mes(stats)

    if dry_run:
        log.info("DRY RUN — no se publica")
        log.info(f"Imagen: {img}")
        return {"img": str(img), "stats": stats, "dry_run": True}

    # FIX 2026-05-24: REGLA SOLO-POSITIVO GLOBAL — si el mes cierra en
    # negativo no se publica el recap a ningun destino. Misma politica
    # que daily y weekly.
    try:
        _net_mes = float(stats.get("net_total", 0) or 0)
    except Exception:
        _net_mes = 0.0
    if _net_mes < 0:
        log.info(f"📕 Monthly recap NO publicado — net negativo {_net_mes:+.0f} (regla solo-positivo global)")
        return {"img": str(img), "stats": stats, "skipped": "negative_net"}

    resultados = {}
    if CHANNEL_ID:
        mid = _tg_send_photo(CHANNEL_ID, img, _caption_vip(stats))
        resultados["Canal VIP"] = bool(mid)
        time.sleep(2)

    if GROUP_ID:
        btn = {"inline_keyboard": [[
            {"text": "👑 JOIN VIP CHANNEL", "url": VIP_DEEPLINK}
        ]]}
        mid = _tg_send_photo(GROUP_ID, img, _caption_grupo(stats), btn)
        resultados["Grupo gratis"] = bool(mid)

    return {"img": str(img), "stats": stats, "resultados": resultados}


# ── 6) CLI ────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mes", type=int, help="Mes 1-12 (default: mes anterior)")
    p.add_argument("--year", type=int, help="Anio (default: actual)")
    p.add_argument("--dry-run", action="store_true",
                   help="Generar imagen sin publicar")
    args = p.parse_args()

    now = datetime.now(TZ)
    if args.mes:
        month, year = args.mes, args.year or now.year
    else:
        # Mes anterior
        if now.month == 1:
            month, year = 12, now.year - 1
        else:
            month, year = now.month - 1, now.year

    log.info(f"Generando recap mensual {month:02d}/{year} (dry_run={args.dry_run})")
    res = publicar_mensual(month, year, dry_run=args.dry_run)
    if res.get("empty"):
        log.info("Sin trades para ese mes — skip")
        return
    if res.get("dry_run"):
        log.info(f"Stats: {json.dumps({k:v for k,v in res['stats'].items() if k!='top_pairs' and k!='categories'}, default=str)}")
    else:
        log.info(f"Resultados publicacion: {res.get('resultados', {})}")


if __name__ == "__main__":
    main()

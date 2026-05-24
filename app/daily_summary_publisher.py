# -*- coding: utf-8 -*-
"""
Publicador automático del resumen diario del Canal VIP BuySell365 Pro.

Destinos:
  1) Canal VIP Telegram (CHANNEL_ID)   — caption limpio, sin CTA
  2) Grupo gratis Telegram (GROUP_ID)  — caption con botón "UNIRME AL CANAL VIP"
  3) Chat privado admin (USER_ID_1)    — confirmación técnica
  4) Instagram Story                   — 24h
  5) Instagram Highlight "Resultados"  — permanente

Programado: 19:00 hora Andorra cada día.

Stats fuente: copier_stats.json (alimentado por signal_copier.py)

Uso:
  - Automático: scheduler en signal_copier.py dispara `publicar_resumen_diario()`
  - Manual:     python daily_summary_publisher.py [--dry-run] [--force]
"""
from __future__ import annotations

import os
import sys
import json
import time
import logging
import math
from pathlib import Path
from datetime import datetime
from typing import Optional

import pytz
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# FIX 2026-05-01: VERDAD SIEMPRE — usa stats_normalizer compartido
# para garantizar que daily, weekly y signal_copier usen los mismos numeros.
from stats_normalizer import compute_day_stats

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")


def _count_open_signals_at_close() -> int:
    """Cuenta senales aun abiertas al cierre del dia (publicadas al VIP).
    FIX 2026-05-19: Excluye orfanas MT5_Reinsert que nunca llegaron al canal
    (no tienen telegram_msg_id). Le da contexto al suscriptor de por que el
    recap puede mostrar 5 partials pero solo 1 full close — el resto sigue
    corriendo en BE."""
    try:
        open_file = BASE / "copier_open_signals.json"
        if not open_file.exists():
            return 0
        data = json.loads(open_file.read_text(encoding="utf-8"))
        return sum(1 for sdata in data.values() if sdata.get("telegram_msg_id"))
    except Exception:
        return 0

# ── Config ─────────────────────────────────────────────────────
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "0"))
GROUP_ID = os.environ.get("GROUP_ID", "").strip()
ADMIN_ID = int(os.environ.get("USER_ID_1", "0") or "0")
IG_USER = os.environ.get("IG_USERNAME", "")
IG_PASS = os.environ.get("IG_PASSWORD", "")

BOT_USERNAME = "Andoperandobot"
VIP_DEEPLINK = f"https://t.me/{BOT_USERNAME}?start=vip"

TZ = pytz.timezone("Europe/Andorra")
LOGO_PATH = BASE / "static" / "bull-logo.png"
STATS_PATH = BASE / "copier_stats.json"
OUT_DIR = BASE / "ig_images"
OUT_DIR.mkdir(exist_ok=True)
IG_SESSION = BASE / "ig_session.json"

# Estado de ejecución diaria (evita doble publicación)
STATE_PATH = BASE / "daily_summary_state.json"

log = logging.getLogger("daily_summary")
if not log.handlers:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [DAILY] %(message)s")

# ── Paleta (mismo estilo que whatsapp_status_promo) ───────────
W, H = 1080, 1920
BG_TOP = (4, 28, 46)
BG_MID = (8, 48, 66)
BG_BOT = (6, 62, 80)
WHITE = (255, 255, 255)
CYAN = (46, 232, 192)
CYAN_SOFT = (120, 220, 210)
GRAY = (210, 220, 230)
GOLD = (255, 204, 0)
GREEN = (0, 220, 120)
RED_SOFT = (220, 130, 130)


# ── Utilidades ────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(s: dict) -> None:
    STATE_PATH.write_text(json.dumps(s, indent=2), encoding="utf-8")


def _font(size: int, bold: bool = False):
    cs_bold = [
        # Windows
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        # Linux
        "/usr/share/fonts/truetype/lato/Lato-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    cs_regular = [
        # Windows
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        # Linux
        "/usr/share/fonts/truetype/lato/Lato-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    cs = cs_bold if bold else cs_regular
    for f in cs:
        if Path(f).exists():
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ── 1) Cálculo de stats ──────────────────────────────────────

def calcular_stats_hoy(fecha_str: Optional[str] = None) -> dict:
    """FIX 2026-05-01: VERDAD SIEMPRE.
    Lee copier_stats.json y calcula agregados REALES del dia con:
      - Categorias separadas (ORO/FOREX/INDICES/OIL/CRIPTO)
      - x10 GOLD consistente
      - Multi-TP grouping (senales unicas, no eventos)
      - Excluye Manual, MT5_Reinsert y blacklist
      - Net = TPs + parciales - SLs (NUNCA solo-ganancias)
    """
    if fecha_str is None:
        fecha_str = datetime.now(TZ).strftime("%d/%m/%Y")

    if not STATS_PATH.exists():
        return _stats_vacias(fecha_str)

    try:
        data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"No se pudo leer copier_stats.json: {e}")
        return _stats_vacias(fecha_str)

    trades = data.get("trades", [])
    ds = compute_day_stats(trades, fecha_str)

    if ds["trades_count"] == 0:
        return _stats_vacias(fecha_str)

    # Conteo de parciales por subtipo (para no romper schema de templates antiguos)
    hoy = [t for t in trades if t.get("fecha") == fecha_str]
    ch = sum(1 for t in hoy if t.get("result") == "close_half")
    cp = sum(1 for t in hoy if t.get("result") == "close_partial")
    fc = sum(1 for t in hoy if t.get("result") == "full_close")

    # Net por subcategoria (forex en pips, resto en pts)
    net_pts = 0.0
    net_pips = 0.0
    por_par: dict = {}
    for cat in ds["categories"]:
        if cat["name"] == "FOREX":
            net_pips += cat["net"]
        else:
            net_pts += cat["net"]

    # Top 3 pares: usar best/worst del normalizer + agregar el resto
    # Reconstruir from trades (consistente x10 GOLD)
    from stats_normalizer import normalize_pips
    for t in hoy:
        if t.get("source") in ("Manual", "MT5_Reinsert"):
            continue
        p, _cat = normalize_pips(t)
        par = t.get("pair_display", t.get("pair", "?"))
        result = t.get("result", "")
        if result == "tp" or (result in ("close_half", "close_partial", "full_close") and p > 0):
            por_par[par] = por_par.get(par, 0.0) + p
        elif result == "sl":
            por_par[par] = por_par.get(par, 0.0) - p

    top_pares = sorted(por_par.items(), key=lambda x: -x[1])[:3]

    return {
        "fecha": fecha_str,
        "senales_unicas": ds["tps_unique"] + ds["sls_unique"],
        "tp": ds["tps_unique"],            # senales unicas ganadoras (no eventos)
        "tp_events": ds["tps_events"],      # eventos individuales (multi-TPs)
        "sl": ds["sls_unique"],            # senales unicas perdedoras
        "sl_events": ds["sls_events"],
        "close_half": ch,
        "close_partial": cp,
        "full_close": fc,
        "parciales_total": ch + cp + fc,
        "net_pts": round(net_pts, 1),
        "net_pips": round(net_pips, 1),
        "net_total": round(ds["net_total"], 1),  # NET REAL (todo junto)
        "winrate": round(ds["wr_unique"], 1),     # WR honesto por senal unica
        "winrate_events": round(ds["wr_events"], 1),
        "top_pares": top_pares,
        "categories": ds["categories"],
    }


def _stats_vacias(fecha_str: str) -> dict:
    return {
        "fecha": fecha_str, "senales_unicas": 0,
        "tp": 0, "tp_events": 0, "sl": 0, "sl_events": 0,
        "close_half": 0, "close_partial": 0, "full_close": 0,
        "parciales_total": 0, "net_pts": 0.0, "net_pips": 0.0,
        "net_total": 0.0,
        "winrate": 0.0, "winrate_events": 0.0, "top_pares": [],
        "categories": [],
    }


# ── 2) Generación de imagen ──────────────────────────────────

def _build_bg():
    img = Image.new("RGB", (W, H), BG_TOP)
    d = ImageDraw.Draw(img)

    def lerp(a, b, t):
        return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    half = H // 2
    for y in range(half):
        d.line([(0, y), (W, y)], fill=lerp(BG_TOP, BG_MID, y/half))
    for y in range(half, H):
        d.line([(0, y), (W, y)], fill=lerp(BG_MID, BG_BOT, (y-half)/(H-half)))

    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    for cx, cy, rad, al in [
        (W//2 - 80, 300, 480, 55),
        (W//2 + 100, 1100, 560, 45),
        (W//2, H - 350, 640, 40),
    ]:
        for r in range(rad, rad - 200, -4):
            a = int(al * (r - (rad - 200)) / 200)
            hd.ellipse([cx - r, cy - r, cx + r, cy + r],
                       fill=(46, 232, 192, max(0, min(al, a))))
    halo = halo.filter(ImageFilter.GaussianBlur(radius=80))
    return Image.alpha_composite(img.convert("RGBA"), halo).convert("RGB")


def _rounded(d, xy, r, fill=None, outline=None, width=0):
    d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=width)


def _icon_check(d, cx, cy, r):
    pts = [(cx - r*0.45, cy + r*0.05), (cx - r*0.05, cy + r*0.4),
           (cx + r*0.5, cy - r*0.35)]
    d.line([pts[0], pts[1]], fill=WHITE, width=8)
    d.line([pts[1], pts[2]], fill=WHITE, width=8)


def _icon_cross(d, cx, cy, r):
    off = r * 0.4
    d.line([(cx-off, cy-off), (cx+off, cy+off)], fill=WHITE, width=8)
    d.line([(cx+off, cy-off), (cx-off, cy+off)], fill=WHITE, width=8)


def _icon_half(d, cx, cy, r):
    d.pieslice([cx - r*0.6, cy - r*0.6, cx + r*0.6, cy + r*0.6],
               start=-90, end=90, fill=WHITE)


def _icon_star(d, cx, cy, r):
    pts = []
    for i in range(10):
        ang = -math.pi/2 + i * math.pi/5
        rr = r*0.55 if i % 2 == 0 else r*0.25
        pts.append((cx + rr*math.cos(ang), cy + rr*math.sin(ang)))
    d.polygon(pts, fill=WHITE)


def _draw_crown(d, cx, cy, size=28):
    c = (4, 28, 46)
    d.rectangle([cx - size, cy + size*0.3, cx + size, cy + size*0.55], fill=c)
    for tri in [
        [(cx-size, cy+size*0.3), (cx-size*0.85, cy-size*0.6), (cx-size*0.55, cy+size*0.3)],
        [(cx-size*0.35, cy+size*0.3), (cx, cy-size*0.85), (cx+size*0.35, cy+size*0.3)],
        [(cx+size*0.55, cy+size*0.3), (cx+size*0.85, cy-size*0.6), (cx+size, cy+size*0.3)],
    ]:
        d.polygon(tri, fill=c)
    for x, y in [(cx-size*0.85, cy-size*0.6), (cx, cy-size*0.85), (cx+size*0.85, cy-size*0.6)]:
        d.ellipse([x-4, y-4, x+4, y+4], fill=c)


def _nombre_dia(dt: datetime) -> str:
    dias  = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
    meses = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
             "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    return f"{dias[dt.weekday()]} {dt.day} · {meses[dt.month-1]} · {dt.year}"


def generar_imagen(stats: dict) -> Path:
    """Genera la imagen 1080x1920 con los datos del día — versión mejorada 2026-05-06."""
    img = _build_bg()
    d = ImageDraw.Draw(img)

    net_total = stats.get("net_total", stats["net_pts"])
    net_pts   = stats["net_pts"]
    net_pips  = stats["net_pips"]
    sig        = "+" if net_total >= 0 else ""
    is_pos     = net_total >= 0

    # ── BARRA SUPERIOR de color (acento visual) ──
    bar_color = (0, 200, 110) if is_pos else (210, 60, 60)
    d.rectangle([0, 0, W, 12], fill=bar_color)

    # ── HEADER ──
    try:
        lg = Image.open(LOGO_PATH).convert("RGBA").resize((140, 140), Image.LANCZOS)
        img.paste(lg, (70, 35), lg)
    except Exception:
        pass
    d.text((240, 42), "BuySell365", fill=WHITE, font=_font(58, True))
    d.text((240, 108), "Pro  ·  Canal VIP", fill=CYAN, font=_font(38, True))
    d.text((240, 158), "DAILY SUMMARY", fill=GRAY, font=_font(22, True))

    # Fecha badge
    try:
        dt = datetime.strptime(stats["fecha"], "%d/%m/%Y").replace(tzinfo=TZ)
    except Exception:
        dt = datetime.now(TZ)
    dia_label = _nombre_dia(dt)
    badge_w = max(15 * len(dia_label) + 40, 300)
    _rounded(d, [70, 230, 70 + badge_w, 300], r=35, outline=CYAN, width=3)
    d.text((70 + badge_w // 2, 265), dia_label, fill=CYAN,
           font=_font(24, True), anchor="mm")

    # ── HERO: ganancia neta con fondo de color ──
    hero_bg   = (0, 60, 30) if is_pos else (60, 15, 15)
    hero_bord = (0, 210, 100) if is_pos else (210, 60, 60)
    hero_color = (0, 240, 120) if is_pos else (240, 80, 80)
    label_top  = "DAY NET RESULT"

    x0, y0, x1, y1 = 60, 330, W - 60, 700
    _rounded(d, [x0, y0, x1, y1], r=30, fill=hero_bg, outline=hero_bord, width=4)
    _rounded(d, [x0, y0, x1, y0 + 70], r=30, fill=(12, 60, 80) if is_pos else (60, 20, 20))
    d.text((W//2, y0 + 35), label_top, fill=hero_bord, font=_font(26, True), anchor="mm")

    hero_txt = f"{sig}{int(round(net_total))}"
    d.text((W//2, y0 + 165), hero_txt, fill=hero_color,
           font=_font(170, True), anchor="mm")
    d.text((W//2, y0 + 265), "NET PIPS / POINTS", fill=WHITE,
           font=_font(36, True), anchor="mm")

    # Breakdown por categoria
    cats = stats.get("categories", [])
    if len(cats) > 1:
        parts = [f"{c['name']} {'+' if c['net']>=0 else ''}{c['net']:.0f} {c['unit']}"
                 for c in cats]
        d.text((W//2, y0 + 315), "  ·  ".join(parts), fill=GRAY,
               font=_font(20), anchor="mm")

    # ── STATS GRID 2x2 — en español ──
    cards = [
        (GREEN,         _icon_check, str(stats["tp"]),              "WINNERS",         GREEN),
        ((200, 90, 90), _icon_cross, str(stats["sl"]),              "LOSERS",          RED_SOFT),
        (GOLD,          _icon_half,  str(stats["parciales_total"]), "PARTIAL CLOSES",  GOLD),
        (CYAN,          _icon_star,  f"{stats['winrate']:.0f}%",   "WIN RATE",        CYAN),
    ]
    card_w = (W - 60 - 60 - 30) // 2
    card_h = 195
    base_x, base_y, gap = 60, 740, 30
    for i, (ic_fill, ic_fn, big, lbl, num_color) in enumerate(cards):
        col, row = i % 2, i // 2
        x = base_x + col * (card_w + gap)
        y = base_y + row * (card_h + gap)
        _rounded(d, [x, y, x + card_w, y + card_h], r=22,
                 fill=(10, 40, 54), outline=(30, 90, 110), width=2)
        d.ellipse([x+21, y+21, x+69, y+69], fill=ic_fill)
        ic_fn(d, x + 45, y + 45, 24)
        d.text((x + card_w // 2, y + card_h // 2 + 10), big,
               fill=num_color, font=_font(64, True), anchor="mm")
        d.text((x + card_w // 2, y + card_h - 26), lbl,
               fill=GRAY, font=_font(18, True), anchor="mm")

    # ── BARRA DE WIN RATE visual ──
    wr_y = base_y + 2 * (card_h + gap) + 20
    wr_val = float(stats.get("winrate", 0))
    bar_x0, bar_x1 = 80, W - 80
    bar_yw = 18
    d.text((W//2, wr_y), f"GLOBAL WIN RATE: {wr_val:.0f}%",
           fill=CYAN, font=_font(26, True), anchor="mm")
    wr_y += 35
    # fondo gris de la barra
    _rounded(d, [bar_x0, wr_y, bar_x1, wr_y + bar_yw], r=9, fill=(30, 70, 85))
    # relleno proporcional
    fill_w = int((bar_x1 - bar_x0) * min(wr_val, 100) / 100)
    if fill_w > 0:
        bar_fill = (0, 200, 110) if wr_val >= 60 else (220, 160, 40) if wr_val >= 40 else (210, 60, 60)
        _rounded(d, [bar_x0, wr_y, bar_x0 + fill_w, wr_y + bar_yw], r=9, fill=bar_fill)
    wr_y += bar_yw + 15

    # ── MEJOR SEÑAL DEL DÍA ──
    best = stats.get("best")
    if best and best.get("pts", 0) > 0:
        bp    = best.get("pair", "ORO")
        bpts  = best.get("pts", 0)
        bunit = "pips" if best.get("category") == "ORO" else "pts"
        best_txt = f"  MEJOR SEÑAL:  {bp}  +{bpts:.0f} {bunit}  "
        _rounded(d, [80, wr_y, W - 80, wr_y + 68], r=18,
                 fill=(10, 55, 35), outline=(0, 200, 100), width=2)
        d.text((W//2, wr_y + 34), f"🏆 {best_txt}", fill=(0, 240, 130),
               font=_font(28, True), anchor="mm")
        wr_y += 88

    # ── TOP 3 ACTIVOS ──
    # FIX 2026-05-19: titulo dinamico segun cuantas categorias ganaron, y
    # filas vacias ya no se renderizan (antes se veia "🥉 — " en la 3a slot
    # cuando solo hubo 2 categorias activas en el dia).
    y_top = wr_y + 20
    top_pares = stats.get("top_pares", [])
    _n_top = min(len(top_pares), 3)
    if _n_top > 0:
        _title_top = f"TOP {_n_top} OF THE DAY" if _n_top < 3 else "TOP 3 OF THE DAY"
        d.text((W//2, y_top), _title_top,
               fill=CYAN, font=_font(30, True), anchor="ma")
    colores_medalla = [
        ((255, 195, 40), GOLD),
        ((190, 200, 215), (200, 220, 230)),
        ((200, 130, 80), (220, 160, 100)),
    ]
    y = y_top + 60
    for idx in range(_n_top):
        par, val = top_pares[idx]
        pts_txt = f"{'+' if val >= 0 else ''}{val:.0f} pts"
        med_col, num_col = colores_medalla[idx]
        _rounded(d, [80, y, W - 80, y + 90], r=18,
                 fill=(8, 42, 58), outline=(30, 90, 110), width=2)
        d.ellipse([100, y + 15, 158, y + 75], fill=med_col)
        d.text((129, y + 45), str(idx + 1), fill=(4, 28, 46),
               font=_font(34, True), anchor="mm")
        d.text((195, y + 45), par, fill=WHITE, font=_font(34, True), anchor="lm")
        d.text((W - 110, y + 45), pts_txt, fill=num_col,
               font=_font(36, True), anchor="rm")
        y += 105

    # ── FOOTER ──
    footer_y = y + 25
    d.line([(W//2 - 220, footer_y), (W//2 + 220, footer_y)], fill=CYAN, width=2)
    d.text((W//2, footer_y + 35), "Signals with exact Entry, TP and SL",
           fill=WHITE, font=_font(27, True), anchor="ma")
    d.text((W//2, footer_y + 68), "Professional management  ·  Total transparency",
           fill=CYAN_SOFT, font=_font(22), anchor="ma")

    # Branding Instagram
    d.text((W//2, footer_y + 105), "@buysell365pro  ·  t.me/" + BOT_USERNAME,
           fill=(80, 160, 140), font=_font(22, True), anchor="ma")

    # CTA
    btn_w, btn_h = 720, 95
    btn_x = (W - btn_w) // 2
    btn_y = footer_y + 140
    if btn_y + btn_h < H - 10:
        _rounded(d, [btn_x, btn_y, btn_x + btn_w, btn_y + btn_h], r=48, fill=CYAN)
        _draw_crown(d, btn_x + 90, btn_y + btn_h // 2, size=26)
        d.text((W//2 + 35, btn_y + btn_h//2), "JOIN VIP CHANNEL",
               fill=(4, 28, 46), font=_font(32, True), anchor="mm")

    # Guardar
    fecha_slug = stats["fecha"].replace("/", "")
    out = OUT_DIR / f"resumen_dia_{fecha_slug}.jpg"
    img.save(out, "JPEG", quality=95, optimize=True)
    log.info(f"[OK] Imagen resumen generada -> {out.name}")
    return out


# ── 3) Captions dinámicos ────────────────────────────────────

def _medallas_txt(top: list, sep: str = "\n") -> str:
    em = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (par, val) in enumerate(top[:3]):
        sig = "+" if val >= 0 else ""
        lines.append(f"{em[i]} {par} *({sig}{val:.0f} pts)*")
    return sep.join(lines) if lines else "_sin datos_"


def _caption_vip(s: dict) -> str:
    fecha = s["fecha"]
    # FIX 2026-05-01: VERDAD SIEMPRE — usar net_total y mostrar breakdown por categoria.
    # tp/sl son ahora SENALES UNICAS (no eventos individuales de TPs).
    net_total = s.get("net_total", s["net_pts"])
    sig_pts = "+" if net_total >= 0 else ""

    # Breakdown por categoria
    cats = s.get("categories", [])
    cat_lines = ""
    if len(cats) > 1:
        parts = []
        for c in cats:
            sgn = "+" if c["net"] >= 0 else ""
            parts.append(f"  {c['name']}: {sgn}{c['net']:.0f} {c['unit']}")
        cat_lines = "\n".join(parts) + "\n\n"

    tp_events_note = ""
    if s.get("tp_events", s["tp"]) > s["tp"]:
        tp_events_note = f"  ({s['tp_events']} TP levels hit across multi-target signals)\n"

    # FIX 2026-05-19: senales aun abiertas al cierre — da contexto al lector
    # cuando hay muchos partials secured pero pocos full closes (caso 19-may
    # ORO: 5 partials, 1 full close, varias en BE corriendo todavia).
    _open_now = _count_open_signals_at_close()
    open_line = (
        f"⏳ *{_open_now} signal{'s' if _open_now != 1 else ''} still running at close* (protected at BE / partial taken)\n"
        if _open_now > 0 else ""
    )

    # FIX 2026-04-28: caption en INGLES para audiencia internacional
    return (
        f"📊 *RECAP {fecha}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"✓ *{s['tp']} winning signals*\n"
        f"{tp_events_note}"
        f"● {s['sl']} losing signals\n"
        f"⚡ {s['parciales_total']} partial closes in profit\n"
        f"{open_line}"
        f"\n"
        f"🏆 *Win Rate: {s['winrate']:.0f}%*\n"
        f"📈 *{sig_pts}{net_total:.0f} points* net\n\n"
        f"{cat_lines}"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{_medallas_txt(s['top_pares'])}\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"Thanks for trading with us today 💎\n"
        f"_Full transparency · Professional management · SLs counted_"
    )


def _get_regalo_stats() -> dict:
    """Lee gift_tracker.json y devuelve info de señales regalo del día.
    FIX 2026-04-23: Para el resumen diario del grupo público."""
    tracker_path = BASE / "gift_tracker.json"
    try:
        if not tracker_path.exists():
            return {}
        data = json.loads(tracker_path.read_text(encoding="utf-8"))
        # Verificar que es del día de hoy
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        if data.get("date") != today:
            return {}
        return data
    except Exception:
        return {}


def _build_regalo_section(regalo: dict) -> str:
    """Construye la sección 🎁 para el caption del grupo público.
    FIX 2026-04-23: Muestra las señales regalo del día con sus resultados."""
    if not regalo:
        return ""

    gold_gifted = regalo.get("gold_gifted", False)
    other_gifted = regalo.get("other_gifted", False)

    if not gold_gifted and not other_gifted:
        return ""

    def _pair_display(pair: str) -> str:
        if not pair:
            return "—"
        p = pair.upper().replace("=X", "")
        # Formato legible
        if p in ("XAUUSD", "GOLD"):
            return "XAU/USD 🪙"
        if len(p) == 6:
            return f"{p[:3]}/{p[3:]}"
        return p

    def _result_emoji(result: str) -> str:
        # FIX 2026-04-28: textos en INGLES
        if result == "tp":
            return "✅ WINNER 🎯"
        elif result == "sl":
            return "❌ Stop Loss"
        else:
            return "⏳ Running..."

    lines = ["━━━━━━━━━━━━━━━━━━━", "🎁 *FREE SIGNALS OF THE DAY*\n"]

    if gold_gifted:
        pair_name = _pair_display(regalo.get("gold_pair") or "XAUUSD")
        result_txt = _result_emoji(regalo.get("gold_result"))
        lines.append(f"  {pair_name} — {result_txt}")

    if other_gifted:
        pair_name = _pair_display(regalo.get("other_pair") or "?")
        result_txt = _result_emoji(regalo.get("other_result"))
        lines.append(f"  {pair_name} — {result_txt}")

    # Resumen de efectividad
    resultados = []
    if gold_gifted and regalo.get("gold_result"):
        resultados.append(regalo.get("gold_result"))
    if other_gifted and regalo.get("other_result"):
        resultados.append(regalo.get("other_result"))

    if resultados:
        wins = sum(1 for r in resultados if r == "tp")
        total = len(resultados)
        pct = int(wins / total * 100)
        emoji = "🔥" if pct == 100 else "💪" if pct >= 50 else "📊"
        lines.append(f"\n{emoji} *{wins}/{total} free signals winners today* ({pct}% effectiveness)")

    return "\n".join(lines) + "\n"


def _caption_grupo(s: dict) -> str:
    # FIX 2026-05-01: VERDAD SIEMPRE — net_total real, conteo por senal unica.
    net_total = s.get("net_total", s["net_pts"])
    sig_pts = "+" if net_total >= 0 else ""
    # medallas en una linea corta
    top = s["top_pares"]
    em = ["🥇", "🥈", "🥉"]
    top_line = []
    for i, (par, val) in enumerate(top[:3]):
        sg = "+" if val >= 0 else ""
        top_line.append(f"{em[i]} {par}: {sg}{val:.0f} pts")

    # FIX 2026-04-23: Añadir sección de señales regalo si las hubo hoy
    regalo = _get_regalo_stats()
    regalo_section = _build_regalo_section(regalo)

    # FIX 2026-04-28: caption del grupo publico en INGLES
    # FIX 2026-05-24: eliminado bloque MT5/credentials (ya no trabajamos con MT5)
    return (
        f"🚀 *HERE'S HOW WE CLOSED THE DAY IN VIP* 📊\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"✓ *{s['tp']} winners*  ·  ● {s['sl']} losers\n"
        f"🏆 *Win Rate: {s['winrate']:.0f}%*\n"
        f"📈 *{sig_pts}{net_total:.0f} points* net\n\n"
        + "\n".join(top_line) + "\n\n"
        + regalo_section
        + f"━━━━━━━━━━━━━━━━━━━\n"
        f"Want signals *live* with exact Entry, TP and SL?\n\n"
        f"👇 *Join the VIP Channel*"
    )


def _caption_admin(s: dict, resultados: list) -> str:
    return (
        f"🔧 *[ADMIN]* Resumen del {s['fecha']} publicado\n\n"
        f"📊 Numeros:\n"
        f"  • Señales: {s['senales_unicas']}\n"
        f"  • TP: {s['tp']} · SL: {s['sl']}\n"
        f"  • Parciales: {s['parciales_total']}\n"
        f"  • Net: {s['net_pts']:+.1f} pts · {s['net_pips']:+.1f} pips\n"
        f"  • WR: {s['winrate']:.1f}%\n\n"
        f"📡 Destinos:\n"
        + "\n".join(f"  {'✅' if ok else '❌'} {name}" for name, ok in resultados)
    )


# ── 4) Publicación ───────────────────────────────────────────

def _tg_send_photo(chat_id, img_path: Path, caption: str,
                   reply_markup: Optional[dict] = None) -> Optional[int]:
    import requests
    if not TOKEN:
        log.warning("Sin TELEGRAM_TOKEN")
        return None
    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    with open(img_path, "rb") as f:
        files = {"photo": (img_path.name, f, "image/jpeg")}
        data = {"chat_id": str(chat_id), "caption": caption, "parse_mode": "Markdown"}
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)
        try:
            r = requests.post(url, data=data, files=files, timeout=60)
            if r.status_code == 200:
                return r.json().get("result", {}).get("message_id")
            log.warning(f"tg_send_photo {chat_id}: {r.status_code} {r.text[:100]}")
        except Exception as e:
            log.warning(f"tg_send_photo {chat_id}: {e}")
    return None


def _tg_send_message(chat_id, text: str, reply_markup: Optional[dict] = None) -> bool:
    """Envía mensaje de texto plano a Telegram."""
    import requests
    if not TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": str(chat_id), "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.status_code == 200
    except Exception as e:
        log.warning(f"tg_send_message {chat_id}: {e}")
        return False


def _load_today_trades_sorted() -> list:
    """Carga y ordena cronológicamente los trades de hoy desde copier_stats.json."""
    try:
        hoy = datetime.now(TZ).strftime("%d/%m/%Y")
        data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        trades = [t for t in data.get("trades", [])
                  if isinstance(t, dict) and t.get("fecha") == hoy]

        def _ts(t):
            return t.get("closed_at") or t.get("time") or 0

        return sorted(trades, key=_ts)
    except Exception as e:
        log.warning(f"_load_today_trades_sorted error: {e}")
        return []


def _build_text_vip(fecha: str) -> str:
    """VIP channel: chronological list grouped by asset, with streak and best trade highlighted."""
    trades = _load_today_trades_sorted()
    if not trades:
        return ""

    wins_pips = 0.0
    losses_pips = 0.0
    n_wins = 0
    n_losses = 0
    best_trade = None

    # Group by asset category
    from collections import defaultdict
    groups = defaultdict(list)
    asset_order = []

    for t in trades:
        pair      = t.get("pair_display") or t.get("pair", "?")
        direction = t.get("direction", "BUY")
        result    = t.get("result", "")
        pips      = float(t.get("pips_numeric") or t.get("pips") or 0)
        unit      = t.get("pips_unit", "pips")
        ts_open   = t.get("opened_at") or 0
        ts_close  = t.get("closed_at") or t.get("time") or 0
        hora      = datetime.fromtimestamp(ts_close, tz=TZ).strftime("%H:%M") if ts_close else "--:--"
        dir_emoji = "📈" if direction == "BUY" else "📉"

        # Duration
        dur_txt = ""
        if ts_open and ts_close and ts_close > ts_open:
            mins = int((ts_close - ts_open) / 60)
            dur_txt = f" `{mins}m`" if mins < 60 else f" `{mins//60}h{mins%60:02d}m`"

        # Classify asset
        p_up = pair.upper().replace("/","")
        if p_up in ("XAUUSD","GOLD","ORO"):
            asset = "GOLD"
        elif p_up in ("US30","US100","US500","NAS100","SP500","NDX","US100CASH","US500CASH"):
            asset = "INDICES"
        elif "BTC" in p_up or "ETH" in p_up or "XRP" in p_up or "SOL" in p_up or "BNB" in p_up:
            asset = "CRYPTO"
        elif "USD" in p_up or "EUR" in p_up or "GBP" in p_up or "JPY" in p_up or "CAD" in p_up:
            asset = "FOREX"
        else:
            asset = "OTHER"

        if asset not in asset_order:
            asset_order.append(asset)

        is_loss = result == "sl"
        if is_loss:
            losses_pips += pips
            n_losses += 1
            line = f"`{hora}` ❌ {dir_emoji} *{pair} {direction}*{dur_txt} — `-{pips:.0f} {unit}`"
        else:
            wins_pips += pips
            n_wins += 1
            r_emoji = "⭐" if (best_trade is None or pips > float(best_trade.get("pips_numeric") or best_trade.get("pips") or 0)) else ("🎯" if result == "tp" else "💰")
            line = f"`{hora}` {r_emoji} {dir_emoji} *{pair} {direction}*{dur_txt} — `+{pips:.0f} {unit}`"
            if best_trade is None or pips > float(best_trade.get("pips_numeric") or best_trade.get("pips") or 0):
                best_trade = t

        groups[asset].append(line)

    # Fix ⭐ — only mark the actual best
    # Rebuild lines marking best correctly
    asset_headers = {"GOLD": "🥇 *GOLD*", "INDICES": "📊 *INDICES*",
                     "FOREX": "💱 *FOREX*", "CRYPTO": "🪙 *CRYPTO*", "OTHER": "📌 *OTHER*"}
    best_pips = float(best_trade.get("pips_numeric") or best_trade.get("pips") or 0) if best_trade else 0
    best_pair = (best_trade.get("pair_display") or best_trade.get("pair","?")) if best_trade else ""
    best_ts   = (best_trade.get("closed_at") or best_trade.get("time") or 0) if best_trade else 0
    best_hora = datetime.fromtimestamp(best_ts, tz=TZ).strftime("%H:%M") if best_ts else "--:--"
    best_unit = best_trade.get("pips_unit","pips") if best_trade else "pips"

    # Build grouped block
    sections = []
    for asset in asset_order:
        lines = groups[asset]
        header = asset_headers.get(asset, f"📌 *{asset}*")
        sections.append(header + "\n" + "\n".join(lines))
    trades_block = "\n\n".join(sections)

    # Winning streak
    streak = 0
    max_streak = 0
    for t in trades:
        if t.get("result") != "sl":
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    net = wins_pips - losses_pips
    net_sign  = "+" if net >= 0 else ""
    net_emoji = "🟢" if net >= 0 else "🔴"
    wr = round(n_wins / (n_wins + n_losses) * 100) if (n_wins + n_losses) > 0 else 0

    streak_line = f"🔥 *Max winning streak: {max_streak} in a row*\n" if max_streak >= 3 else ""
    best_line   = f"⭐ *Best signal: {best_pair} `+{best_pips:.0f} {best_unit}`*\n" if best_trade else ""

    # FIX 2026-05-06 (Capa C): unificar conteo de winners con las otras captions.
    # El loop arriba cuenta cada TP1/TP2/TP3/TP4 como un winner separado → 18 winners.
    # Las otras captions ("HERE'S HOW WE CLOSED", "DAY CLOSED") cuentan señales
    # únicas via compute_day_stats → 6 winners. Inconsistencia confunde a clientes
    # VIP (un mensaje dice 78% WR, el siguiente 54% para el mismo día).
    # Solucion: usar tps_unique/sls_unique de compute_day_stats Y mostrar tambien
    # el conteo de TP levels en una nota — preserva ambas vistas.
    try:
        _ds_unique = compute_day_stats(trades, fecha)
        _n_wins_unique = _ds_unique.get("tps_unique", n_wins)
        _n_losses_unique = _ds_unique.get("sls_unique", n_losses)
        _wr_unique = round(_ds_unique.get("wr_unique", wr))
        _tp_events = _ds_unique.get("tps_events", n_wins)
    except Exception:
        # Fallback al conteo crudo si compute_day_stats falla
        _n_wins_unique = n_wins
        _n_losses_unique = n_losses
        _wr_unique = wr
        _tp_events = n_wins
    _tp_events_note = ""
    if _tp_events > _n_wins_unique:
        _tp_events_note = f"   _({_tp_events} TP levels hit across multi-target signals)_\n"

    return (
        f"📊 *DAILY RECAP*\n"
        f"📅 {fecha}  •  BuySell365 Pro\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
        f"{trades_block}\n\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        f"{best_line}"
        f"{streak_line}"
        f"🎯  Winners: *{_n_wins_unique}*    ❌  Losers: *{_n_losses_unique}*    📊  WR: *{_wr_unique}%*\n"
        f"{_tp_events_note}\n"
        f"✅  `+{wins_pips:.0f}` pips won\n"
        f"🔻  `-{losses_pips:.0f}` pips lost\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        f"{net_emoji}  *DAY NET:  {net_sign}{net:.0f} pips/pts*\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
    )


def _build_text_group(s: dict, fecha: str) -> str:
    """Public group: English summary with example trade, day comparison and CTA."""
    net_total   = s.get("net_total", s.get("net_pts", 0))
    net_sign    = "+" if net_total >= 0 else ""
    net_emoji   = "🟢" if net_total >= 0 else "🔴"
    n_wins      = s.get("tp", 0)
    n_losses    = s.get("sl", 0)
    wr          = s.get("winrate", 0)

    # Best signal as real example
    example_line = ""
    best = s.get("best")
    if best and best.get("pts", 0) > 0:
        bp    = best.get("pair", "?")
        bpts  = best.get("pts", 0)
        bunit = "pips" if best.get("category") == "ORO" else "pts"
        example_line = (
            f"📌 *Real example today:*\n"
            f"   {bp} — Entry ✅ → TP hit → *+{bpts:.0f} {bunit}*\n\n"
        )

    # Yesterday comparison (from copier_stats if available, otherwise skip)
    compare_line = ""
    try:
        data = json.loads(STATS_PATH.read_text(encoding="utf-8"))
        from stats_normalizer import compute_day_stats as _cds
        yesterday_trades = [t for t in data.get("trades", [])
                            if isinstance(t, dict) and t.get("fecha") == _prev_day(fecha)]
        if yesterday_trades:
            yd = _cds(yesterday_trades, _prev_day(fecha))
            yd_net = round(yd.get("net_total", 0))
            yd_sign = "+" if yd_net >= 0 else ""
            arrow = "📈" if net_total > yd_net else "📉"
            compare_line = f"{arrow} *Yesterday: {yd_sign}{yd_net} pts  →  Today: {net_sign}{net_total:.0f} pts*\n\n"
    except Exception:
        pass

    return (
        f"🔥 *DAY CLOSED — {fecha}*\n"
        f"BuySell365 Pro\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
        f"🎯  *{n_wins} winning signals*\n"
        f"❌  *{n_losses} losses*\n"
        f"📊  *Win Rate: {wr:.0f}%*\n\n"
        f"{example_line}"
        f"{compare_line}"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
        f"{net_emoji}  *RESULT: {net_sign}{net_total:.0f} pips/pts*\n"
        f"▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
        f"💬 Next signal coming soon...\n\n"
        f"💎 Want real-time access to every signal?\n"
        f"📩 Message the bot: @Andoperandobot\n"
        f"👤 Or contact the admin: @BuySell365traiding"
    )


def _prev_day(fecha_dmy: str) -> str:
    """Returns the previous day in DD/MM/YYYY format."""
    try:
        from datetime import timedelta
        dt = datetime.strptime(fecha_dmy, "%d/%m/%Y")
        return (dt - timedelta(days=1)).strftime("%d/%m/%Y")
    except Exception:
        return ""


def publicar_telegram(img: Path, s: dict) -> list:
    """Publica el resumen diario al canal VIP y al grupo publico.

    Fix 2026-05-10: ANTES enviaba 2 mensajes por canal (foto+caption +
    texto largo separado) — duplicacion confusa. Ahora solo envia el
    mensaje principal con foto + caption corto (resumen visual).
    El texto detallado con timeline (build_text_vip / build_text_group)
    queda en el codigo por si en el futuro se quiere reactivar, pero
    no se envia automaticamente.
    """
    resultados = []
    # fecha = s.get("fecha", datetime.now(TZ).strftime("%d/%m/%Y"))  # ya no usado

    # Canal VIP — solo imagen + caption (sin texto detallado adicional)
    mid = _tg_send_photo(CHANNEL_ID, img, _caption_vip(s))
    resultados.append(("Canal VIP", bool(mid)))
    time.sleep(2)

    # Grupo gratis — SOLO si dia neto positivo.
    # Regla dura feedback_grupo_solo_ganancias.md: el grupo publico NUNCA
    # recibe SL ni dias negativos. Mismo guard que IG (linea ~985).
    if GROUP_ID:
        try:
            net_total = float(s.get("net_total", s.get("net_pts", 0)) or 0)
        except Exception:
            net_total = 0.0
        if net_total < 0:
            log.info(f"Grupo skip — dia neto negativo (net_total={net_total:+.0f}) — regla solo-ganancias")
            resultados.append(("Grupo gratis", False))
        else:
            btn = {"inline_keyboard": [[
                {"text": "👑 JOIN VIP CHANNEL", "url": VIP_DEEPLINK}
            ]]}
            mid = _tg_send_photo(GROUP_ID, img, _caption_grupo(s), btn)
            resultados.append(("Grupo gratis", bool(mid)))
            time.sleep(2)
    else:
        resultados.append(("Grupo gratis", False))

    return resultados


def publicar_instagram(img: Path, stats: Optional[dict] = None) -> list:
    # FIX 2026-04-22: Regla IG SOLO-GANANCIAS (feedback_instagram_solo_ganancias.md).
    # FIX 2026-05-01: usar net_total (todo junto) para la decision de skip IG.
    if stats is not None:
        try:
            net_total = float(stats.get("net_total", stats.get("net_pts", 0)) or 0)
            if net_total < 0:
                log.info(f"IG skip — dia neto negativo (net_total={net_total:+.0f})")
                return [("IG Story", False), ("IG Highlight", False)]
        except Exception:
            pass

    resultados = []
    try:
        from instagrapi import Client
    except ImportError:
        log.warning("instagrapi no instalado — saltando IG")
        return [("IG Story", False), ("IG Highlight", False)]

    if not IG_USER or not IG_PASS:
        return [("IG Story", False), ("IG Highlight", False)]

    cl = Client()
    # Reutilizar sesión para evitar bloqueos
    try:
        if IG_SESSION.exists():
            cl.load_settings(str(IG_SESSION))
            cl.get_timeline_feed()
        else:
            cl.login(IG_USER, IG_PASS)
            cl.dump_settings(str(IG_SESSION))
    except Exception as e:
        log.warning(f"IG login fallo: {e}")
        return [("IG Story", False), ("IG Highlight", False)]

    # Story
    story_pk = None
    try:
        story = cl.photo_upload_to_story(str(img))
        story_pk = str(story.pk) if story else None
        resultados.append(("IG Story", True))
    except Exception as e:
        log.warning(f"IG Story fallo: {e}")
        resultados.append(("IG Story", False))

    # Highlight "Resultados"
    if story_pk:
        time.sleep(3)
        try:
            highlights = cl.user_highlights(cl.user_id)
            resultados_hl = next((h for h in highlights
                                 if h.title.lower() in ("resultados", "results")), None)
            if resultados_hl:
                cl.highlight_add_stories(resultados_hl.pk, [story_pk])
            else:
                cl.highlight_create("Resultados", [story_pk])
            resultados.append(("IG Highlight", True))
        except Exception as e:
            log.warning(f"IG Highlight fallo: {e}")
            resultados.append(("IG Highlight", False))
    else:
        resultados.append(("IG Highlight", False))

    return resultados


# ── 5) Orquestador ──────────────────────────────────────────

def publicar_resumen_diario(dry_run: bool = False,
                            force: bool = False,
                            fecha_str: Optional[str] = None) -> bool:
    """Genera y publica el resumen del día.

    dry_run:   genera la imagen pero no publica
    force:     publica aunque ya se haya publicado hoy
    fecha_str: dd/mm/YYYY para publicar un día concreto (backfill). None = hoy.
    """
    s = calcular_stats_hoy(fecha_str)
    fecha_slug = s["fecha"].replace("/", "-")
    state = _load_state()

    # Evitar doble publicación en el mismo día
    if not force and state.get("last_publish_fecha") == s["fecha"]:
        log.info(f"Ya se publicó resumen para {s['fecha']} — skip (usa --force para forzar)")
        return False

    # No publicar si no hubo actividad (ej. sábado-domingo)
    eventos = s["tp"] + s["sl"] + s["parciales_total"]
    if eventos == 0 and not force:
        log.info(f"Sin eventos hoy ({s['fecha']}) — no se publica")
        state["last_check_fecha"] = s["fecha"]
        _save_state(state)
        return False

    # FIX 2026-05-24: REGLA SOLO-POSITIVO GLOBAL — si el día neto es negativo
    # no se publica NINGUN recap (ni canal VIP, ni grupo gratis, ni Instagram).
    # Antes solo se silenciaba grupo + IG; el VIP recibia el recap negativo.
    # Decision usuario: no queremos publicar reportes en negativo a ningun sitio.
    try:
        net_total_dia = float(s.get("net_total", s.get("net_pts", 0)) or 0)
    except Exception:
        net_total_dia = 0.0
    if net_total_dia < 0 and not force:
        log.info(f"📕 Dia neto negativo ({net_total_dia:+.0f} pts) — recap NO publicado a ningun destino (regla solo-positivo)")
        state["last_check_fecha"] = s["fecha"]
        state["last_skip_reason"] = f"net_negative_{net_total_dia:+.0f}"
        _save_state(state)
        return False

    log.info(f"Generando imagen resumen {s['fecha']} — "
             f"{s['tp']} TP / {s['sl']} SL / {s['net_pts']:+.0f} pts")
    img = generar_imagen(s)

    if dry_run:
        log.info(f"[DRY-RUN] No se publica. Imagen: {img}")
        return True

    # Telegram (canal VIP + grupo gratis)
    tg_res = publicar_telegram(img, s)

    # Instagram (story + highlight) — pasa stats para respetar regla solo-ganancias
    ig_res = publicar_instagram(img, stats=s)

    resultados = tg_res + ig_res

    # Admin privado — DESACTIVADO 2026-05-12 por decision del usuario.
    # El resumen ya sale a canal VIP/grupo/IG; no necesita confirmacion
    # duplicada al chat privado.
    if ADMIN_ID:
        log.info(f"Resumen {s['fecha']} publicado — notificacion admin desactivada por usuario")

    # Guardar estado
    state["last_publish_fecha"] = s["fecha"]
    state["last_publish_ts"] = int(time.time())
    state["last_results"] = {name: ok for name, ok in resultados}
    _save_state(state)

    exitos = sum(1 for _, ok in resultados if ok)
    log.info(f"[OK] Resumen {s['fecha']} publicado — {exitos}/{len(resultados)} destinos")
    return True


# ── CLI ──────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Solo generar imagen, sin publicar")
    parser.add_argument("--force", action="store_true",
                        help="Publicar aunque ya se haya publicado hoy")
    parser.add_argument("--fecha", default=None,
                        help="Fecha dd/mm/YYYY a publicar (backfill)")
    args = parser.parse_args()
    ok = publicar_resumen_diario(dry_run=args.dry_run, force=args.force, fecha_str=args.fecha)
    print("Resultado:", "OK" if ok else "Sin eventos / ya publicado")
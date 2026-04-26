"""
BuySell365 — Publisher Resumen Semanal
=======================================
Cada viernes a las 19:00 (Andorra) genera y publica el resumen de los últimos
7 días (sábado pasado → viernes hoy) en:
  - Canal VIP Telegram (sin caja de venta — mensaje de gratitud)
  - Grupo público Telegram (con CTA agresivo al VIP)
  - Instagram Feed (post + hashtags)
  - Instagram Stories (3 slides secuencia: hook, prueba, CTA)

Invocación:
  from weekly_summary_publisher import publish_weekly_summary
  publish_weekly_summary()   # usa la fecha actual

Test manual:
  python -X utf8 weekly_summary_publisher.py
"""
import os
import time
import json
import logging
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

log = logging.getLogger("weekly_summary")

# ── Config ──
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
GROUP_ID = os.getenv("GROUP_ID", "@BUYSELL_365_24_7").strip()
IG_ENABLED = bool(os.getenv("IG_USERNAME") and os.getenv("IG_PASSWORD"))

STATS_FILE = BASE_DIR / "copier_stats.json"
IMAGES_DIR = BASE_DIR / "ig_images"
IMAGES_DIR.mkdir(exist_ok=True)

# ── Paleta de marca ──
BG_TOP = (6, 10, 18)
BG_BOT = (15, 22, 32)
GOLD = (255, 199, 44)
GOLD_L = (255, 220, 100)
GOLD_D = (184, 134, 11)
GREEN = (16, 210, 108)
GREEN_D = (22, 100, 60)
WHITE = (255, 255, 255)
GRAY = (156, 163, 175)
GRAY_D = (75, 85, 99)
CARD = (22, 28, 42)
CARD_H = (30, 38, 54)

SEGOE = r"C:\Windows\Fonts\segoeui.ttf"
SEGOE_B = r"C:\Windows\Fonts\segoeuib.ttf"
SEGOE_EMOJI = r"C:\Windows\Fonts\seguiemj.ttf"


def _font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype(SEGOE_B if bold else SEGOE, size)
    except Exception:
        return ImageFont.load_default()


def _efont(size: int):
    try:
        return ImageFont.truetype(SEGOE_EMOJI, size)
    except Exception:
        return _font(size)


def _gradient_bg(img: Image.Image, c1=BG_TOP, c2=BG_BOT):
    draw = ImageDraw.Draw(img)
    W, H = img.size
    for y in range(H):
        t = y / H
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))


def _starry_dots(img: Image.Image, count=80, seed=42):
    draw = ImageDraw.Draw(img)
    W, H = img.size
    rng = random.Random(seed)
    for _ in range(count):
        x, y = rng.randint(0, W), rng.randint(0, H)
        op = rng.randint(20, 60)
        draw.ellipse([(x, y), (x + 2, y + 2)], fill=(op, op + 10, op + 20))


def _load_week_stats(fecha_viernes: datetime) -> dict:
    """Devuelve stats de los 7 días terminando en el viernes dado (sábado → viernes)."""
    if not STATS_FILE.exists():
        return {"days": [], "total_tp": 0, "total_sl": 0, "total_pts": 0, "wr": 0.0}

    with open(STATS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    trades = data.get("trades", [])

    # FIX 2026-04-26: dias en INGLES
    dias_config = [
        ("SATURDAY",  "🌴", fecha_viernes - timedelta(days=6)),
        ("SUNDAY",    "☀️", fecha_viernes - timedelta(days=5)),
        ("MONDAY",    "💵", fecha_viernes - timedelta(days=4)),
        ("TUESDAY",   "🚀", fecha_viernes - timedelta(days=3)),
        ("WEDNESDAY", "⚡", fecha_viernes - timedelta(days=2)),
        ("THURSDAY",  "🔥", fecha_viernes - timedelta(days=1)),
        ("FRIDAY",    "⭐", fecha_viernes),
    ]

    days = []
    total_tp = total_sl = 0
    total_pts = 0.0
    best_day = None

    for name, emoji, dt in dias_config:
        fecha_str = dt.strftime("%d/%m/%Y")
        day_trades = [t for t in trades if t.get("fecha") == fecha_str]
        tps = [t for t in day_trades if t.get("result") == "tp"]
        sls = [t for t in day_trades if t.get("result") == "sl"]
        partials = [t for t in day_trades
                    if t.get("result") in ("close_half", "close_partial", "full_close")
                    and t.get("pips_numeric", 0) > 0]
        pts = sum(t.get("pips_numeric", 0) for t in tps) + sum(t.get("pips_numeric", 0) for t in partials)

        days.append({
            "name": name,
            "emoji": emoji,
            "fecha_corta": dt.strftime("%d %b").upper(),
            "fecha": fecha_str,
            "pts": pts,
            "tps": len(tps),
            "sls": len(sls),
        })
        total_tp += len(tps)
        total_sl += len(sls)
        total_pts += pts
        if best_day is None or pts > best_day["pts"]:
            best_day = days[-1]

    wr = (100 * total_tp / (total_tp + total_sl)) if (total_tp + total_sl) > 0 else 0.0
    return {
        "days": days,
        "total_tp": total_tp,
        "total_sl": total_sl,
        "total_pts": total_pts,
        "wr": wr,
        "best_day": best_day,
        "fecha_inicio": dias_config[0][2].strftime("%d"),
        "fecha_fin": dias_config[-1][2].strftime("%d %B %Y").upper(),
    }


def _draw_emoji(draw, pos, em, size, anchor="mm"):
    try:
        draw.text(pos, em, font=_efont(size), anchor=anchor, embedded_color=True)
    except Exception:
        draw.text(pos, em, font=_font(size), anchor=anchor)


def _render_day_rows(draw, W: int, y0: int, days: list, row_h: int = 76):
    """Pinta las filas del desglose diario."""
    for i, d in enumerate(days):
        y = y0 + i * row_h
        is_today = d["name"] == "FRIDAY" and d["pts"] < 50  # poor Friday
        card_col = CARD_H if is_today else CARD
        pts_col = GRAY if is_today else GREEN
        accent = GRAY_D if is_today else GREEN

        draw.rounded_rectangle([(50, y), (W - 50, y + 66)], radius=13, fill=card_col)
        draw.rectangle([(50, y), (62, y + 66)], fill=accent)
        _draw_emoji(draw, (110, y + 33), d["emoji"], 40)
        draw.text((165, y + 22), d["name"], fill=WHITE, font=_font(24, True), anchor="lm")
        draw.text((165, y + 48), d["fecha_corta"], fill=GRAY, font=_font(16), anchor="lm")
        draw.text((W - 85, y + 38), "pts", fill=GRAY, font=_font(20), anchor="rm")
        pts_str = f"+{d['pts']:.0f}" if d["pts"] >= 0 else f"{d['pts']:.0f}"
        draw.text((W - 145, y + 33), pts_str, fill=pts_col, font=_font(40, True), anchor="rm")


def generate_summary_image(stats: dict, variant: str = "publico") -> Path:
    """Genera la imagen 1080x1350 del resumen semanal.
    variant: 'publico' (con CTA al VIP) o 'vip' (sin caja de venta, con agradecimiento)
    """
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), BG_TOP)
    _gradient_bg(img)
    _starry_dots(img)
    draw = ImageDraw.Draw(img)

    # HEADER
    draw.rectangle([(0, 0), (W, 190)], fill=(3, 6, 12))
    for x in range(60, W - 60):
        t = (x - 60) / (W - 120)
        c = tuple(int(GOLD_D[j] + (GOLD[j] - GOLD_D[j]) * (t if t < 0.5 else 1 - t) * 2) for j in range(3))
        draw.line([(x, 187), (x, 190)], fill=c)

    _draw_emoji(draw, (85, 82), "🚀", 78)
    _draw_emoji(draw, (W - 85, 82), "💰", 78)
    draw.text((W // 2, 70), "BUYSELL365", fill=GOLD, font=_font(56, True), anchor="mm")
    draw.text((W // 2, 115), "PRO", fill=WHITE, font=_font(32, True), anchor="mm")
    draw.text((W // 2, 158), "W E E K L Y   R E C A P", fill=GRAY, font=_font(18, True), anchor="mm")

    # DATE — FIX 2026-04-26: en INGLES
    fecha_label = f"WEEK {stats['fecha_inicio']} — {stats['fecha_fin']}"
    draw.text((W // 2, 220), fecha_label, fill=GOLD_L, font=_font(24, True), anchor="mm")

    # CONTEXT MESSAGE
    box_y = 260
    draw.rounded_rectangle([(50, box_y), (W - 50, box_y + 120)], radius=18, fill=CARD, outline=GOLD, width=2)
    _draw_emoji(draw, (100, box_y + 62), "💎", 48)
    _draw_emoji(draw, (W - 100, box_y + 62), "🏆", 48)
    if variant == "vip":
        draw.text((W // 2, box_y + 38), "THANK YOU VIP FAMILY", fill=GOLD, font=_font(28, True), anchor="mm")
        draw.text((W // 2, box_y + 78), "This week TOGETHER we got:", fill=WHITE, font=_font(22), anchor="mm")
    else:
        draw.text((W // 2, box_y + 38), "Look what we delivered", fill=GOLD, font=_font(28, True), anchor="mm")
        draw.text((W // 2, box_y + 78), "this week to the VIP channel:", fill=WHITE, font=_font(22), anchor="mm")

    # DESGLOSE 7 DÍAS
    _render_day_rows(draw, W, 410, stats["days"], row_h=72)

    # TOTAL SEMANAL (posicionado después de 7 filas)
    total_y = 410 + 7 * 72 + 20  # = ~934
    for yi in range(total_y, total_y + 160):
        t = (yi - total_y) / 160
        c = (int(GREEN_D[0] * (1 - t * 0.3)), int(GREEN_D[1] * (1 - t * 0.3)), int(GREEN_D[2] * (1 - t * 0.3)))
        draw.line([(50, yi), (W - 50, yi)], fill=c)
    draw.rounded_rectangle([(50, total_y), (W - 50, total_y + 160)], radius=20, outline=GREEN, width=4)
    _draw_emoji(draw, (140, total_y + 85), "💰", 72)
    _draw_emoji(draw, (W - 140, total_y + 85), "💰", 72)
    # FIX 2026-04-26: total y CTA en INGLES
    draw.text((W // 2, total_y + 28), "WEEK TOTAL", fill=WHITE, font=_font(20, True), anchor="mm")
    draw.text((W // 2, total_y + 82), f"+{stats['total_pts']:,.0f}", fill=GREEN, font=_font(76, True), anchor="mm")
    draw.text((W // 2, total_y + 122), "POINTS GAINED", fill=GREEN, font=_font(20, True), anchor="mm")
    draw.text((W // 2, total_y + 148),
              f"{stats['total_tp']} TPs hit  ·  {stats['wr']:.1f}% Win Rate",
              fill=GRAY, font=_font(17), anchor="mm")

    # PROMO OR THANKS
    promo_y = total_y + 180
    if variant == "vip":
        # Loyalty message
        draw.rounded_rectangle([(40, promo_y), (W - 40, promo_y + 120)], radius=20,
                                fill=(14, 40, 22), outline=GREEN, width=2)
        _draw_emoji(draw, (90, promo_y + 60), "🙏", 48)
        _draw_emoji(draw, (W - 90, promo_y + 60), "💎", 48)
        draw.text((W // 2, promo_y + 38), "Not luck — system",
                  fill=GREEN, font=_font(26, True), anchor="mm")
        draw.text((W // 2, promo_y + 78), "Enjoy the weekend · See you Monday 🌴",
                  fill=WHITE, font=_font(20), anchor="mm")
    else:
        # CTA to VIP
        draw.rounded_rectangle([(30, promo_y), (W - 30, promo_y + 130)], radius=22,
                                fill=(50, 36, 14), outline=GOLD, width=3)
        _draw_emoji(draw, (85, promo_y + 55), "🔥", 52)
        _draw_emoji(draw, (W - 85, promo_y + 55), "🔥", 52)
        draw.text((W // 2, promo_y + 40), "HOW MUCH DID YOU MAKE?", fill=GOLD, font=_font(30, True), anchor="mm")
        draw.text((W // 2, promo_y + 82), f"VIPs made +{stats['total_pts']:,.0f} pts",
                  fill=GOLD_L, font=_font(22, True), anchor="mm")
        btn_y0 = promo_y + 105
    # CTA button only for public group
    if variant != "vip":
        bt_y = H - 110
        draw.rounded_rectangle([(80, bt_y), (W - 80, bt_y + 55)], radius=14, fill=GOLD)
        draw.text((W // 2, bt_y + 28), "JOIN THE VIP → @Andoperandobot",
                  fill=(5, 8, 15), font=_font(22, True), anchor="mm")

    # FOOTER
    draw.text((W // 2, H - 25), "@Andoperandobot  ·  buysell365.pro",
              fill=GRAY, font=_font(17), anchor="mm")

    out = IMAGES_DIR / f"weekly_{variant}_{datetime.now().strftime('%Y%m%d')}.jpg"
    img.save(str(out), "JPEG", quality=95)
    return out


def generate_story_slides(stats: dict) -> List[Path]:
    """Genera 3 slides 1080x1920 para Instagram Stories."""
    W, H = 1080, 1920
    slides = []

    # ═══ SLIDE 1: HOOK — número gigante ═══
    img = Image.new("RGB", (W, H), BG_TOP)
    _gradient_bg(img)
    _starry_dots(img, count=120, seed=10)
    d = ImageDraw.Draw(img)
    # FIX 2026-04-26: Story slide 1 en INGLES
    _draw_emoji(d, (W // 2, 340), "🔥", 150)
    d.text((W // 2, 540), "THIS WEEK", fill=WHITE, font=_font(70, True), anchor="mm")
    d.text((W // 2, 620), "IN THE VIP CHANNEL", fill=GRAY, font=_font(40), anchor="mm")
    d.text((W // 2, 920), f"+{stats['total_pts']:,.0f}", fill=GREEN, font=_font(240, True), anchor="mm")
    d.text((W // 2, 1080), "POINTS GAINED", fill=GREEN, font=_font(50, True), anchor="mm")
    d.text((W // 2, 1240), f"{stats['total_tp']} TPs  ·  {stats['wr']:.0f}% Win Rate",
           fill=GOLD_L, font=_font(38), anchor="mm")
    _draw_emoji(d, (W // 2 - 230, 1480), "💰", 120)
    _draw_emoji(d, (W // 2 + 230, 1480), "💰", 120)
    d.text((W // 2, 1680), "Swipe to see the details", fill=GRAY, font=_font(32), anchor="mm")
    _draw_emoji(d, (W // 2, 1770), "👉", 70)
    s1 = IMAGES_DIR / f"story1_hook_{datetime.now().strftime('%Y%m%d')}.jpg"
    img.save(str(s1), "JPEG", quality=95)
    slides.append(s1)

    # ═══ SLIDE 2: DESGLOSE 7 DÍAS ═══
    img = Image.new("RGB", (W, H), BG_TOP)
    _gradient_bg(img)
    _starry_dots(img, count=100, seed=20)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 120), "DÍA A DÍA", fill=GOLD, font=_font(70, True), anchor="mm")
    d.text((W // 2, 200), f"Semana {stats['fecha_inicio']} — {stats['fecha_fin']}",
           fill=GRAY, font=_font(32), anchor="mm")
    # Filas grandes
    row_h = 170
    y0 = 290
    for i, day in enumerate(stats["days"]):
        y = y0 + i * row_h
        is_weak = day["pts"] < 50
        pcol = GRAY if is_weak else GREEN
        accent = GRAY_D if is_weak else GREEN
        d.rounded_rectangle([(60, y), (W - 60, y + 150)], radius=20, fill=CARD)
        d.rectangle([(60, y), (80, y + 150)], fill=accent)
        _draw_emoji(d, (160, y + 75), day["emoji"], 70)
        d.text((260, y + 55), day["name"], fill=WHITE, font=_font(48, True), anchor="lm")
        d.text((260, y + 105), day["fecha_corta"], fill=GRAY, font=_font(30), anchor="lm")
        d.text((W - 100, y + 85), "pts", fill=GRAY, font=_font(36), anchor="rm")
        pts_str = f"+{day['pts']:.0f}" if day["pts"] >= 0 else f"{day['pts']:.0f}"
        d.text((W - 200, y + 75), pts_str, fill=pcol, font=_font(72, True), anchor="rm")
    s2 = IMAGES_DIR / f"story2_desglose_{datetime.now().strftime('%Y%m%d')}.jpg"
    img.save(str(s2), "JPEG", quality=95)
    slides.append(s2)

    # ═══ SLIDE 3: CTA ═══
    img = Image.new("RGB", (W, H), BG_TOP)
    _gradient_bg(img)
    _starry_dots(img, count=120, seed=30)
    d = ImageDraw.Draw(img)
    _draw_emoji(d, (W // 2, 350), "🎁", 180)
    d.text((W // 2, 600), "¿LA PRÓXIMA SEMANA", fill=WHITE, font=_font(60, True), anchor="mm")
    d.text((W // 2, 680), "TAMBIÉN TE LA PIERDES?", fill=GOLD, font=_font(60, True), anchor="mm")
    # Precio/oferta
    d.rounded_rectangle([(100, 830), (W - 100, 1120)], radius=30, fill=(50, 36, 14), outline=GOLD, width=4)
    d.text((W // 2, 880), "CANAL VIP", fill=GOLD, font=_font(45, True), anchor="mm")
    d.text((W // 2, 960), "Cada señal en tiempo real", fill=WHITE, font=_font(36), anchor="mm")
    d.text((W // 2, 1010), "Entry · TP · SL claros", fill=WHITE, font=_font(36), anchor="mm")
    d.text((W // 2, 1060), "Resultados reales como este", fill=WHITE, font=_font(36), anchor="mm")
    # Botón enorme
    d.rounded_rectangle([(100, 1250), (W - 100, 1400)], radius=30, fill=GOLD)
    d.text((W // 2, 1325), "ÚNETE AHORA", fill=(5, 8, 15), font=_font(80, True), anchor="mm")
    _draw_emoji(d, (W // 2, 1500), "👇", 90)
    d.text((W // 2, 1620), "@Andoperandobot", fill=GOLD_L, font=_font(52, True), anchor="mm")
    d.text((W // 2, 1710), "Toca el enlace ↓", fill=GRAY, font=_font(34), anchor="mm")
    s3 = IMAGES_DIR / f"story3_cta_{datetime.now().strftime('%Y%m%d')}.jpg"
    img.save(str(s3), "JPEG", quality=95)
    slides.append(s3)

    return slides


def build_caption_publico(stats: dict) -> str:
    lines = [
        "🔥 *AQUÍ ESTÁ LO QUE TE PERDISTE ESTA SEMANA*",
        "",
        f"💎 +{stats['total_pts']:,.0f} puntos ganados en 7 días",
        f"🎯 {stats['total_tp']} TPs alcanzados · {stats['wr']:.1f}% Win Rate",
        "",
    ]
    for d in stats["days"]:
        pts = f"+{d['pts']:.0f}" if d['pts'] >= 0 else f"{d['pts']:.0f}"
        lines.append(f"{d['emoji']} {d['name']:10s} {pts} pts")
    lines += [
        "",
        "Los VIPs recibieron CADA señal en tiempo real.",
        "Tú leíste este resumen.",
        "Diferencia: miles de puntos.",
        "",
        "🎁 Únete HOY → @Andoperandobot",
    ]
    return "\n".join(lines)


def build_caption_vip(stats: dict) -> str:
    best = stats.get("best_day") or {}
    lines = [
        "💎 *GRACIAS FAMILIA VIP* 💎",
        "",
        "Esta semana JUNTOS conseguimos:",
        "",
    ]
    for d in stats["days"]:
        pts = f"+{d['pts']:.0f}" if d['pts'] >= 0 else f"{d['pts']:.0f}"
        lines.append(f"{d['emoji']} {d['name']:10s} {pts} pts")
    lines += [
        "",
        f"🏆 TOTAL: +{stats['total_pts']:,.0f} pts · {stats['total_tp']} TPs · {stats['wr']:.1f}% WR",
    ]
    if best:
        lines.append(f"🥇 Mejor día: {best['name']} (+{best['pts']:.0f} pts)")
    lines += [
        "",
        "No es suerte. Es sistema, disciplina y confianza.",
        "",
        "Disfruten el finde 🌴",
        "El lunes volvemos con todo 💪",
    ]
    return "\n".join(lines)


def build_caption_instagram(stats: dict) -> str:
    lines = [
        f"💎 Cerramos la semana en VERDE 🟢",
        "",
        f"+{stats['total_pts']:,.0f} pts en 7 días de trading",
        f"{stats['total_tp']} TPs alcanzados · {stats['wr']:.1f}% Win Rate",
        "",
        "Señales de Oro · NAS100 · US30 · Forex · BTC",
        "Entregadas en tiempo real al canal VIP.",
        "",
        "Resultados reales. Transparencia total.",
        "",
        "🔗 Link en bio para unirte al VIP",
        "",
        "#trading #forex #xauusd #oro #nas100 #us30",
        "#bitcoin #señalesforex #tradersvip #daytrading",
        "#spain #andorra #traders #investment",
    ]
    return "\n".join(lines)


def _send_telegram_photo(chat_id, photo_path: Path, caption: str) -> Optional[int]:
    """Envía foto a un chat de Telegram con caption Markdown."""
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption[:1020], "parse_mode": "Markdown"},
                files={"photo": (photo_path.name, f.read(), "image/jpeg")},
                timeout=30,
            )
        if resp.status_code == 200:
            return resp.json().get("result", {}).get("message_id")
        log.warning(f"Telegram sendPhoto {chat_id} fallo: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log.warning(f"Telegram sendPhoto error: {e}")
    return None


def publish_weekly_summary(dry_run: bool = False) -> dict:
    """Genera todas las imágenes y publica en canal + grupo + IG feed + IG stories.
    Retorna dict con status de cada publicación.
    """
    log.info("📊 Iniciando publicación resumen semanal...")

    # 1. Stats (hasta hoy viernes)
    today = datetime.now()
    stats = _load_week_stats(today)
    log.info(f"📊 Stats: +{stats['total_pts']:.0f} pts · {stats['total_tp']} TPs · WR {stats['wr']:.1f}%")

    # 2. Generar imágenes
    img_publico = generate_summary_image(stats, variant="publico")
    img_vip = generate_summary_image(stats, variant="vip")
    story_slides = generate_story_slides(stats)
    log.info(f"📸 Imágenes generadas: publico, vip, 3 stories")

    # 3. Captions
    cap_pub = build_caption_publico(stats)
    cap_vip = build_caption_vip(stats)
    cap_ig = build_caption_instagram(stats)

    result = {"stats": stats, "dry_run": dry_run,
              "imgs": {"publico": str(img_publico), "vip": str(img_vip),
                       "stories": [str(s) for s in story_slides]}}

    if dry_run:
        log.info("🧪 DRY RUN — no se publica nada")
        return result

    # 4. Publicar Canal VIP (sin CTA)
    if CHANNEL_ID:
        mid = _send_telegram_photo(CHANNEL_ID, img_vip, cap_vip)
        result["channel_msg_id"] = mid
        log.info(f"✅ Canal VIP: msg_id={mid}")

    # 5. Publicar Grupo público (con CTA)
    if GROUP_ID:
        mid = _send_telegram_photo(GROUP_ID, img_publico, cap_pub)
        result["group_msg_id"] = mid
        log.info(f"✅ Grupo: msg_id={mid}")

    # 6. Instagram Feed + Stories
    # FIX 2026-04-24: Circuit breaker anti-spam-lock de IG
    _ig_lock_file = BASE_DIR / ".ig_feed_locked_until.txt"
    _ig_feed_locked = False
    if _ig_lock_file.exists():
        try:
            _locked_ts = float(_ig_lock_file.read_text().strip())
            if time.time() < _locked_ts:
                _ig_feed_locked = True
                _hours_left = (_locked_ts - time.time()) / 3600
                log.warning(f"⛔ IG Feed en cooldown {_hours_left:.1f}h (anti-spam-lock) — skip feed, stories OK")
        except Exception:
            pass

    if IG_ENABLED:
        try:
            import instagram_poster as ig
            cl = ig._get_client()
            if cl:
                # ─ FEED POST ─ (solo si NO está en cooldown)
                if not _ig_feed_locked:
                    try:
                        cl.photo_upload(str(img_publico), cap_ig)
                        result["ig_feed"] = "ok"
                        log.info("✅ Instagram Feed publicado")
                    except Exception as e_feed:
                        _err_str = str(e_feed).lower()
                        log.warning(f"IG Feed fallo: {e_feed}")
                        result["ig_feed_error"] = str(e_feed)
                        # Si IG detecto spam → ACTIVAR cooldown 48h (IG suele liberar en 24-48h)
                        if any(k in _err_str for k in ("feedback_required", "is_spam", "try again later", "rate", "policy")):
                            _unlock_at = time.time() + (48 * 3600)
                            try:
                                _ig_lock_file.write_text(str(_unlock_at))
                                log.warning(f"⛔ IG Feed LOCKED 48h por spam detection — se reanuda {datetime.fromtimestamp(_unlock_at).strftime('%Y-%m-%d %H:%M')}")
                            except Exception:
                                pass
                else:
                    result["ig_feed"] = "skipped_cooldown"

                # ─ STORIES ─ (siempre, IG es más permisivo con stories)
                stories_ok = 0
                for s in story_slides:
                    try:
                        cl.photo_upload_to_story(str(s))
                        stories_ok += 1
                        time.sleep(3)  # delay humanizado entre stories
                    except Exception as e_s:
                        log.warning(f"IG Story {s.name} fallo: {e_s}")
                result["ig_stories"] = stories_ok
                log.info(f"✅ Instagram Stories: {stories_ok}/{len(story_slides)} slides")
            else:
                log.warning("Instagram cliente no disponible")
                result["ig_error"] = "no client"
        except Exception as e:
            log.warning(f"instagram_poster import fallo: {e}")
            result["ig_error"] = str(e)

    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dry = "--live" not in sys.argv
    if dry:
        print("🧪 Modo DRY-RUN (usa --live para publicar de verdad)")
    result = publish_weekly_summary(dry_run=dry)
    import json as _j
    # Imprimir solo paths sin dump completo de stats
    print(f"\nImágenes generadas:")
    for k, v in result["imgs"].items():
        if isinstance(v, list):
            for i, p in enumerate(v):
                print(f"  {k}[{i}]: {p}")
        else:
            print(f"  {k}: {v}")
    print(f"\nStats: +{result['stats']['total_pts']:.0f} pts · {result['stats']['total_tp']} TPs · WR {result['stats']['wr']:.1f}%")

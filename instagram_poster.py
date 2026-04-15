"""
BuySell365 — Instagram Auto-Poster
====================================
Genera imagenes profesionales y publica automaticamente en Instagram:
- TPs alcanzados (celebraciones)
- Resumen diario de resultados
- Nuevas senales destacadas

Usa instagrapi para la API de Instagram y Pillow para generar imagenes.
Credenciales en .env: IG_USERNAME, IG_PASSWORD
"""

import os
import json
import logging
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv(Path(__file__).parent / ".env")

log = logging.getLogger("instagram_poster")

# ── Config ────────────────────────────────────────────────────
IG_USERNAME = os.getenv("IG_USERNAME", "")
IG_PASSWORD = os.getenv("IG_PASSWORD", "")
IG_ENABLED = bool(IG_USERNAME and IG_PASSWORD)

IMAGES_DIR = Path(__file__).parent / "ig_images"
IMAGES_DIR.mkdir(exist_ok=True)

IG_SESSION_FILE = Path(__file__).parent / "ig_session.json"

# ── Colores del branding ──────────────────────────────────────
COLOR_BG = (13, 17, 23)          # Fondo oscuro (estilo trading)
COLOR_GREEN = (0, 200, 83)       # Verde profit
COLOR_RED = (255, 59, 48)        # Rojo loss
COLOR_GOLD = (255, 204, 0)       # Dorado destacado
COLOR_WHITE = (255, 255, 255)
COLOR_GRAY = (140, 148, 160)
COLOR_ACCENT = (59, 130, 246)    # Azul BuySell365
COLOR_CARD_BG = (22, 27, 34)     # Fondo tarjeta

# ── Dimensiones Instagram (1080x1080 cuadrado) ───────────────
IMG_W, IMG_H = 1080, 1080

# ── Lock para evitar publicaciones simultaneas ────────────────
_ig_lock = threading.Lock()

# ── Cliente Instagram (singleton) ─────────────────────────────
_ig_client = None


def _get_client():
    """Obtiene o crea el cliente de Instagram con session caching.
    Incluye delays para evitar que Instagram detecte actividad automatizada."""
    global _ig_client
    if _ig_client is not None:
        return _ig_client

    if not IG_ENABLED:
        log.debug("Instagram deshabilitado (sin IG_USERNAME/IG_PASSWORD en .env)")
        return None

    try:
        from instagrapi import Client
        cl = Client()
        # Delays entre requests para parecer humano
        cl.delay_range = [2, 5]

        # Intentar cargar sesion guardada
        if IG_SESSION_FILE.exists():
            try:
                cl.load_settings(IG_SESSION_FILE)
                cl.login(IG_USERNAME, IG_PASSWORD)
                _ig_client = cl
                log.info("Instagram: sesion restaurada OK")
                return cl
            except Exception:
                log.debug("Sesion guardada expirada, haciendo login fresco")

        # Login fresco
        cl.login(IG_USERNAME, IG_PASSWORD)
        cl.dump_settings(IG_SESSION_FILE)
        _ig_client = cl
        log.info("Instagram: login exitoso")
        return cl

    except Exception as e:
        log.warning(f"Instagram login fallido: {e}")
        return None


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Busca una fuente del sistema. Fallback a default."""
    font_names = [
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    if bold:
        font_names = [
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
        ]
    for f in font_names:
        try:
            return ImageFont.truetype(f, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_rounded_rect(draw: ImageDraw.Draw, xy, radius, fill, outline=None, width=0):
    """Dibuja un rectangulo con esquinas redondeadas."""
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _draw_gradient_bg(img: Image.Image, color_top: tuple, color_bottom: tuple):
    """Dibuja un gradiente vertical de fondo."""
    for y in range(IMG_H):
        ratio = y / IMG_H
        r = int(color_top[0] + (color_bottom[0] - color_top[0]) * ratio)
        g = int(color_top[1] + (color_bottom[1] - color_top[1]) * ratio)
        b = int(color_top[2] + (color_bottom[2] - color_top[2]) * ratio)
        for x in range(IMG_W):
            img.putpixel((x, y), (r, g, b))


def _draw_gradient_rect(draw: ImageDraw.Draw, img: Image.Image, xy, color_top, color_bottom, radius=0):
    """Dibuja un rectangulo con gradiente vertical y esquinas redondeadas."""
    x1, y1, x2, y2 = xy
    # Crear imagen temporal con gradiente
    for y in range(y1, y2):
        ratio = (y - y1) / max(y2 - y1, 1)
        r = int(color_top[0] + (color_bottom[0] - color_top[0]) * ratio)
        g = int(color_top[1] + (color_bottom[1] - color_top[1]) * ratio)
        b = int(color_top[2] + (color_bottom[2] - color_top[2]) * ratio)
        draw.line([(x1, y), (x2, y)], fill=(r, g, b))
    # Si tiene radius, enmascarar esquinas con el fondo
    if radius > 0:
        mask = Image.new("L", img.size, 255)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rectangle([0, 0, IMG_W, IMG_H], fill=0)
        mask_draw.rounded_rectangle(xy, radius=radius, fill=255)
        bg = Image.new("RGB", img.size, COLOR_BG)
        img.paste(Image.composite(img, bg, mask))


def _fmt_price(v: float) -> str:
    """Formatea precio segun magnitud."""
    if v < 10:
        return f"{v:.5f}"
    if v < 100:
        return f"{v:.3f}"
    return f"{v:.2f}"


def _generate_tp_image(pair: str, direction: str, entry: float, tp: float,
                       pips: str, source: str = "") -> Path:
    """Genera imagen de celebraci\u00f3n de TP — SIN precios (marketing only)."""
    img = Image.new("RGB", (IMG_W, IMG_H), COLOR_BG)
    draw = ImageDraw.Draw(img)

    _draw_gradient_bg(img, (8, 12, 18), (18, 24, 32))
    draw = ImageDraw.Draw(img)

    # Borde superior verde
    for y in range(8):
        alpha = 1.0 - (y / 8)
        c = int(200 * alpha)
        draw.line([(0, y), (IMG_W, y)], fill=(0, c, int(83 * alpha)))

    font_brand = _get_font(36, bold=True)
    font_title = _get_font(56, bold=True)
    font_big = _get_font(90, bold=True)
    font_value = _get_font(38, bold=True)
    font_small = _get_font(24)
    font_pips = _get_font(110, bold=True)
    font_cta = _get_font(28, bold=True)
    font_msg = _get_font(30)

    # Brand
    draw.text((IMG_W // 2, 40), "BUYSELL365 PRO", fill=COLOR_WHITE,
              font=font_brand, anchor="mt")
    draw.line([(390, 80), (690, 80)], fill=COLOR_ACCENT, width=2)

    # TP ALCANZADO banner
    _draw_rounded_rect(draw, [100, 110, IMG_W - 100, 210], radius=16,
                       fill=COLOR_GREEN)
    _draw_rounded_rect(draw, [104, 113, IMG_W - 104, 165], radius=14,
                       fill=(30, 230, 110))
    draw.text((IMG_W // 2, 160), "TP ALCANZADO", fill=(10, 15, 10),
              font=font_title, anchor="mm")

    # Par grande (protagonista)
    is_buy = direction.upper() in ("BUY", "COMPRA")
    dir_color = COLOR_GREEN if is_buy else COLOR_RED
    dir_label = "COMPRA" if is_buy else "VENTA"
    dir_icon = "\u25b2" if is_buy else "\u25bc"

    draw.text((IMG_W // 2, 260), pair.upper(), fill=COLOR_WHITE,
              font=font_big, anchor="mt")
    draw.text((IMG_W // 2, 365), f"{dir_icon} {dir_label}", fill=dir_color,
              font=font_value, anchor="mt")

    # ── PIPS ganados — grande y centrado ──
    _draw_rounded_rect(draw, [80, 440, IMG_W - 80, 650], radius=24,
                       fill=(10, 45, 20))
    _draw_rounded_rect(draw, [82, 442, IMG_W - 82, 500], radius=22,
                       fill=(15, 55, 28))
    _draw_rounded_rect(draw, [80, 440, IMG_W - 80, 650], radius=24,
                       fill=None, outline=(0, 150, 60), width=2)
    draw.text((IMG_W // 2, 545), pips, fill=COLOR_GREEN,
              font=font_pips, anchor="mm")

    # ── Mensaje de marketing ──
    _draw_rounded_rect(draw, [80, 695, IMG_W - 80, 835], radius=16,
                       fill=COLOR_CARD_BG, outline=(40, 46, 54), width=1)
    draw.text((IMG_W // 2, 725), "Otra se\u00f1al exitosa de nuestro",
              fill=COLOR_GRAY, font=font_small, anchor="mt")
    draw.text((IMG_W // 2, 760), "Canal VIP", fill=COLOR_GOLD,
              font=font_value, anchor="mt")
    draw.text((IMG_W // 2, 805), "\u00bfQuieres recibir estas se\u00f1ales?",
              fill=COLOR_WHITE, font=font_small, anchor="mt")

    # CTA
    _draw_rounded_rect(draw, [150, 875, IMG_W - 150, 940], radius=28,
                       fill=COLOR_ACCENT)
    draw.text((IMG_W // 2, 907), "\u00danete al VIP \u2014 Link en bio",
              fill=COLOR_WHITE, font=font_cta, anchor="mm")

    # Fecha
    draw.text((IMG_W // 2, 960), datetime.now().strftime("%d/%m/%Y  %H:%M"),
              fill=(60, 65, 75), font=_get_font(20), anchor="mt")

    # Footer
    draw.line([(150, 990), (IMG_W - 150, 990)], fill=(40, 46, 54), width=1)
    draw.text((IMG_W // 2, 1010), "buysell365.pro  \u2022  Resultados reales",
              fill=COLOR_GRAY, font=font_small, anchor="mt")
    draw.text((IMG_W // 2, 1042), "Publicamos TODOS los resultados \u2014 Transparencia total",
              fill=(70, 75, 85), font=_get_font(18), anchor="mt")

    filename = f"tp_{pair.replace('/', '')}_{int(time.time())}.jpg"
    filepath = IMAGES_DIR / filename
    img.save(filepath, "JPEG", quality=95)
    log.info(f"Instagram: imagen TP generada -> {filename}")
    return filepath


def _generate_daily_summary_image(stats: dict) -> Path:
    """Genera imagen de resumen diario — version premium."""
    img = Image.new("RGB", (IMG_W, IMG_H), COLOR_BG)
    draw = ImageDraw.Draw(img)

    # Gradiente de fondo
    _draw_gradient_bg(img, (8, 10, 18), (16, 20, 30))
    draw = ImageDraw.Draw(img)

    font_brand = _get_font(36, bold=True)
    font_title = _get_font(44, bold=True)
    font_wr = _get_font(80, bold=True)
    font_label = _get_font(26)
    font_value = _get_font(40, bold=True)
    font_small = _get_font(22)
    font_cta = _get_font(26, bold=True)
    font_asset = _get_font(24)

    # Borde superior dorado
    for y in range(6):
        alpha = 1.0 - (y / 6)
        draw.line([(0, y), (IMG_W, y)], fill=(int(255 * alpha), int(204 * alpha), 0))

    # Brand
    draw.text((IMG_W // 2, 30), "BUYSELL365 PRO", fill=COLOR_WHITE,
              font=font_brand, anchor="mt")
    draw.line([(390, 68), (690, 68)], fill=COLOR_GOLD, width=2)

    # Titulo
    fecha = stats.get("fecha", datetime.now().strftime("%d/%m/%Y"))
    draw.text((IMG_W // 2, 88), f"RESUMEN DEL D\u00cdA", fill=COLOR_WHITE,
              font=font_title, anchor="mt")
    draw.text((IMG_W // 2, 138), fecha, fill=COLOR_GRAY,
              font=font_label, anchor="mt")

    # ── Win Rate con barra de progreso ──
    wr = stats.get("wr", 0)
    wr_color = COLOR_GREEN if wr >= 50 else (COLOR_GOLD if wr >= 35 else COLOR_RED)

    draw.text((IMG_W // 2, 190), f"{wr:.0f}%", fill=wr_color,
              font=font_wr, anchor="mt")
    draw.text((IMG_W // 2, 278), "WIN RATE", fill=COLOR_GRAY,
              font=font_label, anchor="mt")

    # Barra de progreso visual
    bar_x, bar_y = 150, 318
    bar_w, bar_h = IMG_W - 300, 16
    _draw_rounded_rect(draw, [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h],
                       radius=8, fill=(35, 40, 50))
    fill_w = max(int(bar_w * wr / 100), 10)
    _draw_rounded_rect(draw, [bar_x, bar_y, bar_x + fill_w, bar_y + bar_h],
                       radius=8, fill=wr_color)

    # ── Stats cards (2x2 grid) ──
    cards = [
        ("TPs Alcanzados", str(stats.get("tps", 0)), COLOR_GREEN),
        ("SLs Tocados", str(stats.get("sls", 0)), COLOR_RED),
        ("Pips Netos", f"{stats.get('pips_netos', 0):+.0f}",
         COLOR_GREEN if stats.get("pips_netos", 0) >= 0 else COLOR_RED),
        ("Se\u00f1ales", str(stats.get("total", 0)), COLOR_ACCENT),
    ]

    card_w, card_h = 440, 120
    start_x, start_y = 80, 365
    gap = 40

    for i, (label, value, color) in enumerate(cards):
        col = i % 2
        row = i // 2
        x = start_x + col * (card_w + gap)
        y = start_y + row * (card_h + gap)

        _draw_rounded_rect(draw, [x, y, x + card_w, y + card_h], radius=12,
                           fill=COLOR_CARD_BG, outline=(35, 40, 50), width=1)
        draw.text((x + card_w // 2, y + 30), label, fill=COLOR_GRAY,
                  font=font_small, anchor="mt")
        draw.text((x + card_w // 2, y + 68), value, fill=color,
                  font=font_value, anchor="mt")

    # ── Mejor y peor se\u00f1al ──
    y_pos = 700
    mejor = stats.get("mejor")
    peor = stats.get("peor")

    if mejor:
        _draw_rounded_rect(draw, [80, y_pos, IMG_W // 2 - 20, y_pos + 85], radius=12,
                           fill=(12, 45, 18), outline=(0, 120, 50), width=1)
        draw.text((80 + (IMG_W // 2 - 100) // 2, y_pos + 12), "Mejor Se\u00f1al", fill=COLOR_GREEN,
                  font=font_small, anchor="mt")
        draw.text((80 + (IMG_W // 2 - 100) // 2, y_pos + 48), mejor, fill=COLOR_WHITE,
                  font=font_asset, anchor="mt")

    if peor:
        _draw_rounded_rect(draw, [IMG_W // 2 + 20, y_pos, IMG_W - 80, y_pos + 85], radius=12,
                           fill=(45, 12, 12), outline=(120, 40, 40), width=1)
        draw.text((IMG_W // 2 + 20 + (IMG_W // 2 - 100) // 2, y_pos + 12), "Peor Se\u00f1al", fill=COLOR_RED,
                  font=font_small, anchor="mt")
        draw.text((IMG_W // 2 + 20 + (IMG_W // 2 - 100) // 2, y_pos + 48), peor, fill=COLOR_WHITE,
                  font=font_asset, anchor="mt")

    # ── CTA ──
    _draw_rounded_rect(draw, [200, 830, IMG_W - 200, 885], radius=25,
                       fill=COLOR_ACCENT)
    draw.text((IMG_W // 2, 857), "\u00danete al VIP \u2014 Link en bio",
              fill=COLOR_WHITE, font=font_cta, anchor="mm")

    # ── Footer ──
    draw.line([(150, 920), (IMG_W - 150, 920)], fill=(40, 46, 54), width=1)
    draw.text((IMG_W // 2, 940), "buysell365.pro  \u2022  Resultados Verificados", fill=COLOR_GRAY,
              font=font_small, anchor="mt")
    draw.text((IMG_W // 2, 975), "Publicamos TODOS los resultados \u2014 Transparencia total", fill=COLOR_GOLD,
              font=_get_font(20), anchor="mt")
    draw.text((IMG_W // 2, 1010), "Los d\u00edas malos tambi\u00e9n se muestran. Sin filtros.", fill=(80, 85, 95),
              font=_get_font(18), anchor="mt")

    filename = f"daily_{fecha.replace('/', '-')}_{int(time.time())}.jpg"
    filepath = IMAGES_DIR / filename
    img.save(filepath, "JPEG", quality=95)
    log.info(f"Instagram: imagen resumen diario generada -> {filename}")
    return filepath


def _generate_new_signal_image(pair: str, direction: str, entry: float,
                                tp: float, sl: float, source: str = "") -> Path:
    """Genera imagen de nueva se\u00f1al publicada — version premium."""
    img = Image.new("RGB", (IMG_W, IMG_H), COLOR_BG)
    _draw_gradient_bg(img, (8, 12, 18), (18, 24, 32))
    draw = ImageDraw.Draw(img)

    font_brand = _get_font(36, bold=True)
    font_title = _get_font(48, bold=True)
    font_big = _get_font(72, bold=True)
    font_label = _get_font(28)
    font_value = _get_font(36, bold=True)
    font_small = _get_font(22)
    font_cta = _get_font(26, bold=True)

    is_buy = direction.upper() in ("BUY", "COMPRA")
    dir_color = COLOR_GREEN if is_buy else COLOR_RED
    dir_label = "COMPRA" if is_buy else "VENTA"

    # Borde superior
    for y in range(8):
        alpha = 1.0 - (y / 8)
        r = int(dir_color[0] * alpha)
        g = int(dir_color[1] * alpha)
        b = int(dir_color[2] * alpha)
        draw.line([(0, y), (IMG_W, y)], fill=(r, g, b))

    # Brand
    draw.text((IMG_W // 2, 35), "BUYSELL365 PRO", fill=COLOR_WHITE,
              font=font_brand, anchor="mt")
    draw.line([(390, 73), (690, 73)], fill=dir_color, width=2)

    # NUEVA SE\u00d1AL banner
    _draw_rounded_rect(draw, [100, 95, IMG_W - 100, 180], radius=16, fill=dir_color)
    _draw_rounded_rect(draw, [104, 98, IMG_W - 104, 142], radius=14,
                       fill=(min(dir_color[0] + 30, 255), min(dir_color[1] + 30, 255), min(dir_color[2] + 30, 255)))
    draw.text((IMG_W // 2, 137), "NUEVA SE\u00d1AL", fill=(10, 15, 10),
              font=font_title, anchor="mm")

    # Par
    draw.text((IMG_W // 2, 220), pair.upper(), fill=COLOR_WHITE,
              font=font_big, anchor="mt")

    # Direccion
    dir_icon = "\u25b2" if is_buy else "\u25bc"
    draw.text((IMG_W // 2, 305), f"{dir_icon} {dir_label}", fill=dir_color,
              font=font_value, anchor="mt")

    # ── Card con niveles ──
    _draw_rounded_rect(draw, [60, 370, IMG_W - 60, 700], radius=16,
                       fill=COLOR_CARD_BG, outline=(35, 40, 50), width=1)

    levels = [
        ("ENTRADA", _fmt_price(entry), COLOR_WHITE, 410),
        ("TAKE PROFIT", _fmt_price(tp), COLOR_GREEN, 510),
        ("STOP LOSS", _fmt_price(sl), COLOR_RED, 610),
    ]

    for label, value, color, y in levels:
        draw.text((120, y), label, fill=COLOR_GRAY, font=font_label)
        draw.text((IMG_W - 120, y), value, fill=color, font=font_value, anchor="rt")
        # Linea separadora sutil
        if y < 610:
            draw.line([(100, y + 70), (IMG_W - 100, y + 70)], fill=(30, 35, 42), width=1)

    # R:R ratio
    if entry > 0 and tp > 0 and sl > 0:
        reward = abs(tp - entry)
        risk = abs(sl - entry)
        if risk > 0:
            rrr = reward / risk
            _draw_rounded_rect(draw, [320, 730, IMG_W - 320, 795], radius=14,
                               fill=COLOR_CARD_BG, outline=COLOR_GOLD, width=2)
            draw.text((IMG_W // 2, 762), f"R:R  {rrr:.1f}:1", fill=COLOR_GOLD,
                      font=font_value, anchor="mm")

    # Source y fecha
    y_info = 825
    if source:
        draw.text((IMG_W // 2, y_info), f"Fuente: {source}", fill=COLOR_GRAY,
                  font=font_small, anchor="mt")
        y_info += 32
    draw.text((IMG_W // 2, y_info), datetime.now().strftime("%d/%m/%Y  %H:%M"),
              fill=(70, 75, 85), font=font_small, anchor="mt")

    # CTA
    _draw_rounded_rect(draw, [200, 895, IMG_W - 200, 948], radius=25,
                       fill=COLOR_ACCENT)
    draw.text((IMG_W // 2, 921), "\u00danete al VIP \u2014 Link en bio",
              fill=COLOR_WHITE, font=font_cta, anchor="mm")

    # Footer
    draw.line([(150, 975), (IMG_W - 150, 975)], fill=(40, 46, 54), width=1)
    draw.text((IMG_W // 2, 995), "buysell365.pro  \u2022  Se\u00f1ales de Trading", fill=COLOR_GRAY,
              font=font_small, anchor="mt")
    draw.text((IMG_W // 2, 1025), "Resultados reales \u2014 Transparencia total", fill=(80, 85, 95),
              font=_get_font(18), anchor="mt")

    filename = f"signal_{pair.replace('/', '')}_{int(time.time())}.jpg"
    filepath = IMAGES_DIR / filename
    img.save(filepath, "JPEG", quality=95)
    log.info(f"Instagram: imagen se\u00f1al generada -> {filename}")
    return filepath


def _generate_carousel_slides(brand_image_path: str = None) -> list:
    """Genera un carrusel de inauguraci\u00f3n/presentaci\u00f3n de BuySell365 Pro (5 slides)."""
    slides = []

    font_brand = _get_font(44, bold=True)
    font_title = _get_font(52, bold=True)
    font_subtitle = _get_font(36, bold=True)
    font_body = _get_font(30)
    font_big = _get_font(68, bold=True)
    font_small = _get_font(24)
    font_cta = _get_font(32, bold=True)
    font_bullet = _get_font(28)

    # ═══════════════════════════════════════════════════════════
    # SLIDE 1: Portada con imagen del toro/oso
    # ═══════════════════════════════════════════════════════════
    img1 = Image.new("RGB", (IMG_W, IMG_H), COLOR_BG)

    # Cargar imagen de fondo si existe
    if brand_image_path and Path(brand_image_path).exists():
        try:
            bg = Image.open(brand_image_path).convert("RGB")
            bg = bg.resize((IMG_W, IMG_H), Image.LANCZOS)
            # Oscurecer para que el texto sea legible
            from PIL import ImageEnhance
            bg = ImageEnhance.Brightness(bg).enhance(0.45)
            img1.paste(bg)
        except Exception:
            _draw_gradient_bg(img1, (5, 8, 18), (15, 20, 35))
    else:
        _draw_gradient_bg(img1, (5, 8, 18), (15, 20, 35))

    draw1 = ImageDraw.Draw(img1)

    # Overlay gradiente inferior para texto
    for y in range(IMG_H // 2, IMG_H):
        alpha = (y - IMG_H // 2) / (IMG_H // 2) * 0.7
        overlay_color = (int(10 * alpha), int(12 * alpha), int(18 * alpha))
        for x in range(IMG_W):
            px = img1.getpixel((x, y))
            blended = tuple(int(px[i] * (1 - alpha) + 0) for i in range(3))
            img1.putpixel((x, y), blended)

    draw1 = ImageDraw.Draw(img1)

    # Borde superior dorado
    for y2 in range(6):
        a = 1.0 - (y2 / 6)
        draw1.line([(0, y2), (IMG_W, y2)], fill=(int(255 * a), int(204 * a), 0))

    # Logo/Brand arriba
    _draw_rounded_rect(draw1, [280, 40, IMG_W - 280, 95], radius=12,
                       fill=(0, 0, 0, 180))
    draw1.rounded_rectangle([280, 40, IMG_W - 280, 95], radius=12,
                            fill=(10, 14, 20))
    draw1.text((IMG_W // 2, 67), "BUYSELL365 PRO", fill=COLOR_ACCENT,
               font=_get_font(32, bold=True), anchor="mm")

    # Texto principal sobre la imagen
    draw1.text((IMG_W // 2, 700), "SE\u00d1ALES DE", fill=COLOR_WHITE,
               font=font_title, anchor="mt")
    draw1.text((IMG_W // 2, 760), "TRADING EN VIVO", fill=COLOR_GOLD,
               font=_get_font(56, bold=True), anchor="mt")

    # Subtitulo
    draw1.text((IMG_W // 2, 850), "Forex  \u2022  Oro  \u2022  \u00cdndices  \u2022  Crypto",
               fill=COLOR_GRAY, font=font_body, anchor="mt")

    # Linea y CTA
    draw1.line([(200, 910), (IMG_W - 200, 910)], fill=COLOR_GOLD, width=1)
    draw1.text((IMG_W // 2, 935), "Desliza para conocernos",
               fill=COLOR_WHITE, font=font_small, anchor="mt")
    # Flechita
    draw1.text((IMG_W // 2, 975), "\u27a1", fill=COLOR_ACCENT,
               font=_get_font(40, bold=True), anchor="mt")

    p1 = IMAGES_DIR / f"carousel_1_portada_{int(time.time())}.jpg"
    img1.save(p1, "JPEG", quality=95)
    slides.append(p1)

    # ═══════════════════════════════════════════════════════════
    # SLIDE 2: Qu\u00e9 hacemos
    # ═══════════════════════════════════════════════════════════
    img2 = Image.new("RGB", (IMG_W, IMG_H), COLOR_BG)
    _draw_gradient_bg(img2, (8, 12, 20), (16, 22, 32))
    draw2 = ImageDraw.Draw(img2)

    for y2 in range(6):
        a = 1.0 - (y2 / 6)
        draw2.line([(0, y2), (IMG_W, y2)], fill=(0, int(200 * a), int(83 * a)))

    draw2.text((IMG_W // 2, 40), "BUYSELL365 PRO", fill=COLOR_WHITE,
               font=_get_font(32, bold=True), anchor="mt")
    draw2.line([(390, 78), (690, 78)], fill=COLOR_ACCENT, width=2)

    draw2.text((IMG_W // 2, 110), "\u00bfQu\u00e9 hacemos?", fill=COLOR_WHITE,
               font=font_title, anchor="mt")

    features = [
        ("Se\u00f1ales en tiempo real",
         "Publicamos se\u00f1ales de Forex, Oro,\n\u00cdndices y m\u00e1s con entrada, TP y SL exactos.",
         COLOR_GREEN),
        ("Resultados transparentes",
         "Mostramos TODOS los resultados:\nlos buenos Y los malos. Sin filtros.",
         COLOR_GOLD),
        ("Copy Trading autom\u00e1tico",
         "Conecta tu cuenta y replica nuestras\noperaciones autom\u00e1ticamente.",
         COLOR_ACCENT),
        ("Estad\u00edsticas diarias",
         "Resumen completo cada d\u00eda:\nWin Rate, pips, mejor/peor se\u00f1al.",
         (180, 130, 255)),
    ]

    y_start = 210
    for title_text, desc, color in features:
        # Indicador de color
        draw2.rounded_rectangle([80, y_start + 5, 92, y_start + 55], radius=4, fill=color)

        draw2.text((115, y_start), title_text, fill=COLOR_WHITE, font=font_subtitle)
        draw2.text((115, y_start + 48), desc, fill=COLOR_GRAY, font=font_small)
        y_start += 175

    # Footer
    draw2.line([(150, 940), (IMG_W - 150, 940)], fill=(40, 46, 54), width=1)
    draw2.text((IMG_W // 2, 970), "buysell365.pro", fill=COLOR_GRAY,
               font=font_small, anchor="mt")
    draw2.text((IMG_W // 2, 1005), "Desliza \u27a1", fill=COLOR_ACCENT,
               font=font_small, anchor="mt")

    p2 = IMAGES_DIR / f"carousel_2_features_{int(time.time())}.jpg"
    img2.save(p2, "JPEG", quality=95)
    slides.append(p2)

    # ═══════════════════════════════════════════════════════════
    # SLIDE 3: Ejemplo de se\u00f1al
    # ═══════════════════════════════════════════════════════════
    p3 = _generate_new_signal_image("EUR/USD", "BUY", 1.1745, 1.1845, 1.1690, "Canal VIP")
    slides.append(p3)

    # ═══════════════════════════════════════════════════════════
    # SLIDE 4: Ejemplo de TP
    # ═══════════════════════════════════════════════════════════
    p4 = _generate_tp_image("XAUUSD", "BUY", 3245.50, 3278.90, "+334 pts", "Canal VIP")
    slides.append(p4)

    # ═══════════════════════════════════════════════════════════
    # SLIDE 5: CTA final
    # ═══════════════════════════════════════════════════════════
    img5 = Image.new("RGB", (IMG_W, IMG_H), COLOR_BG)
    _draw_gradient_bg(img5, (8, 12, 20), (16, 22, 32))
    draw5 = ImageDraw.Draw(img5)

    for y2 in range(6):
        a = 1.0 - (y2 / 6)
        draw5.line([(0, y2), (IMG_W, y2)], fill=(int(59 * a), int(130 * a), int(246 * a)))

    draw5.text((IMG_W // 2, 50), "BUYSELL365 PRO", fill=COLOR_WHITE,
               font=font_brand, anchor="mt")
    draw5.line([(370, 100), (710, 100)], fill=COLOR_ACCENT, width=2)

    draw5.text((IMG_W // 2, 180), "\u00danete al", fill=COLOR_GRAY,
               font=font_subtitle, anchor="mt")
    draw5.text((IMG_W // 2, 240), "CANAL VIP", fill=COLOR_GOLD,
               font=_get_font(80, bold=True), anchor="mt")

    # Beneficios con iconos de texto
    benefits = [
        ("\u2713  Se\u00f1ales diarias con entrada, TP y SL", COLOR_GREEN),
        ("\u2713  Resultados verificados en tiempo real", COLOR_GREEN),
        ("\u2713  Copy Trading autom\u00e1tico disponible", COLOR_GREEN),
        ("\u2713  Soporte y comunidad activa 24/7", COLOR_GREEN),
        ("\u2713  Dashboard web con estad\u00edsticas", COLOR_GREEN),
    ]

    y_b = 400
    for text, color in benefits:
        _draw_rounded_rect(draw5, [100, y_b, IMG_W - 100, y_b + 55], radius=10,
                           fill=COLOR_CARD_BG)
        draw5.text((130, y_b + 12), text, fill=color, font=font_bullet)
        y_b += 72

    # Web
    _draw_rounded_rect(draw5, [200, 780, IMG_W - 200, 830], radius=12,
                       fill=(20, 25, 35), outline=COLOR_ACCENT, width=2)
    draw5.text((IMG_W // 2, 805), "buysell365.pro", fill=COLOR_ACCENT,
               font=font_subtitle, anchor="mm")

    # CTA boton
    _draw_rounded_rect(draw5, [150, 870, IMG_W - 150, 940], radius=28,
                       fill=COLOR_ACCENT)
    draw5.text((IMG_W // 2, 905), "Link en bio para empezar",
               fill=COLOR_WHITE, font=font_cta, anchor="mm")

    # Footer
    draw5.line([(150, 975), (IMG_W - 150, 975)], fill=(40, 46, 54), width=1)
    draw5.text((IMG_W // 2, 1000), "S\u00edguenos para resultados diarios", fill=COLOR_GOLD,
               font=font_small, anchor="mt")
    draw5.text((IMG_W // 2, 1035), "@buysell365.pro_tradingsignals", fill=COLOR_GRAY,
               font=_get_font(20), anchor="mt")

    p5 = IMAGES_DIR / f"carousel_5_cta_{int(time.time())}.jpg"
    img5.save(p5, "JPEG", quality=95)
    slides.append(p5)

    log.info(f"Instagram: carrusel generado — {len(slides)} slides")
    return slides


# ── Funciones publicas (llamadas desde signal_copier.py) ──────

def post_tp_celebration(pair: str, direction: str, entry: float, tp: float,
                        pips: str, source: str = "") -> bool:
    """Genera imagen de TP y publica en Instagram. Thread-safe."""
    if not IG_ENABLED:
        return False

    def _do_post():
        with _ig_lock:
            try:
                image_path = _generate_tp_image(pair, direction, entry, tp, pips, source)
                cl = _get_client()
                if not cl:
                    log.warning("Instagram: no se pudo conectar, imagen guardada localmente")
                    return

                caption = (
                    f"TP ALCANZADO {pair.upper()} {pips}\n\n"
                    f"Otra se\u00f1al exitosa de nuestro Canal VIP\n\n"
                    f"Nuestros miembros recibieron esta se\u00f1al "
                    f"antes de que el mercado se moviera.\n\n"
                    f"\u00bfQuieres recibir las pr\u00f3ximas?\n"
                    f"Link en bio para unirte\n\n"
                    f"Tambi\u00e9n puedes unirte a nuestro grupo GRATIS:\n"
                    f"@BUYSELL_365_24_7 en Telegram\n\n"
                    f"#trading #forex #tp #profit #buysell365 "
                    f"#{pair.lower().replace('/', '')} #tradingview #forexsignals "
                    f"#daytrading #swingtrading #pips #tradinglifestyle "
                    f"#tradingresults #forextrader"
                )

                cl.photo_upload(image_path, caption)
                log.info(f"Instagram: TP {pair} publicado OK")

            except Exception as e:
                log.warning(f"Instagram post TP error: {e}")

    # Ejecutar en thread separado para no bloquear el copier
    threading.Thread(target=_do_post, daemon=True, name="ig_tp_post").start()
    return True


def post_daily_summary(stats: dict) -> bool:
    """Genera imagen de resumen diario y publica en Instagram."""
    if not IG_ENABLED:
        return False

    def _do_post():
        with _ig_lock:
            try:
                image_path = _generate_daily_summary_image(stats)
                cl = _get_client()
                if not cl:
                    log.warning("Instagram: no se pudo conectar, imagen guardada localmente")
                    return

                wr = stats.get("wr", 0)
                tps = stats.get("tps", 0)
                sls = stats.get("sls", 0)
                pips = stats.get("pips_netos", 0)
                fecha = stats.get("fecha", datetime.now().strftime("%d/%m/%Y"))

                caption = (
                    f"RESUMEN DEL DIA - {fecha}\n\n"
                    f"Win Rate: {wr:.0f}%\n"
                    f"TPs: {tps} | SLs: {sls}\n"
                    f"Pips Netos: {pips:+.0f}\n\n"
                    f"Transparencia total - Publicamos TODOS los resultados\n\n"
                    f"Quieres recibir nuestras senales? Link en bio\n\n"
                    f"#trading #forex #results #tradingresults #buysell365 "
                    f"#forexsignals #daytrading #winrate #tradingjournal "
                    f"#accountability #tradinglifestyle #pips"
                )

                cl.photo_upload(image_path, caption)
                log.info(f"Instagram: resumen diario publicado OK")

            except Exception as e:
                log.warning(f"Instagram post resumen error: {e}")

    threading.Thread(target=_do_post, daemon=True, name="ig_daily_post").start()
    return True


def post_new_signal(pair: str, direction: str, entry: float, tp: float,
                    sl: float, source: str = "") -> bool:
    """DESACTIVADO — no publicar se\u00f1ales en Instagram (contenido VIP exclusivo)."""
    # Las se\u00f1ales con precios son contenido exclusivo del canal VIP.
    # Instagram es solo para marketing: TPs, resumenes y publicidad.
    return False


def post_carousel(brand_image_path: str = None, caption: str = None) -> bool:
    """Genera carrusel de presentaci\u00f3n y lo publica en Instagram."""
    if not IG_ENABLED:
        return False

    def _do_post():
        with _ig_lock:
            try:
                slide_paths = _generate_carousel_slides(brand_image_path)
                cl = _get_client()
                if not cl:
                    log.warning("Instagram: no se pudo conectar, carrusel guardado localmente")
                    return

                if not caption:
                    _caption = (
                        "Bienvenidos a BuySell365 Pro\n\n"
                        "Se\u00f1ales de trading en vivo:\n"
                        "Forex \u2022 Oro \u2022 \u00cdndices \u2022 Crypto\n\n"
                        "Lo que nos diferencia:\n"
                        "- Publicamos TODOS los resultados\n"
                        "- Los d\u00edas buenos Y los malos\n"
                        "- Sin filtros, sin trucos\n"
                        "- Transparencia total\n\n"
                        "Desliza para ver c\u00f3mo funcionan nuestras se\u00f1ales\n\n"
                        "Link en bio para unirte al canal VIP\n\n"
                        "#trading #forex #signals #buysell365 #forexsignals "
                        "#tradingsignals #gold #xauusd #indices #nasdaq "
                        "#daytrading #swingtrading #pips #tradinglifestyle "
                        "#tradingview #forextrader #tradingcommunity "
                        "#copytrade #tradingresults #accountability"
                    )
                else:
                    _caption = caption

                cl.album_upload(slide_paths, _caption)
                log.info(f"Instagram: carrusel publicado OK ({len(slide_paths)} slides)")

            except Exception as e:
                log.warning(f"Instagram post carrusel error: {e}")

    threading.Thread(target=_do_post, daemon=True, name="ig_carousel_post").start()
    return True


def generate_tp_image_only(pair: str, direction: str, entry: float, tp: float,
                           pips: str, source: str = "") -> Path:
    """Solo genera la imagen sin publicar (para testing)."""
    return _generate_tp_image(pair, direction, entry, tp, pips, source)


def generate_daily_image_only(stats: dict) -> Path:
    """Solo genera la imagen de resumen sin publicar (para testing)."""
    return _generate_daily_summary_image(stats)


def generate_signal_image_only(pair: str, direction: str, entry: float,
                                tp: float, sl: float, source: str = "") -> Path:
    """Solo genera la imagen de se\u00f1al sin publicar (para testing)."""
    return _generate_new_signal_image(pair, direction, entry, tp, sl, source)


def generate_carousel_only(brand_image_path: str = None) -> list:
    """Solo genera las im\u00e1genes del carrusel sin publicar (para testing)."""
    return _generate_carousel_slides(brand_image_path)


# ── Test rapido ───────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    print("Generando im\u00e1genes de ejemplo...")

    p1 = generate_tp_image_only("XAUUSD", "BUY", 3245.50, 3278.90,
                                "+334 pts", "SureShotFX")
    print(f"  TP image: {p1}")

    p2 = generate_daily_image_only({
        "fecha": "15/04/2026",
        "wr": 26.3,
        "tps": 5,
        "sls": 14,
        "pips_netos": -937,
        "total": 19,
        "mejor": "NAS100 +246 pts",
        "peor": "XAUUSD -110 pts",
    })
    print(f"  Daily image: {p2}")

    p3 = generate_signal_image_only("EUR/USD", "BUY", 1.1745, 1.1845, 1.1690,
                                     "Learn2Trade")
    print(f"  Signal image: {p3}")

    # Carrusel con imagen de marca si se proporciona
    brand_img = sys.argv[1] if len(sys.argv) > 1 else None
    slides = generate_carousel_only(brand_img)
    for i, s in enumerate(slides):
        print(f"  Carousel slide {i+1}: {s}")

    print(f"\nIm\u00e1genes generadas en ig_images/")
    print(f"Instagram habilitado: {IG_ENABLED}")

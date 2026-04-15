"""
BuySell365 — Instagram Auto-Poster
====================================
Genera imagenes profesionales y publica automaticamente en Instagram:
- TPs alcanzados (celebraciones) — post + Story + Reel
- Resumen diario de resultados
- Posts programados (motivacion, tips, horarios mercado)
- Auto-follow conservador de cuentas del nicho

Usa instagrapi para la API de Instagram y Pillow para generar imagenes.
Credenciales en .env: IG_USERNAME, IG_PASSWORD
"""

import os
import json
import logging
import random
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, List

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


# ── Hashtags rotativos por categoria ─────────────────────────
_HASHTAGS = {
    "tp": [
        ["#trading", "#forex", "#tp", "#profit", "#buysell365", "#forexsignals",
         "#tradingresults", "#pips", "#daytrading", "#tradinglifestyle",
         "#forextrader", "#tradingview", "#swingtrading", "#fx"],
        ["#trading", "#forexsignals", "#takeprofit", "#buysell365", "#tradingsignals",
         "#forexprofit", "#pips", "#daytrader", "#tradingcommunity",
         "#forexlife", "#currencytrading", "#tradingjournal", "#priceaction"],
        ["#trading", "#forex", "#profit", "#buysell365", "#forextrading",
         "#tradingresults", "#signalsprovider", "#goldtrading", "#xauusd",
         "#indices", "#nasdaq", "#forexsignals", "#tradingwin", "#pipsgain"],
    ],
    "daily": [
        ["#trading", "#forex", "#tradingresults", "#buysell365", "#forexsignals",
         "#winrate", "#tradingjournal", "#accountability", "#daytrading",
         "#tradinglifestyle", "#pips", "#transparencia", "#tradingstats"],
        ["#trading", "#forexsignals", "#dailyresults", "#buysell365", "#tradingrecap",
         "#forextrader", "#tradingcommunity", "#results", "#daytrader",
         "#tradingperformance", "#forexlife", "#pipscount", "#tradingdata"],
        ["#trading", "#forex", "#tradingresults", "#buysell365", "#dailyrecap",
         "#forexsignals", "#winrate", "#tradingjournal", "#signalsprovider",
         "#forexprofit", "#tradingview", "#priceaction", "#accountability"],
    ],
    "motivational": [
        ["#trading", "#tradingmotivation", "#buysell365", "#forextrader",
         "#tradingmindset", "#tradinglife", "#discipline", "#forexsignals",
         "#daytrader", "#tradingpsychology", "#tradingquotes", "#mindset",
         "#forexlifestyle", "#traderslife"],
        ["#trading", "#motivation", "#buysell365", "#forexmotivation",
         "#tradingjourney", "#tradingdiscipline", "#forextrading", "#patience",
         "#tradingmindset", "#daytrading", "#riskmanagement", "#trader",
         "#forexcommunity", "#growthmindset"],
    ],
    "reel": [
        ["#trading", "#forexreels", "#buysell365", "#tradingresults", "#forex",
         "#tradingview", "#forexsignals", "#pips", "#daytrading", "#reels",
         "#tradinglife", "#forextrader", "#viral", "#explore", "#tradingchart"],
        ["#trading", "#reels", "#buysell365", "#forexsignals", "#tradingsignals",
         "#chartanalysis", "#technicalanalysis", "#forextrading", "#explore",
         "#viral", "#tradingresults", "#forexlife", "#tradingcommunity"],
    ],
}


def _get_hashtags(category: str, pair: str = "") -> str:
    """Devuelve hashtags rotativos para la categoria dada."""
    sets = _HASHTAGS.get(category, _HASHTAGS["tp"])
    tags = random.choice(sets).copy()
    # Añadir hashtag del par si aplica
    if pair:
        pair_tag = "#" + pair.lower().replace("/", "").replace(" ", "")
        if pair_tag not in tags:
            tags.insert(4, pair_tag)
    return " ".join(tags[:20])  # Instagram max ~30, usamos 20 para ser seguros


# ── Generador de imagenes motivacionales ─────────────────────

_MOTIVATIONAL_QUOTES = [
    ("La disciplina es el puente\nentre metas y logros.", "Jim Rohn"),
    ("El mercado recompensa\nla paciencia, no la prisa.", "BuySell365"),
    ("Un buen trader no predice,\ngestiona el riesgo.", "BuySell365"),
    ("La consistencia supera\nal talento sin disciplina.", "BuySell365"),
    ("Protege tu capital.\nLas oportunidades siempre vuelven.", "BuySell365"),
    ("El 90% del trading es\npsicolog\u00eda y gesti\u00f3n de riesgo.", "BuySell365"),
    ("No operes por emoci\u00f3n.\nOpera por estrategia.", "BuySell365"),
    ("Las p\u00e9rdidas son el costo\nde hacer negocios en el mercado.", "BuySell365"),
    ("Un plan de trading sin disciplina\nes solo una lista de deseos.", "BuySell365"),
    ("El mejor trade es el que\nno tomas cuando no hay setup.", "BuySell365"),
    ("Pierde peque\u00f1o, gana grande.\nAs\u00ed se construye una cuenta.", "BuySell365"),
    ("No necesitas ganar siempre.\nNecesitas ganar m\u00e1s de lo que pierdes.", "BuySell365"),
]

_TRADING_TIPS = [
    ("Tip: Stop Loss", "NUNCA operes sin Stop Loss.\nEs tu seguro de vida en el mercado.\nDefine tu riesgo ANTES de entrar."),
    ("Tip: Gesti\u00f3n de Riesgo", "No arriesgues m\u00e1s del 1-2%\nde tu cuenta por operaci\u00f3n.\nSobrevivir > ganar."),
    ("Tip: Horarios Clave", "Las mejores oportunidades:\n08:00-11:00 (Londres)\n14:30-17:00 (NY overlap)\nEvita el mercado asi\u00e1tico si operas Forex."),
    ("Tip: Diario de Trading", "Registra TODAS tus operaciones.\nSin datos no hay mejora.\nRevisa tu journal cada semana."),
    ("Tip: Overtrading", "M\u00e1s operaciones \u2260 m\u00e1s ganancias.\nCalidad > Cantidad.\n3 buenos trades > 15 mediocres."),
    ("Tip: Noticias", "No operes 30 min antes/despu\u00e9s\nde noticias de alto impacto (NFP, CPI, FOMC).\nEl spread se dispara."),
    ("Tip: Tendencia", "La tendencia es tu amiga.\nNo pelees contra el mercado.\nOpera en la direcci\u00f3n del impulso."),
    ("Tip: Take Profit", "No seas codicioso.\nToma parciales cuando el mercado te da.\nAsegurar ganancias = consistencia."),
]

_MARKET_HOURS = {
    "Sydney":   ("22:00", "07:00", "AUD, NZD"),
    "Tokio":    ("00:00", "09:00", "JPY, pares asi\u00e1ticos"),
    "Londres":  ("08:00", "17:00", "GBP, EUR — Mayor volumen"),
    "Nueva York": ("13:00", "22:00", "USD — Overlap con Londres"),
}


def _generate_motivational_image(quote: str = None, author: str = None) -> Path:
    """Genera imagen con frase motivacional de trading."""
    if not quote:
        q, a = random.choice(_MOTIVATIONAL_QUOTES)
        quote, author = q, a

    img = Image.new("RGB", (IMG_W, IMG_H), COLOR_BG)
    _draw_gradient_bg(img, (8, 10, 20), (14, 18, 28))
    draw = ImageDraw.Draw(img)

    font_brand = _get_font(32, bold=True)
    font_quote = _get_font(44, bold=True)
    font_author = _get_font(28)
    font_small = _get_font(22)
    font_cta = _get_font(26, bold=True)

    # Borde superior dorado
    for y in range(6):
        a = 1.0 - (y / 6)
        draw.line([(0, y), (IMG_W, y)], fill=(int(255 * a), int(204 * a), 0))

    # Brand
    draw.text((IMG_W // 2, 40), "BUYSELL365 PRO", fill=COLOR_WHITE,
              font=font_brand, anchor="mt")
    draw.line([(390, 78), (690, 78)], fill=COLOR_GOLD, width=2)

    # Comillas decorativas
    draw.text((100, 200), "\u201c", fill=(COLOR_GOLD[0], COLOR_GOLD[1], COLOR_GOLD[2], 80),
              font=_get_font(120, bold=True))

    # Quote centrado
    _draw_rounded_rect(draw, [80, 280, IMG_W - 80, 680], radius=20,
                       fill=COLOR_CARD_BG, outline=(35, 40, 50), width=1)
    draw.multiline_text((IMG_W // 2, 480), quote, fill=COLOR_WHITE,
                        font=font_quote, anchor="mm", align="center", spacing=16)

    # Comilla final y autor
    draw.text((IMG_W - 120, 600), "\u201d", fill=COLOR_GOLD,
              font=_get_font(120, bold=True), anchor="rt")

    if author:
        draw.text((IMG_W // 2, 730), f"\u2014 {author}", fill=COLOR_GOLD,
                  font=font_author, anchor="mt")

    # Linea decorativa
    draw.line([(300, 800), (IMG_W - 300, 800)], fill=COLOR_ACCENT, width=2)

    # CTA
    draw.text((IMG_W // 2, 840), "\u00bfQuieres se\u00f1ales de trading?",
              fill=COLOR_GRAY, font=font_small, anchor="mt")
    _draw_rounded_rect(draw, [200, 890, IMG_W - 200, 945], radius=25,
                       fill=COLOR_ACCENT)
    draw.text((IMG_W // 2, 917), "\u00danete al VIP \u2014 Link en bio",
              fill=COLOR_WHITE, font=font_cta, anchor="mm")

    # Footer
    draw.line([(150, 975), (IMG_W - 150, 975)], fill=(40, 46, 54), width=1)
    draw.text((IMG_W // 2, 1000), "buysell365.pro  \u2022  Trading Signals",
              fill=COLOR_GRAY, font=font_small, anchor="mt")
    draw.text((IMG_W // 2, 1035), "S\u00edguenos para m\u00e1s contenido",
              fill=(70, 75, 85), font=_get_font(18), anchor="mt")

    filename = f"motivational_{int(time.time())}.jpg"
    filepath = IMAGES_DIR / filename
    img.save(filepath, "JPEG", quality=95)
    log.info(f"Instagram: imagen motivacional generada -> {filename}")
    return filepath


def _generate_tip_image(title: str, body: str) -> Path:
    """Genera imagen con tip de trading."""
    img = Image.new("RGB", (IMG_W, IMG_H), COLOR_BG)
    _draw_gradient_bg(img, (10, 12, 22), (16, 20, 32))
    draw = ImageDraw.Draw(img)

    font_brand = _get_font(32, bold=True)
    font_title = _get_font(48, bold=True)
    font_body = _get_font(34)
    font_small = _get_font(22)
    font_cta = _get_font(26, bold=True)
    font_emoji = _get_font(60, bold=True)

    # Borde superior azul
    for y in range(6):
        a = 1.0 - (y / 6)
        draw.line([(0, y), (IMG_W, y)], fill=(int(59 * a), int(130 * a), int(246 * a)))

    # Brand
    draw.text((IMG_W // 2, 40), "BUYSELL365 PRO", fill=COLOR_WHITE,
              font=font_brand, anchor="mt")
    draw.line([(390, 78), (690, 78)], fill=COLOR_ACCENT, width=2)

    # Icono libro
    draw.text((IMG_W // 2, 130), "\U0001f4d6", fill=COLOR_ACCENT,
              font=font_emoji, anchor="mt")

    # Titulo
    _draw_rounded_rect(draw, [80, 220, IMG_W - 80, 310], radius=16,
                       fill=COLOR_ACCENT)
    draw.text((IMG_W // 2, 265), title.upper(), fill=COLOR_WHITE,
              font=font_title, anchor="mm")

    # Body
    _draw_rounded_rect(draw, [60, 350, IMG_W - 60, 720], radius=20,
                       fill=COLOR_CARD_BG, outline=(35, 40, 50), width=1)
    draw.multiline_text((IMG_W // 2, 535), body, fill=COLOR_WHITE,
                        font=font_body, anchor="mm", align="center", spacing=14)

    # Separador
    draw.line([(200, 770), (IMG_W - 200, 770)], fill=COLOR_GOLD, width=2)

    # CTA
    draw.text((IMG_W // 2, 810), "M\u00e1s tips y se\u00f1ales en nuestro canal",
              fill=COLOR_GRAY, font=font_small, anchor="mt")
    _draw_rounded_rect(draw, [200, 870, IMG_W - 200, 925], radius=25,
                       fill=COLOR_ACCENT)
    draw.text((IMG_W // 2, 897), "\u00danete al VIP \u2014 Link en bio",
              fill=COLOR_WHITE, font=font_cta, anchor="mm")

    # Footer
    draw.line([(150, 960), (IMG_W - 150, 960)], fill=(40, 46, 54), width=1)
    draw.text((IMG_W // 2, 985), "buysell365.pro  \u2022  Trading Signals",
              fill=COLOR_GRAY, font=font_small, anchor="mt")
    draw.text((IMG_W // 2, 1020), "Educa \u2022 Opera \u2022 Crece",
              fill=COLOR_GOLD, font=_get_font(20), anchor="mt")

    filename = f"tip_{int(time.time())}.jpg"
    filepath = IMAGES_DIR / filename
    img.save(filepath, "JPEG", quality=95)
    log.info(f"Instagram: imagen tip generada -> {filename}")
    return filepath


def _generate_market_hours_image() -> Path:
    """Genera imagen con horarios de mercado."""
    img = Image.new("RGB", (IMG_W, IMG_H), COLOR_BG)
    _draw_gradient_bg(img, (8, 12, 20), (14, 18, 28))
    draw = ImageDraw.Draw(img)

    font_brand = _get_font(32, bold=True)
    font_title = _get_font(44, bold=True)
    font_session = _get_font(36, bold=True)
    font_time = _get_font(30)
    font_desc = _get_font(24)
    font_small = _get_font(22)
    font_cta = _get_font(26, bold=True)

    # Borde superior
    for y in range(6):
        a = 1.0 - (y / 6)
        draw.line([(0, y), (IMG_W, y)], fill=(int(255 * a), int(165 * a), 0))

    # Brand
    draw.text((IMG_W // 2, 40), "BUYSELL365 PRO", fill=COLOR_WHITE,
              font=font_brand, anchor="mt")
    draw.line([(390, 78), (690, 78)], fill=COLOR_GOLD, width=2)

    # Titulo
    draw.text((IMG_W // 2, 110), "HORARIOS DEL MERCADO", fill=COLOR_WHITE,
              font=font_title, anchor="mt")
    draw.text((IMG_W // 2, 165), "(Hora Espa\u00f1a / CET)", fill=COLOR_GRAY,
              font=font_desc, anchor="mt")

    # Sesiones
    sessions_colors = [
        ("Sydney", (0, 150, 200)),
        ("Tokio", (200, 50, 50)),
        ("Londres", (0, 180, 100)),
        ("Nueva York", (59, 130, 246)),
    ]
    y_pos = 230
    for session_name, color in sessions_colors:
        hours = _MARKET_HOURS[session_name]
        _draw_rounded_rect(draw, [60, y_pos, IMG_W - 60, y_pos + 140], radius=16,
                           fill=COLOR_CARD_BG, outline=color, width=2)
        # Barra lateral de color
        draw.rounded_rectangle([60, y_pos, 75, y_pos + 140], radius=8, fill=color)

        draw.text((110, y_pos + 15), session_name.upper(), fill=color,
                  font=font_session)
        draw.text((110, y_pos + 60), f"\u23f0  {hours[0]} — {hours[1]}",
                  fill=COLOR_WHITE, font=font_time)
        draw.text((110, y_pos + 100), hours[2], fill=COLOR_GRAY, font=font_desc)
        y_pos += 165

    # Nota overlap
    _draw_rounded_rect(draw, [100, y_pos + 10, IMG_W - 100, y_pos + 60], radius=12,
                       fill=(30, 50, 30), outline=COLOR_GREEN, width=1)
    draw.text((IMG_W // 2, y_pos + 35), "\u26a1 Mejor hora: 14:30-17:00 (Overlap Londres-NY)",
              fill=COLOR_GREEN, font=font_desc, anchor="mm")

    # CTA
    _draw_rounded_rect(draw, [200, y_pos + 90, IMG_W - 200, y_pos + 145], radius=25,
                       fill=COLOR_ACCENT)
    draw.text((IMG_W // 2, y_pos + 117), "\u00danete al VIP \u2014 Link en bio",
              fill=COLOR_WHITE, font=font_cta, anchor="mm")

    # Footer
    draw.line([(150, IMG_H - 60), (IMG_W - 150, IMG_H - 60)], fill=(40, 46, 54), width=1)
    draw.text((IMG_W // 2, IMG_H - 35), "buysell365.pro  \u2022  Trading Signals",
              fill=COLOR_GRAY, font=font_small, anchor="mt")

    filename = f"market_hours_{int(time.time())}.jpg"
    filepath = IMAGES_DIR / filename
    img.save(filepath, "JPEG", quality=95)
    log.info(f"Instagram: imagen horarios generada -> {filename}")
    return filepath


# ── Imagen promo de Instagram para Telegram ──────────────────

def generate_ig_promo_image() -> Path:
    """Genera imagen profesional de promo Instagram para el grupo Telegram.
    Incluye logo Instagram real dibujado con PIL, gradiente de marca, y CTA."""
    img = Image.new("RGB", (IMG_W, IMG_H), COLOR_BG)
    _draw_gradient_bg(img, (10, 8, 20), (20, 14, 35))
    draw = ImageDraw.Draw(img)

    font_brand = _get_font(32, bold=True)
    font_big = _get_font(56, bold=True)
    font_handle = _get_font(34, bold=True)
    font_body = _get_font(30)
    font_small = _get_font(24)
    font_cta = _get_font(28, bold=True)
    font_bullet = _get_font(28)

    # ── Borde superior con gradiente Instagram ──
    for y in range(10):
        a = 1.0 - (y / 10)
        ratio = y / 10
        r = int((240 + (188 - 240) * ratio) * a)
        g = int((148 + (24 - 148) * ratio) * a)
        b = int((51 + (136 - 51) * ratio) * a)
        draw.line([(0, y), (IMG_W, y)], fill=(r, g, b))

    # ── Brand arriba ──
    draw.text((IMG_W // 2, 40), "BUYSELL365 PRO", fill=COLOR_WHITE,
              font=font_brand, anchor="mt")
    draw.line([(390, 78), (690, 78)], fill=COLOR_ACCENT, width=2)

    # ── Logo Instagram (dibujado con formas) ──
    # Cuadrado redondeado con gradiente Instagram
    ig_cx, ig_cy = IMG_W // 2, 230
    ig_size = 140

    # Fondo gradiente del logo
    for _dy in range(-ig_size // 2, ig_size // 2):
        for _dx in range(-ig_size // 2, ig_size // 2):
            # Verificar que esta dentro del rectangulo redondeado
            ax, ay = abs(_dx), abs(_dy)
            corner_r = 30
            in_rect = True
            if ax > ig_size // 2 - corner_r and ay > ig_size // 2 - corner_r:
                dist = ((ax - (ig_size // 2 - corner_r)) ** 2 + (ay - (ig_size // 2 - corner_r)) ** 2) ** 0.5
                if dist > corner_r:
                    in_rect = False
            if in_rect:
                # Gradiente diagonal (naranja arriba-izq a morado abajo-der)
                t = ((_dx + ig_size // 2) + (_dy + ig_size // 2)) / (ig_size * 2)
                r = int(240 + (188 - 240) * t)
                g = int(148 + (24 - 148) * t)
                b = int(51 + (136 - 51) * t)
                img.putpixel((ig_cx + _dx, ig_cy + _dy), (r, g, b))

    # Circulo central (lente de la camara)
    for angle_step in range(360 * 4):
        angle = angle_step / 4 * 3.14159 / 180
        for radius in range(35, 42):
            import math
            px = int(ig_cx + radius * math.cos(angle))
            py = int(ig_cy + radius * math.sin(angle))
            if 0 <= px < IMG_W and 0 <= py < IMG_H:
                img.putpixel((px, py), (255, 255, 255))

    # Punto del flash (esquina superior derecha)
    for _dy2 in range(-6, 7):
        for _dx2 in range(-6, 7):
            if _dx2 ** 2 + _dy2 ** 2 <= 36:
                px2 = ig_cx + 40 + _dx2
                py2 = ig_cy - 40 + _dy2
                if 0 <= px2 < IMG_W and 0 <= py2 < IMG_H:
                    img.putpixel((px2, py2), (255, 255, 255))

    draw = ImageDraw.Draw(img)

    # ── Titulo ──
    draw.text((IMG_W // 2, 340), "S\u00edguenos en", fill=COLOR_GRAY,
              font=font_body, anchor="mt")
    # "Instagram" con gradiente (simulado con color rosa)
    draw.text((IMG_W // 2, 385), "Instagram", fill=(225, 48, 108),
              font=_get_font(64, bold=True), anchor="mt")

    # ── Handle ──
    _draw_rounded_rect(draw, [120, 475, IMG_W - 120, 535], radius=28,
                       fill=(35, 20, 45), outline=(225, 48, 108), width=2)
    draw.text((IMG_W // 2, 505), "@buysell365.pro_tradingsignals",
              fill=COLOR_WHITE, font=font_handle, anchor="mm")

    # ── Beneficios ──
    benefits = [
        ("\u2705  Resultados diarios verificados", COLOR_GREEN),
        ("\U0001f3af  Celebraciones de cada TP en vivo", COLOR_GOLD),
        ("\U0001f4ca  Estad\u00edsticas y win rate real", COLOR_ACCENT),
        ("\U0001f4a1  Tips y motivaci\u00f3n de trading", (180, 130, 255)),
        ("\U0001f50d  Transparencia total \u2014 sin filtros", COLOR_WHITE),
    ]
    y_pos = 580
    for text, color in benefits:
        _draw_rounded_rect(draw, [100, y_pos, IMG_W - 100, y_pos + 50], radius=10,
                           fill=COLOR_CARD_BG)
        draw.text((130, y_pos + 10), text, fill=color, font=font_bullet)
        y_pos += 62

    # ── CTA ──
    # Boton con gradiente Instagram
    _draw_rounded_rect(draw, [150, 920, IMG_W - 150, 985], radius=30,
                       fill=(225, 48, 108))
    draw.text((IMG_W // 2, 952), "S\u00edguenos ahora \u2192",
              fill=COLOR_WHITE, font=font_cta, anchor="mm")

    # ── Footer ──
    draw.line([(200, 1010), (IMG_W - 200, 1010)], fill=(40, 46, 54), width=1)
    draw.text((IMG_W // 2, 1030), "buysell365.pro  \u2022  Trading con IA",
              fill=COLOR_GRAY, font=font_small, anchor="mt")

    filename = f"ig_promo_{int(time.time())}.jpg"
    filepath = IMAGES_DIR / filename
    img.save(filepath, "JPEG", quality=95)
    log.info(f"Instagram promo image generada -> {filename}")
    return filepath


# ── Generador de Reels (video con efecto Ken Burns) ──────────

def _generate_tp_reel_video(pair: str, direction: str, pips: str,
                            tp_image_path: Path = None, duration_s: float = 8.0) -> Optional[Path]:
    """Genera un Reel profesional con escenas animadas:
    Escena 1: Logo + flash (1.5s)
    Escena 2: Par + direccion aparece (2s)
    Escena 3: Pips grandes con efecto (2s)
    Escena 4: Imagen TP + CTA (2.5s)
    """
    try:
        import imageio
        import numpy as np
    except ImportError:
        log.warning("Instagram Reel: imageio/numpy no disponible")
        return None

    try:
        REEL_W, REEL_H = 1080, 1920
        FPS = 24
        total_frames = int(duration_s * FPS)

        is_buy = direction.upper() in ("BUY", "COMPRA")
        dir_color = COLOR_GREEN if is_buy else COLOR_RED
        dir_label = "COMPRA" if is_buy else "VENTA"
        dir_icon = "\u25b2" if is_buy else "\u25bc"

        reel_path = IMAGES_DIR / f"reel_{pair.replace('/', '')}_{int(time.time())}.mp4"
        writer = imageio.get_writer(str(reel_path), fps=FPS, codec="libx264",
                                     quality=8, pixelformat="yuv420p",
                                     macro_block_size=2)

        # Cargar imagen TP si existe
        tp_img = None
        if tp_image_path and Path(tp_image_path).exists():
            tp_img = Image.open(tp_image_path).convert("RGB")

        for frame_i in range(total_frames):
            t = frame_i / max(total_frames - 1, 1)
            seconds = frame_i / FPS

            img = Image.new("RGB", (REEL_W, REEL_H), (10, 14, 22))
            # Gradiente de fondo
            for y in range(REEL_H):
                ratio = y / REEL_H
                r = int(8 + (18 - 8) * ratio)
                g = int(12 + (24 - 12) * ratio)
                b = int(20 + (35 - 20) * ratio)
                for x in range(REEL_W):
                    img.putpixel((x, y), (r, g, b))
            draw = ImageDraw.Draw(img)

            # ── ESCENA 1: Logo + flash (0-1.5s) ──
            if seconds < 1.5:
                scene_t = seconds / 1.5
                # Fade in del brand
                alpha = min(scene_t * 2, 1.0)
                brand_color = tuple(int(c * alpha) for c in COLOR_WHITE)
                accent_color = tuple(int(c * alpha) for c in COLOR_ACCENT)

                draw.text((REEL_W // 2, 750), "BUYSELL365",
                          fill=brand_color, font=_get_font(80, bold=True), anchor="mt")
                draw.text((REEL_W // 2, 850), "PRO",
                          fill=accent_color, font=_get_font(60, bold=True), anchor="mt")

                # Linea que crece
                line_w = int(400 * min(scene_t * 1.5, 1.0))
                if line_w > 0:
                    draw.line([(REEL_W // 2 - line_w, 940),
                               (REEL_W // 2 + line_w, 940)],
                              fill=COLOR_GOLD, width=3)

                # Flash al final de la escena
                if scene_t > 0.85:
                    flash = int((scene_t - 0.85) / 0.15 * 60)
                    for y in range(REEL_H):
                        for x in range(REEL_W):
                            px = img.getpixel((x, y))
                            img.putpixel((x, y), tuple(min(p + flash, 255) for p in px))
                    draw = ImageDraw.Draw(img)

            # ── ESCENA 2: TP ALCANZADO + Par (1.5-3.5s) ──
            elif seconds < 3.5:
                scene_t = (seconds - 1.5) / 2.0

                # Barra verde TP ALCANZADO — slide in desde arriba
                bar_y = int(-120 + 120 * min(scene_t * 3, 1.0))  # Slide down
                if bar_y > -120:
                    _draw_rounded_rect(draw, [80, 350 + bar_y, REEL_W - 80, 470 + bar_y],
                                       radius=20, fill=COLOR_GREEN)
                    draw.text((REEL_W // 2, 410 + bar_y), "TP ALCANZADO",
                              fill=(10, 15, 10), font=_get_font(64, bold=True), anchor="mm")

                # Par — fade in
                if scene_t > 0.3:
                    pair_alpha = min((scene_t - 0.3) / 0.4, 1.0)
                    pair_color = tuple(int(c * pair_alpha) for c in COLOR_WHITE)
                    draw.text((REEL_W // 2, 580), pair.upper(),
                              fill=pair_color, font=_get_font(100, bold=True), anchor="mt")

                # Direccion — fade in
                if scene_t > 0.5:
                    dir_alpha = min((scene_t - 0.5) / 0.3, 1.0)
                    d_color = tuple(int(c * dir_alpha) for c in dir_color)
                    draw.text((REEL_W // 2, 710), f"{dir_icon} {dir_label}",
                              fill=d_color, font=_get_font(50, bold=True), anchor="mt")

                # Brand arriba
                draw.text((REEL_W // 2, 100), "BUYSELL365 PRO",
                          fill=(80, 90, 110), font=_get_font(32, bold=True), anchor="mt")

            # ── ESCENA 3: PIPS grandes con efecto scale (3.5-5.5s) ──
            elif seconds < 5.5:
                scene_t = (seconds - 3.5) / 2.0

                # Brand arriba
                draw.text((REEL_W // 2, 100), "BUYSELL365 PRO",
                          fill=(80, 90, 110), font=_get_font(32, bold=True), anchor="mt")

                # Par arriba
                draw.text((REEL_W // 2, 350), pair.upper(),
                          fill=COLOR_GRAY, font=_get_font(48, bold=True), anchor="mt")

                # Pips — escala de grande a normal (bounce effect)
                scale_factor = 1.0 + max(0, (1.0 - scene_t * 2)) * 0.5
                pips_size = int(130 * min(scale_factor, 1.5))
                pips_font = _get_font(pips_size, bold=True)

                # Caja verde de fondo
                _draw_rounded_rect(draw, [80, 550, REEL_W - 80, 850],
                                   radius=30, fill=(10, 50, 20))
                _draw_rounded_rect(draw, [80, 550, REEL_W - 80, 850],
                                   radius=30, fill=None, outline=(0, 160, 60), width=3)

                draw.text((REEL_W // 2, 700), pips,
                          fill=COLOR_GREEN, font=pips_font, anchor="mm")

                # Texto bajo pips
                if scene_t > 0.4:
                    draw.text((REEL_W // 2, 920), "Otra se\u00f1al exitosa",
                              fill=COLOR_GRAY, font=_get_font(36), anchor="mt")
                    draw.text((REEL_W // 2, 970), "del Canal VIP",
                              fill=COLOR_GOLD, font=_get_font(40, bold=True), anchor="mt")

            # ── ESCENA 4: CTA final (5.5-8s) ──
            else:
                scene_t = (seconds - 5.5) / 2.5

                # Si tenemos la imagen TP, mostrarla centrada
                if tp_img:
                    # Escalar imagen TP a caber en el reel
                    tp_resized = tp_img.resize((900, 900), Image.LANCZOS)
                    paste_x = (REEL_W - 900) // 2
                    paste_y = 200
                    img.paste(tp_resized, (paste_x, paste_y))
                    draw = ImageDraw.Draw(img)

                # Overlay oscuro abajo para CTA
                for yy in range(REEL_H - 500, REEL_H):
                    alpha_ov = min((yy - (REEL_H - 500)) / 250, 1.0) * 0.85
                    for xx in range(REEL_W):
                        px = img.getpixel((xx, yy))
                        img.putpixel((xx, yy), tuple(int(p * (1 - alpha_ov)) for p in px))
                draw = ImageDraw.Draw(img)

                # CTA text
                cta_alpha = min(scene_t * 2, 1.0)
                cta_white = tuple(int(c * cta_alpha) for c in COLOR_WHITE)
                cta_gold = tuple(int(c * cta_alpha) for c in COLOR_GOLD)
                cta_accent = tuple(int(c * cta_alpha) for c in COLOR_ACCENT)

                draw.text((REEL_W // 2, REEL_H - 380),
                          "\u00bfQuieres estas se\u00f1ales?",
                          fill=cta_white, font=_get_font(44, bold=True), anchor="mt")

                # Boton CTA
                if scene_t > 0.3:
                    btn_alpha = min((scene_t - 0.3) / 0.4, 1.0)
                    btn_color = tuple(int(c * btn_alpha) for c in COLOR_ACCENT)
                    _draw_rounded_rect(draw, [150, REEL_H - 290, REEL_W - 150, REEL_H - 210],
                                       radius=30, fill=btn_color)
                    draw.text((REEL_W // 2, REEL_H - 250),
                              "\u00danete al VIP \u2014 Link en bio",
                              fill=cta_white, font=_get_font(36, bold=True), anchor="mm")

                # Web
                draw.text((REEL_W // 2, REEL_H - 150), "buysell365.pro",
                          fill=cta_gold, font=_get_font(32, bold=True), anchor="mt")

                # Brand
                draw.text((REEL_W // 2, REEL_H - 80), "BUYSELL365 PRO",
                          fill=(60, 70, 90), font=_get_font(24, bold=True), anchor="mt")

            writer.append_data(np.array(img))

        writer.close()
        log.info(f"Instagram: Reel profesional generado -> {reel_path.name}")
        return reel_path

    except Exception as e:
        log.warning(f"Instagram Reel generation error: {e}")
        return None


def _generate_reel_from_image(image_path: Path, pair: str = "",
                              pips: str = "", duration_s: float = 6.0) -> Optional[Path]:
    """Genera un Reel (video MP4 9:16) con efecto zoom/pan desde una imagen.
    Usa imageio para escribir frames."""
    try:
        import imageio
        import numpy as np
    except ImportError:
        log.warning("Instagram Reel: imageio/numpy no disponible")
        return None

    try:
        # Reel vertical 1080x1920
        REEL_W, REEL_H = 1080, 1920
        FPS = 24
        total_frames = int(duration_s * FPS)

        # Cargar imagen base y escalar
        src = Image.open(image_path).convert("RGB")
        # Escalar la imagen a mayor tamano para tener margen de zoom
        scale = max(REEL_W / src.width, REEL_H / src.height) * 1.3
        big_w = int(src.width * scale)
        big_h = int(src.height * scale)
        src_big = src.resize((big_w, big_h), Image.LANCZOS)

        # Definir zoom: de 1.0x a 1.15x (sutil)
        zoom_start, zoom_end = 1.0, 1.15
        # Pan: ligero movimiento del centro
        cx_start = big_w * 0.48
        cy_start = big_h * 0.45
        cx_end = big_w * 0.52
        cy_end = big_h * 0.48

        reel_path = IMAGES_DIR / f"reel_{pair.replace('/', '')}_{int(time.time())}.mp4"
        writer = imageio.get_writer(str(reel_path), fps=FPS, codec="libx264",
                                     quality=8, pixelformat="yuv420p",
                                     macro_block_size=2)

        for frame_i in range(total_frames):
            t = frame_i / max(total_frames - 1, 1)
            # Ease in-out
            t_smooth = t * t * (3 - 2 * t)

            zoom = zoom_start + (zoom_end - zoom_start) * t_smooth
            cx = cx_start + (cx_end - cx_start) * t_smooth
            cy = cy_start + (cy_end - cy_start) * t_smooth

            # Crop area
            crop_w = REEL_W / zoom
            crop_h = REEL_H / zoom
            x1 = int(cx - crop_w / 2)
            y1 = int(cy - crop_h / 2)
            x2 = int(cx + crop_w / 2)
            y2 = int(cy + crop_h / 2)

            # Clamp
            x1 = max(0, min(x1, big_w - int(crop_w)))
            y1 = max(0, min(y1, big_h - int(crop_h)))
            x2 = x1 + int(crop_w)
            y2 = y1 + int(crop_h)

            cropped = src_big.crop((x1, y1, x2, y2))
            frame = cropped.resize((REEL_W, REEL_H), Image.LANCZOS)

            # Añadir overlay de texto en los ultimos 2 segundos
            if frame_i >= total_frames - (FPS * 2):
                overlay_draw = ImageDraw.Draw(frame)
                # Semi-transparent bar at bottom
                for yy in range(REEL_H - 300, REEL_H):
                    alpha = min((yy - (REEL_H - 300)) / 150, 1.0) * 0.7
                    for xx in range(REEL_W):
                        px = frame.getpixel((xx, yy))
                        frame.putpixel((xx, yy), tuple(int(p * (1 - alpha)) for p in px))
                overlay_draw = ImageDraw.Draw(frame)
                overlay_draw.text((REEL_W // 2, REEL_H - 200),
                                  "\u00danete al Canal VIP",
                                  fill=COLOR_WHITE, font=_get_font(48, bold=True),
                                  anchor="mt")
                overlay_draw.text((REEL_W // 2, REEL_H - 130),
                                  "Link en bio \u2192 buysell365.pro",
                                  fill=COLOR_GOLD, font=_get_font(36, bold=True),
                                  anchor="mt")

            writer.append_data(np.array(frame))

        writer.close()
        log.info(f"Instagram: Reel generado -> {reel_path.name} ({total_frames} frames)")
        return reel_path

    except Exception as e:
        log.warning(f"Instagram Reel generation error: {e}")
        return None


def _generate_reel_thumbnail(pair: str, pips: str, direction: str) -> Path:
    """Genera thumbnail vertical para el Reel (1080x1920)."""
    REEL_W, REEL_H = 1080, 1920
    img = Image.new("RGB", (REEL_W, REEL_H), COLOR_BG)
    _draw_gradient_bg_custom(img, (8, 12, 20), (16, 22, 32), REEL_W, REEL_H)
    draw = ImageDraw.Draw(img)

    is_buy = direction.upper() in ("BUY", "COMPRA")
    dir_color = COLOR_GREEN if is_buy else COLOR_RED

    draw.text((REEL_W // 2, 400), "TP ALCANZADO", fill=COLOR_GREEN,
              font=_get_font(72, bold=True), anchor="mt")
    draw.text((REEL_W // 2, 550), pair.upper(), fill=COLOR_WHITE,
              font=_get_font(90, bold=True), anchor="mt")
    draw.text((REEL_W // 2, 750), pips, fill=dir_color,
              font=_get_font(120, bold=True), anchor="mt")
    draw.text((REEL_W // 2, 1000), "BUYSELL365 PRO", fill=COLOR_ACCENT,
              font=_get_font(44, bold=True), anchor="mt")
    draw.text((REEL_W // 2, 1100), "\u00danete al VIP \u2014 Link en bio",
              fill=COLOR_GOLD, font=_get_font(36, bold=True), anchor="mt")

    path = IMAGES_DIR / f"reel_thumb_{pair.replace('/', '')}_{int(time.time())}.jpg"
    img.save(path, "JPEG", quality=95)
    return path


def _draw_gradient_bg_custom(img: Image.Image, color_top: tuple, color_bottom: tuple,
                              w: int, h: int):
    """Gradiente vertical para dimensiones arbitrarias."""
    for y in range(h):
        ratio = y / h
        r = int(color_top[0] + (color_bottom[0] - color_top[0]) * ratio)
        g = int(color_top[1] + (color_bottom[1] - color_top[1]) * ratio)
        b = int(color_top[2] + (color_bottom[2] - color_top[2]) * ratio)
        for x in range(w):
            img.putpixel((x, y), (r, g, b))


# ── Funciones publicas (llamadas desde signal_copier.py) ──────

def post_tp_celebration(pair: str, direction: str, entry: float, tp: float,
                        pips: str, source: str = "") -> bool:
    """Genera imagen de TP y publica en Instagram (post + Story + Reel). Thread-safe."""
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

                hashtags = _get_hashtags("tp", pair)
                caption = (
                    f"TP ALCANZADO {pair.upper()} {pips}\n\n"
                    f"Otra se\u00f1al exitosa de nuestro Canal VIP\n\n"
                    f"Nuestros miembros recibieron esta se\u00f1al "
                    f"antes de que el mercado se moviera.\n\n"
                    f"\u00bfQuieres recibir las pr\u00f3ximas?\n"
                    f"Link en bio para unirte\n\n"
                    f"Grupo GRATIS: @BUYSELL_365_24_7 en Telegram\n\n"
                    f"{hashtags}"
                )

                # 1) Post normal
                cl.photo_upload(image_path, caption)
                log.info(f"Instagram: TP {pair} post publicado OK")

                # 2) Story
                time.sleep(random.randint(3, 8))
                try:
                    cl.photo_upload_to_story(image_path)
                    log.info(f"Instagram: TP {pair} Story publicada OK")
                except Exception as e_st:
                    log.warning(f"Instagram Story error: {e_st}")

                # 3) Reel con video profesional
                time.sleep(random.randint(5, 15))
                try:
                    reel_path = _generate_tp_reel_video(pair, direction, pips, image_path)
                    if reel_path and reel_path.exists():
                        thumbnail = _generate_reel_thumbnail(pair, pips, direction)
                        reel_hashtags = _get_hashtags("reel", pair)
                        reel_caption = (
                            f"TP ALCANZADO {pair.upper()} {pips}\n\n"
                            f"Se\u00f1al del Canal VIP\n"
                            f"\u00danete \u2014 Link en bio\n\n"
                            f"{reel_hashtags}"
                        )
                        cl.clip_upload(reel_path, reel_caption, thumbnail)
                        log.info(f"Instagram: TP {pair} Reel publicado OK")
                except Exception as e_rl:
                    log.warning(f"Instagram Reel error: {e_rl}")

            except Exception as e:
                log.warning(f"Instagram post TP error: {e}")

    threading.Thread(target=_do_post, daemon=True, name="ig_tp_post").start()
    return True


def post_daily_summary(stats: dict) -> bool:
    """Genera imagen de resumen diario y publica en Instagram (post + Story)."""
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
                hashtags = _get_hashtags("daily")

                caption = (
                    f"RESUMEN DEL DIA \u2014 {fecha}\n\n"
                    f"Win Rate: {wr:.0f}%\n"
                    f"TPs: {tps} | SLs: {sls}\n"
                    f"Pips Netos: {pips:+.0f}\n\n"
                    f"Transparencia total \u2014 Publicamos TODOS los resultados\n\n"
                    f"\u00bfQuieres recibir nuestras se\u00f1ales? Link en bio\n\n"
                    f"{hashtags}"
                )

                # Post
                cl.photo_upload(image_path, caption)
                log.info(f"Instagram: resumen diario publicado OK")

                # Story
                time.sleep(random.randint(3, 8))
                try:
                    cl.photo_upload_to_story(image_path)
                    log.info(f"Instagram: resumen diario Story OK")
                except Exception as e_st:
                    log.warning(f"Instagram Story resumen error: {e_st}")

            except Exception as e:
                log.warning(f"Instagram post resumen error: {e}")

    threading.Thread(target=_do_post, daemon=True, name="ig_daily_post").start()
    return True


def post_scheduled_content(content_type: str = "auto") -> bool:
    """Publica contenido programado: motivacional, tip o horarios de mercado.
    content_type: 'motivational', 'tip', 'market_hours', 'auto' (rotacion)."""
    if not IG_ENABLED:
        return False

    def _do_post():
        with _ig_lock:
            try:
                cl = _get_client()
                if not cl:
                    return

                # Auto-rotar entre tipos de contenido
                if content_type == "auto":
                    _type = random.choice(["motivational", "motivational", "tip", "tip", "market_hours"])
                else:
                    _type = content_type

                if _type == "motivational":
                    quote, author = random.choice(_MOTIVATIONAL_QUOTES)
                    image_path = _generate_motivational_image(quote, author)
                    hashtags = _get_hashtags("motivational")
                    caption = (
                        f"\u201c{quote.replace(chr(10), ' ')}\u201d\n"
                        f"\u2014 {author}\n\n"
                        f"\u00bfQuieres se\u00f1ales de trading?\n"
                        f"Link en bio para unirte al Canal VIP\n\n"
                        f"Grupo GRATIS: @BUYSELL_365_24_7 en Telegram\n\n"
                        f"{hashtags}"
                    )
                elif _type == "tip":
                    title, body = random.choice(_TRADING_TIPS)
                    image_path = _generate_tip_image(title, body)
                    hashtags = _get_hashtags("motivational")
                    caption = (
                        f"{title}\n\n"
                        f"{body.replace(chr(10), ' ')}\n\n"
                        f"M\u00e1s tips y se\u00f1ales en nuestro Canal VIP\n"
                        f"Link en bio para unirte\n\n"
                        f"{hashtags}"
                    )
                else:  # market_hours
                    image_path = _generate_market_hours_image()
                    hashtags = _get_hashtags("motivational")
                    caption = (
                        f"HORARIOS DEL MERCADO (Hora Espa\u00f1a)\n\n"
                        f"\U0001f30f Sydney: 22:00 - 07:00\n"
                        f"\U0001f1ef\U0001f1f5 Tokio: 00:00 - 09:00\n"
                        f"\U0001f1ec\U0001f1e7 Londres: 08:00 - 17:00\n"
                        f"\U0001f1fa\U0001f1f8 Nueva York: 13:00 - 22:00\n\n"
                        f"\u26a1 Mejor hora: 14:30-17:00 (Overlap)\n\n"
                        f"Link en bio para se\u00f1ales en vivo\n\n"
                        f"{hashtags}"
                    )

                cl.photo_upload(image_path, caption)
                log.info(f"Instagram: contenido {_type} publicado OK")

                # Tambien publicar como Story
                time.sleep(random.randint(3, 8))
                try:
                    cl.photo_upload_to_story(image_path)
                    log.info(f"Instagram: Story {_type} publicada OK")
                except Exception as e_st:
                    log.debug(f"Story {_type} skip: {e_st}")

            except Exception as e:
                log.warning(f"Instagram scheduled content error: {e}")

    threading.Thread(target=_do_post, daemon=True, name="ig_scheduled").start()
    return True


def auto_follow_niche(max_follows: int = 5) -> int:
    """Auto-follow conservador de cuentas del nicho trading.
    Busca por hashtags relevantes y sigue usuarios que no seguimos.
    Max 5 follows por ejecucion, con delays largos.
    Retorna numero de follows realizados."""
    if not IG_ENABLED:
        return 0

    follows_done = 0
    follow_log_file = Path(__file__).parent / "ig_follow_log.json"

    def _do_follow():
        nonlocal follows_done
        with _ig_lock:
            try:
                cl = _get_client()
                if not cl:
                    return

                # Cargar log de follows previos
                followed_ids = set()
                if follow_log_file.exists():
                    try:
                        data = json.loads(follow_log_file.read_text(encoding="utf-8"))
                        followed_ids = set(data.get("followed", []))
                    except Exception:
                        pass

                # Hashtags para buscar
                search_tags = ["forexsignals", "forextrader", "daytrading",
                               "tradingsignals", "forexlife", "tradingview",
                               "goldtrading", "xauusd", "tradingresults"]
                tag = random.choice(search_tags)

                log.info(f"Instagram auto-follow: buscando #{tag}")
                time.sleep(random.randint(2, 5))

                # Obtener medias recientes del hashtag
                try:
                    medias = cl.hashtag_medias_recent(tag, amount=20)
                except Exception as e_ht:
                    log.debug(f"Hashtag search error: {e_ht}")
                    return

                # Filtrar usuarios unicos que no seguimos
                candidates = []
                seen_users = set()
                for media in medias:
                    uid = media.user.pk
                    if uid not in followed_ids and uid not in seen_users:
                        seen_users.add(uid)
                        candidates.append(media.user)

                random.shuffle(candidates)

                for user in candidates[:max_follows]:
                    try:
                        time.sleep(random.randint(15, 40))  # Delay largo entre follows
                        cl.user_follow(user.pk)
                        followed_ids.add(user.pk)
                        follows_done += 1
                        log.info(f"Instagram: follow @{user.username} OK ({follows_done}/{max_follows})")
                    except Exception as e_f:
                        log.warning(f"Follow @{user.username} error: {e_f}")
                        break  # Si hay error, parar para no forzar

                # Guardar log
                follow_log_file.write_text(
                    json.dumps({"followed": list(followed_ids)[-500:],  # Ultimos 500
                                "last_follow": datetime.now().isoformat()},
                               ensure_ascii=False),
                    encoding="utf-8"
                )
                log.info(f"Instagram auto-follow: {follows_done} nuevos follows")

            except Exception as e:
                log.warning(f"Instagram auto-follow error: {e}")

    threading.Thread(target=_do_follow, daemon=True, name="ig_autofollow").start()
    return follows_done


def auto_unfollow_non_followers(max_unfollows: int = 5) -> int:
    """Unfollow de cuentas que no nos siguen de vuelta (limpieza semanal).
    Conservador: max 5 por ejecucion."""
    if not IG_ENABLED:
        return 0

    unfollows_done = 0

    def _do_unfollow():
        nonlocal unfollows_done
        with _ig_lock:
            try:
                cl = _get_client()
                if not cl:
                    return

                my_id = cl.user_id
                time.sleep(random.randint(2, 5))

                # Obtener quienes seguimos
                following = cl.user_following(my_id, amount=100)
                time.sleep(random.randint(3, 8))

                # Obtener quienes nos siguen
                followers = cl.user_followers(my_id, amount=200)
                follower_ids = set(followers.keys())

                # Encontrar no-reciprocos
                non_followers = [uid for uid in following.keys() if uid not in follower_ids]
                random.shuffle(non_followers)

                for uid in non_followers[:max_unfollows]:
                    try:
                        time.sleep(random.randint(15, 40))
                        cl.user_unfollow(uid)
                        unfollows_done += 1
                        uname = following[uid].username if uid in following else uid
                        log.info(f"Instagram: unfollow @{uname} OK ({unfollows_done}/{max_unfollows})")
                    except Exception as e_u:
                        log.warning(f"Unfollow error: {e_u}")
                        break

                log.info(f"Instagram auto-unfollow: {unfollows_done} unfollows")

            except Exception as e:
                log.warning(f"Instagram auto-unfollow error: {e}")

    threading.Thread(target=_do_unfollow, daemon=True, name="ig_unfollow").start()
    return unfollows_done


def post_new_signal(pair: str, direction: str, entry: float, tp: float,
                    sl: float, source: str = "") -> bool:
    """DESACTIVADO — no publicar se\u00f1ales en Instagram (contenido VIP exclusivo)."""
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

    p3 = _generate_motivational_image()
    print(f"  Motivational image: {p3}")

    p4 = _generate_tip_image(*random.choice(_TRADING_TIPS))
    print(f"  Tip image: {p4}")

    p5 = _generate_market_hours_image()
    print(f"  Market hours image: {p5}")

    # Reel desde imagen TP
    p6 = _generate_reel_from_image(p1, "XAUUSD", "+334 pts", duration_s=4.0)
    print(f"  Reel video: {p6}")

    print(f"\nIm\u00e1genes generadas en ig_images/")
    print(f"Instagram habilitado: {IG_ENABLED}")

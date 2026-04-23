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

# ── Highlight "TPs" — se crea automáticamente si no existe ────
_highlight_tp_pk = None  # Se resuelve al primer TP


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


def _get_or_create_tp_highlight(cl) -> Optional[str]:
    """Obtiene el Highlight 'TPs' o lo crea si no existe. Retorna highlight_pk."""
    global _highlight_tp_pk
    if _highlight_tp_pk:
        return _highlight_tp_pk
    try:
        user_id = cl.user_id
        highlights = cl.user_highlights(user_id)
        for h in highlights:
            if h.title.upper() in ("TPS", "TP", "TPS ALCANZADOS"):
                _highlight_tp_pk = h.pk
                log.info(f"Instagram: Highlight '{h.title}' encontrado (pk={h.pk})")
                return _highlight_tp_pk
        # No existe — se creara al primer TP (necesita al menos 1 story)
        log.info("Instagram: Highlight 'TPs' no existe, se creara con el primer TP")
        return None
    except Exception as e:
        log.debug(f"Instagram highlight check error: {e}")
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


def _annotate_chart_with_times(chart_path, pair: str, direction: str, pips: str,
                                fecha: str = "", hora_entrada: str = "",
                                hora_salida: str = "") -> Optional[Path]:
    """Toma un chart JPG y le superpone barra con fecha + hora entrada + hora salida.
    Devuelve nueva imagen (el original no se modifica). Fallback: original."""
    try:
        img = Image.open(str(chart_path)).convert("RGBA")
        W, H = img.size
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        bar_h = max(90, int(H * 0.11))
        draw.rectangle([0, 0, W, bar_h], fill=(0, 0, 0, 200))
        is_buy = direction.upper() in ("BUY", "COMPRA")
        accent = COLOR_GREEN if is_buy else COLOR_RED
        draw.rectangle([0, bar_h - 4, W, bar_h], fill=accent)

        font_title = _get_font(max(26, int(W * 0.028)), bold=True)
        font_body = _get_font(max(22, int(W * 0.022)), bold=False)

        left_x = int(W * 0.025)
        if fecha:
            draw.text((left_x, 14), f"Fecha: {fecha}", fill=(255, 255, 255, 255), font=font_title)
        hora_txt_parts = []
        if hora_entrada:
            hora_txt_parts.append(f"Entrada: {hora_entrada}")
        if hora_salida:
            hora_txt_parts.append(f"Salida: {hora_salida}")
        if hora_txt_parts:
            draw.text((left_x, 14 + int(W * 0.035)), "  |  ".join(hora_txt_parts),
                       fill=(210, 215, 225, 255), font=font_body)

        right_text = f"{pair.upper()} {pips}".strip()
        draw.text((W - left_x, 14), right_text, fill=accent,
                   font=font_title, anchor="rt")
        draw.text((W - left_x, 14 + int(W * 0.035)),
                   ("COMPRA" if is_buy else "VENTA"),
                   fill=(210, 215, 225), font=font_body, anchor="rt")

        merged = Image.alpha_composite(img, overlay).convert("RGB")
        out_path = Path(str(chart_path)).with_name(Path(str(chart_path)).stem + "_ann.jpg")
        merged.save(str(out_path), "JPEG", quality=95)
        return out_path
    except Exception as _e_ann:
        log.warning(f"_annotate_chart_with_times error: {_e_ann}")
        try:
            return Path(str(chart_path))
        except Exception:
            return None


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
        ("An\u00e1lisis con IA",
         "Cada se\u00f1al pasa por filtros de IA\npara maximizar la probabilidad.",
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
        ("\u2713  An\u00e1lisis con IA en tiempo real", COLOR_GREEN),
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
    """Genera imagen compacta y profesional de promo Instagram para Telegram.
    Diseno minimalista: logo + handle + CTA. Sin listas largas."""
    import math

    # Imagen compacta (800x500) — se ve mejor en Telegram
    W, H = 800, 500
    img = Image.new("RGB", (W, H), (8, 10, 16))
    draw = ImageDraw.Draw(img)

    # ── Fondo: gradiente oscuro con tinte morado ──
    for y in range(H):
        t = y / H
        r = int(8 + 12 * t)
        g = int(10 + 6 * t)
        b = int(16 + 20 * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # ── Linea superior gradiente Instagram (fina, elegante) ──
    for y in range(4):
        ratio = y / 4
        r = int(240 - 52 * ratio)
        g = int(148 - 124 * ratio)
        b = int(51 + 85 * ratio)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # ── Tarjeta central ──
    card_m = 40
    _draw_rounded_rect(draw, [card_m, 28, W - card_m, H - 28],
                       radius=20, fill=(14, 17, 24), outline=(40, 44, 55), width=1)

    # ── Logo Instagram (compacto, 80px) ──
    ig_cx, ig_cy = W // 2, 105
    ig_size = 80
    corner_r = 18

    for _dy in range(-ig_size // 2, ig_size // 2):
        for _dx in range(-ig_size // 2, ig_size // 2):
            ax, ay = abs(_dx), abs(_dy)
            in_rect = True
            if ax > ig_size // 2 - corner_r and ay > ig_size // 2 - corner_r:
                dist = ((ax - (ig_size // 2 - corner_r)) ** 2 + (ay - (ig_size // 2 - corner_r)) ** 2) ** 0.5
                if dist > corner_r:
                    in_rect = False
            if in_rect:
                t = ((_dx + ig_size // 2) + (_dy + ig_size // 2)) / (ig_size * 2)
                cr = int(240 + (188 - 240) * t)
                cg = int(148 + (24 - 148) * t)
                cb = int(51 + (136 - 51) * t)
                img.putpixel((ig_cx + _dx, ig_cy + _dy), (cr, cg, cb))

    # Circulo lente
    for ang in range(360 * 4):
        a = ang / 4 * math.pi / 180
        for rad in range(20, 25):
            px = int(ig_cx + rad * math.cos(a))
            py = int(ig_cy + rad * math.sin(a))
            if 0 <= px < W and 0 <= py < H:
                img.putpixel((px, py), (255, 255, 255))

    # Flash dot
    for _dy2 in range(-4, 5):
        for _dx2 in range(-4, 5):
            if _dx2 ** 2 + _dy2 ** 2 <= 16:
                px2 = ig_cx + 24 + _dx2
                py2 = ig_cy - 24 + _dy2
                if 0 <= px2 < W and 0 <= py2 < H:
                    img.putpixel((px2, py2), (255, 255, 255))

    draw = ImageDraw.Draw(img)

    # ── Brand sutil arriba ──
    draw.text((W // 2, 48), "BUYSELL365 PRO", fill=(80, 88, 105),
              font=_get_font(16, bold=True), anchor="mt")

    # ── Handle ──
    draw.text((W // 2, 162), "@buysell365.pro_tradingsignals",
              fill=COLOR_WHITE, font=_get_font(22, bold=True), anchor="mt")

    # ── Linea separadora rosa ──
    draw.line([(W // 2 - 100, 198), (W // 2 + 100, 198)], fill=(225, 48, 108), width=2)

    # ── Subtitulo ──
    draw.text((W // 2, 218), "Resultados reales  |  TPs verificados  |  Sin filtros",
              fill=(120, 128, 145), font=_get_font(16), anchor="mt")

    # ── 3 Stats compactas ──
    stats_y = 268
    cols = [
        ("24/7", "Senales", COLOR_GREEN),
        ("100%", "Transparencia", (225, 48, 108)),
        ("VIP", "Canal Activo", COLOR_GOLD),
    ]
    col_w = (W - card_m * 2) // 3
    for i, (num, label, color) in enumerate(cols):
        cx = card_m + col_w * i + col_w // 2
        draw.text((cx, stats_y), num, fill=color,
                  font=_get_font(30, bold=True), anchor="mt")
        draw.text((cx, stats_y + 38), label, fill=(90, 98, 115),
                  font=_get_font(13), anchor="mt")

    # ── Boton CTA con gradiente Instagram ──
    btn_w, btn_h = 260, 42
    btn_x = (W - btn_w) // 2
    btn_y = 365
    for y in range(btn_h):
        for x in range(btn_w):
            ax2 = min(x, btn_w - 1 - x)
            ay2 = min(y, btn_h - 1 - y)
            r_btn = 21
            in_btn = True
            if ax2 < r_btn and ay2 < r_btn:
                if (r_btn - ax2) ** 2 + (r_btn - ay2) ** 2 > r_btn ** 2:
                    in_btn = False
            if in_btn:
                t = x / btn_w
                cr = int(240 - 52 * t)
                cg = int(148 - 124 * t)
                cb = int(51 + 85 * t)
                img.putpixel((btn_x + x, btn_y + y), (cr, cg, cb))

    draw = ImageDraw.Draw(img)
    draw.text((W // 2, btn_y + btn_h // 2), "Seguir en Instagram",
              fill=COLOR_WHITE, font=_get_font(17, bold=True), anchor="mm")

    # ── Footer ──
    draw.text((W // 2, H - 42), "buysell365.pro",
              fill=(50, 56, 68), font=_get_font(12), anchor="mt")

    filename = f"ig_promo_{int(time.time())}.jpg"
    filepath = IMAGES_DIR / filename
    img.save(filepath, "JPEG", quality=95)
    log.info(f"Instagram promo image generada -> {filename}")
    return filepath


# ── Sonido cha-ching (generado en memoria) ───────────────────

def _generate_cash_sound() -> Path:
    """Genera sonido ka-ching de caja registradora realista."""
    import numpy as np
    import wave
    cash_path = IMAGES_DIR / "cash_sound.wav"
    # Siempre regenerar para usar la version mejorada
    sr = 44100
    dur = 1.2
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    audio = np.zeros_like(t)

    # === PARTE 1: "Ka" — golpe metalico del boton (0-0.15s) ===
    ka_env = np.exp(-30 * t) * (t < 0.15).astype(float)
    audio += 0.6 * ka_env * np.sin(2 * np.pi * 800 * t)   # tono bajo del mecanismo
    audio += 0.3 * ka_env * np.sin(2 * np.pi * 1600 * t)  # armonico
    # Click metalico
    click_env = np.exp(-80 * t) * (t < 0.05).astype(float)
    audio += 0.4 * click_env * np.random.randn(len(t))

    # === PARTE 2: "CHING" — campana metalica brillante (0.12-1.2s) ===
    ching_start = 0.12
    ching_t = np.maximum(t - ching_start, 0)
    ching_env = np.exp(-3.5 * ching_t) * (t >= ching_start).astype(float)
    # Frecuencias de campana metalica (fundamental + armonicos inharmonicos)
    audio += 0.7 * ching_env * np.sin(2 * np.pi * 3520 * t)    # La7 fundamental
    audio += 0.5 * ching_env * np.sin(2 * np.pi * 5280 * t)    # armonico 1.5x
    audio += 0.35 * ching_env * np.sin(2 * np.pi * 7040 * t)   # 2x (octava)
    audio += 0.2 * ching_env * np.sin(2 * np.pi * 8800 * t)    # 2.5x
    audio += 0.15 * ching_env * np.sin(2 * np.pi * 10560 * t)  # 3x (brillo)
    # Shimmer metalico (modulacion de amplitud)
    shimmer = 1.0 + 0.15 * np.sin(2 * np.pi * 12 * t)
    audio *= shimmer

    # === PARTE 3: Cajon abriendo (ruido filtrado sutil 0.08-0.4s) ===
    drawer_env = np.exp(-8 * np.maximum(t - 0.08, 0)) * (t >= 0.08).astype(float) * (t < 0.5).astype(float)
    audio += 0.12 * drawer_env * np.random.randn(len(t))

    # === PARTE 4: Monedas cayendo (pings rapidos 0.25-0.6s) ===
    for i, (off, freq) in enumerate([(0.25, 6000), (0.32, 7200), (0.38, 5500), (0.43, 8000), (0.48, 6800)]):
        coin_env = np.exp(-25 * np.maximum(t - off, 0)) * (t >= off).astype(float)
        audio += 0.15 * coin_env * np.sin(2 * np.pi * freq * t)

    # Normalizar
    audio = audio / np.max(np.abs(audio)) * 0.9
    audio_16 = (audio * 32767).astype(np.int16)
    with wave.open(str(cash_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_16.tobytes())
    log.info("Sonido ka-ching generado")
    return cash_path


def _add_sound_to_video(video_path: Path) -> Path:
    """Agrega sonido ka-ching al video: a los 3s (pips aparecen) y 4.5s (resultado)."""
    import subprocess
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return video_path

    sound = _generate_cash_sound()
    output = video_path.with_name(video_path.stem + "_sound.mp4")
    # Sonido 1 a los 3s (pips en el grafico), sonido 2 a los 4.5s (pantalla resultado)
    cmd = [
        ffmpeg, "-y",
        "-i", str(video_path),
        "-i", str(sound),
        "-i", str(sound),
        "-filter_complex",
        "[1:a]adelay=3000|3000,volume=1.0[c1];"
        "[2:a]adelay=4500|4500,volume=0.7[c2];"
        "[c1][c2]amix=inputs=2:duration=longest[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(output)
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode == 0 and output.exists():
        log.info(f"Instagram: sonido ka-ching agregado -> {output.name}")
        return output
    log.warning(f"ffmpeg error: {result.stderr[-200:]}")
    return video_path


# ── Generador de Reels con velas reales MT5 ──────────────────

def _generate_tp_reel_video(pair: str, direction: str, pips: str,
                            tp_image_path: Path = None, duration_s: float = 8.0,
                            entry_price: float = 0, tp_price: float = 0,
                            fecha: str = "", hora_entrada: str = "",
                            hora_salida: str = "") -> Optional[Path]:
    """Genera Reel con velas reales de MT5 animadas + marca de agua + sonido cha-ching.
    Escena 1: Velas apareciendo una a una con entry/TP (4.5s)
    Escena 2: Resultado grande — par + pips (2s)
    Escena 3: CTA — unirse al VIP (1.5s)
    fecha/hora_entrada/hora_salida: se superponen en barra inferior durante todo el video.
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

        reel_path = IMAGES_DIR / f"reel_{pair.replace('/', '')}_{int(time.time())}.mp4"
        writer = imageio.get_writer(str(reel_path), fps=FPS, codec="libx264",
                                     quality=8, pixelformat="yuv420p",
                                     macro_block_size=2)
        # ── Obtener velas reales de MT5 ──
        candle_opens, candle_closes, candle_highs, candle_lows = None, None, None, None
        try:
            import MetaTrader5 as _mt5r
            # FIX 2026-04-16: Mapa completo — incluye pares con slash (display names) y forex
            _mt5r_map = {
            "GOLD": "GOLD", "XAUUSD": "GOLD", "ORO": "GOLD", "XAU/USD": "GOLD",
            "NAS100": "US100Cash", "NASDAQ": "US100Cash", "US100": "US100Cash", "NQ": "US100Cash",
            "US30": "US30Cash", "DOW": "US30Cash", "DJ30": "US30Cash",
            "US500": "US500Cash", "SP500": "US500Cash", "SPX500": "US500Cash",
            # Forex — manejar tanto "GBPUSD" como "GBP/USD" (display name)
            "EURUSD": "EURUSD", "EUR/USD": "EURUSD",
            "GBPUSD": "GBPUSD", "GBP/USD": "GBPUSD",
            "AUDUSD": "AUDUSD", "AUD/USD": "AUDUSD",
            "USDJPY": "USDJPY", "USD/JPY": "USDJPY",
            "USDCAD": "USDCAD", "USD/CAD": "USDCAD",
            "USDCHF": "USDCHF", "USD/CHF": "USDCHF",
            "NZDUSD": "NZDUSD", "NZD/USD": "NZDUSD",
            "GBPJPY": "GBPJPY", "GBP/JPY": "GBPJPY",
            "EURJPY": "EURJPY", "EUR/JPY": "EURJPY",
            "GBPAUD": "GBPAUD", "GBP/AUD": "GBPAUD",
            "GBPCAD": "GBPCAD", "GBP/CAD": "GBPCAD",
            "EURCAD": "EURCAD", "EUR/CAD": "EURCAD",
            "NZDCHF": "NZDCHF", "NZD/CHF": "NZDCHF",
            }
            # Limpiar slash del par antes de buscar en el mapa (fallback)
            _pair_clean = pair.upper().replace("/", "")
            _mt5r_sym = _mt5r_map.get(pair.upper(), _mt5r_map.get(_pair_clean, _pair_clean))
            if _mt5r.initialize():
                try:
                    _mt5r.symbol_select(_mt5r_sym, True)
                    _rates = _mt5r.copy_rates_from_pos(_mt5r_sym, _mt5r.TIMEFRAME_M15, 0, 40)
                finally:
                    _mt5r.shutdown()  # FIX 2026-04-16: shutdown siempre
                if _rates is not None and len(_rates) >= 10:
                    candle_opens = [float(r["open"]) for r in _rates]
                    candle_closes = [float(r["close"]) for r in _rates]
                    candle_highs = [float(r["high"]) for r in _rates]
                    candle_lows = [float(r["low"]) for r in _rates]
                    log.info(f"Reel: {len(_rates)} velas MT5 para {_mt5r_sym}")
        except Exception as _e_mt5r:
            log.debug(f"Reel: MT5 velas error: {_e_mt5r}")

        has_candles = candle_opens is not None
        n_candles = len(candle_opens) if has_candles else 0

        # Chart geometry — FIX 2026-04-16: Chart más grande, usar más espacio vertical
        chart_left, chart_right = 80, REEL_W - 40
        chart_top, chart_bot = 400, 1500
        chart_w = chart_right - chart_left
        chart_h = chart_bot - chart_top

        if has_candles:
            p_min = min(candle_lows) - (max(candle_highs) - min(candle_lows)) * 0.05
            p_max = max(candle_highs) + (max(candle_highs) - min(candle_lows)) * 0.05
            candle_w = max(4, chart_w // n_candles - 2)
        else:
            p_min, p_max, candle_w = 0, 1, 10

        def _p2y(price):
            return int(chart_bot - (price - p_min) / max(p_max - p_min, 0.001) * chart_h)

        # Pre-generar marca de agua
        wm_font = _get_font(90, bold=True)
        wm_layer = Image.new("RGBA", (REEL_W, REEL_H), (0, 0, 0, 0))
        wm_draw = ImageDraw.Draw(wm_layer)
        wm_color = (255, 255, 255, 18)
        for wy in range(-400, REEL_H + 400, 300):
            for wx in range(-200, REEL_W + 200, 700):
                wm_draw.text((wx, wy), "BUYSELL365", fill=wm_color,
                             font=wm_font, anchor="lt")
        wm_rotated = wm_layer.rotate(30, expand=False, center=(REEL_W // 2, REEL_H // 2))

        # Pre-generar overlay de fecha + horas (barra inferior persistente)
        time_overlay = None
        if fecha or hora_entrada or hora_salida:
            time_overlay = Image.new("RGBA", (REEL_W, REEL_H), (0, 0, 0, 0))
            t_draw = ImageDraw.Draw(time_overlay)
            bar_top = REEL_H - 180
            t_draw.rectangle([0, bar_top, REEL_W, REEL_H - 50], fill=(0, 0, 0, 210))
            t_draw.rectangle([0, bar_top, REEL_W, bar_top + 4], fill=dir_color)
            t_font_l = _get_font(36, bold=True)
            t_font_m = _get_font(30, bold=False)
            cy = bar_top + 30
            if fecha:
                t_draw.text((REEL_W // 2, cy), f"Fecha: {fecha}",
                            fill=(240, 240, 240, 255), font=t_font_l, anchor="mt")
                cy += 48
            hora_bits = []
            if hora_entrada:
                hora_bits.append(f"Entrada {hora_entrada}")
            if hora_salida:
                hora_bits.append(f"Salida {hora_salida}")
            if hora_bits:
                t_draw.text((REEL_W // 2, cy), "   |   ".join(hora_bits),
                            fill=(210, 215, 225, 255), font=t_font_m, anchor="mt")

        for frame_i in range(total_frames):
            sec = frame_i / FPS
            img = Image.new("RGB", (REEL_W, REEL_H), (10, 14, 22))
            draw = ImageDraw.Draw(img)

            # Fondo gradiente
            for y in range(0, REEL_H, 2):
                t = y / REEL_H
                c = (int(10 + 8 * t), int(14 + 6 * t), int(22 + 15 * t))
                draw.line([(0, y), (REEL_W, y)], fill=c)
                draw.line([(0, y + 1), (REEL_W, y + 1)], fill=c)

            # Marca de agua + overlay fecha/horas (persistente)
            img = img.convert("RGBA")
            img = Image.alpha_composite(img, wm_rotated)
            if time_overlay is not None:
                img = Image.alpha_composite(img, time_overlay)
            img = img.convert("RGB")
            draw = ImageDraw.Draw(img)

            # ── ESCENA 1: Velas animadas (0-4.5s) ──
            # FIX 2026-04-16: Layout expandido — chart más grande, menos espacio vacío
            if sec < 4.5 and has_candles:
                scene_t = sec / 4.5
                n_visible = max(1, int(scene_t * n_candles))

                draw.text((REEL_W // 2, 40), "BUYSELL365 PRO", fill=(80, 90, 110),
                          font=_get_font(28, bold=True), anchor="mt")
                draw.rounded_rectangle([100, 90, REEL_W - 100, 190], radius=20, fill=COLOR_GREEN)
                draw.text((REEL_W // 2, 140), "TP ALCANZADO", fill=(10, 15, 10),
                          font=_get_font(60, bold=True), anchor="mm")
                draw.text((REEL_W // 2, 230), pair.upper(), fill=COLOR_WHITE,
                          font=_get_font(90, bold=True), anchor="mt")
                draw.text((REEL_W // 2, 340), dir_label, fill=dir_color,
                          font=_get_font(44, bold=True), anchor="mt")

                # Chart box — más grande
                draw.rounded_rectangle(
                    [chart_left - 20, chart_top - 30, chart_right + 20, chart_bot + 30],
                    radius=15, fill=(14, 17, 24), outline=(30, 34, 45), width=1)
                for i in range(5):
                    gy = chart_top + int(chart_h * i / 4)
                    draw.line([(chart_left, gy), (chart_right, gy)], fill=(25, 30, 40), width=1)
                    pl = p_max - (p_max - p_min) * i / 4
                    draw.text((chart_right + 5, gy), f"{pl:.0f}", fill=(60, 66, 78),
                              font=_get_font(16), anchor="lm")

                # Candles — más grandes
                for i in range(min(n_visible, n_candles)):
                    x = chart_left + int(i * chart_w / n_candles)
                    o_y, c_y = _p2y(candle_opens[i]), _p2y(candle_closes[i])
                    h_y, l_y = _p2y(candle_highs[i]), _p2y(candle_lows[i])
                    bull = candle_closes[i] >= candle_opens[i]
                    color = COLOR_GREEN if bull else COLOR_RED
                    cx = x + candle_w // 2
                    draw.line([(cx, h_y), (cx, l_y)], fill=color, width=2)
                    tb, bb = min(o_y, c_y), max(o_y, c_y)
                    if bb - tb < 2:
                        bb = tb + 2
                    draw.rectangle([x, tb, x + candle_w, bb], fill=color)

                # Entry line
                if entry_price > 0 and p_min <= entry_price <= p_max:
                    ey = _p2y(entry_price)
                    for dx in range(chart_left, chart_right, 12):
                        draw.line([(dx, ey), (dx + 6, ey)], fill=COLOR_ACCENT, width=2)
                    draw.text((chart_left + 5, ey - 22), f"Entrada {entry_price:.0f}",
                              fill=COLOR_ACCENT, font=_get_font(20, bold=True))

                # TP line
                if tp_price > 0 and p_min <= tp_price <= p_max:
                    ty = _p2y(tp_price)
                    for dx in range(chart_left, chart_right, 12):
                        draw.line([(dx, ty), (dx + 6, ty)], fill=COLOR_GREEN, width=2)
                    draw.text((chart_left + 5, ty - 22), f"TP {tp_price:.0f}",
                              fill=COLOR_GREEN, font=_get_font(20, bold=True))

                # Pips fade in — debajo del chart expandido
                if scene_t > 0.6:
                    alpha = min((scene_t - 0.6) / 0.3, 1.0)
                    pc = tuple(int(c * alpha) for c in COLOR_GREEN)
                    draw.text((REEL_W // 2, chart_bot + 60), pips, fill=pc,
                              font=_get_font(100, bold=True), anchor="mt")

                # Info adicional abajo
                if scene_t > 0.8 and entry_price > 0:
                    draw.text((REEL_W // 2, chart_bot + 180), f"Entrada: {entry_price:.0f}  |  TP: {tp_price:.0f}",
                              fill=COLOR_GRAY, font=_get_font(30), anchor="mt")

                draw.text((REEL_W // 2, REEL_H - 60), "buysell365.pro",
                          fill=(40, 46, 58), font=_get_font(22), anchor="mt")

            # ── ESCENA 2: Resultado (4.5-6.5s) — layout expandido ──
            elif sec < 6.5:
                scene_t = (sec - 4.5) / 2.0
                draw.text((REEL_W // 2, 80), "BUYSELL365 PRO", fill=(80, 90, 110),
                          font=_get_font(32, bold=True), anchor="mt")
                draw.rounded_rectangle([80, 180, REEL_W - 80, 330], radius=25, fill=COLOR_GREEN)
                draw.text((REEL_W // 2, 255), "TP ALCANZADO", fill=(10, 15, 10),
                          font=_get_font(72, bold=True), anchor="mm")
                draw.text((REEL_W // 2, 420), pair.upper(), fill=COLOR_WHITE,
                          font=_get_font(110, bold=True), anchor="mt")
                draw.text((REEL_W // 2, 570), dir_label, fill=dir_color,
                          font=_get_font(54, bold=True), anchor="mt")
                # Caja de pips más grande y centrada
                draw.rounded_rectangle([60, 700, REEL_W - 60, 1100], radius=35,
                                       fill=(10, 50, 20), outline=COLOR_GREEN, width=3)
                draw.text((REEL_W // 2, 900), pips, fill=COLOR_GREEN,
                          font=_get_font(150, bold=True), anchor="mm")
                if scene_t > 0.3 and entry_price > 0:
                    draw.text((REEL_W // 2, 1180), f"Entrada: {entry_price:.0f}",
                              fill=COLOR_GRAY, font=_get_font(38), anchor="mt")
                    draw.text((REEL_W // 2, 1240), f"Take Profit: {tp_price:.0f}",
                              fill=COLOR_GREEN, font=_get_font(38), anchor="mt")
                if scene_t > 0.5:
                    draw.text((REEL_W // 2, 1380), "Senal exitosa del Canal VIP",
                              fill=COLOR_GOLD, font=_get_font(40, bold=True), anchor="mt")
                    draw.text((REEL_W // 2, 1460), "Unite — Link en bio",
                              fill=COLOR_GRAY, font=_get_font(32), anchor="mt")
                draw.text((REEL_W // 2, REEL_H - 80), "buysell365.pro",
                          fill=(50, 56, 68), font=_get_font(24), anchor="mt")

            # ── ESCENA 3: CTA (6.5-8s) — expandido ──
            else:
                scene_t = (sec - 6.5) / 1.5
                draw.text((REEL_W // 2, 80), "BUYSELL365 PRO", fill=(80, 90, 110),
                          font=_get_font(32, bold=True), anchor="mt")
                draw.text((REEL_W // 2, 250), pair.upper(), fill=COLOR_GRAY,
                          font=_get_font(60, bold=True), anchor="mt")
                draw.text((REEL_W // 2, 350), pips, fill=COLOR_GREEN,
                          font=_get_font(100, bold=True), anchor="mt")
                draw.line([(REEL_W // 2 - 200, 500), (REEL_W // 2 + 200, 500)],
                          fill=COLOR_GREEN, width=3)
                alpha = min(scene_t * 2, 1.0)
                cw = tuple(int(c * alpha) for c in COLOR_WHITE)
                draw.text((REEL_W // 2, 580), "Quieres recibir", fill=cw,
                          font=_get_font(52), anchor="mt")
                draw.text((REEL_W // 2, 660), "estas senales?", fill=cw,
                          font=_get_font(52), anchor="mt")
                if scene_t > 0.3:
                    draw.rounded_rectangle([150, 790, REEL_W - 150, 900], radius=35, fill=COLOR_GREEN)
                    draw.text((REEL_W // 2, 845), "UNIRSE AL CANAL VIP", fill=(10, 10, 16),
                              font=_get_font(38, bold=True), anchor="mm")
                if scene_t > 0.5:
                    draw.text((REEL_W // 2, 980), "Link en bio", fill=COLOR_GRAY,
                              font=_get_font(34), anchor="mt")
                    draw.text((REEL_W // 2, 1040), "@BUYSELL_365_24_7", fill=COLOR_ACCENT,
                              font=_get_font(38, bold=True), anchor="mt")
                    draw.text((REEL_W // 2, 1150), "Senales en vivo con entrada,",
                              fill=(100, 108, 120), font=_get_font(30), anchor="mt")
                    draw.text((REEL_W // 2, 1200), "TP y SL exactos",
                              fill=(100, 108, 120), font=_get_font(30), anchor="mt")
                draw.text((REEL_W // 2, REEL_H - 80), "buysell365.pro",
                          fill=(50, 56, 68), font=_get_font(24), anchor="mt")

            writer.append_data(np.array(img))

        writer.close()
        log.info(f"Instagram: Reel con velas reales generado -> {reel_path.name}")

        # Agregar sonido cha-ching
        final_path = _add_sound_to_video(reel_path)
        return final_path

    except Exception as e:
        # FIX 2026-04-16: Cerrar writer si falla durante la generación de frames
        try:
            writer.close()
        except Exception:
            pass
        log.warning(f"Instagram Reel generation error: {e}")
        return None


def _generate_tp_telegram_video(pair: str, direction: str, pips: str,
                                entry_price: float = 0, tp_price: float = 0,
                                duration_s: float = 6.0,
                                chart_image: bytes = None,
                                header_text: str = "TP ALCANZADO") -> Optional[Path]:
    """Genera video COMPACTO cuadrado (720x720) para Telegram CON VELAS ANIMADAS.
    Escena 1: Velas reales de MT5 apareciendo una a una (0-3.5s)
    Escena 2: Resultado grande + CTA (3.5-6s)
    Fallback: si no hay velas MT5, usa chart_image estática con zoom.
    """
    try:
        import imageio
        import numpy as np
    except ImportError:
        log.warning("Telegram video: imageio/numpy no disponible")
        return None

    try:
        TG_W, TG_H = 720, 720
        FPS = 24
        total_frames = int(duration_s * FPS)

        is_buy = direction.upper() in ("BUY", "COMPRA")
        dir_color = COLOR_GREEN if is_buy else COLOR_RED
        dir_label = "COMPRA" if is_buy else "VENTA"

        vid_path = IMAGES_DIR / f"tg_{pair.replace('/', '')}_{int(time.time())}.mp4"
        writer = imageio.get_writer(str(vid_path), fps=FPS, codec="libx264",
                                     quality=8, pixelformat="yuv420p",
                                     macro_block_size=2)

        # ── Obtener velas reales de MT5 ──
        candle_opens, candle_closes, candle_highs, candle_lows = None, None, None, None
        try:
            import MetaTrader5 as _mt5t
            _mt5_map = {
                "GOLD": "GOLD", "XAUUSD": "GOLD", "ORO": "GOLD", "XAU/USD": "GOLD",
                "NAS100": "US100Cash", "NASDAQ": "US100Cash", "US100": "US100Cash",
                "US30": "US30Cash", "DOW": "US30Cash",
                "US500": "US500Cash", "SP500": "US500Cash",
                "EURUSD": "EURUSD", "EUR/USD": "EURUSD",
                "GBPUSD": "GBPUSD", "GBP/USD": "GBPUSD",
                "AUDUSD": "AUDUSD", "AUD/USD": "AUDUSD",
                "USDJPY": "USDJPY", "USD/JPY": "USDJPY",
                "USDCAD": "USDCAD", "USD/CAD": "USDCAD",
                "USDCHF": "USDCHF", "USD/CHF": "USDCHF",
                "NZDUSD": "NZDUSD", "NZD/USD": "NZDUSD",
                "GBPJPY": "GBPJPY", "GBP/JPY": "GBPJPY",
                "EURJPY": "EURJPY", "EUR/JPY": "EURJPY",
                "GBPAUD": "GBPAUD", "GBP/AUD": "GBPAUD",
                "GBPCAD": "GBPCAD", "GBP/CAD": "GBPCAD",
                "EURCAD": "EURCAD", "EUR/CAD": "EURCAD",
                "NZDCHF": "NZDCHF", "NZD/CHF": "NZDCHF",
            }
            _pair_clean = pair.upper().replace("/", "")
            _sym = _mt5_map.get(pair.upper(), _mt5_map.get(_pair_clean, _pair_clean))
            if _mt5t.initialize():
                try:
                    _mt5t.symbol_select(_sym, True)
                    _rates = _mt5t.copy_rates_from_pos(_sym, _mt5t.TIMEFRAME_M15, 0, 30)
                finally:
                    _mt5t.shutdown()
                if _rates is not None and len(_rates) >= 10:
                    candle_opens = [float(r["open"]) for r in _rates]
                    candle_closes = [float(r["close"]) for r in _rates]
                    candle_highs = [float(r["high"]) for r in _rates]
                    candle_lows = [float(r["low"]) for r in _rates]
                    log.info(f"Telegram video: {len(_rates)} velas MT5 para {_sym}")
        except Exception as _e_mt5t:
            log.debug(f"Telegram video: MT5 velas error: {_e_mt5t}")

        has_candles = candle_opens is not None
        n_candles = len(candle_opens) if has_candles else 0

        # Chart geometry en cuadrado 720x720
        chart_left, chart_right = 40, TG_W - 60
        chart_top, chart_bot = 220, 560
        chart_w = chart_right - chart_left
        chart_h = chart_bot - chart_top

        if has_candles:
            p_min = min(candle_lows) - (max(candle_highs) - min(candle_lows)) * 0.05
            p_max = max(candle_highs) + (max(candle_highs) - min(candle_lows)) * 0.05
            candle_w = max(3, chart_w // n_candles - 2)
        else:
            p_min, p_max, candle_w = 0, 1, 10

        def _p2y(price):
            return int(chart_bot - (price - p_min) / max(p_max - p_min, 0.001) * chart_h)

        # ── Fallback: chart image estática (si no hay velas MT5) ──
        chart_img_base = None
        if not has_candles and chart_image:
            try:
                import io as _io_chart
                chart_img_base = Image.open(_io_chart.BytesIO(chart_image)).convert("RGB")
                chart_img_base = chart_img_base.resize((TG_W, TG_H), Image.LANCZOS)
                log.info(f"Telegram video: fallback gráfica estática (sin MT5)")
            except Exception as _e_chart:
                log.debug(f"Telegram video: error cargando gráfica: {_e_chart}")

        has_chart = chart_img_base is not None

        # Zoom suave: de 1.0 a 1.08 (solo para fallback estático)
        zoom_start, zoom_end = 1.0, 1.08

        for frame_i in range(total_frames):
            sec = frame_i / FPS

            # ── ESCENA 1: Velas MT5 animadas apareciendo una a una (0-3.5s) ──
            if sec < 3.5 and has_candles:
                scene_t = sec / 3.5
                n_visible = max(1, int(scene_t * n_candles))

                # Fondo oscuro con gradiente
                img = Image.new("RGB", (TG_W, TG_H), (10, 14, 22))
                draw = ImageDraw.Draw(img)
                for y in range(0, TG_H, 2):
                    t = y / TG_H
                    c = (int(10 + 8 * t), int(14 + 6 * t), int(22 + 15 * t))
                    draw.line([(0, y), (TG_W, y)], fill=c)

                # Header
                draw.text((TG_W // 2, 15), "BUYSELL365 PRO", fill=(80, 90, 110),
                          font=_get_font(18, bold=True), anchor="mt")
                draw.rounded_rectangle([60, 45, TG_W - 60, 110], radius=14, fill=COLOR_GREEN)
                draw.text((TG_W // 2, 77), header_text, fill=(10, 15, 10),
                          font=_get_font(32, bold=True), anchor="mm")
                draw.text((TG_W // 2, 125), f"{pair.upper()}  {dir_label}",
                          fill=COLOR_WHITE, font=_get_font(28, bold=True), anchor="mt")

                # Chart box
                draw.rounded_rectangle(
                    [chart_left - 10, chart_top - 15, chart_right + 15, chart_bot + 15],
                    radius=10, fill=(14, 17, 24), outline=(30, 34, 45), width=1)
                # Grid lines + precios
                for i in range(4):
                    gy = chart_top + int(chart_h * i / 3)
                    draw.line([(chart_left, gy), (chart_right, gy)], fill=(25, 30, 40), width=1)
                    pl = p_max - (p_max - p_min) * i / 3
                    draw.text((chart_right + 3, gy), f"{pl:.0f}" if pl >= 10 else f"{pl:.4f}",
                              fill=(60, 66, 78), font=_get_font(11), anchor="lm")

                # Velas — aparecen una por una
                for i in range(min(n_visible, n_candles)):
                    x = chart_left + int(i * chart_w / n_candles)
                    o_y, c_y = _p2y(candle_opens[i]), _p2y(candle_closes[i])
                    h_y, l_y = _p2y(candle_highs[i]), _p2y(candle_lows[i])
                    bull = candle_closes[i] >= candle_opens[i]
                    color = COLOR_GREEN if bull else COLOR_RED
                    cx = x + candle_w // 2
                    draw.line([(cx, h_y), (cx, l_y)], fill=color, width=1)
                    tb, bb = min(o_y, c_y), max(o_y, c_y)
                    if bb - tb < 2:
                        bb = tb + 2
                    draw.rectangle([x, tb, x + candle_w, bb], fill=color)

                # Líneas Entrada / TP
                if entry_price > 0 and p_min <= entry_price <= p_max:
                    e_y = _p2y(entry_price)
                    for _dx in range(0, chart_w, 8):
                        draw.line([(chart_left + _dx, e_y), (chart_left + _dx + 4, e_y)],
                                  fill=COLOR_ACCENT, width=2)
                    draw.text((chart_right - 2, e_y - 8), f"E {entry_price:.0f}",
                              fill=COLOR_ACCENT, font=_get_font(13, bold=True), anchor="rb")
                if tp_price > 0 and p_min <= tp_price <= p_max:
                    t_y = _p2y(tp_price)
                    for _dx in range(0, chart_w, 8):
                        draw.line([(chart_left + _dx, t_y), (chart_left + _dx + 4, t_y)],
                                  fill=COLOR_GOLD, width=2)
                    draw.text((chart_right - 2, t_y - 8), f"TP {tp_price:.0f}",
                              fill=COLOR_GOLD, font=_get_font(13, bold=True), anchor="rb")

                # Pips fade in (aparecen después del 50% de la escena)
                if scene_t > 0.5:
                    alpha_t = min((scene_t - 0.5) / 0.3, 1.0)
                    pc = tuple(int(c * alpha_t) for c in COLOR_GREEN)
                    draw.rounded_rectangle([100, 600, TG_W - 100, 680], radius=12,
                                           fill=(10, 40, 18), outline=COLOR_GREEN, width=2)
                    draw.text((TG_W // 2, 640), pips, fill=pc,
                              font=_get_font(44, bold=True), anchor="mm")

            # ── ESCENA 1 fallback: gráfica estática con zoom ──
            elif sec < 3.5 and has_chart:
                scene_t = sec / 3.5
                t_smooth = scene_t * scene_t * (3 - 2 * scene_t)
                zoom = zoom_start + (zoom_end - zoom_start) * t_smooth
                zoomed_w = int(TG_W * zoom)
                zoomed_h = int(TG_H * zoom)
                zoomed = chart_img_base.resize((zoomed_w, zoomed_h), Image.LANCZOS)
                cx = (zoomed_w - TG_W) // 2
                cy = (zoomed_h - TG_H) // 2
                img = zoomed.crop((cx, cy, cx + TG_W, cy + TG_H))
                draw = ImageDraw.Draw(img)
                for y in range(0, 110):
                    alpha = 0.75 - (y / 110) * 0.3
                    for x in range(TG_W):
                        px = img.getpixel((x, y))
                        img.putpixel((x, y), tuple(int(p * (1 - alpha)) for p in px))
                draw = ImageDraw.Draw(img)
                draw.text((TG_W // 2, 8), "BUYSELL365 PRO", fill=(180, 190, 200),
                          font=_get_font(16, bold=True), anchor="mt")
                draw.rounded_rectangle([100, 30, TG_W - 100, 80], radius=12, fill=COLOR_GREEN)
                draw.text((TG_W // 2, 55), header_text, fill=(10, 15, 10),
                          font=_get_font(32, bold=True), anchor="mm")
                draw.text((TG_W // 2, 92), f"{pair.upper()}  {dir_label}",
                          fill=COLOR_WHITE, font=_get_font(20, bold=True), anchor="mt")
                if scene_t > 0.5:
                    for y in range(TG_H - 100, TG_H):
                        alpha = ((y - (TG_H - 100)) / 100) * 0.8
                        for x in range(TG_W):
                            px = img.getpixel((x, y))
                            img.putpixel((x, y), tuple(int(p * (1 - alpha)) for p in px))
                    draw = ImageDraw.Draw(img)
                    alpha_t = min((scene_t - 0.5) / 0.3, 1.0)
                    pc = tuple(int(c * alpha_t) for c in COLOR_GREEN)
                    draw.text((TG_W // 2, TG_H - 55), pips, fill=pc,
                              font=_get_font(52, bold=True), anchor="mm")

            # ── ESCENA 1 sin gráfica: fondo oscuro con texto ──
            elif sec < 3.5:
                scene_t = sec / 3.5
                img = Image.new("RGB", (TG_W, TG_H), (13, 17, 23))
                draw = ImageDraw.Draw(img)
                for y in range(0, TG_H, 2):
                    t = y / TG_H
                    c = (int(10 + 8 * t), int(14 + 6 * t), int(22 + 15 * t))
                    draw.line([(0, y), (TG_W, y)], fill=c)

                draw.text((TG_W // 2, 80), "BUYSELL365 PRO", fill=(80, 90, 110),
                          font=_get_font(24, bold=True), anchor="mt")
                draw.rounded_rectangle([100, 140, TG_W - 100, 220], radius=16, fill=COLOR_GREEN)
                draw.text((TG_W // 2, 180), header_text, fill=(10, 15, 10),
                          font=_get_font(48, bold=True), anchor="mm")
                draw.text((TG_W // 2, 270), pair.upper(), fill=COLOR_WHITE,
                          font=_get_font(64, bold=True), anchor="mt")
                draw.text((TG_W // 2, 360), dir_label, fill=dir_color,
                          font=_get_font(36, bold=True), anchor="mt")
                if scene_t > 0.4:
                    draw.text((TG_W // 2, 460), pips, fill=COLOR_GREEN,
                              font=_get_font(80, bold=True), anchor="mt")
                if scene_t > 0.7 and entry_price > 0:
                    draw.text((TG_W // 2, 580), f"Entrada: {entry_price:.0f}  |  TP: {tp_price:.0f}",
                              fill=COLOR_GRAY, font=_get_font(22), anchor="mt")

            # ── ESCENA 2: Resultado + CTA (3.5-6s) ──
            else:
                scene_t = (sec - 3.5) / 2.5
                img = Image.new("RGB", (TG_W, TG_H), (10, 14, 22))
                draw = ImageDraw.Draw(img)
                for y in range(0, TG_H, 2):
                    t = y / TG_H
                    c = (int(10 + 8 * t), int(14 + 6 * t), int(22 + 15 * t))
                    draw.line([(0, y), (TG_W, y)], fill=c)

                draw.text((TG_W // 2, 15), "BUYSELL365 PRO", fill=(80, 90, 110),
                          font=_get_font(20, bold=True), anchor="mt")
                draw.rounded_rectangle([100, 50, TG_W - 100, 120], radius=16, fill=COLOR_GREEN)
                draw.text((TG_W // 2, 85), header_text, fill=(10, 15, 10),
                          font=_get_font(44, bold=True), anchor="mm")
                draw.text((TG_W // 2, 150), pair.upper(), fill=COLOR_WHITE,
                          font=_get_font(64, bold=True), anchor="mt")
                draw.text((TG_W // 2, 230), dir_label, fill=dir_color,
                          font=_get_font(34, bold=True), anchor="mt")

                draw.rounded_rectangle([60, 290, TG_W - 60, 460], radius=22,
                                       fill=(10, 50, 20), outline=COLOR_GREEN, width=2)
                draw.text((TG_W // 2, 375), pips, fill=COLOR_GREEN,
                          font=_get_font(90, bold=True), anchor="mm")

                if scene_t > 0.2 and entry_price > 0:
                    draw.text((TG_W // 2, 490), f"Entrada: {entry_price:.0f}",
                              fill=COLOR_GRAY, font=_get_font(24), anchor="mt")
                    draw.text((TG_W // 2, 525), f"Take Profit: {tp_price:.0f}",
                              fill=COLOR_GREEN, font=_get_font(24), anchor="mt")
                if scene_t > 0.4:
                    draw.text((TG_W // 2, 580), "Senal exitosa del Canal VIP",
                              fill=COLOR_GOLD, font=_get_font(26, bold=True), anchor="mt")
                if scene_t > 0.6:
                    alpha_cta = min((scene_t - 0.6) / 0.3, 1.0)
                    cw = tuple(int(c * alpha_cta) for c in COLOR_WHITE)
                    draw.text((TG_W // 2, 635), "Unite al VIP — /vip",
                              fill=cw, font=_get_font(22, bold=True), anchor="mt")
                    draw.text((TG_W // 2, 670), "buysell365.pro",
                              fill=tuple(int(c * alpha_cta) for c in (50, 56, 68)),
                              font=_get_font(18), anchor="mt")

            writer.append_data(np.array(img))

        writer.close()
        log.info(f"Telegram: Video compacto 720x720 con gráfica real -> {vid_path.name}")

        final_path = _add_sound_to_video(vid_path)
        return final_path

    except Exception as e:
        try:
            writer.close()
        except Exception:
            pass
        log.warning(f"Telegram video generation error: {e}")
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

# ── Traducción multi-idioma con Groq (ES → EN + PT) ───────────
_groq_ig_client = None

def _get_groq_client():
    global _groq_ig_client
    if _groq_ig_client is not None:
        return _groq_ig_client
    _key = os.getenv("GROQ_API_KEY", "")
    if not _key:
        return None
    try:
        from groq import Groq as _GroqClient
        _groq_ig_client = _GroqClient(api_key=_key)
        return _groq_ig_client
    except Exception as _e:
        log.debug(f"Groq no disponible para IG: {_e}")
        return None


def _translate_to_en_pt(text_es: str) -> tuple:
    """Traduce ES → (EN, PT) usando Groq. Si falla, devuelve ("", "")."""
    cl = _get_groq_client()
    if not cl or not text_es.strip():
        return ("", "")
    try:
        prompt = (
            "Translate this Spanish Instagram caption to English and Portuguese (Brazil). "
            "Keep emojis, line breaks, and tone. Return ONLY this exact format:\n"
            "EN:\n<english translation>\n---\nPT:\n<portuguese translation>\n\n"
            f"SPANISH CAPTION:\n{text_es}"
        )
        resp = cl.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            max_tokens=500,
            temperature=0.3,
        )
        raw = resp.choices[0].message.content.strip()
        en_part, pt_part = "", ""
        if "EN:" in raw and "PT:" in raw:
            after_en = raw.split("EN:", 1)[1]
            en_part = after_en.split("---", 1)[0].strip() if "---" in after_en else ""
            pt_part = raw.split("PT:", 1)[1].strip()
        return (en_part, pt_part)
    except Exception as _e:
        log.debug(f"Traducción Groq falló: {_e}")
        return ("", "")


def _build_multilang_caption(caption_es: str, hashtags: str = "") -> str:
    """Combina ES + EN + PT en una sola caption IG. Si falla traducción, solo ES."""
    en, pt = _translate_to_en_pt(caption_es.replace(hashtags, "").strip() if hashtags else caption_es)
    blocks = [caption_es.replace(hashtags, "").strip() if hashtags else caption_es]
    if en:
        blocks.append(f"━━━━━━━━━━\n🇬🇧 {en}")
    if pt:
        blocks.append(f"━━━━━━━━━━\n🇧🇷 {pt}")
    out = "\n\n".join(blocks)
    if hashtags and hashtags not in out:
        out += f"\n\n{hashtags}"
    # Instagram caption max 2200 chars
    return out[:2180]


def post_vip_signal_teaser_story(pair: str, direction: str, nivel: str = "PREMIUM") -> bool:
    """Publica SOLO una Story de teaser cuando hay nueva señal VIP.
    No revela entrada/SL/TP — solo dirige al link en bio. Preserva exclusividad VIP."""
    if not IG_ENABLED:
        return False

    def _do_post():
        with _ig_lock:
            try:
                cl = _get_client()
                if not cl:
                    log.warning("Instagram: no se pudo conectar para teaser Story VIP")
                    return

                # Imagen 1080x1920 (formato Story) con teaser
                img = Image.new("RGB", (1080, 1920), COLOR_BG)
                _draw_gradient_bg(img, (15, 20, 30), (5, 8, 14))
                draw = ImageDraw.Draw(img)

                dir_emoji = "🟢" if direction.upper() in ("COMPRA", "BUY") else "🔴"
                dir_text = "COMPRA / BUY" if direction.upper() in ("COMPRA", "BUY") else "VENTA / SELL"

                # Título
                f_title = _get_font(110, bold=True)
                f_pair = _get_font(180, bold=True)
                f_sub = _get_font(60, bold=True)
                f_cta = _get_font(72, bold=True)
                f_small = _get_font(44)

                draw.text((540, 280), "⚡ NUEVA SEÑAL VIP", font=f_title, fill=COLOR_GOLD, anchor="mm")
                draw.text((540, 420), "⚡ NEW VIP SIGNAL", font=f_small, fill=COLOR_GRAY, anchor="mm")

                # Par grande
                draw.text((540, 680), pair.upper(), font=f_pair, fill=COLOR_WHITE, anchor="mm")

                # Dirección
                color_dir = COLOR_GREEN if direction.upper() in ("COMPRA", "BUY") else COLOR_RED
                draw.text((540, 880), f"{dir_emoji} {dir_text}", font=f_sub, fill=color_dir, anchor="mm")

                # Nivel
                draw.text((540, 980), f"⭐ {nivel}", font=f_small, fill=COLOR_ACCENT, anchor="mm")

                # CTA bloque
                _draw_rounded_rect(draw, (140, 1280, 940, 1540), 40, fill=COLOR_ACCENT)
                draw.text((540, 1380), "🔓 DESBLOQUEA LA SEÑAL", font=f_cta, fill=COLOR_WHITE, anchor="mm")
                draw.text((540, 1460), "Link en bio · Link in bio", font=f_small, fill=COLOR_WHITE, anchor="mm")

                draw.text((540, 1720), "@BuySell365.pro", font=f_small, fill=COLOR_GRAY, anchor="mm")
                draw.text((540, 1790), "Señales · Signals · Sinais", font=f_small, fill=COLOR_GRAY, anchor="mm")

                story_path = IMAGES_DIR / f"teaser_{pair}_{int(time.time())}.jpg"
                img.save(str(story_path), "JPEG", quality=92)

                cl.photo_upload_to_story(story_path)
                log.info(f"Instagram: teaser Story VIP {pair} {direction} publicado OK")
            except Exception as _e:
                log.warning(f"Instagram teaser Story error: {_e}")

    threading.Thread(target=_do_post, daemon=True, name="ig_vip_teaser").start()
    return True


def post_tp_celebration(pair: str, direction: str, entry: float, tp: float,
                        pips: str, source: str = "", chart_path=None,
                        reel_entry: float = 0, reel_tp: float = 0,
                        is_gift: bool = False,
                        fecha: str = "", hora_entrada: str = "",
                        hora_salida: str = "") -> bool:
    """Publica TP en Instagram como UN SOLO carrusel [video + grafica anotada] +
    Story + Highlight (sin Reel separado, para evitar publicacion duplicada en feed).
    Si no se pasan fecha/horas se usan los de ahora mismo.
    chart_path: JPG del grafico. reel_entry/reel_tp: precios para el video animado."""
    if not IG_ENABLED:
        return False

    def _do_post():
        with _ig_lock:
            try:
                _fecha = fecha or datetime.now().strftime("%d/%m/%Y")
                _hs = hora_salida or datetime.now().strftime("%H:%M")
                _he = hora_entrada

                image_path = _generate_tp_image(pair, direction, entry, tp, pips, source)
                cl = _get_client()
                if not cl:
                    log.warning("Instagram: no se pudo conectar, imagen guardada localmente")
                    return

                hashtags = _get_hashtags("tp", pair)
                if is_gift:
                    caption_es = (
                        f"🎁 SENAL GRATIS GANADORA 🎁 {pair.upper()} {pips}\n\n"
                        f"Esta senal la regalamos HOY en nuestro grupo publico\n"
                        f"y fue GANADORA\n\n"
                        f"Todos los dias regalamos senales en el grupo\n"
                        f"Imaginate lo que pasa en el Canal VIP\n\n"
                        f"Unite — Link en bio\n"
                        f"Grupo GRATIS: @BUYSELL_365_24_7 en Telegram"
                    )
                else:
                    caption_es = (
                        f"TP ALCANZADO {pair.upper()} {pips}\n\n"
                        f"Otra senal exitosa de nuestro Canal VIP\n\n"
                        f"Nuestros miembros recibieron esta senal "
                        f"antes de que el mercado se moviera.\n\n"
                        f"Quieres recibir las proximas?\n"
                        f"Link en bio para unirte\n\n"
                        f"Grupo GRATIS: @BUYSELL_365_24_7 en Telegram"
                    )
                caption = _build_multilang_caption(caption_es, hashtags)

                # 1) Generar video con fecha/horas incrustadas
                _r_entry = reel_entry if reel_entry > 0 else entry
                _r_tp = reel_tp if reel_tp > 0 else tp
                reel_path = None
                try:
                    reel_path = _generate_tp_reel_video(
                        pair, direction, pips,
                        entry_price=_r_entry, tp_price=_r_tp,
                        fecha=_fecha, hora_entrada=_he, hora_salida=_hs)
                except Exception as _e_rlgen:
                    log.warning(f"Instagram: error generando video TP: {_e_rlgen}")

                # 2) Anotar grafica con fecha/horas (si hay chart)
                chart_annotated = None
                if chart_path and Path(str(chart_path)).exists():
                    chart_annotated = _annotate_chart_with_times(
                        chart_path, pair, direction, pips,
                        _fecha, _he, _hs)

                # 3) Publicar UN SOLO carrusel [video + chart anotado]
                try:
                    slides = []
                    if reel_path and Path(str(reel_path)).exists():
                        slides.append(Path(str(reel_path)))
                    if chart_annotated and Path(str(chart_annotated)).exists():
                        slides.append(Path(str(chart_annotated)))

                    if len(slides) >= 2:
                        cl.album_upload(slides, caption)
                        log.info(f"Instagram: TP {pair} CARRUSEL [video+grafica] publicado OK")
                    elif len(slides) == 1:
                        _only = slides[0]
                        if str(_only).lower().endswith(".mp4"):
                            thumbnail = _generate_reel_thumbnail(pair, pips, direction)
                            cl.clip_upload(_only, caption, thumbnail)
                            log.info(f"Instagram: TP {pair} VIDEO solo publicado OK (sin chart)")
                        else:
                            cl.photo_upload(_only, caption)
                            log.info(f"Instagram: TP {pair} GRAFICA sola publicada OK (sin video)")
                    else:
                        cl.photo_upload(image_path, caption)
                        log.info(f"Instagram: TP {pair} post fallback (imagen celebracion) OK")
                except Exception as _e_album:
                    log.warning(f"Instagram album error, fallback a foto: {_e_album}")
                    try:
                        cl.photo_upload(image_path, caption)
                        log.info(f"Instagram: TP {pair} post publicado OK (fallback)")
                    except Exception as _e_ph:
                        log.warning(f"Instagram fallback photo error: {_e_ph}")

                # 4) Story + Highlight (NO aparece en el feed, solo en Stories/Highlights)
                time.sleep(random.randint(3, 8))
                try:
                    story = cl.photo_upload_to_story(image_path)
                    story_id = story.pk if story else None
                    log.info(f"Instagram: TP {pair} Story publicada OK (id={story_id})")

                    if story_id:
                        time.sleep(random.randint(2, 5))
                        try:
                            global _highlight_tp_pk
                            _hl_pk = _get_or_create_tp_highlight(cl)
                            if _hl_pk:
                                cl.highlight_add_stories(_hl_pk, [str(story_id)])
                                log.info(f"Instagram: Story agregada al Highlight TPs")
                            else:
                                hl = cl.highlight_create("TPs", [str(story_id)])
                                _highlight_tp_pk = hl.pk
                                log.info(f"Instagram: Highlight 'TPs' CREADO (pk={hl.pk})")
                        except Exception as e_hl:
                            log.warning(f"Instagram Highlight error: {e_hl}")

                except Exception as e_st:
                    log.warning(f"Instagram Story error: {e_st}")

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

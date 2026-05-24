"""
Genera imagen 1080x1620 estilo ProSignalsFX con el calendario de noticias
de alto impacto del día + Market Overview + Fear & Greed + Outlook IA.
TODO en una sola imagen — formato preferido del usuario (FIX 2026-05-01).
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont

# ── Paleta estilo ProSignalsFX ──────────────────────────────
BG = (6, 6, 6)                  # negro
GOLD = (201, 166, 107)          # dorado suave (marcos + título)
GOLD_BRIGHT = (220, 186, 120)   # dorado más claro (subtítulo)
TXT = (240, 240, 240)           # texto blanco suave
TXT_DIM = (200, 200, 200)       # texto secundario
GRID = (90, 78, 54)             # líneas de tabla (dorado oscuro)
GREEN = (78, 205, 105)          # bullish
RED = (220, 90, 90)             # bearish
NEUTRAL = (180, 180, 180)       # neutral
ACCENT = (255, 200, 80)         # acento (insights)

W, H = 1080, 1920                # FIX 2026-05-01: extendida 1080->1920 para incluir overview+F&G+outlook+PROMO
PAD = 60
OUT_DIR = Path(__file__).parent / "ig_images"
OUT_DIR.mkdir(exist_ok=True)


def _font(size: int, bold: bool = False):
    cs = (["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"]
          if bold else
          ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"])
    for f in cs:
        if Path(f).exists():
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _font_emoji(size: int):
    """Carga fuente emoji con color (Segoe UI Emoji). Si falla, fallback regular."""
    for f in ("C:/Windows/Fonts/seguiemj.ttf",):
        if Path(f).exists():
            try:
                return ImageFont.truetype(f, size)
            except Exception:
                pass
    return _font(size, bold=False)


def _draw_emoji_label(draw, x, y, emoji, text, fe, ft, fill_text, gap=14):
    """Dibuja emoji (con embedded_color) + texto (con fill_text) en una linea.
    Retorna (ancho_total, alto)."""
    # Emoji con color
    try:
        draw.text((x, y), emoji, font=fe, embedded_color=True)
    except Exception:
        # Fallback sin embedded_color (texto monocromo)
        draw.text((x, y), emoji, font=fe, fill=fill_text)
    eb = draw.textbbox((0, 0), emoji, font=fe)
    ew = eb[2] - eb[0]
    eh = eb[3] - eb[1]
    # Texto a la derecha
    text_x = x + ew + gap
    # Centrar verticalmente texto vs emoji (emoji suele ser un poco más alto)
    tb = draw.textbbox((0, 0), text, font=ft)
    th = tb[3] - tb[1]
    text_y = y + max(0, (eh - th) // 2)
    draw.text((text_x, text_y), text, font=ft, fill=fill_text)
    tw = tb[2] - tb[0]
    return ew + gap + tw, max(eh, th)


# Paletas de bandera simplificada por moneda (se dibuja un mini-rect)
_FLAG_PALETTE = {
    "GBP": [(1, 33, 105), (255, 255, 255), (207, 20, 43)],   # Union Jack (azul/blanco/rojo)
    "USD": [(10, 49, 97), (255, 255, 255), (191, 10, 48)],   # Stars & Stripes
    "EUR": [(0, 51, 153), (255, 204, 0)],                     # azul + amarillo
    "CAD": [(255, 255, 255), (255, 0, 0)],                    # blanco + rojo
    "JPY": [(255, 255, 255), (188, 0, 45)],                   # blanco + círculo rojo
    "AUD": [(1, 33, 105), (255, 255, 255), (207, 20, 43)],   # similar a UK
    "NZD": [(1, 33, 105), (255, 255, 255), (207, 20, 43)],
    "CHF": [(218, 41, 28), (255, 255, 255)],                  # rojo + cruz blanca
    "CNY": [(222, 41, 16), (255, 222, 0)],                    # rojo + estrella amarilla
}


def _draw_flag(draw: ImageDraw.ImageDraw, cx: int, cy: int, w: int, h: int, currency: str):
    """Dibuja una mini-bandera 'estilo pictograma' — simple pero reconocible por colores."""
    currency = currency.upper()
    colors = _FLAG_PALETTE.get(currency, [(100, 100, 100), (60, 60, 60)])
    x0, y0 = cx - w // 2, cy - h // 2
    x1, y1 = x0 + w, y0 + h

    if currency == "JPY":
        draw.rectangle([x0, y0, x1, y1], fill=colors[0], outline=GOLD, width=2)
        r = min(w, h) // 3
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colors[1])
    elif currency == "EUR":
        draw.rectangle([x0, y0, x1, y1], fill=colors[0], outline=GOLD, width=2)
        # anillo de 8 estrellas simuladas como puntos amarillos
        import math
        for i in range(8):
            a = 2 * math.pi * i / 8
            sx = cx + int(min(w, h) * 0.28 * math.cos(a))
            sy = cy + int(min(w, h) * 0.28 * math.sin(a))
            draw.ellipse([sx - 3, sy - 3, sx + 3, sy + 3], fill=colors[1])
    elif currency == "CAD":
        draw.rectangle([x0, y0, x1, y1], fill=colors[0], outline=GOLD, width=2)
        # franjas rojas izquierda y derecha
        draw.rectangle([x0, y0, x0 + w // 4, y1], fill=colors[1])
        draw.rectangle([x1 - w // 4, y0, x1, y1], fill=colors[1])
        # "hoja" central simplificada (rombo rojo)
        cw = w // 6
        draw.polygon([(cx, cy - cw), (cx + cw, cy), (cx, cy + cw), (cx - cw, cy)], fill=colors[1])
    elif currency in ("GBP", "AUD", "NZD"):
        # FIX 2026-05-01: el dibujo anterior solo tenia cruz horizontal+vertical,
        # parecia bandera de Islandia/Suecia (cruz nordica). Faltaban las DIAGONALES
        # tipicas del Union Jack (St. Andrew's saltire + St. Patrick's saltire).
        draw.rectangle([x0, y0, x1, y1], fill=colors[0], outline=GOLD, width=2)
        # Diagonales blancas (St. Andrew's + St. Patrick's saltire)
        draw.line([(x0, y0), (x1, y1)], fill=colors[1], width=5)
        draw.line([(x0, y1), (x1, y0)], fill=colors[1], width=5)
        # Diagonales rojas (St. Patrick's mas delgada)
        draw.line([(x0, y0), (x1, y1)], fill=colors[2], width=2)
        draw.line([(x0, y1), (x1, y0)], fill=colors[2], width=2)
        # Cruz blanca (St. George's) por encima — mas ancha
        draw.rectangle([x0, cy - 5, x1, cy + 5], fill=colors[1])
        draw.rectangle([cx - 5, y0, cx + 5, y1], fill=colors[1])
        # Cruz roja interior (St. George's) — mas delgada
        draw.rectangle([x0, cy - 2, x1, cy + 2], fill=colors[2])
        draw.rectangle([cx - 2, y0, cx + 2, y1], fill=colors[2])
        if currency in ("AUD", "NZD"):
            # Marca distintiva: 2 puntos blancos abajo-derecha (estrellas Southern Cross)
            sr = max(2, min(w, h) // 14)
            draw.ellipse([x1 - 18 - sr, y1 - 14 - sr, x1 - 18 + sr, y1 - 14 + sr], fill=colors[1])
            draw.ellipse([x1 - 8 - sr, y1 - 24 - sr, x1 - 8 + sr, y1 - 24 + sr], fill=colors[1])
    elif currency == "USD":
        draw.rectangle([x0, y0, x1, y1], fill=colors[1], outline=GOLD, width=2)
        # franjas rojas horizontales alternas
        stripe_h = h // 7
        for i in range(1, 7, 2):
            draw.rectangle([x0, y0 + i * stripe_h, x1, y0 + (i + 1) * stripe_h], fill=colors[2])
        # cantón azul arriba-izquierda
        draw.rectangle([x0, y0, x0 + w * 2 // 5, y0 + stripe_h * 4], fill=colors[0])
    elif currency == "CHF":
        draw.rectangle([x0, y0, x1, y1], fill=colors[0], outline=GOLD, width=2)
        cs = min(w, h) // 4
        draw.rectangle([cx - 3, cy - cs, cx + 3, cy + cs], fill=colors[1])
        draw.rectangle([cx - cs, cy - 3, cx + cs, cy + 3], fill=colors[1])
    else:
        draw.rectangle([x0, y0, x1, y1], fill=colors[0], outline=GOLD, width=2)


def _text_size(draw, text, font) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap_text(draw, text: str, font, max_width: int) -> List[str]:
    """Envuelve texto en múltiples líneas que caben en max_width."""
    lines = []
    cur_line = ""
    for word in text.split():
        trial = (cur_line + " " + word).strip()
        tw, _ = _text_size(draw, trial, font)
        if tw > max_width and cur_line:
            lines.append(cur_line)
            cur_line = word
        else:
            cur_line = trial
    if cur_line:
        lines.append(cur_line)
    return lines


def generar_imagen_noticias(
    eventos: List[Tuple[str, str, str]],
    fecha: str,
    fname: str = "noticias_dia.jpg",
    # FIX 2026-05-01: parametros nuevos para integrar TODO en la imagen
    greeting_label: str = "GOOD MORNING",
    greeting_emoji: str = "🌅",
    bullish: Optional[List[Tuple[str, float]]] = None,   # [(nombre, rsi), ...]
    bearish: Optional[List[Tuple[str, float]]] = None,
    neutral: Optional[List[Tuple[str, float]]] = None,
    fear_greed_value: int = 50,
    fear_greed_label: str = "Neutral",
    outlook: str = "",
    hora_local: str = "",
) -> Path:
    """
    Genera la imagen completa del briefing matutino con TODOS los elementos
    (saludo, eventos, panorama, F&G, outlook) en una sola imagen 1080x1620.

    eventos: lista de tuplas (hora_HHMM, currency_3letras, titulo_evento), máx 6.
    fecha:   string "DD / MM / YYYY".
    greeting_label: "GOOD MORNING" / "GOOD AFTERNOON" / "GOOD EVENING"
    greeting_emoji: emoji asociado al saludo (🌅 / 🌞 / 🌙)
    bullish/bearish/neutral: lista de pares con su RSI
    fear_greed_value: 0-100
    fear_greed_label: "Extreme Fear" / "Fear" / "Neutral" / "Greed" / "Extreme Greed"
    outlook: texto del análisis IA (max ~300 chars recomendado)
    hora_local: hora actual "HH:MM" (Andorra)
    """
    eventos = eventos[:6]
    bullish = bullish or []
    bearish = bearish or []
    neutral = neutral or []

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Marco dorado
    d.rectangle([10, 10, W - 10, H - 10], outline=GOLD, width=3)

    # ── HEADER ─────────────────────────────────────
    f_title = _font(82, bold=True)
    f_sub = _font(38, bold=False)
    f_date = _font(26, bold=False)
    title = "BuySell365 Pro"
    tw, th = _text_size(d, title, f_title)
    d.text(((W - tw) // 2, 60), title, font=f_title, fill=GOLD)

    sub = "H I G H   I M P A C T   N E W S"
    sw, sh = _text_size(d, sub, f_sub)
    d.text(((W - sw) // 2, 60 + th + 10), sub, font=f_sub, fill=GOLD_BRIGHT)

    # Fecha
    dw, dh = _text_size(d, fecha, f_date)
    d.text(((W - dw) // 2, 60 + th + 10 + sh + 10), fecha, font=f_date, fill=TXT_DIM)

    # ── GREETING (FIX 2026-05-01) ─────────────────
    f_greet = _font(34, bold=True)
    f_greet_emoji = _font_emoji(34)
    greeting_text = f"{greeting_label}"
    if hora_local:
        greeting_text += f"  ·  {hora_local}"
    # Calcular ancho total para centrar (emoji + gap + texto)
    eb = d.textbbox((0, 0), greeting_emoji, font=f_greet_emoji)
    ew = eb[2] - eb[0]
    tb = d.textbbox((0, 0), greeting_text, font=f_greet)
    tw = tb[2] - tb[0]
    total_w = ew + 18 + tw
    greet_y = 280
    start_x = (W - total_w) // 2
    _draw_emoji_label(d, start_x, greet_y, greeting_emoji, greeting_text,
                      f_greet_emoji, f_greet, ACCENT, gap=18)
    gh = max(eb[3] - eb[1], tb[3] - tb[1])

    # ── TABLA DE EVENTOS ─────────────────────────────────────
    table_top = greet_y + gh + 35
    table_bottom = table_top + 700  # 700px para tabla
    col_time_x = PAD
    col_cur_x = 260
    col_evt_x = 500
    col_evt_max = W - PAD - col_evt_x

    # Cabecera
    f_head = _font(34, bold=True)
    d.text((col_time_x + 30, table_top + 18), "TIME", font=f_head, fill=GOLD_BRIGHT)
    d.text((col_cur_x + 30, table_top + 18), "CURRENCY", font=f_head, fill=GOLD_BRIGHT)
    d.text((col_evt_x + 10, table_top + 18), "EVENTS", font=f_head, fill=GOLD_BRIGHT)

    header_h = 80
    # Líneas de cabecera
    d.line([PAD, table_top, W - PAD, table_top], fill=GRID, width=2)
    d.line([PAD, table_top + header_h, W - PAD, table_top + header_h], fill=GRID, width=2)

    # Filas
    n = max(len(eventos), 1)
    row_h = (table_bottom - (table_top + header_h)) // max(n, 1)
    f_time = _font(34, bold=True)
    f_cur = _font(28, bold=True)
    f_evt = _font(24, bold=False)

    y = table_top + header_h
    for i, (hora, cur, titulo) in enumerate(eventos):
        # Línea inferior de fila
        d.line([PAD, y + row_h, W - PAD, y + row_h], fill=GRID, width=1)

        # TIME
        d.text((col_time_x + 30, y + row_h // 2 - 18), hora, font=f_time, fill=TXT)

        # CURRENCY: bandera + código
        flag_w, flag_h = 64, 44
        _draw_flag(d, col_cur_x + 30, y + row_h // 2, flag_w, flag_h, cur)
        d.text((col_cur_x + 30 + flag_w // 2 + 15, y + row_h // 2 - 14), cur, font=f_cur, fill=TXT)

        # EVENTS (envolver si no cabe)
        lines = _wrap_text(d, titulo, f_evt, col_evt_max)[:2]
        total_h = 30 * len(lines)
        start_y = y + row_h // 2 - total_h // 2
        for li, ln in enumerate(lines):
            d.text((col_evt_x + 10, start_y + li * 30), ln, font=f_evt, fill=TXT)

        y += row_h

    # Líneas verticales entre columnas
    d.line([col_cur_x, table_top, col_cur_x, table_bottom], fill=GRID, width=1)
    d.line([col_evt_x, table_top, col_evt_x, table_bottom], fill=GRID, width=1)

    # ── MARKET OVERVIEW (FIX 2026-05-01) ─────────────────────────────
    f_section_title = _font(28, bold=True)
    f_overview = _font(22, bold=False)
    section_y = table_bottom + 30

    # Separador dorado
    d.line([PAD + 100, section_y, W - PAD - 100, section_y], fill=GOLD, width=1)
    section_y += 18

    f_section_emoji = _font_emoji(28)
    eb_o = d.textbbox((0, 0), "📊", font=f_section_emoji)
    eb_w = eb_o[2] - eb_o[0]
    tb_o = d.textbbox((0, 0), "MARKET OVERVIEW", font=f_section_title)
    tw_o = tb_o[2] - tb_o[0]
    total_o = eb_w + 14 + tw_o
    _draw_emoji_label(d, (W - total_o) // 2, section_y, "📊", "MARKET OVERVIEW",
                      f_section_emoji, f_section_title, GOLD_BRIGHT, gap=14)
    oh = max(eb_o[3] - eb_o[1], tb_o[3] - tb_o[1])
    section_y += oh + 18

    def _format_pairs(pairs, max_chars=70):
        """Formatea lista de (nombre, rsi) en una string."""
        parts = [f"{n} (RSI {r:.0f})" for n, r in pairs]
        joined = ", ".join(parts)
        if len(joined) > max_chars:
            joined = joined[:max_chars - 3] + "..."
        return joined

    f_ov_emoji = _font_emoji(22)
    if bullish:
        text = f"Bullish: {_format_pairs(bullish)}"
        _draw_emoji_label(d, PAD + 30, section_y, "🟢", text, f_ov_emoji, f_overview, GREEN, gap=10)
        section_y += 32
    if bearish:
        text = f"Bearish: {_format_pairs(bearish)}"
        _draw_emoji_label(d, PAD + 30, section_y, "🔴", text, f_ov_emoji, f_overview, RED, gap=10)
        section_y += 32
    if neutral:
        text = f"Neutral: {_format_pairs(neutral)}"
        _draw_emoji_label(d, PAD + 30, section_y, "⚪", text, f_ov_emoji, f_overview, NEUTRAL, gap=10)
        section_y += 32

    # ── FEAR & GREED ─────────────────────────────────────
    section_y += 8
    fg_emoji = "😱" if fear_greed_value <= 25 else ("😨" if fear_greed_value <= 45 else
               ("😐" if fear_greed_value <= 55 else ("😎" if fear_greed_value <= 75 else "🤑")))
    fg_color = (RED if fear_greed_value <= 25 else
                (ACCENT if fear_greed_value <= 45 else
                 (NEUTRAL if fear_greed_value <= 55 else
                  (GREEN if fear_greed_value <= 75 else GREEN))))
    fg_text = f"Fear & Greed:  {fear_greed_value}/100  ({fear_greed_label})"
    f_fg_emoji = _font_emoji(22)
    eb_fg = d.textbbox((0, 0), fg_emoji, font=f_fg_emoji)
    tb_fg = d.textbbox((0, 0), fg_text, font=f_overview)
    total_fg = (eb_fg[2] - eb_fg[0]) + 12 + (tb_fg[2] - tb_fg[0])
    _draw_emoji_label(d, (W - total_fg) // 2, section_y, fg_emoji, fg_text,
                      f_fg_emoji, f_overview, fg_color, gap=12)
    fgh = max(eb_fg[3] - eb_fg[1], tb_fg[3] - tb_fg[1])
    section_y += fgh + 30

    # ── OUTLOOK ────────────────────────────────────────────
    if outlook:
        # Separador dorado
        d.line([PAD + 100, section_y, W - PAD - 100, section_y], fill=GOLD, width=1)
        section_y += 16

        eb_ol = d.textbbox((0, 0), "💡", font=f_section_emoji)
        tb_ol = d.textbbox((0, 0), "OUTLOOK", font=f_section_title)
        total_ol = (eb_ol[2] - eb_ol[0]) + 14 + (tb_ol[2] - tb_ol[0])
        _draw_emoji_label(d, (W - total_ol) // 2, section_y, "💡", "OUTLOOK",
                          f_section_emoji, f_section_title, GOLD_BRIGHT, gap=14)
        oth = max(eb_ol[3] - eb_ol[1], tb_ol[3] - tb_ol[1])
        section_y += oth + 14

        # Texto outlook envuelto a 4 líneas máx
        f_outlook = _font(22, bold=False)
        outlook_max_w = W - PAD * 2 - 40
        outlook_lines = _wrap_text(d, outlook, f_outlook, outlook_max_w)[:4]
        for ln in outlook_lines:
            d.text((PAD + 30, section_y), ln, font=f_outlook, fill=TXT)
            section_y += 30

    # ── BLOQUE PROMO VIP (FIX 2026-05-01) ─────────────────
    # Caja dorada destacada con beneficios + CTA — formato permanente
    section_y += 25
    promo_box_top = section_y
    promo_box_bottom = H - 110  # deja espacio para footer
    promo_box_left = PAD + 30
    promo_box_right = W - PAD - 30

    # Fondo de la caja (gris muy oscuro con borde dorado)
    d.rectangle([promo_box_left, promo_box_top, promo_box_right, promo_box_bottom],
                fill=(15, 12, 8), outline=GOLD, width=2)

    # Título promo dentro de la caja
    f_promo_title = _font(30, bold=True)
    f_promo_emoji = _font_emoji(30)
    f_promo_body = _font(22, bold=False)
    f_promo_cta = _font(26, bold=True)

    promo_y = promo_box_top + 22

    # Línea 1: 💎 JOIN THE VIP CHANNEL
    eb_p = d.textbbox((0, 0), "💎", font=f_promo_emoji)
    tb_p = d.textbbox((0, 0), "JOIN THE VIP CHANNEL", font=f_promo_title)
    total_p = (eb_p[2] - eb_p[0]) + 14 + (tb_p[2] - tb_p[0])
    _draw_emoji_label(d, (W - total_p) // 2, promo_y, "💎", "JOIN THE VIP CHANNEL",
                      f_promo_emoji, f_promo_title, GOLD_BRIGHT, gap=14)
    promo_y += max(eb_p[3] - eb_p[1], tb_p[3] - tb_p[1]) + 18

    # Bullets de beneficios (con emojis)
    f_bullet_emoji = _font_emoji(22)
    bullets = [
        ("📡", "Real-time signals · exact Entry · TP · SL"),
        ("🎯", "Verified Win Rate (audited on MT5)"),
        ("🔍", "On-demand analysis of any asset"),
        ("🤖", "24/7 AI assistant — answers in your language"),
    ]
    bullet_x = promo_box_left + 50
    for emoji, txt in bullets:
        _draw_emoji_label(d, bullet_x, promo_y, emoji, txt,
                          f_bullet_emoji, f_promo_body, TXT, gap=12)
        promo_y += 32

    # CTA final
    promo_y += 8
    cta_text = "Type /vip to start trading with us"
    eb_cta = d.textbbox((0, 0), "👉", font=f_promo_emoji)
    tb_cta = d.textbbox((0, 0), cta_text, font=f_promo_cta)
    total_cta = (eb_cta[2] - eb_cta[0]) + 14 + (tb_cta[2] - tb_cta[0])
    _draw_emoji_label(d, (W - total_cta) // 2, promo_y, "👉", cta_text,
                      f_promo_emoji, f_promo_cta, ACCENT, gap=14)

    # ── FOOTER ─────────────────────────────────────────────
    f_foot = _font(22, bold=False)
    foot_sources = "Sources: ForexFactory · CNN F&G · Internal MT5"
    fsw, fsh = _text_size(d, foot_sources, f_foot)
    d.text(((W - fsw) // 2, H - 80), foot_sources, font=f_foot, fill=TXT_DIM)

    foot = "buysell365.pro"
    fw, fh = _text_size(d, foot, f_foot)
    d.text(((W - fw) // 2, H - 45), foot, font=f_foot, fill=GOLD)

    out = OUT_DIR / fname
    img.save(out, "JPEG", quality=92)
    return out


# ════════════════════════════════════════════════════════════════════
# V4 — DAILY MARKET REPORT (2026-05-12) — replaces the old briefing
# Same branding but English-only, with Previous/Forecast/Actual, affected
# pairs per event, market overview, F&G with insight, and trading plan.
# No more "JOIN VIP" block (was redundant for VIPs, kept only as caption
# CTA on the public group).
# ════════════════════════════════════════════════════════════════════

# Country code → readable country name (English)
_COUNTRY_NAME_EN = {
    "USD": "USA", "EUR": "Eurozone", "GBP": "UK",
    "JPY": "Japan", "CAD": "Canada", "AUD": "Australia",
    "NZD": "N.Zealand", "CHF": "Switzerland", "CNY": "China",
}

# Country code → affected pairs (transparent, actionable)
_COUNTRY_TO_PAIRS = {
    "USD": "GOLD · NAS100 · US30 · EUR/USD · USDJPY",
    "EUR": "EUR/USD · EUR/JPY · EUR/GBP · DAX",
    "GBP": "GBP/USD · EUR/GBP · GBP/JPY · UK100",
    "JPY": "USDJPY · EUR/JPY · GBP/JPY · JP225",
    "CAD": "USD/CAD · BRENT · WTI",
    "AUD": "AUD/USD · AUD/JPY · GOLD",
    "NZD": "NZD/USD · AUD/NZD",
    "CHF": "USD/CHF · EUR/CHF",
    "CNY": "GOLD · AUD/USD · NAS100 · BTC",
}


def _normalize_title_en(t: str) -> str:
    """Convert cryptic ForexFactory tags into readable English."""
    t = t or ""
    repl = [
        (" y/y", " (YoY)"),
        (" m/m", " (MoM)"),
        (" q/q", " (QoQ)"),
        ("Core CPI", "Core CPI (Inflation)"),
        ("CPI (YoY)", "CPI – Inflation (YoY)"),
        ("CPI (MoM)", "CPI – Inflation (MoM)"),
        ("PPI (YoY)", "PPI – Producer Prices (YoY)"),
        ("PPI (MoM)", "PPI – Producer Prices (MoM)"),
        ("Non-Farm Employment Change", "Non-Farm Payrolls (NFP)"),
        ("Federal Funds Rate", "Fed Funds Rate"),
    ]
    for a, b in repl:
        t = t.replace(a, b)
    return t


def generar_imagen_briefing_v2(
    eventos: List[dict],
    fecha: str,
    fname: str = "briefing.jpg",
    greeting_label: str = "GOOD MORNING",
    greeting_emoji: str = "☀️",
    hora_local: str = "",
    bullish: Optional[List[Tuple[str, float]]] = None,
    bearish: Optional[List[Tuple[str, float]]] = None,
    neutral: Optional[List[Tuple[str, float]]] = None,
    fear_greed_value: int = 50,
    fear_greed_label: str = "Neutral",
    fg_insight: str = "",
    avoid_hours: Optional[List[str]] = None,
    best_session: str = "London 09:00 – 12:00 GMT",
    focus_pairs: str = "GOLD + NAS100 (clearest bias)",
) -> Path:
    """
    Genera la imagen v4 — Daily Market Report en ingles con datos
    Previous/Forecast/Actual + pares afectados + plan del dia.

    eventos: lista de dicts con keys: hora, pais (3-letter), titulo,
             impacto ('high'|'medium'), previous, forecast, actual.
             Max 5 mostrados.
    """
    bullish = bullish or []
    bearish = bearish or []
    neutral = neutral or []
    avoid_hours = avoid_hours or []
    eventos = (eventos or [])[:5]

    EVENT_ROW_H = 145
    H_FIXED_TOP = 720
    H_OVERVIEW_PLAN = 480
    H_FOOTER = 130
    H_DYN = H_FIXED_TOP + (EVENT_ROW_H * max(len(eventos), 1)) + H_OVERVIEW_PLAN + H_FOOTER
    H_FINAL = max(H_DYN, 1500)

    img = Image.new("RGB", (W, H_FINAL), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, W - 10, H_FINAL - 10], outline=GOLD, width=3)

    # ── HEADER ──
    f_title = _font(82, bold=True)
    f_sub = _font(34, bold=False)
    f_date = _font(26, bold=False)

    title = "BuySell365 Pro"
    tw, th = _text_size(d, title, f_title)
    d.text(((W - tw) // 2, 60), title, font=f_title, fill=GOLD)

    sub = "M A R K E T   R E P O R T"
    sw, sh = _text_size(d, sub, f_sub)
    d.text(((W - sw) // 2, 60 + th + 10), sub, font=f_sub, fill=GOLD_BRIGHT)

    dw, dh = _text_size(d, fecha, f_date)
    d.text(((W - dw) // 2, 60 + th + 10 + sh + 10), fecha, font=f_date, fill=TXT_DIM)

    # ── GREETING ──
    f_greet = _font(34, bold=True)
    f_greet_emj = _font_emoji(34)
    g_txt = greeting_label
    if hora_local:
        g_txt += f"  ·  {hora_local}"
    eb = d.textbbox((0, 0), greeting_emoji, font=f_greet_emj)
    tb = d.textbbox((0, 0), g_txt, font=f_greet)
    total_g = (eb[2] - eb[0]) + 18 + (tb[2] - tb[0])
    greet_y = 280
    _draw_emoji_label(d, (W - total_g) // 2, greet_y, greeting_emoji, g_txt,
                      f_greet_emj, f_greet, ACCENT, gap=18)
    gh = max(eb[3] - eb[1], tb[3] - tb[1])

    # ── SECTION 1: TODAY'S NEWS ──
    section_y = greet_y + gh + 40
    f_section = _font(28, bold=True)
    f_section_emj = _font_emoji(28)

    d.line([PAD + 100, section_y, W - PAD - 100, section_y], fill=GOLD, width=1)
    section_y += 18

    eb_o = d.textbbox((0, 0), "📅", font=f_section_emj)
    tb_o = d.textbbox((0, 0), "TODAY'S NEWS", font=f_section)
    total_o = (eb_o[2] - eb_o[0]) + 14 + (tb_o[2] - tb_o[0])
    _draw_emoji_label(d, (W - total_o) // 2, section_y, "📅", "TODAY'S NEWS",
                      f_section_emj, f_section, GOLD_BRIGHT, gap=14)
    section_y += max(eb_o[3] - eb_o[1], tb_o[3] - tb_o[1]) + 24

    f_evt_hora = _font(28, bold=True)
    f_evt_country = _font(22, bold=True)
    f_evt_titulo = _font(22, bold=True)
    f_evt_data = _font(20, bold=False)
    f_evt_pares = _font(18, bold=False)
    f_evt_emj_small = _font_emoji(18)
    f_evt_emj_data = _font_emoji(20)

    if not eventos:
        f_empty = _font(26, bold=True)
        f_empty_emj = _font_emoji(26)
        txt_empty = "No critical news today — clean sessions"
        teb = d.textbbox((0, 0), "✅", font=f_empty_emj)
        ttb = d.textbbox((0, 0), txt_empty, font=f_empty)
        total_e = (teb[2] - teb[0]) + 14 + (ttb[2] - ttb[0])
        _draw_emoji_label(d, (W - total_e) // 2, section_y + 10, "✅", txt_empty,
                          f_empty_emj, f_empty, GREEN, gap=14)
        section_y += 100
    else:
        for e in eventos:
            dot_color = RED if e.get("impacto") == "high" else (255, 165, 0)
            d.ellipse([PAD + 20, section_y + 14, PAD + 38, section_y + 32], fill=dot_color)

            d.text((PAD + 58, section_y + 8), e.get("hora", ""), font=f_evt_hora, fill=TXT)

            flag_x = PAD + 180
            pais = (e.get("pais", "") or "")[:3].upper()
            _draw_flag(d, flag_x, section_y + 22, 50, 34, pais)
            country_disp = f"{pais} {_COUNTRY_NAME_EN.get(pais, '')}"
            d.text((flag_x + 34, section_y + 10), country_disp, font=f_evt_country, fill=GOLD_BRIGHT)

            titulo = _normalize_title_en(e.get("titulo", ""))
            d.text((PAD + 58, section_y + 44), titulo, font=f_evt_titulo, fill=TXT)

            prev = (e.get("previous") or "").strip() or "—"
            fcst = (e.get("forecast") or "").strip() or "—"
            actual = (e.get("actual") or "").strip() or "—"
            data_txt = f"Previous: {prev}  ·  Forecast: {fcst}  ·  Actual: {actual}"
            data_color = ACCENT if actual != "—" else TXT_DIM
            _draw_emoji_label(d, PAD + 58, section_y + 76, "📊", data_txt,
                              f_evt_emj_data, f_evt_data, data_color, gap=8)

            pares = _COUNTRY_TO_PAIRS.get(pais, "—")
            _draw_emoji_label(d, PAD + 58, section_y + 104, "📍",
                              f"Affects: {pares}",
                              f_evt_emj_small, f_evt_pares, TXT_DIM, gap=8)

            section_y += EVENT_ROW_H
            d.line([PAD + 30, section_y - 8, W - PAD - 30, section_y - 8], fill=GRID, width=1)

    # ── SECTION 2: MARKET OVERVIEW + FEAR & GREED ──
    section_y += 12
    d.line([PAD + 100, section_y, W - PAD - 100, section_y], fill=GOLD, width=1)
    section_y += 18

    eb_p = d.textbbox((0, 0), "📊", font=f_section_emj)
    tb_p = d.textbbox((0, 0), "MARKET OVERVIEW", font=f_section)
    total_p = (eb_p[2] - eb_p[0]) + 14 + (tb_p[2] - tb_p[0])
    _draw_emoji_label(d, (W - total_p) // 2, section_y, "📊", "MARKET OVERVIEW",
                      f_section_emj, f_section, GOLD_BRIGHT, gap=14)
    section_y += max(eb_p[3] - eb_p[1], tb_p[3] - tb_p[1]) + 18

    f_ov = _font(22, bold=False)
    f_ov_emj = _font_emoji(22)

    def _fmt_pairs(lst, max_chars=70):
        parts = [f"{n} (RSI {r:.0f})" for n, r in lst]
        joined = ", ".join(parts)
        if len(joined) > max_chars:
            joined = joined[:max_chars - 3] + "..."
        return joined

    if bullish:
        _draw_emoji_label(d, PAD + 40, section_y, "🟢", f"Bullish: {_fmt_pairs(bullish)}",
                          f_ov_emj, f_ov, GREEN, gap=10)
        section_y += 32
    if bearish:
        _draw_emoji_label(d, PAD + 40, section_y, "🔴", f"Bearish: {_fmt_pairs(bearish)}",
                          f_ov_emj, f_ov, RED, gap=10)
        section_y += 32
    if neutral:
        _draw_emoji_label(d, PAD + 40, section_y, "⚪", f"Neutral: {_fmt_pairs(neutral)}",
                          f_ov_emj, f_ov, NEUTRAL, gap=10)
        section_y += 32
    section_y += 8

    fg_emj = ("😱" if fear_greed_value <= 25 else
              "😨" if fear_greed_value <= 45 else
              "😐" if fear_greed_value <= 55 else
              "😎" if fear_greed_value <= 75 else "🤑")
    fg_color = (RED if fear_greed_value <= 25 else
                ACCENT if fear_greed_value <= 45 else
                NEUTRAL if fear_greed_value <= 55 else GREEN)
    fg_txt = f"Fear & Greed:  {fear_greed_value}/100  ({fear_greed_label})"
    f_fg_emj = _font_emoji(22)
    eb_fg = d.textbbox((0, 0), fg_emj, font=f_fg_emj)
    tb_fg = d.textbbox((0, 0), fg_txt, font=f_ov)
    total_fg = (eb_fg[2] - eb_fg[0]) + 12 + (tb_fg[2] - tb_fg[0])
    _draw_emoji_label(d, (W - total_fg) // 2, section_y, fg_emj, fg_txt,
                      f_fg_emj, f_ov, fg_color, gap=12)
    section_y += 30

    if fg_insight:
        f_insight = _font(20, bold=False)
        itb = d.textbbox((0, 0), fg_insight, font=f_insight)
        d.text(((W - (itb[2] - itb[0])) // 2, section_y), fg_insight, font=f_insight, fill=TXT_DIM)
        section_y += 30

    # ── SECTION 3: TRADING PLAN ──
    section_y += 16
    d.line([PAD + 100, section_y, W - PAD - 100, section_y], fill=GOLD, width=1)
    section_y += 18

    eb_pl = d.textbbox((0, 0), "💡", font=f_section_emj)
    tb_pl = d.textbbox((0, 0), "TODAY'S TRADING PLAN", font=f_section)
    total_pl = (eb_pl[2] - eb_pl[0]) + 14 + (tb_pl[2] - tb_pl[0])
    _draw_emoji_label(d, (W - total_pl) // 2, section_y, "💡", "TODAY'S TRADING PLAN",
                      f_section_emj, f_section, GOLD_BRIGHT, gap=14)
    section_y += max(eb_pl[3] - eb_pl[1], tb_pl[3] - tb_pl[1]) + 18

    f_plan = _font(22, bold=False)
    f_plan_emj = _font_emoji(22)

    if avoid_hours:
        avoid_dedup = sorted(set(avoid_hours))
        txt_av = f"Avoid trading near: {' · '.join(avoid_dedup)}"
        _draw_emoji_label(d, PAD + 40, section_y, "⚠️", txt_av,
                          f_plan_emj, f_plan, ACCENT, gap=10)
    else:
        _draw_emoji_label(d, PAD + 40, section_y, "🟢", "No high-impact events blocking",
                          f_plan_emj, f_plan, GREEN, gap=10)
    section_y += 32

    if best_session:
        _draw_emoji_label(d, PAD + 40, section_y, "⏰", f"Best session: {best_session}",
                          f_plan_emj, f_plan, TXT, gap=10)
        section_y += 32

    if focus_pairs:
        _draw_emoji_label(d, PAD + 40, section_y, "🎯", f"Pairs in focus: {focus_pairs}",
                          f_plan_emj, f_plan, TXT, gap=10)

    # ── FOOTER ──
    f_foot = _font(20, bold=False)
    fuentes = "Source: ForexFactory · CNN Fear & Greed · MT5"
    fsw, _ = _text_size(d, fuentes, f_foot)
    d.text(((W - fsw) // 2, H_FINAL - 90), fuentes, font=f_foot, fill=TXT_DIM)

    web = "buysell365.pro"
    fw, _ = _text_size(d, web, f_foot)
    d.text(((W - fw) // 2, H_FINAL - 58), web, font=f_foot, fill=GOLD)

    out = OUT_DIR / fname
    img.save(out, "JPEG", quality=92)
    return out


if __name__ == "__main__":
    # Preview con datos del 01/05/2026
    demo = [
        ("02:30", "JPY", "Final PMI manufacturero"),
        ("03:30", "AUD", "PPI q/q"),
        ("08:30", "CHF", "Ventas minoristas y/y"),
        ("10:30", "GBP", "Final PMI manufacturero"),
        ("15:30", "CAD", "PMI manufacturero"),
        ("15:45", "USD", "Final PMI manufacturero"),
    ]
    path = generar_imagen_noticias(
        demo,
        "01 / 05 / 2026",
        fname="noticias_demo.jpg",
        greeting_label="GOOD MORNING",
        greeting_emoji="🌅",
        bullish=[("ORO", 65), ("EUR/USD", 58)],
        bearish=[("NASDAQ", 35)],
        neutral=[("S&P 500", 50)],
        fear_greed_value=26,
        fear_greed_label="Miedo",
        outlook=("Observa el indicador de Miedo y Codicia en 26/100, que sugiere "
                 "estado de miedo en el mercado. No hay tendencias alcistas ni "
                 "bajistas claras. La sesión más activa puede ser en los mercados "
                 "de divisas, como el par EUR/USD."),
        hora_local="07:00",
    )
    print(f"Generado: {path}")

"""
BuySell365 Pro — Generador de tarjetas para Instagram (v1, 22-abr-2026).

4 plantillas pro:
  1) TP ALCANZADO (verde)         → generate_tp_card()
  2) CIERRE PARCIAL (naranja)     → generate_partial_card()
  3) NUEVA SEÑAL (azul)           → generate_new_signal_card()
  4) RESUMEN DEL DÍA (dorado)     → generate_daily_summary_card()

Diseño uniforme: 1080×1080, banda superior con color + icono, footer con branding.
Para TP/Parcial: chart REAL con velas (mplfinance + yfinance) + EMAs + auto-zoom.
Para Nueva Señal: layout de niveles (TP3/TP2/TP1/Entrada/SL con pips).
Para Resumen: dashboard tipo estadístico (WR, TP alcanzados, Top 3 activos).

REGLAS DE NEGOCIO (feedback_instagram_solo_ganancias.md):
- Instagram SOLO publica ganancias. Si pips < 0 → caller NO debe llamar.
- Encabezado sin BUY/SELL/COMPRA/VENTA (usar descripción: "TP ALCANZADO").
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Optional
import io

import matplotlib
matplotlib.use('Agg')  # no-GUI, evita crash Tkinter al usar desde hilos
import matplotlib.pyplot as plt
import matplotlib as mpl
from PIL import Image, ImageDraw, ImageFont

try:
    import yfinance as yf
    import mplfinance as mpf
    _YF_OK = True
except Exception:
    _YF_OK = False

W, H = 1080, 1080
_OUT_DIR = Path(__file__).parent / "ig_images"
_OUT_DIR.mkdir(exist_ok=True)
mpl.rcParams['font.family'] = 'Segoe UI'

PAL = {
    "tp":      {"main": (36, 200, 120),  "bright": (80, 240, 150), "dark": (20, 90, 55),   "shadow": (10, 50, 30)},
    "partial": {"main": (240, 170, 50),  "bright": (255, 200, 90), "dark": (130, 90, 25),  "shadow": (70, 45, 10)},
    "new":     {"main": (70, 130, 230),  "bright": (120, 170, 255),"dark": (30, 60, 130),  "shadow": (15, 30, 70)},
    "resumen": {"main": (201, 166, 107), "bright": (230, 195, 140),"dark": (90, 70, 40),   "shadow": (50, 40, 20)},
}
WHITE = (245, 245, 245)
GOLD = (201, 166, 107)
BG_DARK = (10, 14, 20)

# pair → ticker yfinance para el chart
_PAIR_TO_YF = {
    "GOLD": "GC=F", "XAUUSD": "GC=F", "ORO": "GC=F",
    "GER40": "^GDAXI", "DAX": "^GDAXI", "GER40Cash": "^GDAXI",
    "NAS100": "^IXIC", "NASDAQ": "^IXIC", "US100Cash": "^IXIC",
    "US30": "^DJI", "DOW": "^DJI", "US30Cash": "^DJI",
    "SPX500": "^GSPC", "US500": "^GSPC", "US500Cash": "^GSPC",
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "AUDUSD": "AUDUSD=X",
    "NZDUSD": "NZDUSD=X", "USDCAD": "USDCAD=X", "USDCHF": "USDCHF=X",
    "USDJPY": "USDJPY=X", "EURJPY": "EURJPY=X", "GBPJPY": "GBPJPY=X",
    "BRENT": "BZ=F", "BRENTCash": "BZ=F", "OIL": "CL=F", "OILCash": "CL=F",
    "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD",
}


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    paths = (["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"]
             if bold else ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"])
    for p in paths:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _tsz(d, t, f):
    b = d.textbbox((0, 0), t, font=f)
    return b[2] - b[0], b[3] - b[1]


def _fmt_price(v: float) -> str:
    if v is None:
        return "-"
    if abs(v) >= 10000: return f"{v:,.1f}"
    if abs(v) >= 1000:  return f"{v:,.1f}"
    if abs(v) >= 10:    return f"{v:.2f}"
    return f"{v:.4f}"


def _pips_display(diff: float, ref_price: float) -> str:
    """Formatea diferencia como 'pips' o 'pts' según magnitud del precio."""
    if abs(ref_price) >= 1000:
        return f"{abs(diff):.1f} pts"
    if abs(ref_price) >= 10:
        return f"{abs(diff) * 100:.0f} pips"
    return f"{abs(diff) * 10000:.0f} pips"


def _band_gradient(d: ImageDraw.Draw, band_h: int, pal: dict):
    for y in range(band_h):
        r = int(pal["dark"][0] + (pal["main"][0] - pal["dark"][0]) * (1 - y / band_h))
        g = int(pal["dark"][1] + (pal["main"][1] - pal["dark"][1]) * (1 - y / band_h))
        b = int(pal["dark"][2] + (pal["main"][2] - pal["dark"][2]) * (1 - y / band_h))
        d.line([(0, y), (W, y)], fill=(r, g, b))


def _icon(d: ImageDraw.Draw, kind: str, pal: dict):
    import math
    cx0, cy0 = 130, 170
    rad = 60
    d.ellipse([cx0 - rad, cy0 - rad, cx0 + rad, cy0 + rad], fill=WHITE, outline=pal["bright"], width=6)
    if kind == "tp":
        d.line([(cx0 - 30, cy0 + 5), (cx0 - 5, cy0 + 30), (cx0 + 35, cy0 - 25)], fill=pal["dark"], width=12)
    elif kind == "partial":
        f_frac = _font(68, True)
        fw, fh = _tsz(d, "½", f_frac)
        d.text((cx0 - fw // 2, cy0 - fh // 2 - 5), "½", font=f_frac, fill=pal["dark"])
    elif kind == "new":
        d.polygon([(cx0 - 20, cy0 - 35), (cx0 + 10, cy0 - 5),
                   (cx0 - 5, cy0 - 5), (cx0 + 20, cy0 + 35),
                   (cx0 - 10, cy0 + 5), (cx0 + 5, cy0 + 5)],
                  fill=pal["dark"])
    elif kind == "resumen":
        pts = []
        for i in range(10):
            ang = math.radians(-90 + i * 36)
            r = 45 if i % 2 == 0 else 20
            pts.append((cx0 + r * math.cos(ang), cy0 + r * math.sin(ang)))
        d.polygon(pts, fill=pal["dark"])


def _render_chart_real(ticker: str, entry: float, tp: float, sl: float,
                       extra_tps: Optional[List[float]] = None) -> Image.Image:
    """Genera gráfico real con velas + EMAs + líneas TP/Entry/SL. Devuelve PIL.Image."""
    if not _YF_OK:
        raise RuntimeError("yfinance/mplfinance no disponibles")
    df = yf.download(ticker, period="2d", interval="15m",
                     progress=False, auto_adjust=False, prepost=False)
    if df is None or df.empty or len(df) < 20:
        df = yf.download(ticker, period="1mo", interval="1h",
                         progress=False, auto_adjust=False, prepost=False)
    if df is None or df.empty:
        raise RuntimeError(f"Sin datos para {ticker}")
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    if len(df) > 30:
        df = df.tail(30).copy()
    else:
        df = df.copy()

    all_levels = [tp, entry, sl] + (extra_tps or [])
    data_min = float(df['Low'].min())
    data_max = float(df['High'].max())
    level_min = min(all_levels)
    level_max = max(all_levels)
    combined_min = min(data_min, level_min)
    combined_max = max(data_max, level_max)
    rng = combined_max - combined_min
    if rng <= 0:
        rng = combined_min * 0.005
    margin = rng * 0.15
    y_min = combined_min - margin
    y_max = combined_max + margin

    mc = mpf.make_marketcolors(up='#26e07a', down='#ef4444',
                                edge={'up': '#26e07a', 'down': '#ef4444'},
                                wick={'up': '#26e07a', 'down': '#ef4444'},
                                volume='inherit')
    s = mpf.make_mpf_style(
        base_mpf_style='nightclouds', marketcolors=mc,
        facecolor='#0a0e14', edgecolor='#1f2937',
        gridcolor='#1a2330', gridstyle=':',
        rc={'axes.labelcolor': '#c9a66b', 'xtick.color': '#9ca3af',
            'ytick.color': '#9ca3af', 'axes.edgecolor': '#c9a66b',
            'axes.linewidth': 1.2,
            'font.size': 11, 'xtick.labelsize': 10, 'ytick.labelsize': 10},
    )

    hl_vals = [tp] + (extra_tps or []) + [entry, sl]
    n_tp = 1 + len(extra_tps or [])
    hl_cols = (['#50f096'] * n_tp) + ['#c9a66b', '#ef4444']
    hl_styles = (['--'] * n_tp) + ['--', '--']
    hl_widths = ([2.2] * n_tp) + [2.0, 1.6]
    hlines = dict(hlines=hl_vals, colors=hl_cols,
                  linestyle=hl_styles, linewidths=hl_widths)

    ema9 = df['Close'].ewm(span=9).mean()
    ema20 = df['Close'].ewm(span=20).mean()
    addplots = [
        mpf.make_addplot(ema9, color='#60a5fa', width=1.4),
        mpf.make_addplot(ema20, color='#f59e0b', width=1.4),
    ]

    fig, axes = mpf.plot(
        df, type='candle', style=s, hlines=hlines, addplot=addplots,
        figsize=(10.8, 6.3), returnfig=True,
        xrotation=0, datetime_format='%H:%M',
        tight_layout=True, volume=False,
        ylabel='', ylabel_lower='',
        ylim=(y_min, y_max),
    )

    ax = axes[0]
    xmax = ax.get_xlim()[1]
    items = []
    if extra_tps:
        for i, tpv in enumerate([tp] + extra_tps, start=1):
            items.append((tpv, f'TP{i}  {_fmt_price(tpv)}', '#50f096', True))
    else:
        items.append((tp, f'TP  {_fmt_price(tp)}', '#50f096', True))
    items.append((entry, f'ENTRADA  {_fmt_price(entry)}', '#c9a66b', True))
    items.append((sl, f'SL  {_fmt_price(sl)}', '#ef4444', False))
    items.sort(key=lambda x: x[0])

    rng_axis = y_max - y_min
    min_sep = rng_axis * 0.05
    label_ys = [it[0] for it in items]
    for i in range(1, len(label_ys)):
        if label_ys[i] - label_ys[i - 1] < min_sep:
            label_ys[i] = label_ys[i - 1] + min_sep

    for (price, text, color, bold), ly in zip(items, label_ys):
        ax.annotate(text, xy=(xmax, ly), xytext=(8, 0),
                    textcoords='offset points', color=color,
                    fontsize=11, fontweight='bold' if bold else 'normal', va='center')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, facecolor='#0a0e14',
                bbox_inches='tight', pad_inches=0.2)
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _header_and_pts(card: Image.Image, kind: str, header_line: str, pts_text: str):
    d = ImageDraw.Draw(card)
    pal = PAL[kind]
    band_h = 360
    _band_gradient(d, band_h, pal)
    _icon(d, kind, pal)

    f_dir = _font(54, True)
    d.text((225, 55), header_line, font=f_dir, fill=WHITE)

    f_pts = _font(200, True)
    pw, _ = _tsz(d, pts_text, f_pts)
    px = (W - pw) // 2
    py = 130
    for off in range(8, 0, -2):
        d.text((px + off, py + off), pts_text, font=f_pts, fill=pal["shadow"])
    d.text((px, py), pts_text, font=f_pts, fill=WHITE)
    return band_h


def _footer_branding(card: Image.Image):
    d = ImageDraw.Draw(card)
    f_brand = _font(44, True)
    brand = "BuySell365 pro"
    bw, _ = _tsz(d, brand, f_brand)
    d.text(((W - bw) // 2, H - 130), brand, font=f_brand, fill=GOLD)
    f_cta = _font(28, False)
    cta = "Join the VIP channel  ·  buysell365.pro"
    cw2, _ = _tsz(d, cta, f_cta)
    d.text(((W - cw2) // 2, H - 75), cta, font=f_cta, fill=(220, 220, 220))


def _paste_chart(card: Image.Image, chart: Image.Image, band_h: int):
    cw, ch = chart.size
    target_h = 520
    target_w = int(cw * (target_h / ch))
    if target_w > W - 40:
        target_w = W - 40
        target_h = int(ch * (target_w / cw))
    chart = chart.resize((target_w, target_h), Image.LANCZOS)
    card.paste(chart, ((W - target_w) // 2, band_h + 10))


# ════════════════════ API PÚBLICA ════════════════════

def generate_tp_card(pair: str, pips: str,
                     entry: float, tp: float, sl: float,
                     out_name: str = "ig_tp.jpg") -> Path:
    """Genera tarjeta TP ALCANZADO (verde). Pips debe venir como '+14 pts'."""
    card = Image.new("RGB", (W, H), BG_DARK)
    band_h = _header_and_pts(card, "tp", f"TP ALCANZADO  ·  {pair}", pips)
    # Chart real
    ticker = _PAIR_TO_YF.get(pair.upper())
    if ticker:
        try:
            chart = _render_chart_real(ticker, entry, tp, sl)
            _paste_chart(card, chart, band_h)
        except Exception as e:
            ImageDraw.Draw(card).text((100, band_h + 100), f"[chart err: {e}]",
                                      fill=(255, 100, 100), font=_font(18))
    _footer_branding(card)
    out = _OUT_DIR / out_name
    card.save(out, "JPEG", quality=92)
    return out


def generate_partial_card(pair: str, pips: str,
                          entry: float, tp: float, sl: float,
                          out_name: str = "ig_partial.jpg") -> Path:
    """Tarjeta CIERRE PARCIAL (naranja). Pips debe ser positivo ('+25 pts')."""
    card = Image.new("RGB", (W, H), BG_DARK)
    band_h = _header_and_pts(card, "partial", f"CIERRE PARCIAL  ·  {pair}", pips)
    ticker = _PAIR_TO_YF.get(pair.upper())
    if ticker:
        try:
            chart = _render_chart_real(ticker, entry, tp, sl)
            _paste_chart(card, chart, band_h)
        except Exception as e:
            ImageDraw.Draw(card).text((100, band_h + 100), f"[chart err: {e}]",
                                      fill=(255, 100, 100), font=_font(18))
    _footer_branding(card)
    out = _OUT_DIR / out_name
    card.save(out, "JPEG", quality=92)
    return out


def generate_new_signal_card(pair: str,
                             entry: float, tp: float, sl: float,
                             extra_tps: Optional[List[float]] = None,
                             out_name: str = "ig_new_signal.jpg") -> Path:
    """Tarjeta NUEVA SEÑAL (azul) — layout de niveles con pips."""
    card = Image.new("RGB", (W, H), BG_DARK)
    band_h = _header_and_pts(card, "new", f"NUEVA SEÑAL  ·  {pair}", "ABIERTA")
    _paint_new_signal_levels(card, band_h, entry, tp, sl, extra_tps, PAL["new"])
    _footer_branding(card)
    out = _OUT_DIR / out_name
    card.save(out, "JPEG", quality=92)
    return out


def generate_daily_summary_card(stats: dict,
                                out_name: str = "ig_daily_summary.jpg") -> Path:
    """Tarjeta RESUMEN DEL DÍA (dorado) — dashboard estadístico.
    stats dict: {'pips_net': int, 'wr': int, 'tp': int, 'total': int,
                 'top3': [{'pair': str, 'pts': int}, ...]}"""
    pips_net = stats.get('pips_net', 0)
    pts_text = f"{'+' if pips_net >= 0 else ''}{pips_net} pts"
    card = Image.new("RGB", (W, H), BG_DARK)
    band_h = _header_and_pts(card, "resumen", "DAILY  ·  RECAP", pts_text)
    _paint_resumen_stats(card, stats, band_h, PAL["resumen"])
    _footer_branding(card)
    out = _OUT_DIR / out_name
    card.save(out, "JPEG", quality=92)
    return out


# ════════════════════ PANELS ESPECÍFICOS ════════════════════

def _paint_new_signal_levels(card, band_h, entry, tp, sl, extra_tps, pal):
    d = ImageDraw.Draw(card)
    panel_top = band_h + 30
    panel_bottom = H - 180
    panel_left = 60
    panel_right = W - 60
    d.rectangle([panel_left, panel_top, panel_right, panel_bottom],
                fill=(14, 18, 24), outline=pal["dark"], width=3)

    tps = sorted([tp] + (extra_tps or []), reverse=True)
    items = []
    for i, tpv in enumerate(tps):
        lvl_num = len(tps) - i
        items.append({
            "label": f"TP{lvl_num}" if len(tps) > 1 else "TP",
            "price": tpv, "color": (80, 240, 150), "sign": "+",
            "delta_txt": _pips_display(tpv - entry, entry), "bold": True,
        })
    items.append({"label": "ENTRADA", "price": entry, "color": pal["bright"],
                  "sign": "", "delta_txt": "nivel de entrada", "bold": True})
    items.append({"label": "SL", "price": sl, "color": (230, 100, 100),
                  "sign": "-", "delta_txt": _pips_display(entry - sl, entry), "bold": False})

    usable_h = panel_bottom - panel_top - 40
    row_h = usable_h // len(items)
    y = panel_top + 20

    f_label = _font(36, True)
    f_price = _font(44, True)
    f_delta = _font(26, False)
    for it in items:
        d.rectangle([panel_left + 20, y, panel_right - 20, y + row_h - 10],
                    fill=(22, 28, 36), outline=None)
        d.rectangle([panel_left + 20, y, panel_left + 30, y + row_h - 10], fill=it["color"])
        d.text((panel_left + 55, y + (row_h - 30) // 2 - 12),
               it["label"], font=f_label, fill=it["color"])
        price_txt = _fmt_price(it["price"])
        d.text((panel_left + 300, y + (row_h - 30) // 2 - 18),
               price_txt, font=f_price, fill=WHITE)
        delta_line = f"{it['sign']}{it['delta_txt']}" if it['sign'] else it['delta_txt']
        dw, _ = _tsz(d, delta_line, f_delta)
        d.text((panel_right - 40 - dw, y + (row_h - 30) // 2 - 10),
               delta_line, font=f_delta, fill=it["color"])
        y += row_h


def _paint_resumen_stats(card, stats, band_h, pal):
    d = ImageDraw.Draw(card)
    panel_top = band_h + 30
    panel_bottom = H - 180
    panel_left = 60
    panel_right = W - 60
    d.rectangle([panel_left, panel_top, panel_right, panel_bottom],
                fill=(14, 18, 24), outline=pal["dark"], width=3)

    row1_h = 160
    col_w = (panel_right - panel_left) // 3

    def cell(x, y, w, h, label, value, color):
        d.rectangle([x + 10, y + 10, x + w - 10, y + h - 10],
                    fill=(22, 28, 36), outline=(50, 60, 75), width=1)
        f_val = _font(64, True)
        vw, vh = _tsz(d, value, f_val)
        d.text((x + (w - vw) // 2, y + 30), value, font=f_val, fill=color)
        f_lbl = _font(22, False)
        lw, lh = _tsz(d, label, f_lbl)
        d.text((x + (w - lw) // 2, y + 110), label, font=f_lbl, fill=(160, 170, 190))

    wr = stats.get('wr', 0)
    tp_count = stats.get('tp', 0)
    total = stats.get('total', 0)
    cell(panel_left, panel_top, col_w, row1_h, "WIN RATE", f"{wr}%", (80, 240, 150))
    cell(panel_left + col_w, panel_top, col_w, row1_h, "TP ALCANZADOS", str(tp_count), (80, 240, 150))
    cell(panel_left + 2 * col_w, panel_top, col_w, row1_h, "OPS TOTAL", str(total), WHITE)

    f_title = _font(30, True)
    d.text((panel_left + 30, panel_top + row1_h + 20),
           "TOP 3 ACTIVOS DEL DÍA", font=f_title, fill=pal["bright"])

    y_off = panel_top + row1_h + 70
    medals_txt = ["1°", "2°", "3°"]
    for i, item in enumerate((stats.get('top3') or [])[:3]):
        bar_y = y_off + i * 70
        d.rectangle([panel_left + 20, bar_y, panel_right - 20, bar_y + 55],
                    fill=(22, 28, 36), outline=None)
        f_pos = _font(34, True)
        d.text((panel_left + 40, bar_y + 10), medals_txt[i], font=f_pos, fill=pal["bright"])
        f_pair = _font(32, True)
        d.text((panel_left + 100, bar_y + 10), str(item.get('pair', '?')), font=f_pair, fill=WHITE)
        f_pts_r = _font(34, True)
        pts_txt = f"+{item.get('pts', 0)} pts"
        pw, _ = _tsz(d, pts_txt, f_pts_r)
        d.text((panel_right - 60 - pw, bar_y + 8), pts_txt, font=f_pts_r, fill=(80, 240, 150))


# ════════════════════ TEST ════════════════════
if __name__ == "__main__":
    print(generate_tp_card("GOLD", "+14 pts", 4755, 4762, 4748, "test_tp.jpg"))
    print(generate_partial_card("GER40", "+25 pts", 24380, 24430, 24340, "test_partial.jpg"))
    print(generate_new_signal_card("EURUSD", 1.0842, 1.0860, 1.0820,
                                    extra_tps=[1.0880, 1.0900], out_name="test_new.jpg"))
    print(generate_daily_summary_card({
        'pips_net': 325, 'wr': 71, 'tp': 22, 'total': 31,
        'top3': [{'pair': 'GER40', 'pts': 325},
                 {'pair': 'ORO', 'pts': 209},
                 {'pair': 'XAUUSD', 'pts': 46}],
    }, "test_resumen.jpg"))

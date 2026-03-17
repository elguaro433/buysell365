"""Generate BuySell365 Pro Backtest Report PDF — v3 Final (Solo Premium)."""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER

OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_report.pdf")

BG_DARK = HexColor("#0d1117")
BG_PANEL = HexColor("#161b22")
BG_ROW_ALT = HexColor("#1c2129")
BORDER = HexColor("#30363d")
ACCENT = HexColor("#00d4aa")
GREEN = HexColor("#00c853")
RED = HexColor("#ff5252")
YELLOW = HexColor("#ffa726")
TEXT = HexColor("#e6edf3")
TEXT_SEC = HexColor("#8b949e")
BLUE = HexColor("#58a6ff")
GOLD = HexColor("#ffd700")

WIDTH, HEIGHT = A4

def draw_bg(c, doc):
    c.saveState()
    c.setFillColor(BG_DARK)
    c.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)
    c.restoreState()

def S(name, **kw):
    defaults = {"fontName": "Helvetica", "fontSize": 10, "textColor": TEXT, "leading": 14}
    defaults.update(kw)
    return ParagraphStyle(name, **defaults)

def build_pdf():
    doc = SimpleDocTemplate(OUTPUT, pagesize=A4, topMargin=15*mm, bottomMargin=15*mm, leftMargin=15*mm, rightMargin=15*mm)
    W = doc.width

    st = {
        "title": S("title", fontName="Helvetica-Bold", fontSize=22, textColor=ACCENT, alignment=TA_CENTER, spaceAfter=2*mm),
        "title2": S("title2", fontName="Helvetica-Bold", fontSize=16, textColor=TEXT, alignment=TA_CENTER, spaceAfter=3*mm),
        "sub": S("sub", fontSize=11, textColor=TEXT_SEC, alignment=TA_CENTER, spaceAfter=8*mm),
        "h2": S("h2", fontName="Helvetica-Bold", fontSize=14, textColor=BLUE, spaceBefore=6*mm, spaceAfter=3*mm),
        "body": S("body", spaceAfter=2*mm),
        "finding": S("finding", spaceAfter=3*mm, leftIndent=8*mm),
        "rec": S("rec", spaceAfter=3*mm, leftIndent=8*mm),
        "small": S("small", fontSize=8, textColor=TEXT_SEC, alignment=TA_CENTER),
    }

    story = []

    # === PAGE 1 ===
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("BuySell365 Pro", st["title"]))
    story.append(Paragraph("Backtest Final - Solo Senales Premium", st["title2"]))
    story.append(Paragraph("Periodo: 60 dias | Dic 2025 - Mar 2026 | Velas 15 min | 6 activos | L-V 08:00-18:00", st["sub"]))

    # -- COMPARATIVA: ORIGINAL vs FINAL --
    story.append(Paragraph("Evolucion: Original vs Premium Final", st["h2"]))

    comp = [
        ["Metrica", "ORIGINAL (sin filtros)", "PREMIUM FINAL"],
        ["Total Senales", "395", "164"],
        ["Win Rate", "33.4%", "36.6%"],
        ["Pips Totales", "-53.5", "+2,772"],
        ["Pips / Senal", "-0.1", "+16.9"],
        ["SL Rate", "51.9%", "46.3%"],
        ["Breakout Pips", "+1,624", "+3,035"],
        ["Senales / Dia", "~6.6", "~2.7"],
    ]
    cw = [W*0.35, W*0.32, W*0.33]
    t = Table(comp, colWidths=cw)
    cs = [
        ("BACKGROUND", (0, 0), (-1, 0), BG_PANEL),
        ("TEXTCOLOR", (0, 0), (-1, 0), ACCENT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    for i in range(1, len(comp)):
        bg = BG_ROW_ALT if i % 2 == 0 else BG_DARK
        cs.append(("BACKGROUND", (0, i), (-1, i), bg))
        cs.append(("TEXTCOLOR", (0, i), (0, i), TEXT))
        cs.append(("TEXTCOLOR", (1, i), (1, i), RED))
        cs.append(("TEXTCOLOR", (2, i), (2, i), GREEN))
        cs.append(("FONTNAME", (1, i), (2, i), "Helvetica-Bold"))
    t.setStyle(TableStyle(cs))
    story.append(t)

    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        '<b><font color="#00c853" size="14">De -53 pips a +2,772 pips = +2,825 pips de mejora</font></b>',
        S("key", alignment=TA_CENTER, spaceAfter=2*mm)
    ))
    story.append(Paragraph(
        '<font color="#ffd700" size="12">164 senales PREMIUM en 60 dias | +16.9 pips por senal</font>',
        S("key2", alignment=TA_CENTER, spaceAfter=6*mm)
    ))

    # -- POR ACTIVO --
    story.append(Paragraph("Resultados por Activo", st["h2"]))
    asset = [
        ["Activo", "Senales", "Win Rate", "TP1", "TP2", "TP3", "SL", "Pips", "Estado"],
        ["ORO", "45", "37.8%", "10", "7", "0", "28", "-6.9", "NEUTRAL"],
        ["EUR/USD", "0", "-", "-", "-", "-", "-", "0", "FILTRADO"],
        ["USD/JPY", "1", "-", "0", "0", "0", "0", "+15.2", "POCOS"],
        ["GBP/JPY", "1", "0.0%", "0", "0", "0", "1", "-66.7", "POCOS"],
        ["NASDAQ", "59", "39.0%", "20", "3", "0", "25", "+2,743", "EXCELENTE"],
        ["S&P 500", "58", "34.5%", "16", "3", "1", "22", "+88.6", "RENTABLE"],
    ]
    acw = [W*f for f in [0.11, 0.09, 0.10, 0.07, 0.07, 0.07, 0.07, 0.12, 0.14]]
    acw[-1] += W - sum(acw)
    t2 = Table(asset, colWidths=acw)
    as2 = [
        ("BACKGROUND", (0, 0), (-1, 0), BG_PANEL),
        ("TEXTCOLOR", (0, 0), (-1, 0), ACCENT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    verdicts = {"RENTABLE": GREEN, "EXCELENTE": GREEN, "NEUTRAL": YELLOW, "FILTRADO": TEXT_SEC, "POCOS": TEXT_SEC}
    for i in range(1, len(asset)):
        bg = BG_ROW_ALT if i % 2 == 0 else BG_DARK
        as2.append(("BACKGROUND", (0, i), (-1, i), bg))
        as2.append(("TEXTCOLOR", (0, i), (0, i), TEXT))
        wr_str = asset[i][2].replace("%", "")
        if wr_str not in ("-", "N/A"):
            wr = float(wr_str)
            as2.append(("TEXTCOLOR", (2, i), (2, i), GREEN if wr >= 37 else (YELLOW if wr >= 30 else RED)))
        else:
            as2.append(("TEXTCOLOR", (2, i), (2, i), TEXT_SEC))
        pv_str = asset[i][7].replace(",", "")
        try:
            pv = float(pv_str)
            as2.append(("TEXTCOLOR", (7, i), (7, i), GREEN if pv > 0 else (RED if pv < 0 else TEXT_SEC)))
        except:
            as2.append(("TEXTCOLOR", (7, i), (7, i), TEXT_SEC))
        as2.append(("FONTNAME", (7, i), (7, i), "Helvetica-Bold"))
        vc = verdicts.get(asset[i][8], TEXT)
        as2.append(("TEXTCOLOR", (8, i), (8, i), vc))
        as2.append(("FONTNAME", (8, i), (8, i), "Helvetica-Bold"))
        for c in [1, 3, 4, 5, 6]:
            as2.append(("TEXTCOLOR", (c, i), (c, i), TEXT_SEC))
    t2.setStyle(TableStyle(as2))
    story.append(t2)

    # -- POR ESTRATEGIA --
    story.append(Paragraph("Resultados por Estrategia", st["h2"]))
    strat = [
        ["Estrategia", "Senales", "Win Rate", "Pips", "Pips/Op", "Estado"],
        ["Breakout", "160", "36.9%", "+3,035", "+19.0", "PRINCIPAL"],
        ["Reversion (Score 5)", "4", "25.0%", "-263", "-65.7", "COMPLEMENTO"],
        ["Pullback", "-", "-", "-", "-", "DESACTIVADA"],
        ["Reversion (Score 4)", "-", "-", "-", "-", "DESACTIVADA"],
    ]
    scw = [W/6]*6
    t3 = Table(strat, colWidths=scw)
    ss = [
        ("BACKGROUND", (0, 0), (-1, 0), BG_PANEL),
        ("TEXTCOLOR", (0, 0), (-1, 0), ACCENT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND", (0, 1), (-1, 1), BG_DARK), ("TEXTCOLOR", (0, 1), (-1, 1), TEXT),
        ("TEXTCOLOR", (2, 1), (2, 1), GREEN), ("TEXTCOLOR", (3, 1), (4, 1), GREEN),
        ("TEXTCOLOR", (5, 1), (5, 1), GREEN), ("FONTNAME", (5, 1), (5, 1), "Helvetica-Bold"),
        ("BACKGROUND", (0, 2), (-1, 2), BG_ROW_ALT), ("TEXTCOLOR", (0, 2), (-1, 2), YELLOW),
        ("BACKGROUND", (0, 3), (-1, 3), BG_DARK), ("TEXTCOLOR", (0, 3), (-1, 3), TEXT_SEC),
        ("BACKGROUND", (0, 4), (-1, 4), BG_ROW_ALT), ("TEXTCOLOR", (0, 4), (-1, 4), TEXT_SEC),
    ]
    t3.setStyle(TableStyle(ss))
    story.append(t3)

    # === PAGE 2: CONFIGURACION + HALLAZGOS ===
    story.append(PageBreak())
    story.append(Spacer(1, 5*mm))

    story.append(Paragraph("Configuracion Activa del Bot", st["h2"]))

    config = [
        "<b><font color='#ffd700'>Horario:</font></b> Lunes a Viernes, 08:00 - 18:00 hora Andorra",
        "<b><font color='#ffd700'>Senales:</font></b> Solo PREMIUM (Breakout score 4 + Reversion con divergencia score 5)",
        "<b><font color='#ffd700'>Noticias:</font></b> Para antes de noticias rojas / 3 estrellas (NFP, FOMC, CPI: 4h antes, 3h despues)",
        "<b><font color='#ffd700'>EUR/USD:</font></b> Solo Breakout permitido (Pullback y Reversion bloqueados — 24.7% WR historico)",
        "<b><font color='#ffd700'>Cooldown:</font></b> 60 min entre senales del mismo activo + direccion",
        "<b><font color='#ffd700'>SL por estrategia:</font></b> Breakout +20% SL, Reversion +10% SL",
        "<b><font color='#ffd700'>TP2/TP3:</font></b> Reducidos 30-40% para capturar mas beneficio parcial",
        "<b><font color='#ffd700'>JPY:</font></b> SL minimo 15 pips USD/JPY, 20 pips GBP/JPY | Correlacion corregida",
    ]
    for c in config:
        story.append(Paragraph(c, st["finding"]))

    story.append(Paragraph("Hallazgos del Backtest", st["h2"]))

    hallazgos = [
        "<b><font color='#00c853'>Breakout es la estrategia ganadora:</font></b> 160 senales, +3,035 pips, +19.0/op.",
        "<b><font color='#00c853'>NASDAQ lidera:</font></b> 59 senales, 39% WR, +2,743 pips. Los breakouts en indices son muy rentables.",
        "<b><font color='#00c853'>S&amp;P 500 rentable:</font></b> 58 senales, 34.5% WR, +88.6 pips. Positivo con margen.",
        "<b><font color='#ffa726'>ORO neutral:</font></b> 45 senales, 37.8% WR, -6.9 pips. Practicamente break-even, no pierde.",
        "<b><font color='#ffa726'>EUR/USD, USD/JPY, GBP/JPY:</font></b> Pocas senales Breakout en este periodo. Normal — depende de condiciones de mercado.",
        "<b><font color='#00c853'>+2,772 pips netos:</font></b> Sistema rentable con promedio de +16.9 pips por senal Premium.",
    ]
    for i, h in enumerate(hallazgos):
        story.append(Paragraph(f"{i+1}. {h}", st["finding"]))

    story.append(Paragraph("Proximos Pasos", st["h2"]))
    pasos = [
        "<b>Monitorear 1-2 semanas</b> con la configuracion Premium en operaciones reales.",
        "<b>NASDAQ y S&amp;P 500</b> son los activos principales. Asegurar que MT5 los ejecuta correctamente.",
        "<b>Si ORO sigue en break-even,</b> considerar ajustar SL o desactivar temporalmente.",
        "<b>Backtest mensual</b> para verificar que los parametros siguen siendo optimos.",
    ]
    for i, p in enumerate(pasos):
        story.append(Paragraph(f"{i+1}. {p}", st["rec"]))

    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("BuySell365 Pro | Backtest Premium Final | 17 Marzo 2026", st["small"]))

    doc.build(story, onFirstPage=draw_bg, onLaterPages=draw_bg)
    print(f"PDF generado: {OUTPUT}")

if __name__ == "__main__":
    build_pdf()

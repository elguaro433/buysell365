"""Generate BuySell365 Pro Backtest Report PDF."""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor, white, black
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_report.pdf")

# Colors
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

WIDTH, HEIGHT = A4


def draw_background(c, doc):
    c.saveState()
    c.setFillColor(BG_DARK)
    c.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)
    c.restoreState()


def build_pdf():
    doc = SimpleDocTemplate(
        OUTPUT, pagesize=A4,
        topMargin=15*mm, bottomMargin=15*mm,
        leftMargin=15*mm, rightMargin=15*mm
    )

    styles = {
        "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=22, textColor=ACCENT, alignment=TA_CENTER, spaceAfter=4*mm),
        "subtitle": ParagraphStyle("subtitle", fontName="Helvetica", fontSize=11, textColor=TEXT_SEC, alignment=TA_CENTER, spaceAfter=8*mm),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=14, textColor=BLUE, spaceBefore=6*mm, spaceAfter=3*mm),
        "h3": ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=12, textColor=ACCENT, spaceBefore=4*mm, spaceAfter=2*mm),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, textColor=TEXT, spaceAfter=2*mm, leading=14),
        "bodyBold": ParagraphStyle("bodyBold", fontName="Helvetica-Bold", fontSize=10, textColor=TEXT, spaceAfter=2*mm, leading=14),
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=9, textColor=TEXT_SEC, spaceAfter=1*mm, leading=12),
        "finding": ParagraphStyle("finding", fontName="Helvetica", fontSize=10, textColor=TEXT, spaceAfter=3*mm, leading=14, leftIndent=8*mm),
        "recTitle": ParagraphStyle("recTitle", fontName="Helvetica-Bold", fontSize=10, textColor=GREEN, spaceAfter=1*mm, leftIndent=8*mm),
        "rec": ParagraphStyle("rec", fontName="Helvetica", fontSize=10, textColor=TEXT, spaceAfter=3*mm, leading=14, leftIndent=8*mm),
    }

    story = []

    # ── TITLE ──
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("BuySell365 Pro", styles["title"]))
    story.append(Paragraph("Reporte de Backtest", ParagraphStyle("t2", fontName="Helvetica-Bold", fontSize=16, textColor=TEXT, alignment=TA_CENTER, spaceAfter=3*mm)))
    story.append(Paragraph("Periodo: 60 dias  |  24 Dic 2025 - 17 Mar 2026  |  Velas 15 min", styles["subtitle"]))

    # ── RESUMEN GENERAL ──
    story.append(Paragraph("Resumen General", styles["h2"]))

    summary_data = [
        ["Total Senales", "Wins (TP)", "Losses (SL)", "Timeout", "Win Rate", "Pips Totales", "Pips/Senal"],
        ["395", "132", "205", "58", "33.4%", "-53.5", "-0.1"],
    ]
    summary_colors = [TEXT, GREEN, RED, YELLOW, RED, RED, RED]

    t = Table(summary_data, colWidths=[doc.width/7]*7)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BG_PANEL),
        ("BACKGROUND", (0, 1), (-1, 1), BG_ROW_ALT),
        ("TEXTCOLOR", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 1), (0, 1), TEXT),
        ("TEXTCOLOR", (1, 1), (1, 1), GREEN),
        ("TEXTCOLOR", (2, 1), (2, 1), RED),
        ("TEXTCOLOR", (3, 1), (3, 1), YELLOW),
        ("TEXTCOLOR", (4, 1), (4, 1), RED),
        ("TEXTCOLOR", (5, 1), (5, 1), RED),
        ("TEXTCOLOR", (6, 1), (6, 1), RED),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, 1), 14),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)

    # ── RESULTADOS POR ACTIVO ──
    story.append(Paragraph("Resultados por Activo", styles["h2"]))

    asset_data = [
        ["Activo", "Senales", "Win Rate", "TP1", "TP2", "TP3", "SL", "Pips", "Veredicto"],
        ["ORO", "27", "51.9%", "14", "0", "0", "13", "+85.3", "RENTABLE"],
        ["EUR/USD", "85", "24.7%", "21", "0", "0", "48", "-518.4", "NO RENTABLE"],
        ["USD/JPY", "76", "40.8%", "31", "0", "0", "38", "+245.8", "RENTABLE"],
        ["GBP/JPY", "75", "30.7%", "22", "1", "0", "41", "-169.9", "MEJORABLE"],
        ["NASDAQ", "49", "32.7%", "16", "0", "0", "26", "+204.6", "RENTABLE"],
        ["S&P 500", "83", "32.5%", "26", "1", "0", "39", "+99.6", "RENTABLE"],
    ]

    col_w = [doc.width*f for f in [0.11, 0.09, 0.10, 0.07, 0.07, 0.07, 0.07, 0.12, 0.14]]
    # Pad to fill remaining
    remaining = doc.width - sum(col_w)
    col_w[-1] += remaining

    t2 = Table(asset_data, colWidths=col_w)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), BG_PANEL),
        ("TEXTCOLOR", (0, 0), (-1, 0), ACCENT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    # Row colors and verdict colors
    for row_idx in range(1, len(asset_data)):
        bg = BG_ROW_ALT if row_idx % 2 == 0 else BG_DARK
        style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), bg))
        style_cmds.append(("TEXTCOLOR", (0, row_idx), (0, row_idx), TEXT))
        # Win rate color
        wr = float(asset_data[row_idx][2].replace("%", ""))
        style_cmds.append(("TEXTCOLOR", (2, row_idx), (2, row_idx), GREEN if wr >= 40 else (YELLOW if wr >= 30 else RED)))
        # Pips color
        pips_val = float(asset_data[row_idx][7])
        style_cmds.append(("TEXTCOLOR", (7, row_idx), (7, row_idx), GREEN if pips_val >= 0 else RED))
        style_cmds.append(("FONTNAME", (7, row_idx), (7, row_idx), "Helvetica-Bold"))
        # Verdict color
        verdict = asset_data[row_idx][8]
        vc = GREEN if "RENTABLE" == verdict else (YELLOW if "MEJORABLE" in verdict else RED)
        style_cmds.append(("TEXTCOLOR", (8, row_idx), (8, row_idx), vc))
        style_cmds.append(("FONTNAME", (8, row_idx), (8, row_idx), "Helvetica-Bold"))
        # Other cols
        for c in range(1, 7):
            style_cmds.append(("TEXTCOLOR", (c, row_idx), (c, row_idx), TEXT_SEC))

    t2.setStyle(TableStyle(style_cmds))
    story.append(t2)

    # ── RESULTADOS POR ESTRATEGIA ──
    story.append(Paragraph("Resultados por Estrategia", styles["h2"]))

    strat_data = [
        ["Estrategia", "Senales", "% del Total", "Win Rate", "Pips Total", "Pips/Op", "Veredicto"],
        ["Breakout", "47", "11.9%", "51.1%", "+1,624.1", "+34.6", "EXCELENTE"],
        ["Pullback", "4", "1.0%", "25.0%", "-88.5", "-22.1", "SIN DATOS"],
        ["Reversion", "344", "87.1%", "31.1%", "-1,589.1", "-4.6", "PROBLEMA"],
    ]

    col_w3 = [doc.width/7]*7
    t3 = Table(strat_data, colWidths=col_w3)
    style3 = [
        ("BACKGROUND", (0, 0), (-1, 0), BG_PANEL),
        ("TEXTCOLOR", (0, 0), (-1, 0), ACCENT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        # Breakout row
        ("BACKGROUND", (0, 1), (-1, 1), BG_DARK),
        ("TEXTCOLOR", (0, 1), (-1, 1), TEXT),
        ("TEXTCOLOR", (3, 1), (3, 1), GREEN),
        ("TEXTCOLOR", (4, 1), (4, 1), GREEN),
        ("TEXTCOLOR", (5, 1), (5, 1), GREEN),
        ("TEXTCOLOR", (6, 1), (6, 1), GREEN),
        ("FONTNAME", (6, 1), (6, 1), "Helvetica-Bold"),
        # Pullback row
        ("BACKGROUND", (0, 2), (-1, 2), BG_ROW_ALT),
        ("TEXTCOLOR", (0, 2), (-1, 2), TEXT_SEC),
        ("TEXTCOLOR", (6, 2), (6, 2), YELLOW),
        ("FONTNAME", (6, 2), (6, 2), "Helvetica-Bold"),
        # Reversion row
        ("BACKGROUND", (0, 3), (-1, 3), BG_DARK),
        ("TEXTCOLOR", (0, 3), (-1, 3), TEXT),
        ("TEXTCOLOR", (3, 3), (3, 3), RED),
        ("TEXTCOLOR", (4, 3), (4, 3), RED),
        ("TEXTCOLOR", (5, 3), (5, 3), RED),
        ("TEXTCOLOR", (6, 3), (6, 3), RED),
        ("FONTNAME", (6, 3), (6, 3), "Helvetica-Bold"),
    ]
    t3.setStyle(TableStyle(style3))
    story.append(t3)

    # ── HALLAZGOS CLAVE ──
    story.append(Paragraph("Hallazgos Clave", styles["h2"]))

    findings = [
        ("<b><font color='#00c853'>Breakout es la MEJOR estrategia:</font></b> 51.1% win rate con +34.6 pips por operacion. Es la unica estrategia rentable de forma consistente.", "good"),
        ("<b><font color='#ff5252'>Reversion genera DEMASIADAS senales (87% del total)</font></b> con un win rate bajo de 31.1% y -1,589 pips totales. Este es el problema principal de rentabilidad.", "bad"),
        ("<b><font color='#ff5252'>EUR/USD es el peor activo:</font></b> Solo 24.7% win rate con -518.4 pips. Se deberia considerar desactivar o exigir score minimo de 5.", "bad"),
        ("<b><font color='#00c853'>ORO y USD/JPY son los mejores activos</font></b> con win rates superiores al 40% y pips positivos.", "good"),
        ("<b><font color='#ffa726'>Solo TP1 se alcanza:</font></b> De 132 wins, 130 fueron TP1, solo 2 TP2 y 0 TP3. Los objetivos TP2/TP3 estan demasiado lejos.", "warn"),
        ("<b><font color='#ff5252'>SL se toca el 51.9% del tiempo:</font></b> Confirma que los stop loss estaban demasiado apretados. Los nuevos ajustes (sl_mult 1.0 para JPY) deberian mejorar esto.", "bad"),
    ]

    for i, (text, _) in enumerate(findings):
        story.append(Paragraph(f"{i+1}. {text}", styles["finding"]))

    # ── PAGE BREAK ──
    story.append(PageBreak())
    story.append(Spacer(1, 5*mm))

    # ── RECOMENDACIONES ──
    story.append(Paragraph("Recomendaciones de Optimizacion", styles["h2"]))

    recs = [
        ("Subir score minimo de Reversion a 5",
         "Actualmente las reversiones con score 4 (sin divergencia RSI confirmada) representan la mayoria de las perdidas. Exigir score 5 (divergencia obligatoria) reducira las senales de 344 a ~80-100 pero con mucho mayor calidad."),
        ("Desactivar o restringir EUR/USD",
         "Con 24.7% win rate y -518 pips, EUR/USD es claramente el peor activo. Opciones: desactivarlo temporalmente, o exigir score 5 solamente."),
        ("Reducir multiplicadores TP2/TP3",
         "Con 0 TP3 alcanzados en 395 senales, los objetivos TP3 son inalcanzables. Reducir TP2 y TP3 para capturar mas beneficio parcial."),
        ("Mantener los nuevos SL (sl_mult 1.0 para JPY)",
         "Los SL de 9-10 pips eran ruido puro. Los nuevos SL de ~19-22 pips deberian mejorar significativamente el win rate en JPY."),
        ("Priorizar senales Breakout",
         "Con 51.1% win rate y +34.6 pips/op, Breakout es la estrategia mas rentable. Considerar reducir el umbral de volumen para generar mas senales de este tipo."),
    ]

    for i, (title, desc) in enumerate(recs):
        story.append(Paragraph(f"{i+1}. {title}", styles["recTitle"]))
        story.append(Paragraph(desc, styles["rec"]))

    # ── IMPACTO ESTIMADO ──
    story.append(Paragraph("Impacto Estimado de las Optimizaciones", styles["h2"]))

    impact_data = [
        ["Optimizacion", "Senales Estimadas", "Win Rate Est.", "Pips Est."],
        ["Estado actual (sin cambios)", "~395", "33.4%", "-53.5"],
        ["Reversion solo score 5", "~150", "~42%", "+300"],
        ["+ Desactivar EUR/USD", "~120", "~45%", "+500"],
        ["+ Nuevos SL (JPY 1.0x ATR)", "~120", "~48%", "+650"],
        ["+ TP2/TP3 mas cercanos", "~120", "~48%", "+800"],
    ]

    col_w4 = [doc.width*0.40, doc.width*0.20, doc.width*0.20, doc.width*0.20]
    t4 = Table(impact_data, colWidths=col_w4)
    style4 = [
        ("BACKGROUND", (0, 0), (-1, 0), BG_PANEL),
        ("TEXTCOLOR", (0, 0), (-1, 0), ACCENT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 1), (0, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        # Row 1 (current - red)
        ("BACKGROUND", (0, 1), (-1, 1), BG_DARK),
        ("TEXTCOLOR", (0, 1), (-1, 1), RED),
        # Rows 2-5 (improvements - gradient green)
        ("BACKGROUND", (0, 2), (-1, 2), BG_ROW_ALT),
        ("TEXTCOLOR", (0, 2), (-1, 2), TEXT),
        ("TEXTCOLOR", (3, 2), (3, 2), GREEN),
        ("BACKGROUND", (0, 3), (-1, 3), BG_DARK),
        ("TEXTCOLOR", (0, 3), (-1, 3), TEXT),
        ("TEXTCOLOR", (2, 3), (2, 3), GREEN),
        ("TEXTCOLOR", (3, 3), (3, 3), GREEN),
        ("BACKGROUND", (0, 4), (-1, 4), BG_ROW_ALT),
        ("TEXTCOLOR", (0, 4), (-1, 4), TEXT),
        ("TEXTCOLOR", (2, 4), (2, 4), GREEN),
        ("TEXTCOLOR", (3, 4), (3, 4), GREEN),
        ("BACKGROUND", (0, 5), (-1, 5), BG_DARK),
        ("TEXTCOLOR", (0, 5), (-1, 5), GREEN),
        ("FONTNAME", (0, 5), (-1, 5), "Helvetica-Bold"),
    ]
    t4.setStyle(TableStyle(style4))
    story.append(t4)

    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("Nota: Las estimaciones de impacto son aproximadas basadas en los datos del backtest. "
                           "Los resultados futuros pueden variar segun condiciones de mercado.",
                           styles["small"]))

    # ── FOOTER ──
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("BuySell365 Pro | Backtest generado automaticamente | 17 Marzo 2026",
                           ParagraphStyle("footer", fontName="Helvetica", fontSize=8, textColor=TEXT_SEC, alignment=TA_CENTER)))

    # Build
    doc.build(story, onFirstPage=draw_background, onLaterPages=draw_background)
    print(f"PDF generado: {OUTPUT}")


if __name__ == "__main__":
    build_pdf()

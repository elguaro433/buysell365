"""Generate BuySell365 Pro Backtest Report PDF — v2 (Antes vs Despues)."""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER

OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest_report_v2.pdf")

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
        "h3": S("h3", fontName="Helvetica-Bold", fontSize=12, textColor=ACCENT, spaceBefore=4*mm, spaceAfter=2*mm),
        "body": S("body", spaceAfter=2*mm),
        "finding": S("finding", spaceAfter=3*mm, leftIndent=8*mm),
        "rec": S("rec", spaceAfter=3*mm, leftIndent=8*mm),
        "small": S("small", fontSize=8, textColor=TEXT_SEC, alignment=TA_CENTER),
    }

    story = []

    # ═══ PAGE 1: TITULO + COMPARATIVA ═══
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("BuySell365 Pro", st["title"]))
    story.append(Paragraph("Backtest — Antes vs Despues de Optimizacion", st["title2"]))
    story.append(Paragraph("Periodo: 60 dias | 24 Dic 2025 - 17 Mar 2026 | Velas 15 min | 6 activos", st["sub"]))

    # ── COMPARATIVA PRINCIPAL ──
    story.append(Paragraph("Comparativa General: Antes vs Despues", st["h2"]))

    comp = [
        ["Metrica", "ANTES", "DESPUES", "Cambio"],
        ["Total Senales", "395", "336", "-15%"],
        ["Win Rate", "33.4%", "34.2%", "+0.8%"],
        ["Pips Totales", "-53.5", "+594.1", "+647.6"],
        ["Pips / Senal", "-0.1", "+1.8", "+1.9"],
        ["TP2 Alcanzados", "2", "8", "4x mas"],
        ["TP3 Alcanzados", "0", "2", "Nuevo"],
        ["Losses (SL)", "205 (51.9%)", "175 (52.1%)", "-30 SL"],
    ]
    cw = [W*0.30, W*0.22, W*0.22, W*0.26]
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
        cs.append(("TEXTCOLOR", (1, i), (1, i), RED))       # ANTES = rojo
        cs.append(("TEXTCOLOR", (2, i), (2, i), GREEN))     # DESPUES = verde
        cs.append(("FONTNAME", (1, i), (2, i), "Helvetica-Bold"))
        # Cambio color
        cambio = comp[i][3]
        cc = GREEN if "+" in cambio or "mas" in cambio or "Nuevo" in cambio else (RED if "-" in cambio else TEXT)
        cs.append(("TEXTCOLOR", (3, i), (3, i), cc))
        cs.append(("FONTNAME", (3, i), (3, i), "Helvetica-Bold"))
    # Special: row 3 (Pips Totales) and row 7 (Losses) - fix colors
    cs.append(("TEXTCOLOR", (3, 7), (3, 7), GREEN))  # -30 SL is good
    t.setStyle(TableStyle(cs))
    story.append(t)

    # ── RESULTADO CLAVE ──
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        '<b><font color="#00c853" size="14">De -53 pips a +594 pips = +647 pips de mejora</font></b>',
        S("key", alignment=TA_CENTER, spaceAfter=6*mm)
    ))

    # ── POR ACTIVO ──
    story.append(Paragraph("Resultados por Activo (Despues)", st["h2"]))
    asset = [
        ["Activo", "Senales", "Win Rate", "TP1", "TP2", "TP3", "SL", "Pips", "Veredicto"],
        ["ORO", "23", "52.2%", "10", "2", "0", "11", "+92.3", "RENTABLE"],
        ["EUR/USD", "77", "23.4%", "14", "4", "0", "44", "-464.7", "FILTRADO*"],
        ["USD/JPY", "70", "40.0%", "26", "2", "0", "36", "+149.3", "RENTABLE"],
        ["GBP/JPY", "67", "32.8%", "21", "0", "1", "36", "-15.3", "NEUTRAL"],
        ["NASDAQ", "39", "35.9%", "14", "0", "0", "20", "+878.0", "EXCELENTE"],
        ["S&P 500", "60", "35.0%", "20", "0", "1", "28", "-45.4", "NEUTRAL"],
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
    verdicts = {"RENTABLE": GREEN, "EXCELENTE": GREEN, "NEUTRAL": YELLOW, "FILTRADO*": RED, "NO RENTABLE": RED}
    for i in range(1, len(asset)):
        bg = BG_ROW_ALT if i % 2 == 0 else BG_DARK
        as2.append(("BACKGROUND", (0, i), (-1, i), bg))
        as2.append(("TEXTCOLOR", (0, i), (0, i), TEXT))
        wr = float(asset[i][2].replace("%", ""))
        as2.append(("TEXTCOLOR", (2, i), (2, i), GREEN if wr >= 40 else (YELLOW if wr >= 30 else RED)))
        pv = float(asset[i][7])
        as2.append(("TEXTCOLOR", (7, i), (7, i), GREEN if pv >= 0 else RED))
        as2.append(("FONTNAME", (7, i), (7, i), "Helvetica-Bold"))
        vc = verdicts.get(asset[i][8], TEXT)
        as2.append(("TEXTCOLOR", (8, i), (8, i), vc))
        as2.append(("FONTNAME", (8, i), (8, i), "Helvetica-Bold"))
        for c in [1, 3, 4, 5, 6]:
            as2.append(("TEXTCOLOR", (c, i), (c, i), TEXT_SEC))
    t2.setStyle(TableStyle(as2))
    story.append(t2)
    story.append(Paragraph("* EUR/USD ahora requiere score 5 (divergencia) en el bot real. En el backtest no pasa por el scanner completo.", S("note", fontSize=8, textColor=TEXT_SEC, spaceAfter=2*mm)))

    # ── POR ESTRATEGIA ──
    story.append(Paragraph("Resultados por Estrategia", st["h2"]))

    strat_comp = [
        ["Estrategia", "Senales", "Win Rate", "Pips Antes", "Pips Despues", "Cambio"],
        ["Breakout", "47", "51.1%", "+1,624", "+1,658", "+34"],
        ["Pullback", "4", "25.0%", "-89", "-89", "0"],
        ["Reversion", "285", "31.6%", "-1,589", "-975", "+614"],
    ]
    scw = [W/6]*6
    t3 = Table(strat_comp, colWidths=scw)
    ss = [
        ("BACKGROUND", (0, 0), (-1, 0), BG_PANEL),
        ("TEXTCOLOR", (0, 0), (-1, 0), ACCENT),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        # Breakout
        ("BACKGROUND", (0, 1), (-1, 1), BG_DARK), ("TEXTCOLOR", (0, 1), (-1, 1), TEXT),
        ("TEXTCOLOR", (2, 1), (2, 1), GREEN), ("TEXTCOLOR", (3, 1), (4, 1), GREEN),
        ("TEXTCOLOR", (5, 1), (5, 1), GREEN), ("FONTNAME", (5, 1), (5, 1), "Helvetica-Bold"),
        # Pullback
        ("BACKGROUND", (0, 2), (-1, 2), BG_ROW_ALT), ("TEXTCOLOR", (0, 2), (-1, 2), TEXT_SEC),
        # Reversion
        ("BACKGROUND", (0, 3), (-1, 3), BG_DARK), ("TEXTCOLOR", (0, 3), (-1, 3), TEXT),
        ("TEXTCOLOR", (2, 3), (2, 3), RED),
        ("TEXTCOLOR", (3, 3), (3, 3), RED), ("TEXTCOLOR", (4, 3), (4, 3), YELLOW),
        ("TEXTCOLOR", (5, 3), (5, 3), GREEN), ("FONTNAME", (5, 3), (5, 3), "Helvetica-Bold"),
    ]
    t3.setStyle(TableStyle(ss))
    story.append(t3)

    # ═══ PAGE 2: HALLAZGOS + RECOMENDACIONES ═══
    story.append(PageBreak())
    story.append(Spacer(1, 5*mm))

    # ── CAMBIOS APLICADOS ──
    story.append(Paragraph("5 Cambios Aplicados", st["h2"]))

    cambios = [
        ("<b><font color='#00d4aa'>1. Reversion solo Score 5 (divergencia RSI obligatoria)</font></b><br/>"
         "Eliminadas las reversiones Score 4 (sin divergencia) que tenian 31% win rate. "
         "Solo pasan reversiones con divergencia RSI confirmada. Reduccion de ~344 a ~285 senales."),
        ("<b><font color='#00d4aa'>2. EUR/USD solo Score 5</font></b><br/>"
         "Peor activo del backtest (24.7% WR, -518 pips). Ahora requiere divergencia RSI "
         "para generar senal. Elimina la mayoria de senales perdedoras de este par."),
        ("<b><font color='#00d4aa'>3. TP2/TP3 mas cercanos (-30% / -40%)</font></b><br/>"
         "Antes: 0 TP3 y 2 TP2 en 395 senales. Despues: 2 TP3 y 8 TP2. "
         "Captura mas beneficio parcial en las operaciones ganadoras."),
        ("<b><font color='#00d4aa'>4. SL por estrategia</font></b><br/>"
         "Breakout (51% WR): SL +20% mas amplio para aguantar la volatilidad post-ruptura. "
         "Reversion (Score 5): SL +10% mas amplio."),
        ("<b><font color='#00d4aa'>5. Cooldown 60 min tras SL</font></b><br/>"
         "Era 20 min, ahora 60 min. Evita re-entrar rapidamente en la misma direccion "
         "perdedora. Hoy hubo 4 USD/JPY SELL en una tarde — con 60 min max serian 2."),
    ]
    for c in cambios:
        story.append(Paragraph(c, st["finding"]))

    # ── HALLAZGOS ──
    story.append(Paragraph("Hallazgos Clave", st["h2"]))

    hallazgos = [
        "<b><font color='#00c853'>Breakout es la estrategia ganadora:</font></b> 51.1% win rate, +35 pips/op. No cambio porque ya funcionaba bien.",
        "<b><font color='#00c853'>TP2/TP3 cercanos funcionan:</font></b> De 2 TP2 a 8, de 0 TP3 a 2. Mas beneficio capturado.",
        "<b><font color='#00c853'>NASDAQ mejoro dramaticamente:</font></b> De +204 a +878 pips gracias a TP2/TP3 alcanzables.",
        "<b><font color='#ffa726'>Reversion mejoro pero sigue negativa:</font></b> De -1,589 a -975 pips. La eliminacion de Score 4 ayudo pero las Score 5 aun tienen 31% WR.",
        "<b><font color='#ff5252'>EUR/USD sigue siendo problematico:</font></b> -464 pips. El filtro Score 5 del bot real deberia reducir esto significativamente.",
        "<b><font color='#00c853'>Resultado neto: de PERDIDA a GANANCIA:</font></b> -53 pips a +594 pips = sistema rentable.",
    ]
    for i, h in enumerate(hallazgos):
        story.append(Paragraph(f"{i+1}. {h}", st["finding"]))

    # ── PROXIMOS PASOS ──
    story.append(Paragraph("Proximos Pasos", st["h2"]))

    pasos = [
        "<b>Monitorear 1-2 semanas con los nuevos parametros</b> para validar en datos reales.",
        "<b>Si Reversion sigue perdiendo,</b> considerar desactivarla completamente y operar solo con Breakout + Pullback.",
        "<b>Evaluar EUR/USD</b> tras 2 semanas con filtro Score 5. Si sigue perdiendo, desactivar.",
        "<b>Correr backtest mensual</b> para ajustar parametros segun condiciones de mercado.",
    ]
    for i, p in enumerate(pasos):
        story.append(Paragraph(f"{i+1}. {p}", st["rec"]))

    # ── FOOTER ──
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("BuySell365 Pro | Backtest Comparativo v2 | 17 Marzo 2026", st["small"]))

    doc.build(story, onFirstPage=draw_bg, onLaterPages=draw_bg)
    print(f"PDF generado: {OUTPUT}")

if __name__ == "__main__":
    build_pdf()

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image

import os
HERE = os.path.dirname(os.path.abspath(__file__))
SP = os.path.join(HERE, "..", "..", "output", "belvidere_il") + "/"
os.makedirs(SP, exist_ok=True)
OUT = SP + "Belvidere_FY27_Tax_Revenue.pdf"

import csv

# Amounts come from the mapping file (treatment `tax` = gross, `offset` = rebates/pension share, `excluded_fee` left out).
CSV = os.path.join(HERE, "..", "..", "data", "belvidere_il", "fy2027_general_fund_tax_lines.csv")
L = ['Property tax', 'Municipal sales tax', 'State income tax', 'Home rule sales tax',
     'Utility taxes', 'Replacement tax', 'Video gaming tax', 'Other taxes']
CAT_OF = {'Municipal sales tax': 'Municipal sales tax'}
_rows = list(csv.DictReader(open(CSV)))
_by = {c: 0 for c in L}
OFFSETS = FEES = 0
for _r in _rows:
    _a = int(_r['amount'])
    if _r['treatment'] == 'tax':
        _by[_r['category']] += _a
    elif _r['treatment'] == 'offset':
        OFFSETS += _a
    else:
        FEES += _a
V = [_by[c] for c in L]
C = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#b4b2a9']
T = sum(V)
NET = T + OFFSETS
GF_REVENUE = 25830395  # printed TOTAL GENERAL FUND REVENUES, FY27 budget (checked by extract_budget.py)
assert (T, OFFSETS, NET) == (20998468, -532141, 20466327), (T, OFFSETS, NET)  # includes the $62,000 infrastructure maintenance line (a tax, decided 2026-10-22)

fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=200)
wedges, _ = ax.pie(V, colors=C, startangle=90, counterclock=False,
                   wedgeprops=dict(edgecolor='white', linewidth=1.5))
ax.legend(wedges, [f"{l}  ${v:,.0f}  ({v/T*100:.1f}%)" for l, v in zip(L, V)],
          loc='center left', bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=9)
ax.set_aspect('equal')
plt.tight_layout()
plt.savefig(SP + "pie.png", bbox_inches='tight')

ss = getSampleStyleSheet()
h = ParagraphStyle('h', parent=ss['Title'], fontSize=17, spaceAfter=2, alignment=0)
sub = ParagraphStyle('s', parent=ss['Normal'], fontSize=9.5, textColor=colors.HexColor('#52514e'))
body = ParagraphStyle('b', parent=ss['Normal'], fontSize=9, leading=12)
cell = ParagraphStyle('c', parent=ss['Normal'], fontSize=8.5, leading=10.5)
h2 = ParagraphStyle('h2', parent=ss['Heading3'], fontSize=11, spaceBefore=6, spaceAfter=4)

story = [
    Paragraph("City of Belvidere, IL: FY2027 General Fund Tax Revenue", h),
    Paragraph("Fiscal year May 1, 2026 to April 30, 2027. Source: adopted budget, Ordinance #753H "
              "(passed April 6, 2026). Revision #780H (Aug. 2026) changed only one salary and left revenues unchanged.", sub),
    Spacer(1, 8),
]

kpi = Table([["Gross tax revenue", "Share of all General Fund revenue", "Net after rebates & pension share"],
             [f"${T/1e6:.2f}M", f"{T/GF_REVENUE*100:.0f}%  (of ${GF_REVENUE/1e6:.2f}M)", f"${NET/1e6:.2f}M"]], colWidths=[2.4*inch]*3)
kpi.setStyle(TableStyle([
    ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f4f3ef')),
    ('FONTSIZE', (0, 0), (-1, 0), 8.5), ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#52514e')),
    ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'), ('FONTSIZE', (0, 1), (-1, 1), 15),
    ('TOPPADDING', (0, 1), (-1, 1), 4), ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
    ('LINEAFTER', (0, 0), (1, -1), 2, colors.white),
]))
story += [kpi, Spacer(1, 6), Image(SP + "pie.png", width=7.0*inch, height=3.2*inch, kind='proportional')]

story.append(Paragraph("Breakdown and source lines", h2))
rows = [
    ("Property tax", _by["Property tax"], "12 levies (pp. 1-7): general $1,769,385; police $1,650,212; fire $1,318,090; road & bridge $365,000; "
     "liability insurance $300,000; street lighting $210,000; FICA/Medicare $200,000; IMRF $65,000; refuse $50,000; "
     "forestry $40,000; audit $20,000; civil defense $7,000"),
    ("Municipal sales tax", _by["Municipal sales tax"], "p. 1"),
    ("State income tax", _by["State income tax"], "p. 1"),
    ("Home rule sales tax", _by["Home rule sales tax"], "p. 1"),
    ("Utility taxes", _by["Utility taxes"], "p. 7: electric $852,000; gas $470,000; telephone $125,000"),
    ("Replacement tax", _by["Replacement tax"], "p. 1 ($640,000) + p. 2 IMRF ($66,440)"),
    ("Video gaming tax", _by["Video gaming tax"], "p. 1 (\"Video Gambling\")"),
    ("Other taxes", _by["Other taxes"], "p. 1: local motor fuel $413,000; cannabis $220,000; local use $67,655; "
     "municipal infrastructure maintenance (telecom) $62,000; auto rental $10,500; hotel/motel $1,000"),
]
assert sum(r[1] for r in rows) == T
data = [["Tax", "FY27 amount", "Source in budget (page labels \"X of 15\")"]]
data += [[Paragraph(r[0], cell), f"${r[1]:,.0f}", Paragraph(r[2], cell)] for r in rows]
data.append([Paragraph("<b>Total (gross)</b>", cell), f"${T:,.0f}", ""])
t = Table(data, colWidths=[1.35*inch, 0.95*inch, 4.9*inch], repeatRows=1)
t.setStyle(TableStyle([
    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 8.5),
    ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8e7e1')),
    ('ALIGN', (1, 0), (1, -1), 'RIGHT'), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#d3d1c7')),
    ('FONTNAME', (1, -1), (1, -1), 'Helvetica-Bold'),
    ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
]))
story.append(t)

story.append(Paragraph("Notes", h2))
for n in [
    "<b>Gross vs. net.</b> The budget also deducts $160,000 in sales tax and $45,810 in property tax rebated to developers, "
    "and $326,331 in replacement tax passed to the police and fire pensions. Net tax revenue is $" + f"{NET:,}" + ".",
    "<b>Fees excluded.</b> Franchise fees ($150,000) are not counted as taxes. The $62,000 municipal infrastructure maintenance "
    "line, which the city's audits report as \"Telecommunications tax\", is counted as a tax (in Other taxes).",
    "<b>General Fund only.</b> Other funds also receive tax money: about $3.63M in pension property tax, $240,552 in pension "
    "replacement tax, about $1.18M in state motor fuel tax, and about $107,500 in TIF, special service area and capital fund "
    "property tax. These are left out because the pension levies match the pension expense lines in the police and fire budgets "
    "and could double-count.",
]:
    story.append(Paragraph("&bull; " + n, body))

SimpleDocTemplate(OUT, pagesize=letter, leftMargin=0.6*inch, rightMargin=0.6*inch,
                  topMargin=0.5*inch, bottomMargin=0.5*inch,
                  title="Belvidere FY2027 General Fund Tax Revenue").build(story)
print("ok")

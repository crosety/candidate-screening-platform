"""Export the process design Markdown to a two-page PDF.

Usage: uv run --with reportlab python tools/export_process_pdf.py [es|en]
The Markdown is the source; the PDF is a derived file.
"""

import re
import sys
from html import escape
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES: dict[str, dict[str, str]] = {
    "es": {
        "source": "docs/diseno-proceso.md",
        "out": "output/pdf/diseno-proceso-grupo-sazon.pdf",
        "header_left": "GRUPO SAZÓN  /  SELECCIÓN",
        "header_right": "Repartidores · España y México",
        "footer": "Diseño del proceso · Carlos Rosety",
        "step": "Paso",
        "title": "Grupo Sazón - Diseño del cribado de repartidores",
        "subject": "Proceso, preguntas, validaciones y revisión humana",
    },
    "en": {
        "source": "docs/process-design.md",
        "out": "output/pdf/process-design-grupo-sazon.pdf",
        "header_left": "GRUPO SAZÓN  /  HIRING",
        "header_right": "Delivery drivers · Spain and Mexico",
        "footer": "Process design · Carlos Rosety",
        "step": "Step",
        "title": "Grupo Sazón - Delivery driver screening design",
        "subject": "Process, questions, validation and human review",
    },
}
LANG = LANGUAGES[sys.argv[1] if len(sys.argv) > 1 else "en"]
SOURCE = ROOT / LANG["source"]
OUT = ROOT / LANG["out"]
OUT.parent.mkdir(parents=True, exist_ok=True)

styles = {
    "title": ParagraphStyle(
        "title",
        fontName="Helvetica-Bold",
        fontSize=19,
        leading=22,
        spaceAfter=13,
        textColor=colors.HexColor("#1b1b20"),
    ),
    "h2": ParagraphStyle(
        "h2",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        spaceBefore=10,
        spaceAfter=5,
        keepWithNext=True,
    ),
    "body": ParagraphStyle(
        "body", fontName="Helvetica", fontSize=9.5, leading=12.6, spaceAfter=6, alignment=TA_LEFT
    ),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.6, leading=11.1),
    "head": ParagraphStyle(
        "head", fontName="Helvetica-Bold", fontSize=8.4, leading=10.5, textColor=colors.white
    ),
    "bullet": ParagraphStyle(
        "bullet",
        fontName="Helvetica",
        fontSize=9.5,
        leading=12.6,
        leftIndent=8,
        firstLineIndent=-8,
        spaceAfter=6,
    ),
}


def inline(text: str) -> str:
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`([^`]+)`", r'<font name="Helvetica">\1</font>', text)
    return text


def frame(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(colors.HexColor("#6b6b73"))
    canvas.setFont("Helvetica", 8)
    canvas.drawString(36, h - 26, LANG["header_left"])
    canvas.drawRightString(w - 36, h - 26, LANG["header_right"])
    canvas.setStrokeColor(colors.HexColor("#dddddf"))
    canvas.line(36, 30, w - 36, 30)
    canvas.drawString(36, 18, LANG["footer"])
    canvas.drawRightString(w - 36, 18, str(doc.page))
    canvas.restoreState()


lines = SOURCE.read_text().splitlines()
story = []
i = 0
while i < len(lines):
    line = lines[i]
    if not line.strip():
        i += 1
        continue
    if line.startswith("# "):
        story.append(Paragraph(inline(line[2:]), styles["title"]))
        i += 1
        continue
    if line.startswith("## "):
        if line.startswith("## 4."):
            story.append(PageBreak())
        story.append(Paragraph(inline(line[3:]), styles["h2"]))
        i += 1
        continue
    if line.startswith("|"):
        rows: list[list[Any]] = []
        while i < len(lines) and lines[i].startswith("|"):
            cells = [x.strip() for x in lines[i].strip().strip("|").split("|")]
            if not all(re.fullmatch(r"[:\- ]+", x) for x in cells):
                style = styles["head"] if not rows else styles["cell"]
                rows.append([Paragraph(inline(x), style) for x in cells])
            i += 1
        widths = (
            [76, 158, A4[0] - 72 - 76 - 158]
            if rows[0][0].text == LANG["step"]
            else [106, 209, A4[0] - 72 - 106 - 209]
        )
        table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#25252a")),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.HexColor("#f4f4f5"), colors.white],
                    ),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#dddddf")),
                ]
            )
        )
        story.extend([table, Spacer(1, 7)])
        continue
    bullet = line.startswith("- ")
    para = [line[2:] if bullet else line]
    i += 1
    while i < len(lines) and lines[i].strip() and not lines[i].startswith(("#", "|", "- ")):
        para.append(lines[i].strip())
        i += 1
    text = inline(" ".join(para))
    story.append(Paragraph(("- " if bullet else "") + text, styles["bullet" if bullet else "body"]))

doc = SimpleDocTemplate(
    str(OUT),
    pagesize=A4,
    leftMargin=36,
    rightMargin=36,
    topMargin=41,
    bottomMargin=42,
    title=LANG["title"],
    author="Carlos Rosety",
    subject=LANG["subject"],
)
doc.build(story, onFirstPage=frame, onLaterPages=frame)
print(OUT)

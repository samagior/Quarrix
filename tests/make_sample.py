"""Generate a sample PDF: headings on 3 levels, paragraphs, a table and a chart."""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def build(path: Path) -> Path:
    ss = getSampleStyleSheet()
    story = [
        Paragraph("Slab Design Guide", ss["Title"]),
        Paragraph("1 General", ss["Heading1"]),
        Paragraph("This document presents design rules for reinforced concrete slabs. " * 4, ss["BodyText"]),
        Paragraph("1.1 Scope", ss["Heading2"]),
        Paragraph("The rules apply to solid cast-in-place slabs. " * 3, ss["BodyText"]),
        Paragraph("2 Loads", ss["Heading1"]),
        Paragraph("2.1 Dead loads", ss["Heading2"]),
        Paragraph("2.1.1 Unit weights", ss["Heading3"]),
        Paragraph("Table 1 – Unit weights of materials", ss["BodyText"]),
    ]
    t = Table([
        ["Material", "Unit weight (kN/m³)", "Note"],
        ["Reinforced concrete", "24.0", "typical"],
        ["Steel", "77.0", ""],
        ["Softwood", "5.0", "dry"],
    ])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    story += [t, Spacer(1, 12)]
    d = Drawing(400, 200)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 50, 30, 300, 150
    chart.data = [[24, 77, 5]]
    chart.categoryAxis.categoryNames = ["Concrete", "Steel", "Wood"]
    d.add(chart)
    story += [d, Paragraph("Figure 1 – Comparison of unit weights", ss["BodyText"])]
    story += [Paragraph("2.2 Live loads", ss["Heading2"]),
              Paragraph("Live loads depend on the occupancy of the space. " * 3, ss["BodyText"])]
    SimpleDocTemplate(str(path), pagesize=A4, title="Slab Design Guide",
                      author="Example Publisher").build(story)
    return path


if __name__ == "__main__":
    print(build(Path(sys.argv[1] if len(sys.argv) > 1 else "sample.pdf")))

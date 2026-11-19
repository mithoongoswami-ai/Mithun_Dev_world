
from datetime import datetime
from pathlib import Path
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image,
    KeepTogether,
)

from report_renderer import chart_png_bytes


REPORT_DIR = Path("generated_reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)

PAGE_SIZE = landscape(A4)


def build_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name="ReportTitleCustom",
        parent=styles["Title"],
        fontSize=21,
        leading=26,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#17365D"),
        spaceAfter=10,
    ))

    styles.add(ParagraphStyle(
        name="ReportHeadingCustom",
        parent=styles["Heading2"],
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#17365D"),
        spaceBefore=13,
        spaceAfter=7,
        keepWithNext=True,
    ))

    styles.add(ParagraphStyle(
        name="ReportBodyCustom",
        parent=styles["BodyText"],
        fontSize=8.5,
        leading=12,
        spaceAfter=6,
    ))

    styles.add(ParagraphStyle(
        name="ReportBulletCustom",
        parent=styles["BodyText"],
        fontSize=8.5,
        leading=12,
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=5,
    ))

    styles.add(ParagraphStyle(
        name="ReportTableHeaderCustom",
        parent=styles["BodyText"],
        fontSize=6.5,
        leading=8,
        textColor=colors.white,
        alignment=TA_CENTER,
    ))

    styles.add(ParagraphStyle(
        name="ReportTableCellCustom",
        parent=styles["BodyText"],
        fontSize=6,
        leading=7.5,
    ))

    return styles


def text_paragraph(value, style):
    if value is None:
        value = "—"

    if isinstance(value, (dict, list)):
        value = str(value)

    value = escape(str(value)).replace("\n", "<br/>")
    return Paragraph(value, style)


def add_heading(story, text, styles):
    story.append(
        Paragraph(
            escape(str(text)),
            styles["ReportHeadingCustom"]
        )
    )


def add_bullets(story, items, styles):
    if isinstance(items, str):
        items = [items]

    for item in items or []:
        story.append(
            Paragraph(
                "&#8226;&nbsp;&nbsp;" +
                escape(str(item)),
                styles["ReportBulletCustom"]
            )
        )


def create_pdf_table(table_data, styles):
    if not isinstance(table_data, dict):
        return None

    columns = table_data.get("columns", [])
    rows = table_data.get("rows", [])

    if not columns:
        return None

    data = [[
        text_paragraph(column, styles["ReportTableHeaderCustom"])
        for column in columns
    ]]

    for row in rows:
        if isinstance(row, dict):
            values = [row.get(column, "") for column in columns]
        elif isinstance(row, (list, tuple)):
            values = list(row)
        else:
            continue

        values = values[:len(columns)]
        values += [""] * (len(columns) - len(values))

        data.append([
            text_paragraph(value, styles["ReportTableCellCustom"])
            for value in values
        ])

    available_width = PAGE_SIZE[0] - 24 * mm
    column_width = available_width / len(columns)

    result = Table(
        data,
        colWidths=[column_width] * len(columns),
        repeatRows=1,
        splitByRow=1,
        hAlign="LEFT",
    )

    result.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17365D")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D9E2F3")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white,
            colors.HexColor("#F4F7FB"),
        ]),
    ]))

    return result


def add_table_section(story, table_data, styles):
    if not isinstance(table_data, dict):
        return

    title = table_data.get("title", "Consolidated Data")
    add_heading(story, title, styles)

    table = create_pdf_table(table_data, styles)

    if table:
        story.append(table)
        story.append(Spacer(1, 8))


def add_footer(canvas, document):
    canvas.saveState()

    width, _ = PAGE_SIZE
    canvas.setStrokeColor(colors.HexColor("#D9E2F3"))
    canvas.line(12 * mm, 12 * mm, width - 12 * mm, 12 * mm)

    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.grey)

    canvas.drawString(
        12 * mm,
        8 * mm,
        "SAP Inventory Intelligence | Confidential"
    )

    canvas.drawRightString(
        width - 12 * mm,
        8 * mm,
        f"Page {document.page}"
    )

    canvas.restoreState()


def generate_pdf_report(data):
    if not isinstance(data, dict):
        raise ValueError("Report data must be a dictionary.")

    if data.get("retrieval_status") == "failed":
        raise RuntimeError(
            "Cannot generate a business report because "
            "inventory data retrieval failed: "
            + str(data.get("retrieval_error", "Unknown error"))
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = REPORT_DIR / f"SAP_Inventory_Report_{timestamp}.pdf"

    styles = build_styles()
    story = []

    title = data.get("report_title", "SAP Inventory Report")

    # Title and report metadata
    story.append(Paragraph(
        escape(str(title)),
        styles["ReportTitleCustom"]
    ))

    story.append(Paragraph(
        "SAP Inventory Intelligence | Generated: " +
        datetime.now().strftime("%d %B %Y, %H:%M"),
        styles["ReportBodyCustom"]
    ))

    story.append(Spacer(1, 8))

    # Report request
    if data.get("user_query"):
        add_heading(story, "1. Report Scope", styles)
        story.append(text_paragraph(
            data["user_query"],
            styles["ReportBodyCustom"]
        ))

    # Executive summary bullets
    add_heading(story, "2. Executive Summary", styles)
    add_bullets(
        story,
        data.get("executive_summary", []),
        styles
    )

    # Overall assessment
    if data.get("generated_summary"):
        add_heading(story, "3. Overall Assessment", styles)
        story.append(text_paragraph(
            data["generated_summary"],
            styles["ReportBodyCustom"]
        ))

    # Charts
    visualizations = data.get("visualizations", [])
    chart_count = 0

    for chart in visualizations:
        if not isinstance(chart, dict):
            continue

        image_bytes = chart_png_bytes(chart)

        if not image_bytes:
            continue

        chart_count += 1
        add_heading(
            story,
            chart.get("title", f"Visualization {chart_count}"),
            styles
        )

        import io
        chart_image = Image(io.BytesIO(image_bytes))
        chart_image._restrictSize(230 * mm, 125 * mm)

        story.append(chart_image)

        if chart.get("description"):
            story.append(text_paragraph(
                chart["description"],
                styles["ReportBodyCustom"]
            ))

        story.append(Spacer(1, 8))

    # Top-level consolidated table
    if data.get("consolidated_table"):
        add_table_section(
            story,
            data["consolidated_table"],
            styles
        )

    # Dynamic report sections
    for section in data.get("sections", []):
        if not isinstance(section, dict):
            continue

        heading = section.get("heading", "Report Section")
        add_heading(story, heading, styles)

        if section.get("content"):
            story.append(text_paragraph(
                section["content"],
                styles["ReportBodyCustom"]
            ))

        if section.get("table"):
            add_table_section(
                story,
                section["table"],
                styles
            )

    # Findings and actions
    add_heading(story, "Key Findings", styles)
    add_bullets(story, data.get("key_findings", []), styles)

    add_heading(story, "Recommendations and Actions", styles)
    add_bullets(story, data.get("recommendations", []), styles)

    if data.get("data_limitations"):
        add_heading(story, "Data Limitations and Assumptions", styles)
        add_bullets(story, data["data_limitations"], styles)

    document = SimpleDocTemplate(
        str(pdf_path),
        pagesize=PAGE_SIZE,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=18 * mm,
        title=str(title),
        author="SAP Inventory Intelligence",
        allowSplitting=1,
    )

    document.build(
        story,
        onFirstPage=add_footer,
        onLaterPages=add_footer,
    )

    print("PDF report created:", pdf_path)
    return str(pdf_path)

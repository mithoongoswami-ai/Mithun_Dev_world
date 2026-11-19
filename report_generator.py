
import os
import re
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
    PageBreak,
)


# =========================================================
# 1. CONFIGURATION
# =========================================================

REPORT_DIR = Path("generated_reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)

PAGE_SIZE = landscape(A4)


# =========================================================
# 2. PDF STYLES
# =========================================================

def build_styles():

    styles = getSampleStyleSheet()

    styles.add(
        ParagraphStyle(
            name="DynamicReportTitle",
            parent=styles["Title"],
            fontSize=21,
            leading=26,
            alignment=TA_LEFT,
            textColor=colors.HexColor("#17365D"),
            spaceAfter=10
        )
    )

    styles.add(
        ParagraphStyle(
            name="DynamicSectionHeading",
            parent=styles["Heading2"],
            fontSize=13,
            leading=17,
            textColor=colors.HexColor("#17365D"),
            spaceBefore=13,
            spaceAfter=7,
            keepWithNext=True
        )
    )

    styles.add(
        ParagraphStyle(
            name="DynamicSubHeading",
            parent=styles["Heading3"],
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#305496"),
            spaceBefore=8,
            spaceAfter=5,
            keepWithNext=True
        )
    )

    styles.add(
        ParagraphStyle(
            name="DynamicBody",
            parent=styles["BodyText"],
            fontSize=8.5,
            leading=12,
            spaceAfter=6
        )
    )

    styles.add(
        ParagraphStyle(
            name="DynamicSmall",
            parent=styles["BodyText"],
            fontSize=7,
            leading=9
        )
    )

    styles.add(
        ParagraphStyle(
            name="DynamicTableHeader",
            parent=styles["BodyText"],
            fontSize=7,
            leading=9,
            textColor=colors.white,
            alignment=TA_CENTER
        )
    )

    styles.add(
        ParagraphStyle(
            name="DynamicTableCell",
            parent=styles["BodyText"],
            fontSize=6.5,
            leading=8
        )
    )

    styles.add(
        ParagraphStyle(
            name="DynamicTableCellRight",
            parent=styles["BodyText"],
            fontSize=6.5,
            leading=8,
            alignment=TA_CENTER
        )
    )

    return styles


# =========================================================
# 3. TEXT FORMATTING
# =========================================================

def safe_text(value):

    if value is None:
        return "—"

    if isinstance(value, bool):
        return "Yes" if value else "No"

    if isinstance(value, (dict, list)):
        value = str(value)

    if isinstance(value, float):

        if value.is_integer():
            value = int(value)

        else:
            value = f"{value:,.2f}"

    text = str(value)

    text = escape(text)

    text = text.replace("\n", "<br/>")

    return text


def make_paragraph(value, style):

    return Paragraph(
        safe_text(value),
        style
    )


# =========================================================
# 4. FOOTER
# =========================================================

def add_footer(canvas, document):

    canvas.saveState()

    width, height = PAGE_SIZE

    canvas.setStrokeColor(
        colors.HexColor("#D9E2F3")
    )

    canvas.line(
        12 * mm,
        12 * mm,
        width - 12 * mm,
        12 * mm
    )

    canvas.setFont(
        "Helvetica",
        7
    )

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


# =========================================================
# 5. DYNAMIC TABLE GENERATION
# =========================================================

def create_dynamic_table(table_data, styles):

    if not isinstance(table_data, dict):
        return None

    columns = table_data.get(
        "columns",
        []
    )

    rows = table_data.get(
        "rows",
        []
    )

    if not columns or not rows:
        return None

    column_count = len(columns)

    # Header row.
    pdf_data = [
        [
            make_paragraph(
                column,
                styles["DynamicTableHeader"]
            )
            for column in columns
        ]
    ]

    # Data rows.
    for row in rows:

        if isinstance(row, dict):

            values = [
                row.get(column)
                for column in columns
            ]

        elif isinstance(row, (list, tuple)):

            values = list(row)

        else:

            values = [row]

        values = values[:column_count]

        while len(values) < column_count:
            values.append(None)

        formatted_row = []

        for value in values:

            formatted_row.append(
                make_paragraph(
                    value,
                    styles["DynamicTableCell"]
                )
            )

        pdf_data.append(formatted_row)

    # Distribute available page width among columns.
    available_width = PAGE_SIZE[0] - (24 * mm)

    col_width = available_width / column_count

    col_widths = [
        col_width
        for _ in range(column_count)
    ]

    result = Table(
        pdf_data,
        colWidths=col_widths,
        repeatRows=1,
        splitByRow=1,
        hAlign="LEFT"
    )

    result.setStyle(
        TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                colors.HexColor("#17365D")
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.35,
                colors.HexColor("#D9E2F3")
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP"
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                4
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                4
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
            (
                "ROWBACKGROUNDS",
                (0, 1),
                (-1, -1),
                [
                    colors.white,
                    colors.HexColor("#F4F7FB")
                ]
            ),
        ])
    )

    return result


# =========================================================
# 6. ADD LIST SECTION
# =========================================================

def add_bullet_section(
    story,
    heading,
    items,
    styles
):

    if not items:
        return

    story.append(
        Paragraph(
            escape(heading),
            styles["DynamicSectionHeading"]
        )
    )

    for item in items:

        bullet_text = (
            "&#8226;&nbsp;&nbsp;"
            + safe_text(item)
        )

        story.append(
            Paragraph(
                bullet_text,
                styles["DynamicBody"]
            )
        )


# =========================================================
# 7. PDF GENERATION
# =========================================================

def generate_pdf_report(data):

    if not isinstance(data, dict):

        raise ValueError(
            "generate_pdf_report expects a dictionary."
        )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    pdf_path = REPORT_DIR / (
        f"SAP_Report_{timestamp}.pdf"
    )

    styles = build_styles()

    story = []

    # -----------------------------------------------------
    # REPORT TITLE
    # -----------------------------------------------------

    title = data.get(
        "report_title",
        "SAP Inventory Report"
    )

    story.append(
        Paragraph(
            safe_text(title),
            styles["DynamicReportTitle"]
        )
    )

    story.append(
        Paragraph(
            "Generated on: "
            + datetime.now().strftime(
                "%d %B %Y, %H:%M"
            ),
            styles["DynamicSmall"]
        )
    )

    story.append(Spacer(1, 8))

    # -----------------------------------------------------
    # USER QUERY
    # -----------------------------------------------------

    user_query = data.get("user_query")

    if user_query:

        story.append(
            Paragraph(
                "Report Request",
                styles["DynamicSectionHeading"]
            )
        )

        story.append(
            Paragraph(
                safe_text(user_query),
                styles["DynamicBody"]
            )
        )

    # -----------------------------------------------------
    # EXECUTIVE SUMMARY
    # -----------------------------------------------------

    summary = data.get(
        "generated_summary"
    )

    if summary:

        story.append(
            Paragraph(
                "Executive Summary",
                styles["DynamicSectionHeading"]
            )
        )

        story.append(
            Paragraph(
                safe_text(summary),
                styles["DynamicBody"]
            )
        )

    # -----------------------------------------------------
    # DYNAMIC REPORT SECTIONS
    # -----------------------------------------------------

    sections = data.get(
        "sections",
        []
    )

    for index, section in enumerate(
        sections,
        start=1
    ):

        if not isinstance(section, dict):
            continue

        heading = section.get(
            "heading",
            f"Section {index}"
        )

        story.append(
            Paragraph(
                safe_text(heading),
                styles["DynamicSectionHeading"]
            )
        )

        content = section.get("content")

        if content:

            story.append(
                Paragraph(
                    safe_text(content),
                    styles["DynamicBody"]
                )
            )

        table_data = section.get("table")

        if table_data:

            table_title = table_data.get(
                "title"
            )

            if table_title:

                story.append(
                    Paragraph(
                        safe_text(table_title),
                        styles["DynamicSubHeading"]
                    )
                )

            table = create_dynamic_table(
                table_data,
                styles
            )

            if table:

                story.append(table)
                story.append(Spacer(1, 8))

    # -----------------------------------------------------
    # KEY FINDINGS
    # -----------------------------------------------------

    add_bullet_section(
        story,
        "Key Findings",
        data.get("key_findings", []),
        styles
    )

    # -----------------------------------------------------
    # RECOMMENDATIONS
    # -----------------------------------------------------

    add_bullet_section(
        story,
        "Recommendations",
        data.get("recommendations", []),
        styles
    )

    # -----------------------------------------------------
    # DATA LIMITATIONS
    # -----------------------------------------------------

    add_bullet_section(
        story,
        "Data Limitations",
        data.get("data_limitations", []),
        styles
    )

    # -----------------------------------------------------
    # FALLBACK IF REPORT IS EMPTY
    # -----------------------------------------------------

    if not story:

        story.append(
            Paragraph(
                "No report content was returned.",
                styles["DynamicBody"]
            )
        )

    # -----------------------------------------------------
    # BUILD PDF
    # -----------------------------------------------------

    document = SimpleDocTemplate(
        str(pdf_path),
        pagesize=PAGE_SIZE,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=18 * mm,
        title=str(title),
        author="SAP Inventory Intelligence"
    )

    document.build(
        story,
        onFirstPage=add_footer,
        onLaterPages=add_footer
    )

    print("Dynamic PDF report created:", pdf_path)

    return str(pdf_path)

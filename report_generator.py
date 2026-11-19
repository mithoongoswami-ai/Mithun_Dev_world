
import os
import re
import io
import uuid
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
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
    PageBreak,
)

from report_renderer import chart_png_bytes


def safe_text(value):
    if value is None:
        return ""

    text = str(value)

    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def build_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name="MRPTitle",
        parent=styles["Title"],
        fontSize=20,
        leading=25,
        textColor=colors.HexColor("#17365D"),
        alignment=TA_LEFT,
        spaceAfter=8,
    ))

    styles.add(ParagraphStyle(
        name="MRPSection",
        parent=styles["Heading2"],
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#17365D"),
        spaceBefore=14,
        spaceAfter=8,
    ))

    styles.add(ParagraphStyle(
        name="MRPBody",
        parent=styles["BodyText"],
        fontSize=9,
        leading=13,
        spaceAfter=6,
    ))

    styles.add(ParagraphStyle(
        name="MRPBullet",
        parent=styles["BodyText"],
        fontSize=9,
        leading=13,
        leftIndent=12,
        firstLineIndent=-8,
        spaceAfter=5,
    ))

    styles.add(ParagraphStyle(
        name="MRPTableHeader",
        parent=styles["BodyText"],
        fontSize=7,
        leading=9,
        textColor=colors.white,
    ))

    styles.add(ParagraphStyle(
        name="MRPTableCell",
        parent=styles["BodyText"],
        fontSize=6.5,
        leading=8,
    ))

    return styles


def add_bullets(story, items, styles):
    if not items:
        story.append(
            Paragraph("No additional findings.", styles["MRPBody"])
        )
        return

    if isinstance(items, str):
        items = [items]

    for item in items:
        story.append(
            Paragraph(
                "&#8226; " + safe_text(item),
                styles["MRPBullet"]
            )
        )


def create_dynamic_table(columns, rows, styles):
    if not columns or not rows:
        return None

    data = [[
        Paragraph(safe_text(column), styles["MRPTableHeader"])
        for column in columns
    ]]

    for row in rows:
        cells = []

        for column in columns:
            if isinstance(row, dict):
                value = row.get(column)
            else:
                try:
                    value = row[columns.index(column)]
                except (IndexError, TypeError, ValueError):
                    value = None

            cells.append(
                Paragraph(safe_text(value), styles["MRPTableCell"])
            )

        data.append(cells)

    page_width = landscape(A4)[0] - 25 * mm
    col_width = page_width / len(columns)

    table = Table(
        data,
        colWidths=[col_width] * len(columns),
        repeatRows=1,
        hAlign="LEFT",
    )

    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17365D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D9E2F3")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white,
            colors.HexColor("#F3F6FA")
        ]),
    ]))

    return table


def footer(canvas, document):
    canvas.saveState()

    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.grey)

    canvas.drawString(
        13 * mm,
        8 * mm,
        "SAP Inventory Intelligence | MRP Controller Report"
    )

    canvas.drawRightString(
        landscape(A4)[0] - 13 * mm,
        8 * mm,
        f"Page {document.page}"
    )

    canvas.restoreState()


def generate_pdf_report(report):
    output_dir = "generated_reports"
    os.makedirs(output_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    safe_title = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        str(report.get("report_title", "inventory_report"))
    )[:45]

    filename = (
        f"{safe_title}_{timestamp}_{uuid.uuid4().hex[:6]}.pdf"
    )

    pdf_path = os.path.join(output_dir, filename)

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=13 * mm,
        bottomMargin=15 * mm,
        title=str(report.get("report_title", "Inventory Report")),
    )

    styles = build_styles()
    story = []

    # -----------------------------------------------------
    # TITLE AND REQUEST
    # -----------------------------------------------------

    story.append(Paragraph(
        safe_text(report.get(
            "report_title",
            "SAP Inventory Intelligence Report"
        )),
        styles["MRPTitle"]
    ))

    story.append(Paragraph(
        "Generated: " + safe_text(
            report.get(
                "generated_at",
                datetime.now().strftime("%d %B %Y %H:%M")
            )
        ),
        styles["MRPBody"]
    ))

    story.append(Paragraph(
        "<b>User request:</b> " +
        safe_text(report.get("user_query", "")),
        styles["MRPBody"]
    ))

    story.append(Spacer(1, 5 * mm))

    # -----------------------------------------------------
    # EXECUTIVE SUMMARY
    # -----------------------------------------------------

    story.append(Paragraph(
        "Executive Summary",
        styles["MRPSection"]
    ))

    add_bullets(
        story,
        report.get("executive_summary", []),
        styles
    )

    if report.get("generated_summary"):
        story.append(Paragraph(
            "Overall Assessment",
            styles["MRPSection"]
        ))

        story.append(Paragraph(
            safe_text(report["generated_summary"]),
            styles["MRPBody"]
        ))

    # -----------------------------------------------------
    # KPI SUMMARY
    # -----------------------------------------------------

    summary = report.get("summary", {})

    if summary:
        story.append(Paragraph(
            "Inventory KPI Summary",
            styles["MRPSection"]
        ))

        kpi_data = [
            ["Total materials", "Critical", "High risk", "Normal"],
            [
                str(summary.get("total_materials", 0)),
                str(summary.get("critical_items", 0)),
                str(summary.get("high_risk_items", 0)),
                str(summary.get("normal_items", 0)),
            ]
        ]

        kpi_table = Table(
            kpi_data,
            colWidths=[55 * mm] * 4
        )

        kpi_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17365D")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#F3F6FA")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("PADDING", (0, 0), (-1, -1), 8),
        ]))

        story.append(kpi_table)

    # -----------------------------------------------------
    # CHARTS
    # -----------------------------------------------------

    charts = report.get("visualizations", [])

    if charts:
        story.append(Paragraph(
            "Inventory Visual Analytics",
            styles["MRPSection"]
        ))

        for chart in charts:
            image_bytes = chart_png_bytes(chart)

            if not image_bytes:
                continue

            story.append(Paragraph(
                safe_text(chart.get("title", "Inventory Chart")),
                styles["Heading3"]
            ))

            image = Image(
                io.BytesIO(image_bytes),
                width=230 * mm,
                height=105 * mm,
                kind="proportional"
            )

            story.append(image)
            story.append(Spacer(1, 5 * mm))

    # -----------------------------------------------------
    # INVENTORY TABLE
    # -----------------------------------------------------

    inventory = report.get("inventory", [])

    if inventory:
        story.append(PageBreak())

        story.append(Paragraph(
            "Consolidated Inventory Position",
            styles["MRPSection"]
        ))

        columns = [
            "material_number",
            "plant",
            "storage_location",
            "base_uom",
            "unrestricted_stock",
            "safety_stock",
            "blocked_stock",
            "in_transit_stock",
            "quality_inspection_stock",
            "stock_value_eur",
            "risk",
        ]

        table = create_dynamic_table(
            columns,
            inventory,
            styles
        )

        if table:
            story.append(table)

    # -----------------------------------------------------
    # RECOMMENDATIONS
    # -----------------------------------------------------

    story.append(Paragraph(
        "MRP Recommendations",
        styles["MRPSection"]
    ))

    add_bullets(
        story,
        report.get("recommendations", []),
        styles
    )

    # -----------------------------------------------------
    # DATA LIMITATIONS
    # -----------------------------------------------------

    limitations = report.get("data_limitations", [])

    if limitations:
        story.append(Paragraph(
            "Data Limitations",
            styles["MRPSection"]
        ))

        add_bullets(story, limitations, styles)

    doc.build(
        story,
        onFirstPage=footer,
        onLaterPages=footer
    )

    return pdf_path

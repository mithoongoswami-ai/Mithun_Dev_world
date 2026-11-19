
import base64
import html
import io
from datetime import datetime

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd


def esc(value):
    """Safely render values inside HTML."""
    if value is None:
        return ""
    return html.escape(str(value))


def chart_to_base64(fig):
    """Convert a Matplotlib figure into an embeddable PNG."""
    buffer = io.BytesIO()
    fig.savefig(
        buffer,
        format="png",
        dpi=160,
        bbox_inches="tight",
        facecolor="white"
    )
    plt.close(fig)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("utf-8")


def render_pie_chart(chart):
    labels = chart.get("labels", [])
    values = chart.get("values", [])

    if not labels or not values or len(labels) != len(values):
        return ""

    fig, ax = plt.subplots(figsize=(7, 4.5))
    wedges, _, _ = ax.pie(
        values,
        autopct="%1.1f%%",
        startangle=90,
        pctdistance=0.78,
        wedgeprops={"width": 0.42, "edgecolor": "white"}
    )

    ax.legend(
        wedges,
        [f"{label}: {value}" for label, value in zip(labels, values)],
        loc="center left",
        bbox_to_anchor=(1, 0.5),
        frameon=False
    )
    ax.set_title(chart.get("title", "Distribution"), fontsize=14, pad=16)
    ax.axis("equal")

    return chart_to_base64(fig)


def render_bar_chart(chart):
    categories = chart.get("categories", [])
    series = chart.get("series", [])

    if not categories or not series:
        return ""

    fig, ax = plt.subplots(figsize=(10, 5))
    width = 0.8 / max(len(series), 1)
    positions = list(range(len(categories)))

    for index, item in enumerate(series):
        values = item.get("values", [])
        if len(values) != len(categories):
            continue

        offsets = [
            position - 0.4 + width / 2 + index * width
            for position in positions
        ]

        ax.bar(
            offsets,
            values,
            width=width,
            label=item.get("name", f"Series {index + 1}")
        )

    ax.set_xticks(positions)
    ax.set_xticklabels(categories, rotation=35, ha="right")
    ax.set_title(chart.get("title", "Comparison"), fontsize=14)
    ax.set_ylabel(chart.get("y_axis_title", "Quantity"))
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    ax.legend()
    fig.tight_layout()

    return chart_to_base64(fig)


def render_line_chart(chart):
    categories = chart.get("categories", [])
    series = chart.get("series", [])

    if not categories or not series:
        return ""

    fig, ax = plt.subplots(figsize=(10, 5))

    for item in series:
        values = item.get("values", [])
        if len(values) != len(categories):
            continue

        ax.plot(
            categories,
            values,
            marker="o",
            linewidth=2,
            label=item.get("name", "Series")
        )

    ax.set_title(chart.get("title", "Trend Analysis"), fontsize=14)
    ax.set_ylabel(chart.get("y_axis_title", "Value"))
    ax.grid(True, linestyle="--", alpha=0.3)
    ax.legend()
    plt.xticks(rotation=35, ha="right")
    fig.tight_layout()

    return chart_to_base64(fig)


def render_gantt_chart(chart):
    tasks = chart.get("tasks", [])

    if not tasks:
        return ""

    parsed_tasks = []

    for task in tasks:
        try:
            start = datetime.fromisoformat(task["start"])
            end = datetime.fromisoformat(task["end"])

            if end <= start:
                continue

            parsed_tasks.append((task.get("task", "Activity"), start, end))
        except (KeyError, TypeError, ValueError):
            continue

    if not parsed_tasks:
        return ""

    fig_height = max(3.5, len(parsed_tasks) * 0.45)
    fig, ax = plt.subplots(figsize=(10, fig_height))

    for index, (name, start, end) in enumerate(parsed_tasks):
        left = mdates.date2num(start)
        duration = (end - start).total_seconds() / 86400

        ax.barh(
            index,
            duration,
            left=left,
            height=0.55
        )

    ax.set_yticks(range(len(parsed_tasks)))
    ax.set_yticklabels([item[0] for item in parsed_tasks])
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %Y"))
    ax.set_title(chart.get("title", "Activity Timeline"), fontsize=14)
    ax.grid(axis="x", linestyle="--", alpha=0.3)
    plt.xticks(rotation=25)
    fig.tight_layout()

    return chart_to_base64(fig)


def render_chart(chart):
    chart_type = str(chart.get("type", "")).lower()

    renderers = {
        "pie": render_pie_chart,
        "donut": render_pie_chart,
        "bar": render_bar_chart,
        "line": render_line_chart,
        "gantt": render_gantt_chart
    }

    renderer = renderers.get(chart_type)

    if not renderer:
        return ""

    image_data = renderer(chart)

    if not image_data:
        return ""

    return f"""
        <div class="chart-card">
            <h3>{esc(chart.get("title", "Analysis"))}</h3>
            <img
                src="data:image/png;base64,{image_data}"
                class="report-chart"
                alt="{esc(chart.get('title', 'Report chart'))}"
            />
            <p class="chart-description">
                {esc(chart.get("description", ""))}
            </p>
        </div>
    """


def render_table(table):
    if not isinstance(table, dict):
        return ""

    columns = table.get("columns", [])
    rows = table.get("rows", [])

    if not columns:
        return ""

    header = "".join(
        f"<th>{esc(column)}</th>" for column in columns
    )

    body_rows = []

    for row in rows:
        if isinstance(row, dict):
            cells = [row.get(column, "") for column in columns]
        elif isinstance(row, (list, tuple)):
            cells = list(row)
        else:
            continue

        cells = cells[:len(columns)]
        cells += [""] * (len(columns) - len(cells))

        body_rows.append(
            "<tr>" +
            "".join(f"<td>{esc(value)}</td>" for value in cells) +
            "</tr>"
        )

    title = table.get("title", "Consolidated Data")

    return f"""
        <div class="table-section">
            <h3>{esc(title)}</h3>
            <div class="table-scroll">
                <table class="report-table">
                    <thead><tr>{header}</tr></thead>
                    <tbody>{''.join(body_rows)}</tbody>
                </table>
            </div>
            <p class="table-count">
                {len(body_rows)} records
            </p>
        </div>
    """


def render_bullet_list(items):
    if not items:
        return "<p>Not provided in the generated report.</p>"

    return (
        "<ul class='report-bullets'>" +
        "".join(f"<li>{esc(item)}</li>" for item in items) +
        "</ul>"
    )


def render_report_html(report):
    """Render the structured agent response as a professional report."""

    if not isinstance(report, dict):
        report = {"generated_summary": str(report)}

    title = report.get("report_title", "Inventory Risk Assessment")
    query = report.get("user_query", "")
    summary = report.get("generated_summary", "")
    executive_summary = report.get("executive_summary", [])
    findings = report.get("key_findings", [])
    recommendations = report.get("recommendations", [])
    limitations = report.get("data_limitations", [])
    charts = report.get("visualizations", [])
    sections = report.get("sections", [])
    consolidated_table = report.get("consolidated_table")

    if isinstance(executive_summary, str):
        executive_summary = [
            line.strip(" •-*")
            for line in executive_summary.splitlines()
            if line.strip()
        ]

    if isinstance(findings, str):
        findings = [findings]

    if isinstance(recommendations, str):
        recommendations = [recommendations]

    if isinstance(limitations, str):
        limitations = [limitations]

    chart_html = "".join(
        render_chart(chart)
        for chart in charts
        if isinstance(chart, dict)
    )

    table_html = render_table(consolidated_table)

    section_html = []

    for section in sections:
        if not isinstance(section, dict):
            continue

        section_html.append(
            f"""
            <section class="report-section">
                <h2>{esc(section.get("heading", "Report Section"))}</h2>
                <p>{esc(section.get("content", ""))}</p>
                {render_table(section.get("table"))}
            </section>
            """
        )

    generated_at = datetime.now().strftime("%d %B %Y, %H:%M")

    return f"""
    <div class="professional-report">

        <div class="report-header">
            <div class="report-eyebrow">SAP INVENTORY INTELLIGENCE</div>
            <h1>{esc(title)}</h1>
            <p>Consolidated Inventory Analysis and Risk Assessment</p>
            <div class="report-meta">
                <span>Generated: {generated_at}</span>
            </div>
        </div>

        <section class="report-section query-section">
            <h2>Report Scope</h2>
            <p>{esc(query)}</p>
        </section>

        <section class="report-section executive-section">
            <h2>1. Executive Summary</h2>
            {render_bullet_list(executive_summary)}
        </section>

        {f'''
        <section class="report-section">
            <h2>2. Overall Assessment</h2>
            <p>{esc(summary)}</p>
        </section>
        ''' if summary else ""}

        {f'''
        <section class="report-section">
            <h2>3. Inventory Dashboard and Visual Analysis</h2>
            <div class="chart-grid">{chart_html}</div>
        </section>
        ''' if chart_html else ""}

        {f'''
        <section class="report-section">
            <h2>4. Consolidated Inventory Table</h2>
            {table_html}
        </section>
        ''' if table_html else ""}

        {''.join(section_html)}

        <section class="report-section">
            <h2>Key Findings</h2>
            {render_bullet_list(findings)}
        </section>

        <section class="report-section">
            <h2>Recommendations and Actions</h2>
            {render_bullet_list(recommendations)}
        </section>

        {f'''
        <section class="report-section limitations">
            <h2>Data Limitations and Assumptions</h2>
            {render_bullet_list(limitations)}
        </section>
        ''' if limitations else ""}

        <div class="report-footer">
            SAP Inventory Reporting Assistant | Generated report
        </div>

    </div>

    <style>
        .professional-report {{
            font-family: Arial, sans-serif;
            color: #263445;
            background: #ffffff;
            padding: 26px;
            line-height: 1.55;
            max-width: 1200px;
            margin: auto;
        }}

        .report-header {{
            background: #17365D;
            color: white;
            padding: 30px;
            border-radius: 8px;
            margin-bottom: 25px;
        }}

        .report-header h1 {{
            color: white;
            font-size: 27px;
            margin: 8px 0;
        }}

        .report-header p {{
            color: #e5edf5;
            margin: 5px 0;
        }}

        .report-eyebrow {{
            font-size: 12px;
            letter-spacing: 2px;
            font-weight: bold;
            color: #b9d5ef;
        }}

        .report-meta {{
            border-top: 1px solid #6f88a5;
            margin-top: 18px;
            padding-top: 12px;
            font-size: 12px;
        }}

        .report-section {{
            margin: 24px 0;
            padding: 20px;
            border: 1px solid #e1e7ee;
            border-radius: 8px;
            background: #fff;
        }}

        .report-section h2 {{
            color: #17365D;
            font-size: 20px;
            border-bottom: 2px solid #dbe5ef;
            padding-bottom: 9px;
            margin-top: 0;
        }}

        .report-section h3 {{
            color: #24476a;
            font-size: 16px;
        }}

        .report-bullets {{
            padding-left: 24px;
            margin-bottom: 8px;
        }}

        .report-bullets li {{
            margin: 10px 0;
            padding-left: 4px;
        }}

        .chart-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
            gap: 18px;
        }}

        .chart-card {{
            border: 1px solid #e1e7ee;
            border-radius: 8px;
            padding: 15px;
            min-width: 0;
        }}

        .report-chart {{
            display: block;
            width: 100%;
            height: auto;
            object-fit: contain;
        }}

        .chart-description {{
            font-size: 13px;
            color: #5d6b79;
        }}

        .table-scroll {{
            overflow-x: auto;
            width: 100%;
        }}

        .report-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            min-width: 700px;
        }}

        .report-table th {{
            background: #17365D;
            color: white;
            text-align: left;
            padding: 11px 9px;
            white-space: nowrap;
        }}

        .report-table td {{
            border-bottom: 1px solid #e1e7ee;
            padding: 9px;
            vertical-align: top;
        }}

        .report-table tr:nth-child(even) {{
            background: #f4f7fa;
        }}

        .table-count {{
            color: #64748b;
            font-size: 12px;
            text-align: right;
        }}

        .limitations {{
            background: #fffaf0;
            border-color: #ead9b5;
        }}

        .report-footer {{
            margin-top: 30px;
            padding-top: 15px;
            border-top: 1px solid #dce3eb;
            color: #718096;
            font-size: 11px;
            text-align: center;
        }}
    </style>
    """

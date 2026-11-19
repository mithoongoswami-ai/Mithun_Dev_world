
import base64
import html
import io
from datetime import datetime

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates


def esc(value):
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        value = str(value)
    return html.escape(str(value))


def chart_png_bytes(chart):
    """Return chart image bytes for HTML and PDF rendering."""

    chart_type = str(chart.get("type", "")).lower()

    fig = None

    if chart_type in ("pie", "donut"):
        labels = chart.get("labels", [])
        values = chart.get("values", [])

        if not labels or len(labels) != len(values):
            return None

        numeric_values = []
        for value in values:
            try:
                numeric_values.append(float(value))
            except (TypeError, ValueError):
                return None

        if any(value < 0 for value in numeric_values):
            return None

        if sum(numeric_values) <= 0:
            return None

        fig, ax = plt.subplots(figsize=(7, 4.5))

        wedges, _, _ = ax.pie(
            numeric_values,
            autopct="%1.1f%%",
            startangle=90,
            pctdistance=0.78,
            wedgeprops={
                "width": 0.42 if chart_type == "donut" else 1,
                "edgecolor": "white"
            }
        )

        ax.legend(
            wedges,
            [
                f"{label}: {value}"
                for label, value in zip(labels, values)
            ],
            loc="center left",
            bbox_to_anchor=(1, 0.5),
            frameon=False
        )

        ax.set_title(chart.get("title", "Distribution"))
        ax.axis("equal")

    elif chart_type == "bar":
        categories = chart.get("categories", [])
        series = chart.get("series", [])

        if not categories or not series:
            return None

        fig, ax = plt.subplots(figsize=(10, 5))
        valid_series = []

        for item in series:
            values = item.get("values", [])
            if len(values) != len(categories):
                continue

            try:
                values = [float(value) for value in values]
            except (TypeError, ValueError):
                continue

            valid_series.append((item, values))

        if not valid_series:
            plt.close(fig)
            return None

        width = 0.8 / len(valid_series)
        positions = list(range(len(categories)))

        for index, (item, values) in enumerate(valid_series):
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
        ax.set_ylabel(chart.get("y_axis_title", "Value"))
        ax.set_title(chart.get("title", "Comparison"))
        ax.grid(axis="y", linestyle="--", alpha=0.3)
        ax.legend()
        fig.tight_layout()

    elif chart_type == "line":
        categories = chart.get("categories", [])
        series = chart.get("series", [])

        if not categories or not series:
            return None

        fig, ax = plt.subplots(figsize=(10, 5))
        plotted = False

        for item in series:
            values = item.get("values", [])

            if len(values) != len(categories):
                continue

            try:
                values = [float(value) for value in values]
            except (TypeError, ValueError):
                continue

            ax.plot(
                categories,
                values,
                marker="o",
                linewidth=2,
                label=item.get("name", "Series")
            )
            plotted = True

        if not plotted:
            plt.close(fig)
            return None

        ax.set_title(chart.get("title", "Trend Analysis"))
        ax.set_ylabel(chart.get("y_axis_title", "Value"))
        ax.grid(True, linestyle="--", alpha=0.3)
        ax.legend()
        plt.xticks(rotation=35, ha="right")
        fig.tight_layout()

    elif chart_type == "gantt":
        tasks = chart.get("tasks", [])
        parsed = []

        for task in tasks:
            try:
                start = datetime.fromisoformat(task["start"])
                end = datetime.fromisoformat(task["end"])

                if end > start:
                    parsed.append(
                        (task.get("task", "Activity"), start, end)
                    )
            except (KeyError, TypeError, ValueError):
                continue

        if not parsed:
            return None

        fig, ax = plt.subplots(
            figsize=(10, max(3.5, len(parsed) * 0.45))
        )

        for index, (name, start, end) in enumerate(parsed):
            left = mdates.date2num(start)
            duration = (end - start).total_seconds() / 86400
            ax.barh(index, duration, left=left, height=0.55)

        ax.set_yticks(range(len(parsed)))
        ax.set_yticklabels([item[0] for item in parsed])
        ax.xaxis_date()
        ax.xaxis.set_major_formatter(
            mdates.DateFormatter("%d %b %Y")
        )
        ax.set_title(chart.get("title", "Activity Timeline"))
        ax.grid(axis="x", linestyle="--", alpha=0.3)
        fig.tight_layout()

    else:
        return None

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
    return buffer.getvalue()


def render_chart_html(chart):
    image_bytes = chart_png_bytes(chart)

    if not image_bytes:
        return ""

    encoded = base64.b64encode(image_bytes).decode("ascii")

    return f"""
    <div class="chart-card">
        <h3>{esc(chart.get("title", "Analysis"))}</h3>
        <img
            src="data:image/png;base64,{encoded}"
            class="report-chart"
            alt="{esc(chart.get("title", "Chart"))}"
        />
        <p>{esc(chart.get("description", ""))}</p>
    </div>
    """


def render_table_html(table):
    if not isinstance(table, dict):
        return ""

    columns = table.get("columns", [])
    rows = table.get("rows", [])

    if not columns:
        return "<p>No table columns were returned.</p>"

    header = "".join(
        f"<th>{esc(column)}</th>" for column in columns
    )

    body = []

    for row in rows:
        if isinstance(row, dict):
            values = [row.get(column, "") for column in columns]
        elif isinstance(row, (list, tuple)):
            values = list(row)
        else:
            continue

        values = values[:len(columns)]
        values += [""] * (len(columns) - len(values))

        body.append(
            "<tr>" +
            "".join(f"<td>{esc(value)}</td>" for value in values) +
            "</tr>"
        )

    return f"""
    <h3>{esc(table.get("title", "Consolidated Data"))}</h3>
    <div class="table-scroll">
        <table class="report-table">
            <thead><tr>{header}</tr></thead>
            <tbody>{''.join(body)}</tbody>
        </table>
    </div>
    <p class="table-count">{len(body)} records</p>
    """


def bullet_list(items):
    if isinstance(items, str):
        items = [items]

    if not items:
        return "<p>No items returned.</p>"

    return (
        "<ul class='report-bullets'>" +
        "".join(f"<li>{esc(item)}</li>" for item in items) +
        "</ul>"
    )


def render_report_html(report):
    if not isinstance(report, dict):
        report = {
            "retrieval_status": "failed",
            "generated_summary": str(report)
        }

    status = report.get("retrieval_status", "success")
    title = report.get("report_title", "SAP Inventory Report")

    if status == "failed":
        return f"""
        <div class="professional-report">
            <div class="report-header error-header">
                <div class="report-eyebrow">SAP INVENTORY INTELLIGENCE</div>
                <h1>Inventory Data Retrieval Failed</h1>
            </div>
            <section class="report-section">
                <h2>Unable to generate report</h2>
                <p>{esc(report.get("generated_summary", ""))}</p>
                <h3>Retrieval error</h3>
                <pre>{esc(report.get("retrieval_error", "No error details returned."))}</pre>
            </section>
        </div>
        """

    executive = report.get("executive_summary", [])
    findings = report.get("key_findings", [])
    recommendations = report.get("recommendations", [])
    limitations = report.get("data_limitations", [])
    sections = report.get("sections", [])
    charts = report.get("visualizations", [])
    table = report.get("consolidated_table")

    chart_html = "".join(
        render_chart_html(chart)
        for chart in charts
        if isinstance(chart, dict)
    )

    section_html = []

    for section in sections:
        if not isinstance(section, dict):
            continue

        section_html.append(f"""
        <section class="report-section">
            <h2>{esc(section.get("heading", "Report Section"))}</h2>
            <p>{esc(section.get("content", ""))}</p>
            {render_table_html(section.get("table"))}
        </section>
        """)

    table_html = render_table_html(table) if table else ""

    return f"""
    <div class="professional-report">
        <div class="report-header">
            <div class="report-eyebrow">SAP INVENTORY INTELLIGENCE</div>
            <h1>{esc(title)}</h1>
            <p>Consolidated Inventory Analysis and Business Report</p>
            <div class="report-meta">
                Generated: {datetime.now().strftime("%d %B %Y, %H:%M")}
            </div>
        </div>

        <section class="report-section">
            <h2>1. Report Scope</h2>
            <p>{esc(report.get("user_query", ""))}</p>
        </section>

        <section class="report-section">
            <h2>2. Executive Summary</h2>
            {bullet_list(executive)}
        </section>

        <section class="report-section">
            <h2>3. Overall Assessment</h2>
            <p>{esc(report.get("generated_summary", ""))}</p>
        </section>

        {f'''
        <section class="report-section">
            <h2>4. Inventory Dashboard and Visual Analysis</h2>
            <div class="chart-grid">{chart_html}</div>
        </section>
        ''' if chart_html else ""}

        {f'''
        <section class="report-section">
            <h2>5. Consolidated Inventory Table</h2>
            {table_html}
        </section>
        ''' if table_html else ""}

        {''.join(section_html)}

        <section class="report-section">
            <h2>Key Findings</h2>
            {bullet_list(findings)}
        </section>

        <section class="report-section">
            <h2>Recommendations and Actions</h2>
            {bullet_list(recommendations)}
        </section>

        {f'''
        <section class="report-section limitations">
            <h2>Data Limitations and Assumptions</h2>
            {bullet_list(limitations)}
        </section>
        ''' if limitations else ""}

        <div class="report-footer">
            SAP Inventory Intelligence | Generated report
        </div>
    </div>

    <style>
    .professional-report {{
        font-family: Arial, sans-serif;
        color: #263445;
        background: white;
        padding: 24px;
        line-height: 1.55;
        max-width: 1250px;
        margin: auto;
    }}
    .report-header {{
        background: #17365D;
        color: white;
        padding: 28px;
        border-radius: 8px;
        margin-bottom: 22px;
    }}
    .report-header h1 {{ color: white; font-size: 27px; }}
    .report-header p {{ color: #e5edf5; }}
    .error-header {{ background: #7f1d1d; }}
    .report-eyebrow {{
        color: #b9d5ef;
        font-size: 12px;
        letter-spacing: 2px;
        font-weight: bold;
    }}
    .report-meta {{ font-size: 12px; margin-top: 12px; }}
    .report-section {{
        margin: 22px 0;
        padding: 20px;
        border: 1px solid #e1e7ee;
        border-radius: 8px;
        background: white;
    }}
    .report-section h2 {{
        color: #17365D;
        font-size: 20px;
        border-bottom: 2px solid #dbe5ef;
        padding-bottom: 9px;
    }}
    .report-bullets {{ padding-left: 24px; }}
    .report-bullets li {{ margin: 10px 0; }}
    .chart-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
        gap: 18px;
    }}
    .chart-card {{
        border: 1px solid #e1e7ee;
        border-radius: 8px;
        padding: 14px;
        min-width: 0;
    }}
    .report-chart {{ width: 100%; height: auto; }}
    .table-scroll {{ overflow-x: auto; width: 100%; }}
    .report-table {{
        width: 100%;
        min-width: 700px;
        border-collapse: collapse;
        font-size: 13px;
    }}
    .report-table th {{
        background: #17365D;
        color: white;
        text-align: left;
        padding: 10px;
        white-space: nowrap;
    }}
    .report-table td {{
        padding: 9px;
        border-bottom: 1px solid #e1e7ee;
        vertical-align: top;
    }}
    .report-table tr:nth-child(even) {{ background: #f4f7fa; }}
    .table-count {{ text-align: right; color: #64748b; }}
    .limitations {{ background: #fffaf0; }}
    .report-footer {{
        margin-top: 28px;
        border-top: 1px solid #dce3eb;
        padding-top: 14px;
        color: #718096;
        text-align: center;
        font-size: 11px;
    }}
    </style>
    """

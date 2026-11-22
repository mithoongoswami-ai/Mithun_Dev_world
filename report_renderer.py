
import base64
import html
import io
from datetime import datetime

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator


# =========================================================
# ENTERPRISE COLOR PALETTE
# =========================================================

COLORS = {
    "navy": "#17365D",
    "navy_light": "#244F78",
    "blue": "#3973A5",
    "blue_light": "#6F9FC5",

    "critical": "#C0392B",
    "high": "#D97706",
    "normal": "#238636",

    "background": "#F4F7FB",
    "surface": "#FFFFFF",
    "border": "#D9E2EC",

    "text": "#243447",
    "muted": "#64748B",

    "grid": "#E5EAF0",
    "light_blue": "#EAF0F6",
    "light_red": "#FDECEC",
    "light_amber": "#FFF3DC",
    "light_green": "#E8F5EC",
}


# Consistent Matplotlib defaults
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.labelsize": 9,
    "axes.labelcolor": COLORS["muted"],
    "xtick.color": COLORS["muted"],
    "ytick.color": COLORS["muted"],
    "text.color": COLORS["text"],
    "figure.facecolor": COLORS["surface"],
    "axes.facecolor": COLORS["surface"],
    "savefig.facecolor": COLORS["surface"],
})


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def esc(value):
    """Safely escape values before inserting them into HTML."""
    if value is None:
        return ""

    return html.escape(str(value))


def number(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def get_chart_color(label, index=0):
    """
    Select semantic colors for risk categories.
    Otherwise use the standard enterprise chart palette.
    """

    label = str(label).lower()

    if "critical" in label:
        return COLORS["critical"]

    if "high" in label:
        return COLORS["high"]

    if "normal" in label or "healthy" in label:
        return COLORS["normal"]

    palette = [
        COLORS["blue"],
        COLORS["blue_light"],
        COLORS["navy"],
        "#75A7A0",
        "#8B9BB4",
        "#A7B8C8",
    ]

    return palette[index % len(palette)]


# =========================================================
# CHART GENERATION
# =========================================================

def create_chart_figure(chart):

    chart_type = str(chart.get("type", "")).lower()

    title = chart.get(
        "title",
        "Inventory chart"
    )

    fig, ax = plt.subplots(
        figsize=(10, 4.8),
        facecolor=COLORS["surface"]
    )

    ax.set_facecolor(COLORS["surface"])

    fig.suptitle(
        title,
        fontsize=14,
        fontweight="bold",
        color=COLORS["navy"],
        y=0.98
    )

    # -----------------------------------------------------
    # PIE / DONUT
    # -----------------------------------------------------

    if chart_type in ("pie", "donut"):

        labels = chart.get("labels", [])
        values = chart.get("values", [])

        if not labels or len(labels) != len(values):
            plt.close(fig)
            return None

        values = [number(v) for v in values]

        if sum(values) <= 0:
            plt.close(fig)
            return None

        colors = [
            get_chart_color(label, index)
            for index, label in enumerate(labels)
        ]

        wedges, texts, autotexts = ax.pie(
            values,
            labels=labels,
            colors=colors,
            autopct="%1.1f%%",
            startangle=90,
            pctdistance=0.78 if chart_type == "donut" else 0.65,
            wedgeprops={
                "edgecolor": COLORS["surface"],
                "linewidth": 2,
            },
            textprops={
                "color": COLORS["text"],
                "fontsize": 9,
            },
        )

        for autotext in autotexts:
            autotext.set_color("white")
            autotext.set_fontweight("bold")
            autotext.set_fontsize(9)

        if chart_type == "donut":

            centre = plt.Circle(
                (0, 0),
                0.52,
                fc=COLORS["surface"],
                ec=COLORS["border"],
                linewidth=0.8
            )

            ax.add_artist(centre)

            total = sum(values)

            ax.text(
                0,
                0,
                f"{total:,.0f}",
                ha="center",
                va="center",
                fontsize=19,
                fontweight="bold",
                color=COLORS["navy"]
            )

        ax.axis("equal")

    # -----------------------------------------------------
    # BAR
    # -----------------------------------------------------

    elif chart_type == "bar":

        labels = chart.get("labels", [])
        values = chart.get("values", [])

        if not labels or len(labels) != len(values):
            plt.close(fig)
            return None

        values = [number(v) for v in values]

        colors = [
            get_chart_color(label, index)
            for index, label in enumerate(labels)
        ]

        bars = ax.bar(
            labels,
            values,
            color=colors,
            width=0.62,
            edgecolor=COLORS["surface"],
            linewidth=1,
            zorder=3
        )

        ax.set_ylabel(
            chart.get("y_label", "Quantity")
        )

        ax.tick_params(
            axis="x",
            rotation=35,
            labelsize=8
        )

        for label in ax.get_xticklabels():
            label.set_ha("right")

        ax.yaxis.set_major_locator(
            MaxNLocator(integer=True)
        )

        ax.grid(
            axis="y",
            color=COLORS["grid"],
            linewidth=0.8,
            zorder=0
        )

        ax.set_axisbelow(True)

        # Values on top of bars
        for bar in bars:

            height = bar.get_height()

            ax.annotate(
                f"{height:,.0f}",
                xy=(
                    bar.get_x() + bar.get_width() / 2,
                    height
                ),
                xytext=(0, 4),
                textcoords="offset points",
                ha="center",
                fontsize=8,
                color=COLORS["text"]
            )

    # -----------------------------------------------------
    # LINE
    # -----------------------------------------------------

    elif chart_type == "line":

        series = chart.get("series", [])

        if not series:
            plt.close(fig)
            return None

        line_colors = [
            COLORS["blue"],
            COLORS["critical"],
            COLORS["normal"],
            COLORS["high"],
        ]

        for index, item in enumerate(series):

            labels = item.get("labels", [])
            values = item.get("values", [])

            if len(labels) != len(values) or not labels:
                continue

            color = line_colors[index % len(line_colors)]

            ax.plot(
                labels,
                [number(v) for v in values],
                marker="o",
                markersize=5,
                linewidth=2.3,
                color=color,
                label=item.get("name", "Stock")
            )

        if not ax.lines:
            plt.close(fig)
            return None

        ax.set_ylabel(
            chart.get("y_label", "Quantity")
        )

        ax.set_xlabel(
            chart.get("x_label", "Snapshot date")
        )

        ax.tick_params(
            axis="x",
            rotation=35
        )

        ax.legend(
            frameon=False,
            loc="best"
        )

        ax.grid(
            True,
            color=COLORS["grid"],
            linewidth=0.8
        )

        ax.set_axisbelow(True)

    # -----------------------------------------------------
    # GANTT
    # -----------------------------------------------------

    elif chart_type == "gantt":

        tasks = chart.get("tasks", [])

        valid_tasks = []

        for task in tasks:

            try:

                start = datetime.fromisoformat(
                    str(task["start"]).replace("Z", "+00:00")
                )

                end = datetime.fromisoformat(
                    str(task["end"]).replace("Z", "+00:00")
                )

                if end <= start:
                    continue

                valid_tasks.append(
                    (task, start, end)
                )

            except (ValueError, KeyError, TypeError):
                continue

        if not valid_tasks:
            plt.close(fig)
            return None

        for index, (task, start, end) in enumerate(valid_tasks):

            task_color = get_chart_color(
                task.get("risk", "planned"),
                index
            )

            ax.barh(
                index,
                (end - start).total_seconds() / 86400,
                left=mdates.date2num(start),
                height=0.55,
                color=task_color,
                edgecolor=COLORS["surface"],
                linewidth=1
            )

        ax.set_yticks(
            range(len(valid_tasks))
        )

        ax.set_yticklabels([
            str(item[0].get("task", "Task"))
            for item in valid_tasks
        ])

        ax.invert_yaxis()

        ax.xaxis_date()

        ax.xaxis.set_major_formatter(
            mdates.DateFormatter("%d-%b-%Y")
        )

        ax.set_xlabel(
            "Planned timeline"
        )

        ax.grid(
            axis="x",
            color=COLORS["grid"],
            linewidth=0.8
        )

        ax.set_axisbelow(True)

    else:

        plt.close(fig)
        return None

    # -----------------------------------------------------
    # CHART FINISHING
    # -----------------------------------------------------

    for spine in ax.spines.values():
        spine.set_visible(False)

    ax.tick_params(
        axis="both",
        length=0,
        pad=7
    )

    fig.tight_layout(
        rect=[0, 0, 1, 0.93]
    )

    return fig


# =========================================================
# CHART IMAGE EXPORT
# =========================================================

def chart_png_bytes(chart):

    fig = create_chart_figure(chart)

    if fig is None:
        return None

    buffer = io.BytesIO()

    fig.savefig(
        buffer,
        format="png",
        dpi=170,
        bbox_inches="tight",
        facecolor=COLORS["surface"],
        edgecolor="none"
    )

    plt.close(fig)

    buffer.seek(0)

    return buffer.getvalue()


def chart_to_base64(chart):

    image_bytes = chart_png_bytes(chart)

    if not image_bytes:
        return None

    return base64.b64encode(
        image_bytes
    ).decode("utf-8")


# =========================================================
# HTML TABLE RENDERING
# =========================================================

def render_table(table):

    columns = table.get("columns", [])
    rows = table.get("rows", [])

    if not columns or not rows:

        return (
            '<p class="mrp-empty">'
            'No records available for this table.'
            '</p>'
        )

    output = [
        '<div class="table-container">',
        '<table class="mrp-table">',
        "<thead><tr>"
    ]

    for column in columns:
        output.append(
            f"<th>{esc(column)}</th>"
        )

    output.append(
        "</tr></thead><tbody>"
    )

    for row in rows:

        output.append("<tr>")

        for column in columns:

            if isinstance(row, dict):

                value = row.get(column)

            else:

                try:
                    value = row[columns.index(column)]

                except (IndexError, ValueError, TypeError):
                    value = None

            # Special styling for risk column
            if str(column).lower() in ("risk", "supply_risk", "stock_risk"):

                risk_value = str(value or "").lower()

                if "critical" in risk_value:
                    css_class = "mrp-risk-critical"

                elif "high" in risk_value:
                    css_class = "mrp-risk-high"

                elif "normal" in risk_value or "low" in risk_value:
                    css_class = "mrp-risk-normal"

                else:
                    css_class = "mrp-risk-unknown"

                output.append(
                    f'<td><span class="{css_class}">'
                    f'{esc(value)}'
                    '</span></td>'
                )

            else:

                output.append(
                    f"<td>{esc(value)}</td>"
                )

        output.append("</tr>")

    output.append(
        "</tbody></table></div>"
    )

    return "".join(output)


# =========================================================
# BULLET POINT RENDERING
# =========================================================

def render_bullets(items):

    if not items:
        return (
            '<p class="mrp-empty">'
            'No additional findings.'
            '</p>'
        )

    if isinstance(items, str):
        items = [items]

    result = [
        '<ul class="mrp-bullet-list">'
    ]

    for item in items:

        result.append(
            f"<li>{esc(item)}</li>"
        )

    result.append("</ul>")

    return "".join(result)


# =========================================================
# MAIN HTML REPORT
# =========================================================

def render_report_html(report):

    title = esc(
        report.get(
            "report_title",
            "Inventory Intelligence Report"
        )
    )

    generated = esc(
        report.get(
            "generated_at",
            datetime.now().strftime("%d %B %Y, %H:%M")
        )
    )

    query = esc(
        report.get("user_query", "")
    )

    summary = report.get("summary", {})

    inventory = report.get("inventory", [])

    charts = report.get("visualizations", [])

    # -----------------------------------------------------
    # KPI DEFINITIONS
    # -----------------------------------------------------

    kpis = [
        (
            "Materials",
            summary.get("total_materials", 0),
            "total"
        ),
        (
            "Critical",
            summary.get("critical_items", 0),
            "critical"
        ),
        (
            "High Risk",
            summary.get("high_risk_items", 0),
            "high"
        ),
        (
            "Normal",
            summary.get("normal_items", 0),
            "normal"
        ),
    ]

    # -----------------------------------------------------
    # PROFESSIONAL REPORT CSS
    # -----------------------------------------------------

    parts = ["""

    <style>

    .mrp-report {

        font-family: "Segoe UI", Arial, sans-serif;

        color: #243447;

        background: #F4F7FB;

        line-height: 1.6;

        padding: 8px;

        font-size: 14px;

    }


    /* -----------------------------------------------
       HEADER
    ------------------------------------------------ */

    .mrp-header {

        background: linear-gradient(
            120deg,
            #17365D,
            #244F78
        ) !important;

        color: #FFFFFF !important;

        padding: 30px 24px;

        border-radius: 10px;

        margin-bottom: 20px;

        box-shadow: 0 3px 10px rgba(23,54,93,0.12);

    }

    .mrp-header,
    .mrp-header * {

        color: #FFFFFF !important;

    }

    .mrp-header h2 {

        color: #FFFFFF !important;

        font-size: 23px;

        font-weight: 700;

        margin: 0 0 10px 0;

        line-height: 1.3;

    }

    .mrp-header div {

        color: #FFFFFF !important;

        font-size: 12px;

        opacity: 0.92;

    }


    /* -----------------------------------------------
       KPI CARDS
    ------------------------------------------------ */

    .mrp-kpis {

        display: grid;

        grid-template-columns: repeat(
            4,
            minmax(110px, 1fr)
        );

        gap: 14px;

        margin: 20px 0 26px 0;

    }

    .mrp-kpi {

        background: #FFFFFF !important;

        border: 1px solid #D9E2EC;

        border-top: 4px solid #3973A5;

        border-radius: 9px;

        padding: 17px;

        box-shadow: 0 2px 7px rgba(23,54,93,0.05);

    }

    .mrp-kpi.critical {

        border-top-color: #C0392B;

    }

    .mrp-kpi.high {

        border-top-color: #D97706;

    }

    .mrp-kpi.normal {

        border-top-color: #238636;

    }

    .mrp-kpi .label {

        color: #64748B !important;

        font-size: 13px;

        font-weight: 600;

        margin-bottom: 7px;

    }

    .mrp-kpi .value {

        color: #17365D !important;

        font-size: 29px;

        font-weight: 700;

        line-height: 1.2;

    }

    .mrp-kpi.critical .value {

        color: #C0392B !important;

    }

    .mrp-kpi.high .value {

        color: #D97706 !important;

    }

    .mrp-kpi.normal .value {

        color: #238636 !important;

    }


    /* -----------------------------------------------
       REPORT SECTIONS
    ------------------------------------------------ */

    .mrp-section {

        margin-top: 25px;

    }

    .mrp-section h3 {

        color: #17365D !important;

        font-size: 18px;

        font-weight: 700;

        border-bottom: 2px solid #D9E2EC;

        padding-bottom: 9px;

        margin-bottom: 14px;

    }

    .mrp-section h4 {

        color: #17365D !important;

        font-size: 14px;

        font-weight: 700;

        margin: 0 0 12px 0;

    }


    /* -----------------------------------------------
       CONTENT CARDS
    ------------------------------------------------ */

    .mrp-card {

        background: #FFFFFF !important;

        color: #243447 !important;

        border: 1px solid #D9E2EC;

        border-radius: 9px;

        padding: 18px;

        margin: 12px 0;

        box-shadow: 0 2px 6px rgba(23,54,93,0.035);

    }

    .mrp-card p,
    .mrp-card li {

        color: #243447 !important;

    }


    /* -----------------------------------------------
       EXECUTIVE SUMMARY
    ------------------------------------------------ */

    .mrp-bullet-list {

        padding-left: 22px;

        margin: 4px 0;

    }

    .mrp-bullet-list li {

        padding: 5px 0;

        color: #334155;

    }

    .mrp-bullet-list li::marker {

        color: #3973A5;

    }

    .mrp-empty {

        color: #64748B !important;

        font-style: italic;

    }


    /* -----------------------------------------------
       CHARTS
    ------------------------------------------------ */

    .mrp-chart {

        text-align: center;

        padding: 20px;

    }

    .mrp-chart img {

        width: 100%;

        max-width: 900px;

        height: auto;

        display: block;

        margin: 10px auto 0 auto;

        border-radius: 5px;

    }


    /* -----------------------------------------------
       TABLES
    ------------------------------------------------ */

    .table-container {

        overflow-x: auto;

        border: 1px solid #D9E2EC;

        border-radius: 8px;

        background: #FFFFFF;

        margin: 12px 0;

    }

    .mrp-table {

        border-collapse: collapse;

        width: 100%;

        font-size: 12px;

        background: #FFFFFF;

    }

    .mrp-table th {

        background: #17365D !important;

        color: #FFFFFF !important;

        padding: 12px 11px;

        text-align: left;

        font-weight: 700;

        white-space: nowrap;

        border-bottom: 2px solid #244F78;

    }

    .mrp-table td {

        color: #243447 !important;

        padding: 10px 11px;

        border-bottom: 1px solid #E5EAF0;

        white-space: nowrap;

    }

    .mrp-table tbody tr:nth-child(even) {

        background: #F6F9FC !important;

    }

    .mrp-table tbody tr:hover {

        background: #EAF2F9 !important;

    }


    /* -----------------------------------------------
       RISK LABELS
    ------------------------------------------------ */

    .mrp-risk-critical {

        color: #B42318 !important;

        background: #FDECEC;

        padding: 4px 9px;

        border-radius: 5px;

        font-weight: 700;

    }

    .mrp-risk-high {

        color: #A65B00 !important;

        background: #FFF3DC;

        padding: 4px 9px;

        border-radius: 5px;

        font-weight: 700;

    }

    .mrp-risk-normal {

        color: #238636 !important;

        background: #E8F5EC;

        padding: 4px 9px;

        border-radius: 5px;

        font-weight: 700;

    }

    .mrp-risk-unknown {

        color: #64748B !important;

        background: #EDF1F5;

        padding: 4px 9px;

        border-radius: 5px;

        font-weight: 600;

    }


    /* -----------------------------------------------
       RESPONSIVE LAYOUT
    ------------------------------------------------ */

    @media (max-width: 800px) {

        .mrp-kpis {

            grid-template-columns: repeat(2, 1fr);

        }

    }

    @media (max-width: 500px) {

        .mrp-kpis {

            grid-template-columns: 1fr;

        }

        .mrp-header {

            padding: 20px;

        }

        .mrp-header h2 {

            font-size: 19px;

        }

    }

    </style>

    <div class="mrp-report">

    """]

    # -----------------------------------------------------
    # REPORT HEADER
    # -----------------------------------------------------

    parts.append(f"""

        <div class="mrp-header">

            <h2>{title}</h2>

            <div>
                Generated: {generated}
            </div>

            <div style="margin-top:10px;font-size:13px">

                Request: {query}

            </div>

        </div>

    """)

    # -----------------------------------------------------
    # KPI CARDS
    # -----------------------------------------------------

    parts.append(
        '<div class="mrp-kpis">'
    )

    for label, value, css_class in kpis:

        parts.append(f"""

            <div class="mrp-kpi {css_class}">

                <div class="label">
                    {esc(label)}
                </div>

                <div class="value">
                    {esc(value)}
                </div>

            </div>

        """)

    parts.append(
        "</div>"
    )

    # -----------------------------------------------------
    # EXECUTIVE SUMMARY
    # -----------------------------------------------------

    parts.append("""

        <div class="mrp-section">

            <h3>Executive Summary</h3>

            <div class="mrp-card">

    """)

    parts.append(
        render_bullets(
            report.get("executive_summary", [])
        )
    )

    parts.append("""

            </div>

        </div>

    """)

    # -----------------------------------------------------
    # GENERATED SUMMARY
    # -----------------------------------------------------

    generated_summary = report.get(
        "generated_summary"
    )

    if generated_summary:

        parts.append(f"""

            <div class="mrp-section">

                <h3>Overall Assessment</h3>

                <div class="mrp-card">

                    {esc(generated_summary)}

                </div>

            </div>

        """)

    # -----------------------------------------------------
    # VISUAL ANALYTICS
    # -----------------------------------------------------

    if charts:

        parts.append("""

            <div class="mrp-section">

                <h3>Inventory Visual Analytics</h3>

        """)

        for chart in charts:

            encoded = chart_to_base64(chart)

            if not encoded:
                continue

            chart_title = esc(
                chart.get("title", "Chart")
            )

            parts.append(f"""

                <div class="mrp-card mrp-chart">

                    <h4>{chart_title}</h4>

                    <img
                        src="data:image/png;base64,{encoded}"
                        alt="{chart_title}"
                    >

                </div>

            """)

        parts.append(
            "</div>"
        )

    # -----------------------------------------------------
    # INVENTORY TABLE
    # -----------------------------------------------------

    if inventory:

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

        table_rows = []

        for row in inventory:

            table_rows.append({

                key: row.get(key)

                for key in columns

            })

        parts.append("""

            <div class="mrp-section">

                <h3>Consolidated Inventory Position</h3>

        """)

        parts.append(
            render_table({
                "columns": columns,
                "rows": table_rows
            })
        )

        parts.append(
            "</div>"
        )

    # -----------------------------------------------------
    # VENDOR RESPONSE & SUPPLY RISK (only when vendor check was run)
    # -----------------------------------------------------

    followups = report.get("vendor_followups")
    vendor_summary = report.get("vendor_summary") or {}

    if followups:

        vendor_columns = [
            "material_number", "plant", "stock_risk", "vendor",
            "request_sent", "reply_status", "hours_waiting",
            "reply_outcome", "supply_risk", "assessment",
        ]

        parts.append(
            '<div class="mrp-section">'
            "<h3>Vendor Response &amp; Supply Risk</h3>"
            '<p class="mrp-empty">'
            f"{esc(vendor_summary.get('checked', 0))} materials checked "
            "against the MRP controller mailbox: "
            f"{esc(vendor_summary.get('critical', 0))} Critical, "
            f"{esc(vendor_summary.get('high', 0))} High, "
            f"{esc(vendor_summary.get('medium', 0))} Medium, "
            f"{esc(vendor_summary.get('low', 0))} Low supply risk."
            "</p>"
        )

        parts.append(
            render_table({
                "columns": vendor_columns,
                "rows": [
                    {k: row.get(k) for k in vendor_columns}
                    for row in followups
                ],
            })
        )

        parts.append("</div>")

    # -----------------------------------------------------
    # MRP RECOMMENDATIONS
    # -----------------------------------------------------

    parts.append("""

        <div class="mrp-section">

            <h3>MRP Recommendations</h3>

            <div class="mrp-card">

    """)

    parts.append(
        render_bullets(
            report.get("recommendations", [])
        )
    )

    parts.append("""

            </div>

        </div>

    """)

    # -----------------------------------------------------
    # DATA LIMITATIONS
    # -----------------------------------------------------

    limitations = report.get(
        "data_limitations",
        []
    )

    if limitations:

        parts.append("""

            <div class="mrp-section">

                <h3>Data Limitations</h3>

                <div class="mrp-card">

        """)

        parts.append(
            render_bullets(limitations)
        )

        parts.append("""

                </div>

            </div>

        """)

    # -----------------------------------------------------
    # CLOSE HTML
    # -----------------------------------------------------

    parts.append(
        "</div>"
    )

    return "".join(parts)

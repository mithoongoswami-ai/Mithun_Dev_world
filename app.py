
import os
import re
import json
import asyncio
import html
from datetime import datetime

import gradio as gr
from dotenv import load_dotenv

from google.adk.apps import App
from google.adk.runners import InMemoryRunner
from google.genai import types

from agent_package.agent import root_agent

from report_renderer import render_report_html
from report_generator import generate_pdf_report

from gmail_service import send_report_email


load_dotenv()

APP_NAME = "sap_inventory_intelligence"
USER_ID = "mrp_controller"


# =========================================================
# ADK RUNNER
# =========================================================

adk_app = App(
    name=APP_NAME,
    root_agent=root_agent
)

runner = InMemoryRunner(app=adk_app)


# =========================================================
# GENERAL HELPERS
# =========================================================

def safe_number(value):
    if value is None or value == "":
        return None

    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def display_number(value):
    if value is None:
        return "N/A"

    if float(value).is_integer():
        return str(int(value))

    return f"{value:,.2f}"


def extract_json(text):
    """
    Extract a JSON object from the agent response.
    """

    text = text.strip()

    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(r"\s*```$", "", text)

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise ValueError(
            "The agent did not return a valid JSON object."
        )

    return json.loads(text[start:end + 1])


# =========================================================
# RUN ADK AGENT
# =========================================================

async def run_agent_async(question):
    session = await runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID
    )

    message = types.Content(
        role="user",
        parts=[types.Part(text=question)]
    )

    final_text = []

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=message
    ):

        if getattr(event, "error_code", None):
            raise RuntimeError(
                getattr(event, "error_message", "ADK agent execution failed")
            )

        if event.is_final_response():
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, "text", None):
                        final_text.append(part.text)

    if not final_text:
        raise RuntimeError(
            "The ADK agent did not return a final response."
        )

    return "\n".join(final_text)


def run_inventory_agent(question):
    return asyncio.run(run_agent_async(question))


# =========================================================
# NORMALIZE INVENTORY
# =========================================================

def normalize_inventory(report):
    inventory = report.get("inventory", [])

    if not isinstance(inventory, list):
        raise ValueError("The inventory field must be a list.")

    normalized = []

    for row in inventory:
        if not isinstance(row, dict):
            continue

        item = {
            "material_number": row.get("material_number"),
            "plant": row.get("plant"),
            "storage_location": row.get("storage_location"),
            "base_uom": row.get("base_uom"),

            "unrestricted_stock": safe_number(
                row.get("unrestricted_stock")
            ),

            "safety_stock": safe_number(
                row.get("safety_stock")
            ),

            "stock_value_eur": safe_number(
                row.get("stock_value_eur")
            ),

            "blocked_stock": safe_number(
                row.get("blocked_stock")
            ),

            "in_transit_stock": safe_number(
                row.get("in_transit_stock")
            ),

            "quality_inspection_stock": safe_number(
                row.get("quality_inspection_stock")
            ),

            "snapshot_date": row.get("snapshot_date"),
            "last_count_date": row.get("last_count_date"),
            "last_goods_movement_date": row.get(
                "last_goods_movement_date"
            ),
        }

        stock = item["unrestricted_stock"]
        safety = item["safety_stock"]

        if stock is None or safety is None:
            item["risk"] = "Unknown"
        elif stock == 0:
            item["risk"] = "Critical"
        elif stock < safety:
            item["risk"] = "High"
        else:
            item["risk"] = "Normal"

        normalized.append(item)

    return normalized


# =========================================================
# CALCULATE REPORT KPIs
# =========================================================

def calculate_summary(inventory):
    # A material is counted once per plant and UOM.
    # Storage-location rows for the same material are not
    # treated as separate materials in the KPI count.

    material_keys = set()

    for row in inventory:
        material_keys.add((
            row.get("material_number"),
            row.get("plant"),
            row.get("base_uom"),
            row.get("snapshot_date"),
        ))

    critical = sum(
        1 for row in inventory
        if row["risk"] == "Critical"
    )

    high = sum(
        1 for row in inventory
        if row["risk"] == "High"
    )

    normal = sum(
        1 for row in inventory
        if row["risk"] == "Normal"
    )

    stockout = sum(
        1 for row in inventory
        if row["unrestricted_stock"] == 0
    )

    return {
        "total_materials": len(material_keys),
        "critical_items": critical,
        "high_risk_items": high,
        "normal_items": normal,
        "stockout_items": stockout,
    }


# =========================================================
# BUILD VISUALIZATIONS
# =========================================================

def wants_chart(question, chart_type):
    question = question.lower()

    keywords = {
        "pie": ["pie", "donut", "doughnut"],
        "bar": ["bar", "column", "compare", "comparison"],
        "line": ["line", "trend", "history", "historical"],
        "gantt": ["gantt", "timeline", "procurement schedule"],
    }

    return any(
        word in question
        for word in keywords.get(chart_type, [])
    )


def build_visualizations(inventory, question):
    charts = []

    if not inventory:
        return charts

    # -----------------------------------------------------
    # PIE: RISK DISTRIBUTION
    # -----------------------------------------------------

    risk_counts = {
        "Critical": 0,
        "High": 0,
        "Normal": 0,
        "Unknown": 0,
    }

    for row in inventory:
        risk_counts[row["risk"]] += 1

    risk_labels = [
        key for key, value in risk_counts.items()
        if value > 0
    ]

    risk_values = [
        risk_counts[key] for key in risk_labels
    ]

    if risk_values and (
        wants_chart(question, "pie")
        or wants_chart(question, "gantt") is False
    ):
        charts.append({
            "type": "donut",
            "title": "Inventory Risk Distribution",
            "labels": risk_labels,
            "values": risk_values,
        })

    # -----------------------------------------------------
    # BAR: STOCK BY PLANT AND UOM
    # -----------------------------------------------------

    # Never combine different units of measure.
    uoms = sorted(set(
        str(row.get("base_uom"))
        for row in inventory
        if row.get("base_uom")
    ))

    plants = sorted(set(
        str(row.get("plant"))
        for row in inventory
        if row.get("plant")
    ))

    if plants:
        for uom in uoms:
            plant_totals = {}

            for row in inventory:
                if str(row.get("base_uom")) != uom:
                    continue

                stock = row.get("unrestricted_stock")

                if stock is None:
                    continue

                plant = str(row.get("plant") or "Unknown")

                plant_totals[plant] = (
                    plant_totals.get(plant, 0) + stock
                )

            if plant_totals:
                charts.append({
                    "type": "bar",
                    "title": (
                        f"Unrestricted Stock by Plant ({uom})"
                    ),
                    "labels": list(plant_totals.keys()),
                    "values": list(plant_totals.values()),
                    "y_label": f"Quantity ({uom})",
                })

    # -----------------------------------------------------
    # BAR: TOP MATERIALS BY STOCK
    # -----------------------------------------------------

    # Use the most common UOM for a comparable material chart.
    if uoms:
        main_uom = max(
            uoms,
            key=lambda u: sum(
                1 for row in inventory
                if str(row.get("base_uom")) == u
            )
        )

        material_totals = {}

        for row in inventory:
            if str(row.get("base_uom")) != main_uom:
                continue

            stock = row.get("unrestricted_stock")

            if stock is None:
                continue

            material = str(row.get("material_number") or "Unknown")

            material_totals[material] = (
                material_totals.get(material, 0) + stock
            )

        top_materials = sorted(
            material_totals.items(),
            key=lambda x: x[1],
            reverse=True
        )[:15]

        if top_materials:
            charts.append({
                "type": "bar",
                "title": (
                    f"Top Materials by Unrestricted Stock ({main_uom})"
                ),
                "labels": [item[0] for item in top_materials],
                "values": [item[1] for item in top_materials],
                "y_label": f"Quantity ({main_uom})",
            })

    # -----------------------------------------------------
    # LINE: HISTORICAL INVENTORY TREND
    # -----------------------------------------------------

    snapshots = {}

    for row in inventory:
        date = row.get("snapshot_date")
        uom = row.get("base_uom")
        stock = row.get("unrestricted_stock")

        if not date or not uom or stock is None:
            continue

        try:
            date_key = str(date)[:10]
        except Exception:
            continue

        snapshots.setdefault(uom, {})
        snapshots[uom][date_key] = (
            snapshots[uom].get(date_key, 0) + stock
        )

    for uom, values in snapshots.items():
        dates = sorted(values.keys())

        if len(dates) < 2:
            continue

        charts.append({
            "type": "line",
            "title": f"Inventory Trend ({uom})",
            "x_label": "Snapshot date",
            "y_label": f"Unrestricted stock ({uom})",
            "series": [{
                "name": f"Unrestricted stock ({uom})",
                "labels": dates,
                "values": [values[d] for d in dates],
            }]
        })

    # -----------------------------------------------------
    # GANTT: ONLY WHEN REAL PLANNED DATES EXIST
    # -----------------------------------------------------

    gantt_tasks = []

    for row in inventory:
        start = row.get("planned_start_date")
        end = row.get("planned_end_date")

        if start and end:
            gantt_tasks.append({
                "task": str(row.get("material_number", "Material")),
                "start": start,
                "end": end,
            })

    if gantt_tasks:
        charts.append({
            "type": "gantt",
            "title": "Material Procurement Timeline",
            "tasks": gantt_tasks,
        })

    return charts


# =========================================================
# BUILD MRP RECOMMENDATIONS
# =========================================================

def build_recommendations(inventory):
    recommendations = []

    critical = [
        row for row in inventory
        if row["risk"] == "Critical"
    ]

    high = [
        row for row in inventory
        if row["risk"] == "High"
    ]

    if critical:
        recommendations.append(
            f"{len(critical)} inventory record(s) have zero "
            "unrestricted stock. Review open requirements and "
            "urgent replenishment or transfer options."
        )

    if high:
        recommendations.append(
            f"{len(high)} inventory record(s) are below safety "
            "stock. Review planned orders, open purchase orders "
            "and upcoming demand."
        )

    if not critical and not high:
        recommendations.append(
            "No Critical or High risk records were identified "
            "using the available unrestricted-stock and "
            "safety-stock values."
        )

    missing = sum(
        1 for row in inventory
        if row["unrestricted_stock"] is None
        or row["safety_stock"] is None
    )

    if missing:
        recommendations.append(
            f"{missing} record(s) have missing stock or safety "
            "stock values and could not be classified."
        )

    return recommendations


# =========================================================
# GENERATE REPORT
# =========================================================

def generate_report(question):
    if not question or not question.strip():
        return (
            "<p>Please enter an inventory question.</p>",
            None,
            None,
            None,
            "Please enter a question."
        )

    try:
        raw_response = run_inventory_agent(question)

        report = extract_json(raw_response)

        if report.get("retrieval_status") == "failed":
            raise RuntimeError(
                "Data retrieval failed: " +
                "; ".join(report.get("data_limitations", []))
            )

        inventory = normalize_inventory(report)

        if not inventory:
            raise RuntimeError(
                "The Data Agent returned no inventory records. "
                "Please check the requested material, plant or "
                "inventory scope and try again."
            )

        report["inventory"] = inventory
        report["summary"] = calculate_summary(inventory)

        report["visualizations"] = build_visualizations(
            inventory,
            question
        )

        report["recommendations"] = (
            report.get("recommendations", [])
            + build_recommendations(inventory)
        )

        report["user_query"] = question
        report["generated_at"] = datetime.now().strftime(
            "%d %B %Y, %H:%M"
        )

        if not report.get("report_title"):
            report["report_title"] = "SAP Inventory Intelligence Report"

        if not report.get("executive_summary"):
            report["executive_summary"] = [
                (
                    f"{report['summary']['total_materials']} "
                    "material/plant/UOM/snapshot combinations "
                    "were returned."
                ),
                (
                    f"{report['summary']['critical_items']} "
                    "records are Critical."
                ),
                (
                    f"{report['summary']['high_risk_items']} "
                    "records are High risk."
                ),
            ]

        report_html = render_report_html(report)

        pdf_path = generate_pdf_report(report)

        status = (
            f"Report generated successfully. "
            f"{len(inventory)} inventory records processed."
        )

        return (
            report_html,
            pdf_path,
            pdf_path,
            report,
            status
        )

    except Exception as exc:
        error_html = f"""
        <div style="padding:18px;border:1px solid #e5a5a5;
                    border-radius:8px;color:#9b1c1c">
            <h3>Report generation failed</h3>
            <p>{html.escape(str(exc))}</p>
        </div>
        """

        return (
            error_html,
            None,
            None,
            None,
            f"Error: {exc}"
        )


# =========================================================
# GMAIL INTEGRATION
# =========================================================

def send_email(recipient, subject, body, pdf_path):
    if not recipient or not recipient.strip():
        return "Please enter a recipient email address."

    if not pdf_path or not os.path.exists(pdf_path):
        return "Please generate a report before sending the email."

    if not subject or not subject.strip():
        subject = "SAP Inventory Intelligence Report"

    try:
        result = send_report_email(
            recipient=recipient.strip(),
            subject=subject,
            body=body or "Please find the SAP inventory report attached.",
            attachment_path=pdf_path
        )

        return str(result)

    except Exception as exc:
        return f"Email sending failed: {exc}"


# =========================================================
# CLEAR
# =========================================================

def clear_report():
    return (
        "",
        None,
        None,
        None,
        "<p>Your generated report will appear here.</p>",
        "Ready to generate a report."
    )


# =========================================================
# GRADIO UI
# =========================================================

with gr.Blocks(
    title="SAP Inventory Intelligence",
    theme=gr.themes.Soft()
) as demo:

    gr.Markdown("""
    # SAP Inventory Intelligence
    ### AI-powered inventory analysis for MRP controllers

    Ask questions about material availability, stock risk,
    plant inventory, material shortages and replenishment.
    """)

    report_state = gr.State(None)
    pdf_state = gr.State(None)

    with gr.Row():
        question_input = gr.Textbox(
            label="Ask your inventory question",
            placeholder=(
                "Example: Analyse material stock risk across "
                "all plants and show a donut chart and a bar chart."
            ),
            lines=3,
            scale=5
        )

    with gr.Row():
        generate_button = gr.Button(
            "Generate Inventory Report",
            variant="primary"
        )

        clear_button = gr.Button("Clear")

    report_status = gr.Markdown("Ready to generate a report.")

    gr.Markdown("---")
    gr.Markdown("## Inventory Intelligence Dashboard")

    report_output = gr.HTML(
        value="<p>Your generated report will appear here.</p>"
    )

    with gr.Row():
        pdf_output = gr.File(
            label="Download PDF Report"
        )

    gr.Markdown("---")
    gr.Markdown("## Email Report Using Gmail")

    with gr.Row():
        recipient_input = gr.Textbox(
            label="Recipient email",
            placeholder="recipient@example.com"
        )

        subject_input = gr.Textbox(
            label="Email subject",
            value="SAP Inventory Intelligence Report"
        )

    email_body_input = gr.Textbox(
        label="Email message",
        value=(
            "Hello,\n\n"
            "Please find attached the SAP Inventory Intelligence "
            "report generated from the latest inventory data.\n\n"
            "Regards"
        ),
        lines=4
    )

    send_email_button = gr.Button(
        "Send Report via Gmail",
        variant="primary"
    )

    email_status = gr.Markdown()

    # -----------------------------------------------------
    # GENERATE REPORT
    # -----------------------------------------------------

    generate_button.click(
        fn=generate_report,
        inputs=[question_input],
        outputs=[
            report_output,
            pdf_output,
            pdf_state,
            report_state,
            report_status
        ]
    )

    # -----------------------------------------------------
    # SEND EMAIL
    # -----------------------------------------------------

    send_email_button.click(
        fn=send_email,
        inputs=[
            recipient_input,
            subject_input,
            email_body_input,
            pdf_state
        ],
        outputs=[email_status]
    )

    # -----------------------------------------------------
    # CLEAR
    # -----------------------------------------------------

    clear_button.click(
        fn=clear_report,
        inputs=[],
        outputs=[
            question_input,
            pdf_output,
            pdf_state,
            report_state,
            report_output,
            report_status
        ]
    )


if __name__ == "__main__":
    demo.queue().launch()

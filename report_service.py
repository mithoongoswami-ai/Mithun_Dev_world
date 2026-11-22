"""
report_service.py
=================
UI-independent report pipeline.

Everything that used to live inside app.py (ADK runner, JSON parsing,
risk calculation, chart specs, recommendations) now lives here so that
BOTH the Gradio UI and the background scheduler can call it.

New in this file: run_report_job() - one function that runs the agent,
builds the report, writes it to the persistent reports directory and
records the result in the database.
"""

import os
import re
import sys
import time
import logging
import traceback
import csv
import html as html_lib
import json
import asyncio
import concurrent.futures
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from google.adk.apps import App
from google.adk.runners import InMemoryRunner
from google.genai import types

from agent_package.agent import root_agent

from report_renderer import render_report_html
from report_generator import generate_pdf_report

import report_store as store
import vendor_followup

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
agent_log = logging.getLogger("agent")

APP_NAME = "sap_inventory_intelligence"
USER_ID = "mrp_controller"

REPORT_FORMATS = ["PDF", "HTML", "CSV"]

# Matplotlib (pyplot) is not thread-safe. The UI thread and the scheduler
# thread must never render charts at the same moment.
RENDER_LOCK = threading.RLock()

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

def _log_event(event, started):
    """One console line per ADK event, so a stall is easy to locate."""
    names = []

    for part in (getattr(event.content, "parts", None) or []):
        call = getattr(part, "function_call", None)
        resp = getattr(part, "function_response", None)

        if call:
            names.append(f"CALL {call.name}")
        if resp:
            names.append(f"RESULT {resp.name}")

    agent_log.info(
        "  +%.1fs event from %s %s",
        time.monotonic() - started,
        getattr(event, "author", "?"),
        ", ".join(names),
    )


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
    started = time.monotonic()
    agent_log.info("Agent run started: %s", question[:120])

    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=message
    ):

        _log_event(event, started)

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


# ---------------------------------------------------------
# ONE shared, long-lived event loop for every agent call.
#
# asyncio.run() creates and CLOSES a loop per call, but the ADK /
# Data Agent clients (httpx connection pools) are created once and
# stay bound to the first loop. Re-using them from a new loop gives
# "RuntimeError: Event loop is closed" and hangs. UI threads and
# scheduler threads therefore submit their work to this one loop.
# ---------------------------------------------------------

AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", "900"))

_agent_loop = None
_agent_thread = None
_agent_loop_lock = threading.Lock()


def _get_agent_loop():
    global _agent_loop, _agent_thread

    with _agent_loop_lock:
        if _agent_loop is None or _agent_loop.is_closed():
            _agent_loop = asyncio.new_event_loop()

            _agent_thread = threading.Thread(
                target=_agent_loop.run_forever,
                name="adk-event-loop",
                daemon=True,
            )
            _agent_thread.start()

        return _agent_loop


def _abandon_agent_loop():
    """
    The loop thread did not answer in time. Log where it is stuck and
    drop the loop so that the NEXT request gets a fresh one instead of
    queueing behind a stuck call forever.
    """
    global _agent_loop, _agent_thread

    with _agent_loop_lock:
        thread, loop = _agent_thread, _agent_loop
        _agent_loop = None
        _agent_thread = None

    if thread is not None and thread.ident in sys._current_frames():
        stack = "".join(traceback.format_stack(
            sys._current_frames()[thread.ident]
        ))
        agent_log.error("Agent loop thread is stuck here:\n%s", stack)

    if loop is not None:
        loop.call_soon_threadsafe(loop.stop)


def run_inventory_agent(question):
    future = asyncio.run_coroutine_threadsafe(
        run_agent_async(question),
        _get_agent_loop(),
    )

    try:
        return future.result(timeout=AGENT_TIMEOUT_SECONDS)

    except concurrent.futures.TimeoutError:
        future.cancel()
        _abandon_agent_loop()

        raise RuntimeError(
            f"The ADK agent did not finish within "
            f"{AGENT_TIMEOUT_SECONDS} seconds. See the console log "
            "for the last agent step."
        )


# =========================================================
# VENDOR-FOLLOW-UP REASONING AGENT (separate agent, no tools)
# Only used when a report is requested with the vendor e-mail check.
# =========================================================

VENDOR_APP_NAME = "sap_vendor_followup"

vendor_runner = InMemoryRunner(
    app=App(
        name=VENDOR_APP_NAME,
        root_agent=vendor_followup.reasoning_agent,
    )
)


async def _run_reasoning_async(prompt):
    session = await vendor_runner.session_service.create_session(
        app_name=VENDOR_APP_NAME,
        user_id=USER_ID
    )

    message = types.Content(
        role="user",
        parts=[types.Part(text=prompt)]
    )

    texts = []

    async for event in vendor_runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=message
    ):
        if getattr(event, "error_code", None):
            raise RuntimeError(
                getattr(event, "error_message", "Reasoning agent failed")
            )

        if event.is_final_response() and event.content:
            for part in event.content.parts or []:
                if getattr(part, "text", None):
                    texts.append(part.text)

    if not texts:
        raise RuntimeError("The reasoning agent returned no response.")

    return "\n".join(texts)


def run_reasoning_text(prompt):
    """Blocking helper: runs on the same shared event loop as the agent."""
    future = asyncio.run_coroutine_threadsafe(
        _run_reasoning_async(prompt),
        _get_agent_loop(),
    )

    try:
        return future.result(timeout=300)

    except concurrent.futures.TimeoutError:
        future.cancel()
        raise RuntimeError("The reasoning agent timed out.")


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
# BUILD REPORT DATA  (replaces the first half of the old
# generate_report() in app.py)
# =========================================================

def build_report_data(question):
    """Run the ADK agent and return the fully processed report dict."""

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

    with RENDER_LOCK:
        report["visualizations"] = build_visualizations(
            inventory,
            question
        )

    report["recommendations"] = (
        report.get("recommendations", [])
        + build_recommendations(inventory)
    )

    report["user_query"] = question
    report["generated_at"] = datetime.now().strftime("%d %B %Y, %H:%M")

    if not report.get("report_title"):
        report["report_title"] = "SAP Inventory Intelligence Report"

    if not report.get("executive_summary"):
        report["executive_summary"] = [
            (
                f"{report['summary']['total_materials']} "
                "material/plant/UOM/snapshot combinations "
                "were returned."
            ),
            f"{report['summary']['critical_items']} records are Critical.",
            f"{report['summary']['high_risk_items']} records are High risk.",
        ]

    return report


# =========================================================
# WRITE REPORT FILE (PDF / HTML / CSV)
# =========================================================

CSV_COLUMNS = [
    "material_number", "plant", "storage_location", "base_uom",
    "unrestricted_stock", "safety_stock", "blocked_stock",
    "in_transit_stock", "quality_inspection_stock",
    "stock_value_eur", "snapshot_date", "last_count_date",
    "last_goods_movement_date", "risk",
]


def render_html_safe(report):
    with RENDER_LOCK:
        return render_report_html(report)


def create_report_file(report, report_format, report_id):
    """
    Write the report to the persistent reports directory and
    return the file path. The report_id is part of the filename
    so every file is traceable back to its database record.
    """

    report_format = (report_format or "PDF").upper()
    store.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    if report_format == "PDF":
        with RENDER_LOCK:
            generated = Path(generate_pdf_report(report))

        target = store.REPORTS_DIR / f"{report_id}.pdf"
        generated.replace(target)
        return str(target)

    if report_format == "HTML":
        body = render_html_safe(report)
        title = html_lib.escape(str(report.get("report_title", "Inventory Report")))

        target = store.REPORTS_DIR / f"{report_id}.html"
        target.write_text(
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{title}</title></head><body>{body}</body></html>",
            encoding="utf-8",
        )
        return str(target)

    if report_format == "CSV":
        target = store.REPORTS_DIR / f"{report_id}.csv"

        with open(target, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=CSV_COLUMNS,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(report["inventory"])

        return str(target)

    raise ValueError(f"Unsupported report format: {report_format}")


# =========================================================
# ONE ENTRY POINT FOR UI AND SCHEDULER
# =========================================================

def run_report_job(
    question,
    report_format="PDF",
    name=None,
    report_type="On-demand",
    schedule_id=None,
    include_vendor_check=False,
):
    """
    1. create a 'Running' record
    2. invoke the ADK agent (BigQuery data)
    3. build the report and save the file persistently
    4. mark the record 'Success' or 'Failed'

    Returns {"report_id", "report", "file_path"}.
    Re-raises the exception after recording the failure.
    """

    report_id = store.create_report(
        name=name or "Inventory report",
        report_type=report_type,
        report_format=report_format,
        user_query=question,
        schedule_id=schedule_id,
    )

    try:
        report = build_report_data(question)

        if include_vendor_check:
            # Optional add-on. A failure here must never lose the
            # stock report, so it is recorded as a data limitation.
            try:
                vendor_followup.enrich_report(
                    report, llm=run_reasoning_text
                )
            except Exception as exc:
                agent_log.exception("Vendor e-mail check failed")
                report.setdefault("data_limitations", []).append(
                    f"Vendor e-mail check failed: {exc}"
                )

        file_path = create_report_file(
            report,
            report_format,
            report_id
        )

        store.update_report(
            report_id,
            name=name or report.get("report_title"),
            status="Success",
            file_path=file_path,
            record_count=len(report["inventory"]),
            completed_at=store.now_iso(),
        )

        return {
            "report_id": report_id,
            "report": report,
            "file_path": file_path,
        }

    except Exception as exc:
        store.update_report(
            report_id,
            status="Failed",
            error=str(exc)[:1000],
            completed_at=store.now_iso(),
        )
        raise

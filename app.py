
import os
import re
import json
import uuid
import asyncio
import logging
from pathlib import Path
from datetime import datetime

import gradio as gr
from dotenv import load_dotenv

from google.adk.apps import App
from google.adk.runners import InMemoryRunner
from google.genai import types

from agent_package.agent import root_agent
from report_generator import generate_pdf_report
from report_renderer import render_report_html
from gmail_service import send_report_email


# =========================================================
# 1. CONFIGURATION
# =========================================================

load_dotenv()

APP_NAME = "sap_inventory_reporting"
USER_ID = "sap_inventory_user"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

logger = logging.getLogger("sap_inventory_reporting")


# =========================================================
# 2. ADK INITIALIZATION
# =========================================================

logger.info("Initializing Google ADK application...")

adk_app = App(
    name=APP_NAME,
    root_agent=root_agent
)

adk_runner = InMemoryRunner(
    app=adk_app
)

logger.info("Google ADK application initialized.")


# =========================================================
# 3. ADK EXECUTION
# =========================================================

async def run_inventory_agent(question):

    session_id = str(uuid.uuid4())

    await adk_runner.session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=session_id
    )

    message = types.Content(
        role="user",
        parts=[types.Part(text=question)]
    )

    logger.info("Starting inventory report request: %s", question)

    response_parts = []
    tool_calls = []
    event_errors = []

    async for event in adk_runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=message
    ):

        # Log tool calls requested by the model.
        content = getattr(event, "content", None)

        if content and content.parts:
            for part in content.parts:

                function_call = getattr(part, "function_call", None)

                if function_call:
                    tool_calls.append(function_call.name)
                    logger.info(
                        "ADK tool call: %s",
                        function_call.name
                    )

        # Capture any event-level errors.
        error_code = getattr(event, "error_code", None)
        error_message = getattr(event, "error_message", None)

        if error_code or error_message:
            event_errors.append(
                f"{error_code}: {error_message}"
            )

        # Capture final response.
        if event.is_final_response():

            if content and content.parts:
                for part in content.parts:
                    text_value = getattr(part, "text", None)

                    if text_value:
                        response_parts.append(text_value)

    if event_errors:
        raise RuntimeError(
            "ADK execution error: " + " | ".join(event_errors)
        )

    if not response_parts:
        raise RuntimeError(
            "ADK did not return a final text response. "
            f"Tools called: {tool_calls}"
        )

    response_text = "\n".join(response_parts)

    logger.info("ADK execution completed.")
    logger.info("Tools called: %s", tool_calls)

    # Save diagnostics for the Gradio status area.
    return response_text, tool_calls


# =========================================================
# 4. PARSE AGENT RESPONSE
# =========================================================

def parse_agent_response(response_text):

    cleaned = response_text.strip()

    cleaned = re.sub(
        r"^```(?:json)?\s*",
        "",
        cleaned,
        flags=re.IGNORECASE
    )

    cleaned = re.sub(
        r"\s*```$",
        "",
        cleaned
    )

    try:
        data = json.loads(cleaned)

    except json.JSONDecodeError:

        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start < 0 or end <= start:
            raise ValueError(
                "The agent did not return valid JSON. "
                "Check the terminal for the raw ADK response."
            )

        data = json.loads(cleaned[start:end + 1])

    if not isinstance(data, dict):
        raise ValueError("Agent response must be a JSON object.")

    # Normalise expected fields.
    defaults = {
        "report_title": "SAP Inventory Report",
        "user_query": "",
        "generated_summary": "",
        "executive_summary": [],
        "consolidated_table": {
            "title": "Consolidated Inventory",
            "columns": [],
            "rows": []
        },
        "visualizations": [],
        "sections": [],
        "key_findings": [],
        "recommendations": [],
        "data_limitations": [],
        "retrieval_status": "success",
        "retrieval_error": ""
    }

    for key, default_value in defaults.items():
        data.setdefault(key, default_value)

    for field in [
        "executive_summary",
        "visualizations",
        "sections",
        "key_findings",
        "recommendations",
        "data_limitations"
    ]:
        if isinstance(data[field], str):
            data[field] = [data[field]]

        if not isinstance(data[field], list):
            data[field] = []

    if not isinstance(data["consolidated_table"], dict):
        data["consolidated_table"] = {
            "title": "Consolidated Inventory",
            "columns": [],
            "rows": []
        }

    if data["retrieval_status"] not in ("success", "failed"):
        data["retrieval_status"] = "success"

    return data


# =========================================================
# 5. REPORT GENERATION
# =========================================================

async def generate_report(question):

    if not question or not question.strip():

        return (
            "<h2>Please enter a report request.</h2>",
            None,
            None,
            "Please enter a question."
        )

    try:

        logger.info("Generating inventory report...")

        response_text, tool_calls = await run_inventory_agent(
            question.strip()
        )

        # Print raw response for troubleshooting.
        logger.info("Raw agent response:\n%s", response_text)

        report_data = parse_agent_response(response_text)

        # Never generate a successful report without data retrieval.
        if report_data.get("retrieval_status") == "failed":

            report_html = render_report_html(report_data)

            return (
                report_html,
                None,
                report_data,
                "Inventory retrieval failed. See error in report."
            )

        report_data["user_query"] = (
            report_data.get("user_query") or question.strip()
        )

        # Check whether any report content was actually returned.
        table = report_data.get("consolidated_table", {})
        rows = table.get("rows", []) if isinstance(table, dict) else []

        if (
            not rows
            and not report_data.get("generated_summary")
            and not report_data.get("sections")
            and not report_data.get("executive_summary")
        ):
            raise RuntimeError(
                "The agent returned no inventory records or report "
                "content. Check the Data Agent tool invocation, "
                "permissions and source query."
            )

        # Render HTML.
        report_html = render_report_html(report_data)

        # Generate PDF from the same structured data.
        pdf_path = generate_pdf_report(report_data)

        status = (
            "### Report generated successfully\n\n"
            f"**Records in consolidated table:** {len(rows)}\n\n"
            f"**Data Agent tools called:** "
            f"{', '.join(tool_calls) if tool_calls else 'Not detected'}\n\n"
            f"**PDF:** {Path(pdf_path).name}"
        )

        logger.info("Report generated: %s", pdf_path)

        return (
            report_html,
            pdf_path,
            report_data,
            status
        )

    except Exception as error:

        logger.exception("Report generation failed")

        error_html = f"""
        <div style="padding:22px; border:1px solid #e5b7b7;
                    border-radius:8px; color:#7f1d1d;">
            <h2>Report generation failed</h2>
            <p>{str(error)}</p>
            <p>Check the PowerShell terminal for the complete traceback.</p>
        </div>
        """

        return (
            error_html,
            None,
            None,
            f"Report generation failed: {error}"
        )


# =========================================================
# 6. GMAIL INTEGRATION
# =========================================================

def send_email(
    recipient,
    subject,
    email_body,
    pdf_path
):

    if not recipient or not recipient.strip():
        return "Please enter a recipient email address."

    if not subject or not subject.strip():
        return "Please enter an email subject."

    if not pdf_path:
        return "Please generate the report first."

    if not os.path.exists(pdf_path):
        return "The generated PDF file could not be found."

    try:

        logger.info("Sending report to %s", recipient)

        result = send_report_email(
            recipient=recipient.strip(),
            subject=subject.strip(),
            body=email_body,
            attachment_path=pdf_path
        )

        return (
            "### Email sent successfully\n\n"
            f"Recipient: {recipient}\n\n"
            f"Attachment: {Path(pdf_path).name}\n\n"
            f"{str(result) if result else ''}"
        )

    except Exception as error:

        logger.exception("Gmail sending failed")

        return f"### Gmail sending failed\n\n{error}"


# =========================================================
# 7. CLEAR FORM
# =========================================================

def clear_report():

    return (
        "",
        None,
        None,
        None,
        "Ready. Enter a question to generate a report.",
        None,
        "",
        ""
    )


# =========================================================
# 8. CUSTOM CSS
# =========================================================

CUSTOM_CSS = """
.gradio-container {
    max-width: 1450px !important;
}

#main-title {
    margin-bottom: 5px;
}

#report-workspace {
    min-height: 500px;
}

#generate-button {
    min-height: 52px;
}
"""


# =========================================================
# 9. GRADIO INTERFACE
# =========================================================

with gr.Blocks(
    title="SAP Inventory Intelligence",
    theme=gr.themes.Soft(),
    css=CUSTOM_CSS
) as demo:

    report_state = gr.State(None)
    pdf_state = gr.State(None)

    gr.Markdown(
        """
        # SAP Inventory Intelligence

        Generate professional inventory reports using
        natural-language queries, Google ADK and BigQuery.
        """,
        elem_id="main-title"
    )

    gr.Markdown(
        """
        Enter your business question. The agent retrieves
        relevant inventory data and prepares a consolidated
        report with visualizations, findings and recommendations.
        """
    )

    question_input = gr.Textbox(
        label="Report Request",
        placeholder=(
            "Example: Generate a consolidated report of all "
            "materials at risk across all plants. Include an "
            "executive summary, risk distribution donut chart, "
            "plant-wise risk bar chart and consolidated inventory table."
        ),
        lines=5
    )

    with gr.Row():

        generate_button = gr.Button(
            "Generate Report",
            variant="primary",
            elem_id="generate-button",
            scale=2
        )

        clear_button = gr.Button(
            "Clear",
            variant="secondary",
            scale=1
        )

    report_status = gr.Markdown(
        "Ready. Enter a question to generate a report."
    )

    gr.Markdown("---")
    gr.Markdown("## Generated Report")

    # HTML is required for embedded charts and formatted tables.
    report_output = gr.HTML(
        value="""
        <div style="padding:25px;">
            <h2>Your report will appear here.</h2>
            <p>Enter a report request and select Generate Report.</p>
        </div>
        """,
        elem_id="report-workspace"
    )

    gr.Markdown("---")

    with gr.Accordion(
        "Download and Email Report",
        open=False
    ):

        pdf_output = gr.File(
            label="Download Generated PDF"
        )

        gr.Markdown("### Send report through Gmail")

        recipient_input = gr.Textbox(
            label="Recipient Email Address",
            placeholder="recipient@example.com"
        )

        subject_input = gr.Textbox(
            label="Email Subject",
            value="SAP Inventory Report"
        )

        email_body_input = gr.Textbox(
            label="Email Message",
            value=(
                "Hello,\n\n"
                "Please find attached the requested "
                "SAP Inventory Report.\n\n"
                "Regards,\n"
                "SAP Inventory Reporting Assistant"
            ),
            lines=5
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
            report_state,
            report_output,
            report_status,
            pdf_state,
            recipient_input,
            email_status
        ]
    )


# =========================================================
# 10. START APPLICATION
# =========================================================

if __name__ == "__main__":

    print("\n============================================")
    print("SAP INVENTORY INTELLIGENCE")
    print("============================================")
    print("Agent:", root_agent.name)
    print("Starting Gradio application...")
    print("URL: http://127.0.0.1:7860")

    demo.queue()

    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        show_error=True
    )

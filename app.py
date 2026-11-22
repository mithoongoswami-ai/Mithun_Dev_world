import os
import html

import gradio as gr

import report_store as store
import scheduler_service as sched

from report_service import (
    run_report_job,
    render_html_safe,
    REPORT_FORMATS,
)
from gmail_service import send_report_email, parse_recipients


DEFAULT_SUBJECT = "SAP Inventory Intelligence Report"
DEFAULT_TZ = "Europe/Berlin" if "Europe/Berlin" in sched.TIMEZONES else "UTC"

store.init_db()

EMPTY_REPORT_HTML = """
<div class="empty-state">
  <div class="empty-icon">&#128202;</div>
  <div class="empty-title">No report yet</div>
  <div class="empty-text">
    Enter an inventory question on the left and click
    <b>Generate report</b>. Your dashboard, charts and
    inventory table will appear here.
  </div>
</div>
"""


# =========================================================
# TAB 1  -  ON-DEMAND REPORTS
# =========================================================

def generate_report(question, report_format, include_vendor_check=False):
    if not question or not question.strip():
        return (
            "<p>Please enter an inventory question.</p>",
            None, None, None,
            "Please enter a question."
        )

    try:
        result = run_report_job(
            question=question.strip(),
            report_format=report_format,
            report_type="On-demand",
            include_vendor_check=bool(include_vendor_check),
        )

        return (
            render_html_safe(result["report"]),
            result["file_path"],          # gr.File download
            result["file_path"],          # file_state (for e-mail)
            result["report_id"],          # id_state
            (
                f"Report **{result['report_id']}** generated. "
                f"{len(result['report']['inventory'])} inventory "
                "records processed. It is also listed under "
                "*Generated Reports*."
            ),
        )

    except Exception as exc:
        error_html = f"""
        <div style="padding:18px;border:1px solid #e5a5a5;
                    border-radius:8px;color:#9b1c1c">
            <h3>Report generation failed</h3>
            <p>{html.escape(str(exc))}</p>
        </div>
        """

        return error_html, None, None, None, f"Error: {exc}"


def send_email(recipients, subject, body, file_path, report_id):
    if not file_path or not os.path.exists(file_path):
        return "Please generate a report before sending the email."

    try:
        recipient_list = parse_recipients(recipients)

        send_report_email(
            recipient=recipient_list,
            subject=(subject or "").strip() or DEFAULT_SUBJECT,
            body=body or "Please find the SAP inventory report attached.",
            attachment_path=file_path,
            interactive=True,
        )

        if report_id:
            store.update_report(
                report_id,
                email_status=f"Sent to {len(recipient_list)} recipient(s)"
            )

        return f"Email sent to {', '.join(recipient_list)}."

    except Exception as exc:
        if report_id:
            store.update_report(
                report_id,
                email_status=f"Failed: {exc}"[:300]
            )

        return f"Email sending failed: {exc}"


def clear_report():
    return (
        "",
        None,
        None,
        None,
        EMPTY_REPORT_HTML,
        "Ready to generate a report."
    )


# =========================================================
# TAB 2  -  SCHEDULES
# =========================================================

SCHEDULE_HEADERS = [
    "Schedule ID", "Name", "Recurrence", "Times", "Timezone",
    "Recipients", "Format", "Status", "Next run", "Last run",
    "Last result",
]


def schedules_table():
    rows = []

    for s in store.list_schedules():
        active = s["status"] == "active"

        rows.append([
            s["schedule_id"],
            s["name"],
            s["recurrence"],
            sched.describe_slots(s["recurrence"], s["slots"]),
            s["timezone"],
            ", ".join(s["recipients"]),
            s["report_format"],
            "Active" if active else "Cancelled",
            sched.next_run_text(s["schedule_id"]) if active else "",
            store.fmt_ts(s["last_run_at"], s["timezone"]),
            s["last_status"] or "",
        ])

    return rows


def blank_form():
    """Values for form_outputs (see below) = a new, empty schedule."""
    return [
        "",                              # editing_id
        "",                              # name
        "",                              # question
        "",                              # recipients
        DEFAULT_SUBJECT,                 # subject
        sched.DEFAULT_EMAIL_BODY,        # e-mail body
        DEFAULT_TZ,                      # timezone
        "PDF",                           # report format
        "Everyday",                      # recurrence
        "08:00",                         # time (non-custom)
        *[False] * 7,                    # custom: day enabled
        *["08:00"] * 7,                  # custom: day time
        False,                           # include vendor e-mail check
    ]


def new_schedule():
    return [*blank_form(), "Creating a new schedule."]


def save_schedule(
    editing_id, name, question, recipients, subject, body,
    tz, report_format, recurrence, simple_time, *day_values
):
    try:
        day_enabled = day_values[:7]
        day_times = day_values[7:14]
        include_vendor = bool(day_values[14]) if len(day_values) > 14 else False

        name = (name or "").strip() or "Scheduled inventory report"

        if not question or not question.strip():
            raise ValueError("Enter the inventory question to run.")

        recipient_list = parse_recipients(recipients)

        if tz not in sched.TIMEZONES:
            raise ValueError(f"Unknown time zone: {tz}")

        slots = sched.build_slots(
            recurrence, simple_time, day_enabled, day_times
        )

        values = dict(
            name=name,
            question=question.strip(),
            recipients=recipient_list,
            subject=(subject or "").strip() or DEFAULT_SUBJECT,
            email_body=body or sched.DEFAULT_EMAIL_BODY,
            timezone=tz,
            report_format=report_format,
            recurrence=recurrence,
            slots=slots,
            include_vendor_check=include_vendor,
        )

        if editing_id:
            store.update_schedule(editing_id, status="active", **values)
            schedule_id = editing_id
            verb = "updated"

        else:
            schedule_id = store.create_schedule(
                name=values["name"],
                question=values["question"],
                recipients=values["recipients"],
                subject=values["subject"],
                email_body=values["email_body"],
                tz=values["timezone"],
                report_format=values["report_format"],
                recurrence=values["recurrence"],
                slots=values["slots"],
                include_vendor_check=values["include_vendor_check"],
            )
            verb = "created"

        sched.register_schedule(store.get_schedule(schedule_id))

        message = (
            f"Schedule **{schedule_id}** {verb}. "
            f"Next run: {sched.next_run_text(schedule_id)} ({tz})."
        )

        return schedule_id, message, schedules_table()

    except Exception as exc:
        return editing_id, f"Could not save schedule: {exc}", schedules_table()


def load_schedule(df, evt: gr.SelectData):
    """Row clicked in the schedules table -> load it into the form."""
    schedule_id = df.iloc[evt.index[0]]["Schedule ID"]
    s = store.get_schedule(schedule_id)

    if not s:
        return [*blank_form(), "Schedule not found."]

    slot_by_day = {slot["day"]: slot["time"] for slot in s["slots"]}
    first_time = s["slots"][0]["time"] if s["slots"] else "08:00"

    values = [
        s["schedule_id"],
        s["name"],
        s["question"],
        ", ".join(s["recipients"]),
        s["subject"],
        s["email_body"],
        s["timezone"],
        s["report_format"],
        s["recurrence"],
        first_time,
        *[day in slot_by_day for day in sched.DAYS],
        *[slot_by_day.get(day, "08:00") for day in sched.DAYS],
        bool(s.get("include_vendor_check")),
    ]

    note = f"Editing **{schedule_id}**."

    if s["status"] != "active":
        note += " (cancelled - saving will re-activate it)"

    return [*values, note]


def cancel_schedule(editing_id):
    if not editing_id:
        return "Select a schedule in the table first.", schedules_table()

    store.update_schedule(editing_id, status="cancelled")
    sched.unregister_schedule(editing_id)

    return (
        f"Schedule **{editing_id}** cancelled. "
        "Its previously generated reports are kept.",
        schedules_table(),
    )


def run_selected_now(editing_id):
    if not editing_id:
        return "Select a schedule in the table first."

    sched.run_now_in_background(editing_id)

    return (
        f"Schedule **{editing_id}** started. Follow its progress in "
        "*Generated Reports* (use Refresh)."
    )


def toggle_recurrence(recurrence):
    custom = recurrence == "Custom"

    return (
        gr.update(visible=not custom),
        gr.update(visible=custom),
    )


# =========================================================
# TAB 3  -  GENERATED REPORTS
# =========================================================

REPORT_HEADERS = [
    "Report ID", "Report name", "Generated", "Type", "Format",
    "Status", "Email", "Records",
]


def reports_table(search, report_type, status):
    rows = []

    for r in store.list_reports(search, report_type, status):
        rows.append([
            r["report_id"],
            r["name"],
            store.fmt_ts(r["created_at"]),
            r["report_type"],
            r["report_format"],
            r["status"],
            r["email_status"] or "",
            r["record_count"] if r["record_count"] is not None else "",
        ])

    return rows


def select_report(df, evt: gr.SelectData):
    report_id = df.iloc[evt.index[0]]["Report ID"]
    r = store.get_report(report_id)

    if not r:
        return None, "Report not found."

    details = [
        f"**{r['report_id']}** - {r['name']}",
        f"- Type: {r['report_type']}  |  Format: {r['report_format']}",
        f"- Status: {r['status']}  |  Email: {r['email_status']}",
        f"- Request: {r['user_query']}",
    ]

    if r["error"]:
        details.append(f"- Error: {r['error']}")

    path = r["file_path"]

    if path and os.path.exists(path):
        return path, "\n".join(details)

    details.append("- No downloadable file is available for this report.")
    return None, "\n".join(details)


# =========================================================
# GRADIO UI  -  layout, colours and orientation only
# =========================================================

import inspect

CSS = """
:root{
  --navy:#0B2545; --navy-2:#13315C; --blue:#1D5FD1; --blue-soft:#EAF1FF;
  --ink:#0F172A; --muted:#64748B; --line:#E2E8F0; --bg:#F1F4F9;
  --ok:#0F766E; --danger:#B42318;
}
body, .gradio-container{ background:var(--bg) !important; }
.gradio-container{
  max-width:1560px !important; margin:0 auto !important;
  padding:18px 24px 40px !important;
  font-family:"Inter","Segoe UI",system-ui,-apple-system,sans-serif !important;
  color:var(--ink);
}
footer{ display:none !important; }

/* ---------- header banner ---------- */
.app-header{
  display:flex; justify-content:space-between; align-items:center; gap:20px;
  padding:22px 30px; margin-bottom:16px; border-radius:16px; color:#fff;
  background:linear-gradient(115deg,#0B2545 0%,#13315C 55%,#1D5FD1 135%);
  box-shadow:0 8px 24px rgba(11,37,69,.22);
}
.app-header h1{ margin:0; font-size:24px; font-weight:700; letter-spacing:.2px; color:#fff; }
.app-header p{ margin:4px 0 0; font-size:13.5px; color:#C7D6F0; }
.app-header .chips{ display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
.app-header .chip{
  padding:5px 12px; border-radius:999px; font-size:11.5px; font-weight:600;
  background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.22); color:#E6EEFB;
}

/* ---------- tabs ---------- */
div[role="tablist"], .tab-nav{ border-bottom:1px solid var(--line) !important; margin-bottom:6px; }
button[role="tab"]{
  font-size:14px !important; font-weight:600 !important; color:var(--muted) !important;
  padding:12px 22px !important; border:none !important; background:transparent !important;
  border-bottom:3px solid transparent !important; border-radius:0 !important;
}
button[role="tab"]:hover{ color:var(--navy) !important; }
button[role="tab"][aria-selected="true"], button[role="tab"].selected{
  color:var(--blue) !important; border-bottom:3px solid var(--blue) !important;
}

/* ---------- cards ---------- */
.card{
  background:#fff !important; border:1px solid var(--line) !important;
  border-radius:14px !important; padding:20px 22px !important;
  box-shadow:0 1px 2px rgba(15,23,42,.04), 0 6px 18px rgba(15,23,42,.05) !important;
  gap:12px !important;
}
.sec-title{
  display:flex; align-items:center; gap:10px; margin:0 0 2px;
  font-size:15px; font-weight:700; color:var(--navy);
}
.sec-title::before{
  content:""; width:4px; height:18px; border-radius:2px; background:var(--blue);
}
.sec-sub{ margin:-4px 0 4px; font-size:12.5px; color:var(--muted); }

/* ---------- inputs ---------- */
.card label span, .card .block-title, .card [data-testid="block-info"]{
  font-size:12px !important; font-weight:600 !important; color:#334155 !important;
  letter-spacing:.2px;
}
.card textarea, .card input[type="text"], .card input[type="number"], .card .wrap-inner{
  border-radius:10px !important; border-color:var(--line) !important; background:#F8FAFC !important;
}
.card textarea:focus, .card input[type="text"]:focus{
  border-color:var(--blue) !important; background:#fff !important;
  box-shadow:0 0 0 3px rgba(29,95,209,.14) !important;
}

/* ---------- buttons ---------- */
button.primary{
  background:linear-gradient(180deg,#2468DB,#1D5FD1) !important; border:none !important;
  color:#fff !important; font-weight:600 !important; border-radius:10px !important;
  box-shadow:0 2px 8px rgba(29,95,209,.30) !important;
}
button.primary:hover{ filter:brightness(1.08); }
button.secondary{
  background:#fff !important; color:var(--navy) !important; font-weight:600 !important;
  border:1px solid #CBD5E1 !important; border-radius:10px !important;
}
button.secondary:hover{ background:var(--blue-soft) !important; border-color:var(--blue) !important; }
button.stop{
  background:#fff !important; color:var(--danger) !important; font-weight:600 !important;
  border:1px solid #F2B8B5 !important; border-radius:10px !important;
}
button.stop:hover{ background:#FEF3F2 !important; }

/* ---------- status line ---------- */
.status-line{
  padding:9px 14px !important; border-radius:10px !important;
  background:var(--blue-soft) !important; border:1px solid #D4E2FB !important;
  color:var(--navy) !important; font-size:13px !important; min-height:20px;
}
.status-line p{ margin:0 !important; }

/* ---------- tables ---------- */
.table-wrap, .dataframe-wrap{ border-radius:12px !important; border:1px solid var(--line) !important; overflow:hidden; }
.table-wrap thead th, table thead th{
  background:var(--navy) !important; color:#fff !important; font-size:12px !important;
  font-weight:600 !important; letter-spacing:.3px;
}
.table-wrap thead th *, table thead th *{ color:#fff !important; }
.table-wrap tbody tr:nth-child(even) td{ background:#F8FAFC !important; }
.table-wrap tbody tr:hover td{ background:var(--blue-soft) !important; }
.table-wrap td{ font-size:12.5px !important; }

/* ---------- report area ---------- */
.report-pane{
  background:#fff; border-radius:12px; padding:6px;
  max-height:1100px; overflow:auto;
}
.empty-state{ text-align:center; padding:90px 30px; color:var(--muted); }
.empty-icon{ font-size:44px; margin-bottom:10px; }
.empty-title{ font-size:18px; font-weight:700; color:var(--navy); margin-bottom:6px; }
.empty-text{ font-size:13.5px; max-width:420px; margin:0 auto; line-height:1.55; }

/* ---------- custom schedule rows ---------- */
.day-row{ align-items:center !important; gap:8px !important; }
.day-row input[type="text"]{ text-align:center; }

.detail-box{ font-size:13px; line-height:1.7; color:#334155; }
"""

HEADER_HTML = """
<div class="app-header">
  <div>
    <h1>SAP Inventory Intelligence</h1>
    <p>AI-powered inventory analysis and automated reporting for MRP controllers</p>
  </div>
  <div class="chips">
    <span class="chip">BigQuery</span>
    <span class="chip">Gemini Data Agent</span>
    <span class="chip">MRP Risk Analysis</span>
  </div>
</div>
"""


def section(title, subtitle=None):
    """Card heading used inside every panel."""
    gr.HTML(
        f'<div class="sec-title">{title}</div>'
        + (f'<div class="sec-sub">{subtitle}</div>' if subtitle else "")
    )


THEME = gr.themes.Base(
    primary_hue="blue",
    secondary_hue="indigo",
    neutral_hue="slate",
    radius_size="lg",
    font=[
        gr.themes.GoogleFont("Inter"),
        "Segoe UI", "system-ui", "sans-serif",
    ],
)

# Gradio 6 moved theme/css from Blocks() to launch(); support both.
_STYLE_IN_LAUNCH = "css" in inspect.signature(gr.Blocks.launch).parameters

_blocks_kwargs = {} if _STYLE_IN_LAUNCH else {"theme": THEME, "css": CSS}
_launch_kwargs = {"theme": THEME, "css": CSS} if _STYLE_IN_LAUNCH else {}


with gr.Blocks(title="SAP Inventory Intelligence", **_blocks_kwargs) as demo:

    gr.HTML(HEADER_HTML)

    file_state = gr.State(None)
    id_state = gr.State(None)

    with gr.Tabs():

        # -------------------------------------------------
        # TAB 1  -  ON-DEMAND REPORTS
        #   left: request + e-mail   |   right: dashboard
        # -------------------------------------------------
        with gr.Tab("On-demand Reports"):

            with gr.Row(equal_height=False):

                # ---- left control column ----
                with gr.Column(scale=4, min_width=360):

                    with gr.Column(elem_classes="card"):
                        section(
                            "New report",
                            "Ask a question about stock, plants or materials."
                        )

                        question_input = gr.Textbox(
                            label="Inventory question",
                            placeholder=(
                                "Example: Analyse material stock risk across "
                                "all plants and show a donut chart and a "
                                "bar chart."
                            ),
                            lines=5,
                        )

                        format_input = gr.Dropdown(
                            REPORT_FORMATS, value="PDF",
                            label="Report format"
                        )

                        vendor_check_input = gr.Checkbox(
                            label="Include vendor e-mail check (Gmail)",
                            info=(
                                "Checks the plant MRP controller mailbox "
                                "for replenishment requests and vendor "
                                "replies, and adds a supply-risk section."
                            ),
                            value=False,
                        )

                        with gr.Row():
                            generate_button = gr.Button(
                                "Generate report",
                                variant="primary", scale=3
                            )
                            clear_button = gr.Button("Clear", scale=1)

                        report_status = gr.Markdown(
                            "Ready to generate a report.",
                            elem_classes="status-line"
                        )

                    with gr.Column(elem_classes="card"):
                        section(
                            "Send by email",
                            "Deliver the generated report through Gmail."
                        )

                        recipient_input = gr.Textbox(
                            label="Recipient email(s)",
                            placeholder="a@example.com, b@example.com"
                        )
                        subject_input = gr.Textbox(
                            label="Subject", value=DEFAULT_SUBJECT
                        )

                        with gr.Accordion("Message", open=False):
                            email_body_input = gr.Textbox(
                                value=sched.DEFAULT_EMAIL_BODY,
                                show_label=False, lines=4
                            )

                        send_email_button = gr.Button(
                            "Send report via Gmail", variant="primary"
                        )
                        email_status = gr.Markdown(
                            elem_classes="status-line"
                        )

                # ---- right dashboard column ----
                with gr.Column(scale=9, min_width=520):

                    with gr.Column(elem_classes="card"):
                        section(
                            "Inventory Intelligence Dashboard",
                            "Generated report, charts and inventory position."
                        )

                        file_output = gr.File(label="Download report")

                        report_output = gr.HTML(
                            value=EMPTY_REPORT_HTML,
                            elem_classes="report-pane"
                        )

        # -------------------------------------------------
        # TAB 2  -  SCHEDULE REPORTS
        #   three cards side by side, actions, table below
        # -------------------------------------------------
        with gr.Tab("Schedule Reports") as schedule_tab:

            editing_id = gr.State("")

            gr.HTML(
                '<div class="sec-sub" style="margin:8px 2px 4px">'
                'Create a schedule below, or <b>click a row</b> in the '
                'schedules table to edit it.</div>'
            )

            with gr.Row(equal_height=False):

                with gr.Column(scale=1, min_width=320, elem_classes="card"):
                    section("1 · Report")

                    s_name = gr.Textbox(
                        label="Schedule name",
                        placeholder="Weekly stock-risk review"
                    )
                    s_question = gr.Textbox(
                        label="Inventory question to run",
                        placeholder=(
                            "Analyse stock risk for all plants and "
                            "show a donut chart and a bar chart."
                        ),
                        lines=5
                    )
                    s_format = gr.Dropdown(
                        REPORT_FORMATS, value="PDF",
                        label="Report format"
                    )
                    s_vendor = gr.Checkbox(
                        label="Include vendor e-mail check (Gmail)",
                        value=False,
                    )

                with gr.Column(scale=1, min_width=320, elem_classes="card"):
                    section("2 · Delivery")

                    s_recipients = gr.Textbox(
                        label="Recipients (comma, semicolon or new line)",
                        lines=3,
                        placeholder=(
                            "planner1@example.com, planner2@example.com"
                        )
                    )
                    s_subject = gr.Textbox(
                        label="Email subject", value=DEFAULT_SUBJECT
                    )

                    with gr.Accordion("Email message (optional)", open=False):
                        s_body = gr.Textbox(
                            value=sched.DEFAULT_EMAIL_BODY,
                            show_label=False, lines=4
                        )

                with gr.Column(scale=1, min_width=340, elem_classes="card"):
                    section("3 · Timing")

                    s_tz = gr.Dropdown(
                        sched.TIMEZONES, value=DEFAULT_TZ,
                        label="Timezone", filterable=True
                    )
                    s_recurrence = gr.Radio(
                        sched.RECURRENCES, value="Everyday",
                        label="Recurrence"
                    )

                    s_time = gr.Textbox(
                        label="Run at (24-hour HH:MM)",
                        value="08:00"
                    )

                    with gr.Column(visible=False) as custom_group:
                        gr.HTML(
                            '<div class="sec-sub">Tick the days and set a '
                            'time for each (24-hour HH:MM).</div>'
                        )

                        day_checks, day_times = [], []

                        for label in sched.DAY_LABELS:
                            with gr.Row(elem_classes="day-row"):
                                day_checks.append(
                                    gr.Checkbox(label=label, scale=3,
                                                min_width=120)
                                )
                                day_times.append(
                                    gr.Textbox(
                                        value="08:00",
                                        show_label=False,
                                        container=False,
                                        scale=2, min_width=80
                                    )
                                )

            with gr.Row():
                save_btn = gr.Button("Save schedule", variant="primary",
                                     scale=2)
                new_btn = gr.Button("New schedule", scale=1)
                run_btn = gr.Button("Run selected now", scale=1)
                cancel_btn = gr.Button("Cancel selected schedule",
                                       variant="stop", scale=1)
                refresh_sched_btn = gr.Button("Refresh", scale=1)

            schedule_status = gr.Markdown(elem_classes="status-line")

            with gr.Column(elem_classes="card"):
                section("Schedules", "Click a row to edit, run or cancel it.")

                schedules_df = gr.Dataframe(
                    headers=SCHEDULE_HEADERS,
                    value=schedules_table,
                    interactive=False,
                    wrap=True,
                    show_label=False,
                )

        # -------------------------------------------------
        # TAB 3  -  GENERATED REPORTS
        #   filter bar on top, table left, details right
        # -------------------------------------------------
        with gr.Tab("Generated Reports") as reports_tab:

            with gr.Column(elem_classes="card"):
                with gr.Row():
                    f_search = gr.Textbox(
                        label="Search (ID, name or request)", scale=4,
                        placeholder="Type to search and press Enter"
                    )
                    f_type = gr.Dropdown(
                        ["All", "On-demand", "Scheduled"], value="All",
                        label="Type", scale=1, min_width=140
                    )
                    f_status = gr.Dropdown(
                        ["All", "Success", "Failed", "Running"], value="All",
                        label="Status", scale=1, min_width=140
                    )
                    f_refresh = gr.Button("Refresh", scale=1, min_width=110)

            with gr.Row(equal_height=False):

                with gr.Column(scale=8, min_width=520, elem_classes="card"):
                    section("Reports", "Click a row to select a report.")

                    reports_df = gr.Dataframe(
                        headers=REPORT_HEADERS,
                        value=lambda: reports_table("", "All", "All"),
                        interactive=False,
                        wrap=True,
                        show_label=False,
                    )

                with gr.Column(scale=3, min_width=300, elem_classes="card"):
                    section("Selected report")

                    report_details = gr.Markdown(
                        "Select a report from the list.",
                        elem_classes="detail-box"
                    )
                    report_download = gr.File(label="Download report")

    # =====================================================
    # EVENTS  (unchanged)
    # =====================================================

    # ---- Tab 1 ------------------------------------------

    generate_button.click(
        fn=generate_report,
        inputs=[question_input, format_input, vendor_check_input],
        outputs=[
            report_output, file_output, file_state,
            id_state, report_status
        ],
    )

    send_email_button.click(
        fn=send_email,
        inputs=[
            recipient_input, subject_input, email_body_input,
            file_state, id_state
        ],
        outputs=[email_status],
    )

    clear_button.click(
        fn=clear_report,
        inputs=[],
        outputs=[
            question_input, file_output, file_state, id_state,
            report_output, report_status
        ],
    )

    # ---- Tab 2 ------------------------------------------

    form_outputs = [
        editing_id, s_name, s_question, s_recipients, s_subject, s_body,
        s_tz, s_format, s_recurrence, s_time,
        *day_checks, *day_times, s_vendor,
    ]

    s_recurrence.change(
        toggle_recurrence,
        inputs=[s_recurrence],
        outputs=[s_time, custom_group],
    )

    save_btn.click(
        save_schedule,
        inputs=form_outputs,
        outputs=[editing_id, schedule_status, schedules_df],
    )

    new_btn.click(new_schedule, outputs=[*form_outputs, schedule_status])

    schedules_df.select(
        load_schedule,
        inputs=[schedules_df],
        outputs=[*form_outputs, schedule_status],
    )

    cancel_btn.click(
        cancel_schedule,
        inputs=[editing_id],
        outputs=[schedule_status, schedules_df],
    )

    run_btn.click(
        run_selected_now,
        inputs=[editing_id],
        outputs=[schedule_status],
    )

    refresh_sched_btn.click(schedules_table, outputs=[schedules_df])
    schedule_tab.select(schedules_table, outputs=[schedules_df])

    # ---- Tab 3 ------------------------------------------

    filter_inputs = [f_search, f_type, f_status]

    for trigger in (
        f_search.submit, f_type.change, f_status.change, f_refresh.click
    ):
        trigger(reports_table, inputs=filter_inputs, outputs=[reports_df])

    reports_tab.select(
        reports_table, inputs=filter_inputs, outputs=[reports_df]
    )

    reports_df.select(
        select_report,
        inputs=[reports_df],
        outputs=[report_download, report_details],
    )


if __name__ == "__main__":
    # Start the scheduler ONCE, in the process that serves the UI.
    sched.start()

    demo.queue().launch(
        allowed_paths=[str(store.REPORTS_DIR)],
        **_launch_kwargs,
    )
